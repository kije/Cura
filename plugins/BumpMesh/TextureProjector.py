# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

import numpy
from math import pi

# Default cylindrical cap angle: vertices with |normal·Y| > cos(cap_angle) use XZ projection
_DEFAULT_CAP_ANGLE_DEG = 20.0
# Cubic seam band width for cross-fade blending between projection zones
_CUBIC_SEAM_BAND = 0.35


def project(vertices: numpy.ndarray, normals: numpy.ndarray, mode: int, params: dict) -> numpy.ndarray:
    """Compute UV coordinates for each vertex based on the selected projection mode.

    :param vertices: (N, 3) float32 vertex positions.
    :param normals: (N, 3) float32 per-vertex normals.
    :param mode: Projection mode (0=Triplanar, 1=Cubic, 2=Cylindrical, 3=Spherical,
                 4=Planar XZ, 5=Planar XY, 6=Planar YZ).
    :param params: Dict with scale_u, scale_v, offset_u, offset_v, rotation (degrees).
    :return: (N, 2) float32 UV coordinates.
    """
    if mode == 0:
        uvs = _project_triplanar(vertices, normals)
    elif mode == 1:
        uvs = _project_cubic(vertices, normals)
    elif mode == 2:
        uvs = _project_cylindrical(vertices, normals)
    elif mode == 3:
        uvs = _project_spherical(vertices)
    elif mode == 4:
        uvs = _project_planar_xz(vertices)
    elif mode == 5:
        uvs = _project_planar_xy(vertices)
    elif mode == 6:
        uvs = _project_planar_yz(vertices)
    else:
        uvs = _project_planar_xz(vertices)

    # Apply UV transforms: scale, rotation, offset, texture aspect correction
    uvs = _apply_uv_transform(uvs, params)
    return uvs


def sample_displacement(uvs: numpy.ndarray, texture_data: numpy.ndarray) -> numpy.ndarray:
    """Sample displacement values from texture using bilinear interpolation.

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

    Now properly applies rotation and all UV transforms to each projection plane.

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

    # Apply UV transforms to each projection (including rotation)
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

def _project_planar_xz(vertices: numpy.ndarray) -> numpy.ndarray:
    """Planar projection onto the XZ plane, normalized to bounding box."""
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min
    bbox_size = numpy.where(bbox_size < 1e-6, 1.0, bbox_size)

    u = (vertices[:, 0] - bbox_min[0]) / bbox_size[0]
    v = (vertices[:, 2] - bbox_min[2]) / bbox_size[2]
    return numpy.column_stack([u, v]).astype(numpy.float32)


def _project_planar_xy(vertices: numpy.ndarray) -> numpy.ndarray:
    """Planar projection onto the XY plane, normalized to bounding box."""
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min
    bbox_size = numpy.where(bbox_size < 1e-6, 1.0, bbox_size)

    u = (vertices[:, 0] - bbox_min[0]) / bbox_size[0]
    v = (vertices[:, 1] - bbox_min[1]) / bbox_size[1]
    return numpy.column_stack([u, v]).astype(numpy.float32)


def _project_planar_yz(vertices: numpy.ndarray) -> numpy.ndarray:
    """Planar projection onto the YZ plane, normalized to bounding box."""
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min
    bbox_size = numpy.where(bbox_size < 1e-6, 1.0, bbox_size)

    u = (vertices[:, 1] - bbox_min[1]) / bbox_size[1]
    v = (vertices[:, 2] - bbox_min[2]) / bbox_size[2]
    return numpy.column_stack([u, v]).astype(numpy.float32)


def _project_cylindrical(vertices: numpy.ndarray, normals: numpy.ndarray) -> numpy.ndarray:
    """Cylindrical projection around the Y axis with cap handling.

    Vertices whose normal points mostly up/down (within cap_angle of Y axis) smoothly
    blend from cylindrical side projection to XZ planar projection for the caps.
    This prevents extreme stretching at cylinder tops/bottoms.
    """
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min

    center_x = (bbox_min[0] + bbox_max[0]) * 0.5
    center_z = (bbox_min[2] + bbox_max[2]) * 0.5

    dx = vertices[:, 0] - center_x
    dz = vertices[:, 2] - center_z

    # Side projection: angular U, height V
    u_side = (numpy.arctan2(dz, dx) + pi) / (2 * pi)
    height = bbox_size[1] if bbox_size[1] > 1e-6 else 1.0
    v_side = (vertices[:, 1] - bbox_min[1]) / height

    # Cap projection: planar XZ normalized to bbox
    u_cap = (vertices[:, 0] - bbox_min[0]) / max(bbox_size[0], 1e-6)
    v_cap = (vertices[:, 2] - bbox_min[2]) / max(bbox_size[2], 1e-6)

    # Blend: vertices with normal mostly along Y get cap projection
    cap_angle_rad = numpy.radians(_DEFAULT_CAP_ANGLE_DEG)
    cos_cap = numpy.cos(cap_angle_rad)
    abs_ny = numpy.abs(normals[:, 1])

    # Smooth transition: blend factor from 0 (side) to 1 (cap)
    # Ramp over a 10-degree band
    blend_start = cos_cap  # e.g., cos(20°) ≈ 0.94
    blend_end = numpy.cos(numpy.radians(max(_DEFAULT_CAP_ANGLE_DEG + 10, 30)))  # cos(30°) ≈ 0.87
    cap_blend = numpy.clip((abs_ny - blend_end) / max(blend_start - blend_end, 1e-6), 0.0, 1.0)

    u = u_side * (1.0 - cap_blend) + u_cap * cap_blend
    v = v_side * (1.0 - cap_blend) + v_cap * cap_blend

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
    """Cubic (box) projection with seam blending between dominant axes.

    Uses a smoothstep cross-fade at zone boundaries instead of hard axis selection.
    """
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    bbox_size = bbox_max - bbox_min
    bbox_size = numpy.where(bbox_size < 1e-6, 1.0, bbox_size)

    norm_verts = (vertices - bbox_min) / bbox_size
    abs_normals = numpy.abs(normals)

    # Compute blend weights per axis with seam band cross-fade
    # The dominant axis gets weight 1.0; near boundaries, weights blend smoothly
    ax = abs_normals[:, 0]
    ay = abs_normals[:, 1]
    az = abs_normals[:, 2]

    # Power-law sharpening with seam band softness
    power = max(1.0, 1.0 + (1.0 - _CUBIC_SEAM_BAND) * 11.0)
    wx = ax ** power
    wy = ay ** power
    wz = az ** power

    w_sum = wx + wy + wz
    w_sum = numpy.where(w_sum < 1e-8, 1.0, w_sum)
    wx /= w_sum
    wy /= w_sum
    wz /= w_sum

    # Per-axis UVs
    u_x = norm_verts[:, 1]  # X-dominant: YZ plane
    v_x = norm_verts[:, 2]
    u_y = norm_verts[:, 0]  # Y-dominant: XZ plane
    v_y = norm_verts[:, 2]
    u_z = norm_verts[:, 0]  # Z-dominant: XY plane
    v_z = norm_verts[:, 1]

    # Weighted blend of UVs from all three axes
    u = wx * u_x + wy * u_y + wz * u_z
    v = wx * v_x + wy * v_y + wz * v_z

    return numpy.column_stack([u, v]).astype(numpy.float32)


def _project_triplanar(vertices: numpy.ndarray, normals: numpy.ndarray) -> numpy.ndarray:
    """Triplanar projection -- returns dummy UVs (actual sampling is done in sample_displacement_triplanar)."""
    return numpy.zeros((len(vertices), 2), dtype=numpy.float32)


# --- UV Transform ---

def _apply_uv_transform(uvs: numpy.ndarray, params: dict) -> numpy.ndarray:
    """Apply rotation, scale, offset, and texture aspect correction to UV coordinates.

    Order: center at (0.5, 0.5) -> rotate -> scale (with aspect correction) -> uncenter -> offset.
    """
    scale_u = params.get("scale_u", 1.0)
    scale_v = params.get("scale_v", 1.0)
    offset_u = params.get("offset_u", 0.0)
    offset_v = params.get("offset_v", 0.0)
    rotation_deg = params.get("rotation", 0.0)

    # Texture aspect correction: compensate for non-square textures so that
    # equal world-space distances produce equal texture-space distances.
    tex_width = params.get("tex_width", 0)
    tex_height = params.get("tex_height", 0)
    if tex_width > 0 and tex_height > 0:
        t_max = max(tex_width, tex_height)
        aspect_u = t_max / tex_width
        aspect_v = t_max / tex_height
    else:
        aspect_u = 1.0
        aspect_v = 1.0

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

    # Scale (with aspect correction)
    result[:, 0] *= scale_u * aspect_u
    result[:, 1] *= scale_v * aspect_v

    # Uncenter
    result[:, 0] += 0.5
    result[:, 1] += 0.5

    # Offset
    result[:, 0] += offset_u
    result[:, 1] += offset_v

    return result
