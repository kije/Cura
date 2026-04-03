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
    id: editorDialog
    title: editIndex >= 0 ?
           catalog.i18nc("@title:window", "Edit Mixed Filament") :
           catalog.i18nc("@title:window", "New Mixed Filament")
    width: 500 * screenScaleFactor
    height: 550 * screenScaleFactor
    minimumWidth: 400 * screenScaleFactor
    minimumHeight: 450 * screenScaleFactor

    UM.I18nCatalog { id: catalog; name: "cura" }

    // Properties exposed to parent
    property int editIndex: -1
    property string filamentName: nameField.text
    property int filamentA: extruderACombo.currentIndex
    property int filamentB: extruderBCombo.currentIndex
    property int proxyExtruder: proxyCombo.currentIndex
    property string outputMode: idexRadio.checked ? "tool_change" : "mixing"
    property int ratioA: ratioASpinner.value
    property int ratioB: ratioBSpinner.value
    property string patternMode: ratioModeRadio.checked ? "ratio" : "custom"
    property string customPattern: customPatternField.text

    property var availableExtruders: manager ? manager.availableExtruders : []

    signal accepted()

    function reset()
    {
        nameField.text = "Mixed Filament"
        extruderACombo.currentIndex = 0
        extruderBCombo.currentIndex = Math.min(1, availableExtruders.length - 1)
        proxyCombo.currentIndex = Math.min(2, availableExtruders.length - 1)
        idexRadio.checked = true
        ratioModeRadio.checked = true
        ratioASpinner.value = 1
        ratioBSpinner.value = 1
        customPatternField.text = ""
    }

    function loadFromData(data)
    {
        nameField.text = data.name || "Mixed Filament"
        extruderACombo.currentIndex = data.filament_a || 0
        extruderBCombo.currentIndex = data.filament_b || 1
        proxyCombo.currentIndex = data.proxy_extruder || 2

        if (data.output_mode === "mixing")
        {
            mixingRadio.checked = true
        }
        else
        {
            idexRadio.checked = true
        }

        if (data.pattern)
        {
            if (data.pattern.mode === "custom")
            {
                customModeRadio.checked = true
                customPatternField.text = data.pattern.custom_pattern || ""
            }
            else
            {
                ratioModeRadio.checked = true
                ratioASpinner.value = data.pattern.ratio_a || 1
                ratioBSpinner.value = data.pattern.ratio_b || 1
            }
        }
    }

    function getPatternPreview()
    {
        var pattern = ""
        if (ratioModeRadio.checked)
        {
            for (var i = 0; i < ratioASpinner.value; i++) pattern += "A"
            for (var j = 0; j < ratioBSpinner.value; j++) pattern += "B"
        }
        else
        {
            pattern = customPatternField.text.toUpperCase().replace(/[^AB12]/g, "")
            pattern = pattern.replace(/1/g, "A").replace(/2/g, "B")
        }
        return pattern || "AB"
    }

    function getRatioPercent()
    {
        var pattern = getPatternPreview()
        var aCount = (pattern.match(/A/g) || []).length
        return Math.round(aCount / pattern.length * 100)
    }

    Item
    {
        anchors.fill: parent

        ScrollView
        {
            anchors.fill: parent
            clip: true

            ColumnLayout
            {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").height

                // Name
                ColumnLayout
                {
                    Layout.fillWidth: true
                    spacing: 4

                    UM.Label { text: catalog.i18nc("@label", "Name:") }
                    Cura.TextField
                    {
                        id: nameField
                        Layout.fillWidth: true
                        text: "Mixed Filament"
                        selectByMouse: true
                    }
                }

                // Extruder selection
                ColumnLayout
                {
                    Layout.fillWidth: true
                    spacing: 4

                    UM.Label { text: catalog.i18nc("@label", "Filament A:") }
                    ComboBox
                    {
                        id: extruderACombo
                        Layout.fillWidth: true
                        model: editorDialog.availableExtruders
                        textRole: "name"

                        delegate: ItemDelegate
                        {
                            width: parent.width
                            contentItem: RowLayout
                            {
                                Rectangle
                                {
                                    width: 16; height: 16; radius: 2
                                    color: modelData.color || "#808080"
                                    border.color: "#333"
                                    border.width: 1
                                }
                                Label { text: modelData.name || "" }
                            }
                        }
                    }

                    UM.Label { text: catalog.i18nc("@label", "Filament B:") }
                    ComboBox
                    {
                        id: extruderBCombo
                        Layout.fillWidth: true
                        model: editorDialog.availableExtruders
                        textRole: "name"
                        currentIndex: Math.min(1, editorDialog.availableExtruders.length - 1)

                        delegate: ItemDelegate
                        {
                            width: parent.width
                            contentItem: RowLayout
                            {
                                Rectangle
                                {
                                    width: 16; height: 16; radius: 2
                                    color: modelData.color || "#808080"
                                    border.color: "#333"
                                    border.width: 1
                                }
                                Label { text: modelData.name || "" }
                            }
                        }
                    }

                    UM.Label { text: catalog.i18nc("@label", "Proxy Extruder Slot:") }
                    ComboBox
                    {
                        id: proxyCombo
                        Layout.fillWidth: true
                        model: editorDialog.availableExtruders
                        textRole: "name"
                        currentIndex: Math.min(2, editorDialog.availableExtruders.length - 1)
                    }
                }

                // Color preview
                Rectangle
                {
                    Layout.fillWidth: true
                    height: 50 * screenScaleFactor
                    radius: UM.Theme.getSize("default_radius").width
                    border.color: UM.Theme.getColor("lining")

                    property string previewColor: {
                        if (!manager || !editorDialog.availableExtruders ||
                            editorDialog.availableExtruders.length <= Math.max(extruderACombo.currentIndex, extruderBCombo.currentIndex))
                            return "#808080"

                        var colorA = editorDialog.availableExtruders[extruderACombo.currentIndex].color || "#808080"
                        var colorB = editorDialog.availableExtruders[extruderBCombo.currentIndex].color || "#808080"
                        var ratio = editorDialog.getRatioPercent() / 100.0
                        return manager.previewBlendColor(colorA, colorB, ratio)
                    }

                    color: previewColor

                    UM.Label
                    {
                        anchors.centerIn: parent
                        text: catalog.i18nc("@label", "Preview: %1").arg(parent.previewColor)
                        color: "#ffffff"
                        font: UM.Theme.getFont("default_bold")
                        style: Text.Outline
                        styleColor: "#000000"
                    }
                }

                // Output mode
                ColumnLayout
                {
                    Layout.fillWidth: true
                    spacing: 4

                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Output Mode:")
                        font: UM.Theme.getFont("default_bold")
                    }

                    RowLayout
                    {
                        spacing: UM.Theme.getSize("default_margin").width

                        RadioButton
                        {
                            id: idexRadio
                            text: catalog.i18nc("@option:radio", "IDEX / Tool Changer")
                            checked: true
                        }
                        RadioButton
                        {
                            id: mixingRadio
                            text: catalog.i18nc("@option:radio", "Mixing Hotend")
                        }
                    }

                    // Mixing hotend sub-options
                    RowLayout
                    {
                        visible: mixingRadio.checked
                        spacing: UM.Theme.getSize("default_margin").width

                        UM.Label { text: catalog.i18nc("@label", "G-code flavor:") }
                        RadioButton
                        {
                            id: marlinRadio
                            text: "Marlin (M163/M164)"
                            checked: true
                        }
                        RadioButton
                        {
                            id: reprapRadio
                            text: "RepRap (M567)"
                        }
                    }
                }

                // Pattern mode
                ColumnLayout
                {
                    Layout.fillWidth: true
                    spacing: 4

                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Pattern Mode:")
                        font: UM.Theme.getFont("default_bold")
                    }

                    RowLayout
                    {
                        spacing: UM.Theme.getSize("default_margin").width
                        RadioButton
                        {
                            id: ratioModeRadio
                            text: catalog.i18nc("@option:radio", "Ratio")
                            checked: true
                        }
                        RadioButton
                        {
                            id: customModeRadio
                            text: catalog.i18nc("@option:radio", "Custom Pattern")
                        }
                    }

                    // Ratio controls
                    GridLayout
                    {
                        visible: ratioModeRadio.checked
                        columns: 2
                        columnSpacing: UM.Theme.getSize("default_margin").width
                        rowSpacing: 4

                        UM.Label { text: catalog.i18nc("@label", "Filament A layers:") }
                        SpinBox
                        {
                            id: ratioASpinner
                            from: 1; to: 20; value: 1
                            editable: true
                        }

                        UM.Label { text: catalog.i18nc("@label", "Filament B layers:") }
                        SpinBox
                        {
                            id: ratioBSpinner
                            from: 1; to: 20; value: 1
                            editable: true
                        }
                    }

                    // Custom pattern input
                    ColumnLayout
                    {
                        visible: customModeRadio.checked
                        Layout.fillWidth: true
                        spacing: 4

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Pattern string (A/B or 1/2, separators: / - _ |):")
                        }
                        Cura.TextField
                        {
                            id: customPatternField
                            Layout.fillWidth: true
                            placeholderText: "e.g. AABB, 11212, A/B/A"
                            selectByMouse: true
                        }
                    }

                    // Pattern preview
                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: patternPreviewCol.implicitHeight + 2 * UM.Theme.getSize("narrow_margin").height
                        color: UM.Theme.getColor("detail_background")
                        border.color: UM.Theme.getColor("lining")
                        radius: UM.Theme.getSize("default_radius").width

                        ColumnLayout
                        {
                            id: patternPreviewCol
                            anchors.fill: parent
                            anchors.margins: UM.Theme.getSize("narrow_margin").width
                            spacing: 4

                            UM.Label
                            {
                                text: catalog.i18nc("@label", "Pattern: %1").arg(editorDialog.getPatternPreview())
                                font: UM.Theme.getFont("fixed")
                            }

                            // Visual pattern blocks
                            Row
                            {
                                spacing: 2

                                Repeater
                                {
                                    // Show 2 cycles of the pattern, up to 16 blocks
                                    model: Math.min(editorDialog.getPatternPreview().length * 2, 16)

                                    Rectangle
                                    {
                                        width: 24 * screenScaleFactor
                                        height: 20 * screenScaleFactor
                                        radius: 2

                                        property string patternStr: editorDialog.getPatternPreview()
                                        property string ch: patternStr.charAt(index % patternStr.length)

                                        color: {
                                            if (!editorDialog.availableExtruders || editorDialog.availableExtruders.length < 2)
                                                return ch === "A" ? "#cc4444" : "#4444cc"
                                            return ch === "A" ?
                                                (editorDialog.availableExtruders[extruderACombo.currentIndex].color || "#cc4444") :
                                                (editorDialog.availableExtruders[extruderBCombo.currentIndex].color || "#4444cc")
                                        }
                                        border.color: "#333"
                                        border.width: 1

                                        UM.Label
                                        {
                                            anchors.centerIn: parent
                                            text: ch
                                            font: UM.Theme.getFont("small")
                                            color: "#ffffff"
                                        }
                                    }
                                }
                            }

                            UM.Label
                            {
                                text: catalog.i18nc("@label", "Ratio: %1% A / %2% B")
                                    .arg(editorDialog.getRatioPercent())
                                    .arg(100 - editorDialog.getRatioPercent())
                                color: UM.Theme.getColor("text_inactive")
                            }

                            // Quick presets
                            Row
                            {
                                spacing: 4
                                visible: ratioModeRadio.checked

                                UM.Label { text: catalog.i18nc("@label", "Presets:"); anchors.verticalCenter: parent.verticalCenter }

                                Repeater
                                {
                                    model: [
                                        { label: "1:1", a: 1, b: 1 },
                                        { label: "2:1", a: 2, b: 1 },
                                        { label: "3:1", a: 3, b: 1 },
                                        { label: "1:2", a: 1, b: 2 },
                                        { label: "1:3", a: 1, b: 3 }
                                    ]

                                    Cura.SecondaryButton
                                    {
                                        text: modelData.label
                                        height: 24 * screenScaleFactor
                                        onClicked:
                                        {
                                            ratioASpinner.value = modelData.a
                                            ratioBSpinner.value = modelData.b
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Buttons
                RowLayout
                {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("default_margin").width

                    Item { Layout.fillWidth: true }

                    Cura.SecondaryButton
                    {
                        text: catalog.i18nc("@action:button", "Cancel")
                        onClicked: editorDialog.close()
                    }

                    Cura.PrimaryButton
                    {
                        text: catalog.i18nc("@action:button", "Apply")
                        onClicked:
                        {
                            editorDialog.accepted()
                            editorDialog.close()
                        }
                    }
                }
            }
        }
    }
}
