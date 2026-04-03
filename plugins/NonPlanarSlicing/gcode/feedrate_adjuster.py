"""Feedrate adjustment for non-planar slicing.

When a planar move is bent into 3D space, its true travel distance
increases.  The original feedrate (set by the slicer for the 2D XY
distance) must be reduced so that the nozzle tip speed along the
actual 3D path matches the intended speed.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

# Minimum feedrate floor (mm/min) to prevent stalling.
_MIN_FEEDRATE = 1.0

# Planar distance below which the move is considered vertical and no
# feedrate adjustment is applied.
_PLANAR_EPSILON = 0.001


def adjust_feedrate(
    original_feedrate: float,
    dx: float,
    dy: float,
    dz: float,
) -> float:
    """Adjust feedrate to maintain correct nozzle speed along a 3D path.

    The slicer computes feedrate for the planar (XY) distance of a
    move.  When a non-planar Z offset is added, the actual distance
    the nozzle must travel is longer.  To keep the same tangential
    speed the feedrate must be scaled down by the ratio of planar to
    actual distance.

    For a purely vertical move (dx and dy both near zero), no
    adjustment is needed because there is no meaningful planar
    reference.

    The returned feedrate is clamped to [1.0, original_feedrate] so it
    never increases speed or drops to a stalling value.

    Args:
        original_feedrate: The F value from the slicer (mm/min).
        dx: X displacement of this segment (mm).
        dy: Y displacement of this segment (mm).
        dz: Z displacement of this segment (mm).

    Returns:
        The adjusted feedrate in mm/min.
    """
    if original_feedrate <= 0.0:
        return _MIN_FEEDRATE

    planar_dist = math.sqrt(dx * dx + dy * dy)

    # For near-vertical moves there is no meaningful planar reference
    # for scaling, so return the feedrate unchanged.
    if planar_dist < _PLANAR_EPSILON:
        return original_feedrate

    actual_dist = math.sqrt(dx * dx + dy * dy + dz * dz)

    # actual_dist >= planar_dist always, so ratio <= 1.0.
    ratio = planar_dist / actual_dist
    adjusted = original_feedrate * ratio

    # Clamp: never exceed original (ratio should be <= 1, but guard
    # against float issues) and never go below the floor.
    return max(_MIN_FEEDRATE, min(original_feedrate, adjusted))
