# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Unit tests for vectorized FEA functions and recently-added features.

Covers:
- build_constitutive_matrix_from_bonding (anisotropic D matrix)
- _von_mises_vectorized: batch == per-element scalar
- _von_mises_directional_vectorized: k=1.0 → standard von Mises
- _tsai_hill: scalar Tsai-Hill failure index
- _tsai_hill_vectorized: consistency with per-element _tsai_hill
- compute_element_failure_index: Tsai-Hill path vs von Mises path
- stress_overlay helpers: _stress_to_color, _stress_to_color_vectorized

No Cura/UM imports required.

Run with:
    source .test-venv/bin/activate
    python -m pytest plugins/FEAInfillOptimizer/tests/test_vectorized_fea.py -v
"""

import os
import sys

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Ensure the plugins directory is on sys.path
# ---------------------------------------------------------------------------
_PLUGINS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from FEAInfillOptimizer.fea.homogenization import (
    build_constitutive_matrix,
    build_constitutive_matrix_from_bonding,
)
from FEAInfillOptimizer.fea.fea_solver import (
    LinearElasticitySolver,
    _tsai_hill,
    _tsai_hill_vectorized,
    _von_mises,
    _von_mises_directional_vectorized,
    _von_mises_vectorized,
)
from FEAInfillOptimizer.fea.tetrahedralization import TetMesh


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def two_tet_mesh():
    nodes = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 1.0, 1.0],
        ],
        dtype=np.float64,
    )
    elements = np.array([[0, 1, 2, 3], [1, 2, 3, 4]], dtype=np.int64)
    return TetMesh(nodes=nodes, elements=elements, surface_node_map={})


# ===========================================================================
# 1. build_constitutive_matrix_from_bonding
# ===========================================================================


class TestBuildConstitutiveMatrixFromBonding:

    def test_shape_is_6x6(self):
        D = build_constitutive_matrix_from_bonding(3000.0, 0.36, k=0.6)
        assert D.shape == (6, 6)

    def test_dtype_float64(self):
        D = build_constitutive_matrix_from_bonding(3000.0, 0.36, k=0.6)
        assert D.dtype == np.float64

    def test_symmetry(self):
        D = build_constitutive_matrix_from_bonding(3000.0, 0.36, k=0.7)
        np.testing.assert_allclose(D, D.T, atol=1e-10)

    def test_positive_definite(self):
        D = build_constitutive_matrix_from_bonding(3000.0, 0.36, k=0.5)
        eigvals = np.linalg.eigvalsh(D)
        assert np.all(eigvals > 0), f"Non-positive eigenvalue: {eigvals.min()}"

    def test_k_equals_one_recovers_isotropic(self):
        """k=1.0 must give the same matrix as build_constitutive_matrix."""
        E, nu = 3000.0, 0.36
        D_iso = build_constitutive_matrix(E, nu)
        D_bonding = build_constitutive_matrix_from_bonding(E, nu, k=1.0)
        np.testing.assert_allclose(D_bonding, D_iso, rtol=1e-8)

    def test_k_below_one_reduces_transverse_stiffness(self):
        """k < 1 must reduce the [2,2] (Z-direction normal stiffness) entry."""
        E, nu = 3000.0, 0.36
        D_iso = build_constitutive_matrix(E, nu)
        D_aniso = build_constitutive_matrix_from_bonding(E, nu, k=0.5)
        # D[2,2] is σ_z / ε_z → lower with weaker interlayer bonding
        assert D_aniso[2, 2] < D_iso[2, 2], (
            f"D_aniso[2,2]={D_aniso[2,2]:.1f} should be < D_iso[2,2]={D_iso[2,2]:.1f}"
        )

    def test_invalid_k_zero_raises(self):
        with pytest.raises(ValueError, match="Bonding coefficient"):
            build_constitutive_matrix_from_bonding(3000.0, 0.36, k=0.0)

    def test_invalid_k_negative_raises(self):
        with pytest.raises(ValueError):
            build_constitutive_matrix_from_bonding(3000.0, 0.36, k=-0.1)

    def test_invalid_k_above_one_raises(self):
        with pytest.raises(ValueError):
            build_constitutive_matrix_from_bonding(3000.0, 0.36, k=1.5)

    @pytest.mark.parametrize("k", [0.3, 0.5, 0.7, 0.9, 1.0])
    def test_positive_definite_for_range_of_k(self, k):
        D = build_constitutive_matrix_from_bonding(3000.0, 0.36, k=k)
        eigvals = np.linalg.eigvalsh(D)
        assert np.all(eigvals > 0), f"k={k}: non-positive eigenvalue {eigvals.min()}"


# ===========================================================================
# 2. _von_mises_vectorized vs scalar _von_mises
# ===========================================================================


class TestVonMisesVectorized:

    def test_zero_stress_gives_zero(self):
        stress = np.zeros((5, 6), dtype=np.float64)
        result = _von_mises_vectorized(stress)
        np.testing.assert_allclose(result, 0.0, atol=1e-12)

    def test_uniaxial_tension(self):
        """σ_xx = S → σ_vm = S for each element."""
        S = 100.0
        stress = np.zeros((3, 6), dtype=np.float64)
        stress[:, 0] = S
        result = _von_mises_vectorized(stress)
        np.testing.assert_allclose(result, S, rtol=1e-8)

    def test_hydrostatic_gives_zero(self):
        p = 50.0
        stress = np.zeros((4, 6), dtype=np.float64)
        stress[:, 0] = p; stress[:, 1] = p; stress[:, 2] = p
        result = _von_mises_vectorized(stress)
        np.testing.assert_allclose(result, 0.0, atol=1e-8)

    def test_batch_matches_scalar(self):
        """_von_mises_vectorized must match _von_mises applied per-element."""
        rng = np.random.default_rng(seed=123)
        stress = rng.standard_normal((50, 6)) * 100.0
        vm_batch = _von_mises_vectorized(stress)
        vm_scalar = np.array([_von_mises(stress[i]) for i in range(50)])
        np.testing.assert_allclose(vm_batch, vm_scalar, rtol=1e-8)

    def test_output_non_negative(self):
        rng = np.random.default_rng(seed=7)
        stress = rng.standard_normal((100, 6)) * 200.0
        result = _von_mises_vectorized(stress)
        assert np.all(result >= 0.0)

    def test_output_shape(self):
        stress = np.ones((17, 6), dtype=np.float64)
        assert _von_mises_vectorized(stress).shape == (17,)


# ===========================================================================
# 3. _von_mises_directional_vectorized
# ===========================================================================


class TestVonMisesDirectionalVectorized:

    def test_k_one_matches_standard_von_mises(self):
        """k=1.0 → directional reduces to standard von Mises."""
        rng = np.random.default_rng(seed=42)
        stress = rng.standard_normal((30, 6)) * 50.0
        vm_std = _von_mises_vectorized(stress)
        vm_dir = _von_mises_directional_vectorized(stress, k=1.0)
        np.testing.assert_allclose(vm_dir, vm_std, rtol=1e-8)

    def test_low_k_amplifies_z_stress(self):
        """k < 1 scales sz/k → larger effective Z component → higher VM for z-loaded state."""
        stress = np.zeros((1, 6), dtype=np.float64)
        stress[0, 2] = 10.0  # pure sz
        vm_iso = _von_mises_vectorized(stress)
        vm_dir = _von_mises_directional_vectorized(stress, k=0.5)
        assert float(vm_dir[0]) > float(vm_iso[0]), "Directional VM must amplify z-stress for k<1"

    def test_output_non_negative(self):
        rng = np.random.default_rng(99)
        stress = rng.standard_normal((50, 6)) * 100.0
        result = _von_mises_directional_vectorized(stress, k=0.6)
        assert np.all(result >= 0.0)


# ===========================================================================
# 4. _tsai_hill (scalar)
# ===========================================================================


class TestTsaiHill:

    def test_zero_stress_gives_zero_index(self):
        stress = np.zeros(6)
        fi = _tsai_hill(stress, yield_strength=50.0, bonding_coeff=0.7)
        assert fi == pytest.approx(0.0, abs=1e-12)

    def test_at_yield_in_plane_gives_approximately_one(self):
        """Pure in-plane uniaxial stress at yield_strength → fi ≈ 1.0."""
        ys = 50.0
        stress = np.array([ys, 0.0, 0.0, 0.0, 0.0, 0.0])
        fi = _tsai_hill(stress, yield_strength=ys, bonding_coeff=1.0)
        # With sz=0, tau_op=0, the formula reduces to (sigma_ip/X)^2 = (ys/X)^2 = 1
        assert fi == pytest.approx(1.0, rel=1e-5)

    def test_result_non_negative(self):
        rng = np.random.default_rng(55)
        for _ in range(50):
            stress = rng.standard_normal(6) * 30.0
            fi = _tsai_hill(stress, yield_strength=50.0, bonding_coeff=0.7)
            assert fi >= 0.0

    def test_result_is_float(self):
        stress = np.array([10.0, 5.0, 3.0, 2.0, 1.0, 1.5])
        result = _tsai_hill(stress, yield_strength=50.0, bonding_coeff=0.8)
        assert isinstance(result, float)

    def test_lower_bonding_coeff_gives_higher_failure_index_for_z_stress(self):
        """Lower bonding_coeff → weaker Z direction → higher failure index for z-loaded state."""
        stress = np.array([0.0, 0.0, 20.0, 0.0, 0.0, 0.0])  # pure sz
        fi_strong = _tsai_hill(stress, yield_strength=50.0, bonding_coeff=1.0)
        fi_weak = _tsai_hill(stress, yield_strength=50.0, bonding_coeff=0.5)
        assert fi_weak > fi_strong, (
            f"Weaker bonding should give higher failure index: {fi_weak} vs {fi_strong}"
        )


# ===========================================================================
# 5. _tsai_hill_vectorized vs scalar _tsai_hill
# ===========================================================================


class TestTsaiHillVectorized:

    def test_batch_matches_scalar(self):
        """_tsai_hill_vectorized must match _tsai_hill per-element."""
        rng = np.random.default_rng(seed=11)
        stress = rng.standard_normal((40, 6)) * 30.0
        ys = 50.0
        k = 0.7
        fi_batch = _tsai_hill_vectorized(stress, ys, k)
        fi_scalar = np.array([_tsai_hill(stress[i], ys, k) for i in range(40)])
        np.testing.assert_allclose(fi_batch, fi_scalar, rtol=1e-6, atol=1e-10)

    def test_zero_stress_gives_zero(self):
        stress = np.zeros((5, 6))
        result = _tsai_hill_vectorized(stress, 50.0, 0.7)
        np.testing.assert_allclose(result, 0.0, atol=1e-12)

    def test_output_non_negative(self):
        rng = np.random.default_rng(33)
        stress = rng.standard_normal((100, 6)) * 50.0
        result = _tsai_hill_vectorized(stress, 50.0, 0.6)
        assert np.all(result >= 0.0)

    def test_output_shape(self):
        stress = np.ones((13, 6))
        assert _tsai_hill_vectorized(stress, 50.0, 0.8).shape == (13,)


# ===========================================================================
# 6. compute_element_failure_index
# ===========================================================================


class TestComputeElementFailureIndex:

    @pytest.fixture
    def loaded_mesh_and_displacement(self, two_tet_mesh):
        """Return a two-tet mesh with a non-trivial displacement vector."""
        solver = LinearElasticitySolver()
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        E_arr = np.full(2, 3000.0)
        nu_arr = np.full(2, 0.36)
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        f = np.zeros(n_dof)
        fixed = np.array([0, 1, 2], dtype=np.int64)
        f[4 * 3 + 2] = 500.0
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed)
        u = solver.solve(K_bc, f_bc)
        return two_tet_mesh, u, E_arr, nu_arr

    def test_isotropic_path_returns_non_negative(self, loaded_mesh_and_displacement):
        mesh, u, E_arr, nu_arr = loaded_mesh_and_displacement
        solver = LinearElasticitySolver()
        result = solver.compute_element_failure_index(
            mesh, u, E_arr, nu_arr, bonding_coeff=1.0, yield_strength=50.0
        )
        assert np.all(result >= 0.0)

    def test_tsai_hill_path_activates_for_low_bonding(self, loaded_mesh_and_displacement):
        """bonding_coeff < 0.95 should use Tsai-Hill and return non-negative values."""
        mesh, u, E_arr, nu_arr = loaded_mesh_and_displacement
        solver = LinearElasticitySolver()
        result = solver.compute_element_failure_index(
            mesh, u, E_arr, nu_arr, bonding_coeff=0.7, yield_strength=50.0
        )
        assert result.shape == (2,)
        assert np.all(result >= 0.0)
        assert np.all(np.isfinite(result))

    def test_isotropic_vs_anisotropic_shape_same(self, loaded_mesh_and_displacement):
        mesh, u, E_arr, nu_arr = loaded_mesh_and_displacement
        solver = LinearElasticitySolver()
        r_iso = solver.compute_element_failure_index(
            mesh, u, E_arr, nu_arr, bonding_coeff=1.0, yield_strength=50.0
        )
        r_aniso = solver.compute_element_failure_index(
            mesh, u, E_arr, nu_arr, bonding_coeff=0.6, yield_strength=50.0
        )
        assert r_iso.shape == r_aniso.shape

    def test_zero_displacement_gives_zero_failure_index(self, two_tet_mesh):
        solver = LinearElasticitySolver()
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        u = np.zeros(n_dof)
        E_arr = np.full(2, 3000.0)
        nu_arr = np.full(2, 0.36)
        result = solver.compute_element_failure_index(
            two_tet_mesh, u, E_arr, nu_arr, bonding_coeff=0.7, yield_strength=50.0
        )
        np.testing.assert_allclose(result, 0.0, atol=1e-10)


# ===========================================================================
# 7. stress_overlay helpers (Cura-free pure functions)
# ===========================================================================

# Import after stubbing any cura deps that stress_overlay.py pulls in at
# module level.  The module uses `from cura.CuraApplication import ...` at
# the top — we need to stub that.

import types as _types


def _stub_cura_for_overlay():
    """Register minimal cura/UM stubs so stress_overlay.py can be imported."""
    for name in [
        "cura", "cura.CuraApplication", "cura.Scene", "cura.Scene.CuraSceneNode",
        "UM", "UM.Math", "UM.Math.Color", "UM.Mesh", "UM.Mesh.MeshBuilder",
        "UM.Operations", "UM.Operations.AddSceneNodeOperation",
        "UM.Operations.GroupedOperation", "UM.Operations.RemoveSceneNodeOperation",
    ]:
        if name not in sys.modules:
            mod = _types.ModuleType(name)
            sys.modules[name] = mod

    from unittest.mock import MagicMock
    sys.modules["cura.CuraApplication"].CuraApplication = MagicMock(name="CuraApplication")
    sys.modules["cura.Scene.CuraSceneNode"].CuraSceneNode = MagicMock(name="CuraSceneNode")
    sys.modules["UM.Math.Color"].Color = MagicMock(name="Color")
    sys.modules["UM.Mesh.MeshBuilder"].MeshBuilder = MagicMock(name="MeshBuilder")
    sys.modules["UM.Operations.AddSceneNodeOperation"].AddSceneNodeOperation = MagicMock()
    sys.modules["UM.Operations.GroupedOperation"].GroupedOperation = MagicMock()
    sys.modules["UM.Operations.RemoveSceneNodeOperation"].RemoveSceneNodeOperation = MagicMock()


_stub_cura_for_overlay()

from FEAInfillOptimizer.visualization.stress_overlay import (  # noqa: E402
    _stress_to_color,
    _stress_to_color_vectorized,
    _COLORMAP,
)


class TestStressToColor:

    def test_zero_maps_to_darkest_colormap_color(self):
        rgb = _stress_to_color(0.0)
        expected = np.array(_COLORMAP[0][1], dtype=np.float32)
        np.testing.assert_allclose(rgb, expected, atol=1e-5)

    def test_one_maps_to_brightest_colormap_color(self):
        rgb = _stress_to_color(1.0)
        expected = np.array(_COLORMAP[-1][1], dtype=np.float32)
        np.testing.assert_allclose(rgb, expected, atol=1e-5)

    def test_output_shape_is_3(self):
        assert _stress_to_color(0.5).shape == (3,)

    def test_output_dtype_float32(self):
        assert _stress_to_color(0.5).dtype == np.float32

    def test_output_in_zero_one_range(self):
        for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
            rgb = _stress_to_color(t)
            assert np.all(rgb >= 0.0)
            assert np.all(rgb <= 1.0)

    def test_below_zero_clamped_to_zero(self):
        rgb_neg = _stress_to_color(-1.0)
        rgb_zero = _stress_to_color(0.0)
        np.testing.assert_allclose(rgb_neg, rgb_zero, atol=1e-6)

    def test_above_one_clamped_to_one(self):
        rgb_high = _stress_to_color(2.0)
        rgb_one = _stress_to_color(1.0)
        np.testing.assert_allclose(rgb_high, rgb_one, atol=1e-6)


class TestStressToColorVectorized:

    def test_batch_matches_scalar(self):
        """Vectorized must match scalar for each value."""
        t_values = np.array([0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0])
        batch = _stress_to_color_vectorized(t_values)
        for i, t in enumerate(t_values):
            scalar = _stress_to_color(float(t))
            np.testing.assert_allclose(batch[i], scalar, atol=1e-4, rtol=1e-4)

    def test_output_shape(self):
        t = np.linspace(0, 1, 20)
        result = _stress_to_color_vectorized(t)
        assert result.shape == (20, 3)

    def test_output_dtype_float32(self):
        t = np.array([0.5])
        assert _stress_to_color_vectorized(t).dtype == np.float32

    def test_clamping_below_zero(self):
        t = np.array([-0.5, 0.0])
        result = _stress_to_color_vectorized(t)
        np.testing.assert_allclose(result[0], result[1], atol=1e-5)

    def test_clamping_above_one(self):
        t = np.array([1.0, 2.0])
        result = _stress_to_color_vectorized(t)
        np.testing.assert_allclose(result[0], result[1], atol=1e-5)

    def test_all_values_in_unit_range(self):
        t = np.random.default_rng(5).uniform(-1, 2, 100)
        result = _stress_to_color_vectorized(t)
        assert np.all(result >= 0.0)
        assert np.all(result <= 1.0)


# ===========================================================================
# 8. Performance benchmarks — vectorized ops vs per-element Python loops
# ===========================================================================

import time as _time


def _make_disconnected_tet_mesh(n_elements: int) -> TetMesh:
    """Build n_elements non-overlapping unit tetrahedra for performance tests.

    Each tet has its own 4 nodes (no shared nodes), giving a block-diagonal
    stiffness matrix.  No gmsh required.  Volume of each tet = 1/6.
    """
    base = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    rng = np.random.default_rng(42)
    offsets = rng.uniform(0.0, 1000.0, (n_elements, 3))
    # Vectorized: shape (N, 4, 3)
    nodes = (base[np.newaxis, :, :] + offsets[:, np.newaxis, :]).reshape(-1, 3)
    elements = np.arange(n_elements * 4, dtype=np.int64).reshape(n_elements, 4)
    return TetMesh(nodes=nodes, elements=elements, surface_node_map={})


_N_PERF = 10_000  # element/sample count for perf benchmarks


class TestVectorizedPerformance:
    """Regression benchmarks: vectorized ops must beat per-element Python loops.

    These tests protect against regressions when the vectorized implementations
    are refactored.  They also serve as baselines for the B_all caching fix
    (P1 finding from performance review): post-fix, stiffness assembly timing
    should drop substantially below the thresholds below.
    """

    def test_von_mises_vectorized_10x_faster_than_scalar_loop(self):
        """_von_mises_vectorized must be ≥10× faster than a Python loop."""
        rng = np.random.default_rng(0)
        stress = rng.standard_normal((_N_PERF, 6)) * 100.0

        t0 = _time.perf_counter()
        result_vec = _von_mises_vectorized(stress)
        t_vec = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        result_scalar = np.array([_von_mises(stress[i]) for i in range(_N_PERF)])
        t_scalar = _time.perf_counter() - t0

        np.testing.assert_allclose(result_vec, result_scalar, rtol=1e-8)
        speedup = t_scalar / max(t_vec, 1e-9)
        assert speedup >= 10.0, (
            f"von Mises vectorized speedup {speedup:.1f}x < 10x target "
            f"(vec={t_vec * 1e3:.1f}ms scalar={t_scalar * 1e3:.1f}ms)"
        )

    def test_tsai_hill_vectorized_5x_faster_than_scalar_loop(self):
        """_tsai_hill_vectorized must be ≥5× faster than a Python loop."""
        rng = np.random.default_rng(1)
        stress = rng.standard_normal((_N_PERF, 6)) * 30.0
        ys, k = 50.0, 0.7

        t0 = _time.perf_counter()
        result_vec = _tsai_hill_vectorized(stress, ys, k)
        t_vec = _time.perf_counter() - t0

        t0 = _time.perf_counter()
        result_scalar = np.array([_tsai_hill(stress[i], ys, k) for i in range(_N_PERF)])
        t_scalar = _time.perf_counter() - t0

        np.testing.assert_allclose(result_vec, result_scalar, rtol=1e-6, atol=1e-10)
        speedup = t_scalar / max(t_vec, 1e-9)
        assert speedup >= 5.0, (
            f"Tsai-Hill vectorized speedup {speedup:.1f}x < 5x target "
            f"(vec={t_vec * 1e3:.1f}ms scalar={t_scalar * 1e3:.1f}ms)"
        )

    def test_stiffness_assembly_1k_elements_completes_under_2s(self):
        """Stiffness assembly for 1K disconnected tets must complete within 2s."""
        mesh = _make_disconnected_tet_mesh(1_000)
        solver = LinearElasticitySolver()
        E_arr = np.full(1_000, 3000.0)
        nu_arr = np.full(1_000, 0.36)

        t0 = _time.perf_counter()
        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        elapsed = _time.perf_counter() - t0

        assert K is not None
        assert elapsed < 2.0, (
            f"1K-element assembly took {elapsed:.2f}s (threshold: 2s)"
        )

    @pytest.mark.slow
    def test_stiffness_assembly_10k_elements_completes_under_15s(self):
        """10K-element stiffness assembly: regression baseline for B_all caching fix.

        Post-fix (B_all cached across iterations), this should drop well below 15s.
        Pre-fix, B_all is recomputed 3× per iteration; this threshold accommodates
        the un-optimized path.
        """
        mesh = _make_disconnected_tet_mesh(10_000)
        solver = LinearElasticitySolver()
        E_arr = np.full(10_000, 3000.0)
        nu_arr = np.full(10_000, 0.36)

        t0 = _time.perf_counter()
        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        elapsed = _time.perf_counter() - t0

        assert K is not None
        assert elapsed < 15.0, (
            f"10K-element assembly took {elapsed:.2f}s (threshold: 15s)"
        )
