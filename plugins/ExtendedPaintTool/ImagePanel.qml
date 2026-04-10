// Copyright (c) 2025 UltiMaker
// Cura is released under the terms of the LGPLv3 or higher.

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

import UM 1.7 as UM
import Cura 1.0 as Cura

// Panel for projecting an image onto the model as a paint mask.
ColumnLayout
{
    id: imagePanel
    spacing: UM.Theme.getSize("default_margin").height / 2
    Layout.fillWidth: true

    property bool imageLoaded: UM.Controller.properties.getValue("ImageLoaded") === true
    property int projectionMode: UM.Controller.properties.getValue("ImageProjectionMode") ?? 2

    FileDialog
    {
        id: imageFileDialog
        title: catalog.i18nc("@title:window", "Select an image to project")
        nameFilters: [
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.svg)",
            "All files (*)"
        ]
        onAccepted:
        {
            UM.Controller.setProperty("ImagePath", imageFileDialog.selectedFile.toString())
        }
    }

    UM.Label
    {
        text: catalog.i18nc("@label", "Image Projection")
        font: UM.Theme.getFont("default_bold")
    }

    RowLayout
    {
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").width / 2

        Cura.SecondaryButton
        {
            id: loadImageButton
            text: imagePanel.imageLoaded
                  ? catalog.i18nc("@button", "Replace Image")
                  : catalog.i18nc("@button", "Load Image...")
            onClicked: imageFileDialog.open()
        }

        Cura.SecondaryButton
        {
            id: clearImageButton
            text: catalog.i18nc("@button", "Remove")
            enabled: imagePanel.imageLoaded
            onClicked: UM.Controller.triggerAction("clearImage")
        }
    }

    // Projection mode selector
    UM.Label
    {
        text: catalog.i18nc("@label", "Projection Mode")
        visible: imagePanel.imageLoaded
    }

    Cura.ComboBox
    {
        id: projectionModeCombo
        Layout.fillWidth: true
        visible: imagePanel.imageLoaded
        textRole: "text"
        valueRole: "value"
        model: ListModel
        {
            ListElement { text: "Planar X"; value: 0 }
            ListElement { text: "Planar Y"; value: 1 }
            ListElement { text: "Planar Z"; value: 2 }
            ListElement { text: "Box (tri-planar)"; value: 3 }
            ListElement { text: "Spherical"; value: 4 }
            ListElement { text: "Cylindrical"; value: 5 }
        }
        currentIndex: imagePanel.projectionMode
        onActivated: UM.Controller.setProperty("ImageProjectionMode", currentValue)
    }

    // Threshold control
    UM.Label
    {
        text: catalog.i18nc("@label", "Threshold")
        visible: imagePanel.imageLoaded
    }

    UM.Slider
    {
        id: thresholdSlider
        Layout.fillWidth: true
        indicatorVisible: false
        visible: imagePanel.imageLoaded
        from: 0
        to: 255
        value: UM.Controller.properties.getValue("ImageThreshold") ?? 128
        onPressedChanged: function(pressed)
        {
            if(! pressed)
            {
                UM.Controller.setProperty("ImageThreshold", thresholdSlider.value)
            }
        }
    }

    CheckBox
    {
        id: invertCheckbox
        text: catalog.i18nc("@label", "Invert (light = on)")
        visible: imagePanel.imageLoaded
        checked: UM.Controller.properties.getValue("ImageInvert") === true
        onClicked: UM.Controller.setProperty("ImageInvert", checked)
    }

    // --- Transform: scale
    UM.Label
    {
        text: catalog.i18nc("@label", "Scale")
        visible: imagePanel.imageLoaded
    }

    UM.Slider
    {
        id: scaleSlider
        Layout.fillWidth: true
        indicatorVisible: false
        visible: imagePanel.imageLoaded
        from: 0.05
        to: 4.0
        stepSize: 0.01
        value: UM.Controller.properties.getValue("ImageScale") ?? 1.0
        onPressedChanged: function(pressed)
        {
            if(! pressed)
            {
                UM.Controller.setProperty("ImageScale", scaleSlider.value)
            }
        }
    }

    // --- Transform: rotation
    UM.Label
    {
        text: catalog.i18nc("@label", "Rotation (deg)")
        visible: imagePanel.imageLoaded
    }

    UM.Slider
    {
        id: rotationSlider
        Layout.fillWidth: true
        indicatorVisible: false
        visible: imagePanel.imageLoaded
        from: -180
        to: 180
        stepSize: 1
        value: UM.Controller.properties.getValue("ImageRotation") ?? 0
        onPressedChanged: function(pressed)
        {
            if(! pressed)
            {
                UM.Controller.setProperty("ImageRotation", rotationSlider.value)
            }
        }
    }

    // --- Transform: offset U
    UM.Label
    {
        text: catalog.i18nc("@label", "Offset U")
        visible: imagePanel.imageLoaded
    }

    UM.Slider
    {
        id: offsetUSlider
        Layout.fillWidth: true
        indicatorVisible: false
        visible: imagePanel.imageLoaded
        from: -2.0
        to: 2.0
        stepSize: 0.01
        value: UM.Controller.properties.getValue("ImageOffsetU") ?? 0
        onPressedChanged: function(pressed)
        {
            if(! pressed)
            {
                UM.Controller.setProperty("ImageOffsetU", offsetUSlider.value)
            }
        }
    }

    // --- Transform: offset V
    UM.Label
    {
        text: catalog.i18nc("@label", "Offset V")
        visible: imagePanel.imageLoaded
    }

    UM.Slider
    {
        id: offsetVSlider
        Layout.fillWidth: true
        indicatorVisible: false
        visible: imagePanel.imageLoaded
        from: -2.0
        to: 2.0
        stepSize: 0.01
        value: UM.Controller.properties.getValue("ImageOffsetV") ?? 0
        onPressedChanged: function(pressed)
        {
            if(! pressed)
            {
                UM.Controller.setProperty("ImageOffsetV", offsetVSlider.value)
            }
        }
    }

    RowLayout
    {
        Layout.fillWidth: true
        visible: imagePanel.imageLoaded
        spacing: UM.Theme.getSize("default_margin").width / 2

        Cura.SecondaryButton
        {
            text: catalog.i18nc("@button", "Reset Transform")
            onClicked: UM.Controller.triggerAction("resetImageTransform")
        }

        Cura.PrimaryButton
        {
            text: catalog.i18nc("@button", "Apply to Model")
            onClicked: UM.Controller.triggerAction("applyImage")
        }
    }
}
