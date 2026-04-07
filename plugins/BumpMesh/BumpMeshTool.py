# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

import weakref
from enum import IntEnum
from typing import Optional

import numpy
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QFileDialog

from UM.Event import Event
from UM.Logger import Logger
from UM.Mesh.MeshData import MeshData
from UM.Scene.Selection import Selection
from UM.Tool import Tool

from cura.CuraApplication import CuraApplication

from .DisplacementJob import DisplacementJob
from .MeshDisplaceOperation import MeshDisplaceOperation

# Maximum estimated face count before we refuse to run (prevents OOM)
_MAX_ESTIMATED_FACES = 5_000_000

# Debounce delay in ms before running a preview after parameter changes
_PREVIEW_DEBOUNCE_MS = 350


class BumpMeshTool(Tool):
    """Tool plugin that applies displacement/texture mapping to 3D model surfaces.

    Provides live preview: parameter changes automatically re-run the displacement
    pipeline and update the mesh. Changes are temporary until confirmed; closing
    the tool without confirming reverts the mesh to its original state.
    """

    class State(IntEnum):
        READY = 1
        PROCESSING = 2
        ERROR = 3

    def __init__(self) -> None:
        super().__init__()

        self._shortcut_key = Qt.Key.Key_B
        self._controller = self.getController()

        # Texture data
        self._texture_path: str = ""
        self._texture_image: Optional[QImage] = None
        self._texture_data: Optional[numpy.ndarray] = None  # (H, W) float32 [0,1]

        # Displacement parameters
        self._projection_mode: int = 0  # 0=Triplanar, 1=Cubic, 2=Cylindrical, 3=Spherical, 4=Planar
        self._amplitude: float = 1.0
        self._scale_u: float = 1.0
        self._scale_v: float = 1.0
        self._offset_u: float = 0.0
        self._offset_v: float = 0.0
        self._rotation: float = 0.0
        self._subdivision_level: int = 1
        self._mask_angle: float = 0.0
        self._smoothing: int = 0

        # State
        self._state: int = BumpMeshTool.State.READY
        self._error_message: str = ""
        self._displacement_job: Optional[DisplacementJob] = None

        # Preview state
        self._preview_active: bool = False
        self._has_unconfirmed_changes: bool = False
        self._preview_original_mesh: Optional[MeshData] = None  # mesh before any preview
        self._preview_node_ref: Optional[weakref.ref] = None
        self._pending_preview: bool = False  # re-run preview after current job finishes

        # Debounce timer for preview
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._preview_timer.timeout.connect(self._runPreview)

        self.setExposedProperties(
            "TexturePath", "ProjectionMode", "Amplitude",
            "ScaleU", "ScaleV", "OffsetU", "OffsetV", "Rotation",
            "SubdivisionLevel", "MaskAngle", "Smoothing",
            "State", "HasTexture", "EstimatedVertices", "ErrorMessage",
            "HasUnconfirmedChanges"
        )

        Selection.selectionChanged.connect(self._onSelectionChanged)

    # --- Exposed Property Getters/Setters ---

    def getTexturePath(self) -> str:
        return self._texture_path

    def setTexturePath(self, path: str) -> None:
        if path != self._texture_path:
            self._texture_path = path
            self.propertyChanged.emit()

    def getProjectionMode(self) -> int:
        return self._projection_mode

    def setProjectionMode(self, mode: int) -> None:
        if mode != self._projection_mode:
            self._projection_mode = int(mode)
            self.propertyChanged.emit()
            self._schedulePreview()

    def getAmplitude(self) -> float:
        return self._amplitude

    def setAmplitude(self, value: float) -> None:
        if value != self._amplitude:
            self._amplitude = float(value)
            self.propertyChanged.emit()
            self._schedulePreview()

    def getScaleU(self) -> float:
        return self._scale_u

    def setScaleU(self, value: float) -> None:
        if value != self._scale_u:
            self._scale_u = float(value)
            self.propertyChanged.emit()
            self._schedulePreview()

    def getScaleV(self) -> float:
        return self._scale_v

    def setScaleV(self, value: float) -> None:
        if value != self._scale_v:
            self._scale_v = float(value)
            self.propertyChanged.emit()
            self._schedulePreview()

    def getOffsetU(self) -> float:
        return self._offset_u

    def setOffsetU(self, value: float) -> None:
        if value != self._offset_u:
            self._offset_u = float(value)
            self.propertyChanged.emit()
            self._schedulePreview()

    def getOffsetV(self) -> float:
        return self._offset_v

    def setOffsetV(self, value: float) -> None:
        if value != self._offset_v:
            self._offset_v = float(value)
            self.propertyChanged.emit()
            self._schedulePreview()

    def getRotation(self) -> float:
        return self._rotation

    def setRotation(self, value: float) -> None:
        if value != self._rotation:
            self._rotation = float(value)
            self.propertyChanged.emit()
            self._schedulePreview()

    def getSubdivisionLevel(self) -> int:
        return self._subdivision_level

    def setSubdivisionLevel(self, value: int) -> None:
        value = int(value)
        if value != self._subdivision_level:
            self._subdivision_level = value
            self.propertyChanged.emit()
            self._schedulePreview()

    def getMaskAngle(self) -> float:
        return self._mask_angle

    def setMaskAngle(self, value: float) -> None:
        if value != self._mask_angle:
            self._mask_angle = float(value)
            self.propertyChanged.emit()
            self._schedulePreview()

    def getSmoothing(self) -> int:
        return self._smoothing

    def setSmoothing(self, value: int) -> None:
        value = int(value)
        if value != self._smoothing:
            self._smoothing = value
            self.propertyChanged.emit()
            self._schedulePreview()

    def getState(self) -> int:
        return int(self._state)

    def getHasTexture(self) -> bool:
        return self._texture_data is not None

    def getErrorMessage(self) -> str:
        return self._error_message

    def getHasUnconfirmedChanges(self) -> bool:
        return self._has_unconfirmed_changes

    def getEstimatedVertices(self) -> int:
        node = self._getPreviewNode()
        mesh = self._preview_original_mesh if self._preview_original_mesh else None
        if node is None or mesh is None:
            node = self._getSelectedNode()
            if node is None:
                return 0
            mesh = node.getMeshData()
        if mesh is None:
            return 0
        face_count = mesh.getFaceCount()
        if face_count == 0 and mesh.getVertices() is not None:
            face_count = len(mesh.getVertices()) // 3
        estimated_faces = face_count * (4 ** self._subdivision_level)
        return estimated_faces * 3

    # --- Actions (triggered from QML) ---

    def loadTexture(self) -> None:
        """Open file dialog and load a displacement map image."""
        file_path, _ = QFileDialog.getOpenFileName(
            None,
            "Load Displacement Map",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All Files (*)"
        )
        if not file_path:
            return

        img = QImage(file_path)
        if img.isNull():
            Logger.log("e", "Failed to load displacement map: %s", file_path)
            self._error_message = "Failed to load image file."
            self._state = BumpMeshTool.State.ERROR
            self.propertyChanged.emit()
            return

        self._texture_image = img
        self._texture_path = file_path
        self._texture_data = self._imageToNumpyGrayscale(img)
        self._error_message = ""
        if self._state == BumpMeshTool.State.ERROR:
            self._state = BumpMeshTool.State.READY
        self.propertyChanged.emit()
        Logger.log("i", "Loaded displacement map: %s (%dx%d)", file_path, img.width(), img.height())

        # Auto-run preview with the new texture
        self._schedulePreview()

    def confirmDisplacement(self) -> None:
        """Confirm the current preview as a permanent change (pushed to undo stack)."""
        if not self._has_unconfirmed_changes:
            return

        node = self._getPreviewNode()
        if node is None or self._preview_original_mesh is None:
            return

        current_mesh = node.getMeshData()
        if current_mesh is None:
            return

        # Push a single undo operation: original -> confirmed result
        op = MeshDisplaceOperation(node, self._preview_original_mesh, current_mesh)
        op.push()

        # Update preview baseline to the confirmed state
        self._preview_original_mesh = current_mesh
        self._has_unconfirmed_changes = False
        self.propertyChanged.emit()

    def revertDisplacement(self) -> None:
        """Revert to the mesh state before any preview changes."""
        self._preview_timer.stop()
        self._pending_preview = False
        self._revertPreview()

    # --- Event handling ---

    def event(self, event: Event) -> bool:
        super().event(event)

        if event.type == Event.ToolActivateEvent:
            self._onToolActivated()
        elif event.type == Event.ToolDeactivateEvent:
            self._onToolDeactivated()

        return False

    # --- Internal: Preview lifecycle ---

    def _onToolActivated(self) -> None:
        """Cache the current mesh as the preview baseline."""
        self._preview_active = True
        node = self._getSelectedNode()
        if node is not None:
            self._preview_original_mesh = node.getMeshData()
            self._preview_node_ref = weakref.ref(node)

    def _onToolDeactivated(self) -> None:
        """Revert unconfirmed preview changes when leaving the tool."""
        self._preview_timer.stop()
        self._pending_preview = False
        self._preview_active = False

        if self._has_unconfirmed_changes:
            self._revertPreview()

        self._preview_original_mesh = None
        self._preview_node_ref = None

    def _revertPreview(self) -> None:
        """Restore the mesh to its pre-preview state (no undo operation)."""
        node = self._getPreviewNode()
        if node is None or self._preview_original_mesh is None:
            return

        node.setMeshData(self._preview_original_mesh)
        self._has_unconfirmed_changes = False
        self._error_message = ""
        self._state = BumpMeshTool.State.READY
        self.propertyChanged.emit()

        CuraApplication.getInstance().getController().getScene().sceneChanged.emit(node)

    def _getPreviewNode(self) -> Optional[object]:
        """Get the node being previewed, or None if it was garbage-collected."""
        if self._preview_node_ref is not None:
            return self._preview_node_ref()
        return None

    # --- Internal: Preview scheduling ---

    def _schedulePreview(self) -> None:
        """Schedule a preview update after debounce delay."""
        if not self._preview_active or self._texture_data is None:
            return
        if self._getPreviewNode() is None:
            return

        if self._state == BumpMeshTool.State.PROCESSING:
            # A job is running — flag to re-run when it finishes
            self._pending_preview = True
            return

        self._preview_timer.start()

    def _runPreview(self) -> None:
        """Run the displacement pipeline as a preview (result set directly, no undo op)."""
        node = self._getPreviewNode()
        if node is None or self._texture_data is None:
            return

        # Always displace from the ORIGINAL cached mesh, not the current preview
        mesh = self._preview_original_mesh
        if mesh is None:
            return

        # OOM guard
        face_count = mesh.getFaceCount()
        if face_count == 0 and mesh.getVertices() is not None:
            face_count = len(mesh.getVertices()) // 3
        estimated_faces = face_count * (4 ** self._subdivision_level)
        if estimated_faces > _MAX_ESTIMATED_FACES:
            self._error_message = (
                "Too many faces (~%dM). Lower subdivision level or simplify mesh."
                % (estimated_faces // 1_000_000)
            )
            self._state = BumpMeshTool.State.ERROR
            self.propertyChanged.emit()
            return

        # Copy mesh data on main thread for thread safety
        vertices = mesh.getVertices()
        if vertices is None:
            return
        vertices = vertices.copy()
        indices = mesh.getIndices()
        if indices is not None:
            indices = indices.copy()

        params = {
            "projection_mode": self._projection_mode,
            "amplitude": self._amplitude,
            "scale_u": self._scale_u,
            "scale_v": self._scale_v,
            "offset_u": self._offset_u,
            "offset_v": self._offset_v,
            "rotation": self._rotation,
            "subdivision_level": self._subdivision_level,
            "mask_angle": self._mask_angle,
            "smoothing": self._smoothing,
        }

        self._state = BumpMeshTool.State.PROCESSING
        self._error_message = ""
        self.propertyChanged.emit()

        self._displacement_job = DisplacementJob(node, vertices, indices, self._texture_data, params)
        self._displacement_job.finished.connect(self._onPreviewFinished)
        self._displacement_job.start()

    def _onPreviewFinished(self, job) -> None:
        """Handle preview job completion — set mesh directly (no undo operation)."""
        if job != self._displacement_job:
            return

        self._displacement_job = None
        self._state = BumpMeshTool.State.READY

        error = job.getError()
        if error:
            self._state = BumpMeshTool.State.ERROR
            self._error_message = error
            self.propertyChanged.emit()
            return

        result_mesh = job.getResultMesh()
        if result_mesh is None:
            self._state = BumpMeshTool.State.ERROR
            self._error_message = "Displacement produced no result."
            self.propertyChanged.emit()
            return

        node = job.getNode()
        if node is None:
            self.propertyChanged.emit()
            return

        # Set mesh directly (no undo operation — this is a preview)
        node.setMeshData(result_mesh)
        self._has_unconfirmed_changes = True
        self._error_message = ""
        self.propertyChanged.emit()

        CuraApplication.getInstance().getController().getScene().sceneChanged.emit(node)

        # If parameters changed during processing, run another preview
        if self._pending_preview:
            self._pending_preview = False
            self._schedulePreview()

    # --- Internal: Utilities ---

    def _getSelectedNode(self):
        """Get the currently selected scene node, or None."""
        if not Selection.hasSelection():
            return None
        if Selection.getCount() != 1:
            return None
        node = Selection.getSelectedObject(0)
        if node is None or node.getMeshData() is None:
            return None
        return node

    def _onSelectionChanged(self) -> None:
        """Update preview baseline when selection changes."""
        if self._state == BumpMeshTool.State.PROCESSING:
            return

        # If selection changes while we have unconfirmed changes, revert old node first
        if self._has_unconfirmed_changes:
            self._revertPreview()

        # Set up preview for the new selection
        node = self._getSelectedNode()
        if node is not None and self._preview_active:
            self._preview_original_mesh = node.getMeshData()
            self._preview_node_ref = weakref.ref(node)
        else:
            self._preview_original_mesh = None
            self._preview_node_ref = None

        self._state = BumpMeshTool.State.READY
        self._error_message = ""
        self.propertyChanged.emit()

    @staticmethod
    def _imageToNumpyGrayscale(img: QImage) -> numpy.ndarray:
        """Convert a QImage to a float32 grayscale numpy array [0, 1].

        Uses vectorized numpy operations instead of per-pixel Python loops.
        """
        img = img.convertToFormat(QImage.Format.Format_RGB32)
        width = img.width()
        height = img.height()

        ptr = img.bits()
        ptr.setsize(height * width * 4)
        arr = numpy.frombuffer(ptr, dtype=numpy.uint8).reshape((height, width, 4)).copy()

        # RGB32 layout is [B, G, R, A] on little-endian (Qt stores as 0xAARRGGBB)
        b = arr[:, :, 0].astype(numpy.float32)
        g = arr[:, :, 1].astype(numpy.float32)
        r = arr[:, :, 2].astype(numpy.float32)

        grayscale = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
        return grayscale
