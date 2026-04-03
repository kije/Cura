// Copyright (c) 2026 Community Contributors
// Released under the terms of the LGPLv3 or higher.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

import UM 1.5 as UM

/**
 * A color swatch that shows the blended preview of two filament colors.
 * Includes a subtle striped overlay to indicate dithered/alternating layers.
 */
Rectangle
{
    id: swatch

    property string colorA: "#cc4444"
    property string colorB: "#4444cc"
    property real ratioA: 0.5
    property string blendedColor: "#808080"

    width: 48
    height: 48
    radius: 4
    color: blendedColor
    border.color: UM.Theme.getColor("lining")
    border.width: 1

    // Dithering stripe overlay
    Column
    {
        anchors.fill: parent
        anchors.margins: 1
        clip: true
        opacity: 0.3

        Repeater
        {
            model: 8
            Rectangle
            {
                width: parent.width
                height: parent.height / 8
                color: index % 2 === 0 ? swatch.colorA : swatch.colorB
            }
        }
    }

    ToolTip
    {
        id: tooltip
        visible: mouseArea.containsMouse
        delay: 500
        text: "A: %1 (%2%)\nB: %3 (%4%)\nBlend: %5"
            .arg(swatch.colorA)
            .arg(Math.round(swatch.ratioA * 100))
            .arg(swatch.colorB)
            .arg(Math.round((1.0 - swatch.ratioA) * 100))
            .arg(swatch.blendedColor)
    }

    MouseArea
    {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
    }
}
