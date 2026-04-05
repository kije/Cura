# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

import importlib
import importlib.util
import os
import sys
from typing import Dict, List

from UM.Logger import Logger


def _is_frozen() -> bool:
    """Return True when running inside a PyInstaller-frozen Cura bundle."""
    return getattr(sys, "frozen", False)


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
        """Add the _vendor/ directory (and its lib/ subdirectory) to paths.

        The vendor dir contains the Python API (gmsh.py) and lib/ contains
        the native shared library (libgmsh.*.dylib/.so/.dll).
        """
        if os.path.isdir(self._vendor_dir) and self._vendor_dir not in sys.path:
            sys.path.insert(0, self._vendor_dir)

        # Also add lib/ to the dynamic library search path for ctypes
        lib_dir = os.path.join(self._vendor_dir, "lib")
        if os.path.isdir(lib_dir):
            # On macOS, ctypes uses DYLD_LIBRARY_PATH or direct path
            # gmsh.py searches moduledir/lib/ which is _vendor/lib/ when
            # gmsh.py is in _vendor/ — this should work automatically.
            # But also add to PATH for Windows and LD_LIBRARY_PATH for Linux.
            if sys.platform == "win32":
                os.environ["PATH"] = lib_dir + os.pathsep + os.environ.get("PATH", "")
            elif sys.platform == "linux":
                os.environ["LD_LIBRARY_PATH"] = lib_dir + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")

    def check_all(self) -> Dict[str, bool]:
        """Check which required packages are importable.

        Uses actual import (not just find_spec) because PyInstaller-frozen
        builds may not have proper specs for bundled packages.
        Catches all exceptions (not just ImportError) since some packages
        may fail during import with RuntimeError or OSError.
        """
        result = {}
        for display_name, import_name in REQUIRED_PACKAGES.items():
            try:
                importlib.import_module(import_name)
                result[display_name] = True
            except Exception as e:
                Logger.log("w", "FEA Infill: Package '%s' not available: %s", import_name, str(e))
                result[display_name] = False
        return result

    def all_available(self) -> bool:
        return all(self.check_all().values())

    def missing_packages(self) -> list:
        return [name for name, available in self.check_all().items() if not available]

    def get_vendor_dir(self) -> str:
        return self._vendor_dir

    def get_install_command(self) -> List[str]:
        """Return the pip install command for missing packages.

        Dependencies should be pre-vendored using ``vendor_deps.sh``.
        This method is a fallback for non-frozen development environments.
        In frozen builds, it attempts to use the system pip3.
        """
        missing = self.missing_packages()
        if not missing:
            return []

        if _is_frozen():
            import shutil
            pip_cmd = shutil.which("pip3") or shutil.which("pip")
            if pip_cmd is None:
                Logger.log("e", "FEA Infill: Missing packages %s. "
                           "Run vendor_deps.sh from the plugin directory to bundle them.",
                           missing)
                return []
            return [pip_cmd, "install", "--target", self._vendor_dir,
                    "--upgrade", "--no-deps"] + missing

        return [
            sys.executable, "-m", "pip", "install",
            "--target", self._vendor_dir,
            "--upgrade", "--no-deps",
        ] + missing

    def post_install(self) -> None:
        """Called after installation to refresh imports."""
        self._ensure_vendor_on_path()
        # Clear cached module lookups so newly installed packages are found
        importlib.invalidate_caches()
        Logger.log("i", "FEA Infill: Dependencies installed to %s", self._vendor_dir)
