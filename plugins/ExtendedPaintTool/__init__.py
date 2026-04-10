# Copyright (c) 2025 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

from . import ExtendedPaintTool
from . import ExtendedPaintView
from .DrawingTool import DrawingMode
from .PaintLayer import BlendMode
from .ImageProjection import ProjectionMode

from PyQt6.QtQml import qmlRegisterUncreatableType

from UM.i18n import i18nCatalog
i18n_catalog = i18nCatalog("cura")

def getMetaData():
    return {
        "tool": {
            "name": i18n_catalog.i18nc("@action:button", "Extended Paint"),
            "description": i18n_catalog.i18nc("@info:tooltip", "Professional 3D Paint Tool"),
            "icon": "Brush",
            "tool_panel": "ExtendedPaintTool.qml",
            "weight": 1
        },
        "view": {
            "name": i18n_catalog.i18nc("@item:inmenu", "Extended Paint view"),
            "weight": 1,
            "visible": False
        }
    }

def register(app):
    qmlRegisterUncreatableType(ExtendedPaintTool.ExtendedPaintTool.Brush, "Cura", 1, 0, "This is an enumeration class", "ExtPaintToolBrush")
    qmlRegisterUncreatableType(ExtendedPaintTool.ExtendedPaintTool.Paint, "Cura", 1, 0, "This is an enumeration class", "ExtPaintToolState")
    qmlRegisterUncreatableType(DrawingMode, "Cura", 1, 0, "This is an enumeration class", "ExtPaintToolDrawingMode")
    qmlRegisterUncreatableType(BlendMode, "Cura", 1, 0, "This is an enumeration class", "ExtPaintToolBlendMode")
    qmlRegisterUncreatableType(ProjectionMode, "Cura", 1, 0, "This is an enumeration class", "ExtPaintToolProjectionMode")
    view = ExtendedPaintView.ExtendedPaintView()
    return {
        "tool": ExtendedPaintTool.ExtendedPaintTool(view),
        "view": view
    }
