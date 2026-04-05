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

    def test_surface_mask_expands_grid(self):
        """surface_mask including faces outside candidate bbox should expand grid.

        Creates a mesh with a candidate face near the origin and a
        surface-only face far away.  Without surface_mask the grid covers
        only the candidate face's bounding box.  With surface_mask the
        grid expands to cover both faces.
        """
        # Candidate face: small triangle near origin.
        # Surface-only face: small triangle centred at (20, 20).
        vertices = np.array([
            # Face 0 (candidate): triangle at origin area
            [0, 0, 5], [2, 0, 5], [1, 2, 5],
            # Face 1 (surface-only): triangle far away
            [19, 19, 7], [21, 19, 7], [20, 21, 7],
        ], dtype=np.float64)
        indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.intp)

        # Only face 0 is a candidate.
        candidate_mask = np.array([True, False], dtype=bool)
        # Both faces are surface faces.
        surface_mask = np.array([True, True], dtype=bool)

        # Without surface_mask: grid covers only face 0.
        hm_candidate_only = generate_height_map(
            vertices, indices, candidate_mask, resolution=1.0,
        )

        # With surface_mask: grid should expand to cover face 1 too.
        hm_with_surface = generate_height_map(
            vertices, indices, candidate_mask, resolution=1.0,
            surface_mask=surface_mask,
        )

        # The expanded grid should cover a larger X/Y range.
        candidate_x_range = hm_candidate_only.x_max - hm_candidate_only.x_min
        surface_x_range = hm_with_surface.x_max - hm_with_surface.x_min

        assert surface_x_range > candidate_x_range, (
            f"With surface_mask, grid X range ({surface_x_range:.1f}) should be "
            f"larger than candidate-only range ({candidate_x_range:.1f})"
        )

        # The expanded grid should also have more cells.
        assert hm_with_surface.z_values.size > hm_candidate_only.z_values.size

        # The surface-only face at Z=7 should appear in z_values of
        # the expanded grid, but NOT in candidate_z_values.
        # Check that some cells near (20, 20) have finite Z in z_values.
        if hm_with_surface.in_bounds(20.0, 20.0):
            row, col = hm_with_surface.get_grid_coords(20.0, 20.0)
            assert np.isfinite(hm_with_surface.z_values[row, col]), (
                "Surface face at (20, 20) should produce finite z_values"
            )
            # But candidate_z should be NaN there (face 1 is not a candidate).
            assert np.isnan(hm_with_surface.candidate_z_values[row, col]), (
                "Non-candidate surface face should have NaN in candidate_z_values"
            )

    def test_surface_mask_none_fallback(self):
        """surface_mask=None should fall back to candidate_mask only.

        Behaviour should be identical to not passing surface_mask at all.
        """
        verts, indices = _make_flat_square(z=5.0, scale=10.0)
        candidate_mask = np.ones(2, dtype=bool)

        hm_default = generate_height_map(
            verts, indices, candidate_mask, resolution=1.0,
        )
        hm_explicit_none = generate_height_map(
            verts, indices, candidate_mask, resolution=1.0,
            surface_mask=None,
        )

        # Both should produce identical height maps.
        assert hm_default.x_min == pytest.approx(hm_explicit_none.x_min)
        assert hm_default.x_max == pytest.approx(hm_explicit_none.x_max)
        assert hm_default.y_min == pytest.approx(hm_explicit_none.y_min)
        assert hm_default.y_max == pytest.approx(hm_explicit_none.y_max)
        assert hm_default.z_values.shape == hm_explicit_none.z_values.shape
        # Z values should be identical (NaN-aware comparison).
        np.testing.assert_array_equal(
            np.isnan(hm_default.z_values),
            np.isnan(hm_explicit_none.z_values),
        )
        valid = np.isfinite(hm_default.z_values)
        if valid.any():
            np.testing.assert_allclose(
                hm_default.z_values[valid],
                hm_explicit_none.z_values[valid],
            )
