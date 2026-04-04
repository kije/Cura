# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Tests for the Confirm-and-Optimize → dialog model-selection flow.

Covers the race-condition fix:
- _ensureDialog() caches the dialog (creates once, reuses)
- showDialogForNode sets preselectedNodeKey before showing the dialog
- getSceneNodes returns the correct format and populates _node_cache
- BoundaryConditionTool.setOpenOptimizeDialog dispatches to the extension

Run with:
    source .test-venv/bin/activate
    python -m pytest plugins/FEAInfillOptimizer/tests/test_dialog_model_selection.py -v \\
        --override-ini="testpaths=plugins/FEAInfillOptimizer/tests" \\
        --override-ini="python_files=test_*.py" \\
        --override-ini="python_classes=Test"
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Ensure plugin root is importable
# ---------------------------------------------------------------------------
_PLUGINS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

# ---------------------------------------------------------------------------
# Build the minimal sys.modules stubs required by FEAInfillExtension and
# BoundaryConditionTool before any plugin import occurs.
# ---------------------------------------------------------------------------

def _ensure_real_module(name: str) -> types.ModuleType:
    existing = sys.modules.get(name)
    if isinstance(existing, types.ModuleType):
        return existing
    mod = types.ModuleType(name)
    if isinstance(existing, MagicMock):
        for attr in list(vars(existing)):
            if not attr.startswith("_"):
                try:
                    setattr(mod, attr, getattr(existing, attr))
                except Exception:
                    pass
    sys.modules[name] = mod
    return mod


def _ensure_chain(dotted: str) -> None:
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        _ensure_real_module(".".join(parts[:i]))


_REQUIRED = [
    # PyQt6 — must be real modules so decorators can be evaluated
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtWidgets",
    # UM
    "UM",
    "UM.Application",
    "UM.Extension",
    "UM.JobQueue",
    "UM.Logger",
    "UM.Message",
    "UM.PluginRegistry",
    "UM.Scene",
    "UM.Scene.Iterator",
    "UM.Scene.Iterator.DepthFirstIterator",
    "UM.Scene.SceneNodeDecorator",
    "UM.Scene.Selection",
    "UM.Scene.ToolHandle",
    "UM.Tool",
    "UM.Event",
    "UM.Math",
    "UM.Math.Color",
    "UM.Math.Plane",
    "UM.Math.Quaternion",
    "UM.Math.Vector",
    "UM.Mesh",
    "UM.Mesh.MeshBuilder",
    "UM.Mesh.MeshData",
    "UM.Operations",
    "UM.Operations.AddSceneNodeOperation",
    "UM.Operations.GroupedOperation",
    "UM.Operations.RemoveSceneNodeOperation",
    "UM.Settings",
    "UM.Settings.SettingInstance",
    "UM.Signal",
    "UM.i18n",
    # cura
    "cura",
    "cura.CuraApplication",
    "cura.PickingPass",
    "cura.Scene",
    "cura.Scene.BuildPlateDecorator",
    "cura.Scene.CuraSceneNode",
    "cura.Scene.SliceableObjectDecorator",
    # numpy (already present in test-venv; this is a no-op)
    "numpy",
]

for _name in _REQUIRED:
    _ensure_chain(_name)

# ---------------------------------------------------------------------------
# Attach stub objects so that module-level attribute access succeeds.
# ---------------------------------------------------------------------------

# PyQt6 stubs — we need pyqtProperty, pyqtSignal, pyqtSlot to be no-ops
class _pyqtSignal:  # noqa: N801
    """Descriptor that returns a MagicMock on first access."""
    def __init__(self, *args, **kwargs):
        self._mock = MagicMock(name="signal")
        self._mock.connect = MagicMock()
        self._mock.emit = MagicMock()

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        attr = f"_sig_{self._name}"
        if not hasattr(obj, attr):
            m = MagicMock(name=f"signal_{self._name}")
            m.connect = MagicMock()
            m.emit = MagicMock()
            object.__setattr__(obj, attr, m)
        return object.__getattribute__(obj, attr)

    def connect(self, *a, **kw):
        pass

    def emit(self, *a, **kw):
        pass


def _pyqtProperty(typ, notify=None, fget=None, fset=None):  # noqa: N802
    """Replacement for pyqtProperty: behaves like a plain property."""
    def decorator(func):
        return property(func)
    if fget is not None:
        return property(fget, fset)
    return decorator


def _pyqtSlot(*args, **kwargs):  # noqa: N802
    """Replacement for pyqtSlot: pass-through decorator."""
    def decorator(func):
        return func
    return decorator


_qtcore = sys.modules["PyQt6.QtCore"]
_qtcore.QObject = type("QObject", (), {"__init__": lambda self, parent=None: None})
_qtcore.pyqtSignal = _pyqtSignal
_qtcore.pyqtProperty = _pyqtProperty
_qtcore.pyqtSlot = _pyqtSlot
_qtcore.Qt = MagicMock(name="Qt")

_qtwidgets = sys.modules["PyQt6.QtWidgets"]
_qtwidgets.QApplication = MagicMock(name="QApplication")

# UM.i18n catalog
class _FakeCatalog:
    def __init__(self, *a, **kw): pass
    def i18nc(self, ctx, text, *a): return text
    def i18n(self, text, *a): return text

sys.modules["UM.i18n"].i18nCatalog = _FakeCatalog

# UM.Logger
sys.modules["UM.Logger"].Logger = MagicMock(name="Logger")

# UM.Application
sys.modules["UM.Application"].Application = MagicMock(name="Application")

# UM.Extension
class _FakeExtension:
    def setMenuName(self, name): pass
    def addMenuItem(self, name, cb): pass

sys.modules["UM.Extension"].Extension = _FakeExtension

# UM.JobQueue
sys.modules["UM.JobQueue"].JobQueue = MagicMock(name="JobQueue")

# UM.Message
_msg = MagicMock(name="Message")
_msg.MessageType = MagicMock(name="MessageType")
sys.modules["UM.Message"].Message = _msg

# UM.PluginRegistry
sys.modules["UM.PluginRegistry"].PluginRegistry = MagicMock(name="PluginRegistry")

# UM.Scene.Iterator.DepthFirstIterator
class _FakeDepthFirstIterator:
    """Yields nodes from a flat list stored on root._nodes."""
    def __init__(self, root):
        self._nodes = getattr(root, "_nodes", [])

    def __iter__(self):
        return iter(self._nodes)

sys.modules["UM.Scene.Iterator.DepthFirstIterator"].DepthFirstIterator = _FakeDepthFirstIterator

# UM.Scene.Selection
sys.modules["UM.Scene.Selection"].Selection = MagicMock(name="Selection")

# UM.Scene.ToolHandle
sys.modules["UM.Scene.ToolHandle"].ToolHandle = MagicMock(name="ToolHandle")

# UM.Tool
class _FakeTool:
    def __init__(self):
        self.propertyChanged = MagicMock(name="propertyChanged")
        self.propertyChanged.emit = MagicMock()

    def getController(self):
        return MagicMock(name="controller")

    def setExposedProperties(self, *args):
        pass

    def event(self, event):
        return False

sys.modules["UM.Tool"].Tool = _FakeTool

# UM.Event
sys.modules["UM.Event"].Event = MagicMock(name="Event")
sys.modules["UM.Event"].MouseEvent = MagicMock(name="MouseEvent")

# UM.Math stubs
sys.modules["UM.Math.Plane"].Plane = MagicMock(name="Plane")
sys.modules["UM.Math.Quaternion"].Quaternion = MagicMock(name="Quaternion")

class _Vector:
    Unit_X = None
    Unit_Y = None
    Unit_Z = None
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x); self.y = float(y); self.z = float(z)

sys.modules["UM.Math.Vector"].Vector = _Vector

# UM.Math.Color
sys.modules["UM.Math.Color"].Color = MagicMock(name="Color")

# UM.Mesh stubs
class _FakeMeshData:
    def __init__(self, vertices=None, indices=None):
        import numpy as _np
        self._v = vertices if vertices is not None else _np.zeros((0, 3), dtype="float32")
        self._i = indices  if indices  is not None else _np.zeros((0, 3), dtype="int32")
    def getVertices(self): return self._v
    def getIndices(self):  return self._i

class _FakeMeshBuilder:
    def __init__(self): pass
    def addFace(self, *a, **kw): pass
    def addVertex(self, *a, **kw): pass
    def setVertices(self, v): pass
    def setIndices(self, i): pass
    def calculateNormals(self): pass
    def build(self):
        return _FakeMeshData()

sys.modules["UM.Mesh.MeshBuilder"].MeshBuilder = _FakeMeshBuilder
sys.modules["UM.Mesh.MeshData"].MeshData = _FakeMeshData

# UM.Operations stubs
sys.modules["UM.Operations.AddSceneNodeOperation"].AddSceneNodeOperation = MagicMock(name="AddSceneNodeOperation")
sys.modules["UM.Operations.GroupedOperation"].GroupedOperation = MagicMock(name="GroupedOperation")
sys.modules["UM.Operations.RemoveSceneNodeOperation"].RemoveSceneNodeOperation = MagicMock(name="RemoveSceneNodeOperation")

# UM.Settings stubs
class _FakeSettingInstance:
    def __init__(self, definition, container):
        self.definition = definition
        self.container  = container
        self.properties: dict = {}
    def setProperty(self, prop, value): self.properties[prop] = value
    def resetState(self): pass

sys.modules["UM.Settings.SettingInstance"].SettingInstance = _FakeSettingInstance

# UM.Scene.SceneNodeDecorator
class _FakeSceneNodeDecorator:
    def __init__(self):
        self._node = None
    def setNode(self, node):
        self._node = node

sys.modules["UM.Scene.SceneNodeDecorator"].SceneNodeDecorator = _FakeSceneNodeDecorator

# cura stubs
_CuraApplication_mock = MagicMock(name="CuraApplication")
sys.modules["cura.CuraApplication"].CuraApplication = _CuraApplication_mock

sys.modules["cura.PickingPass"].PickingPass = MagicMock(name="PickingPass")

class _FakeCuraSceneNode:
    """Minimal CuraSceneNode stand-in."""
    def __init__(self, name="TestNode", selectable=True, has_mesh=True, non_printing=False):
        self._name = name
        self._selectable = selectable
        self._has_mesh = has_mesh
        self._non_printing = non_printing
        self._decorators = {}

    def getName(self):
        return self._name

    def isSelectable(self):
        return self._selectable

    def getMeshData(self):
        if not self._has_mesh:
            return None
        mesh = MagicMock(name="mesh_data")
        mesh.getVertices.return_value = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        return mesh

    def callDecoration(self, method_name, *args, **kwargs):
        if method_name == "isNonPrintingMesh":
            return self._non_printing
        if method_name == "getBoundaryConditions":
            return self._decorators.get("bc")
        return None

    def addDecorator(self, decorator):
        if hasattr(decorator, "getBoundaryConditions"):
            self._decorators["bc"] = decorator

sys.modules["cura.Scene.CuraSceneNode"].CuraSceneNode = _FakeCuraSceneNode
sys.modules["cura.Scene.BuildPlateDecorator"].BuildPlateDecorator = MagicMock(name="BuildPlateDecorator")
sys.modules["cura.Scene.SliceableObjectDecorator"].SliceableObjectDecorator = MagicMock(name="SliceableObjectDecorator")

# ---------------------------------------------------------------------------
# Now import the modules under test
# ---------------------------------------------------------------------------
import pytest  # noqa: E402

from FEAInfillOptimizer.FEAInfillExtension import FEAInfillExtension  # noqa: E402
from FEAInfillOptimizer.BoundaryConditionTool import BoundaryConditionTool  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_extension() -> FEAInfillExtension:
    """Create a FEAInfillExtension with all Cura dependencies mocked out."""
    app_inst = MagicMock(name="app_instance")
    app_inst.engineCreatedSignal = MagicMock()
    app_inst.engineCreatedSignal.connect = MagicMock()
    _CuraApplication_mock.getInstance.return_value = app_inst
    ext = FEAInfillExtension.__new__(FEAInfillExtension)
    # Manually run __init__ without QObject machinery
    _FakeExtension.__init__(ext)
    ext._dialog = None
    ext._dep_manager = None
    ext._deps_available = False
    import weakref
    ext._node_cache = weakref.WeakValueDictionary()
    ext._preselected_node_key = ""
    ext._analysis_status = "idle"
    ext._progress = 0.0
    ext._analysis_stage = ""
    ext._results = None
    ext._material_name = "PLA"
    ext._min_density = 10.0
    ext._max_density = 80.0
    ext._num_zones = 6
    ext._infill_pattern = "gyroid"
    ext._max_iterations = 5
    ext._mesh_resolution = "medium"
    ext._safety_factor = 2.0
    ext._bonding_coeff = 0.5

    # Attach signal mocks so emit() calls don't crash
    ext.preselectedNodeChanged = MagicMock(name="preselectedNodeChanged")
    ext.preselectedNodeChanged.emit = MagicMock()
    ext.sceneNodesChanged = MagicMock(name="sceneNodesChanged")
    ext.sceneNodesChanged.emit = MagicMock()
    ext.depsAvailableChanged = MagicMock(name="depsAvailableChanged")
    ext.depsAvailableChanged.emit = MagicMock()

    return ext


def _make_scene_with_nodes(*nodes: _FakeCuraSceneNode):
    """Return a mock app whose scene root contains the given nodes."""
    root = MagicMock(name="scene_root")
    root._nodes = list(nodes)
    scene = MagicMock(name="scene")
    scene.getRoot.return_value = root
    controller = MagicMock(name="controller")
    controller.getScene.return_value = scene
    app_inst = MagicMock(name="app_instance")
    app_inst.getController.return_value = controller
    _CuraApplication_mock.getInstance.return_value = app_inst
    return app_inst


# ===========================================================================
# Test 1: _ensureDialog creates the dialog only once
# ===========================================================================

class TestEnsureDialogCreatedOnce:

    def test_create_qml_called_only_on_first_ensure(self):
        """_ensureDialog() must call createQmlComponent exactly once."""
        # Arrange
        ext = _make_extension()
        fake_dialog = MagicMock(name="dialog")
        app_inst = MagicMock(name="app_instance")
        app_inst.createQmlComponent.return_value = fake_dialog
        _CuraApplication_mock.getInstance.return_value = app_inst

        plugin_reg = MagicMock(name="plugin_registry")
        plugin_reg.getPluginPath.return_value = "/fake/plugin/path"

        with patch("FEAInfillOptimizer.FEAInfillExtension.PluginRegistry") as mock_reg:
            mock_reg.getInstance.return_value = plugin_reg

            # Act — call twice
            ext._ensureDialog()
            ext._ensureDialog()

        # Assert
        assert app_inst.createQmlComponent.call_count == 1

    def test_dialog_instance_is_same_object_on_second_call(self):
        """The dialog reference must be identical after two _ensureDialog() calls."""
        ext = _make_extension()
        fake_dialog = MagicMock(name="dialog")
        app_inst = MagicMock(name="app_instance")
        app_inst.createQmlComponent.return_value = fake_dialog
        _CuraApplication_mock.getInstance.return_value = app_inst

        plugin_reg = MagicMock(name="plugin_registry")
        plugin_reg.getPluginPath.return_value = "/fake/plugin/path"

        with patch("FEAInfillOptimizer.FEAInfillExtension.PluginRegistry") as mock_reg:
            mock_reg.getInstance.return_value = plugin_reg
            ext._ensureDialog()
            dialog_first = ext._dialog
            ext._ensureDialog()
            dialog_second = ext._dialog

        assert dialog_first is dialog_second
        assert dialog_first is fake_dialog

    def test_second_call_returns_early_when_dialog_exists(self):
        """If _dialog is already set, _ensureDialog must not touch PluginRegistry."""
        ext = _make_extension()
        ext._dialog = MagicMock(name="existing_dialog")

        with patch("FEAInfillOptimizer.FEAInfillExtension.PluginRegistry") as mock_reg:
            ext._ensureDialog()
            mock_reg.getInstance.assert_not_called()


# ===========================================================================
# Test 2: showDialogForNode sets preselectedNodeKey before showing
# ===========================================================================

class TestShowDialogForNodeKeyOrder:

    def _setup(self):
        ext = _make_extension()
        fake_dialog = MagicMock(name="dialog")
        ext._dialog = fake_dialog  # pre-set so _ensureDialog is a no-op
        # Empty scene so getSceneNodes doesn't explode
        _make_scene_with_nodes()
        return ext, fake_dialog

    def test_preselected_node_key_set_before_show(self):
        """_preselected_node_key must be updated before _dialog.show() is invoked."""
        ext, fake_dialog = self._setup()
        key = "test_key_123"

        # Track order of side-effects
        call_order = []

        def _record_key(*a, **kw):
            call_order.append(("key_set", ext._preselected_node_key))

        def _record_show(*a, **kw):
            call_order.append(("show", ext._preselected_node_key))

        ext.preselectedNodeChanged.emit.side_effect = _record_key
        fake_dialog.show.side_effect = _record_show

        ext.showDialogForNode(key)

        assert ("key_set", key) in call_order
        assert ("show", key) in call_order
        # key must be set before show
        key_pos  = next(i for i, e in enumerate(call_order) if e[0] == "key_set")
        show_pos = next(i for i, e in enumerate(call_order) if e[0] == "show")
        assert key_pos < show_pos

    def test_preselected_node_key_value_after_call(self):
        """After showDialogForNode the internal key must match the argument."""
        ext, _ = self._setup()
        ext.showDialogForNode("abc_node_42")
        assert ext._preselected_node_key == "abc_node_42"

    def test_preselected_node_changed_signal_emitted(self):
        """preselectedNodeChanged.emit() must be called."""
        ext, _ = self._setup()
        ext.showDialogForNode("some_key")
        ext.preselectedNodeChanged.emit.assert_called()

    def test_dialog_show_called(self):
        """_dialog.show() must be invoked."""
        ext, fake_dialog = self._setup()
        ext.showDialogForNode("xyz")
        fake_dialog.show.assert_called_once()


# ===========================================================================
# Test 3: showDialogForNode populates the node cache
# ===========================================================================

class TestShowDialogForNodePopulatesCache:

    def test_node_in_cache_after_show_dialog_for_node(self):
        """After showDialogForNode the target node must be retrievable via _getNodeById."""
        ext = _make_extension()
        node = _FakeCuraSceneNode(name="Bracket", selectable=True, has_mesh=True)
        _make_scene_with_nodes(node)
        ext._dialog = MagicMock(name="dialog")  # skip actual QML creation

        node_key = str(id(node))
        ext.showDialogForNode(node_key)

        retrieved = ext._getNodeById(node_key)
        assert retrieved is node

    def test_get_scene_nodes_result_contains_node(self):
        """getSceneNodes() result list must include the node used in showDialogForNode."""
        ext = _make_extension()
        node = _FakeCuraSceneNode(name="Wheel", selectable=True, has_mesh=True)
        _make_scene_with_nodes(node)
        ext._dialog = MagicMock(name="dialog")

        node_key = str(id(node))
        ext.showDialogForNode(node_key)

        scene_nodes = ext.getSceneNodes()
        ids = [n["id"] for n in scene_nodes]
        assert node_key in ids


# ===========================================================================
# Test 4: getSceneNodes returns correct format
# ===========================================================================

class TestGetSceneNodesFormat:

    def _ext_with_nodes(self, *nodes):
        ext = _make_extension()
        _make_scene_with_nodes(*nodes)
        return ext

    def test_returns_list_of_dicts_with_name_and_id(self):
        node = _FakeCuraSceneNode(name="Cube", selectable=True, has_mesh=True)
        ext = self._ext_with_nodes(node)

        result = ext.getSceneNodes()

        assert isinstance(result, list)
        assert len(result) >= 1
        for entry in result:
            assert "name" in entry
            assert "id" in entry

    def test_id_values_are_strings(self):
        node = _FakeCuraSceneNode(name="Cube", selectable=True, has_mesh=True)
        ext = self._ext_with_nodes(node)

        result = ext.getSceneNodes()

        for entry in result:
            assert isinstance(entry["id"], str), f"id must be str, got {type(entry['id'])}"

    def test_name_matches_node_name(self):
        node = _FakeCuraSceneNode(name="BracketModel", selectable=True, has_mesh=True)
        ext = self._ext_with_nodes(node)

        result = ext.getSceneNodes()

        assert any(e["name"] == "BracketModel" for e in result)

    def test_nodes_with_mesh_data_are_included(self):
        node = _FakeCuraSceneNode(name="WithMesh", selectable=True, has_mesh=True)
        ext = self._ext_with_nodes(node)

        result = ext.getSceneNodes()

        assert any(e["name"] == "WithMesh" for e in result)

    def test_non_printable_nodes_are_excluded(self):
        printable = _FakeCuraSceneNode(name="Printable", selectable=True, has_mesh=True, non_printing=False)
        non_print = _FakeCuraSceneNode(name="NonPrint", selectable=True, has_mesh=True, non_printing=True)
        ext = self._ext_with_nodes(printable, non_print)

        result = ext.getSceneNodes()

        names = [e["name"] for e in result]
        assert "Printable" in names
        assert "NonPrint" not in names

    def test_non_selectable_nodes_are_excluded(self):
        selectable = _FakeCuraSceneNode(name="Selectable", selectable=True, has_mesh=True)
        not_sel    = _FakeCuraSceneNode(name="NotSelectable", selectable=False, has_mesh=True)
        ext = self._ext_with_nodes(selectable, not_sel)

        result = ext.getSceneNodes()

        names = [e["name"] for e in result]
        assert "Selectable" in names
        assert "NotSelectable" not in names

    def test_nodes_without_mesh_data_are_excluded(self):
        with_mesh    = _FakeCuraSceneNode(name="HasMesh", selectable=True, has_mesh=True)
        without_mesh = _FakeCuraSceneNode(name="NoMesh",  selectable=True, has_mesh=False)
        ext = self._ext_with_nodes(with_mesh, without_mesh)

        result = ext.getSceneNodes()

        names = [e["name"] for e in result]
        assert "HasMesh" in names
        assert "NoMesh" not in names

    def test_empty_scene_returns_empty_list(self):
        ext = self._ext_with_nodes()

        result = ext.getSceneNodes()

        assert result == []

    def test_non_cura_scene_nodes_are_excluded(self):
        """Objects that are not _FakeCuraSceneNode instances must be skipped."""
        node = _FakeCuraSceneNode(name="Real", selectable=True, has_mesh=True)
        non_node = MagicMock(name="non_cura_node")  # not a CuraSceneNode
        ext = _make_extension()
        _make_scene_with_nodes(node, non_node)

        result = ext.getSceneNodes()

        names = [e["name"] for e in result]
        assert "Real" in names
        # non_node's name attr would be a MagicMock, not "non_cura_node"
        assert len(result) == 1


# ===========================================================================
# Test 5: preselectedNodeKey property
# ===========================================================================

class TestPreselectedNodeKeyProperty:

    def test_property_returns_empty_string_initially(self):
        ext = _make_extension()
        assert ext.preselectedNodeKey == ""

    def test_property_returns_set_value(self):
        ext = _make_extension()
        ext._preselected_node_key = "abc123"
        assert ext.preselectedNodeKey == "abc123"

    def test_property_returns_str_type(self):
        ext = _make_extension()
        ext._preselected_node_key = "xyz"
        assert isinstance(ext.preselectedNodeKey, str)

    def test_property_reflects_update(self):
        ext = _make_extension()
        ext._preselected_node_key = "first"
        assert ext.preselectedNodeKey == "first"
        ext._preselected_node_key = "second"
        assert ext.preselectedNodeKey == "second"


# ===========================================================================
# Test 6: Dialog retry when first creation returns None
# ===========================================================================

class TestDialogRetryAfterFailedCreation:

    def test_dialog_remains_none_when_create_returns_none(self):
        """If createQmlComponent returns None, _dialog stays None (no spurious object)."""
        ext = _make_extension()
        app_inst = MagicMock(name="app_instance")
        app_inst.createQmlComponent.return_value = None
        _CuraApplication_mock.getInstance.return_value = app_inst

        plugin_reg = MagicMock(name="plugin_registry")
        plugin_reg.getPluginPath.return_value = "/fake/plugin"

        with patch("FEAInfillOptimizer.FEAInfillExtension.PluginRegistry") as mock_reg:
            mock_reg.getInstance.return_value = plugin_reg
            ext._ensureDialog()

        assert ext._dialog is None

    def test_retry_succeeds_when_second_call_returns_valid_object(self):
        """After a None return, the next _ensureDialog call must try again and succeed."""
        ext = _make_extension()
        fake_dialog = MagicMock(name="dialog")
        app_inst = MagicMock(name="app_instance")
        # First call returns None, second returns a real dialog
        app_inst.createQmlComponent.side_effect = [None, fake_dialog]
        _CuraApplication_mock.getInstance.return_value = app_inst

        plugin_reg = MagicMock(name="plugin_registry")
        plugin_reg.getPluginPath.return_value = "/fake/plugin"

        with patch("FEAInfillOptimizer.FEAInfillExtension.PluginRegistry") as mock_reg:
            mock_reg.getInstance.return_value = plugin_reg
            ext._ensureDialog()          # returns None → _dialog stays None
            assert ext._dialog is None

            ext._ensureDialog()          # retries → should now assign the dialog
            assert ext._dialog is fake_dialog

    def test_create_qml_called_twice_across_two_failed_then_success(self):
        """createQmlComponent must be attempted on each call when _dialog is None."""
        ext = _make_extension()
        fake_dialog = MagicMock(name="dialog")
        app_inst = MagicMock(name="app_instance")
        app_inst.createQmlComponent.side_effect = [None, fake_dialog]
        _CuraApplication_mock.getInstance.return_value = app_inst

        plugin_reg = MagicMock(name="plugin_registry")
        plugin_reg.getPluginPath.return_value = "/fake/plugin"

        with patch("FEAInfillOptimizer.FEAInfillExtension.PluginRegistry") as mock_reg:
            mock_reg.getInstance.return_value = plugin_reg
            ext._ensureDialog()
            ext._ensureDialog()

        assert app_inst.createQmlComponent.call_count == 2


# ===========================================================================
# Test 7: BoundaryConditionTool.setOpenOptimizeDialog flow
# ===========================================================================

class TestBoundaryConditionToolOpenOptimizeDialog:

    def _make_tool(self):
        """Build a BoundaryConditionTool with a mock extension and scene."""
        extension = MagicMock(name="extension")
        extension.showDialogForNode = MagicMock()
        extension.showDialog = MagicMock()

        # Wire up the scene/controller for Tool.__init__ via _FakeTool.getController
        controller = MagicMock(name="controller")
        scene = MagicMock(name="scene")
        root = MagicMock(name="root")
        root._nodes = []
        scene.getRoot.return_value = root
        controller.getScene.return_value = scene

        app_inst = MagicMock(name="app_instance")
        app_inst.getController.return_value = controller
        _CuraApplication_mock.getInstance.return_value = app_inst

        # Patch out the visualization handles so __init__ doesn't crash
        with patch("FEAInfillOptimizer.BoundaryConditionTool.BCHighlightHandle"), \
             patch("FEAInfillOptimizer.BoundaryConditionTool.ForceDirectionHandle"), \
             patch("FEAInfillOptimizer.BoundaryConditionTool.Selection") as mock_sel:
            mock_sel.selectionChanged = MagicMock()
            mock_sel.selectionChanged.connect = MagicMock()
            tool = BoundaryConditionTool(extension=extension)
            tool._extension = extension
            tool._selection_mock = mock_sel

        return tool, extension

    def test_show_dialog_for_node_called_with_correct_key(self):
        """When a node is selected, showDialogForNode must receive str(id(node))."""
        tool, extension = self._make_tool()
        node = _FakeCuraSceneNode(name="Selected")

        with patch("FEAInfillOptimizer.BoundaryConditionTool.Selection") as mock_sel:
            mock_sel.getSelectedObject.return_value = node
            tool.setOpenOptimizeDialog(True)

        expected_key = str(id(node))
        extension.showDialogForNode.assert_called_once_with(expected_key)

    def test_show_dialog_fallback_when_no_selection(self):
        """When no node is selected, showDialog (not showDialogForNode) must be called."""
        tool, extension = self._make_tool()

        with patch("FEAInfillOptimizer.BoundaryConditionTool.Selection") as mock_sel:
            mock_sel.getSelectedObject.return_value = None
            tool.setOpenOptimizeDialog(True)

        extension.showDialog.assert_called_once()
        extension.showDialogForNode.assert_not_called()

    def test_no_op_when_value_is_false(self):
        """setOpenOptimizeDialog(False) must not call any dialog method."""
        tool, extension = self._make_tool()

        with patch("FEAInfillOptimizer.BoundaryConditionTool.Selection"):
            tool.setOpenOptimizeDialog(False)

        extension.showDialogForNode.assert_not_called()
        extension.showDialog.assert_not_called()

    def test_no_op_when_no_extension(self):
        """If _extension is None the setter must not raise."""
        tool, _ = self._make_tool()
        tool._extension = None

        with patch("FEAInfillOptimizer.BoundaryConditionTool.Selection") as mock_sel:
            node = _FakeCuraSceneNode(name="X")
            mock_sel.getSelectedObject.return_value = node
            tool.setOpenOptimizeDialog(True)  # must not raise


# ===========================================================================
# Test 8: Node ID stability — _getNodeById round-trip
# ===========================================================================

class TestNodeIdStability:

    def test_get_node_by_id_returns_same_object(self):
        """A node inserted via getSceneNodes must be retrievable by its str(id())."""
        ext = _make_extension()
        node = _FakeCuraSceneNode(name="Gear", selectable=True, has_mesh=True)
        _make_scene_with_nodes(node)

        key = str(id(node))
        ext.getSceneNodes()  # populates _node_cache

        retrieved = ext._getNodeById(key)
        assert retrieved is node

    def test_unknown_key_returns_none(self):
        """A key that was never cached must return None."""
        ext = _make_extension()
        _make_scene_with_nodes()

        assert ext._getNodeById("99999999999") is None

    def test_cache_cleared_on_each_get_scene_nodes_call(self):
        """Calling getSceneNodes() twice replaces the cache with fresh data."""
        ext = _make_extension()
        node_a = _FakeCuraSceneNode(name="A", selectable=True, has_mesh=True)
        _make_scene_with_nodes(node_a)
        ext.getSceneNodes()
        key_a = str(id(node_a))
        assert ext._getNodeById(key_a) is node_a

        # Replace the scene with a different node
        node_b = _FakeCuraSceneNode(name="B", selectable=True, has_mesh=True)
        _make_scene_with_nodes(node_b)
        ext.getSceneNodes()

        key_b = str(id(node_b))
        assert ext._getNodeById(key_b) is node_b

    def test_node_key_is_string_of_python_id(self):
        """The 'id' field returned by getSceneNodes must equal str(id(node))."""
        ext = _make_extension()
        node = _FakeCuraSceneNode(name="Shaft", selectable=True, has_mesh=True)
        _make_scene_with_nodes(node)

        result = ext.getSceneNodes()

        expected_key = str(id(node))
        assert any(e["id"] == expected_key for e in result)
