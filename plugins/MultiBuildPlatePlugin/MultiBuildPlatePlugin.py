# Copyright (c) 2024 Ultimaker B.V.
# MultiBuildPlatePlugin is released under the terms of the LGPLv3 or higher.

import os
from typing import List

from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, pyqtProperty

from UM.Extension import Extension
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Camera import Camera
from UM.Scene.Selection import Selection

from cura.CuraApplication import CuraApplication


class MultiBuildPlatePlugin(QObject, Extension):
    """Extension plugin that exposes Cura's built-in multi build plate functionality.

    The backend (MultiBuildPlateModel, CuraSceneController, BuildPlateDecorator,
    per-plate slicing) is fully implemented in Cura but has no user-facing UI.
    This plugin adds a collapsible Build Plate panel (similar to the Object List)
    that lets users:
      - See all build plates and the objects on each one
      - Switch plates by clicking a plate header
      - Add new empty plates with the "+" button
      - Move objects between plates by dragging them onto a plate header
    """

    # Emitted whenever the objects in the scene / selection change so that
    # QML can refresh its per-plate object lists.
    objectsChanged = pyqtSignal()

    # Emitted when pendingMaxPlate changes (active plate or actual max changed).
    pendingMaxPlateChanged = pyqtSignal()

    def __init__(self, parent=None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self._application = CuraApplication.getInstance()
        self._panel = None

        # Mark the feature as enabled so future Cura code / scripts can gate on it.
        self._application.getPreferences().addPreference("cura/use_multi_build_plate", True)

        self.setMenuName("Multi Build Plate")
        self.addMenuItem("Next Build Plate", self._nextBuildPlate)
        self.addMenuItem("Previous Build Plate", self._previousBuildPlate)
        self.addMenuItem("Move Selection to New Build Plate", self._moveSelectionToNewBuildPlate)

        self._application.initializationFinished.connect(self._onInitializationFinished)

    def _onInitializationFinished(self) -> None:
        """Create the persistent build plate panel once the QML engine exists."""
        if self._panel is not None:
            return

        qml_path = os.path.join(
            PluginRegistry.getInstance().getPluginPath("MultiBuildPlatePlugin"),
            "qml",
            "BuildPlatePanel.qml",
        )
        self._panel = self._application.createQmlComponent(qml_path, {"manager": self})
        if self._panel is None:
            Logger.log("e", "MultiBuildPlatePlugin: failed to create BuildPlatePanel")

        # Scene / selection changes → refresh per-plate object lists in QML.
        self._application.getController().getScene().sceneChanged.connect(self._onSceneChanged)
        Selection.selectionChanged.connect(self._onSelectionChanged)

        # Track active plate and actual max so pendingMaxPlate stays current.
        mbp = self._application.getMultiBuildPlateModel()
        mbp.maxBuildPlateChanged.connect(self.pendingMaxPlateChanged)
        mbp.activeBuildPlateChanged.connect(self.pendingMaxPlateChanged)

    # ------------------------------------------------------------------
    # Internal scene change handlers
    # ------------------------------------------------------------------

    def _onSceneChanged(self, source=None) -> None:
        if isinstance(source, Camera):
            return
        self.objectsChanged.emit()

    def _onSelectionChanged(self) -> None:
        self.objectsChanged.emit()

    # ------------------------------------------------------------------
    # Extensions menu item callbacks
    # ------------------------------------------------------------------

    def _nextBuildPlate(self) -> None:
        model = self._application.getMultiBuildPlateModel()
        active = model.activeBuildPlate
        if active < model.maxBuildPlate:
            self._application.getCuraSceneController().setActiveBuildPlate(active + 1)

    def _previousBuildPlate(self) -> None:
        model = self._application.getMultiBuildPlateModel()
        active = model.activeBuildPlate
        if active > 0:
            self._application.getCuraSceneController().setActiveBuildPlate(active - 1)

    def _moveSelectionToNewBuildPlate(self) -> None:
        if not Selection.hasSelection():
            Logger.log("d", "MultiBuildPlatePlugin: no selection, nothing to move")
            return
        model = self._application.getMultiBuildPlateModel()
        new_plate = model.maxBuildPlate + 1
        self._application._cura_actions.setBuildPlateForSelection(new_plate)
        self._application.getCuraSceneController().setActiveBuildPlate(new_plate)

    # ------------------------------------------------------------------
    # Properties & slots for QML
    # ------------------------------------------------------------------

    @pyqtProperty(int, notify=pendingMaxPlateChanged)
    def pendingMaxPlate(self) -> int:
        """Highest plate number to show in the panel.

        Equal to max(activeBuildPlate, maxBuildPlate) so that an empty plate
        switched to via addBuildPlate() stays visible until it is populated.
        """
        model = self._application.getMultiBuildPlateModel()
        active = max(0, model.activeBuildPlate)
        return max(active, model.maxBuildPlate)

    @pyqtSlot(int)
    def setActiveBuildPlate(self, plate_nr: int) -> None:
        self._application.getCuraSceneController().setActiveBuildPlate(plate_nr)

    @pyqtSlot()
    def addBuildPlate(self) -> None:
        """Switch to a new empty build plate."""
        model = self._application.getMultiBuildPlateModel()
        new_plate = model.maxBuildPlate + 1
        self._application.getCuraSceneController().setActiveBuildPlate(new_plate)
        self.pendingMaxPlateChanged.emit()

    @pyqtSlot()
    def moveSelectionToNewBuildPlate(self) -> None:
        self._moveSelectionToNewBuildPlate()

    @pyqtSlot(int, result="QVariantList")
    def getObjectsForPlate(self, plate_number: int) -> List[dict]:
        """Return a list of {name, model_index, selected} dicts for the given plate."""
        objects_model = self._application.getObjectsModel()
        result = []
        # getItem(i) is the public API used by CuraSceneController
        i = 0
        while True:
            item = objects_model.getItem(i)
            if not item:
                break
            if item.get("buildplate_number") == plate_number:
                result.append({
                    "name": item.get("name", ""),
                    "model_index": i,
                    "selected": bool(item.get("selected", False)),
                })
            i += 1
        return result

    @pyqtSlot(int, int)
    def moveObjectToBuildPlate(self, model_index: int, plate_number: int) -> None:
        """Move the object at ObjectsModel[model_index] to plate_number."""
        objects_model = self._application.getObjectsModel()
        item = objects_model.getItem(model_index)
        if not item:
            Logger.log("w", f"MultiBuildPlatePlugin: invalid model_index {model_index}")
            return
        node = item.get("node")
        if node is None:
            return

        # Preserve the existing selection so the user doesn't lose it.
        previous = list(Selection.getAllSelectedObjects())
        Selection.clear()
        Selection.add(node)
        self._application._cura_actions.setBuildPlateForSelection(plate_number)
        Selection.clear()
        for n in previous:
            Selection.add(n)

    @pyqtSlot(int)
    def selectObject(self, model_index: int) -> None:
        """Select a single object in the 3D scene by its ObjectsModel index."""
        objects_model = self._application.getObjectsModel()
        item = objects_model.getItem(model_index)
        if not item:
            return
        node = item.get("node")
        if node is None:
            return
        Selection.clear()
        Selection.add(node)
