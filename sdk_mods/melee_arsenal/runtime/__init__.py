"""SDK Runtime entry point for Borderlands 2 Melee Arsenal."""
from __future__ import annotations

import time
from typing import Any

from mods_base import (  # type: ignore[import-not-found]
    BoolOption,
    ButtonOption,
    Game,
    SliderOption,
    build_mod,
    command,
    hook,
)
from unrealsdk.hooks import Block, Type  # type: ignore[import-not-found]

from .combat import (
    _update_first_person_slash_motion,
    capture_first_person_rest_transform,
    clear_all_pending_crit_feedback,
    ensure_combat_resources_cached as ensure_live_combat_cached,
    on_ai_take_damage_post,
    on_display_recent_damage_pre,
    on_light_projectile_add_proj_pre,
    on_melee_weapon_start_zoom_pre,
    on_pawn_weapon_fired_pre,
    on_player_take_damage_pre,
    on_weapon_consume_ammo_post,
    on_weapon_firing_mode_fire_pre,
    on_weapon_instant_fire_pre,
    on_weapon_play_fire_effects_pre,
    on_weapon_reload_pre,
    on_weapon_shared_fire_post,
    restore_all_arms_rest_transforms,
)
from .commands import (
    export_asset_registry_command,
    dump_held_melee_weapon,
    spawn_all_five_archetypes,
    spawn_single_command,
    tune_ui_preview_command,
)
from .definitions import ensure_definitions_built
from .definitions.resources import ensure_combat_resources_cached
from .log import clear_debug_once_cache, log_info
from .loot_pools import inspect_loot_pools, refresh_vending_machines, remove_melee_loot_entries, sync_loot_pools
from .persistence_bridge import (
    acknowledge_restored_inventory,
    clear_pending_restores,
    on_apply_weapon_save_data_pre,
    on_save_game_pre,
    on_validate_weapon_definition_pre,
    restore_player_melee_inventory,
    snapshot_player_melee_inventory,
)
from .secondary.guard import (
    on_melee_weapon_client_stop_zoom_pre,
    on_melee_weapon_stop_zoom_pre,
)
from .secondary.lifecycle import (
    describe_secondary_state,
    reset_secondary_state,
    tick_secondary_attacks,
)
from .resolver import (
    cancel_pending_spawns,
    get_local_player_controller,
    is_melee_weapon,
    on_choose_random_name_parts_post,
    on_init_construct_preview_post,
    on_place_spawned_items_post,
    on_play_weapon_animation_pre,
    on_weapon_initialize_from_def_pre,
    on_weapon_initialize_internal_post,
    on_weapon_initialize_internal_pre,
    tick_spawner_restore,
)
from .state import STATE
from .object_identity import cache_key
from .ui_item_card import (
    clear_active_card_overrides,
    on_set_fun_stats_pre,
    on_set_item_card_ex_post,
    on_set_item_card_ex_pre,
    on_set_title_pre,
    on_set_top_stat_pre,
)


_tick_counter: int = 0
_map_restore_ready_at: float = 0.0
_last_restored_pawn_key: str = ""
_last_frame_tick_at: float = 0.0


def _notify_hud(title: str, message: str) -> None:
    pc = get_local_player_controller()
    if pc is None:
        return
    try:
        hud = pc.GetHUDMovie()
        if hud is not None:
            hud.AddTrainingText(message, title, 4.5, (), "", False, 0, pc.PlayerReplicationInfo, True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Mod Menu Options
# ---------------------------------------------------------------------------

def _on_world_drops_changed(_opt: Any, new_val: bool) -> None:
    STATE.world_drops_enabled = bool(new_val)
    if STATE.initialized:
        sync_loot_pools()


def _on_drop_weight_changed(_opt: Any, new_val: float) -> None:
    STATE.world_drop_weight = float(new_val)
    if STATE.initialized:
        sync_loot_pools()


def _on_slash_scale_changed(_opt: Any, new_val: float) -> None:
    STATE.slash_motion_scale = float(new_val) / 100.0


def _on_camera_shake_changed(_opt: Any, new_val: float) -> None:
    STATE.camera_shake_scale = max(0.0, float(new_val)) / 100.0


def _on_debug_log_changed(_opt: Any, new_val: bool) -> None:
    STATE.debug_attack_log = bool(new_val)


def _on_guard_pose_changed(_opt: Any, new_val: float) -> None:
    STATE.guard_pose_scale = max(-1.0, min(1.0, float(new_val) / 100.0))


def _on_damage_reduction_changed(_opt: Any, new_val: bool) -> None:
    STATE.damage_reduction_enabled = bool(new_val)


def _on_guard_damage_reduction_changed(_opt: Any, new_val: bool) -> None:
    STATE.guard_damage_reduction_enabled = bool(new_val)


def _on_spawn_all_button(_opt: Any) -> None:
    count = spawn_all_five_archetypes("purple")
    if count > 0:
        _notify_hud(
            "Melee Arsenal",
            f"Dropped all {count} Melee Weapon archetypes right in front of you!",
        )


opt_world_drops = BoolOption(
    "Enable World Drops",
    True,
    description="Inject Melee Weapons into world-drop, enemy, chest, and vendor weapon pools.",
    on_change_anytime=_on_world_drops_changed,
)

opt_drop_weight = SliderOption(
    "World Drop Class Weight",
    100,
    10,
    500,
    10,
    True,
    description=(
        "Relative weapon class weight across enemy drops, chests, containers, and vendors "
        "(100 = equal to Pistols/SMGs/ARs/Shotguns at ~16% of weapon drops; up to 500 for heavy farming)."
    ),
    on_change_anytime=_on_drop_weight_changed,
)

opt_slash_scale = SliderOption(
    "1st-Person Slash Motion (%)",
    100,
    0,
    150,
    10,
    True,
    description="Intensity of the first-person slash/cleave motion when attacking with Primary Fire.",
    on_change_anytime=_on_slash_scale_changed,
)

opt_camera_shake = SliderOption(
    "Swing Camera Shake (%)",
    50,
    0,
    150,
    10,
    True,
    description=(
        "Strength of the slow, eased camera bump layered on each blade swing (0% disables it "
        "entirely). 50% is the default camera-shake strength; increase it for a heavier impact."
    ),
    on_change_anytime=_on_camera_shake_changed,
)

opt_guard_pose = SliderOption(
    "Guard Blade Tilt (%)",
    100,
    -100,
    100,
    10,
    True,
    description=(
        "How far raising the guard swings the blade in toward the middle of the screen. "
        "100% lifts the tip and pulls it across the crosshair; negative values lean it the "
        "other way instead (use this if the brace leans away from the centre for you). "
        "0% leaves the hands almost idle."
    ),
    on_change_anytime=_on_guard_pose_changed,
)

opt_debug_log = BoolOption(
    "Log Swing Damage Breakdown",
    False,
    description="Log per-swing damage and combat checkpoints to melee_arsenal.log for crash diagnosis.",
    on_change_anytime=_on_debug_log_changed,
)

opt_damage_reduction = BoolOption(
    "Melee Weapon Damage Reduction",
    True,
    description=(
        "Passive perk: while a Melee Weapon is equipped, incoming damage is reduced by that weapon's rolled "
        "Damage Reduction (30% at Lv 1, climbing with level/parts to a 70% cap at Lv 80 + OP10; "
        "unique rarities go higher). Shown on its item card. Guard mitigation has its own toggle."
    ),
    on_change_anytime=_on_damage_reduction_changed,
)

opt_guard_damage_reduction = BoolOption(
    "Guard Damage Reduction",
    True,
    description=(
        "Active perk: Hyperion/Jakobs RMB guard applies the blade's Damage Reduction a second time "
        "and allows perfect parries. Disable to keep the guard pose/feedback without mitigation."
    ),
    on_change_anytime=_on_guard_damage_reduction_changed,
)

btn_spawn_all = ButtonOption(
    "Spawn All 5 Melee Archetypes",
    description="Drops Hyperion Katana, Bandit Machete, Jakobs Sabre, Maliwan Phaseblade, and Torgue Blast-Cleaver in front of you.",
    on_press=_on_spawn_all_button,
)


# ---------------------------------------------------------------------------
# Engine Hooks
# ---------------------------------------------------------------------------

def _run_frame_tick() -> None:
    """One frame of runtime work, driven by whichever engine tick actually fires.

    `Engine.GameViewportClient:Tick` is not a UFunction on every BL2 build (it is a native
    method), so hooking it alone can silently never run — which froze thrown blades in mid-air
    and left the guard pose stuck. `WillowPlayerController:PlayerTick` is the engine function
    mods reliably hook, so the same body is driven from both, de-duplicated per frame.
    """
    global _tick_counter, _map_restore_ready_at, _last_restored_pawn_key, _last_frame_tick_at
    now = time.monotonic()
    if now - _last_frame_tick_at < 0.002:
        return
    _last_frame_tick_at = now
    _tick_counter += 1

    # 1. Drain staggered multi-weapon grants & restore reward mission template
    tick_spawner_restore()
    acknowledge_restored_inventory()

    # 2a. Capture the idle first-person arms transform once, before any secondary-attack pose can
    #     run: a pose must never be able to bake itself in as the "rest" transform.
    if _tick_counter % 30 == 0:
        try:
            capture_first_person_rest_transform()
        except Exception:
            pass

    # 2b. Advance thrown blades (Bandit/Torgue arcs), keep the held guard honest, and self-heal
    #     any first-person pose whose state has ended. RMB-release hooks are registered with
    #     `ALL_HOOKS` at load.
    tick_secondary_attacks()

    # 3. Drive smooth 1st-person slash motion ONLY during active ~150ms swing window
    if STATE.swing_motion.active:
        pc = get_local_player_controller()
        if pc is not None:
            _update_first_person_slash_motion(pc)

    # 4. Build definitions & inject loot pools once base packages finish loading
    if not STATE.initialized and _tick_counter >= 90 and (_tick_counter % 60 == 0):
        if ensure_definitions_built():
            sync_loot_pools()

    # 5. Check once per second for new pawn spawn in a gameplay map to restore sidecar inventory
    if STATE.initialized and (_tick_counter % 60 == 0):
        pc = get_local_player_controller()
        pawn = getattr(pc, "Pawn", None) if pc is not None else None
        if pawn is None or getattr(pc, "WorldInfo", None) is None:
            return
        try:
            map_name = str(pc.WorldInfo.GetStreamingPersistentMapName()).lower()
        except Exception:
            return
        if map_name in ("menumap", "loader", "exampleentry"):
            return

        # Key the pawn by its stable UObject path, not Python wrapper identity. A new pawn/map
        # cancels pending throws and guards before sidecar restoration; wrapper churn on the same pawn
        # must not re-run inventory grants.
        pawn_key = cache_key(pawn)
        if not pawn_key:
            return
        if pawn_key != _last_restored_pawn_key:
            if _map_restore_ready_at == 0.0:
                reset_secondary_state()
                restore_all_arms_rest_transforms()
                sync_loot_pools()
                _map_restore_ready_at = now + 2.0
                return
            if now >= _map_restore_ready_at:
                _last_restored_pawn_key = pawn_key
                _map_restore_ready_at = 0.0
                ensure_combat_resources_cached()
                ensure_live_combat_cached()
                # Always re-sync on map load so SparkLevelPatchEntry / BLCMM / UCP hotfixes never wipe our pools
                sync_loot_pools()
                restore_player_melee_inventory(pc=pc)


@hook("Engine.GameViewportClient:Tick", Type.POST)
def hook_viewport_tick(obj: Any, args: Any, ret: Any, func: Any) -> None:
    _run_frame_tick()


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def hook_player_tick(obj: Any, args: Any, ret: Any, func: Any) -> None:
    _run_frame_tick()


@hook("WillowGame.WillowWeapon:InitializeFromDefinitionData", Type.PRE)
def hook_weapon_init_from_def(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_initialize_from_def_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:InitializeInternal", Type.PRE)
def hook_weapon_init_internal_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_initialize_internal_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:InitializeInternal", Type.POST)
def hook_weapon_init_internal_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_initialize_internal_post(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:ChooseRandomNameParts", Type.POST)
def hook_weapon_choose_names(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_choose_random_name_parts_post(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:PlayAnimation", Type.PRE)
def hook_weapon_play_anim(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_play_weapon_animation_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:InitConstructPreview", Type.POST)
def hook_weapon_init_preview(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_init_construct_preview_post(obj, args, ret, func)


@hook("WillowGame.WillowPawn:WeaponFired", Type.PRE)
def hook_pawn_weapon_fired(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_pawn_weapon_fired_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:ConsumeAmmo", Type.POST)
def hook_weapon_consume_ammo(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_consume_ammo_post(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:InstantFire", Type.PRE)
def hook_weapon_instant_fire_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_instant_fire_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:FiringModeDefinitionFire", Type.PRE)
def hook_weapon_firing_mode_fire_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_firing_mode_fire_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:PlayFireEffects", Type.PRE)
def hook_weapon_play_fire_effects_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_play_fire_effects_pre(obj, args, ret, func)


@hook("WillowGame.WillowLightProjectileManager:AddProj", Type.PRE)
def hook_light_proj_add_proj(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_light_projectile_add_proj_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:SharedFireAmmunition", Type.POST)
def hook_weapon_shared_fire_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_shared_fire_post(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:HasActiveAmmo", Type.PRE)
def hook_weapon_has_active_ammo(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    if is_melee_weapon(obj):
        return Block, True
    return None


@hook("WillowGame.WillowWeapon:StartReload", Type.PRE)
def hook_weapon_start_reload(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_weapon_reload_pre(obj, args, ret, func)


@hook("WillowGame.WillowAIPawn:TakeDamage", Type.POST)
def hook_ai_take_damage(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_ai_take_damage_post(obj, args, ret, func)


@hook("WillowGame.WillowPlayerPawn:TakeDamage", Type.PRE)
def hook_player_take_damage(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_player_take_damage_pre(obj, args, ret, func)


@hook("WillowGame.WillowDamageTypeDefinition:DisplayRecentDamageForPlayer", Type.PRE)
def hook_display_recent_damage(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_display_recent_damage_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:StartZoom", Type.PRE)
def hook_melee_start_zoom(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_melee_weapon_start_zoom_pre(obj, args, ret, func)


@hook("WillowGame.WillowWeapon:ClientStartZoom", Type.PRE)
def hook_melee_client_start_zoom(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    # Belt-and-braces guard in case a firing path reaches the client-side FOV change directly
    return on_melee_weapon_start_zoom_pre(obj, args, ret, func)


@hook("WillowGame.ItemCardGFxObject:SetItemCard", Type.PRE)
def hook_card_set_item_card_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_set_item_card_ex_pre(obj, args, ret, func)


@hook("WillowGame.ItemCardGFxObject:SetItemCard", Type.POST)
def hook_card_set_item_card_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_set_item_card_ex_post(obj, args, ret, func)


@hook("WillowGame.ItemCardGFxObject:SetItemCardEx", Type.PRE)
def hook_card_set_item_card_ex_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_set_item_card_ex_pre(obj, args, ret, func)


@hook("WillowGame.ItemCardGFxObject:SetTitle", Type.PRE)
def hook_card_set_title_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_set_title_pre(obj, args, ret, func)


@hook("WillowGame.ItemCardGFxObject:SetTopStat", Type.PRE)
def hook_card_set_top_stat_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_set_top_stat_pre(obj, args, ret, func)


@hook("WillowGame.ItemCardGFxObject:SetFunStats", Type.PRE)
def hook_card_set_fun_stats_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_set_fun_stats_pre(obj, args, ret, func)


@hook("WillowGame.ItemCardGFxObject:SetItemCardEx", Type.POST)
def hook_card_set_item_card_ex_post(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_set_item_card_ex_post(obj, args, ret, func)


@hook("WillowGame.WillowPlayerController:ValidateWeaponDefinition", Type.PRE)
def hook_validate_weapon_def(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_validate_weapon_definition_pre(obj, args, ret, func)


@hook("WillowGame.WillowPlayerController:ApplyWeaponSaveGameData", Type.PRE)
def hook_apply_weapon_save_data(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_apply_weapon_save_data_pre(obj, args, ret, func)


@hook("WillowGame.Behavior_SpawnLootAroundPoint:PlaceSpawnedItems", Type.POST)
def hook_place_spawned_items(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_place_spawned_items_post(obj, args, ret, func)


@hook("WillowGame.WillowSaveGameManager:SaveGame", Type.PRE)
def hook_save_game_pre(obj: Any, args: Any, ret: Any, func: Any) -> Any:
    return on_save_game_pre(obj, args, ret, func)


# ---------------------------------------------------------------------------
# Console Commands
# ---------------------------------------------------------------------------

@command(
    "melee_spawn",
    description="Drop a Melee Weapon in front of you: melee_spawn [maker] [rarity] [element] [accessory]",
)
def cmd_melee_spawn(args: Any) -> None:
    count = spawn_single_command(
        maker_arg=str(getattr(args, "maker", "") or ""),
        rarity_arg=str(getattr(args, "rarity", "purple") or "purple"),
        element_arg=str(getattr(args, "element", "") or ""),
        accessory_arg=str(getattr(args, "accessory", "") or ""),
        delivery="drop",
    )
    if count > 0:
        _notify_hud("Melee Arsenal", "Dropped Melee Weapon right in front of you!")


cmd_melee_spawn.add_argument("maker", nargs="?", default="", help="Manufacturer or blade name")
cmd_melee_spawn.add_argument("rarity", nargs="?", default="purple", help="Rarity (white/green/blue/purple)")
cmd_melee_spawn.add_argument("element", nargs="?", default="", help="Optional element override")
cmd_melee_spawn.add_argument(
    "accessory",
    nargs="?",
    default="",
    help="Optional accessory override (serrated|pommel|fuller|razor|guard|spine|coil|none)",
)


@command(
    "melee_spawn_all",
    description="Drop all 5 Manufacturer Melee Weapons right in front of you: melee_spawn_all [white|green|blue|purple]",
)
def cmd_melee_spawn_all(args: Any) -> None:
    rarity = str(getattr(args, "rarity", "purple") or "purple")
    count = spawn_all_five_archetypes(rarity, delivery="drop")
    if count > 0:
        _notify_hud("Melee Arsenal", f"Dropped {count}/5 {rarity.upper()} Melee Archetypes in front of you!")


cmd_melee_spawn_all.add_argument("rarity", nargs="?", default="purple", help="Rarity (white/green/blue/purple)")


@command(
    "melee_dump_held",
    description="Print held Melee Weapon parts, card stats, damage reduction, and live player melee/Roid scaling breakdown.",
)
def cmd_melee_dump_held(args: Any) -> None:
    info = dump_held_melee_weapon()
    if info is not None:
        live = info["live_player_bonuses"]
        _notify_hud(
            info["name"],
            f"Card {info['card_stats']['damage']} | DR {info['card_stats']['damage_reduction_pct']}% "
            f"| Melee x{live['melee_damage_scale']} | Roid +{live['roid_flat_damage']:.0f} "
            f"-> Hit {live['effective_body_hit']}",
        )


@command(
    "melee_secondary",
    description="Inspect live secondary-attack state: held throw arc / guard window & cooldown.",
)
def cmd_melee_secondary(_args: Any) -> None:
    info = describe_secondary_state()
    log_info(f"melee_secondary: {info}")
    _notify_hud("Melee Secondary", str(info.get("detail", info.get("mode", "none"))))


@command(
    "melee_save_now",
    description="Manually snapshot carried Melee Arsenal weapons to sidecar JSON.",
)
def cmd_melee_save_now(args: Any) -> None:
    n = snapshot_player_melee_inventory()
    _notify_hud("Melee Arsenal", f"Saved {n} carried Melee Weapon(s) to sidecar.")


@command(
    "melee_preview",
    description="Inspect or tune Inventory 3D preview: melee_preview [roll_deg] [scale] [focus_radius] [pitch_deg] [yaw_deg] [bounds_z]",
)
def cmd_melee_preview(args: Any) -> None:
    cfg = tune_ui_preview_command(
        roll_deg=str(getattr(args, "roll_deg", "") or ""),
        scale=str(getattr(args, "scale", "") or ""),
        focus_radius=str(getattr(args, "focus_radius", "") or ""),
        pitch_deg=str(getattr(args, "pitch_deg", "") or ""),
        yaw_deg=str(getattr(args, "yaw_deg", "") or ""),
        bounds_z_scale=str(getattr(args, "bounds_z", "") or ""),
    )
    _notify_hud(
        "Melee Preview",
        f"Roll={cfg['roll_deg']} Yaw={cfg['yaw_deg']} | Scale={cfg['scale']}x | BoundsZ={cfg['bounds_z_scale']}",
    )


cmd_melee_preview.add_argument("roll_deg", nargs="?", default="", help="UI roll angle in degrees (default -66)")
cmd_melee_preview.add_argument("scale", nargs="?", default="", help="Preview mesh scale (default 2.05)")
cmd_melee_preview.add_argument("focus_radius", nargs="?", default="", help="Inspect focus radius UU (default 22)")
cmd_melee_preview.add_argument("pitch_deg", nargs="?", default="", help="UI pitch angle in degrees (default 0)")
cmd_melee_preview.add_argument("yaw_deg", nargs="?", default="", help="UI yaw angle in degrees (default 26)")
cmd_melee_preview.add_argument("bounds_z", nargs="?", default="", help="GestaltPartBounds Z slimming factor (default 0.66)")


@command(
    "melee_check_pools",
    description="Re-sync and inspect live Melee Arsenal world, chest, container, and vendor loot pools.",
)
def cmd_melee_check_pools(_args: Any) -> None:
    report = inspect_loot_pools(resync_first=True)
    log_info(
        f"melee_check_pools: enabled={report['world_drops_enabled']} weight={report['world_drop_weight']:.0f} "
        f"injected={report['injected_pools']}/{report['loaded_target_pools']} "
        f"avg_share={report['avg_weapon_share_pct']}% stage_gates_cleared={report['subpool_stage_gates_cleared']} "
        f"by_category={report['by_category']}"
    )
    for row in report["pools"]:
        log_info(
            f"  [{row['category']}] {row['pool']} -> {row['subpool']}: "
            f"melee={row['melee_weight']} / total_weap={row['weapon_total_weight']} "
            f"({row['melee_weapon_share_pct']}%)"
        )
    if report["missing_pools"]:
        log_info(f"  not loaded in this map: {', '.join(report['missing_pools'])}")
    # Manual re-roll: vendors build their shelves during map load, so existing stock can predate
    # the injection above. This makes `melee_check_pools` also refresh every gun vendor in the map.
    vendors = refresh_vending_machines()
    if vendors > 0:
        log_info(f"  re-rolled {vendors} vending machine shelf(ies) from the injected shop pools.")
    _notify_hud(
        "Melee Loot Pools",
        f"{report['injected_pools']}/{report['loaded_target_pools']} pools active | "
        f"~{report['avg_weapon_share_pct']}% of weapon drops (weight={report['world_drop_weight']:.0f})",
    )


ALL_HOOKS = [
    hook_viewport_tick,
    hook_player_tick,
    hook_weapon_init_from_def,
    hook_weapon_init_internal_pre,
    hook_weapon_init_internal_post,
    hook_weapon_choose_names,
    hook_weapon_play_anim,
    hook_weapon_init_preview,
    hook_pawn_weapon_fired,
    hook_weapon_consume_ammo,
    hook_weapon_instant_fire_pre,
    hook_weapon_firing_mode_fire_pre,
    hook_weapon_play_fire_effects_pre,
    hook_light_proj_add_proj,
    hook_weapon_shared_fire_post,
    hook_weapon_has_active_ammo,
    hook_weapon_start_reload,
    hook_ai_take_damage,
    hook_player_take_damage,
    hook_display_recent_damage,
    hook_melee_start_zoom,
    hook_melee_client_start_zoom,
    on_melee_weapon_stop_zoom_pre,
    on_melee_weapon_client_stop_zoom_pre,
    hook_card_set_item_card_pre,
    hook_card_set_item_card_post,
    hook_card_set_item_card_ex_pre,
    hook_card_set_title_pre,
    hook_card_set_top_stat_pre,
    hook_card_set_fun_stats_pre,
    hook_card_set_item_card_ex_post,
    hook_validate_weapon_def,
    hook_apply_weapon_save_data,
    hook_place_spawned_items,
    hook_save_game_pre,
]

ALL_COMMANDS = [
    cmd_melee_spawn,
    cmd_melee_spawn_all,
    cmd_melee_dump_held,
    cmd_melee_secondary,
    cmd_melee_save_now,
    cmd_melee_preview,
    cmd_melee_check_pools,
]

ALL_OPTIONS = [
    opt_world_drops,
    opt_drop_weight,
    opt_slash_scale,
    opt_camera_shake,
    opt_guard_pose,
    opt_debug_log,
    opt_damage_reduction,
    opt_guard_damage_reduction,
    btn_spawn_all,
]


def _on_mod_disable() -> None:
    cancel_pending_spawns()
    clear_pending_restores()
    remove_melee_loot_entries()
    clear_active_card_overrides()
    restore_all_arms_rest_transforms()
    clear_all_pending_crit_feedback()
    reset_secondary_state()
    clear_debug_once_cache()


@command("melee_export_asset_registry")
def _cmd_export_asset_registry() -> None:
    export_asset_registry_command()


def build_runtime_mod(version: str, author: str) -> Any:
    mod_instance = build_mod(
        name="Melee Arsenal",
        author=author,
        version=version,
        description=(
            "Adds a new Weapon Type: Melee Weapons across 5 Blade-defined manufacturers "
            "(Hyperion Katana, Bandit Machete, Jakobs Sabre, Maliwan Phaseblade, Torgue Blast-Cleaver). "
            "Primary Fire swings your Melee Weapon (Single-Target or Cleave) scaling with player Melee & Roid bonuses, "
            "while [V] remains your vanilla character melee. RMB throws Bandit & Torgue blades for a full-strength hit, "
            "or guards with Hyperion & Jakobs blades (parry inside 0.33s). Every Melee Weapon also grants "
            "level-scaled incoming damage reduction (30% at Lv 1 up to 70%+ at OP10) while equipped."
        ),
        supported_games=Game.BL2,
        auto_enable=True,
        options=ALL_OPTIONS,
        hooks=ALL_HOOKS,
        commands=ALL_COMMANDS,
        on_disable=_on_mod_disable,
    )
    # Sync persisted ModMenu option values loaded by `build_mod()` into `STATE` immediately
    STATE.world_drops_enabled = bool(getattr(opt_world_drops, "value", True))
    STATE.world_drop_weight = float(getattr(opt_drop_weight, "value", 100.0))
    STATE.slash_motion_scale = float(getattr(opt_slash_scale, "value", 100)) / 100.0
    STATE.camera_shake_scale = float(getattr(opt_camera_shake, "value", 50)) / 100.0
    STATE.guard_pose_scale = max(
        -1.0, min(1.0, float(getattr(opt_guard_pose, "value", 100)) / 100.0)
    )
    STATE.debug_attack_log = bool(getattr(opt_debug_log, "value", False))
    STATE.damage_reduction_enabled = bool(getattr(opt_damage_reduction, "value", True))
    STATE.guard_damage_reduction_enabled = bool(getattr(opt_guard_damage_reduction, "value", True))
    log_info(f"Melee Arsenal v{version} loaded and armed.")
    return mod_instance
