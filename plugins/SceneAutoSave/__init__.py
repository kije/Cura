# Copyright (c) 2024 Community Contributors
# Cura is released under the terms of the LGPLv3 or higher.

from .SceneAutoSaveExtension import SceneAutoSaveExtension


def getMetaData():
    return {}


def register(app):
    return {"extension": SceneAutoSaveExtension()}
