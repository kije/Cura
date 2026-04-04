# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Quick setup helpers for common boundary condition patterns.

Each function takes mesh vertices, indices, and parameters, and returns
a dict with ``fixed_faces`` and ``force_groups`` ready to apply to a
FEABoundaryConditionDecorator.

All functions are pure numpy — no Cura/UM imports.
"""

from typing import Dict, List, Optional, Tuple
import math

import numpy as np

try:
    from .face_group_analyzer import (
        build_face_adjacency,
        find_coplanar_group,
        find_hole_surface,
        find_cylinder_surface,
    )
    _ANALYZER_AVAILABLE = True
except ImportError:
    _ANALYZER_AVAILABLE = False


def _face_normal(verts, indices, face_idx) -> np.ndarray:
    """Compute unit normal of a triangle face."""
    if indices is not None:
        tri = indices[face_idx]
        v0, v1, v2 = verts[tri[0]], verts[tri[1]], verts[tri[2]]
    else:
        base = face_idx * 3
        v0, v1, v2 = verts[base], verts[base + 1], verts[base + 2]
    e1 = v1 - v0
    e2 = v2 - v0
    n = np.cross(e1, e2)
    length = np.linalg.norm(n)
    return n / length if length > 1e-12 else np.array([0, 1, 0])


def _face_centroid(verts, indices, face_idx) -> np.ndarray:
    """Compute centroid of a triangle face."""
    if indices is not None:
        tri = indices[face_idx]
        return (verts[tri[0]] + verts[tri[1]] + verts[tri[2]]) / 3.0
    else:
        base = face_idx * 3
        return (verts[base] + verts[base + 1] + verts[base + 2]) / 3.0


def gravity_from_face(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    bottom_face_idx: int,
    force_magnitude: float = 10.0,
    adjacency: Optional[dict] = None,
    bottom_threshold: float = 0.05,
    top_threshold: float = 0.05,
) -> Dict:
    """Set up gravity load based on a user-clicked bottom face.

    The face's outward normal defines "down" (gravity direction).
    Faces aligned with this normal are fixed supports (bottom).
    Faces on the opposite side receive the gravity force.

    Args:
        verts: Vertex positions (N, 3).
        indices: Triangle indices (M, 3) or None.
        bottom_face_idx: Index of the face the user clicked as "bottom".
        force_magnitude: Force in Newtons (default 10N ≈ 1kg weight).
        adjacency: Pre-built face adjacency graph (optional, built if needed).
        bottom_threshold: Fraction of model extent for bottom face selection.
        top_threshold: Fraction of model extent for top face selection.

    Returns:
        Dict with keys ``fixed_faces`` (List[int]) and
        ``force_groups`` (List of dicts with ``face_indices`` and ``force``).
    """
    # Get the normal of the clicked face — this is the "down" direction
    down_normal = _face_normal(verts, indices, bottom_face_idx)

    # Project all face centroids along this normal
    n_faces = len(indices) if indices is not None else len(verts) // 3
    projections = np.zeros(n_faces)
    for fi in range(n_faces):
        centroid = _face_centroid(verts, indices, fi)
        projections[fi] = np.dot(centroid, down_normal)

    proj_min = projections.min()
    proj_max = projections.max()
    proj_range = proj_max - proj_min
    if proj_range < 1e-6:
        return {"fixed_faces": [], "force_groups": []}

    # Bottom faces: those closest to the clicked face's plane
    bottom_cutoff = proj_min + proj_range * bottom_threshold
    # Top faces: those furthest from the clicked face's plane
    top_cutoff = proj_max - proj_range * top_threshold

    # Also filter by normal alignment
    bottom_faces = []
    top_faces = []
    for fi in range(n_faces):
        fn = _face_normal(verts, indices, fi)
        if projections[fi] <= bottom_cutoff and np.dot(fn, down_normal) > 0.5:
            bottom_faces.append(fi)
        elif projections[fi] >= top_cutoff and np.dot(fn, down_normal) < -0.5:
            top_faces.append(fi)

    # If using face group analyzer, expand the clicked face to its full
    # coplanar surface for better bottom selection
    if _ANALYZER_AVAILABLE and adjacency is not None:
        expanded_bottom = find_coplanar_group(verts, indices, bottom_face_idx, adjacency)
        # Merge with threshold-based selection
        bottom_set = set(bottom_faces) | set(expanded_bottom)
        bottom_faces = sorted(bottom_set)

    force_groups = []
    if top_faces:
        # Apply gravity force (in the "down" direction)
        force = {
            "face_indices": top_faces,
            "force": (
                float(-down_normal[0] * force_magnitude),
                float(-down_normal[1] * force_magnitude),
                float(-down_normal[2] * force_magnitude),
            ),
        }
        force_groups.append(force)

    return {"fixed_faces": bottom_faces, "force_groups": force_groups}


def mount_holes(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    max_diameter: float = 10.0,
    adjacency: Optional[dict] = None,
) -> Dict:
    """Auto-detect holes below a diameter threshold and mark as fixed supports.

    Scans all faces, finds concave (hole) surfaces, estimates their diameter,
    and marks those below ``max_diameter`` as fixed supports.

    Args:
        verts: Vertex positions (N, 3).
        indices: Triangle indices (M, 3) or None.
        max_diameter: Maximum hole diameter in mm to consider as bolt/screw holes.
        adjacency: Pre-built face adjacency graph (required).

    Returns:
        Dict with ``fixed_faces`` and empty ``force_groups``.
    """
    if not _ANALYZER_AVAILABLE or adjacency is None:
        return {"fixed_faces": [], "force_groups": []}

    visited = set()
    hole_faces = []

    n_faces = len(indices) if indices is not None else len(verts) // 3
    for fi in range(n_faces):
        if fi in visited:
            continue

        # Try to expand as a hole surface
        hole_group = find_hole_surface(verts, indices, fi, adjacency)
        if len(hole_group) < 4:
            # Too few faces — not a real hole
            visited.update(hole_group)
            continue

        # Estimate diameter: compute bounding sphere of the hole group's centroids
        centroids = np.array([_face_centroid(verts, indices, f) for f in hole_group])
        center = centroids.mean(axis=0)
        radii = np.linalg.norm(centroids - center, axis=1)
        estimated_diameter = 2.0 * radii.max()

        visited.update(hole_group)

        if estimated_diameter <= max_diameter:
            hole_faces.extend(hole_group)

    return {"fixed_faces": hole_faces, "force_groups": []}


def cantilever(
    verts: np.ndarray,
    indices: Optional[np.ndarray],
    fixed_face_idx: int,
    force_magnitude: float = 10.0,
    adjacency: Optional[dict] = None,
) -> Dict:
    """Set up a cantilever: fix one end, load the opposite end.

    Args:
        verts: Vertex positions (N, 3).
        indices: Triangle indices (M, 3) or None.
        fixed_face_idx: Face index of the fixed end (user clicks this).
        force_magnitude: Force in Newtons.
        adjacency: Pre-built face adjacency graph (optional).

    Returns:
        Dict with ``fixed_faces`` and ``force_groups``.
    """
    # Fixed end: expand to coplanar surface
    if _ANALYZER_AVAILABLE and adjacency is not None:
        fixed_faces = find_coplanar_group(verts, indices, fixed_face_idx, adjacency)
    else:
        fixed_faces = [fixed_face_idx]

    # Get fixed face normal and centroid
    fixed_normal = _face_normal(verts, indices, fixed_face_idx)
    fixed_centroid = _face_centroid(verts, indices, fixed_face_idx)

    # Find the face most opposite (furthest along the fixed face's normal)
    n_faces = len(indices) if indices is not None else len(verts) // 3
    best_dist = -float("inf")
    best_face = -1
    for fi in range(n_faces):
        if fi in fixed_faces:
            continue
        centroid = _face_centroid(verts, indices, fi)
        dist = np.dot(centroid - fixed_centroid, -fixed_normal)
        if dist > best_dist:
            best_dist = dist
            best_face = fi

    force_groups = []
    if best_face >= 0:
        # Expand the opposite face and apply downward force
        if _ANALYZER_AVAILABLE and adjacency is not None:
            load_faces = find_coplanar_group(verts, indices, best_face, adjacency)
        else:
            load_faces = [best_face]

        # Force direction: perpendicular to the fixed face normal (bending load)
        # Use Y-down as default bending direction, unless that's parallel to the face
        down = np.array([0.0, -1.0, 0.0])
        if abs(np.dot(down, fixed_normal)) > 0.9:
            # Fixed face is horizontal — use X as bending direction
            down = np.array([1.0, 0.0, 0.0])

        force_groups.append({
            "face_indices": load_faces,
            "force": (
                float(down[0] * force_magnitude),
                float(down[1] * force_magnitude),
                float(down[2] * force_magnitude),
            ),
        })

    return {"fixed_faces": fixed_faces, "force_groups": force_groups}
