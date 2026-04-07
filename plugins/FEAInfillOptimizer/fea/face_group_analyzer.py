# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Geometry analysis helpers for intelligent face-group selection.

Given a single *clicked* triangle face on a mesh, these utilities can expand
the selection to an entire "user-perceived face":

- **Coplanar group** — all connected triangles that lie in the same plane
  (e.g. the top face of a cube, regardless of how many triangles tile it).
- **Hole / pocket surface** — all connected triangles forming a concave
  (inward-curving) surface, such as the inside wall of a circular hole.
- **Cylinder / post surface** — the mirror case: all connected triangles
  forming a convex (outward-curving) surface, such as the outside of a
  cylindrical boss.

Design constraints
------------------
- **No Cura / UM imports** — pure numpy so the module is testable without a
  running Cura instance.  The calling code (``BoundaryConditionTool``) is
  responsible for extracting ``verts`` (``np.ndarray`` of shape ``(N, 3)``)
  and ``indices`` (``np.ndarray`` of shape ``(M, 3)`` int, or ``None`` for
  flat/non-indexed meshes) from ``MeshData`` before calling these functions.
- Thread-safe (no module-level mutable state).
- O(n_faces) adjacency build; O(n_group) BFS per query.

Typical usage
-------------
>>> import numpy as np
>>> from fea.face_group_analyzer import build_face_adjacency, find_coplanar_group
>>>
>>> # Simple unit cube — 12 triangles (2 per face × 6 faces)
>>> verts = np.array([
...     [0,0,0],[1,0,0],[1,1,0],[0,1,0],
...     [0,0,1],[1,0,1],[1,1,1],[0,1,1],
... ], dtype=np.float32)
>>> indices = np.array([
...     [4,5,6],[4,6,7],  # top z=1
...     [0,3,2],[0,2,1],  # bottom z=0
...     [0,1,5],[0,5,4],  # front y=0
...     [3,7,6],[3,6,2],  # back y=1
...     [0,4,7],[0,7,3],  # left x=0
...     [1,2,6],[1,6,5],  # right x=1
... ], dtype=np.int32)
>>> adj = build_face_adjacency(verts, indices)
>>> group = find_coplanar_group(verts, indices, seed_face=0, adjacency=adj)
>>> group  # both top-face triangles
[0, 1]
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np

# Absolute iteration limit for all BFS loops — prevents hangs on degenerate geometry
MAX_BFS_ITERATIONS = 50000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_face_vertices(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    face_idx: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return the three vertex positions of triangle *face_idx*.

    Args:
        verts: Float array of shape ``(N, 3)`` containing vertex positions.
        indices: Int array of shape ``(M, 3)`` mapping face indices to vertex
            indices, or ``None`` for flat (non-indexed) meshes where every
            three consecutive rows in *verts* form a triangle.
        face_idx: Zero-based triangle index.

    Returns:
        Tuple ``(v0, v1, v2)`` where each element is a ``(3,)`` float64
        ndarray containing the ``(x, y, z)`` position of one vertex.

    Examples:
        >>> v0, v1, v2 = _get_face_vertices(verts, indices, 0)
        >>> v0.shape
        (3,)
    """
    if indices is not None:
        if face_idx < 0 or face_idx >= indices.shape[0]:
            raise IndexError(
                f"face_idx {face_idx} out of range for mesh with {indices.shape[0]} faces"
            )
        i0 = int(indices[face_idx, 0])
        i1 = int(indices[face_idx, 1])
        i2 = int(indices[face_idx, 2])
        n_verts = verts.shape[0]
        if i0 < 0 or i0 >= n_verts or i1 < 0 or i1 >= n_verts or i2 < 0 or i2 >= n_verts:
            raise IndexError(
                f"Vertex index out of range: face {face_idx} references "
                f"vertices ({i0}, {i1}, {i2}) but mesh has {n_verts} vertices"
            )
        return (
            verts[i0].astype(np.float64),
            verts[i1].astype(np.float64),
            verts[i2].astype(np.float64),
        )
    # Flat layout: face k occupies vertex rows 3k, 3k+1, 3k+2
    base = face_idx * 3
    n_verts = verts.shape[0]
    if base + 2 >= n_verts or face_idx < 0:
        raise IndexError(
            f"face_idx {face_idx} out of range for flat mesh with {n_verts} vertices "
            f"({n_verts // 3} faces)"
        )
    return (
        verts[base].astype(np.float64),
        verts[base + 1].astype(np.float64),
        verts[base + 2].astype(np.float64),
    )


def _face_normal(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    face_idx: int,
) -> np.ndarray:
    """Compute the unit normal of triangle *face_idx*.

    The normal direction follows the right-hand rule for the vertex ordering
    stored in *indices* (or the flat layout when *indices* is ``None``).
    Degenerate triangles (zero area) return the zero vector ``[0, 0, 0]``
    rather than raising an exception; callers should check for this and skip
    such faces.

    Args:
        verts: Float array of shape ``(N, 3)``.
        indices: Int array of shape ``(M, 3)`` or ``None``.
        face_idx: Zero-based triangle index.

    Returns:
        Unit normal as a ``(3,)`` float64 ndarray, or the zero vector for
        degenerate (zero-area) triangles.

    Examples:
        >>> n = _face_normal(verts, indices, 0)
        >>> abs(np.linalg.norm(n) - 1.0) < 1e-9  # unit length
        True
    """
    v0, v1, v2 = _get_face_vertices(verts, indices, face_idx)
    # Guard against NaN/inf vertices from corrupt mesh data
    if not (np.all(np.isfinite(v0)) and np.all(np.isfinite(v1)) and np.all(np.isfinite(v2))):
        return np.zeros(3, dtype=np.float64)
    cross = np.cross(v1 - v0, v2 - v0)
    length = float(np.linalg.norm(cross))
    if length < 1e-12:
        return np.zeros(3, dtype=np.float64)
    return cross / length


def _face_centroid(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    face_idx: int,
) -> np.ndarray:
    """Compute the centroid (arithmetic mean of vertices) of triangle *face_idx*.

    Args:
        verts: Float array of shape ``(N, 3)``.
        indices: Int array of shape ``(M, 3)`` or ``None``.
        face_idx: Zero-based triangle index.

    Returns:
        Centroid as a ``(3,)`` float64 ndarray.

    Examples:
        >>> c = _face_centroid(verts, indices, 0)
        >>> c.shape
        (3,)
    """
    v0, v1, v2 = _get_face_vertices(verts, indices, face_idx)
    return (v0 + v1 + v2) / 3.0


# ---------------------------------------------------------------------------
# Adjacency graph
# ---------------------------------------------------------------------------

def build_face_adjacency(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    *,
    position_tolerance: float = 1e-6,
) -> Dict[int, List[int]]:
    """Build a face-adjacency graph for a triangle mesh.

    Two faces are *adjacent* when they share exactly one edge (two vertices).
    The graph is stored as a dictionary mapping each face index to the list of
    neighbouring face indices.

    **Indexed meshes** (``indices`` is not ``None``)
        Shared edges are detected by comparing vertex *indices*.  An edge is
        represented as the sorted pair ``(min(i, j), max(i, j))`` of the two
        endpoint vertex indices.

    **Non-indexed / flat meshes** (``indices`` is ``None``)
        Vertex positions are compared with a tolerance ``position_tolerance``
        (in the same units as *verts*).  A spatial hash maps rounded
        positions to canonical vertex IDs so that duplicate positions
        (inevitable in flat meshes) are treated as the same vertex.

    Non-manifold edges (more than two faces sharing the same edge) are handled
    correctly: every face on such an edge is listed as a neighbour of every
    other face on that edge.

    Args:
        verts: Float array of shape ``(N, 3)`` containing vertex positions.
        indices: Int array of shape ``(M, 3)`` mapping face→vertex, or
            ``None`` for flat meshes.
        position_tolerance: Spatial snapping tolerance used only when
            ``indices`` is ``None``.  Two vertex positions whose coordinates
            all differ by less than this value are treated as identical.
            Defaults to ``1e-6``.

    Returns:
        ``{face_idx: [neighbour_face_idx, ...], ...}`` for every face.  Faces
        with no neighbours map to an empty list.

    Complexity:
        O(n_faces) edge insertions and O(n_faces) edge lookups on average.

    Examples:
        >>> # Two triangles sharing edge (v1, v2)
        >>> verts = np.array([[0,0,0],[1,0,0],[0,1,0],[1,1,0]], dtype=np.float32)
        >>> idx   = np.array([[0,1,2],[1,3,2]], dtype=np.int32)
        >>> adj   = build_face_adjacency(verts, idx)
        >>> sorted(adj[0])
        [1]
        >>> sorted(adj[1])
        [0]
    """
    if indices is not None:
        return _build_adjacency_indexed(indices)
    return _build_adjacency_flat(verts, position_tolerance)


def _build_adjacency_indexed(indices: np.ndarray) -> Dict[int, List[int]]:
    """Adjacency for indexed meshes — edges identified by vertex-index pairs."""
    n_faces = indices.shape[0]
    # edge_to_faces: sorted edge tuple → list of face indices
    edge_to_faces: Dict[Tuple[int, int], List[int]] = {}

    for fi in range(n_faces):
        tri = indices[fi]
        # Three edges of triangle fi
        for a, b in (
            (int(tri[0]), int(tri[1])),
            (int(tri[1]), int(tri[2])),
            (int(tri[2]), int(tri[0])),
        ):
            edge = (min(a, b), max(a, b))
            if edge not in edge_to_faces:
                edge_to_faces[edge] = []
            edge_to_faces[edge].append(fi)

    adjacency: Dict[int, List[int]] = {fi: [] for fi in range(n_faces)}
    for face_list in edge_to_faces.values():
        if len(face_list) < 2:
            continue
        # Add every pair (handles non-manifold edges with 3+ faces)
        for i, fa in enumerate(face_list):
            for fb in face_list[i + 1:]:
                adjacency[fa].append(fb)
                adjacency[fb].append(fa)

    # Deduplicate neighbor lists (non-manifold edges can cause duplicates)
    for fi in adjacency:
        if len(adjacency[fi]) != len(set(adjacency[fi])):
            adjacency[fi] = list(set(adjacency[fi]))

    return adjacency


def _build_adjacency_flat(
    verts: np.ndarray,
    tolerance: float,
) -> Dict[int, List[int]]:
    """Adjacency for flat (non-indexed) meshes — edges identified by position."""
    n_verts = verts.shape[0]
    if n_verts % 3 != 0:
        raise ValueError(
            f"Flat mesh vertex count ({n_verts}) is not divisible by 3; "
            "cannot determine face boundaries."
        )
    n_faces = n_verts // 3

    # Assign canonical vertex IDs by snapping positions to a grid.
    inv_tol = 1.0 / tolerance
    pos_to_vid: Dict[Tuple[int, int, int], int] = {}
    vid_of_flat: List[int] = []  # vid_of_flat[i] = canonical ID for raw vertex i

    next_vid = 0
    for raw_idx in range(n_verts):
        x, y, z = float(verts[raw_idx, 0]), float(verts[raw_idx, 1]), float(verts[raw_idx, 2])
        key = (int(round(x * inv_tol)), int(round(y * inv_tol)), int(round(z * inv_tol)))
        if key not in pos_to_vid:
            pos_to_vid[key] = next_vid
            next_vid += 1
        vid_of_flat.append(pos_to_vid[key])

    # Build edge → faces using canonical IDs, same as the indexed case.
    edge_to_faces: Dict[Tuple[int, int], List[int]] = {}
    for fi in range(n_faces):
        base = fi * 3
        tri_vids = (vid_of_flat[base], vid_of_flat[base + 1], vid_of_flat[base + 2])
        for a, b in (
            (tri_vids[0], tri_vids[1]),
            (tri_vids[1], tri_vids[2]),
            (tri_vids[2], tri_vids[0]),
        ):
            edge = (min(a, b), max(a, b))
            if edge not in edge_to_faces:
                edge_to_faces[edge] = []
            edge_to_faces[edge].append(fi)

    adjacency: Dict[int, List[int]] = {fi: [] for fi in range(n_faces)}
    for face_list in edge_to_faces.values():
        if len(face_list) < 2:
            continue
        for i, fa in enumerate(face_list):
            for fb in face_list[i + 1:]:
                adjacency[fa].append(fb)
                adjacency[fb].append(fa)

    # Deduplicate neighbor lists (non-manifold edges can cause duplicates)
    for fi in adjacency:
        if len(adjacency[fi]) != len(set(adjacency[fi])):
            adjacency[fi] = list(set(adjacency[fi]))

    return adjacency


# ---------------------------------------------------------------------------
# Coplanar group
# ---------------------------------------------------------------------------

def find_coplanar_group(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    seed_face: int,
    adjacency: Dict[int, List[int]],
    *,
    angle_threshold_deg: float = 3.0,
    max_faces: int = 5000,
) -> List[int]:
    """Flood-fill to find all connected coplanar faces from *seed_face*.

    Starting at *seed_face*, performs a BFS over the adjacency graph and
    collects every reachable face whose normal is within *angle_threshold_deg*
    of the **seed** normal.  This identifies what a human user perceives as a
    single flat surface — for example, all triangles tiling the top face of a
    cube, regardless of tessellation.

    The seed normal is used as the angular reference for *every* candidate
    (not the current BFS-frontier normal).  This ensures strict planarity:
    a slightly-tilted neighbour cannot "pull" the accepted region off the
    original plane over many BFS hops.

    Degenerate triangles (zero-area, detected by a zero normal vector) are
    **excluded** from the result even if they are reachable.

    Args:
        verts: Float array of shape ``(N, 3)``.
        indices: Int array of shape ``(M, 3)`` or ``None`` for flat meshes.
        seed_face: Index of the triangle clicked by the user.
        adjacency: Pre-built adjacency graph from :func:`build_face_adjacency`.
        angle_threshold_deg: Maximum angular deviation (in degrees) between
            the seed normal and a candidate face's normal for the candidate to
            be included.  Defaults to ``3.0°``.
        max_faces: Maximum number of faces to collect before stopping the BFS.
            Prevents runaway exploration on large smooth surfaces. Defaults
            to ``5000``.  Set to ``0`` or negative to disable the limit.

    Returns:
        Sorted list of face indices belonging to the coplanar group (always
        includes *seed_face* unless it is itself degenerate).

    Examples:
        >>> adj = build_face_adjacency(verts, indices)
        >>> group = find_coplanar_group(verts, indices, seed_face=0, adjacency=adj)
        >>> seed_face in group
        True
    """
    seed_normal = _face_normal(verts, indices, seed_face)
    if float(np.linalg.norm(seed_normal)) < 1e-12:
        # Degenerate seed — return empty
        return []

    # Two normals are "coplanar" if |dot| ≥ cos_threshold.
    # We use |dot| (not dot) to handle opposite-winding cases on thin shells.
    cos_threshold = float(np.cos(np.radians(angle_threshold_deg)))

    # ``seen``     — faces we have already evaluated (prevents re-queuing).
    # ``accepted`` — faces that passed the coplanarity test (the result set).
    seen: set[int] = set()
    accepted: set[int] = set()
    queue: deque[int] = deque()

    # The seed is accepted unconditionally (we already checked it's non-degenerate).
    seen.add(seed_face)
    accepted.add(seed_face)
    queue.append(seed_face)

    limit_active = max_faces > 0
    iterations = 0

    while queue:
        if iterations >= MAX_BFS_ITERATIONS:
            break
        iterations += 1
        current = queue.popleft()

        for neighbour in adjacency.get(current, []):
            if neighbour in seen:
                continue
            seen.add(neighbour)  # mark seen regardless of outcome

            nb_normal = _face_normal(verts, indices, neighbour)
            if float(np.linalg.norm(nb_normal)) < 1e-12:
                # Degenerate — don't accept, don't propagate through
                continue

            dot = float(np.dot(seed_normal, nb_normal))
            if abs(dot) >= cos_threshold:
                accepted.add(neighbour)
                if limit_active and len(accepted) >= max_faces:
                    return sorted(accepted)
                queue.append(neighbour)
            # If not accepted: still marked in ``seen``, so we won't re-check.
            # BFS stops propagating through rejected faces, so disconnected
            # coplanar regions on the far side of a wall are not included.

    return sorted(accepted)


# ---------------------------------------------------------------------------
# Hole / concave surface
# ---------------------------------------------------------------------------

def find_hole_surface(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    seed_face: int,
    adjacency: Dict[int, List[int]],
    *,
    angle_threshold_deg: float = 20.0,
    max_faces: int = 5000,
) -> List[int]:
    """Flood-fill to find all connected faces forming a concave (hole) surface.

    Starting from *seed_face*, performs a BFS collecting adjacent faces that:

    1. Have normals within *angle_threshold_deg* of the **current** (parent)
       face in the traversal path — gradual angular continuity, **and**
    2. Curve *inward* relative to the current face — local dihedral is concave.

    This selects surfaces like the cylindrical wall of a drilled hole, an
    interior pocket, or a concave fillet.

    Local dihedral concavity test
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    For two adjacent faces A (current) and B (neighbour):

    .. code-block:: python

        n_A · (centroid_B - centroid_A) > 0   →  concave

    Geometrically: on a concave (bowl-shaped) surface, the neighbour's
    centroid is displaced *along* the current normal direction.  On a convex
    (hill-shaped) surface the sign is reversed.  Coplanar pairs yield ≈ 0
    and are excluded (they belong to flat-face groups, not curved ones).

    Args:
        verts: Float array of shape ``(N, 3)``.
        indices: Int array of shape ``(M, 3)`` or ``None`` for flat meshes.
        seed_face: Index of the seed triangle.
        adjacency: Pre-built adjacency graph from :func:`build_face_adjacency`.
        angle_threshold_deg: Maximum angular deviation between adjacent face
            normals to continue the flood fill.  Defaults to ``20.0°``.
        max_faces: Maximum number of faces to collect before stopping the BFS.
            Prevents runaway exploration on large smooth surfaces. Defaults
            to ``5000``.  Set to ``0`` or negative to disable the limit.

    Returns:
        Sorted list of face indices in the concave surface group.

    Examples:
        >>> # Inner wall of a cylinder has concave curvature
        >>> group = find_hole_surface(verts, indices, seed_face=0, adjacency=adj)
        >>> len(group) > 0
        True
    """
    return _flood_fill_curved(
        verts, indices, seed_face, adjacency,
        angle_threshold_deg=angle_threshold_deg,
        concave=True,
        max_faces=max_faces,
    )


# ---------------------------------------------------------------------------
# Cylinder / convex surface
# ---------------------------------------------------------------------------

def find_cylinder_surface(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    seed_face: int,
    adjacency: Dict[int, List[int]],
    *,
    angle_threshold_deg: float = 20.0,
    max_faces: int = 5000,
) -> List[int]:
    """Flood-fill to find all connected faces forming a convex (cylinder) surface.

    Mirror of :func:`find_hole_surface`: collects adjacent faces that curve
    *outward* relative to the current face (local dihedral is convex).

    Local dihedral convexity test
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    For two adjacent faces A (current) and B (neighbour):

    .. code-block:: python

        n_A · (centroid_B - centroid_A) < 0   →  convex

    Geometrically: on a convex (hill-shaped) surface, the neighbour's
    centroid is displaced *opposite* to the current normal direction — the
    surface bends away from the viewer.  This is what you see on the outside
    of a cylinder, dome, or boss.

    Args:
        verts: Float array of shape ``(N, 3)``.
        indices: Int array of shape ``(M, 3)`` or ``None`` for flat meshes.
        seed_face: Index of the seed triangle.
        adjacency: Pre-built adjacency graph from :func:`build_face_adjacency`.
        angle_threshold_deg: Maximum angular deviation between adjacent face
            normals to continue the flood fill.  Defaults to ``20.0°``.
        max_faces: Maximum number of faces to collect before stopping the BFS.
            Prevents runaway exploration on large smooth surfaces. Defaults
            to ``5000``.  Set to ``0`` or negative to disable the limit.

    Returns:
        Sorted list of face indices in the convex surface group.

    Examples:
        >>> # Outer wall of a cylinder has convex curvature
        >>> group = find_cylinder_surface(verts, indices, seed_face=0, adjacency=adj)
        >>> len(group) > 0
        True
    """
    return _flood_fill_curved(
        verts, indices, seed_face, adjacency,
        angle_threshold_deg=angle_threshold_deg,
        concave=False,
        max_faces=max_faces,
    )


# ---------------------------------------------------------------------------
# Shared BFS for curved-surface detection
# ---------------------------------------------------------------------------

def _flood_fill_curved(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    seed_face: int,
    adjacency: Dict[int, List[int]],
    *,
    angle_threshold_deg: float,
    concave: bool,
    max_faces: int = 5000,
) -> List[int]:
    """Internal BFS for :func:`find_hole_surface` and :func:`find_cylinder_surface`.

    Two-gate acceptance criterion
    ------------------------------
    For each BFS step from face A (current) to face B (candidate neighbour):

    **Gate 1 — Angular continuity:**
    ``dot(n_A, n_B) >= cos(angle_threshold_deg)``

    Ensures the surface bends gradually.  The threshold is applied between
    *directly adjacent* faces (not the seed), so the BFS can follow a full
    360° cylinder even though the total curvature vastly exceeds any
    reasonable threshold.

    **Gate 2 — Local dihedral sign (curvature direction):**

    .. code-block:: python

        dihedral_sign = n_A · (centroid_B - centroid_A)

    - ``dihedral_sign > +eps`` → **concave** dihedral (B's centroid is on
      the positive-normal side of A's plane).
    - ``dihedral_sign < -eps`` → **convex** dihedral (B's centroid is on
      the negative-normal side).
    - ``|dihedral_sign| ≤ eps`` → **coplanar** pair (e.g. same-quad
      diagonal partner, two triangles tiling one rectangular segment).
      Coplanar pairs are admitted as *pass-through* so the BFS can
      traverse within a flat segment strip to reach the next curved
      segment, while still being accepted into the result.  This is the
      correct behaviour: a cylinder wall built from 2-triangle quads
      needs to cross the coplanar intra-quad edge to reach the adjacent
      segment.

    The curvature-direction tolerance ``eps`` is set to the centroid
    displacement magnitude times ``1e-4``, which filters numerical noise
    while being robust to very large or very small meshes.

    BFS correctness
    ---------------
    Faces are added to ``seen`` on first encounter (before any test) to
    guarantee O(n) termination — each face is evaluated at most once.
    Faces are added to ``accepted`` only when Gate 1 passes and Gate 2
    does not *actively reject* (i.e. the dihedral is either correct-sign
    or coplanar).  Only accepted faces are enqueued for further
    propagation.

    Args:
        verts: Float array of shape ``(N, 3)``.
        indices: Int array of shape ``(M, 3)`` or ``None`` for flat meshes.
        seed_face: Index of the starting triangle.
        adjacency: Pre-built adjacency graph.
        angle_threshold_deg: Max angular deviation per BFS step.
        concave: ``True`` → concave (hole/pocket); ``False`` → convex (cylinder/boss).
        max_faces: Maximum number of faces to collect before stopping the BFS.
            Prevents runaway exploration on large smooth surfaces. Defaults
            to ``5000``.  Set to ``0`` or negative to disable the limit.

    Returns:
        Sorted list of admitted face indices.
    """
    seed_normal = _face_normal(verts, indices, seed_face)
    if float(np.linalg.norm(seed_normal)) < 1e-12:
        return []

    cos_threshold = float(np.cos(np.radians(angle_threshold_deg)))
    limit_active = max_faces > 0

    # ``seen``     — prevents re-evaluating the same face.
    # ``accepted`` — the result set; only accepted faces are enqueued.
    seen: set[int] = set()
    accepted: set[int] = set()

    # Queue entries: (face_idx, normal_of_that_face, centroid_of_that_face)
    # Carrying per-face geometry enables the local dihedral test between
    # directly adjacent pairs without recomputing for each neighbour lookup.
    seed_centroid = _face_centroid(verts, indices, seed_face)
    queue: deque[Tuple[int, np.ndarray, np.ndarray]] = deque()

    seen.add(seed_face)
    accepted.add(seed_face)
    queue.append((seed_face, seed_normal, seed_centroid))

    iterations = 0
    while queue:
        if iterations >= MAX_BFS_ITERATIONS:
            break
        iterations += 1
        current, current_normal, current_centroid = queue.popleft()

        for neighbour in adjacency.get(current, []):
            if neighbour in seen:
                continue
            seen.add(neighbour)  # mark on first encounter — evaluated once

            nb_normal = _face_normal(verts, indices, neighbour)
            if float(np.linalg.norm(nb_normal)) < 1e-12:
                continue  # degenerate — skip silently

            # --- Gate 1: Angular continuity --------------------------------
            dot_angle = float(np.dot(current_normal, nb_normal))
            if dot_angle < cos_threshold:
                continue

            # --- Gate 2: Local dihedral curvature direction -----------------
            # dihedral_sign = n_current · (centroid_neighbour - centroid_current)
            #   > +eps → concave
            #   < -eps → convex
            #   |·| ≤ eps → coplanar (pass-through: admit but don't classify)
            nb_centroid = _face_centroid(verts, indices, neighbour)
            if not np.all(np.isfinite(nb_centroid)):
                continue  # degenerate centroid — skip
            displacement = nb_centroid - current_centroid
            dihedral_sign = float(np.dot(current_normal, displacement))

            # Adaptive epsilon: fraction of the centroid-to-centroid distance
            # so it scales with mesh size.  A fixed 1e-8 would fail on very
            # coarse or very dense meshes.
            eps = float(np.linalg.norm(displacement)) * 1e-4

            if concave:
                # Reject only if clearly convex (sign strongly negative)
                if dihedral_sign < -eps:
                    continue
            else:
                # Reject only if clearly concave (sign strongly positive)
                if dihedral_sign > eps:
                    continue
            # dihedral ≈ 0 (coplanar pair): admitted as pass-through

            # All gates passed — accept and propagate.
            accepted.add(neighbour)
            if limit_active and len(accepted) >= max_faces:
                return sorted(accepted)
            queue.append((neighbour, nb_normal, nb_centroid))

    return sorted(accepted)
