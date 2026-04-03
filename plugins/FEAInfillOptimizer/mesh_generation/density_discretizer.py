# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from dataclasses import dataclass, field
from typing import List

import numpy


@dataclass
class Zone:
    """Represents a single density zone with a uniform infill density.

    Attributes:
        density: Normalized density value in [0, 1].
        element_indices: Indices of tet elements belonging to this zone.
    """

    density: float
    element_indices: List[int] = field(default_factory=list)


def discretize_density(
    density_per_element: numpy.ndarray,
    n_zones: int,
    rho_min: float,
    rho_max: float,
) -> List[Zone]:
    """Discretize a continuous per-element density field into N uniform zones.

    Creates ``n_zones`` uniform bins spanning [rho_min, rho_max].  Each
    element is assigned to the nearest bin by its density value.  The
    resulting Zone's density is the bin mid-point.  Empty bins are omitted
    from the returned list.

    Args:
        density_per_element: 1-D array of per-element density values,
            typically in [rho_min, rho_max].
        n_zones: Number of discrete zones (bins) to create.
        rho_min: Lower bound of the density range.
        rho_max: Upper bound of the density range.

    Returns:
        List of Zone objects sorted by ascending density, one per non-empty
        bin.
    """
    if n_zones < 1:
        raise ValueError("n_zones must be >= 1")
    if rho_max <= rho_min:
        raise ValueError("rho_max must be greater than rho_min")

    # Build bin edges and mid-points
    edges = numpy.linspace(rho_min, rho_max, n_zones + 1)
    midpoints = 0.5 * (edges[:-1] + edges[1:])

    # Clamp densities to [rho_min, rho_max] to avoid out-of-range assignments
    clamped = numpy.clip(density_per_element, rho_min, rho_max)

    # Assign each element to the nearest bin midpoint
    # Expand dims for broadcasting: (n_elements, 1) vs (n_zones,)
    distances = numpy.abs(clamped[:, numpy.newaxis] - midpoints[numpy.newaxis, :])
    bin_assignments = numpy.argmin(distances, axis=1)

    # Build Zone objects, skipping empty bins
    zones: List[Zone] = []
    for bin_idx in range(n_zones):
        indices = numpy.where(bin_assignments == bin_idx)[0].tolist()
        if not indices:
            continue
        zones.append(Zone(density=float(midpoints[bin_idx]), element_indices=indices))

    return zones
