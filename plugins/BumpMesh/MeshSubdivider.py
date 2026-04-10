# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

from typing import Optional

import numpy

# Safety cap for adaptive subdivision
_MAX_ADAPTIVE_TRIANGLES = 10_000_000


def subdivide(
    vertices: numpy.ndarray,
    indices: numpy.ndarray,
    levels: int = 1,
    vertex_attr: Optional[numpy.ndarray] = None,
):
    """Perform uniform midpoint subdivision on a triangle mesh.

    Each triangle is split into 4 sub-triangles by inserting midpoints on each edge.
    Shared edges produce shared midpoints (no duplicate vertices).

    :param vertices: (N, 3) float32 array of vertex positions.
    :param indices: (M, 3) int32 array of triangle indices.
    :param levels: Number of subdivision iterations (each multiplies face count by 4).
    :param vertex_attr: Optional (N,) or (N, K) per-vertex attribute that will be
        averaged at midpoints (e.g., paint mask weights).
    :return: (vertices, indices) or (vertices, indices, vertex_attr) if attr provided.
    """
    for _ in range(levels):
        if vertex_attr is None:
            vertices, indices = _subdivide_once(vertices, indices)
        else:
            vertices, indices, vertex_attr = _subdivide_once_with_attr(
                vertices, indices, vertex_attr
            )

    if vertex_attr is None:
        return vertices, indices
    return vertices, indices, vertex_attr


def _subdivide_once_with_attr(
    vertices: numpy.ndarray, indices: numpy.ndarray, vertex_attr: numpy.ndarray
):
    """Single uniform subdivision pass that also interpolates a vertex attribute."""
    num_verts = len(vertices)
    num_faces = len(indices)

    edge_pairs = numpy.stack([
        indices[:, [0, 1]],
        indices[:, [1, 2]],
        indices[:, [0, 2]],
    ], axis=1)
    edge_pairs = numpy.sort(edge_pairs, axis=2)
    all_edges = edge_pairs.reshape(-1, 2)

    unique_edges, edge_inverse = numpy.unique(all_edges, axis=0, return_inverse=True)

    midpoints = (vertices[unique_edges[:, 0]] + vertices[unique_edges[:, 1]]) * 0.5
    # Interpolate the vertex attribute at midpoints (average of endpoints)
    midpoint_attrs = (vertex_attr[unique_edges[:, 0]] + vertex_attr[unique_edges[:, 1]]) * 0.5

    midpoint_global_indices = (num_verts + edge_inverse).astype(numpy.int32)
    face_midpoints = midpoint_global_indices.reshape(num_faces, 3)

    m01 = face_midpoints[:, 0]
    m12 = face_midpoints[:, 1]
    m02 = face_midpoints[:, 2]

    v0 = indices[:, 0]
    v1 = indices[:, 1]
    v2 = indices[:, 2]

    new_indices = numpy.empty((num_faces * 4, 3), dtype=numpy.int32)
    new_indices[0::4] = numpy.column_stack([v0, m01, m02])
    new_indices[1::4] = numpy.column_stack([m01, v1, m12])
    new_indices[2::4] = numpy.column_stack([m02, m12, v2])
    new_indices[3::4] = numpy.column_stack([m01, m12, m02])

    all_vertices = numpy.vstack([vertices, midpoints.astype(vertices.dtype)])
    all_attrs = numpy.concatenate([vertex_attr, midpoint_attrs.astype(vertex_attr.dtype)])

    return all_vertices, new_indices, all_attrs


def subdivide_adaptive(
    vertices: numpy.ndarray,
    indices: numpy.ndarray,
    target_edge_length: float,
    max_triangles: int = _MAX_ADAPTIVE_TRIANGLES,
    max_iterations: int = 20
) -> tuple:
    """Perform adaptive subdivision based on target edge length.

    Only splits edges that exceed the target length. Per-triangle, the number of
    flagged edges (0/1/2/3) determines the split pattern:
      0 flagged: keep unchanged
      1 flagged: split into 2 triangles
      2 flagged: split into 3 triangles
      3 flagged: split into 4 triangles (standard midpoint)

    :param vertices: (N, 3) float32 array of vertex positions.
    :param indices: (M, 3) int32 array of triangle indices.
    :param target_edge_length: Maximum edge length in mm before splitting.
    :param max_triangles: Safety cap on total triangle count.
    :param max_iterations: Maximum subdivision passes.
    :return: Tuple of (new_vertices, new_indices).
    """
    for _ in range(max_iterations):
        if len(indices) >= max_triangles:
            break

        vertices, indices, any_split = _subdivide_adaptive_once(
            vertices, indices, target_edge_length, max_triangles
        )
        if not any_split:
            break

    return vertices, indices


def _subdivide_once(vertices: numpy.ndarray, indices: numpy.ndarray) -> tuple:
    """Single level of uniform midpoint subdivision (fully vectorized)."""
    num_verts = len(vertices)
    num_faces = len(indices)

    edge_pairs = numpy.stack([
        indices[:, [0, 1]],
        indices[:, [1, 2]],
        indices[:, [0, 2]],
    ], axis=1)  # (M, 3, 2)

    edge_pairs = numpy.sort(edge_pairs, axis=2)
    all_edges = edge_pairs.reshape(-1, 2)

    unique_edges, edge_inverse = numpy.unique(all_edges, axis=0, return_inverse=True)

    midpoints = (vertices[unique_edges[:, 0]] + vertices[unique_edges[:, 1]]) * 0.5

    midpoint_global_indices = (num_verts + edge_inverse).astype(numpy.int32)
    face_midpoints = midpoint_global_indices.reshape(num_faces, 3)

    m01 = face_midpoints[:, 0]
    m12 = face_midpoints[:, 1]
    m02 = face_midpoints[:, 2]

    v0 = indices[:, 0]
    v1 = indices[:, 1]
    v2 = indices[:, 2]

    new_indices = numpy.empty((num_faces * 4, 3), dtype=numpy.int32)
    new_indices[0::4] = numpy.column_stack([v0, m01, m02])
    new_indices[1::4] = numpy.column_stack([m01, v1, m12])
    new_indices[2::4] = numpy.column_stack([m02, m12, v2])
    new_indices[3::4] = numpy.column_stack([m01, m12, m02])

    all_vertices = numpy.vstack([vertices, midpoints.astype(numpy.float32)])

    return all_vertices, new_indices


def _subdivide_adaptive_once(
    vertices: numpy.ndarray,
    indices: numpy.ndarray,
    target_edge_length: float,
    max_triangles: int
) -> tuple:
    """Single pass of adaptive subdivision using global edge marking.

    Edges are marked globally so that shared edges between adjacent triangles
    always agree on whether to split, preventing T-junctions (cracks).

    Returns (new_vertices, new_indices, any_split).
    """
    num_verts = len(vertices)
    num_faces = len(indices)

    # ----------------------------------------------------------------
    # Step 1: Build ALL unique edges from the mesh (same pattern as
    # _subdivide_once).  edge_inverse maps each of the M*3 per-face
    # edge slots back to the unique-edge index.
    # ----------------------------------------------------------------
    edge_pairs = numpy.stack([
        indices[:, [0, 1]],
        indices[:, [1, 2]],
        indices[:, [0, 2]],
    ], axis=1)  # (M, 3, 2)

    sorted_edges = numpy.sort(edge_pairs, axis=2)
    all_edges = sorted_edges.reshape(-1, 2)  # (M*3, 2)

    unique_edges, edge_inverse = numpy.unique(
        all_edges, axis=0, return_inverse=True
    )  # unique_edges: (U, 2),  edge_inverse: (M*3,)

    # ----------------------------------------------------------------
    # Step 2: Compute the length of each *unique* edge (once per edge,
    # not once per triangle half-edge).
    # ----------------------------------------------------------------
    unique_edge_vectors = (
        vertices[unique_edges[:, 1]] - vertices[unique_edges[:, 0]]
    )
    unique_edge_lengths = numpy.linalg.norm(unique_edge_vectors, axis=1)  # (U,)

    # ----------------------------------------------------------------
    # Step 3: Mark unique edges exceeding target_edge_length.
    # ----------------------------------------------------------------
    unique_edge_marked = unique_edge_lengths > target_edge_length  # (U,) bool

    if not unique_edge_marked.any():
        return vertices, indices, False

    # ----------------------------------------------------------------
    # Step 4: Map the global marks back to per-triangle flags via
    # edge_inverse.  After reshape, flags[i, j] tells whether the
    # j-th edge of triangle i is marked for splitting.
    # ----------------------------------------------------------------
    flags = unique_edge_marked[edge_inverse].reshape(num_faces, 3)  # (M, 3) bool
    flag_counts = flags.sum(axis=1)  # (M,) — 0, 1, 2, or 3

    # Estimate output triangle count
    output_estimate = int(
        (flag_counts == 0).sum() +
        (flag_counts == 1).sum() * 2 +
        (flag_counts == 2).sum() * 3 +
        (flag_counts == 3).sum() * 4
    )
    if output_estimate > max_triangles:
        return vertices, indices, False

    # ----------------------------------------------------------------
    # Step 5: Compute midpoints for every marked unique edge, then
    # build the per-face midpoint-index table.
    # ----------------------------------------------------------------
    # We only need midpoints for the marked unique edges.  Build a
    # compact midpoint array and a mapping from unique-edge-index to
    # new vertex index (or -1 if not marked).

    marked_unique_indices = numpy.where(unique_edge_marked)[0]  # indices into unique_edges
    midpoints = (
        vertices[unique_edges[marked_unique_indices, 0]] +
        vertices[unique_edges[marked_unique_indices, 1]]
    ) * 0.5  # (num_marked, 3)

    # Map from unique-edge-index -> new-vertex global index (-1 if unmarked)
    unique_to_midpoint = numpy.full(len(unique_edges), -1, dtype=numpy.int32)
    unique_to_midpoint[marked_unique_indices] = numpy.arange(
        num_verts, num_verts + len(marked_unique_indices), dtype=numpy.int32
    )

    # Per-face midpoint indices via edge_inverse: (M*3,) -> reshape (M, 3)
    face_midpoints = unique_to_midpoint[edge_inverse].reshape(num_faces, 3)

    # ----------------------------------------------------------------
    # Step 6: Per-triangle rebuild grouped by flag count (0/1/2/3).
    # ----------------------------------------------------------------
    new_tris_list = []

    # Group 0: unchanged triangles
    mask_0 = flag_counts == 0
    if mask_0.any():
        new_tris_list.append(indices[mask_0])

    # Group 3: full midpoint subdivision (4 sub-tris)
    mask_3 = flag_counts == 3
    if mask_3.any():
        tri3 = indices[mask_3]
        fm3 = face_midpoints[mask_3]
        v0, v1, v2 = tri3[:, 0], tri3[:, 1], tri3[:, 2]
        m01, m12, m02 = fm3[:, 0], fm3[:, 1], fm3[:, 2]
        t0 = numpy.column_stack([v0, m01, m02])
        t1 = numpy.column_stack([m01, v1, m12])
        t2 = numpy.column_stack([m02, m12, v2])
        t3 = numpy.column_stack([m01, m12, m02])
        new_tris_list.extend([t0, t1, t2, t3])

    # Group 1: split into 2 (split the single flagged edge)
    mask_1 = flag_counts == 1
    if mask_1.any():
        tri1 = indices[mask_1]
        fm1 = face_midpoints[mask_1]
        fl1 = flags[mask_1]  # (K, 3) bool — exactly one True per row

        # Find which edge is flagged (0=e01, 1=e12, 2=e02)
        flagged_edge_idx = numpy.argmax(fl1, axis=1)

        for edge_idx in range(3):
            sel = flagged_edge_idx == edge_idx
            if not sel.any():
                continue
            t = tri1[sel]
            fm = fm1[sel]
            v0, v1, v2 = t[:, 0], t[:, 1], t[:, 2]
            mid = fm[:, edge_idx]

            if edge_idx == 0:  # e01 split: mid on v0-v1
                new_tris_list.append(numpy.column_stack([v0, mid, v2]))
                new_tris_list.append(numpy.column_stack([mid, v1, v2]))
            elif edge_idx == 1:  # e12 split: mid on v1-v2
                new_tris_list.append(numpy.column_stack([v0, v1, mid]))
                new_tris_list.append(numpy.column_stack([v0, mid, v2]))
            else:  # e02 split: mid on v0-v2
                new_tris_list.append(numpy.column_stack([v0, v1, mid]))
                new_tris_list.append(numpy.column_stack([v1, v2, mid]))

    # Group 2: split into 3 (two edges flagged — fan from vertex opposite
    # the unsplit edge)
    mask_2 = flag_counts == 2
    if mask_2.any():
        tri2 = indices[mask_2]
        fm2 = face_midpoints[mask_2]
        fl2 = flags[mask_2]

        # Find the unflagged edge (0=e01, 1=e12, 2=e02)
        unflagged_edge_idx = numpy.argmin(fl2.astype(numpy.int32), axis=1)

        for unsplit_idx in range(3):
            sel = unflagged_edge_idx == unsplit_idx
            if not sel.any():
                continue
            t = tri2[sel]
            fm = fm2[sel]
            v0, v1, v2 = t[:, 0], t[:, 1], t[:, 2]

            if unsplit_idx == 0:  # e01 unsplit, e12 and e02 split
                m12, m02 = fm[:, 1], fm[:, 2]
                new_tris_list.append(numpy.column_stack([v0, v1, m12]))
                new_tris_list.append(numpy.column_stack([v0, m12, m02]))
                new_tris_list.append(numpy.column_stack([m12, v2, m02]))
            elif unsplit_idx == 1:  # e12 unsplit, e01 and e02 split
                m01, m02 = fm[:, 0], fm[:, 2]
                new_tris_list.append(numpy.column_stack([v0, m01, m02]))
                new_tris_list.append(numpy.column_stack([m01, v1, m02]))
                new_tris_list.append(numpy.column_stack([v1, v2, m02]))
            else:  # e02 unsplit, e01 and e12 split
                m01, m12 = fm[:, 0], fm[:, 1]
                new_tris_list.append(numpy.column_stack([v0, m01, m12]))
                new_tris_list.append(numpy.column_stack([m01, v1, m12]))
                new_tris_list.append(numpy.column_stack([v0, m12, v2]))

    new_indices = numpy.vstack(new_tris_list).astype(numpy.int32)
    new_vertices = numpy.vstack([vertices, midpoints.astype(numpy.float32)])

    return new_vertices, new_indices, True
