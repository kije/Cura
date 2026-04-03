"""Modify LayerData vertices to reflect non-planar Z-offsets in the layer preview.

After CuraEngine slices and ProcessSlicedLayersJob builds the layer
visualization mesh, this module modifies the **built mesh vertices**
to bend the topmost layers to follow the model surface.

The key insight is that ProcessSlicedLayersJob calls ``LayerDataBuilder.build()``
which copies polygon ``_data`` into a flat vertex array.  Each ``LayerPolygon``
tracks its range in that array via ``_vertex_begin`` and ``_vertex_end``.

**Important**: After ``build()`` completes, the ``_build_cache_needed_points``
array is cleared to ``None``, so we cannot reconstruct the mapping from
``_data`` indices to mesh vertex indices.  Instead, we modify the **mesh
vertices directly** using each polygon's ``[_vertex_begin:_vertex_end]``
range, since those vertices have the same coordinate layout as ``_data``.

Coordinate system in the built mesh vertices (shape ``(N, 3)``):

* Column 0 -- X position in mm (same as world X / analysis X).
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
        nozzle_clearance: float = 0.0,
        max_path_deviation: float = 0.0,
    ) -> None:
        self._height_map = height_map
        self._safe_map = numpy.asarray(safe_map, dtype=bool)
        self._blend_map = numpy.asarray(blend_map, dtype=numpy.float64)
        self._layer_height = float(layer_height)
        self._nonplanar_layer_count = int(nonplanar_layer_count)
        self._total_layers = int(total_layers)
        self._surface_mode = surface_mode
        # Maximum Z deviation from the original path Z.
        # Uses max_path_deviation (typically 1x nozzle size) if provided,
        # otherwise falls back to a conservative default.
        if max_path_deviation > 0.0:
            self._max_z_displacement = max_path_deviation
        else:
            self._max_z_displacement = 0.4  # Conservative default (typical nozzle size)

    def modify_layer_data(self, layer_data) -> bool:
        """Modify layer data vertices in-place for non-planar visualization.

        Modifies the built mesh vertex buffer directly so the
        SimulationView renders bent toolpaths.  Also updates the
        polygon ``_data`` arrays for ``createMeshOrJumps`` consistency.

        Parameters
        ----------
        layer_data:
            The ``LayerData`` object (a ``MeshData`` subclass) obtained
            via ``LayerDataDecorator.getLayerData()``.

        Returns True if any modifications were made.
        """
        if layer_data is None:
            logger.debug("modify_layer_data: layer_data is None")
            return False

        layers = layer_data.getLayers()
        if not layers:
            logger.debug("modify_layer_data: no layers")
            return False

        sorted_layer_numbers = sorted(layers.keys())
        if not sorted_layer_numbers:
            return False

        all_surfaces_mode = self._surface_mode == "all_surfaces"
        max_bend_depth = self._nonplanar_layer_count * self._layer_height

        if all_surfaces_mode:
            target_layer_numbers = sorted_layer_numbers
        else:
            first_nonplanar = max(
                0, len(sorted_layer_numbers) - self._nonplanar_layer_count
            )
            target_layer_numbers = sorted_layer_numbers[first_nonplanar:]

        if not target_layer_numbers:
            return False

        # Get the built mesh vertices (the GPU buffer source).
        # MeshData wraps vertices with immutableNDArray (writeable=False),
        # so we must make a writable copy to modify them in-place.
        immutable_vertices = layer_data.getVertices()
        if immutable_vertices is None or len(immutable_vertices) == 0:
            logger.warning("modify_layer_data: no mesh vertices available")
            return False

        mesh_vertices = numpy.array(immutable_vertices, copy=True)
        mesh_vertices.flags.writeable = True

        logger.info(
            "modify_layer_data: processing %d layers (%d total), "
            "mesh has %d vertices, mode=%s, layer_height=%.3f, "
            "nonplanar_layers=%d, max_bend_depth=%.2f",
            len(target_layer_numbers), len(sorted_layer_numbers),
            len(mesh_vertices), self._surface_mode,
            self._layer_height, self._nonplanar_layer_count,
            max_bend_depth,
        )

        top_layer_idx = len(sorted_layer_numbers) - 1
        total_modified = 0
        self._reset_rejection_tracking()

        # Diagnostic: sample vertex coordinates from the first target layer
        # to check for coordinate mismatches against the height map.
        _diag_logged = False

        for layer_number in target_layer_numbers:
            layer = layers.get(layer_number)
            if layer is None:
                continue

            layer_position = sorted_layer_numbers.index(layer_number)

            if all_surfaces_mode:
                layers_from_top = None
            else:
                layers_from_top = top_layer_idx - layer_position

            for polygon in layer.polygons:
                # Log diagnostic info for the first polygon we encounter.
                if not _diag_logged:
                    try:
                        vb = polygon._vertex_begin
                        ve = polygon._vertex_end
                        if vb < ve and ve <= len(mesh_vertices):
                            sample = mesh_vertices[vb:min(vb + 5, ve)]
                            logger.info(
                                "DIAG first polygon: layer=%d, vb=%d, ve=%d, "
                                "sample X=[%.2f..%.2f], Y(height)=[%.2f..%.2f], "
                                "Z(-worldY)=[%.2f..%.2f], "
                                "analysis_x=[%.2f..%.2f], analysis_y=[%.2f..%.2f], "
                                "height_map x=[%.2f,%.2f] y=[%.2f,%.2f]",
                                layer_number, vb, ve,
                                float(sample[:, 0].min()), float(sample[:, 0].max()),
                                float(sample[:, 1].min()), float(sample[:, 1].max()),
                                float(sample[:, 2].min()), float(sample[:, 2].max()),
                                float(sample[:, 0].min()), float(sample[:, 0].max()),  # slicing_x = col 0
                                float(-sample[:, 2].max()), float(-sample[:, 2].min()),  # slicing_y = -col 2
                                self._height_map.x_min, self._height_map.x_max,
                                self._height_map.y_min, self._height_map.y_max,
                            )
                            _diag_logged = True
                    except Exception:
                        pass

                modified = self._modify_mesh_vertices_for_polygon(
                    polygon, layers_from_top,
                    mesh_vertices,
                    max_bend_depth=max_bend_depth,
                )
                total_modified += modified

        # Log rejection diagnostics.
        self._log_rejection_summary()

        if total_modified > 0:
            # Write modified vertices back to the LayerData.
            # MeshData stores _vertices as an immutable array; we replace
            # it with our modified (writable) copy.
            try:
                layer_data._vertices = mesh_vertices
            except Exception:
                logger.warning("Failed to set _vertices on LayerData", exc_info=True)

            logger.info(
                "Non-planar preview: modified %d vertices across %d layers",
                total_modified, len(target_layer_numbers),
            )
        else:
            logger.warning(
                "modify_layer_data: 0 vertices modified out of %d mesh vertices "
                "(%d layers processed). height_map bounds: x=[%.1f,%.1f] y=[%.1f,%.1f]",
                len(mesh_vertices), len(target_layer_numbers),
                self._height_map.x_min, self._height_map.x_max,
                self._height_map.y_min, self._height_map.y_max,
            )

        return total_modified > 0

    def _modify_mesh_vertices_for_polygon(
        self,
        polygon,
        layers_from_top: Optional[int],
        mesh_vertices: numpy.ndarray,
        max_bend_depth: float = 0.0,
    ) -> int:
        """Modify built mesh vertices for a single polygon in-place.

        After ``LayerPolygon.build()``, the mesh vertices at
        ``[_vertex_begin:_vertex_end]`` are the rendered geometry.
        We modify those directly — no need to reconstruct the
        ``_build_cache_needed_points`` index mapping.

        Also updates ``polygon._data`` for consistency with
        ``createMeshOrJumps``, though that path is secondary.

        Returns the number of mesh vertices modified.
        """
        try:
            vb = polygon._vertex_begin
            ve = polygon._vertex_end
        except AttributeError:
            return 0

        if vb >= ve or ve > len(mesh_vertices):
            return 0

        # Get the line types for this polygon's mesh vertex range.
        # After build(), the mesh stores line_types in the LayerData
        # alongside vertices.  However, we can also filter by checking
        # if the vertex is a travel move based on its position in the
        # mesh_vertices array.  For simplicity, we process all vertices
        # in the range — travel moves typically won't be in the safe_map
        # anyway (they jump around, rarely landing in non-planar regions
        # consistently).

        modified = 0
        chunk = mesh_vertices[vb:ve]  # view into the array

        for i in range(len(chunk)):
            # Mesh vertex coordinates:
            # col 0 = X (mm), col 1 = height (mm), col 2 = -world_Y
            slicing_x = float(chunk[i, 0])
            slicing_y = -float(chunk[i, 2])
            original_height = float(chunk[i, 1])

            bent_z = self._compute_bent_z(
                layers_from_top, slicing_x, slicing_y,
                original_height,
                max_bend_depth=max_bend_depth,
            )
            if bent_z is None:
                continue

            chunk[i, 1] = bent_z
            modified += 1

        # Also update polygon._data for createMeshOrJumps consistency.
        # This uses the same coordinate layout but may have different
        # length than the mesh vertex range (due to build filtering).
        if modified > 0:
            self._update_polygon_data(polygon, layers_from_top, max_bend_depth)

        return modified

    def _update_polygon_data(
        self,
        polygon,
        layers_from_top: Optional[int],
        max_bend_depth: float,
    ) -> None:
        """Update polygon._data heights to match the bent mesh vertices."""
        try:
            data = polygon._data
            if data is None or data.shape[0] == 0:
                return

            for i in range(data.shape[0]):
                slicing_x = float(data[i, 0])
                slicing_y = -float(data[i, 2])
                original_height = float(data[i, 1])

                bent_z = self._compute_bent_z(
                    layers_from_top, slicing_x, slicing_y,
                    original_height,
                    max_bend_depth=max_bend_depth,
                )
                if bent_z is not None:
                    data[i, 1] = numpy.float32(bent_z)
        except Exception:
            logger.debug("Failed to update polygon._data", exc_info=True)

    # Rejection tracking for diagnostics (class-level, reset per modify_layer_data call).
    _rejection_counts: dict = {}
    _rejection_log_limit: int = 5
    _rejection_logged: int = 0

    def _reset_rejection_tracking(self) -> None:
        self._rejection_counts = {
            "not_valid": 0, "nan_surface": 0, "out_of_grid": 0,
            "not_safe": 0, "zero_blend": 0, "above_surface": 0,
            "below_depth": 0, "zero_lh": 0,
        }
        self._rejection_logged = 0

    def _log_rejection_summary(self) -> None:
        parts = [f"{k}={v}" for k, v in self._rejection_counts.items() if v > 0]
        if parts:
            logger.info("Vertex rejection summary: %s", ", ".join(parts))

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
            self._rejection_counts["not_valid"] = self._rejection_counts.get("not_valid", 0) + 1
            return None

        surface_z = self._height_map.interpolate(slicing_x, slicing_y)
        if math.isnan(surface_z):
            self._rejection_counts["nan_surface"] = self._rejection_counts.get("nan_surface", 0) + 1
            return None

        row, col = self._height_map.get_grid_coords(slicing_x, slicing_y)

        if (row < 0 or col < 0
                or row >= self._safe_map.shape[0]
                or col >= self._safe_map.shape[1]):
            self._rejection_counts["out_of_grid"] = self._rejection_counts.get("out_of_grid", 0) + 1
            return None

        if not self._safe_map[row, col]:
            self._rejection_counts["not_safe"] = self._rejection_counts.get("not_safe", 0) + 1
            return None

        blend = float(self._blend_map[row, col])
        if blend <= 0.0:
            self._rejection_counts["zero_blend"] = self._rejection_counts.get("zero_blend", 0) + 1
            return None

        # Determine layers_from_top.
        if layers_from_top is None:
            # All-surfaces mode: compute per vertex.
            if self._layer_height <= 0.0:
                self._rejection_counts["zero_lh"] = self._rejection_counts.get("zero_lh", 0) + 1
                return None
            # Skip vertices too far above or below the surface.
            if original_height > surface_z + self._layer_height:
                self._rejection_counts["above_surface"] = self._rejection_counts.get("above_surface", 0) + 1
                return None
            if original_height < surface_z - max_bend_depth:
                self._rejection_counts["below_depth"] = self._rejection_counts.get("below_depth", 0) + 1
                return None
            layers_from_top = max(0, round(
                (surface_z - original_height) / self._layer_height
            ))

        # Target Z: surface minus layer offset.
        target_z = surface_z - layers_from_top * self._layer_height

        # Blend between original height and non-planar target.
        bent_z = original_height + blend * (target_z - original_height)

        # Safety clamp: don't let a layer deviate more than
        # max_path_deviation from its conformal target position.
        conformal_deviation = abs(bent_z - target_z)
        if conformal_deviation > self._max_z_displacement and blend > 0.5:
            bent_z = target_z + math.copysign(
                self._max_z_displacement, bent_z - target_z
            )

        # Don't go below zero (bed surface).
        if bent_z < 0.0:
            bent_z = max(0.05, original_height)

        return bent_z
