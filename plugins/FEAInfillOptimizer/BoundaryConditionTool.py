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
import threading
import time

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
from .operations.bc_operations import (
    AddFixedFacesOperation,
    RemoveFixedFacesOperation,
    ClearFixedFacesOperation,
    AddForceGroupOperation,
    RemoveForceGroupOperation,
    AddTorqueGroupOperation,
    RemoveTorqueGroupOperation,
    ClearAllBCsOperation,
    UpdateTorqueAxisOperation,
    UpdateForceDirectionOperation,
    UpdateTorqueMagnitudeOperation,
)
from .visualization.bc_highlight import BCHighlightHandle
from .visualization.force_direction_handle import (
    ForceDirectionHandle,
    compute_face_centroid,
    compute_face_normal,
    rotate_vector,
)
from .visualization.torque_axis_placement import TorqueAxisPlacementNode

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
MODE_TORQUE = "torque"
MODE_ROTATE = "rotate"
MODE_TORQUE_EDIT = "torque_edit"


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
        self._force_direction_before_drag: Optional[Vector] = None  # force at drag start for undo

        # Active list selection indices (-1 = none selected)
        self._active_support_index = -1
        self._active_force_index = -1

        # Selection mode: "single", "flat", "hole", "cylinder"
        self._selection_mode = "single"

        # Adjacency cache for face group expansion
        self._adjacency_cache = None
        self._adjacency_cache_node = None

        # Torque state
        self._torque_magnitude = 1.0  # Nm
        self._editing_torque_index = -1  # Index of the torque group being edited (-1 = none)
        self._torque_edit_drag_active = False
        self._torque_edit_angle = 0.0
        self._torque_axis_before_drag: Optional[Vector] = None  # axis at drag start for undo

        # Hover preview faces (shown in orange when mouse hovers over model)
        self._hover_faces: list = []
        self._hover_preview_enabled = True

        # Async hover computation state
        self._hover_generation = 0  # monotonic counter; stale results are discarded
        self._hover_pending = False  # True while a background thread is computing
        self._hover_debounce_time = 0.0  # timestamp of last mouse move
        self._hover_debounce_timer = None  # QTimer for debounced dispatch
        self._hover_debounce_ms = 75  # ms to wait before triggering computation

        # Async adjacency build state
        self._adjacency_building = False  # True while adjacency is being built in bg

        # Re-entrancy guard for hover preview
        self._hover_in_progress = False

        # Centroid cache for fast face picking during hover
        self._centroid_cache = None
        self._centroid_cache_node_id = None
        self._centroid_cache_mesh_id = None
        self._centroid_cache_transform_bytes = None

        # Quick setup state
        self._quick_setup_mode = ""  # "", "gravity_pick_bottom", "cantilever_pick_fixed",
        #                              "torque_set_axis", "torque_pick_faces"
        self._quick_setup_hole_diameter = 8.0  # mm

        # Torque quick setup: axis placement node and captured axis data
        self._torque_axis_node: Optional[TorqueAxisPlacementNode] = None
        self._torque_captured_position: Optional[Vector] = None
        self._torque_captured_direction: Optional[Vector] = None

        # BC overlay
        scene_root = self._controller.getScene().getRoot()
        self._bc_highlight = BCHighlightHandle(parent=scene_root)

        # Force direction rotation handle
        self._force_handle = ForceDirectionHandle(parent=scene_root)

        # Refresh highlights when the user selects a different model
        Selection.selectionChanged.connect(self._update_highlights)

        # Relay extension signals → tool's propertyChanged so QML bindings refresh
        if extension is not None:
            extension.phaseChanged.connect(self.propertyChanged.emit)
            extension.progressChanged.connect(self.propertyChanged.emit)
            extension.resultsChanged.connect(self.propertyChanged.emit)
            extension.analysisStatusChanged.connect(self.propertyChanged.emit)
            extension.depsAvailableChanged.connect(self.propertyChanged.emit)
            extension.settingsChanged.connect(self.propertyChanged.emit)
            extension.errorMessageChanged.connect(self.propertyChanged.emit)
            extension.stressOverlayVisibleChanged.connect(self.propertyChanged.emit)

        self.setExposedProperties(
            "Mode", "ForceX", "ForceY", "ForceZ", "ForceMagnitude",
            "CurrentSelectionCount", "SelectionSummary",
            "ConfirmForceGroup", "ClearAllBCs",
            "ClearFixedFaces", "ClearForceGroups",
            "OpenOptimizeDialog",
            "QuickGravityStart", "QuickMountHoles", "QuickCantileverStart",
            "QuickTorqueAxisStart", "ConfirmTorqueAxis", "ConfirmTorqueFaces",
            "QuickSetupMode", "QuickHoleDiameter",
            "TorqueMagnitude", "ConfirmTorqueGroup",
            "TorqueListModel", "DeleteTorqueGroup",
            "ActiveSupportIndex", "ActiveForceIndex", "ActiveTorqueIndex",
            "SupportListModel", "ForceListModel",
            "SelectionMode",
            "DeleteActiveSupport", "DeleteActiveForce",
            "UpdateForceAtIndex", "UpdateTorqueAtIndex",
            # Inline phase flow
            "Phase",
            "RunAnalysis", "CancelAnalysis", "GoBackToDefine",
            "ApplyModifierMeshes", "ShowStressOverlay", "ClearResults",
            "MaterialName", "MaterialSummary", "InfillPattern", "SafetyFactor", "MeshResolution",
            "AnalysisProgress", "AnalysisStage",
            "MaxStress", "MinStress", "SafetyFactorResult",
            "ConvergenceIterations", "SafetyVerdict", "HasResults",
            "ActiveNodeName", "MeshQuality", "MeshWarnings",
            "MinDensity", "MaxDensity", "NumZones", "MaxIterations", "BondingCoeff",
            "OptimizationMethod", "VolumeFraction",
            "DepsAvailable", "InstallDependencies",
            "HoverPreviewEnabled",
            "ErrorMessage",
            "StressOverlayVisible",
            "GoBackToOptimize",
            "HasFullResults",
        )

    # ── Properties exposed to QML ──────────────────────────────────────────

    def getMode(self) -> str:
        return self._mode

    def setMode(self, mode: str) -> None:
        if mode in (MODE_FIXED, MODE_FORCE, MODE_TORQUE, MODE_ROTATE, MODE_TORQUE_EDIT):
            self._mode = mode
            self._current_face_selection.clear()
            self._hover_faces = []
            self._hover_generation += 1  # invalidate pending hover
            self._hover_pending = False
            if mode not in (MODE_ROTATE, MODE_TORQUE_EDIT):
                self._force_handle.hide()
                self._rotating_group_index = -1
                self._editing_torque_index = -1
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

    def getTorqueMagnitude(self) -> float:
        return self._torque_magnitude

    def setTorqueMagnitude(self, value) -> None:
        self._torque_magnitude = float(value)
        self.propertyChanged.emit()

    def getConfirmTorqueGroup(self) -> bool:
        return False

    def setConfirmTorqueGroup(self, value) -> None:
        if value:
            self._confirmTorqueGroup()

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
        for i, tg in enumerate(bc.getTorqueGroups()):
            parts.append(f"Torque {i+1}: {tg.torque_magnitude:.1f}Nm ({len(tg.face_indices)} faces)")
        if self._current_face_selection:
            parts.append(f"Pending: {len(self._current_face_selection)} faces")
        if self._mode == MODE_ROTATE and self._rotating_group_index >= 0:
            parts.append(f"Rotating force {self._rotating_group_index + 1} direction")
        if self._mode == MODE_TORQUE_EDIT and self._editing_torque_index >= 0:
            parts.append(f"Editing torque {self._editing_torque_index + 1} axis")
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
                # Remove groups in reverse-index order so each RemoveForceGroupOperation
                # snapshots the correct index before prior removals shift the list.
                groups = bc.getForceGroups()
                for i in range(len(groups) - 1, -1, -1):
                    RemoveForceGroupOperation(bc, i).push()
            self._force_handle.hide()
            self._rotating_group_index = -1
            self.propertyChanged.emit()
            self._update_highlights()

    # ── Quick setup properties ────────────────────────────────────────────

    def getQuickSetupMode(self) -> str:
        return self._quick_setup_mode

    def setQuickSetupMode(self, value: str) -> None:
        pass  # Read-only from QML; written by quick-setup actions

    def getQuickHoleDiameter(self) -> float:
        return self._quick_setup_hole_diameter

    def setQuickHoleDiameter(self, value) -> None:
        self._quick_setup_hole_diameter = float(value)
        self.propertyChanged.emit()

    def getQuickGravityStart(self) -> bool:
        return False

    def setQuickGravityStart(self, value) -> None:
        """Enter 'pick bottom face' mode for gravity setup."""
        Logger.log("d", "FEA: setQuickGravityStart called with value=%s (type=%s)", value, type(value).__name__)
        if value:
            self._quick_setup_mode = "gravity_pick_bottom"
            Logger.log("d", "FEA: Quick setup mode set to '%s'", self._quick_setup_mode)
            self.propertyChanged.emit()

    def getQuickCantileverStart(self) -> bool:
        return False

    def setQuickCantileverStart(self, value) -> None:
        """Enter 'pick fixed end' mode for cantilever setup."""
        if value:
            self._quick_setup_mode = "cantilever_pick_fixed"
            self.propertyChanged.emit()

    def getQuickTorqueAxisStart(self) -> bool:
        return False

    def setQuickTorqueAxisStart(self, value) -> None:
        """Enter 'set axis' mode for torque quick setup."""
        if not value:
            return
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return

        # Create the axis placement node at the model's bounding box center
        bbox = selected.getBoundingBox()
        if bbox:
            center = Vector(bbox.center.x, bbox.center.y, bbox.center.z)
            diag = math.sqrt(bbox.width**2 + bbox.height**2 + bbox.depth**2)
            axis_length = max(20.0, diag * 0.8)
        else:
            center = Vector(0, 0, 0)
            axis_length = 100.0

        scene_root = self._controller.getScene().getRoot()
        # Remove any existing axis node
        self._remove_torque_axis_node()

        self._torque_axis_node = TorqueAxisPlacementNode(parent=scene_root,
                                                          length=axis_length)
        self._torque_axis_node.setPosition(center)

        # Select the axis node so Cura's transform gizmos appear on it.
        # IMPORTANT: add the axis node BEFORE removing the old selection.
        # Calling Selection.clear() while the selection is non-empty causes
        # CuraApplication.onSelectionChanged() to detect an empty selection and
        # call setActiveTool(None), which fires ToolDeactivateEvent.  That event
        # handler calls _remove_torque_axis_node(), leaving self._torque_axis_node = None
        # before we reach Selection.add() – causing an AttributeError that also
        # corrupts the global Selection list with a None entry.
        Selection.add(self._torque_axis_node)
        for _obj in list(Selection.getAllSelectedObjects()):
            if _obj is not self._torque_axis_node:
                Selection.remove(_obj)

        self._quick_setup_mode = "torque_set_axis"
        self._current_face_selection.clear()
        self.propertyChanged.emit()

    def getConfirmTorqueAxis(self) -> bool:
        return False

    def setConfirmTorqueAxis(self, value) -> None:
        """Capture axis position + direction, then move to face-picking."""
        if not value or self._quick_setup_mode != "torque_set_axis":
            return
        if self._torque_axis_node is None:
            return

        # Capture position and direction from the placement node
        self._torque_captured_position = self._torque_axis_node.get_axis_position()
        self._torque_captured_direction = self._torque_axis_node.get_axis_direction()

        # Remove the placement node from scene
        self._remove_torque_axis_node()

        # Re-select the model so face picking works
        selected = Selection.getSelectedObject(0)
        if selected is None or isinstance(selected, TorqueAxisPlacementNode):
            # Find the CuraSceneNode that was previously selected
            Selection.clear()
            scene_root = self._controller.getScene().getRoot()
            for child in scene_root.getChildren():
                if isinstance(child, CuraSceneNode) and not child.callDecoration("isNonPrintingMesh"):
                    Selection.add(child)
                    break

        # Switch to face-picking mode
        self._quick_setup_mode = "torque_pick_faces"
        self._mode = MODE_TORQUE
        self._current_face_selection.clear()
        self.propertyChanged.emit()

    def getConfirmTorqueFaces(self) -> bool:
        return False

    def setConfirmTorqueFaces(self, value) -> None:
        """Create the torque group with the captured axis and selected faces."""
        if not value or self._quick_setup_mode != "torque_pick_faces":
            return
        if not self._current_face_selection:
            return
        if self._torque_captured_direction is None:
            return

        selected = Selection.getSelectedObject(0)
        if selected is None:
            return

        bc = self._get_or_create_bc(selected)
        AddTorqueGroupOperation(
            bc, list(self._current_face_selection),
            self._torque_captured_direction,
            self._torque_magnitude,
            self._torque_captured_position
        ).push()

        # Clean up
        self._current_face_selection.clear()
        self._quick_setup_mode = ""
        self._torque_captured_position = None
        self._torque_captured_direction = None

        # Enter torque edit mode on the new group
        group_index = len(bc.getTorqueGroups()) - 1
        self._editing_torque_index = group_index
        self._mode = MODE_TORQUE_EDIT

        # Show rotation rings at the torque centroid
        mesh_data = selected.getMeshData()
        if mesh_data is not None:
            verts = mesh_data.getVertices()
            indices = mesh_data.getIndices()
            if verts is not None:
                transform = selected.getWorldTransformation().getData()
                verts_h = numpy.column_stack([verts, numpy.ones(len(verts))])
                verts_world = (transform @ verts_h.T).T[:, :3]
                centroid = compute_face_centroid(
                    verts_world, indices, bc.getTorqueGroups()[group_index].face_indices
                )
                bbox = selected.getBoundingBox()
                if bbox:
                    diag = math.sqrt(bbox.width**2 + bbox.height**2 + bbox.depth**2)
                    scale = max(0.1, diag / 80.0)
                else:
                    scale = 1.0
                self._force_handle.show_at(
                    centroid, scale=scale,
                    axis_direction=self._torque_captured_direction
                    if self._torque_captured_direction else None,
                )

        self.propertyChanged.emit()
        self._update_highlights()

    def _remove_torque_axis_node(self) -> None:
        """Remove the torque axis placement node from the scene."""
        if self._torque_axis_node is not None:
            parent = self._torque_axis_node.getParent()
            if parent is not None:
                parent.removeChild(self._torque_axis_node)
            self._torque_axis_node = None

    def getQuickMountHoles(self) -> bool:
        return False

    def setQuickMountHoles(self, value) -> None:
        """Auto-detect and fix all small holes (bolt/screw mounts)."""
        if not value:
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

        adjacency = self._ensure_adjacency(selected, verts, indices)
        if adjacency is None:
            return

        from .fea.quick_setup import mount_holes
        result = mount_holes(verts, indices, self._quick_setup_hole_diameter, adjacency)

        if result["fixed_faces"]:
            bc = self._get_or_create_bc(selected)
            AddFixedFacesOperation(bc, result["fixed_faces"]).push()
            self.propertyChanged.emit()
            self._update_highlights()

    def _handle_quick_setup_click(self, node, face_index) -> bool:
        """Handle a face click during a quick setup interaction.

        Returns True if the click was consumed by a quick setup.
        """
        if not self._quick_setup_mode:
            return False

        # In torque_set_axis mode, the user is positioning the axis node —
        # don't handle face clicks on the model
        if self._quick_setup_mode == "torque_set_axis":
            return True  # consume click so it doesn't select faces

        mesh_data = node.getMeshData()
        if mesh_data is None:
            return False
        verts = mesh_data.getVertices()
        indices = mesh_data.getIndices()
        if verts is None:
            return False

        adjacency = self._ensure_adjacency(node, verts, indices)

        if self._quick_setup_mode == "gravity_pick_bottom":
            from .fea.quick_setup import gravity_from_face
            result = gravity_from_face(
                verts, indices, face_index,
                force_magnitude=self._force_magnitude,
                adjacency=adjacency,
            )
            self._apply_quick_setup_result(node, result)
            self._quick_setup_mode = ""
            return True

        elif self._quick_setup_mode == "cantilever_pick_fixed":
            from .fea.quick_setup import cantilever
            result = cantilever(
                verts, indices, face_index,
                force_magnitude=self._force_magnitude,
                adjacency=adjacency,
            )
            self._apply_quick_setup_result(node, result)
            self._quick_setup_mode = ""
            return True

        return False

    def _apply_quick_setup_result(self, node, result: dict) -> None:
        """Apply quick setup results to the BC decorator."""
        bc = self._get_or_create_bc(node)

        if result.get("fixed_faces"):
            AddFixedFacesOperation(bc, result["fixed_faces"]).push()

        for fg in result.get("force_groups", []):
            fx, fy, fz = fg["force"]
            force = Vector(fx, fy, fz)
            AddForceGroupOperation(bc, fg["face_indices"], force).push()
            self._force_x = fx
            self._force_y = fy
            self._force_z = fz
            self._sync_magnitude_from_components()

        self.propertyChanged.emit()
        self._update_highlights()

    def _ensure_adjacency(self, node, verts, indices):
        """Get or build the face adjacency cache for a node (blocking).

        For click-based selection this is acceptable since it only runs once
        per model. For hover preview, use _ensure_adjacency_async instead.
        """
        if not _FACE_GROUP_ANALYZER_AVAILABLE:
            return None
        node_id = id(node)
        if self._adjacency_cache_node != node_id:
            self._adjacency_cache = build_face_adjacency(verts, indices)
            self._adjacency_cache_node = node_id
            self._adjacency_building = False
        return self._adjacency_cache

    def _ensure_adjacency_async(self, node, verts, indices) -> bool:
        """Kick off adjacency build in background if needed. Returns True if ready."""
        if not _FACE_GROUP_ANALYZER_AVAILABLE:
            return False
        node_id = id(node)
        if self._adjacency_cache_node == node_id and self._adjacency_cache is not None:
            return True  # already cached
        if self._adjacency_building:
            return False  # build in progress, not ready yet

        # Start background build
        self._adjacency_building = True
        self._adjacency_cache = None

        def _build():
            try:
                result = build_face_adjacency(verts, indices)
            except Exception:
                Logger.logException("w", "FEA: adjacency build failed")
                self._adjacency_building = False
                return

            def _deliver():
                # Only deliver if the node hasn't changed
                if id(node) == node_id:
                    self._adjacency_cache = result
                    self._adjacency_cache_node = node_id
                self._adjacency_building = False

            CuraApplication.getInstance().callLater(_deliver)

        thread = threading.Thread(target=_build, daemon=True)
        thread.start()
        return False

    def getOpenOptimizeDialog(self) -> bool:
        return False

    def setOpenOptimizeDialog(self, value) -> None:
        if value and self._extension:
            self._extension.enterOptimizePhase()

    # ── Inline phase flow properties ────────────────────────────────────────

    def getPhase(self) -> str:
        if self._extension:
            return self._extension.phase
        return "define"

    def setPhase(self, value: str) -> None:
        pass  # read-only; changes come via extension slots

    def getRunAnalysis(self) -> bool:
        return False

    def setRunAnalysis(self, value) -> None:
        if value and self._extension:
            self._extension.runAnalysis()
            self.propertyChanged.emit()

    def getCancelAnalysis(self) -> bool:
        return False

    def setCancelAnalysis(self, value) -> None:
        if value and self._extension:
            self._extension.cancelAnalysis()
            self.propertyChanged.emit()

    def getGoBackToDefine(self) -> bool:
        return False

    def setGoBackToDefine(self, value) -> None:
        if value and self._extension:
            self._extension.goBackToDefine()
            self.propertyChanged.emit()

    def getApplyModifierMeshes(self) -> bool:
        return False

    def setApplyModifierMeshes(self, value) -> None:
        if value and self._extension:
            self._extension.applyModifierMeshes()
            self.propertyChanged.emit()

    def getShowStressOverlay(self) -> bool:
        return False

    def setShowStressOverlay(self, value) -> None:
        if value and self._extension:
            self._extension.showStressOverlay()
            self.propertyChanged.emit()

    def getClearResults(self) -> bool:
        return False

    def setClearResults(self, value) -> None:
        if value and self._extension:
            self._extension.clearResults()
            self.propertyChanged.emit()

    def getMaterialName(self) -> str:
        if self._extension:
            return self._extension.materialName
        return "PLA"

    def setMaterialName(self, value: str) -> None:
        if self._extension:
            self._extension.materialName = str(value)
        self.propertyChanged.emit()

    def getMaterialSummary(self) -> str:
        if self._extension:
            return self._extension.materialSummary
        return ""

    def setMaterialSummary(self, value) -> None:
        pass  # read-only

    def getInfillPattern(self) -> str:
        if self._extension:
            return self._extension.infillPattern
        return "gyroid"

    def setInfillPattern(self, value: str) -> None:
        if self._extension:
            self._extension.infillPattern = str(value)
        self.propertyChanged.emit()

    def getSafetyFactor(self) -> float:
        if self._extension:
            return self._extension.safetyFactor
        return 2.0

    def setSafetyFactor(self, value) -> None:
        if self._extension:
            self._extension.safetyFactor = float(value)
        self.propertyChanged.emit()

    def getMeshResolution(self):
        if self._extension:
            return self._extension._mesh_resolution
        return 20

    def setMeshResolution(self, value) -> None:
        if self._extension:
            try:
                self._extension._mesh_resolution = int(float(value))
            except (ValueError, TypeError):
                # Legacy string values
                legacy = {"coarse": 10, "medium": 20, "fine": 40}
                self._extension._mesh_resolution = legacy.get(str(value), 20)
        self.propertyChanged.emit()

    def getAnalysisProgress(self) -> float:
        if self._extension:
            return self._extension.progress
        return 0.0

    def setAnalysisProgress(self, value) -> None:
        pass  # read-only

    def getAnalysisStage(self) -> str:
        if self._extension:
            return self._extension.analysisStage
        return ""

    def setAnalysisStage(self, value) -> None:
        pass  # read-only

    def getMaxStress(self) -> float:
        if self._extension:
            return self._extension.maxStress
        return 0.0

    def setMaxStress(self, value) -> None:
        pass

    def getMinStress(self) -> float:
        if self._extension:
            return self._extension.minStress
        return 0.0

    def setMinStress(self, value) -> None:
        pass

    def getSafetyFactorResult(self) -> float:
        """The computed safety factor from analysis results (distinct from the input setting)."""
        if self._extension and self._extension._results:
            return self._extension._results.get("safety_factor", 0.0)
        return 0.0

    def setSafetyFactorResult(self, value) -> None:
        pass

    def getConvergenceIterations(self) -> int:
        if self._extension:
            return self._extension.convergenceIterations
        return 0

    def setConvergenceIterations(self, value) -> None:
        pass

    def getSafetyVerdict(self) -> str:
        if self._extension:
            return self._extension.safetyVerdict
        return ""

    def setSafetyVerdict(self, value) -> None:
        pass

    def getHasResults(self) -> bool:
        if self._extension:
            return self._extension.hasResults
        return False

    def setHasResults(self, value) -> None:
        pass

    def getHasFullResults(self) -> bool:
        """True when full stress_field / zones are in memory (not just a summary)."""
        if self._extension:
            return self._extension.hasFullResults
        return False

    def setHasFullResults(self, value) -> None:
        pass

    def getActiveNodeName(self) -> str:
        """Return the display name of the active node for the RUNNING/REVIEW phases."""
        if self._extension:
            node = self._extension._node_cache.get(self._extension.activeNodeKey)
            if node is not None:
                return node.getName()
        selected = Selection.getSelectedObject(0)
        if selected is not None:
            return selected.getName()
        return ""

    def setActiveNodeName(self, value) -> None:
        pass

    def getMeshQuality(self) -> str:
        """Return mesh quality level: 'high', 'medium', 'low', or '' if no results."""
        if self._extension and self._extension._results:
            return self._extension._results.get("mesh_quality", "")
        return ""

    def setMeshQuality(self, value) -> None:
        pass

    def getMeshWarnings(self) -> str:
        """Return JSON-encoded list of mesh quality warnings."""
        if self._extension and self._extension._results:
            warnings = self._extension._results.get("mesh_warnings", [])
            return json.dumps(warnings) if warnings else "[]"
        return "[]"

    def setMeshWarnings(self, value) -> None:
        pass

    # ── Advanced settings (routed to extension) ──────────────────────────

    def getMinDensity(self) -> float:
        return self._extension._min_density if self._extension else 10.0

    def setMinDensity(self, value) -> None:
        if self._extension:
            self._extension._min_density = float(value)
        self.propertyChanged.emit()

    def getMaxDensity(self) -> float:
        return self._extension._max_density if self._extension else 80.0

    def setMaxDensity(self, value) -> None:
        if self._extension:
            self._extension._max_density = float(value)
        self.propertyChanged.emit()

    def getNumZones(self) -> int:
        return self._extension._num_zones if self._extension else 6

    def setNumZones(self, value) -> None:
        if self._extension:
            self._extension._num_zones = int(value)
        self.propertyChanged.emit()

    def getMaxIterations(self) -> int:
        return self._extension._max_iterations if self._extension else 5

    def setMaxIterations(self, value) -> None:
        if self._extension:
            self._extension._max_iterations = int(value)
        self.propertyChanged.emit()

    def getBondingCoeff(self) -> float:
        return self._extension._bonding_coeff if self._extension else 50.0

    def setBondingCoeff(self, value) -> None:
        if self._extension:
            self._extension._bonding_coeff = float(value)
        self.propertyChanged.emit()

    def getOptimizationMethod(self) -> str:
        return self._extension._optimization_method if self._extension else "heuristic"

    def setOptimizationMethod(self, value) -> None:
        if self._extension:
            self._extension._optimization_method = str(value)
        self.propertyChanged.emit()

    def getVolumeFraction(self) -> float:
        return self._extension._volume_fraction if self._extension else 50.0

    def setVolumeFraction(self, value) -> None:
        if self._extension:
            self._extension._volume_fraction = float(value)
        self.propertyChanged.emit()

    # ── Dependency management ──────────────────────────────────────────────

    def getDepsAvailable(self) -> bool:
        return self._extension._deps_available if self._extension else False

    def setDepsAvailable(self, value) -> None:
        pass

    def getErrorMessage(self) -> str:
        if self._extension:
            return self._extension.errorMessage
        return ""

    def setErrorMessage(self, value) -> None:
        pass

    def getStressOverlayVisible(self) -> bool:
        if self._extension:
            return self._extension.stressOverlayVisible
        return False

    def setStressOverlayVisible(self, value) -> None:
        pass  # read-only; controlled via ShowStressOverlay slot

    def getGoBackToOptimize(self) -> bool:
        return False

    def setGoBackToOptimize(self, value) -> None:
        if value and self._extension:
            self._extension.goBackToOptimize()
            self.propertyChanged.emit()

    def getHoverPreviewEnabled(self) -> bool:
        return self._hover_preview_enabled

    def setHoverPreviewEnabled(self, value) -> None:
        self._hover_preview_enabled = bool(value)
        if not self._hover_preview_enabled:
            self._hover_generation += 1  # invalidate pending hover
            self._hover_pending = False
            if self._hover_faces:
                self._hover_faces = []
                self._update_highlights()
        self.propertyChanged.emit()

    def getInstallDependencies(self) -> bool:
        return False

    def setInstallDependencies(self, value) -> None:
        if value and self._extension:
            self._extension.installDependencies()

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

    def getTorqueListModel(self) -> str:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return "[]"
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is None or not hasattr(bc, "getTorqueGroups"):
            return "[]"
        groups = bc.getTorqueGroups()
        result = []
        for i, tg in enumerate(groups):
            result.append({
                "index": i,
                "label": f"Torque {i + 1}: {tg.torque_magnitude:.1f}Nm ({len(tg.face_indices)} faces)",
                "axisX": round(tg.torque_axis.x, 3),
                "axisY": round(tg.torque_axis.y, 3),
                "axisZ": round(tg.torque_axis.z, 3),
            })
        return json.dumps(result)

    def getDeleteTorqueGroup(self) -> bool:
        return False

    def setDeleteTorqueGroup(self, value) -> None:
        index = int(value) if value is not True else -1
        if index < 0:
            return
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is not None and hasattr(bc, "removeTorqueGroup"):
            RemoveTorqueGroupOperation(bc, index).push()
            if self._editing_torque_index == index:
                self._force_handle.hide()
                self._editing_torque_index = -1
                self._mode = MODE_TORQUE
            elif self._editing_torque_index > index:
                self._editing_torque_index -= 1
            self.propertyChanged.emit()
            self._update_highlights()

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
                    # Enter rotate mode so the force direction gizmo is usable
                    self._mode = MODE_ROTATE
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
        self._update_highlights()

    # ── Torque edit index property ────────────────────────────────────────

    def getActiveTorqueIndex(self) -> int:
        return self._editing_torque_index

    def setActiveTorqueIndex(self, index: int) -> None:
        self._editing_torque_index = int(index)
        if self._editing_torque_index < 0:
            # Exit torque edit mode
            if self._mode == MODE_TORQUE_EDIT:
                self._mode = MODE_TORQUE
                self._force_handle.hide()
            self.propertyChanged.emit()
            self._update_highlights()
            return

        # Enter torque edit mode with gizmo at the torque group centroid
        selected = Selection.getSelectedObject(0)
        if selected is not None:
            bc = selected.callDecoration("getBoundaryConditions")
            if bc is not None:
                groups = bc.getTorqueGroups()
                if 0 <= self._editing_torque_index < len(groups):
                    tg = groups[self._editing_torque_index]
                    self._torque_magnitude = tg.torque_magnitude
                    self._mode = MODE_TORQUE_EDIT
                    # Clear force rotation state
                    self._rotating_group_index = -1
                    self._active_force_index = -1

                    mesh_data = selected.getMeshData()
                    if mesh_data is not None:
                        verts = mesh_data.getVertices()
                        indices = mesh_data.getIndices()
                        if verts is not None:
                            transform = selected.getWorldTransformation().getData()
                            verts_h = numpy.column_stack([verts, numpy.ones(len(verts))])
                            verts_world = (transform @ verts_h.T).T[:, :3]
                            centroid = compute_face_centroid(verts_world, indices, tg.face_indices)
                            bbox = selected.getBoundingBox()
                            if bbox:
                                diag = math.sqrt(bbox.width**2 + bbox.height**2 + bbox.depth**2)
                                scale = max(0.1, diag / 80.0)
                            else:
                                scale = 1.0
                            self._force_handle.show_at(
                                centroid, scale=scale,
                                axis_direction=tg.torque_axis,
                            )
        self.propertyChanged.emit()
        self._update_highlights()

    # ── Inline-edit trigger properties ─────────────────────────────────────

    def getUpdateForceAtIndex(self) -> str:
        return ""

    def setUpdateForceAtIndex(self, value) -> None:
        if not value:
            return
        try:
            data = json.loads(str(value))
        except (json.JSONDecodeError, TypeError):
            return
        index = int(data.get("index", -1))
        new_mag = float(data.get("magnitude", 0))

        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is None:
            return
        groups = bc.getForceGroups()
        if index < 0 or index >= len(groups):
            return

        fg = groups[index]
        old_mag = math.sqrt(fg.force.x**2 + fg.force.y**2 + fg.force.z**2)
        if old_mag < 1e-9:
            return
        scale = new_mag / old_mag
        old_force = Vector(fg.force.x, fg.force.y, fg.force.z)
        new_force = Vector(fg.force.x * scale, fg.force.y * scale, fg.force.z * scale)
        UpdateForceDirectionOperation(bc, index, old_force, new_force).push()

        self._force_x = new_force.x
        self._force_y = new_force.y
        self._force_z = new_force.z
        self._force_magnitude = new_mag

        self.propertyChanged.emit()
        self._update_highlights()

    def getUpdateTorqueAtIndex(self) -> str:
        return ""

    def setUpdateTorqueAtIndex(self, value) -> None:
        if not value:
            return
        try:
            data = json.loads(str(value))
        except (json.JSONDecodeError, TypeError):
            return
        index = int(data.get("index", -1))
        new_mag = float(data.get("magnitude", 0))

        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is None or not hasattr(bc, "getTorqueGroups"):
            return
        groups = bc.getTorqueGroups()
        if index < 0 or index >= len(groups):
            return

        old_mag = groups[index].torque_magnitude
        UpdateTorqueMagnitudeOperation(bc, index, old_mag, new_mag).push()
        self._torque_magnitude = new_mag
        self.propertyChanged.emit()
        self._update_highlights()

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
                ClearFixedFacesOperation(bc).push()
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
                RemoveForceGroupOperation(bc, index).push()
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
            # Track model transforms so overlays follow the model
            selected = Selection.getSelectedObject(0)
            if selected is not None:
                selected.transformationChanged.connect(self._onModelTransformChanged)
                self._tracked_node = selected
            return False

        if event.type == Event.ToolDeactivateEvent:
            self._bc_highlight.clear()
            self._force_handle.hide()
            self._remove_torque_axis_node()
            self._quick_setup_mode = ""
            self._torque_captured_position = None
            self._torque_captured_direction = None
            self._centroid_cache = None  # free memory
            self._rotating_group_index = -1
            self._editing_torque_index = -1
            self._hover_faces = []
            self._hover_generation += 1  # invalidate any pending hover computation
            self._hover_pending = False
            # Disconnect transform tracking
            if hasattr(self, "_tracked_node") and self._tracked_node is not None:
                try:
                    self._tracked_node.transformationChanged.disconnect(self._onModelTransformChanged)
                except Exception:
                    pass
                self._tracked_node = None
            return False

        # ── Only allow face interaction in DEFINE phase ──────────────────
        current_phase = self._extension.phase if self._extension else "define"
        if current_phase != "define":
            # Clear hover when leaving define phase
            if self._hover_faces:
                self._hover_faces = []
                self._update_highlights()
            return False

        # ── Hover preview: highlight face under cursor ─────────────────────
        if event.type == Event.MouseMoveEvent and self._mode in (MODE_FIXED, MODE_FORCE, MODE_TORQUE):
            self._update_hover_preview(event)
            # Don't return True — let the event propagate for camera control

        # ── Torque edit mode: handle ring dragging for torque axis ──────────
        if self._mode == MODE_TORQUE_EDIT and self._editing_torque_index >= 0:
            return self._handle_torque_edit_event(event)

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

            # Quick setup: if in a pick-face mode, handle it first
            if self._quick_setup_mode:
                Logger.log("d", "FEA: Quick setup mode '%s' active, handling face %d",
                           self._quick_setup_mode, face_index)
                if self._handle_quick_setup_click(picked_node, face_index):
                    return True
                Logger.log("w", "FEA: Quick setup click handler returned False")

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
                        RemoveFixedFacesOperation(bc, to_remove).push()
                    if to_add:
                        AddFixedFacesOperation(bc, to_add).push()
                else:
                    AddFixedFacesOperation(bc, face_indices_to_add).push()
                self.propertyChanged.emit()
                self._update_highlights()

            elif self._mode in (MODE_FORCE, MODE_TORQUE):
                if alt:
                    existing = set(self._current_face_selection)
                    for fi in face_indices_to_add:
                        if fi in existing:
                            self._current_face_selection.remove(fi)
                        else:
                            self._current_face_selection.append(fi)
                else:
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

                # Capture force direction before drag for undo
                selected = Selection.getSelectedObject(0)
                if selected is not None:
                    bc = selected.callDecoration("getBoundaryConditions")
                    if bc is not None:
                        groups = bc.getForceGroups()
                        if 0 <= self._rotating_group_index < len(groups):
                            fg = groups[self._rotating_group_index]
                            self._force_direction_before_drag = Vector(
                                fg.force.x, fg.force.y, fg.force.z
                            )
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

            # Push an undo operation for the completed drag
            selected = Selection.getSelectedObject(0)
            if selected is not None and self._force_direction_before_drag is not None:
                bc = selected.callDecoration("getBoundaryConditions")
                if bc is not None:
                    groups = bc.getForceGroups()
                    if 0 <= self._rotating_group_index < len(groups):
                        fg = groups[self._rotating_group_index]
                        UpdateForceDirectionOperation(
                            bc, self._rotating_group_index,
                            self._force_direction_before_drag, fg.force
                        ).push()
            self._force_direction_before_drag = None

            self.propertyChanged.emit()
            return True

        return False

    # ── Torque axis edit event handler ───────────────────────────────────

    def _handle_torque_edit_event(self, event: Event) -> bool:
        """Handle mouse events when editing a torque axis direction."""
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
                self._torque_edit_drag_active = True
                self._torque_edit_angle = 0.0

                # Capture the axis before drag for undo
                selected = Selection.getSelectedObject(0)
                if selected is not None:
                    bc = selected.callDecoration("getBoundaryConditions")
                    if bc is not None:
                        groups = bc.getTorqueGroups()
                        if 0 <= self._editing_torque_index < len(groups):
                            ax = groups[self._editing_torque_index].torque_axis
                            self._torque_axis_before_drag = Vector(ax.x, ax.y, ax.z)

                return True
            return False

        if event.type == Event.MouseMoveEvent and self._torque_edit_drag_active:
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

            self._torque_edit_angle += direction * angle

            # Apply rotation to the torque axis direction
            selected = Selection.getSelectedObject(0)
            if selected is None:
                return False
            bc = selected.callDecoration("getBoundaryConditions")
            if bc is None:
                return False
            groups = bc.getTorqueGroups()
            if self._editing_torque_index >= len(groups):
                return False

            tg = groups[self._editing_torque_index]
            new_axis = rotate_vector(tg.torque_axis, axis, direction * angle)
            # Normalise the axis to avoid drift
            length = math.sqrt(new_axis.x**2 + new_axis.y**2 + new_axis.z**2)
            if length > 1e-9:
                new_axis = Vector(new_axis.x / length, new_axis.y / length, new_axis.z / length)
            tg.torque_axis = new_axis

            # Refresh the axis line visualization to show the new direction
            self._force_handle.show_at(
                self._force_handle.center,
                scale=self._force_handle._outer_radius / 12.5,  # preserve current scale
                axis_direction=new_axis,
            )

            self.setDragStart(event.x, event.y)
            self.propertyChanged.emit()
            self._update_highlights()
            return True

        if event.type == Event.MouseReleaseEvent and self._torque_edit_drag_active:
            self._torque_edit_drag_active = False
            self.setDragPlane(None)
            self.setLockedAxis(ToolHandle.NoAxis)
            self._torque_edit_angle = 0.0

            # Push an undo operation for the completed drag
            selected = Selection.getSelectedObject(0)
            if selected is not None and self._torque_axis_before_drag is not None:
                bc = selected.callDecoration("getBoundaryConditions")
                if bc is not None:
                    groups = bc.getTorqueGroups()
                    if 0 <= self._editing_torque_index < len(groups):
                        tg = groups[self._editing_torque_index]
                        UpdateTorqueAxisOperation(
                            bc, self._editing_torque_index,
                            self._torque_axis_before_drag, tg.torque_axis
                        ).push()
            self._torque_axis_before_drag = None

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

        # Use cached world-space centroids when possible — avoids recomputing
        # the full vertex transform on every mouse move (O(n_verts) → O(1)).
        transform = node.getWorldTransformation().getData()
        node_id = id(node)
        mesh_id = id(mesh_data)
        transform_bytes = transform.tobytes()

        if (self._centroid_cache is not None
                and self._centroid_cache_node_id == node_id
                and self._centroid_cache_mesh_id == mesh_id
                and self._centroid_cache_transform_bytes == transform_bytes):
            centroids = self._centroid_cache
        else:
            verts_h = numpy.column_stack([verts, numpy.ones(len(verts))])
            verts_world = (transform @ verts_h.T).T[:, :3]

            if indices is not None:
                centroids = (verts_world[indices[:, 0]] +
                             verts_world[indices[:, 1]] +
                             verts_world[indices[:, 2]]) / 3.0
            else:
                n_tris = len(verts_world) // 3
                verts_reshaped = verts_world[:n_tris * 3].reshape(n_tris, 3, 3)
                centroids = verts_reshaped.mean(axis=1)

            self._centroid_cache = centroids
            self._centroid_cache_node_id = node_id
            self._centroid_cache_mesh_id = mesh_id
            self._centroid_cache_transform_bytes = transform_bytes

        picked = numpy.array([picked_position.x, picked_position.y, picked_position.z])
        if not numpy.all(numpy.isfinite(picked)):
            return None

        distances = numpy.linalg.norm(centroids - picked, axis=1)
        if len(distances) == 0:
            return None
        return int(numpy.nanargmin(distances))

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

        # Apply the force along the OUTWARD normal (pressing into the surface
        # from the outside, which is the common real-world load direction).
        # The surface normal points outward, so the force acts in the normal
        # direction — e.g. pressing down on a top face, pushing in on a side.
        force_dir = Vector(normal.x, normal.y, normal.z)
        mag = self._force_magnitude
        force = Vector(force_dir.x * mag, force_dir.y * mag, force_dir.z * mag)

        bc = self._get_or_create_bc(selected)
        AddForceGroupOperation(bc, list(self._current_face_selection), force).push()

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

    def _confirmTorqueGroup(self) -> None:
        """Confirm selected faces as a torque application region."""
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

        # Torque axis = average surface normal of selected faces
        normal = compute_face_normal(verts, indices, self._current_face_selection)

        bc = self._get_or_create_bc(selected)
        AddTorqueGroupOperation(
            bc, list(self._current_face_selection), normal, self._torque_magnitude
        ).push()

        # Enter torque edit mode so user can adjust the axis direction
        group_index = len(bc.getTorqueGroups()) - 1
        self._editing_torque_index = group_index
        self._current_face_selection.clear()
        self._mode = MODE_TORQUE_EDIT

        # Show rotation rings at the torque centroid (in world space)
        transform = selected.getWorldTransformation().getData()
        verts_h = numpy.column_stack([verts, numpy.ones(len(verts))])
        verts_world = (transform @ verts_h.T).T[:, :3]
        centroid = compute_face_centroid(
            verts_world, indices, bc.getTorqueGroups()[group_index].face_indices
        )

        # Scale rings relative to model bounding box
        bbox = selected.getBoundingBox()
        if bbox:
            diag = math.sqrt(bbox.width**2 + bbox.height**2 + bbox.depth**2)
            scale = max(0.1, diag / 80.0)
        else:
            scale = 1.0
        self._force_handle.show_at(
            centroid, scale=scale,
            axis_direction=normal,
        )

        self.propertyChanged.emit()
        self._update_highlights()

    def _clearAllBCs(self) -> None:
        selected = Selection.getSelectedObject(0)
        if selected is None:
            return
        bc = selected.callDecoration("getBoundaryConditions")
        if bc is not None:
            ClearAllBCsOperation(bc).push()
        self._current_face_selection.clear()
        self._force_handle.hide()
        self._rotating_group_index = -1
        self._editing_torque_index = -1
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
            RemoveForceGroupOperation(bc, index).push()
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
            ClearFixedFacesOperation(bc).push()
            self.propertyChanged.emit()
            self._update_highlights()

    def _onModelTransformChanged(self, *args) -> None:
        """Called when the tracked model node's transform changes (move/scale/rotate).

        Updates BC highlight overlay to match new position and removes
        the stress overlay (which has pre-baked world-space vertices that
        are now invalid — re-run analysis to regenerate).
        """
        self._centroid_cache = None  # invalidate — positions changed
        self._update_highlights()

        # Remove stress overlay — its world-space vertices are now wrong
        if self._extension and self._extension.stressOverlayVisible:
            node = Selection.getSelectedObject(0)
            if node is not None:
                try:
                    from .visualization.stress_overlay import StressOverlayManager
                    StressOverlayManager.remove_overlay(node)
                    self._extension._stress_overlay_visible = False
                    self._extension.stressOverlayVisibleChanged.emit()
                except Exception:
                    pass

    def _update_highlights(self) -> None:
        # Skip highlight updates during analysis to prevent
        # "RenderBatch.addItem without mesh" spam on the main thread
        current_phase = self._extension.phase if self._extension else "define"
        if current_phase == "running":
            self._bc_highlight.clear()
            self._force_handle.hide()
            return

        node = Selection.getSelectedObject(0)
        if node is None or not isinstance(node, CuraSceneNode):
            self._bc_highlight.clear()
            return

        bc = node.callDecoration("getBoundaryConditions")
        has_bc = bc is not None and bc.hasAnyBC()
        has_pending = len(self._current_face_selection) > 0

        has_hover = len(self._hover_faces) > 0

        if not has_bc and not has_pending and not has_hover:
            self._bc_highlight.clear()
            return

        try:
            current_phase = self._extension.phase if self._extension else "define"
            self._bc_highlight.update_visualization(
                node, bc,
                pending_faces=self._current_face_selection if has_pending else None,
                active_force_index=self._active_force_index,
                active_torque_index=self._editing_torque_index,
                hover_faces=self._hover_faces if has_hover else None,
                phase=current_phase,
            )
        except Exception:
            Logger.logException("w", "FEA Infill: Failed to update BC highlight overlay")
            self._bc_highlight.clear()

    def _update_hover_preview(self, event) -> None:
        """Update the hover face preview on mouse move (debounced + non-blocking)."""
        if not self._hover_preview_enabled:
            return
        # Re-entrancy guard: if _update_highlights triggers a signal cascade
        # that re-enters this method, bail out to prevent infinite recursion.
        if self._hover_in_progress:
            return
        self._hover_in_progress = True
        try:
            self._do_hover_preview(event)
        finally:
            self._hover_in_progress = False

    def _do_hover_preview(self, event) -> None:
        """Inner hover preview — resolves face under cursor with debounced BFS."""
        if self._selection_pass is None:
            self._selection_pass = Application.getInstance().getRenderer().getRenderPass("selection")
            if not self._selection_pass:
                return

        picked_node = self._controller.getScene().findObject(
            self._selection_pass.getIdAtPosition(event.x, event.y)
        )
        if not picked_node or not isinstance(picked_node, CuraSceneNode):
            if self._hover_faces:
                self._hover_faces = []
                self._hover_generation += 1  # invalidate any pending result
                self._update_highlights()
            return

        if picked_node.callDecoration("isNonPrintingMesh"):
            if self._hover_faces:
                self._hover_faces = []
                self._hover_generation += 1
                self._update_highlights()
            return

        if self._picking_pass is None:
            self._picking_pass = Application.getInstance().getRenderer().getRenderPass("picking_selected")
            if not self._picking_pass:
                return

        picked_position = self._picking_pass.getPickedPosition(event.x, event.y)
        face_index = self._find_closest_face(picked_node, picked_position)
        if face_index is None:
            if self._hover_faces:
                self._hover_faces = []
                self._hover_generation += 1
                self._update_highlights()
            return

        # Single mode: instant highlight, no BFS needed
        if self._selection_mode == "single" or not _FACE_GROUP_ANALYZER_AVAILABLE:
            hover = [face_index]
            if hover != self._hover_faces:
                self._hover_faces = hover
                self._hover_generation += 1
                self._update_highlights()
            return

        # Group mode: show single-face highlight immediately for responsiveness,
        # then debounce the expensive BFS expansion.
        if self._hover_faces != [face_index] and not self._hover_pending:
            # Show instant single-face preview while we wait
            self._hover_faces = [face_index]
            self._update_highlights()

        # Bump generation to invalidate any in-flight computation
        self._hover_generation += 1
        generation = self._hover_generation

        # Record debounce timestamp
        self._hover_debounce_time = time.monotonic()

        # Schedule the debounced dispatch via callLater with a delay.
        # We use a closure over generation to detect staleness.
        node_ref = picked_node  # prevent GC during delay
        debounce_ms = self._hover_debounce_ms

        def _debounced_check():
            # If generation has moved on, this event is stale — discard
            if self._hover_generation != generation:
                return
            # Check if enough time has passed since last mouse move
            elapsed_ms = (time.monotonic() - self._hover_debounce_time) * 1000
            if elapsed_ms < debounce_ms * 0.8:
                # Mouse moved again recently; don't start yet
                return
            self._dispatch_hover_computation(node_ref, face_index, generation)

        # Use callLater to schedule the check after the debounce period.
        # callLater runs on the main thread's event loop, so it won't block.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(debounce_ms, _debounced_check)

    def _dispatch_hover_computation(self, node, face_index: int, generation: int) -> None:
        """Start the BFS face-group expansion in a background thread.

        Results are delivered back to the main thread via callLater.
        If generation has changed by delivery time, the result is discarded.
        """
        if self._hover_generation != generation:
            return  # already stale

        mesh_data = node.getMeshData()
        if mesh_data is None:
            return
        verts = mesh_data.getVertices()
        indices = mesh_data.getIndices()
        if verts is None:
            return

        # Check adjacency availability; kick off async build if needed
        node_id = id(node)
        if self._adjacency_cache_node != node_id or self._adjacency_cache is None:
            # Adjacency not ready for this node — start async build
            self._ensure_adjacency_async(node, verts, indices)
            # Keep showing single-face highlight until adjacency is ready
            return

        adjacency = self._adjacency_cache
        self._hover_pending = True
        selection_mode = self._selection_mode

        def _compute():
            try:
                if selection_mode == "flat":
                    result = find_coplanar_group(verts, indices, face_index, adjacency)
                elif selection_mode == "hole":
                    result = find_hole_surface(verts, indices, face_index, adjacency)
                elif selection_mode == "cylinder":
                    result = find_cylinder_surface(verts, indices, face_index, adjacency)
                else:
                    result = [face_index]
            except Exception:
                Logger.logException("w", "FEA: hover group BFS failed")
                result = [face_index]

            def _deliver():
                self._hover_pending = False
                # Discard if generation has moved on (mouse moved to new face)
                if self._hover_generation != generation:
                    return
                if result != self._hover_faces:
                    self._hover_faces = result
                    self._update_highlights()

            CuraApplication.getInstance().callLater(_deliver)

        thread = threading.Thread(target=_compute, daemon=True)
        thread.start()

    def getRequiredExtraRenderingPasses(self) -> list:
        return ["picking_selected"]
