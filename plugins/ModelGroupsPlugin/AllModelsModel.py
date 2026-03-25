# Copyright (c) 2024 Community
# Released under the terms of the LGPLv3 or higher.

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt

from UM.Application import Application
from UM.Qt.ListModel import ListModel
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator

from cura.Scene.CuraSceneNode import CuraSceneNode

from .ModelGroupDecorator import ModelGroupDecorator

if TYPE_CHECKING:
    from .ModelGroupsManager import ModelGroupsManager


class AllModelsModel(ListModel):
    """QML ListModel listing ALL scene models with their visibility/group state.

    This serves as the plugin's own object list, showing both visible and hidden models.
    """

    NodeNameRole = Qt.ItemDataRole.UserRole + 1
    NodeVisibleRole = Qt.ItemDataRole.UserRole + 2
    NodeGroupNameRole = Qt.ItemDataRole.UserRole + 3
    NodeIndexRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self, manager: "ModelGroupsManager", parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._cached_nodes = []

        self.addRoleName(self.NodeNameRole, "node_name")
        self.addRoleName(self.NodeVisibleRole, "node_visible")
        self.addRoleName(self.NodeGroupNameRole, "node_group_name")
        self.addRoleName(self.NodeIndexRole, "node_index")

        self._manager.groupsChanged.connect(self._update)
        Application.getInstance().getController().getScene().sceneChanged.connect(self._onSceneChanged)

    def _onSceneChanged(self, node) -> None:
        from UM.Scene.Camera import Camera
        if not isinstance(node, Camera) and not self._manager.is_updating:
            self._update()

    def _update(self, *args) -> None:
        scene = Application.getInstance().getController().getScene()
        nodes = []
        items = []

        for node in DepthFirstIterator(scene.getRoot()):
            if not isinstance(node, CuraSceneNode):
                continue
            if not node.getMeshData():
                continue
            # Skip nodes that are children of groups (show only top-level)
            parent = node.getParent()
            if parent and parent.callDecoration("isGroup"):
                continue

            is_sliceable = bool(node.callDecoration("isSliceable"))
            decorator = node.getDecorator(ModelGroupDecorator)

            # Include node if it's sliceable (visible) OR if it has our decorator (hidden by us)
            if not is_sliceable and decorator is None:
                continue

            node_visible = is_sliceable
            group_name = ""
            if decorator is not None:
                group_id = decorator.getModelGroupId()
                group = self._manager.getGroup(group_id)
                if group is not None:
                    group_name = group.name
                if not decorator.isModelGroupNodeEnabled():
                    node_visible = False
                elif group is not None and not group.enabled:
                    node_visible = False

            nodes.append(node)
            items.append({
                "node_name": node.getName(),
                "node_visible": node_visible,
                "node_group_name": group_name,
                "node_index": len(items),
            })

        self._cached_nodes = nodes
        self.setItems(items)

    def getNodeByIndex(self, index: int):
        if 0 <= index < len(self._cached_nodes):
            return self._cached_nodes[index]
        return None
