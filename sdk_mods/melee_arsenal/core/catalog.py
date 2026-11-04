"""Backward-compatible facade for the old monolithic part catalog.

The catalog now lives in `core/parts/`, one module per part type (`blades.py`,
`grips.py`, `accessories.py`, `elements.py`, `manufacturers.py`). New code should
import from `core.parts` (or the individual part module); this shim only re-exports
the historical names so existing mods/tests/tools keep working unchanged.
"""
from __future__ import annotations

from .parts.accessories import ACCESSORIES
from .parts.blades import BLADES, BLADE_BY_MANUFACTURER
from .parts.elements import ELEMENTS, ELEMENT_PART_BY_TYPE
from .parts.grips import GRIPS
from .parts.manufacturers import MANUFACTURERS
from .parts.weapon import resolve_weapon_name

__all__ = [
    "ACCESSORIES",
    "BLADES",
    "BLADE_BY_MANUFACTURER",
    "ELEMENTS",
    "ELEMENT_PART_BY_TYPE",
    "GRIPS",
    "MANUFACTURERS",
    "resolve_weapon_name",
]
