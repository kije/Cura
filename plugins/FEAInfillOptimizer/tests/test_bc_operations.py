# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Unit tests for undo/redo BC operations and torque group CRUD.

Covers:
- operations/bc_operations.py: all Operation subclasses (undo/redo).
- FEABoundaryConditionDecorator torque group API:
  addTorqueGroup, getTorqueGroups, removeTorqueGroup, updateTorqueAxis,
  clearTorqueGroups, getTorqueGroupCount, torque round-trip serialization.

No Cura/UM imports required beyond minimal stubs (Operation base class,
Vector, SceneNodeDecorator).

Run with:
    source .test-venv/bin/activate
    python -m pytest plugins/FEAInfillOptimizer/tests/test_bc_operations.py -v
"""

from __future__ import annotations

import os
import sys
import types

# ---------------------------------------------------------------------------
# Ensure the plugins directory is on sys.path
# ---------------------------------------------------------------------------
_PLUGINS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

# ---------------------------------------------------------------------------
# Minimal stubs for UM dependencies
# ---------------------------------------------------------------------------

def _ensure_module(name: str) -> types.ModuleType:
    if name not in sys.modules:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return sys.modules[name]


for _n in [
    "UM", "UM.Math", "UM.Math.Vector", "UM.Operations", "UM.Operations.Operation",
    "UM.Scene", "UM.Scene.SceneNodeDecorator",
]:
    _ensure_module(_n)


class _Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __eq__(self, other):
        if not isinstance(other, _Vector):
            return NotImplemented
        return self.x == other.x and self.y == other.y and self.z == other.z

    def __repr__(self):
        return f"_Vector({self.x}, {self.y}, {self.z})"


sys.modules["UM.Math.Vector"].Vector = _Vector


class _Operation:
    """Minimal Operation base class stub."""
    def __init__(self):
        pass

    def push(self):
        self.redo()


sys.modules["UM.Operations.Operation"].Operation = _Operation


class _FakeDecorator:
    def __init__(self):
        self._node = None

    def setNode(self, node):
        self._node = node


sys.modules["UM.Scene.SceneNodeDecorator"].SceneNodeDecorator = _FakeDecorator

# ---------------------------------------------------------------------------
# Now safe to import plugin modules
# ---------------------------------------------------------------------------
import pytest  # noqa: E402

from FEAInfillOptimizer.FEABoundaryConditionDecorator import (  # noqa: E402
    FEABoundaryConditionDecorator,
    ForceGroup,
    TorqueGroup,
)
from FEAInfillOptimizer.operations.bc_operations import (  # noqa: E402
    AddFixedFacesOperation,
    AddForceGroupOperation,
    AddTorqueGroupOperation,
    ClearAllBCsOperation,
    ClearFixedFacesOperation,
    RemoveFixedFacesOperation,
    RemoveForceGroupOperation,
    RemoveTorqueGroupOperation,
    UpdateTorqueAxisOperation,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def dec():
    return FEABoundaryConditionDecorator()


def _v(x, y, z):
    return _Vector(x, y, z)


# ===========================================================================
# 1. AddFixedFacesOperation
# ===========================================================================


class TestAddFixedFacesOperation:

    def test_redo_adds_faces(self, dec):
        op = AddFixedFacesOperation(dec, [1, 2, 3])
        op.redo()
        assert sorted(dec.getFixedFaces()) == [1, 2, 3]

    def test_push_calls_redo(self, dec):
        op = AddFixedFacesOperation(dec, [10, 20])
        op.push()
        assert 10 in dec.getFixedFaces()

    def test_undo_removes_added_faces(self, dec):
        op = AddFixedFacesOperation(dec, [1, 2, 3])
        op.redo()
        op.undo()
        assert sorted(dec.getFixedFaces()) == []

    def test_undo_does_not_remove_pre_existing_faces(self, dec):
        dec.setFixedFaces([5, 6])
        op = AddFixedFacesOperation(dec, [1, 2])
        op.redo()
        op.undo()
        # Pre-existing faces 5,6 must still be present
        assert 5 in dec.getFixedFaces()
        assert 6 in dec.getFixedFaces()
        assert 1 not in dec.getFixedFaces()

    def test_redo_undo_redo_is_idempotent(self, dec):
        op = AddFixedFacesOperation(dec, [7, 8])
        op.redo()
        op.undo()
        op.redo()
        assert 7 in dec.getFixedFaces()
        assert 8 in dec.getFixedFaces()


# ===========================================================================
# 2. RemoveFixedFacesOperation
# ===========================================================================


class TestRemoveFixedFacesOperation:

    def test_redo_removes_faces(self, dec):
        dec.setFixedFaces([1, 2, 3])
        op = RemoveFixedFacesOperation(dec, [2, 3])
        op.redo()
        assert sorted(dec.getFixedFaces()) == [1]

    def test_undo_restores_removed_faces(self, dec):
        dec.setFixedFaces([1, 2, 3])
        op = RemoveFixedFacesOperation(dec, [2, 3])
        op.redo()
        op.undo()
        assert 2 in dec.getFixedFaces()
        assert 3 in dec.getFixedFaces()

    def test_redo_undo_redo_cycle(self, dec):
        dec.setFixedFaces([1, 2])
        op = RemoveFixedFacesOperation(dec, [1])
        op.redo()
        assert 1 not in dec.getFixedFaces()
        op.undo()
        assert 1 in dec.getFixedFaces()
        op.redo()
        assert 1 not in dec.getFixedFaces()


# ===========================================================================
# 3. ClearFixedFacesOperation
# ===========================================================================


class TestClearFixedFacesOperation:

    def test_redo_clears_all_faces(self, dec):
        dec.setFixedFaces([1, 2, 3, 4])
        op = ClearFixedFacesOperation(dec)
        op.redo()
        assert dec.getFixedFaces() == []

    def test_undo_restores_all_faces(self, dec):
        dec.setFixedFaces([10, 20, 30])
        op = ClearFixedFacesOperation(dec)
        op.redo()
        op.undo()
        assert sorted(dec.getFixedFaces()) == [10, 20, 30]

    def test_undo_on_empty_is_noop(self, dec):
        op = ClearFixedFacesOperation(dec)
        op.redo()
        op.undo()  # nothing to restore
        assert dec.getFixedFaces() == []


# ===========================================================================
# 4. AddForceGroupOperation
# ===========================================================================


class TestAddForceGroupOperation:

    def test_redo_adds_force_group(self, dec):
        op = AddForceGroupOperation(dec, [5, 6], _v(0, 0, -100))
        op.redo()
        groups = dec.getForceGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [5, 6]

    def test_undo_removes_last_force_group(self, dec):
        op = AddForceGroupOperation(dec, [5, 6], _v(0, 0, -100))
        op.redo()
        op.undo()
        assert len(dec.getForceGroups()) == 0

    def test_undo_removes_only_last_group(self, dec):
        """Pre-existing groups must survive undo."""
        dec.addForceGroup([1], _v(1, 0, 0))
        op = AddForceGroupOperation(dec, [2], _v(0, 1, 0))
        op.redo()
        op.undo()
        groups = dec.getForceGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [1]

    def test_force_vector_is_stored_correctly(self, dec):
        op = AddForceGroupOperation(dec, [1], _v(1.5, -2.5, 3.0))
        op.redo()
        f = dec.getForceGroups()[0].force
        assert f.x == pytest.approx(1.5)
        assert f.y == pytest.approx(-2.5)
        assert f.z == pytest.approx(3.0)


# ===========================================================================
# 5. RemoveForceGroupOperation
# ===========================================================================


class TestRemoveForceGroupOperation:

    def test_redo_removes_group_at_index(self, dec):
        dec.addForceGroup([1], _v(1, 0, 0))
        dec.addForceGroup([2], _v(0, 1, 0))
        op = RemoveForceGroupOperation(dec, 0)
        op.redo()
        assert len(dec.getForceGroups()) == 1
        assert dec.getForceGroups()[0].face_indices == [2]

    def test_undo_restores_group_at_original_index(self, dec):
        """Undo must re-insert the removed group at its original position."""
        dec.addForceGroup([1], _v(1, 0, 0))
        dec.addForceGroup([2], _v(0, 1, 0))
        dec.addForceGroup([3], _v(0, 0, 1))
        op = RemoveForceGroupOperation(dec, 1)  # remove middle
        op.redo()
        op.undo()
        groups = dec.getForceGroups()
        assert len(groups) == 3
        assert groups[1].face_indices == [2]  # back at index 1

    def test_undo_preserves_surrounding_groups(self, dec):
        dec.addForceGroup([10], _v(1, 0, 0))
        dec.addForceGroup([20], _v(0, 1, 0))
        op = RemoveForceGroupOperation(dec, 0)
        op.redo()
        op.undo()
        groups = dec.getForceGroups()
        assert groups[0].face_indices == [10]
        assert groups[1].face_indices == [20]


# ===========================================================================
# 6. AddTorqueGroupOperation
# ===========================================================================


class TestAddTorqueGroupOperation:

    def test_redo_adds_torque_group(self, dec):
        op = AddTorqueGroupOperation(dec, [1, 2], _v(0, 0, 1), 10.0)
        op.redo()
        groups = dec.getTorqueGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [1, 2]
        assert groups[0].torque_magnitude == pytest.approx(10.0)

    def test_undo_removes_added_torque_group(self, dec):
        op = AddTorqueGroupOperation(dec, [3], _v(1, 0, 0), 5.0)
        op.redo()
        op.undo()
        assert len(dec.getTorqueGroups()) == 0

    def test_axis_stored_correctly(self, dec):
        op = AddTorqueGroupOperation(dec, [1], _v(0.0, 1.0, 0.0), 20.0)
        op.redo()
        axis = dec.getTorqueGroups()[0].torque_axis
        assert axis.y == pytest.approx(1.0)


# ===========================================================================
# 7. RemoveTorqueGroupOperation
# ===========================================================================


class TestRemoveTorqueGroupOperation:

    def test_redo_removes_group_at_index(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 10.0)
        dec.addTorqueGroup([2], _v(0, 1, 0), 20.0)
        op = RemoveTorqueGroupOperation(dec, 0)
        op.redo()
        groups = dec.getTorqueGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [2]

    def test_undo_restores_group_at_original_position(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 10.0)
        dec.addTorqueGroup([2], _v(0, 1, 0), 20.0)
        op = RemoveTorqueGroupOperation(dec, 0)
        op.redo()
        op.undo()
        groups = dec.getTorqueGroups()
        assert len(groups) == 2
        assert groups[0].face_indices == [1]


# ===========================================================================
# 8. UpdateTorqueAxisOperation
# ===========================================================================


class TestUpdateTorqueAxisOperation:

    def test_redo_sets_new_axis(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 5.0)
        op = UpdateTorqueAxisOperation(dec, 0, _v(0, 0, 1), _v(0, 1, 0))
        op.redo()
        axis = dec.getTorqueGroups()[0].torque_axis
        assert axis.y == pytest.approx(1.0)
        assert axis.z == pytest.approx(0.0)

    def test_undo_restores_old_axis(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 5.0)
        old_axis = _v(0, 0, 1)
        new_axis = _v(1, 0, 0)
        op = UpdateTorqueAxisOperation(dec, 0, old_axis, new_axis)
        op.redo()
        op.undo()
        axis = dec.getTorqueGroups()[0].torque_axis
        assert axis.z == pytest.approx(1.0)
        assert axis.x == pytest.approx(0.0)


# ===========================================================================
# 9. ClearAllBCsOperation
# ===========================================================================


class TestClearAllBCsOperation:

    def test_redo_clears_everything(self, dec):
        dec.setFixedFaces([1, 2])
        dec.addForceGroup([3], _v(0, 0, -1))
        dec.setMaterialName("PLA")
        op = ClearAllBCsOperation(dec)
        op.redo()
        assert dec.getFixedFaces() == []
        assert dec.getForceGroups() == []
        assert dec.getMaterialName() is None

    def test_undo_restores_full_state(self, dec):
        dec.setFixedFaces([1, 2])
        dec.addForceGroup([3], _v(0, 0, -100))
        dec.setMaterialName("ABS")
        op = ClearAllBCsOperation(dec)
        op.redo()
        op.undo()
        assert sorted(dec.getFixedFaces()) == [1, 2]
        assert len(dec.getForceGroups()) == 1
        assert dec.getMaterialName() == "ABS"

    def test_undo_redo_undo_cycle(self, dec):
        dec.setFixedFaces([5, 6])
        op = ClearAllBCsOperation(dec)
        op.redo()  # clear
        op.undo()  # restore
        op.redo()  # clear again
        assert dec.getFixedFaces() == []

    def test_undo_restores_torque_groups(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 10.0)
        op = ClearAllBCsOperation(dec)
        op.redo()
        op.undo()
        groups = dec.getTorqueGroups()
        assert len(groups) == 1
        assert groups[0].torque_magnitude == pytest.approx(10.0)


# ===========================================================================
# 10. TorqueGroup CRUD on FEABoundaryConditionDecorator
# ===========================================================================


class TestTorqueGroupCRUD:

    def test_initial_torque_groups_empty(self, dec):
        assert dec.getTorqueGroups() == []

    def test_add_torque_group(self, dec):
        dec.addTorqueGroup([1, 2], _v(0, 0, 1), 15.0)
        groups = dec.getTorqueGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [1, 2]
        assert groups[0].torque_magnitude == pytest.approx(15.0)

    def test_add_multiple_torque_groups(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 10.0)
        dec.addTorqueGroup([2], _v(1, 0, 0), 20.0)
        assert len(dec.getTorqueGroups()) == 2

    def test_remove_torque_group_by_index(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 10.0)
        dec.addTorqueGroup([2], _v(0, 1, 0), 20.0)
        dec.removeTorqueGroup(0)
        groups = dec.getTorqueGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [2]

    def test_remove_out_of_range_is_noop(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 5.0)
        dec.removeTorqueGroup(99)  # should not crash
        assert len(dec.getTorqueGroups()) == 1

    def test_clear_torque_groups(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 5.0)
        dec.clearTorqueGroups()
        assert dec.getTorqueGroups() == []

    def test_get_torque_group_count(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 5.0)
        dec.addTorqueGroup([2], _v(1, 0, 0), 10.0)
        assert dec.getTorqueGroupCount() == 2

    def test_update_torque_axis(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 5.0)
        dec.updateTorqueAxis(0, _v(1, 0, 0))
        axis = dec.getTorqueGroups()[0].torque_axis
        assert axis.x == pytest.approx(1.0)
        assert axis.z == pytest.approx(0.0)


# ===========================================================================
# 11. TorqueGroup serialization round-trip
# ===========================================================================


class TestTorqueGroupSerialization:

    def test_to_dict_has_face_indices_and_axis_and_magnitude(self):
        tg = TorqueGroup([1, 2], _v(0, 0, 1), 10.0)
        d = tg.to_dict()
        assert "face_indices" in d
        assert "torque_axis" in d
        assert "torque_magnitude" in d

    def test_from_dict_round_trip(self):
        tg = TorqueGroup([5, 6, 7], _v(0.0, 1.0, 0.0), 25.0)
        d = tg.to_dict()
        restored = TorqueGroup.from_dict(d)
        assert restored.face_indices == [5, 6, 7]
        assert restored.torque_magnitude == pytest.approx(25.0)
        assert restored.torque_axis.y == pytest.approx(1.0)

    def test_decorator_torque_round_trip_via_to_from_dict(self, dec):
        dec.addTorqueGroup([1, 2], _v(0, 0, 1), 12.0)
        dec.addTorqueGroup([3], _v(1, 0, 0), 8.0)
        dec.setMaterialName("PLA")

        state = dec.toDict()
        restored = FEABoundaryConditionDecorator()
        restored.fromDict(state)

        groups = restored.getTorqueGroups()
        assert len(groups) == 2
        assert groups[0].face_indices == [1, 2]
        assert groups[0].torque_magnitude == pytest.approx(12.0)
        assert groups[1].face_indices == [3]
        assert groups[1].torque_magnitude == pytest.approx(8.0)
        assert restored.getMaterialName() == "PLA"

    def test_from_dict_missing_torque_groups_defaults_to_empty(self, dec):
        dec.fromDict({"fixed_faces": [], "force_groups": []})
        assert dec.getTorqueGroups() == []

    def test_clear_all_also_clears_torque_groups(self, dec):
        dec.addTorqueGroup([1], _v(0, 0, 1), 5.0)
        dec.clearAll()
        assert dec.getTorqueGroups() == []
        assert dec.hasAnyBC() is False
