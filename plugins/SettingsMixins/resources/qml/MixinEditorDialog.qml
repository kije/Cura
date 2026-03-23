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
    width: 700 * screenScaleFactor
    height: 580 * screenScaleFactor
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

                    property bool isExpr: modelData.isExpression

                    function updateExpressionPreview()
                    {
                        if (isExpr && expressionField.text !== "")
                        {
                            var result = manager.evaluateExpression(expressionField.text)
                            exprPreview.hasError = !result.success
                            exprPreview.text = result.success ? ("= " + result.value) : (result.error ? result.error : "Error")
                        }
                    }

                    // Re-evaluate expression preview when any sibling setting changes
                    Connections
                    {
                        target: manager
                        function onEditingSettingsChanged() { updateExpressionPreview() }
                    }

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

                        // ── Setting label ──
                        UM.Label
                        {
                            text: manager.getSettingLabel(modelData.key)
                            font: UM.Theme.getFont("default")
                            Layout.preferredWidth: parent.width * 0.35
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

                        // ── Literal value field (visible when NOT expression) ──
                        Cura.TextField
                        {
                            id: literalField
                            Layout.fillWidth: true
                            visible: !isExpr
                            text: isExpr ? "" : modelData.value
                            onEditingFinished: manager.setEditingSettingLiteral(modelData.key, text)
                        }

                        // ── Expression field (visible when IS expression) ──
                        Cura.TextField
                        {
                            id: expressionField
                            Layout.fillWidth: true
                            visible: isExpr
                            text: isExpr ? modelData.value : ""
                            font.family: "monospace"

                            onTextChanged:
                            {
                                exprDebounce.restart()
                                // Autocomplete: extract last word
                                var lastWord = text.replace(/.*[\s+\-*\/%(),'"]/,"")
                                if (lastWord.length >= 2)
                                {
                                    exprAutocompleteList.model = manager.searchExpressionCompletions(lastWord)
                                    if (exprAutocompleteList.count > 0)
                                        exprAutocompletePopup.open()
                                    else
                                        exprAutocompletePopup.close()
                                }
                                else
                                {
                                    exprAutocompletePopup.close()
                                }
                            }

                            onEditingFinished:
                            {
                                manager.setEditingSettingExpression(modelData.key, text)
                                exprAutocompletePopup.close()
                            }

                            Keys.onEscapePressed: exprAutocompletePopup.close()

                            // Debounce timer for live preview
                            Timer
                            {
                                id: exprDebounce
                                interval: 300
                                repeat: false
                                onTriggered: updateExpressionPreview()
                            }

                            // Autocomplete popup
                            Popup
                            {
                                id: exprAutocompletePopup
                                y: expressionField.height + 2
                                x: 0
                                width: expressionField.width
                                height: Math.min(150, exprAutocompleteList.count * 28 + 8)
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
                                    id: exprAutocompleteList
                                    anchors.fill: parent
                                    clip: true

                                    delegate: Rectangle
                                    {
                                        width: exprAutocompleteList.width
                                        height: 26
                                        color: acMouseArea.containsMouse ? UM.Theme.getColor("action_button_hovered") : "transparent"
                                        radius: 2

                                        property bool isBuiltin: modelData.type !== "setting"

                                        MouseArea
                                        {
                                            id: acMouseArea
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            onClicked:
                                            {
                                                // Replace the last partial word with the selected key
                                                var curText = expressionField.text
                                                // For builtins containing dots, also replace the prefix before dot
                                                var pattern = isBuiltin
                                                    ? /[a-zA-Z_][a-zA-Z0-9_.]*$/
                                                    : /[a-zA-Z_][a-zA-Z0-9_]*$/
                                                var replaced = curText.replace(pattern, modelData.key)
                                                expressionField.text = replaced
                                                exprAutocompletePopup.close()
                                                expressionField.forceActiveFocus()
                                            }
                                        }

                                        RowLayout
                                        {
                                            anchors.fill: parent
                                            anchors.margins: 4
                                            spacing: 6

                                            // Type badge for builtins
                                            Rectangle
                                            {
                                                visible: isBuiltin
                                                width: 18; height: 14
                                                radius: 2
                                                color: UM.Theme.getColor("action_button_active")
                                                UM.Label
                                                {
                                                    anchors.centerIn: parent
                                                    text: modelData.type === "function" ? "fn" : (modelData.type === "operator" ? "op" : "c")
                                                    font.pointSize: UM.Theme.getFont("small").pointSize - 1
                                                    color: UM.Theme.getColor("action_button_active_text")
                                                }
                                            }

                                            UM.Label
                                            {
                                                text: modelData.key
                                                font.family: "monospace"
                                                font.pointSize: UM.Theme.getFont("small").pointSize
                                                Layout.fillWidth: true
                                                elide: Text.ElideRight
                                            }

                                            UM.Label
                                            {
                                                text: modelData.label
                                                font: UM.Theme.getFont("small")
                                                color: UM.Theme.getColor("text_detail")
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // ── f(x) toggle button ──
                        Rectangle
                        {
                            id: fxToggle
                            width: 32; height: 24
                            radius: UM.Theme.getSize("default_radius").width
                            color: isExpr
                                ? UM.Theme.getColor("action_button_active")
                                : (fxMouse.containsMouse ? UM.Theme.getColor("action_button_hovered") : UM.Theme.getColor("action_button"))
                            border.width: 1
                            border.color: isExpr
                                ? UM.Theme.getColor("action_button_active_border")
                                : UM.Theme.getColor("action_button_border")

                            UM.Label
                            {
                                anchors.centerIn: parent
                                text: "f(x)"
                                font: UM.Theme.getFont("small")
                                color: isExpr ? UM.Theme.getColor("action_button_active_text") : UM.Theme.getColor("action_button_text")
                            }

                            MouseArea
                            {
                                id: fxMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: manager.toggleSettingExpression(modelData.key)
                            }

                            ToolTip.text: isExpr
                                ? catalog.i18nc("@info:tooltip", "Switch to literal value")
                                : catalog.i18nc("@info:tooltip", "Switch to expression (reference other settings)")
                            ToolTip.visible: fxMouse.containsMouse
                            ToolTip.delay: 500
                        }

                        // ── Unit / Live preview ──
                        UM.Label
                        {
                            id: exprPreview
                            property bool hasError: false
                            Layout.preferredWidth: 50
                            font: UM.Theme.getFont("default")
                            color: isExpr
                                ? (hasError ? UM.Theme.getColor("error") : UM.Theme.getColor("text_detail"))
                                : UM.Theme.getColor("text_detail")
                            text: isExpr ? "" : manager.getSettingUnit(modelData.key)
                            visible: manager.getSettingUnit(modelData.key) !== "" || isExpr
                            elide: Text.ElideRight

                            Component.onCompleted: updateExpressionPreview()
                        }

                        // ── Remove button ──
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
