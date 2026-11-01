"""Procedural loot roller for Borderlands 2 Melee Arsenal."""
from __future__ import annotations

import random
from typing import Sequence

from .catalog import (
    ACCESSORIES,
    BLADE_BY_MANUFACTURER,
    BLADES,
    ELEMENT_PART_BY_TYPE,
    ELEMENTS,
    GRIPS,
    MANUFACTURERS,
)
from .enums import Element, ElementPolicy, Manufacturer, Rarity, TargetingMode
from .schemas import (
    AccessoryPartSpec,
    ElementPartSpec,
    MeleeWeaponInstance,
    MeleeWeaponParts,
)

# Vanilla-inspired accessory spawn probability by rarity tier
ACCESSORY_ROLL_CHANCE: dict[Rarity, float] = {
    Rarity.COMMON: 0.20,
    Rarity.UNCOMMON: 0.55,
    Rarity.RARE: 0.85,
    Rarity.VERY_RARE: 1.00,
    Rarity.E_TECH: 1.00,
    Rarity.LEGENDARY: 1.00,
    Rarity.SERAPH: 1.00,
    Rarity.PEARLESCENT: 1.00,
    Rarity.EFFERVESCENT: 1.00,
}


def _weighted_choice(rng: random.Random, items: Sequence[tuple[str, float]]) -> str:
    total = sum(max(0.0, w) for _, w in items)
    if total <= 0.0:
        return items[0][0]
    pick = rng.uniform(0.0, total)
    acc = 0.0
    for item_id, weight in items:
        acc += max(0.0, weight)
        if pick <= acc:
            return item_id
    return items[-1][0]


def eligible_elements_for_blade(blade_id: str) -> list[ElementPartSpec]:
    """Return valid ElementPartSpecs for a given Blade according to its Manufacturer's ElementPolicy."""
    blade = BLADES[blade_id]
    maker = MANUFACTURERS[blade.manufacturer]

    if maker.element_policy == ElementPolicy.NEVER:
        return [ELEMENTS["Elem_None"]]
    if maker.element_policy == ElementPolicy.ALWAYS_EXPLOSIVE:
        return [ELEMENTS["Elem_Explosive"]]
    if maker.element_policy == ElementPolicy.ALWAYS_ELEMENTAL:
        return [
            ELEMENT_PART_BY_TYPE[el]
            for el in maker.allowed_elements
            if el not in (Element.NONE, Element.EXPLOSIVE)
        ]

    # OPTIONAL: includes Elem_None plus allowed elements
    return [ELEMENT_PART_BY_TYPE[el] for el in maker.allowed_elements]


def eligible_accessories_for_roll(
    blade_id: str,
    element_id: str,
    rarity: Rarity,
) -> list[AccessoryPartSpec]:
    """Return non-empty accessories legal for `(blade, element, rarity)`."""
    blade = BLADES[blade_id]
    maker = MANUFACTURERS[blade.manufacturer]
    elem = ELEMENTS[element_id].element
    is_dot_elemental = elem in (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG)
    mode = maker.cleave_profile.mode

    valid: list[AccessoryPartSpec] = []
    for acc in ACCESSORIES.values():
        if acc.part_id == "Acc_None":
            continue
        if int(rarity) < int(acc.min_rarity):
            continue
        if acc.elemental_only and not is_dot_elemental:
            continue
        if acc.cleave_only and mode != TargetingMode.FRONTAL_CLEAVE:
            continue
        if acc.single_target_only and mode != TargetingMode.SINGLE_TARGET:
            continue
        valid.append(acc)
    return valid


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

    if parts.accessory_id != "Acc_None" and parts.accessory_id in ACCESSORIES:
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
    """Roll a complete procedural `MeleeWeaponInstance` with 3-4 parts."""
    rng = random.Random(seed) if seed is not None else random.Random()

    if manufacturer is None:
        maker = rng.choice(tuple(Manufacturer))
    else:
        maker = manufacturer

    blade = BLADE_BY_MANUFACTURER[maker]

    # Grip: weighted across all 5 melee grips (any grip can spawn on any blade)
    grip_id = _weighted_choice(rng, [(g.part_id, g.weight) for g in GRIPS.values()])

    # Element: strictly governed by Blade's ElementPolicy
    elem_choices = eligible_elements_for_blade(blade.part_id)
    element_id = _weighted_choice(rng, [(e.part_id, e.weight) for e in elem_choices])

    # Accessory: rolls according to rarity probability and blade/element compatibility
    acc_chance = ACCESSORY_ROLL_CHANCE.get(rarity, 0.5)
    acc_pool = eligible_accessories_for_roll(blade.part_id, element_id, rarity)
    if acc_pool and rng.random() < acc_chance:
        accessory_id = _weighted_choice(rng, [(a.part_id, a.weight) for a in acc_pool])
    else:
        accessory_id = "Acc_None"

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
