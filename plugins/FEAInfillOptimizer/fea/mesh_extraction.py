# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Extract mesh data from a CuraSceneNode into a trimesh.Trimesh for FEA."""

import numpy as np

try:
    import trimesh
except ImportError:
    trimesh = None  # type: ignore[assignment]

from UM.Logger import Logger


def extract_trimesh_from_arrays(
    vertices: np.ndarray,
    indices: "np.ndarray | None",
    world_transform: np.ndarray,
    node_name: str = "",
) -> "trimesh.Trimesh":
    """Build a repaired trimesh from pre-captured numpy arrays.

    This is the thread-safe core — it does not access any scene node.

    Args:
        vertices: (N, 3) float64 vertices in local coordinates.
        indices: (M, 3) int64 face indices, or None for non-indexed meshes.
        world_transform: 4x4 float64 transformation matrix (row-major).
        node_name: Node name for log messages.

    Returns:
        A trimesh.Trimesh in world coordinates with repairs applied.
    """
    if trimesh is None:
        raise ImportError("trimesh is required but not installed.")

    if vertices is None or len(vertices) == 0:
        raise ValueError(f"Node '{node_name}' has no vertices.")

    vertices = np.asarray(vertices, dtype=np.float64)

    # Build face index array
    if indices is not None:
        faces = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    else:
        n_verts = vertices.shape[0]
        if n_verts % 3 != 0:
            raise ValueError(
                f"Non-indexed mesh vertex count ({n_verts}) is not divisible by 3."
            )
        faces = np.arange(n_verts, dtype=np.int64).reshape(-1, 3)

    # Apply world transformation
    transform_matrix = np.asarray(world_transform, dtype=np.float64)

    # Homogeneous coordinates: (N, 4)
    ones = np.ones((vertices.shape[0], 1), dtype=np.float64)
    verts_h = np.hstack([vertices, ones])
    verts_world = (transform_matrix @ verts_h.T).T
    vertices = verts_world[:, :3]

    # process=True merges duplicate vertices (critical for flat/non-indexed
    # meshes from Cura where each triangle has its own vertex copies).
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)

    Logger.log("d", "FEA mesh: after merge — %d vertices, %d faces (from %d input vertices)",
               len(mesh.vertices), len(mesh.faces), len(vertices))

    # Repair before building the face map so fill_holes() additions are included.
    trimesh.repair.fix_normals(mesh)
    trimesh.repair.fill_holes(mesh)

    # Build Cura→trimesh face index mapping.
    try:
        import scipy.spatial as _spatial
        _cura_centroids = (
            vertices[faces[:, 0]] + vertices[faces[:, 1]] + vertices[faces[:, 2]]
        ) / 3.0
        _trimesh_centroids = np.asarray(mesh.triangles_center)
        if len(_trimesh_centroids) > 0 and len(_cura_centroids) > 0:
            _kd = _spatial.KDTree(_trimesh_centroids)
            _, _cura_to_trimesh = _kd.query(_cura_centroids)
            mesh.metadata['cura_face_map'] = _cura_to_trimesh.astype(np.int32)
            Logger.log("d", "FEA mesh: face map built — %d Cura faces → %d trimesh faces",
                       len(_cura_centroids), len(_trimesh_centroids))
    except Exception as _e:
        Logger.log("w", "FEA mesh: could not build cura_face_map: %s", _e)

    if not mesh.is_watertight:
        Logger.log(
            "w",
            "FEA mesh_extraction: mesh for node '%s' is not watertight after repair. "
            "FEA results may be inaccurate.",
            node_name,
        )

    return mesh


def extract_trimesh(node) -> "trimesh.Trimesh":
    """Convert a CuraSceneNode's MeshData to a repaired trimesh.Trimesh.

    Thin wrapper that captures data from the scene node and delegates to
    :func:`extract_trimesh_from_arrays`.  Prefer calling the arrays variant
    directly from background threads (pre-capture data on the main thread).

    Args:
        node: A CuraSceneNode with valid MeshData.

    Returns:
        A trimesh.Trimesh in world coordinates with repairs applied.
    """
    mesh_data = node.getMeshData()
    if mesh_data is None:
        raise ValueError(f"Node '{node.getName()}' has no MeshData.")

    vertices = mesh_data.getVertices()
    if vertices is None:
        raise ValueError(f"Node '{node.getName()}' MeshData has no vertices.")

    vertices = np.array(vertices, dtype=np.float64)
    raw_indices = mesh_data.getIndices()
    indices = np.array(raw_indices, dtype=np.int64).reshape(-1, 3) if raw_indices is not None else None
    world_transform = np.array(node.getWorldTransformation().getData(), dtype=np.float64)

    return extract_trimesh_from_arrays(vertices, indices, world_transform, node.getName())
