# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import math
from typing import Tuple


class ColorBlender:
    """Color blending utilities for mixed filament preview.

    Provides two blending modes:
    1. Simple weighted RGB average (fast, reasonable for light colors)
    2. Kubelka-Munk approximation (more physically accurate for pigment mixing)

    The Kubelka-Munk approach better models subtractive color mixing, which is
    closer to how layered filaments appear in practice (light passes through
    semi-translucent layers and reflects back).
    """

    @staticmethod
    def blend_rgb(color_a: Tuple[int, int, int], color_b: Tuple[int, int, int],
                  ratio_a: float, mode: str = "kubelka_munk") -> Tuple[int, int, int]:
        """Blend two RGB colors at the given ratio.

        Args:
            color_a: RGB tuple (0-255) for filament A
            color_b: RGB tuple (0-255) for filament B
            ratio_a: Fraction of filament A (0.0 to 1.0)
            mode: "simple" for weighted RGB average, "kubelka_munk" for K-M approx

        Returns:
            Blended RGB tuple (0-255)
        """
        ratio_a = max(0.0, min(1.0, ratio_a))

        if mode == "kubelka_munk":
            return ColorBlender._blend_kubelka_munk(color_a, color_b, ratio_a)
        return ColorBlender._blend_simple(color_a, color_b, ratio_a)

    @staticmethod
    def _blend_simple(color_a: Tuple[int, int, int], color_b: Tuple[int, int, int],
                      ratio_a: float) -> Tuple[int, int, int]:
        """Simple weighted average in linear RGB space."""
        # Convert to linear space for more accurate blending
        la = ColorBlender._srgb_to_linear(color_a)
        lb = ColorBlender._srgb_to_linear(color_b)

        ratio_b = 1.0 - ratio_a
        blended = (
            la[0] * ratio_a + lb[0] * ratio_b,
            la[1] * ratio_a + lb[1] * ratio_b,
            la[2] * ratio_a + lb[2] * ratio_b,
        )

        return ColorBlender._linear_to_srgb(blended)

    @staticmethod
    def _blend_kubelka_munk(color_a: Tuple[int, int, int], color_b: Tuple[int, int, int],
                             ratio_a: float) -> Tuple[int, int, int]:
        """Simplified Kubelka-Munk blending for subtractive color mixing.

        This approximation converts RGB to K/S ratios (absorption/scattering),
        linearly mixes them, then converts back. This better models how pigments
        mix compared to simple RGB averaging.

        For example, blue + yellow = green (subtractive), not gray (additive).
        """
        # Convert sRGB to linear
        la = ColorBlender._srgb_to_linear(color_a)
        lb = ColorBlender._srgb_to_linear(color_b)

        ratio_b = 1.0 - ratio_a

        # Convert reflectance to K/S ratio using Kubelka-Munk equation:
        # K/S = (1-R)^2 / (2*R)  where R is reflectance [0, 1]
        blended = []
        for i in range(3):
            ks_a = ColorBlender._reflectance_to_ks(la[i])
            ks_b = ColorBlender._reflectance_to_ks(lb[i])

            # Linear mix of K/S ratios
            ks_mix = ks_a * ratio_a + ks_b * ratio_b

            # Convert back to reflectance
            r_mix = ColorBlender._ks_to_reflectance(ks_mix)
            blended.append(r_mix)

        return ColorBlender._linear_to_srgb(tuple(blended))

    @staticmethod
    def _reflectance_to_ks(r: float) -> float:
        """Convert reflectance to Kubelka-Munk K/S ratio."""
        r = max(0.001, min(0.999, r))  # Avoid division by zero
        return (1.0 - r) ** 2 / (2.0 * r)

    @staticmethod
    def _ks_to_reflectance(ks: float) -> float:
        """Convert Kubelka-Munk K/S ratio back to reflectance."""
        # R = 1 + K/S - sqrt((K/S)^2 + 2*K/S)
        if ks <= 0:
            return 1.0
        return 1.0 + ks - math.sqrt(ks * ks + 2.0 * ks)

    @staticmethod
    def _srgb_to_linear(color: Tuple[int, int, int]) -> Tuple[float, float, float]:
        """Convert sRGB (0-255) to linear RGB (0.0-1.0)."""
        def convert(c):
            c = c / 255.0
            if c <= 0.04045:
                return c / 12.92
            return ((c + 0.055) / 1.055) ** 2.4
        return (convert(color[0]), convert(color[1]), convert(color[2]))

    @staticmethod
    def _linear_to_srgb(color: Tuple[float, float, float]) -> Tuple[int, int, int]:
        """Convert linear RGB (0.0-1.0) to sRGB (0-255)."""
        def convert(c):
            c = max(0.0, min(1.0, c))
            if c <= 0.0031308:
                return int(round(c * 12.92 * 255))
            return int(round((1.055 * (c ** (1.0 / 2.4)) - 0.055) * 255))
        return (convert(color[0]), convert(color[1]), convert(color[2]))

    @staticmethod
    def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
        """Convert hex color string (#RRGGBB) to RGB tuple."""
        hex_color = hex_color.lstrip("#")
        if len(hex_color) != 6:
            return (128, 128, 128)
        try:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            return (r, g, b)
        except ValueError:
            return (128, 128, 128)

    @staticmethod
    def rgb_to_hex(color: Tuple[int, int, int]) -> str:
        """Convert RGB tuple to hex color string (#RRGGBB)."""
        r = max(0, min(255, color[0]))
        g = max(0, min(255, color[1]))
        b = max(0, min(255, color[2]))
        return f"#{r:02x}{g:02x}{b:02x}"
