"""First-person melee animation and procedural hand/weapon poses."""

from __future__ import annotations
from ..sdk import prevent_hooking_direct_calls, unrealsdk

import copy
import math
import time
from typing import Any


from ...core.enums import Manufacturer, TargetingMode
from ...core.stats import ComputedCardStats
from ..log import log_debug_once, log_info
from ..object_identity import (
    cache_key,
    deref_weak,
    is_dead,
    same_uobject,
    uobject_key,
    wrap_weak,
)
from ..resolver import get_local_player_controller, resolve_melee_weapon, unwrap_weapon_object
from ..state import STATE
from .. import resources
from . import common
from .damage import play_camera_anim


BLADED_MELEE_SEQ_LENGTH = 0.6333


CAMERA_SHAKE_BLEND_IN_SEC = 0.30


CAMERA_SHAKE_BLEND_OUT_SEC = 0.32


CAMERA_SHAKE_RATE_HEAVY = 0.65


CAMERA_SHAKE_RATE_CLEAVE = 0.68


CAMERA_SHAKE_RATE_SINGLE = 0.70


CAMERA_SHAKE_CADENCE_REFERENCE = 1.45


CAMERA_SHAKE_CADENCE_FLOOR = 0.45


CAMERA_SHAKE_SLIDER_GAIN = 0.20


_ARMS_SLOT_CACHE: dict[str, dict[str, Any]] = {}


_ARMS_REST_BASELINE: dict[str, dict[str, Any]] = {}


_POSE_KIND: str = ""


_POSE_STARTED_AT: float = 0.0


_POSE_REST: dict[str, Any] | None = None


_POSE_BLADE_REST: dict[str, Any] | None = None


POSE_GUARD = "guard"


POSE_THROWN = "thrown"


POSE_MAX_HOLD_SEC: float = 12.0


_POSE_OFFSETS: dict[str, dict[str, float]] = {
    # The hand lift is small; the held weapon supplies the visible brace angle.
    POSE_GUARD: {"pitch": 1500.0, "yaw": 0.0, "roll": 0.0, "x": 0.0, "y": 0.0, "z": 0.0},
    POSE_THROWN: {"pitch": 0.0, "yaw": 0.0, "roll": 0.0, "x": 0.0, "y": 4.0, "z": -75.0},
}


GUARD_BLADE_PITCH: float = 2600.0


GUARD_BLADE_YAW: float = -1500.0


GUARD_BLADE_ROLL: float = -1200.0


_LAST_SWING_AT: float = 0.0


def _get_arms_anim_slots(arms: Any) -> dict[str, Any]:
    """Locate & cache `WeaponAction`, `CustomAnimSlot`, and `WeaponRecoil` on `Pawn.Arms`."""
    if arms is None or is_dead(arms):
        return {}
    aid = cache_key(arms)
    cached = _ARMS_SLOT_CACHE.get(aid)
    if cached is not None:
        act = deref_weak(cached.get("action"))
        cus = deref_weak(cached.get("custom"))
        rec = deref_weak(cached.get("recoil"))
        if act is not None or cus is not None:
            return {"action": act, "custom": cus, "recoil": rec}

    found_act = None
    found_cus = None
    found_rec = None
    try:
        for slot in list(unrealsdk.find_all("AnimNodeSlot", exact=False)):
            if is_dead(slot):
                continue
            if not same_uobject(getattr(slot, "SkelComponent", None), arms):
                continue
            nname = str(getattr(slot, "NodeName", ""))
            if nname == "WeaponAction":
                found_act = slot
            elif nname == "CustomAnimSlot":
                found_cus = slot
            elif nname == "WeaponRecoil":
                found_rec = slot
    except Exception:
        pass

    if found_act is not None or found_cus is not None or found_rec is not None:
        _ARMS_SLOT_CACHE[aid] = {
            "arms_ref": wrap_weak(arms),
            "action": wrap_weak(found_act),
            "custom": wrap_weak(found_cus),
            "recoil": wrap_weak(found_rec),
        }
    return {"action": found_act, "custom": found_cus, "recoil": found_rec}


def _snapshot_hands(sm: Any, arms: Any) -> dict[str, Any] | None:
    """Read the *live* `RotOrigin`/`Origin` of the first-person hands right now."""
    try:
        ro = sm.RotOrigin
        origin = getattr(sm, "Origin", None)
        return {
            "arms_ref": wrap_weak(arms),
            "sm_ref": wrap_weak(sm),
            "sm_key": uobject_key(sm),
            "rot": (int(ro.Pitch), int(ro.Yaw), int(ro.Roll)),
            "loc": ((float(origin.X), float(origin.Y), float(origin.Z)) if origin is not None else None),
        }
    except Exception:
        return None


def _capture_arms_rest_transform(arms: Any, sm: Any) -> dict[str, Any] | None:
    """Remember the pristine `RotOrigin`/`Origin` of the first-person hands exactly once."""
    if arms is None or sm is None:
        return None
    if _POSE_KIND:
        # A posed transform is not a valid idle baseline.
        return None
    entry = _ARMS_REST_BASELINE.get(cache_key(sm))
    if entry is not None:
        # Discard cache entries when their weak reference points to a different mesh.
        if same_uobject(deref_weak(entry.get("sm_ref")), sm):
            return entry
        _ARMS_REST_BASELINE.pop(cache_key(sm), None)
    try:
        ro0 = sm.RotOrigin
        origin0 = getattr(sm, "Origin", None)
        entry = {
            "arms_ref": wrap_weak(arms),
            "sm_ref": wrap_weak(sm),
            "rot": (int(ro0.Pitch), int(ro0.Yaw), int(ro0.Roll)),
            "loc": ((float(origin0.X), float(origin0.Y), float(origin0.Z)) if origin0 is not None else None),
        }
        _ARMS_REST_BASELINE[cache_key(sm)] = entry
        log_info(f"Captured first-person arms rest transform: rot={entry['rot']}, loc={entry.get('loc')}")
        return entry
    except Exception:
        return None


def _held_weapon_mesh(pawn: Any) -> Any:
    """The first-person `Mesh` component of the blade the player is holding (or None)."""
    wpn = getattr(pawn, "Weapon", None) if pawn is not None else None
    if wpn is None:
        return None
    try:
        wpn = unwrap_weapon_object(wpn)
    except Exception:
        return None
    mesh = getattr(wpn, "Mesh", None) if wpn is not None else None
    if mesh is None or is_dead(mesh) or not hasattr(mesh, "RotOrigin"):
        return None
    return mesh


def _snapshot_blade(mesh: Any) -> dict[str, Any] | None:
    """Read the held blade mesh's own `RotOrigin` (its pivot is at the hand, not behind it)."""
    try:
        ro = mesh.RotOrigin
        return {
            "mesh_ref": wrap_weak(mesh),
            "rot": (int(ro.Pitch), int(ro.Yaw), int(ro.Roll)),
        }
    except Exception:
        return None


def _write_blade_tilt(mesh: Any, tilt: float, bob: float) -> None:
    """Tilt the held blade into the guard stance (relative to its own captured rest)."""
    rest = _POSE_BLADE_REST
    if mesh is None or rest is None:
        return
    if not same_uobject(deref_weak(rest.get("mesh_ref")), mesh):
        return
    try:
        bp, by, br = rest["rot"]
        ro = copy.copy(mesh.RotOrigin)
        ro.Pitch = common.rotator_add(bp, GUARD_BLADE_PITCH * tilt + 120.0 * bob)
        ro.Yaw = common.rotator_add(by, GUARD_BLADE_YAW * tilt)
        ro.Roll = common.rotator_add(br, GUARD_BLADE_ROLL * tilt + 80.0 * bob)
        mesh.RotOrigin = ro
    except Exception as exc:  # pragma: no cover - engine call shape varies
        log_debug_once("_write_blade_tilt", f"_write_blade_tilt failed: {exc!r}")


def _restore_blade() -> None:
    """Put the held blade mesh back exactly where it was before the pose."""
    rest = _POSE_BLADE_REST
    if rest is None:
        return
    mesh = deref_weak(rest.get("mesh_ref"))
    if mesh is None:
        return
    try:
        bp, by, br = rest["rot"]
        ro = copy.copy(mesh.RotOrigin)
        ro.Pitch, ro.Yaw, ro.Roll = bp, by, br
        mesh.RotOrigin = ro
    except Exception:
        pass


def _resolve_hands(pc: Any) -> tuple[Any, Any, Any] | None:
    """Resolve `(pawn, arms, skeletal_mesh)` for the local first-person hands."""
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None or is_dead(pawn):
        return None
    arms = getattr(pawn, "Arms", None)
    if arms is None or is_dead(arms):
        return None
    sm = getattr(arms, "SkeletalMesh", None)
    if sm is None or is_dead(sm) or not hasattr(sm, "RotOrigin"):
        return None
    return pawn, arms, sm


def _pose_target(pc: Any) -> tuple[Any, Any, Any, dict[str, Any]] | None:
    """Resolve `(pawn, arms, skeletal_mesh, rest_snapshot)` for the current pose."""
    hands = _resolve_hands(pc)
    if hands is None:
        return None
    pawn, arms, sm = hands
    entry = _POSE_REST
    if entry is None or not same_uobject(deref_weak(entry.get("sm_ref")), sm):
        entry = _snapshot_hands(sm, arms)
    if entry is None:
        return None
    return pawn, arms, sm, entry


def apply_first_person_pose(pc: Any, kind: str) -> bool:
    """Brace the blade (`guard`) or empty the hands (`thrown`) in first person.

    The pose is procedural (`Arms.SkeletalMesh.RotOrigin` + `Origin` + `ForceUpdate`) because no
    vanilla sequence holds a guard stance: the same technique the mod already uses for its swing
    fallback, which is verified to move the hands and the attached blade. Returns True when the
    pose is live.
    """
    global _POSE_KIND, _POSE_STARTED_AT, _POSE_REST, _POSE_BLADE_REST
    if kind not in _POSE_OFFSETS:
        return clear_first_person_pose(pc)
    hands = _resolve_hands(pc)
    if hands is None:
        return False
    _pawn, arms, sm = hands
    if _POSE_KIND:
        # A pose must never be applied on top of another one (that is how offsets accumulate):
        # snap back to the previous snapshot first, then re-read the hands.
        _write_rest_transforms()
        _POSE_KIND = ""
    if STATE.swing_motion.active:
        # Let the fallback slash arc finish restoring before the secondary pose takes over.
        restore_all_arms_rest_transforms()
    snapshot = _snapshot_hands(sm, arms)
    if snapshot is None:
        return False
    _POSE_REST = snapshot
    _POSE_BLADE_REST = _snapshot_blade(_held_weapon_mesh(_pawn))
    _POSE_KIND = kind
    _POSE_STARTED_AT = time.monotonic()
    update_first_person_pose(pc)
    log_info(f"First-person pose applied: {kind}")
    return True


def active_first_person_pose() -> str:
    """The currently applied secondary-attack posture ("" when the hands are idle)."""
    return _POSE_KIND


def update_first_person_pose(pc: Any, now: float | None = None) -> None:
    """Re-apply the live pose (guard gets a slow brace breathing bob, throw stays still)."""
    global _POSE_KIND, _POSE_REST
    if not _POSE_KIND:
        return
    target = _pose_target(pc)
    if target is None:
        _write_rest_transforms()
        _POSE_KIND = ""
        return
    _pawn, arms, sm, entry = target
    _POSE_REST = entry
    offsets = _POSE_OFFSETS.get(_POSE_KIND)
    if offsets is None:
        return
    now = time.monotonic() if now is None else now
    if now - _POSE_STARTED_AT > POSE_MAX_HOLD_SEC:
        reset_first_person_pose("held past its safety limit")
        return
    bob = 0.0
    tilt = 1.0
    if _POSE_KIND == POSE_GUARD:
        # A slow, shallow bob keeps the brace alive instead of reading as a frozen T-pose.
        bob = math.sin((now - _POSE_STARTED_AT) * 6.2)
        # `Guard Blade Tilt (%)` lets the player flip/cut the inward swing in game if the
        # RotOrigin sign convention reads backwards on their install.
        tilt = float(getattr(STATE, "guard_pose_scale", 1.0))
    try:
        bp, by, br = entry["rot"]
        ro = copy.copy(sm.RotOrigin)
        ro.Pitch = common.rotator_add(bp, offsets["pitch"] + 90.0 * bob)
        ro.Yaw = common.rotator_add(by, offsets["yaw"] * tilt)
        ro.Roll = common.rotator_add(br, offsets["roll"] * tilt + 60.0 * bob)
        sm.RotOrigin = ro
        loc = entry.get("loc")
        if loc is not None and hasattr(sm, "Origin"):
            origin = copy.copy(sm.Origin)
            origin.X = float(loc[0]) + offsets["x"]
            origin.Y = float(loc[1]) + offsets["y"]
            origin.Z = float(loc[2]) + offsets["z"]
            sm.Origin = origin
        if hasattr(arms, "ForceUpdate"):
            arms.ForceUpdate(False)
    except Exception:
        _write_rest_transforms()
        _POSE_KIND = ""
    if _POSE_KIND == POSE_GUARD:
        # The blade tilt is cosmetic and must never be able to tear down the pose.
        try:
            _write_blade_tilt(_held_weapon_mesh(_pawn), tilt, bob)
        except Exception:
            pass


def reset_first_person_pose(reason: str = "") -> None:
    """Unconditionally restore the idle hands and drop the pose, whatever its kind.

    This is the guard-release path: a hit can consume the brace without the engine ever reporting
    an RMB release, so the restore must never be conditional on the tracked pose kind matching.
    """
    global _POSE_KIND, _POSE_REST, _POSE_BLADE_REST
    had = _POSE_KIND
    _POSE_KIND = ""
    _write_rest_transforms()
    _POSE_REST = None
    _POSE_BLADE_REST = None
    if had:
        log_info(f"First-person pose force-restored ({had}{': ' + reason if reason else ''})")


def capture_first_person_rest_transform() -> bool:
    """Refresh the cached hands transform while no pose or swing is active."""
    if _POSE_KIND or STATE.swing_motion.active:
        return False
    try:
        hands = _resolve_hands(get_local_player_controller())
    except Exception:
        return False
    if hands is None:
        return False
    _pawn, arms, sm = hands
    snap = _snapshot_hands(sm, arms)
    if snap is None:
        return False
    entry = _ARMS_REST_BASELINE.get(cache_key(sm))
    if (
        entry is None
        or entry.get("rot") != snap["rot"]
        or entry.get("loc") != snap["loc"]
        or entry.get("sm_key") != snap["sm_key"]
    ):
        _ARMS_REST_BASELINE[cache_key(sm)] = snap
        log_info(f"Captured first-person arms rest transform: rot={snap['rot']}, loc={snap.get('loc')}")
    return True


def pose_held_seconds() -> float:
    """How long the current pose has been applied (0.0 when idle)."""
    if not _POSE_KIND:
        return 0.0
    return max(0.0, time.monotonic() - _POSE_STARTED_AT)


def clear_first_person_pose(pc: Any = None, kind: str = "") -> bool:
    """Restore idle hands. Pass `kind` to only clear that specific pose."""
    global _POSE_KIND, _POSE_REST, _POSE_BLADE_REST
    if not _POSE_KIND:
        return True
    if kind and _POSE_KIND != kind:
        return False
    cleared = _POSE_KIND
    _POSE_KIND = ""
    _write_rest_transforms()
    _POSE_REST = None
    _POSE_BLADE_REST = None
    log_info(f"First-person pose cleared: {cleared}")
    return True


def _write_rest_transforms() -> None:
    """Write every captured rest transform back (safe to call at any time)."""
    _restore_blade()
    entries = list(_ARMS_REST_BASELINE.items())
    if _POSE_REST is not None:
        entries.append(("__pose__", _POSE_REST))
    for sm_id, entry in entries:
        sm = deref_weak(entry.get("sm_ref"))
        arms = deref_weak(entry.get("arms_ref"))
        if sm is None:
            _ARMS_REST_BASELINE.pop(sm_id, None)
            continue
        try:
            bp, by, br = entry["rot"]
            ro = copy.copy(sm.RotOrigin)
            ro.Pitch, ro.Yaw, ro.Roll = bp, by, br
            sm.RotOrigin = ro
            loc = entry.get("loc")
            if loc is not None and hasattr(sm, "Origin"):
                origin = copy.copy(sm.Origin)
                origin.X, origin.Y, origin.Z = loc
                sm.Origin = origin
            if arms is not None and hasattr(arms, "ForceUpdate"):
                arms.ForceUpdate(False)
        except Exception as exc:  # pragma: no cover - engine call shape varies
            log_debug_once("_write_rest_transforms", f"_write_rest_transforms failed: {exc!r}")


def _trigger_first_person_blade_swing(
    pc: Any,
    pawn: Any,
    card: ComputedCardStats,
    swing_speed_scale: float = 1.0,
) -> None:
    """Play Gearbox's `Bladed_Melee` 1st-person blade slash + CameraAnim + melee whoosh audio."""
    global _LAST_SWING_AT
    now = time.monotonic()
    if now - _LAST_SWING_AT < 0.03:
        return
    _LAST_SWING_AT = now

    resources.ensure_combat_resources_cached()
    eff_speed = max(0.35, card.swing_speed * max(0.25, swing_speed_scale))
    swing_interval = max(0.22, 1.0 / eff_speed)

    # Play real `Bladed_Melee` sequence on `Pawn.Arms` `CustomAnimSlot` (never clobbering
    # `SpecialMove_FirstPerson` on `WeaponAction`, and skipping during mid-swap `PendingWeapon`).
    played_real_anim = False
    pending_wpn = getattr(pawn, "PendingWeapon", None) if pawn is not None else None
    arms = getattr(pawn, "Arms", None) if (pawn is not None and pending_wpn is None) else None
    if arms is not None and not is_dead(arms):
        slots = _get_arms_anim_slots(arms)
        recoil_slot = slots.get("recoil")
        target_slot = slots.get("custom") or slots.get("action")

        with prevent_hooking_direct_calls():
            if recoil_slot is not None:
                try:
                    recoil_slot.StopCustomAnim(0.0)
                except Exception:
                    pass

            if target_slot is not None:
                play_rate = max(0.68, min(2.35, BLADED_MELEE_SEQ_LENGTH / (swing_interval * 0.92)))
                for seq_name in ("Bladed_Melee", "Melee"):
                    try:
                        target_slot.StopCustomAnim(0.02)
                    except Exception:
                        pass
                    try:
                        dur = target_slot.PlayCustomAnim(
                            seq_name,
                            float(play_rate),
                            0.035,
                            0.08,
                            False,
                            True,
                        )
                        if isinstance(dur, (int, float)) and float(dur) > 0.01:
                            played_real_anim = True
                            break
                    except Exception:
                        continue

    # Procedural `RotOrigin` + `ForceUpdate(False)` fallback ONLY if no AnimNodeSlot existed
    if not played_real_anim and arms is not None and not is_dead(arms):
        sm = getattr(arms, "SkeletalMesh", None)
        if sm is not None and hasattr(sm, "RotOrigin"):
            _capture_arms_rest_transform(arms, sm)
            sm_info = STATE.swing_motion
            sm_info.active = True
            sm_info.start_time = now
            sm_info.duration = min(0.55, swing_interval * 0.88)
            sm_info.style = card.manufacturer.value
            sm_info.swing_sign *= -1

    # Manufacturer-tuned CameraAnim impact feel — soft, slow and eased on purpose.
    #    A 2.35 swings/sec Sabre re-triggers this every ~0.42s, so a snappy shake stacked into a
    #    jarring camera flick. Playback runs at 0.60-0.68x (slower opening motion), the blend-in
    #    window is long enough to read as a ramp instead of a jump, and cadence damping scales the
    #    shake down as swing speed rises. `MODS -> Swing Camera Shake (%)` (rescaled by
    #    `CAMERA_SHAKE_SLIDER_GAIN`) is the final taste dial.
    if pc is not None:
        if card.manufacturer == Manufacturer.TORGUE:
            canim = resources._CACHED_CANIMS.get(resources.CANIM_HEAVY_SLAM)
            scale, rate = 0.50, CAMERA_SHAKE_RATE_HEAVY
        elif card.targeting_mode == TargetingMode.FRONTAL_CLEAVE:
            canim = resources._CACHED_CANIMS.get(resources.CANIM_MEDIUM_CLEAVE)
            scale, rate = 0.30, CAMERA_SHAKE_RATE_CLEAVE
        else:
            canim = resources._CACHED_CANIMS.get(resources.CANIM_LIGHT_SLASH)
            scale, rate = 0.18, CAMERA_SHAKE_RATE_SINGLE

        cadence_damping = min(
            1.0,
            max(CAMERA_SHAKE_CADENCE_FLOOR, CAMERA_SHAKE_CADENCE_REFERENCE / eff_speed),
        )
        shake_scale = float(getattr(STATE, "camera_shake_scale", 0.5) or 0.0)
        scale *= cadence_damping * shake_scale * CAMERA_SHAKE_SLIDER_GAIN

        play_camera_anim(pc, canim, scale, rate, CAMERA_SHAKE_BLEND_IN_SEC, CAMERA_SHAKE_BLEND_OUT_SEC)

    # Crisp melee slash whoosh audio
    if pawn is not None:
        ak_whoosh = resources._CACHED_AKEVENTS.get(resources.AK_SWING_WHOOSH)
        if ak_whoosh is not None:
            try:
                pawn.PlayAkEvent(ak_whoosh)
            except Exception:
                pass
        if card.manufacturer in (Manufacturer.BANDIT, Manufacturer.TORGUE):
            ak_heavy = resources._CACHED_AKEVENTS.get(resources.AK_SWING_HEAVY_SWIPE)
            if ak_heavy is not None:
                try:
                    pawn.PlayAkEvent(ak_heavy)
                except Exception:
                    pass


def restore_all_arms_rest_transforms() -> None:
    """Restore any modified fallback `SkeletalMesh` transform cleanly.

    A live secondary-attack pose (`guard` / `thrown`) is intentionally *not* torn down here: the
    next `tick_secondary_attacks()` re-applies it on top of the restored rest transform.
    """
    STATE.swing_motion.active = False
    _write_rest_transforms()


def update_first_person_slash_motion(pc: Any) -> None:
    """Drive fallback `RotOrigin` + `ForceUpdate(False)` arc only when `AnimNodeSlot` was unavailable."""
    sm_state = STATE.swing_motion
    if not sm_state.active:
        return
    # A guard brace / emptied throwing hand owns the transform; the fallback arc never fights it.
    if _POSE_KIND:
        return

    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None or is_dead(pawn):
        restore_all_arms_rest_transforms()
        return

    wpn = getattr(pawn, "Weapon", None)
    if wpn is None or resolve_melee_weapon(wpn) is None:
        restore_all_arms_rest_transforms()
        return

    arms = getattr(pawn, "Arms", None)
    sm = getattr(arms, "SkeletalMesh", None) if arms is not None else None
    entry = _ARMS_REST_BASELINE.get(cache_key(sm)) if sm is not None else None
    if sm is None or entry is None:
        sm_state.active = False
        return

    elapsed = time.monotonic() - sm_state.start_time
    dur = max(0.12, sm_state.duration)
    if elapsed >= dur:
        restore_all_arms_rest_transforms()
        return

    u = elapsed / dur
    arc = math.sin(u * math.pi) * max(0.0, STATE.slash_motion_scale)
    sgn = float(sm_state.swing_sign or 1)
    bp, by, br = entry["rot"]
    try:
        ro = copy.copy(sm.RotOrigin)
        ro.Pitch = int(bp - 3800 * arc)
        ro.Yaw = int(by - 4200 * arc * sgn)
        ro.Roll = int(br + 2800 * arc * sgn)
        sm.RotOrigin = ro
        if hasattr(arms, "ForceUpdate"):
            arms.ForceUpdate(False)
    except Exception:
        sm_state.active = False
