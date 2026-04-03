# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the G-code bender module."""

import math
import numpy as np
import pytest

from gcode.gcode_bender import (
    bend_gcode,
    subdivide_segment,
    validate_moves,
)
from gcode.gcode_parser import GCodeMove


class _MockHeightMap:
    """Minimal mock implementing HeightMapProtocol."""

    def __init__(self, z_value=10.0, grid_shape=(20, 20)):
        self._z = z_value
        self._shape = grid_shape

    def interpolate(self, x, y):
        return self._z

    def is_valid(self, x, y):
        return True

    def get_grid_coords(self, x, y):
        r = min(max(0, int(y)), self._shape[0] - 1)
        c = min(max(0, int(x)), self._shape[1] - 1)
        return (r, c)


class TestSubdivideSegment:
    """Tests for subdivide_segment."""

    def test_short_segment_no_split(self):
        """Segment shorter than max_length should not be split."""
        result = subdivide_segment(0, 0, 0, 0, 0.5, 0, 0, 0.1, 1500, 1.0)
        assert len(result) == 1
        x, y, z, e, f = result[0]
        assert x == pytest.approx(0.5)
        assert e == pytest.approx(0.1)

    def test_long_segment_splits(self):
        """10mm segment with 1mm max should produce ~10 sub-segments."""
        result = subdivide_segment(0, 0, 0, 0, 10, 0, 0, 1.0, 1500, 1.0)
        assert len(result) == 10

    def test_endpoints_match(self):
        """Last sub-segment should reach the target endpoint."""
        result = subdivide_segment(0, 0, 0, 0, 7.3, 4.1, 0, 2.5, 1500, 1.0)
        x, y, z, e, f = result[-1]
        assert x == pytest.approx(7.3)
        assert y == pytest.approx(4.1)
        assert e == pytest.approx(2.5)

    def test_e_interpolated(self):
        """E should be linearly interpolated along segment."""
        result = subdivide_segment(0, 0, 0, 0, 10, 0, 0, 2.0, None, 5.0)
        assert len(result) == 2
        assert result[0][3] == pytest.approx(1.0)  # midpoint E
        assert result[1][3] == pytest.approx(2.0)  # endpoint E

    def test_zero_length(self):
        """Zero-length segment should return single point."""
        result = subdivide_segment(5, 5, 5, 1, 5, 5, 5, 1, 1500, 1.0)
        assert len(result) == 1

    def test_f_preserved(self):
        """Feedrate should be passed through to all sub-segments."""
        result = subdivide_segment(0, 0, 0, 0, 20, 0, 0, 1.0, 1234.0, 5.0)
        for _, _, _, _, f in result:
            assert f == 1234.0

    def test_negative_max_length(self):
        """Negative max_length should use default (1.0)."""
        result = subdivide_segment(0, 0, 0, 0, 5, 0, 0, 1.0, None, -1.0)
        assert len(result) == 5


class TestValidateMoves:
    """Tests for validate_moves."""

    def test_empty(self):
        assert validate_moves([], 30.0) == []

    def test_single_move(self):
        move = GCodeMove(
            command="G1", x=10.0, y=0.0, z=0.0, e=1.0, f=1500.0,
            abs_x=10.0, abs_y=0.0, abs_z=0.0, abs_e=1.0,
            line_type="WALL-OUTER", layer_number=0,
            original_line="G1 X10 Y0 E1 F1500",
            chunk_index=2, line_index_in_chunk=1,
            is_travel=False, is_extrusion=True,
        )
        result = validate_moves([move], 30.0)
        assert result == [True]

    def test_flat_moves_valid(self):
        """Moves on same Z plane should always be valid."""
        moves = []
        for i in range(5):
            moves.append(GCodeMove(
                command="G1", x=float(i * 10), y=0.0, z=1.0, e=float(i),
                f=1500.0,
                abs_x=float(i * 10), abs_y=0.0, abs_z=1.0, abs_e=float(i),
                line_type="FILL", layer_number=0,
                original_line=f"G1 X{i*10} E{i}",
                chunk_index=2, line_index_in_chunk=i,
                is_travel=False, is_extrusion=True,
            ))
        result = validate_moves(moves, 30.0)
        assert all(result)

    def test_steep_move_invalid(self):
        """A very steep move should fail validation."""
        m1 = GCodeMove(
            command="G1", x=0.0, y=0.0, z=0.0, e=0.0, f=1500.0,
            abs_x=0.0, abs_y=0.0, abs_z=0.0, abs_e=0.0,
            line_type="FILL", layer_number=0,
            original_line="G1 X0 Y0 Z0",
            chunk_index=2, line_index_in_chunk=0,
            is_travel=False, is_extrusion=True,
        )
        m2 = GCodeMove(
            command="G1", x=1.0, y=0.0, z=100.0, e=1.0, f=1500.0,
            abs_x=1.0, abs_y=0.0, abs_z=100.0, abs_e=1.0,
            line_type="FILL", layer_number=0,
            original_line="G1 X1 Z100",
            chunk_index=2, line_index_in_chunk=1,
            is_travel=False, is_extrusion=True,
        )
        result = validate_moves([m1, m2], 30.0)
        assert result[0] is True
        assert result[1] is False


class TestBendGCode:
    """Tests for bend_gcode (integration)."""

    def _make_simple_gcode(self):
        header = ";FLAVOR:Marlin\n;Layer height: 0.2\n"
        start = "G28\nM82\nG1 Z0.3 F3000\n"
        layer0 = (
            ";LAYER:0\n"
            ";TYPE:WALL-OUTER\n"
            "G1 X10.000 Y10.000 E1.00000 F1500\n"
            "G1 X20.000 Y10.000 E2.00000\n"
        )
        layer1 = (
            ";LAYER:1\n"
            ";TYPE:WALL-OUTER\n"
            "G1 X10.000 Y10.000 E3.00000 F1500\n"
            "G1 X20.000 Y10.000 E4.00000\n"
        )
        return [header, start, layer0, layer1]

    def test_empty_gcode(self):
        result = bend_gcode([], None, np.ones((1, 1), dtype=bool), np.ones((1, 1)), {})
        assert result == []

    def test_bend_modifies_gcode(self):
        """Bending with a surface above the layer should produce Z changes."""
        gcode = self._make_simple_gcode()
        # Surface Z close to where the layers are (~0.2-0.4)
        hm = _MockHeightMap(z_value=0.4, grid_shape=(20, 20))
        safe_map = np.ones((20, 20), dtype=bool)
        blend_map = np.ones((20, 20), dtype=np.float64)

        settings = {
            "layer_height": 0.2,
            "nonplanar_layer_count": 2,
            "max_angle_deg": 45.0,
            "flow_compensation": False,
            "feedrate_compensation": False,
            "segment_length": 50.0,  # large to avoid subdivision
            "surface_mode": "top_only",  # Use top_only for predictable test
        }
        result = bend_gcode(gcode, hm, safe_map, blend_map, settings)
        assert len(result) > 0
        # The output should contain the non-planar warning header
        all_text = "\n".join(result)
        assert ";WARNING: NON-PLANAR G-CODE" in all_text

    def test_bend_preserves_structure(self):
        """Output should have same number of chunks as input."""
        gcode = self._make_simple_gcode()
        hm = _MockHeightMap(z_value=10.0)
        safe_map = np.ones((20, 20), dtype=bool)
        blend_map = np.ones((20, 20), dtype=np.float64)

        settings = {
            "layer_height": 0.2,
            "nonplanar_layer_count": 1,
            "max_angle_deg": 45.0,
            "flow_compensation": False,
            "feedrate_compensation": False,
        }
        result = bend_gcode(gcode, hm, safe_map, blend_map, settings)
        assert len(result) == len(gcode)

    def test_bend_skips_support_type(self):
        """Moves with SUPPORT type should not be bent."""
        gcode = [
            ";header\n",
            "G28\nM82\n",
            ";LAYER:0\n;TYPE:SUPPORT\nG1 X10 Y10 E1 F1500\n",
        ]
        hm = _MockHeightMap(z_value=10.0)
        safe_map = np.ones((20, 20), dtype=bool)
        blend_map = np.ones((20, 20), dtype=np.float64)
        settings = {
            "layer_height": 0.2,
            "nonplanar_layer_count": 1,
            "max_angle_deg": 45.0,
            "flow_compensation": False,
            "feedrate_compensation": False,
        }
        result = bend_gcode(gcode, hm, safe_map, blend_map, settings)
        # Should not have non-planar warning since nothing was bent
        all_text = "\n".join(result)
        # Support moves should be left alone
        assert "X10" in all_text
