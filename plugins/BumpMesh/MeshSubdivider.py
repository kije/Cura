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
    """Single level of midpoint subdivision (fully vectorized)."""
    num_verts = len(vertices)
    num_faces = len(indices)

    # Build all 3 edges per face, each sorted so smaller index comes first
    # Edge order per face: (v0,v1), (v1,v2), (v0,v2)
    edge_pairs = numpy.stack([
        indices[:, [0, 1]],
        indices[:, [1, 2]],
        indices[:, [0, 2]],
    ], axis=1)  # (M, 3, 2)

    # Sort each edge so (min, max) for deduplication
    edge_pairs = numpy.sort(edge_pairs, axis=2)
    all_edges = edge_pairs.reshape(-1, 2)  # (M*3, 2)

    # Find unique edges and map each of the M*3 edges back to its unique index
    unique_edges, edge_inverse = numpy.unique(all_edges, axis=0, return_inverse=True)
    num_unique_edges = len(unique_edges)

    # Compute midpoints for each unique edge
    midpoints = (vertices[unique_edges[:, 0]] + vertices[unique_edges[:, 1]]) * 0.5

    # Assign new vertex indices starting after existing vertices
    # edge_inverse maps each of the M*3 edges to its unique edge index
    # Reshape to (M, 3) where columns correspond to edge01, edge12, edge02
    midpoint_global_indices = (num_verts + edge_inverse).astype(numpy.int32)
    face_midpoints = midpoint_global_indices.reshape(num_faces, 3)

    m01 = face_midpoints[:, 0]
    m12 = face_midpoints[:, 1]
    m02 = face_midpoints[:, 2]

    v0 = indices[:, 0]
    v1 = indices[:, 1]
    v2 = indices[:, 2]

    # Build 4 sub-triangles per face
    new_indices = numpy.empty((num_faces * 4, 3), dtype=numpy.int32)
    new_indices[0::4] = numpy.column_stack([v0, m01, m02])
    new_indices[1::4] = numpy.column_stack([m01, v1, m12])
    new_indices[2::4] = numpy.column_stack([m02, m12, v2])
    new_indices[3::4] = numpy.column_stack([m01, m12, m02])

    all_vertices = numpy.vstack([vertices, midpoints.astype(numpy.float32)])

    return all_vertices, new_indices
