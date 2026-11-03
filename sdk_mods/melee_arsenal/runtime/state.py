"""Central runtime state, rooted UObject caches, and active swing state."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.enums import Element, Manufacturer, Rarity


# UE3 EObjectFlags: RF_RootSet / KeepAlive used by BL2 PythonSDK mods
KEEP_ALIVE_FLAGS: int = 0x4000


@dataclass
class SwingMotionState:
    """Short-lived procedural 1st-person slash/cleave offset applied over ~160ms."""

    active: bool = False
    start_time: float = 0.0
    duration: float = 0.16
    swing_sign: int = 1           # Alternates +1 / -1 on consecutive swings for fluid left/right combo feel


@dataclass
class RuntimeRegistry:
    """Singleton state populated when definitions & loot pools are built."""

    initialized: bool = False
    pools_injected: bool = False

    # Rooted package & subpackages so GC never collects custom definitions
    rooted_objects: list[Any] = field(default_factory=list)
    root_package: Any = None

    # WeaponTypeDefinitions & FiringModeDefinitions per Blade Manufacturer / Element
    maker_to_wt: dict[Manufacturer, Any] = field(default_factory=dict)
    donor_wt_by_maker: dict[Manufacturer, Any] = field(default_factory=dict)
    wt_path_to_maker: dict[str, Manufacturer] = field(default_factory=dict)
    maker_to_firing_mode: dict[Manufacturer, Any] = field(default_factory=dict)
    fm_path_to_maker: dict[str, Manufacturer] = field(default_factory=dict)
    element_to_firing_mode: dict[Element, Any] = field(default_factory=dict)
    maker_to_donor_manufacturer: dict[Manufacturer, Any] = field(default_factory=dict)

    # WeaponPartDefinitions & reverse path maps
    blade_parts: dict[str, Any] = field(default_factory=dict)        # blade_id -> WeaponPartDefinition (Barrel slot)
    grip_parts: dict[str, Any] = field(default_factory=dict)         # grip_id -> WeaponPartDefinition (Grip slot)
    body_parts: dict[Manufacturer, Any] = field(default_factory=dict)  # maker -> WeaponPartDefinition (Body slot)
    accessory_parts: dict[str, Any] = field(default_factory=dict)    # acc_id -> WeaponPartDefinition (Accessory1 slot)
    element_parts: dict[str, Any] = field(default_factory=dict)      # elem_id -> WeaponPartDefinition (Elemental slot)
    material_parts: dict[tuple[Manufacturer, Rarity], Any] = field(default_factory=dict)

    # Custom Inkblade 3D Gestalt & unified-atlas material state (`v0.3.0+`)
    custom_assets_active: bool = False
    custom_kit_revision: str = ""
    custom_gestalt_def: Any = None
    custom_skeletal_mesh: Any = None
    ui_presentation: Any = None
    preview_roll_deg: float = -66.0
    preview_pitch_deg: float = 0.0
    preview_yaw_deg: float = 26.0
    preview_focus_radius: float = 22.0
    preview_mesh_scale: float = 2.05
    preview_bounds_xy_scale: float = 0.16
    preview_bounds_z_scale: float = 0.66
    logged_preview_diag: bool = False
    element_material_parts: dict[str, Any] = field(default_factory=dict)  # elem_id -> WeaponPartDefinition (Mat_Inkblade_<Elem>_*)
    element_material_parts_by_rarity: dict[tuple[str, Rarity], Any] = field(default_factory=dict)  # (elem_id, Rarity) -> WeaponPartDefinition
    grip_variant_parts: dict[tuple[str, str], Any] = field(default_factory=dict)       # (grip_id, parent_kit_id) -> WeaponPartDefinition
    accessory_variant_parts: dict[tuple[str, str], Any] = field(default_factory=dict)  # (acc_id, parent_kit_id) -> WeaponPartDefinition
    element_variant_parts: dict[tuple[str, str], Any] = field(default_factory=dict)    # (elem_id, parent_kit_id) -> WeaponPartDefinition
    all_extra_part_objects: list[Any] = field(default_factory=list)
    custom_melee_pools: dict[str, Any] = field(default_factory=dict)
    injected_pool_count: int = 0
    # (world_drops_enabled, world_drop_weight) the live vending machines were last re-rolled for
    vendor_refresh_signature: tuple[bool, float] | None = None

    part_path_to_blade_id: dict[str, str] = field(default_factory=dict)
    part_path_to_grip_id: dict[str, str] = field(default_factory=dict)
    part_path_to_accessory_id: dict[str, str] = field(default_factory=dict)
    part_path_to_element_id: dict[str, str] = field(default_factory=dict)

    # WeaponNamePartDefinitions for exact Titles & Prefixes
    title_parts: dict[str, Any] = field(default_factory=dict)        # title_text -> WeaponNamePartDefinition
    prefix_parts: dict[str, Any] = field(default_factory=dict)       # prefix_text -> WeaponNamePartDefinition
    empty_name_part: Any = None

    # WeaponBalanceDefinitions & guaranteed drop ItemPoolDefinitions for (Manufacturer, Rarity)
    balances: dict[tuple[Manufacturer, Rarity], Any] = field(default_factory=dict)
    balance_path_to_meta: dict[str, tuple[Manufacturer, Rarity]] = field(default_factory=dict)
    balance_drop_pools: dict[tuple[Manufacturer, Rarity], Any] = field(default_factory=dict)
    drop_spawner: Any = None

    # Cached DamageTypeDefinitions, ImpactDefinitions, AkEvents, CameraAnims, Attributes
    damage_types: dict[Element, Any] = field(default_factory=dict)
    dmg_source_bullet_cls: Any = None
    impact_bullet_normal: Any = None
    impact_explosive: Any = None

    ak_swing_light: Any = None
    ak_swing_heavy: Any = None
    ak_hit_blade: Any = None
    ak_hit_heavy: Any = None

    canim_light: Any = None
    canim_cleave: Any = None
    canim_heavy: Any = None

    attr_melee_mult: Any = None
    attr_melee_added_buff: Any = None
    attr_roid_damage: Any = None
    attr_player_crit: Any = None
    attr_status_dmg_mult: Any = None

    # Runtime settings controlled via ModMenu options
    world_drops_enabled: bool = True
    world_drop_weight: float = 100.0
    slash_motion_scale: float = 1.0
    camera_shake_scale: float = 0.5   # `Swing Camera Shake (%)` / 100
    guard_pose_scale: float = 1.0     # `Guard Blade Tilt (%)` / 100 (-1..1; negative flips inward/outward)
    stack_player_melee_bonuses: bool = True
    stack_roid_shields: bool = True
    damage_reduction_enabled: bool = True
    guard_damage_reduction_enabled: bool = True
    debug_attack_log: bool = False

    # Re-entrancy guard during melee strike dispatch
    in_melee_swing_dispatch: bool = False
    last_hit_sound_time: float = 0.0
    swing_motion: SwingMotionState = field(default_factory=SwingMotionState)

    def keep_alive(self, obj: Any) -> Any:
        if obj is not None:
            try:
                obj.ObjectFlags |= KEEP_ALIVE_FLAGS
            except Exception:
                pass
            self.rooted_objects.append(obj)
        return obj


STATE = RuntimeRegistry()
