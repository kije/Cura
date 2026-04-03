# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from UM.i18n import i18nCatalog

i18n_catalog = i18nCatalog("cura")


def getMetaData():
    return {
        "tool": {
            "name": i18n_catalog.i18nc("@label", "FEA Boundary Conditions"),
            "description": i18n_catalog.i18nc(
                "@info:tooltip",
                "Define loads and constraints for FEA-driven infill optimization."
            ),
            "icon": "BlockSupportOverlaps",
            "tool_panel": "resources/qml/BoundaryConditionPanel.qml",
            "weight": 5
        }
    }


def register(app):
    from .FEAInfillExtension import FEAInfillExtension
    from .BoundaryConditionTool import BoundaryConditionTool

    extension = FEAInfillExtension()
    tool = BoundaryConditionTool(extension)
    return {
        "extension": extension,
        "tool": tool
    }
