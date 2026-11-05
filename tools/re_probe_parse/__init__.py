"""Parsers for RE Probe (`re_probe.v1`) dumps.

Stdlib only: runs on any machine that has a copy of a dump directory — no
Borderlands 2, unrealsdk, or mods_base required. Importable offline so the
melee-arsenal repo can be syntax-checked without the game.

Typical usage:

    python -m re_probe_parse all sdk_mods/re_probe/dumps/20261105-143012 \
        sdk_mods/re_probe/dumps/*/traces/trace-*.json --out-dir re_probe_reports

See tools/re_probe_parse/README.md for the full command list and how the
outputs map back to assumptions in the melee-arsenal mod.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
