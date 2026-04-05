"""Tests for LayerDataModifier._compute_bent_z()."""

import numpy
import pytest

from visualization.layer_data_modifier import LayerDataModifier


class _MockHeightMap:
    """Minimal mock for HeightMap."""

    def __init__(
        self,
        z_value=10.0,
        grid_shape=(20, 20),
        x_min=0,
        x_max=10,
        y_min=0,
        y_max=10,
        resolution=0.5,
    ):
        self._z = z_value
        self._shape = grid_shape
        self.x_min = x_min
        self.x_max = x_max
        self.y_min = y_min
        self.y_max = y_max
        self.resolution = resolution

    def interpolate(self, x, y):
        return self._z

    def is_valid(self, x, y):
        return True

    def get_grid_coords(self, x, y):
        r = min(max(0, int(y / self.resolution)), self._shape[0] - 1)
        c = min(max(0, int(x / self.resolution)), self._shape[1] - 1)
        return (r, c)


def _make_modifier(
    z_value=10.0,
    grid_shape=(20, 20),
    safe_map=None,
    blend_map=None,
    layer_height=0.2,
    nonplanar_layer_count=5,
    total_layers=50,
    surface_mode="all_surfaces",
    nozzle_clearance=0.0,
    max_path_deviation=0.4,
    height_map=None,
):
    """Create a LayerDataModifier with sensible defaults for testing."""
    if height_map is None:
        height_map = _MockHeightMap(z_value=z_value, grid_shape=grid_shape)

    if safe_map is None:
        safe_map = numpy.ones(grid_shape, dtype=bool)
    if blend_map is None:
        blend_map = numpy.ones(grid_shape, dtype=numpy.float64)

    modifier = LayerDataModifier(
        height_map=height_map,
        safe_map=safe_map,
        blend_map=blend_map,
        layer_height=layer_height,
        nonplanar_layer_count=nonplanar_layer_count,
        total_layers=total_layers,
        surface_mode=surface_mode,
        nozzle_clearance=nozzle_clearance,
        max_path_deviation=max_path_deviation,
    )
    modifier._reset_rejection_tracking()
    return modifier


class TestComputeBentZ:
    """Tests for the _compute_bent_z helper method."""

    def test_applies_z_clamping(self):
        """Verify the max_path_deviation clamp caps bent_z correctly.

        With a uniform height map and blend in [0,1], bent_z = target_z
        and max_bent_z = target_z + deviation, so the clamp is a no-op.
        To actually trigger the clamp we need bent_z > max_bent_z, which
        requires a spatially-varying surface.  We use a mock whose
        interpolate() returns a higher surface than the grid suggests,
        simulating a steep slope where the per-vertex surface exceeds
        the per-layer reference.

        Formula under test:
            layer_below_z = surface_z - (layers_from_top + 1) * layer_height
            max_bent_z    = layer_below_z + layer_height + max_z_displacement
        """
        # Scenario that triggers the clamp:
        # Use a height map that returns surface=12.0 for interpolation but
        # the vertex is at original_height=9.0 with layers_from_top=2.
        #
        # target_z  = 12 - 2*0.2 = 11.6
        # bent_z    = 9.0 + 1.0*(11.6 - 9.0) = 11.6
        # layer_below_z = 12 - 3*0.2 = 11.4
        # max_actual_lh = 0.2 + 0.1 = 0.3   (max_path_deviation=0.1)
        # max_bent_z    = 11.4 + 0.3 = 11.7
        # 11.6 <= 11.7 => no clamp (just barely).
        #
        # With max_path_deviation=0.0 (triggers default 0.4), same story.
        # The math with a single surface_z variable makes clamp impossible
        # for blend=1 because bent_z = target = surface - lft*lh and
        # max_bent_z = target + deviation.
        #
        # To truly trigger: override _max_z_displacement to a very small
        # negative-like value after construction. But that is internal.
        # Instead, verify the formula is correct for a normal case and
        # confirm the output matches when the clamp IS the binding constraint.

        # First verify the standard (unclamped) formula:
        # surface=10, lft=2, lh=0.2, max_dev=0.4, blend=1, original=9.0
        # target_z = 10 - 0.4 = 9.6
        # bent_z = 9.0 + 1*(9.6 - 9.0) = 9.6
        # max_bent_z = (10 - 0.6) + 0.6 = 10.0   (no clamp)
        mod = _make_modifier(z_value=10.0, layer_height=0.2, max_path_deviation=0.4)
        result = mod._compute_bent_z(
            layers_from_top=2, slicing_x=1.0, slicing_y=1.0,
            original_height=9.0, max_bend_depth=1.0,
        )
        assert result == pytest.approx(9.6)

        # Now force the clamp by patching _max_z_displacement to a tiny value
        # so max_bent_z < bent_z.
        # With _max_z_displacement = 0.0:
        #   max_bent_z = layer_below_z + lh + 0 = 9.4 + 0.2 = 9.6
        #   bent_z = 9.6, so 9.6 <= 9.6 => exactly at boundary (no clamp).
        # With _max_z_displacement = -0.1 (hypothetical):
        #   max_bent_z = 9.4 + 0.1 = 9.5
        #   bent_z = 9.6 > 9.5 => clamped to 9.5.
        # We'll set it to 0.0 for the boundary case, then to a smaller value.
        mod._max_z_displacement = 0.0
        result_boundary = mod._compute_bent_z(
            layers_from_top=2, slicing_x=1.0, slicing_y=1.0,
            original_height=9.0, max_bend_depth=1.0,
        )
        # max_bent_z = 9.4 + 0.2 + 0.0 = 9.6; bent_z = 9.6 => exactly at limit
        assert result_boundary == pytest.approx(9.6)

        # Make the displacement negative (impossible in normal init, but tests
        # the clamp branch directly).
        mod._max_z_displacement = -0.15
        result_clamped = mod._compute_bent_z(
            layers_from_top=2, slicing_x=1.0, slicing_y=1.0,
            original_height=9.0, max_bend_depth=1.0,
        )
        # max_bent_z = 9.4 + 0.2 + (-0.15) = 9.45
        # bent_z would be 9.6 but gets clamped to 9.45
        assert result_clamped == pytest.approx(9.45)
        assert result_clamped < result  # strictly less than unclamped

    def test_returns_none_outside_safe_region(self):
        """Point outside safe_map returns None."""
        grid_shape = (20, 20)
        # All cells marked as unsafe
        safe_map = numpy.zeros(grid_shape, dtype=bool)
        blend_map = numpy.ones(grid_shape, dtype=numpy.float64)

        mod = _make_modifier(
            z_value=10.0,
            grid_shape=grid_shape,
            safe_map=safe_map,
            blend_map=blend_map,
        )

        result = mod._compute_bent_z(
            layers_from_top=0, slicing_x=1.0, slicing_y=1.0,
            original_height=9.8, max_bend_depth=1.0,
        )
        assert result is None
        assert mod._rejection_counts["not_safe"] > 0

    def test_respects_blend_factor(self):
        """blend=0 returns None (zero_blend rejection); blend=0.5 and 1.0
        interpolate correctly between original_height and target_z."""
        grid_shape = (20, 20)

        # -- blend = 0.0: rejected with zero_blend --
        blend_zero = numpy.zeros(grid_shape, dtype=numpy.float64)
        mod_zero = _make_modifier(z_value=10.0, grid_shape=grid_shape, blend_map=blend_zero)
        result_zero = mod_zero._compute_bent_z(
            layers_from_top=2, slicing_x=1.0, slicing_y=1.0,
            original_height=9.0, max_bend_depth=1.0,
        )
        assert result_zero is None
        assert mod_zero._rejection_counts["zero_blend"] > 0

        # -- blend = 1.0: full bend toward target --
        # target_z = 10 - 2*0.2 = 9.6
        # bent_z = 9.0 + 1.0*(9.6 - 9.0) = 9.6
        blend_one = numpy.ones(grid_shape, dtype=numpy.float64)
        mod_one = _make_modifier(
            z_value=10.0, grid_shape=grid_shape, blend_map=blend_one,
            layer_height=0.2, max_path_deviation=0.4,
        )
        result_one = mod_one._compute_bent_z(
            layers_from_top=2, slicing_x=1.0, slicing_y=1.0,
            original_height=9.0, max_bend_depth=1.0,
        )
        assert result_one == pytest.approx(9.6)

        # -- blend = 0.5: halfway between original and target --
        # bent_z = 9.0 + 0.5*(9.6 - 9.0) = 9.3
        blend_half = numpy.full(grid_shape, 0.5, dtype=numpy.float64)
        mod_half = _make_modifier(
            z_value=10.0, grid_shape=grid_shape, blend_map=blend_half,
            layer_height=0.2, max_path_deviation=0.4,
        )
        result_half = mod_half._compute_bent_z(
            layers_from_top=2, slicing_x=1.0, slicing_y=1.0,
            original_height=9.0, max_bend_depth=1.0,
        )
        assert result_half == pytest.approx(9.3)

    def test_clamps_below_bed(self):
        """Bent Z < 0 should be clamped to max(0.05, original_height)."""
        grid_shape = (20, 20)

        # Surface at Z=0.1, layer_height=0.2, layers_from_top=3
        # target_z = 0.1 - 3*0.2 = -0.5
        # bent_z(original=0.1) = 0.1 + 1*(-0.5 - 0.1) = -0.5 => < 0
        # clamped to max(0.05, 0.1) = 0.1
        mod = _make_modifier(
            z_value=0.1, grid_shape=grid_shape,
            layer_height=0.2, max_path_deviation=5.0,
        )

        result = mod._compute_bent_z(
            layers_from_top=3, slicing_x=1.0, slicing_y=1.0,
            original_height=0.1, max_bend_depth=2.0,
        )
        assert result == pytest.approx(0.1)

        # With original_height = 0.02 (below the 0.05 floor):
        # clamped to max(0.05, 0.02) = 0.05
        result2 = mod._compute_bent_z(
            layers_from_top=3, slicing_x=1.0, slicing_y=1.0,
            original_height=0.02, max_bend_depth=2.0,
        )
        assert result2 == pytest.approx(0.05)

    def test_none_safe_map_returns_none(self):
        """safe_map=None or blend_map=None should return None without crash."""
        height_map = _MockHeightMap(z_value=10.0)

        # safe_map=None
        mod = LayerDataModifier(
            height_map=height_map, safe_map=None,
            blend_map=numpy.ones((20, 20), dtype=numpy.float64),
            layer_height=0.2, nonplanar_layer_count=5, total_layers=50,
            surface_mode="all_surfaces", nozzle_clearance=0.0,
            max_path_deviation=0.4,
        )
        mod._reset_rejection_tracking()
        result = mod._compute_bent_z(
            layers_from_top=0, slicing_x=1.0, slicing_y=1.0,
            original_height=9.8, max_bend_depth=1.0,
        )
        assert result is None

        # blend_map=None
        mod2 = LayerDataModifier(
            height_map=height_map,
            safe_map=numpy.ones((20, 20), dtype=bool),
            blend_map=None,
            layer_height=0.2, nonplanar_layer_count=5, total_layers=50,
            surface_mode="all_surfaces", nozzle_clearance=0.0,
            max_path_deviation=0.4,
        )
        mod2._reset_rejection_tracking()
        result2 = mod2._compute_bent_z(
            layers_from_top=0, slicing_x=1.0, slicing_y=1.0,
            original_height=9.8, max_bend_depth=1.0,
        )
        assert result2 is None
