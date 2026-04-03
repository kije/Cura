// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item
{
    id: bcPanel

    // Expose the controller property bag so bindings resolve correctly
    property var toolProperties: UM.Controller.properties

    // Convenience aliases from tool property bag
    readonly property string currentMode:     toolProperties.getValue("Mode")          ?? "fixed"
    readonly property string selectionSummary: toolProperties.getValue("SelectionSummary") ?? ""
    readonly property real   forceX:          toolProperties.getValue("ForceX")       ?? 0.0
    readonly property real   forceY:          toolProperties.getValue("ForceY")       ?? 0.0
    readonly property real   forceZ:          toolProperties.getValue("ForceZ")       ?? 0.0

    implicitWidth: columnLayout.implicitWidth
    implicitHeight: columnLayout.implicitHeight

    UM.I18nCatalog { id: catalog; name: "cura" }

    ColumnLayout
    {
        id: columnLayout
        anchors.fill: parent
        spacing: UM.Theme.getSize("default_margin").height

        // ── Mode selector ────────────────────────────────────────────────────
        UM.Label
        {
            text: catalog.i18nc("@label", "What are you marking?")
            font: UM.Theme.getFont("medium_bold")
        }

        RowLayout
        {
            spacing: UM.Theme.getSize("default_margin").width / 2

            UM.ToolbarButton
            {
                id: fixedModeButton
                checkable: true
                checked: bcPanel.currentMode === "fixed"
                text: catalog.i18nc("@action:button", "Support / Mount")
                toolItem: UM.ColorImage
                {
                    source: UM.Theme.getIcon("Lock")
                    color: UM.Theme.getColor("icon")
                    width: UM.Theme.getSize("button_icon").width
                    height: UM.Theme.getSize("button_icon").height
                }
                onClicked: UM.Controller.setProperty("Mode", "fixed")
            }

            UM.ToolbarButton
            {
                id: forceModeButton
                checkable: true
                checked: bcPanel.currentMode === "force"
                text: catalog.i18nc("@action:button", "Apply Load")
                toolItem: UM.ColorImage
                {
                    source: UM.Theme.getIcon("ArrowRight")
                    color: UM.Theme.getColor("icon")
                    width: UM.Theme.getSize("button_icon").width
                    height: UM.Theme.getSize("button_icon").height
                }
                onClicked: UM.Controller.setProperty("Mode", "force")
            }
        }

        UM.Label
        {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            font: UM.Theme.getFont("small")
            color: UM.Theme.getColor("text_medium")
            text: bcPanel.currentMode === "fixed"
                ? catalog.i18nc("@info", "Click faces where the part is held, screwed down, or resting on a surface.")
                : catalog.i18nc("@info", "Click faces where a force or weight pushes/pulls. Then set the load amount below.")
        }

        UM.Label
        {
            Layout.fillWidth: true
            font: UM.Theme.getFont("small")
            color: UM.Theme.getColor("text_inactive")
            text: catalog.i18nc("@info", "Shift+click: add face | Ctrl+click: remove face")
        }

        // ── Force vector inputs (force mode only) ────────────────────────────
        ColumnLayout
        {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").height / 2
            visible: bcPanel.currentMode === "force"

            UM.Label
            {
                text: catalog.i18nc("@label", "Force Vector (N)")
                font: UM.Theme.getFont("medium")
            }

            GridLayout
            {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: UM.Theme.getSize("default_margin").width
                rowSpacing: UM.Theme.getSize("default_margin").height / 2

                UM.Label { text: catalog.i18nc("@label", "Fx:") }
                TextField
                {
                    id: forceXField
                    Layout.fillWidth: true
                    text: bcPanel.forceX.toFixed(2)
                    validator: DoubleValidator { decimals: 2; notation: DoubleValidator.ScientificNotation }
                    onEditingFinished:
                        UM.Controller.setProperty("ForceX", parseFloat(text) || 0.0)
                }

                UM.Label { text: catalog.i18nc("@label", "Fy:") }
                TextField
                {
                    id: forceYField
                    Layout.fillWidth: true
                    text: bcPanel.forceY.toFixed(2)
                    validator: DoubleValidator { decimals: 2; notation: DoubleValidator.ScientificNotation }
                    onEditingFinished:
                        UM.Controller.setProperty("ForceY", parseFloat(text) || 0.0)
                }

                UM.Label { text: catalog.i18nc("@label", "Fz:") }
                TextField
                {
                    id: forceZField
                    Layout.fillWidth: true
                    text: bcPanel.forceZ.toFixed(2)
                    validator: DoubleValidator { decimals: 2; notation: DoubleValidator.ScientificNotation }
                    onEditingFinished:
                        UM.Controller.setProperty("ForceZ", parseFloat(text) || 0.0)
                }
            }

            // Magnitude readout
            UM.Label
            {
                text: catalog.i18nc("@info", "Magnitude: %1 N").arg(
                    Math.sqrt(
                        bcPanel.forceX * bcPanel.forceX +
                        bcPanel.forceY * bcPanel.forceY +
                        bcPanel.forceZ * bcPanel.forceZ
                    ).toFixed(2)
                )
                color: UM.Theme.getColor("text_medium")
                font: UM.Theme.getFont("small")
            }
        }

        // ── Selection summary ────────────────────────────────────────────────
        UM.Label
        {
            text: catalog.i18nc("@label", "Selection")
            font: UM.Theme.getFont("medium_bold")
        }

        Rectangle
        {
            Layout.fillWidth: true
            height: selectionLabel.implicitHeight + UM.Theme.getSize("default_margin").height
            color: UM.Theme.getColor("main_background")
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            radius: UM.Theme.getSize("default_radius").width

            UM.Label
            {
                id: selectionLabel
                anchors
                {
                    left: parent.left
                    right: parent.right
                    verticalCenter: parent.verticalCenter
                    margins: UM.Theme.getSize("default_margin").width
                }
                text: bcPanel.selectionSummary !== ""
                    ? bcPanel.selectionSummary
                    : catalog.i18nc("@info", "No faces selected. Click a face to select it.")
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("small")
            }
        }

        // ── Action buttons ───────────────────────────────────────────────────
        UM.Label
        {
            Layout.fillWidth: true
            visible: bcPanel.currentMode === "force"
            wrapMode: Text.WordWrap
            font: UM.Theme.getFont("small")
            color: UM.Theme.getColor("text_medium")
            text: (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
                ? catalog.i18nc("@info", "%1 face(s) selected. Click 'Confirm Load' to save, or Shift+click to add more.").arg(toolProperties.getValue("CurrentSelectionCount") ?? 0)
                : catalog.i18nc("@info", "Click faces where the load acts. Hold Shift to add more faces.")
        }

        Cura.PrimaryButton
        {
            Layout.fillWidth: true
            text: catalog.i18nc("@action:button", "Confirm Load on Selected Faces")
            enabled: (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
            onClicked: UM.Controller.setProperty("ConfirmForceGroup", true)
        }

        RowLayout
        {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width / 2

            Cura.SecondaryButton
            {
                Layout.fillWidth: true
                text: catalog.i18nc("@action:button", "Clear Supports")
                onClicked: UM.Controller.setProperty("ClearFixedFaces", true)
            }
            Cura.SecondaryButton
            {
                Layout.fillWidth: true
                text: catalog.i18nc("@action:button", "Clear Loads")
                onClicked: UM.Controller.setProperty("ClearForceGroups", true)
            }
        }

        // Bottom spacer
        Item { Layout.fillHeight: true }
    }
}
