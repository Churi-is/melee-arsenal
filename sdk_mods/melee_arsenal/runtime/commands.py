"""Console commands & ModMenu actions for spawning and inspecting Melee Arsenal weapons."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..core.catalog import ACCESSORIES, resolve_weapon_name
from ..core.enums import Element, Manufacturer, Rarity
from ..core.loot_roller import roll_melee_weapon
from ..core.schemas import MeleeWeaponInstance
from ..core.stats import compute_attack_damage
from .combat import read_live_player_melee_context
from .custom_assets import resolve_fitted_accessory_part
from .definitions import apply_ui_preview_settings_to_all_wts, ensure_definitions_built
from .log import log_info, log_warn, obj_path
from .loot_pools import sync_loot_pools
from .resolver import (
    get_local_player_controller,
    get_player_level,
    resolve_melee_weapon,
    spawn_melee_weapon_batch,
)
from .state import STATE


_MAKER_ALIASES: dict[str, Manufacturer] = {
    "hyperion": Manufacturer.HYPERION,
    "katana": Manufacturer.HYPERION,
    "hyp": Manufacturer.HYPERION,
    "bandit": Manufacturer.BANDIT,
    "machete": Manufacturer.BANDIT,
    "ban": Manufacturer.BANDIT,
    "jakobs": Manufacturer.JAKOBS,
    "sabre": Manufacturer.JAKOBS,
    "saber": Manufacturer.JAKOBS,
    "jak": Manufacturer.JAKOBS,
    "maliwan": Manufacturer.MALIWAN,
    "phaseblade": Manufacturer.MALIWAN,
    "mal": Manufacturer.MALIWAN,
    "torgue": Manufacturer.TORGUE,
    "cleaver": Manufacturer.TORGUE,
    "blastcleaver": Manufacturer.TORGUE,
    "tor": Manufacturer.TORGUE,
}

_RARITY_ALIASES: dict[str, Rarity] = {
    "common": Rarity.COMMON,
    "white": Rarity.COMMON,
    "1": Rarity.COMMON,
    "uncommon": Rarity.UNCOMMON,
    "green": Rarity.UNCOMMON,
    "2": Rarity.UNCOMMON,
    "rare": Rarity.RARE,
    "blue": Rarity.RARE,
    "3": Rarity.RARE,
    "veryrare": Rarity.VERY_RARE,
    "very_rare": Rarity.VERY_RARE,
    "purple": Rarity.VERY_RARE,
    "epic": Rarity.VERY_RARE,
    "4": Rarity.VERY_RARE,
}

_ELEMENT_ALIASES: dict[str, Element] = {
    "none": Element.NONE,
    "physical": Element.NONE,
    "fire": Element.FIRE,
    "incendiary": Element.FIRE,
    "shock": Element.SHOCK,
    "corrosive": Element.CORROSIVE,
    "acid": Element.CORROSIVE,
    "slag": Element.SLAG,
    "explosive": Element.EXPLOSIVE,
}


_ACCESSORY_ALIASES: dict[str, str] = {
    "none": "Acc_None",
    "serrated": "Acc_Serrated",
    "pommel": "Acc_Counterweight",
    "counterweight": "Acc_Counterweight",
    "tang": "Acc_ExtendedTang",
    "fuller": "Acc_ExtendedTang",
    "extended": "Acc_ExtendedTang",
    "razor": "Acc_RazorFuller",
    "mono": "Acc_RazorFuller",
    "honing": "Acc_RazorFuller",
    "guard": "Acc_BroadSweep",
    "sweep": "Acc_BroadSweep",
    "cleave": "Acc_BroadSweep",
    "spine": "Acc_HeavySpine",
    "heavy": "Acc_HeavySpine",
    "flux": "Acc_FluxCapacitor",
    "coil": "Acc_FluxCapacitor",
}


def _format_spawned_summary(inst: MeleeWeaponInstance) -> str:
    _, _, full_name = resolve_weapon_name(
        inst.parts.blade_id,
        inst.parts.grip_id,
        inst.parts.accessory_id,
        inst.parts.element_id,
    )
    acc_part = resolve_fitted_accessory_part(
        inst.parts.accessory_id,
        blade_id=inst.parts.blade_id,
        grip_id=inst.parts.grip_id,
    )
    acc_frag = str(getattr(acc_part, "GestaltModeSkeletalMeshName", "None")) if acc_part is not None else "None"
    return (
        f"'{full_name}' [{inst.parts.blade_id} | {inst.parts.grip_id} | "
        f"{inst.parts.accessory_id} ({acc_frag}) | {inst.parts.element_id}]"
    )


def spawn_single_command(
    maker_arg: str = "",
    rarity_arg: str = "purple",
    element_arg: str = "",
    accessory_arg: str = "",
    delivery: str = "drop",
) -> int:
    """Roll and drop one Melee Weapon in front of the player matching optional maker / rarity / element / accessory."""
    if not ensure_definitions_built():
        return 0
    sync_loot_pools()

    pc = get_local_player_controller()
    lvl = get_player_level(pc)

    maker = _MAKER_ALIASES.get(maker_arg.strip().lower()) if maker_arg.strip() else None
    rarity = _RARITY_ALIASES.get(rarity_arg.strip().lower(), Rarity.VERY_RARE)
    forced_elem = _ELEMENT_ALIASES.get(element_arg.strip().lower()) if element_arg.strip() else None
    acc_token = accessory_arg.strip()
    forced_acc = (
        _ACCESSORY_ALIASES.get(acc_token.lower())
        or (acc_token if acc_token in ACCESSORIES else None)
    )

    inst = roll_melee_weapon(game_stage=lvl, manufacturer=maker, rarity=rarity)
    if forced_elem is not None:
        for _ in range(40):
            cand = roll_melee_weapon(game_stage=lvl, manufacturer=maker, rarity=rarity)
            if cand.parts.element_id.lower().endswith(forced_elem.name.lower()):
                inst = cand
                break
    if forced_acc is not None and forced_acc in ACCESSORIES:
        inst = replace(inst, parts=replace(inst.parts, accessory_id=forced_acc))

    spawned = spawn_melee_weapon_batch([inst], pc=pc, delivery=delivery)
    if spawned > 0:
        log_info(f"Spawned Lv{lvl} {rarity.name} {_format_spawned_summary(inst)}")
    return spawned


def spawn_all_five_archetypes(rarity_arg: str = "purple", delivery: str = "drop") -> int:
    """Roll and drop all 5 Manufacturer Melee Archetypes right in front of the player!"""
    if not ensure_definitions_built():
        return 0
    sync_loot_pools()

    pc = get_local_player_controller()
    lvl = get_player_level(pc)
    rarity = _RARITY_ALIASES.get(rarity_arg.strip().lower(), Rarity.VERY_RARE)

    batch = [
        roll_melee_weapon(game_stage=lvl, manufacturer=maker, rarity=rarity)
        for maker in Manufacturer
    ]
    spawned = spawn_melee_weapon_batch(batch, pc=pc, delivery=delivery)
    details = "; ".join(_format_spawned_summary(it) for it in batch)
    log_info(f"Dropped {spawned}/5 Lv{lvl} {rarity.name} Melee Archetypes in front of player: {details}")
    return spawned


def tune_ui_preview_command(
    roll_deg: str = "",
    scale: str = "",
    focus_radius: str = "",
    pitch_deg: str = "",
    yaw_deg: str = "",
    bounds_z_scale: str = "",
) -> dict[str, float]:
    """Inspect or live-update Inventory / Inspect 3D weapon preview rotation, scale, focus radius, and bounds."""
    ensure_definitions_built()
    if roll_deg.strip():
        STATE.preview_roll_deg = float(roll_deg)
    if scale.strip():
        STATE.preview_mesh_scale = max(0.25, float(scale))
    if focus_radius.strip():
        STATE.preview_focus_radius = max(5.0, float(focus_radius))
    if pitch_deg.strip():
        STATE.preview_pitch_deg = float(pitch_deg)
    if yaw_deg.strip():
        STATE.preview_yaw_deg = float(yaw_deg)
    if bounds_z_scale.strip():
        bz = max(0.10, min(1.5, float(bounds_z_scale)))
        STATE.preview_bounds_z_scale = bz
        STATE.preview_bounds_xy_scale = round(bz * 0.24, 3)

    apply_ui_preview_settings_to_all_wts()
    cfg = {
        "roll_deg": round(STATE.preview_roll_deg, 2),
        "pitch_deg": round(STATE.preview_pitch_deg, 2),
        "yaw_deg": round(STATE.preview_yaw_deg, 2),
        "scale": round(STATE.preview_mesh_scale, 2),
        "focus_radius": round(STATE.preview_focus_radius, 2),
        "bounds_z_scale": round(STATE.preview_bounds_z_scale, 2),
    }
    log_info(f"UI Preview config: {cfg}")
    return cfg


def dump_held_melee_weapon() -> dict[str, Any] | None:
    """Log full breakdown of the currently held Melee Weapon + live player melee/Roid scaling."""
    pc = get_local_player_controller()
    pawn = getattr(pc, "Pawn", None) if pc is not None else None
    wpn = getattr(pawn, "Weapon", None) if pawn is not None else None
    resolved = resolve_melee_weapon(wpn, sync_weapon_props=True)
    if resolved is None:
        log_warn("Held weapon is not a Melee Arsenal weapon.")
        return None

    inst, card = resolved
    ctx = read_live_player_melee_context(pc, pawn)
    dmg = compute_attack_damage(card, ctx, is_crit=False)
    dmg_crit = compute_attack_damage(card, ctx, is_crit=True)

    summary = {
        "name": card.full_name,
        "manufacturer": card.manufacturer.value,
        "archetype": card.archetype_name,
        "level": inst.game_stage,
        "rarity": inst.rarity.name,
        "parts": {
            "blade": inst.parts.blade_id,
            "grip": inst.parts.grip_id,
            "accessory": inst.parts.accessory_id,
            "element": inst.parts.element_id,
        },
        "visuals": {
            "custom_assets_active": STATE.custom_assets_active,
            "kit_revision": STATE.custom_kit_revision,
            "blade_fragment": str(
                getattr(getattr(wpn.DefinitionData, "BarrelPartDefinition", None), "GestaltModeSkeletalMeshName", "")
            ),
            "grip_fragment": str(
                getattr(getattr(wpn.DefinitionData, "GripPartDefinition", None), "GestaltModeSkeletalMeshName", "")
            ),
            "accessory_fragment": str(
                getattr(getattr(wpn.DefinitionData, "Accessory1PartDefinition", None), "GestaltModeSkeletalMeshName", "")
            ),
            "element_fragment": str(
                getattr(getattr(wpn.DefinitionData, "ElementalPartDefinition", None), "GestaltModeSkeletalMeshName", "")
            ),
            "material_part": obj_path(getattr(wpn.DefinitionData, "MaterialPartDefinition", None)),
        },
        "card_stats": {
            "damage": card.card_damage,
            "range_m": card.range_meters,
            "swing_speed": card.swing_speed,
            "targeting": card.targeting_mode.name,
            "cleave_arc_deg": card.cleave_arc_degrees,
            "crit_bonus_pct": card.crit_bonus_pct,
            "elemental_dps": card.elemental_dps,
            "elemental_chance_pct": card.elemental_chance_pct,
            "damage_reduction_pct": card.damage_reduction_pct,
        },
        "live_player_bonuses": {
            "melee_damage_scale": round(ctx.melee_damage_scale, 3),
            "roid_flat_damage": round(ctx.roid_flat_damage, 1),
            "status_damage_scale": round(ctx.status_damage_scale, 3),
            "effective_body_hit": dmg.final_hit_damage,
            "effective_weakspot_crit": dmg_crit.final_hit_damage,
            "effective_splash": dmg.splash_hit_damage,
        },
    }
    log_info(f"Held Melee Weapon Breakdown: {summary}")
    return summary
