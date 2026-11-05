"""Class property report.

Answers: *which properties the mod reads/writes on live objects actually exist
on each class (declared or inherited), with exact offsets, types, and flags.*

For every wanted class we walk `super_chain` so inherited members count — a
property only needs to exist somewhere up the chain for `hasattr`/`setattr` to
succeed on a live object.
"""

from __future__ import annotations

from typing import Any

from .loader import Dump

# Classes the mod's runtime reaches into, plus the property names each
# subsystem assumes exist on them (or their ancestors). Entries are checked
# against declared properties walked up `super_chain`.
WANTED_CLASSES: dict[str, list[str]] = {
    "WillowGame.WillowWeapon": [
        # Combat / firing properties written per swing (sync_live_weapon_properties)
        "InstantHitDamage",
        "InstantHitDamageBaseValue",
        "ProjectilesPerShot",
        "ClipSize",
        "ClipSizeBaseValue",
        "ReloadCnt",
        "Spread",
        "FireInterval",
        "FireIntervalBaseValue",
        "ShotCost",
        "ShotCostBaseValue",
        "StatusEffectDamage",
        "StatusEffectDamageBaseValue",
        "BaseStatusEffectChanceModifier",
        "BaseStatusEffectChanceModifierBaseValue",
        "StatusEffectChanceModifier",
        "StatusEffectChanceModifierBaseValue",
        # Identity / definition linkage
        "DefinitionData",
        "UniqueId",
        "GameStage",
        "ManufacturerGradeIndex",
        "RarityLevel",
        "InventoryRarityLevel",
        "Instigator",
        "Owner",
        "WeaponTypeDefinition",
        "BalanceDefinition",
        "BarrelPartDefinition",
        "GripPartDefinition",
        "ElementalPartDefinition",
        "MaterialPartDefinition",
        "BodyPartDefinition",
        "PrefixPartDefinition",
        "TitlePartDefinition",
        "CustomDamageTypeDefinition",
        "SightPartDefinition",
        "StockPartDefinition",
        "Accessory2PartDefinition",
        # ENGINE_FACTS asserts these do NOT exist (negative assertions to verify)
        "Mesh",
        "CurrentFiringModeDefinition",
        "bCanBeUsed",
    ],
    "WillowGame.WillowPlayerPawn": [
        "Weapon",
        "Arms",
        "Mesh",
        "Location",
        "EyeHeight",
        "BaseEyeHeight",
        "CylinderComponent",
        "PawnList",
        "NextPawn",
        "WorldInfo",
        "Controller",
        "IsEnemy",
        "IsAliveAndWell",
        "PlayAkEvent",
        "Behavior_CauseDamage",
        "Behavior_CauseRadiusDamage",
    ],
    "WillowGame.WillowPlayerController": [
        "Pawn",
        "WorldInfo",
        "PlayerReplicationInfo",
        "ExpLevel",
        "SaveGameName",
        "QuickSaveFileName",
    ],
    "WillowGame.WillowAIPawn": [
        "TakeDamage",
        "IsEnemy",
        "IsAliveAndWell",
        "WorldInfo",
    ],
    "WillowGame.WillowPawn": [
        "Location",
        "EyeHeight",
        "BaseEyeHeight",
        "CylinderComponent",
        "Weapon",
    ],
    "WillowGame.ItemCardGFxObject": [
        # 9-param SetTopStat; CompareArrow typed ETopStatArrow; SetTitle's Rarity is Color
    ],
    "WillowGame.WeaponBalanceDefinition": [
        "WeaponPartListCollection",
        "RuntimePartListCollection",
        "InventoryDefinition",
        "BaseDefinition",
        "Manufacturers",
        "PartListCollection",
        "bInterpolateExpLevel",
        # ENGINE_FACTS asserts these do NOT exist
        "MinGameStageIndex",
        "MaxGameStageIndex",
        "GameStageRequirement",
    ],
    "WillowGame.WeaponTypeDefinition": [
        "Presentation",
        "UIMeshRotation",
        "bUIMeshRotationNeedsTransformFixup",
        "FocusRadius",
        "MinimumVerticalPercentage",
        "MinimumHorizontalPercentage",
        "ThirdPersonMeshScale",
        "FireShake",
        "DefaultFiringModeDefinition",
        "ClipSize",
        "ShotCost",
        "Range",
        "Spread",
        "Typename",
        "FireSounds",
        "TitleList",
        "PrefixList",
    ],
    "WillowGame.WeaponPartDefinition": [
        "WeaponAttributeEffects",
        "ExternalAttributeEffects",
        "GestaltModeSkeletalMeshName",
        "AdditionalGestaltModeSkeletalMeshNames",
        "Material",
        "Rarity",
        "CustomDamageTypeDefinition",
        "TitleList",
        "PrefixList",
        "ManufacturerDefinitionForScopeMovie",
    ],
    "WillowGame.ItemPoolDefinition": [
        "BalancedItems",
        "Quantity",
        "MinGameStageRequirement",
        "MaxGameStageRequirement",
        "bAutoReadyItems",
        "bShopsHaveInfiniteQuantity",
        # ENGINE_FACTS: no ItemPool property
        "ItemPool",
    ],
    "WillowGame.MissionDefinition": [
        "GameStage",
        "bEnableAltReward",
        "Reward",
    ],
    "WillowGame.Behavior_SpawnLootAroundPoint": [
        "ItemPools",
        "CustomLocation",
        "CircularScatterRadius",
        "SpawnVelocityRelativeTo",
        "SpawnVelocity",
    ],
    "WillowGame.WillowVendingMachine": [
        "ResetInventory",
    ],
    "WillowGame.WillowSaveGameManager": [
        "SaveGame",
        "LastLoadedFilePath",
        "SaveGameFile",
    ],
    "WillowGame.WillowDamageTypeDefinition": [
        "DisplayRecentDamageForPlayer",
    ],
    "Core.Class": [
        "Name",
    ],
    "Engine.Class": [
        "Name",
    ],
}

# Structs whose field layout the mod relies on when constructing/cloning
# definitions (resolver.py, definitions/balances.py, definitions/parts.py).
WANTED_STRUCTS: list[str] = [
    "WillowGame.WillowWeaponTypes:WeaponDefinitionData",
    "WillowGame.ItemPoolDefinition:BalancedInventoryData",
    "Engine.AttributeInitializationDefinition:AttributeInitializationData",
    "WillowGame.ManufacturerCustomPartWeightData",
    "WillowGame.WeaponManufacturerGradeData",
    "Engine.Color",
    "Engine.Vector",
    "WillowGame.HitInfo",
]


def _prop_summary(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": p.get("name"),
        "type": p.get("type"),
        "kind": p.get("kind"),
        "offset": p.get("offset"),
        "size": p.get("size"),
        "end": p.get("end"),
        "flags": p.get("flags"),
        "flags_decoded": p.get("flags_decoded"),
        "declared_in": p.get("declared_in"),
        "enum": p.get("enum"),
        "object_class": p.get("object_class"),
        "struct": p.get("struct"),
        "array_dim": p.get("array_dim"),
        "element_size": p.get("element_size"),
        "is_param": p.get("is_param"),
        "is_out": p.get("is_out"),
        "is_return": p.get("is_return"),
    }


def _effective_properties(classes: dict[str, dict[str, Any]], path: str) -> dict[str, dict[str, Any]]:
    """Declared properties of `path` plus every ancestor in super_chain."""
    merged: dict[str, dict[str, Any]] = {}
    current = path
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        rec = classes.get(current)
        if rec:
            for p in rec.get("properties") or []:
                name = p.get("name")
                if name is not None and name not in merged:
                    merged[name] = _prop_summary(p)
        # climb
        supers = rec.get("super_chain") if rec else None
        current = supers[0] if supers else None
    return merged


def report_classes(dump: Dump) -> dict[str, Any]:
    classes = dump.index_by_path("classes")

    class_reports: list[dict[str, Any]] = []
    for class_path, wanted_props in WANTED_CLASSES.items():
        rec = classes.get(class_path)
        entry: dict[str, Any] = {
            "path": class_path,
            "found": rec is not None,
        }
        if rec is not None:
            entry.update(
                {
                    "name": rec.get("name"),
                    "package": rec.get("package"),
                    "super": rec.get("super"),
                    "super_chain": rec.get("super_chain"),
                    "subclasses": rec.get("subclasses"),
                    "cdo": rec.get("cdo"),
                    "property_size": rec.get("property_size"),
                    "declared_functions": rec.get("functions"),
                    "declared_properties": [_prop_summary(p) for p in (rec.get("properties") or [])],
                }
            )
            effective = _effective_properties(classes, class_path)
            props_found: list[dict[str, Any]] = []
            props_missing: list[str] = []
            for prop in wanted_props:
                if prop in effective:
                    props_found.append(effective[prop])
                else:
                    props_missing.append(prop)
            entry["wanted_properties_found"] = props_found
            entry["wanted_properties_missing"] = props_missing
            entry["wanted_properties_checked"] = wanted_props
        class_reports.append(entry)

    struct_reports: list[dict[str, Any]] = []
    structs = dump.index_by_path("structs")
    for struct_path in WANTED_STRUCTS:
        rec = structs.get(struct_path)
        entry: dict[str, Any] = {"path": struct_path, "found": rec is not None}
        if rec is not None:
            entry.update(
                {
                    "name": rec.get("name"),
                    "property_size": rec.get("property_size"),
                    "min_alignment": rec.get("min_alignment"),
                    "properties": [
                        {
                            "name": p.get("name"),
                            "type": p.get("type"),
                            "kind": p.get("kind"),
                            "offset": p.get("offset"),
                            "size": p.get("size"),
                            "end": p.get("end"),
                            "struct": p.get("struct"),
                            "enum": p.get("enum"),
                            "object_class": p.get("object_class"),
                            "array_dim": p.get("array_dim"),
                        }
                        for p in sorted(
                            rec.get("properties") or [],
                            key=lambda p: (p.get("offset") or 0, p.get("name") or ""),
                        )
                    ],
                }
            )
        struct_reports.append(entry)

    return {
        "dump_dir": dump.dump_dir,
        "dump_id": dump.dump_id,
        "total_classes": len(classes),
        "classes": class_reports,
        "structs": struct_reports,
    }


def write_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Class & struct report")
    lines.append("")
    lines.append(f"- Dump: `{result['dump_dir']}` (id: {result['dump_id']})")
    lines.append(f"- Total classes in dump: {result['total_classes']}")
    lines.append("")
    lines.append("## Wanted classes — property checks")
    lines.append("")
    lines.append(
        "| class | property | status | declared_in | offset | type | flags |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for entry in result["classes"]:
        path = entry["path"]
        if not entry["found"]:
            lines.append(f"| `{path}` | **class not loaded** | REFUTED | — | — | — | — |")
            continue
        found_names = {p["name"]: p for p in entry.get("wanted_properties_found") or []}
        for prop in entry.get("wanted_properties_checked") or []:
            p = found_names.get(prop)
            if p is None:
                lines.append(f"| `{path}` | `{prop}` | **MISSING** | — | — | — | — |")
            else:
                lines.append(
                    f"| `{path}` | `{prop}` | present | "
                    f"{p.get('declared_in') or '—'} | {p.get('offset')} | "
                    f"{p.get('type')} | {', '.join(p.get('flags_decoded') or []) or p.get('flags') or '—'} |"
                )
    lines.append("")
    lines.append("## Struct layouts")
    lines.append("")
    for entry in result["structs"]:
        path = entry["path"]
        if not entry["found"]:
            lines.append(f"### `{path}` — **not in dump**")
            continue
        lines.append(
            f"### `{path}` — size {entry.get('property_size')} align {entry.get('min_alignment')}"
        )
        lines.append("")
        lines.append("| offset | size | name | type | kind |"
        )
        lines.append("|---|---|---|---|---|")
        for p in entry.get("properties") or []:
            lines.append(
                f"| {p.get('offset')} | {p.get('size')} | `{p.get('name')}` | "
                f"{p.get('type')} | {p.get('kind')} |"
            )
        lines.append("")
    return "\n".join(lines)
