# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from collections import defaultdict
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


def build_zone_mesh(tet_mesh: TetMesh, element_indices: List[int]) -> MeshData:
    """Build a surface mesh for a subset of tetrahedra in ``tet_mesh``.

    A face of a tetrahedron is a *boundary face* of the zone if it is not
    shared with any other element that belongs to the same zone.  Only
    boundary faces are included in the returned surface mesh.

    Args:
        tet_mesh: The full tetrahedral mesh (nodes + connectivity).
        element_indices: Indices of the tet elements that belong to this zone.

    Returns:
        A ``MeshData`` object containing only the triangular boundary faces of
        the zone, with normals computed automatically.
    """
    zone_set = set(element_indices)

    # Count how many zone-elements share each face.
    # Key: sorted tuple of three global node indices.
    # Value: (count, list_of_vertex_index_triples_in_winding_order)
    face_count: defaultdict[Tuple[int, int, int], int] = defaultdict(int)
    face_winding: dict[Tuple[int, int, int], Tuple[int, int, int]] = {}

    for elem_idx in element_indices:
        global_nodes = tet_mesh.elements[elem_idx]  # shape (4,)
        for local_face in _TET_FACES:
            a, b, c = (int(global_nodes[i]) for i in local_face)
            key: Tuple[int, int, int] = tuple(sorted((a, b, c)))  # type: ignore[assignment]
            face_count[key] += 1
            # Store first-seen winding order so outward orientation is consistent
            if key not in face_winding:
                face_winding[key] = (a, b, c)

    # Collect boundary faces (appear exactly once among zone elements)
    boundary_faces = [key for key, cnt in face_count.items() if cnt == 1]

    if not boundary_faces:
        # Return an empty mesh if no boundary faces (degenerate zone)
        builder = MeshBuilder()
        builder.setVertices(numpy.zeros((0, 3), dtype=numpy.float32))
        builder.setIndices(numpy.zeros((0, 3), dtype=numpy.int32))
        return builder.build()

    # Build a compact vertex list (only nodes referenced by boundary faces)
    global_to_local: dict[int, int] = {}
    verts: List[List[float]] = []

    indices: List[List[int]] = []
    for key in boundary_faces:
        a, b, c = face_winding[key]
        local_face_indices: List[int] = []
        for gnode in (a, b, c):
            if gnode not in global_to_local:
                global_to_local[gnode] = len(verts)
                verts.append(tet_mesh.nodes[gnode].tolist())
            local_face_indices.append(global_to_local[gnode])
        indices.append(local_face_indices)

    builder = MeshBuilder()
    builder.setVertices(numpy.asarray(verts, dtype=numpy.float32))
    builder.setIndices(numpy.asarray(indices, dtype=numpy.int32))
    builder.calculateNormals()
    return builder.build()
