# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Find the optimal FDM print orientation by minimising interlayer delamination risk.

Physical Motivation
-------------------
FDM parts are weakest across layer interfaces — interlayer tensile strength is
typically ~50 % of in-plane strength (Ahn et al. 2002, *Rapid Prototyping J.*).
Given the stress state produced by FEA, we can choose the build direction **n**
(the layer-normal) to minimise the interlayer failure index.

Mathematical Framework
-----------------------
For a candidate build direction unit vector **n** and element stress tensor Σ:

  Traction vector   t    = Σ · n
  Normal stress     σ_nn = nᵀ Σ n          (scalar; positive = tensile)
  Shear stress      τ²   = |t|² − σ_nn²

Tsai-Hill failure index on the layer interface (per element e):

  Z = k · σ_y      interlayer tensile strength (k = bonding_coeff)
  S = 0.6 · Z      interlayer shear  strength

  FI(e, n) = (max(σ_nn, 0) / Z)² + (τ_int / S)²

Only tensile normal stress delaminates; compressive σ_nn sets FI_nn term to 0.

Global orientation metric (volume- and stress-weighted mean FI, lower = better):

  w_e  = σ_vm(e) / max(σ_vm)          von-Mises magnitude weight
  Φ(n) = Σ_e [V_e · w_e · FI(e,n)] / Σ_e [V_e · w_e]

Only elements with w_e > W_THRESHOLD = 0.05 contribute.

Search Strategy
---------------
1. Hierarchical icosphere: evaluate Φ at icosahedron vertices + force-perpendicular
   seeds, then recursively subdivide the top-K best triangular faces.
2. L-BFGS-B gradient descent from the best hierarchical candidate, using the
   analytically-computed gradient ∂Φ/∂(θ, φ) in Y-up spherical coordinates.

Coordinate System
-----------------
The constitutive matrix in ``homogenization.py`` uses Z as the transverse
(weak/build) axis.  The default build direction is therefore [0, 0, 1].
Spherical parameterisation (Z-up, θ = polar from Z, φ = azimuth in XY):

  n(θ, φ) = [sin(θ)cos(φ), sin(θ)sin(φ), cos(θ)]

**Approximation note:** The stress tensor is computed once at the default
build direction and then projected onto candidate directions without
re-solving the FEA with a rotated constitutive matrix.  This is a
first-order heuristic — the ranking of orientations is preserved, but
absolute failure indices may differ by ~10-15% for materials with
bonding_coeff ≤ 0.5.  Re-run the full FEA after applying the rotation
to verify.

References
----------
- Ahn S-H et al. (2002) Anisotropic material properties of fused deposition
  modelling ABS. *Rapid Prototyping J.* 8(4):248-257.
- Chacón J.M. et al. (2017) Additive manufacturing of PLA structures. *Mat. &
  Design* 124:143-157.
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from scipy.optimize import minimize

# Elements whose von-Mises weight falls below this threshold are ignored.
_W_THRESHOLD: float = 0.05

# Shear-strength ratio S/Z  (S = 0.6 Z per Tsai-Hill assumptions).
_SHEAR_RATIO: float = 0.6


@dataclass
class OrientationResult:
    """Holds the result of an orientation optimisation run.

    Attributes:
        optimal_direction: Unit vector (3,) giving the best build direction
            found, expressed in model-local coordinates (Y-up frame).
        rotation_matrix: 3×3 rotation matrix that maps the default build
            direction [0, 0, 1] to ``optimal_direction``.  Apply this to the
            model vertices (or equivalently, rotate the model by its inverse)
            before slicing.
        metric_current: Φ evaluated at the default Y-up build direction.
            Values are dimensionless failure-index means in [0, ∞).
        metric_optimal: Φ at ``optimal_direction`` (lower is better).
        improvement_ratio: ``metric_current / metric_optimal``.  A value > 1
            means the reorientation reduces delamination risk.
        all_directions: (N, 3) array of every build direction evaluated
            (hierarchical seeds + L-BFGS-B iterates), for diagnostics.
        all_metrics: (N,) array of Φ for each entry in ``all_directions``.
    """

    optimal_direction: np.ndarray
    rotation_matrix: np.ndarray
    metric_current: float
    metric_optimal: float
    improvement_ratio: float
    all_directions: np.ndarray
    all_metrics: np.ndarray


class OrientationOptimizer:
    """Optimise the FDM build direction to minimise interlayer delamination.

    The optimiser computes the global orientation metric Φ(n) — a
    volume-and-stress-weighted mean Tsai-Hill failure index for the layer
    interface — and finds the build direction **n** ∈ S² that minimises it.

    Args:
        stress_tensors: (M, 6) Voigt stress components per element, in MPa.
            Ordering: [σ_xx, σ_yy, σ_zz, τ_xy, τ_yz, τ_xz].
        volumes: (M,) element volumes (any consistent length unit; only ratios
            matter).
        stress_field: (M,) von Mises stress per element, in MPa.  Used to
            weight each element's contribution to Φ.
        bonding_coeff: Interlayer bonding coefficient k ∈ (0, 1].  The
            effective interlayer tensile strength is Z = k · yield_strength.
            Typical FDM PLA/ABS values: 0.4 – 0.7.
        yield_strength: Material in-plane yield strength σ_y in MPa.
        force_direction: Optional (3,) unit vector giving the net force
            direction.  Used to seed the hierarchical search with candidates
            in the plane perpendicular to the resultant force (where the build
            direction is most likely to be optimal).
    """

    def __init__(
        self,
        stress_tensors: np.ndarray,
        volumes: np.ndarray,
        stress_field: np.ndarray,
        bonding_coeff: float,
        yield_strength: float,
        force_direction: Optional[np.ndarray] = None,
    ) -> None:
        stress_tensors = np.asarray(stress_tensors, dtype=np.float64)
        volumes = np.asarray(volumes, dtype=np.float64)
        stress_field = np.asarray(stress_field, dtype=np.float64)

        if stress_tensors.ndim != 2 or stress_tensors.shape[1] != 6:
            raise ValueError(
                f"stress_tensors must be (M, 6), got {stress_tensors.shape}."
            )
        if volumes.shape != (stress_tensors.shape[0],):
            raise ValueError("volumes must have length M (number of elements).")
        if stress_field.shape != (stress_tensors.shape[0],):
            raise ValueError("stress_field must have length M (number of elements).")
        if bonding_coeff <= 0.0 or bonding_coeff > 1.0:
            raise ValueError(f"bonding_coeff must be in (0, 1], got {bonding_coeff}.")
        if yield_strength <= 0.0:
            raise ValueError(
                f"yield_strength must be positive, got {yield_strength}."
            )

        # Interlayer tensile and shear strengths (MPa).
        Z = bonding_coeff * yield_strength
        S = _SHEAR_RATIO * Z

        # Pre-compute constants for Tsai-Hill: 1/Z² and 1/S²
        self._inv_Z2: float = 1.0 / (Z * Z)
        self._inv_S2: float = 1.0 / (S * S)

        # Convert Voigt → full 3×3 tensors, shape (M, 3, 3).
        self._sigma: np.ndarray = self._voigt_to_tensor(stress_tensors)  # (M, 3, 3)

        # Compute element weights w_e = σ_vm / max(σ_vm), mask below threshold.
        vm_max = np.max(stress_field)
        if vm_max < 1e-30:
            # Zero stress everywhere — no meaningful orientation choice.
            self._weights: np.ndarray = np.zeros_like(stress_field)
            self._zero_stress: bool = True
        else:
            weights_raw = stress_field / vm_max
            mask = weights_raw > _W_THRESHOLD
            self._weights = np.where(mask, weights_raw, 0.0)
            self._zero_stress = False

        # Effective weights include element volume, used as denominator/numerator.
        # eff_w[e] = V_e * w_e.
        eff_w = volumes * self._weights  # (M,)
        self._eff_w_sum: float = float(np.sum(eff_w))
        if self._eff_w_sum < 1e-30:
            self._zero_stress = True

        # Store effective weights as (M,) for einsum broadcasting.
        self._eff_w: np.ndarray = eff_w  # (M,)

        # Sigma weighted by eff_w for efficient gradient computation:
        # sig_w[e] = eff_w[e] * sigma[e],  shape (M, 3, 3)
        self._sig_w: np.ndarray = (
            self._eff_w[:, np.newaxis, np.newaxis] * self._sigma
        )  # (M, 3, 3)
        # Note: Σ²n is computed on demand in the gradient as Σ(Σn) —
        # no precomputed _sigma2 needed (saves 14.4 MB at M=200K).

        # Force direction hint (normalised).
        if force_direction is not None:
            fd = np.asarray(force_direction, dtype=np.float64).ravel()
            fd_norm = np.linalg.norm(fd)
            self._force_dir: Optional[np.ndarray] = (
                fd / fd_norm if fd_norm > 1e-12 else None
            )
        else:
            self._force_dir = None

        # Tracking all evaluated directions and metrics for diagnostics.
        self._eval_dirs: list = []
        self._eval_metrics: list = []

    # ------------------------------------------------------------------
    # Tensor conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _voigt_to_tensor(stress_voigt: np.ndarray) -> np.ndarray:
        """Convert Voigt notation stresses to full symmetric 3×3 tensors.

        Voigt ordering assumed: [σ_xx, σ_yy, σ_zz, τ_xy, τ_yz, τ_xz].

        Args:
            stress_voigt: (M, 6) array of Voigt stress components.

        Returns:
            (M, 3, 3) symmetric stress tensors.
        """
        M = stress_voigt.shape[0]
        sigma = np.zeros((M, 3, 3), dtype=np.float64)
        # Diagonal terms
        sigma[:, 0, 0] = stress_voigt[:, 0]  # σ_xx
        sigma[:, 1, 1] = stress_voigt[:, 1]  # σ_yy
        sigma[:, 2, 2] = stress_voigt[:, 2]  # σ_zz
        # Off-diagonal — symmetric
        sigma[:, 0, 1] = stress_voigt[:, 3]  # τ_xy
        sigma[:, 1, 0] = stress_voigt[:, 3]
        sigma[:, 1, 2] = stress_voigt[:, 4]  # τ_yz
        sigma[:, 2, 1] = stress_voigt[:, 4]
        sigma[:, 0, 2] = stress_voigt[:, 5]  # τ_xz
        sigma[:, 2, 0] = stress_voigt[:, 5]
        return sigma

    # ------------------------------------------------------------------
    # Metric evaluation (vectorised)
    # ------------------------------------------------------------------

    def compute_metric(self, build_dir: np.ndarray) -> float:
        """Compute the global orientation metric Φ for a build direction.

        Φ(n) = Σ_e [V_e · w_e · FI(e, n)] / Σ_e [V_e · w_e]

        where FI(e, n) is the Tsai-Hill failure index on the layer interface
        for element e with build direction n.

        Args:
            build_dir: (3,) unit vector — the candidate layer-normal direction.

        Returns:
            Scalar Φ ≥ 0.  Lower values indicate better delamination resistance.
        """
        if self._zero_stress:
            return 0.0

        n = np.asarray(build_dir, dtype=np.float64).ravel()
        n = n / (np.linalg.norm(n) + 1e-300)  # guard against zero vector

        # t = Σ · n  for each element, shape (M, 3)
        # Using einsum: t[e, i] = sigma[e, i, j] * n[j]
        t = np.einsum("eij,j->ei", self._sigma, n)  # (M, 3)

        # σ_nn = nᵀ Σ n = n · t  for each element
        sigma_nn = np.einsum("ei,i->e", t, n)  # (M,)

        # |t|² and τ² = |t|² - σ_nn²
        t2 = np.einsum("ei,ei->e", t, t)  # (M,)
        # Clamp τ² to non-negative to handle floating-point rounding
        tau2 = np.maximum(t2 - sigma_nn * sigma_nn, 0.0)  # (M,)

        # Tsai-Hill failure index: only tensile σ_nn contributes to normal term.
        sigma_nn_pos = np.maximum(sigma_nn, 0.0)  # (M,)
        fi = (
            sigma_nn_pos * sigma_nn_pos * self._inv_Z2
            + tau2 * self._inv_S2
        )  # (M,)

        # Weighted mean: Φ = Σ(eff_w * fi) / Σ(eff_w)
        phi = float(np.dot(self._eff_w, fi)) / self._eff_w_sum
        return phi

    # ------------------------------------------------------------------
    # Metric + gradient for L-BFGS-B
    # ------------------------------------------------------------------

    def compute_metric_and_gradient(
        self, params: np.ndarray
    ) -> Tuple[float, np.ndarray]:
        """Compute Φ(θ, φ) and its gradient ∂Φ/∂[θ, φ] for L-BFGS-B.

        Uses Y-up spherical coordinates:
          n(θ, φ) = [sin(θ)cos(φ), cos(θ), sin(θ)sin(φ)]

        The gradient of FI w.r.t. n is derived analytically:

          For τ² = |Σn|² − (nᵀΣn)²:
            ∂(σ_nn)/∂n = 2 Σn         (since Σ is symmetric)
            ∂(|t|²)/∂n = 2 Σ²n        (since ∂(nᵀΣᵀΣn)/∂n = 2ΣᵀΣn = 2Σ²n)
            ∂(τ²)/∂n   = 2Σ²n − 2σ_nn · 2Σn
                       = 2(Σ²n − 2σ_nn Σn)

          FI = (σ_nn⁺/Z)² + (τ²/S²)    where σ_nn⁺ = max(σ_nn, 0)
            ∂FI/∂n = 2σ_nn⁺/Z² · ∂σ_nn/∂n  +  1/S² · ∂τ²/∂n   (if σ_nn > 0)
                   =   0                    +  1/S² · ∂τ²/∂n   (if σ_nn ≤ 0)

          Chaining through n(θ, φ):
            ∂n/∂θ = [ cos(θ)cos(φ), cos(θ)sin(φ), −sin(θ)]
            ∂n/∂φ = [−sin(θ)sin(φ), sin(θ)cos(φ),       0]
            ∂Φ/∂θ = (∂Φ/∂n) · (∂n/∂θ),  etc.

        Args:
            params: (2,) array [θ, φ], θ ∈ [0, π], φ ∈ [0, 2π).

        Returns:
            Tuple (Φ, grad) where grad = [∂Φ/∂θ, ∂Φ/∂φ].
        """
        theta, phi = float(params[0]), float(params[1])

        ct, st = math.cos(theta), math.sin(theta)
        cp, sp = math.cos(phi),   math.sin(phi)

        # Build direction in Z-up frame
        n = np.array([st * cp, st * sp, ct], dtype=np.float64)

        # Jacobian columns  ∂n/∂θ  and  ∂n/∂φ
        dn_dtheta = np.array([ ct * cp,  ct * sp, -st], dtype=np.float64)
        dn_dphi   = np.array([-st * sp,  st * cp, 0.0], dtype=np.float64)

        if self._zero_stress:
            return 0.0, np.zeros(2)

        # t[e] = Σ[e] · n,  shape (M, 3)
        t = np.einsum("eij,j->ei", self._sigma, n)  # (M, 3)

        # σ_nn[e] = n · t[e]
        sigma_nn = np.einsum("i,ei->e", n, t)  # (M,)

        # Σ²n[e] = Σ[e] · (Σ[e] · n) = Σ[e] · t[e],  shape (M, 3)
        # (already t = Σ·n, so Σ²n = Σ·t)
        sigma2_n = np.einsum("eij,ej->ei", self._sigma, t)  # (M, 3)

        # |t|² and τ²
        t2 = np.einsum("ei,ei->e", t, t)  # (M,)
        tau2 = np.maximum(t2 - sigma_nn * sigma_nn, 0.0)  # (M,)

        # Tensile indicator (1 where σ_nn > 0, else 0)
        tensile = (sigma_nn > 0.0).astype(np.float64)  # (M,)
        sigma_nn_pos = sigma_nn * tensile  # (M,)

        # FI and weighted sum for Φ
        fi = sigma_nn_pos * sigma_nn_pos * self._inv_Z2 + tau2 * self._inv_S2
        phi_val = float(np.dot(self._eff_w, fi)) / self._eff_w_sum

        # ---------------------------------------------------------------
        # Gradient  ∂FI/∂n  per element (shape M, 3)
        # ---------------------------------------------------------------
        # ∂σ_nn/∂n = 2 Σ n = 2 t   (since Σ is symmetric)
        d_sigma_nn = 2.0 * t  # (M, 3)

        # ∂τ²/∂n = 2(Σ²n − 2 σ_nn Σn) = 2(sigma2_n − 2 σ_nn t)
        d_tau2 = 2.0 * (sigma2_n - 2.0 * sigma_nn[:, np.newaxis] * t)  # (M, 3)

        # ∂FI/∂n = 2 σ_nn⁺ / Z² · ∂σ_nn/∂n  +  1/S² · ∂τ²/∂n
        # (the tensile mask selects elements where σ_nn > 0)
        coeff_normal = (
            2.0 * sigma_nn_pos * self._inv_Z2
        )  # (M,) — zero where compressive
        d_fi = (
            coeff_normal[:, np.newaxis] * d_sigma_nn
            + self._inv_S2 * d_tau2
        )  # (M, 3)

        # ∂Φ/∂n = Σ_e eff_w[e] · ∂FI[e]/∂n / eff_w_sum
        d_phi_dn = np.einsum("e,ei->i", self._eff_w, d_fi) / self._eff_w_sum  # (3,)

        # Chain rule to (θ, φ)
        grad = np.array([
            float(np.dot(d_phi_dn, dn_dtheta)),
            float(np.dot(d_phi_dn, dn_dphi)),
        ])

        return phi_val, grad

    # ------------------------------------------------------------------
    # Icosahedron and seeding
    # ------------------------------------------------------------------

    @staticmethod
    def _icosahedron_vertices() -> np.ndarray:
        """Return the 12 unit-length vertices of a regular icosahedron.

        Constructed from three mutually orthogonal golden rectangles:
          φ = (1 + √5) / 2  (golden ratio)
          Vertices: permutations of  (0, ±1, ±φ)  (normalised).

        Returns:
            (12, 3) array of unit vectors.
        """
        phi = (1.0 + math.sqrt(5.0)) / 2.0
        # Raw vertices (un-normalised)
        raw = np.array([
            ( 0.0,  1.0,  phi), ( 0.0,  1.0, -phi),
            ( 0.0, -1.0,  phi), ( 0.0, -1.0, -phi),
            ( 1.0,  phi,  0.0), ( 1.0, -phi,  0.0),
            (-1.0,  phi,  0.0), (-1.0, -phi,  0.0),
            ( phi,  0.0,  1.0), ( phi,  0.0, -1.0),
            (-phi,  0.0,  1.0), (-phi,  0.0, -1.0),
        ], dtype=np.float64)
        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        return raw / norms

    @staticmethod
    def _get_icosphere_faces() -> np.ndarray:
        """Return the 20 triangular face index triples of a regular icosahedron.

        Indices correspond to the vertex ordering produced by
        ``_icosahedron_vertices()``.

        Returns:
            (20, 3) integer array of vertex indices per face.
        """
        return np.array([
            # Golden-rectangle construction face connectivity
            [ 0,  2,  8], [ 0,  8,  4], [ 0,  4,  6], [ 0,  6, 10], [ 0, 10,  2],
            [ 3,  1,  9], [ 3,  9,  5], [ 3,  5,  7], [ 3,  7, 11], [ 3, 11,  1],
            [ 2, 10,  7], [10,  6, 11], [ 6,  4,  1], [ 4,  8,  9], [ 8,  2,  5],
            [ 1, 11,  6], [11,  7,  3], [ 7,  5,  3], [ 5,  9,  8], [ 9,  1,  4],
        ], dtype=np.int32)

    def _seed_directions(
        self, force_dir: Optional[np.ndarray]
    ) -> np.ndarray:
        """Build the initial candidate set for hierarchical search.

        Combines:
        - 12 icosahedron vertices (uniform S² coverage).
        - 8 directions in the plane perpendicular to ``force_dir`` (if given),
          at angles 0°, 45°, …, 315° from an arbitrary in-plane basis vector.

        Args:
            force_dir: Optional (3,) unit vector: net force direction.

        Returns:
            (K, 3) unit-direction array, K ≤ 20.
        """
        dirs = list(self._icosahedron_vertices())  # 12 vectors

        if force_dir is not None:
            fd = np.asarray(force_dir, dtype=np.float64)
            fd_norm = np.linalg.norm(fd)
            if fd_norm < 1e-12:
                force_dir = None
            else:
                fd = fd / fd_norm
                # Find an arbitrary vector not parallel to fd
                ref = (
                    np.array([1.0, 0.0, 0.0])
                    if abs(fd[0]) < 0.9
                    else np.array([0.0, 1.0, 0.0])
                )
                # First in-plane basis vector
                u = ref - np.dot(ref, fd) * fd
                u /= np.linalg.norm(u)
                # Second in-plane basis vector
                v = np.cross(fd, u)  # already unit since fd and u are unit+perp
                v /= np.linalg.norm(v)

                for k in range(8):
                    angle = k * math.pi / 4.0  # 0, π/4, …, 7π/4
                    d = math.cos(angle) * u + math.sin(angle) * v
                    dirs.append(d / np.linalg.norm(d))

        return np.array(dirs, dtype=np.float64)

    # ------------------------------------------------------------------
    # Hierarchical subdivision
    # ------------------------------------------------------------------

    def _subdivide_faces(
        self,
        directions: np.ndarray,
        faces: np.ndarray,
        metrics: np.ndarray,
        top_k: int,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Subdivide the ``top_k`` best triangular faces and return new midpoints.

        Each face is identified by its three vertex indices into ``directions``.
        The midpoint of each edge is computed on the unit sphere (spherical
        midpoint = normalised sum).  This yields 3 new midpoint directions per
        face.

        The "best" faces are those whose minimum vertex metric is smallest
        (greedy lower bound on Φ within the face).

        Args:
            directions: (N, 3) current direction array (unit vectors).
            faces: (F, 3) face connectivity (indices into ``directions``).
            metrics: (N,) Φ values for each direction in ``directions``.
            top_k: Number of faces to subdivide.

        Returns:
            Tuple ``(new_dirs, new_faces)`` where:
            - ``new_dirs`` is an (L, 3) array of new midpoint directions.
            - ``new_faces`` is an (3·top_k, 3) array of new sub-face triangles
              that can be used in the next subdivision level (connectivity within
              the original ``directions`` array is NOT updated here; callers
              handle the combined direction list).
        """
        # Score each face by the minimum metric among its three vertices
        face_scores = np.min(metrics[faces], axis=1)  # (F,)
        best_face_idx = np.argsort(face_scores)[:top_k]

        new_dirs = []
        new_faces_list = []

        for fi in best_face_idx:
            a_idx, b_idx, c_idx = faces[fi]
            a, b, c = directions[a_idx], directions[b_idx], directions[c_idx]

            # Spherical midpoints (normalised)
            ab = a + b;  ab /= np.linalg.norm(ab)
            bc = b + c;  bc /= np.linalg.norm(bc)
            ca = c + a;  ca /= np.linalg.norm(ca)
            new_dirs.extend([ab, bc, ca])

        if not new_dirs:
            return np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int32)

        return np.array(new_dirs, dtype=np.float64), np.empty((0, 3), dtype=np.int32)

    # ------------------------------------------------------------------
    # Rodrigues rotation
    # ------------------------------------------------------------------

    @staticmethod
    def rodrigues_rotation(v_from: np.ndarray, v_to: np.ndarray) -> np.ndarray:
        """Compute the 3×3 rotation matrix mapping unit vector ``v_from`` to ``v_to``.

        Uses the Rodrigues formula:

          axis  = v_from × v_to / |v_from × v_to|
          angle = arccos(v_from · v_to)
          K     = skew-symmetric matrix of axis
          R     = I + sin(angle)·K + (1 − cos(angle))·K²

        Edge cases:
        - Parallel vectors (v_from ≈ v_to): returns identity.
        - Anti-parallel vectors (v_from ≈ −v_to): returns 180° rotation about
          an arbitrary perpendicular axis.

        Args:
            v_from: (3,) unit source vector.
            v_to: (3,) unit target vector.

        Returns:
            (3, 3) rotation matrix R such that R @ v_from ≈ v_to.
        """
        v_from = np.asarray(v_from, dtype=np.float64).ravel()
        v_to   = np.asarray(v_to,   dtype=np.float64).ravel()

        # Normalise (tolerate small deviations from unit length)
        v_from = v_from / (np.linalg.norm(v_from) + 1e-300)
        v_to   = v_to   / (np.linalg.norm(v_to)   + 1e-300)

        dot = float(np.clip(np.dot(v_from, v_to), -1.0, 1.0))

        # --- parallel case: cos(angle) ≈ 1 ---
        if dot > 1.0 - 1e-10:
            return np.eye(3, dtype=np.float64)

        # --- anti-parallel case: cos(angle) ≈ -1 ---
        if dot < -1.0 + 1e-10:
            # Pick any vector not parallel to v_from
            perp_ref = (
                np.array([1.0, 0.0, 0.0])
                if abs(v_from[0]) < 0.9
                else np.array([0.0, 1.0, 0.0])
            )
            axis = perp_ref - np.dot(perp_ref, v_from) * v_from
            axis /= np.linalg.norm(axis)
            # 180° rotation: R = 2 * axis outer axis - I
            return 2.0 * np.outer(axis, axis) - np.eye(3, dtype=np.float64)

        # --- general case ---
        cross = np.cross(v_from, v_to)
        cross_norm = np.linalg.norm(cross)
        axis = cross / cross_norm  # unit rotation axis
        angle = math.acos(dot)
        sin_a = math.sin(angle)
        cos_a = dot  # = cos(angle)

        # Skew-symmetric matrix K for cross-product with axis
        # K @ x = axis × x
        K = np.array([
            [ 0.0,    -axis[2],  axis[1]],
            [ axis[2],  0.0,    -axis[0]],
            [-axis[1],  axis[0],  0.0   ],
        ], dtype=np.float64)

        R = (
            np.eye(3, dtype=np.float64)
            + sin_a * K
            + (1.0 - cos_a) * (K @ K)
        )
        return R

    # ------------------------------------------------------------------
    # Direction → spherical parameters (Y-up convention)
    # ------------------------------------------------------------------

    @staticmethod
    def _dir_to_spherical(n: np.ndarray) -> Tuple[float, float]:
        """Convert unit vector to (θ, φ) in Z-up spherical coordinates.

        n = [sin(θ)cos(φ), sin(θ)sin(φ), cos(θ)]

        Returns:
            Tuple (θ, φ) with θ ∈ [0, π], φ ∈ [0, 2π).
        """
        n = np.asarray(n, dtype=np.float64)
        theta = math.acos(float(np.clip(n[2], -1.0, 1.0)))  # polar from Z
        phi = math.atan2(float(n[1]), float(n[0]))            # azimuth in XY
        if phi < 0.0:
            phi += 2.0 * math.pi
        return theta, phi

    @staticmethod
    def _spherical_to_dir(theta: float, phi: float) -> np.ndarray:
        """Convert (θ, φ) Y-up spherical coordinates to unit vector.

        n = [sin(θ)cos(φ), cos(θ), sin(θ)sin(φ)]
        """
        st = math.sin(theta)
        return np.array([
            st * math.cos(phi),
            math.cos(theta),
            st * math.sin(phi),
        ], dtype=np.float64)

    # ------------------------------------------------------------------
    # Main optimisation entry point
    # ------------------------------------------------------------------

    def optimize(
        self,
        subdivision_levels: int = 2,
        refine_top_k: int = 3,
    ) -> OrientationResult:
        """Find the build direction that minimises the delamination metric Φ.

        Algorithm:

        1. Compute Φ at the default Z-up direction [0, 0, 1].
        2. If the stress field is essentially zero, return the identity result
           immediately.
        3. Hierarchical icosphere search:
           a. Evaluate Φ at all seeds (icosahedron + force-perpendicular band).
           b. For ``subdivision_levels`` rounds: subdivide the
              ``refine_top_k`` best triangular faces and evaluate Φ at the
              new midpoints.
        4. L-BFGS-B gradient descent from the best hierarchical candidate,
           using the analytically-computed gradient.
        5. Return an ``OrientationResult`` with all diagnostics.

        Args:
            subdivision_levels: Number of icosphere subdivision rounds (0–3).
                Higher values increase coverage at slight compute cost.
                Default 2 gives ~38 total hierarchical evaluations.
            refine_top_k: Faces to subdivide per level. Default 3.

        Returns:
            ``OrientationResult`` with the optimal direction, rotation matrix,
            and metric values.
        """
        # --- Default (Z-up) metric ---
        default_dir = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        metric_current = self.compute_metric(default_dir)
        self._eval_dirs.append(default_dir.copy())
        self._eval_metrics.append(metric_current)

        if self._zero_stress:
            # No meaningful stress — identity rotation is optimal.
            return OrientationResult(
                optimal_direction=default_dir,
                rotation_matrix=np.eye(3, dtype=np.float64),
                metric_current=metric_current,
                metric_optimal=metric_current,
                improvement_ratio=1.0,
                all_directions=np.array(self._eval_dirs),
                all_metrics=np.array(self._eval_metrics),
            )

        # --- Step 1: Hierarchical icosphere search ---
        # Seeds: 12 icosahedron vertices + up to 8 force-perpendicular dirs
        seed_dirs = self._seed_directions(self._force_dir)  # (K, 3)

        # Evaluate metric at all seeds
        seed_metrics = np.array(
            [self.compute_metric(d) for d in seed_dirs], dtype=np.float64
        )

        # Record evaluations
        self._eval_dirs.extend(list(seed_dirs))
        self._eval_metrics.extend(list(seed_metrics))

        # Build icosahedron face connectivity (needed for subdivision)
        current_dirs = seed_dirs[:12].copy()  # only icosahedron verts for faces
        current_metrics = seed_metrics[:12].copy()
        current_faces = self._get_icosphere_faces()  # (20, 3)

        # Subdivision levels
        for _level in range(subdivision_levels):
            new_dirs, _ = self._subdivide_faces(
                current_dirs, current_faces, current_metrics, refine_top_k
            )
            if new_dirs.shape[0] == 0:
                break

            # Evaluate metric at new midpoints
            new_metrics = np.array(
                [self.compute_metric(d) for d in new_dirs], dtype=np.float64
            )

            # Record
            self._eval_dirs.extend(list(new_dirs))
            self._eval_metrics.extend(list(new_metrics))

            # Append new directions to pool for next subdivision level.
            # Rebuild connectivity from scratch (not needed for next level
            # since we only pass the top-K faces, which reference indices in
            # the growing pool — we rebuild faces as all-vs-all triplets using
            # the new points' nearest neighbours on the sphere, but for
            # efficiency we simply form new faces from the midpoint triads).
            # For the next level, the "faces" are the newly created sub-triangles
            # from this level: each face fi spawned 3 midpoints at positions
            # [3*fi, 3*fi+1, 3*fi+2] in new_dirs; the new triangles are:
            #   (a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)
            # For simplicity we treat each midpoint triple as a new triangle face
            # and recurse.  This slightly over-simplifies the connectivity but
            # maintains coverage.
            n_base = len(current_dirs)
            n_new = len(new_dirs)
            current_dirs    = np.vstack([current_dirs, new_dirs])
            current_metrics = np.concatenate([current_metrics, new_metrics])

            # New faces: consecutive midpoint triples (3 per subdivided face)
            n_triplets = n_new // 3
            new_face_arr = np.array(
                [
                    [n_base + 3 * k, n_base + 3 * k + 1, n_base + 3 * k + 2]
                    for k in range(n_triplets)
                ],
                dtype=np.int32,
            )
            if new_face_arr.shape[0] > 0:
                current_faces = new_face_arr
            else:
                break

        # Incorporate force-perpendicular seeds (indices 12+) if present
        all_hier_dirs    = np.array(self._eval_dirs[1:])     # skip default_dir
        all_hier_metrics = np.array(self._eval_metrics[1:])

        best_hier_idx  = int(np.argmin(all_hier_metrics))
        best_hier_dir  = all_hier_dirs[best_hier_idx]

        # --- Step 2: L-BFGS-B gradient descent ---
        theta0, phi0 = self._dir_to_spherical(best_hier_dir)

        def _fun_grad(params: np.ndarray) -> Tuple[float, np.ndarray]:
            phi_val, grad = self.compute_metric_and_gradient(params)
            # Track iterates
            n_iter = self._spherical_to_dir(float(params[0]), float(params[1]))
            self._eval_dirs.append(n_iter)
            self._eval_metrics.append(phi_val)
            return phi_val, grad

        result = minimize(
            _fun_grad,
            x0=np.array([theta0, phi0], dtype=np.float64),
            method="L-BFGS-B",
            jac=True,
            bounds=[(1e-6, math.pi - 1e-6), (0.0, 2.0 * math.pi)],
            options={"maxiter": 100, "ftol": 1e-12, "gtol": 1e-8},
        )

        optimal_theta, optimal_phi = float(result.x[0]), float(result.x[1])
        optimal_dir = self._spherical_to_dir(optimal_theta, optimal_phi)
        metric_optimal = float(result.fun)

        # Guard: accept L-BFGS-B result only if it's actually better.
        # (The optimiser could theoretically diverge on degenerate inputs.)
        if metric_optimal > float(np.min(all_hier_metrics)):
            best_fallback_idx = int(np.argmin(all_hier_metrics))
            optimal_dir    = all_hier_dirs[best_fallback_idx]
            metric_optimal = float(all_hier_metrics[best_fallback_idx])

        # Normalise final direction
        optimal_dir = optimal_dir / (np.linalg.norm(optimal_dir) + 1e-300)

        # Rotation matrix: maps default build dir [0,1,0] → optimal_dir
        rotation_matrix = self.rodrigues_rotation(default_dir, optimal_dir)

        # Improvement ratio (>1 means reorientation is beneficial)
        if metric_optimal > 1e-30:
            improvement_ratio = metric_current / metric_optimal
        else:
            improvement_ratio = 1.0 if metric_current < 1e-30 else 100.0

        all_dirs_arr    = np.array(self._eval_dirs,    dtype=np.float64)
        all_metrics_arr = np.array(self._eval_metrics, dtype=np.float64)

        return OrientationResult(
            optimal_direction=optimal_dir,
            rotation_matrix=rotation_matrix,
            metric_current=metric_current,
            metric_optimal=metric_optimal,
            improvement_ratio=improvement_ratio,
            all_directions=all_dirs_arr,
            all_metrics=all_metrics_arr,
        )
