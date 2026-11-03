"""Complete-weapon value types and the logic that composes parts together.

The registries themselves live in the per-part modules; this module holds the
SDK-independent representation of a rolled/saved weapon (`MeleeWeaponParts`,
`MeleeWeaponInstance`, `PlayerMeleeContext`), a `ResolvedParts` bundle for any
consumer that needs every spec at once, and weapon-name resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..enums import Element, Rarity
from .accessories import ACCESSORIES, NO_ACCESSORY_ID, AccessoryPartSpec
from .blades import BladePartSpec, BLADES
from .elements import NO_ELEMENT_ID, ELEMENTS, ElementPartSpec
from .grips import GRIPS, GripPartSpec
from .manufacturers import MANUFACTURERS, ManufacturerSpec


@dataclass(frozen=True, slots=True)
class MeleeWeaponParts:
    """The 3-4 gameplay parts composing a specific melee weapon."""

    blade_id: str
    grip_id: str
    accessory_id: str             # "Acc_None" when rolled with 3 parts (Blade + Grip + Element/None)
    element_id: str               # "Elem_None" when non-elemental

    @property
    def active_part_count(self) -> int:
        """Count of non-empty gameplay parts (2 to 4; typically 3-4 with accessory/element)."""
        count = 2  # Blade + Grip are always present
        if self.accessory_id != NO_ACCESSORY_ID:
            count += 1
        if self.element_id != NO_ELEMENT_ID:
            count += 1
        return count


@dataclass(frozen=True, slots=True)
class MeleeWeaponInstance:
    """Complete SDK-independent representation of a rolled or saved melee weapon."""

    unique_id: int
    game_stage: int               # 1..90 (1..80 + OP1..OP10)
    rarity: Rarity
    parts: MeleeWeaponParts
    grade_index: int = 0


@dataclass(frozen=True, slots=True)
class PlayerMeleeContext:
    """Snapshot of the player's live melee/gun bonuses read at swing time.

    Allows player melee skills (Zer0, Krieg, Gaige, BAR, Bladed relics/COMs) and depleted
    Roid shields to stack directly on top of the melee weapon's gun-scaled card damage!
    """

    melee_damage_scale: float = 1.0       # 1.0 = +0% melee bonus; 2.5 = +150% melee bonus
    gun_or_all_damage_scale: float = 1.0  # Optional global/all-damage bonus from skills/relics
    roid_flat_damage: float = 0.0         # Active Roid shield AmplifyDamage when shield == 0
    bonus_crit_damage: float = 0.0        # Extra additive crit bonus from player skills
    status_damage_scale: float = 1.0      # Elemental status damage multiplier (e.g. Maya Flicker/Miasma, Krieg Hellborn)
    status_chance_scale: float = 1.0      # Elemental status chance multiplier
    swing_speed_scale: float = 1.0        # Attack speed bonuses (e.g. fire rate / melee speed skills)
    is_uvhm: bool = False                 # Controls Slag multiplier (2.0x NVHM/TVHM vs 3.0x UVHM)
    extra_tags: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedParts:
    """Every spec behind a `MeleeWeaponParts`, resolved once for math/card logic."""

    blade: BladePartSpec
    grip: GripPartSpec
    accessory: AccessoryPartSpec
    element: ElementPartSpec
    manufacturer: ManufacturerSpec


def resolve_parts(parts: MeleeWeaponParts) -> ResolvedParts:
    """Resolve all four part ids (plus the Blade's manufacturer) into one bundle."""
    blade = BLADES[parts.blade_id]
    return ResolvedParts(
        blade=blade,
        grip=GRIPS[parts.grip_id],
        accessory=ACCESSORIES[parts.accessory_id],
        element=ELEMENTS[parts.element_id],
        manufacturer=MANUFACTURERS[blade.manufacturer],
    )


def resolve_weapon_name(
    blade_id: str,
    grip_id: str,
    accessory_id: str,
    element_id: str,
) -> tuple[str, str, str]:
    """Return `(prefix, title, full_name)` for a melee weapon's parts.

    Priority matches BL2 conventions:
    1. Accessory prefix (if an accessory is present)
    2. Elemental prefix (if no accessory, and element has a prefix)
    3. Empty prefix otherwise
    Title is determined by the Blade + Grip manufacturer matrix.
    """
    resolved = resolve_parts(MeleeWeaponParts(blade_id, grip_id, accessory_id, element_id))
    maker = resolved.blade.manufacturer
    title = resolved.blade.title_for(resolved.grip.manufacturer)

    prefix = ""
    if resolved.accessory.part_id != NO_ACCESSORY_ID:
        prefix = resolved.accessory.prefix_for(maker)
    elif resolved.element.element not in (Element.NONE, Element.EXPLOSIVE):
        prefix = resolved.element.prefix_for(maker)

    full_name = f"{prefix} {title}".strip() if prefix else title
    return prefix, title, full_name
