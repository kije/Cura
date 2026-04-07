# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

import os
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
        self._projection_mode: int = 0  # 0=Triplanar, 1=Cubic, 2=Cylindrical, 3=Spherical, 4-6=Planar
        self._amplitude: float = 1.0
        self._scale_u: float = 1.0
        self._scale_v: float = 1.0
        self._offset_u: float = 0.0
        self._offset_v: float = 0.0
        self._rotation: float = 0.0
        self._subdivision_level: int = 2
        self._subdivision_mode: int = 0  # 0=Uniform, 1=Adaptive
        self._target_edge_length: float = 1.0  # mm, for adaptive mode
        self._mask_angle: float = 0.0
        self._smoothing: int = 0

        # State
        self._state: int = BumpMeshTool.State.READY
        self._error_message: str = ""
        self._displacement_job: Optional[DisplacementJob] = None

        # Preview state
        self._preview_active: bool = False
        self._has_unconfirmed_changes: bool = False
        self._preview_original_mesh: Optional[MeshData] = None
        self._preview_node_ref: Optional[weakref.ref] = None
        self._pending_preview: bool = False

        # Debounce timer for preview
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._preview_timer.timeout.connect(self._runPreview)

        self.setExposedProperties(
            "TexturePath", "ProjectionMode", "Amplitude",
            "ScaleU", "ScaleV", "OffsetU", "OffsetV", "Rotation",
            "SubdivisionLevel", "SubdivisionMode", "TargetEdgeLength",
            "MaskAngle", "Smoothing",
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

    def getSubdivisionMode(self) -> int:
        return self._subdivision_mode

    def setSubdivisionMode(self, value: int) -> None:
        value = int(value)
        if value != self._subdivision_mode:
            self._subdivision_mode = value
            self.propertyChanged.emit()
            self._schedulePreview()

    def getTargetEdgeLength(self) -> float:
        return self._target_edge_length

    def setTargetEdgeLength(self, value: float) -> None:
        value = float(value)
        if value != self._target_edge_length:
            self._target_edge_length = max(0.1, value)
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
        if self._subdivision_mode == 0:  # Uniform
            estimated_faces = face_count * (4 ** self._subdivision_level)
        else:  # Adaptive — rough estimate
            estimated_faces = face_count * 4  # conservative estimate
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

        self._loadTextureFromPath(file_path)

    def loadBuiltinTexture(self, name: str) -> None:
        """Load a built-in texture by name."""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        texture_path = os.path.join(plugin_dir, "textures", name)
        if not os.path.exists(texture_path):
            Logger.log("e", "Built-in texture not found: %s", texture_path)
            return
        self._loadTextureFromPath(texture_path)

    def _loadTextureFromPath(self, file_path: str) -> None:
        """Load a displacement map image from a file path."""
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
        Logger.log("i", "Loaded displacement map: %s (%dx%d)", file_path, img.width(), img.height())

        # Auto-compute sensible parameters based on mesh and texture
        self._autoComputeParameters()

        self.propertyChanged.emit()

        # Auto-run preview with the new texture
        self._schedulePreview()

    def autoComputeParameters(self) -> None:
        """Recalculate auto parameters (callable from QML)."""
        self._autoComputeParameters()
        self.propertyChanged.emit()
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

        op = MeshDisplaceOperation(node, self._preview_original_mesh, current_mesh)
        op.push()

        self._preview_original_mesh = current_mesh
        self._has_unconfirmed_changes = False
        self.propertyChanged.emit()

    def revertDisplacement(self) -> None:
        """Revert to the mesh state before any preview changes."""
        self._preview_timer.stop()
        self._pending_preview = False
        self._revertPreview()

    # --- Auto-compute ---

    def _autoComputeParameters(self) -> None:
        """Calculate sensible default parameters from the current mesh and texture."""
        node = self._getPreviewNode()
        if node is None:
            node = self._getSelectedNode()
        if node is None:
            return

        mesh = self._preview_original_mesh if self._preview_original_mesh else node.getMeshData()
        if mesh is None:
            return

        vertices = mesh.getVertices()
        if vertices is None or len(vertices) == 0:
            return

        # Mesh bounding box
        bbox_min = vertices.min(axis=0)
        bbox_max = vertices.max(axis=0)
        bbox_size = bbox_max - bbox_min
        bbox_diagonal = float(numpy.linalg.norm(bbox_size))

        if bbox_diagonal < 0.01:
            return

        # Face count
        face_count = mesh.getFaceCount()
        if face_count == 0:
            face_count = len(vertices) // 3

        # Auto amplitude: ~1-2% of bbox diagonal, clamped
        self._amplitude = round(float(numpy.clip(bbox_diagonal * 0.015, 0.2, 3.0)), 1)

        # Auto scale: target ~3-5 texture repeats across the object
        auto_scale = max(1.0, bbox_diagonal / 15.0)

        # Texture aspect ratio compensation
        if self._texture_image is not None and self._texture_image.height() > 0:
            tex_aspect = self._texture_image.width() / self._texture_image.height()
            self._scale_u = round(auto_scale, 1)
            self._scale_v = round(auto_scale / tex_aspect, 1)
        else:
            self._scale_u = round(auto_scale, 1)
            self._scale_v = round(auto_scale, 1)

        # Auto subdivision: based on average edge length vs desired detail
        if face_count > 0:
            # Approximate average edge length from face count and surface area
            approx_face_area = (bbox_size[0] * bbox_size[1] + bbox_size[1] * bbox_size[2] +
                                bbox_size[0] * bbox_size[2]) * 2.0 / max(face_count, 1)
            avg_edge_length = float(numpy.sqrt(max(approx_face_area, 0.001) * 2.0))

            # Target: edges small enough to resolve the displacement detail
            target_edge = max(0.3, self._amplitude * 0.4)

            # Calculate uniform subdivision level needed
            level = 0
            for lv in range(5):
                if avg_edge_length / (2 ** lv) <= target_edge:
                    level = lv
                    break
                level = lv
            self._subdivision_level = min(level, 3)

            # Adaptive target edge length
            self._target_edge_length = round(max(0.2, target_edge), 1)

        Logger.log("i", "Auto-computed: amplitude=%.1f, scale=%.1f/%.1f, subdiv=%d, edge=%.1f",
                   self._amplitude, self._scale_u, self._scale_v,
                   self._subdivision_level, self._target_edge_length)

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
        self._preview_active = True
        node = self._getSelectedNode()
        if node is not None:
            self._preview_original_mesh = node.getMeshData()
            self._preview_node_ref = weakref.ref(node)

    def _onToolDeactivated(self) -> None:
        self._preview_timer.stop()
        self._pending_preview = False
        self._preview_active = False

        if self._has_unconfirmed_changes:
            self._revertPreview()

        self._preview_original_mesh = None
        self._preview_node_ref = None

    def _revertPreview(self) -> None:
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
        if self._preview_node_ref is not None:
            return self._preview_node_ref()
        return None

    # --- Internal: Preview scheduling ---

    def _schedulePreview(self) -> None:
        if not self._preview_active or self._texture_data is None:
            return
        if self._getPreviewNode() is None:
            return

        if self._state == BumpMeshTool.State.PROCESSING:
            self._pending_preview = True
            return

        self._preview_timer.start()

    def _runPreview(self) -> None:
        node = self._getPreviewNode()
        if node is None or self._texture_data is None:
            return

        mesh = self._preview_original_mesh
        if mesh is None:
            return

        # OOM guard
        face_count = mesh.getFaceCount()
        if face_count == 0 and mesh.getVertices() is not None:
            face_count = len(mesh.getVertices()) // 3
        if self._subdivision_mode == 0:  # Uniform
            estimated_faces = face_count * (4 ** self._subdivision_level)
        else:
            estimated_faces = face_count * 4  # rough estimate for adaptive
        if estimated_faces > _MAX_ESTIMATED_FACES:
            self._error_message = (
                "Too many faces (~%dM). Lower subdivision or simplify mesh."
                % (estimated_faces // 1_000_000)
            )
            self._state = BumpMeshTool.State.ERROR
            self.propertyChanged.emit()
            return

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
            "subdivision_mode": self._subdivision_mode,
            "target_edge_length": self._target_edge_length,
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

        node.setMeshData(result_mesh)
        self._has_unconfirmed_changes = True
        self._error_message = ""
        self.propertyChanged.emit()

        CuraApplication.getInstance().getController().getScene().sceneChanged.emit(node)

        if self._pending_preview:
            self._pending_preview = False
            self._schedulePreview()

    # --- Internal: Utilities ---

    def _getSelectedNode(self):
        if not Selection.hasSelection():
            return None
        if Selection.getCount() != 1:
            return None
        node = Selection.getSelectedObject(0)
        if node is None or node.getMeshData() is None:
            return None
        return node

    def _onSelectionChanged(self) -> None:
        if self._state == BumpMeshTool.State.PROCESSING:
            return

        if self._has_unconfirmed_changes:
            self._revertPreview()

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
        """Convert a QImage to a float32 grayscale numpy array [0, 1]."""
        img = img.convertToFormat(QImage.Format.Format_RGB32)
        width = img.width()
        height = img.height()

        ptr = img.bits()
        ptr.setsize(height * width * 4)
        arr = numpy.frombuffer(ptr, dtype=numpy.uint8).reshape((height, width, 4)).copy()

        b = arr[:, :, 0].astype(numpy.float32)
        g = arr[:, :, 1].astype(numpy.float32)
        r = arr[:, :, 2].astype(numpy.float32)

        grayscale = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
        return grayscale
