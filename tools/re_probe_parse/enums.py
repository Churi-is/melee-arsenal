"""Enum value report.

The SDK does not expose `ETopStatArrow._names()`, so the mod hard-codes the
byte mapping `NONE=0, BETTER=1, EQUAL=2, WORSE=3` (ui_item_card.py). RE Probe
dumps `UEnum.values` directly, so we can confirm or replace that guess.
"""

from __future__ import annotations

from typing import Any

from .loader import Dump

WANTED_ENUMS: list[str] = [
    "WillowGame.ETopStatArrow",
    # Context enums the mod's part catalogs reference by name elsewhere
    "WillowGame.EAmmoType",
    "WillowGame.EControllerMode",
    "WillowGame.EWeaponFireType",
]


def report_enums(dump: Dump) -> dict[str, Any]:
    index = dump.index_by_path("enums")
    results: list[dict[str, Any]] = []
    for path in WANTED_ENUMS:
        rec = index.get(path)
        entry: dict[str, Any] = {"path": path, "found": rec is not None}
        if rec is not None:
            entry.update(
                {
                    "name": rec.get("name"),
                    "package": rec.get("package"),
                    "value_count": rec.get("value_count"),
                    "values": [
                        {"name": v.get("name"), "value": v.get("value"), "alias_of": v.get("alias_of")}
                        for v in sorted(rec.get("values") or [], key=lambda v: (v.get("value") or 0, v.get("name") or ""))
                    ],
                }
            )
        results.append(entry)
    return {
        "dump_dir": dump.dump_dir,
        "dump_id": dump.dump_id,
        "total_enums": len(index),
        "enums": results,
    }


def write_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Enum report")
    lines.append("")
    lines.append(f"- Dump: `{result['dump_dir']}` (id: {result['dump_id']})")
    lines.append(f"- Total enums in dump: {result['total_enums']}")
    lines.append("")
    for entry in result["enums"]:
        path = entry["path"]
        if not entry["found"]:
            lines.append(f"### `{path}` — **not in dump**")
            continue
        lines.append(f"### `{path}` ({entry['name']}) — {entry['value_count']} values")
        lines.append("")
        lines.append("| value | name | alias_of |")
        lines.append("|---|---|---|")
        for v in entry.get("values") or []:
            lines.append(f"| {v['value']} | `{v['name']}` | {v.get('alias_of') or '—'} |")
        lines.append("")
        if entry["name"] == "ETopStatArrow":
            arrow_map = {v["name"]: v["value"] for v in (entry.get("values") or [])}
            lines.append(
                "**Mod assumption:** `CompareArrow` bytes `NONE=0, BETTER=1, EQUAL=2, WORSE=3` "
                f"→ observed mapping: {arrow_map}."
            )
            lines.append("")
    return "\n".join(lines)
