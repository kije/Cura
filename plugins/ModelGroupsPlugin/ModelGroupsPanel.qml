// Copyright (c) 2024 Community
// Released under the terms of the LGPLv3 or higher.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog
{
    id: dialog

    title: catalog.i18nc("@title:window", "Model Groups")
    width: 700 * screenScaleFactor
    height: 500 * screenScaleFactor
    minimumWidth: 500 * screenScaleFactor
    minimumHeight: 350 * screenScaleFactor
    backgroundColor: UM.Theme.getColor("main_background")

    Item
    {
        UM.I18nCatalog { id: catalog; name: "cura" }
        id: base
        anchors.fill: parent

        TabBar
        {
            id: tabBar
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top

            TabButton
            {
                text: catalog.i18nc("@tab", "All Models")
                width: implicitWidth
            }
            TabButton
            {
                text: catalog.i18nc("@tab", "Groups")
                width: implicitWidth
            }
        }

        StackLayout
        {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: tabBar.bottom
            anchors.topMargin: UM.Theme.getSize("default_margin").height
            anchors.bottom: parent.bottom
            currentIndex: tabBar.currentIndex

            // ==================== Tab 0: All Models ====================
            Item
            {
                id: allModelsTab

                UM.Label
                {
                    id: allModelsHeader
                    anchors.left: parent.left
                    anchors.right: parent.right
                    text: catalog.i18nc("@label", "Click the eye icon to hide/show models on the build plate.")
                    elide: Text.ElideRight
                }

                ListView
                {
                    id: allModelsList
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: allModelsHeader.bottom
                    anchors.topMargin: UM.Theme.getSize("narrow_margin").height
                    anchors.bottom: allModelsButtonRow.top
                    anchors.bottomMargin: UM.Theme.getSize("narrow_margin").height
                    clip: true
                    ScrollBar.vertical: UM.ScrollBar { id: allModelsScrollBar }
                    model: manager.allModelsModel

                    delegate: Rectangle
                    {
                        width: allModelsList.width - allModelsScrollBar.width
                        height: UM.Theme.getSize("standard_list_lineheight").height + UM.Theme.getSize("narrow_margin").height
                        color: "transparent"
                        radius: UM.Theme.getSize("default_radius").width

                        RowLayout
                        {
                            anchors.fill: parent
                            anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                            anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                            spacing: UM.Theme.getSize("narrow_margin").width

                            Button
                            {
                                Layout.preferredWidth: UM.Theme.getSize("standard_list_lineheight").height
                                Layout.preferredHeight: UM.Theme.getSize("standard_list_lineheight").height
                                background: Item {}
                                contentItem: UM.ColorImage
                                {
                                    source: UM.Theme.getIcon("Eye")
                                    color: model.node_visible ? UM.Theme.getColor("icon") : UM.Theme.getColor("text_disabled")
                                    opacity: model.node_visible ? 1.0 : 0.4
                                }
                                onClicked: manager.toggleModelVisibility(model.node_index)
                                ToolTip.text: model.node_visible ? catalog.i18nc("@tooltip", "Hide this model") : catalog.i18nc("@tooltip", "Show this model")
                                ToolTip.visible: hovered
                                ToolTip.delay: 500
                            }

                            UM.Label
                            {
                                Layout.fillWidth: true
                                text: model.node_name
                                elide: Text.ElideRight
                                opacity: model.node_visible ? 1.0 : 0.4
                                font.strikeout: !model.node_visible
                            }

                            UM.Label
                            {
                                visible: model.node_group_name !== ""
                                text: model.node_group_name
                                color: UM.Theme.getColor("text_disabled")
                                elide: Text.ElideRight
                                Layout.preferredWidth: 100 * screenScaleFactor
                            }
                        }
                    }
                }

                Row
                {
                    id: allModelsButtonRow
                    anchors.left: parent.left
                    anchors.bottom: parent.bottom
                    spacing: UM.Theme.getSize("narrow_margin").width

                    Cura.SecondaryButton
                    {
                        text: catalog.i18nc("@action:button", "Hide Selected")
                        onClicked: manager.hideSelectedModels()
                    }

                    Cura.SecondaryButton
                    {
                        text: catalog.i18nc("@action:button", "Show Selected")
                        onClicked: manager.showSelectedModels()
                    }
                }
            }

            // ==================== Tab 1: Groups ====================
            Item
            {
                id: groupsTab
                property int columnWidth: Math.round((groupsTab.width / 2) - UM.Theme.getSize("default_margin").width)

                // Left column: Group list
                Column
                {
                    id: groupsColumn
                    width: groupsTab.columnWidth
                    height: parent.height
                    spacing: UM.Theme.getSize("narrow_margin").height

                    UM.Label
                    {
                        id: groupsHeader
                        anchors.left: parent.left
                        anchors.right: parent.right
                        text: catalog.i18nc("@label", "Groups")
                        font: UM.Theme.getFont("large_bold")
                        elide: Text.ElideRight
                    }

                    ListView
                    {
                        id: groupsList
                        anchors.left: parent.left
                        anchors.right: parent.right
                        height: parent.height - groupsHeader.height - groupButtonRow.height - parent.spacing * 3
                        clip: true
                        ScrollBar.vertical: UM.ScrollBar { id: groupsScrollBar }
                        model: manager.groupsModel

                        delegate: Rectangle
                        {
                            width: groupsList.width - groupsScrollBar.width
                            height: UM.Theme.getSize("standard_list_lineheight").height + UM.Theme.getSize("narrow_margin").height
                            color: manager.selectedGroupId === model.group_id ? UM.Theme.getColor("background_3") : "transparent"
                            radius: UM.Theme.getSize("default_radius").width

                            MouseArea
                            {
                                anchors.fill: parent
                                onClicked: manager.selectedGroupId = model.group_id
                            }

                            RowLayout
                            {
                                anchors.fill: parent
                                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                                anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                                spacing: UM.Theme.getSize("narrow_margin").width

                                CheckBox
                                {
                                    checked: model.group_enabled
                                    onClicked: manager.toggleGroup(model.group_id)
                                    ToolTip.text: catalog.i18nc("@tooltip", "Enable/disable this group")
                                    ToolTip.visible: hovered
                                    ToolTip.delay: 500
                                }

                                UM.Label
                                {
                                    Layout.fillWidth: true
                                    text: model.group_name + " (" + model.node_count + ")"
                                    elide: Text.ElideRight
                                }

                                Button
                                {
                                    Layout.preferredWidth: UM.Theme.getSize("small_button_icon").width
                                    Layout.preferredHeight: UM.Theme.getSize("small_button_icon").height
                                    background: Item {}
                                    contentItem: UM.ColorImage
                                    {
                                        source: UM.Theme.getIcon("Pen")
                                        color: UM.Theme.getColor("icon")
                                    }
                                    onClicked:
                                    {
                                        renameDialog.groupIdToRename = model.group_id
                                        renameDialog.currentName = model.group_name
                                        renameField.text = model.group_name
                                        renameDialog.open()
                                    }
                                }

                                Button
                                {
                                    Layout.preferredWidth: UM.Theme.getSize("small_button_icon").width
                                    Layout.preferredHeight: UM.Theme.getSize("small_button_icon").height
                                    background: Item {}
                                    contentItem: UM.ColorImage
                                    {
                                        source: UM.Theme.getIcon("Cancel")
                                        color: UM.Theme.getColor("icon")
                                    }
                                    onClicked: manager.deleteGroup(model.group_id)
                                }
                            }
                        }
                    }

                    Row
                    {
                        id: groupButtonRow
                        spacing: UM.Theme.getSize("narrow_margin").width

                        Cura.SecondaryButton
                        {
                            text: catalog.i18nc("@action:button", "New Group")
                            onClicked: newGroupDialog.open()
                        }

                        Cura.PrimaryButton
                        {
                            text: catalog.i18nc("@action:button", "Add Selected Objects")
                            enabled: manager.selectedGroupId !== ""
                            onClicked: manager.assignSelectedToCurrentGroup()
                        }
                    }
                }

                // Right column: Nodes in selected group
                Column
                {
                    id: nodesColumn
                    anchors.left: groupsColumn.right
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    width: groupsTab.columnWidth
                    height: parent.height
                    spacing: UM.Theme.getSize("narrow_margin").height

                    UM.Label
                    {
                        id: nodesHeader
                        anchors.left: parent.left
                        anchors.right: parent.right
                        text: catalog.i18nc("@label", "Models in Group")
                        font: UM.Theme.getFont("large_bold")
                        elide: Text.ElideRight
                    }

                    ListView
                    {
                        id: nodesList
                        anchors.left: parent.left
                        anchors.right: parent.right
                        height: parent.height - nodesHeader.height - parent.spacing * 2
                        clip: true
                        ScrollBar.vertical: UM.ScrollBar { id: nodesScrollBar }
                        model: manager.nodesModel

                        delegate: Rectangle
                        {
                            width: nodesList.width - nodesScrollBar.width
                            height: UM.Theme.getSize("standard_list_lineheight").height + UM.Theme.getSize("narrow_margin").height
                            color: "transparent"
                            radius: UM.Theme.getSize("default_radius").width

                            RowLayout
                            {
                                anchors.fill: parent
                                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                                anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                                spacing: UM.Theme.getSize("narrow_margin").width

                                CheckBox
                                {
                                    checked: model.node_enabled
                                    onClicked: manager.toggleNodeInGroup(model.node_index)
                                    ToolTip.text: catalog.i18nc("@tooltip", "Enable/disable this model independently")
                                    ToolTip.visible: hovered
                                    ToolTip.delay: 500
                                }

                                UM.Label
                                {
                                    Layout.fillWidth: true
                                    text: model.node_name
                                    elide: Text.ElideRight
                                }

                                Button
                                {
                                    Layout.preferredWidth: UM.Theme.getSize("small_button_icon").width
                                    Layout.preferredHeight: UM.Theme.getSize("small_button_icon").height
                                    background: Item {}
                                    contentItem: UM.ColorImage
                                    {
                                        source: UM.Theme.getIcon("Cancel")
                                        color: UM.Theme.getColor("icon")
                                    }
                                    onClicked: manager.removeNodeFromGroup(model.node_index)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // New Group dialog
    Dialog
    {
        id: newGroupDialog
        title: catalog.i18nc("@title:window", "New Group")
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: 300 * screenScaleFactor
        modal: true
        anchors.centerIn: Overlay.overlay

        onAccepted:
        {
            if (newGroupField.text.trim() !== "")
            {
                manager.createGroup(newGroupField.text.trim())
            }
            newGroupField.text = ""
        }
        onRejected: newGroupField.text = ""

        Column
        {
            anchors.fill: parent
            spacing: UM.Theme.getSize("narrow_margin").height

            UM.Label { text: catalog.i18nc("@label", "Group name:") }
            TextField
            {
                id: newGroupField
                width: parent.width
                selectByMouse: true
                onAccepted: newGroupDialog.accept()
            }
        }
        onOpened: newGroupField.forceActiveFocus()
    }

    // Rename Group dialog
    Dialog
    {
        id: renameDialog
        title: catalog.i18nc("@title:window", "Rename Group")
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: 300 * screenScaleFactor
        modal: true
        anchors.centerIn: Overlay.overlay

        property string groupIdToRename: ""
        property string currentName: ""

        onAccepted:
        {
            if (renameField.text.trim() !== "")
            {
                manager.renameGroup(groupIdToRename, renameField.text.trim())
            }
        }

        Column
        {
            anchors.fill: parent
            spacing: UM.Theme.getSize("narrow_margin").height

            UM.Label { text: catalog.i18nc("@label", "New name:") }
            TextField
            {
                id: renameField
                width: parent.width
                selectByMouse: true
                onAccepted: renameDialog.accept()
            }
        }
        onOpened: renameField.forceActiveFocus()
    }
}
