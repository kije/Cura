# Copyright (c) 2025 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

from enum import IntEnum
from typing import List, Optional

from PyQt6.QtCore import Qt, QObject, QAbstractListModel, QModelIndex, pyqtSignal, pyqtProperty, pyqtSlot, pyqtEnum
from PyQt6.QtGui import QImage, QPainter, QColor, QBrush

from UM.View.GL.OpenGL import OpenGL
from UM.View.GL.Texture import Texture


class BlendMode(QObject):
    @pyqtEnum
    class Mode(IntEnum):
        NORMAL = 0
        MULTIPLY = 1
        ADD = 2


class PaintLayer:
    """A single paint layer with its own texture and properties."""

    def __init__(self, name: str, width: int, height: int) -> None:
        self.name: str = name
        self.visible: bool = True
        self.opacity: float = 1.0
        self.blend_mode: BlendMode.Mode = BlendMode.Mode.NORMAL
        self.locked: bool = False

        self.texture: Texture = OpenGL.getInstance().createTexture(width, height)
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(0)
        self.texture.setImage(image)

    def getImage(self) -> Optional[QImage]:
        return self.texture.getImage()


class PaintLayerStack(QAbstractListModel):
    """Manages ordered list of paint layers for a paint type on an object.

    Exposes a QAbstractListModel interface so QML can directly bind to the
    layer list for display in the LayerPanel.
    """

    NameRole = Qt.ItemDataRole.UserRole + 1
    VisibleRole = Qt.ItemDataRole.UserRole + 2
    OpacityRole = Qt.ItemDataRole.UserRole + 3
    BlendModeRole = Qt.ItemDataRole.UserRole + 4
    LockedRole = Qt.ItemDataRole.UserRole + 5
    ActiveRole = Qt.ItemDataRole.UserRole + 6

    MAX_LAYERS = 8

    layersChanged = pyqtSignal()
    activeLayerChanged = pyqtSignal()
    isolatedModeChanged = pyqtSignal()

    def __init__(self, base_width: int, base_height: int, parent: QObject = None) -> None:
        super().__init__(parent)
        self._layers: List[PaintLayer] = []
        self._base_width: int = base_width
        self._base_height: int = base_height
        self._active_layer_index: int = 0
        self._isolated_mode: bool = False
        self._flattened_cache: Optional[QImage] = None
        self._cache_dirty: bool = True

        # Create default layer
        self.addLayer("Layer 1")

    # --- QAbstractListModel interface ---

    def roleNames(self):
        return {
            self.NameRole: b"layerName",
            self.VisibleRole: b"layerVisible",
            self.OpacityRole: b"layerOpacity",
            self.BlendModeRole: b"layerBlendMode",
            self.LockedRole: b"layerLocked",
            self.ActiveRole: b"layerActive",
        }

    def rowCount(self, parent=QModelIndex()):
        return len(self._layers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._layers):
            return None
        layer = self._layers[index.row()]
        if role == self.NameRole:
            return layer.name
        elif role == self.VisibleRole:
            return layer.visible
        elif role == self.OpacityRole:
            return layer.opacity
        elif role == self.BlendModeRole:
            return int(layer.blend_mode)
        elif role == self.LockedRole:
            return layer.locked
        elif role == self.ActiveRole:
            return index.row() == self._active_layer_index
        return None

    # --- Layer management ---

    @pyqtSlot(str)
    def addLayer(self, name: str = "") -> Optional[PaintLayer]:
        if len(self._layers) >= self.MAX_LAYERS:
            return None

        if not name:
            name = f"Layer {len(self._layers) + 1}"

        layer = PaintLayer(name, self._base_width, self._base_height)
        insert_index = self._active_layer_index + 1 if self._layers else 0

        self.beginInsertRows(QModelIndex(), insert_index, insert_index)
        self._layers.insert(insert_index, layer)
        self.endInsertRows()

        self._active_layer_index = insert_index
        self._cache_dirty = True
        self.layersChanged.emit()
        self.activeLayerChanged.emit()
        return layer

    @pyqtSlot(int)
    def removeLayer(self, index: int) -> None:
        if index < 0 or index >= len(self._layers):
            return
        if len(self._layers) <= 1:
            return  # Always keep at least one layer

        self.beginRemoveRows(QModelIndex(), index, index)
        self._layers.pop(index)
        self.endRemoveRows()

        if self._active_layer_index >= len(self._layers):
            self._active_layer_index = len(self._layers) - 1
        elif self._active_layer_index > index:
            self._active_layer_index -= 1

        self._cache_dirty = True
        self.layersChanged.emit()
        self.activeLayerChanged.emit()

    @pyqtSlot(int, int)
    def moveLayer(self, from_index: int, to_index: int) -> None:
        if (from_index < 0 or from_index >= len(self._layers) or
                to_index < 0 or to_index >= len(self._layers) or
                from_index == to_index):
            return

        # QAbstractListModel requires special handling for move
        if from_index < to_index:
            self.beginMoveRows(QModelIndex(), from_index, from_index, QModelIndex(), to_index + 1)
        else:
            self.beginMoveRows(QModelIndex(), from_index, from_index, QModelIndex(), to_index)

        layer = self._layers.pop(from_index)
        self._layers.insert(to_index, layer)
        self.endMoveRows()

        # Update active index to track the same layer
        if self._active_layer_index == from_index:
            self._active_layer_index = to_index
        elif from_index < self._active_layer_index <= to_index:
            self._active_layer_index -= 1
        elif to_index <= self._active_layer_index < from_index:
            self._active_layer_index += 1

        self._cache_dirty = True
        self.layersChanged.emit()
        self.activeLayerChanged.emit()

    def getActiveLayer(self) -> Optional[PaintLayer]:
        if 0 <= self._active_layer_index < len(self._layers):
            return self._layers[self._active_layer_index]
        return None

    def getActiveLayerTexture(self) -> Optional[Texture]:
        layer = self.getActiveLayer()
        return layer.texture if layer else None

    @pyqtSlot(int)
    def setActiveLayer(self, index: int) -> None:
        if 0 <= index < len(self._layers) and index != self._active_layer_index:
            self._active_layer_index = index
            self.dataChanged.emit(self.index(0), self.index(len(self._layers) - 1), [self.ActiveRole])
            self.activeLayerChanged.emit()

    @pyqtProperty(int, notify=activeLayerChanged)
    def activeLayerIndex(self) -> int:
        return self._active_layer_index

    @pyqtProperty(int, notify=layersChanged)
    def layerCount(self) -> int:
        return len(self._layers)

    # --- Isolated edit mode ---

    @pyqtProperty(bool, notify=isolatedModeChanged)
    def isolatedMode(self) -> bool:
        return self._isolated_mode

    @pyqtSlot(bool)
    def setIsolatedMode(self, enabled: bool) -> None:
        if enabled != self._isolated_mode:
            self._isolated_mode = enabled
            self._cache_dirty = True
            self.isolatedModeChanged.emit()

    @pyqtSlot()
    def toggleIsolatedMode(self) -> None:
        self.setIsolatedMode(not self._isolated_mode)

    # --- Compositing ---

    def flatten(self) -> QImage:
        """Composite all visible layers into a single image.

        In isolated mode, only the active layer is composited.
        Uses QPainter composition modes for blending.
        """
        if not self._cache_dirty and self._flattened_cache is not None:
            return self._flattened_cache

        result = QImage(self._base_width, self._base_height, QImage.Format.Format_ARGB32)
        result.fill(0)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        layers_to_composite = self._layers
        if self._isolated_mode:
            active = self.getActiveLayer()
            layers_to_composite = [active] if active else []

        # Composite bottom-to-top (layers[0] is bottom, layers[-1] is top)
        for layer in layers_to_composite:
            if not layer.visible:
                continue

            image = layer.getImage()
            if image is None:
                continue

            # Set blend mode
            match layer.blend_mode:
                case BlendMode.Mode.NORMAL:
                    painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceOrDestination)
                case BlendMode.Mode.MULTIPLY:
                    painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceAndDestination)
                case BlendMode.Mode.ADD:
                    painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceOrDestination)

            painter.setOpacity(layer.opacity)
            painter.drawImage(0, 0, image)

        painter.end()

        self._flattened_cache = result
        self._cache_dirty = False
        return result

    def invalidateCache(self) -> None:
        """Mark the flattened cache as dirty, forcing recomposite on next flatten()."""
        self._cache_dirty = True

    @pyqtSlot(int)
    def mergeDown(self, index: int) -> None:
        """Merge the layer at index into the layer below it."""
        if index <= 0 or index >= len(self._layers):
            return

        upper = self._layers[index]
        lower = self._layers[index - 1]

        upper_image = upper.getImage()
        lower_image = lower.getImage()
        if upper_image is None or lower_image is None:
            return

        painter = QPainter(lower_image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setCompositionMode(QPainter.CompositionMode.RasterOp_SourceOrDestination)
        painter.setOpacity(upper.opacity)
        painter.drawImage(0, 0, upper_image)
        painter.end()

        lower.texture.updateImagePart(lower_image.rect())

        self.removeLayer(index)
        self._cache_dirty = True

    @pyqtSlot()
    def flattenAll(self) -> None:
        """Flatten all layers into a single layer."""
        if len(self._layers) <= 1:
            return

        flattened_image = self.flatten()

        # Remove all layers except the bottom one
        while len(self._layers) > 1:
            self.beginRemoveRows(QModelIndex(), len(self._layers) - 1, len(self._layers) - 1)
            self._layers.pop()
            self.endRemoveRows()

        # Replace bottom layer's image with flattened result
        bottom = self._layers[0]
        bottom.name = "Flattened"
        painter = QPainter(bottom.getImage())
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(0, 0, flattened_image)
        painter.end()
        bottom.texture.updateImagePart(bottom.getImage().rect())

        self._active_layer_index = 0
        self._cache_dirty = True
        self.layersChanged.emit()
        self.activeLayerChanged.emit()

    @pyqtSlot(int, str)
    def setLayerName(self, index: int, name: str) -> None:
        if 0 <= index < len(self._layers):
            self._layers[index].name = name
            self.dataChanged.emit(self.index(index), self.index(index), [self.NameRole])

    @pyqtSlot(int, bool)
    def setLayerVisible(self, index: int, visible: bool) -> None:
        if 0 <= index < len(self._layers):
            self._layers[index].visible = visible
            self._cache_dirty = True
            self.dataChanged.emit(self.index(index), self.index(index), [self.VisibleRole])
            self.layersChanged.emit()

    @pyqtSlot(int, float)
    def setLayerOpacity(self, index: int, opacity: float) -> None:
        if 0 <= index < len(self._layers):
            self._layers[index].opacity = max(0.0, min(1.0, opacity))
            self._cache_dirty = True
            self.dataChanged.emit(self.index(index), self.index(index), [self.OpacityRole])
            self.layersChanged.emit()

    @pyqtSlot(int, bool)
    def setLayerLocked(self, index: int, locked: bool) -> None:
        if 0 <= index < len(self._layers):
            self._layers[index].locked = locked
            self.dataChanged.emit(self.index(index), self.index(index), [self.LockedRole])
