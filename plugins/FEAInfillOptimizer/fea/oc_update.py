# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""SIMP Optimality Criteria (OC) density update for topology optimization.

Implements the standard OC method from Sigmund (2001) "A 99 line topology
optimization code written in Matlab", *Structural and Multidisciplinary
Optimization* 21(2), pp. 120-127.

The SIMP (Solid Isotropic Material with Penalization) approach models
effective element stiffness as::

    E_e = rho_e^p * E_0

where ``p`` is the penalization exponent (typically 3) and ``rho_e`` is the
element density variable.

Compliance sensitivity
----------------------
For a linear elastic system with compliance objective C = f^T u = u^T K u,
the sensitivity of compliance with respect to element density is::

    dC/drho_e = -p * rho_e^(p-1) * u_e^T * k0_e * u_e

where ``u_e`` is the element displacement vector and ``k0_e`` is the
element stiffness matrix at unit density (before SIMP scaling).

OC update rule
--------------
The density is updated using a fixed-point iteration with a Lagrange
multiplier ``lambda`` enforcing the volume constraint::

    B_e = max(0, -dC/drho_e) / (lambda * V_e)
    rho_new_e = rho_e * B_e^eta

with a move limit applied::

    rho_new_e = max(rho_min, max(rho_e - move,
                min(rho_max, min(rho_e + move, rho_new_e))))

The Lagrange multiplier is found via bisection such that the volume
constraint is satisfied::

    sum(rho_new_e * V_e) = volume_fraction * sum(V_e)
"""

import numpy as np

from .homogenization import build_constitutive_matrix, build_constitutive_matrix_from_bonding
from .tetrahedralization import TetMesh


def _compute_element_stiffness_and_compliance(
    tet_mesh: TetMesh,
    displacements: np.ndarray,
    E_base: float,
    nu: float,
    bonding_coeff: float = 1.0,
) -> tuple:
    """Compute per-element compliance u_e^T k0_e u_e and element volumes.

    Computes the element-level strain energy using the *base* (unit-density)
    stiffness matrix k0_e, which is needed for the SIMP sensitivity
    calculation.

    Args:
        tet_mesh: Tetrahedral mesh.
        displacements: Global displacement vector, shape (n_dof,).
        E_base: Base Young's modulus (at full density), MPa.
        nu: Poisson's ratio.
        bonding_coeff: Layer bonding coefficient in (0, 1].

    Returns:
        Tuple of ``(ce, volumes)`` where:
        - ``ce``: per-element compliance u_e^T k0_e u_e, shape (n_elems,).
        - ``volumes``: per-element volume, shape (n_elems,).
    """
    from .fea_solver import _strain_displacement_matrix

    nodes = tet_mesh.nodes
    elements = tet_mesh.elements
    n_elems = elements.shape[0]

    use_aniso = bonding_coeff < 1.0
    if use_aniso:
        D0 = build_constitutive_matrix_from_bonding(E_base, nu, bonding_coeff)
    else:
        D0 = build_constitutive_matrix(E_base, nu)

    ce = np.zeros(n_elems, dtype=np.float64)
    volumes = np.zeros(n_elems, dtype=np.float64)

    for e_idx in range(n_elems):
        elem = elements[e_idx]
        n0, n1, n2, n3 = int(elem[0]), int(elem[1]), int(elem[2]), int(elem[3])
        x0, x1, x2, x3 = nodes[n0], nodes[n1], nodes[n2], nodes[n3]

        B, V = _strain_displacement_matrix(x0, x1, x2, x3)
        if V <= 0.0:
            continue

        volumes[e_idx] = V

        # Gather element nodal displacements (12,)
        dof_indices = np.array(
            [n0 * 3, n0 * 3 + 1, n0 * 3 + 2,
             n1 * 3, n1 * 3 + 1, n1 * 3 + 2,
             n2 * 3, n2 * 3 + 1, n2 * 3 + 2,
             n3 * 3, n3 * 3 + 1, n3 * 3 + 2],
            dtype=np.int64,
        )
        u_e = displacements[dof_indices]  # (12,)

        # Element stiffness at base (unit-density) material: k0_e = V * B^T D0 B
        # Element compliance: ce_e = u_e^T k0_e u_e = V * u_e^T B^T D0 B u_e
        strain = B @ u_e  # (6,)
        stress = D0 @ strain  # (6,)
        # ce_e = V * strain^T * stress = V * u_e^T B^T D B u_e
        ce[e_idx] = V * float(strain @ stress)

    return ce, volumes


def oc_density_update(
    density: np.ndarray,
    tet_mesh: TetMesh,
    displacements: np.ndarray,
    E_base: float,
    nu: float,
    n_exp: float,
    rho_min: float,
    rho_max: float,
    volume_fraction: float,
    move_limit: float = 0.2,
    eta: float = 0.5,
    bonding_coeff: float = 1.0,
) -> np.ndarray:
    """Update element densities using the SIMP Optimality Criteria method.

    This implements the standard OC update from Sigmund (2001) with bisection
    on the Lagrange multiplier to satisfy the volume constraint exactly.

    The compliance sensitivity for SIMP is::

        dC/drho_e = -p * rho_e^(p-1) * u_e^T * k0_e * u_e

    where ``p`` is the SIMP penalization exponent (``n_exp``), ``k0_e`` is
    the element stiffness at unit density, and ``u_e`` is the element
    displacement vector.

    The OC update rule with move limits is::

        B_e = (-dC/drho_e) / (lambda * V_e)
        rho_new = max(rho_min, max(rho - move,
                  min(rho_max, min(rho + move, rho * B_e^eta))))

    where ``lambda`` is found by bisection so that::

        sum(rho_new * V_e) = volume_fraction * sum(V_e)

    Args:
        density: Current element densities, shape (n_elems,).
        tet_mesh: Tetrahedral mesh (nodes, elements).
        displacements: Global displacement vector from FEA solve, shape (n_dof,).
        E_base: Base (solid) Young's modulus in MPa.
        nu: Poisson's ratio.
        n_exp: SIMP penalization exponent p (typically 3 for topology
            optimization; here we reuse the infill pattern exponent).
        rho_min: Minimum allowed density (e.g. 0.10).
        rho_max: Maximum allowed density (e.g. 0.80).
        volume_fraction: Target volume fraction in (0, 1]. The OC update
            enforces ``sum(rho * V) = volume_fraction * sum(V)``.
        move_limit: Maximum density change per iteration (default 0.2).
            Larger values allow faster convergence but may cause
            oscillation; smaller values are more stable.
        eta: Damping exponent for the OC update (default 0.5).
            The standard value from Sigmund (2001) is 0.5.
        bonding_coeff: Layer bonding coefficient in (0, 1] for anisotropic
            constitutive matrix. Default 1.0 (isotropic).

    Returns:
        Updated density array, shape (n_elems,), values in [rho_min, rho_max].
    """
    n_elems = len(density)
    density = np.copy(density)

    # Compute per-element compliance and volumes
    ce, volumes = _compute_element_stiffness_and_compliance(
        tet_mesh, displacements, E_base, nu, bonding_coeff
    )

    # Compliance sensitivity: dC/drho_e = -p * rho_e^(p-1) * ce_e
    # For the OC update we need -dC/drho_e = p * rho_e^(p-1) * ce_e
    # (always non-negative since ce >= 0 and rho >= rho_min > 0)
    dc = n_exp * np.power(np.maximum(density, rho_min), n_exp - 1.0) * ce

    # Handle zero-volume elements (degenerate tets)
    valid = volumes > 0.0
    if not np.any(valid):
        return density

    total_volume = float(np.sum(volumes[valid]))
    target_volume = volume_fraction * total_volume

    # Bisection to find Lagrange multiplier lambda
    lam_lo = 1e-9
    lam_hi = 1e9

    for _ in range(100):  # bisection iterations (converges in ~30-40)
        lam_mid = 0.5 * (lam_lo + lam_hi)

        # OC update: B_e = dc_e / (lam * V_e), rho_new = rho * B^eta
        # Protect against division by zero for degenerate elements
        B = np.where(valid, dc / (lam_mid * np.maximum(volumes, 1e-30)), 0.0)
        rho_candidate = density * np.power(np.maximum(B, 1e-30), eta)

        # Apply move limits and density bounds
        rho_new = np.maximum(
            rho_min,
            np.maximum(
                density - move_limit,
                np.minimum(
                    rho_max,
                    np.minimum(density + move_limit, rho_candidate)
                )
            )
        )

        # Check volume constraint
        current_volume = float(np.sum(rho_new[valid] * volumes[valid]))
        if current_volume > target_volume:
            lam_lo = lam_mid
        else:
            lam_hi = lam_mid

        # Convergence check on lambda
        if (lam_hi - lam_lo) / max(lam_mid, 1e-30) < 1e-6:
            break

    return rho_new
