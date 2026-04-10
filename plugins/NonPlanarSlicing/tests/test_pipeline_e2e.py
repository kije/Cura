# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""End-to-end pipeline tests for the non-planar slicing plugin.

These tests exercise the full Python-side pipeline:

    mesh vertices/indices
        -> surface analysis
        -> candidate detection
        -> height map (raycasting)
        -> collision check
        -> deformation field
        -> NPDF binary serialization
        -> deserialization (matches engine prototype)
        -> inverse Z transform on synthetic GCodePath data

This catches integration bugs that unit tests miss, such as:
- Coordinate system mismatches between stages
- Data shape/dtype incompatibilities at module boundaries
- Constraint violations that only appear with realistic inputs
- Roundtrip consistency between Python serializer and deserializer
"""

from __future__ import annotations

import math
import struct
from typing import Tuple

import numpy as np
import pytest

from analysis.surface_analyzer import analyze_mesh
from analysis.candidate_detector import detect_candidates
from analysis.height_map import generate_height_map
from analysis.collision_checker import check_collisions
from analysis.transition_blender import compute_blend_map
from analysis.deformation_field import (
    compute_deformation_field,
    DeformationField,
)
from analysis.mesh_deformer import deform_mesh_vertices, inverse_deform_z


# ---------------------------------------------------------------------------
# Mesh fixtures: synthetic test geometries
# ---------------------------------------------------------------------------

def make_inclined_box_mesh(
    width: float = 20.0,
    depth: float = 20.0,
    base_height: float = 5.0,
    top_tilt: float = 2.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Create a box with a tilted top surface (in Z-up coordinates).

    The bottom is flat at z=0, the top tilts from base_height at x=0 to
    base_height + top_tilt at x=width. This produces a controlled inclined
    top surface that should be detected as a non-planar candidate.

    Returns
    -------
    (vertices, indices): vertices is (N, 3) float64, indices is (M, 3) int32
    """
    verts = np.array([
        # Bottom face (z=0)
        [0,     0,     0],  # 0
        [width, 0,     0],  # 1
        [width, depth, 0],  # 2
        [0,     depth, 0],  # 3
        # Top face (tilted in X)
        [0,     0,     base_height],                    # 4
        [width, 0,     base_height + top_tilt],         # 5
        [width, depth, base_height + top_tilt],         # 6
        [0,     depth, base_height],                    # 7
    ], dtype=np.float64)

    indices = np.array([
        # Bottom (Z-down)
        [0, 2, 1], [0, 3, 2],
        # Top (Z-up — these are the candidate faces)
        [4, 5, 6], [4, 6, 7],
        # Sides
        [0, 1, 5], [0, 5, 4],
        [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6],
        [3, 0, 4], [3, 4, 7],
    ], dtype=np.int32)

    return verts, indices


def make_dome_mesh(radius: float = 15.0, segments: int = 16) -> Tuple[np.ndarray, np.ndarray]:
    """Create a dome (hemisphere) sitting on a flat base.

    The dome surface has continuously varying angle from horizontal at the
    apex to vertical at the rim, providing a more realistic candidate
    surface for non-planar slicing.
    """
    verts = []
    # Apex
    verts.append([0, 0, radius])
    # Latitudes (excluding apex and rim)
    for lat in range(1, segments // 2):
        theta = lat * math.pi / segments
        z = radius * math.cos(theta)
        r_at = radius * math.sin(theta)
        for lon in range(segments):
            phi = lon * 2 * math.pi / segments
            verts.append([r_at * math.cos(phi), r_at * math.sin(phi), z])
    # Rim (z=0)
    for lon in range(segments):
        phi = lon * 2 * math.pi / segments
        verts.append([radius * math.cos(phi), radius * math.sin(phi), 0])

    verts = np.array(verts, dtype=np.float64)
    # Index construction for the dome (apex triangles + lat/lon quads)
    indices = []
    n_lat = segments // 2 - 1  # latitudes excluding apex (rim is also a "lat")

    # Cap triangles connecting apex (vertex 0) to first latitude ring
    for lon in range(segments):
        a = 0
        b = 1 + lon
        c = 1 + ((lon + 1) % segments)
        indices.append([a, b, c])

    # Quad strips between latitudes
    for lat in range(n_lat):
        ring_a_start = 1 + lat * segments
        ring_b_start = 1 + (lat + 1) * segments
        for lon in range(segments):
            a = ring_a_start + lon
            b = ring_a_start + ((lon + 1) % segments)
            c = ring_b_start + ((lon + 1) % segments)
            d = ring_b_start + lon
            indices.append([a, b, c])
            indices.append([a, c, d])

    indices = np.array(indices, dtype=np.int32)
    return verts, indices


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_full_pipeline(
    vertices: np.ndarray,
    indices: np.ndarray,
    *,
    max_angle_deg: float = 30.0,
    min_benefit_angle_deg: float = 5.0,
    min_region_area_mm2: float = 10.0,
    heightmap_resolution: float = 1.0,
    nozzle_clearance_mm: float = 8.0,
    blend_distance: float = 2.0,
    layer_height: float = 0.2,
    decay_distance: float = 5.0,
):
    """Run the full Python pipeline and return all intermediate results."""
    # Step 1: Surface analysis
    analysis = analyze_mesh(vertices, indices, transform_matrix=None)
    assert analysis.face_normals.shape[0] == indices.shape[0]

    # Step 2: Candidate detection
    candidates = detect_candidates(
        analysis, indices,
        max_angle_deg=max_angle_deg,
        min_benefit_angle_deg=min_benefit_angle_deg,
        min_region_area_mm2=min_region_area_mm2,
    )

    # Step 3: Height map
    height_map = generate_height_map(
        vertices, indices, candidates.all_candidate_mask,
        resolution=heightmap_resolution,
    )

    # Step 4: Collision check
    printhead_polygon = [[-20, 10], [10, 10], [10, -10], [-20, -10]]
    collision_result = check_collisions(
        height_map,
        printhead_polygon=printhead_polygon,
        nozzle_clearance_mm=nozzle_clearance_mm,
        nozzle_expansion_angle_deg=45.0,
        safety_margin_mm=0.5,
    )

    # Step 5: Blend map
    blend_map = compute_blend_map(
        collision_result.safe_map,
        resolution=heightmap_resolution,
        blend_distance=blend_distance,
    )

    # Step 6: Deformation field
    z_max = float(np.max(vertices[:, 2]))
    total_layers = max(1, int(z_max / layer_height) + 1)
    deformation_field = compute_deformation_field(
        height_map,
        collision_result.safe_map,
        layer_height=layer_height,
        total_layers=total_layers,
        first_layer_z=layer_height,
        decay_distance=decay_distance,
        max_angle_deg=max_angle_deg,
    )

    return {
        "analysis": analysis,
        "candidates": candidates,
        "height_map": height_map,
        "collision_result": collision_result,
        "blend_map": blend_map,
        "deformation_field": deformation_field,
        "total_layers": total_layers,
    }


# ---------------------------------------------------------------------------
# Tests: Full pipeline on inclined box
# ---------------------------------------------------------------------------

class TestInclinedBoxPipeline:
    """Run the complete pipeline on a box with an inclined top surface."""

    @pytest.fixture(scope="class")
    def pipeline_results(self):
        verts, indices = make_inclined_box_mesh(
            width=20.0, depth=20.0,
            base_height=5.0, top_tilt=3.0,
        )
        return run_full_pipeline(verts, indices)

    def test_surface_analysis_classifies_top(self, pipeline_results):
        """Top faces should be detected as upward-facing."""
        analysis = pipeline_results["analysis"]
        assert analysis.is_top_surface.any()
        # Top surface area should be roughly width * depth (~400 mm²)
        # accounting for the slight tilt
        top_area = analysis.face_areas[analysis.is_top_surface].sum()
        assert top_area > 350  # ≥ 87.5% of 400

    def test_candidates_found(self, pipeline_results):
        """Inclined top should produce at least one candidate region."""
        candidates = pipeline_results["candidates"]
        assert len(candidates.regions) >= 1
        # The candidate region should cover most of the top
        total_area = sum(r.total_area for r in candidates.regions)
        assert total_area > 100  # Should be a substantial region

    def test_height_map_contains_z_values(self, pipeline_results):
        """Height map should have finite Z values within the candidate region."""
        height_map = pipeline_results["height_map"]
        assert height_map.z_values.shape[0] > 0
        assert np.any(np.isfinite(height_map.z_values))
        finite_z = height_map.z_values[np.isfinite(height_map.z_values)]
        # Z values should be in the expected range (5-8mm)
        assert finite_z.min() >= 4.5
        assert finite_z.max() <= 8.5

    def test_collision_check_runs(self, pipeline_results):
        """Collision check should produce valid safe/collision maps."""
        cr = pipeline_results["collision_result"]
        assert cr.safe_map.shape == cr.collision_map.shape
        # On a simple inclined box, most of the surface should be safe
        # (no overhangs to collide with)
        assert cr.safe_count > 0

    def test_blend_map_in_range(self, pipeline_results):
        """Blend map values should be in [0, 1]."""
        bm = pipeline_results["blend_map"]
        assert bm.min() >= 0.0
        assert bm.max() <= 1.0

    def test_deformation_field_shape(self, pipeline_results):
        """Deformation field should match height map dimensions."""
        df = pipeline_results["deformation_field"]
        hm = pipeline_results["height_map"]
        assert df.grid_shape == hm.grid_shape
        assert df.num_layers == pipeline_results["total_layers"]

    def test_deformation_field_is_finite(self, pipeline_results):
        """All displacements must be finite (no NaN/Inf)."""
        df = pipeline_results["deformation_field"]
        assert np.all(np.isfinite(df.displacements))

    def test_deformation_field_thickness_bounds(self, pipeline_results):
        """Layer gaps after deformation should respect thickness bounds."""
        df = pipeline_results["deformation_field"]
        layer_height = 0.2
        min_gap = 0.5 * layer_height
        max_gap = 2.0 * layer_height
        for k in range(1, df.num_layers):
            gap = (df.z_levels[k] + df.displacements[k]) - \
                  (df.z_levels[k - 1] + df.displacements[k - 1])
            # Allow 1% tolerance for solver precision
            assert np.all(gap >= min_gap - 0.005), \
                f"Layer {k}: thin gap {gap.min():.4f} < {min_gap:.4f}"
            assert np.all(gap <= max_gap + 0.005), \
                f"Layer {k}: thick gap {gap.max():.4f} > {max_gap:.4f}"

    def test_deformation_field_floor_safe(self, pipeline_results):
        """No deformed Z should go below the bed floor."""
        df = pipeline_results["deformation_field"]
        for k in range(df.num_layers):
            deformed_z = df.z_levels[k] + df.displacements[k]
            assert np.all(deformed_z >= 0.04), \
                f"Layer {k}: below floor (min={deformed_z.min():.4f})"


# ---------------------------------------------------------------------------
# Tests: Full pipeline on dome
# ---------------------------------------------------------------------------

class TestDomePipeline:
    """Run the complete pipeline on a dome (continuously curving surface)."""

    @pytest.fixture(scope="class")
    def pipeline_results(self):
        verts, indices = make_dome_mesh(radius=15.0, segments=12)
        return run_full_pipeline(
            verts, indices,
            max_angle_deg=45.0,  # Dome has steeper angles
            min_region_area_mm2=5.0,
        )

    def test_dome_pipeline_completes(self, pipeline_results):
        """Pipeline should complete without errors on dome geometry."""
        assert pipeline_results["deformation_field"] is not None

    def test_dome_has_apex_candidate(self, pipeline_results):
        """The flat apex region should be detected as a candidate."""
        candidates = pipeline_results["candidates"]
        # Dome must produce at least one candidate region (the top)
        assert len(candidates.regions) >= 1

    def test_dome_field_finite(self, pipeline_results):
        """Deformation field should be finite for dome."""
        df = pipeline_results["deformation_field"]
        assert np.all(np.isfinite(df.displacements))


# ---------------------------------------------------------------------------
# Tests: NPDF serialization roundtrip across pipeline
# ---------------------------------------------------------------------------

class TestNPDFRoundtripWithPipeline:
    """Test that pipeline-produced fields can be serialized and deserialized."""

    def _serialize(self, field: DeformationField) -> bytes:
        """Mirror NonPlanarSlicingExtension._serialize_deformation_field."""
        rows, cols = field.grid_shape
        header = struct.pack(
            "<4sHIII5d",
            b"NPDF", 1,
            field.num_layers, rows, cols,
            field.x_min, field.x_max,
            field.y_min, field.y_max,
            field.resolution,
        )
        z_levels = field.z_levels.astype(np.float32).tobytes()
        displacements = field.displacements.astype(np.float32).tobytes()
        return header + z_levels + displacements

    def test_roundtrip_with_inclined_box(self):
        """Pipeline output → NPDF binary → engine_prototype deserialization."""
        verts, indices = make_inclined_box_mesh(width=15.0, top_tilt=2.0)
        results = run_full_pipeline(verts, indices)

        field = results["deformation_field"]
        data = self._serialize(field)

        # Header parses correctly
        assert data[:4] == b"NPDF"
        version = struct.unpack_from("<H", data, 4)[0]
        assert version == 1

        # Deserialize via the engine prototype's parser
        import sys
        from pathlib import Path
        plugin_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(plugin_root))
        try:
            from engine_prototype import DeformationField as ProtoField
            proto_field = ProtoField.from_bytes(data)

            # Dimensions match
            assert proto_field.num_layers == field.num_layers
            assert proto_field.rows == field.grid_shape[0]
            assert proto_field.cols == field.grid_shape[1]
            assert proto_field.x_min == pytest.approx(field.x_min, abs=1e-4)

            # Sample values match (within float32 precision)
            for k in [0, field.num_layers // 2, field.num_layers - 1]:
                for r in [0, field.grid_shape[0] // 2, field.grid_shape[0] - 1]:
                    for c in [0, field.grid_shape[1] // 2, field.grid_shape[1] - 1]:
                        py_val = float(field.displacements[k, r, c])
                        flat_idx = k * field.grid_shape[0] * field.grid_shape[1] \
                                   + r * field.grid_shape[1] + c
                        proto_val = proto_field.displacements[flat_idx]
                        assert proto_val == pytest.approx(py_val, abs=1e-5)
        finally:
            sys.path.remove(str(plugin_root))


# ---------------------------------------------------------------------------
# Tests: Forward/inverse deformation roundtrip
# ---------------------------------------------------------------------------

class TestForwardInverseRoundtrip:
    """Verify forward mesh deformation + inverse Z transform recovers geometry."""

    def test_pipeline_roundtrip(self):
        """Forward-deform vertices, then inverse-transform deformed Z values.

        This simulates the full architecture:
        1. Python computes deformation field
        2. Python forward-deforms mesh (mesh_deformer.deform_mesh_vertices)
        3. CuraEngine slices the flat (deformed) mesh, producing GCodePath
           data with Z coordinates in the deformed space
        4. Engine plugin (Rust/Python prototype) inverse-transforms Z back
           to the original curved space
        5. Verify we recover the original geometry
        """
        verts, indices = make_inclined_box_mesh(width=20.0, top_tilt=2.0)
        results = run_full_pipeline(verts, indices)
        field = results["deformation_field"]

        # Forward-deform vertices (mimics _onSlicingStarted)
        deformed_verts = deform_mesh_vertices(verts, field, z_up=True)

        # Pick interior vertices (not at boundaries) for the test
        # The top face vertices should have nonzero deformation
        for i in range(len(verts)):
            x, y, z_orig = verts[i]
            x, y, z_def = deformed_verts[i]

            # Inverse transform should recover the original Z
            z_recovered = inverse_deform_z(x, y, z_def, field)
            assert z_recovered == pytest.approx(z_orig, abs=0.01), \
                f"Vertex {i}: orig={z_orig:.4f}, def={z_def:.4f}, " \
                f"rec={z_recovered:.4f}"

    def test_roundtrip_intermediate_layer_points(self):
        """Verify roundtrip at points within the print volume, not just vertices."""
        verts, indices = make_inclined_box_mesh(width=20.0, top_tilt=2.0)
        results = run_full_pipeline(verts, indices)
        field = results["deformation_field"]

        # Sample points within the deformation field bounds
        sample_x = np.linspace(field.x_min + 1, field.x_max - 1, 5)
        sample_y = np.linspace(field.y_min + 1, field.y_max - 1, 5)
        sample_z = np.linspace(0.5, field.z_levels[-1], 5)

        for x in sample_x:
            for y in sample_y:
                for z_orig in sample_z:
                    z_def = z_orig + field.interpolate(x, y, z_orig)
                    z_rec = inverse_deform_z(x, y, z_def, field)
                    assert z_rec == pytest.approx(z_orig, abs=0.01), \
                        f"({x}, {y}, {z_orig}) -> {z_def} -> {z_rec}"


# ---------------------------------------------------------------------------
# Tests: Solver equivalence (QuickCurve vs heuristic)
# ---------------------------------------------------------------------------

class TestSolverEquivalence:
    """Verify different solvers produce qualitatively similar results."""

    def test_solvers_agree_on_simple_input(self):
        """All solvers should produce similar output magnitudes."""
        from analysis.deformation_field import (
            _solve_quickcurve, _solve_heuristic, _HAS_SCIPY_SPARSE,
        )

        rows, cols = 5, 5
        z_vals = np.zeros((rows, cols))
        for c in range(cols):
            z_vals[:, c] = 0.85 + 0.05 * c

        z_levels = np.linspace(0.2, 1.2, 6)
        surface_disp = np.zeros((rows, cols))
        for r in range(rows):
            for c in range(cols):
                sz = z_vals[r, c]
                idx = int(np.searchsorted(z_levels, sz)) - 1
                idx = max(0, min(idx, len(z_levels) - 1))
                surface_disp[r, c] = sz - z_levels[idx]

        safe = np.ones((rows, cols), dtype=np.bool_)

        # Heuristic always available
        h_disp = _solve_heuristic(
            z_levels, z_vals, surface_disp, safe,
            rows, cols, 0.2, 6,
            5.0, 1.0, 30.0,
            0.5, 2.0,
        )

        if _HAS_SCIPY_SPARSE:
            qc_disp = _solve_quickcurve(
                z_levels, z_vals, surface_disp, safe,
                rows, cols, 1.0,
                0.2, 6,
                0.5, 2.0,
                30.0, 5.0,
            )
            # Both should produce some non-zero displacement
            assert np.max(np.abs(h_disp)) > 0
            assert np.max(np.abs(qc_disp)) > 0
            # Magnitudes should be in the same order of magnitude
            ratio = np.max(np.abs(qc_disp)) / max(np.max(np.abs(h_disp)), 1e-9)
            assert 0.1 < ratio < 10.0

    def test_compute_deformation_field_with_each_solver(self):
        """compute_deformation_field should work via QuickCurve and heuristic."""
        from analysis.deformation_field import _HAS_SCIPY_SPARSE
        import analysis.deformation_field as df_mod

        verts, indices = make_inclined_box_mesh(width=15.0, top_tilt=2.0)
        results = run_full_pipeline(verts, indices)
        # The deformation field is finite regardless of solver
        assert np.all(np.isfinite(results["deformation_field"].displacements))

        # Force heuristic by temporarily disabling scipy
        original = df_mod._HAS_SCIPY_SPARSE
        try:
            df_mod._HAS_SCIPY_SPARSE = False
            results_heuristic = run_full_pipeline(verts, indices)
            assert np.all(np.isfinite(
                results_heuristic["deformation_field"].displacements
            ))
        finally:
            df_mod._HAS_SCIPY_SPARSE = original
