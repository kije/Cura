# Copyright (c) 2024 Cura Non-Planar Contributors
# Non-Planar Slicing Plugin is released under the terms of the LGPLv3 or higher.

"""Pytest configuration for NonPlanarSlicing tests.

Adds the plugin root to sys.path so that test modules can import
the analysis and gcode sub-packages directly without triggering the
top-level __init__.py (which requires PyQt6/Cura environment).
"""

import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent
if str(_plugin_root) not in sys.path:
    sys.path.insert(0, str(_plugin_root))

# Also ensure the tests directory itself doesn't get imported as a package
# through the NonPlanarSlicing __init__.py chain.
