"""Blade parts — the defining part of every Melee Weapon.

A Blade chooses the Manufacturer (and therefore the archetype's damage, cadence,
elemental policy, targeting profile and secondary attack), owns the Blade+Grip
title matrix, and carries the mesh/donor metadata the SDK side uses to construct
its UE3 part. Adding a blade is a single `BladePartSpec(...)` entry here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..enums import Manufacturer
from .common import GestaltFragmentMeta, StatModifier


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
    # Matching InkBlade kit fragment id (`tools/inkblade_gen`); None = no custom mesh.
    kit_id: str | None = None

    def title_for(self, grip_manufacturer: Manufacturer) -> str:
        """Blade+Grip title matrix; falls back to the matching-grip title."""
        return self.grip_titles.get(grip_manufacturer, self.base_title)


# Registry: one entry per Blade (one per Manufacturer archetype)

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
        kit_id="blade_hyperion",
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
        kit_id="blade_bandit",
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
        kit_id="blade_jakobs",
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
        kit_id="blade_maliwan",
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
        kit_id="blade_torgue",
    ),
}

BLADE_BY_MANUFACTURER: dict[Manufacturer, BladePartSpec] = {
    blade.manufacturer: blade for blade in BLADES.values()
}
