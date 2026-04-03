# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Custom view that highlights non-planar candidate regions on the model.

Shows the model with color-coded regions:
- Green: safe for non-planar printing
- Yellow: blend zone (transition between planar and non-planar)
- Red: collision detected (will remain planar)
- Default: not a candidate for non-planar printing
"""

from __future__ import annotations

import logging
from typing import Optional

from UM.Application import Application
from UM.Event import Event
from UM.Math.Color import Color
from UM.Resources import Resources
from UM.Scene.Iterator.BreadthFirstIterator import BreadthFirstIterator
from UM.View.RenderBatch import RenderBatch
from UM.View.GL.OpenGL import OpenGL

from cura.CuraApplication import CuraApplication
from cura.CuraView import CuraView
from cura.Scene.ConvexHullNode import ConvexHullNode

logger = logging.getLogger(__name__)


class NonPlanarView(CuraView):
    """View that renders models with non-planar region highlighting.

    When active, renders the model using a solid shader but overlays
    non-planar candidate regions with color-coded transparency to
    indicate safe, blend, and collision zones.
    """

    def __init__(self) -> None:
        super().__init__(parent=None, use_empty_menu_placeholder=True)

        self._default_shader = None
        self._safe_shader = None
        self._blend_shader = None
        self._collision_shader = None

    def _ensureShaders(self) -> None:
        """Create shaders on first use (requires OpenGL context)."""
        if self._default_shader is not None:
            return

        gl = OpenGL.getInstance()

        # Default model shader (light gray)
        self._default_shader = gl.createShaderProgram(
            Resources.getPath(Resources.Shaders, "overhang.shader")
        )
        if self._default_shader is not None:
            self._default_shader.setUniformValue(
                "u_overhangColor", Color(0.5, 0.5, 0.5, 1.0)
            )

        # We use the same basic shader with different colors for each zone.
        # The "color" shader is simplest — a single flat color.
        # But overhang.shader supports per-face coloring which is better.
        # For now, we'll create multiple instances with different colors.

    def beginRendering(self) -> None:
        scene = self.getController().getScene()
        renderer = self.getRenderer()
        self._ensureShaders()

        if not self._default_shader:
            return

        # Get the extension instance to access analysis results
        extension = self._getExtension()
        analysis_result = None
        if extension is not None:
            analysis_result = getattr(extension, "_current_analysis", None)

        for node in BreadthFirstIterator(scene.getRoot()):
            if type(node) is ConvexHullNode:
                continue

            if not node.render(renderer):
                if node.getMeshData() and node.isVisible():
                    # Render the base model with default shader
                    renderer.queueNode(
                        node,
                        shader=self._default_shader,
                        type=RenderBatch.RenderType.Solid,
                    )

        # If we have analysis results, render the overlay nodes
        if analysis_result is not None:
            self._renderOverlays(renderer, scene)

    def _renderOverlays(self, renderer, scene) -> None:
        """Render any NonPlanarOverlayNode children in the scene."""
        from .region_overlay import NonPlanarOverlayNode

        for node in BreadthFirstIterator(scene.getRoot()):
            if isinstance(node, NonPlanarOverlayNode):
                node.render(renderer)

    def endRendering(self) -> None:
        pass

    def event(self, event) -> None:
        if event.type == Event.ViewActivateEvent:
            logger.info("Non-Planar view activated")
            # Ensure overlay is visible and trigger analysis
            extension = self._getExtension()
            if extension is not None:
                try:
                    extension._overlay_visible = True
                    extension._runAnalysis()
                except Exception:
                    logger.debug("Could not trigger analysis", exc_info=True)

        elif event.type == Event.ViewDeactivateEvent:
            logger.info("Non-Planar view deactivated")

    def _getExtension(self) -> Optional[object]:
        """Get the NonPlanarSlicingExtension instance."""
        try:
            plugin_registry = Application.getInstance().getPluginRegistry()
            return plugin_registry.getPluginObject("NonPlanarSlicing")
        except Exception:
            return None
