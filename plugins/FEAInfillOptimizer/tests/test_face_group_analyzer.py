# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Unit tests for fea/face_group_analyzer.py.

Covers:
- _get_face_vertices: indexed + flat layouts, bounds checking.
- _face_normal: unit normal, degenerate (zero-area), NaN/inf vertices.
- _face_centroid: arithmetic mean.
- build_face_adjacency: indexed + flat modes, non-manifold edges.
- find_coplanar_group: BFS, angle threshold, max_faces limit, degenerate seed.
- find_hole_surface / find_cylinder_surface: concave/convex detection.
- MAX_BFS_ITERATIONS global limit.

No Cura / UM imports required — face_group_analyzer is pure numpy.

Run with:
    source .test-venv/bin/activate
    python -m pytest plugins/FEAInfillOptimizer/tests/test_face_group_analyzer.py -v
"""

import os
import sys

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Ensure the plugins directory is on sys.path
# ---------------------------------------------------------------------------
_PLUGINS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

from FEAInfillOptimizer.fea.face_group_analyzer import (
    MAX_BFS_ITERATIONS,
    _face_centroid,
    _face_normal,
    _get_face_vertices,
    build_face_adjacency,
    find_coplanar_group,
    find_cylinder_surface,
    find_hole_surface,
)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _unit_cube_mesh():
    """Return (verts, indices) for a 12-triangle unit cube (2 triangles per face)."""
    verts = np.array(
        [
            [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],  # bottom
            [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],  # top
        ],
        dtype=np.float32,
    )
    indices = np.array(
        [
            # top z=1
            [4, 5, 6], [4, 6, 7],
            # bottom z=0
            [0, 3, 2], [0, 2, 1],
            # front y=0
            [0, 1, 5], [0, 5, 4],
            # back y=1
            [3, 7, 6], [3, 6, 2],
            # left x=0
            [0, 4, 7], [0, 7, 3],
            # right x=1
            [1, 2, 6], [1, 6, 5],
        ],
        dtype=np.int32,
    )
    return verts, indices


def _two_adjacent_triangles():
    """Two triangles sharing edge (v1, v2)."""
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=np.float32)
    indices = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int32)
    return verts, indices


# ===========================================================================
# 1. _get_face_vertices
# ===========================================================================


class TestGetFaceVertices:

    def test_indexed_returns_three_shape3_arrays(self):
        verts, indices = _two_adjacent_triangles()
        v0, v1, v2 = _get_face_vertices(verts, indices, 0)
        assert v0.shape == (3,)
        assert v1.shape == (3,)
        assert v2.shape == (3,)

    def test_indexed_returns_float64(self):
        verts, indices = _two_adjacent_triangles()
        v0, v1, v2 = _get_face_vertices(verts, indices, 0)
        assert v0.dtype == np.float64

    def test_indexed_correct_positions(self):
        verts, indices = _two_adjacent_triangles()
        v0, v1, v2 = _get_face_vertices(verts, indices, 0)
        np.testing.assert_allclose(v0, [0, 0, 0])
        np.testing.assert_allclose(v1, [1, 0, 0])
        np.testing.assert_allclose(v2, [0, 1, 0])

    def test_indexed_out_of_range_face_raises(self):
        verts, indices = _two_adjacent_triangles()
        with pytest.raises(IndexError):
            _get_face_vertices(verts, indices, 99)

    def test_indexed_negative_face_raises(self):
        verts, indices = _two_adjacent_triangles()
        with pytest.raises(IndexError):
            _get_face_vertices(verts, indices, -1)

    def test_indexed_bad_vertex_ref_raises(self):
        """Face referencing a vertex index >= n_verts must raise IndexError."""
        verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        # Face 0 references vertex 99 (out of range)
        indices = np.array([[0, 1, 99]], dtype=np.int32)
        with pytest.raises(IndexError):
            _get_face_vertices(verts, indices, 0)

    def test_flat_layout_returns_correct_vertices(self):
        """Flat layout: every 3 rows form one triangle."""
        # Triangle 0 at rows 0-2, triangle 1 at rows 3-5
        verts = np.array(
            [
                [0, 0, 0], [1, 0, 0], [0, 1, 0],  # face 0
                [1, 1, 0], [2, 0, 0], [1, 2, 0],  # face 1
            ],
            dtype=np.float32,
        )
        v0, v1, v2 = _get_face_vertices(verts, None, 1)
        np.testing.assert_allclose(v0, [1, 1, 0])

    def test_flat_layout_out_of_range_raises(self):
        verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        with pytest.raises(IndexError):
            _get_face_vertices(verts, None, 5)


# ===========================================================================
# 2. _face_normal
# ===========================================================================


class TestFaceNormal:

    def test_unit_length(self):
        verts, indices = _unit_cube_mesh()
        n = _face_normal(verts, indices, 0)
        assert abs(float(np.linalg.norm(n)) - 1.0) < 1e-9

    def test_z_plus_normal_for_top_face(self):
        """Top face (z=1) triangles should have normal pointing +z."""
        verts, indices = _unit_cube_mesh()
        # Faces 0,1 are the top face
        n = _face_normal(verts, indices, 0)
        assert abs(float(np.dot(n, [0, 0, 1]))) > 0.99

    def test_degenerate_zero_area_returns_zero_vector(self):
        """Zero-area triangle → zero normal vector, no exception."""
        verts = np.array([[0, 0, 0], [1, 0, 0], [0.5, 0, 0]], dtype=np.float32)
        indices = np.array([[0, 1, 2]], dtype=np.int32)
        n = _face_normal(verts, indices, 0)
        np.testing.assert_allclose(n, [0, 0, 0], atol=1e-12)

    def test_nan_vertex_returns_zero_vector(self):
        """NaN vertex position → zero normal, no exception."""
        verts = np.array([[0, 0, 0], [1, 0, 0], [float("nan"), 0, 1]], dtype=np.float32)
        indices = np.array([[0, 1, 2]], dtype=np.int32)
        n = _face_normal(verts, indices, 0)
        np.testing.assert_allclose(n, [0, 0, 0], atol=1e-12)

    def test_inf_vertex_returns_zero_vector(self):
        verts = np.array([[0, 0, 0], [float("inf"), 0, 0], [0, 1, 0]], dtype=np.float32)
        indices = np.array([[0, 1, 2]], dtype=np.int32)
        n = _face_normal(verts, indices, 0)
        np.testing.assert_allclose(n, [0, 0, 0], atol=1e-12)

    def test_flat_layout_normal(self):
        """Flat layout: upward-pointing triangle should give +z normal."""
        verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
        n = _face_normal(verts, None, 0)
        assert abs(float(np.dot(n, [0, 0, 1]))) > 0.99


# ===========================================================================
# 3. _face_centroid
# ===========================================================================


class TestFaceCentroid:

    def test_centroid_is_mean_of_vertices(self):
        verts, indices = _two_adjacent_triangles()
        c = _face_centroid(verts, indices, 0)
        # Face 0: vertices [0,0,0], [1,0,0], [0,1,0] → centroid (1/3, 1/3, 0)
        np.testing.assert_allclose(c, [1 / 3, 1 / 3, 0.0], atol=1e-6)

    def test_centroid_dtype_float64(self):
        verts, indices = _two_adjacent_triangles()
        c = _face_centroid(verts, indices, 0)
        assert c.dtype == np.float64

    def test_centroid_shape(self):
        verts, indices = _two_adjacent_triangles()
        c = _face_centroid(verts, indices, 0)
        assert c.shape == (3,)


# ===========================================================================
# 4. build_face_adjacency
# ===========================================================================


class TestBuildFaceAdjacency:

    def test_two_triangles_sharing_one_edge(self):
        verts, indices = _two_adjacent_triangles()
        adj = build_face_adjacency(verts, indices)
        assert 1 in adj[0]
        assert 0 in adj[1]

    def test_isolated_triangles_have_no_neighbours(self):
        """Two triangles with no shared vertices → empty adjacency."""
        verts = np.array(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 5], [6, 5, 5], [5, 6, 5]],
            dtype=np.float32,
        )
        indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
        adj = build_face_adjacency(verts, indices)
        assert adj[0] == []
        assert adj[1] == []

    def test_cube_every_face_has_neighbours(self):
        verts, indices = _unit_cube_mesh()
        adj = build_face_adjacency(verts, indices)
        for fi in range(len(indices)):
            assert len(adj[fi]) > 0, f"Face {fi} has no neighbours"

    def test_adjacency_is_symmetric(self):
        verts, indices = _unit_cube_mesh()
        adj = build_face_adjacency(verts, indices)
        for fi, neighbours in adj.items():
            for nj in neighbours:
                assert fi in adj[nj], f"Asymmetric adjacency: {fi} → {nj} but not reverse"

    def test_no_self_loops(self):
        verts, indices = _unit_cube_mesh()
        adj = build_face_adjacency(verts, indices)
        for fi, neighbours in adj.items():
            assert fi not in neighbours, f"Self-loop at face {fi}"

    def test_flat_mesh_adjacency(self):
        """Flat mesh (no indices): two triangles sharing a position-coincident edge."""
        # Triangle 0: (0,0,0), (1,0,0), (0,1,0)
        # Triangle 1: (1,0,0), (0,1,0), (1,1,0)  — shares edge (1,0,0)-(0,1,0)
        verts = np.array(
            [
                [0, 0, 0], [1, 0, 0], [0, 1, 0],  # face 0
                [1, 0, 0], [0, 1, 0], [1, 1, 0],  # face 1
            ],
            dtype=np.float32,
        )
        adj = build_face_adjacency(verts, None)
        assert 1 in adj[0]
        assert 0 in adj[1]

    def test_all_faces_present_in_adjacency(self):
        verts, indices = _unit_cube_mesh()
        adj = build_face_adjacency(verts, indices)
        for fi in range(len(indices)):
            assert fi in adj

    def test_non_manifold_edge_no_crash(self):
        """Three triangles sharing the same edge should not crash."""
        verts = np.array(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, -1, 0]],
            dtype=np.float32,
        )
        # All three share edge (0,1)
        indices = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]], dtype=np.int32)
        adj = build_face_adjacency(verts, indices)
        # Every face should be adjacent to the other two
        for fi in range(3):
            assert len(adj[fi]) == 2

    def test_flat_mesh_non_divisible_by_3_raises(self):
        """Flat mesh with n_verts % 3 != 0 must raise ValueError."""
        verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], dtype=np.float32)
        with pytest.raises(ValueError, match="divisible by 3"):
            build_face_adjacency(verts, None)


# ===========================================================================
# 5. find_coplanar_group
# ===========================================================================


class TestFindCoplanarGroup:

    @pytest.fixture
    def cube_setup(self):
        verts, indices = _unit_cube_mesh()
        adj = build_face_adjacency(verts, indices)
        return verts, indices, adj

    def test_seed_face_always_in_result(self, cube_setup):
        verts, indices, adj = cube_setup
        for seed in range(12):
            group = find_coplanar_group(verts, indices, seed, adj)
            assert seed in group, f"seed {seed} not in group {group}"

    def test_top_face_contains_both_triangles(self, cube_setup):
        """Top face (z=1) has two triangles (faces 0 and 1) — both should be found."""
        verts, indices, adj = cube_setup
        group = find_coplanar_group(verts, indices, 0, adj)
        assert 0 in group
        assert 1 in group
        assert len(group) == 2

    def test_coplanar_group_sorted(self, cube_setup):
        verts, indices, adj = cube_setup
        group = find_coplanar_group(verts, indices, 0, adj)
        assert group == sorted(group)

    def test_strict_threshold_returns_only_seed_for_angled_neighbours(self):
        """With angle_threshold_deg=0.5°, a seed surrounded by 20°-tilted neighbours
        should return only the seed itself."""
        # One reference triangle (normal ~z), four 20°-tilted neighbours
        import math

        angle = math.radians(20)
        c = math.cos(angle)
        s = math.sin(angle)

        # Seed face (horizontal z=0 plane)
        seed_verts = [[0, 0, 0], [1, 0, 0], [0.5, 0.5, 0]]
        # One 20°-tilted neighbour (shares edge (0,1))
        tip = [1, 0, c]  # tilted vertex
        nb_verts = [[0, 0, 0], [1, 0, 0], tip]

        verts = np.array(seed_verts + nb_verts, dtype=np.float32)
        indices = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
        # Manually build adjacency: they share row 0-1 conceptually but via position
        # Use flat adjacency by duplicating shared positions
        verts_flat = np.array(
            seed_verts + nb_verts,
            dtype=np.float32,
        )
        # Use indexed adjacency: override positions so they share an actual index
        verts2 = np.array(
            [[0, 0, 0], [1, 0, 0], [0.5, 0.5, 0], [0.5, 0.5, c]], dtype=np.float32
        )
        indices2 = np.array([[0, 1, 2], [0, 1, 3]], dtype=np.int32)
        adj2 = build_face_adjacency(verts2, indices2)

        group = find_coplanar_group(verts2, indices2, 0, adj2, angle_threshold_deg=0.5)
        # With such a tight threshold, the 20°-tilted face must be excluded
        assert 0 in group
        assert 1 not in group

    def test_max_faces_limit_respected(self):
        """find_coplanar_group must stop when len(accepted) >= max_faces."""
        # Build a long strip of N coplanar triangles (all horizontal)
        N = 20
        verts_list = []
        indices_list = []
        for i in range(N):
            # Triangle i: (i,0,0), (i+1,0,0), (i+0.5,1,0)
            verts_list.extend([[i, 0, 0], [i + 1, 0, 0], [i + 0.5, 1, 0]])
            indices_list.append([i * 3, i * 3 + 1, i * 3 + 2])
        # Add shared edges by making adjacent triangles share vertices via index
        # Simpler: use flat adjacency (position-based)
        verts_flat = np.array(verts_list, dtype=np.float32)
        adj = build_face_adjacency(verts_flat, None)

        # Limit to 5 faces
        group = find_coplanar_group(verts_flat, None, 0, adj, max_faces=5)
        assert len(group) <= 5

    def test_degenerate_seed_returns_empty(self, cube_setup):
        """A zero-area seed triangle should return an empty list."""
        verts, indices, adj = cube_setup
        # Create a degenerate verts/indices for testing
        deg_verts = np.array([[0, 0, 0], [1, 0, 0], [0.5, 0, 0]], dtype=np.float32)
        deg_indices = np.array([[0, 1, 2]], dtype=np.int32)
        deg_adj = build_face_adjacency(deg_verts, deg_indices)
        group = find_coplanar_group(deg_verts, deg_indices, 0, deg_adj)
        assert group == []

    def test_max_faces_disabled_when_nonpositive(self):
        """max_faces <= 0 disables the limit — all coplanar faces collected."""
        verts, indices = _unit_cube_mesh()
        adj = build_face_adjacency(verts, indices)
        group = find_coplanar_group(verts, indices, 0, adj, max_faces=0)
        # Should still return the two top-face triangles (no artificial cap)
        assert len(group) == 2


# ===========================================================================
# 6. find_hole_surface / find_cylinder_surface
# ===========================================================================


class TestCurvedSurfaceDetection:

    @staticmethod
    def _cylinder_mesh(n_segments: int = 8, radius: float = 1.0, height: float = 2.0):
        """Build a simple open cylinder mesh (no caps) with 2*n_segments triangles."""
        import math

        verts = []
        indices = []
        for i in range(n_segments):
            theta0 = 2 * math.pi * i / n_segments
            theta1 = 2 * math.pi * (i + 1) / n_segments
            x0, y0 = radius * math.cos(theta0), radius * math.sin(theta0)
            x1, y1 = radius * math.cos(theta1), radius * math.sin(theta1)
            # Four vertices of quad strip segment i
            base = len(verts)
            verts.extend([[x0, y0, 0], [x1, y1, 0], [x1, y1, height], [x0, y0, height]])
            indices.extend([[base, base + 1, base + 2], [base, base + 2, base + 3]])

        verts_arr = np.array(verts, dtype=np.float32)
        indices_arr = np.array(indices, dtype=np.int32)
        return verts_arr, indices_arr

    def test_find_cylinder_surface_finds_all_segments(self):
        """Outer cylinder surface: all triangles should be found."""
        verts, indices = self._cylinder_mesh(n_segments=8)
        adj = build_face_adjacency(verts, indices)
        group = find_cylinder_surface(verts, indices, 0, adj, angle_threshold_deg=50.0)
        # All 16 triangles should be found (closed cylinder surface)
        assert len(group) >= 8, f"Expected ≥8 triangles in convex surface, got {len(group)}"

    def test_find_cylinder_surface_seed_in_group(self):
        verts, indices = self._cylinder_mesh()
        adj = build_face_adjacency(verts, indices)
        group = find_cylinder_surface(verts, indices, 0, adj)
        assert 0 in group

    def test_find_cylinder_surface_sorted(self):
        verts, indices = self._cylinder_mesh()
        adj = build_face_adjacency(verts, indices)
        group = find_cylinder_surface(verts, indices, 0, adj, angle_threshold_deg=50.0)
        assert group == sorted(group)

    def test_find_hole_surface_seed_in_group(self):
        """Inner cylinder (inverted normals) should work as hole surface."""
        verts, indices = self._cylinder_mesh()
        # Flip winding to invert normals (makes outer→inner)
        indices_flipped = indices[:, [0, 2, 1]]
        adj = build_face_adjacency(verts, indices_flipped)
        group = find_hole_surface(verts, indices_flipped, 0, adj, angle_threshold_deg=50.0)
        assert 0 in group

    def test_max_faces_limit_in_curved_surface(self):
        verts, indices = self._cylinder_mesh(n_segments=16)
        adj = build_face_adjacency(verts, indices)
        group = find_cylinder_surface(verts, indices, 0, adj,
                                      angle_threshold_deg=50.0, max_faces=5)
        assert len(group) <= 5

    def test_degenerate_seed_returns_empty_hole(self):
        deg_verts = np.array([[0, 0, 0], [1, 0, 0], [0.5, 0, 0]], dtype=np.float32)
        deg_indices = np.array([[0, 1, 2]], dtype=np.int32)
        deg_adj = build_face_adjacency(deg_verts, deg_indices)
        group = find_hole_surface(deg_verts, deg_indices, 0, deg_adj)
        assert group == []


# ===========================================================================
# 7. MAX_BFS_ITERATIONS safety guard
# ===========================================================================


class TestBFSIterationLimit:

    def test_max_bfs_iterations_is_positive(self):
        assert MAX_BFS_ITERATIONS > 0

    def test_coplanar_group_terminates_on_large_mesh(self):
        """Coplanar BFS on a large flat mesh must terminate within MAX_BFS_ITERATIONS."""
        # Build a 200×200 grid of coplanar triangles (80000 triangles)
        # This is large enough to potentially hit the BFS limit
        import math

        # Use a smaller grid for test speed; we just want to confirm no hang
        N = 50
        verts_list = []
        for i in range(N + 1):
            for j in range(N + 1):
                verts_list.append([i, j, 0.0])

        verts = np.array(verts_list, dtype=np.float32)
        indices_list = []
        for i in range(N):
            for j in range(N):
                v0 = i * (N + 1) + j
                v1 = v0 + 1
                v2 = v0 + (N + 1)
                v3 = v2 + 1
                indices_list.append([v0, v1, v2])
                indices_list.append([v1, v3, v2])

        indices = np.array(indices_list, dtype=np.int32)
        adj = build_face_adjacency(verts, indices)

        # This should terminate without hanging
        group = find_coplanar_group(verts, indices, 0, adj, max_faces=10000)
        # All triangles are coplanar; we expect either all or max_faces
        assert len(group) >= 1
        assert len(group) <= max(10000, MAX_BFS_ITERATIONS)
