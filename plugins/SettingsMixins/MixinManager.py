import json
import os
import uuid
from typing import Any, Dict, List, Optional, Set

from UM.Logger import Logger
from UM.Resources import Resources


# Metadata key used on QualityChanges containers to store the ordered list
# of mixin IDs that this profile "includes".
INCLUDES_METADATA_KEY = "setting_mixin_includes"


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

    Mixin *definitions* are stored as JSON files in the user data directory.

    The list of mixins that a profile "includes" is stored as metadata on the
    profile's QualityChanges container:

        qualityChanges.getMetaDataEntry("setting_mixin_includes")
        → '["petg_general", "no_supports"]'   (JSON array of mixin IDs, ordered)

    This means the includes list travels WITH the profile — different profiles
    can include different mixins, and switching profiles automatically switches
    the active mixin set.

    Mixin values are applied to the UserChanges container (index 0).  The
    manager tracks which keys it wrote so that manual user overrides are
    preserved when re-applying.
    """

    def __init__(self) -> None:
        self._mixins: Dict[str, MixinDefinition] = {}  # id → definition
        self._storage_path = ""

        # Track what keys we applied and what values we set, per stack ID.
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

    # ── Mixin Definition CRUD ──────────────────────────────────────────

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
        return True

    def get_mixin(self, mixin_id: str) -> Optional[MixinDefinition]:
        return self._mixins.get(mixin_id)

    def get_all_mixins(self) -> List[MixinDefinition]:
        return sorted(self._mixins.values(), key=lambda m: m.name.lower())

    # ── Profile Includes (stored on QualityChanges container) ──────────

    @staticmethod
    def read_includes(quality_changes_container: Any) -> List[str]:
        """Read the ordered mixin-ID list from a QualityChanges container.

        Returns an empty list when the container is the empty singleton or
        has no includes metadata.
        """
        from cura.Settings.cura_empty_instance_containers import isEmptyContainer
        if isEmptyContainer(quality_changes_container.getId()):
            return []

        raw = quality_changes_container.getMetaDataEntry(INCLUDES_METADATA_KEY, "")
        if not raw:
            return []
        try:
            ids = json.loads(raw)
            if isinstance(ids, list):
                return [str(i) for i in ids]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    @staticmethod
    def write_includes(quality_changes_container: Any, mixin_ids: List[str]) -> None:
        """Write the ordered mixin-ID list to a QualityChanges container."""
        from cura.Settings.cura_empty_instance_containers import isEmptyContainer
        if isEmptyContainer(quality_changes_container.getId()):
            Logger.log("w", "Cannot write mixin includes to the empty quality_changes container")
            return

        quality_changes_container.setMetaDataEntry(
            INCLUDES_METADATA_KEY,
            json.dumps(mixin_ids),
        )

    def get_includes_for_container(self, quality_changes_container: Any) -> List[MixinDefinition]:
        """Resolve the includes list on a container to MixinDefinition objects."""
        ids = self.read_includes(quality_changes_container)
        result = []
        for mixin_id in ids:
            mixin = self._mixins.get(mixin_id)
            if mixin:
                result.append(mixin)
        return result

    def add_include(self, quality_changes_container: Any, mixin_id: str) -> None:
        """Append a mixin to the profile's includes list."""
        ids = self.read_includes(quality_changes_container)
        if mixin_id not in ids:
            ids.append(mixin_id)
            self.write_includes(quality_changes_container, ids)

    def remove_include(self, quality_changes_container: Any, mixin_id: str) -> None:
        """Remove a mixin from the profile's includes list."""
        ids = self.read_includes(quality_changes_container)
        if mixin_id in ids:
            ids.remove(mixin_id)
            self.write_includes(quality_changes_container, ids)

    def move_include(self, quality_changes_container: Any,
                     old_index: int, new_index: int) -> None:
        """Reorder a mixin within the profile's includes list."""
        ids = self.read_includes(quality_changes_container)
        if 0 <= old_index < len(ids) and 0 <= new_index < len(ids):
            mixin_id = ids.pop(old_index)
            ids.insert(new_index, mixin_id)
            self.write_includes(quality_changes_container, ids)

    def remove_mixin_from_all_profiles(self, mixin_id: str) -> None:
        """Remove a deleted mixin from every QualityChanges container that references it."""
        from UM.Settings.ContainerRegistry import ContainerRegistry
        registry = ContainerRegistry.getInstance()
        for container in registry.findContainers(type="quality_changes"):
            ids = self.read_includes(container)
            if mixin_id in ids:
                ids.remove(mixin_id)
                self.write_includes(container, ids)

    # ── Composition ────────────────────────────────────────────────────

    def compute_merged_values(self, quality_changes_container: Any) -> Dict[str, Any]:
        """Compute merged setting values from the profile's included mixins.

        Later mixins in the includes list override earlier ones.
        Returns {setting_key: final_value}.
        """
        merged: Dict[str, Any] = {}
        for mixin in self.get_includes_for_container(quality_changes_container):
            for key, value in mixin.settings.items():
                merged[key] = value
        return merged

    def compute_conflicts(self, quality_changes_container: Any) -> List[Dict[str, Any]]:
        """Find settings defined by multiple included mixins with different values.

        Returns:
            [{"key": str, "sources": [{"mixin_id", "mixin_name", "mixin_color",
                                        "value", "is_active": bool}]}]
        """
        key_sources: Dict[str, List[Dict[str, Any]]] = {}
        active_mixins = self.get_includes_for_container(quality_changes_container)

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

        conflicts = []
        for key, sources in key_sources.items():
            if len(sources) < 2:
                continue
            unique_values = {str(s["value"]) for s in sources}
            if len(unique_values) < 2:
                continue

            for s in sources:
                s["is_active"] = False
            sources[-1]["is_active"] = True
            conflicts.append({"key": key, "sources": sources})

        return conflicts

    def apply_to_stack(self, stack: Any, quality_changes_container: Any) -> Set[str]:
        """Apply the profile's included mixins to a stack's UserChanges.

        Returns the set of setting keys that were applied.
        """
        stack_id = stack.getId()
        user_changes = stack.userChanges

        prev_applied = self._applied_values.get(stack_id, {})

        # Remove previously-applied mixin keys (only if user hasn't overridden)
        for key, our_value in prev_applied.items():
            current_value = user_changes.getProperty(key, "value")
            if current_value is not None and self._values_equal(current_value, our_value):
                try:
                    user_changes.removeInstance(key)
                except Exception:
                    pass

        # Compute new merged values from the profile's includes
        merged = self.compute_merged_values(quality_changes_container)

        # Apply merged values, skipping user-overridden keys
        new_applied: Dict[str, Any] = {}
        for key, value in merged.items():
            current_value = user_changes.getProperty(key, "value")
            was_ours = key in prev_applied
            user_overrode = (
                current_value is not None
                and was_ours
                and not self._values_equal(current_value, prev_applied[key])
            )

            if user_overrode:
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

    def get_setting_origin(self, quality_changes_container: Any,
                           setting_key: str) -> Optional[Dict[str, str]]:
        """Determine which included mixin provides a given setting's value."""
        active_mixins = self.get_includes_for_container(quality_changes_container)
        for mixin in reversed(active_mixins):
            if setting_key in mixin.settings:
                return {
                    "mixin_id": mixin.id,
                    "mixin_name": mixin.name,
                    "mixin_color": mixin.color,
                }
        return None

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
