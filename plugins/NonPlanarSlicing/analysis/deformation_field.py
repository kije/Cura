"""Deformation field computation for non-planar slicing.

Computes a smooth 3D deformation field that propagates surface curvature
downward through the part.  Two solvers are available:

1. **QP solver** (default): Formulates the deformation as a sparse
   constrained quadratic program (CurviSlicer, Étienne et al., SIGGRAPH
   2019). Minimizes displacement smoothness (L2 gradient) subject to
   surface conformance, layer thickness bounds, slope limits, and floor
   safety. Requires the ``osqp`` package.

2. **Heuristic solver** (fallback): Propagates surface displacement
   downward with exponential decay and enforces constraints via
   one-pass clamping. Used when ``osqp`` is not installed.

Copyright (c) 2024 Cura Non-Planar Contributors
Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

try:
    from scipy import sparse
    from scipy.sparse.linalg import spsolve
    _HAS_SCIPY_SPARSE = True
except ImportError:
    _HAS_SCIPY_SPARSE = False

try:
    import osqp
    _HAS_OSQP = _HAS_SCIPY_SPARSE  # osqp also needs scipy.sparse
except ImportError:
    _HAS_OSQP = False

logger = logging.getLogger(__name__)

# Safety floor: never displace below this Z (mm).
_BED_FLOOR_Z = 0.05


@dataclass
class DeformationField:
    """3D deformation field mapping (x, y, z_nominal) → Z displacement.

    Attributes:
        x_min, x_max, y_min, y_max: Bounds of the XY grid in world units (mm).
        resolution: XY grid cell size in mm.
        z_levels: 1D array of nominal Z values per layer, shape (num_layers,).
        displacements: 3D array of Z displacements, shape (num_layers, rows, cols).
            displacements[layer_idx, row, col] is the Z offset to apply at
            that grid cell for that layer.
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    resolution: float
    z_levels: NDArray[np.floating]
    displacements: NDArray[np.floating]

    @property
    def num_layers(self) -> int:
        return self.z_levels.shape[0]

    @property
    def grid_shape(self) -> tuple[int, int]:
        """Return (rows, cols)."""
        return (self.displacements.shape[1], self.displacements.shape[2])

    def _get_grid_coords(self, x: float, y: float) -> tuple[int, int]:
        """Convert world (x, y) to grid (row, col), clamped to bounds."""
        rows, cols = self.grid_shape
        col = int(np.clip(
            np.round((x - self.x_min) / self.resolution), 0, cols - 1
        ))
        row = int(np.clip(
            np.round((y - self.y_min) / self.resolution), 0, rows - 1
        ))
        return row, col

    def _find_z_level_index(self, z: float) -> int:
        """Find the index of the closest z_level <= z. Returns 0 if below all."""
        idx = int(np.searchsorted(self.z_levels, z, side="right")) - 1
        return max(0, min(idx, self.num_layers - 1))

    def in_bounds(self, x: float, y: float) -> bool:
        """Check if (x, y) is within the grid bounding box."""
        half = self.resolution * 0.5
        return (self.x_min - half <= x <= self.x_max + half and
                self.y_min - half <= y <= self.y_max + half)

    def interpolate(self, x: float, y: float, z: float) -> float:
        """Trilinearly interpolate the Z displacement at world (x, y, z).

        Uses bilinear interpolation in XY and linear interpolation
        between adjacent Z levels.
        """
        if not self.in_bounds(x, y):
            return 0.0

        rows, cols = self.grid_shape

        # Continuous grid coordinates in XY.
        cx = (x - self.x_min) / self.resolution
        cy = (y - self.y_min) / self.resolution

        # Integer corners for bilinear XY.
        c0 = int(np.floor(cx))
        r0 = int(np.floor(cy))
        c1 = c0 + 1
        r1 = r0 + 1

        # Clamp to grid.
        c0 = max(0, min(c0, cols - 1))
        c1 = max(0, min(c1, cols - 1))
        r0 = max(0, min(r0, rows - 1))
        r1 = max(0, min(r1, rows - 1))

        fx = cx - int(np.floor(cx))
        fy = cy - int(np.floor(cy))
        fx = max(0.0, min(1.0, fx))
        fy = max(0.0, min(1.0, fy))

        # Z interpolation between layers.
        z_idx_low = self._find_z_level_index(z)
        z_idx_high = min(z_idx_low + 1, self.num_layers - 1)

        if z_idx_low == z_idx_high:
            fz = 0.0
        else:
            z_low = self.z_levels[z_idx_low]
            z_high = self.z_levels[z_idx_high]
            dz = z_high - z_low
            if dz > 1e-9:
                fz = max(0.0, min(1.0, (z - z_low) / dz))
            else:
                fz = 0.0

        # Bilinear interpolation for each Z level, then lerp between levels.
        def _bilinear(layer_idx: int) -> float:
            d = self.displacements[layer_idx]
            v00 = d[r0, c0]
            v01 = d[r0, c1]
            v10 = d[r1, c0]
            v11 = d[r1, c1]
            return float(
                v00 * (1 - fx) * (1 - fy)
                + v01 * fx * (1 - fy)
                + v10 * (1 - fx) * fy
                + v11 * fx * fy
            )

        d_low = _bilinear(z_idx_low)
        d_high = _bilinear(z_idx_high)
        return d_low * (1 - fz) + d_high * fz

    def get_target_z(self, x: float, y: float, orig_z: float) -> float:
        """Get the target Z after deformation at world (x, y, orig_z).

        Returns orig_z + interpolated displacement.
        """
        return orig_z + self.interpolate(x, y, orig_z)

    def get_local_thickness(self, x: float, y: float, z: float,
                            layer_height: float) -> float:
        """Compute local layer thickness at (x, y, z) for flow compensation.

        The thickness is the difference between the deformed Z at this
        layer and the deformed Z at the layer below.
        """
        z_above = self.get_target_z(x, y, z)
        z_below = self.get_target_z(x, y, z - layer_height)
        actual = z_above - z_below
        # Clamp to safe physical range.
        return max(0.05, min(3.0 * layer_height, actual))


def compute_deformation_field(
    height_map,
    safe_map: NDArray[np.bool_],
    *,
    layer_height: float,
    total_layers: int,
    first_layer_z: float = 0.0,
    decay_distance: float = 5.0,
    min_thickness_ratio: float = 0.5,
    max_thickness_ratio: float = 2.0,
    max_angle_deg: float = 45.0,
    optimization_resolution: float = 0.0,
) -> DeformationField:
    """Compute a deformation field from a height map.

    Parameters
    ----------
    height_map:
        Object with x_min, x_max, y_min, y_max, resolution, z_values,
        candidate_z_values attributes (HeightMap).
    safe_map:
        Boolean mask of safe (non-planar) cells, shape (rows, cols).
    layer_height:
        Nominal layer height in mm.
    total_layers:
        Total number of layers in the print.
    first_layer_z:
        Z of the first layer (usually ~layer_height).
    decay_distance:
        Exponential decay distance in mm. Controls how quickly the
        deformation diminishes from the surface downward.
    min_thickness_ratio:
        Minimum allowed layer thickness as fraction of nominal.
    max_thickness_ratio:
        Maximum allowed layer thickness as fraction of nominal.
    max_angle_deg:
        Maximum slope angle in degrees.
    optimization_resolution:
        If > 0, compute deformation on a coarser grid and upsample.
        Set to 0 to use the height map resolution directly.

    Returns
    -------
    DeformationField
    """
    if first_layer_z <= 0.0:
        first_layer_z = layer_height

    # Build nominal Z levels.
    z_levels = np.array([
        first_layer_z + i * layer_height for i in range(total_layers)
    ], dtype=np.float64)

    # Use height map grid parameters.
    x_min = height_map.x_min
    x_max = height_map.x_max
    y_min = height_map.y_min
    y_max = height_map.y_max

    # Determine working resolution: optionally coarser for performance.
    hm_resolution = height_map.resolution
    if optimization_resolution > hm_resolution:
        work_resolution = optimization_resolution
    else:
        work_resolution = hm_resolution

    work_cols = max(1, int(np.ceil((x_max - x_min) / work_resolution)) + 1)
    work_rows = max(1, int(np.ceil((y_max - y_min) / work_resolution)) + 1)

    # Resample height map Z values and safe map to working grid.
    surface_z = _resample_to_grid(
        height_map.z_values, height_map, work_rows, work_cols,
        x_min, y_min, work_resolution,
    )
    safe = _resample_safe_map(
        safe_map, height_map, work_rows, work_cols,
        x_min, y_min, work_resolution,
    )

    # Compute surface displacement: difference between surface Z and
    # the nearest nominal layer Z.
    surface_disp = np.zeros((work_rows, work_cols), dtype=np.float64)
    for r in range(work_rows):
        for c in range(work_cols):
            sz = surface_z[r, c]
            if not np.isfinite(sz) or not safe[r, c]:
                continue
            # Find nearest nominal layer Z.
            idx = int(np.searchsorted(z_levels, sz, side="right")) - 1
            idx = max(0, min(idx, total_layers - 1))
            nearest_z = z_levels[idx]
            # Also check the layer above.
            if idx + 1 < total_layers:
                if abs(z_levels[idx + 1] - sz) < abs(nearest_z - sz):
                    nearest_z = z_levels[idx + 1]
            surface_disp[r, c] = sz - nearest_z

    # Compute displacements using best available solver:
    # 1. QuickCurve (least-squares, fastest, needs scipy.sparse)
    # 2. CurviSlicer QP (OSQP, most precise, needs osqp + scipy.sparse)
    # 3. Heuristic (exponential decay, no dependencies)
    if _HAS_SCIPY_SPARSE:
        displacements = _solve_quickcurve(
            z_levels, surface_z, surface_disp, safe,
            work_rows, work_cols, work_resolution,
            layer_height, total_layers,
            min_thickness_ratio, max_thickness_ratio,
            max_angle_deg, decay_distance,
        )
    else:
        logger.warning(
            "scipy.sparse not installed — using heuristic exponential decay. "
            "Install scipy for QuickCurve-style deformation: pip install scipy"
        )
        displacements = _solve_heuristic(
            z_levels, surface_z, surface_disp, safe,
            work_rows, work_cols,
            layer_height, total_layers,
            decay_distance, work_resolution, max_angle_deg,
            min_thickness_ratio, max_thickness_ratio,
        )

    # If we used coarser resolution, upsample back to height map grid.
    if work_resolution > hm_resolution:
        hm_rows, hm_cols = height_map.z_values.shape
        full_disp = np.zeros((total_layers, hm_rows, hm_cols), dtype=np.float64)
        for layer_idx in range(total_layers):
            full_disp[layer_idx] = _upsample(
                displacements[layer_idx],
                hm_rows, hm_cols,
                x_min, y_min, work_resolution,
                x_min, y_min, hm_resolution,
            )
        displacements = full_disp
        final_resolution = hm_resolution
    else:
        final_resolution = work_resolution

    logger.info(
        "Deformation field computed: %d layers, grid %dx%d, "
        "decay=%.1fmm, max_disp=%.3fmm",
        total_layers,
        displacements.shape[1], displacements.shape[2],
        decay_distance,
        float(np.max(np.abs(displacements))),
    )

    return DeformationField(
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        resolution=final_resolution,
        z_levels=z_levels,
        displacements=displacements,
    )


def _solve_quickcurve(
    z_levels: NDArray,
    surface_z: NDArray,
    surface_disp: NDArray,
    safe: NDArray,
    rows: int,
    cols: int,
    resolution: float,
    layer_height: float,
    total_layers: int,
    min_thickness_ratio: float,
    max_thickness_ratio: float,
    max_angle_deg: float,
    decay_distance: float,
) -> NDArray:
    """Solve deformation field using the QuickCurve approach.

    Adapts the QuickCurve algorithm (2024, arXiv:2406.03966) which is
    10-45x faster than CurviSlicer's full 3D QP. The key insight:

    1. Solve a **2D least-squares** for the optimal surface displacement
       (Poisson-like problem, rows*cols variables instead of
       total_layers*rows*cols). This finds the smoothest surface that
       conforms to the mesh topology at safe cells.

    2. Enforce slope constraints via a **top-down sweep** (post-hoc
       propagation) rather than as in-solver inequality constraints.

    3. Propagate the optimized surface displacement downward through
       layers using exponential decay from the optimal surface.

    4. Enforce thickness bounds via a final clamping pass.
    """
    from scipy import sparse
    from scipy.sparse.linalg import spsolve

    M = rows * cols

    def idx2d(r: int, c: int) -> int:
        return r * cols + c

    # ------------------------------------------------------------------
    # Step 1: Solve 2D least-squares for optimal surface displacement
    #
    # Variables: s[r,c] — optimized surface displacement at each grid cell.
    #
    # Objective: minimize ||∇s||² (Laplacian smoothness)
    # Subject to: s[r,c] = surface_disp[r,c] at safe surface cells (equality)
    #             s[r,c] = 0 at unsafe cells (equality)
    #
    # This is a sparse linear system (Poisson equation with Dirichlet BCs).
    # ------------------------------------------------------------------

    # Classify cells: "fixed" (surface conformance or unsafe=0) vs "free"
    is_fixed = np.zeros((rows, cols), dtype=np.bool_)
    fixed_value = np.zeros((rows, cols), dtype=np.float64)

    for r in range(rows):
        for c in range(cols):
            if not safe[r, c]:
                is_fixed[r, c] = True
                fixed_value[r, c] = 0.0
            elif np.isfinite(surface_z[r, c]) and abs(surface_disp[r, c]) > 1e-6:
                is_fixed[r, c] = True
                fixed_value[r, c] = surface_disp[r, c]

    # Build least-squares system using QR-style assembly (A @ x ≈ b).
    # We construct as a normal-equation form: (A^T A) @ x = A^T b
    # using the Laplacian stencil augmented with gradient steepening.
    #
    # Row types (per QuickCurve, arXiv:2406.03966):
    # 1. Fixed cells: s[i] = fixed_value[i]   (weight = 1.0)
    # 2. Smoothness:  s[i] - s[j] = 0         for free neighbor pairs
    # 3. Gradient steepening: s[i] - s[j] = ±H_target  (encourages
    #    the surface to be as steep as possible in free regions,
    #    maximizing the non-planar curvature benefit)
    #
    # We use the Laplacian with steepening-adjusted RHS.

    # H_target: target height difference per grid cell (mm).
    # This is the key QuickCurve addition — free cells are encouraged
    # to slope at theta_target, not just be smooth.
    h_target = math.tan(math.radians(min(max_angle_deg * 0.7, 89.0))) * resolution

    L_rows = []
    L_cols = []
    L_data = []
    rhs = np.zeros(M, dtype=np.float64)

    for r in range(rows):
        for c in range(cols):
            i = idx2d(r, c)

            if is_fixed[r, c]:
                # Fixed cell: s[i] = fixed_value (Dirichlet BC)
                L_rows.append(i)
                L_cols.append(i)
                L_data.append(1.0)
                rhs[i] = fixed_value[r, c]
            else:
                # Free cell: Laplacian stencil with gradient steepening
                neighbor_count = 0
                steepening_rhs = 0.0
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols:
                        j = idx2d(nr, nc)
                        L_rows.append(i)
                        L_cols.append(j)
                        L_data.append(-1.0)
                        neighbor_count += 1

                        # Gradient steepening: if the neighbor has a
                        # known surface value, encourage this cell to
                        # slope toward it at the target angle.
                        if is_fixed[nr, nc] and safe[nr, nc]:
                            nval = fixed_value[nr, nc]
                            if abs(nval) > 1e-6:
                                # Encourage slope toward the surface
                                steepening_rhs += math.copysign(
                                    min(h_target, abs(nval)),
                                    nval,
                                )

                L_rows.append(i)
                L_cols.append(i)
                L_data.append(float(neighbor_count))
                rhs[i] = steepening_rhs

    L = sparse.csc_matrix((L_data, (L_rows, L_cols)), shape=(M, M))
    surface_opt = spsolve(L, rhs)

    logger.debug(
        "QuickCurve LS: solved %dx%d surface (max_disp=%.3fmm)",
        rows, cols, float(np.max(np.abs(surface_opt))),
    )

    # ------------------------------------------------------------------
    # Step 2: Enforce slope constraints via top-down sweep
    #
    # For each altitude descending, propagate: if a neighbor's displacement
    # would create a slope > max_slope, clamp it.
    # This is the QuickCurve post-hoc constraint enforcement.
    # ------------------------------------------------------------------
    max_slope = math.tan(math.radians(min(max_angle_deg, 89.9)))
    max_delta = max_slope * resolution
    surface_2d = surface_opt.reshape(rows, cols)

    # Multiple passes (converges quickly, typically 2-3 passes)
    for _pass in range(5):
        changed = False
        # Forward sweep (top-left to bottom-right)
        for r in range(rows):
            for c in range(cols):
                if is_fixed[r, c]:
                    continue
                for dr, dc in [(-1, 0), (0, -1)]:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols:
                        diff = surface_2d[r, c] - surface_2d[nr, nc]
                        if abs(diff) > max_delta:
                            surface_2d[r, c] = surface_2d[nr, nc] + math.copysign(max_delta, diff)
                            changed = True
        # Backward sweep (bottom-right to top-left)
        for r in range(rows - 1, -1, -1):
            for c in range(cols - 1, -1, -1):
                if is_fixed[r, c]:
                    continue
                for dr, dc in [(1, 0), (0, 1)]:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols:
                        diff = surface_2d[r, c] - surface_2d[nr, nc]
                        if abs(diff) > max_delta:
                            surface_2d[r, c] = surface_2d[nr, nc] + math.copysign(max_delta, diff)
                            changed = True
        if not changed:
            break

    logger.debug("QuickCurve: slope enforcement converged in %d passes", _pass + 1)

    # ------------------------------------------------------------------
    # Step 3: Propagate optimized surface downward through layers
    #
    # Use exponential decay from the optimized surface displacement.
    # This is much faster than the full 3D QP since the surface is
    # already optimally smooth.
    # ------------------------------------------------------------------
    displacements = np.zeros((total_layers, rows, cols), dtype=np.float64)

    for layer_idx in range(total_layers):
        z = z_levels[layer_idx]
        for r in range(rows):
            for c in range(cols):
                if not safe[r, c]:
                    continue
                sz = surface_z[r, c]
                if not np.isfinite(sz):
                    continue
                if z > sz + layer_height:
                    continue
                depth = max(0.0, sz - z)
                if decay_distance > 0:
                    decay = math.exp(-depth / decay_distance)
                else:
                    decay = 1.0 if depth < layer_height else 0.0
                displacements[layer_idx, r, c] = surface_2d[r, c] * decay

    # ------------------------------------------------------------------
    # Step 4: Enforce thickness and floor constraints
    # ------------------------------------------------------------------
    _enforce_thickness_constraint(
        displacements, z_levels, layer_height,
        min_thickness_ratio, max_thickness_ratio,
    )
    _enforce_floor_constraint(displacements, z_levels)

    # Zero outside safe region
    for layer_idx in range(total_layers):
        displacements[layer_idx][~safe] = 0.0

    logger.info(
        "QuickCurve solver: %dx%d surface → %d layers, max_disp=%.3fmm",
        rows, cols, total_layers,
        float(np.max(np.abs(displacements))),
    )

    return displacements


def _solve_qp(
    z_levels: NDArray,
    surface_z: NDArray,
    surface_disp: NDArray,
    safe: NDArray,
    rows: int,
    cols: int,
    resolution: float,
    layer_height: float,
    total_layers: int,
    min_thickness_ratio: float,
    max_thickness_ratio: float,
    max_angle_deg: float,
    decay_distance: float,
) -> NDArray:
    """Solve deformation field as a constrained quadratic program.

    Implements the CurviSlicer approach (Étienne et al., SIGGRAPH 2019):
    minimizes the L2 norm of displacement gradients (smoothness) subject
    to surface conformance, thickness bounds, slope limits, and floor
    safety constraints.

    Variables: h[k, r, c] — Z displacement at layer k, grid cell (r, c).
    Flattened into a 1D vector of size N = total_layers * rows * cols.

    Objective:
        min  Σ (h[k,r,c] - h[k,r,c-1])² + (h[k,r,c] - h[k,r-1,c])²
             + (h[k,r,c] - h[k-1,r,c])²
        (smoothness in XY and Z directions)

    Constraints:
        h[k,r,c] = surface_disp[r,c]   at surface layer (equality)
        h[k,r,c] = 0                    outside safe region
        min_gap ≤ (z[k]+h[k]) - (z[k-1]+h[k-1]) ≤ max_gap  (thickness)
        |h[k,r,c] - h[k,r,c±1]| ≤ tan(angle) * resolution  (slope)
        z[k] + h[k,r,c] ≥ floor_z                           (floor)
    """
    import osqp
    from scipy import sparse

    N = total_layers * rows * cols

    def idx(k: int, r: int, c: int) -> int:
        return k * rows * cols + r * cols + c

    # ------------------------------------------------------------------
    # Build objective: minimize ||∇h||² (smoothness)
    # P is a sparse matrix such that the objective is 0.5 * h' P h.
    # Each gradient term (h[i] - h[j])² contributes +1 to P[i,i], +1 to
    # P[j,j], and -1 to P[i,j] and P[j,i].
    # ------------------------------------------------------------------
    P_rows_list = []
    P_cols_list = []
    P_data_list = []

    def add_gradient_term(i: int, j: int, weight: float = 1.0) -> None:
        P_rows_list.extend([i, j, i, j])
        P_cols_list.extend([i, j, j, i])
        P_data_list.extend([weight, weight, -weight, -weight])

    # XY smoothness within each layer
    for k in range(total_layers):
        for r in range(rows):
            for c in range(cols):
                if c + 1 < cols:
                    add_gradient_term(idx(k, r, c), idx(k, r, c + 1))
                if r + 1 < rows:
                    add_gradient_term(idx(k, r, c), idx(k, r + 1, c))

    # Z smoothness between layers
    for k in range(total_layers - 1):
        for r in range(rows):
            for c in range(cols):
                add_gradient_term(idx(k, r, c), idx(k + 1, r, c), 0.5)

    P = sparse.csc_matrix(
        (P_data_list, (P_rows_list, P_cols_list)),
        shape=(N, N),
    )

    # Linear term: zero (pure quadratic smoothness)
    q = np.zeros(N, dtype=np.float64)

    # ------------------------------------------------------------------
    # Build constraints: A @ h in [l, u]
    # ------------------------------------------------------------------
    A_rows_list = []
    A_cols_list = []
    A_data_list = []
    l_list = []
    u_list = []
    constraint_idx = 0

    # 1. Surface conformance: h[surface_layer, r, c] = surface_disp[r, c]
    #    For each safe cell, find the topmost layer at or below surface_z.
    for r in range(rows):
        for c in range(cols):
            if not safe[r, c]:
                continue
            sz = surface_z[r, c]
            if not np.isfinite(sz):
                continue
            # Find the layer index closest to the surface
            k = int(np.searchsorted(z_levels, sz, side="right")) - 1
            k = max(0, min(k, total_layers - 1))

            i = idx(k, r, c)
            A_rows_list.append(constraint_idx)
            A_cols_list.append(i)
            A_data_list.append(1.0)
            l_list.append(surface_disp[r, c])
            u_list.append(surface_disp[r, c])
            constraint_idx += 1

    # 2. Fix unsafe cells to zero displacement
    for k in range(total_layers):
        for r in range(rows):
            for c in range(cols):
                if not safe[r, c]:
                    i = idx(k, r, c)
                    A_rows_list.append(constraint_idx)
                    A_cols_list.append(i)
                    A_data_list.append(1.0)
                    l_list.append(0.0)
                    u_list.append(0.0)
                    constraint_idx += 1

    # 3. Layer thickness constraints:
    #    min_gap <= (z[k] + h[k]) - (z[k-1] + h[k-1]) <= max_gap
    #    => min_gap - nominal_gap <= h[k] - h[k-1] <= max_gap - nominal_gap
    min_gap = min_thickness_ratio * layer_height
    max_gap = max_thickness_ratio * layer_height

    for k in range(1, total_layers):
        nominal_gap = z_levels[k] - z_levels[k - 1]
        for r in range(rows):
            for c in range(cols):
                i_above = idx(k, r, c)
                i_below = idx(k - 1, r, c)
                # h[k] - h[k-1]
                A_rows_list.extend([constraint_idx, constraint_idx])
                A_cols_list.extend([i_above, i_below])
                A_data_list.extend([1.0, -1.0])
                l_list.append(min_gap - nominal_gap)
                u_list.append(max_gap - nominal_gap)
                constraint_idx += 1

    # 4. Slope constraints (XY):
    #    |h[k,r,c] - h[k,r,c±1]| <= max_slope * resolution
    max_slope = math.tan(math.radians(min(max_angle_deg, 89.9)))
    max_delta = max_slope * resolution

    for k in range(total_layers):
        for r in range(rows):
            for c in range(cols):
                if c + 1 < cols:
                    i = idx(k, r, c)
                    j = idx(k, r, c + 1)
                    A_rows_list.extend([constraint_idx, constraint_idx])
                    A_cols_list.extend([i, j])
                    A_data_list.extend([1.0, -1.0])
                    l_list.append(-max_delta)
                    u_list.append(max_delta)
                    constraint_idx += 1
                if r + 1 < rows:
                    i = idx(k, r, c)
                    j = idx(k, r + 1, c)
                    A_rows_list.extend([constraint_idx, constraint_idx])
                    A_cols_list.extend([i, j])
                    A_data_list.extend([1.0, -1.0])
                    l_list.append(-max_delta)
                    u_list.append(max_delta)
                    constraint_idx += 1

    # 5. Floor constraint: z[k] + h[k,r,c] >= floor_z
    #    => h[k,r,c] >= floor_z - z[k]
    for k in range(total_layers):
        z = z_levels[k]
        for r in range(rows):
            for c in range(cols):
                i = idx(k, r, c)
                A_rows_list.append(constraint_idx)
                A_cols_list.append(i)
                A_data_list.append(1.0)
                l_list.append(_BED_FLOOR_Z - z)
                u_list.append(np.inf)
                constraint_idx += 1

    A = sparse.csc_matrix(
        (A_data_list, (A_rows_list, A_cols_list)),
        shape=(constraint_idx, N),
    )
    l_arr = np.array(l_list, dtype=np.float64)
    u_arr = np.array(u_list, dtype=np.float64)

    # ------------------------------------------------------------------
    # Solve with OSQP
    # ------------------------------------------------------------------
    solver = osqp.OSQP()
    solver.setup(
        P, q, A, l_arr, u_arr,
        verbose=False,
        eps_abs=1e-4,
        eps_rel=1e-4,
        max_iter=10000,
        warm_start=True,
        polish=True,
    )

    # Warm-start with heuristic solution for faster convergence
    h_init = _solve_heuristic_flat(
        z_levels, surface_z, surface_disp, safe,
        rows, cols,
        layer_height, total_layers,
        decay_distance, resolution, max_angle_deg,
        min_thickness_ratio, max_thickness_ratio,
    )
    solver.warm_start(x=h_init)

    result = solver.solve()

    if result.info.status not in ("solved", "solved_inaccurate"):
        logger.warning(
            "QP solver status: %s — falling back to heuristic",
            result.info.status,
        )
        return _solve_heuristic_flat(
            z_levels, surface_z, surface_disp, safe,
            rows, cols,
            layer_height, total_layers,
            decay_distance, resolution, max_angle_deg,
            min_thickness_ratio, max_thickness_ratio,
        ).reshape(total_layers, rows, cols)

    if result.info.status == "solved_inaccurate":
        logger.warning("QP solver converged with reduced accuracy")

    logger.info(
        "QP solver: %s in %d iterations, obj=%.4f",
        result.info.status, result.info.iter, result.info.obj_val,
    )

    return result.x.reshape(total_layers, rows, cols)


def _solve_heuristic_flat(
    z_levels, surface_z, surface_disp, safe,
    rows, cols,
    layer_height, total_layers,
    decay_distance, resolution, max_angle_deg,
    min_thickness_ratio, max_thickness_ratio,
) -> NDArray:
    """Run heuristic solver and return flat 1D array (for QP warm-start)."""
    displacements = _solve_heuristic(
        z_levels, surface_z, surface_disp, safe,
        rows, cols,
        layer_height, total_layers,
        decay_distance, resolution, max_angle_deg,
        min_thickness_ratio, max_thickness_ratio,
    )
    return displacements.ravel()


def _solve_heuristic(
    z_levels, surface_z, surface_disp, safe,
    rows, cols,
    layer_height, total_layers,
    decay_distance, resolution, max_angle_deg,
    min_thickness_ratio, max_thickness_ratio,
) -> NDArray:
    """Heuristic solver: exponential decay + constraint clamping.

    Fallback when OSQP is not available or QP solver fails.
    """
    displacements = np.zeros((total_layers, rows, cols), dtype=np.float64)

    # Propagate displacement downward with exponential decay.
    for layer_idx in range(total_layers):
        z = z_levels[layer_idx]
        for r in range(rows):
            for c in range(cols):
                sz = surface_z[r, c]
                if not np.isfinite(sz) or not safe[r, c]:
                    continue
                if z > sz + layer_height:
                    continue
                depth = max(0.0, sz - z)
                if decay_distance > 0:
                    decay = math.exp(-depth / decay_distance)
                else:
                    decay = 1.0 if depth < layer_height else 0.0
                displacements[layer_idx, r, c] = surface_disp[r, c] * decay

    # Enforce constraints.
    _enforce_slope_constraint(
        displacements, z_levels, resolution, max_angle_deg,
    )
    _enforce_thickness_constraint(
        displacements, z_levels, layer_height,
        min_thickness_ratio, max_thickness_ratio,
    )
    _enforce_floor_constraint(displacements, z_levels)

    # Zero out displacement outside safe region.
    for layer_idx in range(total_layers):
        displacements[layer_idx][~safe] = 0.0

    return displacements


def _resample_to_grid(
    z_values: NDArray, height_map, rows: int, cols: int,
    x_min: float, y_min: float, resolution: float,
) -> NDArray:
    """Resample height map z_values to a new grid via nearest neighbor."""
    result = np.full((rows, cols), np.nan, dtype=np.float64)
    for r in range(rows):
        y = y_min + r * resolution
        for c in range(cols):
            x = x_min + c * resolution
            if height_map.in_bounds(x, y):
                result[r, c] = height_map.interpolate(x, y)
    return result


def _resample_safe_map(
    safe_map: NDArray, height_map, rows: int, cols: int,
    x_min: float, y_min: float, resolution: float,
) -> NDArray:
    """Resample safe map to a new grid via nearest neighbor."""
    result = np.zeros((rows, cols), dtype=np.bool_)
    for r in range(rows):
        y = y_min + r * resolution
        for c in range(cols):
            x = x_min + c * resolution
            if height_map.in_bounds(x, y):
                src_r, src_c = height_map.get_grid_coords(x, y)
                if (0 <= src_r < safe_map.shape[0] and
                        0 <= src_c < safe_map.shape[1]):
                    result[r, c] = safe_map[src_r, src_c]
    return result


def _enforce_slope_constraint(
    displacements: NDArray, z_levels: NDArray,
    resolution: float, max_angle_deg: float,
) -> None:
    """Clamp displacement gradients to respect maximum slope angle.

    Iterates from bottom layer to top, clamping XY gradients of the
    displacement field so that ``|∂δ/∂x|, |∂δ/∂y| ≤ tan(max_angle)``.
    """
    max_slope = math.tan(math.radians(min(max_angle_deg, 89.9)))
    max_delta_per_cell = max_slope * resolution
    num_layers = displacements.shape[0]

    for layer_idx in range(num_layers):
        d = displacements[layer_idx]
        rows, cols = d.shape

        # Clamp X gradients (column direction).
        for r in range(rows):
            for c in range(1, cols):
                diff = d[r, c] - d[r, c - 1]
                if abs(diff) > max_delta_per_cell:
                    d[r, c] = d[r, c - 1] + math.copysign(max_delta_per_cell, diff)

        # Clamp Y gradients (row direction).
        for r in range(1, rows):
            for c in range(cols):
                diff = d[r, c] - d[r - 1, c]
                if abs(diff) > max_delta_per_cell:
                    d[r, c] = d[r - 1, c] + math.copysign(max_delta_per_cell, diff)


def _enforce_thickness_constraint(
    displacements: NDArray, z_levels: NDArray,
    layer_height: float,
    min_ratio: float, max_ratio: float,
) -> None:
    """Ensure deformed layer thickness stays within bounds.

    For each pair of adjacent layers, the deformed gap must be in
    [min_ratio * h, max_ratio * h] where h is the nominal layer height.
    """
    num_layers = displacements.shape[0]
    min_gap = min_ratio * layer_height
    max_gap = max_ratio * layer_height

    # Process from bottom to top, adjusting upper layers.
    for layer_idx in range(1, num_layers):
        d_above = displacements[layer_idx]
        d_below = displacements[layer_idx - 1]
        z_above = z_levels[layer_idx]
        z_below = z_levels[layer_idx - 1]
        nominal_gap = z_above - z_below  # should be ~layer_height

        rows, cols = d_above.shape
        for r in range(rows):
            for c in range(cols):
                deformed_above = z_above + d_above[r, c]
                deformed_below = z_below + d_below[r, c]
                actual_gap = deformed_above - deformed_below

                if actual_gap < min_gap:
                    # Push the upper layer up so gap = min_gap.
                    d_above[r, c] = deformed_below + min_gap - z_above
                elif actual_gap > max_gap:
                    # Pull the upper layer down so gap = max_gap.
                    d_above[r, c] = deformed_below + max_gap - z_above


def _enforce_floor_constraint(
    displacements: NDArray, z_levels: NDArray,
) -> None:
    """Ensure no deformed Z goes below the bed floor."""
    num_layers = displacements.shape[0]
    for layer_idx in range(num_layers):
        z = z_levels[layer_idx]
        d = displacements[layer_idx]
        # Where z + d < floor, clamp d.
        floor_violation = (z + d) < _BED_FLOOR_Z
        d[floor_violation] = _BED_FLOOR_Z - z


def _upsample(
    coarse: NDArray, target_rows: int, target_cols: int,
    coarse_x_min: float, coarse_y_min: float, coarse_res: float,
    fine_x_min: float, fine_y_min: float, fine_res: float,
) -> NDArray:
    """Upsample a coarse 2D grid to a finer grid via bilinear interpolation."""
    result = np.zeros((target_rows, target_cols), dtype=np.float64)
    coarse_rows, coarse_cols = coarse.shape

    for r in range(target_rows):
        y = fine_y_min + r * fine_res
        cy = (y - coarse_y_min) / coarse_res
        r0 = int(np.floor(cy))
        r1 = r0 + 1
        fy = cy - r0
        r0 = max(0, min(r0, coarse_rows - 1))
        r1 = max(0, min(r1, coarse_rows - 1))

        for c in range(target_cols):
            x = fine_x_min + c * fine_res
            cx = (x - coarse_x_min) / coarse_res
            c0 = int(np.floor(cx))
            c1 = c0 + 1
            fx = cx - c0
            c0 = max(0, min(c0, coarse_cols - 1))
            c1 = max(0, min(c1, coarse_cols - 1))

            v00 = coarse[r0, c0]
            v01 = coarse[r0, c1]
            v10 = coarse[r1, c0]
            v11 = coarse[r1, c1]
            result[r, c] = (
                v00 * (1 - fx) * (1 - fy)
                + v01 * fx * (1 - fy)
                + v10 * (1 - fx) * fy
                + v11 * fx * fy
            )

    return result
