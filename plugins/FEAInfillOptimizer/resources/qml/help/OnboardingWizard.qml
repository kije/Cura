// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.

// Modal onboarding wizard shown on first activation of the FEA tool.
// Controlled by preference: fea_optimizer/onboarding_completed

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Popup
{
    id: onboarding

    property int currentStep: 0
    readonly property int totalSteps: steps.length

    property var steps: [
        {
            title: catalog.i18nc("@title", "What does this tool do?"),
            image: "",
            body: catalog.i18nc("@info",
                "This tool analyzes structural loads on your 3D model and optimizes " +
                "infill density zone-by-zone.\n\n" +
                "Regions under high stress get denser infill for strength. " +
                "Low-stress regions stay light to save material and print time.")
        },
        {
            title: catalog.i18nc("@title", "Step 1: Define Supports"),
            image: Qt.resolvedUrl("../../icons/guide_support.svg"),
            body: catalog.i18nc("@info",
                "Click faces where your part is held, mounted, or resting on a surface. " +
                "These faces will not move during the simulation.\n\n" +
                "Use 'Surface' mode to select an entire flat face with one click.")
        },
        {
            title: catalog.i18nc("@title", "Step 2: Apply Forces"),
            image: Qt.resolvedUrl("../../icons/guide_force.svg"),
            body: catalog.i18nc("@info",
                "Click faces where forces act (weight, push, pull). " +
                "Set the load amount in Newtons.\n\n" +
                "Tip: 1 kg of weight is about 10 N. A firm finger push is 20-50 N.")
        },
        {
            title: catalog.i18nc("@title", "Step 3: Apply Torques (optional)"),
            image: Qt.resolvedUrl("../../icons/guide_torque.svg"),
            body: catalog.i18nc("@info",
                "If your part experiences twisting loads, click faces where the twist is applied " +
                "and set the torque in Newton-meters.\n\n" +
                "Tip: Hand-tightened bolt is about 1-5 Nm.")
        },
        {
            title: catalog.i18nc("@title", "Step 3: Run and Review"),
            image: "",
            body: catalog.i18nc("@info",
                "Click 'Confirm and Optimize', choose your material, " +
                "and run the analysis.\n\n" +
                "The tool will show a safety verdict and suggest optimized " +
                "infill zones you can apply with one click.")
        }
    ]

    modal: true
    width: 300
    height: 420
    anchors.centerIn: Overlay.overlay
    closePolicy: Popup.CloseOnEscape
    padding: UM.Theme.getSize("default_margin").width

    UM.I18nCatalog { id: catalog; name: "cura" }

    background: Rectangle
    {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }

    Accessible.role: Accessible.Dialog
    Accessible.name: catalog.i18nc("@title", "Getting Started") + ", " +
        catalog.i18nc("@info", "Step %1 of %2").arg(currentStep + 1).arg(totalSteps)

    ColumnLayout
    {
        anchors.fill: parent
        spacing: UM.Theme.getSize("default_margin").height

        // Title
        UM.Label
        {
            text: steps[currentStep].title
            font: UM.Theme.getFont("medium_bold")
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
        }

        // Image (if any)
        Image
        {
            visible: steps[currentStep].image !== ""
            source: steps[currentStep].image
            Layout.fillWidth: true
            Layout.preferredHeight: 100
            fillMode: Image.PreserveAspectFit
            sourceSize.width: width
        }

        // Body text
        ScrollView
        {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            UM.Label
            {
                width: parent.width
                text: steps[currentStep].body
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
                wrapMode: Text.WordWrap
            }
        }

        // Don't show again checkbox (on last step)
        CheckBox
        {
            visible: currentStep === totalSteps - 1
            text: catalog.i18nc("@option", "Don't show this again")
            checked: true
            Layout.fillWidth: true
            onCheckedChanged:
            {
                if (!checked)
                {
                    UM.Preferences.resetPreference("fea_optimizer/onboarding_completed")
                }
            }
        }

        // Step indicator dots
        Row
        {
            Layout.alignment: Qt.AlignHCenter
            spacing: 8

            Repeater
            {
                model: onboarding.totalSteps

                Rectangle
                {
                    width: 8; height: 8
                    radius: 4
                    color: index === onboarding.currentStep
                        ? UM.Theme.getColor("primary")
                        : UM.Theme.getColor("lining")
                }
            }
        }

        // Navigation buttons
        RowLayout
        {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width

            Cura.SecondaryButton
            {
                text: currentStep === totalSteps - 1
                    ? catalog.i18nc("@action:button", "Close")
                    : catalog.i18nc("@action:button", "Skip")
                Layout.fillWidth: true
                onClicked:
                {
                    UM.Preferences.setValue("fea_optimizer/onboarding_completed", true)
                    onboarding.close()
                }
            }

            Cura.PrimaryButton
            {
                visible: currentStep < totalSteps - 1
                text: catalog.i18nc("@action:button", "Next")
                Layout.fillWidth: true
                onClicked: onboarding.currentStep++
            }

            Cura.PrimaryButton
            {
                visible: currentStep === totalSteps - 1
                text: catalog.i18nc("@action:button", "Get Started")
                Layout.fillWidth: true
                onClicked:
                {
                    UM.Preferences.setValue("fea_optimizer/onboarding_completed", true)
                    onboarding.close()
                }
            }
        }
    }
}
