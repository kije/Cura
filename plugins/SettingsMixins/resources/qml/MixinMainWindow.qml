// Copyright (c) 2024 Community
// Settings Mixins Plugin - Main Management Window

import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3
import QtQuick.Window 2.2
import QtQuick.Dialogs

import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog
{
    id: mainWindow
    title: catalog.i18nc("@title:window", "Settings Mixins")
    width: 800 * screenScaleFactor
    height: 600 * screenScaleFactor
    minimumWidth: 640 * screenScaleFactor
    minimumHeight: 480 * screenScaleFactor
    backgroundColor: UM.Theme.getColor("main_background")

    property real screenScaleFactor: UM.Theme.getSize("default_margin").width / 10

    UM.I18nCatalog { id: catalog; name: "cura" }

    MixinEditorDialog
    {
        id: editorDialog
    }

    Item
    {
        anchors.fill: parent

        ColumnLayout
        {
            anchors.fill: parent
            spacing: UM.Theme.getSize("default_margin").height

            // ── Header ────────────────────────────────────────────────
            RowLayout
            {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width

                UM.Label
                {
                    text: catalog.i18nc("@label", "Settings Mixins")
                    font: UM.Theme.getFont("large_bold")
                    Layout.fillWidth: true
                }

                UM.Label
                {
                    text: catalog.i18nc("@label", "Scope:")
                    font: UM.Theme.getFont("default")
                }

                ComboBox
                {
                    id: scopeSelector
                    model: [catalog.i18nc("@label", "Global")]
                    Layout.preferredWidth: 150 * screenScaleFactor
                }
            }

            // ── Main Content: Two Panels ──────────────────────────────
            RowLayout
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: UM.Theme.getSize("default_margin").width

                // ── Left Panel: Active Mixins ─────────────────────────
                Rectangle
                {
                    Layout.fillHeight: true
                    Layout.preferredWidth: parent.width * 0.55
                    color: UM.Theme.getColor("main_background")
                    border.width: UM.Theme.getSize("default_lining").width
                    border.color: UM.Theme.getColor("lining")
                    radius: UM.Theme.getSize("default_radius").width

                    ColumnLayout
                    {
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width
                        spacing: UM.Theme.getSize("narrow_margin").height

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Active Mixins (last = highest priority)")
                            font: UM.Theme.getFont("medium_bold")
                        }

                        // Profile info bar
                        Rectangle
                        {
                            Layout.fillWidth: true
                            height: profileInfoRow.height + 10
                            color: manager.hasCustomProfile ? "#E8F5E9" : "#FFF3CD"
                            border.color: manager.hasCustomProfile ? "#C8E6C9" : "#FFEAA7"
                            border.width: 1
                            radius: UM.Theme.getSize("default_radius").width

                            RowLayout
                            {
                                id: profileInfoRow
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 8
                                spacing: 8

                                UM.ColorImage
                                {
                                    source: UM.Theme.getIcon(manager.hasCustomProfile ? "CheckCircle" : "Information")
                                    width: 16; height: 16
                                    color: manager.hasCustomProfile ? "#2E7D32" : "#856404"
                                }

                                UM.Label
                                {
                                    text: manager.hasCustomProfile
                                        ? catalog.i18nc("@info", "Profile: %1").arg(manager.currentProfileName)
                                        : catalog.i18nc("@info", "Built-in profile. Adding a mixin will create a custom copy.")
                                    font: UM.Theme.getFont("default")
                                    color: manager.hasCustomProfile ? "#2E7D32" : "#856404"
                                    Layout.fillWidth: true
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }

                        UM.Label
                        {
                            text: catalog.i18nc("@info", "Use arrows to reorder. Later mixins override earlier ones on conflict.")
                            font: UM.Theme.getFont("default")
                            color: UM.Theme.getColor("text_detail")
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }

                        ScrollView
                        {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true

                            ListView
                            {
                                id: activeMixinsList
                                model: manager.activeMixins
                                spacing: 2

                                delegate: Rectangle
                                {
                                    width: activeMixinsList.width
                                    height: mixinRow.height + 12
                                    color: mouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"
                                    radius: UM.Theme.getSize("default_radius").width
                                    border.width: 1
                                    border.color: UM.Theme.getColor("lining")

                                    MouseArea
                                    {
                                        id: mouseArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                    }

                                    RowLayout
                                    {
                                        id: mixinRow
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        anchors.margins: 8
                                        spacing: 8

                                        UM.Label
                                        {
                                            text: (index + 1) + "."
                                            font: UM.Theme.getFont("default")
                                            color: UM.Theme.getColor("text_detail")
                                            Layout.preferredWidth: 24
                                        }

                                        Rectangle
                                        {
                                            width: 12; height: 12; radius: 6
                                            color: modelData.color
                                            Layout.alignment: Qt.AlignVCenter
                                        }

                                        ColumnLayout
                                        {
                                            Layout.fillWidth: true
                                            spacing: 2

                                            UM.Label
                                            {
                                                text: modelData.name
                                                font: UM.Theme.getFont("default_bold")
                                                Layout.fillWidth: true
                                                elide: Text.ElideRight
                                            }

                                            UM.Label
                                            {
                                                text: modelData.settingSummary
                                                font: UM.Theme.getFont("default")
                                                color: UM.Theme.getColor("text_detail")
                                                Layout.fillWidth: true
                                                elide: Text.ElideRight
                                            }
                                        }

                                        UM.SimpleButton
                                        {
                                            width: 20; height: 20
                                            iconSource: UM.Theme.getIcon("ChevronSingleUp")
                                            color: hovered ? UM.Theme.getColor("small_button_text_hover") : UM.Theme.getColor("small_button_text")
                                            enabled: index > 0
                                            opacity: enabled ? 1.0 : 0.3
                                            onClicked: manager.moveActiveMixin(index, index - 1)
                                        }

                                        UM.SimpleButton
                                        {
                                            width: 20; height: 20
                                            iconSource: UM.Theme.getIcon("ChevronSingleDown")
                                            color: hovered ? UM.Theme.getColor("small_button_text_hover") : UM.Theme.getColor("small_button_text")
                                            enabled: index < activeMixinsList.count - 1
                                            opacity: enabled ? 1.0 : 0.3
                                            onClicked: manager.moveActiveMixin(index, index + 1)
                                        }

                                        UM.SimpleButton
                                        {
                                            width: 20; height: 20
                                            iconSource: UM.Theme.getIcon("Pen")
                                            color: hovered ? UM.Theme.getColor("small_button_text_hover") : UM.Theme.getColor("small_button_text")
                                            onClicked:
                                            {
                                                manager.startEditMixin(modelData.id)
                                                editorDialog.open()
                                            }
                                        }

                                        UM.SimpleButton
                                        {
                                            width: 20; height: 20
                                            iconSource: UM.Theme.getIcon("Cancel")
                                            color: hovered ? UM.Theme.getColor("error") : UM.Theme.getColor("small_button_text")
                                            onClicked: manager.removeMixinFromActive(modelData.id)
                                        }
                                    }
                                }

                                UM.Label
                                {
                                    visible: activeMixinsList.count === 0
                                    anchors.centerIn: parent
                                    text: catalog.i18nc("@info", "No active mixins.\nAdd mixins from the library on the right.")
                                    horizontalAlignment: Text.AlignHCenter
                                    color: UM.Theme.getColor("text_detail")
                                }
                            }
                        }

                        // Conflict summary bar
                        Rectangle
                        {
                            Layout.fillWidth: true
                            height: conflictRow.height + 12
                            visible: manager.conflictCount > 0
                            color: "#FFF3CD"
                            border.color: "#FFEAA7"
                            border.width: 1
                            radius: UM.Theme.getSize("default_radius").width

                            RowLayout
                            {
                                id: conflictRow
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 8
                                spacing: 8

                                UM.ColorImage
                                {
                                    source: UM.Theme.getIcon("Warning")
                                    width: 16; height: 16
                                    color: "#856404"
                                }

                                UM.Label
                                {
                                    text: manager.conflictCount + " conflict" + (manager.conflictCount !== 1 ? "s" : "") + " between mixins"
                                    font: UM.Theme.getFont("default")
                                    color: "#856404"
                                    Layout.fillWidth: true
                                }

                                Cura.SecondaryButton
                                {
                                    text: catalog.i18nc("@action:button", "Details")
                                    height: 28
                                    onClicked: conflictDialog.open()
                                }
                            }
                        }
                    }
                }

                // ── Right Panel: Mixin Library ────────────────────────
                Rectangle
                {
                    Layout.fillHeight: true
                    Layout.fillWidth: true
                    color: UM.Theme.getColor("main_background")
                    border.width: UM.Theme.getSize("default_lining").width
                    border.color: UM.Theme.getColor("lining")
                    radius: UM.Theme.getSize("default_radius").width

                    ColumnLayout
                    {
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width
                        spacing: UM.Theme.getSize("narrow_margin").height

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Mixin Library")
                            font: UM.Theme.getFont("medium_bold")
                        }

                        Cura.TextField
                        {
                            id: librarySearch
                            Layout.fillWidth: true
                            placeholderText: catalog.i18nc("@text:placeholder", "Search mixins...")
                        }

                        ScrollView
                        {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true

                            ListView
                            {
                                id: libraryList
                                model: filteredLibrary()
                                spacing: 2

                                function filteredLibrary()
                                {
                                    var allMixins = manager.mixinLibrary
                                    var query = librarySearch.text.toLowerCase()
                                    if (query === "") return allMixins

                                    var result = []
                                    for (var i = 0; i < allMixins.length; i++)
                                    {
                                        var m = allMixins[i]
                                        if (m.name.toLowerCase().indexOf(query) !== -1 ||
                                            m.description.toLowerCase().indexOf(query) !== -1 ||
                                            m.tags.toLowerCase().indexOf(query) !== -1)
                                        {
                                            result.push(m)
                                        }
                                    }
                                    return result
                                }

                                Connections
                                {
                                    target: librarySearch
                                    function onTextChanged() { libraryList.model = libraryList.filteredLibrary() }
                                }

                                Connections
                                {
                                    target: manager
                                    function onMixinLibraryChanged() { libraryList.model = libraryList.filteredLibrary() }
                                    function onProfileStateChanged() { libraryList.model = libraryList.filteredLibrary() }
                                }

                                delegate: Rectangle
                                {
                                    width: libraryList.width
                                    height: libRow.height + 12
                                    color: libMouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"
                                    radius: UM.Theme.getSize("default_radius").width
                                    border.width: 1
                                    border.color: UM.Theme.getColor("lining")

                                    MouseArea
                                    {
                                        id: libMouseArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                    }

                                    RowLayout
                                    {
                                        id: libRow
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

                                        ColumnLayout
                                        {
                                            Layout.fillWidth: true
                                            spacing: 2

                                            UM.Label
                                            {
                                                text: modelData.name
                                                font: UM.Theme.getFont("default_bold")
                                                Layout.fillWidth: true
                                                elide: Text.ElideRight
                                            }

                                            UM.Label
                                            {
                                                text: modelData.description || (modelData.settingCount + " settings")
                                                font: UM.Theme.getFont("default")
                                                color: UM.Theme.getColor("text_detail")
                                                Layout.fillWidth: true
                                                elide: Text.ElideRight
                                            }
                                        }

                                        // Scope badge
                                        Rectangle
                                        {
                                            width: scopeBadgeLabel.width + 12; height: 20; radius: 10
                                            color: modelData.scope === "global" ? "#E8F5E9" : "#E3F2FD"

                                            UM.Label
                                            {
                                                id: scopeBadgeLabel
                                                anchors.centerIn: parent
                                                text: modelData.scope === "global" ? "G" : "E"
                                                font: UM.Theme.getFont("small")
                                                color: modelData.scope === "global" ? "#2E7D32" : "#1565C0"
                                            }
                                        }

                                        Cura.SecondaryButton
                                        {
                                            text: catalog.i18nc("@action:button", "Add")
                                            height: 28
                                            enabled:
                                            {
                                                var active = manager.activeMixins
                                                for (var i = 0; i < active.length; i++)
                                                {
                                                    if (active[i].id === modelData.id) return false
                                                }
                                                return true
                                            }
                                            onClicked: manager.addMixinToActive(modelData.id)
                                        }

                                        UM.SimpleButton
                                        {
                                            width: 20; height: 20
                                            iconSource: UM.Theme.getIcon("Pen")
                                            color: hovered ? UM.Theme.getColor("small_button_text_hover") : UM.Theme.getColor("small_button_text")
                                            onClicked:
                                            {
                                                manager.startEditMixin(modelData.id)
                                                editorDialog.open()
                                            }
                                        }

                                        UM.SimpleButton
                                        {
                                            width: 20; height: 20
                                            iconSource: UM.Theme.getIcon("Trash")
                                            color: hovered ? UM.Theme.getColor("error") : UM.Theme.getColor("small_button_text")
                                            onClicked: deleteConfirmDialog.mixinToDelete = modelData.id
                                        }
                                    }
                                }

                                UM.Label
                                {
                                    visible: libraryList.count === 0
                                    anchors.centerIn: parent
                                    text: catalog.i18nc("@info", "No mixins yet.\nCreate your first mixin!")
                                    horizontalAlignment: Text.AlignHCenter
                                    color: UM.Theme.getColor("text_detail")
                                }
                            }
                        }

                        RowLayout
                        {
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").width

                            Cura.PrimaryButton
                            {
                                text: catalog.i18nc("@action:button", "Create New Mixin")
                                onClicked:
                                {
                                    manager.startNewMixin()
                                    editorDialog.open()
                                }
                            }

                            Cura.SecondaryButton
                            {
                                text: catalog.i18nc("@action:button", "Capture from Profile...")
                                onClicked: manager.showCaptureDialog()
                            }

                            Cura.SecondaryButton
                            {
                                text: catalog.i18nc("@action:button", "Import...")
                                onClicked: importDialog.open()
                            }
                        }
                    }
                }
            }
        }
    }

    // ── Conflict Details Dialog ────────────────────────────────────────
    Dialog
    {
        id: conflictDialog
        title: catalog.i18nc("@title:window", "Mixin Conflicts")
        width: 500 * screenScaleFactor
        height: 400 * screenScaleFactor
        anchors.centerIn: parent
        standardButtons: Dialog.Ok

        ColumnLayout
        {
            anchors.fill: parent
            spacing: UM.Theme.getSize("default_margin").height

            UM.Label
            {
                text: catalog.i18nc("@info", "Settings defined by multiple active mixins.\nThe last mixin in the list (highest priority) wins.")
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text_detail")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            ScrollView
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                ListView
                {
                    id: conflictList
                    model: manager.conflicts
                    spacing: 8

                    delegate: Rectangle
                    {
                        width: conflictList.width
                        height: conflictContent.height + 16
                        border.width: 1
                        border.color: UM.Theme.getColor("lining")
                        radius: UM.Theme.getSize("default_radius").width
                        color: UM.Theme.getColor("main_background")

                        ColumnLayout
                        {
                            id: conflictContent
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 8
                            spacing: 4

                            UM.Label
                            {
                                text: manager.getSettingLabel(modelData.key) + " (" + modelData.key + ")"
                                font: UM.Theme.getFont("default_bold")
                                Layout.fillWidth: true
                            }

                            Repeater
                            {
                                model: modelData.sources

                                RowLayout
                                {
                                    spacing: 8
                                    Layout.fillWidth: true

                                    Rectangle
                                    {
                                        width: 10; height: 10; radius: 5
                                        color: modelData.mixin_color
                                    }

                                    UM.Label
                                    {
                                        text: modelData.mixin_name + ": " + modelData.value
                                        font: UM.Theme.getFont("default")
                                        color: modelData.is_active ? UM.Theme.getColor("text") : UM.Theme.getColor("text_detail")
                                        Layout.fillWidth: true
                                    }

                                    UM.Label
                                    {
                                        text: modelData.is_active ? "active" : "overridden"
                                        font: UM.Theme.getFont("small")
                                        color: modelData.is_active ? "#2E7D32" : "#B71C1C"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ── Delete Confirmation ───────────────────────────────────────────
    Dialog
    {
        id: deleteConfirmDialog
        title: catalog.i18nc("@title:window", "Delete Mixin")
        anchors.centerIn: parent
        standardButtons: Dialog.Yes | Dialog.No

        property string mixinToDelete: ""
        onMixinToDeleteChanged: if (mixinToDelete !== "") open()

        UM.Label
        {
            text: catalog.i18nc("@info", "Are you sure you want to delete this mixin?\nThis cannot be undone.")
        }

        onAccepted:
        {
            if (mixinToDelete !== "")
            {
                manager.deleteMixin(mixinToDelete)
                mixinToDelete = ""
            }
        }
        onRejected: mixinToDelete = ""
    }

    // ── Import File Dialog ────────────────────────────────────────────
    FileDialog
    {
        id: importDialog
        title: catalog.i18nc("@title:window", "Import Mixin")
        nameFilters: ["Mixin files (*.json *.cura_mixin)", "All files (*)"]
        fileMode: FileDialog.OpenFile
        onAccepted: manager.importMixinFromPath(selectedFile.toString())
    }
}
