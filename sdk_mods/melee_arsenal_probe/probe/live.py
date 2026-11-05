"""Live probes: read the objects that only exist while a character is in a map.

These are the questions the runtime answers by trying several shapes at run time:

* is `Pawn.Arms` an `AnimNodeSlot` owner with `WeaponAction` / `CustomAnimSlot`, and does the
  `Bladed_Melee` sequence exist at all (the `for seq_name in ("Bladed_Melee", "Melee")` loop)?
* are `Mesh.Materials` entries materials or `{Material, ...}` structs (`_material_of`)?
* does the first-person `SkeletalMesh` expose `RotOrigin` / `Origin` (the procedural arc fallback)?
* which class names do melee victims actually have (the `is_valid_melee_victim` string guessing)?
* what frame cadence do the tick hooks run at (how long is "3 ticks" really)?

Nothing here writes to an object: every access is a read.
"""
from __future__ import annotations

from typing import Any

from . import census
from .common import (
    describe_value,
    iter_all,
    keyword_match,
    log,
    obj_path,
    props_of,
    safe_get,
    same_uobject,
    unrealsdk,
)

MELEE_ANIM_KEYWORDS = ("melee", "bladed", "slash", "swing", "cleave", "pistol_melee")
MATERIAL_DEBUG_ENTRIES = 12
ACTOR_CLASS_TARGETS = (
    "WillowAIPawn",
    "WillowPawn",
    "WillowPlayerPawn",
    "WillowBreakableObject",
    "InteractiveObject",
    "DestructibleActor",
    "KActor",
    "StaticMeshActor",
)


# Locating the player -----------------------------------------------------------------------


def _engine() -> Any:
    if unrealsdk is None:
        return None
    try:
        return unrealsdk.find_class("Engine").ClassDefaultObject.GetEngine()
    except Exception:
        return None


def local_player_controller() -> Any:
    """The local `WillowPlayerController`, or None outside gameplay."""
    engine = _engine()
    if engine is None:
        return None
    try:
        game_players = engine.GamePlayers
        first = game_players[0] if game_players else None
        return getattr(first, "Actor", None) if first is not None else None
    except Exception:
        return None


def _player_summary(pc: Any) -> dict[str, Any]:
    if pc is None:
        return {"found": False}
    pawn = safe_get(pc, "Pawn", None)
    world = safe_get(pc, "WorldInfo", None)
    map_name = ""
    try:
        map_name = str(world.GetStreamingPersistentMapName()) if world is not None else ""
    except Exception:
        map_name = ""
    summary: dict[str, Any] = {
        "found": True,
        "path": obj_path(pc),
        "class": str(safe_get(safe_get(pc, "Class", None), "Name", "") or ""),
        "pawn": obj_path(pawn),
        "pawn_class": str(safe_get(safe_get(pawn, "Class", None), "Name", "") or ""),
        "map": map_name,
        "is_menu_map": map_name.lower() in ("menumap", "loader", "exampleentry"),
    }
    exp_level = safe_get(pawn, "ExpLevel", None)
    if exp_level is not None:
        summary["exp_level"] = describe_value(exp_level)
    replication_info = safe_get(pc, "PlayerReplicationInfo", None)
    if replication_info is not None:
        summary["player_replication_info"] = obj_path(replication_info)
    hud = None
    getter = safe_get(pc, "GetHUDMovie", None)
    if callable(getter):
        try:
            hud = getter()
        except Exception:
            hud = None
    summary["hud_movie_available"] = hud is not None
    return summary


# Weapon + definition data ------------------------------------------------------------------


def _weapon_summary(pawn: Any) -> dict[str, Any]:
    if pawn is None:
        return {"found": False}
    weapon = safe_get(pawn, "Weapon", None)
    if weapon is None:
        return {"found": False}
    definition_data = safe_get(weapon, "DefinitionData", None)
    summary: dict[str, Any] = {
        "found": True,
        "path": obj_path(weapon),
        "class": str(safe_get(safe_get(weapon, "Class", None), "Name", "") or ""),
        "definition_data": obj_path(definition_data),
        "definition_data_class": str(
            safe_get(safe_get(definition_data, "Class", None), "Name", "") or ""
        ),
    }
    for field in (
        "WeaponTypeDefinition",
        "BalanceDefinition",
        "BarrelPartDefinition",
        "GripPartDefinition",
        "ElementalPartDefinition",
        "Accessory1PartDefinition",
        "MaterialPartDefinition",
        "FiringModeDefinition",
    ):
        value = safe_get(definition_data, field, None)
        summary[field] = obj_path(value) if value is not None else ""
    for field in ("InstantHitDamage", "ProjectilesPerShot", "ClipSize", "bCanBeUsed", "bCanMelee"):
        value = safe_get(weapon, field, None)
        if value is not None:
            summary[field] = describe_value(value)
    summary["firing_mode_definition"] = obj_path(safe_get(weapon, "CurrentFiringModeDefinition", None))
    summary["mesh"] = _mesh_summary(safe_get(weapon, "Mesh", None))
    return summary


def _mesh_summary(mesh: Any) -> dict[str, Any]:
    """Material-array shape + first-person transform properties, exactly as the runtime probes them."""
    if mesh is None:
        return {"found": False}
    summary: dict[str, Any] = {
        "found": True,
        "path": obj_path(mesh),
        "class": str(safe_get(safe_get(mesh, "Class", None), "Name", "") or ""),
        "has_rot_origin": safe_get(mesh, "RotOrigin", None) is not None,
        "has_origin": safe_get(mesh, "Origin", None) is not None,
        "rot_origin": describe_value(safe_get(mesh, "RotOrigin", None)),
        "origin": describe_value(safe_get(mesh, "Origin", None)),
        "skeletal_mesh": obj_path(safe_get(mesh, "SkeletalMesh", None)),
        "has_force_update": callable(safe_get(mesh, "ForceUpdate", None)),
    }
    materials = safe_get(mesh, "Materials", None)
    entries: list[dict[str, Any]] = []
    try:
        for entry in list(materials or ())[:MATERIAL_DEBUG_ENTRIES]:
            entry_record: dict[str, Any] = {
                "class": str(safe_get(safe_get(entry, "Class", None), "Name", "") or ""),
                "is_struct": safe_get(entry, "Material", None) is not None,
            }
            inner = safe_get(entry, "Material", None)
            entry_record["path"] = obj_path(inner if inner is not None else entry)
            entries.append(entry_record)
    except Exception:
        pass
    summary["material_count"] = _safe_len(materials)
    summary["materials"] = entries
    return summary


def _safe_len(value: Any) -> int:
    try:
        return len(value or ())
    except Exception:
        return 0


# Arms, anim slots, animation sequences -----------------------------------------------------


def _anim_slot_summary(arms: Any) -> dict[str, Any]:
    """Every `AnimNodeSlot` owned by the arms mesh, with the node names the runtime looks up."""
    if arms is None:
        return {"found": False}
    slots: list[dict[str, Any]] = []
    for slot in iter_all("AnimNodeSlot"):
        if not same_uobject(safe_get(slot, "SkelComponent", None), arms):
            continue
        slots.append(
            {
                "path": obj_path(slot),
                "node_name": str(safe_get(slot, "NodeName", "") or ""),
                "has_play_custom_anim": callable(safe_get(slot, "PlayCustomAnim", None)),
                "has_stop_custom_anim": callable(safe_get(slot, "StopCustomAnim", None)),
            }
        )
    node_names = [slot["node_name"] for slot in slots]
    return {
        "found": bool(slots),
        "slots": slots,
        "node_names": node_names,
        "has_weapon_action": "WeaponAction" in node_names,
        "has_custom_anim_slot": "CustomAnimSlot" in node_names,
        "has_weapon_recoil": "WeaponRecoil" in node_names,
    }


def _sequence_names(arms: Any, limit: int = 4000) -> list[str]:
    """Animation sequence names reachable from the arms mesh, matched against melee keywords."""
    names: list[str] = []
    seen: set[str] = set()
    for container_field in ("AnimSets", "Animations"):
        container = safe_get(arms, container_field, None)
        try:
            items = list(container or ())
        except Exception:
            continue
        for item in items:
            sequences = safe_get(item, "Sequences", None)
            try:
                raw_sequences = list(sequences or ())
            except Exception:
                continue
            for sequence in raw_sequences[:limit]:
                name = str(safe_get(sequence, "SequenceName", "") or safe_get(sequence, "Name", "") or "")
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


def _arms_summary(pawn: Any) -> dict[str, Any]:
    if pawn is None:
        return {"found": False}
    arms = safe_get(pawn, "Arms", None)
    if arms is None:
        return {"found": False}
    skeletal_mesh = safe_get(arms, "SkeletalMesh", None)
    summary: dict[str, Any] = {
        "found": True,
        "path": obj_path(arms),
        "class": str(safe_get(safe_get(arms, "Class", None), "Name", "") or ""),
        "skeletal_mesh": _mesh_summary(arms),
        "skeletal_mesh_asset": obj_path(skeletal_mesh),
        "has_force_update": callable(safe_get(arms, "ForceUpdate", None)),
        "has_play_ak_event": callable(safe_get(arms, "PlayAkEvent", None)),
        "anim_slots": _anim_slot_summary(arms),
    }
    sequences = _sequence_names(arms)
    summary["sequence_count"] = len(sequences)
    summary["melee_sequences"] = [
        name for name in sequences if keyword_match(name, MELEE_ANIM_KEYWORDS)
    ][:80]
    summary["bladed_melee_available"] = any(
        name.lower().startswith("bladed_melee") for name in sequences
    )
    summary["melee_fallback_available"] = any(name.lower() == "melee" for name in sequences)
    return summary


# World actors: replace class-name string guessing with observed classes ---------------------


def _world_actors() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cls_name in ACTOR_CLASS_TARGETS:
        paths: list[str] = []
        classes: set[str] = set()
        for actor in iter_all(cls_name)[:120]:
            path = obj_path(actor)
            if not path:
                continue
            paths.append(path)
            classes.add(str(safe_get(safe_get(actor, "Class", None), "Name", "") or ""))
        out[cls_name] = {
            "count": len(paths),
            "example_paths": paths[:12],
            "observed_classes": sorted(name for name in classes if name),
        }
    return out


# Tediore throw VFX donor chain -------------------------------------------------------------


def _spawn_projectile_behaviour_providers() -> dict[str, Any]:
    """Where BL2 keeps `Behavior_SpawnProjectile` nodes: the donor the throw VFX imitates."""
    providers: list[dict[str, Any]] = []
    for provider in iter_all("BehaviorProviderDefinition"):
        path = obj_path(provider)
        if not path:
            continue
        behaviours: list[str] = []
        for field in ("BehaviorSequences", "BehaviorList"):
            container = safe_get(provider, field, None)
            try:
                items = list(container or ())
            except Exception:
                continue
            for item in items:
                name = str(safe_get(safe_get(item, "Class", None), "Name", "") or "")
                if name:
                    behaviours.append(name)
        if not any("SpawnProjectile" in name for name in behaviours):
            continue
        providers.append({"path": path, "behaviours": behaviours[:24]})
        if len(providers) >= 40:
            break
    return {"count": len(providers), "providers": providers}


# Names the runtime wraps in `hasattr(obj, ...)` / `getattr(obj, name, default)`. A class's
# declared property list is not the same thing as "readable on this object": inherited members
# (`Arms` is declared on `WillowPlayerPawn`, not `WillowPawn`) and native ones (`RotOrigin`,
# `PlayAkEvent`) never show up there even though reading them works. So each name is tested
# directly against the live object the runtime would touch.
GUARDED_NAMES: dict[str, tuple[str, ...]] = {
    "pawn": ("Arms", "Weapon", "Mesh", "PendingWeapon", "PlayAkEvent", "Location"),
    "arms_component": (
        "RotOrigin", "Origin", "Rotation", "Translation", "ForceUpdate", "PlayAkEvent",
        "Materials", "SkeletalMesh", "AnimSets",
    ),
    "weapon": (
        "Mesh", "DefinitionData", "WeaponTypeDefinition", "CurrentFiringModeDefinition",
        "PlayAkEvent", "bCanBeUsed", "InstantHitDamage", "ProjectilesPerShot", "ClipSize",
    ),
    "weapon_type": (
        "Presentation", "UIMeshRotation", "bUIMeshRotationNeedsTransformFixup", "FocusRadius",
        "MinimumVerticalPercentage", "MinimumHorizontalPercentage", "ThirdPersonMeshScale",
        "FireShake", "WeaponMesh", "LeftHandSocketName", "RightHandSocketName", "WeaponCardType",
    ),
    "weapon_balance": (
        "MinGameStageIndex", "MaxGameStageIndex", "GameStageRequirement", "Rarity",
        "AttributeStartingValues", "CustomPresentations",
    ),
    "weapon_part": (
        "WeaponAttributeEffects", "ExternalAttributeEffects", "AttributeSlotEffects",
        "AttributeSlotUpgrades", "ZoomFirstPersonHandsIdleAnimations", "CustomFiringModeDefinition",
        "Material", "GestaltModeSkeletalMeshName", "AdditionalGestaltModeSkeletalMeshNames",
        "ManufacturerDefinitionForScopeMovie", "CustomDamageTypeDefinition",
    ),
    "weapon_name_part": ("PartName", "bNameIsUnique", "CustomPresentations", "CustomFiringModeDefinition"),
}

_MISSING = object()


def _class_name(obj: Any) -> str:
    return str(safe_get(safe_get(obj, "Class", None), "Name", "") or "")


def runtime_objects() -> dict[str, Any]:
    """The live definitions the runtime reaches for, keyed like `GUARDED_NAMES`."""
    pc = local_player_controller()
    pawn = safe_get(pc, "Pawn", None) if pc is not None else None
    weapon = safe_get(pawn, "Weapon", None) if pawn is not None else None
    definition_data = safe_get(weapon, "DefinitionData", None) if weapon is not None else None
    weapon_type = safe_get(definition_data, "WeaponTypeDefinition", None) if definition_data is not None else None
    return {
        "player_controller": pc,
        "pawn": pawn,
        "arms_component": safe_get(pawn, "Arms", None) if pawn is not None else None,
        "weapon": weapon,
        "weapon_type": weapon_type,
        "weapon_balance": safe_get(definition_data, "BalanceDefinition", None) if definition_data is not None else None,
        "weapon_part": safe_get(definition_data, "BarrelPartDefinition", None) if definition_data is not None else None,
        "weapon_name_part": (
            safe_get(definition_data, "PrefixPartDefinition", None)
            or safe_get(definition_data, "TitlePartDefinition", None)
        ) if definition_data is not None else None,
        "card_presentation": safe_get(weapon_type, "Presentation", None) if weapon_type is not None else None,
    }


_SNAPSHOT: dict[str, Any] | None = None
_SNAPSHOT_WEAPON = ""
_LATCH_TICKS = 0


def _class_properties(obj: Any) -> dict[str, Any]:
    """Property names declared on the object's own class, read while the package is still loaded."""
    cls = safe_get(obj, "Class", None)
    if cls is None:
        return {}
    names = [str(safe_get(prop, "Name", "") or "") for prop in props_of(cls)]
    names = [name for name in names if name]
    return {
        "class": str(safe_get(cls, "Name", "") or ""),
        "property_count": len(names),
        "property_names": names,
    }


def runtime_classes() -> dict[str, Any]:
    """Property lists for the definitions the runtime patches, taken off the live objects.

    Doing this here (instead of in `probe_schema`) is deliberate: `probe_dump` is often run from the
    main menu, where loot packages are unloaded and `find_class(name)` finds nothing. The class
    object held by a live definition is readable whenever a map is, whatever the name is.
    """
    out: dict[str, Any] = {}
    for logical, obj in runtime_objects().items():
        if obj is None:
            continue
        record = _class_properties(obj)
        if record:
            out[logical] = record
    return out


def discovered_class_names() -> dict[str, str]:
    """Real class names, read off live objects instead of guessed.

    Guessing (`WillowWeaponTypeDefinition`, `WillowWeaponBalanceDefinition`) is why the property
    dumps for the two files that need them most (`definitions/weapon_types.py`,
    `definitions/balances.py`) came back empty: BL2 names those classes differently. Falls back to
    the latched map-time snapshot so a menu-time dump still names them.
    """
    out: dict[str, str] = {}
    for logical, obj in runtime_objects().items():
        name = _class_name(obj) if obj is not None else ""
        if name:
            out[logical] = name
    if out:
        return out
    snapshot = _SNAPSHOT or {}
    return {logical: rec.get("class", "") for logical, rec in (snapshot.get("classes") or {}).items()}


def capture_runtime_snapshot() -> None:
    """Remember the live measurements while a map is loaded (called once per frame tick).

    The first dump of this probe was taken from the main menu, where there is no pawn: `guards` and
    `discovered_classes` came back empty and the class dumps for the remaining cleanup were lost.
    This latches the first usable snapshot and refreshes it whenever the held weapon changes.
    """
    global _SNAPSHOT, _SNAPSHOT_WEAPON, _LATCH_TICKS
    _LATCH_TICKS += 1
    if _LATCH_TICKS % 30 != 0:  # three times a second is plenty for something that rarely changes
        return
    objects = runtime_objects()
    pawn = objects.get("pawn")
    weapon = objects.get("weapon")
    if pawn is None or weapon is None:
        return
    weapon_path = obj_path(weapon)
    if _SNAPSHOT is not None and weapon_path and weapon_path == _SNAPSHOT_WEAPON:
        return
    pc = objects.get("player_controller")
    _SNAPSHOT = {
        "weapon": weapon_path,
        "player": _player_summary(pc),
        "held_weapon": _weapon_summary(pawn),
        "arms": _arms_summary(pawn),
        "guards": guard_report(),
        "classes": runtime_classes(),
    }
    _SNAPSHOT_WEAPON = weapon_path
    log(f"latched map snapshot: weapon={weapon_path or '?'} classes={sorted(_SNAPSHOT['classes'])}")


def map_snapshot() -> dict[str, Any] | None:
    """The last snapshot captured during gameplay, if any."""
    return _SNAPSHOT


def guard_report() -> dict[str, Any]:
    """`hasattr` per guarded name, answered on the live object rather than the class."""
    objects = runtime_objects()
    out: dict[str, Any] = {}
    for logical, names in GUARDED_NAMES.items():
        obj = objects.get(logical)
        if obj is None:
            out[logical] = {"found": False}
            continue
        present: list[str] = []
        missing: list[str] = []
        for name in names:
            try:
                value = getattr(obj, name, _MISSING)
            except Exception:
                value = _MISSING
            (missing if value is _MISSING else present).append(name)
        out[logical] = {
            "found": True,
            "class": _class_name(obj),
            "path": obj_path(obj),
            "present": present,
            "missing": missing,
        }
    return out


# Entry point -------------------------------------------------------------------------------


def probe_live() -> dict[str, Any]:
    """Snapshot of everything that only exists while standing in a map."""
    pc = local_player_controller()
    pawn = safe_get(pc, "Pawn", None) if pc is not None else None
    live: dict[str, Any] = {
        "player": _player_summary(pc),
        "held_weapon": _weapon_summary(pawn),
        "arms": _arms_summary(pawn),
        "frame_cadence": census.frame_stats(),
        "world_actors": _world_actors(),
        "spawn_projectile_providers": _spawn_projectile_behaviour_providers(),
        "item_card_observed": census.snapshot().get("item_card", {}),
        "guards": guard_report(),
        "discovered_classes": discovered_class_names(),
    }
    snapshot = map_snapshot()
    if snapshot is not None:
        live["map_snapshot"] = snapshot
        if not live["player"].get("pawn"):
            live["player"] = snapshot["player"]
            live["held_weapon"] = snapshot["held_weapon"]
            live["arms"] = snapshot["arms"]
            live["guards"] = snapshot["guards"]
            live["note"] = (
                "Dumped outside a map. `map_snapshot` is what was measured while playing; `player`, "
                "`held_weapon`, `arms` and `guards` above are copied from it."
            )
    elif not live["player"].get("pawn"):
        live["note"] = "Not in a map (or no local player yet): load a character and run again."
    if not live["player"].get("found") and "note" not in live:
        live["note"] = "Not in a map (or no local player yet): load a character and run again."
    log(f"live snapshot: player={live['player'].get('found')} arms={live['arms'].get('found')}")
    return live


def probe_pawn_only() -> dict[str, Any]:
    """Cheap subset used while playing, e.g. from a keybind or a repeated command."""
    pc = local_player_controller()
    pawn = safe_get(pc, "Pawn", None) if pc is not None else None
    return {
        "player": _player_summary(pc),
        "arms": _arms_summary(pawn),
        "held_weapon": _weapon_summary(pawn),
    }
