"""Modify LayerData vertices to reflect non-planar Z-offsets in the layer preview.

After CuraEngine slices and ProcessSlicedLayersJob builds the layer
visualization, this module modifies the vertex positions of the topmost
layers so the SimulationView renders curved toolpaths instead of flat
ones.  Modifications are performed in-place on LayerPolygon data arrays.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
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
# These should not be bent because they represent non-printing motion.
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
    """Modifies LayerData vertices to reflect non-planar Z-offsets in the layer preview.

    After CuraEngine slices and ProcessSlicedLayersJob builds the layer
    visualization, this class modifies the vertex positions of the topmost
    layers to show the curved toolpaths instead of flat ones.  The
    modifications happen in-place on the LayerPolygon data arrays.

    Coordinate system in ``LayerPolygon._data`` (shape ``(N, 3)``):

    * Column 0 -- X position in millimetres (same as world X).
    * Column 1 -- Y position, which represents **world Z** (height).
      For Point2D vertices ``ProcessSlicedLayersJob`` stores
      ``layer.height / 1000`` (converting from microns to mm).
      For Point3D vertices the engine already supplies the value in mm.
    * Column 2 -- Z position, which represents **-world Y**.

    We modify column 1 to shift vertices to the non-planar surface height.
    """

    def __init__(
        self,
        height_map: "HeightMap",
        safe_map: numpy.ndarray,
        blend_map: numpy.ndarray,
        layer_height: float,
        nonplanar_layer_count: int,
        total_layers: int,
    ) -> None:
        """Initialise the modifier.

        Parameters
        ----------
        height_map:
            A ``HeightMap`` object that can be sampled at (x, y) positions
            to obtain the target surface Z in mm.
        safe_map:
            2-D boolean array aligned with the height map grid.  ``True``
            where the position is safe for non-planar printing.
        blend_map:
            2-D float array (values in ``[0, 1]``) aligned with the height
            map grid.  Controls how strongly the non-planar offset is
            applied.  1.0 = fully bent, 0.0 = flat (original position).
        layer_height:
            Nominal layer height in mm.
        nonplanar_layer_count:
            Number of topmost layers to bend.
        total_layers:
            Total number of layers in the sliced model.
        """

        self._height_map = height_map
        self._safe_map = numpy.asarray(safe_map, dtype=bool)
        self._blend_map = numpy.asarray(blend_map, dtype=numpy.float64)
        self._layer_height = float(layer_height)
        self._nonplanar_layer_count = int(nonplanar_layer_count)
        self._total_layers = int(total_layers)

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def modify_layer_data(self, layer_data) -> bool:
        """Modify layer data vertices in-place for non-planar visualization.

        Parameters
        ----------
        layer_data:
            The ``LayerData`` object obtained via
            ``LayerDataDecorator.getLayerData()``.

        Returns
        -------
        bool
            ``True`` if any modifications were made, ``False`` otherwise.

        The method:

        1. Identifies which layers are in the non-planar range (topmost N
           layers).
        2. For each ``LayerPolygon`` in those layers, modifies
           ``polygon._data`` to apply Z offsets.
        3. Skips travel-move vertices (no extrusion types).
        4. Uses the height map, safe map, and blend map to compute the
           target Z for each vertex.
        """

        if layer_data is None:
            logger.warning("modify_layer_data called with None layer_data")
            return False

        layers = layer_data.getLayers()
        if not layers:
            logger.debug("No layers found in layer data")
            return False

        # Determine the range of layer numbers to modify.
        sorted_layer_numbers = sorted(layers.keys())
        if not sorted_layer_numbers:
            return False

        first_nonplanar = max(
            0, len(sorted_layer_numbers) - self._nonplanar_layer_count
        )
        target_layer_numbers = sorted_layer_numbers[first_nonplanar:]

        if not target_layer_numbers:
            logger.debug("No layers fall in the non-planar range")
            return False

        total_modified = 0
        total_skipped = 0

        # The topmost layer number is the last in the sorted list.
        top_layer_idx = len(sorted_layer_numbers) - 1

        for layer_number in target_layer_numbers:
            layer = layers.get(layer_number)
            if layer is None:
                continue

            # How many layers from the top surface is this layer?
            layer_position = sorted_layer_numbers.index(layer_number)
            layers_from_top = top_layer_idx - layer_position

            modified, skipped = self._modify_layer_polygons(
                layer, layers_from_top
            )
            total_modified += modified
            total_skipped += skipped

        if total_modified > 0:
            logger.info(
                "Non-planar layer modification complete: %d vertices modified, "
                "%d vertices skipped (travel/out-of-range)",
                total_modified,
                total_skipped,
            )
        else:
            logger.debug(
                "No vertices were modified (all outside non-planar region)"
            )

        return total_modified > 0

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    def _modify_layer_polygons(
        self, layer, layers_from_top: int
    ) -> tuple[int, int]:
        """Modify all polygons in a single layer.

        Returns
        -------
        tuple[int, int]
            (modified_count, skipped_count)
        """

        modified = 0
        skipped = 0

        for polygon in layer.polygons:
            m, s = self._modify_polygon(polygon, layers_from_top)
            modified += m
            skipped += s

        return modified, skipped

    def _modify_polygon(
        self, polygon, layers_from_top: int
    ) -> tuple[int, int]:
        """Modify vertices of a single LayerPolygon in-place.

        Parameters
        ----------
        polygon:
            A ``LayerPolygon`` instance whose ``_data`` array will be
            modified.
        layers_from_top:
            Number of layers between this layer and the topmost layer
            (0 = topmost).

        Returns
        -------
        tuple[int, int]
            (modified_count, skipped_count)
        """

        data = polygon._data  # (N, 3) float32
        types = polygon._types  # (N, 1) or (N,) uint8

        if data.shape[0] == 0:
            return 0, 0

        # Flatten types to 1-D if needed.
        flat_types = types.ravel() if types.ndim > 1 else types

        modified = 0
        skipped = 0

        # Build a mask of extrusion vertices (skip travel moves).
        n_points = data.shape[0]
        extrusion_mask = numpy.ones(n_points, dtype=bool)
        if _TRAVEL_TYPES:
            for travel_type in _TRAVEL_TYPES:
                extrusion_mask &= flat_types[:n_points] != travel_type

        # Extract world coordinates from layer-data coordinate system.
        # data[:, 0] = X (mm)
        # data[:, 1] = world_Z / 1000 for Point2D, or world_Z in engine
        #              units for Point3D.  Since ProcessSlicedLayersJob
        #              already divides by 1000 for Point2D and copies
        #              engine values for Point3D, the stored value is in
        #              mm in both cases (engine units are mm for coords).
        # data[:, 2] = -world_Y (mm)
        world_x = data[:, 0]          # mm
        world_y = -data[:, 2]         # mm (negate to recover world Y)

        for i in range(n_points):
            if not extrusion_mask[i]:
                skipped += 1
                continue

            bent_z = self._compute_bent_z_for_layer(
                layers_from_top, float(world_x[i]), float(world_y[i])
            )
            if bent_z is None:
                skipped += 1
                continue

            # Write back in layer-data coordinate system.
            # Column 1 stores height in mm (same scale as world Z in mm).
            data[i, 1] = numpy.float32(bent_z)
            modified += 1

        return modified, skipped

    def _compute_bent_z_for_layer(
        self,
        layers_from_top: int,
        world_x: float,
        world_y: float,
    ) -> Optional[float]:
        """Compute the bent Z coordinate for a given layer offset and XY position.

        The bent Z formula is::

            target_z = (surface_z - layers_from_top * layer_height) * blend

        where *surface_z* comes from the height map at the given XY and
        *blend* is the blend factor at that grid cell.

        Parameters
        ----------
        layers_from_top:
            How many layers below the top surface this point sits
            (0 = top layer).
        world_x:
            X position in mm (world coordinates).
        world_y:
            Y position in mm (world coordinates).

        Returns
        -------
        float or None
            The target Z in mm, or ``None`` if the position is outside
            the non-planar region or the height map does not cover it.
        """

        # Check if position is within the height map.
        if not self._height_map.is_valid(world_x, world_y):
            return None

        # Sample the height map using bilinear interpolation.
        import math
        surface_z = self._height_map.interpolate(world_x, world_y)
        if math.isnan(surface_z):
            return None

        # Map the world XY to grid indices for the safe/blend maps.
        row, col = self._height_map.get_grid_coords(world_x, world_y)

        # Check bounds against the safe map.
        if (
            row < 0
            or col < 0
            or row >= self._safe_map.shape[0]
            or col >= self._safe_map.shape[1]
        ):
            return None

        if not self._safe_map[row, col]:
            return None

        # Look up the blend factor.
        blend = float(self._blend_map[row, col])
        if blend <= 0.0:
            return None

        # Compute the target Z.
        # The surface Z is where the top layer should sit.  Each layer
        # below the top is offset downward by one layer height.
        flat_z = surface_z - layers_from_top * self._layer_height

        # Determine the original (planar) Z for this layer.
        # The topmost layer number is total_layers - 1.
        original_layer_number = self._total_layers - 1 - layers_from_top
        original_z = original_layer_number * self._layer_height

        # Blend between original flat Z and the non-planar target Z.
        bent_z = original_z + blend * (flat_z - original_z)

        return bent_z
