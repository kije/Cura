# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

try:
    from . import NonPlanarSlicingExtension
except ImportError:
    NonPlanarSlicingExtension = None  # type: ignore[assignment,misc]

try:
    from .NonPlanarEnginePlugin import NonPlanarEnginePlugin
except ImportError:
    NonPlanarEnginePlugin = None  # type: ignore[assignment,misc]

try:
    from .visualization.NonPlanarView import NonPlanarView
except ImportError:
    NonPlanarView = None  # type: ignore[assignment,misc]


def getMetaData():
    metadata = {}
    if NonPlanarView is not None:
        metadata["view"] = {
            "name": "Non-Planar Regions",
            "weight": 2,
        }
    return metadata


def register(app):
    result = {}
    if NonPlanarSlicingExtension is not None:
        result["extension"] = NonPlanarSlicingExtension.NonPlanarSlicingExtension()
    if NonPlanarEnginePlugin is not None:
        result["backend_plugin"] = NonPlanarEnginePlugin()
    if NonPlanarView is not None:
        result["view"] = NonPlanarView()
    return result
