# Copyright (c) 2024 Ultimaker B.V.
# MultiBuildPlatePlugin is released under the terms of the LGPLv3 or higher.

from . import MultiBuildPlatePlugin


def getMetaData():
    return {}


def register(app):
    return {"extension": MultiBuildPlatePlugin.MultiBuildPlatePlugin()}
