"""World-drop, chest, container, and vendor `ItemPoolDefinition` injection (`GD_Itempools` & `GD_ItemPools_Shop`).

Root causes resolved in v0.3.3:
1. Clears inherited `MinGameStageRequirement` (`Gamestage_02` / `Gamestage_07`) and `MaxGameStageRequirement`
   from donor pistol sub-pools on all `Pool_Weapons_Melee_*` pools so `SpawnBalancedInventoryFromPool` never
   zeros out melee drop weights at early levels or in hub maps.
2. Uses in-place `WrappedArray` mutation (`assign_struct_array`: `.clear()` + `.append()`) with read-back
   verification instead of raw Python list assignment, and supports idempotent re-sync on every map load so
   `SparkLevelPatchEntry` hotfixes / BLCMM / UCP pool patches never wipe out Melee Arsenal entries.
3. Scales melee weights proportionally (`weapon_sum * active_weight / 520.0`) across `Pool_Weapons_All_*`,
   `EnemyDropPools.Pool_GunsAndGear_*` (the tiered pools a dying enemy rolls) plus the class-biased
   `Pool_GunsAndGear_Weighted*` variants, `Treasure_ChestPools.*`, `LootablePools.*`, and
   `GD_ItemPools_Shop.WeaponPools.*` while never touching `GD_ItempoolsEnemyUse.*` (preventing AI weapon bugs).
4. Resolves pools by exact path, then by case-insensitive live-object name, then through alternate
   pistol / SMG / AR / shotgun donor pools, so patch / DLC / BLCMM-UCP naming drift never silently
   disables every injection (a missing *donor* used to abort all 7 sub-pools at once).
5. Re-rolls already-stocked `WillowVendingMachine` shelves once after the first successful injection:
   vendors build their stock during map load, which can happen before the mod's pools exist.
"""
from __future__ import annotations

import copy
from typing import Any

try:
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    unrealsdk = None  # type: ignore[assignment]

from ..core.enums import Rarity
from .custom_assets import assign_struct_array
from .definitions import ensure_definitions_built
from .log import log_error, log_info, log_warn, obj_path
from .state import KEEP_ALIVE_FLAGS, STATE

# Sum of the 6 vanilla weapon classes in `Pool_Weapons_All_*`:
# Pistols (100) + SMGs (100) + ARs (100) + Shotguns (100) + Snipers (75) + Launchers (45) = 520.0
VANILLA_WEAPON_CLASS_SUM: float = 520.0

_TIER_SPECS: tuple[tuple[Rarity, str, str], ...] = (
    (
        Rarity.COMMON,
        "01_Common",
        "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_01_Common",
    ),
    (
        Rarity.UNCOMMON,
        "02_Uncommon",
        "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_02_Uncommon",
    ),
    (
        Rarity.RARE,
        "04_Rare",
        "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_04_Rare",
    ),
    (
        Rarity.VERY_RARE,
        "05_VeryRare",
        "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_05_VeryRare",
    ),
)

# Standard Gearbox `AttributeInitializationDefinition` weights in `GD_Balance.Weighting.*`
_KNOWN_WEIGHT_INIT_DEFS: dict[str, float] = {
    "Weight_0_VeryCommon": 200.0,
    "Weight_1_Common": 100.0,
    "Weight_2_Uncommon": 10.0,
    "Weight_3_Uncommoner": 5.0,
    "Weight_4_Rare": 1.0,
    "Weight_5_VeryRare": 0.1,
    "Weight_6_Legendary": 0.01,
}

_NON_WEAPON_POOL_TOKENS: tuple[str, ...] = (
    "Shield",
    "Grenade",
    "ClassMod",
    "Artifact",
    "Relic",
    "Ammo",
    "Health",
    "Money",
    "Currency",
    "Eridium",
    "Customizations",
)

# (category, target_pool_path, custom_melee_pool_key)
TARGET_POOL_MATRIX: tuple[tuple[str, str, str], ...] = (
    # 1. Global World-Drop Rarity Tier Pools (StandardEnemyGunsAndGear, Badasses, Bosses, Skag/Stalker Piles)
    ("world", "GD_Itempools.WeaponPools.Pool_Weapons_All_01_Common", "01_Common"),
    ("world", "GD_Itempools.WeaponPools.Pool_Weapons_All_02_Uncommon", "02_Uncommon"),
    ("world", "GD_Itempools.WeaponPools.Pool_Weapons_All_04_Rare", "04_Rare"),
    ("world", "GD_Itempools.WeaponPools.Pool_Weapons_All_05_VeryRare", "05_VeryRare"),
    # 2a. Standard Enemy / Badass / Boss death-drop pools. These tiered `Pool_GunsAndGear_*` pools are
    #     what `WillowAIPawn` actually rolls when a normal enemy dies, so Melee Weapons must be present
    #     here for enemies to ever drop them (each maps to the matching rarity sub-pool).
    ("enemy", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_01_Common", "01_Common"),
    ("enemy", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_02_Uncommon", "02_Uncommon"),
    ("enemy", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_04_Rare", "04_Rare"),
    ("enemy", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_05_VeryRare", "05_VeryRare"),
    # 2b. Class-Biased Humanoid / Marauder / Nomad / Raider gear-weighted pools
    ("enemy", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedPistols", "All"),
    ("enemy", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedSMGs", "All"),
    ("enemy", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedShotguns", "All"),
    ("enemy", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedSniper", "All"),
    # 3. Regular Weapon Chests & Red Epic Chests
    ("chest", "GD_Itempools.Treasure_ChestPools.Pool_WeaponChest_Weapons_LongGuns", "All"),
    ("chest", "GD_Itempools.Treasure_ChestPools.Pool_WeaponChest_Weapons_Pistols", "All"),
    ("chest", "GD_Itempools.Treasure_ChestPools.Pool_EpicChest_Weapons_LongGuns", "Epic"),
    ("chest", "GD_Itempools.Treasure_ChestPools.Pool_EpicChest_Weapons_Pistols", "Epic"),
    # 4. Lockers & Dumpsters
    ("container", "GD_Itempools.LootablePools.Pool_Locker_Items_SMGsAndPistols", "All"),
    ("container", "GD_Itempools.LootablePools.Pool_Dumpster_Guns", "All"),
    # 5. Marcus Weapon Vending Machines & Item of the Day
    ("shop", "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_Pistols", "Shop"),
    ("shop", "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_SMG", "Shop"),
    ("shop", "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_AssaultRifles", "Shop"),
    ("shop", "GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_Shotguns", "Shop"),
    ("shop", "GD_ItemPools_Shop.WeaponPools.Shoppool_FeaturedItem_WeaponMachine", "Epic"),
)

_LAST_LOG_SIGNATURE: tuple[bool, float, int] | None = None

# Pool paths whose casing/numbering drifts between BL2 patches, DLC packages, and UCP / BLCMM
# level-patch overrides. Donor pistol pools fall back through all of these before giving up.
_DONOR_POOL_TEMPLATES: tuple[str, ...] = (
    "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_{tier}",
    "GD_Itempools.WeaponPools.Pool_Weapons_All_{tier}",
    "GD_Itempools.WeaponPools.Pool_Weapons_SMG_{tier}",
    "GD_Itempools.WeaponPools.Pool_Weapons_AssaultRifles_{tier}",
    "GD_Itempools.WeaponPools.Pool_Weapons_Shotguns_{tier}",
    "GD_Itempools.WeaponPools.Pool_Weapons_Pistols",
)

# Loaded `ItemPoolDefinition` cache (rebuilt once per `sync_loot_pools()` pass).
_POOL_OBJECT_CACHE: list[Any] | None = None


def _iter_loaded_pools() -> list[Any]:
    """All live `ItemPoolDefinition` objects (``[]`` when the SDK cannot enumerate them)."""
    global _POOL_OBJECT_CACHE
    if _POOL_OBJECT_CACHE is not None:
        return _POOL_OBJECT_CACHE
    found: list[Any] = []
    if unrealsdk is not None:
        finder = getattr(unrealsdk, "find_all", None)
        if callable(finder):
            try:
                found = list(finder("ItemPoolDefinition"))
            except Exception:
                found = []
    _POOL_OBJECT_CACHE = found
    return found


def _find_pool(path: str) -> Any:
    """Resolve a live pool, tolerating package/object name casing differences between game builds."""
    if unrealsdk is None:
        return None
    try:
        found = unrealsdk.find_object("ItemPoolDefinition", path)
        if found is not None:
            return found
    except Exception:
        pass
    wanted = path.lower()
    for obj in _iter_loaded_pools():
        try:
            if obj_path(obj).lower() == wanted:
                return obj
        except Exception:
            continue
    return None


def _find_pool_any(paths: tuple[str, ...]) -> Any:
    for path in paths:
        found = _find_pool(path)
        if found is not None:
            return found
    return None


def _ensure_itempool_packages_loaded() -> None:
    if unrealsdk is None:
        return
    for pkg_name in ("GD_Itempools", "GD_ItemPools_Shop"):
        try:
            unrealsdk.load_package(pkg_name)
        except Exception:
            pass


def _ensure_pools_subpackage() -> Any:
    if STATE.root_package is None or unrealsdk is None:
        return None
    full = f"{obj_path(STATE.root_package)}.Pools"
    try:
        existing = unrealsdk.find_object("Package", full)
        if existing is not None:
            return existing
    except Exception:
        pass
    pkg = unrealsdk.construct_object("Package", STATE.root_package, "Pools", KEEP_ALIVE_FLAGS, None)
    return STATE.keep_alive(pkg)


def _sanitize_pool_object(pool_obj: Any) -> None:
    """Strip inherited `Gamestage_02` / `Gamestage_07` gates and normalize `Quantity = 1.0`."""
    if pool_obj is None:
        return
    for gate_attr in ("MinGameStageRequirement", "MaxGameStageRequirement"):
        if hasattr(pool_obj, gate_attr):
            try:
                setattr(pool_obj, gate_attr, None)
            except Exception:
                pass
    if hasattr(pool_obj, "bAutoReadyItems"):
        try:
            pool_obj.bAutoReadyItems = False
        except Exception:
            pass
    if hasattr(pool_obj, "Quantity"):
        try:
            q = copy.copy(pool_obj.Quantity)
            q.BaseValueConstant = 1.0
            q.BaseValueAttribute = None
            q.InitializationDefinition = None
            q.BaseValueScaleConstant = 1.0
            pool_obj.Quantity = q
        except Exception:
            pass


def _set_constant_prob(entry: Any, weight: float) -> None:
    prob = copy.copy(entry.Probability)
    prob.BaseValueConstant = float(weight)
    prob.BaseValueAttribute = None
    prob.InitializationDefinition = None
    prob.BaseValueScaleConstant = 1.0
    entry.Probability = prob


def _eval_prob_weight(prob: Any) -> float:
    """Evaluate an `AttributeInitializationData` struct's effective baseline weight."""
    if prob is None:
        return 0.0
    scale_raw = getattr(prob, "BaseValueScaleConstant", 1.0)
    try:
        scale = float(scale_raw)
    except Exception:
        scale = 1.0
    if scale == 0.0:
        return 0.0

    init_def = getattr(prob, "InitializationDefinition", None)
    if init_def is not None:
        init_name = str(getattr(init_def, "Name", "") or obj_path(init_def).rsplit(".", 1)[-1])
        for key, val in _KNOWN_WEIGHT_INIT_DEFS.items():
            if key in init_name:
                return val * scale
        # Fallback: inspect `ValueFormula.Multiplier.BaseValueConstant` if exposed
        vf = getattr(init_def, "ValueFormula", None)
        if vf is not None:
            mult = getattr(vf, "Multiplier", None)
            if mult is not None:
                try:
                    mv = float(getattr(mult, "BaseValueConstant", 0.0) or 0.0)
                    if mv > 0.0:
                        return mv * scale
                except Exception:
                    pass
        return 1.0 * scale

    bva = getattr(prob, "BaseValueAttribute", None)
    if bva is not None:
        if hasattr(bva, "GetValue"):
            try:
                res = bva.GetValue(None)
                v = float(res[0] if isinstance(res, tuple) else res)
                if v > 0.0:
                    return v * scale
            except Exception:
                pass
        for const_attr in ("ConstantValue", "BaseValue"):
            if hasattr(bva, const_attr):
                try:
                    v = float(getattr(bva, const_attr))
                    if v > 0.0:
                        return v * scale
                except Exception:
                    pass
        return 1.0 * scale

    try:
        bvc = float(getattr(prob, "BaseValueConstant", 0.0) or 0.0)
    except Exception:
        bvc = 0.0
    return max(0.0, bvc * scale)


def _is_melee_pool_entry(entry: Any) -> bool:
    sub_p = obj_path(getattr(entry, "ItmPoolDefinition", None))
    bal_p = obj_path(getattr(entry, "InvBalanceDefinition", None))
    return (
        "MeleeArsenalDev." in sub_p
        or "Pool_Weapons_Melee_" in sub_p
        or "MeleeArsenalDev." in bal_p
        or "Balance_Melee_" in bal_p
    )


def _is_weapon_pool_entry(entry: Any) -> bool:
    sub_p = obj_path(getattr(entry, "ItmPoolDefinition", None))
    bal_p = obj_path(getattr(entry, "InvBalanceDefinition", None))
    joined = f"{sub_p} {bal_p}"
    if not joined.strip():
        return False
    return not any(tok in joined for tok in _NON_WEAPON_POOL_TOKENS)


def _get_or_create_custom_pool(pkg_pools: Any, pool_name: str, donor_pool: Any) -> Any:
    cached = STATE.custom_melee_pools.get(pool_name)
    if cached is not None:
        _sanitize_pool_object(cached)
        return cached

    full_path = f"{obj_path(pkg_pools)}.{pool_name}"
    existing = _find_pool(full_path)
    if existing is None:
        existing = unrealsdk.construct_object(
            "ItemPoolDefinition",
            pkg_pools,
            pool_name,
            KEEP_ALIVE_FLAGS,
            donor_pool,
        )
    STATE.keep_alive(existing)
    _sanitize_pool_object(existing)
    STATE.custom_melee_pools[pool_name] = existing
    return existing


def _build_or_refresh_melee_subpools(pkg_pools: Any) -> dict[str, Any]:
    """Build 4 rarity sub-pools + 3 composite rarity-curve sub-pools (`All`, `Epic`, `Shop`)."""
    subpools: dict[str, Any] = {}
    donor_pool_any: Any = None
    donor_entry_tpl: Any = None

    # 1. Build the 4 per-rarity sub-pools (`01_Common`, `02_Uncommon`, `04_Rare`, `05_VeryRare`)
    for rarity, tier_key, donor_sub_path in _TIER_SPECS:
        donor_sub = _find_pool(donor_sub_path)
        if donor_sub is None:
            donor_sub = _find_pool_any(
                tuple(tmpl.format(tier=tier_key) for tmpl in _DONOR_POOL_TEMPLATES)
            )
        donor_sub = donor_sub or donor_pool_any
        if donor_sub is None:
            continue
        donor_items = list(getattr(donor_sub, "BalancedItems", []) or [])
        if not donor_items and donor_entry_tpl is None:
            continue
        tpl = donor_items[0] if donor_items else donor_entry_tpl
        if donor_pool_any is None:
            donor_pool_any = donor_sub
        if donor_entry_tpl is None:
            donor_entry_tpl = tpl

        pool_name = f"Pool_Weapons_Melee_{tier_key}"
        melee_sub = _get_or_create_custom_pool(pkg_pools, pool_name, donor_sub)

        entries = []
        for ( _maker, rar ), bal_obj in STATE.balances.items():
            if rar != rarity:
                continue
            row = copy.copy(tpl)
            row.ItmPoolDefinition = None
            row.InvBalanceDefinition = bal_obj
            row.bDropOnDeath = True
            _set_constant_prob(row, 1.0)
            entries.append(row)

        assign_struct_array(melee_sub, "BalancedItems", entries)
        subpools[tier_key] = melee_sub

    if not subpools or donor_pool_any is None or donor_entry_tpl is None:
        return subpools

    tier_order = ("01_Common", "02_Uncommon", "04_Rare", "05_VeryRare")
    available_tiers = [t for t in tier_order if t in subpools]

    # 2. Composite `Pool_Weapons_Melee_All`: mirror live `Pool_Weapons_All` Probability structs if present
    melee_all = _get_or_create_custom_pool(pkg_pools, "Pool_Weapons_Melee_All", donor_pool_any)
    live_all = _find_pool("GD_Itempools.WeaponPools.Pool_Weapons_All")
    live_all_items = list(getattr(live_all, "BalancedItems", []) or []) if live_all is not None else []
    default_all_weights = {
        "01_Common": 100.0,
        "02_Uncommon": 10.0,
        "04_Rare": 1.0,
        "05_VeryRare": 0.1,
    }
    all_entries = []
    for idx, tkey in enumerate(available_tiers):
        row = copy.copy(donor_entry_tpl)
        row.ItmPoolDefinition = subpools[tkey]
        row.InvBalanceDefinition = None
        row.bDropOnDeath = True
        matched_prob = None
        for li in live_all_items:
            lp = obj_path(getattr(li, "ItmPoolDefinition", None))
            if tkey in lp:
                matched_prob = copy.copy(li.Probability)
                break
        if matched_prob is not None:
            row.Probability = matched_prob
        elif idx < len(live_all_items):
            row.Probability = copy.copy(live_all_items[idx].Probability)
        else:
            _set_constant_prob(row, default_all_weights.get(tkey, 1.0))
        all_entries.append(row)
    assign_struct_array(melee_all, "BalancedItems", all_entries)
    subpools["All"] = melee_all

    # 3. Composite `Pool_Weapons_Melee_Epic`: Uncommon + Rare + VeryRare (NO White Commons for Red Chests / IOTD!)
    melee_epic = _get_or_create_custom_pool(pkg_pools, "Pool_Weapons_Melee_Epic", donor_pool_any)
    default_epic_weights = (
        ("02_Uncommon", 10.0),
        ("04_Rare", 2.5),
        ("05_VeryRare", 0.5),
    )
    epic_entries = []
    for tkey, w in default_epic_weights:
        if tkey not in subpools:
            continue
        row = copy.copy(donor_entry_tpl)
        row.ItmPoolDefinition = subpools[tkey]
        row.InvBalanceDefinition = None
        row.bDropOnDeath = True
        _set_constant_prob(row, w)
        epic_entries.append(row)
    assign_struct_array(melee_epic, "BalancedItems", epic_entries)
    subpools["Epic"] = melee_epic

    # 4. Composite `Pool_Weapons_Melee_Shop`: mirror `ShopPool_Weapons_Pistols` rarity curve if loaded
    melee_shop = _get_or_create_custom_pool(pkg_pools, "Pool_Weapons_Melee_Shop", donor_pool_any)
    shop_pistols = _find_pool("GD_ItemPools_Shop.WeaponPools.ShopPool_Weapons_Pistols")
    shop_items = [
        it for it in (list(getattr(shop_pistols, "BalancedItems", []) or []) if shop_pistols is not None else [])
        if not _is_melee_pool_entry(it)
    ]
    default_shop_weights = (
        ("01_Common", 100.0),
        ("02_Uncommon", 15.0),
        ("04_Rare", 1.5),
        ("05_VeryRare", 0.15),
    )
    shop_entries = []
    for tkey, fallback_w in default_shop_weights:
        if tkey not in subpools:
            continue
        row = copy.copy(donor_entry_tpl)
        row.ItmPoolDefinition = subpools[tkey]
        row.InvBalanceDefinition = None
        row.bDropOnDeath = True
        matched_prob = None
        for si in shop_items:
            sp = obj_path(getattr(si, "ItmPoolDefinition", None))
            if tkey in sp:
                matched_prob = copy.copy(si.Probability)
                break
        if matched_prob is not None:
            row.Probability = matched_prob
        else:
            _set_constant_prob(row, fallback_w)
        shop_entries.append(row)
    assign_struct_array(melee_shop, "BalancedItems", shop_entries)
    subpools["Shop"] = melee_shop

    return subpools


def sync_loot_pools() -> bool:
    """Idempotently inject/update/remove Melee Arsenal sub-pools across world, chest, container, and shop pools."""
    global _LAST_LOG_SIGNATURE, _POOL_OBJECT_CACHE
    if unrealsdk is None:
        return False
    if not ensure_definitions_built():
        return False

    try:
        # Pools may have just been streamed in / hotfixed; rebuild the loose-name lookup cache.
        _POOL_OBJECT_CACHE = None
        _ensure_itempool_packages_loaded()
        pkg_pools = _ensure_pools_subpackage()
        if pkg_pools is None:
            return False

        subpools = _build_or_refresh_melee_subpools(pkg_pools)
        if not subpools:
            log_warn("Could not build Melee Arsenal sub-pools (`GD_Itempools` pistol donors not loaded yet).")
            STATE.pools_injected = False
            STATE.injected_pool_count = 0
            return False

        active_weight = float(STATE.world_drop_weight) if STATE.world_drops_enabled else 0.0
        class_ratio = max(0.0, active_weight) / VANILLA_WEAPON_CLASS_SUM

        verified_count = 0
        found_targets = 0

        for _category, target_path, subpool_key in TARGET_POOL_MATRIX:
            target_pool = _find_pool(target_path)
            melee_sub = subpools.get(subpool_key)
            if target_pool is None or melee_sub is None:
                continue

            raw_items = list(getattr(target_pool, "BalancedItems", []) or [])
            clean_items = [copy.copy(it) for it in raw_items if not _is_melee_pool_entry(it)]
            if not clean_items and not raw_items:
                continue
            found_targets += 1

            if class_ratio > 0.0:
                tpl = clean_items[0] if clean_items else raw_items[0]
                weapon_sum = sum(_eval_prob_weight(it.Probability) for it in clean_items if _is_weapon_pool_entry(it))
                if weapon_sum <= 0.0:
                    weapon_sum = sum(_eval_prob_weight(it.Probability) for it in clean_items)
                scaled_weight = (
                    max(0.0001, round(weapon_sum * class_ratio, 6))
                    if weapon_sum > 0.0
                    else max(0.0001, active_weight)
                )

                melee_entry = copy.copy(tpl)
                melee_entry.ItmPoolDefinition = melee_sub
                melee_entry.InvBalanceDefinition = None
                melee_entry.bDropOnDeath = True
                _set_constant_prob(melee_entry, scaled_weight)
                clean_items.append(melee_entry)

            assign_struct_array(target_pool, "BalancedItems", clean_items)

            # Read-back verification against live UE3 `TArray`
            after_items = list(getattr(target_pool, "BalancedItems", []) or [])
            has_melee = any(_is_melee_pool_entry(it) for it in after_items)
            if (class_ratio > 0.0 and has_melee) or (class_ratio <= 0.0 and not has_melee):
                verified_count += 1

        STATE.injected_pool_count = verified_count if class_ratio > 0.0 else 0
        STATE.pools_injected = (verified_count > 0) if class_ratio > 0.0 else False

        # Vending machines roll their shelf stock out of these pools when the map streams in — possibly
        # before our first injection. Re-roll only when the injected configuration actually changed so
        # existing vendor stock starts showing Melee Weapons instead of needing a map reload.
        refresh_sig = (bool(STATE.world_drops_enabled), round(active_weight, 2))
        if STATE.pools_injected and refresh_sig != STATE.vendor_refresh_signature:
            refreshed = refresh_vending_machines()
            if refreshed > 0:
                # Only latch once machines actually existed (menus / hub maps can load zero vendors),
                # so the re-roll is retried on the next map until it takes effect.
                STATE.vendor_refresh_signature = refresh_sig
                log_info(f"Re-rolled {refreshed} vending machine(s) to include Melee Arsenal stock.")

        sig = (bool(STATE.world_drops_enabled), round(active_weight, 2), verified_count)
        if sig != _LAST_LOG_SIGNATURE:
            _LAST_LOG_SIGNATURE = sig
            approx_share = (
                100.0 * active_weight / (VANILLA_WEAPON_CLASS_SUM + active_weight)
                if active_weight > 0.0
                else 0.0
            )
            log_info(
                f"Synced Melee Arsenal loot pools: {verified_count}/{found_targets} pools verified "
                f"(enabled={STATE.world_drops_enabled}, class_weight={active_weight:.0f}, "
                f"~{approx_share:.1f}% of weapon drops)."
            )

        return verified_count > 0 or class_ratio <= 0.0
    except Exception as exc:
        log_error(f"sync_loot_pools failed: {exc!r}")
        STATE.pools_injected = False
        return False


def refresh_vending_machines() -> int:
    """Re-roll already-stocked gun vendors so they draw from the freshly injected shop pools.

    BL2 fills `WillowVendingMachine` shelves from `GD_ItemPools_Shop` during map load (`ResetInventory`),
    which can happen before the mod's pools exist — the machine then keeps that stale Common-only stock
    for the whole session. Forcing one `ResetInventory()` after the first successful injection makes
    Melee Weapons appear in Marcus machines without needing a map transition.
    """
    if unrealsdk is None:
        return 0
    finder = getattr(unrealsdk, "find_all", None)
    if not callable(finder):
        return 0
    try:
        machines = list(finder("WillowVendingMachine"))
    except Exception:
        return 0

    refreshed = 0
    for machine in machines:
        if machine is None or str(getattr(machine, "Name", "")).startswith("Default__"):
            continue
        reset = getattr(machine, "ResetInventory", None)
        if not callable(reset):
            continue
        try:
            reset()
            refreshed += 1
        except Exception:
            continue
    return refreshed


def inspect_loot_pools(resync_first: bool = True) -> dict[str, Any]:
    """Re-sync (optional) and return a live diagnostic report of all Melee Arsenal loot pools."""
    if resync_first:
        sync_loot_pools()

    subpool_gates_cleared = True
    for pool_obj in STATE.custom_melee_pools.values():
        if getattr(pool_obj, "MinGameStageRequirement", None) is not None:
            subpool_gates_cleared = False
        if getattr(pool_obj, "MaxGameStageRequirement", None) is not None:
            subpool_gates_cleared = False

    pool_rows: list[dict[str, Any]] = []
    by_category: dict[str, int] = {}
    missing_pools: list[str] = []

    for category, target_path, subpool_key in TARGET_POOL_MATRIX:
        target_pool = _find_pool(target_path)
        if target_pool is None:
            missing_pools.append(target_path)
            continue
        items = list(getattr(target_pool, "BalancedItems", []) or [])
        melee_w = 0.0
        other_weapon_w = 0.0
        for it in items:
            w = _eval_prob_weight(getattr(it, "Probability", None))
            if _is_melee_pool_entry(it):
                melee_w += w
            elif _is_weapon_pool_entry(it):
                other_weapon_w += w
        total_weapon_w = melee_w + other_weapon_w
        share_pct = (100.0 * melee_w / total_weapon_w) if total_weapon_w > 0.0 else 0.0
        if melee_w > 0.0:
            by_category[category] = by_category.get(category, 0) + 1
        pool_rows.append(
            {
                "category": category,
                "pool": target_path.rsplit(".", 1)[-1],
                "subpool": f"Pool_Weapons_Melee_{subpool_key}",
                "melee_weight": round(melee_w, 4),
                "weapon_total_weight": round(total_weapon_w, 4),
                "melee_weapon_share_pct": round(share_pct, 2),
                "injected": melee_w > 0.0,
            }
        )

    injected_total = sum(1 for r in pool_rows if r["injected"])
    avg_share = (
        round(
            sum(r["melee_weapon_share_pct"] for r in pool_rows if r["injected"]) / injected_total,
            2,
        )
        if injected_total > 0
        else 0.0
    )
    return {
        "world_drops_enabled": bool(STATE.world_drops_enabled),
        "world_drop_weight": float(STATE.world_drop_weight),
        "loaded_target_pools": len(pool_rows),
        "injected_pools": injected_total,
        "by_category": by_category,
        "subpool_stage_gates_cleared": subpool_gates_cleared,
        "avg_weapon_share_pct": avg_share,
        "missing_pools": missing_pools,
        "subpools_built": sorted(STATE.custom_melee_pools.keys()),
        "pools": pool_rows,
    }
