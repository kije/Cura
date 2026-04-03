# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

from typing import Dict, List, Optional, Tuple


class GradientKeyframe:
    """A single keyframe in a gradient profile, mapping a Z-height to a mix ratio."""

    def __init__(self, height_mm: float, ratio_a: float) -> None:
        self.height_mm = height_mm
        self.ratio_a = max(0.0, min(1.0, ratio_a))  # Clamp to [0, 1]

    def to_dict(self) -> Dict:
        return {"height_mm": self.height_mm, "ratio_a": self.ratio_a}

    @classmethod
    def from_dict(cls, data: Dict) -> "GradientKeyframe":
        return cls(height_mm=data["height_mm"], ratio_a=data["ratio_a"])


class GradientProfile:
    """Height-based transition between mix ratios.

    Defines keyframes at specific Z-heights with target ratios.
    Between keyframes, the ratio is linearly interpolated.
    Below the first keyframe, uses the first keyframe's ratio.
    Above the last keyframe, uses the last keyframe's ratio.
    """

    def __init__(self, enabled: bool = True, keyframes: Optional[List[GradientKeyframe]] = None) -> None:
        self.enabled = enabled
        self.keyframes = sorted(keyframes or [], key=lambda k: k.height_mm)

    def interpolate_ratio(self, z_height: float) -> float:
        """Get the interpolated ratio_a at a given Z-height.

        Returns a value between 0.0 (all filament B) and 1.0 (all filament A).
        """
        if not self.keyframes:
            return 0.5

        # Below first keyframe
        if z_height <= self.keyframes[0].height_mm:
            return self.keyframes[0].ratio_a

        # Above last keyframe
        if z_height >= self.keyframes[-1].height_mm:
            return self.keyframes[-1].ratio_a

        # Find surrounding keyframes and interpolate
        for i in range(len(self.keyframes) - 1):
            kf_low = self.keyframes[i]
            kf_high = self.keyframes[i + 1]
            if kf_low.height_mm <= z_height <= kf_high.height_mm:
                span = kf_high.height_mm - kf_low.height_mm
                if span <= 0:
                    return kf_low.ratio_a
                t = (z_height - kf_low.height_mm) / span
                return kf_low.ratio_a + t * (kf_high.ratio_a - kf_low.ratio_a)

        return 0.5

    def get_pattern_at_height(self, z_height: float, layer_height: float) -> Tuple[int, int]:
        """Convert interpolated ratio at a height into an approximate integer ratio.

        Returns (ratio_a, ratio_b) as integers that approximate the interpolated ratio.
        Used to generate a local dither pattern at a specific height.
        """
        ratio_a = self.interpolate_ratio(z_height)

        # Convert to integer ratio out of 10 for reasonable granularity
        int_a = max(1, round(ratio_a * 10))
        int_b = max(1, 10 - int_a)

        # Simplify the ratio
        from math import gcd
        d = gcd(int_a, int_b)
        return int_a // d, int_b // d

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "keyframes": [kf.to_dict() for kf in self.keyframes],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "GradientProfile":
        keyframes = [GradientKeyframe.from_dict(kf) for kf in data.get("keyframes", [])]
        return cls(enabled=data.get("enabled", True), keyframes=keyframes)
