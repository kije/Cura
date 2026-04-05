// Copyright (c) 2024 FEA Infill Contributors
// Released under the terms of the LGPLv3 or higher.
//
// This file is intentionally left as a stub.
// The FEA Optimizer UI is now fully inline in BoundaryConditionPanel.qml
// via the phase-based flow (define → optimize → running → review).

import QtQuick 2.15
import UM 1.5 as UM

UM.Dialog
{
    id: feaDialog
    title: ""
    width: 0
    height: 0
    visible: false
    property var manager
}
