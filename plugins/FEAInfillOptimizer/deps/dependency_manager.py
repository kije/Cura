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
        """Add the _vendor/ directory to sys.path and set up library paths.

        Structure:
        - _vendor/gmsh.py — Python API
        - _vendor/lib/libgmsh.*.dylib — active native library (current platform)
        - _vendor/lib/<platform>/libgmsh.* — platform-specific libraries
        """
        if os.path.isdir(self._vendor_dir) and self._vendor_dir not in sys.path:
            sys.path.insert(0, self._vendor_dir)

        # gmsh.py searches moduledir/lib/ which maps to _vendor/lib/
        # For the current platform, the vendor script copies the right
        # native lib directly to _vendor/lib/.  For cross-platform builds,
        # we also detect and copy if needed.
        lib_dir = os.path.join(self._vendor_dir, "lib")
        if os.path.isdir(lib_dir):
            self._setup_platform_lib(lib_dir)

    def _setup_platform_lib(self, lib_dir: str) -> None:
        """Ensure the correct platform native library is in lib/."""
        import platform as plat
        import shutil

        machine = plat.machine()
        system = plat.system()
        if system == "Darwin" and machine == "arm64":
            current_plat = "macosx_12_0_arm64"
        elif system == "Darwin":
            current_plat = "macosx_10_15_x86_64"
        elif system == "Windows":
            current_plat = "win_amd64"
        else:
            return  # Linux — user must install gmsh system-wide

        # Check if the platform-specific lib exists but isn't in lib/ root
        plat_dir = os.path.join(lib_dir, current_plat)
        if os.path.isdir(plat_dir):
            for f in os.listdir(plat_dir):
                src = os.path.join(plat_dir, f)
                dst = os.path.join(lib_dir, f)
                if not os.path.exists(dst) and os.path.isfile(src):
                    shutil.copy2(src, dst)
                    Logger.log("d", "FEA Infill: Copied platform lib %s → lib/", f)

        # On Windows, add lib/ to DLL search path
        if sys.platform == "win32":
            os.environ["PATH"] = lib_dir + os.pathsep + os.environ.get("PATH", "")
            # Python 3.8+ also needs os.add_dll_directory
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(lib_dir)
                except OSError:
                    pass

    def check_all(self) -> Dict[str, bool]:
        """Check which required packages are importable.

        Uses actual import (not just find_spec) because PyInstaller-frozen
        builds may not have proper specs for bundled packages.
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

    @staticmethod
    def is_platform_supported() -> bool:
        """Check if the current platform has gmsh native library support.

        gmsh provides wheels for macOS (ARM64, x86_64) and Windows (x64).
        Linux is NOT supported via wheels — gmsh must be installed via
        the system package manager (apt install gmsh, etc.).
        """
        import platform
        system = platform.system()
        return system in ("Darwin", "Windows")

    @staticmethod
    def platform_message() -> str:
        """Return a user-facing message about platform support."""
        import platform
        system = platform.system()
        if system == "Linux":
            return ("FEA Infill Optimizer requires gmsh, which is not available as a "
                    "bundled package on Linux.\n\n"
                    "Install it via your package manager:\n"
                    "  Ubuntu/Debian: sudo apt install gmsh python3-gmsh\n"
                    "  Fedora: sudo dnf install gmsh\n"
                    "  Arch: sudo pacman -S gmsh\n\n"
                    "Then restart Cura.")
        return ""

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
