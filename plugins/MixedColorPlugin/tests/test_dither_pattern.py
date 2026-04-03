# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import sys
import os
import unittest

# Add parent directories to path so imports work standalone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from models.DitherPattern import DitherPattern


class TestDitherPatternRatioMode(unittest.TestCase):
    """Tests for ratio-based pattern generation."""

    def test_1_to_1_ratio(self):
        p = DitherPattern(mode="ratio", ratio_a=1, ratio_b=1)
        self.assertEqual(p.get_cycle(), [0, 1])

    def test_2_to_1_ratio(self):
        p = DitherPattern(mode="ratio", ratio_a=2, ratio_b=1)
        self.assertEqual(p.get_cycle(), [0, 0, 1])

    def test_3_to_2_ratio(self):
        p = DitherPattern(mode="ratio", ratio_a=3, ratio_b=2)
        self.assertEqual(p.get_cycle(), [0, 0, 0, 1, 1])

    def test_1_to_3_ratio(self):
        p = DitherPattern(mode="ratio", ratio_a=1, ratio_b=3)
        self.assertEqual(p.get_cycle(), [0, 1, 1, 1])

    def test_minimum_ratio_clamped_to_1(self):
        p = DitherPattern(mode="ratio", ratio_a=0, ratio_b=0)
        self.assertEqual(p.ratio_a, 1)
        self.assertEqual(p.ratio_b, 1)


class TestDitherPatternCustomMode(unittest.TestCase):
    """Tests for custom pattern string parsing."""

    def test_ab_notation(self):
        p = DitherPattern(mode="custom", custom_pattern="AABB")
        self.assertEqual(p.get_cycle(), [0, 0, 1, 1])

    def test_12_notation(self):
        p = DitherPattern(mode="custom", custom_pattern="11212")
        self.assertEqual(p.get_cycle(), [0, 0, 1, 0, 1])

    def test_separator_notation(self):
        p = DitherPattern(mode="custom", custom_pattern="A/B/A/B")
        self.assertEqual(p.get_cycle(), [0, 1, 0, 1])

    def test_mixed_separators(self):
        p = DitherPattern(mode="custom", custom_pattern="1-2|1_2")
        self.assertEqual(p.get_cycle(), [0, 1, 0, 1])

    def test_lowercase_input(self):
        p = DitherPattern(mode="custom", custom_pattern="aabab")
        self.assertEqual(p.get_cycle(), [0, 0, 1, 0, 1])

    def test_empty_pattern_fallback(self):
        p = DitherPattern(mode="custom", custom_pattern="")
        self.assertEqual(p.get_cycle(), [0, 1])

    def test_invalid_chars_ignored(self):
        p = DitherPattern(mode="custom", custom_pattern="AXBYCZ")
        self.assertEqual(p.get_cycle(), [0, 1])

    def test_only_invalid_chars_fallback(self):
        p = DitherPattern(mode="custom", custom_pattern="XYZ")
        self.assertEqual(p.get_cycle(), [0, 1])


class TestDitherPatternSequence(unittest.TestCase):
    """Tests for full sequence generation."""

    def test_sequence_repeats(self):
        p = DitherPattern(mode="ratio", ratio_a=1, ratio_b=1)
        seq = p.get_sequence(6)
        self.assertEqual(seq, [0, 1, 0, 1, 0, 1])

    def test_sequence_2_1_repeats(self):
        p = DitherPattern(mode="ratio", ratio_a=2, ratio_b=1)
        seq = p.get_sequence(9)
        self.assertEqual(seq, [0, 0, 1, 0, 0, 1, 0, 0, 1])

    def test_sequence_length_matches(self):
        p = DitherPattern(mode="ratio", ratio_a=3, ratio_b=2)
        seq = p.get_sequence(7)
        self.assertEqual(len(seq), 7)

    def test_sequence_partial_cycle(self):
        p = DitherPattern(mode="ratio", ratio_a=2, ratio_b=1)
        seq = p.get_sequence(4)
        self.assertEqual(seq, [0, 0, 1, 0])  # Partial second cycle

    def test_custom_sequence(self):
        p = DitherPattern(mode="custom", custom_pattern="ABB")
        seq = p.get_sequence(7)
        self.assertEqual(seq, [0, 1, 1, 0, 1, 1, 0])


class TestDitherPatternRatio(unittest.TestCase):
    """Tests for ratio fraction calculation."""

    def test_1_1_ratio_fraction(self):
        p = DitherPattern(mode="ratio", ratio_a=1, ratio_b=1)
        self.assertAlmostEqual(p.get_ratio_fraction(), 0.5)

    def test_2_1_ratio_fraction(self):
        p = DitherPattern(mode="ratio", ratio_a=2, ratio_b=1)
        self.assertAlmostEqual(p.get_ratio_fraction(), 2 / 3)

    def test_1_3_ratio_fraction(self):
        p = DitherPattern(mode="ratio", ratio_a=1, ratio_b=3)
        self.assertAlmostEqual(p.get_ratio_fraction(), 0.25)

    def test_custom_ratio_fraction(self):
        p = DitherPattern(mode="custom", custom_pattern="AAAB")
        self.assertAlmostEqual(p.get_ratio_fraction(), 0.75)


class TestDitherPatternDisplay(unittest.TestCase):
    """Tests for display string generation."""

    def test_ratio_display(self):
        p = DitherPattern(mode="ratio", ratio_a=2, ratio_b=1)
        self.assertEqual(p.get_display_string(), "AAB")

    def test_custom_display(self):
        p = DitherPattern(mode="custom", custom_pattern="1/2/1/2/2")
        self.assertEqual(p.get_display_string(), "ABABB")


class TestDitherPatternSerialization(unittest.TestCase):
    """Tests for to_dict/from_dict round-trip."""

    def test_round_trip_ratio(self):
        p = DitherPattern(mode="ratio", ratio_a=3, ratio_b=2, cadence_height_a=0.3)
        d = p.to_dict()
        p2 = DitherPattern.from_dict(d)
        self.assertEqual(p2.mode, "ratio")
        self.assertEqual(p2.ratio_a, 3)
        self.assertEqual(p2.ratio_b, 2)
        self.assertEqual(p2.cadence_height_a, 0.3)

    def test_round_trip_custom(self):
        p = DitherPattern(mode="custom", custom_pattern="AABAB")
        d = p.to_dict()
        p2 = DitherPattern.from_dict(d)
        self.assertEqual(p2.mode, "custom")
        self.assertEqual(p2.custom_pattern, "AABAB")
        self.assertEqual(p2.get_cycle(), [0, 0, 1, 0, 1])


if __name__ == "__main__":
    unittest.main()
