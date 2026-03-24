# Copyright (c) 2024 Community
# Released under the terms of the LGPLv3 or higher.

from . import ModelGroupsPlugin


def getMetaData():
    return {}


def register(app):
    return {"extension": ModelGroupsPlugin.ModelGroupsPlugin()}
