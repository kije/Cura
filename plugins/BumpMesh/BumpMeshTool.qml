// Copyright (c) 2025 BumpMesh Plugin
// Released under the terms of the LGPLv3 or higher.

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import UM 1.7 as UM
import Cura 1.0 as Cura

Item
{
    id: base

    width: childrenRect.width
    height: Math.min(scrollView.contentHeight, 500)

    UM.I18nCatalog { id: catalog; name: "cura" }

    property var currentState: UM.Controller.properties.getValue("State") ?? 1
    property var hasTexture: UM.Controller.properties.getValue("HasTexture") ?? false
    property var hasUnconfirmedChanges: UM.Controller.properties.getValue("HasUnconfirmedChanges") ?? false
    property var subdivisionMode: UM.Controller.properties.getValue("SubdivisionMode") ?? 0
    property var paintMode: UM.Controller.properties.getValue("PaintMode") ?? 0
    property var hasFaceMask: UM.Controller.properties.getValue("HasFaceMask") ?? false

    ScrollView
    {
        id: scrollView
        width: mainColumn.width
        height: parent.height
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Column
        {
            id: mainColumn
            spacing: UM.Theme.getSize("default_margin").height

            // === Section: Displacement Map ===
            UM.Label
            {
                text: catalog.i18nc("@label", "Displacement Map")
                font: UM.Theme.getFont("default_bold")
            }

            RowLayout
            {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").width

                Cura.SecondaryButton
                {
                    Layout.fillWidth: true
                    text: catalog.i18nc("@action:button", "Load Texture...")
                    enabled: currentState !== 2
                    onClicked: UM.Controller.triggerAction("loadTexture")
                }

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Auto")
                    enabled: hasTexture && currentState !== 2
                    onClicked: UM.Controller.triggerAction("autoComputeParameters")
                }
            }

            // Built-in textures — collapsible grid with previews
            UM.Label
            {
                id: builtinToggleLabel
                property bool expanded: false
                text: expanded ?
                    catalog.i18nc("@label", "Built-in Textures \u25B2") :
                    catalog.i18nc("@label", "Built-in Textures \u25BC")
                font: UM.Theme.getFont("default_bold")
                color: UM.Theme.getColor("text_link")

                MouseArea
                {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: builtinToggleLabel.expanded = !builtinToggleLabel.expanded
                }
            }

            // Scrollable grid of texture thumbnails (visible when expanded)
            Flickable
            {
                visible: builtinToggleLabel.expanded
                width: parent.width
                height: Math.min(contentHeight, 160)
                contentHeight: textureGrid.height
                clip: true
                flickableDirection: Flickable.VerticalFlick

                Grid
                {
                    id: textureGrid
                    columns: 5
                    spacing: 4
                    width: parent.width

                    property var textureFiles: [
                        "diamond.png", "brick.png", "waves.png", "dots.png", "noise.png",
                        "crosshatch.png", "hexagonal.png", "voronoi.png", "knurl.png",
                        "checkerboard.png", "grid.png", "stripes.png", "diagonal_stripes.png",
                        "rings.png", "scales.png", "fine_noise.png", "zigzag.png",
                        "starburst.png", "radial.png", "gradient.png"
                    ]

                    Repeater
                    {
                        model: textureGrid.textureFiles.length
                        delegate: Rectangle
                        {
                            width: (textureGrid.width - textureGrid.spacing * 4) / 5
                            height: width
                            color: "transparent"
                            border.width: thumbMouse.containsMouse ? 2 : 1
                            border.color: thumbMouse.containsMouse ?
                                UM.Theme.getColor("primary") :
                                UM.Theme.getColor("lining")
                            radius: 2

                            Image
                            {
                                anchors.fill: parent
                                anchors.margins: 1
                                source: Qt.resolvedUrl("textures/" + textureGrid.textureFiles[index])
                                fillMode: Image.PreserveAspectFit
                                smooth: false
                            }

                            MouseArea
                            {
                                id: thumbMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: UM.Controller.setProperty("BuiltinTexture", index)
                            }

                            ToolTip
                            {
                                visible: thumbMouse.containsMouse
                                text: textureGrid.textureFiles[index].replace(".png", "").replace("_", " ")
                                delay: 500
                            }
                        }
                    }
                }
            }

            UM.Label
            {
                visible: hasTexture
                text:
                {
                    var path = UM.Controller.properties.getValue("TexturePath")
                    if (path)
                    {
                        var parts = path.split("/")
                        return parts[parts.length - 1]
                    }
                    return ""
                }
                color: UM.Theme.getColor("text_inactive")
            }

            UM.Label
            {
                visible: !hasTexture
                text: catalog.i18nc("@label", "Load a texture to begin. Parameters auto-adjust to your model.")
                color: UM.Theme.getColor("text_inactive")
            }

            // Line separator
            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // === Section: Projection Mode ===
            UM.Label
            {
                text: catalog.i18nc("@label", "Projection Mode")
            }

            Cura.ComboBox
            {
                id: projectionCombo
                width: parent.width
                model: [
                    catalog.i18nc("@item:inlistbox", "Triplanar"),
                    catalog.i18nc("@item:inlistbox", "Cubic"),
                    catalog.i18nc("@item:inlistbox", "Cylindrical"),
                    catalog.i18nc("@item:inlistbox", "Spherical"),
                    catalog.i18nc("@item:inlistbox", "Planar XZ"),
                    catalog.i18nc("@item:inlistbox", "Planar XY"),
                    catalog.i18nc("@item:inlistbox", "Planar YZ")
                ]
                currentIndex: UM.Controller.properties.getValue("ProjectionMode") ?? 0
                onCurrentIndexChanged: UM.Controller.setProperty("ProjectionMode", currentIndex)
            }

            UM.Label
            {
                text:
                {
                    var mode = projectionCombo.currentIndex
                    if (mode === 0) return catalog.i18nc("@label", "Blends 3 projections. Best for complex shapes.")
                    if (mode === 1) return catalog.i18nc("@label", "Box projection by dominant normal.")
                    if (mode === 2) return catalog.i18nc("@label", "Wraps around Y axis. Good for cylinders.")
                    if (mode === 3) return catalog.i18nc("@label", "From center outward. Good for spheres.")
                    if (mode === 4) return catalog.i18nc("@label", "Flat projection onto XZ plane.")
                    if (mode === 5) return catalog.i18nc("@label", "Flat projection onto XY plane.")
                    if (mode === 6) return catalog.i18nc("@label", "Flat projection onto YZ plane.")
                    return ""
                }
                color: UM.Theme.getColor("text_inactive")
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // === Section: Parameters ===
            UM.Label
            {
                text: catalog.i18nc("@label", "Amplitude (mm)")
            }

            RowLayout
            {
                width: parent.width
                UM.Slider
                {
                    id: amplitudeSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: -5.0
                    to: 5.0
                    stepSize: 0.1
                    value: UM.Controller.properties.getValue("Amplitude") ?? 1.0
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("Amplitude", amplitudeSlider.value)
                    }
                }
                UM.Label
                {
                    text: amplitudeSlider.value.toFixed(1) + " mm"
                    Layout.preferredWidth: 50
                }
            }

            // Symmetric/Asymmetric toggle
            RowLayout
            {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").width

                UM.Label
                {
                    text: catalog.i18nc("@label", "Mode:")
                    Layout.preferredWidth: 40
                }

                Cura.ComboBox
                {
                    Layout.fillWidth: true
                    model: [
                        catalog.i18nc("@item:inlistbox", "Symmetric (bidirectional)"),
                        catalog.i18nc("@item:inlistbox", "Outward only")
                    ]
                    currentIndex: (UM.Controller.properties.getValue("Symmetric") ?? true) ? 0 : 1
                    onCurrentIndexChanged: UM.Controller.setProperty("Symmetric", currentIndex === 0)
                }
            }

            UM.Label
            {
                text: catalog.i18nc("@label", "Scale U / V")
            }

            RowLayout
            {
                width: parent.width
                UM.Slider
                {
                    id: scaleUSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: 0.0
                    to: 1.0
                    stepSize: 0.001

                    property real logMin: Math.log(0.05)
                    property real logMax: Math.log(50.0)

                    function scaleToPos(s) { return (Math.log(Math.max(s, 0.05)) - logMin) / (logMax - logMin) }
                    function posToScale(p) { return Math.exp(logMin + p * (logMax - logMin)) }

                    value: scaleToPos(UM.Controller.properties.getValue("ScaleU") ?? 1.0)
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("ScaleU", posToScale(scaleUSlider.value))
                    }
                }
                UM.Label
                {
                    text: scaleUSlider.posToScale(scaleUSlider.value).toFixed(1)
                    Layout.preferredWidth: 30
                }
            }

            RowLayout
            {
                width: parent.width
                UM.Slider
                {
                    id: scaleVSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: 0.0
                    to: 1.0
                    stepSize: 0.001

                    property real logMin: Math.log(0.05)
                    property real logMax: Math.log(50.0)

                    function scaleToPos(s) { return (Math.log(Math.max(s, 0.05)) - logMin) / (logMax - logMin) }
                    function posToScale(p) { return Math.exp(logMin + p * (logMax - logMin)) }

                    value: scaleToPos(UM.Controller.properties.getValue("ScaleV") ?? 1.0)
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("ScaleV", posToScale(scaleVSlider.value))
                    }
                }
                UM.Label
                {
                    text: scaleVSlider.posToScale(scaleVSlider.value).toFixed(1)
                    Layout.preferredWidth: 30
                }
            }

            UM.Label
            {
                text: catalog.i18nc("@label", "Offset U / V")
            }

            RowLayout
            {
                width: parent.width
                UM.Slider
                {
                    id: offsetUSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: -10.0
                    to: 10.0
                    stepSize: 0.1
                    value: UM.Controller.properties.getValue("OffsetU") ?? 0.0
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("OffsetU", offsetUSlider.value)
                    }
                }
                UM.Label
                {
                    text: offsetUSlider.value.toFixed(1)
                    Layout.preferredWidth: 30
                }
            }

            RowLayout
            {
                width: parent.width
                UM.Slider
                {
                    id: offsetVSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: -10.0
                    to: 10.0
                    stepSize: 0.1
                    value: UM.Controller.properties.getValue("OffsetV") ?? 0.0
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("OffsetV", offsetVSlider.value)
                    }
                }
                UM.Label
                {
                    text: offsetVSlider.value.toFixed(1)
                    Layout.preferredWidth: 30
                }
            }

            UM.Label
            {
                text: catalog.i18nc("@label", "Rotation")
            }

            RowLayout
            {
                width: parent.width
                UM.Slider
                {
                    id: rotationSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: 0
                    to: 360
                    stepSize: 1
                    value: UM.Controller.properties.getValue("Rotation") ?? 0
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("Rotation", rotationSlider.value)
                    }
                }
                UM.Label
                {
                    text: rotationSlider.value.toFixed(0) + "\u00B0"
                    Layout.preferredWidth: 35
                }
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // === Section: Subdivision ===
            UM.Label
            {
                text: catalog.i18nc("@label", "Subdivision")
                font: UM.Theme.getFont("default_bold")
            }

            Cura.ComboBox
            {
                id: subdivModeCombo
                width: parent.width
                model: [
                    catalog.i18nc("@item:inlistbox", "Uniform (Level)"),
                    catalog.i18nc("@item:inlistbox", "Adaptive (Edge Length)")
                ]
                currentIndex: subdivisionMode
                onCurrentIndexChanged: UM.Controller.setProperty("SubdivisionMode", currentIndex)
            }

            // Uniform mode: level slider
            RowLayout
            {
                width: parent.width
                visible: subdivisionMode === 0
                UM.Slider
                {
                    id: subdivisionSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: 0
                    to: 4
                    stepSize: 1
                    value: UM.Controller.properties.getValue("SubdivisionLevel") ?? 2
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("SubdivisionLevel", subdivisionSlider.value)
                    }
                }
                UM.Label
                {
                    text: catalog.i18nc("@label", "Level %1").arg(subdivisionSlider.value.toFixed(0))
                    Layout.preferredWidth: 50
                }
            }

            // Adaptive mode: edge length slider
            RowLayout
            {
                width: parent.width
                visible: subdivisionMode === 1
                UM.Slider
                {
                    id: edgeLengthSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: 0.1
                    to: 5.0
                    stepSize: 0.1
                    value: UM.Controller.properties.getValue("TargetEdgeLength") ?? 1.0
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("TargetEdgeLength", edgeLengthSlider.value)
                    }
                }
                UM.Label
                {
                    text: edgeLengthSlider.value.toFixed(1) + " mm"
                    Layout.preferredWidth: 50
                }
            }

            UM.Label
            {
                id: vertexEstimateLabel
                property var estVerts: UM.Controller.properties.getValue("EstimatedVertices") ?? 0
                text:
                {
                    if (estVerts > 1000000)
                        return catalog.i18nc("@label", "Est. vertices: ~%1M").arg((estVerts / 1000000).toFixed(1))
                    else if (estVerts > 1000)
                        return catalog.i18nc("@label", "Est. vertices: ~%1K").arg((estVerts / 1000).toFixed(0))
                    else
                        return catalog.i18nc("@label", "Est. vertices: %1").arg(estVerts)
                }
                color: estVerts > 500000 ? UM.Theme.getColor("warning") : UM.Theme.getColor("text_inactive")
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // === Section: Masking & Smoothing ===
            UM.Label
            {
                text: catalog.i18nc("@label", "Angle Mask")
            }

            RowLayout
            {
                width: parent.width
                UM.Slider
                {
                    id: maskAngleSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: 0
                    to: 90
                    stepSize: 1
                    value: UM.Controller.properties.getValue("MaskAngle") ?? 0
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("MaskAngle", maskAngleSlider.value)
                    }
                }
                UM.Label
                {
                    text: maskAngleSlider.value == 0 ?
                        catalog.i18nc("@label", "Off") :
                        maskAngleSlider.value.toFixed(0) + "\u00B0"
                    Layout.preferredWidth: 30
                }
            }

            UM.Label
            {
                text: catalog.i18nc("@label", "Smoothing")
            }

            RowLayout
            {
                width: parent.width
                UM.Slider
                {
                    id: smoothingSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: 0
                    to: 20
                    stepSize: 1
                    value: UM.Controller.properties.getValue("Smoothing") ?? 0
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("Smoothing", smoothingSlider.value)
                    }
                }
                UM.Label
                {
                    text: smoothingSlider.value == 0 ?
                        catalog.i18nc("@label", "Off") :
                        smoothingSlider.value.toFixed(0)
                    Layout.preferredWidth: 25
                }
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // === Section: Face Painting ===
            UM.Label
            {
                text: catalog.i18nc("@label", "Face Mask")
                font: UM.Theme.getFont("default_bold")
            }

            Cura.ComboBox
            {
                id: paintModeCombo
                width: parent.width
                model: [
                    catalog.i18nc("@item:inlistbox", "Off (camera)"),
                    catalog.i18nc("@item:inlistbox", "Brush — Exclude"),
                    catalog.i18nc("@item:inlistbox", "Brush — Include (Eraser)"),
                    catalog.i18nc("@item:inlistbox", "Bucket — Exclude"),
                    catalog.i18nc("@item:inlistbox", "Bucket — Include")
                ]
                currentIndex: paintMode
                onCurrentIndexChanged: UM.Controller.setProperty("PaintMode", currentIndex)
            }

            UM.Label
            {
                text:
                {
                    if (paintMode === 0) return catalog.i18nc("@label", "Select a mode, then click faces in the viewport.")
                    if (paintMode === 1) return catalog.i18nc("@label", "Click & drag faces to exclude from displacement. Excluded faces stay flat.")
                    if (paintMode === 2) return catalog.i18nc("@label", "Click & drag to restore displacement on excluded faces.")
                    if (paintMode === 3) return catalog.i18nc("@label", "Click a face to flood-fill exclude. Stops at sharp edges.")
                    if (paintMode === 4) return catalog.i18nc("@label", "Click a face to flood-fill include. Stops at sharp edges.")
                    return ""
                }
                color: paintMode > 0 ? UM.Theme.getColor("text_default") : UM.Theme.getColor("text_inactive")
                wrapMode: Text.WordWrap
                width: parent.width
            }

            UM.Label
            {
                visible: paintMode > 0 && hasTexture
                text: catalog.i18nc("@label", "Excluded faces stay flat — that's how you see the mask.")
                color: UM.Theme.getColor("text_inactive")
                wrapMode: Text.WordWrap
                width: parent.width
            }

            // Bucket angle threshold (only relevant for bucket modes)
            UM.Label
            {
                visible: paintMode === 3 || paintMode === 4
                text: catalog.i18nc("@label", "Bucket Angle Threshold")
            }

            RowLayout
            {
                width: parent.width
                visible: paintMode === 3 || paintMode === 4
                UM.Slider
                {
                    id: bucketAngleSlider
                    Layout.fillWidth: true
                    indicatorVisible: false
                    from: 5
                    to: 90
                    stepSize: 1
                    value: UM.Controller.properties.getValue("BucketAngle") ?? 30
                    onPressedChanged: function(pressed)
                    {
                        if (!pressed) UM.Controller.setProperty("BucketAngle", bucketAngleSlider.value)
                    }
                }
                UM.Label
                {
                    text: bucketAngleSlider.value.toFixed(0) + "\u00B0"
                    Layout.preferredWidth: 35
                }
            }

            RowLayout
            {
                visible: hasFaceMask
                spacing: UM.Theme.getSize("default_margin").width

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Clear Mask")
                    onClicked: UM.Controller.triggerAction("clearFaceMask")
                }
                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Invert Mask")
                    onClicked: UM.Controller.triggerAction("invertFaceMask")
                }
            }

            UM.Label
            {
                visible: hasFaceMask
                text: catalog.i18nc("@label", "Face mask active. Excluded faces won't be displaced.")
                color: UM.Theme.getColor("text_inactive")
                wrapMode: Text.WordWrap
                width: parent.width
            }

            Rectangle
            {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            // === Processing indicator ===
            RowLayout
            {
                visible: currentState === 2
                spacing: UM.Theme.getSize("default_margin").width

                UM.ColorImage
                {
                    id: loadingIndicator
                    Layout.preferredWidth: UM.Theme.getSize("section_icon").width
                    Layout.preferredHeight: UM.Theme.getSize("section_icon").height
                    source: UM.Theme.getIcon("ArrowDoubleCircleRight")
                    color: UM.Theme.getColor("text_default")

                    RotationAnimator
                    {
                        target: loadingIndicator
                        from: 0
                        to: 360
                        duration: 2000
                        loops: Animation.Infinite
                        running: currentState === 2
                        alwaysRunToEnd: true
                    }
                }

                UM.Label
                {
                    text: catalog.i18nc("@label", "Updating preview...")
                }
            }

            // === Error message ===
            UM.Label
            {
                visible: currentState === 3
                width: parent.width
                text: UM.Controller.properties.getValue("ErrorMessage") ?? ""
                color: UM.Theme.getColor("error")
                wrapMode: Text.WordWrap
            }

            // === Section: Actions ===
            RowLayout
            {
                spacing: UM.Theme.getSize("default_margin").width

                Cura.PrimaryButton
                {
                    text: catalog.i18nc("@action:button", "Confirm")
                    enabled: hasUnconfirmedChanges == true && currentState !== 2
                    onClicked: UM.Controller.triggerAction("confirmDisplacement")
                }

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Revert")
                    enabled: hasUnconfirmedChanges == true && currentState !== 2
                    onClicked: UM.Controller.triggerAction("revertDisplacement")
                }
            }

            UM.Label
            {
                visible: hasUnconfirmedChanges == true
                text: catalog.i18nc("@label", "Preview active. Confirm to keep, or close tool to revert.")
                color: UM.Theme.getColor("text_inactive")
            }
        }
    }
}
