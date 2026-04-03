# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import json
import os
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Application import Application
from UM.Extension import Extension
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.i18n import i18nCatalog
from cura.CuraApplication import CuraApplication

from .models.MixedFilament import MixedFilament
from .models.DitherPattern import DitherPattern
from .models.GradientProfile import GradientProfile
from .core.GCodeProcessor import GCodeProcessor
from .core.ColorBlender import ColorBlender

i18n_catalog = i18nCatalog("cura")

METADATA_KEY = "mixed_color_filaments"


class MixedColorPlugin(QObject, Extension):
    """Extension plugin that enables mixed-color filaments via layer dithering.

    Supports both IDEX/tool-changer printers (tool change commands) and
    mixing hotend printers (M163/M164 or M567 commands).
    """

    def __init__(self, parent=None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self.setMenuName(i18n_catalog.i18nc("@item:inmenu", "Mixed Colors"))
        self.addMenuItem(i18n_catalog.i18nc("@item:inmenu", "Configure Mixed Filaments..."), self.showPanel)

        self._mixed_filaments: List[MixedFilament] = []
        self._selected_index: int = -1
        self._view = None

        self._global_container_stack = Application.getInstance().getGlobalContainerStack()
        if self._global_container_stack:
            self._restoreFromMetadata()

        Application.getInstance().getOutputDeviceManager().writeStarted.connect(self.execute)
        Application.getInstance().globalContainerStackChanged.connect(self._onGlobalContainerStackChanged)
        CuraApplication.getInstance().mainWindowChanged.connect(self._createView)

    # -- Signals --

    mixedFilamentsChanged = pyqtSignal()
    selectedIndexChanged = pyqtSignal()

    # -- Properties exposed to QML --

    @pyqtProperty("QVariantList", notify=mixedFilamentsChanged)
    def mixedFilaments(self) -> list:
        """Return mixed filaments as a list of dicts for QML consumption."""
        result = []
        for mf in self._mixed_filaments:
            result.append(mf.to_dict())
        return result

    @pyqtProperty(int, notify=mixedFilamentsChanged)
    def mixedFilamentCount(self) -> int:
        return len(self._mixed_filaments)

    @pyqtProperty(int, notify=selectedIndexChanged)
    def selectedIndex(self) -> int:
        return self._selected_index

    @pyqtProperty("QVariantList", constant=True)
    def availableExtruders(self) -> list:
        """Return available extruder info for QML dropdowns."""
        extruders = []
        global_stack = Application.getInstance().getGlobalContainerStack()
        if global_stack:
            for idx, extruder in enumerate(global_stack.extruderList):
                color = extruder.material.getMetaDataEntry("color_code", "#808080") if extruder.material else "#808080"
                name = extruder.material.getMetaDataEntry("material", "Unknown") if extruder.material else "Unknown"
                extruders.append({
                    "index": idx,
                    "name": f"Extruder {idx + 1} - {name}",
                    "color": color
                })
        return extruders

    # -- QML Slots --

    @pyqtSlot()
    def showPanel(self) -> None:
        """Show the mixed colors configuration dialog."""
        if self._view is None:
            self._createView()
        if self._view:
            self._view.show()

    @pyqtSlot(int)
    def setSelectedIndex(self, index: int) -> None:
        if self._selected_index != index:
            self._selected_index = index
            self.selectedIndexChanged.emit()

    @pyqtSlot(str, int, int, int, str, int, int, str, str)
    def addMixedFilament(self, name: str, filament_a: int, filament_b: int,
                         proxy_extruder: int, output_mode: str,
                         ratio_a: int, ratio_b: int,
                         pattern_mode: str, custom_pattern: str) -> None:
        """Add a new mixed filament definition."""
        pattern = DitherPattern(
            mode=pattern_mode,
            ratio_a=ratio_a,
            ratio_b=ratio_b,
            custom_pattern=custom_pattern
        )

        color_a = self._get_extruder_color(filament_a)
        color_b = self._get_extruder_color(filament_b)
        preview = ColorBlender.blend_rgb(color_a, color_b, pattern.get_ratio_fraction())

        mf = MixedFilament(
            name=name,
            filament_a=filament_a,
            filament_b=filament_b,
            proxy_extruder=proxy_extruder,
            pattern=pattern,
            output_mode=output_mode,
            preview_color=preview,
        )
        self._mixed_filaments.append(mf)
        self._saveToMetadata()
        self.mixedFilamentsChanged.emit()

    @pyqtSlot(int, str, int, int, int, str, int, int, str, str)
    def updateMixedFilament(self, index: int, name: str, filament_a: int, filament_b: int,
                            proxy_extruder: int, output_mode: str,
                            ratio_a: int, ratio_b: int,
                            pattern_mode: str, custom_pattern: str) -> None:
        """Update an existing mixed filament at the given index."""
        if 0 <= index < len(self._mixed_filaments):
            mf = self._mixed_filaments[index]
            mf.name = name
            mf.filament_a = filament_a
            mf.filament_b = filament_b
            mf.proxy_extruder = proxy_extruder
            mf.output_mode = output_mode
            mf.pattern = DitherPattern(
                mode=pattern_mode,
                ratio_a=ratio_a,
                ratio_b=ratio_b,
                custom_pattern=custom_pattern,
            )

            color_a = self._get_extruder_color(filament_a)
            color_b = self._get_extruder_color(filament_b)
            mf.preview_color = ColorBlender.blend_rgb(color_a, color_b, mf.pattern.get_ratio_fraction())

            self._saveToMetadata()
            self.mixedFilamentsChanged.emit()

    @pyqtSlot(int)
    def removeMixedFilament(self, index: int) -> None:
        """Remove a mixed filament by index."""
        if 0 <= index < len(self._mixed_filaments):
            del self._mixed_filaments[index]
            if self._selected_index >= len(self._mixed_filaments):
                self._selected_index = len(self._mixed_filaments) - 1
            self._saveToMetadata()
            self.mixedFilamentsChanged.emit()
            self.selectedIndexChanged.emit()

    @pyqtSlot(int, bool)
    def setMixedFilamentEnabled(self, index: int, enabled: bool) -> None:
        """Enable/disable a mixed filament."""
        if 0 <= index < len(self._mixed_filaments):
            self._mixed_filaments[index].enabled = enabled
            self._saveToMetadata()
            self.mixedFilamentsChanged.emit()

    @pyqtSlot(int, str)
    def setGradient(self, index: int, gradient_json: str) -> None:
        """Set gradient profile for a mixed filament from JSON string."""
        if 0 <= index < len(self._mixed_filaments):
            try:
                data = json.loads(gradient_json)
                self._mixed_filaments[index].gradient = GradientProfile.from_dict(data)
                self._saveToMetadata()
                self.mixedFilamentsChanged.emit()
            except (json.JSONDecodeError, KeyError) as e:
                Logger.log("e", f"Failed to parse gradient JSON: {e}")

    @pyqtSlot(int)
    def removeGradient(self, index: int) -> None:
        """Remove gradient from a mixed filament."""
        if 0 <= index < len(self._mixed_filaments):
            self._mixed_filaments[index].gradient = None
            self._saveToMetadata()
            self.mixedFilamentsChanged.emit()

    @pyqtSlot(str, str, float, result=str)
    def previewBlendColor(self, color_a_hex: str, color_b_hex: str, ratio_a: float) -> str:
        """Compute and return a preview blend color as hex string."""
        ca = ColorBlender.hex_to_rgb(color_a_hex)
        cb = ColorBlender.hex_to_rgb(color_b_hex)
        blended = ColorBlender.blend_rgb(ca, cb, ratio_a)
        return ColorBlender.rgb_to_hex(blended)

    # -- G-code Post-Processing --

    def execute(self, output_device) -> None:
        """Post-process G-code to apply mixed filament dithering."""
        scene = Application.getInstance().getController().getScene()
        if not hasattr(scene, "gcode_dict"):
            return
        gcode_dict = getattr(scene, "gcode_dict")
        if not gcode_dict:
            return

        active_build_plate_id = CuraApplication.getInstance().getMultiBuildPlateModel().activeBuildPlate
        gcode_list = gcode_dict.get(active_build_plate_id)
        if not gcode_list:
            return

        # Only process enabled mixed filaments
        active_mixes = [mf for mf in self._mixed_filaments if mf.enabled]
        if not active_mixes:
            return

        # Check if already processed
        if ";MIXED_COLOR_PROCESSED" in gcode_list[0]:
            Logger.log("w", "G-code already processed by Mixed Colors plugin.")
            return

        try:
            processor = GCodeProcessor()
            gcode_list = processor.process(gcode_list, active_mixes)
            gcode_list[0] += ";MIXED_COLOR_PROCESSED\n"
            gcode_dict[active_build_plate_id] = gcode_list
            setattr(scene, "gcode_dict", gcode_dict)
            Logger.log("i", f"Mixed Colors: processed {len(active_mixes)} mixed filament(s).")
        except Exception:
            Logger.logException("e", "Exception in Mixed Colors post-processing.")

    # -- Internal Methods --

    def _createView(self) -> None:
        """Create the QML dialog view."""
        if self._view is not None:
            return

        plugin_path = PluginRegistry.getInstance().getPluginPath(self.getPluginId())
        if not plugin_path:
            Logger.log("e", "Mixed Colors plugin path not found.")
            return

        qml_path = os.path.join(plugin_path, "ui", "MixedColorPanel.qml")
        self._view = CuraApplication.getInstance().createQmlComponent(qml_path, {"manager": self})

    def _onGlobalContainerStackChanged(self) -> None:
        self._global_container_stack = Application.getInstance().getGlobalContainerStack()
        if self._global_container_stack:
            self._restoreFromMetadata()
        self.mixedFilamentsChanged.emit()

    def _saveToMetadata(self) -> None:
        """Persist mixed filament configs to global container stack metadata."""
        if not self._global_container_stack:
            return
        data = [mf.to_dict() for mf in self._mixed_filaments]
        self._global_container_stack.setMetaDataEntry(METADATA_KEY, json.dumps(data))

    def _restoreFromMetadata(self) -> None:
        """Restore mixed filament configs from global container stack metadata."""
        if not self._global_container_stack:
            return
        raw = self._global_container_stack.getMetaDataEntry(METADATA_KEY)
        if not raw:
            self._mixed_filaments = []
            return
        try:
            data = json.loads(raw)
            self._mixed_filaments = [MixedFilament.from_dict(d) for d in data]
        except (json.JSONDecodeError, KeyError) as e:
            Logger.log("e", f"Failed to restore mixed filament data: {e}")
            self._mixed_filaments = []

    def _get_extruder_color(self, extruder_index: int) -> tuple:
        """Get RGB color tuple for an extruder's material."""
        global_stack = Application.getInstance().getGlobalContainerStack()
        if global_stack and extruder_index < len(global_stack.extruderList):
            extruder = global_stack.extruderList[extruder_index]
            if extruder.material:
                hex_color = extruder.material.getMetaDataEntry("color_code", "#808080")
                return ColorBlender.hex_to_rgb(hex_color)
        return (128, 128, 128)
