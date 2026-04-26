# Copyright (c) 2026 Community Contributors
# Released under the terms of the LGPLv3 or higher.

import copy
from typing import Dict, List, Optional, Set

from UM.Logger import Logger
from UM.Settings.InstanceContainer import InstanceContainer
from UM.Settings.ContainerRegistry import ContainerRegistry

from cura.CuraApplication import CuraApplication
from cura.Settings.ExtruderStack import ExtruderStack
from cura.Settings.GlobalStack import GlobalStack
from cura.Settings.CuraStackBuilder import CuraStackBuilder
from cura.Machines.ContainerTree import ContainerTree


VIRTUAL_EXTRUDER_TAG = "mixed_color_virtual"


class VirtualExtruderManager:
    """Manages runtime creation and removal of virtual extruder stacks.

    Virtual extruders are added to the active machine at runtime so users
    can assign objects to them via Cura's standard per-object extruder picker.
    During G-code post-processing, tool commands for virtual extruders are
    rewritten with dithered tool changes between two physical extruders.

    Virtual extruders are re-created from saved mixed filament configs
    each time the plugin loads — they are NOT persisted as permanent
    machine extruder trains.
    """

    def __init__(self) -> None:
        self._virtual_positions: Set[int] = set()

    @property
    def virtual_positions(self) -> Set[int]:
        return self._virtual_positions

    def ensure_virtual_extruders(self, count: int) -> List[int]:
        """Ensure at least `count` virtual extruders exist on the active machine.

        Returns list of virtual extruder positions (0-based indices).
        """
        global_stack = CuraApplication.getInstance().getGlobalContainerStack()
        if not global_stack:
            return []

        current_max = self._get_max_defined_position(global_stack)
        existing_virtual = self._get_existing_virtual_positions(global_stack)

        needed = count - len(existing_virtual)
        new_positions = []

        if needed > 0:
            for i in range(needed):
                new_pos = current_max + 1 + i
                self._create_virtual_extruder(global_stack, new_pos)
                new_positions.append(new_pos)
                existing_virtual.add(new_pos)

        self._virtual_positions = existing_virtual

        # Update machine_extruder_count to include virtual extruders
        total = self._get_max_defined_position(global_stack) + 1
        self._set_extruder_count(global_stack, total)

        return sorted(existing_virtual)

    def remove_all_virtual_extruders(self) -> None:
        """Remove all virtual extruders from the active machine."""
        global_stack = CuraApplication.getInstance().getGlobalContainerStack()
        if not global_stack:
            return

        registry = CuraApplication.getInstance().getContainerRegistry()
        positions_to_remove = self._get_existing_virtual_positions(global_stack)

        for pos in positions_to_remove:
            pos_str = str(pos)
            if pos_str in global_stack._extruders:
                extruder = global_stack._extruders[pos_str]
                # Remove from registry
                try:
                    registry.removeContainer(extruder.userChanges.getId())
                except Exception:
                    pass
                try:
                    registry.removeContainer(extruder.definitionChanges.getId())
                except Exception:
                    pass
                try:
                    registry.removeContainer(extruder.getId())
                except Exception:
                    pass
                del global_stack._extruders[pos_str]

        # Remove virtual entries from machine_extruder_trains
        trains = dict(global_stack.getMetaDataEntry("machine_extruder_trains", {}))
        for pos in positions_to_remove:
            trains.pop(str(pos), None)
        global_stack.setMetaDataEntry("machine_extruder_trains", trains)

        # Reset extruder count to physical only
        if trains:
            physical_count = max(int(p) for p in trains.keys()) + 1
        else:
            physical_count = 1
        self._set_extruder_count(global_stack, physical_count)

        global_stack.extrudersChanged.emit()
        self._virtual_positions.clear()

        Logger.log("i", f"MixedColor: Removed {len(positions_to_remove)} virtual extruders")

    def get_physical_extruder_count(self) -> int:
        """Return the number of physical (non-virtual) extruders."""
        global_stack = CuraApplication.getInstance().getGlobalContainerStack()
        if not global_stack:
            return 1
        trains = global_stack.getMetaDataEntry("machine_extruder_trains", {})
        count = 0
        for pos_str, def_id in trains.items():
            if not str(def_id).startswith(VIRTUAL_EXTRUDER_TAG):
                count += 1
        return max(1, count)

    def _create_virtual_extruder(self, global_stack: GlobalStack, position: int) -> Optional[ExtruderStack]:
        """Create a single virtual extruder at the given position."""
        application = CuraApplication.getInstance()
        registry = application.getContainerRegistry()

        # Use the first physical extruder's definition as a template
        # This ensures compatible materials, variants, etc.
        template_extruder = self._get_template_extruder(global_stack)
        if template_extruder is None:
            Logger.log("e", "MixedColor: No template extruder found to base virtual extruder on")
            return None

        # Add to machine_extruder_trains metadata so maxExtruderCount includes it
        trains = dict(global_stack.getMetaDataEntry("machine_extruder_trains", {}))
        virtual_def_id = f"{VIRTUAL_EXTRUDER_TAG}_{position}"
        trains[str(position)] = virtual_def_id
        global_stack.setMetaDataEntry("machine_extruder_trains", trains)

        # Create the extruder stack by cloning the template's structure
        new_id = registry.uniqueName(f"mixed_color_virtual_extruder_{position}")
        stack = ExtruderStack(new_id)

        # Use the same definition as the template
        stack.setDefinition(template_extruder.definition)
        stack.setName(f"Virtual Extruder {position + 1} (Mixed)")
        stack.setMetaDataEntry("position", str(position))
        stack.setMetaDataEntry(VIRTUAL_EXTRUDER_TAG, "true")

        # Create user changes container
        user_container = CuraStackBuilder.createUserChangesContainer(
            new_id + "_user",
            global_stack.definition.getId(),
            new_id,
            is_global_stack=False
        )

        # Set up the stack layers — copy from template for compatibility
        stack.definitionChanges = CuraStackBuilder.createDefinitionChangesContainer(stack, new_id + "_settings")
        stack.variant = template_extruder.variant
        stack.material = template_extruder.material
        stack.quality = template_extruder.quality
        stack.intent = application.empty_intent_container
        stack.qualityChanges = application.empty_quality_changes_container
        stack.userChanges = user_container

        stack.setNextStack(global_stack)

        # Register
        registry.addContainer(user_container)
        registry.addContainer(stack)

        # Add to global stack
        global_stack.addExtruder(stack)

        self._virtual_positions.add(position)
        Logger.log("i", f"MixedColor: Created virtual extruder at position {position} (id={new_id})")

        return stack

    def _get_template_extruder(self, global_stack: GlobalStack) -> Optional[ExtruderStack]:
        """Get the first physical extruder to use as a template."""
        for pos_str in sorted(global_stack._extruders.keys(), key=int):
            extruder = global_stack._extruders[pos_str]
            if not extruder.getMetaDataEntry(VIRTUAL_EXTRUDER_TAG):
                return extruder
        return None

    def _get_max_defined_position(self, global_stack: GlobalStack) -> int:
        """Get the highest extruder position currently defined."""
        if not global_stack._extruders:
            return -1
        return max(int(pos) for pos in global_stack._extruders.keys())

    def _get_existing_virtual_positions(self, global_stack: GlobalStack) -> Set[int]:
        """Get positions of existing virtual extruders."""
        positions = set()
        for pos_str, extruder in global_stack._extruders.items():
            if extruder.getMetaDataEntry(VIRTUAL_EXTRUDER_TAG):
                positions.add(int(pos_str))
        return positions

    def _set_extruder_count(self, global_stack: GlobalStack, count: int) -> None:
        """Set the machine_extruder_count on the global stack."""
        definition_changes = global_stack.definitionChanges
        if definition_changes:
            definition_changes.setProperty("machine_extruder_count", "value", count)
