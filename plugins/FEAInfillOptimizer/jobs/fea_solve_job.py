# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

import math
import threading
from typing import Any, Callable, Dict

import numpy
import numpy as np

from UM.Job import Job
from UM.Signal import Signal

from ..fea.mesh_extraction import extract_trimesh_from_arrays
from ..fea.tetrahedralization import tetrahedralize
from ..fea.iterative_solver import IterativeFEASolver
from ..mesh_generation.density_discretizer import discretize_density
from ..mesh_generation.zone_mesh_builder import build_zone_mesh


class FEASolveJob(Job):
    """Background job that runs the full FEA pipeline for a scene node.

    Signals:
        progress: Emitted with a float in [0, 100] as analysis advances.

    Args:
        node: The CuraSceneNode to analyse.
        bc_decorator: FEABoundaryConditionDecorator attached to the node.
        material: :class:`~fea.material_database.Material` dataclass instance
            with ``yield_strength``, ``E_xy``, ``nu``, and other properties.
        config: Analysis configuration dict with keys:

            * ``"mesh_resolution"`` (str): ``"coarse"``, ``"medium"``, or
              ``"fine"``.
            * ``"min_density"`` (float): Lower density bound (0–1).
            * ``"max_density"`` (float): Upper density bound (0–1).
            * ``"n_zones"`` (int): Number of discrete density zones.
            * ``"max_iterations"`` (int): Solver iteration limit.
    """

    progress = Signal()

    def __init__(
        self,
        node: Any,
        bc_decorator: Any,
        material: Any,
        config: Dict[str, Any],
        cached_mesh: Any = None,
        initial_density: Any = None,
    ) -> None:
        super().__init__()
        # Pre-capture all scene node data on the main thread.
        # Uranium's SceneNode has no locking — accessing it from a
        # background Job thread is a data race.
        mesh_data = node.getMeshData()
        if mesh_data is None:
            raise ValueError(f"Node '{node.getName()}' has no MeshData.")
        raw_verts = mesh_data.getVertices()
        if raw_verts is None:
            raise ValueError(f"Node '{node.getName()}' MeshData has no vertices.")
        self._vertices = np.array(raw_verts, dtype=np.float64)
        raw_indices = mesh_data.getIndices()
        self._indices = (
            np.array(raw_indices, dtype=np.int64).reshape(-1, 3)
            if raw_indices is not None else None
        )
        self._world_transform = np.array(
            node.getWorldTransformation().getData(), dtype=np.float64
        )
        self._node_name = node.getName()
        self._bc_decorator = bc_decorator
        self._material = material
        self._config = config
        self._cancel_event = threading.Event()

        # Optional cached data from a previous run — avoids re-meshing
        # when only BCs, material, or optimization settings changed.
        self._cached_mesh = cached_mesh       # (surface_mesh, tet_mesh) or None
        self._initial_density = initial_density  # warm-start density field or None

    def requestCancel(self) -> None:
        """Signal the solver to stop at the next iteration boundary."""
        self._cancel_event.set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_progress(self, value: float) -> None:
        """Emit progress signal in a non-blocking way.

        UM.Signal.emit() from a background thread can sometimes block
        waiting for the main thread to process the signal (GIL contention
        or signal queue backpressure). We guard against this with a timeout
        and catch all exceptions to ensure the solver loop never hangs due
        to progress reporting.
        """
        try:
            self.progress.emit(value)
        except Exception:
            pass  # never let progress emission block the solver

    def _resolve_element_size(self, bbox_diag: float) -> float:
        """Map mesh_resolution string to a concrete element size in mm.

        Args:
            bbox_diag: Bounding-box diagonal of the target mesh in model units.

        Returns:
            Target element edge length.
        """
        resolution = self._config.get("mesh_resolution", "medium")
        # Support both legacy string values and numeric divisor
        if isinstance(resolution, (int, float)):
            divisor = max(5.0, min(50.0, float(resolution)))
        else:
            divisors = {"coarse": 10.0, "medium": 20.0, "fine": 40.0}
            divisor = divisors.get(str(resolution), 20.0)
        return bbox_diag / divisor

    # ------------------------------------------------------------------
    # Job entry point
    # ------------------------------------------------------------------

    def run(self) -> None:  # noqa: C901 – intentionally long pipeline method
        """Execute the FEA pipeline and store results on the job object.

        Results are stored in ``self.getResult()`` as a dict with keys:
        ``zones``, ``max_stress``, ``min_stress``, ``safety_factor``,
        ``iterations``, ``converged``, ``stress_field``, ``density_field``,
        ``tet_mesh``.

        Note: Message.show/setProgress/hide must NOT be called from a background
        thread. Progress is communicated to the UI via ``self.progress`` signal
        only; the caller (Extension) is responsible for displaying status UI on
        the main thread.
        """
        try:
            self._emit_progress(0.0)

            if self._cached_mesh is not None:
                # ── Cache hit: skip meshing ──────────────────────── 0→30 %
                surface_mesh, tet_mesh = self._cached_mesh
                from UM.Logger import Logger as _CacheLog
                _CacheLog.log("i", "FEA job: reusing cached mesh (%d elements)",
                              tet_mesh.elements.shape[0])
                self._emit_progress(30.0)
            else:
                # ── Step 1: Extract trimesh ──────────────────────── 10 %
                surface_mesh = extract_trimesh_from_arrays(
                    self._vertices, self._indices,
                    self._world_transform, self._node_name,
                )
                self._emit_progress(10.0)

                # Compute bounding-box diagonal for element size heuristic
                verts = surface_mesh.vertices
                bbox_min = verts.min(axis=0)
                bbox_max = verts.max(axis=0)
                bbox_diag = float(math.sqrt(((bbox_max - bbox_min) ** 2).sum()))

                # ── Step 2: Tetrahedralize ──────────────────────── 30 %
                element_size = self._resolve_element_size(bbox_diag)
                tet_mesh = tetrahedralize(surface_mesh, element_size=element_size)
                self._emit_progress(30.0)

            # ── Step 3: Run iterative FEA solver ────────────────────── 30–90 %
            solver = IterativeFEASolver()

            def _solver_progress_cb(fraction: float) -> None:
                """Translate solver [0, 1] fraction to overall [30, 90] range."""
                overall = 30.0 + fraction * 60.0
                self._emit_progress(overall)

            density_field, stress_field, info = solver.solve(
                tet_mesh=tet_mesh,
                boundary_conditions=self._bc_decorator,
                material=self._material,
                config=self._config,
                progress_callback=_solver_progress_cb,
                surface_mesh=surface_mesh,
                cancel_check=lambda: self._cancel_event.is_set(),
                initial_density=self._initial_density,
            )
            iterations = info["iterations"]
            converged = info["converged"]
            stress_tensors = info.get("stress_tensors")    # (M, 6) or None
            element_volumes = info.get("element_volumes")  # (M,) or None

            # Clean up the temporary .msh file used by EasyFEA
            if tet_mesh.msh_path:
                try:
                    import os as _os
                    _os.unlink(tet_mesh.msh_path)
                except OSError:
                    pass

            # ── Step 5: Discretize density ───────────────────────────── 92 %
            self._emit_progress(92.0)
            zone_objects = discretize_density(
                density_per_element=density_field,
                n_zones=self._config.get("n_zones", 5),
                rho_min=self._config.get("min_density", 0.1),
                rho_max=self._config.get("max_density", 1.0),
            )

            # ── Step 5b: Compute shell thickness per zone ──────────── 93–95 %
            zone_shell_settings = [None] * len(zone_objects)
            shell_optimization_failed = False
            if self._config.get("optimize_shell", True) and stress_tensors is not None:
                try:
                    from ..fea.surface_stress_analyzer import (
                        identify_surface_elements,
                        classify_surface_elements,
                        compute_stress_gradient,
                        compute_wall_metric,
                        compute_tb_metric,
                    )
                    from ..mesh_generation.shell_thickness_mapper import compute_zone_shell_settings

                    self._emit_progress(93.0)
                    sigma_eff = self._material.yield_strength / float(
                        self._config.get("safety_factor", 2.0))
                    bonding_coeff = float(self._config.get(
                        "bonding_coeff", self._material.bonding_coefficient))

                    surface_mask = identify_surface_elements(tet_mesh)
                    wall_mask, top_mask, bottom_mask = classify_surface_elements(
                        tet_mesh, surface_mask)
                    grad_sigma = compute_stress_gradient(
                        tet_mesh, stress_field, surface_mask)
                    W_wall = compute_wall_metric(
                        stress_field, grad_sigma, wall_mask, sigma_eff)
                    W_top, W_bottom = compute_tb_metric(
                        stress_tensors, top_mask, bottom_mask,
                        sigma_eff, bonding_coeff)

                    zone_shell_settings = compute_zone_shell_settings(
                        zone_objects, W_wall, W_top, W_bottom,
                        wall_mask, top_mask, bottom_mask,
                        line_width=float(self._config.get("line_width", 0.4)),
                        layer_height=float(self._config.get("layer_height", 0.2)),
                        bonding_coeff=bonding_coeff,
                        wall_count_min=int(self._config.get("wall_count_min", 1)),
                        wall_count_max=int(self._config.get("wall_count_max", 6)),
                        top_layers_min=int(self._config.get("top_layers_min", 2)),
                        top_layers_max=int(self._config.get("top_layers_max", 8)),
                        bottom_layers_min=int(self._config.get("bottom_layers_min", 2)),
                        bottom_layers_max=int(self._config.get("bottom_layers_max", 8)),
                    )
                    self._emit_progress(95.0)
                except Exception as _shell_exc:
                    import traceback as _tb
                    from UM.Logger import Logger as _Logger
                    _Logger.log("d", "FEA job: shell optimization traceback:\n%s",
                                _tb.format_exc())
                    _Logger.log("w", "FEA job: shell optimization failed (non-fatal): %s",
                                _shell_exc)
                    zone_shell_settings = [None] * len(zone_objects)
                    shell_optimization_failed = True

            # ── Step 6: Build zone surface meshes ───────────────────── 95–100 %
            zones = []
            n_zones = len(zone_objects)
            for i, zone_obj in enumerate(zone_objects):
                progress_val = 95.0 + (i / max(n_zones, 1)) * 5.0
                self._emit_progress(progress_val)

                mesh_data = build_zone_mesh(tet_mesh, zone_obj.element_indices)
                shell = zone_shell_settings[i] if i < len(zone_shell_settings) else None
                zone_dict = {"density": zone_obj.density, "mesh_data": mesh_data}
                if shell is not None:
                    zone_dict["shell"] = shell
                zones.append(zone_dict)

            # Compute aggregate statistics
            from UM.Logger import Logger as _Logger
            _Logger.log("d", "FEA job: stress_field shape=%s, min=%.4f, max=%.4f",
                       stress_field.shape, float(stress_field.min()) if len(stress_field) > 0 else 0,
                       float(stress_field.max()) if len(stress_field) > 0 else 0)
            max_stress = float(numpy.max(stress_field)) if len(stress_field) > 0 else 0.0
            min_stress = float(numpy.min(stress_field)) if len(stress_field) > 0 else 0.0
            yield_strength = self._material.yield_strength
            safety_factor = (yield_strength / max_stress) if max_stress > 0.0 else float("inf")

            self._emit_progress(100.0)

            self.setResult(
                {
                    "zones": zones,
                    "max_stress": max_stress,
                    "min_stress": min_stress,
                    "safety_factor": safety_factor,
                    "iterations": iterations,
                    "converged": converged,
                    "stress_field": stress_field,
                    "density_field": density_field,
                    "tet_mesh": tet_mesh,
                    "surface_mesh": surface_mesh,
                    "mesh_quality": tet_mesh.mesh_quality,
                    "mesh_method": tet_mesh.mesh_method,
                    "mesh_warnings": tet_mesh.warnings,
                    "stress_tensors": stress_tensors,
                    "element_volumes": element_volumes,
                    "shell_optimization_failed": shell_optimization_failed,
                }
            )

        except Exception as exc:
            import traceback
            from UM.Logger import Logger
            Logger.log("e", "FEA Infill: Analysis job failed:\n%s", traceback.format_exc())
            self.setResult(exc)  # Pass the exception as result so Extension can display it
