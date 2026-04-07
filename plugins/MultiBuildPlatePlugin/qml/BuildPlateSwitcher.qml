// Copyright (c) 2024 Ultimaker B.V.
// MultiBuildPlatePlugin is released under the terms of the LGPLv3 or higher.

import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.0 as Cura


// Floating build plate tab switcher.
// Appears at the bottom-centre of the viewport when more than one build plate
// is in use (i.e. when maxBuildPlate > 0).
Item
{
    id: root

    // Grab the model from the application so we can react to plate changes.
    property var multiBuildPlateModel: CuraApplication.getMultiBuildPlateModel()

    // Only show the panel once there is more than one build plate.
    visible: multiBuildPlateModel.maxBuildPlate > 0

    // Size the root to the row it contains.
    width: tabRow.width + UM.Theme.getSize("default_margin").width * 2
    height: tabRow.height + UM.Theme.getSize("default_margin").height * 2

    // Anchor to the bottom-centre of the main window, above any system bars.
    anchors
    {
        bottom: parent ? parent.bottom : undefined
        bottomMargin: UM.Theme.getSize("default_margin").height * 4
        horizontalCenter: parent ? parent.horizontalCenter : undefined
    }

    // Panel background.
    Rectangle
    {
        anchors.fill: parent
        color: UM.Theme.getColor("main_background")
        border.width: UM.Theme.getSize("default_lining").width
        border.color: UM.Theme.getColor("lining")
        radius: UM.Theme.getSize("action_button_radius").width
    }

    RowLayout
    {
        id: tabRow
        anchors.centerIn: parent
        spacing: UM.Theme.getSize("narrow_margin").width

        // One tab button per build plate.
        Repeater
        {
            model: root.multiBuildPlateModel

            Button
            {
                id: plateButton

                property bool isActive: root.multiBuildPlateModel.activeBuildPlate === model.buildPlateNumber

                Layout.preferredWidth: UM.Theme.getSize("action_button").height * 2.5
                Layout.preferredHeight: UM.Theme.getSize("action_button").height
                checkable: false
                hoverEnabled: true

                onClicked: Cura.SceneController.setActiveBuildPlate(model.buildPlateNumber)

                background: Rectangle
                {
                    color: plateButton.isActive
                        ? UM.Theme.getColor("primary")
                        : plateButton.hovered
                            ? UM.Theme.getColor("action_button_hovered")
                            : "transparent"
                    radius: UM.Theme.getSize("action_button_radius").width
                    border.width: UM.Theme.getSize("default_lining").width
                    border.color: plateButton.isActive
                        ? UM.Theme.getColor("primary")
                        : UM.Theme.getColor("lining")
                }

                contentItem: UM.Label
                {
                    text: model.name
                    color: plateButton.isActive
                        ? UM.Theme.getColor("primary_button_text")
                        : UM.Theme.getColor("text_scene")
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
            }
        }

        // Separator between plates and the "add" button.
        Rectangle
        {
            Layout.preferredWidth: UM.Theme.getSize("default_lining").width
            Layout.preferredHeight: UM.Theme.getSize("action_button").height
            color: UM.Theme.getColor("lining")
        }

        // "+" button — moves selected objects to a new build plate.
        Button
        {
            id: addPlateButton

            Layout.preferredWidth: UM.Theme.getSize("action_button").height
            Layout.preferredHeight: UM.Theme.getSize("action_button").height
            checkable: false
            hoverEnabled: true

            onClicked: manager.moveSelectionToNewBuildPlate()

            background: Rectangle
            {
                color: addPlateButton.hovered
                    ? UM.Theme.getColor("action_button_hovered")
                    : "transparent"
                radius: UM.Theme.getSize("action_button_radius").width
                border.width: UM.Theme.getSize("default_lining").width
                border.color: UM.Theme.getColor("lining")
            }

            contentItem: UM.Label
            {
                text: "+"
                color: UM.Theme.getColor("text_scene")
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                font.bold: true
            }

            UM.ToolTip
            {
                id: addPlateTooltip
                tooltipText: catalog.i18nc("@tooltip", "Move selected objects to a new build plate")
                visible: addPlateButton.hovered
            }
        }
    }

    UM.I18nCatalog
    {
        id: catalog
        name: "cura"
    }
}
