// Copyright (c) 2024 Community
// Settings Mixins Plugin - Bulk capture settings into a mixin via checkboxes

import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog
{
    id: captureDialog
    title: catalog.i18nc("@title:window", "Capture Settings to Mixin")
    width: 550 * screenScaleFactor
    height: 500 * screenScaleFactor
    minimumWidth: 450 * screenScaleFactor
    minimumHeight: 350 * screenScaleFactor
    backgroundColor: UM.Theme.getColor("main_background")

    property real screenScaleFactor: UM.Theme.getSize("default_margin").width / 10
    property var changedSettings: []
    property var selectedKeys: ({})  // key -> bool map
    property int selectedCount: 0

    UM.I18nCatalog { id: catalog; name: "cura" }

    onVisibleChanged:
    {
        if (visible)
        {
            changedSettings = manager.getChangedSettings()
            selectedKeys = {}
            selectedCount = 0
            targetSelector.currentIndex = 0
            newMixinName.text = ""
            searchField.text = ""
        }
    }

    function updateSelectedCount()
    {
        var count = 0
        for (var key in selectedKeys)
        {
            if (selectedKeys[key]) count++
        }
        selectedCount = count
    }

    function getSelectedKeyList()
    {
        var keys = []
        for (var key in selectedKeys)
        {
            if (selectedKeys[key]) keys.push(key)
        }
        return keys
    }

    function filteredSettings()
    {
        var query = searchField.text.toLowerCase()
        if (query === "") return changedSettings

        var result = []
        for (var i = 0; i < changedSettings.length; i++)
        {
            var s = changedSettings[i]
            if (s.label.toLowerCase().indexOf(query) !== -1 ||
                s.key.toLowerCase().indexOf(query) !== -1)
            {
                result.push(s)
            }
        }
        return result
    }

    Item
    {
        anchors.fill: parent

        ColumnLayout
        {
            anchors.fill: parent
            spacing: UM.Theme.getSize("default_margin").height

            // Header info
            UM.Label
            {
                text: catalog.i18nc("@info", "Select settings from your current profile to store in a mixin.")
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text_detail")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            // Search + select all row
            RowLayout
            {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width

                Cura.TextField
                {
                    id: searchField
                    Layout.fillWidth: true
                    placeholderText: catalog.i18nc("@text:placeholder", "Search settings...")
                }

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Select All")
                    height: 28
                    onClicked:
                    {
                        var filtered = filteredSettings()
                        for (var i = 0; i < filtered.length; i++)
                        {
                            selectedKeys[filtered[i].key] = true
                        }
                        selectedKeys = selectedKeys  // trigger binding update
                        updateSelectedCount()
                    }
                }

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Select None")
                    height: 28
                    onClicked:
                    {
                        selectedKeys = {}
                        updateSelectedCount()
                    }
                }
            }

            // Settings list with checkboxes
            Rectangle
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                border.width: UM.Theme.getSize("default_lining").width
                border.color: UM.Theme.getColor("lining")
                radius: UM.Theme.getSize("default_radius").width
                color: UM.Theme.getColor("main_background")

                ScrollView
                {
                    anchors.fill: parent
                    anchors.margins: 2
                    clip: true

                    ListView
                    {
                        id: settingsList
                        model: filteredSettings()
                        spacing: 1

                        Connections
                        {
                            target: searchField
                            function onTextChanged() { settingsList.model = captureDialog.filteredSettings() }
                        }

                        delegate: Rectangle
                        {
                            width: settingsList.width
                            height: settingRow.height + 6
                            color: settingMouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"

                            MouseArea
                            {
                                id: settingMouseArea
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked:
                                {
                                    var key = modelData.key
                                    selectedKeys[key] = !selectedKeys[key]
                                    selectedKeys = selectedKeys  // trigger update
                                    updateSelectedCount()
                                }
                            }

                            RowLayout
                            {
                                id: settingRow
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                spacing: 8

                                CheckBox
                                {
                                    checked: selectedKeys[modelData.key] === true
                                    onClicked:
                                    {
                                        selectedKeys[modelData.key] = checked
                                        selectedKeys = selectedKeys
                                        updateSelectedCount()
                                    }
                                    Layout.preferredWidth: 20
                                    Layout.preferredHeight: 20
                                }

                                ColumnLayout
                                {
                                    Layout.fillWidth: true
                                    spacing: 0

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
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                }

                                UM.Label
                                {
                                    text: modelData.value + (modelData.unit !== "" ? " " + modelData.unit : "")
                                    font: UM.Theme.getFont("default")
                                    color: UM.Theme.getColor("text_link")
                                    Layout.preferredWidth: 80
                                    horizontalAlignment: Text.AlignRight
                                    elide: Text.ElideRight
                                }

                                // Source badge
                                Rectangle
                                {
                                    width: sourceLabel.width + 8; height: 16; radius: 8
                                    color: modelData.source.indexOf("user") !== -1 ? "#FFF3CD" : "#E8F5E9"

                                    UM.Label
                                    {
                                        id: sourceLabel
                                        anchors.centerIn: parent
                                        text: modelData.source
                                        font: UM.Theme.getFont("small")
                                        color: modelData.source.indexOf("user") !== -1 ? "#856404" : "#2E7D32"
                                    }
                                }
                            }
                        }
                    }
                }

                // Empty state
                UM.Label
                {
                    visible: settingsList.count === 0
                    anchors.centerIn: parent
                    text: changedSettings.length === 0
                        ? catalog.i18nc("@info", "No changed settings in the current profile.\nModify some settings first.")
                        : catalog.i18nc("@info", "No settings match your search.")
                    font: UM.Theme.getFont("default")
                    color: UM.Theme.getColor("text_detail")
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
            }

            // Selection count
            UM.Label
            {
                text: selectedCount + " setting" + (selectedCount !== 1 ? "s" : "") + " selected"
                font: UM.Theme.getFont("default_bold")
                color: selectedCount > 0 ? UM.Theme.getColor("text") : UM.Theme.getColor("text_detail")
            }

            // Target mixin selector
            Rectangle
            {
                Layout.fillWidth: true
                height: targetColumn.height + 16
                border.width: UM.Theme.getSize("default_lining").width
                border.color: UM.Theme.getColor("lining")
                radius: UM.Theme.getSize("default_radius").width
                color: UM.Theme.getColor("main_background")

                ColumnLayout
                {
                    id: targetColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 8
                    spacing: 6

                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Store in:")
                        font: UM.Theme.getFont("default_bold")
                    }

                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 8

                        ComboBox
                        {
                            id: targetSelector
                            Layout.fillWidth: true

                            model:
                            {
                                var items = []
                                items.push("+ Create New Mixin")
                                var library = manager.mixinLibrary
                                for (var i = 0; i < library.length; i++)
                                {
                                    items.push(library[i].name)
                                }
                                return items
                            }
                        }

                        // New mixin name (visible when "Create New" selected)
                        Cura.TextField
                        {
                            id: newMixinName
                            visible: targetSelector.currentIndex === 0
                            Layout.fillWidth: true
                            placeholderText: catalog.i18nc("@text:placeholder", "Mixin name...")
                        }
                    }
                }
            }

            // Action buttons
            RowLayout
            {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width

                Item { Layout.fillWidth: true }

                Cura.SecondaryButton
                {
                    text: catalog.i18nc("@action:button", "Cancel")
                    onClicked: captureDialog.close()
                }

                Cura.PrimaryButton
                {
                    text: catalog.i18nc("@action:button", "Capture %1 Settings").arg(selectedCount)
                    enabled: selectedCount > 0 && (targetSelector.currentIndex > 0 || newMixinName.text.trim() !== "")
                    onClicked:
                    {
                        var keys = getSelectedKeyList()
                        if (targetSelector.currentIndex === 0)
                        {
                            // Create new mixin
                            manager.captureSettingsToNewMixin(newMixinName.text.trim(), keys)
                        }
                        else
                        {
                            // Add to existing mixin
                            var library = manager.mixinLibrary
                            var mixinId = library[targetSelector.currentIndex - 1].id
                            manager.captureSettingsToMixin(mixinId, keys)
                        }
                        captureDialog.close()
                    }
                }
            }
        }
    }
}
