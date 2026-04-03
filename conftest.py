# Root conftest.py — installs UM/cura stubs before any plugin package is imported.
#
# pytest processes this file before descending into subdirectories, so all
# sys.modules entries are in place before FEAInfillOptimizer/__init__.py
# (which does "from UM.i18n import i18nCatalog") is first imported.

import sys
import numpy as _np
from unittest.mock import MagicMock


def _stub(name: str) -> MagicMock:
    if name not in sys.modules:
        mod = MagicMock(name=name)
        sys.modules[name] = mod
    return sys.modules[name]


_STUBS = [
    "UM",
    "UM.i18n",
    "UM.Math",
    "UM.Math.Vector",
    "UM.Math.Matrix",
    "UM.Scene",
    "UM.Scene.SceneNodeDecorator",
    "UM.Logger",
    "UM.Mesh",
    "UM.Mesh.MeshBuilder",
    "UM.Mesh.MeshData",
    "UM.Signal",
    "UM.PluginObject",
    "UM.PluginRegistry",
    "UM.Application",
    "cura",
    "cura.Scene",
    "cura.Scene.CuraSceneNode",
    "cura.CuraApplication",
]

for _name in _STUBS:
    _stub(_name)

# SceneNodeDecorator: must be a real class (used as base class)
class _FakeSceneNodeDecorator:
    def __init__(self) -> None:
        self._node = None

sys.modules["UM.Scene.SceneNodeDecorator"].SceneNodeDecorator = _FakeSceneNodeDecorator

# Logger
sys.modules["UM.Logger"].Logger = MagicMock()
sys.modules["UM.Logger"].Logger.log = MagicMock()

# i18nCatalog: i18nc / i18n return the raw text string
class _FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass
    def i18nc(self, context: str, text: str, *args) -> str:
        return text
    def i18n(self, text: str, *args) -> str:
        return text

sys.modules["UM.i18n"].i18nCatalog = _FakeCatalog

# MeshData / MeshBuilder: lightweight geometry containers
class _FakeMeshData:
    def __init__(self, vertices=None, indices=None) -> None:
        self._vertices = vertices if vertices is not None else _np.zeros((0, 3), dtype="float32")
        self._indices = indices if indices is not None else _np.zeros((0, 3), dtype="int32")

    def getVertices(self):
        return self._vertices

    def getIndices(self):
        return self._indices


class _FakeMeshBuilder:
    def __init__(self) -> None:
        self._vertices = _np.zeros((0, 3), dtype="float32")
        self._indices = _np.zeros((0, 3), dtype="int32")

    def setVertices(self, v) -> None:
        self._vertices = _np.asarray(v, dtype="float32")

    def setIndices(self, idx) -> None:
        self._indices = _np.asarray(idx, dtype="int32")

    def calculateNormals(self) -> None:
        pass

    def build(self) -> _FakeMeshData:
        return _FakeMeshData(self._vertices, self._indices)


sys.modules["UM.Mesh.MeshBuilder"].MeshBuilder = _FakeMeshBuilder
sys.modules["UM.Mesh.MeshData"].MeshData = _FakeMeshData
sys.modules["UM.Mesh"].MeshBuilder = _FakeMeshBuilder
sys.modules["UM.Mesh"].MeshData = _FakeMeshData
sys.modules["UM"].Mesh = sys.modules["UM.Mesh"]
