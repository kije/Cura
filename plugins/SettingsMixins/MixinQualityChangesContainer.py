# Copyright (c) 2024 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

from typing import Any, Dict, Iterable, List, Optional, Set

from UM.Logger import Logger
from UM.Settings.InstanceContainer import InstanceContainer
from UM.Signal import Signal


class MixinQualityChangesContainer(InstanceContainer):
    """A virtual container that overlays mixin values on top of a QualityChanges container.

    This container sits at index 1 (QualityChanges) in the container stack. It wraps the real
    QualityChanges container and applies mixin overlays on top. Property resolution order:

        1. Mixins (last in list = highest priority)
        2. Wrapped QualityChanges (original custom profile values)

    This container is ephemeral — mixin values are never serialized. Only the wrapped
    QualityChanges container is persisted. The list of active mixin IDs is stored as
    metadata ("setting_mixin_includes") on the wrapped QualityChanges container.
    """

    mixinsChanged = Signal()

    def __init__(self, wrapped_quality_changes: InstanceContainer) -> None:
        # Use a synthetic ID; we override getId() to delegate to wrapped container
        super().__init__("__mixin_wrapper__")
        self._wrapped_quality_changes = wrapped_quality_changes
        self._mixin_settings: List[Dict[str, Any]] = []  # Each dict is {setting_key: value}
        self._mixin_ids: List[str] = []  # Parallel list of mixin IDs for tracking

        # Forward property changes from the wrapped container through this wrapper
        self._wrapped_quality_changes.propertyChanged.connect(self._onWrappedPropertyChanged)
        self._wrapped_quality_changes.metaDataChanged.connect(self.metaDataChanged)

    @property
    def wrappedQualityChanges(self) -> InstanceContainer:
        """Access the underlying QualityChanges container directly."""
        return self._wrapped_quality_changes

    def setWrappedQualityChanges(self, new_qc: InstanceContainer) -> None:
        """Replace the wrapped QualityChanges container (e.g. when switching profiles)."""
        old_qc = self._wrapped_quality_changes
        if old_qc is new_qc:
            return

        old_qc.propertyChanged.disconnect(self._onWrappedPropertyChanged)
        old_qc.metaDataChanged.disconnect(self.metaDataChanged)

        self._wrapped_quality_changes = new_qc

        new_qc.propertyChanged.connect(self._onWrappedPropertyChanged)
        new_qc.metaDataChanged.connect(self.metaDataChanged)

        # Emit changes for all keys that could be affected
        self._emitPropertyChangedForAllKeys()

    # --- Mixin management ---

    def setMixinOverlays(self, mixin_ids: List[str], mixin_settings: List[Dict[str, Any]]) -> None:
        """Replace the full mixin overlay list.

        :param mixin_ids: Ordered list of mixin IDs (for tracking/identification).
        :param mixin_settings: Parallel list of setting dicts {key: value} per mixin.
                               Later entries have higher priority.
        """
        old_keys = self._getAllMixinKeys()

        self._mixin_ids = list(mixin_ids)
        self._mixin_settings = list(mixin_settings)

        new_keys = self._getAllMixinKeys()
        affected_keys = old_keys | new_keys
        for key in affected_keys:
            self.propertyChanged.emit(key, "value")

        self.mixinsChanged.emit()

    def clearMixinOverlays(self) -> None:
        """Remove all mixin overlays."""
        if not self._mixin_settings:
            return
        affected_keys = self._getAllMixinKeys()
        self._mixin_ids = []
        self._mixin_settings = []
        for key in affected_keys:
            self.propertyChanged.emit(key, "value")
        self.mixinsChanged.emit()

    def hasMixins(self) -> bool:
        return len(self._mixin_settings) > 0

    # --- ContainerInterface: property resolution ---

    def getProperty(self, key: str, property_name: str, context=None) -> Any:
        """Resolve a property: check mixin overlays first (last wins), then wrapped QualityChanges."""
        # Check mixins in reverse order (last in list = highest priority)
        for settings in reversed(self._mixin_settings):
            if key in settings and property_name == "value":
                return settings[key]
        # Fall through to wrapped QualityChanges
        return self._wrapped_quality_changes.getProperty(key, property_name, context)

    def hasProperty(self, key: str, property_name: str) -> bool:
        if property_name == "value":
            for settings in self._mixin_settings:
                if key in settings:
                    return True
        return self._wrapped_quality_changes.hasProperty(key, property_name)

    def getAllKeys(self) -> Iterable[str]:
        keys: Set[str] = set(self._wrapped_quality_changes.getAllKeys())
        for settings in self._mixin_settings:
            keys |= set(settings.keys())
        return keys

    # --- ContainerInterface: write operations delegate to wrapped container ---

    def setProperty(self, key: str, property_name: str, property_value: Any,
                    container=None, set_from_cache: bool = False) -> None:
        """Write operations go to the wrapped QualityChanges, never to mixin overlays."""
        self._wrapped_quality_changes.setProperty(key, property_name, property_value, container, set_from_cache)

    def removeInstance(self, key: str, postpone_emit: bool = False) -> None:
        """Remove an instance from the wrapped QualityChanges only."""
        self._wrapped_quality_changes.removeInstance(key, postpone_emit)

    def clear(self) -> None:
        """Clear only the wrapped QualityChanges, NOT the mixin overlays."""
        self._wrapped_quality_changes.clear()

    # --- ContainerInterface: identity & metadata delegate to wrapped container ---

    def getId(self) -> str:
        return self._wrapped_quality_changes.getId()

    def getName(self) -> str:
        return self._wrapped_quality_changes.getName()

    def setName(self, name: str) -> None:
        self._wrapped_quality_changes.setName(name)

    def isReadOnly(self) -> bool:
        return self._wrapped_quality_changes.isReadOnly()

    def getMetaDataEntry(self, entry: str, default=None) -> Any:
        return self._wrapped_quality_changes.getMetaDataEntry(entry, default)

    def setMetaDataEntry(self, key: str, value: Any) -> None:
        self._wrapped_quality_changes.setMetaDataEntry(key, value)

    def getMetaData(self) -> Dict[str, Any]:
        return self._wrapped_quality_changes.getMetaData()

    def getPath(self) -> str:
        return self._wrapped_quality_changes.getPath()

    def setPath(self, path: str) -> None:
        self._wrapped_quality_changes.setPath(path)

    def setDefinition(self, definition_id: str) -> None:
        self._wrapped_quality_changes.setDefinition(definition_id)

    def getDefinition(self):
        return self._wrapped_quality_changes.getDefinition()

    # --- Serialization: delegate to wrapped container (mixins are ephemeral) ---

    def serialize(self, ignored_metadata_keys=None) -> str:
        return self._wrapped_quality_changes.serialize(ignored_metadata_keys)

    def deserialize(self, serialized: str, file_name: Optional[str] = None) -> str:
        return self._wrapped_quality_changes.deserialize(serialized, file_name)

    # --- Signal forwarding ---

    def _onWrappedPropertyChanged(self, key: str, property_name: str) -> None:
        """Forward property changes from the wrapped container."""
        self.propertyChanged.emit(key, property_name)

    # --- Internal helpers ---

    def _getAllMixinKeys(self) -> Set[str]:
        keys: Set[str] = set()
        for settings in self._mixin_settings:
            keys |= set(settings.keys())
        return keys

    def _emitPropertyChangedForAllKeys(self) -> None:
        for key in self.getAllKeys():
            self.propertyChanged.emit(key, "value")

    def isMixinKey(self, key: str) -> bool:
        """Check if a key's value comes from a mixin overlay (not the wrapped QC)."""
        for settings in self._mixin_settings:
            if key in settings:
                return True
        return False
