// Copyright (c) 2024 Community
// Settings Mixins Plugin - Collapsible sidebar panel for Custom Print Setup

import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.0 as Cura

// This component is reparented into CustomPrintSetup via addAdditionalComponent("customPrintSetup")
Item
{
    id: mixinSidebarPanel
    objectName: "settingsMixinsSidebarPanel"

    width: parent ? parent.width : 0
    height: panelColumn.height
    visible: true

    property var manager: null  // Set from Python

    UM.I18nCatalog { id: catalog; name: "cura" }

    Column
    {
        id: panelColumn
        width: parent.width
        spacing: 0

        // ── Collapsible Header ──────────────────────────────────
        Rectangle
        {
            id: header
            width: parent.width
            height: headerRow.height + 2 * UM.Theme.getSize("narrow_margin").height
            color: headerMouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : UM.Theme.getColor("main_background")
            border.width: UM.Theme.getSize("default_lining").width
            border.color: UM.Theme.getColor("lining")

            property bool expanded: false

            MouseArea
            {
                id: headerMouseArea
                anchors.fill: parent
                hoverEnabled: true
                onClicked: header.expanded = !header.expanded
            }

            RowLayout
            {
                id: headerRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: UM.Theme.getSize("default_margin").width
                anchors.rightMargin: UM.Theme.getSize("default_margin").width
                spacing: UM.Theme.getSize("narrow_margin").width

                UM.ColorImage
                {
                    source: UM.Theme.getIcon(header.expanded ? "ChevronSingleDown" : "ChevronSingleRight")
                    width: UM.Theme.getSize("standard_arrow").width
                    height: UM.Theme.getSize("standard_arrow").height
                    color: UM.Theme.getColor("small_button_text")
                }

                UM.Label
                {
                    text: catalog.i18nc("@label", "Setting Mixins")
                    font: UM.Theme.getFont("medium")
                    Layout.fillWidth: true
                }

                // Mixin count badge
                Rectangle
                {
                    visible: manager != null && manager.activeMixins.length > 0
                    width: badgeLabel.width + 10
                    height: 18
                    radius: 9
                    color: UM.Theme.getColor("text_link")

                    UM.Label
                    {
                        id: badgeLabel
                        anchors.centerIn: parent
                        text: manager != null ? manager.activeMixins.length.toString() : "0"
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("primary_button_text")
                    }
                }

                UM.SimpleButton
                {
                    width: UM.Theme.getSize("print_setup_icon").width
                    height: UM.Theme.getSize("print_setup_icon").height
                    iconSource: UM.Theme.getIcon("Settings")
                    color: hovered ? UM.Theme.getColor("small_button_text_hover") : UM.Theme.getColor("small_button_text")
                    onClicked: if (manager) manager.showManageWindow()

                    UM.ToolTip
                    {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.x, Math.round(parent.y + parent.height / 2))
                        text: catalog.i18nc("@tooltip", "Open Mixin Manager")
                    }
                }
            }
        }

        // ── Expanded Content ────────────────────────────────────
        Rectangle
        {
            width: parent.width
            height: expandedContent.height + 2 * UM.Theme.getSize("narrow_margin").height
            visible: header.expanded
            color: UM.Theme.getColor("main_background")
            border.width: UM.Theme.getSize("default_lining").width
            border.color: UM.Theme.getColor("lining")

            Column
            {
                id: expandedContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: UM.Theme.getSize("narrow_margin").width
                spacing: UM.Theme.getSize("narrow_margin").height

                // Profile notice for built-in profiles
                Rectangle
                {
                    width: parent.width
                    height: profileNoticeRow.height + 8
                    visible: manager != null && !manager.hasCustomProfile
                    color: "#FFF3CD"
                    border.color: "#FFEAA7"
                    border.width: 1
                    radius: UM.Theme.getSize("default_radius").width

                    RowLayout
                    {
                        id: profileNoticeRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 6
                        spacing: 6

                        UM.ColorImage
                        {
                            source: UM.Theme.getIcon("Information")
                            width: 14; height: 14
                            color: "#856404"
                        }

                        UM.Label
                        {
                            text: catalog.i18nc("@info", "Adding a mixin will create a custom profile copy.")
                            font: UM.Theme.getFont("default")
                            color: "#856404"
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // Active mixins list
                Repeater
                {
                    model: manager != null ? manager.activeMixins : []

                    Rectangle
                    {
                        width: expandedContent.width
                        height: mixinItemRow.height + 8
                        color: itemMouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"
                        radius: UM.Theme.getSize("default_radius").width

                        MouseArea
                        {
                            id: itemMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                        }

                        RowLayout
                        {
                            id: mixinItemRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 4
                            spacing: 6

                            UM.Label
                            {
                                text: (index + 1) + "."
                                font: UM.Theme.getFont("default")
                                color: UM.Theme.getColor("text_detail")
                                Layout.preferredWidth: 18
                            }

                            Rectangle
                            {
                                width: 10; height: 10; radius: 5
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

                            UM.SimpleButton
                            {
                                width: 16; height: 16
                                iconSource: UM.Theme.getIcon("ChevronSingleUp")
                                color: hovered ? UM.Theme.getColor("small_button_text_hover") : UM.Theme.getColor("small_button_text")
                                enabled: index > 0
                                opacity: enabled ? 1.0 : 0.3
                                visible: itemMouseArea.containsMouse
                                onClicked: manager.moveActiveMixin(index, index - 1)
                            }

                            UM.SimpleButton
                            {
                                width: 16; height: 16
                                iconSource: UM.Theme.getIcon("ChevronSingleDown")
                                color: hovered ? UM.Theme.getColor("small_button_text_hover") : UM.Theme.getColor("small_button_text")
                                enabled: index < (manager != null ? manager.activeMixins.length - 1 : 0)
                                opacity: enabled ? 1.0 : 0.3
                                visible: itemMouseArea.containsMouse
                                onClicked: manager.moveActiveMixin(index, index + 1)
                            }

                            UM.SimpleButton
                            {
                                width: 16; height: 16
                                iconSource: UM.Theme.getIcon("Cancel")
                                color: hovered ? UM.Theme.getColor("error") : UM.Theme.getColor("small_button_text")
                                visible: itemMouseArea.containsMouse
                                onClicked: manager.removeMixinFromActive(modelData.id)
                            }
                        }
                    }
                }

                // Empty state
                UM.Label
                {
                    visible: manager == null || manager.activeMixins.length === 0
                    text: catalog.i18nc("@info", "No active mixins")
                    font: UM.Theme.getFont("default")
                    color: UM.Theme.getColor("text_detail")
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                }

                // Conflict indicator
                Rectangle
                {
                    width: parent.width
                    height: conflictInfoRow.height + 6
                    visible: manager != null && manager.conflictCount > 0
                    color: "#FFF3CD"
                    border.color: "#FFEAA7"
                    border.width: 1
                    radius: UM.Theme.getSize("default_radius").width

                    RowLayout
                    {
                        id: conflictInfoRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 6
                        spacing: 6

                        UM.ColorImage
                        {
                            source: UM.Theme.getIcon("Warning")
                            width: 14; height: 14
                            color: "#856404"
                        }

                        UM.Label
                        {
                            text: (manager != null ? manager.conflictCount : 0) + " conflict" + ((manager != null && manager.conflictCount !== 1) ? "s" : "")
                            font: UM.Theme.getFont("default")
                            color: "#856404"
                            Layout.fillWidth: true
                        }
                    }
                }

                // Add mixin button row
                RowLayout
                {
                    width: parent.width
                    spacing: UM.Theme.getSize("narrow_margin").width

                    Cura.SecondaryButton
                    {
                        text: catalog.i18nc("@action:button", "+ Add Mixin")
                        height: 24
                        onClicked: addMixinMenu.open()
                    }

                    Item { Layout.fillWidth: true }

                    Cura.SecondaryButton
                    {
                        text: catalog.i18nc("@action:button", "Manage...")
                        height: 24
                        onClicked: if (manager) manager.showManageWindow()
                    }
                }

                // Add Mixin popup menu
                Popup
                {
                    id: addMixinMenu
                    width: expandedContent.width
                    height: Math.min(addMixinColumn.height + 16, 250)
                    padding: 8
                    y: -height

                    background: Rectangle
                    {
                        color: UM.Theme.getColor("main_background")
                        border.color: UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        radius: UM.Theme.getSize("default_radius").width
                    }

                    ScrollView
                    {
                        anchors.fill: parent
                        clip: true

                        Column
                        {
                            id: addMixinColumn
                            width: parent.width
                            spacing: 2

                            UM.Label
                            {
                                text: catalog.i18nc("@label", "Available Mixins")
                                font: UM.Theme.getFont("default_bold")
                                width: parent.width
                                visible: availableMixinRepeater.count > 0
                            }

                            Repeater
                            {
                                id: availableMixinRepeater
                                model: manager != null ? manager.availableMixins : []

                                Rectangle
                                {
                                    width: addMixinColumn.width
                                    height: addRow.height + 8
                                    color: addItemMouse.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"
                                    radius: UM.Theme.getSize("default_radius").width

                                    MouseArea
                                    {
                                        id: addItemMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        onClicked:
                                        {
                                            manager.addMixinToActive(modelData.id)
                                            addMixinMenu.close()
                                        }
                                    }

                                    RowLayout
                                    {
                                        id: addRow
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        anchors.margins: 4
                                        spacing: 6

                                        Rectangle
                                        {
                                            width: 10; height: 10; radius: 5
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

                                        // Scope badge
                                        Rectangle
                                        {
                                            width: scopeLabel.width + 8; height: 16; radius: 8
                                            color: modelData.scope === "global" ? "#E8F5E9" : "#E3F2FD"

                                            UM.Label
                                            {
                                                id: scopeLabel
                                                anchors.centerIn: parent
                                                text: modelData.scope === "global" ? "G" : "E"
                                                font: UM.Theme.getFont("small")
                                                color: modelData.scope === "global" ? "#2E7D32" : "#1565C0"
                                            }
                                        }
                                    }
                                }
                            }

                            UM.Label
                            {
                                visible: availableMixinRepeater.count === 0
                                text: catalog.i18nc("@info", "No mixins available.\nUse Manage to create one.")
                                font: UM.Theme.getFont("default")
                                color: UM.Theme.getColor("text_detail")
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }
        }
    }
}
