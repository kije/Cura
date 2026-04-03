# Copyright (c) 2024 Community Contributors
# Cura is released under the terms of the LGPLv3 or higher.

"""macOS-specific integration for the SceneAutoSave plugin.

Uses PyObjC to integrate with macOS system features:
- Sudden Termination: prevents macOS from killing Cura during logout/restart
  when there are unsaved scene changes.
- App Nap Prevention: ensures autosave timers fire reliably even when Cura
  is in the background.

This module is only imported on macOS and gracefully degrades if PyObjC
is not available.
"""

from typing import Any, Optional

from UM.Logger import Logger

try:
    from Foundation import NSProcessInfo
    _PYOBJC_AVAILABLE = True
except ImportError:
    _PYOBJC_AVAILABLE = False


# NSActivityUserInitiated = 0x00000001
# NSActivityLatencyCritical = 0x00000002  (not needed)
# We combine NSActivityUserInitiated with NSActivityIdleSystemSleepDisabled
# to prevent App Nap during saves.
_NS_ACTIVITY_USER_INITIATED = 0x00000001
_NS_ACTIVITY_IDLE_SYSTEM_SLEEP_DISABLED = 0x00100000


class MacOSAutoSaveHelper:
    """Provides macOS-specific system integration for the autosave plugin."""

    def __init__(self) -> None:
        if not _PYOBJC_AVAILABLE:
            raise ImportError("PyObjC (Foundation framework) is required for macOS integration")

        self._sudden_termination_disabled = False
        self._process_info = NSProcessInfo.processInfo()

        Logger.log("d", "MacOSAutoSaveHelper: initialized with PyObjC")

    def disableSuddenTermination(self) -> None:
        """Tell macOS not to suddenly terminate the app (e.g., during logout).

        Call this when the scene has unsaved changes. Safe to call multiple times;
        only the first call takes effect until enableSuddenTermination() is called.
        """
        if self._sudden_termination_disabled:
            return

        try:
            self._process_info.disableSuddenTermination()
            self._sudden_termination_disabled = True
            Logger.log("d", "MacOSAutoSaveHelper: sudden termination disabled")
        except Exception:
            Logger.logException("w", "MacOSAutoSaveHelper: failed to disable sudden termination")

    def enableSuddenTermination(self) -> None:
        """Tell macOS that sudden termination is OK again.

        Call this after a successful autosave when the scene state has been persisted.
        """
        if not self._sudden_termination_disabled:
            return

        try:
            self._process_info.enableSuddenTermination()
            self._sudden_termination_disabled = False
            Logger.log("d", "MacOSAutoSaveHelper: sudden termination enabled")
        except Exception:
            Logger.logException("w", "MacOSAutoSaveHelper: failed to enable sudden termination")

    def beginSaveActivity(self) -> Optional[Any]:
        """Begin an NSProcessInfo activity to prevent App Nap during a save operation.

        Returns an activity token that must be passed to endSaveActivity() when done.
        """
        try:
            activity = self._process_info.beginActivityWithOptions_reason_(
                _NS_ACTIVITY_USER_INITIATED | _NS_ACTIVITY_IDLE_SYSTEM_SLEEP_DISABLED,
                "Autosaving Cura workspace"
            )
            return activity
        except Exception:
            Logger.logException("w", "MacOSAutoSaveHelper: failed to begin save activity")
            return None

    def endSaveActivity(self, activity_token: Any) -> None:
        """End a previously started save activity to allow App Nap again.

        :param activity_token: The token returned by beginSaveActivity().
        """
        if activity_token is None:
            return

        try:
            self._process_info.endActivity_(activity_token)
        except Exception:
            Logger.logException("w", "MacOSAutoSaveHelper: failed to end save activity")
