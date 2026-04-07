# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

from typing import Any, Dict, List

from cura.CuraApplication import CuraApplication
from cura.Operations.SetParentOperation import SetParentOperation
from cura.Scene.BuildPlateDecorator import BuildPlateDecorator
from cura.Scene.CuraSceneNode import CuraSceneNode
from cura.Scene.SliceableObjectDecorator import SliceableObjectDecorator
from cura.Settings.SettingOverrideDecorator import SettingOverrideDecorator
from UM.Mesh.MeshData import MeshData
from UM.Operations.AddSceneNodeOperation import AddSceneNodeOperation
from UM.Operations.GroupedOperation import GroupedOperation
from UM.Settings.SettingInstance import SettingInstance


def create_all_modifier_meshes(
    parent_node: CuraSceneNode,
    zones: List[Dict[str, Any]],
    infill_pattern: str = "gyroid",
) -> None:
    """Create CuraSceneNode modifier meshes for every density zone and add
    them to the scene under ``parent_node``.

    Each zone receives an *infill mesh* modifier with:
    - ``infill_mesh = True``
    - ``infill_sparse_density`` set to the zone's density percentage
    - ``infill_pattern`` set to ``infill_pattern``

    All scene-graph mutations are batched into a single
    :class:`~UM.Operations.GroupedOperation.GroupedOperation` so that the
    operation can be undone as one step.

    Args:
        parent_node: The scene node that will be the logical parent of all
            generated modifier meshes.
        zones: List of dicts, each with keys:

            * ``"density"`` (float, 0–1) – normalised infill density.
            * ``"mesh_data"`` (:class:`~UM.Mesh.MeshData.MeshData`) – the
              triangulated boundary surface for this zone.
        infill_pattern: Cura infill pattern name (e.g. ``"gyroid"``,
            ``"triangles"``).  Defaults to ``"gyroid"``.
    """
    application = CuraApplication.getInstance()
    controller = application.getController()
    scene = controller.getScene()

    active_build_plate = application.getMultiBuildPlateModel().activeBuildPlate

    grouped_op = GroupedOperation()

    for zone in zones:
        density: float = zone["density"]
        mesh_data: MeshData = zone["mesh_data"]
        density_pct = density * 100.0

        node = CuraSceneNode()
        if density_pct < 40:
            zone_label = "Low"
        elif density_pct < 65:
            zone_label = "Medium"
        else:
            zone_label = "High"
        node.setName(f"FEA Zone: {zone_label} ({density_pct:.0f}%)")
        node.setSelectable(True)
        node.setMeshData(mesh_data)
        node.setCalculateBoundingBox(True)
        node.calculateBoundingBoxMesh()

        node.addDecorator(SettingOverrideDecorator())
        node.addDecorator(BuildPlateDecorator(active_build_plate))
        node.addDecorator(SliceableObjectDecorator())

        # The SettingOverrideDecorator provides a per-object container stack.
        # We add setting instances to the *top* container (user changes)
        # — exactly as SupportEraser does.
        stack = node.callDecoration("getStack")
        if stack is not None:
            settings = stack.getTop()

            # First pass: set infill_mesh=True so the definition resolves
            # correctly before the companion settings are applied.
            definition = stack.getSettingDefinition("infill_mesh")
            if definition is not None:
                instance = SettingInstance(definition, settings)
                instance.setProperty("value", True)
                instance.resetState()
                settings.addInstance(instance)
            else:
                from UM.Logger import Logger
                Logger.log("e", "FEA Infill: 'infill_mesh' setting definition not found. "
                           "Zone '%s' will not function as an infill modifier.", node.getName())

            # Second pass: companion settings required by Cura on infill meshes.
            for key, value in [
                ("wall_thickness", 0),
                ("top_bottom_thickness", 0),
                ("infill_sparse_density", density_pct),
                ("infill_pattern", infill_pattern),
            ]:
                definition = stack.getSettingDefinition(key)
                if definition is not None:
                    instance = SettingInstance(definition, settings)
                    instance.setProperty("value", value)
                    instance.resetState()
                    settings.addInstance(instance)

        grouped_op.addOperation(AddSceneNodeOperation(node, scene.getRoot()))
        grouped_op.addOperation(SetParentOperation(node, parent_node))

    grouped_op.push()
    scene.sceneChanged.emit(parent_node)
