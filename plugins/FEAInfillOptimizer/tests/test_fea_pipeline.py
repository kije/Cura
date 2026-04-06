# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Comprehensive unit tests for FEA math modules.

The conftest.py in this directory patches all UM.* / cura.* imports before
collection, so every module import here is safe.

Run with:
    source .test-venv/bin/activate
    python -m pytest plugins/FEAInfillOptimizer/tests/test_fea_pipeline.py -v
"""

import os
import sys
import warnings

import numpy as np
import pytest
import scipy.sparse as sp

# ---------------------------------------------------------------------------
# Ensure the plugins directory is on sys.path so FEAInfillOptimizer is importable.
# ---------------------------------------------------------------------------
_PLUGINS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

# ---------------------------------------------------------------------------
# Imports — conftest.py has already injected all UM/cura stubs into sys.modules
# ---------------------------------------------------------------------------

from FEAInfillOptimizer.fea.homogenization import (
    build_constitutive_matrix,
    effective_properties,
    _PATTERN_EXPONENTS,
    _DEFAULT_EXPONENT,
)
from FEAInfillOptimizer.fea.material_database import Material, MaterialDatabase
from FEAInfillOptimizer.fea.stress_to_density import stress_to_density
from FEAInfillOptimizer.fea.tetrahedralization import TetMesh
from FEAInfillOptimizer.fea.fea_solver import (
    LinearElasticitySolver,
    _strain_displacement_matrix,
    _von_mises,
)


# ===========================================================================
# Shared fixtures
# ===========================================================================


@pytest.fixture
def pla_material():
    """Return the PLA material from the database."""
    return MaterialDatabase.get_material("PLA")


@pytest.fixture
def unit_tet_nodes():
    """Four nodes of a canonical right-angle tetrahedron with unit edge length.

    Volume = (1/6) * |det J| = (1/6) * 1 = 1/6.
    """
    return (
        np.array([0.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    )


@pytest.fixture
def two_tet_mesh():
    """A minimal valid TetMesh built from 5 nodes / 2 tetrahedra.

    Nodes span [0,1]^3 (a unit cube divided into two tets sharing face 0-1-2-3).
    This is sufficient to test stiffness assembly and solving.
    """
    nodes = np.array(
        [
            [0.0, 0.0, 0.0],  # 0
            [1.0, 0.0, 0.0],  # 1
            [0.0, 1.0, 0.0],  # 2
            [0.0, 0.0, 1.0],  # 3
            [1.0, 1.0, 1.0],  # 4
        ],
        dtype=np.float64,
    )
    elements = np.array(
        [
            [0, 1, 2, 3],
            [1, 2, 3, 4],
        ],
        dtype=np.int64,
    )
    surface_node_map = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
    return TetMesh(nodes=nodes, elements=elements, surface_node_map=surface_node_map)


def _make_cube_trimesh(side: float = 1.0):
    """Return a watertight trimesh unit-cube of the given side length."""
    import trimesh
    box = trimesh.creation.box(extents=[side, side, side])
    # translate so the box occupies [0, side]^3 rather than [-side/2, side/2]^3
    box.apply_translation([side / 2, side / 2, side / 2])
    return box


# ===========================================================================
# 1. homogenization.py
# ===========================================================================


class TestBuildConstitutiveMatrix:
    """Tests for build_constitutive_matrix(E, nu)."""

    # --- Shape and symmetry ---

    def test_shape_is_6x6(self):
        D = build_constitutive_matrix(3000.0, 0.36)
        assert D.shape == (6, 6)

    def test_dtype_is_float64(self):
        D = build_constitutive_matrix(3000.0, 0.36)
        assert D.dtype == np.float64

    def test_D_is_symmetric(self):
        D = build_constitutive_matrix(3000.0, 0.36)
        np.testing.assert_allclose(D, D.T, atol=1e-12)

    def test_D_is_positive_definite(self):
        D = build_constitutive_matrix(3000.0, 0.36)
        eigenvalues = np.linalg.eigvalsh(D)
        assert np.all(eigenvalues > 0), f"Non-positive eigenvalues: {eigenvalues}"

    # --- Analytic entries (Lamé parameters) ---

    def test_diagonal_normal_equals_lambda_plus_2mu(self):
        E, nu = 3000.0, 0.36
        lam = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
        mu = E / (2.0 * (1.0 + nu))
        D = build_constitutive_matrix(E, nu)
        expected_diag = lam + 2.0 * mu
        assert D[0, 0] == pytest.approx(expected_diag)
        assert D[1, 1] == pytest.approx(expected_diag)
        assert D[2, 2] == pytest.approx(expected_diag)

    def test_shear_diagonal_equals_mu(self):
        E, nu = 3000.0, 0.36
        mu = E / (2.0 * (1.0 + nu))
        D = build_constitutive_matrix(E, nu)
        assert D[3, 3] == pytest.approx(mu)
        assert D[4, 4] == pytest.approx(mu)
        assert D[5, 5] == pytest.approx(mu)

    def test_off_diagonal_normal_equals_lambda(self):
        E, nu = 3000.0, 0.36
        lam = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
        D = build_constitutive_matrix(E, nu)
        for i, j in [(0, 1), (0, 2), (1, 2)]:
            assert D[i, j] == pytest.approx(lam)
            assert D[j, i] == pytest.approx(lam)

    def test_shear_off_diagonal_are_zero(self):
        D = build_constitutive_matrix(3000.0, 0.36)
        # No coupling between normal and shear rows/columns
        for i in range(3):
            for j in range(3, 6):
                assert D[i, j] == pytest.approx(0.0)
                assert D[j, i] == pytest.approx(0.0)

    # --- Edge cases ---

    def test_nu_zero_lambda_is_zero(self):
        """With nu=0, lam=0; D becomes 2*mu on normal diagonal, mu on shear."""
        E = 1000.0
        D = build_constitutive_matrix(E, 0.0)
        mu = E / 2.0  # nu=0 → mu = E/2
        assert D[0, 0] == pytest.approx(2.0 * mu)
        assert D[0, 1] == pytest.approx(0.0)  # lam = 0
        assert D[3, 3] == pytest.approx(mu)

    def test_nu_zero_positive_definite(self):
        D = build_constitutive_matrix(1000.0, 0.0)
        assert np.all(np.linalg.eigvalsh(D) > 0)

    def test_nu_0p3_positive_definite(self):
        D = build_constitutive_matrix(2100.0, 0.30)
        assert np.all(np.linalg.eigvalsh(D) > 0)

    def test_nu_0p49_warns_and_positive_definite(self):
        """Near-incompressible nu=0.49 should warn but remain PD."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            D = build_constitutive_matrix(1000.0, 0.49)
        assert len(w) == 1
        assert "locking" in str(w[0].message).lower()
        assert np.all(np.linalg.eigvalsh(D) > 0)

    def test_negative_nu_raises(self):
        with pytest.raises(ValueError, match="admissible"):
            build_constitutive_matrix(1000.0, -1.1)

    def test_nu_gte_0p5_raises(self):
        with pytest.raises(ValueError, match="admissible"):
            build_constitutive_matrix(1000.0, 0.5)

    def test_nu_exactly_minus1_raises(self):
        with pytest.raises(ValueError):
            build_constitutive_matrix(1000.0, -1.0)


class TestEffectiveProperties:
    """Tests for effective_properties(E_bulk, nu, density_fraction, pattern)."""

    # --- Power-law scaling ---

    def test_density_one_returns_E_bulk(self):
        E_eff, _ = effective_properties(3000.0, 0.36, 1.0, "lines")
        assert E_eff == pytest.approx(3000.0)

    def test_density_zero_returns_zero(self):
        E_eff, _ = effective_properties(3000.0, 0.36, 0.0, "lines")
        assert E_eff == pytest.approx(0.0)

    def test_density_half_lines_exponent_one(self):
        """'lines' has exponent=1, so E_eff = E_bulk * 0.5."""
        E_eff, _ = effective_properties(3000.0, 0.36, 0.5, "lines")
        assert E_eff == pytest.approx(1500.0)

    @pytest.mark.parametrize("pattern,n", _PATTERN_EXPONENTS.items())
    def test_each_pattern_obeys_power_law(self, pattern, n):
        E_bulk = 2000.0
        rho = 0.6
        E_eff, _ = effective_properties(E_bulk, 0.35, rho, pattern)
        assert E_eff == pytest.approx(E_bulk * (rho ** n))

    def test_unknown_pattern_uses_default_exponent(self):
        E_bulk = 2000.0
        rho = 0.7
        E_eff, _ = effective_properties(E_bulk, 0.35, rho, "nonexistent_pattern")
        assert E_eff == pytest.approx(E_bulk * (rho ** _DEFAULT_EXPONENT))

    def test_pattern_lookup_is_case_insensitive(self):
        e1, _ = effective_properties(3000.0, 0.36, 0.5, "Gyroid")
        e2, _ = effective_properties(3000.0, 0.36, 0.5, "gyroid")
        assert e1 == pytest.approx(e2)

    # --- nu is passed through unchanged ---

    def test_nu_eff_equals_input_nu(self):
        _, nu_eff = effective_properties(3000.0, 0.36, 0.5, "grid")
        assert nu_eff == pytest.approx(0.36)

    # --- Clamping ---

    def test_density_above_one_is_clamped_to_one(self):
        E_eff_1, _ = effective_properties(3000.0, 0.36, 1.0, "lines")
        E_eff_high, _ = effective_properties(3000.0, 0.36, 1.5, "lines")
        assert E_eff_high == pytest.approx(E_eff_1)

    def test_density_below_zero_is_clamped_to_zero(self):
        E_eff, _ = effective_properties(3000.0, 0.36, -0.5, "lines")
        assert E_eff == pytest.approx(0.0)

    def test_E_eff_is_positive_for_mid_density(self):
        E_eff, _ = effective_properties(3000.0, 0.36, 0.5, "honeycomb")
        assert E_eff > 0.0


# ===========================================================================
# 2. material_database.py
# ===========================================================================


class TestMaterialDatabase:
    """Tests for MaterialDatabase.get_material and database content."""

    def test_all_known_materials_load(self):
        for name in MaterialDatabase.available_materials():
            mat = MaterialDatabase.get_material(name)
            assert isinstance(mat, Material)
            assert mat.name == name

    def test_pla_properties(self):
        mat = MaterialDatabase.get_material("PLA")
        assert mat.E_xy == pytest.approx(3000.0)
        assert mat.E_z == pytest.approx(1500.0)
        assert mat.nu == pytest.approx(0.36)
        assert mat.yield_strength == pytest.approx(50.0)
        assert mat.density == pytest.approx(1.24)

    def test_all_E_xy_positive(self):
        for name in MaterialDatabase.available_materials():
            mat = MaterialDatabase.get_material(name)
            assert mat.E_xy > 0, f"{name}: E_xy={mat.E_xy} not positive"

    def test_all_E_z_positive(self):
        for name in MaterialDatabase.available_materials():
            mat = MaterialDatabase.get_material(name)
            assert mat.E_z > 0, f"{name}: E_z={mat.E_z} not positive"

    def test_all_nu_in_valid_range(self):
        for name in MaterialDatabase.available_materials():
            mat = MaterialDatabase.get_material(name)
            assert -1.0 < mat.nu < 0.5, f"{name}: nu={mat.nu} out of range"

    def test_all_yield_strength_positive(self):
        for name in MaterialDatabase.available_materials():
            mat = MaterialDatabase.get_material(name)
            assert mat.yield_strength > 0, f"{name}: yield_strength={mat.yield_strength}"

    def test_all_density_positive(self):
        for name in MaterialDatabase.available_materials():
            mat = MaterialDatabase.get_material(name)
            assert mat.density > 0, f"{name}: density={mat.density}"

    def test_case_insensitive_lookup_lowercase(self):
        mat_upper = MaterialDatabase.get_material("PLA")
        mat_lower = MaterialDatabase.get_material("pla")
        assert mat_upper == mat_lower

    def test_case_insensitive_lookup_mixed(self):
        mat_canon = MaterialDatabase.get_material("CF_Nylon")
        mat_mixed = MaterialDatabase.get_material("cf_nylon")
        assert mat_canon == mat_mixed

    def test_unknown_material_falls_back_to_pla(self):
        pla = MaterialDatabase.get_material("PLA")
        unknown = MaterialDatabase.get_material("UnknownPolimer99")
        assert unknown == pla

    def test_available_materials_sorted(self):
        names = MaterialDatabase.available_materials()
        assert names == sorted(names)

    def test_material_is_immutable_dataclass(self):
        mat = MaterialDatabase.get_material("ABS")
        with pytest.raises((AttributeError, TypeError)):
            mat.E_xy = 9999.0  # frozen=True should prevent this


# ===========================================================================
# 3. fea_solver.py — helper functions
# ===========================================================================


class TestStrainDisplacementMatrix:
    """Tests for _strain_displacement_matrix(x0, x1, x2, x3)."""

    def test_shape_is_6x12(self, unit_tet_nodes):
        x0, x1, x2, x3 = unit_tet_nodes
        B, _ = _strain_displacement_matrix(x0, x1, x2, x3)
        assert B.shape == (6, 12)

    def test_volume_positive_for_valid_tet(self, unit_tet_nodes):
        x0, x1, x2, x3 = unit_tet_nodes
        _, V = _strain_displacement_matrix(x0, x1, x2, x3)
        assert V > 0.0

    def test_volume_exact_for_unit_tet(self, unit_tet_nodes):
        """Right-angle unit tet has V = 1/6."""
        x0, x1, x2, x3 = unit_tet_nodes
        _, V = _strain_displacement_matrix(x0, x1, x2, x3)
        assert V == pytest.approx(1.0 / 6.0)

    def test_degenerate_tet_returns_zero_volume(self):
        """All four nodes in a plane → det J = 0 → V = 0."""
        x0 = np.array([0.0, 0.0, 0.0])
        x1 = np.array([1.0, 0.0, 0.0])
        x2 = np.array([0.0, 1.0, 0.0])
        x3 = np.array([0.5, 0.5, 0.0])  # coplanar
        B, V = _strain_displacement_matrix(x0, x1, x2, x3)
        assert V == pytest.approx(0.0, abs=1e-12)
        np.testing.assert_allclose(B, np.zeros((6, 12)))

    def test_scaled_tet_has_correct_volume(self):
        """Uniform scaling by s multiplies volume by s^3."""
        s = 2.5
        x0 = np.array([0.0, 0.0, 0.0])
        x1 = np.array([s, 0.0, 0.0])
        x2 = np.array([0.0, s, 0.0])
        x3 = np.array([0.0, 0.0, s])
        _, V = _strain_displacement_matrix(x0, x1, x2, x3)
        assert V == pytest.approx((s ** 3) / 6.0)

    def test_B_dtype_float64(self, unit_tet_nodes):
        x0, x1, x2, x3 = unit_tet_nodes
        B, _ = _strain_displacement_matrix(x0, x1, x2, x3)
        assert B.dtype == np.float64

    def test_rigid_body_translation_gives_zero_strain(self, unit_tet_nodes):
        """Uniform translation of all nodes gives zero strain (partition of unity)."""
        x0, x1, x2, x3 = unit_tet_nodes
        B, _ = _strain_displacement_matrix(x0, x1, x2, x3)
        # u = [1,0,0] for all 4 nodes → u_e is [1,0,0, 1,0,0, 1,0,0, 1,0,0]
        u_e = np.tile([1.0, 0.0, 0.0], 4)
        strain = B @ u_e
        np.testing.assert_allclose(strain, 0.0, atol=1e-12)

    def test_B_columns_obey_nodal_dof_layout(self, unit_tet_nodes):
        """Column layout: DOFs [ux0,uy0,uz0, ux1,...,uz3] → 4 nodes × 3 DOFs."""
        x0, x1, x2, x3 = unit_tet_nodes
        B, _ = _strain_displacement_matrix(x0, x1, x2, x3)
        # ε_xx = ∂u/∂x → only cols 0,3,6,9 (ux DOFs) are non-zero in row 0
        nonzero_cols_row0 = np.nonzero(B[0, :])[0]
        # All active columns should be in {0,3,6,9}
        assert set(nonzero_cols_row0).issubset({0, 3, 6, 9})


class TestVonMises:
    """Tests for _von_mises(stress)."""

    def test_zero_stress_gives_zero_vm(self):
        assert _von_mises(np.zeros(6)) == pytest.approx(0.0)

    def test_uniaxial_tension(self):
        """σ_xx = S, all others zero → σ_vm = S."""
        S = 100.0
        stress = np.array([S, 0.0, 0.0, 0.0, 0.0, 0.0])
        assert _von_mises(stress) == pytest.approx(S)

    def test_equibiaxial_stress(self):
        """σ_xx = σ_yy = S, others zero → σ_vm = S (equal biaxial cancels)."""
        S = 100.0
        stress = np.array([S, S, 0.0, 0.0, 0.0, 0.0])
        assert _von_mises(stress) == pytest.approx(S)

    def test_hydrostatic_stress_gives_zero_vm(self):
        """σ_xx = σ_yy = σ_zz = p, shear = 0 → σ_vm = 0 (no deviatoric part)."""
        p = 50.0
        stress = np.array([p, p, p, 0.0, 0.0, 0.0])
        assert _von_mises(stress) == pytest.approx(0.0, abs=1e-10)

    def test_pure_shear(self):
        """τ_xy = T → σ_vm = sqrt(3) * T."""
        T = 30.0
        stress = np.array([0.0, 0.0, 0.0, T, 0.0, 0.0])
        assert _von_mises(stress) == pytest.approx(np.sqrt(3.0) * T)

    def test_result_is_non_negative(self):
        """σ_vm must never be negative."""
        rng = np.random.default_rng(seed=42)
        for _ in range(50):
            stress = rng.standard_normal(6) * 100.0
            assert _von_mises(stress) >= 0.0

    def test_result_is_scalar(self):
        stress = np.array([10.0, 5.0, -3.0, 2.0, 1.0, 4.0])
        result = _von_mises(stress)
        assert isinstance(result, float)


# ===========================================================================
# 3. fea_solver.py — LinearElasticitySolver
# ===========================================================================


class TestLinearElasticitySolver:
    """Tests for the LinearElasticitySolver class."""

    @pytest.fixture
    def solver(self):
        return LinearElasticitySolver()

    @pytest.fixture
    def E_nu_for_two_tet(self, two_tet_mesh):
        n = two_tet_mesh.elements.shape[0]
        return np.full(n, 3000.0), np.full(n, 0.36)

    # --- Stiffness assembly ---

    def test_assembled_K_shape(self, solver, two_tet_mesh, E_nu_for_two_tet):
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        assert K.shape == (n_dof, n_dof)

    def test_assembled_K_is_symmetric(self, solver, two_tet_mesh, E_nu_for_two_tet):
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        diff = (K - K.T).toarray()
        np.testing.assert_allclose(diff, 0.0, atol=1e-10)

    def test_assembled_K_is_csr(self, solver, two_tet_mesh, E_nu_for_two_tet):
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        assert sp.issparse(K)
        assert K.format == "csr"

    def test_assembled_K_positive_semidefinite(self, solver, two_tet_mesh, E_nu_for_two_tet):
        """Before BCs, K is singular (rigid-body modes) but PSD — eigenvalues >= 0."""
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        K_dense = K.toarray()
        eigvals = np.linalg.eigvalsh(K_dense)
        assert np.all(eigvals >= -1e-8), f"Negative eigenvalue: {eigvals.min()}"

    def test_empty_mesh_returns_zero_matrix(self, solver):
        nodes = np.zeros((4, 3), dtype=np.float64)
        elements = np.zeros((0, 4), dtype=np.int64)
        mesh = TetMesh(nodes=nodes, elements=elements, surface_node_map={})
        E_arr = np.array([], dtype=np.float64)
        nu_arr = np.array([], dtype=np.float64)
        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        assert K.shape == (12, 12)
        assert K.nnz == 0

    # --- Boundary conditions ---

    def test_bc_zeroes_constrained_dofs_in_f(self, solver, two_tet_mesh, E_nu_for_two_tet):
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        f = np.ones(n_dof, dtype=np.float64)
        fixed_nodes = np.array([0, 1], dtype=np.int64)
        _, f_bc = solver.apply_boundary_conditions(K, f, fixed_nodes)
        for node in fixed_nodes:
            for d in range(3):
                assert f_bc[node * 3 + d] == pytest.approx(0.0)

    def test_bc_diagonal_is_one_for_constrained_dofs(self, solver, two_tet_mesh, E_nu_for_two_tet):
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        f = np.ones(n_dof)
        fixed_nodes = np.array([0], dtype=np.int64)
        K_bc, _ = solver.apply_boundary_conditions(K, f, fixed_nodes)
        K_dense = K_bc.toarray()
        for d in range(3):
            dof = 0 * 3 + d
            assert K_dense[dof, dof] == pytest.approx(1.0)

    def test_bc_off_diagonal_is_zero_for_constrained_dofs(
        self, solver, two_tet_mesh, E_nu_for_two_tet
    ):
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        f = np.ones(n_dof)
        fixed_nodes = np.array([0], dtype=np.int64)
        K_bc, _ = solver.apply_boundary_conditions(K, f, fixed_nodes)
        K_dense = K_bc.toarray()
        for d in range(3):
            dof = 0 * 3 + d
            np.testing.assert_allclose(K_dense[dof, :dof], 0.0, atol=1e-12)
            np.testing.assert_allclose(K_dense[dof, dof + 1 :], 0.0, atol=1e-12)
            np.testing.assert_allclose(K_dense[:dof, dof], 0.0, atol=1e-12)
            np.testing.assert_allclose(K_dense[dof + 1 :, dof], 0.0, atol=1e-12)

    # --- Solve: singular system raises RuntimeError ---

    def test_solve_singular_raises_runtime_error(self, solver):
        """A structurally zero stiffness matrix is exactly singular.

        spsolve raises MatrixRankWarning and returns NaN; our wrapper must
        convert that into a RuntimeError with a useful message.
        """
        n_dof = 6
        K_zero = sp.csr_matrix((n_dof, n_dof), dtype=np.float64)
        f = np.ones(n_dof, dtype=np.float64)
        with pytest.raises(RuntimeError, match="non-finite"):
            solver.solve(K_zero, f)

    # --- Solve: fixed-tet analytical check ---

    def test_solve_fixed_tet_constrained_dofs_are_zero(
        self, solver, unit_tet_nodes
    ):
        """Fix node 0; solve for any loading; constrained DOFs must stay zero."""
        x0, x1, x2, x3 = unit_tet_nodes
        nodes = np.stack([x0, x1, x2, x3])
        elements = np.array([[0, 1, 2, 3]], dtype=np.int64)
        mesh = TetMesh(nodes=nodes, elements=elements, surface_node_map={})
        E_arr = np.array([3000.0])
        nu_arr = np.array([0.36])
        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        n_dof = 4 * 3
        f = np.zeros(n_dof)
        # Fix all nodes except node 3; apply force on node 3 in z
        fixed_nodes = np.array([0, 1, 2], dtype=np.int64)
        f[3 * 3 + 2] = 1000.0
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed_nodes)
        u = solver.solve(K_bc, f_bc)
        for node in fixed_nodes:
            for d in range(3):
                assert u[node * 3 + d] == pytest.approx(0.0, abs=1e-10)

    def test_solve_displacement_direction_matches_force(
        self, solver, unit_tet_nodes
    ):
        """Force in +z on free node should produce positive z-displacement."""
        x0, x1, x2, x3 = unit_tet_nodes
        nodes = np.stack([x0, x1, x2, x3])
        elements = np.array([[0, 1, 2, 3]], dtype=np.int64)
        mesh = TetMesh(nodes=nodes, elements=elements, surface_node_map={})
        E_arr = np.array([3000.0])
        nu_arr = np.array([0.36])
        K = solver.assemble_stiffness_matrix(mesh, E_arr, nu_arr)
        n_dof = 4 * 3
        f = np.zeros(n_dof)
        fixed_nodes = np.array([0, 1, 2], dtype=np.int64)
        f[3 * 3 + 2] = 1000.0  # +z force on node 3
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed_nodes)
        u = solver.solve(K_bc, f_bc)
        # Node 3 z-displacement must be positive
        assert u[3 * 3 + 2] > 0.0

    # --- Von Mises stress computation ---

    def test_compute_stress_shape(self, solver, two_tet_mesh, E_nu_for_two_tet):
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        fixed_nodes = np.array([0, 1, 2], dtype=np.int64)
        f[4 * 3 + 2] = 500.0
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed_nodes)
        u = solver.solve(K_bc, f_bc)
        vm = solver.compute_element_stress(two_tet_mesh, u, E_arr, nu_arr)
        assert vm.shape == (two_tet_mesh.elements.shape[0],)

    def test_compute_stress_non_negative(self, solver, two_tet_mesh, E_nu_for_two_tet):
        E_arr, nu_arr = E_nu_for_two_tet
        K = solver.assemble_stiffness_matrix(two_tet_mesh, E_arr, nu_arr)
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        f = np.zeros(n_dof)
        fixed_nodes = np.array([0, 1, 2], dtype=np.int64)
        f[4 * 3 + 2] = 500.0
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, fixed_nodes)
        u = solver.solve(K_bc, f_bc)
        vm = solver.compute_element_stress(two_tet_mesh, u, E_arr, nu_arr)
        assert np.all(vm >= 0.0)

    def test_compute_stress_zero_displacement_gives_zero(
        self, solver, two_tet_mesh, E_nu_for_two_tet
    ):
        E_arr, nu_arr = E_nu_for_two_tet
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        u = np.zeros(n_dof)
        vm = solver.compute_element_stress(two_tet_mesh, u, E_arr, nu_arr)
        np.testing.assert_allclose(vm, 0.0, atol=1e-12)


# ===========================================================================
# 4. stress_to_density.py
# ===========================================================================


class TestStressToDensity:
    """Tests for stress_to_density(von_mises, sigma_yield, rho_min, rho_max, method)."""

    RHO_MIN = 0.10
    RHO_MAX = 0.80

    # --- Linear method ---

    def test_linear_zero_stress_gives_rho_min(self):
        vm = np.array([0.0])
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="linear", safety_factor=1.0)
        assert result[0] == pytest.approx(self.RHO_MIN)

    def test_linear_yield_stress_gives_rho_max(self):
        vm = np.array([50.0])
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="linear", safety_factor=1.0)
        assert result[0] == pytest.approx(self.RHO_MAX)

    def test_linear_half_yield_stress_gives_midpoint(self):
        vm = np.array([25.0])
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="linear", safety_factor=1.0)
        expected = self.RHO_MIN + (self.RHO_MAX - self.RHO_MIN) * 0.5
        assert result[0] == pytest.approx(expected)

    def test_linear_endpoints_exact(self):
        vm = np.array([0.0, 50.0])
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="linear", safety_factor=1.0)
        assert result[0] == pytest.approx(self.RHO_MIN)
        assert result[1] == pytest.approx(self.RHO_MAX)

    def test_linear_vectorized_array(self):
        """Verify result shape matches input shape for array input."""
        vm = np.linspace(0.0, 50.0, 20)
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="linear", safety_factor=1.0)
        assert result.shape == (20,)
        assert np.all(result >= self.RHO_MIN)
        assert np.all(result <= self.RHO_MAX)

    # --- Power (sqrt) method ---

    def test_power_zero_stress_gives_rho_min(self):
        vm = np.array([0.0])
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="power", safety_factor=1.0)
        assert result[0] == pytest.approx(self.RHO_MIN)

    def test_power_yield_stress_gives_rho_max(self):
        vm = np.array([50.0])
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="power", safety_factor=1.0)
        assert result[0] == pytest.approx(self.RHO_MAX)

    def test_power_half_yield_uses_sqrt(self):
        """Half yield stress → s=0.5 → density = rho_min + (rho_max-rho_min)*sqrt(0.5)."""
        vm = np.array([25.0])
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="power", safety_factor=1.0)
        expected = self.RHO_MIN + (self.RHO_MAX - self.RHO_MIN) * np.sqrt(0.5)
        assert result[0] == pytest.approx(expected)

    def test_power_gives_higher_density_than_linear_midpoint(self):
        """sqrt(s) > s for s in (0,1) → power method gives more material."""
        vm = np.array([25.0])
        rho_linear = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="linear", safety_factor=1.0)
        rho_power = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="power", safety_factor=1.0)
        assert rho_power[0] > rho_linear[0]

    # --- Clamping ---

    def test_stress_above_yield_clamped_to_rho_max(self):
        vm = np.array([200.0])  # >> yield
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="linear", safety_factor=1.0)
        assert result[0] == pytest.approx(self.RHO_MAX)

    def test_negative_stress_clamped_to_rho_min(self):
        vm = np.array([-10.0])
        result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="linear", safety_factor=1.0)
        assert result[0] == pytest.approx(self.RHO_MIN)

    def test_output_always_in_rho_range(self):
        """Random stress values should always produce density in [rho_min, rho_max]."""
        rng = np.random.default_rng(seed=7)
        vm = rng.uniform(-100.0, 200.0, 200)
        for method in ("linear", "power"):
            result = stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method=method)
            assert np.all(result >= self.RHO_MIN - 1e-12)
            assert np.all(result <= self.RHO_MAX + 1e-12)

    # --- Error handling ---

    def test_non_positive_sigma_yield_raises(self):
        vm = np.array([10.0])
        with pytest.raises(ValueError, match="sigma_yield"):
            stress_to_density(vm, 0.0, self.RHO_MIN, self.RHO_MAX)

    def test_negative_sigma_yield_raises(self):
        vm = np.array([10.0])
        with pytest.raises(ValueError, match="sigma_yield"):
            stress_to_density(vm, -5.0, self.RHO_MIN, self.RHO_MAX)

    def test_unknown_method_raises(self):
        vm = np.array([10.0])
        with pytest.raises(ValueError, match="Unknown method"):
            stress_to_density(vm, 50.0, self.RHO_MIN, self.RHO_MAX, method="quadratic")

    def test_rho_min_ge_rho_max_raises(self):
        vm = np.array([10.0])
        with pytest.raises(ValueError, match="rho_min"):
            stress_to_density(vm, 50.0, rho_min=0.8, rho_max=0.1)

    def test_rho_min_equals_rho_max_raises(self):
        vm = np.array([10.0])
        with pytest.raises(ValueError):
            stress_to_density(vm, 50.0, rho_min=0.5, rho_max=0.5)


# ===========================================================================
# 5. tetrahedralization.py
# ===========================================================================


@pytest.mark.skipif(
    "gmsh" not in sys.modules or sys.modules.get("gmsh") is None,
    reason="gmsh not installed",
)
class TestTetrahedralization:
    """Tests for tetrahedralize(surface_mesh, element_size).

    Each test performs a real Gmsh call; they are placed in their own class so
    the entire class can be skipped quickly when gmsh is absent.
    """

    from FEAInfillOptimizer.fea.tetrahedralization import tetrahedralize

    @pytest.fixture(scope="class")
    def cube_tet_mesh(self):
        from FEAInfillOptimizer.fea.tetrahedralization import tetrahedralize
        cube = _make_cube_trimesh(side=5.0)
        return tetrahedralize(cube, element_size=2.5)

    def test_tet_mesh_has_nodes(self, cube_tet_mesh):
        assert cube_tet_mesh.nodes.shape[0] > 0

    def test_tet_mesh_has_elements(self, cube_tet_mesh):
        assert cube_tet_mesh.elements.shape[0] > 0

    def test_elements_have_4_nodes(self, cube_tet_mesh):
        assert cube_tet_mesh.elements.shape[1] == 4

    def test_element_indices_valid(self, cube_tet_mesh):
        n_nodes = cube_tet_mesh.nodes.shape[0]
        assert np.all(cube_tet_mesh.elements >= 0)
        assert np.all(cube_tet_mesh.elements < n_nodes)

    def test_surface_node_map_non_empty(self, cube_tet_mesh):
        assert len(cube_tet_mesh.surface_node_map) > 0

    def test_surface_node_map_indices_valid(self, cube_tet_mesh):
        n_nodes = cube_tet_mesh.nodes.shape[0]
        for tet_idx in cube_tet_mesh.surface_node_map.values():
            assert 0 <= tet_idx < n_nodes

    def test_all_element_volumes_positive(self, cube_tet_mesh):
        """No element should have non-positive volume."""
        nodes = cube_tet_mesh.nodes
        for elem in cube_tet_mesh.elements:
            x0, x1, x2, x3 = nodes[elem[0]], nodes[elem[1]], nodes[elem[2]], nodes[elem[3]]
            J = np.array([x1 - x0, x2 - x0, x3 - x0])
            V = abs(np.linalg.det(J)) / 6.0
            assert V > 0.0, f"Degenerate element found: nodes {elem}, V={V}"

    def test_nodes_dtype_float64(self, cube_tet_mesh):
        assert cube_tet_mesh.nodes.dtype == np.float64

    def test_elements_dtype_int64_or_similar(self, cube_tet_mesh):
        assert np.issubdtype(cube_tet_mesh.elements.dtype, np.integer)

    def test_coarser_mesh_has_fewer_elements(self):
        """A much coarser element size must produce fewer tetrahedra than a fine one."""
        from FEAInfillOptimizer.fea.tetrahedralization import tetrahedralize
        cube = _make_cube_trimesh(side=5.0)
        # Use a 5× size difference to ensure Gmsh produces a reliably different count.
        coarse = tetrahedralize(cube, element_size=3.0)
        fine = tetrahedralize(cube, element_size=0.6)
        assert coarse.elements.shape[0] < fine.elements.shape[0], (
            f"Expected coarse ({coarse.elements.shape[0]}) < fine ({fine.elements.shape[0]})"
        )

    def test_preset_medium_produces_valid_mesh(self):
        from FEAInfillOptimizer.fea.tetrahedralization import tetrahedralize
        cube = _make_cube_trimesh(side=5.0)
        mesh = tetrahedralize(cube, element_size="medium")
        assert mesh.elements.shape[0] > 0


# ===========================================================================
# 6. Integration test — unit cube under compression
# ===========================================================================


@pytest.mark.skipif(
    "gmsh" not in sys.modules or sys.modules.get("gmsh") is None,
    reason="gmsh not installed",
)
class TestCubeCompressionIntegration:
    """End-to-end: tetrahedralize a unit cube, apply compression BCs, solve FEA.

    Physical setup
    --------------
    - Unit cube: nodes in [0, 1]^3 (mm scale for this test).
    - Bottom face (z ≈ 0): fully fixed (zero displacement in all DOFs).
    - Top face (z ≈ 1): uniform downward load summing to -100 N total.
    - Material: E = 3000 MPa, nu = 0.36 (PLA-like).
    """

    SIDE = 1.0
    ELEMENT_SIZE = 0.5
    FORCE_Z = -100.0  # total force (N)
    TOL = 1e-4        # position tolerance for identifying faces

    @pytest.fixture(scope="class")
    def compression_results(self):
        from FEAInfillOptimizer.fea.tetrahedralization import tetrahedralize

        cube = _make_cube_trimesh(side=self.SIDE)
        tet_mesh = tetrahedralize(cube, element_size=self.ELEMENT_SIZE)

        nodes = tet_mesh.nodes
        n_nodes = nodes.shape[0]
        n_elems = tet_mesh.elements.shape[0]

        # Identify bottom nodes (z ≈ 0) and top nodes (z ≈ 1)
        bottom_mask = nodes[:, 2] < self.TOL
        top_mask = nodes[:, 2] > self.SIDE - self.TOL

        bottom_nodes = np.where(bottom_mask)[0].astype(np.int64)
        top_nodes = np.where(top_mask)[0].astype(np.int64)

        assert len(bottom_nodes) > 0, "No bottom nodes found"
        assert len(top_nodes) > 0, "No top nodes found"

        # Build force vector: distribute total downward force across top nodes
        n_dof = n_nodes * 3
        f = np.zeros(n_dof, dtype=np.float64)
        fz_per_node = self.FORCE_Z / len(top_nodes)
        for tn in top_nodes:
            f[tn * 3 + 2] += fz_per_node

        # Uniform material: PLA
        E_arr = np.full(n_elems, 3000.0, dtype=np.float64)
        nu_arr = np.full(n_elems, 0.36, dtype=np.float64)

        solver = LinearElasticitySolver()
        K = solver.assemble_stiffness_matrix(tet_mesh, E_arr, nu_arr)
        K_bc, f_bc = solver.apply_boundary_conditions(K, f, bottom_nodes)
        u = solver.solve(K_bc, f_bc)
        vm = solver.compute_element_stress(tet_mesh, u, E_arr, nu_arr)

        return {
            "tet_mesh": tet_mesh,
            "u": u,
            "vm": vm,
            "top_nodes": top_nodes,
            "bottom_nodes": bottom_nodes,
        }

    def test_bottom_nodes_have_zero_displacement(self, compression_results):
        """Fixed nodes must have exactly zero displacement."""
        u = compression_results["u"]
        for node in compression_results["bottom_nodes"]:
            for d in range(3):
                assert u[node * 3 + d] == pytest.approx(0.0, abs=1e-8)

    def test_top_nodes_displace_downward_in_z(self, compression_results):
        """Under downward force the top face must move in −z direction."""
        u = compression_results["u"]
        top_nodes = compression_results["top_nodes"]
        uz_top = [u[tn * 3 + 2] for tn in top_nodes]
        assert all(uz < 0.0 for uz in uz_top), (
            f"Some top nodes did not displace downward: {uz_top}"
        )

    def test_top_mean_z_displacement_negative(self, compression_results):
        u = compression_results["u"]
        top_nodes = compression_results["top_nodes"]
        uz_mean = np.mean([u[tn * 3 + 2] for tn in top_nodes])
        assert uz_mean < 0.0

    def test_von_mises_all_non_negative(self, compression_results):
        vm = compression_results["vm"]
        assert np.all(vm >= 0.0)

    def test_von_mises_positive_under_load(self, compression_results):
        """With non-zero applied force the body must be in a non-trivial stress state."""
        vm = compression_results["vm"]
        assert np.max(vm) > 0.0

    def test_displacements_finite(self, compression_results):
        u = compression_results["u"]
        assert np.all(np.isfinite(u))

    def test_stress_finite(self, compression_results):
        vm = compression_results["vm"]
        assert np.all(np.isfinite(vm))

    def test_stress_roughly_uniform(self, compression_results):
        """Under uniform axial compression, stress should not vary wildly.

        Coefficient of variation of von Mises stress should be below 2 (i.e.
        std < 2 * mean), indicating the solver is not producing wildly erratic
        element stresses.
        """
        vm = compression_results["vm"]
        nonzero = vm[vm > 1e-6]
        if len(nonzero) == 0:
            pytest.skip("All stresses too small to measure variation")
        cv = np.std(nonzero) / np.mean(nonzero)
        assert cv < 2.0, f"Stress too non-uniform: CV={cv:.2f}"
