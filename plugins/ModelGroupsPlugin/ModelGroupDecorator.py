# Copyright (c) 2024 Community
# Released under the terms of the LGPLv3 or higher.

from UM.Scene.SceneNodeDecorator import SceneNodeDecorator


class ModelGroupDecorator(SceneNodeDecorator):
    """Decorator that tags a scene node with its model group membership."""

    def __init__(self) -> None:
        super().__init__()
        self._group_id: str = ""
        self._node_enabled: bool = True

    def getModelGroupId(self) -> str:
        return self._group_id

    def setModelGroupId(self, group_id: str) -> None:
        self._group_id = group_id

    def isModelGroupNodeEnabled(self) -> bool:
        return self._node_enabled

    def setModelGroupNodeEnabled(self, enabled: bool) -> None:
        self._node_enabled = enabled

    def __deepcopy__(self, memo) -> "ModelGroupDecorator":
        copied = ModelGroupDecorator()
        copied._group_id = self._group_id
        copied._node_enabled = self._node_enabled
        return copied
