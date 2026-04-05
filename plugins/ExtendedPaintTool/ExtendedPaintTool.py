# Copyright (c) 2025 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.
import math

from enum import IntEnum
import numpy
from PyQt6.QtCore import Qt, QObject, pyqtEnum, QPointF
from PyQt6.QtGui import QImage, QPainter, QPen, QBrush, QPolygonF, QPainterPath
from PyQt6.QtWidgets import QApplication
from typing import cast, Optional, Tuple, List, Dict, Set
import pyUvula as uvula

from UM.Application import Application
from UM.Event import Event, MouseEvent, KeyEvent
from UM.Job import Job
from UM.Logger import Logger
from UM.Math.AxisAlignedBox2D import AxisAlignedBox2D
from UM.Math.Polygon import Polygon
from UM.Math.Vector import Vector
from UM.Mesh.MeshData import MeshData
from UM.Scene.Camera import Camera
from UM.Scene.SceneNode import SceneNode
from UM.Scene.Selection import Selection
from UM.Tool import Tool
from UM.View.GL.OpenGLContext import OpenGLContext

from cura.CuraApplication import CuraApplication
from cura.PickingPass import PickingPass
from UM.View.SelectionPass import SelectionPass
from .ExtendedPaintView import ExtendedPaintView
from .PrepareTextureJob import PrepareTextureJob
from .DrawingTool import (
    DrawingMode, DrawingTool, DrawingToolResult,
    FreehandTool, LineTool, RectangleTool, CircleTool, PolygonTool, FillTool,
)


class ExtendedPaintTool(Tool):
    """Provides the tool to paint meshes with professional drawing tools."""

    class Brush(QObject):
        @pyqtEnum
        class Shape(IntEnum):
            SQUARE = 0
            CIRCLE = 1

    class Paint(QObject):
        @pyqtEnum
        class State(IntEnum):
            MULTIPLE_SELECTION = 0 # Multiple objects are selected, wait until there is only one
            PREPARING_MODEL = 1    # Model is being prepared (UV-unwrapping, texture generation)
            READY = 2              # Ready to paint !
            NOT_SUPPORTED = 3      # Painting is not supported (due to OpenGL compatibility mode)

    def __init__(self, view: ExtendedPaintView) -> None:
        super().__init__()

        self._view: ExtendedPaintView = view
        self._view.canUndoChanged.connect(self._onCanUndoChanged)
        self._view.canRedoChanged.connect(self._onCanRedoChanged)
        self._view.currentPaintedObjectMeshDataChanged.connect(self._updateState)

        self._picking_pass: Optional[PickingPass] = None
        self._faces_selection_pass: Optional[SelectionPass] = None

        self._shortcut_key: Qt.Key = Qt.Key.Key_P

        self._node_cache: Optional[SceneNode] = None
        self._mesh_transformed_cache: Optional[MeshData] = None
        self._cache_dirty: bool = True

        self._brush_size: int = 10
        self._brush_color: str = "preferred"
        self._brush_extruder: int = 0
        self._brush_shape: ExtendedPaintTool.Brush.Shape = ExtendedPaintTool.Brush.Shape.CIRCLE
        self._brush_pen: QPen = self._createBrushPen()

        self._mouse_held: bool = False

        self._last_world_coords: Optional[numpy.ndarray] = None

        # Drawing tool system
        self._freehand_tool = FreehandTool()
        self._line_tool = LineTool()
        self._rectangle_tool = RectangleTool()
        self._circle_tool = CircleTool()
        self._polygon_tool = PolygonTool()
        self._fill_tool = FillTool()

        self._drawing_tools: Dict[int, DrawingTool] = {
            DrawingMode.Mode.FREEHAND: self._freehand_tool,
            DrawingMode.Mode.LINE: self._line_tool,
            DrawingMode.Mode.RECTANGLE: self._rectangle_tool,
            DrawingMode.Mode.CIRCLE: self._circle_tool,
            DrawingMode.Mode.POLYGON: self._polygon_tool,
            DrawingMode.Mode.FILL: self._fill_tool,
        }
        self._drawing_mode: DrawingMode.Mode = DrawingMode.Mode.FREEHAND
        self._active_drawing_tool: DrawingTool = self._freehand_tool

        # Symmetry painting
        self._symmetry_x: bool = False
        self._symmetry_y: bool = False
        self._symmetry_z: bool = False

        # Stroke stabilization
        self._stabilize: bool = False
        self._stabilize_strength: int = 5

        legacy_opengl = OpenGLContext.isLegacyOpenGL()
        self._state: ExtendedPaintTool.Paint.State = ExtendedPaintTool.Paint.State.NOT_SUPPORTED if legacy_opengl else\
                                                                                ExtendedPaintTool.Paint.State.MULTIPLE_SELECTION
        self._prepare_texture_job: Optional[PrepareTextureJob] = None

        self.setExposedProperties(
            "PaintType", "BrushSize", "BrushColor", "BrushShape", "BrushExtruder",
            "State", "CanUndo", "CanRedo",
            "DrawingMode", "SymmetryX", "SymmetryY", "SymmetryZ",
            "Stabilize", "StabilizeStrength",
            "LayerStack",
        )

        self._controller.activeViewChanged.connect(self._updateIgnoreUnselectedObjects)
        self._controller.activeToolChanged.connect(self._onActiveToolChanged)
        self._controller.activeStageChanged.connect(self._updateActiveView)

        self._camera: Optional[Camera] = None
        self._cam_pos: numpy.ndarray = numpy.array([0.0, 0.0, 0.0])
        self._cam_norm: numpy.ndarray = numpy.array([0.0, 0.0, 1.0])
        self._cam_axis_q: numpy.ndarray = numpy.array([1.0, 0.0, 0.0])
        self._cam_axis_r: numpy.ndarray = numpy.array([0.0, -1.0, 0.0])

    # --- Drawing Mode ---

    def getDrawingMode(self) -> int:
        return self._drawing_mode

    def setDrawingMode(self, mode: int) -> None:
        mode = DrawingMode.Mode(mode)
        if mode != self._drawing_mode:
            # Cancel any in-progress drawing on the old tool
            self._active_drawing_tool.cancel()
            self._drawing_mode = mode
            self._active_drawing_tool = self._drawing_tools[mode]
            self._syncBrushParamsToTool()
            self.propertyChanged.emit()

    def _syncBrushParamsToTool(self) -> None:
        """Sync brush size/shape to the active drawing tool if it supports them."""
        tool = self._active_drawing_tool
        if hasattr(tool, 'set_brush_params'):
            tool.set_brush_params(self._brush_size, self._brush_shape)
        if isinstance(tool, FreehandTool):
            tool.set_stabilize(self._stabilize, self._stabilize_strength)

    # --- Symmetry ---

    def getSymmetryX(self) -> bool:
        return self._symmetry_x

    def setSymmetryX(self, enabled: bool) -> None:
        if enabled != self._symmetry_x:
            self._symmetry_x = enabled
            self.propertyChanged.emit()

    def getSymmetryY(self) -> bool:
        return self._symmetry_y

    def setSymmetryY(self, enabled: bool) -> None:
        if enabled != self._symmetry_y:
            self._symmetry_y = enabled
            self.propertyChanged.emit()

    def getSymmetryZ(self) -> bool:
        return self._symmetry_z

    def setSymmetryZ(self, enabled: bool) -> None:
        if enabled != self._symmetry_z:
            self._symmetry_z = enabled
            self.propertyChanged.emit()

    # --- Stabilization ---

    def getStabilize(self) -> bool:
        return self._stabilize

    def setStabilize(self, enabled: bool) -> None:
        if enabled != self._stabilize:
            self._stabilize = enabled
            if isinstance(self._active_drawing_tool, FreehandTool):
                self._active_drawing_tool.set_stabilize(enabled, self._stabilize_strength)
            self.propertyChanged.emit()

    def getStabilizeStrength(self) -> int:
        return self._stabilize_strength

    def setStabilizeStrength(self, strength: int) -> None:
        strength = int(strength)
        if strength != self._stabilize_strength:
            self._stabilize_strength = strength
            if isinstance(self._active_drawing_tool, FreehandTool):
                self._active_drawing_tool.set_stabilize(self._stabilize, strength)
            self.propertyChanged.emit()

    # --- Layer Stack ---

    def getLayerStack(self):
        """Get the layer stack for the current painted object and paint type.
        Exposed to QML for the LayerPanel."""
        return self._view.getLayerStack()

    # --- Camera ---

    def _updateCamera(self, *args) -> None:
        if self._camera is None:
            self._camera = Application.getInstance().getController().getScene().getActiveCamera()
            self._camera.transformationChanged.connect(self._updateCamera)
        self._cam_pos = self._camera.getPosition().getData()
        cam_ray = self._camera.getRay(0, 0)
        self._cam_norm = cam_ray.direction.getData()
        self._cam_norm /= -numpy.linalg.norm(self._cam_norm)
        axis_up = numpy.array([0.0, -1.0, 0.0]) if abs(self._cam_norm[1]) < abs(self._cam_norm[2]) else numpy.array([0.0, 0.0, 1.0])
        self._cam_axis_q = numpy.cross(self._cam_norm, axis_up)
        self._cam_axis_q /= numpy.linalg.norm(self._cam_axis_q)
        self._cam_axis_r = numpy.cross(self._cam_axis_q, self._cam_norm)
        self._cam_axis_r /= numpy.linalg.norm(self._cam_axis_r)

    # --- Brush ---

    def _createBrushPen(self) -> QPen:
        pen = QPen()
        pen.setWidth(2)
        pen.setColor(Qt.GlobalColor.white)

        match self._brush_shape:
            case ExtendedPaintTool.Brush.Shape.SQUARE:
                pen.setCapStyle(Qt.PenCapStyle.SquareCap)
            case ExtendedPaintTool.Brush.Shape.CIRCLE:
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            case _:
                Logger.error(f"Unknown brush shape '{self._brush_shape}', painting may not work.")
        return pen

    def _createStrokePath(self, polygons: List[Polygon]) -> QPainterPath:
        path = QPainterPath()

        for polygon in polygons:
            path.moveTo(polygon[0][0], polygon[0][1])
            for point in polygon:
                path.lineTo(point[0], point[1])
            path.closeSubpath()

        return path

    def getPaintType(self) -> str:
        return self._view.getPaintType()

    def setPaintType(self, paint_type: str) -> None:
        if paint_type != self.getPaintType():
            self._view.setPaintType(paint_type)

            self._brush_pen = self._createBrushPen()
            self._updateScene()
            self.propertyChanged.emit()

    def getBrushSize(self) -> int:
        return self._brush_size

    def setBrushSize(self, brush_size: float) -> None:
        brush_size_int = int(brush_size)
        if brush_size_int != self._brush_size:
            self._brush_size = brush_size_int
            self._brush_pen = self._createBrushPen()
            self._syncBrushParamsToTool()
            self.propertyChanged.emit()

    def getBrushColor(self) -> str:
        return self._brush_color

    def setBrushColor(self, brush_color: str) -> None:
        if brush_color != self._brush_color:
            self._brush_color = brush_color
            self.propertyChanged.emit()

    def getBrushExtruder(self) -> int:
        return self._brush_extruder

    def setBrushExtruder(self, brush_extruder: int) -> None:
        if brush_extruder != self._brush_extruder:
            self._brush_extruder = brush_extruder
            self.propertyChanged.emit()

    def getBrushShape(self) -> int:
        return self._brush_shape

    def setBrushShape(self, brush_shape: int) -> None:
        if brush_shape != self._brush_shape:
            self._brush_shape = brush_shape
            self._brush_pen = self._createBrushPen()
            self._syncBrushParamsToTool()
            self.propertyChanged.emit()

    def getCanUndo(self) -> bool:
        return self._view.canUndo()

    def getCanRedo(self) -> bool:
        return self._view.canRedo()

    def getState(self) -> int:
        return self._state

    def _onCanUndoChanged(self):
        self.propertyChanged.emit()

    def _onCanRedoChanged(self):
        self.propertyChanged.emit()

    def undoStackAction(self) -> None:
        self._view.undoStroke()
        self._updateScene(update_node = True)

    def redoStackAction(self) -> None:
        self._view.redoStroke()
        self._updateScene(update_node = True)

    def clear(self) -> None:
        self._view.clearPaint()
        self._updateScene(update_node = True)

    def _nodeTransformChanged(self, *args) -> None:
        self._cache_dirty = True

    @staticmethod
    def _getBarycentricCoordinates(points: numpy.array, triangle: numpy.array) -> Optional[numpy.array]:
        v0 = triangle[1] - triangle[0]
        v1 = triangle[2] - triangle[0]
        v2 = points - triangle[0]

        d00 = numpy.sum(v0 * v0, axis=0)
        d01 = numpy.sum(v0 * v1, axis=0)
        d11 = numpy.sum(v1 * v1, axis=0)
        d20 = numpy.sum(v2 * v0, axis=1)
        d21 = numpy.sum(v2 * v1, axis=1)

        denominator = d00 * d11 - d01 ** 2

        if denominator < 1e-6:  # Degenerate triangle
            return None

        v = (d11 * d20 - d01 * d21) / denominator
        w = (d00 * d21 - d01 * d20) / denominator
        u = 1 - v - w

        return numpy.column_stack((u, v, w))

    def _getStrokePolygon(self, stroke_a: numpy.ndarray, stroke_b: numpy.ndarray) -> Polygon:
        shape = None
        side = self._brush_size
        match self._brush_shape:
            case ExtendedPaintTool.Brush.Shape.SQUARE:
                shape = Polygon([(side, side), (-side, side), (-side, -side), (side, -side)])
            case ExtendedPaintTool.Brush.Shape.CIRCLE:
                shape = Polygon.approximatedCircle(side, 32)
            case _:
                Logger.error(f"Unknown brush shape '{self._brush_shape}'.")
        if shape is None:
            return Polygon()
        return shape.translate(stroke_a[0], stroke_a[1]).unionConvexHulls(shape.translate(stroke_b[0], stroke_b[1]))

    # NOTE: Currently, it's unclear how well this would work for non-convex brush-shapes.
    def _getUvAreasForStroke(self, world_coords_a: numpy.ndarray, world_coords_b: numpy.ndarray, face_id: int) -> List[Polygon]:
        """ Fetches all texture-coordinate areas within the provided stroke on the mesh.

        Calculates intersections of the stroke with the surface of the geometry and maps them to UV-space polygons.

        :param world_coords_a: 3D ('world') coordinates corresponding to the starting stroke point.
        :param world_coords_b: 3D ('world') coordinates corresponding to the ending stroke point.
        :param face_id: the ID of the face at the center of the stroke
        :return: A list of UV-mapped polygons representing areas intersected by the stroke on the node's mesh surface.
        """

        def get_projected_on_plane(pt: numpy.ndarray) -> numpy.ndarray:
            return numpy.array([*self._camera.projectToViewport(Vector(*pt))], dtype=numpy.float32)

        stroke_poly = self._getStrokePolygon(get_projected_on_plane(world_coords_a), get_projected_on_plane(world_coords_b))
        stroke_poly.toType(numpy.float32)

        mesh_indices = self._mesh_transformed_cache.getIndices()
        if mesh_indices is None:
            mesh_indices = numpy.array([], dtype=numpy.int32)

        res = uvula.project(stroke_poly.getPoints(),
                            self._mesh_transformed_cache.getVertices(),
                            mesh_indices,
                            self._node_cache.getMeshData().getUVCoordinates(),
                            self._node_cache.getMeshData().getFacesConnections(),
                            self._view.getUvTexDimensions()[0],
                            self._view.getUvTexDimensions()[1],
                            self._camera.getProjectToViewMatrix().getData(),
                            self._camera.isPerspective(),
                            self._camera.getViewportWidth(),
                            self._camera.getViewportHeight(),
                            self._cam_norm,
                            face_id)
        return [Polygon(points) for points in res]

    def _getUvAreasForViewportPolygon(self, viewport_polygon: Polygon, face_id: int) -> List[Polygon]:
        """Project a viewport-space polygon to UV space using pyUvula."""
        viewport_polygon.toType(numpy.float32)

        mesh_indices = self._mesh_transformed_cache.getIndices()
        if mesh_indices is None:
            mesh_indices = numpy.array([], dtype=numpy.int32)

        res = uvula.project(viewport_polygon.getPoints(),
                            self._mesh_transformed_cache.getVertices(),
                            mesh_indices,
                            self._node_cache.getMeshData().getUVCoordinates(),
                            self._node_cache.getMeshData().getFacesConnections(),
                            self._view.getUvTexDimensions()[0],
                            self._view.getUvTexDimensions()[1],
                            self._camera.getProjectToViewMatrix().getData(),
                            self._camera.isPerspective(),
                            self._camera.getViewportWidth(),
                            self._camera.getViewportHeight(),
                            self._cam_norm,
                            face_id)
        return [Polygon(points) for points in res]

    def _getViewportPos(self, world_coords: numpy.ndarray) -> numpy.ndarray:
        """Project 3D world coords to 2D viewport coordinates."""
        return numpy.array([*self._camera.projectToViewport(Vector(*world_coords))], dtype=numpy.float32)

    def _reflectWorldCoords(self, world_coords: numpy.ndarray) -> List[numpy.ndarray]:
        """Generate mirrored copies of world coordinates based on enabled symmetry axes.

        Returns a list of reflected coordinate sets (not including the original).
        """
        if not (self._symmetry_x or self._symmetry_y or self._symmetry_z):
            return []

        painted_object = self._view.getPaintedObject()
        if painted_object is None:
            return []

        center = painted_object.getBoundingBox().center.getData()

        axes = []
        if self._symmetry_x:
            axes.append(0)
        if self._symmetry_y:
            axes.append(1)
        if self._symmetry_z:
            axes.append(2)

        # Generate all combinations of axis reflections
        reflections = []
        for i in range(1, 1 << len(axes)):
            reflected = world_coords.copy()
            for bit, axis in enumerate(axes):
                if i & (1 << bit):
                    reflected[axis] = 2 * center[axis] - reflected[axis]
            reflections.append(reflected)

        return reflections

    def _processDrawingToolResult(self, result: DrawingToolResult, face_id: int,
                                   brush_color: str, painted_object: SceneNode) -> bool:
        """Process a DrawingToolResult from a drawing tool, converting to UV and painting."""
        if result is None:
            return False

        event_caught = False
        try:
            if result.is_uv_space:
                # Fill tool: UV polygons provided directly
                if result.polygons and not result.is_preview:
                    stroke_path = self._createStrokePath(result.polygons)
                    self._view.addStroke(result.polygons, brush_color, result.merge_with_previous)
                    event_caught = True
                return event_caught

            # For tools that provide world coordinates for UV projection
            if hasattr(result, '_world_coords_start') and hasattr(result, '_world_coords_end'):
                world_start = result._world_coords_start
                world_end = result._world_coords_end
                result_face_id = getattr(result, '_face_id', face_id)

                # Handle special polygon generation for geometric tools
                if hasattr(result, '_line_tool'):
                    line_tool = result._line_tool
                    vp_start = self._getViewportPos(world_start)
                    vp_end = self._getViewportPos(world_end)
                    if getattr(result, '_snap', False):
                        vp_end = LineTool._snap_angle(vp_start, vp_end)
                    viewport_poly = line_tool._make_line_polygon(vp_start, vp_end)
                    uv_areas = self._getUvAreasForViewportPolygon(viewport_poly, result_face_id)
                elif hasattr(result, '_constrain') and isinstance(self._active_drawing_tool, RectangleTool):
                    vp_start = self._getViewportPos(world_start)
                    vp_end = self._getViewportPos(world_end)
                    viewport_poly = RectangleTool._make_rect_polygon(vp_start, vp_end, result._constrain)
                    uv_areas = self._getUvAreasForViewportPolygon(viewport_poly, result_face_id)
                elif hasattr(result, '_constrain') and isinstance(self._active_drawing_tool, CircleTool):
                    vp_start = self._getViewportPos(world_start)
                    vp_end = self._getViewportPos(world_end)
                    viewport_poly = CircleTool._make_circle_polygon(vp_start, vp_end, result._constrain)
                    uv_areas = self._getUvAreasForViewportPolygon(viewport_poly, result_face_id)
                else:
                    # Freehand or default: use existing stroke polygon pipeline
                    uv_areas = self._getUvAreasForStroke(world_start, world_end, result_face_id)

                if not uv_areas:
                    return False

                # Apply symmetry: generate mirrored strokes
                all_uv_areas = list(uv_areas)
                if not result.is_preview and (self._symmetry_x or self._symmetry_y or self._symmetry_z):
                    for reflected_start in self._reflectWorldCoords(world_start):
                        reflected_end = reflected_start + (world_end - world_start)
                        for refl_end in self._reflectWorldCoords(world_end):
                            reflected_end = refl_end
                            break
                        try:
                            mirror_uv = self._getUvAreasForStroke(reflected_start, reflected_end, result_face_id)
                            all_uv_areas.extend(mirror_uv)
                        except:
                            pass  # Mirrored position may be off-mesh

                if result.is_preview:
                    cursor_path = self._createStrokePath(uv_areas)
                    self._view.setCursorStroke(cursor_path, brush_color)
                else:
                    self._view.addStroke(all_uv_areas, brush_color, result.merge_with_previous)
                    event_caught = True

            elif hasattr(result, '_polygon_vertices_world'):
                # Polygon tool: vertices in world space
                vertices_world = result._polygon_vertices_world
                result_face_id = getattr(result, '_face_id', face_id)

                if len(vertices_world) >= 3:
                    vp_points = numpy.array([self._getViewportPos(v) for v in vertices_world])
                    viewport_poly = Polygon(vp_points)
                    uv_areas = self._getUvAreasForViewportPolygon(viewport_poly, result_face_id)

                    if uv_areas:
                        if result.is_preview:
                            cursor_path = self._createStrokePath(uv_areas)
                            self._view.setCursorStroke(cursor_path, brush_color)
                        else:
                            self._view.addStroke(uv_areas, brush_color, False)
                            event_caught = True

        except:
            Logger.logException("e", "Error processing drawing tool result")

        return event_caught

    def event(self, event: Event) -> bool:
        """Handle mouse and keyboard events.

        :param event: The event to handle.
        :return: Whether this event has been caught by this tool (True) or should
        be passed on (False).
        """
        super().event(event)

        painted_object = self._view.getPaintedObject()
        if painted_object is None:
            return False

        # Make sure the displayed values are updated if the bounding box of the selected mesh(es) changes
        if event.type == Event.ToolActivateEvent:
            return True

        if event.type == Event.ToolDeactivateEvent:
            self._active_drawing_tool.cancel()
            return True

        if self._state != ExtendedPaintTool.Paint.State.READY:
            return False

        if self._controller.getActiveView() is not self._view:
            return False

        # Handle keyboard events
        if event.type == Event.KeyPressEvent:
            key_evt = cast(KeyEvent, event)
            if key_evt.key == Qt.Key.Key_Escape:
                if self._active_drawing_tool.is_active():
                    self._active_drawing_tool.cancel()
                    self._view.clearCursorStroke()
                    self._updateScene(painted_object)
                    return True
            return False

        if event.type == Event.MouseReleaseEvent and self._controller.getToolsEnabled():
            mouse_evt = cast(MouseEvent, event)
            if MouseEvent.LeftButton not in mouse_evt.buttons:
                return False

            # For release-based tools (rectangle, circle), we need hit testing
            if isinstance(self._active_drawing_tool, (RectangleTool, CircleTool)):
                if not self._active_drawing_tool.is_active():
                    self._mouse_held = False
                    self._last_world_coords = None
                    return True

                # Get face/world data for release event
                if self._faces_selection_pass and self._picking_pass and self._mesh_transformed_cache:
                    face_id = self._faces_selection_pass.getFaceIdAtPosition(mouse_evt.x, mouse_evt.y)
                    if face_id >= 0 and face_id < self._mesh_transformed_cache.getFaceCount():
                        world_coords = self._picking_pass.getPickedPosition(mouse_evt.x, mouse_evt.y).getData()
                        viewport_pos = self._getViewportPos(world_coords)
                        modifiers = QApplication.keyboardModifiers()
                        result = self._active_drawing_tool.handle_release(world_coords, face_id, viewport_pos, modifiers)
                        brush_color = self._brush_color if self.getPaintType() != "extruder" else str(self._brush_extruder)
                        event_caught = self._processDrawingToolResult(result, face_id, brush_color, painted_object)
                        self._view.clearCursorStroke()
                        self._updateScene(painted_object, update_node=event_caught)
                        self._mouse_held = False
                        self._last_world_coords = None
                        return True

            self._mouse_held = False
            self._last_world_coords = None
            return True

        is_moved = event.type == Event.MouseMoveEvent
        is_pressed = event.type == Event.MousePressEvent
        if (is_moved or is_pressed) and self._controller.getToolsEnabled():
            mouse_evt = cast(MouseEvent, event)

            if not self._picking_pass:
                self._picking_pass = CuraApplication.getInstance().getRenderer().getRenderPass("picking_selected")
                if not self._picking_pass:
                    return False

            if is_pressed:
                if MouseEvent.LeftButton not in mouse_evt.buttons:
                    return False
                else:
                    self._mouse_held = True

            if not self._faces_selection_pass:
                self._faces_selection_pass = CuraApplication.getInstance().getRenderer().getRenderPass("selection_faces")
                if not self._faces_selection_pass:
                    return False

            if self._camera is None:
                self._updateCamera()
            if self._camera is None:
                return False

            if painted_object != self._node_cache:
                if self._node_cache is not None:
                    self._node_cache.transformationChanged.disconnect(self._nodeTransformChanged)
                self._node_cache = painted_object
                self._node_cache.transformationChanged.connect(self._nodeTransformChanged)
                self._cache_dirty = True
            if self._cache_dirty:
                self._cache_dirty = False
                self._mesh_transformed_cache = self._node_cache.getMeshDataTransformed()
            if not self._mesh_transformed_cache:
                return False

            face_id = self._faces_selection_pass.getFaceIdAtPosition(mouse_evt.x, mouse_evt.y)
            if face_id < 0 or face_id >= self._mesh_transformed_cache.getFaceCount():
                if self._view.clearCursorStroke():
                    self._updateScene(painted_object, update_node=self._mouse_held)
                    return True
                return False

            world_coords_vec = self._picking_pass.getPickedPosition(mouse_evt.x, mouse_evt.y)
            world_coords = world_coords_vec.getData()
            viewport_pos = self._getViewportPos(world_coords)
            modifiers = QApplication.keyboardModifiers()

            if self._last_world_coords is None:
                self._last_world_coords = world_coords

            # Update fill tool context if needed
            if isinstance(self._active_drawing_tool, FillTool):
                self._fill_tool.set_mesh_context(
                    self._node_cache.getMeshData(),
                    self._view._paint_texture,
                    self._view._current_bits_ranges,
                )

            brush_color = self._brush_color if self.getPaintType() != "extruder" else str(self._brush_extruder)
            event_caught = False

            try:
                if self._drawing_mode == DrawingMode.Mode.FREEHAND:
                    # Freehand: use original optimized path with cursor preview
                    uv_areas_cursor = self._getUvAreasForStroke(world_coords, world_coords, face_id)
                    if len(uv_areas_cursor) > 0:
                        cursor_path = self._createStrokePath(uv_areas_cursor)
                        self._view.setCursorStroke(cursor_path, brush_color)
                    else:
                        self._view.clearCursorStroke()

                    if is_pressed:
                        self._freehand_tool.handle_press(world_coords, face_id, viewport_pos, modifiers)
                    elif is_moved and self._mouse_held:
                        result = self._freehand_tool.handle_move(world_coords, face_id, viewport_pos, modifiers)
                        if result and hasattr(result, '_world_coords_start'):
                            uv_areas = self._getUvAreasForStroke(result._world_coords_start, result._world_coords_end, face_id)
                            if len(uv_areas) > 0:
                                # Apply symmetry
                                all_uv_areas = list(uv_areas)
                                if self._symmetry_x or self._symmetry_y or self._symmetry_z:
                                    for reflected_start in self._reflectWorldCoords(result._world_coords_start):
                                        try:
                                            reflected_end = reflected_start + (result._world_coords_end - result._world_coords_start)
                                            mirror_uv = self._getUvAreasForStroke(reflected_start, reflected_end, face_id)
                                            all_uv_areas.extend(mirror_uv)
                                        except:
                                            pass
                                event_caught = True
                                self._view.addStroke(all_uv_areas, brush_color, result.merge_with_previous)
                else:
                    # Non-freehand tools: route through drawing tool state machine
                    if is_pressed:
                        result = self._active_drawing_tool.handle_press(world_coords, face_id, viewport_pos, modifiers)
                        event_caught = self._processDrawingToolResult(result, face_id, brush_color, painted_object)
                        # For multi-click tools, catch the event to prevent camera rotation
                        if self._active_drawing_tool.is_active():
                            event_caught = True
                    elif is_moved:
                        result = self._active_drawing_tool.handle_move(world_coords, face_id, viewport_pos, modifiers)
                        self._processDrawingToolResult(result, face_id, brush_color, painted_object)
                        if self._active_drawing_tool.is_active():
                            event_caught = True
            except:
                Logger.logException("e", "Error when adding paint stroke")

            self._last_world_coords = world_coords
            self._updateScene(painted_object, update_node=event_caught)
            return event_caught

        return False

    def getRequiredExtraRenderingPasses(self) -> list[str]:
        return ["selection_faces", "picking_selected"]

    def _updateScene(self, node: SceneNode = None, update_node: bool = False):
        """
        Updates the current displayed scene
        :param node: the specific scene node to be updated, otherwise the current painted object will be used
        :param update_node: Indicates whether the specific node should be updated, which will invalidate its slicing
                            data, or the whole scene, which will just trigger a redraw of the view
        :return:
        """
        if node is None:
            node = self._view.getPaintedObject()
        if node is not None:
            if update_node:
                Application.getInstance().getController().getScene().sceneChanged.emit(node)
            else:
                scene = self.getController().getScene()
                scene.sceneChanged.emit(scene.getRoot())

    @staticmethod
    def _isModifierMesh(node: SceneNode) -> bool:
        """Returns True if the node is a modifier/special mesh type that should not be painted.

        Painting modifier meshes (e.g. support blockers) triggers UV-unwrapping and texture preparation
        that corrupts their mesh data and causes slicing to fail.
        """
        modifier_mesh_decorations = ("isAntiOverhangMesh", "isSupportMesh", "isCuttingMesh", "isInfillMesh")

        return any(node.callDecoration(d) for d in modifier_mesh_decorations)

    def _onSelectionChanged(self) -> None:
        super()._onSelectionChanged()

        single_selection = len(Selection.getAllSelectedObjects()) == 1
        selected_object = Selection.getSelectedObject(0) if single_selection else None
        if selected_object is not None and self._isModifierMesh(selected_object):
            selected_object = None

        self._view.setPaintedObject(selected_object)
        self._updateActiveView()
        self._updateState()

    def _onActiveToolChanged(self) -> None:
        self._updateActiveView()
        self._updateState()

    def _updateActiveView(self) -> None:
        if self._state == ExtendedPaintTool.Paint.State.NOT_SUPPORTED:
            return

        # Only manage the view when this tool is the active tool, to avoid
        # conflicts with the built-in PaintTool which uses the same pattern.
        if self._controller.getActiveTool() != self:
            return

        has_painted_object = self._view.hasPaintedObject()
        stage_is_prepare = self._controller.getActiveStage().stageId == "PrepareStage"
        self.setActiveView("ExtendedPaintTool" if has_painted_object and stage_is_prepare else None)

    def _updateState(self):
        if self._state == ExtendedPaintTool.Paint.State.NOT_SUPPORTED:
            return

        painted_object = self._view.getPaintedObject()
        if painted_object is not None and self._controller.getActiveTool() == self:
            if painted_object.callDecoration("getPaintTexture") is not None and painted_object.getMeshData().hasUVCoordinates():
                new_state = ExtendedPaintTool.Paint.State.READY
            else:
                new_state = ExtendedPaintTool.Paint.State.PREPARING_MODEL
                self._prepare_texture_job = PrepareTextureJob(painted_object)
                self._prepare_texture_job.finished.connect(self._onPrepareTextureFinished)
                self._prepare_texture_job.start()
        else:
            new_state = ExtendedPaintTool.Paint.State.MULTIPLE_SELECTION

        if new_state != self._state:
            self._state = new_state
            self.propertyChanged.emit()
            if new_state == ExtendedPaintTool.Paint.State.READY:
                self._updateActiveView()

    def _onPrepareTextureFinished(self, job: Job):
        if job == self._prepare_texture_job:
            self._prepare_texture_job = None
            self._state = ExtendedPaintTool.Paint.State.READY
            self.propertyChanged.emit()
            self._updateActiveView()
            self._updateScene()

    def _updateIgnoreUnselectedObjects(self):
        if self._controller.getActiveTool() != self:
            return
        active_view = self._controller.getActiveView()
        ignore_unselected_objects = active_view is not None and active_view is self._view
        CuraApplication.getInstance().getRenderer().getRenderPass("selection").setIgnoreUnselectedObjects(ignore_unselected_objects)
        CuraApplication.getInstance().getRenderer().getRenderPass("selection_faces").setIgnoreUnselectedObjects(ignore_unselected_objects)
