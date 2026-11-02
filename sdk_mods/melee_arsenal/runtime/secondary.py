"""Live secondary attacks for Melee Weapons: **Throw** (Bandit/Torgue) and **Guard** (Hyperion/Jakobs).

RMB / Left Trigger is free on a Melee Weapon because this mod suppresses the donor pistol's ADS
zoom. This module spends that input on the manufacturer's signature move:

**Throw (Bandit Machete, Torgue Blast-Cleaver)**
   - The blade is launched along the ballistic arc solved by `core.secondary.build_throw_flight_plan`
     from the weapon's real part mass (a Torgue slab lobs short and high, a Bandit blade flies
     flatter and further).
   - Each tick sweeps the arc segment with the weapon's own `CalcWeaponFire` trace, plus a pure
     Python swept-segment check against live hostiles, so a fast blade cannot tunnel past an enemy.
   - A hit deals exactly the normal LMB breakdown at `200%` damage and detonates Torgue splash.
   - After an impact — or once the arc expires without touching anything — the blade rebounds to
     your hand (`THROW_RETURN_SEC`). Primary Fire is locked out for the whole flight.

**Guard (Hyperion Katana, Jakobs Sabre)**
   - Holding RMB braces the blade. The next incoming hit has the blade's rolled card Damage
     Reduction applied twice (`(damage * card DR) * card DR`), then the guard consumes itself and
     starts its part-driven cooldown.
   - A hit inside the first `0.33 s` is a perfect parry: zero damage taken, and melee damage is
     reflected back at the attacker as a forced critical hit (with the vanilla CRITICAL popup).
   - `StopZoom` ends the hold. The hook is enabled defensively and a held guard is force-dropped
     after `GUARD_MAX_HOLD_SEC`, so a missing engine function can never leave you stuck bracing.

Maliwan gets neither attack — RMB stays inert on a Phaseblade.

**Visuals** (`runtime/secondary_vfx.py` + `runtime/combat.py` first-person posing)

   - Throw: the release slash plays, then the first-person hands drop out of frame while a real
     actor carrying the weapon's `SkeletalMesh` + materials flies the solved arc (a Tediore-style
     thrown-weapon actor without Tediore's fuse explosion on Bandit blades), tumbles, and travels
     back from the strike point to your hand on the rebound before it is caught.
   - Guard: the hands rotate into a raised brace with a slow bob for as long as RMB is held.
   - Parry: `PERFECT PARRY!` HUD callout, blade-on-blade clang and a camera jolt, on top of the
     vanilla CRITICAL popup of the reflected hit.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Any

import unrealsdk  # type: ignore[import-not-found]
from mods_base import hook  # type: ignore[import-not-found]
from unrealsdk.hooks import Type, prevent_hooking_direct_calls  # type: ignore[import-not-found]

from ..core.enums import Element, SecondaryAttack
from ..core.schemas import MeleeWeaponInstance
from ..core.secondary import (
    GUARD_MAX_HOLD_SEC,
    GUARD_PARRY_WINDOW_SEC,
    GUARD_RELEASE_DEBOUNCE_SEC,
    THROW_DAMAGE_MULTIPLIER,
    THROW_HAND_EMPTY_DELAY_SEC,
    THROW_HIT_PADDING_UU,
    ThrowFlightPlan,
    build_throw_flight_plan,
    compute_parry_reflect_damage,
    compute_throw_weight_kg,
    point_segment_distance_uu,
)
from ..core.stats import ComputedCardStats, apply_guard_damage_reduction, compute_attack_damage
from . import secondary_vfx
from .log import log_error, log_info
from .resolver import get_local_player_controller, resolve_melee_weapon, unwrap_weapon_object
from .state import STATE

# Trace/impact helpers are shared with the primary-swing engine. Importing `combat` at module
# scope would be circular (combat imports this module for RMB dispatch), so it is pulled in lazily.
def _combat() -> Any:
    from . import combat

    return combat


# ---------------------------------------------------------------------------
# Small object helpers (kept local so this module never depends on combat at import time)
# ---------------------------------------------------------------------------

# UE3 `EObjectFlags` bits that mark an object as destroyed / pending kill.
_DEAD_OBJECT_FLAGS = 0x0000200000000000 | 0x0000000200000000 | 0x8000 | 0x10000


def _is_dead(obj: Any) -> bool:
    if obj is None:
        return True
    try:
        flags = getattr(obj, "ObjectFlags", 0)
        return bool(int(flags) & _DEAD_OBJECT_FLAGS)
    except Exception:
        return False


def _wrap_weak(obj: Any) -> Any:
    if obj is None:
        return None
    wp_cls = getattr(unrealsdk.unreal, "WeakPointer", None) if hasattr(unrealsdk, "unreal") else None
    if wp_cls is not None:
        try:
            return wp_cls(obj)
        except Exception:
            return obj
    return obj


def _deref_weak(ref: Any) -> Any:
    if ref is None:
        return None
    if callable(ref) and not hasattr(ref, "_path_name") and not hasattr(ref, "PlayCustomAnim"):
        try:
            obj = ref()
            return None if _is_dead(obj) else obj
        except Exception:
            return None
    return None if _is_dead(ref) else ref


def _same_object(ref: Any, obj: Any) -> bool:
    if ref is None or obj is None:
        return False
    return _deref_weak(ref) is obj


# ---------------------------------------------------------------------------
# Throw state
# ---------------------------------------------------------------------------

@dataclass
class ThrowState:
    """One blade currently in flight (there is only ever one local player)."""

    wpn_ref: Any
    shooter_ref: Any
    pc_ref: Any
    card: ComputedCardStats
    inst: MeleeWeaponInstance
    plan: ThrowFlightPlan
    origin_xyz: tuple[float, float, float]
    dir_xy: tuple[float, float]
    started_at: float
    last_point: tuple[float, float, float]
    vec_template: Any = None
    phase: str = "outbound"          # "outbound" -> "return"
    return_started_at: float = 0.0
    hit_actor_name: str = ""
    damage_dealt: int = 0
    visual_updates: int = 0


@dataclass
class GuardState:
    """The braced guard currently held by the local player."""

    wpn_ref: Any
    card: ComputedCardStats
    started_at: float
    cooldown_sec: float
    hits_absorbed: int = 0
    parries: int = 0


_THROW: ThrowState | None = None
_GUARD: GuardState | None = None
_GUARD_COOLDOWN_UNTIL: float = 0.0
_OPTIONAL_HOOKS_ENABLED: bool = False


# ---------------------------------------------------------------------------
# Throw
# ---------------------------------------------------------------------------

def _aim_forward(pc: Any, shooter: Any) -> tuple[float, float, float]:
    combat = _combat()
    rot = getattr(pc, "Rotation", None) if pc is not None else None
    if rot is None and shooter is not None:
        rot = getattr(shooter, "Rotation", None)
    return combat._rot_to_forward(rot) if rot is not None else (1.0, 0.0, 0.0)


def _play_throw_sfx(shooter: Any, kind: str = "whoosh") -> None:
    """Loose the blade with the melee swing whoosh (or a meaty impact/rebound cue)."""
    if shooter is None:
        return
    combat = _combat()
    try:
        combat.ensure_combat_resources_cached()
        if kind == "impact":
            ak = combat._CACHED_AKEVENTS.get(combat.AK_BLADE_HIT_FLESH)
        else:
            # "whoosh" (release / catch) and "rebound" (blade turning back) both read as air.
            ak = combat._CACHED_AKEVENTS.get(combat.AK_SWING_WHOOSH)
        if ak is not None:
            shooter.PlayAkEvent(ak)
    except Exception:
        pass


def begin_throw(wpn: Any, inst: MeleeWeaponInstance, card: ComputedCardStats) -> bool:
    """Hurl the held blade (RMB). Returns False if the weapon cannot throw right now."""
    global _THROW
    if card.secondary_attack != SecondaryAttack.THROW:
        return False
    if _THROW is not None:
        return False
    combat = _combat()
    shooter, pc = combat._resolve_shooter_and_pc(wpn)
    if shooter is None or _is_dead(shooter):
        return False

    weight = float(card.throw_weight_kg) or compute_throw_weight_kg(inst)
    plan = build_throw_flight_plan(weight)

    vec_tpl = getattr(shooter, "Location", None)
    eye = (
        float(getattr(vec_tpl, "X", 0.0)),
        float(getattr(vec_tpl, "Y", 0.0)),
        float(getattr(vec_tpl, "Z", 0.0))
        + float(getattr(shooter, "EyeHeight", getattr(shooter, "BaseEyeHeight", 64.0))),
    )
    try:
        with prevent_hooking_direct_calls():
            start = wpn.InstantFireStartTrace()
        if start is not None:
            eye = (float(start.X), float(start.Y), float(start.Z))
    except Exception:
        pass

    aim = _aim_forward(pc, shooter)
    horiz = math.hypot(aim[0], aim[1])
    dir_xy = (aim[0] / horiz, aim[1] / horiz) if horiz > 1e-4 else (1.0, 0.0)

    # Launch a few UU forward so the swept trace never starts inside the thrower's own capsule.
    origin = (eye[0] + dir_xy[0] * 8.0, eye[1] + dir_xy[1] * 8.0, eye[2])
    _THROW = ThrowState(
        wpn_ref=_wrap_weak(wpn),
        shooter_ref=_wrap_weak(shooter),
        pc_ref=_wrap_weak(pc),
        card=card,
        inst=inst,
        plan=plan,
        origin_xyz=origin,
        dir_xy=dir_xy,
        started_at=time.monotonic(),
        last_point=origin,
        vec_template=vec_tpl,
    )
    _play_throw_sfx(shooter, "whoosh")
    # A real spinning actor carries the weapon's own mesh along the arc (Tediore-style throw visual).
    secondary_vfx.spawn_thrown_blade(shooter, wpn, origin)
    log_info(
        f"Threw [{card.manufacturer.value} {card.archetype_name}] "
        f"{plan.weight_kg:.2f}kg at {plan.launch_speed_uu_s:.0f} uu/s, "
        f"{plan.launch_pitch_deg:.1f}° loft -> {plan.range_meters:.1f}m / "
        f"{plan.flight_time_sec:.2f}s arc"
    )
    return True


def is_weapon_thrown(wpn: Any) -> bool:
    """True while THIS blade is airborne (Primary Fire is locked out until it rebounds)."""
    st = _THROW
    if st is None or wpn is None:
        return False
    return _same_object(st.wpn_ref, wpn)


def is_any_weapon_thrown() -> bool:
    return _THROW is not None


def thrown_weapon() -> Any:
    return _deref_weak(_THROW.wpn_ref) if _THROW is not None else None


def _throw_point(st: ThrowState, elapsed: float) -> tuple[float, float, float]:
    forward, height = st.plan.offset_at(elapsed)
    return (
        st.origin_xyz[0] + st.dir_xy[0] * forward,
        st.origin_xyz[1] + st.dir_xy[1] * forward,
        st.origin_xyz[2] + height,
    )


def _nearest_victim_on_segment(
    shooter: Any, start: tuple[float, float, float], end: tuple[float, float, float]
) -> tuple[Any, tuple[float, float, float]] | None:
    """Pure-Python swept-segment check so a fast blade can never tunnel between limbs."""
    combat = _combat()
    try:
        candidates, pawn_map = combat._snapshot_cleave_candidates(shooter)
    except Exception:
        return None

    best: tuple[float, Any, tuple[float, float, float]] | None = None
    for cand in candidates:
        chest = cand.chest_point
        point = (chest.x, chest.y, chest.z)
        dist = point_segment_distance_uu(point, start, end)
        if dist > float(cand.collision_radius) + THROW_HIT_PADDING_UU:
            continue
        pawn = pawn_map.get(cand.target_id)
        if pawn is None:
            continue
        # Prefer whichever enemy the blade reaches first along the sweep.
        along = math.sqrt(
            (point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2 + (point[2] - start[2]) ** 2
        )
        if best is None or along < best[0]:
            best = (along, pawn, point)
    if best is None:
        return None
    return best[1], best[2]


def _begin_return(st: ThrowState, reason: str) -> None:
    st.phase = "return"
    st.return_started_at = time.monotonic()
    _play_throw_sfx(_deref_weak(st.shooter_ref), "rebound")
    log_info(
        f"Thrown [{st.card.manufacturer.value} {st.card.archetype_name}] rebounding ({reason})"
        + (f" after {st.damage_dealt:,} damage on {st.hit_actor_name}" if st.damage_dealt else "")
    )


def _finish_throw() -> None:
    global _THROW
    if _THROW is None:
        return
    _play_throw_sfx(_deref_weak(_THROW.shooter_ref), "whoosh")
    debug = secondary_vfx.thrown_blade_debug_info()
    secondary_vfx.destroy_thrown_blade()
    combat = _combat()
    try:
        combat.clear_first_person_pose(_deref_weak(_THROW.pc_ref), combat.POSE_THROWN)
    except Exception:
        pass
    updates = _THROW.visual_updates
    _THROW = None
    log_info(
        f"Thrown blade caught — Primary Fire unlocked ({updates} flight frames, "
        f"engine position {debug.get('engine_xyz', 'unreadable')})."
    )


def _select_throw_damage_type(wpn: Any, card: ComputedCardStats) -> Any:
    """Roll the thrown blade's elemental proc exactly like an LMB hit."""
    combat = _combat()
    roll = False
    if card.element in (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG):
        chance = min(1.0, card.elemental_chance_pct / 100.0)
        roll = random.random() < chance
    if roll:
        dt_def = combat._CACHED_DMG_TYPES_PROC.get(card.element)
        _set_status_chance(wpn, 50.0)
    else:
        dt_def = combat._CACHED_DMG_TYPES_NODOT.get(card.element) or combat._CACHED_DMG_TYPES_PROC.get(
            card.element
        )
        _set_status_chance(wpn, 0.0)
    return dt_def


def _set_status_chance(wpn: Any, value: float) -> None:
    for attr_n in (
        "BaseStatusEffectChanceModifierBaseValue",
        "BaseStatusEffectChanceModifier",
        "StatusEffectChanceModifierBaseValue",
        "StatusEffectChanceModifier",
    ):
        try:
            setattr(wpn, attr_n, float(value))
        except Exception:
            pass


def _apply_throw_impact(
    st: ThrowState,
    victim: Any | None,
    hit_xyz: tuple[float, float, float] | None,
) -> None:
    """Deal the thrown blade's 200% LMB hit (and Torgue splash), then start the rebound."""
    combat = _combat()
    wpn = _deref_weak(st.wpn_ref)
    shooter = _deref_weak(st.shooter_ref)
    pc = _deref_weak(st.pc_ref)
    if wpn is None or shooter is None:
        _begin_return(st, "weapon lost")
        return

    try:
        combat.ensure_combat_resources_cached()
    except Exception:
        pass

    ctx = combat.read_player_melee_context(shooter, pc)
    try:
        breakdown = compute_attack_damage(
            st.card, ctx, is_crit=False, damage_scale=THROW_DAMAGE_MULTIPLIER
        )
    except Exception as exc:
        log_error(f"Thrown damage computation failed: {exc!r}")
        _begin_return(st, "damage error")
        return

    hit_point = hit_xyz or st.last_point
    if victim is not None:
        dt_def = _select_throw_damage_type(wpn, st.card)
        try:
            with prevent_hooking_direct_calls():
                shooter.Behavior_CauseDamage(
                    Target=victim,
                    DamageAmount=float(breakdown.final_hit_damage),
                    Momentum=12000.0 * max(0.5, st.card.momentum_scale),
                    DamageType=combat._CACHED_BULLET_DMG_CLS,
                    DamageTypeDefinition=dt_def,
                    ImpactDefinition=combat._CACHED_MELEE_IMPACT,
                    bCanDamageFriendlies=False,
                    DamageCauser=wpn,
                )
            st.damage_dealt = int(breakdown.final_hit_damage)
            st.hit_actor_name = str(getattr(victim, "Name", "unknown"))
        except Exception as exc:
            log_error(f"Thrown Behavior_CauseDamage failed on {victim}: {exc!r}")
        finally:
            _set_status_chance(wpn, 1.0)
    else:
        st.hit_actor_name = "world"

    # Impact sparks & blood at the exact strike point.
    vec_tpl = st.vec_template
    if vec_tpl is not None and hit_point is not None:
        try:
            with prevent_hooking_direct_calls():
                wpn.PlayImpactEffects(
                    combat._make_vec_from(vec_tpl, *st.last_point),
                    combat._make_vec_from(vec_tpl, *hit_point),
                )
        except Exception:
            pass

    # Torgue Blast-Cleaver: the thrown slab detonates on any impact (its 200% splash).
    if st.card.splash_radius_uu > 0.0 and breakdown.splash_hit_damage > 0 and vec_tpl is not None:
        try:
            sx, sy, sz = float(vec_tpl.X), float(vec_tpl.Y), float(vec_tpl.Z)
            offset_vec = combat._make_vec_from(
                vec_tpl,
                hit_point[0] - sx,
                hit_point[1] - sy,
                hit_point[2] - sz,
            )
            with prevent_hooking_direct_calls():
                shooter.Behavior_CauseRadiusDamage(
                    Radius=float(st.card.splash_radius_uu),
                    DamageAmount=float(breakdown.splash_hit_damage),
                    bFullDamage=True,
                    Momentum=16000.0,
                    DamageType=combat._CACHED_GRENADE_DMG_CLS or combat._CACHED_BULLET_DMG_CLS,
                    DamageTypeDefinition=combat._CACHED_DMG_TYPES_PROC.get(Element.EXPLOSIVE),
                    ImpactDefinition=combat._CACHED_MELEE_IMPACT,
                    bCanDamageFriendlies=False,
                    Offset=offset_vec,
                    DamageCauser=wpn,
                    bSkipTraceTest=False,
                )
            combat._spawn_torgue_explosion_vfx_sfx(
                shooter=shooter,
                pc=pc,
                vec_tpl=vec_tpl,
                eye_xyz=st.origin_xyz,
                blast_xyz=hit_point,
            )
        except Exception as exc:
            log_error(f"Thrown splash failed: {exc!r}")

    # The rebound starts from wherever the blade struck, so the flying actor visibly returns
    # from the target to your hand instead of teleporting backwards.
    st.last_point = hit_point
    secondary_vfx.update_thrown_blade(hit_point)
    _play_throw_sfx(shooter, "impact")
    try:
        combat.play_thrown_impact_feedback(
            pc, shooter, shake=st.card.splash_radius_uu <= 0.0
        )
    except Exception:
        pass

    _begin_return(st, "impact")


def tick_throw() -> None:
    """Advance the airborne blade one frame (arc sweep, hit test, rebound timer)."""
    global _THROW
    st = _THROW
    if st is None:
        return
    now = time.monotonic()
    wpn = _deref_weak(st.wpn_ref)
    shooter = _deref_weak(st.shooter_ref)
    if wpn is None or shooter is None or _is_dead(shooter):
        secondary_vfx.destroy_thrown_blade()
        _THROW = None
        _clear_pose("thrown")
        return

    if st.phase == "return":
        u = 1.0
        if st.plan.return_time_sec > 0.0:
            u = min(1.0, max(0.0, (now - st.return_started_at) / st.plan.return_time_sec))
        secondary_vfx.update_thrown_blade(
            secondary_vfx.lerp_xyz(st.last_point, st.origin_xyz, u)
        )
        if u >= 1.0:
            _finish_throw()
        return

    elapsed = now - st.started_at
    if elapsed >= st.plan.flight_time_sec:
        _begin_return(st, "arc expired")
        return

    point = _throw_point(st, elapsed)
    combat = _combat()

    # First-person sell: the release slash plays, then the hands drop out of frame so the blade is
    # visibly gone from them until it is caught.
    if elapsed >= THROW_HAND_EMPTY_DELAY_SEC:
        if combat.active_first_person_pose() != combat.POSE_THROWN:
            combat.apply_first_person_pose(_deref_weak(st.pc_ref), combat.POSE_THROWN)
        secondary_vfx.hide_held_blade()
    secondary_vfx.update_thrown_blade(point)
    st.visual_updates += 1
    hit_actor, hit_xyz, _bone = combat._run_weapon_trace(wpn, st.vec_template, st.last_point, point)

    if hit_actor is not None:
        if combat._is_valid_melee_victim(shooter, hit_actor):
            _apply_throw_impact(st, hit_actor, hit_xyz or point)
        else:
            _apply_throw_impact(st, None, hit_xyz or point)
        return

    swept = _nearest_victim_on_segment(shooter, st.last_point, point)
    if swept is not None:
        _apply_throw_impact(st, swept[0], swept[1])
        return

    st.last_point = point


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

def guard_cooldown_remaining() -> float:
    return max(0.0, _GUARD_COOLDOWN_UNTIL - time.monotonic())


def is_guarding(wpn: Any = None) -> bool:
    st = _GUARD
    if st is None:
        return False
    if wpn is None:
        return True
    return _same_object(st.wpn_ref, wpn)


def try_begin_guard(wpn: Any, inst: MeleeWeaponInstance, card: ComputedCardStats) -> bool:
    """Raise the guard on RMB press. Returns False while the part-driven cooldown is running."""
    global _GUARD
    if card.secondary_attack != SecondaryAttack.GUARD:
        return False
    if is_guarding(wpn):
        # Some engine builds repeat `StartZoom` while the button is held — never re-open the
        # parry window on a guard that is already up.
        return True
    remaining = guard_cooldown_remaining()
    if remaining > 0.0:
        if STATE.debug_attack_log:
            log_info(f"Guard on cooldown ({remaining:.2f}s left).")
        return True

    cooldown = float(card.guard_cooldown_sec) or 2.0
    _GUARD = GuardState(
        wpn_ref=_wrap_weak(wpn),
        card=card,
        started_at=time.monotonic(),
        cooldown_sec=cooldown,
    )
    _play_guard_sfx(wpn, card)
    # Visible brace: the hands (and the blade attached to them) lift into a guard posture.
    try:
        combat = _combat()
        combat.apply_first_person_pose(get_local_player_controller(), combat.POSE_GUARD)
    except Exception:
        pass
    log_info(
        f"Guard raised [{card.manufacturer.value} {card.archetype_name}] — "
        f"{card.guard_reduction_pct}% braced DR, {GUARD_PARRY_WINDOW_SEC:.2f}s parry window, "
        f"{cooldown:.2f}s cooldown"
    )
    return True


def _play_guard_sfx(wpn: Any, card: ComputedCardStats) -> None:
    """A short blade-readiness cue so raising the guard is audible."""
    combat = _combat()
    shooter = None
    try:
        shooter = getattr(wpn, "Instigator", None) or getattr(wpn, "Owner", None)
        combat.ensure_combat_resources_cached()
        ak = combat._CACHED_AKEVENTS.get(combat.AK_SWING_WHOOSH)
        if ak is not None and shooter is not None:
            shooter.PlayAkEvent(ak)
    except Exception:
        pass


def end_guard(
    wpn: Any = None, *, start_cooldown: bool = True, respect_debounce: bool = False
) -> None:
    """Drop the guard (RMB release, weapon swap, death, or a consumed block)."""
    global _GUARD, _GUARD_COOLDOWN_UNTIL
    st = _GUARD
    if st is None:
        return
    if wpn is not None and not _same_object(st.wpn_ref, wpn):
        return
    # A same-frame `StartZoom` + `StopZoom` pair is engine noise, not a real release.
    if respect_debounce and (time.monotonic() - st.started_at) < GUARD_RELEASE_DEBOUNCE_SEC:
        return
    _GUARD = None
    if start_cooldown:
        _GUARD_COOLDOWN_UNTIL = time.monotonic() + st.cooldown_sec
    try:
        combat = _combat()
        combat.clear_first_person_pose(None, combat.POSE_GUARD)
    except Exception:
        pass
    log_info(
        f"Guard dropped [{st.card.manufacturer.value} {st.card.archetype_name}] — "
        + (f"cooldown {st.cooldown_sec:.2f}s" if start_cooldown else "no cooldown")
    )


def _path_of(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        return str(obj._path_name())
    except Exception:
        return str(getattr(obj, "Name", "") or "")


def _damage_is_melee(args: Any) -> bool:
    """Best-effort melee-source detection for the parry reflection."""
    if args is None:
        return False
    for field_name in ("DamageType", "DamageTypeDefinition"):
        path = _path_of(getattr(args, field_name, None)).lower()
        if "melee" in path:
            return True
    causer = getattr(args, "DamageCauser", None)
    if causer is not None:
        if bool(getattr(causer, "bMeleeWeapon", False)):
            return True
        wt = getattr(causer, "WeaponTypeDefinition", None)
        if "melee" in str(getattr(wt, "Typename", "") or "").lower():
            return True
        if "melee" in _path_of(getattr(causer, "DefinitionData", None)).lower():
            return True
    return False


def _melee_attacker(args: Any) -> Any:
    """Resolve the pawn/controller that owns an incoming hit (for parry reflection)."""
    if args is None:
        return None
    for field_name in ("EventInstigator", "InstigatedBy", "Instigator", "DamageInstigator"):
        try:
            ctrl = getattr(args, field_name, None)
        except Exception:
            ctrl = None
        if ctrl is None:
            continue
        pawn = getattr(ctrl, "Pawn", None)
        if pawn is not None:
            return pawn
        if hasattr(ctrl, "Location"):
            return ctrl  # already a pawn
    causer = getattr(args, "DamageCauser", None)
    for field_name in ("Instigator", "Owner"):
        try:
            owner = getattr(causer, field_name, None)
        except Exception:
            owner = None
        if owner is not None:
            return owner
    return None


def _reflect_parry(pawn: Any, st: GuardState, args: Any, incoming_damage: float) -> int:
    """Bounce a parried melee blow back with a forced critical hit."""
    attacker = _melee_attacker(args)
    if attacker is None or attacker is pawn or _is_dead(attacker):
        return 0
    combat = _combat()
    # The reflect must read as a critical hit from the blade in your hands, not as a scaling of a
    # weak enemy poke (a 112-card blade used to reflect ~50 damage).
    pc = get_local_player_controller()
    try:
        ctx = combat.read_player_melee_context(pawn, pc)
        blade_crit = compute_attack_damage(st.card, ctx, is_crit=True).final_hit_damage
    except Exception:
        blade_crit = 0
    reflected = compute_parry_reflect_damage(
        incoming_damage, st.card.crit_bonus_pct, blade_crit
    )
    if reflected <= 0:
        return 0
    wpn = _deref_weak(st.wpn_ref)
    try:
        combat.ensure_combat_resources_cached()
        # Tag first: the reflected hit is a crit, so BL2's floating number must say CRITICAL.
        combat._mark_pending_crit_feedback(attacker)
        dt_def = combat._CACHED_DMG_TYPES_PROC.get(st.card.element)
        with prevent_hooking_direct_calls():
            pawn.Behavior_CauseDamage(
                Target=attacker,
                DamageAmount=float(reflected),
                Momentum=18000.0,
                DamageType=combat._CACHED_BULLET_DMG_CLS,
                DamageTypeDefinition=dt_def,
                ImpactDefinition=combat._CACHED_MELEE_IMPACT,
                bCanDamageFriendlies=False,
                DamageCauser=wpn,
            )
        if wpn is not None:
            try:
                pawn_loc = getattr(pawn, "Location", None)
                att_loc = getattr(attacker, "Location", None)
                wpn.PlayImpactEffects(
                    combat._make_vec_from(
                        pawn_loc,
                        float(getattr(pawn_loc, "X", 0.0)),
                        float(getattr(pawn_loc, "Y", 0.0)),
                        float(getattr(pawn_loc, "Z", 0.0)) + float(getattr(pawn, "EyeHeight", 64.0)),
                    ),
                    combat._make_vec_from(
                        pawn_loc,
                        float(getattr(att_loc, "X", 0.0)),
                        float(getattr(att_loc, "Y", 0.0)),
                        float(getattr(att_loc, "Z", 0.0)) + 40.0,
                    ),
                )
            except Exception:
                pass
        _play_throw_sfx(pawn, "impact")
        try:
            combat.play_parry_feedback(pc, pawn, attacker)
        except Exception:
            pass
        log_info(
            f"PARRY! Reflected {reflected:,} forced-crit damage back at "
            f"{getattr(attacker, 'Name', 'attacker')} (incoming {incoming_damage:,.0f})"
        )
        return reflected
    except Exception as exc:
        log_error(f"Parry reflection failed: {exc!r}")
        return 0


def handle_guard_incoming_damage(pawn: Any, args: Any, func: Any) -> bool:
    """Resolve an incoming hit against the held guard.

    Returns True when this module already consumed the hit (damage zeroed or re-issued mitigated),
    so the caller must `Block` the vanilla call. Returns False to let the normal damage pipeline
    (including the equipped passive Damage Reduction) run untouched.
    """
    st = _GUARD
    if st is None or pawn is None or args is None:
        return False
    pc = get_local_player_controller()
    if pc is None or getattr(pc, "Pawn", None) is not pawn:
        return False

    # Guard only exists while its own blade is still the held weapon.
    wpn = _deref_weak(st.wpn_ref)
    held = getattr(pawn, "Weapon", None)
    resolved = resolve_melee_weapon(held, sync_weapon_props=False)
    guard_wpn = unwrap_weapon_object(wpn) or wpn
    held_wpn = unwrap_weapon_object(held) or held
    if (
        wpn is None
        or resolved is None
        or resolved[1].secondary_attack != SecondaryAttack.GUARD
        or guard_wpn is not held_wpn
    ):
        end_guard(start_cooldown=False)
        return False

    combat = _combat()
    try:
        found = combat._read_take_damage_amount(args)
    except Exception:
        found = None
    if found is None:
        return False
    field_name, damage = found
    if damage <= 0.0:
        return False

    now = time.monotonic()
    parry_window = now - st.started_at <= GUARD_PARRY_WINDOW_SEC

    if parry_window:
        reflected = 0
        if _damage_is_melee(args):
            reflected = _reflect_parry(pawn, st, args, damage)
        st.parries += 1
        st.hits_absorbed += 1
        end_guard()
        log_info(
            f"PERFECT PARRY on {st.card.manufacturer.value} {st.card.archetype_name}: "
            f"{damage:,.0f} incoming -> 0 taken"
            + (f", {reflected:,} reflected" if reflected else " (non-melee hit: no reflection)")
        )
        return True  # block the original call outright: exactly zero damage

    guarded = apply_guard_damage_reduction(damage, st.card.damage_reduction_pct)
    if guarded >= damage:
        return False
    try:
        setattr(args, field_name, guarded)
        with prevent_hooking_direct_calls():
            func(args)
    except Exception as exc:
        log_error(f"Guarded TakeDamage re-call failed: {exc!r}")
        return False

    st.hits_absorbed += 1
    _play_throw_sfx(pawn, "impact")
    end_guard()
    log_info(
        f"Guard absorbed a hit [{st.card.manufacturer.value} {st.card.archetype_name}] "
        f"{st.card.damage_reduction_pct}% x2: {damage:,.0f} -> {guarded:,.0f} incoming"
    )
    return True


def tick_guard() -> None:
    """Auto-drop a guard that outlived its weapon, its owner, or the engine's release event."""
    st = _GUARD
    if st is None:
        return
    now = time.monotonic()
    pc = get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None:
        end_guard(start_cooldown=False)
        return
    held = getattr(pawn, "Weapon", None)
    resolved = resolve_melee_weapon(held, sync_weapon_props=False)
    if resolved is None or resolved[1].secondary_attack != SecondaryAttack.GUARD:
        end_guard(start_cooldown=False)
        return
    # A held-weapon change must drop the brace even when the new blade is also a guard weapon.
    guard_wpn = unwrap_weapon_object(_deref_weak(st.wpn_ref)) or _deref_weak(st.wpn_ref)
    held_wpn = unwrap_weapon_object(held) or held
    if guard_wpn is not None and held_wpn is not None and guard_wpn is not held_wpn:
        log_info("Guard dropped (held weapon changed).")
        end_guard(start_cooldown=False)
        return
    try:
        _combat().update_first_person_pose(pc)
    except Exception:
        pass
    if now - st.started_at >= GUARD_MAX_HOLD_SEC:
        log_info("Guard force-released (held past its maximum brace time).")
        end_guard()


# ---------------------------------------------------------------------------
# Frame driver & lifecycle
# ---------------------------------------------------------------------------

def _clear_pose(pose_kind: str) -> None:
    """Best-effort pose restore that never depends on the throw/guard objects still existing."""
    try:
        combat = _combat()
        combat.clear_first_person_pose(None, getattr(combat, f"POSE_{pose_kind.upper()}"))
    except Exception:
        pass


def _reconcile_secondary_visuals() -> None:
    """Self-heal: no live throw/guard may leave a pose or a flying actor behind.

    A first-person pose is a raw transform on `Arms.SkeletalMesh`, so it must never be able to
    outlive its state (a stuck hand needs a game restart). This runs every frame and clears any
    pose or actor whose owner state has already ended.
    """
    combat = _combat()
    if _THROW is None and secondary_vfx.has_thrown_blade_visual():
        secondary_vfx.destroy_thrown_blade()
    pose = combat.active_first_person_pose()
    if not pose:
        return
    if pose == combat.POSE_THROWN and _THROW is None:
        combat.clear_first_person_pose(None, combat.POSE_THROWN)
    elif pose == combat.POSE_GUARD and _GUARD is None:
        combat.clear_first_person_pose(None, combat.POSE_GUARD)


def tick_secondary_attacks() -> None:
    """Per-frame driver: advance airborne throws and keep guard state honest."""
    try:
        tick_throw()
    except Exception as exc:
        log_error(f"tick_throw failed: {exc!r}")
    try:
        tick_guard()
    except Exception as exc:
        log_error(f"tick_guard failed: {exc!r}")
    try:
        _reconcile_secondary_visuals()
    except Exception as exc:
        log_error(f"secondary visual reconcile failed: {exc!r}")


def describe_secondary_state() -> dict[str, Any]:
    """Human-readable snapshot of the live secondary attack state (console diagnostics)."""
    card: ComputedCardStats | None = None
    pc = get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    resolved = resolve_melee_weapon(getattr(pawn, "Weapon", None), sync_weapon_props=False)
    if resolved is not None:
        card = resolved[1]

    info: dict[str, Any] = {
        "mode": card.secondary_attack.value if card is not None else "none",
        "held": card.full_name if card is not None else "not a Melee Weapon",
    }
    if _THROW is not None:
        info["thrown"] = True
        info["throw_phase"] = _THROW.phase
        info["thrown_visual"] = secondary_vfx.has_thrown_blade_visual()
        info["weight_kg"] = _THROW.plan.weight_kg
        info["range_m"] = _THROW.plan.range_meters
        info["detail"] = (
            f"Throw {_THROW.plan.weight_kg:.2f}kg -> {_THROW.plan.range_meters:.1f}m "
            f"({_THROW.phase})"
        )
    elif _GUARD is not None:
        info["guarding"] = True
        info["guard_cooldown_sec"] = _GUARD.cooldown_sec
        info["detail"] = (
            f"Guarding ({_GUARD.card.guard_reduction_pct}% DR, "
            f"{GUARD_PARRY_WINDOW_SEC:.2f}s parry window)"
        )
    else:
        remaining = guard_cooldown_remaining()
        info["guard_cooldown_remaining_sec"] = round(remaining, 2)
        if remaining > 0.0:
            info["detail"] = f"Guard cooldown {remaining:.2f}s"
        elif card is not None and card.secondary_attack == SecondaryAttack.THROW:
            info["detail"] = (
                f"Throw ready ({card.throw_weight_kg:.2f}kg, {card.throw_range_m:.1f}m arc)"
            )
        elif card is not None and card.secondary_attack == SecondaryAttack.GUARD:
            info["detail"] = (
                f"Guard ready ({card.guard_reduction_pct}% DR, "
                f"{card.guard_cooldown_sec:.2f}s cooldown)"
            )
        else:
            info["detail"] = "No secondary attack"
    return info


def reset_secondary_state() -> None:
    """Map change / weapon reset / mod disable hygiene."""
    global _THROW, _GUARD, _GUARD_COOLDOWN_UNTIL
    _THROW = None
    _GUARD = None
    _GUARD_COOLDOWN_UNTIL = 0.0
    secondary_vfx.destroy_thrown_blade()
    try:
        combat = _combat()
        combat.clear_first_person_pose(None, combat.POSE_GUARD)
        combat.clear_first_person_pose(None, combat.POSE_THROWN)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# RMB release hooks (`StopZoom`)
#
# Zoom is suppressed on Melee Weapons, so these hooks exist purely to learn when the player
# lets go of RMB. They are *not* part of `ALL_HOOKS`: `enable_optional_hooks()` turns them on
# behind a try/except after load, because a build without the function must not break the mod.
# ---------------------------------------------------------------------------

@hook("WillowGame.WillowWeapon:StopZoom", Type.PRE)
def on_melee_weapon_stop_zoom_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """RMB released: drop the braced guard (starts its part-driven cooldown)."""
    try:
        if resolve_melee_weapon(obj, sync_weapon_props=False) is not None:
            end_guard(obj, respect_debounce=True)
    except Exception as exc:
        log_error(f"on_melee_weapon_stop_zoom_pre failed: {exc!r}")
    return None


@hook("WillowGame.WillowWeapon:ClientStopZoom", Type.PRE)
def on_melee_weapon_client_stop_zoom_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Client-side RMB release mirror (some builds only fire this one)."""
    try:
        if resolve_melee_weapon(obj, sync_weapon_props=False) is not None:
            end_guard(obj, respect_debounce=True)
    except Exception as exc:
        log_error(f"on_melee_weapon_client_stop_zoom_pre failed: {exc!r}")
    return None


OPTIONAL_HOOKS = (on_melee_weapon_stop_zoom_pre, on_melee_weapon_client_stop_zoom_pre)


def enable_optional_hooks() -> None:
    """RMB-release hooks are registered with `ALL_HOOKS` at load; this only reports them.

    Previously they were enabled lazily from the frame tick, which meant a build whose tick never
    fired could hold a guard forever: the release was never wired up. Registering them at load
    (unrealsdk accepts a hook name whether or not the UFunction exists) removes that dependency.
    """
    global _OPTIONAL_HOOKS_ENABLED
    if _OPTIONAL_HOOKS_ENABLED:
        return
    _OPTIONAL_HOOKS_ENABLED = True
    log_info(
        "RMB-release hooks registered at load: WillowWeapon:StopZoom / ClientStopZoom "
        "(guard release no longer depends on the frame tick)."
    )


def disable_optional_hooks() -> None:
    global _OPTIONAL_HOOKS_ENABLED
    for hook_fn in OPTIONAL_HOOKS:
        disable = getattr(hook_fn, "disable", None)
        if disable is None:
            continue
        try:
            disable()
        except Exception:
            pass
    _OPTIONAL_HOOKS_ENABLED = False
