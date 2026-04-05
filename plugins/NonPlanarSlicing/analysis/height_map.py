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

    def in_bounds(self, x: float, y: float) -> bool:
        """Return True if (x, y) is within the grid bounding box.

        The point must be within half a cell of the grid boundary to be
        considered in-bounds (consistent with nearest-neighbor rounding).
        """
        half = self.resolution * 0.5
        return (self.x_min - half <= x <= self.x_max + half and
                self.y_min - half <= y <= self.y_max + half)

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
        """Return True if (x, y) is within bounds and the nearest cell has finite Z."""
        if not self.in_bounds(x, y):
            return False
        row, col = self.get_grid_coords(x, y)
        return bool(np.isfinite(self.z_values[row, col]))

    def interpolate(self, x: float, y: float) -> float:
        """Bilinearly interpolate the Z value at world coordinates *(x, y)*.

        Falls back to nearest-neighbor lookup when the point is near
        the grid boundary or when a bilinear neighbour is NaN.  Returns
        ``NaN`` if the point is outside the grid bounds or the nearest
        cell has no data.
        """
        if not self.in_bounds(x, y):
            return float("nan")

        rows, cols = self.grid_shape

        # Continuous grid coordinates.
        cx = (x - self.x_min) / self.resolution
        cy = (y - self.y_min) / self.resolution

        # Integer corners for bilinear interpolation.
        c0 = int(np.floor(cx))
        r0 = int(np.floor(cy))
        c1 = c0 + 1
        r1 = r0 + 1

        # Try bilinear interpolation first (interior points).
        if 0 <= c0 and 0 <= r0 and c1 < cols and r1 < rows:
            fx = cx - c0
            fy = cy - r0

            z00 = self.z_values[r0, c0]
            z01 = self.z_values[r0, c1]
            z10 = self.z_values[r1, c0]
            z11 = self.z_values[r1, c1]

            if (np.isfinite(z00) and np.isfinite(z01) and
                    np.isfinite(z10) and np.isfinite(z11)):
                z = (
                    z00 * (1 - fx) * (1 - fy)
                    + z01 * fx * (1 - fy)
                    + z10 * (1 - fx) * fy
                    + z11 * fx * fy
                )
                return float(z)

        # Fall back to nearest-neighbor (handles boundary cells and
        # partial NaN neighbours).  This keeps interpolate() consistent
        # with is_valid() which also uses nearest-neighbor via
        # get_grid_coords().
        row, col = self.get_grid_coords(x, y)
        val = self.z_values[row, col]
        if np.isfinite(val):
            return float(val)
        return float("nan")


# ======================================================================
# Public API
# ======================================================================


def generate_height_map(
    vertices: NDArray[np.floating],
    indices: NDArray[np.integer] | None,
    candidate_mask: NDArray[np.bool_],
    *,
    resolution: float = 0.5,
    surface_mask: NDArray[np.bool_] | None = None,
) -> HeightMap:
    """Generate a height map by raycasting onto the mesh.

    Uses a vectorized triangle-rasterization approach: for each triangle,
    compute the Z value at all grid points within its 2D bounding box
    that lie inside the triangle (barycentric test).  This is orders of
    magnitude faster than per-cell raycasting for large grids.

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
    surface_mask:
        Optional (M,) boolean mask for ALL upward-facing surfaces
        (including non-candidates).  When provided, the grid bounding
        box and ``z_values`` cover all surface faces, enabling proper
        collision detection against non-candidate obstacles.  If
        ``None``, falls back to using ``candidate_mask`` only (the
        old behaviour).

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

    # Determine which faces contribute to the z_values grid (for
    # collision detection).  If a broader surface_mask is provided,
    # use it; otherwise fall back to candidate_mask.
    if surface_mask is not None:
        surface_mask = np.asarray(surface_mask, dtype=np.bool_)
        if surface_mask.shape[0] != num_faces:
            logger.warning(
                "surface_mask length (%d) != num_faces (%d); ignoring",
                surface_mask.shape[0], num_faces,
            )
            surface_mask = candidate_mask
    else:
        surface_mask = candidate_mask

    # ---- bounding box ----
    # Use the UNION of surface_mask and candidate_mask for the bounding
    # box so both collision surfaces and candidate surfaces are covered.
    bbox_mask = surface_mask | candidate_mask
    bbox_ids = np.nonzero(bbox_mask)[0]
    cand_ids = np.nonzero(candidate_mask)[0]

    if cand_ids.size == 0:
        logger.warning("No candidate faces; returning empty height map")
        return HeightMap(
            x_min=0.0, x_max=0.0, y_min=0.0, y_max=0.0,
            resolution=resolution,
            z_values=np.full((1, 1), np.nan),
            candidate_z_values=np.full((1, 1), np.nan),
        )

    bbox_verts = tri_verts[bbox_ids].reshape(-1, 3)
    bbox_min = bbox_verts.min(axis=0) - _BBOX_PAD
    bbox_max = bbox_verts.max(axis=0) + _BBOX_PAD

    x_min, y_min = float(bbox_min[0]), float(bbox_min[1])
    x_max, y_max = float(bbox_max[0]), float(bbox_max[1])

    cols = max(1, int(np.ceil((x_max - x_min) / resolution)) + 1)
    rows = max(1, int(np.ceil((y_max - y_min) / resolution)) + 1)

    # Cap grid size to prevent memory exhaustion when surface_mask
    # includes faces far from candidates (e.g. tall vertical walls).
    _MAX_GRID_DIM = 2000
    if rows > _MAX_GRID_DIM or cols > _MAX_GRID_DIM:
        scale = max(rows, cols) / _MAX_GRID_DIM
        resolution = resolution * scale
        cols = max(1, int(np.ceil((x_max - x_min) / resolution)) + 1)
        rows = max(1, int(np.ceil((y_max - y_min) / resolution)) + 1)
        logger.warning(
            "Height-map grid exceeded %d cells; coarsened resolution to %.3f mm "
            "(%d x %d cells)",
            _MAX_GRID_DIM, resolution, rows, cols,
        )

    logger.debug(
        "Height-map grid: %d x %d (%.1f x %.1f mm, res=%.2f mm), %d faces",
        rows, cols, x_max - x_min, y_max - y_min, resolution, num_faces,
    )

    # ---- vectorized triangle rasterization ----
    z_values = np.full((rows, cols), np.nan, dtype=np.float64)
    candidate_z = np.full((rows, cols), np.nan, dtype=np.float64)

    v0 = tri_verts[:, 0, :]  # (M, 3)
    v1 = tri_verts[:, 1, :]
    v2 = tri_verts[:, 2, :]

    # Process triangles in batches to balance vectorization vs memory.
    _BATCH_SIZE = 256
    for batch_start in range(0, num_faces, _BATCH_SIZE):
        batch_end = min(batch_start + _BATCH_SIZE, num_faces)
        _rasterize_triangle_batch(
            v0[batch_start:batch_end],
            v1[batch_start:batch_end],
            v2[batch_start:batch_end],
            candidate_mask[batch_start:batch_end],
            x_min, y_min, resolution, rows, cols,
            z_values, candidate_z,
        )

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
# Vectorized triangle rasterization
# ======================================================================


def _rasterize_triangle_batch(
    v0: NDArray[np.floating],
    v1: NDArray[np.floating],
    v2: NDArray[np.floating],
    is_candidate: NDArray[np.bool_],
    x_min: float,
    y_min: float,
    resolution: float,
    rows: int,
    cols: int,
    z_values: NDArray[np.floating],
    candidate_z: NDArray[np.floating],
) -> None:
    """Rasterize a batch of triangles onto the height-map grids.

    For each triangle, finds all grid points within its 2D bounding box,
    tests them for containment using barycentric coordinates, computes
    the Z at each interior point via the triangle's plane equation, and
    updates z_values/candidate_z with the maximum Z.

    Modifies z_values and candidate_z in place.
    """
    batch_size = v0.shape[0]

    for i in range(batch_size):
        ax, ay, az = v0[i, 0], v0[i, 1], v0[i, 2]
        bx, by, bz = v1[i, 0], v1[i, 1], v1[i, 2]
        cx, cy, cz = v2[i, 0], v2[i, 1], v2[i, 2]

        # 2D bounding box of this triangle in grid coordinates.
        tri_x_min = min(ax, bx, cx)
        tri_x_max = max(ax, bx, cx)
        tri_y_min = min(ay, by, cy)
        tri_y_max = max(ay, by, cy)

        col_lo = max(0, int(np.floor((tri_x_min - x_min) / resolution)))
        col_hi = min(cols - 1, int(np.ceil((tri_x_max - x_min) / resolution)))
        row_lo = max(0, int(np.floor((tri_y_min - y_min) / resolution)))
        row_hi = min(rows - 1, int(np.ceil((tri_y_max - y_min) / resolution)))

        if col_lo > col_hi or row_lo > row_hi:
            continue

        # Generate grid coordinates for all points in the bounding box.
        grid_cols = np.arange(col_lo, col_hi + 1, dtype=np.float64)
        grid_rows = np.arange(row_lo, row_hi + 1, dtype=np.float64)
        gx = x_min + grid_cols * resolution  # (nc,)
        gy = y_min + grid_rows * resolution  # (nr,)

        # Meshgrid: px[r, c], py[r, c]
        px, py = np.meshgrid(gx, gy)  # both (nr, nc)

        # Barycentric coordinates for point-in-triangle test.
        # Using the cross-product method:
        #   v0 = C - A, v1 = B - A, v2 = P - A
        #   dot00 = v0 . v0, dot01 = v0 . v1, dot02 = v0 . v2
        #   dot11 = v1 . v1, dot12 = v1 . v2
        #   u = (dot11*dot02 - dot01*dot12) / denom
        #   v = (dot00*dot12 - dot01*dot02) / denom
        #   inside if u >= 0, v >= 0, u + v <= 1
        e0x = cx - ax
        e0y = cy - ay
        e1x = bx - ax
        e1y = by - ay

        dot00 = e0x * e0x + e0y * e0y
        dot01 = e0x * e1x + e0y * e1y
        dot11 = e1x * e1x + e1y * e1y
        denom = dot00 * dot11 - dot01 * dot01

        if abs(denom) < _EPS:
            continue  # Degenerate triangle

        inv_denom = 1.0 / denom

        # Vector from A to each grid point.
        dpx = px - ax  # (nr, nc)
        dpy = py - ay

        dot02 = e0x * dpx + e0y * dpy
        dot12 = e1x * dpx + e1y * dpy

        u = (dot11 * dot02 - dot01 * dot12) * inv_denom
        v = (dot00 * dot12 - dot01 * dot02) * inv_denom

        inside = (u >= -_EPS) & (v >= -_EPS) & (u + v <= 1.0 + _EPS)

        if not np.any(inside):
            continue

        # Compute Z at each interior point using the plane of the triangle.
        # P = A + u*(C-A) + v*(B-A)  =>  z = az + u*(cz-az) + v*(bz-az)
        z_hit = az + u * (cz - az) + v * (bz - az)

        # Update z_values: keep the maximum Z at each cell.
        # Use vectorized np.maximum with NaN handling.
        hit_rows_local, hit_cols_local = np.nonzero(inside)
        if len(hit_rows_local) == 0:
            continue

        abs_rows = hit_rows_local + row_lo
        abs_cols = hit_cols_local + col_lo
        z_vals = z_hit[hit_rows_local, hit_cols_local]

        # Update z_values grid — np.fmax treats NaN as missing.
        current_vals = z_values[abs_rows, abs_cols]
        z_values[abs_rows, abs_cols] = np.fmax(current_vals, z_vals)

        if is_candidate[i]:
            current_cvals = candidate_z[abs_rows, abs_cols]
            candidate_z[abs_rows, abs_cols] = np.fmax(current_cvals, z_vals)
