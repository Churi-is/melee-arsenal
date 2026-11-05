"""Embedded RE Probe dump parser for the runner mod.

Stdlib-only (json, glob, os, pathlib).  Reads a completed RE Probe dump
directory and returns plain-Python section data that the findings builder
turns into a markdown + json report.  Safe to run in-game (no unrealsdk
dependency), so ``mrp_parse <dump_dir>`` works from the console.
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any


class _Dump:
    """Minimal in-game dump reader: manifest-authoritative, glob fallback."""

    def __init__(self, dump_dir: str) -> None:
        self.dump_dir = os.path.abspath(dump_dir)
        if not os.path.isdir(self.dump_dir):
            raise FileNotFoundError(f"dump directory not found: {self.dump_dir}")
        manifest_path = os.path.join(self.dump_dir, "manifest.json")
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                self.manifest = json.load(fh)
        except (OSError, ValueError):
            self.manifest = {}
        self.sections = self.manifest.get("sections") or {}
        self.summary = self.manifest.get("summary") or {}
        self.status = self.manifest.get("status")
        self.dump_id = self.manifest.get("dump_id") or os.path.basename(self.dump_dir)

    def _load_file(self, path: str) -> Any:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def section_items(self, section: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        paths = self._shard_paths(section)
        for path in paths:
            doc = self._load_file(path)
            if isinstance(doc, dict):
                items.extend(doc.get("items") or [])
        return items

    def _shard_paths(self, section: str) -> list[str]:
        paths: list[str] = []
        sec = self.sections.get(section)
        if isinstance(sec, dict):
            for shard in sec.get("shards") or []:
                p = shard.get("path")
                if p:
                    paths.append(p if os.path.isabs(p) else os.path.join(self.dump_dir, p))
        if not paths:
            paths = sorted(glob.glob(os.path.join(self.dump_dir, f"{section}-*.json")))
        return paths

    def index_by_path(self, section: str) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for item in self.section_items(section):
            path = item.get("path")
            if isinstance(path, str):
                index[path] = item
        return index

    def trace_files(self) -> list[str]:
        paths: list[str] = []
        sec = self.sections.get("traces")
        if isinstance(sec, dict):
            for shard in sec.get("shards") or []:
                p = shard.get("path")
                if p:
                    paths.append(p if os.path.isabs(p) else os.path.join(self.dump_dir, p))
        if not paths:
            paths = sorted(glob.glob(os.path.join(self.dump_dir, "traces", "*.json")))
        return paths


# ---------------------------------------------------------------------------
# Section readers
# ---------------------------------------------------------------------------

WANTED_FUNCTIONS: list[str] = [
    "WillowGame.WillowPlayerController:PlayerTick",
    "WillowGame.WillowWeapon:InstantFire",
    "WillowGame.WillowWeapon:ConsumeAmmo",
    "WillowGame.WillowWeapon:SharedFireAmmunition",
    "WillowGame.WillowWeapon:FiringModeDefinitionFire",
    "WillowGame.WillowWeapon:PlayFireEffects",
    "WillowGame.WillowWeapon:HasActiveAmmo",
    "WillowGame.WillowWeapon:StartZoom",
    "WillowGame.WillowWeapon:StopZoom",
    "WillowGame.WillowWeapon:ClientStartZoom",
    "WillowGame.WillowWeapon:ClientStopZoom",
    "WillowGame.WillowWeapon:InitializeInternal",
    "WillowGame.WillowWeapon:ChooseRandomNameParts",
    "WillowGame.WillowWeapon:InitConstructPreview",
    "WillowGame.WillowPawn:Behavior_CauseDamage",
    "WillowGame.WillowPawn:Behavior_CauseRadiusDamage",
    "WillowGame.WillowPawn:IsEnemy",
    "WillowGame.WillowPawn:IsAliveAndWell",
    "WillowGame.WillowPawn:PlayAkEvent",
    "WillowGame.WillowPawn:WeaponFired",
    "WillowGame.WillowAIPawn:TakeDamage",
    "WillowGame.WillowPlayerPawn:TakeDamage",
    "WillowGame.WillowPlayerController:ServerGrantMissionRewards",
    "WillowGame.WillowPlayerController:ValidateWeaponDefinition",
    "WillowGame.WillowPlayerController:ApplyWeaponSaveGameData",
    "WillowGame.WillowSaveGameManager:SaveGame",
    "WillowGame.ItemCardGFxObject:SetTopStat",
    "WillowGame.ItemCardGFxObject:SetTitle",
    "WillowGame.ItemCardGFxObject:SetItemCard",
    "WillowGame.ItemCardGFxObject:SetItemCardEx",
    "WillowGame.ItemCardGFxObject:SetFunStats",
    "WillowGame.ItemCardGFxObject:SetBottomStat",
    "WillowGame.WillowDamageTypeDefinition:DisplayRecentDamageForPlayer",
    "WillowGame.WillowHUDMovieBase:AddTrainingText",
    "WillowGame.Behavior_SpawnLootAroundPoint:PlaceSpawnedItems",
    "WillowGame.WillowVendingMachine:ResetInventory",
    "Engine.GameViewportClient:Tick",
]

WANTED_CLASSES: dict[str, list[str]] = {
    "WillowGame.WillowWeapon": [
        "InstantHitDamage", "InstantHitDamageBaseValue", "ProjectilesPerShot",
        "ClipSize", "ClipSizeBaseValue", "ReloadCnt", "Spread", "FireInterval",
        "FireIntervalBaseValue", "ShotCost", "ShotCostBaseValue",
        "StatusEffectDamage", "StatusEffectDamageBaseValue",
        "BaseStatusEffectChanceModifier", "BaseStatusEffectChanceModifierBaseValue",
        "StatusEffectChanceModifier", "StatusEffectChanceModifierBaseValue",
        "DefinitionData", "UniqueId", "GameStage", "ManufacturerGradeIndex",
        "RarityLevel", "InventoryRarityLevel", "Instigator", "Owner",
        "WeaponTypeDefinition", "BalanceDefinition", "BarrelPartDefinition",
        "GripPartDefinition", "ElementalPartDefinition", "MaterialPartDefinition",
        "BodyPartDefinition", "PrefixPartDefinition", "TitlePartDefinition",
        "CustomDamageTypeDefinition", "SightPartDefinition", "StockPartDefinition",
        "Accessory2PartDefinition",
        # Negative assertions (should NOT exist):
        "Mesh", "CurrentFiringModeDefinition", "bCanBeUsed",
    ],
    "WillowGame.WeaponBalanceDefinition": [
        "WeaponPartListCollection", "RuntimePartListCollection", "InventoryDefinition",
        "BaseDefinition", "Manufacturers", "PartListCollection", "bInterpolateExpLevel",
        "MinGameStageIndex", "MaxGameStageIndex", "GameStageRequirement",
    ],
    "WillowGame.ItemPoolDefinition": [
        "BalancedItems", "Quantity", "MinGameStageRequirement",
        "MaxGameStageRequirement", "bAutoReadyItems", "bShopsHaveInfiniteQuantity",
        "ItemPool",
    ],
}

WANTED_ENUMS: list[str] = [
    "WillowGame.ETopStatArrow",
]

WANTED_OBJECTS: list[dict[str, str]] = [
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Hyperion_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Bandit_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Jakobs_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Maliwan_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Torgue_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Hyperion", "class": "ManufacturerDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Bandit", "class": "ManufacturerDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Jakobs", "class": "ManufacturerDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Maliwan", "class": "ManufacturerDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Torgue", "class": "ManufacturerDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Hyperion", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Hyperion_1_Common", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Hyperion_2_Uncommon", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Bandit", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Bandit_1_Common", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Bandit_2_Uncommon", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Jakobs", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Jakobs_1_Common", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Jakobs_2_Uncommon", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Maliwan", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Maliwan_1_Common", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Maliwan_2_Uncommon", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Torgue", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Torgue_1_Common", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Torgue_2_Uncommon", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.FiringModes.Bullet_Pistol_Default", "class": "FiringModeDefinition"},
    {"path": "GD_Weap_Shotgun.FiringModes.Bullet_Shotgun_TidalWave", "class": "FiringModeDefinition"},
    {"path": "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child", "class": "FiringModeDefinition"},
    {"path": "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child_2", "class": "FiringModeDefinition"},
    {"path": "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child_3", "class": "FiringModeDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_01_Common", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_02_Uncommon", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_04_Rare", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_05_VeryRare", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_All", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_All_01_Common", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_All_02_Uncommon", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_All_04_Rare", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.WeaponPools.Pool_Weapons_All_05_VeryRare", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_01_Common", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_02_Uncommon", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_04_Rare", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_05_VeryRare", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedPistols", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedSMGs", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedShotguns", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedSniper", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.Treasure_ChestPools.Pool_WeaponChest_Weapons_Pistols", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.Treasure_ChestPools.Pool_WeaponChest_Weapons_LongGuns", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.Treasure_ChestPools.Pool_EpicChest_Weapons_Pistols", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.Treasure_ChestPools.Pool_EpicChest_Weapons_LongGuns", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.LootablePools.Pool_Locker_Items_SMGsAndPistols", "class": "ItemPoolDefinition"},
    {"path": "GD_Itempools.LootablePools.Pool_Dumpster_Guns", "class": "ItemPoolDefinition"},
    {"path": "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_Pistols", "class": "ItemPoolDefinition"},
    {"path": "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_SMG", "class": "ItemPoolDefinition"},
    {"path": "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_AssaultRifles", "class": "ItemPoolDefinition"},
    {"path": "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_Shotguns", "class": "ItemPoolDefinition"},
    {"path": "GD_ItemPools_Shop.WeaponPools.Shoppool_FeaturedItem_WeaponMachine", "class": "ItemPoolDefinition"},
    {"path": "GD_Episode01.M_Ep1_Champion", "class": "MissionDefinition"},
    {"path": "Ake_Wep_Melee.Ak_Play_Wep_Melee_Generic_Attack", "class": "AkEvent"},
    {"path": "Ake_Wep_Melee.Ak_Play_wep_melee_BladedWeap_hit", "class": "AkEvent"},
    {"path": "Ake_Wep_Melee.Ak_Play_wep_melee_Merc_impact", "class": "AkEvent"},
    {"path": "Ake_Mon_Thresher.v2.Ak_Play_Mon_Thresher_Melee_Swipe", "class": "AkEvent"},
    {"path": "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_MED", "class": "AkEvent"},
    {"path": "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_SM", "class": "AkEvent"},
    {"path": "Ake_Exp_Elemental.Exp_Explosive.Ak_Play_Exp_Elemental_Explosive_LG", "class": "AkEvent"},
    {"path": "Anim_CameraAnimations.Melee.Canim_RepeaterPistol_Melee", "class": "CameraAnim"},
    {"path": "Anim_CameraAnimations.Melee.Canim_CombatShotgun_Melee", "class": "CameraAnim"},
    {"path": "Anim_CameraAnimations.Melee.Canim_RocketLauncher_Melee", "class": "CameraAnim"},
    {"path": "Anim_CameraAnimations.Explosions.Canim_Explosion_Medium", "class": "CameraAnim"},
    {"path": "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Medium", "class": "ParticleSystem"},
    {"path": "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Small", "class": "ParticleSystem"},
    {"path": "GD_Impacts.Melee.Impact_MeleeGeneric", "class": "ImpactDefinition"},
    {"path": "GD_Impact.DamageType.DmgType_Normal", "class": "WillowDamageTypeDefinition"},
    {"path": "GD_Incendiary.DamageType.DmgType_Incendiary_Impact_NoDoT", "class": "WillowDamageTypeDefinition"},
    {"path": "GD_Shock.DamageType.DmgType_Shock_Impact_NoDoT", "class": "WillowDamageTypeDefinition"},
    {"path": "GD_Corrosive.DamageType.DmgType_Corrosive_Impact_NoDoT", "class": "WillowDamageTypeDefinition"},
    {"path": "GD_Explosive.DamageType.DmgType_Explosive_Impact_ForceFlinch", "class": "WillowDamageTypeDefinition"},
    {"path": "Char_Assassin.Mesh.Hands_Assassin", "class": "SkeletalMesh"},
]


def read_dump(dump_dir: str) -> dict[str, Any]:
    """Read every RE Probe section from a dump directory."""
    dump = _Dump(dump_dir)

    functions_index = dump.index_by_path("functions")
    functions = []
    for path in WANTED_FUNCTIONS:
        fn = functions_index.get(path)
        entry = {"path": path, "found": fn is not None}
        if fn is not None:
            entry.update(
                {
                    "name": fn.get("name"),
                    "owner": fn.get("owner"),
                    "native": fn.get("native"),
                    "event": fn.get("event"),
                    "num_params": fn.get("num_params"),
                    "return_type": fn.get("return_type"),
                    "display_signature": fn.get("display_signature"),
                    "params": [
                        {
                            "name": p.get("name"),
                            "type": p.get("type"),
                            "offset": p.get("offset"),
                            "is_out": p.get("is_out"),
                            "is_return": p.get("is_return"),
                            "is_optional": p.get("is_optional"),
                        }
                        for p in (fn.get("params") or [])
                    ],
                }
            )
        functions.append(entry)

    classes_index = dump.index_by_path("classes")
    class_reports = []
    for class_path, wanted_props in WANTED_CLASSES.items():
        rec = classes_index.get(class_path)
        entry = {"path": class_path, "found": rec is not None}
        if rec is not None:
            # Build effective properties by walking super_chain.
            effective: dict[str, dict[str, Any]] = {}
            current = class_path
            seen: set[str] = set()
            while current and current not in seen:
                seen.add(current)
                crec = classes_index.get(current)
                if crec:
                    for p in crec.get("properties") or []:
                        name = p.get("name")
                        if name is not None and name not in effective:
                            effective[name] = p
                supers = crec.get("super_chain") if crec else None
                current = supers[0] if supers else None
            entry["super"] = rec.get("super")
            entry["super_chain"] = rec.get("super_chain")
            entry["cdo"] = rec.get("cdo")
            found, missing = [], []
            for prop in wanted_props:
                p = effective.get(prop)
                if p is None:
                    missing.append(prop)
                else:
                    found.append(
                        {
                            "name": p.get("name"),
                            "type": p.get("type"),
                            "offset": p.get("offset"),
                            "declared_in": p.get("declared_in"),
                            "flags_decoded": p.get("flags_decoded"),
                        }
                    )
            entry["wanted_found"] = found
            entry["wanted_missing"] = missing
        class_reports.append(entry)

    structs_index = dump.index_by_path("structs")
    struct_reports = []
    for struct_path in [
        "WillowGame.WeaponDefinitionData",
        "WillowGame.WeightedInventoryBalanceData",
        "WillowGame.AttributeInitializationData",
        "WillowGame.ManufacturerCustomPartWeightData",
    ]:
        rec = structs_index.get(struct_path)
        entry = {"path": struct_path, "found": rec is not None}
        if rec is not None:
            entry["properties"] = [
                {
                    "name": p.get("name"),
                    "type": p.get("type"),
                    "offset": p.get("offset"),
                    "size": p.get("size"),
                    "struct": p.get("struct"),
                }
                for p in sorted(rec.get("properties") or [], key=lambda p: p.get("offset") or 0)
            ]
        struct_reports.append(entry)

    enums_index = dump.index_by_path("enums")
    enum_reports = []
    for path in WANTED_ENUMS:
        rec = enums_index.get(path)
        entry = {"path": path, "found": rec is not None}
        if rec is not None:
            entry["values"] = [
                {"name": v.get("name"), "value": v.get("value"), "alias_of": v.get("alias_of")}
                for v in sorted(rec.get("values") or [], key=lambda v: (v.get("value") or 0, v.get("name") or ""))
            ]
        enum_reports.append(entry)

    objects_index = dump.index_by_path("objects")
    object_results = []
    for spec in WANTED_OBJECTS:
        path = spec["path"]
        obj = objects_index.get(path)
        entry = {"path": path, "expected_class": spec["class"], "found": obj is not None}
        if obj is not None:
            entry.update(
                {
                    "actual_class": obj.get("class") or obj.get("class_name"),
                    "class_name": obj.get("class_name"),
                    "kind": obj.get("kind"),
                    "package": obj.get("package"),
                }
            )
            entry["class_matches_expectation"] = obj.get("class") == spec["class"]
        object_results.append(entry)

    defaults_index: dict[str, dict[str, Any]] = {}
    for rec in dump.section_items("defaults"):
        defaults_index[rec.get("class")] = rec

    default_results = []
    for class_path, prop in [
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
    ]:
        rec = defaults_index.get(class_path)
        entry = {"class": class_path, "property": prop, "found": False, "value": None}
        if rec is not None:
            values = rec.get("values") or {}
            entry["found"] = prop in values
            entry["value"] = values.get(prop)
            entry["cdo"] = rec.get("cdo")
        default_results.append(entry)

    trace_results = None
    for trace_path in dump.trace_files():
        doc = _load_trace_file(trace_path)
        if doc is not None:
            trace_results = _summarize_trace(doc)
            break

    return {
        "dump_dir": dump_dir,
        "dump_id": dump.dump_id,
        "status": dump.status,
        "functions": functions,
        "classes": class_reports,
        "structs": struct_reports,
        "enums": enum_reports,
        "objects": object_results,
        "defaults": default_results,
        "trace": trace_results,
    }


def _load_trace_file(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _summarize_trace(doc: Any) -> dict[str, Any]:
    session = doc.get("session") or {}
    funcs = doc.get("functions") or []
    edges = doc.get("edges") or []
    func_index = {f.get("path"): f for f in funcs}
    wanted = []
    for path in WANTED_FUNCTIONS:
        f = func_index.get(path)
        entry = {"path": path, "found": f is not None, "calls": f.get("calls") if f else 0}
        if f is not None:
            entry["callers"] = f.get("callers") or []
            entry["objects"] = f.get("objects") or []
        wanted.append(entry)
    return {
        "session": session,
        "wanted": wanted,
        "edges_total": len(edges),
        "top_edges": sorted(edges, key=lambda e: e.get("count", 0), reverse=True)[:40],
    }


def parse_dump(dump_dir: str) -> dict[str, Any]:
    """Public entry point: read + summarize a dump directory."""
    return read_dump(dump_dir)
