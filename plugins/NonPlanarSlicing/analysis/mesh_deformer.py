"""Mesh deformation for CurviSlicer-style extended non-planar slicing (Phase II).

Deforms mesh vertices using a deformation field before CuraEngine slices,
producing genuinely curved infill.  After slicing, the G-code is
back-transformed (inverse deformation) to restore original geometry.

This module provides:
  - ``deform_mesh_vertices``: apply forward deformation to vertex array
  - ``inverse_deform_gcode_z``: back-transform G-code Z coordinates
  - ``validate_deformation``: check for self-intersections / invalid geometry

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .deformation_field import DeformationField

logger = logging.getLogger(__name__)

# Maximum number of Newton iterations for inverse Z lookup.
_INVERSE_MAX_ITER = 20
_INVERSE_TOLERANCE = 1e-4  # mm


def deform_mesh_vertices(
    vertices: NDArray[np.floating],
    deformation_field: DeformationField,
    *,
    z_up: bool = True,
) -> NDArray[np.floating]:
    """Apply forward deformation to mesh vertices.

    Each vertex's Z coordinate is displaced according to the deformation
    field at its (x, y, z) position.

    Parameters
    ----------
    vertices:
        (N, 3) vertex array. Modified copy is returned (original untouched).
    deformation_field:
        The deformation field to apply.
    z_up:
        If True, vertices are in Z-up coordinates (x, y, z).
        If False, vertices are in Cura scene coordinates (x, y_scene, z_scene)
        where y_scene=height, z_scene=-depth.

    Returns
    -------
    NDArray: (N, 3) deformed vertex array.
    """
    result = vertices.copy()
    n = result.shape[0]

    for i in range(n):
        if z_up:
            x, y, z = float(result[i, 0]), float(result[i, 1]), float(result[i, 2])
        else:
            # Cura scene: (x, y_scene, z_scene) → slicing: (x, -z_scene, y_scene)
            x = float(result[i, 0])
            y = -float(result[i, 2])
            z = float(result[i, 1])

        displacement = deformation_field.interpolate(x, y, z)

        if z_up:
            result[i, 2] += displacement
        else:
            # In scene coords, height = y_scene = column 1.
            result[i, 1] += displacement

    return result


def inverse_deform_z(
    x: float, y: float, z_deformed: float,
    deformation_field: DeformationField,
) -> float:
    """Find the original Z given a deformed Z at (x, y).

    Uses Newton's method to solve: z_orig + field(x, y, z_orig) = z_deformed.

    Parameters
    ----------
    x, y:
        XY position in analysis/slicing coordinates.
    z_deformed:
        The Z coordinate after deformation.
    deformation_field:
        The deformation field used for forward deformation.

    Returns
    -------
    float: The original (pre-deformation) Z coordinate.
    """
    # Initial guess: the deformed Z itself.
    z_guess = z_deformed

    for _ in range(_INVERSE_MAX_ITER):
        # Forward: z_guess + disp(z_guess) should equal z_deformed.
        disp = deformation_field.interpolate(x, y, z_guess)
        residual = z_guess + disp - z_deformed

        if abs(residual) < _INVERSE_TOLERANCE:
            return z_guess

        # Numerical derivative of (z + disp(z)) w.r.t. z.
        eps = 0.001
        disp_plus = deformation_field.interpolate(x, y, z_guess + eps)
        deriv = 1.0 + (disp_plus - disp) / eps

        if abs(deriv) < 1e-12:
            # Degenerate — fall back to simple subtraction.
            return z_deformed - disp

        z_guess -= residual / deriv

    # Did not converge — return best guess.
    logger.debug(
        "inverse_deform_z did not converge at (%.2f, %.2f, z_def=%.4f), "
        "residual=%.6f", x, y, z_deformed,
        z_guess + deformation_field.interpolate(x, y, z_guess) - z_deformed,
    )
    return z_guess


def inverse_deform_gcode_line(
    line: str,
    deformation_field: DeformationField,
    current_x: float,
    current_y: float,
    current_z: float,
    gcode_offset_x: float = 0.0,
    gcode_offset_y: float = 0.0,
) -> tuple[str, float, float, float]:
    """Back-transform a single G-code line's Z coordinate.

    Parses G0/G1 moves, applies inverse deformation to the Z value,
    and returns the modified line along with updated position tracking.

    Parameters
    ----------
    line:
        Raw G-code line.
    deformation_field:
        The deformation field used for forward deformation.
    current_x, current_y, current_z:
        Current absolute position before this line.
    gcode_offset_x, gcode_offset_y:
        Offsets to convert G-code XY → analysis XY.

    Returns
    -------
    (modified_line, new_x, new_y, new_z)
    """
    stripped = line.strip()
    if not (stripped.startswith("G0") or stripped.startswith("G1")):
        return line, current_x, current_y, current_z

    # Parse parameters.
    parts = stripped.split()
    new_x, new_y, new_z = current_x, current_y, current_z
    z_idx = -1

    for i, part in enumerate(parts):
        if part.startswith("X"):
            try:
                new_x = float(part[1:])
            except ValueError:
                pass
        elif part.startswith("Y"):
            try:
                new_y = float(part[1:])
            except ValueError:
                pass
        elif part.startswith("Z"):
            try:
                new_z = float(part[1:])
                z_idx = i
            except ValueError:
                pass

    if z_idx < 0:
        # No Z in this line — just update XY tracking.
        return line, new_x, new_y, current_z

    # Convert to analysis coordinates.
    ax = new_x - gcode_offset_x
    ay = new_y - gcode_offset_y

    # Apply inverse deformation.
    original_z = inverse_deform_z(ax, ay, new_z, deformation_field)

    # Reconstruct the Z parameter.
    parts[z_idx] = f"Z{original_z:.4f}"
    modified = " ".join(parts)

    # Preserve any trailing comment.
    if ";" in line and ";" not in modified:
        comment_start = line.index(";")
        modified += " " + line[comment_start:]

    return modified, new_x, new_y, original_z


def validate_deformation(
    vertices: NDArray[np.floating],
    deformation_field: DeformationField,
) -> bool:
    """Check if a deformation would produce valid (non-self-intersecting) geometry.

    Currently uses a simple heuristic: checks that no vertex's deformed
    Z crosses its neighbors' deformed Z (layer ordering preserved).

    Parameters
    ----------
    vertices:
        (N, 3) vertex array in Z-up coordinates.
    deformation_field:
        The deformation field to validate.

    Returns
    -------
    bool: True if the deformation appears valid.
    """
    n = vertices.shape[0]
    if n == 0:
        return True

    # Sample a grid of points and check that Z ordering is preserved.
    # Use the deformation field's own grid for efficiency.
    z_levels = deformation_field.z_levels
    rows, cols = deformation_field.grid_shape

    for layer_idx in range(1, deformation_field.num_layers):
        z_above = z_levels[layer_idx]
        z_below = z_levels[layer_idx - 1]

        for r in range(rows):
            for c in range(cols):
                x = deformation_field.x_min + c * deformation_field.resolution
                y = deformation_field.y_min + r * deformation_field.resolution

                deformed_above = deformation_field.get_target_z(x, y, z_above)
                deformed_below = deformation_field.get_target_z(x, y, z_below)

                if deformed_above <= deformed_below:
                    logger.warning(
                        "Deformation validation failed: layer %d Z=%.3f <= "
                        "layer %d Z=%.3f at (%.1f, %.1f)",
                        layer_idx, deformed_above,
                        layer_idx - 1, deformed_below,
                        x, y,
                    )
                    return False

    return True
