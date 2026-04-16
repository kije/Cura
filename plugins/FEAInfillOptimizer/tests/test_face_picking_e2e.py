#!/usr/bin/env python3
"""E2E tests for the GPU-based face picking pipeline.

These tests validate that:
1. BoundaryConditionTool.getRequiredExtraRenderingPasses includes "selection_faces".
2. The hover preview logic correctly interprets the GPU face ID:
   - face_id < 0  → hover cleared
   - face_id >= mesh_face_count → hover cleared (bounds check)
   - valid face_id → _hover_faces updated
3. The tool deactivation restores setIgnoreUnselectedObjects to False.

The tests do NOT instantiate BoundaryConditionTool (which requires a full Cura
application stack). Instead they use a _HoverPickingHarness that replicates
the exact conditional logic from _do_hover_preview, giving us a spec-level
contract test.  If the production logic diverges from the spec, manual testing
(or updating this harness) will be required.

Run with: python3 -m pytest tests/test_face_picking_e2e.py -v
"""

import sys
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Locate the plugin source file — used for string-level pass registration test
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_TOOL_SOURCE = (_PLUGIN_ROOT / "BoundaryConditionTool.py").read_text()


# ---------------------------------------------------------------------------
# _HoverPickingHarness — minimal replica of _do_hover_preview picking logic
#
# This mirrors the exact code path in BoundaryConditionTool._do_hover_preview
# after the GPU face-ID switch.  It lets us test every branch in isolation.
# ---------------------------------------------------------------------------

class _HoverPickingHarness:
    """Replicates the GPU-face-ID hover logic from _do_hover_preview."""

    def __init__(self, mesh_face_count: int = 100):
        self._hover_faces: list = []
        self._hover_generation: int = 0
        self._mesh_face_count = mesh_face_count
        self._update_calls: int = 0

    def _update_highlights(self):
        self._update_calls += 1

    def process_face_id(self, face_id: int) -> None:
        """Process a raw GPU face ID exactly as _do_hover_preview does."""
        # --- replicated from _do_hover_preview ---
        if face_id < 0:
            if self._hover_faces:
                self._hover_faces = []
                self._hover_generation += 1
                self._update_highlights()
            return

        # Bounds-check: reject stale/corrupt GPU reads
        mesh_face_count = self._mesh_face_count
        if mesh_face_count > 0 and face_id >= mesh_face_count:
            if self._hover_faces:
                self._hover_faces = []
                self._hover_generation += 1
                self._update_highlights()
            return

        # Valid face — use it directly (single-face instant preview)
        if self._hover_faces != [face_id]:
            self._hover_faces = [face_id]
            self._hover_generation += 1
            self._update_highlights()


# ---------------------------------------------------------------------------
# Test 1: required rendering passes
# ---------------------------------------------------------------------------

def test_selection_faces_pass_in_required_passes():
    """getRequiredExtraRenderingPasses must include 'selection_faces'.

    We check the source text directly — avoids instantiating the full Cura
    application stack while still catching regressions.
    """
    # Find the method body
    match = re.search(
        r"def getRequiredExtraRenderingPasses\(.*?\).*?return\s+\[([^\]]+)\]",
        _TOOL_SOURCE,
        re.DOTALL,
    )
    assert match is not None, "getRequiredExtraRenderingPasses not found in source"
    return_contents = match.group(1)
    assert '"selection_faces"' in return_contents or "'selection_faces'" in return_contents, (
        "getRequiredExtraRenderingPasses does not include 'selection_faces'.\n"
        f"Found: {return_contents!r}"
    )


def test_picking_selected_pass_still_present():
    """getRequiredExtraRenderingPasses must still include 'picking_selected'."""
    match = re.search(
        r"def getRequiredExtraRenderingPasses\(.*?\).*?return\s+\[([^\]]+)\]",
        _TOOL_SOURCE,
        re.DOTALL,
    )
    assert match is not None
    return_contents = match.group(1)
    assert '"picking_selected"' in return_contents or "'picking_selected'" in return_contents, (
        "getRequiredExtraRenderingPasses dropped 'picking_selected'.\n"
        f"Found: {return_contents!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: face_id < 0 clears hover
# ---------------------------------------------------------------------------

def test_negative_face_id_clears_hover():
    """face_id < 0 (cursor off-model) must clear _hover_faces."""
    h = _HoverPickingHarness(mesh_face_count=12)
    # Seed with a non-empty hover state
    h._hover_faces = [3]
    h._hover_generation = 1

    h.process_face_id(-1)

    assert h._hover_faces == [], "hover_faces should be cleared"
    assert h._hover_generation == 2, "generation should have incremented"
    assert h._update_calls == 1, "_update_highlights should have been called"


def test_negative_face_id_noop_when_already_empty():
    """face_id < 0 when hover is already empty should not call _update_highlights."""
    h = _HoverPickingHarness(mesh_face_count=12)
    # hover_faces starts empty
    h.process_face_id(-1)

    assert h._hover_faces == []
    assert h._update_calls == 0, "_update_highlights should not be called unnecessarily"


# ---------------------------------------------------------------------------
# Test 3: out-of-bounds face_id is rejected
# ---------------------------------------------------------------------------

def test_out_of_bounds_face_id_clears_hover():
    """face_id >= mesh_face_count must be rejected to guard against GPU corruption."""
    h = _HoverPickingHarness(mesh_face_count=12)
    h._hover_faces = [5]

    h.process_face_id(99999)

    assert h._hover_faces == [], "out-of-bounds face ID must clear hover"
    assert h._update_calls == 1


def test_out_of_bounds_face_id_at_boundary():
    """face_id == mesh_face_count (exactly) is also out of bounds (0-indexed)."""
    h = _HoverPickingHarness(mesh_face_count=12)
    h._hover_faces = [7]

    h.process_face_id(12)  # valid indices are 0..11

    assert h._hover_faces == []


# ---------------------------------------------------------------------------
# Test 4: valid face_id sets hover
# ---------------------------------------------------------------------------

def test_valid_face_id_sets_hover():
    """A valid GPU face ID must be accepted and stored directly in _hover_faces."""
    h = _HoverPickingHarness(mesh_face_count=12)

    h.process_face_id(7)

    assert h._hover_faces == [7]
    assert h._update_calls == 1


def test_valid_face_id_zero_is_accepted():
    """face_id == 0 is a valid first triangle — must not be treated as falsy."""
    h = _HoverPickingHarness(mesh_face_count=12)

    h.process_face_id(0)

    assert h._hover_faces == [0]


def test_same_face_id_does_not_retrigger_update():
    """Moving within the same face (same GPU ID) must not call _update_highlights."""
    h = _HoverPickingHarness(mesh_face_count=12)
    h._hover_faces = [5]

    h.process_face_id(5)  # same face as current hover

    assert h._update_calls == 0, "no update needed when face didn't change"


def test_face_id_change_triggers_update():
    """Moving to a different face must update _hover_faces and call _update_highlights."""
    h = _HoverPickingHarness(mesh_face_count=12)
    h._hover_faces = [3]

    h.process_face_id(7)

    assert h._hover_faces == [7]
    assert h._update_calls == 1


# ---------------------------------------------------------------------------
# Test 5: no centroid math used in picking path
# ---------------------------------------------------------------------------

def test_find_closest_face_not_called_in_hover_preview():
    """_do_hover_preview must not call _find_closest_face (centroid heuristic removed).

    We verify via source inspection that the hover preview path no longer calls
    _find_closest_face, which was the root cause of the ~1mm shift bug.
    """
    # Isolate the _do_hover_preview method body
    match = re.search(
        r"def _do_hover_preview\(self.*?(?=\n    def |\Z)",
        _TOOL_SOURCE,
        re.DOTALL,
    )
    assert match is not None, "_do_hover_preview not found in source"
    method_body = match.group(0)

    assert "_find_closest_face" not in method_body, (
        "_do_hover_preview still calls _find_closest_face (centroid heuristic). "
        "This was the source of the ~1mm face selection shift bug. "
        "Use getFaceIdAtPosition from the selection_faces render pass instead."
    )
    assert "getFaceIdAtPosition" in method_body, (
        "_do_hover_preview must use getFaceIdAtPosition (GPU face ID) for picking."
    )


def test_mouse_press_uses_gpu_face_id():
    """The MousePressEvent click-handling block must use getFaceIdAtPosition.

    In BoundaryConditionTool the click handler is dispatched from event() via
    Event.MousePressEvent (not a separate mousePressEvent method).  We locate
    the click-handling block by finding the MousePressEvent + LeftButton branch.
    """
    # Find the click-handling block inside event()
    match = re.search(
        r"Event\.MousePressEvent.*?(?=def _handle_rotate_event|\Z)",
        _TOOL_SOURCE,
        re.DOTALL,
    )
    assert match is not None, "MousePressEvent click-handling block not found in source"
    click_block = match.group(0)

    assert "_find_closest_face" not in click_block, (
        "MousePressEvent handler still calls _find_closest_face (centroid heuristic). "
        "This was the source of the ~1mm face selection shift bug. "
        "Use getFaceIdAtPosition from the selection_faces render pass instead."
    )
    assert "getFaceIdAtPosition" in click_block, (
        "MousePressEvent handler must use getFaceIdAtPosition (GPU face ID)."
    )


# ---------------------------------------------------------------------------
# Test 6: setIgnoreUnselectedObjects called on activation
# ---------------------------------------------------------------------------

def test_tool_activate_sets_ignore_unselected():
    """ToolActivateEvent must call setIgnoreUnselectedObjects(True) on the pass.

    Checked via source inspection — the actual call is what matters for Cura
    to restrict the selection_faces render pass to the active model.
    """
    match = re.search(
        r"Event\.ToolActivateEvent.*?(?=Event\.Tool|\Z)",
        _TOOL_SOURCE,
        re.DOTALL,
    )
    assert match is not None, "ToolActivateEvent handler not found"
    activate_block = match.group(0)

    assert "setIgnoreUnselectedObjects(True)" in activate_block, (
        "ToolActivateEvent must call setIgnoreUnselectedObjects(True) on "
        "_faces_selection_pass so only the selected model is face-pickable."
    )


def test_tool_deactivate_restores_ignore_unselected():
    """ToolDeactivateEvent must restore setIgnoreUnselectedObjects(False)."""
    match = re.search(
        r"Event\.ToolDeactivateEvent.*?(?=Event\.Tool|\Z)",
        _TOOL_SOURCE,
        re.DOTALL,
    )
    assert match is not None, "ToolDeactivateEvent handler not found"
    deactivate_block = match.group(0)

    assert "setIgnoreUnselectedObjects(False)" in deactivate_block, (
        "ToolDeactivateEvent must restore setIgnoreUnselectedObjects(False) "
        "so other tools (e.g. PaintTool) are not affected after FEA tool deactivation."
    )
