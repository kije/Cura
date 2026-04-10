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


def compute_boundary_falloff(
    vertices: numpy.ndarray,
    mask: numpy.ndarray,
    falloff_distance: float = 2.0,
) -> numpy.ndarray:
    """Smooth the boundary of a binary mask using distance-based falloff.

    For each vertex near a mask boundary (where adjacent vertices have different
    mask values), smoothly ramp the mask from 0 to 1 over `falloff_distance` mm.
    Uses a spatial grid for efficient neighbor lookup.

    :param vertices: (N, 3) float32 vertex positions.
    :param mask: (N,) float32 mask values (0.0 or 1.0, or already smooth).
    :param falloff_distance: Distance in mm over which to smooth the transition.
    :return: (N,) float32 smoothed mask values [0, 1].
    """
    n = len(vertices)
    if n == 0 or falloff_distance <= 0.0:
        return mask.copy()

    # --- Step 1: Identify boundary vertices ---
    # Quantize positions to find coincident / nearby vertices (same logic as
    # compute_flat_normals) so that triangle-soup vertices sharing the same
    # spatial position are grouped together.
    quantized = numpy.round(vertices * QUANTISE_FACTOR).astype(numpy.int64)
    keys = (quantized[:, 0] * 1000000007
            + quantized[:, 1] * 1000000009
            + quantized[:, 2])
    unique_keys, inverse, counts = numpy.unique(
        keys, return_inverse=True, return_counts=True
    )

    # For each unique position, compute min and max mask value among its vertices
    num_groups = len(unique_keys)
    group_mask_min = numpy.ones(num_groups, dtype=numpy.float32)
    group_mask_max = numpy.zeros(num_groups, dtype=numpy.float32)

    # Use bincount-based min/max: min via -max(-x), max via max(x)
    # numpy.minimum/maximum with at for scatter
    numpy.minimum.at(group_mask_min, inverse, mask)
    numpy.maximum.at(group_mask_max, inverse, mask)

    # A group is on the boundary if its vertices don't all share the same mask value
    group_is_boundary = group_mask_max - group_mask_min > 0.01

    # Also check triangle-level adjacency: for each triangle, if the 3 vertices
    # (by unique-group) have differing mask values, mark those groups as boundary.
    num_tris = n // 3
    if num_tris > 0:
        tri_groups = inverse.reshape(num_tris, 3)
        tri_mask_vals = mask.reshape(num_tris, 3)
        tri_min = tri_mask_vals.min(axis=1)
        tri_max = tri_mask_vals.max(axis=1)
        mixed_tris = numpy.where(tri_max - tri_min > 0.01)[0]
        if len(mixed_tris) > 0:
            mixed_groups = tri_groups[mixed_tris].ravel()
            group_is_boundary[mixed_groups] = True

    # Map boundary flag back to per-vertex
    vertex_is_boundary = group_is_boundary[inverse]
    boundary_indices = numpy.where(vertex_is_boundary)[0]

    if len(boundary_indices) == 0:
        return mask.copy()

    boundary_positions = vertices[boundary_indices]  # (B, 3)

    # --- Step 2: Build spatial grid for efficient neighbor lookup ---
    cell_size = falloff_distance
    if cell_size < 1e-6:
        return mask.copy()

    # Determine which vertices are candidates for smoothing: those within
    # falloff_distance of any boundary vertex.  We process all non-boundary
    # vertices to check, using the spatial grid for efficiency.

    # Grid cell coordinates for boundary vertices
    b_cells = numpy.floor(boundary_positions / cell_size).astype(numpy.int64)

    # Build a dict mapping cell -> list of boundary vertex indices (into boundary_positions)
    grid = {}
    for idx in range(len(boundary_indices)):
        cx, cy, cz = int(b_cells[idx, 0]), int(b_cells[idx, 1]), int(b_cells[idx, 2])
        key = (cx, cy, cz)
        if key not in grid:
            grid[key] = []
        grid[key].append(idx)

    # Convert lists to arrays for vectorized distance computation
    for key in grid:
        grid[key] = numpy.array(grid[key], dtype=numpy.int64)

    # --- Step 3: For each vertex, find distance to nearest boundary vertex ---
    result = mask.copy()

    # Process vertices in chunks to limit memory usage
    chunk_size = 4096
    for start in range(0, n, chunk_size):
        end = min(start + chunk_size, n)
        chunk_verts = vertices[start:end]  # (C, 3)
        chunk_cells = numpy.floor(chunk_verts / cell_size).astype(numpy.int64)

        for local_i in range(end - start):
            global_i = start + local_i
            # Boundary vertices keep their original mask
            if vertex_is_boundary[global_i]:
                # Set boundary vertices to the threshold (0.5) for smooth transition
                continue

            cx = int(chunk_cells[local_i, 0])
            cy = int(chunk_cells[local_i, 1])
            cz = int(chunk_cells[local_i, 2])

            # Gather boundary vertices from the 3x3x3 neighborhood of cells
            nearby_boundary_idx = []
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    for dz in range(-1, 2):
                        cell_key = (cx + dx, cy + dy, cz + dz)
                        if cell_key in grid:
                            nearby_boundary_idx.append(grid[cell_key])

            if len(nearby_boundary_idx) == 0:
                continue

            nearby_boundary_idx = numpy.concatenate(nearby_boundary_idx)
            nearby_positions = boundary_positions[nearby_boundary_idx]

            # Compute distances to nearby boundary vertices
            diffs = nearby_positions - chunk_verts[local_i]
            dists = numpy.sqrt(numpy.sum(diffs * diffs, axis=1))
            min_dist = numpy.min(dists)

            if min_dist > falloff_distance:
                continue

            # Signed distance: positive inside masked region, negative outside
            current_mask_val = mask[global_i]
            if current_mask_val > 0.5:
                signed_dist = min_dist  # inside -> positive
            else:
                signed_dist = -min_dist  # outside -> negative

            # Map to [0, falloff_distance] then smoothstep to [0, 1]
            t = numpy.clip((signed_dist + falloff_distance) / (2.0 * falloff_distance), 0.0, 1.0)
            # Smoothstep: 3t^2 - 2t^3
            smoothed = t * t * (3.0 - 2.0 * t)

            result[global_i] = smoothed

    return result.astype(numpy.float32)


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
