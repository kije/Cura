# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import json
import os
from typing import Dict, List, Optional, cast

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
MESH_ASSIGNMENTS_KEY = "mixed_color_mesh_assignments"


class MixedColorPlugin(QObject, Extension):
    """Extension plugin that enables mixed-color filaments via layer dithering.

    Supports both IDEX/tool-changer printers (tool change commands) and
    mixing hotend printers (M163/M164 or M567 commands).

    Features:
    - Proxy extruder slot based assignment
    - Per-object assignment via ;MESH: G-code comments
    - Bresenham error diffusion for smooth gradient transitions
    - Temperature pre-heating with configurable lookahead
    - Save button indicator showing active mixed filaments
    """

    def __init__(self, parent=None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self.setMenuName(i18n_catalog.i18nc("@item:inmenu", "Mixed Colors"))
        self.addMenuItem(i18n_catalog.i18nc("@item:inmenu", "Configure Mixed Filaments..."), self.showPanel)

        self._mixed_filaments: List[MixedFilament] = []
        self._selected_index: int = -1
        self._view = None
        self._preheat_layers: int = 3
        self._enable_preheat: bool = True

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
        return [mf.to_dict() for mf in self._mixed_filaments]

    @pyqtProperty(int, notify=mixedFilamentsChanged)
    def mixedFilamentCount(self) -> int:
        return len(self._mixed_filaments)

    @pyqtProperty(int, notify=mixedFilamentsChanged)
    def enabledMixedFilamentCount(self) -> int:
        return sum(1 for mf in self._mixed_filaments if mf.enabled)

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

    @pyqtProperty(int, notify=mixedFilamentsChanged)
    def preheatLayers(self) -> int:
        return self._preheat_layers

    @pyqtProperty(bool, notify=mixedFilamentsChanged)
    def enablePreheat(self) -> bool:
        return self._enable_preheat

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

    @pyqtSlot(str, int, int, str, int, int, str, str, bool)
    def addMixedFilament(self, name: str, filament_a: int, filament_b: int,
                         output_mode: str,
                         ratio_a: int, ratio_b: int,
                         pattern_mode: str, custom_pattern: str,
                         apply_globally: bool) -> None:
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
            pattern=pattern,
            output_mode=output_mode,
            preview_color=preview,
            apply_globally=apply_globally,
        )
        self._mixed_filaments.append(mf)
        self._saveToMetadata()
        self.mixedFilamentsChanged.emit()

    @pyqtSlot(int, str, int, int, str, int, int, str, str, bool)
    def updateMixedFilament(self, index: int, name: str, filament_a: int, filament_b: int,
                            output_mode: str,
                            ratio_a: int, ratio_b: int,
                            pattern_mode: str, custom_pattern: str,
                            apply_globally: bool) -> None:
        """Update an existing mixed filament at the given index."""
        if 0 <= index < len(self._mixed_filaments):
            mf = self._mixed_filaments[index]
            mf.name = name
            mf.filament_a = filament_a
            mf.filament_b = filament_b
            mf.output_mode = output_mode
            mf.apply_globally = apply_globally
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
        if 0 <= index < len(self._mixed_filaments):
            del self._mixed_filaments[index]
            if self._selected_index >= len(self._mixed_filaments):
                self._selected_index = len(self._mixed_filaments) - 1
            self._saveToMetadata()
            self.mixedFilamentsChanged.emit()
            self.selectedIndexChanged.emit()

    @pyqtSlot(int, bool)
    def setMixedFilamentEnabled(self, index: int, enabled: bool) -> None:
        if 0 <= index < len(self._mixed_filaments):
            self._mixed_filaments[index].enabled = enabled
            self._saveToMetadata()
            self.mixedFilamentsChanged.emit()

    @pyqtSlot(int, str)
    def setGradient(self, index: int, gradient_json: str) -> None:
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
        if 0 <= index < len(self._mixed_filaments):
            self._mixed_filaments[index].gradient = None
            self._saveToMetadata()
            self.mixedFilamentsChanged.emit()

    @pyqtSlot(int, str)
    def addMeshToFilament(self, index: int, mesh_name: str) -> None:
        """Add a mesh name to a mixed filament's assigned_meshes list."""
        if 0 <= index < len(self._mixed_filaments):
            mf = self._mixed_filaments[index]
            if mesh_name not in mf.assigned_meshes:
                mf.assigned_meshes.append(mesh_name)
                self._saveToMetadata()
                self.mixedFilamentsChanged.emit()

    @pyqtSlot(int, str)
    def removeMeshFromFilament(self, index: int, mesh_name: str) -> None:
        """Remove a mesh name from a mixed filament's assigned_meshes list."""
        if 0 <= index < len(self._mixed_filaments):
            mf = self._mixed_filaments[index]
            if mesh_name in mf.assigned_meshes:
                mf.assigned_meshes.remove(mesh_name)
                self._saveToMetadata()
                self.mixedFilamentsChanged.emit()

    @pyqtSlot(int, bool)
    def setApplyGlobally(self, index: int, apply_globally: bool) -> None:
        """Set whether a mixed filament applies globally or per-mesh."""
        if 0 <= index < len(self._mixed_filaments):
            self._mixed_filaments[index].apply_globally = apply_globally
            self._saveToMetadata()
            self.mixedFilamentsChanged.emit()

    @pyqtProperty("QVariantList", constant=False, notify=mixedFilamentsChanged)
    def sceneObjectNames(self) -> list:
        """Return names of objects in the scene for mesh assignment UI."""
        names = []
        try:
            from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
            scene = Application.getInstance().getController().getScene()
            for node in DepthFirstIterator(scene.getRoot()):
                if node.getMeshData() and not node.callDecoration("isGroup"):
                    name = node.getName()
                    if name and name not in names:
                        names.append(name)
        except Exception:
            pass
        return names

    @pyqtSlot(int)
    def setPreheatLayers(self, layers: int) -> None:
        self._preheat_layers = max(0, min(20, layers))
        self._saveToMetadata()
        self.mixedFilamentsChanged.emit()

    @pyqtSlot(bool)
    def setEnablePreheat(self, enabled: bool) -> None:
        self._enable_preheat = enabled
        self._saveToMetadata()
        self.mixedFilamentsChanged.emit()

    @pyqtSlot(str, str, float, result=str)
    def previewBlendColor(self, color_a_hex: str, color_b_hex: str, ratio_a: float) -> str:
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

        active_mixes = [mf for mf in self._mixed_filaments if mf.enabled]
        if not active_mixes:
            return

        if ";MIXED_COLOR_PROCESSED" in gcode_list[0]:
            Logger.log("w", "G-code already processed by Mixed Colors plugin.")
            return

        try:
            # Gather extruder temperatures for pre-heating
            extruder_temps = self._get_extruder_temperatures()
            standby_temp = self._get_standby_temperature()

            processor = GCodeProcessor(
                preheat_layers=self._preheat_layers if self._enable_preheat else 0,
                extruder_temperatures=extruder_temps,
                standby_temperature=standby_temp,
            )
            gcode_list = processor.process(gcode_list, active_mixes)
            gcode_list[0] += ";MIXED_COLOR_PROCESSED\n"
            gcode_dict[active_build_plate_id] = gcode_list
            setattr(scene, "gcode_dict", gcode_dict)
            Logger.log("i", f"Mixed Colors: processed {len(active_mixes)} mixed filament(s).")
        except Exception:
            Logger.logException("e", "Exception in Mixed Colors post-processing.")

    # -- Internal Methods --

    def _createView(self) -> None:
        """Create the QML dialog view and save button indicator."""
        if self._view is not None:
            return

        plugin_path = PluginRegistry.getInstance().getPluginPath(self.getPluginId())
        if not plugin_path:
            Logger.log("e", "Mixed Colors plugin path not found.")
            return

        qml_path = os.path.join(plugin_path, "ui", "MixedColorPanel.qml")
        self._view = CuraApplication.getInstance().createQmlComponent(qml_path, {"manager": self})
        if self._view is None:
            Logger.log("e", "Failed to create Mixed Colors QML view.")
            return

        # Register save button indicator (same pattern as PostProcessingPlugin)
        save_button = self._view.findChild(QObject, "mixedColorSaveAreaButton")
        if save_button:
            CuraApplication.getInstance().addAdditionalComponent("saveButton", save_button)
            Logger.log("d", "Mixed Colors save button indicator registered.")

    def _onGlobalContainerStackChanged(self) -> None:
        self._global_container_stack = Application.getInstance().getGlobalContainerStack()
        if self._global_container_stack:
            self._restoreFromMetadata()
        self.mixedFilamentsChanged.emit()

    def _saveToMetadata(self) -> None:
        if not self._global_container_stack:
            return
        data = {
            "filaments": [mf.to_dict() for mf in self._mixed_filaments],
            "preheat_layers": self._preheat_layers,
            "enable_preheat": self._enable_preheat,
        }
        self._global_container_stack.setMetaDataEntry(METADATA_KEY, json.dumps(data))

    def _restoreFromMetadata(self) -> None:
        if not self._global_container_stack:
            return
        raw = self._global_container_stack.getMetaDataEntry(METADATA_KEY)
        if not raw:
            self._mixed_filaments = []
            return
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                self._mixed_filaments = [MixedFilament.from_dict(d) for d in data]
            else:
                self._mixed_filaments = [MixedFilament.from_dict(d) for d in data.get("filaments", [])]
                self._preheat_layers = data.get("preheat_layers", 3)
                self._enable_preheat = data.get("enable_preheat", True)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            Logger.log("e", f"Failed to restore mixed filament data: {e}")
            self._mixed_filaments = []

    def _get_extruder_color(self, extruder_index: int) -> tuple:
        global_stack = Application.getInstance().getGlobalContainerStack()
        if global_stack and extruder_index < len(global_stack.extruderList):
            extruder = global_stack.extruderList[extruder_index]
            if extruder.material:
                hex_color = extruder.material.getMetaDataEntry("color_code", "#808080")
                return ColorBlender.hex_to_rgb(hex_color)
        return (128, 128, 128)

    def _get_extruder_temperatures(self) -> Dict[int, float]:
        """Get print temperatures for each extruder from settings."""
        temps = {}
        global_stack = Application.getInstance().getGlobalContainerStack()
        if global_stack:
            for idx, extruder in enumerate(global_stack.extruderList):
                temp = extruder.getProperty("material_print_temperature", "value")
                if temp:
                    temps[idx] = float(temp)
        return temps

    def _get_standby_temperature(self) -> float:
        """Get standby temperature from settings."""
        global_stack = Application.getInstance().getGlobalContainerStack()
        if global_stack and global_stack.extruderList:
            temp = global_stack.extruderList[0].getProperty("material_standby_temperature", "value")
            if temp:
                return float(temp)
        return 150.0
