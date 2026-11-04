"""SDK hook callbacks that route into the combat subsystems."""

from __future__ import annotations

from typing import Any

from unrealsdk.hooks import Block

from ...core.enums import SecondaryAttack
from ...core.parts.weapon import MeleeWeaponInstance
from ...core.stats import ComputedCardStats
from ..log import log_error, log_info
from ..object_identity import is_dead
from ..resolver import resolve_melee_weapon
from .. import secondary
from ..state import STATE
from . import attacks, damage, pose


MELEE_SECONDARY_ATTACK_ENABLED = True


_SHOT_SEQ: int = 0


_HANDLED_SHOT_SEQ: int = 0


def _try_melee_secondary_attack(wpn: Any, inst: MeleeWeaponInstance, card: ComputedCardStats) -> bool:
    """Dispatch a Melee Weapon secondary attack on RMB / Left Trigger.

    - `THROW` (Bandit, Torgue): hurl the blade. Returns False if it is already airborne.
    - `GUARD` (Hyperion, Jakobs): raise the guard; it is held until RMB is released (`StopZoom`)
      and is refused while its part-driven cooldown is still running.
    - `NONE` (Maliwan): nothing happens, RMB stays inert.

    Zoom is blocked either way, so returning False never re-enables ADS on a blade.
    """
    if not MELEE_SECONDARY_ATTACK_ENABLED:
        return False
    if card.secondary_attack == SecondaryAttack.THROW:
        started = secondary.begin_throw(wpn, inst, card)
        if started:
            # Give the throw a real arm motion + whoosh + camera bump (the blade itself leaves the
            # hand logically: state is authoritative, the 1st-person swing sells the release).
            shooter, pc = attacks._resolve_shooter_and_pc(wpn)
            if shooter is not None and not is_dead(shooter):
                ctx = damage.read_player_melee_context(shooter, pc)
                pose._trigger_first_person_blade_swing(pc, shooter, card, ctx.swing_speed_scale)
        return started
    if card.secondary_attack == SecondaryAttack.GUARD:
        return secondary.try_begin_guard(wpn, inst, card)
    return False


def on_melee_weapon_start_zoom_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block RMB zoom while a Melee Weapon is held and offer the secondary-attack stub a turn."""
    resolved = resolve_melee_weapon(obj, sync_weapon_props=False)
    if resolved is None:
        return None
    inst, card = resolved

    if _try_melee_secondary_attack(obj, inst, card):
        return Block

    if STATE.debug_attack_log:
        log_info(
            f"RMB zoom suppressed [{card.manufacturer.value} {card.archetype_name}] "
            f"(secondary attack: {card.secondary_attack.value})"
        )
    return Block


def on_weapon_consume_ammo_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Keep melee magazine full, enforce semi-auto on Jakobs Sabre (Gotcha #24), & arm swing."""
    global _SHOT_SEQ
    resolved = resolve_melee_weapon(obj, sync_weapon_props=True)
    if resolved is None:
        return None
    _inst, card = resolved
    _SHOT_SEQ += 1

    try:
        clip = int(getattr(obj, "ClipSize", 100) or 100)
        obj.ReloadCnt = max(10, clip)
    except Exception:
        pass

    if card.semi_auto_click:
        try:
            obj.ClearPendingFire(0)
        except Exception:
            pass
    return None


on_weapon_consume_ammo_pre = on_weapon_consume_ammo_post


def on_weapon_instant_fire_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Primary `LMB` melee attack intercept (`WillowWeapon:InstantFire` PRE -> `Block`)."""
    global _HANDLED_SHOT_SEQ
    resolved = resolve_melee_weapon(obj)
    if resolved is None:
        return None
    # The blade is in the air (Bandit / Torgue throw): Primary Fire is locked until it rebounds.
    # Consume the shot sequence too, so the `SharedFireAmmunition` backup never fires a delayed
    # swing once the blade comes back.
    if secondary.is_weapon_thrown(obj):
        _HANDLED_SHOT_SEQ = _SHOT_SEQ
        return Block
    inst, card = resolved
    _HANDLED_SHOT_SEQ = _SHOT_SEQ
    try:
        attacks._execute_melee_attack(obj, inst, card)
    except Exception as exc:
        log_error(f"on_weapon_instant_fire_pre error: {exc!r}")
    return Block


def on_weapon_firing_mode_fire_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block native gun bullet spawning (`WillowWeapon:FiringModeDefinitionFire` PRE)."""
    if resolve_melee_weapon(obj) is not None:
        return Block
    return None


def on_weapon_play_fire_effects_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block gun muzzle flashes & gun-fire twitch animations (`WillowWeapon:PlayFireEffects` PRE)."""
    if resolve_melee_weapon(obj) is not None:
        return Block
    return None


def on_weapon_shared_fire_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Backup safety net if any firing path ever bypasses `InstantFire`."""
    global _HANDLED_SHOT_SEQ
    if _HANDLED_SHOT_SEQ == _SHOT_SEQ:
        return None
    resolved = resolve_melee_weapon(obj)
    if resolved is None:
        return None
    # Swallow any backup `SharedFireAmmunition` path while the thrown blade is still airborne,
    # consuming the shot sequence so no delayed swing fires after the blade comes back.
    if secondary.is_weapon_thrown(obj):
        _HANDLED_SHOT_SEQ = _SHOT_SEQ
        return None
    inst, card = resolved
    _HANDLED_SHOT_SEQ = _SHOT_SEQ
    try:
        attacks._execute_melee_attack(obj, inst, card)
    except Exception as exc:
        log_error(f"on_weapon_shared_fire_post error: {exc!r}")
    return None


def on_pawn_weapon_fired_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block 1st-person gun recoil (`ADD_Fire_Recoil`) on `Pawn.Arms` for Melee Arsenal blades."""
    try:
        in_weapon = getattr(args, "InWeapon", None) or getattr(obj, "Weapon", None)
    except Exception:
        in_weapon = getattr(obj, "Weapon", None)
    if in_weapon is not None and resolve_melee_weapon(in_weapon) is not None:
        return Block
    return None


def on_light_projectile_add_proj_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block any stray `MeleeArsenalDev.*` light projectile if ever invoked."""
    try:
        fm = getattr(args, "FiringModeDefinition", None)
        if fm is not None and str(fm._path_name()).startswith("MeleeArsenalDev."):
            return Block, -1
    except Exception:
        pass
    return None


def on_weapon_reload_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Melee weapons never reload — block reload attempts and keep `ReloadCnt` full."""
    if resolve_melee_weapon(obj) is None:
        return None
    try:
        obj.ReloadCnt = max(10, int(getattr(obj, "ClipSize", 100) or 100))
    except Exception:
        pass
    return Block


def on_player_tick_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Update active Combo Hit 2 Right->Left return slash right after `PlayerTick`."""
    if STATE.swing_motion.active:
        pose._update_first_person_slash_motion(obj)
    return None


def on_ai_take_damage_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Optional debug telemetry on AI damage taken from Melee Arsenal blades."""
    if not STATE.debug_attack_log:
        return None
    try:
        causer = getattr(args, "DamageCauser", None)
        op = getattr(causer, "ObjectPointer", None)
        if op is not None:
            causer = op
        if causer is not None and resolve_melee_weapon(causer) is not None:
            dmg = getattr(args, "Damage", 0.0)
            log_info(f"AI TakeDamage -> {getattr(obj, 'Name', '?')} took {dmg:.1f}")
    except Exception:
        pass
    return None
