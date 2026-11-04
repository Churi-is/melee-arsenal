"""Melee Arsenal part catalog — the SDK-independent **data layer**.

One module per part type; each owns its spec dataclass, its full registry, derived
indexes (including InkBlade kit ids), and all pure-Python logic for that part:

===========================  ==========================================================
``manufacturers.py``          Archetypes: damage/cadence, element policy, cleave
                              profile, secondary attack, mass, guard recovery
``blades.py``                 Blade specs, title matrix, Blade->Manufacturer index
``grips.py``                  Grip specs, matching-grip check, grip roll
``accessories.py``            Accessory specs, rarity gates, compatibility, rolls,
                              throw mass and guard cooldown deltas
``elements.py``               Element specs, ElementPolicy eligibility, element roll
``weapon.py``                 Composed weapon value types + name resolution
``common.py``                 Shared dataclasses and helpers
``validation.py``             Registry invariants (`validate_catalog()`)
===========================  ==========================================================

Nothing here imports ``unrealsdk`` — the package is fully testable in plain
CPython. To add a part, add ONE `Spec(...)` entry to the matching registry; the
derived indexes and eligibility/roll logic pick it up automatically. Run
``validate_catalog()`` (or the unit tests) afterwards. See ``docs/DEVELOPER.md``
for the full checklist, including the SDK-side and InkBlade kit steps.
"""
from __future__ import annotations

from .accessories import (
    ACCESSORIES,
    ACCESSORY_GUARD_COOLDOWN_DELTA,
    ACCESSORY_ROLL_CHANCE,
    ACCESSORY_TO_KIT_ID,
    ACCESSORY_WEIGHT_KG,
    NO_ACCESSORY_ID,
    AccessoryPartSpec,
    eligible_accessories_for_roll,
    roll_accessory_id,
)
from .blades import (
    BLADE_BY_MANUFACTURER,
    BLADE_TO_KIT_ID,
    BLADES,
    BladePartSpec,
    blade_for_manufacturer,
    title_for_parts,
)
from .common import GestaltFragmentMeta, StatModifier, resolve_prefix, weighted_choice
from .elements import (
    DOT_ELEMENTS,
    ELEMENT_ID_TO_ATLAS_KEY,
    ELEMENT_PART_BY_TYPE,
    ELEMENT_TO_KIT_ID,
    ELEMENTS,
    NO_ELEMENT_ID,
    ElementPartSpec,
    eligible_elements_for_blade,
    roll_element_id,
)
from .grips import (
    GRIP_BY_MANUFACTURER,
    GRIP_TO_KIT_ID,
    GRIPS,
    GripPartSpec,
    is_matching_grip,
    roll_grip_id,
)
from .manufacturers import (
    GUARD_COOLDOWN_BY_MANUFACTURER,
    GUARD_COOLDOWN_DEFAULT_SEC,
    MANUFACTURERS,
    CleaveProfile,
    ManufacturerSpec,
    allowed_elements_for,
    guard_cooldown_base_sec,
    manufacturer_spec,
    secondary_attack_for,
)
from .validation import PART_REGISTRIES, validate_catalog
from .weapon import (
    MeleeWeaponInstance,
    MeleeWeaponParts,
    PlayerMeleeContext,
    ResolvedParts,
    resolve_parts,
    resolve_weapon_name,
)

__all__ = [
    "ACCESSORIES",
    "ACCESSORY_GUARD_COOLDOWN_DELTA",
    "ACCESSORY_ROLL_CHANCE",
    "ACCESSORY_TO_KIT_ID",
    "ACCESSORY_WEIGHT_KG",
    "BLADE_BY_MANUFACTURER",
    "BLADE_TO_KIT_ID",
    "BLADES",
    "CleaveProfile",
    "DOT_ELEMENTS",
    "ELEMENT_ID_TO_ATLAS_KEY",
    "ELEMENT_PART_BY_TYPE",
    "ELEMENT_TO_KIT_ID",
    "ELEMENTS",
    "GRIP_BY_MANUFACTURER",
    "GRIP_TO_KIT_ID",
    "GRIPS",
    "GUARD_COOLDOWN_BY_MANUFACTURER",
    "GUARD_COOLDOWN_DEFAULT_SEC",
    "GestaltFragmentMeta",
    "MANUFACTURERS",
    "MeleeWeaponInstance",
    "MeleeWeaponParts",
    "NO_ACCESSORY_ID",
    "NO_ELEMENT_ID",
    "PART_REGISTRIES",
    "PlayerMeleeContext",
    "ResolvedParts",
    "StatModifier",
    "AccessoryPartSpec",
    "BladePartSpec",
    "ElementPartSpec",
    "GripPartSpec",
    "ManufacturerSpec",
    "allowed_elements_for",
    "blade_for_manufacturer",
    "eligible_accessories_for_roll",
    "eligible_elements_for_blade",
    "guard_cooldown_base_sec",
    "is_matching_grip",
    "manufacturer_spec",
    "resolve_prefix",
    "resolve_parts",
    "resolve_weapon_name",
    "roll_accessory_id",
    "roll_element_id",
    "roll_grip_id",
    "secondary_attack_for",
    "title_for_parts",
    "validate_catalog",
    "weighted_choice",
]
