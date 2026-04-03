# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Comprehensive unit tests for mesh generation and utility modules.

Modules under test
------------------
- mesh_generation/density_discretizer.py   (pure numpy, no Cura deps)
- mesh_generation/zone_mesh_builder.py     (uses UM.Mesh.MeshBuilder stub)
- mesh_generation/modifier_mesh_creator.py (uses cura.* and UM.* stubs)
- FEABoundaryConditionDecorator.py         (uses UM.Math.Vector, UM.Scene.*)
- deps/dependency_manager.py              (uses UM.Logger stub)

All UM.* / cura.* base stubs are installed by conftest.py before this module
is imported.  This file adds the additional sub-modules that conftest does not
register (cura.Operations.*, UM.Operations.*, UM.Settings.*) and replaces the
MagicMock-registered "cura" entry with proper types.ModuleType objects so that
the Python import machinery can traverse ``cura.Operations.SetParentOperation``.

Run with:
    source .test-venv/bin/activate
    python -m pytest plugins/FEAInfillOptimizer/tests/test_mesh_generation.py -v
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# IMPORTANT: all sys.modules patching MUST happen before any plugin import.
# ---------------------------------------------------------------------------
import os
import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Step 1 – Ensure the plugins directory is on sys.path so that
#          ``import FEAInfillOptimizer.*`` works.
# ---------------------------------------------------------------------------
_PLUGINS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)

# ---------------------------------------------------------------------------
# Step 2 – conftest.py registered "cura" and several sub-paths as MagicMock
#          objects.  MagicMock objects are NOT proper packages: the import
#          machinery cannot descend into them via ``from cura.Operations…``.
#
#          Replace every cura.* and UM.* entry that modifier_mesh_creator /
#          dependency_manager need with real types.ModuleType stubs.  We keep
#          any real class attributes already set by conftest (e.g. the
#          _FakeMeshBuilder class on UM.Mesh.MeshBuilder) by *not* overwriting
#          those keys.
# ---------------------------------------------------------------------------

def _ensure_real_module(name: str) -> types.ModuleType:
    """Return (and register) a real types.ModuleType for *name*.

    If sys.modules[name] is already a real module keep it.
    If it is a MagicMock, replace it with a bare ModuleType (preserving any
    class attributes that conftest already wrote onto it).
    """
    existing = sys.modules.get(name)
    if isinstance(existing, types.ModuleType):
        return existing

    # It's a MagicMock or absent — create a proper module.
    mod = types.ModuleType(name)
    # Copy across any class attributes that conftest may have set.
    if isinstance(existing, MagicMock):
        for attr in list(vars(existing)):
            if not attr.startswith("_"):
                try:
                    setattr(mod, attr, getattr(existing, attr))
                except Exception:
                    pass
    sys.modules[name] = mod
    return mod


def _ensure_parent_chain(dotted: str) -> None:
    """Ensure every prefix of *dotted* is a real module in sys.modules."""
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        _ensure_real_module(".".join(parts[:i]))


# Modules that modifier_mesh_creator.py imports at the top level.
_REQUIRED_REAL_MODULES = [
    "cura",
    "cura.CuraApplication",
    "cura.Operations",
    "cura.Operations.SetParentOperation",
    "cura.Scene",
    "cura.Scene.BuildPlateDecorator",
    "cura.Scene.CuraSceneNode",
    "cura.Scene.SliceableObjectDecorator",
    "UM",
    "UM.Logger",
    "UM.Math",
    "UM.Math.Vector",
    "UM.Mesh",
    "UM.Mesh.MeshBuilder",
    "UM.Mesh.MeshData",
    "UM.Operations",
    "UM.Operations.AddSceneNodeOperation",
    "UM.Operations.GroupedOperation",
    "UM.Scene",
    "UM.Scene.SceneNodeDecorator",
    "UM.Settings",
    "UM.Settings.SettingInstance",
    "UM.i18n",
]

for _name in _REQUIRED_REAL_MODULES:
    _ensure_parent_chain(_name)


# ---------------------------------------------------------------------------
# Step 3 – Attach stub classes to the (now real) module objects.
# ---------------------------------------------------------------------------

# UM.Logger -------------------------------------------------------------------
if not hasattr(sys.modules["UM.Logger"], "Logger"):
    sys.modules["UM.Logger"].Logger = MagicMock(name="Logger")

# UM.i18n --------------------------------------------------------------------
if not hasattr(sys.modules["UM.i18n"], "i18nCatalog"):
    class _FakeCatalog:
        def __init__(self, *a: object, **kw: object) -> None:
            pass
        def i18nc(self, ctx: str, text: str, *a: object) -> str:
            return text
        def i18n(self, text: str, *a: object) -> str:
            return text
    sys.modules["UM.i18n"].i18nCatalog = _FakeCatalog

# UM.Math.Vector -------------------------------------------------------------
# Use a *real* lightweight class so FEABoundaryConditionDecorator round-trips
# and deep-copy can compare field values.
class _Vector:
    """Minimal stand-in for UM.Math.Vector."""
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.x = float(x); self.y = float(y); self.z = float(z)
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _Vector): return NotImplemented
        return self.x == other.x and self.y == other.y and self.z == other.z
    def __repr__(self) -> str:  # pragma: no cover
        return f"_Vector({self.x}, {self.y}, {self.z})"

sys.modules["UM.Math.Vector"].Vector = _Vector

# UM.Scene.SceneNodeDecorator ------------------------------------------------
# Must be a real class so FEABoundaryConditionDecorator can inherit from it.
if not hasattr(sys.modules["UM.Scene.SceneNodeDecorator"], "SceneNodeDecorator"):
    class _FakeDecorator:
        def __init__(self) -> None:
            self._node = None
        def setNode(self, node: object) -> None:
            self._node = node
    sys.modules["UM.Scene.SceneNodeDecorator"].SceneNodeDecorator = _FakeDecorator
else:
    # conftest may have installed a proper class already
    _FakeDecorator = sys.modules["UM.Scene.SceneNodeDecorator"].SceneNodeDecorator  # type: ignore[assignment]

# UM.Mesh.MeshBuilder / MeshData ---------------------------------------------
# conftest installs _FakeMeshBuilder and _FakeMeshData.  If they're already
# present keep them; otherwise create minimal stubs.
import numpy as _np  # noqa: E402 — needed for _FakeMeshBuilder

if not hasattr(sys.modules["UM.Mesh.MeshData"], "MeshData"):
    class _FakeMeshData:
        def __init__(self, vertices=None, indices=None) -> None:
            self._vertices = vertices if vertices is not None else _np.zeros((0, 3), dtype="float32")
            self._indices  = indices  if indices  is not None else _np.zeros((0, 3), dtype="int32")
        def getVertices(self): return self._vertices
        def getIndices(self):  return self._indices
    sys.modules["UM.Mesh.MeshData"].MeshData = _FakeMeshData

if not hasattr(sys.modules["UM.Mesh.MeshBuilder"], "MeshBuilder"):
    class _FakeMeshBuilder:
        def __init__(self) -> None:
            self._v = _np.zeros((0, 3), dtype="float32")
            self._i = _np.zeros((0, 3), dtype="int32")
        def setVertices(self, v) -> None:
            self._v = _np.asarray(v, dtype="float32")
        def setIndices(self, idx) -> None:
            self._i = _np.asarray(idx, dtype="int32")
        def calculateNormals(self) -> None:
            pass
        def build(self):
            return sys.modules["UM.Mesh.MeshData"].MeshData(self._v, self._i)
    sys.modules["UM.Mesh.MeshBuilder"].MeshBuilder = _FakeMeshBuilder

# Grab the live classes for use in tests.
_FakeMeshData    = sys.modules["UM.Mesh.MeshData"].MeshData    # type: ignore[assignment]
_FakeMeshBuilder = sys.modules["UM.Mesh.MeshBuilder"].MeshBuilder  # type: ignore[assignment]

# UM.Operations.* ------------------------------------------------------------
_GroupedOperation_cls      = MagicMock(name="GroupedOperation")
_AddSceneNodeOperation_cls = MagicMock(name="AddSceneNodeOperation")
sys.modules["UM.Operations.GroupedOperation"].GroupedOperation         = _GroupedOperation_cls
sys.modules["UM.Operations.AddSceneNodeOperation"].AddSceneNodeOperation = _AddSceneNodeOperation_cls

# UM.Settings.SettingInstance ------------------------------------------------
class _SettingInstance:
    """Records ``setProperty`` calls so modifier_mesh_creator tests can assert values."""
    def __init__(self, definition: object, container: object) -> None:
        self.definition = definition
        self.container  = container
        self.properties: dict = {}
    def setProperty(self, prop: str, value: object) -> None:
        self.properties[prop] = value
    def resetState(self) -> None:
        pass

sys.modules["UM.Settings.SettingInstance"].SettingInstance = _SettingInstance

# cura.* ----------------------------------------------------------------------
_CuraApplication_cls           = MagicMock(name="CuraApplication")
_SetParentOperation_cls        = MagicMock(name="SetParentOperation")
_BuildPlateDecorator_cls       = MagicMock(name="BuildPlateDecorator")
_CuraSceneNode_cls             = MagicMock(name="CuraSceneNode")
_SliceableObjectDecorator_cls  = MagicMock(name="SliceableObjectDecorator")

sys.modules["cura.CuraApplication"].CuraApplication                       = _CuraApplication_cls
sys.modules["cura.Operations.SetParentOperation"].SetParentOperation       = _SetParentOperation_cls
sys.modules["cura.Scene.BuildPlateDecorator"].BuildPlateDecorator          = _BuildPlateDecorator_cls
sys.modules["cura.Scene.CuraSceneNode"].CuraSceneNode                      = _CuraSceneNode_cls
sys.modules["cura.Scene.SliceableObjectDecorator"].SliceableObjectDecorator = _SliceableObjectDecorator_cls

# ---------------------------------------------------------------------------
# Step 4 – Now safe to import plugin modules.
# ---------------------------------------------------------------------------
import importlib as _importlib  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from copy import deepcopy  # noqa: E402
from typing import Any  # noqa: E402

from FEAInfillOptimizer.mesh_generation.density_discretizer import (  # noqa: E402
    Zone,
    discretize_density,
)
from FEAInfillOptimizer.mesh_generation.zone_mesh_builder import (  # noqa: E402
    build_zone_mesh,
)
from FEAInfillOptimizer.mesh_generation.modifier_mesh_creator import (  # noqa: E402
    create_all_modifier_meshes,
)
from FEAInfillOptimizer.FEABoundaryConditionDecorator import (  # noqa: E402
    FEABoundaryConditionDecorator,
    ForceGroup,
)
from FEAInfillOptimizer.deps.dependency_manager import (  # noqa: E402
    DependencyManager,
    REQUIRED_PACKAGES,
)
from FEAInfillOptimizer.fea.tetrahedralization import TetMesh  # noqa: E402


# ===========================================================================
# Shared geometry fixtures
# ===========================================================================

def _make_two_tet_mesh() -> TetMesh:
    """5-node, 2-element TetMesh where tets share face {1, 2, 3}.

    Tet 0: nodes [0, 1, 2, 3]
    Tet 1: nodes [1, 2, 3, 4]
    Shared face: sorted nodes (1, 2, 3).
    """
    nodes = np.array(
        [
            [0.0, 0.0, 0.0],  # 0
            [1.0, 0.0, 0.0],  # 1
            [0.0, 1.0, 0.0],  # 2
            [0.0, 0.0, 1.0],  # 3
            [1.0, 1.0, 1.0],  # 4
        ],
        dtype=np.float64,
    )
    elements = np.array([[0, 1, 2, 3], [1, 2, 3, 4]], dtype=np.int64)
    return TetMesh(nodes=nodes, elements=elements)


def _make_single_tet_mesh() -> TetMesh:
    """4-node, 1-element TetMesh — a lone tetrahedron."""
    nodes = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    elements = np.array([[0, 1, 2, 3]], dtype=np.int64)
    return TetMesh(nodes=nodes, elements=elements)


@pytest.fixture()
def two_tet_mesh() -> TetMesh:
    return _make_two_tet_mesh()


@pytest.fixture()
def single_tet_mesh() -> TetMesh:
    return _make_single_tet_mesh()


@pytest.fixture()
def decorator() -> FEABoundaryConditionDecorator:
    return FEABoundaryConditionDecorator()


# ===========================================================================
# 1. density_discretizer.py
# ===========================================================================

class TestDiscretizeDensity:
    """Unit tests for ``discretize_density``."""

    # ------------------------------------------------------------------
    # Basic binning
    # ------------------------------------------------------------------

    def test_three_zones_correct_bin_assignment(self) -> None:
        """Elements with well-separated densities land in three distinct bins.

        n_zones=3, rho=[0,1] → midpoints [1/6, 3/6, 5/6].
        """
        densities = np.array([0.1, 0.5, 0.9], dtype=np.float64)

        zones = discretize_density(densities, n_zones=3, rho_min=0.0, rho_max=1.0)

        assert len(zones) == 3
        assert zones[0].element_indices == [0]   # lowest density → first bin
        assert zones[1].element_indices == [1]   # middle density → second bin
        assert zones[2].element_indices == [2]   # highest density → third bin

    def test_zones_sorted_by_ascending_density(self) -> None:
        densities = np.array([0.9, 0.1, 0.5])

        zones = discretize_density(densities, n_zones=3, rho_min=0.0, rho_max=1.0)

        out_densities = [z.density for z in zones]
        assert out_densities == sorted(out_densities)

    def test_zone_density_equals_bin_midpoint(self) -> None:
        """Zone density is the bin midpoint (not the element's raw value).

        n_zones=2 → edges [0, 0.5, 1], midpoints [0.25, 0.75].
        """
        densities = np.array([0.1, 0.8])

        zones = discretize_density(densities, n_zones=2, rho_min=0.0, rho_max=1.0)

        assert len(zones) == 2
        assert abs(zones[0].density - 0.25) < 1e-10
        assert abs(zones[1].density - 0.75) < 1e-10

    def test_element_indices_cover_every_input_element(self) -> None:
        """Union of all zone element_indices == range(n_elements)."""
        n = 50
        rng = np.random.default_rng(42)
        densities = rng.uniform(0.1, 0.9, n)

        zones = discretize_density(densities, n_zones=5, rho_min=0.0, rho_max=1.0)

        all_indices = sorted(idx for z in zones for idx in z.element_indices)
        assert all_indices == list(range(n))

    # ------------------------------------------------------------------
    # All same density → single zone
    # ------------------------------------------------------------------

    def test_uniform_density_produces_one_zone(self) -> None:
        densities = np.full(10, 0.5)

        zones = discretize_density(densities, n_zones=5, rho_min=0.0, rho_max=1.0)

        assert len(zones) == 1
        assert len(zones[0].element_indices) == 10

    # ------------------------------------------------------------------
    # Boundary values
    # ------------------------------------------------------------------

    def test_element_at_rho_min_assigned_to_lowest_bin(self) -> None:
        densities = np.array([0.0])

        zones = discretize_density(densities, n_zones=4, rho_min=0.0, rho_max=1.0)

        assert len(zones) == 1
        assert zones[0].element_indices == [0]
        assert zones[0].density < 0.5  # first-bin midpoint < midpoint of range

    def test_element_at_rho_max_assigned_to_highest_bin(self) -> None:
        densities = np.array([1.0])

        zones = discretize_density(densities, n_zones=4, rho_min=0.0, rho_max=1.0)

        assert len(zones) == 1
        assert zones[0].element_indices == [0]
        assert zones[0].density > 0.5

    def test_below_rho_min_clamped_and_assigned(self) -> None:
        """Elements below rho_min are clamped to rho_min before binning."""
        densities = np.array([-999.0, 0.5])

        zones = discretize_density(densities, n_zones=2, rho_min=0.0, rho_max=1.0)

        total = sum(len(z.element_indices) for z in zones)
        assert total == 2

    def test_above_rho_max_clamped_and_assigned(self) -> None:
        """Elements above rho_max are clamped to rho_max before binning."""
        densities = np.array([999.0, 0.1])

        zones = discretize_density(densities, n_zones=2, rho_min=0.0, rho_max=1.0)

        total = sum(len(z.element_indices) for z in zones)
        assert total == 2

    # ------------------------------------------------------------------
    # Empty bins omitted
    # ------------------------------------------------------------------

    def test_sparse_distribution_omits_empty_middle_bin(self) -> None:
        """Bins with no elements are absent from the result.

        Densities near the extremes of a 3-bin range leave the middle bin empty.
        """
        densities = np.array([0.05, 0.95])

        zones = discretize_density(densities, n_zones=3, rho_min=0.0, rho_max=1.0)

        assert len(zones) == 2
        for z in zones:
            assert abs(z.density - 0.5) > 0.1, (
                f"Unexpected middle-bin zone with density {z.density}"
            )

    # ------------------------------------------------------------------
    # Single element
    # ------------------------------------------------------------------

    def test_single_element_produces_one_zone(self) -> None:
        densities = np.array([0.42])

        zones = discretize_density(densities, n_zones=5, rho_min=0.0, rho_max=1.0)

        assert len(zones) == 1
        assert zones[0].element_indices == [0]

    # ------------------------------------------------------------------
    # n_zones=1
    # ------------------------------------------------------------------

    def test_n_zones_one_all_elements_in_one_zone(self) -> None:
        """n_zones=1 assigns every element to the single bin (midpoint = 0.5)."""
        densities = np.linspace(0.1, 0.9, 20)

        zones = discretize_density(densities, n_zones=1, rho_min=0.0, rho_max=1.0)

        assert len(zones) == 1
        assert len(zones[0].element_indices) == 20
        assert abs(zones[0].density - 0.5) < 1e-10

    # ------------------------------------------------------------------
    # Non-unit range
    # ------------------------------------------------------------------

    def test_non_unit_range_correct_assignment(self) -> None:
        densities = np.array([0.3, 0.5, 0.7])

        zones = discretize_density(densities, n_zones=3, rho_min=0.3, rho_max=0.7)

        total = sum(len(z.element_indices) for z in zones)
        assert total == 3

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_n_zones_zero_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="n_zones must be >= 1"):
            discretize_density(np.array([0.5]), n_zones=0, rho_min=0.0, rho_max=1.0)

    def test_rho_max_equal_rho_min_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="rho_max must be greater than rho_min"):
            discretize_density(np.array([0.5]), n_zones=3, rho_min=0.5, rho_max=0.5)

    def test_rho_max_less_than_rho_min_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            discretize_density(np.array([0.5]), n_zones=3, rho_min=1.0, rho_max=0.0)

    def test_no_duplicate_element_indices_across_zones(self) -> None:
        """Each element index must appear in exactly one zone."""
        n = 30
        rng = np.random.default_rng(7)
        densities = rng.uniform(0.0, 1.0, n)

        zones = discretize_density(densities, n_zones=4, rho_min=0.0, rho_max=1.0)

        all_indices = [idx for z in zones for idx in z.element_indices]
        assert len(all_indices) == len(set(all_indices))


# ===========================================================================
# 2. zone_mesh_builder.py
# ===========================================================================

class TestBuildZoneMesh:
    """Unit tests for ``build_zone_mesh``.

    The UM.Mesh.MeshBuilder stub (conftest or our fallback) calls
    ``MeshBuilder.build()`` which returns a ``_FakeMeshData`` with
    ``getVertices()`` and ``getIndices()``.
    """

    # ------------------------------------------------------------------
    # Single tet
    # ------------------------------------------------------------------

    def test_single_tet_has_four_boundary_faces(
        self, single_tet_mesh: TetMesh
    ) -> None:
        result = build_zone_mesh(single_tet_mesh, [0])

        assert isinstance(result, _FakeMeshData)
        assert result.getIndices().shape == (4, 3)

    def test_single_tet_has_four_unique_vertices(
        self, single_tet_mesh: TetMesh
    ) -> None:
        result = build_zone_mesh(single_tet_mesh, [0])
        assert result.getVertices().shape[0] == 4

    def test_single_tet_vertex_positions_match_mesh_nodes(
        self, single_tet_mesh: TetMesh
    ) -> None:
        """Output vertex positions match the TetMesh node coordinates."""
        result = build_zone_mesh(single_tet_mesh, [0])

        verts = result.getVertices()
        for node in single_tet_mesh.nodes:
            node_f32 = node.astype(np.float32)
            found = any(np.allclose(node_f32, verts[i]) for i in range(len(verts)))
            assert found, f"Node {node} not found in vertex array"

    def test_single_tet_vertex_dtype_is_float32(
        self, single_tet_mesh: TetMesh
    ) -> None:
        result = build_zone_mesh(single_tet_mesh, [0])
        assert result.getVertices().dtype == np.float32

    def test_single_tet_index_dtype_is_int32(
        self, single_tet_mesh: TetMesh
    ) -> None:
        result = build_zone_mesh(single_tet_mesh, [0])
        assert result.getIndices().dtype == np.int32

    # ------------------------------------------------------------------
    # Two-tet mesh
    # ------------------------------------------------------------------

    def test_both_tets_selected_shared_face_excluded(
        self, two_tet_mesh: TetMesh
    ) -> None:
        """Shared face {1,2,3} appears twice → excluded → 6 boundary faces."""
        result = build_zone_mesh(two_tet_mesh, [0, 1])

        assert result.getIndices().shape == (6, 3)

    def test_single_tet_from_two_tet_mesh_has_four_faces(
        self, two_tet_mesh: TetMesh
    ) -> None:
        """Selecting only tet 0 from a two-tet mesh: its 4 faces are all boundary."""
        result = build_zone_mesh(two_tet_mesh, [0])

        assert result.getIndices().shape == (4, 3)

    def test_all_tets_selected_outer_surface_only(
        self, two_tet_mesh: TetMesh
    ) -> None:
        """Full mesh selected: boundary = outer surface = 6 faces."""
        all_idx = list(range(len(two_tet_mesh.elements)))

        result = build_zone_mesh(two_tet_mesh, all_idx)

        assert result.getIndices().shape[0] == 6

    def test_indices_reference_valid_vertices(
        self, two_tet_mesh: TetMesh
    ) -> None:
        result = build_zone_mesh(two_tet_mesh, [0, 1])

        verts = result.getVertices()
        idx   = result.getIndices()
        assert int(idx.min()) >= 0
        assert int(idx.max()) < len(verts)

    # ------------------------------------------------------------------
    # Empty element list
    # ------------------------------------------------------------------

    def test_empty_element_list_produces_empty_mesh(
        self, single_tet_mesh: TetMesh
    ) -> None:
        result = build_zone_mesh(single_tet_mesh, [])

        assert result.getVertices().shape == (0, 3)
        assert result.getIndices().shape  == (0, 3)

    # ------------------------------------------------------------------
    # Six-tet unit cube
    # ------------------------------------------------------------------

    @staticmethod
    def _make_cube_tet_mesh() -> tuple[TetMesh, list[int]]:
        """Unit cube decomposed into 6 tetrahedra (Freudenthal).

        Nodes:
            0=(0,0,0), 1=(1,0,0), 2=(0,1,0), 3=(1,1,0)
            4=(0,0,1), 5=(1,0,1), 6=(0,1,1), 7=(1,1,1)
        """
        nodes = np.array(
            [
                [0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
                [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1],
            ],
            dtype=np.float64,
        )
        elements = np.array(
            [
                [0, 1, 3, 7], [0, 3, 2, 7], [0, 2, 6, 7],
                [0, 6, 4, 7], [0, 4, 5, 7], [0, 5, 1, 7],
            ],
            dtype=np.int64,
        )
        return TetMesh(nodes=nodes, elements=elements), list(range(6))

    def test_cube_all_tets_have_non_empty_surface(self) -> None:
        mesh, all_idx = self._make_cube_tet_mesh()

        result = build_zone_mesh(mesh, all_idx)

        assert result.getVertices().shape[0] > 0
        assert result.getIndices().shape[0]  > 0

    def test_cube_all_tets_vertices_within_unit_cube(self) -> None:
        """All output vertex coordinates lie within [0, 1]^3."""
        mesh, all_idx = self._make_cube_tet_mesh()

        result = build_zone_mesh(mesh, all_idx)

        verts = result.getVertices()
        assert np.all(verts >= -1e-6)
        assert np.all(verts <= 1.0 + 1e-6)


# ===========================================================================
# 3. modifier_mesh_creator.py
# ===========================================================================

class TestCreateAllModifierMeshes:
    """Unit tests for ``create_all_modifier_meshes``."""

    # ------------------------------------------------------------------
    # Internal helpers (called from each test, no state leak)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_app_graph(
        recorded: list[_SettingInstance] | None = None,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Wire up app/controller/scene mocks and return (app, grouped_op, scene)."""
        scene = MagicMock(name="scene")
        scene.sceneChanged = MagicMock()

        controller = MagicMock(name="controller")
        controller.getScene.return_value = scene

        bp_model = MagicMock(name="bp_model")
        bp_model.activeBuildPlate = 0

        app = MagicMock(name="app")
        app.getController.return_value = controller
        app.getMultiBuildPlateModel.return_value = bp_model

        _CuraApplication_cls.getInstance.return_value = app

        # CuraSceneNode factory
        def _make_node(*_a: Any, **_kw: Any) -> MagicMock:
            node  = MagicMock(name="node")
            stack = MagicMock(name="stack")
            top   = MagicMock(name="top")

            if recorded is not None:
                top.addInstance.side_effect = recorded.append
            else:
                top.addInstance.return_value = None

            stack.getTop.return_value = top
            stack.getSettingDefinition.side_effect = (
                lambda k: MagicMock(name=f"def_{k}")
            )
            node.callDecoration.return_value = stack
            return node

        _CuraSceneNode_cls.reset_mock()
        _CuraSceneNode_cls.side_effect = _make_node

        # GroupedOperation
        grouped_op = MagicMock(name="grouped_op")
        _GroupedOperation_cls.reset_mock()
        _GroupedOperation_cls.return_value = grouped_op

        return app, grouped_op, scene

    @staticmethod
    def _zones(n: int) -> list[dict]:
        return [
            {"density": (i + 1) / (n + 1), "mesh_data": MagicMock(name=f"md_{i}")}
            for i in range(n)
        ]

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_correct_number_of_nodes_created(self) -> None:
        self._build_app_graph()
        parent = MagicMock(name="parent")

        create_all_modifier_meshes(parent, self._zones(3))

        assert _CuraSceneNode_cls.call_count == 3

    def test_grouped_operation_push_called_exactly_once(self) -> None:
        _, grouped_op, _ = self._build_app_graph()
        parent = MagicMock(name="parent")

        create_all_modifier_meshes(parent, self._zones(5))

        grouped_op.push.assert_called_once()

    def test_scene_changed_emitted_with_parent_node(self) -> None:
        _, _, scene = self._build_app_graph()
        parent = MagicMock(name="parent")

        create_all_modifier_meshes(parent, self._zones(2))

        scene.sceneChanged.emit.assert_called_once_with(parent)

    def test_add_operation_called_twice_per_zone(self) -> None:
        """addOperation: once for AddSceneNodeOperation + once for SetParentOperation."""
        _, grouped_op, _ = self._build_app_graph()
        parent = MagicMock(name="parent")
        n = 3

        create_all_modifier_meshes(parent, self._zones(n))

        assert grouped_op.addOperation.call_count == n * 2

    def test_infill_sparse_density_set_to_density_percent(self) -> None:
        """infill_sparse_density must be set to density × 100."""
        recorded: list[_SettingInstance] = []
        self._build_app_graph(recorded=recorded)
        parent = MagicMock(name="parent")
        target = 0.42

        create_all_modifier_meshes(parent, [{"density": target, "mesh_data": MagicMock()}])

        expected = target * 100.0
        matching = [
            inst for inst in recorded
            if isinstance(inst, _SettingInstance) and inst.properties.get("value") == expected
        ]
        assert len(matching) >= 1, (
            f"No SettingInstance with value={expected}; "
            f"got: {[i.properties for i in recorded if isinstance(i, _SettingInstance)]}"
        )

    def test_default_infill_pattern_is_gyroid(self) -> None:
        recorded: list[_SettingInstance] = []
        self._build_app_graph(recorded=recorded)
        parent = MagicMock(name="parent")

        create_all_modifier_meshes(parent, [{"density": 0.5, "mesh_data": MagicMock()}])

        gyroid = [
            inst for inst in recorded
            if isinstance(inst, _SettingInstance) and inst.properties.get("value") == "gyroid"
        ]
        assert len(gyroid) >= 1

    def test_custom_infill_pattern_propagated(self) -> None:
        recorded: list[_SettingInstance] = []
        self._build_app_graph(recorded=recorded)
        parent = MagicMock(name="parent")

        create_all_modifier_meshes(
            parent,
            [{"density": 0.3, "mesh_data": MagicMock()}],
            infill_pattern="triangles",
        )

        tri = [
            inst for inst in recorded
            if isinstance(inst, _SettingInstance) and inst.properties.get("value") == "triangles"
        ]
        assert len(tri) >= 1

    def test_empty_zones_list_creates_no_nodes_still_pushes(self) -> None:
        _, grouped_op, _ = self._build_app_graph()
        parent = MagicMock(name="parent")

        create_all_modifier_meshes(parent, [])

        assert _CuraSceneNode_cls.call_count == 0
        grouped_op.push.assert_called_once()

    def test_add_scene_node_operation_called_per_zone(self) -> None:
        self._build_app_graph()
        parent = MagicMock(name="parent")
        _AddSceneNodeOperation_cls.reset_mock()
        n = 4

        create_all_modifier_meshes(parent, self._zones(n))

        assert _AddSceneNodeOperation_cls.call_count == n

    def test_set_parent_operation_called_per_zone(self) -> None:
        self._build_app_graph()
        parent = MagicMock(name="parent")
        _SetParentOperation_cls.reset_mock()
        n = 2

        create_all_modifier_meshes(parent, self._zones(n))

        assert _SetParentOperation_cls.call_count == n

    def test_scene_changed_emitted_exactly_once_for_many_zones(self) -> None:
        _, _, scene = self._build_app_graph()
        parent = MagicMock(name="parent")

        create_all_modifier_meshes(parent, self._zones(10))

        scene.sceneChanged.emit.assert_called_once()


# ===========================================================================
# 4. FEABoundaryConditionDecorator.py
# ===========================================================================

class TestFEABoundaryConditionDecorator:
    """Unit tests for ``FEABoundaryConditionDecorator`` and ``ForceGroup``."""

    # ------------------------------------------------------------------
    # Fixed faces
    # ------------------------------------------------------------------

    def test_initial_fixed_faces_empty(self, decorator: FEABoundaryConditionDecorator) -> None:
        assert decorator.getFixedFaces() == []

    def test_set_fixed_faces(self, decorator: FEABoundaryConditionDecorator) -> None:
        decorator.setFixedFaces([1, 2, 3])
        assert decorator.getFixedFaces() == [1, 2, 3]

    def test_set_fixed_faces_replaces_previous(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1, 2])
        decorator.setFixedFaces([9, 10, 11])
        assert decorator.getFixedFaces() == [9, 10, 11]

    def test_add_fixed_faces_appends_new_indices(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1, 2])
        decorator.addFixedFaces([3, 4])
        assert sorted(decorator.getFixedFaces()) == [1, 2, 3, 4]

    def test_add_fixed_faces_no_duplicates(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1, 2, 3])
        decorator.addFixedFaces([2, 3, 4])
        assert sorted(decorator.getFixedFaces()) == [1, 2, 3, 4]

    def test_remove_fixed_faces(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1, 2, 3, 4])
        decorator.removeFixedFaces([2, 4])
        assert sorted(decorator.getFixedFaces()) == [1, 3]

    def test_remove_nonexistent_fixed_faces_noop(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1, 2])
        decorator.removeFixedFaces([99])
        assert sorted(decorator.getFixedFaces()) == [1, 2]

    def test_clear_fixed_faces(self, decorator: FEABoundaryConditionDecorator) -> None:
        decorator.setFixedFaces([1, 2, 3])
        decorator.clearFixedFaces()
        assert decorator.getFixedFaces() == []

    def test_get_fixed_face_count(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([10, 20, 30])
        assert decorator.getFixedFaceCount() == 3

    # ------------------------------------------------------------------
    # Force groups
    # ------------------------------------------------------------------

    def test_initial_force_groups_empty(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        assert decorator.getForceGroups() == []

    def test_add_force_group(self, decorator: FEABoundaryConditionDecorator) -> None:
        v = _Vector(1.0, 0.0, 0.0)
        decorator.addForceGroup([5, 6], v)

        groups = decorator.getForceGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [5, 6]
        assert groups[0].force == v

    def test_add_multiple_force_groups(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.addForceGroup([1], _Vector(1.0, 0.0, 0.0))
        decorator.addForceGroup([2], _Vector(0.0, 1.0, 0.0))
        assert len(decorator.getForceGroups()) == 2

    def test_remove_force_group_by_index(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.addForceGroup([1], _Vector(1.0, 0.0, 0.0))
        decorator.addForceGroup([2], _Vector(0.0, 1.0, 0.0))
        decorator.removeForceGroup(0)

        groups = decorator.getForceGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [2]

    def test_remove_force_group_out_of_range_noop(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.addForceGroup([1], _Vector(1.0, 0.0, 0.0))
        decorator.removeForceGroup(99)
        assert len(decorator.getForceGroups()) == 1

    def test_clear_force_groups(self, decorator: FEABoundaryConditionDecorator) -> None:
        decorator.addForceGroup([1], _Vector(1.0, 0.0, 0.0))
        decorator.clearForceGroups()
        assert decorator.getForceGroups() == []

    def test_get_force_group_count(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.addForceGroup([1], _Vector(0.0, 0.0, -9.81))
        decorator.addForceGroup([2, 3], _Vector(0.0, 0.0, -9.81))
        assert decorator.getForceGroupCount() == 2

    # ------------------------------------------------------------------
    # hasAnyBC
    # ------------------------------------------------------------------

    def test_has_any_bc_false_when_empty(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        assert decorator.hasAnyBC() is False

    def test_has_any_bc_true_with_fixed_faces(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([0])
        assert decorator.hasAnyBC() is True

    def test_has_any_bc_true_with_force_groups(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.addForceGroup([0], _Vector(1.0, 0.0, 0.0))
        assert decorator.hasAnyBC() is True

    def test_has_any_bc_false_after_clear_all(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1])
        decorator.addForceGroup([2], _Vector(0.0, 0.0, -1.0))
        decorator.clearAll()
        assert decorator.hasAnyBC() is False

    # ------------------------------------------------------------------
    # clearAll
    # ------------------------------------------------------------------

    def test_clear_all_resets_everything(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1, 2])
        decorator.addForceGroup([3], _Vector(1.0, 0.0, 0.0))
        decorator.setMaterialName("PLA")

        decorator.clearAll()

        assert decorator.getFixedFaces() == []
        assert decorator.getForceGroups() == []
        assert decorator.getMaterialName() is None

    # ------------------------------------------------------------------
    # Material name
    # ------------------------------------------------------------------

    def test_set_and_get_material_name(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setMaterialName("PETG")
        assert decorator.getMaterialName() == "PETG"

    def test_material_name_default_none(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        assert decorator.getMaterialName() is None

    # ------------------------------------------------------------------
    # getBoundaryConditions returns self
    # ------------------------------------------------------------------

    def test_get_boundary_conditions_returns_self(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        assert decorator.getBoundaryConditions() is decorator

    # ------------------------------------------------------------------
    # Serialisation round-trip
    # ------------------------------------------------------------------

    def test_to_dict_has_expected_keys(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        assert set(decorator.toDict().keys()) == {"fixed_faces", "force_groups", "material_name"}

    def test_round_trip_empty_decorator(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        restored = FEABoundaryConditionDecorator()
        restored.fromDict(decorator.toDict())

        assert restored.getFixedFaces() == []
        assert restored.getForceGroups() == []
        assert restored.getMaterialName() is None

    def test_round_trip_with_all_data(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1, 2, 3])
        decorator.addForceGroup([4, 5], _Vector(0.0, 0.0, -9.81))
        decorator.setMaterialName("ABS")

        restored = FEABoundaryConditionDecorator()
        restored.fromDict(decorator.toDict())

        assert restored.getFixedFaces() == [1, 2, 3]
        assert restored.getMaterialName() == "ABS"
        groups = restored.getForceGroups()
        assert len(groups) == 1
        assert groups[0].face_indices == [4, 5]
        assert abs(groups[0].force.z - (-9.81)) < 1e-9

    def test_round_trip_multiple_force_groups(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.fromDict(
            {
                "fixed_faces": [10, 20],
                "force_groups": [
                    {"face_indices": [1], "force": [1.0, 0.0, 0.0]},
                    {"face_indices": [2, 3], "force": [0.0, -5.0, 0.0]},
                ],
                "material_name": "TPU",
            }
        )

        assert decorator.getFixedFaces() == [10, 20]
        assert decorator.getMaterialName() == "TPU"
        groups = decorator.getForceGroups()
        assert len(groups) == 2
        assert groups[0].force == _Vector(1.0, 0.0, 0.0)
        assert groups[1].force == _Vector(0.0, -5.0, 0.0)

    def test_from_dict_missing_keys_use_defaults(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        """fromDict with empty dict must not raise; all fields default."""
        decorator.fromDict({})

        assert decorator.getFixedFaces() == []
        assert decorator.getForceGroups() == []
        assert decorator.getMaterialName() is None

    # ------------------------------------------------------------------
    # Deep copy
    # ------------------------------------------------------------------

    def test_deep_copy_creates_independent_copy(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        decorator.setFixedFaces([1, 2])
        decorator.addForceGroup([3], _Vector(1.0, 0.0, 0.0))
        decorator.setMaterialName("PLA")

        copy = deepcopy(decorator)

        # Mutate original — copy must be unaffected.
        decorator.setFixedFaces([99])
        decorator.clearForceGroups()
        decorator.setMaterialName("ABS")

        assert copy.getFixedFaces() == [1, 2]
        assert len(copy.getForceGroups()) == 1
        assert copy.getMaterialName() == "PLA"

    def test_deep_copy_force_vectors_independent(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        v = _Vector(1.0, 0.0, 0.0)
        decorator.addForceGroup([1], v)

        copy = deepcopy(decorator)
        decorator.getForceGroups()[0].force.x = 999.0

        assert copy.getForceGroups()[0].force.x == 1.0

    def test_deep_copy_node_is_none(
        self, decorator: FEABoundaryConditionDecorator
    ) -> None:
        copy = deepcopy(decorator)
        assert copy._node is None

    # ------------------------------------------------------------------
    # ForceGroup serialisation
    # ------------------------------------------------------------------

    def test_force_group_to_dict(self) -> None:
        fg = ForceGroup([10, 20], _Vector(1.0, 2.0, 3.0))
        d  = fg.to_dict()

        assert d["face_indices"] == [10, 20]
        assert d["force"] == [1.0, 2.0, 3.0]

    def test_force_group_from_dict(self) -> None:
        fg = ForceGroup.from_dict({"face_indices": [5, 6, 7], "force": [0.0, -9.81, 0.0]})

        assert fg.face_indices == [5, 6, 7]
        assert abs(fg.force.y - (-9.81)) < 1e-9

    def test_force_group_face_indices_are_copied(self) -> None:
        """ForceGroup stores an independent copy of the face_indices list."""
        src = [1, 2, 3]
        fg  = ForceGroup(src, _Vector(0.0, 0.0, -1.0))
        src.append(99)

        assert 99 not in fg.face_indices


# ===========================================================================
# 5. deps/dependency_manager.py
# ===========================================================================

class TestDependencyManager:
    """Unit tests for ``DependencyManager``."""

    @pytest.fixture()
    def tmp_plugin_path(self, tmp_path: Any) -> str:
        return str(tmp_path)

    @pytest.fixture()
    def manager(self, tmp_plugin_path: str) -> DependencyManager:
        return DependencyManager(tmp_plugin_path)

    # ------------------------------------------------------------------
    # check_all
    # ------------------------------------------------------------------

    def test_check_all_returns_all_required_keys(
        self, manager: DependencyManager
    ) -> None:
        assert set(manager.check_all().keys()) == set(REQUIRED_PACKAGES.keys())

    def test_check_all_values_are_bool(self, manager: DependencyManager) -> None:
        for k, v in manager.check_all().items():
            assert isinstance(v, bool), f"Expected bool for {k}, got {type(v)}"

    def test_check_all_scipy_available(self, manager: DependencyManager) -> None:
        """scipy is in the test venv → must report True.

        Assumption: the .test-venv has scipy installed (used by the fea modules).
        """
        assert manager.check_all()["scipy"] is True

    def test_check_all_all_missing_when_find_spec_returns_none(
        self, manager: DependencyManager
    ) -> None:
        with patch("importlib.util.find_spec", return_value=None):
            result = manager.check_all()
        assert all(not v for v in result.values())

    def test_check_all_all_present_when_find_spec_returns_spec(
        self, manager: DependencyManager
    ) -> None:
        fake_spec = MagicMock(name="spec")
        with patch("importlib.util.find_spec", return_value=fake_spec):
            result = manager.check_all()
        assert all(v for v in result.values())

    # ------------------------------------------------------------------
    # all_available
    # ------------------------------------------------------------------

    def test_all_available_true_when_all_present(
        self, manager: DependencyManager
    ) -> None:
        with patch.object(manager, "check_all", return_value={k: True for k in REQUIRED_PACKAGES}):
            assert manager.all_available() is True

    def test_all_available_false_when_one_missing(
        self, manager: DependencyManager
    ) -> None:
        status = {k: True for k in REQUIRED_PACKAGES}
        status[next(iter(REQUIRED_PACKAGES))] = False
        with patch.object(manager, "check_all", return_value=status):
            assert manager.all_available() is False

    # ------------------------------------------------------------------
    # missing_packages
    # ------------------------------------------------------------------

    def test_missing_packages_empty_when_all_available(
        self, manager: DependencyManager
    ) -> None:
        with patch.object(manager, "check_all", return_value={k: True for k in REQUIRED_PACKAGES}):
            assert manager.missing_packages() == []

    def test_missing_packages_returns_only_missing(
        self, manager: DependencyManager
    ) -> None:
        status = {k: True for k in REQUIRED_PACKAGES}
        status["trimesh"] = False
        status["gmsh"]    = False
        with patch.object(manager, "check_all", return_value=status):
            missing = manager.missing_packages()

        assert set(missing) == {"trimesh", "gmsh"}
        assert "scipy" not in missing

    def test_missing_packages_all_missing(self, manager: DependencyManager) -> None:
        with patch.object(manager, "check_all", return_value={k: False for k in REQUIRED_PACKAGES}):
            assert set(manager.missing_packages()) == set(REQUIRED_PACKAGES.keys())

    # ------------------------------------------------------------------
    # get_install_command
    # ------------------------------------------------------------------

    def test_install_command_empty_when_nothing_missing(
        self, manager: DependencyManager
    ) -> None:
        with patch.object(manager, "missing_packages", return_value=[]):
            assert manager.get_install_command() == []

    def test_install_command_contains_missing_packages(
        self, manager: DependencyManager
    ) -> None:
        with patch.object(manager, "missing_packages", return_value=["trimesh", "gmsh"]):
            cmd = manager.get_install_command()

        assert sys.executable in cmd
        assert "pip"     in cmd
        assert "install" in cmd
        assert "trimesh" in cmd
        assert "gmsh"    in cmd

    def test_install_command_target_is_vendor_dir(
        self, manager: DependencyManager
    ) -> None:
        with patch.object(manager, "missing_packages", return_value=["trimesh"]):
            cmd = manager.get_install_command()

        idx = cmd.index("--target")
        assert cmd[idx + 1] == manager.get_vendor_dir()

    def test_install_command_includes_upgrade_flag(
        self, manager: DependencyManager
    ) -> None:
        with patch.object(manager, "missing_packages", return_value=["gmsh"]):
            assert "--upgrade" in manager.get_install_command()

    def test_install_command_starts_with_sys_executable(
        self, manager: DependencyManager
    ) -> None:
        with patch.object(manager, "missing_packages", return_value=["trimesh"]):
            cmd = manager.get_install_command()
        assert cmd[0] == sys.executable

    def test_install_command_empty_in_frozen_env(
        self, manager: DependencyManager
    ) -> None:
        with patch(
            "FEAInfillOptimizer.deps.dependency_manager._is_frozen",
            return_value=True,
        ):
            assert manager.get_install_command() == []

    # ------------------------------------------------------------------
    # get_vendor_dir
    # ------------------------------------------------------------------

    def test_vendor_dir_is_under_plugin_path(
        self, manager: DependencyManager, tmp_plugin_path: str
    ) -> None:
        vendor = manager.get_vendor_dir()
        assert vendor.startswith(tmp_plugin_path)
        assert "_vendor" in vendor

    # ------------------------------------------------------------------
    # sys.path management
    # ------------------------------------------------------------------

    def test_vendor_dir_inserted_into_sys_path_when_it_exists(
        self, tmp_plugin_path: str
    ) -> None:
        vendor_dir = os.path.join(tmp_plugin_path, "_vendor")
        os.makedirs(vendor_dir, exist_ok=True)
        m = DependencyManager(tmp_plugin_path)
        try:
            assert vendor_dir in sys.path
        finally:
            sys.path[:] = [p for p in sys.path if p != vendor_dir]

    def test_vendor_dir_not_inserted_when_absent(self, tmp_plugin_path: str) -> None:
        vendor_dir = os.path.join(tmp_plugin_path, "_vendor")
        assert not os.path.isdir(vendor_dir)
        DependencyManager(tmp_plugin_path)
        assert vendor_dir not in sys.path

    def test_vendor_dir_not_duplicated_in_sys_path(
        self, tmp_plugin_path: str
    ) -> None:
        """Creating two managers for the same path must not duplicate the entry."""
        vendor_dir = os.path.join(tmp_plugin_path, "_vendor")
        os.makedirs(vendor_dir, exist_ok=True)
        DependencyManager(tmp_plugin_path)
        DependencyManager(tmp_plugin_path)
        try:
            assert sys.path.count(vendor_dir) <= 1
        finally:
            sys.path[:] = [p for p in sys.path if p != vendor_dir]
