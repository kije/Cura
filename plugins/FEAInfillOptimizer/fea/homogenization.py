# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Homogenization: compute effective elastic properties for infill regions.

Uses a power-law model (also known as SIMP — Solid Isotropic Material with
Penalization) to relate infill density fraction to effective stiffness.

    E_eff = E_bulk × density_fraction^n

where *n* (the penalization exponent) depends on the infill pattern.
"""

from typing import Tuple

import numpy as np

# Penalization exponents per infill pattern.
# Higher n → stronger contrast between dense and sparse regions (more aggressive
# material redistribution).
_PATTERN_EXPONENTS: dict[str, float] = {
    "lines": 1.0,
    "grid": 1.2,
    "triangles": 1.4,
    "gyroid": 1.3,
    "cubic": 1.5,
    "honeycomb": 1.5,
}

_DEFAULT_EXPONENT = 1.3  # fallback for unknown patterns


def effective_properties(
    E_bulk: float,
    nu: float,
    density_fraction: float,
    pattern: str,
) -> Tuple[float, float]:
    """Compute effective Young's modulus and Poisson's ratio for an infill region.

    Uses the SIMP power law::

        E_eff = E_bulk × density_fraction^n

    Poisson's ratio is treated as approximately constant with density (a common
    assumption in structural topology optimisation for moderate density ranges).

    Args:
        E_bulk: Young's modulus of the solid material in MPa.
        nu: Poisson's ratio of the solid material (dimensionless).
        density_fraction: Volumetric infill fraction in [0, 1].
        pattern: Infill pattern name (see :data:`_PATTERN_EXPONENTS`).

    Returns:
        Tuple of ``(E_eff, nu_eff)`` where both are floats.
    """
    density_fraction = float(np.clip(density_fraction, 0.0, 1.0))
    n = _PATTERN_EXPONENTS.get(pattern.lower(), _DEFAULT_EXPONENT)
    E_eff = E_bulk * (density_fraction ** n)
    nu_eff = float(nu)  # treated as constant
    return E_eff, nu_eff


def build_constitutive_matrix(E: float, nu: float) -> np.ndarray:
    """Build the 6×6 isotropic linear-elasticity constitutive matrix D.

    Relates Voigt-ordered engineering stress to Voigt-ordered engineering strain::

        {σ} = [D] {ε}

    Stress/strain ordering: [σ_xx, σ_yy, σ_zz, σ_xy, σ_yz, σ_xz].

    Args:
        E: Young's modulus in MPa (or any consistent pressure unit).
        nu: Poisson's ratio (dimensionless), must satisfy 0 < nu < 0.5.

    Returns:
        6×6 numpy ndarray of dtype float64.

    Raises:
        ValueError: If ``nu`` is outside the physically admissible range.
    """
    if not (0.0 < nu < 0.5):
        raise ValueError(
            f"Poisson's ratio nu={nu} is outside the admissible range (0, 0.5)."
        )

    lam = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))  # first Lamé parameter
    mu = E / (2.0 * (1.0 + nu))                       # shear modulus

    D = np.zeros((6, 6), dtype=np.float64)

    # Normal–normal block (top-left 3×3)
    D[0, 0] = D[1, 1] = D[2, 2] = lam + 2.0 * mu
    D[0, 1] = D[0, 2] = D[1, 0] = D[1, 2] = D[2, 0] = D[2, 1] = lam

    # Shear block (bottom-right 3×3)
    D[3, 3] = D[4, 4] = D[5, 5] = mu

    return D
