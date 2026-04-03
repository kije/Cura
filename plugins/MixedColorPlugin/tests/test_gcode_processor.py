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
MeshSection = LayerAnalyzer_mod.MeshSection
MixedFilament = MixedFilament_mod.MixedFilament
DitherPattern = DitherPattern_mod.DitherPattern
GradientProfile = GradientProfile_mod.GradientProfile
GradientKeyframe = GradientProfile_mod.GradientKeyframe

# Now load GCodeProcessor with exec to handle relative imports
_gcode_proc_path = os.path.join(_plugin_dir, "core", "GCodeProcessor.py")
with open(_gcode_proc_path) as f:
    _src = f.read()
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
    "Tuple": tuple,
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

# Sample with ;MESH: comments
SAMPLE_GCODE_MESH = [
    ";FLAVOR:Marlin\n;Layer height: 0.2\nG28\nT0\n",
    ";LAYER:0\n;MESH:Cube.stl\n;TYPE:WALL-OUTER\nT2\nG1 X10 Y10 Z0.2 E0.5\n;MESH:Sphere.stl\n;TYPE:WALL-OUTER\nT0\nG1 X30 Y30 E1.0\n",
    ";LAYER:1\n;MESH:Cube.stl\n;TYPE:WALL-OUTER\nT2\nG1 X10 Y10 Z0.4 E1.5\n;MESH:Sphere.stl\n;TYPE:WALL-OUTER\nT0\nG1 X30 Y30 E2.0\n",
    ";LAYER:2\n;MESH:Cube.stl\n;TYPE:WALL-OUTER\nT2\nG1 X10 Y10 Z0.6 E2.5\n",
]


class TestLayerAnalyzer(unittest.TestCase):

    def test_parse_layers(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE)
        self.assertEqual(len(layers), 6)

    def test_layer_numbers(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE)
        self.assertEqual([l.layer_number for l in layers], [0, 1, 2, 3, 4, 5])

    def test_layer_tools(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE)
        tools = [l.active_tool for l in layers]
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
        self.assertEqual(len(analyzer.get_layers_for_tool(2)), 5)
        self.assertEqual(len(analyzer.get_layers_for_tool(0)), 1)


class TestLayerAnalyzerMesh(unittest.TestCase):

    def test_mesh_sections_parsed(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE_MESH)
        # Layer 0 should have Cube.stl and Sphere.stl sections
        self.assertGreater(len(layers[0].mesh_sections), 0)

    def test_get_meshes(self):
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(SAMPLE_GCODE_MESH)
        meshes = layers[0].get_meshes()
        self.assertIn("Cube.stl", meshes)
        self.assertIn("Sphere.stl", meshes)

    def test_get_all_mesh_names(self):
        analyzer = LayerAnalyzer()
        analyzer.parse(SAMPLE_GCODE_MESH)
        names = analyzer.get_all_mesh_names()
        self.assertIn("Cube.stl", names)
        self.assertIn("Sphere.stl", names)

    def test_get_layers_for_mesh(self):
        analyzer = LayerAnalyzer()
        analyzer.parse(SAMPLE_GCODE_MESH)
        cube_layers = analyzer.get_layers_for_mesh("Cube.stl")
        self.assertEqual(len(cube_layers), 3)  # All 3 layers have Cube.stl

    def test_nonmesh_excluded(self):
        gcode = [
            ";FLAVOR:Marlin\n;Layer height: 0.2\n",
            ";LAYER:0\n;MESH:NONMESH\nG0 X0 Y0\n;MESH:Cube.stl\nT2\nG1 X10 Y10 E0.5\n",
        ]
        analyzer = LayerAnalyzer()
        layers = analyzer.parse(gcode)
        meshes = layers[0].get_meshes()
        self.assertIn("Cube.stl", meshes)
        self.assertNotIn("NONMESH", meshes)


class TestGCodeProcessorToolChange(unittest.TestCase):

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
        mf = self._make_mix(ratio_a=1, ratio_b=1)
        gcode = [block for block in SAMPLE_GCODE]
        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # With Bresenham at 1:1, layers alternate T0 and T1
        self.assertIn("T0", result[1])
        self.assertNotIn("T2", result[1])
        self.assertIn("T1", result[2])
        self.assertNotIn("T2", result[2])

    def test_non_proxy_layers_untouched(self):
        mf = self._make_mix()
        gcode = [block for block in SAMPLE_GCODE]
        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])
        self.assertIn("T0", result[5])

    def test_header_untouched(self):
        mf = self._make_mix()
        gcode = [block for block in SAMPLE_GCODE]
        original_header = gcode[0]
        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])
        self.assertEqual(result[0], original_header)

    def test_geometry_preserved(self):
        mf = self._make_mix()
        gcode = [block for block in SAMPLE_GCODE]
        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])
        self.assertIn("G1 F1200 X20 Y10 E0.5", result[1])

    def test_mixed_color_comment_added(self):
        mf = self._make_mix()
        gcode = [block for block in SAMPLE_GCODE]
        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])
        self.assertIn(";MixedColor:TestMix", result[1])

    def test_no_mixed_filaments_passthrough(self):
        gcode = [block for block in SAMPLE_GCODE]
        processor = GCodeProcessor()
        result = processor.process(gcode, [])
        for i in range(len(gcode)):
            self.assertEqual(result[i], gcode[i])


class TestBresenhamDithering(unittest.TestCase):
    """Test that Bresenham distributes layers more evenly than simple repetition."""

    def test_2_1_bresenham_distribution(self):
        """With 2:1 ratio over 9 layers, Bresenham should distribute evenly."""
        mf = MixedFilament(
            name="Test",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=DitherPattern(mode="ratio", ratio_a=2, ratio_b=1),
            output_mode="tool_change",
            enabled=True,
        )
        # Create 9 proxy layers
        gcode = [";FLAVOR:Marlin\n;Layer height: 0.2\n"]
        for i in range(9):
            gcode.append(f";LAYER:{i}\nT2\nG1 X10 Y10 Z{(i+1)*0.2} E{i*0.5}\n")

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # Count T0 and T1 occurrences across layers
        t0_count = sum(1 for block in result[1:] if "T0 ;MixedColor" in block)
        t1_count = sum(1 for block in result[1:] if "T1 ;MixedColor" in block)

        # 2:1 ratio = 67% A, 33% B. For 9 layers: ~6 A, ~3 B
        self.assertEqual(t0_count, 6)
        self.assertEqual(t1_count, 3)

    def test_bresenham_no_clumping(self):
        """Bresenham should avoid putting all A layers in a row for moderate ratios."""
        mf = MixedFilament(
            name="Test",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=DitherPattern(mode="ratio", ratio_a=2, ratio_b=3),
            output_mode="tool_change",
            enabled=True,
        )
        gcode = [";FLAVOR:Marlin\n;Layer height: 0.2\n"]
        for i in range(10):
            gcode.append(f";LAYER:{i}\nT2\nG1 X10 Y10 Z{(i+1)*0.2} E{i*0.5}\n")

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # Extract the sequence of tools
        sequence = []
        for block in result[1:]:
            if "T0 ;MixedColor" in block:
                sequence.append(0)
            elif "T1 ;MixedColor" in block:
                sequence.append(1)

        # With Bresenham, we should never see 3+ consecutive same tool
        for i in range(len(sequence) - 2):
            same_three = (sequence[i] == sequence[i+1] == sequence[i+2])
            if same_three:
                self.fail(f"Three consecutive same tools at position {i}: {sequence}")


class TestGradientBresenham(unittest.TestCase):

    def test_gradient_transitions_smoothly(self):
        """Gradient from 100% A to 0% A should transition smoothly."""
        gradient = GradientProfile(enabled=True, keyframes=[
            GradientKeyframe(0.0, 1.0),
            GradientKeyframe(2.0, 0.0),
        ])
        mf = MixedFilament(
            name="GradTest",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=DitherPattern(mode="ratio", ratio_a=1, ratio_b=1),
            gradient=gradient,
            output_mode="tool_change",
            enabled=True,
        )
        gcode = [";FLAVOR:Marlin\n;Layer height: 0.2\n"]
        for i in range(10):
            gcode.append(f";LAYER:{i}\nT2\nG1 X10 Y10 Z{(i+1)*0.2} E{i*0.5}\n")

        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])

        # Count A/B in first half vs second half
        first_half_a = sum(1 for block in result[1:6] if "T0 ;MixedColor" in block)
        second_half_a = sum(1 for block in result[6:] if "T0 ;MixedColor" in block)

        # First half should have more A than second half
        self.assertGreater(first_half_a, second_half_a)


class TestGCodeProcessorMixingHotend(unittest.TestCase):

    def _make_mix_marlin(self):
        pattern = DitherPattern(mode="ratio", ratio_a=1, ratio_b=1)
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
        mf = self._make_mix_marlin()
        gcode = [block for block in SAMPLE_GCODE]
        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])
        self.assertIn("M163", result[1])
        self.assertIn("M164", result[1])
        self.assertIn("T2", result[1])

    def test_reprap_m567_injected(self):
        mf = self._make_mix_reprap()
        gcode = [block for block in SAMPLE_GCODE]
        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])
        self.assertIn("M567", result[1])
        self.assertIn("T2", result[1])

    def test_marlin_ratio_values(self):
        mf = self._make_mix_marlin()
        gcode = [block for block in SAMPLE_GCODE]
        processor = GCodeProcessor()
        result = processor.process(gcode, [mf])
        self.assertIn("M163 S0 P0.50", result[1])
        self.assertIn("M163 S1 P0.50", result[1])
        self.assertIn("M164 S2", result[1])


class TestTemperaturePreheating(unittest.TestCase):

    def test_preheat_commands_inserted(self):
        """Pre-heat commands should appear before tool changes."""
        mf = MixedFilament(
            name="PreheatTest",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=DitherPattern(mode="ratio", ratio_a=1, ratio_b=1),
            output_mode="tool_change",
            enabled=True,
        )
        gcode = [";FLAVOR:Marlin\n;Layer height: 0.2\n"]
        for i in range(6):
            gcode.append(f";LAYER:{i}\nT2\nG1 X10 Y10 Z{(i+1)*0.2} E{i*0.5}\n")

        processor = GCodeProcessor(
            preheat_layers=2,
            extruder_temperatures={0: 200.0, 1: 210.0},
            standby_temperature=150.0,
        )
        result = processor.process(gcode, [mf])

        # Check that M104 preheat commands exist somewhere
        all_gcode = "\n".join(result)
        self.assertIn("M104", all_gcode)
        self.assertIn(";MixedColor preheat", all_gcode)

    def test_no_preheat_when_disabled(self):
        """No preheat commands when preheat_layers is 0."""
        mf = MixedFilament(
            name="NoPreheat",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=DitherPattern(mode="ratio", ratio_a=1, ratio_b=1),
            output_mode="tool_change",
            enabled=True,
        )
        gcode = [";FLAVOR:Marlin\n;Layer height: 0.2\n"]
        for i in range(6):
            gcode.append(f";LAYER:{i}\nT2\nG1 X10 Y10 Z{(i+1)*0.2} E{i*0.5}\n")

        processor = GCodeProcessor(
            preheat_layers=0,
            extruder_temperatures={0: 200.0, 1: 210.0},
        )
        result = processor.process(gcode, [mf])

        all_gcode = "\n".join(result)
        self.assertNotIn("M104", all_gcode)


class TestPerObjectMeshAssignment(unittest.TestCase):

    def test_mesh_assignment_applied(self):
        """Per-object mesh assignments should override proxy matching."""
        mf = MixedFilament(
            id="mix-cube",
            name="CubeMix",
            filament_a=0,
            filament_b=1,
            proxy_extruder=2,
            pattern=DitherPattern(mode="ratio", ratio_a=1, ratio_b=1),
            output_mode="tool_change",
            enabled=True,
        )
        gcode = [block for block in SAMPLE_GCODE_MESH]

        processor = GCodeProcessor()
        result = processor.process(
            gcode, [mf],
            mesh_assignments={"Cube.stl": "mix-cube"}
        )

        # The Cube.stl sections should have mixed color processing
        # (either T0 or T1, not T2)
        for block in result[1:]:
            if ";MESH:Cube.stl" in block and ";MixedColor" in block:
                self.assertTrue("T0 ;MixedColor" in block or "T1 ;MixedColor" in block)


if __name__ == "__main__":
    unittest.main()
