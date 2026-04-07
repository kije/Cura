# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Comprehensive benchmark test suite for FEA solver algorithms.

Measures performance of every solver step at multiple mesh sizes, detects
performance regressions, and identifies bottlenecks.  All tests run without
Cura/UM imports (stubs installed by root conftest.py).

Mesh sizes
----------
- Tiny:   ~100 elements   (n_per_axis=4)
- Small:  ~1K elements    (n_per_axis=7)
- Medium: ~10K elements   (n_per_axis=14)
- Large:  ~50K elements   (n_per_axis=22)
- XLarge: ~100K elements  (n_per_axis=28)  [marked slow]

Run with:
    source .test-venv/bin/activate
    python -m pytest plugins/FEAInfillOptimizer/tests/test_solver_benchmarks.py -v
    python -m pytest plugins/FEAInfillOptimizer/tests/test_solver_benchmarks.py -v -m "not slow"
"""

import os
import sys
import time

import numpy as np
import pytest
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.spatial import Delaunay

# ---------------------------------------------------------------------------
# Ensure the plugins directory is on sys.path
# ---------------------------------------------------------------------------
_PLUGINS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from FEAInfillOptimizer.fea.fea_solver import (
    LinearElasticitySolver,
    _build_D_matrices,
    _gather_element_displacements,
    _strain_displacement_matrices_vectorized,
    _von_mises_vectorized,
)
from FEAInfillOptimizer.fea.tetrahedralization import TetMesh
from FEAInfillOptimizer.fea.oc_update import oc_density_update


# ===========================================================================
# A) Test mesh generation
# ===========================================================================


def _make_test_mesh(n_points_per_axis: int) -> TetMesh:
    """Generate a cubic tetrahedral mesh using scipy Delaunay.

    Creates a regular grid of ``n_points_per_axis^3`` points inside a
    100 mm cube, then tetrahedralizes them.  The resulting mesh has
    approximately ``5 * (n-1)^3`` elements (Delaunay on a cubic grid
    produces ~5-6 tets per cube cell).

    Args:
        n_points_per_axis: Number of points along each axis.

    Returns:
        TetMesh with nodes, elements, and an empty surface_node_map.
    """
    x = np.linspace(0.0, 100.0, n_points_per_axis)
    xx, yy, zz = np.meshgrid(x, x, x, indexing="ij")
    pts = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])

    tri = Delaunay(pts)
    elements = tri.simplices.astype(np.int64)

    # Build a trivial surface_node_map: boundary nodes map to themselves
    # (sufficient for benchmark purposes)
    eps = 1e-6
    on_boundary = (
        (pts[:, 0] < eps) | (pts[:, 0] > 100.0 - eps)
        | (pts[:, 1] < eps) | (pts[:, 1] > 100.0 - eps)
        | (pts[:, 2] < eps) | (pts[:, 2] > 100.0 - eps)
    )
    boundary_indices = np.where(on_boundary)[0]
    surface_node_map = {int(i): int(i) for i in boundary_indices}

    return TetMesh(
        nodes=pts.astype(np.float64),
        elements=elements,
        surface_node_map=surface_node_map,
    )


def _fixed_nodes_for_mesh(mesh: TetMesh, fraction: float) -> np.ndarray:
    """Select a fraction of nodes on the z=0 face as fixed.

    Picks nodes closest to z=0, up to the requested fraction of total nodes.

    Args:
        mesh: TetMesh to select from.
        fraction: Approximate fraction of total nodes to fix.

    Returns:
        1-D ndarray of unique node indices (int64).
    """
    n_nodes = mesh.nodes.shape[0]
    n_fix = max(3, int(n_nodes * fraction))  # at least 3 for rigid body

    # Sort by z-coordinate, take the lowest n_fix nodes
    z_order = np.argsort(mesh.nodes[:, 2])
    return z_order[:n_fix].astype(np.int64)


def _estimate_peak_memory_mb(n_elems: int) -> float:
    """Estimate peak memory in MB during COO assembly.

    Dominant arrays during assembly:
    - k_e_all:  n_elems * 12 * 12 * 8 bytes  (element stiffness)
    - BtD:      n_elems * 12 * 6 * 8 bytes
    - B_all:    n_elems * 6 * 12 * 8 bytes
    - D_all:    n_elems * 6 * 6 * 8 bytes
    - row_idx:  n_elems * 12 * 12 * 8 bytes
    - col_idx:  n_elems * 12 * 12 * 8 bytes
    - COO data: n_elems * 144 * 8 bytes
    """
    bytes_per_elem = (
        12 * 12 * 8       # k_e_all
        + 12 * 6 * 8      # BtD
        + 6 * 12 * 8      # B_all
        + 6 * 6 * 8       # D_all
        + 12 * 12 * 8     # row_idx
        + 12 * 12 * 8     # col_idx
        + 144 * 8         # COO data
    )
    return n_elems * bytes_per_elem / (1024 * 1024)


# ---------------------------------------------------------------------------
# Mesh size presets (n_points_per_axis, approx elements)
# ---------------------------------------------------------------------------

_MESH_PRESETS = {
    "tiny":   4,   # ~320 elements
    "small":  7,   # ~1.8K elements
    "medium": 14,  # ~13K elements
    "large":  22,  # ~53K elements
    "xlarge": 28,  # ~110K elements
}


# ---------------------------------------------------------------------------
# Timing thresholds (seconds) — generous to avoid CI flakiness.
# Set at approximately 3-5x expected time on modern hardware.
# These catch catastrophic regressions (10x+ slowdown) not micro-regressions.
# ---------------------------------------------------------------------------

THRESHOLDS = {
    # --- Per-step thresholds for ~10K element mesh (medium) ---
    "medium": {
        "b_matrices":           3.0,
        "d_matrices_iso":       0.5,
        "d_matrices_aniso":     1.0,
        "assemble_iso":         5.0,
        "assemble_aniso":       8.0,
        "bcs_5pct":             0.5,
        "solve_spsolve":       15.0,
        "stress":               3.0,
        "compliance":           3.0,
        "oc_update":           20.0,
        "full_heuristic":      30.0,
        "full_oc":             40.0,
    },
    # --- Per-step thresholds for ~1K element mesh (small) ---
    "small": {
        "b_matrices":           0.5,
        "d_matrices_iso":       0.1,
        "d_matrices_aniso":     0.2,
        "assemble_iso":         1.0,
        "assemble_aniso":       1.5,
        "bcs_5pct":             0.1,
        "solve_spsolve":        2.0,
        "stress":               0.5,
        "compliance":           0.5,
        "oc_update":            3.0,
        "full_heuristic":       5.0,
        "full_oc":              8.0,
    },
}


# ===========================================================================
# Fixtures — cached per module to avoid regenerating meshes
# ===========================================================================


@pytest.fixture(scope="module")
def tiny_mesh():
    return _make_test_mesh(_MESH_PRESETS["tiny"])


@pytest.fixture(scope="module")
def small_mesh():
    return _make_test_mesh(_MESH_PRESETS["small"])


@pytest.fixture(scope="module")
def medium_mesh():
    return _make_test_mesh(_MESH_PRESETS["medium"])


@pytest.fixture(scope="module")
def large_mesh():
    return _make_test_mesh(_MESH_PRESETS["large"])


# ===========================================================================
# B) Benchmark each solver step independently
# ===========================================================================


class TestBMatrixComputation:
    """Benchmark _strain_displacement_matrices_vectorized (B matrices)."""

    @pytest.mark.benchmark
    @pytest.mark.parametrize("n_per_axis", [4, 7, 10, 14])
    def test_b_matrix_scaling(self, n_per_axis):
        """B matrix computation time should scale roughly linearly with element count."""
        mesh = _make_test_mesh(n_per_axis)
        n_elems = mesh.elements.shape[0]

        t0 = time.perf_counter()
        B_all, V_all, valid = _strain_displacement_matrices_vectorized(mesh)
        elapsed = time.perf_counter() - t0

        assert B_all.shape == (n_elems, 6, 12)
        assert V_all.shape == (n_elems,)
        assert valid.shape == (n_elems,)
        assert np.all(V_all[valid] > 0)
        print(f"  B matrices: n_per_axis={n_per_axis}, n_elems={n_elems}, "
              f"time={elapsed:.3f}s, per_elem={elapsed/n_elems*1e6:.1f}us")

    def test_b_matrices_small_under_threshold(self, small_mesh):
        t0 = time.perf_counter()
        _strain_displacement_matrices_vectorized(small_mesh)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["small"]["b_matrices"]
        assert elapsed < threshold, (
            f"B matrix computation ({small_mesh.elements.shape[0]} elems) "
            f"took {elapsed:.3f}s > threshold {threshold}s"
        )

    def test_b_matrices_medium_under_threshold(self, medium_mesh):
        t0 = time.perf_counter()
        _strain_displacement_matrices_vectorized(medium_mesh)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["medium"]["b_matrices"]
        assert elapsed < threshold, (
            f"B matrix computation ({medium_mesh.elements.shape[0]} elems) "
            f"took {elapsed:.3f}s > threshold {threshold}s"
        )


class TestDMatrixComputation:
    """Benchmark _build_D_matrices for isotropic and anisotropic cases."""

    @pytest.mark.benchmark
    @pytest.mark.parametrize("n_per_axis,bonding_coeff", [
        (7, 1.0), (7, 0.5), (14, 1.0), (14, 0.5),
    ])
    def test_d_matrix_scaling(self, n_per_axis, bonding_coeff):
        mesh = _make_test_mesh(n_per_axis)
        n_elems = mesh.elements.shape[0]
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        label = "iso" if bonding_coeff >= 1.0 else f"aniso(k={bonding_coeff})"

        t0 = time.perf_counter()
        D_all = _build_D_matrices(E_arr, nu_arr, bonding_coeff)
        elapsed = time.perf_counter() - t0

        assert D_all.shape == (n_elems, 6, 6)
        print(f"  D matrices ({label}): n_per_axis={n_per_axis}, n_elems={n_elems}, "
              f"time={elapsed:.3f}s")

    def test_d_matrices_iso_small_under_threshold(self, small_mesh):
        n_elems = small_mesh.elements.shape[0]
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        t0 = time.perf_counter()
        _build_D_matrices(E_arr, nu_arr, 1.0)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["small"]["d_matrices_iso"]
        assert elapsed < threshold, (
            f"D matrices iso ({n_elems} elems) took {elapsed:.3f}s > {threshold}s"
        )

    def test_d_matrices_aniso_small_under_threshold(self, small_mesh):
        n_elems = small_mesh.elements.shape[0]
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        t0 = time.perf_counter()
        _build_D_matrices(E_arr, nu_arr, 0.5)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["small"]["d_matrices_aniso"]
        assert elapsed < threshold, (
            f"D matrices aniso ({n_elems} elems) took {elapsed:.3f}s > {threshold}s"
        )

    def test_d_matrices_iso_medium_under_threshold(self, medium_mesh):
        n_elems = medium_mesh.elements.shape[0]
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        t0 = time.perf_counter()
        _build_D_matrices(E_arr, nu_arr, 1.0)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["medium"]["d_matrices_iso"]
        assert elapsed < threshold, (
            f"D matrices iso ({n_elems} elems) took {elapsed:.3f}s > {threshold}s"
        )

    def test_d_matrices_aniso_medium_under_threshold(self, medium_mesh):
        n_elems = medium_mesh.elements.shape[0]
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        t0 = time.perf_counter()
        _build_D_matrices(E_arr, nu_arr, 0.5)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["medium"]["d_matrices_aniso"]
        assert elapsed < threshold, (
            f"D matrices aniso ({n_elems} elems) took {elapsed:.3f}s > {threshold}s"
        )

    def test_d_matrices_nonuniform_nu_is_slow_path(self, small_mesh):
        """Non-uniform nu forces per-element D construction (slow path).

        Verifies the slow path still produces correct shapes and catches
        if someone accidentally makes the slow path even slower.
        """
        n_elems = small_mesh.elements.shape[0]
        E_arr = np.full(n_elems, 3000.0)
        # Two distinct nu values force the per-element loop
        nu_arr = np.where(np.arange(n_elems) % 2 == 0, 0.30, 0.36)

        t0 = time.perf_counter()
        D_all = _build_D_matrices(E_arr, nu_arr, 1.0)
        elapsed = time.perf_counter() - t0

        assert D_all.shape == (n_elems, 6, 6)
        # The slow path should still complete in reasonable time
        # for small meshes (< 5s even with per-element loop)
        assert elapsed < 5.0, (
            f"D matrices non-uniform nu ({n_elems} elems) took {elapsed:.3f}s > 5s"
        )
        print(f"  D matrices (non-uniform nu, slow path): {n_elems} elems, "
              f"time={elapsed:.3f}s")


class TestStiffnessAssembly:
    """Benchmark assemble_stiffness_matrix for isotropic and anisotropic cases."""

    @pytest.mark.benchmark
    @pytest.mark.parametrize("n_per_axis", [4, 7, 10, 14])
    def test_assembly_scaling(self, n_per_axis):
        """Assembly time should scale roughly linearly with element count."""
        mesh = _make_test_mesh(n_per_axis)
        n_elems = mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        t0 = time.perf_counter()
        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        elapsed = time.perf_counter() - t0

        n_dof = mesh.nodes.shape[0] * 3
        assert K.shape == (n_dof, n_dof)
        assert K.nnz > 0
        print(f"  Assembly: n_per_axis={n_per_axis}, n_elems={n_elems}, "
              f"n_dof={n_dof}, nnz={K.nnz}, time={elapsed:.3f}s")

    def test_assembly_iso_small_under_threshold(self, small_mesh):
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        t0 = time.perf_counter()
        K = solver.assemble_stiffness_matrix(mesh=small_mesh, E_per_element=E_arr,
                                             nu_per_element=nu_arr)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["small"]["assemble_iso"]
        assert elapsed < threshold, (
            f"Assembly iso ({n_elems} elems) took {elapsed:.3f}s > {threshold}s"
        )

    def test_assembly_aniso_small_under_threshold(self, small_mesh):
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        t0 = time.perf_counter()
        K = solver.assemble_stiffness_matrix(mesh=small_mesh, E_per_element=E_arr,
                                             nu_per_element=nu_arr,
                                             bonding_coeff=0.5)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["small"]["assemble_aniso"]
        assert elapsed < threshold, (
            f"Assembly aniso ({n_elems} elems) took {elapsed:.3f}s > {threshold}s"
        )

    def test_assembly_iso_medium_under_threshold(self, medium_mesh):
        n_elems = medium_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        t0 = time.perf_counter()
        K = solver.assemble_stiffness_matrix(mesh=medium_mesh, E_per_element=E_arr,
                                             nu_per_element=nu_arr)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["medium"]["assemble_iso"]
        assert elapsed < threshold, (
            f"Assembly iso ({n_elems} elems) took {elapsed:.3f}s > {threshold}s"
        )

    def test_assembly_aniso_medium_under_threshold(self, medium_mesh):
        n_elems = medium_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)
        t0 = time.perf_counter()
        K = solver.assemble_stiffness_matrix(mesh=medium_mesh, E_per_element=E_arr,
                                             nu_per_element=nu_arr,
                                             bonding_coeff=0.5)
        elapsed = time.perf_counter() - t0
        threshold = THRESHOLDS["medium"]["assemble_aniso"]
        assert elapsed < threshold, (
            f"Assembly aniso ({n_elems} elems) took {elapsed:.3f}s > {threshold}s"
        )

    @pytest.mark.slow
    def test_assembly_large_completes(self, large_mesh):
        """Large mesh (~50K elems) assembly must complete without OOM or extreme time."""
        n_elems = large_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        mem_est = _estimate_peak_memory_mb(n_elems)
        print(f"  Large mesh: {n_elems} elems, estimated peak memory: {mem_est:.0f} MB")

        t0 = time.perf_counter()
        K = solver.assemble_stiffness_matrix(mesh=large_mesh, E_per_element=E_arr,
                                             nu_per_element=nu_arr)
        elapsed = time.perf_counter() - t0

        assert K.shape[0] == large_mesh.nodes.shape[0] * 3
        assert elapsed < 30.0, (
            f"Large mesh assembly ({n_elems} elems) took {elapsed:.2f}s > 30s"
        )
        print(f"  Large assembly: {n_elems} elems, time={elapsed:.3f}s")


class TestBoundaryConditions:
    """Benchmark apply_boundary_conditions with varying fixed DOF fractions."""

    @pytest.mark.benchmark
    @pytest.mark.parametrize("fix_fraction", [0.01, 0.05, 0.20])
    def test_bc_scaling_small(self, small_mesh, fix_fraction):
        """BC application time for different fixed DOF fractions."""
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        K = solver.assemble_stiffness_matrix(small_mesh, E_arr, nu_arr)
        n_dof = small_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        f[-1] = -100.0  # load on last DOF

        fixed = _fixed_nodes_for_mesh(small_mesh, fix_fraction)

        t0 = time.perf_counter()
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        elapsed = time.perf_counter() - t0

        n_fixed_dofs = len(fixed) * 3
        print(f"  BCs: fix_frac={fix_fraction}, n_fixed_nodes={len(fixed)}, "
              f"n_fixed_dofs={n_fixed_dofs}/{n_dof}, time={elapsed:.3f}s")

        # All BC variations on small mesh should be fast
        assert elapsed < THRESHOLDS["small"]["bcs_5pct"] * 3, (
            f"BC application took {elapsed:.3f}s (fix_frac={fix_fraction})"
        )

    def test_bc_medium_5pct_under_threshold(self, medium_mesh):
        n_elems = medium_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        K = solver.assemble_stiffness_matrix(medium_mesh, E_arr, nu_arr)
        n_dof = medium_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        f[-1] = -100.0

        fixed = _fixed_nodes_for_mesh(medium_mesh, 0.05)

        t0 = time.perf_counter()
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        elapsed = time.perf_counter() - t0

        threshold = THRESHOLDS["medium"]["bcs_5pct"]
        assert elapsed < threshold, (
            f"BC application ({n_elems} elems, 5%) took {elapsed:.3f}s > {threshold}s"
        )


class TestSolve:
    """Benchmark the sparse linear solve (spsolve)."""

    def _prepare_system(self, mesh: TetMesh, fix_fraction: float = 0.05):
        """Assemble K, apply BCs, return (K_bc, f_bc)."""
        n_elems = mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        n_dof = mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        # Apply a downward force on nodes near z=100
        z_max = mesh.nodes[:, 2].max()
        top_nodes = np.where(mesh.nodes[:, 2] > z_max - 1e-6)[0]
        for tn in top_nodes:
            f[tn * 3 + 2] = -100.0 / max(len(top_nodes), 1)

        fixed = _fixed_nodes_for_mesh(mesh, fix_fraction)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        return K_bc, f_bc, solver

    def test_solve_small_under_threshold(self, small_mesh):
        K_bc, f_bc, solver = self._prepare_system(small_mesh)

        t0 = time.perf_counter()
        u = solver.solve(K_bc, f_bc)
        elapsed = time.perf_counter() - t0

        assert u.shape == f_bc.shape
        assert np.all(np.isfinite(u))
        threshold = THRESHOLDS["small"]["solve_spsolve"]
        assert elapsed < threshold, (
            f"Solve ({small_mesh.nodes.shape[0] * 3} DOFs) "
            f"took {elapsed:.3f}s > {threshold}s"
        )
        print(f"  Solve small: {small_mesh.nodes.shape[0]*3} DOFs, "
              f"time={elapsed:.3f}s, max_u={np.max(np.abs(u)):.6f}")

    def test_solve_medium_under_threshold(self, medium_mesh):
        K_bc, f_bc, solver = self._prepare_system(medium_mesh)

        t0 = time.perf_counter()
        u = solver.solve(K_bc, f_bc)
        elapsed = time.perf_counter() - t0

        assert u.shape == f_bc.shape
        assert np.all(np.isfinite(u))
        threshold = THRESHOLDS["medium"]["solve_spsolve"]
        assert elapsed < threshold, (
            f"Solve ({medium_mesh.nodes.shape[0] * 3} DOFs) "
            f"took {elapsed:.3f}s > {threshold}s"
        )
        print(f"  Solve medium: {medium_mesh.nodes.shape[0]*3} DOFs, "
              f"time={elapsed:.3f}s, max_u={np.max(np.abs(u)):.6f}")

    @pytest.mark.slow
    def test_solve_large_completes(self, large_mesh):
        """Large mesh solve must complete within 60s."""
        K_bc, f_bc, solver = self._prepare_system(large_mesh)

        t0 = time.perf_counter()
        u = solver.solve(K_bc, f_bc)
        elapsed = time.perf_counter() - t0

        assert u.shape == f_bc.shape
        assert np.all(np.isfinite(u))
        assert elapsed < 60.0, (
            f"Large solve ({large_mesh.nodes.shape[0] * 3} DOFs) "
            f"took {elapsed:.2f}s > 60s"
        )
        print(f"  Solve large: {large_mesh.nodes.shape[0]*3} DOFs, "
              f"time={elapsed:.3f}s")


class TestStressComputation:
    """Benchmark compute_element_stress (vectorized)."""

    def _prepare_solved(self, mesh: TetMesh):
        """Return (solver, mesh, u, E_arr, nu_arr) with a solved displacement."""
        n_elems = mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        n_dof = mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        z_max = mesh.nodes[:, 2].max()
        top_nodes = np.where(mesh.nodes[:, 2] > z_max - 1e-6)[0]
        for tn in top_nodes:
            f[tn * 3 + 2] = -100.0 / max(len(top_nodes), 1)

        fixed = _fixed_nodes_for_mesh(mesh, 0.05)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        u = solver.solve(K_bc, f_bc)
        return solver, mesh, u, E_arr, nu_arr

    def test_stress_small_under_threshold(self, small_mesh):
        solver, mesh, u, E_arr, nu_arr = self._prepare_solved(small_mesh)

        t0 = time.perf_counter()
        vm = solver.compute_element_stress(mesh, u, E_arr, nu_arr)
        elapsed = time.perf_counter() - t0

        assert vm.shape == (mesh.elements.shape[0],)
        assert np.all(vm >= 0)
        threshold = THRESHOLDS["small"]["stress"]
        assert elapsed < threshold, (
            f"Stress computation ({mesh.elements.shape[0]} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )
        print(f"  Stress small: {mesh.elements.shape[0]} elems, time={elapsed:.3f}s, "
              f"max_vm={np.max(vm):.2f} MPa")

    def test_stress_medium_under_threshold(self, medium_mesh):
        solver, mesh, u, E_arr, nu_arr = self._prepare_solved(medium_mesh)

        t0 = time.perf_counter()
        vm = solver.compute_element_stress(mesh, u, E_arr, nu_arr)
        elapsed = time.perf_counter() - t0

        threshold = THRESHOLDS["medium"]["stress"]
        assert elapsed < threshold, (
            f"Stress computation ({mesh.elements.shape[0]} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )

    def test_compliance_small_under_threshold(self, small_mesh):
        solver, mesh, u, E_arr, nu_arr = self._prepare_solved(small_mesh)

        t0 = time.perf_counter()
        ce, volumes = solver.compute_element_compliance(mesh, u, E_arr, nu_arr)
        elapsed = time.perf_counter() - t0

        assert ce.shape == (mesh.elements.shape[0],)
        assert np.all(ce >= 0)
        threshold = THRESHOLDS["small"]["compliance"]
        assert elapsed < threshold, (
            f"Compliance computation ({mesh.elements.shape[0]} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )

    def test_compliance_medium_under_threshold(self, medium_mesh):
        solver, mesh, u, E_arr, nu_arr = self._prepare_solved(medium_mesh)

        t0 = time.perf_counter()
        ce, volumes = solver.compute_element_compliance(mesh, u, E_arr, nu_arr)
        elapsed = time.perf_counter() - t0

        threshold = THRESHOLDS["medium"]["compliance"]
        assert elapsed < threshold, (
            f"Compliance computation ({mesh.elements.shape[0]} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )


class TestOCUpdateBenchmark:
    """Benchmark oc_density_update."""

    def _prepare_for_oc(self, mesh: TetMesh):
        """Return (density, mesh, u) ready for OC update."""
        n_elems = mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        n_dof = mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        z_max = mesh.nodes[:, 2].max()
        top_nodes = np.where(mesh.nodes[:, 2] > z_max - 1e-6)[0]
        for tn in top_nodes:
            f[tn * 3 + 2] = -100.0 / max(len(top_nodes), 1)

        fixed = _fixed_nodes_for_mesh(mesh, 0.05)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        u = solver.solve(K_bc, f_bc)

        density = np.full(n_elems, 0.5, dtype=np.float64)
        return density, mesh, u

    def test_oc_update_small_under_threshold(self, small_mesh):
        density, mesh, u = self._prepare_for_oc(small_mesh)

        t0 = time.perf_counter()
        rho_new = oc_density_update(
            density, mesh, u,
            E_base=3000.0, nu=0.36, n_exp=1.6,
            rho_min=0.1, rho_max=0.8,
            volume_fraction=0.5,
        )
        elapsed = time.perf_counter() - t0

        assert rho_new.shape == density.shape
        assert np.all(rho_new >= 0.1 - 1e-9)
        assert np.all(rho_new <= 0.8 + 1e-9)
        threshold = THRESHOLDS["small"]["oc_update"]
        assert elapsed < threshold, (
            f"OC update ({mesh.elements.shape[0]} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )
        print(f"  OC update small: {mesh.elements.shape[0]} elems, time={elapsed:.3f}s")

    def test_oc_update_medium_under_threshold(self, medium_mesh):
        density, mesh, u = self._prepare_for_oc(medium_mesh)

        t0 = time.perf_counter()
        rho_new = oc_density_update(
            density, mesh, u,
            E_base=3000.0, nu=0.36, n_exp=1.6,
            rho_min=0.1, rho_max=0.8,
            volume_fraction=0.5,
        )
        elapsed = time.perf_counter() - t0

        threshold = THRESHOLDS["medium"]["oc_update"]
        assert elapsed < threshold, (
            f"OC update ({mesh.elements.shape[0]} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )


# ===========================================================================
# C) End-to-end solver iteration benchmarks
# ===========================================================================


class TestFullIterationBenchmarks:
    """Benchmark complete solver iterations (assemble -> BCs -> solve -> stress -> update)."""

    def _run_heuristic_iteration(self, mesh: TetMesh):
        """Run one full heuristic iteration and return elapsed time."""
        from FEAInfillOptimizer.fea.stress_to_density import stress_to_density

        n_elems = mesh.elements.shape[0]
        solver = LinearElasticitySolver()

        # Material properties
        E_base = 3000.0
        nu = 0.36
        density = np.full(n_elems, 0.45, dtype=np.float64)

        # Effective stiffness
        E_eff = E_base * np.power(density, 1.6)
        E_eff = np.maximum(E_eff, E_base * 0.01)
        nu_arr = np.full(n_elems, nu)

        t_start = time.perf_counter()

        # Step 1: Assemble
        K = solver.assemble_stiffness_matrix(mesh, E_eff, nu_arr)

        # Step 2: BCs
        n_dof = mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        z_max = mesh.nodes[:, 2].max()
        top_nodes = np.where(mesh.nodes[:, 2] > z_max - 1e-6)[0]
        for tn in top_nodes:
            f[tn * 3 + 2] = -100.0 / max(len(top_nodes), 1)
        fixed = _fixed_nodes_for_mesh(mesh, 0.05)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)

        # Step 3: Solve
        u = solver.solve(K_bc, f_bc)

        # Step 4: Stress
        vm = solver.compute_element_stress(mesh, u, E_eff, nu_arr)

        # Step 5: Density update (heuristic)
        density_new = stress_to_density(
            vm, sigma_yield=50.0, rho_min=0.1, rho_max=0.8,
            method="power", safety_factor=2.0,
        )
        density_damped = 0.5 * density + 0.5 * density_new
        density_damped = np.clip(density_damped, 0.1, 0.8)

        elapsed = time.perf_counter() - t_start
        return elapsed, density_damped, vm

    def _run_oc_iteration(self, mesh: TetMesh):
        """Run one full SIMP OC iteration and return elapsed time."""
        n_elems = mesh.elements.shape[0]
        solver = LinearElasticitySolver()

        E_base = 3000.0
        nu = 0.36
        density = np.full(n_elems, 0.45, dtype=np.float64)

        E_eff = E_base * np.power(density, 1.6)
        E_eff = np.maximum(E_eff, E_base * 0.01)
        nu_arr = np.full(n_elems, nu)

        t_start = time.perf_counter()

        # Step 1: Assemble
        K = solver.assemble_stiffness_matrix(mesh, E_eff, nu_arr)

        # Step 2: BCs
        n_dof = mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        z_max = mesh.nodes[:, 2].max()
        top_nodes = np.where(mesh.nodes[:, 2] > z_max - 1e-6)[0]
        for tn in top_nodes:
            f[tn * 3 + 2] = -100.0 / max(len(top_nodes), 1)
        fixed = _fixed_nodes_for_mesh(mesh, 0.05)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)

        # Step 3: Solve
        u = solver.solve(K_bc, f_bc)

        # Step 4: Stress (for reporting)
        vm = solver.compute_element_stress(mesh, u, E_eff, nu_arr)

        # Step 5: OC density update
        density_new = oc_density_update(
            density, mesh, u,
            E_base=E_base, nu=nu, n_exp=1.6,
            rho_min=0.1, rho_max=0.8, volume_fraction=0.5,
        )

        elapsed = time.perf_counter() - t_start
        return elapsed, density_new, vm

    def test_full_heuristic_small_under_threshold(self, small_mesh):
        elapsed, density, vm = self._run_heuristic_iteration(small_mesh)
        threshold = THRESHOLDS["small"]["full_heuristic"]
        n_elems = small_mesh.elements.shape[0]
        assert elapsed < threshold, (
            f"Full heuristic iteration ({n_elems} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )
        print(f"  Full heuristic small: {n_elems} elems, time={elapsed:.3f}s")

    def test_full_heuristic_medium_under_threshold(self, medium_mesh):
        elapsed, density, vm = self._run_heuristic_iteration(medium_mesh)
        threshold = THRESHOLDS["medium"]["full_heuristic"]
        n_elems = medium_mesh.elements.shape[0]
        assert elapsed < threshold, (
            f"Full heuristic iteration ({n_elems} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )
        print(f"  Full heuristic medium: {n_elems} elems, time={elapsed:.3f}s")

    def test_full_oc_small_under_threshold(self, small_mesh):
        elapsed, density, vm = self._run_oc_iteration(small_mesh)
        threshold = THRESHOLDS["small"]["full_oc"]
        n_elems = small_mesh.elements.shape[0]
        assert elapsed < threshold, (
            f"Full OC iteration ({n_elems} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )
        print(f"  Full OC small: {n_elems} elems, time={elapsed:.3f}s")

    def test_full_oc_medium_under_threshold(self, medium_mesh):
        elapsed, density, vm = self._run_oc_iteration(medium_mesh)
        threshold = THRESHOLDS["medium"]["full_oc"]
        n_elems = medium_mesh.elements.shape[0]
        assert elapsed < threshold, (
            f"Full OC iteration ({n_elems} elems) "
            f"took {elapsed:.3f}s > {threshold}s"
        )
        print(f"  Full OC medium: {n_elems} elems, time={elapsed:.3f}s")

    @pytest.mark.slow
    def test_full_heuristic_large(self, large_mesh):
        """Large mesh full iteration must complete within 120s."""
        elapsed, density, vm = self._run_heuristic_iteration(large_mesh)
        n_elems = large_mesh.elements.shape[0]
        assert elapsed < 120.0, (
            f"Full heuristic large ({n_elems} elems) took {elapsed:.2f}s > 120s"
        )
        print(f"  Full heuristic large: {n_elems} elems, time={elapsed:.3f}s")


# ===========================================================================
# D) Regression detection — scaling behavior
# ===========================================================================


class TestScalingBehavior:
    """Verify that solver steps exhibit expected algorithmic scaling.

    These tests run multiple mesh sizes and check that timing grows
    at most quadratically (not exponentially) with element count.
    """

    @pytest.mark.benchmark
    def test_assembly_subquadratic(self):
        """Assembly should be O(n) to O(n log n), not O(n^2)."""
        sizes = [4, 7, 10]
        times = []
        counts = []

        for n in sizes:
            mesh = _make_test_mesh(n)
            n_elems = mesh.elements.shape[0]
            solver = LinearElasticitySolver()
            E_arr = np.full(n_elems, 3000.0)
            nu_arr = np.full(n_elems, 0.36)

            t0 = time.perf_counter()
            solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
            elapsed = time.perf_counter() - t0

            times.append(elapsed)
            counts.append(n_elems)

        # Check that doubling elements does not more than quadruple time
        # (allows O(n log n) but rejects O(n^2))
        for i in range(1, len(times)):
            ratio_elems = counts[i] / counts[i - 1]
            ratio_time = times[i] / max(times[i - 1], 1e-9)
            # Allow quadratic + margin: time ratio <= elem_ratio^2.5
            max_allowed = ratio_elems ** 2.5
            print(f"  Scaling: {counts[i-1]} -> {counts[i]} elems, "
                  f"time ratio={ratio_time:.1f}, elem ratio={ratio_elems:.1f}, "
                  f"max allowed={max_allowed:.1f}")
            assert ratio_time < max_allowed, (
                f"Assembly scaling looks superquadratic: {counts[i-1]} -> {counts[i]} "
                f"elems, time grew {ratio_time:.1f}x vs {ratio_elems:.1f}x elem increase"
            )

    @pytest.mark.benchmark
    def test_stress_linear_scaling(self):
        """Stress computation should scale linearly with element count."""
        sizes = [4, 7, 10]
        times = []
        counts = []

        for n in sizes:
            mesh = _make_test_mesh(n)
            n_elems = mesh.elements.shape[0]
            solver = LinearElasticitySolver()
            E_arr = np.full(n_elems, 3000.0)
            nu_arr = np.full(n_elems, 0.36)

            # Generate displacements
            n_dof = mesh.nodes.shape[0] * 3
            u = np.random.default_rng(42).standard_normal(n_dof) * 0.01

            t0 = time.perf_counter()
            solver.compute_element_stress(mesh, u, E_arr, nu_arr)
            elapsed = time.perf_counter() - t0

            times.append(elapsed)
            counts.append(n_elems)

        for i in range(1, len(times)):
            ratio_elems = counts[i] / counts[i - 1]
            ratio_time = times[i] / max(times[i - 1], 1e-9)
            # Allow quadratic + margin for stress
            max_allowed = ratio_elems ** 2.5
            assert ratio_time < max_allowed, (
                f"Stress scaling superquadratic: {counts[i-1]} -> {counts[i]} "
                f"elems, time grew {ratio_time:.1f}x"
            )


# ===========================================================================
# E) Matrix conditioning tests
# ===========================================================================


class TestMatrixConditioning:
    """Test solver behavior with different matrix conditioning."""

    def test_well_conditioned_uniform_density(self, small_mesh):
        """Uniform density with adequate BCs -> spsolve should complete cleanly."""
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        K = solver.assemble_stiffness_matrix(small_mesh, E_arr, nu_arr)
        n_dof = small_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        f[-1] = -100.0

        fixed = _fixed_nodes_for_mesh(small_mesh, 0.05)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        u = solver.solve(K_bc, f_bc)

        assert np.all(np.isfinite(u))
        assert np.max(np.abs(u)) > 0, "Solution should have non-zero displacements"

    def test_extreme_density_ratio(self, small_mesh):
        """Extreme density ratio (100:1 in E) should still produce finite solution."""
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        nu_arr = np.full(n_elems, 0.36)

        # Alternate between strong and weak elements
        E_arr = np.where(
            np.arange(n_elems) % 2 == 0,
            3000.0,   # strong
            30.0,     # 100x weaker
        )

        K = solver.assemble_stiffness_matrix(small_mesh, E_arr, nu_arr)
        n_dof = small_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        f[-1] = -100.0

        fixed = _fixed_nodes_for_mesh(small_mesh, 0.05)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        u = solver.solve(K_bc, f_bc)

        assert np.all(np.isfinite(u)), "Extreme density ratio produced non-finite displacements"

    def test_very_few_bcs_less_than_1pct(self, small_mesh):
        """Very few fixed DOFs (< 1%) should not crash but may produce large displacements."""
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        K = solver.assemble_stiffness_matrix(small_mesh, E_arr, nu_arr)
        n_dof = small_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        f[-1] = -100.0

        # Fix just 3 nodes (minimum for rigid body constraint in 3D)
        fixed = np.array([0, 1, 2], dtype=np.int64)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)

        # Should not crash; solution may be large but finite
        u = solver.solve(K_bc, f_bc)
        assert u.shape == (n_dof,)
        # The solve method returns zeros on failure, so just check it finishes
        assert np.all(np.isfinite(u))

    def test_no_bcs_returns_zeros(self, tiny_mesh):
        """No BCs at all -> solver should handle gracefully (return zeros or solve)."""
        n_elems = tiny_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        K = solver.assemble_stiffness_matrix(tiny_mesh, E_arr, nu_arr)
        n_dof = tiny_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        f[-1] = -100.0

        # No fixed nodes -> singular matrix
        fixed = np.array([], dtype=np.int64)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)

        # Should not crash or hang indefinitely
        u = solver.solve(K_bc, f_bc)
        assert u.shape == (n_dof,)
        # Result may be zeros (solver detected singularity) or non-finite
        # The solve() method clamps non-finite to zeros
        assert np.all(np.isfinite(u))

    def test_simp_density_variation(self, small_mesh):
        """Realistic SIMP density field (0.1 to 0.8) should solve cleanly."""
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        nu_arr = np.full(n_elems, 0.36)

        # Simulate a SIMP density field: random densities in [0.1, 0.8]
        rng = np.random.default_rng(seed=42)
        density = rng.uniform(0.1, 0.8, n_elems)
        E_arr = 3000.0 * np.power(density, 1.6)
        E_arr = np.maximum(E_arr, 3000.0 * 0.01)  # floor

        K = solver.assemble_stiffness_matrix(small_mesh, E_arr, nu_arr)
        n_dof = small_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        z_max = small_mesh.nodes[:, 2].max()
        top_nodes = np.where(small_mesh.nodes[:, 2] > z_max - 1e-6)[0]
        for tn in top_nodes:
            f[tn * 3 + 2] = -100.0 / max(len(top_nodes), 1)

        fixed = _fixed_nodes_for_mesh(small_mesh, 0.05)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        u = solver.solve(K_bc, f_bc)

        assert np.all(np.isfinite(u))
        assert np.max(np.abs(u)) > 0


# ===========================================================================
# F) Memory usage estimation
# ===========================================================================


class TestMemoryEstimation:
    """Verify memory estimates are reasonable and warn about large meshes."""

    @pytest.mark.parametrize("n_per_axis,max_mem_mb", [
        (4, 50),       # ~320 elems -> < 50 MB
        (7, 100),      # ~1.8K elems -> < 100 MB
        (14, 500),     # ~13K elems -> < 500 MB
        (22, 2000),    # ~53K elems -> < 2000 MB
    ])
    def test_memory_estimate_within_budget(self, n_per_axis, max_mem_mb):
        """Peak memory estimate should stay within the expected budget."""
        mesh = _make_test_mesh(n_per_axis)
        n_elems = mesh.elements.shape[0]
        est = _estimate_peak_memory_mb(n_elems)

        print(f"  Memory: n_per_axis={n_per_axis}, n_elems={n_elems}, "
              f"estimated={est:.0f} MB, budget={max_mem_mb} MB")

        assert est < max_mem_mb, (
            f"Estimated peak memory {est:.0f} MB exceeds budget {max_mem_mb} MB "
            f"for {n_elems} elements"
        )

    def test_memory_scales_linearly_with_elements(self):
        """Memory should scale linearly with element count."""
        n1 = 1000
        n2 = 10000
        m1 = _estimate_peak_memory_mb(n1)
        m2 = _estimate_peak_memory_mb(n2)

        ratio = m2 / m1
        expected_ratio = n2 / n1
        assert abs(ratio - expected_ratio) < 0.1, (
            f"Memory ratio {ratio:.1f} != elem ratio {expected_ratio:.1f}"
        )


# ===========================================================================
# G) B matrix caching tests
# ===========================================================================


class TestBMatrixCaching:
    """Verify that B matrix caching works and provides speedup."""

    def test_b_cache_hit_is_fast(self, small_mesh):
        """Second call to assemble with same mesh should reuse cached B."""
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        # First call — populates cache
        t0 = time.perf_counter()
        K1 = solver.assemble_stiffness_matrix(small_mesh, E_arr, nu_arr)
        t_first = time.perf_counter() - t0

        # Second call — should use cache
        E_arr2 = np.full(n_elems, 2000.0)  # different E, same mesh
        t0 = time.perf_counter()
        K2 = solver.assemble_stiffness_matrix(small_mesh, E_arr2, nu_arr)
        t_second = time.perf_counter() - t0

        print(f"  B cache: first={t_first:.3f}s, second={t_second:.3f}s, "
              f"speedup={t_first/max(t_second, 1e-9):.1f}x")

        # Second call should be faster (B matrices cached)
        # Allow some margin — at least not significantly slower
        assert t_second < t_first * 1.5, (
            f"Cache miss? Second assembly ({t_second:.3f}s) is slower than "
            f"first ({t_first:.3f}s)"
        )

    def test_b_cache_invalidated_on_different_mesh(self, small_mesh, tiny_mesh):
        """Changing mesh object should invalidate the B cache."""
        solver = LinearElasticitySolver()

        # Assemble with small mesh
        n1 = small_mesh.elements.shape[0]
        K1 = solver.assemble_stiffness_matrix(
            small_mesh, np.full(n1, 3000.0), np.full(n1, 0.36)
        )

        # Assemble with tiny mesh — cache should be invalidated
        n2 = tiny_mesh.elements.shape[0]
        K2 = solver.assemble_stiffness_matrix(
            tiny_mesh, np.full(n2, 3000.0), np.full(n2, 0.36)
        )

        # Shapes must differ (different meshes)
        assert K1.shape != K2.shape

    def test_stress_after_assembly_reuses_b_cache(self, small_mesh):
        """compute_element_stress should reuse B matrices cached by assembly."""
        n_elems = small_mesh.elements.shape[0]
        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        # Assembly caches B
        K = solver.assemble_stiffness_matrix(small_mesh, E_arr, nu_arr)
        n_dof = small_mesh.nodes.shape[0] * 3
        u = np.random.default_rng(42).standard_normal(n_dof) * 0.01

        # Stress computation should reuse cached B
        t0 = time.perf_counter()
        vm = solver.compute_element_stress(small_mesh, u, E_arr, nu_arr)
        t_stress = time.perf_counter() - t0

        assert vm.shape == (n_elems,)
        # Stress should be fast since B is cached — well under 1s for small mesh
        assert t_stress < 1.0, (
            f"Stress with cached B took {t_stress:.3f}s — may not be using cache"
        )


# ===========================================================================
# H) Comprehensive timing report (informational, always passes)
# ===========================================================================


class TestTimingReport:
    """Print a comprehensive timing breakdown for the medium mesh.

    This test always passes — its purpose is to print timing data for
    manual inspection and trend tracking.
    """

    def test_print_timing_report(self, medium_mesh):
        """Print detailed timing for every solver step at medium mesh size."""
        n_elems = medium_mesh.elements.shape[0]
        n_nodes = medium_mesh.nodes.shape[0]
        n_dof = n_nodes * 3

        solver = LinearElasticitySolver()
        E_arr = np.full(n_elems, 3000.0)
        nu_arr = np.full(n_elems, 0.36)

        print(f"\n{'='*70}")
        print(f"  TIMING REPORT: {n_elems} elements, {n_nodes} nodes, {n_dof} DOFs")
        print(f"  Estimated peak memory: {_estimate_peak_memory_mb(n_elems):.0f} MB")
        print(f"{'='*70}")

        # B matrices
        t0 = time.perf_counter()
        B_all, V_all, valid = _strain_displacement_matrices_vectorized(medium_mesh)
        t_b = time.perf_counter() - t0
        print(f"  B matrices:             {t_b:8.3f}s  "
              f"({n_elems} elems, {np.sum(valid)} valid)")

        # D matrices (iso)
        t0 = time.perf_counter()
        D_iso = _build_D_matrices(E_arr, nu_arr, 1.0)
        t_d_iso = time.perf_counter() - t0
        print(f"  D matrices (iso):       {t_d_iso:8.3f}s")

        # D matrices (aniso)
        t0 = time.perf_counter()
        D_aniso = _build_D_matrices(E_arr, nu_arr, 0.5)
        t_d_aniso = time.perf_counter() - t0
        print(f"  D matrices (aniso):     {t_d_aniso:8.3f}s")

        # Assembly (iso) — uses fresh solver to measure without B cache
        solver_fresh = LinearElasticitySolver()
        t0 = time.perf_counter()
        K = solver_fresh.assemble_stiffness_matrix(medium_mesh, E_arr, nu_arr)
        t_asm = time.perf_counter() - t0
        print(f"  Assembly (iso, cold):   {t_asm:8.3f}s  (nnz={K.nnz})")

        # Assembly with cache hit
        t0 = time.perf_counter()
        K2 = solver_fresh.assemble_stiffness_matrix(
            medium_mesh, E_arr * 0.5, nu_arr
        )
        t_asm_cached = time.perf_counter() - t0
        print(f"  Assembly (iso, cached): {t_asm_cached:8.3f}s  "
              f"(speedup: {t_asm/max(t_asm_cached, 1e-9):.1f}x)")

        # Assembly (aniso)
        solver_aniso = LinearElasticitySolver()
        t0 = time.perf_counter()
        K_aniso = solver_aniso.assemble_stiffness_matrix(
            medium_mesh, E_arr, nu_arr, bonding_coeff=0.5
        )
        t_asm_aniso = time.perf_counter() - t0
        print(f"  Assembly (aniso):       {t_asm_aniso:8.3f}s")

        # BCs
        f = np.zeros(n_dof)
        z_max = medium_mesh.nodes[:, 2].max()
        top_nodes = np.where(medium_mesh.nodes[:, 2] > z_max - 1e-6)[0]
        for tn in top_nodes:
            f[tn * 3 + 2] = -100.0 / max(len(top_nodes), 1)
        fixed = _fixed_nodes_for_mesh(medium_mesh, 0.05)

        t0 = time.perf_counter()
        K_bc, f_bc = solver_fresh.apply_boundary_conditions(K, f, fixed)
        t_bc = time.perf_counter() - t0
        print(f"  BCs (5% fixed):         {t_bc:8.3f}s  "
              f"({len(fixed)} fixed nodes, {len(fixed)*3} fixed DOFs)")

        # Solve
        t0 = time.perf_counter()
        u = solver_fresh.solve(K_bc, f_bc)
        t_solve = time.perf_counter() - t0
        print(f"  Solve (spsolve):        {t_solve:8.3f}s  "
              f"(max_u={np.max(np.abs(u)):.6f})")

        # Stress
        t0 = time.perf_counter()
        vm = solver_fresh.compute_element_stress(
            medium_mesh, u, E_arr, nu_arr
        )
        t_stress = time.perf_counter() - t0
        print(f"  Stress (von Mises):     {t_stress:8.3f}s  "
              f"(max={np.max(vm):.2f} MPa)")

        # Compliance
        t0 = time.perf_counter()
        ce, volumes = solver_fresh.compute_element_compliance(
            medium_mesh, u, E_arr, nu_arr
        )
        t_compliance = time.perf_counter() - t0
        print(f"  Compliance:             {t_compliance:8.3f}s  "
              f"(total={np.sum(ce):.4f})")

        # OC update
        density = np.full(n_elems, 0.5)
        t0 = time.perf_counter()
        rho_new = oc_density_update(
            density, medium_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=1.6,
            rho_min=0.1, rho_max=0.8, volume_fraction=0.5,
        )
        t_oc = time.perf_counter() - t0
        print(f"  OC update:              {t_oc:8.3f}s  "
              f"(mean_rho={np.mean(rho_new):.3f})")

        t_total = t_asm + t_bc + t_solve + t_stress + t_oc
        print(f"  {'─'*50}")
        print(f"  Full iteration (est):   {t_total:8.3f}s")
        print(f"{'='*70}")

        # Bottleneck analysis
        steps = {
            "B matrices": t_b,
            "D matrices (iso)": t_d_iso,
            "Assembly": t_asm,
            "BCs": t_bc,
            "Solve": t_solve,
            "Stress": t_stress,
            "OC update": t_oc,
        }
        slowest = max(steps, key=steps.get)
        print(f"  BOTTLENECK: {slowest} ({steps[slowest]:.3f}s, "
              f"{steps[slowest]/t_total*100:.0f}% of total)")
        print()
