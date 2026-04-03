"""G-code parser for Cura non-planar slicing.

Parses Cura-formatted G-code into a structured representation suitable for
non-planar Z-bending transformations, and reconstructs modified G-code from
the structured form.

The Cura G-code list format is:
  gcode_list[0] = header comments
  gcode_list[1] = start G-code (machine init)
  gcode_list[2:] = layer chunks, each starting with ;LAYER:N

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Known line types from Cura ;TYPE: comments.
_KNOWN_LINE_TYPES = frozenset({
    "WALL-OUTER", "WALL-INNER", "SKIN", "FILL",
    "SUPPORT", "SUPPORT-INTERFACE", "PRIME-TOWER", "SKIRT",
})

# Regex for parsing G0/G1 commands.  Handles both "G0 X10 Y20" and
# "G0X10Y20" (no space after command) formats.
_MOVE_RE = re.compile(
    r"^(G[01])\s*"
    r"(?:.*?X([-+]?\d*\.?\d+))?"
    r"(?:.*?Y([-+]?\d*\.?\d+))?"
    r"(?:.*?Z([-+]?\d*\.?\d+))?"
    r"(?:.*?E([-+]?\d*\.?\d+))?"
    r"(?:.*?F([-+]?\d*\.?\d+))?",
    re.IGNORECASE,
)

# More robust per-parameter extraction for cases where parameters appear in
# any order (the single regex above assumes X before Y before Z etc.).
_PARAM_RE = {
    "X": re.compile(r"X([-+]?\d*\.?\d+)", re.IGNORECASE),
    "Y": re.compile(r"Y([-+]?\d*\.?\d+)", re.IGNORECASE),
    "Z": re.compile(r"Z([-+]?\d*\.?\d+)", re.IGNORECASE),
    "E": re.compile(r"E([-+]?\d*\.?\d+)", re.IGNORECASE),
    "F": re.compile(r"F([-+]?\d*\.?\d+)", re.IGNORECASE),
}

_LAYER_RE = re.compile(r"^;LAYER:(-?\d+)", re.IGNORECASE)
_TYPE_RE = re.compile(r"^;TYPE:(\S+)", re.IGNORECASE)
_LAYER_HEIGHT_RE = re.compile(r"^;Layer height:\s*([\d.]+)", re.IGNORECASE)
_COMMAND_RE = re.compile(r"^(G[01])(?:\s|[XYZEF])", re.IGNORECASE)


@dataclass
class GCodeMove:
    """A single parsed G0/G1 move with full positional state.

    Attributes:
        command: The G-code command, "G0" or "G1".
        x, y, z, e, f: Parameter values parsed from this specific line,
            or None if not present on this line.
        abs_x, abs_y, abs_z, abs_e: Absolute machine positions AFTER
            this move has been applied.  These track cumulative state.
        line_type: The current line type from the most recent ;TYPE:
            comment (e.g. "WALL-OUTER", "FILL", "UNKNOWN").
        layer_number: Current layer from the most recent ;LAYER:N comment.
        original_line: The raw G-code line exactly as it appeared.
        chunk_index: Index into the gcode_list for this line's chunk.
        line_index_in_chunk: Zero-based line number within the chunk.
        is_travel: True if this is a travel move (G0, or G1 with no
            extrusion change).
        is_extrusion: True if this move extrudes material (E increases
            in absolute mode, or any E value in relative mode).
    """

    command: str
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    e: Optional[float] = None
    f: Optional[float] = None
    abs_x: float = 0.0
    abs_y: float = 0.0
    abs_z: float = 0.0
    abs_e: float = 0.0
    line_type: str = "UNKNOWN"
    layer_number: int = -1
    original_line: str = ""
    chunk_index: int = 0
    line_index_in_chunk: int = 0
    is_travel: bool = True
    is_extrusion: bool = False


@dataclass
class ParsedGCode:
    """Structured representation of parsed Cura G-code.

    Attributes:
        moves: All parsed G0/G1 moves in order.
        total_layers: Total number of layers detected.
        layer_height: Nominal layer height detected from comments or Z
            differences, or 0.0 if not determinable.
        is_relative_extrusion: True if M83 (relative E) was detected
            more recently than M82 (absolute E).
        chunks: The original gcode_list for reconstruction.
    """

    moves: list[GCodeMove] = field(default_factory=list)
    total_layers: int = 0
    layer_height: float = 0.0
    is_relative_extrusion: bool = False
    chunks: list[str] = field(default_factory=list)


def _extract_param(line: str, param: str) -> Optional[float]:
    """Extract a single parameter value from a G-code line.

    Args:
        line: The G-code line (comment portion stripped).
        param: The parameter letter ("X", "Y", "Z", "E", or "F").

    Returns:
        The float value if the parameter is present, else None.
    """
    m = _PARAM_RE[param].search(line)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_gcode(gcode_list: list[str]) -> ParsedGCode:
    """Parse a Cura-format G-code list into a structured representation.

    Args:
        gcode_list: List of G-code chunk strings as provided by Cura.
            Index 0 is the header, index 1 is start G-code, and
            indices 2+ are layer chunks.

    Returns:
        A ParsedGCode instance containing all parsed moves and metadata.
    """
    if not gcode_list:
        logger.warning("Empty gcode_list provided to parse_gcode.")
        return ParsedGCode(chunks=[])

    result = ParsedGCode(chunks=list(gcode_list))

    # State tracking.
    abs_x = 0.0
    abs_y = 0.0
    abs_z = 0.0
    abs_e = 0.0
    current_layer = -1
    current_type = "UNKNOWN"
    is_relative_e = False
    layer_height_from_comment = 0.0
    layer_numbers_seen: set[int] = set()
    first_layer_z: Optional[float] = None
    second_layer_z: Optional[float] = None

    for chunk_idx, chunk in enumerate(gcode_list):
        lines = chunk.split("\n")
        for line_idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue

            # Strip inline comments for command parsing, but keep
            # the original line intact.
            code_part = line.split(";")[0].strip() if ";" in line and not line.startswith(";") else line

            # Check for metadata comments.
            if line.startswith(";"):
                layer_m = _LAYER_RE.match(line)
                if layer_m:
                    current_layer = int(layer_m.group(1))
                    layer_numbers_seen.add(current_layer)
                    continue

                type_m = _TYPE_RE.match(line)
                if type_m:
                    raw_type = type_m.group(1).upper()
                    current_type = raw_type if raw_type in _KNOWN_LINE_TYPES else "UNKNOWN"
                    continue

                height_m = _LAYER_HEIGHT_RE.match(line)
                if height_m:
                    try:
                        layer_height_from_comment = float(height_m.group(1))
                    except ValueError:
                        pass
                    continue

                continue

            # Detect extrusion mode commands.
            upper_code = code_part.upper()
            if upper_code.startswith("M83"):
                is_relative_e = True
                continue
            if upper_code.startswith("M82"):
                is_relative_e = False
                continue

            # Only parse G0/G1 moves.
            if not (upper_code.startswith("G0") or upper_code.startswith("G1")):
                continue

            # Determine command.
            if upper_code.startswith("G0"):
                command = "G0"
            else:
                command = "G1"

            # Extract parameters using robust per-parameter regex.
            param_x = _extract_param(code_part, "X")
            param_y = _extract_param(code_part, "Y")
            param_z = _extract_param(code_part, "Z")
            param_e = _extract_param(code_part, "E")
            param_f = _extract_param(code_part, "F")

            # Update absolute position state.
            prev_e = abs_e

            if param_x is not None:
                abs_x = param_x
            if param_y is not None:
                abs_y = param_y
            if param_z is not None:
                abs_z = param_z

            if param_e is not None:
                if is_relative_e:
                    abs_e += param_e
                else:
                    abs_e = param_e

            # Track Z values for layer height detection.
            if param_z is not None and current_layer >= 0:
                if first_layer_z is None:
                    first_layer_z = param_z
                elif second_layer_z is None and param_z != first_layer_z:
                    second_layer_z = param_z

            # Determine travel vs extrusion.
            if is_relative_e:
                has_extrusion = param_e is not None and param_e > 0.0
            else:
                has_extrusion = param_e is not None and param_e > prev_e

            is_travel = command == "G0" or not has_extrusion

            move = GCodeMove(
                command=command,
                x=param_x,
                y=param_y,
                z=param_z,
                e=param_e,
                f=param_f,
                abs_x=abs_x,
                abs_y=abs_y,
                abs_z=abs_z,
                abs_e=abs_e,
                line_type=current_type,
                layer_number=current_layer,
                original_line=raw_line,
                chunk_index=chunk_idx,
                line_index_in_chunk=line_idx,
                is_travel=is_travel,
                is_extrusion=has_extrusion,
            )
            result.moves.append(move)

    # Finalize metadata.
    result.is_relative_extrusion = is_relative_e

    if layer_numbers_seen:
        result.total_layers = max(layer_numbers_seen) + 1
    else:
        result.total_layers = 0

    # Determine layer height: prefer comment, fall back to Z difference.
    if layer_height_from_comment > 0.0:
        result.layer_height = layer_height_from_comment
    elif first_layer_z is not None and second_layer_z is not None:
        result.layer_height = abs(second_layer_z - first_layer_z)
    else:
        result.layer_height = 0.0

    logger.info(
        "Parsed G-code: %d moves, %d layers, layer_height=%.3f, relative_e=%s",
        len(result.moves),
        result.total_layers,
        result.layer_height,
        result.is_relative_extrusion,
    )

    return result


def _format_move(move: GCodeMove) -> str:
    """Format a GCodeMove back into a G-code line string.

    Coordinates are formatted to 3 decimal places, E to 5 decimal
    places, and F to 0 decimal places (integer).

    Args:
        move: The move to format.

    Returns:
        A G-code line string (without trailing newline).
    """
    parts = [move.command]
    if move.x is not None:
        parts.append(f" X{move.x:.3f}")
    if move.y is not None:
        parts.append(f" Y{move.y:.3f}")
    if move.z is not None:
        parts.append(f" Z{move.z:.3f}")
    if move.e is not None:
        parts.append(f" E{move.e:.5f}")
    if move.f is not None:
        parts.append(f" F{move.f:.0f}")
    return "".join(parts)


def reconstruct_gcode(
    parsed: ParsedGCode,
    modified_moves: list[GCodeMove],
) -> list[str]:
    """Rebuild the gcode_list from a parsed structure with modified moves.

    Only lines that correspond to moves which have changed are
    rewritten; all other lines (comments, M-commands, etc.) are
    preserved exactly as they appeared in the original.

    When a single original move is replaced by multiple moves (e.g.
    from subdivision), the extra lines are inserted immediately after
    the original line's position.

    Args:
        parsed: The original ParsedGCode from parse_gcode().
        modified_moves: A list of GCodeMove instances, potentially
            longer than the original due to subdivision.  Each move
            retains chunk_index and line_index_in_chunk pointing to
            the original line it replaces or was derived from.

    Returns:
        A new gcode_list with the same chunk structure.
    """
    if not parsed.chunks:
        return []

    # Build a mapping: (chunk_index, line_index) -> list of replacement lines.
    # Multiple modified moves may map to the same original position (subdivision).
    replacements: dict[tuple[int, int], list[str]] = {}
    for move in modified_moves:
        key = (move.chunk_index, move.line_index_in_chunk)
        formatted = _format_move(move)
        if key not in replacements:
            replacements[key] = []
        replacements[key].append(formatted)

    # Also build a set of original lines from the parsed moves so we can
    # detect which (chunk, line) slots are move lines in the original.
    original_move_keys: set[tuple[int, int]] = set()
    for move in parsed.moves:
        original_move_keys.add((move.chunk_index, move.line_index_in_chunk))

    # Rebuild each chunk.
    new_chunks: list[str] = []
    for chunk_idx, chunk in enumerate(parsed.chunks):
        lines = chunk.split("\n")
        new_lines: list[str] = []
        for line_idx, original_line in enumerate(lines):
            key = (chunk_idx, line_idx)
            if key in replacements:
                # Replace the original move line with the modified line(s).
                new_lines.extend(replacements[key])
            elif key in original_move_keys:
                # This was a move in the original that is NOT in the
                # modified set -- keep as-is (it was not targeted for
                # modification).
                new_lines.append(original_line)
            else:
                # Non-move line: preserve exactly.
                new_lines.append(original_line)
        new_chunks.append("\n".join(new_lines))

    return new_chunks
