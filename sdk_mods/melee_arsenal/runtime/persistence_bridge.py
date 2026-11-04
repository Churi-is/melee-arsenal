"""Save-game sidecar persistence & `ValidateWeaponDefinition` sanity-check guard for Melee Arsenal."""
from __future__ import annotations

import copy
import sys
import time
from dataclasses import replace
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
    quarantine_corrupt_save,
    read_save_records,
    save_record_path,
    serialize_weapon,
    write_save_records_atomic,
)
from .definitions import ensure_definitions_built
from .log import log_debug, log_debug_once, log_error, log_info, log_warn
from .object_identity import cache_key
from .resolver import (
    allocate_unique_id,
    apply_instance_to_definition_data,
    get_local_player_controller,
    is_melee_definition_data,
    resolve_melee_weapon,
    spawn_melee_weapon_batch,
    unwrap_weapon_object,
)


SAVES_DIR: Path = Path(MODS_DIR) / "_melee_arsenal_saves"

_PENDING_RESTORES: dict[str, dict[int, float]] = {}
_RESTORE_ACK_TIMEOUT_SEC: float = 20.0


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
    seen_ids: set[str] = set()
    inv_mgr = getattr(pawn, "InvManager", None) if pawn is not None else None
    if inv_mgr is None:
        return found

    try:
        cur = getattr(inv_mgr, "InventoryChain", None)
        steps = 0
        while cur is not None and steps < 64:
            steps += 1
            wpn = unwrap_weapon_object(cur)
            if wpn is not None and cache_key(wpn) not in seen_ids:
                seen_ids.add(cache_key(wpn))
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
            if wpn is not None and cache_key(wpn) not in seen_ids:
                seen_ids.add(cache_key(wpn))
                if resolve_melee_weapon(wpn, sync_weapon_props=False) is not None:
                    found.append((wpn, "backpack"))
    except Exception as exc:
        log_debug(f"Backpack scan failed: {exc!r}")

    return found


def _read_sidecar_for_update(target: Path) -> dict[str, dict[str, Any]]:
    try:
        return read_save_records(target)
    except (ValueError, OSError) as exc:
        quarantined = quarantine_corrupt_save(target)
        log_warn(f"Quarantined unreadable melee sidecar {target.name}: {exc!r} -> {quarantined}")
        return {}


def _assign_live_unique_id(wpn: Any, inst: Any, reserved: set[int]) -> Any | None:
    """Commit a fresh positive UniqueId to the live weapon before it can be serialized."""
    new_id = allocate_unique_id(reserved)
    try:
        dd = copy.copy(wpn.DefinitionData)
        dd.UniqueId = new_id
        wpn.DefinitionData = dd
        if int(getattr(wpn.DefinitionData, "UniqueId", 0) or 0) != new_id:
            raise ValueError("engine did not retain the assigned UniqueId")
    except Exception as exc:
        log_error(f"Could not assign a stable UniqueId for sidecar save: {exc!r}")
        return None
    reserved.add(new_id)
    return replace(inst, unique_id=new_id)


def clear_pending_restores() -> None:
    _PENDING_RESTORES.clear()


def acknowledge_restored_inventory(pc: Any = None) -> None:
    """Drop restore acknowledgements once the granted UniqueIds are actually carried."""
    if not _PENDING_RESTORES:
        return
    pc = pc or get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None:
        return
    present: set[int] = set()
    for wpn, _slot in _collect_carried_melee_weapons(pawn):
        resolved = resolve_melee_weapon(wpn, sync_weapon_props=False)
        if resolved is not None and int(resolved[0].unique_id) > 0:
            present.add(int(resolved[0].unique_id))
    now = time.monotonic()
    for save_name, pending in list(_PENDING_RESTORES.items()):
        for uid, started in list(pending.items()):
            if uid in present:
                pending.pop(uid, None)
            elif now - started > _RESTORE_ACK_TIMEOUT_SEC:
                pending.pop(uid, None)
                log_warn(f"Restore acknowledgement timed out for {save_name} uid={uid}; will retry on next map load.")
        if not pending:
            _PENDING_RESTORES.pop(save_name, None)


def snapshot_player_melee_inventory(pc: Any = None, explicit_save_name: str | None = None) -> int:
    """Persist all carried Melee Arsenal weapons for the active character to atomic JSON sidecar."""
    pc = pc or get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    if pawn is None:
        return 0

    save_name = explicit_save_name or _get_save_name_from_pc(pc)
    if not save_name:
        return 0

    target = save_record_path(SAVES_DIR, save_name)
    existing = _read_sidecar_for_update(target)

    carried = _collect_carried_melee_weapons(pawn)
    updates: dict[str, dict[str, Any]] = {}
    carried_uids: list[str] = []
    reserved: set[int] = {int(k) for k in existing if str(k).lstrip("-").isdigit()}
    zero_id_record = existing.get("0") if isinstance(existing.get("0"), dict) else None
    zero_id_record_used = False

    for wpn, slot_tag in carried:
        resolved = resolve_melee_weapon(wpn, sync_weapon_props=False)
        if resolved is None:
            continue
        inst, _ = resolved
        previous = existing.get(str(int(inst.unique_id)))
        if int(inst.unique_id) <= 0 or str(int(inst.unique_id)) in updates:
            if int(inst.unique_id) <= 0 and zero_id_record is not None and not zero_id_record_used:
                previous = zero_id_record
                zero_id_record_used = True
            inst = _assign_live_unique_id(wpn, inst, reserved)
            if inst is None:
                continue
        key = str(int(inst.unique_id))
        rec = serialize_weapon(inst, previous_record=previous)
        rec["carried_slot"] = slot_tag
        updates[key] = rec
        carried_uids.append(key)

    if zero_id_record_used and "0" in existing:
        updates["0"] = {**zero_id_record, "carried_slot": ""}

    for k, existing_rec in existing.items():
        if k.startswith("_") or not isinstance(existing_rec, dict):
            continue
        if k not in updates and existing_rec.get("carried_slot"):
            updated_rec = dict(existing_rec)
            updated_rec["carried_slot"] = ""
            updates[k] = updated_rec

    try:
        write_save_records_atomic(target, updates)
        if carried_uids:
            log_info(f"Saved {len(carried_uids)} carried Melee Arsenal weapon(s) to {target.name}.")
        return len(carried_uids)
    except Exception as exc:
        log_error(f"Failed writing melee sidecar {save_name!r}: {exc!r}")
        return 0


def restore_player_melee_inventory(pc: Any = None, explicit_save_name: str | None = None) -> int:
    """Fallback restore of carried Melee Arsenal weapons missing from inventory after load.

    Restoration is acknowledged by UniqueId, not by the number of queued grants.  A second
    call while the spawner is still delivering the first batch is a no-op for those IDs.
    """
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
    zero_present = 0
    for wpn, _ in _collect_carried_melee_weapons(pawn):
        res = resolve_melee_weapon(wpn, sync_weapon_props=False)
        if res is not None:
            uid = int(res[0].unique_id)
            if uid > 0:
                present_uids.add(uid)
            else:
                zero_present += 1

    pending = _PENDING_RESTORES.setdefault(save_name, {})
    missing_instances = []
    migration_updates: dict[str, dict[str, Any]] = {}
    reserved = set(present_uids) | {int(k) for k in records if str(k).lstrip("-").isdigit()}
    for key, rec in records.items():
        if key.startswith("_") or not isinstance(rec, dict):
            continue
        slot_tag = str(rec.get("carried_slot") or "")
        if not slot_tag:
            continue
        try:
            inst = deserialize_weapon(rec)
            uid = int(inst.unique_id)
            if uid <= 0:
                # A zero-ID record cannot be matched to a carried weapon; avoid duplicating one.
                if zero_present > 0:
                    log_warn(f"Skipping zero-ID sidecar entry for {save_name}; a zero-ID weapon is already carried.")
                    continue
                inst = replace(inst, unique_id=allocate_unique_id(reserved))
                reserved.add(int(inst.unique_id))
                migration_updates[str(int(inst.unique_id))] = {**serialize_weapon(inst, previous_record=rec), "carried_slot": slot_tag}
                migration_updates[key] = {**rec, "carried_slot": ""}
                uid = int(inst.unique_id)
            if uid in present_uids or uid in pending:
                continue
            missing_instances.append(inst)
        except Exception as exc:
            log_warn(f"Skipping invalid melee sidecar entry {key}: {exc}")

    if migration_updates:
        try:
            merged = {k: v for k, v in records.items() if isinstance(v, dict)}
            merged.update(migration_updates)
            write_save_records_atomic(target, merged)
        except Exception as exc:
            log_error(f"Could not migrate zero-ID sidecar entry: {exc!r}")
            return 0

    if not missing_instances:
        if not pending:
            _PENDING_RESTORES.pop(save_name, None)
        return 0

    restored = spawn_melee_weapon_batch(missing_instances, pc=pc, delivery="backpack")
    if restored > 0:
        now = time.monotonic()
        for inst in missing_instances:
            pending[int(inst.unique_id)] = now
        log_info(f"Queued restore of {restored} Melee Arsenal weapon(s) from {target.name}; awaiting inventory acknowledgement.")
    else:
        _PENDING_RESTORES.pop(save_name, None)
    return restored


# Engine Hook Callbacks

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
