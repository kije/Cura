"""Tests for the mesh deformer module (Phase II).

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import numpy as np
import pytest

from analysis.deformation_field import DeformationField
from analysis.mesh_deformer import (
    deform_mesh_vertices,
    inverse_deform_z,
    inverse_deform_gcode_line,
    validate_deformation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_field(num_layers=5, rows=3, cols=3, layer_height=0.2,
                displacement_value=0.0):
    z_levels = np.array([0.2 + i * layer_height for i in range(num_layers)])
    displacements = np.full(
        (num_layers, rows, cols), displacement_value, dtype=np.float64,
    )
    return DeformationField(
        x_min=0.0, x_max=(cols - 1) * 1.0,
        y_min=0.0, y_max=(rows - 1) * 1.0,
        resolution=1.0,
        z_levels=z_levels,
        displacements=displacements,
    )


# ---------------------------------------------------------------------------
# Tests: deform_mesh_vertices
# ---------------------------------------------------------------------------

class TestDeformMeshVertices:

    def test_zero_displacement_unchanged(self):
        field = _make_field(displacement_value=0.0)
        verts = np.array([
            [1.0, 1.0, 0.4],
            [0.5, 0.5, 0.6],
        ], dtype=np.float64)
        result = deform_mesh_vertices(verts, field, z_up=True)
        np.testing.assert_array_almost_equal(result, verts)

    def test_uniform_displacement_z_up(self):
        field = _make_field(displacement_value=0.1)
        verts = np.array([[1.0, 1.0, 0.4]], dtype=np.float64)
        result = deform_mesh_vertices(verts, field, z_up=True)
        assert result[0, 2] == pytest.approx(0.5, abs=0.01)

    def test_original_not_modified(self):
        field = _make_field(displacement_value=0.1)
        verts = np.array([[1.0, 1.0, 0.4]], dtype=np.float64)
        original = verts.copy()
        deform_mesh_vertices(verts, field, z_up=True)
        np.testing.assert_array_equal(verts, original)

    def test_scene_coordinates(self):
        """In scene coords, height is column 1, depth is -column 2."""
        field = _make_field(displacement_value=0.1)
        # Scene coords: (x, y_scene=height, z_scene=-depth)
        # Slicing: x=x, y=-z_scene, z=y_scene
        # For point at slicing (1.0, 1.0, 0.4):
        #   scene = (1.0, 0.4, -1.0)
        verts = np.array([[1.0, 0.4, -1.0]], dtype=np.float64)
        result = deform_mesh_vertices(verts, field, z_up=False)
        # Height (col 1) should increase by ~0.1.
        assert result[0, 1] == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: inverse_deform_z
# ---------------------------------------------------------------------------

class TestInverseDeformZ:

    def test_zero_displacement_identity(self):
        field = _make_field(displacement_value=0.0)
        orig = inverse_deform_z(1.0, 1.0, 0.4, field)
        assert orig == pytest.approx(0.4, abs=0.001)

    def test_uniform_displacement_inverse(self):
        field = _make_field(displacement_value=0.1)
        # Forward: z=0.4 → deformed=0.5
        # Inverse: deformed=0.5 → should get back ~0.4
        orig = inverse_deform_z(1.0, 1.0, 0.5, field)
        assert orig == pytest.approx(0.4, abs=0.01)

    def test_roundtrip(self):
        """Forward then inverse should return original Z."""
        field = _make_field(num_layers=10, displacement_value=0.0)
        # Set varying displacement.
        for i in range(10):
            field.displacements[i, :, :] = 0.02 * i
        original_z = 0.6
        deformed_z = field.get_target_z(1.0, 1.0, original_z)
        recovered_z = inverse_deform_z(1.0, 1.0, deformed_z, field)
        assert recovered_z == pytest.approx(original_z, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: inverse_deform_gcode_line
# ---------------------------------------------------------------------------

class TestInverseDeformGcodeLine:

    def test_non_move_unchanged(self):
        field = _make_field(displacement_value=0.1)
        line = ";This is a comment"
        result, x, y, z = inverse_deform_gcode_line(
            line, field, 0.0, 0.0, 0.0,
        )
        assert result == line
        assert z == 0.0

    def test_move_without_z_unchanged(self):
        field = _make_field(displacement_value=0.1)
        line = "G1 X10.0 Y10.0 E1.5"
        result, x, y, z = inverse_deform_gcode_line(
            line, field, 0.0, 0.0, 0.0,
        )
        assert "Z" not in result
        assert x == pytest.approx(10.0)
        assert y == pytest.approx(10.0)

    def test_move_with_z_transformed(self):
        field = _make_field(displacement_value=0.0)
        line = "G1 X1.0 Y1.0 Z0.4 E1.0"
        result, x, y, z = inverse_deform_gcode_line(
            line, field, 0.0, 0.0, 0.0,
        )
        # With zero displacement, Z should be unchanged.
        assert "Z0.4000" in result
        assert z == pytest.approx(0.4, abs=0.001)

    def test_gcode_offset_applied(self):
        field = _make_field(displacement_value=0.0)
        line = "G1 X101.0 Y101.0 Z0.4 E1.0"
        result, x, y, z = inverse_deform_gcode_line(
            line, field, 0.0, 0.0, 0.0,
            gcode_offset_x=100.0, gcode_offset_y=100.0,
        )
        # After offset, analysis coords are (1.0, 1.0) which is in bounds.
        assert "Z0.4000" in result


# ---------------------------------------------------------------------------
# Tests: validate_deformation
# ---------------------------------------------------------------------------

class TestValidateDeformation:

    def test_zero_displacement_valid(self):
        field = _make_field(displacement_value=0.0)
        verts = np.array([[1.0, 1.0, 0.4]], dtype=np.float64)
        assert validate_deformation(verts, field) is True

    def test_moderate_displacement_valid(self):
        field = _make_field(num_layers=5, displacement_value=0.0)
        # Increasing displacement preserves layer ordering.
        for i in range(5):
            field.displacements[i, :, :] = 0.01 * i
        verts = np.array([[1.0, 1.0, 0.4]], dtype=np.float64)
        assert validate_deformation(verts, field) is True

    def test_inverted_layers_invalid(self):
        field = _make_field(num_layers=3, displacement_value=0.0)
        # Make layer 1 go above layer 2 → inversion.
        field.displacements[1, :, :] = 0.3   # z=0.4 → 0.7
        field.displacements[2, :, :] = -0.3  # z=0.6 → 0.3
        verts = np.array([[1.0, 1.0, 0.4]], dtype=np.float64)
        assert validate_deformation(verts, field) is False

    def test_empty_vertices_valid(self):
        field = _make_field()
        verts = np.empty((0, 3), dtype=np.float64)
        assert validate_deformation(verts, field) is True
