# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Undoable operations for FEA boundary condition mutations.

Each class wraps a single BC decorator mutation so it can be pushed onto
Cura's undo stack via ``op.push()``.  ``push()`` calls ``redo()``
internally, so the mutation must NOT be performed before calling push().
"""

from UM.Math.Vector import Vector
from UM.Operations.Operation import Operation

from ..FEABoundaryConditionDecorator import ForceGroup, TorqueGroup


class AddFixedFacesOperation(Operation):
    """Undoable add of fixed faces to a BC decorator."""

    def __init__(self, decorator, face_indices):
        super().__init__()
        self._decorator = decorator
        self._face_indices = list(face_indices)

    def undo(self):
        self._decorator.removeFixedFaces(self._face_indices)

    def redo(self):
        self._decorator.addFixedFaces(self._face_indices)


class RemoveFixedFacesOperation(Operation):
    """Undoable removal of specific fixed faces from a BC decorator."""

    def __init__(self, decorator, face_indices):
        super().__init__()
        self._decorator = decorator
        self._face_indices = list(face_indices)

    def undo(self):
        self._decorator.addFixedFaces(self._face_indices)

    def redo(self):
        self._decorator.removeFixedFaces(self._face_indices)


class ClearFixedFacesOperation(Operation):
    """Undoable clear of all fixed faces."""

    def __init__(self, decorator):
        super().__init__()
        self._decorator = decorator
        self._saved_faces = list(decorator.getFixedFaces())

    def undo(self):
        self._decorator.addFixedFaces(self._saved_faces)

    def redo(self):
        self._decorator.clearFixedFaces()


class AddForceGroupOperation(Operation):
    """Undoable add of a force group (appended to end of list)."""

    def __init__(self, decorator, face_indices, force: Vector):
        super().__init__()
        self._decorator = decorator
        self._face_indices = list(face_indices)
        self._force = Vector(force.x, force.y, force.z)

    def undo(self):
        # The group was appended; undo removes the last entry.
        groups = self._decorator.getForceGroups()
        if groups:
            self._decorator.removeForceGroup(len(groups) - 1)

    def redo(self):
        self._decorator.addForceGroup(self._face_indices, self._force)


class RemoveForceGroupOperation(Operation):
    """Undoable removal of a force group by index.

    Undo restores the full force-group list order by snapshotting the
    surrounding groups and re-inserting the removed entry at its original
    position using direct list manipulation on the decorator's internal list.
    """

    def __init__(self, decorator, index: int):
        super().__init__()
        self._decorator = decorator
        self._index = index
        group = decorator.getForceGroups()[index]
        self._face_indices = list(group.face_indices)
        self._force = Vector(group.force.x, group.force.y, group.force.z)

    def undo(self):
        # Re-insert at original position directly into the internal list.
        self._decorator._force_groups.insert(
            self._index,
            ForceGroup(self._face_indices, self._force)
        )

    def redo(self):
        self._decorator.removeForceGroup(self._index)


class AddTorqueGroupOperation(Operation):
    """Undoable add of a torque group (appended to end of list)."""

    def __init__(self, decorator, face_indices, torque_axis: Vector,
                 torque_magnitude: float):
        super().__init__()
        self._decorator = decorator
        self._face_indices = list(face_indices)
        self._torque_axis = Vector(torque_axis.x, torque_axis.y, torque_axis.z)
        self._torque_magnitude = torque_magnitude

    def undo(self):
        groups = self._decorator.getTorqueGroups()
        if groups:
            self._decorator.removeTorqueGroup(len(groups) - 1)

    def redo(self):
        self._decorator.addTorqueGroup(
            self._face_indices, self._torque_axis, self._torque_magnitude
        )


class RemoveTorqueGroupOperation(Operation):
    """Undoable removal of a torque group by index."""

    def __init__(self, decorator, index: int):
        super().__init__()
        self._decorator = decorator
        self._index = index
        group = decorator.getTorqueGroups()[index]
        self._face_indices = list(group.face_indices)
        self._torque_axis = Vector(group.torque_axis.x, group.torque_axis.y,
                                   group.torque_axis.z)
        self._torque_magnitude = group.torque_magnitude

    def undo(self):
        self._decorator._torque_groups.insert(
            self._index,
            TorqueGroup(self._face_indices, self._torque_axis,
                        self._torque_magnitude)
        )

    def redo(self):
        self._decorator.removeTorqueGroup(self._index)


class UpdateTorqueAxisOperation(Operation):
    """Undoable update of a torque group's axis direction.

    The axis is mutated in-place during drag; this operation receives both
    the old (pre-drag) and new (post-drag) axes so undo/redo work correctly.
    Note: ``redo()`` is called automatically by ``push()``, but the axis is
    already set during drag — ``updateTorqueAxis`` is idempotent.
    """

    def __init__(self, decorator, index: int, old_axis: Vector, new_axis: Vector):
        super().__init__()
        self._decorator = decorator
        self._index = index
        self._old_axis = Vector(old_axis.x, old_axis.y, old_axis.z)
        self._new_axis = Vector(new_axis.x, new_axis.y, new_axis.z)

    def undo(self):
        self._decorator.updateTorqueAxis(self._index, self._old_axis)

    def redo(self):
        self._decorator.updateTorqueAxis(self._index, self._new_axis)


class ClearAllBCsOperation(Operation):
    """Undoable clear-all; snapshots full BC state for undo."""

    def __init__(self, decorator):
        super().__init__()
        self._decorator = decorator
        self._saved_state = decorator.toDict()

    def undo(self):
        self._decorator.fromDict(self._saved_state)

    def redo(self):
        self._decorator.clearAll()
