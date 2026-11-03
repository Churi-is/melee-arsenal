"""Unit tests for the `core/parts/` data layer (one module per part type).

Covers the layout itself, the single-source-of-truth derived tables, the new
`validate_catalog()` invariants, backward-compatible facades, and the fact that
the per-part roll helpers reproduce the orchestrator's seeded rolls exactly.
"""
from __future__ import annotations

import ast
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "sdk_mods") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "sdk_mods"))

from melee_arsenal.core import catalog as legacy_catalog  # noqa: E402
from melee_arsenal.core import loot_roller, schemas as legacy_schemas  # noqa: E402
from melee_arsenal.core import secondary  # noqa: E402
from melee_arsenal.core.enums import (  # noqa: E402
    Element,
    Manufacturer,
    PartSlot,
    Rarity,
    SecondaryAttack,
)
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
    PART_REGISTRIES,
    AccessoryPartSpec,
    BladePartSpec,
    ElementPartSpec,
    GripPartSpec,
    ManufacturerSpec,
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
    secondary_attack_for,
    validate_catalog,
)


class TestPartsPackageLayout(unittest.TestCase):
    def test_one_module_per_part_type(self) -> None:
        parts_dir = Path(__import__("melee_arsenal.core.parts", fromlist=["__file__"]).__file__).parent
        for name in (
            "manufacturers",
            "blades",
            "grips",
            "accessories",
            "elements",
            "weapon",
            "common",
            "validation",
        ):
            with self.subTest(module=name):
                self.assertTrue((parts_dir / f"{name}.py").is_file(), f"missing parts/{name}.py")

    def test_parts_package_imports_no_sdk(self) -> None:
        parts_dir = Path(__import__("melee_arsenal.core.parts", fromlist=["__file__"]).__file__).parent
        for path in sorted(parts_dir.glob("*.py")):
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        self.assertFalse(any(a.name.startswith("unrealsdk") for a in node.names))
                    elif isinstance(node, ast.ImportFrom):
                        self.assertFalse((node.module or "").startswith("unrealsdk"))

    def test_registry_keys_are_part_ids_and_slots_are_distinct(self) -> None:
        self.assertEqual(
            set(PART_REGISTRIES),
            {PartSlot.BLADE, PartSlot.GRIP, PartSlot.ACCESSORY, PartSlot.ELEMENT},
        )
        seen: set[str] = set()
        for slot, registry in PART_REGISTRIES.items():
            self.assertTrue(registry, f"{slot.value} registry is empty")
            for key, spec in registry.items():
                self.assertEqual(key, spec.part_id)
                self.assertNotIn(key, seen, f"duplicate part id {key!r}")
                seen.add(key)

    def test_legacy_facades_point_at_the_parts_modules(self) -> None:
        """Old import paths still work and resolve to the exact same objects."""
        for name, expected in (
            ("BLADES", BLADES),
            ("GRIPS", GRIPS),
            ("ACCESSORIES", ACCESSORIES),
            ("ELEMENTS", ELEMENTS),
            ("MANUFACTURERS", MANUFACTURERS),
            ("BLADE_BY_MANUFACTURER", BLADE_BY_MANUFACTURER),
            ("ELEMENT_PART_BY_TYPE", ELEMENT_PART_BY_TYPE),
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(legacy_catalog, name), expected)
        self.assertIs(legacy_catalog.resolve_weapon_name, resolve_weapon_name)
        for name, expected in (
            ("BladePartSpec", BladePartSpec),
            ("GripPartSpec", GripPartSpec),
            ("AccessoryPartSpec", AccessoryPartSpec),
            ("ElementPartSpec", ElementPartSpec),
            ("ManufacturerSpec", ManufacturerSpec),
        ):
            with self.subTest(schema=name):
                self.assertIs(getattr(legacy_schemas, name), expected)
        self.assertIs(secondary.secondary_attack_for, secondary_attack_for)
        self.assertIs(loot_roller.eligible_elements_for_blade, eligible_elements_for_blade)
        self.assertIs(loot_roller.eligible_accessories_for_roll, eligible_accessories_for_roll)


class TestDerivedPartData(unittest.TestCase):
    def test_validate_catalog_is_clean(self) -> None:
        self.assertEqual(validate_catalog(), [])

    def test_validate_catalog_flags_a_mismatched_entry(self) -> None:
        from dataclasses import replace

        broken = replace(BLADES["Blade_Hyperion_Katana"], part_id="Blade_Wrong_Id")
        with mock.patch.dict(BLADES, {"Blade_Hyperion_Katana": broken}):
            errors = validate_catalog()
            self.assertTrue(any("!=" in e for e in errors), errors)
        self.assertEqual(validate_catalog(), [], "catalog was not restored")

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
        self.assertIs(ACCESSORY_WEIGHT_KG, secondary.ACCESSORY_WEIGHT_KG)
        self.assertIs(ACCESSORY_GUARD_COOLDOWN_DELTA, secondary.ACCESSORY_GUARD_COOLDOWN_DELTA)
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
            {f"Grip_{m.value}": f"grip_{m.value.lower()}" for m in Manufacturer},
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

    def test_runtime_consumes_the_derived_kit_tables(self) -> None:
        """The SDK asset binder imports the part-data tables instead of copying them.

        Checked from source so this test stays pure-CPython (`runtime/__init__.py`
        needs the in-game `mods_base` module, which is not available offline).
        """
        source = (ROOT_DIR / "sdk_mods/melee_arsenal/runtime/custom_assets.py").read_text(encoding="utf-8")
        for name in (
            "BLADE_TO_KIT_ID",
            "GRIP_TO_KIT_ID",
            "ACCESSORY_TO_KIT_ID",
            "ELEMENT_TO_KIT_ID",
            "ELEMENT_ID_TO_ATLAS_KEY",
        ):
            with self.subTest(table=name):
                self.assertIn(name, source)
                self.assertNotRegex(source, rf"^{name}\s*:\s*dict\[[^]]+\]\s*=\s*\{{")


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
            [e.part_id for e in eligible_elements_for_blade("Blade_Jakobs_Sabre")],
            [NO_ELEMENT_ID],
        )
        self.assertEqual(
            [e.part_id for e in eligible_elements_for_blade("Blade_Torgue_BlastCleaver")],
            ["Elem_Explosive"],
        )
        self.assertTrue(all(
            e.element in (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG)
            for e in eligible_elements_for_blade("Blade_Maliwan_Phaseblade")
        ))
        self.assertTrue(ELEMENTS["Elem_Fire"].is_dot_element)
        self.assertFalse(ELEMENTS["Elem_Explosive"].is_dot_element)
        self.assertTrue(ELEMENTS["Elem_Explosive"].is_explosive)
        pool = eligible_accessories_for_roll("Blade_Maliwan_Phaseblade", "Elem_Fire", Rarity.VERY_RARE)
        self.assertIn("Acc_FluxCapacitor", {a.part_id for a in pool})
        self.assertNotIn("Acc_RazorFuller", {a.part_id for a in pool})

    def test_part_roll_helpers_reproduce_the_orchestrator(self) -> None:
        """Per-part helpers must consume RNG in exactly the old order."""
        for i in range(60):
            maker = tuple(Manufacturer)[i % 5]
            rarity = (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE)[i % 4]
            seed = f"parts-parity:{i}"
            rng = random.Random(seed)
            blade = blade_for_manufacturer(maker)
            grip_id = roll_grip_id(rng)
            element_id = roll_element_id(rng, blade.part_id)
            accessory_id = roll_accessory_id(rng, blade.part_id, element_id, rarity)
            uid = rng.randint(1, 2_147_483_647)

            expected = MeleeWeaponParts(blade.part_id, grip_id, accessory_id, element_id)
            actual = loot_roller.roll_melee_weapon(
                game_stage=1 + i, rarity=rarity, manufacturer=maker, seed=seed, unique_id=uid
            )
            self.assertEqual(actual.parts, expected, f"seed {seed!r} diverged")

    def test_adding_an_accessory_needs_only_one_registry_entry(self) -> None:
        """Add a spec to ACCESSORIES and eligibility/rolls pick it up automatically."""
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
        # With a single legal accessory it must be the one rolled (VERY_RARE = 100% chance).
        with mock.patch.dict(ACCESSORIES, {extra.part_id: extra}, clear=True):
            rng = random.Random("one-pool")
            self.assertEqual(
                roll_accessory_id(rng, "Blade_Hyperion_Katana", NO_ELEMENT_ID, Rarity.VERY_RARE),
                extra.part_id,
            )
        self.assertNotIn(extra.part_id, ACCESSORIES)

    def test_manufacturer_logic_lives_with_manufacturer_data(self) -> None:
        self.assertEqual(secondary_attack_for(Manufacturer.TORGUE), SecondaryAttack.THROW)
        self.assertEqual(secondary_attack_for(Manufacturer.MALIWAN), SecondaryAttack.NONE)
        self.assertIn(Element.EXPLOSIVE, MANUFACTURERS[Manufacturer.TORGUE].allowed_elements)
        self.assertEqual(
            MANUFACTURERS[Manufacturer.JAKOBS].allowed_elements, (Element.NONE,)
        )


if __name__ == "__main__":
    unittest.main()
