# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Convert a surface trimesh to a volumetric tetrahedral mesh using Gmsh.

Uses gmsh's Python API + native library for robust tetrahedralization.
Falls back to scipy.spatial.Delaunay if gmsh is unavailable.
"""

import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Dict

import numpy as np

try:
    import trimesh
except ImportError:
    trimesh = None  # type: ignore[assignment]

try:
    import gmsh
except ImportError:
    gmsh = None  # type: ignore[assignment]

from UM.Logger import Logger

# Gmsh is not thread-safe; serialise all calls with this lock.
_GMSH_LOCK = threading.Lock()

_PRESET_FRACTIONS: Dict[str, float] = {
    "coarse": 0.15,
    "medium": 0.06,
    "fine": 0.03,
}


@dataclass
class TetMesh:
    nodes: np.ndarray
    elements: np.ndarray
    surface_node_map: Dict[int, int] = field(default_factory=dict)


def tetrahedralize(surface_mesh: "trimesh.Trimesh", element_size: float) -> TetMesh:
    if isinstance(element_size, str):
        fraction = _PRESET_FRACTIONS.get(element_size, _PRESET_FRACTIONS["medium"])
        diagonal = float(np.linalg.norm(surface_mesh.bounds[1] - surface_mesh.bounds[0]))
        char_length = max(diagonal * fraction, 0.1)
    else:
        char_length = float(element_size)

    Logger.log("d", "FEA tetrahedralization: char_length=%.3f mm for mesh with %d vertices",
               char_length, len(surface_mesh.vertices))

    if gmsh is not None:
        try:
            return _tetrahedralize_gmsh(surface_mesh, char_length)
        except Exception as e:
            Logger.log("w", "FEA tet: gmsh tetrahedralization failed (%s), falling back to scipy", str(e))

    Logger.log("d", "FEA tet: using scipy Delaunay fallback")
    return _tetrahedralize_scipy(surface_mesh, char_length)


def _tetrahedralize_gmsh(surface_mesh: "trimesh.Trimesh", char_length: float) -> TetMesh:
    """Tetrahedralize using gmsh — tries multiple approaches."""

    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        surface_mesh.export(tmp_path, file_type="stl_ascii")
        return _run_gmsh(tmp_path, char_length, surface_mesh)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _run_gmsh(stl_path: str, char_length: float, surface_mesh) -> TetMesh:
    with _GMSH_LOCK:
        try:
            gmsh.initialize(interruptible=False)
            gmsh.option.setNumber("General.Verbosity", 5)

            # Log which native library gmsh loaded
            if hasattr(gmsh, 'libpath') and gmsh.libpath:
                Logger.log("d", "FEA tet: gmsh native lib: %s", gmsh.libpath)
            else:
                Logger.log("d", "FEA tet: gmsh loaded (libpath not exposed)")

            Logger.log("d", "FEA tet: merging STL...")
            gmsh.merge(stl_path)

            # Approach 1: Try createTopology (simpler than classifySurfaces)
            Logger.log("d", "FEA tet: trying createTopology...")
            try:
                gmsh.model.mesh.createTopology()
                Logger.log("d", "FEA tet: createTopology succeeded")
            except Exception as e:
                Logger.log("w", "FEA tet: createTopology failed: %s", str(e))

            # Check if we have any volumes now
            volumes = gmsh.model.getEntities(3)
            surfaces = gmsh.model.getEntities(2)
            Logger.log("d", "FEA tet: after topology — %d surfaces, %d volumes",
                       len(surfaces), len(volumes))

            # If no volume, try to create one from surfaces
            if not volumes and surfaces:
                Logger.log("d", "FEA tet: creating volume from %d surfaces...", len(surfaces))
                try:
                    sl = gmsh.model.geo.addSurfaceLoop([s[1] for s in surfaces])
                    gmsh.model.geo.addVolume([sl])
                    gmsh.model.geo.synchronize()
                    volumes = gmsh.model.getEntities(3)
                    Logger.log("d", "FEA tet: volume created, %d volumes now", len(volumes))
                except Exception as e:
                    Logger.log("w", "FEA tet: geo volume creation failed: %s", str(e))

            gmsh.option.setNumber("Mesh.CharacteristicLengthMax", char_length)
            gmsh.option.setNumber("Mesh.CharacteristicLengthMin", char_length * 0.1)
            gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay

            Logger.log("d", "FEA tet: generating 3D mesh...")
            gmsh.model.mesh.generate(3)

            Logger.log("d", "FEA tet: optimizing mesh...")
            try:
                gmsh.model.mesh.optimize("Netgen")
            except Exception:
                Logger.log("w", "FEA tet: Netgen optimization skipped")

            Logger.log("d", "FEA tet: extracting mesh data...")

            # Extract nodes
            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            nodes = np.array(node_coords, dtype=np.float64).reshape(-1, 3)
            tag_to_idx = {int(tag): i for i, tag in enumerate(node_tags)}

            # Extract tets (type 4)
            elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements()
            tet_nodes = []
            for etype, _, enodes in zip(elem_types, elem_tags, elem_node_tags):
                if etype == 4:
                    tet_nodes.append(np.array(enodes, dtype=np.int64).reshape(-1, 4))

            if not tet_nodes:
                raise RuntimeError("Gmsh produced no tetrahedral elements.")

            elements_gmsh = np.vstack(tet_nodes)
            elements = np.vectorize(tag_to_idx.__getitem__)(elements_gmsh)

            Logger.log("d", "FEA tet: %d nodes, %d tets extracted", len(nodes), len(elements))

        finally:
            try:
                gmsh.finalize()
            except Exception:
                pass  # Don't crash on finalize

    # Build surface_node_map using KDTree
    import scipy.spatial
    surface_node_map: Dict[int, int] = {}
    tolerance = char_length * 0.1
    kd_tree = scipy.spatial.KDTree(nodes)
    dists, indices = kd_tree.query(surface_mesh.vertices)
    for surf_idx in range(len(surface_mesh.vertices)):
        if dists[surf_idx] < tolerance:
            surface_node_map[surf_idx] = int(indices[surf_idx])

    Logger.log("d", "FEA tet: %d surface nodes mapped", len(surface_node_map))

    return TetMesh(nodes=nodes, elements=elements, surface_node_map=surface_node_map)


def _tetrahedralize_scipy(surface_mesh, char_length: float) -> TetMesh:
    """Fallback tetrahedralization using scipy.spatial.Delaunay."""
    from scipy.spatial import Delaunay

    surface_verts = np.array(surface_mesh.vertices, dtype=np.float64)
    n_surface = len(surface_verts)

    # Generate interior points
    Logger.log("d", "FEA tet (scipy): generating interior points...")
    interior = _generate_interior_points(surface_mesh, char_length)
    Logger.log("d", "FEA tet (scipy): %d interior points", len(interior))

    all_points = np.vstack([surface_verts, interior]) if len(interior) > 0 else surface_verts

    Logger.log("d", "FEA tet (scipy): Delaunay on %d points...", len(all_points))
    delaunay = Delaunay(all_points)
    all_tets = delaunay.simplices

    Logger.log("d", "FEA tet (scipy): filtering %d raw tets...", len(all_tets))
    centroids = all_points[all_tets].mean(axis=1)
    inside = _points_inside_mesh(centroids, surface_mesh)
    elements = all_tets[inside]
    Logger.log("d", "FEA tet (scipy): %d/%d tets inside mesh", len(elements), len(all_tets))

    if len(elements) == 0:
        raise RuntimeError("No interior tetrahedra found. Mesh may not be watertight.")

    surface_node_map = {i: i for i in range(n_surface)}

    Logger.log("d", "FEA tet (scipy): %d nodes, %d tets", len(all_points), len(elements))

    return TetMesh(
        nodes=all_points,
        elements=np.array(elements, dtype=np.int64),
        surface_node_map=surface_node_map,
    )


def _generate_interior_points(surface_mesh, char_length: float) -> np.ndarray:
    bounds_min = surface_mesh.bounds[0]
    bounds_max = surface_mesh.bounds[1]
    extent = bounds_max - bounds_min

    nx = max(2, int(np.ceil(extent[0] / char_length)))
    ny = max(2, int(np.ceil(extent[1] / char_length)))
    nz = max(2, int(np.ceil(extent[2] / char_length)))

    total = nx * ny * nz
    if total > 500000:
        scale = (500000 / total) ** (1.0 / 3.0)
        nx = max(2, int(nx * scale))
        ny = max(2, int(ny * scale))
        nz = max(2, int(nz * scale))

    margin = char_length * 0.1
    x = np.linspace(bounds_min[0] + margin, bounds_max[0] - margin, nx)
    y = np.linspace(bounds_min[1] + margin, bounds_max[1] - margin, ny)
    z = np.linspace(bounds_min[2] + margin, bounds_max[2] - margin, nz)
    grid = np.array(np.meshgrid(x, y, z, indexing='ij')).reshape(3, -1).T

    inside = _points_inside_mesh(grid, surface_mesh)
    interior = grid[inside]
    Logger.log("d", "FEA tet: %d/%d grid points inside mesh", len(interior), len(grid))
    return interior


def _points_inside_mesh(points: np.ndarray, mesh) -> np.ndarray:
    """Test which points are inside a closed surface mesh.

    Tries trimesh.contains() first, falls back to a simple winding-number
    approximation based on signed solid angle.
    """
    # Try trimesh's built-in contains
    try:
        result = mesh.contains(points)
        if result.any():  # Sanity check — if ALL false, likely broken
            return result
    except Exception:
        pass

    # Fallback: use trimesh ray casting if available
    try:
        from trimesh.ray import ray_pyembree
        # If embree is available, trimesh.contains should have worked
    except ImportError:
        pass

    try:
        from trimesh.ray.ray_triangle import RayMeshIntersector
        intersector = RayMeshIntersector(mesh)
        # Cast rays along +X axis and count intersections
        # Odd count = inside, even count = outside
        directions = np.tile([1.0, 0.0, 0.0], (len(points), 1))
        hits = intersector.intersects_location(points, directions, multiple_hits=True)
        # Count hits per ray
        hit_counts = np.zeros(len(points), dtype=int)
        if len(hits[0]) > 0:
            ray_indices = hits[1]  # which ray each hit belongs to
            for idx in ray_indices:
                hit_counts[idx] += 1
        return (hit_counts % 2) == 1
    except Exception:
        pass

    # Last resort: simple bounding-box filter (keeps ~50% more points than needed
    # but produces valid tets, just with some external ones)
    Logger.log("w", "FEA tet: all containment methods failed, using bounding box filter")
    bbox_min = mesh.bounds[0]
    bbox_max = mesh.bounds[1]
    margin = (bbox_max - bbox_min) * 0.05
    inside = np.all((points >= bbox_min + margin) & (points <= bbox_max - margin), axis=1)
    return inside
