"""Tests for part data integrity, eligibility, and seeded rolls."""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "sdk_mods") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "sdk_mods"))

from melee_arsenal.core import loot_roller  # noqa: E402
from melee_arsenal.core import secondary  # noqa: E402
from melee_arsenal.core.enums import Element, Manufacturer, PartSlot, Rarity  # noqa: E402
from melee_arsenal.core.parts import (  # noqa: E402
    ACCESSORIES,
    ACCESSORY_GUARD_COOLDOWN_DELTA,
    ACCESSORY_TO_KIT_ID,
    ACCESSORY_WEIGHT_KG,
    BLADE_BY_MANUFACTURER,
    BLADE_TO_KIT_ID,
    BLADES,
    ELEMENT_ID_TO_ATLAS_KEY,
    ELEMENT_PART_BY_TYPE,
    ELEMENT_TO_KIT_ID,
    ELEMENTS,
    GRIP_TO_KIT_ID,
    GRIPS,
    GUARD_COOLDOWN_BY_MANUFACTURER,
    MANUFACTURERS,
    NO_ACCESSORY_ID,
    NO_ELEMENT_ID,
    AccessoryPartSpec,
    MeleeWeaponParts,
    blade_for_manufacturer,
    eligible_accessories_for_roll,
    eligible_elements_for_blade,
    is_matching_grip,
    resolve_parts,
    resolve_weapon_name,
    roll_accessory_id,
    roll_element_id,
    roll_grip_id,
)

# The test owns this inventory so core SDK files need no catalog-wide validator.
PART_REGISTRIES = {
    PartSlot.BLADE: BLADES,
    PartSlot.GRIP: GRIPS,
    PartSlot.ACCESSORY: ACCESSORIES,
    PartSlot.ELEMENT: ELEMENTS,
}


class TestPartDataIntegrity(unittest.TestCase):
    def test_every_part_has_a_unique_id_and_valid_asset_data(self) -> None:
        seen_ids: set[str] = set()
        seen_fragments: set[str] = set()
        seen_kit_ids: set[str] = set()

        for slot, registry in PART_REGISTRIES.items():
            self.assertTrue(registry, f"{slot.value} registry is empty")
            for key, spec in registry.items():
                with self.subTest(slot=slot.value, part=key):
                    self.assertEqual(key, spec.part_id)
                    self.assertNotIn(key, seen_ids, f"duplicate part id {key!r}")
                    self.assertTrue(spec.donor_part_path)
                    weight = getattr(spec, "weight", None)
                    if weight is not None:
                        self.assertGreater(weight, 0)

                    gestalt = getattr(spec, "gestalt", None)
                    fragment = getattr(gestalt, "fragment_name", None)
                    if fragment:
                        self.assertNotIn(fragment, seen_fragments)
                        seen_fragments.add(fragment)
                    kit_id = getattr(spec, "kit_id", None)
                    if kit_id:
                        self.assertNotIn(kit_id, seen_kit_ids)
                        seen_kit_ids.add(kit_id)
                    seen_ids.add(key)

    def test_manufacturer_and_element_indexes_match_the_specs(self) -> None:
        self.assertEqual(set(MANUFACTURERS), set(Manufacturer))
        self.assertEqual(set(BLADE_BY_MANUFACTURER), set(Manufacturer))
        for maker in Manufacturer:
            with self.subTest(manufacturer=maker.value):
                manufacturer = MANUFACTURERS[maker]
                self.assertIs(manufacturer.manufacturer, maker)
                self.assertTrue(manufacturer.donor_manufacturer_path)
                blade = BLADE_BY_MANUFACTURER[maker]
                self.assertIs(BLADES.get(blade.part_id), blade)
                self.assertIs(blade.manufacturer, maker)
                for element in manufacturer.allowed_elements:
                    self.assertIn(element, ELEMENT_PART_BY_TYPE)

        for blade_id, blade in BLADES.items():
            with self.subTest(blade=blade_id):
                self.assertIn(blade.manufacturer, MANUFACTURERS)
                self.assertTrue(eligible_elements_for_blade(blade_id))

        self.assertEqual(set(ELEMENT_PART_BY_TYPE), set(Element))
        for element, spec in ELEMENT_PART_BY_TYPE.items():
            with self.subTest(element=element.value):
                self.assertIs(ELEMENTS.get(spec.part_id), spec)
                self.assertIs(spec.element, element)


class TestDerivedPartData(unittest.TestCase):
    def test_accessory_mass_and_guard_delta_come_from_the_specs(self) -> None:
        expected_weight = {
            "Acc_None": 0.0,
            "Acc_Serrated": 0.10,
            "Acc_Counterweight": 0.18,
            "Acc_ExtendedTang": 0.15,
            "Acc_RazorFuller": 0.08,
            "Acc_BroadSweep": 0.22,
            "Acc_HeavySpine": 0.32,
            "Acc_FluxCapacitor": 0.20,
        }
        expected_delta = {
            "Acc_None": 0.0,
            "Acc_Serrated": 0.0,
            "Acc_Counterweight": -0.10,
            "Acc_ExtendedTang": 0.05,
            "Acc_RazorFuller": 0.0,
            "Acc_BroadSweep": 0.10,
            "Acc_HeavySpine": 0.25,
            "Acc_FluxCapacitor": 0.05,
        }
        self.assertEqual(ACCESSORY_WEIGHT_KG, expected_weight)
        self.assertEqual(ACCESSORY_GUARD_COOLDOWN_DELTA, expected_delta)
        self.assertIs(secondary.ACCESSORY_WEIGHT_KG, ACCESSORY_WEIGHT_KG)
        self.assertIs(secondary.ACCESSORY_GUARD_COOLDOWN_DELTA, ACCESSORY_GUARD_COOLDOWN_DELTA)
        for part_id, spec in ACCESSORIES.items():
            self.assertEqual(spec.throw_weight_kg, expected_weight[part_id])
            self.assertEqual(spec.guard_cooldown_delta_sec, expected_delta[part_id])

    def test_guard_cooldowns_come_from_manufacturer_specs(self) -> None:
        self.assertEqual(
            GUARD_COOLDOWN_BY_MANUFACTURER,
            {Manufacturer.HYPERION: 1.60, Manufacturer.JAKOBS: 2.20},
        )
        self.assertIs(secondary.GUARD_COOLDOWN_BY_MANUFACTURER, GUARD_COOLDOWN_BY_MANUFACTURER)
        self.assertEqual(MANUFACTURERS[Manufacturer.MALIWAN].guard_cooldown_sec, 2.0)

    def test_kit_and_atlas_ids_come_from_the_specs(self) -> None:
        self.assertEqual(
            BLADE_TO_KIT_ID,
            {
                "Blade_Hyperion_Katana": "blade_hyperion",
                "Blade_Bandit_Machete": "blade_bandit",
                "Blade_Jakobs_Sabre": "blade_jakobs",
                "Blade_Maliwan_Phaseblade": "blade_maliwan",
                "Blade_Torgue_BlastCleaver": "blade_torgue",
            },
        )
        self.assertEqual(
            GRIP_TO_KIT_ID,
            {f"Grip_{maker.value}": f"grip_{maker.value.lower()}" for maker in Manufacturer},
        )
        self.assertEqual(
            ACCESSORY_TO_KIT_ID,
            {
                "Acc_None": None,
                "Acc_Serrated": "accessory_serrated_edge",
                "Acc_Counterweight": "accessory_balanced_pommel",
                "Acc_ExtendedTang": "accessory_extended_blade_fuller",
                "Acc_RazorFuller": "accessory_monofilament_honing",
                "Acc_BroadSweep": "accessory_flared_cleave_guard",
                "Acc_HeavySpine": "accessory_reinforced_heavy_spine",
                "Acc_FluxCapacitor": "accessory_overclocked_elemental_coil",
            },
        )
        self.assertEqual(
            ELEMENT_TO_KIT_ID,
            {
                "Elem_None": None,
                "Elem_Fire": "element_fire",
                "Elem_Shock": "element_shock",
                "Elem_Corrosive": "element_corrosive",
                "Elem_Slag": "element_slag",
                "Elem_Explosive": "element_explosive",
            },
        )
        self.assertEqual(
            ELEMENT_ID_TO_ATLAS_KEY,
            {
                "Elem_None": "None",
                "Elem_Fire": "Fire",
                "Elem_Shock": "Shock",
                "Elem_Corrosive": "Corrosive",
                "Elem_Slag": "Slag",
                "Elem_Explosive": "Explosive",
            },
        )


class TestPartDataLogic(unittest.TestCase):
    def test_resolve_parts_and_matching_grip(self) -> None:
        parts = MeleeWeaponParts(
            "Blade_Hyperion_Katana", "Grip_Hyperion", NO_ACCESSORY_ID, "Elem_Fire"
        )
        resolved = resolve_parts(parts)
        self.assertIs(resolved.blade, BLADES[parts.blade_id])
        self.assertIs(resolved.grip, GRIPS[parts.grip_id])
        self.assertIs(resolved.accessory, ACCESSORIES[NO_ACCESSORY_ID])
        self.assertIs(resolved.element, ELEMENTS["Elem_Fire"])
        self.assertIs(resolved.manufacturer, MANUFACTURERS[Manufacturer.HYPERION])
        self.assertTrue(is_matching_grip(resolved.blade, resolved.grip))
        self.assertFalse(is_matching_grip(resolved.blade, GRIPS["Grip_Torgue"]))
        self.assertEqual(parts.active_part_count, 3)
        self.assertEqual(
            MeleeWeaponParts("Blade_Hyperion_Katana", "Grip_Hyperion", NO_ACCESSORY_ID, NO_ELEMENT_ID).active_part_count,
            2,
        )

    def test_name_resolution_uses_blade_titles_and_part_prefixes(self) -> None:
        self.assertEqual(
            resolve_weapon_name(
                "Blade_Hyperion_Katana", "Grip_Jakobs", NO_ACCESSORY_ID, "Elem_Fire"
            ),
            ("Hot-Button", "Kensei", "Hot-Button Kensei"),
        )
        self.assertEqual(
            resolve_weapon_name(
                "Blade_Hyperion_Katana", "Grip_Jakobs", "Acc_Serrated", "Elem_Fire"
            ),
            ("Lacerating", "Kensei", "Lacerating Kensei"),
        )
        self.assertEqual(BLADES["Blade_Hyperion_Katana"].title_for(Manufacturer.JAKOBS), "Kensei")

    def test_element_policy_and_accessory_eligibility(self) -> None:
        self.assertEqual(
            [element.part_id for element in eligible_elements_for_blade("Blade_Jakobs_Sabre")],
            [NO_ELEMENT_ID],
        )
        self.assertEqual(
            [element.part_id for element in eligible_elements_for_blade("Blade_Torgue_BlastCleaver")],
            ["Elem_Explosive"],
        )
        self.assertTrue(
            all(
                element.element in (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG)
                for element in eligible_elements_for_blade("Blade_Maliwan_Phaseblade")
            )
        )
        self.assertTrue(ELEMENTS["Elem_Fire"].is_dot_element)
        self.assertFalse(ELEMENTS["Elem_Explosive"].is_dot_element)
        self.assertTrue(ELEMENTS["Elem_Explosive"].is_explosive)
        pool = eligible_accessories_for_roll(
            "Blade_Maliwan_Phaseblade", "Elem_Fire", Rarity.VERY_RARE
        )
        pool_ids = {accessory.part_id for accessory in pool}
        self.assertIn("Acc_FluxCapacitor", pool_ids)
        self.assertNotIn("Acc_RazorFuller", pool_ids)

    def test_part_roll_helpers_preserve_the_seeded_roll_sequence(self) -> None:
        for index in range(60):
            maker = tuple(Manufacturer)[index % len(Manufacturer)]
            rarity = (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE)[index % 4]
            seed = f"parts-parity:{index}"
            rng = random.Random(seed)
            blade = blade_for_manufacturer(maker)
            grip_id = roll_grip_id(rng)
            element_id = roll_element_id(rng, blade.part_id)
            accessory_id = roll_accessory_id(rng, blade.part_id, element_id, rarity)
            expected = MeleeWeaponParts(blade.part_id, grip_id, accessory_id, element_id)
            unique_id = rng.randint(1, 2_147_483_647)

            actual = loot_roller.roll_melee_weapon(
                game_stage=1 + index,
                rarity=rarity,
                manufacturer=maker,
                seed=seed,
                unique_id=unique_id,
            )
            self.assertEqual(actual.parts, expected, f"seed {seed!r} diverged")

    def test_added_accessory_is_available_to_eligibility_and_rolls(self) -> None:
        extra = AccessoryPartSpec(
            part_id="Acc_TestOnly",
            display_name="Test-Only Hilt",
            prefix_by_manufacturer={},
            default_prefix="Test",
            modifiers=(),
            gestalt=None,
            donor_part_path="GD_Weap_Pistol.Accessory.Pistol_Accessory_None",
            min_rarity=Rarity.COMMON,
            weight=1.0,
            throw_weight_kg=0.11,
            guard_cooldown_delta_sec=-0.01,
            kit_id="accessory_test_only",
        )
        with mock.patch.dict(ACCESSORIES, {extra.part_id: extra}):
            pool = eligible_accessories_for_roll(
                "Blade_Hyperion_Katana", NO_ELEMENT_ID, Rarity.VERY_RARE
            )
            self.assertIn(extra, pool)
        with mock.patch.dict(ACCESSORIES, {extra.part_id: extra}, clear=True):
            rng = random.Random("one-pool")
            self.assertEqual(
                roll_accessory_id(rng, "Blade_Hyperion_Katana", NO_ELEMENT_ID, Rarity.VERY_RARE),
                extra.part_id,
            )
        self.assertNotIn(extra.part_id, ACCESSORIES)


if __name__ == "__main__":
    unittest.main()
