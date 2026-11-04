"""Damage, status-effect, mitigation, and crit-feedback behavior."""

from __future__ import annotations
from ..sdk import Block, prevent_hooking_direct_calls

import random
import time
from typing import Any


from ...core.enums import Element
from ...core.parts.weapon import PlayerMeleeContext
from ...core.stats import ComputedCardStats, apply_incoming_damage_reduction
from ..log import log_debug_once, log_error, log_info
from ..object_identity import cache_key, same_uobject
from ..resolver import get_local_player_controller, resolve_melee_weapon
from ..secondary.guard import handle_guard_incoming_damage
from ..state import STATE
from .. import resources


DAMAGE_EVENT_FLAG_CRIT = 1


CRIT_FEEDBACK_WINDOW_SEC = 0.75


PENDING_CRIT_FEEDBACK: dict[str, float] = {}


_STATUS_CHANCE_FIELDS = (
    "BaseStatusEffectChanceModifierBaseValue",
    "BaseStatusEffectChanceModifier",
    "StatusEffectChanceModifierBaseValue",
    "StatusEffectChanceModifier",
)


def set_weapon_status_chance(wpn: Any, value: float) -> None:
    for attr_n in _STATUS_CHANCE_FIELDS:
        try:
            setattr(wpn, attr_n, float(value))
        except Exception as exc:
            log_debug_once(f"combat.status_chance:{attr_n}", f"set {attr_n} failed: {exc!r}")


def sync_weapon_status_damage(wpn: Any, breakdown: Any) -> None:
    """Write the player-scaled DoT value used by the native status-effect pipeline."""
    dot = float(max(0.0, getattr(breakdown, "effective_dot_dps", 0.0)))
    for attr_n in ("StatusEffectDamageBaseValue", "StatusEffectDamage"):
        try:
            setattr(wpn, attr_n, dot)
        except Exception as exc:
            log_debug_once(f"combat.status_damage:{attr_n}", f"set {attr_n} failed: {exc!r}")


def select_attack_damage_type(wpn: Any, card: ComputedCardStats, breakdown: Any) -> tuple[Any, bool]:
    """One authoritative proc roll for both LMB and thrown hits.

    The chance comes from the *player-scaled* breakdown, not the raw card, and the weapon's
    native status modifiers are forced so BL2 does not double-roll behind our back.
    """
    proc = False
    if card.element in (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG):
        proc = random.random() < float(breakdown.effective_proc_chance)
        if proc:
            dt_def = resources._CACHED_DMG_TYPES_PROC.get(card.element)
            set_weapon_status_chance(wpn, 50.0)
        else:
            dt_def = resources._CACHED_DMG_TYPES_NODOT.get(card.element) or resources._CACHED_DMG_TYPES_PROC.get(
                card.element
            )
            set_weapon_status_chance(wpn, 0.0)
    else:
        dt_def = resources._CACHED_DMG_TYPES_PROC.get(card.element)
        set_weapon_status_chance(wpn, 0.0)
    return dt_def, proc


def play_camera_anim(
    pc: Any,
    canim: Any,
    scale: float,
    rate: float,
    blend_in: float,
    blend_out: float,
) -> bool:
    """Play a mod-authored camera animation, honoring the user's motion scale.

    Never falls back to an unmodified `ClientPlayCameraAnim(canim)` call: that would bypass
    the accessibility setting on SDK builds with a different signature.
    """
    if pc is None or canim is None or float(scale) <= 0.001:
        return False
    try:
        pc.ClientPlayCameraAnim(canim, float(scale), float(rate), float(blend_in), float(blend_out), False, False)
        return True
    except Exception as exc:
        log_debug_once("combat.play_camera_anim", f"ClientPlayCameraAnim failed: {exc!r}")
        return False


def _eval_attr(attr_path: str, pawn: Any, default: float) -> float:
    resources.ensure_combat_resources_cached()
    attr_obj = resources._CACHED_ATTRS.get(attr_path)
    if attr_obj is None or pawn is None:
        return default
    try:
        res = attr_obj.GetValue(pawn)
        if isinstance(res, tuple) and len(res) >= 1:
            return float(res[0])
        return float(res)
    except Exception:
        return default


def read_player_melee_context(pawn: Any, pc: Any = None) -> PlayerMeleeContext:
    """Snapshot live player melee skill multipliers, Roid shield flat damage, and UVHM flag."""
    if not STATE.stack_player_melee_bonuses or pawn is None:
        return PlayerMeleeContext()

    melee_mult = max(0.1, _eval_attr(resources.ATTR_MELEE_MODIFIER, pawn, 1.0))
    roid_flat = max(0.0, _eval_attr(resources.ATTR_ROID_BUFF, pawn, 0.0)) if STATE.stack_roid_shields else 0.0
    if STATE.stack_roid_shields and roid_flat == 0.0:
        try:
            roid_flat = max(0.0, float(getattr(pawn, "InstigatedMeleeDamageAdded", 0.0)))
        except Exception:
            roid_flat = 0.0

    status_dmg_mult = max(0.1, _eval_attr(resources.ATTR_STATUS_DMG_MOD, pawn, 1.0))
    status_chance_mult = max(0.1, _eval_attr(resources.ATTR_STATUS_CHANCE_MOD, pawn, 1.0))

    is_uvhm = False
    if pc is not None:
        try:
            is_uvhm = int(pc.GetCurrentPlaythrough()) >= 2
        except Exception:
            is_uvhm = False

    return PlayerMeleeContext(
        melee_damage_scale=melee_mult,
        roid_flat_damage=roid_flat,
        status_damage_scale=status_dmg_mult,
        status_chance_scale=status_chance_mult,
        is_uvhm=is_uvhm,
    )


_TAKE_DAMAGE_AMOUNT_FIELDS = ("Damage", "DamageAmount", "InDamage", "RawDamage", "BaseDamage")


def read_take_damage_amount(args: Any) -> tuple[str, float] | None:
    """Locate and read the incoming-damage float field off a `TakeDamage` args struct."""
    if args is None:
        return None
    for field_name in _TAKE_DAMAGE_AMOUNT_FIELDS:
        try:
            if not hasattr(args, field_name):
                continue
            return field_name, float(getattr(args, field_name))
        except Exception:
            continue
    return None


def on_player_take_damage_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Grant the equipped Melee Weapon's rolled level- & part-scaled Damage Reduction (`TakeDamage`).

    Only the LOCAL player's pawn is touched, and only while a Melee Arsenal weapon is the active
    `Pawn.Weapon`. pyunrealsdk hook `args` are mutable but never propagate to the live call, so the
    reduction is applied by blocking the original hit and re-calling the same `TakeDamage` with the
    reduced damage inside `prevent_hooking_direct_calls()` (BL2 then splits that reduced amount
    between shields and health exactly as it normally would). Any unexpected state falls through and
    lets vanilla damage apply untouched.

    The rolled percentage starts at `30%` for a Lv 1 blade and climbs to the `70%` cap (76% for
    unique rarities) at Lv 80 + OP10 — see `core.stats.compute_damage_reduction_pct`.
    """
    try:
        pc = get_local_player_controller()
        if pc is None or obj is None or not same_uobject(getattr(pc, "Pawn", None), obj):
            return None

        # RMB Guard (Hyperion / Jakobs) supersedes the passive perk: a braced hit has the card
        # Damage Reduction applied twice, and a hit inside the parry window is reflected instead.
        if handle_guard_incoming_damage(obj, args, func):
            return Block

        if not STATE.damage_reduction_enabled:
            return None

        resolved = resolve_melee_weapon(getattr(obj, "Weapon", None), sync_weapon_props=False)
        if resolved is None:
            return None
        _inst, card = resolved

        pct = int(getattr(card, "damage_reduction_pct", 0))
        if pct <= 0:
            return None

        found = read_take_damage_amount(args)
        if found is None:
            return None
        field_name, damage = found
        if damage <= 0.0:
            return None

        reduced = apply_incoming_damage_reduction(damage, pct)
        if reduced >= damage:
            return None

        try:
            setattr(args, field_name, reduced)
            with prevent_hooking_direct_calls():
                func(args)
        except Exception as exc:
            log_error(f"Reduced-damage TakeDamage re-call failed: {exc!r}")
            return None

        if STATE.debug_attack_log:
            log_info(
                f"Melee Damage Reduction [{card.manufacturer.value} {card.archetype_name}] "
                f"{pct}%: {damage:.1f} -> {reduced:.1f} incoming"
            )
        return Block
    except Exception as exc:
        log_error(f"on_player_take_damage_pre failed: {exc!r}")
        return None


def mark_pending_crit_feedback(victim: Any) -> None:
    """Tag a victim we just critically struck so its vanilla damage popup shows as a crit."""
    if victim is None:
        return
    now = time.monotonic()
    for stale_id, expires_at in list(PENDING_CRIT_FEEDBACK.items()):
        if expires_at < now:
            PENDING_CRIT_FEEDBACK.pop(stale_id, None)
    PENDING_CRIT_FEEDBACK[cache_key(victim)] = now + CRIT_FEEDBACK_WINDOW_SEC


def clear_pending_crit_feedback(victim: Any) -> None:
    if victim is not None:
        PENDING_CRIT_FEEDBACK.pop(cache_key(victim), None)


def clear_all_pending_crit_feedback() -> None:
    """Drop every pending crit-feedback tag (map change / mod disable hygiene)."""
    PENDING_CRIT_FEEDBACK.clear()


def on_display_recent_damage_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Force the native CRITICAL popup for our Python-side weakspot crits.

    BL2 builds its floating damage numbers (and the red "CRITICAL" text) in
    `WillowDamageTypeDefinition:DisplayRecentDamageForPlayer`, reading
    `FPlayerRecentDamageEventData.DamageEventFlags`. The engine's damage pipeline only sets the crit
    bit when it is handed a real `HitInfo` crit region — and `Behavior_CauseDamage` has no `HitInfo`
    parameter, so our crits dealt full crit damage but always drew as a normal white number.

    pyunrealsdk hook args are a *copy*, so mutating them in place never reaches the live call. We
    therefore block the vanilla display and re-call the same function with `DamageEventFlags`' crit
    bit OR-ed in, exactly the block-and-recall pattern the damage-reduction hook already uses.
    Any unexpected state falls through to the untouched vanilla call.
    """
    if not PENDING_CRIT_FEEDBACK:
        return None

    try:
        data = getattr(args, "DamageEventData", None)
        actor = getattr(data, "DamagedActor", None) if data is not None else None
        if actor is None:
            return None

        expires_at = PENDING_CRIT_FEEDBACK.get(cache_key(actor))
        if expires_at is None:
            return None
        if time.monotonic() > expires_at:
            PENDING_CRIT_FEEDBACK.pop(cache_key(actor), None)
            return None

        # Only ever touch the local player's own client-side numbers (co-op safe).
        pc = getattr(args, "PC", None)
        local_pc = get_local_player_controller()
        if pc is not None and local_pc is not None and not same_uobject(pc, local_pc):
            return None

        flags = int(getattr(data, "DamageEventFlags", 0) or 0)
        if flags & DAMAGE_EVENT_FLAG_CRIT:
            PENDING_CRIT_FEEDBACK.pop(cache_key(actor), None)
            return None

        try:
            data.DamageEventFlags = flags | DAMAGE_EVENT_FLAG_CRIT
        except Exception:
            return None

        # Consume the tag so later DoT ticks / follow-up hits never inherit a stale crit flag.
        PENDING_CRIT_FEEDBACK.pop(cache_key(actor), None)
        try:
            with prevent_hooking_direct_calls():
                func(args)
        except Exception as exc:
            log_error(f"Crit damage-popup re-call failed: {exc!r}")
            return None
        if STATE.debug_attack_log:
            log_info(
                f"Forced crit damage popup on {getattr(actor, 'Name', 'unknown')} "
                f"({float(getattr(data, 'TotalDamageForDamageType', 0.0) or 0.0):.1f} dmg)"
            )
        return Block
    except Exception as exc:
        log_error(f"on_display_recent_damage_pre failed: {exc!r}")
        return None
