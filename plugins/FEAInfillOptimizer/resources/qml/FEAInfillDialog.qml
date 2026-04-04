// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM
import Cura 1.0 as Cura

UM.Dialog
{
    id: feaDialog
    title: catalog.i18nc("@title:window", "FEA Infill Optimizer")

    width: UM.Theme.getSize("large_popup_dialog").width
    height: UM.Theme.getSize("large_popup_dialog").height
    minimumWidth: 480 * screenScaleFactor
    minimumHeight: 600 * screenScaleFactor

    property var manager  // bound to FEAInfillExtension Python object from caller
    backgroundColor: UM.Theme.getColor("main_background")

    // Scene node list model (populated from Python on dialog open)
    ListModel { id: sceneNodeListModel }

    function refreshNodeList()
    {
        sceneNodeListModel.clear()
        if (!manager) return

        var nodes = manager.getSceneNodes()
        if (!nodes) return

        for (var i = 0; i < nodes.length; i++)
        {
            sceneNodeListModel.append({"name": nodes[i].name, "nodeId": nodes[i].id})
        }

        // If a node was pre-selected (from BC tool's "Confirm and Optimize"),
        // find and select it in the ComboBox.
        var preKey = manager.preselectedNodeKey
        if (preKey && preKey !== "")
        {
            for (var j = 0; j < sceneNodeListModel.count; j++)
            {
                if (sceneNodeListModel.get(j).nodeId === preKey)
                {
                    nodeSelector.currentIndex = j
                    return
                }
            }
        }

        // Default: select first item if any
        if (sceneNodeListModel.count > 0)
        {
            nodeSelector.currentIndex = 0
        }
    }

    onVisibleChanged:
    {
        if (visible) refreshNodeList()
    }

    Item
    {
        anchors.fill: parent

        UM.I18nCatalog { id: catalog; name: "cura" }

        ScrollView
        {
            anchors.fill: parent
            anchors.margins: UM.Theme.getSize("default_margin").width
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout
            {
                width: feaDialog.width - 2 * UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").height

            // ── Dependency warning banner ────────────────────────────────────
            Rectangle
            {
                id: depsBanner
                Layout.fillWidth: true
                visible: manager !== undefined && !manager.depsAvailable
                height: visible ? depsBannerContent.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                color: UM.Theme.getColor("warning_background")
                radius: UM.Theme.getSize("default_radius").width
                border.color: UM.Theme.getColor("warning_border")
                border.width: UM.Theme.getSize("default_lining").width

                RowLayout
                {
                    id: depsBannerContent
                    anchors
                    {
                        left: parent.left
                        right: parent.right
                        verticalCenter: parent.verticalCenter
                        margins: UM.Theme.getSize("default_margin").width
                    }
                    spacing: UM.Theme.getSize("default_margin").width

                    UM.ColorImage
                    {
                        source: UM.Theme.getIcon("Warning")
                        color: UM.Theme.getColor("warning_icon")
                        width: UM.Theme.getSize("section_icon").width
                        height: UM.Theme.getSize("section_icon").height
                    }

                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@info:warning",
                            "Required Python libraries are missing. " +
                            "Click 'Install Dependencies' below, then restart Cura.")
                        wrapMode: Text.WordWrap
                        color: UM.Theme.getColor("warning_text")
                    }

                    Cura.SecondaryButton
                    {
                        text: catalog.i18nc("@action:button", "Install Dependencies")
                        onClicked: { if (manager) manager.installDependencies() }
                    }
                }
            }

            // ── Target model ────────────────────────────────────────────────
            UM.Label
            {
                text: catalog.i18nc("@label", "Target Model")
                font: UM.Theme.getFont("medium_bold")
            }

            UM.Label
            {
                visible: sceneNodeListModel.count === 0
                Layout.fillWidth: true
                text: catalog.i18nc("@info", "No models loaded. Load a model first, then re-open this dialog.")
                color: UM.Theme.getColor("text_medium")
                wrapMode: Text.WordWrap
            }

            Cura.ComboBox
            {
                id: nodeSelector
                Layout.fillWidth: true
                visible: sceneNodeListModel.count > 0
                textRole: "name"
                valueRole: "nodeId"
                model: sceneNodeListModel
            }

            // ── Material preset ─────────────────────────────────────────────
            UM.Label
            {
                text: catalog.i18nc("@label", "Material Preset")
                font: UM.Theme.getFont("medium_bold")
            }

            Cura.ComboBox
            {
                id: materialSelector
                Layout.fillWidth: true
                model: ListModel
                {
                    ListElement { text: "PLA";      value: "PLA"      }
                    ListElement { text: "ABS";      value: "ABS"      }
                    ListElement { text: "PETG";     value: "PETG"     }
                    ListElement { text: "Nylon";    value: "Nylon"    }
                    ListElement { text: "PC";       value: "PC"       }
                    ListElement { text: "TPU 95A";  value: "TPU_95A"  }
                    ListElement { text: "CF-Nylon"; value: "CF_Nylon" }
                }
                textRole: "text"
                valueRole: "value"
            }

            UM.Label
            {
                Layout.fillWidth: true
                text: catalog.i18nc("@info", "Select your printing material. This determines stiffness and strength for analysis.")
                color: UM.Theme.getColor("text_medium")
                font: UM.Theme.getFont("small")
                wrapMode: Text.WordWrap
            }

            // ── Boundary condition summary ───────────────────────────────────
            UM.Label
            {
                text: catalog.i18nc("@label", "Loads and Supports")
                font: UM.Theme.getFont("medium_bold")
            }

            UM.Label
            {
                Layout.fillWidth: true
                text: catalog.i18nc("@info", "Define where your part is held and where forces act before running analysis.")
                color: UM.Theme.getColor("text_medium")
                font: UM.Theme.getFont("small")
                wrapMode: Text.WordWrap
            }

            Rectangle
            {
                Layout.fillWidth: true
                height: bcSummaryLabel.implicitHeight + UM.Theme.getSize("default_margin").height
                color: UM.Theme.getColor("main_background")
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width

                UM.Label
                {
                    id: bcSummaryLabel
                    anchors
                    {
                        left: parent.left
                        right: parent.right
                        verticalCenter: parent.verticalCenter
                        margins: UM.Theme.getSize("default_margin").width
                    }
                    text: (manager !== undefined && nodeSelector.currentValue !== undefined)
                        ? manager.getBCSummary(nodeSelector.currentValue)
                        : catalog.i18nc("@info", "No model selected.")
                    wrapMode: Text.WordWrap
                    color: UM.Theme.getColor("text")
                }
            }

            Cura.SecondaryButton
            {
                Layout.fillWidth: true
                text: catalog.i18nc("@action:button", "Open BC Tool to Define Loads")
                visible: manager !== undefined && nodeSelector.currentValue !== undefined
                onClicked:
                {
                    UM.Controller.setActiveTool("FEAInfillOptimizer")
                    feaDialog.visible = false
                }
            }

            // ── Analysis settings ────────────────────────────────────────────
            UM.Label
            {
                text: catalog.i18nc("@label", "Analysis Settings")
                font: UM.Theme.getFont("medium_bold")
            }

            GridLayout
            {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: UM.Theme.getSize("default_margin").width
                rowSpacing: UM.Theme.getSize("default_margin").height / 2

                UM.Label { text: catalog.i18nc("@label", "Lightest area infill (%)") }
                SpinBox
                {
                    id: minDensitySpinBox
                    from: 5; to: 90; value: 10; stepSize: 5
                    Layout.fillWidth: true
                }

                UM.Label { text: catalog.i18nc("@label", "Heaviest area infill (%)") }
                SpinBox
                {
                    id: maxDensitySpinBox
                    from: 10; to: 100; value: 80; stepSize: 5
                    Layout.fillWidth: true
                }

                UM.Label { text: catalog.i18nc("@label", "Density steps") }
                SpinBox
                {
                    id: nZonesSpinBox
                    from: 2; to: 20; value: 5; stepSize: 1
                    Layout.fillWidth: true
                }

                UM.Label { text: catalog.i18nc("@label", "Analysis passes") }
                SpinBox
                {
                    id: maxIterSpinBox
                    from: 1; to: 10; value: 5; stepSize: 1
                    Layout.fillWidth: true
                }

                UM.Label { text: catalog.i18nc("@label", "Safety margin (×10)") }
                SpinBox
                {
                    // Integer SpinBox scaled by 10: value 20 = SF 2.0, range 1.0–5.0
                    id: safetyFactorSpinBox
                    from: 10; to: 50; value: 20; stepSize: 5
                    Layout.fillWidth: true
                }

                UM.Label { text: catalog.i18nc("@label", "Mesh Resolution") }
                Cura.ComboBox
                {
                    id: resolutionSelector
                    Layout.fillWidth: true
                    model: ListModel
                    {
                        ListElement { text: "Coarse"; value: "coarse" }
                        ListElement { text: "Medium"; value: "medium" }
                        ListElement { text: "Fine";   value: "fine"   }
                    }
                    textRole: "text"
                    valueRole: "value"
                    currentIndex: 1  // default: medium
                }
            }

            // ── Run button ───────────────────────────────────────────────────
            Cura.PrimaryButton
            {
                Layout.fillWidth: true
                text: catalog.i18nc("@action:button", "Run FEA Analysis")
                enabled: manager !== undefined
                    && manager.depsAvailable
                    && nodeSelector.currentValue !== undefined
                    && manager.analysisStatus !== "running"
                onClicked:
                {
                    if (!manager || nodeSelector.currentValue === undefined) return
                    manager.materialName = materialSelector.currentValue
                    manager.minDensity = minDensitySpinBox.value
                    manager.maxDensity = maxDensitySpinBox.value
                    manager.numZones = nZonesSpinBox.value
                    manager.maxIterations = maxIterSpinBox.value
                    manager.safetyFactor = safetyFactorSpinBox.value / 10.0
                    manager.runAnalysis(nodeSelector.currentValue)
                }
            }

            // ── Progress bar ─────────────────────────────────────────────────
            ProgressBar
            {
                id: progressBar
                Layout.fillWidth: true
                from: 0; to: 100
                value: (manager !== undefined) ? manager.progress : 0
                visible: manager !== undefined && manager.analysisStatus === "running"

                background: Rectangle
                {
                    implicitHeight: UM.Theme.getSize("default_lining").height * 2
                    color: UM.Theme.getColor("lining")
                    radius: UM.Theme.getSize("default_radius").width
                }

                contentItem: Item
                {
                    Rectangle
                    {
                        width: progressBar.visualPosition * parent.width
                        height: parent.height
                        radius: UM.Theme.getSize("default_radius").width
                        color: UM.Theme.getColor("primary")
                    }
                }
            }

            // ── Analysis stage label ─────────────────────────────────────────
            UM.Label
            {
                visible: manager !== undefined && manager.analysisStatus === "running"
                Layout.fillWidth: true
                text: manager ? manager.analysisStage : ""
                color: UM.Theme.getColor("text_medium")
                font: UM.Theme.getFont("small")
            }

            // ── Error state ──────────────────────────────────────────────────
            Rectangle
            {
                Layout.fillWidth: true
                visible: manager !== undefined && manager.analysisStatus === "error"
                height: visible ? errorLabel.implicitHeight + UM.Theme.getSize("default_margin").height * 2 : 0
                color: "#442222"
                border.color: "#aa4444"
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width

                UM.Label
                {
                    id: errorLabel
                    anchors
                    {
                        left: parent.left; right: parent.right
                        verticalCenter: parent.verticalCenter
                        margins: UM.Theme.getSize("default_margin").width
                    }
                    text: catalog.i18nc("@info:error", "Analysis failed. Check that boundary conditions are correctly defined, then try a coarser mesh resolution.")
                    wrapMode: Text.WordWrap
                    color: "#ff6666"
                }
            }

            // ── Results section ──────────────────────────────────────────────
            ColumnLayout
            {
                id: resultsSection
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").height / 2
                visible: manager !== undefined && manager.hasResults

                UM.Label
                {
                    text: catalog.i18nc("@label", "Results")
                    font: UM.Theme.getFont("medium_bold")
                }

                Rectangle
                {
                    Layout.fillWidth: true
                    visible: manager !== undefined && manager.hasResults
                    height: visible ? verdictLabel.implicitHeight + UM.Theme.getSize("default_margin").height : 0
                    radius: UM.Theme.getSize("default_radius").width
                    color: {
                        var v = manager ? manager.safetyVerdict : ""
                        if (v === "unsafe")       return "#442222"
                        if (v === "marginal")     return "#443322"
                        if (v === "safe")         return "#224422"
                        if (v === "conservative") return "#222244"
                        return UM.Theme.getColor("main_background")
                    }

                    UM.Label
                    {
                        id: verdictLabel
                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: UM.Theme.getSize("default_margin").width }
                        wrapMode: Text.WordWrap
                        color: "#ffffff"
                        text: {
                            var v = manager ? manager.safetyVerdict : ""
                            if (v === "unsafe")       return catalog.i18nc("@info", "Warning: Part may fail under this load. Increase max infill or redesign.")
                            if (v === "marginal")     return catalog.i18nc("@info", "Marginal safety. Consider increasing max infill density.")
                            if (v === "safe")         return catalog.i18nc("@info", "Part should handle this load safely with optimized infill.")
                            if (v === "conservative") return catalog.i18nc("@info", "Part is over-engineered. You could reduce max infill to save material.")
                            return ""
                        }
                    }
                }

                Rectangle
                {
                    Layout.fillWidth: true
                    height: resultsGrid.implicitHeight + UM.Theme.getSize("default_margin").height
                    color: UM.Theme.getColor("main_background")
                    border.color: UM.Theme.getColor("lining")
                    border.width: UM.Theme.getSize("default_lining").width
                    radius: UM.Theme.getSize("default_radius").width

                    GridLayout
                    {
                        id: resultsGrid
                        anchors
                        {
                            left: parent.left
                            right: parent.right
                            top: parent.top
                            margins: UM.Theme.getSize("default_margin").width
                        }
                        columns: 2
                        columnSpacing: UM.Theme.getSize("default_margin").width
                        rowSpacing: UM.Theme.getSize("default_margin").height / 2

                        UM.Label { text: catalog.i18nc("@label", "Max Stress:") }
                        UM.Label
                        {
                            text: (manager !== undefined && manager.hasResults)
                                ? manager.maxStress.toFixed(1) + " MPa"
                                : "—"
                        }

                        UM.Label { text: catalog.i18nc("@label", "Min Stress:") }
                        UM.Label
                        {
                            text: (manager !== undefined && manager.hasResults)
                                ? manager.minStress.toFixed(1) + " MPa"
                                : "—"
                        }

                        UM.Label { text: catalog.i18nc("@label", "Safety Factor:") }
                        UM.Label
                        {
                            text: (manager !== undefined && manager.hasResults)
                                ? manager.safetyFactor.toFixed(2)
                                : "—"
                        }

                        UM.Label { text: catalog.i18nc("@label", "Iterations:") }
                        UM.Label
                        {
                            text: (manager !== undefined && manager.hasResults)
                                ? manager.convergenceIterations.toString()
                                : "—"
                        }
                    }
                }

                // ── Action buttons ────────────────────────────────────────────
                Cura.PrimaryButton
                {
                    Layout.fillWidth: true
                    text: catalog.i18nc("@action:button", "Apply Optimized Infill to Print")
                    onClicked:
                    {
                        if (manager && nodeSelector.currentValue !== undefined)
                            manager.applyModifierMeshes(nodeSelector.currentValue)
                    }
                }

                RowLayout
                {
                    Layout.fillWidth: true
                    spacing: UM.Theme.getSize("default_margin").width

                    Cura.SecondaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Show Stress Map")
                        onClicked: { if (manager && nodeSelector.currentValue !== undefined) manager.showStressOverlay(nodeSelector.currentValue) }
                    }
                    Cura.SecondaryButton
                    {
                        Layout.fillWidth: true
                        text: catalog.i18nc("@action:button", "Clear Results")
                        onClicked: { if (manager && nodeSelector.currentValue !== undefined) manager.clearResults(nodeSelector.currentValue) }
                    }
                }
            }

            // Bottom spacer
            Item { height: UM.Theme.getSize("default_margin").height }
        }
        }  // ScrollView
    }  // Item
}
