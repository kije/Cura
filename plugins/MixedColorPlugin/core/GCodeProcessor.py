# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import re
from typing import Dict, List, Optional, Tuple

from UM.Logger import Logger

from ..models.MixedFilament import MixedFilament
from ..models.DitherPattern import DitherPattern
from .LayerAnalyzer import LayerAnalyzer, LayerInfo


class GCodeProcessor:
    """Post-processes G-code to apply mixed filament layer dithering.

    Each mixed filament has a proxy_extruder (a virtual extruder position).
    The processor identifies layers that use the proxy extruder's Tn command
    and rewrites them with dithered tool changes between the two physical
    extruders.

    Output modes:
    - IDEX/tool-change: replaces Tn with the appropriate physical extruder
    - Mixing hotend: injects M163/M164 (Marlin) or M567 (RepRap) commands

    Features:
    - Bresenham error diffusion for smooth dither distribution
    - Temperature pre-heating with N-layer lookahead
    """

    TOOL_CMD_PATTERN = re.compile(r"^(T)(\d+)\s*$", re.MULTILINE)

    def __init__(self, preheat_layers: int = 3,
                 extruder_temperatures: Optional[Dict[int, float]] = None,
                 standby_temperature: float = 150.0) -> None:
        self.preheat_layers = preheat_layers
        self.extruder_temperatures = extruder_temperatures or {}
        self.standby_temperature = standby_temperature

    def process(self, gcode_list: List[str], mixed_filaments: List[MixedFilament]) -> List[str]:
        """Process the full G-code list, applying all mixed filament definitions."""
        if not mixed_filaments:
            return gcode_list

        # Build map of proxy_extruder -> MixedFilament
        proxy_map: Dict[int, MixedFilament] = {}
        for mf in mixed_filaments:
            if mf.enabled and mf.proxy_extruder >= 0:
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

        # For each mixed filament, find applicable layers and compute schedule
        for proxy_pos, mf in proxy_map.items():
            applicable = [l for l in layers if l.active_tool == proxy_pos]
            if not applicable:
                Logger.log("d", f"MixedColor: No layers use proxy T{proxy_pos} for '{mf.name}'")
                continue

            Logger.log("d", f"MixedColor: '{mf.name}' (proxy T{proxy_pos}) applies to {len(applicable)} layers")

            # Compute Bresenham schedule
            schedule = self._bresenham_schedule(mf, applicable, layer_height)

            # Insert pre-heat commands
            if self.preheat_layers > 0 and self.extruder_temperatures and mf.output_mode == "tool_change":
                gcode_list = self._insert_preheat_commands(gcode_list, layers, applicable, schedule, mf)

            # Apply tool changes
            for layer_info, assignment in zip(applicable, schedule):
                gcode_idx = layer_info.gcode_index

                if mf.output_mode == "tool_change":
                    target_tool = mf.filament_a if assignment == 0 else mf.filament_b
                    gcode_list[gcode_idx] = self._rewrite_tool_change(
                        gcode_list[gcode_idx], mf, target_tool)
                else:
                    ratio_a = assignment if isinstance(assignment, float) else mf.get_mix_ratio_for_layer(0, layer_info.z_height)
                    gcode_list[gcode_idx] = self._rewrite_mixing_hotend(
                        gcode_list[gcode_idx], mf, ratio_a)

        return gcode_list

    def _bresenham_schedule(self, mf: MixedFilament, layer_list: List[LayerInfo],
                            layer_height: float) -> List:
        """Use Bresenham-style error diffusion for smooth dither distribution."""
        if mf.output_mode != "tool_change":
            return [mf.get_mix_ratio_for_layer(i, li.z_height)
                    for i, li in enumerate(layer_list)]

        if mf.gradient and mf.gradient.enabled and mf.gradient.keyframes:
            return self._bresenham_gradient(mf, layer_list)

        return self._bresenham_fixed_ratio(mf, len(layer_list))

    def _bresenham_fixed_ratio(self, mf: MixedFilament, num_layers: int) -> List[int]:
        cycle = mf.pattern.get_cycle()
        total = len(cycle)
        count_a = sum(1 for x in cycle if x == 0)

        if count_a == 0:
            return [1] * num_layers
        if count_a == total:
            return [0] * num_layers

        result = []
        error = 0.0
        ratio_a = count_a / total

        for _ in range(num_layers):
            error += ratio_a
            if error >= 0.5:
                result.append(0)
                error -= 1.0
            else:
                result.append(1)

        return result

    def _bresenham_gradient(self, mf: MixedFilament, layer_list: List[LayerInfo]) -> List[int]:
        result = []
        error = 0.0

        for layer_info in layer_list:
            ratio_a = mf.gradient.interpolate_ratio(layer_info.z_height)
            error += ratio_a
            if error >= 0.5:
                result.append(0)
                error -= 1.0
            else:
                result.append(1)

        return result

    # -- Temperature Pre-heating --

    def _insert_preheat_commands(self, gcode_list: List[str], all_layers: List[LayerInfo],
                                  applicable_layers: List[LayerInfo],
                                  schedule: List, mf: MixedFilament) -> List[str]:
        layer_index_map = {l.gcode_index: i for i, l in enumerate(all_layers)}

        prev_tool = None
        for layer_info, assignment in zip(applicable_layers, schedule):
            target_tool = mf.filament_a if assignment == 0 else mf.filament_b
            if target_tool != prev_tool and prev_tool is not None:
                current_global_idx = layer_index_map.get(layer_info.gcode_index)
                if current_global_idx is not None:
                    preheat_global_idx = max(0, current_global_idx - self.preheat_layers)
                    preheat_layer = all_layers[preheat_global_idx]

                    if preheat_layer.gcode_index < layer_info.gcode_index:
                        target_temp = self.extruder_temperatures.get(target_tool)
                        if target_temp is not None:
                            preheat_cmd = f"M104 S{target_temp:.0f} T{target_tool} ;MixedColor preheat\n"
                            gcode_list[preheat_layer.gcode_index] = self._insert_after_layer_comment(
                                gcode_list[preheat_layer.gcode_index], preheat_cmd
                            )
            prev_tool = target_tool

        return gcode_list

    def _insert_after_layer_comment(self, gcode: str, command: str) -> str:
        lines = gcode.split("\n")
        result = []
        inserted = False
        for line in lines:
            result.append(line)
            if line.startswith(";LAYER:") and not inserted:
                result.append(command.rstrip())
                inserted = True
        if not inserted:
            result.insert(0, command.rstrip())
        return "\n".join(result)

    # -- Tool Change Rewriting --

    def _rewrite_tool_change(self, gcode: str, mf: MixedFilament,
                              target_extruder: int) -> str:
        """Replace proxy extruder Tn with the target physical extruder."""
        comment = f" ;MixedColor:{mf.name}"

        def replace_tool(match):
            tool_num = int(match.group(2))
            if tool_num == mf.proxy_extruder:
                return f"T{target_extruder}{comment}"
            return match.group(0)

        return self.TOOL_CMD_PATTERN.sub(replace_tool, gcode)

    def _rewrite_mixing_hotend(self, gcode: str, mf: MixedFilament,
                                ratio_a: float) -> str:
        ratio_b = 1.0 - ratio_a
        comment = f" ;MixedColor:{mf.name}"

        if mf.mix_gcode == "marlin_m163":
            mix_commands = self._generate_marlin_mix(mf.filament_a, mf.filament_b,
                                                      ratio_a, ratio_b)
        else:
            mix_commands = self._generate_reprap_mix(mf.filament_a, mf.filament_b,
                                                      ratio_a, ratio_b)

        def replace_tool(match):
            tool_num = int(match.group(2))
            if tool_num == mf.proxy_extruder:
                return mix_commands + f"\nT{tool_num}{comment}"
            return match.group(0)

        return self.TOOL_CMD_PATTERN.sub(replace_tool, gcode)

    def _generate_marlin_mix(self, extruder_a: int, extruder_b: int,
                              ratio_a: float, ratio_b: float) -> str:
        lines = []
        if ratio_a >= 0.999:
            lines.append(f"M163 S{extruder_a} P1")
            lines.append(f"M163 S{extruder_b} P0")
        elif ratio_a <= 0.001:
            lines.append(f"M163 S{extruder_a} P0")
            lines.append(f"M163 S{extruder_b} P1")
        else:
            lines.append(f"M163 S{extruder_a} P{ratio_a:.2f}")
            lines.append(f"M163 S{extruder_b} P{ratio_b:.2f}")
        lines.append("M164 S2")
        return "\n".join(lines)

    def _generate_reprap_mix(self, extruder_a: int, extruder_b: int,
                              ratio_a: float, ratio_b: float) -> str:
        return f"M567 P0 E{ratio_a:.2f}:{ratio_b:.2f}"
