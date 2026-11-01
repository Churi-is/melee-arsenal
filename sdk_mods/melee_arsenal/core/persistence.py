"""Atomic sidecar save persistence & AssetLibrary manifest builder.

Follows the battle-tested `Ribs Arsenal` persistence rules:
1. Never store transient UObjects in `.sav` files without sidecar restoration + AssetLibrary registration.
2. Key every weapon record by `str(int(UniqueId))` in `<sdk_mods>/_melee_arsenal_saves/<SaveName>.json`.
3. Write updates via atomic `.tmp` rename so crashes mid-save never corrupt character sidecars.
4. Preserve the original `game_stage` / `grade_index` on first sight so if BL2 resets level to 1
   before restoration runs, `repair_instance_level` restores the true level automatically.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .catalog import ACCESSORIES, BLADES, ELEMENTS, GRIPS, MANUFACTURERS
from .enums import Rarity
from .loot_roller import validate_weapon_parts
from .schemas import MeleeWeaponInstance, MeleeWeaponParts

PACKAGE_ROOT: str = "MeleeArsenalDev"


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
    """Serialize a `MeleeWeaponInstance` into a JSON-compatible sidecar record.

    Preserves previously recorded `game_stage` / `grade_index` if present (a weapon's level
    never changes in BL2, guarding against engine level-reset bugs).
    """
    prev_level = (previous_record or {}).get("level")
    if isinstance(prev_level, dict) and prev_level.get("game_stage", 0) >= 1:
        stage = int(prev_level["game_stage"])
        grade = int(prev_level.get("grade_index", stage))
    else:
        stage = int(weapon.game_stage)
        grade = int(weapon.grade_index)

    maker = BLADES[weapon.parts.blade_id].manufacturer.value
    return {
        "schema": 1,
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
    parts_raw = record["parts"]
    parts = MeleeWeaponParts(
        blade_id=str(parts_raw["blade_id"]),
        grip_id=str(parts_raw["grip_id"]),
        accessory_id=str(parts_raw.get("accessory_id", "Acc_None")),
        element_id=str(parts_raw.get("element_id", "Elem_None")),
    )
    errors = validate_weapon_parts(parts)
    if errors:
        raise ValueError(f"Invalid persisted melee parts: {'; '.join(errors)}")

    level_info = record.get("level") or {}
    stage = max(1, int(level_info.get("game_stage", 1)))
    grade = max(0, int(level_info.get("grade_index", stage)))
    rarity = Rarity.from_bl2_level(int(record.get("rarity", 2)))

    return MeleeWeaponInstance(
        unique_id=int(record["unique_id"]),
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
    return raw


def write_save_records_atomic(
    target: Path,
    updates: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge `updates` into `target` atomically via `.tmp` write + replace."""
    target = Path(target)
    merged = read_save_records(target)
    merged.update(updates)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return merged


def build_asset_sublibrary_manifest(package_name: str = PACKAGE_ROOT) -> dict[str, list[str]]:
    """Deterministic append-only asset order for `AssetLibraryManager` (Bank & Shared Stash).

    Bank and Claptrap's Stash encode items as serial numbers indexing `(sublibrary, asset_idx)`.
    Keeping a deterministic ordered manifest per UClass ensures stored melee weapons survive
    across sessions and mod updates.
    """
    balances: list[str] = []
    types: list[str] = []
    parts: list[str] = []
    names: list[str] = []

    for maker in MANUFACTURERS:
        m_name = maker.value
        types.append(f"{package_name}.WT_Melee_{m_name}")
        parts.append(f"{package_name}.BodyHost_Melee_{m_name}")
        for rarity in (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE):
            balances.append(f"{package_name}.Balance_Melee_{m_name}_{rarity.name}")
            parts.append(f"{package_name}.Material_Melee_{m_name}_{rarity.name}")

    for blade_id, blade in BLADES.items():
        parts.append(f"{package_name}.{blade_id}")
        for grip_maker, title in blade.grip_titles.items():
            safe_title = "".join(ch for ch in title if ch.isalnum())
            names.append(f"{package_name}.Title_{blade.manufacturer.value}_{grip_maker.value}_{safe_title}")

    for grip_id in GRIPS:
        parts.append(f"{package_name}.{grip_id}")

    for acc_id, acc in ACCESSORIES.items():
        parts.append(f"{package_name}.{acc_id}")
        if acc.part_id != "Acc_None":
            for maker in MANUFACTURERS:
                pref = acc.prefix_for(maker)
                safe_pref = "".join(ch for ch in pref if ch.isalnum())
                names.append(f"{package_name}.Prefix_{acc_id}_{maker.value}_{safe_pref}")

    for elem_id, elem in ELEMENTS.items():
        parts.append(f"{package_name}.{elem_id}")
        if elem.default_prefix:
            for maker in MANUFACTURERS:
                pref = elem.prefix_for(maker)
                safe_pref = "".join(ch for ch in pref if ch.isalnum())
                names.append(f"{package_name}.Prefix_{elem_id}_{maker.value}_{safe_pref}")

    return {
        "WeaponBalanceDefinition": balances,
        "WeaponTypeDefinition": types,
        "WeaponPartDefinition": parts,
        "WeaponNamePartDefinition": names,
    }
