# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from typing import Dict, List, Optional, Tuple

from UM.Math.Vector import Vector
from UM.Scene.SceneNodeDecorator import SceneNodeDecorator


class ForceGroup:
    """A group of faces with an applied force vector."""

    def __init__(self, face_indices: List[int], force: Vector) -> None:
        self.face_indices = list(face_indices)
        self.force = force

    def to_dict(self) -> dict:
        return {
            "face_indices": self.face_indices,
            "force": [self.force.x, self.force.y, self.force.z]
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ForceGroup":
        return cls(
            face_indices=data["face_indices"],
            force=Vector(data["force"][0], data["force"][1], data["force"][2])
        )


class TorqueGroup:
    """A group of faces with an applied torque (moment) about an axis.

    The torque is defined by a magnitude and an axis direction. During FEA
    assembly, the torque is converted to equivalent tangential nodal forces:
    for each node at distance r from the torque center, the tangential force
    is F_t = T / (N * r) where N is the number of nodes and r is the
    perpendicular distance from the axis.
    """

    def __init__(self, face_indices: List[int], torque_axis: Vector,
                 torque_magnitude: float) -> None:
        self.face_indices = list(face_indices)
        self.torque_axis = torque_axis      # unit direction vector of the axis
        self.torque_magnitude = torque_magnitude  # Nm

    def to_dict(self) -> dict:
        return {
            "face_indices": self.face_indices,
            "torque_axis": [self.torque_axis.x, self.torque_axis.y, self.torque_axis.z],
            "torque_magnitude": self.torque_magnitude
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TorqueGroup":
        return cls(
            face_indices=data["face_indices"],
            torque_axis=Vector(data["torque_axis"][0], data["torque_axis"][1],
                               data["torque_axis"][2]),
            torque_magnitude=data["torque_magnitude"]
        )


class FEABoundaryConditionDecorator(SceneNodeDecorator):
    """Stores FEA boundary condition data on a CuraSceneNode.

    Boundary conditions consist of:
    - Fixed faces: triangles with zero displacement (Dirichlet BC)
    - Force groups: sets of triangles with an applied force vector (Neumann BC)
    - Torque groups: sets of triangles with a moment about an axis
    - Material override: optional per-node material specification
    """

    def __init__(self) -> None:
        super().__init__()
        self._fixed_faces: List[int] = []
        self._force_groups: List[ForceGroup] = []
        self._torque_groups: List[TorqueGroup] = []
        self._material_name: Optional[str] = None

    # -- Fixed faces --

    def getFixedFaces(self) -> List[int]:
        return self._fixed_faces

    def setFixedFaces(self, face_indices: List[int]) -> None:
        self._fixed_faces = list(face_indices)

    def addFixedFaces(self, face_indices: List[int]) -> None:
        existing = set(self._fixed_faces)
        for idx in face_indices:
            if idx not in existing:
                self._fixed_faces.append(idx)
                existing.add(idx)

    def removeFixedFaces(self, face_indices: List[int]) -> None:
        to_remove = set(face_indices)
        self._fixed_faces = [f for f in self._fixed_faces if f not in to_remove]

    def clearFixedFaces(self) -> None:
        self._fixed_faces.clear()

    # -- Force groups --

    def getForceGroups(self) -> List[ForceGroup]:
        return self._force_groups

    def addForceGroup(self, face_indices: List[int], force: Vector) -> None:
        self._force_groups.append(ForceGroup(face_indices, force))

    def removeForceGroup(self, index: int) -> None:
        if 0 <= index < len(self._force_groups):
            self._force_groups.pop(index)

    def clearForceGroups(self) -> None:
        self._force_groups.clear()

    # -- Torque groups --

    def getTorqueGroups(self) -> List[TorqueGroup]:
        return self._torque_groups

    def addTorqueGroup(self, face_indices: List[int], torque_axis: Vector,
                       torque_magnitude: float) -> None:
        self._torque_groups.append(TorqueGroup(face_indices, torque_axis, torque_magnitude))

    def removeTorqueGroup(self, index: int) -> None:
        if 0 <= index < len(self._torque_groups):
            self._torque_groups.pop(index)

    def clearTorqueGroups(self) -> None:
        self._torque_groups.clear()

    def getTorqueGroupCount(self) -> int:
        return len(self._torque_groups)

    # -- Material --

    def getMaterialName(self) -> Optional[str]:
        return self._material_name

    def setMaterialName(self, name: Optional[str]) -> None:
        self._material_name = name

    # -- Decorator access (for node.callDecoration("getBoundaryConditions")) --

    def getBoundaryConditions(self) -> "FEABoundaryConditionDecorator":
        """Return self so that callDecoration("getBoundaryConditions") works."""
        return self

    # -- Queries --

    def hasAnyBC(self) -> bool:
        return (len(self._fixed_faces) > 0 or len(self._force_groups) > 0
                or len(self._torque_groups) > 0)

    def getFixedFaceCount(self) -> int:
        return len(self._fixed_faces)

    def getForceGroupCount(self) -> int:
        return len(self._force_groups)

    # -- Clear all --

    def clearAll(self) -> None:
        self._fixed_faces.clear()
        self._force_groups.clear()
        self._torque_groups.clear()
        self._material_name = None

    # -- Serialization --

    def toDict(self) -> dict:
        return {
            "fixed_faces": self._fixed_faces,
            "force_groups": [fg.to_dict() for fg in self._force_groups],
            "torque_groups": [tg.to_dict() for tg in self._torque_groups],
            "material_name": self._material_name
        }

    def fromDict(self, data: dict) -> None:
        self._fixed_faces = data.get("fixed_faces", [])
        self._force_groups = [
            ForceGroup.from_dict(fg) for fg in data.get("force_groups", [])
        ]
        self._torque_groups = [
            TorqueGroup.from_dict(tg) for tg in data.get("torque_groups", [])
        ]
        self._material_name = data.get("material_name")

    def __deepcopy__(self, memo: dict) -> "FEABoundaryConditionDecorator":
        # SceneNodeDecorator.__deepcopy__ raises NotImplementedError by design;
        # construct the copy manually and reset _node to None (the node
        # relationship is re-established when the decorator is added to a copied
        # scene node via setNode()).
        copy = FEABoundaryConditionDecorator()
        memo[id(self)] = copy
        copy._node = None
        copy._fixed_faces = list(self._fixed_faces)
        copy._force_groups = [
            ForceGroup(list(fg.face_indices), Vector(fg.force.x, fg.force.y, fg.force.z))
            for fg in self._force_groups
        ]
        copy._torque_groups = [
            TorqueGroup(list(tg.face_indices),
                        Vector(tg.torque_axis.x, tg.torque_axis.y, tg.torque_axis.z),
                        tg.torque_magnitude)
            for tg in self._torque_groups
        ]
        copy._material_name = self._material_name
        return copy
