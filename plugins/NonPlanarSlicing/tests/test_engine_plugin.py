# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the CuraEngine plugin architecture.

Tests the deformation field serialization (Python → binary → Python roundtrip),
the inverse Z transform logic, and the engine prototype's path modification.
"""

import math
import struct

import numpy as np
import pytest

from analysis.deformation_field import DeformationField


# ---------------------------------------------------------------------------
# Deformation Field Serialization
# ---------------------------------------------------------------------------

NPDF_MAGIC = b"NPDF"
NPDF_VERSION = 1


def serialize_deformation_field(field: DeformationField) -> bytes:
    """Serialize a DeformationField to NPDF binary format.

    This mirrors the serialization in NonPlanarSlicingExtension._serialize_deformation_field().
    """
    rows, cols = field.grid_shape
    header = struct.pack(
        "<4sHIII5d",
        NPDF_MAGIC,
        NPDF_VERSION,
        field.num_layers,
        rows,
        cols,
        field.x_min,
        field.x_max,
        field.y_min,
        field.y_max,
        field.resolution,
    )
    z_levels = field.z_levels.astype(np.float32).tobytes()
    displacements = field.displacements.astype(np.float32).tobytes()
    return header + z_levels + displacements


def deserialize_deformation_field_header(data: bytes):
    """Parse NPDF header. Returns (num_layers, rows, cols, x_min, x_max, y_min, y_max, resolution)."""
    assert data[:4] == NPDF_MAGIC
    version = struct.unpack_from("<H", data, 4)[0]
    assert version == NPDF_VERSION
    num_layers, rows, cols = struct.unpack_from("<III", data, 6)
    x_min, x_max, y_min, y_max, resolution = struct.unpack_from("<5d", data, 18)
    return num_layers, rows, cols, x_min, x_max, y_min, y_max, resolution


class TestDeformationFieldSerialization:
    """Test roundtrip serialization of DeformationField to NPDF binary format."""

    def _make_simple_field(self) -> DeformationField:
        """Create a simple test deformation field."""
        num_layers = 5
        rows, cols = 4, 6
        z_levels = np.linspace(0.2, 1.0, num_layers)
        displacements = np.random.default_rng(42).uniform(
            -0.5, 0.5, (num_layers, rows, cols)
        )
        return DeformationField(
            x_min=-10.0,
            x_max=10.0,
            y_min=-8.0,
            y_max=8.0,
            resolution=2.0,
            z_levels=z_levels,
            displacements=displacements,
        )

    def test_serialize_header(self):
        field = self._make_simple_field()
        data = serialize_deformation_field(field)

        # Check magic and version
        assert data[:4] == NPDF_MAGIC
        assert struct.unpack_from("<H", data, 4)[0] == 1

        # Check dimensions
        nl, rows, cols, x_min, x_max, y_min, y_max, res = deserialize_deformation_field_header(data)
        assert nl == 5
        assert rows == 4
        assert cols == 6
        assert x_min == pytest.approx(-10.0)
        assert x_max == pytest.approx(10.0)
        assert y_min == pytest.approx(-8.0)
        assert y_max == pytest.approx(8.0)
        assert res == pytest.approx(2.0)

    def test_serialize_data_size(self):
        field = self._make_simple_field()
        data = serialize_deformation_field(field)

        header_size = 58
        z_size = 5 * 4  # 5 layers * f32
        disp_size = 5 * 4 * 6 * 4  # 5 layers * 4 rows * 6 cols * f32
        expected = header_size + z_size + disp_size
        assert len(data) == expected

    def test_roundtrip_z_levels(self):
        field = self._make_simple_field()
        data = serialize_deformation_field(field)

        # Deserialize z_levels
        header_size = 58
        z_levels = np.frombuffer(data[header_size:header_size + 5 * 4], dtype=np.float32)
        np.testing.assert_allclose(z_levels, field.z_levels.astype(np.float32), rtol=1e-6)

    def test_roundtrip_displacements(self):
        field = self._make_simple_field()
        data = serialize_deformation_field(field)

        header_size = 58
        z_size = 5 * 4
        disp_offset = header_size + z_size
        displacements = np.frombuffer(
            data[disp_offset:], dtype=np.float32
        ).reshape(5, 4, 6)
        np.testing.assert_allclose(
            displacements, field.displacements.astype(np.float32), rtol=1e-6
        )


class TestInverseZTransform:
    """Test the inverse Z transform logic used by the engine plugin."""

    def _make_flat_field(self) -> DeformationField:
        """Create a field with known displacement for testing inverse."""
        num_layers = 10
        rows, cols = 10, 10
        z_levels = np.linspace(0.2, 2.0, num_layers)
        # Uniform displacement of 0.1mm everywhere
        displacements = np.full((num_layers, rows, cols), 0.1)
        return DeformationField(
            x_min=0.0, x_max=9.0,
            y_min=0.0, y_max=9.0,
            resolution=1.0,
            z_levels=z_levels,
            displacements=displacements,
        )

    def test_inverse_uniform_displacement(self):
        """With uniform displacement, inverse should subtract it."""
        from analysis.mesh_deformer import inverse_deform_z
        field = self._make_flat_field()

        # z_deformed = z_orig + 0.1
        # So if z_deformed = 1.1, z_orig should be ≈ 1.0
        z_orig = inverse_deform_z(5.0, 5.0, 1.1, field)
        assert z_orig == pytest.approx(1.0, abs=0.001)

    def test_inverse_at_boundary(self):
        """Test inverse transform outside the field bounds returns z_deformed."""
        from analysis.mesh_deformer import inverse_deform_z
        field = self._make_flat_field()

        # Outside bounds: displacement should be 0, so z_orig ≈ z_deformed
        z_orig = inverse_deform_z(100.0, 100.0, 1.0, field)
        assert z_orig == pytest.approx(1.0, abs=0.001)

    def test_forward_inverse_roundtrip(self):
        """Forward deformation followed by inverse should recover original Z."""
        from analysis.mesh_deformer import inverse_deform_z
        field = self._make_flat_field()

        z_original = 1.0
        x, y = 5.0, 5.0

        # Forward: z_deformed = z_original + interpolate(x, y, z_original)
        disp = field.interpolate(x, y, z_original)
        z_deformed = z_original + disp

        # Inverse: should recover z_original
        z_recovered = inverse_deform_z(x, y, z_deformed, field)
        assert z_recovered == pytest.approx(z_original, abs=0.001)


class TestPrototypeDeformationField:
    """Test the Python prototype's DeformationField deserialization."""

    def test_prototype_deserialize(self):
        """Test that engine_prototype.py can deserialize NPDF format."""
        import sys
        import os
        # Add the plugin root to path so we can import engine_prototype
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, plugin_root)

        from engine_prototype import DeformationField as ProtoField

        # Create a field, serialize it, deserialize with prototype code
        from analysis.deformation_field import DeformationField as AnalysisField
        num_layers = 3
        rows, cols = 4, 5
        z_levels = np.array([0.2, 0.4, 0.6])
        displacements = np.ones((num_layers, rows, cols), dtype=np.float64) * 0.05

        field = AnalysisField(
            x_min=0.0, x_max=4.0,
            y_min=0.0, y_max=3.0,
            resolution=1.0,
            z_levels=z_levels,
            displacements=displacements,
        )

        data = serialize_deformation_field(field)
        proto_field = ProtoField.from_bytes(data)

        assert proto_field.num_layers == 3
        assert proto_field.rows == 4
        assert proto_field.cols == 5
        assert proto_field.x_min == pytest.approx(0.0)
        assert proto_field.resolution == pytest.approx(1.0)

        # Check interpolation matches
        for x in [1.0, 2.5]:
            for y in [1.0, 2.0]:
                for z in [0.2, 0.4]:
                    orig_val = field.interpolate(x, y, z)
                    proto_val = proto_field.interpolate(x, y, z)
                    assert proto_val == pytest.approx(orig_val, abs=0.01), \
                        f"Mismatch at ({x}, {y}, {z}): {proto_val} vs {orig_val}"
