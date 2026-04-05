# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

import numpy


def subdivide(vertices: numpy.ndarray, indices: numpy.ndarray, levels: int = 1) -> tuple:
    """Perform midpoint subdivision on a triangle mesh.

    Each triangle is split into 4 sub-triangles by inserting midpoints on each edge.
    Shared edges produce shared midpoints (no duplicate vertices).

    :param vertices: (N, 3) float32 array of vertex positions.
    :param indices: (M, 3) int32 array of triangle indices.
    :param levels: Number of subdivision iterations (each multiplies face count by 4).
    :return: Tuple of (new_vertices, new_indices).
    """
    for _ in range(levels):
        vertices, indices = _subdivide_once(vertices, indices)
    return vertices, indices


def _subdivide_once(vertices: numpy.ndarray, indices: numpy.ndarray) -> tuple:
    """Single level of midpoint subdivision."""
    num_verts = len(vertices)
    edge_midpoints = {}  # (min_idx, max_idx) -> midpoint_vertex_index
    new_verts_list = []
    next_idx = num_verts

    new_indices = numpy.empty((len(indices) * 4, 3), dtype=numpy.int32)

    for i, tri in enumerate(indices):
        v0, v1, v2 = int(tri[0]), int(tri[1]), int(tri[2])

        # Get or create midpoints for each edge
        m01 = _get_or_create_midpoint(edge_midpoints, new_verts_list, vertices, v0, v1, next_idx)
        if m01 >= next_idx:
            next_idx += 1
        m12 = _get_or_create_midpoint(edge_midpoints, new_verts_list, vertices, v1, v2, next_idx)
        if m12 >= next_idx:
            next_idx += 1
        m02 = _get_or_create_midpoint(edge_midpoints, new_verts_list, vertices, v0, v2, next_idx)
        if m02 >= next_idx:
            next_idx += 1

        # Create 4 sub-triangles
        base = i * 4
        new_indices[base] = [v0, m01, m02]
        new_indices[base + 1] = [m01, v1, m12]
        new_indices[base + 2] = [m02, m12, v2]
        new_indices[base + 3] = [m01, m12, m02]

    if new_verts_list:
        new_verts_array = numpy.array(new_verts_list, dtype=numpy.float32)
        all_vertices = numpy.vstack([vertices, new_verts_array])
    else:
        all_vertices = vertices

    return all_vertices, new_indices


def _get_or_create_midpoint(
    edge_midpoints: dict,
    new_verts_list: list,
    vertices: numpy.ndarray,
    v_a: int,
    v_b: int,
    next_idx: int
) -> int:
    """Get existing midpoint for an edge, or create a new one."""
    key = (min(v_a, v_b), max(v_a, v_b))
    if key in edge_midpoints:
        return edge_midpoints[key]

    midpoint = (vertices[v_a] + vertices[v_b]) * 0.5
    new_verts_list.append(midpoint)
    edge_midpoints[key] = next_idx
    return next_idx
