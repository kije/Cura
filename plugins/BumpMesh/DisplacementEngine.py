# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

import numpy


def displace(
    vertices: numpy.ndarray,
    normals: numpy.ndarray,
    displacement_values: numpy.ndarray,
    amplitude: float,
    mask: numpy.ndarray
) -> numpy.ndarray:
    """Displace mesh vertices along their normals.

    :param vertices: (N, 3) float32 vertex positions.
    :param normals: (N, 3) float32 per-vertex normals (should be unit length).
    :param displacement_values: (N,) float32 displacement factors [0, 1].
    :param amplitude: Displacement distance in mm.
    :param mask: (N,) float32 per-vertex mask [0, 1].
    :return: (N, 3) float32 displaced vertex positions.
    """
    offset = displacement_values * amplitude * mask
    return vertices + normals * offset[:, numpy.newaxis]


def compute_vertex_normals(vertices: numpy.ndarray, indices: numpy.ndarray) -> numpy.ndarray:
    """Compute area-weighted per-vertex normals from triangle mesh.

    :param vertices: (N, 3) float32 vertex positions.
    :param indices: (M, 3) int32 triangle indices.
    :return: (N, 3) float32 unit normals per vertex.
    """
    v0 = vertices[indices[:, 0]]
    v1 = vertices[indices[:, 1]]
    v2 = vertices[indices[:, 2]]

    # Face normals (not normalized = area-weighted)
    edge1 = v1 - v0
    edge2 = v2 - v0
    face_normals = numpy.cross(edge1, edge2)

    # Accumulate face normals onto vertices
    vertex_normals = numpy.zeros_like(vertices)
    for i in range(3):
        numpy.add.at(vertex_normals, indices[:, i], face_normals)

    # Normalize
    lengths = numpy.linalg.norm(vertex_normals, axis=1, keepdims=True)
    lengths = numpy.where(lengths < 1e-8, 1.0, lengths)
    vertex_normals /= lengths

    return vertex_normals.astype(numpy.float32)


def compute_angle_mask(normals: numpy.ndarray, mask_angle_deg: float) -> numpy.ndarray:
    """Compute per-vertex mask based on angle between normal and up vector.

    :param normals: (N, 3) float32 per-vertex normals.
    :param mask_angle_deg: Maximum angle from up vector in degrees. 0 = no masking (all pass).
    :return: (N,) float32 mask values [0, 1].
    """
    if mask_angle_deg <= 0:
        return numpy.ones(len(normals), dtype=numpy.float32)

    up = numpy.array([0.0, 1.0, 0.0], dtype=numpy.float32)
    cos_angles = numpy.dot(normals, up)
    angles_deg = numpy.degrees(numpy.arccos(numpy.clip(cos_angles, -1.0, 1.0)))

    # Smooth falloff over 10 degrees
    falloff = 10.0
    mask = numpy.clip(1.0 - (angles_deg - mask_angle_deg) / falloff, 0.0, 1.0)

    return mask.astype(numpy.float32)


def smooth_texture(texture_data: numpy.ndarray, iterations: int) -> numpy.ndarray:
    """Apply box blur smoothing to the displacement map.

    Uses the same convolution pattern as Cura's ImageReader.

    :param texture_data: (H, W) float32 grayscale texture.
    :param iterations: Number of blur passes.
    :return: (H, W) float32 smoothed texture.
    """
    if iterations <= 0:
        return texture_data

    result = texture_data.copy()
    for _ in range(iterations):
        padded = numpy.pad(result, ((1, 1), (1, 1)), mode="edge")

        result = (padded[1:-1, 1:-1] +
                  padded[1:-1, 2:] + padded[1:-1, :-2] +
                  padded[2:, 1:-1] + padded[:-2, 1:-1] +
                  padded[2:, 2:] + padded[:-2, 2:] +
                  padded[2:, :-2] + padded[:-2, :-2]) / 9.0

    return result.astype(numpy.float32)
