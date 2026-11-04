"""Held guard, cooldown, damage reduction, parry, and release handling."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from mods_base import hook  # type: ignore[import-not-found]
from unrealsdk.hooks import Type, prevent_hooking_direct_calls  # type: ignore[import-not-found]

from ...core.enums import SecondaryAttack
from ...core.parts.weapon import MeleeWeaponInstance
from ...core.secondary import (
    GUARD_MAX_HOLD_SEC,
    GUARD_PARRY_WINDOW_SEC,
    GUARD_RELEASE_DEBOUNCE_SEC,
    compute_parry_reflect_damage,
)
from ...core.stats import ComputedCardStats, apply_guard_damage_reduction, compute_attack_damage
from ..log import log_error, log_info
from ..object_identity import deref_weak, is_dead, same_uobject, wrap_weak
from ..resolver import get_local_player_controller, resolve_melee_weapon, unwrap_weapon_object
from ..state import STATE
from .common import _combat, _same_object
from .throw import _play_throw_sfx


@dataclass
class GuardState:
    """The braced guard currently held by the local player."""

    wpn_ref: Any
    card: ComputedCardStats
    started_at: float
    cooldown_sec: float
    hits_absorbed: int = 0
    parries: int = 0


_GUARD: GuardState | None = None


_GUARD_COOLDOWN_UNTIL: float = 0.0


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


def end_guard(wpn: Any = None, *, start_cooldown: bool = True, respect_debounce: bool = False) -> None:
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
    reflected = compute_parry_reflect_damage(incoming_damage, st.card.crit_bonus_pct, blade_crit)
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
