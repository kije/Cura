# Settings Inheritance: Mixin System Design

## Overview

This document describes the design for a **mixin system** that allows Cura quality profiles to
include reusable "mixin" overlays. Mixins are applied as ephemeral runtime overlays — they are
not persisted as part of the profile, but re-computed whenever the mixin configuration changes.

## Requirements

1. **Mixin values override custom profile (QualityChanges) values** — a mixin takes priority over
   the base custom profile settings it's applied to.
2. **User changes (UI tweaks) still override mixins** — the UserChanges container (index 0) remains
   the highest-priority layer.
3. **Mixin values show the blue "user modified" dot** — this is acceptable and desired behavior.
4. **Mixin values are NOT cleared by "Reset User Changes"** — that operation only clears index 0.
5. **Mixin values ARE cleared when the mixin is removed** from the active mixin list.
6. **The base profile remains untouched on disk** — mixins are a runtime overlay.
7. **Re-computed on every mixin change** — add, remove, or reorder.
8. **Ordering matters** — later mixins override earlier ones for conflicting settings.

## Architecture

### Container Stack Context

Cura's container stack resolves settings top-to-bottom:

```
Index 0: UserChanges      (type: "user")           ← UI tweaks, highest priority
Index 1: QualityChanges    (type: "quality_changes") ← Custom profile values
Index 2: Intent            (type: "intent")
Index 3: Quality           (type: "quality")         ← Base quality profile
Index 4: Material          (type: "material")
Index 5: Variant           (type: "variant")
Index 6: DefinitionChanges (type: "definition_changes")
Index 7: Definition        (type: "definition")      ← Machine defaults, lowest priority
```

### Design: Virtual Container at Index 1

We introduce a `MixinQualityChangesContainer` — a subclass of `InstanceContainer` that wraps
the real QualityChanges container and overlays mixin values on top.

```
Index 0: UserChanges                               ← Still highest priority (unchanged)
Index 1: MixinQualityChangesContainer              ← NEW: wraps QualityChanges + mixins
           ├── mixin_n (last added, highest mixin priority)
           ├── ...
           ├── mixin_1 (first added, lowest mixin priority)
           └── wrapped QualityChanges (original custom profile, lowest priority in this layer)
Index 2: Intent                                    (unchanged)
Index 3: Quality                                   (unchanged)
...
```

**Property resolution within MixinQualityChangesContainer:**

```python
def getProperty(self, key, property_name, context=None):
    # 1. Check mixins in reverse order (last mixin = highest priority)
    for mixin in reversed(self._mixins):
        value = mixin.getProperty(key, property_name, context)
        if value is not None:
            return value
    # 2. Fall through to wrapped QualityChanges
    return self._wrapped_quality_changes.getProperty(key, property_name, context)
```

### Class: `MixinQualityChangesContainer`

```python
class MixinQualityChangesContainer(InstanceContainer):
    """A virtual container that overlays mixin values on top of a QualityChanges container.

    This container is ephemeral — it is never serialized to disk. It delegates to the
    wrapped QualityChanges container for all persistence operations.
    """

    mixinsChanged = Signal()  # Emitted when mixin list changes

    def __init__(self, wrapped_quality_changes: InstanceContainer):
        # Use a derived ID so it's unique but traceable
        super().__init__(f"mixin_wrapped_{wrapped_quality_changes.id}")
        self._wrapped_quality_changes = wrapped_quality_changes
        self._mixins: List[InstanceContainer] = []

        # Forward property changes from wrapped container
        self._wrapped_quality_changes.propertyChanged.connect(self.propertyChanged)

    @property
    def wrappedQualityChanges(self) -> InstanceContainer:
        """Access the underlying QualityChanges container (for merge operations, etc.)."""
        return self._wrapped_quality_changes

    def setWrappedQualityChanges(self, new_qc: InstanceContainer):
        """Replace the wrapped QualityChanges (e.g. when switching profiles)."""
        self._wrapped_quality_changes.propertyChanged.disconnect(self.propertyChanged)
        self._wrapped_quality_changes = new_qc
        self._wrapped_quality_changes.propertyChanged.connect(self.propertyChanged)
        # Emit changes for all keys
        self._emitAllPropertyChanges()

    def setMixins(self, mixins: List[InstanceContainer]):
        """Replace the full mixin list. Triggers re-computation."""
        old_keys = self._getAllMixinKeys()

        # Disconnect old mixin signals
        for mixin in self._mixins:
            mixin.propertyChanged.disconnect(self.propertyChanged)

        self._mixins = list(mixins)

        # Connect new mixin signals
        for mixin in self._mixins:
            mixin.propertyChanged.connect(self.propertyChanged)

        new_keys = self._getAllMixinKeys()

        # Emit propertyChanged for all affected keys
        for key in old_keys | new_keys:
            self.propertyChanged.emit(key, "value")

        self.mixinsChanged.emit()

    def addMixin(self, mixin: InstanceContainer):
        """Add a mixin to the end of the list (highest priority)."""
        self._mixins.append(mixin)
        mixin.propertyChanged.connect(self.propertyChanged)
        for key in mixin.getAllKeys():
            self.propertyChanged.emit(key, "value")
        self.mixinsChanged.emit()

    def removeMixin(self, mixin_id: str):
        """Remove a mixin by ID. Its values disappear immediately."""
        for i, mixin in enumerate(self._mixins):
            if mixin.id == mixin_id:
                affected_keys = set(mixin.getAllKeys())
                mixin.propertyChanged.disconnect(self.propertyChanged)
                self._mixins.pop(i)
                for key in affected_keys:
                    self.propertyChanged.emit(key, "value")
                self.mixinsChanged.emit()
                return

    # --- ContainerInterface overrides ---

    def getProperty(self, key, property_name, context=None):
        # Check mixins first (reverse order = last added has highest priority)
        for mixin in reversed(self._mixins):
            value = mixin.getProperty(key, property_name, context)
            if value is not None:
                return value
        # Fall through to wrapped QualityChanges
        return self._wrapped_quality_changes.getProperty(key, property_name, context)

    def hasProperty(self, key, property_name):
        for mixin in self._mixins:
            if mixin.hasProperty(key, property_name):
                return True
        return self._wrapped_quality_changes.hasProperty(key, property_name)

    def getAllKeys(self):
        keys = set(self._wrapped_quality_changes.getAllKeys())
        for mixin in self._mixins:
            keys |= set(mixin.getAllKeys())
        return keys

    # Metadata delegated to wrapped container
    def getMetaDataEntry(self, entry, default=None):
        return self._wrapped_quality_changes.getMetaDataEntry(entry, default)

    def getMetaData(self):
        return self._wrapped_quality_changes.getMetaData()

    def getName(self):
        return self._wrapped_quality_changes.getName()

    @property
    def id(self):
        return self._wrapped_quality_changes.id

    def isReadOnly(self):
        return self._wrapped_quality_changes.isReadOnly()

    # Serialization: delegate to wrapped container (mixins are ephemeral)
    def serialize(self, ignored_metadata_keys=None):
        return self._wrapped_quality_changes.serialize(ignored_metadata_keys)

    # setProperty: delegate to wrapped container (user changes go to index 0, not here)
    def setProperty(self, key, property_name, property_value, container=None, set_from_cache=False):
        self._wrapped_quality_changes.setProperty(key, property_name, property_value, container, set_from_cache)

    def clear(self):
        """Clear only the wrapped QualityChanges, not the mixins."""
        self._wrapped_quality_changes.clear()

    # --- Internal helpers ---

    def _getAllMixinKeys(self):
        keys = set()
        for mixin in self._mixins:
            keys |= set(mixin.getAllKeys())
        return keys

    def _emitAllPropertyChanges(self):
        for key in self.getAllKeys():
            self.propertyChanged.emit(key, "value")
```

### Interaction with Existing Code

#### `stack.qualityChanges` property access

`CuraContainerStack.qualityChanges` returns `self._containers[1]`. After wrapping, this returns
the `MixinQualityChangesContainer`. Code that accesses `stack.qualityChanges` will transparently
get the mixin-overlaid values.

**For code that needs the raw QualityChanges** (e.g., `updateQualityChanges()` which merges
UserChanges into QualityChanges):

```python
quality_changes = stack.qualityChanges
# If wrapped, get the inner container
if isinstance(quality_changes, MixinQualityChangesContainer):
    quality_changes = quality_changes.wrappedQualityChanges
```

#### `setQualityChanges()` — profile switching

When the user switches profiles, `MachineManager._setQualityGroup()` sets
`stack.qualityChanges = empty_quality_changes_container`. We need to intercept this to update
the wrapper rather than replace it:

**Option A (recommended):** Override `setQualityChanges()` in CuraContainerStack to detect the wrapper:

```python
def setQualityChanges(self, new_quality_changes, postpone_emit=False):
    current = self._containers[_ContainerIndexes.QualityChanges]
    if isinstance(current, MixinQualityChangesContainer):
        # Update the wrapped container, keep the mixin wrapper in place
        current.setWrappedQualityChanges(new_quality_changes)
        # MixinManager will read active_mixins from new_quality_changes metadata
        # and call setMixins() accordingly
        if not postpone_emit:
            self.containersChanged.emit(current)
    else:
        self.replaceContainer(_ContainerIndexes.QualityChanges, new_quality_changes, postpone_emit)
```

After `setQualityChanges()`, the `MixinManager` reacts (via signal) and:
1. Reads `active_mixins` from the new QualityChanges metadata
2. Resolves mixin IDs to mixin containers via `MixinRegistry`
3. Calls `wrapper.setMixins(resolved_mixins)` to update the overlay
4. If no mixins are referenced, unwraps the container

#### `clearUserContainers()` — "Reset User Changes"

This clears `stack.userChanges` (index 0) only. No changes needed — mixins at index 1 are
untouched. ✓

#### `hasUserValue()` — blue dot indicator

Returns `True` for settings in index 0 or index 1. Since the mixin wrapper is at index 1,
mixin-set values will show the blue dot. This is the desired behavior. ✓

#### `updateQualityChanges()` — "Update Profile with Current Settings"

This merges UserChanges into QualityChanges via `_performMerge()`. We need to ensure the merge
targets the **wrapped** QualityChanges, not the mixin container:

```python
# In ContainerManager.updateQualityChanges():
quality_changes = stack.qualityChanges
if isinstance(quality_changes, MixinQualityChangesContainer):
    quality_changes = quality_changes.wrappedQualityChanges
# ... existing merge logic using quality_changes
```

## Mixin Definition Format

Mixins are stored as standard `InstanceContainer` files (INI format), with type `"mixin"`:

```ini
[general]
version = 4
name = PETG General
definition = fdmprinter

[metadata]
type = mixin
setting_version = 23
description = General PETG temperature and retraction settings
tags = material:petg;category:temperature

[values]
material_print_temperature = 230
material_bed_temperature = 80
retraction_amount = 6
retraction_speed = 25
```

### Storage Location

Mixins are stored in the Cura resources directory:

```
<cura_resources>/mixins/           # Built-in mixins shipped with Cura
<cura_user_data>/mixins/           # User-created mixins
```

### Mixin Registry

A `MixinRegistry` (or extension of `ContainerRegistry`) manages mixin discovery and loading:

```python
class MixinRegistry:
    """Discovers, loads, and manages available mixins."""

    mixinsChanged = Signal()

    def __init__(self):
        self._available_mixins: Dict[str, InstanceContainer] = {}

    def loadMixins(self):
        """Scan mixin directories and load all .cfg.mixin files."""
        ...

    def getMixin(self, mixin_id: str) -> Optional[InstanceContainer]:
        return self._available_mixins.get(mixin_id)

    def getAvailableMixins(self) -> List[InstanceContainer]:
        return list(self._available_mixins.values())

    def createMixin(self, name: str, settings: Dict[str, Any]) -> InstanceContainer:
        """Create a new user mixin from settings."""
        ...

    def saveMixin(self, mixin: InstanceContainer):
        """Persist a mixin to the user data directory."""
        ...
```

## Mixin Configuration Persistence

The list of active mixins is stored as **metadata on the QualityChanges container** itself.
Each profile carries its own mixin references:

```ini
[metadata]
type = quality_changes
quality_type = normal
setting_version = 23
active_mixins = ["mixin_petg_general", "mixin_retraction_conservative"]
```

This means:
- **Each profile declares its own mixins** — switching profiles automatically switches mixins
- **Saving a new profile inherits the mixin list** — the `active_mixins` metadata is copied
  to the new QualityChanges container, but mixin VALUES are NOT baked in
- **Mixin references are portable** — exporting a profile includes the mixin IDs in metadata;
  on import, if the referenced mixins exist, they are automatically applied
- **No machine-level mixin state** — the machine doesn't need to track which mixins go with
  which profile; the profile knows

### What Gets Saved When Creating a New Profile

When the user clicks "Create Profile from Current Settings" with mixins active:

1. A new QualityChanges container is created
2. **Only the wrapped (real) QualityChanges values + UserChanges are merged in** — mixin
   values are excluded from the merge
3. The `active_mixins` metadata from the source profile is **copied** to the new profile
4. On activation, the MixinManager reads `active_mixins` and re-applies the mixin overlays

```python
# In QualityManagementModel.createQualityChanges():
quality_changes_container = stack.qualityChanges
if isinstance(quality_changes_container, MixinQualityChangesContainer):
    # Copy mixin references to new profile metadata
    mixin_ids = quality_changes_container.wrappedQualityChanges.getMetaDataEntry("active_mixins", [])
    new_changes.setMetaDataEntry("active_mixins", mixin_ids)
    # Merge only the real QualityChanges values (exclude mixin values)
    quality_changes_container = quality_changes_container.wrappedQualityChanges
container_manager._performMerge(new_changes, quality_changes_container, clear_settings=False)
container_manager._performMerge(new_changes, stack.userChanges)
```

This way the new profile:
- Contains only "real" custom profile values (not mixin values)
- Automatically gets the same mixins applied at runtime
- Stays in sync if the mixin definitions are later updated

## Lifecycle & Integration Points

### Startup

1. `MixinRegistry.loadMixins()` — scan and load all available mixin definitions
2. For each stack (global + extruders), read `active_mixins` from QualityChanges metadata
3. If mixins are referenced, wrap QualityChanges with `MixinQualityChangesContainer` and apply

### Quality Profile Change

1. `MachineManager._setQualityGroup()` or `_setQualityChangesGroup()` sets new containers
2. The overridden `setQualityChanges()` updates the wrapped container inside `MixinQualityChangesContainer`
3. `MixinManager` reads `active_mixins` from the **new** QualityChanges container's metadata
4. Calls `mixinContainer.setMixins(new_mixin_list)` — triggers `propertyChanged` for affected keys
5. If the new profile has no mixins, the wrapper is unwrapped (removed)

### Mixin Add/Remove (User Action)

1. User adds/removes a mixin via UI
2. Call `mixinContainer.addMixin()` / `mixinContainer.removeMixin()` on affected stacks
3. Update the `active_mixins` metadata on the **QualityChanges container** (persisted with profile)
4. `propertyChanged` signals propagate → UI updates automatically

### Profile Export

When exporting a profile with mixins:
- Standard `.curaprofile` export: QualityChanges are exported with `active_mixins` in metadata
  (mixin references only, not resolved values)
- The mixin definitions themselves are NOT bundled in standard export
- Extended export (future): could bundle mixin definitions alongside the profile in the ZIP

### Profile Import

- Standard import restores QualityChanges with `active_mixins` metadata intact
- On activation, `MixinManager` reads the mixin references and applies available mixins
- If a referenced mixin is not installed, it is silently skipped (with a log warning)
- User can install missing mixins separately

## MixinManager (Orchestrator)

```python
class MixinManager(QObject):
    """Orchestrates mixin application across container stacks.

    Responsible for:
    - Wrapping/unwrapping QualityChanges containers with MixinQualityChangesContainer
    - Loading active mixin configuration from machine metadata
    - Responding to quality profile changes
    - Persisting mixin configuration
    """

    activeMixinsChanged = pyqtSignal()

    def __init__(self, application):
        self._application = application
        self._mixin_registry = MixinRegistry()

    def initialize(self):
        """Called during application startup."""
        self._mixin_registry.loadMixins()
        machine_manager = self._application.getMachineManager()
        machine_manager.activeMachineChanged.connect(self._onActiveMachineChanged)
        machine_manager.activeQualityGroupChanged.connect(self._onQualityChanged)

    def getActiveMixins(self, stack) -> List[InstanceContainer]:
        """Get the active mixins for a stack's current quality profile."""
        ...

    def setActiveMixins(self, stack, mixin_ids: List[str]):
        """Set the active mixins for the current quality profile on a stack."""
        ...

    def _onActiveMachineChanged(self):
        """Re-apply mixins when machine changes."""
        self._applyMixinsToAllStacks()

    def _onQualityChanged(self):
        """Re-apply mixins when quality profile changes."""
        self._applyMixinsToAllStacks()

    def _applyMixinsToAllStacks(self):
        """Ensure all stacks have the correct mixin wrapper with correct mixins."""
        global_stack = self._application.getMachineManager().activeMachine
        if not global_stack:
            return
        for stack in [global_stack] + global_stack.extruderList:
            self._applyMixinsToStack(stack)

    def _applyMixinsToStack(self, stack):
        """Apply or update mixins on a single stack."""
        current_qc = stack.qualityChanges
        # Read mixin references from QualityChanges metadata
        raw_qc = current_qc.wrappedQualityChanges if isinstance(current_qc, MixinQualityChangesContainer) else current_qc
        mixin_ids = raw_qc.getMetaDataEntry("active_mixins", [])
        mixins = [self._mixin_registry.getMixin(mid) for mid in mixin_ids if self._mixin_registry.getMixin(mid)]

        if not mixins:
            # No mixins — unwrap if currently wrapped
            if isinstance(current_qc, MixinQualityChangesContainer):
                stack.replaceContainer(1, current_qc.wrappedQualityChanges)
            return

        if isinstance(current_qc, MixinQualityChangesContainer):
            # Already wrapped — just update mixins
            current_qc.setMixins(mixins)
        else:
            # Wrap the QualityChanges container
            wrapper = MixinQualityChangesContainer(current_qc)
            wrapper.setMixins(mixins)
            stack.replaceContainer(1, wrapper)
```

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `cura/Settings/MixinQualityChangesContainer.py` | Virtual container class |
| `cura/Settings/MixinManager.py` | Orchestrator for mixin lifecycle |
| `cura/Settings/MixinRegistry.py` | Mixin discovery and loading |

### Modified Files

| File | Change |
|------|--------|
| `cura/Settings/CuraContainerStack.py` | Override `setQualityChanges()` to handle wrapper |
| `cura/Settings/ContainerManager.py` | `updateQualityChanges()` unwraps before merge |
| `cura/CuraApplication.py` | Initialize `MixinManager` |
| `cura/Settings/MachineManager.py` | Connect mixin signals, expose mixin API to QML |

## Edge Cases

1. **Empty QualityChanges + Mixins**: Wrapper wraps `empty_quality_changes_container`, mixins
   still overlay. Works correctly — mixin values resolve, empty QC has no values to conflict.

2. **Profile save ("Create Profile from Current Settings")**: Only real QualityChanges values +
   UserChanges are merged into the new profile. Mixin values are excluded. The `active_mixins`
   metadata is copied to the new profile so the same mixins are re-applied at runtime. ✓

3. **3MF workspace export**: `ThreeMFWorkspaceWriter` iterates `stack.getContainers()` which
   returns `self._containers[1]` (the wrapper). The wrapper's `serialize()` delegates to
   wrapped QualityChanges, so only real profile values are saved. The `active_mixins` metadata
   is included in the serialized output, preserving mixin references. ✓

4. **Per-extruder mixins**: Each extruder stack gets its own `MixinQualityChangesContainer`.
   Mixins can be applied globally or per-extruder. The `active_mixins` metadata can be
   structured to support both.

5. **Mixin conflicts**: When two mixins set the same key, the last mixin in the list wins
   (reverse iteration order). This is by design — mixin ordering is user-controlled.

## Future Extensions

- **UI for mixin management**: QML components for browsing, adding, removing, reordering mixins
- **Mixin marketplace**: Share mixins via community profiles
- **Conditional mixins**: Mixins that auto-activate based on material/machine selection
- **Mixin dependencies**: Declaring that mixin A requires mixin B
