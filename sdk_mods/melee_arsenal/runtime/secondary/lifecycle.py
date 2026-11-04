"""Coordinate throw and guard state, per-frame updates, and lifecycle hooks."""

from __future__ import annotations

from typing import Any

from ...core.enums import SecondaryAttack
from ...core.secondary import GUARD_PARRY_WINDOW_SEC
from ...core.stats import ComputedCardStats
from .. import secondary_vfx
from ..log import log_error
from ..resolver import get_local_player_controller, resolve_melee_weapon
from . import guard, throw
from .common import _combat


def _reconcile_secondary_visuals() -> None:
    """Self-heal: no live throw/guard may leave a pose or a flying actor behind.

    A first-person pose is a raw transform on `Arms.SkeletalMesh`, so it must never be able to
    outlive its state (a stuck hand needs a game restart). This runs every frame and clears any
    pose or actor whose owner state has already ended.
    """
    combat = _combat()
    if throw._THROW is None and secondary_vfx.has_thrown_blade_visual():
        secondary_vfx.destroy_thrown_blade()
    pose = combat.active_first_person_pose()
    if not pose:
        return
    if pose == combat.POSE_THROWN and throw._THROW is None:
        combat.reset_first_person_pose("throw ended")
    elif pose == combat.POSE_GUARD and guard._GUARD is None:
        combat.reset_first_person_pose("guard ended")


def tick_secondary_attacks() -> None:
    """Per-frame driver: advance airborne throws and keep guard state honest."""
    try:
        throw.tick_throw()
    except Exception as exc:
        log_error(f"tick_throw failed: {exc!r}")
    try:
        guard.tick_guard()
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
    if throw._THROW is not None:
        info["thrown"] = True
        info["throw_phase"] = throw._THROW.phase
        info["thrown_visual"] = secondary_vfx.has_thrown_blade_visual()
        info["weight_kg"] = throw._THROW.plan.weight_kg
        info["range_m"] = throw._THROW.plan.range_meters
        info["detail"] = (
            f"Throw {throw._THROW.plan.weight_kg:.2f}kg -> {throw._THROW.plan.range_meters:.1f}m ({throw._THROW.phase})"
        )
    elif guard._GUARD is not None:
        info["guarding"] = True
        info["guard_cooldown_sec"] = guard._GUARD.cooldown_sec
        info["detail"] = (
            f"Guarding ({guard._GUARD.card.guard_reduction_pct}% DR, {GUARD_PARRY_WINDOW_SEC:.2f}s parry window)"
        )
    else:
        remaining = guard.guard_cooldown_remaining()
        info["guard_cooldown_remaining_sec"] = round(remaining, 2)
        if remaining > 0.0:
            info["detail"] = f"Guard cooldown {remaining:.2f}s"
        elif card is not None and card.secondary_attack == SecondaryAttack.THROW:
            info["detail"] = f"Throw ready ({card.throw_weight_kg:.2f}kg, {card.throw_range_m:.1f}m arc)"
        elif card is not None and card.secondary_attack == SecondaryAttack.GUARD:
            info["detail"] = f"Guard ready ({card.guard_reduction_pct}% DR, {card.guard_cooldown_sec:.2f}s cooldown)"
        else:
            info["detail"] = "No secondary attack"
    return info


def reset_secondary_state() -> None:
    """Map change / weapon reset / mod disable hygiene."""
    throw._THROW = None
    guard._GUARD = None
    guard._GUARD_COOLDOWN_UNTIL = 0.0
    secondary_vfx.destroy_thrown_blade()
    try:
        _combat().reset_first_person_pose("secondary state reset")
    except Exception:
        pass
