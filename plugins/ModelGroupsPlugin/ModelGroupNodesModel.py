# Copyright (c) 2024 Community
# Released under the terms of the LGPLv3 or higher.

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt

from UM.Qt.ListModel import ListModel

from .ModelGroupDecorator import ModelGroupDecorator

if TYPE_CHECKING:
    from .ModelGroupsManager import ModelGroupsManager


class ModelGroupNodesModel(ListModel):
    """QML ListModel exposing the nodes within a selected model group."""

    NodeNameRole = Qt.ItemDataRole.UserRole + 1
    NodeEnabledRole = Qt.ItemDataRole.UserRole + 2
    NodeIndexRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self, manager: "ModelGroupsManager", parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._group_id: str = ""

        self.addRoleName(self.NodeNameRole, "node_name")
        self.addRoleName(self.NodeEnabledRole, "node_enabled")
        self.addRoleName(self.NodeIndexRole, "node_index")

        self._manager.groupsChanged.connect(self._update)

    def setGroupId(self, group_id: str) -> None:
        self._group_id = group_id
        self._update()

    def getGroupId(self) -> str:
        return self._group_id

    def _update(self, *args) -> None:
        if not self._group_id:
            self.setItems([])
            return

        nodes = self._manager.getNodesInGroup(self._group_id)
        items = []
        for i, node in enumerate(nodes):
            decorator = node.getDecorator(ModelGroupDecorator)
            node_enabled = decorator.isModelGroupNodeEnabled() if decorator else True
            items.append({
                "node_name": node.getName(),
                "node_enabled": node_enabled,
                "node_index": i,
            })
        self.setItems(items)

    def getNodeByIndex(self, index: int):
        """Get the actual SceneNode by its index in the current group's node list."""
        nodes = self._manager.getNodesInGroup(self._group_id)
        if 0 <= index < len(nodes):
            return nodes[index]
        return None
