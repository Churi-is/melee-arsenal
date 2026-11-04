"""Resolves live `WillowWeapon` UObjects to `MeleeWeaponInstance`, spawns via native reward grant,
and provides 1st-person animation donor context swaps.

Why single-item staggered `ServerGrantMissionRewards` (`GD_Episode01.M_Ep1_Champion`) is used:
- Putting `>= 2` items in `MissionDefinition.Reward.RewardItems` opens BL2's "Choose 1 of 2" quest
  reward prompt when entering Inventory.
- Putting `[bal]` (length 1) grants the item directly into the Backpack with NO choice screen!
- For multi-weapon batches (`melee_spawn_all`), item #1 grants immediately and remaining items grant
  1 every 3 ticks (~50ms apart) so all 5 archetypes arrive directly in your Backpack!
"""
from __future__ import annotations

import copy
import math
import random
import secrets
import traceback
from collections import deque
from typing import Any

try:
    import unrealsdk  # type: ignore[import-not-found]
    from unrealsdk.hooks import Block, prevent_hooking_direct_calls  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    unrealsdk = None  # type: ignore[assignment]
    Block = object()  # type: ignore[assignment]
    prevent_hooking_direct_calls = None  # type: ignore[assignment]

from ..core.parts import (
    BLADES,
    BLADE_BY_MANUFACTURER,
    ELEMENT_PART_BY_TYPE,
    MANUFACTURERS,
    NO_ACCESSORY_ID,
    resolve_weapon_name,
)
from ..core.enums import Manufacturer, Rarity
from ..core.parts.weapon import MeleeWeaponInstance, MeleeWeaponParts
from ..core.stats import ComputedCardStats, compute_card_stats
from .custom_assets import (
    get_part_rarity,
    is_custom_asset_part,
    resolve_fitted_accessory_part,
    resolve_fitted_element_part,
    resolve_fitted_grip_part,
    resolve_fitted_material_part,
    set_part_rarity,
    sync_definition_data_visual_parts,
)
from .definitions import (
    MELEE_CLIP_SIZE,
    ensure_definitions_built,
    get_or_create_prefix_part,
    get_or_create_title_part,
)
from .log import log_debug_once, log_error, log_info, log_warn, obj_path
from .state import STATE


SPAWNER_MISSION_PATH: str = "GD_Episode01.M_Ep1_Champion"

_PENDING_GRANTS: dict[Manufacturer, deque[MeleeWeaponInstance]] = {
    m: deque() for m in Manufacturer
}
_PENDING_DROP_INSTANCES: deque[MeleeWeaponInstance] = deque()
_LAST_DROP_COUNT: int = 0
_DEFERRED_SPAWN_QUEUE: deque[MeleeWeaponInstance] = deque()
_DEFERRED_STEP_TICKS: int = 0
_MISSION_BACKUP: dict[str, Any] | None = None
_MISSION_RESTORE_TICKS: int = 0
_STAMPED_UIDS: set[int] = set()
_MAX_UNIQUE_ID: int = 2_147_483_647


def allocate_unique_id(reserved: set[int] | None = None) -> int:
    """Allocate a positive engine-compatible UniqueId that is not already reserved."""
    reserved = reserved or set()
    for _ in range(1000):
        candidate = secrets.randbelow(_MAX_UNIQUE_ID) + 1
        if candidate not in reserved and candidate not in _STAMPED_UIDS:
            return candidate
    raise RuntimeError("Could not allocate a free Melee Arsenal UniqueId")


def grant_was_stamped(unique_id: int) -> bool:
    return int(unique_id) in _STAMPED_UIDS


def has_pending_grants() -> bool:
    return bool(_DEFERRED_SPAWN_QUEUE) or any(_PENDING_GRANTS.values())


def unwrap_weapon_object(item_or_pickup: Any) -> Any:
    """Unwrap `WillowPickup` or `InterfacePointer` to the underlying `WillowWeapon` if present.

    IMPORTANT: Check `DefinitionData` BEFORE `.Inventory`, because on an equipped `WillowWeapon`,
    `Engine.Inventory.Inventory` points to the NEXT item in `InvManager.InventoryChain`!
    """
    if item_or_pickup is None:
        return None
    try:
        ptr = getattr(item_or_pickup, "ObjectPointer", None)
        if ptr is not None and ptr is not item_or_pickup:
            item_or_pickup = ptr
    except Exception as exc:
        log_debug_once("resolver.unwrap.ObjectPointer", f"unwrap ObjectPointer failed: {exc!r}")
    if hasattr(item_or_pickup, "DefinitionData"):
        return item_or_pickup
    try:
        pickup_inv = getattr(item_or_pickup, "Inventory", None)
        if pickup_inv is not None and hasattr(pickup_inv, "DefinitionData"):
            return pickup_inv
    except Exception as exc:
        log_debug_once("resolver.unwrap.Inventory", f"unwrap Inventory failed: {exc!r}")
    return None


def is_melee_definition_data(dd: Any) -> bool:
    if dd is None:
        return False
    try:
        if obj_path(getattr(dd, "WeaponTypeDefinition", None)) in STATE.wt_path_to_maker:
            return True
        if obj_path(getattr(dd, "BarrelPartDefinition", None)) in STATE.part_path_to_blade_id:
            return True
        if obj_path(getattr(dd, "BalanceDefinition", None)) in STATE.balance_path_to_meta:
            return True
    except Exception as exc:
        log_debug_once("resolver.is_melee_definition_data", f"is_melee_definition_data check failed: {exc!r}")
        return False
    return False


def is_melee_weapon(item_or_pickup: Any) -> bool:
    wpn = unwrap_weapon_object(item_or_pickup)
    if wpn is None:
        return False
    try:
        return is_melee_definition_data(wpn.DefinitionData)
    except Exception as exc:
        log_debug_once("resolver.is_melee_weapon", f"is_melee_weapon check failed: {exc!r}")
        return False


def _infer_rarity(wpn: Any, dd: Any) -> Rarity:
    bal_p = obj_path(getattr(dd, "BalanceDefinition", None))
    if bal_p in STATE.balance_path_to_meta:
        return STATE.balance_path_to_meta[bal_p][1]
    try:
        rl = int(getattr(wpn, "RarityLevel", 2) or 2)
        if rl <= 1:
            return Rarity.COMMON
        if rl == 2:
            return Rarity.UNCOMMON
        if rl == 3:
            return Rarity.RARE
        return Rarity.VERY_RARE
    except Exception as exc:
        log_debug_once("resolver._infer_rarity", f"_infer_rarity failed: {exc!r}")
        return Rarity.UNCOMMON


def resolve_melee_weapon(
    item_or_pickup: Any,
    sync_weapon_props: bool = True,
) -> tuple[MeleeWeaponInstance, ComputedCardStats] | None:
    """Extract `MeleeWeaponInstance` + `ComputedCardStats` from a live `WillowWeapon` (or `None`)."""
    wpn = unwrap_weapon_object(item_or_pickup)
    if wpn is None:
        return None
    try:
        dd = wpn.DefinitionData
        if not is_melee_definition_data(dd):
            return None

        wt_p = obj_path(getattr(dd, "WeaponTypeDefinition", None))
        barrel_p = obj_path(getattr(dd, "BarrelPartDefinition", None))
        bal_p = obj_path(getattr(dd, "BalanceDefinition", None))

        blade_id = STATE.part_path_to_blade_id.get(barrel_p)
        if blade_id is None:
            maker = STATE.wt_path_to_maker.get(wt_p)
            if maker is None and bal_p in STATE.balance_path_to_meta:
                maker = STATE.balance_path_to_meta[bal_p][0]
            if maker is None:
                return None
            blade_id = BLADE_BY_MANUFACTURER[maker].part_id

        maker = BLADES[blade_id].manufacturer
        mspec = MANUFACTURERS[maker]

        grip_p = obj_path(getattr(dd, "GripPartDefinition", None))
        grip_id = STATE.part_path_to_grip_id.get(grip_p, f"Grip_{maker.value}")

        acc_p = obj_path(getattr(dd, "Accessory1PartDefinition", None))
        acc_id = STATE.part_path_to_accessory_id.get(acc_p, NO_ACCESSORY_ID)

        elem_p = obj_path(getattr(dd, "ElementalPartDefinition", None))
        default_elem = mspec.allowed_elements[0]
        elem_id = STATE.part_path_to_element_id.get(elem_p, ELEMENT_PART_BY_TYPE[default_elem].part_id)

        game_stage = max(
            1,
            int(getattr(dd, "GameStage", 1) or getattr(dd, "ManufacturerGradeIndex", 1) or 1),
        )
        grade_idx = int(getattr(dd, "ManufacturerGradeIndex", game_stage) or game_stage)
        unique_id = int(getattr(dd, "UniqueId", 0) or 0)
        if unique_id == 0 and sync_weapon_props:
            unique_id = random.randint(100_000, 2_000_000_000)
        rarity = _infer_rarity(wpn, dd)

        inst = MeleeWeaponInstance(
            unique_id=unique_id,
            game_stage=game_stage,
            rarity=rarity,
            parts=MeleeWeaponParts(
                blade_id=blade_id,
                grip_id=grip_id,
                accessory_id=acc_id,
                element_id=elem_id,
            ),
            grade_index=grade_idx,
        )
        card = compute_card_stats(inst)

        if sync_weapon_props:
            sync_live_weapon_properties(wpn, inst, card)

        return inst, card
    except Exception as exc:
        log_error(f"resolve_melee_weapon failed: {exc!r}")
        return None


def _ensure_material_rarity(mat_part: Any, rarity: Rarity) -> None:
    """Keep our Material part's `Rarity` level in sync with the rolled tier (never touch vanilla parts)."""
    if mat_part is None or not is_custom_asset_part(mat_part):
        return
    if abs(get_part_rarity(mat_part) - float(int(rarity))) > 1e-3:
        set_part_rarity(mat_part, rarity)


def stamp_inventory_rarity(item_or_pickup: Any, rarity: Rarity) -> None:
    """Stamp the live rarity level the engine uses for loot beams, item cards and sell value.

    `WillowPickup.InventoryRarityLevel` (ground-beam colour) and `WillowInventory.RarityLevel`
    (item-card colour) are cached when the item is *initialized*.  Weapons we re-assemble after
    that point (command spawns, sidecar restores, natural rolls finalized in
    `ChooseRandomNameParts`) would otherwise keep the stale Common level and show up white.
    """
    level = float(int(rarity))
    targets = [item_or_pickup]
    weapon = unwrap_weapon_object(item_or_pickup)
    if weapon is not None and weapon is not item_or_pickup:
        targets.append(weapon)
    for obj in targets:
        if obj is None:
            continue
        for attr in ("InventoryRarityLevel", "RarityLevel"):
            try:
                setattr(obj, attr, level)
            except Exception:
                continue


def apply_instance_to_definition_data(dd: Any, inst: MeleeWeaponInstance) -> None:
    """Write exact UObject part references for `inst` onto a `WeaponDefinitionData` struct."""
    blade_spec = BLADES[inst.parts.blade_id]
    maker = blade_spec.manufacturer
    wt = STATE.maker_to_wt[maker]
    bal = STATE.balances.get((maker, inst.rarity)) or STATE.balances.get((maker, Rarity.UNCOMMON))
    mfr_obj = STATE.maker_to_donor_manufacturer.get(maker)
    body_part = STATE.body_parts.get(maker)
    blade_part = STATE.blade_parts[inst.parts.blade_id]
    grip_part = resolve_fitted_grip_part(inst.parts.grip_id, blade_id=inst.parts.blade_id)
    acc_part = resolve_fitted_accessory_part(
        inst.parts.accessory_id,
        blade_id=inst.parts.blade_id,
        grip_id=inst.parts.grip_id,
    )
    elem_part = resolve_fitted_element_part(inst.parts.element_id, blade_id=inst.parts.blade_id)
    mat_part = resolve_fitted_material_part(maker, inst.rarity, elem_id=inst.parts.element_id)
    _ensure_material_rarity(mat_part, inst.rarity)

    prefix_txt, title_txt, _ = resolve_weapon_name(
        inst.parts.blade_id,
        inst.parts.grip_id,
        inst.parts.accessory_id,
        inst.parts.element_id,
    )
    title_np = get_or_create_title_part(title_txt)
    prefix_np = get_or_create_prefix_part(prefix_txt)

    dd.WeaponTypeDefinition = wt
    dd.BalanceDefinition = bal
    if mfr_obj is not None:
        dd.ManufacturerDefinition = mfr_obj
    dd.ManufacturerGradeIndex = inst.game_stage
    if body_part is not None:
        dd.BodyPartDefinition = body_part
    dd.GripPartDefinition = grip_part
    dd.BarrelPartDefinition = blade_part
    dd.SightPartDefinition = None
    dd.StockPartDefinition = None
    dd.ElementalPartDefinition = elem_part
    dd.Accessory1PartDefinition = acc_part
    dd.Accessory2PartDefinition = None
    if mat_part is not None:
        dd.MaterialPartDefinition = mat_part
    dd.PrefixPartDefinition = prefix_np
    dd.TitlePartDefinition = title_np
    dd.GameStage = inst.game_stage
    if inst.unique_id:
        dd.UniqueId = inst.unique_id


def sync_live_weapon_properties(
    wpn: Any,
    inst: MeleeWeaponInstance,
    card: ComputedCardStats,
) -> None:
    """Keep live `WillowWeapon` runtime stats & Prefix/Title/Visual parts aligned with `ComputedCardStats`."""
    try:
        prefix_txt, title_txt, _ = resolve_weapon_name(
            inst.parts.blade_id,
            inst.parts.grip_id,
            inst.parts.accessory_id,
            inst.parts.element_id,
        )
        title_np = get_or_create_title_part(title_txt)
        prefix_np = get_or_create_prefix_part(prefix_txt)
        dd = copy.copy(wpn.DefinitionData)
        changed = sync_definition_data_visual_parts(dd)
        if inst.unique_id and int(getattr(dd, "UniqueId", 0) or 0) == 0:
            dd.UniqueId = inst.unique_id
            changed = True
        if title_np is not None and getattr(dd, "TitlePartDefinition", None) != title_np:
            dd.TitlePartDefinition = title_np
            changed = True
        if prefix_np is not None and getattr(dd, "PrefixPartDefinition", None) != prefix_np:
            dd.PrefixPartDefinition = prefix_np
            changed = True
        if changed:
            wpn.DefinitionData = dd
    except Exception:
        pass

    fire_iv = round(1.0 / max(0.25, card.swing_speed), 4)
    elem_dps = float(max(0.0, card.elemental_dps))
    # Live rarity level drives loot-beam / item-card colour when the engine reads it off the item.
    stamp_inventory_rarity(wpn, inst.rarity)
    for prop_name, val in (
        ("InstantHitDamageBaseValue", float(card.card_damage)),
        ("InstantHitDamage", float(card.card_damage)),
        ("StatusEffectDamageBaseValue", elem_dps),
        ("StatusEffectDamage", elem_dps),
        ("BaseStatusEffectChanceModifierBaseValue", 1.0),
        ("BaseStatusEffectChanceModifier", 1.0),
        ("StatusEffectChanceModifierBaseValue", 1.0),
        ("StatusEffectChanceModifier", 1.0),
        ("FireIntervalBaseValue", fire_iv),
        ("FireInterval", fire_iv),
        ("ShotCostBaseValue", 0),
        ("ShotCost", 0),
        ("ClipSizeBaseValue", MELEE_CLIP_SIZE),
        ("ClipSize", MELEE_CLIP_SIZE),
        ("ReloadCnt", MELEE_CLIP_SIZE),
        ("Spread", 0.0),
    ):
        if hasattr(wpn, prop_name):
            try:
                setattr(wpn, prop_name, val)
            except Exception:
                pass


def get_local_player_controller() -> Any:
    if unrealsdk is None:
        return None
    try:
        from mods_base import get_pc  # type: ignore[import-not-found]

        pc = get_pc()
        if pc is not None:
            return pc
    except Exception:
        pass
    try:
        eng = unrealsdk.find_class("Engine").ClassDefaultObject.GetEngine()
        for lp in getattr(eng, "GamePlayers", []) or []:
            actor = getattr(lp, "Actor", None)
            if actor is not None:
                return actor
    except Exception:
        pass
    return None


def get_player_level(pc: Any = None) -> int:
    pc = pc or get_local_player_controller()
    if pc is None:
        return 1
    for obj, attr in (
        (getattr(pc, "PlayerReplicationInfo", None), "ExpLevel"),
        (getattr(pc, "Pawn", None), "ExpLevel"),
    ):
        if obj is not None and hasattr(obj, attr):
            try:
                lvl = int(getattr(obj, attr))
                if lvl >= 1:
                    return lvl
            except Exception:
                pass
    return 1


def _ensure_mission_backed_up() -> Any:
    global _MISSION_BACKUP
    try:
        unrealsdk.load_package("GD_Episode01")
    except Exception:
        pass
    mission = unrealsdk.find_object("MissionDefinition", SPAWNER_MISSION_PATH)
    if mission is None:
        return None
    if _MISSION_BACKUP is None:
        _MISSION_BACKUP = {
            "mission": mission,
            "game_stage": int(mission.GameStage),
            "enable_alt": bool(getattr(mission, "bEnableAltReward", False)),
            "reward_items": list(mission.Reward.RewardItems),
            "reward_pools": list(mission.Reward.RewardItemPools),
        }
    return mission


def _restore_spawner_mission_if_needed() -> None:
    global _MISSION_BACKUP
    if _MISSION_BACKUP is None:
        return
    try:
        m = _MISSION_BACKUP["mission"]
        m.GameStage = _MISSION_BACKUP["game_stage"]
        if hasattr(m, "bEnableAltReward"):
            m.bEnableAltReward = _MISSION_BACKUP["enable_alt"]
        rew = copy.copy(m.Reward)
        rew.RewardItems = _MISSION_BACKUP["reward_items"]
        rew.RewardItemPools = _MISSION_BACKUP["reward_pools"]
        m.Reward = rew
    except Exception as exc:
        log_warn(f"Could not restore reward spawner mission backup: {exc}")
    finally:
        _MISSION_BACKUP = None


def _grant_one_now(inst: MeleeWeaponInstance, pc: Any = None) -> bool:
    global _MISSION_RESTORE_TICKS
    pc = pc or get_local_player_controller()
    if pc is None or getattr(pc, "Pawn", None) is None:
        return False
    mission = _ensure_mission_backed_up()
    if mission is None:
        return False

    maker = BLADES[inst.parts.blade_id].manufacturer
    bal = STATE.balances.get((maker, inst.rarity)) or STATE.balances.get((maker, Rarity.UNCOMMON))
    if bal is None:
        return False

    _PENDING_GRANTS[maker].append(inst)
    mission.GameStage = int(inst.game_stage)
    if hasattr(mission, "bEnableAltReward"):
        mission.bEnableAltReward = False
    rew = copy.copy(mission.Reward)
    rew.RewardItems = [bal]
    rew.RewardItemPools = []
    mission.Reward = rew
    _MISSION_RESTORE_TICKS = 30
    pc.ServerGrantMissionRewards(mission, False)
    return True


def cancel_pending_spawns() -> None:
    """Cancel deferred grants/drops and restore the donor mission immediately (mod disable/travel)."""
    global _DEFERRED_STEP_TICKS, _MISSION_RESTORE_TICKS, _LAST_DROP_COUNT
    _DEFERRED_SPAWN_QUEUE.clear()
    _PENDING_DROP_INSTANCES.clear()
    for q in _PENDING_GRANTS.values():
        q.clear()
    _DEFERRED_STEP_TICKS = 0
    _MISSION_RESTORE_TICKS = 0
    _LAST_DROP_COUNT = 0
    _restore_spawner_mission_if_needed()
    _STAMPED_UIDS.clear()


def tick_spawner_restore() -> None:
    """Drain multi-weapon grant queue 1 item every 3 ticks, then restore `M_Ep1_Champion`."""
    global _DEFERRED_STEP_TICKS, _MISSION_RESTORE_TICKS
    if _DEFERRED_SPAWN_QUEUE:
        if _DEFERRED_STEP_TICKS > 0:
            _DEFERRED_STEP_TICKS -= 1
            return
        nxt = _DEFERRED_SPAWN_QUEUE.popleft()
        _grant_one_now(nxt)
        _DEFERRED_STEP_TICKS = 3
        return

    if _MISSION_RESTORE_TICKS > 0:
        _MISSION_RESTORE_TICKS -= 1
        if _MISSION_RESTORE_TICKS == 0:
            _restore_spawner_mission_if_needed()
            for q in _PENDING_GRANTS.values():
                q.clear()


def _pin_plc_slots_for_instance(plc: Any, inst: MeleeWeaponInstance) -> dict[str, tuple[bool, list[Any]]]:
    """Temporarily pin `plc`'s Grip, Element, Accessory1, and Material slots to `inst`'s exact fitted parts."""
    saved: dict[str, tuple[bool, list[Any]]] = {}
    if plc is None:
        return saved
    maker = BLADES[inst.parts.blade_id].manufacturer
    pin_targets = {
        "GripPartData": resolve_fitted_grip_part(inst.parts.grip_id, blade_id=inst.parts.blade_id),
        "ElementalPartData": resolve_fitted_element_part(inst.parts.element_id, blade_id=inst.parts.blade_id),
        "Accessory1PartData": resolve_fitted_accessory_part(
            inst.parts.accessory_id,
            blade_id=inst.parts.blade_id,
            grip_id=inst.parts.grip_id,
        ),
        "MaterialPartData": resolve_fitted_material_part(
            maker,
            inst.rarity,
            elem_id=inst.parts.element_id,
        ),
    }
    for slot_name, part_obj in pin_targets.items():
        if part_obj is None:
            continue
        slot = getattr(plc, slot_name, None)
        if slot is None:
            continue
        orig_rows = [copy.copy(r) for r in (getattr(slot, "WeightedParts", []) or [])]
        orig_enabled = bool(getattr(slot, "bEnabled", False))
        saved[slot_name] = (orig_enabled, orig_rows)
        if orig_rows:
            r0 = copy.copy(orig_rows[0])
            r0.Part = part_obj
            r0.Manufacturers = []
            slot.WeightedParts = [r0]
            slot.bEnabled = True
    return saved


def _restore_plc_slots(plc: Any, saved: dict[str, tuple[bool, list[Any]]]) -> None:
    if plc is None or not saved:
        return
    for slot_name, (orig_enabled, orig_rows) in saved.items():
        slot = getattr(plc, slot_name, None)
        if slot is not None:
            slot.WeightedParts = orig_rows
            slot.bEnabled = orig_enabled


def _apply_behavior_to_context(spawner: Any, context_obj: Any) -> None:
    """Invoke `Behavior_SpawnLootAroundPoint.ApplyBehaviorToContext` with dynamic struct lookup."""
    func = spawner.Class._find("ApplyBehaviorToContext")
    fields = {str(prop.Name): prop for prop in func._properties()}
    kernel_struct = str(fields["KernelInfo"].Struct.Name)
    event_struct = str(fields["EventData"].Struct.Name)
    spawner.ApplyBehaviorToContext(
        ContextObject=context_obj,
        KernelInfo=unrealsdk.make_struct(kernel_struct),
        SelfObject=None,
        MyInstigatorObject=None,
        OtherEventParticipantObject=None,
        EventData=unrealsdk.make_struct(event_struct),
    )


def _set_spawner_custom_location(spawner: Any, x: float, y: float, z: float) -> None:
    loc_vec = unrealsdk.make_struct("Vector", X=float(x), Y=float(y), Z=float(z))
    custom = copy.copy(spawner.CustomLocation)
    loc_struct = spawner.Class._find("CustomLocation").Struct
    for prop in loc_struct._properties():
        pname = str(prop.Name)
        if hasattr(prop, "Struct") and prop.Struct is not None and str(prop.Struct.Name) == "Vector":
            setattr(custom, pname, loc_vec)
        elif "usecustom" in pname.lower() or "bcustom" in pname.lower():
            try:
                setattr(custom, pname, True)
            except Exception:
                pass
    spawner.CustomLocation = custom


def drop_melee_weapons_in_front(instances: list[MeleeWeaponInstance], pc: Any = None) -> int:
    """Drop 1 or more `WillowPickup` Melee Arsenal weapons directly in front of the player (zero XP!)."""
    global _LAST_DROP_COUNT
    if not instances or unrealsdk is None or not ensure_definitions_built():
        return 0

    pc = pc or get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pc is None or pawn is None:
        log_error("Cannot drop Melee Weapon: load into a character in-game first.")
        return 0

    spawner = STATE.drop_spawner
    if spawner is None:
        return 0

    ploc = pawn.Location
    base_x, base_y, base_z = float(ploc.X), float(ploc.Y), float(ploc.Z)
    yaw_units = int(
        getattr(getattr(pc, "Rotation", None), "Yaw", 0)
        or getattr(getattr(pawn, "Rotation", None), "Yaw", 0)
        or 0
    )
    base_yaw_rad = yaw_units * (2.0 * math.pi / 65536.0)

    total = len(instances)
    dropped_total = 0

    for idx, inst in enumerate(instances):
        maker = BLADES[inst.parts.blade_id].manufacturer
        bal = STATE.balances.get((maker, inst.rarity)) or STATE.balances.get((maker, Rarity.UNCOMMON))
        pool = STATE.balance_drop_pools.get((maker, inst.rarity)) or STATE.balance_drop_pools.get(
            (maker, Rarity.UNCOMMON)
        )
        if bal is None or pool is None:
            continue

        # Fan multiple weapons neatly in a semi-circle ~165 UU in front of the player
        if total == 1:
            ang_offset = 0.0
        else:
            ang_offset = math.radians(-40.0 + (80.0 * idx / max(1, total - 1)))
        ang = base_yaw_rad + ang_offset
        drop_x = base_x + math.cos(ang) * 165.0
        drop_y = base_y + math.sin(ang) * 165.0
        drop_z = base_z + 90.0

        plc = getattr(bal, "RuntimePartListCollection", None) or getattr(
            bal, "WeaponPartListCollection", None
        )
        saved_slots = _pin_plc_slots_for_instance(plc, inst)
        _PENDING_GRANTS[maker].append(inst)
        _PENDING_DROP_INSTANCES.append(inst)
        _LAST_DROP_COUNT = 0

        try:
            spawner.ItemPools = [pool]
            _set_spawner_custom_location(spawner, drop_x, drop_y, drop_z)
            spawner.CircularScatterRadius = 18.0 if total > 1 else 25.0
            spawner.SpawnVelocityRelativeTo = 1
            spawner.SpawnVelocity = unrealsdk.make_struct(
                "Vector",
                X=math.cos(ang) * 55.0,
                Y=math.sin(ang) * 55.0,
                Z=115.0,
            )
            _apply_behavior_to_context(spawner, pawn)
            dropped_total += max(1, _LAST_DROP_COUNT) if _LAST_DROP_COUNT > 0 else 1
        except Exception as exc:
            log_error(f"drop_melee_weapons_in_front error on {inst.parts.blade_id}: {exc!r}")
        finally:
            _restore_plc_slots(plc, saved_slots)
            while _PENDING_DROP_INSTANCES:
                _PENDING_DROP_INSTANCES.popleft()
            while _PENDING_GRANTS[maker]:
                _PENDING_GRANTS[maker].popleft()

    if dropped_total > 0:
        log_info(f"Dropped {dropped_total} Melee Arsenal weapon pickup(s) in front of player.")
    return dropped_total


def spawn_melee_weapon_batch(
    instances: list[MeleeWeaponInstance],
    pc: Any = None,
    delivery: str = "drop",
) -> int:
    """Spawn 1 or more `MeleeWeaponInstance`s (`delivery='drop'` in front of player or `'backpack'`)."""
    global _DEFERRED_STEP_TICKS
    if not instances or unrealsdk is None or not ensure_definitions_built():
        return 0

    pc = pc or get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pc is None or pawn is None:
        log_error("Cannot spawn Melee Weapon: load into a character in-game first.")
        return 0

    if delivery != "backpack" and STATE.drop_spawner is not None:
        dropped = drop_melee_weapons_in_front(instances, pc=pc)
        if dropped > 0:
            return dropped

    try:
        if not _grant_one_now(instances[0], pc=pc):
            return 0
        for extra in instances[1:]:
            _DEFERRED_SPAWN_QUEUE.append(extra)
        if len(instances) > 1:
            _DEFERRED_STEP_TICKS = 3
        log_info(f"Queued/granted {len(instances)} Melee Arsenal weapon(s) directly to Backpack.")
        return len(instances)
    except Exception as exc:
        _restore_spawner_mission_if_needed()
        log_error(f"spawn_melee_weapon_batch error: {exc!r}\n{traceback.format_exc()}")
        return 0


def spawn_melee_weapon_instance(
    inst: MeleeWeaponInstance,
    pc: Any = None,
    delivery: str = "drop",
    yaw_offset_deg: float = 0.0,
) -> int:
    return spawn_melee_weapon_batch([inst], pc=pc, delivery=delivery)


def on_place_spawned_items_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Stamp exact player level (`GameStage`), `UniqueId`, and names onto dropped Melee Arsenal pickups."""
    global _LAST_DROP_COUNT
    if STATE.drop_spawner is None or obj != STATE.drop_spawner:
        return None
    try:
        spawned = list(getattr(args, "SpawnedLoot", []) or [])
        _LAST_DROP_COUNT = len(spawned)
        for pickup in spawned:
            wpn = unwrap_weapon_object(pickup)
            if wpn is None:
                continue
            inst = _PENDING_DROP_INSTANCES.popleft() if _PENDING_DROP_INSTANCES else None
            if inst is not None:
                dd = copy.copy(wpn.DefinitionData)
                apply_instance_to_definition_data(dd, inst)
                wpn.DefinitionData = dd
                if inst.unique_id:
                    _STAMPED_UIDS.add(int(inst.unique_id))
                stamp_inventory_rarity(pickup, inst.rarity)
            resolve_melee_weapon(wpn, sync_weapon_props=True)
    except Exception as exc:
        log_error(f"on_place_spawned_items_post error: {exc!r}")
    return None


# ---------------------------------------------------------------------------
# Engine Hook Callbacks for Initialization, Naming & 1st-Person Animations
# ---------------------------------------------------------------------------

def _try_stamp_pending_grant(dd: Any) -> bool:
    if dd is None or not is_melee_definition_data(dd):
        return False
    changed = False
    cur_uid = int(getattr(dd, "UniqueId", 0) or 0)
    if not (cur_uid and cur_uid in _STAMPED_UIDS):
        bal_p = obj_path(getattr(dd, "BalanceDefinition", None))
        wt_p = obj_path(getattr(dd, "WeaponTypeDefinition", None))
        maker = STATE.wt_path_to_maker.get(wt_p)
        if maker is None and bal_p in STATE.balance_path_to_meta:
            maker = STATE.balance_path_to_meta[bal_p][0]

        if maker is not None and _PENDING_GRANTS[maker]:
            inst = _PENDING_GRANTS[maker].popleft()
            apply_instance_to_definition_data(dd, inst)
            changed = True
            new_uid = int(getattr(dd, "UniqueId", 0) or 0)
            if new_uid:
                _STAMPED_UIDS.add(new_uid)

    if sync_definition_data_visual_parts(dd):
        changed = True
    return changed


def on_weapon_initialize_from_def_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    try:
        for name in dir(args):
            val = getattr(args, name, None)
            if val is not None and hasattr(val, "BalanceDefinition") and hasattr(val, "BarrelPartDefinition"):
                dd_copy = copy.copy(val)
                if _try_stamp_pending_grant(dd_copy):
                    try:
                        setattr(args, name, dd_copy)
                    except Exception:
                        _try_stamp_pending_grant(val)
                break
    except Exception as exc:
        log_error(f"on_weapon_initialize_from_def_pre error: {exc!r}")
    return None


def on_weapon_initialize_internal_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    try:
        dd = getattr(obj, "DefinitionData", None)
        if dd is not None and is_melee_definition_data(dd):
            dd_copy = copy.copy(dd)
            if _try_stamp_pending_grant(dd_copy):
                obj.DefinitionData = dd_copy
    except Exception as exc:
        log_error(f"on_weapon_initialize_internal_pre error: {exc!r}")
    return None


def on_weapon_initialize_internal_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Ensure newly assembled Melee Weapons start with a full magazine (`ReloadCnt = 100`) & synced stats."""
    if is_melee_weapon(obj):
        resolve_melee_weapon(obj, sync_weapon_props=True)
    return None


def on_choose_random_name_parts_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    if is_melee_weapon(obj):
        resolve_melee_weapon(obj, sync_weapon_props=True)
    return None


def on_play_weapon_animation_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Block 0.2667s pistol gunshot recoil (`Fire_*`) on Melee Weapons without touching `DefinitionData`."""
    if not is_melee_weapon(obj):
        return None
    seq_name = str(getattr(args, "Sequence", "") or "")
    if seq_name.startswith("Fire"):
        return Block, 0.0
    return None


def on_init_construct_preview_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Scale Melee Arsenal UI preview `MeshComponent` (`WillowWeapon:InitConstructPreview` POST)."""
    if ret is None:
        return None
    try:
        inv_def = getattr(args, "InvDefinition", None) or getattr(args, "InvDef", None)
        is_melee_preview = (
            (inv_def is not None and obj_path(inv_def) in STATE.wt_path_to_maker)
            or is_melee_weapon(obj)
        )
        if not is_melee_preview:
            return None

        if not STATE.logged_preview_diag:
            STATE.logged_preview_diag = True
            rot = getattr(ret, "Rotation", None)
            trans = getattr(ret, "Translation", None)
            rot_txt = (
                f"({int(rot.Pitch)},{int(rot.Yaw)},{int(rot.Roll)})"
                if rot is not None and hasattr(rot, "Roll")
                else repr(rot)
            )
            trans_txt = (
                f"({float(trans.X):.2f},{float(trans.Y):.2f},{float(trans.Z):.2f})"
                if trans is not None and hasattr(trans, "X")
                else repr(trans)
            )
            s3d = getattr(ret, "Scale3D", None)
            s3d_txt = (
                f"({float(s3d.X):.2f},{float(s3d.Y):.2f},{float(s3d.Z):.2f})"
                if s3d is not None and hasattr(s3d, "X")
                else repr(s3d)
            )
            log_info(
                f"UI InitConstructPreview component={getattr(getattr(ret, 'Class', None), 'Name', '?')} "
                f"scale={getattr(ret, 'Scale', None)!r} scale3d={s3d_txt} rot={rot_txt} trans={trans_txt}"
            )

        target_scale = float(STATE.preview_mesh_scale)
        if target_scale > 0.0:
            applied = False
            if hasattr(ret, "SetScale"):
                try:
                    ret.SetScale(target_scale)
                    applied = True
                except Exception:
                    applied = False
            if not applied and hasattr(ret, "Scale"):
                try:
                    ret.Scale = target_scale
                except Exception:
                    pass
    except Exception as exc:
        log_warn(f"on_init_construct_preview_post warning: {exc!r}")
    return None

