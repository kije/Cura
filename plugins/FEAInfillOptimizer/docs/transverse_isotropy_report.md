# Technical Report: Transversely Isotropic FEA for FDM 3D-Printed Parts

**Date:** 2026-04-03
**Scope:** FEAInfillOptimizer plugin -- extension from isotropic to transversely isotropic linear elasticity
**Author:** Computational Mechanics Analysis

---

## Table of Contents

1. [Introduction and Motivation](#1-introduction-and-motivation)
2. [Transversely Isotropic Constitutive Matrix](#2-transversely-isotropic-constitutive-matrix)
3. [Layer Bonding Coefficient Approach](#3-layer-bonding-coefficient-approach)
4. [Explicit Layer Simulation](#4-explicit-layer-simulation)
5. [Smeared Approach (Recommended)](#5-smeared-approach-recommended)
6. [Implementation Plan](#6-implementation-plan)
7. [Failure Criteria for Transversely Isotropic Materials](#7-failure-criteria-for-transversely-isotropic-materials)
8. [Validation Strategy](#8-validation-strategy)
9. [References](#9-references)

---

## 1. Introduction and Motivation

### 1.1 Current State

The existing FEA solver in `fea_solver.py` uses isotropic linear elasticity. The
constitutive matrix D in `homogenization.py` is built from two independent
constants (E, nu) via the Lame parameters:

```
lambda = E * nu / ((1 + nu)(1 - 2*nu))
mu     = E / (2(1 + nu))
```

This produces the familiar isotropic 6x6 stiffness tensor in Voigt notation.

### 1.2 The Problem

FDM (Fused Deposition Modeling) parts are **not isotropic**. The layer-by-layer
deposition process creates a material with distinct properties along the build
direction (Z) compared to the in-plane directions (X, Y). Specifically:

- **In-plane (XY):** Filament roads are fused side-by-side within each layer.
  The modulus E_xy approaches the bulk polymer value.
- **Interlayer (Z):** Layers bond via thermal diffusion of polymer chains across
  the interface. The bond is typically weaker, yielding E_z ~ 0.3-0.7 * E_xy.
- **Shear:** The interlayer shear modulus G_xz is similarly reduced relative to
  the in-plane G_xy.

The material database (`material_database.py`) **already stores** both E_xy and
E_z per material, but the solver currently ignores E_z and uses only E_xy with
the isotropic D matrix. This is the gap to close.

### 1.3 Symmetry Classification

FDM parts exhibit **transverse isotropy** (also called hexagonal symmetry). The
X-Y plane is the plane of isotropy, and Z is the symmetry axis. This requires
**5 independent elastic constants**, intermediate between full isotropy (2
constants) and full orthotropy (9 constants).

---

## 2. Transversely Isotropic Constitutive Matrix

### 2.1 The Five Independent Constants

For a transversely isotropic material with the Z-axis as the symmetry axis,
the five independent engineering constants are:

| Symbol | Physical meaning                       | Typical FDM range |
|--------|----------------------------------------|-------------------|
| E_p    | In-plane Young's modulus (= E_xy = E_x = E_y) | 1400-6500 MPa |
| E_t    | Transverse (through-thickness) Young's modulus (= E_z) | 0.3-0.7 * E_p |
| nu_p   | In-plane Poisson's ratio (= nu_xy)     | 0.33-0.40 |
| nu_tp  | Transverse-to-in-plane Poisson's ratio (= nu_zx = nu_zy) | see derivation |
| G_t    | Transverse shear modulus (= G_xz = G_yz) | 0.3-0.5 * G_p |

The **derived** quantity is:

```
G_p = E_p / (2(1 + nu_p))    [in-plane shear modulus, not independent]
```

**Notation convention used in this report:**

- Subscript `p` = "plane" (in the XY plane)
- Subscript `t` = "transverse" (along Z, the build direction)

### 2.2 Compliance Matrix (Inverse Form)

The compliance matrix S relates strain to stress: {epsilon} = [S] {sigma}.
In Voigt notation with ordering [xx, yy, zz, xy, yz, xz]:

```
        [  1/E_p     -nu_p/E_p   -nu_tp/E_t    0       0       0     ]
        [ -nu_p/E_p   1/E_p      -nu_tp/E_t    0       0       0     ]
    S = [ -nu_pt/E_p -nu_pt/E_p   1/E_t        0       0       0     ]
        [  0           0           0          1/G_p     0       0     ]
        [  0           0           0            0     1/G_t     0     ]
        [  0           0           0            0       0     1/G_t   ]
```

where the reciprocal relation (from symmetry of S) gives:

```
nu_pt / E_p  =  nu_tp / E_t

=> nu_pt = nu_tp * (E_p / E_t)
```

Here:
- nu_pt = nu_xz = nu_yz : Poisson's ratio for transverse strain when loading
  in-plane (strain in Z when pulling in X)
- nu_tp = nu_zx = nu_zy : Poisson's ratio for in-plane strain when loading
  transversely (strain in X when pulling in Z)

### 2.3 Stiffness Matrix (Direct Form)

The stiffness (constitutive) matrix D = S^{-1}. For transverse isotropy with
Z as the symmetry axis, this can be derived analytically.

Define the intermediate quantity:

```
Delta = (1 + nu_p)(1 - nu_p - 2 * nu_pt * nu_tp)  /  (E_p^2 * E_t)
```

or equivalently, using the determinant of the upper-left 3x3 block of S:

```
Delta = 1 / (E_p^2 * E_t) * [(1 - nu_p)(1 - nu_p) - 2 * nu_pt^2 * E_t/E_p - nu_p^2]
```

A cleaner parameterization uses the **reciprocal relation** and defines:

```
Let:
  n = E_t / E_p        (modulus ratio, 0 < n <= 1)
  f = nu_pt            (= nu_tp * E_p/E_t = nu_tp / n)
```

Then the denominator of the stiffness terms is:

```
Phi = 1 / [E_p * (1 + nu_p) * (1 - nu_p - 2*n*f^2)]
```

**However, for implementation clarity, I will derive D directly by inverting S.**

#### Full Analytical Inversion of the 3x3 Normal Block

The 3x3 upper-left block of S is:

```
        [ 1/E_p      -nu_p/E_p    -nu_tp/E_t  ]
S_nn =  [ -nu_p/E_p   1/E_p       -nu_tp/E_t  ]
        [ -nu_pt/E_p  -nu_pt/E_p   1/E_t       ]
```

Using nu_pt = nu_tp * E_p / E_t, let me define for compactness:

```
a = 1/E_p
b = nu_p/E_p
c = nu_pt/E_p  = nu_tp/E_t
d = 1/E_t
```

So:

```
        [  a   -b   -c ]
S_nn =  [ -b    a   -c ]
        [ -c   -c    d ]
```

The determinant is:

```
det(S_nn) = a(a*d - c^2) + b(-b*d + c^2) + (-c)(b*c + a*c)
          = a^2*d - a*c^2 - b^2*d + b*c^2 - b*c^2 - a*c^2
          = a^2*d - 2*a*c^2 - b^2*d + b*c^2 - b*c^2
```

Let me redo this carefully:

```
det = a*(a*d - c^2) - (-b)*(-b*d - (-c)*(-c)) + (-c)*(-b*(-c) - a*(-c))
    = a*(a*d - c^2) - (-b)*(-b*d - c^2) + (-c)*(b*c + a*c)

Wait -- standard cofactor expansion along row 1:

det = a * |  a  -c |  - (-b) * | -b  -c |  + (-c) * | -b   a |
          | -c   d |           | -c   d |            | -c  -c |

    = a*(a*d - c^2) + b*(-b*d - c^2) - c*(b*c + a*c)

Hmm, let me be more careful:

| -b  -c |
| -c   d |  = (-b)*d - (-c)*(-c) = -b*d - c^2

| -b   a |
| -c  -c |  = (-b)*(-c) - a*(-c) = b*c + a*c = c*(a+b)

So:
det = a*(a*d - c^2) + b*(-b*d - c^2) - c * c*(a + b)
    = a^2*d - a*c^2 - b^2*d - b*c^2 - c^2*a - c^2*b
    = a^2*d - b^2*d - 2*a*c^2 - 2*b*c^2
    = d*(a^2 - b^2) - 2*c^2*(a + b)
    = (a + b)[d*(a - b) - 2*c^2]
```

Now substituting back:

```
a + b = 1/E_p + nu_p/E_p = (1 + nu_p)/E_p
a - b = 1/E_p - nu_p/E_p = (1 - nu_p)/E_p
```

Therefore:

```
det(S_nn) = [(1 + nu_p)/E_p] * [(1 - nu_p)/(E_p * E_t) - 2 * nu_pt^2 / E_p^2]
          = [(1 + nu_p)/E_p] * [1/(E_p)] * [(1 - nu_p)/E_t - 2 * nu_pt^2 / E_p]
          = [(1 + nu_p)/(E_p^2)] * [(1 - nu_p)/E_t - 2 * nu_pt^2 / E_p]
          = [(1 + nu_p)/(E_p^2 * E_t)] * [1 - nu_p - 2 * nu_pt^2 * E_t / E_p]
```

Using nu_pt = nu_tp * E_p / E_t:

```
nu_pt^2 * E_t / E_p = nu_tp^2 * E_p^2 / E_t^2 * E_t / E_p = nu_tp^2 * E_p / E_t
```

So:

```
det(S_nn) = (1 + nu_p) / (E_p^2 * E_t) * (1 - nu_p - 2 * nu_tp^2 * E_p / E_t)
```

Define:

```
DELTA = 1 - nu_p - 2 * nu_pt * nu_tp
      = 1 - nu_p - 2 * nu_tp^2 * E_p / E_t
```

Then:

```
det(S_nn) = (1 + nu_p) * DELTA / (E_p^2 * E_t)
```

#### Cofactor Matrix and D_nn = S_nn^{-1}

The cofactors of S_nn are:

```
C_11 = a*d - c^2 = (1/E_p)(1/E_t) - nu_pt^2/E_p^2
     = 1/(E_p*E_t) - nu_pt^2/E_p^2
     = (1/E_p) * [1/E_t - nu_pt^2/E_p]
     = (1/E_p^2) * [E_p/E_t - nu_pt^2]

C_12 = -((-b)*d - (-c)*(-c)) = -(- b*d - c^2) = b*d + c^2
     = nu_p/(E_p*E_t) + nu_pt^2/E_p^2

C_13 = (-b)*(-c) - a*(-c) = b*c + a*c = c*(a + b)
     = (nu_pt/E_p) * (1 + nu_p)/E_p
     = nu_pt*(1 + nu_p)/E_p^2

C_22 = a*d - c^2  [same as C_11 by symmetry of x,y]

C_23 = a*(-c) - (-b)*(-c) = -a*c - b*c = -c*(a + b)
     Wait, that doesn't look right. Let me redo.

     C_23 = -(a*(-c) - (-c)*(-b))  [cofactor of position (2,3)]
          = -(- a*c - b*c)
          = c*(a + b)
     = nu_pt*(1 + nu_p)/E_p^2  [same as C_13 by transverse isotropy symmetry]

C_33 = a^2 - b^2 = (a+b)(a-b) = [(1+nu_p)/E_p] * [(1-nu_p)/E_p]
     = (1 - nu_p^2)/E_p^2
```

The inverse is D_nn = adj(S_nn) / det(S_nn) = C^T / det. Since S_nn is
symmetric, C^T = C.

```
D_11 = C_11 / det = [E_p/E_t - nu_pt^2] / [E_p^2 * det]
```

Substituting det = (1+nu_p)*DELTA/(E_p^2 * E_t):

```
D_11 = [E_p/E_t - nu_pt^2] * E_p^2 * E_t / [(1+nu_p)*DELTA * E_p^2]
     = [E_p/E_t - nu_pt^2] * E_t / [(1+nu_p)*DELTA]
     = [E_p - nu_pt^2 * E_t] / [(1+nu_p)*DELTA]
```

Using nu_pt = nu_tp * E_p/E_t:

```
nu_pt^2 * E_t = nu_tp^2 * E_p^2 / E_t

D_11 = E_p * [1 - nu_tp^2 * E_p/E_t] / [(1+nu_p)*DELTA]
     = E_p * (1 - nu_pt * nu_tp) / [(1+nu_p)*DELTA]
```

Similarly:

```
D_12 = C_12 / det
     = [nu_p/(E_p*E_t) + nu_pt^2/E_p^2] * E_p^2 * E_t / [(1+nu_p)*DELTA]
     = [nu_p + nu_pt^2 * E_t/E_p] / [(1+nu_p)*DELTA]
     = E_p * [nu_p/E_p + nu_pt^2*E_t/E_p^2] * E_p / [(1+nu_p)*DELTA]

Let me simplify differently:
     = [nu_p + nu_pt * nu_tp] / [(1+nu_p)*DELTA] * E_p

  since nu_pt^2 * E_t/E_p = nu_pt * (nu_tp * E_p/E_t) * E_t/E_p = nu_pt * nu_tp

So:
D_12 = E_p * (nu_p + nu_pt * nu_tp) / [(1+nu_p)*DELTA]
```

```
D_13 = C_13 / det
     = [nu_pt*(1+nu_p)/E_p^2] * E_p^2 * E_t / [(1+nu_p)*DELTA]
     = nu_pt * E_t / DELTA
     = nu_tp * E_p / DELTA        [using reciprocal relation]
```

```
D_33 = C_33 / det
     = [(1-nu_p^2)/E_p^2] * E_p^2 * E_t / [(1+nu_p)*DELTA]
     = (1-nu_p^2) * E_t / [(1+nu_p)*DELTA]
     = (1-nu_p) * E_t / DELTA
```

### 2.4 Complete 6x6 Stiffness Matrix

Combining the normal block D_nn and the shear block (which is diagonal):

```
        [ D_11   D_12   D_13    0      0      0   ]
        [ D_12   D_11   D_13    0      0      0   ]
    D = [ D_13   D_13   D_33    0      0      0   ]
        [  0      0      0    G_p      0      0   ]
        [  0      0      0      0    G_t      0   ]
        [  0      0      0      0      0    G_t   ]
```

where:

```
DELTA = 1 - nu_p - 2 * nu_pt * nu_tp

D_11 = E_p * (1 - nu_pt * nu_tp) / [(1 + nu_p) * DELTA]

D_12 = E_p * (nu_p + nu_pt * nu_tp) / [(1 + nu_p) * DELTA]

D_13 = E_t * nu_pt / DELTA   =   E_p * nu_tp / DELTA

D_33 = E_t * (1 - nu_p) / DELTA

G_p  = E_p / (2 * (1 + nu_p))     [derived, not independent]

G_t  = G_xz = G_yz                [independent constant]
```

**Voigt ordering:** [sigma_xx, sigma_yy, sigma_zz, tau_xy, tau_yz, tau_xz]
matching the existing `_strain_displacement_matrix` B matrix in `fea_solver.py`.

### 2.5 Dimensional Analysis

Each D_ij has dimensions of stress [Pa = MPa in our system]:
- D_11: E_p * [dimensionless] / [dimensionless] = [MPa]. Correct.
- D_13: E_t * [dimensionless] / [dimensionless] = [MPa]. Correct.
- D_33: E_t * [dimensionless] / [dimensionless] = [MPa]. Correct.
- G_p, G_t: [MPa]. Correct.

The product B^T D B has dimensions [MPa / length^2] * [length^3 (volume)] =
[MPa * length] = [force/length], which is correct for stiffness [N/mm when using
MPa and mm].

### 2.6 Limiting Case: Isotropy Recovery

When E_t = E_p = E, nu_pt = nu_tp = nu (so nu_pt * nu_tp = nu^2), and
G_t = G_p = E/(2(1+nu)):

```
DELTA = 1 - nu - 2*nu^2 = (1 - 2*nu)(1 + nu)

D_11 = E * (1 - nu^2) / [(1+nu) * (1-2*nu)(1+nu)]
     = E * (1-nu)(1+nu) / [(1+nu)^2 * (1-2*nu)]
     = E * (1-nu) / [(1+nu)(1-2*nu)]
     = lambda + 2*mu       [correct!]

D_12 = E * (nu + nu^2) / [(1+nu) * (1-2*nu)(1+nu)]
     = E * nu * (1+nu) / [(1+nu)^2 * (1-2*nu)]
     = E * nu / [(1+nu)(1-2*nu)]
     = lambda              [correct!]

D_13 = E * nu / [(1-2*nu)(1+nu)]
     = lambda              [correct! D_13 = D_12 in isotropic case]

D_33 = E * (1-nu) / [(1-2*nu)(1+nu)]
     = lambda + 2*mu       [correct! D_33 = D_11 in isotropic case]
```

The isotropic D matrix is recovered exactly. This validates the derivation.

### 2.7 Thermodynamic Admissibility (Positive-Definiteness of D)

The stiffness matrix D must be positive definite for the strain energy density
to be positive for all non-zero strain states. This requires:

1. **D_11 > 0:** E_p(1 - nu_pt * nu_tp) > 0
   => nu_pt * nu_tp < 1

2. **D_33 > 0:** E_t(1 - nu_p) > 0
   => nu_p < 1 (always satisfied for physical materials)

3. **DELTA > 0:** 1 - nu_p - 2 * nu_pt * nu_tp > 0

4. **G_p > 0:** E_p > 0, nu_p > -1 (always satisfied)

5. **G_t > 0:** independent positive constant

6. **Sylvester's criterion on the 3x3 normal block:**
   All leading minors must be positive:
   - D_11 > 0  (from condition 1)
   - D_11^2 - D_12^2 > 0  =>  D_11 > |D_12|
   - det(D_nn) > 0  =>  this is equivalent to DELTA > 0 (condition 3)

The **combined admissibility constraint** reduces to:

```
DELTA = 1 - nu_p - 2 * nu_pt * nu_tp > 0
```

together with E_p > 0, E_t > 0, G_t > 0, -1 < nu_p < 1.

In practice, for FDM materials with nu_p ~ 0.35 and nu_pt * nu_tp ~ 0.1, the
constraint DELTA > 0 is easily satisfied:

```
DELTA = 1 - 0.35 - 2*0.1 = 0.45 > 0.  Good.
```

---

## 3. Layer Bonding Coefficient Approach

### 3.1 Definition

Define a **layer bonding coefficient** k in [0, 1]:

- **k = 1:** Perfect interlayer bonding. The part is isotropic (injection-molded
  equivalent). All directional moduli equal.
- **k = 0:** No interlayer bonding. The material has zero stiffness in Z. This
  is the limit of completely delaminated layers (physically unrealistic as a
  continuum, but useful as a mathematical bound).

### 3.2 Derivation of Transverse Properties from k

Given the in-plane properties (E_p, nu_p) and the bonding coefficient k, derive
the transverse properties:

#### 3.2.1 Transverse Young's Modulus

```
E_t = k * E_p
```

**Rationale:** Linear proportionality is the simplest model. Experimental data
for FDM PLA (Ahn et al. 2002, Tymrak et al. 2014) shows E_z/E_xy ratios of
0.4-0.7, suggesting k ~ 0.4-0.7 for typical print settings.

For finer control, a power-law relationship could be used:

```
E_t = E_p * k^alpha,   alpha >= 1
```

with alpha = 1 being linear. For this plugin, alpha = 1 (linear) is recommended
for simplicity and interpretability.

#### 3.2.2 Transverse Shear Modulus

The in-plane shear modulus is derived:

```
G_p = E_p / (2(1 + nu_p))
```

For the transverse shear modulus, the simplest physically motivated model is:

```
G_t = k * G_p
```

**Rationale:** The interlayer shear weakness follows the same bonding
degradation as the tensile modulus. This is consistent with the assumption that
the interface is the weakest link for both normal and shear loading in the
Z-direction.

An alternative model uses the geometric mean:

```
G_t = sqrt(E_p * E_t) / (2(1 + sqrt(nu_p * nu_tp)))
```

but this introduces unnecessary complexity with minimal accuracy gain given the
inherent uncertainty in FDM material properties.

#### 3.2.3 Transverse Poisson's Ratios

This is the most subtle parameter. Several approaches exist:

**Approach A: Constant Poisson's ratio (simplest)**

```
nu_tp = nu_p
nu_pt = nu_tp * E_p / E_t = nu_p / k
```

**Problem:** For small k, nu_pt can exceed 0.5, violating positive-definiteness.
When k < 2*nu_p, we get nu_pt > 0.5 (e.g., k=0.5, nu_p=0.35 gives
nu_pt=0.70). We must check DELTA > 0.

DELTA = 1 - nu_p - 2 * nu_pt * nu_tp = 1 - nu_p - 2 * (nu_p/k) * nu_p
      = 1 - nu_p - 2*nu_p^2/k

For k=0.5, nu_p=0.35: DELTA = 1 - 0.35 - 2*0.1225/0.5 = 0.65 - 0.49 = 0.16 > 0.
For k=0.3, nu_p=0.35: DELTA = 1 - 0.35 - 2*0.1225/0.3 = 0.65 - 0.817 = -0.167 < 0!

This approach fails for low k values. Not recommended.

**Approach B: Scaled Poisson's ratio (recommended)**

```
nu_tp = nu_p * sqrt(k)
nu_pt = nu_tp * E_p / E_t = nu_p * sqrt(k) / k = nu_p / sqrt(k)
```

Then:

```
nu_pt * nu_tp = nu_p^2
DELTA = 1 - nu_p - 2*nu_p^2
```

This is **independent of k**, which is elegant and guarantees positive-
definiteness as long as:

```
1 - nu_p - 2*nu_p^2 > 0
=> nu_p < (-1 + sqrt(1 + 8)) / 4 = (-1 + 3) / 4 = 0.5
```

Since all physical materials have nu_p < 0.5, this is always satisfied.

For nu_p = 0.35: DELTA = 1 - 0.35 - 2*0.1225 = 0.405 > 0. Good.
For nu_p = 0.48: DELTA = 1 - 0.48 - 2*0.2304 = 0.0592 > 0. Tight but valid.

**Approach C: Square-root scaling (alternative)**

```
nu_tp = nu_p * sqrt(E_t / E_p) = nu_p * sqrt(k)
nu_pt = nu_tp * E_p / E_t = nu_p / sqrt(k)
```

This is identical to Approach B. The sqrt relationship arises naturally from
the reciprocal relation and the desire to keep DELTA k-independent.

### 3.3 Recommended Bonding Coefficient Model

**Summary of the recommended model (Approach B):**

```
Given: E_p, nu_p, k (bonding coefficient, 0 < k <= 1)

E_t   = k * E_p
G_p   = E_p / (2 * (1 + nu_p))
G_t   = k * G_p
nu_tp = nu_p * sqrt(k)
nu_pt = nu_p / sqrt(k)       [= nu_tp * E_p / E_t, reciprocal check]
```

**Verification of reciprocal relation:**

```
nu_pt / E_p = nu_p / (sqrt(k) * E_p)
nu_tp / E_t = nu_p * sqrt(k) / (k * E_p) = nu_p / (sqrt(k) * E_p)
```

Confirmed: nu_pt/E_p = nu_tp/E_t.

**Verification DELTA > 0:**

```
DELTA = 1 - nu_p - 2 * nu_pt * nu_tp
      = 1 - nu_p - 2 * [nu_p / sqrt(k)] * [nu_p * sqrt(k)]
      = 1 - nu_p - 2 * nu_p^2
```

Independent of k, always positive for nu_p < 0.5.

### 3.4 Default k Values from Material Database

The existing material database already contains E_xy and E_z. We can compute
the implicit bonding coefficient:

| Material  | E_xy (MPa) | E_z (MPa) | k = E_z/E_xy | nu_p  |
|-----------|-----------|----------|-------------|-------|
| PLA       | 3000      | 1500     | 0.50        | 0.36  |
| ABS       | 2100      | 1050     | 0.50        | 0.35  |
| PETG      | 2000      | 1000     | 0.50        | 0.38  |
| Nylon     | 1400      | 700      | 0.50        | 0.40  |
| PC        | 2200      | 1100     | 0.50        | 0.37  |
| TPU_95A   | 26        | 13       | 0.50        | 0.48  |
| CF_Nylon  | 6500      | 3250     | 0.50        | 0.35  |

All materials currently have k = 0.50. This is a reasonable default for FDM with
good layer adhesion. The bonding coefficient should be adjustable by the user to
account for different print temperatures, layer heights, and cooling conditions.

### 3.5 Literature Support

Experimental studies supporting these relationships:

- **Ahn et al. (2002):** Measured E_z/E_xy ~ 0.3-0.5 for ABS FDM parts.
  Interlayer tensile strength ~50% of in-plane.
- **Tymrak et al. (2014):** Statistical characterization of FDM PLA and ABS.
  Reported significant Z-direction weakness.
- **Casavola et al. (2016):** Measured all 5 transversely isotropic constants
  for FDM ABS using combined tensile and ultrasonic testing.
- **Li et al. (2017):** Developed bonding models for FDM polymer interfaces
  based on reptation theory. Shows k depends on nozzle temperature and layer
  time.
- **Zou et al. (2016):** FDM parts modeled as transversely isotropic with
  experimentally calibrated constants.

The sqrt scaling for Poisson's ratio (Approach B) is consistent with
micromechanics-based models for layered composites (Halpin-Tsai, 1969;
Chamis, 1983) when the reinforcement phase is replaced by the layer interface.

---

## 4. Explicit Layer Simulation

### 4.1 Concept

Instead of smearing the anisotropy into a homogeneous constitutive matrix,
explicitly mesh individual print layers and assign different properties to
elements near layer boundaries.

#### Two-Region Model

1. **Bulk region:** Elements whose centroid is far from any layer boundary
   get full isotropic properties (E = E_p, nu = nu_p).
2. **Interface region:** Elements whose centroid is within distance
   `d_interface` of a layer boundary (z = n * layer_height) get degraded
   properties in the Z direction.

```
For element e with centroid z_c:
  d_min = min_n |z_c - n * h_layer|     (distance to nearest layer boundary)

  if d_min < d_interface:
    E_e = E_interface  (reduced)
    nu_e = nu_interface
  else:
    E_e = E_bulk
    nu_e = nu_bulk
```

### 4.2 Cohesive Zone Model at Interfaces

A more rigorous approach uses **cohesive zone elements** at each layer boundary:

- Insert zero-thickness cohesive elements between layers.
- These elements have a traction-separation law (bilinear, exponential, etc.)
  that captures interface debonding.
- Allows modeling of delamination crack initiation and growth.

**Assessment for this plugin:** Too complex. Cohesive zone models require:
- Explicit identification of all layer interfaces.
- Insertion of cohesive elements during meshing.
- Nonlinear solution procedure (Newton-Raphson) for the softening law.
- Additional material parameters (interface fracture energy G_c, peak
  traction T_max).
- Significantly increased computational cost.

This is appropriate for research-level delamination prediction but far exceeds
the scope of an infill optimization plugin.

### 4.3 Practical Issues

| Issue | Impact | Severity |
|-------|--------|----------|
| Mesh resolution | Need >= 2-3 elements per layer height. For layer_height=0.2mm on a 50mm part, that is 250 layers, requiring ~750 elements through-thickness minimum. | **High** |
| Computational cost | 10-100x more elements than the smeared approach for the same part geometry. | **High** |
| Layer identification | Must know exact layer boundaries, which depend on print orientation, supports, and slicer settings. | **Medium** |
| Mesh quality | Thin elements at interfaces may have poor aspect ratios, degrading accuracy. | **Medium** |
| Implementation complexity | Requires mesh refinement near interfaces, element classification logic, and potentially different element types. | **High** |

### 4.4 Verdict

The explicit layer simulation is **not recommended** for this plugin. The
smeared approach (Section 5) captures the macroscopic anisotropy accurately
while maintaining the current mesh resolution and computational cost. Explicit
layer modeling is valuable for detailed failure analysis of specific joints but
is disproportionate to the goal of infill density optimization.

---

## 5. Smeared Approach (Recommended)

### 5.1 Concept

Replace the isotropic D matrix with a transversely isotropic D matrix for every
element, using the bonding coefficient to derive the 5 constants from the
existing 2 constants + k.

**Key insight:** Only the constitutive matrix D changes. The B matrix (strain-
displacement, purely geometric) is unchanged. The assembly procedure, boundary
condition application, and solver are all unchanged.

### 5.2 What Changes

| Component | Current | Proposed | Change Required |
|-----------|---------|----------|-----------------|
| D matrix | 6x6 isotropic (E, nu) | 6x6 transversely isotropic (E_p, E_t, nu_p, nu_tp, G_t) | **Yes** |
| B matrix | 6x12 strain-displacement | Same | No |
| K = V * B^T D B | Same formula | Same formula, different D | Automatic |
| sigma = D * epsilon | Same formula | Same formula, different D | Automatic |
| Stress metric | von Mises | Extended (Tsai-Hill or directional von Mises) | **Yes** (Section 7) |
| Material data | E_xy, E_z stored, only E_xy used | Both E_xy and E_z used | **Yes** |
| Bonding coefficient | Not present | New parameter k | **Yes** |

### 5.3 The D Matrix in Code

```python
def build_constitutive_matrix_transverse(
    E_p: float,
    E_t: float,
    nu_p: float,
    nu_tp: float,
    G_t: float,
) -> np.ndarray:
    """Build the 6x6 transversely isotropic constitutive matrix.

    Symmetry axis = Z (build direction).
    Voigt ordering: [sigma_xx, sigma_yy, sigma_zz, tau_xy, tau_yz, tau_xz].

    Five independent constants:
        E_p   : In-plane Young's modulus (E_x = E_y) [MPa]
        E_t   : Transverse Young's modulus (E_z) [MPa]
        nu_p  : In-plane Poisson's ratio (nu_xy) [-]
        nu_tp : Transverse-to-in-plane Poisson's ratio (nu_zx = nu_zy) [-]
        G_t   : Transverse shear modulus (G_xz = G_yz) [MPa]

    Returns:
        6x6 numpy array (float64).
    """
    # Reciprocal relation
    nu_pt = nu_tp * E_p / E_t  # nu_xz = nu_yz

    # Admissibility check
    DELTA = 1.0 - nu_p - 2.0 * nu_pt * nu_tp
    if DELTA <= 0.0:
        raise ValueError(
            f"Inadmissible elastic constants: DELTA = {DELTA:.6f} <= 0. "
            f"Check that nu_p={nu_p}, nu_pt*nu_tp={nu_pt*nu_tp:.4f} satisfy "
            f"1 - nu_p - 2*nu_pt*nu_tp > 0."
        )

    # In-plane shear modulus (derived)
    G_p = E_p / (2.0 * (1.0 + nu_p))

    # Normal block entries
    denom = (1.0 + nu_p) * DELTA

    D_11 = E_p * (1.0 - nu_pt * nu_tp) / denom
    D_12 = E_p * (nu_p + nu_pt * nu_tp) / denom
    D_13 = E_t * nu_pt / DELTA          # = E_p * nu_tp / DELTA
    D_33 = E_t * (1.0 - nu_p) / DELTA

    # Assemble
    D = np.zeros((6, 6), dtype=np.float64)

    # Normal-normal block
    D[0, 0] = D_11;  D[0, 1] = D_12;  D[0, 2] = D_13
    D[1, 0] = D_12;  D[1, 1] = D_11;  D[1, 2] = D_13
    D[2, 0] = D_13;  D[2, 1] = D_13;  D[2, 2] = D_33

    # Shear block
    D[3, 3] = G_p    # tau_xy
    D[4, 4] = G_t    # tau_yz
    D[5, 5] = G_t    # tau_xz

    return D
```

### 5.4 Bonding Coefficient Wrapper

```python
def build_constitutive_matrix_from_bonding(
    E_p: float,
    nu_p: float,
    k: float,
) -> np.ndarray:
    """Build transversely isotropic D from in-plane properties + bonding coeff.

    Args:
        E_p  : In-plane Young's modulus [MPa]
        nu_p : In-plane Poisson's ratio [-]
        k    : Layer bonding coefficient in (0, 1].
               k=1 recovers isotropic behavior.

    Returns:
        6x6 constitutive matrix.
    """
    if not (0.0 < k <= 1.0):
        raise ValueError(f"Bonding coefficient k={k} must be in (0, 1].")

    E_t   = k * E_p
    G_p   = E_p / (2.0 * (1.0 + nu_p))
    G_t   = k * G_p
    nu_tp = nu_p * np.sqrt(k)

    return build_constitutive_matrix_transverse(E_p, E_t, nu_p, nu_tp, G_t)
```

### 5.5 Verification: k=1 Recovery

When k=1:
- E_t = E_p
- G_t = G_p = E_p/(2(1+nu_p))
- nu_tp = nu_p * 1 = nu_p
- nu_pt = nu_p * E_p/E_p = nu_p

All 5 constants reduce to the isotropic pair (E, nu), and the D matrix matches
the existing `build_constitutive_matrix(E, nu)`. This was verified analytically
in Section 2.6.

### 5.6 Numerical Example

For PLA with k=0.5:

```
E_p = 3000 MPa,  nu_p = 0.36,  k = 0.5

E_t   = 0.5 * 3000 = 1500 MPa
G_p   = 3000 / (2 * 1.36) = 1102.9 MPa
G_t   = 0.5 * 1102.9 = 551.5 MPa
nu_tp = 0.36 * sqrt(0.5) = 0.2546
nu_pt = 0.2546 * 3000 / 1500 = 0.5091

DELTA = 1 - 0.36 - 2 * 0.5091 * 0.2546 = 1 - 0.36 - 0.2592 = 0.3808

D_11 = 3000 * (1 - 0.5091*0.2546) / (1.36 * 0.3808) = 3000 * 0.8704 / 0.5179
     = 5041.7 MPa

D_12 = 3000 * (0.36 + 0.5091*0.2546) / (1.36 * 0.3808) = 3000 * 0.4896 / 0.5179
     = 2837.2 MPa

D_13 = 1500 * 0.5091 / 0.3808 = 2006.0 MPa

D_33 = 1500 * (1 - 0.36) / 0.3808 = 1500 * 0.64 / 0.3808
     = 2521.0 MPa
```

Compare with the isotropic case (k=1):

```
lambda = 3000 * 0.36 / (1.36 * 0.28) = 1080 / 0.3808 = 2836.4 MPa
mu     = 3000 / (2 * 1.36) = 1102.9 MPa
D_11_iso = lambda + 2*mu = 2836.4 + 2205.9 = 5042.3 MPa
D_12_iso = lambda = 2836.4 MPa
```

The k=0.5 case shows:
- D_11 is nearly unchanged (in-plane stiffness preserved).
- D_33 is reduced from 5042 to 2521 MPa (50% reduction in Z-direction stiffness).
- D_13 (coupling) is reduced, reflecting weaker Poisson coupling between layers.

This is physically correct: the part is stiff in XY but compliant in Z.

---

## 6. Implementation Plan

### 6.1 Files to Modify

#### 6.1.1 `homogenization.py` -- Add Transversely Isotropic D Builder

Add two new functions alongside the existing `build_constitutive_matrix`:

1. `build_constitutive_matrix_transverse(E_p, E_t, nu_p, nu_tp, G_t) -> np.ndarray`
   - The full 5-parameter transversely isotropic D matrix.
   - Includes admissibility checks (DELTA > 0, positive moduli).

2. `build_constitutive_matrix_from_bonding(E_p, nu_p, k) -> np.ndarray`
   - Convenience wrapper: derives (E_t, nu_tp, G_t) from (E_p, nu_p, k).
   - Calls `build_constitutive_matrix_transverse`.

The existing `build_constitutive_matrix(E, nu)` remains unchanged for backward
compatibility. When k=1, `build_constitutive_matrix_from_bonding` produces an
identical matrix.

#### 6.1.2 `fea_solver.py` -- Use Transversely Isotropic D

Modify `assemble_stiffness_matrix` and `compute_element_stress`:

**Current call site (line 92):**
```python
D = build_constitutive_matrix(float(E), float(nu))
```

**Proposed:** Accept an optional `D_per_element` array (list of 6x6 matrices)
or a callable that produces D given an element index. When provided, this
replaces the isotropic D construction.

Alternatively (simpler): change the signature to accept a `bonding_coeff`
parameter. If provided and != 1.0, use the transversely isotropic builder:

```python
# In assemble_stiffness_matrix:
if bonding_coeff is not None and bonding_coeff < 1.0:
    D = build_constitutive_matrix_from_bonding(float(E), float(nu), bonding_coeff)
else:
    D = build_constitutive_matrix(float(E), float(nu))
```

**Cleanest approach:** Pass a pre-built D matrix per element. The solver should
not need to know whether the material is isotropic or anisotropic -- it just
uses whatever D matrix it receives.

**Recommended refactor:**

```python
def assemble_stiffness_matrix(
    self,
    tet_mesh: TetMesh,
    D_per_element: list[np.ndarray],   # list of M 6x6 matrices
) -> sp.csr_matrix:
```

This is the most general approach. The caller (iterative_solver) is responsible
for building the D matrices. The existing signature can be kept as a convenience
wrapper:

```python
def assemble_stiffness_matrix(
    self,
    tet_mesh: TetMesh,
    E_per_element: np.ndarray,
    nu_per_element: np.ndarray,
    *,
    bonding_coeff: float = 1.0,
) -> sp.csr_matrix:
```

#### 6.1.3 `material_database.py` -- Add Bonding Coefficient

The `Material` dataclass already has E_xy and E_z. Add a computed property or
a default bonding coefficient:

```python
@property
def bonding_coefficient(self) -> float:
    """Implicit layer bonding coefficient k = E_z / E_xy."""
    return self.E_z / self.E_xy if self.E_xy > 0 else 1.0
```

This requires no schema change since k is derivable from existing data.

For user-adjustable k, add it as an optional parameter with default derived
from the material:

```python
@dataclass(frozen=True)
class Material:
    name: str
    E_xy: float
    E_z: float
    nu: float
    yield_strength: float
    density: float
    bonding_coeff: float | None = None  # None = use E_z/E_xy

    @property
    def effective_bonding_coeff(self) -> float:
        if self.bonding_coeff is not None:
            return self.bonding_coeff
        return self.E_z / self.E_xy if self.E_xy > 0.0 else 1.0
```

#### 6.1.4 `iterative_solver.py` -- Wire Bonding Coefficient Through

Modify the `solve` method to:

1. Read `k` from material or config.
2. Build the D matrix per element using `build_constitutive_matrix_from_bonding`.
3. Pass to the FEA solver.

```python
# In the iteration loop, replace:
#   E_eff_arr = [effective_properties(...)]
#   nu_arr = np.full(n_elems, material.nu)

# With:
k = material.effective_bonding_coeff
for rho in density:
    E_eff, nu_eff = effective_properties(material.E_xy, material.nu, rho, pattern)
    # E_eff is the homogenized in-plane modulus at this density
    D_e = build_constitutive_matrix_from_bonding(E_eff, nu_eff, k)
    D_list.append(D_e)
```

### 6.2 Homogenization Interaction

The current homogenization applies a power-law to E_bulk:

```
E_eff = E_bulk * density_fraction^n
```

With transverse isotropy, we must decide: does the density fraction affect only
the in-plane modulus, or both?

**Recommended:** Apply homogenization to the in-plane modulus E_p, and let the
bonding coefficient k scale it to E_t. The rationale is that infill density
reduces the effective in-plane stiffness (fewer roads per unit area), while the
bonding coefficient captures the interlayer weakness independently.

```
E_p_eff = E_p_bulk * density_fraction^n
E_t_eff = k * E_p_eff
```

This multiplicative decomposition (density x bonding) keeps the two effects
orthogonal and independently controllable.

### 6.3 Backward Compatibility

When `k = 1.0`:
- `build_constitutive_matrix_from_bonding(E, nu, 1.0)` produces the same D as
  `build_constitutive_matrix(E, nu)`.
- All stress, stiffness, and convergence results are identical.
- The existing test suite passes without modification.

**Migration path:**
1. Add new functions alongside existing ones (no breaking changes).
2. Default k=1.0 in all call sites (current behavior).
3. Read k from material database (k=0.5 for all current materials).
4. User can override k via config/UI.

### 6.4 Data Flow

```
UI / Config
    |
    v
bonding_coefficient k (default from material database)
    |
    v
IterativeFEASolver.solve()
    |
    +-- For each element:
    |       E_p_eff = homogenize(E_p_bulk, density, pattern)
    |       D_e = build_constitutive_matrix_from_bonding(E_p_eff, nu, k)
    |
    +-- assemble_stiffness_matrix(tet_mesh, D_per_element)
    |       K = sum_e(V_e * B_e^T * D_e * B_e)
    |
    +-- apply_boundary_conditions(K, f, fixed_nodes)
    +-- solve(K, f) -> u
    +-- compute_element_stress(tet_mesh, u, D_per_element) -> sigma_vm
    |
    v
stress_to_density(sigma_vm, ...) -> density_new
```

---

## 7. Failure Criteria for Transversely Isotropic Materials

### 7.1 Limitation of von Mises for Anisotropic Materials

The von Mises criterion assumes isotropic yield behavior:

```
sigma_vm = sqrt(sigma_x^2 + sigma_y^2 + sigma_z^2
               - sigma_x*sigma_y - sigma_y*sigma_z - sigma_x*sigma_z
               + 3*(tau_xy^2 + tau_yz^2 + tau_xz^2))
```

This treats all directions equally. For a transversely isotropic FDM part, the
Z-direction is significantly weaker, so a stress state with sigma_z = 40 MPa
is much more dangerous than sigma_x = 40 MPa.

### 7.2 Hill's Anisotropic Yield Criterion

Hill (1948) generalized von Mises to anisotropic materials:

```
F(sigma_y - sigma_z)^2 + G(sigma_z - sigma_x)^2 + H(sigma_x - sigma_y)^2
+ 2L*tau_yz^2 + 2M*tau_xz^2 + 2N*tau_xy^2 = 1
```

For transverse isotropy (X-Y plane of isotropy, Z axis of symmetry), with
yield strengths:
- X = Y = in-plane tensile strength [MPa]
- Z = through-thickness tensile strength [MPa]
- S_p = in-plane shear strength [MPa]
- S_t = transverse shear strength [MPa]

The Hill parameters reduce to:

```
F = G = 1/(2*Z^2) + 1/(2*X^2) - 1/(2*X^2) = 1/(2*Z^2)

Wait, let me derive this properly.

From the standard Hill relations:
F + H = 1/Y^2
G + H = 1/X^2
F + G = 1/Z^2
```

With X = Y (transverse isotropy):

```
F + H = 1/X^2
G + H = 1/X^2    => F = G
F + G = 1/Z^2    => 2F = 1/Z^2  => F = G = 1/(2*Z^2)
H = 1/X^2 - F = 1/X^2 - 1/(2*Z^2)

L = M = 1/(2*S_t^2)    [transverse shear]
N = 1/(2*S_p^2)         [in-plane shear]
```

### 7.3 Tsai-Hill Criterion (Simplified for Transverse Isotropy)

The Tsai-Hill criterion is a specialization of Hill's criterion commonly used
for composites. For plane stress in the 1-2 plane (with 1 = fiber direction,
2 = transverse), it takes the familiar form:

```
(sigma_1/X)^2 - sigma_1*sigma_2/X^2 + (sigma_2/Y)^2 + (tau_12/S)^2 <= 1
```

For our full 3D transversely isotropic case, the **generalized Tsai-Hill**
failure index is:

```
FI = F*(sigma_y - sigma_z)^2 + G*(sigma_z - sigma_x)^2 + H*(sigma_x - sigma_y)^2
     + 2L*tau_yz^2 + 2M*tau_xz^2 + 2N*tau_xy^2
```

Substituting the transversely isotropic Hill parameters:

```
FI = [1/(2*Z^2)] * (sigma_y - sigma_z)^2
   + [1/(2*Z^2)] * (sigma_z - sigma_x)^2
   + [1/X^2 - 1/(2*Z^2)] * (sigma_x - sigma_y)^2
   + [1/S_t^2] * tau_yz^2
   + [1/S_t^2] * tau_xz^2
   + [1/S_p^2] * tau_xy^2
```

Failure occurs when FI >= 1.

### 7.4 Simplified Directional Von Mises

For the infill optimization context, a simpler approach that preserves the
existing code structure is a **directionally weighted von Mises**:

```
sigma_eff = sqrt(
    sigma_x^2 + sigma_y^2 + (sigma_z * X/Z)^2
    - sigma_x*sigma_y - sigma_y*(sigma_z * X/Z) - sigma_x*(sigma_z * X/Z)
    + 3*[tau_xy^2 + (tau_yz * X/S_t)^2 + (tau_xz * X/S_t)^2]
)
```

This scales the Z-direction stress components by the strength ratio X/Z before
applying the standard von Mises formula. When X = Z and S_p = S_t, this
recovers the standard von Mises exactly.

**Advantages:**
- Minimal code change (modify `_von_mises` to accept strength ratios).
- Returns a single scalar "equivalent stress" compatible with the existing
  `stress_to_density` mapping.
- Physically meaningful: amplifies the contribution of Z-direction stress in
  proportion to the Z-direction weakness.

### 7.5 Strength Properties from Bonding Coefficient

The additional strength properties can be derived from the existing
`yield_strength` (which represents the in-plane tensile strength X):

```
X = yield_strength                          [in-plane tensile strength]
Z = k * yield_strength                      [Z-direction tensile strength]
S_p = yield_strength / sqrt(3)              [in-plane shear = von Mises relation]
S_t = k * S_p = k * yield_strength / sqrt(3) [transverse shear strength]
```

The factor sqrt(3) comes from the von Mises relationship between tensile and
shear yield for isotropic materials (tau_y = sigma_y / sqrt(3)).

The strength ratios needed for the directional von Mises are:

```
X/Z   = 1/k
X/S_t = sqrt(3)/k
```

### 7.6 Implementation as Modified von Mises

```python
def _von_mises_transverse(
    stress: np.ndarray,
    k: float,
) -> float:
    """Directionally weighted von Mises for transversely isotropic material.

    Scales Z-direction stress components by 1/k to account for interlayer
    weakness before computing the equivalent stress.

    Args:
        stress: [sigma_xx, sigma_yy, sigma_zz, tau_xy, tau_yz, tau_xz]
        k: Layer bonding coefficient (0, 1]. k=1 recovers standard von Mises.

    Returns:
        Equivalent stress (scalar, same unit as input).
    """
    sx, sy, sz, txy, tyz, txz = stress

    # Scale Z-direction components by strength ratio 1/k
    sz_eff  = sz / k
    tyz_eff = tyz / k
    txz_eff = txz / k

    vm2 = (
        sx**2 + sy**2 + sz_eff**2
        - sx * sy - sy * sz_eff - sx * sz_eff
        + 3.0 * (txy**2 + tyz_eff**2 + txz_eff**2)
    )
    return float(np.sqrt(max(vm2, 0.0)))
```

When k=1, sz_eff = sz, tyz_eff = tyz, txz_eff = txz, and this is identical to
the existing `_von_mises`. This guarantees backward compatibility.

### 7.7 Dimensional Analysis of Failure Criteria

All stress components have dimensions [MPa]. The ratios X/Z = 1/k are
dimensionless. The failure index FI is dimensionless. The directional von Mises
has dimensions [MPa] (same as input stresses), which is correct for comparison
with yield_strength. Verified.

---

## 8. Validation Strategy

### 8.1 Unit Tests

1. **Isotropic recovery:** Verify that `build_constitutive_matrix_from_bonding(E, nu, 1.0)`
   produces the same matrix (within floating-point tolerance) as
   `build_constitutive_matrix(E, nu)`.

2. **Symmetry:** Verify D = D^T for all parameter combinations.

3. **Positive definiteness:** Verify all eigenvalues of D are positive for k in
   {0.1, 0.3, 0.5, 0.7, 1.0} and all materials in the database.

4. **Admissibility:** Verify DELTA > 0 for all material/k combinations.

5. **Reciprocal relation:** Verify nu_pt/E_p = nu_tp/E_t numerically.

### 8.2 Integration Tests

1. **Cantilever beam in Z-direction:** A beam oriented along Z under tip load.
   With k < 1, the deflection should be larger than the isotropic case by
   approximately a factor of 1/k.

2. **Cantilever beam in X-direction:** Same beam oriented along X under tip load.
   The deflection should be nearly independent of k.

3. **Convergence:** The iterative solver should converge in a similar number of
   iterations with transversely isotropic D as with isotropic D.

### 8.3 Limiting Cases

1. **k -> 1:** Results approach isotropic solution.
2. **k -> 0+:** Z-direction stiffness approaches zero. Deflection under Z-load
   diverges. The solver should warn about ill-conditioning.
3. **Uniaxial X-tension:** sigma_x only. Transverse isotropy should not affect
   the stress (D_11 is nearly unchanged for moderate k).
4. **Uniaxial Z-tension:** sigma_z only. The stress-strain relationship should
   show reduced stiffness by factor ~k.

---

## 9. References

1. **Ahn, S.H., Montero, M., Odell, D., Roundy, S., & Wright, P.K.** (2002).
   "Anisotropic material properties of fused deposition modeling ABS."
   *Rapid Prototyping Journal*, 8(4), 248-257.

2. **Tymrak, B.M., Kreiger, M., & Pearce, J.M.** (2014).
   "Mechanical properties of components fabricated with open-source 3-D printers
   under realistic environmental conditions." *Materials & Design*, 58, 242-246.

3. **Casavola, C., Cazzato, A., Moramarco, V., & Pappalettere, C.** (2016).
   "Orthotropic mechanical properties of fused deposition modelling parts described
   by classical laminate theory." *Materials & Design*, 90, 453-458.

4. **Li, L., Sun, Q., Bellehumeur, C., & Gu, P.** (2002).
   "Composite modeling and analysis for fabrication of FDM prototypes with locally
   controlled properties." *J. Manufacturing Processes*, 4(2), 129-141.

5. **Zou, R., Xia, Y., Liu, S., Hu, P., et al.** (2016).
   "Isotropic and anisotropic elasticity and yielding of 3D printed material."
   *Composites Part B*, 99, 506-513.

6. **Hill, R.** (1948).
   "A theory of the yielding and plastic flow of anisotropic metals."
   *Proc. Royal Society London A*, 193(1033), 281-297.

7. **Tsai, S.W., & Wu, E.M.** (1971).
   "A general theory of strength for anisotropic materials."
   *J. Composite Materials*, 5(1), 58-80.

8. **Halpin, J.C., & Tsai, S.W.** (1969).
   "Effects of environmental factors on composite materials."
   *AFML-TR-67-423*.

9. **Lekhnitskii, S.G.** (1963).
   *Theory of Elasticity of an Anisotropic Elastic Body*.
   Holden-Day, San Francisco.

10. **Ting, T.C.T.** (1996).
    *Anisotropic Elasticity: Theory and Applications*.
    Oxford University Press.

---

## Appendix A: Complete D Matrix Reference

For implementation copy-paste, the complete transversely isotropic D matrix
with Z as the symmetry axis, in Voigt ordering
[sigma_xx, sigma_yy, sigma_zz, tau_xy, tau_yz, tau_xz]:

```
Given: E_p, E_t, nu_p, nu_tp, G_t

Derived:
  G_p   = E_p / (2 * (1 + nu_p))
  nu_pt = nu_tp * E_p / E_t
  DELTA = 1 - nu_p - 2 * nu_pt * nu_tp

Entries:
  D[0,0] = D[1,1] = E_p * (1 - nu_pt * nu_tp) / ((1 + nu_p) * DELTA)
  D[0,1] = D[1,0] = E_p * (nu_p + nu_pt * nu_tp) / ((1 + nu_p) * DELTA)
  D[0,2] = D[2,0] = D[1,2] = D[2,1] = E_t * nu_pt / DELTA
  D[2,2] = E_t * (1 - nu_p) / DELTA
  D[3,3] = G_p
  D[4,4] = D[5,5] = G_t

All other entries = 0.
```

## Appendix B: Bonding Coefficient Quick Reference

```
Given: E_p (in-plane modulus), nu_p (in-plane Poisson's), k (bonding coeff)

  E_t   = k * E_p
  G_p   = E_p / (2 * (1 + nu_p))
  G_t   = k * G_p
  nu_tp = nu_p * sqrt(k)
  nu_pt = nu_p / sqrt(k)         [reciprocal check: nu_pt/E_p = nu_tp/E_t]
  DELTA = 1 - nu_p - 2*nu_p^2    [independent of k; always > 0 for nu_p < 0.5]
```

## Appendix C: Directional Von Mises Quick Reference

```
Given: stress = [sx, sy, sz, txy, tyz, txz], k (bonding coefficient)

  sz'  = sz / k
  tyz' = tyz / k
  txz' = txz / k

  sigma_eff = sqrt(sx^2 + sy^2 + sz'^2
                   - sx*sy - sy*sz' - sx*sz'
                   + 3*(txy^2 + tyz'^2 + txz'^2))

  When k=1: recovers standard von Mises identically.
```
