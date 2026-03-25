# Copyright (c) 2024 Community
# Released under the terms of the LGPLv3 or higher.

import uuid
from typing import Dict, List, Optional

from UM.Application import Application
from UM.Logger import Logger
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.SceneNode import SceneNode
from UM.Signal import Signal

from cura.Scene.ConvexHullDecorator import ConvexHullDecorator
from cura.Scene.SliceableObjectDecorator import SliceableObjectDecorator

from .ModelGroupDecorator import ModelGroupDecorator


class ModelGroup:
    """Simple data holder for a model group."""

    def __init__(self, group_id: str, name: str, enabled: bool = True) -> None:
        self.id = group_id
        self.name = name
        self.enabled = enabled


class ModelGroupsManager:
    """Manages model groups: creation, deletion, assignment, and enable/disable toggling."""

    def __init__(self) -> None:
        self._groups: Dict[str, ModelGroup] = {}
        self._stored_sliceable_decorators: Dict[int, SliceableObjectDecorator] = {}
        self._stored_convexhull_decorators: Dict[int, ConvexHullDecorator] = {}
        self._updating: bool = False  # Guard against re-entrant scene change handling

        self.groupsChanged = Signal()

    def getGroups(self) -> Dict[str, ModelGroup]:
        return self._groups

    def getGroup(self, group_id: str) -> Optional[ModelGroup]:
        return self._groups.get(group_id)

    def createGroup(self, name: str) -> str:
        group_id = str(uuid.uuid4())
        self._groups[group_id] = ModelGroup(group_id, name)
        self.groupsChanged.emit()
        return group_id

    def deleteGroup(self, group_id: str) -> None:
        if group_id not in self._groups:
            return

        self._updating = True
        try:
            for node in self.getNodesInGroup(group_id):
                self._enableNode(node)
                node.removeDecorator(ModelGroupDecorator)
                self._clearNodeMetadata(node)
            del self._groups[group_id]
        finally:
            self._updating = False
        self.groupsChanged.emit()

    def renameGroup(self, group_id: str, name: str) -> None:
        group = self._groups.get(group_id)
        if group is None:
            return
        group.name = name
        self._updateMetadataForGroup(group_id)
        self.groupsChanged.emit()

    def addNodeToGroup(self, node: SceneNode, group_id: str) -> Optional[str]:
        """Add a node to a group. Returns previous group_id if the node was already in one, else None."""
        group = self._groups.get(group_id)
        if group is None:
            return None

        self._updating = True
        try:
            previous_group_id = None
            decorator = node.getDecorator(ModelGroupDecorator)
            if decorator is not None:
                previous_group_id = decorator.getModelGroupId()
                if previous_group_id == group_id:
                    return previous_group_id
                self._enableNode(node)
            else:
                decorator = ModelGroupDecorator()
                node.addDecorator(decorator)

            decorator.setModelGroupId(group_id)
            decorator.setModelGroupNodeEnabled(True)

            if not group.enabled:
                self._disableNode(node)

            self._updateNodeMetadata(node)
        finally:
            self._updating = False
        self.groupsChanged.emit()
        return previous_group_id

    def removeNodeFromGroup(self, node: SceneNode) -> Optional[str]:
        """Remove a node from its group. Returns the old group_id, or None."""
        decorator = node.getDecorator(ModelGroupDecorator)
        if decorator is None:
            return None

        self._updating = True
        try:
            old_group_id = decorator.getModelGroupId()
            self._enableNode(node)
            node.removeDecorator(ModelGroupDecorator)
            self._clearNodeMetadata(node)
        finally:
            self._updating = False
        self.groupsChanged.emit()
        return old_group_id

    def setGroupEnabled(self, group_id: str, enabled: bool) -> List[SceneNode]:
        """Toggle a group on/off. Returns list of affected nodes."""
        group = self._groups.get(group_id)
        if group is None or group.enabled == enabled:
            return []

        self._updating = True
        try:
            group.enabled = enabled
            affected_nodes = []

            for node in self.getNodesInGroup(group_id):
                decorator = node.getDecorator(ModelGroupDecorator)
                if decorator is None:
                    continue

                if enabled:
                    if decorator.isModelGroupNodeEnabled():
                        self._enableNode(node)
                else:
                    self._disableNode(node)

                self._updateNodeMetadata(node)
                affected_nodes.append(node)
        finally:
            self._updating = False
        self.groupsChanged.emit()
        return affected_nodes

    def setNodeEnabled(self, node: SceneNode, enabled: bool) -> None:
        """Toggle an individual node within its group."""
        decorator = node.getDecorator(ModelGroupDecorator)
        if decorator is None:
            return

        self._updating = True
        try:
            decorator.setModelGroupNodeEnabled(enabled)
            group = self._groups.get(decorator.getModelGroupId())

            if enabled and (group is None or group.enabled):
                self._enableNode(node)
            elif not enabled:
                self._disableNode(node)

            self._updateNodeMetadata(node)
        finally:
            self._updating = False
        self.groupsChanged.emit()

    def isNodeEffectivelyEnabled(self, node: SceneNode) -> bool:
        decorator = node.getDecorator(ModelGroupDecorator)
        if decorator is None:
            return True

        group = self._groups.get(decorator.getModelGroupId())
        if group is None:
            return True

        return group.enabled and decorator.isModelGroupNodeEnabled()

    def getNodesInGroup(self, group_id: str) -> List[SceneNode]:
        scene = Application.getInstance().getController().getScene()
        nodes = []
        for node in DepthFirstIterator(scene.getRoot()):
            decorator = node.getDecorator(ModelGroupDecorator)
            if decorator is not None and decorator.getModelGroupId() == group_id:
                nodes.append(node)
        return nodes

    def getAllGroupedNodes(self) -> List[SceneNode]:
        scene = Application.getInstance().getController().getScene()
        nodes = []
        for node in DepthFirstIterator(scene.getRoot()):
            if node.getDecorator(ModelGroupDecorator) is not None:
                nodes.append(node)
        return nodes

    @property
    def is_updating(self) -> bool:
        return self._updating

    def _disableNode(self, node: SceneNode) -> None:
        node_id = id(node)

        # Store and remove SliceableObjectDecorator
        sliceable = node.getDecorator(SliceableObjectDecorator)
        if sliceable is not None:
            self._stored_sliceable_decorators[node_id] = sliceable
            node.removeDecorator(SliceableObjectDecorator)

        # Store and remove ConvexHullDecorator to prevent shadow rendering
        convex_hull = node.getDecorator(ConvexHullDecorator)
        if convex_hull is not None:
            self._stored_convexhull_decorators[node_id] = convex_hull
            node.removeDecorator(ConvexHullDecorator)

        node.setVisible(False)
        node.setSelectable(False)

    def _enableNode(self, node: SceneNode) -> None:
        node_id = id(node)

        # Restore SliceableObjectDecorator
        stored_sliceable = self._stored_sliceable_decorators.pop(node_id, None)
        if stored_sliceable is not None:
            node.addDecorator(stored_sliceable)
        elif node.getDecorator(SliceableObjectDecorator) is None:
            node.addDecorator(SliceableObjectDecorator())

        # Restore ConvexHullDecorator
        stored_convex = self._stored_convexhull_decorators.pop(node_id, None)
        if stored_convex is not None:
            node.addDecorator(stored_convex)
        elif node.getDecorator(ConvexHullDecorator) is None:
            node.addDecorator(ConvexHullDecorator())

        node.setVisible(True)
        node.setSelectable(True)

    def _updateNodeMetadata(self, node: SceneNode) -> None:
        decorator = node.getDecorator(ModelGroupDecorator)
        if decorator is None:
            return
        group = self._groups.get(decorator.getModelGroupId())
        if group is None:
            return

        node.metadata["model_groups_plugin:group_id"] = group.id
        node.metadata["model_groups_plugin:group_name"] = group.name
        node.metadata["model_groups_plugin:group_enabled"] = str(group.enabled)
        node.metadata["model_groups_plugin:node_enabled"] = str(decorator.isModelGroupNodeEnabled())

    def _updateMetadataForGroup(self, group_id: str) -> None:
        for node in self.getNodesInGroup(group_id):
            self._updateNodeMetadata(node)

    def _clearNodeMetadata(self, node: SceneNode) -> None:
        for key in list(node.metadata.keys()):
            if key.startswith("model_groups_plugin:"):
                del node.metadata[key]

    def restoreFromMetadata(self) -> None:
        """After loading a 3MF, reconstruct groups from node metadata."""
        scene = Application.getInstance().getController().getScene()
        discovered_groups: Dict[str, ModelGroup] = {}

        self._updating = True
        try:
            for node in DepthFirstIterator(scene.getRoot()):
                group_id = node.metadata.get("model_groups_plugin:group_id")
                if group_id is None:
                    continue

                group_name = node.metadata.get("model_groups_plugin:group_name", "Unnamed")
                group_enabled_str = node.metadata.get("model_groups_plugin:group_enabled", "True")
                node_enabled_str = node.metadata.get("model_groups_plugin:node_enabled", "True")

                group_enabled = group_enabled_str == "True"
                node_enabled = node_enabled_str == "True"

                if group_id not in discovered_groups:
                    discovered_groups[group_id] = ModelGroup(group_id, group_name, group_enabled)

                decorator = ModelGroupDecorator()
                decorator.setModelGroupId(group_id)
                decorator.setModelGroupNodeEnabled(node_enabled)
                node.addDecorator(decorator)

                if not group_enabled or not node_enabled:
                    self._disableNode(node)

            self._groups.update(discovered_groups)
        finally:
            self._updating = False
        if discovered_groups:
            self.groupsChanged.emit()

    def clear(self) -> None:
        """Clear all groups and re-enable all nodes."""
        self._updating = True
        try:
            for group_id in list(self._groups.keys()):
                for node in self.getNodesInGroup(group_id):
                    self._enableNode(node)
                    node.removeDecorator(ModelGroupDecorator)
                    self._clearNodeMetadata(node)

            self._groups.clear()
            self._stored_sliceable_decorators.clear()
            self._stored_convexhull_decorators.clear()
        finally:
            self._updating = False
        self.groupsChanged.emit()

    def handleNodeRemoved(self, node: SceneNode) -> None:
        """Clean up when a node is removed from the scene."""
        node_id = id(node)
        self._stored_sliceable_decorators.pop(node_id, None)
        self._stored_convexhull_decorators.pop(node_id, None)
