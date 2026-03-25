// Copyright (c) 2024 Community
// Released under the terms of the LGPLv3 or higher.

import QtQuick 2.15
import QtQuick.Controls 2.15

import UM 1.5 as UM
import Cura 1.0 as Cura

Item
{
    UM.I18nCatalog { id: catalog; name: "cura" }

    Cura.Menu
    {
        id: modelGroupsMenu
        title: catalog.i18nc("@item:inmenu", "Model Visibility")

        Cura.MenuItem
        {
            text: catalog.i18nc("@action:inmenu", "Hide Selected Model(s)")
            enabled: UM.Selection.hasSelection
            onTriggered: manager.hideSelectedModels()
        }

        Cura.MenuItem
        {
            text: catalog.i18nc("@action:inmenu", "Show Selected Model(s)")
            enabled: UM.Selection.hasSelection
            onTriggered: manager.showSelectedModels()
        }

        Cura.MenuSeparator {}

        Cura.MenuItem
        {
            text: catalog.i18nc("@action:inmenu", "Manage Groups...")
            onTriggered: manager.showPopup()
        }
    }

    Cura.MenuSeparator
    {
        id: modelGroupsSeparator
    }

    function moveToContextMenu(contextMenu)
    {
        contextMenu.insertItem(0, modelGroupsSeparator)
        contextMenu.insertMenu(0, modelGroupsMenu)
    }
}
