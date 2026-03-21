# Settings Mixins Plugin - Architecture & UX Design

## Problem Statement

Cura users who fine-tune print settings often repeat the same groups of settings
across profiles. For example, "PETG general" speeds/fan settings, or "no supports
with overhang compensation." There is no way to define reusable, composable
bundles of settings that can be mixed into any profile.

**Goal**: Allow users to define named "mixins" (reusable setting bundles) and
compose them in an ordered list within their print profile. Later mixins in the
list override earlier ones on conflict. Mixins can be global or per-extruder.

---

## Cura Architecture Context

### Container Stack (Fixed, 8 Layers)

```
Index 0: UserChanges      (type: "user")               ← highest priority
Index 1: QualityChanges   (type: "quality_changes")     ← custom profile
Index 2: Intent           (type: "intent")              ← e.g. "engineering"
Index 3: Quality          (type: "quality")              ← e.g. "fine"
Index 4: Material         (type: "material")
Index 5: Variant          (type: "variant")
Index 6: DefinitionChanges(type: "definition_changes")
Index 7: Definition       (type: "definition")           ← lowest priority
```

**Critical constraint**: The stack is fixed-size. `addContainer()`,
`insertContainer()`, and `removeContainer()` all raise
`InvalidOperationError`. We cannot add new layers.

### Key Insight

Mixins must work *within* the existing container system. The most natural
integration point is the **QualityChanges** container (index 1), which is
already the "user's custom profile" layer. Mixins compose *into* this layer.

---

## Recommended Architecture: "Compose into QualityChanges"

### Core Concept

Mixins are stored as separate `InstanceContainer` files with a custom type
(`"setting_mixin"`). They are **not** inserted into the container stack.
Instead, when the user activates/reorders mixins, the plugin **merges them
in order into the QualityChanges container**, respecting the user's priority
ordering. Manual user setting changes (UserChanges, index 0) always take
precedence over everything.

```
Effective settings resolution:
  UserChanges (manual tweaks)          ← always wins
  QualityChanges (= base profile       ← composed from:
                   + mixin_1                1. Original profile values
                   + mixin_2                2. Mixin 1 (lowest priority mixin)
                   + mixin_3)               3. Mixin 2
                                            4. Mixin 3 (highest priority mixin)
  Intent
  Quality
  Material / Variant / Definition
```

### Why This Approach

1. **No core stack changes needed** - works entirely within the existing
   QualityChanges container
2. **Idiomatic** - QualityChanges is already the "custom overrides" layer
3. **Compatible** - profiles with mixins can be shared; recipients without the
   plugin just see a normal QualityChanges profile
4. **Transparent** - the final resolved values are always visible in the
   standard settings view

---

## Detailed Design

### 1. Mixin Storage Format

Mixin definitions are stored as **JSON files** in
`~/.local/share/cura/<version>/setting_mixins/` (or the platform equivalent).

```json
// ~/.local/share/cura/5.x/setting_mixins/a1b2c3d4e5f6.json
{
  "id": "a1b2c3d4e5f6",
  "name": "PETG General",
  "description": "Common speed and cooling settings for PETG",
  "scope": "global",
  "color": "#FF6B35",
  "tags": ["petg", "material", "cooling"],
  "settings": {
    "material_print_temperature": 230,
    "material_bed_temperature": 80,
    "speed_print": 50,
    "speed_infill": 60,
    "speed_wall": 40,
    "speed_wall_0": 35,
    "cool_fan_speed": 50,
    "cool_fan_speed_max": 80,
    "cool_fan_full_at_height": 0.6
  }
}
```

**Fields**:
- `id` - unique identifier (auto-generated 12-char hex)
- `name` - user-facing display name
- `scope` - either `"global"` or `"extruder"` (determines where settings apply)
- `description` - user-facing description shown in UI
- `tags` - searchable/filterable tags array
- `color` - hex color for visual identification in the UI
- `settings` - key-value map of setting overrides

### 2. Mixin Activation & Composition

The list of mixins that a profile "includes" is stored as **metadata on the
profile's own QualityChanges container**. This means the includes list is a
property of each profile — different profiles carry different mixin recipes,
and switching profiles automatically switches the active mixin set.

```python
# Metadata key on QualityChanges containers:
INCLUDES_METADATA_KEY = "setting_mixin_includes"

# Value is a JSON array of mixin IDs, in priority order:
# '["petg_general", "no_supports", "fine_detail_walls"]'
```

Reading/writing includes:
```python
MixinManager.read_includes(quality_changes_container) -> List[str]
MixinManager.write_includes(quality_changes_container, mixin_ids: List[str])
```

**Built-in profiles** use the empty QualityChanges singleton, which cannot
carry metadata. When the user first adds a mixin to a built-in profile, the
plugin auto-creates a custom quality_changes profile (e.g., "Fine (with
mixins)") and activates it, so that mixin includes can be stored.

**Composition algorithm** (executed whenever mixins change or profiles switch):

Mixin values are applied to the **UserChanges** container (index 0). The
manager tracks which keys it wrote (`_applied_values` dict per stack) so that
manual user overrides are detected and preserved when re-applying:

```python
def apply_to_stack(self, stack, quality_changes_container):
    # 1. Remove previously-applied mixin keys (only if user hasn't overridden)
    # 2. Compute merged values from profile's includes (later wins)
    # 3. Apply merged values, skipping user-overridden keys
    # 4. Track what we applied for next cycle
```

This approach means:
- User manual changes (in UserChanges) are always preserved
- No sidecar files needed — the original QualityChanges values are untouched
- Toggling/reordering mixins cleanly updates only the mixin-managed keys

### 3. Conflict Resolution

**Rule: Later mixin in the list wins (like CSS cascade).**

The UI visualizes this clearly:

```
┌─────────────────────────────────────┐
│ Active Mixins                   [+] │
│                                     │
│  ☰  1. PETG General         [✎][✕] │  ← lowest priority
│  ☰  2. No Supports          [✎][✕] │
│  ☰  3. Fine Detail Walls    [✎][✕] │  ← highest priority
│                                     │
│  ⚠ 2 conflicts (click to review)   │
└─────────────────────────────────────┘
```

When clicking the conflict warning:

```
┌──────────────────────────────────────────────┐
│ Setting Conflicts                            │
│                                              │
│  speed_wall_0:                               │
│    PETG General:       35 mm/s  (overridden) │
│    Fine Detail Walls:  25 mm/s  ✓ (active)   │
│                                              │
│  cool_fan_speed:                             │
│    PETG General:       50%      (overridden) │
│    Fine Detail Walls:  70%      ✓ (active)   │
│                                              │
│  Tip: Drag mixins to reorder priorities.     │
│  The bottom mixin has the highest priority.  │
└──────────────────────────────────────────────┘
```

### 4. User Interaction with UserChanges

When a user manually changes a setting in the settings panel:
- The change goes into **UserChanges** (index 0) as it does today
- UserChanges always has highest priority, so it naturally overrides mixins
- The mixin panel shows a small indicator: "2 manual overrides over mixins"
- The user can "release" manual overrides back to mixin control

### 5. Plugin Structure

```
plugins/SettingsMixins/
├── plugin.json                    # Plugin metadata (api 8)
├── __init__.py                    # Plugin registration
├── SettingsMixinsExtension.py     # QObject + Extension: QML API, editor state
├── MixinManager.py                # Core logic: CRUD, includes, composition
└── resources/
    └── qml/
        ├── MixinMainWindow.qml    # Main management window (UM.Dialog)
        └── MixinEditorDialog.qml  # Create/edit mixin dialog
```

### 6. UI Design

#### A. Mixin Panel in Print Setup Sidebar

The mixin panel integrates into the Custom Print Setup view, between the
profile selector and the settings list. It's a collapsible section:

```
┌─ Profile ─────────────────────────────────┐
│ [Custom PETG Profile ▾]  [⚠ modified]     │
├─ Setting Mixins ──────────────── [▾ ▸] ──┤
│                                           │
│  Applied mixins (drag to reorder):        │
│                                           │
│  ┌──────────────────────────────────────┐ │
│  │ ☰ 🟠 PETG General          [✎] [✕] │ │
│  │   speeds, cooling · 8 settings       │ │
│  ├──────────────────────────────────────┤ │
│  │ ☰ 🔵 No Supports           [✎] [✕] │ │
│  │   support, overhang · 3 settings     │ │
│  ├──────────────────────────────────────┤ │
│  │ ☰ 🟢 Fine Detail            [✎] [✕]│ │
│  │   walls, top/bottom · 5 settings     │ │
│  └──────────────────────────────────────┘ │
│                                           │
│  [+ Add Mixin ▾]                          │
│                                           │
│  ⚠ 2 conflicts · 1 manual override       │
│                                           │
├─ Settings ────────────────────────────────┤
│  ▸ Quality                                │
│  ▸ Shell                                  │
│  ▾ Speed                                  │
│    Print Speed:  [50    ] ← from mixin 🟠 │
│    ...                                    │
```

**Visual indicators in the settings list**: Settings that come from a mixin
show a small colored dot matching the mixin's color. This lets users instantly
see which settings are mixin-controlled vs. manual.

#### B. "Add Mixin" Dropdown

```
┌─ Add Mixin ──────────────────────────┐
│ 🔍 Search...                         │
│                                      │
│ ── My Mixins ──────────────────────  │
│ 🟠 PETG General                      │
│ 🔴 ABS General                       │
│ 🔵 No Supports                       │
│ 🟢 Fine Detail Walls                 │
│ 🟣 Speed Demon                       │
│                                      │
│ ── From Community ─────────────────  │
│ (import from file...)                │
│                                      │
│ ── Actions ────────────────────────  │
│ [+ Create New Mixin]                 │
│ [↓ Import from File...]              │
└──────────────────────────────────────┘
```

#### C. Mixin Editor Dialog

Accessed via the [✎] button or "Create New Mixin":

```
┌─ Edit Mixin: PETG General ───────────────────────┐
│                                                   │
│  Name:    [PETG General              ]            │
│  Color:   [🟠 ▾]                                  │
│  Scope:   (●) Global  ( ) Per-Extruder            │
│  Desc:    [Common PETG speed/cooling   ]          │
│  Tags:    [petg, material, cooling     ]          │
│                                                   │
│ ┌─ Settings ───────────────────────────────────┐  │
│ │  🔍 Search or add setting...                 │  │
│ │                                              │  │
│ │  material_print_temperature:  [230  ] [✕]    │  │
│ │  material_bed_temperature:    [80   ] [✕]    │  │
│ │  speed_print:                 [50   ] [✕]    │  │
│ │  speed_infill:                [60   ] [✕]    │  │
│ │  speed_wall:                  [40   ] [✕]    │  │
│ │  speed_wall_0:                [35   ] [✕]    │  │
│ │  cool_fan_speed:              [50   ] [✕]    │  │
│ │  cool_fan_speed_max:          [80   ] [✕]    │  │
│ │  cool_fan_full_at_height:     [0.6  ] [✕]    │  │
│ │                                              │  │
│ │  [+ Add setting from current profile]        │  │
│ │  [+ Add setting by name...]                  │  │
│ └──────────────────────────────────────────────┘  │
│                                                   │
│  [Cancel]                    [Save]  [Export...]   │
└───────────────────────────────────────────────────┘
```

**Quick-create workflow**: In the settings panel, right-click a setting →
"Add to mixin..." → pick an existing mixin or create new one.

#### D. Settings List Integration (Mixin Origin Indicators)

In the regular settings list, settings controlled by mixins get a visual
indicator:

```
Speed
  Print Speed         [50    ] 🟠  ← colored dot = from "PETG General"
  Wall Speed          [40    ] 🟠
  Wall Speed (Outer)  [25    ] 🟢  ← from "Fine Detail" (overrode PETG General's 35)
  Infill Speed        [60    ] 🟠
  Travel Speed        [150   ]     ← no dot = from base profile/default
```

Hovering the dot shows a tooltip: "Set by mixin 'PETG General' (value: 50).
Click to override manually."

### 7. Per-Extruder vs. Global Mixins

- **Global-scope mixins** apply their settings to the global stack's
  QualityChanges container.
- **Extruder-scope mixins** are attached per-extruder. Each extruder can have
  its own mixin list. The mixin panel updates when switching extruder tabs.

The mixin's `scope` metadata determines where it appears:
- `scope = global` → shown in global mixin list, applies to global settings
- `scope = extruder` → shown in per-extruder mixin list, applies to extruder settings

If a global mixin contains extruder-specific settings (e.g. `speed_print`),
the plugin applies them to all enabled extruders.

### 8. Persistence & Profile Integration

**Active mixin state** is stored as metadata directly on each QualityChanges
container via the `setting_mixin_includes` metadata key:

```
# On the global QualityChanges container:
qualityChanges.getMetaDataEntry("setting_mixin_includes")
→ '["petg_general", "fine_detail_walls"]'

# On an extruder's QualityChanges container:
extruderQualityChanges.getMetaDataEntry("setting_mixin_includes")
→ '["no_supports"]'
```

This means:
- **The includes list travels WITH the profile** — switching profiles
  automatically switches the active mixin set
- Each profile (global and per-extruder) carries its own includes
- No separate preferences or sidecar files needed

**Built-in profiles** use the empty QualityChanges singleton which cannot
store metadata. The plugin auto-creates a custom profile copy when the user
first adds a mixin, showing a notification in the UI.

**When loading a profile** with mixin metadata:
- If the plugin is installed, it reads includes and applies the mixin
  composition
- If the plugin is NOT installed, the metadata is simply ignored — the
  UserChanges values from the last session remain (graceful degradation)

### 9. Minimal Core Changes Needed

Only 2 small core changes would improve the experience:

1. **ContainerManager: `settingOrigin` signal/method** (optional)
   Add a method that the plugin can hook into to provide "origin" metadata
   for settings in the UI. This enables the colored-dot indicators in the
   settings list. Without this, the plugin would need to overlay its own UI
   on top of the settings list (doable but hacky).

   ```python
   # In ContainerManager or MachineManager:
   def getSettingOrigin(self, key: str) -> Optional[Dict[str, str]]:
       """Returns metadata about what set this value.
       Plugins can register origin providers."""
       for provider in self._setting_origin_providers:
           origin = provider.getOrigin(key)
           if origin:
               return origin
       return None
   ```

2. **QualityChanges metadata extensibility** (already works)
   The existing `setMetaDataEntry` on InstanceContainer already allows
   arbitrary metadata, so no change needed for storing mixin references.

### 10. Import/Export & Sharing

- **Export**: Mixin → `.cura_mixin` file (just a renamed `.inst.cfg`)
- **Import**: File picker or drag-and-drop onto Cura
- **Bulk export**: Export all mixins as a `.zip` bundle
- **Profile export with mixins**: When exporting a profile that uses mixins,
  offer to bundle the referenced mixins alongside the profile

### 11. Implementation Phases

**Phase 1 - Core + Minimal UI** (MVP):
- MixinManager: create, edit, delete, store mixin containers
- Composition engine: merge mixins into QualityChanges
- Basic sidebar panel with add/remove/reorder
- Mixin editor dialog

**Phase 2 - Rich UI Integration**:
- Conflict visualization dialog
- Setting origin indicators (colored dots) in settings list
- Right-click "Add to mixin" in settings panel
- Tooltips showing mixin origin on hover

**Phase 3 - Sharing & Polish**:
- Import/export with `.cura_mixin` files
- Profile bundling with mixins
- Community mixin browser (future)
- Mixin templates (common starting points)

---

## Alternative Approaches Considered

### Alternative A: "Virtual Container Stack" (Rejected)

Maintain a parallel virtual stack that computes values and injects them into
UserChanges at the last moment.

**Why rejected**: Fighting the architecture. UserChanges is meant for manual
user tweaks. Overwriting it breaks the mental model and "has user settings"
indicators.

### Alternative B: "Extend the Container Stack" (Rejected)

Modify `CuraContainerStack` to support dynamic insertion of mixin containers
between QualityChanges and Intent.

**Why rejected**: Violates the fixed-stack design. Would break assumptions
across the entire codebase - settings resolution, serialization, UI models
all assume exactly 8 containers at fixed indices. Extremely high risk.

### Alternative C: "Pre-composition at Profile Level" (Simpler Alternative)

Instead of live composition, mixins are purely a *profile creation tool*.
You compose mixins when creating/editing a profile, and the result is a
standard flat QualityChanges profile.

**Tradeoffs**: Simpler to implement, but loses the "live" composability.
Changing a mixin wouldn't auto-update profiles that use it. Could be a
reasonable Phase 0 if the full approach is too complex initially.

---

## Summary

| Aspect | Design Decision |
|---|---|
| Mixin storage | JSON files in `setting_mixins/` directory |
| Includes storage | Metadata on QualityChanges container (`setting_mixin_includes`) |
| Application target | UserChanges container (index 0), tracking applied keys |
| Conflict resolution | Ordered list, later items win (CSS cascade) |
| Scope | Both global and per-extruder |
| UI location | Extensions menu → Settings Mixins (UM.Dialog window) |
| Core changes | None required — pure plugin |
| Degradation | Metadata ignored without plugin; UserChanges values persist |
| File format | `.json` / `.cura_mixin` for export |
