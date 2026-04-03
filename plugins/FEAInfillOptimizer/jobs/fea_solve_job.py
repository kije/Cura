# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

import math
from typing import Any, Callable, Dict

from UM.Job import Job
from UM.Message import Message
from UM.Signal import Signal
from UM.i18n import i18nCatalog

from ..fea.mesh_extraction import extract_trimesh
from ..fea.tetrahedralization import tetrahedralize
from ..fea.iterative_solver import IterativeFEASolver
from ..mesh_generation.density_discretizer import discretize_density
from ..mesh_generation.zone_mesh_builder import build_zone_mesh

catalog = i18nCatalog("cura")


class FEASolveJob(Job):
    """Background job that runs the full FEA pipeline for a scene node.

    Signals:
        progress: Emitted with a float in [0, 100] as analysis advances.

    Args:
        node: The CuraSceneNode to analyse.
        bc_decorator: FEABoundaryConditionDecorator attached to the node.
        material: Dict of material properties (e.g. ``{"E": 210e9, "nu": 0.3}``).
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
        material: Dict[str, float],
        config: Dict[str, Any],
    ) -> None:
        super().__init__()
        self._node = node
        self._bc_decorator = bc_decorator
        self._material = material
        self._config = config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_progress(self, value: float) -> None:
        """Emit progress signal and update the status message."""
        self.progress.emit(value)

    def _resolve_element_size(self, bbox_diag: float) -> float:
        """Map mesh_resolution string to a concrete element size in mm.

        Args:
            bbox_diag: Bounding-box diagonal of the target mesh in model units.

        Returns:
            Target element edge length.
        """
        resolution = self._config.get("mesh_resolution", "medium")
        divisors = {"coarse": 10.0, "medium": 20.0, "fine": 40.0}
        divisor = divisors.get(resolution, 20.0)
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
        """
        message = Message(
            catalog.i18nc("@info:status", "Running FEA analysis…"),
            lifetime=0,
            dismissable=False,
            progress=0,
            title=catalog.i18nc("@info:title", "FEA Infill Optimizer"),
        )
        message.show()

        try:
            # ── Step 1: Extract trimesh ──────────────────────────────── 10 %
            self._emit_progress(0.0)
            trimesh = extract_trimesh(self._node)
            self._emit_progress(10.0)
            message.setProgress(10)

            # Compute bounding-box diagonal for element size heuristic
            verts = trimesh.vertices
            bbox_min = verts.min(axis=0)
            bbox_max = verts.max(axis=0)
            bbox_diag = float(math.sqrt(((bbox_max - bbox_min) ** 2).sum()))

            # ── Step 2: Tetrahedralize ───────────────────────────────── 30 %
            element_size = self._resolve_element_size(bbox_diag)
            tet_mesh = tetrahedralize(trimesh, element_size=element_size)
            self._emit_progress(30.0)
            message.setProgress(30)

            # ── Step 3: Run iterative FEA solver ────────────────────── 30–90 %
            solver = IterativeFEASolver()

            def _solver_progress_cb(fraction: float) -> None:
                """Translate solver [0, 1] fraction to overall [30, 90] range."""
                overall = 30.0 + fraction * 60.0
                self._emit_progress(overall)
                message.setProgress(int(overall))

            density_field, stress_field, info = solver.solve(
                tet_mesh=tet_mesh,
                boundary_conditions=self._bc_decorator,
                material=self._material,
                config=self._config,
                progress_callback=_solver_progress_cb,
            )
            iterations = info["iterations"]
            converged = info["converged"]

            # ── Step 5: Discretize density ───────────────────────────── 92 %
            self._emit_progress(92.0)
            message.setProgress(92)
            zone_objects = discretize_density(
                density_per_element=density_field,
                n_zones=self._config.get("n_zones", 5),
                rho_min=self._config.get("min_density", 0.1),
                rho_max=self._config.get("max_density", 1.0),
            )

            # ── Step 6: Build zone surface meshes ───────────────────── 95–100 %
            zones = []
            n_zones = len(zone_objects)
            for i, zone_obj in enumerate(zone_objects):
                progress_val = 95.0 + (i / max(n_zones, 1)) * 5.0
                self._emit_progress(progress_val)
                message.setProgress(int(progress_val))

                mesh_data = build_zone_mesh(tet_mesh, zone_obj.element_indices)
                zones.append({"density": zone_obj.density, "mesh_data": mesh_data})

            # Compute aggregate statistics
            import numpy
            max_stress = float(numpy.max(stress_field)) if len(stress_field) > 0 else 0.0
            min_stress = float(numpy.min(stress_field)) if len(stress_field) > 0 else 0.0
            yield_strength = self._material.get("yield_strength", 250e6)
            safety_factor = (yield_strength / max_stress) if max_stress > 0.0 else float("inf")

            self._emit_progress(100.0)
            message.setProgress(100)

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
                }
            )

        except Exception as exc:
            self.setResult(None)
            self.setError(exc)
        finally:
            message.hide()
            self.finished.emit(self)
