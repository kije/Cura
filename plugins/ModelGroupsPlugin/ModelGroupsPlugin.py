# Copyright (c) 2024 Community
# Released under the terms of the LGPLv3 or higher.

import os
from typing import Optional, cast

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Application import Application
from UM.Extension import Extension
from UM.Logger import Logger
from UM.Operations.GroupedOperation import GroupedOperation
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.Selection import Selection
from UM.i18n import i18nCatalog

from cura.CuraApplication import CuraApplication
from cura.Scene.CuraSceneNode import CuraSceneNode

from .ModelGroupDecorator import ModelGroupDecorator
from .ModelGroupNodesModel import ModelGroupNodesModel
from .ModelGroupsManager import ModelGroupsManager
from .ModelGroupsModel import ModelGroupsModel
from .Operations import (
    AssignToGroupOperation,
    RemoveFromGroupOperation,
    ToggleGroupOperation,
    ToggleNodeOperation,
)

catalog = i18nCatalog("cura")


class ModelGroupsPlugin(QObject, Extension):
    """Extension plugin that lets users organize models into named groups for batch printing."""

    def __init__(self, parent=None) -> None:
        QObject.__init__(self, parent)
        Extension.__init__(self)

        self.setMenuName(catalog.i18nc("@item:inmenu", "Model Groups"))
        self.addMenuItem(catalog.i18nc("@item:inmenu", "Manage Groups"), self.showPopup)

        self._view = None
        self._manager = ModelGroupsManager()
        self._groups_model = ModelGroupsModel(self._manager)
        self._nodes_model = ModelGroupNodesModel(self._manager)
        self._selected_group_id: str = ""

        CuraApplication.getInstance().fileCompleted.connect(self._onFileLoaded)
        CuraApplication.getInstance().getController().getScene().sceneChanged.connect(self._onSceneChanged)

        # Register this plugin instance on CuraApplication so QML can access it
        CuraApplication.getInstance()._model_groups_plugin = self

    selectedGroupChanged = pyqtSignal()
    groupsChanged = pyqtSignal()

    def _connectSignals(self) -> None:
        self._manager.groupsChanged.connect(self._onGroupsChanged)

    def _onGroupsChanged(self) -> None:
        self.groupsChanged.emit()

    def _onFileLoaded(self, filename: str) -> None:
        self._manager.restoreFromMetadata()
        self._groups_model._update()

    def _onSceneChanged(self, node) -> None:
        """Handle node removal from scene - clean up group membership."""
        if node is None:
            return
        scene = Application.getInstance().getController().getScene()
        root = scene.getRoot()

        # Check if any tracked nodes have been removed from the scene
        for grouped_node in self._manager.getAllGroupedNodes():
            if grouped_node.getParent() is None and grouped_node != root:
                self._manager.handleNodeRemoved(grouped_node)

    @pyqtProperty(QObject, constant=True)
    def groupsModel(self):
        return self._groups_model

    @pyqtProperty(QObject, constant=True)
    def nodesModel(self):
        return self._nodes_model

    @pyqtProperty(str, notify=selectedGroupChanged)
    def selectedGroupId(self) -> str:
        return self._selected_group_id

    @selectedGroupId.setter
    def selectedGroupId(self, group_id: str) -> None:
        if self._selected_group_id != group_id:
            self._selected_group_id = group_id
            self._nodes_model.setGroupId(group_id)
            self.selectedGroupChanged.emit()

    @pyqtSlot(str)
    def createGroup(self, name: str) -> None:
        group_id = self._manager.createGroup(name)
        self.selectedGroupId = group_id

    @pyqtSlot(str)
    def deleteGroup(self, group_id: str) -> None:
        self._manager.deleteGroup(group_id)
        if self._selected_group_id == group_id:
            self.selectedGroupId = ""

    @pyqtSlot(str, str)
    def renameGroup(self, group_id: str, new_name: str) -> None:
        self._manager.renameGroup(group_id, new_name)

    @pyqtSlot(str)
    def toggleGroup(self, group_id: str) -> None:
        group = self._manager.getGroup(group_id)
        if group is None:
            return

        new_enabled = not group.enabled
        operation = ToggleGroupOperation(self._manager, group_id, new_enabled)
        operation.push()

    @pyqtSlot(int)
    def toggleNodeInGroup(self, node_index: int) -> None:
        node = self._nodes_model.getNodeByIndex(node_index)
        if node is None:
            return

        decorator = node.getDecorator(ModelGroupDecorator)
        if decorator is None:
            return

        new_enabled = not decorator.isModelGroupNodeEnabled()
        operation = ToggleNodeOperation(self._manager, node, new_enabled)
        operation.push()

    @pyqtSlot(str)
    def assignSelectedToGroup(self, group_id: str) -> None:
        selected_nodes = Selection.getAllSelectedObjects()
        if not selected_nodes:
            return

        operation = GroupedOperation()
        for node in selected_nodes:
            if isinstance(node, CuraSceneNode) and node.getMeshData():
                operation.addOperation(AssignToGroupOperation(self._manager, node, group_id))

        if operation.getNumChildrenOperations() > 0:
            operation.push()

    @pyqtSlot(int)
    def removeNodeFromGroup(self, node_index: int) -> None:
        node = self._nodes_model.getNodeByIndex(node_index)
        if node is None:
            return

        operation = RemoveFromGroupOperation(self._manager, node)
        operation.push()

    @pyqtSlot()
    def assignSelectedToCurrentGroup(self) -> None:
        if self._selected_group_id:
            self.assignSelectedToGroup(self._selected_group_id)

    @pyqtSlot()
    def hideSelectedModels(self) -> None:
        """Hide all currently selected models (adds to an auto-created hidden group)."""
        selected_nodes = Selection.getAllSelectedObjects()
        if not selected_nodes:
            return

        # Create or get the default hidden group
        hidden_group_id = self._getOrCreateHiddenGroup()

        operation = GroupedOperation()
        for node in selected_nodes:
            if isinstance(node, CuraSceneNode) and node.getMeshData():
                decorator = node.getDecorator(ModelGroupDecorator)
                if decorator is None:
                    # Assign to hidden group and disable
                    operation.addOperation(AssignToGroupOperation(self._manager, node, hidden_group_id))
                # Disable the individual node
                operation.addOperation(ToggleNodeOperation(self._manager, node, False))

        if operation.getNumChildrenOperations() > 0:
            operation.push()

    @pyqtSlot()
    def showSelectedModels(self) -> None:
        """Show all currently selected models."""
        selected_nodes = Selection.getAllSelectedObjects()
        if not selected_nodes:
            return

        operation = GroupedOperation()
        for node in selected_nodes:
            if isinstance(node, CuraSceneNode):
                decorator = node.getDecorator(ModelGroupDecorator)
                if decorator is not None and not decorator.isModelGroupNodeEnabled():
                    operation.addOperation(ToggleNodeOperation(self._manager, node, True))

        if operation.getNumChildrenOperations() > 0:
            operation.push()

    @pyqtSlot(int)
    def toggleNodeVisibilityByIndex(self, index: int) -> None:
        """Toggle visibility of a node by its index in the ObjectsModel."""
        from cura.UI.ObjectsModel import ObjectsModel
        app = CuraApplication.getInstance()
        objects_model = None
        # Find the ObjectsModel instance
        for child in app.findChildren(ObjectsModel):
            objects_model = child
            break

        if objects_model is None:
            return

        items = objects_model.items
        if 0 <= index < len(items):
            node = items[index]["node"]
            decorator = node.getDecorator(ModelGroupDecorator)

            if decorator is not None and not decorator.isModelGroupNodeEnabled():
                # Currently hidden -> show it
                operation = ToggleNodeOperation(self._manager, node, True)
                operation.push()
            else:
                # Currently visible -> hide it
                hidden_group_id = self._getOrCreateHiddenGroup()
                op = GroupedOperation()
                if decorator is None:
                    op.addOperation(AssignToGroupOperation(self._manager, node, hidden_group_id))
                op.addOperation(ToggleNodeOperation(self._manager, node, False))
                op.push()

    def _getOrCreateHiddenGroup(self) -> str:
        """Get or create the default 'Hidden' group for quick hide/show."""
        for group_id, group in self._manager.getGroups().items():
            if group.name == "Hidden":
                return group_id
        return self._manager.createGroup("Hidden")

    @pyqtSlot(result=bool)
    def hasHiddenSelection(self) -> bool:
        """Check if any selected node is hidden."""
        for node in Selection.getAllSelectedObjects():
            decorator = node.getDecorator(ModelGroupDecorator)
            if decorator is not None and not decorator.isModelGroupNodeEnabled():
                return True
        return False

    def showPopup(self) -> None:
        if self._view is None:
            self._createView()
            if self._view is None:
                Logger.log("e", "Failed to create Model Groups dialog.")
                return
        # Refresh data before showing
        self._groups_model._update()
        self._view.show()

    def _createView(self) -> None:
        Logger.log("d", "Creating Model Groups plugin view.")
        plugin_path = cast(str, PluginRegistry.getInstance().getPluginPath("ModelGroupsPlugin"))
        path = os.path.join(plugin_path, "ModelGroupsPanel.qml")
        self._view = CuraApplication.getInstance().createQmlComponent(path, {"manager": self})
        if self._view is None:
            Logger.log("e", "Failed to create ModelGroupsPanel QML component.")
            return
        self._connectSignals()
        Logger.log("d", "Model Groups view created.")
