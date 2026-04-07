# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

import json
import os
import weakref
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Application import Application
from UM.Extension import Extension
from UM.JobQueue import JobQueue
from UM.Logger import Logger
from UM.Message import Message
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.Selection import Selection
from UM.i18n import i18nCatalog

from cura.CuraApplication import CuraApplication
from cura.Scene.CuraSceneNode import CuraSceneNode

from .FEABoundaryConditionDecorator import FEABoundaryConditionDecorator
from .deps.dependency_manager import DependencyManager

i18n_catalog = i18nCatalog("cura")


class FEAInfillExtension(QObject, Extension):
    """Main extension for FEA-driven infill optimization.

    Provides:
    - Menu item to open the FEA dialog
    - Orchestration of FEA solving and modifier mesh generation
    - Python↔QML bridge via pyqtProperty/pyqtSlot/pyqtSignal
    """

    # -- Signals for QML --
    analysisStatusChanged = pyqtSignal()
    progressChanged = pyqtSignal()
    resultsChanged = pyqtSignal()
    boundaryConditionsChanged = pyqtSignal()
    depsAvailableChanged = pyqtSignal()
    sceneNodesChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    preselectedNodeChanged = pyqtSignal()
    phaseChanged = pyqtSignal()
    errorMessageChanged = pyqtSignal()
    stressOverlayVisibleChanged = pyqtSignal()

    def __init__(self, parent=None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self.setMenuName(i18n_catalog.i18nc("@item:inmenu", "FEA Infill"))
        self.addMenuItem(
            i18n_catalog.i18nc("@item:inmenu", "Optimize Infill..."),
            self.showDialog
        )

        self._dialog = None
        self._dep_manager: Optional[DependencyManager] = None
        self._deps_available = False

        # Weak-reference cache for scene node lookup (C11: safe id-based lookup)
        self._node_cache: weakref.WeakValueDictionary = weakref.WeakValueDictionary()

        # Set of node IDs already restored from metadata (prevents re-restore)
        self._restored_node_ids: set = set()

        # Per-model analysis state: node_key → {phase, results, settings, ...}
        self._per_node_state: Dict[str, Dict[str, Any]] = {}

        # Pre-selected node for dialog opening from tool
        self._preselected_node_key = ""

        # Inline phase management: define → optimize → running → review / error
        self._phase = "define"
        self._active_node_key = ""

        # Analysis state
        self._analysis_status = "idle"  # idle, running, complete, error
        self._progress = 0.0
        self._analysis_stage = ""
        self._results: Optional[Dict[str, Any]] = None
        self._cancel_requested = False
        self._stress_overlay_visible = False
        self._error_message = ""

        # Settings
        self._material_name = "PLA"
        self._min_density = 10.0
        self._max_density = 80.0
        self._num_zones = 6
        self._infill_pattern = "gyroid"  # overridden by _syncInfillPatternFromCura
        self._max_iterations = 20
        self._mesh_resolution = "medium"
        self._safety_factor = 2.0
        self._bonding_coeff = 0.5  # 50 % default; overridden by UI or material
        self._optimization_method = "heuristic"  # "heuristic" or "oc"
        self._volume_fraction = 50.0  # target volume % for OC method (displayed as %)

        # Help system preferences
        CuraApplication.getInstance().getPreferences().addPreference(
            "fea_optimizer/onboarding_completed", False
        )

        # Deferred initialization
        CuraApplication.getInstance().engineCreatedSignal.connect(self._onEngineCreated)

    # Cura material type → plugin database key mapping
    _CURA_MATERIAL_MAP = {
        "pla": "PLA", "abs": "ABS", "petg": "PETG", "pet": "PETG",
        "nylon": "Nylon", "pa": "Nylon", "pa6": "Nylon", "pa12": "Nylon",
        "pc": "PC", "polycarbonate": "PC",
        "tpu": "TPU_95A", "tpu 95a": "TPU_95A", "flex": "TPU_95A",
        "cf": "CF_Nylon", "cf-nylon": "CF_Nylon", "cf nylon": "CF_Nylon",
        "carbon": "CF_Nylon", "cf-pet": "CF_Nylon", "cf-pla": "CF_Nylon",
        "pet-cf": "CF_PET", "petcf": "CF_PET", "pet cf": "CF_PET",
        "pla-cf": "CF_Nylon", "placf": "CF_Nylon", "pla cf": "CF_Nylon",
        "cpe": "PETG", "cpe+": "PETG",  # Ultimaker CPE ≈ PETG mechanically
        "asa": "ABS",  # ASA is similar to ABS mechanically
        "hips": "ABS",  # HIPS is similar to ABS
        "pva": "PLA",  # PVA (support) — use PLA as approximation
        "breakaway": "PLA",  # support material
    }

    def _syncMaterialFromCura(self) -> None:
        """Read the active material type from Cura and update the plugin's material selection.

        Cura's .fdm_material XML files contain no mechanical properties (only density
        and diameter).  We read the material *type name* (e.g. "pla", "abs") via
        ``extruder.material.getMetaDataEntry("material")`` and map it to the plugin's
        internal MaterialDatabase which has FDM-specific mechanical properties (E_xy,
        E_z, yield_strength, etc.).
        """
        try:
            global_stack = CuraApplication.getInstance().getGlobalContainerStack()
            if global_stack is None or not global_stack.extruderList:
                return

            extruder = global_stack.extruderList[0]
            if extruder.material is None:
                return

            cura_type = extruder.material.getMetaDataEntry("material", "")
            if not cura_type:
                return

            cura_type_lower = cura_type.lower().strip()
            db_name = self._CURA_MATERIAL_MAP.get(cura_type_lower)

            # Fuzzy fallback: check if any map key is a substring of the Cura type
            # or vice versa (handles "Ultimaker PET-CF", "Generic PLA", etc.)
            if not db_name:
                normalized = cura_type_lower.replace("-", "").replace(" ", "").replace("_", "")
                for key, val in self._CURA_MATERIAL_MAP.items():
                    key_norm = key.replace("-", "").replace(" ", "").replace("_", "")
                    if key_norm in normalized or normalized in key_norm:
                        db_name = val
                        break

            if db_name and db_name != self._material_name:
                Logger.log("i", "FEA Infill: Auto-detected Cura material '%s' → mapped to '%s'",
                           cura_type, db_name)
                self._material_name = db_name
                self.settingsChanged.emit()
            elif not db_name and cura_type_lower:
                Logger.log("d", "FEA Infill: Cura material type '%s' has no mapping, keeping '%s'",
                           cura_type, self._material_name)
        except Exception:
            Logger.logException("d", "FEA Infill: Failed to sync material from Cura")

    def _syncInfillPatternFromCura(self) -> None:
        """Read the active infill pattern from Cura's print profile."""
        try:
            global_stack = CuraApplication.getInstance().getGlobalContainerStack()
            if global_stack is None or not global_stack.extruderList:
                return
            extruder = global_stack.extruderList[0]
            cura_pattern = extruder.getProperty("infill_pattern", "value")
            if cura_pattern and str(cura_pattern) != self._infill_pattern:
                Logger.log("i", "FEA Infill: Auto-detected Cura infill pattern '%s'", cura_pattern)
                self._infill_pattern = str(cura_pattern)
                self.settingsChanged.emit()
        except Exception:
            Logger.logException("d", "FEA Infill: Failed to sync infill pattern from Cura")

    _signals_connected = False

    def _onEngineCreated(self) -> None:
        plugin_path = PluginRegistry.getInstance().getPluginPath("FEAInfillOptimizer")
        if plugin_path:
            self._dep_manager = DependencyManager(plugin_path)

            # Show platform warning on Linux
            if not DependencyManager.is_platform_supported():
                platform_msg = DependencyManager.platform_message()
                if platform_msg:
                    Logger.log("w", "FEA Infill: %s", platform_msg)
                    Message(
                        platform_msg,
                        title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer — Platform Notice"),
                        message_type=Message.MessageType.WARNING,
                        lifetime=0
                    ).show()

            self._recheckDeps()

        # Connect signals exactly once (guard prevents multi-connect on retries)
        if not self._signals_connected:
            self._signals_connected = True

            app = CuraApplication.getInstance()

            # Restore BCs when files are loaded
            app.fileLoaded.connect(self._restoreBCsFromScene)
            app.fileCompleted.connect(self._restoreBCsFromScene)
            if hasattr(app, "workspaceLoaded"):
                app.workspaceLoaded.connect(self._restoreBCsFromScene)

            # Scene changes — BC metadata and orphaned overlay cleanup
            app.getController().getScene().sceneChanged.connect(self._onSceneNodeMayHaveBCMetadata)
            app.getController().getScene().sceneChanged.connect(self._cleanupOrphanedOverlays)

            # Per-model state switching on selection change
            Selection.selectionChanged.connect(self._onSelectionChanged)

            # Sync BC data to node.metadata before save
            app.getOutputDeviceManager().writeStarted.connect(self._syncAllBCsToMetadata)

            # Sync material and infill pattern from Cura's active profile
            machine_manager = app.getMachineManager()
            if machine_manager is not None:
                machine_manager.activeMaterialChanged.connect(self._syncMaterialFromCura)
                machine_manager.activeQualityChanged.connect(self._syncInfillPatternFromCura)

        # Sync material and infill pattern from Cura's active profile
        self._syncMaterialFromCura()
        self._syncInfillPatternFromCura()

    _recheck_count = 0

    def _recheckDeps(self) -> None:
        """Re-evaluate dependency availability and update the flag."""
        if self._dep_manager is None:
            return
        check = self._dep_manager.check_all()
        self._deps_available = all(check.values())
        Logger.log("d", "FEA Infill: Dependency check: %s → available=%s", check, self._deps_available)
        self.depsAvailableChanged.emit()

        # Retry up to 3 times at startup (some paths may not be ready yet)
        if not self._deps_available and self._recheck_count < 3:
            self._recheck_count += 1
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, self._recheckDeps)
        elif not self._deps_available:
            missing = [k for k, v in check.items() if not v]
            Logger.log("w", "FEA Infill: Missing packages after retries: %s. "
                       "These must be installed manually into Cura's Python environment "
                       "or bundled in the plugin's _vendor/ directory.", missing)

        # Signal connections are handled in _onEngineCreated() with a guard flag.

    _BC_METADATA_KEY = "fea_infill_boundary_conditions"
    _SETTINGS_METADATA_KEY = "fea_infill_settings"
    _RESULTS_METADATA_KEY = "fea_infill_results"

    def _syncAllBCsToMetadata(self, *args) -> None:
        """Persist BC data, analysis settings, and results summary → node.metadata.

        Called right before writing a 3MF file.  The 3MF writer reads
        node.metadata and saves each entry via savitar_node.setSetting().
        Settings and results are saved per-node from _per_node_state, falling
        back to the current global state for the active node.
        """
        import json

        # Snapshot current active node state before saving
        if self._active_node_key:
            self._saveCurrentStateForNode(self._active_node_key)

        scene = CuraApplication.getInstance().getController().getScene()
        saved_settings_on = set()
        for node in DepthFirstIterator(scene.getRoot()):
            if not isinstance(node, CuraSceneNode):
                continue
            node_key = str(id(node))
            try:
                bc = node.callDecoration("getBoundaryConditions")
                if bc is not None and bc.hasAnyBC():
                    bc_dict = bc.toDict()
                    node.metadata[self._BC_METADATA_KEY] = json.dumps(bc_dict)
                    Logger.log("d", "FEA Infill: Saved BCs for node '%s' (%d fixed, %d forces, %d torques)",
                               node.getName(), bc.getFixedFaceCount(),
                               bc.getForceGroupCount(), bc.getTorqueGroupCount())

                    # Get per-node state (fall back to current global state)
                    node_state = self._per_node_state.get(node_key, {})
                    settings = {
                        "material_name": node_state.get("material_name", self._material_name),
                        "infill_pattern": node_state.get("infill_pattern", self._infill_pattern),
                        "min_density": node_state.get("min_density", self._min_density),
                        "max_density": node_state.get("max_density", self._max_density),
                        "num_zones": node_state.get("num_zones", self._num_zones),
                        "max_iterations": node_state.get("max_iterations", self._max_iterations),
                        "mesh_resolution": node_state.get("mesh_resolution", self._mesh_resolution),
                        "safety_factor": node_state.get("safety_factor", self._safety_factor),
                        "bonding_coeff": node_state.get("bonding_coeff", self._bonding_coeff),
                        "optimization_method": node_state.get("optimization_method", self._optimization_method),
                        "volume_fraction": node_state.get("volume_fraction", self._volume_fraction),
                    }
                    node.metadata[self._SETTINGS_METADATA_KEY] = json.dumps(settings)

                    node_results = node_state.get("results")
                    if node_results is not None:
                        results_summary = {
                            "max_stress": node_results.get("max_stress", 0),
                            "min_stress": node_results.get("min_stress", 0),
                            "safety_factor": node_results.get("safety_factor", 0),
                            "iterations": node_results.get("iterations", 0),
                            "converged": node_results.get("converged", False),
                            "mesh_quality": node_results.get("mesh_quality", ""),
                            "mesh_method": node_results.get("mesh_method", ""),
                            "num_zones": len(node_results.get("zones", [])),
                            "zone_densities": [z["density"] for z in node_results.get("zones", [])],
                        }
                        node.metadata[self._RESULTS_METADATA_KEY] = json.dumps(results_summary)
                    elif self._RESULTS_METADATA_KEY in node.metadata:
                        del node.metadata[self._RESULTS_METADATA_KEY]
                    saved_settings_on.add(id(node))
                elif self._BC_METADATA_KEY in node.metadata:
                    del node.metadata[self._BC_METADATA_KEY]
            except Exception:
                Logger.logException("w", "FEA Infill: Failed to save BCs for node '%s'",
                                    node.getName())

        # If there is an active node (results phase) without BCs metadata yet,
        # still persist settings/results so they survive a save-without-BCs edge case.
        if self._active_node_key and (self._results is not None):
            active_node = self._node_cache.get(self._active_node_key)
            if active_node is not None and id(active_node) not in saved_settings_on:
                try:
                    settings = {
                        "material_name": self._material_name,
                        "infill_pattern": self._infill_pattern,
                        "min_density": self._min_density,
                        "max_density": self._max_density,
                        "num_zones": self._num_zones,
                        "max_iterations": self._max_iterations,
                        "mesh_resolution": self._mesh_resolution,
                        "safety_factor": self._safety_factor,
                        "bonding_coeff": self._bonding_coeff,
                        "optimization_method": self._optimization_method,
                        "volume_fraction": self._volume_fraction,
                    }
                    active_node.metadata[self._SETTINGS_METADATA_KEY] = json.dumps(settings)
                    results_summary = {
                        "max_stress": self._results.get("max_stress", 0),
                        "min_stress": self._results.get("min_stress", 0),
                        "safety_factor": self._results.get("safety_factor", 0),
                        "iterations": self._results.get("iterations", 0),
                        "converged": self._results.get("converged", False),
                        "mesh_quality": self._results.get("mesh_quality", ""),
                        "mesh_method": self._results.get("mesh_method", ""),
                        "num_zones": len(self._results.get("zones", [])),
                        "zone_densities": [z["density"] for z in self._results.get("zones", [])],
                    }
                    active_node.metadata[self._RESULTS_METADATA_KEY] = json.dumps(results_summary)
                except Exception:
                    pass

    _last_overlay_cleanup = 0.0

    def _cleanupOrphanedOverlays(self, *args) -> None:
        """Remove stress overlays whose parent model was deleted.

        Throttled to at most once per second since sceneChanged fires
        very frequently (mouse moves, rendering updates, etc.).
        """
        import time as _time
        now = _time.monotonic()
        if now - self._last_overlay_cleanup < 1.0:
            return
        self._last_overlay_cleanup = now
        try:
            from .visualization.stress_overlay import StressOverlayManager
            StressOverlayManager.cleanup_orphaned_overlays()
        except Exception:
            pass

    def _onSceneNodeMayHaveBCMetadata(self, node) -> None:
        """Check a single node for BC metadata and restore if found.

        Connected to sceneChanged — fires for each node as it's added/modified.
        This catches nodes that get their metadata restored asynchronously
        during workspace loading (after workspaceLoaded has already fired).
        Uses a set-based guard (_restored_node_ids) instead of a time throttle
        to ensure all models are restored even when events arrive close together.
        """
        if node is None or not isinstance(node, CuraSceneNode):
            return
        node_id = id(node)
        if node_id in self._restored_node_ids:
            return  # already processed
        try:
            if not hasattr(node, "metadata"):
                return
            if self._BC_METADATA_KEY not in node.metadata:
                return
            # Already has BC decorator — skip
            if node.callDecoration("getBoundaryConditions") is not None:
                self._restored_node_ids.add(node_id)
                return
            import json
            from .FEABoundaryConditionDecorator import FEABoundaryConditionDecorator
            raw = node.metadata[self._BC_METADATA_KEY]
            json_str = raw.value if hasattr(raw, "value") else str(raw)
            bc_data = json.loads(json_str)
            decorator = FEABoundaryConditionDecorator()
            decorator.fromDict(bc_data)
            node.addDecorator(decorator)
            self._restored_node_ids.add(node_id)
            Logger.log("d", "FEA Infill: Restored BCs for node '%s' via sceneChanged "
                       "(%d fixed, %d forces, %d torques)",
                       node.getName(), decorator.getFixedFaceCount(),
                       decorator.getForceGroupCount(), decorator.getTorqueGroupCount())

            # Restore settings and results into per-node state
            node_key = str(node_id)
            self._node_cache[node_key] = node

            if self._SETTINGS_METADATA_KEY in node.metadata:
                self._restoreSettingsFromNode(node)

            if self._RESULTS_METADATA_KEY in node.metadata:
                self._restoreResultsFromNode(node)

            # Save the restored state as per-node state
            self._saveCurrentStateForNode(node_key)
        except Exception:
            pass  # Silently ignore — this fires very frequently

    def _restoreBCsFromScene(self, *args) -> None:
        """Restore BC decorators, settings, and results from node.metadata after loading.

        The 3MF reader puts unknown savitar settings into node.metadata.
        The value might be a Savitar setting object (with .value attr)
        or a plain string — we handle both.
        """
        import json
        from .FEABoundaryConditionDecorator import FEABoundaryConditionDecorator
        scene = CuraApplication.getInstance().getController().getScene()
        for node in DepthFirstIterator(scene.getRoot()):
            if not isinstance(node, CuraSceneNode):
                continue
            if not hasattr(node, "metadata"):
                continue
            if self._BC_METADATA_KEY not in node.metadata:
                continue
            node_id = id(node)
            # Already has a BC decorator — don't overwrite
            if node.callDecoration("getBoundaryConditions") is not None:
                self._restored_node_ids.add(node_id)
                continue
            try:
                raw = node.metadata[self._BC_METADATA_KEY]
                # Handle both plain string and Savitar setting object
                json_str = raw.value if hasattr(raw, "value") else str(raw)
                bc_data = json.loads(json_str)
                decorator = FEABoundaryConditionDecorator()
                decorator.fromDict(bc_data)
                node.addDecorator(decorator)
                self._restored_node_ids.add(node_id)
                Logger.log("d", "FEA Infill: Restored BCs for node '%s' (%d fixed, %d forces)",
                           node.getName(), decorator.getFixedFaceCount(),
                           decorator.getForceGroupCount())
            except Exception:
                Logger.logException("w", "FEA Infill: Failed to restore BCs for node '%s'",
                                    node.getName())

            # Restore analysis settings
            node_key = str(node_id)
            self._node_cache[node_key] = node

            if self._SETTINGS_METADATA_KEY in node.metadata:
                self._restoreSettingsFromNode(node)

            # Restore results summary (puts plugin in review phase)
            if self._RESULTS_METADATA_KEY in node.metadata:
                self._restoreResultsFromNode(node)

            # Save restored state as per-node state
            self._saveCurrentStateForNode(node_key)

    def _restoreSettingsFromNode(self, node) -> None:
        """Read fea_infill_settings from node.metadata and apply to extension state."""
        try:
            raw = node.metadata[self._SETTINGS_METADATA_KEY]
            settings_str = raw.value if hasattr(raw, "value") else str(raw)
            settings = json.loads(settings_str)
            self._material_name = settings.get("material_name", self._material_name)
            self._infill_pattern = settings.get("infill_pattern", self._infill_pattern)
            self._min_density = float(settings.get("min_density", self._min_density))
            self._max_density = float(settings.get("max_density", self._max_density))
            self._num_zones = int(settings.get("num_zones", self._num_zones))
            self._max_iterations = int(settings.get("max_iterations", self._max_iterations))
            self._mesh_resolution = settings.get("mesh_resolution", self._mesh_resolution)
            self._safety_factor = float(settings.get("safety_factor", self._safety_factor))
            self._bonding_coeff = float(settings.get("bonding_coeff", self._bonding_coeff))
            self._optimization_method = settings.get("optimization_method", self._optimization_method)
            self._volume_fraction = float(settings.get("volume_fraction", self._volume_fraction))
            self.settingsChanged.emit()
            Logger.log("d", "FEA Infill: Restored settings from node '%s'", node.getName())
        except Exception:
            Logger.logException("w", "FEA Infill: Failed to restore settings for node '%s'",
                                node.getName())

    def _restoreResultsFromNode(self, node) -> None:
        """Read fea_infill_results from node.metadata and enter review phase."""
        try:
            raw = node.metadata[self._RESULTS_METADATA_KEY]
            results_str = raw.value if hasattr(raw, "value") else str(raw)
            results_summary = json.loads(results_str)
            # Store as partial results — no stress_field/density_field/tet_mesh/zones
            self._results = results_summary
            self._phase = "review"
            self._active_node_key = str(id(node))
            self._node_cache[self._active_node_key] = node
            self.resultsChanged.emit()
            self.phaseChanged.emit()
            Logger.log("d", "FEA Infill: Restored results summary from node '%s' "
                       "(max_stress=%.1f, sf=%.2f)",
                       node.getName(),
                       results_summary.get("max_stress", 0),
                       results_summary.get("safety_factor", 0))
        except Exception:
            Logger.logException("w", "FEA Infill: Failed to restore results for node '%s'",
                                node.getName())

    # -- Per-model state management --

    def _saveCurrentStateForNode(self, node_key: str) -> None:
        """Snapshot the current global analysis state into _per_node_state[node_key]."""
        self._per_node_state[node_key] = {
            "phase": self._phase,
            "results": self._results,
            "analysis_status": self._analysis_status,
            "progress": self._progress,
            "analysis_stage": self._analysis_stage,
            "stress_overlay_visible": self._stress_overlay_visible,
            "error_message": self._error_message,
            "material_name": self._material_name,
            "infill_pattern": self._infill_pattern,
            "min_density": self._min_density,
            "max_density": self._max_density,
            "num_zones": self._num_zones,
            "max_iterations": self._max_iterations,
            "mesh_resolution": self._mesh_resolution,
            "safety_factor": self._safety_factor,
            "bonding_coeff": self._bonding_coeff,
            "optimization_method": self._optimization_method,
            "volume_fraction": self._volume_fraction,
        }

    def _loadStateForNode(self, node_key: str) -> None:
        """Load per-node state into the global Extension properties.

        If no per-node state exists (e.g. old project or new model), resets to defaults.
        """
        state = self._per_node_state.get(node_key, {})
        self._phase = state.get("phase", "define")
        self._results = state.get("results", None)
        self._analysis_status = state.get("analysis_status", "idle")
        self._progress = state.get("progress", 0.0)
        self._analysis_stage = state.get("analysis_stage", "")
        self._stress_overlay_visible = state.get("stress_overlay_visible", False)
        self._error_message = state.get("error_message", "")
        self._material_name = state.get("material_name", self._material_name)
        self._infill_pattern = state.get("infill_pattern", self._infill_pattern)
        self._min_density = state.get("min_density", self._min_density)
        self._max_density = state.get("max_density", self._max_density)
        self._num_zones = state.get("num_zones", self._num_zones)
        self._max_iterations = state.get("max_iterations", self._max_iterations)
        self._mesh_resolution = state.get("mesh_resolution", self._mesh_resolution)
        self._safety_factor = state.get("safety_factor", self._safety_factor)
        self._bonding_coeff = state.get("bonding_coeff", self._bonding_coeff)
        self._optimization_method = state.get("optimization_method", self._optimization_method)
        self._volume_fraction = state.get("volume_fraction", self._volume_fraction)

    def _onSelectionChanged(self) -> None:
        """Switch per-model analysis state when the user selects a different model."""
        # Save current model's state
        if self._active_node_key:
            self._saveCurrentStateForNode(self._active_node_key)

        # Determine the new selection
        selected = Selection.getSelectedObject(0)
        if selected is None or not isinstance(selected, CuraSceneNode):
            return

        new_key = str(id(selected))
        if new_key == self._active_node_key:
            return  # same model, no switch needed

        self._active_node_key = new_key
        self._node_cache[new_key] = selected

        # Load the new model's state (defaults if none stored)
        self._loadStateForNode(new_key)

        # Emit all signals so QML refreshes
        self.phaseChanged.emit()
        self.resultsChanged.emit()
        self.analysisStatusChanged.emit()
        self.progressChanged.emit()
        self.settingsChanged.emit()
        self.errorMessageChanged.emit()
        self.stressOverlayVisibleChanged.emit()

    # -- Phase management --

    @pyqtProperty(str, notify=phaseChanged)
    def phase(self) -> str:
        return self._phase

    @pyqtProperty(str, notify=phaseChanged)
    def activeNodeKey(self) -> str:
        return self._active_node_key

    @pyqtSlot()
    def enterOptimizePhase(self) -> None:
        """Transition from DEFINE to OPTIMIZE phase.

        Called by the BoundaryConditionTool's 'Confirm and Optimize' button.
        Captures the currently selected node and advances the phase.
        """
        from UM.Scene.Selection import Selection
        selected = Selection.getSelectedObject(0)
        if selected is None:
            Logger.log("w", "FEA Infill: No model selected, cannot enter optimize phase")
            return
        self._active_node_key = str(id(selected))
        self._node_cache[self._active_node_key] = selected
        self._phase = "optimize"
        self.phaseChanged.emit()

    @pyqtSlot()
    def goBackToDefine(self) -> None:
        """Return to the DEFINE phase without clearing BCs."""
        self._phase = "define"
        self.phaseChanged.emit()

    @pyqtSlot()
    def goBackToOptimize(self) -> None:
        """Return to the OPTIMIZE phase without clearing results."""
        self._phase = "optimize"
        self.phaseChanged.emit()

    @pyqtSlot()
    def cancelAnalysis(self) -> None:
        """Cancel any running analysis and return to OPTIMIZE phase."""
        self._cancel_requested = True
        # Signal the active job's cancel event so the solver stops promptly
        if hasattr(self, "_active_job") and self._active_job is not None:
            self._active_job.requestCancel()
        self._phase = "optimize"
        self._analysis_status = "idle"
        self._progress = 0.0
        self.phaseChanged.emit()
        self.analysisStatusChanged.emit()
        self.progressChanged.emit()

    # -- Legacy dialog support (Extensions menu entry) --

    def showDialog(self) -> None:
        """Extensions menu entry: activate the BC tool and enter optimize phase."""
        from cura.CuraApplication import CuraApplication as _App
        controller = _App.getInstance().getController()
        controller.setActiveTool("FEAInfillOptimizer")
        # Only advance phase if we already have BCs defined; otherwise stay in define
        from UM.Scene.Selection import Selection
        selected = Selection.getSelectedObject(0)
        if selected is not None:
            bc = selected.callDecoration("getBoundaryConditions")
            if bc is not None and bc.hasAnyBC():
                self.enterOptimizePhase()

    def showDialogForNode(self, node_key: str) -> None:
        """Legacy: called by tool; now delegates to inline phase flow."""
        self._active_node_key = node_key
        # Populate cache so runAnalysis can resolve the node
        self.getSceneNodes()
        self._phase = "optimize"
        self.phaseChanged.emit()

    # -- Dependency management --

    @pyqtProperty(bool, notify=depsAvailableChanged)
    def depsAvailable(self) -> bool:
        return self._deps_available

    @pyqtProperty(str, notify=errorMessageChanged)
    def errorMessage(self) -> str:
        return self._error_message

    @pyqtSlot()
    def installDependencies(self) -> None:
        if self._dep_manager is None:
            return
        from .jobs.dependency_install_job import DependencyInstallJob
        job = DependencyInstallJob(self._dep_manager)
        job.finished.connect(self._onDepsInstalled)
        JobQueue.getInstance().add(job)

    def _onDepsInstalled(self, job) -> None:
        def _apply() -> None:
            result = job.getResult()
            if isinstance(result, Exception):
                Message(
                    str(result),
                    title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                    message_type=Message.MessageType.ERROR
                ).show()
            elif result == "ok":
                Message(
                    i18n_catalog.i18nc("@info:status",
                                       "FEA dependencies installed successfully."),
                    title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                    lifetime=5
                ).show()
            if self._dep_manager:
                self._deps_available = self._dep_manager.all_available()
                self.depsAvailableChanged.emit()
        CuraApplication.getInstance().callLater(_apply)

    # -- Pre-selected node (set by tool's "Confirm and Optimize" button) --

    @pyqtProperty(str, notify=preselectedNodeChanged)
    def preselectedNodeKey(self) -> str:
        return self._preselected_node_key

    # -- Scene node listing --

    @pyqtSlot(result="QVariantList")
    def refreshSceneNodes(self) -> List[Dict[str, Any]]:
        """Refresh and return scene nodes; callable from QML on demand (e.g. on dialog open)."""
        return self.getSceneNodes()

    @pyqtSlot(result="QVariantList")
    def getSceneNodes(self) -> List[Dict[str, Any]]:
        """Return list of printable scene nodes for the target model dropdown.

        Populates ``_node_cache`` (WeakValueDictionary) so that
        ``_getNodeById`` can resolve nodes without relying on id() stability
        across GC cycles (C11).
        """
        nodes = []
        self._node_cache.clear()
        scene = CuraApplication.getInstance().getController().getScene()
        for node in DepthFirstIterator(scene.getRoot()):
            if not isinstance(node, CuraSceneNode):
                continue
            if not node.isSelectable():
                continue
            if node.callDecoration("isNonPrintingMesh"):
                continue
            mesh_data = node.getMeshData()
            if mesh_data is None or mesh_data.getVertices() is None:
                continue
            node_key = str(id(node))
            self._node_cache[node_key] = node
            nodes.append({
                "name": node.getName(),
                "id": node_key
            })
        return nodes

    def _getNodeById(self, node_id) -> Optional[CuraSceneNode]:
        """Resolve a node from the cache populated by ``getSceneNodes``.

        Returns None if the node has been garbage-collected or was never cached.
        """
        return self._node_cache.get(str(node_id))

    # -- Analysis status --

    @pyqtProperty(str, notify=analysisStatusChanged)
    def analysisStatus(self) -> str:
        return self._analysis_status

    @pyqtProperty(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

    @pyqtProperty(str, notify=progressChanged)
    def analysisStage(self) -> str:
        return self._analysis_stage

    # -- Results --

    @pyqtProperty(float, notify=resultsChanged)
    def maxStress(self) -> float:
        if self._results:
            return self._results.get("max_stress", 0.0)
        return 0.0

    @pyqtProperty(float, notify=resultsChanged)
    def minStress(self) -> float:
        if self._results:
            return self._results.get("min_stress", 0.0)
        return 0.0

    @pyqtProperty(float, notify=settingsChanged)
    def safetyFactor(self) -> float:
        return self._safety_factor

    @safetyFactor.setter
    def safetyFactor(self, value: float) -> None:
        if self._safety_factor != value:
            self._safety_factor = value
            self.settingsChanged.emit()

    @pyqtProperty(int, notify=resultsChanged)
    def convergenceIterations(self) -> int:
        if self._results:
            return self._results.get("iterations", 0)
        return 0

    @pyqtProperty(bool, notify=resultsChanged)
    def hasResults(self) -> bool:
        return self._results is not None

    @pyqtProperty(bool, notify=resultsChanged)
    def hasFullResults(self) -> bool:
        """True only when full analysis data (stress_field, zones) is in memory.

        False when results were restored from a saved project file (summary only),
        which means stress overlay and modifier mesh creation are unavailable.
        """
        return (self._results is not None and "stress_field" in self._results)

    @pyqtProperty(bool, notify=stressOverlayVisibleChanged)
    def stressOverlayVisible(self) -> bool:
        return self._stress_overlay_visible

    @pyqtProperty(str, notify=resultsChanged)
    def safetyVerdict(self) -> str:
        if self._results is None:
            return ""
        sf = self._results.get("safety_factor", 0.0)
        if sf <= 0:
            return ""
        if sf < 1.0:
            return "unsafe"
        if sf < 1.5:
            return "marginal"
        if sf < 3.0:
            return "safe"
        return "conservative"

    # -- Boundary condition summary --

    @pyqtSlot(str, result=str)
    def getBCSummary(self, node_id: str) -> str:
        node = self._getNodeById(node_id)
        if node is None:
            return ""
        bc = node.callDecoration("getBoundaryConditions")
        if bc is None:
            return "No boundary conditions defined."

        parts = []
        fixed_count = bc.getFixedFaceCount()
        if fixed_count > 0:
            parts.append(f"Fixed: {fixed_count} faces")
        for i, fg in enumerate(bc.getForceGroups()):
            mag = (fg.force.x**2 + fg.force.y**2 + fg.force.z**2) ** 0.5
            parts.append(f"Force {i+1}: {mag:.0f}N on {len(fg.face_indices)} faces")
        return "\n".join(parts) if parts else "No boundary conditions defined."

    # -- Analysis settings --

    @pyqtProperty(str, notify=settingsChanged)
    def materialName(self) -> str:
        return self._material_name

    @materialName.setter
    def materialName(self, value: str) -> None:
        if self._material_name != value:
            self._material_name = value
            self.settingsChanged.emit()

    @pyqtProperty(str, notify=settingsChanged)
    def materialSummary(self) -> str:
        """Return a human-readable summary of the active material's properties."""
        from .fea.material_database import MaterialDatabase
        mat = MaterialDatabase.get_material(self._material_name)
        parts = [
            f"E = {mat.E_xy:.0f} MPa",
            f"σ_yield = {mat.yield_strength:.0f} MPa",
            f"ν = {mat.nu:.2f}",
            f"k = {mat.bonding_coefficient:.2f}",
        ]
        if mat.failure_mode == "hyperelastic":
            parts.append("⚠ Hyperelastic — linear FEA not valid")
        elif mat.failure_mode == "brittle":
            parts.append("Brittle — von Mises may overestimate strength")
        return " | ".join(parts)

    @pyqtProperty(str, notify=settingsChanged)
    def infillPattern(self) -> str:
        return self._infill_pattern

    @infillPattern.setter
    def infillPattern(self, value: str) -> None:
        if self._infill_pattern != value:
            self._infill_pattern = value
            self.settingsChanged.emit()

    @pyqtProperty(float, notify=settingsChanged)
    def minDensity(self) -> float:
        return self._min_density

    @minDensity.setter
    def minDensity(self, value: float) -> None:
        if self._min_density != value:
            self._min_density = value
            self.settingsChanged.emit()

    @pyqtProperty(float, notify=settingsChanged)
    def maxDensity(self) -> float:
        return self._max_density

    @maxDensity.setter
    def maxDensity(self, value: float) -> None:
        if self._max_density != value:
            self._max_density = value
            self.settingsChanged.emit()

    @pyqtProperty(int, notify=settingsChanged)
    def numZones(self) -> int:
        return self._num_zones

    @numZones.setter
    def numZones(self, value: int) -> None:
        if self._num_zones != value:
            self._num_zones = value
            self.settingsChanged.emit()

    @pyqtProperty(int, notify=settingsChanged)
    def maxIterations(self) -> int:
        return self._max_iterations

    @maxIterations.setter
    def maxIterations(self, value: int) -> None:
        if self._max_iterations != value:
            self._max_iterations = value
            self.settingsChanged.emit()

    @pyqtProperty(float, notify=settingsChanged)
    def bondingCoeff(self) -> float:
        return self._bonding_coeff

    @bondingCoeff.setter
    def bondingCoeff(self, value: float) -> None:
        if self._bonding_coeff != value:
            self._bonding_coeff = value
            self.settingsChanged.emit()

    @pyqtProperty(str, notify=settingsChanged)
    def optimizationMethod(self) -> str:
        return self._optimization_method

    @optimizationMethod.setter
    def optimizationMethod(self, value: str) -> None:
        if self._optimization_method != value:
            self._optimization_method = value
            self.settingsChanged.emit()

    @pyqtProperty(float, notify=settingsChanged)
    def volumeFraction(self) -> float:
        return self._volume_fraction

    @volumeFraction.setter
    def volumeFraction(self, value: float) -> None:
        if self._volume_fraction != value:
            self._volume_fraction = value
            self.settingsChanged.emit()

    # -- FEA Actions --

    @pyqtSlot()
    @pyqtSlot(str)
    def runAnalysis(self, node_id: str = "") -> None:
        """Triggered from QML 'Run Analysis' button.

        Uses _active_node_key when no node_id is provided (inline flow).
        """
        if not node_id:
            node_id = self._active_node_key

        if not self._deps_available:
            missing = self._dep_manager.missing_packages() if self._dep_manager else ["gmsh"]
            Message(
                i18n_catalog.i18nc("@info:status",
                                   "Missing: {packages}\n\n"
                                   "Click 'Install Dependencies' in the Analysis Setup panel, "
                                   "then restart Cura.\n\n"
                                   "Or install manually in Terminal:\n"
                                   "pip3 install {packages}").format(packages=" ".join(missing)),
                title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer — Dependencies Missing"),
                message_type=Message.MessageType.WARNING,
                lifetime=0
            ).show()
            self._phase = "optimize"
            self.phaseChanged.emit()
            return

        node = self._getNodeById(node_id)
        if node is None:
            Logger.log("e", "FEA Infill: Target node not found")
            self._phase = "error"
            self.phaseChanged.emit()
            return

        bc_decorator = node.callDecoration("getBoundaryConditions")
        if bc_decorator is None or not bc_decorator.hasAnyBC():
            Message(
                i18n_catalog.i18nc("@info:status",
                                   "Please define boundary conditions first using the BC Tool."),
                title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                message_type=Message.MessageType.WARNING
            ).show()
            return

        import time as _time
        self._cancel_requested = False
        self._phase = "running"
        self._analysis_status = "running"
        self._progress = 0.0
        self._analysis_start_time = _time.monotonic()
        self.phaseChanged.emit()
        self.analysisStatusChanged.emit()
        self.progressChanged.emit()

        from .jobs.fea_solve_job import FEASolveJob
        from .fea.material_database import MaterialDatabase

        material = MaterialDatabase.get_material(self._material_name)
        config = {
            "min_density": self._min_density / 100.0,
            "max_density": self._max_density / 100.0,
            "n_zones": self._num_zones,
            "infill_pattern": self._infill_pattern,
            "max_iterations": self._max_iterations,
            "mesh_resolution": self._mesh_resolution,
            "safety_factor": self._safety_factor,
            "bonding_coeff": self._bonding_coeff / 100.0,
            "optimization_method": self._optimization_method,
            "volume_fraction": self._volume_fraction / 100.0,
        }

        job = FEASolveJob(node, bc_decorator, material, config)
        job.finished.connect(self._onFEAFinished)
        job.progress.connect(self._onFEAProgress)
        self._active_job = job
        JobQueue.getInstance().add(job)

    _last_progress_time = 0.0

    def _onFEAProgress(self, progress: float) -> None:
        # Marshal to the main thread; this slot may be called from the
        # background Job thread via UM.Signal (C15: thread safety).
        #
        # THROTTLE: Only queue a callLater update at most every 500ms.
        # Without throttling, rapid iterations (2s each) flood the main
        # thread's event queue with callLater closures, freezing the UI.
        import time as _time
        now = _time.monotonic()
        if now - self._last_progress_time < 0.5 and progress < 99:
            return  # skip — too soon since last update
        self._last_progress_time = now

        def _update() -> None:
            self._progress = progress

            elapsed = _time.monotonic() - getattr(self, "_analysis_start_time", _time.monotonic())
            eta_str = ""
            if progress > 10 and progress < 100:
                fraction = progress / 100.0
                if fraction > 0:
                    total_est = elapsed / fraction
                    remaining = total_est - elapsed
                    if remaining > 60:
                        eta_str = " — ~%d min remaining" % int(remaining / 60 + 0.5)
                    elif remaining > 5:
                        eta_str = " — ~%d sec remaining" % int(remaining)

            if progress <= 10:
                self._analysis_stage = "Extracting mesh..."
            elif progress <= 30:
                self._analysis_stage = "Building volume mesh..." + eta_str
            elif progress <= 90:
                fea_frac = (progress - 30.0) / 60.0  # 0.0–1.0 within FEA phase
                iter_float = fea_frac * max(1, self._max_iterations)
                iter_num = max(1, int(iter_float) + 1)
                sub_frac = iter_float - int(iter_float)
                if sub_frac <= 0.2:
                    sub_label = "Assembling stiffness"
                elif sub_frac <= 0.3:
                    sub_label = "Applying BCs"
                elif sub_frac <= 0.7:
                    sub_label = "Solving linear system"
                else:
                    sub_label = "Computing stress"
                self._analysis_stage = "FEA iter %d — %s...%s" % (iter_num, sub_label, eta_str)
            elif progress <= 95:
                self._analysis_stage = "Discretizing density..."
            else:
                self._analysis_stage = "Building zone meshes..."
            self.progressChanged.emit()
        CuraApplication.getInstance().callLater(_update)

    def _onFEAFinished(self, job) -> None:
        result = job.getResult()
        def _apply() -> None:
            if self._cancel_requested:
                return
            if result is None or isinstance(result, Exception):
                self._analysis_status = "error"
                self._phase = "error"
                self._results = None
                error_msg = str(result) if result is not None else "Unknown error (job returned None)"
                Logger.log("e", "FEA Infill: Analysis failed: %s", error_msg)
                self._error_message = error_msg
                self.errorMessageChanged.emit()
                # Show error to user
                Message(
                    i18n_catalog.i18nc("@info:status",
                                       "FEA analysis failed: {error}").format(error=error_msg),
                    title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                    message_type=Message.MessageType.ERROR,
                    lifetime=0
                ).show()
            else:
                self._analysis_status = "complete"
                self._phase = "review"
                self._results = result
                # Auto-show stress overlay on success
                node = self._getNodeById(self._active_node_key)
                if node is not None:
                    try:
                        from .visualization.stress_overlay import StressOverlayManager
                        StressOverlayManager.toggle_overlay(node, self._results)
                        self._stress_overlay_visible = True
                        self.stressOverlayVisibleChanged.emit()
                    except Exception:
                        Logger.logException("w", "FEA Infill: Auto stress overlay failed")
            # Save per-node state after analysis completes
            if self._active_node_key:
                self._saveCurrentStateForNode(self._active_node_key)
            self.phaseChanged.emit()
            self.analysisStatusChanged.emit()
            self.resultsChanged.emit()
        CuraApplication.getInstance().callLater(_apply)

    @pyqtSlot()
    @pyqtSlot(str)
    def applyModifierMeshes(self, node_id: str = "") -> None:
        """Create infill modifier meshes from the FEA results."""
        if not node_id:
            node_id = self._active_node_key

        if self._results is None:
            return

        node = self._getNodeById(node_id)
        if node is None:
            return

        from .mesh_generation.modifier_mesh_creator import create_all_modifier_meshes
        try:
            create_all_modifier_meshes(
                parent_node=node,
                zones=self._results["zones"],
                infill_pattern=self._infill_pattern
            )
            Message(
                i18n_catalog.i18nc("@info:status",
                                   "Modifier meshes applied ({count} zones).").format(
                    count=len(self._results["zones"])),
                title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                lifetime=5
            ).show()
        except Exception as e:
            Logger.log("e", "FEA Infill: Failed to create modifier meshes: %s", str(e))
            Message(
                str(e),
                title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                message_type=Message.MessageType.ERROR
            ).show()

    @pyqtSlot()
    @pyqtSlot(str)
    def showStressOverlay(self, node_id: str = "") -> None:
        """Toggle stress visualization overlay on the model."""
        if not node_id:
            node_id = self._active_node_key
        if self._results is None:
            return
        node = self._getNodeById(node_id)
        if node is None:
            return

        from .visualization.stress_overlay import StressOverlayManager
        StressOverlayManager.toggle_overlay(node, self._results)
        self._stress_overlay_visible = not self._stress_overlay_visible
        self.stressOverlayVisibleChanged.emit()

    @pyqtSlot()
    @pyqtSlot()
    @pyqtSlot(str)
    def clearResults(self, node_id: str = "") -> None:
        """Remove FEA results and any modifier meshes/overlays."""
        if not node_id:
            node_id = self._active_node_key
        self._results = None
        self._analysis_status = "idle"
        self._phase = "define"
        self._progress = 0.0
        self._error_message = ""
        self._stress_overlay_visible = False

        # Update per-node state
        if self._active_node_key:
            self._saveCurrentStateForNode(self._active_node_key)

        self.phaseChanged.emit()
        self.analysisStatusChanged.emit()
        self.progressChanged.emit()
        self.resultsChanged.emit()
        self.errorMessageChanged.emit()
        self.stressOverlayVisibleChanged.emit()

        node = self._getNodeById(node_id)
        if node is None:
            return

        from .visualization.stress_overlay import StressOverlayManager
        StressOverlayManager.remove_overlay(node)

        # Remove existing FEA modifier meshes (children named "FEA Zone ...")
        from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
        from UM.Operations.GroupedOperation import GroupedOperation
        op = GroupedOperation()
        for child in list(node.getChildren()):
            if child.getName().startswith("FEA Zone"):
                op.addOperation(RemoveSceneNodeOperation(child))
        if op.getNumChildrenOperations() > 0:
            op.push()
            CuraApplication.getInstance().getController().getScene().sceneChanged.emit(node)
