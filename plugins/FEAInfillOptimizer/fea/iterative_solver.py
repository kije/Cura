# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Fixed-point iteration loop coupling FEA and density homogenization.

The algorithm is:

1. Initialise all element densities to ``config["min_density"]``.
2. Repeat until convergence or max iterations:
   a. Homogenize: compute E_eff per element from current density.
   b. Assemble and solve the FEA system.
   c. Compute von Mises stress per element.
   d. Map stress → new density field.
   e. Apply damping: ``ρ_new = 0.5 × ρ_old + 0.5 × ρ_candidate``.
   f. Check convergence: ``max|ρ_new - ρ_old| < tol``.
3. Return final density field, stress field, and diagnostic info dict.

Boundary conditions (fixed faces and force groups) are mapped from surface
triangle face indices to tet-mesh node indices via
``TetMesh.surface_node_map``.
"""

from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

try:
    import trimesh as _trimesh
except ImportError:
    _trimesh = None  # type: ignore[assignment]

from .fea_solver import LinearElasticitySolver
from .homogenization import effective_properties
from .material_database import Material
from .stress_to_density import stress_to_density
from .tetrahedralization import TetMesh

_CONVERGENCE_TOL = 1e-3
_DAMPING = 0.5


class IterativeFEASolver:
    """Orchestrate the fixed-point FEA ↔ density iteration.

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
                surface_node_map).
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
        n_elems = tet_mesh.elements.shape[0]
        min_rho = float(config.get("min_density", 0.10))
        max_rho = float(config.get("max_density", 0.80))
        max_iter = int(config.get("max_iterations", 5))
        pattern = str(config.get("infill_pattern", "gyroid"))

        # --- Build boundary condition arrays from surface face → tet node map ---
        fixed_nodes = _fixed_nodes_from_bc(boundary_conditions, tet_mesh, surface_mesh)
        force_vector = _build_force_vector(boundary_conditions, tet_mesh, surface_mesh)

        fea_solver = LinearElasticitySolver()

        # Initialise uniform density
        density = np.full(n_elems, min_rho, dtype=np.float64)
        stress = np.zeros(n_elems, dtype=np.float64)

        converged = False
        max_change = float("inf")
        iteration = 0

        for iteration in range(max_iter):
            # --- Homogenize ---
            E_eff_arr = np.array(
                [
                    effective_properties(material.E_xy, material.nu, float(rho), pattern)[0]
                    for rho in density
                ],
                dtype=np.float64,
            )
            nu_arr = np.full(n_elems, material.nu, dtype=np.float64)

            # --- Assemble & apply BCs ---
            K = fea_solver.assemble_stiffness_matrix(tet_mesh, E_eff_arr, nu_arr)
            K, f = fea_solver.apply_boundary_conditions(K, force_vector.copy(), fixed_nodes)

            # --- Solve ---
            displacements = fea_solver.solve(K, f)

            # --- Compute stress ---
            stress = fea_solver.compute_element_stress(
                tet_mesh, displacements, E_eff_arr, nu_arr
            )

            # --- Map stress → density candidate ---
            density_candidate = stress_to_density(
                stress,
                sigma_yield=material.yield_strength,
                rho_min=min_rho,
                rho_max=max_rho,
                method="power",
            )

            # --- Damping to prevent oscillation ---
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
# Internal helpers
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

    return f
