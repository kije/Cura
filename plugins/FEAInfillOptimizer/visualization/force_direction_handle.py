# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""ToolHandle that draws rotation rings for visually setting force direction.

The handle renders three coloured donut rings (X=red, Y=green, Z=blue)
centred at the force application centroid.  Dragging a ring rotates the
force direction vector around the corresponding axis.  The visual arrow
from ``BCHighlightHandle`` updates in real-time during the drag.
"""

import math
from typing import Optional

import numpy as np

from UM.Math.Plane import Plane
from UM.Math.Quaternion import Quaternion
from UM.Math.Vector import Vector
from UM.Mesh.MeshBuilder import MeshBuilder
from UM.Scene.ToolHandle import ToolHandle


class ForceDirectionHandle(ToolHandle):
    """Rotation gizmo for setting force direction on a force group."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._name = "FEAForceDirectionHandle"
        self._auto_scale = False

        # Ring geometry — scaled relative to the model; will be overridden
        # per-force-group based on bounding box.
        self._inner_radius = 12.0
        self._outer_radius = 12.5
        self._line_width = 0.5
        self._active_inner_radius = 10.0
        self._active_outer_radius = 15.0
        self._active_line_width = 0.8

        self._center = Vector(0, 0, 0)
        self._visible = False

    @property
    def center(self) -> Vector:
        return self._center

    def show_at(self, center: Vector, scale: float = 1.0,
                axis_direction: Optional[Vector] = None) -> None:
        """Build and display the rotation rings at the given world position.

        Args:
            center: World-space position to place the gizmo.
            scale: Size multiplier based on model bounding box.
            axis_direction: Optional direction vector to draw a visible axis
                line through the center. Used for torque axis visualization
                so the user can see what they're rotating. If None, only
                the rotation rings are shown (force editing mode).
        """
        self._center = center
        self._visible = True
        self.setEnabled(True)

        # Scale ring size relative to model
        inner = self._inner_radius * scale
        outer = self._outer_radius * scale
        width = self._line_width * scale
        a_inner = self._active_inner_radius * scale
        a_outer = self._active_outer_radius * scale
        a_width = self._active_line_width * scale

        # Solid (visible) mesh — three coloured rings
        mb = MeshBuilder()
        mb.addDonut(
            inner_radius=inner, outer_radius=outer, width=width,
            color=self._z_axis_color, center=center
        )
        mb.addDonut(
            inner_radius=inner, outer_radius=outer, width=width,
            axis=Vector.Unit_X, angle=math.pi / 2,
            color=self._y_axis_color, center=center
        )
        mb.addDonut(
            inner_radius=inner, outer_radius=outer, width=width,
            axis=Vector.Unit_Y, angle=math.pi / 2,
            color=self._x_axis_color, center=center
        )

        # Axis direction line — a visible cylinder along the axis direction
        # so the user can see what they're rotating (critical for torque editing)
        if axis_direction is not None:
            axis_len = outer * 2.0  # extend beyond the rings
            d = axis_direction.normalized()
            # Cyan color for the axis line (distinct from ring colors)
            axis_color = ToolHandle._DisabledSelectionColor  # fallback
            try:
                from UM.Math.Color import Color
                axis_color = Color(0.0, 0.85, 0.95, 1.0)  # cyan
            except Exception:
                pass
            p1 = center + d * axis_len
            p2 = center - d * axis_len
            mb.addCube(
                width=width * 1.5, height=axis_len * 2, depth=width * 1.5,
                center=center, color=axis_color,
            )
            # Draw small cone arrowheads at both ends
            # Use addVertex for a simple line representation
            # Actually, addCube stretched along the axis is simplest —
            # but it's always axis-aligned. Use two small cubes at the tips instead.
            tip_size = width * 3
            mb.addCube(
                width=tip_size, height=tip_size, depth=tip_size,
                center=p1, color=axis_color,
            )
            mb.addCube(
                width=tip_size, height=tip_size, depth=tip_size,
                center=p2, color=axis_color,
            )

        self.setSolidMesh(mb.build())

        # Selection (hit-test) mesh — wider invisible rings for easier clicking
        smb = MeshBuilder()
        smb.addDonut(
            inner_radius=a_inner, outer_radius=a_outer, width=a_width,
            color=ToolHandle.ZAxisSelectionColor, center=center
        )
        smb.addDonut(
            inner_radius=a_inner, outer_radius=a_outer, width=a_width,
            axis=Vector.Unit_X, angle=math.pi / 2,
            color=ToolHandle.YAxisSelectionColor, center=center
        )
        smb.addDonut(
            inner_radius=a_inner, outer_radius=a_outer, width=a_width,
            axis=Vector.Unit_Y, angle=math.pi / 2,
            color=ToolHandle.XAxisSelectionColor, center=center
        )
        self.setSelectionMesh(smb.build())

    def hide(self) -> None:
        """Remove the rings from the viewport."""
        self._visible = False
        self.setEnabled(False)
        mb = MeshBuilder()
        self.setSolidMesh(mb.build())
        self.setSelectionMesh(mb.build())

    @property
    def is_visible(self) -> bool:
        return self._visible

    def isAxis(self, selection_id: int) -> bool:
        """Return True if *selection_id* corresponds to one of our ring axes."""
        return selection_id in (
            ToolHandle.XAxis, ToolHandle.YAxis, ToolHandle.ZAxis
        )


def compute_face_normal(verts: np.ndarray, indices: Optional[np.ndarray],
                        face_indices: list) -> Vector:
    """Compute the average outward normal of a set of triangle faces.

    Args:
        verts: Vertex positions, shape (N, 3).
        indices: Triangle index array, shape (M, 3), or None for flat layout.
        face_indices: List of triangle face indices to average over.

    Returns:
        Normalised average normal as a UM Vector.  Falls back to -Y (downward)
        if computation fails.
    """
    normals = []
    for fi in face_indices:
        try:
            if indices is not None:
                if fi >= len(indices):
                    continue
                tri = indices[fi]
                v0, v1, v2 = verts[tri[0]], verts[tri[1]], verts[tri[2]]
            else:
                base = fi * 3
                if base + 2 >= len(verts):
                    continue
                v0, v1, v2 = verts[base], verts[base + 1], verts[base + 2]
            e1 = v1 - v0
            e2 = v2 - v0
            n = np.cross(e1, e2)
            length = float(np.linalg.norm(n))
            if length > 1e-12:
                normals.append(n / length)
        except (IndexError, ValueError):
            continue

    if normals:
        avg = np.mean(normals, axis=0)
        length = float(np.linalg.norm(avg))
        if length > 1e-12:
            avg /= length
            return Vector(float(avg[0]), float(avg[1]), float(avg[2]))

    # Fallback: downward
    return Vector(0, -1, 0)


def compute_face_centroid(verts: np.ndarray, indices: Optional[np.ndarray],
                          face_indices: list) -> Vector:
    """Compute the average centroid of a set of triangle faces.

    Returns a UM Vector in the same coordinate space as *verts*.
    """
    centroids = []
    for fi in face_indices:
        try:
            if indices is not None:
                if fi >= len(indices):
                    continue
                tri = indices[fi]
                c = (verts[tri[0]] + verts[tri[1]] + verts[tri[2]]) / 3.0
            else:
                base = fi * 3
                if base + 2 >= len(verts):
                    continue
                c = (verts[base] + verts[base + 1] + verts[base + 2]) / 3.0
            centroids.append(c)
        except (IndexError, ValueError):
            continue

    if centroids:
        avg = np.mean(centroids, axis=0)
        return Vector(float(avg[0]), float(avg[1]), float(avg[2]))
    return Vector(0, 0, 0)


def rotate_vector(vec: Vector, axis: Vector, angle: float) -> Vector:
    """Rotate *vec* around *axis* by *angle* radians using a quaternion."""
    q = Quaternion.fromAngleAxis(angle, axis)
    m = q.toMatrix()
    return vec.preMultiply(m)
