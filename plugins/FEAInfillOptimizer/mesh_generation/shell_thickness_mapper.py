# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Map FEA surface-stress metrics to per-zone Cura shell (wall/top/bottom) settings.

This module sits between the surface stress analyzer — which produces
per-element wall/top/bottom metrics — and the modifier mesh creator that
applies those settings to Cura scene nodes.  It aggregates element-level
metrics to a single :class:`ShellSettings` value per zone using the 85th
percentile of non-zero surface elements, then scales the result to discrete
Cura parameters (line counts and layer counts) via a square-root mapping that
favours lower stress regions while still reacting to high-stress outliers.

Typical usage::

    from mesh_generation.shell_thickness_mapper import compute_zone_shell_settings

    settings_per_zone = compute_zone_shell_settings(
        zones=zones,
        W_wall=wall_metric,
        W_top=top_metric,
        W_bottom=bottom_metric,
        wall_mask=wall_surface_mask,
        top_mask=top_surface_mask,
        bottom_mask=bottom_surface_mask,
        line_width=0.4,
        layer_height=0.2,
    )
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional

import numpy as np


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ShellSettings:
    """Cura shell parameters for a single zone.

    All thickness values are in millimetres.

    Attributes:
        wall_thickness_mm: Total wall thickness (line_width * wall_line_count).
        wall_line_count: Number of wall perimeter lines.
        top_thickness_mm: Total top skin thickness (layer_height * top_layers).
        top_layers: Number of top solid layers.
        bottom_thickness_mm: Total bottom skin thickness (layer_height * bottom_layers).
        bottom_layers: Number of bottom solid layers.
    """

    wall_thickness_mm: float
    wall_line_count: int
    top_thickness_mm: float
    top_layers: int
    bottom_thickness_mm: float
    bottom_layers: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _percentile_nonzero(values: np.ndarray, p: float) -> float:
    """Return the *p*-th percentile of the non-zero entries in *values*.

    Falls back to ``0.0`` when *values* is empty or contains no non-zero
    entries.

    Args:
        values: 1-D float array of metric values, expected range [0, 1].
        p: Percentile in [0, 100].

    Returns:
        Percentile value as a Python float, or 0.0 when no data is available.
    """
    if values.size == 0:
        return 0.0
    nonzero = values[values > 0.0]
    if nonzero.size == 0:
        return 0.0
    return float(np.percentile(nonzero, p))


def _sqrt_map(metric: float, lo: int, hi: int) -> int:
    """Map a normalised metric in [0, 1] to an integer in [lo, hi] via sqrt.

    The square-root curve gives a steeper response at low metric values and
    compresses the high end, so even moderately stressed regions receive
    meaningful shell reinforcement.

    Args:
        metric: Normalised stress metric in [0, 1].
        lo: Minimum discrete output value (>= 1).
        hi: Maximum discrete output value (>= lo).

    Returns:
        Rounded integer in ``[lo, hi]``.
    """
    metric = max(0.0, min(1.0, metric))  # defensive clamp
    raw = lo + (hi - lo) * math.sqrt(metric)
    return int(round(raw))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_zone_shell_settings(
    zones: List[Any],
    W_wall: np.ndarray,
    W_top: np.ndarray,
    W_bottom: np.ndarray,
    wall_mask: np.ndarray,
    top_mask: np.ndarray,
    bottom_mask: np.ndarray,
    line_width: float = 0.4,
    layer_height: float = 0.2,
    bonding_coeff: float = 1.0,
    wall_count_min: int = 1,
    wall_count_max: int = 6,
    top_layers_min: int = 2,
    top_layers_max: int = 8,
    bottom_layers_min: int = 2,
    bottom_layers_max: int = 8,
) -> List[Optional[ShellSettings]]:
    """Compute :class:`ShellSettings` for every zone from element-level metrics.

    For each zone the function:

    1. Identifies which elements in the zone are exposed surface (wall/top/
       bottom) using the corresponding boolean masks.
    2. If **no** surface elements exist, returns ``None`` for that zone — the
       caller should treat these as interior zones and leave Cura wall/skin
       settings at their defaults (typically 0, relying on the infill).
    3. Aggregates the per-element metric for each surface type using the 85th
       percentile of non-zero values (robust to isolated noise spikes).
    4. Amplifies the wall requirement for low-density zones to compensate for
       reduced infill support::

           W_wall_adjusted = min(W_wall_zone * (1 + 0.5 * (1 - zone.density)), 1.0)

    5. Maps each adjusted metric to a discrete count via :func:`_sqrt_map`,
       then converts counts to mm thicknesses:

       * ``wall_thickness_mm  = line_width  * wall_line_count``
       * ``top_thickness_mm   = layer_height * top_layers``
       * ``bottom_thickness_mm = layer_height * bottom_layers``

    The ``bonding_coeff`` parameter is reserved for future use (e.g. scaling
    top/bottom layers for materials with poor interlayer adhesion); it is
    accepted but currently has no effect on the output.

    Args:
        zones: Zone objects that each expose a ``density`` attribute (float in
            [0, 1]) and an ``element_indices`` attribute (list of int).
        W_wall: Per-element wall surface stress metric, shape ``(M,)``, values
            in [0, 1].
        W_top: Per-element top surface stress metric, shape ``(M,)``, values
            in [0, 1].
        W_bottom: Per-element bottom surface stress metric, shape ``(M,)``,
            values in [0, 1].
        wall_mask: Boolean mask of shape ``(M,)`` marking wall surface elements.
        top_mask: Boolean mask of shape ``(M,)`` marking top surface elements.
        bottom_mask: Boolean mask of shape ``(M,)`` marking bottom surface
            elements.
        line_width: Nozzle line width in mm (used to convert wall line count to
            mm).  Defaults to ``0.4``.
        layer_height: Layer height in mm (used to convert layer counts to mm).
            Defaults to ``0.2``.
        bonding_coeff: Interlayer bonding coefficient (reserved for future use).
            Defaults to ``1.0``.
        wall_count_min: Minimum number of wall perimeter lines.  Defaults to
            ``1``.
        wall_count_max: Maximum number of wall perimeter lines.  Defaults to
            ``6``.
        top_layers_min: Minimum number of top solid layers.  Defaults to ``2``.
        top_layers_max: Maximum number of top solid layers.  Defaults to ``8``.
        bottom_layers_min: Minimum number of bottom solid layers.  Defaults to
            ``2``.
        bottom_layers_max: Maximum number of bottom solid layers.  Defaults to
            ``8``.

    Returns:
        List of :class:`ShellSettings` (or ``None``) with one entry per zone.
        ``None`` indicates a fully interior zone with no surface exposure.

    Raises:
        ValueError: If any ``*_min > *_max`` bound pair is invalid, or if
            ``line_width`` or ``layer_height`` is not positive.
    """
    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    if line_width <= 0.0:
        raise ValueError(f"line_width must be positive, got {line_width}")
    if layer_height <= 0.0:
        raise ValueError(f"layer_height must be positive, got {layer_height}")
    if wall_count_min > wall_count_max:
        raise ValueError(
            f"wall_count_min ({wall_count_min}) > wall_count_max ({wall_count_max})"
        )
    if top_layers_min > top_layers_max:
        raise ValueError(
            f"top_layers_min ({top_layers_min}) > top_layers_max ({top_layers_max})"
        )
    if bottom_layers_min > bottom_layers_max:
        raise ValueError(
            f"bottom_layers_min ({bottom_layers_min}) > bottom_layers_max ({bottom_layers_max})"
        )

    # Convert masks to numpy bool arrays defensively
    wall_mask_b = np.asarray(wall_mask, dtype=bool)
    top_mask_b = np.asarray(top_mask, dtype=bool)
    bottom_mask_b = np.asarray(bottom_mask, dtype=bool)

    results: List[Optional[ShellSettings]] = []

    for zone in zones:
        zone_indices = np.asarray(zone.element_indices, dtype=np.intp)

        # ----------------------------------------------------------------
        # Guard: empty zone
        # ----------------------------------------------------------------
        if zone_indices.size == 0:
            results.append(None)
            continue

        # ----------------------------------------------------------------
        # Identify surface elements within this zone
        # ----------------------------------------------------------------
        zone_wall_mask = wall_mask_b[zone_indices]
        zone_top_mask = top_mask_b[zone_indices]
        zone_bottom_mask = bottom_mask_b[zone_indices]

        has_wall = zone_wall_mask.any()
        has_top = zone_top_mask.any()
        has_bottom = zone_bottom_mask.any()

        # Interior zone — no surface elements at all
        if not (has_wall or has_top or has_bottom):
            results.append(None)
            continue

        # ----------------------------------------------------------------
        # Aggregate: 85th percentile of non-zero surface element metrics
        # ----------------------------------------------------------------
        if has_wall:
            wall_elem_indices = zone_indices[zone_wall_mask]
            W_wall_zone = _percentile_nonzero(W_wall[wall_elem_indices], 85.0)
        else:
            W_wall_zone = 0.0

        if has_top:
            top_elem_indices = zone_indices[zone_top_mask]
            W_top_zone = _percentile_nonzero(W_top[top_elem_indices], 85.0)
        else:
            W_top_zone = 0.0

        if has_bottom:
            bottom_elem_indices = zone_indices[zone_bottom_mask]
            W_bottom_zone = _percentile_nonzero(W_bottom[bottom_elem_indices], 85.0)
        else:
            W_bottom_zone = 0.0

        # ----------------------------------------------------------------
        # Infill-wall interaction: amplify wall metric for low-density zones
        # ----------------------------------------------------------------
        W_wall_adjusted = min(W_wall_zone * (1.0 + 0.5 * (1.0 - zone.density)), 1.0)

        # ----------------------------------------------------------------
        # Map to discrete counts via sqrt curve
        # ----------------------------------------------------------------
        wall_count = _sqrt_map(W_wall_adjusted, wall_count_min, wall_count_max)
        top_count = _sqrt_map(W_top_zone, top_layers_min, top_layers_max)
        bottom_count = _sqrt_map(W_bottom_zone, bottom_layers_min, bottom_layers_max)

        # ----------------------------------------------------------------
        # Convert to mm thicknesses
        # ----------------------------------------------------------------
        wall_thickness = line_width * wall_count
        top_thickness = layer_height * top_count
        bottom_thickness = layer_height * bottom_count

        results.append(
            ShellSettings(
                wall_thickness_mm=wall_thickness,
                wall_line_count=wall_count,
                top_thickness_mm=top_thickness,
                top_layers=top_count,
                bottom_thickness_mm=bottom_thickness,
                bottom_layers=bottom_count,
            )
        )

    return results
