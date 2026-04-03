# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the height map module."""

import math
import numpy as np
import pytest

from analysis.height_map import HeightMap, generate_height_map


def _make_flat_square(z=5.0, scale=10.0):
    """Create a flat horizontal square at height z."""
    vertices = np.array([
        [0, 0, z], [scale, 0, z], [scale, scale, z],
        [0, 0, z], [scale, scale, z], [0, scale, z],
    ], dtype=np.float64)
    indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.intp)
    return vertices, indices


class TestHeightMapObject:
    """Tests for HeightMap data class methods."""

    def test_get_grid_coords(self):
        hm = HeightMap(
            x_min=0.0, x_max=10.0, y_min=0.0, y_max=10.0,
            resolution=1.0,
            z_values=np.ones((11, 11)),
            candidate_z_values=np.ones((11, 11)),
        )
        row, col = hm.get_grid_coords(5.0, 5.0)
        assert row == 5
        assert col == 5

    def test_get_grid_coords_clamped(self):
        hm = HeightMap(
            x_min=0.0, x_max=10.0, y_min=0.0, y_max=10.0,
            resolution=1.0,
            z_values=np.ones((11, 11)),
            candidate_z_values=np.ones((11, 11)),
        )
        row, col = hm.get_grid_coords(-5.0, -5.0)
        assert row == 0
        assert col == 0

    def test_is_valid_finite(self):
        z = np.ones((5, 5))
        hm = HeightMap(
            x_min=0.0, x_max=4.0, y_min=0.0, y_max=4.0,
            resolution=1.0, z_values=z, candidate_z_values=z,
        )
        assert hm.is_valid(2.0, 2.0)

    def test_is_valid_nan(self):
        z = np.full((5, 5), np.nan)
        hm = HeightMap(
            x_min=0.0, x_max=4.0, y_min=0.0, y_max=4.0,
            resolution=1.0, z_values=z, candidate_z_values=z,
        )
        assert not hm.is_valid(2.0, 2.0)

    def test_interpolate_flat(self):
        """Flat surface should interpolate to the constant Z."""
        z = np.full((11, 11), 5.0)
        hm = HeightMap(
            x_min=0.0, x_max=10.0, y_min=0.0, y_max=10.0,
            resolution=1.0, z_values=z, candidate_z_values=z,
        )
        result = hm.interpolate(5.0, 5.0)
        assert result == pytest.approx(5.0)

    def test_interpolate_nan_outside(self):
        """Points outside bounds should return NaN."""
        z = np.ones((5, 5))
        hm = HeightMap(
            x_min=0.0, x_max=4.0, y_min=0.0, y_max=4.0,
            resolution=1.0, z_values=z, candidate_z_values=z,
        )
        result = hm.interpolate(-10.0, -10.0)
        assert math.isnan(result)

    def test_interpolate_linear(self):
        """Test bilinear interpolation on a ramp."""
        z = np.zeros((3, 3))
        z[0, :] = 0.0
        z[1, :] = 1.0
        z[2, :] = 2.0
        hm = HeightMap(
            x_min=0.0, x_max=2.0, y_min=0.0, y_max=2.0,
            resolution=1.0, z_values=z, candidate_z_values=z,
        )
        # Midpoint between row 0 and row 1 at col 1
        result = hm.interpolate(1.0, 0.5)
        assert result == pytest.approx(0.5)

    def test_grid_shape(self):
        z = np.ones((5, 10))
        hm = HeightMap(
            x_min=0.0, x_max=9.0, y_min=0.0, y_max=4.0,
            resolution=1.0, z_values=z, candidate_z_values=z,
        )
        assert hm.grid_shape == (5, 10)


class TestGenerateHeightMap:
    """Tests for generate_height_map."""

    def test_flat_surface(self):
        """Flat square should produce uniform Z in height map."""
        verts, indices = _make_flat_square(z=5.0, scale=10.0)
        candidate_mask = np.ones(2, dtype=bool)
        hm = generate_height_map(verts, indices, candidate_mask, resolution=1.0)

        assert isinstance(hm, HeightMap)
        # Some cells within the triangle should have z ~5.0
        valid = np.isfinite(hm.z_values)
        if valid.any():
            assert np.nanmin(hm.z_values[valid]) == pytest.approx(5.0, abs=0.5)

    def test_no_candidates(self):
        """All-false candidate mask should return empty height map."""
        verts, indices = _make_flat_square(z=5.0)
        candidate_mask = np.zeros(2, dtype=bool)
        hm = generate_height_map(verts, indices, candidate_mask)
        # Should still return a HeightMap, just with NaN values
        assert isinstance(hm, HeightMap)

    def test_candidate_mask_mismatch(self):
        """Wrong-length candidate mask should raise ValueError."""
        verts, indices = _make_flat_square()
        with pytest.raises(ValueError):
            generate_height_map(verts, indices, np.ones(99, dtype=bool))

    def test_non_indexed_mesh(self):
        """Should work without explicit indices."""
        verts = np.array([
            [0, 0, 3], [10, 0, 3], [10, 10, 3],
            [0, 0, 3], [10, 10, 3], [0, 10, 3],
        ], dtype=np.float64)
        candidate_mask = np.ones(2, dtype=bool)
        hm = generate_height_map(verts, None, candidate_mask, resolution=1.0)
        assert isinstance(hm, HeightMap)

    def test_resolution_affects_grid_size(self):
        """Smaller resolution should produce a larger grid."""
        verts, indices = _make_flat_square(z=5.0, scale=10.0)
        candidate_mask = np.ones(2, dtype=bool)
        hm_coarse = generate_height_map(verts, indices, candidate_mask, resolution=2.0)
        hm_fine = generate_height_map(verts, indices, candidate_mask, resolution=0.5)
        assert hm_fine.z_values.size > hm_coarse.z_values.size
