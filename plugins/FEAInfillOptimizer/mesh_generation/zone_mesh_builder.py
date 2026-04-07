# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from typing import List, Tuple

import numpy

from UM.Mesh.MeshBuilder import MeshBuilder
from UM.Mesh.MeshData import MeshData

from ..fea.tetrahedralization import TetMesh

# The four triangular faces of a tetrahedron, each defined as a triple of
# local node indices (0-3).
_TET_FACES: Tuple[Tuple[int, int, int], ...] = (
    (0, 1, 2),
    (0, 1, 3),
    (0, 2, 3),
    (1, 2, 3),
)

# Precomputed as numpy array for vectorized face extraction
_TET_FACES_ARR = numpy.array(_TET_FACES, dtype=numpy.int32)  # (4, 3)


def build_zone_mesh(tet_mesh: TetMesh, element_indices: List[int]) -> MeshData:
    """Build a surface mesh for a subset of tetrahedra in ``tet_mesh``.

    A face of a tetrahedron is a *boundary face* of the zone if it is not
    shared with any other element that belongs to the same zone.  Only
    boundary faces are included in the returned surface mesh.

    Uses vectorized numpy operations for face extraction and counting:
    ~10-50x faster than per-element Python loops for large zones.

    Args:
        tet_mesh: The full tetrahedral mesh (nodes + connectivity).
        element_indices: Indices of the tet elements that belong to this zone.

    Returns:
        A ``MeshData`` object containing only the triangular boundary faces of
        the zone, with normals computed automatically.
    """
    if not element_indices:
        builder = MeshBuilder()
        builder.setVertices(numpy.zeros((0, 3), dtype=numpy.float32))
        builder.setIndices(numpy.zeros((0, 3), dtype=numpy.int32))
        return builder.build()

    elem_idx_arr = numpy.array(element_indices, dtype=numpy.int64)
    zone_elements = tet_mesh.elements[elem_idx_arr]  # (Z, 4) global node indices

    # Extract all 4 faces per element: (Z, 4, 3) → reshape to (Z*4, 3)
    # _TET_FACES_ARR[f] gives local indices for face f → gather global nodes
    all_faces = zone_elements[:, _TET_FACES_ARR]  # (Z, 4, 3) global node indices
    n_zone = len(elem_idx_arr)
    all_faces_flat = all_faces.reshape(n_zone * 4, 3)  # (Z*4, 3)

    # Store original winding order before sorting for key comparison
    winding_faces = all_faces_flat.copy()

    # Sort each face's node indices to create canonical keys
    sorted_faces = numpy.sort(all_faces_flat, axis=1)  # (Z*4, 3)

    # Find boundary faces: faces that appear exactly once.
    # Use structured array for efficient unique counting.
    sorted_view = sorted_faces.view(
        dtype=[('a', sorted_faces.dtype), ('b', sorted_faces.dtype), ('c', sorted_faces.dtype)]
    ).reshape(-1)

    _, inverse, counts = numpy.unique(sorted_view, return_inverse=True, return_counts=True)

    # Boundary faces: count == 1
    boundary_mask = counts[inverse] == 1
    boundary_winding = winding_faces[boundary_mask]  # (B, 3) in original winding order

    if len(boundary_winding) == 0:
        builder = MeshBuilder()
        builder.setVertices(numpy.zeros((0, 3), dtype=numpy.float32))
        builder.setIndices(numpy.zeros((0, 3), dtype=numpy.int32))
        return builder.build()

    # Build compact vertex list: unique global node indices → local indices
    unique_nodes, local_indices = numpy.unique(boundary_winding, return_inverse=True)
    local_faces = local_indices.reshape(-1, 3)  # (B, 3) local face indices

    verts = tet_mesh.nodes[unique_nodes]  # (U, 3) vertex positions

    builder = MeshBuilder()
    builder.setVertices(numpy.asarray(verts, dtype=numpy.float32))
    builder.setIndices(numpy.asarray(local_faces, dtype=numpy.int32))
    builder.calculateNormals()
    return builder.build()
