"""Save-game sidecar persistence & `ValidateWeaponDefinition` sanity-check guard for Melee Arsenal."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from mods_base import MODS_DIR, get_ordered_mod_list  # type: ignore[import-not-found]
    from unrealsdk.hooks import Block  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    MODS_DIR = Path(__file__).resolve().parents[2]
    get_ordered_mod_list = None  # type: ignore[assignment]
    Block = object()  # type: ignore[assignment]

from ..core.persistence import (
    deserialize_weapon,
    read_save_records,
    save_record_path,
    serialize_weapon,
    write_save_records_atomic,
)
from .definitions import ensure_definitions_built
from .log import log_debug, log_debug_once, log_error, log_info, log_warn
from .resolver import (
    apply_instance_to_definition_data,
    get_local_player_controller,
    is_melee_definition_data,
    resolve_melee_weapon,
    spawn_melee_weapon_batch,
    unwrap_weapon_object,
)


SAVES_DIR: Path = Path(MODS_DIR) / "_melee_arsenal_saves"


def _sanity_saver_active() -> bool:
    """Detect Sanity Saver so we don't conflict with its two-pass `ApplyWeaponSaveGameData` wrapper."""
    if get_ordered_mod_list is not None:
        try:
            for mod in get_ordered_mod_list():
                if str(getattr(mod, "name", "")) == "Sanity Saver":
                    return bool(getattr(mod, "is_enabled", False))
        except Exception as exc:
            log_debug(f"Sanity Saver check failed: {exc!r}")
    for name, module in list(sys.modules.items()):
        if name.endswith("SanitySaver") and getattr(module, "instance", None) is not None:
            return bool(getattr(module.instance, "IsEnabled", False))
    return False


def _get_save_name_from_pc(pc: Any) -> str | None:
    if pc is None:
        return None
    for prop in ("SaveGameName", "QuickSaveFileName"):
        val = getattr(pc, prop, None)
        if val and str(val).strip() and str(val).strip().lower() != "none":
            return Path(str(val).strip()).stem
    try:
        sgm = pc.GetSaveGameManager()
        if sgm is not None:
            last = getattr(sgm, "LastLoadedFilePath", None) or getattr(sgm, "SaveGameFile", None)
            if last and str(last).strip():
                return Path(str(last)).stem
    except Exception as exc:
        log_debug_once("persistence._get_save_name", f"GetSaveGameManager failed: {exc!r}")
    return None


def _collect_carried_melee_weapons(pawn: Any) -> list[tuple[Any, str]]:
    """Return `(wpn_obj, slot_tag)` for all Melee Arsenal weapons in equipped slots & backpack."""
    found: list[tuple[Any, str]] = []
    seen_ids: set[int] = set()
    inv_mgr = getattr(pawn, "InvManager", None) if pawn is not None else None
    if inv_mgr is None:
        return found

    try:
        cur = getattr(inv_mgr, "InventoryChain", None)
        steps = 0
        while cur is not None and steps < 64:
            steps += 1
            wpn = unwrap_weapon_object(cur)
            if wpn is not None and id(wpn) not in seen_ids:
                seen_ids.add(id(wpn))
                slot_num = int(getattr(wpn, "QuickSelectSlot", 0) or 0)
                tag = f"equip_slot_{slot_num}" if 1 <= slot_num <= 4 else "backpack"
                if resolve_melee_weapon(wpn, sync_weapon_props=False) is not None:
                    found.append((wpn, tag))
            cur = getattr(cur, "Inventory", None)
    except Exception as exc:
        log_debug(f"InventoryChain scan failed: {exc!r}")

    try:
        for item in getattr(inv_mgr, "Backpack", []) or []:
            wpn = unwrap_weapon_object(item)
            if wpn is not None and id(wpn) not in seen_ids:
                seen_ids.add(id(wpn))
                if resolve_melee_weapon(wpn, sync_weapon_props=False) is not None:
                    found.append((wpn, "backpack"))
    except Exception as exc:
        log_debug(f"Backpack scan failed: {exc!r}")

    return found


def snapshot_player_melee_inventory(pc: Any = None, explicit_save_name: str | None = None) -> int:
    """Persist all carried Melee Arsenal weapons for the active character to atomic JSON sidecar."""
    pc = pc or get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None:
        return 0

    save_name = explicit_save_name or _get_save_name_from_pc(pc)
    if not save_name:
        return 0

    try:
        target = save_record_path(SAVES_DIR, save_name)
        existing = read_save_records(target)
    except ValueError:
        return 0
    except Exception as exc:
        log_warn(f"Could not read existing sidecar for {save_name!r}: {exc}")
        existing = {}

    carried = _collect_carried_melee_weapons(pawn)
    updates: dict[str, dict[str, Any]] = {}
    carried_uids: list[str] = []

    for wpn, slot_tag in carried:
        resolved = resolve_melee_weapon(wpn, sync_weapon_props=False)
        if resolved is None:
            continue
        inst, _ = resolved
        key = str(int(inst.unique_id))
        rec = serialize_weapon(inst, previous_record=existing.get(key))
        rec["carried_slot"] = slot_tag
        updates[key] = rec
        carried_uids.append(key)

    for k, old_rec in existing.items():
        if k.startswith("_") or not isinstance(old_rec, dict):
            continue
        if k not in updates and old_rec.get("carried_slot"):
            updated_old = dict(old_rec)
            updated_old["carried_slot"] = ""
            updates[k] = updated_old

    try:
        write_save_records_atomic(target, updates)
        if carried_uids:
            log_info(f"Saved {len(carried_uids)} carried Melee Arsenal weapon(s) to {target.name}.")
        return len(carried_uids)
    except Exception as exc:
        log_error(f"Failed writing melee sidecar {save_name!r}: {exc!r}")
        return 0


def restore_player_melee_inventory(pc: Any = None, explicit_save_name: str | None = None) -> int:
    """Fallback restore of any carried Melee Arsenal weapons missing from inventory after load."""
    pc = pc or get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None or not ensure_definitions_built():
        return 0

    save_name = explicit_save_name or _get_save_name_from_pc(pc)
    if not save_name:
        return 0

    try:
        target = save_record_path(SAVES_DIR, save_name)
        records = read_save_records(target)
    except Exception as exc:
        log_debug(f"read_save_records for restore failed: {exc!r}")
        return 0

    if not records:
        return 0

    present_uids: set[int] = set()
    for wpn, _ in _collect_carried_melee_weapons(pawn):
        res = resolve_melee_weapon(wpn, sync_weapon_props=True)
        if res is not None:
            present_uids.add(int(res[0].unique_id))

    missing_instances = []
    for key, rec in records.items():
        if key.startswith("_") or not isinstance(rec, dict):
            continue
        slot_tag = str(rec.get("carried_slot") or "")
        if not slot_tag:
            continue
        try:
            inst = deserialize_weapon(rec)
            if int(inst.unique_id) not in present_uids:
                missing_instances.append(inst)
        except Exception as exc:
            log_warn(f"Skipping invalid melee sidecar entry {key}: {exc}")

    if not missing_instances:
        return 0

    restored = spawn_melee_weapon_batch(missing_instances, pc=pc, delivery="backpack")
    if restored > 0:
        log_info(f"Restored {restored} Melee Arsenal weapon(s) from {target.name}.")
    return restored


# ---------------------------------------------------------------------------
# Engine Hook Callbacks
# ---------------------------------------------------------------------------

def on_validate_weapon_definition_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Allow Melee Arsenal weapons to pass BL2's built-in weapon sanity check on load."""
    dd = getattr(args, "DefinitionData", None)
    if is_melee_definition_data(dd) and not _sanity_saver_active():
        return Block, True
    return None


def on_apply_weapon_save_data_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Re-attach live `MeleeArsenalDev` UObjects onto `SaveGame.WeaponData` before BL2 builds inventory."""
    try:
        ensure_definitions_built()
        save_game = getattr(args, "SaveGame", None)
        if save_game is None:
            return None
        save_name = _get_save_name_from_pc(obj)
        if not save_name:
            return None
        target = save_record_path(SAVES_DIR, save_name)
        records = read_save_records(target)
        if not records:
            return None

        reattached = 0
        for entry in getattr(save_game, "WeaponData", []) or []:
            dd = getattr(entry, "WeaponDefinitionData", None)
            if dd is None:
                continue
            uid_key = str(int(getattr(dd, "UniqueId", 0) or 0))
            rec = records.get(uid_key)
            if isinstance(rec, dict):
                inst = deserialize_weapon(rec)
                apply_instance_to_definition_data(dd, inst)
                reattached += 1
        if reattached > 0:
            log_info(f"Re-attached {reattached} Melee Arsenal definition(s) in ApplyWeaponSaveGameData.")
    except Exception as exc:
        log_warn(f"on_apply_weapon_save_data_pre warning: {exc!r}")
    return None


def on_save_game_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    """Snapshot carried Melee Arsenal weapons right before Borderlands 2 writes `.sav`."""
    save_file = getattr(args, "Filename", None) or getattr(args, "sFilename", None)
    explicit = Path(str(save_file)).stem if save_file else None
    pc = obj if hasattr(obj, "Pawn") else get_local_player_controller()
    snapshot_player_melee_inventory(pc=pc, explicit_save_name=explicit)
    return None
