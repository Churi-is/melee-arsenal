"""Asset probes: catalogue the objects BL2 actually has loaded instead of scoring guesses.

`runtime/resources.py` currently scans every loaded `ParticleSystem` and `AkEvent` and ranks the
paths by keyword score, because the author had no way to see the real list. Same story for the
`Behavior_Explode` donor hunt, the donor item pools, and the `AttributeInitializationData` weights.
One catalogue dump per class turns each of those hunts into one verified constant.
"""
from __future__ import annotations

from typing import Any

from .common import (
    describe_value,
    iter_all,
    keyword_match,
    log,
    obj_path,
    props_of,
    safe_get,
)

# logical name -> (class name, keyword filter on the object path, max objects kept)
CATALOGUE_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "particle_systems": ("ParticleSystem", ("explo", "impact", "melee", "blade", "slash", "spark", "splash")),
    "ak_events": ("AkEvent", ("explo", "melee", "blade", "impact", "swing", "slash", "whoosh")),
    "camera_anims": ("CameraAnim", ("melee", "explo", "pistol", "shotgun", "rocket", "slam")),
    "impact_definitions": ("ImpactDefinition", ("melee", "blade", "generic")),
    "damage_types": ("DamageTypeDefinition", ("incend", "shock", "corros", "slag", "explo", "normal", "melee")),
    "explosion_collections": ("ExplosionCollectionDefinition", ()),
    "behavior_explodes": ("Behavior_Explode", ()),
    "willow_weapon_types": ("WillowWeaponTypeDefinition", ("pistol", "melee", "tediore")),
    "card_presentations": ("InventoryCardPresentationDefinition", ()),
    "item_pools": ("ItemPoolDefinition", ("weapon", "shop", "pistol", "melee")),
    "asset_libraries": ("AssetLibrary", ()),
}

# Fields worth reading off each catalogue entry. Anything missing is simply absent from the output.
INTERESTING_FIELDS: dict[str, tuple[str, ...]] = {
    "behavior_explodes": (
        "OverrideName",
        "DamageFormula",
        "DamageRadiusFormula",
        "MomentumFormula",
        "bDisableRadiusDamageFalloff",
        "bCanDamageFriendlies",
    ),
    "willow_weapon_types": (
        "Presentation",
        "BalanceDefinition",
        "UIMeshRotation",
        "FocusRadius",
        "MinimumHorizontalPercentage",
        "MinimumVerticalPercentage",
        "ThirdPersonMeshScale",
        "bUIMeshRotationNeedsTransformFixup",
    ),
    "card_presentations": (
        "MeshRotation",
        "MeshScale",
        "BoundsScale",
        "FocusRadius",
        "CardRegion",
    ),
}


def _describe_fields(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in fields:
        value = safe_get(obj, field, None)
        if value is None:
            continue
        out[field] = describe_value(value, depth=1)
    return out


def _behaviour_classes_of(firing_mode: Any) -> dict[str, list[str]]:
    """Behaviour graph nodes reachable from a `FiringModeDefinition` (Timer/Hit/Impact events)."""
    found: dict[str, list[str]] = {}

    def _walk(label: str, behaviours: Any) -> None:
        names: list[str] = []
        try:
            for behaviour in behaviours or ():
                name = str(safe_get(safe_get(behaviour, "Class", None), "Name", "") or "")
                if name:
                    names.append(name)
        except Exception:
            pass
        if names:
            found[label] = names

    _walk("OnAnyImpact", safe_get(firing_mode, "OnAnyImpact", None))
    _walk("OnHit", safe_get(firing_mode, "OnHit", None))
    _walk("OnMiss", safe_get(firing_mode, "OnMiss", None))

    timer_names: list[str] = []
    try:
        for event in safe_get(firing_mode, "TimerEvents", None) or ():
            response = safe_get(event, "Response", None)
            for behaviour in safe_get(response, "Behaviors", None) or ():
                name = str(safe_get(safe_get(behaviour, "Class", None), "Name", "") or "")
                if name and name not in timer_names:
                    timer_names.append(name)
    except Exception:
        pass
    if timer_names:
        found["TimerEvents"] = timer_names

    for extra in ("ExplosionOverrideDefinition", "ConditionalPartSysTemplate", "PartSysTemplate"):
        value = safe_get(firing_mode, extra, None)
        if value is not None:
            found[f"_{extra}"] = [obj_path(value) or str(safe_get(value, "Name", "") or "")]
    return found


def _collect_firing_modes() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for firing_mode in iter_all("FiringModeDefinition"):
        path = obj_path(firing_mode)
        if not path:
            continue
        record: dict[str, Any] = {
            "path": path,
            "behaviours": _behaviour_classes_of(firing_mode),
        }
        shots = safe_get(firing_mode, "ShotsPerFire", None)
        if shots is not None:
            record["ShotsPerFire"] = describe_value(shots)
        out.append(record)
    return out


def _collect_explosion_donors(firing_modes: list[dict[str, Any]]) -> dict[str, Any]:
    """The exact `Behavior_Explode` donors a splash firing mode can clone, without a global scan."""
    donors: list[dict[str, Any]] = []
    for record in firing_modes:
        behaviours = record.get("behaviours", {})
        names = [name for group in behaviours.values() for name in group]
        if "Behavior_Explode" not in names:
            continue
        donors.append(
            {
                "firing_mode": record["path"],
                "function_that_carries_it": next(
                    (label for label, group in behaviours.items() if "Behavior_Explode" in group),
                    "",
                ),
            }
        )
    return {"count": len(donors), "donors": donors[:60]}


def _collect_attribute_init_defs() -> list[dict[str, Any]]:
    """Every loaded `AttributeInitializationDefinition`, with its resolved multiplier constant.

    `runtime/loot_pools.py` guesses these weights from the object *name* (`Weight_4_Rare` -> 1.0)
    and then falls back to walking `ValueFormula.Multiplier`. This dump makes the mapping exact.
    """
    out: list[dict[str, Any]] = []
    for init_def in iter_all("AttributeInitializationDefinition"):
        path = obj_path(init_def)
        if not path:
            continue
        value_formula = safe_get(init_def, "ValueFormula", None)
        multiplier = safe_get(value_formula, "Multiplier", None) if value_formula is not None else None
        constant = safe_get(multiplier, "BaseValueConstant", None) if multiplier is not None else None
        out.append(
            {
                "path": path,
                "name": str(safe_get(init_def, "Name", "") or obj_path(init_def).rsplit(".", 1)[-1]),
                "multiplier_base_value_constant": describe_value(constant),
                "value_formula_scale": describe_value(
                    safe_get(value_formula, "Scale", None) if value_formula is not None else None
                ),
            }
        )
    return out


def _pool_entries(pool: Any, limit: int = 40) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        raw = safe_get(pool, "ItemPool", None) or ()
    except Exception:
        raw = ()
    for entry in list(raw)[:limit]:
        item = safe_get(entry, "Item", None)
        probability = safe_get(entry, "Probability", None)
        entry_record: dict[str, Any] = {
            "item": obj_path(item) or str(safe_get(item, "Name", "") or ""),
            "item_class": str(safe_get(safe_get(item, "Class", None), "Name", "") or ""),
        }
        if probability is not None:
            init_def = safe_get(probability, "InitializationDefinition", None)
            entry_record["probability"] = {
                "BaseValueConstant": describe_value(safe_get(probability, "BaseValueConstant", None)),
                "BaseValueScaleConstant": describe_value(
                    safe_get(probability, "BaseValueScaleConstant", None)
                ),
                "BaseValueAttribute": obj_path(safe_get(probability, "BaseValueAttribute", None)),
                "InitializationDefinition": obj_path(init_def),
                "InitializationDefinitionName": str(
                    safe_get(init_def, "Name", "") if init_def is not None else ""
                ),
            }
        quantity = safe_get(entry, "Quantity", None)
        if quantity is not None:
            entry_record["quantity_constant"] = describe_value(
                safe_get(quantity, "BaseValueConstant", None)
            )
        entries.append(entry_record)
    return entries


POOL_KEYWORDS: tuple[str, ...] = ("weapon", "shop", "melee", "pistol", "manufacturer")
MAX_POOLS = 400
MAX_POOL_ENTRIES = 12


def _collect_pools() -> dict[str, Any]:
    """Weapon/shop pools with their entries. Bounded: BL2 loads thousands of unrelated pools."""
    out: list[dict[str, Any]] = []
    skipped = 0
    for pool in iter_all("ItemPoolDefinition"):
        path = obj_path(pool)
        if not path:
            continue
        if not keyword_match(path, POOL_KEYWORDS):
            skipped += 1
            continue
        if len(out) >= MAX_POOLS:
            skipped += 1
            continue
        entries = _pool_entries(pool, limit=MAX_POOL_ENTRIES)
        record: dict[str, Any] = {
            "path": path,
            "entry_count": len(entries),
            "quantity": describe_value(safe_get(safe_get(pool, "Quantity", None), "BaseValueConstant", None)),
            "min_game_stage": obj_path(safe_get(pool, "MinGameStageRequirement", None)),
            "max_game_stage": obj_path(safe_get(pool, "MaxGameStageRequirement", None)),
            "b_auto_ready_items": describe_value(safe_get(pool, "bAutoReadyItems", None)),
            "entries": entries,
        }
        out.append(record)
    return {"pools": out, "skipped_count": skipped, "truncated": skipped > 0}


def _collect_sublibraries() -> list[dict[str, Any]]:
    """The `AssetLibraryManager` registration shape the runtime rebuilds by hand."""
    out: list[dict[str, Any]] = []
    for manager in iter_all("AssetLibraryManager"):
        path = obj_path(manager)
        if not path:
            continue
        record: dict[str, Any] = {"path": path}
        sublibraries = safe_get(manager, "Sublibraries", None)
        record["sublibrary_count"] = _safe_len(sublibraries)
        record["sublibraries"] = [
            obj_path(entry) or describe_value(entry) for entry in list(sublibraries or ())[:20]
        ]
        links = safe_get(manager, "SublibraryLinks", None)
        record["sublibrary_link_count"] = _safe_len(links)
        record["sublibrary_link_properties"] = [
            prop.get("name", "") for prop in (props_of(links[0]) if _safe_len(links) else [])
        ]
        out.append(record)
    return out


def _safe_len(value: Any) -> int:
    try:
        return len(value or ())
    except Exception:
        return 0


def probe_assets() -> dict[str, Any]:
    """Path catalogues per class, keyword-filtered so the file stays readable."""
    result: dict[str, Any] = {}
    for logical, (cls_name, keywords) in CATALOGUE_TARGETS.items():
        objects = iter_all(cls_name)
        paths: list[str] = []
        matched: list[dict[str, Any]] = []
        fields = INTERESTING_FIELDS.get(logical, ())
        for obj in objects:
            path = obj_path(obj)
            if not path:
                continue
            paths.append(path)
            if keywords and not keyword_match(path, keywords):
                continue
            if len(matched) >= 120:
                continue
            record: dict[str, Any] = {"path": path}
            record.update(_describe_fields(obj, fields))
            matched.append(record)
        result[logical] = {
            "class": cls_name,
            "total_loaded": len(paths),
            "truncated": len(paths) >= 20_000,
            "matched": matched,
            "all_paths": sorted(paths)[:2000],
        }
        log(f"assets {cls_name}: {len(paths)} loaded, {len(matched)} matched keywords")
    return result


def probe_behaviours() -> dict[str, Any]:
    """Which firing modes carry `Behavior_Explode`, and every init-def weight constant."""
    firing_modes = _collect_firing_modes()
    return {
        "firing_modes": firing_modes,
        "explosion_donors": _collect_explosion_donors(firing_modes),
        "attribute_init_defs": _collect_attribute_init_defs(),
        "asset_library_managers": _collect_sublibraries(),
    }


def probe_pools() -> dict[str, Any]:
    """Loaded weapon/shop `ItemPoolDefinition`s with their entries, gates and weights."""
    collected = _collect_pools()
    return {
        "pool_count": len(collected["pools"]),
        "skipped_count": collected["skipped_count"],
        "truncated": collected["truncated"],
        "pools": collected["pools"],
    }


def probe_all() -> dict[str, Any]:
    return {
        "catalogues": probe_assets(),
        "behaviours": probe_behaviours(),
        "pools": probe_pools(),
    }
