// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM
import Cura 1.0 as Cura

import "help" as Help

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
    readonly property int    activeTorqueIdx:  toolProperties.getValue("ActiveTorqueIndex")  ?? -1
    property string _supportJson: toolProperties.getValue("SupportListModel") ?? "[]"
    property string _forceJson:   toolProperties.getValue("ForceListModel")   ?? "[]"
    property string _torqueJson:  toolProperties.getValue("TorqueListModel")  ?? "[]"
    readonly property var    supportListModel: JSON.parse(_supportJson)
    readonly property var    forceListModel:   JSON.parse(_forceJson)
    readonly property var    torqueListModel:  JSON.parse(_torqueJson)

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
    readonly property bool   stressOverlayVisible: toolProperties.getValue("StressOverlayVisible") === true || toolProperties.getValue("StressOverlayVisible") === "true"
    readonly property bool   hasFullResults:       toolProperties.getValue("HasFullResults")       === true || toolProperties.getValue("HasFullResults") === "true"

    implicitWidth: 280 * screenScaleFactor
    // Use 80% of the application window height so the panel fills most of
    // the viewport without overflowing behind Cura's toolbars. Content scrolls.
    implicitHeight: {
        try {
            var w = UM.Controller.activeStage ? UM.Controller.activeStage.mainComponent.height : 0
            if (w > 200) return w * 0.8
        } catch(e) {}
        return 700 * screenScaleFactor
    }

    UM.I18nCatalog { id: catalog; name: "cura" }

    // Help system: content manager and popover
    Help.HelpContent
    {
        id: helpContentManager
        popoverItem: helpPopover
    }

    Help.HelpPopover
    {
        id: helpPopover
        parent: bcPanel
    }

    Help.OnboardingWizard
    {
        id: onboardingWizard
    }

    Help.ExamplesGallery
    {
        id: examplesGallery
    }

    // Show onboarding on first use
    Component.onCompleted:
    {
        if (UM.Preferences.getValue("fea_optimizer/onboarding_completed") !== true
            && UM.Preferences.getValue("fea_optimizer/onboarding_completed") !== "true")
        {
            onboardingWizard.open()
        }
    }

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
                Layout.preferredHeight: visible ? defineColumn.implicitHeight : 0

                ColumnLayout
                {
                    id: defineColumn
                    width: parent.width
                    spacing: UM.Theme.getSize("default_margin").height

                    property int editingForceIndex: -1
                    property int editingTorqueIndex: -1
                    property bool isForceEditMode: editingForceIndex >= 0
                    property bool isTorqueEditMode: editingTorqueIndex >= 0

                    // ── Step guide (visible when no BCs defined) ──────────
                    Rectangle
                    {
                        Layout.fillWidth: true
                        visible: bcPanel.supportListModel.length === 0 && bcPanel.forceListModel.length === 0 && bcPanel.torqueListModel.length === 0
                        height: visible ? stepGuideColumn.implicitHeight + UM.Theme.getSize("default_margin").height * 2 : 0
                        color: UM.Theme.getColor("detail_background")
                        radius: UM.Theme.getSize("default_radius").width

                        ColumnLayout
                        {
                            id: stepGuideColumn
                            anchors
                            {
                                left: parent.left; right: parent.right
                                verticalCenter: parent.verticalCenter
                                margins: UM.Theme.getSize("default_margin").width
                            }
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label
                            {
                                id: stepGuide
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                color: UM.Theme.getColor("text")
                                font: UM.Theme.getFont("small")
                                text: catalog.i18nc("@info",
                                    "Quick start:\n" +
                                    "1. Select 'Support / Mount' and click faces where the part is held\n" +
                                    "2. Select 'Apply Load' and click faces where forces act\n" +
                                    "3. Set the load amount and click 'Confirm Load'\n" +
                                    "4. Click 'Confirm and Optimize' to run analysis")
                            }

                            RowLayout
                            {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width

                                UM.Label
                                {
                                    text: catalog.i18nc("@action", "Show Tutorial")
                                    font: UM.Theme.getFont("small")
                                    color: UM.Theme.getColor("primary")

                                    MouseArea
                                    {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked:
                                        {
                                            onboardingWizard.currentStep = 0
                                            onboardingWizard.open()
                                        }
                                    }
                                }

                                UM.Label
                                {
                                    text: catalog.i18nc("@action", "View Examples")
                                    font: UM.Theme.getFont("small")
                                    color: UM.Theme.getColor("primary")

                                    MouseArea
                                    {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: examplesGallery.open()
                                    }
                                }
                            }
                        }
                    }

                    // ── Quick setup ───────────────────────────────────────
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 4

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Quick Setup")
                            font: UM.Theme.getFont("medium_bold")
                            Layout.fillWidth: true
                        }

                        Help.HelpTooltipIcon
                        {
                            tooltipText: catalog.i18nc("@tooltip", "One-click setups for common load scenarios like gravity, cantilever beams, and bolt-hole mounting.")
                        }
                    }

                    Rectangle
                    {
                        Layout.fillWidth: true
                        visible: (toolProperties.getValue("QuickSetupMode") ?? "") !== ""
                        height: visible ? quickModeLabel.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                        color: Qt.rgba(UM.Theme.getColor("success").r, UM.Theme.getColor("success").g, UM.Theme.getColor("success").b, 0.15)
                        radius: UM.Theme.getSize("default_radius").width

                        UM.Label
                        {
                            id: quickModeLabel
                            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: UM.Theme.getSize("default_margin").width }
                            wrapMode: Text.WordWrap
                            color: UM.Theme.getColor("text")
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
                            // Internal integer ×100 → display as mm with 2 decimals
                            // e.g. value 625 → "6.25 mm"
                            from: 50; to: 5000; value: 800; stepSize: 25
                            onValueModified: UM.Controller.setProperty("QuickHoleDiameter", value / 100.0)
                            textFromValue: function(v) { return (v / 100.0).toFixed(2) }
                            valueFromText: function(t) { return Math.round(parseFloat(t) * 100) }
                        }
                        UM.Label { text: "mm"; font: UM.Theme.getFont("small") }
                    }

                    // Hover preview toggle
                    CheckBox
                    {
                        id: hoverToggle
                        text: catalog.i18nc("@option", "Highlight face on hover")
                        checked: toolProperties.getValue("HoverPreviewEnabled") !== false
                        Layout.fillWidth: true
                        onClicked: UM.Controller.setProperty("HoverPreviewEnabled", checked)
                    }

                    // ── Tabbed BC definition (Supports / Forces / Torques) ─
                    TabBar
                    {
                        id: bcTabBar
                        Layout.fillWidth: true

                        onCurrentIndexChanged:
                        {
                            if (currentIndex === 0) UM.Controller.setProperty("Mode", "fixed")
                            else if (currentIndex === 1) UM.Controller.setProperty("Mode", "force")
                            else if (currentIndex === 2) UM.Controller.setProperty("Mode", "torque")
                        }

                        TabButton { text: catalog.i18nc("@tab", "Supports"); width: implicitWidth }
                        TabButton { text: catalog.i18nc("@tab", "Forces");   width: implicitWidth }
                        TabButton { text: catalog.i18nc("@tab", "Torques");  width: implicitWidth }
                    }

                    StackLayout
                    {
                        Layout.fillWidth: true
                        currentIndex: bcTabBar.currentIndex

                        // ── Tab 0: Supports ───────────────────────────────
                        ColumnLayout
                        {
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            // Instruction diagram with guide link
                            RowLayout
                            {
                                Layout.fillWidth: true
                                Layout.bottomMargin: UM.Theme.getSize("default_margin").height / 2

                                Image
                                {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 80 * screenScaleFactor
                                    fillMode: Image.PreserveAspectFit
                                    source: Qt.resolvedUrl("../icons/guide_support.svg")
                                }

                                Help.HelpTooltipIcon
                                {
                                    tooltipText: catalog.i18nc("@tooltip", "Click for full guide on fixed supports")
                                    guideId: "D01"
                                    helpContentManager: helpContentManager
                                }
                            }

                            // Instruction text
                            UM.Label
                            {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_medium")
                                text: catalog.i18nc("@info", "Click faces where the part is held, screwed down, or resting on a surface.")
                            }

                            UM.Label
                            {
                                Layout.fillWidth: true
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_inactive")
                                text: catalog.i18nc("@info", "Click: select face | Alt+click (Option on Mac): toggle face")
                            }

                            // Selection helper
                            RowLayout
                            {
                                Layout.fillWidth: true
                                spacing: 4

                                UM.Label
                                {
                                    text: catalog.i18nc("@label", "Selection helper")
                                    font: UM.Theme.getFont("medium_bold")
                                    Layout.fillWidth: true
                                }

                                Help.HelpTooltipIcon
                                {
                                    tooltipText: catalog.i18nc("@tooltip", "Single: one triangle. Surface: entire flat face. Hole: inside of a circular opening. Cylinder: outside of a round post.")
                                }
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

                            // Supports list
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
                                    text: catalog.i18nc("@info", "No supports defined. Click faces where the part is held.")
                                    wrapMode: Text.WordWrap
                                    font: UM.Theme.getFont("small")
                                    color: UM.Theme.getColor("text_inactive")
                                }
                            }
                        }  // Tab 0: Supports

                        // ── Tab 1: Forces ─────────────────────────────────
                        ColumnLayout
                        {
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            // Instruction diagram with guide link
                            RowLayout
                            {
                                Layout.fillWidth: true
                                Layout.bottomMargin: UM.Theme.getSize("default_margin").height / 2
                                visible: bcPanel.currentMode !== "rotate"

                                Image
                                {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 80 * screenScaleFactor
                                    fillMode: Image.PreserveAspectFit
                                    source: Qt.resolvedUrl("../icons/guide_force.svg")
                                }

                                Help.HelpTooltipIcon
                                {
                                    tooltipText: catalog.i18nc("@tooltip", "Click for full guide on applying forces")
                                    guideId: "D02"
                                    helpContentManager: helpContentManager
                                }
                            }

                            // Instruction text
                            UM.Label
                            {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_medium")
                                text: bcPanel.currentMode === "rotate"
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

                            // Rotate mode indicator
                            Rectangle
                            {
                                Layout.fillWidth: true
                                visible: bcPanel.currentMode === "rotate"
                                height: visible ? rotateModeLabel.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                                color: Qt.rgba(UM.Theme.getColor("primary").r, UM.Theme.getColor("primary").g, UM.Theme.getColor("primary").b, 0.15)
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
                                    color: UM.Theme.getColor("text")
                                    text: catalog.i18nc("@info", "Drag the rings to adjust direction. Click 'Support / Mount' or 'Apply Load' to exit.")
                                }
                            }

                            // Selection helper
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

                            // Edit mode banner
                            Rectangle
                            {
                                Layout.fillWidth: true
                                visible: defineColumn.isForceEditMode
                                height: visible ? editForceBannerRow.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                                color: Qt.rgba(UM.Theme.getColor("warning").r, UM.Theme.getColor("warning").g, UM.Theme.getColor("warning").b, 0.2)
                                radius: UM.Theme.getSize("default_radius").width

                                RowLayout
                                {
                                    id: editForceBannerRow
                                    anchors
                                    {
                                        left: parent.left; right: parent.right
                                        verticalCenter: parent.verticalCenter
                                        margins: UM.Theme.getSize("default_margin").width / 2
                                    }

                                    UM.Label
                                    {
                                        Layout.fillWidth: true
                                        text: catalog.i18nc("@info", "Editing Force %1").arg(defineColumn.editingForceIndex + 1)
                                        color: UM.Theme.getColor("warning")
                                        font: UM.Theme.getFont("small_bold")
                                    }

                                    UM.ColorImage
                                    {
                                        source: UM.Theme.getIcon("Cancel")
                                        color: UM.Theme.getColor("warning")
                                        width: UM.Theme.getSize("small_button_icon").width
                                        height: width
                                        MouseArea
                                        {
                                            anchors.fill: parent
                                            onClicked:
                                            {
                                                defineColumn.editingForceIndex = -1
                                                UM.Controller.setProperty("ActiveForceIndex", -1)
                                            }
                                        }
                                    }
                                }
                            }

                            // Force settings
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
                                    validator: DoubleValidator { bottom: 0; decimals: 1 }
                                    property real backendMagnitude: Number(toolProperties.getValue("ForceMagnitude") ?? 100)
                                    onBackendMagnitudeChanged: { if (!activeFocus) text = backendMagnitude.toFixed(1) }
                                    Component.onCompleted: text = backendMagnitude.toFixed(1)
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
                                    font.pointSize: UM.Theme.getFont("small").pointSize
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
                                    font.pointSize: UM.Theme.getFont("small").pointSize
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
                                    font.pointSize: UM.Theme.getFont("small").pointSize
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

                            // Forces list
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
                                            onClicked:
                                            {
                                                UM.Controller.setProperty("ActiveForceIndex", modelData.index)
                                                defineColumn.editingForceIndex = modelData.index
                                                defineColumn.editingTorqueIndex = -1
                                            }
                                        }
                                    }
                                }

                                UM.Label
                                {
                                    visible: bcPanel.forceListModel.length === 0
                                    width: defineColumn.width
                                    text: catalog.i18nc("@info", "No forces defined. Select faces, then confirm.")
                                    wrapMode: Text.WordWrap
                                    font: UM.Theme.getFont("small")
                                    color: UM.Theme.getColor("text_inactive")
                                }
                            }

                            // Confirm load
                            UM.Label
                            {
                                Layout.fillWidth: true
                                visible: !defineColumn.isForceEditMode
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
                                text: defineColumn.isForceEditMode
                                    ? catalog.i18nc("@action:button", "Apply Changes")
                                    : catalog.i18nc("@action:button", "Confirm Load on Selected Faces")
                                enabled: defineColumn.isForceEditMode || (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
                                onClicked:
                                {
                                    if (defineColumn.isForceEditMode)
                                    {
                                        var payload = JSON.stringify({
                                            "index": defineColumn.editingForceIndex,
                                            "magnitude": parseFloat(magnitudeField.text) || 100.0
                                        })
                                        UM.Controller.setProperty("UpdateForceAtIndex", payload)
                                        defineColumn.editingForceIndex = -1
                                        UM.Controller.setProperty("ActiveForceIndex", -1)
                                    }
                                    else
                                    {
                                        UM.Controller.setProperty("ConfirmForceGroup", true)
                                    }
                                }
                            }

                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                visible: defineColumn.isForceEditMode
                                text: catalog.i18nc("@action:button", "Cancel Edit")
                                onClicked:
                                {
                                    defineColumn.editingForceIndex = -1
                                    UM.Controller.setProperty("ActiveForceIndex", -1)
                                }
                            }
                        }  // Tab 1: Forces

                        // ── Tab 2: Torques ────────────────────────────────
                        ColumnLayout
                        {
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            // Instruction diagram with guide link
                            RowLayout
                            {
                                Layout.fillWidth: true
                                Layout.bottomMargin: UM.Theme.getSize("default_margin").height / 2
                                visible: bcPanel.currentMode !== "torque_edit"

                                Image
                                {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 80 * screenScaleFactor
                                    fillMode: Image.PreserveAspectFit
                                    source: Qt.resolvedUrl("../icons/guide_torque.svg")
                                }

                                Help.HelpTooltipIcon
                                {
                                    tooltipText: catalog.i18nc("@tooltip", "Click for full guide on applying torques")
                                    guideId: "D03"
                                    helpContentManager: helpContentManager
                                }
                            }

                            // Instruction text
                            UM.Label
                            {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_medium")
                                text: bcPanel.currentMode === "torque_edit"
                                    ? catalog.i18nc("@info", "Drag the rotation rings to adjust the torque axis direction.")
                                    : catalog.i18nc("@info", "Click faces where a rotational load (twist) is applied. Then set the torque amount below.")
                            }

                            UM.Label
                            {
                                Layout.fillWidth: true
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_inactive")
                                text: catalog.i18nc("@info", "Click: select face | Alt+click (Option on Mac): toggle face")
                            }

                            // Torque axis edit mode indicator
                            Rectangle
                            {
                                Layout.fillWidth: true
                                visible: bcPanel.currentMode === "torque_edit"
                                height: visible ? torqueEditColumn.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                                color: Qt.rgba(UM.Theme.getColor("secondary").r, UM.Theme.getColor("secondary").g, UM.Theme.getColor("secondary").b, 0.15)
                                radius: UM.Theme.getSize("default_radius").width

                                ColumnLayout
                                {
                                    id: torqueEditColumn
                                    anchors
                                    {
                                        left: parent.left
                                        right: parent.right
                                        verticalCenter: parent.verticalCenter
                                        margins: UM.Theme.getSize("default_margin").width
                                    }
                                    spacing: UM.Theme.getSize("default_margin").height / 2

                                    UM.Label
                                    {
                                        Layout.fillWidth: true
                                        wrapMode: Text.WordWrap
                                        color: UM.Theme.getColor("text")
                                        font: UM.Theme.getFont("small_bold")
                                        text: catalog.i18nc("@info", "Editing Torque Axis — Drag the rings to rotate the axis direction")
                                    }

                                    UM.Label
                                    {
                                        Layout.fillWidth: true
                                        font: UM.Theme.getFont("small")
                                        color: UM.Theme.getColor("text_medium")
                                        text: {
                                            var idx = bcPanel.activeTorqueIdx
                                            if (idx >= 0 && idx < bcPanel.torqueListModel.length) {
                                                var tg = bcPanel.torqueListModel[idx]
                                                return catalog.i18nc("@info", "Current axis: [%1, %2, %3]")
                                                    .arg(tg.axisX !== undefined ? tg.axisX.toFixed(3) : "?")
                                                    .arg(tg.axisY !== undefined ? tg.axisY.toFixed(3) : "?")
                                                    .arg(tg.axisZ !== undefined ? tg.axisZ.toFixed(3) : "?")
                                            }
                                            return ""
                                        }
                                    }

                                    Cura.SecondaryButton
                                    {
                                        Layout.fillWidth: true
                                        text: catalog.i18nc("@action:button", "Done Editing Axis")
                                        onClicked:
                                        {
                                            UM.Controller.setProperty("ActiveTorqueIndex", -1)
                                            defineColumn.editingTorqueIndex = -1
                                        }
                                    }
                                }
                            }

                            // Selection helper
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

                            // Edit mode banner
                            Rectangle
                            {
                                Layout.fillWidth: true
                                visible: defineColumn.isTorqueEditMode
                                height: visible ? editTorqueBannerRow.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                                color: Qt.rgba(UM.Theme.getColor("warning").r, UM.Theme.getColor("warning").g, UM.Theme.getColor("warning").b, 0.2)
                                radius: UM.Theme.getSize("default_radius").width

                                RowLayout
                                {
                                    id: editTorqueBannerRow
                                    anchors
                                    {
                                        left: parent.left; right: parent.right
                                        verticalCenter: parent.verticalCenter
                                        margins: UM.Theme.getSize("default_margin").width / 2
                                    }

                                    UM.Label
                                    {
                                        Layout.fillWidth: true
                                        text: catalog.i18nc("@info", "Editing Torque %1").arg(defineColumn.editingTorqueIndex + 1)
                                        color: UM.Theme.getColor("warning")
                                        font: UM.Theme.getFont("small_bold")
                                    }

                                    UM.ColorImage
                                    {
                                        source: UM.Theme.getIcon("Cancel")
                                        color: UM.Theme.getColor("warning")
                                        width: UM.Theme.getSize("small_button_icon").width
                                        height: width
                                        MouseArea
                                        {
                                            anchors.fill: parent
                                            onClicked:
                                            {
                                                defineColumn.editingTorqueIndex = -1
                                                UM.Controller.setProperty("ActiveTorqueIndex", -1)
                                            }
                                        }
                                    }
                                }
                            }

                            // Torque settings
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
                                    id: torqueMagnitudeField
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
                                visible: !defineColumn.isTorqueEditMode && (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
                                text: catalog.i18nc("@info", "%1 face(s) selected for torque. The torque axis will be the average surface normal.").arg(toolProperties.getValue("CurrentSelectionCount") ?? 0)
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text_medium")
                                wrapMode: Text.WordWrap
                            }

                            // Torques list
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
                                        height: torqueRowLayout.implicitHeight + UM.Theme.getSize("default_margin").height
                                        color: bcPanel.activeTorqueIdx === modelData.index
                                            ? Qt.rgba(UM.Theme.getColor("secondary").r, UM.Theme.getColor("secondary").g, UM.Theme.getColor("secondary").b, 0.5)
                                            : defineColumn.editingTorqueIndex === modelData.index
                                                ? UM.Theme.getColor("primary")
                                                : UM.Theme.getColor("main_background")
                                        border.color: UM.Theme.getColor("lining")
                                        border.width: UM.Theme.getSize("default_lining").width
                                        radius: UM.Theme.getSize("default_radius").width

                                        ColumnLayout
                                        {
                                            id: torqueRowLayout
                                            anchors.fill: parent
                                            anchors.margins: UM.Theme.getSize("default_margin").width / 2
                                            spacing: 2

                                            RowLayout
                                            {
                                                Layout.fillWidth: true

                                                UM.Label
                                                {
                                                    id: torqueRowLabel
                                                    Layout.fillWidth: true
                                                    text: modelData.label
                                                    color: UM.Theme.getColor("text")
                                                    elide: Text.ElideRight
                                                }

                                                UM.Label
                                                {
                                                    text: catalog.i18nc("@action", "Edit Axis")
                                                    font: UM.Theme.getFont("small")
                                                    color: UM.Theme.getColor("primary")

                                                    MouseArea
                                                    {
                                                        anchors.fill: parent
                                                        onClicked:
                                                        {
                                                            defineColumn.editingForceIndex = -1
                                                            UM.Controller.setProperty("ActiveForceIndex", -1)
                                                            UM.Controller.setProperty("ActiveTorqueIndex", modelData.index)
                                                        }
                                                    }
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

                                            // Show axis values inline when this torque is being edited
                                            UM.Label
                                            {
                                                visible: bcPanel.activeTorqueIdx === modelData.index
                                                Layout.fillWidth: true
                                                font: UM.Theme.getFont("small")
                                                color: UM.Theme.getColor("text_medium")
                                                text: catalog.i18nc("@info", "Axis: [%1, %2, %3]")
                                                    .arg(modelData.axisX !== undefined ? modelData.axisX.toFixed(3) : "?")
                                                    .arg(modelData.axisY !== undefined ? modelData.axisY.toFixed(3) : "?")
                                                    .arg(modelData.axisZ !== undefined ? modelData.axisZ.toFixed(3) : "?")
                                            }
                                        }

                                        MouseArea
                                        {
                                            anchors.fill: parent
                                            z: -1
                                            onClicked:
                                            {
                                                defineColumn.editingTorqueIndex = modelData.index
                                                defineColumn.editingForceIndex = -1
                                                UM.Controller.setProperty("ActiveForceIndex", -1)
                                            }
                                        }
                                    }
                                }

                                UM.Label
                                {
                                    visible: bcPanel.torqueListModel.length === 0
                                    width: defineColumn.width
                                    text: catalog.i18nc("@info", "No torques defined. Select faces, then confirm.")
                                    wrapMode: Text.WordWrap
                                    font: UM.Theme.getFont("small")
                                    color: UM.Theme.getColor("text_inactive")
                                }
                            }

                            // Confirm torque
                            Cura.PrimaryButton
                            {
                                Layout.fillWidth: true
                                text: defineColumn.isTorqueEditMode
                                    ? catalog.i18nc("@action:button", "Apply Changes")
                                    : catalog.i18nc("@action:button", "Confirm Torque on Selected Faces")
                                enabled: defineColumn.isTorqueEditMode || (toolProperties.getValue("CurrentSelectionCount") ?? 0) > 0
                                onClicked:
                                {
                                    if (defineColumn.isTorqueEditMode)
                                    {
                                        var payload = JSON.stringify({
                                            "index": defineColumn.editingTorqueIndex,
                                            "magnitude": parseFloat(torqueMagnitudeField.text) || 1.0
                                        })
                                        UM.Controller.setProperty("UpdateTorqueAtIndex", payload)
                                        defineColumn.editingTorqueIndex = -1
                                    }
                                    else
                                    {
                                        UM.Controller.setProperty("ConfirmTorqueGroup", true)
                                    }
                                }
                            }

                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                visible: defineColumn.isTorqueEditMode
                                text: catalog.i18nc("@action:button", "Cancel Edit")
                                onClicked:
                                {
                                    defineColumn.editingForceIndex = -1
                                    defineColumn.editingTorqueIndex = -1
                                    UM.Controller.setProperty("ActiveForceIndex", -1)
                                    UM.Controller.setProperty("ActiveTorqueIndex", -1)
                                }
                            }
                        }  // Tab 2: Torques

                    }  // StackLayout

                    // ── Confirm and Optimize button ───────────────────────
                    Item { height: UM.Theme.getSize("default_margin").height }

                    Cura.PrimaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Confirm and Optimize")
                        enabled: bcPanel.supportListModel.length > 0 && (bcPanel.forceListModel.length > 0 || bcPanel.torqueListModel.length > 0)
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
                Layout.preferredHeight: visible ? optimizeColumn.implicitHeight : 0

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
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 4

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Material")
                            font: UM.Theme.getFont("medium_bold")
                            Layout.fillWidth: true
                        }

                        Help.HelpTooltipIcon
                        {
                            tooltipText: catalog.i18nc("@tooltip", "Your printing material determines stiffness (E) and strength. Stiffer materials resist bending; stronger materials resist breaking.")
                            guideId: "O01"
                            helpContentManager: helpContentManager
                        }
                    }

                    ComboBox
                    {
                        id: materialSelector
                        Layout.fillWidth: true
                        model: ["PLA", "ABS", "PETG", "Nylon", "PC", "TPU_95A", "CF_Nylon", "CF_PET"]
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
                        text: toolProperties.getValue("MaterialSummary") ?? ""
                        visible: text !== ""
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text_inactive")
                        wrapMode: Text.WordWrap
                    }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@info", "Select your printing material. This determines stiffness and strength for analysis.")
                        color: UM.Theme.getColor("text_medium")
                        font: UM.Theme.getFont("small")
                        wrapMode: Text.WordWrap
                    }

                    // Infill pattern
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 4

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Infill Pattern")
                            font: UM.Theme.getFont("medium_bold")
                            Layout.fillWidth: true
                        }

                        Help.HelpTooltipIcon
                        {
                            tooltipText: catalog.i18nc("@tooltip", "Determines how stiffness scales with density. Gyroid (recommended) is isotropic. Grid/Lines scale differently.")
                            guideId: "O09"
                            helpContentManager: helpContentManager
                        }
                    }

                    ComboBox
                    {
                        id: infillPatternSelector
                        Layout.fillWidth: true
                        model: [
                            { value: "gyroid",       text: "Gyroid (recommended)" },
                            { value: "grid",         text: "Grid" },
                            { value: "lines",        text: "Lines" },
                            { value: "triangles",    text: "Triangles" },
                            { value: "cubic",        text: "Cubic" },
                            { value: "honeycomb",    text: "Honeycomb" },
                            { value: "trihexagon",   text: "Tri-Hexagon" },
                            { value: "tetrahedral",  text: "Octet" },
                            { value: "quarter_cubic", text: "Quarter Cubic" },
                            { value: "concentric",   text: "Concentric" },
                            { value: "zigzag",       text: "Zig Zag" },
                            { value: "cross",        text: "Cross" },
                            { value: "cross_3d",     text: "Cross 3D" },
                            { value: "cubicsubdiv",  text: "Cubic Subdivision" },
                            { value: "lightning",    text: "Lightning" }
                        ]
                        textRole: "text"
                        valueRole: "value"
                        currentIndex: {
                            var pat = toolProperties.getValue("InfillPattern") ?? "gyroid"
                            for (var i = 0; i < model.length; i++) {
                                if (model[i].value === pat) return i
                            }
                            return 0
                        }
                        onActivated: function(index) {
                            UM.Controller.setProperty("InfillPattern", model[index].value)
                        }
                    }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@info", "Pattern used in infill zones. Auto-detected from your print profile. Affects how stiffness scales with density.")
                        color: UM.Theme.getColor("text_medium")
                        font: UM.Theme.getFont("small")
                        wrapMode: Text.WordWrap
                    }

                    // Safety factor
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 4

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Safety Factor")
                            font: UM.Theme.getFont("medium_bold")
                            Layout.fillWidth: true
                        }

                        Help.HelpTooltipIcon
                        {
                            tooltipText: catalog.i18nc("@tooltip", "Ratio of material strength to max allowed stress. 2.0x means the part can handle 2x the applied load. Typical: 1.5x lightweight, 2.0x general, 3.0x safety-critical.")
                            guideId: "O12"
                            helpContentManager: helpContentManager
                        }
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
                            valueFromText: function(t) { return Math.round(parseFloat(t.replace(/[^\d.]/g, "")) * 10) }
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
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 4

                        UM.Label
                        {
                            text: catalog.i18nc("@label", "Mesh Quality")
                            font: UM.Theme.getFont("medium_bold")
                            Layout.fillWidth: true
                        }

                        Help.HelpTooltipIcon
                        {
                            tooltipText: catalog.i18nc("@tooltip", "Controls mesh density. Fast: ~5K elements (quick). Balanced: ~15K (good tradeoff). Precise: ~50K (accurate but slower). Use Precise for thin-walled parts.")
                            guideId: "O13"
                            helpContentManager: helpContentManager
                        }
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
                        id: advancedSection
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

                                Help.HelpTooltipIcon
                                {
                                    tooltipText: catalog.i18nc("@tooltip", "Fine-tune min/max infill density, density steps, iteration count, layer bonding, and optimization method.")
                                }

                                UM.ColorImage
                                {
                                    source: advancedSection.expanded ? UM.Theme.getIcon("ChevronSingleUp") : UM.Theme.getIcon("ChevronSingleDown")
                                    color: UM.Theme.getColor("text_medium")
                                    width: UM.Theme.getSize("small_button_icon").width
                                    height: width
                                }
                            }

                            MouseArea
                            {
                                anchors.fill: parent
                                onClicked: advancedSection.expanded = !advancedSection.expanded
                            }
                        }

                        // Expandable content (participates in ColumnLayout)
                        GridLayout
                        {
                            Layout.fillWidth: true
                            visible: advancedSection.expanded
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
                            SpinBox { from: 1; to: 50; value: 20; stepSize: 1; Layout.fillWidth: true
                                onValueModified: UM.Controller.setProperty("MaxIterations", value) }

                            UM.Label { text: catalog.i18nc("@label", "Layer bonding (%)"); font: UM.Theme.getFont("small") }
                            SpinBox { from: 10; to: 100; value: 50; stepSize: 5; Layout.fillWidth: true
                                onValueModified: UM.Controller.setProperty("BondingCoeff", value) }

                            UM.Label { text: catalog.i18nc("@label", "Optimization"); font: UM.Theme.getFont("small") }
                            ComboBox
                            {
                                Layout.fillWidth: true
                                model: [
                                    { value: "heuristic", text: catalog.i18nc("@option", "Heuristic (default)") },
                                    { value: "oc",        text: catalog.i18nc("@option", "SIMP OC (advanced)") }
                                ]
                                textRole: "text"
                                valueRole: "value"
                                currentIndex: (toolProperties.getValue("OptimizationMethod") ?? "heuristic") === "oc" ? 1 : 0
                                onActivated: function(index) {
                                    UM.Controller.setProperty("OptimizationMethod", model[index].value)
                                }
                            }

                            UM.Label
                            {
                                text: catalog.i18nc("@label", "Target volume (%)")
                                font: UM.Theme.getFont("small")
                                visible: (toolProperties.getValue("OptimizationMethod") ?? "heuristic") === "oc"
                            }
                            SpinBox
                            {
                                from: 10; to: 90; value: 50; stepSize: 5
                                Layout.fillWidth: true
                                visible: (toolProperties.getValue("OptimizationMethod") ?? "heuristic") === "oc"
                                onValueModified: UM.Controller.setProperty("VolumeFraction", value)
                            }
                        }
                    }

                    // Dependency warning
                    Rectangle
                    {
                        Layout.fillWidth: true
                        visible: !toolProperties.getValue("DepsAvailable")
                        height: visible ? depsLabel.implicitHeight + UM.Theme.getSize("default_margin").height * 2 : 0
                        color: Qt.rgba(UM.Theme.getColor("error").r, UM.Theme.getColor("error").g, UM.Theme.getColor("error").b, 0.2)
                        radius: UM.Theme.getSize("default_radius").width

                        ColumnLayout
                        {
                            id: depsLabel
                            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: UM.Theme.getSize("default_margin").width }

                            UM.Label
                            {
                                Layout.fillWidth: true
                                text: catalog.i18nc("@info:warning", "Required libraries not installed. Click Install, then restart Cura.")
                                color: UM.Theme.getColor("error")
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
                Layout.preferredHeight: visible ? runningColumn.implicitHeight : 0

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
                Layout.preferredHeight: visible ? reviewColumn.implicitHeight : 0

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

                    // Note shown when results are restored from a saved project file
                    Rectangle
                    {
                        Layout.fillWidth: true
                        visible: bcPanel.hasResults && !bcPanel.hasFullResults
                        height: visible ? restoredNoteLabel.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                        color: Qt.rgba(UM.Theme.getColor("warning").r, UM.Theme.getColor("warning").g, UM.Theme.getColor("warning").b, 0.15)
                        radius: UM.Theme.getSize("default_radius").width

                        UM.Label
                        {
                            id: restoredNoteLabel
                            anchors
                            {
                                left: parent.left; right: parent.right
                                verticalCenter: parent.verticalCenter
                                margins: UM.Theme.getSize("default_margin").width / 2
                            }
                            text: catalog.i18nc("@info",
                                "Results loaded from project file. Re-run analysis to enable stress map and modifier meshes.")
                            color: UM.Theme.getColor("text_inactive")
                            font: UM.Theme.getFont("small")
                            wrapMode: Text.WordWrap
                        }
                    }

                    // Mesh quality indicator
                    Rectangle
                    {
                        Layout.fillWidth: true
                        visible: bcPanel.hasResults
                        height: visible ? meshQualityRow.implicitHeight + UM.Theme.getSize("default_margin").height / 2 : 0
                        radius: UM.Theme.getSize("default_radius").width
                        color: {
                            var q = toolProperties.getValue("MeshQuality") ?? ""
                            if (q === "high") return Qt.rgba(UM.Theme.getColor("success").r, UM.Theme.getColor("success").g, UM.Theme.getColor("success").b, 0.15)
                            if (q === "medium") return Qt.rgba(UM.Theme.getColor("warning").r, UM.Theme.getColor("warning").g, UM.Theme.getColor("warning").b, 0.15)
                            if (q === "low") return Qt.rgba(UM.Theme.getColor("error").r, UM.Theme.getColor("error").g, UM.Theme.getColor("error").b, 0.15)
                            return "transparent"
                        }

                        RowLayout
                        {
                            id: meshQualityRow
                            anchors
                            {
                                left: parent.left; right: parent.right
                                verticalCenter: parent.verticalCenter
                                margins: UM.Theme.getSize("default_margin").width / 2
                            }
                            spacing: UM.Theme.getSize("default_margin").width / 2

                            UM.Label
                            {
                                text: {
                                    var q = toolProperties.getValue("MeshQuality") ?? ""
                                    if (q === "high") return "HIGH ●"
                                    if (q === "medium") return "MED ◐"
                                    if (q === "low") return "LOW ○"
                                    return ""
                                }
                                color: {
                                    var q = toolProperties.getValue("MeshQuality") ?? ""
                                    if (q === "high") return UM.Theme.getColor("success")
                                    if (q === "medium") return UM.Theme.getColor("warning")
                                    if (q === "low") return UM.Theme.getColor("error")
                                    return UM.Theme.getColor("text_inactive")
                                }
                                font.pointSize: UM.Theme.getFont("large").pointSize
                            }

                            ColumnLayout
                            {
                                Layout.fillWidth: true
                                spacing: 0

                                UM.Label
                                {
                                    text: {
                                        var q = toolProperties.getValue("MeshQuality") ?? ""
                                        if (q === "high") return catalog.i18nc("@info", "High confidence — Gmsh tetrahedralization")
                                        if (q === "medium") return catalog.i18nc("@info", "Medium confidence — fallback mesh method")
                                        if (q === "low") return catalog.i18nc("@info", "Low confidence — approximate mesh, increase safety margin")
                                        return ""
                                    }
                                    font: UM.Theme.getFont("small")
                                    color: UM.Theme.getColor("text")
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }

                                UM.Label
                                {
                                    property var _meshWarnings: JSON.parse(toolProperties.getValue("MeshWarnings") ?? "[]")
                                    visible: _meshWarnings.length > 0
                                    text: _meshWarnings.join("\n")
                                    font: UM.Theme.getFont("small")
                                    color: UM.Theme.getColor("warning")
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }

                    // Safety verdict chip
                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: visible ? verdictRow.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                        radius: UM.Theme.getSize("default_radius").width
                        color: {
                            var v = bcPanel.safetyVerdict
                            if (v === "unsafe")       return Qt.rgba(UM.Theme.getColor("error").r, UM.Theme.getColor("error").g, UM.Theme.getColor("error").b, 0.35)
                            if (v === "marginal")     return Qt.rgba(UM.Theme.getColor("warning").r, UM.Theme.getColor("warning").g, UM.Theme.getColor("warning").b, 0.3)
                            if (v === "safe")         return Qt.rgba(UM.Theme.getColor("success").r, UM.Theme.getColor("success").g, UM.Theme.getColor("success").b, 0.3)
                            if (v === "conservative") return Qt.rgba(UM.Theme.getColor("primary").r, UM.Theme.getColor("primary").g, UM.Theme.getColor("primary").b, 0.25)
                            return UM.Theme.getColor("main_background")
                        }

                        RowLayout
                        {
                            id: verdictRow
                            anchors
                            {
                                left: parent.left; right: parent.right
                                verticalCenter: parent.verticalCenter
                                margins: UM.Theme.getSize("default_margin").width
                            }
                            spacing: 4

                            UM.Label
                            {
                                id: verdictLabel
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                color: UM.Theme.getColor("button_text")
                                font: UM.Theme.getFont("medium_bold")
                                text: {
                                    var v = bcPanel.safetyVerdict
                                    if (v === "unsafe")       return "\u2717 " + catalog.i18nc("@info", "Unsafe: Part may fail under this load. Increase max infill or redesign.")
                                    if (v === "marginal")     return "\u26a0 " + catalog.i18nc("@info", "Marginal: Safety is borderline. Consider increasing max infill density.")
                                    if (v === "safe")         return "\u2713 " + catalog.i18nc("@info", "Safe: Part should handle this load safely with optimized infill.")
                                    if (v === "conservative") return "\u2139 " + catalog.i18nc("@info", "Conservative: Part is over-engineered. You could reduce max infill to save material.")
                                    return catalog.i18nc("@info", "Analysis complete.")
                                }
                            }

                            Help.HelpTooltipIcon
                            {
                                tooltipText: catalog.i18nc("@tooltip", "Compares peak stress to material yield strength / safety factor. SAFE: well below limit. MARGINAL: close. UNSAFE: exceeds limit. CONSERVATIVE: very low stress.")
                                guideId: "R01"
                                helpContentManager: helpContentManager
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
                            columns: 3
                            columnSpacing: UM.Theme.getSize("default_margin").width
                            rowSpacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: catalog.i18nc("@label", "Max Stress:"); font: UM.Theme.getFont("small") }
                            UM.Label
                            {
                                text: bcPanel.hasResults ? bcPanel.maxStress.toFixed(1) + " MPa" : "\u2014"
                                font: UM.Theme.getFont("small")
                                Layout.fillWidth: true
                            }
                            Help.HelpTooltipIcon
                            {
                                tooltipText: catalog.i18nc("@tooltip", "Peak von Mises stress. Combines tension, compression, and shear into one value. If this exceeds yield strength / safety factor, the part may fail.")
                            }

                            UM.Label { text: catalog.i18nc("@label", "Min Stress:"); font: UM.Theme.getFont("small") }
                            UM.Label
                            {
                                text: bcPanel.hasResults ? bcPanel.minStress.toFixed(1) + " MPa" : "\u2014"
                                font: UM.Theme.getFont("small")
                                Layout.fillWidth: true
                            }
                            Item { width: 20; height: 20 }

                            UM.Label { text: catalog.i18nc("@label", "Safety Factor:"); font: UM.Theme.getFont("small") }
                            UM.Label
                            {
                                text: bcPanel.hasResults ? bcPanel.safetyFactorResult.toFixed(2) : "\u2014"
                                font: UM.Theme.getFont("small")
                                Layout.fillWidth: true
                            }
                            Help.HelpTooltipIcon
                            {
                                tooltipText: catalog.i18nc("@tooltip", "Actual ratio of yield strength to peak stress. Values >1.0 mean the part should survive. This is the result, not the target you set.")
                            }

                            UM.Label { text: catalog.i18nc("@label", "Iterations:"); font: UM.Theme.getFont("small") }
                            UM.Label
                            {
                                text: bcPanel.hasResults ? bcPanel.convergenceIter.toString() : "\u2014"
                                font: UM.Theme.getFont("small")
                                Layout.fillWidth: true
                            }
                            Help.HelpTooltipIcon
                            {
                                tooltipText: catalog.i18nc("@tooltip", "Optimization passes completed. If this equals the maximum allowed, the result may not be fully converged.")
                            }
                        }
                    }

                    // Primary action
                    Cura.PrimaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Apply Optimized Infill")
                        enabled: bcPanel.hasResults && bcPanel.hasFullResults
                        onClicked: UM.Controller.setProperty("ApplyModifierMeshes", true)
                    }

                    // Secondary actions
                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: 4

                        Cura.SecondaryButton
                        {
                            Layout.fillWidth: true
                            enabled: bcPanel.hasFullResults
                            text: bcPanel.stressOverlayVisible
                                ? catalog.i18nc("@action:button", "Hide Stress Map")
                                : catalog.i18nc("@action:button", "Show Stress Map")
                            onClicked: UM.Controller.setProperty("ShowStressOverlay", true)
                        }

                        Help.HelpTooltipIcon
                        {
                            tooltipText: catalog.i18nc("@tooltip", "Overlays a color map showing stress distribution. Dark purple = low stress, yellow = high stress (viridis colorblind-safe gradient).")
                            guideId: "R06"
                            helpContentManager: helpContentManager
                        }
                    }

                    RowLayout
                    {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("default_margin").width / 2

                        Cura.SecondaryButton
                        {
                            Layout.fillWidth: true
                            text: catalog.i18nc("@action:button", "Edit Boundary Conditions")
                            onClicked: UM.Controller.setProperty("GoBackToDefine", true)
                        }

                        Cura.SecondaryButton
                        {
                            Layout.fillWidth: true
                            text: catalog.i18nc("@action:button", "Edit Analysis Settings")
                            onClicked: UM.Controller.setProperty("GoBackToOptimize", true)
                        }
                    }

                    Cura.SecondaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Clear Results")
                        onClicked: clearConfirmDialog.open()
                    }

                    UM.Dialog
                    {
                        id: clearConfirmDialog
                        title: catalog.i18nc("@title:dialog", "Clear FEA Results")
                        width: 400 * screenScaleFactor

                        UM.Label
                        {
                            width: parent.width
                            text: catalog.i18nc("@info:question", "This will remove all FEA results and modifier meshes. Are you sure?")
                            wrapMode: Text.WordWrap
                        }

                        rightButtons:
                        [
                            Cura.PrimaryButton
                            {
                                text: catalog.i18nc("@action:button", "Clear")
                                onClicked:
                                {
                                    clearConfirmDialog.accept()
                                    UM.Controller.setProperty("ClearResults", true)
                                }
                            },
                            Cura.SecondaryButton
                            {
                                text: catalog.i18nc("@action:button", "Cancel")
                                onClicked: clearConfirmDialog.reject()
                            }
                        ]
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
                Layout.preferredHeight: visible ? errorColumn.implicitHeight : 0

                ColumnLayout
                {
                    id: errorColumn
                    width: parent.width
                    spacing: UM.Theme.getSize("default_margin").height

                    Item { height: UM.Theme.getSize("default_margin").height }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: toolProperties.getValue("ErrorMessage") ?? ""
                        visible: text !== ""
                        color: UM.Theme.getColor("error")
                        font: UM.Theme.getFont("default")
                        wrapMode: Text.WordWrap
                        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                    }

                    Rectangle
                    {
                        Layout.fillWidth: true
                        height: errorMsgLabel.implicitHeight + UM.Theme.getSize("default_margin").height * 2
                        color: Qt.rgba(UM.Theme.getColor("error").r, UM.Theme.getColor("error").g, UM.Theme.getColor("error").b, 0.2)
                        border.color: UM.Theme.getColor("error")
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
                            color: UM.Theme.getColor("error")
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
