"""Candidate region detection for non-planar slicing.

Identifies connected regions of mesh faces whose surface normals make them
suitable for non-planar printing -- faces that are "top surfaces" with
angles in a beneficial range -- then groups them into contiguous regions
via face-adjacency analysis.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import List

import numpy as np
from numpy.typing import NDArray

from .surface_analyzer import SurfaceAnalysis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidateRegion:
    """A single connected region of candidate faces.

    Attributes:
        face_indices: Sorted array of face indices belonging to this region.
        total_area: Sum of face areas in mm^2.
        bbox_min: (3,) lower corner of the axis-aligned bounding box.
        bbox_max: (3,) upper corner of the axis-aligned bounding box.
        max_z_range: Extent of the region along the Z axis (mm).
    """

    face_indices: NDArray[np.intp]
    total_area: float
    bbox_min: NDArray[np.floating]
    bbox_max: NDArray[np.floating]
    max_z_range: float


@dataclass(frozen=True)
class CandidateRegions:
    """Collection of all detected candidate regions.

    Attributes:
        regions: List of :class:`CandidateRegion` instances, sorted by
            descending total area.
        all_candidate_mask: (M,) boolean mask over all mesh faces; True
            for every face that belongs to *any* retained region.
    """

    regions: List[CandidateRegion] = field(default_factory=list)
    all_candidate_mask: NDArray[np.bool_] = field(
        default_factory=lambda: np.empty(0, dtype=np.bool_)
    )


def detect_candidates(
    analysis: SurfaceAnalysis,
    indices: NDArray[np.integer] | None,
    *,
    max_angle_deg: float = 30.0,
    min_benefit_angle_deg: float = 5.0,
    min_region_area_mm2: float = 100.0,
) -> CandidateRegions:
    """Detect connected regions of faces suitable for non-planar printing.

    Parameters
    ----------
    analysis:
        Result of :func:`surface_analyzer.analyze_mesh`.
    indices:
        (M, 3) triangle vertex-index array (same one passed to
        ``analyze_mesh``).  May be ``None`` for non-indexed meshes; in
        that case sequential vertex triplets are assumed, producing
        indices ``[[0,1,2],[3,4,5],...]``.
    max_angle_deg:
        Maximum angle from horizontal (Z-up) for a face to be a
        candidate.  Faces steeper than this are excluded.
    min_benefit_angle_deg:
        Minimum angle -- faces flatter than this gain little from
        non-planar printing and are excluded.
    min_region_area_mm2:
        Minimum total area for a connected region to be retained.

    Returns
    -------
    CandidateRegions
    """

    num_faces = analysis.face_normals.shape[0]
    logger.debug(
        "Detecting candidate regions: max_angle=%.1f deg, "
        "min_benefit=%.1f deg, min_area=%.1f mm^2",
        max_angle_deg,
        min_benefit_angle_deg,
        min_region_area_mm2,
    )

    # ---- build resolved index array ----
    if indices is not None:
        tri_indices = np.asarray(indices, dtype=np.intp)
        if tri_indices.shape[0] != num_faces:
            raise ValueError(
                f"Index array has {tri_indices.shape[0]} faces but analysis "
                f"has {num_faces}"
            )
    else:
        # Non-indexed: synthesize sequential indices.
        tri_indices = np.arange(num_faces * 3, dtype=np.intp).reshape(-1, 3)

    # ---- select candidate faces ----
    min_rad = np.radians(min_benefit_angle_deg)
    max_rad = np.radians(max_angle_deg)

    candidate_mask = (
        analysis.is_top_surface
        & (analysis.angles_from_horizontal >= min_rad)
        & (analysis.angles_from_horizontal <= max_rad)
    )

    candidate_face_ids = np.nonzero(candidate_mask)[0]
    logger.debug(
        "%d / %d faces pass angle filter", candidate_face_ids.size, num_faces
    )

    if candidate_face_ids.size == 0:
        return CandidateRegions(
            regions=[],
            all_candidate_mask=np.zeros(num_faces, dtype=np.bool_),
        )

    # ---- build face adjacency (among candidates only) ----
    adj = _build_adjacency(tri_indices, candidate_mask)

    # ---- find connected components via BFS ----
    components = _connected_components_bfs(candidate_face_ids, adj)

    # ---- build CandidateRegion objects, filter by area ----
    regions: List[CandidateRegion] = []
    for comp_ids in components:
        face_idx = np.sort(np.array(comp_ids, dtype=np.intp))
        total_area = float(np.sum(analysis.face_areas[face_idx]))
        if total_area < min_region_area_mm2:
            continue

        centers = analysis.face_centers[face_idx]
        bbox_min = centers.min(axis=0)
        bbox_max = centers.max(axis=0)
        z_range = float(bbox_max[2] - bbox_min[2])

        regions.append(
            CandidateRegion(
                face_indices=face_idx,
                total_area=total_area,
                bbox_min=bbox_min,
                bbox_max=bbox_max,
                max_z_range=z_range,
            )
        )

    # Sort regions by area (largest first).
    regions.sort(key=lambda r: r.total_area, reverse=True)

    # Build combined mask.
    all_mask = np.zeros(num_faces, dtype=np.bool_)
    for region in regions:
        all_mask[region.face_indices] = True

    logger.debug(
        "Detected %d candidate regions (after area filter), "
        "covering %d faces",
        len(regions),
        int(np.count_nonzero(all_mask)),
    )

    return CandidateRegions(regions=regions, all_candidate_mask=all_mask)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_adjacency(
    tri_indices: NDArray[np.intp],
    candidate_mask: NDArray[np.bool_],
) -> dict[int, list[int]]:
    """Build a face-adjacency dict for candidate faces only.

    Two faces are adjacent if they share exactly two vertex indices
    (i.e. they share an edge).

    Uses an edge-to-face mapping for efficiency.  We try to use
    ``scipy.sparse`` for the mapping; if unavailable we fall back to a
    pure-numpy / dict approach.

    Returns
    -------
    dict mapping each candidate face index to a list of adjacent
    candidate face indices.
    """

    candidate_ids = np.nonzero(candidate_mask)[0]
    candidate_set = set(candidate_ids.tolist())

    # Build edge -> list[face_id] mapping.
    # Each triangle has 3 edges; we represent each edge as a tuple of
    # sorted vertex indices so that ordering is canonical.
    edge_to_faces: dict[tuple[int, int], list[int]] = {}

    # Vectorised extraction of edge vertex pairs.
    # Edges: (v0,v1), (v1,v2), (v2,v0).
    v0 = tri_indices[candidate_ids, 0]
    v1 = tri_indices[candidate_ids, 1]
    v2 = tri_indices[candidate_ids, 2]

    edges_a = np.stack([v0, v1], axis=1)  # (K, 2)
    edges_b = np.stack([v1, v2], axis=1)
    edges_c = np.stack([v2, v0], axis=1)
    all_edges = np.concatenate([edges_a, edges_b, edges_c], axis=0)  # (3K, 2)
    all_edges.sort(axis=1)

    # Face id repeated for each of the 3 edges.
    face_ids_rep = np.tile(candidate_ids, 3)  # (3K,)

    # Group by edge -- use a dict keyed on (min_v, max_v).
    for idx in range(all_edges.shape[0]):
        key = (int(all_edges[idx, 0]), int(all_edges[idx, 1]))
        lst = edge_to_faces.get(key)
        if lst is None:
            edge_to_faces[key] = [int(face_ids_rep[idx])]
        else:
            lst.append(int(face_ids_rep[idx]))

    # Convert to adjacency list.
    adj: dict[int, list[int]] = {int(f): [] for f in candidate_ids}
    for faces_sharing_edge in edge_to_faces.values():
        if len(faces_sharing_edge) == 2:
            a, b = faces_sharing_edge
            adj[a].append(b)
            adj[b].append(a)
        elif len(faces_sharing_edge) > 2:
            # Non-manifold edge -- connect all pairs.
            for i in range(len(faces_sharing_edge)):
                for j in range(i + 1, len(faces_sharing_edge)):
                    adj[faces_sharing_edge[i]].append(faces_sharing_edge[j])
                    adj[faces_sharing_edge[j]].append(faces_sharing_edge[i])

    return adj


def _connected_components_bfs(
    face_ids: NDArray[np.intp],
    adj: dict[int, list[int]],
) -> list[list[int]]:
    """Find connected components among *face_ids* using BFS.

    Returns a list of components, each being a list of face indices.
    """

    visited: set[int] = set()
    components: list[list[int]] = []

    for start in face_ids:
        start_int = int(start)
        if start_int in visited:
            continue

        component: list[int] = []
        queue: deque[int] = deque([start_int])
        visited.add(start_int)

        while queue:
            node = queue.popleft()
            component.append(node)
            for neighbour in adj.get(node, []):
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(neighbour)

        components.append(component)

    return components
