# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Map element-wise von Mises stress to infill density values.

Two mapping methods are supported:

- ``"linear"``: density scales linearly with normalised stress.
- ``"power"``:  density scales with the square-root of normalised stress,
  which places more material in moderately stressed regions and avoids
  extreme density contrasts.
"""

import numpy as np


def stress_to_density(
    von_mises: np.ndarray,
    sigma_yield: float,
    rho_min: float,
    rho_max: float,
    method: str = "power",
) -> np.ndarray:
    """Convert element von Mises stresses to infill density fractions.

    Normalised stress ``s = clip(σ_vm / σ_yield, 0, 1)`` is mapped to density
    via the chosen method, then clamped to ``[rho_min, rho_max]``.

    Methods:
    - ``"linear"``:  ``ρ = rho_min + (rho_max - rho_min) × s``
    - ``"power"``:   ``ρ = rho_min + (rho_max - rho_min) × s^0.5``

    Args:
        von_mises: Von Mises stress per element, shape (M,), same units as
            ``sigma_yield``.
        sigma_yield: Yield / reference stress of the material in the same
            pressure unit as ``von_mises``.  Must be positive.
        rho_min: Minimum allowed density fraction (e.g. 0.10 for 10 % infill).
        rho_max: Maximum allowed density fraction (e.g. 0.80 for 80 % infill).
        method: Mapping method; one of ``"linear"`` or ``"power"``.

    Returns:
        Density array, shape (M,), values in ``[rho_min, rho_max]``.

    Raises:
        ValueError: If ``sigma_yield`` ≤ 0, or an unknown ``method`` is given.
    """
    if sigma_yield <= 0.0:
        raise ValueError(f"sigma_yield must be positive, got {sigma_yield}.")
    if method not in ("linear", "power"):
        raise ValueError(
            f"Unknown method '{method}'. Choose 'linear' or 'power'."
        )
    if rho_min >= rho_max:
        raise ValueError(
            f"rho_min ({rho_min}) must be less than rho_max ({rho_max})."
        )

    # Normalise and clamp to [0, 1]
    s = np.clip(np.asarray(von_mises, dtype=np.float64) / sigma_yield, 0.0, 1.0)

    if method == "linear":
        density = rho_min + (rho_max - rho_min) * s
    else:  # "power"
        density = rho_min + (rho_max - rho_min) * np.sqrt(s)

    # Clamp output to [rho_min, rho_max]
    return np.clip(density, rho_min, rho_max)
