"""Colored mesh overlay showing candidate non-planar regions on the model.

In the PrepareStage this overlay highlights which parts of the model
surface will be treated non-planarly:

* **Green** -- safe non-planar regions (will be bent).
* **Yellow** -- borderline regions (near the collision boundary).
* **Red** -- rejected regions (collision detected).

The overlay is a custom ``SceneNode`` subclass with its own ``render()``
method (following the ``ConvexHullNode`` pattern) that uses a transparent
shader.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
from typing import List, Optional, TYPE_CHECKING

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

# ---- Colour constants (RGBA, float) ----

COLOR_SAFE = numpy.array([0.2, 0.8, 0.2, 0.5], dtype=numpy.float32)
COLOR_BORDERLINE = numpy.array([0.9, 0.8, 0.1, 0.5], dtype=numpy.float32)
COLOR_REJECTED = numpy.array([0.8, 0.2, 0.2, 0.5], dtype=numpy.float32)

# Small offset along face normals to prevent Z-fighting (mm).
_NORMAL_OFFSET = 0.15


# ---- Custom SceneNode subclass with rendering ----

if _CURA_AVAILABLE:
    class NonPlanarOverlayNode(SceneNode):
        """Scene node that renders a transparent coloured mesh overlay.

        Follows the same pattern as ConvexHullNode: overrides ``render()``
        to set up a transparent shader and queue the mesh for rendering.
        """

        _shader = None  # Class-level shader cache

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.setSelectable(False)
            self.setCalculateBoundingBox(False)
            self._color = Color(0.2, 0.8, 0.2, 1.0)

        def render(self, renderer):
            """Render with a transparent shader."""
            if not self.getMeshData():
                return True

            if NonPlanarOverlayNode._shader is None:
                NonPlanarOverlayNode._shader = OpenGL.getInstance().createShaderProgram(
                    Resources.getPath(Resources.Shaders, "transparent_object.shader")
                )
                NonPlanarOverlayNode._shader.setUniformValue("u_diffuseColor", self._color)
                NonPlanarOverlayNode._shader.setUniformValue("u_opacity", 0.5)

            renderer.queueNode(
                self,
                shader=NonPlanarOverlayNode._shader,
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
    ) -> Optional[object]:
        """Create and attach the overlay mesh to the parent scene node.

        Parameters
        ----------
        parent_node:
            The ``CuraSceneNode`` being analysed.
        vertices:
            ``(N, 3)`` mesh vertices in **model space** (Y-up, as
            returned by ``getMeshData().getVertices()``).
        indices:
            ``(M, 3)`` triangle vertex-index array.
        candidate_regions:
            Detected candidate regions from
            :func:`~analysis.candidate_detector.detect_candidates`.
        collision_result:
            Optional collision-check results.  When provided, each
            candidate face is classified as safe, borderline, or
            rejected.
        height_map:
            Optional height map (in Z-up slicing coordinates) for
            mapping face positions to the safe map.
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

        mesh_data = self._build_overlay_mesh(
            vertices, indices, candidate_regions, collision_result, height_map,
            parent_node,
        )
        if mesh_data is None:
            return None

        overlay_node = NonPlanarOverlayNode(parent=parent_node)
        overlay_node.setMeshData(mesh_data)

        self._overlay_nodes.append(overlay_node)

        logger.info(
            "Created non-planar overlay with %d faces",
            mesh_data.getVertexCount() // 3 if mesh_data.getVertexCount() else 0,
        )

        return overlay_node

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

    def _build_overlay_mesh(
        self,
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        candidate_regions: "CandidateRegions",
        collision_result: Optional["CollisionResult"],
        height_map,
        parent_node=None,
    ) -> Optional[object]:
        """Build the overlay ``MeshData`` with per-vertex colours.

        Vertices are in model space (Y-up).  For collision/safety
        lookups we transform to slicing coordinates (Z-up) to match
        the height map.
        """

        num_faces = indices.shape[0]
        candidate_mask = candidate_regions.all_candidate_mask
        if candidate_mask.size != num_faces:
            logger.error("candidate_mask length mismatch")
            return None

        candidate_face_ids = numpy.nonzero(candidate_mask)[0]
        if candidate_face_ids.size == 0:
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

        # Classify faces (using transformed coordinates for safety lookups).
        face_colors = self._classify_faces(
            vertices, indices, candidate_face_ids,
            collision_result, height_map, world_transform,
        )

        # Compute face normals in model space for the offset.
        face_normals = self._compute_face_normals(vertices, indices, candidate_face_ids)

        # Gather triangle vertices and apply normal offset.
        tris = indices[candidate_face_ids]
        v0 = vertices[tris[:, 0]].copy()
        v1 = vertices[tris[:, 1]].copy()
        v2 = vertices[tris[:, 2]].copy()

        offset = face_normals * _NORMAL_OFFSET
        v0 += offset
        v1 += offset
        v2 += offset

        n_overlay_faces = candidate_face_ids.size
        overlay_verts = numpy.empty((n_overlay_faces * 3, 3), dtype=numpy.float32)
        overlay_verts[0::3] = v0
        overlay_verts[1::3] = v1
        overlay_verts[2::3] = v2

        overlay_indices = numpy.arange(
            n_overlay_faces * 3, dtype=numpy.int32
        ).reshape(-1, 3)

        overlay_colors = numpy.repeat(
            face_colors.astype(numpy.float32), 3, axis=0
        )

        builder = MeshBuilder()
        builder.reserveFaceAndVertexCount(n_overlay_faces, n_overlay_faces * 3)
        builder.addFacesWithColor(overlay_verts, overlay_indices, overlay_colors)

        return builder.build()

    @staticmethod
    def _model_to_slicing_coords(
        vertices: numpy.ndarray,
        world_transform: Optional[numpy.ndarray],
    ) -> numpy.ndarray:
        """Convert model-space Y-up vertices to slicing Z-up coordinates.

        This matches the transform done in
        ``NonPlanarSlicingExtension._transformVertices``.
        """
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
        """Assign a colour to each candidate face.

        Returns (K, 4) RGBA colour array.
        """
        n_faces = candidate_face_ids.size
        colors = numpy.tile(COLOR_SAFE, (n_faces, 1))

        if collision_result is None or height_map is None:
            return colors

        safe_map = getattr(collision_result, "safe_map", None)
        if safe_map is None:
            return colors

        safe_map = numpy.asarray(safe_map, dtype=bool)

        # Transform vertices to slicing coordinates for height map lookup.
        referenced = numpy.unique(indices[candidate_face_ids].ravel())
        slicing_verts = self._model_to_slicing_coords(
            vertices[referenced], world_transform,
        )

        # Build a vertex index -> safety lookup.
        # In slicing coords: column 0 = X, column 1 = Y, column 2 = Z (height).
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
                colors[local_idx] = COLOR_SAFE
            elif numpy.any(vs):
                colors[local_idx] = COLOR_BORDERLINE
            else:
                colors[local_idx] = COLOR_REJECTED

        return colors

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
