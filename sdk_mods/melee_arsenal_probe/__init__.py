"""Melee Arsenal Ground-Truth Probe — a developer mod that measures BL2, so the main mod can stop guessing.

`melee_arsenal` is long because it cannot see the engine at development time: it probes for
properties with `hasattr`, hunts assets by keyword score, registers two hooks per intent, and learns
icon strings while you play. This probe replaces all of that trial and error with one recorded
observation per question — and each section names the runtime symbols its answer would delete.

It is standalone on purpose: it never imports `melee_arsenal`, so it keeps working when the mod
under test fails to load.

Workflow:

1. Install this folder into the game's `sdk_mods/` next to `melee_arsenal` (`./melee probe` does it).
2. Enable it, load a map, and play for a minute: swing, guard, throw, open an inventory card,
   look at a vanilla gun's card, and hit a target dummy.
3. Run `probe_dump` (or MODS -> Melee Arsenal Probe -> Dump Ground Truth).
4. Copy `ground_truth/ground_truth.json` and `ground_truth/GROUND_TRUTH.md` into the repo.

Console commands: `probe_dump`, `probe_schema`, `probe_assets`, `probe_live`, `probe_census`,
`probe_reset`, `probe_plan`, `probe_where`. See `README.md` for what each one answers.
"""
from __future__ import annotations

__version__ = "1.0.0"
__author__ = "MeleeArsenal"

try:
    import mods_base  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - offline tooling / CI syntax check
    mods_base = None  # type: ignore[assignment]

if mods_base is not None:
    from .mod_surface import build_probe_mod

    mod = build_probe_mod(version=__version__, author=__author__)
else:  # pragma: no cover - only outside the game
    mod = None


__all__ = ["mod", "__version__"]
