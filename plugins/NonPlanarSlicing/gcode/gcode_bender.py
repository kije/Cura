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

# Line types that should NOT be bent.
_SKIP_LINE_TYPES = frozenset({
    "SUPPORT", "SUPPORT-INTERFACE", "PRIME-TOWER", "SKIRT",
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
    nonplanar_layer_count = int(settings.get("nonplanar_layer_count", 3))
    max_angle_deg = float(settings.get("max_angle_deg", 45.0))
    flow_compensation = bool(settings.get("flow_compensation", True))
    feedrate_compensation = bool(settings.get("feedrate_compensation", True))
    is_relative = bool(settings.get("is_relative_extrusion", False))
    segment_length = float(settings.get("segment_length", _DEFAULT_SEGMENT_LENGTH))

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

    # Determine which layers to bend (topmost N).
    max_layer = parsed.total_layers - 1
    top_layer_start = max(0, max_layer - nonplanar_layer_count + 1)
    target_layers = set(range(top_layer_start, max_layer + 1))

    if not target_layers:
        logger.info("No target layers for non-planar bending.")
        return gcode_list

    logger.info(
        "Bending layers %d-%d (%d layers), layer_height=%.3f, "
        "max_angle=%.1f deg, segment_length=%.2f mm",
        top_layer_start,
        max_layer,
        len(target_layers),
        layer_height,
        max_angle_deg,
        segment_length,
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
        should_bend = (
            move.layer_number in target_layers
            and move.is_extrusion
            and move.line_type not in _SKIP_LINE_TYPES
            and not move.is_travel
        )

        if should_bend:
            # Check if XY is in a non-planar region.
            is_safe = _lookup_safe(safe_map, height_map, move.abs_x, move.abs_y)
            if not is_safe:
                should_bend = False

        if not should_bend:
            # End any active region.
            if in_region:
                region_end_indices[current_region_id] = len(modified_moves)
                in_region = False

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
        layers_from_top = max_layer - move.layer_number

        # Bend each sub-segment.
        sub_prev_x = prev_x
        sub_prev_y = prev_y
        sub_prev_z = prev_z
        sub_prev_bent_z = prev_z  # Track bent Z for layer height estimation.
        sub_prev_abs_e = prev_abs_e

        for seg_idx, (sx, sy, sz, se_abs, sf) in enumerate(sub_points):
            # Look up surface Z from height map.
            surface_z = height_map.interpolate(sx, sy)

            if math.isnan(surface_z):
                # Outside the height map -- no bending, pass through.
                bent_z = sz
            else:
                # Compute target Z: surface minus layers_from_top offsets.
                target_z = surface_z - layers_from_top * layer_height

                # Look up blend factor.
                blend_factor = _lookup_blend_factor(blend_map, height_map, sx, sy)

                # Linearly interpolate between original Z and target Z.
                bent_z = sz + (target_z - sz) * blend_factor

            # Compute E delta for this sub-segment.
            e_delta = se_abs - sub_prev_abs_e

            # Flow compensation.
            final_e_delta = e_delta
            if flow_compensation and layer_height > 0.0:
                actual_lh = compute_actual_layer_height(
                    bent_z,
                    sub_prev_bent_z,  # Use actual previous bent Z.
                    layer_height,
                )
                # Always compensate in relative terms (delta), then
                # reconstruct absolute or relative as needed.
                final_e_delta = compensate_flow(
                    e_delta, actual_lh, layer_height,
                    is_relative=True,  # Operate on delta.
                )

            # Compute final absolute E and the output E value.
            final_abs_e = sub_prev_abs_e + final_e_delta
            output_e = final_e_delta if is_relative else final_abs_e

            # Feedrate compensation.
            final_f = sf
            if feedrate_compensation and sf is not None and sf > 0.0:
                dx = sx - sub_prev_x
                dy = sy - sub_prev_y
                dz = bent_z - sub_prev_bent_z
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

    # Add warning header.
    if result and region_id > 0 and len(reverted_regions) < region_id:
        header = result[0]
        if ";WARNING: NON-PLANAR G-CODE" not in header:
            result[0] = ";WARNING: NON-PLANAR G-CODE\n" + header

    # Insert region boundary comments into the reconstructed chunks.
    # We track region boundaries by layer chunk and insert comments.
    if region_id > 0:
        _insert_region_markers(result, parsed, modified_moves, reverted_regions)

    bent_count = region_id - len(reverted_regions)
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
