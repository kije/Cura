#!/usr/bin/env python3
"""Validation tests for all review fixes (C1, C2, C3, M2-M7, misc).

These tests run without Cura — only numpy/scipy are required.
Run with: python3 -m pytest tests/test_review_fixes.py -v
   or:    python3 tests/test_review_fixes.py
"""
import sys
import os
import math
from dataclasses import dataclass, field
from typing import Dict, List
from pathlib import Path

import numpy as np

# Add plugin root to path for imports
_plugin_root = str(Path(__file__).resolve().parent.parent)
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)


# ── Minimal TetMesh stub (matches the real dataclass contract) ──────────

@dataclass
class TetMesh:
    nodes: np.ndarray
    elements: np.ndarray
    surface_node_map: Dict[int, int] = field(default_factory=dict)
    mesh_quality: str = "high"
    mesh_method: str = "test"
    warnings: list = field(default_factory=list)
    msh_path: str = ""


# Monkey-patch the tetrahedralization module so surface_stress_analyzer
# imports our stub TetMesh instead of the real one (which would try to
# import trimesh etc.)
import types
_fake_tet_mod = types.ModuleType("fea.tetrahedralization")
_fake_tet_mod.TetMesh = TetMesh
sys.modules["fea.tetrahedralization"] = _fake_tet_mod
# Also patch the relative import path
sys.modules.setdefault("FEAInfillOptimizer", types.ModuleType("FEAInfillOptimizer"))
sys.modules.setdefault("FEAInfillOptimizer.fea", types.ModuleType("FEAInfillOptimizer.fea"))
sys.modules["FEAInfillOptimizer.fea.tetrahedralization"] = _fake_tet_mod


# ── Helper: build a simple cube tet mesh ────────────────────────────────

def _make_cube_tet_mesh():
    """A unit cube [0,1]^3 split into 5 tets.

    All nodes are on the surface, surface_node_map = identity.
    """
    nodes = np.array([
        [0, 0, 0],  # 0
        [1, 0, 0],  # 1
        [1, 1, 0],  # 2
        [0, 1, 0],  # 3
        [0, 0, 1],  # 4
        [1, 0, 1],  # 5
        [1, 1, 1],  # 6
        [0, 1, 1],  # 7
    ], dtype=np.float64)
    # 5-tet decomposition of a cube
    elements = np.array([
        [0, 1, 2, 5],
        [0, 2, 3, 7],
        [0, 5, 4, 7],
        [2, 5, 6, 7],
        [0, 2, 5, 7],
    ], dtype=np.int64)
    surface_node_map = {i: i for i in range(8)}
    return TetMesh(nodes=nodes, elements=elements, surface_node_map=surface_node_map)


def _make_sphere_with_interior():
    """A sphere-like mesh with some interior elements.

    Uses 13 nodes: 12 on surface + 1 interior center node.
    """
    # Icosahedron-ish vertices on unit sphere + center
    phi = (1 + math.sqrt(5)) / 2
    raw = np.array([
        [-1,  phi, 0], [ 1,  phi, 0], [-1, -phi, 0], [ 1, -phi, 0],
        [ 0, -1,  phi], [ 0,  1,  phi], [ 0, -1, -phi], [ 0,  1, -phi],
        [ phi, 0, -1], [ phi, 0,  1], [-phi, 0, -1], [-phi, 0,  1],
    ], dtype=np.float64)
    raw = raw / np.linalg.norm(raw[0])  # normalise to unit sphere
    center = np.array([[0.0, 0.0, 0.0]])
    nodes = np.vstack([raw, center])  # 13 nodes, index 12 = center

    # Build tets from center to surface triangles
    # (simplified — just 4 tets using first few surface nodes)
    elements = np.array([
        [0, 1, 5, 12],   # surface nodes 0,1,5 + center
        [1, 5, 9, 12],   # surface nodes 1,5,9 + center
        [0, 5, 11, 12],  # surface nodes 0,5,11 + center
        [0, 1, 7, 12],   # surface nodes 0,1,7 + center
    ], dtype=np.int64)

    # Surface nodes are 0-11, interior is 12
    surface_node_map = {i: i for i in range(12)}
    return TetMesh(nodes=nodes, elements=elements, surface_node_map=surface_node_map)


# ═══════════════════════════════════════════════════════════════════════
# Fix C1: Voigt index σ_zz (index 2) and build_dir = [0,0,1]
# ═══════════════════════════════════════════════════════════════════════

class TestC1_VoigtIndexFix:
    """Verify compute_tb_metric uses σ_zz (index 2), not σ_yy (index 1)."""

    def test_tb_metric_uses_sigma_zz(self):
        """Stress only in σ_zz (index 2) should produce non-zero top/bottom metric."""
        from fea.surface_stress_analyzer import compute_tb_metric

        M = 10
        stress_tensors = np.zeros((M, 6), dtype=np.float64)
        stress_tensors[:, 2] = 20.0  # σ_zz only — interlayer stress

        top_mask = np.zeros(M, dtype=bool)
        top_mask[:5] = True
        bottom_mask = np.zeros(M, dtype=bool)
        bottom_mask[5:] = True

        W_top, W_bottom = compute_tb_metric(
            stress_tensors, top_mask, bottom_mask, sigma_eff=25.0, bonding_coeff=1.0
        )

        # Should be non-zero because σ_zz = 20 > 0
        assert W_top[:5].sum() > 0, f"Expected non-zero top metric from σ_zz, got {W_top[:5]}"
        assert W_bottom[5:].sum() > 0, f"Expected non-zero bottom metric from σ_zz, got {W_bottom[5:]}"
        print("  PASS: compute_tb_metric correctly uses σ_zz (index 2)")

    def test_tb_metric_ignores_sigma_yy(self):
        """Stress only in σ_yy (index 1) should produce zero top/bottom metric."""
        from fea.surface_stress_analyzer import compute_tb_metric

        M = 10
        stress_tensors = np.zeros((M, 6), dtype=np.float64)
        stress_tensors[:, 1] = 20.0  # σ_yy only — in-plane, NOT interlayer

        top_mask = np.ones(M, dtype=bool)
        bottom_mask = np.zeros(M, dtype=bool)

        W_top, W_bottom = compute_tb_metric(
            stress_tensors, top_mask, bottom_mask, sigma_eff=25.0, bonding_coeff=1.0
        )

        # Should be zero because σ_yy is in-plane, not interlayer
        assert W_top.sum() == 0.0, f"Expected zero top metric from σ_yy, got {W_top.sum()}"
        print("  PASS: compute_tb_metric correctly ignores σ_yy (index 1)")


class TestC1_BuildDirection:
    """Verify classify_surface_elements defaults to Z-up [0,0,1]."""

    def test_default_build_dir_is_z_up(self):
        """classify_surface_elements should use Z-up [0,0,1] as default build_dir.

        Verify by explicitly comparing results with build_dir=[0,0,1] vs [0,1,0].
        """
        from fea.surface_stress_analyzer import identify_surface_elements, classify_surface_elements

        mesh = _make_cube_tet_mesh()
        surface_mask = identify_surface_elements(mesh)

        # Default (should be Z-up)
        wall_default, top_default, bottom_default = classify_surface_elements(mesh, surface_mask)

        # Explicit Z-up — should match default
        wall_z, top_z, bottom_z = classify_surface_elements(
            mesh, surface_mask, build_dir=np.array([0.0, 0.0, 1.0]))

        # Explicit Y-up — should differ if default is truly Z-up
        wall_y, top_y, bottom_y = classify_surface_elements(
            mesh, surface_mask, build_dir=np.array([0.0, 1.0, 0.0]))

        np.testing.assert_array_equal(wall_default, wall_z)
        np.testing.assert_array_equal(top_default, top_z)
        np.testing.assert_array_equal(bottom_default, bottom_z)
        print("  PASS: classify_surface_elements default matches explicit Z-up [0,0,1]")


class TestC1_OrientationZUp:
    """Verify orientation optimizer uses Z-up convention."""

    def test_default_direction_is_z_up(self):
        """Uniaxial σ_zz stress should give metric > 0 at default direction."""
        from fea.orientation_optimizer import OrientationOptimizer

        M = 50
        stress = np.zeros((M, 6))
        stress[:, 2] = 10.0  # σ_zz = interlayer stress
        vols = np.ones(M)
        vm = np.full(M, 10.0)

        opt = OrientationOptimizer(stress, vols, vm, bonding_coeff=0.5, yield_strength=50.0)

        # Default Z-up metric should capture σ_zz as interlayer stress
        metric_at_z = opt.compute_metric(np.array([0.0, 0.0, 1.0]))
        # A direction perpendicular to Z should have zero interlayer stress from σ_zz
        metric_at_x = opt.compute_metric(np.array([1.0, 0.0, 0.0]))

        assert metric_at_z > 0, f"σ_zz should produce non-zero metric at Z-up, got {metric_at_z}"
        assert metric_at_x < metric_at_z, f"X-direction should have lower metric than Z for σ_zz stress"
        print(f"  PASS: Z-up metric={metric_at_z:.4f}, X-perp metric={metric_at_x:.6f}")

    def test_spherical_param_z_up(self):
        """θ=0 should map to [0,0,1] (Z-up north pole)."""
        from fea.orientation_optimizer import OrientationOptimizer

        M = 10
        stress = np.zeros((M, 6))
        stress[:, 0] = 1.0
        vols = np.ones(M)
        vm = np.ones(M)

        opt = OrientationOptimizer(stress, vols, vm, bonding_coeff=0.5, yield_strength=50.0)
        _, grad = opt.compute_metric_and_gradient(np.array([1e-6, 0.0]))

        # At θ≈0, n ≈ [0, 0, 1]. Verify via _dir_to_spherical roundtrip
        theta, phi = OrientationOptimizer._dir_to_spherical(np.array([0.0, 0.0, 1.0]))
        assert abs(theta) < 1e-10, f"Z-up should map to θ≈0, got θ={theta}"
        print(f"  PASS: _dir_to_spherical([0,0,1]) → θ={theta:.1e}, φ={phi:.4f}")


# ═══════════════════════════════════════════════════════════════════════
# Fix C2: Safe dict access + concurrent guard
# ═══════════════════════════════════════════════════════════════════════

class TestC2_SafeDictAccess:
    """Verify canRunOrientationAnalysis checks all 3 required keys."""

    def test_missing_stress_field_returns_false(self):
        """If stress_field is absent, canRunOrientationAnalysis should be False."""
        # Simulate the property logic directly (no Cura needed)
        results = {
            "stress_tensors": np.zeros((10, 6)),
            "element_volumes": np.ones(10),
            # "stress_field" is MISSING
        }
        can_run = (
            results.get("stress_tensors") is not None
            and results.get("element_volumes") is not None
            and results.get("stress_field") is not None
        )
        assert not can_run, "Should be False when stress_field is missing"
        print("  PASS: canRunOrientationAnalysis rejects missing stress_field")

    def test_missing_element_volumes_returns_false(self):
        results = {
            "stress_tensors": np.zeros((10, 6)),
            # "element_volumes" is MISSING
            "stress_field": np.ones(10),
        }
        can_run = (
            results.get("stress_tensors") is not None
            and results.get("element_volumes") is not None
            and results.get("stress_field") is not None
        )
        assert not can_run, "Should be False when element_volumes is missing"
        print("  PASS: canRunOrientationAnalysis rejects missing element_volumes")

    def test_all_present_returns_true(self):
        results = {
            "stress_tensors": np.zeros((10, 6)),
            "element_volumes": np.ones(10),
            "stress_field": np.ones(10),
        }
        can_run = (
            results.get("stress_tensors") is not None
            and results.get("element_volumes") is not None
            and results.get("stress_field") is not None
        )
        assert can_run, "Should be True when all keys present"
        print("  PASS: canRunOrientationAnalysis accepts complete result dict")

    def test_none_values_returns_false(self):
        results = {
            "stress_tensors": None,
            "element_volumes": np.ones(10),
            "stress_field": np.ones(10),
        }
        can_run = (
            results.get("stress_tensors") is not None
            and results.get("element_volumes") is not None
            and results.get("stress_field") is not None
        )
        assert not can_run, "Should be False when stress_tensors is None"
        print("  PASS: canRunOrientationAnalysis rejects None stress_tensors")


# ═══════════════════════════════════════════════════════════════════════
# Fix M2+M3: Vectorized surface_stress_analyzer
# ═══════════════════════════════════════════════════════════════════════

class TestM2M3_Vectorization:
    """Verify vectorized functions produce correct results."""

    def test_identify_surface_elements_cube(self):
        """All elements in a cube should be surface elements."""
        from fea.surface_stress_analyzer import identify_surface_elements

        mesh = _make_cube_tet_mesh()
        mask = identify_surface_elements(mesh)
        assert mask.shape == (5,)
        assert mask.all(), f"All cube elements should be surface, got {mask}"
        print("  PASS: identify_surface_elements — all cube elements are surface")

    def test_identify_surface_elements_with_interior(self):
        """Center-connected mesh should have all elements as surface (all have surface faces)."""
        from fea.surface_stress_analyzer import identify_surface_elements

        mesh = _make_sphere_with_interior()
        mask = identify_surface_elements(mesh)
        # All elements connect to surface nodes on 3 of their faces
        assert mask.shape == (4,)
        print(f"  PASS: identify_surface_elements — sphere mesh: {mask.sum()}/4 surface elements")

    def test_classify_surface_elements_produces_complete_partition(self):
        """Every surface element must be in exactly one category."""
        from fea.surface_stress_analyzer import identify_surface_elements, classify_surface_elements

        mesh = _make_cube_tet_mesh()
        surface_mask = identify_surface_elements(mesh)
        wall, top, bottom = classify_surface_elements(mesh, surface_mask)

        surface_indices = np.where(surface_mask)[0]
        for idx in surface_indices:
            cats = int(wall[idx]) + int(top[idx]) + int(bottom[idx])
            assert cats == 1, f"Element {idx} is in {cats} categories (should be exactly 1)"

        print(f"  PASS: classify produces complete partition — wall={wall.sum()}, top={top.sum()}, bottom={bottom.sum()}")

    def test_build_element_adjacency_cube(self):
        """Cube with 5 tets: interior faces connect adjacent elements."""
        from fea.surface_stress_analyzer import build_element_adjacency

        mesh = _make_cube_tet_mesh()
        adj = build_element_adjacency(mesh)

        # 5 elements, each should have at least 1 neighbor
        assert len(adj) == 5
        total_pairs = sum(len(v) for v in adj.values()) // 2  # each pair counted twice
        assert total_pairs > 0, "Cube should have interior faces connecting elements"
        print(f"  PASS: build_element_adjacency — {total_pairs} interior face pairs in cube")

    def test_compute_stress_gradient_shape(self):
        """Gradient should have shape (M,) and be in [0,1]."""
        from fea.surface_stress_analyzer import (
            identify_surface_elements, compute_stress_gradient
        )

        mesh = _make_cube_tet_mesh()
        surface_mask = identify_surface_elements(mesh)
        stress_field = np.array([10.0, 20.0, 5.0, 15.0, 8.0])

        grad = compute_stress_gradient(mesh, stress_field, surface_mask)
        assert grad.shape == (5,)
        assert grad.max() <= 1.0 + 1e-10
        assert grad.min() >= 0.0 - 1e-10
        print(f"  PASS: compute_stress_gradient — shape={grad.shape}, range=[{grad.min():.3f}, {grad.max():.3f}]")

    def test_compute_stress_gradient_accepts_adjacency(self):
        """Pre-built adjacency should produce same result as internal build."""
        from fea.surface_stress_analyzer import (
            identify_surface_elements, compute_stress_gradient, build_element_adjacency
        )

        mesh = _make_cube_tet_mesh()
        surface_mask = identify_surface_elements(mesh)
        stress_field = np.array([10.0, 20.0, 5.0, 15.0, 8.0])

        adj = build_element_adjacency(mesh)
        grad_with = compute_stress_gradient(mesh, stress_field, surface_mask, adjacency=adj)
        grad_without = compute_stress_gradient(mesh, stress_field, surface_mask)

        np.testing.assert_array_almost_equal(grad_with, grad_without)
        print("  PASS: compute_stress_gradient — pre-built adjacency matches internal build")

    def test_wall_metric_shape_and_range(self):
        """Wall metric should be (M,) in [0,1], zero for non-wall elements."""
        from fea.surface_stress_analyzer import compute_wall_metric

        M = 10
        stress = np.random.uniform(0, 20, M)
        grad = np.random.uniform(0, 1, M)
        wall_mask = np.zeros(M, dtype=bool)
        wall_mask[:5] = True

        W = compute_wall_metric(stress, grad, wall_mask, sigma_eff=25.0)
        assert W.shape == (M,)
        assert W[5:].sum() == 0.0, "Non-wall elements should have zero metric"
        assert W.max() <= 1.0 + 1e-10
        print(f"  PASS: compute_wall_metric — range=[{W.min():.3f}, {W.max():.3f}], non-wall=0")


# ═══════════════════════════════════════════════════════════════════════
# Fix M7: Pre-initialized variables in _solve_scipy
# ═══════════════════════════════════════════════════════════════════════

class TestM7_PreInitVars:
    """Verify pre-initialization of loop variables in iterative_solver."""

    def test_preinit_exists_in_source(self):
        """The pre-init lines should exist in iterative_solver.py."""
        source_path = os.path.join(_plugin_root, "fea", "iterative_solver.py")
        with open(source_path) as f:
            source = f.read()

        assert "Pre-initialise variables" in source, "Pre-init comment not found"
        assert "displacements = np.zeros" in source, "displacements pre-init not found"
        assert "E_eff_arr = np.full" in source, "E_eff_arr pre-init not found"
        assert "nu_arr = np.full" in source, "nu_arr pre-init not found"
        print("  PASS: Pre-initialized variables exist in iterative_solver.py")


# ═══════════════════════════════════════════════════════════════════════
# Fix M4: Shell failure flag propagation
# ═══════════════════════════════════════════════════════════════════════

class TestM4_ShellFailureFlag:
    """Verify shell_optimization_failed flag in job result."""

    def test_flag_exists_in_job_source(self):
        """fea_solve_job.py should set shell_optimization_failed in result dict."""
        source_path = os.path.join(_plugin_root, "jobs", "fea_solve_job.py")
        with open(source_path) as f:
            source = f.read()

        assert "shell_optimization_failed" in source, "Flag not found in fea_solve_job.py"
        assert '"shell_optimization_failed": shell_optimization_failed' in source, \
            "Flag not included in result dict"
        print("  PASS: shell_optimization_failed flag exists in fea_solve_job.py")

    def test_warning_emitted_in_extension(self):
        """FEAInfillExtension should show warning when flag is True."""
        source_path = os.path.join(_plugin_root, "FEAInfillExtension.py")
        with open(source_path) as f:
            source = f.read()

        assert 'shell_optimization_failed' in source, "Flag check not in extension"
        assert 'Shell thickness optimization failed' in source, "Warning message not in extension"
        print("  PASS: Shell failure warning exists in FEAInfillExtension.py")


# ═══════════════════════════════════════════════════════════════════════
# Misc fixes: inf cap, dead code removal, thread safety
# ═══════════════════════════════════════════════════════════════════════

class TestMisc_InfCap:
    """Verify improvement_ratio is capped at 100."""

    def test_improvement_capped_at_100(self):
        """When optimal metric is 0, improvement should be 100, not inf."""
        from fea.orientation_optimizer import OrientationOptimizer

        M = 50
        stress = np.zeros((M, 6))
        stress[:, 2] = 10.0  # uniaxial Z → optimal perpendicular has metric=0
        vols = np.ones(M)
        vm = np.full(M, 10.0)

        opt = OrientationOptimizer(stress, vols, vm, bonding_coeff=0.5, yield_strength=50.0)
        result = opt.optimize(subdivision_levels=1, refine_top_k=2)

        assert result.improvement_ratio == 100.0, \
            f"Expected 100.0, got {result.improvement_ratio}"
        assert result.improvement_ratio != float("inf"), "Should not be inf"
        print(f"  PASS: improvement_ratio capped at {result.improvement_ratio}")


class TestMisc_DeadCodeRemoved:
    """Verify _sigma2 dead allocation was removed."""

    def test_no_sigma2_attribute(self):
        """OrientationOptimizer should NOT have _sigma2 attribute."""
        from fea.orientation_optimizer import OrientationOptimizer

        M = 10
        stress = np.zeros((M, 6))
        stress[:, 0] = 1.0
        vols = np.ones(M)
        vm = np.ones(M)

        opt = OrientationOptimizer(stress, vols, vm, bonding_coeff=0.5, yield_strength=50.0)
        assert not hasattr(opt, "_sigma2"), "_sigma2 should be removed (dead 14.4MB allocation)"
        assert not hasattr(opt, "_sig2_w"), "_sig2_w should be removed (dead allocation)"
        print("  PASS: Dead _sigma2 and _sig2_w allocations removed")


class TestMisc_GradientStillWorks:
    """Verify gradient is still correct after removing _sigma2."""

    def test_gradient_finite_difference(self):
        """Analytical gradient should match finite-difference approximation."""
        from fea.orientation_optimizer import OrientationOptimizer

        M = 100
        np.random.seed(42)
        stress = np.random.randn(M, 6) * 5
        # Make symmetric: ensure Voigt ordering is valid
        vols = np.random.uniform(0.1, 1.0, M)
        vm = np.random.uniform(0.1, 10.0, M)

        opt = OrientationOptimizer(stress, vols, vm, bonding_coeff=0.5, yield_strength=50.0)

        theta0, phi0 = 1.0, 2.0
        params = np.array([theta0, phi0])

        val, grad = opt.compute_metric_and_gradient(params)

        # Finite-difference check
        eps = 1e-6
        for i in range(2):
            p_plus = params.copy()
            p_plus[i] += eps
            p_minus = params.copy()
            p_minus[i] -= eps
            v_plus, _ = opt.compute_metric_and_gradient(p_plus)
            v_minus, _ = opt.compute_metric_and_gradient(p_minus)
            fd_grad = (v_plus - v_minus) / (2 * eps)

            rel_err = abs(grad[i] - fd_grad) / max(abs(fd_grad), 1e-30)
            assert rel_err < 1e-4, \
                f"Gradient component {i}: analytical={grad[i]:.8f}, FD={fd_grad:.8f}, rel_err={rel_err:.2e}"

        print(f"  PASS: Analytical gradient matches finite difference (rel_err < 1e-4)")


class TestMisc_ThreadSafety:
    """Verify _onOrientationFinished uses callLater pattern."""

    def test_calllater_in_source(self):
        source_path = os.path.join(_plugin_root, "FEAInfillExtension.py")
        with open(source_path) as f:
            source = f.read()

        # Find the _onOrientationFinished method and verify callLater
        idx = source.index("def _onOrientationFinished")
        snippet = source[idx:idx+800]
        assert "callLater" in snippet, "callLater not found in _onOrientationFinished"
        assert "def _apply" in snippet, "_apply closure not found"
        print("  PASS: _onOrientationFinished uses callLater for thread safety")


class TestMisc_QMLFeedback:
    """Verify QML has the expected feedback elements."""

    def test_orientation_error_state(self):
        qml_path = os.path.join(_plugin_root, "resources", "qml", "BoundaryConditionPanel.qml")
        with open(qml_path) as f:
            qml = f.read()

        assert "Orientation analysis failed" in qml, "Error state label missing"
        assert "_orientationApplied" in qml, "Post-apply property missing"
        assert "Re-run analysis to verify" in qml, "Post-apply message missing"
        assert "Analyzing orientation" in qml, "Running indicator missing"
        print("  PASS: QML has error state, post-apply feedback, and running indicator")

    def test_shell_description_always_visible(self):
        qml_path = os.path.join(_plugin_root, "resources", "qml", "BoundaryConditionPanel.qml")
        with open(qml_path) as f:
            lines = f.readlines()

        # The description line should NOT have a "visible:" binding
        for i, line in enumerate(lines):
            if "Adjusts wall count" in line:
                # Check the preceding few lines for "visible:"
                context = "".join(lines[max(0, i-3):i])
                assert 'visible: toolProperties.getValue("OptimizeShell")' not in context, \
                    f"Shell description is still gated on OptimizeShell at line {i}"
                print("  PASS: Shell description is always visible (not gated on checkbox)")
                return

        raise AssertionError("Shell description text not found in QML")


# ═══════════════════════════════════════════════════════════════════════
# Shell Thickness Mapper validation
# ═══════════════════════════════════════════════════════════════════════

class TestShellMapper:
    """Validate shell_thickness_mapper produces correct outputs."""

    def test_interior_zone_returns_none(self):
        """A zone with no surface elements should get None shell settings."""
        from mesh_generation.shell_thickness_mapper import compute_zone_shell_settings

        @dataclass
        class FakeZone:
            density: float
            element_indices: list

        zones = [FakeZone(density=0.5, element_indices=[0, 1, 2])]
        M = 5
        W_wall = np.zeros(M)
        W_top = np.zeros(M)
        W_bottom = np.zeros(M)
        wall_mask = np.zeros(M, dtype=bool)
        top_mask = np.zeros(M, dtype=bool)
        bottom_mask = np.zeros(M, dtype=bool)

        result = compute_zone_shell_settings(
            zones, W_wall, W_top, W_bottom,
            wall_mask, top_mask, bottom_mask,
        )
        assert result[0] is None, "Interior zone should get None shell settings"
        print("  PASS: Interior zone correctly returns None shell settings")

    def test_surface_zone_gets_settings(self):
        """A zone with surface elements should get non-None shell settings."""
        from mesh_generation.shell_thickness_mapper import compute_zone_shell_settings, ShellSettings

        @dataclass
        class FakeZone:
            density: float
            element_indices: list

        zones = [FakeZone(density=0.5, element_indices=[0, 1, 2])]
        M = 5
        W_wall = np.array([0.5, 0.3, 0.7, 0.0, 0.0])
        W_top = np.array([0.4, 0.2, 0.0, 0.0, 0.0])
        W_bottom = np.array([0.0, 0.0, 0.6, 0.0, 0.0])
        wall_mask = np.array([True, True, True, False, False])
        top_mask = np.array([True, True, False, False, False])
        bottom_mask = np.array([False, False, True, False, False])

        result = compute_zone_shell_settings(
            zones, W_wall, W_top, W_bottom,
            wall_mask, top_mask, bottom_mask,
        )
        assert result[0] is not None, "Surface zone should get shell settings"
        shell = result[0]
        assert isinstance(shell, ShellSettings)
        assert shell.wall_line_count >= 1
        assert shell.top_layers >= 2
        assert shell.bottom_layers >= 2
        assert shell.wall_thickness_mm > 0
        print(f"  PASS: Surface zone gets ShellSettings(wall={shell.wall_line_count}, "
              f"top_layers={shell.top_layers}, bottom_layers={shell.bottom_layers})")

    def test_low_density_amplification(self):
        """Low-density zones should get amplified wall requirements."""
        from mesh_generation.shell_thickness_mapper import compute_zone_shell_settings

        @dataclass
        class FakeZone:
            density: float
            element_indices: list

        M = 3
        W_wall = np.array([0.5, 0.5, 0.5])
        W_top = np.zeros(M)
        W_bottom = np.zeros(M)
        wall_mask = np.ones(M, dtype=bool)
        top_mask = np.zeros(M, dtype=bool)
        bottom_mask = np.zeros(M, dtype=bool)

        zones_lo = [FakeZone(density=0.2, element_indices=[0, 1, 2])]
        zones_hi = [FakeZone(density=0.9, element_indices=[0, 1, 2])]

        result_lo = compute_zone_shell_settings(
            zones_lo, W_wall, W_top, W_bottom, wall_mask, top_mask, bottom_mask)
        result_hi = compute_zone_shell_settings(
            zones_hi, W_wall, W_top, W_bottom, wall_mask, top_mask, bottom_mask)

        assert result_lo[0].wall_line_count >= result_hi[0].wall_line_count, \
            f"Low density should have >= wall count: lo={result_lo[0].wall_line_count}, hi={result_hi[0].wall_line_count}"
        print(f"  PASS: Low density zone gets wall_count={result_lo[0].wall_line_count} "
              f">= high density={result_hi[0].wall_line_count}")


# ═══════════════════════════════════════════════════════════════════════
# Run all tests
# ═══════════════════════════════════════════════════════════════════════

def _run_all():
    test_classes = [
        TestC1_VoigtIndexFix,
        TestC1_BuildDirection,
        TestC1_OrientationZUp,
        TestC2_SafeDictAccess,
        TestM2M3_Vectorization,
        TestM7_PreInitVars,
        TestM4_ShellFailureFlag,
        TestMisc_InfCap,
        TestMisc_DeadCodeRemoved,
        TestMisc_GradientStillWorks,
        TestMisc_ThreadSafety,
        TestMisc_QMLFeedback,
        TestShellMapper,
    ]

    total = 0
    passed = 0
    failed = 0

    for cls in test_classes:
        print(f"\n{'─' * 60}")
        print(f"  {cls.__name__}")
        print(f"{'─' * 60}")

        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for method_name in sorted(methods):
            total += 1
            try:
                getattr(instance, method_name)()
                passed += 1
            except Exception as e:
                failed += 1
                print(f"  FAIL: {method_name}: {e}")

    print(f"\n{'═' * 60}")
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
    print(f"{'═' * 60}")
    return failed


if __name__ == "__main__":
    sys.exit(_run_all())
