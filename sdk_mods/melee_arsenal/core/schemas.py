"""SDK-independent schemas and dataclasses for Borderlands 2 Melee Arsenal."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .enums import (
    Element,
    ElementPolicy,
    Manufacturer,
    ModifierMode,
    PartSlot,
    Rarity,
    StatId,
    TargetingMode,
)


@dataclass(frozen=True, slots=True)
class StatModifier:
    """Single stat modifier mirroring Gearbox's AttributeEffectData."""

    stat: StatId
    mode: ModifierMode
    value: float


@dataclass(frozen=True, slots=True)
class GestaltFragmentMeta:
    """Metadata mapping a part to a custom or donor GestaltSkeletalMesh fragment.

    Compatible with `bl2-part-pipeline` / `Ribs Arsenal` `.upk` fragment registration:
    before custom `.upk` blade/grip meshes are cooked, `custom_package` is None and the
    SDK bridge binds `donor_fragment` from the host weapon type's gestalt mesh.
    """

    fragment_name: str
    donor_fragment: str
    custom_package: str | None = None
    first_index: int | None = None
    num_primitives: int | None = None
    bounds_min: tuple[float, float, float] | None = None
    bounds_max: tuple[float, float, float] | None = None
    sockets: tuple[str, ...] = ("Muzzle",)


@dataclass(frozen=True, slots=True)
class CleaveProfile:
    """Spatial strike profile determined by the Blade (and modified by parts)."""

    mode: TargetingMode
    base_range_uu: float          # Reach in Unreal Units (~100 UU = 1m in card display; vanilla melee ~140-160 UU)
    arc_degrees: float            # Full horizontal cone angle in front of player (e.g. 95° for Bandit, 12° for Katana)
    pitch_limit_degrees: float    # Vertical half-angle tolerance so frontal cleave hits crouched/leaping foes
    max_targets: int              # 1 for single-target; 16+ (effectively unlimited) for frontal cleave
    can_weakspot_crit: bool       # True for surgical single-target blades (Hyperion/Jakobs); optional on cleave
    base_crit_bonus: float        # Extra critical hit bonus (e.g. 0.75 = +75% crit dmg)
    splash_radius_uu: float = 0.0 # Explosive splash radius (Torgue Blast-Cleaver)
    splash_damage_pct: float = 0.0


@dataclass(frozen=True, slots=True)
class ManufacturerSpec:
    """Defines a manufacturer's blade identity, stats, element rules, and input behavior."""

    manufacturer: Manufacturer
    archetype_name: str           # e.g. "Katana", "Machete", "Sabre", "Phaseblade", "Blast-Cleaver"
    description: str
    element_policy: ElementPolicy
    allowed_elements: tuple[Element, ...]
    base_damage_mult: float       # Multiplies the melee weapon type's level-scaled gun-damage baseline
    base_swing_speed: float       # Swings per second (sets WillowWeapon.FireInterval = 1 / swing_speed)
    semi_auto_click: bool         # True = swings as fast as clicked (Jakobs); False = hold LMB for continuous swings
    cleave_profile: CleaveProfile
    matching_grip_damage_bonus: float = 0.12
    matching_grip_speed_bonus: float = 0.10
    intrinsic_modifiers: tuple[StatModifier, ...] = ()
    donor_manufacturer_path: str = ""


@dataclass(frozen=True, slots=True)
class BladePartSpec:
    """Primary defining part of a melee weapon (occupies BarrelPartDefinition in BL2)."""

    part_id: str                  # e.g. "Blade_Hyperion_Katana"
    manufacturer: Manufacturer
    display_name: str
    base_title: str               # Default title when paired with matching grip
    grip_titles: Mapping[Manufacturer, str]  # Title matrix by Grip manufacturer
    modifiers: tuple[StatModifier, ...]
    gestalt: GestaltFragmentMeta
    donor_part_path: str


@dataclass(frozen=True, slots=True)
class GripPartSpec:
    """Cross-manufacturer melee grip part (occupies GripPartDefinition in BL2)."""

    part_id: str                  # e.g. "Grip_Jakobs"
    manufacturer: Manufacturer
    display_name: str
    modifiers: tuple[StatModifier, ...]
    gestalt: GestaltFragmentMeta
    donor_part_path: str
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class AccessoryPartSpec:
    """Optional/rarity-gated melee accessory part (occupies Accessory1PartDefinition in BL2)."""

    part_id: str                  # e.g. "Acc_Serrated"
    display_name: str
    prefix_by_manufacturer: Mapping[Manufacturer, str]
    default_prefix: str
    modifiers: tuple[StatModifier, ...]
    gestalt: GestaltFragmentMeta | None
    donor_part_path: str
    min_rarity: Rarity = Rarity.COMMON
    elemental_only: bool = False
    cleave_only: bool = False
    single_target_only: bool = False
    weight: float = 1.0

    def prefix_for(self, manufacturer: Manufacturer) -> str:
        return self.prefix_by_manufacturer.get(manufacturer, self.default_prefix)


@dataclass(frozen=True, slots=True)
class ElementPartSpec:
    """Elemental infusion part (occupies ElementalPartDefinition in BL2)."""

    part_id: str                  # e.g. "Elem_Fire"
    element: Element
    display_name: str
    prefix_by_manufacturer: Mapping[Manufacturer, str]
    default_prefix: str
    modifiers: tuple[StatModifier, ...]
    dot_dps_ratio: float          # Base DoT per second as a fraction of lvl-scaled base hit damage
    base_proc_chance: float       # Base elemental proc chance per swing (e.g. 0.28 = 28%)
    damage_type_path: str
    donor_part_path: str
    weight: float = 1.0

    def prefix_for(self, manufacturer: Manufacturer) -> str:
        return self.prefix_by_manufacturer.get(manufacturer, self.default_prefix)


@dataclass(frozen=True, slots=True)
class MeleeWeaponParts:
    """The 3-4 gameplay parts composing a specific melee weapon."""

    blade_id: str
    grip_id: str
    accessory_id: str             # "Acc_None" when rolled with 3 parts (Blade + Grip + Element/None)
    element_id: str               # "Elem_None" when non-elemental

    @property
    def active_part_count(self) -> int:
        """Count of non-empty gameplay parts (2 to 4; typically 3-4 with accessory/element)."""
        count = 2  # Blade + Grip are always present
        if self.accessory_id != "Acc_None":
            count += 1
        if self.element_id != "Elem_None":
            count += 1
        return count


@dataclass(frozen=True, slots=True)
class MeleeWeaponInstance:
    """Complete SDK-independent representation of a rolled or saved melee weapon."""

    unique_id: int
    game_stage: int               # 1..90 (1..80 + OP1..OP10)
    rarity: Rarity
    parts: MeleeWeaponParts
    grade_index: int = 0


@dataclass(frozen=True, slots=True)
class PlayerMeleeContext:
    """Snapshot of the player's live melee/gun bonuses read at swing time.

    Allows player melee skills (Zer0, Krieg, Gaige, BAR, Bladed relics/COMs) and depleted
    Roid shields to stack directly on top of the melee weapon's gun-scaled card damage!
    """

    melee_damage_scale: float = 1.0       # 1.0 = +0% melee bonus; 2.5 = +150% melee bonus
    gun_or_all_damage_scale: float = 1.0  # Optional global/all-damage bonus from skills/relics
    roid_flat_damage: float = 0.0         # Active Roid shield AmplifyDamage when shield == 0
    bonus_crit_damage: float = 0.0        # Extra additive crit bonus from player skills
    status_damage_scale: float = 1.0      # Elemental status damage multiplier (e.g. Maya Flicker/Miasma, Krieg Hellborn)
    status_chance_scale: float = 1.0      # Elemental status chance multiplier
    swing_speed_scale: float = 1.0        # Attack speed bonuses (e.g. fire rate / melee speed skills)
    is_uvhm: bool = False                 # Controls Slag multiplier (2.0x NVHM/TVHM vs 3.0x UVHM)
    extra_tags: Mapping[str, float] = field(default_factory=dict)
