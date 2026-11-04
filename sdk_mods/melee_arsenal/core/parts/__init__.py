"""Data layer: part specs, registries, and the roll rules that own them.

One module per part type. Each owns its spec dataclass, its complete registry, and the
logic for that part type, so adding a blade/grip/accessory/element never touches another file.
Nothing in this package imports `unrealsdk`.
"""
from __future__ import annotations

from .accessories import (
    ACCESSORIES,
    ACCESSORY_ROLL_CHANCE,
    NO_ACCESSORY_ID,
    AccessoryPartSpec,
    eligible_accessories_for_roll,
    roll_accessory_id,
)
from .blades import (
    BLADE_BY_MANUFACTURER,
    BLADES,
    BladePartSpec,
)
from .common import GestaltFragmentMeta, StatModifier, resolve_prefix, weighted_choice
from .elements import (
    ELEMENT_PART_BY_TYPE,
    ELEMENTS,
    NO_ELEMENT_ID,
    ElementPartSpec,
    eligible_elements_for_blade,
    roll_element_id,
)
from .grips import GRIPS, GripPartSpec, roll_grip_id
from .manufacturers import MANUFACTURERS, CleaveProfile, ManufacturerSpec
from .weapon import (
    MeleeWeaponInstance,
    MeleeWeaponParts,
    PlayerMeleeContext,
    resolve_weapon_name,
)

__all__ = [
    "ACCESSORIES",
    "ACCESSORY_ROLL_CHANCE",
    "BLADES",
    "BLADE_BY_MANUFACTURER",
    "ELEMENTS",
    "ELEMENT_PART_BY_TYPE",
    "GRIPS",
    "MANUFACTURERS",
    "NO_ACCESSORY_ID",
    "NO_ELEMENT_ID",
    "AccessoryPartSpec",
    "BladePartSpec",
    "CleaveProfile",
    "ElementPartSpec",
    "GestaltFragmentMeta",
    "GripPartSpec",
    "ManufacturerSpec",
    "MeleeWeaponInstance",
    "MeleeWeaponParts",
    "PlayerMeleeContext",
    "StatModifier",
    "eligible_accessories_for_roll",
    "eligible_elements_for_blade",
    "resolve_prefix",
    "resolve_weapon_name",
    "roll_accessory_id",
    "roll_element_id",
    "roll_grip_id",
    "weighted_choice",
]
