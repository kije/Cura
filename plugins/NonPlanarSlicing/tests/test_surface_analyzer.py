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

    def test_sphere_has_top_surfaces(self):
        """A sphere in Z-up space should have top surfaces on upper hemisphere."""
        # Generate a UV sphere with ~200 faces
        n_lat, n_lon = 10, 20
        verts = []
        faces = []
        for i in range(n_lat + 1):
            theta = math.pi * i / n_lat  # 0 (north) to pi (south)
            for j in range(n_lon):
                phi = 2 * math.pi * j / n_lon
                x = 10.0 * math.sin(theta) * math.cos(phi)
                y = 10.0 * math.sin(theta) * math.sin(phi)
                z = 10.0 * math.cos(theta)
                verts.append([x, y, z])
        for i in range(n_lat):
            for j in range(n_lon):
                v0 = i * n_lon + j
                v1 = i * n_lon + (j + 1) % n_lon
                v2 = (i + 1) * n_lon + j
                v3 = (i + 1) * n_lon + (j + 1) % n_lon
                faces.append([v0, v1, v2])
                faces.append([v1, v3, v2])

        vertices = np.array(verts, dtype=np.float64)
        indices = np.array(faces, dtype=np.intp)

        result = analyze_mesh(vertices, indices)

        # Upper hemisphere faces should be top surfaces
        n_top = int(np.count_nonzero(result.is_top_surface))
        assert n_top > 0, "Sphere should have top surface faces"
        # Roughly half the faces should be top surfaces (upper hemisphere)
        assert n_top > len(faces) * 0.3, (
            f"Expected >30% top surfaces, got {n_top}/{len(faces)}"
        )

    def test_z_component_determines_top_surface(self):
        """analyze_mesh uses face_normals[:,2] (Z component) for top detection.

        A face with normal pointing in +Y but Z=0 should NOT be detected
        as top surface. Only faces with positive Z normal component count.
        This confirms that Y-up data must be converted to Z-up before calling
        analyze_mesh.
        """
        # Face in XZ plane at Y=5: normal points +Y (or -Y depending on winding)
        # Either way, the Z component of the normal is 0
        verts = np.array([
            [0, 5, 0], [1, 5, 0], [1, 5, 1],
            [0, 5, 0], [1, 5, 1], [0, 5, 1],
        ], dtype=np.float64)
        indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.intp)

        result = analyze_mesh(verts, indices)
        # Normal has no Z component — should not be detected as top surface
        for n in result.face_normals:
            assert abs(n[2]) < 1e-10, "Normal Z component should be ~0"
        assert not result.is_top_surface.any(), (
            "Face with normal in XZ plane should NOT be top surface"
        )
