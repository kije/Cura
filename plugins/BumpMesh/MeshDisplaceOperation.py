# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

from UM.Mesh.MeshData import MeshData
from UM.Operations.Operation import Operation
from UM.Scene.SceneNode import SceneNode


class MeshDisplaceOperation(Operation):
    """Undoable operation that swaps mesh data on a scene node."""

    def __init__(self, node: SceneNode, old_mesh_data: MeshData, new_mesh_data: MeshData) -> None:
        super().__init__()
        self._node = node
        self._old_mesh_data = old_mesh_data
        self._new_mesh_data = new_mesh_data

    def undo(self) -> None:
        self._node.setMeshData(self._old_mesh_data)

    def redo(self) -> None:
        self._node.setMeshData(self._new_mesh_data)
