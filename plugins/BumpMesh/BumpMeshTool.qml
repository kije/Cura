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
    height: childrenRect.height
    UM.I18nCatalog { id: catalog; name: "cura" }

    // States: 0=NO_SELECTION, 1=READY, 2=PROCESSING, 3=ERROR
    property int currentState: UM.Controller.properties.getValue("State")
    property bool hasTexture: UM.Controller.properties.getValue("HasTexture")

    Column
    {
        id: mainColumn
        spacing: UM.Theme.getSize("default_margin").height
        visible: (currentState === 1 || currentState === 3)

        // === Section: Displacement Map ===
        UM.Label
        {
            text: catalog.i18nc("@label", "Displacement Map")
            font: UM.Theme.getFont("default_bold")
        }

        Cura.SecondaryButton
        {
            id: loadTextureButton
            width: parent.width
            text: catalog.i18nc("@action:button", "Load Texture...")
            onClicked: UM.Controller.triggerAction("loadTexture")
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
            text: catalog.i18nc("@label", "Load a displacement map texture to begin.")
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
                catalog.i18nc("@item:inlistbox", "Planar")
            ]
            currentIndex: UM.Controller.properties.getValue("ProjectionMode")
            onCurrentIndexChanged: UM.Controller.setProperty("ProjectionMode", currentIndex)
        }

        UM.Label
        {
            text:
            {
                var mode = projectionCombo.currentIndex
                if (mode === 0) return catalog.i18nc("@label", "Blends 3 planar projections. Best for complex shapes.")
                if (mode === 1) return catalog.i18nc("@label", "Projects from 6 box faces by dominant normal.")
                if (mode === 2) return catalog.i18nc("@label", "Wraps around the Y axis. Good for cylindrical parts.")
                if (mode === 3) return catalog.i18nc("@label", "Projects from center outward. Good for round objects.")
                if (mode === 4) return catalog.i18nc("@label", "Flat projection onto the XZ plane.")
                return ""
            }
            color: UM.Theme.getColor("text_inactive")
        }

        // Line separator
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
                value: UM.Controller.properties.getValue("Amplitude")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("Amplitude", amplitudeSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: amplitudeSlider.value.toFixed(1)
                Layout.preferredWidth: 35
            }
        }

        UM.Label
        {
            text: catalog.i18nc("@label", "Scale U")
        }

        RowLayout
        {
            width: parent.width
            UM.Slider
            {
                id: scaleUSlider
                Layout.fillWidth: true
                indicatorVisible: false
                from: 0.1
                to: 20.0
                stepSize: 0.1
                value: UM.Controller.properties.getValue("ScaleU")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("ScaleU", scaleUSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: scaleUSlider.value.toFixed(1)
                Layout.preferredWidth: 35
            }
        }

        UM.Label
        {
            text: catalog.i18nc("@label", "Scale V")
        }

        RowLayout
        {
            width: parent.width
            UM.Slider
            {
                id: scaleVSlider
                Layout.fillWidth: true
                indicatorVisible: false
                from: 0.1
                to: 20.0
                stepSize: 0.1
                value: UM.Controller.properties.getValue("ScaleV")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("ScaleV", scaleVSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: scaleVSlider.value.toFixed(1)
                Layout.preferredWidth: 35
            }
        }

        UM.Label
        {
            text: catalog.i18nc("@label", "Offset U")
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
                value: UM.Controller.properties.getValue("OffsetU")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("OffsetU", offsetUSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: offsetUSlider.value.toFixed(1)
                Layout.preferredWidth: 35
            }
        }

        UM.Label
        {
            text: catalog.i18nc("@label", "Offset V")
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
                value: UM.Controller.properties.getValue("OffsetV")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("OffsetV", offsetVSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: offsetVSlider.value.toFixed(1)
                Layout.preferredWidth: 35
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
                value: UM.Controller.properties.getValue("Rotation")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("Rotation", rotationSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: rotationSlider.value.toFixed(0) + "\u00B0"
                Layout.preferredWidth: 35
            }
        }

        // Line separator
        Rectangle
        {
            width: parent.width
            height: UM.Theme.getSize("default_lining").height
            color: UM.Theme.getColor("lining")
        }

        // === Section: Subdivision ===
        UM.Label
        {
            text: catalog.i18nc("@label", "Subdivision Level")
        }

        RowLayout
        {
            width: parent.width
            UM.Slider
            {
                id: subdivisionSlider
                Layout.fillWidth: true
                indicatorVisible: false
                from: 0
                to: 4
                stepSize: 1
                value: UM.Controller.properties.getValue("SubdivisionLevel")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("SubdivisionLevel", subdivisionSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: subdivisionSlider.value.toFixed(0)
                Layout.preferredWidth: 20
            }
        }

        UM.Label
        {
            visible: subdivisionSlider.value == 0
            text: catalog.i18nc("@label", "0 = original mesh density (no added detail)")
            color: UM.Theme.getColor("text_inactive")
        }

        UM.Label
        {
            id: vertexEstimateLabel
            property int estVerts: UM.Controller.properties.getValue("EstimatedVertices")
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

        UM.Label
        {
            visible: vertexEstimateLabel.estVerts > 500000
            text: catalog.i18nc("@label", "High vertex counts may be slow or run out of memory.")
            color: UM.Theme.getColor("warning")
        }

        // Line separator
        Rectangle
        {
            width: parent.width
            height: UM.Theme.getSize("default_lining").height
            color: UM.Theme.getColor("lining")
        }

        // === Section: Masking & Smoothing ===
        UM.Label
        {
            text: catalog.i18nc("@label", "Angle Mask (degrees)")
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
                value: UM.Controller.properties.getValue("MaskAngle")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("MaskAngle", maskAngleSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: maskAngleSlider.value.toFixed(0) + "\u00B0"
                Layout.preferredWidth: 35
            }
        }

        UM.Label
        {
            text: maskAngleSlider.value == 0 ?
                catalog.i18nc("@label", "(no masking \u2014 displace all surfaces)") :
                catalog.i18nc("@label", "(only surfaces within %1\u00B0 of up)").arg(maskAngleSlider.value.toFixed(0))
            color: UM.Theme.getColor("text_inactive")
        }

        UM.Label
        {
            text: catalog.i18nc("@label", "Texture Smoothing (blur passes)")
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
                value: UM.Controller.properties.getValue("Smoothing")
                onPressedChanged: function(pressed)
                {
                    if (!pressed)
                    {
                        UM.Controller.setProperty("Smoothing", smoothingSlider.value)
                    }
                }
            }
            UM.Label
            {
                text: smoothingSlider.value.toFixed(0)
                Layout.preferredWidth: 20
            }
        }

        // Line separator
        Rectangle
        {
            width: parent.width
            height: UM.Theme.getSize("default_lining").height
            color: UM.Theme.getColor("lining")
        }

        // === Error message ===
        UM.Label
        {
            visible: currentState === 3
            width: parent.width
            text: UM.Controller.properties.getValue("ErrorMessage") || ""
            color: UM.Theme.getColor("error")
            wrapMode: Text.WordWrap
        }

        // === Section: Actions ===
        RowLayout
        {
            spacing: UM.Theme.getSize("default_margin").width

            Cura.PrimaryButton
            {
                text: catalog.i18nc("@action:button", "Apply")
                enabled: hasTexture && currentState !== 2
                onClicked: UM.Controller.triggerAction("applyDisplacement")
            }

            Cura.SecondaryButton
            {
                text: catalog.i18nc("@action:button", "Reset")
                onClicked: UM.Controller.triggerAction("resetMesh")
            }
        }
    }

    // === Processing overlay ===
    Rectangle
    {
        id: processingOverlay
        anchors.fill: parent
        color: UM.Theme.getColor("main_background")
        visible: currentState === 2

        ColumnLayout
        {
            anchors.fill: parent

            UM.Label
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.verticalStretchFactor: 2

                text: catalog.i18nc("@label", "Applying displacement...")
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

    // === No selection message ===
    Rectangle
    {
        id: noSelectionOverlay
        anchors.fill: parent
        color: UM.Theme.getColor("main_background")
        visible: currentState === 0

        UM.Label
        {
            anchors.fill: parent
            text: catalog.i18nc("@label", "Select a single model to apply displacement")
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
