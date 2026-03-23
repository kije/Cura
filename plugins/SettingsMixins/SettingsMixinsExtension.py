import json
import os
from typing import Any, cast, Dict, List, Optional

from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Application import Application
from UM.Extension import Extension
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.Settings.ContainerRegistry import ContainerRegistry
from UM.Settings.InstanceContainer import InstanceContainer
from UM.i18n import i18nCatalog

from cura.CuraApplication import CuraApplication

from .MixinManager import MixinDefinition, MixinManager, is_expression, get_expression_text, get_display_value
from .MixinQualityChangesContainer import MixinQualityChangesContainer

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

    The list of active mixins ("includes") is stored as metadata on each
    profile's QualityChanges container, so different profiles carry different
    mixin recipes.  When the user switches profiles the includes list updates
    automatically.
    """

    # ── Signals ─────────────────────────────────────────────────────────

    mixinLibraryChanged = pyqtSignal()
    activeMixinsChanged = pyqtSignal()
    conflictsChanged = pyqtSignal()
    currentScopeChanged = pyqtSignal()
    editingMixinChanged = pyqtSignal()
    editingSettingsChanged = pyqtSignal()
    profileStateChanged = pyqtSignal()  # has custom profile or not
    changedSettingsChanged = pyqtSignal()
    pendingSettingKeyChanged = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self._manager = MixinManager()
        self._main_window = None
        self._sidebar_panel = None
        self._mixin_picker_dialog = None
        self._capture_dialog = None

        self._quick_apply_dialog = None
        self._current_scope_key = "global"  # "global" or "extruder_N"

        # True if CuraContainerStack.setQualityChanges() supports wrapper preservation.
        # Detected at startup. When False, the plugin still works but re-installs
        # the wrapper after every profile switch (brief unresolved flash possible).
        self._has_core_wrapper_support = False

        # Pending setting key from context menu "Add to Mixin..."
        self._pending_setting_key: Optional[str] = None

        # Editor state
        self._editing_mixin_id: Optional[str] = None
        self._editing_name = ""
        self._editing_description = ""
        self._editing_scope = "global"
        self._editing_color = MIXIN_COLORS[0]
        self._editing_settings: Dict[str, Any] = {}

        self.setMenuName(catalog.i18nc("@item:inmenu", "Settings Mixins"))
        self.addMenuItem(
            catalog.i18nc("@item:inmenu", "Apply Mixins to Profile..."),
            self._show_quick_apply_dialog,
        )
        self.addMenuItem(
            catalog.i18nc("@item:inmenu", "Manage Mixins"),
            self._show_main_window,
        )

        Application.getInstance().engineCreatedSignal.connect(self._on_engine_created)

    def _on_engine_created(self) -> None:
        app = CuraApplication.getInstance()
        self._manager.load_all_mixins()

        # Detect whether the core wrapper-preservation change is present.
        # We check if CuraContainerStack.setQualityChanges uses getattr-based
        # duck typing to detect wrapper containers.
        self._has_core_wrapper_support = self._detect_core_wrapper_support()
        if self._has_core_wrapper_support:
            Logger.log("i", "SettingsMixins: core wrapper support detected — wrappers preserved during profile switches")
        else:
            Logger.log("i", "SettingsMixins: core wrapper support NOT detected — using signal-based re-wrapping fallback")

        machine_manager = app.getMachineManager()
        machine_manager.globalContainerChanged.connect(self._on_machine_changed)
        machine_manager.activeQualityGroupChanged.connect(self._on_profile_changed)
        machine_manager.activeQualityChangesGroupChanged.connect(self._on_profile_changed)

        # Listen for new containers to post-process "Create Profile from Current Settings"
        ContainerRegistry.getInstance().containerAdded.connect(self._on_container_added)

        self._patch_user_changes_model()
        self._register_sidebar_panel()
        self._register_context_menu()
        self._apply_all_mixins()

    # ── Helpers to get the current QualityChanges container ────────────

    def _get_global_quality_changes(self) -> Any:
        """The global stack's QualityChanges container (may be empty singleton)."""
        global_stack = CuraApplication.getInstance().getGlobalContainerStack()
        if global_stack:
            return global_stack.qualityChanges
        return None

    def _get_extruder_quality_changes(self, extruder_index: int) -> Any:
        """An extruder's QualityChanges container."""
        global_stack = CuraApplication.getInstance().getGlobalContainerStack()
        if global_stack and 0 <= extruder_index < len(global_stack.extruderList):
            return global_stack.extruderList[extruder_index].qualityChanges
        return None

    def _get_current_quality_changes(self) -> Any:
        """The QualityChanges container for the currently selected scope."""
        if self._current_scope_key == "global":
            return self._get_global_quality_changes()
        if self._current_scope_key.startswith("extruder_"):
            idx = int(self._current_scope_key.split("_")[1])
            return self._get_extruder_quality_changes(idx)
        return self._get_global_quality_changes()

    def _has_custom_profile(self) -> bool:
        """True if the current profile is a custom quality_changes (not built-in)."""
        from cura.Settings.cura_empty_instance_containers import isEmptyContainer
        qc = self._get_current_quality_changes()
        if qc is None:
            return False
        return not isEmptyContainer(qc.getId())

    def _ensure_custom_profile(self) -> bool:
        """Ensure a custom quality_changes profile exists.

        If the user is on a built-in profile, auto-create a custom one so
        that we can store mixin includes on it.  Returns True if a custom
        profile is available after the call.
        """
        if self._has_custom_profile():
            return True

        # Programmatically create a quality_changes profile, mirroring what
        # QualityManagementModel.createQualityChanges() does.
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return False

        machine_manager = app.getMachineManager()
        active_quality_name = machine_manager.activeQualityOrQualityChangesName
        if not active_quality_name:
            active_quality_name = "Custom"

        base_name = active_quality_name + " (with mixins)"
        container_registry = ContainerRegistry.getInstance()
        unique_name = container_registry.uniqueName(base_name)

        from cura.Machines.ContainerTree import ContainerTree

        # Create global quality_changes
        global_qc = self._create_quality_changes_container(
            unique_name, global_stack, extruder_stack=None
        )
        container_registry.addContainer(global_qc)

        # Create one per extruder
        for extruder in global_stack.extruderList:
            ext_qc = self._create_quality_changes_container(
                unique_name, global_stack, extruder_stack=extruder
            )
            container_registry.addContainer(ext_qc)

        # Activate the new profile
        for quality_changes in ContainerTree.getInstance().getCurrentQualityChangesGroups():
            if quality_changes.name == unique_name:
                machine_manager.setQualityChangesGroup(quality_changes)
                break

        Logger.log("i", "Auto-created custom profile '%s' for mixin includes", unique_name)
        return self._has_custom_profile()

    def _create_quality_changes_container(
        self, name: str, global_stack: Any, extruder_stack: Any = None
    ) -> InstanceContainer:
        """Create a quality_changes InstanceContainer.

        Follows the same pattern as QualityManagementModel._createQualityChanges.
        """
        import cura.CuraApplication
        from cura.Machines.ContainerTree import ContainerTree

        container_registry = ContainerRegistry.getInstance()
        base_id = global_stack.definition.getId() if extruder_stack is None else extruder_stack.getId()
        new_id = (base_id + "_" + name).lower().replace(" ", "_")
        new_id = container_registry.uniqueName(new_id)

        quality_type = global_stack.quality.getMetaDataEntry("quality_type", "normal")

        qc = InstanceContainer(new_id)
        qc.setName(name)
        qc.setMetaDataEntry("type", "quality_changes")
        qc.setMetaDataEntry("quality_type", quality_type)

        if extruder_stack is not None:
            qc.setMetaDataEntry("position", extruder_stack.getMetaDataEntry("position"))
            intent_category = extruder_stack.intent.getMetaDataEntry("intent_category")
            if intent_category:
                qc.setMetaDataEntry("intent_category", intent_category)

        machine_definition_id = ContainerTree.getInstance().machines[
            global_stack.definition.getId()
        ].quality_definition
        qc.setDefinition(machine_definition_id)

        qc.setMetaDataEntry(
            "setting_version",
            cura.CuraApplication.CuraApplication.getInstance().SettingVersion,
        )
        return qc

    # ── QML Properties ──────────────────────────────────────────────────

    @pyqtProperty("QVariantList", notify=mixinLibraryChanged)
    def mixinLibrary(self) -> List[Dict[str, Any]]:
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
        """Mixins included by the current profile, in priority order."""
        qc = self._get_current_quality_changes()
        if qc is None:
            return []

        result = []
        for mixin in self._manager.get_includes_for_container(qc):
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
        """Mixins not yet included by the current profile."""
        qc = self._get_current_quality_changes()
        active_ids = set(self._manager.read_includes(qc)) if qc else set()

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
        qc = self._get_current_quality_changes()
        if qc is None:
            return []
        raw = self._manager.compute_conflicts(qc)
        for conflict in raw:
            for source in conflict["sources"]:
                source["value"] = get_display_value(source["value"])
        return raw

    @pyqtProperty(int, notify=conflictsChanged)
    def conflictCount(self) -> int:
        qc = self._get_current_quality_changes()
        if qc is None:
            return 0
        return len(self._manager.compute_conflicts(qc))

    @pyqtProperty(str, notify=currentScopeChanged)
    def currentScopeKey(self) -> str:
        return self._current_scope_key

    @pyqtProperty(bool, notify=profileStateChanged)
    def hasCustomProfile(self) -> bool:
        return self._has_custom_profile()

    @pyqtProperty(str, notify=profileStateChanged)
    def currentProfileName(self) -> str:
        app = CuraApplication.getInstance()
        mm = app.getMachineManager()
        return mm.activeQualityOrQualityChangesName if mm else ""

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
    def editingSettings(self) -> List[Dict[str, Any]]:
        result = []
        for k, v in sorted(self._editing_settings.items()):
            expr = is_expression(v)
            result.append({
                "key": k,
                "value": get_expression_text(v) if expr else str(v),
                "isExpression": expr,
            })
        return result

    # ── Slots: Active Mixin Management ──────────────────────────────────

    @pyqtSlot(str)
    def setCurrentScope(self, scope_key: str) -> None:
        if self._current_scope_key != scope_key:
            self._current_scope_key = scope_key
            self.currentScopeChanged.emit()
            self.activeMixinsChanged.emit()
            self.conflictsChanged.emit()
            self.profileStateChanged.emit()

    @pyqtSlot(str)
    def addMixinToActive(self, mixin_id: str) -> None:
        """Add a mixin to the current profile's includes list."""
        if not self._ensure_custom_profile():
            Logger.log("w", "Cannot add mixin — failed to create custom profile")
            return

        qc = self._get_current_quality_changes()
        if qc is None:
            return

        self._manager.add_include(qc, mixin_id)
        self._apply_all_mixins()
        self._emit_all_changed()

    @pyqtSlot(str)
    def removeMixinFromActive(self, mixin_id: str) -> None:
        """Remove a mixin from the current profile's includes list."""
        qc = self._get_current_quality_changes()
        if qc is None:
            return

        self._manager.remove_include(qc, mixin_id)
        self._apply_all_mixins()
        self._emit_all_changed()

    @pyqtSlot(int, int)
    def moveActiveMixin(self, old_index: int, new_index: int) -> None:
        """Reorder a mixin within the current profile's includes list."""
        qc = self._get_current_quality_changes()
        if qc is None:
            return

        self._manager.move_include(qc, old_index, new_index)
        self._apply_all_mixins()
        self._emit_all_changed()

    @pyqtSlot()
    def reapplyMixins(self) -> None:
        self._apply_all_mixins()

    # ── Slots: Mixin Editor ─────────────────────────────────────────────

    @pyqtSlot()
    def startNewMixin(self) -> None:
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
    def setEditingSettingLiteral(self, key: str, value: str) -> None:
        """Store a literal (non-expression) value for an editing setting."""
        self._editing_settings[key] = self._parse_setting_value(value)
        self.editingSettingsChanged.emit()

    @pyqtSlot(str, str)
    def setEditingSettingExpression(self, key: str, expression: str) -> None:
        """Store an expression value for an editing setting (with '=' prefix)."""
        self._editing_settings[key] = "=" + expression
        self.editingSettingsChanged.emit()

    @pyqtSlot(str, result=bool)
    def isSettingExpression(self, key: str) -> bool:
        """Check if an editing setting is currently in expression mode."""
        return is_expression(self._editing_settings.get(key))

    @pyqtSlot(str, result=bool)
    def toggleSettingExpression(self, key: str) -> bool:
        """Toggle a setting between literal and expression mode.

        Literal → Expression: wraps current value as expression text.
        Expression → Literal: evaluates expression, stores result. Returns False if eval fails.
        """
        value = self._editing_settings.get(key)
        if value is None:
            return False

        if is_expression(value):
            # Expression → Literal: evaluate and store result
            result = self._evaluate_expression_internal(get_expression_text(value))
            if not result["success"]:
                return False
            self._editing_settings[key] = result["raw_value"]
        else:
            # Literal → Expression: use current value as initial expression text
            self._editing_settings[key] = "=" + str(value)

        self.editingSettingsChanged.emit()
        return True

    @pyqtSlot(str, result="QVariantMap")
    def evaluateExpression(self, expression: str) -> Dict[str, Any]:
        """Evaluate an expression against the current scope's container stack.

        Returns {"success": bool, "value": str, "error": str}.
        """
        result = self._evaluate_expression_internal(expression)
        return {
            "success": result["success"],
            "value": str(result["raw_value"]) if result["success"] else "",
            "error": result.get("error", ""),
        }

    def _evaluate_expression_internal(self, expression: str) -> Dict[str, Any]:
        """Internal expression evaluation. Returns {"success", "raw_value", "error"}.

        Injects other literal settings from the current editing mixin as
        additional_variables so that expressions can reference sibling mixin
        values (e.g. an expression for outer_wall_inset can use the mixin's
        own wall_line_width_0 value rather than the profile's).
        """
        from UM.Settings.SettingFunction import SettingFunction

        if not expression.strip():
            return {"success": False, "raw_value": None, "error": "Empty expression"}

        try:
            func = SettingFunction(expression)
        except Exception as e:
            return {"success": False, "raw_value": None, "error": str(e)}

        stack = self._get_evaluation_stack()
        if stack is None:
            return {"success": False, "raw_value": None, "error": "No active stack"}

        # Collect literal values from the editing mixin to use as overrides
        additional_variables = {}
        for key, value in self._editing_settings.items():
            if not is_expression(value):
                additional_variables[key] = value

        try:
            value = func(stack, additional_variables=additional_variables)
            if value is None:
                return {"success": False, "raw_value": None, "error": "Expression evaluated to None"}
            return {"success": True, "raw_value": value}
        except Exception as e:
            return {"success": False, "raw_value": None, "error": str(e)}

    def _get_evaluation_stack(self) -> Any:
        """Get the container stack appropriate for the current editing scope."""
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return None

        if self._editing_scope == "extruder":
            extruder_stack = self._get_active_extruder_stack()
            return extruder_stack if extruder_stack else global_stack

        return global_stack

    @pyqtSlot(str)
    def removeEditingSetting(self, key: str) -> None:
        self._editing_settings.pop(key, None)
        self.editingSettingsChanged.emit()

    @pyqtSlot(str)
    def captureCurrentValue(self, key: str) -> None:
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        if self._editing_scope == "extruder":
            stack = self._get_active_extruder_stack()
            value = stack.getProperty(key, "value") if stack else global_stack.getProperty(key, "value")
        else:
            value = global_stack.getProperty(key, "value")

        if value is not None:
            self._editing_settings[key] = value
            self.editingSettingsChanged.emit()

    @pyqtSlot()
    def saveEditingMixin(self) -> None:
        if not self._editing_name.strip():
            return

        # Warn about invalid expressions at save time
        for key, value in self._editing_settings.items():
            if is_expression(value):
                result = self._evaluate_expression_internal(get_expression_text(value))
                if not result["success"]:
                    Logger.log("w", "Mixin '%s': expression for '%s' failed evaluation: %s",
                               self._editing_name.strip(), key, result.get("error", "unknown"))

        if self._editing_mixin_id:
            self._manager.update_mixin(
                self._editing_mixin_id,
                name=self._editing_name.strip(),
                description=self._editing_description.strip(),
                scope=self._editing_scope,
                color=self._editing_color,
                settings=dict(self._editing_settings),
            )
        else:
            self._manager.create_mixin(
                name=self._editing_name.strip(),
                description=self._editing_description.strip(),
                scope=self._editing_scope,
                color=self._editing_color,
                settings=dict(self._editing_settings),
            )

        self._apply_all_mixins()
        self.mixinLibraryChanged.emit()
        self._emit_all_changed()

    @pyqtSlot(str)
    def deleteMixin(self, mixin_id: str) -> None:
        # Remove from all profiles that reference it
        self._manager.remove_mixin_from_all_profiles(mixin_id)
        self._manager.delete_mixin(mixin_id)

        # Re-apply mixins (will unwrap stacks that no longer have mixins)
        self._apply_all_mixins()
        self.mixinLibraryChanged.emit()
        self._emit_all_changed()

    # ── Slots: Import / Export ──────────────────────────────────────────

    @pyqtSlot(str, str, result=bool)
    def exportMixinToPath(self, mixin_id: str, path: str) -> bool:
        if path.startswith("file://"):
            path = QUrl(path).toLocalFile()
        return self._manager.export_mixin(mixin_id, path)

    @pyqtSlot(str, result=bool)
    def importMixinFromPath(self, path: str) -> bool:
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
                results.append({"key": defn.key, "label": defn.label, "unit": defn.unit or ""})
                if len(results) >= 20:
                    break
        return results

    @pyqtSlot()
    def showManageWindow(self) -> None:
        """Open the mixin manager window (callable from QML sidebar panel)."""
        self._show_main_window()

    @pyqtSlot()
    def showQuickApplyDialog(self) -> None:
        """Open the quick-apply dialog (callable from QML)."""
        self._show_quick_apply_dialog()

    # ── Slots: Context Menu & Capture ────────────────────────────────────

    @pyqtProperty(str, notify=pendingSettingKeyChanged)
    def pendingSettingKey(self) -> str:
        return self._pending_setting_key or ""

    @pyqtProperty(str, notify=pendingSettingKeyChanged)
    def pendingSettingLabel(self) -> str:
        if not self._pending_setting_key:
            return ""
        return self.getSettingLabel(self._pending_setting_key)

    @pyqtProperty(str, notify=pendingSettingKeyChanged)
    def pendingSettingValue(self) -> str:
        if not self._pending_setting_key:
            return ""
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return ""
        value = global_stack.getProperty(self._pending_setting_key, "value")
        return str(value) if value is not None else ""

    def _on_add_to_mixin_triggered(self, kwargs: Dict[str, Any]) -> None:
        """Called from the settings context menu 'Add to Mixin...'."""
        key = kwargs.get("key", "")
        if not key:
            return
        self._pending_setting_key = key
        self.pendingSettingKeyChanged.emit()
        self._show_mixin_picker()

    @pyqtSlot(str)
    def addPendingSettingToMixin(self, mixin_id: str) -> None:
        """Add the pending setting (from context menu) to the specified mixin."""
        if not self._pending_setting_key:
            return
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        value = global_stack.getProperty(self._pending_setting_key, "value")
        if value is None:
            return

        mixin = self._manager.get_mixin(mixin_id)
        if not mixin:
            return

        settings = dict(mixin.settings)
        settings[self._pending_setting_key] = value
        self._manager.update_mixin(mixin_id, settings=settings)

        self._pending_setting_key = None
        self.pendingSettingKeyChanged.emit()
        self.mixinLibraryChanged.emit()
        self._apply_all_mixins()
        self._emit_all_changed()
        Logger.log("i", "Added setting '%s' to mixin '%s'", self._pending_setting_key, mixin.name)

    @pyqtSlot(str)
    def addPendingSettingToNewMixin(self, name: str) -> None:
        """Create a new mixin with the pending setting."""
        if not self._pending_setting_key:
            return
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        value = global_stack.getProperty(self._pending_setting_key, "value")
        if value is None:
            return

        color = MIXIN_COLORS[len(self._manager.get_all_mixins()) % len(MIXIN_COLORS)]
        self._manager.create_mixin(
            name=name.strip() or "New Mixin",
            description="",
            scope="global",
            color=color,
            settings={self._pending_setting_key: value},
        )

        self._pending_setting_key = None
        self.pendingSettingKeyChanged.emit()
        self.mixinLibraryChanged.emit()
        self._apply_all_mixins()
        self._emit_all_changed()

    @pyqtSlot(result="QVariantList")
    def getChangedSettings(self) -> List[Dict[str, Any]]:
        """Get all non-default settings from the active stack for bulk capture.

        Returns settings from UserChanges and QualityChanges containers.
        """
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return []

        results: List[Dict[str, Any]] = []
        seen_keys: set = set()

        # Collect from UserChanges (index 0)
        user_changes = global_stack.userChanges
        if user_changes:
            for key in user_changes.getAllKeys():
                value = user_changes.getProperty(key, "value")
                if value is not None and key not in seen_keys:
                    seen_keys.add(key)
                    results.append({
                        "key": key,
                        "label": self.getSettingLabel(key),
                        "value": str(value),
                        "source": "user",
                        "unit": self.getSettingUnit(key),
                    })

        # Collect from QualityChanges (index 1) — unwrap to get real QC values only
        qc = global_stack.qualityChanges
        raw_qc = self._manager.unwrap_quality_changes(qc) if qc else None
        if raw_qc:
            from cura.Settings.cura_empty_instance_containers import isEmptyContainer
            if not isEmptyContainer(raw_qc.getId()):
                for key in raw_qc.getAllKeys():
                    value = raw_qc.getProperty(key, "value")
                    if value is not None and key not in seen_keys:
                        seen_keys.add(key)
                        results.append({
                            "key": key,
                            "label": self.getSettingLabel(key),
                            "value": str(value),
                            "source": "profile",
                            "unit": self.getSettingUnit(key),
                        })

        # Also collect from active extruder
        extruder_stack = self._get_active_extruder_stack()
        if extruder_stack:
            ext_user = extruder_stack.userChanges
            if ext_user:
                for key in ext_user.getAllKeys():
                    value = ext_user.getProperty(key, "value")
                    if value is not None and key not in seen_keys:
                        seen_keys.add(key)
                        results.append({
                            "key": key,
                            "label": self.getSettingLabel(key),
                            "value": str(value),
                            "source": "user (extruder)",
                            "unit": self.getSettingUnit(key),
                        })

            ext_qc = extruder_stack.qualityChanges
            raw_ext_qc = self._manager.unwrap_quality_changes(ext_qc) if ext_qc else None
            if raw_ext_qc:
                from cura.Settings.cura_empty_instance_containers import isEmptyContainer
                if not isEmptyContainer(raw_ext_qc.getId()):
                    for key in raw_ext_qc.getAllKeys():
                        value = raw_ext_qc.getProperty(key, "value")
                        if value is not None and key not in seen_keys:
                            seen_keys.add(key)
                            results.append({
                                "key": key,
                                "label": self.getSettingLabel(key),
                                "value": str(value),
                                "source": "profile (extruder)",
                                "unit": self.getSettingUnit(key),
                            })

        results.sort(key=lambda r: r["label"])
        return results

    @pyqtSlot(str, "QVariantList")
    def captureSettingsToMixin(self, mixin_id: str, keys: List[str]) -> None:
        """Add multiple settings (by key) with their current values to a mixin."""
        if not keys:
            return
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        mixin = self._manager.get_mixin(mixin_id)
        if not mixin:
            return

        settings = dict(mixin.settings)
        for key in keys:
            # Try active stack first (resolves through the full stack)
            value = global_stack.getProperty(key, "value")
            # Also check extruder
            extruder_stack = self._get_active_extruder_stack()
            if extruder_stack:
                ext_value = extruder_stack.getProperty(key, "value")
                if ext_value is not None:
                    value = ext_value
            if value is not None:
                settings[key] = value

        self._manager.update_mixin(mixin_id, settings=settings)
        self.mixinLibraryChanged.emit()
        self._apply_all_mixins()
        self._emit_all_changed()
        Logger.log("i", "Captured %d settings to mixin '%s'", len(keys), mixin.name)

    @pyqtSlot(str, "QVariantList")
    def captureSettingsToNewMixin(self, name: str, keys: List[str]) -> None:
        """Create a new mixin from a selection of current setting keys."""
        if not keys:
            return
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        settings: Dict[str, Any] = {}
        for key in keys:
            value = global_stack.getProperty(key, "value")
            extruder_stack = self._get_active_extruder_stack()
            if extruder_stack:
                ext_value = extruder_stack.getProperty(key, "value")
                if ext_value is not None:
                    value = ext_value
            if value is not None:
                settings[key] = value

        if not settings:
            return

        color = MIXIN_COLORS[len(self._manager.get_all_mixins()) % len(MIXIN_COLORS)]
        self._manager.create_mixin(
            name=name.strip() or "New Mixin",
            description="",
            scope="global",
            color=color,
            settings=settings,
        )
        self.mixinLibraryChanged.emit()
        self._apply_all_mixins()
        self._emit_all_changed()
        Logger.log("i", "Created new mixin '%s' with %d settings", name, len(settings))

    @pyqtSlot()
    def showCaptureDialog(self) -> None:
        """Open the capture-settings-to-mixin dialog."""
        self.changedSettingsChanged.emit()
        self._show_capture_dialog()

    # ── Internal Methods ────────────────────────────────────────────────

    @staticmethod
    def _detect_core_wrapper_support() -> bool:
        """Check if CuraContainerStack.setQualityChanges() preserves wrapper containers.

        Inspects the method source for the duck-typing ``getattr(..., "setWrappedQualityChanges")``
        pattern. Returns True if the core change is present, False otherwise.
        """
        import inspect
        from cura.Settings.CuraContainerStack import CuraContainerStack
        try:
            source = inspect.getsource(CuraContainerStack.setQualityChanges)
            return "setWrappedQualityChanges" in source
        except (OSError, TypeError):
            return False

    def _register_context_menu(self) -> None:
        """Register 'Add to Mixin...' in the settings right-click context menu."""
        app = CuraApplication.getInstance()
        menu_item = {
            "name": "Add to Mixin...",
            "icon_name": "Plus",
            "actions": ["_on_add_to_mixin_triggered"],
            "menu_item": self,
        }
        app.getCuraAPI().interface.settings.addContextMenuItem(menu_item)
        Logger.log("i", "Settings Mixins context menu item registered")

    def _patch_user_changes_model(self) -> None:
        """Monkey-patch UserChangesModel._update to include mixin reference rows.

        When the 'Discard or Keep Changes' dialog shows, it displays all user-changed
        settings via UserChangesModel. Since setting_mixin_includes is metadata (not a
        setting), it doesn't appear. This patch appends a virtual row so users can see
        that mixin references will be saved with the new profile.
        """
        try:
            from cura.Machines.Models.UserChangesModel import UserChangesModel
        except ImportError:
            Logger.log("w", "SettingsMixins: Could not import UserChangesModel for patching")
            return

        manager = self._manager
        original_update = UserChangesModel._update

        def _patched_update(model_self: Any) -> None:
            original_update(model_self)

            app = CuraApplication.getInstance()
            global_stack = app.getGlobalContainerStack()
            if not global_stack:
                return

            # Collect mixin includes from all stacks
            mixin_names: List[str] = []
            for stack in [global_stack] + list(global_stack.extruderList):
                qc = stack.qualityChanges
                raw_qc = manager.unwrap_quality_changes(qc)
                includes = manager.read_includes(raw_qc)
                if includes:
                    for mixin_id in includes:
                        mixin = manager.get_mixin(mixin_id)
                        if mixin:
                            mixin_names.append(mixin.name)
                    break

            if not mixin_names:
                return

            items = list(model_self.items)
            items.append({
                "key": "setting_mixin_includes",
                "label": catalog.i18nc("@label", "Setting Mixins"),
                "user_value": ", ".join(mixin_names),
                "original_value": catalog.i18nc("@label", "(none)"),
                "extruder": "",
                "category": catalog.i18nc("@label", "Mixins"),
            })
            model_self.setItems(items)

        UserChangesModel._update = _patched_update
        Logger.log("i", "SettingsMixins: Patched UserChangesModel to show mixin references")

    def _show_mixin_picker(self) -> None:
        """Show the mixin picker popup for 'Add to Mixin...' context menu action."""
        if self._mixin_picker_dialog is None:
            plugin_path = cast(
                str,
                PluginRegistry.getInstance().getPluginPath(self.getPluginId()),
            )
            qml_path = os.path.join(plugin_path, "resources", "qml", "MixinPickerDialog.qml")
            self._mixin_picker_dialog = CuraApplication.getInstance().createQmlComponent(
                qml_path, {"manager": self}
            )
        if self._mixin_picker_dialog:
            self.mixinLibraryChanged.emit()
            self._mixin_picker_dialog.show()

    def _show_capture_dialog(self) -> None:
        """Show the bulk capture dialog."""
        if self._capture_dialog is None:
            plugin_path = cast(
                str,
                PluginRegistry.getInstance().getPluginPath(self.getPluginId()),
            )
            qml_path = os.path.join(plugin_path, "resources", "qml", "CaptureSettingsDialog.qml")
            self._capture_dialog = CuraApplication.getInstance().createQmlComponent(
                qml_path, {"manager": self}
            )
        if self._capture_dialog:
            self.changedSettingsChanged.emit()
            self.mixinLibraryChanged.emit()
            self._capture_dialog.show()

    def _register_sidebar_panel(self) -> None:
        """Register the collapsible mixin panel into the Custom Print Setup sidebar."""
        app = CuraApplication.getInstance()
        plugin_path = cast(
            str,
            PluginRegistry.getInstance().getPluginPath(self.getPluginId()),
        )
        qml_path = os.path.join(plugin_path, "resources", "qml", "MixinSidebarPanel.qml")
        self._sidebar_panel = app.createQmlComponent(qml_path, {"manager": self})
        if self._sidebar_panel is None:
            Logger.log("e", "Failed to create Settings Mixins sidebar panel")
            return

        panel_item = self._sidebar_panel.findChild(QObject, "settingsMixinsSidebarPanel")
        if panel_item is None:
            panel_item = self._sidebar_panel

        app.addAdditionalComponent("customPrintSetup", panel_item)
        Logger.log("i", "Settings Mixins sidebar panel registered")

    def _show_quick_apply_dialog(self) -> None:
        """Open the quick-apply dialog for toggling mixins on the current profile."""
        if self._quick_apply_dialog is None:
            plugin_path = cast(
                str,
                PluginRegistry.getInstance().getPluginPath(self.getPluginId()),
            )
            qml_path = os.path.join(plugin_path, "resources", "qml", "QuickApplyDialog.qml")
            self._quick_apply_dialog = CuraApplication.getInstance().createQmlComponent(
                qml_path, {"manager": self}
            )
            if self._quick_apply_dialog is None:
                Logger.log("e", "Failed to create Quick Apply Mixins dialog")
                return

        self.mixinLibraryChanged.emit()
        self.profileStateChanged.emit()
        self._emit_all_changed()
        self._quick_apply_dialog.show()

    def _show_main_window(self) -> None:
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
        self.profileStateChanged.emit()
        self._emit_all_changed()
        self._main_window.show()

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
        """Apply all included mixins to the current machine's stacks."""
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        # Global includes
        global_qc = global_stack.qualityChanges
        self._manager.apply_to_stack(global_stack, global_qc)

        # Per-extruder includes
        for extruder in global_stack.extruderList:
            ext_qc = extruder.qualityChanges
            self._manager.apply_to_stack(extruder, ext_qc)

    def _on_machine_changed(self) -> None:
        self._apply_all_mixins()
        self.profileStateChanged.emit()
        self._emit_all_changed()

    def _on_profile_changed(self) -> None:
        # Without the core wrapper-preservation change, the wrapper was destroyed
        # by setQualityChanges() before this signal fires. _apply_all_mixins()
        # will detect that and re-install fresh wrappers — this is the fallback path.
        self._apply_all_mixins()
        self.profileStateChanged.emit()
        self._emit_all_changed()

    def _on_container_added(self, container: InstanceContainer) -> None:
        """Post-process newly created quality_changes containers.

        When "Create Profile from Current Settings" is used while mixins are active,
        copy the mixin includes metadata to the new profile so that it inherits the
        mixin references. Mixin values are NOT baked in because the wrapper's
        getAllKeys() excludes mixin overlay keys from _performMerge iteration.
        """
        if container.getMetaDataEntry("type") != "quality_changes":
            return

        # Find the source profile's mixin includes by checking the active stacks
        app = CuraApplication.getInstance()
        global_stack = app.getGlobalContainerStack()
        if not global_stack:
            return

        source_includes: List[str] = []
        for stack in [global_stack] + global_stack.extruderList:
            current_qc = stack.qualityChanges
            raw_qc = self._manager.unwrap_quality_changes(current_qc)
            includes = self._manager.read_includes(raw_qc)
            if includes:
                source_includes = includes
                break

        if not source_includes:
            return

        # Copy the mixin includes metadata to the new profile
        if not container.getMetaDataEntry("setting_mixin_includes"):
            from .MixinManager import INCLUDES_METADATA_KEY
            container.setMetaDataEntry(INCLUDES_METADATA_KEY, json.dumps(source_includes))
            Logger.log("d", "Copied mixin includes to new profile '%s'", container.getId())

    def _emit_all_changed(self) -> None:
        self.activeMixinsChanged.emit()
        self.conflictsChanged.emit()

    @staticmethod
    def _setting_summary(mixin: MixinDefinition) -> str:
        keys = sorted(mixin.settings.keys())
        if not keys:
            return "no settings"
        categories = set()
        for key in keys:
            parts = key.split("_")
            categories.add(parts[0] if len(parts) > 1 else key)
        cat_names = sorted(categories)[:3]
        summary = ", ".join(cat_names)
        count = len(keys)
        return f"{summary} · {count} setting{'s' if count != 1 else ''}"

    @staticmethod
    def _parse_setting_value(value_str: str) -> Any:
        if value_str.lower() in ("true", "yes"):
            return True
        if value_str.lower() in ("false", "no"):
            return False
        try:
            return int(value_str)
        except ValueError:
            pass
        try:
            return float(value_str)
        except ValueError:
            pass
        return value_str
