# Copyright (c) 2024 Ultimaker B.V.
# MultiBuildPlatePlugin is released under the terms of the LGPLv3 or higher.

import os
import sys
from typing import Any, Dict, List

from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, pyqtProperty

from UM.Extension import Extension
from UM.Logger import Logger
from UM.Message import Message
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Camera import Camera
from UM.Scene.Selection import Selection

from cura.CuraApplication import CuraApplication


class BuildPlateMenuActions:
    """Action object registered with Cura's sidebar settings context menu.

    The sidebar context menu calls each method with a ``kwargs`` dict that
    currently contains ``{"key": settingKey}`` (the setting that was
    right-clicked).  We accept but ignore the key — our operations act on
    the 3D-scene selection, not on a setting value.
    """

    def __init__(self, plugin: "MultiBuildPlatePlugin") -> None:
        self._plugin = plugin

    def moveToNextPlate(self, kwargs: Dict[str, Any]) -> None:
        self._plugin._nextBuildPlate()

    def moveToPreviousPlate(self, kwargs: Dict[str, Any]) -> None:
        self._plugin._previousBuildPlate()

    def moveToNewPlate(self, kwargs: Dict[str, Any]) -> None:
        self._plugin._moveSelectionToNewBuildPlate()


class MultiBuildPlatePlugin(QObject, Extension):
    """Extension plugin that exposes Cura's built-in multi build plate functionality.

    The backend (MultiBuildPlateModel, CuraSceneController, BuildPlateDecorator,
    per-plate slicing) is fully implemented in Cura but has no user-facing UI.
    This plugin integrates with every available Cura UI extension point:

      • Extension menu       — Next / Previous / Move to New Plate
      • createQmlComponent   — collapsible Build Plates panel (bottom-left)
      • Sidebar context menu — Move to Next / Previous / New Plate (right-click
                               on any print setting)
      • UM.Message toasts    — confirmation after every plate operation
      • QML Shortcut items   — Ctrl+] / Ctrl+[ / Ctrl+Shift+N (in the panel QML)
    """

    # Emitted whenever the objects in the scene / selection change so that
    # QML can refresh its per-plate object lists.
    objectsChanged = pyqtSignal()

    # Emitted when pendingMaxPlate changes (active plate or actual max changed).
    pendingMaxPlateChanged = pyqtSignal()

    # Emitted when canAddNewPlate changes (active-plate object count changed).
    canAddNewPlateChanged = pyqtSignal()

    def __init__(self, parent=None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self._application = CuraApplication.getInstance()
        self._panel = None
        self._navigator = None
        self._menu_actions = BuildPlateMenuActions(self)

        # Mark the feature as enabled so future Cura code / scripts can gate on it.
        self._application.getPreferences().addPreference("cura/use_multi_build_plate", True)

        # ── Extension menu ───────────────────────────────────────────────────
        self.setMenuName("Multi Build Plate")
        self.addMenuItem("Next Build Plate",                    self._nextBuildPlate)
        self.addMenuItem("Previous Build Plate",                self._previousBuildPlate)
        self.addMenuItem("Move Selection to New Build Plate",   self._moveSelectionToNewBuildPlate)

        self._application.initializationFinished.connect(self._onInitializationFinished)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _onInitializationFinished(self) -> None:
        """Wire up all extension points once the QML engine and scene exist."""
        if self._panel is not None:
            return

        # ── createQmlComponent — the Build Plates panel ──────────────────────
        qml_path = os.path.join(
            PluginRegistry.getInstance().getPluginPath("MultiBuildPlatePlugin"),
            "qml",
            "BuildPlatePanel.qml",
        )
        self._panel = self._application.createQmlComponent(qml_path, {"manager": self})
        if self._panel is None:
            Logger.log("e", "MultiBuildPlatePlugin: failed to create BuildPlatePanel")

        # ── saveButton additional component — prev/next navigator ────────────
        # Appears to the left of the Slice button in ActionPanelWidget.
        nav_qml_path = os.path.join(
            PluginRegistry.getInstance().getPluginPath("MultiBuildPlatePlugin"),
            "qml",
            "BuildPlateNavigator.qml",
        )
        self._navigator = self._application.createQmlComponent(nav_qml_path, {"manager": self})
        if self._navigator is not None:
            self._application.addAdditionalComponent("saveButton", self._navigator)
        else:
            Logger.log("e", "MultiBuildPlatePlugin: failed to create BuildPlateNavigator")

        # Scene / selection changes → refresh per-plate object lists in QML.
        self._application.getController().getScene().sceneChanged.connect(self._onSceneChanged)
        Selection.selectionChanged.connect(self._onSelectionChanged)

        # Track active-plate and max so pendingMaxPlate and canAddNewPlate stay current.
        mbp = self._application.getMultiBuildPlateModel()
        mbp.maxBuildPlateChanged.connect(self.pendingMaxPlateChanged)
        mbp.maxBuildPlateChanged.connect(self.canAddNewPlateChanged)
        mbp.activeBuildPlateChanged.connect(self.pendingMaxPlateChanged)
        mbp.activeBuildPlateChanged.connect(self.canAddNewPlateChanged)

        # ── Sidebar context menu ─────────────────────────────────────────────
        self._registerSidebarMenuItems()

        # ── Temporary 3MF round-trip workaround ──────────────────────────────
        # TODO: remove once ThreeMFWriter/ThreeMFReader are patched upstream and
        #       the plugin ships with a proper Cura PR merged.
        self._apply_3mf_patches()

    def _registerSidebarMenuItems(self) -> None:
        """Add three items to the print-settings right-click context menu."""
        app = self._application
        app.addSidebarCustomMenuItem({
            "name":      "Move to Next Plate",
            "icon_name": "ArrowDoubleCircleRight",
            "actions":   ["moveToNextPlate"],
            "menu_item": self._menu_actions,
        })
        app.addSidebarCustomMenuItem({
            "name":      "Move to Previous Plate",
            "icon_name": "ArrowReset",
            "actions":   ["moveToPreviousPlate"],
            "menu_item": self._menu_actions,
        })
        app.addSidebarCustomMenuItem({
            "name":      "Move to New Plate",
            "icon_name": "Plus",
            "actions":   ["moveToNewPlate"],
            "menu_item": self._menu_actions,
        })

    # ------------------------------------------------------------------
    # Temporary 3MF round-trip workaround
    # ------------------------------------------------------------------

    def _apply_3mf_patches(self) -> None:
        """Monkey-patch ThreeMFWriter.write() so all build plates are saved.

        TEMPORARY — for testing while awaiting a proper upstream fix in
        ThreeMFWriter.py and ThreeMFReader.py.

        Strategy (writer side):
          Before each write, copy every node's real plate number into
          node.metadata["buildplate_number"] and temporarily reset its
          BuildPlateDecorator to the current active plate so the existing
          active-plate filter passes for every node.  After write, restore
          the original plate numbers and remove the temporary metadata.

        Strategy (reader side):
          The reader already drops unknown settings into node.metadata.
          _applyBuildPlateMetadata() (called from _onSceneChanged) picks
          up metadata["buildplate_number"] on any newly loaded node and
          applies it via setBuildPlateNumber.
        """
        # Find the ThreeMFWriter class in the already-loaded modules.
        writer_class = None
        for mod in sys.modules.values():
            klass = getattr(mod, "ThreeMFWriter", None)
            if klass is not None and hasattr(klass, "_convertUMNodeToSavitarNode"):
                writer_class = klass
                break

        if writer_class is None:
            Logger.log("w", "MultiBuildPlatePlugin: ThreeMFWriter not yet loaded — "
                            "3MF patch will not be applied")
            return

        original_write = writer_class.write

        def _patched_write(writer_self, stream, nodes, *args, **kwargs):
            from cura.CuraApplication import CuraApplication
            from UM.Scene.Camera import Camera as _Camera

            app      = CuraApplication.getInstance()
            scene    = app.getController().getScene()
            active   = app.getMultiBuildPlateModel().activeBuildPlate

            # Collect every scene node that has a BuildPlate decorator.
            patched_nodes: List[tuple] = []

            def _collect(node) -> None:
                if isinstance(node, _Camera):
                    return
                plate = node.callDecoration("getBuildPlateNumber")
                if plate is not None:
                    patched_nodes.append((node, plate))
                for child in node.getChildren():
                    _collect(child)

            _collect(scene.getRoot())

            # Temporarily make every node look like it lives on the active
            # plate (so the filter in _convertUMNodeToSavitarNode passes),
            # and stash the real plate number in metadata so the writer's
            # existing metadata loop persists it to the 3MF file.
            for node, plate in patched_nodes:
                node.metadata["buildplate_number"] = str(plate)
                node.callDecoration("setBuildPlateNumber", active)

            try:
                return original_write(writer_self, stream, nodes, *args, **kwargs)
            finally:
                for node, plate in patched_nodes:
                    node.callDecoration("setBuildPlateNumber", plate)
                    node.metadata.pop("buildplate_number", None)

        writer_class.write = _patched_write
        Logger.log("d", "MultiBuildPlatePlugin: patched ThreeMFWriter for multi-plate 3MF saving")

    def _applyBuildPlateMetadata(self) -> None:
        """After loading a 3MF, restore build plate assignments from metadata.

        The ThreeMFReader stores unknown settings into node.metadata.  Our
        patched writer saves each node's plate number as metadata key
        "buildplate_number", so after loading we find it there and apply it.
        """
        scene = self._application.getController().getScene()
        for node in scene.getRoot().getAllChildren():
            raw = node.metadata.get("buildplate_number")
            if raw is None:
                continue
            try:
                # The reader stores the raw Savitar Setting object; real
                # string values come from .value.  Handle both cases.
                val = raw.value if hasattr(raw, "value") else str(raw)
                plate_nr = int(val)
                node.callDecoration("setBuildPlateNumber", plate_nr)
                del node.metadata["buildplate_number"]
            except (ValueError, TypeError, AttributeError):
                pass

    # ------------------------------------------------------------------
    # Internal scene change handlers
    # ------------------------------------------------------------------

    def _onSceneChanged(self, source=None) -> None:
        if isinstance(source, Camera):
            return
        self._applyBuildPlateMetadata()
        self.objectsChanged.emit()
        self.canAddNewPlateChanged.emit()

    def _onSelectionChanged(self) -> None:
        self.objectsChanged.emit()

    # ------------------------------------------------------------------
    # Extension menu item callbacks
    # ------------------------------------------------------------------

    def _nextBuildPlate(self) -> None:
        model = self._application.getMultiBuildPlateModel()
        active = model.activeBuildPlate
        if active < model.maxBuildPlate:
            target = active + 1
            self._application.getCuraSceneController().setActiveBuildPlate(target)
            self._showSwitchToast(target)

    def _previousBuildPlate(self) -> None:
        model = self._application.getMultiBuildPlateModel()
        active = model.activeBuildPlate
        if active > 0:
            target = active - 1
            self._application.getCuraSceneController().setActiveBuildPlate(target)
            self._showSwitchToast(target)

    def _moveSelectionToNewBuildPlate(self) -> None:
        if not Selection.hasSelection():
            Logger.log("d", "MultiBuildPlatePlugin: no selection, nothing to move")
            return
        model = self._application.getMultiBuildPlateModel()
        new_plate = model.maxBuildPlate + 1
        self._application._cura_actions.setBuildPlateForSelection(new_plate)
        self._application.getCuraSceneController().setActiveBuildPlate(new_plate)
        Message(
            title="Build Plates",
            text=f"Selection moved to Plate {new_plate + 1}",
            lifetime=3,
        ).show()

    # ------------------------------------------------------------------
    # Toast helper
    # ------------------------------------------------------------------

    def _showSwitchToast(self, plate_nr: int) -> None:
        Message(
            title="Build Plates",
            text=f"Switched to Plate {plate_nr + 1}",
            lifetime=2,
        ).show()

    # ------------------------------------------------------------------
    # Properties & slots exposed to QML
    # ------------------------------------------------------------------

    @pyqtProperty(int, notify=pendingMaxPlateChanged)
    def pendingMaxPlate(self) -> int:
        """Highest plate number to show in the panel.

        Equal to max(activeBuildPlate, maxBuildPlate) so that a newly-added
        empty plate remains visible until the user populates it.
        """
        model = self._application.getMultiBuildPlateModel()
        active = max(0, model.activeBuildPlate)
        return max(active, model.maxBuildPlate)

    @pyqtProperty(bool, notify=canAddNewPlateChanged)
    def canAddNewPlate(self) -> bool:
        """False when the active plate is empty.

        Prevents the user from stacking multiple consecutive empty plates by
        clicking "+" repeatedly.  The "+" button in the QML panel binds its
        ``enabled`` state to this property.
        """
        model = self._application.getMultiBuildPlateModel()
        return len(self.getObjectsForPlate(model.activeBuildPlate)) > 0

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
        i = 0
        while True:
            item = objects_model.getItem(i)
            if not item:
                break
            if item.get("buildplate_number") == plate_number:
                result.append({
                    "name":        item.get("name", ""),
                    "model_index": i,
                    "selected":    bool(item.get("selected", False)),
                })
            i += 1
        return result

    @pyqtSlot(int, result=int)
    def getObjectPlate(self, model_index: int) -> int:
        """Return the plate number of the object at ObjectsModel[model_index].

        Used by the drop handler to guard against dropping an object onto the
        plate it already lives on (which would create a spurious undo entry).
        """
        objects_model = self._application.getObjectsModel()
        item = objects_model.getItem(model_index)
        if not item:
            return -1
        return item.get("buildplate_number", -1)

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

        # Toast feedback (UM.Message extension point)
        obj_name = item.get("name", "Object")
        Message(
            title="Build Plates",
            text=f"{obj_name} moved to Plate {plate_number + 1}",
            lifetime=2,
        ).show()

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
