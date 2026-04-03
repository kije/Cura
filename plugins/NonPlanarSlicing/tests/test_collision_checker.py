# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the collision checker module."""

import numpy as np
import pytest

from analysis.height_map import HeightMap
from analysis.collision_checker import check_collisions, CollisionResult


def _make_height_map(z_value=5.0, rows=20, cols=20, resolution=1.0):
    """Create a simple uniform HeightMap."""
    z = np.full((rows, cols), z_value, dtype=np.float64)
    return HeightMap(
        x_min=0.0, x_max=(cols - 1) * resolution,
        y_min=0.0, y_max=(rows - 1) * resolution,
        resolution=resolution,
        z_values=z,
        candidate_z_values=z,
    )


def _simple_polygon():
    """Simple rectangular printhead polygon (±10mm in X, ±10mm in Y)."""
    return [[-10, -10], [10, -10], [10, 10], [-10, 10]]


class TestCheckCollisions:
    """Tests for check_collisions."""

    def test_flat_surface_all_safe(self):
        """Flat surface with plenty of clearance should be all safe."""
        hm = _make_height_map(z_value=5.0)
        result = check_collisions(
            hm, _simple_polygon(),
            nozzle_clearance_mm=10.0,
            safety_margin_mm=1.0,
        )
        assert isinstance(result, CollisionResult)
        assert result.safe_count > 0
        # Flat surface: all cells at same Z, so max neighbourhood Z == cell Z.
        # shroud_bottom = z + effective_clearance = 5 + 9 = 14.
        # max_neighbourhood_z = 5. 5 <= 14 → all safe.
        assert result.collision_count == 0

    def test_spike_causes_collision(self):
        """A tall spike in the height map should cause collisions nearby."""
        hm = _make_height_map(z_value=5.0)
        # Add a spike
        hm.z_values[10, 10] = 50.0
        result = check_collisions(
            hm, _simple_polygon(),
            nozzle_clearance_mm=8.0,
            safety_margin_mm=1.0,
        )
        # The spike itself and surrounding cells should show collisions
        assert result.collision_count > 0

    def test_result_shape(self):
        """Result maps should match height map shape."""
        hm = _make_height_map(rows=15, cols=25)
        result = check_collisions(hm, _simple_polygon(), nozzle_clearance_mm=10.0)
        assert result.safe_map.shape == (15, 25)
        assert result.collision_map.shape == (15, 25)

    def test_nan_cells_neither_safe_nor_collision(self):
        """NaN cells should not be marked as safe or collision."""
        hm = _make_height_map(z_value=np.nan, rows=10, cols=10)
        result = check_collisions(hm, _simple_polygon(), nozzle_clearance_mm=10.0)
        assert result.safe_count == 0
        assert result.collision_count == 0

    def test_zero_clearance_causes_collisions(self):
        """With zero clearance and safety margin, slight variations cause collisions."""
        z = np.full((20, 20), 5.0)
        z[5:15, 5:15] = 5.5  # Slight elevation in center
        hm = HeightMap(
            x_min=0.0, x_max=19.0, y_min=0.0, y_max=19.0,
            resolution=1.0, z_values=z, candidate_z_values=z,
        )
        result = check_collisions(
            hm, _simple_polygon(),
            nozzle_clearance_mm=0.0,
            safety_margin_mm=0.0,
        )
        # Some collisions expected at edges where lower cells are near higher ones
        # Actually with zero clearance, any neighbor higher than cell = collision
        assert result.collision_count > 0

    def test_safe_and_collision_mutually_exclusive(self):
        """Safe and collision maps should not both be True for any cell."""
        hm = _make_height_map(z_value=5.0)
        hm.z_values[10, 10] = 20.0
        result = check_collisions(hm, _simple_polygon(), nozzle_clearance_mm=8.0)
        overlap = result.safe_map & result.collision_map
        assert not overlap.any()

    def test_counts_match_maps(self):
        """safe_count and collision_count should match map sums."""
        hm = _make_height_map(z_value=5.0)
        hm.z_values[5, 5] = 30.0
        result = check_collisions(hm, _simple_polygon(), nozzle_clearance_mm=8.0)
        assert result.safe_count == int(np.count_nonzero(result.safe_map))
        assert result.collision_count == int(np.count_nonzero(result.collision_map))

    def test_large_clearance_all_safe(self):
        """Very large clearance should make everything safe (shroud check)."""
        hm = _make_height_map(z_value=5.0)
        # Don't add a bump — nozzle cone check can still fail even with
        # large shroud clearance if nearby cells are above cone allowance.
        result = check_collisions(
            hm, _simple_polygon(),
            nozzle_clearance_mm=100.0,
            safety_margin_mm=0.0,
        )
        # Flat surface: all same height, so cone check also passes.
        assert result.safe_count == np.count_nonzero(np.isfinite(hm.z_values))
        assert result.collision_count == 0
