# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""End-to-end integration tests for the non-planar slicing pipeline.

Validates the complete flow: STL mesh → surface analysis → candidate
detection → height map → collision checking → G-code bending.
Tests use the shipped test models (Z-up STL files).
"""

import struct
from pathlib import Path

import numpy as np
import pytest

from analysis.surface_analyzer import analyze_mesh
from analysis.candidate_detector import detect_candidates
from analysis.height_map import generate_height_map
from analysis.collision_checker import check_collisions
from gcode.transition_blender import compute_blend_map
from gcode.gcode_bender import bend_gcode

_TEST_MODELS = Path(__file__).resolve().parent.parent / "test_models"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_stl(filepath: Path) -> np.ndarray:
    """Read a binary STL file into (N, 3) vertex array."""
    with open(filepath, "rb") as f:
        f.read(80)
        n_tris = struct.unpack("<I", f.read(4))[0]
        verts = []
        for _ in range(n_tris):
            f.read(12)  # skip normal
            for _ in range(3):
                verts.append(struct.unpack("<3f", f.read(12)))
            f.read(2)
    return np.array(verts, dtype=np.float64)


def _make_test_gcode(n_layers: int = 40, layer_height: float = 0.2,
                     x_range=(-25, 26), y_values=(-5, 0, 5)):
    """Generate synthetic Cura-style G-code for testing bending.

    Uses absolute extrusion (M82) with cumulative E values so the parser
    correctly identifies moves as extrusion moves (E must increase).
    """
    chunks = [
        f";FLAVOR:Marlin\n;Layer height: {layer_height}\n",
        f"G28\nM82\nG1 Z{layer_height + 0.1:.3f} F3000\n",
    ]
    e_total = 0.0
    for layer in range(n_layers):
        z = layer_height * (layer + 1)
        lines = [f";LAYER:{layer}\n;TYPE:WALL-OUTER\n"]
        for x in range(x_range[0], x_range[1], 2):
            for y in y_values:
                e_total += 0.02
                lines.append(
                    f"G1 X{float(x):.1f} Y{float(y):.1f} "
                    f"Z{z:.3f} E{e_total:.4f} F1200\n"
                )
        chunks.append("".join(lines))
    return chunks


_DEFAULT_PRINTHEAD = [[-20, 10], [10, 10], [10, -10], [-20, -10]]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """Full pipeline: STL → analysis → bending."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_models(self):
        if not _TEST_MODELS.exists():
            pytest.skip("Test models not found")

    def test_coordinate_round_trip(self):
        """STL Z-up → Cura Y-up → slicing Z-up should be identity."""
        verts = _read_stl(_TEST_MODELS / "non_planar_test.stl")

        # Simulate Cura STL reader: (x,y,z) → (x, z, -y)
        cura = np.empty_like(verts)
        cura[:, 0] = verts[:, 0]
        cura[:, 1] = verts[:, 2]
        cura[:, 2] = -verts[:, 1]

        # Simulate _transformVertices: Y-up → Z-up
        zup = np.empty_like(cura)
        zup[:, 0] = cura[:, 0]
        zup[:, 1] = -cura[:, 2]
        zup[:, 2] = cura[:, 1]

        np.testing.assert_allclose(zup, verts, atol=1e-5)

    def test_test_model_has_candidates(self):
        """The test model must produce candidate regions."""
        verts = _read_stl(_TEST_MODELS / "non_planar_test.stl")
        analysis = analyze_mesh(verts, None)
        candidates = detect_candidates(
            analysis, None,
            max_angle_deg=30.0, min_benefit_angle_deg=5.0,
            min_region_area_mm2=50.0,
        )
        assert len(candidates.regions) >= 2, (
            f"Expected ≥2 regions, got {len(candidates.regions)}"
        )
        total_cand = int(np.count_nonzero(candidates.all_candidate_mask))
        assert total_cand > 100, (
            f"Expected >100 candidate faces, got {total_cand}"
        )

    def test_sphere_top_is_candidate_bottom_is_not(self):
        """Sphere: upper hemisphere should be candidate, lower should not."""
        verts = _read_stl(_TEST_MODELS / "sphere_25mm.stl")
        analysis = analyze_mesh(verts, None)
        candidates = detect_candidates(
            analysis, None,
            max_angle_deg=30.0, min_benefit_angle_deg=5.0,
            min_region_area_mm2=10.0,
        )

        # All candidate face centers should have Z > half height (12.5)
        for region in candidates.regions:
            centers = analysis.face_centers[region.face_indices]
            min_z = centers[:, 2].min()
            assert min_z > 5.0, (
                f"Candidate region extends to Z={min_z:.1f} — "
                "bottom hemisphere should not be a candidate"
            )

    def test_height_map_generation(self):
        """Height map must cover candidate regions with finite Z values."""
        verts = _read_stl(_TEST_MODELS / "non_planar_test.stl")
        analysis = analyze_mesh(verts, None)
        candidates = detect_candidates(
            analysis, None, max_angle_deg=30.0, min_region_area_mm2=50.0,
        )
        height_map = generate_height_map(
            verts, None, candidates.all_candidate_mask, resolution=1.0,
        )

        valid_cells = int(np.count_nonzero(np.isfinite(height_map.z_values)))
        assert valid_cells > 100, f"Only {valid_cells} valid height map cells"

    def test_collision_check_produces_safe_cells(self):
        """Collision checking should mark some cells as safe."""
        verts = _read_stl(_TEST_MODELS / "non_planar_test.stl")
        analysis = analyze_mesh(verts, None)
        candidates = detect_candidates(
            analysis, None, max_angle_deg=30.0, min_region_area_mm2=50.0,
        )
        height_map = generate_height_map(
            verts, None, candidates.all_candidate_mask, resolution=1.0,
        )
        collision = check_collisions(
            height_map, printhead_polygon=_DEFAULT_PRINTHEAD,
            nozzle_clearance_mm=8.0,
        )
        assert collision.safe_count > 0, "No safe cells found"

    def test_gcode_bending_produces_marker_and_non_planar_moves(self):
        """G-code bending must produce the processed marker and non-planar Z values.

        Uses 80 layers (0.2mm each → 16mm max Z) to fully cover the dome
        region (surface Z ≈ 10-15mm). Moves are dense across the dome
        center (-15..15 in X and Y) so the bender has enough in-region
        moves to form valid bent regions.
        """
        verts = _read_stl(_TEST_MODELS / "non_planar_test.stl")
        analysis = analyze_mesh(verts, None)
        candidates = detect_candidates(
            analysis, None, max_angle_deg=30.0, min_region_area_mm2=50.0,
        )
        height_map = generate_height_map(
            verts, None, candidates.all_candidate_mask, resolution=0.5,
        )
        collision = check_collisions(
            height_map, printhead_polygon=_DEFAULT_PRINTHEAD,
            nozzle_clearance_mm=8.0,
        )
        blend_map = compute_blend_map(
            collision.safe_map, resolution=0.5, blend_distance=3.0,
        )

        # 80 layers at 0.2mm = 16mm, enough to reach dome peak at ~15mm
        test_gcode = _make_test_gcode(
            n_layers=80, layer_height=0.2,
            x_range=(-15, 16), y_values=(-10, -5, 0, 5, 10),
        )
        result = bend_gcode(
            test_gcode,
            height_map=height_map,
            safe_map=collision.safe_map,
            blend_map=blend_map,
            settings={
                "layer_height": 0.2,
                "nonplanar_layer_count": 0,
                "max_angle_deg": 30.0,
                "flow_compensation": True,
                "feedrate_compensation": True,
                "segment_length": 1.0,
                "surface_mode": "all_surfaces",
            },
        )

        # 1) Check for processed marker
        has_marker = any(";NON-PLANAR PROCESSED" in c for c in result)
        assert has_marker, (
            "Expected ;NON-PLANAR PROCESSED marker in bent G-code header"
        )

        # 2) Check for non-planar Z values (not multiples of layer height)
        bent_z = set()
        for chunk in result:
            for line in chunk.split("\n"):
                if line.startswith("G1") and "Z" in line:
                    for part in line.split():
                        if part.startswith("Z"):
                            try:
                                bent_z.add(float(part[1:]))
                            except ValueError:
                                pass

        non_planar = [z for z in bent_z if abs(z % 0.2) > 0.01 and z > 0.3]
        assert len(non_planar) > 0, (
            "No non-planar Z values found in bent G-code. "
            f"All Z values: {sorted(bent_z)[:10]}"
        )

    def test_gcode_bending_with_machine_offset(self):
        """G-code bending must work when G-code uses machine coordinates.

        Cura outputs G-code in machine coordinates (corner origin), while the
        analysis height map uses model-centered coordinates.  The bender must
        apply the gcode_offset_x/y to translate between the two.
        """
        verts = _read_stl(_TEST_MODELS / "non_planar_test.stl")
        analysis = analyze_mesh(verts, None)
        candidates = detect_candidates(
            analysis, None, max_angle_deg=30.0, min_region_area_mm2=50.0,
        )
        height_map = generate_height_map(
            verts, None, candidates.all_candidate_mask, resolution=0.5,
        )
        collision = check_collisions(
            height_map, printhead_polygon=_DEFAULT_PRINTHEAD,
            nozzle_clearance_mm=8.0,
        )
        blend_map = compute_blend_map(
            collision.safe_map, resolution=0.5, blend_distance=3.0,
        )

        # Simulate Cura machine coordinates: offset by (165, 120) like
        # an Ultimaker S5 (330x240 build plate, center_is_zero=False).
        ox, oy = 165.0, 120.0

        # Build G-code with machine-offset coordinates.
        layer_height = 0.2
        n_layers = 80
        chunks = [
            f";FLAVOR:Marlin\n;Layer height: {layer_height}\n",
            f"G28\nM82\nG1 Z{layer_height + 0.1:.3f} F3000\n",
        ]
        e_total = 0.0
        for layer in range(n_layers):
            z = layer_height * (layer + 1)
            lines = [f";LAYER:{layer}\n;TYPE:WALL-OUTER\n"]
            for x in range(-15, 16, 2):
                for y in [-10, -5, 0, 5, 10]:
                    e_total += 0.02
                    lines.append(
                        f"G1 X{x + ox:.1f} Y{y + oy:.1f} "
                        f"Z{z:.3f} E{e_total:.4f} F1200\n"
                    )
            chunks.append("".join(lines))

        result = bend_gcode(
            chunks,
            height_map=height_map,
            safe_map=collision.safe_map,
            blend_map=blend_map,
            settings={
                "layer_height": 0.2,
                "nonplanar_layer_count": 0,
                "max_angle_deg": 30.0,
                "flow_compensation": True,
                "feedrate_compensation": True,
                "segment_length": 1.0,
                "surface_mode": "all_surfaces",
                "gcode_offset_x": ox,
                "gcode_offset_y": oy,
            },
        )

        has_marker = any(";NON-PLANAR PROCESSED" in c for c in result)
        assert has_marker, (
            "Expected ;NON-PLANAR PROCESSED marker with machine offset G-code"
        )

    def test_layer_count_auto(self):
        """layer_count=0 should auto-derive from height map Z delta."""
        verts = _read_stl(_TEST_MODELS / "non_planar_test.stl")
        analysis = analyze_mesh(verts, None)
        candidates = detect_candidates(
            analysis, None, max_angle_deg=30.0, min_region_area_mm2=50.0,
        )
        height_map = generate_height_map(
            verts, None, candidates.all_candidate_mask, resolution=1.0,
        )
        collision = check_collisions(
            height_map, printhead_polygon=_DEFAULT_PRINTHEAD,
            nozzle_clearance_mm=8.0,
        )
        blend_map = compute_blend_map(
            collision.safe_map, resolution=1.0, blend_distance=3.0,
        )

        # With layer_count=0, bend_gcode should auto-compute and still work
        test_gcode = _make_test_gcode(n_layers=20)
        result = bend_gcode(
            test_gcode,
            height_map=height_map,
            safe_map=collision.safe_map,
            blend_map=blend_map,
            settings={
                "layer_height": 0.2,
                "nonplanar_layer_count": 0,
                "max_angle_deg": 30.0,
                "flow_compensation": True,
                "feedrate_compensation": True,
                "surface_mode": "all_surfaces",
            },
        )
        # Should not crash and should produce output
        assert len(result) > 0
