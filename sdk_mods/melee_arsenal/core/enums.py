"""SDK-independent core enums for Borderlands 2 Melee Arsenal.

Compatible with CPython 3.11–3.14+ (including Willow2 Mod Manager v3.8's embedded Python 3.14).
Contains zero `unrealsdk` imports so all data structures, math, loot generation, and card logic
can be tested offline in standard CPython.
"""
from __future__ import annotations

from enum import Enum, IntEnum


class Manufacturer(str, Enum):
    """Select manufacturers producing blade-defined melee weapons."""

    HYPERION = "Hyperion"
    BANDIT = "Bandit"
    JAKOBS = "Jakobs"
    MALIWAN = "Maliwan"
    TORGUE = "Torgue"


class TargetingMode(str, Enum):
    """How a blade acquires and strikes enemies on primary fire (LMB)."""

    SINGLE_TARGET = "single_target"
    FRONTAL_CLEAVE = "frontal_cleave"


class SecondaryAttack(str, Enum):
    """Manufacturer-locked Right Mouse Button / Left Trigger secondary attack.

    Melee Weapons deliberately suppress the donor pistol's ADS zoom, freeing RMB for a
    manufacturer signature move:

    - `THROW` (Bandit, Torgue): hurl the blade along a weight-driven arc. It strikes with
      a normal LMB hit, then homes back to your hand. LMB is locked while airborne.
    - `GUARD` (Hyperion, Jakobs): hold RMB to brace. The next incoming hit has the blade's
      rolled Damage Reduction applied twice, and a hit inside the `0.33 s` parry window deals
      zero damage and is reflected back at a melee attacker as a forced critical hit.
    - `NONE` (Maliwan): RMB stays inert.
    """

    NONE = "none"
    THROW = "throw"
    GUARD = "guard"


class Element(str, Enum):
    """Elemental damage types supported on melee weapons."""

    NONE = "None"
    FIRE = "Fire"
    SHOCK = "Shock"
    CORROSIVE = "Corrosive"
    SLAG = "Slag"
    EXPLOSIVE = "Explosive"


class ElementPolicy(str, Enum):
    """Manufacturer rule governing whether an elemental part rolls on a blade."""

    NEVER = "never"                    # Jakobs: strictly non-elemental
    OPTIONAL = "optional"              # Hyperion, Bandit: physical or allowed elements
    ALWAYS_ELEMENTAL = "always_elem"   # Maliwan: always Fire / Shock / Corrosive / Slag
    ALWAYS_EXPLOSIVE = "always_expl"   # Torgue: always Explosive


class PartSlot(str, Enum):
    """Gameplay-visible melee part slots (3-4 parts per weapon) plus internal host slots."""

    BLADE = "Blade"            # Maps to BarrelPartDefinition (defines manufacturer, archetype & targeting)
    GRIP = "Grip"              # Maps to GripPartDefinition (handling, speed/damage tradeoff, matching bonus)
    ACCESSORY = "Accessory"    # Maps to Accessory1PartDefinition (prefixes & specialized stat perks)
    ELEMENT = "Element"        # Maps to ElementalPartDefinition (optional/locked per Blade's ElementPolicy)

    # Internal structural slots on BL2's WeaponDefinitionData (locked to Blade manufacturer / rarity)
    BODY_HOST = "BodyHost"     # Maps to BodyPartDefinition (supplies manufacturer logo, anim context, empty sight/stock)
    MATERIAL = "Material"      # Maps to MaterialPartDefinition (rarity skin MIC)


class ModifierMode(str, Enum):
    """Mirrors Gearbox's EModifierType attribute evaluation stages in BL2."""

    PRE_ADD = "MT_PreAdd"
    SCALE = "MT_Scale"
    POST_ADD = "MT_PostAdd"


class StatId(str, Enum):
    """Canonical stat identifiers modified by blades, grips, accessories, and elements."""

    BASE_DAMAGE = "base_damage"
    RANGE_UU = "range_uu"
    SWING_SPEED = "swing_speed"
    CRIT_BONUS = "crit_bonus"
    CLEAVE_ARC = "cleave_arc"
    STATUS_CHANCE = "status_chance"
    STATUS_DAMAGE = "status_damage"
    SPLASH_RADIUS = "splash_radius"
    SPLASH_PCT = "splash_pct"
    MOMENTUM = "momentum"


class Rarity(IntEnum):
    """Canonical rarity tiers mapped to BL2's GlobalsDefinition.RarityLevelColors."""

    COMMON = 1         # White
    UNCOMMON = 2       # Green
    RARE = 3           # Blue
    VERY_RARE = 4      # Purple
    E_TECH = 6         # Magenta (unused for base melee, included for accurate BL2 rarity decoding)
    LEGENDARY = 5      # Orange (note: BL2 also uses 7-10 for legendary pools; see decode_bl2_rarity)
    PEARLESCENT = 500  # Cyan
    SERAPH = 501       # Pink
    EFFERVESCENT = 506 # Rainbow

    @classmethod
    def from_bl2_level(cls, level: int) -> Rarity:
        """Decode BL2's non-contiguous StaticCalculateWeaponRarityLevel integer."""
        if level <= 1:
            return cls.COMMON
        if level == 2:
            return cls.UNCOMMON
        if level == 3:
            return cls.RARE
        if level == 4:
            return cls.VERY_RARE
        if level == 6:
            return cls.E_TECH
        if level in (5, 7, 8, 9, 10):
            return cls.LEGENDARY
        if level == 500:
            return cls.PEARLESCENT
        if level == 501:
            return cls.SERAPH
        if level >= 506:
            return cls.EFFERVESCENT
        return cls.LEGENDARY if level > 4 else cls.COMMON

    @property
    def hex_color(self) -> str:
        """Scaleform HTML hex color matching BL2 item cards."""
        return {
            Rarity.COMMON: "#ffffff",
            Rarity.UNCOMMON: "#3dd201",
            Rarity.RARE: "#3c8eff",
            Rarity.VERY_RARE: "#a83fe5",
            Rarity.E_TECH: "#ca00a8",
            Rarity.LEGENDARY: "#ff9c00",
            Rarity.PEARLESCENT: "#00ffff",
            Rarity.SERAPH: "#ff6bd6",
            Rarity.EFFERVESCENT: "#ff77ff",
        }[self]

    @property
    def label(self) -> str:
        return {
            Rarity.COMMON: "Common",
            Rarity.UNCOMMON: "Uncommon",
            Rarity.RARE: "Rare",
            Rarity.VERY_RARE: "Very Rare",
            Rarity.E_TECH: "E-Tech",
            Rarity.LEGENDARY: "Legendary",
            Rarity.PEARLESCENT: "Pearlescent",
            Rarity.SERAPH: "Seraph",
            Rarity.EFFERVESCENT: "Effervescent",
        }[self]
