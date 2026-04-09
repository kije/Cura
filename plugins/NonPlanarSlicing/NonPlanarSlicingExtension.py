# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

from __future__ import annotations

import json
import os
import collections
import logging
import struct
import tempfile
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
SETTING_NOZZLE_CLEARANCE = "nonplanar_nozzle_clearance"
SETTING_MIN_REGION_AREA = "nonplanar_min_region_area"
SETTING_BLEND_DISTANCE = "nonplanar_blend_distance"
SETTING_HEIGHTMAP_RESOLUTION = "nonplanar_heightmap_resolution"
SETTING_SAFETY_MARGIN = "nonplanar_safety_margin"
SETTING_MIN_BENEFIT_ANGLE = "nonplanar_min_benefit_angle"
SETTING_FIELD_DECAY_MM = "nonplanar_field_decay_mm"
SETTING_MIN_THICKNESS_RATIO = "nonplanar_min_thickness_ratio"
SETTING_MAX_THICKNESS_RATIO = "nonplanar_max_thickness_ratio"
SETTING_OPTIMIZATION_RESOLUTION = "nonplanar_optimization_resolution"

# Existing Cura settings we read from the printer profile
MACHINE_HEAD_POLYGON = "machine_head_with_fans_polygon"
MACHINE_GANTRY_HEIGHT = "gantry_height"
MACHINE_NOZZLE_EXPANSION_ANGLE = "machine_nozzle_expansion_angle"
MACHINE_NOZZLE_SIZE = "machine_nozzle_size"

# Settings whose changes should invalidate the analysis cache and trigger
# re-analysis.
SETTINGS_INVALIDATE_ANALYSIS = frozenset([
    SETTING_MAX_ANGLE,
    SETTING_NOZZLE_CLEARANCE,
    SETTING_MIN_REGION_AREA,
    SETTING_BLEND_DISTANCE,
    SETTING_HEIGHTMAP_RESOLUTION,
    SETTING_SAFETY_MARGIN,
    SETTING_MIN_BENEFIT_ANGLE,
    SETTING_FIELD_DECAY_MM,
    SETTING_MIN_THICKNESS_RATIO,
    SETTING_MAX_THICKNESS_RATIO,
    SETTING_OPTIMIZATION_RESOLUTION,
    MACHINE_HEAD_POLYGON,
    MACHINE_GANTRY_HEIGHT,
    MACHINE_NOZZLE_EXPANSION_ANGLE,
    MACHINE_NOZZLE_SIZE,
])

# NPDF serialization constants
_NPDF_MAGIC = b"NPDF"
_NPDF_VERSION = 1


class NonPlanarSlicingExtension(QObject, Extension):
    """Main extension class for the Non-Planar Slicing plugin.

    Orchestrates the non-planar slicing pipeline:
    1. Injects custom settings into Cura's "experimental" category
    2. Analyzes mesh geometry to identify non-planar candidate regions
    3. Before slicing, deforms the mesh and serializes the deformation field
    4. The CuraEngine plugin (NonPlanarEnginePlugin) inverse-transforms paths
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

        # Connect backend signals for mesh deformation lifecycle.
        backend = app.getBackend()
        if backend is not None:
            backend.backendStateChange.connect(self._onBackendStateChanged)
            try:
                backend.slicingStarted.connect(self._onSlicingStarted)
            except AttributeError:
                pass  # Signal may not exist in all Cura versions.

        # Track original meshes for deform/restore cycle.
        self._mesh_swap_originals: Dict[int, tuple] = {}  # id(node) → (node, MeshData)
        self._mesh_swap_active = False

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
            "nozzle_clearance_mm": float(self._getSetting(SETTING_NOZZLE_CLEARANCE, 8.0)),
            "min_region_area_mm2": float(self._getSetting(SETTING_MIN_REGION_AREA, 100.0)),
            "blend_distance_mm": float(self._getSetting(SETTING_BLEND_DISTANCE, 3.0)),
            "heightmap_resolution": float(self._getSetting(SETTING_HEIGHTMAP_RESOLUTION, 0.5)),
            "safety_margin_mm": float(self._getSetting(SETTING_SAFETY_MARGIN, 0.5)),
            "min_benefit_angle_deg": float(self._getSetting(SETTING_MIN_BENEFIT_ANGLE, 5.0)),
            "field_decay_mm": float(self._getSetting(SETTING_FIELD_DECAY_MM, 5.0)),
            "min_thickness_ratio": float(self._getSetting(SETTING_MIN_THICKNESS_RATIO, 0.5)),
            "max_thickness_ratio": float(self._getSetting(SETTING_MAX_THICKNESS_RATIO, 2.0)),
            "optimization_resolution": float(self._getSetting(SETTING_OPTIMIZATION_RESOLUTION, 2.0)),
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

    def _runAnalysisSynchronous(self) -> None:
        """Run analysis synchronously on the main thread.

        Used as fallback when post-processing needs results immediately
        (e.g. when the user enabled the setting after loading the model
        and slicing has already finished).
        """
        settings = self._getSettings()
        scene = Application.getInstance().getController().getScene()

        for node in DepthFirstIterator(scene.getRoot()):
            if not self._isSliceableNode(node):
                continue
            if self._getResultForNode(node) is not None:
                continue

            mesh_data = node.getMeshData()
            if mesh_data is None:
                continue
            vertices = mesh_data.getVertices()
            if vertices is None or len(vertices) == 0:
                continue
            indices = mesh_data.getIndices()
            transform = node.getWorldTransformation()
            transform_matrix = transform.getData() if transform is not None else None

            snapshot = _NodeSnapshot(
                node=node,
                vertices=numpy.array(vertices),
                indices=numpy.array(indices) if indices is not None else None,
                transform_matrix=numpy.array(transform_matrix) if transform_matrix is not None else None,
                name=node.getName(),
            )

            Logger.log("d", "Running synchronous analysis for '%s'", node.getName())
            result = _AnalysisJob._analyzeSnapshot(
                snapshot, settings,
                *_AnalysisJob._get_analysis_imports(),
            )
            if result is not None:
                self._analysis_entries.append((weakref.ref(node), result))
                self._watchNodeTransform(node)
                self._updating_overlays = True
                try:
                    self._updateOverlayForNode(node, result, settings)
                finally:
                    self._updating_overlays = False

    # -------------------------------------------------------------------------
    # Deformation Field Serialization
    # -------------------------------------------------------------------------

    @staticmethod
    def _serialize_deformation_field(field) -> bytes:
        """Serialize a DeformationField to NPDF binary format.

        Layout (little-endian):
          magic: 4 bytes = b"NPDF"
          version: u16 = 1
          num_layers: u32
          rows: u32
          cols: u32
          x_min, x_max, y_min, y_max, resolution: 5 x f64
          z_levels: [f32; num_layers]
          displacements: [f32; num_layers * rows * cols]
        """
        rows, cols = field.grid_shape
        header = struct.pack(
            "<4sHIII5d",
            _NPDF_MAGIC,
            _NPDF_VERSION,
            field.num_layers,
            rows,
            cols,
            field.x_min,
            field.x_max,
            field.y_min,
            field.y_max,
            field.resolution,
        )
        z_levels = field.z_levels.astype(numpy.float32).tobytes()
        displacements = field.displacements.astype(numpy.float32).tobytes()
        return header + z_levels + displacements

    # -------------------------------------------------------------------------
    # Mesh Deformation (pre-slicing) & Engine Plugin Integration
    # -------------------------------------------------------------------------

    def _onSlicingStarted(self) -> None:
        """Hook into slicingStarted to deform meshes and serialize deformation field.

        Deforms mesh vertices using the deformation field before CuraEngine
        slices them. The originals are saved for restoration after slicing.
        The serialized deformation field is stored as a setting so the
        CuraEngine plugin receives it via settings broadcast and can
        inverse-transform the toolpath Z coordinates.
        """
        settings = self._getSettings()
        if not settings.get("enabled", False):
            return

        # Find analysis results with a deformation field.
        analysis_result = self._getActiveAnalysisResult()
        if analysis_result is None or analysis_result.deformation_field is None:
            return

        try:
            from .analysis.mesh_deformer import deform_mesh_vertices

            app = Application.getInstance()
            scene = app.getController().getScene()
            backend = app.getBackend()

            deformation_field = analysis_result.deformation_field

            # Serialize the deformation field to a temp file.
            # Binary data cannot survive the settings broadcast pipeline
            # (str() serialization corrupts bytes), so we write to a file
            # and pass the path as a string setting.
            try:
                field_bytes = self._serialize_deformation_field(deformation_field)
                Logger.log("i", "Serialized deformation field: %d bytes", len(field_bytes))

                from UM.Resources import Resources
                field_path = os.path.join(
                    Resources.getDataStoragePath(),
                    "nonplanar_deformation_field.bin",
                )
                with open(field_path, "wb") as f:
                    f.write(field_bytes)

                # Store the file path as a setting for broadcast to the
                # engine plugin. String settings survive the str() pipeline.
                stack = self._getGlobalStack()
                if stack is not None:
                    stack.setProperty("nonplanar_deformation_field", "value", field_path)
                    Logger.log("i", "Deformation field written to %s", field_path)
            except Exception:
                Logger.logException("e", "Failed to serialize deformation field")

            # Suppress sceneChanged to prevent recursive re-slicing.
            if backend is not None:
                try:
                    scene.sceneChanged.disconnect(backend._onSceneChanged)
                except (TypeError, AttributeError):
                    pass

            try:
                self._mesh_swap_originals.clear()
                swap_count = 0

                for node in DepthFirstIterator(scene.getRoot()):
                    if not hasattr(node, "callDecoration"):
                        continue
                    if not node.callDecoration("isSliceable"):
                        continue

                    mesh_data = node.getMeshData()
                    if mesh_data is None:
                        continue

                    vertices = mesh_data.getVertices()
                    if vertices is None or len(vertices) == 0:
                        continue

                    # Save original for restoration.
                    self._mesh_swap_originals[id(node)] = (node, mesh_data)

                    # Deform vertices (scene coordinates: Y-up).
                    deformed_verts = deform_mesh_vertices(
                        vertices, deformation_field, z_up=False,
                    )

                    # Create new MeshData with deformed vertices.
                    from UM.Mesh.MeshData import MeshData
                    new_mesh = MeshData(
                        vertices=deformed_verts,
                        indices=mesh_data.getIndices(),
                        normals=mesh_data.getNormals(),
                    )
                    node.setMeshData(new_mesh)
                    swap_count += 1

                self._mesh_swap_active = swap_count > 0
                if swap_count > 0:
                    Logger.log("i", "Non-planar: deformed %d meshes for slicing", swap_count)

            finally:
                # Always reconnect sceneChanged.
                if backend is not None:
                    try:
                        scene.sceneChanged.connect(backend._onSceneChanged)
                    except (TypeError, AttributeError):
                        pass

        except Exception:
            Logger.logException("e", "Non-planar: mesh deformation failed")
            self._restoreMeshes()

    def _restoreMeshes(self) -> None:
        """Restore original meshes after slicing completes."""
        if not self._mesh_swap_originals:
            return

        app = Application.getInstance()
        scene = app.getController().getScene()
        backend = app.getBackend()

        # Suppress sceneChanged during restoration.
        if scene is not None and backend is not None:
            try:
                scene.sceneChanged.disconnect(backend._onSceneChanged)
            except (TypeError, AttributeError):
                pass

        try:
            restored = 0
            for node_id, (node, original_mesh) in self._mesh_swap_originals.items():
                try:
                    node.setMeshData(original_mesh)
                    restored += 1
                except Exception:
                    Logger.logException("w", "Failed to restore mesh for node %s", node_id)

            if restored > 0:
                Logger.log("i", "Non-planar: restored %d original meshes", restored)
        finally:
            self._mesh_swap_originals.clear()
            self._mesh_swap_active = False
            if scene is not None and backend is not None:
                try:
                    scene.sceneChanged.connect(backend._onSceneChanged)
                except (TypeError, AttributeError):
                    pass

    # -------------------------------------------------------------------------
    # Backend State Handling
    # -------------------------------------------------------------------------

    def _onBackendStateChanged(self, state) -> None:
        """Called when the backend state changes.

        Restores original meshes when slicing completes. The CuraEngine
        plugin handles the inverse Z transform — no G-code post-processing
        is needed here.
        """
        try:
            from UM.Backend.Backend import BackendState
        except ImportError:
            return

        if state in (BackendState.Done, BackendState.Error, BackendState.Disabled):
            # Restore original meshes after slicing completes (or fails)
            if self._mesh_swap_active:
                self._restoreMeshes()
                Logger.log("i", "Non-planar: restored original meshes (state=%s)", state)

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
        "deformation_field",
    ]

    def __init__(
        self,
        analysis: Optional[SurfaceAnalysis] = None,
        candidate_regions: Optional[CandidateRegions] = None,
        height_map: Optional[HeightMap] = None,
        collision_result: Optional[CollisionResult] = None,
        safe_map: Optional[numpy.ndarray] = None,
        blend_map: Optional[numpy.ndarray] = None,
        deformation_field=None,
    ) -> None:
        self.analysis = analysis
        self.candidate_regions = candidate_regions
        self.height_map = height_map
        self.collision_result = collision_result
        self.safe_map = safe_map
        self.blend_map = blend_map
        self.deformation_field = deformation_field


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
        from .analysis.transition_blender import compute_blend_map
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

        # Step 5: Compute blend map
        blend_map_arr = compute_blend_map(
            collision_result.safe_map,
            resolution=settings["heightmap_resolution"],
            blend_distance=settings["blend_distance_mm"],
        )

        # Step 6: Compute deformation field (always — needed for engine plugin)
        deformation_field = None
        try:
            from .analysis.deformation_field import compute_deformation_field

            # Get layer height from print profile (fall back to 0.2mm)
            lh = 0.2
            try:
                from cura.CuraApplication import CuraApplication
                stack = CuraApplication.getInstance().getGlobalContainerStack()
                if stack is not None:
                    profile_lh = stack.getProperty("layer_height", "value")
                    if profile_lh is not None and float(profile_lh) > 0:
                        lh = float(profile_lh)
            except Exception:
                pass  # Use default if Cura is not available (e.g. in tests)
            z_vals = height_map.z_values
            finite_z = z_vals[numpy.isfinite(z_vals)]
            if finite_z.size > 0:
                z_max = float(numpy.max(finite_z))
                total_layers_est = max(1, int(z_max / lh) + 1)
            else:
                total_layers_est = 1

            t0 = time.time()
            deformation_field = compute_deformation_field(
                height_map,
                collision_result.safe_map,
                layer_height=lh,
                total_layers=total_layers_est,
                first_layer_z=lh,
                decay_distance=settings.get("field_decay_mm", 5.0),
                min_thickness_ratio=settings.get("min_thickness_ratio", 0.5),
                max_thickness_ratio=settings.get("max_thickness_ratio", 2.0),
                max_angle_deg=settings["max_angle_deg"],
                optimization_resolution=settings.get("optimization_resolution", 2.0),
            )
            Logger.log("d", "  Deformation field: %d layers, grid %dx%d in %.2fs",
                       deformation_field.num_layers,
                       deformation_field.grid_shape[0],
                       deformation_field.grid_shape[1],
                       time.time() - t0)
        except Exception:
            Logger.logException("e", "Failed to compute deformation field")

        return _AnalysisResult(
            analysis=analysis,
            candidate_regions=candidates,
            height_map=height_map,
            collision_result=collision_result,
            safe_map=collision_result.safe_map,
            blend_map=blend_map_arr,
            deformation_field=deformation_field,
        )
