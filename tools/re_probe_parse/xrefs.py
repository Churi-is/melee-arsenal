"""Cross-reference report: which objects reference which.

Built from values actually read in defaults/instances/globals. Answers:
- what points at our custom balance definitions / weapon types / part definitions?
- which objects reference the vanilla donors the mod clones?
"""

from __future__ import annotations

from typing import Any

from .loader import Dump

# Targets we care about referrers for.
WANTED_TARGETS: list[str] = [
    "WillowGame.Default__WillowWeapon",
    # mod-owned paths (only present after definitions are built in-session)
    "MeleeArsenalDev.Balances.Balance_Melee_Hyperion_VeryRare",
    "MeleeArsenalDev.WeaponTypes.WeaponType_Hyperion_Melee",
]


def report_xrefs(dump: Dump) -> dict[str, Any]:
    xrefs = dump.section_items("xrefs")
    incoming: dict[str, list[dict[str, Any]]] = {}
    for x in xrefs:
        target = x.get("to")
        if target:
            incoming.setdefault(target, []).append(
                {"from": x.get("from"), "via": x.get("via"), "kind": x.get("kind")}
            )

    results: list[dict[str, Any]] = []
    for target in WANTED_TARGETS:
        refs = incoming.get(target, [])
        results.append({"target": target, "referrers": refs, "count": len(refs)})

    return {
        "dump_dir": dump.dump_dir,
        "dump_id": dump.dump_id,
        "total_xrefs": len(xrefs),
        "targets": results,
    }


def write_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Cross-reference report")
    lines.append("")
    lines.append(f"- Dump: `{result['dump_dir']}` (id: {result['dump_id']})")
    lines.append(f"- Total xref edges: {result['total_xrefs']}")
    lines.append("")
    for entry in result["targets"]:
        lines.append(f"## `{entry['target']}` — {entry['count']} referrer(s)")
        lines.append("")
        if not entry["referrers"]:
            lines.append("*No incoming references found in the read values.*")
        for r in entry["referrers"]:
            lines.append(f"- `{r.get('from')}` via `{r.get('via')}` (kind: {r.get('kind')})")
        lines.append("")
    return "\n".join(lines)
