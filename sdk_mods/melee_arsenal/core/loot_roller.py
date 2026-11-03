"""Procedural loot roller for Borderlands 2 Melee Arsenal.

Part-specific eligibility, weights and roll rules live in `core/parts/` next to the
part data (`grips.roll_grip_id`, `elements.roll_element_id`,
`accessories.roll_accessory_id`). This module is only the orchestrator: pick a
manufacturer/Blade, roll the four part ids in order, and assemble a
`MeleeWeaponInstance`. It also validates a set of part ids (`validate_weapon_parts`).
"""
from __future__ import annotations

import random

from .enums import Manufacturer, Rarity
from .parts.accessories import (
    ACCESSORIES,
    ACCESSORY_ROLL_CHANCE,  # re-exported for older callers (runtime/definitions/balances.py, tools)
    NO_ACCESSORY_ID,
    eligible_accessories_for_roll,
    roll_accessory_id,
)
from .parts.blades import BLADES, blade_for_manufacturer
from .parts.elements import ELEMENTS, eligible_elements_for_blade, roll_element_id
from .parts.grips import GRIPS, roll_grip_id
from .parts.weapon import MeleeWeaponInstance, MeleeWeaponParts

__all__ = [
    "ACCESSORY_ROLL_CHANCE",
    "eligible_accessories_for_roll",
    "eligible_elements_for_blade",
    "roll_melee_weapon",
    "validate_weapon_parts",
]


def validate_weapon_parts(parts: MeleeWeaponParts) -> list[str]:
    """Validate part legality; returns a list of violation messages (empty if valid)."""
    errors: list[str] = []
    if parts.blade_id not in BLADES:
        errors.append(f"Unknown blade_id: {parts.blade_id!r}")
        return errors
    if parts.grip_id not in GRIPS:
        errors.append(f"Unknown grip_id: {parts.grip_id!r}")
    if parts.accessory_id not in ACCESSORIES:
        errors.append(f"Unknown accessory_id: {parts.accessory_id!r}")
    if parts.element_id not in ELEMENTS:
        errors.append(f"Unknown element_id: {parts.element_id!r}")
        return errors

    legal_elem_ids = {ep.part_id for ep in eligible_elements_for_blade(parts.blade_id)}
    if parts.element_id not in legal_elem_ids:
        errors.append(
            f"Illegal element {parts.element_id!r} for blade {parts.blade_id!r} "
            f"(allowed: {sorted(legal_elem_ids)})"
        )

    if parts.accessory_id != NO_ACCESSORY_ID and parts.accessory_id in ACCESSORIES:
        # Check structural compatibility (rarity-independent)
        legal_acc_ids = {
            a.part_id
            for a in eligible_accessories_for_roll(
                parts.blade_id, parts.element_id, Rarity.PEARLESCENT
            )
        }
        if parts.accessory_id not in legal_acc_ids:
            errors.append(
                f"Incompatible accessory {parts.accessory_id!r} on blade {parts.blade_id!r} "
                f"with element {parts.element_id!r}"
            )
    return errors


def roll_melee_weapon(
    *,
    game_stage: int,
    rarity: Rarity = Rarity.UNCOMMON,
    manufacturer: Manufacturer | None = None,
    seed: int | str | None = None,
    unique_id: int | None = None,
) -> MeleeWeaponInstance:
    """Roll a complete procedural `MeleeWeaponInstance` with 3-4 parts.

    The RNG call order is part of the format: manufacturer -> Grip -> Element ->
    optional Accessory -> UniqueId. Keeping those picks in the per-part modules does
    not change it, so seeded rolls (tests, debug spawns, previews) stay reproducible.
    """
    rng = random.Random(seed) if seed is not None else random.Random()

    maker = rng.choice(tuple(Manufacturer)) if manufacturer is None else manufacturer
    blade = blade_for_manufacturer(maker)

    grip_id = roll_grip_id(rng)
    element_id = roll_element_id(rng, blade.part_id)
    accessory_id = roll_accessory_id(rng, blade.part_id, element_id, rarity)

    uid = unique_id if unique_id is not None else rng.randint(1, 2_147_483_647)

    return MeleeWeaponInstance(
        unique_id=uid,
        game_stage=max(1, game_stage),
        rarity=rarity,
        parts=MeleeWeaponParts(
            blade_id=blade.part_id,
            grip_id=grip_id,
            accessory_id=accessory_id,
            element_id=element_id,
        ),
        grade_index=max(1, game_stage),
    )
