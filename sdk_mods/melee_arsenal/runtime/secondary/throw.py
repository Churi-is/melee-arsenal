"""Thrown-blade state, flight, collision, and impact handling."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from ...core.enums import Element, SecondaryAttack
from ...core.parts.weapon import MeleeWeaponInstance
from ...core.secondary import (
    THROW_CATCH_RADIUS_UU,
    THROW_DAMAGE_MULTIPLIER,
    THROW_FALL_PROBE_UU,
    THROW_HAND_EMPTY_DELAY_SEC,
    THROW_HIT_PADDING_UU,
    THROW_MAX_FALL_UU,
    THROW_RETURN_MAX_SEC,
    ThrowFlightPlan,
    build_throw_flight_plan,
    compute_throw_weight_kg,
    point_segment_distance_uu,
)
from ...core.stats import ComputedCardStats, compute_attack_damage
from .. import secondary_vfx
from ..log import log_error, log_info
from ..object_identity import deref_weak, is_dead, same_uobject, wrap_weak
from ..resolver import get_local_player_controller, unwrap_weapon_object
from unrealsdk.hooks import prevent_hooking_direct_calls
from .common import _clear_pose, _combat, _same_object


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
    phase: str = "outbound"  # "outbound" -> "return"
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


# Ignore launch-time overlaps with the thrower’s arms, weapon, or camera-space collision.
THROW_LAUNCH_GRACE_UU: float = 150.0


_THROW: ThrowState | None = None


def _aim_forward(pc: Any, shooter: Any) -> tuple[float, float, float]:
    combat = _combat()
    rot = getattr(pc, "Rotation", None) if pc is not None else None
    if rot is None and shooter is not None:
        rot = getattr(shooter, "Rotation", None)
    return combat.rot_to_forward(rot) if rot is not None else (1.0, 0.0, 0.0)


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
    shooter, pc = combat.resolve_shooter_and_pc(wpn)
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
        candidates, pawn_map = combat.snapshot_cleave_candidates(shooter)
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
        along = math.sqrt((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2 + (point[2] - start[2]) ** 2)
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
        breakdown = compute_attack_damage(st.card, ctx, is_crit=False, damage_scale=THROW_DAMAGE_MULTIPLIER)
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
                    combat.make_vec_from(vec_tpl, *st.last_point),
                    combat.make_vec_from(vec_tpl, *hit_point),
                )
        except Exception:
            pass

    # Torgue Blast-Cleaver: the thrown slab detonates on any impact (its normal splash).
    if st.card.splash_radius_uu > 0.0 and breakdown.splash_hit_damage > 0 and vec_tpl is not None:
        try:
            sx, sy, sz = float(vec_tpl.X), float(vec_tpl.Y), float(vec_tpl.Z)
            offset_vec = combat.make_vec_from(
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
            combat.spawn_torgue_explosion_vfx_sfx(
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
        combat.play_thrown_impact_feedback(pc, shooter, shake=st.card.splash_radius_uu <= 0.0)
    except Exception:
        pass

    _begin_return(st, "impact")


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
        ground, ground_xyz, _g_bone = combat.run_weapon_trace(wpn, st.vec_template, point, below)
        where = f"ground at z={ground_xyz[2]:.0f}" if ground_xyz is not None else "no ground found"
        log_info(
            f"Throw arc ended at ({point[0]:.0f}, {point[1]:.0f}, {point[2]:.0f}) — "
            f"{where} within {THROW_FALL_PROBE_UU:.0f} uu below; still flying until it hits"
        )
    if st.origin_xyz[2] - point[2] >= THROW_MAX_FALL_UU:
        _begin_return(st, "fell out of the world")
        return

    hit_actor, hit_xyz, _bone = combat.run_weapon_trace(wpn, st.vec_template, st.last_point, point)

    if hit_actor is not None:
        hx, hy, hz = hit_xyz or point
        close_to_hand = (
            math.sqrt((hx - st.origin_xyz[0]) ** 2 + (hy - st.origin_xyz[1]) ** 2 + (hz - st.origin_xyz[2]) ** 2)
            < THROW_LAUNCH_GRACE_UU
        )
        if same_uobject(hit_actor, shooter) or _is_ghost_hit(hit_actor) or close_to_hand:
            # The thrower's own body / arms mesh, or a non-solid volume: keep flying.
            st.last_point = point
            return
        if combat.is_valid_melee_victim(shooter, hit_actor):
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
