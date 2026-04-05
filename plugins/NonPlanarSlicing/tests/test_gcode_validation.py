import math
import logging
import pytest

from gcode.gcode_bender import _validate_output_gcode


class TestValidateOutputGcode:
    """Tests for _validate_output_gcode."""

    def test_validate_detects_negative_z(self, caplog):
        """G-code with Z<0 should be detected."""
        gcode_list = [
            ";HEADER\nG28\n",
            "G1 X10 Y10 Z-1.0 E1.0 F1000\n"
            "G1 X20 Y20 Z-2.0 E2.0\n",
        ]

        with caplog.at_level(logging.WARNING, logger="gcode.gcode_bender"):
            _validate_output_gcode(gcode_list, max_angle_deg=45.0)

        assert any("Z < 0" in msg for msg in caplog.messages), (
            f"Expected a warning about Z < 0, got: {caplog.messages}"
        )
        # Both moves have Z<0, so the count should be 2.
        assert any("2 moves with Z < 0" in msg for msg in caplog.messages)

    def test_validate_detects_steep_angle(self, caplog):
        """Consecutive extrusion moves at ~63 deg should trigger angle violation."""
        # Two consecutive G1 extrusion moves:
        #   Move 1: from (0,0,0) to (10, 0, 0) — flat, establishes prev position
        #   Move 2: from (10,0,0) to (15, 0, 10) — planar dist=5, dz=10
        #   angle = arctan(10/5) ≈ 63.4°, well above 45° max
        gcode_list = [
            "G1 X10 Y0 Z0 E1.0 F1000\n"
            "G1 X15 Y0 Z10 E2.0\n",
        ]

        with caplog.at_level(logging.WARNING, logger="gcode.gcode_bender"):
            _validate_output_gcode(gcode_list, max_angle_deg=45.0)

        assert any("exceed max nozzle angle" in msg for msg in caplog.messages), (
            f"Expected angle violation warning, got: {caplog.messages}"
        )
        assert any("45.0 deg" in msg for msg in caplog.messages)

    def test_validate_detects_backwards_e(self, caplog):
        """Decreasing absolute E values should be flagged."""
        # In absolute E mode (default / M82), E going from 10 to 9
        # is a backwards jump (not a retraction via G10/firmware retract).
        gcode_list = [
            "M82\n"
            "G1 X10 Y10 Z0.2 E10.0 F1000\n"
            "G1 X20 Y20 Z0.2 E9.0\n",
        ]

        with caplog.at_level(logging.WARNING, logger="gcode.gcode_bender"):
            _validate_output_gcode(gcode_list, max_angle_deg=45.0)

        assert any("decreasing absolute E" in msg for msg in caplog.messages), (
            f"Expected backwards-E warning, got: {caplog.messages}"
        )
        assert any("1 moves with decreasing absolute E" in msg for msg in caplog.messages)

    def test_validate_passes_clean_gcode(self, caplog):
        """Valid G-code should pass all checks with an info-level message."""
        # Normal printing: Z > 0, gentle angles, monotonically increasing E.
        gcode_list = [
            ";HEADER\nM82\n",
            "G1 X10 Y0 Z0.2 E1.0 F1500\n"
            "G1 X20 Y0 Z0.3 E2.0\n"
            "G1 X30 Y0 Z0.4 E3.0\n"
            "G1 X40 Y10 Z0.5 E4.0\n",
        ]

        with caplog.at_level(logging.INFO, logger="gcode.gcode_bender"):
            _validate_output_gcode(gcode_list, max_angle_deg=45.0)

        # No warnings should be present.
        warning_messages = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert warning_messages == [], (
            f"Expected no warnings for clean G-code, got: {warning_messages}"
        )
        # Should have the "all checks passed" info message.
        assert any("all checks passed" in msg for msg in caplog.messages), (
            f"Expected 'all checks passed' info message, got: {caplog.messages}"
        )

    def test_validate_handles_relative_e(self, caplog):
        """M83 mode with negative E (retraction) should NOT flag as backwards."""
        # In relative extrusion mode, negative E is a normal retraction
        # and should not be treated as a backwards-E error.
        gcode_list = [
            "M83\n"
            "G1 X10 Y0 Z0.2 E0.5 F1500\n"
            "G1 X20 Y0 Z0.2 E0.5\n"
            "G1 E-1.0\n"  # retraction — negative E is fine in relative mode
            "G0 X30 Y0 Z0.4\n"  # travel
            "G1 E1.0\n"  # de-retraction
            "G1 X40 Y0 Z0.4 E0.5\n",
        ]

        with caplog.at_level(logging.WARNING, logger="gcode.gcode_bender"):
            _validate_output_gcode(gcode_list, max_angle_deg=45.0)

        # No backwards-E warning should appear.
        assert not any("decreasing absolute E" in msg for msg in caplog.messages), (
            f"Relative-mode retraction should not trigger backwards-E warning, "
            f"got: {caplog.messages}"
        )
