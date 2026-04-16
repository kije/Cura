// Copyright (c) 2024 Ultimaker B.V.
// MultiBuildPlatePlugin is released under the terms of the LGPLv3 or higher.

import QtQuick 2.10
import QtQuick.Controls 2.3

import UM 1.5 as UM
import Cura 1.0 as Cura


// Prev / plate-indicator / Next row injected into ActionPanelWidget's
// additionalComponents["saveButton"] area (left of the Slice button).
//
// Only visible when more than one build plate exists.
Row
{
    id: plateNavigator

    spacing: UM.Theme.getSize("narrow_margin").width
    height:  UM.Theme.getSize("action_button").height

    // Show only when multiple plates exist.
    visible: manager.pendingMaxPlate > 0

    property var plateModel: CuraApplication.getMultiBuildPlateModel()

    // ── ◀ Previous plate ─────────────────────────────────────────────────
    Cura.SecondaryButton
    {
        id: prevBtn
        height:       UM.Theme.getSize("action_button").height
        width:        height                             // square icon button
        iconSource:   UM.Theme.getIcon("ChevronSingleLeft")
        iconSize:     UM.Theme.getSize("action_button_icon_small").height
        leftPadding:  0
        rightPadding: 0
        enabled:      plateNavigator.plateModel.activeBuildPlate > 0
        tooltip:      catalog.i18nc("@tooltip", "Previous build plate  (Ctrl+[)")

        onClicked: manager.setActiveBuildPlate(plateNavigator.plateModel.activeBuildPlate - 1)
    }

    // ── Plate indicator ───────────────────────────────────────────────────
    Rectangle
    {
        height:       UM.Theme.getSize("action_button").height
        width:        plateLabel.implicitWidth + UM.Theme.getSize("default_margin").width * 2
        color:        UM.Theme.getColor("main_background")
        border.width: UM.Theme.getSize("default_lining").width
        border.color: UM.Theme.getColor("lining")
        radius:       UM.Theme.getSize("action_button_radius").width

        UM.Label
        {
            id: plateLabel
            anchors.centerIn: parent
            // "Plate 2 / 3"
            text: catalog.i18nc("@label:build_plate_indicator",
                      "Plate %1 / %2")
                      .arg(plateNavigator.plateModel.activeBuildPlate + 1)
                      .arg(manager.pendingMaxPlate + 1)
            font.bold: true
        }
    }

    // ── ▶ Next plate ──────────────────────────────────────────────────────
    Cura.SecondaryButton
    {
        id: nextBtn
        height:       UM.Theme.getSize("action_button").height
        width:        height
        iconSource:   UM.Theme.getIcon("ChevronSingleRight")
        iconSize:     UM.Theme.getSize("action_button_icon_small").height
        leftPadding:  0
        rightPadding: 0
        enabled:      plateNavigator.plateModel.activeBuildPlate < manager.pendingMaxPlate
        tooltip:      catalog.i18nc("@tooltip", "Next build plate  (Ctrl+])")

        onClicked: manager.setActiveBuildPlate(plateNavigator.plateModel.activeBuildPlate + 1)
    }

    UM.I18nCatalog { id: catalog; name: "cura" }
}
