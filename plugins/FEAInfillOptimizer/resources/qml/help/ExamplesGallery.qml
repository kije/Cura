// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.

// Browsable gallery of example FEA setups.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog
{
    id: examplesDialog
    title: catalog.i18nc("@title:window", "Example Setups - FEA Infill Optimizer")
    width: 700 * screenScaleFactor
    height: 500 * screenScaleFactor
    minimumWidth: 500 * screenScaleFactor
    minimumHeight: 350 * screenScaleFactor

    property var examples: []
    property string searchQuery: ""
    property string selectedCategory: "all"
    property string selectedExampleId: ""

    UM.I18nCatalog { id: catalog; name: "cura" }

    // Load examples from JSON
    Component.onCompleted:
    {
        var xhr = new XMLHttpRequest()
        var url = Qt.resolvedUrl("../../help/examples.json")
        xhr.open("GET", url, false)
        xhr.send()
        if (xhr.status === 200 || xhr.status === 0)
        {
            try
            {
                var data = JSON.parse(xhr.responseText)
                examples = data.examples || []
            }
            catch (e)
            {
                console.warn("ExamplesGallery: Failed to parse examples.json:", e)
            }
        }
    }

    // Filtered examples list
    property var filteredExamples:
    {
        var result = []
        var query = searchQuery.toLowerCase()
        for (var i = 0; i < examples.length; i++)
        {
            var ex = examples[i]
            // Category filter
            if (selectedCategory !== "all")
            {
                var catMatch = false
                for (var c = 0; c < ex.category.length; c++)
                {
                    if (ex.category[c].toLowerCase() === selectedCategory)
                    {
                        catMatch = true
                        break
                    }
                }
                if (!catMatch) continue
            }
            // Search filter
            if (query !== "")
            {
                var text = (ex.title + " " + ex.subtitle + " " + ex.scenario).toLowerCase()
                if (text.indexOf(query) < 0) continue
            }
            result.push(ex)
        }
        return result
    }

    // Find selected example object
    property var selectedExample:
    {
        if (selectedExampleId === "") return null
        for (var i = 0; i < examples.length; i++)
        {
            if (examples[i].id === selectedExampleId) return examples[i]
        }
        return null
    }

    // ── Gallery View ──
    Item
    {
        anchors.fill: parent
        visible: examplesDialog.selectedExampleId === ""

        ColumnLayout
        {
            anchors.fill: parent
            anchors.margins: UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").height

            // Search + filter bar
            RowLayout
            {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width

                TextField
                {
                    Layout.fillWidth: true
                    placeholderText: catalog.i18nc("@placeholder", "Search examples...")
                    onTextChanged: examplesDialog.searchQuery = text
                }

                ComboBox
                {
                    model: [
                        catalog.i18nc("@option", "All"),
                        catalog.i18nc("@option", "Bracket"),
                        catalog.i18nc("@option", "Beam"),
                        catalog.i18nc("@option", "Enclosure"),
                        catalog.i18nc("@option", "Handle")
                    ]
                    onCurrentTextChanged: examplesDialog.selectedCategory = currentText.toLowerCase()
                }
            }

            // Card grid
            GridView
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                cellWidth: (width - UM.Theme.getSize("default_margin").width) / 2
                cellHeight: 200 * screenScaleFactor
                clip: true

                model: examplesDialog.filteredExamples

                delegate: Item
                {
                    width: GridView.view.cellWidth
                    height: GridView.view.cellHeight

                    Rectangle
                    {
                        anchors.fill: parent
                        anchors.margins: 4
                        color: UM.Theme.getColor("main_background")
                        border.color: cardMouse.containsMouse
                            ? UM.Theme.getColor("primary")
                            : UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        radius: UM.Theme.getSize("default_radius").width

                        ColumnLayout
                        {
                            anchors.fill: parent
                            anchors.margins: UM.Theme.getSize("default_margin").width
                            spacing: 4

                            Image
                            {
                                source: modelData.image
                                    ? Qt.resolvedUrl("../../" + modelData.image) : ""
                                Layout.fillWidth: true
                                Layout.preferredHeight: 80
                                fillMode: Image.PreserveAspectFit
                                sourceSize.width: width

                                Accessible.description: modelData.image_alt || ""
                            }

                            UM.Label
                            {
                                text: modelData.title
                                font: UM.Theme.getFont("medium_bold")
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }

                            UM.Label
                            {
                                text: modelData.subtitle
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_medium")
                                Layout.fillWidth: true
                            }

                            RowLayout
                            {
                                Layout.fillWidth: true
                                spacing: 4

                                Rectangle
                                {
                                    width: diffLabel.implicitWidth + 12
                                    height: diffLabel.implicitHeight + 4
                                    radius: 4
                                    color: modelData.difficulty === "Beginner"
                                        ? "#1522AA44" : "#154488DD"

                                    UM.Label
                                    {
                                        id: diffLabel
                                        anchors.centerIn: parent
                                        text: modelData.difficulty
                                        font: UM.Theme.getFont("small")
                                    }
                                }

                                Item { Layout.fillWidth: true }

                                UM.Label
                                {
                                    text: catalog.i18nc("@action", "View Setup >")
                                    color: UM.Theme.getColor("primary")
                                    font: UM.Theme.getFont("small")
                                }
                            }
                        }

                        MouseArea
                        {
                            id: cardMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: examplesDialog.selectedExampleId = modelData.id
                        }

                        Accessible.role: Accessible.Button
                        Accessible.name: modelData.title + ", " + modelData.difficulty
                    }
                }
            }
        }
    }

    // ── Detail View ──
    Item
    {
        anchors.fill: parent
        visible: examplesDialog.selectedExampleId !== ""

        ScrollView
        {
            anchors.fill: parent
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout
            {
                width: parent.parent.width
                spacing: UM.Theme.getSize("default_margin").height

                // Back button + title
                RowLayout
                {
                    Layout.fillWidth: true
                    Layout.margins: UM.Theme.getSize("default_margin").width
                    spacing: UM.Theme.getSize("default_margin").width

                    UM.Label
                    {
                        text: catalog.i18nc("@action", "< Back")
                        color: UM.Theme.getColor("primary")
                        font: UM.Theme.getFont("default")

                        MouseArea
                        {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: examplesDialog.selectedExampleId = ""
                        }
                    }

                    UM.Label
                    {
                        text: examplesDialog.selectedExample ? examplesDialog.selectedExample.title : ""
                        font: UM.Theme.getFont("large_bold")
                        Layout.fillWidth: true
                    }
                }

                // Example SVG
                Image
                {
                    visible: examplesDialog.selectedExample && examplesDialog.selectedExample.image
                    source: (examplesDialog.selectedExample && examplesDialog.selectedExample.image)
                        ? Qt.resolvedUrl("../../" + examplesDialog.selectedExample.image) : ""
                    Layout.preferredWidth: 300
                    Layout.preferredHeight: 180
                    Layout.alignment: Qt.AlignHCenter
                    fillMode: Image.PreserveAspectFit
                    sourceSize.width: 300
                }

                // Scenario description
                UM.Label
                {
                    visible: examplesDialog.selectedExample !== null
                    text: examplesDialog.selectedExample ? examplesDialog.selectedExample.scenario : ""
                    font: UM.Theme.getFont("default")
                    color: UM.Theme.getColor("text")
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.leftMargin: UM.Theme.getSize("default_margin").width
                    Layout.rightMargin: UM.Theme.getSize("default_margin").width
                }

                // Supports section
                Rectangle
                {
                    visible: examplesDialog.selectedExample && examplesDialog.selectedExample.supports
                    Layout.fillWidth: true
                    Layout.leftMargin: UM.Theme.getSize("default_margin").width
                    Layout.rightMargin: UM.Theme.getSize("default_margin").width
                    height: visible ? supportCol.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                    color: "transparent"
                    border.color: "#22AA44"
                    border.width: 2
                    radius: UM.Theme.getSize("default_radius").width

                    // Green left accent bar
                    Rectangle
                    {
                        width: 4; height: parent.height
                        color: "#22AA44"
                        radius: 2
                    }

                    ColumnLayout
                    {
                        id: supportCol
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width
                        anchors.leftMargin: UM.Theme.getSize("default_margin").width + 8
                        spacing: 4

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "FIXED SUPPORTS")
                            font: UM.Theme.getFont("small_bold")
                            color: "#22AA44"
                        }
                        UM.Label
                        {
                            text: examplesDialog.selectedExample
                                ? examplesDialog.selectedExample.supports.description : ""
                            font: UM.Theme.getFont("small")
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        UM.Label
                        {
                            text: examplesDialog.selectedExample
                                ? "Selection mode: " + examplesDialog.selectedExample.supports.selection_mode : ""
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_medium")
                        }
                        UM.Label
                        {
                            text: examplesDialog.selectedExample
                                ? examplesDialog.selectedExample.supports.instructions : ""
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_medium")
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }

                // Forces section
                Repeater
                {
                    model: examplesDialog.selectedExample ? examplesDialog.selectedExample.forces : []

                    Rectangle
                    {
                        Layout.fillWidth: true
                        Layout.leftMargin: UM.Theme.getSize("default_margin").width
                        Layout.rightMargin: UM.Theme.getSize("default_margin").width
                        height: forceCol.implicitHeight + UM.Theme.getSize("default_margin").height
                        color: "transparent"
                        border.color: "#DD4444"
                        border.width: 2
                        radius: UM.Theme.getSize("default_radius").width

                        Rectangle
                        {
                            width: 4; height: parent.height
                            color: "#DD4444"
                            radius: 2
                        }

                        ColumnLayout
                        {
                            id: forceCol
                            anchors.fill: parent
                            anchors.margins: UM.Theme.getSize("default_margin").width
                            anchors.leftMargin: UM.Theme.getSize("default_margin").width + 8
                            spacing: 4

                            UM.Label
                            {
                                text: catalog.i18nc("@label", "APPLIED FORCE") + " - " + modelData.magnitude_N + " N " + modelData.direction
                                font: UM.Theme.getFont("small_bold")
                                color: "#DD4444"
                            }
                            UM.Label
                            {
                                text: modelData.description
                                font: UM.Theme.getFont("small")
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            UM.Label
                            {
                                text: "Where: " + modelData.face + " | Mode: " + modelData.selection_mode
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_medium")
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            UM.Label
                            {
                                visible: modelData.estimation !== undefined
                                text: "Estimation: " + (modelData.estimation || "")
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_medium")
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                    }
                }

                // Torques section
                Repeater
                {
                    model: examplesDialog.selectedExample ? examplesDialog.selectedExample.torques : []

                    Rectangle
                    {
                        Layout.fillWidth: true
                        Layout.leftMargin: UM.Theme.getSize("default_margin").width
                        Layout.rightMargin: UM.Theme.getSize("default_margin").width
                        height: torqueCol.implicitHeight + UM.Theme.getSize("default_margin").height
                        color: "transparent"
                        border.color: "#4488DD"
                        border.width: 2
                        radius: UM.Theme.getSize("default_radius").width

                        Rectangle
                        {
                            width: 4; height: parent.height
                            color: "#4488DD"
                            radius: 2
                        }

                        ColumnLayout
                        {
                            id: torqueCol
                            anchors.fill: parent
                            anchors.margins: UM.Theme.getSize("default_margin").width
                            anchors.leftMargin: UM.Theme.getSize("default_margin").width + 8
                            spacing: 4

                            UM.Label
                            {
                                text: catalog.i18nc("@label", "APPLIED TORQUE") + " - " + modelData.magnitude_Nm + " Nm"
                                font: UM.Theme.getFont("small_bold")
                                color: "#4488DD"
                            }
                            UM.Label
                            {
                                text: modelData.description
                                font: UM.Theme.getFont("small")
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            UM.Label
                            {
                                text: "Axis: " + modelData.axis + " | Mode: " + modelData.selection_mode
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_medium")
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                    }
                }

                // Key insights
                Rectangle
                {
                    visible: examplesDialog.selectedExample && examplesDialog.selectedExample.key_insights.length > 0
                    Layout.fillWidth: true
                    Layout.leftMargin: UM.Theme.getSize("default_margin").width
                    Layout.rightMargin: UM.Theme.getSize("default_margin").width
                    height: visible ? insightsCol.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                    color: UM.Theme.getColor("detail_background")
                    radius: UM.Theme.getSize("default_radius").width

                    ColumnLayout
                    {
                        id: insightsCol
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width
                        spacing: 4

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "KEY INSIGHTS")
                            font: UM.Theme.getFont("small_bold")
                            color: UM.Theme.getColor("text")
                        }

                        Repeater
                        {
                            model: examplesDialog.selectedExample
                                ? examplesDialog.selectedExample.key_insights : []

                            UM.Label
                            {
                                text: "\u2022 " + modelData
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text")
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                    }
                }

                // Expected stress
                UM.Label
                {
                    visible: examplesDialog.selectedExample !== null
                    text: examplesDialog.selectedExample
                        ? catalog.i18nc("@label", "Expected stress pattern: ") + examplesDialog.selectedExample.expected_stress : ""
                    font: UM.Theme.getFont("small")
                    color: UM.Theme.getColor("text_medium")
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Layout.leftMargin: UM.Theme.getSize("default_margin").width
                    Layout.rightMargin: UM.Theme.getSize("default_margin").width
                }

                // Recommended settings
                Rectangle
                {
                    visible: examplesDialog.selectedExample !== null
                    Layout.fillWidth: true
                    Layout.leftMargin: UM.Theme.getSize("default_margin").width
                    Layout.rightMargin: UM.Theme.getSize("default_margin").width
                    height: visible ? settingsRow.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                    color: UM.Theme.getColor("detail_background")
                    radius: UM.Theme.getSize("default_radius").width

                    RowLayout
                    {
                        id: settingsRow
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width
                        spacing: UM.Theme.getSize("default_margin").width

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "RECOMMENDED")
                            font: UM.Theme.getFont("small_bold")
                        }

                        UM.Label
                        {
                            text: examplesDialog.selectedExample
                                ? examplesDialog.selectedExample.recommended_settings.material
                                    + " | " + examplesDialog.selectedExample.recommended_settings.safety_factor
                                    + " | " + examplesDialog.selectedExample.recommended_settings.mesh_quality
                                    + " | " + examplesDialog.selectedExample.recommended_settings.infill_pattern
                                : ""
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text")
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }

                // Spacer at bottom
                Item { height: UM.Theme.getSize("default_margin").height }
            }
        }
    }
}
