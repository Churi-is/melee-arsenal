"""Comprehensive offline CPython unit tests for `melee_arsenal.core`."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "sdk_mods") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "sdk_mods"))

from melee_arsenal.core import (  # noqa: E402
    BLADES,
    DAMAGE_REDUCTION_MAX_PCT,
    DAMAGE_REDUCTION_MIN_PCT,
    ELEMENTS,
    GRIPS,
    MANUFACTURERS,
    CompareArrow,
    Element,
    Manufacturer,
    MeleeWeaponInstance,
    MeleeWeaponParts,
    PlayerMeleeContext,
    Rarity,
    Rotator,
    TargetCandidate,
    TargetingMode,
    Vec3,
    apply_incoming_damage_reduction,
    build_asset_sublibrary_manifest,
    build_item_card_payload,
    compute_attack_damage,
    compute_card_stats,
    compute_damage_reduction_pct,
    deserialize_weapon,
    plan_swing_strikes,
    read_save_records,
    roll_melee_weapon,
    save_record_path,
    serialize_weapon,
    validate_weapon_parts,
    write_save_records_atomic,
)


class TestMeleeArsenalCore(unittest.TestCase):
    def test_manufacturer_roster_and_element_policies(self) -> None:
        self.assertEqual(len(MANUFACTURERS), 5)
        self.assertEqual(len(BLADES), 5)
        self.assertEqual(len(GRIPS), 5)

        # Roll 250 weapons across all manufacturers & verify 0 illegal element/accessory combos
        for i in range(250):
            maker = tuple(Manufacturer)[i % 5]
            rarity = (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE)[i % 4]
            wpn = roll_melee_weapon(
                game_stage=1 + (i % 80),
                rarity=rarity,
                manufacturer=maker,
                seed=f"test:{i}",
                unique_id=1000 + i,
            )
            errors = validate_weapon_parts(wpn.parts)
            self.assertEqual(errors, [], f"Validation failed on roll {i}: {errors}")

            elem = ELEMENTS[wpn.parts.element_id].element
            if maker == Manufacturer.JAKOBS:
                self.assertEqual(elem, Element.NONE)
            elif maker == Manufacturer.MALIWAN:
                self.assertIn(elem, (Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG))
            elif maker == Manufacturer.TORGUE:
                self.assertEqual(elem, Element.EXPLOSIVE)
            elif maker == Manufacturer.BANDIT:
                self.assertIn(elem, (Element.NONE, Element.FIRE, Element.CORROSIVE, Element.SLAG))
            elif maker == Manufacturer.HYPERION:
                self.assertIn(
                    elem,
                    (Element.NONE, Element.FIRE, Element.SHOCK, Element.CORROSIVE, Element.SLAG),
                )

    def test_gun_scaled_card_damage_and_matching_grip_bonus(self) -> None:
        mismatched = MeleeWeaponInstance(
            unique_id=1,
            game_stage=50,
            rarity=Rarity.VERY_RARE,
            parts=MeleeWeaponParts(
                blade_id="Blade_Hyperion_Katana",
                grip_id="Grip_Maliwan",
                accessory_id="Acc_Counterweight",
                element_id="Elem_Shock",
            ),
        )
        matched = MeleeWeaponInstance(
            unique_id=2,
            game_stage=50,
            rarity=Rarity.VERY_RARE,
            parts=MeleeWeaponParts(
                blade_id="Blade_Hyperion_Katana",
                grip_id="Grip_Hyperion",
                accessory_id="Acc_Counterweight",
                element_id="Elem_Shock",
            ),
        )
        s_mis = compute_card_stats(mismatched)
        s_mat = compute_card_stats(matched)

        self.assertFalse(s_mis.matching_grip)
        self.assertTrue(s_mat.matching_grip)
        self.assertGreater(s_mat.swing_speed, s_mis.swing_speed)
        self.assertGreater(s_mat.card_damage, 10_000)  # True Lv50 BL2 gun-scale integer damage!

        # Level 1 vs Level 50 vs Level 80 exponential 1.13^L scaling
        lv1 = compute_card_stats(
            MeleeWeaponInstance(3, 1, Rarity.COMMON, matched.parts)
        )
        lv80 = compute_card_stats(
            MeleeWeaponInstance(4, 80, Rarity.VERY_RARE, matched.parts)
        )
        self.assertLess(lv1.card_damage, 200)
        self.assertGreater(lv80.card_damage, 500_000)

    def test_player_melee_and_roid_stack_on_weapon_base_damage(self) -> None:
        katana = compute_card_stats(
            MeleeWeaponInstance(
                unique_id=10,
                game_stage=50,
                rarity=Rarity.RARE,
                parts=MeleeWeaponParts(
                    blade_id="Blade_Jakobs_Sabre",
                    grip_id="Grip_Jakobs",
                    accessory_id="Acc_RazorFuller",
                    element_id="Elem_None",
                ),
            )
        )
        base_hit = compute_attack_damage(katana, PlayerMeleeContext())
        self.assertEqual(base_hit.final_hit_damage, katana.card_damage)

        # Stack +150% player melee skill bonus + 15,000 Roid shield + Critical Hit + UVHM Slag
        buffed_ctx = PlayerMeleeContext(
            melee_damage_scale=2.5,
            roid_flat_damage=15_000.0,
            is_uvhm=True,
        )
        buffed_hit = compute_attack_damage(
            katana,
            buffed_ctx,
            is_crit=True,
            target_is_slagged=True,
        )
        self.assertGreater(buffed_hit.roid_component, 0.0)
        self.assertEqual(buffed_hit.player_melee_multiplier, 2.5)
        self.assertGreater(buffed_hit.crit_multiplier, 4.0)  # Jakobs +100% + grip + razor fuller!
        self.assertEqual(buffed_hit.slag_multiplier, 3.0)
        self.assertGreater(buffed_hit.final_hit_damage, base_hit.final_hit_damage * 25)

    def test_single_target_vs_frontal_cleave_geometry(self) -> None:
        eye = Vec3(0.0, 0.0, 64.0)
        rot = Rotator.from_degrees(0.0, 0.0)  # Facing +X

        enemies = [
            TargetCandidate("front_center", Vec3(200.0, 0.0, 40.0), collision_radius=40.0),
            TargetCandidate("front_left_35deg", Vec3(180.0, 126.0, 40.0), collision_radius=40.0),
            TargetCandidate("front_right_40deg", Vec3(170.0, -142.0, 40.0), collision_radius=40.0),
            TargetCandidate("behind_player", Vec3(-150.0, 0.0, 40.0), collision_radius=40.0),
            TargetCandidate("out_of_range", Vec3(600.0, 0.0, 40.0), collision_radius=40.0),
        ]

        # Single-target Hyperion Katana: only strikes 1 target near crosshair
        katana_stats = compute_card_stats(
            MeleeWeaponInstance(
                unique_id=20,
                game_stage=30,
                rarity=Rarity.RARE,
                parts=MeleeWeaponParts(
                    "Blade_Hyperion_Katana", "Grip_Hyperion", "Acc_None", "Elem_None"
                ),
            )
        )
        st_plan = plan_swing_strikes(eye, rot, katana_stats, enemies)
        self.assertEqual(st_plan.mode, TargetingMode.SINGLE_TARGET)
        self.assertEqual(len(st_plan.rays), 1)
        self.assertEqual(st_plan.matched_target_ids, ("front_center",))

        # Frontal-cleave Bandit Machete (95° + 10° Bandit grip = 105° arc, +-52.5°):
        # hits front_center, front_left_35deg, AND front_right_40deg, ignoring behind & out-of-range!
        machete_stats = compute_card_stats(
            MeleeWeaponInstance(
                unique_id=21,
                game_stage=30,
                rarity=Rarity.RARE,
                parts=MeleeWeaponParts(
                    "Blade_Bandit_Machete", "Grip_Bandit", "Acc_None", "Elem_Fire"
                ),
            )
        )
        cleave_plan = plan_swing_strikes(eye, rot, machete_stats, enemies)
        self.assertEqual(cleave_plan.mode, TargetingMode.FRONTAL_CLEAVE)
        self.assertEqual(
            set(cleave_plan.matched_target_ids),
            {"front_center", "front_left_35deg", "front_right_40deg"},
        )
        self.assertEqual(len(cleave_plan.rays), 3)

    def test_item_card_payload_and_elemental_toggle(self) -> None:
        jakobs = compute_card_stats(
            MeleeWeaponInstance(
                unique_id=30,
                game_stage=40,
                rarity=Rarity.RARE,
                parts=MeleeWeaponParts(
                    "Blade_Jakobs_Sabre", "Grip_Jakobs", "Acc_Serrated", "Elem_None"
                ),
            )
        )
        maliwan = compute_card_stats(
            MeleeWeaponInstance(
                unique_id=31,
                game_stage=40,
                rarity=Rarity.VERY_RARE,
                parts=MeleeWeaponParts(
                    "Blade_Maliwan_Phaseblade", "Grip_Maliwan", "Acc_FluxCapacitor", "Elem_Corrosive"
                ),
            )
        )

        card_j = build_item_card_payload(jakobs)
        self.assertIsNone(card_j.elemental_section)
        visible_j = [r.label for r in card_j.top_stats if r.visible]
        self.assertEqual(visible_j, ["Damage", "Range", "Swing Speed"])

        # The old green "Stacks with Player Melee & Roid Bonuses" line clipped into the manufacturer
        # logo and repeated what every card already implies — it must be gone from every card.
        for payload in (card_j, build_item_card_payload(maliwan)):
            self.assertNotIn("Stacks with Player Melee", payload.fun_stats_html)
            self.assertNotIn("Roid Bonuses", payload.fun_stats_html)
            self.assertNotIn("#9be7a8", payload.fun_stats_html)
            # ...and the rolled Damage Reduction is advertised instead (kept short so it never clips)
            self.assertTrue(payload.fun_stats_lines[0].startswith("<font color='#7fd4ff'>Damage Reduction "))
            self.assertIn("(Equipped)</font>", payload.fun_stats_lines[0])

        card_m = build_item_card_payload(maliwan, compared_against=jakobs)
        self.assertIsNotNone(card_m.elemental_section)
        assert card_m.elemental_section is not None
        self.assertEqual(card_m.elemental_section.element, Element.CORROSIVE)
        # Jakobs has higher per-hit damage, Maliwan has longer range than Sabre (3.25m vs 3.10m)
        self.assertEqual(card_m.top_stats[0].arrow, CompareArrow.WORSE)
        self.assertEqual(card_m.top_stats[1].arrow, CompareArrow.BETTER)

    def test_every_melee_weapon_rolls_50_to_70_damage_reduction(self) -> None:
        self.assertEqual((DAMAGE_REDUCTION_MIN_PCT, DAMAGE_REDUCTION_MAX_PCT), (50, 70))

        seen_values: set[int] = set()
        for i in range(400):
            maker = tuple(Manufacturer)[i % 5]
            rarity = (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE)[i % 4]
            wpn = roll_melee_weapon(
                game_stage=1 + (i % 80),
                rarity=rarity,
                manufacturer=maker,
                seed=f"dr:{i}",
                unique_id=2000 + i,
            )
            pct = compute_card_stats(wpn).damage_reduction_pct
            self.assertTrue(
                DAMAGE_REDUCTION_MIN_PCT <= pct <= DAMAGE_REDUCTION_MAX_PCT,
                f"{wpn.parts.blade_id} rolled {pct}% damage reduction (must be 50-70%)",
            )
            seen_values.add(pct)

            # Deterministic: the same weapon always reports (and applies) the same reduction, so the
            # card, `melee_dump_held`, and the live `TakeDamage` hook can never disagree.
            self.assertEqual(pct, compute_damage_reduction_pct(wpn))
            self.assertEqual(pct, compute_card_stats(wpn).damage_reduction_pct)

            # ...even when BL2 hands back a zeroed `UniqueId` / reset `GameStage` for a re-assembled
            # item (the same drift `persistence` level-repair guards against).
            corrupted = MeleeWeaponInstance(
                unique_id=0, game_stage=1, rarity=wpn.rarity, parts=wpn.parts
            )
            self.assertEqual(pct, compute_card_stats(corrupted).damage_reduction_pct)

        # Every manufacturer must be represented across the full 50-70% band
        self.assertEqual(len(seen_values), DAMAGE_REDUCTION_MAX_PCT - DAMAGE_REDUCTION_MIN_PCT + 1)

    def test_incoming_damage_reduction_math(self) -> None:
        self.assertAlmostEqual(apply_incoming_damage_reduction(1000.0, 50), 500.0)
        self.assertAlmostEqual(apply_incoming_damage_reduction(1000.0, 70), 300.0)
        self.assertAlmostEqual(apply_incoming_damage_reduction(1000.0, 62.5), 375.0)
        # Clamped: never heals, never amplifies, never goes negative
        self.assertAlmostEqual(apply_incoming_damage_reduction(1000.0, 0), 1000.0)
        self.assertAlmostEqual(apply_incoming_damage_reduction(1000.0, -25), 1000.0)
        self.assertAlmostEqual(apply_incoming_damage_reduction(1000.0, 100), 0.0)
        self.assertAlmostEqual(apply_incoming_damage_reduction(1000.0, 250), 0.0)
        self.assertAlmostEqual(apply_incoming_damage_reduction(0.0, 60), 0.0)
        self.assertAlmostEqual(apply_incoming_damage_reduction(-500.0, 60), -500.0)

    def test_sidecar_persistence_and_level_repair(self) -> None:
        wpn = roll_melee_weapon(
            game_stage=61,
            rarity=Rarity.VERY_RARE,
            manufacturer=Manufacturer.TORGUE,
            seed="persist-1",
            unique_id=987654,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            target = save_record_path(Path(tmpdir), "Save0001.sav")
            rec = serialize_weapon(wpn)
            write_save_records_atomic(target, {str(wpn.unique_id): rec})

            # Simulate BL2 resetting weapon GameStage to 1 before save hook runs
            corrupted_wpn = MeleeWeaponInstance(
                unique_id=wpn.unique_id,
                game_stage=1,
                rarity=wpn.rarity,
                parts=wpn.parts,
                grade_index=1,
            )
            previous = read_save_records(target)[str(wpn.unique_id)]
            re_saved = serialize_weapon(corrupted_wpn, previous_record=previous)
            restored = deserialize_weapon(re_saved)
            self.assertEqual(restored.game_stage, 61)
            self.assertEqual(restored.parts, wpn.parts)

    def test_asset_sublibrary_manifest_deterministic(self) -> None:
        m1 = build_asset_sublibrary_manifest()
        m2 = build_asset_sublibrary_manifest()
        self.assertEqual(m1, m2)
        self.assertEqual(len(m1["WeaponTypeDefinition"]), 5)
        self.assertEqual(len(m1["WeaponBalanceDefinition"]), 20)


if __name__ == "__main__":
    unittest.main()
