# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import re
from typing import Dict, List


class DitherPattern:
    """Defines the layer alternation pattern for a mixed filament.

    Supports two modes:
    - "ratio": Auto-generate a repeating pattern from a ratio (e.g., 2:1 = AAB)
    - "custom": User-defined pattern string (e.g., "AABAB" or "11212")
    """

    SEPARATOR_PATTERN = re.compile(r"[/\-_|]")

    def __init__(self, mode: str = "ratio", ratio_a: int = 1, ratio_b: int = 1,
                 custom_pattern: str = "", cadence_height_a: float = 0.0,
                 cadence_height_b: float = 0.0) -> None:
        self.mode = mode  # "ratio" or "custom"
        self.ratio_a = max(1, ratio_a)
        self.ratio_b = max(1, ratio_b)
        self.custom_pattern = custom_pattern
        self.cadence_height_a = cadence_height_a  # 0 means use layer height
        self.cadence_height_b = cadence_height_b

    def get_cycle(self) -> List[int]:
        """Return one cycle of the pattern as a list of 0s (filament A) and 1s (filament B)."""
        if self.mode == "custom" and self.custom_pattern.strip():
            return self._parse_custom_pattern()
        return self._generate_ratio_cycle()

    def _generate_ratio_cycle(self) -> List[int]:
        """Generate a cycle from the ratio: e.g., ratio 2:1 → [0, 0, 1]."""
        return [0] * self.ratio_a + [1] * self.ratio_b

    def _parse_custom_pattern(self) -> List[int]:
        """Parse a custom pattern string into a cycle.

        Accepts:
        - "AB", "AABB", "AABAB" (A/B notation)
        - "12", "1122", "11212" (1/2 notation)
        - "1/2", "1-2-1", "1|2|1|2" (separator notation)
        """
        cleaned = self.SEPARATOR_PATTERN.sub("", self.custom_pattern.strip().upper())
        if not cleaned:
            return [0, 1]  # Default fallback

        cycle = []
        for ch in cleaned:
            if ch in ("A", "1"):
                cycle.append(0)
            elif ch in ("B", "2"):
                cycle.append(1)
            # Ignore unrecognized characters

        return cycle if cycle else [0, 1]

    def get_sequence(self, num_layers: int) -> List[int]:
        """Return the full dither sequence for a given number of layers.

        Each element is 0 (filament A) or 1 (filament B).
        The cycle repeats to fill the requested number of layers.
        """
        cycle = self.get_cycle()
        if not cycle:
            cycle = [0, 1]

        sequence = []
        cycle_len = len(cycle)
        for i in range(num_layers):
            sequence.append(cycle[i % cycle_len])
        return sequence

    def get_ratio_fraction(self) -> float:
        """Return the fraction of filament A in the pattern (0.0 to 1.0)."""
        cycle = self.get_cycle()
        if not cycle:
            return 0.5
        a_count = sum(1 for x in cycle if x == 0)
        return a_count / len(cycle)

    def get_display_string(self) -> str:
        """Return a human-readable representation of the pattern."""
        cycle = self.get_cycle()
        return "".join("A" if x == 0 else "B" for x in cycle)

    def to_dict(self) -> Dict:
        return {
            "mode": self.mode,
            "ratio_a": self.ratio_a,
            "ratio_b": self.ratio_b,
            "custom_pattern": self.custom_pattern,
            "cadence_height_a": self.cadence_height_a,
            "cadence_height_b": self.cadence_height_b,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DitherPattern":
        return cls(
            mode=data.get("mode", "ratio"),
            ratio_a=data.get("ratio_a", 1),
            ratio_b=data.get("ratio_b", 1),
            custom_pattern=data.get("custom_pattern", ""),
            cadence_height_a=data.get("cadence_height_a", 0.0),
            cadence_height_b=data.get("cadence_height_b", 0.0),
        )
