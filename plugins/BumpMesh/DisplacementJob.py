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
        # Per-original-vertex weights from face painting (None or copy of weights)
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
        """Resample per-vertex weights from an original mesh to a new mesh.

        Used after adaptive subdivision to map face mask weights from the original
        vertices to the subdivided vertex set. For each new vertex, finds the
        nearest original vertex and inherits its weight.

        :param new_vertices: (N_new, 3) new vertex positions.
        :param orig_vertices: (N_orig, 3) original vertex positions.
        :param orig_weights: (N_orig,) original per-vertex weights.
        :return: (N_new,) resampled weights.
        """
        # Brute-force nearest neighbor (acceptable for typical mesh sizes).
        # For each new vertex, find the closest original vertex.
        # We process in chunks to avoid huge intermediate arrays.
        n_new = len(new_vertices)
        result = numpy.empty(n_new, dtype=numpy.float32)
        chunk = 4096
        for i in range(0, n_new, chunk):
            block = new_vertices[i:i + chunk]
            # (chunk, N_orig) distance matrix would be too large; use squared distance
            # via expansion for moderate sizes
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
        face_mask_weights = self._face_mask_weights  # per-original-vertex weights or None

        if indices is None:
            # If no index buffer, create sequential indices (every 3 vertices = 1 triangle)
            num_verts = len(vertices)
            indices = numpy.arange(num_verts, dtype=numpy.int32).reshape(-1, 3)

        subdivision_level = self._params.get("subdivision_level", 0)
        subdivision_mode = self._params.get("subdivision_mode", 0)
        target_edge_length = self._params.get("target_edge_length", 1.0)
        projection_mode = self._params.get("projection_mode", 0)
        amplitude = self._params.get("amplitude", 1.0)
        mask_angle = self._params.get("mask_angle", 0.0)
        smoothing = self._params.get("smoothing", 0)

        # Step 1: Subdivide mesh (with shared vertices for correct topology)
        if subdivision_mode == 0 and subdivision_level > 0:
            # Uniform subdivision — propagate face mask weights through midpoint averaging
            message.setProgress(5)
            if face_mask_weights is not None:
                vertices, indices, face_mask_weights = MeshSubdivider.subdivide(
                    vertices, indices, subdivision_level, vertex_attr=face_mask_weights
                )
            else:
                vertices, indices = MeshSubdivider.subdivide(vertices, indices, subdivision_level)
            Job.yieldThread()
        elif subdivision_mode == 1:
            # Adaptive subdivision
            message.setProgress(5)
            # Save originals for nearest-vertex resampling of face mask weights
            orig_verts_for_resample = vertices if face_mask_weights is not None else None
            orig_weights_for_resample = face_mask_weights
            vertices, indices = MeshSubdivider.subdivide_adaptive(
                vertices, indices, target_edge_length
            )
            # Resample face mask weights to new vertices via nearest-position lookup
            if orig_weights_for_resample is not None:
                face_mask_weights = self._resample_weights(
                    vertices, orig_verts_for_resample, orig_weights_for_resample
                )
            Job.yieldThread()

        message.setProgress(20)

        # Step 2: Flatten mesh to triangle soup
        # This is critical: shared vertices at sharp edges get wrong averaged normals
        # which causes spikes and disconnected geometry. Flattening gives each face its
        # own vertices, then we recompute smooth normals with crease-angle detection.
        vertices = DisplacementEngine.flatten_mesh(vertices, indices)
        # Also flatten the face mask weights to match the triangle soup vertex count
        flat_face_mask = None
        if face_mask_weights is not None:
            flat_face_mask = face_mask_weights[indices.ravel()].astype(numpy.float32)
        Job.yieldThread()
        message.setProgress(30)

        # Step 3: Compute normals with smooth-group detection
        # Coincident vertices get averaged normals only if their face normals are
        # within 60 degrees of each other. Sharp edges get per-face normals.
        normals = DisplacementEngine.compute_flat_normals(vertices)
        Job.yieldThread()
        message.setProgress(40)

        # Step 4: Smooth texture if needed
        texture = self._texture_data
        if smoothing > 0:
            texture = DisplacementEngine.smooth_texture(texture, smoothing)
            Job.yieldThread()
        message.setProgress(50)

        # Step 5: Compute angle mask, multiplied by face paint mask
        mask = DisplacementEngine.compute_angle_mask(normals, mask_angle)
        if flat_face_mask is not None:
            mask = mask * flat_face_mask
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
        new_vertices = DisplacementEngine.displace(vertices, normals, displacement_values, amplitude, mask)
        message.setProgress(80)

        # Step 8: Build new mesh (triangle soup — no index buffer)
        builder = MeshBuilder()
        builder.setVertices(new_vertices)
        builder.calculateNormals(fast=True)
        self._result_mesh = builder.build()

        Job.yieldThread()
        message.setProgress(100)
