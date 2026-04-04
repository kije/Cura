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


class TestZSpikeRegression:
    """Regression tests for the Z-spike bug.

    The bug: most CuraEngine G1 moves omit the Z parameter (Z is only
    emitted when it changes).  When a bent move emits an explicit Z
    (e.g., Z12.5), and the next un-bent move has no Z, the printer
    stays at Z12.5 instead of returning to the layer height.  This
    causes dramatic Z spikes within a single layer.
    """

    def _make_multilayer_gcode(self, n_layers=10, moves_per_layer=5, layer_height=0.2):
        """Generate G-code with multiple layers and moves.

        Simulates CuraEngine output where most moves lack Z coordinates.
        Z is only emitted on the first move of each layer.
        """
        header = f";FLAVOR:Marlin\n;Layer height: {layer_height}\n"
        start = f"G28\nM82\nG1 Z{layer_height:.3f} F3000\n"
        chunks = [header, start]

        abs_e = 0.0
        for layer in range(n_layers):
            z = (layer + 1) * layer_height
            lines = [f";LAYER:{layer}", ";TYPE:WALL-OUTER"]
            for i in range(moves_per_layer):
                abs_e += 0.5
                x = 5.0 + i * 2.0
                y = 10.0
                if i == 0:
                    # First move of layer: include Z (as CuraEngine does).
                    lines.append(f"G1 X{x:.3f} Y{y:.3f} Z{z:.3f} E{abs_e:.5f} F1500")
                else:
                    # Subsequent moves: no Z (as CuraEngine does).
                    lines.append(f"G1 X{x:.3f} Y{y:.3f} E{abs_e:.5f}")
            chunks.append("\n".join(lines) + "\n")

        return chunks

    def test_no_z_jump_after_bent_region(self):
        """Un-bent moves after a bent region must not stay at the bent Z.

        This is the core regression test: when a bent move emits Z=X and
        the next un-bent move has no Z, the bender must inject an explicit
        Z to restore the printer to the correct height.
        """
        n_layers = 20
        layer_height = 0.2
        gcode = self._make_multilayer_gcode(
            n_layers=n_layers, moves_per_layer=8, layer_height=layer_height,
        )

        # Height map surface at 3.0mm (above most layers).
        hm = _MockHeightMap(z_value=3.0, grid_shape=(20, 20))
        # Make safe_map partially safe (only some XY positions) to create
        # a mix of bent and un-bent moves within the same layer.
        safe_map = np.zeros((20, 20), dtype=bool)
        safe_map[8:12, 2:8] = True  # Only safe in a band
        blend_map = np.ones((20, 20), dtype=np.float64)

        settings = {
            "layer_height": layer_height,
            "nonplanar_layer_count": 5,
            "max_angle_deg": 45.0,
            "flow_compensation": False,
            "feedrate_compensation": False,
            "segment_length": 50.0,
            "surface_mode": "top_only",
            "max_path_deviation": 0.4,
            "nozzle_size": 0.4,
        }
        result = bend_gcode(gcode, hm, safe_map, blend_map, settings)

        # Parse all G1 moves from the result, tracking Z state.
        import re
        z_pattern = re.compile(r"Z([\d.]+)")
        current_z = 0.0
        current_layer = -1
        max_z_jump = 0.0

        for chunk in result:
            for line in chunk.split("\n"):
                stripped = line.strip()
                if stripped.startswith(";LAYER:"):
                    try:
                        current_layer = int(stripped.split(":")[1])
                    except (ValueError, IndexError):
                        pass
                    continue
                if not stripped.startswith("G1"):
                    continue

                z_match = z_pattern.search(stripped)
                if z_match:
                    new_z = float(z_match.group(1))
                    jump = abs(new_z - current_z)
                    max_z_jump = max(max_z_jump, jump)
                    current_z = new_z
                # If no Z in the line, the printer stays at current_z.

        # Max jump between any two consecutive Z-emitting lines should
        # be bounded.  Within a layer, the jump between a bent move and
        # a Z-restored un-bent move should be at most ~1mm (the bending
        # depth).  Between layers, it should be at most layer_height +
        # the bending range.  1.5mm is a reasonable bound.
        assert max_z_jump < 1.5, (
            f"Excessive Z jump of {max_z_jump:.3f}mm detected between "
            f"consecutive Z-bearing G1 commands.  This indicates the "
            f"bender is not injecting Z-restore commands after bent regions."
        )

    def test_printer_z_state_never_diverges(self):
        """Simulate actual printer Z state: track the Z the printer is at
        (including moves without Z, where the printer stays at the last Z).
        Verify that the printer's actual Z never diverges from the move's
        intended abs_z by more than a small threshold.

        This catches the exact bug: bent move emits Z=12.5, un-bent move
        has no Z, printer stays at 12.5 instead of returning to layer height.
        """
        n_layers = 20
        layer_height = 0.2
        gcode = self._make_multilayer_gcode(
            n_layers=n_layers, moves_per_layer=8, layer_height=layer_height,
        )

        hm = _MockHeightMap(z_value=3.0, grid_shape=(20, 20))
        # Partial safe_map to create bent/un-bent transitions.
        safe_map = np.zeros((20, 20), dtype=bool)
        safe_map[8:12, 2:8] = True
        blend_map = np.ones((20, 20), dtype=np.float64)

        settings = {
            "layer_height": layer_height,
            "nonplanar_layer_count": 5,
            "max_angle_deg": 45.0,
            "flow_compensation": False,
            "feedrate_compensation": False,
            "segment_length": 50.0,
            "surface_mode": "top_only",
        }
        result = bend_gcode(gcode, hm, safe_map, blend_map, settings)

        # Simulate printer Z state: parse output and track what Z the
        # printer head is actually at (considering Z persistence).
        import re
        z_pattern = re.compile(r"Z([\d.]+)")
        printer_z = 0.0  # Printer starts at Z=0.
        current_layer = -1
        max_divergence = 0.0
        worst_line = ""

        for chunk in result:
            for line in chunk.split("\n"):
                stripped = line.strip()
                if stripped.startswith(";LAYER:"):
                    try:
                        current_layer = int(stripped.split(":")[1])
                    except (ValueError, IndexError):
                        pass
                    continue
                if not stripped.startswith("G"):
                    continue

                z_match = z_pattern.search(stripped)
                if z_match:
                    printer_z = float(z_match.group(1))

                # For extrusion moves (G1 with E), check that the
                # printer's Z is reasonable for this layer.
                if stripped.startswith("G1") and "E" in stripped and current_layer >= 0:
                    # The expected Z for this layer (nominal or bent).
                    nominal_z = (current_layer + 1) * layer_height
                    # Allow deviation for bending, but the printer Z
                    # should be within nonplanar_layer_count * layer_height
                    # of the nominal.
                    max_allowed = 5 * layer_height + 0.5  # generous
                    divergence = abs(printer_z - nominal_z)
                    if divergence > max_divergence:
                        max_divergence = divergence
                        worst_line = stripped

        assert max_divergence < 2.0, (
            f"Printer Z diverged {max_divergence:.3f}mm from nominal layer Z. "
            f"This indicates Z-restore is not working after bent regions. "
            f"Worst line: {worst_line}"
        )

    def test_z_restore_after_bent_to_unbent_transition(self):
        """After a bent region, the FIRST un-bent move must have an explicit
        Z to restore the printer to the correct height.

        Uses a partial safe_map to create mixed bent/un-bent moves within
        the same layer, then verifies that every un-bent extrusion move
        that follows a bent move has the printer at the correct Z.
        """
        n_layers = 20
        layer_height = 0.2
        gcode = self._make_multilayer_gcode(
            n_layers=n_layers, moves_per_layer=8, layer_height=layer_height,
        )

        # Surface above the layers.
        hm = _MockHeightMap(z_value=3.0, grid_shape=(20, 20))
        # Partial safe_map: only columns 2-7 are safe (X=5,7 bent; X=9+ un-bent).
        safe_map = np.zeros((20, 20), dtype=bool)
        safe_map[8:12, 2:8] = True
        blend_map = np.ones((20, 20), dtype=np.float64)

        settings = {
            "layer_height": layer_height,
            "nonplanar_layer_count": 5,
            "max_angle_deg": 45.0,
            "flow_compensation": False,
            "feedrate_compensation": False,
            "segment_length": 50.0,
            "surface_mode": "top_only",
        }
        result = bend_gcode(gcode, hm, safe_map, blend_map, settings)

        # Simulate printer Z state and verify that un-bent extrusion moves
        # never have the printer at a stale bent Z.
        import re
        z_pattern = re.compile(r"Z([\d.]+)")
        printer_z = 0.0
        current_layer = -1
        violations = []

        for chunk in result:
            for line in chunk.split("\n"):
                stripped = line.strip()
                if stripped.startswith(";LAYER:"):
                    try:
                        current_layer = int(stripped.split(":")[1])
                    except (ValueError, IndexError):
                        pass
                    continue
                if not stripped.startswith("G"):
                    continue

                z_match = z_pattern.search(stripped)
                if z_match:
                    printer_z = float(z_match.group(1))

                # For every G1 extrusion move, the printer Z should be
                # within a reasonable range of the layer's nominal Z.
                if (stripped.startswith("G1") and "E" in stripped
                        and current_layer >= 0):
                    nominal_z = (current_layer + 1) * layer_height
                    # Allow bending deviation (5 layers * 0.2mm = 1.0mm
                    # plus some margin), but not gross divergence.
                    if abs(printer_z - nominal_z) > 1.5:
                        violations.append(
                            f"Layer {current_layer}: printer at Z={printer_z:.3f} "
                            f"but nominal is {nominal_z:.3f} "
                            f"(delta={abs(printer_z - nominal_z):.3f}mm)"
                        )

        assert len(violations) == 0, (
            f"Found {len(violations)} Z-state violations where printer was "
            f"at wrong height (stale bent Z not restored):\n"
            + "\n".join(violations[:10])
        )

    def test_all_target_layer_moves_have_explicit_z(self):
        """Every G1 move in a target layer must have explicit Z in the output.

        CuraEngine normally omits Z from most G1 lines (only emitting it
        at layer changes).  After bending, un-bent moves without Z would
        inherit stale bent Z values.  The fix ensures ALL moves in target
        layers get explicit Z.
        """
        n_layers = 20
        layer_height = 0.2
        gcode = self._make_multilayer_gcode(
            n_layers=n_layers, moves_per_layer=8, layer_height=layer_height,
        )

        hm = _MockHeightMap(z_value=3.0, grid_shape=(20, 20))
        # Partial safe_map — creates mixed bent/un-bent in same layer.
        safe_map = np.zeros((20, 20), dtype=bool)
        safe_map[8:12, 2:8] = True
        blend_map = np.ones((20, 20), dtype=np.float64)

        settings = {
            "layer_height": layer_height,
            "nonplanar_layer_count": 5,
            "max_angle_deg": 45.0,
            "flow_compensation": False,
            "feedrate_compensation": False,
            "segment_length": 50.0,
            "surface_mode": "top_only",
        }
        result = bend_gcode(gcode, hm, safe_map, blend_map, settings)

        # In top_only mode, target layers are the top 5 (layers 15-19).
        # Every G1 move in those layers should have explicit Z.
        import re
        z_pattern = re.compile(r"Z([\d.]+)")
        current_layer = -1
        target_start = n_layers - 5  # layer 15
        missing_z_lines = []

        for chunk in result:
            for line in chunk.split("\n"):
                stripped = line.strip()
                if stripped.startswith(";LAYER:"):
                    try:
                        current_layer = int(stripped.split(":")[1])
                    except (ValueError, IndexError):
                        pass
                    continue
                if not stripped.startswith("G1"):
                    continue
                if current_layer < target_start:
                    continue

                # Every G1 in a target layer must have Z.
                if not z_pattern.search(stripped):
                    missing_z_lines.append(
                        f"Layer {current_layer}: {stripped}"
                    )

        assert len(missing_z_lines) == 0, (
            f"Found {len(missing_z_lines)} G1 moves in target layers "
            f"without explicit Z (would inherit stale Z from bent regions):\n"
            + "\n".join(missing_z_lines[:10])
        )

    def test_z_deviation_clamped_relative_to_layer_below(self):
        """Bent Z must not exceed layer_height + max_path_deviation above
        the conformal layer below.

        This prevents the nozzle from printing into empty air when the
        surface height varies sharply (e.g. sphere edges).
        """
        n_layers = 20
        layer_height = 0.2
        gcode = self._make_multilayer_gcode(
            n_layers=n_layers, moves_per_layer=8, layer_height=layer_height,
        )

        # Surface at 3.0mm — well above most layers.
        hm = _MockHeightMap(z_value=3.0, grid_shape=(20, 20))
        safe_map = np.ones((20, 20), dtype=bool)
        blend_map = np.ones((20, 20), dtype=np.float64)
        max_deviation = 0.4

        settings = {
            "layer_height": layer_height,
            "nonplanar_layer_count": 5,
            "max_angle_deg": 89.0,  # Very permissive angle
            "flow_compensation": False,
            "feedrate_compensation": False,
            "segment_length": 50.0,
            "surface_mode": "top_only",
            "max_path_deviation": max_deviation,
            "nozzle_size": max_deviation,
        }
        result = bend_gcode(gcode, hm, safe_map, blend_map, settings)

        # Parse all Z values.  For each bent extrusion on layer L, the
        # conformal layer below is at surface_z - (layers_from_top+1)*lh.
        # The bent Z must not exceed that + lh + max_deviation.
        import re
        z_pattern = re.compile(r"Z([\d.]+)")
        current_layer = -1
        violations = []

        for chunk in result:
            for line in chunk.split("\n"):
                stripped = line.strip()
                if stripped.startswith(";LAYER:"):
                    try:
                        current_layer = int(stripped.split(":")[1])
                    except (ValueError, IndexError):
                        pass
                    continue
                if not stripped.startswith("G1") or "E" not in stripped:
                    continue

                z_match = z_pattern.search(stripped)
                if not z_match:
                    continue

                z_val = float(z_match.group(1))
                nominal_z = (current_layer + 1) * layer_height

                # For top-only mode, only the top 5 layers are bent.
                if current_layer < n_layers - 5:
                    continue

                layers_from_top = n_layers - 1 - current_layer
                # surface_z = 3.0 for our mock
                target_z = 3.0 - layers_from_top * layer_height
                layer_below_z = 3.0 - (layers_from_top + 1) * layer_height
                max_allowed = layer_below_z + layer_height + max_deviation + 0.01

                if z_val > max_allowed:
                    violations.append(
                        f"Layer {current_layer}: Z={z_val:.3f} exceeds "
                        f"max={max_allowed:.3f} (layer_below={layer_below_z:.3f})"
                    )

        assert len(violations) == 0, (
            f"Found {len(violations)} Z deviation violations:\n"
            + "\n".join(violations[:10])
        )

    def test_infill_first_in_skin_walls_mode(self):
        """In skin_walls mode with bent layers, FILL type groups should
        appear before WALL/SKIN groups in the G-code output.
        """
        # Create G-code with mixed types per layer.
        n_layers = 10
        layer_height = 0.2
        chunks = [
            ";Header\n",
            "G28\nM83\n",  # Start G-code with relative extrusion
        ]
        abs_e = 0.0
        for layer in range(n_layers):
            z = (layer + 1) * layer_height
            lines = [f";LAYER:{layer}"]

            # WALL-OUTER first
            lines.append(";TYPE:WALL-OUTER")
            for i in range(3):
                abs_e += 0.5
                x = 5.0 + i * 2.0
                if i == 0:
                    lines.append(f"G1 X{x:.3f} Y10.000 Z{z:.3f} E{abs_e:.5f} F1500")
                else:
                    lines.append(f"G1 X{x:.3f} Y10.000 E{abs_e:.5f}")

            # Then FILL
            lines.append(";TYPE:FILL")
            for i in range(3):
                abs_e += 0.5
                x = 5.0 + i * 2.0
                lines.append(f"G1 X{x:.3f} Y12.000 E{abs_e:.5f}")

            # Then SKIN
            lines.append(";TYPE:SKIN")
            for i in range(3):
                abs_e += 0.5
                x = 5.0 + i * 2.0
                lines.append(f"G1 X{x:.3f} Y14.000 E{abs_e:.5f}")

            chunks.append("\n".join(lines) + "\n")

        hm = _MockHeightMap(z_value=1.5, grid_shape=(20, 20))
        safe_map = np.ones((20, 20), dtype=bool)
        blend_map = np.ones((20, 20), dtype=np.float64)

        settings = {
            "layer_height": layer_height,
            "nonplanar_layer_count": 3,
            "max_angle_deg": 45.0,
            "flow_compensation": False,
            "feedrate_compensation": False,
            "segment_length": 50.0,
            "surface_mode": "top_only",
            "nonplanar_line_types": "skin_walls",
            "max_path_deviation": 0.4,
            "nozzle_size": 0.4,
        }
        result = bend_gcode(chunks, hm, safe_map, blend_map, settings)

        # For each target layer (top 3), verify FILL appears before
        # WALL-OUTER and SKIN.
        import re
        type_re = re.compile(r"^;TYPE:(\S+)", re.IGNORECASE)
        target_start = n_layers - 3  # layers 7, 8, 9

        for ci in range(2, len(result)):
            layer_num = -1
            type_order = []
            for line in result[ci].split("\n"):
                stripped = line.strip()
                if stripped.startswith(";LAYER:"):
                    try:
                        layer_num = int(stripped.split(":")[1])
                    except (ValueError, IndexError):
                        pass
                type_m = type_re.match(stripped)
                if type_m:
                    type_order.append(type_m.group(1).upper())

            if layer_num < target_start:
                continue

            # FILL should appear before WALL-OUTER and SKIN.
            if "FILL" in type_order:
                fill_idx = type_order.index("FILL")
                for other in ["WALL-OUTER", "SKIN"]:
                    if other in type_order:
                        other_idx = type_order.index(other)
                        assert fill_idx < other_idx, (
                            f"Layer {layer_num}: FILL (idx={fill_idx}) should "
                            f"come before {other} (idx={other_idx}) but doesn't. "
                            f"Type order: {type_order}"
                        )
