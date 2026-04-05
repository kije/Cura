# Copyright (c) 2025 BumpMesh Plugin
# Released under the terms of the LGPLv3 or higher.

from . import BumpMeshTool

from UM.i18n import i18nCatalog
i18n_catalog = i18nCatalog("cura")


def getMetaData():
    return {
        "tool": {
            "name": i18n_catalog.i18nc("@action:button", "BumpMesh"),
            "description": i18n_catalog.i18nc("@info:tooltip", "Apply displacement textures to model surfaces"),
            "icon": "MeshTypeNormal",
            "tool_panel": "BumpMeshTool.qml",
            "weight": 5
        }
    }


def register(app):
    return {"tool": BumpMeshTool.BumpMeshTool()}
