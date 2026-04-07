// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.

// Popover card displaying a help guide entry.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.5 as UM

Popup
{
    id: helpPopover

    property string title: ""
    property string imagePath: ""
    property string imageAlt: ""
    property string body: ""
    property var steps: []
    property var tips: []

    width: 260
    height: Math.min(contentColumn.implicitHeight + padding * 2, 400)
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: UM.Theme.getSize("default_margin").width

    background: Rectangle
    {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width

        layer.enabled: true
        layer.effect: Item
        {
            // Simple shadow effect using a larger rectangle behind
        }
    }

    Accessible.role: Accessible.Dialog
    Accessible.name: title

    ScrollView
    {
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout
        {
            id: contentColumn
            width: helpPopover.width - helpPopover.padding * 2
            spacing: UM.Theme.getSize("default_margin").height / 2

            // Header row
            RowLayout
            {
                Layout.fillWidth: true
                spacing: 4

                UM.Label
                {
                    text: helpPopover.title
                    font: UM.Theme.getFont("medium_bold")
                    Layout.fillWidth: true
                }

                UM.ColorImage
                {
                    source: UM.Theme.getIcon("Cancel")
                    color: closeMouseArea.containsMouse
                        ? UM.Theme.getColor("text")
                        : UM.Theme.getColor("text_medium")
                    width: 20; height: 20

                    MouseArea
                    {
                        id: closeMouseArea
                        anchors.fill: parent
                        anchors.margins: -4
                        hoverEnabled: true
                        onClicked: helpPopover.close()
                    }

                    Accessible.role: Accessible.Button
                    Accessible.name: "Close help"
                }
            }

            // SVG illustration
            Image
            {
                visible: helpPopover.imagePath !== ""
                source: helpPopover.imagePath !== "" ? helpPopover.imagePath : ""
                Layout.fillWidth: true
                Layout.preferredHeight: 100
                fillMode: Image.PreserveAspectFit
                sourceSize.width: width

                Accessible.role: Accessible.Graphic
                Accessible.description: helpPopover.imageAlt
            }

            // Body text
            UM.Label
            {
                visible: helpPopover.body !== ""
                text: helpPopover.body
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text")
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            // Numbered steps
            ColumnLayout
            {
                visible: helpPopover.steps.length > 0
                Layout.fillWidth: true
                spacing: 2

                UM.Label
                {
                    text: "Steps:"
                    font: UM.Theme.getFont("small_bold")
                }

                Repeater
                {
                    model: helpPopover.steps

                    UM.Label
                    {
                        text: (index + 1) + ". " + modelData
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text")
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        Layout.leftMargin: UM.Theme.getSize("default_margin").width / 2
                    }
                }
            }

            // Tips list
            ColumnLayout
            {
                visible: helpPopover.tips.length > 0
                Layout.fillWidth: true
                spacing: 2

                UM.Label
                {
                    text: "Tips:"
                    font: UM.Theme.getFont("small_bold")
                }

                Repeater
                {
                    model: helpPopover.tips

                    UM.Label
                    {
                        text: "\u2022 " + modelData
                        font: UM.Theme.getFont("small")
                        color: UM.Theme.getColor("text_medium")
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        Layout.leftMargin: UM.Theme.getSize("default_margin").width / 2
                    }
                }
            }
        }
    }
}
