"""Gun-scaled card stat evaluation & melee-bonus-stacked attack damage math."""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Iterable

from .enums import Element, Manufacturer, ModifierMode, Rarity, SecondaryAttack, StatId, TargetingMode
from .parts import (
    MeleeWeaponInstance,
    PlayerMeleeContext,
    StatModifier,
    is_matching_grip,
    resolve_parts,
    resolve_weapon_name,
)
from .secondary import (
    GUARD_PARRY_WINDOW_SEC,
    build_throw_flight_plan,
    compute_guard_cooldown_sec,
    compute_throw_weight_kg,
    guard_effective_reduction_pct,
)

# Borderlands 2 exponential level scaling curve: 1.13^GameStage.
# Vanilla pistol base constant is ~18.0; melee weapon baseline scales ~2.65x higher per swing
# to reward close-quarters risk while staying cleanly comparable to shotguns/snipers on cards.
BL2_LEVEL_GROWTH: float = 1.13
GUN_DAMAGE_L0_CONSTANT: float = 18.0
MELEE_WEAPON_TYPE_DAMAGE_SCALAR: float = 2.65
UU_PER_METER: float = 100.0
ROID_REFERENCE_SWING_SPEED: float = 1.50  # Normalizes flat Roid shield bonuses across fast/slow blades

# Swing-speed / per-hit damage budget.
# Fast blades (Jakobs Sabre 2.35/s) used to out-DPS everything while also being the safest to whiff,
# so per-hit damage now trades against the blade's *final* swing speed. The exponent stays below 1.0
# on purpose: fast blades still win slightly on sustained DPS, just no longer by a landslide.
SWING_SPEED_DAMAGE_REFERENCE: float = 1.50  # swings/sec that keeps 100% of its card damage
SWING_SPEED_DAMAGE_EXPONENT: float = 0.55   # 0 = no speed penalty (old behaviour), 1 = flat DPS

# Every Melee Arsenal weapon rolls a flat incoming-damage reduction (Blade Guard) while equipped.
# It now scales with the weapon's level/stage AND its parts:
#   * Lv 1 (no OP levels)  -> 30% floor, ~30-34% with parts
#   * Lv 80 + OP10         -> 61% floor, up to the 70% cap with good parts
#   * Unique rarities (Legendary / Seraph / Pearlescent / Effervescent) may pass the 70% cap.
DAMAGE_REDUCTION_MIN_PCT: int = 30
DAMAGE_REDUCTION_MAX_PCT: int = 70
DAMAGE_REDUCTION_MIN_STAGE: int = 1
DAMAGE_REDUCTION_MAX_STAGE: int = 90        # Lv 80 + OP1..OP10
DAMAGE_REDUCTION_FLOOR_AT_MIN_STAGE: float = 30.0
DAMAGE_REDUCTION_FLOOR_AT_MAX_STAGE: float = 61.0
DAMAGE_REDUCTION_PART_SPAN_AT_MIN_STAGE: float = 4.0
DAMAGE_REDUCTION_PART_SPAN_AT_MAX_STAGE: float = 9.0
DAMAGE_REDUCTION_UNIQUE_HEADROOM_PCT: int = 6

UNIQUE_RARITIES: frozenset[Rarity] = frozenset(
    {Rarity.LEGENDARY, Rarity.SERAPH, Rarity.PEARLESCENT, Rarity.EFFERVESCENT}
)

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

    # Defensive perk: level- & part-scaled incoming damage reduction while equipped (30-70%+)
    damage_reduction_pct: int

    # RMB / Left Trigger secondary attack (Bandit & Torgue throw, Hyperion & Jakobs guard,
    # Maliwan none). Throw values describe the ballistic arc its weight produces; guard values
    # describe the braced mitigation window and its part-driven cooldown.
    secondary_attack: SecondaryAttack = SecondaryAttack.NONE
    throw_weight_kg: float = 0.0
    throw_range_m: float = 0.0
    throw_arc_height_m: float = 0.0
    throw_flight_time_sec: float = 0.0
    guard_cooldown_sec: float = 0.0
    guard_parry_window_sec: float = 0.0
    guard_reduction_pct: int = 0


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


def damage_reduction_level_scale(game_stage: int) -> float:
    """0.0 at Lv 1 (no OP levels) -> 1.0 at Lv 80 + OP10 (`game_stage` 90)."""
    stage = max(DAMAGE_REDUCTION_MIN_STAGE, int(game_stage or DAMAGE_REDUCTION_MIN_STAGE))
    span = max(1, DAMAGE_REDUCTION_MAX_STAGE - DAMAGE_REDUCTION_MIN_STAGE)
    return min(1.0, max(0.0, (stage - DAMAGE_REDUCTION_MIN_STAGE) / float(span)))


def compute_swing_speed_damage_scale(swing_speed: float) -> float:
    """Per-hit damage multiplier that trades raw damage against cadence.

    At `SWING_SPEED_DAMAGE_REFERENCE` (1.50 swings/sec) this is exactly 1.0. Faster blades lose a
    little card damage per hit, slower blades gain a little — so a Jakobs Sabre (2.35/s) no longer
    out-DPSes a Torgue Blast-Cleaver (0.92/s) by a wide margin while also being the easiest to land.
    """
    speed = max(0.05, float(swing_speed))
    scale = (SWING_SPEED_DAMAGE_REFERENCE / speed) ** SWING_SPEED_DAMAGE_EXPONENT
    return min(1.35, max(0.60, scale))


def compute_damage_reduction_pct(weapon: MeleeWeaponInstance) -> int:
    """Roll this weapon's flat incoming-damage reduction (deterministic, level + part scaled).

    The floor climbs from `30%` at Lv 1 to `61%` at Lv 80 + OP10, and the weapon's construction
    (Blade + Grip + Accessory + Element + rarity) decides where inside the remaining headroom it
    lands — up to the `70%` cap, or past it for unique rarities. Everything is derived from the same
    `MeleeWeaponInstance` the item card, `melee_dump_held`, and the live
    `WillowPlayerPawn:TakeDamage` hook all read, so they always agree for a given blade.
    """
    seed = "|".join(
        (
            weapon.parts.blade_id,
            weapon.parts.grip_id,
            weapon.parts.accessory_id,
            weapon.parts.element_id,
            weapon.rarity.name,
        )
    )
    digest = zlib.crc32(seed.encode("utf-8"))
    part_fraction = (digest % 1000) / 999.0

    t = damage_reduction_level_scale(weapon.game_stage)
    floor_pct = DAMAGE_REDUCTION_FLOOR_AT_MIN_STAGE + (
        DAMAGE_REDUCTION_FLOOR_AT_MAX_STAGE - DAMAGE_REDUCTION_FLOOR_AT_MIN_STAGE
    ) * t
    part_span = DAMAGE_REDUCTION_PART_SPAN_AT_MIN_STAGE + (
        DAMAGE_REDUCTION_PART_SPAN_AT_MAX_STAGE - DAMAGE_REDUCTION_PART_SPAN_AT_MIN_STAGE
    ) * t

    pct = floor_pct + part_span * part_fraction
    cap = DAMAGE_REDUCTION_MAX_PCT
    if weapon.rarity in UNIQUE_RARITIES:
        # Unique blades get a small level-scaled bonus that can carry them past the normal 70% cap
        # (it fades out at low levels so an early legendary is not a free 40%+ guard).
        headroom = DAMAGE_REDUCTION_UNIQUE_HEADROOM_PCT * t
        pct += headroom
        cap += DAMAGE_REDUCTION_UNIQUE_HEADROOM_PCT
    return int(max(0, min(cap, round(pct))))


def apply_incoming_damage_reduction(damage: float, damage_reduction_pct: float) -> float:
    """Apply a Melee Weapon's flat damage reduction to an incoming damage amount.

    `damage_reduction_pct` is the percentage of the hit removed (30-70+), so 100 damage with 60%
    reduction becomes 40. Non-positive damage or reduction is returned untouched.
    """
    if damage <= 0.0 or damage_reduction_pct <= 0.0:
        return damage
    pct = min(100.0, float(damage_reduction_pct))
    return damage * (1.0 - pct / 100.0)


def apply_guard_damage_reduction(damage: float, damage_reduction_pct: float) -> float:
    """Braced (RMB Guard) mitigation: apply the blade's card Damage Reduction twice.

    The card's `Damage Reduction NN%` is already the passive equipped perk; holding Guard on a
    Hyperion/Jakobs blade layers it again, exactly like `(damage * card DR) * card DR`. At a 60%
    card roll a 1,000 damage hit becomes `1,000 * 0.4 * 0.4 = 160` while bracing.
    """
    guarded = apply_incoming_damage_reduction(damage, damage_reduction_pct)
    return apply_incoming_damage_reduction(guarded, damage_reduction_pct)


def compute_card_stats(weapon: MeleeWeaponInstance) -> ComputedCardStats:
    """Compute exact item card and runtime combat stats for a `MeleeWeaponInstance`."""
    resolved = resolve_parts(weapon.parts)
    blade = resolved.blade
    grip = resolved.grip
    accessory = resolved.accessory
    element_part = resolved.element
    maker_spec = resolved.manufacturer

    prefix, title, full_name = resolve_weapon_name(
        weapon.parts.blade_id,
        weapon.parts.grip_id,
        weapon.parts.accessory_id,
        weapon.parts.element_id,
    )

    matching_grip = is_matching_grip(blade, grip)

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

    # 1. Swing Speed (swings/sec) & FireInterval — resolved first because the per-hit damage
    #    budget below trades against the blade's FINAL cadence (matching grip & accessory included).
    swing_speed = max(
        0.35,
        apply_bl2_modifiers(maker_spec.base_swing_speed, all_mods, StatId.SWING_SPEED),
    )
    fire_interval_sec = 1.0 / swing_speed

    # 2. Gun-scaled Base Damage, then the swing-speed trade-off (fast blades hit a little softer)
    level_curve = GUN_DAMAGE_L0_CONSTANT * (BL2_LEVEL_GROWTH ** max(1, weapon.game_stage))
    unmodified_damage = level_curve * MELEE_WEAPON_TYPE_DAMAGE_SCALAR * maker_spec.base_damage_mult
    unspeed_damage = max(1.0, apply_bl2_modifiers(unmodified_damage, all_mods, StatId.BASE_DAMAGE))
    raw_damage = max(1.0, unspeed_damage * compute_swing_speed_damage_scale(swing_speed))
    card_damage = max(1, round(raw_damage))

    # 3. Range (UU & meters)
    range_uu = max(
        120.0,
        apply_bl2_modifiers(maker_spec.cleave_profile.base_range_uu, all_mods, StatId.RANGE_UU),
    )
    range_meters = round(range_uu / UU_PER_METER, 2)

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
    if element_part.is_dot_element:
        base_dot = level_curve * MELEE_WEAPON_TYPE_DAMAGE_SCALAR * element_part.dot_dps_ratio
        elem_dps = max(1.0, apply_bl2_modifiers(base_dot, all_mods, StatId.STATUS_DAMAGE))
        proc_chance = min(
            0.95,
            max(0.01, apply_bl2_modifiers(element_part.base_proc_chance, all_mods, StatId.STATUS_CHANCE)),
        )
        show_elem = True
    elif element_part.is_explosive:
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

    # 7. Manufacturer secondary attack (RMB / Left Trigger). Throw arcs are solved from the
    #    weapon's actual mass (blade + grip manufacturer hardware + accessory), guard cooldowns
    #    from its parts. Maliwan deliberately has no secondary attack.
    damage_reduction_pct = compute_damage_reduction_pct(weapon)
    secondary_attack = maker_spec.secondary_attack
    throw_weight_kg = 0.0
    throw_range_m = 0.0
    throw_arc_height_m = 0.0
    throw_flight_time_sec = 0.0
    guard_cooldown_sec = 0.0
    guard_parry_window_sec = 0.0
    guard_reduction_pct = 0
    if secondary_attack == SecondaryAttack.THROW:
        throw_weight_kg = compute_throw_weight_kg(weapon)
        flight = build_throw_flight_plan(throw_weight_kg)
        throw_range_m = flight.range_meters
        throw_arc_height_m = flight.apex_meters
        throw_flight_time_sec = flight.flight_time_sec
    elif secondary_attack == SecondaryAttack.GUARD:
        guard_cooldown_sec = compute_guard_cooldown_sec(weapon)
        guard_parry_window_sec = GUARD_PARRY_WINDOW_SEC
        guard_reduction_pct = guard_effective_reduction_pct(damage_reduction_pct)

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
        damage_reduction_pct=damage_reduction_pct,
        secondary_attack=secondary_attack,
        throw_weight_kg=throw_weight_kg,
        throw_range_m=throw_range_m,
        throw_arc_height_m=throw_arc_height_m,
        throw_flight_time_sec=throw_flight_time_sec,
        guard_cooldown_sec=guard_cooldown_sec,
        guard_parry_window_sec=guard_parry_window_sec,
        guard_reduction_pct=guard_reduction_pct,
    )


def compute_attack_damage(
    card: ComputedCardStats,
    ctx: PlayerMeleeContext | None = None,
    *,
    is_crit: bool = False,
    target_is_slagged: bool = False,
    surface_mult: float = 1.0,
    normalize_roid_by_speed: bool = True,
    damage_scale: float = 1.0,
) -> AttackDamageBreakdown:
    """Stack Player Melee Bonuses + Roid Shield on top of the weapon's base Card Damage.

    Formula:
      weapon_term = card.raw_damage * ctx.gun_or_all_damage_scale
      roid_term   = ctx.roid_flat_damage * (ROID_REFERENCE_SWING_SPEED / card.swing_speed)
      subtotal    = (weapon_term + roid_term) * ctx.melee_damage_scale
      final       = subtotal * crit_mult * slag_mult * surface_mult * damage_scale

    `damage_scale` exists for secondary attacks: a thrown Bandit/Torgue blade strikes with the
    exact same breakdown as its LMB swing (`THROW_DAMAGE_MULTIPLIER` is `1.0`).
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

    scale = max(0.0, float(damage_scale))
    raw_final = pre_melee * melee_mult * crit_mult * slag_mult * max(0.0, surface_mult) * scale
    final_hit = max(1, round(raw_final))

    if card.splash_damage > 0:
        splash_ratio = card.splash_damage / max(1.0, card.raw_damage)
        splash_hit = max(
            1,
            round(pre_melee * splash_ratio * melee_mult * slag_mult * max(0.0, surface_mult) * scale),
        )
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
