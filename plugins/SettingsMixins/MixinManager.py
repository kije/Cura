import json
import os
import shutil
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from UM.Logger import Logger
from UM.Resources import Resources


class MixinDefinition:
    """A reusable bundle of print settings."""

    def __init__(self, mixin_id: str = "", name: str = "", description: str = "",
                 scope: str = "global", color: str = "#808080",
                 tags: Optional[List[str]] = None,
                 settings: Optional[Dict[str, Any]] = None) -> None:
        self.id = mixin_id or str(uuid.uuid4()).replace("-", "")[:12]
        self.name = name
        self.description = description
        self.scope = scope  # "global" or "extruder"
        self.color = color
        self.tags = tags or []
        self.settings = settings or {}  # {setting_key: value}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "scope": self.scope,
            "color": self.color,
            "tags": self.tags,
            "settings": self.settings,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MixinDefinition":
        return cls(
            mixin_id=data.get("id", ""),
            name=data.get("name", "Unnamed Mixin"),
            description=data.get("description", ""),
            scope=data.get("scope", "global"),
            color=data.get("color", "#808080"),
            tags=data.get("tags", []),
            settings=data.get("settings", {}),
        )

    def setting_count(self) -> int:
        return len(self.settings)


class MixinManager:
    """Manages mixin definitions and their application to the container stack.

    Mixins are stored as JSON files in the user data directory.
    Active mixin configurations (which mixins are enabled, their order) are
    stored in Cura preferences keyed by machine ID.

    Mixin values are applied to the UserChanges container (index 0) in the
    container stack. The manager tracks which keys were set by mixins vs.
    manually by the user, so that user overrides are preserved on re-apply.
    """

    def __init__(self) -> None:
        self._mixins: Dict[str, MixinDefinition] = {}  # id -> definition
        self._storage_path = ""

        # Per-machine active mixin state
        # Stored in preferences as JSON: {machine_id: {"global": [mixin_ids], "extruder_0": [ids], ...}}
        self._active_mixins: Dict[str, Dict[str, List[str]]] = {}

        # Track what keys we applied and what values we set, per stack ID
        # {stack_id: {setting_key: value_we_set}}
        self._applied_values: Dict[str, Dict[str, Any]] = {}

    @property
    def storage_path(self) -> str:
        if not self._storage_path:
            self._storage_path = os.path.join(
                Resources.getDataStoragePath(), "setting_mixins"
            )
            os.makedirs(self._storage_path, exist_ok=True)
        return self._storage_path

    # ── CRUD Operations ────────────────────────────────────────────────

    def load_all_mixins(self) -> None:
        """Load all mixin definitions from the storage directory."""
        self._mixins.clear()
        if not os.path.isdir(self.storage_path):
            return

        for filename in os.listdir(self.storage_path):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(self.storage_path, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                mixin = MixinDefinition.from_dict(data)
                self._mixins[mixin.id] = mixin
            except (json.JSONDecodeError, KeyError, OSError) as e:
                Logger.log("w", "Failed to load mixin from %s: %s", filepath, str(e))

        Logger.log("i", "Loaded %d setting mixins", len(self._mixins))

    def save_mixin(self, mixin: MixinDefinition) -> None:
        """Save a mixin definition to disk."""
        filepath = os.path.join(self.storage_path, f"{mixin.id}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(mixin.to_dict(), f, indent=2, ensure_ascii=False)
        except OSError as e:
            Logger.log("e", "Failed to save mixin %s: %s", mixin.id, str(e))

    def create_mixin(self, name: str, description: str = "", scope: str = "global",
                     color: str = "#808080", tags: Optional[List[str]] = None,
                     settings: Optional[Dict[str, Any]] = None) -> MixinDefinition:
        """Create a new mixin definition and save it."""
        mixin = MixinDefinition(
            name=name, description=description, scope=scope,
            color=color, tags=tags, settings=settings,
        )
        self._mixins[mixin.id] = mixin
        self.save_mixin(mixin)
        return mixin

    def update_mixin(self, mixin_id: str, name: Optional[str] = None,
                     description: Optional[str] = None, scope: Optional[str] = None,
                     color: Optional[str] = None, tags: Optional[List[str]] = None,
                     settings: Optional[Dict[str, Any]] = None) -> Optional[MixinDefinition]:
        """Update an existing mixin definition."""
        mixin = self._mixins.get(mixin_id)
        if mixin is None:
            return None

        if name is not None:
            mixin.name = name
        if description is not None:
            mixin.description = description
        if scope is not None:
            mixin.scope = scope
        if color is not None:
            mixin.color = color
        if tags is not None:
            mixin.tags = tags
        if settings is not None:
            mixin.settings = settings

        self.save_mixin(mixin)
        return mixin

    def delete_mixin(self, mixin_id: str) -> bool:
        """Delete a mixin definition from disk and memory."""
        if mixin_id not in self._mixins:
            return False

        filepath = os.path.join(self.storage_path, f"{mixin_id}.json")
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except OSError as e:
            Logger.log("e", "Failed to delete mixin file %s: %s", filepath, str(e))

        del self._mixins[mixin_id]

        # Remove from all active configurations
        for machine_id, scopes in self._active_mixins.items():
            for scope_key, mixin_ids in scopes.items():
                if mixin_id in mixin_ids:
                    mixin_ids.remove(mixin_id)

        return True

    def get_mixin(self, mixin_id: str) -> Optional[MixinDefinition]:
        return self._mixins.get(mixin_id)

    def get_all_mixins(self) -> List[MixinDefinition]:
        return sorted(self._mixins.values(), key=lambda m: m.name.lower())

    # ── Active Mixin Management ────────────────────────────────────────

    def get_active_mixin_ids(self, machine_id: str, scope_key: str = "global") -> List[str]:
        """Get the ordered list of active mixin IDs for a machine/scope."""
        return list(self._active_mixins.get(machine_id, {}).get(scope_key, []))

    def get_active_mixins(self, machine_id: str, scope_key: str = "global") -> List[MixinDefinition]:
        """Get the ordered list of active MixinDefinitions for a machine/scope."""
        ids = self.get_active_mixin_ids(machine_id, scope_key)
        result = []
        for mixin_id in ids:
            mixin = self._mixins.get(mixin_id)
            if mixin:
                result.append(mixin)
        return result

    def set_active_mixin_ids(self, machine_id: str, scope_key: str,
                             mixin_ids: List[str]) -> None:
        """Set the ordered list of active mixin IDs for a machine/scope."""
        if machine_id not in self._active_mixins:
            self._active_mixins[machine_id] = {}
        self._active_mixins[machine_id][scope_key] = list(mixin_ids)

    def add_active_mixin(self, machine_id: str, scope_key: str, mixin_id: str) -> None:
        """Add a mixin to the end (highest priority) of the active list."""
        ids = self.get_active_mixin_ids(machine_id, scope_key)
        if mixin_id not in ids:
            ids.append(mixin_id)
            self.set_active_mixin_ids(machine_id, scope_key, ids)

    def remove_active_mixin(self, machine_id: str, scope_key: str, mixin_id: str) -> None:
        """Remove a mixin from the active list."""
        ids = self.get_active_mixin_ids(machine_id, scope_key)
        if mixin_id in ids:
            ids.remove(mixin_id)
            self.set_active_mixin_ids(machine_id, scope_key, ids)

    def move_active_mixin(self, machine_id: str, scope_key: str,
                          old_index: int, new_index: int) -> None:
        """Move a mixin within the active list (reorder)."""
        ids = self.get_active_mixin_ids(machine_id, scope_key)
        if 0 <= old_index < len(ids) and 0 <= new_index < len(ids):
            mixin_id = ids.pop(old_index)
            ids.insert(new_index, mixin_id)
            self.set_active_mixin_ids(machine_id, scope_key, ids)

    # ── Composition ────────────────────────────────────────────────────

    def compute_merged_values(self, machine_id: str,
                              scope_key: str = "global") -> Dict[str, Any]:
        """Compute the merged setting values from all active mixins.

        Later mixins in the list override earlier ones.
        Returns {setting_key: final_value}.
        """
        merged: Dict[str, Any] = {}
        for mixin in self.get_active_mixins(machine_id, scope_key):
            for key, value in mixin.settings.items():
                merged[key] = value
        return merged

    def compute_conflicts(self, machine_id: str,
                          scope_key: str = "global") -> List[Dict[str, Any]]:
        """Find settings that are set by multiple active mixins.

        Returns a list of conflict dicts:
        [{"key": str, "sources": [{"mixin_id": str, "mixin_name": str,
                                    "value": Any, "is_active": bool}]}]
        """
        # Collect all sources per key
        key_sources: Dict[str, List[Dict[str, Any]]] = {}
        active_mixins = self.get_active_mixins(machine_id, scope_key)

        for i, mixin in enumerate(active_mixins):
            for key, value in mixin.settings.items():
                if key not in key_sources:
                    key_sources[key] = []
                key_sources[key].append({
                    "mixin_id": mixin.id,
                    "mixin_name": mixin.name,
                    "mixin_color": mixin.color,
                    "value": value,
                    "index": i,
                })

        # Filter to only conflicting keys (multiple sources with different values)
        conflicts = []
        for key, sources in key_sources.items():
            if len(sources) < 2:
                continue
            unique_values = set()
            for s in sources:
                unique_values.add(str(s["value"]))
            if len(unique_values) < 2:
                continue  # Same value from multiple mixins is not a real conflict

            # Mark which source is active (last one wins)
            for s in sources:
                s["is_active"] = False
            sources[-1]["is_active"] = True

            conflicts.append({"key": key, "sources": sources})

        return conflicts

    def apply_to_stack(self, stack: Any, machine_id: str,
                       scope_key: str = "global") -> Set[str]:
        """Apply active mixins to a container stack's UserChanges.

        Returns the set of keys that were applied.
        """
        stack_id = stack.getId()
        user_changes = stack.userChanges

        # Determine which keys were previously applied by us
        prev_applied = self._applied_values.get(stack_id, {})

        # Remove previously-applied mixin keys (only if user hasn't overridden)
        for key, our_value in prev_applied.items():
            current_value = user_changes.getProperty(key, "value")
            # If the current value matches what we set, remove it (it's mixin-managed)
            # If it differs, the user manually changed it — leave it
            if current_value is not None and self._values_equal(current_value, our_value):
                try:
                    user_changes.removeInstance(key)
                except Exception:
                    pass  # Key may already be removed

        # Compute new merged values
        merged = self.compute_merged_values(machine_id, scope_key)

        # Apply merged values, skipping user-overridden keys
        new_applied: Dict[str, Any] = {}
        for key, value in merged.items():
            # Check if user has a manual override (a value in UserChanges that
            # we didn't set, or that they changed after we set it)
            current_value = user_changes.getProperty(key, "value")
            was_ours = key in prev_applied
            user_overrode = (
                current_value is not None
                and was_ours
                and not self._values_equal(current_value, prev_applied[key])
            )

            if user_overrode:
                # User manually changed this — don't overwrite
                Logger.log("d", "Skipping mixin key %s (user override)", key)
                continue

            user_changes.setProperty(key, "value", value)
            new_applied[key] = value

        self._applied_values[stack_id] = new_applied
        return set(new_applied.keys())

    def clear_from_stack(self, stack: Any) -> None:
        """Remove all mixin-applied values from a stack's UserChanges."""
        stack_id = stack.getId()
        prev_applied = self._applied_values.get(stack_id, {})
        user_changes = stack.userChanges

        for key, our_value in prev_applied.items():
            current_value = user_changes.getProperty(key, "value")
            if current_value is not None and self._values_equal(current_value, our_value):
                try:
                    user_changes.removeInstance(key)
                except Exception:
                    pass

        self._applied_values.pop(stack_id, None)

    def get_setting_origin(self, machine_id: str, scope_key: str,
                           setting_key: str) -> Optional[Dict[str, str]]:
        """Determine which mixin (if any) provides a given setting's value.

        Returns {"mixin_id": str, "mixin_name": str, "mixin_color": str}
        or None if not from a mixin.
        """
        active_mixins = self.get_active_mixins(machine_id, scope_key)
        # Last mixin with this key wins
        for mixin in reversed(active_mixins):
            if setting_key in mixin.settings:
                return {
                    "mixin_id": mixin.id,
                    "mixin_name": mixin.name,
                    "mixin_color": mixin.color,
                }
        return None

    def get_user_overrides(self, stack: Any, machine_id: str,
                           scope_key: str = "global") -> List[str]:
        """Get list of setting keys where user has overridden a mixin value."""
        stack_id = stack.getId()
        applied = self._applied_values.get(stack_id, {})
        merged = self.compute_merged_values(machine_id, scope_key)
        user_changes = stack.userChanges

        overrides = []
        for key in merged:
            if key not in applied:
                # We tried to apply but it was already overridden
                current = user_changes.getProperty(key, "value")
                if current is not None:
                    overrides.append(key)
        return overrides

    # ── Persistence (preferences) ──────────────────────────────────────

    def save_active_config(self, preferences: Any) -> None:
        """Save active mixin configuration to Cura preferences."""
        data = json.dumps(self._active_mixins)
        preferences.setValue("settings_mixins/active_config", data)

    def load_active_config(self, preferences: Any) -> None:
        """Load active mixin configuration from Cura preferences."""
        raw = preferences.getValue("settings_mixins/active_config")
        if raw:
            try:
                self._active_mixins = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                self._active_mixins = {}

    # ── Import / Export ────────────────────────────────────────────────

    def export_mixin(self, mixin_id: str, target_path: str) -> bool:
        """Export a mixin to a .cura_mixin file."""
        mixin = self._mixins.get(mixin_id)
        if not mixin:
            return False
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(mixin.to_dict(), f, indent=2, ensure_ascii=False)
            return True
        except OSError as e:
            Logger.log("e", "Export failed: %s", str(e))
            return False

    def import_mixin(self, source_path: str) -> Optional[MixinDefinition]:
        """Import a mixin from a .cura_mixin or .json file."""
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Generate a new ID to avoid collisions
            data["id"] = str(uuid.uuid4()).replace("-", "")[:12]
            mixin = MixinDefinition.from_dict(data)
            self._mixins[mixin.id] = mixin
            self.save_mixin(mixin)
            return mixin
        except (json.JSONDecodeError, KeyError, OSError) as e:
            Logger.log("e", "Import failed: %s", str(e))
            return None

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _values_equal(a: Any, b: Any) -> bool:
        """Compare setting values, handling type coercion."""
        try:
            if type(a) != type(b):
                return str(a) == str(b)
            return a == b
        except (TypeError, ValueError):
            return str(a) == str(b)
