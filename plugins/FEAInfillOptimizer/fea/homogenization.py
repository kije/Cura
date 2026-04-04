# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Homogenization: compute effective elastic properties for infill regions.

Uses a power-law model to relate infill density fraction to effective stiffness.
Note: the exponents below are *infill homogenization exponents* specific to each
infill geometry — they are NOT the SIMP (Solid Isotropic Material with
Penalization) topology-optimisation penalty, which is a separate concept.

    E_eff = E_bulk × density_fraction^n

where *n* (the homogenization exponent) depends on the infill pattern and its
dominant deformation mechanism (stretching- vs. bending-dominated).

Exponent sources
----------------
- **lines** (n=1.0): Stretching-dominated; uniaxial rule-of-mixtures limit.
- **grid** (n=2.0): Bending-dominated open-cell lattice; Gibson & Ashby (1997)
  *Cellular Solids*, Cambridge University Press; validated experimentally by
  Fernandez-Vicente et al. (2016) *Rapid Prototyping Journal* 22(5).
- **triangles** (n=1.3): Triangulated lattice, near-stretching-dominated;
  intermediate between lines and grid per Gibson-Ashby theory.
- **gyroid** (n=1.6): Triply periodic minimal surface (TPMS); Al-Ketan et al.
  (2018) *Advanced Engineering Materials* 20(2), Table 2.
- **cubic** (n=2.0): BCC-type bending-dominated; Maskery et al. (2018)
  *Additive Manufacturing* 19, pp. 1–11.
- **honeycomb** (n=2.3): In-plane bending-dominated hexagonal cell; Gibson &
  Ashby (1997); Fernandez-Vicente et al. (2016) experimental data.
"""

import math
from typing import Tuple

import numpy as np

# Homogenization exponents per infill pattern.
# Higher n → stronger contrast between dense and sparse regions (more aggressive
# material redistribution).
# Values are calibrated to Gibson-Ashby theory and published experimental data;
# see module docstring for full literature references.
_PATTERN_EXPONENTS: dict[str, float] = {
    "lines": 1.0,      # stretching-dominated (rule-of-mixtures)
    "grid": 2.0,       # bending-dominated; Gibson-Ashby / Fernandez-Vicente 2016
    "triangles": 1.3,  # near-stretching-dominated triangulated lattice
    "gyroid": 1.6,     # TPMS; Al-Ketan et al. 2018
    "cubic": 2.0,      # BCC bending-dominated; Maskery 2018
    "honeycomb": 2.3,  # bending-dominated hexagonal; Gibson-Ashby / Fernandez-Vicente 2016
}

_DEFAULT_EXPONENT = 1.3  # fallback for unknown patterns


def effective_properties(
    E_bulk: float,
    nu: float,
    density_fraction: float,
    pattern: str,
) -> Tuple[float, float]:
    """Compute effective Young's modulus and Poisson's ratio for an infill region.

    Uses a power-law (infill homogenization) model::

        E_eff = E_bulk × density_fraction^n

    where *n* is a pattern-specific homogenization exponent (not the SIMP
    topology-optimisation penalty).  Poisson's ratio is treated as approximately
    constant with density (a common assumption for moderate density ranges).

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
        nu: Poisson's ratio (dimensionless), must satisfy -1 < nu < 0.5.
            Values > 0.45 risk volumetric locking with linear tetrahedra.

    Returns:
        6×6 numpy ndarray of dtype float64.

    Raises:
        ValueError: If ``nu`` is outside the physically admissible range.
    """
    import warnings
    if not (-1.0 < nu < 0.5):
        raise ValueError(
            f"Poisson's ratio nu={nu} is outside the admissible range (-1, 0.5)."
        )
    if nu > 0.45:
        warnings.warn(
            f"Poisson's ratio nu={nu} > 0.45 risks volumetric locking with linear "
            "tetrahedral elements. Consider using a reduced integration scheme.",
            UserWarning,
            stacklevel=2,
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


def build_constitutive_matrix_transverse(
    E_p: float,
    E_t: float,
    nu_p: float,
    nu_tp: float,
    G_t: float,
) -> np.ndarray:
    """Build the 6x6 transversely isotropic constitutive matrix D.

    Symmetry axis = Z (build direction).
    Voigt ordering: [sigma_xx, sigma_yy, sigma_zz, tau_xy, tau_yz, tau_xz].

    Five independent constants:

    Args:
        E_p: In-plane Young's modulus (E_x = E_y) in MPa.
        E_t: Transverse Young's modulus (E_z) in MPa.
        nu_p: In-plane Poisson's ratio (nu_xy), dimensionless.
        nu_tp: Transverse-to-in-plane Poisson's ratio (nu_zx = nu_zy).
        G_t: Transverse shear modulus (G_xz = G_yz) in MPa.

    Returns:
        6x6 numpy ndarray of dtype float64.

    Raises:
        ValueError: If elastic constants are thermodynamically inadmissible
            (DELTA <= 0, or non-positive moduli).
    """
    if E_p <= 0.0 or E_t <= 0.0 or G_t <= 0.0:
        raise ValueError(
            f"All moduli must be positive: E_p={E_p}, E_t={E_t}, G_t={G_t}."
        )

    # Reciprocal relation: nu_pt / E_p = nu_tp / E_t
    nu_pt = nu_tp * E_p / E_t

    # Thermodynamic admissibility: DELTA must be positive for D to be
    # positive-definite (see transverse_isotropy_report.md Section 2.7).
    DELTA = 1.0 - nu_p - 2.0 * nu_pt * nu_tp
    if DELTA <= 0.0:
        raise ValueError(
            f"Inadmissible elastic constants: DELTA = {DELTA:.6f} <= 0. "
            f"Requires 1 - nu_p - 2*nu_pt*nu_tp > 0 "
            f"(nu_p={nu_p}, nu_pt={nu_pt:.4f}, nu_tp={nu_tp:.4f})."
        )

    # In-plane shear modulus (derived, not independent)
    G_p = E_p / (2.0 * (1.0 + nu_p))

    # Normal block entries (analytical inverse of 3x3 compliance block;
    # see transverse_isotropy_report.md Section 2.3 for full derivation).
    denom = (1.0 + nu_p) * DELTA

    D_11 = E_p * (1.0 - nu_pt * nu_tp) / denom
    D_12 = E_p * (nu_p + nu_pt * nu_tp) / denom
    D_13 = E_t * nu_pt / DELTA          # = E_p * nu_tp / DELTA
    D_33 = E_t * (1.0 - nu_p) / DELTA

    # Assemble 6x6
    D = np.zeros((6, 6), dtype=np.float64)

    # Normal-normal block
    D[0, 0] = D_11;  D[0, 1] = D_12;  D[0, 2] = D_13
    D[1, 0] = D_12;  D[1, 1] = D_11;  D[1, 2] = D_13
    D[2, 0] = D_13;  D[2, 1] = D_13;  D[2, 2] = D_33

    # Shear block
    D[3, 3] = G_p    # in-plane shear (tau_xy)
    D[4, 4] = G_t    # transverse shear (tau_yz)
    D[5, 5] = G_t    # transverse shear (tau_xz)

    return D


def build_constitutive_matrix_from_bonding(
    E_p: float,
    nu_p: float,
    k: float,
) -> np.ndarray:
    """Build transversely isotropic D from in-plane properties + bonding coeff.

    Uses the sqrt-scaling parameterisation for Poisson's ratios which
    guarantees thermodynamic admissibility for all nu_p < 0.5 regardless
    of k (see transverse_isotropy_report.md Section 3.3).

    Parameterisation::

        E_t   = k * E_p
        G_t   = k * G_p       where G_p = E_p / (2*(1+nu_p))
        nu_tp = nu_p * sqrt(k)

    When k = 1 this recovers the isotropic constitutive matrix exactly.

    Args:
        E_p: In-plane Young's modulus in MPa.
        nu_p: In-plane Poisson's ratio (dimensionless).
        k: Layer bonding coefficient in (0, 1].
           k=1: perfect bonding (isotropic).
           k->0: no interlayer bonding (very weak Z direction).

    Returns:
        6x6 numpy ndarray of dtype float64.

    Raises:
        ValueError: If ``k`` is outside (0, 1].
    """
    if not (0.0 < k <= 1.0):
        raise ValueError(f"Bonding coefficient k={k} must be in (0, 1].")

    E_t = k * E_p
    G_p = E_p / (2.0 * (1.0 + nu_p))
    G_t = k * G_p
    nu_tp = nu_p * math.sqrt(k)

    return build_constitutive_matrix_transverse(E_p, E_t, nu_p, nu_tp, G_t)
