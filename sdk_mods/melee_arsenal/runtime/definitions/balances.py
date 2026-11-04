"""20 `WeaponBalanceDefinition`s, their part-list collections and drop item pools.

For each (Manufacturer, Rarity) this builds the weighted Blade/Grip/Accessory/
Element part lists from the catalog specs, then a dedicated 100% ground-drop pool.
"""
from __future__ import annotations
from ..sdk import unrealsdk

import copy
from typing import Any

from ...core.enums import Element, ElementPolicy, Rarity, TargetingMode
from ...core.parts import (
    ACCESSORIES,
    BLADE_BY_MANUFACTURER,
    ELEMENTS,
    GRIPS,
    MANUFACTURERS,
    NO_ACCESSORY_ID,
)
from ...core.parts.accessories import ACCESSORY_ROLL_CHANCE
from ..custom_assets import (
    assign_struct_array,
    resolve_fitted_accessory_part,
    resolve_fitted_element_part,
    resolve_fitted_grip_part,
    resolve_fitted_material_part,
)
from ..log import log_warn, obj_path
from ..state import KEEP_ALIVE_FLAGS, STATE
from .context import BuildContext
from .helpers import _ensure_subpackage, _find, _inherited_manufacturers, _inherited_plc


def _find_template_weighted_row(donor_plc: Any) -> Any:
    for slot_name in (
        "BarrelPartData",
        "GripPartData",
        "BodyPartData",
        "MaterialPartData",
        "ElementalPartData",
        "Accessory1PartData",
    ):
        slot = getattr(donor_plc, slot_name, None)
        if slot is not None:
            rows = list(getattr(slot, "WeightedParts", []) or [])
            if rows:
                return rows[0]
    return None


def _find_template_manufacturer_weight(donor_plc: Any) -> Any:
    for slot_name in (
        "ElementalPartData",
        "Accessory1PartData",
        "GripPartData",
        "BarrelPartData",
        "BodyPartData",
        "MaterialPartData",
        "SightPartData",
        "StockPartData",
    ):
        slot = getattr(donor_plc, slot_name, None)
        if slot is None:
            continue
        for row in getattr(slot, "WeightedParts", []) or []:
            mfrs = list(getattr(row, "Manufacturers", []) or [])
            if mfrs:
                return mfrs[0]
    return None


def _configure_part_list_collection(
    plc: Any,
    donor_plc: Any,
    body_part: Any,
    blade_part: Any,
    mat_part: Any,
    grip_weighted: list[tuple[Any, float]],
    elem_weighted: list[tuple[Any, float]],
    acc_weighted: list[tuple[Any, float]],
) -> None:
    donor_init = list(getattr(donor_plc, "ConsolidatedAttributeInitData", []) or [])
    if not donor_init:
        init_list = [
            unrealsdk.make_struct(
                "AttributeInitializationData",
                BaseValueConstant=1.0,
                BaseValueAttribute=None,
                InitializationDefinition=None,
                BaseValueScaleConstant=1.0,
            )
        ]
    else:
        init_list = [copy.copy(row) for row in donor_init]

    init_template = init_list[0]
    weight_to_idx: dict[float, int] = {}

    def _weight_index(w: float) -> int:
        key = round(float(w), 3)
        if key in weight_to_idx:
            return weight_to_idx[key]
        entry = copy.copy(init_template)
        entry.BaseValueConstant = key
        entry.BaseValueAttribute = None
        entry.InitializationDefinition = None
        entry.BaseValueScaleConstant = 1.0
        idx = len(init_list)
        init_list.append(entry)
        weight_to_idx[key] = idx
        return idx

    min_stage_idx = _weight_index(1.0)
    max_stage_idx = _weight_index(10000.0)
    tpl_row = _find_template_weighted_row(donor_plc)
    tpl_mfr_w = _find_template_manufacturer_weight(donor_plc)

    # Native `Borderlands2.exe` (`ChooseRandomParts`) ignores `row.DefaultWeightIndex` and substitutes
    # a flat weight of `100` whenever `row.Manufacturers` is empty! Supplying a single entry with
    # `Manufacturer=None` and `DefaultWeightIndex=min_stage_idx` (`1.0`) instructs native part selection
    # to multiply `row.DefaultWeightIndex * 1.0`, honoring our exact Grip, Element (`8:3`), and Accessory weights.
    mfr_weight_one: Any = None
    if tpl_mfr_w is not None:
        try:
            mw = copy.copy(tpl_mfr_w)
            mw.Manufacturer = None
            mw.DefaultWeightIndex = min_stage_idx
            if hasattr(mw, "MinGameStageIndex"):
                mw.MinGameStageIndex = min_stage_idx
            if hasattr(mw, "MaxGameStageIndex"):
                mw.MaxGameStageIndex = max_stage_idx
            mfr_weight_one = mw
        except Exception:
            mfr_weight_one = None
    if mfr_weight_one is None and unrealsdk is not None:
        try:
            mfr_weight_one = unrealsdk.make_struct(
                "ManufacturerCustomPartWeightData",
                Manufacturer=None,
                MinGameStageIndex=min_stage_idx,
                MaxGameStageIndex=max_stage_idx,
                DefaultWeightIndex=min_stage_idx,
            )
        except Exception:
            mfr_weight_one = None

    def _set_slot(slot_name: str, weighted_items: list[tuple[Any, float]]) -> None:
        slot = getattr(plc, slot_name, None)
        if slot is None:
            return
        valid = [(p, w) for (p, w) in weighted_items if p is not None and w > 0]
        if not valid or tpl_row is None:
            slot.bEnabled = False
            assign_struct_array(slot, "WeightedParts", [])
            return
        rows = []
        for part_obj, weight_val in valid:
            row = copy.copy(tpl_row)
            row.Part = part_obj
            assign_struct_array(
                row,
                "Manufacturers",
                [copy.copy(mfr_weight_one)] if mfr_weight_one is not None else [],
            )
            row.MinGameStageIndex = min_stage_idx
            row.MaxGameStageIndex = max_stage_idx
            row.DefaultWeightIndex = _weight_index(weight_val)
            rows.append(row)
        slot.bEnabled = True
        assign_struct_array(slot, "WeightedParts", rows)

    _set_slot("BodyPartData", [(body_part, 1.0)])
    _set_slot("BarrelPartData", [(blade_part, 1.0)])
    _set_slot("MaterialPartData", [(mat_part, 1.0)])
    _set_slot("GripPartData", grip_weighted)
    _set_slot("ElementalPartData", elem_weighted)
    _set_slot("Accessory1PartData", acc_weighted)
    _set_slot("Accessory2PartData", [])
    _set_slot("SightPartData", [])
    _set_slot("StockPartData", [])

    assign_struct_array(plc, "ConsolidatedAttributeInitData", init_list)


def _configure_balance_definition(
    bal: Any,
    donor_bal: Any,
    wt_obj: Any,
    mfr_obj: Any,
    plc: Any,
    fallback_bal: Any = None,
) -> None:
    bal.BaseDefinition = None
    bal.InventoryDefinition = wt_obj
    bal.RuntimePartListCollection = plc
    bal.WeaponPartListCollection = plc

    donor_mfrs = _inherited_manufacturers(donor_bal) or _inherited_manufacturers(fallback_bal)
    if donor_mfrs:
        mfr_entry = copy.copy(donor_mfrs[0])
        if mfr_obj is not None:
            mfr_entry.Manufacturer = mfr_obj
        grades = list(getattr(mfr_entry, "Grades", []) or [])
        if grades:
            g0 = copy.copy(grades[0])
            if hasattr(g0, "GameStageRequirement"):
                req = copy.copy(g0.GameStageRequirement)
                req.MinGameStage = 1
                req.MaxGameStage = 10000
                g0.GameStageRequirement = req
            assign_struct_array(mfr_entry, "Grades", [g0])
        assign_struct_array(bal, "Manufacturers", [mfr_entry])


def build_balances(ctx: BuildContext) -> None:
    """Build WeaponPartListCollections, balances and dedicated drop pools for every Maker x Rarity."""
    donors = ctx.donors
    root_pkg = ctx.root_pkg
    bal_pkg = ctx.bal_pkg
    # 9. Build 20 WeaponBalanceDefinitions, WeaponPartListCollections & Drop ItemPools
    pools_pkg = _ensure_subpackage(root_pkg, "Pools")
    drop_pool_donor = (
        _find("ItemPoolDefinition", "GD_Itempools.Runnables.Pool_MadameVonBartlesby")
        or _find("ItemPoolDefinition", "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_01_Common")
        or _find("ItemPoolDefinition", "GD_Itempools.WeaponPools.Pool_Weapons_Pistols_02_Uncommon")
    )
    all_donor_mfr_source = next(
        (b for b in donors["balances"].values() if _inherited_manufacturers(b)),
        None,
    )

    for maker, mspec in MANUFACTURERS.items():
        blade_id = BLADE_BY_MANUFACTURER[maker].part_id
        blade_part = STATE.blade_parts[blade_id]
        body_part = STATE.body_parts.get(maker)
        wt_obj = STATE.maker_to_wt[maker]
        mfr_obj = STATE.maker_to_donor_manufacturer.get(maker)
        maker_common_bal = donors["balances"].get((maker, Rarity.COMMON)) or all_donor_mfr_source

        allowed_elem_parts = [
            (
                resolve_fitted_element_part(espec.part_id, blade_id=blade_id),
                8.0 if espec.element == Element.NONE else 3.0,
            )
            for espec in ELEMENTS.values()
            if espec.element in mspec.allowed_elements
        ]
        grip_weighted = [
            (resolve_fitted_grip_part(gid, blade_id=blade_id), float(GRIPS[gid].weight))
            for gid in STATE.grip_parts
        ]

        for rarity in (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE):
            real_acc_parts = [
                (
                    resolve_fitted_accessory_part(aspec.part_id, blade_id=blade_id),
                    float(aspec.weight),
                )
                for aspec in ACCESSORIES.values()
                if aspec.part_id != NO_ACCESSORY_ID
                and rarity >= aspec.min_rarity
                and not (aspec.elemental_only and mspec.element_policy == ElementPolicy.NEVER)
                and not (
                    aspec.cleave_only
                    and mspec.cleave_profile.mode != TargetingMode.FRONTAL_CLEAVE
                )
                and not (
                    aspec.single_target_only
                    and mspec.cleave_profile.mode != TargetingMode.SINGLE_TARGET
                )
            ]
            acc_chance = float(ACCESSORY_ROLL_CHANCE.get(rarity, 0.25))
            none_acc_part = STATE.accessory_parts.get(NO_ACCESSORY_ID)
            if acc_chance >= 1.0 and real_acc_parts:
                allowed_acc_parts = real_acc_parts
            elif acc_chance <= 0.0 or not real_acc_parts:
                allowed_acc_parts = [(none_acc_part, 1.0)] if none_acc_part is not None else real_acc_parts
            else:
                real_w_sum = sum(w for _, w in real_acc_parts)
                none_w = round(real_w_sum * (1.0 - acc_chance) / acc_chance, 3)
                allowed_acc_parts = (
                    [(none_acc_part, none_w), *real_acc_parts]
                    if none_acc_part is not None
                    else real_acc_parts
                )
            donor_bal = donors["balances"].get((maker, rarity)) or next(
                iter(donors["balances"].values())
            )
            donor_plc = _inherited_plc(donor_bal) or _inherited_plc(maker_common_bal)
            mat_part = (
                resolve_fitted_material_part(maker, rarity)
                or next(iter(STATE.material_parts.values()), None)
            )

            plc_name = f"PLC_Melee_{maker.value}_{rarity.name}"
            plc = _find("WeaponPartListCollectionDefinition", f"{obj_path(bal_pkg)}.{plc_name}")
            if plc is None:
                plc = unrealsdk.construct_object(
                    "WeaponPartListCollectionDefinition",
                    bal_pkg,
                    plc_name,
                    KEEP_ALIVE_FLAGS,
                    donor_plc,
                )
                STATE.keep_alive(plc)

            plc.AssociatedWeaponType = wt_obj
            _configure_part_list_collection(
                plc,
                donor_plc,
                body_part=body_part,
                blade_part=blade_part,
                mat_part=mat_part,
                grip_weighted=grip_weighted,
                elem_weighted=allowed_elem_parts,
                acc_weighted=allowed_acc_parts,
            )

            bal_name = f"Balance_Melee_{maker.value}_{rarity.name}"
            bal = _find("WeaponBalanceDefinition", f"{obj_path(bal_pkg)}.{bal_name}")
            if bal is None:
                bal = unrealsdk.construct_object(
                    "WeaponBalanceDefinition",
                    bal_pkg,
                    bal_name,
                    KEEP_ALIVE_FLAGS,
                    donor_bal,
                )
                STATE.keep_alive(bal)

            _configure_balance_definition(
                bal,
                donor_bal,
                wt_obj,
                mfr_obj,
                plc,
                fallback_bal=maker_common_bal,
            )
            STATE.balances[(maker, rarity)] = bal
            STATE.balance_path_to_meta[obj_path(bal)] = (maker, rarity)

            # Build dedicated 100%-probability ItemPoolDefinition for ground-drop spawning
            if drop_pool_donor is not None:
                pool_name = f"Pool_Drop_{maker.value}_{rarity.name}"
                dpool = _find("ItemPoolDefinition", f"{obj_path(pools_pkg)}.{pool_name}")
                if dpool is None:
                    dpool = unrealsdk.construct_object(
                        "ItemPoolDefinition",
                        pools_pkg,
                        pool_name,
                        KEEP_ALIVE_FLAGS,
                        drop_pool_donor,
                    )
                    STATE.keep_alive(dpool)
                # Clear any inherited GameStage gates (`Gamestage_02` / `Gamestage_07`) from donor pistol pools
                for gate_attr in ("MinGameStageRequirement", "MaxGameStageRequirement"):
                    if hasattr(dpool, gate_attr):
                        try:
                            setattr(dpool, gate_attr, None)
                        except Exception:
                            pass
                if hasattr(dpool, "bAutoReadyItems"):
                    try:
                        dpool.bAutoReadyItems = False
                    except Exception:
                        pass
                if hasattr(dpool, "Quantity"):
                    try:
                        q = copy.copy(dpool.Quantity)
                        q.BaseValueConstant = 1.0
                        q.BaseValueAttribute = None
                        q.InitializationDefinition = None
                        q.BaseValueScaleConstant = 1.0
                        dpool.Quantity = q
                    except Exception:
                        pass
                donor_items = list(getattr(drop_pool_donor, "BalancedItems", []) or [])
                if donor_items:
                    entry = copy.copy(donor_items[0])
                    entry.InvBalanceDefinition = bal
                    entry.ItmPoolDefinition = None
                    entry.bDropOnDeath = True
                    prob = copy.copy(entry.Probability)
                    prob.BaseValueConstant = 1.0
                    prob.BaseValueAttribute = None
                    prob.InitializationDefinition = None
                    prob.BaseValueScaleConstant = 1.0
                    entry.Probability = prob
                    assign_struct_array(dpool, "BalancedItems", [entry])
                STATE.balance_drop_pools[(maker, rarity)] = dpool

    # Build reusable Behavior_SpawnLootAroundPoint for zero-XP drops in front of player
    if STATE.drop_spawner is None:
        try:
            spawner = _find("Behavior_SpawnLootAroundPoint", f"{obj_path(pools_pkg)}.MeleeDropSpawner")
            if spawner is None:
                spawner = unrealsdk.construct_object(
                    "Behavior_SpawnLootAroundPoint",
                    pools_pkg,
                    "MeleeDropSpawner",
                    KEEP_ALIVE_FLAGS,
                    None,
                )
            STATE.drop_spawner = STATE.keep_alive(spawner)
        except Exception as sp_exc:
            log_warn(f"Could not construct Behavior_SpawnLootAroundPoint: {sp_exc!r}")
