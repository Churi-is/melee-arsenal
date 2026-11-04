"""Atomic JSON sidecar persistence for Melee Arsenal weapons."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .enums import Rarity
from .loot_roller import validate_weapon_parts
from .parts.accessories import NO_ACCESSORY_ID
from .parts.blades import BLADES
from .parts.elements import NO_ELEMENT_ID
from .parts.weapon import MeleeWeaponInstance, MeleeWeaponParts

PACKAGE_ROOT: str = "MeleeArsenalDev"
SAVE_SCHEMA: int = 1


def sanitize_save_name(save_name: str | None) -> str:
    """Validate and sanitize a Borderlands 2 save file name (rejects empty/'None' on fresh characters)."""
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(save_name or "").strip())
    if not cleaned or cleaned in (".", "..") or cleaned.lower() == "none":
        raise ValueError(f"Invalid or unassigned save name: {save_name!r}")
    return cleaned


def save_record_path(save_dir: Path, save_name: str) -> Path:
    return Path(save_dir) / f"{sanitize_save_name(save_name)}.json"


def serialize_weapon(
    weapon: MeleeWeaponInstance,
    previous_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a weapon into a sidecar record, retaining its stored level when available."""
    prev_level = (previous_record or {}).get("level")
    if isinstance(prev_level, dict) and prev_level.get("game_stage", 0) >= 1:
        stage = int(prev_level["game_stage"])
        grade = int(prev_level.get("grade_index", stage))
    else:
        stage = int(weapon.game_stage)
        grade = int(weapon.grade_index)

    if int(weapon.unique_id) <= 0:
        raise ValueError("Cannot serialize a Melee weapon without a positive UniqueId")
    maker = BLADES[weapon.parts.blade_id].manufacturer.value
    return {
        "schema": SAVE_SCHEMA,
        "unique_id": int(weapon.unique_id),
        "manufacturer": maker,
        "rarity": int(weapon.rarity),
        "balance": f"{PACKAGE_ROOT}.Balance_Melee_{maker}_{weapon.rarity.name}",
        "parts": {
            "blade_id": weapon.parts.blade_id,
            "grip_id": weapon.parts.grip_id,
            "accessory_id": weapon.parts.accessory_id,
            "element_id": weapon.parts.element_id,
        },
        "level": {
            "game_stage": stage,
            "grade_index": grade,
        },
    }


def deserialize_weapon(record: dict[str, Any]) -> MeleeWeaponInstance:
    """Reconstruct and validate a `MeleeWeaponInstance` from a sidecar JSON record."""
    schema = int(record.get("schema", SAVE_SCHEMA))
    if schema != SAVE_SCHEMA:
        raise ValueError(f"Unsupported melee sidecar schema: {schema}")
    unique_id = int(record["unique_id"])
    if unique_id < 0:
        raise ValueError(f"Invalid negative UniqueId: {unique_id}")
    parts_raw = record["parts"]
    parts = MeleeWeaponParts(
        blade_id=str(parts_raw["blade_id"]),
        grip_id=str(parts_raw["grip_id"]),
        accessory_id=str(parts_raw.get("accessory_id", NO_ACCESSORY_ID)),
        element_id=str(parts_raw.get("element_id", NO_ELEMENT_ID)),
    )
    errors = validate_weapon_parts(parts)
    if errors:
        raise ValueError(f"Invalid persisted melee parts: {'; '.join(errors)}")

    level_info = record.get("level") or {}
    stage = max(1, int(level_info.get("game_stage", 1)))
    grade = max(0, int(level_info.get("grade_index", stage)))
    rarity = Rarity.from_bl2_level(int(record.get("rarity", 2)))

    maker = BLADES[parts.blade_id].manufacturer.value
    recorded_maker = record.get("manufacturer")
    if recorded_maker is not None and str(recorded_maker) != maker:
        raise ValueError(f"Sidecar manufacturer {recorded_maker!r} does not match blade maker {maker!r}")
    expected_balance = f"{PACKAGE_ROOT}.Balance_Melee_{maker}_{rarity.name}"
    recorded_balance = record.get("balance")
    if recorded_balance is not None and str(recorded_balance) != expected_balance:
        raise ValueError(f"Sidecar balance {recorded_balance!r} does not match {expected_balance!r}")

    return MeleeWeaponInstance(
        unique_id=unique_id,
        game_stage=stage,
        rarity=rarity,
        parts=parts,
        grade_index=grade,
    )


def read_save_records(target: Path) -> dict[str, dict[str, Any]]:
    target = Path(target)
    if not target.exists():
        return {}
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object in {target}, got {type(raw).__name__}")
    for key, rec in raw.items():
        if str(key).startswith("_"):
            continue
        if not isinstance(rec, dict):
            raise ValueError(f"Sidecar entry {key!r} is not an object")
        schema = int(rec.get("schema", SAVE_SCHEMA))
        if schema != SAVE_SCHEMA:
            raise ValueError(f"Unsupported melee sidecar schema for {key!r}: {schema}")
    return raw


def quarantine_corrupt_save(target: Path) -> Path | None:
    """Move an unreadable sidecar aside so a fresh snapshot can proceed without deleting it."""
    target = Path(target)
    if not target.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    quarantined = target.with_name(f"{target.stem}.corrupt-{stamp}{target.suffix}")
    suffix = 0
    while quarantined.exists():
        suffix += 1
        quarantined = target.with_name(f"{target.stem}.corrupt-{stamp}-{suffix}{target.suffix}")
    target.replace(quarantined)
    return quarantined


def write_save_records_atomic(
    target: Path,
    updates: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge `updates` into `target` atomically, keeping one last-known-good `.bak`.

    The read-back validation happens before any rename.  If the existing file is corrupt,
    this raises rather than silently replacing it; callers may quarantine it explicitly.
    """
    target = Path(target)
    merged = read_save_records(target)
    merged.update(updates)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, target.with_suffix(".bak"))
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return merged
