# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the G-code parser module."""

import pytest
from gcode.gcode_parser import parse_gcode, reconstruct_gcode, GCodeMove, ParsedGCode


def _make_simple_gcode():
    """Create a minimal Cura-style G-code list for testing."""
    header = ";FLAVOR:Marlin\n;Layer height: 0.2\n;Generated with Cura\n"
    start = "G28\nM82\nG1 Z0.3 F3000\n"
    layer0 = (
        ";LAYER:0\n"
        ";TYPE:WALL-OUTER\n"
        "G1 X10.000 Y10.000 E1.00000 F1500\n"
        "G1 X20.000 Y10.000 E2.00000\n"
        "G1 X20.000 Y20.000 E3.00000\n"
        "G1 X10.000 Y20.000 E4.00000\n"
        ";TYPE:FILL\n"
        "G1 X15.000 Y15.000 E5.00000\n"
    )
    layer1 = (
        ";LAYER:1\n"
        "G0 X10.000 Y10.000 Z0.5\n"
        ";TYPE:WALL-OUTER\n"
        "G1 X20.000 Y10.000 E6.00000 F1500\n"
        "G1 X20.000 Y20.000 E7.00000\n"
    )
    return [header, start, layer0, layer1]


class TestParseGCode:
    """Tests for parse_gcode."""

    def test_basic_parsing(self):
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)

        assert isinstance(parsed, ParsedGCode)
        assert parsed.total_layers == 2
        assert parsed.layer_height == pytest.approx(0.2)
        assert parsed.is_relative_extrusion == False
        assert len(parsed.moves) > 0

    def test_layer_detection(self):
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)

        layer_numbers = set(m.layer_number for m in parsed.moves)
        assert 0 in layer_numbers
        assert 1 in layer_numbers

    def test_line_type_detection(self):
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)

        # Find moves in layer 0
        layer0_moves = [m for m in parsed.moves if m.layer_number == 0]
        # First moves should be WALL-OUTER
        wall_moves = [m for m in layer0_moves if m.line_type == "WALL-OUTER"]
        fill_moves = [m for m in layer0_moves if m.line_type == "FILL"]
        assert len(wall_moves) > 0
        assert len(fill_moves) > 0

    def test_absolute_position_tracking(self):
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)

        # Find the move to X20 Y10
        for move in parsed.moves:
            if move.x == pytest.approx(20.0) and move.y == pytest.approx(10.0) and move.layer_number == 0:
                assert move.abs_x == pytest.approx(20.0)
                assert move.abs_y == pytest.approx(10.0)
                break
        else:
            pytest.fail("Could not find expected move")

    def test_travel_vs_extrusion(self):
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)

        # G0 moves should be travel
        g0_moves = [m for m in parsed.moves if m.command == "G0"]
        for m in g0_moves:
            assert m.is_travel

        # G1 moves with increasing E should be extrusion
        extrusion_moves = [m for m in parsed.moves if m.is_extrusion]
        assert len(extrusion_moves) > 0

    def test_relative_extrusion_detection(self):
        gcode_list = [
            ";header\n",
            "G28\nM83\n",  # M83 = relative extrusion
            ";LAYER:0\n;TYPE:FILL\nG1 X10 Y10 E0.5 F1500\nG1 X20 Y10 E0.5\n",
        ]
        parsed = parse_gcode(gcode_list)
        assert parsed.is_relative_extrusion == True

    def test_feedrate_tracking(self):
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)

        # The first extrusion move in layer 0 sets F1500
        for move in parsed.moves:
            if move.f is not None and move.f == pytest.approx(1500.0):
                break
        else:
            pytest.fail("No move with F1500 found")

    def test_empty_gcode(self):
        parsed = parse_gcode([])
        assert parsed.total_layers == 0
        assert len(parsed.moves) == 0

    def test_chunk_tracking(self):
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)

        # Layer 0 moves should be in chunk 2
        layer0_moves = [m for m in parsed.moves if m.layer_number == 0]
        for m in layer0_moves:
            assert m.chunk_index == 2


class TestReconstructGCode:
    """Tests for reconstruct_gcode."""

    def test_unmodified_reconstruction(self):
        """Reconstructing without changes should preserve the original."""
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)
        result = reconstruct_gcode(parsed, parsed.moves)

        assert len(result) == len(gcode_list)
        # Comments and non-move lines should be preserved
        assert ";FLAVOR:Marlin" in result[0]
        assert ";LAYER:0" in result[2]

    def test_modified_z_reconstruction(self):
        """Modifying a move's Z should be reflected in output."""
        gcode_list = _make_simple_gcode()
        parsed = parse_gcode(gcode_list)

        # Modify the first extrusion move's Z
        modified = list(parsed.moves)
        for i, m in enumerate(modified):
            if m.is_extrusion and m.layer_number == 0:
                modified[i] = GCodeMove(
                    command=m.command, x=m.x, y=m.y, z=5.0,
                    e=m.e, f=m.f, abs_x=m.abs_x, abs_y=m.abs_y,
                    abs_z=5.0, abs_e=m.abs_e, line_type=m.line_type,
                    layer_number=m.layer_number, original_line=m.original_line,
                    chunk_index=m.chunk_index, line_index_in_chunk=m.line_index_in_chunk,
                    is_travel=m.is_travel, is_extrusion=m.is_extrusion,
                )
                break

        result = reconstruct_gcode(parsed, modified)
        # Check that Z5.000 appears in the output
        all_gcode = "\n".join(result)
        assert "Z5.000" in all_gcode
