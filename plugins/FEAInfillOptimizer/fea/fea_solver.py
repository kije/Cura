# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Linear elasticity FEA solver using 4-node (linear) tetrahedral elements.

The implementation follows standard FEM formulations for linear tetrahedra:
- Strain-displacement matrix B is constant within each element.
- Element stiffness: k_e = V_e × B^T D B  (exact for linear tet).
- Global assembly via COO format, converted to CSR for solving.
- Boundary conditions applied by zeroing constrained rows/columns and setting
  diagonal to 1 (penalty-free elimination).
- System solved with scipy.sparse.linalg.spsolve (direct sparse solver).
"""

from typing import Tuple

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .homogenization import build_constitutive_matrix, build_constitutive_matrix_from_bonding
from .tetrahedralization import TetMesh


class LinearElasticitySolver:
    """Assemble and solve a linear-elasticity FEA problem on a tetrahedral mesh.

    Usage::

        solver = LinearElasticitySolver()
        K = solver.assemble_stiffness_matrix(tet_mesh, E_arr, nu_arr)
        K, f = solver.apply_boundary_conditions(K, f, fixed_nodes)
        u = solver.solve(K, f)
        vm = solver.compute_element_stress(tet_mesh, u, E_arr, nu_arr)
    """

    # ------------------------------------------------------------------
    # Stiffness assembly
    # ------------------------------------------------------------------

    def assemble_stiffness_matrix(
        self,
        tet_mesh: TetMesh,
        E_per_element: np.ndarray,
        nu_per_element: np.ndarray,
        *,
        bonding_coeff: float = 1.0,
    ) -> sp.csr_matrix:
        """Assemble the global stiffness matrix K (ndof × ndof, CSR).

        For each linear tetrahedron the element stiffness is::

            k_e = V_e × B^T × D × B        (12 × 12)

        where B is the constant strain-displacement matrix and D is the
        constitutive matrix (isotropic when ``bonding_coeff=1.0``,
        transversely isotropic otherwise).

        Degrees of freedom are ordered node-by-node: [u_x0, u_y0, u_z0,
        u_x1, u_y1, u_z1, ...].

        Args:
            tet_mesh: Tetrahedral mesh with ``nodes`` (N×3) and
                ``elements`` (M×4).
            E_per_element: Young's modulus per element, shape (M,), MPa.
            nu_per_element: Poisson's ratio per element, shape (M,).
            bonding_coeff: Layer bonding coefficient k in (0, 1].
                k=1.0 (default) uses isotropic D; k<1.0 uses the
                transversely isotropic D with Z as the weak axis.

        Returns:
            Global stiffness matrix as a scipy CSR sparse matrix.
        """
        nodes = tet_mesh.nodes         # (N, 3)
        elements = tet_mesh.elements   # (M, 4)
        n_nodes = nodes.shape[0]
        n_elems = elements.shape[0]
        n_dof = n_nodes * 3

        use_aniso = bonding_coeff < 1.0

        # Vectorized assembly: compute ALL element stiffness matrices at once
        # using numpy broadcasting. ~50-100x faster than the Python loop.

        # 1. Gather element vertex coordinates: (M, 4, 3)
        elem_nodes = nodes[elements]  # (M, 4, 3)
        x0 = elem_nodes[:, 0, :]  # (M, 3)
        x1 = elem_nodes[:, 1, :]
        x2 = elem_nodes[:, 2, :]
        x3 = elem_nodes[:, 3, :]

        # 2. Jacobian: edge vectors (M, 3, 3), rows = edges
        J = np.stack([x1 - x0, x2 - x0, x3 - x0], axis=1)  # (M, 3, 3)
        detJ = np.linalg.det(J)  # (M,)
        V = np.abs(detJ) / 6.0   # (M,)

        # Skip degenerate elements
        max_edge = np.max(np.linalg.norm(
            np.stack([x1-x0, x2-x0, x3-x0, x2-x1, x3-x1, x3-x2], axis=1),
            axis=2), axis=1)  # (M,)
        degen_threshold = (max_edge ** 3) * 1e-10
        valid = np.abs(detJ) >= degen_threshold
        valid &= V > 0

        # 3. Shape function gradients: dN/dx = dN/dxi @ J^{-1}
        # Reference gradients for linear tet (constant per element):
        # N0 = 1 - xi - eta - zeta; N1 = xi; N2 = eta; N3 = zeta
        dN_ref = np.array([
            [-1.0, -1.0, -1.0],
            [ 1.0,  0.0,  0.0],
            [ 0.0,  1.0,  0.0],
            [ 0.0,  0.0,  1.0],
        ], dtype=np.float64)  # (4, 3)

        J_inv = np.linalg.inv(J)  # (M, 3, 3)
        # dN_xyz[m, i, j] = sum_k dN_ref[i,k] * J_inv[m,k,j]
        dN_xyz = np.einsum("ik,mkj->mij", dN_ref, J_inv)  # (M, 4, 3)

        # 4. Build B matrices: (M, 6, 12)
        B_all = np.zeros((n_elems, 6, 12), dtype=np.float64)
        for i in range(4):
            col = i * 3
            bx = dN_xyz[:, i, 0]  # (M,)
            by = dN_xyz[:, i, 1]
            bz = dN_xyz[:, i, 2]
            B_all[:, 0, col + 0] = bx      # eps_xx
            B_all[:, 1, col + 1] = by      # eps_yy
            B_all[:, 2, col + 2] = bz      # eps_zz
            B_all[:, 3, col + 0] = by      # gamma_xy
            B_all[:, 3, col + 1] = bx
            B_all[:, 4, col + 1] = bz      # gamma_yz
            B_all[:, 4, col + 2] = by
            B_all[:, 5, col + 0] = bz      # gamma_xz
            B_all[:, 5, col + 2] = bx

        # 5. Build D matrices: (M, 6, 6) or (6, 6) if uniform nu
        if use_aniso:
            # Per-element D with bonding coefficient
            D_all = np.stack([
                build_constitutive_matrix_from_bonding(float(E), float(nu), bonding_coeff)
                for E, nu in zip(E_per_element, nu_per_element)
            ])  # (M, 6, 6)
        else:
            # Check if all nu are the same (common case)
            nu_unique = np.unique(nu_per_element)
            if len(nu_unique) == 1:
                # D shape depends only on nu; scale by E per element
                D_unit = build_constitutive_matrix(1.0, float(nu_unique[0]))  # (6, 6)
                D_all = E_per_element[:, np.newaxis, np.newaxis] * D_unit[np.newaxis, :, :]
            else:
                D_all = np.stack([
                    build_constitutive_matrix(float(E), float(nu))
                    for E, nu in zip(E_per_element, nu_per_element)
                ])

        # 6. Element stiffness: k_e = V * B^T @ D @ B, all at once
        # BtD = B^T @ D: (M, 12, 6) = (M, 12, 6) einsum
        BtD = np.einsum("mji,mjk->mik", B_all, D_all)  # (M, 12, 6)
        k_e_all = np.einsum("m,mij,mjk->mik", V, BtD, B_all)  # (M, 12, 12)

        # Zero out degenerate elements
        k_e_all[~valid] = 0.0

        # 7. Assemble into global sparse matrix via COO
        # DOF indices: (M, 12)
        elem_dofs = np.zeros((n_elems, 12), dtype=np.int64)
        for i in range(4):
            elem_dofs[:, i*3 + 0] = elements[:, i] * 3
            elem_dofs[:, i*3 + 1] = elements[:, i] * 3 + 1
            elem_dofs[:, i*3 + 2] = elements[:, i] * 3 + 2

        # Row/col indices: (M, 12, 12) via broadcasting
        row_idx = np.repeat(elem_dofs[:, :, np.newaxis], 12, axis=2)  # (M, 12, 12)
        col_idx = np.repeat(elem_dofs[:, np.newaxis, :], 12, axis=1)  # (M, 12, 12)

        K_coo = sp.coo_matrix(
            (k_e_all.ravel(), (row_idx.ravel(), col_idx.ravel())),
            shape=(n_dof, n_dof),
        )
        return K_coo.tocsr()

    # ------------------------------------------------------------------
    # Boundary conditions
    # ------------------------------------------------------------------

    def apply_boundary_conditions(
        self,
        K: sp.csr_matrix,
        f: np.ndarray,
        fixed_nodes: np.ndarray,
    ) -> Tuple[sp.csr_matrix, np.ndarray]:
        """Enforce zero-displacement Dirichlet BCs by direct DOF elimination.

        For each constrained DOF *d*:
        - Row *d* is zeroed and diagonal set to 1.
        - Column *d* is zeroed.
        - ``f[d]`` is set to 0.

        This preserves the symmetric positive-definite structure of K.

        Args:
            K: Global stiffness matrix (CSR), modified in-place (copy made).
            f: Global force vector, shape (ndof,).
            fixed_nodes: Node indices (0-based) whose all 3 DOFs are fixed.

        Returns:
            Tuple of (modified K, modified f).
        """
        K = K.tolil()
        f = f.copy()

        for node_idx in fixed_nodes:
            for d in range(3):
                dof = int(node_idx) * 3 + d
                K[dof, :] = 0.0
                K[:, dof] = 0.0
                K[dof, dof] = 1.0
                f[dof] = 0.0

        return K.tocsr(), f

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------

    def solve(self, K: sp.csr_matrix, f: np.ndarray) -> np.ndarray:
        """Solve the linear system K u = f for nodal displacements u.

        Args:
            K: Assembled, BC-applied stiffness matrix (CSR), shape (ndof, ndof).
            f: Force vector, shape (ndof,).

        Returns:
            Displacement vector u, shape (ndof,), same units as f/K implies.
        """
        u = spla.spsolve(K, f)
        if not np.isfinite(u).all():
            raise RuntimeError(
                "FEA solve produced non-finite displacements. "
                "Check that boundary conditions constrain all rigid-body modes "
                "and that the stiffness matrix is non-singular."
            )
        return u

    # ------------------------------------------------------------------
    # Stress computation
    # ------------------------------------------------------------------

    def compute_element_stress(
        self,
        tet_mesh: TetMesh,
        displacements: np.ndarray,
        E_per_element: np.ndarray,
        nu_per_element: np.ndarray,
        *,
        bonding_coeff: float = 1.0,
    ) -> np.ndarray:
        """Compute equivalent stress for each tetrahedral element.

        For a linear tetrahedron the strain is constant within the element::

            eps = B u_e
            sigma = D eps

        When ``bonding_coeff < 1.0`` the directional von Mises criterion is
        used, which scales Z-direction stress components by ``1/k`` before
        computing the equivalent stress.  This amplifies the contribution
        of interlayer stresses in proportion to the interlayer weakness.
        When ``bonding_coeff == 1.0`` this reduces to standard von Mises.

        Args:
            tet_mesh: Tetrahedral mesh.
            displacements: Nodal displacement vector, shape (ndof,).
            E_per_element: Young's modulus per element, shape (M,).
            nu_per_element: Poisson's ratio per element, shape (M,).
            bonding_coeff: Layer bonding coefficient k in (0, 1].

        Returns:
            Equivalent stress per element, shape (M,), same pressure unit as E.
        """
        nodes = tet_mesh.nodes
        elements = tet_mesh.elements
        n_elems = elements.shape[0]
        vm_stress = np.zeros(n_elems, dtype=np.float64)

        use_aniso = bonding_coeff < 1.0

        for e_idx, (elem, E, nu) in enumerate(
            zip(elements, E_per_element, nu_per_element)
        ):
            n0, n1, n2, n3 = int(elem[0]), int(elem[1]), int(elem[2]), int(elem[3])
            x0, x1, x2, x3 = nodes[n0], nodes[n1], nodes[n2], nodes[n3]

            B, V = _strain_displacement_matrix(x0, x1, x2, x3)
            if V <= 0.0:
                continue

            # Gather element nodal displacements (12,)
            dof_indices = np.array(
                [n0 * 3, n0 * 3 + 1, n0 * 3 + 2,
                 n1 * 3, n1 * 3 + 1, n1 * 3 + 2,
                 n2 * 3, n2 * 3 + 1, n2 * 3 + 2,
                 n3 * 3, n3 * 3 + 1, n3 * 3 + 2],
                dtype=np.int64,
            )
            u_e = displacements[dof_indices]  # (12,)

            if use_aniso:
                D = build_constitutive_matrix_from_bonding(
                    float(E), float(nu), bonding_coeff
                )
            else:
                D = build_constitutive_matrix(float(E), float(nu))
            strain = B @ u_e               # (6,)  eps = B u_e
            stress = D @ strain            # (6,)  sigma = D eps

            if use_aniso:
                vm_stress[e_idx] = _von_mises_directional(stress, bonding_coeff)
            else:
                vm_stress[e_idx] = _von_mises(stress)

        return vm_stress

    def compute_element_compliance(
        self,
        tet_mesh: TetMesh,
        displacements: np.ndarray,
        E_per_element: np.ndarray,
        nu_per_element: np.ndarray,
        *,
        bonding_coeff: float = 1.0,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Compute per-element compliance u_e^T k_e u_e and element volumes.

        For each element the compliance is computed as::

            ce_e = V_e × (B u_e)^T × D × (B u_e)
                 = V_e × strain^T × stress

        This equals the element strain energy contribution u_e^T k_e u_e
        and is the key quantity needed for SIMP topology optimization
        sensitivity analysis.

        Args:
            tet_mesh: Tetrahedral mesh.
            displacements: Nodal displacement vector, shape (ndof,).
            E_per_element: Young's modulus per element, shape (M,).
            nu_per_element: Poisson's ratio per element, shape (M,).
            bonding_coeff: Layer bonding coefficient k in (0, 1].

        Returns:
            Tuple of ``(ce, volumes)`` where:
            - ``ce``: per-element compliance, shape (M,), same energy unit
              as (force × displacement).
            - ``volumes``: per-element volume, shape (M,).
        """
        nodes = tet_mesh.nodes
        elements = tet_mesh.elements
        n_elems = elements.shape[0]
        ce = np.zeros(n_elems, dtype=np.float64)
        volumes = np.zeros(n_elems, dtype=np.float64)

        use_aniso = bonding_coeff < 1.0

        for e_idx, (elem, E, nu) in enumerate(
            zip(elements, E_per_element, nu_per_element)
        ):
            n0, n1, n2, n3 = int(elem[0]), int(elem[1]), int(elem[2]), int(elem[3])
            x0, x1, x2, x3 = nodes[n0], nodes[n1], nodes[n2], nodes[n3]

            B, V = _strain_displacement_matrix(x0, x1, x2, x3)
            if V <= 0.0:
                continue

            volumes[e_idx] = V

            dof_indices = np.array(
                [n0 * 3, n0 * 3 + 1, n0 * 3 + 2,
                 n1 * 3, n1 * 3 + 1, n1 * 3 + 2,
                 n2 * 3, n2 * 3 + 1, n2 * 3 + 2,
                 n3 * 3, n3 * 3 + 1, n3 * 3 + 2],
                dtype=np.int64,
            )
            u_e = displacements[dof_indices]

            if use_aniso:
                D = build_constitutive_matrix_from_bonding(
                    float(E), float(nu), bonding_coeff
                )
            else:
                D = build_constitutive_matrix(float(E), float(nu))

            strain = B @ u_e        # (6,)
            stress = D @ strain     # (6,)
            ce[e_idx] = V * float(strain @ stress)

        return ce, volumes

    def compute_element_failure_index(
        self,
        tet_mesh: TetMesh,
        displacements: np.ndarray,
        E_per_element: np.ndarray,
        nu_per_element: np.ndarray,
        *,
        bonding_coeff: float = 1.0,
        yield_strength: float = 50.0,
    ) -> np.ndarray:
        """Compute per-element failure index using Tsai-Hill or von Mises.

        Uses Tsai-Hill when ``bonding_coeff < 0.95`` (anisotropic material),
        and standard von Mises otherwise (isotropic).  The failure index is
        dimensionless: 0 = no load, 1 = at failure, >1 = overloaded.

        When using Tsai-Hill, multiply the returned index by ``yield_strength``
        to obtain an equivalent stress that can be fed to ``stress_to_density``
        with ``sigma_yield=yield_strength``, preserving the same mapping.

        Returns:
            Equivalent stress per element, shape (M,), in MPa.  For Tsai-Hill
            this is ``failure_index × yield_strength``; for von Mises it is the
            raw von Mises stress.
        """
        nodes = tet_mesh.nodes
        elements = tet_mesh.elements
        n_elems = elements.shape[0]
        result = np.zeros(n_elems, dtype=np.float64)

        use_tsai_hill = bonding_coeff < 0.95

        for e_idx, (elem, E, nu) in enumerate(
            zip(elements, E_per_element, nu_per_element)
        ):
            n0, n1, n2, n3 = int(elem[0]), int(elem[1]), int(elem[2]), int(elem[3])
            x0, x1, x2, x3 = nodes[n0], nodes[n1], nodes[n2], nodes[n3]

            B, V = _strain_displacement_matrix(x0, x1, x2, x3)
            if V <= 0.0:
                continue

            dof_indices = np.array(
                [n0 * 3, n0 * 3 + 1, n0 * 3 + 2,
                 n1 * 3, n1 * 3 + 1, n1 * 3 + 2,
                 n2 * 3, n2 * 3 + 1, n2 * 3 + 2,
                 n3 * 3, n3 * 3 + 1, n3 * 3 + 2],
                dtype=np.int64,
            )
            u_e = displacements[dof_indices]

            if bonding_coeff < 1.0:
                D = build_constitutive_matrix_from_bonding(
                    float(E), float(nu), bonding_coeff
                )
            else:
                D = build_constitutive_matrix(float(E), float(nu))
            strain = B @ u_e
            stress_vec = D @ strain

            if use_tsai_hill:
                fi = _tsai_hill(stress_vec, yield_strength, bonding_coeff)
                result[e_idx] = fi * yield_strength
            else:
                result[e_idx] = _von_mises(stress_vec)

        return result


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _strain_displacement_matrix(
    x0: np.ndarray,
    x1: np.ndarray,
    x2: np.ndarray,
    x3: np.ndarray,
) -> Tuple[np.ndarray, float]:
    """Compute the 6×12 strain-displacement matrix B and element volume V.

    For a 4-node linear tetrahedron with nodes x0…x3 the shape function
    gradients are constant.  The B matrix maps element nodal displacements
    (12,) to engineering strains (6,) in Voigt notation::

        ε = [ε_xx, ε_yy, ε_zz, γ_xy, γ_yz, γ_xz]^T

    The Jacobian of the isoparametric mapping from reference to physical
    coordinates is::

        J = [x1-x0, x2-x0, x3-x0]^T   (3×3)

    Args:
        x0, x1, x2, x3: Node coordinate arrays, shape (3,).

    Returns:
        Tuple of ``(B, V)`` where B is ndarray (6, 12) and V is the element
        volume (positive if node ordering is consistent).
    """
    # Jacobian: each row is (xi - x0)
    J = np.array([x1 - x0, x2 - x0, x3 - x0], dtype=np.float64)  # (3, 3)
    detJ = np.linalg.det(J)
    V = abs(detJ) / 6.0  # tet volume

    # Degenerate threshold: relative to max edge length cubed so it scales
    # correctly with the element size instead of using a fixed absolute value.
    max_edge = max(
        np.linalg.norm(x1 - x0),
        np.linalg.norm(x2 - x0),
        np.linalg.norm(x3 - x0),
        np.linalg.norm(x2 - x1),
        np.linalg.norm(x3 - x1),
        np.linalg.norm(x3 - x2),
    )
    degen_threshold = (max_edge ** 3) * 1e-10 if max_edge > 0.0 else 1e-30
    if abs(detJ) < degen_threshold:
        return np.zeros((6, 12), dtype=np.float64), 0.0

    J_inv = np.linalg.inv(J)  # (3, 3)

    # Shape function gradients in global coordinates
    # N0 = 1 - ξ - η - ζ,  N1 = ξ,  N2 = η,  N3 = ζ  (reference tet)
    # ∂N/∂[ξ,η,ζ] in reference:
    dN_ref = np.array(
        [[-1.0, -1.0, -1.0],
         [ 1.0,  0.0,  0.0],
         [ 0.0,  1.0,  0.0],
         [ 0.0,  0.0,  1.0]],
        dtype=np.float64,
    )  # (4, 3)

    # ∂N/∂[x,y,z] = ∂N/∂[ξ,η,ζ] × (J^{-T})  (using chain rule)
    # J maps [ξ,η,ζ] → [x,y,z], so ∂/∂x = J^{-T} ∂/∂ξ
    dN_xyz = dN_ref @ J_inv  # (4, 3): row i → [∂Ni/∂x, ∂Ni/∂y, ∂Ni/∂z]

    # Assemble B matrix (6, 12)
    B = np.zeros((6, 12), dtype=np.float64)
    for i in range(4):
        bx, by, bz = dN_xyz[i, 0], dN_xyz[i, 1], dN_xyz[i, 2]
        col = i * 3
        # ε_xx = ∂u/∂x
        B[0, col + 0] = bx
        # ε_yy = ∂v/∂y
        B[1, col + 1] = by
        # ε_zz = ∂w/∂z
        B[2, col + 2] = bz
        # γ_xy = ∂u/∂y + ∂v/∂x
        B[3, col + 0] = by
        B[3, col + 1] = bx
        # γ_yz = ∂v/∂z + ∂w/∂y
        B[4, col + 1] = bz
        B[4, col + 2] = by
        # γ_xz = ∂u/∂z + ∂w/∂x
        B[5, col + 0] = bz
        B[5, col + 2] = bx

    return B, V


def _tsai_hill(stress: np.ndarray, yield_strength: float, bonding_coeff: float) -> float:
    """Tsai-Hill failure index for transversely isotropic FDM parts.

    For a transversely isotropic layup with Z as the weak (interlayer) axis::

        (σ_x/X)² + (σ_z/Z)² - σ_x·σ_z/X² + (τ_xz/S)² ≤ 1

    where X = in-plane strength (yield_strength), Z = interlayer strength
    (bonding_coeff × yield_strength), S = interlayer shear ≈ 0.6 × Z.

    Returns a failure index: 0 = no load, 1 = at failure, >1 = overloaded.
    """
    sx, sy, sz, txy, tyz, txz = stress

    X = yield_strength
    Z = bonding_coeff * yield_strength
    S = 0.6 * Z

    if X <= 0.0 or Z <= 0.0 or S <= 0.0:
        return 0.0

    # In-plane stress resultant (XY plane is isotropic)
    # Use combined in-plane stress for the X-direction terms
    sigma_ip = float(np.sqrt(max(sx**2 + sy**2 - sx * sy + 3.0 * txy**2, 0.0)))

    # Out-of-plane shear resultant
    tau_op = float(np.sqrt(tyz**2 + txz**2))

    fi2 = (sigma_ip / X) ** 2 + (sz / Z) ** 2 - sigma_ip * sz / (X ** 2) + (tau_op / S) ** 2
    return float(np.sqrt(max(fi2, 0.0)))


def _von_mises(stress: np.ndarray) -> float:
    """Compute von Mises stress from a Voigt stress vector.

    Args:
        stress: Stress vector [sigma_xx, sigma_yy, sigma_zz, tau_xy, tau_yz, tau_xz],
            shape (6,).

    Returns:
        Von Mises equivalent stress (scalar, same unit as input).
    """
    sx, sy, sz, txy, tyz, txz = stress
    vm2 = (
        sx**2 + sy**2 + sz**2
        - sx * sy - sy * sz - sx * sz
        + 3.0 * (txy**2 + tyz**2 + txz**2)
    )
    return float(np.sqrt(max(vm2, 0.0)))


def _von_mises_directional(stress: np.ndarray, k: float) -> float:
    """Directionally weighted von Mises for transversely isotropic material.

    Scales Z-direction stress components by ``1/k`` to account for interlayer
    weakness before computing the equivalent stress.  This amplifies the
    contribution of interlayer (Z) stresses in proportion to the interlayer
    weakness, recovering standard von Mises when k=1.

    See transverse_isotropy_report.md Section 7.4-7.6.

    Args:
        stress: Voigt stress vector [sigma_xx, sigma_yy, sigma_zz,
            tau_xy, tau_yz, tau_xz], shape (6,).
        k: Layer bonding coefficient in (0, 1].

    Returns:
        Directional von Mises equivalent stress (scalar, same unit as input).
    """
    sx, sy, sz, txy, tyz, txz = stress

    # Scale Z-direction components by strength ratio 1/k
    sz_eff = sz / k
    tyz_eff = tyz / k
    txz_eff = txz / k

    vm2 = (
        sx**2 + sy**2 + sz_eff**2
        - sx * sy - sy * sz_eff - sx * sz_eff
        + 3.0 * (txy**2 + tyz_eff**2 + txz_eff**2)
    )
    return float(np.sqrt(max(vm2, 0.0)))
