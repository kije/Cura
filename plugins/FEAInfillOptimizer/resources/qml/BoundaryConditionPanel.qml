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

    // Convenience aliases from tool property bag — BC definition
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
    readonly property var    torqueListModel:  JSON.parse(toolProperties.getValue("TorqueListModel")  ?? "[]")

    // Phase and optimization properties
    readonly property string currentPhase:     toolProperties.getValue("Phase")            ?? "define"
    readonly property string activeNodeName:   toolProperties.getValue("ActiveNodeName")   ?? ""
    readonly property real   analysisProgress: Number(toolProperties.getValue("AnalysisProgress") ?? 0)
    readonly property string analysisStage:    toolProperties.getValue("AnalysisStage")    ?? ""
    readonly property bool   hasResults:       toolProperties.getValue("HasResults")       === true || toolProperties.getValue("HasResults") === "true"
    readonly property real   maxStress:        Number(toolProperties.getValue("MaxStress")        ?? 0)
    readonly property real   minStress:        Number(toolProperties.getValue("MinStress")        ?? 0)
    readonly property real   safetyFactor:     Number(toolProperties.getValue("SafetyFactor")     ?? 2)
    readonly property real   safetyFactorResult: Number(toolProperties.getValue("SafetyFactorResult") ?? 0)
    readonly property int    convergenceIter:  Number(toolProperties.getValue("ConvergenceIterations") ?? 0)
    readonly property string safetyVerdict:    toolProperties.getValue("SafetyVerdict")    ?? ""

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

            // ══════════════════════════════════════════════════════════════
            // DEFINE PHASE — all BC definition UI
            // ══════════════════════════════════════════════════════════════
            Item
            {
                Layout.fillWidth: true
                visible: bcPanel.currentPhase === "define"
                implicitHeight: visible ? defineColumn.implicitHeight : 0

                ColumnLayout
                {
                    id: defineColumn
                    width: parent.width
                    spacing: UM.Theme.getSize("default_margin").height

                    // ── Step guide (visible when no BCs defined) ──────────
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

                    // ── Mode selector ─────────────────────────────────────
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

                        UM.ToolbarButton
                        {
                            id: torqueModeButton
                            checkable: true
                            checked: bcPanel.currentMode === "torque"
                            text: catalog.i18nc("@action:button", "Torque")
                            toolItem: UM.ColorImage
                            {
                                source: Qt.resolvedUrl("../icons/torque.svg")
                                color: UM.Theme.getColor("icon")
                                width: UM.Theme.getSize("button_icon").width
                                height: UM.Theme.getSize("button_icon").height
                            }
                            onClicked: UM.Controller.setProperty("Mode", "torque")
                        }
                    }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text_medium")
                        text: {
                            if (bcPanel.currentMode === "fixed")
                                return catalog.i18nc("@info", "Click faces where the part is held, screwed down, or resting on a surface.")
                            if (bcPanel.currentMode === "torque")
                                return catalog.i18nc("@info", "Click faces where a rotational load (twist) is applied. Then set the torque amount below.")
                            if (bcPanel.currentMode === "rotate")
                                return catalog.i18nc("@info", "Drag the rotation rings to adjust force direction.")
                            return catalog.i18nc("@info", "Click faces where a force or weight pushes/pulls. Then set the load amount below.")
                        }
                    }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text_inactive")
                        text: catalog.i18nc("@info", "Click: select face | Alt+click (Option on Mac): toggle face")
                    }

                    // ── Rotate mode indicator ─────────────────────────────
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

                    // ── Selection helper ──────────────────────────────────
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

                    // ── Supports list ─────────────────────────────────────
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
                                width: defineColumn.width
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

                        UM.Label
                        {
                            visible: bcPanel.supportListModel.length === 0
                            width: defineColumn.width
                            text: catalog.i18nc("@info", "No supports defined. Switch to Support / Mount mode and click faces.")
                            wrapMode: Text.WordWrap
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_inactive")
                        }
                    }

                    // ── Forces list ───────────────────────────────────────
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
                                width: defineColumn.width
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

                        UM.Label
                        {
                            visible: bcPanel.forceListModel.length === 0
                            width: defineColumn.width
                            text: catalog.i18nc("@info", "No forces defined. Switch to Apply Load mode, select faces, then confirm.")
                            wrapMode: Text.WordWrap
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_inactive")
                        }
                    }

                    // ── Torques list ──────────────────────────────────────
                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Torques")
                        font: UM.Theme.getFont("medium_bold")
                    }

                    Column
                    {
                        Layout.fillWidth: true
                        spacing: 2

                        Repeater
                        {
                            model: bcPanel.torqueListModel

                            Rectangle
                            {
                                width: defineColumn.width
                                height: torqueRowLabel.implicitHeight + UM.Theme.getSize("default_margin").height
                                color: UM.Theme.getColor("main_background")
                                border.color: UM.Theme.getColor("lining")
                                border.width: UM.Theme.getSize("default_lining").width
                                radius: UM.Theme.getSize("default_radius").width

                                RowLayout
                                {
                                    anchors.fill: parent
                                    anchors.margins: UM.Theme.getSize("default_margin").width / 2

                                    UM.Label
                                    {
                                        id: torqueRowLabel
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
                                            onClicked: UM.Controller.setProperty("DeleteTorqueGroup", modelData.index)
                                        }
                                    }
                                }
                            }
                        }

                        UM.Label
                        {
                            visible: bcPanel.torqueListModel.length === 0
                            width: defineColumn.width
                            text: catalog.i18nc("@info", "No torques defined. Switch to Torque mode, select faces, then confirm.")
                            wrapMode: Text.WordWrap
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_inactive")
                        }
                    }

                    // ── Force settings (force + rotate mode) ──────────────
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

                    // ── Confirm load button (force mode) ──────────────────
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

                    // ── Torque settings (torque mode) ─────────────────────
                    ColumnLayout
                    {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("default_margin").height / 2
                        visible: bcPanel.currentMode === "torque"

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Torque Amount")
                            font: UM.Theme.getFont("medium_bold")
                        }

                        RowLayout
                        {
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").width / 2

                            TextField
                            {
                                Layout.fillWidth: true
                                text: Number(toolProperties.getValue("TorqueMagnitude") ?? 1).toFixed(2)
                                validator: DoubleValidator { bottom: 0; decimals: 2 }
                                onEditingFinished: UM.Controller.setProperty("TorqueMagnitude", parseFloat(text) || 1.0)
                            }
                            UM.Label { text: "Nm" }
                        }

                        UM.Label
                        {
                            Layout.fillWidth: true
                            text: catalog.i18nc("@info", "Tip: Hand-tightened bolt \u2248 1-5 Nm. Wrench-tightened \u2248 10-50 Nm.")
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_inactive")
                            wrapMode: Text.WordWrap
                        }

                        UM.Label
                        {
                            Layout.fillWidth: true
                            visible: (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
                            text: catalog.i18nc("@info", "%1 face(s) selected for torque. The torque axis will be the average surface normal.").arg(toolProperties.getValue("CurrentSelectionCount") ?? 0)
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_medium")
                            wrapMode: Text.WordWrap
                        }
                    }

                    Cura.PrimaryButton
                    {
                        Layout.fillWidth: true
                        visible: bcPanel.currentMode === "torque"
                        text: catalog.i18nc("@action:button", "Confirm Torque on Selected Faces")
                        enabled: (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
                        onClicked: UM.Controller.setProperty("ConfirmTorqueGroup", true)
                    }

                    // ── Quick setup ───────────────────────────────────────
                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Quick Setup")
                        font: UM.Theme.getFont("medium_bold")
                    }

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

                    // ── Confirm and Optimize button ───────────────────────
                    Item { height: UM.Theme.getSize("default_margin").height }

                    Cura.PrimaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Confirm and Optimize")
                        enabled: bcPanel.supportListModel.length > 0 || bcPanel.forceListModel.length > 0 || bcPanel.torqueListModel.length > 0
                        onClicked: UM.Controller.setProperty("OpenOptimizeDialog", true)
                    }

                    Item { height: UM.Theme.getSize("default_margin").height }
                }  // defineColumn
            }  // DEFINE phase Item

            // ══════════════════════════════════════════════════════════════
            // OPTIMIZE PHASE — material, safety, mesh quality, run button
            // ══════════════════════════════════════════════════════════════
            Item
            {
                Layout.fillWidth: true
                visible: bcPanel.currentPhase === "optimize"
                implicitHeight: visible ? optimizeColumn.implicitHeight : 0

                ColumnLayout
                {
                    id: optimizeColumn
                    width: parent.width
                    spacing: UM.Theme.getSize("default_margin").height

                    // Phase header
                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@label", "Analysis Setup")
                        font: UM.Theme.getFont("large_bold")
                    }

                    // BC summary chip (tappable to go back)
                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: bcSummaryRow.implicitHeight + UM.Theme.getSize("default_margin").height
                        color: UM.Theme.getColor("main_background")
                        border.color: UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        radius: UM.Theme.getSize("default_radius").width

                        RowLayout
                        {
                            id: bcSummaryRow
                            anchors
                            {
                                left: parent.left; right: parent.right
                                verticalCenter: parent.verticalCenter
                                margins: UM.Theme.getSize("default_margin").width / 2
                            }
                            spacing: UM.Theme.getSize("default_margin").width / 2

                            UM.ColorImage
                            {
                                source: UM.Theme.getIcon("Settings")
                                color: UM.Theme.getColor("text_medium")
                                width: UM.Theme.getSize("small_button_icon").width
                                height: width
                            }

                            UM.Label
                            {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text")
                                text: {
                                    var s = bcPanel.supportListModel.length
                                    var f = bcPanel.forceListModel.length
                                    var t = bcPanel.torqueListModel.length
                                    var parts = []
                                    if (s > 0) parts.push(catalog.i18nc("@info", "%1 support(s)").arg(s))
                                    if (f > 0) parts.push(catalog.i18nc("@info", "%1 force(s)").arg(f))
                                    if (t > 0) parts.push(catalog.i18nc("@info", "%1 torque(s)").arg(t))
                                    return parts.length > 0 ? parts.join(", ") : catalog.i18nc("@info", "No BCs defined")
                                }
                            }

                            UM.Label
                            {
                                text: catalog.i18nc("@action", "Edit")
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("primary")
                            }
                        }

                        MouseArea
                        {
                            anchors.fill: parent
                            onClicked: UM.Controller.setProperty("GoBackToDefine", true)
                        }
                    }

                    // Material
                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Material")
                        font: UM.Theme.getFont("medium_bold")
                    }

                    ComboBox
                    {
                        id: materialSelector
                        Layout.fillWidth: true
                        model: ["PLA", "ABS", "PETG", "Nylon", "PC", "TPU 95A", "CF-Nylon"]
                        currentIndex: {
                            var mat = toolProperties.getValue("MaterialName") ?? "PLA"
                            var idx = model.indexOf(mat)
                            return idx >= 0 ? idx : 0
                        }
                        onCurrentTextChanged: UM.Controller.setProperty("MaterialName", currentText)
                    }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@info", "Select your printing material. This determines stiffness and strength for analysis.")
                        color: UM.Theme.getColor("text_medium")
                        font: UM.Theme.getFont("small")
                        wrapMode: Text.WordWrap
                    }

                    // Safety margin
                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Safety Margin")
                        font: UM.Theme.getFont("medium_bold")
                    }

                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("default_margin").width / 2

                        SpinBox
                        {
                            id: safetySpinBox
                            // Integer 10–50 mapped to 1.0×–5.0×
                            from: 10; to: 50; stepSize: 5
                            value: Math.round((toolProperties.getValue("SafetyFactor") ?? 2.0) * 10)
                            onValueModified: UM.Controller.setProperty("SafetyFactor", value / 10.0)
                            textFromValue: function(v) { return (v / 10.0).toFixed(1) + "\u00d7" }
                            valueFromText: function(t) { return Math.round(parseFloat(t) * 10) }
                        }

                        UM.Label
                        {
                            text: catalog.i18nc("@info", "Higher margin = more conservative (heavier) infill")
                            font: UM.Theme.getFont("small")
                            color: UM.Theme.getColor("text_inactive")
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    // Mesh quality
                    UM.Label
                    {
                        text: catalog.i18nc("@label", "Mesh Quality")
                        font: UM.Theme.getFont("medium_bold")
                    }

                    ColumnLayout
                    {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("default_margin").height / 4

                        Repeater
                        {
                            model: [
                                { label: catalog.i18nc("@option", "Fast (coarse)"),        value: "coarse"  },
                                { label: catalog.i18nc("@option", "Balanced (medium)"),    value: "medium"  },
                                { label: catalog.i18nc("@option", "Precise (fine)"),       value: "fine"    }
                            ]

                            RowLayout
                            {
                                spacing: UM.Theme.getSize("default_margin").width / 2

                                RadioButton
                                {
                                    checked: (toolProperties.getValue("MeshResolution") ?? "medium") === modelData.value
                                    onClicked: UM.Controller.setProperty("MeshResolution", modelData.value)
                                }

                                UM.Label
                                {
                                    text: modelData.label
                                    font: UM.Theme.getFont("default")
                                    MouseArea
                                    {
                                        anchors.fill: parent
                                        onClicked: UM.Controller.setProperty("MeshResolution", modelData.value)
                                    }
                                }
                            }
                        }
                    }

                    // Advanced section (collapsible)
                    ColumnLayout
                    {
                        Layout.fillWidth: true
                        spacing: 0

                        property bool expanded: false

                        // Header bar (always visible, clickable)
                        Rectangle
                        {
                            Layout.fillWidth: true
                            height: advancedHeaderRow.implicitHeight + UM.Theme.getSize("default_margin").height / 2
                            color: "transparent"
                            border.color: UM.Theme.getColor("lining")
                            border.width: UM.Theme.getSize("default_lining").width
                            radius: UM.Theme.getSize("default_radius").width

                            RowLayout
                            {
                                id: advancedHeaderRow
                                anchors
                                {
                                    left: parent.left; right: parent.right
                                    verticalCenter: parent.verticalCenter
                                    margins: UM.Theme.getSize("default_margin").width / 2
                                }

                                UM.Label
                                {
                                    Layout.fillWidth: true
                                    text: catalog.i18nc("@label", "Advanced Settings")
                                    font: UM.Theme.getFont("small")
                                    color: UM.Theme.getColor("text_medium")
                                }

                                UM.ColorImage
                                {
                                    source: parent.parent.parent.expanded ? UM.Theme.getIcon("ChevronSingleUp") : UM.Theme.getIcon("ChevronSingleDown")
                                    color: UM.Theme.getColor("text_medium")
                                    width: UM.Theme.getSize("small_button_icon").width
                                    height: width
                                }
                            }

                            MouseArea
                            {
                                anchors.fill: parent
                                onClicked: parent.parent.expanded = !parent.parent.expanded
                            }
                        }

                        // Expandable content (participates in ColumnLayout)
                        GridLayout
                        {
                            Layout.fillWidth: true
                            visible: parent.expanded
                            Layout.leftMargin: UM.Theme.getSize("default_margin").width / 2
                            Layout.rightMargin: UM.Theme.getSize("default_margin").width / 2
                            Layout.topMargin: UM.Theme.getSize("default_margin").height / 2
                            columns: 2
                            columnSpacing: UM.Theme.getSize("default_margin").width
                            rowSpacing: UM.Theme.getSize("default_margin").height / 4

                            UM.Label { text: catalog.i18nc("@label", "Min infill (%)"); font: UM.Theme.getFont("small") }
                            SpinBox { from: 5; to: 90; value: 10; stepSize: 5; Layout.fillWidth: true
                                onValueModified: UM.Controller.setProperty("MinDensity", value) }

                            UM.Label { text: catalog.i18nc("@label", "Max infill (%)"); font: UM.Theme.getFont("small") }
                            SpinBox { from: 10; to: 100; value: 80; stepSize: 5; Layout.fillWidth: true
                                onValueModified: UM.Controller.setProperty("MaxDensity", value) }

                            UM.Label { text: catalog.i18nc("@label", "Density steps"); font: UM.Theme.getFont("small") }
                            SpinBox { from: 2; to: 20; value: 5; stepSize: 1; Layout.fillWidth: true
                                onValueModified: UM.Controller.setProperty("NumZones", value) }

                            UM.Label { text: catalog.i18nc("@label", "Analysis passes"); font: UM.Theme.getFont("small") }
                            SpinBox { from: 1; to: 10; value: 5; stepSize: 1; Layout.fillWidth: true
                                onValueModified: UM.Controller.setProperty("MaxIterations", value) }

                            UM.Label { text: catalog.i18nc("@label", "Layer bonding (%)"); font: UM.Theme.getFont("small") }
                            SpinBox { from: 10; to: 100; value: 50; stepSize: 5; Layout.fillWidth: true
                                onValueModified: UM.Controller.setProperty("BondingCoeff", value) }
                        }
                    }

                    // Dependency warning
                    Rectangle
                    {
                        Layout.fillWidth: true
                        visible: !toolProperties.getValue("DepsAvailable")
                        height: visible ? depsLabel.implicitHeight + UM.Theme.getSize("default_margin").height * 2 : 0
                        color: "#442222"
                        radius: UM.Theme.getSize("default_radius").width

                        ColumnLayout
                        {
                            id: depsLabel
                            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: UM.Theme.getSize("default_margin").width }

                            UM.Label
                            {
                                Layout.fillWidth: true
                                text: catalog.i18nc("@info:warning", "Required libraries not installed. Click Install, then restart Cura.")
                                color: "#ff6666"
                                wrapMode: Text.WordWrap
                                font: UM.Theme.getFont("small")
                            }

                            Cura.SecondaryButton
                            {
                                text: catalog.i18nc("@action:button", "Install Dependencies")
                                onClicked: UM.Controller.setProperty("InstallDependencies", true)
                            }
                        }
                    }

                    // Run button
                    Item { height: UM.Theme.getSize("default_margin").height / 2 }

                    Cura.PrimaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Run Analysis")
                        enabled: !!toolProperties.getValue("DepsAvailable")
                        onClicked: UM.Controller.setProperty("RunAnalysis", true)
                    }

                    // Back button
                    Cura.SecondaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Back to Setup")
                        onClicked: UM.Controller.setProperty("GoBackToDefine", true)
                    }

                    Item { height: UM.Theme.getSize("default_margin").height }
                }  // optimizeColumn
            }  // OPTIMIZE phase Item

            // ══════════════════════════════════════════════════════════════
            // RUNNING PHASE — progress bar, stage label, stop button
            // ══════════════════════════════════════════════════════════════
            Item
            {
                Layout.fillWidth: true
                visible: bcPanel.currentPhase === "running"
                implicitHeight: visible ? runningColumn.implicitHeight : 0

                ColumnLayout
                {
                    id: runningColumn
                    width: parent.width
                    spacing: UM.Theme.getSize("default_margin").height

                    Item { height: UM.Theme.getSize("default_margin").height }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@label", "Running Analysis")
                        font: UM.Theme.getFont("large_bold")
                        horizontalAlignment: Text.AlignHCenter
                    }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: bcPanel.activeNodeName !== "" ? bcPanel.activeNodeName : catalog.i18nc("@info", "Selected model")
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text_medium")
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideMiddle
                    }

                    Item { height: UM.Theme.getSize("default_margin").height / 2 }

                    ProgressBar
                    {
                        id: analysisProgressBar
                        Layout.fillWidth: true
                        from: 0; to: 100
                        value: bcPanel.analysisProgress

                        background: Rectangle
                        {
                            implicitHeight: 8 * screenScaleFactor
                            color: UM.Theme.getColor("lining")
                            radius: 4 * screenScaleFactor
                        }

                        contentItem: Item
                        {
                            Rectangle
                            {
                                width: analysisProgressBar.visualPosition * parent.width
                                height: parent.height
                                radius: 4 * screenScaleFactor
                                color: UM.Theme.getColor("primary")
                            }
                        }
                    }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: bcPanel.analysisStage !== "" ? bcPanel.analysisStage : catalog.i18nc("@info", "Preparing...")
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text_medium")
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                    }

                    Item { height: UM.Theme.getSize("default_margin").height }

                    Cura.SecondaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Stop")
                        onClicked: UM.Controller.setProperty("CancelAnalysis", true)
                    }

                    Item { height: UM.Theme.getSize("default_margin").height }
                }  // runningColumn
            }  // RUNNING phase Item

            // ══════════════════════════════════════════════════════════════
            // REVIEW PHASE — verdict, metrics, apply / hide map
            // ══════════════════════════════════════════════════════════════
            Item
            {
                Layout.fillWidth: true
                visible: bcPanel.currentPhase === "review"
                implicitHeight: visible ? reviewColumn.implicitHeight : 0

                ColumnLayout
                {
                    id: reviewColumn
                    width: parent.width
                    spacing: UM.Theme.getSize("default_margin").height

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@label", "Analysis Results")
                        font: UM.Theme.getFont("large_bold")
                    }

                    // Safety verdict chip
                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: visible ? verdictLabel.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                        radius: UM.Theme.getSize("default_radius").width
                        color: {
                            var v = bcPanel.safetyVerdict
                            if (v === "unsafe")       return "#442222"
                            if (v === "marginal")     return "#443322"
                            if (v === "safe")         return "#224422"
                            if (v === "conservative") return "#222244"
                            return UM.Theme.getColor("main_background")
                        }

                        UM.Label
                        {
                            id: verdictLabel
                            anchors
                            {
                                left: parent.left; right: parent.right
                                verticalCenter: parent.verticalCenter
                                margins: UM.Theme.getSize("default_margin").width
                            }
                            wrapMode: Text.WordWrap
                            color: "#ffffff"
                            font: UM.Theme.getFont("medium_bold")
                            text: {
                                var v = bcPanel.safetyVerdict
                                if (v === "unsafe")       return catalog.i18nc("@info", "Warning: Part may fail under this load. Increase max infill or redesign.")
                                if (v === "marginal")     return catalog.i18nc("@info", "Marginal safety. Consider increasing max infill density.")
                                if (v === "safe")         return catalog.i18nc("@info", "Part should handle this load safely with optimized infill.")
                                if (v === "conservative") return catalog.i18nc("@info", "Part is over-engineered. You could reduce max infill to save material.")
                                return catalog.i18nc("@info", "Analysis complete.")
                            }
                        }
                    }

                    // Metrics grid
                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: metricsGrid.implicitHeight + UM.Theme.getSize("default_margin").height
                        color: UM.Theme.getColor("main_background")
                        border.color: UM.Theme.getColor("lining")
                        border.width: UM.Theme.getSize("default_lining").width
                        radius: UM.Theme.getSize("default_radius").width

                        GridLayout
                        {
                            id: metricsGrid
                            anchors
                            {
                                left: parent.left; right: parent.right; top: parent.top
                                margins: UM.Theme.getSize("default_margin").width
                            }
                            columns: 2
                            columnSpacing: UM.Theme.getSize("default_margin").width
                            rowSpacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: catalog.i18nc("@label", "Max Stress:"); font: UM.Theme.getFont("small") }
                            UM.Label
                            {
                                text: bcPanel.hasResults ? bcPanel.maxStress.toFixed(1) + " MPa" : "—"
                                font: UM.Theme.getFont("small")
                            }

                            UM.Label { text: catalog.i18nc("@label", "Min Stress:"); font: UM.Theme.getFont("small") }
                            UM.Label
                            {
                                text: bcPanel.hasResults ? bcPanel.minStress.toFixed(1) + " MPa" : "—"
                                font: UM.Theme.getFont("small")
                            }

                            UM.Label { text: catalog.i18nc("@label", "Safety Factor:"); font: UM.Theme.getFont("small") }
                            UM.Label
                            {
                                text: bcPanel.hasResults ? bcPanel.safetyFactorResult.toFixed(2) : "—"
                                font: UM.Theme.getFont("small")
                            }

                            UM.Label { text: catalog.i18nc("@label", "Iterations:"); font: UM.Theme.getFont("small") }
                            UM.Label
                            {
                                text: bcPanel.hasResults ? bcPanel.convergenceIter.toString() : "—"
                                font: UM.Theme.getFont("small")
                            }
                        }
                    }

                    // Primary action
                    Cura.PrimaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Apply Optimized Infill")
                        enabled: bcPanel.hasResults
                        onClicked: UM.Controller.setProperty("ApplyModifierMeshes", true)
                    }

                    // Secondary actions
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("default_margin").width / 2

                        Cura.SecondaryButton
                        {
                            Layout.fillWidth: true
                            text: catalog.i18nc("@action:button", "Hide Stress Map")
                            onClicked: UM.Controller.setProperty("ShowStressOverlay", true)
                        }

                        Cura.SecondaryButton
                        {
                            Layout.fillWidth: true
                            text: catalog.i18nc("@action:button", "Edit Setup")
                            onClicked: UM.Controller.setProperty("GoBackToDefine", true)
                        }
                    }

                    Cura.SecondaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Clear Results")
                        onClicked: UM.Controller.setProperty("ClearResults", true)
                    }

                    Item { height: UM.Theme.getSize("default_margin").height }
                }  // reviewColumn
            }  // REVIEW phase Item

            // ══════════════════════════════════════════════════════════════
            // ERROR PHASE — error message and recovery actions
            // ══════════════════════════════════════════════════════════════
            Item
            {
                Layout.fillWidth: true
                visible: bcPanel.currentPhase === "error"
                implicitHeight: visible ? errorColumn.implicitHeight : 0

                ColumnLayout
                {
                    id: errorColumn
                    width: parent.width
                    spacing: UM.Theme.getSize("default_margin").height

                    Item { height: UM.Theme.getSize("default_margin").height }

                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: errorMsgLabel.implicitHeight + UM.Theme.getSize("default_margin").height * 2
                        color: "#442222"
                        border.color: "#aa4444"
                        border.width: UM.Theme.getSize("default_lining").width
                        radius: UM.Theme.getSize("default_radius").width

                        UM.Label
                        {
                            id: errorMsgLabel
                            anchors
                            {
                                left: parent.left; right: parent.right
                                verticalCenter: parent.verticalCenter
                                margins: UM.Theme.getSize("default_margin").width
                            }
                            wrapMode: Text.WordWrap
                            color: "#ff6666"
                            font: UM.Theme.getFont("small")
                            text: catalog.i18nc("@info:error",
                                "Analysis failed.\n\n" +
                                "Suggestions:\n" +
                                "\u2022 Try a coarser mesh resolution\n" +
                                "\u2022 Check that boundary conditions are correctly defined\n" +
                                "\u2022 Ensure supports and forces are on different faces")
                        }
                    }

                    Cura.PrimaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Try Again")
                        onClicked: UM.Controller.setProperty("RunAnalysis", true)
                    }

                    Cura.SecondaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Edit Setup")
                        onClicked: UM.Controller.setProperty("GoBackToDefine", true)
                    }

                    Item { height: UM.Theme.getSize("default_margin").height }
                }  // errorColumn
            }  // ERROR phase Item

        }  // ColumnLayout
    }  // ScrollView
}
