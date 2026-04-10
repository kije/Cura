# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

import numpy

# --- Constants (aligned with stlTexturizer) ---
# Crease angle for smooth group detection in compute_flat_normals
SMOOTH_COS_THRESHOLD = 0.866  # cos(30°) — tighter than previous cos(60°)=0.5
# Spatial dedup precision: 0.0001mm (10x finer than before)
QUANTISE_FACTOR = 10000.0


def displace(
    vertices: numpy.ndarray,
    normals: numpy.ndarray,
    displacement_values: numpy.ndarray,
    amplitude: float,
    mask: numpy.ndarray,
    symmetric: bool = True,
) -> numpy.ndarray:
    """Displace mesh vertices along their normals.

    :param vertices: (N, 3) float32 vertex positions.
    :param normals: (N, 3) float32 per-vertex normals (should be unit length).
    :param displacement_values: (N,) float32 displacement factors [0, 1].
    :param amplitude: Displacement distance in mm.
    :param mask: (N,) float32 per-vertex mask [0, 1].
    :param symmetric: If True, 50% grey = neutral (outward+inward).
                      If False, 0 = no displacement, 1 = full outward.
    :return: (N, 3) float32 displaced vertex positions.
    """
    if symmetric:
        # Map [0, 1] -> [-1, 1]: black pushes inward, grey is neutral, white pushes outward
        scaled = (displacement_values - 0.5) * 2.0
    else:
        # Asymmetric: 0 = no displacement, 1 = full amplitude outward
        scaled = displacement_values

    offset = scaled * amplitude * mask
    return vertices + normals * offset[:, numpy.newaxis]


def flatten_mesh(vertices: numpy.ndarray, indices: numpy.ndarray) -> numpy.ndarray:
    """Flatten an indexed mesh to triangle soup (no shared vertices).

    Each triangle gets its own 3 vertices. This is essential before displacement
    to prevent artifacts at sharp edges where shared vertices would get averaged
    normals that point in wrong directions.

    :param vertices: (N, 3) float32 vertex positions.
    :param indices: (M, 3) int32 triangle indices.
    :return: (M*3, 3) float32 flat vertex array (every 3 = one triangle).
    """
    return vertices[indices.ravel()].reshape(-1, 3).copy()


def compute_flat_normals(vertices: numpy.ndarray) -> numpy.ndarray:
    """Compute per-vertex normals for a flat triangle soup mesh.

    Uses a position-based smooth group approach with area-weighted face normals:
    1. Compute face normals (unnormalized = area-weighted)
    2. Find coincident vertices (same position within QUANTISE_FACTOR tolerance)
    3. For each group of coincident vertices, accumulate area-weighted normals
       only from faces within cos(30°) of each other (crease detection)

    :param vertices: (M*3, 3) float32 triangle soup vertices.
    :return: (M*3, 3) float32 unit normals per vertex.
    """
    num_verts = len(vertices)

    # Compute face normals (NOT normalized — length = 2× triangle area = area weighting)
    v0 = vertices[0::3]
    v1 = vertices[1::3]
    v2 = vertices[2::3]
    face_normals_raw = numpy.cross(v1 - v0, v2 - v0)

    # Also compute normalized face normals for angle comparison
    fn_lengths = numpy.linalg.norm(face_normals_raw, axis=1, keepdims=True)
    fn_lengths = numpy.where(fn_lengths < 1e-8, 1.0, fn_lengths)
    face_normals_unit = face_normals_raw / fn_lengths

    # Assign each vertex its face's area-weighted normal and unit normal
    per_vertex_raw = numpy.repeat(face_normals_raw, 3, axis=0)  # (M*3, 3)
    per_vertex_unit = numpy.repeat(face_normals_unit, 3, axis=0)

    # Quantize vertex positions for grouping coincident vertices
    quantized = numpy.round(vertices * QUANTISE_FACTOR).astype(numpy.int64)
    keys = quantized[:, 0] * 1000000007 + quantized[:, 1] * 1000000009 + quantized[:, 2]

    # Find unique positions and group indices
    unique_keys, inverse, counts = numpy.unique(keys, return_inverse=True, return_counts=True)

    # Start with face normals (correct for isolated vertices and sharp edges)
    result_normals = per_vertex_raw.copy()

    # Only process groups with 2+ coincident vertices (skip singletons)
    multi_groups = numpy.where(counts > 1)[0]
    if len(multi_groups) == 0:
        # Normalize and return
        lengths = numpy.linalg.norm(result_normals, axis=1, keepdims=True)
        lengths = numpy.where(lengths < 1e-8, 1.0, lengths)
        return (result_normals / lengths).astype(numpy.float32)

    # Sort by group for efficient sequential access
    sorted_order = numpy.argsort(inverse)
    group_starts = numpy.zeros(len(unique_keys) + 1, dtype=numpy.int64)
    numpy.cumsum(counts, out=group_starts[1:])

    for group_id in multi_groups:
        start = group_starts[group_id]
        end = group_starts[group_id + 1]
        group_indices = sorted_order[start:end]
        group_unit = per_vertex_unit[group_indices]
        group_raw = per_vertex_raw[group_indices]
        n = len(group_indices)

        # Compute pairwise dot products of unit normals for crease detection
        dots = group_unit @ group_unit.T  # (n, n)

        # For each vertex, accumulate area-weighted normals from smooth neighbors
        for i in range(n):
            smooth_mask = dots[i] > SMOOTH_COS_THRESHOLD
            avg = group_raw[smooth_mask].sum(axis=0)
            length = numpy.linalg.norm(avg)
            if length > 1e-8:
                result_normals[group_indices[i]] = avg / length

    # Normalize final result
    lengths = numpy.linalg.norm(result_normals, axis=1, keepdims=True)
    lengths = numpy.where(lengths < 1e-8, 1.0, lengths)
    return (result_normals / lengths).astype(numpy.float32)


def compute_flat_face_normals(vertices: numpy.ndarray) -> numpy.ndarray:
    """Compute per-vertex face normals via direct cross product (no averaging).

    Used post-displacement to avoid normal flipping from vertex-normal averaging.
    Each vertex gets its face's flat normal.

    :param vertices: (M*3, 3) float32 triangle soup vertices.
    :return: (M*3, 3) float32 unit face normals per vertex.
    """
    v0 = vertices[0::3]
    v1 = vertices[1::3]
    v2 = vertices[2::3]
    face_normals = numpy.cross(v1 - v0, v2 - v0)
    fn_lengths = numpy.linalg.norm(face_normals, axis=1, keepdims=True)
    fn_lengths = numpy.where(fn_lengths < 1e-8, 1.0, fn_lengths)
    face_normals = face_normals / fn_lengths
    return numpy.repeat(face_normals, 3, axis=0).astype(numpy.float32)


def compute_vertex_normals(vertices: numpy.ndarray, indices: numpy.ndarray) -> numpy.ndarray:
    """Compute area-weighted per-vertex normals from indexed triangle mesh.

    Uses numpy.bincount for fast scatter-add (10-20x faster than numpy.add.at).

    :param vertices: (N, 3) float32 vertex positions.
    :param indices: (M, 3) int32 triangle indices.
    :return: (N, 3) float32 unit normals per vertex.
    """
    v0 = vertices[indices[:, 0]]
    v1 = vertices[indices[:, 1]]
    v2 = vertices[indices[:, 2]]

    edge1 = v1 - v0
    edge2 = v2 - v0
    face_normals = numpy.cross(edge1, edge2)

    num_verts = len(vertices)
    flat_indices = indices.ravel()
    face_normals_repeated = numpy.repeat(face_normals, 3, axis=0)

    vertex_normals = numpy.zeros((num_verts, 3), dtype=numpy.float64)
    for axis in range(3):
        vertex_normals[:, axis] = numpy.bincount(
            flat_indices, weights=face_normals_repeated[:, axis], minlength=num_verts
        )

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

    Uses tile-then-blur-then-crop to preserve seamless tiling: the texture is
    tiled 3x3, blurred, then the center tile is cropped. This ensures the
    blur wraps correctly at texture edges.

    :param texture_data: (H, W) float32 grayscale texture.
    :param iterations: Number of blur passes.
    :return: (H, W) float32 smoothed texture.
    """
    if iterations <= 0:
        return texture_data

    h, w = texture_data.shape

    # Tile 3x3 for seamless blur wrapping
    tiled = numpy.tile(texture_data, (3, 3))  # (3H, 3W)
    th, tw = tiled.shape

    result = tiled.copy()
    padded = numpy.empty((th + 2, tw + 2), dtype=numpy.float32)

    for _ in range(iterations):
        # Fill interior
        padded[1:-1, 1:-1] = result
        # Edge padding (replicate border of the tiled image — fine since it's already tiled)
        padded[0, 1:-1] = result[0, :]
        padded[-1, 1:-1] = result[-1, :]
        padded[1:-1, 0] = result[:, 0]
        padded[1:-1, -1] = result[:, -1]
        padded[0, 0] = result[0, 0]
        padded[0, -1] = result[0, -1]
        padded[-1, 0] = result[-1, 0]
        padded[-1, -1] = result[-1, -1]

        result = (padded[1:-1, 1:-1] +
                  padded[1:-1, 2:] + padded[1:-1, :-2] +
                  padded[2:, 1:-1] + padded[:-2, 1:-1] +
                  padded[2:, 2:] + padded[:-2, 2:] +
                  padded[2:, :-2] + padded[:-2, :-2]) / 9.0

    # Crop center tile
    return result[h:2*h, w:2*w].astype(numpy.float32)
