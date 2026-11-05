"""Melee Arsenal — Borderlands 2 New Weapon Type Mod.

- `melee_arsenal.core`: 100% SDK-independent CPython data & math layer (5 Blade-defined
  manufacturers, 3–4 part system, gun-scaled flat damage + player melee/Roid stacking,
  single-target & frontal-cleave 3D targeting, custom item cards, loot roller, and persistence).
- `melee_arsenal.runtime`: Live Borderlands 2 PythonSDK integration for
  Willow2 Mod Manager v3.8+ (mods_base 1.12+ / unrealsdk 3.2 / pyunrealsdk 1.10)
  built against verified in-game object paths and function signatures.
"""
from __future__ import annotations

from . import core

__version__ = "0.4.6"
__author__ = "MeleeArsenal"

try:
    import unrealsdk  # type: ignore[import-not-found]
    import mods_base  # type: ignore[import-not-found]
except ImportError:
    unrealsdk = None  # type: ignore[assignment]
    mods_base = None  # type: ignore[assignment]
    runtime = None  # type: ignore[assignment]
    mod = None
else:
    from . import runtime

    mod = runtime.build_runtime_mod(version=__version__, author=__author__)

__all__ = ["core", "runtime", "mod", "__version__"]
