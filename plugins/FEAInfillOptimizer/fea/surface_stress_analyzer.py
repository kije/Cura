# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Surface stress analysis for FEA Infill Optimizer.

Identifies surface elements in a tetrahedral mesh, classifies them as wall,
top, or bottom, and computes stress-based metrics used to determine wall
thickness and top/bottom skin thickness.

All operations are pure numpy — no Cura dependencies.

Coordinate convention (Cura Y-up):
    Y  = build direction (gravity is -Y)
    XZ = print plane
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from .tetrahedralization import TetMesh

# Face index sets for a single tetrahedron (local node indices 0-3).
# Each row is the 3 local node indices that form one face.
_TET_FACE_LOCAL: np.ndarray = np.array(
    [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], dtype=np.intp
)

# Cosine thresholds for surface classification.
_COS_60 = 0.5      # cos(60°) — reserved for future overhang classification
_COS_30 = np.sqrt(3.0) / 2.0  # cos(30°) ≈ 0.866 — top/bottom boundary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def identify_surface_elements(tet_mesh: TetMesh) -> np.ndarray:
    """Return a boolean mask of elements that have at least one surface face.

    A face is a *surface face* when all three of its nodes are surface nodes
    (i.e. appear in the values of ``tet_mesh.surface_node_map``).  An element
    is a *surface element* when any of its four faces is a surface face.

    Args:
        tet_mesh: Tetrahedral mesh produced by :mod:`tetrahedralization`.

    Returns:
        Boolean array of shape ``(M,)`` where ``M`` is the number of
        elements.  ``True`` marks surface elements.
    """
    elems = tet_mesh.elements  # (M, 4)
    M = len(elems)

    if M == 0 or not tet_mesh.surface_node_map:
        return np.zeros(M, dtype=bool)

    surface_nodes: np.ndarray = np.fromiter(
        tet_mesh.surface_node_map.values(), dtype=np.int64
    )
    surface_set = set(surface_nodes.tolist())

    # Build a boolean membership array indexed by node index for O(1) lookup.
    max_node = int(elems.max()) + 1
    is_surface_node = np.zeros(max_node, dtype=bool)
    valid_surface = surface_nodes[surface_nodes < max_node]
    is_surface_node[valid_surface] = True

    # For each element, check all 4 faces.
    # face_nodes shape: (M, 4, 3) — for each element, 4 faces, each with 3 nodes.
    face_nodes = elems[:, _TET_FACE_LOCAL]  # (M, 4, 3)

    # is_surface_node[face_nodes] → (M, 4, 3) bool
    face_node_on_surface = is_surface_node[face_nodes]  # (M, 4, 3)

    # A face is a surface face when ALL 3 nodes are surface nodes.
    face_is_surface = face_node_on_surface.all(axis=2)  # (M, 4)

    # An element is a surface element when ANY face is a surface face.
    surface_mask: np.ndarray = face_is_surface.any(axis=1)  # (M,)
    return surface_mask


def classify_surface_elements(
    tet_mesh: TetMesh,
    surface_mask: np.ndarray,
    build_dir: np.ndarray | None = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Classify surface elements as wall, top, or bottom.

    Classification is based on the angle between each element's outward
    surface-face normal and the build direction.

    Thresholds (``build_dir`` = Z-up by default, matching the FEA
    constitutive matrix which uses Z as the transverse/weak axis):

    * **Top**:    ``normal · build_dir > cos(30°) ≈ 0.866``
    * **Bottom**: ``normal · build_dir < -cos(30°) ≈ -0.866``
    * **Wall**:   everything else (conservative — avoids over-thinning
                  overhang/bridge faces).

    Args:
        tet_mesh:     Tetrahedral mesh.
        surface_mask: Boolean array ``(M,)`` from :func:`identify_surface_elements`.
        build_dir:    Unit vector for the build direction.  Defaults to
                      ``[0, 0, 1]`` (Z-up, matching the constitutive matrix
                      weak axis in ``homogenization.py``).

    Returns:
        ``(wall_mask, top_mask, bottom_mask)`` — mutually exclusive boolean
        arrays of shape ``(M,)``.  Non-surface elements are ``False`` in all
        three.
    """
    M = len(surface_mask)
    wall_mask = np.zeros(M, dtype=bool)
    top_mask = np.zeros(M, dtype=bool)
    bottom_mask = np.zeros(M, dtype=bool)

    if M == 0 or not surface_mask.any():
        return wall_mask, top_mask, bottom_mask

    if build_dir is None:
        build_dir = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        build_dir = np.asarray(build_dir, dtype=np.float64)
        norm = np.linalg.norm(build_dir)
        if norm > 0.0:
            build_dir = build_dir / norm

    nodes = tet_mesh.nodes        # (N, 3)
    elems = tet_mesh.elements     # (M, 4)

    surface_indices = np.where(surface_mask)[0]
    S = len(surface_indices)

    if not tet_mesh.surface_node_map:
        return wall_mask, top_mask, bottom_mask

    # Build is_surface_node boolean array for membership testing.
    surface_nodes_arr: np.ndarray = np.fromiter(
        tet_mesh.surface_node_map.values(), dtype=np.int64
    )
    max_node = int(elems.max()) + 1
    is_surface_node = np.zeros(max_node, dtype=bool)
    valid = surface_nodes_arr[surface_nodes_arr < max_node]
    is_surface_node[valid] = True

    # --- Vectorized outward-normal computation ---
    #
    # surf_elems: (S, 4) — node indices for each surface element
    surf_elems = elems[surface_indices]  # (S, 4)

    # face_nodes: (S, 4, 3) — 3 node indices per face, 4 faces per element
    face_nodes = surf_elems[:, _TET_FACE_LOCAL]  # (S, 4, 3)

    # face_on_surface: (S, 4) — True when all 3 face nodes are surface nodes
    face_on_surface = is_surface_node[face_nodes].all(axis=2)  # (S, 4)

    # face_verts: (S, 4, 3, 3) — xyz coordinates for each face vertex
    face_verts = nodes[face_nodes]  # (S, 4, 3, 3)

    # Edge vectors for cross-product normal: (S, 4, 3)
    edge1 = face_verts[:, :, 1, :] - face_verts[:, :, 0, :]  # (S, 4, 3)
    edge2 = face_verts[:, :, 2, :] - face_verts[:, :, 0, :]  # (S, 4, 3)
    raw_normals = np.cross(edge1, edge2)  # (S, 4, 3)

    # Area magnitude: (S, 4); used for normalisation and degenerate-face masking
    area2 = np.linalg.norm(raw_normals, axis=2)  # (S, 4)
    nonzero_area = area2 > 1e-14  # (S, 4)

    # Avoid division by zero — replace zero areas with 1.0 temporarily
    safe_area = np.where(nonzero_area, area2, 1.0)
    unit_normals = raw_normals / safe_area[:, :, np.newaxis]  # (S, 4, 3)

    # Opposite vertex for each face (local index not in the face triple).
    # _TET_FACE_LOCAL rows cover nodes {0,1,2}, {0,1,3}, {0,2,3}, {1,2,3};
    # the opposite local indices are           3,        2,        1,        0.
    _OPPOSITE_LOCAL = np.array([3, 2, 1, 0], dtype=np.intp)  # (4,)

    opp_verts = nodes[surf_elems[:, _OPPOSITE_LOCAL]]  # (S, 4, 3)
    face_centroids = face_verts.mean(axis=2)            # (S, 4, 3)
    to_opp = opp_verts - face_centroids                 # (S, 4, 3)

    # Dot product of raw unit normal with vector toward opposite vertex.
    dot_opp = (unit_normals * to_opp).sum(axis=2)  # (S, 4)

    # Flip normals that point *toward* the opposite vertex (dot > 0).
    flip = (dot_opp > 0.0).astype(np.float64) * -2.0 + 1.0  # +1 or -1
    outward_normals = unit_normals * flip[:, :, np.newaxis]  # (S, 4, 3)

    # Mask out non-surface faces and degenerate faces before averaging.
    valid_face = face_on_surface & nonzero_area  # (S, 4)

    # Zero out invalid faces so they don't contribute to the mean.
    masked_normals = outward_normals * valid_face[:, :, np.newaxis]  # (S, 4, 3)

    # Sum of valid face normals per element and count of valid faces.
    normal_sum = masked_normals.sum(axis=1)    # (S, 3)
    valid_count = valid_face.sum(axis=1)       # (S,)  integer

    # Elements with no valid surface face are degenerate → treat as wall.
    has_valid = valid_count > 0  # (S,)

    avg_norm_mag = np.linalg.norm(normal_sum, axis=1)  # (S,)
    has_nonzero_avg = avg_norm_mag > 1e-14              # (S,)

    good = has_valid & has_nonzero_avg  # (S,)

    # Normalise the averaged normal vectors where valid.
    avg_normals = np.zeros((S, 3), dtype=np.float64)
    avg_normals[good] = (
        normal_sum[good] / avg_norm_mag[good, np.newaxis]
    )

    # Classify via dot product with build direction.
    dots = avg_normals @ build_dir  # (S,)

    # Default for degenerate (not good): wall.
    is_top    = good & (dots >  _COS_30)
    is_bottom = good & (dots < -_COS_30)
    is_wall   = (~good) | (good & ~is_top & ~is_bottom)

    top_mask[surface_indices[is_top]]    = True
    bottom_mask[surface_indices[is_bottom]] = True
    wall_mask[surface_indices[is_wall]]  = True

    return wall_mask, top_mask, bottom_mask


def build_element_adjacency(tet_mesh: TetMesh) -> Dict[int, List[int]]:
    """Build face-based element adjacency for the tetrahedral mesh.

    Two elements are adjacent when they share exactly one face (3 nodes).

    Args:
        tet_mesh: Tetrahedral mesh.

    Returns:
        Dictionary mapping ``element_index`` → list of neighboring
        ``element_index`` values.  Every element appears as a key; isolated
        elements map to an empty list.
    """
    elems = tet_mesh.elements  # (M, 4)
    M = len(elems)
    adjacency: Dict[int, List[int]] = {i: [] for i in range(M)}

    if M == 0:
        return adjacency

    # --- Vectorized face-key construction ---
    # Each element contributes 4 faces; generate all 4M face records at once.
    face_nodes_all = elems[:, _TET_FACE_LOCAL]          # (M, 4, 3)
    face_nodes_flat = face_nodes_all.reshape(-1, 3)      # (4M, 3)
    elem_ids = np.repeat(np.arange(M, dtype=np.int64), 4)  # (4M,)

    # Canonical face: sort node indices within each face row.
    face_sorted = np.sort(face_nodes_flat, axis=1)  # (4M, 3)

    # Pack the 3 sorted node indices into a single int64 key for O(n log n)
    # matching via argsort (avoids hashing overhead of a Python dict for 4M
    # entries, while still producing exact canonical keys).
    stride = int(elems.max()) + 1
    keys = (
        face_sorted[:, 0].astype(np.int64) * stride * stride
        + face_sorted[:, 1].astype(np.int64) * stride
        + face_sorted[:, 2].astype(np.int64)
    )  # (4M,)

    # Sort by key so that shared faces (interior faces) become consecutive pairs.
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    sorted_elems = elem_ids[order]

    # Adjacent pairs: consecutive entries with identical key share a face.
    same = sorted_keys[:-1] == sorted_keys[1:]  # (4M-1,)
    pairs_a = sorted_elems[:-1][same]            # number of interior faces
    pairs_b = sorted_elems[1:][same]

    # The final Python loop is over ~M interior-face pairs (much smaller than
    # the original 4M-element loop), so overhead is negligible.
    for a, b in zip(pairs_a.tolist(), pairs_b.tolist()):
        adjacency[a].append(b)
        adjacency[b].append(a)

    return adjacency


def compute_stress_gradient(
    tet_mesh: TetMesh,
    stress_field: np.ndarray,
    surface_mask: np.ndarray,
    adjacency: Dict[int, List[int]] | None = None,
) -> np.ndarray:
    """Compute a normalised stress gradient for surface elements.

    For each surface element the gradient is estimated as the absolute
    difference between the element's scalar stress and the mean stress of its
    interior (non-surface) neighbours, divided by the centroid-to-centroid
    distance.

    Thin features with no interior neighbours use
    ``σ_surface / L_char`` where ``L_char`` is the mean edge length of the
    element.

    The returned array is normalised to ``[0, 1]`` by dividing by the global
    maximum.  Non-surface elements are zero.

    Args:
        tet_mesh:     Tetrahedral mesh.
        stress_field: Scalar effective stress, shape ``(M,)``.
        surface_mask: Boolean array ``(M,)`` from
                      :func:`identify_surface_elements`.
        adjacency:    Pre-built adjacency dict from
                      :func:`build_element_adjacency`.  If ``None``, it is
                      built internally.  Pass it when calling multiple times
                      on the same mesh to avoid redundant computation.

    Returns:
        Normalised gradient array of shape ``(M,)``.
    """
    M = len(surface_mask)
    grad = np.zeros(M, dtype=np.float64)

    if M == 0 or not surface_mask.any():
        return grad

    stress_field = np.asarray(stress_field, dtype=np.float64)
    if stress_field.shape != (M,):
        raise ValueError(
            f"stress_field must have shape ({M},); got {stress_field.shape}"
        )

    nodes = tet_mesh.nodes    # (N, 3)
    elems = tet_mesh.elements  # (M, 4)

    # Element centroids: (M, 3) — one vectorised operation for the whole mesh.
    centroids = nodes[elems].mean(axis=1)

    if adjacency is None:
        adjacency = build_element_adjacency(tet_mesh)

    surface_indices = np.where(surface_mask)[0]  # (S,)

    # --- Classify each surface element as "has interior neighbours" or not ---
    # Build a list of interior-neighbour index lists for every surface element.
    # The adjacency structure is inherently sparse; the list comprehension here
    # is over S surface elements (not M total), which is the irreducible minimum
    # for a sparse irregular structure.
    interior_nb_lists: list[list[int]] = [
        [nb for nb in adjacency[int(ei)] if not surface_mask[nb]]
        for ei in surface_indices
    ]
    has_interior = np.array([len(nb) > 0 for nb in interior_nb_lists], dtype=bool)  # (S,)

    # ---- Elements WITH interior neighbours (vectorised per sub-group) ----
    if has_interior.any():
        hi_surf_idx = surface_indices[has_interior]          # indices into full mesh
        hi_nb_lists = [interior_nb_lists[i] for i in np.where(has_interior)[0]]

        # Compute per-element stats using numpy; the variable-length neighbour
        # lists prevent a single rectangular array, but we batch the work by
        # stacking into a flat array with offsets.
        nb_counts   = np.array([len(nb) for nb in hi_nb_lists], dtype=np.int64)  # (H,)
        nb_flat     = np.concatenate([nb for nb in hi_nb_lists]).astype(np.int64)  # (sum_k,)
        offsets     = np.concatenate([[0], np.cumsum(nb_counts[:-1])])             # (H,)

        # Flat stress for all neighbours in one index.
        sigma_nb_flat = stress_field[nb_flat]  # (sum_k,)
        # Flat centroids for all neighbours.
        cent_nb_flat  = centroids[nb_flat]     # (sum_k, 3)
        # Replicate surface-element centroids to match flat layout.
        cent_e_rep    = centroids[hi_surf_idx].repeat(nb_counts, axis=0)  # (sum_k, 3)

        dist_flat = np.linalg.norm(cent_nb_flat - cent_e_rep, axis=1)  # (sum_k,)

        # Reduce (mean) per surface element using np.add.reduceat.
        sigma_nb_mean = np.add.reduceat(sigma_nb_flat, offsets) / nb_counts  # (H,)
        mean_dist     = np.add.reduceat(dist_flat,     offsets) / nb_counts  # (H,)

        sigma_e = stress_field[hi_surf_idx]  # (H,)
        delta   = np.abs(sigma_e - sigma_nb_mean)

        raw_grad = np.where(mean_dist > 0.0, delta / mean_dist, delta)
        grad[hi_surf_idx] = raw_grad

    # ---- Elements WITHOUT interior neighbours (thin features) ----
    if (~has_interior).any():
        thin_surf_idx = surface_indices[~has_interior]  # (T,)
        thin_elems    = elems[thin_surf_idx]            # (T, 4)

        # Vectorised mean edge length: each tet has 6 edges.
        _EDGE_PAIRS = np.array(
            [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]], dtype=np.intp
        )  # (6, 2)
        # vert_a/b: (T, 6, 3)
        vert_a = nodes[thin_elems[:, _EDGE_PAIRS[:, 0]]]
        vert_b = nodes[thin_elems[:, _EDGE_PAIRS[:, 1]]]
        L_char = np.linalg.norm(vert_a - vert_b, axis=2).mean(axis=1)  # (T,)

        sigma_thin = stress_field[thin_surf_idx]
        grad[thin_surf_idx] = np.where(L_char > 0.0, sigma_thin / L_char, 0.0)

    max_grad = float(grad.max())
    if max_grad > 0.0:
        grad /= max_grad

    return grad


def compute_wall_metric(
    stress_field: np.ndarray,
    grad_sigma: np.ndarray,
    wall_mask: np.ndarray,
    sigma_eff: float,
    alpha: float = 0.6,
) -> np.ndarray:
    """Compute the wall thickness metric W_wall for surface wall elements.

    .. math::

        W_{\\text{wall}}(e) = \\operatorname{clip}\\left(
            \\alpha \\frac{\\sigma(e)}{\\sigma_{\\text{eff}}}
            + (1-\\alpha)\\,\\nabla\\sigma(e),\\; 0,\\; 1
        \\right)

    Args:
        stress_field: Scalar effective stress per element, shape ``(M,)``.
        grad_sigma:   Normalised stress gradient, shape ``(M,)``, from
                      :func:`compute_stress_gradient`.
        wall_mask:    Boolean array ``(M,)`` from
                      :func:`classify_surface_elements`.
        sigma_eff:    Effective yield/reference stress used to normalise.
                      Must be > 0.
        alpha:        Blending coefficient in ``[0, 1]``.  Defaults to 0.6.

    Returns:
        ``W_wall`` array of shape ``(M,)``.  Zero for non-wall elements.
    """
    stress_field = np.asarray(stress_field, dtype=np.float64)
    grad_sigma = np.asarray(grad_sigma, dtype=np.float64)
    wall_mask = np.asarray(wall_mask, dtype=bool)

    M = len(wall_mask)
    w_wall = np.zeros(M, dtype=np.float64)

    if M == 0 or not wall_mask.any():
        return w_wall

    if sigma_eff <= 0.0:
        raise ValueError(f"sigma_eff must be positive; got {sigma_eff}")

    wall_idx = np.where(wall_mask)[0]
    raw = alpha * (stress_field[wall_idx] / sigma_eff) + (1.0 - alpha) * grad_sigma[wall_idx]
    w_wall[wall_idx] = np.clip(raw, 0.0, 1.0)
    return w_wall


def compute_tb_metric(
    stress_tensors: np.ndarray,
    top_mask: np.ndarray,
    bottom_mask: np.ndarray,
    sigma_eff: float,
    bonding_coeff: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Compute top/bottom thickness metrics W_top and W_bottom.

    Uses the build-direction stress component (σ_yy in Cura Y-up coordinates,
    Voigt index 1) to quantify inter-layer normal stress.

    .. math::

        W_{\\text{top/bottom}}(e) = \\operatorname{clip}\\left(
            \\frac{|\\sigma_{yy}(e)|}{\\sigma_{\\text{eff}} \\cdot c_{\\text{bond}}},
            \\; 0,\\; 1
        \\right)

    Args:
        stress_tensors: Voigt stress tensors per element, shape ``(M, 6)``:
                        ``[σ_xx, σ_yy, σ_zz, τ_xy, τ_yz, τ_xz]``.
        top_mask:       Boolean array ``(M,)`` from
                        :func:`classify_surface_elements`.
        bottom_mask:    Boolean array ``(M,)`` from
                        :func:`classify_surface_elements`.
        sigma_eff:      Effective yield/reference stress.  Must be > 0.
        bonding_coeff:  Layer-bonding coefficient that scales the reference
                        stress.  Values < 1 indicate weaker bonding and
                        therefore lower threshold for flagging thickness.
                        Defaults to 1.0.

    Returns:
        ``(W_top, W_bottom)`` — each shape ``(M,)``.  Zero for elements
        not in the corresponding mask.
    """
    stress_tensors = np.asarray(stress_tensors, dtype=np.float64)
    top_mask = np.asarray(top_mask, dtype=bool)
    bottom_mask = np.asarray(bottom_mask, dtype=bool)

    M = len(top_mask)
    w_top = np.zeros(M, dtype=np.float64)
    w_bottom = np.zeros(M, dtype=np.float64)

    if M == 0:
        return w_top, w_bottom

    if stress_tensors.shape != (M, 6):
        raise ValueError(
            f"stress_tensors must have shape ({M}, 6); got {stress_tensors.shape}"
        )
    if sigma_eff <= 0.0:
        raise ValueError(f"sigma_eff must be positive; got {sigma_eff}")
    if bonding_coeff <= 0.0:
        raise ValueError(f"bonding_coeff must be positive; got {bonding_coeff}")

    # Voigt index 2 is σ_zz — the interlayer normal stress.  The constitutive
    # matrix in homogenization.py uses Z as the transverse (weak/build) axis,
    # consistent with the existing Tsai-Hill in fea_solver.py which also
    # uses stress_all[:, 2] for the out-of-plane component.
    sigma_zz = stress_tensors[:, 2]
    denom = sigma_eff * bonding_coeff

    if top_mask.any():
        top_idx = np.where(top_mask)[0]
        w_top[top_idx] = np.clip(np.abs(sigma_zz[top_idx]) / denom, 0.0, 1.0)

    if bottom_mask.any():
        bot_idx = np.where(bottom_mask)[0]
        w_bottom[bot_idx] = np.clip(np.abs(sigma_zz[bot_idx]) / denom, 0.0, 1.0)

    return w_top, w_bottom
