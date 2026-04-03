"""Colored mesh overlay showing candidate non-planar regions on the model.

In the PrepareStage this overlay highlights which parts of the model
surface will be treated non-planarly:

* **Green** -- safe non-planar regions (will be bent).
* **Yellow** -- borderline regions (near the collision boundary).
* **Red** -- rejected regions (collision detected).

Each colour category gets its own ``NonPlanarOverlayNode`` because the
``transparent_object.shader`` only supports a uniform diffuse colour
(no per-vertex colour attribute).

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, TYPE_CHECKING

import numpy

if TYPE_CHECKING:
    from ..analysis.candidate_detector import CandidateRegions
    from ..analysis.collision_checker import CollisionResult

logger = logging.getLogger(__name__)

# ---- Cura / Uranium imports (wrapped for testability) ----

try:
    from UM.Mesh.MeshBuilder import MeshBuilder
    from UM.Mesh.MeshData import MeshData
    from UM.Scene.SceneNode import SceneNode
    from UM.View.GL.OpenGL import OpenGL
    from UM.Resources import Resources
    from UM.Math.Color import Color

    _CURA_AVAILABLE = True
except ImportError:
    _CURA_AVAILABLE = False
    MeshBuilder = None  # type: ignore[assignment,misc]
    MeshData = None  # type: ignore[assignment,misc]
    SceneNode = None  # type: ignore[assignment,misc]

# ---- Colour constants ----

# Uniform Color objects for the shader (R, G, B, A)
_COLOR_SAFE = (0.2, 0.8, 0.2, 1.0)
_COLOR_BORDERLINE = (0.9, 0.8, 0.1, 1.0)
_COLOR_REJECTED = (0.8, 0.2, 0.2, 1.0)

# Small offset along face normals to prevent Z-fighting (mm).
_NORMAL_OFFSET = 0.15
# Opacity for the overlay
_OPACITY = 0.5


# ---- Classification enum ----

_SAFE = 0
_BORDERLINE = 1
_REJECTED = 2


# ---- Custom SceneNode subclass with rendering ----

if _CURA_AVAILABLE:
    class NonPlanarOverlayNode(SceneNode):
        """Scene node that renders a transparent coloured mesh overlay.

        Each instance gets its own shader with a specific diffuse colour,
        following the ConvexHullNode pattern.
        """

        def __init__(self, parent=None, color_rgba=None) -> None:
            super().__init__(parent)
            self.setSelectable(False)
            self.setCalculateBoundingBox(False)
            if color_rgba is None:
                color_rgba = _COLOR_SAFE
            self._color = Color(*color_rgba)
            self._shader = None

        def render(self, renderer):
            """Render with a transparent shader."""
            if not self.getMeshData():
                return True

            if self._shader is None:
                self._shader = OpenGL.getInstance().createShaderProgram(
                    Resources.getPath(Resources.Shaders, "transparent_object.shader")
                )
                self._shader.setUniformValue("u_diffuseColor", self._color)
                self._shader.setUniformValue("u_opacity", _OPACITY)

            renderer.queueNode(
                self,
                shader=self._shader,
                transparent=True,
                backface_cull=False,
                sort=-7,
            )
            return True
else:
    NonPlanarOverlayNode = None  # type: ignore[assignment,misc]


class NonPlanarRegionOverlay:
    """Creates a visual overlay mesh highlighting non-planar candidate regions.

    Typical usage::

        overlay = NonPlanarRegionOverlay()
        node = overlay.create_overlay(
            parent_node, vertices, indices, candidate_regions,
            collision_result=collision_result,
            height_map=height_map,
        )
        # ... later ...
        overlay.remove_overlays()
    """

    def __init__(self) -> None:
        self._overlay_nodes: List[object] = []

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def create_overlay(
        self,
        parent_node,
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        candidate_regions: "CandidateRegions",
        collision_result: Optional["CollisionResult"] = None,
        height_map=None,
    ) -> Optional[List[object]]:
        """Create and attach overlay mesh nodes to the parent scene node.

        Creates up to three overlay nodes (safe/borderline/rejected), each
        with its own uniform colour, because the shader does not support
        per-vertex colours.

        Returns a list of created overlay nodes.
        """

        if not _CURA_AVAILABLE:
            logger.warning("Cura imports not available; cannot create overlay")
            return None

        if not hasattr(candidate_regions, "regions") or not candidate_regions.regions:
            logger.debug("No candidate regions; skipping overlay creation")
            return None

        vertices = numpy.asarray(vertices, dtype=numpy.float64)
        if indices is not None:
            indices = numpy.asarray(indices, dtype=numpy.intp)
        else:
            # Non-indexed mesh: synthesize sequential triangle indices
            num_verts = vertices.shape[0]
            if num_verts % 3 != 0:
                logger.error("Non-indexed mesh vertex count %d not divisible by 3", num_verts)
                return None
            indices = numpy.arange(num_verts, dtype=numpy.intp).reshape(-1, 3)

        if vertices.ndim != 2 or vertices.shape[1] != 3:
            logger.error("Invalid vertices shape %s", vertices.shape)
            return None
        if indices.ndim != 2 or indices.shape[1] != 3:
            logger.error("Invalid indices shape %s", indices.shape)
            return None

        # Get the world transform for converting model-space vertices
        # to slicing coordinates (Z-up) for height map lookups.
        world_transform = None
        if parent_node is not None:
            try:
                t = parent_node.getWorldTransformation()
                if t is not None:
                    world_transform = t.getData()
            except Exception:
                pass

        # Classify candidate faces.
        num_faces = indices.shape[0]
        candidate_mask = candidate_regions.all_candidate_mask
        if candidate_mask.size != num_faces:
            logger.error("candidate_mask length mismatch: %d vs %d faces",
                         candidate_mask.size, num_faces)
            return None

        candidate_face_ids = numpy.nonzero(candidate_mask)[0]
        if candidate_face_ids.size == 0:
            return None

        face_classes = self._classify_faces(
            vertices, indices, candidate_face_ids,
            collision_result, height_map, world_transform,
        )

        # Compute face normals in model space for the offset.
        face_normals = self._compute_face_normals(vertices, indices, candidate_face_ids)

        # Build one overlay node per colour category.
        color_map = {
            _SAFE: _COLOR_SAFE,
            _BORDERLINE: _COLOR_BORDERLINE,
            _REJECTED: _COLOR_REJECTED,
        }

        created_nodes = []
        for cls_value, color_rgba in color_map.items():
            mask = face_classes == cls_value
            if not numpy.any(mask):
                continue

            cls_face_ids = candidate_face_ids[mask]
            cls_normals = face_normals[mask]

            mesh_data = self._build_mesh_for_faces(
                vertices, indices, cls_face_ids, cls_normals,
            )
            if mesh_data is None:
                continue

            overlay_node = NonPlanarOverlayNode(parent=parent_node, color_rgba=color_rgba)
            overlay_node.setMeshData(mesh_data)
            self._overlay_nodes.append(overlay_node)
            created_nodes.append(overlay_node)

        total_faces = candidate_face_ids.size
        logger.info(
            "Created %d non-planar overlay node(s) covering %d faces",
            len(created_nodes), total_faces,
        )

        return created_nodes if created_nodes else None

    def remove_overlays(self) -> None:
        """Remove all overlay nodes from the scene."""
        removed = 0
        for node in self._overlay_nodes:
            try:
                node.setParent(None)
                removed += 1
            except Exception:
                logger.debug("Failed to detach overlay node", exc_info=True)

        self._overlay_nodes.clear()
        if removed:
            logger.debug("Removed %d overlay node(s)", removed)

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    def _build_mesh_for_faces(
        self,
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        face_ids: numpy.ndarray,
        face_normals: numpy.ndarray,
    ) -> Optional[object]:
        """Build MeshData for a set of faces with normal offset."""
        n_faces = face_ids.size
        if n_faces == 0:
            return None

        tris = indices[face_ids]
        v0 = vertices[tris[:, 0]].copy()
        v1 = vertices[tris[:, 1]].copy()
        v2 = vertices[tris[:, 2]].copy()

        offset = face_normals * _NORMAL_OFFSET
        v0 += offset
        v1 += offset
        v2 += offset

        overlay_verts = numpy.empty((n_faces * 3, 3), dtype=numpy.float32)
        overlay_verts[0::3] = v0
        overlay_verts[1::3] = v1
        overlay_verts[2::3] = v2

        overlay_indices = numpy.arange(
            n_faces * 3, dtype=numpy.int32
        ).reshape(-1, 3)

        builder = MeshBuilder()
        builder.setVertices(overlay_verts)
        builder.setIndices(overlay_indices)

        return builder.build()

    @staticmethod
    def _model_to_slicing_coords(
        vertices: numpy.ndarray,
        world_transform: Optional[numpy.ndarray],
    ) -> numpy.ndarray:
        """Convert model-space Y-up vertices to slicing Z-up coordinates."""
        if world_transform is not None:
            rot_scale = world_transform[:3, :3]
            translate = world_transform[:3, 3]
            world_verts = vertices.dot(rot_scale.T) + translate
        else:
            world_verts = vertices

        result = numpy.empty_like(world_verts)
        result[:, 0] = world_verts[:, 0]    # X stays
        result[:, 1] = -world_verts[:, 2]   # Y = -Z_scene
        result[:, 2] = world_verts[:, 1]    # Z = Y_scene (height)
        return result

    def _classify_faces(
        self,
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        candidate_face_ids: numpy.ndarray,
        collision_result: Optional["CollisionResult"],
        height_map,
        world_transform: Optional[numpy.ndarray],
    ) -> numpy.ndarray:
        """Assign a classification to each candidate face.

        Returns (K,) int array with values _SAFE, _BORDERLINE, or _REJECTED.
        """
        n_faces = candidate_face_ids.size
        classes = numpy.full(n_faces, _SAFE, dtype=numpy.int32)

        if collision_result is None or height_map is None:
            return classes

        safe_map = getattr(collision_result, "safe_map", None)
        if safe_map is None:
            return classes

        safe_map = numpy.asarray(safe_map, dtype=bool)

        # Transform vertices to slicing coordinates for height map lookup.
        referenced = numpy.unique(indices[candidate_face_ids].ravel())
        slicing_verts = self._model_to_slicing_coords(
            vertices[referenced], world_transform,
        )

        # Build a vertex index -> safety lookup.
        vertex_safe = numpy.zeros(vertices.shape[0], dtype=bool)
        for i, vi in enumerate(referenced):
            sx, sy = float(slicing_verts[i, 0]), float(slicing_verts[i, 1])
            if not height_map.is_valid(sx, sy):
                continue
            row, col = height_map.get_grid_coords(sx, sy)
            if 0 <= row < safe_map.shape[0] and 0 <= col < safe_map.shape[1]:
                vertex_safe[vi] = safe_map[row, col]

        # Classify each face based on vertex safety.
        for local_idx in range(n_faces):
            face_id = candidate_face_ids[local_idx]
            tri = indices[face_id]
            vs = vertex_safe[tri]

            if numpy.all(vs):
                classes[local_idx] = _SAFE
            elif numpy.any(vs):
                classes[local_idx] = _BORDERLINE
            else:
                classes[local_idx] = _REJECTED

        return classes

    @staticmethod
    def _compute_face_normals(
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        face_ids: numpy.ndarray,
    ) -> numpy.ndarray:
        """Compute unit normals for the specified faces.

        Returns (K, 3) unit normals. Degenerate triangles get (0, 1, 0).
        """
        tris = indices[face_ids]
        v0 = vertices[tris[:, 0]]
        v1 = vertices[tris[:, 1]]
        v2 = vertices[tris[:, 2]]

        edge1 = v1 - v0
        edge2 = v2 - v0
        cross = numpy.cross(edge1, edge2)

        lengths = numpy.linalg.norm(cross, axis=1, keepdims=True)
        degenerate = lengths.ravel() < 1e-12
        safe_lengths = numpy.where(degenerate[:, numpy.newaxis], 1.0, lengths)
        normals = cross / safe_lengths
        normals[degenerate] = numpy.array([0.0, 1.0, 0.0])

        return normals
