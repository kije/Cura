"""Modify LayerData vertices to reflect non-planar Z-offsets in the layer preview.

After CuraEngine slices and ProcessSlicedLayersJob builds the layer
visualization mesh, this module modifies the **built mesh vertices**
to bend the topmost layers to follow the model surface.

The key insight is that ProcessSlicedLayersJob calls ``LayerDataBuilder.build()``
which copies polygon ``_data`` into a flat vertex array.  Each ``LayerPolygon``
tracks its range in that array via ``_vertex_begin`` and ``_vertex_end``.
We modify both the polygon ``_data`` (for ``createMeshOrJumps``) and the
built mesh vertices (for the SimulationPass renderer).

Coordinate system in the built mesh vertices (shape ``(N, 3)``):

* Column 0 -- X position in mm (same as world X).
* Column 1 -- Height in mm (world Z / Y_scene).
* Column 2 -- Depth in mm (-world_Y / Z_scene negated).

The height map uses slicing coordinates (Z-up):
* X = world X = column 0
* Y = -column 2 (negate to get world Y from the stored -world_Y)

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, TYPE_CHECKING

import numpy

if TYPE_CHECKING:
    from ..analysis.height_map import HeightMap

try:
    from cura.LayerPolygon import LayerPolygon
except ImportError:
    LayerPolygon = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Line types that represent travel moves (no material extruded).
_TRAVEL_TYPES: frozenset[int] = frozenset()
if LayerPolygon is not None:
    _TRAVEL_TYPES = frozenset({
        LayerPolygon.NoneType,
        LayerPolygon.MoveUnretractedType,
        LayerPolygon.MoveRetractedType,
        LayerPolygon.MoveWhileRetractingType,
        LayerPolygon.MoveWhileUnretractingType,
        LayerPolygon.StationaryRetractUnretract,
    })


class LayerDataModifier:
    """Modifies built LayerData mesh vertices for non-planar preview.

    This must be called AFTER ``ProcessSlicedLayersJob`` has finished and
    built the mesh, because we modify the final vertex arrays directly.
    """

    def __init__(
        self,
        height_map: "HeightMap",
        safe_map: numpy.ndarray,
        blend_map: numpy.ndarray,
        layer_height: float,
        nonplanar_layer_count: int,
        total_layers: int,
        surface_mode: str = "all_surfaces",
    ) -> None:
        self._height_map = height_map
        self._safe_map = numpy.asarray(safe_map, dtype=bool)
        self._blend_map = numpy.asarray(blend_map, dtype=numpy.float64)
        self._layer_height = float(layer_height)
        self._nonplanar_layer_count = int(nonplanar_layer_count)
        self._total_layers = int(total_layers)
        self._surface_mode = surface_mode

    def modify_layer_data(self, layer_data) -> bool:
        """Modify layer data vertices in-place for non-planar visualization.

        Modifies both the polygon source ``_data`` arrays AND the built
        mesh vertex buffer so the SimulationView renders bent toolpaths.

        Parameters
        ----------
        layer_data:
            The ``LayerData`` object (a ``MeshData`` subclass) obtained
            via ``LayerDataDecorator.getLayerData()``.

        Returns True if any modifications were made.
        """
        if layer_data is None:
            return False

        layers = layer_data.getLayers()
        if not layers:
            return False

        sorted_layer_numbers = sorted(layers.keys())
        if not sorted_layer_numbers:
            return False

        all_surfaces_mode = self._surface_mode == "all_surfaces"
        max_bend_depth = self._nonplanar_layer_count * self._layer_height

        if all_surfaces_mode:
            # In all_surfaces mode, any layer could have vertices near
            # the surface.  Process all of them; _compute_bent_z filters
            # per-vertex based on proximity to the height map surface.
            target_layer_numbers = sorted_layer_numbers
        else:
            first_nonplanar = max(
                0, len(sorted_layer_numbers) - self._nonplanar_layer_count
            )
            target_layer_numbers = sorted_layer_numbers[first_nonplanar:]

        if not target_layer_numbers:
            return False

        # Get the built mesh vertices (the GPU buffer source).
        mesh_vertices = layer_data.getVertices()
        has_mesh_vertices = mesh_vertices is not None and len(mesh_vertices) > 0

        top_layer_idx = len(sorted_layer_numbers) - 1
        total_modified = 0

        for layer_number in target_layer_numbers:
            layer = layers.get(layer_number)
            if layer is None:
                continue

            layer_position = sorted_layer_numbers.index(layer_number)

            if all_surfaces_mode:
                # layers_from_top will be computed per-vertex in
                # _compute_bent_z based on surface proximity.
                layers_from_top = None
            else:
                layers_from_top = top_layer_idx - layer_position

            for polygon in layer.polygons:
                modified = self._modify_polygon(
                    polygon, layers_from_top,
                    mesh_vertices if has_mesh_vertices else None,
                    max_bend_depth=max_bend_depth,
                )
                total_modified += modified

        if total_modified > 0 and has_mesh_vertices:
            # Force the MeshData to recognize the vertex change.
            # MeshData caches vertex data; we need to invalidate it.
            try:
                # Directly set the modified vertices back.
                # LayerData inherits from MeshData which stores _vertices.
                layer_data._vertices = mesh_vertices
            except Exception:
                pass

            logger.info(
                "Non-planar preview: modified %d vertices in %d layers",
                total_modified, len(target_layer_numbers),
            )

        return total_modified > 0

    def _modify_polygon(
        self,
        polygon,
        layers_from_top: Optional[int],
        mesh_vertices: Optional[numpy.ndarray],
        max_bend_depth: float = 0.0,
    ) -> int:
        """Modify vertices of a single LayerPolygon.

        Modifies both ``polygon._data`` (for createMeshOrJumps) and
        the corresponding range in ``mesh_vertices`` (for SimulationPass).

        Returns the number of vertices modified.
        """
        data = polygon._data  # (N, 3) float32
        types = polygon._types

        if data.shape[0] == 0:
            return 0

        flat_types = types.ravel() if types.ndim > 1 else types
        n_points = data.shape[0]
        n_types = len(flat_types)

        # Build extrusion mask (skip travel moves).
        # Note: _types and _data can differ in length for some polygons
        # (e.g. first point has no type). Use the shorter length safely.
        extrusion_mask = numpy.ones(n_points, dtype=bool)
        if _TRAVEL_TYPES and n_types > 0:
            usable = min(n_points, n_types)
            for travel_type in _TRAVEL_TYPES:
                extrusion_mask[:usable] &= flat_types[:usable] != travel_type

        modified = 0

        for i in range(n_points):
            if not extrusion_mask[i]:
                continue

            # Layer data coordinates:
            # col 0 = X (mm), col 1 = height (mm), col 2 = scene_Z (depth)
            # For height map lookup we need analysis/slicing coordinates:
            # analysis X = scene_X = col 0
            # analysis Y = -scene_Z = -col 2
            slicing_x = float(data[i, 0])
            slicing_y = -float(data[i, 2])
            original_height = float(data[i, 1])

            bent_z = self._compute_bent_z(
                layers_from_top, slicing_x, slicing_y,
                original_height,
                max_bend_depth=max_bend_depth,
            )
            if bent_z is None:
                continue

            # Update polygon source data (used by createMeshOrJumps).
            data[i, 1] = numpy.float32(bent_z)
            modified += 1

        # Also update the built mesh vertices if available.
        if mesh_vertices is not None and modified > 0:
            self._update_mesh_vertices(polygon, mesh_vertices)

        return modified

    def _update_mesh_vertices(
        self,
        polygon,
        mesh_vertices: numpy.ndarray,
    ) -> None:
        """Copy modified polygon._data into the built mesh vertex range.

        LayerPolygon.build() stored vertices at
        ``mesh_vertices[_vertex_begin:_vertex_end]``.  We need to
        recompute the index list and copy the updated data.
        """
        try:
            vb = polygon._vertex_begin
            ve = polygon._vertex_end
            if vb >= ve or ve > len(mesh_vertices):
                return

            # Rebuild the index list (same logic as LayerPolygon.build).
            needed_points = polygon._build_cache_needed_points
            if needed_points is None:
                return

            types = polygon._types
            index_list = (
                numpy.arange(len(types)).reshape((-1, 1))
                + numpy.array([[0, 1]])
            ).reshape((-1, 1))[needed_points.reshape((-1, 1))]

            mesh_vertices[vb:ve, :] = polygon._data[index_list, :]

        except Exception:
            logger.debug("Failed to update mesh vertices for polygon", exc_info=True)

    def _compute_bent_z(
        self,
        layers_from_top: Optional[int],
        slicing_x: float,
        slicing_y: float,
        original_height: float,
        max_bend_depth: float = 0.0,
    ) -> Optional[float]:
        """Compute the bent Z (height) for a vertex.

        Parameters
        ----------
        layers_from_top:
            Fixed layers-from-top value (top_only mode), or None to
            auto-compute based on vertex height vs surface (all_surfaces).
        slicing_x, slicing_y:
            Position in analysis/slicing coordinates.
        original_height:
            Original vertex height in mm.
        max_bend_depth:
            Maximum depth below the surface to bend (for all_surfaces
            mode filtering).

        Returns the new height in mm, or None if outside non-planar region.
        """
        if not self._height_map.is_valid(slicing_x, slicing_y):
            return None

        surface_z = self._height_map.interpolate(slicing_x, slicing_y)
        if math.isnan(surface_z):
            return None

        row, col = self._height_map.get_grid_coords(slicing_x, slicing_y)

        if (row < 0 or col < 0
                or row >= self._safe_map.shape[0]
                or col >= self._safe_map.shape[1]):
            return None

        if not self._safe_map[row, col]:
            return None

        blend = float(self._blend_map[row, col])
        if blend <= 0.0:
            return None

        # Determine layers_from_top.
        if layers_from_top is None:
            # All-surfaces mode: compute per vertex.
            if self._layer_height <= 0.0:
                return None
            # Skip vertices too far above or below the surface.
            if original_height > surface_z + self._layer_height:
                return None
            if original_height < surface_z - max_bend_depth:
                return None
            layers_from_top = max(0, round(
                (surface_z - original_height) / self._layer_height
            ))

        # Target Z: surface minus layer offset.
        target_z = surface_z - layers_from_top * self._layer_height

        # Blend between original height and non-planar target.
        bent_z = original_height + blend * (target_z - original_height)

        return bent_z
