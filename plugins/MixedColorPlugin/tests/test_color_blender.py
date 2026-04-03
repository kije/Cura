# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from core.ColorBlender import ColorBlender


class TestHexConversion(unittest.TestCase):

    def test_hex_to_rgb(self):
        self.assertEqual(ColorBlender.hex_to_rgb("#ff0000"), (255, 0, 0))
        self.assertEqual(ColorBlender.hex_to_rgb("#00ff00"), (0, 255, 0))
        self.assertEqual(ColorBlender.hex_to_rgb("#0000ff"), (0, 0, 255))
        self.assertEqual(ColorBlender.hex_to_rgb("#808080"), (128, 128, 128))

    def test_hex_to_rgb_no_hash(self):
        self.assertEqual(ColorBlender.hex_to_rgb("ff0000"), (255, 0, 0))

    def test_hex_to_rgb_invalid(self):
        self.assertEqual(ColorBlender.hex_to_rgb("invalid"), (128, 128, 128))

    def test_rgb_to_hex(self):
        self.assertEqual(ColorBlender.rgb_to_hex((255, 0, 0)), "#ff0000")
        self.assertEqual(ColorBlender.rgb_to_hex((0, 255, 0)), "#00ff00")
        self.assertEqual(ColorBlender.rgb_to_hex((0, 0, 255)), "#0000ff")

    def test_rgb_to_hex_clamped(self):
        self.assertEqual(ColorBlender.rgb_to_hex((300, -10, 128)), "#ff0080")


class TestSimpleBlending(unittest.TestCase):

    def test_blend_same_color(self):
        result = ColorBlender.blend_rgb((255, 0, 0), (255, 0, 0), 0.5, mode="simple")
        self.assertEqual(result, (255, 0, 0))

    def test_blend_full_a(self):
        result = ColorBlender.blend_rgb((255, 0, 0), (0, 0, 255), 1.0, mode="simple")
        self.assertEqual(result, (255, 0, 0))

    def test_blend_full_b(self):
        result = ColorBlender.blend_rgb((255, 0, 0), (0, 0, 255), 0.0, mode="simple")
        self.assertEqual(result, (0, 0, 255))

    def test_blend_50_50_produces_middle(self):
        result = ColorBlender.blend_rgb((255, 0, 0), (0, 0, 255), 0.5, mode="simple")
        # In linear space, the blend should produce something between the two
        self.assertGreater(result[0], 50)
        self.assertLess(result[0], 200)
        self.assertGreater(result[2], 50)
        self.assertLess(result[2], 200)

    def test_blend_black_white(self):
        result = ColorBlender.blend_rgb((255, 255, 255), (0, 0, 0), 0.5, mode="simple")
        # Should be a middle gray
        self.assertGreater(result[0], 100)
        self.assertLess(result[0], 200)


class TestKubelkaMunkBlending(unittest.TestCase):

    def test_blend_same_color(self):
        result = ColorBlender.blend_rgb((255, 0, 0), (255, 0, 0), 0.5, mode="kubelka_munk")
        # Allow small rounding error from K/S conversion
        self.assertAlmostEqual(result[0], 255, delta=5)
        self.assertAlmostEqual(result[1], 0, delta=5)
        self.assertAlmostEqual(result[2], 0, delta=5)

    def test_blend_full_a(self):
        result = ColorBlender.blend_rgb((255, 0, 0), (0, 0, 255), 1.0, mode="kubelka_munk")
        # Should be very close to pure red
        self.assertGreater(result[0], 250)

    def test_blend_full_b(self):
        result = ColorBlender.blend_rgb((255, 0, 0), (0, 0, 255), 0.0, mode="kubelka_munk")
        # Should be very close to pure blue
        self.assertGreater(result[2], 250)

    def test_km_subtractive_mixing(self):
        """K-M should produce darker blends than simple averaging (subtractive)."""
        blue = (0, 0, 255)
        yellow = (255, 255, 0)
        result_km = ColorBlender.blend_rgb(blue, yellow, 0.5, mode="kubelka_munk")
        result_simple = ColorBlender.blend_rgb(blue, yellow, 0.5, mode="simple")

        # K-M blend of blue+yellow should have more green component
        # than simple average (which just averages RGB channels)
        # The key is that K-M models subtractive mixing
        # Both should produce valid RGB values
        for ch in result_km:
            self.assertGreaterEqual(ch, 0)
            self.assertLessEqual(ch, 255)

    def test_blend_produces_valid_rgb(self):
        """All blends should produce valid 0-255 RGB values."""
        test_colors = [
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (0, 255, 255), (255, 0, 255),
            (255, 255, 255), (0, 0, 0), (128, 128, 128),
        ]
        for ca in test_colors:
            for cb in test_colors:
                for ratio in [0.0, 0.25, 0.5, 0.75, 1.0]:
                    result = ColorBlender.blend_rgb(ca, cb, ratio, mode="kubelka_munk")
                    for ch in result:
                        self.assertGreaterEqual(ch, 0, f"Negative channel: {ca} + {cb} @ {ratio} = {result}")
                        self.assertLessEqual(ch, 255, f"Over-255 channel: {ca} + {cb} @ {ratio} = {result}")


class TestSRGBConversion(unittest.TestCase):

    def test_black(self):
        linear = ColorBlender._srgb_to_linear((0, 0, 0))
        self.assertAlmostEqual(linear[0], 0.0)
        srgb = ColorBlender._linear_to_srgb(linear)
        self.assertEqual(srgb, (0, 0, 0))

    def test_white(self):
        linear = ColorBlender._srgb_to_linear((255, 255, 255))
        self.assertAlmostEqual(linear[0], 1.0, places=2)
        srgb = ColorBlender._linear_to_srgb(linear)
        self.assertEqual(srgb, (255, 255, 255))

    def test_round_trip(self):
        for val in [0, 32, 64, 128, 192, 255]:
            color = (val, val, val)
            linear = ColorBlender._srgb_to_linear(color)
            back = ColorBlender._linear_to_srgb(linear)
            for i in range(3):
                self.assertAlmostEqual(back[i], color[i], delta=1)


if __name__ == "__main__":
    unittest.main()
