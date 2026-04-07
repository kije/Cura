// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.

// Reusable "?" icon that shows a tooltip on hover and optionally
// opens a popover on click (if guideId is set).

import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM

Item
{
    id: helpIcon

    property string tooltipText: ""
    property string guideId: ""
    property var helpContentManager: null

    width: 20; height: 20

    UM.ColorImage
    {
        anchors.centerIn: parent
        source: UM.Theme.getIcon("Help", "default")
        color: iconMouseArea.containsMouse
            ? UM.Theme.getColor("text")
            : UM.Theme.getColor("text_inactive")
        width: 16; height: 16
    }

    MouseArea
    {
        id: iconMouseArea
        anchors.fill: parent
        anchors.margins: -2
        hoverEnabled: true
        cursorShape: Qt.WhatsThisCursor

        onClicked:
        {
            if (guideId !== "" && helpContentManager)
            {
                helpContentManager.openPopover(guideId, helpIcon)
            }
        }
    }

    ToolTip
    {
        id: helpTooltip
        visible: iconMouseArea.containsMouse && tooltipText !== ""
        text: tooltipText
        delay: 500
        timeout: 10000
        width: Math.min(implicitWidth, 240)

        background: Rectangle
        {
            color: UM.Theme.getColor("tooltip")
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            radius: UM.Theme.getSize("default_radius").width
        }

        contentItem: Text
        {
            text: helpTooltip.text
            font: UM.Theme.getFont("small")
            color: UM.Theme.getColor("text")
            wrapMode: Text.WordWrap
        }
    }

    Accessible.role: Accessible.Button
    Accessible.name: tooltipText !== "" ? "Help: " + tooltipText : "Help"
}
