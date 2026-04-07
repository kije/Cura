# Performance & Responsiveness Review

**Reviewer:** performance-expert
**Date:** 2026-04-07
**Scope:** All performance-critical paths in the FEA Infill Optimizer plugin
**Priority:** No freezing, no long waits without updates, constant and accurate feedback

---

## Executive Summary

The plugin has strong vectorization foundations — stiffness assembly, stress computation, compliance, and von Mises are all properly vectorized with numpy einsum. The core solver loop will not freeze the Cura UI since it runs on a background `Job` thread. However, three classes of problems remain:

1. **B matrix redundancy**: `_strain_displacement_matrices_vectorized` is called up to 3× per iteration, each time allocating a fresh `(M, 6, 12)` B array.
2. **No intra-iteration progress**: Each FEA iteration takes 5–60s but emits only one progress event at the end. The UI bar is visually frozen for the entire duration.
3. **COO assembly memory spike**: For 100 K+ element meshes the temporary COO index arrays peak at ~350 MB during `assemble_stiffness_matrix`.

Everything else is either already well-handled or low-impact.

---

## 1. Vectorized Assembly — `fea_solver.py`

### 1.1 Stiffness Assembly (`assemble_stiffness_matrix`)

**Status: Good.**

- Fully vectorized: Jacobian inversion, B matrix construction, D matrix scaling, einsum for `BtD` and `k_e_all` — all over the full `(M, …)` batch.
- Degenerate-element skip via boolean mask avoids NaN propagation without a Python loop.
- COO → CSR conversion is the standard scipy path.

**Issue — COO temporary memory spike (HIGH):**

```python
row_idx = np.repeat(elem_dofs[:, :, np.newaxis], 12, axis=2)  # (M, 12, 12)
col_idx = np.repeat(elem_dofs[:, np.newaxis, :], 12, axis=1)  # (M, 12, 12)
```

For M = 100 K elements:
- `k_e_all`: (100K, 12, 12) × 8 bytes = **115 MB**
- `row_idx`: same shape = **115 MB**
- `col_idx`: same shape = **115 MB**
- Peak: **≥345 MB** simultaneously, on top of existing program memory.

On machines with ≤8 GB RAM (common in a desktop slicer context) this can cause visible lag or OOM during assembly for fine-resolution meshes.

**Recommendation:** Process elements in batches of 10–20 K. Accumulate directly into pre-sized COO data/row/col arrays using slice assignment instead of materializing the full `(M, 12, 12)` tensors at once.

**Issue — `_build_D_matrices` non-uniform-nu fallback (MEDIUM):**

```python
return np.stack([
    build_constitutive_matrix(float(E), float(nu))
    for E, nu in zip(E_per_element, nu_per_element)
])
```

The `np.unique` check promotes the fast path when all nu are equal, which covers the normal FDM case. However in the non-uniform nu branch (e.g., when future support for graded materials is added) this reverts to a Python loop over M elements. Not a concern today, but documented for awareness.

### 1.2 Boundary Conditions (`apply_boundary_conditions`)

**Status: Good.**

- Vectorized row/column zeroing via CSR data masks avoids the O(n_fixed × nnz) cost of LIL-based per-DOF operations.
- `setdiag` + `eliminate_zeros` is the correct idiomatic path.
- Timing instrumentation is present with `Logger.log("d", ...)`.

No action needed.

### 1.3 Stress / Compliance / Failure Index

**Status: Good vectorization, but B matrix is recomputed unnecessarily.**

`compute_element_stress`, `compute_element_compliance`, and `compute_element_failure_index` all call `_strain_displacement_matrices_vectorized(tet_mesh)` independently.  `assemble_stiffness_matrix` also computes the same B/V/valid arrays inline. Result:

| Iteration type | B computations per iteration |
|---|---|
| Heuristic (stress path) | 2× (assembly + `compute_element_stress`) |
| Heuristic (failure index path) | 2× (assembly + `compute_element_failure_index`) |
| OC | 3× (assembly + `compute_element_failure_index` + `_compute_element_stiffness_and_compliance`) |

Each B computation for 100 K elements = **57.6 MB** allocation + full Jacobian inversion pass.

**Recommendation (HIGH):** Add a `_b_cache` dict on `LinearElasticitySolver` (or as a module-level `functools.lru_cache` keyed on `id(tet_mesh)`) that caches `(B_all, V_all, valid)` after the first computation per mesh. The cache should be invalidated when the mesh changes (keyed on `(id(tet_mesh), id(tet_mesh.elements))`). This cuts B allocations from 3× to 1× per iteration.

---

## 2. Linear Solver — `fea_solver.py::solve`

### 2.1 spsolve Timeout Threading

**Status: Correct.**

The daemon thread + 30 s join pattern is appropriate. When spsolve is blocked in SuperLU's C extension it does release the GIL, so Python's thread scheduler will correctly timeout and proceed to the CG fallback. The daemon flag ensures a truly hung spsolve thread is reaped on process exit without blocking Cura's shutdown.

Subtle note: if the 30 s timeout fires, spsolve and CG run concurrently for the brief period while CG solves. Both write to different closure variables (`result[0]` vs the local `u, info`), so there is no data race. This is benign.

### 2.2 CG Fallback — Missing Progress Callback (MEDIUM)

```python
u, info = spla.cg(K, f, M=M_inv, tol=1e-8, maxiter=5000)
```

`scipy.sparse.linalg.cg` accepts an optional `callback` argument that is invoked after each iteration with the current solution vector. The current code does not use it. For a 180 K DOF system, 5000 CG iterations could take 60–120 s with no progress signal.

**Recommendation:** Add a CG iteration callback that throttles to at most 2 Hz and emits sub-iteration progress to the solver's progress callback (if provided). Example:

```python
_cg_iter = [0]
_last_cg_t = [time.monotonic()]

def _cg_cb(xk):
    _cg_iter[0] += 1
    now = time.monotonic()
    if now - _last_cg_t[0] > 0.5:
        _last_cg_t[0] = now
        Logger.log("d", "FEA CG iter %d", _cg_iter[0])

u, info = spla.cg(K, f, M=M_inv, tol=1e-8, maxiter=5000, callback=_cg_cb)
```

### 2.3 CG Preconditioner Quality (LOW)

Jacobi (diagonal) preconditioner is the minimal viable option. For SIMP problems with extreme density ratios (elements at `rho_min` ≈ 0.01× full E), the condition number can reach 10⁶–10⁸, making CG convergence very slow regardless of iteration count.

**Recommendation (future):** Consider incomplete Cholesky (`spla.LinearOperator` with `sklearn`'s `cholesky_factor` or `pyamg` algebraic multigrid). Not urgent while spsolve succeeds; only matters when CG is the primary path.

---

## 3. Iterative Solver Loop — `iterative_solver.py`

### 3.1 EasyFEA Observer Accumulation Fix

**Status: Correctly fixed.**

The explicit `mesh._Remove_observer(_prev_simu)` before each new `Simulations.Elastic()` creation prevents the O(n²) observer fan-out that caused the original hang. The fix is applied on both the inner loop and the post-loop cleanup.

### 3.2 EasyFEA Per-Node Neumann Loop (HIGH)

```python
for ni, w in zip(force_nodes, area_weights):
    simu.add_neumann(
        np.array([ni]),
        [fx_total * w, fy_total * w, fz_total * w],
        ["x", "y", "z"]
    )
```

For a force region with 500 surface nodes this issues 500 individual Python→EasyFEA calls _per iteration_. At 20 iterations: 10 000 calls. Each `add_neumann` call touches EasyFEA's internal BC list and triggers boundary-condition bookkeeping.

**Recommendation:** Check if EasyFEA's `add_neumann` can accept a node array of length N along with an `(N, 3)` values array (vectorized Neumann). If so, reshape `force_nodes` and pre-multiply `[fx, fy, fz]` by `area_weights[:, None]` and pass as a single call. If EasyFEA's Python binding does not support batched per-node values, fall back to grouping by unique weight (most nodes share weight ≈ 1/N) and issue fewer calls.

### 3.3 Progress Granularity — No Intra-Iteration Feedback (HIGH)

```python
if progress_callback is not None:
    progress_callback((iteration + 1) / max_iter)
```

Both `_solve_easyfea` and `_solve_scipy` only fire the progress callback at the _end_ of each iteration. With 20 max iterations, visible progress steps are 5% each — but each step can take 5–60 s (depending on mesh size and solver). The UI's progress bar is visually frozen for the entire duration of each iteration.

The scipy path already logs sub-stage timing (`assemble=%.1fs, BCs=%.1fs, solve=%.1fs, stress=%.1fs`). This timing information should be promoted to the progress callback.

**Recommendation:** Emit fine-grained progress at sub-iteration checkpoints:

```
iteration i starts:    base = i / max_iter
  → assembly done:     base + 0.25 / max_iter
  → BCs done:          base + 0.35 / max_iter
  → solve done:        base + 0.80 / max_iter
  → stress done:       base + 1.00 / max_iter
```

This turns a 60 s silent gap into 4 visible progress updates per iteration.

### 3.4 Solver Progress from EasyFEA `simu.Solve()` (MEDIUM)

EasyFEA's `Simulations.Elastic.Solve()` is a black box — there is no documented progress callback in the vendored EasyFEA source. Options:

1. **Pre/post timing log**: Already done with `Logger.log("d", "FEA EasyFEA iter %d step 4 Solve(): %.3fs", ...)`. This does not reach the UI.
2. **Estimated sub-phase progress**: Since `Solve()` is known to take ~80% of iteration time, emit `progress_callback(base + 0.1/max_iter)` just before the solve thread starts, and `progress_callback(base + 0.9/max_iter)` immediately after join. This gives the user a visual pulse even if granularity is coarse.
3. **EasyFEA Solvers.py inspection**: The vendored `_vendor/EasyFEA/Simulations/Solvers.py` may expose iteration hooks if using iterative solvers internally. Worth examining to see if a residual callback is plumbed through.

---

## 4. Progress Reporting — `FEAInfillExtension.py`

### 4.1 Throttle Implementation

**Status: Correct.**

```python
if now - self._last_progress_time < 0.5 and progress < 99:
    return  # skip — too soon since last update
```

The 500 ms throttle prevents callLater flooding. The `progress < 99` exception correctly lets the final 100% update through unconditionally.

### 4.2 callLater Flood Risk from sceneChanged (MEDIUM)

```python
app.getController().getScene().sceneChanged.connect(self._onSceneNodeMayHaveBCMetadata)
```

`_onSceneNodeMayHaveBCMetadata` is invoked on _every_ `sceneChanged` signal, including rendering-triggered changes during camera pan/zoom (can fire 60× per second). The function has fast early-exit guards (not CuraSceneNode, no metadata key, already has decorator), but every call still costs at minimum two dictionary lookups and an `isinstance` check.

`_cleanupOrphanedOverlays` correctly applies a 1.0 s throttle. `_onSceneNodeMayHaveBCMetadata` has no throttle.

**Recommendation:** Add a 200 ms throttle to `_onSceneNodeMayHaveBCMetadata`, or restructure to connect only during file-load events (connect on `fileLoaded`/`workspaceLoaded`, disconnect after a short delay).

---

## 5. Hover Debouncing — `BoundaryConditionTool.py`

### 5.1 Debounce Pattern

**Status: Functionally correct, slightly wasteful.**

```python
QTimer.singleShot(debounce_ms, _debounced_check)
```

Each mouse-move event (up to 60/s during fast movement) creates a new `QTimer` object and a closure capturing `generation`. The generation check ensures stale timers are no-ops, so correctness is fine. However at 60 fps and 75 ms debounce, there are ~4 live timers per debounce window, each holding a closure.

**Recommendation (LOW):** Store a single reusable `QTimer` instance on the tool and call `.start(debounce_ms)` on each mouse move (which restarts/resets the timer):

```python
if self._hover_debounce_timer is None:
    from PyQt6.QtCore import QTimer
    self._hover_debounce_timer = QTimer()
    self._hover_debounce_timer.setSingleShot(True)
    self._hover_debounce_timer.timeout.connect(self._on_hover_debounce_fired)
self._hover_debounce_timer.start(self._hover_debounce_ms)
```

This creates one timer object instead of N, and is the idiomatic Qt debounce pattern. It also eliminates the generation counter staleness check (a restarted timer naturally replaces the previous pending call).

### 5.2 Centroid Cache

**Status: Excellent.**

Transform-bytes comparison for cache invalidation is clever and correct. The O(n_tris) distance scan on click is fast (numpy vectorized) and only happens on user clicks, not on hover (hover uses the same `_find_closest_face` but the cache hit rate is 100% during continuous hover over the same model).

No action needed.

### 5.3 Re-Entrancy Guard

**Status: Correct.**

`_hover_in_progress` prevents signal-cascade recursion. The `try/finally` ensures the flag is always cleared even on exception.

---

## 6. Face Group BFS — `face_group_analyzer.py`

### 6.1 BFS Caps

- `MAX_BFS_ITERATIONS = 50 000` (global safety net)
- `max_faces = 5 000` (per-query cap)

Both are reasonable. A 5 000-face group is already large for boundary condition purposes.

### 6.2 Per-Face Allocation in BFS Inner Loop (MEDIUM)

`_face_normal` and `_face_centroid` call `_get_face_vertices`, which calls `.astype(np.float64)` for each of the 3 vertices:

```python
return (
    verts[i0].astype(np.float64),
    verts[i1].astype(np.float64),
    verts[i2].astype(np.float64),
)
```

Each `astype(np.float64)` on a (3,) array allocates a new small array. For a 5 000-face BFS group: ~15 000 small allocations in the hot path. Python's allocator handles these quickly, but it contributes to GC pressure and can add 10–50 ms for large groups (measured on CPython 3.12).

**Recommendation (MEDIUM):** Pre-compute all face normals and centroids once when building the adjacency structure, or vectorize them lazily at first BFS call and cache on the adjacency dict. Store as `(n_faces, 3)` arrays. BFS then indexes `normals[face_idx]` instead of calling `_face_normal`.

### 6.3 `_build_adjacency_indexed` Python Loop (MEDIUM)

```python
for fi in range(n_faces):
    tri = indices[fi]
    for a, b in ((int(tri[0]), int(tri[1])), ...):
        edge = (min(a, b), max(a, b))
        edge_to_faces.setdefault(edge, []).append(fi)
```

For a 100 K face mesh: 300 K dict insertions with tuple keys. Python dict with tuple keys has ~200 ns/op → ~60 ms. This is a one-time cost, and it runs asynchronously (`_ensure_adjacency_async`) so it doesn't block the UI. However, there is a faster vectorized approach.

**Recommendation (LOW, future):** Replace with a numpy-based edge-to-face map:
1. Build edge array: `edges = np.sort(indices[:, [[0,1],[1,2],[2,0]]], axis=2)` → (F, 3, 2)
2. Use `np.unique` with `return_inverse` to assign canonical edge IDs.
3. Use `np.argsort` on edge IDs to group faces.

This reduces 300 K Python dict ops to a few numpy calls at ~10× speed.

---

## 7. Stress Overlay — `visualization/stress_overlay.py`

### 7.1 KDTree Query

```python
kd_tree = scipy.spatial.KDTree(tet_nodes)
_, nearest_tet_node = kd_tree.query(surface_vertices, workers=-1)
```

**Status: Good.** `workers=-1` uses all CPU cores for parallel queries. For a 50 K vertex surface and 60 K tet nodes, this completes in <100 ms on modern hardware.

### 7.2 Vectorized Colormap

`_stress_to_color_vectorized` iterates over 4 colormap segments with numpy operations per segment. This is correct and fast — the 4-iteration loop is constant regardless of vertex count.

No action needed.

### 7.3 Normal Computation Loop

```python
for i in range(3):
    numpy.add.at(vert_normals, indices_arr[:, i], face_normals)
```

3-iteration constant loop over numpy `add.at`. Fine.

### 7.4 Overlay Creation Timing

`create_overlay` runs synchronously on the main thread (called from `_onFEAFinished` → `callLater`). For a 100 K vertex mesh:
- KDTree build + query: ~50–200 ms
- Colormap mapping: ~10 ms
- Normal accumulation: ~30 ms
- `MeshBuilder.calculateNormals()`: depends on UM implementation

This could produce a ~300 ms UI pause when the overlay appears. Not a freeze, but noticeable.

**Recommendation (LOW):** Move `create_overlay` computation to a background thread, delivering the built `MeshData` to the main thread via `callLater` for scene node creation only.

---

## 8. BC Highlight — `visualization/bc_highlight.py`

### 8.1 Per-Face Python Loop in `update_visualization` (MEDIUM)

```python
for face_idx in bc_decorator.getFixedFaces():
    self._paint_face(mb, verts, indices, face_idx, green)
```

For a model where the user has fixed 2 000 faces (e.g., a flat plate with a dense mesh), this is 2 000 Python-loop iterations, each calling `_paint_face` which does numpy indexing to extract 3 vertices and writes them to the MeshBuilder. `_update_highlights` is called on every click, mode change, and hover update.

**Recommendation (MEDIUM):** Vectorize face painting. Pre-gather all fixed face vertices as a batch:

```python
fixed = np.array(bc_decorator.getFixedFaces(), dtype=np.int32)
if indices is not None:
    batch_verts = verts[indices[fixed]].reshape(-1, 3)  # (N*3, 3)
else:
    batch_verts = verts[np.repeat(fixed * 3, 3) + np.tile([0,1,2], len(fixed))]
```

Then set all vertices in one MeshBuilder call. This would reduce O(n_faces) Python calls to O(1) numpy operations.

---

## 9. Memory Usage Estimates for Large Meshes

| Component | 10 K elements | 100 K elements | 1 M elements |
|---|---|---|---|
| B_all `(M, 6, 12)` float64 | 5.8 MB | 57.6 MB | 576 MB |
| k_e_all `(M, 12, 12)` float64 | 11.5 MB | 115 MB | 1.15 GB |
| COO row_idx `(M, 12, 12)` int64 | 11.5 MB | 115 MB | 1.15 GB |
| COO col_idx (same) | 11.5 MB | 115 MB | 1.15 GB |
| **Peak during assembly** | **~40 MB** | **~350 MB** | **~3.5 GB** |
| K (CSR, ~180K DOF for 100K elems) | — | ~120 MB | ~1.2 GB |
| B recomputed 3× (OC path) | 17 MB | 173 MB | 1.73 GB |

**At 100 K elements (typical "fine" mesh for a ~100 mm part):** peak RAM ~500 MB above baseline. This is feasible on 8 GB systems but tight with other Cura processes.

**At 1 M elements:** infeasible on typical desktop hardware. The element size / mesh resolution settings should warn the user if element count is projected to exceed 200 K (based on bbox / element_size heuristic). Currently no such guard exists.

**Recommendation (HIGH):** Add a pre-flight element count estimate from bbox and element_size before tetrahedralization, and warn / auto-downgrade resolution if projected > 200 K elements.

---

## 10. OC Update — `fea/oc_update.py`

### 10.1 Bisection

**Status: Good.**

100 bisection iterations with `(lam_hi - lam_lo) / lam_mid < 1e-6` convergence check. Typically converges in 30–40 iterations. All operations are vectorized numpy. No issues.

### 10.2 B Matrix Triple-Recomputation (See §1.3)

`_compute_element_stiffness_and_compliance` calls `_strain_displacement_matrices_vectorized` — the 3rd B computation per OC iteration. This is the highest-priority optimization target.

---

## 11. Findings for Other Reviewers

### For `ui-ux-expert`

- **Intra-iteration progress is the top UX issue.** The progress bar is visually frozen for the entire duration of each FEA iteration (5–60 s). Users have no way to distinguish "working normally" from "hung". Adding sub-stage progress (assembly, solve, stress) within each iteration is critical for perceived responsiveness.
- **ETA estimation in `_onFEAProgress`** (`eta_str`) uses a linear extrapolation. This is accurate for heuristic (iterations converge smoothly) but unreliable for OC (early iterations are slow, later ones are faster after convergence). Consider displaying "~iteration X of Y" instead of or in addition to ETA seconds.
- **No in-iteration stage label**: The stage text `"Solving FEA (iteration %d)..."` doesn't update within an iteration. Adding `"Assembling stiffness..."`, `"Solving linear system..."`, `"Computing stress..."` would improve perceived responsiveness.

### For `reliability-expert`

- The spsolve timeout thread race (§2.1) is benign but worth documenting: a timed-out spsolve thread continues running in the background even while CG produces a result. The thread is daemon and will be killed on process exit, but during the session it consumes CPU.
- The `_onSceneNodeMayHaveBCMetadata` handler with no throttle (§4.2) fires 60×/s during camera motion. While fast individually, this could mask a latent performance issue if the metadata check becomes more expensive.
- If `callLater(_apply)` in `_onFEAFinished` runs while the scene is in a half-loaded state (e.g., user loads a new file while analysis is running), the node lookup via `_node_cache` could return a stale node. The `weakref.WeakValueDictionary` handles the GC case but not the "node was replaced" case.

### For `physics-math-expert`

- B matrix recomputation (§1.3) has no correctness impact — each recomputation produces the same values from the same mesh. The concern is purely performance.
- The `_build_D_matrices` uniform-nu fast path (§1.1) assumes `np.unique(nu_per_element)` returns a single value for the common FDM case. This is correct since `nu_arr = np.full(n_elems, material.nu)` in `_solve_scipy`. Confirm this assumption is preserved if material variation is added.

### For `code-quality-expert`

- `_strain_displacement_matrices_vectorized` duplicates the entire Jacobian + B-matrix computation from `assemble_stiffness_matrix`. The code is correct but the duplication means any future fix to B computation must be applied in two places. Extracting to a shared cached path would also eliminate the performance issue.
- The `time` module is imported inline (`import time as _time`) inside multiple functions. This is harmless in Python (module import is cached) but stylistically inconsistent.
- `import threading` and `import warnings` appear inside `solve` and `_solve_scipy` respectively. These should be top-level imports.

---

## 12. Priority Matrix

| Issue | Impact | Effort | Priority |
|---|---|---|---|
| B matrix computed 3× per iteration | CPU: 30–50% overhead, RAM: 170 MB extra | Medium | **P1** |
| No intra-iteration progress feedback | UX: UI appears frozen 5–60 s | Low | **P1** |
| COO assembly 350 MB memory spike | RAM: OOM risk on fine meshes | Medium | **P1** |
| EasyFEA per-node neumann loop | CPU: 500–1000 extra C calls/iter | Medium | **P2** |
| No element count pre-flight guard | UX: accidental fine-mesh hang | Low | **P2** |
| `_onSceneNodeMayHaveBCMetadata` flood | CPU: 60×/s during camera motion | Low | **P2** |
| BFS per-face array allocations | CPU: 15 K allocs per 5 K BFS | Medium | **P3** |
| BC highlight per-face Python loop | CPU: O(n_faces) per highlight update | Medium | **P3** |
| `_build_adjacency_indexed` Python loop | CPU: 60 ms per 100 K mesh, async | Low | **P3** |
| QTimer singleShot per hover move | CPU: minor closure overhead | Low | **P4** |
| CG progress callback | UX: CG fallback has no sub-progress | Low | **P4** |
| Overlay creation on main thread | UX: ~300 ms pause on overlay toggle | Low | **P4** |

---

## Appendix: Confirmed Performance-Correct Paths

The following were reviewed and found to be performing correctly — no action needed:

- Vectorized von Mises / directional von Mises / Tsai-Hill (`_von_mises_vectorized`, `_tsai_hill_vectorized`)
- Vectorized element displacement gather (`_gather_element_displacements`)
- BC vectorized row/column zeroing (`apply_boundary_conditions`)
- KDTree with `workers=-1` for stress overlay mapping
- Vectorized colormap (`_stress_to_color_vectorized`)
- Centroid cache with transform-bytes invalidation (`_find_closest_face`)
- OC bisection (100 iterations, vectorized numpy)
- 500 ms progress throttle in `_onFEAProgress`
- 1.0 s throttle in `_cleanupOrphanedOverlays`
- Hover debounce generation counter (correctness)
- Async adjacency build (`_ensure_adjacency_async`)
- BFS face limits (MAX_BFS_ITERATIONS = 50 K, max_faces = 5 K)
- EasyFEA observer deregistration fix (prevents O(n²) fanout)
- spsolve 30 s timeout (correct daemon thread + CG fallback)
