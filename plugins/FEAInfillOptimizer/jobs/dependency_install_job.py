# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

import subprocess

from UM.Job import Job
from UM.Logger import Logger

from ..deps.dependency_manager import DependencyManager


class DependencyInstallJob(Job):
    """Background job that pip-installs missing FEA dependencies.

    All UI feedback (Message objects) is handled by the caller on the main
    thread via the ``finished`` signal — never from ``run()`` itself.
    """

    def __init__(self, dep_manager: DependencyManager) -> None:
        super().__init__()
        self._dep_manager = dep_manager

    def run(self) -> None:
        cmd = self._dep_manager.get_install_command()
        if not cmd:
            Logger.log("i", "FEA Infill: No missing dependencies.")
            self.setResult("no_missing")
            return

        try:
            Logger.log("i", "FEA Infill: Running pip install: %s", " ".join(cmd))
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 minutes max
            )

            if result.returncode != 0:
                Logger.log("e", "FEA Infill: pip install failed:\n%s", result.stderr)
                self.setResult(Exception("pip install failed: " + result.stderr[:500]))
                return

            self._dep_manager.post_install()
            self.setResult("ok")

        except subprocess.TimeoutExpired:
            Logger.log("e", "FEA Infill: pip install timed out after 600 seconds")
            self.setResult(Exception("Dependency installation timed out."))
        except Exception as e:
            Logger.log("e", "FEA Infill: pip install error: %s", str(e))
            self.setResult(Exception(str(e)))
