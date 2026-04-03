# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

import importlib
import importlib.util
import os
import sys
from typing import Dict

from UM.Logger import Logger


REQUIRED_PACKAGES = {
    "trimesh": "trimesh",
    "gmsh": "gmsh",
    "scipy": "scipy",
}

# numpy is already available in Cura's Python environment


class DependencyManager:
    """Manages external Python dependencies for the FEA plugin.

    Installs packages to a plugin-local _vendor/ directory to avoid
    polluting Cura's Python environment.
    """

    def __init__(self, plugin_path: str) -> None:
        self._plugin_path = plugin_path
        self._vendor_dir = os.path.join(plugin_path, "_vendor")
        self._ensure_vendor_on_path()

    def _ensure_vendor_on_path(self) -> None:
        if os.path.isdir(self._vendor_dir) and self._vendor_dir not in sys.path:
            sys.path.insert(0, self._vendor_dir)

    def check_all(self) -> Dict[str, bool]:
        """Check which required packages are importable."""
        result = {}
        for display_name, import_name in REQUIRED_PACKAGES.items():
            result[display_name] = importlib.util.find_spec(import_name) is not None
        return result

    def all_available(self) -> bool:
        return all(self.check_all().values())

    def missing_packages(self) -> list:
        return [name for name, available in self.check_all().items() if not available]

    def get_vendor_dir(self) -> str:
        return self._vendor_dir

    def get_install_command(self) -> list:
        """Return the pip install command arguments."""
        missing = self.missing_packages()
        if not missing:
            return []
        return [
            sys.executable, "-m", "pip", "install",
            "--target", self._vendor_dir,
            "--upgrade",
        ] + missing

    def post_install(self) -> None:
        """Called after installation to refresh imports."""
        self._ensure_vendor_on_path()
        # Clear cached module lookups so newly installed packages are found
        importlib.invalidate_caches()
        Logger.log("i", "FEA Infill: Dependencies installed to %s", self._vendor_dir)
