// Copyright (c) 2025 UltiMaker
// Cura is released under the terms of the LGPLv3 or higher.

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import UM 1.7 as UM
import Cura 1.0 as Cura

Column
{
    id: layerPanel

    spacing: UM.Theme.getSize("default_margin").height / 2

    property var layerStack: UM.Controller.properties.getValue("LayerStack") ?? null

    // Layer list
    Rectangle
    {
        width: parent.width
        height: Math.min(layerListView.contentHeight + 4, 150)
        color: UM.Theme.getColor("setting_category")
        border.width: UM.Theme.getSize("default_lining").width
        border.color: UM.Theme.getColor("lining")
        radius: UM.Theme.getSize("default_radius").width

        ListView
        {
            id: layerListView
            anchors.fill: parent
            anchors.margins: 2
            clip: true
            model: layerPanel.layerStack

            delegate: Rectangle
            {
                width: layerListView.width
                height: UM.Theme.getSize("section").height
                color: layerActive ? UM.Theme.getColor("primary") : "transparent"
                radius: UM.Theme.getSize("default_radius").width

                MouseArea
                {
                    anchors.fill: parent
                    onClicked:
                    {
                        if (layerPanel.layerStack)
                        {
                            layerPanel.layerStack.setActiveLayer(index)
                        }
                    }
                    onDoubleClicked:
                    {
                        if (layerPanel.layerStack)
                        {
                            layerPanel.layerStack.toggleIsolatedMode()
                        }
                    }
                }

                RowLayout
                {
                    anchors.fill: parent
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width / 2
                    anchors.rightMargin: UM.Theme.getSize("default_margin").width / 2
                    spacing: UM.Theme.getSize("default_margin").width / 2

                    // Visibility toggle
                    UM.ToolbarButton
                    {
                        Layout.preferredWidth: UM.Theme.getSize("small_button").width
                        Layout.preferredHeight: UM.Theme.getSize("small_button").height
                        toolItem: UM.ColorImage
                        {
                            source: layerVisible ? UM.Theme.getIcon("Eye") : UM.Theme.getIcon("Eye")
                            color: layerVisible ? UM.Theme.getColor("icon") : UM.Theme.getColor("text_inactive")
                        }
                        onClicked:
                        {
                            if (layerPanel.layerStack)
                            {
                                layerPanel.layerStack.setLayerVisible(index, !layerVisible)
                            }
                        }
                    }

                    // Layer name
                    UM.Label
                    {
                        Layout.fillWidth: true
                        text: layerName
                        color: layerActive ? UM.Theme.getColor("primary_text") : UM.Theme.getColor("text")
                        elide: Text.ElideRight
                    }

                    // Opacity display
                    UM.Label
                    {
                        text: Math.round(layerOpacity * 100) + "%"
                        color: layerActive ? UM.Theme.getColor("primary_text") : UM.Theme.getColor("text_inactive")
                    }
                }
            }
        }
    }

    // Isolated mode indicator
    UM.Label
    {
        visible: layerPanel.layerStack ? layerPanel.layerStack.isolatedMode : false
        text: catalog.i18nc("@label", "Isolated Edit Mode (double-click layer to exit)")
        color: UM.Theme.getColor("warning")
        font.italic: true
    }

    // Layer action buttons
    RowLayout
    {
        spacing: UM.Theme.getSize("default_margin").width / 2

        Cura.SecondaryButton
        {
            text: catalog.i18nc("@button", "Add")
            enabled: layerPanel.layerStack ? layerPanel.layerStack.layerCount < 8 : false
            onClicked:
            {
                if (layerPanel.layerStack)
                {
                    layerPanel.layerStack.addLayer("")
                }
            }
        }

        Cura.SecondaryButton
        {
            text: catalog.i18nc("@button", "Remove")
            enabled: layerPanel.layerStack ? layerPanel.layerStack.layerCount > 1 : false
            onClicked:
            {
                if (layerPanel.layerStack)
                {
                    layerPanel.layerStack.removeLayer(layerPanel.layerStack.activeLayerIndex)
                }
            }
        }

        Cura.SecondaryButton
        {
            text: catalog.i18nc("@button", "Merge Down")
            enabled: layerPanel.layerStack ? layerPanel.layerStack.activeLayerIndex > 0 : false
            onClicked:
            {
                if (layerPanel.layerStack)
                {
                    layerPanel.layerStack.mergeDown(layerPanel.layerStack.activeLayerIndex)
                }
            }
        }
    }

    RowLayout
    {
        spacing: UM.Theme.getSize("default_margin").width / 2

        Cura.SecondaryButton
        {
            text: layerPanel.layerStack && layerPanel.layerStack.isolatedMode
                  ? catalog.i18nc("@button", "Exit Isolate")
                  : catalog.i18nc("@button", "Isolate")
            onClicked:
            {
                if (layerPanel.layerStack)
                {
                    layerPanel.layerStack.toggleIsolatedMode()
                }
            }
        }

        Cura.SecondaryButton
        {
            text: catalog.i18nc("@button", "Flatten All")
            enabled: layerPanel.layerStack ? layerPanel.layerStack.layerCount > 1 : false
            onClicked:
            {
                if (layerPanel.layerStack)
                {
                    layerPanel.layerStack.flattenAll()
                }
            }
        }
    }

    // Layer opacity slider
    RowLayout
    {
        visible: layerPanel.layerStack !== null

        UM.Label
        {
            text: catalog.i18nc("@label", "Layer Opacity")
        }

        UM.Slider
        {
            id: layerOpacitySlider
            Layout.fillWidth: true
            indicatorVisible: false

            from: 0
            to: 100
            value: layerPanel.layerStack ? layerPanel.layerStack.data(
                       layerPanel.layerStack.index(layerPanel.layerStack.activeLayerIndex, 0),
                       259  /* OpacityRole = UserRole + 3 */
                   ) * 100 : 100

            onPressedChanged: function(pressed)
            {
                if (!pressed && layerPanel.layerStack)
                {
                    layerPanel.layerStack.setLayerOpacity(
                        layerPanel.layerStack.activeLayerIndex,
                        layerOpacitySlider.value / 100.0
                    )
                }
            }
        }
    }
}
