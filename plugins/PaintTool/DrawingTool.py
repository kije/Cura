# Copyright (c) 2025 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, List, Tuple, TYPE_CHECKING

import math
import numpy
from PyQt6.QtCore import Qt, QObject, pyqtEnum

from UM.Math.Polygon import Polygon

if TYPE_CHECKING:
    from UM.Mesh.MeshData import MeshData
    from UM.View.GL.Texture import Texture


class DrawingMode(QObject):
    @pyqtEnum
    class Mode(IntEnum):
        FREEHAND = 0
        LINE = 1
        RECTANGLE = 2
        CIRCLE = 3
        POLYGON = 4
        FILL = 5


@dataclass
class DrawingToolResult:
    """Result returned by a drawing tool after processing an event."""
    polygons: List[Polygon] = field(default_factory=list)
    is_preview: bool = False
    merge_with_previous: bool = False
    is_uv_space: bool = False


class DrawingTool(ABC):
    """Abstract base class for all drawing tools in the PaintTool system.

    Each tool manages its own state machine for multi-step interactions
    and produces viewport-space Polygons as output that feed into the
    existing UV projection pipeline.
    """

    @abstractmethod
    def handle_press(self, world_coords: numpy.ndarray, face_id: int,
                     viewport_pos: numpy.ndarray, modifiers: Qt.KeyboardModifier) -> Optional[DrawingToolResult]:
        pass

    @abstractmethod
    def handle_move(self, world_coords: numpy.ndarray, face_id: int,
                    viewport_pos: numpy.ndarray, modifiers: Qt.KeyboardModifier) -> Optional[DrawingToolResult]:
        pass

    @abstractmethod
    def handle_release(self, world_coords: numpy.ndarray, face_id: int,
                       viewport_pos: numpy.ndarray, modifiers: Qt.KeyboardModifier) -> Optional[DrawingToolResult]:
        pass

    def cancel(self) -> None:
        """Reset tool state. Called when Escape is pressed."""
        pass

    def is_active(self) -> bool:
        """Whether the tool is mid-interaction (accumulating input)."""
        return False

    def needs_brush_size(self) -> bool:
        """Whether this tool uses the brush size parameter."""
        return True

    def needs_brush_shape(self) -> bool:
        """Whether this tool uses the brush shape parameter."""
        return True


class FreehandTool(DrawingTool):
    """Freehand brush painting tool — wraps the existing painting behavior.

    Press+drag to paint a continuous stroke. Brush shape (circle/square)
    and size are applied along the stroke path.
    """

    def __init__(self) -> None:
        self._mouse_held: bool = False
        self._last_world_coords: Optional[numpy.ndarray] = None
        self._brush_size: int = 10
        self._brush_shape: int = 1  # 0=SQUARE, 1=CIRCLE
        self._stabilize: bool = False
        self._stabilize_strength: int = 5
        self._position_buffer: deque = deque(maxlen=20)

    def set_brush_params(self, size: int, shape: int) -> None:
        self._brush_size = size
        self._brush_shape = shape

    def set_stabilize(self, enabled: bool, strength: int = 5) -> None:
        self._stabilize = enabled
        self._stabilize_strength = max(2, min(20, strength))
        self._position_buffer = deque(maxlen=self._stabilize_strength)

    def _get_stroke_polygon(self, stroke_a: numpy.ndarray, stroke_b: numpy.ndarray) -> Polygon:
        side = self._brush_size
        if self._brush_shape == 0:  # SQUARE
            shape = Polygon(numpy.array([(side, side), (-side, side), (-side, -side), (side, -side)]))
        else:  # CIRCLE
            shape = Polygon.approximatedCircle(side, 32)
        return shape.translate(stroke_a[0], stroke_a[1]).unionConvexHulls(
            shape.translate(stroke_b[0], stroke_b[1]))

    def _stabilized_position(self, world_coords: numpy.ndarray) -> numpy.ndarray:
        if not self._stabilize:
            return world_coords
        self._position_buffer.append(world_coords.copy())
        if len(self._position_buffer) < 2:
            return world_coords
        positions = numpy.array(self._position_buffer)
        weights = numpy.linspace(0.5, 1.0, len(positions))
        weights /= weights.sum()
        return numpy.average(positions, axis=0, weights=weights)

    def handle_press(self, world_coords, face_id, viewport_pos, modifiers):
        self._mouse_held = True
        self._last_world_coords = world_coords
        self._position_buffer.clear()
        if self._stabilize:
            self._position_buffer.append(world_coords.copy())
        return None

    def handle_move(self, world_coords, face_id, viewport_pos, modifiers):
        stabilized = self._stabilized_position(world_coords)
        if not self._mouse_held:
            return None
        if self._last_world_coords is None:
            self._last_world_coords = stabilized
            return None
        result = DrawingToolResult(merge_with_previous=True)
        result._world_coords_start = self._last_world_coords
        result._world_coords_end = stabilized
        result._face_id = face_id
        self._last_world_coords = stabilized
        return result

    def handle_release(self, world_coords, face_id, viewport_pos, modifiers):
        self._mouse_held = False
        self._last_world_coords = None
        self._position_buffer.clear()
        return None

    def is_active(self) -> bool:
        return self._mouse_held


class LineTool(DrawingTool):
    """Precision line drawing tool.

    Click to set start point, move to preview, click again to commit.
    Shift constrains to 45-degree angle increments.
    """

    def __init__(self) -> None:
        self._start_world: Optional[numpy.ndarray] = None
        self._start_face_id: int = -1
        self._brush_size: int = 10
        self._brush_shape: int = 1

    def set_brush_params(self, size: int, shape: int) -> None:
        self._brush_size = size
        self._brush_shape = shape

    @staticmethod
    def _snap_angle(start: numpy.ndarray, end: numpy.ndarray) -> numpy.ndarray:
        """Snap the line angle to nearest 45-degree increment."""
        delta = end - start
        angle = math.atan2(delta[1], delta[0])
        snapped_angle = round(angle / (math.pi / 4)) * (math.pi / 4)
        length = numpy.linalg.norm(delta)
        return start + numpy.array([math.cos(snapped_angle), math.sin(snapped_angle)]) * length

    def _make_line_polygon(self, vp_a: numpy.ndarray, vp_b: numpy.ndarray) -> Polygon:
        """Create an oriented rectangle along the line with brush width."""
        direction = vp_b - vp_a
        length = numpy.linalg.norm(direction)
        if length < 1e-6:
            if self._brush_shape == 0:
                s = self._brush_size
                return Polygon(numpy.array([(vp_a[0]+s, vp_a[1]+s), (vp_a[0]-s, vp_a[1]+s),
                                            (vp_a[0]-s, vp_a[1]-s), (vp_a[0]+s, vp_a[1]-s)]))
            return Polygon.approximatedCircle(self._brush_size, 32).translate(vp_a[0], vp_a[1])

        direction = direction / length
        perp = numpy.array([-direction[1], direction[0]])
        half_w = self._brush_size

        corners = numpy.array([
            vp_a + perp * half_w,
            vp_a - perp * half_w,
            vp_b - perp * half_w,
            vp_b + perp * half_w,
        ])
        return Polygon(corners)

    def handle_press(self, world_coords, face_id, viewport_pos, modifiers):
        if self._start_world is None:
            self._start_world = world_coords.copy()
            self._start_face_id = face_id
            return None
        else:
            # Second click: commit
            end_world = world_coords
            result = DrawingToolResult()
            result._world_coords_start = self._start_world
            result._world_coords_end = end_world
            result._face_id = face_id
            result._snap = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            result._line_tool = self
            self._start_world = None
            self._start_face_id = -1
            return result

    def handle_move(self, world_coords, face_id, viewport_pos, modifiers):
        if self._start_world is None:
            return None
        result = DrawingToolResult(is_preview=True)
        result._world_coords_start = self._start_world
        result._world_coords_end = world_coords
        result._face_id = face_id
        result._snap = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        result._line_tool = self
        return result

    def handle_release(self, world_coords, face_id, viewport_pos, modifiers):
        return None

    def cancel(self):
        self._start_world = None
        self._start_face_id = -1

    def is_active(self):
        return self._start_world is not None

    def needs_brush_shape(self):
        return False


class RectangleTool(DrawingTool):
    """Rectangle drawing tool.

    Press to set first corner, drag, release to set opposite corner.
    Ctrl constrains to square.
    """

    def __init__(self) -> None:
        self._start_world: Optional[numpy.ndarray] = None
        self._start_face_id: int = -1
        self._dragging: bool = False

    @staticmethod
    def _make_rect_polygon(vp_a: numpy.ndarray, vp_b: numpy.ndarray, constrain_square: bool) -> Polygon:
        if constrain_square:
            dx = vp_b[0] - vp_a[0]
            dy = vp_b[1] - vp_a[1]
            side = max(abs(dx), abs(dy))
            vp_b = numpy.array([
                vp_a[0] + math.copysign(side, dx),
                vp_a[1] + math.copysign(side, dy)
            ])

        return Polygon(numpy.array([
            [vp_a[0], vp_a[1]],
            [vp_b[0], vp_a[1]],
            [vp_b[0], vp_b[1]],
            [vp_a[0], vp_b[1]],
        ]))

    def handle_press(self, world_coords, face_id, viewport_pos, modifiers):
        self._start_world = world_coords.copy()
        self._start_face_id = face_id
        self._dragging = True
        return None

    def handle_move(self, world_coords, face_id, viewport_pos, modifiers):
        if not self._dragging or self._start_world is None:
            return None
        result = DrawingToolResult(is_preview=True)
        result._world_coords_start = self._start_world
        result._world_coords_end = world_coords
        result._face_id = face_id
        result._constrain = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        return result

    def handle_release(self, world_coords, face_id, viewport_pos, modifiers):
        if not self._dragging or self._start_world is None:
            return None
        self._dragging = False
        result = DrawingToolResult()
        result._world_coords_start = self._start_world
        result._world_coords_end = world_coords
        result._face_id = face_id
        result._constrain = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        self._start_world = None
        self._start_face_id = -1
        return result

    def cancel(self):
        self._start_world = None
        self._start_face_id = -1
        self._dragging = False

    def is_active(self):
        return self._dragging

    def needs_brush_size(self):
        return False

    def needs_brush_shape(self):
        return False


class CircleTool(DrawingTool):
    """Circle/ellipse drawing tool.

    Press to set center, drag to define radius, release to commit.
    Ctrl constrains to perfect circle (otherwise allows ellipse).
    """

    NUM_SEGMENTS = 64

    def __init__(self) -> None:
        self._center_world: Optional[numpy.ndarray] = None
        self._center_face_id: int = -1
        self._dragging: bool = False

    @staticmethod
    def _make_circle_polygon(vp_center: numpy.ndarray, vp_edge: numpy.ndarray, constrain_circle: bool) -> Polygon:
        dx = abs(vp_edge[0] - vp_center[0])
        dy = abs(vp_edge[1] - vp_center[1])

        if constrain_circle:
            radius = math.sqrt(dx * dx + dy * dy)
            rx = ry = radius
        else:
            rx = max(dx, 1.0)
            ry = max(dy, 1.0)

        points = []
        for i in range(CircleTool.NUM_SEGMENTS):
            angle = 2 * math.pi * i / CircleTool.NUM_SEGMENTS
            points.append([
                vp_center[0] + rx * math.cos(angle),
                vp_center[1] + ry * math.sin(angle),
            ])
        return Polygon(numpy.array(points))

    def handle_press(self, world_coords, face_id, viewport_pos, modifiers):
        self._center_world = world_coords.copy()
        self._center_face_id = face_id
        self._dragging = True
        return None

    def handle_move(self, world_coords, face_id, viewport_pos, modifiers):
        if not self._dragging or self._center_world is None:
            return None
        result = DrawingToolResult(is_preview=True)
        result._world_coords_start = self._center_world
        result._world_coords_end = world_coords
        result._face_id = face_id
        result._constrain = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        return result

    def handle_release(self, world_coords, face_id, viewport_pos, modifiers):
        if not self._dragging or self._center_world is None:
            return None
        self._dragging = False
        result = DrawingToolResult()
        result._world_coords_start = self._center_world
        result._world_coords_end = world_coords
        result._face_id = face_id
        result._constrain = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        self._center_world = None
        self._center_face_id = -1
        return result

    def cancel(self):
        self._center_world = None
        self._center_face_id = -1
        self._dragging = False

    def is_active(self):
        return self._dragging

    def needs_brush_size(self):
        return False

    def needs_brush_shape(self):
        return False


class PolygonTool(DrawingTool):
    """Multi-point polygon drawing tool.

    Click to add vertices. Double-click or click near the first vertex
    to close and commit the polygon. Escape cancels.
    """

    CLOSE_DISTANCE = 15.0  # pixels

    def __init__(self) -> None:
        self._vertices_world: List[numpy.ndarray] = []
        self._vertices_vp: List[numpy.ndarray] = []
        self._last_face_id: int = -1
        self._last_click_time: float = 0.0

    def handle_press(self, world_coords, face_id, viewport_pos, modifiers):
        import time

        current_time = time.time()
        is_double_click = (current_time - self._last_click_time) < 0.35
        self._last_click_time = current_time

        if len(self._vertices_vp) >= 3:
            # Check if clicking near the first vertex (close polygon)
            first = self._vertices_vp[0]
            dist = numpy.linalg.norm(viewport_pos - first)
            if dist < self.CLOSE_DISTANCE or is_double_click:
                return self._commit(face_id)

        self._vertices_world.append(world_coords.copy())
        self._vertices_vp.append(viewport_pos.copy())
        self._last_face_id = face_id
        return None

    def handle_move(self, world_coords, face_id, viewport_pos, modifiers):
        if not self._vertices_world:
            return None
        # Preview: polygon through all vertices + current mouse position
        result = DrawingToolResult(is_preview=True)
        result._polygon_vertices_world = self._vertices_world + [world_coords]
        result._face_id = face_id
        return result

    def handle_release(self, world_coords, face_id, viewport_pos, modifiers):
        return None

    def _commit(self, face_id: int) -> DrawingToolResult:
        result = DrawingToolResult()
        result._polygon_vertices_world = list(self._vertices_world)
        result._face_id = face_id
        self._vertices_world.clear()
        self._vertices_vp.clear()
        return result

    def cancel(self):
        self._vertices_world.clear()
        self._vertices_vp.clear()
        self._last_face_id = -1

    def is_active(self):
        return len(self._vertices_world) > 0

    def needs_brush_size(self):
        return False

    def needs_brush_shape(self):
        return False


class FillTool(DrawingTool):
    """Flood-fill tool that fills connected faces sharing the same paint value.

    Single click to fill. Uses BFS on the mesh face adjacency graph.
    Produces UV-space triangles directly, bypassing the viewport->UV projection.
    """

    MAX_FILL_FACES = 50000

    def __init__(self) -> None:
        self._mesh_data: Optional["MeshData"] = None
        self._paint_texture: Optional["Texture"] = None
        self._bit_range: Tuple[int, int] = (0, 0)

    def set_mesh_context(self, mesh_data: Optional["MeshData"],
                         paint_texture: Optional["Texture"],
                         bit_range: Tuple[int, int]) -> None:
        self._mesh_data = mesh_data
        self._paint_texture = paint_texture
        self._bit_range = bit_range

    def _get_face_paint_value(self, face_id: int) -> int:
        """Read the paint value of a face from the texture at its UV centroid."""
        if self._mesh_data is None or self._paint_texture is None:
            return -1

        uv_coords = self._mesh_data.getUVCoordinates()
        indices = self._mesh_data.getIndices()
        image = self._paint_texture.getImage()
        if uv_coords is None or image is None:
            return -1

        if indices is not None:
            i0, i1, i2 = indices[face_id]
        else:
            i0, i1, i2 = face_id * 3, face_id * 3 + 1, face_id * 3 + 2

        centroid_u = (uv_coords[i0][0] + uv_coords[i1][0] + uv_coords[i2][0]) / 3.0
        centroid_v = (uv_coords[i0][1] + uv_coords[i1][1] + uv_coords[i2][1]) / 3.0

        tex_x = int(centroid_u * image.width()) % image.width()
        tex_y = int(centroid_v * image.height()) % image.height()

        pixel = image.pixel(tex_x, tex_y)
        bit_start, bit_end = self._bit_range
        mask = ((0xFFFFFFFF << (32 - 1 - (bit_end - bit_start))) & 0xFFFFFFFF) >> (32 - 1 - bit_end)
        return (pixel & mask) >> bit_start

    def _flood_fill(self, start_face_id: int) -> List[int]:
        """BFS flood fill from start face, stopping at paint boundaries."""
        if self._mesh_data is None:
            return []

        connections = self._mesh_data.getFacesConnections()
        if connections is None:
            return []

        target_value = self._get_face_paint_value(start_face_id)
        if target_value < 0:
            return [start_face_id]

        visited = set()
        queue = deque([start_face_id])
        filled = []

        while queue and len(filled) < self.MAX_FILL_FACES:
            face = queue.popleft()
            if face in visited:
                continue
            visited.add(face)

            if self._get_face_paint_value(face) != target_value:
                continue

            filled.append(face)

            for neighbor in connections[face]:
                if neighbor >= 0 and neighbor not in visited:
                    queue.append(neighbor)

        return filled

    def _faces_to_uv_polygons(self, face_ids: List[int]) -> List[Polygon]:
        """Convert face indices to UV-space triangle polygons."""
        if self._mesh_data is None or self._paint_texture is None:
            return []

        uv_coords = self._mesh_data.getUVCoordinates()
        indices = self._mesh_data.getIndices()
        image = self._paint_texture.getImage()
        if uv_coords is None or image is None:
            return []

        w, h = image.width(), image.height()
        polygons = []
        for face_id in face_ids:
            if indices is not None:
                i0, i1, i2 = indices[face_id]
            else:
                i0, i1, i2 = face_id * 3, face_id * 3 + 1, face_id * 3 + 2

            tri = numpy.array([
                [uv_coords[i0][0] * w, uv_coords[i0][1] * h],
                [uv_coords[i1][0] * w, uv_coords[i1][1] * h],
                [uv_coords[i2][0] * w, uv_coords[i2][1] * h],
            ])
            polygons.append(Polygon(tri))

        return polygons

    def handle_press(self, world_coords, face_id, viewport_pos, modifiers):
        filled_faces = self._flood_fill(face_id)
        if not filled_faces:
            return None

        uv_polygons = self._faces_to_uv_polygons(filled_faces)
        if not uv_polygons:
            return None

        return DrawingToolResult(
            polygons=uv_polygons,
            is_uv_space=True,
        )

    def handle_move(self, world_coords, face_id, viewport_pos, modifiers):
        return None

    def handle_release(self, world_coords, face_id, viewport_pos, modifiers):
        return None

    def needs_brush_size(self):
        return False

    def needs_brush_shape(self):
        return False
