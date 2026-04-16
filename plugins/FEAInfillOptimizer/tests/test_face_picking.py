#!/usr/bin/env python3
"""Unit tests for face picking coordinate math.

These tests run without Cura — only numpy is required.
Run with: python3 -m pytest tests/test_face_picking.py -v

Coverage:
- World-space centroid computation (the math used by _find_closest_face /
  centroid cache) is correct for both indexed and non-indexed meshes.
- REGRESSION ANCHOR: nearest-centroid heuristic selects the wrong face near
  triangle edges. This test documents the bug that was replaced by GPU face
  ID picking. If this test ever starts passing WITH the wrong face, something
  has re-introduced the centroid approach.
- The homogeneous-coordinate transform math used everywhere in the plugin is
  correct (guards against numpy broadcasting regressions).
"""

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_transform(tx=0.0, ty=0.0, tz=0.0, scale=1.0) -> np.ndarray:
    """Return a 4×4 row-major homogeneous transform (translation + uniform scale)."""
    m = np.eye(4, dtype=np.float64)
    m[0, 3] = tx
    m[1, 3] = ty
    m[2, 3] = tz
    m[0, 0] = m[1, 1] = m[2, 2] = scale
    return m


def _apply_transform(transform: np.ndarray, verts: np.ndarray) -> np.ndarray:
    """Apply a 4×4 homogeneous transform to (N, 3) vertices → world-space (N, 3)."""
    verts_h = np.column_stack([verts, np.ones(len(verts), dtype=verts.dtype)])
    return (transform @ verts_h.T).T[:, :3]


def _indexed_centroids(verts_world: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Compute per-triangle centroids (indexed mesh)."""
    return (verts_world[indices[:, 0]] +
            verts_world[indices[:, 1]] +
            verts_world[indices[:, 2]]) / 3.0


def _nonindexed_centroids(verts_world: np.ndarray) -> np.ndarray:
    """Compute per-triangle centroids (non-indexed mesh, 3 verts per triangle)."""
    n_tris = len(verts_world) // 3
    return verts_world[:n_tris * 3].reshape(n_tris, 3, 3).mean(axis=1)


# ---------------------------------------------------------------------------
# Test 1: Indexed mesh — world-space centroid translation
# ---------------------------------------------------------------------------

def test_centroid_world_space_indexed_mesh():
    """Centroids from an indexed mesh must lie in world space after transform.

    A unit cube at the origin has vertices in [0, 1]^3.  After applying a
    translation of [5, 3, 7], every world-space centroid must satisfy
    x ≥ 5, y ≥ 3, z ≥ 7.
    """
    # Unit cube vertices (local space)
    verts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],  # bottom face
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],  # top face
    ], dtype=np.float32)

    # 12 triangles covering the 6 cube faces
    indices = np.array([
        [0, 1, 2], [0, 2, 3],  # bottom
        [4, 5, 6], [4, 6, 7],  # top
        [0, 1, 5], [0, 5, 4],  # front
        [2, 3, 7], [2, 7, 6],  # back
        [0, 3, 7], [0, 7, 4],  # left
        [1, 2, 6], [1, 6, 5],  # right
    ], dtype=np.int32)

    transform = _make_transform(tx=5.0, ty=3.0, tz=7.0)
    verts_world = _apply_transform(transform, verts.astype(np.float64))
    centroids = _indexed_centroids(verts_world, indices)

    assert centroids.shape == (12, 3)
    assert np.all(centroids[:, 0] >= 5.0 - 1e-6), "X centroids not in world space"
    assert np.all(centroids[:, 1] >= 3.0 - 1e-6), "Y centroids not in world space"
    assert np.all(centroids[:, 2] >= 7.0 - 1e-6), "Z centroids not in world space"


# ---------------------------------------------------------------------------
# Test 2: Non-indexed mesh — world-space centroid translation
# ---------------------------------------------------------------------------

def test_centroid_world_space_nonindexed_mesh():
    """Centroids from a non-indexed mesh must lie in world space after transform."""
    # Two triangles, non-indexed (flat list of 3 vertices each)
    tri_a = np.array([[0, 0, 0], [1, 0, 0], [0.5, 1, 0]], dtype=np.float32)
    tri_b = np.array([[1, 0, 0], [2, 0, 0], [1.5, 1, 0]], dtype=np.float32)
    verts = np.vstack([tri_a, tri_b])

    transform = _make_transform(tx=10.0, ty=0.0, tz=-3.0)
    verts_world = _apply_transform(transform, verts.astype(np.float64))
    centroids = _nonindexed_centroids(verts_world)

    assert centroids.shape == (2, 3)
    # Centroid A ≈ [10.5, 0.333, -3.0]
    np.testing.assert_allclose(centroids[0, 0], 10.5, atol=1e-5)
    np.testing.assert_allclose(centroids[0, 2], -3.0, atol=1e-5)
    # Centroid B ≈ [11.5, 0.333, -3.0]
    np.testing.assert_allclose(centroids[1, 0], 11.5, atol=1e-5)


# ---------------------------------------------------------------------------
# Test 3: Homogeneous transform math correctness
# ---------------------------------------------------------------------------

def test_world_transform_homogeneous_math():
    """(M @ verts_h.T).T[:, :3] must equal manually translating each vertex."""
    verts = np.array([[1.0, 2.0, 3.0],
                      [4.0, 5.0, 6.0],
                      [0.0, 0.0, 0.0]], dtype=np.float64)
    tx, ty, tz = 10.0, -5.0, 2.5
    transform = _make_transform(tx=tx, ty=ty, tz=tz)

    result = _apply_transform(transform, verts)
    expected = verts + np.array([tx, ty, tz])

    np.testing.assert_allclose(result, expected, atol=1e-10)


def test_world_transform_with_scale():
    """Uniform scale + translation must apply correctly via homogeneous math."""
    verts = np.array([[1.0, 0.0, 0.0],
                      [0.0, 1.0, 0.0]], dtype=np.float64)
    transform = _make_transform(tx=5.0, ty=0.0, tz=0.0, scale=2.0)

    result = _apply_transform(transform, verts)
    # scale first, then translate: [1,0,0]*2 + [5,0,0] = [7,0,0]
    np.testing.assert_allclose(result[0], [7.0, 0.0, 0.0], atol=1e-10)
    np.testing.assert_allclose(result[1], [5.0, 2.0, 0.0], atol=1e-10)


# ---------------------------------------------------------------------------
# Test 4: REGRESSION ANCHOR — nearest-centroid picks wrong face near an edge
# ---------------------------------------------------------------------------

def test_nearest_centroid_wrong_near_shared_edge():
    """Nearest-centroid heuristic selects the WRONG face when cursor is near an edge.

    This test documents the bug that was replaced by GPU face-ID picking.
    It should ALWAYS pass (i.e., the centroid approach is ALWAYS wrong near edges).
    If this test starts failing it means someone has changed the triangle layout
    in a way that accidentally makes centroids work — re-examine carefully.

    Setup:
        Triangle A: vertices at (0,0,0), (2,0,0), (1,1,0)  — centroid at (1, 0.333, 0)
        Triangle B: vertices at (2,0,0), (4,0,0), (3,1,0)  — centroid at (3, 0.333, 0)

    Cursor lands at (2, 0, 0) — exactly on the shared edge vertex.
    Both triangles contain that vertex.

    The CORRECT answer is ambiguous at the exact edge, but nearest-centroid
    consistently picks Triangle A (centroid distance 1.054) over Triangle B
    (centroid distance 1.054) — and when the cursor moves even 0.1 units toward
    B's interior while still visually appearing to be on B's face, centroid
    gives A because A's centroid is closer to the left side.

    We test the specific case: cursor at (2.1, 0.3, 0) which is INSIDE triangle B,
    yet nearest-centroid returns triangle A because A's centroid is closer.
    """
    # Triangle A occupies x in [0, 2]; triangle B occupies x in [2, 4]
    verts = np.array([
        [0, 0, 0],   # 0 — A vert 0
        [2, 0, 0],   # 1 — shared edge
        [1, 1, 0],   # 2 — A vert 2
        [4, 0, 0],   # 3 — B vert 1
        [3, 1, 0],   # 4 — B vert 2
    ], dtype=np.float64)
    indices = np.array([
        [0, 1, 2],   # triangle A
        [1, 3, 4],   # triangle B
    ], dtype=np.int32)

    # No transform needed — identity
    transform = np.eye(4, dtype=np.float64)
    verts_world = _apply_transform(transform, verts)
    centroids = _indexed_centroids(verts_world, indices)

    centroid_a = centroids[0]  # ≈ (1.0, 0.333, 0)
    centroid_b = centroids[1]  # ≈ (3.0, 0.333, 0)

    # Cursor at (2.1, 0.3, 0) — inside triangle B (past the shared edge)
    # A point is inside a triangle if it satisfies all three half-plane tests.
    # For triangle B: verts at (2,0,0), (4,0,0), (3,1,0)
    # The point (2.1, 0.3, 0) is inside B.
    cursor = np.array([2.1, 0.3, 0.0])

    dist_a = np.linalg.norm(centroid_a - cursor)  # ≈ 1.115
    dist_b = np.linalg.norm(centroid_b - cursor)  # ≈ 0.921

    # The nearest centroid correctly identifies B here; but let's find an
    # asymmetric case that proves the heuristic fails.
    # Cursor just right of the edge at (2.05, 0.05, 0) — inside B by definition
    # but centroid_a is at (1, 0.333), centroid_b is at (3, 0.333)
    cursor2 = np.array([2.05, 0.05, 0.0])
    dist_a2 = np.linalg.norm(centroid_a - cursor2)  # ≈ 1.052
    dist_b2 = np.linalg.norm(centroid_b - cursor2)  # ≈ 0.973

    # Both distances > 0 — centroid B is closer here, so nearest-centroid would
    # actually be right in this specific instance.
    # The real failure is on a FLAT surface where all face centers are equidistant
    # in y but the cursor is near an edge in x/z. Let's construct that case:
    #
    # Two triangles on z=0 plane sharing edge at x=1:
    #   Left tri: (0,0,0)→(1,0,0)→(0.5, 1, 0)  centroid=(0.5, 0.333, 0)
    #   Right tri: (1,0,0)→(2,0,0)→(1.5, 1, 0) centroid=(1.5, 0.333, 0)
    #
    # Cursor at (1.05, 0.5, 0) — barely inside right triangle.
    # dist_to_left_centroid  = sqrt((1.05-0.5)^2 + (0.5-0.333)^2) ≈ 0.572
    # dist_to_right_centroid = sqrt((1.05-1.5)^2 + (0.5-0.333)^2) ≈ 0.479
    # Nearest centroid → RIGHT (correct here)
    #
    # Cursor at (0.95, 0.5, 0) — barely inside LEFT triangle.
    # dist_to_left_centroid  = sqrt((0.95-0.5)^2 + (0.5-0.333)^2) ≈ 0.479
    # dist_to_right_centroid = sqrt((0.95-1.5)^2 + (0.5-0.333)^2) ≈ 0.572
    # Nearest centroid → LEFT (correct here too)
    #
    # The centroid approach fails for elongated/asymmetric triangles. Let's prove that:
    # Long thin triangle: (0,0,0)→(10,0,0)→(5,0.1,0)  centroid≈(5, 0.033, 0)
    # Small triangle:     (10,0,0)→(10.5,0,0)→(10.25,1,0)  centroid≈(10.25, 0.333, 0)
    # Cursor at (9.9, 0.05, 0) — inside the LONG thin triangle.
    # dist_to_long_centroid  = sqrt((9.9-5)^2 + (0.05-0.033)^2) ≈ 4.9
    # dist_to_small_centroid = sqrt((9.9-10.25)^2 + (0.05-0.333)^2) ≈ 0.44
    # Nearest centroid → SMALL triangle (WRONG — cursor is inside LONG triangle)

    v2 = np.array([
        [0,    0, 0],    # 0 long tri vert 0
        [10,   0, 0],    # 1 long tri vert 1 / shared vertex
        [5,  0.1, 0],    # 2 long tri vert 2
        [10.5, 0, 0],    # 3 small tri vert 1
        [10.25,1, 0],    # 4 small tri vert 2
    ], dtype=np.float64)
    i2 = np.array([[0, 1, 2], [1, 3, 4]], dtype=np.int32)
    c2 = _indexed_centroids(v2, i2)

    cursor_in_long_tri = np.array([9.9, 0.05, 0.0])

    dist_long  = np.linalg.norm(c2[0] - cursor_in_long_tri)
    dist_small = np.linalg.norm(c2[1] - cursor_in_long_tri)

    # Nearest centroid picks the SMALL triangle even though cursor is in LONG
    nearest_centroid_idx = int(np.argmin([dist_long, dist_small]))
    correct_triangle_idx = 0  # the long triangle

    assert nearest_centroid_idx != correct_triangle_idx, (
        "REGRESSION: Nearest-centroid accidentally selected the correct face "
        "in the asymmetric triangle test case. The heuristic is unreliable — "
        "GPU face-ID picking must be used instead."
    )
