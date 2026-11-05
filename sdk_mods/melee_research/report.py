"""Bounded evidence extraction from RE Probe's completed JSON shards."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Specific engineering questions, not claims that missing reflection means absence.
QUERIES = {
    "weight_and_pool": ["WeightedInventoryBalanceData", "AttributeInitializationData", "ManufacturerCustomPartWeightData", "ManufacturerGrade", "ItemPoolDefinition", "Pool_Weapons_All", "Weight_0_", "Weight_1_", "Weight_2_", "Weight_3_", "Weight_4_", "Weight_5_", "Weight_6_", "ValueFormula"],
    "weapon_and_save": ["WeaponDefinitionData", "WillowWeapon", "WeaponBalanceDefinition", "WeaponPartDefinition", "SaveGame", "ValidateWeaponDefinition", "ServerGrantMissionRewards"],
    "combat_and_ui": ["WillowPlayerPawn", "WillowAIPawn", "WillowPlayerController", "ItemCardGFxObject", "ETopStatArrow", "FiringModeDefinition", "Behavior_Explode", "Behavior_CauseDamage", "SkeletalMeshComponent", "AnimNodeSlot"],
    "custom_objects": ["MeleeArsenalDev", "FM_Melee", "Pool_Melee"],
}
SECTIONS = ("classes", "functions", "structs", "enums", "consts", "objects", "assets", "defaults", "instances", "globals")
LIMITS = {"classes": 160, "functions": 400, "structs": 120, "enums": 50, "consts": 100, "objects": 180, "assets": 100, "defaults": 100, "instances": 100, "globals": 30}


def _read(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def build_report(dump: Path, traces: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = _read(dump / "manifest.json")
    result: dict[str, Any] = {
        "schema": "melee_research.v1", "dump_directory": str(dump),
        "manifest": manifest, "queries": QUERIES, "sections": {},
        "traces": traces, "snapshots": snapshots, "errors": [],
        "caveats": ["Matches are evidence, not assertions. Missing entries do not prove an API or asset is absent.",
                    "Values and instances are capped by RE Probe; defaults are declared-only unless configured otherwise.",
                    "Traces cover only armed ProcessEvent calls during the described action; null caller is outside the hooked set, not necessarily native."],
    }
    needles = [s.lower() for group in QUERIES.values() for s in group]
    for section in SECTIONS:
        info = manifest.get("sections", {}).get(section, {})
        selected: list[Any] = []
        matched = 0
        scanned = 0
        for shard in info.get("shards", []):
            path = dump / shard["path"]
            try:
                doc = _read(path)
                if doc.get("section") != section or not isinstance(doc.get("items"), list):
                    raise ValueError("invalid section or items")
                for item in doc["items"]:
                    scanned += 1
                    # Match identity only: searching all properties makes broad classes swamp the report.
                    identity = " ".join(str(item.get(k) or "") for k in ("path", "name", "class", "class_name", "cdo")) if isinstance(item, dict) else ""
                    if any(n in identity.lower() for n in needles):
                        matched += 1
                        if len(selected) < LIMITS[section]:
                            selected.append(item)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result["errors"].append({"file": str(path), "error": str(exc)})
        result["sections"][section] = {"scanned": scanned, "matched": matched, "included": len(selected), "truncated": matched > len(selected), "items": selected}
    return result


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False, default=str)
    tmp.replace(path)
