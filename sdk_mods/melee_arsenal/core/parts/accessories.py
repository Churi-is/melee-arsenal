"""Accessory parts — optional, rarity-gated prefixes and focused stat perks.

Everything about an accessory lives in its `AccessoryPartSpec` entry: prefixes,
modifiers, roll weight, rarity gate, Blade/Element compatibility flags, its
Gestalt/kit fragment, mass added to a thrown blade, Guard cooldown delta, and the
donor part path. Rarity spawn chances, eligibility filtering and the accessory
roll itself are all data-side logic and therefore also live in this file.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping

from ..enums import Manufacturer, ModifierMode, Rarity, StatId, TargetingMode
from .blades import BLADES
from .common import GestaltFragmentMeta, StatModifier, resolve_prefix, weighted_choice
from .elements import ELEMENTS
from .manufacturers import MANUFACTURERS

# BL2 donor part used when a weapon has no accessory.
NO_ACCESSORY_ID: str = "Acc_None"


@dataclass(frozen=True, slots=True)
class AccessoryPartSpec:
    """Optional/rarity-gated melee accessory part (occupies Accessory1PartDefinition in BL2)."""

    part_id: str                  # e.g. "Acc_Serrated"
    display_name: str
    prefix_by_manufacturer: Mapping[Manufacturer, str]
    default_prefix: str
    modifiers: tuple[StatModifier, ...]
    gestalt: GestaltFragmentMeta | None
    donor_part_path: str
    min_rarity: Rarity = Rarity.COMMON
    elemental_only: bool = False
    cleave_only: bool = False
    single_target_only: bool = False
    weight: float = 1.0
    # Extra kilograms a thrown blade carries when this accessory is fitted.
    throw_weight_kg: float = 0.0
    # Seconds added to a Guard's recovery when this accessory is fitted.
    guard_cooldown_delta_sec: float = 0.0
    # Matching InkBlade kit fragment id; None = no custom mesh (Acc_None).
    kit_id: str | None = None

    def prefix_for(self, manufacturer: Manufacturer) -> str:
        return resolve_prefix(self.prefix_by_manufacturer, self.default_prefix, manufacturer)


# ---------------------------------------------------------------------------
# Rarity -> accessory spawn probability (BL2-inspired)
# ---------------------------------------------------------------------------

# Vanilla-inspired accessory spawn probability by rarity tier
ACCESSORY_ROLL_CHANCE: dict[Rarity, float] = {
    Rarity.COMMON: 0.20,
    Rarity.UNCOMMON: 0.55,
    Rarity.RARE: 0.85,
    Rarity.VERY_RARE: 1.00,
    Rarity.E_TECH: 1.00,
    Rarity.LEGENDARY: 1.00,
    Rarity.SERAPH: 1.00,
    Rarity.PEARLESCENT: 1.00,
    Rarity.EFFERVESCENT: 1.00,
}


# ---------------------------------------------------------------------------
# Registry: one entry per Accessory (including the empty one)
# ---------------------------------------------------------------------------

ACCESSORIES: dict[str, AccessoryPartSpec] = {
    "Acc_None": AccessoryPartSpec(
        part_id="Acc_None",
        display_name="No Accessory",
        prefix_by_manufacturer={},
        default_prefix="",
        modifiers=(),
        gestalt=None,
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_None",
        min_rarity=Rarity.COMMON,
        weight=1.5,
        throw_weight_kg=0.0,
        guard_cooldown_delta_sec=0.0,
        kit_id=None,
    ),
    "Acc_Serrated": AccessoryPartSpec(
        part_id="Acc_Serrated",
        display_name="Serrated Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Lacerating",
            Manufacturer.BANDIT: "Jaggid",
            Manufacturer.JAKOBS: "Vicious",
            Manufacturer.MALIWAN: "Rending",
            Manufacturer.TORGUE: "Shredding",
        },
        default_prefix="Serrated",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.16),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_Serrated",
            donor_fragment="Acc_Barrel_Blade1",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Bayonet_1",
        min_rarity=Rarity.COMMON,
        weight=1.0,
        throw_weight_kg=0.10,
        guard_cooldown_delta_sec=0.0,
        kit_id="accessory_serrated_edge",
    ),
    "Acc_Counterweight": AccessoryPartSpec(
        part_id="Acc_Counterweight",
        display_name="Balanced Pommel",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Dynamic",
            Manufacturer.BANDIT: "Twitchy",
            Manufacturer.JAKOBS: "Nimble",
            Manufacturer.MALIWAN: "Harmonic",
            Manufacturer.TORGUE: "Turbo",
        },
        default_prefix="Swift",
        modifiers=(
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, 0.18),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_Counterweight",
            donor_fragment="Acc_Grip_Stock",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Stock_Stability",
        min_rarity=Rarity.COMMON,
        weight=1.0,
        throw_weight_kg=0.18,
        guard_cooldown_delta_sec=-0.10,
        kit_id="accessory_balanced_pommel",
    ),
    "Acc_ExtendedTang": AccessoryPartSpec(
        part_id="Acc_ExtendedTang",
        display_name="Extended Blade Fuller",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Long-Reach",
            Manufacturer.BANDIT: "Longg",
            Manufacturer.JAKOBS: "Lunging",
            Manufacturer.MALIWAN: "Projected",
            Manufacturer.TORGUE: "Colossal",
        },
        default_prefix="Extended",
        modifiers=(
            StatModifier(StatId.RANGE_UU, ModifierMode.SCALE, 0.22),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.05),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_ExtendedTang",
            donor_fragment="Acc_Barrel_Blade2",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Bayonet_2",
        min_rarity=Rarity.UNCOMMON,
        weight=1.0,
        throw_weight_kg=0.15,
        guard_cooldown_delta_sec=0.05,
        kit_id="accessory_extended_blade_fuller",
    ),
    "Acc_RazorFuller": AccessoryPartSpec(
        part_id="Acc_RazorFuller",
        display_name="Monofilament Honing",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Auditing",
            Manufacturer.JAKOBS: "Deadeye",
        },
        default_prefix="Keen",
        modifiers=(
            StatModifier(StatId.CRIT_BONUS, ModifierMode.POST_ADD, 0.45),
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.06),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_RazorFuller",
            donor_fragment="Acc_Barrel_Laser",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Laser_Accuracy",
        min_rarity=Rarity.UNCOMMON,
        single_target_only=True,
        weight=1.0,
        throw_weight_kg=0.08,
        guard_cooldown_delta_sec=0.0,
        kit_id="accessory_monofilament_honing",
    ),
    "Acc_BroadSweep": AccessoryPartSpec(
        part_id="Acc_BroadSweep",
        display_name="Flared Cleave Guard",
        prefix_by_manufacturer={
            Manufacturer.BANDIT: "Whirlwind",
            Manufacturer.MALIWAN: "Crescent",
            Manufacturer.TORGUE: "Shockwave",
        },
        default_prefix="Sweeping",
        modifiers=(
            StatModifier(StatId.CLEAVE_ARC, ModifierMode.POST_ADD, 25.0),
            StatModifier(StatId.RANGE_UU, ModifierMode.SCALE, 0.10),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_BroadSweep",
            donor_fragment="Acc_Barrel_Tech2",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Tech_2_Damage",
        min_rarity=Rarity.UNCOMMON,
        cleave_only=True,
        weight=1.0,
        throw_weight_kg=0.22,
        guard_cooldown_delta_sec=0.10,
        kit_id="accessory_flared_cleave_guard",
    ),
    "Acc_HeavySpine": AccessoryPartSpec(
        part_id="Acc_HeavySpine",
        display_name="Reinforced Weighted Spine",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Executive",
            Manufacturer.BANDIT: "Brutul",
            Manufacturer.JAKOBS: "Tempered",
            Manufacturer.MALIWAN: "Dense",
            Manufacturer.TORGUE: "MAX-TORQUE",
        },
        default_prefix="Heavy",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.26),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.10),
            StatModifier(StatId.MOMENTUM, ModifierMode.SCALE, 0.30),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_HeavySpine",
            donor_fragment="Acc_Barrel_Tech1",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Tech_1_Mag",
        min_rarity=Rarity.RARE,
        weight=0.85,
        throw_weight_kg=0.32,
        guard_cooldown_delta_sec=0.25,
        kit_id="accessory_reinforced_heavy_spine",
    ),
    "Acc_FluxCapacitor": AccessoryPartSpec(
        part_id="Acc_FluxCapacitor",
        display_name="Overclocked Elemental Coil",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Ionized",
            Manufacturer.BANDIT: "Sizzlin",
            Manufacturer.MALIWAN: "Catalyzed",
            Manufacturer.TORGUE: "Volatile",
        },
        default_prefix="Overcharged",
        modifiers=(
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.30),
            StatModifier(StatId.STATUS_DAMAGE, ModifierMode.SCALE, 0.25),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_FluxCapacitor",
            donor_fragment="Acc_Barrel_Tech3",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Tech_3_Firerate",
        min_rarity=Rarity.UNCOMMON,
        elemental_only=True,
        weight=0.9,
        throw_weight_kg=0.20,
        guard_cooldown_delta_sec=0.05,
        kit_id="accessory_overclocked_elemental_coil",
    ),
}

# Derived lookups used by throw math and the runtime asset binder.
ACCESSORY_WEIGHT_KG: dict[str, float] = {
    part_id: acc.throw_weight_kg for part_id, acc in ACCESSORIES.items()
}
ACCESSORY_GUARD_COOLDOWN_DELTA: dict[str, float] = {
    part_id: acc.guard_cooldown_delta_sec for part_id, acc in ACCESSORIES.items()
}
ACCESSORY_TO_KIT_ID: dict[str, str | None] = {
    part_id: acc.kit_id for part_id, acc in ACCESSORIES.items()
}


# ---------------------------------------------------------------------------
# Accessory data-side logic
# ---------------------------------------------------------------------------

def eligible_accessories_for_roll(
    blade_id: str,
    element_id: str,
    rarity: Rarity,
) -> list[AccessoryPartSpec]:
    """Return non-empty accessories legal for `(blade, element, rarity)`."""
    blade = BLADES[blade_id]
    maker = MANUFACTURERS[blade.manufacturer]
    is_dot_elemental = ELEMENTS[element_id].is_dot_element
    mode = maker.cleave_profile.mode

    valid: list[AccessoryPartSpec] = []
    for acc in ACCESSORIES.values():
        if acc.part_id == NO_ACCESSORY_ID:
            continue
        if int(rarity) < int(acc.min_rarity):
            continue
        if acc.elemental_only and not is_dot_elemental:
            continue
        if acc.cleave_only and mode != TargetingMode.FRONTAL_CLEAVE:
            continue
        if acc.single_target_only and mode != TargetingMode.SINGLE_TARGET:
            continue
        valid.append(acc)
    return valid


def roll_accessory_id(
    rng: random.Random,
    blade_id: str,
    element_id: str,
    rarity: Rarity,
) -> str:
    """Roll an accessory (or `NO_ACCESSORY_ID`) for `(blade, element, rarity)`.

    Rarity decides whether an accessory appears at all; compatibility filtering and
    the weighted pick are accessory-side rules, so they stay in this module.
    """
    acc_chance = ACCESSORY_ROLL_CHANCE.get(rarity, 0.5)
    acc_pool = eligible_accessories_for_roll(blade_id, element_id, rarity)
    if acc_pool and rng.random() < acc_chance:
        return weighted_choice(rng, [(a.part_id, a.weight) for a in acc_pool])
    return NO_ACCESSORY_ID
