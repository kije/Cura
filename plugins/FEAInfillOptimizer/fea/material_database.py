# Copyright (c) 2024 FEA Infill Contributors
# Released under the terms of the LGPLv3 or higher.

"""Material property database for common FDM 3D-printing filaments.

All stiffness values reflect the typical anisotropy of FDM parts:
- E_xy: in-plane (XY) Young's modulus, MPa
- E_z:  through-layer (Z) Young's modulus, MPa
- nu:   Poisson's ratio (treated as isotropic approximation)
- yield_strength: tensile yield strength, MPa
- density: filament density, g/cm³
- failure_mode: dominant failure mode ("ductile", "brittle", "hyperelastic")

Material-specific notes
-----------------------
- **Nylon** (PA6, conditioned state at 50 % RH): moisture absorption reduces
  stiffness and strength relative to dry-as-moulded values.
- **CF_Nylon**: continuous carbon-fibre reinforcement in the XY plane.
  E_z reflects the fibre alignment effect — Z-direction modulus is approximately
  25 % of E_xy (fibre orientation penalty), not the ~50 % ratio seen for neat
  polymer interlayer weakness.
- **TPU_95A**: this material is hyperelastic and is NOT valid for linear elastic
  FEA.  A runtime warning is issued when it is selected.  Results must not be
  used for structural assessment without a nonlinear hyperelastic solver.
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
        failure_mode: Dominant failure mode — one of ``"ductile"``,
            ``"brittle"``, or ``"hyperelastic"``.  Materials with
            ``"hyperelastic"`` failure mode are not valid for linear elastic FEA.
        notes: Optional free-text annotation for conditioning state, caveats, etc.
    """

    name: str
    E_xy: float            # MPa
    E_z: float             # MPa
    nu: float              # dimensionless
    yield_strength: float  # MPa
    density: float         # g/cm³
    failure_mode: str = "ductile"   # "ductile" | "brittle" | "hyperelastic"
    notes: str = ""


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
        failure_mode="brittle",
    ),
    "ABS": Material(
        name="ABS",
        E_xy=2100.0,
        E_z=1050.0,
        nu=0.35,
        yield_strength=35.0,
        density=1.05,
        failure_mode="ductile",
    ),
    "PETG": Material(
        name="PETG",
        E_xy=2000.0,
        E_z=1000.0,
        nu=0.38,
        yield_strength=42.0,
        density=1.27,
        failure_mode="ductile",
    ),
    "Nylon": Material(
        name="Nylon",
        E_xy=1400.0,
        E_z=700.0,
        nu=0.40,
        yield_strength=48.0,
        density=1.14,
        failure_mode="ductile",
        notes="PA6, conditioned state (50 % RH). Dry-as-moulded stiffness is ~20 % higher.",
    ),
    "PC": Material(
        name="PC",
        E_xy=2200.0,
        E_z=1100.0,
        nu=0.37,
        yield_strength=60.0,
        density=1.20,
        failure_mode="ductile",
    ),
    "TPU_95A": Material(
        name="TPU_95A",
        E_xy=26.0,
        E_z=13.0,
        nu=0.48,
        yield_strength=30.0,
        density=1.21,
        failure_mode="hyperelastic",
        notes="Hyperelastic elastomer. Linear elastic FEA is NOT valid. "
              "Results must not be used for structural assessment.",
    ),
    "CF_Nylon": Material(
        name="CF_Nylon",
        E_xy=6500.0,
        E_z=1600.0,   # ~25 % of E_xy: fibre alignment in XY depresses Z-modulus
        nu=0.35,
        yield_strength=80.0,
        density=1.10,
        failure_mode="brittle",
        notes="Short carbon-fibre reinforced PA. E_z reflects fibre alignment penalty "
              "(~25 % of E_xy), not the ~50 % ratio of neat polymer interlayer weakness.",
    ),
}


class MaterialDatabase:
    """Static accessor for the built-in material property database.

    Notes:
        - ``E_z`` (through-layer modulus) is stored per material but the
          current isotropic FEA solver uses only ``E_xy``.  Anisotropic support
          is planned for a future release.
        - **Nylon** values represent PA6 in the conditioned state (50 % relative
          humidity).  Dry-as-moulded stiffness is approximately 20 % higher.
        - **CF_Nylon** ``E_z`` is ~25 % of ``E_xy`` because carbon fibre
          reinforcement is aligned in the XY print plane; this is lower than the
          ~50 % ratio expected for neat-polymer interlayer weakness.
        - **TPU_95A** is a hyperelastic elastomer.  Linear elastic FEA is NOT
          valid for this material.  A ``UserWarning`` is raised at solver startup
          when TPU_95A is selected.  Results must not be used for structural
          assessment without a nonlinear hyperelastic formulation.
        - **TPU_95A** also has ``nu=0.48``, which is near the incompressible
          limit and will cause volumetric locking with linear tetrahedral elements
          (nu > 0.45).

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
