# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

import weakref
from typing import Optional

import numpy

from UM.Job import Job
from UM.Logger import Logger
from UM.Mesh.MeshBuilder import MeshBuilder
from UM.Mesh.MeshData import MeshData
from UM.Message import Message
from UM.Scene.SceneNode import SceneNode

from . import DisplacementEngine
from . import MeshSubdivider
from . import TextureProjector


class DisplacementJob(Job):
    """Background job that runs the full displacement pipeline.

    Mesh data (vertices/indices) is pre-copied on the main thread for thread safety.
    The node is held via a weak reference to avoid preventing garbage collection.
    """

    def __init__(
        self,
        node: SceneNode,
        vertices: numpy.ndarray,
        indices: Optional[numpy.ndarray],
        texture_data: numpy.ndarray,
        params: dict,
        face_mask_weights: Optional[numpy.ndarray] = None,
    ) -> None:
        super().__init__()
        self._node_ref = weakref.ref(node)
        self._vertices = vertices
        self._indices = indices
        self._texture_data = texture_data
        self._params = params
        self._face_mask_weights = face_mask_weights
        self._result_mesh: Optional[MeshData] = None
        self._error: Optional[str] = None

    def getNode(self) -> Optional[SceneNode]:
        return self._node_ref()

    def getResultMesh(self) -> Optional[MeshData]:
        return self._result_mesh

    def getError(self) -> Optional[str]:
        return self._error

    @staticmethod
    def _resample_weights(
        new_vertices: numpy.ndarray,
        orig_vertices: numpy.ndarray,
        orig_weights: numpy.ndarray,
    ) -> numpy.ndarray:
        """Resample per-vertex weights via nearest-vertex lookup (for adaptive subdivision)."""
        n_new = len(new_vertices)
        result = numpy.empty(n_new, dtype=numpy.float32)
        chunk = 4096
        for i in range(0, n_new, chunk):
            block = new_vertices[i:i + chunk]
            diff = block[:, numpy.newaxis, :] - orig_vertices[numpy.newaxis, :, :]
            sq_dist = numpy.sum(diff * diff, axis=2)
            nearest = numpy.argmin(sq_dist, axis=1)
            result[i:i + chunk] = orig_weights[nearest]
        return result

    def run(self) -> None:
        message = Message(
            "Applying displacement...",
            lifetime=0,
            dismissable=False,
            progress=0,
            title="BumpMesh"
        )
        message.show()

        try:
            self._run_pipeline(message)
        except MemoryError:
            Logger.logException("e", "Out of memory during displacement")
            self._result_mesh = None
            self._error = "Out of memory. Try lowering the subdivision level."
        except Exception:
            Logger.logException("e", "Error during displacement")
            self._result_mesh = None
            self._error = "Displacement failed. Check the log for details."
        finally:
            message.hide()

    def _run_pipeline(self, message: Message) -> None:
        vertices = self._vertices
        indices = self._indices
        face_mask_weights = self._face_mask_weights

        if indices is None:
            num_verts = len(vertices)
            indices = numpy.arange(num_verts, dtype=numpy.int32).reshape(-1, 3)

        subdivision_level = self._params.get("subdivision_level", 0)
        subdivision_mode = self._params.get("subdivision_mode", 0)
        target_edge_length = self._params.get("target_edge_length", 1.0)
        projection_mode = self._params.get("projection_mode", 0)
        amplitude = self._params.get("amplitude", 1.0)
        mask_angle = self._params.get("mask_angle", 0.0)
        smoothing = self._params.get("smoothing", 0)
        symmetric = self._params.get("symmetric", True)

        # Step 1: Subdivide mesh
        if subdivision_mode == 0 and subdivision_level > 0:
            message.setProgress(5)
            if face_mask_weights is not None:
                vertices, indices, face_mask_weights = MeshSubdivider.subdivide(
                    vertices, indices, subdivision_level, vertex_attr=face_mask_weights
                )
            else:
                vertices, indices = MeshSubdivider.subdivide(vertices, indices, subdivision_level)
            Job.yieldThread()
        elif subdivision_mode == 1:
            message.setProgress(5)
            orig_verts = vertices if face_mask_weights is not None else None
            orig_weights = face_mask_weights
            vertices, indices = MeshSubdivider.subdivide_adaptive(
                vertices, indices, target_edge_length
            )
            if orig_weights is not None:
                face_mask_weights = self._resample_weights(vertices, orig_verts, orig_weights)
            Job.yieldThread()

        message.setProgress(20)

        # Step 2: Flatten mesh to triangle soup
        vertices = DisplacementEngine.flatten_mesh(vertices, indices)
        flat_face_mask = None
        if face_mask_weights is not None:
            flat_face_mask = face_mask_weights[indices.ravel()].astype(numpy.float32)
        Job.yieldThread()
        message.setProgress(30)

        # Step 3: Compute normals with smooth-group detection (cos(30°) crease threshold)
        normals = DisplacementEngine.compute_flat_normals(vertices)
        Job.yieldThread()
        message.setProgress(40)

        # Step 4: Smooth texture (tile-then-blur-then-crop for seamless tiling)
        texture = self._texture_data
        if smoothing > 0:
            texture = DisplacementEngine.smooth_texture(texture, smoothing)
            Job.yieldThread()
        message.setProgress(50)

        # Step 5: Compute angle mask + face paint mask
        mask = DisplacementEngine.compute_angle_mask(normals, mask_angle)
        if flat_face_mask is not None:
            mask = mask * flat_face_mask

        # Apply boundary falloff to smooth mask transitions
        if flat_face_mask is not None:
            mask = DisplacementEngine.compute_boundary_falloff(vertices, mask, falloff_distance=2.0)
        message.setProgress(55)

        # Step 6: Sample displacement values
        if projection_mode == 0:  # Triplanar
            displacement_values = TextureProjector.sample_displacement_triplanar(
                vertices, normals, texture, self._params
            )
        else:
            uvs = TextureProjector.project(vertices, normals, projection_mode, self._params)
            displacement_values = TextureProjector.sample_displacement(uvs, texture)

        Job.yieldThread()
        message.setProgress(70)

        # Step 7: Displace vertices
        new_vertices = DisplacementEngine.displace(
            vertices, normals, displacement_values, amplitude, mask, symmetric=symmetric
        )

        # Step 7b: Weld coincident vertices to close gaps between adjacent faces.
        # After displacement, vertices that were at the same position may have moved
        # slightly apart (different normals/displacement). Averaging their final
        # positions closes the cracks while keeping triangle soup format.
        new_vertices = DisplacementEngine.weld_coincident_vertices(new_vertices)
        message.setProgress(80)

        # Step 8: Compute post-displacement normals via direct cross-product
        # (avoids averaging-based normals which can flip on excluded faces)
        post_normals = DisplacementEngine.compute_flat_face_normals(new_vertices)
        message.setProgress(90)

        # Step 9: Build new mesh (triangle soup — no index buffer)
        builder = MeshBuilder()
        builder.setVertices(new_vertices)
        # Set flat face normals directly instead of using calculateNormals
        # which uses vertex averaging that can produce incorrect results
        num_verts = len(new_vertices)
        builder._normals = post_normals
        builder._vertex_count = num_verts
        builder._face_count = num_verts // 3
        self._result_mesh = builder.build()

        Job.yieldThread()
        message.setProgress(100)
