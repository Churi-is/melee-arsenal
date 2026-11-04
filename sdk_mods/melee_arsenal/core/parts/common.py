"""Shared building blocks for the Melee Arsenal part catalog (pure Python, no SDK).

`core/parts/` is the **data layer** of the mod. Each gameplay part type has its own
module (`blades.py`, `grips.py`, `accessories.py`, `elements.py`, `manufacturers.py`)
that owns its spec dataclass, its complete registry, derived indexes, and all the
SDK-independent logic for that part type. This module holds only the pieces that are
genuinely shared between them.

Nothing in this package imports `unrealsdk`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..enums import Manufacturer, ModifierMode, StatId


@dataclass(frozen=True, slots=True)
class StatModifier:
    """Single stat modifier mirroring Gearbox's AttributeEffectData."""

    stat: StatId
    mode: ModifierMode
    value: float


@dataclass(frozen=True, slots=True)
class GestaltFragmentMeta:
    """Metadata mapping a part to a custom or donor GestaltSkeletalMesh fragment.

    Compatible with `bl2-part-pipeline` / `Ribs Arsenal` `.upk` fragment registration:
    before custom `.upk` blade/grip meshes are cooked, `custom_package` is None and the
    SDK bridge binds `donor_fragment` from the host weapon type's gestalt mesh.
    """

    fragment_name: str
    donor_fragment: str
    custom_package: str | None = None
    first_index: int | None = None
    num_primitives: int | None = None
    bounds_min: tuple[float, float, float] | None = None
    bounds_max: tuple[float, float, float] | None = None
    sockets: tuple[str, ...] = ("Muzzle",)


def resolve_prefix(
    prefix_by_manufacturer: Mapping[Manufacturer, str],
    default_prefix: str,
    manufacturer: Manufacturer,
) -> str:
    """Manufacturer-specific item-card prefix, falling back to the part's default."""
    return prefix_by_manufacturer.get(manufacturer, default_prefix)


def weighted_choice(rng: random.Random, items: Sequence[tuple[str, float]]) -> str:
    """Pick a part id using its roll weight (negative/zero weights never win)."""
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
