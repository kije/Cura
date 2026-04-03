# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Tests for the candidate detector module."""

import math
import numpy as np
import pytest

from analysis.surface_analyzer import analyze_mesh
from analysis.candidate_detector import detect_candidates, CandidateRegion, CandidateRegions


def _make_ramp(angle_deg=20.0, scale=20.0):
    """Create a large ramp suitable for candidate detection.

    Returns vertices, indices with enough area to pass min_region_area filter.
    """
    rise = math.tan(math.radians(angle_deg)) * scale
    vertices = np.array([
        [0, 0, 0],
        [scale, 0, 0],
        [scale, scale, rise],
        [0, 0, 0],
        [scale, scale, rise],
        [0, scale, rise],
    ], dtype=np.float64)
    indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.intp)
    return vertices, indices


def _make_flat_square(z=0.0, scale=20.0):
    """Flat horizontal square."""
    vertices = np.array([
        [0, 0, z], [scale, 0, z], [scale, scale, z],
        [0, 0, z], [scale, scale, z], [0, scale, z],
    ], dtype=np.float64)
    indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.intp)
    return vertices, indices


class TestDetectCandidates:
    """Tests for detect_candidates."""

    def test_ramp_is_candidate(self):
        """A 20-degree ramp should be detected as candidate."""
        verts, indices = _make_ramp(20.0, scale=20.0)
        analysis = analyze_mesh(verts, indices)
        result = detect_candidates(analysis, indices, max_angle_deg=30.0, min_benefit_angle_deg=5.0, min_region_area_mm2=10.0)

        assert isinstance(result, CandidateRegions)
        assert len(result.regions) > 0
        assert result.all_candidate_mask.any()

    def test_flat_surface_not_candidate(self):
        """Flat horizontal surface: angle ~0, below min_benefit_angle."""
        verts, indices = _make_flat_square(z=5.0)
        analysis = analyze_mesh(verts, indices)
        result = detect_candidates(analysis, indices, min_benefit_angle_deg=5.0, min_region_area_mm2=10.0)

        assert len(result.regions) == 0
        assert not result.all_candidate_mask.any()

    def test_steep_surface_not_candidate(self):
        """60-degree ramp should be excluded by max_angle_deg=30."""
        verts, indices = _make_ramp(60.0, scale=20.0)
        analysis = analyze_mesh(verts, indices)
        result = detect_candidates(analysis, indices, max_angle_deg=30.0, min_region_area_mm2=10.0)

        assert len(result.regions) == 0

    def test_small_region_filtered(self):
        """Region smaller than min_region_area should be filtered out."""
        verts, indices = _make_ramp(20.0, scale=1.0)  # very small
        analysis = analyze_mesh(verts, indices)
        result = detect_candidates(analysis, indices, min_region_area_mm2=100.0)

        assert len(result.regions) == 0

    def test_region_sorted_by_area(self):
        """Regions should be sorted by descending area."""
        # Two separate ramps at different scales
        v1, i1 = _make_ramp(15.0, scale=30.0)
        v2, i2 = _make_ramp(15.0, scale=10.0)
        v2 += np.array([50, 0, 0])  # Offset to separate
        i2 += len(v1)

        verts = np.vstack([v1, v2])
        indices = np.vstack([i1, i2])

        analysis = analyze_mesh(verts, indices)
        result = detect_candidates(analysis, indices, min_region_area_mm2=1.0)

        if len(result.regions) >= 2:
            assert result.regions[0].total_area >= result.regions[1].total_area

    def test_candidate_region_has_bbox(self):
        """CandidateRegion should have valid bounding box."""
        verts, indices = _make_ramp(20.0, scale=20.0)
        analysis = analyze_mesh(verts, indices)
        result = detect_candidates(analysis, indices, min_region_area_mm2=10.0)

        if result.regions:
            region = result.regions[0]
            assert region.bbox_min.shape == (3,)
            assert region.bbox_max.shape == (3,)
            assert region.total_area > 0

    def test_non_indexed_mesh(self):
        """Should work with indices=None."""
        verts = np.array([
            [0, 0, 0], [20, 0, 0], [20, 20, 5],
            [0, 0, 0], [20, 20, 5], [0, 20, 5],
        ], dtype=np.float64)
        analysis = analyze_mesh(verts, None)
        result = detect_candidates(analysis, None, min_region_area_mm2=10.0)
        assert isinstance(result, CandidateRegions)

    def test_all_candidate_mask_shape(self):
        """all_candidate_mask should have correct shape."""
        verts, indices = _make_ramp(20.0, scale=20.0)
        analysis = analyze_mesh(verts, indices)
        result = detect_candidates(analysis, indices, min_region_area_mm2=10.0)

        assert result.all_candidate_mask.shape == (indices.shape[0],)
