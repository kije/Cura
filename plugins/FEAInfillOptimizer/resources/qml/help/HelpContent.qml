// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.

// Loads help_content.json and manages the popover lifecycle.
// Instantiated once in BoundaryConditionPanel.qml.

import QtQuick 2.15

QtObject
{
    id: helpContentManager

    property var entries: ({})
    property var examples: ({})
    property bool loaded: false

    // Reference to the popover component instance (set by parent)
    property var popoverItem: null

    Component.onCompleted:
    {
        var xhr = new XMLHttpRequest()
        var url = Qt.resolvedUrl("../../help/help_content.json")
        xhr.open("GET", url, false)
        xhr.send()
        if (xhr.status === 200 || xhr.status === 0)
        {
            try
            {
                var data = JSON.parse(xhr.responseText)
                entries = data.entries || {}
                examples = data.examples || {}
                loaded = true
            }
            catch (e)
            {
                console.warn("HelpContent: Failed to parse help_content.json:", e)
            }
        }
        else
        {
            console.warn("HelpContent: Failed to load help_content.json, status:", xhr.status)
        }
    }

    function getTooltip(entryId)
    {
        if (!loaded || !(entryId in entries)) return ""
        return entries[entryId].tooltip || ""
    }

    function getEntry(entryId)
    {
        if (!loaded || !(entryId in entries)) return null
        return entries[entryId]
    }

    function openPopover(entryId, anchor)
    {
        if (!loaded || !(entryId in entries)) return
        if (!popoverItem) return

        var entry = entries[entryId]
        var guide = entry.guide || {}

        popoverItem.title = entry.title || ""
        popoverItem.imagePath = guide.image ? Qt.resolvedUrl("../../" + guide.image) : ""
        popoverItem.imageAlt = guide.image_alt || ""
        popoverItem.body = guide.body || ""
        popoverItem.steps = guide.steps || []
        popoverItem.tips = guide.tips || []

        // Position near the anchor
        var pos = anchor.mapToItem(popoverItem.parent, 0, anchor.height)
        popoverItem.x = Math.max(0, Math.min(pos.x - popoverItem.width / 2, popoverItem.parent.width - popoverItem.width))
        popoverItem.y = Math.max(0, Math.min(pos.y + 4, popoverItem.parent.height - popoverItem.height))

        popoverItem.open()
    }
}
