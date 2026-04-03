"""Colored mesh overlay showing candidate non-planar regions on the model.

In the PrepareStage this overlay highlights which parts of the model
surface will be treated non-planarly:

* **Green** -- safe non-planar regions (will be bent).
* **Yellow** -- borderline regions (near the collision boundary).
* **Red** -- rejected regions (collision detected).

The overlay is built with ``UM.Mesh.MeshBuilder`` and attached as a child
``SceneNode`` of the model being analysed.

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

    _CURA_AVAILABLE = True
except ImportError:
    _CURA_AVAILABLE = False
    MeshBuilder = None  # type: ignore[assignment,misc]
    MeshData = None  # type: ignore[assignment,misc]
    SceneNode = None  # type: ignore[assignment,misc]

# ---- Colour constants (RGBA, float) ----

COLOR_SAFE = numpy.array([0.2, 0.8, 0.2, 0.4], dtype=numpy.float32)
COLOR_BORDERLINE = numpy.array([0.8, 0.8, 0.2, 0.4], dtype=numpy.float32)
COLOR_REJECTED = numpy.array([0.8, 0.2, 0.2, 0.4], dtype=numpy.float32)

# Small offset along face normals to prevent Z-fighting (mm).
_NORMAL_OFFSET = 0.1


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
            ``(N, 3)`` mesh vertices in world coordinates (mm).
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
            Optional height map for mapping face positions to the safe
            map.  If supplied it must have ``is_valid(x, y)`` and
            ``get_grid_coords(x, y)`` methods, and the
            collision_result must carry a ``safe_map`` attribute.

        Returns
        -------
        SceneNode or None
            The created overlay node, or ``None`` if no overlay was
            needed (e.g. no candidate regions, or Cura imports
            unavailable).
        """

        if not _CURA_AVAILABLE:
            logger.warning(
                "Cura imports not available; cannot create overlay node"
            )
            return None

        if not hasattr(candidate_regions, "regions") or not candidate_regions.regions:
            logger.debug("No candidate regions; skipping overlay creation")
            return None

        vertices = numpy.asarray(vertices, dtype=numpy.float64)
        indices = numpy.asarray(indices, dtype=numpy.intp)

        if vertices.ndim != 2 or vertices.shape[1] != 3:
            logger.error(
                "Invalid vertices shape %s; expected (N, 3)", vertices.shape
            )
            return None
        if indices.ndim != 2 or indices.shape[1] != 3:
            logger.error(
                "Invalid indices shape %s; expected (M, 3)", indices.shape
            )
            return None

        mesh_data = self._build_overlay_mesh(
            vertices, indices, candidate_regions, collision_result, height_map
        )
        if mesh_data is None:
            return None

        overlay_node = SceneNode()
        overlay_node.setMeshData(mesh_data)
        overlay_node.setSelectable(False)
        overlay_node.setCalculateBoundingBox(False)
        overlay_node.setParent(parent_node)

        self._overlay_nodes.append(overlay_node)

        logger.info(
            "Created non-planar overlay node with %d faces",
            mesh_data.getVertexCount() // 3 if mesh_data.getVertexCount() else 0,
        )

        return overlay_node

    def remove_overlays(self) -> None:
        """Remove all overlay nodes from the scene.

        Each previously created overlay node is detached from its parent
        and dereferenced so it can be garbage-collected.
        """

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
    ) -> Optional[object]:
        """Build the overlay ``MeshData`` with per-vertex colours.

        Only faces that belong to at least one candidate region are
        included.  Each face is coloured according to its collision
        status.
        """

        num_faces = indices.shape[0]
        candidate_mask = candidate_regions.all_candidate_mask
        if candidate_mask.size != num_faces:
            logger.error(
                "candidate_mask length (%d) does not match face count (%d)",
                candidate_mask.size,
                num_faces,
            )
            return None

        candidate_face_ids = numpy.nonzero(candidate_mask)[0]
        if candidate_face_ids.size == 0:
            logger.debug("No candidate faces to overlay")
            return None

        # Classify each candidate face.
        face_colors = self._classify_faces(
            vertices, indices, candidate_face_ids,
            candidate_regions, collision_result, height_map,
        )

        # Compute per-face normals for offsetting.
        face_normals = self._compute_face_normals(
            vertices, indices, candidate_face_ids
        )

        # Gather triangle vertices and apply normal offset (vectorised).
        tris = indices[candidate_face_ids]  # (K, 3)
        v0 = vertices[tris[:, 0]]  # (K, 3)
        v1 = vertices[tris[:, 1]]
        v2 = vertices[tris[:, 2]]

        offset = face_normals * _NORMAL_OFFSET  # (K, 3)
        v0 = v0 + offset
        v1 = v1 + offset
        v2 = v2 + offset

        # Stack into the interleaved vertex array that addFacesWithColor
        # expects: rows of [v0 v1 v2] concatenated, shape (K*3, 3).
        n_overlay_faces = candidate_face_ids.size
        overlay_verts = numpy.empty((n_overlay_faces * 3, 3), dtype=numpy.float32)
        overlay_verts[0::3] = v0
        overlay_verts[1::3] = v1
        overlay_verts[2::3] = v2

        # Face index array: sequential triplets.
        overlay_indices = numpy.arange(
            n_overlay_faces * 3, dtype=numpy.int32
        ).reshape(-1, 3)

        # Per-vertex colours: repeat each face colour 3 times (one per
        # vertex of the triangle).
        overlay_colors = numpy.repeat(
            face_colors.astype(numpy.float32), 3, axis=0
        )

        # Build using the bulk API (same pattern as Layer.createMeshOrJumps).
        builder = MeshBuilder()
        builder.reserveFaceAndVertexCount(n_overlay_faces, n_overlay_faces * 3)
        builder.addFacesWithColor(overlay_verts, overlay_indices, overlay_colors)

        mesh = builder.build()
        return mesh

    def _classify_faces(
        self,
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        candidate_face_ids: numpy.ndarray,
        candidate_regions: "CandidateRegions",
        collision_result: Optional["CollisionResult"],
        height_map,
    ) -> numpy.ndarray:
        """Assign a colour to each candidate face.

        Returns
        -------
        numpy.ndarray
            ``(K, 4)`` RGBA colour array, one row per candidate face.
        """

        n_faces = candidate_face_ids.size
        colors = numpy.tile(COLOR_SAFE, (n_faces, 1))  # default: green

        if collision_result is None:
            # No collision data -- everything is optimistically green.
            return colors

        # Try to obtain a per-face safety mask from the collision result.
        safe_mask = self._get_per_face_safety(
            vertices, indices, candidate_face_ids,
            collision_result, height_map,
        )

        if safe_mask is None:
            # Cannot determine safety; leave everything green.
            return colors

        # Classify each face.
        for local_idx in range(n_faces):
            face_id = candidate_face_ids[local_idx]
            tri = indices[face_id]

            # A face is safe if ALL its vertices map to safe cells.
            vertex_safe = safe_mask[tri]

            if numpy.all(vertex_safe):
                colors[local_idx] = COLOR_SAFE
            elif numpy.any(vertex_safe):
                colors[local_idx] = COLOR_BORDERLINE
            else:
                colors[local_idx] = COLOR_REJECTED

        return colors

    def _get_per_face_safety(
        self,
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        candidate_face_ids: numpy.ndarray,
        collision_result: "CollisionResult",
        height_map,
    ) -> Optional[numpy.ndarray]:
        """Build a per-vertex boolean safety mask.

        Uses the collision result's ``safe_map`` and the height map's
        ``get_grid_coords`` to map each vertex to a grid cell and check
        safety.

        Returns
        -------
        numpy.ndarray or None
            ``(N,)`` boolean array (True = safe) indexed by vertex
            index, or ``None`` if the necessary data is not available.
        """

        safe_map = getattr(collision_result, "safe_map", None)
        if safe_map is None:
            logger.debug("CollisionResult has no safe_map attribute")
            return None
        if height_map is None:
            logger.debug("No height_map provided; cannot map vertices to grid")
            return None

        safe_map = numpy.asarray(safe_map, dtype=bool)
        n_verts = vertices.shape[0]
        vertex_safe = numpy.zeros(n_verts, dtype=bool)

        # Determine which vertices are actually referenced by candidate faces
        # to avoid processing the entire mesh.
        referenced = numpy.unique(indices[candidate_face_ids].ravel())

        for vi in referenced:
            wx, wy = float(vertices[vi, 0]), float(vertices[vi, 1])

            if not height_map.is_valid(wx, wy):
                continue
            row, col = height_map.get_grid_coords(wx, wy)

            if (
                0 <= row < safe_map.shape[0]
                and 0 <= col < safe_map.shape[1]
            ):
                vertex_safe[vi] = safe_map[row, col]

        return vertex_safe

    @staticmethod
    def _compute_face_normals(
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        face_ids: numpy.ndarray,
    ) -> numpy.ndarray:
        """Compute unit normals for the specified faces.

        Parameters
        ----------
        vertices:
            ``(N, 3)`` vertex positions.
        indices:
            ``(M, 3)`` face indices.
        face_ids:
            ``(K,)`` subset of face indices to compute normals for.

        Returns
        -------
        numpy.ndarray
            ``(K, 3)`` unit normals.  Degenerate triangles get a
            fallback normal of ``(0, 1, 0)``.
        """

        tris = indices[face_ids]  # (K, 3)
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

        # Replace degenerate normals with an upward-facing default.
        normals[degenerate] = numpy.array([0.0, 1.0, 0.0])

        return normals
