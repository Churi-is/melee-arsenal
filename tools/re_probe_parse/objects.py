"""Object index report.

Verifies every hard-coded object path the mod assumes exists:
donor weapon types, manufacturers, balances, firing modes, item pools,
the grant mission, and all combat audio/FX/damage-type assets. Also lists the
classes and packages actually loaded, so "donor not loaded yet" deferrals in
`definitions/donors.py` can be cross-checked.
"""

from __future__ import annotations

from typing import Any

from .loader import Dump

# Hard-coded object paths the mod assumes resolve, from runtime/resolver.py,
# runtime/definitions/donors.py, runtime/definitions/balances.py,
# runtime/resources.py, runtime/persistence_bridge.py, docs/ENGINE_FACTS.md.
WANTED_OBJECTS: list[dict[str, str]] = [
    # --- Weapon types per manufacturer ---
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Hyperion_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Bandit_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Jakobs_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Maliwan_Pistol", "class": "WeaponTypeDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.WeaponType_Torgue_Pistol", "class": "WeaponTypeDefinition"},
    # --- Manufacturer definitions ---
    {"path": "GD_Manufacturers.Manufacturers.Hyperion", "class": "ManufacturerDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Bandit", "class": "ManufacturerDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Jakobs", "class": "ManufacturerDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Maliwan", "class": "ManufacturerDefinition"},
    {"path": "GD_Manufacturers.Manufacturers.Torgue", "class": "ManufacturerDefinition"},
    # --- Balance donors per rarity (suffix guesses in _RARITY_DONOR_SUFFIX) ---
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Hyperion", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Hyperion_1_Common", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Hyperion_2_Uncommon", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Hyperion_3_Rare", "class": "WeaponBalanceDefinition"},
    {"path": "GD_Weap_Pistol.A_Weapons.Pistol_Hyperion_4_VeryRare", "class": "WeaponBalanceDefinition"},
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
    # --- Firing modes ---
    {"path": "GD_Weap_Pistol.FiringModes.Bullet_Pistol_Default", "class": "FiringModeDefinition"},
    {"path": "GD_Weap_Shotgun.FiringModes.Bullet_Shotgun_TidalWave", "class": "FiringModeDefinition"},
    {"path": "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child", "class": "FiringModeDefinition"},
    {"path": "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child_2", "class": "FiringModeDefinition"},
    {"path": "GD_Weap_Shotgun.FiringModes.Bullets_Shotgun_Torgue_Flakker_Child_3", "class": "FiringModeDefinition"},
    # --- Item pool donors ---
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
    # --- Grant mission (ServerGrantMissionRewards donor) ---
    {"path": "GD_Episode01.M_Ep1_Champion", "class": "MissionDefinition"},
    # --- Combat audio / FX / damage-type assets (ENGINE_FACTS: measured loaded) ---
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


def _obj_summary(o: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": o.get("path"),
        "name": o.get("name"),
        "class": o.get("class"),
        "class_name": o.get("class_name"),
        "kind": o.get("kind"),
        "package": o.get("package"),
        "outer": o.get("outer"),
        "flags": o.get("flags"),
        "internal_index": o.get("index"),
        "address": o.get("address"),
        "asset_type": o.get("asset_type"),
    }


def report_objects(dump: Dump) -> dict[str, Any]:
    objects = dump.index_by_path("objects")
    classes_index = dump.index_by_path("classes")

    wanted_results: list[dict[str, Any]] = []
    for spec in WANTED_OBJECTS:
        path = spec["path"]
        obj = objects.get(path)
        entry: dict[str, Any] = {"path": path, "expected_class": spec["class"], "found": obj is not None}
        if obj is not None:
            entry.update(_obj_summary(obj))
            entry["class_matches_expectation"] = obj.get("class") == spec["class"]
        wanted_results.append(entry)

    by_class_name: dict[str, int] = {}
    for obj in objects.values():
        cn = obj.get("class_name") or "(unknown)"
        by_class_name[cn] = by_class_name.get(cn, 0) + 1

    # Classes present in the dump (for the "donor package loaded?" deferral checks)
    class_paths = sorted(classes_index.keys())

    # Assets of the classes the mod's asset cache cares about
    asset_classes = {
        "AkEvent",
        "CameraAnim",
        "ParticleSystem",
        "ImpactDefinition",
        "WillowDamageTypeDefinition",
        "StaticMesh",
        "SkeletalMesh",
        "Material",
        "MaterialInstanceConstant",
        "Texture2D",
    }
    assets: list[dict[str, Any]] = []
    for obj in objects.values():
        if obj.get("asset_type") in asset_classes or obj.get("class_name") in asset_classes:
            assets.append(_obj_summary(obj))
    assets.sort(key=lambda a: a["path"] or "")

    mod_objects = [
        _obj_summary(o) for o in objects.values()
        if o.get("package") and "MeleeArsenalDev" in o.get("package", "")
    ]
    mod_objects.sort(key=lambda a: a["path"] or "")

    return {
        "dump_dir": dump.dump_dir,
        "dump_id": dump.dump_id,
        "profile": dump.profile,
        "total_objects": len(objects),
        "total_classes": len(class_paths),
        "wanted_objects": wanted_results,
        "wanted_found": sum(1 for e in wanted_results if e["found"]),
        "wanted_missing": [e["path"] for e in wanted_results if not e["found"]],
        "class_mismatches": [e["path"] for e in wanted_results if e.get("found") and not e.get("class_matches_expectation")],
        "by_class_name": dict(sorted(by_class_name.items(), key=lambda kv: -kv[1])),
        "classes": class_paths[:200],
        "assets": assets,
        "mod_objects": mod_objects,
    }


def write_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Object index report")
    lines.append("")
    lines.append(f"- Dump: `{result['dump_dir']}` (id: {result['dump_id']}, profile: {result['profile']})")
    lines.append(f"- Total objects indexed: {result['total_objects']}")
    lines.append(f"- Total classes: {result['total_classes']}")
    lines.append(f"- Wanted objects found: {result['wanted_found']} / {len(result['wanted_objects'])}")
    lines.append("")
    lines.append("## Wanted object paths")
    lines.append("")
    lines.append("| path | expected class | actual class | status |")
    lines.append("|---|---|---|---|")
    for entry in result["wanted_objects"]:
        path = entry["path"]
        if not entry["found"]:
            lines.append(f"| `{path}` | `{entry['expected_class']}` | — | **MISSING** |")
        else:
            actual = entry.get("class_name") or entry.get("class") or "—"
            match = "OK" if entry.get("class_matches_expectation") else "CLASS MISMATCH"
            lines.append(f"| `{path}` | `{entry['expected_class']}` | `{actual}` | {match} |")
    lines.append("")
    if result["wanted_missing"]:
        lines.append("## Missing paths")
        lines.append("")
        for path in result["wanted_missing"]:
            lines.append(f"- `{path}`")
        lines.append("")
    if result["class_mismatches"]:
        lines.append("## Class mismatches (path exists but class differs from assumption)")
        lines.append("")
        for path in result["class_mismatches"]:
            lines.append(f"- `{path}`")
        lines.append("")
    top_classes = list(result["by_class_name"].items())[:12]
    lines.append("## Top classes by instance count")
    lines.append("")
    for name, count in top_classes:
        lines.append(f"- {name}: {count}")
    lines.append("")
    lines.append(f"- Asset-class objects loaded: {len(result['assets'])}")
    lines.append(f"- MeleeArsenalDev objects present: {len(result['mod_objects'])}")
    return "\n".join(lines)
