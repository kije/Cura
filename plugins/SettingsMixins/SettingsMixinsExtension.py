import json
import os
from typing import Any, cast, Dict, List, Optional

from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Application import Application
from UM.Extension import Extension
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.i18n import i18nCatalog

from cura.CuraApplication import CuraApplication

from .MixinManager import MixinDefinition, MixinManager

catalog = i18nCatalog("cura")

# Predefined color palette for mixin identification
MIXIN_COLORS = [
    "#FF6B35",  # Orange
    "#4ECDC4",  # Teal
    "#45B7D1",  # Sky Blue
    "#96CEB4",  # Sage Green
    "#DDA0DD",  # Plum
    "#F7DC6F",  # Gold
    "#82E0AA",  # Mint
    "#F1948A",  # Salmon
    "#85C1E9",  # Light Blue
    "#BB8FCE",  # Lavender
]


class SettingsMixinsExtension(QObject, Extension):
    """Main extension class for the Settings Mixins plugin.

    Provides the QML-facing API for managing setting mixins:
    creating, editing, ordering, and applying reusable setting bundles.
    """

    # ── Signals ─────────────────────────────────────────────────────────

    mixinLibraryChanged = pyqtSignal()
    activeMixinsChanged = pyqtSignal()
    conflictsChanged = pyqtSignal()
    currentScopeChanged = pyqtSignal()
    editingMixinChanged = pyqtSignal()
    editingSettingsChanged = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self._manager = MixinManager()
        self._main_window = None
        self._editor_dialog = None

        self._current_scope_key = "global"  # "global" or "extruder_N"

        # State for the mixin editor
        self._editing_mixin_id: Optional[str] = None
        self._editing_name = ""
        self._editing_description = ""
        self._editing_scope = "global"
        self._editing_color = MIXIN_COLORS[0]
        self._editing_settings: Dict[str, Any] = {}

        # Register menu items
        self.setMenuName(catalog.i18nc("@item:inmenu", "Settings Mixins"))
        self.addMenuItem(
            catalog.i18nc("@item:inmenu", "Manage Mixins"),
            self._show_main_window,
        )

        # Connect to application signals after it's ready
        Application.getInstance().engineCreatedSignal.connect(self._on_engine_created)

    def _on_engine_created(self) -> None:
        """Called when the QML engine is ready."""
        app = CuraApplication.getInstance()
        preferences = app.getPreferences()

        preferences.addPreference("settings_mixins/active_config", "")

        self._manager.load_all_mixins()
        self._manager.load_active_config(preferences)

        # Connect to profile/machine change signals to re-apply mixins
        machine_manager = app.getMachineManager()
        machine_manager.globalContainerChanged.connect(self._on_machine_changed)
        machine_manager.activeQualityGroupChanged.connect(self._on_profile_changed)
        machine_manager.activeQualityChangesGroupChanged.connect(self._on_profile_changed)

        # Apply mixins on startup
        self._apply_all_mixins()

    # ── QML Properties ──────────────────────────────────────────────────

    @pyqtProperty("QVariantList", notify=mixinLibraryChanged)
    def mixinLibrary(self) -> List[Dict[str, Any]]:
        """All available mixin definitions for display in QML."""
        result = []
        for mixin in self._manager.get_all_mixins():
            result.append({
                "id": mixin.id,
                "name": mixin.name,
                "description": mixin.description,
                "scope": mixin.scope,
                "color": mixin.color,
                "tags": ", ".join(mixin.tags),
                "settingCount": mixin.setting_count(),
            })
        return result

    @pyqtProperty("QVariantList", notify=activeMixinsChanged)
    def activeMixins(self) -> List[Dict[str, Any]]:
        """Active mixins for the current machine/scope, in order."""
        machine_id = self._get_machine_id()
        if not machine_id:
            return []

        result = []
        for mixin in self._manager.get_active_mixins(machine_id, self._current_scope_key):
            result.append({
                "id": mixin.id,
                "name": mixin.name,
                "description": mixin.description,
                "color": mixin.color,
                "scope": mixin.scope,
                "settingCount": mixin.setting_count(),
                "settingSummary": self._setting_summary(mixin),
            })
        return result

    @pyqtProperty("QVariantList", notify=activeMixinsChanged)
    def availableMixins(self) -> List[Dict[str, Any]]:
        """Mixins that can be added (not already active in current scope)."""
        machine_id = self._get_machine_id()
        active_ids = set(self._manager.get_active_mixin_ids(machine_id, self._current_scope_key)) if machine_id else set()

        result = []
        for mixin in self._manager.get_all_mixins():
            if mixin.id not in active_ids:
                result.append({
                    "id": mixin.id,
                    "name": mixin.name,
                    "description": mixin.description,
                    "color": mixin.color,
                    "scope": mixin.scope,
                    "settingCount": mixin.setting_count(),
                })
        return result

    @pyqtProperty("QVariantList", notify=conflictsChanged)
    def conflicts(self) -> List[Dict[str, Any]]:
        """Current conflicts between active mixins."""
        machine_id = self._get_machine_id()
        if not machine_id:
            return []
        raw = self._manager.compute_conflicts(machine_id, self._current_scope_key)
        # Convert for QML (ensure all values are strings for display)
        for conflict in raw:
            for source in conflict["sources"]:
                source["value"] = str(source["value"])
        return raw

    @pyqtProperty(int, notify=conflictsChanged)
    def conflictCount(self) -> int:
        machine_id = self._get_machine_id()
        if not machine_id:
            return 0
        return len(self._manager.compute_conflicts(machine_id, self._current_scope_key))

    @pyqtProperty(str, notify=currentScopeChanged)
    def currentScopeKey(self) -> str:
        return self._current_scope_key

    @pyqtProperty("QVariantList", constant=True)
    def colorPalette(self) -> List[str]:
        return MIXIN_COLORS

    # ── Editor Properties ───────────────────────────────────────────────

    @pyqtProperty(str, notify=editingMixinChanged)
    def editingMixinId(self) -> str:
        return self._editing_mixin_id or ""

    @pyqtProperty(str, notify=editingMixinChanged)
    def editingName(self) -> str:
        return self._editing_name

    @pyqtProperty(str, notify=editingMixinChanged)
    def editingDescription(self) -> str:
        return self._editing_description

    @pyqtProperty(str, notify=editingMixinChanged)
    def editingScope(self) -> str:
        return self._editing_scope

    @pyqtProperty(str, notify=editingMixinChanged)
    def editingColor(self) -> str:
        return self._editing_color

    @pyqtProperty("QVariantList", notify=editingSettingsChanged)
    def editingSettings(self) -> List[Dict[str, str]]:
        """Settings in the currently-edited mixin, as a list for QML."""
        return [
            {"key": k, "value": str(v)}
            for k, v in sorted(self._editing_settings.items())
        ]

    # ── Slots: Active Mixin Management ──────────────────────────────────

    @pyqtSlot(str)
    def setCurrentScope(self, scope_key: str) -> None:
        if self._current_scope_key != scope_key:
            self._current_scope_key = scope_key
            self.currentScopeChanged.emit()
            self.activeMixinsChanged.emit()
            self.conflictsChanged.emit()

    @pyqtSlot(str)
    def addMixinToActive(self, mixin_id: str) -> None:
        """Add a mixin to the active list for the current machine/scope."""
        machine_id = self._get_machine_id()
        if not machine_id:
            return
        self._manager.add_active_mixin(machine_id, self._current_scope_key, mixin_id)
        self._apply_all_mixins()
        self._save_config()
        self.activeMixinsChanged.emit()
        self.conflictsChanged.emit()

    @pyqtSlot(str)
    def removeMixinFromActive(self, mixin_id: str) -> None:
        """Remove a mixin from the active list."""
        machine_id = self._get_machine_id()
        if not machine_id:
            return
        self._manager.remove_active_mixin(machine_id, self._current_scope_key, mixin_id)
        self._apply_all_mixins()
        self._save_config()
        self.activeMixinsChanged.emit()
        self.conflictsChanged.emit()

    @pyqtSlot(int, int)
    def moveActiveMixin(self, old_index: int, new_index: int) -> None:
        """Reorder a mixin in the active list."""
        machine_id = self._get_machine_id()
        if not machine_id:
            return
        self._manager.move_active_mixin(machine_id, self._current_scope_key,
                                        old_index, new_index)
        self._apply_all_mixins()
        self._save_config()
        self.activeMixinsChanged.emit()
        self.conflictsChanged.emit()

    @pyqtSlot()
    def reapplyMixins(self) -> None:
        """Force re-apply all active mixins."""
        self._apply_all_mixins()

    # ── Slots: Mixin Editor ─────────────────────────────────────────────

    @pyqtSlot()
    def startNewMixin(self) -> None:
        """Initialize the editor for creating a new mixin."""
        self._editing_mixin_id = None
        self._editing_name = ""
        self._editing_description = ""
        self._editing_scope = "global"
        self._editing_color = MIXIN_COLORS[len(self._manager.get_all_mixins()) % len(MIXIN_COLORS)]
        self._editing_settings = {}
        self.editingMixinChanged.emit()
        self.editingSettingsChanged.emit()

    @pyqtSlot(str)
    def startEditMixin(self, mixin_id: str) -> None:
        """Initialize the editor for editing an existing mixin."""
        mixin = self._manager.get_mixin(mixin_id)
        if not mixin:
            return
        self._editing_mixin_id = mixin.id
        self._editing_name = mixin.name
        self._editing_description = mixin.description
        self._editing_scope = mixin.scope
        self._editing_color = mixin.color
        self._editing_settings = dict(mixin.settings)
        self.editingMixinChanged.emit()
        self.editingSettingsChanged.emit()

    @pyqtSlot(str)
    def setEditingName(self, name: str) -> None:
        self._editing_name = name

    @pyqtSlot(str)
    def setEditingDescription(self, desc: str) -> None:
        self._editing_description = desc

    @pyqtSlot(str)
    def setEditingScope(self, scope: str) -> None:
        self._editing_scope = scope
        self.editingMixinChanged.emit()

    @pyqtSlot(str)
    def setEditingColor(self, color: str) -> None:
        self._editing_color = color
        self.editingMixinChanged.emit()

    @pyqtSlot(str, str)
    def setEditingSetting(self, key: str, value: str) -> None:
        """Add or update a setting in the mixin being edited."""
        self._editing_settings[key] = self._parse_setting_value(value)
        self.editingSettingsChanged.emit()

    @pyqtSlot(str)
    def removeEditingSetting(self, key: str) -> None:
        """Remove a setting from the mixin being edited."""
        self._editing_settings.pop(key, None)
        self.editingSettingsChanged.emit()

    @pyqtSlot(str)
    def captureCurrentValue(self, key: str) -> None:
        """Capture the current value of a setting from the active stack."""
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        if self._editing_scope == "extruder":
            stack = self._get_active_extruder_stack()
            if stack:
                value = stack.getProperty(key, "value")
            else:
                value = global_stack.getProperty(key, "value")
        else:
            value = global_stack.getProperty(key, "value")

        if value is not None:
            self._editing_settings[key] = value
            self.editingSettingsChanged.emit()

    @pyqtSlot()
    def saveEditingMixin(self) -> None:
        """Save the mixin currently being edited."""
        if not self._editing_name.strip():
            return

        if self._editing_mixin_id:
            # Update existing
            self._manager.update_mixin(
                self._editing_mixin_id,
                name=self._editing_name.strip(),
                description=self._editing_description.strip(),
                scope=self._editing_scope,
                color=self._editing_color,
                settings=dict(self._editing_settings),
            )
        else:
            # Create new
            self._manager.create_mixin(
                name=self._editing_name.strip(),
                description=self._editing_description.strip(),
                scope=self._editing_scope,
                color=self._editing_color,
                settings=dict(self._editing_settings),
            )

        self._apply_all_mixins()
        self.mixinLibraryChanged.emit()
        self.activeMixinsChanged.emit()
        self.conflictsChanged.emit()

    @pyqtSlot(str)
    def deleteMixin(self, mixin_id: str) -> None:
        """Delete a mixin from the library."""
        machine_id = self._get_machine_id()
        if machine_id:
            # Clear from stacks first
            app = CuraApplication.getInstance()
            global_stack = app.getGlobalContainerStack()
            if global_stack:
                self._manager.clear_from_stack(global_stack)
                for extruder in global_stack.extruderList:
                    self._manager.clear_from_stack(extruder)

        self._manager.delete_mixin(mixin_id)

        # Re-apply remaining mixins
        self._apply_all_mixins()
        self._save_config()

        self.mixinLibraryChanged.emit()
        self.activeMixinsChanged.emit()
        self.conflictsChanged.emit()

    # ── Slots: Import / Export ──────────────────────────────────────────

    @pyqtSlot(str)
    def exportMixin(self, mixin_id: str) -> None:
        """Export a mixin — opens a file save dialog via QML."""
        # The actual file dialog is handled in QML; this just does the save
        # once we have a path. See exportMixinToPath.
        pass

    @pyqtSlot(str, str, result=bool)
    def exportMixinToPath(self, mixin_id: str, path: str) -> bool:
        """Export a mixin to a specific file path."""
        # Strip file:// prefix if present
        if path.startswith("file://"):
            path = QUrl(path).toLocalFile()
        return self._manager.export_mixin(mixin_id, path)

    @pyqtSlot(str, result=bool)
    def importMixinFromPath(self, path: str) -> bool:
        """Import a mixin from a file path."""
        if path.startswith("file://"):
            path = QUrl(path).toLocalFile()
        mixin = self._manager.import_mixin(path)
        if mixin:
            self.mixinLibraryChanged.emit()
            return True
        return False

    # ── Slots: Utility ──────────────────────────────────────────────────

    @pyqtSlot(str, result=str)
    def getSettingLabel(self, key: str) -> str:
        """Get the human-readable label for a setting key."""
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return key
        definition = global_stack.getBottom()
        if definition and definition.findDefinitions(key=key):
            return definition.findDefinitions(key=key)[0].label
        return key

    @pyqtSlot(str, result=str)
    def getSettingUnit(self, key: str) -> str:
        """Get the unit for a setting key."""
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return ""
        definition = global_stack.getBottom()
        if definition and definition.findDefinitions(key=key):
            return definition.findDefinitions(key=key)[0].unit or ""
        return ""

    @pyqtSlot(str, result="QVariantList")
    def searchSettings(self, query: str) -> List[Dict[str, str]]:
        """Search available settings by label or key. Returns up to 20 matches."""
        if len(query) < 2:
            return []

        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return []

        definition = global_stack.getBottom()
        if not definition:
            return []

        query_lower = query.lower()
        results = []
        for defn in definition.findDefinitions():
            if defn.type == "category":
                continue
            if query_lower in defn.key.lower() or query_lower in defn.label.lower():
                results.append({
                    "key": defn.key,
                    "label": defn.label,
                    "unit": defn.unit or "",
                })
                if len(results) >= 20:
                    break
        return results

    # ── Internal Methods ────────────────────────────────────────────────

    def _show_main_window(self) -> None:
        """Show the main mixin management window."""
        if self._main_window is None:
            plugin_path = cast(
                str,
                PluginRegistry.getInstance().getPluginPath(self.getPluginId()),
            )
            qml_path = os.path.join(plugin_path, "resources", "qml", "MixinMainWindow.qml")
            self._main_window = CuraApplication.getInstance().createQmlComponent(
                qml_path, {"manager": self}
            )
            if self._main_window is None:
                Logger.log("e", "Failed to create Settings Mixins window")
                return

        self.mixinLibraryChanged.emit()
        self.activeMixinsChanged.emit()
        self.conflictsChanged.emit()
        self._main_window.show()

    def _get_machine_id(self) -> Optional[str]:
        global_stack = CuraApplication.getInstance().getGlobalContainerStack()
        if global_stack:
            return global_stack.getId()
        return None

    def _get_active_extruder_stack(self) -> Any:
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return None
        extruder_index = app.getExtruderManager().activeExtruderIndex
        if 0 <= extruder_index < len(global_stack.extruderList):
            return global_stack.extruderList[extruder_index]
        return None

    def _apply_all_mixins(self) -> None:
        """Apply all active mixins to the current machine's stacks."""
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        machine_id = global_stack.getId()

        # Apply global-scope mixins to the global stack
        self._manager.apply_to_stack(global_stack, machine_id, "global")

        # Apply extruder-scope mixins to each extruder
        for i, extruder in enumerate(global_stack.extruderList):
            scope_key = f"extruder_{i}"
            self._manager.apply_to_stack(extruder, machine_id, scope_key)

    def _on_machine_changed(self) -> None:
        """Called when the active machine changes."""
        self._apply_all_mixins()
        self.activeMixinsChanged.emit()
        self.conflictsChanged.emit()

    def _on_profile_changed(self) -> None:
        """Called when the active quality profile changes."""
        self._apply_all_mixins()

    def _save_config(self) -> None:
        """Save the active mixin configuration to preferences."""
        preferences = CuraApplication.getInstance().getPreferences()
        self._manager.save_active_config(preferences)

    @staticmethod
    def _setting_summary(mixin: MixinDefinition) -> str:
        """Generate a short summary of a mixin's settings for display."""
        keys = sorted(mixin.settings.keys())
        if not keys:
            return "no settings"

        # Group by category prefix
        categories = set()
        for key in keys:
            parts = key.split("_")
            if len(parts) > 1:
                categories.add(parts[0])
            else:
                categories.add(key)

        cat_names = sorted(categories)[:3]
        summary = ", ".join(cat_names)
        count = len(keys)
        return f"{summary} · {count} setting{'s' if count != 1 else ''}"

    @staticmethod
    def _parse_setting_value(value_str: str) -> Any:
        """Parse a setting value string into the appropriate Python type."""
        # Try boolean
        if value_str.lower() in ("true", "yes"):
            return True
        if value_str.lower() in ("false", "no"):
            return False

        # Try integer
        try:
            return int(value_str)
        except ValueError:
            pass

        # Try float
        try:
            return float(value_str)
        except ValueError:
            pass

        # Return as string
        return value_str
