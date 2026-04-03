"""Surface analysis module for non-planar slicing.

Classifies mesh faces by surface normal direction to identify top surfaces
suitable for non-planar printing. Works in Z-up coordinate space.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

# Faces with normals within this angle (in degrees) of the Z-up axis are
# considered "top surfaces".  80 degrees means even fairly steep faces count.
_DEFAULT_TOP_SURFACE_MAX_ANGLE_DEG: float = 80.0


@dataclass(frozen=True)
class SurfaceAnalysis:
    """Result of mesh surface analysis.

    All arrays are indexed by face (triangle) index, so element *i*
    corresponds to the *i*-th triangle in the mesh.

    Attributes:
        face_normals: (M, 3) unit-length face normals.  Degenerate
            triangles receive a zero-vector normal.
        face_centers: (M, 3) triangle centroids.
        face_areas: (M,) triangle areas in mm^2.
        angles_from_horizontal: (M,) angle in radians between each
            face normal and the Z-up axis.  A perfectly horizontal
            upward-facing face has angle 0; a vertical face has angle
            pi/2.
        is_top_surface: (M,) boolean mask -- True when the face normal
            has a positive Z component and the angle from the Z axis is
            less than the configured threshold (default 80 deg).
    """

    face_normals: NDArray[np.floating]
    face_centers: NDArray[np.floating]
    face_areas: NDArray[np.floating]
    angles_from_horizontal: NDArray[np.floating]
    is_top_surface: NDArray[np.bool_]


def analyze_mesh(
    vertices: NDArray[np.floating],
    indices: NDArray[np.integer] | None,
    transform_matrix: NDArray[np.floating] | None = None,
    *,
    top_surface_max_angle_deg: float = _DEFAULT_TOP_SURFACE_MAX_ANGLE_DEG,
) -> SurfaceAnalysis:
    """Analyse a triangle mesh and classify faces by normal direction.

    Parameters
    ----------
    vertices:
        (N, 3) array of vertex positions (float32 or float64).
    indices:
        (M, 3) array of triangle vertex indices.  May be ``None`` for a
        non-indexed mesh, in which case *vertices* must contain
        ``3 * M`` rows interpreted as consecutive triangle corners.
    transform_matrix:
        Optional 4x4 homogeneous transformation applied to all vertices
        (and the corresponding inverse-transpose to normals) before
        analysis.
    top_surface_max_angle_deg:
        Maximum angle (in degrees) from the Z-up direction for a face
        to be considered a top surface.

    Returns
    -------
    SurfaceAnalysis
        Dataclass with per-face normals, centres, areas and
        classification masks.

    Raises
    ------
    ValueError
        If *vertices* has the wrong shape, or a non-indexed mesh has a
        vertex count that is not a multiple of 3.
    """

    vertices = np.asarray(vertices, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 3:
        raise ValueError(
            f"vertices must be (N, 3); got shape {vertices.shape}"
        )

    # ---- resolve triangle corners ----
    if indices is not None:
        indices = np.asarray(indices, dtype=np.intp)
        if indices.ndim != 2 or indices.shape[1] != 3:
            raise ValueError(
                f"indices must be (M, 3); got shape {indices.shape}"
            )
        tri_verts = vertices[indices]  # (M, 3, 3)
    else:
        if vertices.shape[0] % 3 != 0:
            raise ValueError(
                "Non-indexed mesh must have a vertex count divisible by 3; "
                f"got {vertices.shape[0]} vertices"
            )
        tri_verts = vertices.reshape(-1, 3, 3)

    num_faces = tri_verts.shape[0]
    logger.debug("Analysing mesh with %d faces", num_faces)

    # ---- apply transform ----
    if transform_matrix is not None:
        transform_matrix = np.asarray(transform_matrix, dtype=np.float64)
        if transform_matrix.shape != (4, 4):
            raise ValueError(
                f"transform_matrix must be (4, 4); got {transform_matrix.shape}"
            )
        tri_verts = _apply_transform(tri_verts, transform_matrix)

    # ---- edge vectors & cross product ----
    v0 = tri_verts[:, 0, :]  # (M, 3)
    v1 = tri_verts[:, 1, :]
    v2 = tri_verts[:, 2, :]

    edge1 = v1 - v0  # (M, 3)
    edge2 = v2 - v0

    cross = np.cross(edge1, edge2)  # (M, 3)

    # ---- areas (half the cross-product magnitude) ----
    cross_mag = np.linalg.norm(cross, axis=1)  # (M,)
    face_areas = cross_mag * 0.5

    # ---- normals (normalised cross product) ----
    # Guard against degenerate (zero-area) triangles.
    degenerate = cross_mag < 1e-12
    safe_mag = np.where(degenerate, 1.0, cross_mag)[:, np.newaxis]
    face_normals = cross / safe_mag
    face_normals[degenerate] = 0.0

    if transform_matrix is not None:
        # Normals need the inverse-transpose of the upper-left 3x3 to
        # remain correct after non-uniform scaling.
        normal_matrix = np.linalg.inv(transform_matrix[:3, :3]).T
        face_normals = (face_normals @ normal_matrix.T)
        nm = np.linalg.norm(face_normals, axis=1, keepdims=True)
        nm_safe = np.where(nm < 1e-12, 1.0, nm)
        face_normals = face_normals / nm_safe
        face_normals[nm.squeeze() < 1e-12] = 0.0

    # ---- centroids ----
    face_centers = (v0 + v1 + v2) / 3.0

    # ---- angle from Z-up ----
    z_component = np.clip(face_normals[:, 2], -1.0, 1.0)
    angles_from_z = np.arccos(z_component)  # 0 = pointing up, pi = down

    # ---- top surface classification ----
    threshold_rad = np.radians(top_surface_max_angle_deg)
    is_top = (z_component > 0.0) & (angles_from_z < threshold_rad) & ~degenerate

    n_top = int(np.count_nonzero(is_top))
    logger.debug(
        "Surface analysis complete: %d / %d faces classified as top surface",
        n_top,
        num_faces,
    )

    return SurfaceAnalysis(
        face_normals=face_normals.astype(np.float64),
        face_centers=face_centers.astype(np.float64),
        face_areas=face_areas.astype(np.float64),
        angles_from_horizontal=angles_from_z.astype(np.float64),
        is_top_surface=is_top,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_transform(
    tri_verts: NDArray[np.floating],
    matrix: NDArray[np.floating],
) -> NDArray[np.floating]:
    """Apply a 4x4 homogeneous transform to triangle vertices in-place.

    Parameters
    ----------
    tri_verts:
        (M, 3, 3) array of triangle vertices.
    matrix:
        (4, 4) homogeneous transformation matrix.

    Returns
    -------
    Transformed (M, 3, 3) vertices (new array).
    """
    M = tri_verts.shape[0]
    # Flatten to (M*3, 3), add homogeneous coordinate, transform, drop w.
    flat = tri_verts.reshape(-1, 3)  # (M*3, 3)
    ones = np.ones((flat.shape[0], 1), dtype=flat.dtype)
    homo = np.hstack([flat, ones])  # (M*3, 4)
    transformed = homo @ matrix.T  # (M*3, 4)
    return transformed[:, :3].reshape(M, 3, 3)
