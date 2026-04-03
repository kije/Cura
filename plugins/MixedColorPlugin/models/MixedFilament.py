# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import uuid
from typing import Dict, Optional, Tuple

from .DitherPattern import DitherPattern
from .GradientProfile import GradientProfile


class MixedFilament:
    """A virtual filament created by alternating two physical filaments.

    Objects are assigned to mixed filaments via the plugin UI. During
    G-code post-processing, layers containing those objects (identified
    by ;MESH: comments) have their tool commands rewritten based on
    the dither pattern.

    For global mode (no per-object assignment), ALL layers are processed.
    """

    def __init__(
        self,
        name: str = "Mixed Filament",
        filament_a: int = 0,
        filament_b: int = 1,
        pattern: Optional[DitherPattern] = None,
        gradient: Optional[GradientProfile] = None,
        output_mode: str = "tool_change",
        mix_gcode: str = "marlin_m163",
        enabled: bool = True,
        preview_color: Tuple[int, int, int] = (128, 128, 128),
        id: Optional[str] = None,
        assigned_meshes: Optional[list] = None,
        apply_globally: bool = True,
    ) -> None:
        self.id = id or str(uuid.uuid4())
        self.name = name
        self.filament_a = filament_a  # 0-based extruder index
        self.filament_b = filament_b  # 0-based extruder index
        self.pattern = pattern or DitherPattern()
        self.gradient = gradient
        self.output_mode = output_mode  # "tool_change" or "mixing"
        self.mix_gcode = mix_gcode  # "marlin_m163" or "reprap_m567"
        self.enabled = enabled
        self.preview_color = preview_color
        self.assigned_meshes = assigned_meshes or []  # List of mesh names this mix applies to
        self.apply_globally = apply_globally  # If True, applies to all layers (ignores mesh assignment)

    def applies_to_mesh(self, mesh_name: str) -> bool:
        """Check if this mixed filament should be applied to a specific mesh."""
        if self.apply_globally:
            return True
        return mesh_name in self.assigned_meshes

    def get_extruder_for_layer(self, layer_index: int, z_height: float,
                                layer_height: float) -> int:
        """Determine which physical extruder to use for a given layer."""
        if self.gradient and self.gradient.enabled and self.gradient.keyframes:
            ratio_a = self.gradient.interpolate_ratio(z_height)
            ra, rb = self.gradient.get_pattern_at_height(z_height, layer_height)
            local_pattern = DitherPattern(mode="ratio", ratio_a=ra, ratio_b=rb)
            cycle = local_pattern.get_cycle()
            choice = cycle[layer_index % len(cycle)]
        else:
            sequence = self.pattern.get_sequence(layer_index + 1)
            choice = sequence[layer_index]

        return self.filament_a if choice == 0 else self.filament_b

    def get_mix_ratio_for_layer(self, layer_index: int, z_height: float) -> float:
        """Get the mix ratio of filament A for a given layer (0.0 to 1.0)."""
        if self.gradient and self.gradient.enabled and self.gradient.keyframes:
            return self.gradient.interpolate_ratio(z_height)
        return self.pattern.get_ratio_fraction()

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "filament_a": self.filament_a,
            "filament_b": self.filament_b,
            "pattern": self.pattern.to_dict(),
            "gradient": self.gradient.to_dict() if self.gradient else None,
            "output_mode": self.output_mode,
            "mix_gcode": self.mix_gcode,
            "enabled": self.enabled,
            "preview_color": list(self.preview_color),
            "assigned_meshes": self.assigned_meshes,
            "apply_globally": self.apply_globally,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "MixedFilament":
        pattern = DitherPattern.from_dict(data.get("pattern", {}))
        gradient_data = data.get("gradient")
        gradient = GradientProfile.from_dict(gradient_data) if gradient_data else None
        preview = tuple(data.get("preview_color", [128, 128, 128]))

        return cls(
            id=data.get("id"),
            name=data.get("name", "Mixed Filament"),
            filament_a=data.get("filament_a", 0),
            filament_b=data.get("filament_b", 1),
            pattern=pattern,
            gradient=gradient,
            output_mode=data.get("output_mode", "tool_change"),
            mix_gcode=data.get("mix_gcode", "marlin_m163"),
            enabled=data.get("enabled", True),
            preview_color=preview,
            assigned_meshes=data.get("assigned_meshes", []),
            apply_globally=data.get("apply_globally", True),
        )
