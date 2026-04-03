# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the surface analyzer module."""

import math
import numpy as np
import pytest

from analysis.surface_analyzer import analyze_mesh, SurfaceAnalysis


def _make_flat_square(z=0.0):
    """Create a flat horizontal square at height z (2 triangles, Z-up)."""
    vertices = np.array([
        [0, 0, z], [1, 0, z], [1, 1, z],
        [0, 0, z], [1, 1, z], [0, 1, z],
    ], dtype=np.float64)
    indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.intp)
    return vertices, indices


def _make_ramp(angle_deg=30.0):
    """Create a ramp surface tilted at the given angle from horizontal.

    The ramp rises in the Y direction.
    """
    rise = math.tan(math.radians(angle_deg))
    vertices = np.array([
        [0, 0, 0],
        [1, 0, 0],
        [1, 1, rise],
        [0, 0, 0],
        [1, 1, rise],
        [0, 1, rise],
    ], dtype=np.float64)
    indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.intp)
    return vertices, indices


def _make_vertical_wall():
    """Create a vertical wall (normal pointing in +X direction)."""
    vertices = np.array([
        [0, 0, 0], [0, 1, 0], [0, 1, 1],
        [0, 0, 0], [0, 1, 1], [0, 0, 1],
    ], dtype=np.float64)
    indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.intp)
    return vertices, indices


def _make_bottom_face():
    """Create a downward-facing face (normal pointing -Z)."""
    # Clockwise winding when viewed from above = normal points down
    vertices = np.array([
        [0, 0, 1], [1, 1, 1], [1, 0, 1],
    ], dtype=np.float64)
    indices = np.array([[0, 1, 2]], dtype=np.intp)
    return vertices, indices


class TestAnalyzeMesh:
    """Tests for analyze_mesh function."""

    def test_flat_horizontal_top(self):
        """Flat horizontal surface should be classified as top surface."""
        verts, indices = _make_flat_square(z=5.0)
        result = analyze_mesh(verts, indices)

        assert isinstance(result, SurfaceAnalysis)
        assert result.face_normals.shape == (2, 3)
        assert result.face_centers.shape == (2, 3)
        assert result.face_areas.shape == (2,)
        assert result.angles_from_horizontal.shape == (2,)
        assert result.is_top_surface.shape == (2,)

        # Normal should point up (0, 0, 1)
        for i in range(2):
            np.testing.assert_allclose(result.face_normals[i], [0, 0, 1], atol=1e-10)

        # Angle from Z-up should be ~0
        for i in range(2):
            assert result.angles_from_horizontal[i] < 0.01

        # Should be top surface
        assert result.is_top_surface.all()

    def test_ramp_30deg(self):
        """30-degree ramp should be a top surface."""
        verts, indices = _make_ramp(30.0)
        result = analyze_mesh(verts, indices)

        # Angle from Z-up should be ~30 degrees
        expected_angle = math.radians(30.0)
        for i in range(2):
            assert abs(result.angles_from_horizontal[i] - expected_angle) < 0.1

        # Should still be top surface (< 80 deg threshold)
        assert result.is_top_surface.all()

    def test_vertical_wall_not_top(self):
        """Vertical wall should NOT be top surface."""
        verts, indices = _make_vertical_wall()
        result = analyze_mesh(verts, indices)

        # Angle from Z-up should be ~90 degrees
        for i in range(2):
            assert abs(result.angles_from_horizontal[i] - math.pi / 2) < 0.1

        # Should not be top surface
        assert not result.is_top_surface.any()

    def test_bottom_face_not_top(self):
        """Downward-facing face should NOT be top surface."""
        verts, indices = _make_bottom_face()
        result = analyze_mesh(verts, indices)

        # Normal should point down (negative Z)
        assert result.face_normals[0, 2] < 0

        # Should not be top surface
        assert not result.is_top_surface.any()

    def test_non_indexed_mesh(self):
        """Non-indexed mesh (indices=None) should work."""
        verts = np.array([
            [0, 0, 0], [1, 0, 0], [1, 1, 1],  # face 0
            [0, 0, 5], [1, 0, 5], [1, 1, 5],  # face 1 (flat at z=5)
        ], dtype=np.float64)
        result = analyze_mesh(verts, None)

        assert result.face_normals.shape == (2, 3)
        assert result.face_areas.shape == (2,)

    def test_degenerate_triangle(self):
        """Degenerate (zero-area) triangle should be handled gracefully."""
        verts = np.array([
            [0, 0, 0], [0, 0, 0], [0, 0, 0],  # degenerate
            [0, 0, 1], [1, 0, 1], [1, 1, 1],  # valid
        ], dtype=np.float64)
        result = analyze_mesh(verts, None)

        # Degenerate face should have zero normal and not be top surface
        np.testing.assert_allclose(result.face_normals[0], [0, 0, 0])
        assert result.face_areas[0] < 1e-10
        assert not result.is_top_surface[0]

    def test_transform_matrix(self):
        """Transform matrix should be applied to vertices."""
        verts, indices = _make_flat_square(z=0.0)
        # Translation matrix: move up by 10
        transform = np.eye(4)
        transform[2, 3] = 10.0

        result = analyze_mesh(verts, indices, transform_matrix=transform)

        # Centers should be at z=10
        for i in range(2):
            assert abs(result.face_centers[i, 2] - 10.0) < 0.5

    def test_empty_mesh_raises(self):
        """Empty or wrong-shaped input should raise ValueError."""
        with pytest.raises(ValueError):
            analyze_mesh(np.array([]), None)

    def test_face_areas_positive(self):
        """Face areas should be non-negative."""
        verts, indices = _make_flat_square(z=0.0)
        result = analyze_mesh(verts, indices)
        assert (result.face_areas >= 0).all()
