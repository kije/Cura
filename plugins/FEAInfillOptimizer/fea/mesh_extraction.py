# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Extract mesh data from a CuraSceneNode into a trimesh.Trimesh for FEA."""

import numpy as np

try:
    import trimesh
except ImportError:
    trimesh = None  # type: ignore[assignment]

from UM.Logger import Logger
from UM.Math.Matrix import Matrix


def extract_trimesh(node) -> "trimesh.Trimesh":
    """Convert a CuraSceneNode's MeshData to a repaired trimesh.Trimesh.

    Applies the node's world transformation to bring vertices into world space,
    then runs trimesh repair routines (fix_normals, fill_holes) to ensure a
    clean, watertight surface mesh suitable for tetrahedralization.

    Args:
        node: A CuraSceneNode with valid MeshData (vertices must be non-None).

    Returns:
        A trimesh.Trimesh in world coordinates with repairs applied.

    Raises:
        ImportError: If trimesh is not installed.
        ValueError: If the node has no mesh data or no vertices.
    """
    if trimesh is None:
        raise ImportError("trimesh is required but not installed.")

    mesh_data = node.getMeshData()
    if mesh_data is None:
        raise ValueError(f"Node '{node.getName()}' has no MeshData.")

    vertices = mesh_data.getVertices()
    if vertices is None:
        raise ValueError(f"Node '{node.getName()}' MeshData has no vertices.")

    # getVertices() returns shape (N, 3) as float32 — promote to float64
    vertices = np.array(vertices, dtype=np.float64)

    # Build face index array
    indices = mesh_data.getIndices()
    if indices is not None:
        # Indexed mesh: shape (M, 3) of int32
        faces = np.array(indices, dtype=np.int64).reshape(-1, 3)
    else:
        # Non-indexed: each consecutive triple of vertices is a face
        n_verts = vertices.shape[0]
        if n_verts % 3 != 0:
            raise ValueError(
                f"Non-indexed mesh vertex count ({n_verts}) is not divisible by 3."
            )
        faces = np.arange(n_verts, dtype=np.int64).reshape(-1, 3)

    # Apply world transformation
    world_transform: Matrix = node.getWorldTransformation()
    # getData() returns a row-major 4×4 ndarray (standard math convention):
    # translation is in column 3, rotation/scale in the upper-left 3×3.
    transform_matrix = np.array(world_transform.getData(), dtype=np.float64)

    # Homogeneous coordinates: (N, 4)
    ones = np.ones((vertices.shape[0], 1), dtype=np.float64)
    verts_h = np.hstack([vertices, ones])
    # (M @ v_h.T).T applies the 4×4 transform to each column-vector v_h[i],
    # producing transformed row-vectors. Correct for the row-major convention above.
    verts_world = (transform_matrix @ verts_h.T).T
    vertices = verts_world[:, :3]

    # process=True merges duplicate vertices (critical for flat/non-indexed
    # meshes from Cura where each triangle has its own vertex copies).
    # Without merging, a cube has 36 vertices instead of 8, which causes:
    # - gmsh: "overlapping facets" (triangles not topologically connected)
    # - scipy: singular stiffness matrix (BCs only fix some copies of
    #   each corner, leaving unconstrained rigid-body modes)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)

    Logger.log("d", "FEA mesh: after merge — %d vertices, %d faces (from %d input vertices)",
               len(mesh.vertices), len(mesh.faces), len(vertices))

    # Repair
    trimesh.repair.fix_normals(mesh)
    trimesh.repair.fill_holes(mesh)

    if not mesh.is_watertight:
        Logger.log(
            "w",
            "FEA mesh_extraction: mesh for node '%s' is not watertight after repair. "
            "FEA results may be inaccurate.",
            node.getName(),
        )

    return mesh
