# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import re
from typing import Dict, List, Optional

from UM.Logger import Logger

from ..models.MixedFilament import MixedFilament
from ..models.DitherPattern import DitherPattern
from .LayerAnalyzer import LayerAnalyzer, LayerInfo


class GCodeProcessor:
    """Post-processes G-code to apply mixed filament layer dithering.

    For each active mixed filament, identifies layers printed with the proxy
    extruder and rewrites tool commands according to the dither pattern:

    - IDEX/tool-change mode: replaces Tn with the appropriate physical extruder
    - Mixing hotend mode: injects M163/M164 (Marlin) or M567 (RepRap) commands
    """

    TOOL_CMD_PATTERN = re.compile(r"^(T)(\d+)\s*$", re.MULTILINE)

    def process(self, gcode_list: List[str], mixed_filaments: List[MixedFilament]) -> List[str]:
        """Process the full G-code list, applying all mixed filament definitions.

        Args:
            gcode_list: Cura's G-code list (header + layers)
            mixed_filaments: List of active MixedFilament definitions

        Returns:
            Modified gcode_list with dithered tool commands
        """
        if not mixed_filaments:
            return gcode_list

        # Build a map of proxy_extruder -> MixedFilament for quick lookup
        proxy_map: Dict[int, MixedFilament] = {}
        for mf in mixed_filaments:
            if mf.enabled:
                proxy_map[mf.proxy_extruder] = mf

        if not proxy_map:
            return gcode_list

        # Parse layer structure
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(gcode_list)
        layer_height = analyzer.layer_height

        Logger.log("d", f"MixedColor: Found {len(layers)} layers, "
                   f"layer_height={layer_height}, "
                   f"proxy extruders: {list(proxy_map.keys())}")

        # Track how many layers each proxy extruder has been used for
        # (needed for dither sequencing)
        proxy_layer_counts: Dict[int, int] = {pe: 0 for pe in proxy_map}

        # Process each layer
        for layer_info in layers:
            if layer_info.active_tool not in proxy_map:
                continue

            mf = proxy_map[layer_info.active_tool]
            proxy_layer_idx = proxy_layer_counts[mf.proxy_extruder]
            proxy_layer_counts[mf.proxy_extruder] += 1

            gcode_idx = layer_info.gcode_index
            original_gcode = gcode_list[gcode_idx]

            if mf.output_mode == "tool_change":
                modified = self._rewrite_tool_change(
                    original_gcode, mf, proxy_layer_idx,
                    layer_info.z_height, layer_height
                )
            else:  # mixing hotend
                modified = self._rewrite_mixing_hotend(
                    original_gcode, mf, proxy_layer_idx,
                    layer_info.z_height, layer_height
                )

            gcode_list[gcode_idx] = modified

        return gcode_list

    def _rewrite_tool_change(self, gcode: str, mf: MixedFilament,
                              layer_index: int, z_height: float,
                              layer_height: float) -> str:
        """Rewrite tool commands for IDEX/tool-changer mode.

        Replaces the proxy extruder's Tn command with the actual physical extruder
        determined by the dither pattern at this layer.
        """
        target_extruder = mf.get_extruder_for_layer(layer_index, z_height, layer_height)
        proxy_tool = f"T{mf.proxy_extruder}"
        replacement_tool = f"T{target_extruder}"

        # Add a comment indicating mixed color processing
        comment = f" ;MixedColor:{mf.name}"

        def replace_tool(match):
            tool_letter = match.group(1)
            tool_num = int(match.group(2))
            if tool_num == mf.proxy_extruder:
                return f"{tool_letter}{target_extruder}{comment}"
            return match.group(0)

        return self.TOOL_CMD_PATTERN.sub(replace_tool, gcode)

    def _rewrite_mixing_hotend(self, gcode: str, mf: MixedFilament,
                                layer_index: int, z_height: float,
                                layer_height: float) -> str:
        """Rewrite tool commands for mixing hotend mode.

        Injects mixing ratio commands (M163/M164 or M567) before the tool select
        command for the proxy extruder.
        """
        ratio_a = mf.get_mix_ratio_for_layer(layer_index, z_height)
        ratio_b = 1.0 - ratio_a
        proxy = mf.proxy_extruder
        comment = f" ;MixedColor:{mf.name}"

        if mf.mix_gcode == "marlin_m163":
            mix_commands = self._generate_marlin_mix(mf.filament_a, mf.filament_b,
                                                      ratio_a, ratio_b, proxy)
        else:  # reprap_m567
            mix_commands = self._generate_reprap_mix(mf.filament_a, mf.filament_b,
                                                      ratio_a, ratio_b, proxy)

        def replace_tool(match):
            tool_letter = match.group(1)
            tool_num = int(match.group(2))
            if tool_num == proxy:
                return mix_commands + f"\n{tool_letter}{tool_num}{comment}"
            return match.group(0)

        return self.TOOL_CMD_PATTERN.sub(replace_tool, gcode)

    def _generate_marlin_mix(self, extruder_a: int, extruder_b: int,
                              ratio_a: float, ratio_b: float, virtual_tool: int) -> str:
        """Generate Marlin M163/M164 mixing commands.

        M163 S<extruder> P<weight>  - Set mix factor for an extruder
        M164 S<virtual_tool>         - Save the current mix to a virtual tool
        """
        lines = []

        # Handle edge cases for clean output
        if ratio_a >= 0.999:
            lines.append(f"M163 S{extruder_a} P1")
            lines.append(f"M163 S{extruder_b} P0")
        elif ratio_a <= 0.001:
            lines.append(f"M163 S{extruder_a} P0")
            lines.append(f"M163 S{extruder_b} P1")
        else:
            lines.append(f"M163 S{extruder_a} P{ratio_a:.2f}")
            lines.append(f"M163 S{extruder_b} P{ratio_b:.2f}")

        lines.append(f"M164 S{virtual_tool}")
        return "\n".join(lines)

    def _generate_reprap_mix(self, extruder_a: int, extruder_b: int,
                              ratio_a: float, ratio_b: float, virtual_tool: int) -> str:
        """Generate RepRap M567 mixing command.

        M567 P<tool> E<ratio_a>:<ratio_b>  - Set mix ratio for a tool
        """
        return f"M567 P{virtual_tool} E{ratio_a:.2f}:{ratio_b:.2f}"
