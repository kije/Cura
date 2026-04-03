# Copyright (c) 2024 Community Contributors
# Cura is released under the terms of the LGPLv3 or higher.

import json
import os
import time
from datetime import datetime
from typing import Any, Optional

from PyQt6.QtCore import QUrl

from UM.Logger import Logger
from UM.Message import Message
from UM.i18n import i18nCatalog

catalog = i18nCatalog("cura")


class RecoveryManager:
    """Detects unclean shutdowns and offers to restore the last auto-saved workspace."""

    LOCK_FILENAME = "autosave.lock"
    AUTOSAVE_FILENAME = "autosave.3mf"
    META_FILENAME = "autosave.meta"

    def __init__(self, application: Any, recovery_dir: str) -> None:
        self._application = application
        self._recovery_dir = recovery_dir
        self._recovery_message = None  # type: Optional[Message]

    def _getLockFilePath(self) -> str:
        return os.path.join(self._recovery_dir, self.LOCK_FILENAME)

    def _getRecoveryFilePath(self) -> str:
        return os.path.join(self._recovery_dir, self.AUTOSAVE_FILENAME)

    def _getMetaFilePath(self) -> str:
        return os.path.join(self._recovery_dir, self.META_FILENAME)

    def checkAndOfferRecovery(self) -> None:
        """Check if the previous session ended uncleanly and offer to restore it."""
        lock_path = self._getLockFilePath()
        recovery_path = self._getRecoveryFilePath()

        if not os.path.exists(lock_path):
            # Clean shutdown last time; nothing to recover
            return

        if not os.path.exists(recovery_path):
            # Lock file exists but no recovery data; just clean up the stale lock
            self._removeLockFile()
            return

        # Unclean shutdown detected with recovery data available
        Logger.log("i", "SceneAutoSave: unclean shutdown detected, recovery file available")

        # Read metadata for a more informative message
        meta = self._readMetadata()
        message_text = self._buildRecoveryMessage(meta)

        self._recovery_message = Message(
            message_text,
            title=catalog.i18nc("@info:title", "Session Recovery"),
            lifetime=0,  # Don't auto-dismiss
            message_type=Message.MessageType.WARNING,
        )

        self._recovery_message.addAction(
            "restore",
            name=catalog.i18nc("@action:button", "Restore"),
            icon="",
            description=catalog.i18nc("@action:description", "Restore the auto-saved session"),
            button_align=Message.ActionButtonAlignment.ALIGN_RIGHT,
        )

        self._recovery_message.addAction(
            "discard",
            name=catalog.i18nc("@action:button", "Discard"),
            icon="",
            description=catalog.i18nc("@action:description", "Discard the auto-saved session"),
        )

        self._recovery_message.actionTriggered.connect(self._onRecoveryActionTriggered)
        self._recovery_message.show()

    def _buildRecoveryMessage(self, meta: Optional[dict]) -> str:
        """Build a human-readable recovery message from metadata."""
        if meta and "timestamp" in meta:
            save_time = datetime.fromtimestamp(meta["timestamp"])
            time_str = save_time.strftime("%Y-%m-%d %H:%M:%S")
            machine_name = meta.get("machine_name", "Unknown")
            return catalog.i18nc(
                "@info:status",
                "Cura did not shut down cleanly. An auto-saved session from {time} "
                "(printer: {machine}) is available. Would you like to restore it?"
            ).format(time=time_str, machine=machine_name)
        else:
            return catalog.i18nc(
                "@info:status",
                "Cura did not shut down cleanly. An auto-saved session is available. "
                "Would you like to restore it?"
            )

    def _readMetadata(self) -> Optional[dict]:
        """Read the autosave metadata file."""
        meta_path = self._getMetaFilePath()
        if not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path, "r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            Logger.log("w", "SceneAutoSave: could not read metadata file")
            return None

    def _onRecoveryActionTriggered(self, message: Message, action: str) -> None:
        """Handle the user's response to the recovery prompt."""
        message.hide()

        if action == "restore":
            self._restoreSession()
        elif action == "discard":
            Logger.log("i", "SceneAutoSave: user discarded recovery session")

        # Clean up recovery files and stale lock
        self._cleanupAllRecoveryFiles()

    def _restoreSession(self) -> None:
        """Restore the auto-saved workspace."""
        recovery_path = self._getRecoveryFilePath()

        if not os.path.exists(recovery_path):
            Logger.log("w", "SceneAutoSave: recovery file disappeared before restore")
            return

        Logger.log("i", "SceneAutoSave: restoring session from %s", recovery_path)

        try:
            self._application.readLocalFile(
                QUrl.fromLocalFile(recovery_path), add_to_recent_files=False
            )
        except Exception:
            Logger.logException("e", "SceneAutoSave: failed to restore session")

    def _removeLockFile(self) -> None:
        """Remove the lock file."""
        lock_path = self._getLockFilePath()
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except OSError:
                pass

    def _cleanupAllRecoveryFiles(self) -> None:
        """Remove all recovery-related files."""
        for filename in [self.LOCK_FILENAME, self.AUTOSAVE_FILENAME, self.META_FILENAME]:
            filepath = os.path.join(self._recovery_dir, filename)
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass
