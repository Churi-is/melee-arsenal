"""Complete part, manufacturer, and naming catalog for Borderlands 2 Melee Arsenal."""
from __future__ import annotations

from .enums import (
    Element,
    ElementPolicy,
    Manufacturer,
    ModifierMode,
    Rarity,
    SecondaryAttack,
    StatId,
    TargetingMode,
)
from .schemas import (
    AccessoryPartSpec,
    BladePartSpec,
    CleaveProfile,
    ElementPartSpec,
    GestaltFragmentMeta,
    GripPartSpec,
    ManufacturerSpec,
    StatModifier,
)


# ---------------------------------------------------------------------------
# 1. Manufacturers (defined by the Blade!)
# ---------------------------------------------------------------------------

MANUFACTURERS: dict[Manufacturer, ManufacturerSpec] = {
    Manufacturer.HYPERION: ManufacturerSpec(
        manufacturer=Manufacturer.HYPERION,
        archetype_name="Katana",
        description=(
            "High-frequency corporate monomolecular katana. Surgical single-target reach "
            "with fast cadence and elevated weakspot critical damage. Optional elemental edge."
        ),
        element_policy=ElementPolicy.OPTIONAL,
        allowed_elements=(
            Element.NONE,
            Element.FIRE,
            Element.SHOCK,
            Element.CORROSIVE,
            Element.SLAG,
        ),
        base_damage_mult=1.18,
        base_swing_speed=1.85,
        semi_auto_click=False,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.SINGLE_TARGET,
            base_range_uu=340.0,     # 3.40m surgical lunge/slash reach
            arc_degrees=14.0,        # Generous aim-assist ray cone onto single closest-to-reticle target
            pitch_limit_degrees=45.0,
            max_targets=1,
            can_weakspot_crit=True,
            base_crit_bonus=0.75,    # +75% critical hit bonus
        ),
        matching_grip_damage_bonus=0.10,
        matching_grip_speed_bonus=0.12,
        intrinsic_modifiers=(
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.10),
        ),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Hyperion",
        # High-tech monomolecular alloy: the lightest blade in the kit.
        secondary_attack=SecondaryAttack.GUARD,
        part_weight_kg=1.40,
    ),
    Manufacturer.BANDIT: ManufacturerSpec(
        manufacturer=Manufacturer.BANDIT,
        archetype_name="Machete",
        description=(
            "Jury-rigged scrapyard heavy machete. Wide brutal horizontal cleave hitting every "
            "enemy in front of you. Optional incendiary, corrosive, or slag coating."
        ),
        element_policy=ElementPolicy.OPTIONAL,
        allowed_elements=(
            Element.NONE,
            Element.FIRE,
            Element.CORROSIVE,
            Element.SLAG,
        ),
        base_damage_mult=1.48,
        base_swing_speed=1.35,
        semi_auto_click=False,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.FRONTAL_CLEAVE,
            base_range_uu=285.0,     # 2.85m brawling cleave reach
            arc_degrees=95.0,        # Hits ALL enemies in a 95-degree frontal cone
            pitch_limit_degrees=50.0,
            max_targets=24,
            can_weakspot_crit=False,
            base_crit_bonus=0.0,
        ),
        matching_grip_damage_bonus=0.15,
        matching_grip_speed_bonus=0.08,
        intrinsic_modifiers=(
            StatModifier(StatId.MOMENTUM, ModifierMode.SCALE, 0.25),
        ),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Bandit",
        # Scrapyard steel slab: heavy enough to throw short, flat and mean.
        secondary_attack=SecondaryAttack.THROW,
        part_weight_kg=2.90,
    ),
    Manufacturer.JAKOBS: ManufacturerSpec(
        manufacturer=Manufacturer.JAKOBS,
        archetype_name="Sabre",
        description=(
            "Hand-forged frontier duelist sabre. Strictly non-elemental, high raw steel damage, "
            "massive critical multiplier, and swings as fast as you pull the trigger."
        ),
        element_policy=ElementPolicy.NEVER,
        allowed_elements=(Element.NONE,),
        base_damage_mult=1.40,       # Fastest cadence in the kit: per-hit damage stays in check
        base_swing_speed=2.35,       # Semi-auto cadence cap ("Swings as fast as you pull the trigger")
        semi_auto_click=True,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.SINGLE_TARGET,
            base_range_uu=310.0,     # 3.10m duelist thrust/slash reach
            arc_degrees=12.0,
            pitch_limit_degrees=45.0,
            max_targets=1,
            can_weakspot_crit=True,
            base_crit_bonus=1.00,    # +100% critical hit bonus
        ),
        matching_grip_damage_bonus=0.15,
        matching_grip_speed_bonus=0.10,
        intrinsic_modifiers=(),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Jakobs",
        # Solid frontier steel with a weighted pommel: guard-tuned, mid weight.
        secondary_attack=SecondaryAttack.GUARD,
        part_weight_kg=2.10,
    ),
    Manufacturer.MALIWAN: ManufacturerSpec(
        manufacturer=Manufacturer.MALIWAN,
        archetype_name="Phaseblade",
        description=(
            "Artisanal hard-light elemental phaseblade. Always elemental, sweeping gracefully "
            "through all enemies in a wide arc with superior elemental chance and DoT."
        ),
        element_policy=ElementPolicy.ALWAYS_ELEMENTAL,
        allowed_elements=(
            Element.FIRE,
            Element.SHOCK,
            Element.CORROSIVE,
            Element.SLAG,
        ),
        base_damage_mult=1.12,
        base_swing_speed=1.65,
        semi_auto_click=False,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.FRONTAL_CLEAVE,
            base_range_uu=325.0,     # 3.25m sweeping energy arc
            arc_degrees=110.0,       # Wide 110-degree sweeping cleave
            pitch_limit_degrees=50.0,
            max_targets=24,
            can_weakspot_crit=False,
            base_crit_bonus=0.0,
        ),
        matching_grip_damage_bonus=0.10,
        matching_grip_speed_bonus=0.10,
        intrinsic_modifiers=(
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.40),
            StatModifier(StatId.STATUS_DAMAGE, ModifierMode.SCALE, 0.35),
        ),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Maliwan",
        # Hard-light emitter: almost massless, and Maliwan grants no secondary attack at all.
        secondary_attack=SecondaryAttack.NONE,
        part_weight_kg=1.20,
    ),
    Manufacturer.TORGUE: ManufacturerSpec(
        manufacturer=Manufacturer.TORGUE,
        archetype_name="Blast-Cleaver",
        description=(
            "Rocket-assisted explosive heavy cleaver. Slow, earth-shaking swings that cleave "
            "every enemy in front of you and detonate on impact for bonus explosive splash!"
        ),
        element_policy=ElementPolicy.ALWAYS_EXPLOSIVE,
        allowed_elements=(Element.EXPLOSIVE,),
        base_damage_mult=2.20,
        base_swing_speed=0.92,
        semi_auto_click=False,
        cleave_profile=CleaveProfile(
            mode=TargetingMode.FRONTAL_CLEAVE,
            base_range_uu=275.0,     # 2.75m heavy slam cleave
            arc_degrees=85.0,
            pitch_limit_degrees=55.0,
            max_targets=24,
            can_weakspot_crit=False,
            base_crit_bonus=0.0,
            splash_radius_uu=180.0,  # 1.8m explosive shockwave around struck targets
            splash_damage_pct=0.50,
        ),
        matching_grip_damage_bonus=0.16,
        matching_grip_speed_bonus=0.08,
        intrinsic_modifiers=(
            StatModifier(StatId.MOMENTUM, ModifierMode.SCALE, 0.60),
        ),
        donor_manufacturer_path="GD_Manufacturers.Manufacturers.Torgue",
        # Rocket-assisted slab: the heaviest thing you can hurl.
        secondary_attack=SecondaryAttack.THROW,
        part_weight_kg=3.80,
    ),
}


# ---------------------------------------------------------------------------
# 2. Blades (define the weapon's Manufacturer, Title Matrix & Core Profile)
# ---------------------------------------------------------------------------

BLADES: dict[str, BladePartSpec] = {
    "Blade_Hyperion_Katana": BladePartSpec(
        part_id="Blade_Hyperion_Katana",
        manufacturer=Manufacturer.HYPERION,
        display_name="Hyperion Katana Blade",
        base_title="Ronin",
        grip_titles={
            Manufacturer.HYPERION: "Ronin",
            Manufacturer.BANDIT: "Street Samurai",
            Manufacturer.JAKOBS: "Kensei",
            Manufacturer.MALIWAN: "Cyber-Wakizashi",
            Manufacturer.TORGUE: "Shocktrooper",
        },
        modifiers=(),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Blade_Hyperion_Katana",
            donor_fragment="Pistol_Barrel_Hyperion",
        ),
        donor_part_path="GD_Weap_Pistol.Barrel.Pistol_Barrel_Hyperion",
    ),
    "Blade_Bandit_Machete": BladePartSpec(
        part_id="Blade_Bandit_Machete",
        manufacturer=Manufacturer.BANDIT,
        display_name="Bandit Machete Blade",
        base_title="Chopper",
        grip_titles={
            Manufacturer.BANDIT: "Chopper",
            Manufacturer.HYPERION: "Slicr",
            Manufacturer.JAKOBS: "Skinner",
            Manufacturer.MALIWAN: "Slag-Hakr",
            Manufacturer.TORGUE: "Gutz-Rippr",
        },
        modifiers=(),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Blade_Bandit_Machete",
            donor_fragment="Pistol_Barrel_Bandit",
        ),
        donor_part_path="GD_Weap_Pistol.Barrel.Pistol_Barrel_Bandit",
    ),
    "Blade_Jakobs_Sabre": BladePartSpec(
        part_id="Blade_Jakobs_Sabre",
        manufacturer=Manufacturer.JAKOBS,
        display_name="Jakobs Cavalry Sabre Blade",
        base_title="Cavalier",
        grip_titles={
            Manufacturer.JAKOBS: "Cavalier",
            Manufacturer.HYPERION: "Duelist",
            Manufacturer.BANDIT: "Cutlass",
            Manufacturer.MALIWAN: "Rapier",
            Manufacturer.TORGUE: "Broadsword",
        },
        modifiers=(),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Blade_Jakobs_Sabre",
            donor_fragment="Pistol_Barrel_Jakobs",
        ),
        donor_part_path="GD_Weap_Pistol.Barrel.Pistol_Barrel_Jakobs",
    ),
    "Blade_Maliwan_Phaseblade": BladePartSpec(
        part_id="Blade_Maliwan_Phaseblade",
        manufacturer=Manufacturer.MALIWAN,
        display_name="Maliwan Phaseblade",
        base_title="Seraphim",
        grip_titles={
            Manufacturer.MALIWAN: "Seraphim",
            Manufacturer.HYPERION: "Vector",
            Manufacturer.BANDIT: "Arc-Scythe",
            Manufacturer.JAKOBS: "Solstice",
            Manufacturer.TORGUE: "Supernova",
        },
        modifiers=(),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Blade_Maliwan_Phaseblade",
            donor_fragment="Pistol_Barrel_Maliwan",
        ),
        donor_part_path="GD_Weap_Pistol.Barrel.Pistol_Barrel_Maliwan",
    ),
    "Blade_Torgue_BlastCleaver": BladePartSpec(
        part_id="Blade_Torgue_BlastCleaver",
        manufacturer=Manufacturer.TORGUE,
        display_name="Torgue Blast-Cleaver Blade",
        base_title="Detonator",
        grip_titles={
            Manufacturer.TORGUE: "Detonator",
            Manufacturer.BANDIT: "Wrecking Bar",
            Manufacturer.HYPERION: "Impact Driver",
            Manufacturer.JAKOBS: "Anvil",
            Manufacturer.MALIWAN: "Thermobaric",
        },
        modifiers=(),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Blade_Torgue_BlastCleaver",
            donor_fragment="Pistol_Barrel_Torgue",
        ),
        donor_part_path="GD_Weap_Pistol.Barrel.Pistol_Barrel_Torgue",
    ),
}


# ---------------------------------------------------------------------------
# 3. Grips (cross-manufacturer compatible; matching Blade+Grip grants bonus!)
# ---------------------------------------------------------------------------

GRIPS: dict[str, GripPartSpec] = {
    "Grip_Hyperion": GripPartSpec(
        part_id="Grip_Hyperion",
        manufacturer=Manufacturer.HYPERION,
        display_name="Hyperion Ergonomic Hilt",
        modifiers=(
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, 0.08),
            StatModifier(StatId.RANGE_UU, ModifierMode.SCALE, 0.06),
            StatModifier(StatId.CRIT_BONUS, ModifierMode.POST_ADD, 0.15),
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.07),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Hyperion",
            donor_fragment="Pistol_Grip_Hyperion",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Hyperion",
    ),
    "Grip_Bandit": GripPartSpec(
        part_id="Grip_Bandit",
        manufacturer=Manufacturer.BANDIT,
        display_name="Bandit Wrapped Tang",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.08),
            StatModifier(StatId.CLEAVE_ARC, ModifierMode.POST_ADD, 10.0),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.06),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Bandit",
            donor_fragment="Pistol_Grip_Bandit",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Bandit",
    ),
    "Grip_Jakobs": GripPartSpec(
        part_id="Grip_Jakobs",
        manufacturer=Manufacturer.JAKOBS,
        display_name="Jakobs Rosewood Hilt",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.12),
            StatModifier(StatId.CRIT_BONUS, ModifierMode.POST_ADD, 0.20),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.09),
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, -0.10),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Jakobs",
            donor_fragment="Pistol_Grip_Jakobs",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Jakobs",
    ),
    "Grip_Maliwan": GripPartSpec(
        part_id="Grip_Maliwan",
        manufacturer=Manufacturer.MALIWAN,
        display_name="Maliwan Conduit Hilt",
        modifiers=(
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.20),
            StatModifier(StatId.STATUS_DAMAGE, ModifierMode.SCALE, 0.15),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, 0.05),
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.05),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Maliwan",
            donor_fragment="Pistol_Grip_Maliwan",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Maliwan",
    ),
    "Grip_Torgue": GripPartSpec(
        part_id="Grip_Torgue",
        manufacturer=Manufacturer.TORGUE,
        display_name="Torgue Piston Hilt",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.14),
            StatModifier(StatId.MOMENTUM, ModifierMode.SCALE, 0.25),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.10),
            StatModifier(StatId.RANGE_UU, ModifierMode.SCALE, -0.04),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Grip_Torgue",
            donor_fragment="Pistol_Grip_Torgue",
        ),
        donor_part_path="GD_Weap_Pistol.Grip.Pistol_Grip_Torgue",
    ),
}


# ---------------------------------------------------------------------------
# 4. Accessories (optional 3rd/4th part; grants prefixes & focused perks)
# ---------------------------------------------------------------------------

ACCESSORIES: dict[str, AccessoryPartSpec] = {
    "Acc_None": AccessoryPartSpec(
        part_id="Acc_None",
        display_name="No Accessory",
        prefix_by_manufacturer={},
        default_prefix="",
        modifiers=(),
        gestalt=None,
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_None",
        min_rarity=Rarity.COMMON,
        weight=1.5,
    ),
    "Acc_Serrated": AccessoryPartSpec(
        part_id="Acc_Serrated",
        display_name="Serrated Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Lacerating",
            Manufacturer.BANDIT: "Jaggid",
            Manufacturer.JAKOBS: "Vicious",
            Manufacturer.MALIWAN: "Rending",
            Manufacturer.TORGUE: "Shredding",
        },
        default_prefix="Serrated",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.16),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_Serrated",
            donor_fragment="Acc_Barrel_Blade1",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Bayonet_1",
        min_rarity=Rarity.COMMON,
        weight=1.0,
    ),
    "Acc_Counterweight": AccessoryPartSpec(
        part_id="Acc_Counterweight",
        display_name="Balanced Pommel",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Dynamic",
            Manufacturer.BANDIT: "Twitchy",
            Manufacturer.JAKOBS: "Nimble",
            Manufacturer.MALIWAN: "Harmonic",
            Manufacturer.TORGUE: "Turbo",
        },
        default_prefix="Swift",
        modifiers=(
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, 0.18),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_Counterweight",
            donor_fragment="Acc_Grip_Stock",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Stock_Stability",
        min_rarity=Rarity.COMMON,
        weight=1.0,
    ),
    "Acc_ExtendedTang": AccessoryPartSpec(
        part_id="Acc_ExtendedTang",
        display_name="Extended Blade Fuller",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Long-Reach",
            Manufacturer.BANDIT: "Longg",
            Manufacturer.JAKOBS: "Lunging",
            Manufacturer.MALIWAN: "Projected",
            Manufacturer.TORGUE: "Colossal",
        },
        default_prefix="Extended",
        modifiers=(
            StatModifier(StatId.RANGE_UU, ModifierMode.SCALE, 0.22),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.05),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_ExtendedTang",
            donor_fragment="Acc_Barrel_Blade2",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Bayonet_2",
        min_rarity=Rarity.UNCOMMON,
        weight=1.0,
    ),
    "Acc_RazorFuller": AccessoryPartSpec(
        part_id="Acc_RazorFuller",
        display_name="Monofilament Honing",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Auditing",
            Manufacturer.JAKOBS: "Deadeye",
        },
        default_prefix="Keen",
        modifiers=(
            StatModifier(StatId.CRIT_BONUS, ModifierMode.POST_ADD, 0.45),
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.06),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_RazorFuller",
            donor_fragment="Acc_Barrel_Laser",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Laser_Accuracy",
        min_rarity=Rarity.UNCOMMON,
        single_target_only=True,
        weight=1.0,
    ),
    "Acc_BroadSweep": AccessoryPartSpec(
        part_id="Acc_BroadSweep",
        display_name="Flared Cleave Guard",
        prefix_by_manufacturer={
            Manufacturer.BANDIT: "Whirlwind",
            Manufacturer.MALIWAN: "Crescent",
            Manufacturer.TORGUE: "Shockwave",
        },
        default_prefix="Sweeping",
        modifiers=(
            StatModifier(StatId.CLEAVE_ARC, ModifierMode.POST_ADD, 25.0),
            StatModifier(StatId.RANGE_UU, ModifierMode.SCALE, 0.10),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_BroadSweep",
            donor_fragment="Acc_Barrel_Tech2",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Tech_2_Damage",
        min_rarity=Rarity.UNCOMMON,
        cleave_only=True,
        weight=1.0,
    ),
    "Acc_HeavySpine": AccessoryPartSpec(
        part_id="Acc_HeavySpine",
        display_name="Reinforced Weighted Spine",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Executive",
            Manufacturer.BANDIT: "Brutul",
            Manufacturer.JAKOBS: "Tempered",
            Manufacturer.MALIWAN: "Dense",
            Manufacturer.TORGUE: "MAX-TORQUE",
        },
        default_prefix="Heavy",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, 0.26),
            StatModifier(StatId.SWING_SPEED, ModifierMode.SCALE, -0.10),
            StatModifier(StatId.MOMENTUM, ModifierMode.SCALE, 0.30),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_HeavySpine",
            donor_fragment="Acc_Barrel_Tech1",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Tech_1_Mag",
        min_rarity=Rarity.RARE,
        weight=0.85,
    ),
    "Acc_FluxCapacitor": AccessoryPartSpec(
        part_id="Acc_FluxCapacitor",
        display_name="Overclocked Elemental Coil",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Ionized",
            Manufacturer.BANDIT: "Sizzlin",
            Manufacturer.MALIWAN: "Catalyzed",
            Manufacturer.TORGUE: "Volatile",
        },
        default_prefix="Overcharged",
        modifiers=(
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.30),
            StatModifier(StatId.STATUS_DAMAGE, ModifierMode.SCALE, 0.25),
        ),
        gestalt=GestaltFragmentMeta(
            fragment_name="ML_Acc_FluxCapacitor",
            donor_fragment="Acc_Barrel_Tech3",
        ),
        donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_Tech_3_Firerate",
        min_rarity=Rarity.UNCOMMON,
        elemental_only=True,
        weight=0.9,
    ),
}


# ---------------------------------------------------------------------------
# 5. Elements (optional/locked 4th part governed by Blade's ElementPolicy)
# ---------------------------------------------------------------------------

ELEMENTS: dict[str, ElementPartSpec] = {
    "Elem_None": ElementPartSpec(
        part_id="Elem_None",
        element=Element.NONE,
        display_name="Physical Steel",
        prefix_by_manufacturer={},
        default_prefix="",
        modifiers=(),
        dot_dps_ratio=0.0,
        base_proc_chance=0.0,
        damage_type_path="GD_Impact.DamageType.DmgType_Normal",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_None",
        weight=2.2,
    ),
    "Elem_Fire": ElementPartSpec(
        part_id="Elem_Fire",
        element=Element.FIRE,
        display_name="Incendiary Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Hot-Button",
            Manufacturer.BANDIT: "Burny",
            Manufacturer.MALIWAN: "Searing",
        },
        default_prefix="Incendiary",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.14),
        ),
        dot_dps_ratio=0.72,
        base_proc_chance=0.30,
        damage_type_path="GD_Incendiary.DamageType.DmgType_Incendiary_Impact",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Fire",
        weight=1.0,
    ),
    "Elem_Shock": ElementPartSpec(
        part_id="Elem_Shock",
        element=Element.SHOCK,
        display_name="Shock Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Voltaic",
            Manufacturer.BANDIT: "Zappy",
            Manufacturer.MALIWAN: "Arcing",
        },
        default_prefix="Shock",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.14),
        ),
        dot_dps_ratio=0.72,
        base_proc_chance=0.30,
        damage_type_path="GD_Shock.DamageType.DmgType_Shock_Impact",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Shock",
        weight=1.0,
    ),
    "Elem_Corrosive": ElementPartSpec(
        part_id="Elem_Corrosive",
        element=Element.CORROSIVE,
        display_name="Corrosive Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Caustic",
            Manufacturer.BANDIT: "Assid",
            Manufacturer.MALIWAN: "Vitriolic",
        },
        default_prefix="Corrosive",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.14),
        ),
        dot_dps_ratio=0.72,
        base_proc_chance=0.30,
        damage_type_path="GD_Corrosive.DamageType.DmgType_Corrosive_Impact",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Corrosive",
        weight=1.0,
    ),
    "Elem_Slag": ElementPartSpec(
        part_id="Elem_Slag",
        element=Element.SLAG,
        display_name="Slag Edge",
        prefix_by_manufacturer={
            Manufacturer.HYPERION: "Slagging",
            Manufacturer.BANDIT: "Slaggy",
            Manufacturer.MALIWAN: "Transmuting",
        },
        default_prefix="Slag",
        modifiers=(
            StatModifier(StatId.BASE_DAMAGE, ModifierMode.SCALE, -0.16),
            StatModifier(StatId.STATUS_CHANCE, ModifierMode.SCALE, 0.15),
        ),
        dot_dps_ratio=0.25,
        base_proc_chance=0.36,
        damage_type_path="GD_Amp.DamageType.DmgType_Amp_Impact",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Slag",
        weight=0.8,
    ),
    "Elem_Explosive": ElementPartSpec(
        part_id="Elem_Explosive",
        element=Element.EXPLOSIVE,
        display_name="Explosive Charge",
        prefix_by_manufacturer={
            Manufacturer.TORGUE: "BLOW-UP",
        },
        default_prefix="Explosive",
        modifiers=(),  # Torgue Blast-Cleaver's base_damage_mult already budgets explosive cleave + splash
        dot_dps_ratio=0.0,
        base_proc_chance=1.0,  # Every swing detonates explosive splash on impact
        damage_type_path="GD_Explosive.DamageType.DmgType_Explosive_Impact_ForceFlinch",
        donor_part_path="GD_Weap_Pistol.elemental.Pistol_Elemental_Fire",
        weight=1.0,
    ),
}


BLADE_BY_MANUFACTURER: dict[Manufacturer, BladePartSpec] = {
    blade.manufacturer: blade for blade in BLADES.values()
}

ELEMENT_PART_BY_TYPE: dict[Element, ElementPartSpec] = {
    ep.element: ep for ep in ELEMENTS.values()
}


def resolve_weapon_name(
    blade_id: str,
    grip_id: str,
    accessory_id: str,
    element_id: str,
) -> tuple[str, str, str]:
    """Return `(prefix, title, full_name)` for a melee weapon's parts.

    Priority matches BL2 conventions:
    1. Accessory prefix (if an accessory is present)
    2. Elemental prefix (if no accessory, and element has a prefix)
    3. Empty prefix otherwise
    Title is determined by the Blade + Grip manufacturer matrix.
    """
    blade = BLADES[blade_id]
    grip = GRIPS[grip_id]
    accessory = ACCESSORIES[accessory_id]
    element = ELEMENTS[element_id]

    maker = blade.manufacturer
    title = blade.grip_titles.get(grip.manufacturer, blade.base_title)

    prefix = ""
    if accessory.part_id != "Acc_None":
        prefix = accessory.prefix_for(maker)
    elif element.element not in (Element.NONE, Element.EXPLOSIVE):
        prefix = element.prefix_for(maker)

    full_name = f"{prefix} {title}".strip() if prefix else title
    return prefix, title, full_name
