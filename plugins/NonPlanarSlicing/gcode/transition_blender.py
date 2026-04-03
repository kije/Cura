"""Transition blending for non-planar slicing.

Computes smooth blend factors at the boundary between planar and
non-planar printing regions.  This prevents abrupt Z jumps at region
edges by smoothly transitioning from the original (planar) Z to the
non-planar (bent) Z over a configurable distance.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

try:
    from scipy.ndimage import distance_transform_edt
except ImportError:  # pragma: no cover
    distance_transform_edt = None  # type: ignore[assignment]
    logger.warning(
        "scipy is not available; transition blending will fall back to "
        "a binary (non-blended) boundary."
    )


def smoothstep(t: NDArray[np.floating] | float) -> NDArray[np.floating] | float:
    """Hermite smoothstep function with C1 continuity.

    Maps input values through ``3t^2 - 2t^3``, which has zero first
    derivative at both endpoints (t=0 and t=1).  Input values are
    clamped to [0, 1] before the polynomial is applied.

    This is a vectorized operation when *t* is a numpy array.

    Args:
        t: Input value(s) in any range.  Values outside [0, 1] are
            clamped.

    Returns:
        Smoothstepped value(s) in [0, 1].
    """
    t_clamped = np.clip(t, 0.0, 1.0)
    return t_clamped * t_clamped * (3.0 - 2.0 * t_clamped)


def compute_blend_map(
    safe_map: NDArray[np.bool_],
    resolution: float,
    blend_distance: float,
) -> NDArray[np.floating]:
    """Compute a 2D blend-factor map from a boolean safe region mask.

    The blend factor smoothly transitions from 0.0 at the boundary of
    the safe (non-planar) region to 1.0 deep inside the region, over
    the specified ``blend_distance``.

    The Euclidean distance transform of the safe_map is computed,
    scaled by the grid ``resolution`` to get physical millimetre
    distances, normalized by ``blend_distance``, and passed through a
    smoothstep function for C1-continuous blending.

    Points outside the safe region (safe_map == False) always get a
    blend factor of 0.0.

    Args:
        safe_map: 2D boolean array where True marks grid cells inside
            a non-planar region.  Shape (rows, cols).
        resolution: Physical size of each grid cell in mm.  Used as
            the sampling parameter for the distance transform.
        blend_distance: Distance in mm over which to blend from
            planar to non-planar Z.  Must be positive.

    Returns:
        2D float array of the same shape as ``safe_map``, with values
        in [0.0, 1.0].  0.0 at the region boundary (and outside),
        1.0 deep inside.

    Raises:
        ValueError: If ``blend_distance`` or ``resolution`` is not
            positive.
    """
    if resolution <= 0.0:
        raise ValueError(f"resolution must be positive, got {resolution}")
    if blend_distance <= 0.0:
        raise ValueError(f"blend_distance must be positive, got {blend_distance}")

    if safe_map.size == 0:
        return np.empty_like(safe_map, dtype=np.float64)

    # If scipy is unavailable, fall back to a binary map (no smooth
    # transition).
    if distance_transform_edt is None:
        logger.warning(
            "Using binary blend map (no smooth transition) because "
            "scipy.ndimage is not available."
        )
        return safe_map.astype(np.float64)

    # The distance transform computes, for each True cell, the
    # Euclidean distance (in grid units) to the nearest False cell.
    # We scale to mm using the ``sampling`` parameter.
    dist_mm: NDArray[np.floating] = distance_transform_edt(
        safe_map, sampling=resolution,
    )

    # Normalize so that ``blend_distance`` mm maps to 1.0.
    normalized = dist_mm / blend_distance

    # Apply smoothstep for C1 continuity.
    blend: NDArray[np.floating] = smoothstep(normalized)

    # Ensure anything outside the safe region is exactly 0.
    blend[~safe_map] = 0.0

    logger.debug(
        "Blend map computed: shape=%s, non-zero cells=%d, "
        "blend_distance=%.2f mm, resolution=%.3f mm",
        blend.shape,
        int(np.count_nonzero(blend)),
        blend_distance,
        resolution,
    )

    return blend
