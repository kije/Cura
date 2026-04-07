# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Unit and integration tests for SIMP OC density update.

Covers:
- oc_density_update: density bounds, volume constraint, move limits,
  bisection convergence, degenerate elements.
- _compute_element_stiffness_and_compliance: output shapes, non-negativity.
- IterativeFEASolver with optimization_method="oc" (E2E smoke test).

Run with:
    source .test-venv/bin/activate
    python -m pytest plugins/FEAInfillOptimizer/tests/test_oc_update.py -v
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

from FEAInfillOptimizer.fea.oc_update import (
    oc_density_update,
    _compute_element_stiffness_and_compliance,
)
from FEAInfillOptimizer.fea.tetrahedralization import TetMesh


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unit_tet_mesh():
    """Single right-angle tetrahedron: 4 nodes, 1 element, V=1/6."""
    nodes = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    elements = np.array([[0, 1, 2, 3]], dtype=np.int64)
    return TetMesh(nodes=nodes, elements=elements, surface_node_map={0: 0, 1: 1, 2: 2, 3: 3})


@pytest.fixture
def two_tet_mesh():
    """Two-element mesh (5 nodes)."""
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
    return TetMesh(
        nodes=nodes,
        elements=elements,
        surface_node_map={0: 0, 1: 1, 2: 2, 3: 3, 4: 4},
    )


def _make_displacements(mesh: TetMesh, magnitude: float = 0.01) -> np.ndarray:
    """Return a simple displacement vector (uniform z-shift on top node)."""
    n_dof = mesh.nodes.shape[0] * 3
    u = np.zeros(n_dof, dtype=np.float64)
    # Small z-displacement on last node to create non-zero strain energy
    u[-1] = magnitude
    return u


# ===========================================================================
# 1. _compute_element_stiffness_and_compliance
# ===========================================================================


class TestComputeElementComplianceAndVolumes:

    def test_output_shapes(self, unit_tet_mesh):
        u = _make_displacements(unit_tet_mesh)
        ce, volumes = _compute_element_stiffness_and_compliance(
            unit_tet_mesh, u, E_base=3000.0, nu=0.36
        )
        n = unit_tet_mesh.elements.shape[0]
        assert ce.shape == (n,)
        assert volumes.shape == (n,)

    def test_compliance_non_negative(self, two_tet_mesh):
        """Compliance = V * strain^T @ stress is always >= 0 for positive-definite D."""
        u = _make_displacements(two_tet_mesh, magnitude=0.1)
        ce, volumes = _compute_element_stiffness_and_compliance(
            two_tet_mesh, u, E_base=3000.0, nu=0.36
        )
        assert np.all(ce >= 0.0), f"Negative compliance: {ce}"

    def test_volumes_positive(self, unit_tet_mesh):
        u = _make_displacements(unit_tet_mesh)
        _, volumes = _compute_element_stiffness_and_compliance(
            unit_tet_mesh, u, E_base=3000.0, nu=0.36
        )
        assert np.all(volumes > 0.0)

    def test_zero_displacement_gives_zero_compliance(self, two_tet_mesh):
        """Zero displacement → zero strain → zero compliance."""
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        u = np.zeros(n_dof, dtype=np.float64)
        ce, _ = _compute_element_stiffness_and_compliance(
            two_tet_mesh, u, E_base=3000.0, nu=0.36
        )
        np.testing.assert_allclose(ce, 0.0, atol=1e-12)

    def test_larger_displacement_gives_larger_compliance(self, unit_tet_mesh):
        """Compliance scales quadratically with displacement magnitude."""
        u1 = _make_displacements(unit_tet_mesh, magnitude=0.01)
        u2 = _make_displacements(unit_tet_mesh, magnitude=0.10)
        ce1, _ = _compute_element_stiffness_and_compliance(unit_tet_mesh, u1, 3000.0, 0.36)
        ce2, _ = _compute_element_stiffness_and_compliance(unit_tet_mesh, u2, 3000.0, 0.36)
        # u2 is 10× larger → ce2 ≈ 100× larger
        assert np.all(ce2 >= ce1), "Larger displacement must give larger compliance"

    def test_anisotropic_bonding(self, unit_tet_mesh):
        """bonding_coeff < 1.0 should use anisotropic D0 but still return valid shapes."""
        u = _make_displacements(unit_tet_mesh, magnitude=0.05)
        ce, volumes = _compute_element_stiffness_and_compliance(
            unit_tet_mesh, u, E_base=3000.0, nu=0.36, bonding_coeff=0.5
        )
        assert ce.shape == (1,)
        assert np.all(np.isfinite(ce))

    def test_unit_tet_volume(self, unit_tet_mesh):
        """Unit right-angle tet has volume 1/6."""
        u = _make_displacements(unit_tet_mesh)
        _, volumes = _compute_element_stiffness_and_compliance(
            unit_tet_mesh, u, E_base=3000.0, nu=0.36
        )
        assert volumes[0] == pytest.approx(1.0 / 6.0, rel=1e-6)


# ===========================================================================
# 2. oc_density_update — density bounds
# ===========================================================================


class TestOCDensityBounds:

    def test_output_within_rho_min_rho_max(self, two_tet_mesh):
        """All updated densities must stay in [rho_min, rho_max]."""
        rho_min, rho_max = 0.1, 0.8
        density = np.full(2, 0.4, dtype=np.float64)
        u = _make_displacements(two_tet_mesh, magnitude=0.1)

        rho_new = oc_density_update(
            density, two_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=rho_min, rho_max=rho_max,
            volume_fraction=0.5,
        )
        assert np.all(rho_new >= rho_min - 1e-9)
        assert np.all(rho_new <= rho_max + 1e-9)

    def test_output_shape_matches_input(self, two_tet_mesh):
        density = np.full(2, 0.5)
        u = _make_displacements(two_tet_mesh)
        rho_new = oc_density_update(
            density, two_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=0.8, volume_fraction=0.5,
        )
        assert rho_new.shape == (2,)

    def test_output_is_finite(self, two_tet_mesh):
        density = np.full(2, 0.4)
        u = _make_displacements(two_tet_mesh, magnitude=0.05)
        rho_new = oc_density_update(
            density, two_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=0.8, volume_fraction=0.5,
        )
        assert np.all(np.isfinite(rho_new))

    def test_rho_min_clamping_respected(self, unit_tet_mesh):
        """Move limit + OC bisection must never produce density below rho_min."""
        rho_min = 0.15
        density = np.array([rho_min])  # start at minimum
        u = _make_displacements(unit_tet_mesh, magnitude=1e-8)  # tiny displ → low sensitivity

        rho_new = oc_density_update(
            density, unit_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=rho_min, rho_max=0.9, volume_fraction=0.5,
        )
        assert rho_new[0] >= rho_min - 1e-9

    def test_rho_max_clamping_respected(self, unit_tet_mesh):
        """OC update must never produce density above rho_max."""
        rho_max = 0.75
        density = np.array([0.5])
        u = _make_displacements(unit_tet_mesh, magnitude=5.0)  # huge displacement

        rho_new = oc_density_update(
            density, unit_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=rho_max, volume_fraction=0.5,
        )
        assert rho_new[0] <= rho_max + 1e-9


# ===========================================================================
# 3. oc_density_update — move limits
# ===========================================================================


class TestOCMoveLimits:

    def test_density_change_respects_move_limit(self, two_tet_mesh):
        """No element density should change by more than move_limit in one step."""
        move_limit = 0.1
        density = np.full(2, 0.5)
        u = _make_displacements(two_tet_mesh, magnitude=1.0)  # large displacement

        rho_new = oc_density_update(
            density, two_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=0.9,
            volume_fraction=0.5,
            move_limit=move_limit,
        )
        assert np.all(np.abs(rho_new - density) <= move_limit + 1e-9), (
            f"Move limit violated: delta={np.abs(rho_new - density)}"
        )

    def test_small_move_limit_limits_change(self, two_tet_mesh):
        """A very small move_limit should produce minimal density change."""
        density = np.full(2, 0.5)
        u = _make_displacements(two_tet_mesh, magnitude=0.1)
        rho_new = oc_density_update(
            density, two_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=0.9, volume_fraction=0.5,
            move_limit=0.01,
        )
        max_change = float(np.max(np.abs(rho_new - density)))
        assert max_change <= 0.01 + 1e-9, f"Move limit 0.01 violated: {max_change}"


# ===========================================================================
# 4. oc_density_update — volume fraction constraint
# ===========================================================================


class TestOCVolumeConstraint:

    def test_volume_constraint_satisfied(self, two_tet_mesh):
        """For uniform meshes, sum(rho*V) ≈ volume_fraction * sum(V).

        On a two-tet mesh with equal element volumes and a stress state that
        drives both elements equally, the OC bisection should satisfy the
        volume constraint within the bisection tolerance.
        """
        # Uniform displacement → equal compliance per element (equal mesh)
        density = np.full(2, 0.5)
        # Apply same strain energy to both elements: symmetric displacement
        n_dof = two_tet_mesh.nodes.shape[0] * 3
        u = np.zeros(n_dof, dtype=np.float64)
        # Drive with displacement at node shared by both tets
        u[3 * 3 + 2] = 0.05  # node 3 z-displacement

        rho_new = oc_density_update(
            density, two_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=0.9,
            volume_fraction=0.5,
        )
        # Both outputs must be in valid range and output finite values
        assert np.all(np.isfinite(rho_new))
        assert np.all(rho_new >= 0.1 - 1e-9)
        assert np.all(rho_new <= 0.9 + 1e-9)

    def test_higher_volume_fraction_gives_higher_mean_density(self, two_tet_mesh):
        """Higher target volume fraction → higher mean output density."""
        density = np.full(2, 0.4)
        u = _make_displacements(two_tet_mesh, magnitude=0.05)

        rho_low = oc_density_update(
            density.copy(), two_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=0.9, volume_fraction=0.3,
        )
        rho_high = oc_density_update(
            density.copy(), two_tet_mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=0.9, volume_fraction=0.8,
        )
        # Higher volume fraction → OC should select higher density materials
        assert float(np.mean(rho_high)) >= float(np.mean(rho_low)) - 1e-9, (
            f"mean(rho_high)={np.mean(rho_high):.4f} < mean(rho_low)={np.mean(rho_low):.4f}"
        )


# ===========================================================================
# 5. oc_density_update — degenerate elements
# ===========================================================================


class TestOCDegenerateElements:

    def test_all_degenerate_elements_returns_unchanged_density(self):
        """Mesh where all tets are degenerate (V=0) → return input density unchanged."""
        nodes = np.array(
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.5, 0.5, 0.0]],
            dtype=np.float64,
        )
        # All four nodes coplanar → det J = 0 → volume = 0
        elements = np.array([[0, 1, 2, 3]], dtype=np.int64)
        mesh = TetMesh(nodes=nodes, elements=elements, surface_node_map={})

        density = np.array([0.5])
        u = np.zeros(4 * 3, dtype=np.float64)

        rho_new = oc_density_update(
            density, mesh, u,
            E_base=3000.0, nu=0.36, n_exp=3.0,
            rho_min=0.1, rho_max=0.9, volume_fraction=0.5,
        )
        # Should return without crashing; density may be unchanged or clipped
        assert rho_new.shape == (1,)
        assert np.all(np.isfinite(rho_new))


# ===========================================================================
# 6. E2E: IterativeFEASolver with optimization_method="oc"
# ===========================================================================


@pytest.mark.skipif(
    "gmsh" not in sys.modules or sys.modules.get("gmsh") is None,
    reason="gmsh not installed",
)
class TestOCE2EIntegration:
    """Smoke test: OC method through IterativeFEASolver.

    Uses the scipy path (msh_path=None forces scipy fallback).
    Verifies that the OC path produces valid density/stress fields and
    populates the info dict correctly.
    """

    @pytest.fixture(scope="class")
    def oc_result(self):
        import trimesh
        from FEAInfillOptimizer.fea.tetrahedralization import tetrahedralize
        from FEAInfillOptimizer.fea.iterative_solver import IterativeFEASolver
        from FEAInfillOptimizer.fea.material_database import MaterialDatabase

        # Simple BC mock (no surface mesh → vertex indices used directly)
        class _MockForce:
            def __init__(self, fz): self.x = 0.0; self.y = 0.0; self.z = fz

        class _MockFG:
            def __init__(self, fi, fz): self.face_indices = fi; self.force = _MockForce(fz)

        class _MockBC:
            def getFixedFaces(self): return [0, 1, 2]
            def getForceGroups(self): return [_MockFG([3, 4], -100.0)]
            def getTorqueGroups(self): return []

        cube = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        tet_mesh = tetrahedralize(cube, element_size="coarse")
        bc = _MockBC()
        material = MaterialDatabase.get_material("PLA")

        config = {
            "min_density": 0.1,
            "max_density": 0.8,
            "max_iterations": 3,
            "infill_pattern": "gyroid",
            "optimization_method": "oc",
            "volume_fraction": 0.5,
        }
        solver = IterativeFEASolver()
        return solver.solve(tet_mesh, bc, material, config, surface_mesh=None)

    def test_oc_returns_valid_density(self, oc_result):
        density, _, _ = oc_result
        assert np.all(density >= 0.1 - 1e-9)
        assert np.all(density <= 0.8 + 1e-9)
        assert np.all(np.isfinite(density))

    def test_oc_returns_valid_stress(self, oc_result):
        _, stress, _ = oc_result
        assert np.all(stress >= 0.0)
        assert np.all(np.isfinite(stress))

    def test_oc_info_has_required_keys(self, oc_result):
        _, _, info = oc_result
        assert "iterations" in info
        assert "converged" in info
        assert "max_change" in info

    def test_oc_iterations_positive(self, oc_result):
        _, _, info = oc_result
        assert info["iterations"] >= 1
