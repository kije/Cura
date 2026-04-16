# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Background job that computes the optimal print orientation.

Runs :class:`~fea.orientation_optimizer.OrientationOptimizer` in a worker
thread and reports progress via a :class:`~UM.Signal.Signal`.  The result
is an :class:`~fea.orientation_optimizer.OrientationResult` dataclass (or
an ``Exception`` on failure).
"""

from typing import Any, Dict, Optional

import numpy as np

from UM.Job import Job
from UM.Signal import Signal

from ..fea.orientation_optimizer import OrientationOptimizer, OrientationResult


class OrientationOptimizeJob(Job):
    """Background job for FEA-driven print orientation analysis.

    Args:
        stress_tensors: Per-element Voigt stress array, shape ``(M, 6)``.
        element_volumes: Per-element volumes, shape ``(M,)``.
        stress_field: Scalar von Mises stress per element, shape ``(M,)``.
        bonding_coeff: Layer bonding coefficient *k*.
        yield_strength: Material yield strength in MPa.
        force_direction: Optional net force direction hint for warm start.
    """

    progress = Signal()

    def __init__(
        self,
        stress_tensors: np.ndarray,
        element_volumes: np.ndarray,
        stress_field: np.ndarray,
        bonding_coeff: float,
        yield_strength: float,
        force_direction: Optional[np.ndarray] = None,
    ) -> None:
        super().__init__()
        self._stress_tensors = stress_tensors
        self._element_volumes = element_volumes
        self._stress_field = stress_field
        self._bonding_coeff = bonding_coeff
        self._yield_strength = yield_strength
        self._force_direction = force_direction

    def run(self) -> None:
        """Execute the orientation optimisation and store the result."""
        try:
            self._emit_progress(0.0)

            optimizer = OrientationOptimizer(
                stress_tensors=self._stress_tensors,
                volumes=self._element_volumes,
                stress_field=self._stress_field,
                bonding_coeff=self._bonding_coeff,
                yield_strength=self._yield_strength,
                force_direction=self._force_direction,
            )
            self._emit_progress(10.0)

            result = optimizer.optimize(
                subdivision_levels=2,
                refine_top_k=3,
            )
            self._emit_progress(100.0)
            self.setResult(result)

        except Exception as exc:
            import traceback
            from UM.Logger import Logger
            Logger.log("e", "Orientation optimize job failed:\n%s",
                       traceback.format_exc())
            self.setResult(exc)

    def _emit_progress(self, value: float) -> None:
        try:
            self.progress.emit(value)
        except Exception:
            pass
