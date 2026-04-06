# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Fixed-point iteration loop coupling FEA and density homogenization.

The algorithm is:

1. Initialise all element densities to ``config["min_density"]``.
2. Repeat until convergence or max iterations:
   a. Homogenize: compute average effective E from current density field.
   b. Solve the FEA system via EasyFEA (primary) or custom scipy solver
      (fallback).
   c. Compute von Mises stress per element.
   d. Map stress → new density field.
   e. Apply damping: ``ρ_new = 0.5 × ρ_old + 0.5 × ρ_candidate``.
   f. Check convergence: ``max|ρ_new - ρ_old| < tol``.
3. Return final density field, stress field, and diagnostic info dict.

Boundary conditions (fixed faces and force groups) are mapped from surface
triangle face indices to tet-mesh node indices via
``TetMesh.surface_node_map`` (scipy fallback) or via position-based
``mesh.Nodes_Conditions`` queries (EasyFEA path).
"""

import os
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from UM.Logger import Logger

try:
    import trimesh as _trimesh
except ImportError:
    _trimesh = None  # type: ignore[assignment]

try:
    from EasyFEA import Mesher, Simulations
    from EasyFEA.Models.Elastic import TransverselyIsotropic, Isotropic
    _EASYFEA_AVAILABLE = True
except Exception:
    _EASYFEA_AVAILABLE = False

from .fea_solver import LinearElasticitySolver
from .homogenization import effective_properties
from .material_database import Material
from .stress_to_density import stress_to_density
from .tetrahedralization import TetMesh

_CONVERGENCE_TOL = 1e-3
_DAMPING = 0.5

_PATTERN_EXPONENTS: Dict[str, float] = {
    "lines": 1.0, "grid": 2.0, "triangles": 1.3,
    "gyroid": 1.6, "cubic": 2.0, "honeycomb": 2.3,
}


class IterativeFEASolver:
    """Orchestrate the fixed-point FEA ↔ density iteration.

    Uses EasyFEA as the primary solver when available, falling back to the
    custom scipy-based ``LinearElasticitySolver`` otherwise.

    Example::

        solver = IterativeFEASolver()
        density, stress, info = solver.solve(
            tet_mesh, bc_decorator, material, config,
            progress_callback=lambda p: print(f"{p:.0%}")
        )
    """

    def solve(
        self,
        tet_mesh: TetMesh,
        boundary_conditions: Any,
        material: Material,
        config: Dict[str, Any],
        progress_callback: Optional[Callable[[float], None]] = None,
        surface_mesh: Any = None,
    ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
        """Run the fixed-point density iteration.

        Args:
            tet_mesh: Tetrahedral mesh (nodes N×3, elements M×4,
                surface_node_map, msh_path).
            boundary_conditions: A ``FEABoundaryConditionDecorator`` instance
                providing ``getFixedFaces()`` and ``getForceGroups()``.
            material: :class:`~fea.material_database.Material` with E_xy,
                nu, and yield_strength properties.
            config: Dictionary with keys:

                - ``min_density`` (float): minimum infill fraction, e.g. 0.10.
                - ``max_density`` (float): maximum infill fraction, e.g. 0.80.
                - ``max_iterations`` (int): iteration cap.
                - ``infill_pattern`` (str): pattern name for homogenization.
                - ``mesh_resolution`` (str | float): passed to tetrahedralize
                  (unused here; mesh is pre-built).

            progress_callback: Optional callable receiving a float in [0, 1]
                after each iteration.
            surface_mesh: The original ``trimesh.Trimesh`` surface mesh.  When
                provided, face indices returned by the BC decorator are expanded
                to vertex indices via ``surface_mesh.faces[face_idx]`` before
                lookup in ``tet_mesh.surface_node_map``.  If ``None``, the
                face indices are treated as vertex indices directly (legacy
                behaviour).

        Returns:
            Tuple ``(density_field, stress_field, info_dict)`` where:

            - ``density_field``: ndarray (M,) with element densities in
              ``[min_density, max_density]``.
            - ``stress_field``: ndarray (M,) with von Mises stress (MPa).
            - ``info_dict``: dict with keys ``iterations`` (int),
              ``converged`` (bool), ``max_change`` (float).
        """
        import warnings

        # Warn early if the material is hyperelastic / near-incompressible —
        # linear elastic FEA is not valid in that regime.
        if material.E_xy < 100.0 or material.nu > 0.45:
            warnings.warn(
                "Material properties suggest an elastomer "
                f"(E_xy={material.E_xy} MPa, nu={material.nu}). "
                "Linear elastic FEA is not valid for hyperelastic materials like TPU. "
                "Results should not be used for structural assessment.",
                UserWarning,
                stacklevel=2,
            )

        # Warn if material failure mode is not suited for von Mises criterion
        if hasattr(material, "failure_mode") and material.failure_mode == "brittle":
            warnings.warn(
                f"Material '{material.name}' has failure_mode='brittle'. "
                "Von Mises is a ductile failure criterion and may overestimate "
                "strength by 15-30% for brittle polymers like PLA. "
                "Consider using a lower safety factor.",
                UserWarning,
                stacklevel=2,
            )

        use_easyfea = _EASYFEA_AVAILABLE and bool(tet_mesh.msh_path) and os.path.exists(tet_mesh.msh_path)

        if use_easyfea:
            Logger.log("i", "FEA solve: using EasyFEA solver (msh=%s)", tet_mesh.msh_path)
            return self._solve_easyfea(
                tet_mesh, boundary_conditions, material, config,
                progress_callback, surface_mesh,
            )
        else:
            Logger.log("i", "FEA solve: using scipy fallback solver (EasyFEA available=%s)",
                       _EASYFEA_AVAILABLE)
            return self._solve_scipy(
                tet_mesh, boundary_conditions, material, config,
                progress_callback, surface_mesh,
            )

    # ------------------------------------------------------------------
    # EasyFEA solver path
    # ------------------------------------------------------------------

    def _solve_easyfea(
        self,
        tet_mesh: TetMesh,
        boundary_conditions: Any,
        material: Material,
        config: Dict[str, Any],
        progress_callback: Optional[Callable[[float], None]],
        surface_mesh: Any,
    ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
        """Fixed-point iteration using EasyFEA as the FEA kernel."""
        import time as _time

        min_rho = float(config.get("min_density", 0.10))
        max_rho = float(config.get("max_density", 0.80))
        max_iter = int(config.get("max_iterations", 5))
        pattern = str(config.get("infill_pattern", "gyroid"))
        safety_factor = float(config.get("safety_factor", 2.0))
        bonding_coeff = float(config.get("bonding_coeff", material.bonding_coefficient))

        # Import EasyFEA mesh from .msh file
        # Initialize gmsh with interruptible=False BEFORE EasyFEA touches it
        # (EasyFEA's Mesher.__init__ calls gmsh.initialize() which tries to
        # register signal handlers — fails on background threads)
        import gmsh as _gmsh
        if not _gmsh.isInitialized():
            _gmsh.initialize(interruptible=False)

        _t0 = _time.monotonic()
        mesher = Mesher()
        mesh = mesher.Mesh_Import_mesh(tet_mesh.msh_path)
        n_elems = mesh.Ne
        Logger.log("d", "FEA EasyFEA: imported mesh: %d nodes, %d elems (%.3fs)",
                   mesh.Nn, n_elems, _time.monotonic() - _t0)

        # Resolve BC nodes by bounding-box position queries against the mesh
        _t1 = _time.monotonic()
        fixed_nodes_ef = _easyfea_fixed_nodes(boundary_conditions, mesh, surface_mesh)
        force_groups_ef = _easyfea_force_groups(boundary_conditions, mesh, surface_mesh)
        Logger.log("d", "FEA EasyFEA: BC mapping: %d fixed nodes, %d force groups (%.3fs)",
                   len(fixed_nodes_ef) if fixed_nodes_ef is not None else 0,
                   len(force_groups_ef), _time.monotonic() - _t1)

        # Material scalars
        E_xy = material.E_xy
        nu = material.nu
        n_exp = _PATTERN_EXPONENTS.get(pattern, 1.5)

        density = np.full(n_elems, min_rho, dtype=np.float64)
        stress = np.zeros(n_elems, dtype=np.float64)
        converged = False
        max_change = float("inf")
        iteration = 0

        for iteration in range(max_iter):
            _iter_t = _time.monotonic()

            # Effective isotropic-equivalent properties from density field
            rho_mean = float(np.mean(density))
            scale = rho_mean ** n_exp

            E_avg = E_xy * scale
            E_t_avg = E_xy * bonding_coeff * scale
            # Shear: G = E / (2(1+ν)) for isotropic layer, scaled by bonding for transverse
            G_avg = E_avg / (2.0 * (1.0 + nu))
            nu_t = nu * (bonding_coeff ** 0.5)

            mat = TransverselyIsotropic(
                dim=3,
                El=E_avg,
                Et=E_t_avg,
                Gl=G_avg,
                vl=nu,
                vt=nu_t,
            )

            simu = Simulations.Elastic(mesh, mat)

            # Apply Dirichlet BCs (zero displacement)
            if fixed_nodes_ef is not None and len(fixed_nodes_ef) > 0:
                simu.add_dirichlet(fixed_nodes_ef, [0, 0, 0], ["x", "y", "z"])

            # Apply Neumann BCs (distributed forces)
            for force_nodes, force_vec in force_groups_ef:
                if len(force_nodes) > 0:
                    n_fn = len(force_nodes)
                    fx = float(force_vec.x) / n_fn
                    fy = float(force_vec.y) / n_fn
                    fz = float(force_vec.z) / n_fn
                    simu.add_neumann(force_nodes, [fx, fy, fz], ["x", "y", "z"])

            simu.Solve()

            # Extract von Mises stress per element
            svm = simu.Result("Svm")
            if svm is None or len(svm) == 0:
                stress = np.zeros(n_elems, dtype=np.float64)
            else:
                stress = np.asarray(svm, dtype=np.float64)
                if len(stress) != n_elems:
                    # EasyFEA may return nodal values; average to elements
                    stress = np.full(n_elems, float(np.mean(stress)), dtype=np.float64)

            # Map stress → density candidate
            density_candidate = stress_to_density(
                stress,
                sigma_yield=material.yield_strength,
                rho_min=min_rho,
                rho_max=max_rho,
                method="power",
                safety_factor=safety_factor,
            )

            # Damping
            density_new = _DAMPING * density + (1.0 - _DAMPING) * density_candidate
            density_new = np.clip(density_new, min_rho, max_rho)

            max_change = float(np.max(np.abs(density_new - density)))
            density = density_new

            Logger.log("d", "FEA EasyFEA iter %d: max_change=%.4f, rho_mean=%.3f (%.1fs)",
                       iteration + 1, max_change, float(np.mean(density)),
                       _time.monotonic() - _iter_t)

            if progress_callback is not None:
                progress_callback((iteration + 1) / max_iter)

            if max_change < _CONVERGENCE_TOL:
                converged = True
                break

        info: Dict[str, Any] = {
            "iterations": iteration + 1,
            "converged": converged,
            "max_change": max_change,
        }
        return density, stress, info

    # ------------------------------------------------------------------
    # Scipy fallback solver path (original implementation)
    # ------------------------------------------------------------------

    def _solve_scipy(
        self,
        tet_mesh: TetMesh,
        boundary_conditions: Any,
        material: Material,
        config: Dict[str, Any],
        progress_callback: Optional[Callable[[float], None]],
        surface_mesh: Any,
    ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
        """Fixed-point iteration using the custom scipy FEA kernel."""
        import time as _time

        n_elems = tet_mesh.elements.shape[0]
        min_rho = float(config.get("min_density", 0.10))
        max_rho = float(config.get("max_density", 0.80))
        max_iter = int(config.get("max_iterations", 5))
        pattern = str(config.get("infill_pattern", "gyroid"))
        safety_factor = float(config.get("safety_factor", 2.0))
        bonding_coeff = float(config.get("bonding_coeff", material.bonding_coefficient))

        _t0 = _time.monotonic()
        fixed_nodes = _fixed_nodes_from_bc(boundary_conditions, tet_mesh, surface_mesh)
        force_vector = _build_force_vector(boundary_conditions, tet_mesh, surface_mesh)
        Logger.log("d", "FEA solve: %d fixed nodes, force vector norm=%.2f (%.3fs)",
                   len(fixed_nodes), float(np.linalg.norm(force_vector)),
                   _time.monotonic() - _t0)

        fea_solver = LinearElasticitySolver()

        density = np.full(n_elems, min_rho, dtype=np.float64)
        stress = np.zeros(n_elems, dtype=np.float64)
        converged = False
        max_change = float("inf")
        iteration = 0

        for iteration in range(max_iter):
            _iter_start = _time.monotonic()
            n_exp = _PATTERN_EXPONENTS.get(pattern, 1.5)
            E_eff_arr = material.E_xy * np.power(density, n_exp)
            nu_arr = np.full(n_elems, material.nu, dtype=np.float64)
            _t1 = _time.monotonic()
            Logger.log("d", "FEA iter %d: homogenize=%.3fs", iteration + 1, _t1 - _iter_start)
            K = fea_solver.assemble_stiffness_matrix(
                tet_mesh, E_eff_arr, nu_arr, bonding_coeff=bonding_coeff
            )
            _t2 = _time.monotonic()
            K, f = fea_solver.apply_boundary_conditions(K, force_vector.copy(), fixed_nodes)
            _t3 = _time.monotonic()

            displacements = fea_solver.solve(K, f)
            _t4 = _time.monotonic()

            stress = fea_solver.compute_element_stress(
                tet_mesh, displacements, E_eff_arr, nu_arr,
                bonding_coeff=bonding_coeff,
            )
            _t5 = _time.monotonic()
            Logger.log("d", "FEA iter %d: assemble=%.1fs, BCs=%.1fs, solve=%.1fs, stress=%.1fs",
                       iteration + 1, _t2 - _t1, _t3 - _t2, _t4 - _t3, _t5 - _t4)

            density_candidate = stress_to_density(
                stress,
                sigma_yield=material.yield_strength,
                rho_min=min_rho,
                rho_max=max_rho,
                method="power",
                safety_factor=safety_factor,
            )

            density_new = _DAMPING * density + (1.0 - _DAMPING) * density_candidate
            density_new = np.clip(density_new, min_rho, max_rho)

            max_change = float(np.max(np.abs(density_new - density)))
            density = density_new

            if progress_callback is not None:
                progress_callback((iteration + 1) / max_iter)

            if max_change < _CONVERGENCE_TOL:
                converged = True
                break

        info: Dict[str, Any] = {
            "iterations": iteration + 1,
            "converged": converged,
            "max_change": max_change,
        }
        return density, stress, info


# ---------------------------------------------------------------------------
# EasyFEA BC helpers (position-based node queries)
# ---------------------------------------------------------------------------


def _easyfea_fixed_nodes(bc: Any, mesh: Any, surface_mesh: Any) -> Optional[np.ndarray]:
    """Resolve fixed-face indices to EasyFEA mesh node indices via KDTree proximity.

    Uses nearest-neighbor distance matching instead of AABB (which was too broad
    for flat models — an AABB of the bottom face spans the entire XY extent).
    """
    fixed_face_indices: List[int] = bc.getFixedFaces()
    if not fixed_face_indices:
        return None

    if surface_mesh is None:
        return None

    sv_indices = _face_indices_to_vertex_indices(fixed_face_indices, surface_mesh)
    if not sv_indices:
        return None

    # Get the actual fixed vertex positions
    fixed_positions = np.array([surface_mesh.vertices[i] for i in sv_indices], dtype=np.float64)

    # Get all mesh node positions from EasyFEA
    mesh_coords = mesh.coord  # (N, 3) array of node positions

    # Find mesh nodes that are CLOSE to any fixed vertex (within tolerance)
    from scipy.spatial import KDTree
    fixed_tree = KDTree(fixed_positions)
    # For each mesh node, find distance to nearest fixed vertex
    dists, _ = fixed_tree.query(mesh_coords)

    # Tolerance: nodes within 0.5mm of a fixed surface vertex are constrained
    tolerance = 0.5
    close_mask = dists < tolerance
    close_indices = np.where(close_mask)[0]

    Logger.log("d", "FEA BC: %d fixed face vertices → %d mesh nodes within %.1fmm",
               len(fixed_positions), len(close_indices), tolerance)

    return close_indices if len(close_indices) > 0 else None


def _easyfea_force_groups(
    bc: Any, mesh: Any, surface_mesh: Any
) -> List[Tuple[np.ndarray, Any]]:
    """Map force groups to (EasyFEA node array, force UM.Vector) pairs.

    Uses KDTree proximity matching instead of AABB.
    """
    result: List[Tuple[np.ndarray, Any]] = []
    if surface_mesh is None:
        return result

    mesh_coords = mesh.coord  # (N, 3)

    for force_group in bc.getForceGroups():
        sv_indices = _face_indices_to_vertex_indices(
            [int(fi) for fi in force_group.face_indices], surface_mesh
        )
        if not sv_indices:
            continue

        force_positions = np.array([surface_mesh.vertices[i] for i in sv_indices], dtype=np.float64)

        from scipy.spatial import KDTree
        force_tree = KDTree(force_positions)
        dists, _ = force_tree.query(mesh_coords)

        tolerance = 0.5
        close_mask = dists < tolerance
        force_nodes = np.where(close_mask)[0]

        if len(force_nodes) > 0:
            Logger.log("d", "FEA BC: force group %d face vertices → %d mesh nodes",
                       len(force_positions), len(force_nodes))
            result.append((force_nodes, force_group.force))

    return result


# ---------------------------------------------------------------------------
# Internal helpers (scipy path)
# ---------------------------------------------------------------------------


def _face_indices_to_vertex_indices(
    face_indices: List[int], surface_mesh: Any
) -> List[int]:
    """Expand triangle face indices to unique surface vertex indices.

    Args:
        face_indices: List of triangle face indices into ``surface_mesh.faces``.
        surface_mesh: ``trimesh.Trimesh`` surface mesh providing ``.faces``
            (shape F×3, int).

    Returns:
        Deduplicated list of vertex indices referenced by the given faces.
    """
    vertex_set: Set[int] = set()
    faces = surface_mesh.faces  # (F, 3) int array
    for fi in face_indices:
        for vi in faces[int(fi)]:
            vertex_set.add(int(vi))
    return list(vertex_set)


def _fixed_nodes_from_bc(bc, tet_mesh: TetMesh, surface_mesh: Any = None) -> np.ndarray:
    """Resolve fixed-face triangle indices to tet-mesh node indices.

    The BC decorator stores *face* indices into the surface trimesh.  Each face
    references 3 surface vertices which are then looked up in
    ``tet_mesh.surface_node_map`` (keyed by surface vertex index) to obtain the
    corresponding tet-mesh node indices.

    Args:
        bc: ``FEABoundaryConditionDecorator`` instance.
        tet_mesh: Tetrahedral mesh with ``surface_node_map``.
        surface_mesh: Original ``trimesh.Trimesh`` used to expand face indices
            to vertex indices.  If ``None``, face indices are treated as vertex
            indices directly (legacy fallback).

    Returns:
        1-D ndarray of unique tet-mesh node indices (int64).
    """
    smap = tet_mesh.surface_node_map
    fixed_face_indices: List[int] = bc.getFixedFaces()

    if surface_mesh is not None:
        sv_indices = _face_indices_to_vertex_indices(fixed_face_indices, surface_mesh)
    else:
        sv_indices = [int(fi) for fi in fixed_face_indices]

    tet_node_set: Set[int] = set()
    for sv_idx in sv_indices:
        tn = smap.get(sv_idx)
        if tn is not None:
            tet_node_set.add(tn)

    return np.array(sorted(tet_node_set), dtype=np.int64)


def _build_force_vector(bc, tet_mesh: TetMesh, surface_mesh: Any = None) -> np.ndarray:
    """Distribute force groups onto the global force vector.

    For each force group the total force vector is divided equally among the
    tet-mesh nodes that correspond to the face indices in the group.  Face
    indices are expanded to vertex indices via ``surface_mesh.faces`` when the
    surface mesh is provided.

    Args:
        bc: ``FEABoundaryConditionDecorator`` instance.
        tet_mesh: Tetrahedral mesh.
        surface_mesh: Original ``trimesh.Trimesh`` used to expand face indices
            to vertex indices.  If ``None``, face indices are treated as vertex
            indices directly (legacy fallback).

    Returns:
        Global force vector, shape (n_nodes × 3,), float64.
    """
    n_dof = tet_mesh.nodes.shape[0] * 3
    f = np.zeros(n_dof, dtype=np.float64)
    smap = tet_mesh.surface_node_map

    for force_group in bc.getForceGroups():
        fv = force_group.force  # UM Vector
        fx, fy, fz = float(fv.x), float(fv.y), float(fv.z)

        if surface_mesh is not None:
            sv_indices = _face_indices_to_vertex_indices(
                [int(fi) for fi in force_group.face_indices], surface_mesh
            )
        else:
            sv_indices = [int(fi) for fi in force_group.face_indices]

        # Collect tet nodes for this group's vertex indices
        tet_nodes_in_group: List[int] = []
        for sv_idx in sv_indices:
            tn = smap.get(sv_idx)
            if tn is not None:
                tet_nodes_in_group.append(tn)

        if not tet_nodes_in_group:
            continue

        # Distribute evenly
        n_nodes_grp = len(tet_nodes_in_group)
        fx_per = fx / n_nodes_grp
        fy_per = fy / n_nodes_grp
        fz_per = fz / n_nodes_grp

        for tn in tet_nodes_in_group:
            f[tn * 3 + 0] += fx_per
            f[tn * 3 + 1] += fy_per
            f[tn * 3 + 2] += fz_per

    # ── Torque groups → equivalent tangential nodal forces ───────────
    if hasattr(bc, "getTorqueGroups"):
        for torque_group in bc.getTorqueGroups():
            axis = torque_group.torque_axis  # UM Vector
            T_mag = float(torque_group.torque_magnitude)
            n_axis = np.array([float(axis.x), float(axis.y), float(axis.z)])
            n_norm = np.linalg.norm(n_axis)
            if n_norm < 1e-12 or abs(T_mag) < 1e-12:
                continue
            n_axis = n_axis / n_norm

            if surface_mesh is not None:
                sv_indices = _face_indices_to_vertex_indices(
                    [int(fi) for fi in torque_group.face_indices], surface_mesh
                )
            else:
                sv_indices = [int(fi) for fi in torque_group.face_indices]

            tet_nodes_in_group_t: List[int] = []
            for sv_idx in sv_indices:
                tn = smap.get(sv_idx)
                if tn is not None:
                    tet_nodes_in_group_t.append(tn)

            if not tet_nodes_in_group_t:
                continue

            # Compute center of all torque nodes
            node_positions = np.array([tet_mesh.nodes[tn] for tn in tet_nodes_in_group_t])
            center = node_positions.mean(axis=0)

            # For each node, compute tangential force from torque
            for tn in tet_nodes_in_group_t:
                pos = tet_mesh.nodes[tn]
                r = pos - center
                # Project r onto plane perpendicular to axis
                r_along_axis = np.dot(r, n_axis) * n_axis
                r_perp = r - r_along_axis
                r_perp_mag = np.linalg.norm(r_perp)
                if r_perp_mag < 1e-12:
                    continue  # node is on the axis — no tangential force

                # Tangential direction = axis × r_perp (right-hand rule)
                tangent = np.cross(n_axis, r_perp)
                tangent_mag = np.linalg.norm(tangent)
                if tangent_mag < 1e-12:
                    continue
                tangent = tangent / tangent_mag

                # Force magnitude: T = sum(F_t * r_perp) across all nodes
                # Distribute equally: F_t = T / (N * r_perp)
                n_nodes_t = len(tet_nodes_in_group_t)
                F_t = T_mag / (n_nodes_t * r_perp_mag)

                f[tn * 3 + 0] += F_t * tangent[0]
                f[tn * 3 + 1] += F_t * tangent[1]
                f[tn * 3 + 2] += F_t * tangent[2]

    return f
