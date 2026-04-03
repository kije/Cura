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

    Item
    {
        UM.I18nCatalog { id: catalog; name: "cura" }
        id: base
        anchors.fill: parent

        property var mixedFilaments: manager ? manager.mixedFilaments : []
        property int selectedIndex: -1

        // Sub-dialog instances created as Window children of base Item.
        // UM.Dialog is a Window type, so nesting them inside another UM.Dialog
        // doesn't work. Instead we use Loader to create them on demand.
        property var filamentEditorComponent: Component
        {
            MixedFilamentEditor { }
        }
        property var gradientEditorComponent: Component
        {
            GradientEditor { }
        }

        property var filamentEditorInstance: null
        property var gradientEditorInstance: null

        function showFilamentEditor(editIndex, modelData)
        {
            if (!filamentEditorInstance)
            {
                filamentEditorInstance = filamentEditorComponent.createObject(base)
                filamentEditorInstance.applied.connect(function() {
                    var ed = filamentEditorInstance
                    if (ed.editIndex >= 0)
                    {
                        manager.updateMixedFilament(
                            ed.editIndex, ed.filamentName, ed.filamentA, ed.filamentB,
                            ed.outputMode, ed.ratioA, ed.ratioB,
                            ed.patternMode, ed.customPattern, ed.applyGlobally)
                    }
                    else
                    {
                        manager.addMixedFilament(
                            ed.filamentName, ed.filamentA, ed.filamentB,
                            ed.outputMode, ed.ratioA, ed.ratioB,
                            ed.patternMode, ed.customPattern, ed.applyGlobally)
                    }
                })
            }

            filamentEditorInstance.editIndex = editIndex
            if (editIndex >= 0 && modelData)
            {
                filamentEditorInstance.loadFromData(modelData)
            }
            else
            {
                filamentEditorInstance.reset()
            }
            filamentEditorInstance.show()
        }

        function showGradientEditor(targetIndex, gradientData)
        {
            if (!gradientEditorInstance)
            {
                gradientEditorInstance = gradientEditorComponent.createObject(base)
                gradientEditorInstance.applied.connect(function() {
                    var ge = gradientEditorInstance
                    if (ge.targetIndex >= 0)
                    {
                        manager.setGradient(ge.targetIndex, ge.getGradientJson())
                    }
                })
            }

            gradientEditorInstance.targetIndex = targetIndex
            if (gradientData)
            {
                gradientEditorInstance.loadFromData(gradientData)
            }
            else
            {
                gradientEditorInstance.reset()
            }
            gradientEditorInstance.show()
        }

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
                    onClicked: base.showFilamentEditor(-1, null)
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
                        model: base.mixedFilaments
                        spacing: UM.Theme.getSize("narrow_margin").height

                        delegate: Rectangle
                        {
                            width: filamentList.width
                            height: filamentContent.implicitHeight + 2 * UM.Theme.getSize("default_margin").height
                            color: base.selectedIndex === index ?
                                   UM.Theme.getColor("action_button_active") :
                                   UM.Theme.getColor("action_button")
                            border.color: UM.Theme.getColor("lining")
                            border.width: UM.Theme.getSize("default_lining").width
                            radius: UM.Theme.getSize("default_radius").width

                            MouseArea
                            {
                                anchors.fill: parent
                                onClicked: base.selectedIndex = index
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
                                        text: "Extruder " + (modelData.filament_a + 1) +
                                              " + Extruder " + (modelData.filament_b + 1) +
                                              "  |  Mode: " + (modelData.output_mode === "tool_change" ? "IDEX" : "Mixing")
                                        font: UM.Theme.getFont("default")
                                        color: UM.Theme.getColor("text_inactive")
                                    }
                                }

                                // Enable checkbox
                                CheckBox
                                {
                                    checked: modelData.enabled !== undefined ? modelData.enabled : true
                                    onCheckedChanged: manager.setMixedFilamentEnabled(index, checked)
                                }

                                // Action buttons
                                ColumnLayout
                                {
                                    spacing: 2

                                    Cura.SecondaryButton
                                    {
                                        text: catalog.i18nc("@action:button", "Edit")
                                        height: 24 * screenScaleFactor
                                        onClicked: base.showFilamentEditor(index, modelData)
                                    }

                                    Cura.SecondaryButton
                                    {
                                        text: catalog.i18nc("@action:button", "Gradient")
                                        height: 24 * screenScaleFactor
                                        onClicked: base.showGradientEditor(index, modelData.gradient || null)
                                    }
                                }

                                // Delete button
                                Cura.SecondaryButton
                                {
                                    text: "X"
                                    width: 28 * screenScaleFactor
                                    height: 28 * screenScaleFactor
                                    onClicked: manager.removeMixedFilament(index)
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
                    visible: !base.mixedFilaments || base.mixedFilaments.length === 0
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
            UM.Label
            {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: catalog.i18nc("@info",
                    "Mixed filaments alternate layers of different physical filaments to create blended colors. " +
                    "Assign objects to proxy extruder slots, then define which physical filaments alternate.")
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text_inactive")
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
                    var filaments = manager.mixedFilaments;
                    tipText += "<ul>";
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
            labelText: manager ? "" + manager.enabledMixedFilamentCount : "0"
        }
    }
}
