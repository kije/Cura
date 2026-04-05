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

    _diag_z_restored = 0  # Count of moves where Z-restore was injected (post-reversion pass).

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
    # Track which chunks had actual Z-bending (for region markers).
    _chunks_with_bending: set[int] = set()

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
            # Check BOTH start and endpoint: if either is safe, bend the
            # move.  This prevents paths from being fragmented when they
            # cross the safe_map boundary — individual moves whose
            # endpoint happens to land just outside the safe region would
            # otherwise stay flat while adjacent moves are bent, creating
            # isolated spikes instead of smooth contours.
            endpoint_safe = _lookup_safe(safe_map, height_map, ax, ay)
            start_safe = _lookup_safe(
                safe_map, height_map,
                prev_x - gcode_offset_x,
                prev_y - gcode_offset_y,
            ) if move_idx > 0 else False
            is_safe = endpoint_safe or start_safe
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
            elif move.abs_z > surface_z_check + 2 * layer_height:
                # Move is above the surface — don't bend.
                # Use 2x layer_height margin to prevent path fragmentation
                # at the surface boundary (adjacent moves can differ by
                # a fraction of layer_height in their local surface_z).
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

            # Ensure explicit Z on all moves in target layers to prevent
            # stale Z from preceding bent regions.  CuraEngine omits Z
            # from most G1 moves (only emitting it at layer changes).
            # After bending modifies Z for some moves, any subsequent
            # move without explicit Z inherits the stale bent Z value,
            # causing the nozzle to stay at the wrong height.  Setting
            # z=abs_z on all target-layer moves eliminates this class of
            # bugs entirely.  The file size increases slightly but
            # correctness is guaranteed.
            if move.layer_number in target_layers and move.z is None:
                move = GCodeMove(
                    command=move.command,
                    x=move.x,
                    y=move.y,
                    z=move.abs_z,
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

            modified_moves.append(move)
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
        # In "top_only" mode, this is a fixed value based on the layer
        # number and doesn't change per sub-segment.
        # In "all_surfaces" mode, it MUST be computed per sub-segment
        # because the surface height varies spatially — using a single
        # endpoint value for all sub-segments causes Z discontinuities
        # on moves that cross areas with varying surface height.
        if not all_surfaces_mode:
            layers_from_top = float(max_layer - move.layer_number)
        else:
            layers_from_top = 0.0  # Will be overridden per sub-segment.

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
                # In all_surfaces mode, compute layers_from_top per
                # sub-segment using the LOCAL surface_z at this XY.
                # Using floating-point values (not rounded integers)
                # produces smoother Z transitions across moves that
                # straddle layer boundaries.
                if all_surfaces_mode:
                    layers_from_top = max(0.0, (surface_z - sz) / layer_height)

                # Compute target Z: surface minus layers_from_top offsets.
                target_z = surface_z - layers_from_top * layer_height

                # Look up blend factor.
                blend_factor = _lookup_blend_factor(blend_map, height_map, sax, say)

                # Linearly interpolate between original Z and target Z.
                bent_z = sz + (target_z - sz) * blend_factor

                # Clamp bent_z so the actual layer height (distance from
                # the conformal layer below) doesn't exceed a safe maximum.
                # Without this, the nozzle can be multiple layer-heights
                # above the material below, printing into empty air.
                #
                # The conformal layer below sits at:
                #   layer_below_z = surface_z - (layers_from_top + 1) * layer_height
                # The actual layer height is: bent_z - layer_below_z
                # We clamp to: layer_height + max_path_deviation
                layer_below_z = surface_z - (layers_from_top + 1) * layer_height
                max_actual_lh = layer_height + max_path_deviation
                max_bent_z = layer_below_z + max_actual_lh
                if bent_z > max_bent_z:
                    bent_z = max_bent_z
                    _diag_z_clamped += 1

                # Safety: don't go below zero (bed surface).
                if bent_z < 0.0:
                    bent_z = max(0.05, sz)
                    _diag_z_clamped += 1

                # Track bending statistics.
                z_delta = abs(bent_z - sz)
                if z_delta > 0.001:
                    _diag_actually_bent += 1
                    _diag_max_z_delta = max(_diag_max_z_delta, z_delta)
                    _chunks_with_bending.add(move.chunk_index)
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

    # POST-REVERSION Z-RESTORE PASS
    #
    # CuraEngine G-code typically omits Z from most G1 moves (Z is only
    # emitted when it changes, e.g., at layer boundaries).  After bending,
    # bent moves have explicit Z values, but un-bent moves following them
    # don't -- causing the printer to stay at the last bent Z.
    #
    # This pass runs AFTER reversion so we see the final move sequence.
    # It injects explicit Z coordinates on un-bent moves that follow bent
    # moves to restore the printer to the correct layer height.
    last_emitted_z: float | None = None
    z_restored_moves: list[GCodeMove] = []
    for move in modified_moves:
        if move.z is not None:
            # This move has an explicit Z -- track it.
            last_emitted_z = move.z
            z_restored_moves.append(move)
        elif (last_emitted_z is not None
              and abs(last_emitted_z - move.abs_z) > 0.001):
            # This move lacks Z, but the printer is at the wrong height.
            # Inject an explicit Z to restore the correct height.
            restored_move = GCodeMove(
                command=move.command,
                x=move.x,
                y=move.y,
                z=move.abs_z,
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
            z_restored_moves.append(restored_move)
            last_emitted_z = move.abs_z
            _diag_z_restored += 1
        else:
            z_restored_moves.append(move)

    modified_moves = z_restored_moves

    # COLLINEAR SEGMENT MERGE PASS
    #
    # Subdivision splits each original move into short sub-segments
    # (~1mm).  After bending, many adjacent sub-segments end up on a
    # straight line (same direction, same Z) because the bending
    # produced no curvature at that location.  These can be merged
    # back into a single longer segment, significantly reducing G-code
    # size without changing the toolpath geometry.
    #
    # Two adjacent G1 moves are merged if:
    #   - Same command (G1), both extrusions or both travels
    #   - Same chunk_index and line_index_in_chunk (from same original)
    #   - Collinear in 3D: the direction vector is parallel (within
    #     tolerance) to the previous segment's direction
    #   - Same feedrate (F)
    modified_moves = _merge_collinear_segments(modified_moves, is_relative)

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

    # Reorder infill-first in non-planar layers when using skin_walls mode.
    # This ensures flat infill is laid down before bent walls/skin, providing
    # a stable foundation.
    if nonplanar_line_types == "skin_walls" and _chunks_with_bending:
        _reorder_infill_first(result, _chunks_with_bending, is_relative)

    # Insert region boundary comments into the reconstructed chunks.
    # Only mark chunks that had actual Z-bending (z_delta > 0.001).
    if _chunks_with_bending:
        _insert_region_markers(result, _chunks_with_bending)

    # Final safety validation of the output G-code.
    _validate_output_gcode(result, max_angle_deg)

    logger.info(
        "Non-planar bending complete: %d regions bent, %d reverted, "
        "%d total moves processed.",
        bent_count,
        len(reverted_regions),
        len(modified_moves),
    )

    return result


def _validate_output_gcode(
    gcode_list: list[str],
    max_angle_deg: float,
) -> None:
    """Validate the final output G-code for safety issues.

    Scans the modified G-code for problems that could cause physical
    damage or failed prints.  Issues are logged as warnings but do NOT
    modify the output — the caller is responsible for deciding what to
    do (typically: the earlier pipeline stages should have prevented
    these issues, so warnings here indicate bugs).

    Checks:
      - No Z < 0 on any move (bed collision)
      - No consecutive extrusion moves with nozzle angle > max_angle_deg
      - E values are monotonically increasing (absolute mode detection)

    Args:
        gcode_list: The final reconstructed G-code list.
        max_angle_deg: Maximum allowed nozzle angle.
    """
    import re as _re
    param_re = {
        "X": _re.compile(r"X([-+]?\d*\.?\d+)", _re.IGNORECASE),
        "Y": _re.compile(r"Y([-+]?\d*\.?\d+)", _re.IGNORECASE),
        "Z": _re.compile(r"Z([-+]?\d*\.?\d+)", _re.IGNORECASE),
        "E": _re.compile(r"E([-+]?\d*\.?\d+)", _re.IGNORECASE),
    }

    tan_max = math.tan(math.radians(min(max_angle_deg, 89.9)))
    z_below_zero = 0
    angle_violations = 0
    e_backwards = 0

    prev_x, prev_y, prev_z = 0.0, 0.0, 0.0
    prev_e = 0.0
    abs_x, abs_y, abs_z = 0.0, 0.0, 0.0
    is_relative_e = False
    prev_was_extrusion = False

    for chunk in gcode_list:
        for raw_line in chunk.split("\n"):
            line = raw_line.strip()
            if not line:
                continue

            code_part = line.split(";")[0].strip() if ";" in line and not line.startswith(";") else line
            upper = code_part.upper()

            if upper.startswith("M83"):
                is_relative_e = True
                continue
            if upper.startswith("M82"):
                is_relative_e = False
                continue

            if not (upper.startswith("G0") or upper.startswith("G1")):
                continue

            # Extract parameters.
            def _p(key):
                m = param_re[key].search(code_part)
                return float(m.group(1)) if m else None

            px, py, pz, pe = _p("X"), _p("Y"), _p("Z"), _p("E")

            if px is not None:
                abs_x = px
            if py is not None:
                abs_y = py
            if pz is not None:
                abs_z = pz

            has_extrusion = False
            if pe is not None:
                if is_relative_e:
                    has_extrusion = pe > 0.0
                    prev_e += pe
                else:
                    has_extrusion = pe > prev_e
                    # Check for backwards E (absolute mode).
                    if pe < prev_e - 0.001:
                        e_backwards += 1
                    prev_e = pe

            # Check Z < 0.
            if abs_z < -0.001:
                z_below_zero += 1

            # Check nozzle angle (extrusion moves only).
            is_extrusion = has_extrusion and not upper.startswith("G0")
            if is_extrusion and prev_was_extrusion:
                dx = abs_x - prev_x
                dy = abs_y - prev_y
                dz = abs(abs_z - prev_z)
                planar = math.sqrt(dx * dx + dy * dy)
                if planar > 0.001 and dz / planar > tan_max + 0.01:
                    angle_violations += 1

            if is_extrusion:
                prev_x, prev_y, prev_z = abs_x, abs_y, abs_z
                prev_was_extrusion = True
            else:
                prev_was_extrusion = False

    # Log results.
    if z_below_zero > 0:
        logger.warning(
            "OUTPUT VALIDATION: %d moves with Z < 0 (potential bed collision)",
            z_below_zero,
        )
    if angle_violations > 0:
        logger.warning(
            "OUTPUT VALIDATION: %d consecutive extrusion moves exceed "
            "max nozzle angle (%.1f deg)",
            angle_violations, max_angle_deg,
        )
    if e_backwards > 0:
        logger.warning(
            "OUTPUT VALIDATION: %d moves with decreasing absolute E "
            "(potential extrusion discontinuity)",
            e_backwards,
        )
    if z_below_zero == 0 and angle_violations == 0 and e_backwards == 0:
        logger.info("OUTPUT VALIDATION: all checks passed")


def _merge_collinear_segments(
    moves: list[GCodeMove],
    is_relative_extrusion: bool = False,
) -> list[GCodeMove]:
    """Merge adjacent collinear G1 sub-segments back into single moves.

    During bending, original moves are subdivided into short (~1mm)
    segments for curvature accuracy.  Many of these sub-segments end
    up on the same straight line after bending (e.g. flat regions).
    This pass detects such runs and collapses them into one move,
    significantly reducing G-code size without altering geometry.

    Two adjacent moves are mergeable when:
      - Both are G1 (not G0)
      - Same chunk_index and line_index_in_chunk (from same original)
      - Both extrusions or both non-extrusions
      - Same feedrate (F), or second has no F (inherits first's)
      - 3D direction vectors are parallel (cross product magnitude < tol)

    Args:
        moves: The list of moves to merge.
        is_relative_extrusion: True if M83 (relative E mode).  In
            relative mode, E values are per-move deltas that must be
            summed when merging.  In absolute mode, the merged move
            keeps the final move's E value (the target position).

    Returns:
        A new list with collinear runs collapsed.
    """
    import math

    if len(moves) < 2:
        return moves

    CROSS_TOL = 1e-3  # cross-product magnitude tolerance for collinearity
    F_TOL = 0.5       # feedrate tolerance (integer F in G-code)

    merged: list[GCodeMove] = [moves[0]]
    merge_count = 0

    for i in range(1, len(moves)):
        cur = moves[i]
        prev = merged[-1]

        # Only merge G1 moves from the same original line.
        if (
            cur.command != "G1"
            or prev.command != "G1"
            or cur.chunk_index != prev.chunk_index
            or cur.line_index_in_chunk != prev.line_index_in_chunk
            or cur.is_extrusion != prev.is_extrusion
            or cur.is_travel != prev.is_travel
        ):
            merged.append(cur)
            continue

        # Feedrate must match (or cur inherits via None).
        if cur.f is not None and prev.f is not None:
            if abs(cur.f - prev.f) > F_TOL:
                merged.append(cur)
                continue

        # We need the move before prev to compute prev's direction.
        # prev's direction = (prev.abs_x - start_x, ...) where start is
        # the position before prev.  But we don't store prev's start
        # directly.  Instead, compute from the move before prev in
        # the merged list — that's merged[-2] if it exists and shares
        # the same original line.
        if len(merged) < 2:
            merged.append(cur)
            continue

        before_prev = merged[-2]
        # The start position of prev is the abs position of before_prev
        # (if they share the same original), OR the original abs position
        # if before_prev is from a different original line.
        if (
            before_prev.chunk_index == prev.chunk_index
            and before_prev.line_index_in_chunk == prev.line_index_in_chunk
        ):
            start_x, start_y, start_z = before_prev.abs_x, before_prev.abs_y, before_prev.abs_z
        else:
            # prev is the first sub-segment of its original — no prior
            # segment to compare direction with.
            merged.append(cur)
            continue

        # Direction of prev segment: start → prev.abs
        dx1 = prev.abs_x - start_x
        dy1 = prev.abs_y - start_y
        dz1 = prev.abs_z - start_z

        # Direction of cur segment: prev.abs → cur.abs
        dx2 = cur.abs_x - prev.abs_x
        dy2 = cur.abs_y - prev.abs_y
        dz2 = cur.abs_z - prev.abs_z

        # Cross product magnitude for collinearity check.
        cx = dy1 * dz2 - dz1 * dy2
        cy = dz1 * dx2 - dx1 * dz2
        cz = dx1 * dy2 - dy1 * dx2
        cross_mag = math.sqrt(cx * cx + cy * cy + cz * cz)

        # Also check that segments point the same way (dot > 0) to
        # avoid merging segments that reverse direction.
        dot = dx1 * dx2 + dy1 * dy2 + dz1 * dz2

        if cross_mag > CROSS_TOL or dot < 0:
            merged.append(cur)
            continue

        # Collinear — merge cur into prev by extending prev to cur's endpoint.
        # Handle E based on extrusion mode:
        #   - Relative (M83): e values are per-move deltas → sum them
        #   - Absolute (M82): e values are target positions → keep final
        new_abs_e = cur.abs_e
        if is_relative_extrusion:
            # Relative mode: accumulate deltas.
            if cur.e is not None and prev.e is not None:
                new_e = prev.e + cur.e
            elif cur.e is not None:
                new_e = cur.e
            else:
                new_e = prev.e
        else:
            # Absolute mode: keep the final absolute E position.
            if cur.e is not None:
                new_e = cur.e
            else:
                new_e = prev.e

        merged_move = GCodeMove(
            command="G1",
            x=cur.x if cur.x is not None else prev.x,
            y=cur.y if cur.y is not None else prev.y,
            z=cur.z if cur.z is not None else prev.z,
            e=new_e,
            f=prev.f if prev.f is not None else cur.f,
            abs_x=cur.abs_x,
            abs_y=cur.abs_y,
            abs_z=cur.abs_z,
            abs_e=new_abs_e,
            line_type=cur.line_type,
            layer_number=cur.layer_number,
            original_line=cur.original_line,
            chunk_index=cur.chunk_index,
            line_index_in_chunk=cur.line_index_in_chunk,
            is_travel=cur.is_travel,
            is_extrusion=cur.is_extrusion,
        )
        merged[-1] = merged_move
        merge_count += 1

    if merge_count > 0:
        logger.info(
            "Collinear merge: removed %d redundant sub-segments (%.1f%% reduction).",
            merge_count,
            100.0 * merge_count / len(moves),
        )

    return merged


def _insert_region_markers(
    chunks: list[str],
    bent_chunks: set[int],
) -> None:
    """Insert ;NON-PLANAR START/END comment markers into reconstructed chunks.

    This modifies *chunks* in place.  Markers are placed around
    contiguous runs of bent moves within each chunk.

    Args:
        chunks: The reconstructed gcode_list (modified in place).
        bent_chunks: Set of chunk indices that had actual Z-bending
            (z_delta > 0.001mm from original).
    """
    region_counter = 0
    for ci in sorted(bent_chunks):
        if ci >= len(chunks):
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


def _reorder_infill_first(
    chunks: list[str],
    bent_chunks: set[int],
    is_relative_extrusion: bool,
) -> None:
    """Reorder non-planar layer chunks so FILL moves print before walls/skin.

    In "skin_walls" mode, infill stays flat while walls/skin are bent.
    Printing infill first ensures a flat foundation exists under the bent
    paths.  Without reordering, walls might be bent BEFORE infill is
    laid down, leaving them unsupported.

    This modifies *chunks* in place.  Only chunks that had actual
    Z-bending are reordered.

    The function splits each target layer chunk into "type groups"
    (sections between ;TYPE: comments), moves FILL groups before
    non-FILL groups, and fixes absolute E values if needed.

    Args:
        chunks: The reconstructed gcode_list (modified in place).
        bent_chunks: Set of chunk indices that had actual Z-bending.
        is_relative_extrusion: True if M83 mode (relative E).
    """
    import re
    type_re = re.compile(r"^;TYPE:(\S+)", re.IGNORECASE)
    e_re = re.compile(r"E([-+]?\d*\.?\d+)", re.IGNORECASE)

    for ci in sorted(bent_chunks):
        if ci >= len(chunks):
            continue

        lines = chunks[ci].split("\n")

        # Split lines into type groups.
        # A type group starts with a ;TYPE: comment and includes all
        # subsequent lines until the next ;TYPE: or end of chunk.
        # Lines before the first ;TYPE: are a "preamble" (layer change,
        # initial travel, etc.) and are always kept first.
        groups: list[tuple[str, list[str]]] = []  # (type_name, lines)
        preamble: list[str] = []
        current_type = ""
        current_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            type_m = type_re.match(stripped)
            if type_m:
                # Save the previous group if it had content.
                if current_type or current_lines:
                    if current_type:
                        groups.append((current_type, current_lines))
                    else:
                        preamble.extend(current_lines)
                current_type = type_m.group(1).upper()
                current_lines = [line]
            else:
                current_lines.append(line)

        # Save the last group.
        if current_type:
            groups.append((current_type, current_lines))
        elif current_lines:
            preamble.extend(current_lines)

        if not groups:
            continue  # No type groups found — skip.

        # Separate FILL groups from non-FILL groups.
        fill_groups = [(t, ll) for t, ll in groups if t == "FILL"]
        other_groups = [(t, ll) for t, ll in groups if t != "FILL"]

        if not fill_groups or not other_groups:
            continue  # Nothing to reorder.

        # Check if FILL is already first — skip if so.
        first_non_preamble_type = groups[0][0] if groups else ""
        if first_non_preamble_type == "FILL":
            continue  # Already in correct order.

        # For absolute extrusion, extract per-move E deltas from the
        # ORIGINAL order BEFORE reordering.  These deltas represent
        # the material each move deposits, which doesn't change when
        # the moves are reordered.
        original_e_deltas: list[float] = []
        if not is_relative_extrusion:
            original_e_deltas = _extract_e_deltas(lines, e_re)

        # Reconstruct: preamble + FILL groups + other groups.
        reordered_lines = list(preamble)
        for _, group_lines in fill_groups:
            reordered_lines.extend(group_lines)
        for _, group_lines in other_groups:
            reordered_lines.extend(group_lines)

        # For absolute extrusion, renumber E values using the
        # original deltas applied in the new line order.
        if not is_relative_extrusion:
            _fix_absolute_e_values(reordered_lines, e_re, original_e_deltas)

        chunks[ci] = "\n".join(reordered_lines)
        logger.debug("Reordered chunk %d: FILL groups moved before walls/skin", ci)


def _extract_e_deltas(lines: list[str], e_re) -> list[tuple[int, float, float]]:
    """Extract per-move E deltas from G-code lines in their current order.

    Each G0/G1 line with an E parameter contributes one entry:
    ``(line_index, e_delta, absolute_e)``.  ``e_delta`` is the
    difference from the previous E value, representing the material
    this individual move deposits.

    Args:
        lines: G-code lines to scan.
        e_re: Compiled regex for extracting E values.

    Returns:
        List of (line_index, delta, abs_e) tuples.
    """
    prev_e = 0.0
    result: list[tuple[int, float, float]] = []
    first = True
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("G0") or stripped.startswith("G1")):
            continue
        m = e_re.search(stripped)
        if m:
            e_val = float(m.group(1))
            if first:
                # Store the base E (the E at chunk entry) as first entry's
                # implicit predecessor.
                prev_e = e_val
                result.append((idx, 0.0, e_val))
                first = False
            else:
                result.append((idx, e_val - prev_e, e_val))
                prev_e = e_val
    return result


def _fix_absolute_e_values(
    lines: list[str],
    e_re,
    original_e_info: list[tuple[int, float, float]],
) -> None:
    """Renumber absolute E values after reordering lines.

    After line reordering, each G0/G1 line still carries its old absolute
    E value from the original order.  We need to reassign monotonically
    increasing E values that preserve each move's per-move delta
    (material deposited).

    The approach:
    1. Tag each G0/G1+E line in the reordered output with its original
       absolute E value (which is still embedded in the line text).
    2. Look up that original E value in the pre-reorder E info to find
       the per-move delta for that specific line.
    3. Re-accumulate from the chunk's starting E using each line's
       delta in the new order.

    Args:
        lines: Reordered G-code lines (modified in place).
        e_re: Compiled regex for extracting E values.
        original_e_info: Output of ``_extract_e_deltas`` from the
            ORIGINAL line order: (line_idx, delta, abs_e) tuples.
    """
    if not original_e_info:
        return

    # Build a mapping from original absolute E value → per-move delta.
    # Since E values are unique per move (monotonically increasing),
    # this maps each line's embedded E to its extrusion amount.
    # Use a tolerance-based lookup to handle float formatting.
    abs_e_to_delta: dict[str, float] = {}
    for _, delta, abs_e in original_e_info:
        # Key on the formatted string to match exactly what's in the line.
        key = f"{abs_e:.5f}"
        abs_e_to_delta[key] = delta

    # Determine the starting E for this chunk (E before the first move).
    # The first entry in original_e_info has delta=0.0 and abs_e = the
    # first E value, which IS the starting position.
    base_e = original_e_info[0][2]

    # Scan reordered lines, look up each line's delta, and reassign
    # cumulative E values.
    cumulative_e = base_e
    first_move = True
    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("G0") or stripped.startswith("G1")):
            continue
        m = e_re.search(stripped)
        if not m:
            continue

        old_e_val = float(m.group(1))
        old_e_key = f"{old_e_val:.5f}"

        # Look up this line's delta from the original order.
        delta = abs_e_to_delta.get(old_e_key, 0.0)

        if first_move:
            # First move after reordering: starts at base_e + its delta.
            cumulative_e = base_e + delta
            first_move = False
        else:
            cumulative_e += delta

        # Replace the E value in the line.
        old_e_str = m.group(0)  # "E1234.56789"
        new_e_str = f"E{cumulative_e:.5f}"
        lines[line_idx] = line.replace(old_e_str, new_e_str, 1)
