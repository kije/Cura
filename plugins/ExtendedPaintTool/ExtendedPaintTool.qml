// Copyright (c) 2025 UltiMaker
// Cura is released under the terms of the LGPLv3 or higher.

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import UM 1.7 as UM
import Cura 1.0 as Cura

Item
{
    id: base

    width: childrenRect.width
    // Cap height so the panel doesn't overflow the screen; content scrolls inside.
    height: Math.min(scrollContent.implicitHeight, base.maxPanelHeight)
    UM.I18nCatalog { id: catalog; name: "cura"}

    // Use 60% of the window height as the max panel height
    property real maxPanelHeight: (typeof CuraApplication !== "undefined" && CuraApplication.mainWindow)
                                  ? CuraApplication.mainWindow.height * 0.6
                                  : 600

    property int currentDrawingMode: UM.Controller.properties.getValue("DrawingMode") ?? 0
    property bool isFreehand: currentDrawingMode === Cura.ExtPaintToolDrawingMode.FREEHAND
    property bool isLine: currentDrawingMode === Cura.ExtPaintToolDrawingMode.LINE
    property bool isFill: currentDrawingMode === Cura.ExtPaintToolDrawingMode.FILL
    property bool showBrushShape: isFreehand
    property bool showBrushSize: isFreehand || isLine

    Action
    {
        id: undoAction
        shortcut: "Ctrl+L"
        enabled: UM.Controller.properties.getValue("CanUndo")
        onTriggered: UM.Controller.triggerAction("undoStackAction")
    }

    Action
    {
        id: redoAction
        shortcut: "Ctrl+Shift+L"
        enabled: UM.Controller.properties.getValue("CanRedo")
        onTriggered: UM.Controller.triggerAction("redoStackAction")
    }

    Flickable
    {
        id: scrollFlickable
        width: scrollContent.implicitWidth
        height: parent.height
        contentWidth: scrollContent.implicitWidth
        contentHeight: scrollContent.implicitHeight
        clip: true
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds

        ScrollBar.vertical: ScrollBar
        {
            policy: scrollFlickable.contentHeight > scrollFlickable.height
                    ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
        }

        Column
        {
            id: scrollContent
            spacing: UM.Theme.getSize("default_margin").height

            // --- Extruder Selection ---
            RowLayout
            {
                id: rowExtruder

                UM.Label
                {
                    text: catalog.i18nc("@label", "Material")
                }

                Repeater
                {
                    id: repeaterExtruders
                    model: CuraApplication.getExtrudersModel()
                    delegate: Cura.ExtruderButton
                    {
                        extruder: model
                        checked: UM.Controller.properties.getValue("BrushExtruder") === model.index
                        onClicked: UM.Controller.setProperty("BrushExtruder", model.index)
                    }
                }
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // --- Drawing Tool ---
            UM.Label
            {
                text: catalog.i18nc("@label", "Drawing Tool")
            }

            GridLayout
            {
                id: gridDrawingTool
                columns: 3
                columnSpacing: UM.Theme.getSize("default_margin").width / 2
                rowSpacing: UM.Theme.getSize("default_margin").height / 2

                DrawingToolButton
                {
                    drawingMode: Cura.ExtPaintToolDrawingMode.FREEHAND
                    text: catalog.i18nc("@action:button", "Brush")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("Brush")
                        color: UM.Theme.getColor("icon")
                    }
                }

                DrawingToolButton
                {
                    drawingMode: Cura.ExtPaintToolDrawingMode.LINE
                    text: catalog.i18nc("@action:button", "Line")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("Pen")
                        color: UM.Theme.getColor("icon")
                    }
                }

                DrawingToolButton
                {
                    drawingMode: Cura.ExtPaintToolDrawingMode.RECTANGLE
                    text: catalog.i18nc("@action:button", "Rect")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("MeshTypeNormal")
                        color: UM.Theme.getColor("icon")
                    }
                }

                DrawingToolButton
                {
                    drawingMode: Cura.ExtPaintToolDrawingMode.CIRCLE
                    text: catalog.i18nc("@action:button", "Circle")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("Circle")
                        color: UM.Theme.getColor("icon")
                    }
                }

                DrawingToolButton
                {
                    drawingMode: Cura.ExtPaintToolDrawingMode.POLYGON
                    text: catalog.i18nc("@action:button", "Polygon")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("Star")
                        color: UM.Theme.getColor("icon")
                    }
                }

                DrawingToolButton
                {
                    drawingMode: Cura.ExtPaintToolDrawingMode.FILL
                    text: catalog.i18nc("@action:button", "Fill")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("Hammer")
                        color: UM.Theme.getColor("icon")
                    }
                }
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // --- Brush Shape (Freehand only) ---
            RowLayout
            {
                id: rowBrushShape
                visible: base.showBrushShape

                UM.Label
                {
                    text: catalog.i18nc("@label", "Brush Shape")
                }

                BrushShapeButton
                {
                    id: buttonBrushCircle
                    shape: Cura.ExtPaintToolBrush.CIRCLE
                    text: catalog.i18nc("@action:button", "Circle")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("Circle")
                        color: UM.Theme.getColor("icon")
                    }
                }

                BrushShapeButton
                {
                    id: buttonBrushSquare
                    shape: Cura.ExtPaintToolBrush.SQUARE
                    text: catalog.i18nc("@action:button", "Square")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("MeshTypeNormal")
                        color: UM.Theme.getColor("icon")
                    }
                }
            }

            // --- Brush Size (Freehand + Line) ---
            UM.Label
            {
                text: catalog.i18nc("@label", "Brush Size")
                visible: base.showBrushSize
            }

            UM.Slider
            {
                id: shapeSizeSlider
                width: parent.width
                indicatorVisible: false
                visible: base.showBrushSize

                from: 1
                to: 100
                value: UM.Controller.properties.getValue("BrushSize") ?? 10

                onPressedChanged: function(pressed)
                {
                    if(! pressed)
                    {
                        UM.Controller.setProperty("BrushSize", shapeSizeSlider.value);
                    }
                }
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // --- Symmetry ---
            UM.Label
            {
                text: catalog.i18nc("@label", "Symmetry")
            }

            RowLayout
            {
                id: rowSymmetry

                UM.ToolbarButton
                {
                    text: "X"
                    checked: UM.Controller.properties.getValue("SymmetryX") === true
                    onClicked: UM.Controller.setProperty("SymmetryX", !checked)
                }

                UM.ToolbarButton
                {
                    text: "Y"
                    checked: UM.Controller.properties.getValue("SymmetryY") === true
                    onClicked: UM.Controller.setProperty("SymmetryY", !checked)
                }

                UM.ToolbarButton
                {
                    text: "Z"
                    checked: UM.Controller.properties.getValue("SymmetryZ") === true
                    onClicked: UM.Controller.setProperty("SymmetryZ", !checked)
                }
            }

            // --- Stabilizer (Freehand only) ---
            RowLayout
            {
                id: rowStabilize
                visible: base.isFreehand

                CheckBox
                {
                    id: stabilizeCheckbox
                    text: catalog.i18nc("@label", "Stabilize Stroke")
                    checked: UM.Controller.properties.getValue("Stabilize") === true
                    onClicked: UM.Controller.setProperty("Stabilize", checked)
                }
            }

            UM.Slider
            {
                id: stabilizeStrengthSlider
                width: parent.width
                indicatorVisible: false
                visible: base.isFreehand && (UM.Controller.properties.getValue("Stabilize") === true)

                from: 2
                to: 20
                value: UM.Controller.properties.getValue("StabilizeStrength") ?? 5

                onPressedChanged: function(pressed)
                {
                    if(! pressed)
                    {
                        UM.Controller.setProperty("StabilizeStrength", stabilizeStrengthSlider.value);
                    }
                }
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // --- Undo / Redo / Clear ---
            RowLayout
            {
                UM.ToolbarButton
                {
                    id: undoButton
                    enabled: undoAction.enabled
                    text: catalog.i18nc("@action:button", "Undo Stroke")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("ArrowReset")
                        color: UM.Theme.getColor("icon")
                    }
                    onClicked: undoAction.trigger()
                }

                UM.ToolbarButton
                {
                    id: redoButton
                    enabled: redoAction.enabled
                    text: catalog.i18nc("@action:button", "Redo Stroke")
                    toolItem: UM.ColorImage
                    {
                        source: UM.Theme.getIcon("ArrowReset")
                        color: UM.Theme.getColor("icon")
                        transform: [
                            Scale { xScale: -1; origin.x: width/2 }
                        ]
                    }
                    onClicked: redoAction.trigger()
                }

                Cura.SecondaryButton
                {
                    id: clearButton
                    text: catalog.i18nc("@button", "Clear all")
                    onClicked: UM.Controller.triggerAction("clear")
                }
            }
        }
    }

    Rectangle
    {
        id: waitPrepareItem
        anchors.fill: parent
        color: UM.Theme.getColor("main_background")
        visible: UM.Controller.properties.getValue("State") === Cura.ExtPaintToolState.PREPARING_MODEL

        ColumnLayout
        {
            anchors.fill: parent

            UM.Label
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.verticalStretchFactor: 2
                text: catalog.i18nc("@label", "Preparing model for painting...")
                verticalAlignment: Text.AlignBottom
                horizontalAlignment: Text.AlignHCenter
            }

            Item
            {
                Layout.preferredWidth: loadingIndicator.width
                Layout.alignment: Qt.AlignHCenter
                Layout.fillHeight: true
                Layout.verticalStretchFactor: 1

                UM.ColorImage
                {
                    id: loadingIndicator
                    anchors.top: parent.top
                    anchors.left: parent.left
                    width: UM.Theme.getSize("card_icon").width
                    height: UM.Theme.getSize("card_icon").height
                    source: UM.Theme.getIcon("ArrowDoubleCircleRight")
                    color: UM.Theme.getColor("text_default")

                    RotationAnimator
                    {
                        target: loadingIndicator
                        from: 0
                        to: 360
                        duration: 2000
                        loops: Animation.Infinite
                        running: true
                        alwaysRunToEnd: true
                    }
                }
            }
        }
    }

    Rectangle
    {
        id: selectSingleMessageItem
        anchors.fill: parent
        color: UM.Theme.getColor("main_background")
        visible: UM.Controller.properties.getValue("State") === Cura.ExtPaintToolState.MULTIPLE_SELECTION

        UM.Label
        {
            anchors.fill: parent
            text: catalog.i18nc("@label", "Select a single ungrouped model to start painting")
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
        }
    }

    Rectangle
    {
        id: warningLegacyOpenGLItem
        anchors.fill: parent
        color: UM.Theme.getColor("main_background")
        visible: UM.Controller.properties.getValue("State") === Cura.ExtPaintToolState.NOT_SUPPORTED

        UM.Label
        {
            anchors.fill: parent
            text: catalog.i18nc("@label", "Painting is not available on this device. Your graphics card or drivers do not fully support it. Updating your graphics drivers may enable this feature.")
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
