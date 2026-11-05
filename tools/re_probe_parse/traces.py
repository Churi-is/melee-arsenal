"""Runtime trace report: observed caller → callee edges.

Answers the dynamic questions reflection cannot:
- who calls `InstantFire`, `ConsumeAmmo`, `SharedFireAmmunition` per swing?
- does `SharedFireAmmunition` ever fire while a blade is airborne?
- do all four zoom functions fire for RMB guard/throw?
- is `PlayerTick` the real tick driver and does anything hook `Engine.GameViewportClient:Tick`?
- who calls `Behavior_CauseDamage`, `TakeDamage`, `DisplayRecentDamageForPlayer`?
- which objects receive the calls (WillowWeapon_0 etc.) and what were sample args?
"""

from __future__ import annotations

import json
from typing import Any

from .loader import Dump

# Combat functions the mod's call-graph assumptions revolve around.
WANTED_CALLEES: list[str] = [
    "WillowGame.WillowWeapon:InstantFire",
    "WillowGame.WillowWeapon:ConsumeAmmo",
    "WillowGame.WillowWeapon:SharedFireAmmunition",
    "WillowGame.WillowWeapon:FiringModeDefinitionFire",
    "WillowGame.WillowWeapon:PlayFireEffects",
    "WillowGame.WillowWeapon:HasActiveAmmo",
    "WillowGame.WillowWeapon:StartReload",
    "WillowGame.WillowWeapon:StartZoom",
    "WillowGame.WillowWeapon:StopZoom",
    "WillowGame.WillowWeapon:ClientStartZoom",
    "WillowGame.WillowWeapon:ClientStopZoom",
    "WillowGame.WillowWeapon:CalcWeaponFire",
    "WillowGame.WillowWeapon:PlayImpactEffects",
    "WillowGame.WillowWeapon:PlayAnimation",
    "WillowGame.WillowWeapon:InitializeInternal",
    "WillowGame.WillowWeapon:ChooseRandomNameParts",
    "WillowGame.WillowWeapon:InitConstructPreview",
    "WillowGame.WillowPawn:Behavior_CauseDamage",
    "WillowGame.WillowPawn:Behavior_CauseRadiusDamage",
    "WillowGame.WillowAIPawn:TakeDamage",
    "WillowGame.WillowPlayerPawn:TakeDamage",
    "WillowGame.WillowPlayerController:PlayerTick",
    "WillowGame.WillowPlayerController:ServerGrantMissionRewards",
    "WillowGame.WillowPlayerController:ValidateWeaponDefinition",
    "WillowGame.WillowPlayerController:ApplyWeaponSaveGameData",
    "WillowGame.ItemCardGFxObject:SetTopStat",
    "WillowGame.ItemCardGFxObject:SetTitle",
    "WillowGame.ItemCardGFxObject:SetItemCard",
    "WillowGame.ItemCardGFxObject:SetItemCardEx",
    "WillowGame.ItemCardGFxObject:SetFunStats",
    "WillowGame.Behavior_SpawnLootAroundPoint:PlaceSpawnedItems",
    "WillowGame.WillowVendingMachine:ResetInventory",
    "WillowGame.WillowSaveGameManager:SaveGame",
    "WillowGame.WillowDamageTypeDefinition:DisplayRecentDamageForPlayer",
    "WillowGame.WillowHUDMovieBase:AddTrainingText",
    "WillowGame.WillowPawn:IsEnemy",
    "WillowGame.WillowPawn:IsAliveAndWell",
    "WillowGame.WillowPawn:PlayAkEvent",
    "Engine.GameViewportClient:Tick",
]


def _load_trace(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def report_trace(trace_path: str) -> dict[str, Any]:
    doc = _load_trace(trace_path)
    session = doc.get("session") or {}
    funcs = doc.get("functions") or []
    edges = doc.get("edges") or []

    func_index = {f.get("path"): f for f in funcs}

    wanted: list[dict[str, Any]] = []
    for callee in WANTED_CALLEES:
        f = func_index.get(callee)
        entry: dict[str, Any] = {"path": callee, "found": f is not None}
        if f is not None:
            entry.update(
                {
                    "calls": f.get("calls"),
                    "callers": f.get("callers") or [],
                    "callees": f.get("callees") or [],
                    "objects": f.get("objects") or [],
                    "objects_truncated": f.get("objects_truncated"),
                }
            )
        wanted.append(entry)

    edge_rows = []
    for e in edges:
        edge_rows.append(
            {
                "caller": e.get("caller"),
                "callee": e.get("callee"),
                "count": e.get("count"),
                "max_depth": e.get("max_depth"),
                "sample_count": len(e.get("samples") or []),
                "sample_args": (e.get("samples") or [])[:2],
            }
        )
    edge_rows.sort(key=lambda r: r["count"] or 0, reverse=True)

    # Aggregate caller→callee counts for wanted callees.
    callee_callers: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        callee_callers.setdefault(e.get("callee"), []).append(
            {"caller": e.get("caller"), "count": e.get("count"), "max_depth": e.get("max_depth")}
        )

    return {
        "trace_path": trace_path,
        "session": session,
        "wanted": wanted,
        "edges_total": len(edges),
        "edges_shown": len(edge_rows),
        "top_edges": edge_rows[:40],
        "callee_callers": {
            k: sorted(v, key=lambda r: r["count"] or 0, reverse=True)
            for k, v in sorted(callee_callers.items())
        },
        "notes": doc.get("notes") or [],
    }


def write_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Runtime trace report")
    lines.append("")
    session = result.get("session") or {}
    lines.append(f"- Trace file: `{result['trace_path']}`")
    lines.append(f"- Session: {session.get('session_id')} — hooks: {session.get('hooks')} — "
                 f"calls: {session.get('calls')} — edges: {session.get('edges')} — "
                 f"functions: {session.get('functions')} — samples: {session.get('samples')}")
    lines.append(f"- Patterns: {session.get('patterns')}")
    lines.append("")
    lines.append("## Wanted callee coverage")
    lines.append("")
    lines.append("| callee | calls | callers | objects |")
    lines.append("|---|---|---|---|")
    for entry in result["wanted"]:
        if not entry["found"]:
            lines.append(f"| `{entry['path']}` | **not hooked / not called** | — | — |")
            continue
        callers = entry.get("callers") or []
        caller_desc = ", ".join(
            f"{c.get('path') or 'null'}×{c.get('count')}" for c in callers[:4]
        )
        objects = entry.get("objects") or []
        obj_desc = ", ".join(f"{o.get('path')}×{o.get('count')}" for o in objects[:3])
        lines.append(f"| `{entry['path']}` | {entry.get('calls')} | {caller_desc or '—'} | {obj_desc or '—'} |")
    lines.append("")
    lines.append("## Top caller → callee edges")
    lines.append("")
    lines.append("| caller | callee | count | max_depth | samples |")
    lines.append("|---|---|---|---|---|")
    for e in result["top_edges"]:
        lines.append(
            f"| {e['caller'] or 'null'} | `{e['callee']}` | {e['count']} | {e['max_depth']} | {e['sample_count']} |"
        )
    lines.append("")
    if result.get("notes"):
        lines.append("## Session notes")
        for n in result["notes"]:
            lines.append(f"- {n}")
    return "\n".join(lines)
