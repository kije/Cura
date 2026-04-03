# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from models.GradientProfile import GradientProfile, GradientKeyframe


class TestGradientKeyframe(unittest.TestCase):

    def test_ratio_clamped(self):
        kf = GradientKeyframe(height_mm=0.0, ratio_a=1.5)
        self.assertEqual(kf.ratio_a, 1.0)

        kf2 = GradientKeyframe(height_mm=0.0, ratio_a=-0.5)
        self.assertEqual(kf2.ratio_a, 0.0)

    def test_serialization(self):
        kf = GradientKeyframe(height_mm=5.0, ratio_a=0.7)
        d = kf.to_dict()
        kf2 = GradientKeyframe.from_dict(d)
        self.assertEqual(kf2.height_mm, 5.0)
        self.assertAlmostEqual(kf2.ratio_a, 0.7)


class TestGradientInterpolation(unittest.TestCase):

    def _make_profile(self):
        return GradientProfile(enabled=True, keyframes=[
            GradientKeyframe(0.0, 1.0),
            GradientKeyframe(10.0, 0.5),
            GradientKeyframe(30.0, 0.0),
        ])

    def test_below_first_keyframe(self):
        gp = self._make_profile()
        self.assertAlmostEqual(gp.interpolate_ratio(-5.0), 1.0)

    def test_at_first_keyframe(self):
        gp = self._make_profile()
        self.assertAlmostEqual(gp.interpolate_ratio(0.0), 1.0)

    def test_at_second_keyframe(self):
        gp = self._make_profile()
        self.assertAlmostEqual(gp.interpolate_ratio(10.0), 0.5)

    def test_at_last_keyframe(self):
        gp = self._make_profile()
        self.assertAlmostEqual(gp.interpolate_ratio(30.0), 0.0)

    def test_above_last_keyframe(self):
        gp = self._make_profile()
        self.assertAlmostEqual(gp.interpolate_ratio(50.0), 0.0)

    def test_midpoint_interpolation(self):
        gp = self._make_profile()
        # Between 0mm (1.0) and 10mm (0.5), at 5mm should be 0.75
        self.assertAlmostEqual(gp.interpolate_ratio(5.0), 0.75)

    def test_second_segment_interpolation(self):
        gp = self._make_profile()
        # Between 10mm (0.5) and 30mm (0.0), at 20mm should be 0.25
        self.assertAlmostEqual(gp.interpolate_ratio(20.0), 0.25)

    def test_empty_keyframes(self):
        gp = GradientProfile(enabled=True, keyframes=[])
        self.assertAlmostEqual(gp.interpolate_ratio(5.0), 0.5)

    def test_single_keyframe(self):
        gp = GradientProfile(enabled=True, keyframes=[
            GradientKeyframe(5.0, 0.8),
        ])
        # Below: use first keyframe value
        self.assertAlmostEqual(gp.interpolate_ratio(0.0), 0.8)
        # At: use keyframe value
        self.assertAlmostEqual(gp.interpolate_ratio(5.0), 0.8)
        # Above: use last keyframe value
        self.assertAlmostEqual(gp.interpolate_ratio(10.0), 0.8)


class TestGradientPatternAtHeight(unittest.TestCase):

    def test_full_a_ratio(self):
        gp = GradientProfile(enabled=True, keyframes=[
            GradientKeyframe(0.0, 1.0),
        ])
        ra, rb = gp.get_pattern_at_height(0.0, 0.2)
        # Should be heavily biased toward A
        self.assertGreater(ra, rb)

    def test_full_b_ratio(self):
        gp = GradientProfile(enabled=True, keyframes=[
            GradientKeyframe(0.0, 0.0),
        ])
        ra, rb = gp.get_pattern_at_height(0.0, 0.2)
        # Should be heavily biased toward B
        self.assertGreater(rb, ra)

    def test_equal_ratio(self):
        gp = GradientProfile(enabled=True, keyframes=[
            GradientKeyframe(0.0, 0.5),
        ])
        ra, rb = gp.get_pattern_at_height(0.0, 0.2)
        self.assertEqual(ra, rb)


class TestGradientSerialization(unittest.TestCase):

    def test_round_trip(self):
        gp = GradientProfile(enabled=True, keyframes=[
            GradientKeyframe(0.0, 1.0),
            GradientKeyframe(15.0, 0.3),
            GradientKeyframe(30.0, 0.0),
        ])
        d = gp.to_dict()
        gp2 = GradientProfile.from_dict(d)
        self.assertEqual(gp2.enabled, True)
        self.assertEqual(len(gp2.keyframes), 3)
        self.assertAlmostEqual(gp2.keyframes[1].ratio_a, 0.3)
        self.assertAlmostEqual(gp2.keyframes[1].height_mm, 15.0)


if __name__ == "__main__":
    unittest.main()
