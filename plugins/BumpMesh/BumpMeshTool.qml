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
    height: Math.min(scrollView.contentHeight, 450)

    UM.I18nCatalog { id: catalog; name: "cura" }

    // States: 1=READY, 2=PROCESSING, 3=ERROR
    property var currentState: UM.Controller.properties.getValue("State") ?? 1
    property var hasTexture: UM.Controller.properties.getValue("HasTexture") ?? false
    property var hasUnconfirmedChanges: UM.Controller.properties.getValue("HasUnconfirmedChanges") ?? false

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

            Cura.SecondaryButton
            {
                id: loadTextureButton
                width: parent.width
                text: catalog.i18nc("@action:button", "Load Texture...")
                enabled: currentState !== 2
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
                currentIndex: UM.Controller.properties.getValue("ProjectionMode") ?? 0
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
                    value: UM.Controller.properties.getValue("Amplitude") ?? 1.0
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
                    value: UM.Controller.properties.getValue("ScaleU") ?? 1.0
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
                    value: UM.Controller.properties.getValue("ScaleV") ?? 1.0
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
                    value: UM.Controller.properties.getValue("OffsetU") ?? 0.0
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
                    value: UM.Controller.properties.getValue("OffsetV") ?? 0.0
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
                    value: UM.Controller.properties.getValue("Rotation") ?? 0
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
                    value: UM.Controller.properties.getValue("SubdivisionLevel") ?? 1
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
                    value: UM.Controller.properties.getValue("MaskAngle") ?? 0
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
                    value: UM.Controller.properties.getValue("Smoothing") ?? 0
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

            // === Processing indicator (inline, not overlay) ===
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
