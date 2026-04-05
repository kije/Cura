# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

import numpy

from UM.Math.Color import Color
from UM.Math.Vector import Vector
from UM.Mesh.MeshBuilder import MeshBuilder
from UM.Scene.ToolHandle import ToolHandle


class BCHighlightHandle(ToolHandle):
    """Renders boundary condition overlays on the selected mesh.

    Fixed faces are shown in green; force faces in red. An arrow is drawn
    at the centroid of each force group indicating direction and magnitude.

    The handle is parented to the scene root and its transformation is set
    to the selected node's world transformation so that all face painting
    can be done in local (mesh) coordinates without manual re-projection.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._name = "FEABCHighlight"
        self._auto_scale = False
        self.setEnabled(False)  # Start disabled to avoid "batch without mesh" warnings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_visualization(self, node, bc_decorator,
                             pending_faces=None,
                             active_force_index=-1,
                             hover_faces=None) -> None:
        """Rebuild the highlight mesh from current BC data.

        Vertices are taken in local mesh space; the handle's world
        transformation is set to the node's world transformation so the
        overlay matches the rendered mesh exactly.

        Args:
            node: CuraSceneNode whose mesh is being decorated.
            bc_decorator: FEABoundaryConditionDecorator attached to *node*,
                or None if only pending faces should be shown.
            pending_faces: Optional list of face indices currently selected
                but not yet confirmed (shown in YELLOW).
            active_force_index: Index of the force group currently selected
                in the UI list. That group is shown in BRIGHT RED; others
                in darker red.
        """
        mb = MeshBuilder()
        mesh_data = node.getMeshData()
        if mesh_data is None:
            self.setSolidMesh(mb.build())
            return

        verts = mesh_data.getVertices()   # local-space (N, 3) float32
        indices = mesh_data.getIndices()  # (T, 3) int32 or None

        if verts is None:
            self.setSolidMesh(mb.build())
            return

        # Position the handle in world space so local-coord painting aligns.
        self.setEnabled(True)
        self.setTransformation(node.getWorldTransformation())

        if bc_decorator is not None:
            # Paint fixed faces GREEN
            green = Color(0, 200, 0, 200)
            for face_idx in bc_decorator.getFixedFaces():
                self._paint_face(mb, verts, indices, face_idx, green)

            # Paint force faces RED + arrows
            red_inactive = Color(150, 40, 40, 180)      # dim red for inactive forces
            red_active = Color(255, 60, 60, 220)         # bright red for active force
            blue = Color(0, 100, 255, 255)

            for i, fg in enumerate(bc_decorator.getForceGroups()):
                is_active = (i == active_force_index)
                face_color = red_active if is_active else red_inactive
                centroids = []
                for face_idx in fg.face_indices:
                    self._paint_face(mb, verts, indices, face_idx, face_color)
                    centroid = self._face_centroid(verts, indices, face_idx)
                    if centroid is not None:
                        centroids.append(centroid)

                if not centroids:
                    continue

                center = numpy.mean(centroids, axis=0)
                center_vec = Vector(float(center[0]), float(center[1]), float(center[2]))

                fx, fy, fz = fg.force.x, fg.force.y, fg.force.z
                mag = (fx ** 2 + fy ** 2 + fz ** 2) ** 0.5
                if mag > 0:
                    direction = Vector(fx / mag, fy / mag, fz / mag)
                    # Active force gets a bigger, brighter arrow
                    if is_active:
                        self._paint_arrow(mb, center_vec, direction, blue,
                                          head_length=4.0, head_width=2.0,
                                          tail_length=max(8.0, mag / 8.0),
                                          tail_width=0.6)
                    else:
                        self._paint_arrow(mb, center_vec, direction,
                                          Color(0, 80, 200, 200),
                                          head_length=2.5, head_width=1.2,
                                          tail_length=max(4.0, mag / 12.0),
                                          tail_width=0.3)

        # Paint PENDING faces in YELLOW (not yet confirmed)
        if pending_faces:
            yellow = Color(255, 220, 0, 200)
            for face_idx in pending_faces:
                self._paint_face(mb, verts, indices, face_idx, yellow)

        # Paint HOVER faces in ORANGE (preview of what would be selected)
        if hover_faces:
            orange = Color(255, 160, 40, 160)
            for face_idx in hover_faces:
                self._paint_face(mb, verts, indices, face_idx, orange)

        self.setSolidMesh(mb.build())

    def clear(self) -> None:
        """Remove all highlight geometry."""
        self.setEnabled(False)
        self.setSolidMesh(MeshBuilder().build())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _face_centroid(verts, indices, face_idx):
        """Return the centroid of *face_idx* in local coordinates, or None."""
        if indices is not None:
            if face_idx >= len(indices):
                return None
            tri = indices[face_idx]
            return (verts[tri[0]] + verts[tri[1]] + verts[tri[2]]) / 3.0
        else:
            base = face_idx * 3
            if base + 2 >= len(verts):
                return None
            return (verts[base] + verts[base + 1] + verts[base + 2]) / 3.0

    @staticmethod
    def _paint_face(mb: MeshBuilder, verts, indices, face_idx: int, color: Color) -> None:
        """Paint a single triangle face with *color* (local coordinates)."""
        if indices is not None:
            if face_idx >= len(indices):
                return
            tri = indices[face_idx]
            v0 = Vector(float(verts[tri[0]][0]), float(verts[tri[0]][1]), float(verts[tri[0]][2]))
            v1 = Vector(float(verts[tri[1]][0]), float(verts[tri[1]][1]), float(verts[tri[1]][2]))
            v2 = Vector(float(verts[tri[2]][0]), float(verts[tri[2]][1]), float(verts[tri[2]][2]))
        else:
            base = face_idx * 3
            if base + 2 >= len(verts):
                return
            v0 = Vector(float(verts[base][0]),     float(verts[base][1]),     float(verts[base][2]))
            v1 = Vector(float(verts[base + 1][0]), float(verts[base + 1][1]), float(verts[base + 1][2]))
            v2 = Vector(float(verts[base + 2][0]), float(verts[base + 2][1]), float(verts[base + 2][2]))

        mb.addFace(v0, v1, v2, color=color)
        mb.addFace(v2, v1, v0, color=color)  # back face so overlay is visible from both sides

    @staticmethod
    def _paint_arrow(mb: MeshBuilder, center: Vector, direction: Vector, color: Color,
                     head_length: float, head_width: float,
                     tail_length: float, tail_width: float) -> None:
        """Paint a 3D arrow starting at *center* pointing in *direction*.

        The arrow is made up of:
        - A diamond cone head (6 triangles, 3 perpendicular axes × 2 faces each)
        - A thin rectangular tail (6 faces)

        Args:
            mb: MeshBuilder to append geometry to.
            center: Arrow tip position in local coordinates.
            direction: Normalised direction vector.
            color: RGBA color for all arrow faces.
            head_length: Length of the arrowhead cone along *direction*.
            head_width: Half-width of the arrowhead base diamond.
            tail_length: Length of the arrow shaft.
            tail_width: Half-width of the shaft cross-section.
        """
        n = direction.normalized()
        p_tip = center
        p_base = center + n * head_length      # where head meets shaft
        p_tail_end = center + n * (head_length + tail_length)

        # Build two perpendicular vectors to *n* for the cross-section.
        # Pick an arbitrary non-parallel vector to cross with.
        arbitrary = Vector(0.0, 1.0, 0.0) if abs(n.y) < 0.9 else Vector(1.0, 0.0, 0.0)
        perp1 = n.cross(arbitrary).normalized()
        perp2 = n.cross(perp1).normalized()

        # ── Arrowhead ──────────────────────────────────────────────────
        # Four base corners of the diamond
        c1 = p_base + perp1 * head_width
        c2 = p_base - perp1 * head_width
        c3 = p_base + perp2 * head_width
        c4 = p_base - perp2 * head_width

        for corner_a, corner_b in [(c1, c3), (c3, c2), (c2, c4), (c4, c1)]:
            mb.addFace(corner_a, p_tip, corner_b, color=color)
            mb.addFace(corner_b, p_tip, corner_a, color=color)

        # ── Shaft / tail ───────────────────────────────────────────────
        # Four rectangular faces of the shaft prism
        s1 = p_base + perp1 * tail_width
        s2 = p_base - perp1 * tail_width
        s3 = p_base + perp2 * tail_width
        s4 = p_base - perp2 * tail_width
        e1 = p_tail_end + perp1 * tail_width
        e2 = p_tail_end - perp1 * tail_width
        e3 = p_tail_end + perp2 * tail_width
        e4 = p_tail_end - perp2 * tail_width

        for sa, sb, ea, eb in [(s1, s3, e1, e3), (s3, s2, e3, e2),
                                (s2, s4, e2, e4), (s4, s1, e4, e1)]:
            mb.addFace(sa, ea, sb, color=color)
            mb.addFace(sb, ea, eb, color=color)
            mb.addFace(sb, ea, sa, color=color)  # back faces
            mb.addFace(eb, ea, sb, color=color)
