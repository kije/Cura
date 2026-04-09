# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the transition blender module."""

import numpy as np
import pytest
from analysis.transition_blender import smoothstep, compute_blend_map


class TestSmoothstep:
    """Tests for smoothstep."""

    def test_zero(self):
        assert smoothstep(0.0) == pytest.approx(0.0)

    def test_one(self):
        assert smoothstep(1.0) == pytest.approx(1.0)

    def test_half(self):
        assert smoothstep(0.5) == pytest.approx(0.5)

    def test_clamp_negative(self):
        assert smoothstep(-1.0) == pytest.approx(0.0)

    def test_clamp_above_one(self):
        assert smoothstep(2.0) == pytest.approx(1.0)

    def test_monotonic(self):
        """Smoothstep should be monotonically increasing on [0, 1]."""
        t = np.linspace(0, 1, 100)
        s = smoothstep(t)
        assert np.all(np.diff(s) >= 0)

    def test_array_input(self):
        arr = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
        result = smoothstep(arr)
        assert result.shape == (5,)
        assert result[0] == pytest.approx(0.0)
        assert result[-1] == pytest.approx(1.0)


class TestComputeBlendMap:
    """Tests for compute_blend_map."""

    def test_all_safe_interior(self):
        """Large safe region should have 1.0 deep inside."""
        safe_map = np.ones((20, 20), dtype=bool)
        blend = compute_blend_map(safe_map, resolution=1.0, blend_distance=3.0)
        # Center should be fully blended
        assert blend[10, 10] == pytest.approx(1.0)

    def test_all_unsafe(self):
        """All-False should produce all-zero blend."""
        safe_map = np.zeros((10, 10), dtype=bool)
        blend = compute_blend_map(safe_map, resolution=1.0, blend_distance=3.0)
        assert np.all(blend == 0.0)

    def test_boundary_zero(self):
        """Cells just outside the safe region should be 0.0."""
        safe_map = np.zeros((10, 10), dtype=bool)
        safe_map[3:7, 3:7] = True
        blend = compute_blend_map(safe_map, resolution=1.0, blend_distance=3.0)
        # Just outside the region
        assert blend[2, 5] == 0.0

    def test_boundary_gradient(self):
        """Blend should increase from boundary toward interior."""
        safe_map = np.ones((30, 30), dtype=bool)
        blend = compute_blend_map(safe_map, resolution=0.5, blend_distance=3.0)
        # Edge cells should have lower blend than center
        assert blend[0, 0] < blend[15, 15]

    def test_invalid_resolution(self):
        with pytest.raises(ValueError):
            compute_blend_map(np.ones((5, 5), dtype=bool), resolution=0.0, blend_distance=1.0)

    def test_invalid_blend_distance(self):
        with pytest.raises(ValueError):
            compute_blend_map(np.ones((5, 5), dtype=bool), resolution=1.0, blend_distance=0.0)

    def test_empty_map(self):
        blend = compute_blend_map(np.empty((0, 0), dtype=bool), resolution=1.0, blend_distance=1.0)
        assert blend.size == 0

    def test_values_in_range(self):
        """All blend values should be in [0, 1]."""
        safe_map = np.random.random((20, 20)) > 0.3
        blend = compute_blend_map(safe_map, resolution=0.5, blend_distance=2.0)
        assert np.all(blend >= 0.0)
        assert np.all(blend <= 1.0)
