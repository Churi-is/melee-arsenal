"""Comprehensive offline CPython unit tests for `melee_arsenal.core`."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "sdk_mods") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "sdk_mods"))

from melee_arsenal.core import stats as stats_module  # noqa: E402
from melee_arsenal.core import (  # noqa: E402
    BLADES,
    BLADE_BY_MANUFACTURER,
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
    SecondaryAttack,
    TargetCandidate,
    TargetingMode,
    Vec3,
    GUARD_PARRY_WINDOW_SEC,
    THROW_DAMAGE_MULTIPLIER,
    apply_guard_damage_reduction,
    apply_incoming_damage_reduction,
    build_asset_sublibrary_manifest,
    build_throw_flight_plan,
    build_item_card_payload,
    compute_attack_damage,
    compute_card_stats,
    compute_damage_reduction_pct,
    compute_guard_cooldown_sec,
    compute_parry_reflect_damage,
    compute_swing_speed_damage_scale,
    compute_throw_weight_kg,
    guard_effective_reduction_pct,
    damage_reduction_level_scale,
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

    def test_damage_reduction_scales_with_level_and_parts(self) -> None:
        self.assertEqual((DAMAGE_REDUCTION_MIN_PCT, DAMAGE_REDUCTION_MAX_PCT), (30, 70))
        self.assertEqual(damage_reduction_level_scale(1), 0.0)
        self.assertEqual(damage_reduction_level_scale(90), 1.0)
        # Below Lv 1 / above Lv 80 + OP10 the curve clamps instead of wrapping
        self.assertEqual(damage_reduction_level_scale(0), 0.0)
        self.assertEqual(damage_reduction_level_scale(500), 1.0)

        rarities = (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.VERY_RARE)

        # Lv 1: hard 30% floor, only a sliver of part headroom (never the old 50-70%)
        low_values: set[int] = set()
        for i in range(150):
            wpn = roll_melee_weapon(
                game_stage=1,
                rarity=rarities[i % 4],
                manufacturer=tuple(Manufacturer)[i % 5],
                seed=f"dr-low:{i}",
                unique_id=2000 + i,
            )
            pct = compute_card_stats(wpn).damage_reduction_pct
            self.assertTrue(30 <= pct <= 34, f"Lv1 {wpn.parts.blade_id} rolled {pct}% (want 30-34)")
            low_values.add(pct)
            # Deterministic: card + hook + `melee_dump_held` all read the same instance
            self.assertEqual(pct, compute_damage_reduction_pct(wpn))
        self.assertIn(30, low_values)

        # Lv 80 + OP10: reaches the 70% cap, but only at the top of the part roll
        high_values: set[int] = set()
        for i in range(300):
            wpn = roll_melee_weapon(
                game_stage=90,
                rarity=rarities[i % 4],
                manufacturer=tuple(Manufacturer)[i % 5],
                seed=f"dr-high:{i}",
                unique_id=4000 + i,
            )
            pct = compute_card_stats(wpn).damage_reduction_pct
            self.assertTrue(61 <= pct <= 70, f"OP10 {wpn.parts.blade_id} rolled {pct}% (want 61-70)")
            high_values.add(pct)
        self.assertEqual(max(high_values), DAMAGE_REDUCTION_MAX_PCT)

        # Parts matter: the same level, different construction -> different reductions
        self.assertGreater(len(high_values), 3)

        # Higher level never reduces protection for the same blade
        blade = roll_melee_weapon(
            game_stage=1,
            rarity=Rarity.RARE,
            manufacturer=Manufacturer.HYPERION,
            seed="dr-monotonic",
            unique_id=7000,
        )
        previous = -1
        for stage in (1, 15, 30, 50, 72, 90):
            staged = MeleeWeaponInstance(7000, stage, blade.rarity, blade.parts)
            pct = compute_damage_reduction_pct(staged)
            self.assertGreaterEqual(pct, previous)
            previous = pct

        # Unique rarities keep a little headroom above the 70% cap
        best_unique = 0
        for i in range(200):
            wpn = roll_melee_weapon(
                game_stage=90,
                rarity=(Rarity.LEGENDARY, Rarity.SERAPH, Rarity.PEARLESCENT)[i % 3],
                manufacturer=tuple(Manufacturer)[i % 5],
                seed=f"dr-unique:{i}",
                unique_id=9000 + i,
            )
            best_unique = max(best_unique, compute_damage_reduction_pct(wpn))
        self.assertGreater(best_unique, DAMAGE_REDUCTION_MAX_PCT)

    def test_faster_swinging_blades_hit_softer(self) -> None:
        self.assertAlmostEqual(
            compute_swing_speed_damage_scale(1.50), 1.0
        )  # `SWING_SPEED_DAMAGE_REFERENCE`
        self.assertLess(compute_swing_speed_damage_scale(2.35), 1.0)   # Jakobs Sabre cadence
        self.assertGreater(compute_swing_speed_damage_scale(0.92), 1.0)  # Torgue Blast-Cleaver cadence

        speeds = (0.8, 1.0, 1.5, 2.0, 2.6)
        scales = [compute_swing_speed_damage_scale(sp) for sp in speeds]
        self.assertEqual(scales, sorted(scales, reverse=True))

        # End-to-end: per-hit card damage really is the unspeeded roll multiplied by the cadence
        # trade-off (patch the exponent to 0 to read the same weapon's unspeeded damage).
        part_sets = [
            MeleeWeaponParts("Blade_Jakobs_Sabre", "Grip_Maliwan", "Acc_None", "Elem_None"),
            MeleeWeaponParts("Blade_Hyperion_Katana", "Grip_Hyperion", "Acc_Serrated", "Elem_None"),
            MeleeWeaponParts("Blade_Torgue_BlastCleaver", "Grip_Torgue", "Acc_Counterweight", "Elem_Explosive"),
        ]
        for index, parts in enumerate(part_sets):
            weapon = MeleeWeaponInstance(4000 + index, 50, Rarity.RARE, parts)
            stats = compute_card_stats(weapon)
            with mock.patch.object(stats_module, "SWING_SPEED_DAMAGE_EXPONENT", 0.0):
                unspeeded = compute_card_stats(weapon).raw_damage
            expected_scale = compute_swing_speed_damage_scale(stats.swing_speed)
            # `swing_speed` on the card is rounded to 2 dp, so allow a hair of rounding slack
            self.assertAlmostEqual(stats.raw_damage, unspeeded * expected_scale, delta=unspeeded * 0.01)
            if stats.swing_speed > 1.50:
                self.assertLess(stats.card_damage, round(unspeeded))   # fast blade hit softer
            else:
                self.assertGreater(stats.card_damage, round(unspeeded))  # slow blade hit harder

        # ...and no manufacturer runs away with sustained DPS, even though each keeps its own
        # identity (Torgue hits hardest & slowest, Maliwan trades raw damage for element DoT, ...).
        # Matched parts isolate cadence + base multiplier from accessory/element damage rolls.
        for stage in (50, 90):
            dps = []
            for maker in Manufacturer:
                blade_id = BLADE_BY_MANUFACTURER[maker].part_id
                card = compute_card_stats(
                    MeleeWeaponInstance(
                        6000 + len(dps),
                        stage,
                        Rarity.VERY_RARE,
                        MeleeWeaponParts(blade_id, f"Grip_{maker.value}", "Acc_None", "Elem_None"),
                    )
                )
                dps.append(card.card_damage * card.swing_speed)
            self.assertLess(
                max(dps) / min(dps),
                1.75,
                f"Lv{stage} DPS spread {min(dps):,.0f}-{max(dps):,.0f} is too wide",
            )

        # Fastest cadence in the kit must not also be the runaway DPS leader (the old Jakobs problem)
        fastest = max(
            (
                compute_card_stats(
                    MeleeWeaponInstance(
                        7000 + i,
                        50,
                        Rarity.VERY_RARE,
                        MeleeWeaponParts(
                            BLADE_BY_MANUFACTURER[maker].part_id,
                            f"Grip_{maker.value}",
                            "Acc_None",
                            "Elem_None",
                        ),
                    )
                )
                for i, maker in enumerate(Manufacturer)
            ),
            key=lambda c: c.swing_speed,
        )
        self.assertGreaterEqual(fastest.swing_speed, 2.0)
        self.assertLess(fastest.card_damage, 40_000)

    def test_secondary_attack_roster_and_throw_arcs(self) -> None:
        # Manufacturer identities: Bandit/Torgue throw, Hyperion/Jakobs guard, Maliwan nothing.
        self.assertEqual(MANUFACTURERS[Manufacturer.BANDIT].secondary_attack, SecondaryAttack.THROW)
        self.assertEqual(MANUFACTURERS[Manufacturer.TORGUE].secondary_attack, SecondaryAttack.THROW)
        self.assertEqual(MANUFACTURERS[Manufacturer.HYPERION].secondary_attack, SecondaryAttack.GUARD)
        self.assertEqual(MANUFACTURERS[Manufacturer.JAKOBS].secondary_attack, SecondaryAttack.GUARD)
        self.assertEqual(MANUFACTURERS[Manufacturer.MALIWAN].secondary_attack, SecondaryAttack.NONE)

        # Thrown blades strike for exactly 200% of the normal LMB hit.
        self.assertEqual(THROW_DAMAGE_MULTIPLIER, 2.0)
        parts = MeleeWeaponParts("Blade_Bandit_Machete", "Grip_Bandit", "Acc_None", "Elem_None")
        weapon = MeleeWeaponInstance(11, 50, Rarity.RARE, parts)
        card = compute_card_stats(weapon)
        self.assertEqual(card.secondary_attack, SecondaryAttack.THROW)
        self.assertAlmostEqual(card.throw_weight_kg, compute_throw_weight_kg(weapon))
        normal = compute_attack_damage(card)
        thrown = compute_attack_damage(card, damage_scale=THROW_DAMAGE_MULTIPLIER)
        self.assertAlmostEqual(thrown.final_hit_damage, normal.final_hit_damage * 2, delta=2)

        # Weight is defined by the manufacturers of the actual parts: a Torgue blade is heavier
        # than a Bandit one, and an off-manufacturer grip changes the mass again.
        torgue = MeleeWeaponInstance(
            12, 50, Rarity.RARE,
            MeleeWeaponParts("Blade_Torgue_BlastCleaver", "Grip_Torgue", "Acc_HeavySpine", "Elem_Explosive"),
        )
        bandit_light = MeleeWeaponInstance(
            13, 50, Rarity.RARE,
            MeleeWeaponParts("Blade_Bandit_Machete", "Grip_Maliwan", "Acc_Counterweight", "Elem_None"),
        )
        self.assertLess(compute_throw_weight_kg(bandit_light), compute_throw_weight_kg(weapon))
        self.assertLess(compute_throw_weight_kg(weapon), compute_throw_weight_kg(torgue))

        # Heavier -> slower launch, higher loft, shorter reach & higher apex.
        light_plan = build_throw_flight_plan(compute_throw_weight_kg(bandit_light))
        heavy_plan = build_throw_flight_plan(compute_throw_weight_kg(torgue))
        self.assertGreater(light_plan.launch_speed_uu_s, heavy_plan.launch_speed_uu_s)
        self.assertLess(light_plan.launch_pitch_deg, heavy_plan.launch_pitch_deg)
        self.assertGreater(light_plan.range_uu, heavy_plan.range_uu)
        self.assertLess(light_plan.apex_uu, heavy_plan.apex_uu)
        light_card = compute_card_stats(bandit_light)
        heavy_card = compute_card_stats(torgue)
        self.assertGreater(light_card.throw_range_m, heavy_card.throw_range_m)
        self.assertLess(light_card.throw_arc_height_m, heavy_card.throw_arc_height_m)
        # The arc actually moves forward and comes back down
        fwd0, up0 = heavy_plan.offset_at(0.0)
        fwd_mid, up_mid = heavy_plan.offset_at(heavy_plan.flight_time_sec * 0.5)
        fwd_end, up_end = heavy_plan.offset_at(heavy_plan.flight_time_sec)
        self.assertAlmostEqual(fwd0, 0.0)
        self.assertAlmostEqual(fwd_mid, heavy_plan.horizontal_speed_uu_s * heavy_plan.flight_time_sec * 0.5)
        self.assertGreater(up_mid, up0)
        self.assertGreater(fwd_end, fwd_mid)
        self.assertLessEqual(up_end, up_mid)
        self.assertAlmostEqual(fwd_end, heavy_plan.range_uu, delta=heavy_plan.range_uu * 0.02)

        # Guard blades expose no throw data & Maliwan exposes no secondary attack whatsoever
        guard_card = compute_card_stats(
            MeleeWeaponInstance(
                14, 50, Rarity.RARE,
                MeleeWeaponParts("Blade_Hyperion_Katana", "Grip_Hyperion", "Acc_None", "Elem_None"),
            )
        )
        self.assertEqual(guard_card.throw_range_m, 0.0)
        maliwan_card = compute_card_stats(
            MeleeWeaponInstance(
                15, 50, Rarity.RARE,
                MeleeWeaponParts("Blade_Maliwan_Phaseblade", "Grip_Maliwan", "Acc_None", "Elem_Fire"),
            )
        )
        self.assertEqual(maliwan_card.secondary_attack, SecondaryAttack.NONE)
        self.assertEqual(maliwan_card.guard_cooldown_sec, 0.0)
        self.assertEqual(maliwan_card.guard_reduction_pct, 0)
        self.assertFalse(
            any("RMB" in line for line in build_item_card_payload(maliwan_card).fun_stats_lines)
        )

        # Cards advertise the secondary attack right under the Damage Reduction line
        throw_lines = build_item_card_payload(card).fun_stats_lines
        self.assertTrue(any("RMB Throw: 200% Damage" in line for line in throw_lines))
        guard_lines = build_item_card_payload(guard_card).fun_stats_lines
        self.assertTrue(any("RMB Hold: Guard" in line for line in guard_lines))

    def test_guard_reduction_parry_and_cooldown(self) -> None:
        self.assertEqual(GUARD_PARRY_WINDOW_SEC, 0.33)

        # Braced mitigation is the card DR applied twice: (damage * card DR) * card DR.
        self.assertAlmostEqual(apply_guard_damage_reduction(1000.0, 50), 250.0)
        self.assertAlmostEqual(apply_guard_damage_reduction(1000.0, 60), 160.0)
        self.assertAlmostEqual(apply_guard_damage_reduction(1000.0, 0), 1000.0)
        self.assertAlmostEqual(apply_guard_damage_reduction(0.0, 60), 0.0)
        self.assertAlmostEqual(apply_guard_damage_reduction(-50.0, 60), -50.0)
        self.assertEqual(guard_effective_reduction_pct(50), 75)
        self.assertEqual(guard_effective_reduction_pct(60), 84)
        self.assertEqual(guard_effective_reduction_pct(0), 0)

        # A perfect parry reflects the melee hit as a forced critical: 2.0x base crit x the
        # blade's own card crit bonus (Jakobs +100% -> 4x).
        self.assertEqual(compute_parry_reflect_damage(1000.0, 0.0), 2000)
        self.assertEqual(compute_parry_reflect_damage(1000.0, 100.0), 4000)
        self.assertEqual(compute_parry_reflect_damage(0.0, 100.0), 0)

        # Cooldown depends on parts and always stays inside sane bounds.
        hyperion = MeleeWeaponInstance(
            21, 50, Rarity.RARE,
            MeleeWeaponParts("Blade_Hyperion_Katana", "Grip_Hyperion", "Acc_None", "Elem_None"),
        )
        jakobs = MeleeWeaponInstance(
            22, 50, Rarity.RARE,
            MeleeWeaponParts("Blade_Jakobs_Sabre", "Grip_Jakobs", "Acc_None", "Elem_None"),
        )
        jakobs_heavy = MeleeWeaponInstance(
            23, 50, Rarity.RARE,
            MeleeWeaponParts("Blade_Jakobs_Sabre", "Grip_Torgue", "Acc_HeavySpine", "Elem_None"),
        )
        jakobs_nimble = MeleeWeaponInstance(
            24, 50, Rarity.RARE,
            MeleeWeaponParts("Blade_Jakobs_Sabre", "Grip_Jakobs", "Acc_Counterweight", "Elem_None"),
        )
        cd_hyp = compute_guard_cooldown_sec(hyperion)
        cd_jak = compute_guard_cooldown_sec(jakobs)
        cd_heavy = compute_guard_cooldown_sec(jakobs_heavy)
        cd_nimble = compute_guard_cooldown_sec(jakobs_nimble)
        self.assertLess(cd_hyp, cd_jak)          # Hyperion tech recovers faster than Jakobs steel
        self.assertLess(cd_jak, cd_heavy)        # heavy grip/accessory slows recovery
        self.assertLess(cd_nimble, cd_jak)       # balanced pommel speeds it up
        for maker in (Manufacturer.HYPERION, Manufacturer.JAKOBS):
            for i in range(40):
                wpn = roll_melee_weapon(
                    game_stage=(1, 30, 50, 72, 90)[i % 5],
                    rarity=(Rarity.COMMON, Rarity.RARE, Rarity.LEGENDARY)[i % 3],
                    manufacturer=maker,
                    seed=f"guard-cd:{i}",
                    unique_id=3000 + i,
                )
                self.assertGreaterEqual(compute_guard_cooldown_sec(wpn), 1.0)
                self.assertLessEqual(compute_guard_cooldown_sec(wpn), 4.0)
                card = compute_card_stats(wpn)
                self.assertEqual(card.guard_parry_window_sec, 0.33)
                self.assertGreater(card.guard_reduction_pct, card.damage_reduction_pct)

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
