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
    readonly property string currentMode:      toolProperties.getValue("Mode")             ?? "fixed"
    readonly property string selectionSummary: toolProperties.getValue("SelectionSummary") ?? ""
    readonly property real   forceX:           Number(toolProperties.getValue("ForceX")           ?? 0)
    readonly property real   forceY:           Number(toolProperties.getValue("ForceY")           ?? 0)
    readonly property real   forceZ:           Number(toolProperties.getValue("ForceZ")           ?? 0)
    readonly property string selectionMode:    toolProperties.getValue("SelectionMode")    ?? "single"
    readonly property int    activeSupportIdx: toolProperties.getValue("ActiveSupportIndex") ?? -1
    readonly property int    activeForceIdx:   toolProperties.getValue("ActiveForceIndex")   ?? -1
    readonly property var    supportListModel: JSON.parse(toolProperties.getValue("SupportListModel") ?? "[]")
    readonly property var    forceListModel:   JSON.parse(toolProperties.getValue("ForceListModel")   ?? "[]")

    implicitWidth: 280 * screenScaleFactor
    implicitHeight: 600 * screenScaleFactor

    UM.I18nCatalog { id: catalog; name: "cura" }

    ScrollView
    {
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout
        {
            id: columnLayout
            width: bcPanel.width
            spacing: UM.Theme.getSize("default_margin").height

        // ── Step guide (visible when no BCs defined) ────────────────────
        Rectangle
        {
            Layout.fillWidth: true
            visible: bcPanel.supportListModel.length === 0 && bcPanel.forceListModel.length === 0
            height: visible ? stepGuide.implicitHeight + UM.Theme.getSize("default_margin").height * 2 : 0
            color: "#1a2a3a"
            radius: UM.Theme.getSize("default_radius").width

            UM.Label
            {
                id: stepGuide
                anchors
                {
                    left: parent.left; right: parent.right
                    verticalCenter: parent.verticalCenter
                    margins: UM.Theme.getSize("default_margin").width
                }
                wrapMode: Text.WordWrap
                color: "#aaccee"
                font: UM.Theme.getFont("small")
                text: catalog.i18nc("@info",
                    "Quick start:\n" +
                    "1. Select 'Support / Mount' and click faces where the part is held\n" +
                    "2. Select 'Apply Load' and click faces where forces act\n" +
                    "3. Set the load amount and click 'Confirm Load'\n" +
                    "4. Click 'Confirm and Optimize' to run analysis")
            }
        }

        // ── Mode selector ─────────────────────────────────────────────────
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
                    source: Qt.resolvedUrl("../icons/mount.svg")
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
                    source: Qt.resolvedUrl("../icons/force.svg")
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
                : bcPanel.currentMode === "rotate"
                    ? catalog.i18nc("@info", "Drag the rotation rings to adjust force direction.")
                    : catalog.i18nc("@info", "Click faces where a force or weight pushes/pulls. Then set the load amount below.")
        }

        UM.Label
        {
            Layout.fillWidth: true
            font: UM.Theme.getFont("small")
            color: UM.Theme.getColor("text_inactive")
            text: catalog.i18nc("@info", "Click: select face | Alt+click (Option on Mac): toggle face")
        }

        // ── Rotate mode indicator ─────────────────────────────────────────
        Rectangle
        {
            Layout.fillWidth: true
            visible: bcPanel.currentMode === "rotate"
            height: visible ? rotateModeLabel.implicitHeight + UM.Theme.getSize("default_margin").height : 0
            color: "#222244"
            radius: UM.Theme.getSize("default_radius").width

            UM.Label
            {
                id: rotateModeLabel
                anchors
                {
                    left: parent.left
                    right: parent.right
                    verticalCenter: parent.verticalCenter
                    margins: UM.Theme.getSize("default_margin").width
                }
                wrapMode: Text.WordWrap
                color: "#aaaaff"
                text: catalog.i18nc("@info", "Drag the rings to adjust direction. Click 'Support / Mount' or 'Apply Load' to exit.")
            }
        }

        // ── Selection helper (face group expansion) ───────────────────────
        UM.Label
        {
            text: catalog.i18nc("@label", "Selection helper")
            font: UM.Theme.getFont("medium_bold")
        }

        RowLayout
        {
            spacing: UM.Theme.getSize("default_margin").width / 4

            UM.ToolbarButton
            {
                checkable: true
                checked: bcPanel.selectionMode === "single"
                text: catalog.i18nc("@option", "Single")
                toolItem: UM.ColorImage
                {
                    source: Qt.resolvedUrl("../icons/select_single.svg")
                    color: UM.Theme.getColor("icon")
                    width: UM.Theme.getSize("button_icon").width
                    height: UM.Theme.getSize("button_icon").height
                }
                onClicked: UM.Controller.setProperty("SelectionMode", "single")
            }

            UM.ToolbarButton
            {
                checkable: true
                checked: bcPanel.selectionMode === "flat"
                text: catalog.i18nc("@option", "Surface")
                toolItem: UM.ColorImage
                {
                    source: Qt.resolvedUrl("../icons/select_surface.svg")
                    color: UM.Theme.getColor("icon")
                    width: UM.Theme.getSize("button_icon").width
                    height: UM.Theme.getSize("button_icon").height
                }
                onClicked: UM.Controller.setProperty("SelectionMode", "flat")
            }

            UM.ToolbarButton
            {
                checkable: true
                checked: bcPanel.selectionMode === "hole"
                text: catalog.i18nc("@option", "Hole")
                toolItem: UM.ColorImage
                {
                    source: Qt.resolvedUrl("../icons/select_hole.svg")
                    color: UM.Theme.getColor("icon")
                    width: UM.Theme.getSize("button_icon").width
                    height: UM.Theme.getSize("button_icon").height
                }
                onClicked: UM.Controller.setProperty("SelectionMode", "hole")
            }

            UM.ToolbarButton
            {
                checkable: true
                checked: bcPanel.selectionMode === "cylinder"
                text: catalog.i18nc("@option", "Cylinder")
                toolItem: UM.ColorImage
                {
                    source: Qt.resolvedUrl("../icons/select_cylinder.svg")
                    color: UM.Theme.getColor("icon")
                    width: UM.Theme.getSize("button_icon").width
                    height: UM.Theme.getSize("button_icon").height
                }
                onClicked: UM.Controller.setProperty("SelectionMode", "cylinder")
            }
        }

        // ── Supports list ─────────────────────────────────────────────────
        UM.Label
        {
            text: catalog.i18nc("@label", "Supports")
            font: UM.Theme.getFont("medium_bold")
        }

        Column
        {
            Layout.fillWidth: true
            spacing: 2

            Repeater
            {
                model: bcPanel.supportListModel

                Rectangle
                {
                    width: columnLayout.width
                    height: supportRowLabel.implicitHeight + UM.Theme.getSize("default_margin").height
                    color: bcPanel.activeSupportIdx === modelData.index
                        ? UM.Theme.getColor("primary")
                        : UM.Theme.getColor("main_background")
                    border.color: UM.Theme.getColor("lining")
                    border.width: UM.Theme.getSize("default_lining").width
                    radius: UM.Theme.getSize("default_radius").width

                    RowLayout
                    {
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width / 2

                        UM.Label
                        {
                            id: supportRowLabel
                            Layout.fillWidth: true
                            text: modelData.label
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                        }

                        UM.ColorImage
                        {
                            source: UM.Theme.getIcon("Cancel")
                            color: UM.Theme.getColor("text_medium")
                            width: UM.Theme.getSize("small_button_icon").width
                            height: width

                            MouseArea
                            {
                                anchors.fill: parent
                                onClicked:
                                {
                                    UM.Controller.setProperty("ActiveSupportIndex", modelData.index)
                                    UM.Controller.setProperty("DeleteActiveSupport", true)
                                }
                            }
                        }
                    }

                    MouseArea
                    {
                        anchors.fill: parent
                        z: -1
                        onClicked: UM.Controller.setProperty("ActiveSupportIndex", modelData.index)
                    }
                }
            }

            // Empty state
            UM.Label
            {
                visible: bcPanel.supportListModel.length === 0
                width: columnLayout.width
                text: catalog.i18nc("@info", "No supports defined. Switch to Support / Mount mode and click faces.")
                wrapMode: Text.WordWrap
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text_inactive")
            }
        }

        // ── Forces list ───────────────────────────────────────────────────
        UM.Label
        {
            text: catalog.i18nc("@label", "Forces")
            font: UM.Theme.getFont("medium_bold")
        }

        Column
        {
            Layout.fillWidth: true
            spacing: 2

            Repeater
            {
                model: bcPanel.forceListModel

                Rectangle
                {
                    width: columnLayout.width
                    height: forceRowLabel.implicitHeight + UM.Theme.getSize("default_margin").height
                    color: bcPanel.activeForceIdx === modelData.index
                        ? UM.Theme.getColor("primary")
                        : UM.Theme.getColor("main_background")
                    border.color: UM.Theme.getColor("lining")
                    border.width: UM.Theme.getSize("default_lining").width
                    radius: UM.Theme.getSize("default_radius").width

                    RowLayout
                    {
                        anchors.fill: parent
                        anchors.margins: UM.Theme.getSize("default_margin").width / 2

                        UM.Label
                        {
                            id: forceRowLabel
                            Layout.fillWidth: true
                            text: modelData.label
                            color: UM.Theme.getColor("text")
                            elide: Text.ElideRight
                        }

                        UM.ColorImage
                        {
                            source: UM.Theme.getIcon("Cancel")
                            color: UM.Theme.getColor("text_medium")
                            width: UM.Theme.getSize("small_button_icon").width
                            height: width

                            MouseArea
                            {
                                anchors.fill: parent
                                onClicked:
                                {
                                    UM.Controller.setProperty("ActiveForceIndex", modelData.index)
                                    UM.Controller.setProperty("DeleteActiveForce", true)
                                }
                            }
                        }
                    }

                    MouseArea
                    {
                        anchors.fill: parent
                        z: -1
                        onClicked: UM.Controller.setProperty("ActiveForceIndex", modelData.index)
                    }
                }
            }

            // Empty state
            UM.Label
            {
                visible: bcPanel.forceListModel.length === 0
                width: columnLayout.width
                text: catalog.i18nc("@info", "No forces defined. Switch to Apply Load mode, select faces, then confirm.")
                wrapMode: Text.WordWrap
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text_inactive")
            }
        }

        // ── Force settings (force + rotate mode) ──────────────────────────
        ColumnLayout
        {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").height / 2
            visible: bcPanel.currentMode === "force" || bcPanel.currentMode === "rotate"

            UM.Label
            {
                text: catalog.i18nc("@label", "Load Amount")
                font: UM.Theme.getFont("medium_bold")
            }

            RowLayout
            {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width / 2

                TextField
                {
                    id: magnitudeField
                    Layout.fillWidth: true
                    text: (toolProperties.getValue("ForceMagnitude") ?? 100).toFixed(1)
                    validator: DoubleValidator { bottom: 0; decimals: 1 }
                    onEditingFinished:
                        UM.Controller.setProperty("ForceMagnitude", parseFloat(text) || 100.0)
                }

                UM.Label { text: "N" }
            }

            UM.Label
            {
                Layout.fillWidth: true
                text: catalog.i18nc("@info", "Tip: 1 kg weight \u2248 10 N. A finger push \u2248 20\u201350 N.")
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text_inactive")
                wrapMode: Text.WordWrap
            }

            UM.Label
            {
                text: catalog.i18nc("@label", "Direction (advanced)")
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text_medium")
            }

            GridLayout
            {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: UM.Theme.getSize("default_margin").width
                rowSpacing: UM.Theme.getSize("default_margin").height / 4

                UM.Label { text: "Fx:"; font: UM.Theme.getFont("small") }
                TextField
                {
                    id: forceXField
                    Layout.fillWidth: true
                    text: bcPanel.forceX.toFixed(1)
                    readOnly: bcPanel.currentMode === "rotate"
                    font.pointSize: 9
                    validator: DoubleValidator { decimals: 1 }
                    onEditingFinished:
                        UM.Controller.setProperty("ForceX", parseFloat(text) || 0.0)
                }

                UM.Label { text: "Fy:"; font: UM.Theme.getFont("small") }
                TextField
                {
                    id: forceYField
                    Layout.fillWidth: true
                    text: bcPanel.forceY.toFixed(1)
                    readOnly: bcPanel.currentMode === "rotate"
                    font.pointSize: 9
                    validator: DoubleValidator { decimals: 1 }
                    onEditingFinished:
                        UM.Controller.setProperty("ForceY", parseFloat(text) || 0.0)
                }

                UM.Label { text: "Fz:"; font: UM.Theme.getFont("small") }
                TextField
                {
                    id: forceZField
                    Layout.fillWidth: true
                    text: bcPanel.forceZ.toFixed(1)
                    readOnly: bcPanel.currentMode === "rotate"
                    font.pointSize: 9
                    validator: DoubleValidator { decimals: 1 }
                    onEditingFinished:
                        UM.Controller.setProperty("ForceZ", parseFloat(text) || 0.0)
                }
            }

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

        // ── Confirm load button (force mode) ──────────────────────────────
        UM.Label
        {
            Layout.fillWidth: true
            visible: bcPanel.currentMode === "force"
            wrapMode: Text.WordWrap
            font: UM.Theme.getFont("small")
            color: UM.Theme.getColor("text_medium")
            text: (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
                ? catalog.i18nc("@info", "%1 face(s) selected. Click 'Confirm Load' to save, or click more faces to add. Alt+click to deselect.").arg(toolProperties.getValue("CurrentSelectionCount") ?? 0)
                : catalog.i18nc("@info", "Click faces where the load acts. Each click adds to selection. Alt+click to deselect.")
        }

        Cura.PrimaryButton
        {
            Layout.fillWidth: true
            visible: bcPanel.currentMode === "force"
            text: catalog.i18nc("@action:button", "Confirm Load on Selected Faces")
            enabled: (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
            onClicked: UM.Controller.setProperty("ConfirmForceGroup", true)
        }

        // ── Quick setup ──────────────────────────────────────────────────
        UM.Label
        {
            text: catalog.i18nc("@label", "Quick Setup")
            font: UM.Theme.getFont("medium_bold")
        }

        // Active quick setup mode indicator
        Rectangle
        {
            Layout.fillWidth: true
            visible: (toolProperties.getValue("QuickSetupMode") ?? "") !== ""
            height: visible ? quickModeLabel.implicitHeight + UM.Theme.getSize("default_margin").height : 0
            color: "#1a3a2a"
            radius: UM.Theme.getSize("default_radius").width

            UM.Label
            {
                id: quickModeLabel
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: UM.Theme.getSize("default_margin").width }
                wrapMode: Text.WordWrap
                color: "#aaffcc"
                font: UM.Theme.getFont("small")
                text: {
                    var mode = toolProperties.getValue("QuickSetupMode") ?? ""
                    if (mode === "gravity_pick_bottom") return catalog.i18nc("@info", "Click the bottom face of your part (the face resting on the build plate or surface).")
                    if (mode === "cantilever_pick_fixed") return catalog.i18nc("@info", "Click the face where the part is fixed/clamped (the end that doesn't move).")
                    return ""
                }
            }
        }

        Cura.SecondaryButton
        {
            Layout.fillWidth: true
            text: catalog.i18nc("@action:button", "Gravity: Click Bottom Face")
            onClicked: UM.Controller.setProperty("QuickGravityStart", true)
        }

        Cura.SecondaryButton
        {
            Layout.fillWidth: true
            text: catalog.i18nc("@action:button", "Cantilever: Click Fixed End")
            onClicked: UM.Controller.setProperty("QuickCantileverStart", true)
        }

        RowLayout
        {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width / 2

            Cura.SecondaryButton
            {
                Layout.fillWidth: true
                text: catalog.i18nc("@action:button", "Fix Bolt Holes")
                onClicked: UM.Controller.setProperty("QuickMountHoles", true)
            }
            SpinBox
            {
                id: holeDiameterSpinBox
                from: 2; to: 30; value: 8; stepSize: 1
                onValueModified: UM.Controller.setProperty("QuickHoleDiameter", value)
            }
            UM.Label { text: "mm"; font: UM.Theme.getFont("small") }
        }

        // ── Optimize button ───────────────────────────────────────────────
        Item { height: UM.Theme.getSize("default_margin").height }

        Cura.PrimaryButton
        {
            Layout.fillWidth: true
            text: catalog.i18nc("@action:button", "Confirm and Optimize")
            enabled: bcPanel.supportListModel.length > 0 || bcPanel.forceListModel.length > 0
            onClicked: UM.Controller.setProperty("OpenOptimizeDialog", true)
        }

        // Bottom spacer
        Item { height: UM.Theme.getSize("default_margin").height }
        }  // ColumnLayout
    }  // ScrollView
}
