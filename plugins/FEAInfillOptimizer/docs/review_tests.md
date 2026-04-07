# Test Suite Review & Gap Analysis

**Reviewed by:** testing-expert
**Date:** 2026-04-07
**Branch:** worktree-streamed-prancing-biscuit

---

## 1. Executive Summary

The existing test suite has strong coverage of the core FEA math pipeline and the Cura integration layer (BC decorator, modifier mesh creator, dialog flow). However, **four entire source modules have zero test coverage**, and several recently-added features (SIMP OC, Tsai-Hill, CG fallback, torque groups, adaptive damping) are not directly exercised.

**Critical gaps (files with 0% coverage):**
- `fea/oc_update.py` — SIMP OC density update
- `fea/face_group_analyzer.py` — BFS face group selection
- `operations/bc_operations.py` — undo/redo operation classes
- `visualization/stress_overlay.py` — colormap + vertex mapping helpers

**New test files created:** see Section 4.

---

## 2. Existing Test Files — What Is Covered

### `tests/test_fea_pipeline.py`
| Module | Functions/Classes Covered |
|--------|--------------------------|
| `homogenization.py` | `build_constitutive_matrix` (shape, symmetry, PD, entries, edge cases) |
| `homogenization.py` | `effective_properties` (power law, all patterns, clamping) |
| `material_database.py` | `Material`, `MaterialDatabase` (all materials, PLA props, case-insensitive lookup, fallback) |
| `fea_solver.py` | `_strain_displacement_matrix` (shape, volume, B columns, rigid body translation) |
| `fea_solver.py` | `_von_mises` (zero, uniaxial, equibiaxial, hydrostatic, pure shear, non-negative) |
| `fea_solver.py` | `LinearElasticitySolver` (assembly shape/symmetry/CSR/PSD, BC zeroing/diagonal, solve singular/fixed/direction, stress shape/non-negative/zero) |
| `stress_to_density.py` | `stress_to_density` (linear, power, clamping, error handling) |
| `tetrahedralization.py` | `tetrahedralize`, `TetMesh` (Gmsh-dependent; skipped when gmsh absent) |

### `tests/test_e2e_pipeline.py`
| Module | Coverage |
|--------|----------|
| `iterative_solver.py` | `IterativeFEASolver.solve` (cube compression, cantilever beam, convergence, multiple materials) |
| `density_discretizer.py` | E2E discretization |
| `zone_mesh_builder.py` | E2E zone mesh building |

**Limitation:** All E2E tests use `optimization_method="heuristic"` only. The SIMP OC path (`optimization_method="oc"`) is never exercised.

### `tests/test_mesh_generation.py`
| Module | Coverage |
|--------|----------|
| `density_discretizer.py` | Full unit coverage (binning, sorting, edge cases, input validation) |
| `zone_mesh_builder.py` | Full unit coverage (single/two tet, inner surface exclusion, cube) |
| `modifier_mesh_creator.py` | `create_all_modifier_meshes` (node count, op calls, settings, infill pattern) |
| `FEABoundaryConditionDecorator.py` | Fixed faces CRUD, force groups CRUD, `hasAnyBC`, `clearAll`, material name, serialization, deep copy |
| `deps/dependency_manager.py` | `DependencyManager` (check_all, all_available, missing_packages, install_command, vendor_dir) |

**Gap:** Torque group operations (`addTorqueGroup`, `getTorqueGroups`, `removeTorqueGroup`, `updateTorqueAxis`, torque round-trip serialization) have NO tests.

### `tests/test_dialog_model_selection.py`
| Module | Coverage |
|--------|----------|
| `FEAInfillExtension.py` | `_ensureDialog`, `showDialogForNode`, `getSceneNodes`, `preselectedNodeKey` property, dialog retry |
| `BoundaryConditionTool.py` | `setOpenOptimizeDialog` (node selected, no selection, value=False, no extension) |

---

## 3. Coverage Gaps Identified

### 3.1 Zero-Coverage Modules

#### `fea/oc_update.py` — CRITICAL
- **`oc_density_update`**: SIMP OC density update with bisection on Lagrange multiplier. No tests.
  - Density bounds `[rho_min, rho_max]` not verified to be maintained.
  - Volume fraction constraint (sum of rho*V = target) not verified.
  - Move limit `move_limit` behavior not tested.
  - Bisection convergence not tested.
  - Degenerate (zero-volume) element handling not tested.
- **`_compute_element_stiffness_and_compliance`**: Vectorized einsum compliance computation. No tests.

#### `fea/face_group_analyzer.py` — CRITICAL
- **`_get_face_vertices`**: Bounds checking for negative/out-of-range `face_idx` and invalid vertex refs. No tests.
- **`_face_normal`**: Degenerate triangles (zero area, NaN/inf vertices). No tests.
- **`_face_centroid`**: No tests.
- **`build_face_adjacency`**: Both indexed and flat mesh modes. No tests.
- **`find_coplanar_group`**: BFS with `max_faces` limit, `angle_threshold_deg`, degenerate seed. No tests.
- **`find_hole_surface`** / **`find_cylinder_surface`**: Concave/convex detection. No tests.
- **`MAX_BFS_ITERATIONS`** limit enforcement. No tests.

#### `operations/bc_operations.py` — HIGH
- `AddFixedFacesOperation.undo/redo`: No tests.
- `RemoveFixedFacesOperation.undo/redo`: No tests.
- `ClearFixedFacesOperation.undo/redo`: No tests.
- `AddForceGroupOperation.undo/redo`: No tests.
- `RemoveForceGroupOperation.undo/redo` (re-insertion at original index): No tests.
- `AddTorqueGroupOperation.undo/redo`: No tests.
- `RemoveTorqueGroupOperation.undo/redo`: No tests.
- `UpdateTorqueAxisOperation.undo/redo`: No tests.
- `ClearAllBCsOperation.undo/redo` (full state snapshot/restore): No tests.

#### `visualization/stress_overlay.py` — MEDIUM (Cura-free helpers testable)
- **`_stress_to_color`**: Colormap interpolation for normalized values. No tests.
- **`_stress_to_color_vectorized`**: Vectorized colormap consistency with scalar. No tests.
- **`_map_element_stress_to_vertices`**: KDTree-based stress mapping from elements to surface vertices. No tests.
- `StressOverlayManager`: Heavy Cura dependency — test world-space transform correctness (overlay parented to scene root) would require extensive mocking.

### 3.2 Partial Coverage — New Features Missing Direct Tests

#### Vectorized FEA functions (`fea/fea_solver.py`)
- **`_von_mises_vectorized`**: Only tested indirectly via `compute_element_stress`. Should verify batch == per-element scalar for random inputs.
- **`_von_mises_directional_vectorized`**: Not directly tested. `k=1.0` should reduce to standard von Mises.
- **`_tsai_hill`**: Single-element scalar version. No direct test.
- **`_tsai_hill_vectorized`**: Batch version. No test verifying consistency with `_tsai_hill`.
- **`compute_element_failure_index`**: Uses Tsai-Hill when `bonding_coeff < 0.95`. Not tested with any non-isotropic scenario.

#### `homogenization.py` — `build_constitutive_matrix_from_bonding`
- Not tested at all. Critical for anisotropic FDM modeling.
- `k=1.0` should reduce to isotropic `build_constitutive_matrix`. Not verified.
- Invalid `k <= 0` or `k > 1` should raise `ValueError`. Not verified.
- Shape/symmetry/positive-definiteness for anisotropic case. Not verified.

#### CG fallback in `LinearElasticitySolver.solve`
- The `spsolve` → CG fallback is never exercised by existing tests.
- A nearly-singular system that causes spsolve to fail/hang would trigger CG.

#### SIMP OC E2E in `IterativeFEASolver`
- The `optimization_method="oc"` path forces scipy solver and uses `oc_density_update`. Completely untested at E2E level.
- OC dual-convergence criteria (`_CONVERGENCE_TOL_OC_DENSITY` + `_CONVERGENCE_TOL_OC_COMPLIANCE`) never exercised.

#### Adaptive damping logic
- `_DAMPING_INITIAL = 0.5`, `_DAMPING_MIN = 0.2` constants not tested.
- Oscillation detection (consecutive max_change increase) not tested.
- Damping reduction factor (`damping * 0.8`) not verified.

#### Torque groups in `FEABoundaryConditionDecorator`
- `addTorqueGroup`, `getTorqueGroups`, `removeTorqueGroup`, `updateTorqueAxis`, `clearTorqueGroups`, `getTorqueGroupCount` — NO tests.
- `TorqueGroup` serialization `to_dict`/`from_dict` round-trip — NO tests.
- Torque groups in `toDict`/`fromDict` round-trip on the decorator — NO tests.

#### BC persistence settings
- `FEABoundaryConditionDecorator.fromDict` restores torque groups (key `"torque_groups"` in dict). The `toDict` test checks the key exists but no torque data is tested.

---

## 4. New Test Files Created

| File | Covers |
|------|--------|
| `tests/test_oc_update.py` | `oc_density_update`, `_compute_element_stiffness_and_compliance`, OC E2E via IterativeFEASolver |
| `tests/test_face_group_analyzer.py` | All face_group_analyzer functions: bounds checking, BFS limits, coplanar/hole/cylinder groups |
| `tests/test_vectorized_fea.py` | `_von_mises_vectorized`, `_tsai_hill`, `_tsai_hill_vectorized`, `build_constitutive_matrix_from_bonding`, `compute_element_failure_index`, stress overlay helpers |
| `tests/test_bc_operations.py` | All undo/redo operation classes, torque group CRUD on decorator |

---

## 5. Cross-Discipline Findings

### For `physics-math-expert`:
- `_tsai_hill` uses `sigma_ip·sz/X²` as the cross-term, where `sigma_ip = sqrt(sx²+sy²-sx·sy+3·τxy²)` is the in-plane von Mises equivalent. **Clarified (2026-04-07): This is a deliberate, rotation-invariant generalization for transversely isotropic (FDM) materials.** The XY plane is isotropic, so individual σ_x/σ_y components are frame-dependent; `sigma_ip` is the correct invariant measure. For uniaxial in-plane loading, `sigma_ip = σ_x` and the formula reduces to classical Tsai-Hill exactly. Slightly conservative for biaxial in-plane + Z combined loading — safe-sided for an infill optimizer. Recommend adding a code comment documenting this design choice.
- `_compute_element_stiffness_and_compliance` uses D0 at base density (pre-scaling), consistent with SIMP sensitivity analysis in Sigmund (2001). Correct.

### For `reliability-expert`:
- CG fallback in `LinearElasticitySolver.solve` has a 30s timeout but uses `threading.Thread(daemon=True)` — on Cura main thread this could create thread lifecycle issues if the dialog is closed during solve.
- `_solve_easyfea` uses 120s per-solve timeout. A 20-iteration solve with 120s each could block for 40 minutes.

### For `performance-expert`:
- `oc_update.py` uses `np.einsum("mij,mj->mi", B_all, u_e_all)` for batch strain — this is O(M·6·12) which is optimal. Good.
- `_build_adjacency_indexed` is O(n_faces) using dict. `_build_adjacency_flat` has the same complexity but involves position hashing. Fine.
- **B_all triple-recomputation confirmed:** `B_all` (shape `(M,6,12)`, depends only on fixed element geometry) is recomputed identically in `assemble_stiffness_matrix`, `_solve_scipy` post-processing, and `_compute_element_stiffness_and_compliance`. Caching on first solve call is a clean fix for ~30-50% CPU reduction per OC iteration (P1 finding from performance review).
- **New benchmark tests added** to `tests/test_vectorized_fea.py` (`TestVectorizedPerformance`): vectorized von Mises ≥10× speedup, vectorized Tsai-Hill ≥5× speedup, stiffness assembly timing for 1K/10K disconnected tets. These serve as regression baselines for the B_all caching fix.

### For `code-quality-expert`:
- `bc_operations.py` `RemoveForceGroupOperation.undo` directly mutates `self._decorator._force_groups` (private attribute). This is a fragile dependency on internal representation.
- `test_mesh_generation.py` duplicates `_ensure_real_module` logic from `test_dialog_model_selection.py`. This should be in `conftest.py`.

---

## 6. Test Framework Assessment

- **Mocking strategy**: Correct — all UM/cura modules stubbed via `sys.modules` before any plugin import. The pattern in `conftest.py` ensures safety.
- **Import paths**: Correct — `plugins/` directory inserted into `sys.path`.
- **Gmsh-dependent tests**: Properly skipped with `@pytest.mark.skipif`.
- **Fixture scoping**: Module-level fixtures for expensive operations (tetrahedralization, solve) is appropriate.
- **Missing conftest setup**: The `test_mesh_generation.py` and `test_dialog_model_selection.py` both re-implement `_ensure_real_module`. This should be extracted to the package-level conftest.
