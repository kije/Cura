# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the flow compensator module."""

import pytest
from gcode.flow_compensator import compensate_flow, compute_actual_layer_height


class TestCompensateFlow:
    """Tests for compensate_flow."""

    def test_no_change_same_height(self):
        """If actual == nominal, E should be unchanged."""
        result = compensate_flow(1.0, 0.2, 0.2, is_relative=True)
        assert result == pytest.approx(1.0)

    def test_thicker_layer_more_extrusion(self):
        """Thicker layer should increase extrusion."""
        result = compensate_flow(1.0, 0.3, 0.2, is_relative=True)
        assert result == pytest.approx(1.5)

    def test_thinner_layer_less_extrusion(self):
        """Thinner layer should decrease extrusion."""
        result = compensate_flow(1.0, 0.1, 0.2, is_relative=True)
        assert result == pytest.approx(0.5)

    def test_clamp_high(self):
        """Multiplier should be clamped to 2.0."""
        result = compensate_flow(1.0, 1.0, 0.2, is_relative=True)
        assert result == pytest.approx(2.0)

    def test_clamp_low(self):
        """Multiplier should be clamped to 0.5."""
        result = compensate_flow(1.0, 0.01, 0.2, is_relative=True)
        assert result == pytest.approx(0.5)

    def test_absolute_mode(self):
        """In absolute mode, the delta is scaled and added back to previous."""
        # previous_abs_e = 10.0, e_value = 11.0 → delta = 1.0
        # actual 0.3, nominal 0.2 → multiplier = 1.5
        # result = 10.0 + 1.0 * 1.5 = 11.5
        result = compensate_flow(11.0, 0.3, 0.2, is_relative=False, previous_abs_e=10.0)
        assert result == pytest.approx(11.5)

    def test_zero_nominal_returns_unchanged(self):
        """Zero nominal layer height should return E unchanged."""
        result = compensate_flow(5.0, 0.2, 0.0, is_relative=True)
        assert result == pytest.approx(5.0)

    def test_negative_nominal_returns_unchanged(self):
        """Negative nominal should return E unchanged."""
        result = compensate_flow(5.0, 0.2, -0.1, is_relative=True)
        assert result == pytest.approx(5.0)


class TestComputeActualLayerHeight:
    """Tests for compute_actual_layer_height."""

    def test_normal_case(self):
        result = compute_actual_layer_height(1.0, 0.8, 0.2)
        assert result == pytest.approx(0.2)

    def test_none_layer_below(self):
        """None layer_below_z should return nominal."""
        result = compute_actual_layer_height(1.0, None, 0.2)
        assert result == pytest.approx(0.2)

    def test_clamp_minimum(self):
        """Very thin layers should be clamped to 0.05."""
        result = compute_actual_layer_height(1.0, 0.999, 0.2)
        assert result >= 0.05

    def test_clamp_maximum(self):
        """Very thick layers should be clamped to 3x nominal."""
        result = compute_actual_layer_height(2.0, 0.0, 0.2)
        assert result == pytest.approx(0.6, abs=1e-9)

    def test_exact_nominal(self):
        result = compute_actual_layer_height(0.4, 0.2, 0.2)
        assert result == pytest.approx(0.2)
