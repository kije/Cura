"""Printhead collision checking for non-planar slicing.

Verifies that the printhead (including fan shroud and nozzle cone) has
sufficient clearance at every height-map grid point.  Points where the
printhead would collide with already-printed material are flagged as
unsafe for non-planar toolpaths.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np
from numpy.typing import NDArray

from .height_map import HeightMap

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollisionResult:
    """Result of printhead collision analysis.

    Arrays have the same shape as the source :class:`HeightMap` grid.

    Attributes:
        safe_map: True where non-planar printing is safe (no collision
            and valid Z data).
        collision_map: True where a collision was detected.
        safe_count: Number of safe cells.
        collision_count: Number of collision cells.
    """

    safe_map: NDArray[np.bool_]
    collision_map: NDArray[np.bool_]
    safe_count: int
    collision_count: int


def check_collisions(
    height_map: HeightMap,
    printhead_polygon: Sequence[Sequence[float]],
    nozzle_clearance_mm: float,
    nozzle_expansion_angle_deg: float = 45.0,
    safety_margin_mm: float = 1.0,
) -> CollisionResult:
    """Check printhead clearance over the height map.

    Parameters
    ----------
    height_map:
        A :class:`HeightMap` produced by :func:`height_map.generate_height_map`.
    printhead_polygon:
        List of ``[x, y]`` pairs defining the 2D printhead footprint
        (e.g. from ``machine_head_with_fans_polygon``).  Coordinates are
        in mm relative to the nozzle tip.
    nozzle_clearance_mm:
        Vertical distance from the nozzle tip to the first mechanical
        obstruction above it (mm).
    nozzle_expansion_angle_deg:
        Half-angle of the nozzle cone in degrees measured from the
        vertical axis.  At horizontal distance *d* from the nozzle
        centre, printed material must be below
        ``nozzle_tip_z + d / tan(angle)``.
    safety_margin_mm:
        Conservative margin subtracted from *nozzle_clearance_mm*.

    Returns
    -------
    CollisionResult
    """

    z = height_map.z_values
    rows, cols = z.shape
    res = height_map.resolution

    logger.debug(
        "Collision check: grid %d x %d, clearance=%.1f mm, "
        "safety=%.1f mm, cone=%.1f deg",
        rows, cols, nozzle_clearance_mm, safety_margin_mm,
        nozzle_expansion_angle_deg,
    )

    # Use at least 50% of the declared clearance (so a 1mm clearance with
    # 1mm margin still yields 0.5mm rather than zero).
    effective_clearance = max(nozzle_clearance_mm - safety_margin_mm,
                             nozzle_clearance_mm * 0.5)

    # ------------------------------------------------------------------
    # 1.  Rasterise the printhead polygon into a binary footprint on the
    #     height-map grid.
    # ------------------------------------------------------------------
    footprint = _rasterise_polygon(printhead_polygon, res)

    logger.debug(
        "Printhead footprint rasterised to %d x %d cells (%d active)",
        footprint.shape[0], footprint.shape[1],
        int(np.count_nonzero(footprint)),
    )

    # ------------------------------------------------------------------
    # 2.  Shroud collision: for each cell, the shroud bottom is at
    #     z[i,j] + effective_clearance.  Any neighbouring cell (within
    #     the printhead footprint) whose z value exceeds that shroud
    #     height means a collision.
    #
    #     Efficiently: compute max z in the printhead footprint
    #     neighbourhood using scipy's maximum_filter.
    # ------------------------------------------------------------------
    try:
        from scipy.ndimage import maximum_filter  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "scipy not available; falling back to brute-force collision check"
        )
        maximum_filter = None

    # Replace NaN with -inf so max-filter ignores empty cells.
    z_filled = np.where(np.isfinite(z), z, -np.inf)

    if maximum_filter is not None:
        z_max_neighbourhood = maximum_filter(z_filled, footprint=footprint)
    else:
        z_max_neighbourhood = _manual_max_filter(z_filled, footprint)

    # A cell is collision-free (shroud check) when the tallest
    # neighbouring surface is below the shroud bottom.
    shroud_bottom = np.where(np.isfinite(z), z + effective_clearance, np.nan)
    shroud_ok = z_max_neighbourhood <= shroud_bottom

    # ------------------------------------------------------------------
    # 3.  Nozzle-cone collision: at each grid cell we additionally check
    #     a distance-dependent height limit.  For a cell offset (dr, dc)
    #     from the nozzle centre, the horizontal distance is
    #     d = sqrt(dr^2 + dc^2) * resolution.  Material there must be
    #     below  nozzle_tip_z + d / tan(cone_angle).
    # ------------------------------------------------------------------
    cone_ok = _check_nozzle_cone(
        z, z_filled, res, nozzle_expansion_angle_deg, footprint
    )

    # ------------------------------------------------------------------
    # 4.  Combine results.
    # ------------------------------------------------------------------
    has_z = np.isfinite(z)

    collision = has_z & (~shroud_ok | ~cone_ok)
    safe = has_z & ~collision

    safe_count = int(np.count_nonzero(safe))
    collision_count = int(np.count_nonzero(collision))

    logger.debug(
        "Collision check complete: %d safe, %d collision, %d invalid (NaN)",
        safe_count, collision_count, rows * cols - safe_count - collision_count,
    )

    return CollisionResult(
        safe_map=safe,
        collision_map=collision,
        safe_count=safe_count,
        collision_count=collision_count,
    )


# ======================================================================
# Internal helpers
# ======================================================================


def _rasterise_polygon(
    polygon: Sequence[Sequence[float]],
    resolution: float,
) -> NDArray[np.bool_]:
    """Convert a printhead polygon (in mm, relative to nozzle) to a
    binary grid footprint suitable for use as a filter kernel.

    The polygon is rasterised using a simple scanline fill.  The
    resulting array is always odd-sized with the nozzle at the centre.
    """

    pts = np.asarray(polygon, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise ValueError(
            f"printhead_polygon must be (K, 2); got shape {pts.shape}"
        )

    # Determine footprint size in grid cells.
    x_min, y_min = pts.min(axis=0)
    x_max, y_max = pts.max(axis=0)

    # Half-extents in cells (from nozzle centre).
    hx = int(np.ceil(max(abs(x_min), abs(x_max)) / resolution))
    hy = int(np.ceil(max(abs(y_min), abs(y_max)) / resolution))

    # Ensure at least 1 cell and odd size.
    hx = max(hx, 1)
    hy = max(hy, 1)
    size_x = 2 * hx + 1
    size_y = 2 * hy + 1

    footprint = np.zeros((size_y, size_x), dtype=np.bool_)

    # Centre of the footprint in grid coords.
    cx, cy = hx, hy

    # Scale polygon to grid coords (centred on (cx, cy)).
    grid_pts = pts / resolution
    grid_pts[:, 0] += cx
    grid_pts[:, 1] += cy

    # Scanline rasterisation.
    n = len(grid_pts)
    for row in range(size_y):
        # Find all X intersections of the polygon edges with this row.
        intersections: list[float] = []
        for i in range(n):
            j = (i + 1) % n
            y0, y1 = grid_pts[i, 1], grid_pts[j, 1]
            if y0 == y1:
                continue
            if not (min(y0, y1) <= row < max(y0, y1)):
                continue
            # Linear interpolation.
            t = (row - y0) / (y1 - y0)
            x_int = grid_pts[i, 0] + t * (grid_pts[j, 0] - grid_pts[i, 0])
            intersections.append(x_int)

        intersections.sort()
        # Fill between pairs.
        for k in range(0, len(intersections) - 1, 2):
            c_start = max(0, int(np.floor(intersections[k])))
            c_end = min(size_x - 1, int(np.ceil(intersections[k + 1])))
            footprint[row, c_start : c_end + 1] = True

    # Always include the centre cell.
    footprint[cy, cx] = True

    return footprint


def _manual_max_filter(
    z: NDArray[np.floating],
    footprint: NDArray[np.bool_],
) -> NDArray[np.floating]:
    """Brute-force maximum filter (fallback when scipy is unavailable).

    Slides the footprint over every cell and records the maximum Z value
    among the cells covered by the footprint.
    """

    rows, cols = z.shape
    fy, fx = footprint.shape
    hy, hx = fy // 2, fx // 2

    result = np.full_like(z, -np.inf)

    offsets = np.argwhere(footprint) - np.array([hy, hx])  # (P, 2)

    for dr, dc in offsets:
        # Shifted view.
        r_src_start = max(0, -dr)
        r_src_end = min(rows, rows - dr)
        c_src_start = max(0, -dc)
        c_src_end = min(cols, cols - dc)

        r_dst_start = max(0, dr)
        r_dst_end = r_dst_start + (r_src_end - r_src_start)
        c_dst_start = max(0, dc)
        c_dst_end = c_dst_start + (c_src_end - c_src_start)

        np.maximum(
            result[r_dst_start:r_dst_end, c_dst_start:c_dst_end],
            z[r_src_start:r_src_end, c_src_start:c_src_end],
            out=result[r_dst_start:r_dst_end, c_dst_start:c_dst_end],
        )

    return result


def _check_nozzle_cone(
    z: NDArray[np.floating],
    z_filled: NDArray[np.floating],
    resolution: float,
    cone_angle_deg: float,
    footprint: NDArray[np.bool_],
) -> NDArray[np.bool_]:
    """Check the nozzle-cone clearance constraint.

    At horizontal distance *d* from the nozzle centre, material must
    be below ``nozzle_tip_z + d / tan(cone_angle)``.

    Returns a boolean array (same shape as *z*) where True means the
    cone constraint is satisfied.
    """

    rows, cols = z.shape
    fy, fx = footprint.shape
    hy, hx = fy // 2, fx // 2

    cone_angle_rad = np.radians(cone_angle_deg)
    if cone_angle_rad <= 0 or cone_angle_rad >= np.pi / 2:
        # Degenerate cone; skip check (treat as always safe).
        logger.debug("Nozzle cone angle out of range; skipping cone check")
        return np.ones((rows, cols), dtype=np.bool_)

    tan_cone = np.tan(cone_angle_rad)
    if tan_cone < 1e-12:
        return np.ones((rows, cols), dtype=np.bool_)

    # For each active cell in the footprint, compute its distance from
    # the centre and the resulting height allowance.
    offsets = np.argwhere(footprint)  # (P, 2) in footprint coords
    offsets_from_centre = offsets - np.array([hy, hx])  # relative to centre

    cone_ok = np.ones((rows, cols), dtype=np.bool_)

    for off_r, off_c in offsets_from_centre:
        if off_r == 0 and off_c == 0:
            continue  # The nozzle tip itself; no cone constraint.

        d = np.sqrt(off_r ** 2 + off_c ** 2) * resolution  # mm
        height_allowance = d / tan_cone  # mm above nozzle tip

        # For each cell (i, j), the nozzle tip is at z[i, j].  The
        # material at (i + off_r, j + off_c) must be below
        # z[i, j] + height_allowance.

        # Compute shifted z values.
        r_src_start = max(0, int(off_r))
        r_src_end = min(rows, rows + int(off_r))
        c_src_start = max(0, int(off_c))
        c_src_end = min(cols, cols + int(off_c))

        r_dst_start = max(0, -int(off_r))
        r_dst_end = r_dst_start + (r_src_end - r_src_start)
        c_dst_start = max(0, -int(off_c))
        c_dst_end = c_dst_start + (c_src_end - c_src_start)

        if (r_src_end <= r_src_start) or (c_src_end <= c_src_start):
            continue

        nozzle_z = z[r_dst_start:r_dst_end, c_dst_start:c_dst_end]
        neighbour_z = z_filled[r_src_start:r_src_end, c_src_start:c_src_end]

        # Cells where the nozzle tip Z is valid.
        nozzle_valid = np.isfinite(nozzle_z)
        max_allowed = nozzle_z + height_allowance

        violation = nozzle_valid & (neighbour_z > max_allowed)
        cone_ok[r_dst_start:r_dst_end, c_dst_start:c_dst_end] &= ~violation

    return cone_ok
