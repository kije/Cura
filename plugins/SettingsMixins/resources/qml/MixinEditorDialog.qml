// Copyright (c) 2024 Community
// Settings Mixins Plugin - Mixin Editor Dialog

import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3
import QtQuick.Dialogs

import UM 1.5 as UM
import Cura 1.0 as Cura

Dialog
{
    id: editorDialog
    title: manager.editingMixinId !== "" ? catalog.i18nc("@title:window", "Edit Mixin") : catalog.i18nc("@title:window", "Create New Mixin")
    width: 650 * screenScaleFactor
    height: 550 * screenScaleFactor
    anchors.centerIn: parent
    standardButtons: Dialog.NoButton
    modal: true

    property real screenScaleFactor: UM.Theme.getSize("default_margin").width / 10

    UM.I18nCatalog { id: catalog; name: "cura" }

    ColumnLayout
    {
        anchors.fill: parent
        spacing: UM.Theme.getSize("default_margin").height

        // ── Metadata Fields ───────────────────────────────────────────
        GridLayout
        {
            columns: 2
            Layout.fillWidth: true
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("narrow_margin").height

            UM.Label { text: catalog.i18nc("@label", "Name:"); font: UM.Theme.getFont("default") }
            Cura.TextField
            {
                id: nameField
                Layout.fillWidth: true
                text: manager.editingName
                placeholderText: catalog.i18nc("@text:placeholder", "e.g., PETG General, Fine Detail...")
                onTextChanged: manager.setEditingName(text)
            }

            UM.Label { text: catalog.i18nc("@label", "Description:"); font: UM.Theme.getFont("default") }
            Cura.TextField
            {
                id: descField
                Layout.fillWidth: true
                text: manager.editingDescription
                placeholderText: catalog.i18nc("@text:placeholder", "Brief description of what this mixin does")
                onTextChanged: manager.setEditingDescription(text)
            }

            UM.Label { text: catalog.i18nc("@label", "Scope:"); font: UM.Theme.getFont("default") }
            RowLayout
            {
                spacing: UM.Theme.getSize("default_margin").width

                RadioButton
                {
                    text: catalog.i18nc("@option", "Global")
                    checked: manager.editingScope === "global"
                    onCheckedChanged: if (checked) manager.setEditingScope("global")
                }
                RadioButton
                {
                    text: catalog.i18nc("@option", "Per-Extruder")
                    checked: manager.editingScope === "extruder"
                    onCheckedChanged: if (checked) manager.setEditingScope("extruder")
                }
            }

            UM.Label { text: catalog.i18nc("@label", "Color:"); font: UM.Theme.getFont("default") }
            Row
            {
                spacing: 4
                Repeater
                {
                    model: manager.colorPalette
                    Rectangle
                    {
                        width: 24; height: 24; radius: 12
                        color: modelData
                        border.width: manager.editingColor === modelData ? 3 : 1
                        border.color: manager.editingColor === modelData ? UM.Theme.getColor("text") : UM.Theme.getColor("lining")
                        MouseArea
                        {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: manager.setEditingColor(modelData)
                        }
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: UM.Theme.getColor("lining") }

        // ── Settings Header ───────────────────────────────────────────
        UM.Label
        {
            text: catalog.i18nc("@label", "Settings")
            font: UM.Theme.getFont("medium_bold")
        }

        // Search to add settings
        RowLayout
        {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("narrow_margin").width

            Cura.TextField
            {
                id: settingSearchField
                Layout.fillWidth: true
                placeholderText: catalog.i18nc("@text:placeholder", "Search setting to add (e.g., 'print speed', 'fan')...")

                onTextChanged:
                {
                    if (text.length >= 2)
                    {
                        searchResultsList.model = manager.searchSettings(text)
                        searchPopup.open()
                    }
                    else
                    {
                        searchPopup.close()
                    }
                }

                Keys.onEscapePressed: searchPopup.close()
            }
        }

        // Search results dropdown
        Popup
        {
            id: searchPopup
            y: settingSearchField.mapToItem(editorDialog.contentItem, 0, settingSearchField.height).y
            x: settingSearchField.mapToItem(editorDialog.contentItem, 0, 0).x
            width: settingSearchField.width
            height: Math.min(200, searchResultsList.count * 32 + 8)
            padding: 4
            closePolicy: Popup.CloseOnPressOutside | Popup.CloseOnEscape

            background: Rectangle
            {
                color: UM.Theme.getColor("main_background")
                border.width: 1
                border.color: UM.Theme.getColor("lining")
                radius: UM.Theme.getSize("default_radius").width
            }

            ListView
            {
                id: searchResultsList
                anchors.fill: parent
                clip: true

                delegate: Rectangle
                {
                    width: searchResultsList.width
                    height: 30
                    color: searchMouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"
                    radius: 2

                    MouseArea
                    {
                        id: searchMouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked:
                        {
                            manager.captureCurrentValue(modelData.key)
                            settingSearchField.text = ""
                            searchPopup.close()
                        }
                    }

                    RowLayout
                    {
                        anchors.fill: parent
                        anchors.margins: 4
                        spacing: 8

                        UM.Label
                        {
                            text: modelData.label
                            font: UM.Theme.getFont("default")
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }

                        UM.Label
                        {
                            text: modelData.key
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_detail")
                        }

                        UM.Label
                        {
                            text: modelData.unit
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_detail")
                            visible: modelData.unit !== ""
                        }
                    }
                }
            }
        }

        // ── Mixin Settings List ───────────────────────────────────────
        ScrollView
        {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ListView
            {
                id: settingsList
                model: manager.editingSettings
                spacing: 2

                delegate: Rectangle
                {
                    width: settingsList.width
                    height: settingRow.height + 8
                    color: settingMouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"
                    radius: UM.Theme.getSize("default_radius").width
                    border.width: 1
                    border.color: UM.Theme.getColor("lining")

                    MouseArea
                    {
                        id: settingMouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                    }

                    RowLayout
                    {
                        id: settingRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 8
                        spacing: 8

                        UM.Label
                        {
                            text: manager.getSettingLabel(modelData.key)
                            font: UM.Theme.getFont("default")
                            Layout.preferredWidth: parent.width * 0.4
                            elide: Text.ElideRight

                            ToolTip.text: modelData.key
                            ToolTip.visible: settingLabelMouse.containsMouse
                            ToolTip.delay: 500

                            MouseArea
                            {
                                id: settingLabelMouse
                                anchors.fill: parent
                                hoverEnabled: true
                            }
                        }

                        Cura.TextField
                        {
                            Layout.fillWidth: true
                            text: modelData.value
                            onEditingFinished: manager.setEditingSetting(modelData.key, text)
                        }

                        UM.Label
                        {
                            text: manager.getSettingUnit(modelData.key)
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text_detail")
                            Layout.preferredWidth: 40
                            visible: manager.getSettingUnit(modelData.key) !== ""
                        }

                        UM.SimpleButton
                        {
                            width: 20; height: 20
                            iconSource: UM.Theme.getIcon("Cancel")
                            color: hovered ? UM.Theme.getColor("error") : UM.Theme.getColor("small_button_text")
                            onClicked: manager.removeEditingSetting(modelData.key)
                        }
                    }
                }

                UM.Label
                {
                    visible: settingsList.count === 0
                    anchors.centerIn: parent
                    text: catalog.i18nc("@info", "No settings added yet.\nSearch for settings above to add them.")
                    horizontalAlignment: Text.AlignHCenter
                    color: UM.Theme.getColor("text_detail")
                }
            }
        }

        // ── Action Buttons ────────────────────────────────────────────
        RowLayout
        {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width

            Item { Layout.fillWidth: true }

            Cura.SecondaryButton
            {
                text: catalog.i18nc("@action:button", "Export...")
                visible: manager.editingMixinId !== ""
                onClicked: exportDialog.open()
            }

            Cura.SecondaryButton
            {
                text: catalog.i18nc("@action:button", "Cancel")
                onClicked: editorDialog.close()
            }

            Cura.PrimaryButton
            {
                text: manager.editingMixinId !== "" ? catalog.i18nc("@action:button", "Save") : catalog.i18nc("@action:button", "Create")
                enabled: nameField.text.trim() !== ""
                onClicked:
                {
                    manager.saveEditingMixin()
                    editorDialog.close()
                }
            }
        }
    }

    FileDialog
    {
        id: exportDialog
        title: catalog.i18nc("@title:window", "Export Mixin")
        nameFilters: ["Mixin files (*.cura_mixin)", "JSON files (*.json)"]
        fileMode: FileDialog.SaveFile
        onAccepted: manager.exportMixinToPath(manager.editingMixinId, selectedFile.toString())
    }
}
