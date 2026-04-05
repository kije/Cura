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

        # Settings
        self._material_name = "PLA"
        self._min_density = 10.0
        self._max_density = 80.0
        self._num_zones = 6
        self._infill_pattern = "gyroid"
        self._max_iterations = 5
        self._mesh_resolution = "medium"
        self._safety_factor = 2.0
        self._bonding_coeff = 0.5  # 50 % default; overridden by UI or material

        # Deferred initialization
        CuraApplication.getInstance().engineCreatedSignal.connect(self._onEngineCreated)

    def _onEngineCreated(self) -> None:
        plugin_path = PluginRegistry.getInstance().getPluginPath("FEAInfillOptimizer")
        if plugin_path:
            self._dep_manager = DependencyManager(plugin_path)
            self._deps_available = self._dep_manager.all_available()
            self.depsAvailableChanged.emit()

        # Connect to signals for BC persistence in 3MF project files.
        app = CuraApplication.getInstance()

        # Restore BCs when files are loaded.  We connect to multiple signals
        # because the timing differs between STL imports and workspace loads.
        app.fileLoaded.connect(self._restoreBCsFromScene)
        app.fileCompleted.connect(self._restoreBCsFromScene)
        if hasattr(app, "workspaceLoaded"):
            app.workspaceLoaded.connect(self._restoreBCsFromScene)

        # Also listen for scene changes — nodes may get metadata restored
        # asynchronously during workspace loading.
        app.getController().getScene().sceneChanged.connect(self._onSceneNodeMayHaveBCMetadata)

        # Sync BC data to node.metadata before save.  writeStarted fires
        # right before the 3MF writer serialises nodes, so our metadata
        # will be picked up by savitar_node.setSetting().
        app.getOutputDeviceManager().writeStarted.connect(self._syncAllBCsToMetadata)

    _BC_METADATA_KEY = "fea_infill_boundary_conditions"

    def _syncAllBCsToMetadata(self, *args) -> None:
        """Persist BC data from decorators → node.metadata for all scene nodes.

        Called right before writing a 3MF file.  The 3MF writer reads
        node.metadata and saves each entry via savitar_node.setSetting().
        """
        import json
        scene = CuraApplication.getInstance().getController().getScene()
        for node in DepthFirstIterator(scene.getRoot()):
            if not isinstance(node, CuraSceneNode):
                continue
            try:
                bc = node.callDecoration("getBoundaryConditions")
                if bc is not None and bc.hasAnyBC():
                    node.metadata[self._BC_METADATA_KEY] = json.dumps(bc.toDict())
                    Logger.log("d", "FEA Infill: Saved BCs for node '%s' (%d fixed, %d forces)",
                               node.getName(), bc.getFixedFaceCount(), bc.getForceGroupCount())
                elif self._BC_METADATA_KEY in node.metadata:
                    del node.metadata[self._BC_METADATA_KEY]
            except Exception:
                Logger.logException("w", "FEA Infill: Failed to save BCs for node '%s'",
                                    node.getName())

    def _onSceneNodeMayHaveBCMetadata(self, node) -> None:
        """Check a single node for BC metadata and restore if found.

        Connected to sceneChanged — fires for each node as it's added/modified.
        This catches nodes that get their metadata restored asynchronously
        during workspace loading (after workspaceLoaded has already fired).
        """
        if node is None or not isinstance(node, CuraSceneNode):
            return
        try:
            if not hasattr(node, "metadata"):
                return
            if self._BC_METADATA_KEY not in node.metadata:
                return
            # Already has BC decorator — skip
            if node.callDecoration("getBoundaryConditions") is not None:
                return
            import json
            from .FEABoundaryConditionDecorator import FEABoundaryConditionDecorator
            raw = node.metadata[self._BC_METADATA_KEY]
            json_str = raw.value if hasattr(raw, "value") else str(raw)
            bc_data = json.loads(json_str)
            decorator = FEABoundaryConditionDecorator()
            decorator.fromDict(bc_data)
            node.addDecorator(decorator)
            Logger.log("d", "FEA Infill: Restored BCs for node '%s' via sceneChanged "
                       "(%d fixed, %d forces, %d torques)",
                       node.getName(), decorator.getFixedFaceCount(),
                       decorator.getForceGroupCount(), decorator.getTorqueGroupCount())
        except Exception:
            pass  # Silently ignore — this fires very frequently

    def _restoreBCsFromScene(self, *args) -> None:
        """Restore BC decorators from node.metadata after loading.

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
            # Already has a BC decorator — don't overwrite
            if node.callDecoration("getBoundaryConditions") is not None:
                continue
            try:
                raw = node.metadata[self._BC_METADATA_KEY]
                # Handle both plain string and Savitar setting object
                json_str = raw.value if hasattr(raw, "value") else str(raw)
                bc_data = json.loads(json_str)
                decorator = FEABoundaryConditionDecorator()
                decorator.fromDict(bc_data)
                node.addDecorator(decorator)
                Logger.log("d", "FEA Infill: Restored BCs for node '%s' (%d fixed, %d forces)",
                           node.getName(), decorator.getFixedFaceCount(),
                           decorator.getForceGroupCount())
            except Exception:
                Logger.logException("w", "FEA Infill: Failed to restore BCs for node '%s'",
                                    node.getName())

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
    def cancelAnalysis(self) -> None:
        """Cancel any running analysis and return to OPTIMIZE phase."""
        self._cancel_requested = True
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
            Message(
                i18n_catalog.i18nc("@info:status",
                                   "Required Python libraries (trimesh, gmsh, scipy) are not installed.\n\n"
                                   "To install: scroll up in the Analysis Setup panel and click "
                                   "'Install Dependencies', then restart Cura.\n\n"
                                   "Alternatively, install manually:\n"
                                   "pip install trimesh gmsh scipy"),
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

        self._cancel_requested = False
        self._phase = "running"
        self._analysis_status = "running"
        self._progress = 0.0
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
        }

        job = FEASolveJob(node, bc_decorator, material, config)
        job.finished.connect(self._onFEAFinished)
        job.progress.connect(self._onFEAProgress)
        JobQueue.getInstance().add(job)

    def _onFEAProgress(self, job, progress: float) -> None:
        # Marshal to the main thread; this slot may be called from the
        # background Job thread via UM.Signal (C15: thread safety).
        def _update() -> None:
            self._progress = progress
            if progress <= 10:
                self._analysis_stage = "Extracting mesh..."
            elif progress <= 30:
                self._analysis_stage = "Building volume mesh..."
            elif progress <= 90:
                iteration = max(1, int((progress - 30) / 12) + 1)
                self._analysis_stage = "Solving FEA (iteration %d)..." % iteration
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
                Logger.log("e", "FEA Infill: Analysis failed: %s", str(result))
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
                    except Exception:
                        Logger.logException("w", "FEA Infill: Auto stress overlay failed")
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
        self.phaseChanged.emit()
        self.analysisStatusChanged.emit()
        self.progressChanged.emit()
        self.resultsChanged.emit()

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
