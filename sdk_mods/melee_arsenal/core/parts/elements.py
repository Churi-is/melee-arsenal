"""Element parts — optional/locked 4th part governed by the Blade's ElementPolicy.

Each `ElementPartSpec` owns its prefix table, modifiers, DoT/proc values, damage
type, donor path, roll weight, and InkBlade kit/atlas ids. Element eligibility for
a Blade (NEVER / OPTIONAL / ALWAYS_ELEMENTAL / ALWAYS_EXPLOSIVE) and the element
roll are pure data-side logic and live here too.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping

from ..enums import Element, ElementPolicy, Manufacturer, ModifierMode, StatId
from .blades import BLADES
from .common import StatModifier, resolve_prefix, weighted_choice
from .manufacturers import MANUFACTURERS

NO_ELEMENT_ID: str = "Elem_None"

@dataclass(frozen=True, slots=True)
class ElementPartSpec:
    """Elemental infusion part (occupies ElementalPartDefinition in BL2)."""

    part_id: str                  # e.g. "Elem_Fire"
    element: Element
    display_name: str
    prefix_by_manufacturer: Mapping[Manufacturer, str]
    default_prefix: str
    modifiers: tuple[StatModifier, ...]
    dot_dps_ratio: float          # Base DoT per second as a fraction of lvl-scaled base hit damage
    base_proc_chance: float       # Base elemental proc chance per swing (e.g. 0.28 = 28%)
    damage_type_path: str
    donor_part_path: str
    weight: float = 1.0
    # Matching InkBlade kit fragment id; None = no custom mesh (physical steel).
    kit_id: str | None = None
    # Key into the six elemental texture atlases in the compiled InkBlade kit.
    atlas_key: str = "None"

    def prefix_for(self, manufacturer: Manufacturer) -> str:
        return resolve_prefix(self.prefix_by_manufacturer, self.default_prefix, manufacturer)

    @property
    def is_dot_element(self) -> bool:
        """True for Fire / Shock / Corrosive / Slag (show DoT + chance card rows)."""
        return self.element in (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG)

    @property
    def is_explosive(self) -> bool:
        """True for Torgue's always-on explosive splash."""
        return self.element is Element.EXPLOSIVE


# Registry: one entry per Element (including physical / no element)

ELEMENTS: dict[str, ElementPartSpec] = {
    "Elem_None": ElementPartSpec(
        part_id="Elem_None",
        element=Element.NONE,
        display_name="Physical Steel",
        prefix_by_manufacturer={},
        default_prefix="",
        modifiers=(),
        dot_dps_ratio=0.0,
        base_proc_chance=0.0,
        damage_type_path="GD_Impact.DamageType.DmgType_Normal",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_None",
        weight=2.2,
        kit_id=None,
        atlas_key="None",
    ),
    "Elem_Fire": ElementPartSpec(
        part_id="Elem_Fire",
        element=Element.FIRE,
        display_name="Incendiary Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Hot-Button",
            Manufacturer.BANDIT: "Burny",
            Manufacturer.MALIWAN: "Searing",
        },
        default_prefix="Incendiary",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.14),
        ),
        dot_dps_ratio=0.72,
        base_proc_chance=0.30,
        damage_type_path="GD_Incendiary.DamageType.DmgType_Incendiary_Impact",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Fire",
        weight=1.0,
        kit_id="element_fire",
        atlas_key="Fire",
    ),
    "Elem_Shock": ElementPartSpec(
        part_id="Elem_Shock",
        element=Element.SHOCK,
        display_name="Shock Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Voltaic",
            Manufacturer.BANDIT: "Zappy",
            Manufacturer.MALIWAN: "Arcing",
        },
        default_prefix="Shock",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.14),
        ),
        dot_dps_ratio=0.72,
        base_proc_chance=0.30,
        damage_type_path="GD_Shock.DamageType.DmgType_Shock_Impact",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Shock",
        weight=1.0,
        kit_id="element_shock",
        atlas_key="Shock",
    ),
    "Elem_Corrosive": ElementPartSpec(
        part_id="Elem_Corrosive",
        element=Element.CORROSIVE,
        display_name="Corrosive Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Caustic",
            Manufacturer.BANDIT: "Assid",
            Manufacturer.MALIWAN: "Vitriolic",
        },
        default_prefix="Corrosive",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.14),
        ),
        dot_dps_ratio=0.72,
        base_proc_chance=0.30,
        damage_type_path="GD_Corrosive.DamageType.DmgType_Corrosive_Impact",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Corrosive",
        weight=1.0,
        kit_id="element_corrosive",
        atlas_key="Corrosive",
    ),
    "Elem_Slag": ElementPartSpec(
        part_id="Elem_Slag",
        element=Element.SLAG,
        display_name="Slag Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Slagging",
            Manufacturer.BANDIT: "Slaggy",
            Manufacturer.MALIWAN: "Transmuting",
        },
        default_prefix="Slag",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.16),
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.15),
        ),
        dot_dps_ratio=0.25,
        base_proc_chance=0.36,
        damage_type_path="GD_Amp.DamageType.DmgType_Amp_Impact",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Slag",
        weight=0.8,
        kit_id="element_slag",
        atlas_key="Slag",
    ),
    "Elem_Explosive": ElementPartSpec(
        part_id="Elem_Explosive",
        element=Element.EXPLOSIVE,
        display_name="Explosive Charge",
        prefix_by_manufacturer={
            Manufacturer.TORGUE: "BLOW-UP",
        },
        default_prefix="Explosive",
        modifiers=(),  # Torgue Blast-Cleaver's base_damage_mult already budgets explosive cleave + splash
        dot_dps_ratio=0.0,
        base_proc_chance=1.0,  # Every swing detonates explosive splash on impact
        damage_type_path="GD_Explosive.DamageType.DmgType_Explosive_Impact_ForceFlinch",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Fire",
        weight=1.0,
        kit_id="element_explosive",
        atlas_key="Explosive",
    ),
}

ELEMENT_PART_BY_TYPE: dict[Element, ElementPartSpec] = {
    ep.element: ep for ep in ELEMENTS.values()
}

# Element data-side logic

def eligible_elements_for_blade(blade_id: str) -> list[ElementPartSpec]:
    """Return valid ElementPartSpecs for a given Blade according to its Manufacturer's ElementPolicy."""
    blade = BLADES[blade_id]
    maker = MANUFACTURERS[blade.manufacturer]

    if maker.element_policy == ElementPolicy.NEVER:
        return [ELEMENTS[NO_ELEMENT_ID]]
    if maker.element_policy == ElementPolicy.ALWAYS_EXPLOSIVE:
        return [ELEMENT_PART_BY_TYPE[Element.EXPLOSIVE]]
    if maker.element_policy == ElementPolicy.ALWAYS_ELEMENTAL:
        return [
            ELEMENT_PART_BY_TYPE[el]
            for el in maker.allowed_elements
            if el not in (Element.NONE, Element.EXPLOSIVE)
        ]

    # OPTIONAL: includes Elem_None plus allowed elements
    return [ELEMENT_PART_BY_TYPE[el] for el in maker.allowed_elements]


def roll_element_id(rng: random.Random, blade_id: str) -> str:
    """Weighted pick from the element parts legal for this Blade's ElementPolicy."""
    choices = eligible_elements_for_blade(blade_id)
    return weighted_choice(rng, [(e.part_id, e.weight) for e in choices])
