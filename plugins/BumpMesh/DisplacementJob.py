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
        params: dict
    ) -> None:
        super().__init__()
        self._node_ref = weakref.ref(node)
        self._vertices = vertices
        self._indices = indices
        self._texture_data = texture_data
        self._params = params
        self._result_mesh: Optional[MeshData] = None
        self._error: Optional[str] = None

    def getNode(self) -> Optional[SceneNode]:
        return self._node_ref()

    def getResultMesh(self) -> Optional[MeshData]:
        return self._result_mesh

    def getError(self) -> Optional[str]:
        return self._error

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
            # Uniform subdivision
            message.setProgress(5)
            vertices, indices = MeshSubdivider.subdivide(vertices, indices, subdivision_level)
            Job.yieldThread()
        elif subdivision_mode == 1:
            # Adaptive subdivision
            message.setProgress(5)
            vertices, indices = MeshSubdivider.subdivide_adaptive(
                vertices, indices, target_edge_length
            )
            Job.yieldThread()

        message.setProgress(20)

        # Step 2: Flatten mesh to triangle soup
        # This is critical: shared vertices at sharp edges get wrong averaged normals
        # which causes spikes and disconnected geometry. Flattening gives each face its
        # own vertices, then we recompute smooth normals with crease-angle detection.
        vertices = DisplacementEngine.flatten_mesh(vertices, indices)
        # After flattening, vertices is (M*3, 3) triangle soup — no index buffer needed
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

        # Step 5: Compute angle mask
        mask = DisplacementEngine.compute_angle_mask(normals, mask_angle)
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
