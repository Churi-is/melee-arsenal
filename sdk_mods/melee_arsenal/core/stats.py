"""Gun-scaled card stat evaluation & melee-bonus-stacked attack damage math."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .catalog import ACCESSORIES, BLADES, ELEMENTS, GRIPS, MANUFACTURERS, resolve_weapon_name
from .enums import Element, Manufacturer, ModifierMode, Rarity, StatId, TargetingMode
from .schemas import MeleeWeaponInstance, PlayerMeleeContext, StatModifier

# Borderlands 2 exponential level scaling curve: 1.13^GameStage.
# Vanilla pistol base constant is ~18.0; melee weapon baseline scales ~2.65x higher per swing
# to reward close-quarters risk while staying cleanly comparable to shotguns/snipers on cards.
BL2_LEVEL_GROWTH: float = 1.13
GUN_DAMAGE_L0_CONSTANT: float = 18.0
MELEE_WEAPON_TYPE_DAMAGE_SCALAR: float = 2.65
UU_PER_METER: float = 100.0
ROID_REFERENCE_SWING_SPEED: float = 1.50  # Normalizes flat Roid shield bonuses across fast/slow blades

RARITY_DAMAGE_GRADE_BONUS: dict[Rarity, float] = {
    Rarity.COMMON: 0.00,
    Rarity.UNCOMMON: 0.06,
    Rarity.RARE: 0.13,
    Rarity.VERY_RARE: 0.21,
    Rarity.E_TECH: 0.24,
    Rarity.LEGENDARY: 0.30,
    Rarity.SERAPH: 0.30,
    Rarity.PEARLESCENT: 0.34,
    Rarity.EFFERVESCENT: 0.36,
}


def apply_bl2_modifiers(base_value: float, modifiers: Iterable[StatModifier], stat: StatId) -> float:
    """Evaluate Gearbox's 3-stage attribute formula (`PreAdd -> Scale -> PostAdd`).

    Note BL2's asymmetrical `MT_Scale` rule:
    - When net scale `s >= 0`: multiplies by `(1.0 + s)`
    - When net scale `s < 0`:  multiplies by `1.0 / (1.0 - s)` so penalties never hit <= 0.
    """
    pre_add = 0.0
    scale_sum = 0.0
    post_add = 0.0
    for mod in modifiers:
        if mod.stat != stat:
            continue
        if mod.mode == ModifierMode.PRE_ADD:
            pre_add += mod.value
        elif mod.mode == ModifierMode.SCALE:
            scale_sum += mod.value
        elif mod.mode == ModifierMode.POST_ADD:
            post_add += mod.value

    scale_factor = (1.0 + scale_sum) if scale_sum >= 0.0 else (1.0 / (1.0 - scale_sum))
    return (base_value + pre_add) * scale_factor + post_add


@dataclass(frozen=True, slots=True)
class ComputedCardStats:
    """Resolved item-card and weapon-definition stats for a MeleeWeaponInstance."""

    full_name: str
    prefix: str
    title: str
    manufacturer: Manufacturer
    archetype_name: str
    rarity: Rarity
    game_stage: int

    # Core item card stats (always shown on every melee weapon card)
    card_damage: int
    raw_damage: float
    range_uu: float
    range_meters: float
    swing_speed: float            # Swings / second
    fire_interval_sec: float      # 1.0 / swing_speed -> written to WillowWeapon.FireInterval

    # Targeting & crit behavior
    targeting_mode: TargetingMode
    semi_auto_click: bool
    cleave_arc_degrees: float
    pitch_limit_degrees: float
    max_targets: int
    can_weakspot_crit: bool
    crit_bonus_pct: int           # e.g. 75 means +75% critical hit bonus

    # Optional elemental item card stats
    element: Element
    show_elemental_stats: bool
    elemental_dps: float
    elemental_chance_pct: float

    # Explosive / splash & synergies
    splash_radius_uu: float
    splash_damage: int
    matching_grip: bool
    momentum_scale: float


@dataclass(frozen=True, slots=True)
class AttackDamageBreakdown:
    """Detailed breakdown of an actual LMB melee weapon strike in combat."""

    weapon_card_damage: float
    scaled_weapon_component: float
    roid_component: float
    pre_melee_subtotal: float
    player_melee_multiplier: float
    crit_multiplier: float
    slag_multiplier: float
    surface_multiplier: float
    final_hit_damage: int
    splash_hit_damage: int
    effective_proc_chance: float
    effective_dot_dps: float


def compute_card_stats(weapon: MeleeWeaponInstance) -> ComputedCardStats:
    """Compute exact item card and runtime combat stats for a `MeleeWeaponInstance`."""
    blade = BLADES[weapon.parts.blade_id]
    grip = GRIPS[weapon.parts.grip_id]
    accessory = ACCESSORIES[weapon.parts.accessory_id]
    element_part = ELEMENTS[weapon.parts.element_id]
    maker_spec = MANUFACTURERS[blade.manufacturer]

    prefix, title, full_name = resolve_weapon_name(
        weapon.parts.blade_id,
        weapon.parts.grip_id,
        weapon.parts.accessory_id,
        weapon.parts.element_id,
    )

    matching_grip = grip.manufacturer == blade.manufacturer

    all_mods: list[StatModifier] = [
        *maker_spec.intrinsic_modifiers,
        *blade.modifiers,
        *grip.modifiers,
        *accessory.modifiers,
        *element_part.modifiers,
    ]

    # Rarity grade bonus + matching-grip bonus (just like BL2 matching grip bonuses)
    rarity_dmg = RARITY_DAMAGE_GRADE_BONUS.get(weapon.rarity, 0.0)
    if rarity_dmg:
        all_mods.append(StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, rarity_dmg))
    if matching_grip:
        all_mods.append(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, maker_spec.matching_grip_damage_bonus)
        )
        all_mods.append(
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, maker_spec.matching_grip_speed_bonus)
        )

    # 1. Gun-scaled Base Damage
    level_curve = GUN_DAMAGE_L0_CONSTANT * (BL2_LEVEL_GROWTH ** max(1, weapon.game_stage))
    unmodified_damage = level_curve * MELEE_WEAPON_TYPE_DAMAGE_SCALAR * maker_spec.base_damage_mult
    raw_damage = max(1.0, apply_bl2_modifiers(unmodified_damage, all_mods, StatId.BASE_DAMAGE))
    card_damage = max(1, round(raw_damage))

    # 2. Range (UU & meters)
    range_uu = max(
        120.0,
        apply_bl2_modifiers(maker_spec.cleave_profile.base_range_uu, all_mods, StatId.RANGE_UU),
    )
    range_meters = round(range_uu / UU_PER_METER, 2)

    # 3. Swing Speed (swings/sec) & FireInterval
    swing_speed = max(
        0.35,
        apply_bl2_modifiers(maker_spec.base_swing_speed, all_mods, StatId.SWING_SPEED),
    )
    fire_interval_sec = 1.0 / swing_speed

    # 4. Targeting Arc & Critical Bonus
    cleave_arc = maker_spec.cleave_profile.arc_degrees
    if maker_spec.cleave_profile.mode == TargetingMode.FRONTAL_CLEAVE:
        cleave_arc = min(
            175.0,
            max(30.0, apply_bl2_modifiers(cleave_arc, all_mods, StatId.CLEAVE_ARC)),
        )

    crit_bonus = max(
        0.0,
        apply_bl2_modifiers(maker_spec.cleave_profile.base_crit_bonus, all_mods, StatId.CRIT_BONUS),
    )

    # 5. Optional Elemental Stats (Fire, Shock, Corrosive, Slag show DoT + Chance; Explosive shows Explosive element)
    element = element_part.element
    if element in (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG):
        base_dot = level_curve * MELEE_WEAPON_TYPE_DAMAGE_SCALAR * element_part.dot_dps_ratio
        elem_dps = max(1.0, apply_bl2_modifiers(base_dot, all_mods, StatId.STATUS_DAMAGE))
        proc_chance = min(
            0.95,
            max(0.01, apply_bl2_modifiers(element_part.base_proc_chance, all_mods, StatId.STATUS_CHANCE)),
        )
        show_elem = True
    elif element == Element.EXPLOSIVE:
        elem_dps = 0.0
        proc_chance = 1.0
        show_elem = True
    else:
        elem_dps = 0.0
        proc_chance = 0.0
        show_elem = False

    # 6. Splash & Knockback Momentum
    splash_radius = apply_bl2_modifiers(
        maker_spec.cleave_profile.splash_radius_uu, all_mods, StatId.SPLASH_RADIUS
    )
    splash_pct = apply_bl2_modifiers(
        maker_spec.cleave_profile.splash_damage_pct, all_mods, StatId.SPLASH_PCT
    )
    splash_damage = round(raw_damage * splash_pct) if splash_radius > 0.0 and splash_pct > 0.0 else 0
    momentum_scale = max(0.1, apply_bl2_modifiers(1.0, all_mods, StatId.MOMENTUM))

    return ComputedCardStats(
        full_name=full_name,
        prefix=prefix,
        title=title,
        manufacturer=blade.manufacturer,
        archetype_name=maker_spec.archetype_name,
        rarity=weapon.rarity,
        game_stage=weapon.game_stage,
        card_damage=card_damage,
        raw_damage=raw_damage,
        range_uu=round(range_uu, 1),
        range_meters=range_meters,
        swing_speed=round(swing_speed, 2),
        fire_interval_sec=round(fire_interval_sec, 4),
        targeting_mode=maker_spec.cleave_profile.mode,
        semi_auto_click=maker_spec.semi_auto_click,
        cleave_arc_degrees=round(cleave_arc, 1),
        pitch_limit_degrees=maker_spec.cleave_profile.pitch_limit_degrees,
        max_targets=maker_spec.cleave_profile.max_targets,
        can_weakspot_crit=maker_spec.cleave_profile.can_weakspot_crit,
        crit_bonus_pct=round(crit_bonus * 100),
        element=element,
        show_elemental_stats=show_elem,
        elemental_dps=round(elem_dps, 1),
        elemental_chance_pct=round(proc_chance * 100, 1),
        splash_radius_uu=round(splash_radius, 1),
        splash_damage=splash_damage,
        matching_grip=matching_grip,
        momentum_scale=round(momentum_scale, 2),
    )


def compute_attack_damage(
    card: ComputedCardStats,
    ctx: PlayerMeleeContext | None = None,
    *,
    is_crit: bool = False,
    target_is_slagged: bool = False,
    surface_mult: float = 1.0,
    normalize_roid_by_speed: bool = True,
) -> AttackDamageBreakdown:
    """Stack Player Melee Bonuses + Roid Shield on top of the weapon's base Card Damage.

    Formula:
      weapon_term = card.raw_damage * ctx.gun_or_all_damage_scale
      roid_term   = ctx.roid_flat_damage * (ROID_REFERENCE_SWING_SPEED / card.swing_speed)
      subtotal    = (weapon_term + roid_term) * ctx.melee_damage_scale
      final       = subtotal * crit_mult * slag_mult * surface_mult
    """
    ctx = ctx or PlayerMeleeContext()

    weapon_component = card.raw_damage * max(0.0, ctx.gun_or_all_damage_scale)
    if ctx.roid_flat_damage > 0.0:
        speed_norm = (ROID_REFERENCE_SWING_SPEED / card.swing_speed) if normalize_roid_by_speed else 1.0
        roid_component = ctx.roid_flat_damage * speed_norm
    else:
        roid_component = 0.0

    pre_melee = weapon_component + roid_component
    melee_mult = max(0.1, ctx.melee_damage_scale)

    if is_crit and card.can_weakspot_crit:
        # BL2 base weapon crit is 2.0x * (1 + weapon_crit_bonus) + additive skill crit
        crit_mult = 2.0 * (1.0 + (card.crit_bonus_pct / 100.0) + max(0.0, ctx.bonus_crit_damage))
    else:
        crit_mult = 1.0

    if target_is_slagged and card.element != Element.SLAG:
        slag_mult = 3.0 if ctx.is_uvhm else 2.0
    else:
        slag_mult = 1.0

    raw_final = pre_melee * melee_mult * crit_mult * slag_mult * max(0.0, surface_mult)
    final_hit = max(1, round(raw_final))

    if card.splash_damage > 0:
        splash_ratio = card.splash_damage / max(1.0, card.raw_damage)
        splash_hit = max(1, round(pre_melee * splash_ratio * melee_mult * slag_mult * max(0.0, surface_mult)))
    else:
        splash_hit = 0

    eff_chance = min(1.0, (card.elemental_chance_pct / 100.0) * max(0.0, ctx.status_chance_scale))
    eff_dot = card.elemental_dps * max(0.0, ctx.status_damage_scale) * (
        (3.0 if ctx.is_uvhm else 2.0) if (target_is_slagged and card.element != Element.SLAG) else 1.0
    )

    return AttackDamageBreakdown(
        weapon_card_damage=card.raw_damage,
        scaled_weapon_component=round(weapon_component, 2),
        roid_component=round(roid_component, 2),
        pre_melee_subtotal=round(pre_melee, 2),
        player_melee_multiplier=round(melee_mult, 4),
        crit_multiplier=round(crit_mult, 4),
        slag_multiplier=slag_mult,
        surface_multiplier=surface_mult,
        final_hit_damage=final_hit,
        splash_hit_damage=splash_hit,
        effective_proc_chance=round(eff_chance, 4),
        effective_dot_dps=round(eff_dot, 2),
    )
