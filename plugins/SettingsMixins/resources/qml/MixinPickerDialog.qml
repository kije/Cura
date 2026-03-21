// Copyright (c) 2024 Community
// Settings Mixins Plugin - Pick a mixin to add a setting to

import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog
{
    id: pickerDialog
    title: catalog.i18nc("@title:window", "Add Setting to Mixin")
    width: 360 * screenScaleFactor
    height: contentColumn.height + 80 * screenScaleFactor
    minimumWidth: 300 * screenScaleFactor
    minimumHeight: 200 * screenScaleFactor
    backgroundColor: UM.Theme.getColor("main_background")

    property real screenScaleFactor: UM.Theme.getSize("default_margin").width / 10

    UM.I18nCatalog { id: catalog; name: "cura" }

    Item
    {
        anchors.fill: parent

        ColumnLayout
        {
            id: contentColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            spacing: UM.Theme.getSize("default_margin").height

            // Setting info
            Rectangle
            {
                Layout.fillWidth: true
                height: settingInfoColumn.height + 12
                color: "#E3F2FD"
                border.color: "#90CAF9"
                border.width: 1
                radius: UM.Theme.getSize("default_radius").width

                ColumnLayout
                {
                    id: settingInfoColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 8
                    spacing: 2

                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Setting: %1").arg(manager.pendingSettingLabel)
                        font: UM.Theme.getFont("default_bold")
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }

                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Value: %1").arg(manager.pendingSettingValue)
                        font: UM.Theme.getFont("default")
                        color: UM.Theme.getColor("text_detail")
                        Layout.fillWidth: true
                    }

                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Key: %1").arg(manager.pendingSettingKey)
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text_detail")
                        Layout.fillWidth: true
                    }
                }
            }

            UM.Label
            {
                text: catalog.i18nc("@label", "Choose a mixin:")
                font: UM.Theme.getFont("medium_bold")
            }

            // Existing mixins list
            ScrollView
            {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(mixinPickerList.contentHeight, 200 * screenScaleFactor)
                clip: true

                ListView
                {
                    id: mixinPickerList
                    model: manager.mixinLibrary
                    spacing: 2

                    delegate: Rectangle
                    {
                        width: mixinPickerList.width
                        height: pickRow.height + 8
                        color: pickMouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"
                        radius: UM.Theme.getSize("default_radius").width
                        border.width: 1
                        border.color: pickMouseArea.containsMouse ? UM.Theme.getColor("lining") : "transparent"

                        MouseArea
                        {
                            id: pickMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked:
                            {
                                manager.addPendingSettingToMixin(modelData.id)
                                pickerDialog.close()
                            }
                        }

                        RowLayout
                        {
                            id: pickRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 8
                            spacing: 8

                            Rectangle
                            {
                                width: 12; height: 12; radius: 6
                                color: modelData.color
                                Layout.alignment: Qt.AlignVCenter
                            }

                            UM.Label
                            {
                                text: modelData.name
                                font: UM.Theme.getFont("default")
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }

                            UM.Label
                            {
                                text: modelData.settingCount + " settings"
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_detail")
                            }
                        }
                    }
                }
            }

            UM.Label
            {
                visible: mixinPickerList.count === 0
                text: catalog.i18nc("@info", "No mixins yet. Create one below.")
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text_detail")
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
            }

            // Create new mixin section
            Rectangle
            {
                Layout.fillWidth: true
                height: newMixinRow.height + 12
                color: UM.Theme.getColor("main_background")
                border.width: UM.Theme.getSize("default_lining").width
                border.color: UM.Theme.getColor("lining")
                radius: UM.Theme.getSize("default_radius").width

                RowLayout
                {
                    id: newMixinRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 8
                    spacing: 8

                    Cura.TextField
                    {
                        id: newMixinNameField
                        Layout.fillWidth: true
                        placeholderText: catalog.i18nc("@text:placeholder", "New mixin name...")
                        onAccepted:
                        {
                            if (text.trim() !== "")
                            {
                                manager.addPendingSettingToNewMixin(text.trim())
                                pickerDialog.close()
                            }
                        }
                    }

                    Cura.PrimaryButton
                    {
                        text: catalog.i18nc("@action:button", "Create")
                        height: 28
                        enabled: newMixinNameField.text.trim() !== ""
                        onClicked:
                        {
                            manager.addPendingSettingToNewMixin(newMixinNameField.text.trim())
                            pickerDialog.close()
                        }
                    }
                }
            }
        }
    }

    onVisibleChanged:
    {
        if (visible)
        {
            newMixinNameField.text = ""
        }
    }
}
