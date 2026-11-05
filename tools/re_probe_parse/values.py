"""CDO defaults, live instance values, and globals report.

Checks the exact values the mod hard-codes or guesses against what the engine
actually exposes:
- `Default__WillowWeapon` and other CDO property values (clip size, damage, spread...)
- Live `WillowWeapon_*` instances (what `IsEnemy`, `RarityLevel` etc. look like at runtime)
- Global constants (`MAX_PARTS`-style) and singletons
"""

from __future__ import annotations

from typing import Any

from .loader import Dump

# (class path, property name) — CDO defaults the mod's math and guards lean on.
WANTED_DEFAULTS: list[tuple[str, str]] = [
    ("WillowGame.WillowWeapon", "ClipSize"),
    ("WillowGame.WillowWeapon", "InstantHitDamage"),
    ("WillowGame.WillowWeapon", "Spread"),
    ("WillowGame.WillowWeapon", "FireInterval"),
    ("WillowGame.WillowWeapon", "ShotCost"),
    ("WillowGame.WillowWeapon", "RarityLevel"),
    ("WillowGame.WeaponTypeDefinition", "ClipSize"),
    ("WillowGame.WeaponTypeDefinition", "Range"),
    ("WillowGame.WeaponBalanceDefinition", "ClipSize"),
    ("WillowGame.ItemPoolDefinition", "Quantity"),
    ("WillowGame.ItemPoolDefinition", "bAutoReadyItems"),
    ("WillowGame.ItemPoolDefinition", "MinGameStageRequirement"),
    ("WillowGame.ItemPoolDefinition", "MaxGameStageRequirement"),
    ("WillowGame.MissionDefinition", "GameStage"),
    ("WillowGame.MissionDefinition", "bEnableAltReward"),
]

# Live object paths we'd like values for (only present if such objects exist in
# the session the dump was taken in).
WANTED_INSTANCES: list[str] = [
    "WillowGame.WillowWeapon_0",
    "WillowGame.WillowPlayerPawn_0",
    "WillowGame.WillowGameEngine_0",
    "WillowGame.WillowGame_0",
]


def report_values(dump: Dump) -> dict[str, Any]:
    defaults_index: dict[str, dict[str, Any]] = {}
    for rec in dump.section_items("defaults"):
        defaults_index[rec.get("class")] = rec

    instances_index: dict[str, dict[str, Any]] = {}
    for rec in dump.section_items("instances"):
        instances_index[rec.get("path")] = rec

    globals_list = dump.section_items("globals")

    default_results: list[dict[str, Any]] = []
    for class_path, prop in WANTED_DEFAULTS:
        rec = defaults_index.get(class_path)
        entry: dict[str, Any] = {
            "class": class_path,
            "property": prop,
            "cdo_has_defaults": rec is not None,
            "found": False,
            "value": None,
            "errors": rec.get("errors") if rec else None,
        }
        if rec is not None:
            value = (rec.get("values") or {}).get(prop)
            entry["found"] = value is not None or prop in (rec.get("values") or {})
            entry["value"] = value
            entry["cdo"] = rec.get("cdo")
        default_results.append(entry)

    instance_results: list[dict[str, Any]] = []
    for path in WANTED_INSTANCES:
        rec = instances_index.get(path)
        entry: dict[str, Any] = {"path": path, "found": rec is not None}
        if rec is not None:
            entry.update(
                {
                    "class": rec.get("class"),
                    "values": rec.get("values"),
                    "errors": rec.get("errors"),
                }
            )
        instance_results.append(entry)

    globals_summary = []
    for g in globals_list:
        if g.get("type") == "constant":
            globals_summary.append(
                {"type": "constant", "path": g.get("path"), "name": g.get("name"), "value": g.get("value")}
            )
        else:
            globals_summary.append(
                {
                    "type": "global",
                    "path": g.get("path"),
                    "class": g.get("class"),
                    "class_name": g.get("class_name"),
                    "value_count": len(g.get("values") or {}),
                }
            )

    return {
        "dump_dir": dump.dump_dir,
        "dump_id": dump.dump_id,
        "defaults": default_results,
        "defaults_found": sum(1 for d in default_results if d["found"]),
        "defaults_checked": len(default_results),
        "instances": instance_results,
        "instances_found": sum(1 for i in instance_results if i["found"]),
        "instance_candidates": dump.summary.get("instance_candidates"),
        "globals": globals_summary,
        "total_globals": len(globals_summary),
    }


def write_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Values report (CDO defaults, instances, globals)")
    lines.append("")
    lines.append(f"- Dump: `{result['dump_dir']}` (id: {result['dump_id']})")
    lines.append(f"- CDO defaults found: {result['defaults_found']} / {result['defaults_checked']}")
    lines.append(f"- Live instances found: {result['instances_found']} / {len(result['instances'])}")
    if result["instance_candidates"] is not None:
        lines.append(f"- Instance candidates vs written: {result['instance_candidates']} candidates")
    lines.append(f"- Global items: {result['total_globals']}")
    lines.append("")
    lines.append("## Wanted CDO defaults")
    lines.append("")
    lines.append("| class | property | cdo | value | status |")
    lines.append("|---|---|---|---|---|")
    for d in result["defaults"]:
        status = "present" if d["found"] else ("no CDO values" if not d["cdo_has_defaults"] else "ABSENT")
        value = d["value"]
        if isinstance(value, dict) and "$ref" in value:
            value = f"$ref → {value['$ref']}"
        lines.append(
            f"| {d['class']} | {d['property']} | {d.get('cdo') or '—'} | {value} | {status} |"
        )
    lines.append("")
    if result["defaults_found"] < result["defaults_checked"]:
        lines.append("## Missing defaults")
        for d in result["defaults"]:
            if not d["found"]:
                lines.append(f"- {d['class']}.{d['property']}")
        lines.append("")
    lines.append("## Wanted live instances")
    lines.append("")
    for i in result["instances"]:
        if i["found"]:
            vals = i.get("values") or {}
            sample = ", ".join(f"{k}={v!r}" for k, v in list(vals.items())[:6])
            lines.append(f"- `{i['path']}` ({i['class']}): {sample}")
        else:
            lines.append(f"- `{i['path']}` — **not present in this session**")
    lines.append("")
    lines.append("## Globals (sample)")
    lines.append("")
    for g in result["globals"][:20]:
        if g["type"] == "constant":
            lines.append(f"- constant `{g['path']}` = {g['value']!r}")
        else:
            lines.append(f"- global `{g['path']}` ({g['class_name']}) — {g['value_count']} values")
    return "\n".join(lines)
