# Copyright (c) 2024 Ultimaker B.V.
# MultiBuildPlatePlugin is released under the terms of the LGPLv3 or higher.

import os

from PyQt6.QtCore import QObject, pyqtSlot

from UM.Extension import Extension
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Selection import Selection

from cura.CuraApplication import CuraApplication


class MultiBuildPlatePlugin(QObject, Extension):
    """Extension plugin that exposes Cura's built-in multi build plate functionality.

    The backend infrastructure (MultiBuildPlateModel, CuraSceneController,
    BuildPlateDecorator, per-plate slicing) is fully implemented in Cura but has
    no user-facing UI. This plugin adds:
      - A floating build plate tab switcher panel in the 3D viewport
      - Extensions menu items for keyboard-friendly plate navigation
    """

    def __init__(self, parent=None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self._application = CuraApplication.getInstance()
        self._switcher_panel = None

        # Mark the feature as enabled in preferences so user scripts / future
        # Cura code can gate on this flag.
        self._application.getPreferences().addPreference("cura/use_multi_build_plate", True)

        self.setMenuName("Multi Build Plate")
        self.addMenuItem("Next Build Plate", self._nextBuildPlate)
        self.addMenuItem("Previous Build Plate", self._previousBuildPlate)
        self.addMenuItem("Move Selection to New Build Plate", self._moveSelectionToNewBuildPlate)

        # Wait for the QML engine to be ready before creating the panel.
        self._application.initializationFinished.connect(self._onInitializationFinished)

    def _onInitializationFinished(self) -> None:
        """Create the persistent build plate switcher panel once the QML engine exists."""
        if self._switcher_panel is not None:
            return

        qml_path = os.path.join(
            PluginRegistry.getInstance().getPluginPath("MultiBuildPlatePlugin"),
            "qml",
            "BuildPlateSwitcher.qml",
        )
        self._switcher_panel = self._application.createQmlComponent(qml_path, {"manager": self})
        if self._switcher_panel is None:
            Logger.log("e", "MultiBuildPlatePlugin: failed to create BuildPlateSwitcher panel")

    # ------------------------------------------------------------------
    # Slots used by Extensions menu items
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
    # Slots callable from QML (BuildPlateSwitcher.qml)
    # ------------------------------------------------------------------

    @pyqtSlot(int)
    def setActiveBuildPlate(self, plate_nr: int) -> None:
        """Switch the active build plate. Called from QML tab buttons."""
        self._application.getCuraSceneController().setActiveBuildPlate(plate_nr)

    @pyqtSlot()
    def moveSelectionToNewBuildPlate(self) -> None:
        """Move all selected objects to a new build plate and switch to it."""
        self._moveSelectionToNewBuildPlate()
