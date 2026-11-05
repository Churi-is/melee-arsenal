"""UFunction signature report.

Answers: *which functions the mod touches actually exist in this build, with
exact parameter names, types, offsets, flags, and signatures* — so every
`hook("Class:Function", ...)` in `runtime/__init__.py` can be confirmed or
flagged as silently no-opping.
"""

from __future__ import annotations

from typing import Any

from .loader import Dump

# Every UFunction the mod hooks directly or calls through unrealsdk, gathered
# from runtime/__init__.py HOOKS, runtime/resolver.py, runtime/combat/*, and
# runtime/persistence_bridge.py. ENGINE_FACTS.md already flags two as known to
# not resolve (AddTrainingText, SetBottomStat) — this check verifies the rest.
WANTED_FUNCTIONS: list[str] = [
    # --- Tick / frame drivers ---
    "WillowGame.WillowPlayerController:PlayerTick",
    "WillowGame.WillowGameViewportClient:Tick",
    "Engine.GameViewportClient:Tick",
    # --- Weapon lifecycle & firing (the whole combat model rests on these) ---
    "WillowGame.WillowWeapon:InitializeInternal",
    "WillowGame.WillowWeapon:InitializeFromDefinitionData",
    "WillowGame.WillowWeapon:ChooseRandomNameParts",
    "WillowGame.WillowWeapon:PlayAnimation",
    "WillowGame.WillowWeapon:InitConstructPreview",
    "WillowGame.WillowWeapon:InstantFire",
    "WillowGame.WillowWeapon:ConsumeAmmo",
    "WillowGame.WillowWeapon:SharedFireAmmunition",
    "WillowGame.WillowWeapon:FiringModeDefinitionFire",
    "WillowGame.WillowWeapon:PlayFireEffects",
    "WillowGame.WillowWeapon:HasActiveAmmo",
    "WillowGame.WillowWeapon:StartReload",
    "WillowGame.WillowWeapon:StartZoom",
    "WillowGame.WillowWeapon:ClientStartZoom",
    "WillowGame.WillowWeapon:StopZoom",
    "WillowGame.WillowWeapon:ClientStopZoom",
    "WillowGame.WillowWeapon:CalcWeaponFire",
    "WillowGame.WillowWeapon:InstantFireStartTrace",
    "WillowGame.WillowWeapon:GetTraceImpact",
    "WillowGame.WillowWeapon:PlayImpactEffects",
    "WillowGame.WillowWeapon:ClearPendingFire",
    # --- Pawn / combat ---
    "WillowGame.WillowPawn:WeaponFired",
    "WillowGame.WillowAIPawn:TakeDamage",
    "WillowGame.WillowPlayerPawn:TakeDamage",
    "WillowGame.WillowPawn:IsEnemy",
    "WillowGame.WillowPawn:IsAliveAndWell",
    "WillowGame.WillowPawn:Behavior_CauseDamage",
    "WillowGame.WillowPawn:Behavior_CauseRadiusDamage",
    "WillowGame.WillowPawn:PlayAkEvent",
    # --- Damage tracker / HUD ---
    "WillowGame.WillowDamageTypeDefinition:DisplayRecentDamageForPlayer",
    "WillowGame.WillowHUDMovieBase:AddTrainingText",
    # --- Item card ---
    "WillowGame.ItemCardGFxObject:SetItemCard",
    "WillowGame.ItemCardGFxObject:SetItemCardEx",
    "WillowGame.ItemCardGFxObject:SetTitle",
    "WillowGame.ItemCardGFxObject:SetTopStat",
    "WillowGame.ItemCardGFxObject:SetFunStats",
    "WillowGame.ItemCardGFxObject:SetBottomStat",
    # --- Save / spawn / pools / mission ---
    "WillowGame.WillowPlayerController:ValidateWeaponDefinition",
    "WillowGame.WillowPlayerController:ApplyWeaponSaveGameData",
    "WillowGame.WillowPlayerController:GetHUDMovie",
    "WillowGame.WillowPlayerController:GetSaveGameManager",
    "WillowGame.WillowPlayerController:ServerGrantMissionRewards",
    "WillowGame.WillowSaveGameManager:SaveGame",
    "WillowGame.Behavior_SpawnLootAroundPoint:PlaceSpawnedItems",
    "WillowGame.Behavior_SpawnLootAroundPoint:ApplyBehaviorToContext",
    "WillowGame.WillowVendingMachine:ResetInventory",
]


def _param_summary(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": p.get("name"),
        "type": p.get("type"),
        "kind": p.get("kind"),
        "offset": p.get("offset"),
        "size": p.get("size"),
        "is_param": p.get("is_param"),
        "is_out": p.get("is_out"),
        "is_return": p.get("is_return"),
        "is_optional": p.get("is_optional"),
        "param_index": p.get("param_index"),
        "enum": p.get("enum"),
        "object_class": p.get("object_class"),
        "struct": p.get("struct"),
    }


def summarize_function(fn: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": fn.get("path"),
        "name": fn.get("name"),
        "owner": fn.get("owner"),
        "owner_name": fn.get("owner_name"),
        "package": fn.get("package"),
        "flags": fn.get("flags"),
        "flags_decoded": fn.get("flags_decoded"),
        "native": fn.get("native"),
        "static": fn.get("static"),
        "event": fn.get("event"),
        "latent": fn.get("latent"),
        "exec": fn.get("exec"),
        "private": fn.get("private"),
        "protected": fn.get("protected"),
        "num_params": fn.get("num_params"),
        "params_size": fn.get("params_size"),
        "return_value_offset": fn.get("return_value_offset"),
        "property_size": fn.get("property_size"),
        "return_type": fn.get("return_type"),
        "return_param": fn.get("return_param"),
        "display_signature": fn.get("display_signature"),
        "overrides": fn.get("overrides"),
        "bytecode_available": fn.get("bytecode_available"),
        "params": [_param_summary(p) for p in (fn.get("params") or [])],
        "locals": [_param_summary(p) for p in (fn.get("locals") or [])],
    }


def report_functions(dump: Dump) -> dict[str, Any]:
    index = dump.index_by_path("functions")
    wanted: list[dict[str, Any]] = []
    for path in WANTED_FUNCTIONS:
        fn = index.get(path)
        entry: dict[str, Any] = {"path": path, "found": fn is not None}
        if fn is not None:
            entry.update(summarize_function(fn))
        wanted.append(entry)

    all_fn = [summarize_function(fn) for fn in index.values()]
    all_fn.sort(key=lambda f: f["path"] or "")

    by_owner: dict[str, list[str]] = {}
    for f in all_fn:
        by_owner.setdefault(f["owner"] or "", []).append(f["path"] or "")

    return {
        "dump_dir": dump.dump_dir,
        "dump_id": dump.dump_id,
        "profile": dump.profile,
        "status": dump.status,
        "total_functions": len(all_fn),
        "wanted_checked": len(wanted),
        "wanted_found": sum(1 for e in wanted if e["found"]),
        "wanted_missing": [e["path"] for e in wanted if not e["found"]],
        "wanted": wanted,
        "by_owner": {k: sorted(v) for k, v in sorted(by_owner.items())},
    }


def write_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# UFunction signature report")
    lines.append("")
    lines.append(
        f"- Dump: `{result['dump_dir']}` (id: {result['dump_id']}, profile: {result['profile']})"
    )
    lines.append(f"- Total UFunctions in dump: {result['total_functions']}")
    lines.append(
        f"- Wanted functions checked: {result['wanted_checked']}  "
        f"found: {result['wanted_found']}  missing: {len(result['wanted_missing'])}"
    )
    if result["wanted_missing"]:
        lines.append(f"- Missing: {', '.join(result['wanted_missing'])}")
    lines.append("")
    lines.append("## Wanted functions (mod touchpoints)")
    lines.append("")
    lines.append(
        "| path | exists | native | params (offset: type name) | return | signature |"
    )
    lines.append("|---|---|---|---|---|---|")
    for entry in result["wanted"]:
        path = entry["path"]
        if not entry["found"]:
            lines.append(f"| `{path}` | **NO** | — | — | — | **not in dump — hook no-ops** |")
            continue
        params = ", ".join(
            f"{p['offset']}: {p['type']} {p['name']}"
            + (" (out)" if p["is_out"] else "")
            + (" (return)" if p["is_return"] else "")
            for p in (entry.get("params") or [])
        )
        ret = entry["return_type"] or (entry["params"][-1]["type"] if entry.get("params") else "")
        lines.append(
            f"| `{path}` | yes | {entry['native']} | {params} | {ret} | `{entry['display_signature']}` |"
        )
    lines.append("")
    lines.append("## Functions by owner class (top 30)")
    lines.append("")
    for owner, paths in list(result["by_owner"].items())[:30]:
        lines.append(f"- {owner or '(none)'}: {len(paths)}")
    return "\n".join(lines)
