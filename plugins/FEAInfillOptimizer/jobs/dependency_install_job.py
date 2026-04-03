# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

import subprocess

from UM.Job import Job
from UM.Logger import Logger
from UM.Message import Message
from UM.i18n import i18nCatalog

from ..deps.dependency_manager import DependencyManager

i18n_catalog = i18nCatalog("cura")


class DependencyInstallJob(Job):
    """Background job that pip-installs missing FEA dependencies."""

    def __init__(self, dep_manager: DependencyManager) -> None:
        super().__init__()
        self._dep_manager = dep_manager

    def run(self) -> None:
        cmd = self._dep_manager.get_install_command()
        if not cmd:
            Logger.log("i", "FEA Infill: No missing dependencies.")
            return

        missing = self._dep_manager.missing_packages()
        msg = Message(
            i18n_catalog.i18nc("@info:status",
                               "Installing FEA dependencies: {packages}...").format(
                packages=", ".join(missing)),
            lifetime=0,
            dismissable=False,
            progress=0,
            title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer")
        )
        msg.show()

        try:
            msg.setProgress(10)
            Logger.log("i", "FEA Infill: Running pip install: %s", " ".join(cmd))

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes max
            )

            if result.returncode != 0:
                Logger.log("e", "FEA Infill: pip install failed:\n%s", result.stderr)
                msg.hide()
                error_msg = Message(
                    i18n_catalog.i18nc("@info:status",
                                       "Failed to install FEA dependencies. "
                                       "Check the log for details."),
                    title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                    message_type=Message.MessageType.ERROR
                )
                error_msg.show()
                return

            msg.setProgress(90)
            self._dep_manager.post_install()
            msg.setProgress(100)
            msg.hide()

            success_msg = Message(
                i18n_catalog.i18nc("@info:status",
                                   "FEA dependencies installed successfully."),
                title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                lifetime=5
            )
            success_msg.show()

        except subprocess.TimeoutExpired:
            Logger.log("e", "FEA Infill: pip install timed out after 600 seconds")
            msg.hide()
            error_msg = Message(
                i18n_catalog.i18nc("@info:status",
                                   "Dependency installation timed out."),
                title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                message_type=Message.MessageType.ERROR
            )
            error_msg.show()
        except Exception as e:
            Logger.log("e", "FEA Infill: pip install error: %s", str(e))
            msg.hide()
            error_msg = Message(
                i18n_catalog.i18nc("@info:status",
                                   "Dependency installation failed: {error}").format(
                    error=str(e)),
                title=i18n_catalog.i18nc("@info:title", "FEA Infill Optimizer"),
                message_type=Message.MessageType.ERROR
            )
            error_msg.show()
