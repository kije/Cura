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
from .oc_update import oc_density_update
from .stress_to_density import stress_to_density
from .tetrahedralization import TetMesh

_CONVERGENCE_TOL = 1e-3
_DAMPING_INITIAL = 0.5
_DAMPING_MIN = 0.2

_PATTERN_EXPONENTS: Dict[str, float] = {
    # Stretching-dominated (n ≈ 1): stiffness scales linearly with density
    "lines": 1.0,
    "zigzag": 1.0,
    "concentric": 1.0,
    # Mixed (n ≈ 1.3–1.6): some stretching, some bending
    "triangles": 1.3,
    "trihexagon": 1.3,
    "gyroid": 1.6,
    "lightning": 1.0,  # sparse tree structure, acts like lines
    # Bending-dominated (n ≈ 2.0): stiffness scales quadratically
    "grid": 2.0,
    "cubic": 2.0,
    "cubicsubdiv": 2.0,
    "tetrahedral": 1.8,  # octet truss — between stretching and bending
    "quarter_cubic": 1.8,
    "octagon": 2.0,
    # Highly bending-dominated (n > 2): cell wall bending dominates
    "honeycomb": 2.3,
    "cross": 2.3,
    "cross_3d": 2.3,
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

        # Volumetric locking guard — linear tet elements (C3D4) cannot
        # represent constant-volume deformation modes, producing spuriously
        # stiff and qualitatively wrong results when nu approaches 0.5.
        if material.nu > 0.45:
            raise ValueError(
                f"Material '{material.name}' has Poisson's ratio {material.nu} > 0.45. "
                "Linear tetrahedral elements suffer from volumetric locking at high "
                "Poisson's ratios, producing qualitatively wrong results. "
                "Use a different material or reduce nu."
            )

        # Warn early if the material is hyperelastic / near-incompressible —
        # linear elastic FEA is not valid in that regime.
        if material.E_xy < 100.0:
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

        opt_method = str(config.get("optimization_method", "heuristic"))
        use_easyfea = _EASYFEA_AVAILABLE and bool(tet_mesh.msh_path) and os.path.exists(tet_mesh.msh_path)

        # SIMP OC requires scipy path (needs per-element stiffness + displacement access)
        if opt_method == "oc":
            use_easyfea = False
            Logger.log("i", "FEA solve: SIMP OC selected → forcing scipy solver path")

        if use_easyfea:
            Logger.log("i", "FEA solve: using EasyFEA solver (msh=%s)", tet_mesh.msh_path)
            return self._solve_easyfea(
                tet_mesh, boundary_conditions, material, config,
                progress_callback, surface_mesh,
            )
        else:
            Logger.log("i", "FEA solve: using scipy solver (EasyFEA available=%s, opt=%s)",
                       _EASYFEA_AVAILABLE, opt_method)
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
        import threading as _threading
        import time as _time

        min_rho = float(config.get("min_density", 0.10))
        max_rho = float(config.get("max_density", 0.80))
        max_iter = int(config.get("max_iterations", 20))
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

        # Start at midpoint density (not min_rho) so the first iteration has
        # a well-conditioned stiffness matrix.  Starting at min_rho creates
        # extremely low element stiffness (e.g. 0.8% of full for rho=0.05,
        # n=1.6) which causes solver timeout or singular matrix.
        initial_rho = (min_rho + max_rho) / 2.0
        density = np.full(n_elems, initial_rho, dtype=np.float64)
        stress = np.zeros(n_elems, dtype=np.float64)
        converged = False
        max_change = float("inf")
        iteration = 0

        # Adaptive damping: reduce damping when oscillating (max_change grows)
        damping = _DAMPING_INITIAL
        prev_max_change = float("inf")
        oscillation_count = 0

        # Timeout in seconds for a single simu.Solve() call.  If it does not
        # return within this budget the iteration loop is aborted with an error
        # log rather than hanging the process indefinitely.
        _SOLVE_TIMEOUT = 120.0

        # Track the previous Simulations.Elastic instance so we can remove it
        # from the mesh observer list before creating the next one.
        #
        # ROOT CAUSE of the hang: _Simu.__init__ calls mesh._Add_observer(self)
        # and model._Add_observer(self), registering every new Elastic instance
        # as a listener on the shared `mesh` object.  Without explicit removal,
        # each iteration *appends* a stale observer.  By iteration 2-4 the
        # mesh carries 2-4 live observer references, and any Need_Update()
        # notification (triggered during stiffness-matrix assembly in Solve())
        # fans out to all of them.  The cascade of redundant matrix rebuilds
        # grows exponentially and the process appears to hang indefinitely.
        # This is intermittent because CPython's GC sometimes collects the
        # stale simu objects in time (ref cycle through material observer list),
        # allowing the solver to complete before the fan-out reaches a
        # pathological size.
        _prev_simu: Optional[Any] = None

        for iteration in range(max_iter):
            _iter_t = _time.monotonic()

            # --- Step 1: homogenize (per-element) --------------------------
            # Each element gets its own stiffness scaled by density^n_exp.
            # EasyFEA supports heterogeneous materials via per-element C
            # matrices: when material.C has shape (n_elems, 6, 6) instead of
            # (6, 6), the stiffness assembly uses per-element properties.
            _t0 = _time.monotonic()

            nu_t = nu * (bonding_coeff ** 0.5)
            G_base = E_xy / (2.0 * (1.0 + nu))
            E_t_base = E_xy * bonding_coeff

            mat = TransverselyIsotropic(
                dim=3,
                El=E_xy,
                Et=E_t_base,
                Gl=G_base,
                vl=nu,
                vt=nu_t,
            )

            # Get the base (6,6) constitutive matrix, then scale per element
            C_base = mat.C.copy()  # (6, 6)
            scales = np.power(density, n_exp)  # (n_elems,)
            # Broadcast: (n_elems, 1, 1) * (6, 6) → (n_elems, 6, 6)
            C_per_elem = scales[:, np.newaxis, np.newaxis] * C_base[np.newaxis, :, :]
            mat.C = C_per_elem  # triggers isHeterogeneous = True

            Logger.log("d", "FEA EasyFEA iter %d step 1 homogenize: per-element C (%d elems), "
                       "rho_mean=%.3f, scale_range=[%.4f, %.4f] (%.3fs)",
                       iteration + 1, n_elems, float(np.mean(density)),
                       float(scales.min()), float(scales.max()),
                       _time.monotonic() - _t0)

            # --- Step 2: build simulation (observer-safe) -------------------
            # Remove the previous simu from the mesh/model observer lists
            # BEFORE creating the new one to prevent observer accumulation.
            _t0 = _time.monotonic()
            if _prev_simu is not None:
                try:
                    mesh._Remove_observer(_prev_simu)
                except Exception:
                    pass
                try:
                    _prev_simu.model._Remove_observer(_prev_simu)
                except Exception:
                    pass
            _prev_simu = None  # drop ref so GC can reclaim it

            simu = Simulations.Elastic(mesh, mat)
            # Force the simulation to rebuild its stiffness matrix from scratch
            # rather than relying on the observer/Need_Update mechanism which
            # can accumulate stale state between iterations.
            try:
                simu.Need_Update()
            except Exception:
                pass
            Logger.log("d", "FEA EasyFEA iter %d step 2 build simu: %.3fs",
                       iteration + 1, _time.monotonic() - _t0)

            # --- Step 3: apply BCs -----------------------------------------
            _t0 = _time.monotonic()
            # Apply Dirichlet BCs (zero displacement)
            if fixed_nodes_ef is not None and len(fixed_nodes_ef) > 0:
                simu.add_dirichlet(fixed_nodes_ef, [0, 0, 0], ["x", "y", "z"])

            # Apply Neumann BCs (area-weighted force distribution)
            # NOTE on EasyFEA semantics: add_neumann → __Bc_pointLoad divides
            # values by len(nodes) internally (_simu.py:2351).  So passing the
            # TOTAL force to a multi-node array distributes it equally.  The
            # per-node weighted path below passes F*w to single-node arrays
            # (len=1, so division is a no-op) which is also correct.
            for force_nodes, force_vec, area_weights in force_groups_ef:
                if len(force_nodes) > 0:
                    fx_total = float(force_vec.x)
                    fy_total = float(force_vec.y)
                    fz_total = float(force_vec.z)

                    if area_weights is not None and len(area_weights) == len(force_nodes):
                        # Per-node area-weighted distribution
                        for ni, w in zip(force_nodes, area_weights):
                            simu.add_neumann(
                                np.array([ni]),
                                [fx_total * w, fy_total * w, fz_total * w],
                                ["x", "y", "z"]
                            )
                    else:
                        # Equal distribution — pass total force; EasyFEA
                        # divides by len(nodes) in __Bc_pointLoad
                        simu.add_neumann(
                            force_nodes,
                            [fx_total, fy_total, fz_total],
                            ["x", "y", "z"]
                        )
            Logger.log("d", "FEA EasyFEA iter %d step 3 apply BCs: %.3fs",
                       iteration + 1, _time.monotonic() - _t0)

            # --- Step 4: solve (with 60s timeout) ---------------------------
            # simu.Solve() calls into scipy/superLU/pardiso linear algebra.
            # Run it on a daemon thread so we can detect a hang and abort
            # cleanly rather than freezing the Cura process forever.
            _t0 = _time.monotonic()
            _solve_exc: List[Optional[Exception]] = [None]

            def _solve_worker():
                try:
                    simu.Solve()
                except Exception as _exc:
                    _solve_exc[0] = _exc

            _solve_thread = _threading.Thread(target=_solve_worker, daemon=True)
            _solve_thread.start()
            _solve_thread.join(timeout=_SOLVE_TIMEOUT)

            if _solve_thread.is_alive():
                Logger.log("e",
                    "FEA EasyFEA iter %d: Solve() did not finish within %.0fs "
                    "— aborting iteration loop.  Check that boundary conditions "
                    "fully constrain rigid-body motion (the stiffness matrix "
                    "may be singular).",
                    iteration + 1, _SOLVE_TIMEOUT)
                _prev_simu = simu  # still deregister on exit
                break

            if _solve_exc[0] is not None:
                raise _solve_exc[0]

            Logger.log("d", "FEA EasyFEA iter %d step 4 Solve(): %.3fs",
                       iteration + 1, _time.monotonic() - _t0)

            # Keep a ref so we can deregister it at the top of the next iter.
            _prev_simu = simu

            # --- Step 5: extract stress -------------------------------------
            _t0 = _time.monotonic()
            svm = simu.Result("Svm")
            max_disp = float(np.max(np.abs(simu.displacement))) if simu.displacement is not None else 0.0
            Logger.log("d", "FEA EasyFEA: max displacement=%.6f mm", max_disp)

            if svm is None or len(svm) == 0:
                Logger.log("w", "FEA EasyFEA: Svm returned None or empty!")
                stress = np.zeros(n_elems, dtype=np.float64)
            else:
                svm_arr = np.asarray(svm, dtype=np.float64)
                Logger.log("d", "FEA EasyFEA: Svm shape=%s (nodes=%d, elems=%d), min=%.4f, max=%.4f MPa",
                           svm_arr.shape, mesh.Nn, n_elems, float(svm_arr.min()), float(svm_arr.max()))

                if len(svm_arr) == n_elems:
                    # Per-element stress — use directly
                    stress = svm_arr
                elif len(svm_arr) == mesh.Nn:
                    # Per-node stress — average to elements
                    # Each element's stress = mean of its 4 node stresses
                    connect = mesh.groupElem.connect  # (n_elems, 4) connectivity
                    stress = np.mean(svm_arr[connect], axis=1)
                    Logger.log("d", "FEA EasyFEA: mapped %d nodal stresses to %d element stresses",
                               len(svm_arr), len(stress))
                else:
                    Logger.log("w", "FEA EasyFEA: unexpected Svm length %d", len(svm_arr))
                    stress = np.full(n_elems, float(np.mean(svm_arr)), dtype=np.float64)
            Logger.log("d", "FEA EasyFEA iter %d step 5 extract stress: %.3fs",
                       iteration + 1, _time.monotonic() - _t0)

            # --- Step 6: density update -------------------------------------
            # Map stress → density candidate
            density_candidate = stress_to_density(
                stress,
                sigma_yield=material.yield_strength,
                rho_min=min_rho,
                rho_max=max_rho,
                method="power",
                safety_factor=safety_factor,
            )

            # Adaptive damping
            density_new = damping * density + (1.0 - damping) * density_candidate
            density_new = np.clip(density_new, min_rho, max_rho)

            max_change = float(np.max(np.abs(density_new - density)))
            density = density_new

            # Detect oscillation: if max_change grew for 2 consecutive iters,
            # reduce damping to stabilize convergence.
            if max_change > prev_max_change:
                oscillation_count += 1
                if oscillation_count >= 2:
                    damping = max(_DAMPING_MIN, damping * 0.8)
                    Logger.log("d", "FEA EasyFEA iter %d: oscillation detected, "
                               "reducing damping to %.3f", iteration + 1, damping)
                    oscillation_count = 0
            else:
                oscillation_count = 0
            prev_max_change = max_change

            Logger.log("d", "FEA EasyFEA iter %d: max_change=%.4f, damping=%.3f, "
                       "rho_mean=%.3f (total %.1fs)",
                       iteration + 1, max_change, damping,
                       float(np.mean(density)), _time.monotonic() - _iter_t)

            if progress_callback is not None:
                progress_callback((iteration + 1) / max_iter)

            if max_change < _CONVERGENCE_TOL:
                converged = True
                break

        # Deregister the final simu so the mesh does not retain a stale ref.
        if _prev_simu is not None:
            try:
                mesh._Remove_observer(_prev_simu)
            except Exception:
                pass
            try:
                _prev_simu.model._Remove_observer(_prev_simu)
            except Exception:
                pass

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
        max_iter = int(config.get("max_iterations", 20))
        pattern = str(config.get("infill_pattern", "gyroid"))
        safety_factor = float(config.get("safety_factor", 2.0))
        bonding_coeff = float(config.get("bonding_coeff", material.bonding_coefficient))

        # OC optimization method: "heuristic" (default) or "oc"
        opt_method = str(config.get("optimization_method", "heuristic"))
        volume_fraction = float(config.get("volume_fraction", 0.5))
        use_oc = opt_method == "oc"
        if use_oc:
            Logger.log("i", "FEA solve: using SIMP Optimality Criteria (OC) density update, "
                       "volume_fraction=%.2f", volume_fraction)

        _t0 = _time.monotonic()
        fixed_nodes = _fixed_nodes_from_bc(boundary_conditions, tet_mesh, surface_mesh)
        force_vector = _build_force_vector(boundary_conditions, tet_mesh, surface_mesh)
        Logger.log("d", "FEA solve: %d fixed nodes, force vector norm=%.2f (%.3fs)",
                   len(fixed_nodes), float(np.linalg.norm(force_vector)),
                   _time.monotonic() - _t0)

        fea_solver = LinearElasticitySolver()

        initial_rho = (min_rho + max_rho) / 2.0
        density = np.full(n_elems, initial_rho, dtype=np.float64)
        stress = np.zeros(n_elems, dtype=np.float64)
        converged = False
        max_change = float("inf")
        iteration = 0

        # Adaptive damping state
        damping = _DAMPING_INITIAL
        prev_max_change = float("inf")
        oscillation_count = 0

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

            # Use Tsai-Hill failure criterion for anisotropic materials,
            # von Mises for isotropic.  The failure index is converted back
            # to an equivalent stress (MPa) for the density mapping.
            if bonding_coeff < 0.95:
                stress = fea_solver.compute_element_failure_index(
                    tet_mesh, displacements, E_eff_arr, nu_arr,
                    bonding_coeff=bonding_coeff,
                    yield_strength=material.yield_strength,
                )
            else:
                stress = fea_solver.compute_element_stress(
                    tet_mesh, displacements, E_eff_arr, nu_arr,
                    bonding_coeff=bonding_coeff,
                )
            _t5 = _time.monotonic()
            Logger.log("d", "FEA iter %d: assemble=%.1fs, BCs=%.1fs, solve=%.1fs, stress=%.1fs",
                       iteration + 1, _t2 - _t1, _t3 - _t2, _t4 - _t3, _t5 - _t4)

            if use_oc:
                # SIMP OC density update — uses compliance sensitivity
                # and bisection on Lagrange multiplier for volume constraint
                n_exp = _PATTERN_EXPONENTS.get(pattern, 1.5)
                density_new = oc_density_update(
                    density,
                    tet_mesh,
                    displacements,
                    E_base=material.E_xy,
                    nu=material.nu,
                    n_exp=n_exp,
                    rho_min=min_rho,
                    rho_max=max_rho,
                    volume_fraction=volume_fraction,
                    move_limit=0.2,
                    eta=0.5,
                    bonding_coeff=bonding_coeff,
                )
            else:
                # Heuristic stress-to-density mapping (default)
                density_candidate = stress_to_density(
                    stress,
                    sigma_yield=material.yield_strength,
                    rho_min=min_rho,
                    rho_max=max_rho,
                    method="power",
                    safety_factor=safety_factor,
                )

                # Adaptive damping
                density_new = damping * density + (1.0 - damping) * density_candidate
                density_new = np.clip(density_new, min_rho, max_rho)

            max_change = float(np.max(np.abs(density_new - density)))
            density = density_new

            if max_change > prev_max_change:
                oscillation_count += 1
                if oscillation_count >= 2:
                    damping = max(_DAMPING_MIN, damping * 0.8)
                    Logger.log("d", "FEA iter %d: oscillation detected, "
                               "reducing damping to %.3f", iteration + 1, damping)
                    oscillation_count = 0
            else:
                oscillation_count = 0
            prev_max_change = max_change

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

    # Tolerance: scale-relative — use 3% of the mesh bounding box diagonal
    # to handle both small (< 20mm) and large (> 500mm) models correctly.
    bbox_diag = float(np.linalg.norm(mesh_coords.max(axis=0) - mesh_coords.min(axis=0)))
    tolerance = max(0.1, bbox_diag * 0.03)  # minimum 0.1mm floor
    close_mask = dists < tolerance
    close_indices = np.where(close_mask)[0]

    Logger.log("d", "FEA BC: %d fixed face vertices → %d mesh nodes within %.2fmm (bbox_diag=%.1f)",
               len(fixed_positions), len(close_indices), tolerance, bbox_diag)

    return close_indices if len(close_indices) > 0 else None


def _easyfea_force_groups(
    bc: Any, mesh: Any, surface_mesh: Any
) -> List[Tuple[np.ndarray, Any, Optional[np.ndarray]]]:
    """Map force groups to (EasyFEA node array, force vector, area_weights) tuples.

    Uses KDTree proximity matching. Returns per-node tributary area weights
    so forces are distributed proportional to the area each node represents,
    not equally.
    """
    result: List[Tuple[np.ndarray, Any, Optional[np.ndarray]]] = []
    if surface_mesh is None:
        return result

    mesh_coords = mesh.coord  # (N, 3)

    for force_group in bc.getForceGroups():
        face_indices = [int(fi) for fi in force_group.face_indices]
        sv_indices = _face_indices_to_vertex_indices(face_indices, surface_mesh)
        if not sv_indices:
            continue

        force_positions = np.array([surface_mesh.vertices[i] for i in sv_indices], dtype=np.float64)

        from scipy.spatial import KDTree
        force_tree = KDTree(force_positions)
        dists, nearest = force_tree.query(mesh_coords)

        bbox_diag = float(np.linalg.norm(mesh_coords.max(axis=0) - mesh_coords.min(axis=0)))
        tolerance = max(0.1, bbox_diag * 0.03)
        close_mask = dists < tolerance
        force_nodes = np.where(close_mask)[0]

        if len(force_nodes) == 0:
            continue

        # Compute tributary area weights per surface vertex
        # Each triangle contributes 1/3 of its area to each of its vertices
        vertex_areas = np.zeros(len(surface_mesh.vertices), dtype=np.float64)
        verts = np.array(surface_mesh.vertices, dtype=np.float64)
        faces = np.array(surface_mesh.faces, dtype=np.int32)
        for fi in face_indices:
            if fi >= len(faces):
                continue
            tri = faces[fi]
            v0, v1, v2 = verts[tri[0]], verts[tri[1]], verts[tri[2]]
            area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))
            for vi in tri:
                vertex_areas[vi] += area / 3.0

        # Map tributary areas from surface vertices to mesh nodes
        # Each mesh node inherits the area of its nearest surface vertex
        sv_indices_arr = np.array(sv_indices, dtype=np.int32)
        node_weights = np.zeros(len(force_nodes), dtype=np.float64)
        for i, ni in enumerate(force_nodes):
            nearest_sv = nearest[ni]
            if nearest_sv < len(sv_indices_arr):
                sv_idx = sv_indices_arr[nearest_sv]
                node_weights[i] = vertex_areas[sv_idx]

        total_weight = node_weights.sum()
        if total_weight > 0:
            node_weights /= total_weight  # normalize to sum=1
        else:
            node_weights = np.ones(len(force_nodes)) / len(force_nodes)  # equal fallback

        Logger.log("d", "FEA BC: force group %d faces → %d mesh nodes (area-weighted)",
                   len(face_indices), len(force_nodes))
        result.append((force_nodes, force_group.force, node_weights))

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

        # Distribute proportional to tributary area (area/3 per vertex per face)
        face_indices_int = [int(fi) for fi in force_group.face_indices]
        node_weights: Dict[int, float] = {}

        if surface_mesh is not None:
            verts_arr = np.array(surface_mesh.vertices, dtype=np.float64)
            faces_arr = np.array(surface_mesh.faces, dtype=np.int32)
            for fi in face_indices_int:
                if fi >= len(faces_arr):
                    continue
                tri = faces_arr[fi]
                v0, v1, v2 = verts_arr[tri[0]], verts_arr[tri[1]], verts_arr[tri[2]]
                area = 0.5 * float(np.linalg.norm(np.cross(v1 - v0, v2 - v0)))
                for vi in tri:
                    tn = smap.get(int(vi))
                    if tn is not None:
                        node_weights[tn] = node_weights.get(tn, 0.0) + area / 3.0
        else:
            for tn in tet_nodes_in_group:
                node_weights[tn] = node_weights.get(tn, 0.0) + 1.0

        total_weight = sum(node_weights.values())
        if total_weight < 1e-16 or not node_weights:
            # Fallback to equal distribution
            node_weights = {tn: 1.0 for tn in tet_nodes_in_group}
            total_weight = float(len(tet_nodes_in_group))
            if total_weight < 1e-16:
                continue

        for tn, w in node_weights.items():
            f[tn * 3 + 0] += fx * w / total_weight
            f[tn * 3 + 1] += fy * w / total_weight
            f[tn * 3 + 2] += fz * w / total_weight

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

            # Pre-compute perpendicular distances for rigid-body rotation model.
            # Physical model: F_i = lambda * r_perp_i (rigid body angular disp)
            # Total torque: T = sum(F_i * r_perp_i) = lambda * sum(r_perp_i^2)
            # So: lambda = T / sum(r_perp_i^2), F_i = T * r_perp_i / sum(r_perp_i^2)
            r_perps = []
            tangents = []
            for tn in tet_nodes_in_group_t:
                pos = tet_mesh.nodes[tn]
                r = pos - center
                r_along_axis = np.dot(r, n_axis) * n_axis
                r_perp = r - r_along_axis
                r_perp_mag = float(np.linalg.norm(r_perp))
                tangent = np.cross(n_axis, r_perp)
                tangent_mag = float(np.linalg.norm(tangent))
                if tangent_mag > 1e-12:
                    tangent = tangent / tangent_mag
                r_perps.append(r_perp_mag)
                tangents.append(tangent)

            sum_r2 = sum(r ** 2 for r in r_perps)
            if sum_r2 < 1e-24:
                continue  # all nodes on axis — cannot apply torque

            for i, tn in enumerate(tet_nodes_in_group_t):
                r_perp_mag = r_perps[i]
                if r_perp_mag < 1e-12:
                    continue  # node on axis — no tangential force
                tangent = tangents[i]
                # Rigid-body rotation: F_i proportional to r_perp_i
                F_t = T_mag * r_perp_mag / sum_r2

                f[tn * 3 + 0] += F_t * tangent[0]
                f[tn * 3 + 1] += F_t * tangent[1]
                f[tn * 3 + 2] += F_t * tangent[2]

    return f
