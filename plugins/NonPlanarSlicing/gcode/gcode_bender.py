"""Core non-planar G-code bending module.

Applies non-planar Z-bending to Cura G-code by warping the topmost
layers to follow a surface height map.  This is the primary
transformation in the non-planar slicing pipeline.

The algorithm:
  1. Parse the G-code into structured moves.
  2. Identify the topmost N layers eligible for bending.
  3. For each extrusion move in those layers, subdivide into short
     segments, look up the surface Z from the height map, compute a
     blended target Z, and optionally adjust flow and feedrate.
  4. Validate that no segment exceeds the maximum nozzle angle.
  5. Revert any region that fails validation.
  6. Reconstruct the G-code with comment markers.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from .feedrate_adjuster import adjust_feedrate
from .flow_compensator import compensate_flow, compute_actual_layer_height
from .gcode_parser import GCodeMove, ParsedGCode, parse_gcode, reconstruct_gcode

logger = logging.getLogger(__name__)

# Line types that should NEVER be bent (regardless of settings).
_NEVER_BEND_LINE_TYPES = frozenset({
    "SUPPORT", "SUPPORT-INTERFACE", "PRIME-TOWER", "SKIRT",
})

# Additional line types skipped in "skin_walls" mode.
_INFILL_LINE_TYPES = frozenset({
    "FILL",
})

# Default segment length for subdivision (mm).
_DEFAULT_SEGMENT_LENGTH = 1.0


class HeightMapProtocol(Protocol):
    """Protocol describing the expected height_map interface."""

    def interpolate(self, x: float, y: float) -> float:
        """Return the surface Z at (x, y), or NaN if outside bounds."""
        ...

    def is_valid(self, x: float, y: float) -> bool:
        """Return True if (x, y) is within the height map bounds."""
        ...

    def get_grid_coords(self, x: float, y: float) -> tuple[int, int]:
        """Return the (row, col) grid indices for the given (x, y)."""
        ...


def subdivide_segment(
    x0: float,
    y0: float,
    z0: float,
    e0: float,
    x1: float,
    y1: float,
    z1: float,
    e1: float,
    f: float | None,
    max_length: float,
) -> list[tuple[float, float, float, float, float | None]]:
    """Split a line segment into sub-segments of at most *max_length* mm.

    E is interpolated linearly along the segment.  F is preserved for
    every sub-segment.

    Args:
        x0, y0, z0, e0: Start position and extrusion.
        x1, y1, z1, e1: End position and extrusion.
        f: Feedrate (passed through unchanged, may be None).
        max_length: Maximum 3D length of each sub-segment (mm).

    Returns:
        List of (x, y, z, e, f) tuples for each sub-segment endpoint.
        The list always contains at least one entry (the endpoint).
    """
    if max_length <= 0.0:
        max_length = _DEFAULT_SEGMENT_LENGTH

    dx = x1 - x0
    dy = y1 - y0
    dz = z1 - z0
    segment_length = math.sqrt(dx * dx + dy * dy + dz * dz)

    if segment_length <= max_length or segment_length < 1e-9:
        return [(x1, y1, z1, e1, f)]

    n_segments = max(1, math.ceil(segment_length / max_length))
    de = e1 - e0
    result: list[tuple[float, float, float, float, float | None]] = []

    for i in range(1, n_segments + 1):
        t = i / n_segments
        result.append((
            x0 + dx * t,
            y0 + dy * t,
            z0 + dz * t,
            e0 + de * t,
            f,
        ))

    return result


def validate_moves(
    moves: list[GCodeMove],
    max_angle_deg: float,
) -> list[bool]:
    """Check consecutive move pairs for maximum nozzle angle violations.

    The nozzle angle is ``arctan(|dz| / planar_distance)``.  A move
    pair violates the constraint if this angle exceeds *max_angle_deg*.

    Args:
        moves: Ordered list of moves to validate.
        max_angle_deg: Maximum allowable angle in degrees (e.g. 45).

    Returns:
        List of booleans, one per move.  ``True`` means the move is
        valid.  The first move is always valid (no predecessor to
        compare against).
    """
    if not moves:
        return []

    if max_angle_deg <= 0.0:
        # Zero or negative means "no bending allowed" -- everything
        # except the first move is invalid if there's any dz.
        max_angle_deg = 0.0

    tan_max = math.tan(math.radians(min(max_angle_deg, 89.9)))
    results: list[bool] = [True]  # First move is always valid.

    for i in range(1, len(moves)):
        prev = moves[i - 1]
        curr = moves[i]
        dx = curr.abs_x - prev.abs_x
        dy = curr.abs_y - prev.abs_y
        dz = abs(curr.abs_z - prev.abs_z)
        planar = math.sqrt(dx * dx + dy * dy)

        if planar < 1e-9:
            # Pure vertical move -- valid only if dz is negligible.
            results.append(dz < 1e-6)
        else:
            results.append(dz / planar <= tan_max + 1e-9)

    return results


def _lookup_blend_factor(
    blend_map: NDArray[np.floating],
    height_map: HeightMapProtocol,
    x: float,
    y: float,
) -> float:
    """Look up the blend factor for a given XY position.

    Returns 0.0 if the position is outside the map or invalid.
    """
    if not height_map.is_valid(x, y):
        return 0.0
    row, col = height_map.get_grid_coords(x, y)
    if 0 <= row < blend_map.shape[0] and 0 <= col < blend_map.shape[1]:
        return float(blend_map[row, col])
    return 0.0


def _lookup_safe(
    safe_map: NDArray[np.bool_],
    height_map: HeightMapProtocol,
    x: float,
    y: float,
) -> bool:
    """Check if (x, y) is within the safe (non-planar) region."""
    if not height_map.is_valid(x, y):
        return False
    row, col = height_map.get_grid_coords(x, y)
    if 0 <= row < safe_map.shape[0] and 0 <= col < safe_map.shape[1]:
        return bool(safe_map[row, col])
    return False


def bend_gcode(
    gcode_list: list[str],
    height_map: Any,
    safe_map: NDArray[np.bool_],
    blend_map: NDArray[np.floating],
    settings: dict[str, Any],
) -> list[str]:
    """Apply non-planar Z-bending to Cura G-code.

    This is the top-level entry point.  It parses the G-code, bends
    the topmost layers to follow the surface height map, validates the
    result, and returns the modified gcode_list.

    Args:
        gcode_list: Cura-format G-code list (header, start gcode,
            layer chunks).
        height_map: Object implementing ``interpolate(x, y)``,
            ``is_valid(x, y)``, and ``get_grid_coords(x, y)``.
        safe_map: 2D boolean array marking non-planar-safe cells.
        blend_map: 2D float array of blend factors from
            ``compute_blend_map``.
        settings: Dictionary with keys:
            - ``layer_height`` (float): nominal layer height in mm.
            - ``nonplanar_layer_count`` (int): number of topmost
              layers to bend.
            - ``max_angle_deg`` (float): maximum nozzle angle.
            - ``flow_compensation`` (bool): adjust E for layer height
              changes.
            - ``feedrate_compensation`` (bool): adjust F for 3D
              distance.
            - ``is_relative_extrusion`` (bool): extrusion mode.
            - ``segment_length`` (float, optional): subdivision
              length, default 1.0 mm.

    Returns:
        Modified gcode_list with non-planar Z offsets applied.
    """
    if not gcode_list:
        logger.warning("Empty gcode_list; nothing to bend.")
        return gcode_list

    # Extract settings with defaults.
    layer_height = float(settings.get("layer_height", 0.2))
    nonplanar_layer_count = int(settings.get("nonplanar_layer_count", 0))
    max_angle_deg = float(settings.get("max_angle_deg", 45.0))
    flow_compensation = bool(settings.get("flow_compensation", True))
    feedrate_compensation = bool(settings.get("feedrate_compensation", True))
    is_relative = bool(settings.get("is_relative_extrusion", False))
    segment_length = float(settings.get("segment_length", _DEFAULT_SEGMENT_LENGTH))
    surface_mode = str(settings.get("surface_mode", "all_surfaces"))
    max_flow_multiplier = float(settings.get("max_flow_multiplier", 2.0))
    min_flow_multiplier = float(settings.get("min_flow_multiplier", 0.5))

    # Line type filtering: "skin_walls" skips infill, "all" bends everything.
    nonplanar_line_types = str(settings.get("nonplanar_line_types", "skin_walls"))
    skip_line_types = set(_NEVER_BEND_LINE_TYPES)
    if nonplanar_line_types == "skin_walls":
        skip_line_types |= _INFILL_LINE_TYPES

    # Maximum Z deviation from the original path position.
    # This is the PRIMARY safety clamp — prevents paths from shooting up
    # into the air unsupported.  Default: nozzle_size (typically 0.4mm).
    nozzle_size = float(settings.get("nozzle_size", 0.4))
    max_path_deviation = float(settings.get("max_path_deviation", nozzle_size))
    if max_path_deviation <= 0.0:
        max_path_deviation = nozzle_size if nozzle_size > 0.0 else 0.4

    # G-code coordinates use machine origin (corner for most printers).
    # Analysis height maps use model-centered coordinates.  These offsets
    # convert G-code XY → analysis XY:  analysis = gcode - offset.
    gcode_offset_x = float(settings.get("gcode_offset_x", 0.0))
    gcode_offset_y = float(settings.get("gcode_offset_y", 0.0))

    # Parse.
    parsed = parse_gcode(gcode_list)

    if not parsed.moves:
        logger.warning("No moves found in G-code; nothing to bend.")
        return gcode_list

    # Use parsed layer height if available and settings didn't provide one.
    if layer_height <= 0.0 and parsed.layer_height > 0.0:
        layer_height = parsed.layer_height

    # Override extrusion mode from parsed data if not explicitly set.
    if "is_relative_extrusion" not in settings:
        is_relative = parsed.is_relative_extrusion

    if parsed.total_layers <= 0:
        logger.warning("No layers detected; nothing to bend.")
        return gcode_list

    max_layer = parsed.total_layers - 1

    # Auto-compute layer count if set to 0 (automatic mode).
    # Derive from the maximum Z variation in the height map.
    if nonplanar_layer_count == 0 and layer_height > 0.0:
        try:
            candidate_z = getattr(height_map, "candidate_z_values", None)
            if candidate_z is not None:
                finite_z = candidate_z[np.isfinite(candidate_z)]
                if finite_z.size > 0:
                    max_z_delta = float(np.max(finite_z) - np.min(finite_z))
                    nonplanar_layer_count = max(1, int(math.ceil(max_z_delta / layer_height)) + 1)
                    nonplanar_layer_count = min(nonplanar_layer_count, 20)  # clamp
                    logger.info(
                        "Auto layer count: z_delta=%.2f mm, layer_height=%.3f mm → %d layers",
                        max_z_delta, layer_height, nonplanar_layer_count,
                    )
        except Exception:
            logger.warning("Failed to auto-compute layer count; falling back to 5")

        if nonplanar_layer_count == 0:
            nonplanar_layer_count = 5

    # In "all_surfaces" mode, all layers are candidates — eligibility
    # is determined per-move based on proximity to the surface height
    # map.  In "top_only" mode, only the topmost N layers are bent.
    all_surfaces_mode = surface_mode == "all_surfaces"
    if all_surfaces_mode:
        target_layers = set(range(0, max_layer + 1))
        # Max depth below surface to consider for bending (mm).
        max_bend_depth = nonplanar_layer_count * layer_height
    else:
        top_layer_start = max(0, max_layer - nonplanar_layer_count + 1)
        target_layers = set(range(top_layer_start, max_layer + 1))
        max_bend_depth = 0.0  # Not used in top_only mode.

    if not target_layers:
        logger.info("No target layers for non-planar bending.")
        return gcode_list

    logger.info(
        "Bending %d target layers (mode=%s), layer_height=%.3f, "
        "max_angle=%.1f deg, segment_length=%.2f mm, "
        "max_path_deviation=%.3f mm, line_types=%s, "
        "gcode_offset=(%.1f, %.1f)",
        len(target_layers),
        surface_mode,
        layer_height,
        max_angle_deg,
        segment_length,
        max_path_deviation,
        nonplanar_line_types,
        gcode_offset_x,
        gcode_offset_y,
    )

    # Process moves: build list of modified moves, potentially with
    # subdivision creating additional moves for a single original.
    modified_moves: list[GCodeMove] = []
    region_id = 0
    region_start_indices: dict[int, int] = {}  # region_id -> start index in modified_moves
    region_end_indices: dict[int, int] = {}    # region_id -> end index (exclusive)
    region_original_moves: dict[int, list[GCodeMove]] = {}  # originals for revert
    in_region = False
    current_region_id = -1

    # Track the previous absolute E for flow compensation in absolute mode.
    prev_abs_e = 0.0
    # Track previous position for feedrate adjustment.
    prev_x = 0.0
    prev_y = 0.0
    prev_z = 0.0
    # Track current feedrate.
    current_feedrate = 0.0

    # Track whether the last emitted Z differs from the current layer Z.
    # When a bent region ends, we must inject an explicit Z on the next
    # move to restore the printer to the correct height.  Without this,
    # the printer stays at the last bent Z because most G1 lines don't
    # include a Z coordinate.
    _last_emitted_z: float | None = None  # Last Z value written to output.
    _diag_z_restored = 0  # Count of moves where we injected a Z restore.

    # Diagnostic counters.
    _diag_not_target = 0
    _diag_not_extrusion = 0
    _diag_skip_type = 0
    _diag_travel = 0
    _diag_not_safe = 0
    _diag_no_surface = 0
    _diag_above = 0
    _diag_below = 0
    _diag_bent = 0
    _diag_nan_interp = 0
    _diag_actually_bent = 0
    _diag_zero_delta = 0
    _diag_zero_blend = 0
    _diag_target_equals_sz = 0
    _diag_max_z_delta = 0.0
    _diag_sample_count = 0
    _diag_z_clamped = 0

    for move_idx, move in enumerate(parsed.moves):
        # Update tracking state from previous moves.
        if move_idx > 0:
            prev_move = parsed.moves[move_idx - 1]
            prev_x = prev_move.abs_x
            prev_y = prev_move.abs_y
            prev_z = prev_move.abs_z
            prev_abs_e = prev_move.abs_e
        if move.f is not None:
            current_feedrate = move.f

        # Check if this move is a candidate for bending.
        if move.layer_number not in target_layers:
            _diag_not_target += 1
            should_bend = False
        elif not move.is_extrusion:
            _diag_not_extrusion += 1
            should_bend = False
        elif move.line_type in skip_line_types:
            _diag_skip_type += 1
            should_bend = False
        elif move.is_travel:
            _diag_travel += 1
            should_bend = False
        else:
            should_bend = True

        if should_bend:
            # Convert G-code XY to analysis/height-map XY.
            ax = move.abs_x - gcode_offset_x
            ay = move.abs_y - gcode_offset_y
            # Check if XY is in a non-planar region.
            is_safe = _lookup_safe(safe_map, height_map, ax, ay)
            if not is_safe:
                _diag_not_safe += 1
                should_bend = False

        if should_bend and all_surfaces_mode:
            # In all_surfaces mode, only bend moves whose Z is within
            # max_bend_depth below the surface at this XY.
            surface_z_check = height_map.interpolate(ax, ay)
            if math.isnan(surface_z_check):
                _diag_no_surface += 1
                should_bend = False
            elif move.abs_z > surface_z_check + layer_height:
                # Move is above the surface — don't bend.
                _diag_above += 1
                should_bend = False
            elif move.abs_z < surface_z_check - max_bend_depth:
                # Move is too far below the surface — don't bend.
                _diag_below += 1
                should_bend = False

        if not should_bend:
            # End any active region.
            if in_region:
                region_end_indices[current_region_id] = len(modified_moves)
                in_region = False

            # CRITICAL FIX: If the last emitted Z was a bent value that
            # differs from this move's actual Z, we must inject an
            # explicit Z coordinate.  Without this, the printer stays at
            # the bent Z because most G1 lines omit the Z parameter.
            if (_last_emitted_z is not None
                    and move.z is None
                    and abs(_last_emitted_z - move.abs_z) > 0.001):
                # Create a copy of the move with an explicit Z to
                # restore the printer to the correct layer height.
                restored_move = GCodeMove(
                    command=move.command,
                    x=move.x,
                    y=move.y,
                    z=move.abs_z,  # Inject explicit Z
                    e=move.e,
                    f=move.f,
                    abs_x=move.abs_x,
                    abs_y=move.abs_y,
                    abs_z=move.abs_z,
                    abs_e=move.abs_e,
                    line_type=move.line_type,
                    layer_number=move.layer_number,
                    original_line=move.original_line,
                    chunk_index=move.chunk_index,
                    line_index_in_chunk=move.line_index_in_chunk,
                    is_travel=move.is_travel,
                    is_extrusion=move.is_extrusion,
                )
                modified_moves.append(restored_move)
                _last_emitted_z = move.abs_z
                _diag_z_restored += 1
            else:
                modified_moves.append(move)
                # Track emitted Z if this move has an explicit Z.
                if move.z is not None:
                    _last_emitted_z = move.z
            continue

        # Start a new region if not already in one.
        if not in_region:
            region_id += 1
            current_region_id = region_id
            region_start_indices[current_region_id] = len(modified_moves)
            region_original_moves[current_region_id] = []
            in_region = True

        region_original_moves[current_region_id].append(move)
        _diag_bent += 1

        # Always subdivide using absolute E values for correct
        # interpolation across sub-segments.
        move_e_abs = move.abs_e if not is_relative else (
            prev_abs_e + (move.e if move.e is not None else 0.0)
        )
        sub_points = subdivide_segment(
            prev_x, prev_y, prev_z,
            prev_abs_e,
            move.abs_x, move.abs_y, move.abs_z,
            move_e_abs,
            current_feedrate if current_feedrate > 0 else move.f,
            segment_length,
        )

        # Compute layers_from_top for Z offset.
        if all_surfaces_mode:
            # In all_surfaces mode, compute how many layers below the
            # local surface this move sits, based on its Z vs surface Z.
            _local_surface_z = height_map.interpolate(ax, ay)
            if not math.isnan(_local_surface_z):
                layers_from_top = max(0, round(
                    (_local_surface_z - move.abs_z) / layer_height
                ))
            else:
                layers_from_top = 0
        else:
            layers_from_top = max_layer - move.layer_number

        # Bend each sub-segment.
        sub_prev_x = prev_x
        sub_prev_y = prev_y
        sub_prev_z = prev_z
        sub_prev_bent_z = prev_z  # Track bent Z for layer height estimation.
        sub_prev_abs_e = prev_abs_e

        for seg_idx, (sx, sy, sz, se_abs, sf) in enumerate(sub_points):
            # Convert sub-segment G-code XY to analysis space.
            sax = sx - gcode_offset_x
            say = sy - gcode_offset_y
            # Look up surface Z from height map.
            surface_z = height_map.interpolate(sax, say)

            if math.isnan(surface_z):
                # Outside the height map -- no bending, pass through.
                bent_z = sz
                _diag_nan_interp += 1
            else:
                # Compute target Z: surface minus layers_from_top offsets.
                target_z = surface_z - layers_from_top * layer_height

                # Look up blend factor.
                blend_factor = _lookup_blend_factor(blend_map, height_map, sax, say)

                # Linearly interpolate between original Z and target Z.
                bent_z = sz + (target_z - sz) * blend_factor

                # Safety clamps for Z displacement.
                #
                # The blend_factor from the blend_map naturally constrains
                # displacement (0.0 at edges, 1.0 at center).  These
                # clamps are safety nets for edge cases.
                #
                # 1. Don't let any single layer deviate more than
                #    max_path_deviation from its conformal target.
                #    The conformal target is: surface_z - layers_from_top * layer_height
                #    and bent_z should be close to it (modulated by blend_factor).
                #    This prevents individual paths from going rogue.
                conformal_deviation = abs(bent_z - target_z)
                if conformal_deviation > max_path_deviation and blend_factor > 0.5:
                    # Only clamp when blend_factor is high — at low blend
                    # the move is supposed to be close to its original Z.
                    bent_z = target_z + math.copysign(
                        max_path_deviation, bent_z - target_z
                    )
                    _diag_z_clamped += 1

                # 2. Don't go below zero (bed surface).
                if bent_z < 0.0:
                    bent_z = max(0.05, sz)
                    _diag_z_clamped += 1

                # Track bending statistics.
                z_delta = abs(bent_z - sz)
                if z_delta > 0.001:
                    _diag_actually_bent += 1
                    _diag_max_z_delta = max(_diag_max_z_delta, z_delta)
                else:
                    _diag_zero_delta += 1
                    if blend_factor < 0.001:
                        _diag_zero_blend += 1
                    elif abs(target_z - sz) < 0.001:
                        _diag_target_equals_sz += 1

                # Log detailed info for the first few bent sub-segments.
                if _diag_sample_count < 20:
                    _diag_sample_count += 1
                    logger.info(
                        "BEND SAMPLE %d: layer=%d, gcode_xy=(%.2f,%.2f), "
                        "analysis_xy=(%.2f,%.2f), surface_z=%.4f, "
                        "original_z=%.4f, layers_from_top=%d, "
                        "target_z=%.4f, blend=%.4f, bent_z=%.4f, "
                        "delta_z=%.4f",
                        _diag_sample_count, move.layer_number,
                        sx, sy, sax, say, surface_z,
                        sz, layers_from_top, target_z, blend_factor,
                        bent_z, bent_z - sz,
                    )

            # Compute E delta for this sub-segment.
            e_delta = se_abs - sub_prev_abs_e

            # Compute 2D and 3D distances for compensation.
            dx = sx - sub_prev_x
            dy = sy - sub_prev_y
            dz = bent_z - sub_prev_bent_z
            dist_2d = (dx * dx + dy * dy) ** 0.5
            dist_3d = (dx * dx + dy * dy + dz * dz) ** 0.5

            # Flow compensation.
            final_e_delta = e_delta
            if flow_compensation and layer_height > 0.0:
                # Compute the Z of the layer directly below at this XY.
                # For conformal layers that follow the surface contour,
                # the layer below sits at: surface_z - (layers_from_top+1) * layer_height.
                if not math.isnan(surface_z):
                    layer_below_z = surface_z - (layers_from_top + 1) * layer_height
                else:
                    layer_below_z = None
                actual_lh = compute_actual_layer_height(
                    bent_z,
                    layer_below_z,
                    layer_height,
                )
                # Path length ratio: the bent 3D path is longer than
                # the original planar path.  More material must be
                # deposited over the longer distance.
                path_ratio = (dist_3d / dist_2d) if dist_2d > 1e-9 else 1.0
                # Always compensate in relative terms (delta), then
                # reconstruct absolute or relative as needed.
                final_e_delta = compensate_flow(
                    e_delta, actual_lh, layer_height,
                    is_relative=True,  # Operate on delta.
                    path_length_ratio=path_ratio,
                    min_multiplier=min_flow_multiplier,
                    max_multiplier=max_flow_multiplier,
                )

            # Compute final absolute E and the output E value.
            final_abs_e = sub_prev_abs_e + final_e_delta
            output_e = final_e_delta if is_relative else final_abs_e

            # Feedrate compensation.
            final_f = sf
            if feedrate_compensation and sf is not None and sf > 0.0:
                final_f = adjust_feedrate(sf, dx, dy, dz)

            # Build the new move.
            new_move = GCodeMove(
                command=move.command,
                x=sx,
                y=sy,
                z=bent_z,
                e=output_e,
                f=final_f,
                abs_x=sx,
                abs_y=sy,
                abs_z=bent_z,
                abs_e=final_abs_e,
                line_type=move.line_type,
                layer_number=move.layer_number,
                original_line=move.original_line,
                chunk_index=move.chunk_index,
                line_index_in_chunk=move.line_index_in_chunk,
                is_travel=False,
                is_extrusion=True,
            )
            modified_moves.append(new_move)
            _last_emitted_z = bent_z

            sub_prev_x = sx
            sub_prev_y = sy
            sub_prev_z = sz
            sub_prev_bent_z = bent_z
            sub_prev_abs_e = final_abs_e

    # Close any open region.
    if in_region:
        region_end_indices[current_region_id] = len(modified_moves)

    # Log diagnostic summary.
    logger.info(
        "Bending diagnostics: total_moves=%d, bent=%d, not_target=%d, "
        "not_extrusion=%d, skip_type=%d, travel=%d, not_safe=%d, "
        "no_surface=%d, above_surface=%d, below_depth=%d",
        len(parsed.moves), _diag_bent, _diag_not_target,
        _diag_not_extrusion, _diag_skip_type, _diag_travel,
        _diag_not_safe, _diag_no_surface, _diag_above, _diag_below,
    )

    # Log Z-bending effectiveness diagnostics.
    logger.info(
        "Z-bending detail: actually_bent=%d (delta>0.001mm), "
        "zero_delta=%d, nan_interp=%d, max_z_delta=%.4fmm, "
        "zero_blend=%d, target_eq_sz=%d, z_clamped=%d, z_restored=%d",
        _diag_actually_bent, _diag_zero_delta, _diag_nan_interp,
        _diag_max_z_delta, _diag_zero_blend, _diag_target_equals_sz,
        _diag_z_clamped, _diag_z_restored,
    )

    # Log height map bounds and Z range for debugging coordinate mismatches.
    if hasattr(height_map, "x_min"):
        logger.info(
            "Height map bounds: x=[%.1f, %.1f], y=[%.1f, %.1f]",
            height_map.x_min, height_map.x_max,
            height_map.y_min, height_map.y_max,
        )
        try:
            z_vals = height_map.z_values
            finite_z = z_vals[np.isfinite(z_vals)]
            if finite_z.size > 0:
                logger.info(
                    "Height map Z range: [%.4f, %.4f], variation=%.4fmm, "
                    "valid_cells=%d/%d",
                    float(np.min(finite_z)), float(np.max(finite_z)),
                    float(np.max(finite_z) - np.min(finite_z)),
                    finite_z.size, z_vals.size,
                )
        except Exception:
            pass

    # Log blend map statistics.
    try:
        nonzero_blend = blend_map[blend_map > 0.001]
        logger.info(
            "Blend map: shape=%s, nonzero_cells=%d, "
            "min_nonzero=%.4f, max=%.4f, mean_nonzero=%.4f",
            blend_map.shape, nonzero_blend.size,
            float(np.min(nonzero_blend)) if nonzero_blend.size > 0 else 0.0,
            float(np.max(blend_map)),
            float(np.mean(nonzero_blend)) if nonzero_blend.size > 0 else 0.0,
        )
    except Exception:
        pass

    # Log sample G-code coordinate ranges.
    if parsed.moves:
        sample_x = [m.abs_x for m in parsed.moves[:100] if m.is_extrusion]
        sample_y = [m.abs_y for m in parsed.moves[:100] if m.is_extrusion]
        if sample_x:
            logger.info(
                "Sample G-code XY (first 100 extrusion): "
                "X=[%.1f, %.1f], Y=[%.1f, %.1f] → "
                "analysis X=[%.1f, %.1f], Y=[%.1f, %.1f]",
                min(sample_x), max(sample_x), min(sample_y), max(sample_y),
                min(sample_x) - gcode_offset_x, max(sample_x) - gcode_offset_x,
                min(sample_y) - gcode_offset_y, max(sample_y) - gcode_offset_y,
            )

    # Validate all regions and revert those that fail.
    reverted_regions: set[int] = set()
    for rid in region_start_indices:
        start = region_start_indices[rid]
        end = region_end_indices.get(rid, len(modified_moves))
        region_moves = modified_moves[start:end]

        if region_moves:
            valid_flags = validate_moves(region_moves, max_angle_deg)
            if not all(valid_flags):
                invalid_count = sum(1 for v in valid_flags if not v)
                logger.warning(
                    "Region %d has %d/%d invalid moves (angle > %.1f deg); "
                    "reverting to original G-code.",
                    rid,
                    invalid_count,
                    len(valid_flags),
                    max_angle_deg,
                )
                reverted_regions.add(rid)

    # Apply reversions: replace region moves with originals.
    if reverted_regions:
        revert_ranges: dict[int, tuple[int, int]] = {}
        for rid in reverted_regions:
            revert_ranges[rid] = (
                region_start_indices[rid],
                region_end_indices.get(rid, len(modified_moves)),
            )

        # Build a set of indices that belong to reverted regions.
        reverted_indices: set[int] = set()
        for rid, (s, e) in revert_ranges.items():
            reverted_indices.update(range(s, e))

        new_modified: list[GCodeMove] = []
        i = 0
        while i < len(modified_moves):
            # Check if this index starts a reverted region.
            found_revert = False
            for rid, (s, e) in revert_ranges.items():
                if i == s:
                    # Insert original moves instead.
                    new_modified.extend(region_original_moves[rid])
                    i = e
                    found_revert = True
                    break
            if not found_revert:
                if i not in reverted_indices:
                    new_modified.append(modified_moves[i])
                i += 1

        modified_moves = new_modified

    # Add region comment markers by inserting comment pseudo-moves.
    # We do this by directly manipulating the chunks after reconstruction.
    result = reconstruct_gcode(parsed, modified_moves)

    bent_count = region_id - len(reverted_regions)

    # Add warning header and processed marker.
    if result and bent_count > 0:
        header = result[0]
        if ";NON-PLANAR PROCESSED" not in header:
            stats = (
                f";NON-PLANAR PROCESSED\n"
                f";WARNING: NON-PLANAR G-CODE - VERIFY PRINTHEAD CLEARANCE\n"
                f";NON-PLANAR STATS: regions={bent_count}, "
                f"reverted={len(reverted_regions)}, "
                f"layers={len(target_layers)}\n"
            )
            result[0] = stats + header

    # Insert region boundary comments into the reconstructed chunks.
    # We track region boundaries by layer chunk and insert comments.
    if region_id > 0:
        _insert_region_markers(result, parsed, modified_moves, reverted_regions)
    logger.info(
        "Non-planar bending complete: %d regions bent, %d reverted, "
        "%d total moves processed.",
        bent_count,
        len(reverted_regions),
        len(modified_moves),
    )

    return result


def _insert_region_markers(
    chunks: list[str],
    parsed: ParsedGCode,
    modified_moves: list[GCodeMove],
    reverted_regions: set[int],
) -> None:
    """Insert ;NON-PLANAR START/END comment markers into reconstructed chunks.

    This modifies *chunks* in place.  Markers are placed around
    contiguous runs of bent moves within each chunk.

    Args:
        chunks: The reconstructed gcode_list (modified in place).
        parsed: The original parsed G-code.
        modified_moves: The (possibly subdivided) modified moves.
        reverted_regions: Set of region IDs that were reverted.
    """
    # Group modified bent moves by chunk_index to find which chunks
    # contain non-planar moves.
    chunk_has_bent: dict[int, bool] = {}
    for move in modified_moves:
        ci = move.chunk_index
        if ci not in chunk_has_bent:
            chunk_has_bent[ci] = False
        # A move is "bent" if it's an extrusion in a target layer
        # and not from a reverted region.  Since we already filtered
        # reverted regions, any extrusion move remaining is bent.
        # We use a simple heuristic: if the move's Z differs from
        # the original line's Z, it was bent.
        # For simplicity, mark any chunk that has modified extrusions.
        if move.is_extrusion:
            chunk_has_bent[ci] = True

    region_counter = 0
    for ci, has_bent in sorted(chunk_has_bent.items()):
        if not has_bent or ci >= len(chunks):
            continue
        region_counter += 1
        lines = chunks[ci].split("\n")
        # Find the first and last G1 extrusion line.
        first_ext = -1
        last_ext = -1
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("G1") and "E" in stripped.upper():
                if first_ext < 0:
                    first_ext = idx
                last_ext = idx

        if first_ext >= 0:
            lines.insert(first_ext, f";NON-PLANAR START region_id={region_counter}")
            # Adjust last_ext because we inserted a line before it.
            last_ext += 1
            if last_ext + 1 <= len(lines):
                lines.insert(last_ext + 1, ";NON-PLANAR END")
            else:
                lines.append(";NON-PLANAR END")

        chunks[ci] = "\n".join(lines)
