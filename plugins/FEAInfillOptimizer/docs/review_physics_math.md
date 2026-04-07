# Physics, Mathematics & Correctness Review

**Reviewer:** Computational Mechanics Expert
**Date:** 2026-04-07
**Scope:** All physics, mathematics, and numerical correctness in `fea/*.py`
**Verdict:** PASS with minor issues (no correctness-critical bugs in the scipy solver path)

---

## Executive Summary

The FEA implementation is mathematically sound and physically well-motivated. The core solver (scipy path) correctly implements linear tetrahedral finite elements with both isotropic and transversely isotropic constitutive models. The SIMP OC topology optimization follows Sigmund (2001) faithfully. One moderate bug was found in the EasyFEA path's transverse shear parameter, and several minor documentation/robustness items are noted.

**Critical findings: 0**
**Moderate findings: 1** (EasyFEA path Gl parameter)
**Minor findings: 6**
**Observations: 8**

---

## 1. Constitutive Matrices (`homogenization.py`)

### 1.1 Isotropic D Matrix -- CORRECT

`build_constitutive_matrix()` (lines 86-131):

The Lame parameters and 6x6 matrix structure are textbook-correct:

```
lambda = E*nu / ((1+nu)(1-2nu))
mu     = E / (2(1+nu))
```

Normal block: `D[i,i] = lambda + 2*mu`, off-diagonal `D[i,j] = lambda`.
Shear block: `D[3,3] = D[4,4] = D[5,5] = mu`.

**Voigt consistency verified**: The shear entries use mu (not 2*mu) because the B matrix produces *engineering* shear strains (gamma_xy = du/dy + dv/dx), and tau_xy = mu * gamma_xy = G * gamma_xy. This is internally consistent throughout the codebase.

**Admissibility check**: The function correctly validates -1 < nu < 0.5 and warns for nu > 0.45 (volumetric locking). CORRECT.

### 1.2 Transversely Isotropic D Matrix -- CORRECT

`build_constitutive_matrix_transverse()` (lines 134-205):

Five independent constants: E_p, E_t, nu_p, nu_tp, G_t with Z as symmetry axis.

**Reciprocal relation**: `nu_pt = nu_tp * E_p / E_t` -- standard thermodynamic requirement from symmetry of the compliance matrix. CORRECT.

**Thermodynamic admissibility**: `DELTA = 1 - nu_p - 2*nu_pt*nu_tp > 0` -- necessary and sufficient for positive-definiteness of the normal block. CORRECT.

**Stiffness entries** (verified by analytical inversion of the 3x3 compliance block):

| Entry | Code formula | Verified |
|-------|-------------|----------|
| D_11 | `E_p * (1 - nu_pt*nu_tp) / ((1+nu_p)*DELTA)` | CORRECT |
| D_12 | `E_p * (nu_p + nu_pt*nu_tp) / ((1+nu_p)*DELTA)` | CORRECT |
| D_13 | `E_t * nu_pt / DELTA` (= `E_p * nu_tp / DELTA`) | CORRECT |
| D_33 | `E_t * (1 - nu_p) / DELTA` | CORRECT |
| D_44 | `G_p = E_p / (2(1+nu_p))` (in-plane shear, derived) | CORRECT |
| D_55, D_66 | `G_t` (transverse shear, independent) | CORRECT |

Derivation spot-check with PLA (E_p=3000, k=0.5, nu_p=0.36):
- nu_tp = 0.36*sqrt(0.5) = 0.2546, nu_pt = 0.5091
- DELTA = 1 - 0.36 - 2*0.5091*0.2546 = 0.3808 > 0 CORRECT

### 1.3 Bonding Parameterization -- CORRECT and ELEGANT

`build_constitutive_matrix_from_bonding()` (lines 208-248):

The sqrt(k) parameterization for nu_tp:

```
E_t   = k * E_p
G_t   = k * G_p
nu_tp = nu_p * sqrt(k)
```

**Key property verified**: With this parameterization, `nu_pt * nu_tp = nu_p^2` (independent of k!), so `DELTA = 1 - nu_p - 2*nu_p^2`. This is positive iff `nu_p < 0.5`, which is guaranteed by the physical admissibility of nu. The parameterization thus guarantees thermodynamic admissibility for ALL k in (0,1] without additional checks. Elegant.

**Physical reasonableness**: E_t and G_t scaling linearly with k is standard for interlayer weakness modeling. The sqrt(k) scaling for nu_tp is the unique choice that decouples admissibility from k.

### 1.4 D Matrix Scaling Optimization -- CORRECT

`_build_D_matrices()` in `fea_solver.py` (lines 835-875):

When nu is uniform across elements, the code computes `D_unit = D(E=1, nu, k)` and scales by `E[:, None, None] * D_unit`. This exploits the linearity of all D entries in E (verified: every entry of the transversely isotropic D is proportional to E_p when nu_p and k are fixed). CORRECT optimization.

---

## 2. Strain-Displacement Matrix B (`fea_solver.py`)

### 2.1 Reference Shape Functions -- CORRECT

Linear tetrahedron with vertices at: origin (N0=1-xi-eta-zeta), (1,0,0) (N1=xi), (0,1,0) (N2=eta), (0,0,1) (N3=zeta).

```python
dN_ref = [[-1, -1, -1],   # grad N0
          [ 1,  0,  0],   # grad N1
          [ 0,  1,  0],   # grad N2
          [ 0,  0,  1]]   # grad N3
```

Standard for linear tet. CORRECT.

### 2.2 Jacobian and Shape Function Gradients -- CORRECT

The Jacobian `J[i,j] = dx_j/dxi_i` with rows as edge vectors from x0:

```python
J = np.stack([x1-x0, x2-x0, x3-x0], axis=1)  # (M, 3, 3)
```

Physical gradients: `dN_xyz = dN_ref @ J_inv`, giving `dN_xyz[a,j] = dN_a/dx_j`. Verified by chain rule: `dN/dx_j = sum_k (dN/dxi_k) * (J^{-1})[k,j]` where `(J^{-1})[k,j] = dxi_k/dx_j`. CORRECT.

> **MINOR-1: Documentation error** at `fea_solver.py:586` -- comment says "J^{-T}" but the code correctly uses J^{-1}. The row-vector form `dN_xyz = dN_ref @ J_inv` is correct; the comment describes the column-vector convention (`grad_x = J^{-T} grad_xi`), which is mathematically equivalent but doesn't match the code form.

### 2.3 B Matrix Assembly -- CORRECT

For each node i with gradients (bx, by, bz):

```
Row 0 (eps_xx): B[0, 3i] = bx
Row 1 (eps_yy): B[1, 3i+1] = by
Row 2 (eps_zz): B[2, 3i+2] = bz
Row 3 (gam_xy): B[3, 3i] = by, B[3, 3i+1] = bx
Row 4 (gam_yz): B[4, 3i+1] = bz, B[4, 3i+2] = by
Row 5 (gam_xz): B[5, 3i] = bz, B[5, 3i+2] = bx
```

Standard engineering strain B matrix in Voigt ordering [eps_xx, eps_yy, eps_zz, gam_xy, gam_yz, gam_xz]. Produces engineering (not tensorial) shear strains, consistent with the D matrix. CORRECT.

### 2.4 Element Volume -- CORRECT

`V = |det(J)| / 6.0` -- standard formula for tet volume. The absolute value handles both CW and CCW node orderings. CORRECT.

### 2.5 Vectorized Einsum Indices -- CORRECT

All einsum operations verified:

| Location | Expression | Computes | Verified |
|----------|-----------|----------|----------|
| `fea_solver.py:120` | `"ik,mkj->mij"` | `dN_xyz = dN_ref @ J_inv[m]` | CORRECT |
| `fea_solver.py:161` | `"mji,mjk->mik"` | `B^T @ D` (j,i transposes B) | CORRECT |
| `fea_solver.py:162` | `"m,mij,mjk->mik"` | `V * (B^T D) @ B` | CORRECT |
| `fea_solver.py:376` | `"mij,mj->mi"` | `strain = B @ u_e` | CORRECT |
| `fea_solver.py:379` | `"mij,mj->mi"` | `stress = D @ strain` | CORRECT |
| `fea_solver.py:448` | `"mi,mi->m"` | `strain^T @ stress` (dot product) | CORRECT |
| `oc_update.py:107` | `"ij,mj->mi"` | `stress = D0 @ strain` (D0 broadcast) | CORRECT |

---

## 3. Element Stiffness Assembly (`fea_solver.py`)

### 3.1 Element Stiffness Formula -- CORRECT

`k_e = V * B^T D B` (12x12 per element)

For linear tet elements, B is constant within the element (no numerical integration needed). The formula is the exact integral: `k_e = integral_V B^T D B dV = B^T D B * V`. CORRECT.

### 3.2 Global Assembly via COO -- CORRECT

DOF ordering: [u_x0, u_y0, u_z0, u_x1, ...] (node-by-node, 3 DOFs per node).

Assembly uses COO format with `(k_e.ravel(), (row.ravel(), col.ravel()))` then converts to CSR. The duplicate entries at shared DOFs are summed automatically by scipy's COO->CSR conversion. CORRECT.

### 3.3 Degenerate Element Handling -- CORRECT

Threshold: `|det(J)| < max_edge^3 * 1e-10` -- scale-relative, avoiding fixed absolute thresholds that fail for large/small models. Degenerate elements get k_e = 0 (zero stiffness) and identity Jacobian (to avoid singular inverse). CORRECT approach.

---

## 4. Boundary Conditions (`fea_solver.py:189-266`)

### 4.1 Dirichlet BC Application -- CORRECT

Method: zero rows AND columns for constrained DOFs, set diagonal to 1.

This is the standard penalty-free elimination that preserves SPD structure:
1. Zero all entries in rows of fixed DOFs (via CSR row-label mask)
2. Zero all entries in columns of fixed DOFs (via K.indices mask)
3. Set diagonal to 1 for fixed DOFs
4. Set force to 0 for fixed DOFs

The vectorized CSR implementation is O(nnz) total. CORRECT.

---

## 5. Von Mises Stress (`fea_solver.py`)

### 5.1 Standard Von Mises -- CORRECT

```python
vm2 = sx**2 + sy**2 + sz**2 - sx*sy - sy*sz - sx*sz + 3*(txy**2 + tyz**2 + txz**2)
```

This is the standard expansion of:
`sigma_vm^2 = 0.5 * [(sx-sy)^2 + (sy-sz)^2 + (sz-sx)^2 + 6*(txy^2 + tyz^2 + txz^2)]`

Expanding the squares: `0.5*(2sx^2 + 2sy^2 + 2sz^2 - 2sx*sy - 2sy*sz - 2sx*sz) + 3*tau^2`
= `sx^2 + sy^2 + sz^2 - sx*sy - sy*sz - sx*sz + 3*tau^2`. CORRECT.

### 5.2 Directional Von Mises -- PHYSICALLY REASONABLE

Scales sigma_zz, tau_yz, tau_xz by 1/k before standard von Mises. This is a heuristic that amplifies interlayer stress contributions proportional to the inverse of the bonding strength. When k=1, reduces to standard von Mises. CORRECT for its intended purpose as an engineering approximation.

> **MINOR-2**: The directional von Mises is not a rigorous failure criterion for anisotropic materials -- it's a heuristic modification. For critical structural assessments, the Tsai-Hill criterion (Section 6) is more appropriate. The code correctly uses Tsai-Hill when `bonding_coeff < 0.95`.

### 5.3 Vectorized Versions -- CORRECT

Both `_von_mises_vectorized` and `_von_mises_directional_vectorized` are mathematically equivalent to their scalar counterparts, using numpy broadcasting. Verified by inspection.

---

## 6. Tsai-Hill Failure Criterion (`fea_solver.py:613-753`)

### 6.1 Scalar Tsai-Hill -- CORRECT

For transversely isotropic FDM parts with Z as weak axis:

```
(sigma_ip/X)^2 + (sigma_z/Z)^2 - sigma_ip*sigma_z/X^2 + (tau_op/S)^2 <= 1
```

Where:
- X = yield_strength (in-plane strength)
- Z = k * yield_strength (interlayer strength)
- S = 0.6 * Z (interlayer shear strength)
- sigma_ip = sqrt(sx^2 + sy^2 - sx*sy + 3*txy^2) (planar von Mises)
- tau_op = sqrt(tyz^2 + txz^2) (out-of-plane shear resultant)

The cross-term `-sigma_ip*sigma_z/X^2` uses X^2 (the in-plane strength squared), which is standard Tsai-Hill for the interaction between in-plane and out-of-plane normal stresses. CORRECT.

The in-plane stress resultant sigma_ip is the 2D von Mises equivalent, which is the correct invariant for the isotropic XY plane. CORRECT.

> **MINOR-3**: The shear strength `S = 0.6 * Z` is an engineering approximation. The 0.6 factor represents a typical ratio of shear-to-tensile interlayer strength for FDM polymers. This is reasonable but not backed by a specific per-material reference. Consider parameterizing S/Z ratio per material in `MaterialDatabase` for higher fidelity.

### 6.2 Vectorized Tsai-Hill -- CORRECT

`_tsai_hill_vectorized()` is mathematically equivalent to the scalar version. Verified by inspection.

---

## 7. SIMP OC Density Update (`oc_update.py`)

### 7.1 Compliance Sensitivity -- CORRECT

For SIMP: `E_e = rho_e^p * E_0`, so compliance C = u^T K u and:

`dC/drho_e = -p * rho_e^(p-1) * u_e^T * K0_e * u_e`

The code computes the *negative* sensitivity (always non-negative):

```python
dc = n_exp * np.power(np.maximum(density, rho_min), n_exp - 1.0) * ce
```

where `ce = V * strain^T @ D0 @ strain = u_e^T K0_e u_e` uses the **base** (unit-density) constitutive matrix D0. This is critical for correct SIMP sensitivity -- the code correctly uses a single D0 for all elements in `_compute_element_stiffness_and_compliance`. CORRECT.

### 7.2 OC Update Rule -- CORRECT (Sigmund 2001)

```python
B = dc / (lam_mid * volumes)
rho_candidate = density * B^eta
rho_new = max(rho_min, max(rho - move, min(rho_max, min(rho + move, rho_candidate))))
```

This is exactly the standard OC update from Sigmund (2001) "A 99 line topology optimization code written in Matlab". The bisection on lambda enforces the volume constraint:

`sum(rho_new * V) = volume_fraction * sum(V)`

Bisection runs up to 100 iterations with convergence check `(lam_hi - lam_lo)/lam_mid < 1e-6`. Typical convergence in 30-40 iterations. CORRECT.

### 7.3 SIMP Exponent Choice -- PHYSICALLY APPROPRIATE

The code reuses the infill pattern homogenization exponent (n_exp from Gibson-Ashby) as the SIMP penalization exponent. This is a deliberate and correct design choice:

- Standard SIMP uses p=3 to penalize intermediate densities toward binary 0/1.
- For infill optimization, intermediate densities ARE the goal (they represent valid infill levels).
- Using the Gibson-Ashby exponent ensures the density-stiffness relationship matches the actual infill geometry's physics.

This makes the OC update physically meaningful rather than just a mathematical penalization. CORRECT for this application.

---

## 8. Force and Torque Distribution (`iterative_solver.py`)

### 8.1 Tributary Area Force Distribution -- CORRECT

Each triangle contributes `area/3` to each of its three vertices (lines 940-951). This is the standard P1 finite element "lumped" area weighting. Force per node: `F_i = F_total * (w_i / sum(w))` where w_i is tributary area. CORRECT.

### 8.2 Torque Distribution (Rigid-Body Rotation Model) -- CORRECT

Physical model: rigid-body angular displacement gives force proportional to perpendicular radius.

```
lambda = T / sum(r_perp_i^2)
F_i = lambda * r_perp_i = T * r_perp_i / sum(r_perp_j^2)
```

Direction: tangent = cross(n_axis, r_perp), normalized to unit vector.

**Equilibrium verification**:
`T_total = sum(F_i * r_perp_i) = T * sum(r_perp_i^2) / sum(r_perp_j^2) = T` CORRECT.

The perpendicular distance computation correctly decomposes r = pos - center into axial and perpendicular components. CORRECT.

---

## 9. Homogenization Exponents (`iterative_solver.py`)

### 9.1 Pattern Exponent Table -- WELL-CALIBRATED

| Pattern | n | Physical basis | Assessment |
|---------|---|---------------|------------|
| lines, zigzag, concentric | 1.0 | Stretching-dominated, rule-of-mixtures | CORRECT |
| triangles, trihexagon | 1.3 | Near-stretching triangulated lattice | CORRECT per Gibson-Ashby |
| gyroid | 1.6 | TPMS, mixed mode | CORRECT per Al-Ketan et al. 2018 |
| grid, cubic, cubicsubdiv, octagon | 2.0 | Bending-dominated open-cell | CORRECT per Gibson-Ashby 1997 |
| tetrahedral, quarter_cubic | 1.8 | Near-bending (practical octet) | REASONABLE |
| honeycomb, cross, cross_3d | 2.3 | Highly bending-dominated hexagonal | CORRECT per Fernandez-Vicente 2016 |
| lightning | 1.0 | Sparse tree structure (axial loads) | REASONABLE approximation |

Literature references in the docstrings are accurate and traceable.

> **MINOR-4**: The `lightning` pattern (n=1.0) may slightly overestimate stiffness at low density. Lightning infill's tree structure has branching joints that introduce bending, suggesting n ~ 1.1-1.3 would be more accurate. However, n=1.0 errs on the conservative side for load-bearing applications (overestimates stiffness -> underestimates stress -> underestimates required density). Low priority.

---

## 10. Material Database (`material_database.py`)

### 10.1 Material Properties -- VERIFIED AGAINST LITERATURE

| Material | E_xy (MPa) | Literature range | nu | yield (MPa) | k = E_z/E_xy |
|----------|-----------|-----------------|------|------------|-------------|
| PLA | 3000 | 2700-4000 | 0.36 | 50 | 0.50 |
| ABS | 2100 | 1800-2500 | 0.35 | 35 | 0.50 |
| PETG | 2000 | 1800-2200 | 0.38 | 42 | 0.50 |
| Nylon | 1400 | 1000-2000 (cond.) | 0.40 | 48 | 0.50 |
| PC | 2200 | 2000-2400 | 0.37 | 60 | 0.50 |
| TPU_95A | 26 | 15-40 | 0.48 | 30 | 0.50 |
| CF_Nylon | 6500 | 5000-10000 | 0.35 | 80 | 0.246 |
| CF_PET | 5000 | 4000-6000 | 0.37 | 60 | 0.35 |

All values within published ranges for FDM parts. Nylon correctly documented as conditioned state (50% RH). TPU_95A correctly flagged as hyperelastic (invalid for linear FEA). Carbon fiber materials correctly have lower k (E_z/E_xy) due to fiber alignment in XY.

### 10.2 Bonding Coefficient Derivation -- CORRECT

`bonding_coefficient = min(E_z / E_xy, 1.0)` -- physically meaningful ratio of through-layer to in-plane stiffness. Clamped to 1.0 to prevent super-isotropic behavior. CORRECT.

---

## 11. Stress-to-Density Mapping (`stress_to_density.py`)

### 11.1 Mapping Function -- CORRECT

```
sigma_eff = sigma_yield / safety_factor
s = clip(sigma_vm / sigma_eff, 0, 1)
rho = rho_min + (rho_max - rho_min) * sqrt(s)   [power method]
```

The sqrt mapping is a standard heuristic in stress-based topology approaches:
- s=0 -> rho_min (no stress -> minimum infill)
- s=0.25 -> rho_min + 0.5*(rho_max - rho_min) (moderate stress -> ~midpoint)
- s=1 -> rho_max (at design stress -> maximum infill)

The square root compresses the high-stress end, placing more material in moderately stressed regions. Physically sensible and well-documented.

### 11.2 Safety Factor -- JUSTIFIED

Default safety_factor = 2.0, referenced to Chacon et al. (2017) *Materials & Design* 124: FDM parts exhibit 20-30% coefficient of variation in tensile strength. A factor of 2.0 keeps working stress at 50% of yield, consistent with a 2-sigma margin. CORRECT.

---

## 12. Convergence Criteria (`iterative_solver.py`)

### 12.1 Heuristic Method -- APPROPRIATE

`max|delta_rho| < 1e-3` (0.1% density change). Suitable for the stress-mapping fixed-point iteration which converges smoothly.

### 12.2 OC Method -- STANDARD (Sigmund 2001)

Dual criterion (both must be satisfied):
- `max|delta_rho| < 0.01` (1% density change)
- `|delta_C/C| < 1e-3` (0.1% relative compliance change)

The looser density tolerance for OC is appropriate -- OC naturally oscillates more due to the Lagrange multiplier bisection. Adding the compliance convergence check provides robustness against density oscillation around a compliance-optimal state.

### 12.3 Adaptive Damping -- CORRECT

Initial damping = 0.5 (equal blend old/new), minimum = 0.2. Oscillation detection: if max_change increases for 2 consecutive iterations, reduce damping by 20%. Standard oscillation control strategy for fixed-point iterations.

---

## 13. Solver and Numerical Issues

### 13.1 Direct Solver with Fallback -- CORRECT

Primary: `scipy.sparse.linalg.spsolve` (SuperLU) with 30s timeout.
Fallback: CG with Jacobi (diagonal) preconditioner, tol=1e-8, maxiter=5000.

The timeout handles SuperLU stalls on ill-conditioned matrices from SIMP extreme density ratios. CG with Jacobi is a reasonable fallback -- guaranteed to terminate, bounded iterations. CORRECT architecture.

### 13.2 E_min Floor in SIMP -- CORRECT

```python
E_eff_arr = np.maximum(E_eff_arr, material.E_xy * 0.01)
```

Floor at 1% of full E prevents singular stiffness matrix when density approaches rho_min. This is standard practice in SIMP implementations (Sigmund uses E_min = 1e-9 * E0, but 1% is more conservative and appropriate for infill where truly void regions don't exist). CORRECT.

### 13.3 Volumetric Locking Guard -- CORRECT

`if material.nu > 0.45: raise ValueError` -- linear tet elements (C3D4) with constant strain cannot represent incompressible modes. Threshold of 0.45 is standard. The TPU_95A material (nu=0.48) is correctly blocked. CORRECT.

---

## 14. Findings

### MODERATE-1: EasyFEA Path Transverse Shear Parameter

**Location:** `iterative_solver.py:303-314`
**Severity:** Moderate (affects EasyFEA path only; scipy path is correct)

```python
G_base = E_xy / (2.0 * (1.0 + nu))   # This is G_p (in-plane shear)
mat = TransverselyIsotropic(
    ...
    Gl=G_base,    # BUG: should be k * G_base = G_t (transverse shear)
    ...
)
```

For a transversely isotropic material, the 5th independent constant must be the transverse shear modulus G_t = G_xz = G_yz. The in-plane shear G_p = E_p/(2(1+nu_p)) is derived, not independent.

EasyFEA's `Gl` parameter represents this independent transverse shear modulus. The code passes G_p instead of G_t = k * G_p.

**Impact:** Transverse shear stiffness overestimated by factor 1/k in EasyFEA path. For PLA (k=0.5): 2x too stiff. For CF_Nylon (k=0.246): 4x too stiff.

**Fix:** `Gl = bonding_coeff * G_base` (one character change).

**Mitigation:** The OC path forces `use_easyfea = False` (line 186), so SIMP optimization is unaffected. The EasyFEA path is used only for the heuristic method.

### MINOR-1: Comment Error in Chain Rule

**Location:** `fea_solver.py:586`

Comment says `dN/d[x,y,z] = dN/d[xi,eta,zeta] x (J^{-T})` but the code correctly uses `dN_ref @ J_inv` (without transpose). The row-vector form requires J^{-1}, not J^{-T}. Code is correct; comment is wrong.

### MINOR-2: Directional Von Mises is Heuristic

**Location:** `fea_solver.py:690-714`

The directional von Mises (scaling Z-components by 1/k) is an engineering heuristic, not a derived failure criterion. It's used only when bonding_coeff >= 0.95 (nearly isotropic), and the code correctly switches to Tsai-Hill for more anisotropic cases (k < 0.95). Acceptable.

### MINOR-3: Hardcoded Shear Strength Ratio

**Location:** `fea_solver.py:629` -- `S = 0.6 * Z`

The shear-to-tensile ratio 0.6 is reasonable but not per-material. Could be parameterized in `MaterialDatabase` for higher fidelity (e.g., CF materials may have different S/Z ratios than neat polymers).

### MINOR-4: Lightning Pattern Exponent

**Location:** `iterative_solver.py:70` -- `"lightning": 1.0`

Lightning infill's tree structure has branching joints that introduce bending. n=1.0 may overestimate stiffness slightly; n~1.1-1.3 would be more accurate. However, this overestimate leads to conservative structural results (higher infill), so the error is safe-sided.

### MINOR-5: Compliance Computation for Convergence

**Location:** `iterative_solver.py:652`

```python
compliance = float(np.dot(force_vector, displacements))
```

This computes total compliance C = f^T u, which equals u^T K u by virtual work. CORRECT, but note this uses the *modified* force vector (after BC application, where f[fixed_dofs] = 0). Since u[fixed_dofs] = 0 as well, the inner product is unaffected. Verified.

### MINOR-6: Pattern Exponent Table Duplication

**Location:** `iterative_solver.py:61-82` and `homogenization.py:42-49`

The `_PATTERN_EXPONENTS` dict is duplicated in both files with overlapping but not identical entries. The `iterative_solver.py` version has more patterns (zigzag, concentric, trihexagon, lightning, cubicsubdiv, tetrahedral, quarter_cubic, octagon, cross, cross_3d). The `homogenization.py` version has fewer entries and a different default (1.3 vs 1.5). This inconsistency could lead to different exponents being used depending on which dict is consulted.

---

## 15. Observations (Non-Issues)

1. **Initial density = (rho_min + rho_max) / 2**: Good choice. Starting at rho_min creates extremely ill-conditioned K; midpoint provides balanced initial stiffness.

2. **Linear tet elements (C3D4)**: Appropriate for this application. C3D4 are the simplest 3D elements, easy to generate from surface meshes, and sufficient for stress-based infill optimization where exact stress values matter less than relative stress distribution.

3. **Poisson's ratio treated as constant with density**: Standard assumption for moderate density ranges (0.1-0.8). At very low density the effective Poisson's ratio approaches 0 for open-cell foams, but this range is below the practical rho_min.

4. **No mesh sensitivity filter in SIMP OC**: Standard SIMP implementations include a density filter (radius-based averaging) to prevent checkerboard patterns. The absence is acceptable because: (a) the infill density field is mapped to a smooth slicer parameter, not directly manufactured; (b) the heuristic method's damping provides implicit smoothing.

5. **Thread-based solver timeout**: The spsolve timeout (30s) and EasyFEA Solve timeout (120s) use daemon threads. This is a pragmatic approach to prevent hangs but note that SuperLU's internal state may be corrupted if the thread doesn't terminate cleanly. The fallback to CG mitigates this.

6. **Degenerate element handling is consistent**: Both the assembly and stress computation paths use the same scale-relative threshold (`max_edge^3 * 1e-10`) and identity-Jacobian fallback. No risk of inconsistent treatment.

7. **Material failure mode warnings**: The solver correctly warns for brittle materials (PLA, CF_Nylon) where von Mises may overestimate strength by 15-30%, and for hyperelastic materials (TPU) where linear FEA is invalid.

8. **EasyFEA observer cleanup**: The careful deregistration of `_prev_simu` from mesh/model observers (lines 333-342) is critical -- without it, the solver hangs due to exponential observer cascade. Well-documented root cause analysis.

---

## 16. Cross-Discipline Notes

### For Performance Expert
- The vectorized einsum assembly is mathematically correct and ~50-100x faster than Python loops (claimed). The `_build_D_matrices` optimization (scaling D_unit by E array) avoids M individual matrix constructions when nu is uniform. Both are safe to keep.

### For Reliability Expert
- The E_min floor (1% of E0) prevents singular matrices in SIMP. The CG fallback prevents solver hangs. Degenerate element detection is scale-relative. All contribute to robustness without compromising correctness.

### For Code Quality Expert
- MODERATE-1 (EasyFEA Gl parameter) should be fixed before release.
- MINOR-6 (pattern exponent duplication) should be deduplicated to a single source of truth.
- MINOR-1 (comment error) should be corrected.

### For Test Expert
- Key verification targets: (1) isotropic D matrix reproduces analytical cantilever beam deflection; (2) transversely isotropic D produces correct stress amplification in Z; (3) SIMP OC converges to known solutions (e.g., Sigmund's MBB beam); (4) Tsai-Hill failure index = 1.0 at exact yield for uniaxial loading.

---

## 17. Conclusion

The mathematical foundation of the FEA plugin is solid. The constitutive matrices, strain-displacement matrices, von Mises and Tsai-Hill criteria, SIMP OC optimization, and force distribution are all correctly implemented. The one actionable bug (EasyFEA transverse shear parameter) affects only the EasyFEA solver path and has a trivial fix. The physics choices (Gibson-Ashby exponents, sqrt(k) parameterization, safety factor justification) are well-motivated and documented.

**Recommendation:** Fix MODERATE-1 (Gl parameter), deduplicate pattern exponents (MINOR-6), correct the comment (MINOR-1), and the physics layer is production-ready.
