"""Grip parts — cross-manufacturer hilts with handling/stat trade-offs.

Every Grip can roll on every Blade; a Grip whose manufacturer matches the Blade
grants the archetype's matching-grip damage/speed bonuses (see `core.stats`).
Roll weights, matching logic and the kit fragment ids live here next to the data.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..enums import Manufacturer, ModifierMode, StatId
from .blades import BladePartSpec
from .common import GestaltFragmentMeta, StatModifier, weighted_choice


@dataclass(frozen=True, slots=True)
class GripPartSpec:
    """Cross-manufacturer melee grip part (occupies GripPartDefinition in BL2)."""

    part_id: str                  # e.g. "Grip_Jakobs"
    manufacturer: Manufacturer
    display_name: str
    modifiers: tuple[StatModifier, ...]
    gestalt: GestaltFragmentMeta
    donor_part_path: str
    weight: float = 1.0
    # Matching InkBlade kit fragment id (`tools/inkblade_gen`).
    kit_id: str | None = None


# ---------------------------------------------------------------------------
# Registry: one entry per Grip
# ---------------------------------------------------------------------------

GRIPS: dict[str, GripPartSpec] = {
    "Grip_Hyperion": GripPartSpec(
        part_id="Grip_Hyperion",
        manufacturer=Manufacturer.HYPERION,
        display_name="Hyperion Ergonomic Hilt",
        modifiers=(
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, 0.08),
            StatModifier(StatId.RANGE_UU, ModifierMode.SCALE, 0.06),
            StatModifier(StatId.CRIT_BONUS, ModifierMode.POST_ADD, 0.15),
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.07),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Hyperion",
            donor_fragment="Pistol_Grip_Hyperion",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Hyperion",
        kit_id="grip_hyperion",
    ),
    "Grip_Bandit": GripPartSpec(
        part_id="Grip_Bandit",
        manufacturer=Manufacturer.BANDIT,
        display_name="Bandit Wrapped Tang",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.08),
            StatModifier(StatId.CLEAVE_ARC, ModifierMode.POST_ADD, 10.0),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.06),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Bandit",
            donor_fragment="Pistol_Grip_Bandit",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Bandit",
        kit_id="grip_bandit",
    ),
    "Grip_Jakobs": GripPartSpec(
        part_id="Grip_Jakobs",
        manufacturer=Manufacturer.JAKOBS,
        display_name="Jakobs Rosewood Hilt",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.12),
            StatModifier(StatId.CRIT_BONUS, ModifierMode.POST_ADD, 0.20),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.09),
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, -0.10),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Jakobs",
            donor_fragment="Pistol_Grip_Jakobs",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Jakobs",
        kit_id="grip_jakobs",
    ),
    "Grip_Maliwan": GripPartSpec(
        part_id="Grip_Maliwan",
        manufacturer=Manufacturer.MALIWAN,
        display_name="Maliwan Conduit Hilt",
        modifiers=(
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.20),
            StatModifier(StatId.STATUS_DAMAGE, ModifierMode.SCALE, 0.15),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, 0.05),
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.05),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Maliwan",
            donor_fragment="Pistol_Grip_Maliwan",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Maliwan",
        kit_id="grip_maliwan",
    ),
    "Grip_Torgue": GripPartSpec(
        part_id="Grip_Torgue",
        manufacturer=Manufacturer.TORGUE,
        display_name="Torgue Piston Hilt",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.14),
            StatModifier(StatId.MOMENTUM, ModifierMode.SCALE, 0.25),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.10),
            StatModifier(StatId.RANGE_UU, ModifierMode.SCALE, -0.04),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Torgue",
            donor_fragment="Pistol_Grip_Torgue",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Torgue",
        kit_id="grip_torgue",
    ),
}

GRIP_BY_MANUFACTURER: dict[Manufacturer, GripPartSpec] = {
    grip.manufacturer: grip for grip in GRIPS.values()
}

GRIP_TO_KIT_ID: dict[str, str | None] = {
    part_id: grip.kit_id for part_id, grip in GRIPS.items()
}


# ---------------------------------------------------------------------------
# Grip data-side logic
# ---------------------------------------------------------------------------

def is_matching_grip(blade: BladePartSpec, grip: GripPartSpec) -> bool:
    """True when Blade and Grip share a manufacturer (BL2 matching-grip bonus)."""
    return grip.manufacturer == blade.manufacturer


def roll_grip_id(rng: random.Random) -> str:
    """Weighted pick across every Grip (any Grip can spawn on any Blade)."""
    return weighted_choice(rng, [(g.part_id, g.weight) for g in GRIPS.values()])
