# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from typing import Any, Dict, List, Optional

import numpy
import scipy.spatial

from cura.CuraApplication import CuraApplication
from cura.Scene.CuraSceneNode import CuraSceneNode
from UM.Math.Color import Color
from UM.Mesh.MeshBuilder import MeshBuilder
from UM.Operations.AddSceneNodeOperation import AddSceneNodeOperation
from UM.Operations.GroupedOperation import GroupedOperation
from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
from UM.Settings.SettingInstance import SettingInstance

_OVERLAY_NAME = "FEA Stress Overlay"

# Viridis colour map control points: (normalised_value, (R, G, B))
# Perceptually uniform and colorblind-safe (purple → blue → teal → green → yellow).
_COLORMAP = [
    (0.00, (0.267, 0.004, 0.329)),  # dark purple
    (0.25, (0.282, 0.140, 0.458)),  # blue-purple
    (0.50, (0.127, 0.566, 0.551)),  # teal
    (0.75, (0.544, 0.773, 0.247)),  # yellow-green
    (1.00, (0.993, 0.906, 0.144)),  # yellow
]


def _stress_to_color(normalized: float) -> numpy.ndarray:
    """Map a normalised stress value in [0, 1] to an (R, G, B) triple.

    Uses piecewise-linear interpolation between the _COLORMAP control points.

    Args:
        normalized: Stress value clamped to [0, 1].

    Returns:
        1-D float32 array ``[R, G, B]`` with components in [0, 1].
    """
    t = float(numpy.clip(normalized, 0.0, 1.0))
    for i in range(len(_COLORMAP) - 1):
        t0, c0 = _COLORMAP[i]
        t1, c1 = _COLORMAP[i + 1]
        if t <= t1:
            alpha = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            rgb = tuple(c0[j] + alpha * (c1[j] - c0[j]) for j in range(3))
            return numpy.array(rgb, dtype=numpy.float32)
    return numpy.array(_COLORMAP[-1][1], dtype=numpy.float32)


def _map_element_stress_to_vertices(
    surface_vertices: numpy.ndarray,
    tet_nodes: numpy.ndarray,
    tet_elements: numpy.ndarray,
    stress_per_element: numpy.ndarray,
) -> numpy.ndarray:
    """Average element von Mises stresses onto surface vertices.

    For every surface vertex, find all tet elements whose nodes include that
    vertex (by nearest-node lookup) and average their stresses.

    Args:
        surface_vertices: ``(V, 3)`` float array of surface vertex positions.
        tet_nodes: ``(N, 3)`` float array of tet mesh node positions.
        tet_elements: ``(E, 4)`` int array of tet element connectivity.
        stress_per_element: ``(E,)`` float array of von Mises stress per element.

    Returns:
        ``(V,)`` float array of per-vertex stress.
    """
    n_vertices = len(surface_vertices)
    vertex_stress = numpy.zeros(n_vertices, dtype=numpy.float64)
    vertex_count = numpy.zeros(n_vertices, dtype=numpy.int32)

    # Map each surface vertex to the nearest tet node index using a KDTree
    # (O(V log N) vs the O(V×N) brute-force, critical for large meshes).
    kd_tree = scipy.spatial.KDTree(tet_nodes)
    _, nearest_tet_node = kd_tree.query(surface_vertices, workers=1)
    # nearest_tet_node: shape (V,)

    # Build a mapping: tet_node_index → list of element indices
    node_to_elements: Dict[int, List[int]] = {}
    for elem_idx, elem_nodes in enumerate(tet_elements):
        for node_idx in elem_nodes:
            node_to_elements.setdefault(int(node_idx), []).append(elem_idx)

    for vert_idx, tet_node in enumerate(nearest_tet_node):
        adj_elements = node_to_elements.get(int(tet_node), [])
        if adj_elements:
            vertex_stress[vert_idx] = stress_per_element[adj_elements].mean()
            vertex_count[vert_idx] = len(adj_elements)

    return vertex_stress.astype(numpy.float32)


class StressOverlayManager:
    """Manages creation and removal of stress-visualisation overlay meshes.

    All methods are class methods; no instance state is maintained.
    """

    @classmethod
    def toggle_overlay(cls, node: CuraSceneNode, results: Dict[str, Any]) -> None:
        """Toggle the stress overlay on ``node``.

        If an overlay already exists it is removed; otherwise a new one is
        created from ``results``.

        Args:
            node: Parent scene node (the analysed mesh).
            results: FEA result dict containing at minimum ``"stress_field"``,
                ``"tet_mesh"``.
        """
        existing = cls._find_overlay(node)
        if existing is not None:
            cls.remove_overlay(node)
        else:
            cls.create_overlay(node, results)

    @classmethod
    def remove_overlay(cls, node: CuraSceneNode) -> None:
        """Remove the stress overlay child node from ``node`` if present.

        Args:
            node: Parent scene node from which the overlay should be removed.
        """
        overlay = cls._find_overlay(node)
        if overlay is None:
            return

        op = RemoveSceneNodeOperation(overlay)
        op.push()

    @classmethod
    def create_overlay(cls, node: CuraSceneNode, results: Dict[str, Any]) -> None:
        """Create a vertex-coloured surface mesh overlay representing stress.

        The overlay is added as a child of ``node`` and marked as a
        non-printing anti-overhang mesh so it does not affect slicing.

        Args:
            node: Parent scene node (the analysed mesh).
            results: FEA result dict with keys:

                * ``"stress_field"`` – per-element von Mises stress array.
                * ``"tet_mesh"`` – :class:`~..fea.tetrahedralization.TetMesh`.
        """
        from UM.Logger import Logger

        stress_field: numpy.ndarray = numpy.asarray(results["stress_field"], dtype=numpy.float32)
        tet_mesh = results["tet_mesh"]

        Logger.log("d", "FEA overlay: stress_field shape=%s, tet_mesh nodes=%d elems=%d",
                   stress_field.shape, len(tet_mesh.nodes), len(tet_mesh.elements))

        # Obtain surface representation from the node's mesh data
        source_mesh = node.getMeshData()
        if source_mesh is None:
            Logger.log("w", "FEA overlay: node has no mesh data")
            return

        raw_verts = source_mesh.getVertices()
        if raw_verts is None or len(raw_verts) == 0:
            Logger.log("w", "FEA overlay: node has no vertices")
            return
        surface_verts = numpy.asarray(raw_verts, dtype=numpy.float64)
        Logger.log("d", "FEA overlay: surface has %d vertices", len(surface_verts))

        # Map element stress to surface vertices
        vertex_stress = _map_element_stress_to_vertices(
            surface_vertices=surface_verts,
            tet_nodes=numpy.asarray(tet_mesh.nodes, dtype=numpy.float64),
            tet_elements=numpy.asarray(tet_mesh.elements, dtype=numpy.int32),
            stress_per_element=stress_field,
        )

        # Normalise stress to [0, 1]
        s_min = float(vertex_stress.min())
        s_max = float(vertex_stress.max())
        s_range = s_max - s_min if s_max > s_min else 1.0
        normalized = (vertex_stress - s_min) / s_range

        # Build per-vertex colour array (R, G, B, A) in [0, 1]
        colors = numpy.array(
            [numpy.append(_stress_to_color(float(n)), 1.0) for n in normalized],
            dtype=numpy.float32,
        )

        # Build overlay MeshData with colours
        # Use a tiny uniform scale-up (1.001×) from the center to prevent
        # Z-fighting instead of normal offset (which only works on one side)
        center = surface_verts.mean(axis=0)
        offset_verts = center + (surface_verts - center) * 1.002  # 0.2% scale-up
        offset_verts = offset_verts.astype(numpy.float32)

        builder = MeshBuilder()
        builder.setVertices(offset_verts)
        builder.setColors(colors)

        surface_indices = source_mesh.getIndices()
        if surface_indices is not None:
            builder.setIndices(numpy.asarray(surface_indices, dtype=numpy.int32))

        builder.calculateNormals()
        overlay_mesh = builder.build()
        Logger.log("d", "FEA overlay: built mesh with %d verts, %d colors",
                   len(offset_verts), len(colors))

        # Create overlay scene node
        application = CuraApplication.getInstance()
        global_stack = application.getGlobalContainerStack()

        # Create overlay node that renders with the transparent_object shader.
        # Uses the same approach as NonPlanarSlicing's region_overlay.py:
        # custom SceneNode with render() method using transparent_object.shader.
        from UM.Scene.SceneNode import SceneNode
        from UM.View.GL.OpenGL import OpenGL

        class _StressOverlayNode(SceneNode):
            """SceneNode that renders per-vertex colored stress visualization."""

            def __init__(self, parent=None):
                super().__init__(parent)
                self._shader = None

            def render(self, renderer):
                if not self.getMeshData():
                    return True
                if self._shader is None:
                    self._shader = OpenGL.getInstance().createShaderProgram(
                        Resources.getPath(Resources.Shaders, "transparent_object.shader")
                    )
                    if self._shader is None:
                        return True
                    self._shader.setUniformValue("u_opacity", 0.45)

                renderer.queueNode(
                    self,
                    shader=self._shader,
                    transparent=True,
                    backface_cull=False,
                    sort=-8,
                )
                return True

        from UM.Resources import Resources

        overlay_node = _StressOverlayNode()
        overlay_node.setName(_OVERLAY_NAME)
        overlay_node.setSelectable(False)
        overlay_node.setMeshData(overlay_mesh)
        overlay_node.setCalculateBoundingBox(False)

        controller = application.getController()
        scene = controller.getScene()

        grouped_op = GroupedOperation()
        grouped_op.addOperation(
            AddSceneNodeOperation(overlay_node, scene.getRoot())
        )
        from cura.Operations.SetParentOperation import SetParentOperation
        grouped_op.addOperation(SetParentOperation(overlay_node, node))
        grouped_op.push()

        Logger.log("d", "FEA overlay: overlay node created with transparent shader, added to '%s'",
                   node.getName())

        scene.sceneChanged.emit(overlay_node)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _find_overlay(cls, node: CuraSceneNode) -> Optional[CuraSceneNode]:
        """Return the overlay child node or ``None`` if absent."""
        for child in node.getChildren():
            if child.getName() == _OVERLAY_NAME:
                return child  # type: ignore[return-value]
        return None
