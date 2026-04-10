# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

"""Per-face mask with adjacency-based flood fill for BumpMesh face painting.

Design:
- A FaceMask stores a per-face float32 array where 1.0 = displacement active,
  0.0 = excluded (no displacement). Intermediate values are not used in V1.
- Adjacency is built lazily from the mesh indices by finding shared edges.
- Bucket fill uses BFS across face-face neighbors, stopping when the dihedral
  angle between neighbors exceeds a threshold (preventing flood across creases).
- Per-vertex weights are derived from the face mask for use in the displacement
  pipeline (each vertex weight = average of adjacent face weights).
"""

from typing import Optional

import numpy


class FaceMask:
    """Per-face mask with adjacency for flood fill operations."""

    def __init__(self, face_count: int) -> None:
        # 1.0 = active (displace), 0.0 = excluded
        self._mask: numpy.ndarray = numpy.ones(face_count, dtype=numpy.float32)
        self._adjacency: Optional[numpy.ndarray] = None  # (M, 3) int32 neighbors or -1
        self._face_normals: Optional[numpy.ndarray] = None

    @property
    def face_count(self) -> int:
        return len(self._mask)

    @property
    def mask(self) -> numpy.ndarray:
        return self._mask

    def is_active(self, face_id: int) -> bool:
        return 0 <= face_id < len(self._mask) and self._mask[face_id] > 0.5

    def has_any_excluded(self) -> bool:
        return bool((self._mask < 0.5).any())

    def has_any_active(self) -> bool:
        return bool((self._mask > 0.5).any())

    def clear(self) -> None:
        """Reset: all faces active (no exclusion)."""
        self._mask.fill(1.0)

    def invert(self) -> None:
        """Invert the mask: excluded becomes active and vice versa."""
        self._mask = 1.0 - self._mask

    def set_all_excluded(self) -> None:
        """Mark all faces as excluded (for Include Only mode starting state)."""
        self._mask.fill(0.0)

    # --- Painting operations ---

    def exclude_face(self, face_id: int) -> None:
        """Mark a single face as excluded from displacement."""
        if 0 <= face_id < len(self._mask):
            self._mask[face_id] = 0.0

    def include_face(self, face_id: int) -> None:
        """Mark a single face as active (erase exclusion)."""
        if 0 <= face_id < len(self._mask):
            self._mask[face_id] = 1.0

    def toggle_face(self, face_id: int) -> None:
        """Toggle a face's mask state."""
        if 0 <= face_id < len(self._mask):
            self._mask[face_id] = 1.0 - self._mask[face_id]

    # --- Bucket fill ---

    def bucket_fill(
        self,
        seed_face: int,
        set_value: float,
        vertices: numpy.ndarray,
        indices: numpy.ndarray,
        angle_threshold_deg: float = 30.0,
    ) -> int:
        """Flood fill from seed face, stopping at sharp edges.

        :param seed_face: Starting face index.
        :param set_value: Target mask value (0.0 to exclude, 1.0 to include).
        :param vertices: (N, 3) mesh vertices (for normal computation).
        :param indices: (M, 3) mesh indices (for adjacency).
        :param angle_threshold_deg: Max dihedral angle to cross during fill.
        :return: Number of faces affected.
        """
        if not (0 <= seed_face < len(self._mask)):
            return 0

        if self._adjacency is None or self._face_normals is None:
            self._build_adjacency_and_normals(vertices, indices)

        cos_threshold = numpy.cos(numpy.radians(angle_threshold_deg))

        visited = numpy.zeros(len(self._mask), dtype=bool)
        visited[seed_face] = True
        queue = [seed_face]
        count = 0

        adjacency = self._adjacency
        face_normals = self._face_normals

        while queue:
            f = queue.pop()
            self._mask[f] = set_value
            count += 1

            my_normal = face_normals[f]
            for neighbor in adjacency[f]:
                if neighbor < 0 or visited[neighbor]:
                    continue
                # Check dihedral angle (dot of face normals)
                neighbor_normal = face_normals[neighbor]
                dot = float(numpy.dot(my_normal, neighbor_normal))
                if dot >= cos_threshold:
                    visited[neighbor] = True
                    queue.append(neighbor)

        return count

    # --- Per-vertex weights for displacement pipeline ---

    def compute_vertex_weights(self, indices: numpy.ndarray, num_vertices: int) -> numpy.ndarray:
        """Convert per-face mask to per-vertex weights [0, 1].

        A vertex weight is the average of its adjacent face mask values.
        This gives smooth transitions at mask boundaries.

        :param indices: (M, 3) int32 triangle indices (must match self._mask length).
        :param num_vertices: Total number of vertices in the mesh.
        :return: (num_vertices,) float32 weights.
        """
        # Accumulate mask values per vertex
        weight_sum = numpy.zeros(num_vertices, dtype=numpy.float64)
        weight_count = numpy.zeros(num_vertices, dtype=numpy.float64)

        flat_indices = indices.ravel()  # (M*3,)
        face_mask_repeated = numpy.repeat(self._mask, 3)  # (M*3,) — each face mask 3x
        ones = numpy.ones_like(face_mask_repeated)

        numpy.add.at(weight_sum, flat_indices, face_mask_repeated)
        numpy.add.at(weight_count, flat_indices, ones)

        weight_count = numpy.where(weight_count < 0.5, 1.0, weight_count)
        return (weight_sum / weight_count).astype(numpy.float32)

    # --- Internal: adjacency construction ---

    def _build_adjacency_and_normals(
        self, vertices: numpy.ndarray, indices: numpy.ndarray
    ) -> None:
        """Build face-to-face adjacency via shared edges and compute face normals.

        For each face, stores the indices of its (up to 3) edge-adjacent neighbors.
        """
        num_faces = len(indices)

        # Compute face normals (unit length)
        v0 = vertices[indices[:, 0]]
        v1 = vertices[indices[:, 1]]
        v2 = vertices[indices[:, 2]]
        fn = numpy.cross(v1 - v0, v2 - v0)
        fn_lengths = numpy.linalg.norm(fn, axis=1, keepdims=True)
        fn_lengths = numpy.where(fn_lengths < 1e-8, 1.0, fn_lengths)
        self._face_normals = (fn / fn_lengths).astype(numpy.float32)

        # Build edge list: for each face, 3 sorted edges
        edges = numpy.stack([
            numpy.sort(indices[:, [0, 1]], axis=1),
            numpy.sort(indices[:, [1, 2]], axis=1),
            numpy.sort(indices[:, [0, 2]], axis=1),
        ], axis=1)  # (M, 3, 2)

        flat_edges = edges.reshape(-1, 2)  # (M*3, 2)
        face_of_edge = numpy.repeat(numpy.arange(num_faces, dtype=numpy.int32), 3)  # (M*3,)

        # Find pairs of faces that share an edge
        # Sort by edge so identical edges are adjacent
        edge_keys = flat_edges[:, 0].astype(numpy.int64) * (2**32) + flat_edges[:, 1].astype(numpy.int64)
        order = numpy.argsort(edge_keys)
        sorted_keys = edge_keys[order]
        sorted_faces = face_of_edge[order]

        # Adjacent faces: where consecutive sorted_keys are equal
        adjacency = numpy.full((num_faces, 3), -1, dtype=numpy.int32)
        adj_count = numpy.zeros(num_faces, dtype=numpy.int32)

        # Find matching pairs
        matches = sorted_keys[:-1] == sorted_keys[1:]
        match_idx = numpy.where(matches)[0]

        for i in match_idx:
            fa = int(sorted_faces[i])
            fb = int(sorted_faces[i + 1])
            if adj_count[fa] < 3:
                adjacency[fa, adj_count[fa]] = fb
                adj_count[fa] += 1
            if adj_count[fb] < 3:
                adjacency[fb, adj_count[fb]] = fa
                adj_count[fb] += 1

        self._adjacency = adjacency
