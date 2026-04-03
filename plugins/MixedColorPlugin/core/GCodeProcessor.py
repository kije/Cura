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

    Features:
    - IDEX/tool-change mode: replaces Tn with the appropriate physical extruder
    - Mixing hotend mode: injects M163/M164 (Marlin) or M567 (RepRap) commands
    - Bresenham error diffusion for smoother gradient distribution
    - Temperature pre-heating with N-layer lookahead
    - Per-object (;MESH:) based mixed filament assignment
    """

    TOOL_CMD_PATTERN = re.compile(r"^(T)(\d+)\s*$", re.MULTILINE)
    MESH_CMD_PATTERN = re.compile(r"^;MESH:(.+)$", re.MULTILINE)

    def __init__(self, preheat_layers: int = 3,
                 extruder_temperatures: Optional[Dict[int, float]] = None,
                 standby_temperature: float = 150.0) -> None:
        """Initialize the processor.

        Args:
            preheat_layers: How many layers ahead to start pre-heating the next extruder
            extruder_temperatures: Map of extruder index -> print temperature (C)
            standby_temperature: Temperature for idle extruders (C)
        """
        self.preheat_layers = preheat_layers
        self.extruder_temperatures = extruder_temperatures or {}
        self.standby_temperature = standby_temperature

    def process(self, gcode_list: List[str], mixed_filaments: List[MixedFilament],
                mesh_assignments: Optional[Dict[str, int]] = None) -> List[str]:
        """Process the full G-code list, applying all mixed filament definitions.

        Args:
            gcode_list: Cura's G-code list (header + layers)
            mixed_filaments: List of active MixedFilament definitions
            mesh_assignments: Optional map of mesh_name -> mixed_filament index
                for per-object assignment. If None, uses proxy extruder matching.

        Returns:
            Modified gcode_list with dithered tool commands
        """
        if not mixed_filaments:
            return gcode_list

        # Build maps for quick lookup
        proxy_map: Dict[int, MixedFilament] = {}
        id_map: Dict[str, MixedFilament] = {}
        for mf in mixed_filaments:
            if mf.enabled:
                proxy_map[mf.proxy_extruder] = mf
                id_map[mf.id] = mf

        if not proxy_map:
            return gcode_list

        # Parse layer structure
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(gcode_list)
        layer_height = analyzer.layer_height

        Logger.log("d", f"MixedColor: Found {len(layers)} layers, "
                   f"layer_height={layer_height}, "
                   f"proxy extruders: {list(proxy_map.keys())}, "
                   f"meshes: {analyzer.get_all_mesh_names()}")

        # Compute the full tool schedule for all layers
        schedule = self._compute_schedule(layers, proxy_map, mesh_assignments,
                                          id_map, layer_height)

        # Insert pre-heat commands based on lookahead
        if self.preheat_layers > 0 and self.extruder_temperatures:
            gcode_list = self._insert_preheat_commands(gcode_list, layers, schedule)

        # Apply tool changes
        for layer_info in layers:
            gcode_idx = layer_info.gcode_index
            if gcode_idx not in schedule:
                continue

            entry = schedule[gcode_idx]
            mf = entry[0]
            original_gcode = gcode_list[gcode_idx]

            if mf.output_mode == "tool_change":
                target_tool = entry[1]
                modified = self._rewrite_tool_change(original_gcode, mf, target_tool)
            else:
                ratio_a = entry[2] if len(entry) > 2 else mf.pattern.get_ratio_fraction()
                modified = self._rewrite_mixing_hotend(original_gcode, mf, ratio_a)

            gcode_list[gcode_idx] = modified

        return gcode_list

    def _compute_schedule(self, layers: List[LayerInfo],
                          proxy_map: Dict[int, MixedFilament],
                          mesh_assignments: Optional[Dict[str, int]],
                          id_map: Dict[str, MixedFilament],
                          layer_height: float) -> Dict:
        """Compute the tool assignment for each layer using Bresenham error diffusion.

        Returns a dict mapping gcode_index -> (MixedFilament, target_tool)
        or gcode_index -> (MixedFilament, target_tool, ratio_a) for mixing mode.
        """
        schedule = {}

        # Group layers by their mixed filament assignment
        mf_layers: Dict[str, List[LayerInfo]] = {}  # mf.id -> list of layers

        for layer_info in layers:
            mf = None

            # First check per-object mesh assignments
            if mesh_assignments:
                for mesh_name in layer_info.get_meshes():
                    if mesh_name in mesh_assignments:
                        mf_id = mesh_assignments[mesh_name]
                        if mf_id in id_map:
                            mf = id_map[mf_id]
                            break

            # Fall back to proxy extruder matching
            if mf is None and layer_info.active_tool in proxy_map:
                mf = proxy_map[layer_info.active_tool]

            if mf is None:
                continue

            if mf.id not in mf_layers:
                mf_layers[mf.id] = []
            mf_layers[mf.id].append(layer_info)

        # For each mixed filament, compute assignments using Bresenham diffusion
        for mf_id, layer_list in mf_layers.items():
            mf = id_map[mf_id]
            assignments = self._bresenham_schedule(mf, layer_list, layer_height)

            for layer_info, assignment in zip(layer_list, assignments):
                if mf.output_mode == "tool_change":
                    target_tool = mf.filament_a if assignment == 0 else mf.filament_b
                    schedule[layer_info.gcode_index] = (mf, target_tool)
                else:
                    # For mixing mode, store the ratio
                    ratio_a = assignment  # This is the float ratio from Bresenham
                    schedule[layer_info.gcode_index] = (mf, mf.proxy_extruder, ratio_a)

        return schedule

    def _bresenham_schedule(self, mf: MixedFilament, layer_list: List[LayerInfo],
                            layer_height: float) -> List:
        """Use Bresenham-style error diffusion for smoother gradient distribution.

        For tool-change mode: returns list of 0 (filament A) or 1 (filament B).
        For mixing mode: returns list of float ratios.

        The Bresenham approach distributes filament choices evenly across layers,
        avoiding the clumping that simple cycle-based patterns produce.
        For example, with a 2:5 ratio (29% A), instead of [A,A,B,B,B,B,B] repeated,
        it produces [B,A,B,B,A,B,B] which distributes A layers more evenly.
        """
        if mf.output_mode != "tool_change":
            # For mixing mode, just return interpolated ratios
            return [mf.get_mix_ratio_for_layer(i, li.z_height)
                    for i, li in enumerate(layer_list)]

        # For tool-change mode with gradient
        if mf.gradient and mf.gradient.enabled and mf.gradient.keyframes:
            return self._bresenham_gradient(mf, layer_list, layer_height)

        # For tool-change mode without gradient: use standard Bresenham
        return self._bresenham_fixed_ratio(mf, len(layer_list))

    def _bresenham_fixed_ratio(self, mf: MixedFilament, num_layers: int) -> List[int]:
        """Bresenham error diffusion for a fixed ratio pattern.

        Distributes A/B choices evenly without the clumping of simple repetition.
        """
        cycle = mf.pattern.get_cycle()
        total = len(cycle)
        count_a = sum(1 for x in cycle if x == 0)

        if count_a == 0:
            return [1] * num_layers
        if count_a == total:
            return [0] * num_layers

        # Bresenham line algorithm: distribute count_a choices over total slots
        result = []
        error = 0.0
        ratio_a = count_a / total

        for _ in range(num_layers):
            error += ratio_a
            if error >= 0.5:
                result.append(0)  # Filament A
                error -= 1.0
            else:
                result.append(1)  # Filament B

        return result

    def _bresenham_gradient(self, mf: MixedFilament, layer_list: List[LayerInfo],
                            layer_height: float) -> List[int]:
        """Bresenham error diffusion with height-varying gradient ratio.

        At each layer, the target ratio comes from the gradient profile.
        The accumulated error ensures smooth transitions.
        """
        result = []
        error = 0.0

        for layer_info in layer_list:
            ratio_a = mf.gradient.interpolate_ratio(layer_info.z_height)
            error += ratio_a
            if error >= 0.5:
                result.append(0)  # Filament A
                error -= 1.0
            else:
                result.append(1)  # Filament B

        return result

    # -- Temperature Pre-heating --

    def _insert_preheat_commands(self, gcode_list: List[str], layers: List[LayerInfo],
                                  schedule: Dict) -> List[str]:
        """Insert M104 pre-heat commands N layers before a tool change occurs.

        Looks ahead in the schedule to find upcoming tool changes and inserts
        non-blocking heat commands (M104 Sn Tn) to start heating the next
        extruder early, reducing wait time.
        """
        # Build ordered list of (gcode_index, target_tool) from schedule
        tool_changes = []
        prev_tool = None
        for layer in layers:
            gcode_idx = layer.gcode_index
            if gcode_idx in schedule:
                entry = schedule[gcode_idx]
                target_tool = entry[1]
                if target_tool != prev_tool:
                    tool_changes.append((layer.index, gcode_idx, target_tool))
                prev_tool = target_tool

        if not tool_changes:
            return gcode_list

        # For each tool change, insert preheat N layers before
        for change_layer_idx, change_gcode_idx, target_tool in tool_changes:
            preheat_layer_idx = max(0, change_layer_idx - self.preheat_layers)

            # Find the layer info for the preheat point
            if preheat_layer_idx >= len(layers):
                continue
            preheat_layer = layers[preheat_layer_idx]
            preheat_gcode_idx = preheat_layer.gcode_index

            # Don't preheat if we're already at or past the change
            if preheat_gcode_idx >= change_gcode_idx:
                continue

            target_temp = self.extruder_temperatures.get(target_tool)
            if target_temp is None:
                continue

            # Insert M104 (non-blocking heat) at the start of the preheat layer
            preheat_cmd = f"M104 S{target_temp:.0f} T{target_tool} ;MixedColor preheat\n"
            gcode_list[preheat_gcode_idx] = self._insert_after_layer_comment(
                gcode_list[preheat_gcode_idx], preheat_cmd
            )

        return gcode_list

    def _insert_after_layer_comment(self, gcode: str, command: str) -> str:
        """Insert a command right after the ;LAYER: comment line."""
        lines = gcode.split("\n")
        result = []
        inserted = False
        for line in lines:
            result.append(line)
            if line.startswith(";LAYER:") and not inserted:
                result.append(command.rstrip())
                inserted = True
        if not inserted:
            # Fallback: insert at beginning
            result.insert(0, command.rstrip())
        return "\n".join(result)

    # -- Tool Change Rewriting --

    def _rewrite_tool_change(self, gcode: str, mf: MixedFilament,
                              target_extruder: int) -> str:
        """Rewrite tool commands for IDEX/tool-changer mode."""
        comment = f" ;MixedColor:{mf.name}"

        def replace_tool(match):
            tool_letter = match.group(1)
            tool_num = int(match.group(2))
            if tool_num == mf.proxy_extruder:
                return f"{tool_letter}{target_extruder}{comment}"
            return match.group(0)

        return self.TOOL_CMD_PATTERN.sub(replace_tool, gcode)

    def _rewrite_mixing_hotend(self, gcode: str, mf: MixedFilament,
                                ratio_a: float) -> str:
        """Rewrite tool commands for mixing hotend mode."""
        ratio_b = 1.0 - ratio_a
        proxy = mf.proxy_extruder
        comment = f" ;MixedColor:{mf.name}"

        if mf.mix_gcode == "marlin_m163":
            mix_commands = self._generate_marlin_mix(mf.filament_a, mf.filament_b,
                                                      ratio_a, ratio_b, proxy)
        else:
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
        """Generate Marlin M163/M164 mixing commands."""
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
        lines.append(f"M164 S{virtual_tool}")
        return "\n".join(lines)

    def _generate_reprap_mix(self, extruder_a: int, extruder_b: int,
                              ratio_a: float, ratio_b: float, virtual_tool: int) -> str:
        """Generate RepRap M567 mixing command."""
        return f"M567 P{virtual_tool} E{ratio_a:.2f}:{ratio_b:.2f}"
