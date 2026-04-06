# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

import numpy
from math import pi


def project(vertices: numpy.ndarray, normals: numpy.ndarray, mode: int, params: dict) -> numpy.ndarray:
    """Compute UV coordinates for each vertex based on the selected projection mode.

    :param vertices: (N, 3) float32 vertex positions.
    :param normals: (N, 3) float32 per-vertex normals.
    :param mode: Projection mode (0=Triplanar, 1=Cubic, 2=Cylindrical, 3=Spherical, 4=Planar).
    :param params: Dict with scale_u, scale_v, offset_u, offset_v, rotation (degrees).
    :return: (N, 2) float32 UV coordinates.
    """
    if mode == 0:
        uvs = _project_triplanar(vertices, normals)
    elif mode == 1:
        uvs = _project_cubic(vertices, normals)
    elif mode == 2:
        uvs = _project_cylindrical(vertices)
    elif mode == 3:
        uvs = _project_spherical(vertices)
    elif mode == 4:
        uvs = _project_planar(vertices)
    else:
        uvs = _project_planar(vertices)

    # Apply UV transforms: scale, rotation, offset
    uvs = _apply_uv_transform(uvs, params)
    return uvs


def sample_displacement(uvs: numpy.ndarray, texture_data: numpy.ndarray) -> numpy.ndarray:
    """Sample displacement values from texture using bilinear interpolation.

    For triplanar mode, use sample_displacement_triplanar instead.

    :param uvs: (N, 2) float32 UV coordinates.
    :param texture_data: (H, W) float32 grayscale texture [0, 1].
    :return: (N,) float32 displacement values [0, 1].
    """
    h, w = texture_data.shape

    # Tile UVs to [0, 1) range
    u = uvs[:, 0] % 1.0
    v = uvs[:, 1] % 1.0

    # Handle negative modulo
    u = numpy.where(u < 0, u + 1.0, u)
    v = numpy.where(v < 0, v + 1.0, v)

    # Convert to pixel coordinates
    u_px = u * (w - 1)
    v_px = v * (h - 1)

    # Bilinear interpolation
    x0 = numpy.floor(u_px).astype(numpy.int32)
    y0 = numpy.floor(v_px).astype(numpy.int32)
    x1 = numpy.minimum(x0 + 1, w - 1)
    y1 = numpy.minimum(y0 + 1, h - 1)

    fx = u_px - x0
    fy = v_px - y0

    d00 = texture_data[y0, x0]
    d10 = texture_data[y0, x1]
    d01 = texture_data[y1, x0]
    d11 = texture_data[y1, x1]

    displacement = (d00 * (1 - fx) * (1 - fy) +
                    d10 * fx * (1 - fy) +
                    d01 * (1 - fx) * fy +
                    d11 * fx * fy)

    return displacement.astype(numpy.float32)


def sample_displacement_triplanar(
    vertices: numpy.ndarray,
    normals: numpy.ndarray,
    texture_data: numpy.ndarray,
    params: dict,
    sharpness: float = 4.0
) -> numpy.ndarray:
    """Sample displacement using triplanar blending (3 planar projections blended by normal).

    :param vertices: (N, 3) float32 vertex positions.
    :param normals: (N, 3) float32 per-vertex normals.
    :param texture_data: (H, W) float32 grayscale texture [0, 1].
    :param params: UV transform parameters.
    :param sharpness: Blending sharpness (higher = sharper transitions between planes).
    :return: (N,) float32 displacement values.
    """
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min
    bbox_size = numpy.where(bbox_size < 1e-6, 1.0, bbox_size)

    # Normalize vertex positions to [0, 1]
    norm_verts = (vertices - bbox_min) / bbox_size

    # Three planar projections (YZ, XZ, XY planes)
    uv_yz = numpy.column_stack([norm_verts[:, 1], norm_verts[:, 2]])  # project along X
    uv_xz = numpy.column_stack([norm_verts[:, 0], norm_verts[:, 2]])  # project along Y
    uv_xy = numpy.column_stack([norm_verts[:, 0], norm_verts[:, 1]])  # project along Z

    # Apply UV transforms to each projection
    uv_yz = _apply_uv_transform(uv_yz, params)
    uv_xz = _apply_uv_transform(uv_xz, params)
    uv_xy = _apply_uv_transform(uv_xy, params)

    # Sample from each projection
    d_yz = sample_displacement(uv_yz, texture_data)
    d_xz = sample_displacement(uv_xz, texture_data)
    d_xy = sample_displacement(uv_xy, texture_data)

    # Blend weights from absolute normals
    weights = numpy.abs(normals) ** sharpness
    weight_sum = weights.sum(axis=1, keepdims=True)
    weight_sum = numpy.where(weight_sum < 1e-6, 1.0, weight_sum)
    weights = weights / weight_sum

    # Weighted blend
    displacement = weights[:, 0] * d_yz + weights[:, 1] * d_xz + weights[:, 2] * d_xy

    return displacement.astype(numpy.float32)


# --- Projection Mode Implementations ---

def _project_planar(vertices: numpy.ndarray) -> numpy.ndarray:
    """Planar projection onto the XZ plane, normalized to bounding box."""
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min
    bbox_size = numpy.where(bbox_size < 1e-6, 1.0, bbox_size)

    u = (vertices[:, 0] - bbox_min[0]) / bbox_size[0]
    v = (vertices[:, 2] - bbox_min[2]) / bbox_size[2]
    return numpy.column_stack([u, v]).astype(numpy.float32)


def _project_cylindrical(vertices: numpy.ndarray) -> numpy.ndarray:
    """Cylindrical projection around the Y axis."""
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min

    center_x = (bbox_min[0] + bbox_max[0]) * 0.5
    center_z = (bbox_min[2] + bbox_max[2]) * 0.5

    dx = vertices[:, 0] - center_x
    dz = vertices[:, 2] - center_z

    u = (numpy.arctan2(dz, dx) + pi) / (2 * pi)

    height = bbox_size[1] if bbox_size[1] > 1e-6 else 1.0
    v = (vertices[:, 1] - bbox_min[1]) / height

    return numpy.column_stack([u, v]).astype(numpy.float32)


def _project_spherical(vertices: numpy.ndarray) -> numpy.ndarray:
    """Spherical projection from the bounding box center."""
    center = (vertices.min(axis=0) + vertices.max(axis=0)) * 0.5
    d = vertices - center

    r = numpy.sqrt(numpy.sum(d * d, axis=1))
    r = numpy.where(r < 1e-6, 1.0, r)

    u = (numpy.arctan2(d[:, 2], d[:, 0]) + pi) / (2 * pi)
    v = numpy.arccos(numpy.clip(d[:, 1] / r, -1.0, 1.0)) / pi

    return numpy.column_stack([u, v]).astype(numpy.float32)


def _project_cubic(vertices: numpy.ndarray, normals: numpy.ndarray) -> numpy.ndarray:
    """Cubic (box) projection -- each face projects based on its dominant normal axis."""
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min
    bbox_size = numpy.where(bbox_size < 1e-6, 1.0, bbox_size)

    norm_verts = (vertices - bbox_min) / bbox_size

    abs_normals = numpy.abs(normals)
    dominant = numpy.argmax(abs_normals, axis=1)

    uvs = numpy.zeros((len(vertices), 2), dtype=numpy.float32)

    # X-dominant faces -> project onto YZ plane
    mask_x = dominant == 0
    uvs[mask_x, 0] = norm_verts[mask_x, 1]
    uvs[mask_x, 1] = norm_verts[mask_x, 2]

    # Y-dominant faces -> project onto XZ plane
    mask_y = dominant == 1
    uvs[mask_y, 0] = norm_verts[mask_y, 0]
    uvs[mask_y, 1] = norm_verts[mask_y, 2]

    # Z-dominant faces -> project onto XY plane
    mask_z = dominant == 2
    uvs[mask_z, 0] = norm_verts[mask_z, 0]
    uvs[mask_z, 1] = norm_verts[mask_z, 1]

    return uvs


def _project_triplanar(vertices: numpy.ndarray, normals: numpy.ndarray) -> numpy.ndarray:
    """Triplanar projection -- returns dummy UVs (actual sampling is done in sample_displacement_triplanar)."""
    # For triplanar, we don't use standard UVs. Return zeros as placeholder.
    # The actual triplanar blending happens in sample_displacement_triplanar().
    return numpy.zeros((len(vertices), 2), dtype=numpy.float32)


# --- UV Transform ---

def _apply_uv_transform(uvs: numpy.ndarray, params: dict) -> numpy.ndarray:
    """Apply rotation, scale, and offset to UV coordinates.

    Order: center at (0.5, 0.5) -> rotate -> scale -> uncenter -> offset.
    This ensures rotation always happens around the texture center regardless
    of the scale values.
    """
    scale_u = params.get("scale_u", 1.0)
    scale_v = params.get("scale_v", 1.0)
    offset_u = params.get("offset_u", 0.0)
    offset_v = params.get("offset_v", 0.0)
    rotation_deg = params.get("rotation", 0.0)

    result = uvs.copy()

    # Center at (0.5, 0.5)
    result[:, 0] -= 0.5
    result[:, 1] -= 0.5

    # Rotate around origin (which is now the UV center)
    if abs(rotation_deg) > 0.01:
        rotation_rad = numpy.radians(rotation_deg)
        cos_r = numpy.cos(rotation_rad)
        sin_r = numpy.sin(rotation_rad)
        u_rot = result[:, 0] * cos_r - result[:, 1] * sin_r
        v_rot = result[:, 0] * sin_r + result[:, 1] * cos_r
        result[:, 0] = u_rot
        result[:, 1] = v_rot

    # Scale around the center
    result[:, 0] *= scale_u
    result[:, 1] *= scale_v

    # Uncenter
    result[:, 0] += 0.5
    result[:, 1] += 0.5

    # Offset
    result[:, 0] += offset_u
    result[:, 1] += offset_v

    return result
