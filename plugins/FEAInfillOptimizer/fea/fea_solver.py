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

    def __init__(self) -> None:
        self._B_cache_key = None
        self._B_all = None
        self._V_all = None
        self._valid = None

    def _get_cached_B(self, tet_mesh: TetMesh):
        """Return cached (B_all, V_all, valid) for tet_mesh, recomputing on mesh change."""
        key = id(tet_mesh)
        if self._B_cache_key != key:
            self._B_all, self._V_all, self._valid = _strain_displacement_matrices_vectorized(tet_mesh)
            self._B_cache_key = key
        return self._B_all, self._V_all, self._valid

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

        # Get B matrices, volumes, and validity mask (cached per mesh identity)
        B_all, V, valid = self._get_cached_B(tet_mesh)

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
        Uses vectorized CSR operations instead of per-DOF LIL zeroing,
        which is O(nnz) total instead of O(n_fixed × ndof).

        Args:
            K: Global stiffness matrix (CSR), modified in-place (copy made).
            f: Global force vector, shape (ndof,).
            fixed_nodes: Node indices (0-based) whose all 3 DOFs are fixed.

        Returns:
            Tuple of (modified K, modified f).
        """
        import time as _time
        _t0 = _time.monotonic()

        f = f.copy()

        # Build fixed DOF set: each node has 3 DOFs
        fixed_dofs = np.repeat(fixed_nodes.astype(np.int64) * 3, 3) + np.tile(
            np.arange(3, dtype=np.int64), len(fixed_nodes)
        )

        # Zero force for fixed DOFs
        f[fixed_dofs] = 0.0

        # Vectorized CSR BC application:
        # 1. Zero rows for fixed DOFs (fast: CSR row slicing is O(nnz_row))
        # 2. Zero columns by exploiting symmetry: K = K^T, so also zero
        #    rows in K^T, then transpose back.
        # 3. Set diagonal to 1.

        K = K.copy().tocsr()

        # Create a boolean mask for fixed DOFs
        n_dof = K.shape[0]
        is_fixed = np.zeros(n_dof, dtype=bool)
        is_fixed[fixed_dofs] = True

        # Zero rows: build boolean mask over K.data for all entries in fixed rows.
        # For CSR, entries for row i are K.data[indptr[i]:indptr[i+1]].
        # Vectorized via a row-label array: assign each data entry its row index,
        # then mask entries whose row is in the fixed set.
        # np.repeat(row_indices, row_lengths) creates the row label per data entry.
        row_lengths = np.diff(K.indptr)  # (ndof,)
        data_row_labels = np.repeat(np.arange(n_dof), row_lengths)  # (nnz,)
        row_data_mask = is_fixed[data_row_labels]
        K.data[row_data_mask] = 0.0

        # Zero columns: multiply each entry K[i,j] by 0 if j is fixed
        col_mask = is_fixed[K.indices]
        K.data[col_mask] = 0.0

        # Set diagonal to 1 for fixed DOFs — use diagonal vector directly
        # instead of tolil() conversion which is extremely slow for large matrices
        K.eliminate_zeros()
        diag = K.diagonal()
        diag[fixed_dofs] = 1.0
        K.setdiag(diag)

        _t1 = _time.monotonic()
        from UM.Logger import Logger
        Logger.log("d", "FEA apply_boundary_conditions: vectorized %.3fs (%d fixed DOFs)",
                   _t1 - _t0, len(fixed_dofs))

        return K, f

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------

    def solve(self, K: sp.csr_matrix, f: np.ndarray) -> np.ndarray:
        """Solve the linear system K u = f for nodal displacements u.

        Uses spsolve (SuperLU) with a 30s timeout. Falls back to CG
        iterative solver if SuperLU stalls on ill-conditioned matrices
        (common in SIMP optimization with extreme density ratios).

        Args:
            K: Assembled, BC-applied stiffness matrix (CSR), shape (ndof, ndof).
            f: Force vector, shape (ndof,).

        Returns:
            Displacement vector u, shape (ndof,), same units as f/K implies.
        """
        import threading
        from UM.Logger import Logger

        result = [None]
        exc = [None]

        def _direct():
            try:
                result[0] = spla.spsolve(K, f)
            except Exception as e:
                exc[0] = e

        # spsolve with timeout — SuperLU can hang on ill-conditioned K
        t = threading.Thread(target=_direct, daemon=True)
        t.start()
        t.join(timeout=30.0)

        if t.is_alive() or result[0] is None or exc[0] is not None:
            reason = "timeout (30s)" if t.is_alive() else str(exc[0])
            Logger.log("w", "FEA solve: spsolve %s, falling back to CG iterative solver", reason)

            # CPython cannot kill daemon threads.  The zombie spsolve thread
            # may keep references to the large K and f arrays.  Copy the
            # inputs for CG so the zombie doesn't pin shared memory.
            K_cg = K.copy()
            f_cg = f.copy()

            # CG with Jacobi preconditioner + timeout thread
            # CG can also hang on near-singular matrices, so we wrap it too
            cg_result = [None]
            cg_info = [None]

            def _cg_solve():
                diag = K_cg.diagonal().copy()
                diag[diag == 0] = 1.0
                M_inv = sp.diags(1.0 / diag)
                cg_result[0], cg_info[0] = spla.cg(
                    K_cg, f_cg, M=M_inv, tol=1e-6, maxiter=2000
                )

            cg_thread = threading.Thread(target=_cg_solve, daemon=True)
            cg_thread.start()
            cg_thread.join(timeout=30.0)

            if cg_thread.is_alive() or cg_result[0] is None:
                Logger.log("e", "FEA solve: CG also timed out (30s). Matrix may be singular. "
                           "Check that boundary conditions fully constrain the model.")
                # Return zero displacements — caller will see zero stress
                u = np.zeros(f.shape[0], dtype=np.float64)
            else:
                u = cg_result[0]
                if cg_info[0] != 0:
                    Logger.log("w", "FEA solve: CG info=%d (partial convergence)", cg_info[0])
        else:
            u = result[0]

        if not np.isfinite(u).all():
            Logger.log("e", "FEA solve: non-finite displacements detected — returning zeros")
            u = np.zeros_like(u)
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
        import time as _time
        _t0 = _time.monotonic()

        use_aniso = bonding_coeff < 1.0

        # Get B matrices and volumes (cached per mesh identity)
        B_all, V_all, valid = self._get_cached_B(tet_mesh)

        # Build D matrices (M, 6, 6) — reuses same logic as assemble_stiffness_matrix
        D_all = _build_D_matrices(E_per_element, nu_per_element, bonding_coeff)

        # Gather element displacements: (M, 12)
        u_e_all = _gather_element_displacements(tet_mesh.elements, displacements)

        # Batched strain: strain = B @ u_e → (M, 6) = einsum("mij,mj->mi")
        strain_all = np.einsum("mij,mj->mi", B_all, u_e_all)  # (M, 6)

        # Batched stress: stress = D @ strain → (M, 6) = einsum("mij,mj->mi")
        stress_all = np.einsum("mij,mj->mi", D_all, strain_all)  # (M, 6)

        # Vectorized von Mises (or directional von Mises)
        if use_aniso:
            vm_stress = _von_mises_directional_vectorized(stress_all, bonding_coeff)
        else:
            vm_stress = _von_mises_vectorized(stress_all)

        # Zero out degenerate elements
        vm_stress[~valid] = 0.0

        _t1 = _time.monotonic()
        from UM.Logger import Logger
        Logger.log("d", "FEA compute_element_stress: vectorized %.3fs (%d elems)",
                   _t1 - _t0, len(vm_stress))

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
        import time as _time
        _t0 = _time.monotonic()

        # Get B matrices and volumes (cached per mesh identity)
        B_all, V_all, valid = self._get_cached_B(tet_mesh)

        # Build D matrices (M, 6, 6)
        D_all = _build_D_matrices(E_per_element, nu_per_element, bonding_coeff)

        # Gather element displacements: (M, 12)
        u_e_all = _gather_element_displacements(tet_mesh.elements, displacements)

        # Batched strain and stress
        strain_all = np.einsum("mij,mj->mi", B_all, u_e_all)  # (M, 6)
        stress_all = np.einsum("mij,mj->mi", D_all, strain_all)  # (M, 6)

        # Compliance: ce = V * strain^T @ stress (per-element dot product)
        # Equivalent to: ce[m] = V[m] * sum(strain[m,i] * stress[m,i])
        ce = V_all * np.einsum("mi,mi->m", strain_all, stress_all)
        ce[~valid] = 0.0

        volumes = V_all.copy()
        volumes[~valid] = 0.0

        _t1 = _time.monotonic()
        from UM.Logger import Logger
        Logger.log("d", "FEA compute_element_compliance: vectorized %.3fs (%d elems)",
                   _t1 - _t0, len(ce))

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
        import time as _time
        _t0 = _time.monotonic()

        use_tsai_hill = bonding_coeff < 0.95

        # Get B matrices and volumes (cached per mesh identity)
        B_all, V_all, valid = self._get_cached_B(tet_mesh)

        # Build D matrices (M, 6, 6)
        D_all = _build_D_matrices(E_per_element, nu_per_element, bonding_coeff)

        # Gather element displacements: (M, 12)
        u_e_all = _gather_element_displacements(tet_mesh.elements, displacements)

        # Batched strain and stress
        strain_all = np.einsum("mij,mj->mi", B_all, u_e_all)  # (M, 6)
        stress_all = np.einsum("mij,mj->mi", D_all, strain_all)  # (M, 6)

        # Vectorized failure criterion
        if use_tsai_hill:
            fi = _tsai_hill_vectorized(stress_all, yield_strength, bonding_coeff)
            result = fi * yield_strength
        else:
            result = _von_mises_vectorized(stress_all)

        # Zero out degenerate elements
        result[~valid] = 0.0

        _t1 = _time.monotonic()
        from UM.Logger import Logger
        Logger.log("d", "FEA compute_element_failure_index: vectorized %.3fs (%d elems)",
                   _t1 - _t0, len(result))

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


def _von_mises_vectorized(stress_all: np.ndarray) -> np.ndarray:
    """Vectorized von Mises stress for all elements at once.

    Mathematically equivalent to calling _von_mises() per element, but
    operates on the full (M, 6) stress array with numpy broadcasting.

    Args:
        stress_all: Stress vectors, shape (M, 6), Voigt ordering.

    Returns:
        Von Mises stress per element, shape (M,).
    """
    sx = stress_all[:, 0]
    sy = stress_all[:, 1]
    sz = stress_all[:, 2]
    txy = stress_all[:, 3]
    tyz = stress_all[:, 4]
    txz = stress_all[:, 5]
    vm2 = (
        sx**2 + sy**2 + sz**2
        - sx * sy - sy * sz - sx * sz
        + 3.0 * (txy**2 + tyz**2 + txz**2)
    )
    return np.sqrt(np.maximum(vm2, 0.0))


def _von_mises_directional_vectorized(stress_all: np.ndarray, k: float) -> np.ndarray:
    """Vectorized directional von Mises for all elements at once.

    Mathematically equivalent to calling _von_mises_directional() per element.
    Scales Z-direction stress components by 1/k before computing.

    Args:
        stress_all: Stress vectors, shape (M, 6), Voigt ordering.
        k: Layer bonding coefficient in (0, 1].

    Returns:
        Directional von Mises stress per element, shape (M,).
    """
    sx = stress_all[:, 0]
    sy = stress_all[:, 1]
    sz_eff = stress_all[:, 2] / k
    txy = stress_all[:, 3]
    tyz_eff = stress_all[:, 4] / k
    txz_eff = stress_all[:, 5] / k
    vm2 = (
        sx**2 + sy**2 + sz_eff**2
        - sx * sy - sy * sz_eff - sx * sz_eff
        + 3.0 * (txy**2 + tyz_eff**2 + txz_eff**2)
    )
    return np.sqrt(np.maximum(vm2, 0.0))


def _tsai_hill_vectorized(
    stress_all: np.ndarray, yield_strength: float, bonding_coeff: float
) -> np.ndarray:
    """Vectorized Tsai-Hill failure index for all elements at once.

    Mathematically equivalent to calling _tsai_hill() per element.

    Args:
        stress_all: Stress vectors, shape (M, 6), Voigt ordering.
        yield_strength: In-plane yield strength X.
        bonding_coeff: Bonding coefficient k.

    Returns:
        Failure index per element, shape (M,).
    """
    X = yield_strength
    Z = bonding_coeff * yield_strength
    S = 0.6 * Z

    if X <= 0.0 or Z <= 0.0 or S <= 0.0:
        return np.zeros(stress_all.shape[0], dtype=np.float64)

    sx = stress_all[:, 0]
    sy = stress_all[:, 1]
    sz = stress_all[:, 2]
    txy = stress_all[:, 3]
    tyz = stress_all[:, 4]
    txz = stress_all[:, 5]

    # In-plane stress resultant
    sigma_ip = np.sqrt(np.maximum(sx**2 + sy**2 - sx * sy + 3.0 * txy**2, 0.0))

    # Out-of-plane shear resultant
    tau_op = np.sqrt(tyz**2 + txz**2)

    fi2 = (sigma_ip / X) ** 2 + (sz / Z) ** 2 - sigma_ip * sz / (X ** 2) + (tau_op / S) ** 2
    return np.sqrt(np.maximum(fi2, 0.0))


def _strain_displacement_matrices_vectorized(
    tet_mesh: TetMesh,
) -> tuple:
    """Compute B matrices and volumes for ALL elements at once.

    Reuses the same vectorized Jacobian/B-matrix computation from
    assemble_stiffness_matrix, avoiding per-element Python calls to
    _strain_displacement_matrix().

    Args:
        tet_mesh: Tetrahedral mesh.

    Returns:
        Tuple of ``(B_all, V_all, valid)`` where:
        - ``B_all``: (M, 6, 12) strain-displacement matrices.
        - ``V_all``: (M,) element volumes.
        - ``valid``: (M,) boolean mask of non-degenerate elements.
    """
    nodes = tet_mesh.nodes     # (N, 3)
    elements = tet_mesh.elements  # (M, 4)
    n_elems = elements.shape[0]

    # Gather element vertex coordinates: (M, 4, 3)
    elem_nodes = nodes[elements]
    x0 = elem_nodes[:, 0, :]
    x1 = elem_nodes[:, 1, :]
    x2 = elem_nodes[:, 2, :]
    x3 = elem_nodes[:, 3, :]

    # Jacobian: (M, 3, 3)
    J = np.stack([x1 - x0, x2 - x0, x3 - x0], axis=1)
    detJ = np.linalg.det(J)
    V = np.abs(detJ) / 6.0

    # Degenerate element check (same as assemble_stiffness_matrix)
    max_edge = np.max(np.linalg.norm(
        np.stack([x1-x0, x2-x0, x3-x0, x2-x1, x3-x1, x3-x2], axis=1),
        axis=2), axis=1)
    degen_threshold = (max_edge ** 3) * 1e-10
    valid = np.abs(detJ) >= degen_threshold
    valid &= V > 0

    # Shape function gradients
    dN_ref = np.array([
        [-1.0, -1.0, -1.0],
        [ 1.0,  0.0,  0.0],
        [ 0.0,  1.0,  0.0],
        [ 0.0,  0.0,  1.0],
    ], dtype=np.float64)

    # For degenerate elements, use identity Jacobian to avoid singular inverse
    J_safe = J.copy()
    J_safe[~valid] = np.eye(3)
    J_inv = np.linalg.inv(J_safe)
    dN_xyz = np.einsum("ik,mkj->mij", dN_ref, J_inv)  # (M, 4, 3)

    # Build B matrices: (M, 6, 12)
    B_all = np.zeros((n_elems, 6, 12), dtype=np.float64)
    for i in range(4):
        col = i * 3
        bx = dN_xyz[:, i, 0]
        by = dN_xyz[:, i, 1]
        bz = dN_xyz[:, i, 2]
        B_all[:, 0, col + 0] = bx
        B_all[:, 1, col + 1] = by
        B_all[:, 2, col + 2] = bz
        B_all[:, 3, col + 0] = by
        B_all[:, 3, col + 1] = bx
        B_all[:, 4, col + 1] = bz
        B_all[:, 4, col + 2] = by
        B_all[:, 5, col + 0] = bz
        B_all[:, 5, col + 2] = bx

    # Zero out B for degenerate elements
    B_all[~valid] = 0.0

    return B_all, V, valid


def _build_D_matrices(
    E_per_element: np.ndarray,
    nu_per_element: np.ndarray,
    bonding_coeff: float,
) -> np.ndarray:
    """Build constitutive matrices for all elements.

    For the common isotropic case with uniform nu, uses the efficient
    scaling approach: D = E[:, None, None] * D_unit.

    Args:
        E_per_element: Young's modulus per element, shape (M,).
        nu_per_element: Poisson's ratio per element, shape (M,).
        bonding_coeff: Layer bonding coefficient.

    Returns:
        (M, 6, 6) array of constitutive matrices.
    """
    use_aniso = bonding_coeff < 1.0

    if use_aniso:
        # Per-element D with bonding coefficient — check if nu is uniform
        nu_unique = np.unique(nu_per_element)
        if len(nu_unique) == 1:
            D_unit = build_constitutive_matrix_from_bonding(1.0, float(nu_unique[0]), bonding_coeff)
            return E_per_element[:, np.newaxis, np.newaxis] * D_unit[np.newaxis, :, :]
        else:
            return np.stack([
                build_constitutive_matrix_from_bonding(float(E), float(nu), bonding_coeff)
                for E, nu in zip(E_per_element, nu_per_element)
            ])
    else:
        nu_unique = np.unique(nu_per_element)
        if len(nu_unique) == 1:
            D_unit = build_constitutive_matrix(1.0, float(nu_unique[0]))
            return E_per_element[:, np.newaxis, np.newaxis] * D_unit[np.newaxis, :, :]
        else:
            return np.stack([
                build_constitutive_matrix(float(E), float(nu))
                for E, nu in zip(E_per_element, nu_per_element)
            ])


def _gather_element_displacements(
    elements: np.ndarray,
    displacements: np.ndarray,
) -> np.ndarray:
    """Gather element nodal displacements for all elements at once.

    Instead of per-element dof_indices construction + indexing, builds
    the (M, 12) DOF index array vectorially and gathers in one operation.

    Args:
        elements: Element connectivity, shape (M, 4), node indices.
        displacements: Global displacement vector, shape (ndof,).

    Returns:
        (M, 12) array of per-element displacements.
    """
    # Build DOF indices: (M, 12) — for each node, 3 DOFs (x, y, z)
    elem_dofs = np.zeros((elements.shape[0], 12), dtype=np.int64)
    for i in range(4):
        elem_dofs[:, i*3 + 0] = elements[:, i] * 3
        elem_dofs[:, i*3 + 1] = elements[:, i] * 3 + 1
        elem_dofs[:, i*3 + 2] = elements[:, i] * 3 + 2
    return displacements[elem_dofs]  # (M, 12) — numpy fancy indexing


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
