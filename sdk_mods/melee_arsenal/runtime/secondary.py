"""Handle the runtime throw and guard attacks bound to RMB / Left Trigger."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from mods_base import hook  # type: ignore[import-not-found]
from unrealsdk.hooks import Type, prevent_hooking_direct_calls  # type: ignore[import-not-found]

from ..core.enums import Element, SecondaryAttack
from ..core.parts.weapon import MeleeWeaponInstance
from ..core.secondary import (
    GUARD_MAX_HOLD_SEC,
    GUARD_PARRY_WINDOW_SEC,
    GUARD_RELEASE_DEBOUNCE_SEC,
    THROW_CATCH_RADIUS_UU,
    THROW_DAMAGE_MULTIPLIER,
    THROW_FALL_PROBE_UU,
    THROW_HAND_EMPTY_DELAY_SEC,
    THROW_HIT_PADDING_UU,
    THROW_MAX_FALL_UU,
    THROW_RETURN_MAX_SEC,
    ThrowFlightPlan,
    build_throw_flight_plan,
    compute_parry_reflect_damage,
    compute_throw_weight_kg,
    point_segment_distance_uu,
)
from ..core.stats import ComputedCardStats, apply_guard_damage_reduction, compute_attack_damage
from . import secondary_vfx
from .log import log_error, log_info
from .object_identity import deref_weak, is_dead, same_uobject, wrap_weak
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

def _same_object(ref: Any, obj: Any) -> bool:
    if ref is None or obj is None:
        return False
    return same_uobject(deref_weak(ref), obj)


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
    # Rebound leg: the blade homes on the player's *current* position, so these track the live
    # flight point rather than a straight lerp back to where the throw started.
    return_point: tuple[float, float, float] = (0.0, 0.0, 0.0)
    last_return_at: float = 0.0
    return_speed_uu_s: float = 0.0
    last_return_reason: str = ""
    arc_probe_logged: bool = False


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
            ak = combat.resources._CACHED_AKEVENTS.get(combat.resources.AK_BLADE_HIT_FLESH)
        else:
            # "whoosh" (release / catch) and "rebound" (blade turning back) both read as air.
            ak = combat.resources._CACHED_AKEVENTS.get(combat.resources.AK_SWING_WHOOSH)
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
    if shooter is None or is_dead(shooter):
        return False

    weight = float(card.throw_weight_kg) or compute_throw_weight_kg(inst)

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
    # Follow the camera: the weapon's loft is added on top of where the player is actually aiming,
    # so looking up lobs the blade up and looking down sends it down (gravity still bends the arc).
    aim_pitch_deg = math.degrees(math.atan2(float(aim[2]), max(1e-4, horiz)))
    plan = build_throw_flight_plan(weight, aim_pitch_deg)

    # Launch a few UU forward so the swept trace never starts inside the thrower's own capsule.
    origin = (eye[0] + dir_xy[0] * 8.0, eye[1] + dir_xy[1] * 8.0, eye[2])
    _THROW = ThrowState(
        wpn_ref=wrap_weak(wpn),
        shooter_ref=wrap_weak(shooter),
        pc_ref=wrap_weak(pc),
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
        f"{plan.launch_pitch_deg:.1f}° launch (aim {aim_pitch_deg:.1f}°) -> "
        f"{plan.range_meters:.1f}m / {plan.flight_time_sec:.2f}s arc"
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
    return deref_weak(_THROW.wpn_ref) if _THROW is not None else None


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


def _player_grab_point(st: ThrowState) -> tuple[float, float, float] | None:
    """Where the returning blade should be caught: the player's *live* position, right now."""
    shooter = deref_weak(st.shooter_ref)
    if shooter is None or is_dead(shooter):
        return None
    loc = getattr(shooter, "Location", None)
    if loc is None:
        return None
    try:
        eye = float(getattr(shooter, "EyeHeight", getattr(shooter, "BaseEyeHeight", 64.0)))
        return (float(loc.X), float(loc.Y), float(loc.Z) + eye * 0.55)
    except Exception:
        return None


def _begin_return(st: ThrowState, reason: str) -> None:
    st.phase = "return"
    st.last_return_reason = reason
    st.return_started_at = time.monotonic()
    st.last_return_at = st.return_started_at
    st.return_point = st.last_point
    _play_throw_sfx(deref_weak(st.shooter_ref), "rebound")
    grab = _player_grab_point(st)
    if grab is not None:
        dist = math.sqrt(sum((grab[i] - st.last_point[i]) ** 2 for i in range(3)))
        # Reach the hand in `THROW_RETURN_SEC`, then keep accelerating if the player runs away.
        st.return_speed_uu_s = max(700.0, dist / max(0.05, st.plan.return_time_sec))
    else:
        st.return_speed_uu_s = 1200.0
    px, py, pz = st.last_point
    log_info(
        f"Thrown [{st.card.manufacturer.value} {st.card.archetype_name}] rebounding ({reason}) "
        f"from ({px:.0f}, {py:.0f}, {pz:.0f}) after {st.plan.flight_time_sec:.2f}s arc"
        + (f", {st.damage_dealt:,} damage on {st.hit_actor_name}" if st.damage_dealt else "")
    )


def _finish_throw() -> None:
    global _THROW
    if _THROW is None:
        return
    _play_throw_sfx(deref_weak(_THROW.shooter_ref), "whoosh")
    debug = secondary_vfx.thrown_blade_debug_info()
    secondary_vfx.destroy_thrown_blade()
    combat = _combat()
    try:
        # Unconditional: whatever posture the hands are in, catching the blade puts them back.
        combat.reset_first_person_pose("thrown blade caught")
    except Exception:
        pass
    updates = _THROW.visual_updates
    _THROW = None
    log_info(
        f"Thrown blade caught — Primary Fire unlocked ({updates} flight frames, "
        f"engine position {debug.get('engine_xyz', 'unreadable')})."
    )


def _set_status_chance(wpn: Any, value: float) -> None:
    _combat().set_weapon_status_chance(wpn, value)


def _select_throw_damage_type(wpn: Any, card: ComputedCardStats, breakdown: Any) -> Any:
    """Use the same player-scaled proc/DoT policy as a primary swing."""
    combat = _combat()
    combat.sync_weapon_status_damage(wpn, breakdown)
    dt_def, _proc = combat.select_attack_damage_type(wpn, card, breakdown)
    return dt_def


def _apply_throw_impact(
    st: ThrowState,
    victim: Any | None,
    hit_xyz: tuple[float, float, float] | None,
) -> None:
    """Deal the thrown blade's normal-strength LMB hit (and Torgue splash), then rebound."""
    combat = _combat()
    wpn = deref_weak(st.wpn_ref)
    shooter = deref_weak(st.shooter_ref)
    pc = deref_weak(st.pc_ref)
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
        dt_def = _select_throw_damage_type(wpn, st.card, breakdown)
        try:
            with prevent_hooking_direct_calls():
                shooter.Behavior_CauseDamage(
                    Target=victim,
                    DamageAmount=float(breakdown.final_hit_damage),
                    Momentum=12000.0 * max(0.5, st.card.momentum_scale),
                    DamageType=combat.resources._CACHED_BULLET_DMG_CLS,
                    DamageTypeDefinition=dt_def,
                    ImpactDefinition=combat.resources._CACHED_MELEE_IMPACT,
                    bCanDamageFriendlies=False,
                    DamageCauser=wpn,
                )
            st.damage_dealt = int(breakdown.final_hit_damage)
            st.hit_actor_name = str(getattr(victim, "Name", "unknown"))
        except Exception as exc:
            log_error(f"Thrown Behavior_CauseDamage failed on {victim}: {exc!r}")
        finally:
            _set_status_chance(wpn, 1.0)
    elif not st.hit_actor_name:
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

    # Torgue Blast-Cleaver: the thrown slab detonates on any impact (its normal splash).
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
                    DamageType=combat.resources._CACHED_GRENADE_DMG_CLS or combat.resources._CACHED_BULLET_DMG_CLS,
                    DamageTypeDefinition=combat.resources._CACHED_DMG_TYPES_PROC.get(Element.EXPLOSIVE),
                    ImpactDefinition=combat.resources._CACHED_MELEE_IMPACT,
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


# A thrown blade spawns in front of the player's face. Anything it "hits" inside this radius is
# the thrower themselves (arms mesh, held weapon, camera-space collision) — not a real impact.
THROW_LAUNCH_GRACE_UU: float = 150.0

# Ignore non-solid actors such as volume brushes, triggers, and emitters during throw traces.
_THROW_GHOST_CLASS_TOKENS = (
    "PhysicsVolume",
    "Trigger",
    "Emitter",
    "PostProcessVolume",
    "LightVolume",
    "Camera",
    "BoundaryVolume",
    "BlockingVolume",
)


def _is_ghost_hit(actor: Any) -> bool:
    """True for non-solid engine actors that must never stop a thrown blade."""
    if actor is None:
        return True
    cls_name = str(getattr(getattr(actor, "Class", None), "Name", "") or "")
    if not cls_name:
        return False
    return any(token in cls_name for token in _THROW_GHOST_CLASS_TOKENS)


def _tick_return(st: ThrowState, now: float) -> None:
    """Homing rebound: chase the player's *current* hand position until it is caught."""
    dt = min(0.10, max(0.0005, now - st.last_return_at))
    st.last_return_at = now
    target = _player_grab_point(st) or st.origin_xyz
    cur = st.return_point
    dx, dy, dz = target[0] - cur[0], target[1] - cur[1], target[2] - cur[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)

    timed_out = (now - st.return_started_at) >= THROW_RETURN_MAX_SEC
    if dist <= THROW_CATCH_RADIUS_UU or timed_out:
        secondary_vfx.update_thrown_blade(target)
        _finish_throw()
        return

    # Aim to close the whole gap in `THROW_RETURN_SEC`; when the player keeps running the gap
    # grows and the blade simply chases faster (Minecraft-trident style).
    speed = min(6000.0, max(700.0, dist / max(0.05, st.plan.return_time_sec)))
    st.return_speed_uu_s = speed
    step = min(dist, speed * dt)
    scale = step / dist
    st.return_point = (cur[0] + dx * scale, cur[1] + dy * scale, cur[2] + dz * scale)
    secondary_vfx.update_thrown_blade(st.return_point)


def _cancel_throw(reason: str) -> None:
    """Abort a throw whose owner/weapon/world epoch changed (map travel, swap, disable)."""
    global _THROW
    if _THROW is None:
        return
    secondary_vfx.destroy_thrown_blade()
    _THROW = None
    _clear_pose("thrown")
    log_info(f"Throw cancelled ({reason}).")


def tick_throw() -> None:
    """Advance the airborne blade one frame (arc sweep, hit test, homing rebound)."""
    global _THROW
    st = _THROW
    if st is None:
        return
    now = time.monotonic()
    wpn = deref_weak(st.wpn_ref)
    shooter = deref_weak(st.shooter_ref)
    if wpn is None or shooter is None or is_dead(shooter):
        _cancel_throw("owner or weapon lost")
        return
    pc = get_local_player_controller() or deref_weak(st.pc_ref)
    current_pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if current_pawn is not None and not same_uobject(current_pawn, shooter):
        _cancel_throw("pawn/map changed")
        return
    held = getattr(current_pawn, "Weapon", None) if current_pawn is not None else None
    if held is not None and not same_uobject(unwrap_weapon_object(held) or held, wpn):
        _cancel_throw("held weapon changed")
        return

    if st.phase == "return":
        _tick_return(st, now)
        return

    elapsed = now - st.started_at
    if elapsed >= st.plan.max_flight_sec:
        _begin_return(st, "flight fail-safe")
        return

    point = _throw_point(st, elapsed)
    combat = _combat()

    # First-person sell: the release slash plays, then the hands drop out of frame so the blade is
    # visibly gone from them until it is caught.
    if elapsed >= THROW_HAND_EMPTY_DELAY_SEC:
        if combat.active_first_person_pose() != combat.POSE_THROWN:
            combat.apply_first_person_pose(deref_weak(st.pc_ref), combat.POSE_THROWN)
        secondary_vfx.hide_held_blade()
    secondary_vfx.update_thrown_blade(point)
    st.visual_updates += 1

    # Past the planned arc the blade is simply still falling: the arc is only a prediction for
    # *flat* ground, so the flight continues (same parabola) and the per-frame trace below finds
    # the real surface. Never snap the blade to a probed ground point here — that teleported it
    # several metres straight down ("it stops in mid-air, then jumps to the ground and returns").
    if elapsed >= st.plan.flight_time_sec and not st.arc_probe_logged:
        st.arc_probe_logged = True
        below = (point[0], point[1], point[2] - THROW_FALL_PROBE_UU)
        ground, ground_xyz, _g_bone = combat._run_weapon_trace(wpn, st.vec_template, point, below)
        where = (
            f"ground at z={ground_xyz[2]:.0f}"
            if ground_xyz is not None
            else "no ground found"
        )
        log_info(
            f"Throw arc ended at ({point[0]:.0f}, {point[1]:.0f}, {point[2]:.0f}) — "
            f"{where} within {THROW_FALL_PROBE_UU:.0f} uu below; still flying until it hits"
        )
    if st.origin_xyz[2] - point[2] >= THROW_MAX_FALL_UU:
        _begin_return(st, "fell out of the world")
        return

    hit_actor, hit_xyz, _bone = combat._run_weapon_trace(wpn, st.vec_template, st.last_point, point)

    if hit_actor is not None:
        hx, hy, hz = hit_xyz or point
        close_to_hand = math.sqrt(
            (hx - st.origin_xyz[0]) ** 2
            + (hy - st.origin_xyz[1]) ** 2
            + (hz - st.origin_xyz[2]) ** 2
        ) < THROW_LAUNCH_GRACE_UU
        if same_uobject(hit_actor, shooter) or _is_ghost_hit(hit_actor) or close_to_hand:
            # The thrower's own body / arms mesh, or a non-solid volume: keep flying.
            st.last_point = point
            return
        if combat._is_valid_melee_victim(shooter, hit_actor):
            _apply_throw_impact(st, hit_actor, hit_xyz or point)
        else:
            if not st.hit_actor_name:
                st.hit_actor_name = str(getattr(hit_actor, "Name", "") or "world")
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
        wpn_ref=wrap_weak(wpn),
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
        ak = combat.resources._CACHED_AKEVENTS.get(combat.resources.AK_SWING_WHOOSH)
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
        # A hit can consume the brace without an RMB release event; always restore the hands.
        _combat().reset_first_person_pose("guard ended")
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
    if attacker is None or same_uobject(attacker, pawn) or is_dead(attacker):
        return 0
    combat = _combat()
    # Include the blade's critical damage so the reflected hit matches the equipped weapon.
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
    wpn = deref_weak(st.wpn_ref)
    try:
        combat.ensure_combat_resources_cached()
        # Tag first: the reflected hit is a crit, so BL2's floating number must say CRITICAL.
        combat._mark_pending_crit_feedback(attacker)
        dt_def = combat.resources._CACHED_DMG_TYPES_PROC.get(st.card.element)
        with prevent_hooking_direct_calls():
            pawn.Behavior_CauseDamage(
                Target=attacker,
                DamageAmount=float(reflected),
                Momentum=18000.0,
                DamageType=combat.resources._CACHED_BULLET_DMG_CLS,
                DamageTypeDefinition=dt_def,
                ImpactDefinition=combat.resources._CACHED_MELEE_IMPACT,
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
    if not getattr(STATE, "guard_damage_reduction_enabled", True):
        return False
    pc = get_local_player_controller()
    if pc is None or not same_uobject(getattr(pc, "Pawn", None), pawn):
        return False

    # Guard only exists while its own blade is still the held weapon.
    wpn = deref_weak(st.wpn_ref)
    held = getattr(pawn, "Weapon", None)
    resolved = resolve_melee_weapon(held, sync_weapon_props=False)
    guard_wpn = unwrap_weapon_object(wpn) or wpn
    held_wpn = unwrap_weapon_object(held) or held
    if (
        wpn is None
        or resolved is None
        or resolved[1].secondary_attack != SecondaryAttack.GUARD
        or not same_uobject(guard_wpn, held_wpn)
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
    guard_wpn = unwrap_weapon_object(deref_weak(st.wpn_ref)) or deref_weak(st.wpn_ref)
    held_wpn = unwrap_weapon_object(held) or held
    if guard_wpn is not None and held_wpn is not None and not same_uobject(guard_wpn, held_wpn):
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
    """Unconditional pose restore that never depends on the throw/guard objects still existing."""
    try:
        combat = _combat()
        combat.reset_first_person_pose(pose_kind)
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
        combat.reset_first_person_pose("throw ended")
    elif pose == combat.POSE_GUARD and _GUARD is None:
        combat.reset_first_person_pose("guard ended")


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
        _combat().reset_first_person_pose("secondary state reset")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# RMB release hooks (`StopZoom`)
#
# Zoom is suppressed on Melee Weapons, so these hooks exist purely to learn when the player
# lets go of RMB. They are registered with `ALL_HOOKS` at load; the frame tick only enforces
# the maximum hold timeout as a fail-safe.
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
