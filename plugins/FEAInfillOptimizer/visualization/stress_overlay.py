# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from typing import Any, Dict, Optional

import numpy
import scipy.spatial

from cura.CuraApplication import CuraApplication
from cura.Scene.CuraSceneNode import CuraSceneNode
from UM.Math.Color import Color
from UM.Mesh.MeshBuilder import MeshBuilder
from UM.Operations.AddSceneNodeOperation import AddSceneNodeOperation
from UM.Operations.GroupedOperation import GroupedOperation
from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation

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


def _stress_to_color_vectorized(normalized: numpy.ndarray) -> numpy.ndarray:
    """Vectorized colormap: map an array of normalized values to RGB colors.

    Mathematically equivalent to calling _stress_to_color() per value, but
    operates on the full array at once using numpy broadcasting. ~100x faster
    for typical vertex counts (10K-100K).

    Args:
        normalized: 1-D array of stress values in [0, 1], shape (V,).

    Returns:
        (V, 3) float32 array of RGB colors.
    """
    t = numpy.clip(normalized, 0.0, 1.0)
    n = len(t)
    rgb = numpy.zeros((n, 3), dtype=numpy.float32)

    # Build arrays from colormap control points
    cp_t = numpy.array([p[0] for p in _COLORMAP], dtype=numpy.float64)
    cp_c = numpy.array([p[1] for p in _COLORMAP], dtype=numpy.float64)  # (n_cp, 3)

    # For each segment, find values that fall in it and interpolate
    for i in range(len(_COLORMAP) - 1):
        t0 = cp_t[i]
        t1 = cp_t[i + 1]
        c0 = cp_c[i]   # (3,)
        c1 = cp_c[i + 1]  # (3,)

        if i == 0:
            mask = t <= t1
        elif i == len(_COLORMAP) - 2:
            mask = t > t0
        else:
            mask = (t > t0) & (t <= t1)

        if not numpy.any(mask):
            continue

        dt = t1 - t0 if t1 > t0 else 1.0
        alpha = ((t[mask] - t0) / dt)[:, numpy.newaxis]  # (count, 1)
        rgb[mask] = (c0[numpy.newaxis, :] + alpha * (c1 - c0)[numpy.newaxis, :]).astype(numpy.float32)

    return rgb


def _map_element_stress_to_vertices(
    surface_vertices: numpy.ndarray,
    tet_nodes: numpy.ndarray,
    tet_elements: numpy.ndarray,
    stress_per_element: numpy.ndarray,
) -> numpy.ndarray:
    """Average element von Mises stresses onto surface vertices.

    For every surface vertex, find all tet elements whose nodes include that
    vertex (by nearest-node lookup) and average their stresses.

    Uses vectorized numpy operations: scatter-accumulate via np.add.at to
    build node→stress sums and counts, then gather via fancy indexing.
    ~50-100x faster than the per-element Python dict loop for typical meshes.

    Args:
        surface_vertices: ``(V, 3)`` float array of surface vertex positions.
        tet_nodes: ``(N, 3)`` float array of tet mesh node positions.
        tet_elements: ``(E, 4)`` int array of tet element connectivity.
        stress_per_element: ``(E,)`` float array of von Mises stress per element.

    Returns:
        ``(V,)`` float array of per-vertex stress.
    """
    import time as _time
    _t0 = _time.monotonic()

    n_nodes = len(tet_nodes)
    n_elems = len(tet_elements)

    # Map each surface vertex to the nearest tet node index using a KDTree
    kd_tree = scipy.spatial.KDTree(tet_nodes)
    _, nearest_tet_node = kd_tree.query(surface_vertices, workers=-1)
    # nearest_tet_node: shape (V,)

    # Vectorized node_to_elements: accumulate stress sum and count per node
    # Each element contributes its stress to all 4 of its nodes.
    # node_stress_sum[n] = sum of stress over all elements containing node n
    # node_elem_count[n] = number of elements containing node n
    node_stress_sum = numpy.zeros(n_nodes, dtype=numpy.float64)
    node_elem_count = numpy.zeros(n_nodes, dtype=numpy.int32)

    # tet_elements is (E, 4): each element has 4 nodes
    # Expand stress to match: repeat each element's stress 4 times
    stress_expanded = numpy.repeat(stress_per_element, 4)  # (E*4,)
    node_indices_flat = tet_elements.ravel()  # (E*4,)

    numpy.add.at(node_stress_sum, node_indices_flat, stress_expanded)
    numpy.add.at(node_elem_count, node_indices_flat, 1)

    # Average stress per node (avoid division by zero)
    node_avg_stress = numpy.zeros(n_nodes, dtype=numpy.float64)
    has_elements = node_elem_count > 0
    node_avg_stress[has_elements] = node_stress_sum[has_elements] / node_elem_count[has_elements]

    # Gather: each surface vertex gets the average stress of its nearest tet node
    vertex_stress = node_avg_stress[nearest_tet_node]

    _t1 = _time.monotonic()
    from UM.Logger import Logger
    Logger.log("d", "FEA stress_to_vertices: vectorized %.3fs (%d verts, %d elems)",
               _t1 - _t0, len(surface_vertices), n_elems)

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

        # Transform vertices to world space (following NonPlanarSlicing's
        # region_overlay.py pattern — avoids double-transformation when the
        # overlay is parented to scene_root instead of the model node).
        world_transform = node.getWorldTransformation()
        if world_transform is not None:
            world_matrix = world_transform.getData()
            rot_scale = world_matrix[:3, :3]
            translate = world_matrix[:3, 3]
            surface_verts = surface_verts.dot(rot_scale.T) + translate

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
        # Vectorized: compute all RGB values at once, then append alpha channel
        rgb = _stress_to_color_vectorized(normalized)  # (V, 3)
        colors = numpy.ones((len(normalized), 4), dtype=numpy.float32)
        colors[:, :3] = rgb

        # Build overlay MeshData with colours
        # Offset vertices slightly along face normals to prevent Z-fighting.
        # Compute per-face normals, then average to per-vertex normals.
        surface_indices = source_mesh.getIndices()
        offset_verts = surface_verts.copy().astype(numpy.float32)

        # Compute per-vertex normals for offset
        if surface_indices is not None:
            indices_arr = numpy.asarray(surface_indices, dtype=numpy.int32)
            v0 = surface_verts[indices_arr[:, 0]]
            v1 = surface_verts[indices_arr[:, 1]]
            v2 = surface_verts[indices_arr[:, 2]]
            face_normals = numpy.cross(v1 - v0, v2 - v0)
            norms = numpy.linalg.norm(face_normals, axis=1, keepdims=True)
            norms[norms < 1e-10] = 1.0
            face_normals /= norms

            # Accumulate face normals to vertices
            vert_normals = numpy.zeros_like(surface_verts)
            for i in range(3):
                numpy.add.at(vert_normals, indices_arr[:, i], face_normals)
            vn_norms = numpy.linalg.norm(vert_normals, axis=1, keepdims=True)
            vn_norms[vn_norms < 1e-10] = 1.0
            vert_normals /= vn_norms

            # Offset along normals (outward) — 0.15mm
            offset_verts += (vert_normals * 0.15).astype(numpy.float32)
        else:
            # Flat vertex layout: offset per-triangle (vectorized)
            n_tris = len(surface_verts) // 3
            if n_tris > 0:
                tri_verts = surface_verts[:n_tris * 3].reshape(n_tris, 3, 3)
                v0 = tri_verts[:, 0, :]  # (n_tris, 3)
                v1 = tri_verts[:, 1, :]
                v2 = tri_verts[:, 2, :]
                normals = numpy.cross(v1 - v0, v2 - v0)  # (n_tris, 3)
                nl = numpy.linalg.norm(normals, axis=1, keepdims=True)
                nl[nl < 1e-10] = 1.0
                normals /= nl
                # Expand: each triangle normal applies to 3 vertices
                normals_expanded = numpy.repeat(normals, 3, axis=0)  # (n_tris*3, 3)
                offset_verts[:n_tris * 3] += (normals_expanded * 0.15).astype(numpy.float32)

        builder = MeshBuilder()
        builder.setVertices(offset_verts)
        builder.setColors(colors)

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
        # custom SceneNode with render() using a per-vertex-color shader.
        # The built-in transparent_object.shader only uses u_diffuseColor uniform
        # and ignores vertex colors entirely. Our custom stress_overlay.shader
        # reads per-vertex a_color attribute for the viridis colormap.
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
                    # Use our custom shader that reads per-vertex a_color
                    import os
                    shader_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "resources", "shaders", "stress_overlay.shader"
                    )
                    self._shader = OpenGL.getInstance().createShaderProgram(shader_path)
                    if self._shader is None:
                        Logger.log("w", "FEA overlay: failed to load stress_overlay.shader, "
                                   "falling back to transparent_object")
                        self._shader = OpenGL.getInstance().createShaderProgram(
                            Resources.getPath(Resources.Shaders, "transparent_object.shader")
                        )
                    if self._shader is None:
                        return True
                    self._shader.setUniformValue("u_opacity", 0.85)

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

        # Parent to scene_root (NOT the model node) since vertices are already
        # in world space.  This follows the ConvexHullNode / NonPlanarSlicing
        # pattern to avoid double-transformation that causes the overlay to
        # appear below the build plate.
        # Store the parent node's id so _find_overlay can locate it.
        overlay_node._fea_parent_node_id = id(node)

        op = AddSceneNodeOperation(overlay_node, scene.getRoot())
        op.push()

        Logger.log("d", "FEA overlay: overlay node created with transparent shader, "
                   "parented to scene root (world-space coords), for model '%s'",
                   node.getName())

        scene.sceneChanged.emit(overlay_node)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def cleanup_orphaned_overlays(cls) -> None:
        """Remove any stress overlay nodes whose parent model no longer exists.

        Since overlays are parented to scene_root (not the model node),
        they persist when the model is deleted. This method scans for
        orphans and removes them.  Should be called on scene changes.
        """
        scene = CuraApplication.getInstance().getController().getScene()
        root = scene.getRoot()

        # Build set of live node ids
        live_ids = set()
        for node in root.getChildren():
            if node.getName() != _OVERLAY_NAME:
                live_ids.add(id(node))

        # Find and remove orphaned overlays
        to_remove = []
        for child in list(root.getChildren()):
            if child.getName() == _OVERLAY_NAME:
                parent_id = getattr(child, "_fea_parent_node_id", None)
                if parent_id is not None and parent_id not in live_ids:
                    to_remove.append(child)

        for overlay in to_remove:
            op = RemoveSceneNodeOperation(overlay)
            op.push()

    @classmethod
    def _find_overlay(cls, node: CuraSceneNode) -> Optional[CuraSceneNode]:
        """Return the overlay node for ``node`` or ``None`` if absent.

        The overlay is parented to scene_root (not the model node) to avoid
        double-transformation.  We find it by scanning scene_root's children
        for nodes named ``_OVERLAY_NAME`` that have a matching parent id tag.
        """
        node_id = id(node)
        scene = CuraApplication.getInstance().getController().getScene()
        for child in scene.getRoot().getChildren():
            if child.getName() == _OVERLAY_NAME:
                if getattr(child, "_fea_parent_node_id", None) == node_id:
                    return child  # type: ignore[return-value]
        # Backward compat: also check direct children of node (old overlays)
        for child in node.getChildren():
            if child.getName() == _OVERLAY_NAME:
                return child  # type: ignore[return-value]
        return None
