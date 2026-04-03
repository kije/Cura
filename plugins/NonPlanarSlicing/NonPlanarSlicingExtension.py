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

# Existing Cura settings we read from the printer profile
MACHINE_HEAD_POLYGON = "machine_head_with_fans_polygon"
MACHINE_GANTRY_HEIGHT = "gantry_height"
MACHINE_NOZZLE_EXPANSION_ANGLE = "machine_nozzle_expansion_angle"
MACHINE_NOZZLE_SIZE = "machine_nozzle_size"

# Non-planar layer types to skip (G-code ;TYPE: values)
SKIP_LINE_TYPES = frozenset([
    "SUPPORT", "SUPPORT-INTERFACE", "PRIME-TOWER", "SKIRT",
])

# Internal constants
HEIGHTMAP_RESOLUTION = 0.5  # mm
SEGMENT_LENGTH = 1.0  # mm, max G-code segment length for subdivision
SAFETY_MARGIN = 1.0  # mm, subtracted from nozzle clearance

# Settings whose changes should invalidate the analysis cache and trigger
# re-analysis.  These affect candidate detection, height maps, or collision
# checking — NOT settings that only affect G-code output.
SETTINGS_INVALIDATE_ANALYSIS = frozenset([
    SETTING_MAX_ANGLE,
    SETTING_SURFACE_MODE,
    SETTING_NOZZLE_CLEARANCE,
    SETTING_MIN_REGION_AREA,
    SETTING_BLEND_DISTANCE,
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

        # Layer data modification after ProcessSlicedLayersJob finishes.
        backend = app.getBackend()
        if backend is not None:
            backend.backendStateChange.connect(self._onBackendStateChanged)

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
            # Machine settings (read-only from printer profile)
            "printhead_polygon": self._getSetting(MACHINE_HEAD_POLYGON, [[-20, 10], [10, 10], [10, -10], [-20, -10]]),
            "gantry_height": float(self._getSetting(MACHINE_GANTRY_HEIGHT, 99999)),
            "nozzle_expansion_angle_deg": float(self._getSetting(MACHINE_NOZZLE_EXPANSION_ANGLE, 45)),
            "nozzle_size_mm": float(self._getSetting(MACHINE_NOZZLE_SIZE, 0.4)),
            # Internal constants
            "heightmap_resolution": HEIGHTMAP_RESOLUTION,
            "segment_length": SEGMENT_LENGTH,
            "safety_margin_mm": SAFETY_MARGIN,
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
                QTimer.singleShot(300, self._runAnalysis)
            else:
                Logger.log("d", "Non-planar slicing disabled — clearing overlays")
                self._clearOverlays()
        elif key in SETTINGS_INVALIDATE_ANALYSIS:
            if self._isEnabled() and self._analysis_entries:
                Logger.log("d", "Setting '%s' changed — re-analyzing", key)
                QTimer.singleShot(300, self._forceReanalyze)

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
        Analysis runs once per model; cache is invalidated by
        setting/transform/removal changes.
        """
        if not self._isEnabled():
            self._clearOverlays()
            return

        settings = self._getSettings()
        scene = Application.getInstance().getController().getScene()
        analyzed_count = 0
        total_candidate_area = 0.0
        total_regions = 0

        # Prune dead weakrefs first
        self._pruneDeadEntries()

        for node in DepthFirstIterator(scene.getRoot()):
            if not self._isSliceableNode(node):
                continue

            # Check if already analyzed (by identity, not id)
            existing = self._getResultForNode(node)
            if existing is not None:
                if existing.candidate_regions is not None:
                    for region in existing.candidate_regions.regions:
                        total_candidate_area += region.total_area
                        total_regions += 1
                    analyzed_count += 1
                continue

            Logger.log("d", "Analyzing node '%s' for non-planar slicing", node.getName())

            result = self._analyzeNode(node, settings)
            if result is not None:
                self._analysis_entries.append((weakref.ref(node), result))
                analyzed_count += 1
                if result.candidate_regions is not None:
                    for region in result.candidate_regions.regions:
                        total_candidate_area += region.total_area
                        total_regions += 1

                # Watch for transform changes on this node
                self._watchNodeTransform(node)

                # Update overlay visualization
                self._updating_overlays = True
                try:
                    self._updateOverlayForNode(node, result, settings)
                finally:
                    self._updating_overlays = False

        if analyzed_count > 0 and total_regions > 0:
            self._showAnalysisMessage(total_regions, total_candidate_area)
        elif analyzed_count > 0:
            Logger.log("d", "Non-planar analysis: no candidate regions found")

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
        with the new transform.
        """
        self._removeResultForNode(node)
        if self._isEnabled():
            Logger.log("d", "Node '%s' transformed — scheduling re-analysis", node.getName())
            QTimer.singleShot(500, self._runAnalysis)

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

    def _analyzeNode(self, node, settings: Dict[str, Any]) -> Optional[_AnalysisResult]:
        """Run the full analysis pipeline on a single mesh node."""
        from .analysis.surface_analyzer import analyze_mesh
        from .analysis.candidate_detector import detect_candidates
        from .analysis.height_map import generate_height_map
        from .analysis.collision_checker import check_collisions

        try:
            mesh_data = node.getMeshData()
            if mesh_data is None:
                return None

            vertices = mesh_data.getVertices()
            if vertices is None or len(vertices) == 0:
                return None

            indices = mesh_data.getIndices()

            # Get world transformation matrix
            transform = node.getWorldTransformation()
            transform_matrix = transform.getData() if transform is not None else None

            # Transform vertices from Cura Y-up scene space to Z-up slicing
            # space BEFORE surface analysis.  analyze_mesh checks
            # face_normals[:,2] (Z component) for "upward", which is only
            # correct in Z-up coordinates.
            zup_vertices = self._transformVertices(vertices, transform_matrix)

            # Step 1: Surface analysis (in Z-up space, no extra transform)
            t0 = time.time()
            analysis = analyze_mesh(zup_vertices, indices, transform_matrix=None)
            Logger.log("d", "Surface analysis: %d faces in %.2fs",
                       len(analysis.face_normals), time.time() - t0)

            # Step 2: Candidate detection
            t0 = time.time()
            candidates = detect_candidates(
                analysis, indices,
                max_angle_deg=settings["max_angle_deg"],
                min_benefit_angle_deg=5.0,
                min_region_area_mm2=settings["min_region_area_mm2"],
            )
            Logger.log("d", "Candidate detection: %d regions in %.2fs",
                       len(candidates.regions), time.time() - t0)

            if not candidates.regions:
                return _AnalysisResult(
                    analysis=analysis, candidate_regions=candidates,
                    height_map=None, collision_result=None,
                    safe_map=None, blend_map=None,
                )

            # Step 3: Height map generation
            t0 = time.time()

            # Reuse the Z-up transformed vertices for height map
            height_map = generate_height_map(
                zup_vertices, indices, candidates.all_candidate_mask,
                resolution=settings["heightmap_resolution"],
            )
            Logger.log("d", "Height map: %dx%d grid in %.2fs",
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
            Logger.log("d", "Collision check: %d safe, %d collision in %.2fs",
                       collision_result.safe_count, collision_result.collision_count,
                       time.time() - t0)

            # Step 5: Compute blend map
            from .gcode.transition_blender import compute_blend_map
            blend_map = compute_blend_map(
                collision_result.safe_map,
                resolution=settings["heightmap_resolution"],
                blend_distance=settings["blend_distance_mm"],
            )

            return _AnalysisResult(
                analysis=analysis,
                candidate_regions=candidates,
                height_map=height_map,
                collision_result=collision_result,
                safe_map=collision_result.safe_map,
                blend_map=blend_map,
            )

        except Exception:
            Logger.logException("e", "Failed to analyze node for non-planar slicing")
            return None

    def _transformVertices(self, vertices: numpy.ndarray,
                           transform_matrix: Optional[numpy.ndarray]) -> numpy.ndarray:
        """Transform vertices to world space, converting Y-up to Z-up."""
        if transform_matrix is not None:
            rot_scale = transform_matrix[:3, :3]
            translate = transform_matrix[:3, 3]
            world_verts = vertices.dot(rot_scale.T) + translate
        else:
            world_verts = vertices.copy()

        # Convert from Y-up (Cura scene) to Z-up (slicing coords)
        result = numpy.empty_like(world_verts)
        result[:, 0] = world_verts[:, 0]   # X stays
        result[:, 1] = -world_verts[:, 2]  # Y = -Z_scene
        result[:, 2] = world_verts[:, 1]   # Z = Y_scene
        return result

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
            "segment_length": settings.get("segment_length", SEGMENT_LENGTH),
            "surface_mode": settings.get("surface_mode", "all_surfaces"),
            "gcode_offset_x": gcode_offset_x,
            "gcode_offset_y": gcode_offset_y,
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

        When slicing starts (Processing), we connect to the
        ProcessSlicedLayersJob's finished signal so we can modify
        layer data AFTER the mesh is built (not before).
        """
        try:
            from UM.Backend.Backend import BackendState
        except ImportError:
            return

        if state == BackendState.Processing:
            # Slicing just started. Monitor for ProcessSlicedLayersJob.
            self._connectToProcessLayersJob()

    def _connectToProcessLayersJob(self) -> None:
        """Connect to the ProcessSlicedLayersJob.finished signal.

        The backend creates this job after receiving layer data from the
        engine. We poll briefly to catch it when it's created.
        """
        if not self._isEnabled():
            return

        backend = Application.getInstance().getBackend()
        if backend is None:
            return

        # The backend stores the job as _process_layers_job.
        # We use a short polling timer to detect when it starts.
        self._layer_job_check_timer = QTimer()
        self._layer_job_check_timer.setInterval(200)
        self._layer_job_check_count = 0

        def _checkForJob():
            self._layer_job_check_count += 1
            job = getattr(backend, "_process_layers_job", None)
            if job is not None:
                self._layer_job_check_timer.stop()
                try:
                    job.finished.connect(self._onProcessLayersFinished)
                    Logger.log("d", "Connected to ProcessSlicedLayersJob.finished")
                except Exception:
                    Logger.logException("w", "Failed to connect to ProcessSlicedLayersJob")
            elif self._layer_job_check_count > 50:  # 10 seconds max
                self._layer_job_check_timer.stop()

        self._layer_job_check_timer.timeout.connect(_checkForJob)
        self._layer_job_check_timer.start()

    def _onProcessLayersFinished(self, job) -> None:
        """Called when ProcessSlicedLayersJob finishes building the layer mesh.

        At this point, the LayerData mesh is fully built and we can
        modify its vertices for non-planar preview.  We also bend the
        G-code in-place so the preview and any subsequent export both
        reflect non-planar changes.
        """
        if not self._isEnabled():
            return
        # Slight delay to ensure the decorator is set on the scene node.
        QTimer.singleShot(100, self._applyNonPlanarPostProcessing)

    def _applyNonPlanarPostProcessing(self) -> None:
        """Apply both layer data modification and G-code bending after slicing."""
        if not self._isEnabled():
            return

        # Ensure analysis has been run. If the user enabled the setting
        # after loading the model, analysis might not have happened yet.
        if self._getActiveAnalysisResult() is None:
            Logger.log("d", "No analysis results at post-processing time; running analysis now")
            self._runAnalysis()

        # 1. Modify layer data for SimulationView preview
        self._modifyLayerData()

        # 2. Bend G-code in-place so preview and export match
        self._bendGCodeInPlace()

    def _modifyLayerData(self) -> None:
        """Modify the built layer data mesh for non-planar preview."""
        analysis_result = self._getActiveAnalysisResult()
        if analysis_result is None or analysis_result.height_map is None:
            return

        if analysis_result.safe_map is None or not numpy.any(analysis_result.safe_map):
            return

        settings = self._getSettings()
        scene = Application.getInstance().getController().getScene()

        from .visualization.layer_data_modifier import LayerDataModifier

        for node in DepthFirstIterator(scene.getRoot()):
            layer_data = node.callDecoration("getLayerData")
            if layer_data is None:
                continue

            try:
                layers = layer_data.getLayers()
                if not layers:
                    continue
                total_layers = max(layers.keys()) + 1 if layers else 0

                layer_height = self._detectLayerHeight(
                    getattr(scene, "gcode_dict", {}).get(0, [""]),
                    settings,
                )

                modifier = LayerDataModifier(
                    height_map=analysis_result.height_map,
                    safe_map=analysis_result.safe_map,
                    blend_map=analysis_result.blend_map,
                    layer_height=layer_height,
                    nonplanar_layer_count=settings["nonplanar_layer_count"],
                    total_layers=total_layers,
                )

                if modifier.modify_layer_data(layer_data):
                    Logger.log("i", "Modified layer data for non-planar preview")
                    # Trigger a scene update so SimulationView re-renders.
                    scene.sceneChanged.emit(node)

            except Exception:
                Logger.logException("w", "Failed to modify layer data for non-planar preview")

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

        try:
            self._overlay.create_overlay(
                parent_node=node,
                vertices=vertices,
                indices=indices,
                candidate_regions=result.candidate_regions,
                collision_result=result.collision_result,
                height_map=result.height_map,
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
