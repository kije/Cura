# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Material property database for common FDM 3D-printing filaments.

All stiffness values reflect the typical anisotropy of FDM parts:
- E_xy: in-plane (XY) Young's modulus, MPa
- E_z:  through-layer (Z) Young's modulus, MPa  ≈ 0.5 × E_xy
- nu:   Poisson's ratio (treated as isotropic approximation)
- yield_strength: tensile yield strength, MPa
- density: filament density, g/cm³
"""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Material:
    """Mechanical properties for a single 3D-printing material.

    Attributes:
        name: Human-readable material identifier (e.g. ``"PLA"``).
        E_xy: In-plane Young's modulus in MPa.
        E_z: Through-layer Young's modulus in MPa (interlayer weakness).
        nu: Poisson's ratio (dimensionless).
        yield_strength: Tensile yield / ultimate strength in MPa.
        density: Material density in g/cm³.
    """

    name: str
    E_xy: float       # MPa
    E_z: float        # MPa
    nu: float         # dimensionless
    yield_strength: float  # MPa
    density: float    # g/cm³


# ---------------------------------------------------------------------------
# Database entries
# ---------------------------------------------------------------------------

_MATERIALS: Dict[str, Material] = {
    "PLA": Material(
        name="PLA",
        E_xy=3000.0,
        E_z=1500.0,
        nu=0.36,
        yield_strength=50.0,
        density=1.24,
    ),
    "ABS": Material(
        name="ABS",
        E_xy=2100.0,
        E_z=1050.0,
        nu=0.35,
        yield_strength=35.0,
        density=1.05,
    ),
    "PETG": Material(
        name="PETG",
        E_xy=2000.0,
        E_z=1000.0,
        nu=0.38,
        yield_strength=42.0,
        density=1.27,
    ),
    "Nylon": Material(
        name="Nylon",
        E_xy=1400.0,
        E_z=700.0,
        nu=0.40,
        yield_strength=48.0,
        density=1.14,
    ),
    "PC": Material(
        name="PC",
        E_xy=2200.0,
        E_z=1100.0,
        nu=0.37,
        yield_strength=60.0,
        density=1.20,
    ),
    "TPU_95A": Material(
        name="TPU_95A",
        E_xy=26.0,
        E_z=13.0,
        nu=0.48,
        yield_strength=30.0,
        density=1.21,
    ),
    "CF_Nylon": Material(
        name="CF_Nylon",
        E_xy=6500.0,
        E_z=3250.0,
        nu=0.35,
        yield_strength=80.0,
        density=1.10,
    ),
}


class MaterialDatabase:
    """Static accessor for the built-in material property database.

    Example::

        mat = MaterialDatabase.get_material("PLA")
        print(mat.E_xy, mat.yield_strength)
    """

    @staticmethod
    def get_material(name: str) -> Material:
        """Retrieve a :class:`Material` by name.

        Lookup is case-insensitive.  Falls back to PLA if the name is unknown.

        Args:
            name: Material identifier, e.g. ``"PLA"``, ``"cf_nylon"``.

        Returns:
            The matching :class:`Material` dataclass instance.
        """
        key = name.strip()
        # Try exact match first
        if key in _MATERIALS:
            return _MATERIALS[key]
        # Case-insensitive fallback
        key_upper = key.upper()
        for k, mat in _MATERIALS.items():
            if k.upper() == key_upper:
                return mat
        # Unknown material — warn and return PLA as safe default
        from UM.Logger import Logger
        Logger.log(
            "w",
            "FEA MaterialDatabase: unknown material '%s', defaulting to PLA.",
            name,
        )
        return _MATERIALS["PLA"]

    @staticmethod
    def available_materials() -> list[str]:
        """Return the list of known material names.

        Returns:
            Sorted list of material name strings.
        """
        return sorted(_MATERIALS.keys())
