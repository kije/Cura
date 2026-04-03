// Copyright (c) 2026 Community Contributors
// Released under the terms of the LGPLv3 or higher.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15

import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog
{
    id: gradientDialog
    title: catalog.i18nc("@title:window", "Gradient Editor")
    width: 500 * screenScaleFactor
    height: 550 * screenScaleFactor
    minimumWidth: 400 * screenScaleFactor
    minimumHeight: 400 * screenScaleFactor

    property int targetIndex: -1
    property bool gradientEnabled: enableCheck.checked

    signal applied()

    function reset()
    {
        enableCheck.checked = true
        keyframeModel.clear()
        keyframeModel.append({ "height_mm": 0.0, "ratio_a": 1.0 })
        keyframeModel.append({ "height_mm": 10.0, "ratio_a": 0.5 })
        keyframeModel.append({ "height_mm": 30.0, "ratio_a": 0.0 })
    }

    function loadFromData(data)
    {
        enableCheck.checked = data.enabled !== undefined ? data.enabled : true
        keyframeModel.clear()
        if (data.keyframes)
        {
            for (var i = 0; i < data.keyframes.length; i++)
            {
                keyframeModel.append({
                    "height_mm": data.keyframes[i].height_mm,
                    "ratio_a": data.keyframes[i].ratio_a
                })
            }
        }
    }

    function getGradientJson()
    {
        var keyframes = []
        for (var i = 0; i < keyframeModel.count; i++)
        {
            var item = keyframeModel.get(i)
            keyframes.push({
                "height_mm": item.height_mm,
                "ratio_a": item.ratio_a
            })
        }
        return JSON.stringify({
            "enabled": enableCheck.checked,
            "keyframes": keyframes
        })
    }

    Item
    {
        UM.I18nCatalog { id: catalog; name: "cura" }
        anchors.fill: parent

        // Internal keyframe model (must be inside Item, not direct child of UM.Dialog)
        ListModel
        {
            id: keyframeModel
        }

        ColumnLayout
        {
            anchors.fill: parent
            spacing: UM.Theme.getSize("default_margin").height

            // Enable toggle
            CheckBox
            {
                id: enableCheck
                text: catalog.i18nc("@option:check", "Enable gradient")
                checked: true
            }

            // Gradient visualization
            Rectangle
            {
                Layout.fillWidth: true
                height: 120 * screenScaleFactor
                color: UM.Theme.getColor("detail_background")
                border.color: UM.Theme.getColor("lining")
                radius: UM.Theme.getSize("default_radius").width
                opacity: enableCheck.checked ? 1.0 : 0.3

                Canvas
                {
                    id: gradientCanvas
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width

                    onPaint:
                    {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)

                        if (keyframeModel.count < 2) return

                        // Draw axes
                        ctx.strokeStyle = UM.Theme.getColor("lining").toString()
                        ctx.lineWidth = 1
                        ctx.beginPath()
                        ctx.moveTo(30, 0)
                        ctx.lineTo(30, height - 20)
                        ctx.lineTo(width, height - 20)
                        ctx.stroke()

                        // Axis labels
                        ctx.fillStyle = UM.Theme.getColor("text_inactive").toString()
                        ctx.font = "10px sans-serif"
                        ctx.fillText("100%A", 0, 12)
                        ctx.fillText("0%A", 0, height - 22)
                        ctx.fillText("Height (mm)", width / 2, height - 2)

                        // Find height range
                        var minH = keyframeModel.get(0).height_mm
                        var maxH = keyframeModel.get(keyframeModel.count - 1).height_mm
                        if (maxH <= minH) maxH = minH + 10

                        var chartW = width - 40
                        var chartH = height - 30

                        // Draw gradient line
                        ctx.strokeStyle = UM.Theme.getColor("primary").toString()
                        ctx.lineWidth = 2
                        ctx.beginPath()

                        for (var i = 0; i < keyframeModel.count; i++)
                        {
                            var kf = keyframeModel.get(i)
                            var x = 35 + (kf.height_mm - minH) / (maxH - minH) * chartW
                            var y = 5 + (1.0 - kf.ratio_a) * chartH

                            if (i === 0) ctx.moveTo(x, y)
                            else ctx.lineTo(x, y)
                        }
                        ctx.stroke()

                        // Draw keyframe points
                        ctx.fillStyle = UM.Theme.getColor("primary").toString()
                        for (var j = 0; j < keyframeModel.count; j++)
                        {
                            var kf2 = keyframeModel.get(j)
                            var px = 35 + (kf2.height_mm - minH) / (maxH - minH) * chartW
                            var py = 5 + (1.0 - kf2.ratio_a) * chartH
                            ctx.beginPath()
                            ctx.arc(px, py, 5, 0, 2 * Math.PI)
                            ctx.fill()
                        }
                    }

                    // Repaint when model changes
                    Connections
                    {
                        target: keyframeModel
                        function onCountChanged() { gradientCanvas.requestPaint() }
                        function onDataChanged() { gradientCanvas.requestPaint() }
                    }
                }
            }

            // Keyframe table header
            UM.Label
            {
                text: catalog.i18nc("@label", "Keyframes:")
                font: UM.Theme.getFont("default_bold")
            }

            // Keyframe list
            Rectangle
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: UM.Theme.getColor("detail_background")
                border.color: UM.Theme.getColor("lining")
                radius: UM.Theme.getSize("default_radius").width
                opacity: enableCheck.checked ? 1.0 : 0.3

                ColumnLayout
                {
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("narrow_margin").width
                    spacing: 2

                    // Table header
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("default_margin").width

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Height (mm)")
                            Layout.preferredWidth: 120
                            font: UM.Theme.getFont("default_bold")
                        }
                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Ratio A (%)")
                            Layout.preferredWidth: 120
                            font: UM.Theme.getFont("default_bold")
                        }
                        Item { Layout.fillWidth: true }
                    }

                    // Keyframe rows
                    ListView
                    {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: keyframeModel
                        clip: true
                        spacing: 4

                        delegate: RowLayout
                        {
                            width: parent ? parent.width : 0
                            spacing: UM.Theme.getSize("default_margin").width

                            SpinBox
                            {
                                Layout.preferredWidth: 120
                                from: 0
                                to: 9999
                                value: Math.round(model.height_mm * 10)
                                stepSize: 1
                                editable: true

                                property real realValue: value / 10.0

                                textFromValue: function(value) { return (value / 10.0).toFixed(1) }
                                valueFromText: function(text) { return Math.round(parseFloat(text) * 10) }

                                onValueChanged:
                                {
                                    keyframeModel.setProperty(index, "height_mm", value / 10.0)
                                    gradientCanvas.requestPaint()
                                }
                            }

                            SpinBox
                            {
                                Layout.preferredWidth: 120
                                from: 0
                                to: 100
                                value: Math.round(model.ratio_a * 100)
                                stepSize: 5
                                editable: true

                                textFromValue: function(value) { return value + "%" }
                                valueFromText: function(text) { return parseInt(text) }

                                onValueChanged:
                                {
                                    keyframeModel.setProperty(index, "ratio_a", value / 100.0)
                                    gradientCanvas.requestPaint()
                                }
                            }

                            // Color preview at this keyframe
                            Rectangle
                            {
                                width: 20; height: 20; radius: 2
                                border.color: "#333"; border.width: 1
                                color: {
                                    if (!manager) return "#808080"
                                    var extruders = manager.availableExtruders
                                    if (!extruders || extruders.length < 2) return "#808080"
                                    var colorA = extruders[0].color || "#808080"
                                    var colorB = extruders[1].color || "#808080"
                                    return manager.previewBlendColor(colorA, colorB, model.ratio_a)
                                }
                            }

                            Item { Layout.fillWidth: true }

                            Cura.SecondaryButton
                            {
                                text: catalog.i18nc("@action:button", "X")
                                width: 24 * screenScaleFactor
                                height: 24 * screenScaleFactor
                                enabled: keyframeModel.count > 2
                                onClicked: keyframeModel.remove(index)
                            }
                        }
                    }

                    Cura.SecondaryButton
                    {
                        text: catalog.i18nc("@action:button", "+ Add Keyframe")
                        Layout.fillWidth: true
                        onClicked:
                        {
                            // Add at the end with reasonable defaults
                            var lastH = keyframeModel.count > 0 ?
                                        keyframeModel.get(keyframeModel.count - 1).height_mm + 10 : 0
                            keyframeModel.append({ "height_mm": lastH, "ratio_a": 0.5 })
                            gradientCanvas.requestPaint()
                        }
                    }
                }
            }

            // Buttons
            RowLayout
            {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Remove Gradient")
                    onClicked:
                    {
                        if (gradientDialog.targetIndex >= 0)
                        {
                            manager.removeGradient(gradientDialog.targetIndex)
                        }
                        gradientDialog.visible = false
                    }
                }

                Item { Layout.fillWidth: true }

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Cancel")
                    onClicked: gradientDialog.visible = false
                }

                Cura.PrimaryButton
                {
                    text: catalog.i18nc("@action:button", "Apply")
                    onClicked:
                    {
                        gradientDialog.applied()
                        gradientDialog.visible = false
                    }
                }
            }
        }
    }
}
