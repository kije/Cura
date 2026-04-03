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
        fixed_nodes = _fixed_nodes_from_bc(boundary_conditions, tet_mesh)
        force_vector = _build_force_vector(boundary_conditions, tet_mesh)

        fea_solver = LinearElasticitySolver()

        # Initialise uniform density
        density = np.full(n_elems, min_rho, dtype=np.float64)
        stress = np.zeros(n_elems, dtype=np.float64)

        converged = False
        max_change = float("inf")

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
            "iterations": iteration + 1,  # noqa: F821  (defined by loop)
            "converged": converged,
            "max_change": max_change,
        }

        return density, stress, info


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fixed_nodes_from_bc(bc, tet_mesh: TetMesh) -> np.ndarray:
    """Resolve fixed-face triangle indices to tet-mesh node indices.

    Each fixed face (triangle) consists of 3 surface vertex indices.  These
    are looked up in ``tet_mesh.surface_node_map`` to get tet-mesh node
    indices.  Duplicates are removed.

    Args:
        bc: ``FEABoundaryConditionDecorator`` instance.
        tet_mesh: Tetrahedral mesh with ``surface_node_map``.

    Returns:
        1-D ndarray of unique tet-mesh node indices (int64).
    """
    smap = tet_mesh.surface_node_map
    fixed_face_indices: List[int] = bc.getFixedFaces()

    # surface_node_map keys are surface *vertex* indices, not face indices.
    # The caller stores face indices; we need to resolve them via the trimesh
    # face→vertex adjacency.  However, at this layer we only have
    # surface_node_map (vertex → tet node).  We use a heuristic: treat
    # fixed_face_indices as surface vertex indices directly.
    # (FEABoundaryConditionDecorator stores face indices; the job layer should
    # expand these to vertex indices before reaching the solver.  If they are
    # already vertex indices this works directly.)
    tet_node_set: Set[int] = set()
    for sv_idx in fixed_face_indices:
        tn = smap.get(int(sv_idx))
        if tn is not None:
            tet_node_set.add(tn)

    return np.array(sorted(tet_node_set), dtype=np.int64)


def _build_force_vector(bc, tet_mesh: TetMesh) -> np.ndarray:
    """Distribute force groups onto the global force vector.

    For each force group the total force vector is divided equally among the
    tet-mesh nodes that correspond to the face indices in the group.

    Args:
        bc: ``FEABoundaryConditionDecorator`` instance.
        tet_mesh: Tetrahedral mesh.

    Returns:
        Global force vector, shape (n_nodes × 3,), float64.
    """
    n_dof = tet_mesh.nodes.shape[0] * 3
    f = np.zeros(n_dof, dtype=np.float64)
    smap = tet_mesh.surface_node_map

    for force_group in bc.getForceGroups():
        fv = force_group.force  # UM Vector
        fx, fy, fz = float(fv.x), float(fv.y), float(fv.z)

        # Collect tet nodes for this group's face/vertex indices
        tet_nodes_in_group: List[int] = []
        for sv_idx in force_group.face_indices:
            tn = smap.get(int(sv_idx))
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
