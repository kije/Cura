"""Tests for the deformation field module.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from analysis.deformation_field import (
    DeformationField,
    compute_deformation_field,
    _enforce_slope_constraint,
    _enforce_thickness_constraint,
    _enforce_floor_constraint,
    _HAS_SCIPY_SPARSE,
)


# ---------------------------------------------------------------------------
# Helpers: mock height map
# ---------------------------------------------------------------------------

class MockHeightMap:
    """Minimal height map for testing."""

    def __init__(self, z_values, x_min=0.0, y_min=0.0, resolution=1.0,
                 candidate_z_values=None):
        self.z_values = np.asarray(z_values, dtype=np.float64)
        self.x_min = x_min
        self.y_min = y_min
        self.resolution = resolution
        rows, cols = self.z_values.shape
        self.x_max = x_min + (cols - 1) * resolution
        self.y_max = y_min + (rows - 1) * resolution
        if candidate_z_values is not None:
            self.candidate_z_values = np.asarray(candidate_z_values, dtype=np.float64)
        else:
            self.candidate_z_values = self.z_values.copy()

    def in_bounds(self, x, y):
        half = self.resolution * 0.5
        return (self.x_min - half <= x <= self.x_max + half and
                self.y_min - half <= y <= self.y_max + half)

    def interpolate(self, x, y):
        if not self.in_bounds(x, y):
            return float("nan")
        row, col = self.get_grid_coords(x, y)
        return float(self.z_values[row, col])

    def get_grid_coords(self, x, y):
        cols = self.z_values.shape[1]
        rows = self.z_values.shape[0]
        col = int(np.clip(np.round((x - self.x_min) / self.resolution), 0, cols - 1))
        row = int(np.clip(np.round((y - self.y_min) / self.resolution), 0, rows - 1))
        return row, col

    def is_valid(self, x, y):
        if not self.in_bounds(x, y):
            return False
        row, col = self.get_grid_coords(x, y)
        return bool(np.isfinite(self.z_values[row, col]))


# ---------------------------------------------------------------------------
# Tests: DeformationField class
# ---------------------------------------------------------------------------

class TestDeformationField:
    """Tests for the DeformationField dataclass methods."""

    def _make_field(self, num_layers=5, rows=3, cols=3, layer_height=0.2):
        z_levels = np.array([0.2 + i * layer_height for i in range(num_layers)])
        displacements = np.zeros((num_layers, rows, cols), dtype=np.float64)
        return DeformationField(
            x_min=0.0, x_max=(cols - 1) * 1.0,
            y_min=0.0, y_max=(rows - 1) * 1.0,
            resolution=1.0,
            z_levels=z_levels,
            displacements=displacements,
        )

    def test_zero_displacement_returns_original_z(self):
        field = self._make_field()
        # All displacements are zero, so target Z == original Z.
        assert field.get_target_z(1.0, 1.0, 0.4) == pytest.approx(0.4)

    def test_uniform_displacement(self):
        field = self._make_field()
        field.displacements[:] = 0.1  # uniform +0.1mm shift
        target = field.get_target_z(1.0, 1.0, 0.4)
        assert target == pytest.approx(0.5, abs=0.01)

    def test_out_of_bounds_returns_original(self):
        field = self._make_field()
        field.displacements[:] = 0.5
        # Way outside bounds.
        target = field.get_target_z(100.0, 100.0, 0.4)
        assert target == pytest.approx(0.4)

    def test_interpolation_between_layers(self):
        """Displacement should interpolate between Z levels."""
        field = self._make_field(num_layers=3, layer_height=0.2)
        # Layer 0 (z=0.2): disp=0.0, Layer 1 (z=0.4): disp=0.1, Layer 2 (z=0.6): disp=0.2
        field.displacements[0, :, :] = 0.0
        field.displacements[1, :, :] = 0.1
        field.displacements[2, :, :] = 0.2
        # At z=0.3 (midpoint between layer 0 and 1), expect ~0.05.
        disp = field.interpolate(1.0, 1.0, 0.3)
        assert disp == pytest.approx(0.05, abs=0.01)

    def test_bilinear_xy_interpolation(self):
        """Displacement should interpolate bilinearly in XY."""
        field = self._make_field(num_layers=1, rows=2, cols=2, layer_height=0.2)
        field.displacements[0, 0, 0] = 0.0
        field.displacements[0, 0, 1] = 0.1
        field.displacements[0, 1, 0] = 0.1
        field.displacements[0, 1, 1] = 0.2
        # At center (0.5, 0.5), expect average = 0.1.
        disp = field.interpolate(0.5, 0.5, 0.2)
        assert disp == pytest.approx(0.1, abs=0.01)

    def test_local_thickness_uniform(self):
        """With zero displacement, thickness should equal nominal."""
        field = self._make_field()
        thickness = field.get_local_thickness(1.0, 1.0, 0.4, layer_height=0.2)
        assert thickness == pytest.approx(0.2, abs=0.01)

    def test_local_thickness_with_displacement(self):
        """With varying displacement, thickness should change."""
        field = self._make_field(num_layers=5, layer_height=0.2)
        # Bottom layers have less displacement, top layers more.
        for i in range(5):
            field.displacements[i, :, :] = 0.05 * i
        # At z=0.6 (layer 2), disp=0.10; at z=0.4 (layer 1), disp=0.05.
        # Actual gap: (0.6 + 0.10) - (0.4 + 0.05) = 0.25.
        thickness = field.get_local_thickness(1.0, 1.0, 0.6, layer_height=0.2)
        assert thickness == pytest.approx(0.25, abs=0.02)

    def test_num_layers(self):
        field = self._make_field(num_layers=10)
        assert field.num_layers == 10

    def test_grid_shape(self):
        field = self._make_field(rows=4, cols=5)
        assert field.grid_shape == (4, 5)

    def test_in_bounds(self):
        field = self._make_field(rows=3, cols=3)
        assert field.in_bounds(1.0, 1.0)
        assert not field.in_bounds(100.0, 100.0)


# ---------------------------------------------------------------------------
# Tests: compute_deformation_field
# ---------------------------------------------------------------------------

class TestComputeDeformationField:
    """Tests for the main compute_deformation_field function."""

    def test_flat_surface_zero_displacement(self):
        """A perfectly flat surface at a layer boundary should yield zero displacement."""
        # Surface at z=1.0, which is exactly layer 5 (0.2 * 5).
        z_vals = np.full((5, 5), 1.0)
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((5, 5), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=5, first_layer_z=0.2,
            decay_distance=5.0,
        )

        # All displacements should be zero (surface sits exactly on a layer).
        assert np.max(np.abs(field.displacements)) == pytest.approx(0.0, abs=0.01)

    def test_inclined_surface_positive_displacement(self):
        """An inclined surface should produce nonzero displacement at the surface."""
        # Create a tilted surface: z varies from 0.8 to 1.2 across cols.
        rows, cols = 5, 5
        z_vals = np.zeros((rows, cols))
        for c in range(cols):
            z_vals[:, c] = 0.8 + 0.1 * c  # 0.8, 0.9, 1.0, 1.1, 1.2
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=7, first_layer_z=0.2,
            decay_distance=5.0,
        )

        # The surface at col=0 (z=0.8) is on a layer boundary.
        # The surface at col=2 (z=1.0) is on a layer boundary.
        # The surface at col=1 (z=0.9) is between layers → should have displacement.
        assert field.displacements.shape[0] == 7
        # Check that some displacement is nonzero.
        assert np.max(np.abs(field.displacements)) > 0.01

    def test_decay_reduces_displacement_at_depth(self):
        """Deeper layers should have less displacement than surface layers."""
        rows, cols = 3, 3
        # Surface at z=1.1 (midway between layers).
        z_vals = np.full((rows, cols), 1.1)
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=10, first_layer_z=0.2,
            decay_distance=2.0,
        )

        # Find the surface layer index (z=1.0, idx=4).
        surface_disp = abs(field.displacements[4, 1, 1])
        # Deep layer (z=0.2, idx=0) should have much less displacement.
        deep_disp = abs(field.displacements[0, 1, 1])
        assert deep_disp < surface_disp

    def test_safe_map_zeroes_outside(self):
        """Displacement should be zero outside the safe region."""
        rows, cols = 3, 3
        z_vals = np.full((rows, cols), 1.1)
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.zeros((rows, cols), dtype=np.bool_)
        safe[1, 1] = True  # Only center cell safe.

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=6, first_layer_z=0.2,
            decay_distance=5.0,
        )

        # Corner cells should have zero displacement.
        assert field.displacements[:, 0, 0].sum() == pytest.approx(0.0)
        assert field.displacements[:, 2, 2].sum() == pytest.approx(0.0)

    def test_nan_cells_handled(self):
        """NaN cells in height map should not produce displacements."""
        rows, cols = 3, 3
        z_vals = np.full((rows, cols), np.nan)
        z_vals[1, 1] = 1.0  # Only center has data.
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=5, first_layer_z=0.2,
            decay_distance=5.0,
        )

        # NaN corners: the resampled surface_z will be NaN there,
        # so no displacement should be generated.
        assert np.all(np.isfinite(field.displacements))

    def test_floor_constraint(self):
        """No deformed Z should go below the bed floor (0.05mm)."""
        rows, cols = 3, 3
        z_vals = np.full((rows, cols), 0.15)  # Very low surface.
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.1, total_layers=3, first_layer_z=0.1,
            decay_distance=5.0,
        )

        # Verify no deformed Z < 0.05.
        for i in range(field.num_layers):
            deformed = field.z_levels[i] + field.displacements[i]
            assert np.all(deformed >= 0.05 - 1e-9)

    def test_optimization_resolution(self):
        """Coarser optimization resolution should still produce valid field."""
        rows, cols = 10, 10
        z_vals = np.full((rows, cols), 1.5)
        hm = MockHeightMap(z_vals, resolution=0.5)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=8, first_layer_z=0.2,
            decay_distance=5.0,
            optimization_resolution=2.0,
        )

        # Should produce a field matching the height map grid.
        assert field.displacements.shape[1] == rows
        assert field.displacements.shape[2] == cols


# ---------------------------------------------------------------------------
# Tests: constraint enforcement
# ---------------------------------------------------------------------------

class TestConstraints:
    """Tests for individual constraint enforcement functions."""

    def test_slope_constraint_clamps_gradient(self):
        """Steep gradients should be clamped to max slope."""
        z_levels = np.array([0.2])
        displacements = np.zeros((1, 1, 5), dtype=np.float64)
        # Create a step discontinuity.
        displacements[0, 0, :] = [0.0, 0.0, 1.0, 1.0, 1.0]
        resolution = 1.0
        _enforce_slope_constraint(displacements, z_levels, resolution, max_angle_deg=45.0)
        # The gradient from col 1 to col 2 was 1.0/1.0mm = 45 deg.
        # tan(45) = 1.0, so max delta per cell = 1.0. Should be at limit.
        assert displacements[0, 0, 2] <= displacements[0, 0, 1] + 1.0 + 1e-9

    def test_thickness_constraint_prevents_thin_layers(self):
        """Adjacent layers should not get thinner than min ratio."""
        z_levels = np.array([0.2, 0.4, 0.6])
        displacements = np.zeros((3, 1, 1), dtype=np.float64)
        # Make layer 1 and layer 2 collide.
        displacements[1, 0, 0] = 0.15  # z=0.4 → 0.55
        displacements[2, 0, 0] = -0.1  # z=0.6 → 0.50
        # Gap would be 0.50 - 0.55 = -0.05, way too thin.
        _enforce_thickness_constraint(
            displacements, z_levels,
            layer_height=0.2, min_ratio=0.5, max_ratio=2.0,
        )
        # After constraint, gap should be at least 0.5 * 0.2 = 0.1.
        deformed_1 = 0.4 + displacements[1, 0, 0]
        deformed_2 = 0.6 + displacements[2, 0, 0]
        assert deformed_2 - deformed_1 >= 0.1 - 1e-9

    def test_thickness_constraint_prevents_thick_layers(self):
        """Adjacent layers should not get thicker than max ratio."""
        z_levels = np.array([0.2, 0.4, 0.6])
        displacements = np.zeros((3, 1, 1), dtype=np.float64)
        # Make layers diverge wildly.
        displacements[1, 0, 0] = -0.3  # z=0.4 → 0.10
        displacements[2, 0, 0] = 0.3   # z=0.6 → 0.90
        # Gap would be 0.90 - 0.10 = 0.80, exceeds 2.0 * 0.2 = 0.40.
        _enforce_thickness_constraint(
            displacements, z_levels,
            layer_height=0.2, min_ratio=0.5, max_ratio=2.0,
        )
        deformed_1 = 0.4 + displacements[1, 0, 0]
        deformed_2 = 0.6 + displacements[2, 0, 0]
        assert deformed_2 - deformed_1 <= 0.4 + 1e-9

    def test_floor_constraint_prevents_negative_z(self):
        """Deformed Z should never go below bed floor."""
        z_levels = np.array([0.1])
        displacements = np.zeros((1, 1, 1), dtype=np.float64)
        displacements[0, 0, 0] = -0.2  # Would put z at -0.1.
        _enforce_floor_constraint(displacements, z_levels)
        deformed = 0.1 + displacements[0, 0, 0]
        assert deformed >= 0.05 - 1e-9


# ---------------------------------------------------------------------------
# Tests: QP solver (if osqp available)
# ---------------------------------------------------------------------------

class TestQPSolver:
    """Tests for the QP-based deformation field solver."""

    @pytest.fixture(autouse=True)
    def _check_osqp(self):
        try:
            import osqp
            from scipy import sparse
        except ImportError:
            pytest.skip("osqp not installed")

    def test_qp_produces_smooth_field(self):
        """QP solver should produce a smoother field than the heuristic."""
        rows, cols = 5, 5
        # Tilted surface: z varies from 0.85 to 1.15
        z_vals = np.zeros((rows, cols))
        for c in range(cols):
            z_vals[:, c] = 0.85 + 0.075 * c
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=6, first_layer_z=0.2,
            decay_distance=5.0,
        )

        # The field should have non-zero displacement
        assert np.max(np.abs(field.displacements)) > 0.01

        # Check smoothness: gradient magnitude should be bounded
        for k in range(field.num_layers):
            d = field.displacements[k]
            if d.shape[1] > 1:
                dx = np.diff(d, axis=1)
                assert np.all(np.abs(dx) < 1.0), "X gradient too large"
            if d.shape[0] > 1:
                dy = np.diff(d, axis=0)
                assert np.all(np.abs(dy) < 1.0), "Y gradient too large"

    def test_qp_respects_thickness_bounds(self):
        """QP solution should maintain valid layer thickness."""
        rows, cols = 3, 3
        z_vals = np.full((rows, cols), 1.1)
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=6, first_layer_z=0.2,
            decay_distance=3.0,
            min_thickness_ratio=0.5,
            max_thickness_ratio=2.0,
        )

        # Check thickness bounds between adjacent layers
        for k in range(1, field.num_layers):
            gap = (field.z_levels[k] + field.displacements[k]) - \
                  (field.z_levels[k - 1] + field.displacements[k - 1])
            min_gap = 0.5 * 0.2
            max_gap = 2.0 * 0.2
            # Allow small tolerance for solver precision
            assert np.all(gap >= min_gap - 0.01), \
                f"Layer {k}: min thickness violated (min gap={gap.min():.4f})"
            assert np.all(gap <= max_gap + 0.01), \
                f"Layer {k}: max thickness violated (max gap={gap.max():.4f})"

    def test_qp_zeroes_outside_safe(self):
        """QP should produce zero displacement outside safe region."""
        rows, cols = 5, 5
        z_vals = np.full((rows, cols), 1.1)
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.zeros((rows, cols), dtype=np.bool_)
        safe[2, 2] = True  # Only center

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=6, first_layer_z=0.2,
            decay_distance=5.0,
        )

        # All corners should be zero
        assert field.displacements[:, 0, 0].sum() == pytest.approx(0.0, abs=0.01)
        assert field.displacements[:, 4, 4].sum() == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: QuickCurve solver (if scipy.sparse available)
# ---------------------------------------------------------------------------

class TestQuickCurveSolver:
    """Tests for the QuickCurve least-squares deformation field solver."""

    @pytest.fixture(autouse=True)
    def _check_scipy(self):
        if not _HAS_SCIPY_SPARSE:
            pytest.skip("scipy.sparse not installed")

    def test_quickcurve_flat_surface_zero_displacement(self):
        """A flat surface at a layer boundary should yield near-zero displacement."""
        z_vals = np.full((5, 5), 1.0)
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((5, 5), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=5, first_layer_z=0.2,
            decay_distance=5.0,
        )

        assert np.max(np.abs(field.displacements)) == pytest.approx(0.0, abs=0.02)

    def test_quickcurve_produces_smooth_surface(self):
        """QuickCurve should produce a smoother field than raw surface displacement."""
        rows, cols = 7, 7
        z_vals = np.zeros((rows, cols))
        for c in range(cols):
            z_vals[:, c] = 0.85 + 0.05 * c
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=6, first_layer_z=0.2,
            decay_distance=5.0,
        )

        # Should have nonzero displacement
        assert np.max(np.abs(field.displacements)) > 0.01

        # Gradient should be smooth (no sharp jumps between cells)
        for k in range(field.num_layers):
            d = field.displacements[k]
            if d.shape[1] > 1:
                dx = np.abs(np.diff(d, axis=1))
                assert np.max(dx) < 0.5, f"Layer {k}: X gradient too steep"

    def test_quickcurve_respects_safe_region(self):
        """Displacement should be zero outside the safe region."""
        rows, cols = 5, 5
        z_vals = np.full((rows, cols), 1.1)
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.zeros((rows, cols), dtype=np.bool_)
        safe[2, 2] = True

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=6, first_layer_z=0.2,
            decay_distance=5.0,
        )

        assert field.displacements[:, 0, 0].sum() == pytest.approx(0.0, abs=0.01)
        assert field.displacements[:, 4, 4].sum() == pytest.approx(0.0, abs=0.01)

    def test_quickcurve_decay_with_depth(self):
        """Deeper layers should have less displacement than surface layers."""
        rows, cols = 3, 3
        z_vals = np.full((rows, cols), 1.1)
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=10, first_layer_z=0.2,
            decay_distance=2.0,
        )

        # Surface layer (idx ~4-5) should have more displacement than deep layer (idx 0)
        surface_disp = abs(field.displacements[4, 1, 1])
        deep_disp = abs(field.displacements[0, 1, 1])
        assert deep_disp < surface_disp

    def test_quickcurve_slope_enforcement(self):
        """After QuickCurve solve, slope should respect max angle."""
        rows, cols = 5, 5
        z_vals = np.zeros((rows, cols))
        # Create a steep step: one side at 0.5, other at 1.5
        z_vals[:, :2] = 0.5
        z_vals[:, 3:] = 1.5
        z_vals[:, 2] = 1.0
        hm = MockHeightMap(z_vals, resolution=1.0)
        safe = np.ones((rows, cols), dtype=np.bool_)

        field = compute_deformation_field(
            hm, safe,
            layer_height=0.2, total_layers=8, first_layer_z=0.2,
            decay_distance=5.0,
            max_angle_deg=30.0,
        )

        # Check that XY gradient doesn't exceed slope limit
        max_slope = math.tan(math.radians(30.0))
        max_delta = max_slope * 1.0  # resolution = 1.0mm
        for k in range(field.num_layers):
            d = field.displacements[k]
            if d.shape[1] > 1:
                dx = np.abs(np.diff(d, axis=1))
                # Allow some tolerance for the LS solution + enforcement
                assert np.max(dx) <= max_delta + 0.05, \
                    f"Layer {k}: slope violation (max dx={np.max(dx):.3f}, limit={max_delta:.3f})"
