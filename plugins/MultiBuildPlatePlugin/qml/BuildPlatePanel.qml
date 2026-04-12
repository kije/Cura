// Copyright (c) 2024 Ultimaker B.V.
// MultiBuildPlatePlugin is released under the terms of the LGPLv3 or higher.

import QtQuick 2.10
import QtQuick.Controls 2.3

import UM 1.5 as UM
import Cura 1.0 as Cura


// Collapsible build-plate management panel.
// Positioned bottom-left (mirroring the Object List panel).
//
// Extension-point integrations wired here:
//   • createQmlComponent  — this panel is the created component
//   • QML Shortcut items  — Ctrl+] / Ctrl+[ / Ctrl+Shift+N
//   • manager.canAddNewPlate — disables "+" when the active plate is empty
//   • UM.Message toasts   — fired by the Python backend on every operation
//
// Features:
//   • One collapsible section per build plate showing its objects
//   • Single-click a plate header → switch to that plate and toggle expand
//   • "+" icon button in header → add a new empty plate (disabled when plate empty)
//   • Drag an object row onto a plate header → move it to that plate
//   • Guard: dropping onto the current plate is a no-op (no undo-stack pollution)
//   • Empty-plate hint text ("Drag objects here")
//   • Keyboard shortcuts: Ctrl+] next, Ctrl+[ previous, Ctrl+Shift+N move to new

Item
{
    id: root

    property var multiBuildPlateModel: CuraApplication.getMultiBuildPlateModel()

    // Match the width of the Object List panel.
    width: UM.Theme.getSize("objects_menu_size").width

    // Only show when objects are present in the scene (same as ObjectSelector).
    visible: CuraApplication.platformActivity

    // Anchor bottom-left, leaving room for the toolbar on the left and the
    // bottom action bar below.
    anchors
    {
        left:         parent ? parent.left : undefined
        leftMargin:   UM.Theme.getSize("button").width + UM.Theme.getSize("default_margin").width * 2
        bottom:       parent ? parent.bottom : undefined
        bottomMargin: UM.Theme.getSize("default_margin").height * 14
    }

    // ─────────────────────────────────────────────────────────────────────
    // Keyboard shortcuts (QML Shortcut extension point)
    // ─────────────────────────────────────────────────────────────────────

    Shortcut
    {
        sequence: "Ctrl+]"
        onActivated:
        {
            var active = root.multiBuildPlateModel.activeBuildPlate
            if (active < manager.pendingMaxPlate)
                manager.setActiveBuildPlate(active + 1)
        }
    }

    Shortcut
    {
        sequence: "Ctrl+["
        onActivated:
        {
            var active = root.multiBuildPlateModel.activeBuildPlate
            if (active > 0)
                manager.setActiveBuildPlate(active - 1)
        }
    }

    Shortcut
    {
        sequence: "Ctrl+Shift+N"
        onActivated: manager.moveSelectionToNewBuildPlate()
    }

    // ─────────────────────────────────────────────────────────────────────
    // Drag ghost
    // A floating visual that follows the mouse when the user drags an object
    // row.  Lives at root level (outside the clipped ScrollView) so it is
    // never hidden, and its Drag.active drives all DropArea hit-testing.
    // ─────────────────────────────────────────────────────────────────────
    Rectangle
    {
        id: dragGhost
        z: 1000
        visible: false
        width:  root.width - UM.Theme.getSize("default_margin").width
        height: UM.Theme.getSize("action_button").height
        radius: UM.Theme.getSize("action_button_radius").width
        color:  UM.Theme.getColor("action_button_hovered")
        border.color: UM.Theme.getColor("primary")
        border.width: UM.Theme.getSize("default_lining").width * 2
        opacity: 0.92

        // The index into ObjectsModel carried by this drag operation.
        property int    objectModelIndex: -1
        property string ghostName: ""

        Drag.active:    dragGhost.visible
        Drag.source:    dragGhost
        Drag.hotSpot.x: width  / 2
        Drag.hotSpot.y: height / 2

        UM.Label
        {
            anchors
            {
                left:           parent.left
                right:          parent.right
                leftMargin:     UM.Theme.getSize("default_margin").width
                rightMargin:    UM.Theme.getSize("default_margin").width
                verticalCenter: parent.verticalCenter
            }
            text:  dragGhost.ghostName
            elide: Text.ElideRight
            color: UM.Theme.getColor("text_scene")
        }
    }

    // ─────────────────────────────────────────────────────────────────────
    // Toggle button (mirrors ObjectSelector styling)
    // ─────────────────────────────────────────────────────────────────────
    property bool panelOpen: true

    Button
    {
        id: toggleButton
        width:        parent.width
        height:       contentItem.height + bottomPadding
        hoverEnabled: true
        padding:      0
        bottomPadding: UM.Theme.getSize("narrow_margin").height / 2 | 0

        anchors
        {
            bottom:           panelRect.top
            horizontalCenter: parent.horizontalCenter
        }

        contentItem: Item
        {
            width:  parent.width
            height: toggleChevron.height

            UM.ColorImage
            {
                id: toggleChevron
                width:  UM.Theme.getSize("standard_arrow").width
                height: UM.Theme.getSize("standard_arrow").height
                anchors.left: parent.left
                color: toggleButton.hovered
                    ? UM.Theme.getColor("small_button_text_hover")
                    : UM.Theme.getColor("small_button_text")
                source: root.panelOpen
                    ? UM.Theme.getIcon("ChevronSingleDown")
                    : UM.Theme.getIcon("ChevronSingleUp")
            }

            UM.Label
            {
                id: panelLabel
                anchors
                {
                    left:           toggleChevron.right
                    leftMargin:     UM.Theme.getSize("default_margin").width
                    right:          addPlateBtn.left
                    rightMargin:    UM.Theme.getSize("narrow_margin").width
                    verticalCenter: parent.verticalCenter
                }
                text:  catalog.i18nc("@label", "Build Plates")
                color: toggleButton.hovered
                    ? UM.Theme.getColor("small_button_text_hover")
                    : UM.Theme.getColor("small_button_text")
                elide: Text.ElideRight
            }

            // "+" icon add-plate button — disabled when the active plate is empty
            // so the user cannot stack multiple consecutive empty plates.
            Button
            {
                id: addPlateBtn
                anchors
                {
                    right:          parent.right
                    verticalCenter: parent.verticalCenter
                }
                padding:      UM.Theme.getSize("narrow_margin").width / 2 | 0
                width:        UM.Theme.getSize("small_button_icon").width  + padding * 2
                height:       UM.Theme.getSize("small_button_icon").height + padding * 2
                hoverEnabled: true
                enabled:      manager.canAddNewPlate

                onClicked:
                {
                    manager.addBuildPlate()
                    root.panelOpen = true
                }

                contentItem: UM.ColorImage
                {
                    source: UM.Theme.getIcon("Plus")
                    width:  UM.Theme.getSize("small_button_icon").width
                    height: UM.Theme.getSize("small_button_icon").height
                    color: !addPlateBtn.enabled
                        ? UM.Theme.getColor("text_disabled")
                        : addPlateBtn.hovered
                            ? UM.Theme.getColor("small_button_text_hover")
                            : UM.Theme.getColor("small_button_text")
                }

                background: Rectangle
                {
                    color: (addPlateBtn.hovered && addPlateBtn.enabled)
                        ? UM.Theme.getColor("action_button_hovered")
                        : "transparent"
                    radius: UM.Theme.getSize("action_button_radius").width
                }

                UM.ToolTip
                {
                    tooltipText: addPlateBtn.enabled
                        ? catalog.i18nc("@tooltip", "Add new build plate  (Ctrl+Shift+N to move selection)")
                        : catalog.i18nc("@tooltip", "Add objects to the current plate before creating a new one")
                    visible: addPlateBtn.hovered
                }
            }
        }

        background: Item {}

        onClicked: root.panelOpen = !root.panelOpen

        // Explain the feature to users who hover over the panel title.
        UM.ToolTip
        {
            tooltipText: catalog.i18nc("@tooltip",
                "Build Plates let you organize models into separate virtual print jobs.\n" +
                "Each plate is sliced and printed independently on the same printer.\n\n" +
                "Shortcuts:  Ctrl+]  next plate   Ctrl+[  previous plate")
            visible: toggleButton.hovered
        }
    }

    // ─────────────────────────────────────────────────────────────────────
    // Panel content
    // ─────────────────────────────────────────────────────────────────────
    Rectangle
    {
        id: panelRect
        anchors.bottom: parent.bottom
        width:   parent.width
        visible: root.panelOpen
        height: visible
            ? Math.min(
                sectionsCol.implicitHeight + border.width * 2,
                UM.Theme.getSize("objects_menu_size").height)
            : 0

        color:        UM.Theme.getColor("main_background")
        border.width: UM.Theme.getSize("default_lining").width
        border.color: UM.Theme.getColor("lining")
        clip: true

        Behavior on height { NumberAnimation { duration: 100 } }

        ListView
        {
            id: plateListView
            anchors.fill:    parent
            anchors.margins: panelRect.border.width
            clip: true
            interactive: true

            ScrollBar.vertical: UM.ScrollBar { id: scrollBar }

            // Drive the count from pendingMaxPlate so newly-added empty
            // plates remain visible until the user populates them.
            model: manager.pendingMaxPlate + 1

            // Disable the built-in highlight so we can style each section.
            highlight: Item {}
            highlightFollowsCurrentItem: false

            delegate: Column
            {
                id: plateSection
                width: plateListView.width - scrollBar.width

                property int  plateNumber: index
                property bool isActive:   root.multiBuildPlateModel.activeBuildPlate === plateNumber
                property bool expanded:   isActive
                property var  plateObjects: []

                // Refresh when the scene or selection changes.
                Connections
                {
                    target: manager
                    function onObjectsChanged()
                    {
                        plateSection.plateObjects = manager.getObjectsForPlate(plateSection.plateNumber)
                    }
                }
                Component.onCompleted:
                {
                    plateObjects = manager.getObjectsForPlate(plateNumber)
                }

                // ── Plate header ─────────────────────────────────────
                Rectangle
                {
                    id: plateHeader
                    width:  parent.width
                    height: UM.Theme.getSize("action_button").height

                    // Rounded corners for a button-like appearance.
                    radius: UM.Theme.getSize("action_button_radius").width

                    color: plateSection.isActive
                        ? UM.Theme.getColor("primary")
                        : headerDropArea.containsDrag
                            ? Qt.rgba(
                                UM.Theme.getColor("primary").r,
                                UM.Theme.getColor("primary").g,
                                UM.Theme.getColor("primary").b,
                                0.40)
                            : headerMouseArea.containsMouse
                                ? UM.Theme.getColor("action_button_hovered")
                                : "transparent"

                    // Smooth color transitions when switching plates or hovering.
                    Behavior on color { ColorAnimation { duration: 100 } }

                    // Plate name + object count
                    UM.Label
                    {
                        anchors
                        {
                            left:           parent.left
                            leftMargin:     UM.Theme.getSize("default_margin").width
                            right:          chevron.left
                            rightMargin:    UM.Theme.getSize("narrow_margin").width
                            verticalCenter: parent.verticalCenter
                        }
                        text: catalog.i18nc("@label:build_plate", "Plate %1").arg(plateSection.plateNumber + 1)
                            + " (%1)".arg(plateSection.plateObjects.length)
                        color: plateSection.isActive
                            ? UM.Theme.getColor("primary_button_text")
                            : UM.Theme.getColor("text_scene")
                        font.bold: plateSection.isActive
                        elide: Text.ElideRight
                    }

                    // Expand / collapse chevron
                    UM.ColorImage
                    {
                        id: chevron
                        anchors
                        {
                            right:          parent.right
                            rightMargin:    UM.Theme.getSize("narrow_margin").width
                            verticalCenter: parent.verticalCenter
                        }
                        width:  UM.Theme.getSize("standard_arrow").width
                        height: UM.Theme.getSize("standard_arrow").height
                        source: plateSection.expanded
                            ? UM.Theme.getIcon("ChevronSingleDown")
                            : UM.Theme.getIcon("ChevronSingleRight")
                        color: plateSection.isActive
                            ? UM.Theme.getColor("primary_button_text")
                            : UM.Theme.getColor("text_scene")
                    }

                    // Click → switch to plate and toggle expand
                    MouseArea
                    {
                        id: headerMouseArea
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked:
                        {
                            Cura.SceneController.setActiveBuildPlate(plateSection.plateNumber)
                            plateSection.expanded = !plateSection.expanded
                        }
                    }

                    // Drop target — accepts dragged object rows.
                    // Guard: ignore drops where the object is already on this plate.
                    DropArea
                    {
                        id: headerDropArea
                        anchors.fill: parent
                        onDropped:
                        {
                            var src = drag.source
                            if (src && src.objectModelIndex !== undefined && src.objectModelIndex >= 0)
                            {
                                // Skip if the object already lives on this plate (avoids
                                // polluting the undo stack with a no-op operation).
                                if (manager.getObjectPlate(src.objectModelIndex) !== plateSection.plateNumber)
                                {
                                    manager.moveObjectToBuildPlate(src.objectModelIndex, plateSection.plateNumber)
                                }
                            }
                        }
                    }
                }

                // ── Object list (collapsible) ─────────────────────────
                Column
                {
                    width:   parent.width
                    visible: plateSection.expanded

                    // Empty-plate hint shown when no objects are on this plate.
                    Item
                    {
                        width:   parent.width
                        height:  plateSection.plateObjects.length === 0
                                     ? UM.Theme.getSize("action_button").height
                                     : 0
                        visible: plateSection.plateObjects.length === 0

                        UM.Label
                        {
                            anchors.centerIn: parent
                            text:    catalog.i18nc("@label:empty_plate", "Drag objects here")
                            color:   UM.Theme.getColor("text_scene")
                            opacity: 0.45
                        }
                    }

                    Repeater
                    {
                        model: plateSection.plateObjects

                        // ── Object row ───────────────────────────────
                        Item
                        {
                            id: objectRow
                            width:  parent.width
                            height: UM.Theme.getSize("action_button").height

                            property int    objectModelIndex: modelData.model_index
                            property string objectName:       modelData.name
                            property bool   isSelected:       modelData.selected

                            // Row background — selection at 0.35 opacity for clear visibility.
                            Rectangle
                            {
                                anchors.fill: parent
                                color: objectRow.isSelected
                                    ? Qt.rgba(
                                        UM.Theme.getColor("primary").r,
                                        UM.Theme.getColor("primary").g,
                                        UM.Theme.getColor("primary").b,
                                        0.35)
                                    : rowMouseArea.containsMouse
                                        ? UM.Theme.getColor("action_button_hovered")
                                        : "transparent"
                                radius: UM.Theme.getSize("action_button_radius").width
                            }

                            // Indented object name
                            UM.Label
                            {
                                anchors
                                {
                                    left:           parent.left
                                    right:          parent.right
                                    leftMargin:     UM.Theme.getSize("default_margin").width * 2
                                    rightMargin:    UM.Theme.getSize("narrow_margin").width
                                    verticalCenter: parent.verticalCenter
                                }
                                text:  objectRow.objectName
                                color: UM.Theme.getColor("text_scene")
                                elide: Text.ElideRight
                            }

                            // ── Mouse / drag handling ─────────────────
                            MouseArea
                            {
                                id: rowMouseArea
                                anchors.fill: parent
                                hoverEnabled: true

                                property point pressPos
                                property bool  dragging: false
                                readonly property real dragThreshold: 6

                                onPressed:
                                {
                                    pressPos = Qt.point(mouse.x, mouse.y)
                                    dragging = false
                                }

                                onPositionChanged:
                                {
                                    if (!pressed) { return }

                                    var dx   = mouse.x - pressPos.x
                                    var dy   = mouse.y - pressPos.y
                                    var dist = Math.sqrt(dx * dx + dy * dy)

                                    if (!dragging && dist > dragThreshold)
                                    {
                                        dragging = true
                                        dragGhost.objectModelIndex = objectRow.objectModelIndex
                                        dragGhost.ghostName        = objectRow.objectName
                                        var p = mapToItem(root, mouse.x, mouse.y)
                                        dragGhost.x       = p.x - dragGhost.width  / 2
                                        dragGhost.y       = p.y - dragGhost.height / 2
                                        dragGhost.visible = true
                                    }

                                    if (dragging)
                                    {
                                        var p2 = mapToItem(root, mouse.x, mouse.y)
                                        dragGhost.x = p2.x - dragGhost.width  / 2
                                        dragGhost.y = p2.y - dragGhost.height / 2
                                    }
                                }

                                onReleased:
                                {
                                    if (dragging)
                                    {
                                        dragGhost.Drag.drop()
                                        dragGhost.visible = false
                                        dragging          = false
                                    }
                                    else
                                    {
                                        // Tap without drag → select the object in the scene.
                                        manager.selectObject(objectRow.objectModelIndex)
                                    }
                                }

                                onCanceled:
                                {
                                    if (dragging)
                                    {
                                        dragGhost.Drag.cancel()
                                        dragGhost.visible = false
                                        dragging          = false
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    UM.I18nCatalog { id: catalog; name: "cura" }
}
