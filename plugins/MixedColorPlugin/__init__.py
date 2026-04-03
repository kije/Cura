# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

from . import MixedColorPlugin


def getMetaData():
    return {}


def register(app):
    return {"extension": MixedColorPlugin.MixedColorPlugin()}
