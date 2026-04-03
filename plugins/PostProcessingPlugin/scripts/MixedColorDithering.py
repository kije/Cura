# Mixed Color Dithering - Layer-based color mixing via filament alternation
# This script alternates tool changes between layers to create the visual
# appearance of mixed colors on IDEX/tool-changer or mixing hotend printers.
#
# For the full-featured version with UI, gradients, and per-object assignment,
# use the Mixed Colors Extension plugin (Extensions > Mixed Colors).
#
# Released under the terms of the LGPLv3 or higher.

import re
from typing import List
from ..Script import Script


class MixedColorDithering(Script):
    def __init__(self):
        super().__init__()

    def getSettingDataString(self):
        return """{
            "name": "Mixed Color Dithering",
            "key": "MixedColorDithering",
            "metadata": {},
            "version": 2,
            "settings":
            {
                "extruder_a":
                {
                    "label": "Extruder A",
                    "description": "First physical extruder (0-based index).",
                    "type": "int",
                    "default_value": 0,
                    "minimum_value": 0
                },
                "extruder_b":
                {
                    "label": "Extruder B",
                    "description": "Second physical extruder (0-based index).",
                    "type": "int",
                    "default_value": 1,
                    "minimum_value": 0
                },
                "output_mode":
                {
                    "label": "Output Mode",
                    "description": "IDEX alternates tool changes. Mixing Hotend sets mix ratios via M163/M164.",
                    "type": "enum",
                    "options": {"tool_change": "IDEX / Tool Changer", "mixing_marlin": "Mixing Hotend (Marlin M163)", "mixing_reprap": "Mixing Hotend (RepRap M567)"},
                    "default_value": "tool_change"
                },
                "pattern_mode":
                {
                    "label": "Pattern Mode",
                    "description": "Ratio: auto-generate from ratio. Custom: explicit pattern string.",
                    "type": "enum",
                    "options": {"ratio": "Ratio", "custom": "Custom Pattern"},
                    "default_value": "ratio"
                },
                "ratio_a":
                {
                    "label": "Ratio A (layers)",
                    "description": "Number of consecutive layers for extruder A per cycle.",
                    "type": "int",
                    "default_value": 1,
                    "minimum_value": 1,
                    "maximum_value": 20,
                    "enabled": "pattern_mode == 'ratio'"
                },
                "ratio_b":
                {
                    "label": "Ratio B (layers)",
                    "description": "Number of consecutive layers for extruder B per cycle.",
                    "type": "int",
                    "default_value": 1,
                    "minimum_value": 1,
                    "maximum_value": 20,
                    "enabled": "pattern_mode == 'ratio'"
                },
                "custom_pattern":
                {
                    "label": "Custom Pattern",
                    "description": "Pattern string using A/B or 1/2. Example: AABB, 11212, A/B/A. Separators (/ - _ |) are ignored.",
                    "type": "str",
                    "default_value": "",
                    "enabled": "pattern_mode == 'custom'"
                },
                "use_bresenham":
                {
                    "label": "Bresenham Dithering",
                    "description": "Use Bresenham error diffusion for smoother distribution of A/B layers. Recommended for ratios other than 1:1.",
                    "type": "bool",
                    "default_value": true
                },
                "gradient_enabled":
                {
                    "label": "Enable Gradient",
                    "description": "Change the mix ratio over the height of the print.",
                    "type": "bool",
                    "default_value": false
                },
                "gradient_start_height":
                {
                    "label": "Gradient Start Height",
                    "description": "Z-height (mm) where the gradient begins.",
                    "type": "float",
                    "default_value": 0,
                    "minimum_value": 0,
                    "enabled": "gradient_enabled"
                },
                "gradient_end_height":
                {
                    "label": "Gradient End Height",
                    "description": "Z-height (mm) where the gradient ends.",
                    "type": "float",
                    "default_value": 30,
                    "minimum_value": 0,
                    "enabled": "gradient_enabled"
                },
                "gradient_start_ratio":
                {
                    "label": "Start Ratio A (%)",
                    "description": "Percentage of extruder A at the start of the gradient (0-100).",
                    "type": "float",
                    "default_value": 100,
                    "minimum_value": 0,
                    "maximum_value": 100,
                    "enabled": "gradient_enabled"
                },
                "gradient_end_ratio":
                {
                    "label": "End Ratio A (%)",
                    "description": "Percentage of extruder A at the end of the gradient (0-100).",
                    "type": "float",
                    "default_value": 0,
                    "minimum_value": 0,
                    "maximum_value": 100,
                    "enabled": "gradient_enabled"
                },
                "preheat_layers":
                {
                    "label": "Pre-heat Lookahead",
                    "description": "Start heating the next extruder this many layers before the switch. Set to 0 to disable.",
                    "type": "int",
                    "default_value": 3,
                    "minimum_value": 0,
                    "maximum_value": 20,
                    "enabled": "output_mode == 'tool_change'"
                }
            }
        }"""

    def execute(self, data: List[str]) -> List[str]:
        extruder_a = int(self.getSettingValueByKey("extruder_a"))
        extruder_b = int(self.getSettingValueByKey("extruder_b"))
        output_mode = self.getSettingValueByKey("output_mode")
        pattern_mode = self.getSettingValueByKey("pattern_mode")
        ratio_a = int(self.getSettingValueByKey("ratio_a"))
        ratio_b = int(self.getSettingValueByKey("ratio_b"))
        custom_pattern = self.getSettingValueByKey("custom_pattern")
        use_bresenham = self.getSettingValueByKey("use_bresenham")
        gradient_enabled = self.getSettingValueByKey("gradient_enabled")
        gradient_start_h = float(self.getSettingValueByKey("gradient_start_height"))
        gradient_end_h = float(self.getSettingValueByKey("gradient_end_height"))
        gradient_start_r = float(self.getSettingValueByKey("gradient_start_ratio")) / 100.0
        gradient_end_r = float(self.getSettingValueByKey("gradient_end_ratio")) / 100.0
        preheat_layers = int(self.getSettingValueByKey("preheat_layers"))

        # Parse pattern
        if pattern_mode == "custom" and custom_pattern.strip():
            cycle = self._parse_pattern(custom_pattern)
        else:
            cycle = [0] * ratio_a + [1] * ratio_b

        # Extract layer height
        layer_height = 0.2
        for block in data[:3]:
            match = re.search(r";Layer height:\s*([\d.]+)", block)
            if match:
                layer_height = float(match.group(1))
                break

        # Parse layers (global mode: process ALL layers)
        layers = []  # (data_index, layer_number, z_height)
        current_z = 0.0
        for idx, block in enumerate(data):
            layer_match = re.search(r";LAYER:(-?\d+)", block)
            if layer_match is None:
                continue
            layer_num = int(layer_match.group(1))
            z_matches = re.findall(r"G[01]\s.*?Z([\d.]+)", block)
            if z_matches:
                current_z = float(z_matches[0])
            layers.append((idx, layer_num, current_z))

        if not layers:
            return data

        # Compute schedule
        num_proxy_layers = len(layers)
        if gradient_enabled:
            schedule = self._gradient_schedule(
                layers, gradient_start_h, gradient_end_h,
                gradient_start_r, gradient_end_r, use_bresenham
            )
        elif use_bresenham:
            schedule = self._bresenham_schedule(cycle, num_proxy_layers)
        else:
            schedule = [cycle[i % len(cycle)] for i in range(num_proxy_layers)]

        # Insert preheat commands (tool change mode only)
        if output_mode == "tool_change" and preheat_layers > 0:
            data = self._insert_preheat(data, layers, schedule, extruder_a,
                                         extruder_b, preheat_layers)

        # Add processing marker
        data[0] += ";MIXED_COLOR_DITHERING_PROCESSED\n"

        # Apply changes - replace ALL Tn commands in each layer
        tool_pattern = re.compile(r"^(T)(\d+)\s*$", re.MULTILINE)

        for i, (data_idx, layer_num, z_height) in enumerate(layers):
            choice = schedule[i]

            if output_mode == "tool_change":
                target = extruder_a if choice == 0 else extruder_b
                data[data_idx] = tool_pattern.sub(
                    f"T{target} ;MixedColorDithering", data[data_idx]
                )
            else:
                # Mixing hotend mode
                if isinstance(choice, float):
                    ratio = choice
                else:
                    total = len(cycle)
                    count_a = sum(1 for x in cycle if x == 0)
                    ratio = count_a / total
                ratio_b_val = 1.0 - ratio

                if output_mode == "mixing_marlin":
                    mix_cmd = (f"M163 S{extruder_a} P{ratio:.2f}\n"
                              f"M163 S{extruder_b} P{ratio_b_val:.2f}\n"
                              f"M164 S2\n")
                else:  # mixing_reprap
                    mix_cmd = f"M567 P0 E{ratio:.2f}:{ratio_b_val:.2f}\n"

                def add_mix(match):
                    return mix_cmd + match.group(0).rstrip() + " ;MixedColorDithering"

                data[data_idx] = tool_pattern.sub(add_mix, data[data_idx])

        return data

    def _parse_pattern(self, pattern_str: str) -> List[int]:
        cleaned = re.sub(r"[/\-_|]", "", pattern_str.strip().upper())
        cycle = []
        for ch in cleaned:
            if ch in ("A", "1"):
                cycle.append(0)
            elif ch in ("B", "2"):
                cycle.append(1)
        return cycle if cycle else [0, 1]

    def _bresenham_schedule(self, cycle: List[int], num_layers: int) -> List[int]:
        total = len(cycle)
        count_a = sum(1 for x in cycle if x == 0)
        if count_a == 0:
            return [1] * num_layers
        if count_a == total:
            return [0] * num_layers

        result = []
        error = 0.0
        ratio = count_a / total
        for _ in range(num_layers):
            error += ratio
            if error >= 0.5:
                result.append(0)
                error -= 1.0
            else:
                result.append(1)
        return result

    def _gradient_schedule(self, layers, start_h, end_h, start_r, end_r,
                           use_bresenham) -> List:
        result = []
        error = 0.0
        for _, _, z in layers:
            if end_h <= start_h:
                ratio_a = start_r
            elif z <= start_h:
                ratio_a = start_r
            elif z >= end_h:
                ratio_a = end_r
            else:
                t = (z - start_h) / (end_h - start_h)
                ratio_a = start_r + t * (end_r - start_r)

            if use_bresenham:
                error += ratio_a
                if error >= 0.5:
                    result.append(0)
                    error -= 1.0
                else:
                    result.append(1)
            else:
                # For non-Bresenham, return float ratios (for mixing mode)
                result.append(ratio_a)
        return result

    def _insert_preheat(self, data, layers, schedule, ext_a, ext_b, lookahead):
        """Insert M104 preheat commands before tool changes."""
        # Get temperatures from Cura settings
        from UM.Application import Application
        stack = Application.getInstance().getGlobalContainerStack()
        temps = {}
        if stack:
            for idx, ext in enumerate(stack.extruderList):
                t = ext.getProperty("material_print_temperature", "value")
                if t:
                    temps[idx] = float(t)

        if not temps:
            return data

        prev_tool = None
        for i, (data_idx, _, _) in enumerate(layers):
            target = ext_a if schedule[i] == 0 else ext_b
            if target != prev_tool and prev_tool is not None:
                # Insert preheat N layers before this change
                preheat_idx = max(0, i - lookahead)
                ph_data_idx = layers[preheat_idx][0]
                if ph_data_idx < data_idx:
                    temp = temps.get(target)
                    if temp:
                        cmd = f"M104 S{temp:.0f} T{target} ;MixedColor preheat\n"
                        lines = data[ph_data_idx].split("\n")
                        for li, line in enumerate(lines):
                            if line.startswith(";LAYER:"):
                                lines.insert(li + 1, cmd.rstrip())
                                break
                        data[ph_data_idx] = "\n".join(lines)
            prev_tool = target

        return data
