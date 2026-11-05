"""Schema probes: which engine functions exist, what their parameters are named, and which
properties each class actually has.

Every `hasattr(obj, "X")` and every `getattr(args, "X", default)` in `melee_arsenal.runtime` is a
question this section answers once, permanently. The answers are cheap to read and never change
between BL2 sessions, so they belong in a constants module rather than in defensive branches.
"""
from __future__ import annotations

from typing import Any

from . import common
from .common import (
    describe_value,
    enum_names,
    find_object,
    func_params,
    log,
    obj_path,
    prop_info,
    props_of,
    safe_get,
    struct_shape,
)


# logical name -> (class path, function name). `exists: false` is itself the answer: it means the
# hook in `runtime/__init__.py` can be deleted instead of kept as an "in case" registration.
FUNCTION_TARGETS: dict[str, tuple[str, ...]] = {
    # --- Tick drives: the runtime registers BOTH and de-duplicates per frame "in case" one is dead.
    "tick_viewport_pre": ("Engine.GameViewportClient:Tick", "PRE"),
    "tick_viewport_post": ("Engine.GameViewportClient:Tick", "POST"),
    "tick_player_post": ("WillowGame.WillowPlayerController:PlayerTick", "POST"),
    "tick_willow_viewport_post": ("WillowGame.WillowGameViewportClient:Tick", "POST"),
    # --- Weapon construction: three overlapping hooks exist only because the firing order
    #     `InitializeFromDefinitionData` -> `InitializeInternal` -> `InitConstructPreview` was not known.
    "weapon_init_from_def_pre": ("WillowGame.WillowWeapon:InitializeFromDefinitionData", "PRE"),
    "weapon_init_internal_pre": ("WillowGame.WillowWeapon:InitializeInternal", "PRE"),
    "weapon_init_internal_post": ("WillowGame.WillowWeapon:InitializeInternal", "POST"),
    "weapon_init_construct_preview_post": ("WillowGame.WillowWeapon:InitConstructPreview", "POST"),
    "weapon_choose_name_parts_post": ("WillowGame.WillowWeapon:ChooseRandomNameParts", "POST"),
    "weapon_play_animation_pre": ("WillowGame.WillowWeapon:PlayAnimation", "PRE"),
    # --- Fire path: several candidate entry points, only some of which a melee weapon reaches.
    "pawn_weapon_fired_pre": ("WillowGame.WillowPawn:WeaponFired", "PRE"),
    "weapon_instant_fire_pre": ("WillowGame.WillowWeapon:InstantFire", "PRE"),
    "weapon_firing_mode_fire_pre": ("WillowGame.WillowWeapon:FiringModeDefinitionFire", "PRE"),
    "weapon_play_fire_effects_pre": ("WillowGame.WillowWeapon:PlayFireEffects", "PRE"),
    "weapon_consume_ammo_post": ("WillowGame.WillowWeapon:ConsumeAmmo", "POST"),
    "weapon_shared_fire_post": ("WillowGame.WillowWeapon:SharedFireAmmunition", "POST"),
    "weapon_has_active_ammo_pre": ("WillowGame.WillowWeapon:HasActiveAmmo", "PRE"),
    "weapon_start_reload_pre": ("WillowGame.WillowWeapon:StartReload", "PRE"),
    "light_projectile_add_pre": ("WillowGame.WillowLightProjectileManager:AddProj", "PRE"),
    # --- Zoom: the runtime installs StartZoom AND ClientStartZoom for the same intent.
    "weapon_start_zoom_pre": ("WillowGame.WillowWeapon:StartZoom", "PRE"),
    "weapon_client_start_zoom_pre": ("WillowGame.WillowWeapon:ClientStartZoom", "PRE"),
    "weapon_stop_zoom_pre": ("WillowGame.WillowWeapon:StopZoom", "PRE"),
    "weapon_client_stop_zoom_pre": ("WillowGame.WillowWeapon:ClientStopZoom", "PRE"),
    # --- Item card: four hooks, whose nesting order and icon strings are currently self-learned.
    "card_set_item_card_pre": ("WillowGame.ItemCardGFxObject:SetItemCard", "PRE"),
    "card_set_item_card_post": ("WillowGame.ItemCardGFxObject:SetItemCard", "POST"),
    "card_set_item_card_ex_pre": ("WillowGame.ItemCardGFxObject:SetItemCardEx", "PRE"),
    "card_set_item_card_ex_post": ("WillowGame.ItemCardGFxObject:SetItemCardEx", "POST"),
    "card_set_title_pre": ("WillowGame.ItemCardGFxObject:SetTitle", "PRE"),
    "card_set_top_stat_pre": ("WillowGame.ItemCardGFxObject:SetTopStat", "PRE"),
    "card_set_bottom_stat_pre": ("WillowGame.ItemCardGFxObject:SetBottomStat", "PRE"),
    "card_set_fun_stats_pre": ("WillowGame.ItemCardGFxObject:SetFunStats", "PRE"),
    "card_adjust_height_pre": ("WillowGame.ItemCardGFxObject:AdjustHeight", "PRE"),
    # --- Damage / persistence.
    "ai_take_damage_post": ("WillowGame.WillowAIPawn:TakeDamage", "POST"),
    "player_take_damage_pre": ("WillowGame.WillowPlayerPawn:TakeDamage", "PRE"),
    "display_recent_damage_pre": ("WillowGame.WillowDamageTypeDefinition:DisplayRecentDamageForPlayer", "PRE"),
    "validate_weapon_definition_pre": ("WillowGame.WillowPlayerController:ValidateWeaponDefinition", "PRE"),
    "apply_weapon_save_data_pre": ("WillowGame.WillowPlayerController:ApplyWeaponSaveGameData", "PRE"),
    "save_game_pre": ("WillowGame.WillowSaveGameManager:SaveGame", "PRE"),
    "place_spawned_items_post": ("WillowGame.Behavior_SpawnLootAroundPoint:PlaceSpawnedItems", "POST"),
    # --- Engine entry points the runtime calls reflectively instead of hooking.
    "apply_behavior_to_context": ("WillowGame.Behavior_SpawnLootAroundPoint:ApplyBehaviorToContext", "NATIVE"),
    "arms_play_custom_anim": ("WillowGame.AnimNodeSlot:PlayCustomAnim", "NATIVE"),
    "arms_stop_custom_anim": ("WillowGame.AnimNodeSlot:StopCustomAnim", "NATIVE"),
    "pawn_play_ak_event": ("WillowGame.WillowPawn:PlayAkEvent", "NATIVE"),
    "player_play_camera_anim": ("WillowGame.WillowPlayerController:PlayCameraAnim", "NATIVE"),
    "hud_add_training_text": ("WillowGame.WillowHUDMovieBase:AddTrainingText", "NATIVE"),
    "pc_get_hud_movie": ("WillowGame.WillowPlayerController:GetHUDMovie", "NATIVE"),
}


# Classes whose property lists the runtime currently probes with `hasattr` / `getattr(..., None)`.
CLASS_TARGETS: dict[str, str] = {
    "weapon": "WillowWeapon",
    "weapon_definition_data": "WillowWeaponDefinitionData",
    "weapon_type": "WillowWeaponTypeDefinition",
    "weapon_balance": "WillowWeaponBalanceDefinition",
    "weapon_part": "WeaponPartDefinition",
    "weapon_name_part": "WeaponNamePartDefinition",
    "part_list_collection": "WeaponPartListCollectionDefinition",
    "firing_mode": "FiringModeDefinition",
    "behavior_explode": "Behavior_Explode",
    "behavior_spawn_loot": "Behavior_SpawnLootAroundPoint",
    "item_pool": "ItemPoolDefinition",
    "item_pool_list": "ItemPoolListDefinition",
    "card_presentation": "InventoryCardPresentationDefinition",
    "damage_type": "WillowDamageTypeDefinition",
    "impact": "ImpactDefinition",
    "explosion_collection": "ExplosionCollectionDefinition",
    "player_controller": "WillowPlayerController",
    "player_pawn": "WillowPlayerPawn",
    "ai_pawn": "WillowAIPawn",
    "pawn": "WillowPawn",
    "arms_component": "SkeletalMeshComponent",
    "anim_slot": "AnimNodeSlot",
    "light_projectile": "WillowProjectile",
    "projectile_manager": "WillowLightProjectileManager",
    "asset_library_manager": "AssetLibraryManager",
    "attribute_init_data": "AttributeInitializationDefinition",
    "inventory_manager": "InventoryManager",
    "pickup": "WillowPickup",
    "mission": "MissionDefinition",
    "asset_library": "AssetLibrary",
    "package_asset_sublibrary": "PackageAssetSublibrary",
}


# Structs whose layout the runtime knows only by name. Nested structs found on the classes above
# are added automatically at run time.
STRUCT_TARGETS: tuple[str, ...] = (
    "AttributeInitializationData",
    "ManufacturerCustomPartWeightData",
    "WeightedInventoryBalanceData",
    "NameBasedObjectPath",
)


def _function_paths(class_path: str, func_name: str) -> tuple[str, ...]:
    """UE3 object paths for a function. A UFunction's outer is its class, hence `Class:Function`."""
    return (f"{class_path}:{func_name}", f"{class_path}.{func_name}")


def _resolve_function(hook_name: str) -> Any:
    """`Package.Class:Function` -> live UFunction, loading the package if the SDK allows it."""
    if common.unrealsdk is None:
        return None
    class_path, _, func_name = hook_name.partition(":")
    if not class_path or not func_name:
        return None
    for path in _function_paths(class_path, func_name):
        found = find_object("Function", path)
        if found is not None:
            return found

    package_name = class_path.split(".", 1)[0]
    loader = safe_get(common.unrealsdk, "load_package", None)
    if not callable(loader):
        return None
    try:
        loader(package_name)
    except Exception:
        return None
    for path in _function_paths(class_path, func_name):
        found = find_object("Function", path)
        if found is not None:
            return found
    return None


def probe_functions() -> dict[str, Any]:
    """Does the UFunction exist, and what are its real parameter names and types?"""
    result: dict[str, Any] = {}
    for logical, (hook_name, hook_type) in FUNCTION_TARGETS.items():
        func = _resolve_function(hook_name)
        record: dict[str, Any] = {
            "hook": f"{hook_name} [{hook_type}]",
            "exists": func is not None,
        }
        if func is None:
            record["note"] = "UFunction not found: any hook on this name is dead code"
            result[logical] = record
            continue
        params = func_params(func)
        record["params"] = params
        record["param_names"] = [param.get("name", "") for param in params]
        record["has_return_value"] = any(
            param.get("name") == "ReturnValue" for param in params
        )
        record["out_params"] = [
            param.get("name", "") for param in params if param.get("is_out_param")
        ]
        enum_params = {
            param["name"]: param["enum_values"]
            for param in params
            if param.get("enum_values")
        }
        if enum_params:
            record["enum_params"] = enum_params
        result[logical] = record
        log(f"function {logical}: {len(params)} params" if params else f"function {logical}: no params")
    return result


def probe_classes() -> dict[str, Any]:
    """Property list + class-default values for every class the runtime touches."""
    result: dict[str, Any] = {}
    for logical, cls_name in CLASS_TARGETS.items():
        cls_obj = None
        finder = safe_get(common.unrealsdk, "find_class", None) if common.unrealsdk else None
        if callable(finder):
            try:
                cls_obj = finder(cls_name)
            except Exception:
                cls_obj = None

        record: dict[str, Any] = {"class": cls_name, "found": cls_obj is not None}
        if cls_obj is None:
            result[logical] = record
            continue

        properties = props_of(cls_obj)
        record["property_count"] = len(properties)
        record["properties"] = [prop_info(prop) for prop in properties]
        record["property_names"] = [
            info["name"] for info in record["properties"] if info.get("name")
        ]

        cdo = safe_get(cls_obj, "ClassDefaultObject", None)
        if cdo is not None:
            record["cdo_path"] = obj_path(cdo)
            defaults: dict[str, Any] = {}
            for prop in properties:
                name = str(safe_get(prop, "Name", "") or "")
                if not name or name.startswith("VfTable"):
                    continue
                value = safe_get(cdo, name, None)
                described = describe_value(value, depth=1)
                if described is None:
                    continue
                defaults[name] = described
            record["cdo_defaults"] = defaults
        result[logical] = record
        log(f"class {cls_name}: {len(properties)} properties")
    return result


def probe_enums() -> dict[str, Any]:
    """Every enum reachable from the target classes, in declared order (= integer value)."""
    result: dict[str, Any] = {}
    finder = safe_get(common.unrealsdk, "find_class", None) if common.unrealsdk else None
    if not callable(finder):
        return result
    def _record(enum_obj: Any, used_as: str) -> None:
        key = obj_path(enum_obj) or str(safe_get(enum_obj, "Name", "") or "unknown_enum")
        names = enum_names(enum_obj)
        if names is None:
            return
        existing = result.get(key)
        if existing is None:
            result[key] = {
                "name": str(safe_get(enum_obj, "Name", "") or ""),
                "values": {index: name for index, name in enumerate(names)},
                "used_by": [used_as],
            }
        elif used_as not in existing["used_by"]:
            existing["used_by"].append(used_as)

    # Enum properties declared on a class.
    for cls_name in CLASS_TARGETS.values():
        try:
            cls_obj = finder(cls_name)
        except Exception:
            continue
        for prop in props_of(cls_obj):
            enum_obj = safe_get(prop, "Enum", None)
            if enum_obj is not None:
                _record(enum_obj, f"{cls_name}.{safe_get(prop, 'Name', '')}")

    # Enum parameters of the engine functions we call: this is where `ECompareStat` lives, and it
    # is the only reason `runtime/ui_item_card.py` currently reflect-scans `SetTopStat`.
    for hook_name, _hook_type in FUNCTION_TARGETS.values():
        func = _resolve_function(hook_name)
        if func is None:
            continue
        for prop in props_of(func):
            enum_obj = safe_get(prop, "Enum", None)
            if enum_obj is not None:
                _record(enum_obj, f"{hook_name}.{safe_get(prop, 'Name', '')}")
    return result


def probe_structs() -> dict[str, Any]:
    """Field layout of the structs the runtime builds or reads blind."""
    result: dict[str, Any] = {}
    finder = safe_get(common.unrealsdk, "find_class", None) if common.unrealsdk else None

    for struct_name in STRUCT_TARGETS:
        struct_obj = find_object("ScriptStruct", struct_name)
        if struct_obj is None:
            struct_obj = find_object("Struct", struct_name)
        if struct_obj is None:
            result[struct_name] = {"found": False}
            continue
        result[struct_name] = {
            "found": True,
            "path": obj_path(struct_obj),
            "fields": struct_shape(struct_obj),
        }

    # Auto-discover nested structs from the target classes (AttributeInitializationData lives
    # inside half of BL2's balances and pools, and its shape is currently guessed at).
    if callable(finder):
        for cls_name in CLASS_TARGETS.values():
            try:
                cls_obj = finder(cls_name)
            except Exception:
                continue
            for prop in props_of(cls_obj):
                struct_obj = safe_get(prop, "Struct", None)
                if struct_obj is None:
                    continue
                key = str(safe_get(struct_obj, "Name", "") or "")
                if not key:
                    continue
                existing = result.get(key)
                if existing is not None and existing.get("found"):
                    continue
                result[key] = {
                    "found": True,
                    "path": obj_path(struct_obj),
                    "discovered_on": f"{cls_name}.{safe_get(prop, 'Name', '')}",
                    "fields": struct_shape(struct_obj),
                }
    return result


def probe_library_globals() -> dict[str, Any]:
    """Verify the reflective accessors the runtime uses (`find_class`, `make_struct`, `_find`)."""
    out: dict[str, Any] = {}
    if common.unrealsdk is None:
        return {"unrealsdk": "unavailable"}
    for name in (
        "find_object",
        "find_all",
        "find_class",
        "load_package",
        "construct_object",
        "make_struct",
        "console_exec",
    ):
        member = safe_get(common.unrealsdk, name, None)
        out[name] = callable(member)
    out["unrealsdk_version"] = str(safe_get(common.unrealsdk, "__version__", "") or "")

    # `Class._find(name)` is what `resolver.py` uses for struct-name lookups; confirm it works.
    sample = find_object("Class", "WillowGame.WillowWeapon")
    if sample is not None:
        finder = safe_get(sample, "_find", None)
        if callable(finder):
            try:
                func = finder("InitializeInternal")
                out["willowweapon_find_works"] = func is not None
                out["willowweapon_find_param_names"] = [
                    str(safe_get(prop, "Name", "")) for prop in props_of(func)
                ]
            except Exception as exc:
                out["willowweapon_find_works"] = False
                out["willowweapon_find_error"] = repr(exc)
    return out


def probe_all() -> dict[str, Any]:
    """Every schema section in one dict."""
    return {
        "library": probe_library_globals(),
        "functions": probe_functions(),
        "classes": probe_classes(),
        "enums": probe_enums(),
        "structs": probe_structs(),
    }
