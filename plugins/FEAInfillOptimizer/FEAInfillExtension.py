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

        # Analysis state
        self._analysis_status = "idle"  # idle, running, complete, error
        self._progress = 0.0
        self._results: Optional[Dict[str, Any]] = None

        # Settings
        self._material_name = "PLA"
        self._min_density = 10.0
        self._max_density = 80.0
        self._num_zones = 6
        self._infill_pattern = "gyroid"
        self._max_iterations = 5
        self._mesh_resolution = "medium"

        # Deferred initialization
        CuraApplication.getInstance().engineCreatedSignal.connect(self._onEngineCreated)

    def _onEngineCreated(self) -> None:
        plugin_path = PluginRegistry.getInstance().getPluginPath("FEAInfillOptimizer")
        if plugin_path:
            self._dep_manager = DependencyManager(plugin_path)
            self._deps_available = self._dep_manager.all_available()
            self.depsAvailableChanged.emit()

    # -- Dialog --

    def showDialog(self) -> None:
        if self._dialog is None:
            plugin_path = PluginRegistry.getInstance().getPluginPath("FEAInfillOptimizer")
            if not plugin_path:
                Logger.log("e", "FEA Infill: Could not find plugin path")
                return
            qml_path = os.path.join(plugin_path, "resources", "qml", "FEAInfillDialog.qml")
            self._dialog = CuraApplication.getInstance().createQmlComponent(
                qml_path, {"manager": self}
            )
        if self._dialog:
            self._dialog.show()

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
        if self._dep_manager:
            self._deps_available = self._dep_manager.all_available()
            self.depsAvailableChanged.emit()

    # -- Scene node listing --

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

        Falls back to a full scene walk if the cache entry is missing (e.g.
        the cache was not yet populated for this call site).
        """
        node_key = str(node_id)
        cached = self._node_cache.get(node_key)
        if cached is not None:
            return cached
        # Fallback: walk the scene comparing by identity via str(id)
        scene = CuraApplication.getInstance().getController().getScene()
        for node in DepthFirstIterator(scene.getRoot()):
            if str(id(node)) == node_key:
                return node
        return None

    # -- Analysis status --

    @pyqtProperty(str, notify=analysisStatusChanged)
    def analysisStatus(self) -> str:
        return self._analysis_status

    @pyqtProperty(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

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

    @pyqtProperty(float, notify=resultsChanged)
    def safetyFactor(self) -> float:
        if self._results:
            return self._results.get("safety_factor", 0.0)
        return 0.0

    @pyqtProperty(int, notify=resultsChanged)
    def convergenceIterations(self) -> int:
        if self._results:
            return self._results.get("iterations", 0)
        return 0

    @pyqtProperty(bool, notify=resultsChanged)
    def hasResults(self) -> bool:
        return self._results is not None

    # -- Boundary condition summary --

    @pyqtSlot(int, result=str)
    def getBCSummary(self, node_id: int) -> str:
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

    # -- FEA Actions --

    @pyqtSlot(int)
    def runAnalysis(self, node_id: int) -> None:
        """Triggered from QML 'Run FEA Analysis' button."""
        if not self._deps_available:
            Message(
                i18n_catalog.i18nc("@info:status",
                                   "Please install dependencies first."),
                title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                message_type=Message.MessageType.WARNING
            ).show()
            return

        node = self._getNodeById(node_id)
        if node is None:
            Logger.log("e", "FEA Infill: Target node not found")
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

        self._analysis_status = "running"
        self._progress = 0.0
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
            self.progressChanged.emit()
        CuraApplication.getInstance().callLater(_update)

    def _onFEAFinished(self, job) -> None:
        result = job.getResult()
        if result is None or isinstance(result, Exception):
            self._analysis_status = "error"
            self._results = None
            Logger.log("e", "FEA Infill: Analysis failed: %s", str(result))
        else:
            self._analysis_status = "complete"
            self._results = result
        self.analysisStatusChanged.emit()
        self.resultsChanged.emit()

    @pyqtSlot(int)
    def applyModifierMeshes(self, node_id: int) -> None:
        """Create infill modifier meshes from the FEA results."""
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

    @pyqtSlot(int)
    def showStressOverlay(self, node_id: int) -> None:
        """Toggle stress visualization overlay on the model."""
        if self._results is None:
            return
        node = self._getNodeById(node_id)
        if node is None:
            return

        from .visualization.stress_overlay import StressOverlayManager
        StressOverlayManager.toggle_overlay(node, self._results)

    @pyqtSlot(int)
    def clearResults(self, node_id: int) -> None:
        """Remove FEA results and any modifier meshes/overlays."""
        self._results = None
        self._analysis_status = "idle"
        self._progress = 0.0
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
