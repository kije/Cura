# Copyright (c) 2025 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

"""Undoable command that applies a numpy bool mask to the paint texture.

Used by the image-projection feature: after projecting an image to a 1-bit
mask in texture space, we push an instance of this command onto the undo
stack so the operation can be reverted just like a normal stroke.
"""

from typing import Optional
import math

import numpy
from PyQt6.QtCore import QRect, QPoint
from PyQt6.QtGui import QUndoCommand, QImage, QPainter, QBrush

from UM.View.GL.Texture import Texture
from cura.Scene.SliceableObjectDecorator import SliceableObjectDecorator

from .PaintCommand import PaintCommand


class PaintImageCommand(PaintCommand):
    """Paints a bit-packed value into every texel that is True in the given mask."""

    def __init__(self,
                 texture: Texture,
                 mask: numpy.ndarray,
                 set_value: int,
                 bit_range: tuple[int, int],
                 sliceable_object_decorator: Optional[SliceableObjectDecorator] = None) -> None:
        # Image projection is always a fresh, non-mergeable command so we always
        # snapshot the previous state for undo.
        super().__init__(texture,
                         bit_range,
                         make_original_image = True,
                         sliceable_object_decorator = sliceable_object_decorator)

        self._mask: numpy.ndarray = numpy.asarray(mask, dtype = bool)
        self._set_value: int = int(set_value)
        self._calculateBoundingRect()

    def id(self) -> int:
        return 1

    def redo(self) -> None:
        img = self._texture.getImage()
        if img is None or self._mask.size == 0:
            return

        width = img.width()
        height = img.height()
        if width <= 0 or height <= 0:
            return

        # Same writable buffer pattern as MultiMaterialExtruderConverter.
        image_ptr = img.bits()
        image_ptr.setsize(img.sizeInBytes())
        image_array = numpy.frombuffer(image_ptr, dtype = numpy.uint32).reshape((height, width))

        mask = self._mask
        if mask.shape != (height, width):
            # Resize via nearest-neighbour crop / pad if shapes don't line up.
            resized = numpy.zeros((height, width), dtype = bool)
            copy_h = min(mask.shape[0], height)
            copy_w = min(mask.shape[1], width)
            resized[:copy_h, :copy_w] = mask[:copy_h, :copy_w]
            mask = resized

        bit_mask = numpy.uint32(self._getBitRangeMask())
        set_value = numpy.uint32(self._set_value) & bit_mask

        # Clear the bits in the range for masked texels, then OR in the new value.
        cleared = image_array[mask] & ~bit_mask
        image_array[mask] = cleared | set_value

        self._setPaintedExtrudersCountDirty()
        self._texture.updateImagePart(self._bounding_rect)

    def _clearTextureBits(self, painter: QPainter, extended: bool = False) -> None:
        # Called by the base class' undo path. We clear the bit range inside
        # the bounding rect of the mask. The base class then ORs back the
        # original snapshot on top.
        rect = self._bounding_rect
        if rect.isEmpty():
            return
        painter.setCompositionMode(QPainter.CompositionMode.RasterOp_NotSourceAndDestination)
        painter.fillRect(rect, QBrush(self._getBitRangeMask()))

    def _calculateBoundingRect(self) -> None:
        if self._mask.size == 0 or not self._mask.any():
            self._bounding_rect = QRect()
            return

        rows = numpy.any(self._mask, axis = 1)
        cols = numpy.any(self._mask, axis = 0)
        top = int(numpy.argmax(rows))
        bottom = int(len(rows) - 1 - numpy.argmax(rows[::-1]))
        left = int(numpy.argmax(cols))
        right = int(len(cols) - 1 - numpy.argmax(cols[::-1]))

        self._bounding_rect = QRect(QPoint(left, top), QPoint(right, bottom))
        self._bounding_rect &= self._texture.getImage().rect()
