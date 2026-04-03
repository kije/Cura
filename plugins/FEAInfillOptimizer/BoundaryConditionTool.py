# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from typing import Optional

import numpy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from UM.Application import Application
from UM.Event import Event, MouseEvent
from UM.Logger import Logger
from UM.Math.Vector import Vector
from UM.Scene.Selection import Selection
from UM.Tool import Tool

from cura.CuraApplication import CuraApplication
from cura.PickingPass import PickingPass
from cura.Scene.CuraSceneNode import CuraSceneNode

from .FEABoundaryConditionDecorator import FEABoundaryConditionDecorator
from .visualization.bc_highlight import BCHighlightHandle

# Modes for the boundary condition tool
MODE_FIXED = "fixed"
MODE_FORCE = "force"


class BoundaryConditionTool(Tool):
    """Interactive tool for defining FEA boundary conditions on mesh faces.

    NOTE: UM.Tool is NOT a QObject — do not use pyqtSignal/pyqtSlot.
    State changes are communicated to QML via ``self.propertyChanged``
    (a UM.Signal) and ``setExposedProperties``.
    """

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

        # Faces currently being selected (before confirming a force group)
        self._current_face_selection: list = []

        # BC overlay: parented to the scene root so it lives in world space.
        # Transformation is updated per-frame to match the selected node.
        scene_root = self._controller.getScene().getRoot()
        self._bc_highlight = BCHighlightHandle(parent=scene_root)

        # Refresh highlights when the user selects a different model.
        Selection.selectionChanged.connect(self._update_highlights)

        self.setExposedProperties("Mode", "ForceX", "ForceY", "ForceZ",
                                  "CurrentSelectionCount", "SelectionSummary",
                                  "ConfirmForceGroup", "ClearAllBCs",
                                  "ClearFixedFaces", "ClearForceGroups")

    # -- Properties exposed to QML via UM.Controller.properties --

    def getMode(self) -> str:
        return self._mode

    def setMode(self, mode: str) -> None:
        if mode in (MODE_FIXED, MODE_FORCE):
            self._mode = mode
            self._current_face_selection.clear()
            self.propertyChanged.emit()
            self._update_highlights()

    def getForceX(self) -> float:
        return self._force_x

    def setForceX(self, value: float) -> None:
        self._force_x = float(value)
        self.propertyChanged.emit()

    def getForceY(self) -> float:
        return self._force_y

    def setForceY(self, value: float) -> None:
        self._force_y = float(value)
        self.propertyChanged.emit()

    def getForceZ(self) -> float:
        return self._force_z

    def setForceZ(self, value: float) -> None:
        self._force_z = float(value)
        self.propertyChanged.emit()

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
            mag = (fg.force.x**2 + fg.force.y**2 + fg.force.z**2) ** 0.5
            parts.append(f"Force {i+1}: {mag:.0f}N ({len(fg.face_indices)} faces)")
        if self._current_face_selection:
            parts.append(f"Current selection: {len(self._current_face_selection)} faces")
        return "\n".join(parts) if parts else "No BCs defined. Click faces to begin."

    # -- Action trigger properties (QML sets these to True to trigger actions) --

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
            self.propertyChanged.emit()
            self._update_highlights()

    # -- Event handling --

    def event(self, event: Event) -> bool:
        super().event(event)

        if event.type == Event.ToolActivateEvent:
            self._update_highlights()
            return False

        if event.type == Event.ToolDeactivateEvent:
            self._bc_highlight.clear()
            return False

        if event.type == Event.MousePressEvent and MouseEvent.LeftButton in event.buttons:
            if not self._controller.getToolsEnabled():
                return False

            modifiers = QApplication.keyboardModifiers()
            ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

            if self._selection_pass is None:
                self._selection_pass = Application.getInstance().getRenderer().getRenderPass("selection")

            picked_node = self._controller.getScene().findObject(
                self._selection_pass.getIdAtPosition(event.x, event.y)
            )
            if not picked_node or not isinstance(picked_node, CuraSceneNode):
                return False

            # Don't place BCs on modifier meshes
            if picked_node.callDecoration("isNonPrintingMesh"):
                return False

            mesh_data = picked_node.getMeshData()
            if mesh_data is None:
                return False

            # Get picking position
            if self._picking_pass is None:
                self._picking_pass = Application.getInstance().getRenderer().getRenderPass("picking_selected")
                if not self._picking_pass:
                    return False

            picked_position = self._picking_pass.getPickedPosition(event.x, event.y)

            # Find the closest face
            face_index = self._find_closest_face(picked_node, picked_position)
            if face_index is None:
                return False

            if self._mode == MODE_FIXED:
                bc = self._get_or_create_bc(picked_node)
                if ctrl:
                    bc.removeFixedFaces([face_index])
                else:
                    bc.addFixedFaces([face_index])
                self.propertyChanged.emit()
                self._update_highlights()

            elif self._mode == MODE_FORCE:
                if ctrl:
                    if face_index in self._current_face_selection:
                        self._current_face_selection.remove(face_index)
                elif shift:
                    if face_index not in self._current_face_selection:
                        self._current_face_selection.append(face_index)
                else:
                    self._current_face_selection = [face_index]
                self.propertyChanged.emit()
                self._update_highlights()

            return True

        return False

    def _find_closest_face(self, node: CuraSceneNode, picked_position: Vector) -> Optional[int]:
        """Find the triangle face index closest to the picked world position."""
        mesh_data = node.getMeshData()
        if mesh_data is None:
            return None

        verts = mesh_data.getVertices()
        indices = mesh_data.getIndices()
        if verts is None:
            return None

        transform = node.getWorldTransformation().getData()

        # Transform vertices to world space
        verts_h = numpy.column_stack([verts, numpy.ones(len(verts))])
        verts_world = (transform @ verts_h.T).T[:, :3]

        picked = numpy.array([picked_position.x, picked_position.y, picked_position.z])

        if indices is not None:
            # Compute centroid of each triangle
            centroids = (verts_world[indices[:, 0]] +
                         verts_world[indices[:, 1]] +
                         verts_world[indices[:, 2]]) / 3.0
        else:
            # Flat vertex list: every 3 vertices form a triangle
            n_tris = len(verts_world) // 3
            verts_reshaped = verts_world[:n_tris * 3].reshape(n_tris, 3, 3)
            centroids = verts_reshaped.mean(axis=1)

        # Find closest centroid
        distances = numpy.linalg.norm(centroids - picked, axis=1)
        return int(numpy.argmin(distances))

    def _get_or_create_bc(self, node: CuraSceneNode) -> FEABoundaryConditionDecorator:
        """Get or attach a FEABoundaryConditionDecorator to the node."""
        bc = node.callDecoration("getBoundaryConditions")
        if bc is None:
            decorator = FEABoundaryConditionDecorator()
            node.addDecorator(decorator)
            return decorator
        return bc

    # -- Internal actions (triggered by exposed property setters) --

    def _confirmForceGroup(self) -> None:
        """Confirm the current face selection as a force group."""
        if not self._current_face_selection:
            return

        selected = Selection.getSelectedObject(0)
        if selected is None:
            return

        bc = self._get_or_create_bc(selected)
        force = Vector(self._force_x, self._force_y, self._force_z)
        bc.addForceGroup(list(self._current_face_selection), force)

        self._current_face_selection.clear()
        self.propertyChanged.emit()
        self._update_highlights()

    def _clearAllBCs(self) -> None:
        """Clear all boundary conditions on the selected model."""
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is not None:
            bc.clearAll()
        self._current_face_selection.clear()
        self.propertyChanged.emit()
        self._update_highlights()

    def deleteForceGroup(self, index: int) -> None:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is not None:
            bc.removeForceGroup(index)
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
        """Rebuild the BC overlay for the currently selected node.

        Clears the overlay when no model is selected or when the selected
        node has no boundary condition decorator.
        """
        node = Selection.getSelectedObject(0)
        if node is None or not isinstance(node, CuraSceneNode):
            self._bc_highlight.clear()
            return

        bc = node.callDecoration("getBoundaryConditions")
        if bc is None or not bc.hasAnyBC():
            self._bc_highlight.clear()
            return

        try:
            self._bc_highlight.update_visualization(node, bc)
        except Exception:
            Logger.logException("w", "FEA Infill: Failed to update BC highlight overlay")
            self._bc_highlight.clear()

    def getRequiredExtraRenderingPasses(self) -> list:
        return ["picking_selected"]
