# Copyright (c) 2024 Community
# Released under the terms of the LGPLv3 or higher.

from typing import TYPE_CHECKING, List, Optional

from UM.Operations.Operation import Operation
from UM.Scene.SceneNode import SceneNode

if TYPE_CHECKING:
    from .ModelGroupsManager import ModelGroupsManager


class ToggleGroupOperation(Operation):
    """Operation to toggle a model group on/off, supporting undo/redo."""

    def __init__(self, manager: "ModelGroupsManager", group_id: str, new_enabled: bool) -> None:
        super().__init__()
        self._manager = manager
        self._group_id = group_id
        self._new_enabled = new_enabled
        self._old_enabled = not new_enabled

    def undo(self) -> None:
        self._manager.setGroupEnabled(self._group_id, self._old_enabled)

    def redo(self) -> None:
        group = self._manager.getGroup(self._group_id)
        if group is not None:
            self._old_enabled = group.enabled
        self._manager.setGroupEnabled(self._group_id, self._new_enabled)


class ToggleNodeOperation(Operation):
    """Operation to toggle an individual node on/off within its group."""

    def __init__(self, manager: "ModelGroupsManager", node: SceneNode, new_enabled: bool) -> None:
        super().__init__()
        self._manager = manager
        self._node = node
        self._new_enabled = new_enabled
        self._old_enabled = not new_enabled

    def undo(self) -> None:
        self._manager.setNodeEnabled(self._node, self._old_enabled)

    def redo(self) -> None:
        from .ModelGroupDecorator import ModelGroupDecorator
        decorator = self._node.getDecorator(ModelGroupDecorator)
        if decorator is not None:
            self._old_enabled = decorator.isModelGroupNodeEnabled()
        self._manager.setNodeEnabled(self._node, self._new_enabled)


class AssignToGroupOperation(Operation):
    """Operation to assign a node to a group, supporting undo/redo."""

    def __init__(self, manager: "ModelGroupsManager", node: SceneNode, group_id: str) -> None:
        super().__init__()
        self._manager = manager
        self._node = node
        self._group_id = group_id
        self._previous_group_id: Optional[str] = None

    def undo(self) -> None:
        if self._previous_group_id is not None:
            self._manager.addNodeToGroup(self._node, self._previous_group_id)
        else:
            self._manager.removeNodeFromGroup(self._node)

    def redo(self) -> None:
        self._previous_group_id = self._manager.addNodeToGroup(self._node, self._group_id)


class RemoveFromGroupOperation(Operation):
    """Operation to remove a node from its group, supporting undo/redo."""

    def __init__(self, manager: "ModelGroupsManager", node: SceneNode) -> None:
        super().__init__()
        self._manager = manager
        self._node = node
        self._old_group_id: Optional[str] = None

    def undo(self) -> None:
        if self._old_group_id is not None:
            self._manager.addNodeToGroup(self._node, self._old_group_id)

    def redo(self) -> None:
        self._old_group_id = self._manager.removeNodeFromGroup(self._node)
