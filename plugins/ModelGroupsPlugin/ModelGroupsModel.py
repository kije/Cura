# Copyright (c) 2024 Community
# Released under the terms of the LGPLv3 or higher.

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt

from UM.Qt.ListModel import ListModel

if TYPE_CHECKING:
    from .ModelGroupsManager import ModelGroupsManager


class ModelGroupsModel(ListModel):
    """QML ListModel exposing the list of model groups."""

    GroupIdRole = Qt.ItemDataRole.UserRole + 1
    GroupNameRole = Qt.ItemDataRole.UserRole + 2
    GroupEnabledRole = Qt.ItemDataRole.UserRole + 3
    NodeCountRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self, manager: "ModelGroupsManager", parent=None) -> None:
        super().__init__(parent)
        self._manager = manager

        self.addRoleName(self.GroupIdRole, "group_id")
        self.addRoleName(self.GroupNameRole, "group_name")
        self.addRoleName(self.GroupEnabledRole, "group_enabled")
        self.addRoleName(self.NodeCountRole, "node_count")

        self._manager.groupsChanged.connect(self._update)

    def _update(self, *args) -> None:
        items = []
        for group in self._manager.getGroups().values():
            nodes = self._manager.getNodesInGroup(group.id)
            items.append({
                "group_id": group.id,
                "group_name": group.name,
                "group_enabled": group.enabled,
                "node_count": len(nodes),
            })
        self.setItems(items)
