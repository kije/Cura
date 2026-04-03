# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the feedrate adjuster module."""

import math
import pytest
from gcode.feedrate_adjuster import adjust_feedrate


class TestAdjustFeedrate:
    """Tests for adjust_feedrate."""

    def test_no_z_change(self):
        """Purely planar move should keep original feedrate."""
        result = adjust_feedrate(1500.0, 10.0, 0.0, 0.0)
        assert result == pytest.approx(1500.0)

    def test_45deg_slope(self):
        """45-degree slope: 3D distance = sqrt(2) * planar distance."""
        result = adjust_feedrate(1500.0, 10.0, 0.0, 10.0)
        expected = 1500.0 * (10.0 / math.sqrt(200.0))
        assert result == pytest.approx(expected)

    def test_vertical_move(self):
        """Near-vertical move (zero XY) should return original feedrate."""
        result = adjust_feedrate(1500.0, 0.0, 0.0, 10.0)
        assert result == pytest.approx(1500.0)

    def test_never_exceeds_original(self):
        """Result should never exceed original feedrate."""
        result = adjust_feedrate(1500.0, 10.0, 5.0, 1.0)
        assert result <= 1500.0

    def test_never_below_minimum(self):
        """Result should never go below 1.0 mm/min."""
        result = adjust_feedrate(1.0, 0.001, 0.001, 100.0)
        assert result >= 1.0

    def test_zero_feedrate(self):
        """Zero feedrate input should return minimum."""
        result = adjust_feedrate(0.0, 10.0, 0.0, 5.0)
        assert result >= 1.0

    def test_small_dz(self):
        """Small dz should produce a small adjustment."""
        result = adjust_feedrate(1500.0, 100.0, 0.0, 0.1)
        # Nearly unchanged
        assert result > 1490.0
        assert result <= 1500.0

    def test_diagonal_xy(self):
        """Diagonal XY move with Z change."""
        result = adjust_feedrate(1000.0, 3.0, 4.0, 5.0)
        planar = 5.0  # sqrt(9+16)
        actual = math.sqrt(9 + 16 + 25)
        expected = 1000.0 * planar / actual
        assert result == pytest.approx(expected)
