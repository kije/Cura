# Copyright (c) 2024 Community Contributors
# Cura is released under the terms of the LGPLv3 or higher.

import os
import json
import time
from typing import Any, Optional

from PyQt6.QtCore import QObject, QTimer

from UM.Extension import Extension
from UM.Logger import Logger
from UM.Resources import Resources
from UM.Workspace.WorkspaceWriter import WorkspaceWriter

from cura.CuraApplication import CuraApplication

from .RecoveryManager import RecoveryManager


class SceneAutoSaveExtension(QObject, Extension):
    """Periodically auto-saves the full workspace to a recovery .3mf file.

    On startup after a crash, offers to restore the last auto-saved session.
    """

    RECOVERY_DIR_NAME = "recovery"
    AUTOSAVE_FILENAME = "autosave.3mf"
    AUTOSAVE_TMP_FILENAME = "autosave_tmp.3mf"
    LOCK_FILENAME = "autosave.lock"
    META_FILENAME = "autosave.meta"

    def __init__(self) -> None:
        QObject.__init__(self, None)
        Extension.__init__(self)

        self._application = CuraApplication.getInstance()
        self._global_stack = None  # type: Any
        self._saving = False
        self._scene_dirty = False
        self._last_save_time = 0.0
        self._initialized = False

        # Preferences
        preferences = self._application.getPreferences()
        preferences.addPreference("cura/scene_autosave_enabled", True)
        preferences.addPreference("cura/scene_autosave_interval", 1000 * 60 * 5)  # 5 minutes
        preferences.addPreference("cura/scene_autosave_debounce", 1000 * 3)       # 3 seconds
        preferences.addPreference("cura/scene_autosave_min_interval", 1000 * 30)   # 30 seconds

        # Debounce timer: resets on each change, fires after debounce delay
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(
            int(preferences.getValue("cura/scene_autosave_debounce"))
        )

        # Periodic timer: fires at the configured interval regardless of changes
        self._periodic_timer = QTimer()
        self._periodic_timer.setInterval(
            int(preferences.getValue("cura/scene_autosave_interval"))
        )

        # Recovery manager
        self._recovery_manager = RecoveryManager(self._application, self._getRecoveryDir())

        # macOS integration (optional)
        self._macos_helper = None  # type: Any
        self._init_macos_integration()

        # Connect after application is ready
        self._application.initializationFinished.connect(self._onApplicationInitialized)
        self._application.applicationShuttingDown.connect(self._onApplicationShuttingDown)

    def _init_macos_integration(self) -> None:
        """Initialize macOS-specific integration if available."""
        try:
            from UM.Platform import Platform
            if Platform.isOSX():
                from .macos_integration import MacOSAutoSaveHelper
                self._macos_helper = MacOSAutoSaveHelper()
                Logger.log("i", "SceneAutoSave: macOS integration enabled")
        except ImportError:
            Logger.log("d", "SceneAutoSave: macOS integration not available (PyObjC not installed)")
        except Exception:
            Logger.log("w", "SceneAutoSave: Failed to initialize macOS integration")

    def _getRecoveryDir(self) -> str:
        """Get the path to the recovery directory, creating it if needed."""
        recovery_dir = os.path.join(
            Resources.getDataStoragePath(), self.RECOVERY_DIR_NAME
        )
        os.makedirs(recovery_dir, exist_ok=True)
        return recovery_dir

    def _getRecoveryFilePath(self) -> str:
        return os.path.join(self._getRecoveryDir(), self.AUTOSAVE_FILENAME)

    def _getTempFilePath(self) -> str:
        return os.path.join(self._getRecoveryDir(), self.AUTOSAVE_TMP_FILENAME)

    def _getLockFilePath(self) -> str:
        return os.path.join(self._getRecoveryDir(), self.LOCK_FILENAME)

    def _getMetaFilePath(self) -> str:
        return os.path.join(self._getRecoveryDir(), self.META_FILENAME)

    # ---- Lifecycle ----

    def _onApplicationInitialized(self) -> None:
        """Called when Cura is fully initialized. Sets up signals and crash detection."""
        if self._initialized:
            return
        self._initialized = True

        # Check for crash recovery BEFORE creating a new lock file
        self._recovery_manager.checkAndOfferRecovery()

        # Create lock file to detect future crashes
        self._createLockFile()

        if not self._application.getPreferences().getValue("cura/scene_autosave_enabled"):
            Logger.log("i", "SceneAutoSave: disabled via preferences")
            return

        # Connect timers
        self._debounce_timer.timeout.connect(self._onDebounceTimeout)
        self._periodic_timer.timeout.connect(self._onPeriodicTimeout)

        # Connect scene signals
        scene = self._application.getController().getScene()
        scene.sceneChanged.connect(self._onSceneChanged)

        # Connect stack signals (same pattern as AutoSave)
        self._application.globalContainerStackChanged.connect(self._onGlobalStackChanged)
        self._onGlobalStackChanged()

        # Connect file load signals
        self._application.fileCompleted.connect(self._onSceneChanged)

        # Start periodic timer
        self._periodic_timer.start()

        Logger.log("i", "SceneAutoSave: initialized and monitoring scene changes")

    def _onApplicationShuttingDown(self) -> None:
        """Clean shutdown: remove lock file and stop timers."""
        self._debounce_timer.stop()
        self._periodic_timer.stop()

        # Remove lock file on clean shutdown
        lock_path = self._getLockFilePath()
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except OSError:
                Logger.log("w", "SceneAutoSave: could not remove lock file on shutdown")

        # Notify macOS that sudden termination is OK
        if self._macos_helper:
            self._macos_helper.enableSuddenTermination()

        Logger.log("d", "SceneAutoSave: clean shutdown, lock file removed")

    def _createLockFile(self) -> None:
        """Create a lock file that indicates Cura is running. Presence after a crash signals unclean shutdown."""
        lock_path = self._getLockFilePath()
        try:
            with open(lock_path, "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            Logger.log("w", "SceneAutoSave: could not create lock file")

    # ---- Change Detection ----

    def _onSceneChanged(self, *args: Any) -> None:
        """Called when the scene changes (model added/removed/transformed)."""
        self._scene_dirty = True

        # Notify macOS that we have unsaved changes
        if self._macos_helper:
            self._macos_helper.disableSuddenTermination()

        if not self._saving:
            self._debounce_timer.start()

    def _onGlobalStackChanged(self) -> None:
        """Called when the active machine changes. Re-wire stack signals."""
        if self._global_stack:
            try:
                self._global_stack.propertyChanged.disconnect(self._onSceneChanged)
                self._global_stack.containersChanged.disconnect(self._onSceneChanged)
            except (TypeError, RuntimeError):
                pass  # Signal was not connected

        self._global_stack = self._application.getGlobalContainerStack()

        if self._global_stack:
            self._global_stack.propertyChanged.connect(self._onSceneChanged)
            self._global_stack.containersChanged.connect(self._onSceneChanged)

    # ---- Timer Callbacks ----

    def _onDebounceTimeout(self) -> None:
        """Called after the debounce delay. Performs save if enough time has passed."""
        min_interval_ms = int(
            self._application.getPreferences().getValue("cura/scene_autosave_min_interval")
        )
        min_interval_s = min_interval_ms / 1000.0
        elapsed = time.time() - self._last_save_time

        if elapsed < min_interval_s:
            # Too soon since last save; re-schedule with remaining wait time
            remaining_ms = int((min_interval_s - elapsed) * 1000) + 100
            QTimer.singleShot(remaining_ms, self._performAutoSave)
            return

        self._performAutoSave()

    def _onPeriodicTimeout(self) -> None:
        """Called at the periodic interval. Only saves if the scene is dirty."""
        if self._scene_dirty:
            self._performAutoSave()

    # ---- Save Logic ----

    def _performAutoSave(self) -> None:
        """Save the full workspace to the recovery file."""
        if self._saving:
            return

        if not self._application.started:
            return

        # Check that there is actually something on the build plate
        if not self._application.platformActivity:
            # Scene is empty; clean up any existing recovery file
            self._cleanupRecoveryFiles()
            self._scene_dirty = False
            return

        # Need an active machine
        machine_manager = self._application.getMachineManager()
        if machine_manager.activeMachine is None:
            return

        self._saving = True
        save_start_time = time.time()

        # Prevent App Nap on macOS during save
        activity_token = None
        if self._macos_helper:
            activity_token = self._macos_helper.beginSaveActivity()

        try:
            self._writeWorkspaceToRecoveryFile()
            self._writeMetadata()
            self._scene_dirty = False
            self._last_save_time = time.time()

            elapsed = time.time() - save_start_time
            Logger.log("d", "SceneAutoSave: workspace saved in %.2f seconds", elapsed)

            # After successful save, macOS can allow sudden termination again
            if self._macos_helper:
                self._macos_helper.enableSuddenTermination()

        except Exception:
            Logger.logException("w", "SceneAutoSave: failed to save workspace")
        finally:
            self._saving = False
            if self._macos_helper and activity_token:
                self._macos_helper.endSaveActivity(activity_token)

    def _writeWorkspaceToRecoveryFile(self) -> None:
        """Write the full workspace to a temporary file, then atomically rename."""
        workspace_handler = self._application.getWorkspaceFileHandler()
        if workspace_handler is None:
            Logger.log("w", "SceneAutoSave: no workspace file handler available")
            return

        # Get the workspace writer (ThreeMFWorkspaceWriter) — same lookup as UCPDialog
        workspace_writer = workspace_handler.getWriter("3MFWriter")
        if workspace_writer is None:
            Logger.log("w", "SceneAutoSave: could not find workspace writer (3MFWriter)")
            return

        # Ensure UCP model is not set (we want a plain workspace, not UCP)
        if hasattr(workspace_writer, "setExportModel"):
            workspace_writer.setExportModel(None)

        nodes = [self._application.getController().getScene().getRoot()]
        temp_path = self._getTempFilePath()
        final_path = self._getRecoveryFilePath()

        with open(temp_path, "wb") as f:
            success = workspace_writer.write(f, nodes, WorkspaceWriter.OutputMode.BinaryMode)

        if success:
            os.replace(temp_path, final_path)
        else:
            Logger.log("w", "SceneAutoSave: workspace writer returned failure")
            # Clean up temp file
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _writeMetadata(self) -> None:
        """Write metadata alongside the recovery file for display in the recovery prompt."""
        meta_path = self._getMetaFilePath()
        machine_manager = self._application.getMachineManager()

        metadata = {
            "timestamp": time.time(),
            "cura_version": self._application.getVersion(),
            "machine_name": machine_manager.activeMachine.getName() if machine_manager.activeMachine else "Unknown",
            "pid": os.getpid(),
        }

        try:
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)
        except OSError:
            Logger.log("w", "SceneAutoSave: could not write metadata file")

    def _cleanupRecoveryFiles(self) -> None:
        """Remove recovery files (e.g., when scene becomes empty)."""
        for path in [self._getRecoveryFilePath(), self._getTempFilePath(), self._getMetaFilePath()]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
