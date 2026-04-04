# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

from __future__ import annotations

import json
import os
import collections
import logging
import time
import weakref
from typing import Optional, Dict, Any, List, TYPE_CHECKING

import numpy

from PyQt6.QtCore import QObject, QTimer

from UM.Application import Application
from UM.Extension import Extension
from UM.Job import Job
from UM.Logger import Logger
from UM.Message import Message
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Camera import Camera
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Settings.ContainerRegistry import ContainerRegistry
from UM.Settings.DefinitionContainer import DefinitionContainer
from UM.Settings.SettingDefinition import SettingDefinition
from UM.i18n import i18nCatalog

if TYPE_CHECKING:
    from UM.Scene.SceneNode import SceneNode
    from cura.CuraApplication import CuraApplication
    from cura.Scene.CuraSceneNode import CuraSceneNode

    from .analysis.surface_analyzer import SurfaceAnalysis
    from .analysis.candidate_detector import CandidateRegions
    from .analysis.height_map import HeightMap
    from .analysis.collision_checker import CollisionResult
    from .visualization.region_overlay import NonPlanarRegionOverlay

catalog = i18nCatalog("cura")
logger = logging.getLogger(__name__)

# Setting keys
SETTING_ENABLED = "nonplanar_enabled"
SETTING_MAX_ANGLE = "nonplanar_max_angle"
SETTING_SURFACE_MODE = "nonplanar_surface_mode"
SETTING_LAYER_COUNT = "nonplanar_layer_count"
SETTING_NOZZLE_CLEARANCE = "nonplanar_nozzle_clearance"
SETTING_MIN_REGION_AREA = "nonplanar_min_region_area"
SETTING_BLEND_DISTANCE = "nonplanar_blend_distance"
SETTING_FLOW_COMPENSATION = "nonplanar_flow_compensation"
SETTING_FEEDRATE_COMPENSATION = "nonplanar_feedrate_compensation"
SETTING_HEIGHTMAP_RESOLUTION = "nonplanar_heightmap_resolution"
SETTING_SEGMENT_LENGTH = "nonplanar_segment_length"
SETTING_SAFETY_MARGIN = "nonplanar_safety_margin"
SETTING_MIN_BENEFIT_ANGLE = "nonplanar_min_benefit_angle"
SETTING_MAX_FLOW_MULTIPLIER = "nonplanar_max_flow_multiplier"
SETTING_MIN_FLOW_MULTIPLIER = "nonplanar_min_flow_multiplier"
SETTING_LINE_TYPES = "nonplanar_line_types"
SETTING_MAX_PATH_DEVIATION = "nonplanar_max_path_deviation"
SETTING_MIN_REGION_WIDTH = "nonplanar_min_region_width"

# Existing Cura settings we read from the printer profile
MACHINE_HEAD_POLYGON = "machine_head_with_fans_polygon"
MACHINE_GANTRY_HEIGHT = "gantry_height"
MACHINE_NOZZLE_EXPANSION_ANGLE = "machine_nozzle_expansion_angle"
MACHINE_NOZZLE_SIZE = "machine_nozzle_size"

# Non-planar layer types to skip (G-code ;TYPE: values)
SKIP_LINE_TYPES = frozenset([
    "SUPPORT", "SUPPORT-INTERFACE", "PRIME-TOWER", "SKIRT",
])

# Settings whose changes should invalidate the analysis cache and trigger
# re-analysis.  These affect candidate detection, height maps, or collision
# checking — NOT settings that only affect G-code output.
SETTINGS_INVALIDATE_ANALYSIS = frozenset([
    SETTING_MAX_ANGLE,
    SETTING_SURFACE_MODE,
    SETTING_NOZZLE_CLEARANCE,
    SETTING_MIN_REGION_AREA,
    SETTING_BLEND_DISTANCE,
    SETTING_HEIGHTMAP_RESOLUTION,
    SETTING_SAFETY_MARGIN,
    SETTING_MIN_BENEFIT_ANGLE,
    SETTING_LINE_TYPES,
    SETTING_MAX_PATH_DEVIATION,
    SETTING_MIN_REGION_WIDTH,
    MACHINE_HEAD_POLYGON,
    MACHINE_GANTRY_HEIGHT,
    MACHINE_NOZZLE_EXPANSION_ANGLE,
    MACHINE_NOZZLE_SIZE,
])


class NonPlanarSlicingExtension(QObject, Extension):
    """Main extension class for the Non-Planar Slicing plugin.

    Orchestrates the full non-planar slicing pipeline:
    1. Injects custom settings into Cura's "experimental" category
    2. Analyzes mesh geometry to identify non-planar candidate regions
    3. After slicing, post-processes G-code to bend top layers onto the model surface
    4. Modifies layer visualization data for the preview
    5. Provides overlay visualization of candidate regions
    """

    def __init__(self) -> None:
        QObject.__init__(self)
        Extension.__init__(self)

        self.setMenuName(catalog.i18nc("@item:inmenu", "Non-Planar Slicing"))
        self.addMenuItem(catalog.i18nc("@item:inmenu", "Toggle Non-Planar Surface Overlay"), self._toggleOverlay)
        self.addMenuItem(catalog.i18nc("@item:inmenu", "Re-analyze Model"), self._forceReanalyze)
        self.addMenuItem(catalog.i18nc("@item:inmenu", "About Non-Planar Slicing"), self._showAboutMessage)

        self._overlay_visible = True

        # Analysis results: maps node weakref → _AnalysisResult.
        # Using a list of (weakref, result) pairs allows automatic cleanup
        # when nodes are garbage collected.  No separate ID set needed.
        self._analysis_entries: List[tuple] = []  # [(weakref.ref(node), _AnalysisResult), ...]
        # Nodes we have connected transformationChanged on
        self._transform_watched_nodes: List[weakref.ref] = []

        # Overlay manager
        self._overlay: Optional[NonPlanarRegionOverlay] = None

        # Settings injection state
        self._settings_injected = False
        self._settings_dict: Dict[str, Any] = {}

        # Guard against re-entrant scene change signals
        self._updating_overlays = False

        # Background analysis job tracking
        self._analysis_job: Optional[_AnalysisJob] = None

        # Debounce timer for re-analysis on settings changes.
        # Using a persistent timer (instead of singleShot) means that rapid
        # sequential changes only trigger one re-analysis after the last change.
        self._reanalyze_timer = QTimer()
        self._reanalyze_timer.setSingleShot(True)
        self._reanalyze_timer.setInterval(500)  # 500ms debounce
        self._reanalyze_timer.timeout.connect(self._forceReanalyze)

        # Debounce timer for transform changes (drag/rotate/scale).
        # Transform events fire at 60+ Hz during interaction — using a
        # single debounce timer prevents hundreds of analysis restarts.
        self._transform_debounce_timer = QTimer()
        self._transform_debounce_timer.setSingleShot(True)
        self._transform_debounce_timer.setInterval(800)  # 800ms debounce
        self._transform_debounce_timer.timeout.connect(self._runAnalysis)

        # Track the currently connected global stack to avoid duplicate signal connections
        self._connected_stack = None

        # Load settings definitions from JSON
        self._loadSettingsDefinitions()

        # Connect signals
        app = Application.getInstance()

        # Settings injection on container load
        ContainerRegistry.getInstance().containerLoadComplete.connect(self._onContainerLoadComplete)

        # Trigger analysis when a file finishes loading (model added to scene)
        app.fileLoaded.connect(self._onFileLoaded)

        # Re-trigger analysis when the user enables non-planar slicing
        # (the setting might be off when the file is first loaded).
        app.globalContainerStackChanged.connect(self._onGlobalStackChanged)

        # G-code post-processing hook (same pattern as PostProcessingPlugin)
        app.getOutputDeviceManager().writeStarted.connect(self._onWriteStarted)

        # Layer data modification after slicing/layer processing finishes.
        backend = app.getBackend()
        if backend is not None:
            backend.backendStateChange.connect(self._onBackendStateChanged)

        # Also hook into view changes: ProcessSlicedLayersJob often runs
        # when the user switches to SimulationView, AFTER slicing is done.
        app.getController().activeViewChanged.connect(self._onActiveViewChanged)

        # Track whether we've already post-processed the current slice
        self._postprocessing_done_for_gcode = False

        Logger.log("i", "Non-Planar Slicing plugin initialized")

    # -------------------------------------------------------------------------
    # Settings Injection (ArcWelder pattern)
    # -------------------------------------------------------------------------

    def _loadSettingsDefinitions(self) -> None:
        """Load setting definitions from the JSON file."""
        settings_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "nonplanar_settings.def.json"
        )
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f, object_pairs_hook=collections.OrderedDict)
            self._settings_dict = data.get("settings", {})
            Logger.log("d", "Loaded %d non-planar setting definitions", len(self._settings_dict))
        except Exception:
            Logger.logException("e", "Failed to load non-planar settings definitions")
            self._settings_dict = {}

    def _onContainerLoadComplete(self, container_id: str) -> None:
        """Inject our settings into the 'experimental' category of fdmprinter definitions."""
        if self._settings_injected:
            return

        # Only inject once the base fdmprinter definition is loaded.
        if container_id != "fdmprinter":
            return

        if not self._settings_dict:
            Logger.log("w", "No non-planar settings to inject")
            return

        container_registry = ContainerRegistry.getInstance()
        # Find all definition containers that have the "experimental" category
        for container in container_registry.findDefinitionContainers():
            if not isinstance(container, DefinitionContainer):
                continue

            # Find the "experimental" category
            experimental_category = None
            try:
                for definition in container.definitions:
                    if definition.key == "experimental":
                        experimental_category = definition
                        break
            except Exception:
                continue

            if experimental_category is None:
                continue

            # Inject our settings into this category
            self._injectSettings(container, experimental_category)

        self._settings_injected = True

        # Make settings visible in the UI
        self._makeSettingsVisible()

        Logger.log("i", "Non-planar settings injected into experimental category")

    def _injectSettings(self, container: DefinitionContainer,
                        category: SettingDefinition) -> None:
        """Inject setting definitions into a category."""
        for setting_key, setting_data in self._settings_dict.items():
            # Check if this setting already exists (avoid duplicates on reload)
            if container.findDefinitions(key=setting_key):
                continue

            try:
                setting_definition = SettingDefinition(setting_key, container, category)
                setting_definition.deserialize(setting_data)

                # Add to the category's children
                category._children.append(setting_definition)

                # Update the container's definition cache
                container._definition_cache[setting_key] = setting_definition

            except Exception:
                Logger.logException("e", "Failed to inject setting: %s", setting_key)

    def _makeSettingsVisible(self) -> None:
        """Add our settings to the visible settings preference."""
        try:
            preferences = Application.getInstance().getPreferences()
            visible_settings = preferences.getValue("general/visible_settings")
            if visible_settings is None:
                return

            our_keys = set(self._settings_dict.keys())
            current_visible = set(visible_settings.split(";"))
            missing = our_keys - current_visible

            if missing:
                new_visible = visible_settings + ";" + ";".join(sorted(missing))
                preferences.setValue("general/visible_settings", new_visible)
                Logger.log("d", "Added %d non-planar settings to visibility", len(missing))
        except Exception:
            Logger.logException("w", "Could not update setting visibility")

    def _emitEnabledChangedForChildren(self) -> None:
        """Force the UI to refresh the 'enabled' state of all child settings.

        Dynamically injected settings don't participate in Uranium's
        automatic relation tracking, so when ``nonplanar_enabled`` changes,
        the QML SettingPropertyProviders for child settings don't know to
        re-evaluate their ``enabled`` expressions.  We fix this by
        explicitly emitting ``propertyChanged(key, "enabled")`` for each
        child setting on the global container stack.
        """
        stack = self._getGlobalStack()
        if stack is None:
            return

        child_keys = [k for k in self._settings_dict if k != SETTING_ENABLED]
        for key in child_keys:
            stack.propertyChanged.emit(key, "enabled")

    # -------------------------------------------------------------------------
    # Settings Access
    # -------------------------------------------------------------------------

    def _getGlobalStack(self):
        """Get the global container stack, or None."""
        return Application.getInstance().getGlobalContainerStack()

    def _getSetting(self, key: str, default=None):
        """Get a setting value from the global stack."""
        stack = self._getGlobalStack()
        if stack is None:
            return default
        value = stack.getProperty(key, "value")
        return value if value is not None else default

    def _isEnabled(self) -> bool:
        """Check if non-planar slicing is enabled."""
        return bool(self._getSetting(SETTING_ENABLED, False))

    def _getSettings(self) -> Dict[str, Any]:
        """Collect all non-planar settings into a dict."""
        return {
            "enabled": self._isEnabled(),
            "max_angle_deg": float(self._getSetting(SETTING_MAX_ANGLE, 30.0)),
            "surface_mode": str(self._getSetting(SETTING_SURFACE_MODE, "all_surfaces")),
            "nonplanar_layer_count": int(self._getSetting(SETTING_LAYER_COUNT, 5)),
            "nozzle_clearance_mm": float(self._getSetting(SETTING_NOZZLE_CLEARANCE, 8.0)),
            "min_region_area_mm2": float(self._getSetting(SETTING_MIN_REGION_AREA, 100.0)),
            "blend_distance_mm": float(self._getSetting(SETTING_BLEND_DISTANCE, 3.0)),
            "flow_compensation": bool(self._getSetting(SETTING_FLOW_COMPENSATION, True)),
            "feedrate_compensation": bool(self._getSetting(SETTING_FEEDRATE_COMPENSATION, True)),
            # Tunable analysis/bending parameters
            "heightmap_resolution": float(self._getSetting(SETTING_HEIGHTMAP_RESOLUTION, 0.5)),
            "segment_length": float(self._getSetting(SETTING_SEGMENT_LENGTH, 1.0)),
            "safety_margin_mm": float(self._getSetting(SETTING_SAFETY_MARGIN, 0.5)),
            "min_benefit_angle_deg": float(self._getSetting(SETTING_MIN_BENEFIT_ANGLE, 5.0)),
            "max_flow_multiplier": float(self._getSetting(SETTING_MAX_FLOW_MULTIPLIER, 2.0)),
            "min_flow_multiplier": float(self._getSetting(SETTING_MIN_FLOW_MULTIPLIER, 0.5)),
            # Line type filtering and path quality
            "nonplanar_line_types": str(self._getSetting(SETTING_LINE_TYPES, "skin_walls")),
            "max_path_deviation": float(self._getSetting(SETTING_MAX_PATH_DEVIATION, 0.4)),
            "min_region_width": float(self._getSetting(SETTING_MIN_REGION_WIDTH, 2.0)),
            # Machine settings (read-only from printer profile)
            "printhead_polygon": self._getSetting(MACHINE_HEAD_POLYGON, [[-20, 10], [10, 10], [10, -10], [-20, -10]]),
            "gantry_height": float(self._getSetting(MACHINE_GANTRY_HEIGHT, 99999)),
            "nozzle_expansion_angle_deg": float(self._getSetting(MACHINE_NOZZLE_EXPANSION_ANGLE, 45)),
            "nozzle_size_mm": float(self._getSetting(MACHINE_NOZZLE_SIZE, 0.4)),
        }

    # -------------------------------------------------------------------------
    # Scene Change Handling & Mesh Analysis
    # -------------------------------------------------------------------------

    def _onGlobalStackChanged(self) -> None:
        """Called when the global container stack changes (printer switch, etc.).

        Disconnects from the old stack and connects to the new one to avoid
        duplicate signal connections.
        """
        # Disconnect from old stack
        if self._connected_stack is not None:
            try:
                self._connected_stack.propertyChanged.disconnect(self._onSettingChanged)
            except Exception:
                pass

        stack = self._getGlobalStack()
        self._connected_stack = stack
        if stack is not None:
            stack.propertyChanged.connect(self._onSettingChanged)

        # Printer change means machine settings (head polygon, gantry height) changed.
        # Invalidate analysis that depends on collision checking.
        if self._analysis_entries:
            Logger.log("d", "Printer changed — invalidating non-planar analysis cache")
            self._forceReanalyze()

    def _onSettingChanged(self, key: str, property_name: str) -> None:
        """Called when any setting changes.

        Handles:
        - ``nonplanar_enabled``: toggle analysis on/off
        - Analysis-affecting settings: invalidate cache and re-analyze
        """
        if property_name != "value":
            return

        if key == SETTING_ENABLED:
            if self._isEnabled():
                Logger.log("d", "Non-planar slicing enabled — scheduling analysis")
                # Ensure our settings are visible in the current preset.
                self._makeSettingsVisible()
                QTimer.singleShot(300, self._runAnalysis)
            else:
                Logger.log("d", "Non-planar slicing disabled — clearing overlays")
                self._clearOverlays()
            # Force the UI to refresh "enabled" state for all dependent
            # settings. Dynamically injected settings don't get automatic
            # relation tracking, so the QML SettingPropertyProviders won't
            # re-evaluate their "enabled" expressions unless we explicitly
            # emit propertyChanged for each child key.
            self._emitEnabledChangedForChildren()
        elif key == SETTING_FLOW_COMPENSATION:
            # Flow multiplier sub-settings depend on this too.
            self._emitEnabledChangedForChildren()
        elif key in SETTINGS_INVALIDATE_ANALYSIS:
            if self._isEnabled() and self._analysis_entries:
                Logger.log("d", "Setting '%s' changed — scheduling re-analysis (debounced)", key)
                # Restart the debounce timer — only the last change in a
                # rapid burst will actually trigger re-analysis.
                self._reanalyze_timer.start()

    def _onFileLoaded(self, file_name: str) -> None:
        """Called when a file finishes loading. Triggers analysis on new models.

        This replaces the sceneChanged-based approach which fired too
        frequently (on every property change, convex hull update, etc.).
        File loading is the correct time to analyze: the mesh is new and
        stable.
        """
        if not self._isEnabled():
            return
        Logger.log("d", "File loaded: %s — scheduling non-planar analysis", file_name)
        # Small delay so the scene node is fully set up (transforms, etc.)
        QTimer.singleShot(500, self._runAnalysis)

    def _runAnalysis(self) -> None:
        """Run mesh analysis on all sliceable nodes in the scene.

        Only analyzes nodes that don't already have a cached result.
        Heavy computation runs on a background thread via ``_AnalysisJob``
        so the UI remains responsive.
        """
        if not self._isEnabled():
            self._clearOverlays()
            return

        settings = self._getSettings()
        scene = Application.getInstance().getController().getScene()

        # Prune dead weakrefs first
        self._pruneDeadEntries()

        # Collect nodes that need analysis
        nodes_to_analyze: List = []
        for node in DepthFirstIterator(scene.getRoot()):
            if not self._isSliceableNode(node):
                continue
            existing = self._getResultForNode(node)
            if existing is not None:
                continue  # already cached
            nodes_to_analyze.append(node)

        if not nodes_to_analyze:
            # All nodes already cached — just show existing results
            self._showCachedResults()
            return

        # Cancel any running analysis job
        if self._analysis_job is not None:
            Logger.log("d", "Cancelling previous analysis job")
            self._analysis_job.cancel()
            self._analysis_job = None

        # Prepare lightweight snapshots for the background thread.
        # We read mesh data and transforms on the main thread (Qt objects
        # are not thread-safe) and pass pure numpy arrays to the job.
        node_snapshots = []
        for node in nodes_to_analyze:
            mesh_data = node.getMeshData()
            if mesh_data is None:
                continue
            vertices = mesh_data.getVertices()
            if vertices is None or len(vertices) == 0:
                continue
            indices = mesh_data.getIndices()
            transform = node.getWorldTransformation()
            transform_matrix = transform.getData() if transform is not None else None
            node_snapshots.append(_NodeSnapshot(
                node=node,
                vertices=numpy.array(vertices),  # writable copy
                indices=numpy.array(indices) if indices is not None else None,
                transform_matrix=numpy.array(transform_matrix) if transform_matrix is not None else None,
                name=node.getName(),
            ))

        if not node_snapshots:
            return

        Logger.log("i", "Starting background analysis for %d node(s)", len(node_snapshots))

        job = _AnalysisJob(node_snapshots, settings, self)
        job.finished.connect(self._onAnalysisJobFinished)
        self._analysis_job = job
        job.start()

    def _onAnalysisJobFinished(self, job: Job) -> None:
        """Handle completed background analysis — runs on the main thread."""
        if job != self._analysis_job:
            return  # stale job (was cancelled and replaced)
        self._analysis_job = None

        if not isinstance(job, _AnalysisJob):
            return

        results = job.getResults()
        if not results:
            return

        settings = self._getSettings()
        total_candidate_area = 0.0
        total_regions = 0
        analyzed_count = 0

        for node, result in results:
            if result is None:
                continue
            # Node may have been removed while analysis was running
            if node is None or not self._isSliceableNode(node):
                continue

            self._analysis_entries.append((weakref.ref(node), result))
            analyzed_count += 1

            if result.candidate_regions is not None:
                for region in result.candidate_regions.regions:
                    total_candidate_area += region.total_area
                    total_regions += 1

            self._watchNodeTransform(node)

            # Update overlay (on main thread — safe for Qt/scene operations)
            self._updating_overlays = True
            try:
                self._updateOverlayForNode(node, result, settings)
            finally:
                self._updating_overlays = False

        # Include already-cached results in the message
        for ref, result in self._analysis_entries:
            n = ref()
            # Skip results we just added (already counted above)
            if n is not None and any(n is node for node, _ in results):
                continue
            if result.candidate_regions is not None:
                for region in result.candidate_regions.regions:
                    total_candidate_area += region.total_area
                    total_regions += 1
                analyzed_count += 1

        if analyzed_count > 0 and total_regions > 0:
            self._showAnalysisMessage(total_regions, total_candidate_area)
        elif analyzed_count > 0:
            Logger.log("d", "Non-planar analysis: no candidate regions found")

        # Analysis is now available — if post-processing was attempted
        # earlier but skipped (no analysis yet), retry it now.
        if not self._postprocessing_done_for_gcode and analyzed_count > 0:
            Logger.log("d", "NonPlanar: analysis just finished — retrying post-processing")
            QTimer.singleShot(200, self._tryApplyPostProcessing)

    def _showCachedResults(self) -> None:
        """Show analysis message from already-cached results."""
        total_candidate_area = 0.0
        total_regions = 0
        for _ref, result in self._analysis_entries:
            if result.candidate_regions is not None:
                for region in result.candidate_regions.regions:
                    total_candidate_area += region.total_area
                    total_regions += 1
        if total_regions > 0:
            self._showAnalysisMessage(total_regions, total_candidate_area)

    def _getResultForNode(self, node) -> Optional[_AnalysisResult]:
        """Look up cached analysis for a node (by identity, not id)."""
        for ref, result in self._analysis_entries:
            n = ref()
            if n is node:
                return result
        return None

    def _removeResultForNode(self, node) -> None:
        """Remove the cached result for a specific node."""
        self._analysis_entries = [
            (ref, res) for ref, res in self._analysis_entries
            if ref() is not None and ref() is not node
        ]

    def _pruneDeadEntries(self) -> None:
        """Remove entries whose node weakrefs have died (node removed from scene)."""
        before = len(self._analysis_entries)
        self._analysis_entries = [
            (ref, res) for ref, res in self._analysis_entries if ref() is not None
        ]
        self._transform_watched_nodes = [
            ref for ref in self._transform_watched_nodes if ref() is not None
        ]
        pruned = before - len(self._analysis_entries)
        if pruned:
            Logger.log("d", "Pruned %d stale analysis cache entries", pruned)

    def _watchNodeTransform(self, node) -> None:
        """Connect to a node's transformationChanged signal (once per node)."""
        for ref in self._transform_watched_nodes:
            if ref() is node:
                return  # Already watching
        try:
            node.transformationChanged.connect(self._onNodeTransformChanged)
            self._transform_watched_nodes.append(weakref.ref(node))
        except Exception:
            pass

    def _onNodeTransformChanged(self, node) -> None:
        """Called when a watched node's transform changes (move/rotate/scale).

        Invalidates the analysis for this node so it will be re-analyzed
        with the new transform.  Uses a debounce timer to avoid hundreds
        of analysis restarts during interactive manipulation (drag fires
        at 60+ Hz).
        """
        self._removeResultForNode(node)
        if self._isEnabled():
            # Restart the debounce timer — only the LAST transform event
            # in a rapid sequence will trigger re-analysis.
            self._transform_debounce_timer.start()

    def _isSliceableNode(self, node) -> bool:
        """Check if a node is a sliceable mesh."""
        if not hasattr(node, "callDecoration"):
            return False
        if not node.callDecoration("isSliceable"):
            return False
        if not node.getMeshData():
            return False
        if not node.isVisible():
            return False
        return True

    # -------------------------------------------------------------------------
    # G-code Post-Processing
    # -------------------------------------------------------------------------

    def _bendGCodeInPlace(self) -> None:
        """Bend G-code in-place right after slicing, so preview reflects changes."""
        if not self._isEnabled():
            return

        scene = Application.getInstance().getController().getScene()
        gcode_dict = getattr(scene, "gcode_dict", None)
        if not gcode_dict:
            return

        from cura.CuraApplication import CuraApplication
        active_plate = CuraApplication.getInstance().getMultiBuildPlateModel().activeBuildPlate
        gcode_list = gcode_dict.get(active_plate)
        if not gcode_list:
            return

        if gcode_list[0] and ";NON-PLANAR PROCESSED" in gcode_list[0]:
            return

        analysis_result = self._getActiveAnalysisResult()
        if analysis_result is None or analysis_result.height_map is None:
            Logger.log("d", "No non-planar analysis available for G-code bending")
            return

        if analysis_result.safe_map is None or not numpy.any(analysis_result.safe_map):
            Logger.log("d", "No safe non-planar regions for G-code bending")
            return

        settings = self._getSettings()
        try:
            t0 = time.time()
            modified_gcode = self._bendGCode(gcode_list, analysis_result, settings)
            gcode_dict[active_plate] = modified_gcode
            setattr(scene, "gcode_dict", gcode_dict)
            Logger.log("i", "Non-planar G-code bending completed in %.2fs", time.time() - t0)
        except Exception:
            Logger.logException("e", "Failed to apply non-planar G-code bending after slicing")

    def _onWriteStarted(self, output_device) -> None:
        """Post-process G-code before export: apply non-planar bending."""
        if not self._isEnabled():
            return

        scene = Application.getInstance().getController().getScene()
        if not hasattr(scene, "gcode_dict"):
            return

        gcode_dict = getattr(scene, "gcode_dict")
        if not gcode_dict:
            return

        from cura.CuraApplication import CuraApplication
        active_plate = CuraApplication.getInstance().getMultiBuildPlateModel().activeBuildPlate
        gcode_list = gcode_dict.get(active_plate)
        if not gcode_list:
            return

        # Check for already-processed G-code
        if gcode_list[0] and ";NON-PLANAR PROCESSED" in gcode_list[0]:
            Logger.log("d", "G-code already non-planar processed, skipping")
            return

        # Find the analysis result for the active scene
        analysis_result = self._getActiveAnalysisResult()
        if analysis_result is None or analysis_result.height_map is None:
            Logger.log("d", "No non-planar analysis available, skipping G-code bending")
            return

        if analysis_result.safe_map is None or not numpy.any(analysis_result.safe_map):
            Logger.log("d", "No safe non-planar regions, skipping G-code bending")
            return

        settings = self._getSettings()

        try:
            t0 = time.time()
            modified_gcode = self._bendGCode(gcode_list, analysis_result, settings)
            elapsed = time.time() - t0

            gcode_dict[active_plate] = modified_gcode
            setattr(scene, "gcode_dict", gcode_dict)

            Logger.log("i", "Non-planar G-code bending completed in %.2fs", elapsed)

            Message(
                catalog.i18nc("@info:status",
                              "Non-planar slicing applied successfully ({time:.1f}s).").format(time=elapsed),
                title=catalog.i18nc("@info:title", "Non-Planar Slicing"),
                message_type=Message.MessageType.POSITIVE,
                lifetime=5,
            ).show()

        except Exception:
            Logger.logException("e", "Failed to apply non-planar G-code bending")
            Message(
                catalog.i18nc("@info:status",
                              "Non-planar slicing failed. Original G-code preserved."),
                title=catalog.i18nc("@info:title", "Non-Planar Slicing"),
                message_type=Message.MessageType.ERROR,
                lifetime=10,
            ).show()

    def _bendGCode(self, gcode_list: List[str], analysis: _AnalysisResult,
                   settings: Dict[str, Any]) -> List[str]:
        """Apply non-planar bending to the G-code."""
        from .gcode.gcode_bender import bend_gcode

        # Detect layer height from G-code
        layer_height = self._detectLayerHeight(gcode_list, settings)

        # Compute G-code ↔ analysis coordinate offset.
        # CuraEngine maps: gcode_X = scene_X + W/2, gcode_Y = -scene_Z + D/2
        # Analysis maps:   analysis_X = scene_X,     analysis_Y = -scene_Z
        # So: analysis = gcode - (W/2, D/2)
        gcode_offset_x = 0.0
        gcode_offset_y = 0.0
        stack = self._getGlobalStack()
        if stack is not None:
            center_is_zero = stack.getProperty("machine_center_is_zero", "value")
            if not center_is_zero:
                machine_width = float(stack.getProperty("machine_width", "value") or 0)
                machine_depth = float(stack.getProperty("machine_depth", "value") or 0)
                gcode_offset_x = machine_width / 2.0
                gcode_offset_y = machine_depth / 2.0

        bender_settings = {
            "layer_height": layer_height,
            "nonplanar_layer_count": settings["nonplanar_layer_count"],
            "max_angle_deg": settings["max_angle_deg"],
            "flow_compensation": settings["flow_compensation"],
            "feedrate_compensation": settings["feedrate_compensation"],
            "segment_length": settings.get("segment_length", 1.0),
            "surface_mode": settings.get("surface_mode", "all_surfaces"),
            "gcode_offset_x": gcode_offset_x,
            "gcode_offset_y": gcode_offset_y,
            "nozzle_clearance": settings.get("nozzle_clearance_mm", 8.0),
            "max_flow_multiplier": settings.get("max_flow_multiplier", 2.0),
            "min_flow_multiplier": settings.get("min_flow_multiplier", 0.5),
            "nonplanar_line_types": settings.get("nonplanar_line_types", "skin_walls"),
            "max_path_deviation": settings.get("max_path_deviation", 0.4),
            "nozzle_size": settings.get("nozzle_size_mm", 0.4),
        }

        return bend_gcode(
            gcode_list,
            height_map=analysis.height_map,
            safe_map=analysis.safe_map,
            blend_map=analysis.blend_map,
            settings=bender_settings,
        )

    def _detectLayerHeight(self, gcode_list: List[str], settings: Dict[str, Any]) -> float:
        """Detect layer height from G-code comments or settings."""
        # Try to find ;Layer height: X.XX in header
        for chunk in gcode_list[:3]:
            for line in chunk.split("\n"):
                if line.startswith(";Layer height:"):
                    try:
                        return float(line.split(":")[1].strip())
                    except (ValueError, IndexError):
                        pass

        # Fall back to the global setting
        stack = self._getGlobalStack()
        if stack is not None:
            lh = stack.getProperty("layer_height", "value")
            if lh is not None and lh > 0:
                return float(lh)

        return 0.2  # safe default

    # -------------------------------------------------------------------------
    # Layer Data Modification (SimulationView preview)
    # -------------------------------------------------------------------------

    def _onBackendStateChanged(self, state) -> None:
        """Called when the backend state changes.

        We track two state transitions:
        - Processing: mark that a new slice is in progress (reset flag)
        - Done: slicing finished; schedule post-processing with a delay
          to allow ProcessSlicedLayersJob to run first
        """
        try:
            from UM.Backend.Backend import BackendState
        except ImportError:
            return

        if state == BackendState.Processing:
            # New slice started — reset the post-processing flag
            self._postprocessing_done_for_gcode = False
        elif state == BackendState.Done:
            if not self._isEnabled():
                return
            # Slicing finished. ProcessSlicedLayersJob may or may not
            # have been started yet (depends on whether SimulationView
            # is active). Schedule a delayed check.
            Logger.log("d", "NonPlanar: slicing done — scheduling post-processing check")
            QTimer.singleShot(500, self._tryApplyPostProcessing)

    def _onActiveViewChanged(self) -> None:
        """Called when the user switches views (e.g. to SimulationView).

        ProcessSlicedLayersJob often starts when switching to
        SimulationView AFTER slicing completes. We schedule a delayed
        post-processing attempt to catch this case.
        """
        if not self._isEnabled():
            return
        if self._postprocessing_done_for_gcode:
            return

        controller = Application.getInstance().getController()
        view = controller.getActiveView()
        if view is not None and view.getPluginId() == "SimulationView":
            Logger.log("d", "NonPlanar: SimulationView activated — scheduling post-processing check")
            # Delay to allow ProcessSlicedLayersJob to finish building
            # the layer mesh (it typically runs a few hundred ms).
            QTimer.singleShot(1000, self._tryApplyPostProcessing)

    def _tryApplyPostProcessing(self) -> None:
        """Attempt post-processing if layer data is available.

        Called from multiple hooks (backend done, view change). Checks
        whether there's layer data to modify and G-code to bend.
        Guards against duplicate processing via _postprocessing_done_for_gcode.
        """
        if not self._isEnabled():
            return
        if self._postprocessing_done_for_gcode:
            return

        # Check if layer data is available (ProcessSlicedLayersJob has finished)
        scene = Application.getInstance().getController().getScene()
        has_layer_data = False
        for node in DepthFirstIterator(scene.getRoot()):
            if node.callDecoration("getLayerData") is not None:
                has_layer_data = True
                break

        if not has_layer_data:
            Logger.log("d", "NonPlanar: no layer data available yet — will retry on view change")
            return

        # Check if there's G-code to process
        gcode_dict = getattr(scene, "gcode_dict", None)
        if not gcode_dict:
            Logger.log("d", "NonPlanar: no G-code available yet")
            return

        Logger.log("i", "NonPlanar: layer data and G-code available — applying post-processing")
        self._postprocessing_done_for_gcode = True
        self._applyNonPlanarPostProcessing()

    def _applyNonPlanarPostProcessing(self) -> None:
        """Apply both layer data modification and G-code bending after slicing."""
        if not self._isEnabled():
            return

        # Ensure analysis has been run. If analysis is not yet available,
        # start a background job (or wait for the running one to finish).
        # NEVER run analysis synchronously — it freezes the UI for 10+ seconds.
        if self._getActiveAnalysisResult() is None:
            if self._analysis_job is not None:
                Logger.log("d", "NonPlanar: analysis job still running — "
                           "post-processing will be retried when it finishes")
            else:
                Logger.log("d", "NonPlanar: no analysis results — starting background analysis")
                # Reset the flag so post-processing will be retried
                # when analysis finishes (via _onAnalysisJobFinished).
                self._postprocessing_done_for_gcode = False
                self._runAnalysis()
            return

        # 1. Modify layer data for SimulationView preview
        self._modifyLayerData()

        # 2. Bend G-code in-place so preview and export match
        self._bendGCodeInPlace()

    def _modifyLayerData(self) -> None:
        """Modify the built layer data mesh for non-planar preview."""
        analysis_result = self._getActiveAnalysisResult()
        if analysis_result is None or analysis_result.height_map is None:
            Logger.log("d", "NonPlanar: _modifyLayerData skipped — no analysis/height_map")
            return

        if analysis_result.safe_map is None or not numpy.any(analysis_result.safe_map):
            Logger.log("d", "NonPlanar: _modifyLayerData skipped — no safe cells")
            return

        Logger.log("i", "NonPlanar: _modifyLayerData starting (safe_cells=%d/%d, blend_range=%.3f..%.3f)",
                    int(numpy.sum(analysis_result.safe_map)),
                    analysis_result.safe_map.size,
                    float(numpy.min(analysis_result.blend_map)),
                    float(numpy.max(analysis_result.blend_map)))

        settings = self._getSettings()
        scene = Application.getInstance().getController().getScene()

        from .visualization.layer_data_modifier import LayerDataModifier

        found_layer_data = False
        for node in DepthFirstIterator(scene.getRoot()):
            layer_data = node.callDecoration("getLayerData")
            if layer_data is None:
                continue

            found_layer_data = True
            try:
                layers = layer_data.getLayers()
                if not layers:
                    Logger.log("d", "NonPlanar: layer_data has no layers")
                    continue
                total_layers = max(layers.keys()) + 1 if layers else 0

                layer_height = self._detectLayerHeight(
                    getattr(scene, "gcode_dict", {}).get(0, [""]),
                    settings,
                )
                Logger.log("d", "NonPlanar: layer_data found with %d layers, layer_height=%.3f",
                            total_layers, layer_height)

                modifier = LayerDataModifier(
                    height_map=analysis_result.height_map,
                    safe_map=analysis_result.safe_map,
                    blend_map=analysis_result.blend_map,
                    layer_height=layer_height,
                    nonplanar_layer_count=settings["nonplanar_layer_count"],
                    total_layers=total_layers,
                    surface_mode=settings.get("surface_mode", "all_surfaces"),
                    nozzle_clearance=settings.get("nozzle_clearance_mm", 8.0),
                    max_path_deviation=settings.get("max_path_deviation", settings.get("nozzle_size_mm", 0.4)),
                )

                new_layer_data = modifier.modify_layer_data(layer_data)
                if new_layer_data is not None:
                    # Replace the LayerData with a NEW object so Uranium's
                    # GPU VBO cache (keyed by MeshData object identity)
                    # is invalidated and fresh vertices are uploaded.
                    from cura.LayerDataDecorator import LayerDataDecorator
                    decorator = node.getDecorator(LayerDataDecorator)
                    if decorator is not None:
                        decorator.setLayerData(new_layer_data)
                    Logger.log("i", "NonPlanar: replaced LayerData for preview — triggering scene update")
                    scene.sceneChanged.emit(node)
                else:
                    Logger.log("w", "NonPlanar: modify_layer_data returned None — no vertices modified")

            except Exception:
                Logger.logException("w", "Failed to modify layer data for non-planar preview")

        if not found_layer_data:
            Logger.log("w", "NonPlanar: _modifyLayerData found no nodes with LayerData")

    # -------------------------------------------------------------------------
    # Overlay Visualization
    # -------------------------------------------------------------------------

    def _updateOverlayForNode(self, node, result: _AnalysisResult,
                              settings: Dict[str, Any]) -> None:
        """Update the overlay visualization for a scene node."""
        if not self._overlay_visible:
            return

        from .visualization.region_overlay import NonPlanarRegionOverlay

        if self._overlay is None:
            self._overlay = NonPlanarRegionOverlay()

        # Remove previous overlays
        self._overlay.remove_overlays()

        if result.candidate_regions is None or not result.candidate_regions.regions:
            return

        mesh_data = node.getMeshData()
        if mesh_data is None:
            return

        vertices = mesh_data.getVertices()
        indices = mesh_data.getIndices()

        if vertices is None:
            return

        # Get scene root for ConvexHullNode-style parenting (world-space
        # overlay vertices parented to the root avoid transform caching issues).
        scene_root = Application.getInstance().getController().getScene().getRoot()

        try:
            self._overlay.create_overlay(
                parent_node=node,
                vertices=vertices,
                indices=indices,
                candidate_regions=result.candidate_regions,
                collision_result=result.collision_result,
                height_map=result.height_map,
                scene_root=scene_root,
            )
        except Exception:
            Logger.logException("w", "Failed to create non-planar region overlay")

    def _clearOverlays(self) -> None:
        """Remove all non-planar overlay visualizations."""
        if self._overlay is not None:
            self._overlay.remove_overlays()

    def _toggleOverlay(self) -> None:
        """Toggle the non-planar region overlay on/off."""
        self._overlay_visible = not self._overlay_visible
        if self._overlay_visible:
            # Rebuild overlays from cache, or run fresh analysis if needed
            self._rebuildOverlaysFromCache()
            self._runAnalysis()
            Message(
                catalog.i18nc("@info:status", "Non-planar overlay enabled."),
                title=catalog.i18nc("@info:title", "Non-Planar Slicing"),
                message_type=Message.MessageType.NEUTRAL,
                lifetime=3,
            ).show()
        else:
            self._clearOverlays()
            Message(
                catalog.i18nc("@info:status", "Non-planar overlay hidden."),
                title=catalog.i18nc("@info:title", "Non-Planar Slicing"),
                message_type=Message.MessageType.NEUTRAL,
                lifetime=3,
            ).show()

    def _rebuildOverlaysFromCache(self) -> None:
        """Recreate overlay visuals from cached analysis (no re-computation)."""
        if not self._overlay_visible:
            return
        settings = self._getSettings()
        self._updating_overlays = True
        try:
            for ref, result in self._analysis_entries:
                node = ref()
                if node is not None and self._isSliceableNode(node):
                    self._updateOverlayForNode(node, result, settings)
        finally:
            self._updating_overlays = False

    def _forceReanalyze(self) -> None:
        """Clear cache and re-run analysis on all models."""
        if self._analysis_job is not None:
            self._analysis_job.cancel()
            self._analysis_job = None
        self._analysis_entries.clear()
        self._clearOverlays()
        self._runAnalysis()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _getActiveAnalysisResult(self) -> Optional[_AnalysisResult]:
        """Get the analysis result for the active/visible scene nodes."""
        self._pruneDeadEntries()
        scene = Application.getInstance().getController().getScene()
        for node in DepthFirstIterator(scene.getRoot()):
            if not self._isSliceableNode(node):
                continue
            result = self._getResultForNode(node)
            if result is not None and result.height_map is not None:
                return result
        return None

    def _showAnalysisMessage(self, region_count: int, total_area: float) -> None:
        """Show a message about the analysis results."""
        Message(
            catalog.i18nc("@info:status",
                          "Non-planar slicing: found {count} region(s) "
                          "covering {area:.0f} mm\u00b2.").format(
                count=region_count, area=total_area),
            title=catalog.i18nc("@info:title", "Non-Planar Slicing"),
            message_type=Message.MessageType.POSITIVE,
            lifetime=8,
        ).show()

    def _showAboutMessage(self) -> None:
        """Show information about the plugin."""
        Message(
            catalog.i18nc("@info:status",
                          "Non-Planar Slicing (Experimental)\n\n"
                          "This plugin applies curved layers to top surfaces of your model, "
                          "eliminating stair-stepping artifacts on inclined surfaces.\n\n"
                          "Enable it in Print Settings > Experimental > Non-Planar Slicing.\n\n"
                          "The plugin automatically detects suitable regions and verifies "
                          "that the printhead will not collide with the model."),
            title=catalog.i18nc("@info:title", "Non-Planar Slicing"),
            message_type=Message.MessageType.NEUTRAL,
            lifetime=0,
        ).show()


class _AnalysisResult:
    """Stores the results of the non-planar analysis pipeline for a mesh node."""

    __slots__ = [
        "analysis", "candidate_regions", "height_map",
        "collision_result", "safe_map", "blend_map",
    ]

    def __init__(
        self,
        analysis: Optional[SurfaceAnalysis] = None,
        candidate_regions: Optional[CandidateRegions] = None,
        height_map: Optional[HeightMap] = None,
        collision_result: Optional[CollisionResult] = None,
        safe_map: Optional[numpy.ndarray] = None,
        blend_map: Optional[numpy.ndarray] = None,
    ) -> None:
        self.analysis = analysis
        self.candidate_regions = candidate_regions
        self.height_map = height_map
        self.collision_result = collision_result
        self.safe_map = safe_map
        self.blend_map = blend_map


class _NodeSnapshot:
    """Thread-safe snapshot of a scene node's mesh data for background analysis.

    Qt scene objects are not thread-safe, so we read all necessary data
    on the main thread and pass pure numpy arrays to the background job.
    """

    __slots__ = ["node", "vertices", "indices", "transform_matrix", "name"]

    def __init__(
        self,
        node,
        vertices: numpy.ndarray,
        indices: Optional[numpy.ndarray],
        transform_matrix: Optional[numpy.ndarray],
        name: str,
    ) -> None:
        self.node = node  # kept for result association (only accessed on main thread)
        self.vertices = vertices
        self.indices = indices
        self.transform_matrix = transform_matrix
        self.name = name


class _AnalysisJob(Job):
    """Runs the non-planar mesh analysis pipeline on a background thread.

    This handles the CPU-heavy work: surface analysis, candidate detection,
    height map generation, collision checking, and blend map computation.
    The results are collected and made available via ``getResults()`` for
    the main thread to store in the cache and update overlays.
    """

    def __init__(
        self,
        snapshots: List[_NodeSnapshot],
        settings: Dict[str, Any],
        extension: "NonPlanarSlicingExtension",
    ) -> None:
        super().__init__()
        self._snapshots = snapshots
        self._settings = settings
        self._extension = extension
        self._results: List[tuple] = []  # [(node, _AnalysisResult | None), ...]
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True
        super().cancel()

    def getResults(self) -> List[tuple]:
        return self._results

    @staticmethod
    def _get_analysis_imports():
        from .analysis.surface_analyzer import analyze_mesh
        from .analysis.candidate_detector import detect_candidates
        from .analysis.height_map import generate_height_map
        from .analysis.collision_checker import check_collisions
        from .gcode.transition_blender import compute_blend_map
        return analyze_mesh, detect_candidates, generate_height_map, check_collisions, compute_blend_map

    def run(self) -> None:
        imports = self._get_analysis_imports()

        for snapshot in self._snapshots:
            if self._cancel:
                return

            Logger.log("d", "Background analysis: processing '%s'", snapshot.name)

            try:
                result = self._analyzeSnapshot(
                    snapshot, self._settings, *imports,
                )
                self._results.append((snapshot.node, result))
            except Exception:
                Logger.logException("e", "Background analysis failed for '%s'", snapshot.name)
                self._results.append((snapshot.node, None))

            Job.yieldThread()

    @staticmethod
    def _analyzeSnapshot(
        snapshot: _NodeSnapshot,
        settings: Dict[str, Any],
        analyze_mesh, detect_candidates,
        generate_height_map, check_collisions,
        compute_blend_map,
    ) -> Optional[_AnalysisResult]:
        """Run the full analysis pipeline on a node snapshot.

        This is the same logic as the old ``_analyzeNode`` but operates
        on pre-extracted numpy arrays instead of live scene objects.
        """
        vertices = snapshot.vertices
        indices = snapshot.indices
        transform_matrix = snapshot.transform_matrix

        # Transform vertices from Y-up (Cura scene) to Z-up (slicing coords)
        if transform_matrix is not None:
            rot_scale = transform_matrix[:3, :3]
            translate = transform_matrix[:3, 3]
            world_verts = vertices.dot(rot_scale.T) + translate
        else:
            world_verts = vertices.copy()

        zup_vertices = numpy.empty_like(world_verts)
        zup_vertices[:, 0] = world_verts[:, 0]   # X stays
        zup_vertices[:, 1] = -world_verts[:, 2]  # Y = -Z_scene
        zup_vertices[:, 2] = world_verts[:, 1]   # Z = Y_scene

        # Step 1: Surface analysis
        t0 = time.time()
        analysis = analyze_mesh(zup_vertices, indices, transform_matrix=None)
        Logger.log("d", "  Surface analysis: %d faces in %.2fs",
                   len(analysis.face_normals), time.time() - t0)

        # Step 2: Candidate detection
        t0 = time.time()
        candidates = detect_candidates(
            analysis, indices,
            max_angle_deg=settings["max_angle_deg"],
            min_benefit_angle_deg=settings["min_benefit_angle_deg"],
            min_region_area_mm2=settings["min_region_area_mm2"],
        )
        Logger.log("d", "  Candidate detection: %d regions in %.2fs",
                   len(candidates.regions), time.time() - t0)

        if not candidates.regions:
            return _AnalysisResult(
                analysis=analysis, candidate_regions=candidates,
                height_map=None, collision_result=None,
                safe_map=None, blend_map=None,
            )

        # Step 3: Height map generation
        t0 = time.time()
        height_map = generate_height_map(
            zup_vertices, indices, candidates.all_candidate_mask,
            resolution=settings["heightmap_resolution"],
        )
        Logger.log("d", "  Height map: %dx%d grid in %.2fs",
                   height_map.grid_shape[0], height_map.grid_shape[1],
                   time.time() - t0)

        # Step 4: Collision checking
        t0 = time.time()
        collision_result = check_collisions(
            height_map,
            printhead_polygon=settings["printhead_polygon"],
            nozzle_clearance_mm=settings["nozzle_clearance_mm"],
            nozzle_expansion_angle_deg=settings["nozzle_expansion_angle_deg"],
            safety_margin_mm=settings["safety_margin_mm"],
        )
        Logger.log("d", "  Collision check: %d safe, %d collision in %.2fs",
                   collision_result.safe_count, collision_result.collision_count,
                   time.time() - t0)

        # Step 5: Erode safe_map to remove isolated/narrow regions.
        # This prevents isolated raised paths that would print without
        # neighboring support.  The erosion radius is min_region_width.
        safe_map = collision_result.safe_map
        min_region_width = settings.get("min_region_width", 2.0)
        resolution = settings["heightmap_resolution"]
        if min_region_width > 0.0 and resolution > 0.0:
            erosion_cells = max(1, int(round(min_region_width / resolution)))
            try:
                from scipy.ndimage import binary_erosion, binary_dilation
                # Erode then dilate (opening) to remove narrow features
                # while preserving the shape of wide regions.
                structure = numpy.ones((2 * erosion_cells + 1, 2 * erosion_cells + 1), dtype=bool)
                eroded = binary_erosion(safe_map, structure=structure)
                safe_map = binary_dilation(eroded, structure=structure).astype(bool)
                removed = int(numpy.count_nonzero(collision_result.safe_map) - numpy.count_nonzero(safe_map))
                if removed > 0:
                    Logger.log("d", "  Region erosion: removed %d isolated cells (radius=%d cells)",
                               removed, erosion_cells)
            except ImportError:
                # scipy not available — skip erosion
                Logger.log("w", "scipy not available — skipping safe_map erosion")

        # Step 6: Compute blend map
        blend_map_arr = compute_blend_map(
            safe_map,
            resolution=resolution,
            blend_distance=settings["blend_distance_mm"],
        )

        return _AnalysisResult(
            analysis=analysis,
            candidate_regions=candidates,
            height_map=height_map,
            collision_result=collision_result,
            safe_map=safe_map,
            blend_map=blend_map_arr,
        )
