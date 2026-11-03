"""Manufacturer identities — the data that a Blade selects when it is rolled.

One entry in `MANUFACTURERS` owns everything about an archetype: archetype name and
flavour text, elemental policy, base damage/cadence, the cleave/targeting profile,
matching-grip bonuses, intrinsic modifiers, secondary attack, hardware mass, guard
recovery, and the donor manufacturer path used by the SDK side. Editing a Katana's
reach or adding a sixth manufacturer happens entirely in this file.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..enums import (
    Element,
    ElementPolicy,
    Manufacturer,
    ModifierMode,
    SecondaryAttack,
    StatId,
    TargetingMode,
)
from .common import StatModifier

# Fallback guard recovery for a manufacturer without an explicit tuning value.
GUARD_COOLDOWN_DEFAULT_SEC: float = 2.00


@dataclass(frozen=True, slots=True)
class CleaveProfile:
    """Spatial strike profile determined by the Blade (and modified by parts)."""

    mode: TargetingMode
    base_range_uu: float          # Reach in Unreal Units (~100 UU = 1m in card display; vanilla melee ~140-160 UU)
    arc_degrees: float            # Full horizontal cone angle in front of player (e.g. 95° for Bandit, 12° for Katana)
    pitch_limit_degrees: float    # Vertical half-angle tolerance so frontal cleave hits crouched/leaping foes
    max_targets: int              # 1 for single-target; 16+ (effectively unlimited) for frontal cleave
    can_weakspot_crit: bool       # True for surgical single-target blades (Hyperion/Jakobs); optional on cleave
    base_crit_bonus: float        # Extra critical hit bonus (e.g. 0.75 = +75% crit dmg)
    splash_radius_uu: float = 0.0 # Explosive splash radius (Torgue Blast-Cleaver)
    splash_damage_pct: float = 0.0


@dataclass(frozen=True, slots=True)
class ManufacturerSpec:
    """Defines a manufacturer's blade identity, stats, element rules, and input behavior."""

    manufacturer: Manufacturer
    archetype_name: str           # e.g. "Katana", "Machete", "Sabre", "Phaseblade", "Blast-Cleaver"
    description: str
    element_policy: ElementPolicy
    allowed_elements: tuple[Element, ...]
    base_damage_mult: float       # Multiplies the melee weapon type's level-scaled gun-damage baseline
    base_swing_speed: float       # Swings per second (sets WillowWeapon.FireInterval = 1 / swing_speed)
    semi_auto_click: bool         # True = swings as fast as clicked (Jakobs); False = hold LMB for continuous swings
    cleave_profile: CleaveProfile
    matching_grip_damage_bonus: float = 0.12
    matching_grip_speed_bonus: float = 0.10
    intrinsic_modifiers: tuple[StatModifier, ...] = ()
    donor_manufacturer_path: str = ""
    # RMB / Left Trigger secondary attack identity (Maliwan deliberately gets none).
    secondary_attack: SecondaryAttack = SecondaryAttack.NONE
    # Mass of this manufacturer's blade & grip hardware in kilograms. Bandit/Torgue thrown
    # blades take their flight arc (speed, loft, range) from the mass of the actual parts.
    part_weight_kg: float = 2.0
    # Base RMB Guard recovery (seconds); only GUARD manufacturers tune it explicitly.
    guard_cooldown_sec: float = GUARD_COOLDOWN_DEFAULT_SEC


# ---------------------------------------------------------------------------
# Registry: one entry per manufacturer / blade archetype
# ---------------------------------------------------------------------------

MANUFACTURERS: dict[Manufacturer, ManufacturerSpec] = {
    Manufacturer.HYPERION: ManufacturerSpec(
        manufacturer=Manufacturer.HYPERION,
        archetype_name="Katana",
        description=(
            "High-frequency corporate monomolecular katana. Surgical single-target reach "
            "with fast cadence and elevated weakspot critical damage. Optional elemental edge."
        ),
        element_policy=ElementPolicy.OPTIONAL,
        allowed_elements=(
            Element.NONE,
            Element.FIRE,
            Element.SHOCK,
            Element.CORROSIVE,
            Element.SLAG,
        ),
        base_damage_mult=1.18,
        base_swing_speed=1.85,
        semi_auto_click=False,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.SINGLE_TARGET,
            base_range_uu=340.0,     # 3.40m surgical lunge/slash reach
            arc_degrees=14.0,        # Generous aim-assist ray cone onto single closest-to-reticle target
            pitch_limit_degrees=45.0,
            max_targets=1,
            can_weakspot_crit=True,
            base_crit_bonus=0.75,    # +75% critical hit bonus
        ),
        matching_grip_damage_bonus=0.10,
        matching_grip_speed_bonus=0.12,
        intrinsic_modifiers=(
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.10),
        ),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Hyperion",
        # High-tech monomolecular alloy: the lightest blade in the kit.
        secondary_attack=SecondaryAttack.GUARD,
        part_weight_kg=1.40,
        guard_cooldown_sec=1.60,
    ),
    Manufacturer.BANDIT: ManufacturerSpec(
        manufacturer=Manufacturer.BANDIT,
        archetype_name="Machete",
        description=(
            "Jury-rigged scrapyard heavy machete. Wide brutal horizontal cleave hitting every "
            "enemy in front of you. Optional incendiary, corrosive, or slag coating."
        ),
        element_policy=ElementPolicy.OPTIONAL,
        allowed_elements=(
            Element.NONE,
            Element.FIRE,
            Element.CORROSIVE,
            Element.SLAG,
        ),
        base_damage_mult=1.48,
        base_swing_speed=1.35,
        semi_auto_click=False,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.FRONTAL_CLEAVE,
            base_range_uu=285.0,     # 2.85m brawling cleave reach
            arc_degrees=95.0,        # Hits ALL enemies in a 95-degree frontal cone
            pitch_limit_degrees=50.0,
            max_targets=24,
            can_weakspot_crit=False,
            base_crit_bonus=0.0,
        ),
        matching_grip_damage_bonus=0.15,
        matching_grip_speed_bonus=0.08,
        intrinsic_modifiers=(
            StatModifier(StatId.MOMENTUM, ModifierMode.SCALE, 0.25),
        ),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Bandit",
        # Scrapyard steel slab: heavy enough to throw short, flat and mean.
        secondary_attack=SecondaryAttack.THROW,
        part_weight_kg=2.90,
    ),
    Manufacturer.JAKOBS: ManufacturerSpec(
        manufacturer=Manufacturer.JAKOBS,
        archetype_name="Sabre",
        description=(
            "Hand-forged frontier duelist sabre. Strictly non-elemental, high raw steel damage, "
            "massive critical multiplier, and swings as fast as you pull the trigger."
        ),
        element_policy=ElementPolicy.NEVER,
        allowed_elements=(Element.NONE,),
        base_damage_mult=1.40,       # Fastest cadence in the kit: per-hit damage stays in check
        base_swing_speed=2.35,       # Semi-auto cadence cap ("Swings as fast as you pull the trigger")
        semi_auto_click=True,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.SINGLE_TARGET,
            base_range_uu=310.0,     # 3.10m duelist thrust/slash reach
            arc_degrees=12.0,
            pitch_limit_degrees=45.0,
            max_targets=1,
            can_weakspot_crit=True,
            base_crit_bonus=1.00,    # +100% critical hit bonus
        ),
        matching_grip_damage_bonus=0.15,
        matching_grip_speed_bonus=0.10,
        intrinsic_modifiers=(),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Jakobs",
        # Solid frontier steel with a weighted pommel: guard-tuned, mid weight.
        secondary_attack=SecondaryAttack.GUARD,
        part_weight_kg=2.10,
        guard_cooldown_sec=2.20,
    ),
    Manufacturer.MALIWAN: ManufacturerSpec(
        manufacturer=Manufacturer.MALIWAN,
        archetype_name="Phaseblade",
        description=(
            "Artisanal hard-light elemental phaseblade. Always elemental, sweeping gracefully "
            "through all enemies in a wide arc with superior elemental chance and DoT."
        ),
        element_policy=ElementPolicy.ALWAYS_ELEMENTAL,
        allowed_elements=(
            Element.FIRE,
            Element.SHOCK,
            Element.CORROSIVE,
            Element.SLAG,
        ),
        base_damage_mult=1.12,
        base_swing_speed=1.65,
        semi_auto_click=False,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.FRONTAL_CLEAVE,
            base_range_uu=325.0,     # 3.25m sweeping energy arc
            arc_degrees=110.0,       # Wide 110-degree sweeping cleave
            pitch_limit_degrees=50.0,
            max_targets=24,
            can_weakspot_crit=False,
            base_crit_bonus=0.0,
        ),
        matching_grip_damage_bonus=0.10,
        matching_grip_speed_bonus=0.10,
        intrinsic_modifiers=(
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.40),
            StatModifier(StatId.STATUS_DAMAGE, ModifierMode.SCALE, 0.35),
        ),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Maliwan",
        # Hard-light emitter: almost massless, and Maliwan grants no secondary attack at all.
        secondary_attack=SecondaryAttack.NONE,
        part_weight_kg=1.20,
    ),
    Manufacturer.TORGUE: ManufacturerSpec(
        manufacturer=Manufacturer.TORGUE,
        archetype_name="Blast-Cleaver",
        description=(
            "Rocket-assisted explosive heavy cleaver. Slow, earth-shaking swings that cleave "
            "every enemy in front of you and detonate on impact for bonus explosive splash!"
        ),
        element_policy=ElementPolicy.ALWAYS_EXPLOSIVE,
        allowed_elements=(Element.EXPLOSIVE,),
        base_damage_mult=2.20,
        base_swing_speed=0.92,
        semi_auto_click=False,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.FRONTAL_CLEAVE,
            base_range_uu=275.0,     # 2.75m heavy slam cleave
            arc_degrees=85.0,
            pitch_limit_degrees=55.0,
            max_targets=24,
            can_weakspot_crit=False,
            base_crit_bonus=0.0,
            splash_radius_uu=180.0,  # 1.8m explosive shockwave around struck targets
            splash_damage_pct=0.50,
        ),
        matching_grip_damage_bonus=0.16,
        matching_grip_speed_bonus=0.08,
        intrinsic_modifiers=(
            StatModifier(StatId.MOMENTUM, ModifierMode.SCALE, 0.60),
        ),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Torgue",
        # Rocket-assisted slab: the heaviest thing you can hurl.
        secondary_attack=SecondaryAttack.THROW,
        part_weight_kg=3.80,
    ),
}

# Only GUARD manufacturers have hand-tuned recovery values. `core.secondary` reads this
# table (and `GUARD_COOLDOWN_DEFAULT_SEC`) when it solves a weapon's guard cooldown.
GUARD_COOLDOWN_BY_MANUFACTURER: dict[Manufacturer, float] = {
    maker: spec.guard_cooldown_sec
    for maker, spec in MANUFACTURERS.items()
    if spec.secondary_attack is SecondaryAttack.GUARD
}


# ---------------------------------------------------------------------------
# Manufacturer data-side logic
# ---------------------------------------------------------------------------

def manufacturer_spec(manufacturer: Manufacturer) -> ManufacturerSpec:
    """Look up the identity for `manufacturer` (KeyError on an unknown maker)."""
    return MANUFACTURERS[manufacturer]


def allowed_elements_for(manufacturer: Manufacturer) -> tuple[Element, ...]:
    """The element types this manufacturer's blades are allowed to roll."""
    return MANUFACTURERS[manufacturer].allowed_elements


def secondary_attack_for(manufacturer: Manufacturer) -> SecondaryAttack:
    """The manufacturer's RMB attack identity (`NONE` for Maliwan)."""
    spec = MANUFACTURERS.get(manufacturer)
    return spec.secondary_attack if spec is not None else SecondaryAttack.NONE


def guard_cooldown_base_sec(manufacturer: Manufacturer) -> float:
    """Base Guard recovery for a blade of this manufacturer."""
    return GUARD_COOLDOWN_BY_MANUFACTURER.get(manufacturer, GUARD_COOLDOWN_DEFAULT_SEC)
