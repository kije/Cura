# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import sys
import os
import types
import unittest

# Mock UM.Logger before any imports that reference it
_um_module = types.ModuleType("UM")
_um_logger_module = types.ModuleType("UM.Logger")


class _MockLogger:
    @staticmethod
    def log(*args, **kwargs):
        pass

    @staticmethod
    def logException(*args, **kwargs):
        pass


_um_logger_module.Logger = _MockLogger
sys.modules["UM"] = _um_module
sys.modules["UM.Logger"] = _um_logger_module

# Import modules directly to avoid triggering __init__.py which needs PyQt6
_plugin_dir = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _plugin_dir)

# We need to handle the relative imports in GCodeProcessor.
# Trick: create a fake package structure so relative imports work.
import importlib.util


def _load_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load models first (no relative imports)
_load_module("models", os.path.join(_plugin_dir, "models", "__init__.py"))
DitherPattern_mod = _load_module("models.DitherPattern", os.path.join(_plugin_dir, "models", "DitherPattern.py"))
GradientProfile_mod = _load_module("models.GradientProfile", os.path.join(_plugin_dir, "models", "GradientProfile.py"))
MixedFilament_mod = _load_module("models.MixedFilament", os.path.join(_plugin_dir, "models", "MixedFilament.py"))

# Load core modules
_load_module("core", os.path.join(_plugin_dir, "core", "__init__.py"))
LayerAnalyzer_mod = _load_module("core.LayerAnalyzer", os.path.join(_plugin_dir, "core", "LayerAnalyzer.py"))

LayerAnalyzer = LayerAnalyzer_mod.LayerAnalyzer
LayerInfo = LayerAnalyzer_mod.LayerInfo
MixedFilament = MixedFilament_mod.MixedFilament
DitherPattern = DitherPattern_mod.DitherPattern

# Now load GCodeProcessor - it has relative imports that won't work standalone.
# Use exec with the necessary names injected.
_gcode_proc_path = os.path.join(_plugin_dir, "core", "GCodeProcessor.py")
with open(_gcode_proc_path) as f:
    _src = f.read()
# Strip relative imports
_src = _src.replace("from ..models.MixedFilament import MixedFilament", "")
_src = _src.replace("from ..models.DitherPattern import DitherPattern", "")
_src = _src.replace("from .LayerAnalyzer import LayerAnalyzer, LayerInfo", "")
_src = _src.replace("from UM.Logger import Logger", "")
_gcode_ns = {
    "__name__": "core.GCodeProcessor",
    "__builtins__": __builtins__,
    "re": __import__("re"),
    "Dict": dict,
    "List": list,
    "Optional": type(None),
    "Logger": _MockLogger,
    "MixedFilament": MixedFilament,
    "DitherPattern": DitherPattern,
    "LayerAnalyzer": LayerAnalyzer,
    "LayerInfo": LayerInfo,
}
exec(compile(_src, _gcode_proc_path, "exec"), _gcode_ns)
GCodeProcessor = _gcode_ns["GCodeProcessor"]


# Sample G-code that mimics Cura output
SAMPLE_GCODE = [
    # Header (index 0)
    ";FLAVOR:Marlin\n;TIME:1234\n;Layer height: 0.2\n;MINX:10\nG28\nG1 Z5 F5000\nT0\n",
    # Layer 0 (index 1) - uses T2 (proxy extruder)
    ";LAYER:0\n;TYPE:WALL-OUTER\nT2\nG0 F6000 X10 Y10 Z0.2\nG1 F1200 X20 Y10 E0.5\nG1 X20 Y20 E1.0\n",
    # Layer 1 (index 2) - uses T2
    ";LAYER:1\n;TYPE:WALL-OUTER\nT2\nG0 F6000 X10 Y10 Z0.4\nG1 F1200 X20 Y10 E1.5\nG1 X20 Y20 E2.0\n",
    # Layer 2 (index 3) - uses T2
    ";LAYER:2\n;TYPE:WALL-OUTER\nT2\nG0 F6000 X10 Y10 Z0.6\nG1 F1200 X20 Y10 E2.5\n",
    # Layer 3 (index 4) - uses T2
    ";LAYER:3\n;TYPE:WALL-OUTER\nT2\nG0 F6000 X10 Y10 Z0.8\nG1 F1200 X20 Y10 E3.0\n",
    # Layer 4 (index 5) - uses T0 (NOT proxy, should be untouched)
    ";LAYER:4\n;TYPE:WALL-OUTER\nT0\nG0 F6000 X10 Y10 Z1.0\nG1 F1200 X20 Y10 E3.5\n",
    # Layer 5 (index 6) - uses T2 again
    ";LAYER:5\n;TYPE:WALL-OUTER\nT2\nG0 F6000 X10 Y10 Z1.2\nG1 F1200 X20 Y10 E4.0\n",
]


class TestLayerAnalyzer(unittest.TestCase):

    def test_parse_layers(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE)
        self.assertEqual(len(layers), 6)  # 6 layers (0-5)

    def test_layer_numbers(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE)
        self.assertEqual([l.layer_number for l in layers], [0, 1, 2, 3, 4, 5])

    def test_layer_tools(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE)
        tools = [l.active_tool for l in layers]
        # Layers 0-3 and 5 use T2, layer 4 uses T0
        self.assertEqual(tools, [2, 2, 2, 2, 0, 2])

    def test_layer_height_extracted(self):
        analyzer = LayerAnalyzer()
        analyzer.parse(SAMPLE_GCODE)
        self.assertAlmostEqual(analyzer.layer_height, 0.2)

    def test_z_heights(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE)
        self.assertAlmostEqual(layers[0].z_height, 0.2)
        self.assertAlmostEqual(layers[1].z_height, 0.4)

    def test_get_layers_for_tool(self):
        analyzer = LayerAnalyzer()
        analyzer.parse(SAMPLE_GCODE)
        t2_layers = analyzer.get_layers_for_tool(2)
        self.assertEqual(len(t2_layers), 5)
        t0_layers = analyzer.get_layers_for_tool(0)
        self.assertEqual(len(t0_layers), 1)


class TestGCodeProcessorToolChange(unittest.TestCase):
    """Tests for IDEX/tool-change mode G-code rewriting."""

    def _make_mix(self, ratio_a=1, ratio_b=1):
        pattern = DitherPattern(mode="ratio", ratio_a=ratio_a, ratio_b=ratio_b)
        return MixedFilament(
            name="TestMix",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=pattern,
            output_mode="tool_change",
            enabled=True,
        )

    def test_alternating_1_1(self):
        """With 1:1 ratio, layers should alternate T0 and T1."""
        mf = self._make_mix(ratio_a=1, ratio_b=1)
        gcode = [block for block in SAMPLE_GCODE]  # Copy

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # Layer 0 (proxy layer 0): pattern [0,1] → 0 → T0
        self.assertIn("T0", result[1])
        self.assertNotIn("T2", result[1])

        # Layer 1 (proxy layer 1): pattern [0,1] → 1 → T1
        self.assertIn("T1", result[2])
        self.assertNotIn("T2", result[2])

        # Layer 2 (proxy layer 2): pattern wraps → 0 → T0
        self.assertIn("T0", result[3])

        # Layer 3 (proxy layer 3): pattern wraps → 1 → T1
        self.assertIn("T1", result[4])

    def test_non_proxy_layers_untouched(self):
        """Layers not using the proxy extruder should remain unchanged."""
        mf = self._make_mix()
        gcode = [block for block in SAMPLE_GCODE]

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # Layer 4 uses T0, should stay T0
        self.assertIn("T0", result[5])

    def test_header_untouched(self):
        """The header block should not be modified."""
        mf = self._make_mix()
        gcode = [block for block in SAMPLE_GCODE]
        original_header = gcode[0]

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        self.assertEqual(result[0], original_header)

    def test_geometry_preserved(self):
        """G-code move commands should be preserved."""
        mf = self._make_mix()
        gcode = [block for block in SAMPLE_GCODE]

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # Check that geometry commands are still present
        self.assertIn("G1 F1200 X20 Y10 E0.5", result[1])
        self.assertIn("G1 X20 Y20 E1.0", result[1])

    def test_2_1_ratio(self):
        """With 2:1 ratio, two layers of A then one of B."""
        mf = self._make_mix(ratio_a=2, ratio_b=1)
        gcode = [block for block in SAMPLE_GCODE]

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # Pattern: [A, A, B, A, A, B, ...]
        # Proxy layers: 0→T0, 1→T0, 2→T1, 3→T0, 4→T0 (layer4 is not proxy)
        self.assertIn("T0", result[1])  # proxy layer 0 → A
        self.assertIn("T0", result[2])  # proxy layer 1 → A
        self.assertIn("T1", result[3])  # proxy layer 2 → B
        self.assertIn("T0", result[4])  # proxy layer 3 → A (new cycle)

    def test_mixed_color_comment_added(self):
        """Processed layers should have a MixedColor comment."""
        mf = self._make_mix()
        gcode = [block for block in SAMPLE_GCODE]

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        self.assertIn(";MixedColor:TestMix", result[1])

    def test_no_mixed_filaments_passthrough(self):
        """Empty mixed filament list should return unmodified G-code."""
        gcode = [block for block in SAMPLE_GCODE]

        processor = GCodeProcessor()
        result = processor.process(gcode, [])

        for i in range(len(gcode)):
            self.assertEqual(result[i], gcode[i])


class TestGCodeProcessorMixingHotend(unittest.TestCase):
    """Tests for mixing hotend mode G-code rewriting."""

    def _make_mix_marlin(self, ratio_a=1, ratio_b=1):
        pattern = DitherPattern(mode="ratio", ratio_a=ratio_a, ratio_b=ratio_b)
        return MixedFilament(
            name="MarlinMix",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=pattern,
            output_mode="mixing",
            mix_gcode="marlin_m163",
            enabled=True,
        )

    def _make_mix_reprap(self):
        pattern = DitherPattern(mode="ratio", ratio_a=1, ratio_b=1)
        return MixedFilament(
            name="RepRapMix",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=pattern,
            output_mode="mixing",
            mix_gcode="reprap_m567",
            enabled=True,
        )

    def test_marlin_m163_injected(self):
        """Marlin mode should inject M163/M164 commands."""
        mf = self._make_mix_marlin()
        gcode = [block for block in SAMPLE_GCODE]

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # Should contain M163 and M164 commands
        self.assertIn("M163", result[1])
        self.assertIn("M164", result[1])
        # T2 should still be present (it's the virtual tool)
        self.assertIn("T2", result[1])

    def test_reprap_m567_injected(self):
        """RepRap mode should inject M567 commands."""
        mf = self._make_mix_reprap()
        gcode = [block for block in SAMPLE_GCODE]

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        self.assertIn("M567", result[1])
        self.assertIn("T2", result[1])

    def test_marlin_ratio_values(self):
        """Marlin M163 should have correct ratio values for 1:1 mix."""
        mf = self._make_mix_marlin(ratio_a=1, ratio_b=1)
        gcode = [block for block in SAMPLE_GCODE]

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # 1:1 ratio = 0.50 each
        self.assertIn("M163 S0 P0.50", result[1])
        self.assertIn("M163 S1 P0.50", result[1])
        self.assertIn("M164 S2", result[1])


if __name__ == "__main__":
    unittest.main()
