# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

try:
    from . import NonPlanarSlicingExtension
except ImportError:
    NonPlanarSlicingExtension = None  # type: ignore[assignment,misc]


def getMetaData():
    return {}


def register(app):
    return {"extension": NonPlanarSlicingExtension.NonPlanarSlicingExtension()}
