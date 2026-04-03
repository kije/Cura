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
    id: dialog
    title: catalog.i18nc("@title:window", "Mixed Colors")
    width: 750 * screenScaleFactor
    height: 600 * screenScaleFactor
    minimumWidth: 550 * screenScaleFactor
    minimumHeight: 400 * screenScaleFactor
    backgroundColor: UM.Theme.getColor("main_background")

    UM.I18nCatalog { id: catalog; name: "cura" }

    property var mixedFilaments: manager.mixedFilaments
    property int selectedIndex: -1

    // Editor dialog instances
    MixedFilamentEditor
    {
        id: filamentEditor
        onApplied:
        {
            if (filamentEditor.editIndex >= 0)
            {
                manager.updateMixedFilament(
                    filamentEditor.editIndex,
                    filamentEditor.filamentName,
                    filamentEditor.filamentA,
                    filamentEditor.filamentB,
                    filamentEditor.proxyExtruder,
                    filamentEditor.outputMode,
                    filamentEditor.ratioA,
                    filamentEditor.ratioB,
                    filamentEditor.patternMode,
                    filamentEditor.customPattern
                )
            }
            else
            {
                manager.addMixedFilament(
                    filamentEditor.filamentName,
                    filamentEditor.filamentA,
                    filamentEditor.filamentB,
                    filamentEditor.proxyExtruder,
                    filamentEditor.outputMode,
                    filamentEditor.ratioA,
                    filamentEditor.ratioB,
                    filamentEditor.patternMode,
                    filamentEditor.customPattern
                )
            }
        }
    }

    GradientEditor
    {
        id: gradientEditor
        onApplied:
        {
            if (gradientEditor.targetIndex >= 0)
            {
                manager.setGradient(gradientEditor.targetIndex, gradientEditor.getGradientJson())
            }
        }
    }

    Item
    {
        anchors.fill: parent

        ColumnLayout
        {
            anchors.fill: parent
            spacing: UM.Theme.getSize("default_margin").height

            // Header row
            RowLayout
            {
                Layout.fillWidth: true

                UM.Label
                {
                    text: catalog.i18nc("@label", "Mixed Filaments")
                    font: UM.Theme.getFont("large_bold")
                    Layout.fillWidth: true
                }

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Add Mixed Filament")
                    onClicked:
                    {
                        filamentEditor.editIndex = -1
                        filamentEditor.reset()
                        filamentEditor.show()
                    }
                }
            }

            // Filament list
            Rectangle
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: UM.Theme.getColor("detail_background")
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width

                ScrollView
                {
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    clip: true

                    ListView
                    {
                        id: filamentList
                        model: dialog.mixedFilaments
                        spacing: UM.Theme.getSize("narrow_margin").height

                        delegate: Rectangle
                        {
                            width: filamentList.width
                            height: filamentContent.implicitHeight + 2 * UM.Theme.getSize("default_margin").height
                            color: dialog.selectedIndex === index ?
                                   UM.Theme.getColor("action_button_active") :
                                   UM.Theme.getColor("action_button")
                            border.color: UM.Theme.getColor("lining")
                            border.width: UM.Theme.getSize("default_lining").width
                            radius: UM.Theme.getSize("default_radius").width

                            MouseArea
                            {
                                anchors.fill: parent
                                onClicked: dialog.selectedIndex = index
                            }

                            RowLayout
                            {
                                id: filamentContent
                                anchors.fill: parent
                                anchors.margins: UM.Theme.getSize("default_margin").width
                                spacing: UM.Theme.getSize("default_margin").width

                                // Color preview swatch
                                Rectangle
                                {
                                    width: 40 * screenScaleFactor
                                    height: 40 * screenScaleFactor
                                    radius: 4
                                    color: modelData.preview_color ?
                                           Qt.rgba(modelData.preview_color[0] / 255,
                                                   modelData.preview_color[1] / 255,
                                                   modelData.preview_color[2] / 255, 1.0) :
                                           "#808080"
                                    border.color: UM.Theme.getColor("lining")
                                    border.width: 1

                                    // Striped pattern overlay to show dithering
                                    Column
                                    {
                                        anchors.fill: parent
                                        anchors.margins: 2
                                        clip: true
                                        Repeater
                                        {
                                            model: 8
                                            Rectangle
                                            {
                                                width: parent.width
                                                height: parent.height / 8
                                                color: "transparent"
                                                border.color: Qt.rgba(1, 1, 1, 0.15)
                                                border.width: index % 2 === 0 ? 0 : 1
                                            }
                                        }
                                    }
                                }

                                // Filament info
                                ColumnLayout
                                {
                                    Layout.fillWidth: true
                                    spacing: 2

                                    UM.Label
                                    {
                                        text: modelData.name || "Mixed Filament"
                                        font: UM.Theme.getFont("default_bold")
                                    }

                                    UM.Label
                                    {
                                        text: catalog.i18nc("@label", "Extruder %1 + Extruder %2  |  Pattern: %3  |  Mode: %4")
                                            .arg(modelData.filament_a + 1)
                                            .arg(modelData.filament_b + 1)
                                            .arg(modelData.pattern ? modelData.pattern.display || "AB" : "AB")
                                            .arg(modelData.output_mode === "tool_change" ? "IDEX" : "Mixing")
                                        font: UM.Theme.getFont("default")
                                        color: UM.Theme.getColor("text_inactive")
                                    }

                                    UM.Label
                                    {
                                        text: modelData.gradient ?
                                              catalog.i18nc("@label", "Gradient: %1 keyframes").arg(
                                                  modelData.gradient.keyframes ? modelData.gradient.keyframes.length : 0) :
                                              catalog.i18nc("@label", "No gradient")
                                        font: UM.Theme.getFont("small")
                                        color: UM.Theme.getColor("text_inactive")
                                        visible: modelData.gradient !== null && modelData.gradient !== undefined
                                    }
                                }

                                // Enable checkbox
                                CheckBox
                                {
                                    checked: modelData.enabled !== undefined ? modelData.enabled : true
                                    onCheckedChanged: manager.setMixedFilamentEnabled(index, checked)

                                    ToolTip.text: catalog.i18nc("@info:tooltip", "Enable/disable this mixed filament")
                                    ToolTip.visible: hovered
                                    ToolTip.delay: 500
                                }

                                // Action buttons
                                ColumnLayout
                                {
                                    spacing: 2

                                    Cura.SecondaryButton
                                    {
                                        text: catalog.i18nc("@action:button", "Edit")
                                        height: 24 * screenScaleFactor
                                        onClicked:
                                        {
                                            filamentEditor.editIndex = index
                                            filamentEditor.loadFromData(modelData)
                                            filamentEditor.show()
                                        }
                                    }

                                    Cura.SecondaryButton
                                    {
                                        text: catalog.i18nc("@action:button", "Gradient")
                                        height: 24 * screenScaleFactor
                                        onClicked:
                                        {
                                            gradientEditor.targetIndex = index
                                            if (modelData.gradient)
                                            {
                                                gradientEditor.loadFromData(modelData.gradient)
                                            }
                                            else
                                            {
                                                gradientEditor.reset()
                                            }
                                            gradientEditor.show()
                                        }
                                    }
                                }

                                // Delete button
                                Cura.SecondaryButton
                                {
                                    text: catalog.i18nc("@action:button", "X")
                                    width: 28 * screenScaleFactor
                                    height: 28 * screenScaleFactor
                                    onClicked: manager.removeMixedFilament(index)

                                    ToolTip.text: catalog.i18nc("@info:tooltip", "Remove this mixed filament")
                                    ToolTip.visible: hovered
                                    ToolTip.delay: 500
                                }
                            }
                        }
                    }
                }

                // Empty state
                UM.Label
                {
                    anchors.centerIn: parent
                    text: catalog.i18nc("@label", "No mixed filaments defined.\nClick 'Add Mixed Filament' to create one.")
                    horizontalAlignment: Text.AlignHCenter
                    color: UM.Theme.getColor("text_inactive")
                    visible: !dialog.mixedFilaments || dialog.mixedFilaments.length === 0
                }
            }

            // Pre-heat settings
            Rectangle
            {
                Layout.fillWidth: true
                height: preheatCol.implicitHeight + 2 * UM.Theme.getSize("default_margin").height
                color: UM.Theme.getColor("action_button")
                border.color: UM.Theme.getColor("lining")
                radius: UM.Theme.getSize("default_radius").width

                ColumnLayout
                {
                    id: preheatCol
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    spacing: 4

                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Temperature Pre-heating")
                        font: UM.Theme.getFont("default_bold")
                    }

                    RowLayout
                    {
                        spacing: UM.Theme.getSize("default_margin").width

                        CheckBox
                        {
                            id: preheatCheck
                            text: catalog.i18nc("@option:check", "Pre-heat next extruder")
                            checked: manager ? manager.enablePreheat : true
                            onCheckedChanged: if (manager) manager.setEnablePreheat(checked)

                            ToolTip.text: catalog.i18nc("@info:tooltip",
                                "Start heating the next extruder before the tool change " +
                                "to reduce waiting time.")
                            ToolTip.visible: hovered
                            ToolTip.delay: 500
                        }

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Lookahead:")
                            visible: preheatCheck.checked
                        }

                        SpinBox
                        {
                            from: 1; to: 20
                            value: manager ? manager.preheatLayers : 3
                            visible: preheatCheck.checked
                            editable: true
                            onValueChanged: if (manager) manager.setPreheatLayers(value)

                            ToolTip.text: catalog.i18nc("@info:tooltip",
                                "Number of layers ahead to start pre-heating.")
                            ToolTip.visible: hovered
                            ToolTip.delay: 500
                        }

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "layers")
                            visible: preheatCheck.checked
                        }
                    }
                }
            }

            // Info text
            Rectangle
            {
                Layout.fillWidth: true
                height: infoText.implicitHeight + 2 * UM.Theme.getSize("default_margin").height
                color: UM.Theme.getColor("action_button")
                border.color: UM.Theme.getColor("lining")
                radius: UM.Theme.getSize("default_radius").width

                UM.Label
                {
                    id: infoText
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    wrapMode: Text.WordWrap
                    text: catalog.i18nc("@info",
                        "Mixed filaments create blended colors by alternating layers of different physical filaments. " +
                        "Assign objects to proxy extruder slots, then define which physical filaments alternate.\n\n" +
                        "IDEX/Tool Changer: Alternates tool changes between layers.\n" +
                        "Mixing Hotend: Sets mix ratios via M163/M164 (Marlin) or M567 (RepRap).\n\n" +
                        "Per-object assignment: Objects are matched by ;MESH: comments in G-code. " +
                        "Use the Mesh Assignments section to map specific objects to mixed filaments.\n\n" +
                        "Bresenham dithering distributes layer alternation evenly for smoother color transitions.")
                    font: UM.Theme.getFont("small")
                    color: UM.Theme.getColor("text_inactive")
                }
            }

            // Bottom buttons
            RowLayout
            {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width

                Item { Layout.fillWidth: true }

                Cura.PrimaryButton
                {
                    text: catalog.i18nc("@action:button", "Close")
                    onClicked: dialog.close()
                }
            }
        }
    }

    // Save button indicator - shown next to the save/export button when
    // mixed filaments are active. Same pattern as PostProcessingPlugin.
    Item
    {
        objectName: "mixedColorSaveAreaButton"
        visible: manager ? manager.enabledMixedFilamentCount > 0 : false
        height: UM.Theme.getSize("action_button").height
        width: height

        Cura.SecondaryButton
        {
            height: UM.Theme.getSize("action_button").height
            tooltip:
            {
                var tipText = catalog.i18nc("@info:tooltip", "Mixed Colors active.");
                if (manager && manager.enabledMixedFilamentCount > 0)
                {
                    tipText += "<br><br>" + catalog.i18nc("@info:tooltip",
                        "%1 mixed filament(s) will be applied:").arg(manager.enabledMixedFilamentCount);
                    tipText += "<ul>";
                    var filaments = manager.mixedFilaments;
                    for (var i = 0; i < filaments.length; i++)
                    {
                        if (filaments[i].enabled)
                        {
                            tipText += "<li>" + filaments[i].name + "</li>";
                        }
                    }
                    tipText += "</ul>";
                }
                return tipText
            }
            toolTipContentAlignment: UM.Enums.ContentAlignment.AlignLeft
            onClicked: dialog.show()
            iconSource: Qt.resolvedUrl("../resources/icons/mixed_color.svg")
            fixedWidthMode: false
        }

        Cura.NotificationIcon
        {
            id: activeMixCountIcon
            visible: manager ? manager.enabledMixedFilamentCount > 0 : false
            anchors
            {
                horizontalCenter: parent.right
                verticalCenter: parent.top
            }
            labelText: manager ? manager.enabledMixedFilamentCount : "0"
        }
    }
}
