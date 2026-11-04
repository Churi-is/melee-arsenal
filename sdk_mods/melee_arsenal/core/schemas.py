"""Backward-compatible facade for the old monolithic schemas module.

Part specs now live next to their registries in `core/parts/` (one module per part
type), and the composed-weapon value types live in `core/parts/weapon.py`. New code
should import from `core.parts`; this shim re-exports the historical names so
existing mods/tests/tools keep working unchanged.
"""
from __future__ import annotations

from .parts.accessories import AccessoryPartSpec
from .parts.blades import BladePartSpec
from .parts.common import GestaltFragmentMeta, StatModifier
from .parts.elements import ElementPartSpec
from .parts.grips import GripPartSpec
from .parts.manufacturers import CleaveProfile, ManufacturerSpec
from .parts.weapon import MeleeWeaponInstance, MeleeWeaponParts, PlayerMeleeContext

__all__ = [
    "AccessoryPartSpec",
    "BladePartSpec",
    "CleaveProfile",
    "ElementPartSpec",
    "GestaltFragmentMeta",
    "GripPartSpec",
    "ManufacturerSpec",
    "MeleeWeaponInstance",
    "MeleeWeaponParts",
    "PlayerMeleeContext",
    "StatModifier",
]
