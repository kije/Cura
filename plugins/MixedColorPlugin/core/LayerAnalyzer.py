# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import re
from typing import Dict, List, Optional


class LayerInfo:
    """Parsed information about a single G-code layer."""

    def __init__(self, index: int, layer_number: int, z_height: float,
                 active_tool: int, gcode_index: int) -> None:
        self.index = index            # Index in the gcode_list
        self.layer_number = layer_number  # Layer number from ;LAYER: comment
        self.z_height = z_height      # Z-height in mm
        self.active_tool = active_tool  # Active tool/extruder at start of layer
        self.gcode_index = gcode_index  # Index in gcode_list array

    def __repr__(self) -> str:
        return (f"LayerInfo(layer={self.layer_number}, z={self.z_height:.3f}, "
                f"tool=T{self.active_tool}, gcode_idx={self.gcode_index})")


class LayerAnalyzer:
    """Parses G-code layer structure from Cura's gcode_list format.

    Cura's gcode_list is a List[str] where:
    - gcode_list[0] = header/prefix (start G-code, settings comments)
    - gcode_list[1..N] = individual layers, each typically starting with ;LAYER:N
    """

    LAYER_PATTERN = re.compile(r";LAYER:(-?\d+)")
    Z_PATTERN = re.compile(r"G[01]\s.*?Z([\d.]+)")
    TOOL_PATTERN = re.compile(r"^T(\d+)", re.MULTILINE)
    LAYER_HEIGHT_PATTERN = re.compile(r";Layer height:\s*([\d.]+)")

    def __init__(self) -> None:
        self._layers: List[LayerInfo] = []
        self._layer_height: float = 0.2  # Default

    @property
    def layers(self) -> List[LayerInfo]:
        return self._layers

    @property
    def layer_height(self) -> float:
        return self._layer_height

    def parse(self, gcode_list: List[str]) -> List[LayerInfo]:
        """Parse the gcode_list and extract layer information.

        Returns a list of LayerInfo objects, one per detected layer.
        """
        self._layers = []
        self._extract_layer_height(gcode_list)

        current_tool = 0
        layer_idx = 0

        for gcode_index, gcode_block in enumerate(gcode_list):
            # Look for layer marker
            layer_match = self.LAYER_PATTERN.search(gcode_block)
            if layer_match is None:
                # Still track tool changes in non-layer blocks (e.g., header)
                tool_matches = self.TOOL_PATTERN.findall(gcode_block)
                if tool_matches:
                    current_tool = int(tool_matches[-1])
                continue

            layer_number = int(layer_match.group(1))

            # Extract Z height from G0/G1 moves in this block
            z_height = self._extract_z_height(gcode_block, layer_number)

            # Find the first tool command in this layer
            tool_matches = self.TOOL_PATTERN.findall(gcode_block)
            if tool_matches:
                current_tool = int(tool_matches[0])

            info = LayerInfo(
                index=layer_idx,
                layer_number=layer_number,
                z_height=z_height,
                active_tool=current_tool,
                gcode_index=gcode_index,
            )
            self._layers.append(info)

            # Track tool at end of layer for next layer's starting tool
            if tool_matches:
                current_tool = int(tool_matches[-1])

            layer_idx += 1

        return self._layers

    def get_layers_for_tool(self, tool_index: int) -> List[LayerInfo]:
        """Return all layers that use a specific tool/extruder."""
        return [layer for layer in self._layers if layer.active_tool == tool_index]

    def _extract_layer_height(self, gcode_list: List[str]) -> None:
        """Extract the layer height from G-code header comments."""
        for block in gcode_list[:3]:  # Usually in the first few blocks
            match = self.LAYER_HEIGHT_PATTERN.search(block)
            if match:
                self._layer_height = float(match.group(1))
                return

    def _extract_z_height(self, gcode_block: str, layer_number: int) -> float:
        """Extract Z-height from G-code moves, or estimate from layer number."""
        z_matches = self.Z_PATTERN.findall(gcode_block)
        if z_matches:
            return float(z_matches[0])
        # Estimate from layer number
        return max(0.0, layer_number * self._layer_height)
