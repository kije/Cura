"""Height-map generation via raycasting for non-planar slicing.

Projects rays downward (-Z) onto the mesh surface to build a regular 2D
grid of Z-height values.  The resulting height map is used for toolpath
generation and collision checking.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Tuple

import numpy as np
from numpy.typing import NDArray

from .surface_analyzer import SurfaceAnalysis

logger = logging.getLogger(__name__)

# Small epsilon used for floating-point comparisons in ray-triangle tests.
_EPS: float = 1e-8

# Padding added to the bounding box of candidate faces (mm).
_BBOX_PAD: float = 1.0

# Number of spatial-index grid buckets along each axis (default).
_SPATIAL_GRID_RESOLUTION: int = 64


@dataclass
class HeightMap:
    """Regular 2D grid of Z-height values obtained by raycasting.

    Attributes:
        x_min, x_max, y_min, y_max: Bounds of the grid in world units (mm).
        resolution: Size of each grid cell in mm.
        z_values: 2D array (rows, cols) of the highest Z intersection
            found at each grid point.  ``NaN`` where no intersection.
        candidate_z_values: Same shape as *z_values* but ``NaN``
            outside candidate regions.
        grid_shape: ``(rows, cols)`` convenience property.
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    resolution: float
    z_values: NDArray[np.floating]
    candidate_z_values: NDArray[np.floating]

    @property
    def grid_shape(self) -> Tuple[int, int]:
        """Return ``(rows, cols)``."""
        return (self.z_values.shape[0], self.z_values.shape[1])

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_grid_coords(self, x: float, y: float) -> Tuple[int, int]:
        """Convert world (x, y) to integer grid ``(row, col)``.

        The returned indices are clipped to the valid grid range.
        """
        col = int(np.clip(
            np.round((x - self.x_min) / self.resolution), 0, self.z_values.shape[1] - 1
        ))
        row = int(np.clip(
            np.round((y - self.y_min) / self.resolution), 0, self.z_values.shape[0] - 1
        ))
        return (row, col)

    def is_valid(self, x: float, y: float) -> bool:
        """Return True if the grid cell nearest to *(x, y)* has a finite Z."""
        row, col = self.get_grid_coords(x, y)
        return bool(np.isfinite(self.z_values[row, col]))

    def interpolate(self, x: float, y: float) -> float:
        """Bilinearly interpolate the Z value at world coordinates *(x, y)*.

        Returns ``NaN`` if any of the four surrounding grid cells is
        ``NaN`` or if the point is outside the grid bounds.
        """
        # Continuous grid coordinates.
        cx = (x - self.x_min) / self.resolution
        cy = (y - self.y_min) / self.resolution

        rows, cols = self.grid_shape

        # Integer corners.
        c0 = int(np.floor(cx))
        r0 = int(np.floor(cy))
        c1 = c0 + 1
        r1 = r0 + 1

        if c0 < 0 or r0 < 0 or c1 >= cols or r1 >= rows:
            return float("nan")

        # Fractional parts.
        fx = cx - c0
        fy = cy - r0

        z00 = self.z_values[r0, c0]
        z01 = self.z_values[r0, c1]
        z10 = self.z_values[r1, c0]
        z11 = self.z_values[r1, c1]

        if not (np.isfinite(z00) and np.isfinite(z01) and
                np.isfinite(z10) and np.isfinite(z11)):
            return float("nan")

        z = (
            z00 * (1 - fx) * (1 - fy)
            + z01 * fx * (1 - fy)
            + z10 * (1 - fx) * fy
            + z11 * fx * fy
        )
        return float(z)


# ======================================================================
# Public API
# ======================================================================


def generate_height_map(
    vertices: NDArray[np.floating],
    indices: NDArray[np.integer] | None,
    candidate_mask: NDArray[np.bool_],
    *,
    resolution: float = 0.5,
) -> HeightMap:
    """Generate a height map by raycasting onto the mesh.

    Parameters
    ----------
    vertices:
        (N, 3) vertex positions.
    indices:
        (M, 3) triangle indices, or ``None`` for non-indexed meshes.
    candidate_mask:
        (M,) boolean mask indicating candidate faces.
    resolution:
        Grid cell size in mm.

    Returns
    -------
    HeightMap
    """

    vertices = np.asarray(vertices, dtype=np.float64)
    if indices is not None:
        indices = np.asarray(indices, dtype=np.intp)
        tri_verts = vertices[indices]  # (M, 3, 3)
    else:
        tri_verts = vertices.reshape(-1, 3, 3)

    num_faces = tri_verts.shape[0]
    candidate_mask = np.asarray(candidate_mask, dtype=np.bool_)
    if candidate_mask.shape[0] != num_faces:
        raise ValueError(
            f"candidate_mask length ({candidate_mask.shape[0]}) != "
            f"number of faces ({num_faces})"
        )

    # ---- bounding box of candidate faces ----
    cand_ids = np.nonzero(candidate_mask)[0]
    if cand_ids.size == 0:
        logger.warning("No candidate faces; returning empty height map")
        return HeightMap(
            x_min=0.0, x_max=0.0, y_min=0.0, y_max=0.0,
            resolution=resolution,
            z_values=np.full((1, 1), np.nan),
            candidate_z_values=np.full((1, 1), np.nan),
        )

    cand_verts = tri_verts[cand_ids]  # (K, 3, 3)
    cand_flat = cand_verts.reshape(-1, 3)  # (K*3, 3)
    bbox_min = cand_flat.min(axis=0) - _BBOX_PAD
    bbox_max = cand_flat.max(axis=0) + _BBOX_PAD

    x_min, y_min = float(bbox_min[0]), float(bbox_min[1])
    x_max, y_max = float(bbox_max[0]), float(bbox_max[1])

    cols = max(1, int(np.ceil((x_max - x_min) / resolution)) + 1)
    rows = max(1, int(np.ceil((y_max - y_min) / resolution)) + 1)

    logger.debug(
        "Height-map grid: %d x %d (%.1f x %.1f mm, res=%.2f mm)",
        rows, cols, x_max - x_min, y_max - y_min, resolution,
    )

    # ---- build spatial index ----
    spatial = _build_spatial_index(tri_verts, x_min, x_max, y_min, y_max)

    # ---- raycast ----
    v0_all = tri_verts[:, 0, :]
    v1_all = tri_verts[:, 1, :]
    v2_all = tri_verts[:, 2, :]

    z_values = np.full((rows, cols), np.nan, dtype=np.float64)
    candidate_z = np.full((rows, cols), np.nan, dtype=np.float64)

    # Process in row batches for memory efficiency.
    for row in range(rows):
        oy = y_min + row * resolution
        ox_arr = x_min + np.arange(cols, dtype=np.float64) * resolution

        for col in range(cols):
            ox = ox_arr[col]
            face_ids = _query_spatial_index(spatial, ox, oy, x_min, x_max, y_min, y_max)
            if face_ids is None or len(face_ids) == 0:
                continue

            fids = np.array(face_ids, dtype=np.intp)
            t_vals, hit_mask = _ray_triangle_batch(
                ox, oy, v0_all[fids], v1_all[fids], v2_all[fids]
            )
            if not np.any(hit_mask):
                continue

            z_hits = t_vals[hit_mask]
            best_z = float(np.max(z_hits))
            z_values[row, col] = best_z

            # Candidate-only z: highest Z among hits on candidate faces.
            cand_hit = hit_mask & candidate_mask[fids]
            if np.any(cand_hit):
                candidate_z[row, col] = float(np.max(t_vals[cand_hit]))

    valid_count = int(np.count_nonzero(np.isfinite(z_values)))
    logger.debug(
        "Height-map complete: %d / %d cells have valid Z values",
        valid_count, rows * cols,
    )

    return HeightMap(
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        resolution=resolution,
        z_values=z_values,
        candidate_z_values=candidate_z,
    )


# ======================================================================
# Ray-triangle intersection (vectorised Moller-Trumbore)
# ======================================================================


def _ray_triangle_batch(
    ox: float,
    oy: float,
    v0: NDArray[np.floating],
    v1: NDArray[np.floating],
    v2: NDArray[np.floating],
) -> Tuple[NDArray[np.floating], NDArray[np.bool_]]:
    """Intersect a vertical ray at (ox, oy) going in -Z with triangles.

    The ray origin is at (ox, oy, +inf) and points in the -Z direction.
    We are interested in the Z coordinate of the intersection.

    Parameters
    ----------
    ox, oy:
        XY position of the ray.
    v0, v1, v2:
        (K, 3) triangle corner arrays.

    Returns
    -------
    z_values:
        (K,) Z coordinate of intersection (meaningful only where *hit*
        is True).
    hit:
        (K,) boolean mask of successful intersections.
    """
    K = v0.shape[0]
    z_out = np.full(K, np.nan, dtype=np.float64)
    hit = np.zeros(K, dtype=np.bool_)

    if K == 0:
        return z_out, hit

    # Ray direction: (0, 0, -1).
    edge1 = v1 - v0  # (K, 3)
    edge2 = v2 - v0

    # h = cross(dir, edge2) with dir = (0, 0, -1)
    # cross((0,0,-1), (e2x, e2y, e2z)) = (0*e2z - (-1)*e2y, (-1)*e2x - 0*e2z, 0*e2y - 0*e2x)
    #                                    = (e2y, -e2x, 0)
    hx = edge2[:, 1]
    hy = -edge2[:, 0]
    # hz = 0

    # a = dot(edge1, h)
    a = edge1[:, 0] * hx + edge1[:, 1] * hy  # (K,)

    # Parallel test.
    valid = np.abs(a) > _EPS
    inv_a = np.where(valid, 1.0 / np.where(valid, a, 1.0), 0.0)

    # s = ray_origin - v0   (ray origin at (ox, oy, big_z) but z cancels)
    sx = ox - v0[:, 0]
    sy = oy - v0[:, 1]
    sz_comp = -v0[:, 2]  # We can pick any z for origin; terms cancel for u,v
    # Actually we need proper formulation. Let's use origin_z = 0 and then
    # the hit z = v0z + u*edge1z + v*edge2z.  But the standard M-T gives t
    # along the ray.  Let's just do it properly.

    # s = O - v0 where O = (ox, oy, Zo) for some large Zo.
    # But since direction is (0,0,-1), the parameter t gives the point
    # P = O + t*d = (ox, oy, Zo - t).  So z_hit = Zo - t.
    # We pick Zo = 0 for simplicity -> z_hit = -t.  But then
    # s = (ox - v0x, oy - v0y, 0 - v0z) = (sx, sy, -v0z).

    sz = -v0[:, 2]

    # u = inv_a * dot(s, h)
    u = inv_a * (sx * hx + sy * hy)

    valid &= (u >= 0.0) & (u <= 1.0)

    # q = cross(s, edge1)
    qx = sy * edge1[:, 2] - sz * edge1[:, 1]
    qy = sz * edge1[:, 0] - sx * edge1[:, 2]
    qz = sx * edge1[:, 1] - sy * edge1[:, 0]

    # v = inv_a * dot(dir, q) = inv_a * (0*qx + 0*qy + (-1)*qz) = -inv_a * qz
    v = -inv_a * qz

    valid &= (v >= 0.0) & (u + v <= 1.0)

    # t = inv_a * dot(edge2, q)
    t = inv_a * (edge2[:, 0] * qx + edge2[:, 1] * qy + edge2[:, 2] * qz)

    # We want t > 0 (intersection in the -Z direction from origin at z=0,
    # meaning the surface is *below* z=0... but we actually want any hit).
    # z_hit = -t.  Since the ray goes downward, positive t means the
    # triangle is below origin.  We accept any finite t (the mesh can be
    # anywhere).
    valid &= np.isfinite(t)

    z_out[valid] = -t[valid]  # z_hit = 0 - t = -t (origin z = 0)
    hit[valid] = True

    return z_out, hit


# ======================================================================
# Simple grid-based spatial index
# ======================================================================


def _build_spatial_index(
    tri_verts: NDArray[np.floating],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    grid_res: int = _SPATIAL_GRID_RESOLUTION,
) -> dict:
    """Build a simple 2D grid spatial index for triangles.

    Each cell stores a list of face indices whose XY bounding box
    overlaps that cell.
    """
    num_faces = tri_verts.shape[0]
    x_range = max(x_max - x_min, 1e-6)
    y_range = max(y_max - y_min, 1e-6)

    cell_w = x_range / grid_res
    cell_h = y_range / grid_res

    # Per-triangle XY bounding boxes.
    tri_xy_min = tri_verts[:, :, :2].min(axis=1)  # (M, 2)
    tri_xy_max = tri_verts[:, :, :2].max(axis=1)

    col_min = np.clip(((tri_xy_min[:, 0] - x_min) / cell_w).astype(np.intp), 0, grid_res - 1)
    col_max = np.clip(((tri_xy_max[:, 0] - x_min) / cell_w).astype(np.intp), 0, grid_res - 1)
    row_min = np.clip(((tri_xy_min[:, 1] - y_min) / cell_h).astype(np.intp), 0, grid_res - 1)
    row_max = np.clip(((tri_xy_max[:, 1] - y_min) / cell_h).astype(np.intp), 0, grid_res - 1)

    grid: dict[Tuple[int, int], list[int]] = {}
    for face_id in range(num_faces):
        r0, r1 = int(row_min[face_id]), int(row_max[face_id])
        c0, c1 = int(col_min[face_id]), int(col_max[face_id])
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                key = (r, c)
                bucket = grid.get(key)
                if bucket is None:
                    grid[key] = [face_id]
                else:
                    bucket.append(face_id)

    return {
        "grid": grid,
        "grid_res": grid_res,
        "x_min": x_min,
        "y_min": y_min,
        "cell_w": cell_w,
        "cell_h": cell_h,
    }


def _query_spatial_index(
    spatial: dict,
    x: float,
    y: float,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> list[int] | None:
    """Return face indices whose bounding box overlaps point (x, y)."""
    grid = spatial["grid"]
    grid_res = spatial["grid_res"]
    cell_w = spatial["cell_w"]
    cell_h = spatial["cell_h"]

    col = int(np.clip((x - spatial["x_min"]) / cell_w, 0, grid_res - 1))
    row = int(np.clip((y - spatial["y_min"]) / cell_h, 0, grid_res - 1))

    return grid.get((row, col))
