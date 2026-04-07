# Code Quality & Architecture Review — FEA Infill Optimizer Plugin

**Reviewer:** code-quality-expert
**Date:** 2026-04-07
**Scope:** All Python files in `plugins/FEAInfillOptimizer/` (excluding `_vendor/`)

---

## Executive Summary

The plugin demonstrates strong architectural instincts: clean package separation, correct use of Cura's UM API patterns, well-documented physics modules, and a solid undo/redo foundation. The primary code quality concerns are: (1) two God-class files that have grown beyond a single responsibility, (2) a signal-connection bug that causes duplicate callbacks, (3) force rotation missing from the undo stack, (4) direct private-attribute access across class boundaries, and (5) helper-function duplication across three modules.

**Severity summary:**

| Severity | Count | Key issues |
|----------|-------|------------|
| Critical | 1 | Signal multi-connect in `_recheckDeps` |
| High | 7 | Force rotation not on undo stack; `setUpdateForceAtIndex` bypasses undo; missing cancellation propagation; B-matrix duplication in `iterative_solver.py`; transverse shear `Gl` coefficient missing; `quick_setup._face_normal` wrong default |
| Medium | 8 | God classes (`BoundaryConditionTool`, `FEAInfillExtension`); broad silent exception swallowing; private-attribute access cross-boundary; `_face_normal`/`_face_centroid` duplication; `_last_containment_method` module-level mutable state; missing `_StressOverlayNode` top-level class; `ClearAllBCsOperation` serialization coupling |
| Low | 13 | Type-annotation gaps in `bc_operations.py`; `list[str]` vs `List[str]`; step numbering comment error; redundant re-imports; legacy dead code; `_outer_radius` private access; inconsistent mixed camelCase; `_build` closure node-GC edge case; inline stdlib imports; `J^{-T}` comment error; exponent dict duplication; hardcoded Tsai-Hill shear strength; no signal disconnect on plugin disable; test helper duplication |

---

## 1. Plugin Registration

### `plugin.json`

**Status: Correct.** All required fields present (`name`, `author`, `version`, `api`, `description`, `i18n-catalog`). `api: 8` is correct for the target Cura version.

### `__init__.py`

**Status: Correct.** `getMetaData()` returns the `"tool"` key with required fields. `register()` uses deferred imports inside the function body — this is the correct pattern to avoid circular imports at module load time. The `FEAInfillExtension` instance is passed to `BoundaryConditionTool`, establishing the correct ownership relationship.

---

## 2. Module Organization & Separation of Concerns

### What Works Well

The package layout is clean and principled:

```
FEAInfillOptimizer/
├── fea/                  — pure FEA computation (mostly Cura-free)
│   ├── face_group_analyzer.py   — pure numpy, no Cura imports (good)
│   ├── quick_setup.py           — pure numpy, no Cura imports (good)
│   ├── material_database.py     — pure Python dataclass (excellent testability)
│   ├── mesh_extraction.py
│   ├── tetrahedralization.py
│   ├── homogenization.py
│   ├── stress_to_density.py
│   └── iterative_solver.py
├── jobs/                 — background job wrappers
├── mesh_generation/      — Cura scene node creation
├── operations/           — undo/redo operation objects
├── visualization/        — scene overlay rendering
├── deps/                 — dependency management
├── FEABoundaryConditionDecorator.py  — data model
├── FEAInfillExtension.py             — plugin lifecycle + QML bridge
└── BoundaryConditionTool.py          — interactive tool + QML bridge
```

### Issue CQ-1 (Medium): `BoundaryConditionTool` is a God Class

`BoundaryConditionTool.py` is ~1000 lines and has at least 5 distinct responsibilities:
1. BC editing (mode management, face picking, face group expansion via BFS)
2. Force direction rotation gizmo (ring drag, locked-axis plane intersection)
3. Hover preview system (async BFS, debouncing, generation-counting)
4. Quick setup workflows (gravity, cantilever, mount-holes)
5. Full QML bridge for analysis settings and phase transitions — a complete mirror of `FEAInfillExtension`'s QML API

The tool has 50+ exposed properties (`setExposedProperties` at line 171). The phase-management bridge (lines 540–860) is essentially a passthrough façade over `FEAInfillExtension` that adds no value while doubling maintenance surface. If `FEAInfillExtension` adds a property, the tool must be updated too.

**Recommendation:** Extract the hover preview, quick setup, and phase/analysis bridge into separate collaborator objects. The tool's core responsibility should be face picking and BC editing only.

### Issue CQ-2 (Medium): `FEAInfillExtension` is a God Class

`FEAInfillExtension.py` is ~1100 lines and combines:
1. Plugin lifecycle (engine-created initialization, deferred signal wiring)
2. 3MF persistence (serialize/deserialize BCs and results to node metadata)
3. Dependency management orchestration
4. FEA job orchestration (`runAnalysis`, `_onFEAFinished`, `_onFEAProgress`)
5. Phase state machine (define → optimize → running → review → error)
6. Full Qt property/signal layer for QML

The 3MF persistence logic alone (`_syncAllBCsToMetadata`, `_restoreBCsFromScene`, `_onSceneNodeMayHaveBCMetadata`, `_restoreSettingsFromNode`, `_restoreResultsFromNode`) spans ~100 lines and should be a dedicated `BCPersistenceManager` class.

---

## 3. Naming Conventions

### What Works Well

- `snake_case` for methods/variables, `PascalCase` for classes — consistently applied.
- Module-level constants use `UPPER_SNAKE_CASE` (`MODE_FIXED`, `MAX_BFS_ITERATIONS`, `REQUIRED_PACKAGES`).
- Private attributes consistently prefixed with `_`.
- `_CURA_MATERIAL_MAP` class variable correctly uppercased as a class-level constant.

### Issue CQ-3 (Low): camelCase Methods in `FEABoundaryConditionDecorator`

`toDict()`, `fromDict()`, `getFixedFaces()`, `addFixedFaces()`, etc. use camelCase inherited from Qt/Cura's Java-style API convention. This is internally consistent with the rest of Cura (e.g., `getWorldTransformation()`, `getMeshData()`) but violates Python convention for project-local code. The choice is defensible — `callDecoration("getBoundaryConditions")` is a Cura pattern — but should be documented as an intentional deviation.

### Issue CQ-4 (Low): `_recheck_count` Defined as Class Variable

```python
# FEAInfillExtension.py line 211
_recheck_count = 0  # ← class variable, shared across all instances
```

This is semantically an instance counter but defined at class scope. Works in practice (single instance) but is misleading. Should be in `__init__`.

### Issue CQ-5 (Low): `list[str]` vs `List[str]`

`MaterialDatabase.available_materials()` returns `list[str]` (Python 3.9+ builtin generic). All other code uses `List[str]` from `typing`. Pick one style; `List[str]` is the current codebase convention.

---

## 4. Type Annotations

### What Works Well

- `fea/face_group_analyzer.py`, `fea/homogenization.py`, `fea/stress_to_density.py` are fully typed.
- `FEABoundaryConditionDecorator.py` types all public methods.
- `FEASolveJob.__init__` uses `Any` appropriately for Cura dynamic types.

### Issue CQ-6 (Low): `bc_operations.py` Has No Type Annotations

All operation `__init__` parameters are untyped:

```python
# Current
def __init__(self, decorator, face_indices):

# Should be
def __init__(self, decorator: "FEABoundaryConditionDecorator", face_indices: List[int]) -> None:
```

Affects: `AddFixedFacesOperation`, `RemoveFixedFacesOperation`, `ClearFixedFacesOperation`, `AddForceGroupOperation`, `RemoveForceGroupOperation`, `AddTorqueGroupOperation`, `RemoveTorqueGroupOperation`, `UpdateTorqueAxisOperation`, `ClearAllBCsOperation`.

### Issue CQ-7 (Low): `BoundaryConditionTool` Getters/Setters Completely Untyped

The ~50 getter/setter pairs in `BoundaryConditionTool` have no annotations. For a large public interface this is a significant maintenance liability.

---

## 5. Error Handling

### What Works Well

- `stress_to_density.py`, `homogenization.py` validate inputs with clear `ValueError` messages.
- `DependencyInstallJob` handles `subprocess.TimeoutExpired` separately.
- `FEASolveJob.run()` wraps the entire pipeline in a try/except, passes exceptions as the job result rather than crashing the background thread.
- `_emit_progress` catches all exceptions to prevent progress emission from blocking the solver.

### Issue CQ-8 (Critical): Signal Multi-Connection in `_recheckDeps`

```python
# FEAInfillExtension.py lines 222–247
def _recheckDeps(self) -> None:
    ...
    if not self._deps_available and self._recheck_count < 3:
        self._recheck_count += 1
        QTimer.singleShot(2000, self._recheckDeps)  # calls _recheckDeps again!

    # These signal connections are inside _recheckDeps, NOT guarded:
    app.fileLoaded.connect(self._restoreBCsFromScene)       # line 238
    app.fileCompleted.connect(self._restoreBCsFromScene)    # line 239
    app.workspaceLoaded.connect(self._restoreBCsFromScene)  # line 241
    app.getController().getScene().sceneChanged.connect(...)  # line 246
    app.getController().getScene().sceneChanged.connect(...)  # line 247
    app.getOutputDeviceManager().writeStarted.connect(...)    # line 252
```

When dependencies are not available at startup, `_recheckDeps` schedules itself up to 3 more times. Each call re-executes the `app.fileLoaded.connect(...)` block, **adding duplicate signal connections**. After 3 retries:
- `_restoreBCsFromScene` is connected to `fileLoaded` **4 times**
- `_cleanupOrphanedOverlays` is connected to `sceneChanged` **4 times**
- `_syncAllBCsToMetadata` is connected to `writeStarted` **4 times**

This causes 4x BC restoration on every file load, 4x metadata writes on every save, and 4x orphan cleanup on every scene change.

**Fix:** Move all signal connections out of `_recheckDeps` into `_onEngineCreated`, connecting them exactly once regardless of dependency status. A `_disconnectAllSignals()` helper called at the start of `_recheckDeps` would also work as a defensive guard if other code paths connect signals. **Note:** the duplicate connections also defeat the 500ms `sceneChanged` throttle — with N connections the effective rate becomes 500ms/N. Confirmed Critical by reliability-expert (CRIT-1).

### Issue CQ-9 (High): Force Rotation Not On Undo Stack

`_handle_rotate_event` mutates `fg.force` directly during drag and on release but never pushes an undo operation:

```python
# BoundaryConditionTool.py lines 1383-1384
fg = groups[self._rotating_group_index]
new_dir = rotate_vector(fg.force, axis, direction * angle)
fg.force = new_dir  # ← direct mutation, no undo operation
```

Contrast with torque axis editing which correctly pushes `UpdateTorqueAxisOperation` on `MouseReleaseEvent`. Force direction rotation changes cannot be undone with Ctrl+Z.

**There should be a corresponding `UpdateForceDirectionOperation`** that snapshots the pre-drag and post-drag force vectors, pushed on `MouseReleaseEvent`.

### Issue CQ-10 (High): `setUpdateForceAtIndex` / `setUpdateTorqueAtIndex` Bypass Undo

```python
# BoundaryConditionTool.py lines 1065-1066
fg.force = Vector(fg.force.x * scale, fg.force.y * scale, fg.force.z * scale)
# No operation pushed — cannot be undone

# BoundaryConditionTool.py line 1098
groups[index].torque_magnitude = new_mag  # same issue
```

Inline magnitude edits from the list model UI are not undoable. This is inconsistent with all other BC mutations which go through `bc_operations.py`.

### Issue CQ-11 (Medium): Broad Silent Exception Swallowing Across Plugin

The most visible instance is in `_onSceneNodeMayHaveBCMetadata`:

```python
# FEAInfillExtension.py line 392
except Exception:
    pass  # Silently ignore — this fires very frequently
```

But reliability review (MED-4) confirms this is a plugin-wide pattern: multiple hot paths catch `Exception` broadly and either `pass` or log minimally, masking logic errors that look like normal operation. Unexpected exceptions from API changes or bad state are swallowed silently, making regressions invisible.

**Fix:** Differentiate expected exceptions (e.g., `AttributeError` on a `None` node during scene teardown) from unexpected ones. Use `Logger.logException("d", ...)` at debug level for the expected branch; re-raise or log at warning/error for anything else. The comment explaining high-frequency firing is correct context but not a justification for discarding the exception entirely.

### Issue CQ-32 (Low): No Signal Disconnection on Plugin Disable

The plugin connects multiple signals in `_onEngineCreated`/`_recheckDeps` but has no `deinitialize()` or equivalent that disconnects them when the plugin is disabled or unloaded. While Cura's plugin lifecycle rarely disables plugins mid-session, persistent signal connections after disable can cause callbacks to fire on a partially torn-down object, leading to AttributeErrors or ghost behaviour in edge cases.

**Fix:** Implement `deinitialize()` (or use a `_disconnectAllSignals()` helper already recommended for CQ-8) to cleanly disconnect all signals. From reliability-expert (LOW-4).

---

## 6. Code Duplication

### Issue CQ-12 (Medium): `_face_normal` / `_face_centroid` in Three Files

These helpers appear (with variations) in:
- `fea/face_group_analyzer.py` — full version with NaN/IndexError guards, docstrings
- `fea/quick_setup.py` — simplified version (no guards, e.g., line 42: falls back to `[0,1,0]` without warning)
- `visualization/force_direction_handle.py` — likely another copy (`compute_face_normal`, `compute_face_centroid` imported by `BoundaryConditionTool`)

The `quick_setup.py` version at line 42:
```python
return n / length if length > 1e-12 else np.array([0, 1, 0])
```
silently returns `+Y` for degenerate faces, which is incorrect — it should return the zero vector and let callers handle it, matching the `face_group_analyzer.py` contract.

**Fix:** Export `_get_face_vertices`, `_face_normal`, `_face_centroid` as public utilities from `face_group_analyzer.py` and import them in `quick_setup.py`.

### Issue CQ-13 (Medium): BC Restoration Logic Duplicated

`_restoreBCsFromScene` (iterates all scene nodes) and `_onSceneNodeMayHaveBCMetadata` (single node) contain near-identical per-node logic:
1. Parse `fea_infill_boundary_conditions` from metadata
2. Construct `FEABoundaryConditionDecorator` from dict
3. Call `node.addDecorator()`
4. Optionally restore settings and results

Extract a private `_restore_bc_for_node(node)` method and call it from both sites.

### Issue CQ-14 (Medium): Ring-Drag Event Handling Duplicated

`_handle_rotate_event` (force direction, ~100 lines) and `_handle_torque_edit_event` (torque axis, ~130 lines) share almost identical structure:
- `MousePressEvent`: detect axis handle, set drag plane, record start
- `MouseMoveEvent`: compute angle from drag delta, apply rotation
- `MouseReleaseEvent`: finalize and (for torque only) push undo operation

The difference is whether `fg.force` or `tg.torque_axis` is rotated and whether an undo operation is pushed. This duplication will cause drift if the rotation math needs to change. Extract a `_handle_ring_drag_event(event, on_rotate_callback, on_release_callback)` helper.

---

## 7. Circular Imports

**No circular imports detected.** Import graph is well-structured:

```
FEAInfillExtension → FEABoundaryConditionDecorator ← bc_operations
BoundaryConditionTool → FEABoundaryConditionDecorator
BoundaryConditionTool → bc_operations
FEAInfillExtension → DependencyManager
fea/* → (no cross-package imports except iterative_solver → homogenization)
```

**Minor:** `FEAInfillExtension` imports `FEABoundaryConditionDecorator` at module level (line 23) but also re-imports it inside `_onSceneNodeMayHaveBCMetadata` (line 372) and `_restoreBCsFromScene` (line 402). The deferred imports are unnecessary since the top-level import already exists.

---

## 8. Cura/UM API Usage

### What Works Well

- `SceneNodeDecorator` + `callDecoration()` pattern: correct.
- `Operation.push()` for undo: correct.
- `GroupedOperation` in `create_all_modifier_meshes`: correct.
- `JobQueue.getInstance().add(job)` + `job.finished.connect()`: correct UM pattern.
- `CuraApplication.getInstance().callLater()` for thread marshalling: correct.
- `pyqtProperty(type, notify=signal)` + QML binding pattern: correct.
- `weakref.WeakValueDictionary` for node cache: correct GC-safe approach.
- `ToolHandle` subclasses for BC highlight and force gizmo: correct.

### Issue CQ-15 (Low): `_force_handle._outer_radius` Private Access

```python
# BoundaryConditionTool.py line 1507
self._force_handle.show_at(
    self._force_handle.center,
    scale=self._force_handle._outer_radius / 12.5,  # ← private attribute
    axis_direction=new_axis,
)
```

`ForceDirectionHandle._outer_radius` is private. A `scale` property or a `current_scale` property on `ForceDirectionHandle` would be cleaner.

### Issue CQ-16 (Medium): `SettingInstance.resetState()` Undocumented Pattern

In `modifier_mesh_creator.py`, `SettingInstance.resetState()` is called immediately after `setProperty("value", ...)`. This mimics what Cura's internal code does but is not part of the documented API. If `resetState()` semantics change in future Cura versions this could silently break. A comment explaining why `resetState()` is required would help maintainers.

---

## 9. Thread Safety

### What Works Well

- `_GMSH_LOCK` serializes gmsh access (gmsh is not thread-safe).
- `callLater()` marshals all state mutations to the main thread from background jobs.
- `_hover_generation` monotonic counter correctly detects stale BFS results.
- `_hover_in_progress` re-entrancy guard prevents hover recursion.
- `_adjacency_building` flag prevents concurrent adjacency builds.

### Issue CQ-17 (Medium): `_last_containment_method` — Mutable Module-Level State

```python
# tetrahedralization.py lines 43, 363, 382, 388
_last_containment_method = "unknown"  # module-level mutable

def _points_inside_mesh(points, mesh):
    global _last_containment_method
    ...
    _last_containment_method = "trimesh"  # written from solver thread
```

This global is written from the FEA background thread (`FEASolveJob.run()`) and read from the same thread (same call stack), so in practice it's safe today since only one analysis runs at a time. However, it is non-reentrant. If two analyses ever ran concurrently, the quality classification would be nondeterministic.

**Fix:** Return the containment method from `_points_inside_mesh` or pass it as output through `_tetrahedralize_scipy`.

### Issue CQ-18 (High): Cancellation Does Not Interrupt Background Job

```python
# FEAInfillExtension.py line 974
if self._cancel_requested:
    return  # ← only checked AFTER the job finishes
```

`_cancel_requested` is only checked in `_onFEAFinished` (main thread, post-job). The `FEASolveJob` background thread never reads this flag. Clicking "Cancel" during a long FEA run does not stop the solver — it only discards the result when the job eventually completes. For a computation that can take minutes, this is a poor UX and wastes resources.

**Fix:** Pass a cancellation token (`threading.Event`) to `FEASolveJob` and have `IterativeFEASolver` check it between iterations, including inside the conjugate gradient solver callback where the inner loops run. Confirmed Critical by reliability-expert (CRIT-2).

---

## 10. Undo/Redo Architecture (`operations/bc_operations.py`)

### What Works Well

- Correct `Operation` subclass pattern; `redo()` performs the action, `undo()` reverses it.
- Module docstring explicitly documents `push()` calls `redo()` — prevents a common mistake.
- `ClearAllBCsOperation` snapshots the full BC state via `toDict()`/`fromDict()` — functionally correct, but see CQ-34 for the serialization-coupling risk.
- `UpdateTorqueAxisOperation` records both old and new axis for clean undo/redo.

### Issue CQ-19 (High): `RemoveForceGroupOperation.undo()` Accesses Private Attribute

```python
def undo(self):
    self._decorator._force_groups.insert(
        self._index,
        ForceGroup(self._face_indices, self._force)
    )
```

This breaks encapsulation to preserve index order, which `addForceGroup()` doesn't support. The comment acknowledges this. `RemoveTorqueGroupOperation.undo()` has the identical problem accessing `self._decorator._torque_groups` directly (confirmed by test-expert). The clean fix is to add package-internal methods `_insert_force_group_at(index, group)` and `_insert_torque_group_at(index, group)` to the decorator (underscored to signal they are not for external callers) so neither operation needs to reach into private state.

### Issue CQ-20 (Low): `AddForceGroupOperation.undo()` Fragile Assumption

```python
def undo(self):
    # The group was appended; undo removes the last entry.
    groups = self._decorator.getForceGroups()
    if groups:
        self._decorator.removeForceGroup(len(groups) - 1)
```

This is only correct when undoing operations in the order they were pushed (LIFO). With Cura's undo stack this holds, but the assumption is implicit. The comment documents it, but a more robust approach would be to store the insertion index and verify it in `undo()`.

### Issue CQ-21 (Low): Missing Step Label in `FEASolveJob.run()`

Step numbering in the pipeline comments skips Step 4:
```
# Step 1: Extract trimesh (line 105)
# Step 2: Tetrahedralize (line 116)
# Step 3: Run iterative FEA solver (line 121)
# Step 5: Discretize density (line 148)  ← skipped Step 4
# Step 6: Build zone surface meshes (line 157)
```

### Issue CQ-34 (Medium): `ClearAllBCsOperation` Couples Undo Snapshot to Serialization Format

`ClearAllBCsOperation` captures and restores state via `decorator.toDict()` / `decorator.fromDict()`. While this reuses existing code, it tightly couples the undo mechanism to the 3MF serialization format. If the serialization schema changes (field renamed, added, or removed), `undo()` will silently fail to restore the full BC state — the operation will succeed but some fields may be missing or reset to defaults.

**Fix:** Replace `toDict()`/`fromDict()` with a dedicated `__deepcopy__` snapshot of the decorator (or a separate `_snapshot()`/`_restore()` pair that is not the serialization format). The decorator already implements `__deepcopy__`, making this straightforward. Identified by test-expert.

---

## 11. Dead Code & Legacy

### Issue CQ-22 (Low): `FEAInfillExtension.showDialogForNode()` Is Legacy

```python
def showDialogForNode(self, node_key: str) -> None:
    """Legacy: called by tool; now delegates to inline phase flow."""
```

The docstring itself marks this as legacy. If it is no longer called by the tool, it should be removed.

### Issue CQ-23 (Low): `_find_overlay` Backward Compatibility Check

```python
# stress_overlay.py lines 445-447
# Backward compat: also check direct children of node (old overlays)
for child in node.getChildren():
    if child.getName() == _OVERLAY_NAME:
        return child
```

This searches the model's direct children for overlays created in an older version of the plugin. Since this is a first-party plugin (not a shipped upgrade), this compatibility code can be removed once the old overlay format is no longer needed.

### Issue CQ-24 (Low): `_StressOverlayNode` Inner Class Prevents Shader Reuse

`_StressOverlayNode` is defined inside `StressOverlayManager.create_overlay()`. A new class is created on every `create_overlay()` call. Since `_shader` is an instance attribute initialized to `None`, the shader is reloaded from disk on first render after each overlay creation. Moving `_StressOverlayNode` to module level would allow the class to persist (though each instance would still re-load the shader as the shader is instance-level).

---

## 12. FEA Solver Layer (`fea/iterative_solver.py`, `fea/fea_solver.py`, `fea/quick_setup.py`)

### Issue CQ-25 (High): B-Matrix Computation Duplicated Between `assemble_stiffness_matrix` and `_strain_displacement_matrices_vectorized`

Both `assemble_stiffness_matrix` and `_strain_displacement_matrices_vectorized` independently compute the full B/Jacobian/volume pipeline from the same mesh data. This creates two problems:

1. **Correctness risk**: any future fix to the B-matrix computation must be applied in two places; a discrepancy between the two copies may go undetected.
2. **Performance**: B is computed 2–3× per solver iteration instead of once. Per the performance review, this is the top computational hotspot in the entire solve pipeline.

**Fix:** Cache the result of `_strain_displacement_matrices_vectorized` on first call and reuse it throughout the iteration, or refactor `assemble_stiffness_matrix` to call `_strain_displacement_matrices_vectorized` so there is a single source of truth.

### Issue CQ-26 (Low): Inline `import` Statements Inside Function Bodies

Multiple functions in `iterative_solver.py` contain inline import statements:

```python
def apply_boundary_conditions(self, ...):
    import time as _time  # ← inside function

def solve(self, ...):
    import threading  # ← inside function

def _solve_scipy(self, ...):
    import warnings  # ← inside function
```

`import time as _time` appears in at least five methods (`apply_boundary_conditions`, `compute_element_stress`, `compute_element_compliance`, `compute_element_failure_index`, `_compute_element_stiffness_and_compliance`). All three modules (`time`, `threading`, `warnings`) are standard library and always available; there is no circular-import or optional-dependency rationale for deferring them.

**Fix:** Move all standard-library imports to the module top-level. Reserve function-level imports for genuinely optional heavy dependencies (e.g., `scipy`, `pytetwild`).

### Issue CQ-27 (High): Transverse Shear Coefficient Missing in EasyFEA Code Path

`iterative_solver.py:304` computes `Gl=G_base` for the transverse shear term of the constitutive matrix but it should be `Gl=bonding_coeff * G_base`. The bonding coefficient scales the shear modulus to account for infill bonding quality. Without this factor, transverse shear stiffness is overestimated for low-bonding materials (e.g., PETG + gyroid), producing incorrect element stiffness matrices and wrong stress distributions.

**Fix:** `Gl = bonding_coeff * G_base` — one character addition. Confirmed by physics-math-expert review.

### Issue CQ-28 (Low): Comment `J^{-T}` Contradicts Correct Code Using `J^{-1}`

`fea_solver.py:586` documents the B-matrix transformation as `J^{-T}` (inverse-transpose) but the code correctly uses `J^{-1}` (the inverse). Code is right; comment is wrong.

**Fix:** Update the comment to `J^{-1}`.

### Issue CQ-29 (Low): Pattern Exponent Dicts Duplicated with Divergent Defaults

Pattern exponent dictionaries appear in two places:
- `iterative_solver.py:61-82` — default exponent `1.5`
- `homogenization.py:42-49` — default exponent `1.3`

The key sets also differ between the two copies. Any update to one may silently not be applied to the other, leading to different stiffness behaviour depending on which code path is taken.

**Fix:** Export the canonical exponent dict from `homogenization.py` and import it in `iterative_solver.py`.

### Issue CQ-30 (Low): `S = 0.6 × Z` Hardcoded in Tsai-Hill Criterion

`fea_solver.py:629` computes in-plane shear strength as `S = 0.6 * Z`. This rule of thumb is a reasonable default but differs measurably from lab-measured values for some materials. Should be a per-material field in the `Material` dataclass or an overridable `MaterialDatabase` entry.

### Issue CQ-31 (High): `quick_setup._face_normal` Returns Wrong Default for Degenerate Faces

Confirmed by physics-math-expert as a correctness bug:

```python
# quick_setup.py
def _face_normal(verts):
    ...
    if norm < 1e-10:
        return np.array([0, 1, 0])  # silently wrong
```

In `gravity_from_face()`, a degenerate clicked face sets `down_normal = [0, 1, 0]`, producing wrong fixed support selection, wrong force direction, and wrong FEA results with no user-visible warning. The `face_group_analyzer` implementation correctly returns a zero vector that callers can detect and skip.

**Fix:** Either (a) import `_face_normal` from `face_group_analyzer` to eliminate the reimplementation, or (b) return zero vector and add a guard in `gravity_from_face()` that raises/warns when `norm(down_normal) < 1e-12`.

### Issue CQ-33 (Low): Test Helper `_ensure_real_module()` Duplicated Across Test Files

`_ensure_real_module()` is copy-pasted verbatim in `tests/test_mesh_generation.py` and `tests/test_dialog_model_selection.py`. This is the same DRY violation as the production-code duplications in section 6, applied to test infrastructure. A fix to the helper's logic must be applied in two places, and a divergence between copies may cause tests to behave differently in ways that are hard to diagnose.

**Fix:** Extract to `tests/conftest.py` as a shared pytest helper or autouse fixture. Identified by test-expert.

---

## 13. Summary of Issues by Priority

### Critical (Fix Before Merge)
| ID | Location | Description |
|----|----------|-------------|
| CQ-8 | `FEAInfillExtension._recheckDeps` | Signal connections added 4× if deps not available at startup |

### High (Fix in Next Sprint)
| ID | Location | Description |
|----|----------|-------------|
| CQ-9 | `BoundaryConditionTool._handle_rotate_event` | Force rotation not on undo stack |
| CQ-10 | `BoundaryConditionTool.setUpdateForceAtIndex/Torque` | Inline magnitude edits bypass undo |
| CQ-18 | `FEAInfillExtension.cancelAnalysis` | Cancel doesn't interrupt background thread |
| CQ-19 | `bc_operations.RemoveForceGroupOperation.undo` | Accesses `_force_groups` directly |
| CQ-25 | `fea/iterative_solver.py` | B-matrix computed 2–3× per iteration; duplication correctness risk |
| CQ-27 | `fea/iterative_solver.py:304` | Transverse shear `Gl=G_base` missing `bonding_coeff` factor |
| CQ-31 | `fea/quick_setup.py` | `_face_normal` returns `[0,1,0]` for degenerate faces; wrong gravity direction |

### Medium (Quality Improvements)
| ID | Location | Description |
|----|----------|-------------|
| CQ-1 | `BoundaryConditionTool` | God class (~1000 lines, 5+ responsibilities) |
| CQ-2 | `FEAInfillExtension` | God class (~1100 lines, 5+ responsibilities) |
| CQ-11 | `FEAInfillExtension` (+ wider) | Broad silent exception swallowing masks logic errors |
| CQ-12 | `quick_setup.py`, `face_group_analyzer.py` | `_face_normal`/`_face_centroid` duplicated across 3+ files |
| CQ-13 | `FEAInfillExtension` | BC restoration logic duplicated in two methods |
| CQ-14 | `BoundaryConditionTool` | Ring-drag event handling duplicated 100+ lines |
| CQ-17 | `tetrahedralization.py` | `_last_containment_method` mutable module-level state |
| CQ-34 | `operations/bc_operations.py` | `ClearAllBCsOperation` undo snapshot coupled to serialization format |

### Low (Housekeeping)
| ID | Location | Description |
|----|----------|-------------|
| CQ-3 | `FEABoundaryConditionDecorator` | camelCase methods (intentional but undocumented) |
| CQ-4 | `FEAInfillExtension` | `_recheck_count` class variable, should be instance |
| CQ-5 | `MaterialDatabase` | `list[str]` vs `List[str]` inconsistency |
| CQ-6 | `bc_operations.py` | No type annotations on operation constructors |
| CQ-7 | `BoundaryConditionTool` | Getter/setter pairs completely untyped |
| CQ-15 | `BoundaryConditionTool` | Accesses `_force_handle._outer_radius` (private) |
| CQ-16 | `modifier_mesh_creator.py` | `SettingInstance.resetState()` undocumented why |
| CQ-20 | `bc_operations.AddForceGroupOperation` | Fragile assumption about append-order undo |
| CQ-21 | `fea_solve_job.py` | Step 4 label missing in pipeline comments |
| CQ-22 | `FEAInfillExtension` | `showDialogForNode()` marked legacy, not removed |
| CQ-23 | `stress_overlay.py` | Backward compat overlay check for deleted code path |
| CQ-24 | `stress_overlay.py` | `_StressOverlayNode` defined inside function body |
| CQ-26 | `fea/iterative_solver.py` | Inline `import` statements inside function bodies (std-lib only) |
| CQ-28 | `fea/fea_solver.py:586` | Comment says `J^{-T}`, code correctly uses `J^{-1}` |
| CQ-29 | `iterative_solver.py` + `homogenization.py` | Pattern exponent dicts duplicated with different defaults (1.5 vs 1.3) |
| CQ-30 | `fea/fea_solver.py:629` | `S = 0.6 * Z` Tsai-Hill shear strength hardcoded; should be per-material |
| CQ-32 | `FEAInfillExtension` | No signal disconnection on plugin disable |
| CQ-33 | `tests/test_mesh_generation.py` + `test_dialog_model_selection.py` | `_ensure_real_module()` helper duplicated |

---

## 14. Cross-Discipline Findings (For Teammates)

The following issues identified here have implications beyond pure code quality:

**For reliability-expert:**
- CQ-8 (signal multi-connect): Will cause BC restoration to run 4× on file load, which could create duplicate BC decorators on nodes.
- CQ-18 (no cancel propagation): Background thread cannot be stopped; a failed analysis blocks the job queue.
- CQ-17 (`_last_containment_method` race): Affects quality classification if the scipy fallback path is hit.

**From reliability-expert (inbound — incorporated as CQ-11 upgrade and CQ-32):**
- **CRIT-1**: Confirmed CQ-8 — additionally notes the 500ms/N throttle defeat; recommends `_disconnectAllSignals()` helper.
- **CRIT-2**: Confirmed CQ-18 — recommends cancellation check inside the CG solver callback (inner loop).
- **MED-4**: Broad silent exception swallowing across the plugin — CQ-11 upgraded from Low to Medium and description expanded.
- **LOW-4**: No explicit cleanup on plugin disable — added as CQ-32 (Low).

**For ui-ux-expert:**
- CQ-9, CQ-10 (undo gaps): Users cannot undo force direction rotation or magnitude edits — significant UX regression.
- CQ-18 (cancel): "Cancel" button appears to work (phase transitions back to optimize) but solver runs to completion anyway — a progress bar that never stops until done despite "Cancel" being clicked.

**For physics-math-expert:**
- CQ-12: `quick_setup.py`'s `_face_normal` falls back to `[0,1,0]` for degenerate faces instead of the zero vector. Gravity setup from a degenerate face will produce a wrong "down" direction silently.

**For performance-expert:**
- CQ-2: `FEAInfillExtension._onSceneNodeMayHaveBCMetadata` fires on every `sceneChanged` with no throttle (unlike `_cleanupOrphanedOverlays` which throttles to 1Hz). It does minimal work (metadata dict lookup), so not critical, but worth noting.
- CQ-24: Shader reloaded from disk on every overlay creation cycle.

**From performance-expert (inbound — incorporated as CQ-25, CQ-26):**
- CQ-25: B-matrix duplicated between `_strain_displacement_matrices_vectorized` and `assemble_stiffness_matrix` — identified by performance-expert as the top solver hotspot.
- CQ-26: Inline `import` statements inside five+ methods — identified by performance-expert as obscuring module dependencies.

**From ui-ux-expert (inbound — QML code quality):**
The following QML-layer duplication issues from `docs/review_ui_ux.md` are also code quality concerns:
- The selection-mode toolbar (Single/Surface/Hole/Cylinder `RowLayout`) is duplicated verbatim across three tabs in `BoundaryConditionPanel.qml` (~60 lines × 3 with no abstraction). Extract to a reusable QML component. **Note:** the duplication also creates a UX scope-confusion risk — if selection mode is global across tabs, users may not realise changing it on one tab affects others; a shared component makes the global state explicit. ui-ux-expert can provide a component spec for the implementer.
- `ExamplesGallery.qml` uses two hardcoded ARGB hex colors (`#1522AA44`, `#154488DD`) that bypass `UM.Theme` entirely.
- Several English strings in `ExamplesGallery.qml` are not wrapped in `catalog.i18nc()`.
- `id` declarations (`forceCol`, `torqueCol`) inside `Repeater` delegates — IDs inside delegates are not scoped to the delegate and can collide.

**From ui-ux-expert (UX critical escalations from CQ-9/10/18):**
ui-ux-expert has added UX-22/23/24 (all Critical) to `docs/review_ui_ux.md` based on the undo and cancel gaps identified here:
- **UX-22**: Force rotation non-undoable — stopgap: show warning banner on rotate mode entry.
- **UX-23**: Magnitude edit non-undoable — stopgap: warning near magnitude field.
- **UX-24**: Cancel false affordance — stopgap: change "Stop" to "Stopping…" + spinner; gate phase transition on actual thread termination.
CQ-9, CQ-10, CQ-18 remain blockers on the code side regardless of the UX stopgaps.

**From test-expert (inbound — incorporated as CQ-19 update, CQ-33, CQ-34):**
- **CQ-19 updated**: `RemoveTorqueGroupOperation.undo()` confirmed to have the same private `_torque_groups` access as `RemoveForceGroupOperation` — fix extended to cover both.
- **CQ-33 (Low)**: `_ensure_real_module()` copy-pasted in two test files — extract to `tests/conftest.py`.
- **CQ-34 (Medium)**: `ClearAllBCsOperation` uses `toDict()`/`fromDict()` for undo snapshot — couples undo mechanism to serialization format; `__deepcopy__` preferred instead.

**From physics-math-expert (inbound — incorporated as CQ-27 through CQ-31):**
- **CQ-27**: Transverse shear `Gl=G_base` missing `bonding_coeff` factor — confirmed by physics review as incorrect numerical result.
- **CQ-28**: `J^{-T}` comment error in `fea_solver.py` (code correct, comment wrong).
- **CQ-29**: Pattern exponent dict duplicated with divergent defaults — single source of truth needed in `homogenization.py`.
- **CQ-30**: Tsai-Hill `S = 0.6 * Z` hardcoded — should be per-material.
- **CQ-31**: `quick_setup._face_normal` returning `[0,1,0]` for degenerate faces confirmed as real correctness bug (wrong gravity direction, no user warning).
