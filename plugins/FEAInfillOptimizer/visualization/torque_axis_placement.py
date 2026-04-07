# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Selectable SceneNode representing a torque rotation axis.

The user moves and rotates this node with Cura's native Move/Rotate tools
to position it where the physical rotation axis is (shaft, hinge pin, bolt).
After confirming, the plugin extracts position + direction from the node's
world transformation.
"""

import math

import numpy as np

from UM.Math.Color import Color
from UM.Math.Vector import Vector
from UM.Mesh.MeshBuilder import MeshBuilder
from UM.Scene.SceneNode import SceneNode


class TorqueAxisPlacementNode(SceneNode):
    """A visible cylinder + endpoint spheres representing a rotation axis."""

    def __init__(self, parent=None, length: float = 100.0) -> None:
        super().__init__(parent)
        self._name = "TorqueAxisPlacement"
        self._selectable = True
        self._length = length

        self._build_mesh(length)

    def _build_mesh(self, length: float) -> None:
        """Build a thin cylinder along local +Y with small spheres at endpoints."""
        builder = MeshBuilder()

        radius = 1.0  # 1mm radius cylinder
        sphere_radius = 2.5  # endpoint markers
        segments = 16

        # -- Cylinder along Y axis from -length/2 to +length/2 --
        half = length / 2.0
        for i in range(segments):
            a0 = 2.0 * math.pi * i / segments
            a1 = 2.0 * math.pi * (i + 1) / segments
            x0, z0 = radius * math.cos(a0), radius * math.sin(a0)
            x1, z1 = radius * math.cos(a1), radius * math.sin(a1)

            # Two triangles per quad
            v0 = [x0, -half, z0]
            v1 = [x1, -half, z1]
            v2 = [x1, half, z1]
            v3 = [x0, half, z0]

            # Cyan color (0, 0.8, 0.9, 0.7)
            cr, cg, cb, ca = 0.0, 0.8, 0.9, 0.7
            builder.addFace(
                Vector(*v0), Vector(*v2), Vector(*v1),
                color=Color(cr, cg, cb, ca)
            )
            builder.addFace(
                Vector(*v0), Vector(*v3), Vector(*v2),
                color=Color(cr, cg, cb, ca)
            )

        # -- Endpoint spheres --
        for y_center in [-half, half]:
            self._add_sphere(builder, 0, y_center, 0, sphere_radius, segments)

        mesh_data = builder.build()
        self.setMeshData(mesh_data)

    def _add_sphere(self, builder: MeshBuilder, cx: float, cy: float, cz: float,
                    radius: float, segments: int) -> None:
        """Add a simple low-poly sphere to the mesh builder."""
        rings = segments // 2
        cr, cg, cb, ca = 0.0, 0.9, 1.0, 0.85  # bright cyan for endpoints

        for i in range(rings):
            phi0 = math.pi * i / rings
            phi1 = math.pi * (i + 1) / rings
            for j in range(segments):
                theta0 = 2.0 * math.pi * j / segments
                theta1 = 2.0 * math.pi * (j + 1) / segments

                def _pt(phi, theta):
                    return Vector(
                        cx + radius * math.sin(phi) * math.cos(theta),
                        cy + radius * math.cos(phi),
                        cz + radius * math.sin(phi) * math.sin(theta)
                    )

                p00 = _pt(phi0, theta0)
                p10 = _pt(phi1, theta0)
                p01 = _pt(phi0, theta1)
                p11 = _pt(phi1, theta1)

                builder.addFace(p00, p10, p11, color=Color(cr, cg, cb, ca))
                builder.addFace(p00, p11, p01, color=Color(cr, cg, cb, ca))

    def get_axis_position(self) -> Vector:
        """Extract the axis origin from the node's world transform."""
        world = self.getWorldTransformation().getData()
        p = world[:3, 3]
        return Vector(float(p[0]), float(p[1]), float(p[2]))

    def get_axis_direction(self) -> Vector:
        """Extract the axis direction (local +Y in world space), normalized."""
        world = self.getWorldTransformation().getData()
        d_raw = world[:3, 1]  # Y-axis column
        norm = float(np.linalg.norm(d_raw))
        if norm < 1e-12:
            return Vector(0, 1, 0)
        d = d_raw / norm
        return Vector(float(d[0]), float(d[1]), float(d[2]))
