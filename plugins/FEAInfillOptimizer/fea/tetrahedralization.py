# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Convert a surface trimesh to a volumetric tetrahedral mesh.

Tries gmsh first (best quality), falls back to scipy Delaunay if gmsh fails.
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

try:
    import pytetwild
except ImportError:
    pytetwild = None  # type: ignore[assignment]

from UM.Logger import Logger

_GMSH_LOCK = threading.Lock()

_PRESET_FRACTIONS: Dict[str, float] = {
    "coarse": 0.15,
    "medium": 0.06,
    "fine": 0.03,
}

# Track which containment method was last used
_last_containment_method = "unknown"


@dataclass
class TetMesh:
    nodes: np.ndarray
    elements: np.ndarray
    surface_node_map: Dict[int, int] = field(default_factory=dict)
    mesh_quality: str = "high"
    mesh_method: str = ""
    warnings: list = field(default_factory=list)
    msh_path: str = ""


def tetrahedralize(surface_mesh, element_size) -> TetMesh:
    if isinstance(element_size, str):
        fraction = _PRESET_FRACTIONS.get(element_size, _PRESET_FRACTIONS["medium"])
        diagonal = float(np.linalg.norm(surface_mesh.bounds[1] - surface_mesh.bounds[0]))
        char_length = max(diagonal * fraction, 0.1)
    else:
        char_length = float(element_size)

    Logger.log("d", "FEA tetrahedralization: char_length=%.3f mm for mesh with %d vertices",
               char_length, len(surface_mesh.vertices))

    # Try pytetwild first (most robust, handles non-manifold meshes)
    if pytetwild is not None:
        try:
            return _tetrahedralize_pytetwild(surface_mesh, char_length)
        except Exception as e:
            Logger.log("w", "FEA tet: pytetwild failed (%s), trying gmsh...", str(e))

    # Try gmsh second (good quality but fragile on some meshes)
    if gmsh is not None:
        try:
            return _tetrahedralize_gmsh(surface_mesh, char_length)
        except Exception as e:
            Logger.log("w", "FEA tet: gmsh failed (%s), falling back to scipy", str(e))

    # Last resort: scipy Delaunay
    Logger.log("d", "FEA tet: using scipy Delaunay fallback")
    return _tetrahedralize_scipy(surface_mesh, char_length)


def _tetrahedralize_pytetwild(surface_mesh, char_length: float) -> TetMesh:
    """Tetrahedralize using pytetwild (fTetWild) — most robust method."""
    V = np.array(surface_mesh.vertices, dtype=np.float64)
    F = np.array(surface_mesh.faces, dtype=np.int32)

    diagonal = float(np.linalg.norm(surface_mesh.bounds[1] - surface_mesh.bounds[0]))
    edge_fac = char_length / diagonal if diagonal > 0 else 0.06

    Logger.log("d", "FEA tet: pytetwild — %d verts, %d faces, edge_fac=%.4f",
               len(V), len(F), edge_fac)

    v_out, tets = pytetwild.tetrahedralize(V, F, edge_length_fac=edge_fac, optimize=True)

    nodes = np.array(v_out, dtype=np.float64)
    elements = np.array(tets, dtype=np.int64)

    Logger.log("d", "FEA tet: pytetwild — %d nodes, %d tets", len(nodes), len(elements))

    if len(elements) == 0:
        raise RuntimeError("pytetwild produced no tetrahedra")

    # Save as .msh for EasyFEA import via gmsh
    msh_path = ""
    if gmsh is not None:
        try:
            msh_tmp = tempfile.NamedTemporaryFile(suffix=".msh", delete=False)
            msh_tmp.close()
            msh_path = msh_tmp.name

            gmsh.initialize(interruptible=False)
            gmsh.option.setNumber("General.Verbosity", 0)

            # Add nodes
            node_tags = list(range(1, len(nodes) + 1))
            gmsh.model.addDiscreteEntity(3, 1)
            gmsh.model.mesh.addNodes(3, 1, node_tags, nodes.flatten().tolist())

            # Add tet elements (type 4 = linear tetrahedron)
            elem_tags = list(range(1, len(elements) + 1))
            # gmsh expects 1-based node tags
            node_tags_flat = (elements + 1).flatten().tolist()
            gmsh.model.mesh.addElements(3, 1, [4], [elem_tags], [node_tags_flat])

            gmsh.write(msh_path)
            gmsh.finalize()
            Logger.log("d", "FEA tet: saved pytetwild mesh to %s", msh_path)
        except Exception as e:
            Logger.log("w", "FEA tet: failed to save .msh for EasyFEA: %s", str(e))
            msh_path = ""
            try:
                gmsh.finalize()
            except Exception:
                pass

    # Build surface_node_map using KDTree
    import scipy.spatial
    surface_node_map: Dict[int, int] = {}
    tolerance = char_length * 0.1
    kd_tree = scipy.spatial.KDTree(nodes)
    dists, indices = kd_tree.query(surface_mesh.vertices)
    for surf_idx in range(len(surface_mesh.vertices)):
        if dists[surf_idx] < tolerance:
            surface_node_map[surf_idx] = int(indices[surf_idx])

    Logger.log("d", "FEA tet: pytetwild — %d surface nodes mapped", len(surface_node_map))

    return TetMesh(
        nodes=nodes, elements=elements, surface_node_map=surface_node_map,
        mesh_quality="high",
        mesh_method="fTetWild (pytetwild) robust tetrahedralization",
        msh_path=msh_path,
    )


def _tetrahedralize_gmsh(surface_mesh, char_length: float) -> TetMesh:
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

            Logger.log("d", "FEA tet: merging STL...")
            gmsh.merge(stl_path)

            # classifySurfaces with forReparametrization=FALSE (the True
            # setting was causing infinite hangs in gmsh 4.15.2).
            # Also use a generous angle threshold to group coplanar triangles.
            Logger.log("d", "FEA tet: classifying surfaces (forReparam=False)...")
            angle = 40.0
            gmsh.model.mesh.classifySurfaces(
                np.deg2rad(angle),      # angle threshold
                True,                    # boundary
                False,                   # forReparametrization — was True, caused hang!
                np.deg2rad(180.0),       # curveAngle
            )
            Logger.log("d", "FEA tet: classifySurfaces completed")

            Logger.log("d", "FEA tet: creating geometry...")
            gmsh.model.mesh.createGeometry()

            surfaces = gmsh.model.getEntities(2)
            Logger.log("d", "FEA tet: %d surfaces found", len(surfaces))

            if not surfaces:
                raise RuntimeError("No surfaces after classifySurfaces")

            Logger.log("d", "FEA tet: creating volume...")
            sl = gmsh.model.geo.addSurfaceLoop([s[1] for s in surfaces])
            gmsh.model.geo.addVolume([sl])
            gmsh.model.geo.synchronize()

            gmsh.option.setNumber("Mesh.CharacteristicLengthMax", char_length)
            gmsh.option.setNumber("Mesh.CharacteristicLengthMin", char_length * 0.1)
            gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay

            Logger.log("d", "FEA tet: generating 3D mesh...")
            gmsh.model.mesh.generate(3)

            Logger.log("d", "FEA tet: optimizing...")
            try:
                gmsh.model.mesh.optimize("Netgen")
            except Exception:
                Logger.log("w", "FEA tet: Netgen optimization skipped")

            # Save .msh file for EasyFEA import before extracting nodes
            msh_tmp = tempfile.NamedTemporaryFile(suffix=".msh", delete=False)
            msh_tmp.close()
            msh_path = msh_tmp.name
            gmsh.write(msh_path)
            Logger.log("d", "FEA tet: saved mesh to %s", msh_path)

            Logger.log("d", "FEA tet: extracting mesh data...")

            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            nodes = np.array(node_coords, dtype=np.float64).reshape(-1, 3)
            tag_to_idx = {int(tag): i for i, tag in enumerate(node_tags)}

            elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements()
            tet_nodes = []
            for etype, _, enodes in zip(elem_types, elem_tags, elem_node_tags):
                if etype == 4:
                    tet_nodes.append(np.array(enodes, dtype=np.int64).reshape(-1, 4))

            if not tet_nodes:
                raise RuntimeError("Gmsh produced no tetrahedral elements")

            elements_gmsh = np.vstack(tet_nodes)
            elements = np.vectorize(tag_to_idx.__getitem__)(elements_gmsh)

            Logger.log("d", "FEA tet: %d nodes, %d tets", len(nodes), len(elements))

        finally:
            try:
                gmsh.finalize()
            except Exception:
                pass

    # Build surface_node_map
    import scipy.spatial
    surface_node_map: Dict[int, int] = {}
    tolerance = char_length * 0.1
    kd_tree = scipy.spatial.KDTree(nodes)
    dists, indices = kd_tree.query(surface_mesh.vertices)
    for surf_idx in range(len(surface_mesh.vertices)):
        if dists[surf_idx] < tolerance:
            surface_node_map[surf_idx] = int(indices[surf_idx])

    Logger.log("d", "FEA tet: %d surface nodes mapped", len(surface_node_map))

    return TetMesh(
        nodes=nodes, elements=elements, surface_node_map=surface_node_map,
        mesh_quality="high",
        mesh_method="Gmsh Delaunay tetrahedralization",
        msh_path=msh_path,
    )


def _tetrahedralize_scipy(surface_mesh, char_length: float) -> TetMesh:
    """Fallback using scipy.spatial.Delaunay."""
    from scipy.spatial import Delaunay

    surface_verts = np.array(surface_mesh.vertices, dtype=np.float64)
    n_surface = len(surface_verts)
    warnings = ["Gmsh unavailable — using scipy Delaunay (reduced mesh quality near sharp features)"]

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

    containment_quality = _last_containment_method
    if containment_quality == "bbox":
        quality = "low"
        warnings.append("Point containment used bounding-box approximation — "
                        "some elements may be outside the model surface")
    else:
        quality = "medium"

    if len(elements) == 0:
        raise RuntimeError("No interior tetrahedra found. Mesh may not be watertight.")

    surface_node_map = {i: i for i in range(n_surface)}

    Logger.log("d", "FEA tet (scipy): %d nodes, %d tets, quality=%s",
               len(all_points), len(elements), quality)

    return TetMesh(
        nodes=all_points,
        elements=np.array(elements, dtype=np.int64),
        surface_node_map=surface_node_map,
        mesh_quality=quality,
        mesh_method="Scipy Delaunay + %s containment" % containment_quality,
        warnings=warnings,
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
    """Test which points are inside a closed surface mesh."""
    global _last_containment_method

    # Method 1: trimesh.contains()
    try:
        result = mesh.contains(points)
        if result.any():
            _last_containment_method = "trimesh"
            Logger.log("d", "FEA tet: containment via trimesh.contains()")
            return result
    except Exception:
        pass

    # Method 2: ray casting
    try:
        from trimesh.ray.ray_triangle import RayMeshIntersector
        intersector = RayMeshIntersector(mesh)
        directions = np.tile([1.0, 0.0, 0.0], (len(points), 1))
        hits = intersector.intersects_location(points, directions, multiple_hits=True)
        hit_counts = np.zeros(len(points), dtype=int)
        if len(hits[0]) > 0:
            for idx in hits[1]:
                hit_counts[idx] += 1
        result = (hit_counts % 2) == 1
        if result.any():
            _last_containment_method = "raycast"
            Logger.log("d", "FEA tet: containment via ray casting (%d/%d inside)",
                       result.sum(), len(result))
            return result
    except Exception as e:
        Logger.log("w", "FEA tet: ray casting failed: %s", str(e))

    # Method 3: bounding box
    Logger.log("w", "FEA tet: all containment methods failed, using bounding box")
    _last_containment_method = "bbox"
    bbox_min = mesh.bounds[0]
    bbox_max = mesh.bounds[1]
    margin = (bbox_max - bbox_min) * 0.05
    return np.all((points >= bbox_min + margin) & (points <= bbox_max - margin), axis=1)
