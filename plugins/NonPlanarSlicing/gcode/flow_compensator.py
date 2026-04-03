"""Flow compensation for non-planar slicing.

Adjusts extrusion (E) values to account for varying actual layer heights
in non-planar regions.  When a layer is thicker than nominal, more material
is needed; when thinner, less.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Multiplier bounds to prevent dangerously extreme extrusion changes.
_MIN_MULTIPLIER = 0.5
_MAX_MULTIPLIER = 2.0

# Physical bounds for actual layer height (mm).
_MIN_LAYER_HEIGHT = 0.05
_MAX_LAYER_HEIGHT_FACTOR = 3.0


def compensate_flow(
    e_value: float,
    actual_layer_height: float,
    nominal_layer_height: float,
    is_relative: bool,
    previous_abs_e: float = 0.0,
) -> float:
    """Scale an E (extrusion) value to compensate for changed layer height.

    When non-planar bending changes the Z of a layer, the effective
    layer thickness differs from the slicer's nominal layer height.
    This function scales the extrusion proportionally so that the
    volumetric flow matches the actual gap being filled.

    The multiplier is clamped to [0.5, 2.0] to guard against extreme
    values from degenerate geometry.

    Args:
        e_value: The E parameter value from the G-code line.
        actual_layer_height: The true layer thickness at this point
            after non-planar bending (mm).
        nominal_layer_height: The slicer's original layer height (mm).
        is_relative: True if the printer is in relative extrusion mode
            (M83).  In relative mode ``e_value`` is a delta; in
            absolute mode it is the target E position.
        previous_abs_e: Only used in absolute extrusion mode.  The
            absolute E position before this move, needed to compute
            and scale the delta.

    Returns:
        The adjusted E value (relative delta or absolute position,
        matching the input mode).
    """
    if nominal_layer_height <= 0.0:
        logger.debug(
            "Nominal layer height is %.4f; skipping flow compensation.",
            nominal_layer_height,
        )
        return e_value

    # Compute and clamp the multiplier.
    raw_multiplier = actual_layer_height / nominal_layer_height
    multiplier = max(_MIN_MULTIPLIER, min(_MAX_MULTIPLIER, raw_multiplier))

    if is_relative:
        # In relative mode, e_value is already a delta -- scale directly.
        return e_value * multiplier
    else:
        # In absolute mode, compute the delta from the previous position,
        # scale it, and add back to the previous position.
        delta = e_value - previous_abs_e
        scaled_delta = delta * multiplier
        return previous_abs_e + scaled_delta


def compute_actual_layer_height(
    current_z: float,
    layer_below_z: float | None,
    nominal_layer_height: float,
) -> float:
    """Compute the actual layer thickness at a given point.

    The actual height is the difference between the current Z and the
    Z of the layer directly below.  The result is clamped to physical
    bounds to prevent absurd values from degenerate geometry.

    Args:
        current_z: Z coordinate of the current layer at this XY
            position (mm).
        layer_below_z: Z coordinate of the layer below at the same
            XY position (mm), or None if not available (e.g. first
            layer or outside the height map).
        nominal_layer_height: The slicer's original layer height,
            used as a fallback and for computing the upper clamp
            bound (mm).

    Returns:
        The actual layer height in mm, clamped to
        [0.05, nominal_layer_height * 3].
    """
    if layer_below_z is None:
        return nominal_layer_height

    actual = current_z - layer_below_z

    # Clamp to safe physical range.
    max_height = max(nominal_layer_height * _MAX_LAYER_HEIGHT_FACTOR, _MIN_LAYER_HEIGHT)
    clamped = max(_MIN_LAYER_HEIGHT, min(max_height, actual))

    if clamped != actual:
        logger.debug(
            "Clamped actual layer height from %.4f to %.4f (nominal=%.3f).",
            actual,
            clamped,
            nominal_layer_height,
        )

    return clamped
