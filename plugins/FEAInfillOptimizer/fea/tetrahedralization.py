# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Convert a surface trimesh to a volumetric tetrahedral mesh using Gmsh."""

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

# Element-size presets as fractions of the bounding-box diagonal
_PRESET_FRACTIONS: Dict[str, float] = {
    "coarse": 0.15,   # ~5 mm on a 33 mm diagonal object
    "medium": 0.06,   # ~2 mm
    "fine": 0.03,     # ~1 mm
}


@dataclass
class TetMesh:
    """Volumetric tetrahedral mesh produced by Gmsh.

    Attributes:
        nodes: Node positions, shape (N, 3), float64.
        elements: Tetrahedral connectivity, shape (M, 4), int64 (0-based indices
            into ``nodes``).
        surface_node_map: Mapping from surface vertex index (into the original
            trimesh vertex array) to tet-mesh node index.
    """

    nodes: np.ndarray
    elements: np.ndarray
    surface_node_map: Dict[int, int] = field(default_factory=dict)


def tetrahedralize(surface_mesh: "trimesh.Trimesh", element_size: float) -> TetMesh:
    """Generate a 3D tetrahedral mesh from a closed surface mesh.

    Args:
        surface_mesh: Closed, (ideally watertight) trimesh.Trimesh surface.
        element_size: Characteristic element length (mm) or a preset name.

    Returns:
        A :class:`TetMesh` with nodes, elements, and surface_node_map populated.
    """
    if gmsh is None:
        raise ImportError("gmsh is required but not installed.")

    # Resolve preset or absolute size
    if isinstance(element_size, str):
        fraction = _PRESET_FRACTIONS.get(element_size, _PRESET_FRACTIONS["medium"])
        diagonal = float(np.linalg.norm(surface_mesh.bounds[1] - surface_mesh.bounds[0]))
        char_length = max(diagonal * fraction, 0.1)
    else:
        char_length = float(element_size)

    Logger.log(
        "d",
        "FEA tetrahedralization: char_length=%.3f mm for mesh with %d vertices",
        char_length,
        len(surface_mesh.vertices),
    )

    # We still need a temp STL because gmsh.model.occ.importShapes needs a file.
    # Direct API mesh input is possible but unreliable across gmsh versions.
    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Export as ASCII STL (binary STL can cause gmsh parse issues)
        surface_mesh.export(tmp_path, file_type="stl_ascii")
        tet_mesh = _run_gmsh(tmp_path, char_length, surface_mesh)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return tet_mesh


def _run_gmsh(
    stl_path: str, char_length: float, surface_mesh: "trimesh.Trimesh"
) -> TetMesh:
    """Internal: run Gmsh on an STL file and extract node/element arrays."""
    with _GMSH_LOCK:
        try:
            gmsh.initialize(interruptible=False)
            # Max verbosity so gmsh logs its internal progress
            gmsh.option.setNumber("General.Verbosity", 99)

            # Log the STL file details for debugging
            stl_size = os.path.getsize(stl_path)
            with open(stl_path, 'r') as f:
                first_line = f.readline().strip()
            Logger.log("d", "FEA tet: STL file: %d bytes, first line: '%s'", stl_size, first_line[:80])

            # Import the STL surface mesh
            Logger.log("d", "FEA tet: merging STL...")
            gmsh.merge(stl_path)

            # Log what gmsh got from the merge
            entities_0d = gmsh.model.getEntities(0)
            entities_1d = gmsh.model.getEntities(1)
            entities_2d = gmsh.model.getEntities(2)
            entities_3d = gmsh.model.getEntities(3)
            Logger.log("d", "FEA tet: after merge — points=%d, curves=%d, surfaces=%d, volumes=%d",
                       len(entities_0d), len(entities_1d), len(entities_2d), len(entities_3d))

            node_tags_pre, _, _ = gmsh.model.mesh.getNodes()
            elem_types_pre, _, _ = gmsh.model.mesh.getElements()
            Logger.log("d", "FEA tet: after merge — %d mesh nodes, %d element types",
                       len(node_tags_pre), len(elem_types_pre))

            # Try classifySurfaces with a thread-based timeout
            Logger.log("d", "FEA tet: attempting classifySurfaces (with 30s timeout)...")
            angle = 40.0
            classify_result = [None]  # mutable container for thread result
            classify_error = [None]

            def _do_classify():
                try:
                    gmsh.model.mesh.classifySurfaces(
                        np.deg2rad(angle), True, True, np.deg2rad(180.0)
                    )
                    classify_result[0] = True
                except Exception as e:
                    classify_error[0] = e

            import threading as _threading
            classify_thread = _threading.Thread(target=_do_classify, daemon=True)
            classify_thread.start()
            classify_thread.join(timeout=30.0)  # 30 second timeout

            if classify_thread.is_alive():
                Logger.log("e", "FEA tet: classifySurfaces TIMED OUT after 30s — "
                           "this gmsh version may have a bug with this mesh. "
                           "Attempting alternative approach...")
                # classifySurfaces is stuck — we can't kill the thread but we can
                # finalize gmsh and restart with a different approach
                raise RuntimeError("classifySurfaces timed out after 30 seconds. "
                                   "Try using a different mesh resolution.")

            if classify_error[0]:
                Logger.log("w", "FEA tet: classifySurfaces failed: %s", str(classify_error[0]))
                Logger.log("w", "FEA tet: trying direct 3D mesh without volume definition...")
            else:
                Logger.log("d", "FEA tet: classifySurfaces completed OK")
                Logger.log("d", "FEA tet: creating geometry...")
                gmsh.model.mesh.createGeometry()

                surfaces = gmsh.model.getEntities(2)
                Logger.log("d", "FEA tet: %d surfaces found, creating volume...", len(surfaces))
                if surfaces:
                    sl = gmsh.model.geo.addSurfaceLoop([s[1] for s in surfaces])
                    gmsh.model.geo.addVolume([sl])
                    gmsh.model.geo.synchronize()
                else:
                    Logger.log("w", "FEA tet: no surfaces found after classify, trying direct mesh...")

            gmsh.option.setNumber("Mesh.CharacteristicLengthMax", char_length)
            gmsh.option.setNumber("Mesh.CharacteristicLengthMin", char_length * 0.1)
            gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay

            Logger.log("d", "FEA tet: generating 3D mesh...")
            gmsh.model.mesh.generate(3)

            Logger.log("d", "FEA tet: optimizing mesh...")
            try:
                gmsh.model.mesh.optimize("Netgen")
            except Exception:
                Logger.log("w", "FEA tet: Netgen optimization failed, using unoptimized mesh")

            Logger.log("d", "FEA tet: mesh generation complete")

            # Extract nodes
            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            nodes = np.array(node_coords, dtype=np.float64).reshape(-1, 3)

            # Build 0-based tag → index map
            tag_to_idx: Dict[int, int] = {int(tag): i for i, tag in enumerate(node_tags)}

            # Extract tetrahedral elements (Gmsh element type 4 = 4-node tet)
            elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements()
            tet_nodes: list[np.ndarray] = []
            for etype, _, enodes in zip(elem_types, elem_tags, elem_node_tags):
                if etype == 4:  # linear tetrahedron
                    connectivity = np.array(enodes, dtype=np.int64).reshape(-1, 4)
                    tet_nodes.append(connectivity)

            if not tet_nodes:
                raise RuntimeError(
                    "Gmsh produced no tetrahedral elements. "
                    "Check that the surface mesh is closed and watertight."
                )

            elements_gmsh = np.vstack(tet_nodes)
            # Convert Gmsh 1-based tags to 0-based indices
            elements = np.vectorize(tag_to_idx.__getitem__)(elements_gmsh)

            Logger.log("d", "FEA tet: %d nodes, %d tets extracted", len(nodes), len(elements))

        finally:
            gmsh.finalize()

    # Build surface_node_map using KDTree for O(S log N)
    import scipy.spatial
    surface_node_map: Dict[int, int] = {}
    tolerance = char_length * 0.1
    kd_tree = scipy.spatial.KDTree(nodes)
    dists, indices = kd_tree.query(surface_mesh.vertices)
    for surf_idx in range(len(surface_mesh.vertices)):
        if dists[surf_idx] < tolerance:
            surface_node_map[surf_idx] = int(indices[surf_idx])

    Logger.log(
        "d",
        "FEA tetrahedralization: %d nodes, %d tets, %d surface nodes mapped",
        len(nodes),
        len(elements),
        len(surface_node_map),
    )

    return TetMesh(nodes=nodes, elements=elements, surface_node_map=surface_node_map)
