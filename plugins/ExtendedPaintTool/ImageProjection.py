# Copyright (c) 2025 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

"""Image-to-model projection for the Extended Paint Tool.

Loads an arbitrary image (including PNG with alpha), converts it to a 1-bit
mask, and projects it onto a mesh's paint texture using one of several
projection modes (planar, box, spherical, cylindrical). The user can offset,
rotate and scale the image within the projection before applying it.

The core output is a QImage bit-mask in paint-texture space where every
"ON" texel should receive the selected extruder colour.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Tuple

import numpy
from PyQt6.QtCore import Qt, QObject, pyqtEnum
from PyQt6.QtGui import QImage

from UM.Logger import Logger


class ProjectionMode(QObject):
    """Projection strategies used to map image pixels onto 3D surface points."""

    @pyqtEnum
    class Mode(IntEnum):
        PLANAR_X = 0      # project along +X (image in YZ plane)
        PLANAR_Y = 1      # project along +Y (image in XZ plane)
        PLANAR_Z = 2      # project along +Z (image in XY plane)
        BOX = 3           # cubic / tri-planar (dominant axis per texel)
        SPHERICAL = 4     # lat / lon mapping (Y up)
        CYLINDRICAL = 5   # azimuth / height (Y up)


@dataclass
class ImageTransform:
    """User-controllable transform applied in projection space before sampling the image.

    Offsets are in normalized projection units (1.0 == full bbox extent for the
    planar/box modes, 1.0 == full latitude/longitude for spherical, etc.).
    """
    offset_u: float = 0.0
    offset_v: float = 0.0
    rotation_deg: float = 0.0
    scale: float = 1.0          # uniform scale multiplier; 1.0 = image fits full range

    # World-space center of projection. For spherical/cylindrical this is the
    # sphere/cylinder center; for planar/box this is the origin of the
    # projection plane.
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0

    def copy(self) -> "ImageTransform":
        return ImageTransform(
            offset_u = self.offset_u,
            offset_v = self.offset_v,
            rotation_deg = self.rotation_deg,
            scale = self.scale,
            center_x = self.center_x,
            center_y = self.center_y,
            center_z = self.center_z,
        )


def image_to_bitmap(image: QImage, threshold: int = 128, invert: bool = False) -> numpy.ndarray:
    """Convert an arbitrary QImage to a 1-bit (bool) numpy mask.

    * If the image has an alpha channel, fully / mostly transparent pixels are
      treated as OFF regardless of colour.
    * The remaining pixels are compared against the luminance threshold.

    :param image: source image (any supported Qt format)
    :param threshold: 0..255, luminance cutoff
    :param invert: if True, light pixels are ON instead of dark
    :return: 2D numpy bool array of shape (height, width), True = ON
    """
    if image.isNull():
        return numpy.zeros((1, 1), dtype = bool)

    converted = image.convertToFormat(QImage.Format.Format_ARGB32)
    width = converted.width()
    height = converted.height()

    # Build a numpy view on the ARGB32 data (note: little-endian layout = BGRA).
    ptr = converted.constBits()
    ptr.setsize(height * width * 4)
    buffer = numpy.frombuffer(ptr, dtype = numpy.uint8).reshape((height, width, 4))

    blue  = buffer[:, :, 0].astype(numpy.int32)
    green = buffer[:, :, 1].astype(numpy.int32)
    red   = buffer[:, :, 2].astype(numpy.int32)
    alpha = buffer[:, :, 3]

    # Rec. 709 luminance
    luminance = (red * 54 + green * 183 + blue * 19) >> 8

    if invert:
        mask = luminance >= threshold
    else:
        mask = luminance < threshold

    mask = mask & (alpha >= 128)
    return mask.astype(bool)


class ImageProjector:
    """Holds the current image + transform state and computes paint-texture masks."""

    def __init__(self) -> None:
        self._source_image: Optional[QImage] = None
        self._bitmap: Optional[numpy.ndarray] = None
        self._threshold: int = 128
        self._invert: bool = False
        self._mode: int = ProjectionMode.Mode.PLANAR_Z
        self._transform: ImageTransform = ImageTransform()

    def has_image(self) -> bool:
        return self._source_image is not None and not self._source_image.isNull()

    def load_image(self, path: str) -> bool:
        img = QImage(path)
        if img.isNull():
            Logger.error(f"ImageProjector: failed to load image at {path}")
            self._source_image = None
            self._bitmap = None
            return False
        self._source_image = img
        self._rebuild_bitmap()
        return True

    def clear(self) -> None:
        self._source_image = None
        self._bitmap = None

    def get_image_size(self) -> Tuple[int, int]:
        if self._source_image is None:
            return (0, 0)
        return (self._source_image.width(), self._source_image.height())

    def set_threshold(self, threshold: int) -> None:
        self._threshold = max(0, min(255, int(threshold)))
        self._rebuild_bitmap()

    def get_threshold(self) -> int:
        return self._threshold

    def set_invert(self, invert: bool) -> None:
        self._invert = bool(invert)
        self._rebuild_bitmap()

    def get_invert(self) -> bool:
        return self._invert

    def set_mode(self, mode: int) -> None:
        self._mode = int(mode)

    def get_mode(self) -> int:
        return self._mode

    @property
    def transform(self) -> ImageTransform:
        return self._transform

    def set_transform(self, transform: ImageTransform) -> None:
        self._transform = transform

    def _rebuild_bitmap(self) -> None:
        if self._source_image is None:
            self._bitmap = None
            return
        self._bitmap = image_to_bitmap(self._source_image, self._threshold, self._invert)

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def _project_world_to_image_uv(self,
                                    world_points: numpy.ndarray,
                                    bbox_size: numpy.ndarray) -> numpy.ndarray:
        """Project an (N, 3) array of world-space points to (N, 2) image UVs in [0, 1].

        Points whose UV falls outside [0, 1] should later be treated as "no hit".
        """
        if world_points.size == 0:
            return numpy.zeros((0, 2), dtype = numpy.float32)

        center = numpy.array([self._transform.center_x,
                              self._transform.center_y,
                              self._transform.center_z], dtype = numpy.float32)
        rel = world_points - center

        mode = self._mode
        M = ProjectionMode.Mode
        if mode in (M.PLANAR_X, M.PLANAR_Y, M.PLANAR_Z):
            # Two perpendicular axes depending on projection axis.
            if mode == M.PLANAR_X:
                u_axis, v_axis = 2, 1   # -Z as U, Y as V
                u_raw = -rel[:, u_axis]
                v_raw =  rel[:, v_axis]
            elif mode == M.PLANAR_Y:
                u_axis, v_axis = 0, 2   # X as U, Z as V
                u_raw = rel[:, u_axis]
                v_raw = rel[:, v_axis]
            else:  # PLANAR_Z
                u_axis, v_axis = 0, 1   # X as U, Y as V
                u_raw = rel[:, u_axis]
                v_raw = rel[:, v_axis]

            ref = float(max(bbox_size.max(), 1e-6))
            pu = u_raw / ref
            pv = v_raw / ref

        elif mode == M.BOX:
            # Dominant axis tri-planar.
            ref = float(max(bbox_size.max(), 1e-6))
            abs_rel = numpy.abs(rel)
            dominant = numpy.argmax(abs_rel, axis = -1)  # (N,)

            pu = numpy.zeros(len(rel), dtype = numpy.float32)
            pv = numpy.zeros(len(rel), dtype = numpy.float32)

            mask_x = dominant == 0
            mask_y = dominant == 1
            mask_z = dominant == 2

            # For X-dominant: use Z, Y
            pu[mask_x] = -rel[mask_x, 2] / ref
            pv[mask_x] =  rel[mask_x, 1] / ref
            # For Y-dominant: use X, Z
            pu[mask_y] = rel[mask_y, 0] / ref
            pv[mask_y] = rel[mask_y, 2] / ref
            # For Z-dominant: use X, Y
            pu[mask_z] = rel[mask_z, 0] / ref
            pv[mask_z] = rel[mask_z, 1] / ref

        elif mode == M.SPHERICAL:
            r = numpy.linalg.norm(rel, axis = -1)
            r_safe = numpy.maximum(r, 1e-9)
            # Azimuth around Y axis, from +X towards +Z
            theta = numpy.arctan2(rel[:, 2], rel[:, 0])         # [-pi, pi]
            phi = numpy.arcsin(numpy.clip(rel[:, 1] / r_safe, -1.0, 1.0))  # [-pi/2, pi/2]
            pu = theta / numpy.pi        # [-1, 1]
            pv = (2.0 * phi) / numpy.pi  # [-1, 1]

        elif mode == M.CYLINDRICAL:
            theta = numpy.arctan2(rel[:, 2], rel[:, 0])
            pu = theta / numpy.pi
            ref_h = float(max(bbox_size[1], 1e-6))
            pv = rel[:, 1] / ref_h
        else:
            return numpy.full((len(rel), 2), numpy.nan, dtype = numpy.float32)

        # Apply user transform (offset / rotate / scale) in projection space.
        pu = pu - self._transform.offset_u
        pv = pv - self._transform.offset_v

        angle = numpy.radians(self._transform.rotation_deg)
        cos_r = numpy.cos(angle)
        sin_r = numpy.sin(angle)
        pu_r = pu * cos_r - pv * sin_r
        pv_r = pu * sin_r + pv * cos_r

        # Map to image UV. Image width covers 2 * scale of projection units,
        # so (pu_r / scale) goes from -1..1 and we shift to 0..1.
        scale = max(float(self._transform.scale), 1e-6)
        img_u = (pu_r / (2.0 * scale)) + 0.5

        aspect = 1.0
        if self._source_image is not None and self._source_image.width() > 0:
            aspect = self._source_image.height() / self._source_image.width()
        img_v = (pv_r / (2.0 * scale * aspect)) + 0.5

        return numpy.stack([img_u, img_v], axis = -1).astype(numpy.float32)

    def _sample_bitmap(self, img_uvs: numpy.ndarray) -> numpy.ndarray:
        """Nearest-neighbour sample the cached 1-bit bitmap at (N, 2) UVs.

        Out-of-range UVs return False.
        """
        if self._bitmap is None or img_uvs.size == 0:
            return numpy.zeros(len(img_uvs), dtype = bool)

        h, w = self._bitmap.shape
        u = img_uvs[:, 0]
        v = img_uvs[:, 1]

        in_range = (u >= 0.0) & (u < 1.0) & (v >= 0.0) & (v < 1.0)
        result = numpy.zeros(len(img_uvs), dtype = bool)
        if not numpy.any(in_range):
            return result

        xs = numpy.clip((u[in_range] * w).astype(numpy.int32), 0, w - 1)
        # Image coordinates: V=0 is top of image, but our projection has +V up.
        # Flip so the image is not upside-down on the model.
        ys = numpy.clip(((1.0 - v[in_range]) * h).astype(numpy.int32), 0, h - 1)
        result[in_range] = self._bitmap[ys, xs]
        return result

    def build_texture_mask(self,
                           world_vertices: numpy.ndarray,
                           uv_coords: numpy.ndarray,
                           indices: Optional[numpy.ndarray],
                           texture_width: int,
                           texture_height: int,
                           bbox_size: numpy.ndarray,
                           camera_forward: Optional[numpy.ndarray] = None,
                           cull_backfaces: bool = True) -> Optional[numpy.ndarray]:
        """Rasterize the projection into a (H, W) bool numpy mask in paint-texture space.

        :param world_vertices: (V, 3) mesh vertices already in world-space
        :param uv_coords: (V, 2) mesh UVs in [0, 1]
        :param indices: (T, 3) triangle indices, or None for unindexed meshes
        :param texture_width/height: paint texture dimensions
        :param bbox_size: (3,) world-space bounding-box size (max - min) of the mesh
        :param camera_forward: optional (3,) camera direction; used to backface-cull
                               so we don't paint the far side of the model as well.
        :param cull_backfaces: whether to cull triangles facing away from the camera
        :return: bool mask, or None if no bitmap is loaded.
        """
        if self._bitmap is None:
            return None
        if texture_width <= 0 or texture_height <= 0:
            return None

        if world_vertices is None or world_vertices.size == 0:
            return None
        if uv_coords is None or uv_coords.size == 0:
            return None

        world_vertices = numpy.asarray(world_vertices, dtype = numpy.float32)
        uv_coords = numpy.asarray(uv_coords, dtype = numpy.float32)

        if indices is not None and len(indices) > 0:
            indices = numpy.asarray(indices, dtype = numpy.int32).reshape((-1, 3))
            tri_verts = world_vertices[indices]
            tri_uvs = uv_coords[indices]
        else:
            # Unindexed: treat vertices as sequential triangle list
            n_tris = len(world_vertices) // 3
            tri_verts = world_vertices[: n_tris * 3].reshape((n_tris, 3, 3))
            tri_uvs = uv_coords[: n_tris * 3].reshape((n_tris, 3, 2))

        n_tris = len(tri_verts)
        if n_tris == 0:
            return None

        mask = numpy.zeros((texture_height, texture_width), dtype = bool)

        # Pre-compute UV pixel coordinates for all triangles.
        tri_uvs_px = tri_uvs.copy()
        tri_uvs_px[..., 0] *= texture_width
        tri_uvs_px[..., 1] *= texture_height

        # Backface-culling (optional): skip triangles whose normal points away
        # from the camera in world-space. This prevents image projection from
        # wrapping to the far side of the model automatically.
        if cull_backfaces and camera_forward is not None:
            edges_a = tri_verts[:, 1] - tri_verts[:, 0]
            edges_b = tri_verts[:, 2] - tri_verts[:, 0]
            normals = numpy.cross(edges_a, edges_b)
            # camera_forward points from camera towards scene; a front-facing
            # triangle has normal . (-camera_forward) > 0.
            dots = normals @ (-numpy.asarray(camera_forward, dtype = numpy.float32))
            visible_tris = dots > 0.0
        else:
            visible_tris = numpy.ones(n_tris, dtype = bool)

        for i in range(n_tris):
            if not visible_tris[i]:
                continue

            uv_tri = tri_uvs_px[i]
            world_tri = tri_verts[i]

            uv0, uv1, uv2 = uv_tri[0], uv_tri[1], uv_tri[2]
            w0, w1, w2 = world_tri[0], world_tri[1], world_tri[2]

            min_u = int(numpy.floor(min(uv0[0], uv1[0], uv2[0])))
            max_u = int(numpy.ceil(max(uv0[0], uv1[0], uv2[0])))
            min_v = int(numpy.floor(min(uv0[1], uv1[1], uv2[1])))
            max_v = int(numpy.ceil(max(uv0[1], uv1[1], uv2[1])))

            min_u = max(0, min_u)
            max_u = min(texture_width - 1, max_u)
            min_v = max(0, min_v)
            max_v = min(texture_height - 1, max_v)
            if max_u < min_u or max_v < min_v:
                continue

            # Build a grid of pixel-center coordinates.
            us = numpy.arange(min_u, max_u + 1, dtype = numpy.float32) + 0.5
            vs = numpy.arange(min_v, max_v + 1, dtype = numpy.float32) + 0.5
            uu, vv = numpy.meshgrid(us, vs)

            # Barycentric coordinates relative to the UV triangle.
            e0 = uv1 - uv0
            e1 = uv2 - uv0
            d00 = float(e0 @ e0)
            d01 = float(e0 @ e1)
            d11 = float(e1 @ e1)
            denom = d00 * d11 - d01 * d01
            if abs(denom) < 1e-12:
                continue

            px = uu - uv0[0]
            py = vv - uv0[1]
            d20 = px * e0[0] + py * e0[1]
            d21 = px * e1[0] + py * e1[1]

            v_bc = (d11 * d20 - d01 * d21) / denom
            w_bc = (d00 * d21 - d01 * d20) / denom
            u_bc = 1.0 - v_bc - w_bc

            inside = (u_bc >= 0.0) & (v_bc >= 0.0) & (w_bc >= 0.0)
            if not numpy.any(inside):
                continue

            # Interpolate world positions for texels inside the triangle.
            world_x = u_bc * w0[0] + v_bc * w1[0] + w_bc * w2[0]
            world_y = u_bc * w0[1] + v_bc * w1[1] + w_bc * w2[1]
            world_z = u_bc * w0[2] + v_bc * w1[2] + w_bc * w2[2]

            world_inside = numpy.stack([
                world_x[inside], world_y[inside], world_z[inside]
            ], axis = -1)

            img_uvs = self._project_world_to_image_uv(world_inside, bbox_size)
            hits = self._sample_bitmap(img_uvs)
            if not numpy.any(hits):
                continue

            # Write texels that hit an "on" pixel into the mask.
            rows_local, cols_local = numpy.where(inside)
            rows_hit = rows_local[hits]
            cols_hit = cols_local[hits]
            mask[min_v + rows_hit, min_u + cols_hit] = True

        return mask
