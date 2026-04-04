# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Interactive tool for defining FEA boundary conditions on mesh faces.

Supports three modes:
- **fixed**: Click faces to mark as zero-displacement supports.
- **force**: Click faces to select a force application region, then
  confirm to create a force group with auto-computed surface normal.
- **rotate**: After confirming a force group, drag the rotation rings
  to adjust the force direction visually.

NOTE: UM.Tool is NOT a QObject — do not use pyqtSignal/pyqtSlot.
State changes are communicated to QML via ``self.propertyChanged``
(a UM.Signal) and ``setExposedProperties``.
"""

from typing import Optional
import json
import math

import numpy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from UM.Application import Application
from UM.Event import Event, MouseEvent
from UM.Logger import Logger
from UM.Math.Plane import Plane
from UM.Math.Quaternion import Quaternion
from UM.Math.Vector import Vector
from UM.Scene.Selection import Selection
from UM.Scene.ToolHandle import ToolHandle
from UM.Tool import Tool

from cura.CuraApplication import CuraApplication
from cura.PickingPass import PickingPass
from cura.Scene.CuraSceneNode import CuraSceneNode

from .FEABoundaryConditionDecorator import FEABoundaryConditionDecorator
from .visualization.bc_highlight import BCHighlightHandle
from .visualization.force_direction_handle import (
    ForceDirectionHandle,
    compute_face_centroid,
    compute_face_normal,
    rotate_vector,
)

try:
    from .fea.face_group_analyzer import (
        build_face_adjacency,
        find_coplanar_group,
        find_hole_surface,
        find_cylinder_surface,
    )
    _FACE_GROUP_ANALYZER_AVAILABLE = True
except ImportError:
    _FACE_GROUP_ANALYZER_AVAILABLE = False

# Modes for the boundary condition tool
MODE_FIXED = "fixed"
MODE_FORCE = "force"
MODE_ROTATE = "rotate"


class BoundaryConditionTool(Tool):

    def __init__(self, extension=None) -> None:
        super().__init__()
        self._extension = extension
        self._controller = self.getController()
        self._selection_pass = None
        self._picking_pass: Optional[PickingPass] = None

        self._mode = MODE_FIXED
        self._force_x = 0.0
        self._force_y = -100.0
        self._force_z = 0.0
        self._force_magnitude = 100.0

        # Faces currently being selected (before confirming a force group)
        self._current_face_selection: list = []

        # Index of the force group currently being rotated (-1 = none)
        self._rotating_group_index = -1
        self._rotate_drag_active = False
        self._rotate_angle = 0.0

        # Active list selection indices (-1 = none selected)
        self._active_support_index = -1
        self._active_force_index = -1

        # Selection mode: "single", "flat", "hole", "cylinder"
        self._selection_mode = "single"

        # Adjacency cache for face group expansion
        self._adjacency_cache = None
        self._adjacency_cache_node = None

        # BC overlay
        scene_root = self._controller.getScene().getRoot()
        self._bc_highlight = BCHighlightHandle(parent=scene_root)

        # Force direction rotation handle
        self._force_handle = ForceDirectionHandle(parent=scene_root)

        # Refresh highlights when the user selects a different model
        Selection.selectionChanged.connect(self._update_highlights)

        self.setExposedProperties(
            "Mode", "ForceX", "ForceY", "ForceZ", "ForceMagnitude",
            "CurrentSelectionCount", "SelectionSummary",
            "ConfirmForceGroup", "ClearAllBCs",
            "ClearFixedFaces", "ClearForceGroups",
            "OpenOptimizeDialog",
            "ActiveSupportIndex", "ActiveForceIndex",
            "SupportListModel", "ForceListModel",
            "SelectionMode",
            "DeleteActiveSupport", "DeleteActiveForce",
        )

    # ── Properties exposed to QML ──────────────────────────────────────────

    def getMode(self) -> str:
        return self._mode

    def setMode(self, mode: str) -> None:
        if mode in (MODE_FIXED, MODE_FORCE, MODE_ROTATE):
            self._mode = mode
            self._current_face_selection.clear()
            if mode != MODE_ROTATE:
                self._force_handle.hide()
                self._rotating_group_index = -1
            self.propertyChanged.emit()
            self._update_highlights()

    def getForceX(self) -> float:
        return self._force_x

    def setForceX(self, value: float) -> None:
        self._force_x = float(value)
        self._sync_magnitude_from_components()
        self.propertyChanged.emit()

    def getForceY(self) -> float:
        return self._force_y

    def setForceY(self, value: float) -> None:
        self._force_y = float(value)
        self._sync_magnitude_from_components()
        self.propertyChanged.emit()

    def getForceZ(self) -> float:
        return self._force_z

    def setForceZ(self, value: float) -> None:
        self._force_z = float(value)
        self._sync_magnitude_from_components()
        self.propertyChanged.emit()

    def getForceMagnitude(self) -> float:
        return self._force_magnitude

    def setForceMagnitude(self, value: float) -> None:
        self._force_magnitude = float(value)
        # Rescale components to match new magnitude while keeping direction
        mag = math.sqrt(self._force_x**2 + self._force_y**2 + self._force_z**2)
        if mag > 1e-9:
            scale = self._force_magnitude / mag
            self._force_x *= scale
            self._force_y *= scale
            self._force_z *= scale
        else:
            # Default direction: downward
            self._force_x = 0.0
            self._force_y = -self._force_magnitude
            self._force_z = 0.0
        self.propertyChanged.emit()

    def _sync_magnitude_from_components(self) -> None:
        self._force_magnitude = math.sqrt(
            self._force_x**2 + self._force_y**2 + self._force_z**2
        )

    def getCurrentSelectionCount(self) -> int:
        return len(self._current_face_selection)

    def getSelectionSummary(self) -> str:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return "No model selected."
        bc = self._get_or_create_bc(selected)
        parts = []
        fixed = bc.getFixedFaceCount()
        if fixed > 0:
            parts.append(f"Fixed: {fixed} faces")
        for i, fg in enumerate(bc.getForceGroups()):
            mag = math.sqrt(fg.force.x**2 + fg.force.y**2 + fg.force.z**2)
            parts.append(f"Force {i+1}: {mag:.0f}N ({len(fg.face_indices)} faces)")
        if self._current_face_selection:
            parts.append(f"Pending: {len(self._current_face_selection)} faces")
        if self._mode == MODE_ROTATE and self._rotating_group_index >= 0:
            parts.append(f"Rotating force {self._rotating_group_index + 1} direction")
        return "\n".join(parts) if parts else "No BCs defined. Click faces to begin."

    # ── Action trigger properties ──────────────────────────────────────────

    def getConfirmForceGroup(self) -> bool:
        return False

    def setConfirmForceGroup(self, value) -> None:
        if value:
            self._confirmForceGroup()

    def getClearAllBCs(self) -> bool:
        return False

    def setClearAllBCs(self, value) -> None:
        if value:
            self._clearAllBCs()

    def getClearFixedFaces(self) -> bool:
        return False

    def setClearFixedFaces(self, value) -> None:
        if value:
            self.clearFixedFaces()

    def getClearForceGroups(self) -> bool:
        return False

    def setClearForceGroups(self, value) -> None:
        if value:
            selected = Selection.getSelectedObject(0)
            if selected is None:
                return
            bc = selected.callDecoration("getBoundaryConditions")
            if bc is not None:
                bc.clearForceGroups()
            self._force_handle.hide()
            self._rotating_group_index = -1
            self.propertyChanged.emit()
            self._update_highlights()

    def getOpenOptimizeDialog(self) -> bool:
        return False

    def setOpenOptimizeDialog(self, value) -> None:
        if value and self._extension:
            # Get the currently selected node's cache key
            selected = Selection.getSelectedObject(0)
            if selected is not None:
                node_key = str(id(selected))
                self._extension.showDialogForNode(node_key)
            else:
                self._extension.showDialog()

    # ── List model properties ───────────────────────────────────────────────

    def getSupportListModel(self) -> str:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return "[]"
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is None:
            return "[]"
        fixed = bc.getFixedFaces()
        if not fixed:
            return "[]"
        return json.dumps([{"index": 0, "label": f"Support ({len(fixed)} faces)", "faces": len(fixed)}])

    def getForceListModel(self) -> str:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return "[]"
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is None:
            return "[]"
        groups = bc.getForceGroups()
        result = []
        for i, fg in enumerate(groups):
            mag = math.sqrt(fg.force.x**2 + fg.force.y**2 + fg.force.z**2)
            result.append({
                "index": i,
                "label": f"Force {i + 1}: {mag:.0f}N ({len(fg.face_indices)} faces)",
                "magnitude": round(mag, 1),
                "faces": len(fg.face_indices),
            })
        return json.dumps(result)

    # ── Active index properties ─────────────────────────────────────────────

    def getActiveSupportIndex(self) -> int:
        return self._active_support_index

    def setActiveSupportIndex(self, index: int) -> None:
        self._active_support_index = int(index)
        self.propertyChanged.emit()

    def getActiveForceIndex(self) -> int:
        return self._active_force_index

    def setActiveForceIndex(self, index: int) -> None:
        self._active_force_index = int(index)
        # Sync Fx/Fy/Fz to show the selected force group's values
        selected = Selection.getSelectedObject(0)
        if selected is not None:
            bc = selected.callDecoration("getBoundaryConditions")
            if bc is not None:
                groups = bc.getForceGroups()
                if 0 <= self._active_force_index < len(groups):
                    fg = groups[self._active_force_index]
                    self._force_x = fg.force.x
                    self._force_y = fg.force.y
                    self._force_z = fg.force.z
                    self._sync_magnitude_from_components()
                    # Show rotation handle at this force group's centroid
                    self._rotating_group_index = self._active_force_index
                    mesh_data = selected.getMeshData()
                    if mesh_data is not None:
                        verts = mesh_data.getVertices()
                        indices = mesh_data.getIndices()
                        if verts is not None:
                            transform = selected.getWorldTransformation().getData()
                            verts_h = numpy.column_stack([verts, numpy.ones(len(verts))])
                            verts_world = (transform @ verts_h.T).T[:, :3]
                            centroid = compute_face_centroid(verts_world, indices, fg.face_indices)
                            bbox = selected.getBoundingBox()
                            if bbox:
                                diag = math.sqrt(bbox.width**2 + bbox.height**2 + bbox.depth**2)
                                scale = max(0.1, diag / 80.0)
                            else:
                                scale = 1.0
                            self._force_handle.show_at(centroid, scale=scale)
        self.propertyChanged.emit()

    # ── Delete trigger properties ───────────────────────────────────────────

    def getDeleteActiveSupport(self) -> bool:
        return False

    def setDeleteActiveSupport(self, value) -> None:
        if value:
            selected = Selection.getSelectedObject(0)
            if selected is None:
                return
            bc = selected.callDecoration("getBoundaryConditions")
            if bc is not None:
                bc.clearFixedFaces()
            self._active_support_index = -1
            self.propertyChanged.emit()
            self._update_highlights()

    def getDeleteActiveForce(self) -> bool:
        return False

    def setDeleteActiveForce(self, value) -> None:
        if value:
            index = self._active_force_index
            if index < 0:
                return
            selected = Selection.getSelectedObject(0)
            if selected is None:
                return
            bc = selected.callDecoration("getBoundaryConditions")
            if bc is not None:
                bc.removeForceGroup(index)
                if self._rotating_group_index == index:
                    self._force_handle.hide()
                    self._rotating_group_index = -1
            self._active_force_index = -1
            self.propertyChanged.emit()
            self._update_highlights()

    # ── Selection mode property ─────────────────────────────────────────────

    def getSelectionMode(self) -> str:
        return self._selection_mode

    def setSelectionMode(self, mode: str) -> None:
        if mode in ("single", "flat", "hole", "cylinder"):
            self._selection_mode = mode
            self.propertyChanged.emit()

    # ── Event handling ─────────────────────────────────────────────────────

    def event(self, event: Event) -> bool:
        super().event(event)

        if event.type == Event.ToolActivateEvent:
            self._update_highlights()
            return False

        if event.type == Event.ToolDeactivateEvent:
            self._bc_highlight.clear()
            self._force_handle.hide()
            self._rotating_group_index = -1
            return False

        # ── Rotation mode: handle ring dragging ────────────────────────────
        if self._mode == MODE_ROTATE and self._rotating_group_index >= 0:
            return self._handle_rotate_event(event)

        # ── Fixed / Force mode: handle face picking ────────────────────────
        if event.type == Event.MousePressEvent and MouseEvent.LeftButton in event.buttons:
            if not self._controller.getToolsEnabled():
                return False

            modifiers = QApplication.keyboardModifiers()
            # Use Alt (Option on macOS) for add/remove — Shift and Ctrl
            # are already bound by Cura for camera orbit/pan.
            alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)

            if self._selection_pass is None:
                self._selection_pass = Application.getInstance().getRenderer().getRenderPass("selection")

            picked_node = self._controller.getScene().findObject(
                self._selection_pass.getIdAtPosition(event.x, event.y)
            )
            if not picked_node or not isinstance(picked_node, CuraSceneNode):
                return False

            if picked_node.callDecoration("isNonPrintingMesh"):
                return False

            mesh_data = picked_node.getMeshData()
            if mesh_data is None:
                return False

            if self._picking_pass is None:
                self._picking_pass = Application.getInstance().getRenderer().getRenderPass("picking_selected")
                if not self._picking_pass:
                    return False

            picked_position = self._picking_pass.getPickedPosition(event.x, event.y)
            face_index = self._find_closest_face(picked_node, picked_position)
            if face_index is None:
                return False

            # Expand selection if a face group mode is active
            if self._selection_mode != "single" and _FACE_GROUP_ANALYZER_AVAILABLE:
                mesh_data = picked_node.getMeshData()
                verts = mesh_data.getVertices()
                indices = mesh_data.getIndices()
                node_id = id(picked_node)
                if self._adjacency_cache_node != node_id:
                    self._adjacency_cache = build_face_adjacency(verts, indices)
                    self._adjacency_cache_node = node_id
                if self._selection_mode == "flat":
                    face_indices_to_add = find_coplanar_group(verts, indices, face_index, self._adjacency_cache)
                elif self._selection_mode == "hole":
                    face_indices_to_add = find_hole_surface(verts, indices, face_index, self._adjacency_cache)
                elif self._selection_mode == "cylinder":
                    face_indices_to_add = find_cylinder_surface(verts, indices, face_index, self._adjacency_cache)
                else:
                    face_indices_to_add = [face_index]
            else:
                face_indices_to_add = [face_index]

            if self._mode == MODE_FIXED:
                bc = self._get_or_create_bc(picked_node)
                if alt:
                    # Alt+click toggles: remove if already fixed, add otherwise
                    existing = set(bc.getFixedFaces())
                    to_add = [f for f in face_indices_to_add if f not in existing]
                    to_remove = [f for f in face_indices_to_add if f in existing]
                    if to_remove:
                        bc.removeFixedFaces(to_remove)
                    if to_add:
                        bc.addFixedFaces(to_add)
                else:
                    bc.addFixedFaces(face_indices_to_add)
                self.propertyChanged.emit()
                self._update_highlights()

            elif self._mode == MODE_FORCE:
                if alt:
                    # Alt+click toggles face in/out of pending selection
                    existing = set(self._current_face_selection)
                    for fi in face_indices_to_add:
                        if fi in existing:
                            self._current_face_selection.remove(fi)
                        else:
                            self._current_face_selection.append(fi)
                else:
                    # Plain click: add to pending selection (accumulate)
                    existing = set(self._current_face_selection)
                    for fi in face_indices_to_add:
                        if fi not in existing:
                            self._current_face_selection.append(fi)
                self.propertyChanged.emit()
                self._update_highlights()

            return True

        return False

    # ── Rotation event handler ─────────────────────────────────────────────

    def _handle_rotate_event(self, event: Event) -> bool:
        """Handle mouse events when in rotation mode."""
        if event.type == Event.MousePressEvent and MouseEvent.LeftButton in event.buttons:
            if self._selection_pass is None:
                self._selection_pass = Application.getInstance().getRenderer().getRenderPass("selection")

            selection_id = self._selection_pass.getIdAtPosition(event.x, event.y)
            if not selection_id:
                return False

            if self._force_handle.isAxis(selection_id):
                self.setLockedAxis(selection_id)
                handle_pos = self._force_handle.center

                if selection_id == ToolHandle.XAxis:
                    self.setDragPlane(Plane(Vector.Unit_X, handle_pos.x))
                elif selection_id == ToolHandle.YAxis:
                    self.setDragPlane(Plane(Vector.Unit_Y, handle_pos.y))
                elif selection_id == ToolHandle.ZAxis:
                    self.setDragPlane(Plane(Vector.Unit_Z, handle_pos.z))

                self.setDragStart(event.x, event.y)
                self._rotate_drag_active = True
                self._rotate_angle = 0.0
                return True
            return False

        if event.type == Event.MouseMoveEvent and self._rotate_drag_active:
            if not self.getDragPlane() or not self.getDragStart():
                return False

            handle_pos = self._force_handle.center
            drag_start = (self.getDragStart() - handle_pos).normalized()
            drag_position = self.getDragPosition(event.x, event.y)
            if not drag_position:
                return False
            drag_end = (drag_position - handle_pos).normalized()

            try:
                angle = math.acos(max(-1.0, min(1.0, drag_start.dot(drag_end))))
            except ValueError:
                angle = 0

            # Snap to 1-degree increments
            snap = math.radians(1)
            angle = int(angle / snap) * snap
            if angle == 0:
                return False

            # Determine rotation direction
            if self.getLockedAxis() == ToolHandle.XAxis:
                direction = 1 if Vector.Unit_X.dot(drag_start.cross(drag_end)) > 0 else -1
                axis = Vector.Unit_X
            elif self.getLockedAxis() == ToolHandle.YAxis:
                direction = 1 if Vector.Unit_Y.dot(drag_start.cross(drag_end)) > 0 else -1
                axis = Vector.Unit_Y
            elif self.getLockedAxis() == ToolHandle.ZAxis:
                direction = 1 if Vector.Unit_Z.dot(drag_start.cross(drag_end)) > 0 else -1
                axis = Vector.Unit_Z
            else:
                return False

            self._rotate_angle += direction * angle

            # Apply rotation to the force direction
            selected = Selection.getSelectedObject(0)
            if selected is None:
                return False
            bc = selected.callDecoration("getBoundaryConditions")
            if bc is None:
                return False
            groups = bc.getForceGroups()
            if self._rotating_group_index >= len(groups):
                return False

            fg = groups[self._rotating_group_index]
            new_dir = rotate_vector(fg.force, axis, direction * angle)
            fg.force = new_dir

            # Update Fx/Fy/Fz display
            self._force_x = fg.force.x
            self._force_y = fg.force.y
            self._force_z = fg.force.z
            self._sync_magnitude_from_components()

            self.setDragStart(event.x, event.y)
            self.propertyChanged.emit()
            self._update_highlights()
            return True

        if event.type == Event.MouseReleaseEvent and self._rotate_drag_active:
            self._rotate_drag_active = False
            self.setDragPlane(None)
            self.setLockedAxis(ToolHandle.NoAxis)
            self._rotate_angle = 0.0
            self.propertyChanged.emit()
            return True

        return False

    # ── Face finding ───────────────────────────────────────────────────────

    def _find_closest_face(self, node: CuraSceneNode, picked_position: Vector) -> Optional[int]:
        mesh_data = node.getMeshData()
        if mesh_data is None:
            return None

        verts = mesh_data.getVertices()
        indices = mesh_data.getIndices()
        if verts is None:
            return None

        transform = node.getWorldTransformation().getData()
        verts_h = numpy.column_stack([verts, numpy.ones(len(verts))])
        verts_world = (transform @ verts_h.T).T[:, :3]

        picked = numpy.array([picked_position.x, picked_position.y, picked_position.z])

        if indices is not None:
            centroids = (verts_world[indices[:, 0]] +
                         verts_world[indices[:, 1]] +
                         verts_world[indices[:, 2]]) / 3.0
        else:
            n_tris = len(verts_world) // 3
            verts_reshaped = verts_world[:n_tris * 3].reshape(n_tris, 3, 3)
            centroids = verts_reshaped.mean(axis=1)

        distances = numpy.linalg.norm(centroids - picked, axis=1)
        return int(numpy.argmin(distances))

    def _get_or_create_bc(self, node: CuraSceneNode) -> FEABoundaryConditionDecorator:
        bc = node.callDecoration("getBoundaryConditions")
        if bc is None:
            decorator = FEABoundaryConditionDecorator()
            node.addDecorator(decorator)
            return decorator
        return bc

    # ── Internal actions ───────────────────────────────────────────────────

    def _confirmForceGroup(self) -> None:
        """Confirm selected faces as a force group with auto-computed normal."""
        if not self._current_face_selection:
            return

        selected = Selection.getSelectedObject(0)
        if selected is None:
            return

        mesh_data = selected.getMeshData()
        if mesh_data is None:
            return

        verts = mesh_data.getVertices()
        indices = mesh_data.getIndices()
        if verts is None:
            return

        # Auto-compute outward surface normal as initial force direction
        normal = compute_face_normal(verts, indices, self._current_face_selection)

        # Apply the force in the INWARD direction (push into the surface)
        # with current magnitude
        force_dir = Vector(-normal.x, -normal.y, -normal.z)
        mag = self._force_magnitude
        force = Vector(force_dir.x * mag, force_dir.y * mag, force_dir.z * mag)

        bc = self._get_or_create_bc(selected)
        bc.addForceGroup(list(self._current_face_selection), force)

        # Update Fx/Fy/Fz to reflect the auto-computed direction
        self._force_x = force.x
        self._force_y = force.y
        self._force_z = force.z
        self._sync_magnitude_from_components()

        # Enter rotate mode so user can adjust the direction
        group_index = len(bc.getForceGroups()) - 1
        self._rotating_group_index = group_index
        self._active_force_index = group_index
        self._current_face_selection.clear()
        self._mode = MODE_ROTATE

        # Show rotation rings at the force centroid (in world space)
        transform = selected.getWorldTransformation().getData()
        verts_h = numpy.column_stack([verts, numpy.ones(len(verts))])
        verts_world = (transform @ verts_h.T).T[:, :3]
        centroid = compute_face_centroid(
            verts_world, indices, bc.getForceGroups()[group_index].face_indices
        )

        # Scale rings relative to model bounding box
        bbox = selected.getBoundingBox()
        if bbox:
            diag = math.sqrt(bbox.width**2 + bbox.height**2 + bbox.depth**2)
            scale = max(0.1, diag / 80.0)
        else:
            scale = 1.0
        self._force_handle.show_at(centroid, scale=scale)

        self.propertyChanged.emit()
        self._update_highlights()

    def _clearAllBCs(self) -> None:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is not None:
            bc.clearAll()
        self._current_face_selection.clear()
        self._force_handle.hide()
        self._rotating_group_index = -1
        self._active_support_index = -1
        self._active_force_index = -1
        self._mode = MODE_FIXED
        self.propertyChanged.emit()
        self._update_highlights()

    def deleteForceGroup(self, index: int) -> None:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is not None:
            bc.removeForceGroup(index)
            if self._rotating_group_index == index:
                self._force_handle.hide()
                self._rotating_group_index = -1
                self._mode = MODE_FORCE
            self.propertyChanged.emit()
            self._update_highlights()

    def clearFixedFaces(self) -> None:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is not None:
            bc.clearFixedFaces()
            self.propertyChanged.emit()
            self._update_highlights()

    def _update_highlights(self) -> None:
        node = Selection.getSelectedObject(0)
        if node is None or not isinstance(node, CuraSceneNode):
            self._bc_highlight.clear()
            return

        bc = node.callDecoration("getBoundaryConditions")
        has_bc = bc is not None and bc.hasAnyBC()
        has_pending = len(self._current_face_selection) > 0

        if not has_bc and not has_pending:
            self._bc_highlight.clear()
            return

        try:
            # Paint confirmed BCs + pending selection + active force handle
            self._bc_highlight.update_visualization(
                node, bc,
                pending_faces=self._current_face_selection if has_pending else None,
                active_force_index=self._active_force_index,
            )
        except Exception:
            Logger.logException("w", "FEA Infill: Failed to update BC highlight overlay")
            self._bc_highlight.clear()

    def getRequiredExtraRenderingPasses(self) -> list:
        return ["picking_selected"]
