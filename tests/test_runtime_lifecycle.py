from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.runtime_mock_support import FakeWrappedStruct, MockBL2World, SDK_MODS_DIR


class TestRuntimeLifecycle(unittest.TestCase):
    def test_strict_wrapped_struct_and_full_lifecycle(self) -> None:
        if str(SDK_MODS_DIR) not in sys.path:
            sys.path.insert(0, str(SDK_MODS_DIR))

        with tempfile.TemporaryDirectory() as tmp:
            world = MockBL2World(Path(tmp))
            sdk_mods = world.make_sdk_modules()

            for mod_name in list(sys.modules):
                if mod_name == "melee_arsenal" or mod_name.startswith("melee_arsenal.runtime"):
                    sys.modules.pop(mod_name, None)

            with mock.patch.dict(sys.modules, sdk_mods):
                import melee_arsenal  # type: ignore[import-not-found]
                from melee_arsenal.core.parts.accessories import ACCESSORIES
                from melee_arsenal.core.enums import Rarity
                from melee_arsenal.core.item_card import build_item_card_payload
                from melee_arsenal.core.parts.weapon import (
                    MeleeWeaponInstance,
                    MeleeWeaponParts,
                    PlayerMeleeContext,
                )
                from melee_arsenal.core.secondary import (
                    THROW_DAMAGE_MULTIPLIER,
                    build_throw_flight_plan,
                    compute_parry_reflect_damage,
                    compute_throw_weight_kg,
                )
                from melee_arsenal.core.stats import (
                    apply_guard_damage_reduction,
                    compute_attack_damage,
                    compute_card_stats,
                    compute_damage_reduction_pct,
                )
                from melee_arsenal.runtime import (
                    combat,
                    commands,
                    definitions,
                    loot_pools,
                    persistence_bridge,
                    resolver,
                    secondary_vfx,
                    state,
                    ui_item_card,
                )
                from melee_arsenal.runtime.secondary import guard as secondary_guard
                from melee_arsenal.runtime.secondary import lifecycle as secondary_lifecycle
                from melee_arsenal.runtime.secondary import throw as secondary_throw

                self.assertIsNotNone(melee_arsenal.mod)

                # 1. Build Definitions under strict WrappedStruct & schema checks
                self.assertTrue(definitions.ensure_definitions_built())
                self.assertEqual(len(state.STATE.maker_to_wt), 5)
                self.assertEqual(len(state.STATE.element_to_firing_mode), 6)
                self.assertEqual(len(state.STATE.blade_parts), 5)
                self.assertEqual(len(state.STATE.accessory_parts), len(ACCESSORIES))
                self.assertEqual(len(state.STATE.balances), 20)

                # Verify EVERY balance (Common, Uncommon, Rare, VeryRare) inherited Manufacturers cleanly!
                for bal_key, bal_obj in state.STATE.balances.items():
                    self.assertEqual(len(bal_obj.Manufacturers), 1, f"{bal_key} has empty Manufacturers!")

                # Verify Torgue rocket CustomFiringModeDefinition was cleared on Blade_Torgue_BlastCleaver
                self.assertIsNone(state.STATE.blade_parts["Blade_Torgue_BlastCleaver"].CustomFiringModeDefinition)

                # Verify firing-mode trace and weapon recoil settings.
                for efm in state.STATE.element_to_firing_mode.values():
                    self.assertEqual(efm.TraceExtent.X, 0.0)
                    self.assertEqual(efm.TraceExtent.Y, 0.0)
                    self.assertEqual(efm.TraceExtent.Z, 0.0)
                    self.assertEqual(list(efm.FiringPatternLines), [])
                    self.assertFalse(efm.bPenetratePawn)
                for wt_obj in state.STATE.maker_to_wt.values():
                    self.assertEqual(list(wt_obj.WeaponFireAnimations), [])
                    self.assertEqual(wt_obj.WeaponKickUp, 0.0)
                    self.assertEqual(wt_obj.Range, 4096.0)

                # 2. Loot Pool Injection
                self.assertTrue(loot_pools.sync_loot_pools())

                # 3. Drop All 5 Archetypes in front of player — ZERO XP (`reward_call_lengths == []`)!
                count_all = commands.spawn_all_five_archetypes("purple")
                self.assertEqual(count_all, 5)
                self.assertEqual(len(world.ground_pickups), 5)
                self.assertEqual(world.reward_call_lengths, [])

                # Verify newly dropped melee pickups start with ReloadCnt == 100 and exact player GameStage == 30
                self.assertEqual(world.ground_pickups[0].Inventory.ReloadCnt, 100)
                self.assertEqual(world.ground_pickups[0].Inventory.DefinitionData.GameStage, 30)

                # Verify exact element filter (`melee_spawn hyperion purple fire`)
                world.ground_pickups.clear()
                commands.spawn_single_command("hyperion", "purple", "fire")
                hyp_fire = world.ground_pickups[-1].Inventory
                self.assertTrue(hyp_fire.DefinitionData.ElementalPartDefinition._path_name().endswith("Part_Elem_Fire"))
                self.assertEqual(hyp_fire.DefinitionData.GameStage, 30)

                # Drop Jakobs Sabre, Maliwan Fire Phaseblade, and Torgue Blast-Cleaver & pick them up
                world.ground_pickups.clear()
                commands.spawn_single_command("jakobs", "purple", "")
                sabre = world.ground_pickups[-1].Inventory
                commands.spawn_single_command("maliwan", "purple", "fire")
                phaseblade = world.ground_pickups[-1].Inventory
                commands.spawn_single_command("torgue", "purple", "")
                cleaver = world.ground_pickups[-1].Inventory
                world.backpack.extend([sabre, phaseblade, cleaver])

                # 4. Primary melee: `InstantFire`, `Bladed_Melee`, `CalcWeaponFire`, and `Behavior_CauseDamage`.
                world.set_enemies(
                    [
                        (210.0, 0.0, 64.0, False, "Pawn_TargetDummy_0"),  # Sanctuary Target Dummy!
                        (220.0, -65.0, 64.0, True, "WillowAIPawn_Bandit_1"),
                        (220.0, 70.0, 64.0, True, "WillowAIPawn_Bandit_2"),
                        (205.0, 35.0, 64.0, True, "Wall_Blocked_Bandit"),  # behind wall -> MUST NOT be hit!
                        (200.0, 20.0, 64.0, False, "Sanctuary_Citizen"),  # friendly NPC -> ignored!
                        (900.0, 0.0, 64.0, True, "Far_Bandit"),  # out of melee reach!
                    ]
                )
                world.wall_blocked_names = {"Wall_Blocked_Bandit"}
                world.melee_mult = 2.0
                world.roid_buff = 500.0

                # 4A. Single-Target Jakobs Sabre Headshot Crit (`BoneName="Head"` -> 4.0x `+100%` crit!)
                world.pawn.Weapon = sabre
                world.caused_damages.clear()
                world.played_custom_anims.clear()
                world.stopped_slots.clear()
                combat.pose._LAST_SWING_AT = 0.0

                _, sabre_card = resolver.resolve_melee_weapon(sabre)
                expected_sabre_crit = compute_attack_damage(
                    sabre_card,
                    PlayerMeleeContext(melee_damage_scale=2.0, roid_flat_damage=500.0),
                    is_crit=True,
                ).final_hit_damage

                # Aim directly at Pawn_TargetDummy_0's Head!
                dummy_pawn = world.world_info.PawnList
                world.next_center_trace_override = (dummy_pawn, world._vec(210.0, 0.0, 95.0), "Head")

                combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                self.assertTrue(sabre.pending_fire_cleared)

                res_if = combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                self.assertEqual(res_if, world.block_sentinel)
                # Played real `Bladed_Melee` on `CustomAnimSlot` WITHOUT touching `WeaponAction` (`SpecialMove_FirstPerson`)!
                self.assertEqual(len(world.played_custom_anims), 1)
                self.assertEqual(world.played_custom_anims[0][0], "CustomAnimSlot")
                self.assertEqual(world.played_custom_anims[0][1], "Bladed_Melee")
                self.assertNotIn("WeaponAction", world.stopped_slots)
                self.assertIn("WeaponRecoil", world.stopped_slots)
                # Dealt exact headshot crit stacked melee+roid damage to ONLY the single target!
                self.assertEqual(len(world.caused_damages), 1)
                self.assertEqual(world.caused_damages[0]["Target"], dummy_pawn)
                self.assertAlmostEqual(world.caused_damages[0]["DamageAmount"], float(expected_sabre_crit))
                # ...and the crit was tagged so BL2's floating number can be re-issued as a CRITICAL
                self.assertIn(combat.cache_key(dummy_pawn), combat._PENDING_CRIT_FEEDBACK)
                combat.clear_all_pending_crit_feedback()

                # Mid-swap guard: if `pawn.PendingWeapon` is active, swing must NOT touch `Arms` anim slots!
                world.pawn.PendingWeapon = phaseblade
                try:
                    combat.pose._LAST_SWING_AT -= 0.35
                    combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                    combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                    self.assertEqual(len(world.played_custom_anims), 1)
                finally:
                    world.pawn.PendingWeapon = None

                # 4A-2. Consecutive Swing 2 ALSO plays `Bladed_Melee` cleanly without touching `Origin` / `RotOrigin`:
                combat.pose._LAST_SWING_AT -= 0.35
                combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                self.assertFalse(combat.STATE.swing_motion.active)
                self.assertEqual(len(world.played_custom_anims), 2)
                self.assertEqual(world.played_custom_anims[1][1], "Bladed_Melee")
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.X, 1.0)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Y, -2.0)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Z, 3.0)
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Yaw, -16384)

                # Verify gun bullet, fire effects, & recoil hooks all return Block for Melee Arsenal weapons
                self.assertEqual(combat.on_weapon_firing_mode_fire_pre(sabre, None, None, None), world.block_sentinel)
                self.assertEqual(combat.on_weapon_play_fire_effects_pre(sabre, None, None, None), world.block_sentinel)
                self.assertEqual(
                    combat.on_pawn_weapon_fired_pre(
                        world.pawn,
                        types.SimpleNamespace(InWeapon=sabre, bViaReplication=False, HitLocation=world._vec()),
                        None,
                        None,
                    ),
                    world.block_sentinel,
                )

                # Verify PlayAnimation blocks `Fire_*` recoil sequences without calling `func` or mutating `DefinitionData`,
                # and passes `Equip`/`Idle` straight through (`None`) without re-entry!
                res_fire_seq = resolver.on_play_weapon_animation_pre(
                    sabre,
                    types.SimpleNamespace(
                        AnimType=0,
                        Sequence="Fire_Jakobs",
                        fDesiredDuration=0.26,
                        bLoop=False,
                        BlendInTime=0.0,
                        BlendOutTime=0.0,
                    ),
                    None,
                    lambda **kw: (_ for _ in ()).throw(AssertionError("func must never be called")),
                )
                self.assertEqual(res_fire_seq, (world.block_sentinel, 0.0))
                res_equip_seq = resolver.on_play_weapon_animation_pre(
                    sabre,
                    types.SimpleNamespace(
                        AnimType=0,
                        Sequence="Equip_Jakobs",
                        fDesiredDuration=0.33,
                        bLoop=False,
                        BlendInTime=0.0,
                        BlendOutTime=0.0,
                    ),
                    None,
                    lambda **kw: (_ for _ in ()).throw(AssertionError("func must never be called")),
                )
                self.assertIsNone(res_equip_seq)
                self.assertFalse(hasattr(resolver, "on_play_first_person_body_anim_pre"))

                # 4B. Frontal-Cleave Maliwan Fire Phaseblade:
                # Hits TargetDummy + Bandit_1 + Bandit_2 (= 3 targets), while Wall_Blocked_Bandit,
                # Sanctuary_Citizen, and Far_Bandit are all excluded!
                world.pawn.Weapon = phaseblade
                world.caused_damages.clear()
                combat.pose._LAST_SWING_AT = 0.0
                combat.on_weapon_consume_ammo_post(phaseblade, None, None, None)
                combat.on_weapon_instant_fire_pre(phaseblade, None, None, None)
                hit_names = {d["Target"].Name for d in world.caused_damages}
                self.assertEqual(
                    hit_names,
                    {"Pawn_TargetDummy_0", "WillowAIPawn_Bandit_1", "WillowAIPawn_Bandit_2"},
                )
                for d in world.caused_damages:
                    dtd_path = d["DamageTypeDefinition"]._path_name()
                    self.assertIn(
                        dtd_path,
                        {
                            "GD_Incendiary.DamageType.DmgType_Incendiary_Impact",
                            "GD_Incendiary.DamageType.DmgType_Incendiary_Impact_NoDoT",
                        },
                    )
                    self.assertGreater(d["_wpn_dot_dmg"], 0.0)
                    if dtd_path.endswith("_NoDoT"):
                        self.assertEqual(d["_wpn_base_chance_mod"], 0.0)
                        self.assertEqual(d["_wpn_chance_mod"], 0.0)
                    else:
                        self.assertEqual(d["_wpn_base_chance_mod"], 50.0)
                        self.assertEqual(d["_wpn_chance_mod"], 50.0)
                # Restored clean modifier state after swing loop
                self.assertEqual(phaseblade.BaseStatusEffectChanceModifier, 1.0)

                # 4C. Frontal-Cleave Torgue Blast-Cleaver (`SharedFireAmmunition` backup + Explosive Splash + VFX/SFX!)
                world.pawn.Weapon = cleaver
                world.caused_damages.clear()
                world.caused_radius_damages.clear()
                world.spawned_emitters.clear()
                combat.pose._LAST_SWING_AT = 0.0
                combat.on_weapon_consume_ammo_post(cleaver, None, None, None)
                with (
                    mock.patch.object(combat.STATE, "debug_attack_log", True),
                    mock.patch.object(combat.attacks, "log_info") as combat_log,
                ):
                    combat.on_weapon_shared_fire_post(cleaver, None, None, None)
                checkpoints = [call.args[0] for call in combat_log.call_args_list]
                for stage in (
                    "swing start",
                    "before direct damage",
                    "after direct damage",
                    "before radius damage",
                    "after radius damage",
                    "before explosion effects",
                    "after explosion effects",
                ):
                    self.assertTrue(any(stage in line for line in checkpoints), stage)
                self.assertEqual(len(world.caused_damages), 3)
                self.assertEqual(len(world.caused_radius_damages), 1)
                self.assertAlmostEqual(world.caused_radius_damages[0]["Radius"], 180.0)
                self.assertEqual(
                    world.caused_radius_damages[0]["DamageTypeDefinition"]._path_name(),
                    "GD_Explosive.DamageType.DmgType_Explosive_Impact_ForceFlinch",
                )
                self.assertEqual(len(world.spawned_emitters), 1)
                self.assertEqual(
                    world.spawned_emitters[0][0],
                    "FX_WEP_Explosions.Particles.Part_Gyrojet_Explosion",
                )
                self.assertIn("Ake_Wep_Explosions.Ak_Play_Wep_Explosion_Gyrojet", world.played_ak)
                self.assertIn("Anim_CameraAnimations.Explosions.Canim_Explosion_Medium", world.played_canims)

                # 4D. Defensive Blade Guard: the held Melee Weapon's rolled level-scaled Damage
                # Reduction blocks the incoming `WillowPlayerPawn:TakeDamage` and re-calls it reduced!
                world.pawn.Weapon = sabre
                sabre_inst_dr, sabre_dr_card = resolver.resolve_melee_weapon(sabre, sync_weapon_props=False)
                dr_pct = sabre_dr_card.damage_reduction_pct
                self.assertTrue(30 <= dr_pct <= 70, f"Damage Reduction {dr_pct}% outside the 30-70% band!")
                self.assertEqual(dr_pct, compute_damage_reduction_pct(sabre_inst_dr))
                # A Lv30 blade must sit well under the Lv80+OP10-only 70% cap
                self.assertLess(dr_pct, 70)
                self.assertIn(melee_arsenal.runtime.hook_player_take_damage, melee_arsenal.runtime.ALL_HOOKS)

                td_re_calls = []
                td_args = FakeWrappedStruct("WillowPlayerPawn.TakeDamage", Damage=1000.0)
                expected_reduced = 1000.0 * (1.0 - dr_pct / 100.0)

                res_dr = combat.on_player_take_damage_pre(
                    world.pawn,
                    td_args,
                    None,
                    lambda *a, **k: td_re_calls.append((a, k)),
                )
                self.assertEqual(res_dr, world.block_sentinel)
                self.assertEqual(len(td_re_calls), 1)
                # `func(args)` re-call received the SAME args struct, now carrying reduced damage
                self.assertIs(td_re_calls[0][0][0], td_args)
                self.assertAlmostEqual(td_args.Damage, expected_reduced, places=3)

                # A vanilla gun in hand (or no weapon) must never reduce incoming damage
                vanilla_wpn = types.SimpleNamespace(
                    Name="WillowWeapon_VanillaPistol",
                    ObjectFlags=0,
                    DefinitionData=FakeWrappedStruct(
                        "WeaponDefinitionData",
                        WeaponTypeDefinition=None,
                        BarrelPartDefinition=None,
                        BalanceDefinition=None,
                    ),
                )
                for held in (vanilla_wpn, None):
                    world.pawn.Weapon = held
                    td_re_calls.clear()
                    self.assertIsNone(
                        combat.on_player_take_damage_pre(world.pawn, td_args, None, lambda *a: td_re_calls.append(a))
                    )
                    self.assertEqual(td_re_calls, [])

                # Co-op safety: only the LOCAL player's own pawn is ever modified
                co_op_pawn = types.SimpleNamespace(Name="WillowPlayerPawn_CoOp", ObjectFlags=0, Weapon=sabre)
                td_re_calls.clear()
                self.assertIsNone(
                    combat.on_player_take_damage_pre(co_op_pawn, td_args, None, lambda *a: td_re_calls.append(a))
                )
                self.assertEqual(td_re_calls, [])

                # The Mod Menu toggle turns the whole defensive perk off
                world.pawn.Weapon = sabre
                state.STATE.damage_reduction_enabled = False
                td_re_calls.clear()
                try:
                    self.assertIsNone(
                        combat.on_player_take_damage_pre(world.pawn, td_args, None, lambda *a: td_re_calls.append(a))
                    )
                    self.assertEqual(td_re_calls, [])
                finally:
                    state.STATE.damage_reduction_enabled = True

                # 4E. Weakspot crits must surface the vanilla floating "CRITICAL" popup.
                # `Behavior_CauseDamage` has no HitInfo, so the engine's recent-damage tracker never
                # sets the crit bit itself; we tag the victim and re-issue the display call with it.
                combat.clear_all_pending_crit_feedback()
                crit_display_calls: list[object] = []
                crit_event = types.SimpleNamespace(
                    PC=world.pc,
                    DamageEventData=types.SimpleNamespace(
                        DamagedActor=dummy_pawn, DamageEventFlags=0, TotalDamageForDamageType=123.0
                    ),
                )
                # Unknown victim: never forced, vanilla display runs untouched
                self.assertIsNone(
                    combat.on_display_recent_damage_pre(
                        object(), crit_event, None, lambda *a: crit_display_calls.append(a)
                    )
                )
                self.assertEqual(crit_display_calls, [])

                # Co-op safety: a remote player's floating numbers are never modified
                combat._mark_pending_crit_feedback(dummy_pawn)
                remote_event = types.SimpleNamespace(
                    PC=types.SimpleNamespace(Name="WillowPlayerController_CoOp"),
                    DamageEventData=types.SimpleNamespace(DamagedActor=dummy_pawn, DamageEventFlags=0),
                )
                self.assertIsNone(
                    combat.on_display_recent_damage_pre(
                        object(), remote_event, None, lambda *a: crit_display_calls.append(a)
                    )
                )
                self.assertEqual(crit_display_calls, [])
                self.assertEqual(remote_event.DamageEventData.DamageEventFlags, 0)

                # Local crit: display re-called exactly once with the crit bit (1) forced on
                self.assertIs(
                    combat.on_display_recent_damage_pre(
                        object(), crit_event, None, lambda *a: crit_display_calls.append(a)
                    ),
                    world.block_sentinel,
                )
                self.assertEqual(len(crit_display_calls), 1)
                self.assertEqual(crit_event.DamageEventData.DamageEventFlags, 1)
                # Tag consumed: a follow-up DoT/normal popup on the same actor is never force-crit
                crit_event.DamageEventData.DamageEventFlags = 0
                self.assertIsNone(
                    combat.on_display_recent_damage_pre(
                        object(), crit_event, None, lambda *a: crit_display_calls.append(a)
                    )
                )
                self.assertEqual(len(crit_display_calls), 1)

                # Expired tags are ignored (no stale crits when a swing is interrupted/lagging)
                combat._mark_pending_crit_feedback(dummy_pawn)
                combat._PENDING_CRIT_FEEDBACK[combat.cache_key(dummy_pawn)] = 0.0
                self.assertIsNone(
                    combat.on_display_recent_damage_pre(
                        object(), crit_event, None, lambda *a: crit_display_calls.append(a)
                    )
                )
                self.assertEqual(len(crit_display_calls), 1)
                combat.clear_all_pending_crit_feedback()

                # 4F. RMB / ADS zoom is disabled on Melee Weapons (reserved for secondary attacks)
                zoom_func_calls: list[object] = []
                world.pawn.Weapon = sabre
                self.assertIs(
                    combat.on_melee_weapon_start_zoom_pre(sabre, None, None, lambda *a: zoom_func_calls.append(a)),
                    world.block_sentinel,
                )
                self.assertEqual(zoom_func_calls, [])
                # ...but a vanilla gun in hand keeps its native scope/ADS behaviour!
                self.assertIsNone(
                    combat.on_melee_weapon_start_zoom_pre(vanilla_wpn, None, None, lambda *a: zoom_func_calls.append(a))
                )
                # RMB dispatches the manufacturer secondary attack: a Jakobs Sabre guards.
                self.assertTrue(combat.MELEE_SECONDARY_ATTACK_ENABLED)
                secondary_lifecycle.reset_secondary_state()
                self.assertTrue(combat._try_melee_secondary_attack(sabre, sabre_inst_dr, sabre_dr_card))
                self.assertTrue(secondary_guard.is_guarding(sabre))
                secondary_guard.end_guard(sabre)
                secondary_guard._GUARD_COOLDOWN_UNTIL = 0.0
                # Maliwan gets nothing: RMB is inert on a Phaseblade.
                _, phase_card = resolver.resolve_melee_weapon(phaseblade, sync_weapon_props=False)
                self.assertFalse(combat._try_melee_secondary_attack(phaseblade, sabre_inst_dr, phase_card))
                self.assertFalse(secondary_guard.is_guarding())
                self.assertIn(melee_arsenal.runtime.hook_melee_start_zoom, melee_arsenal.runtime.ALL_HOOKS)
                self.assertIn(melee_arsenal.runtime.cmd_melee_secondary, melee_arsenal.runtime.ALL_COMMANDS)
                self.assertIn(melee_arsenal.runtime.hook_display_recent_damage, melee_arsenal.runtime.ALL_HOOKS)

                # 4G. Camera shake must be a slow, eased camera bump for fast blades (no more flicks)
                world.camera_anim_calls.clear()
                world.played_canims.clear()
                world.pawn.Weapon = sabre

                def _swing_shake() -> list:
                    world.camera_anim_calls.clear()
                    combat.pose._LAST_SWING_AT = 0.0
                    combat.on_weapon_consume_ammo_post(sabre, None, None, None)
                    combat.on_weapon_instant_fire_pre(sabre, None, None, None)
                    return world.camera_anim_calls

                calls = _swing_shake()
                self.assertTrue(calls, "expected a CameraAnim on swing")
                for path, cam_args in calls:
                    self.assertIn("Melee", path)
                    scale, rate, blend_in, blend_out = cam_args[0], cam_args[1], cam_args[2], cam_args[3]
                    # The blend-in is long enough to smooth the opening motion.
                    self.assertGreaterEqual(blend_in, 0.25, f"blend-in {blend_in}s is too abrupt")
                    self.assertGreaterEqual(blend_out, blend_in * 0.9, "fade-out must match the ease-in")
                    # Slow playback softens the camera animation's opening impulse.
                    self.assertLessEqual(rate, 0.70, f"camera shake rate {rate} is too fast")
                    self.assertLessEqual(scale, 0.05, f"camera shake scale {scale} too strong")
                default_scale = calls[0][1][0]

                # 50%, 100%, and 150% produce 1x, 2x, and 3x the authored amplitude.
                self.assertAlmostEqual(combat.CAMERA_SHAKE_SLIDER_GAIN, 0.2)
                self.assertAlmostEqual(state.STATE.camera_shake_scale, 0.5)
                state.STATE.camera_shake_scale = 1.0
                louder = _swing_shake()[0][1][0]
                self.assertAlmostEqual(louder, default_scale * 2.0)
                state.STATE.camera_shake_scale = 1.5
                boosted = _swing_shake()[0][1][0]
                self.assertAlmostEqual(boosted, default_scale * 3.0)
                # 0% is a true off switch (no CameraAnim call at all)
                state.STATE.camera_shake_scale = 0.0
                self.assertEqual(_swing_shake(), [], "0% camera shake must stay silent")
                state.STATE.camera_shake_scale = 0.5
                world.camera_anim_calls.clear()
                world.played_canims.clear()

                # 4H. Secondary Attack 1 — GUARD (Hyperion / Jakobs): hold RMB to brace.
                #     The next hit has the card DR applied twice, a hit inside 0.33s is a perfect
                #     zero-damage parry reflected back at a melee attacker as a forced crit, and the
                #     part-driven cooldown locks the guard out afterwards.
                secondary_lifecycle.reset_secondary_state()
                world.pawn.Weapon = sabre
                sabre_inst_g, sabre_card_g = resolver.resolve_melee_weapon(sabre, sync_weapon_props=False)
                self.assertEqual(sabre_card_g.secondary_attack.value, "guard")
                self.assertAlmostEqual(sabre_card_g.guard_parry_window_sec, 0.33)
                self.assertGreater(sabre_card_g.guard_cooldown_sec, 0.0)
                self.assertGreater(sabre_card_g.guard_reduction_pct, sabre_card_g.damage_reduction_pct)
                # Guard mitigation really is the card DR applied twice
                self.assertAlmostEqual(
                    apply_guard_damage_reduction(1000.0, sabre_card_g.damage_reduction_pct),
                    1000.0
                    * (1.0 - sabre_card_g.damage_reduction_pct / 100.0)
                    * (1.0 - sabre_card_g.damage_reduction_pct / 100.0),
                )

                guard_re_calls = []
                guard_args = FakeWrappedStruct("WillowPlayerPawn.TakeDamage", Damage=1000.0)
                self.assertTrue(secondary_guard.try_begin_guard(sabre, sabre_inst_g, sabre_card_g))
                self.assertTrue(secondary_guard.is_guarding(sabre))
                # The brace is VISIBLE: the first-person hands (and the blade attached to them)
                # rotate into a guard posture instead of staying in the idle pose.
                self.assertEqual(combat.active_first_person_pose(), combat.POSE_GUARD)
                # The hands only lift; they must never slide sideways across the screen (that was
                # the "it just moves the hand toward the centre and left" complaint).
                self.assertGreater(world.arms_skel_mesh.RotOrigin.Pitch, 800)
                self.assertLess(world.arms_skel_mesh.RotOrigin.Pitch, 2200)
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Yaw, -16384)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Z, 3.0, msg="guard must not translate the hands")
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Y, -2.0)
                # ...the visible BRACE is the held blade itself tilting: its mesh pivots at the
                # hand, so the tip lifts and swings toward the crosshair while the hand stays put.
                guard_blade_mesh = sabre.Mesh
                self.assertGreater(guard_blade_mesh.RotOrigin.Pitch, 1000, "blade tip must lift")
                self.assertLess(guard_blade_mesh.RotOrigin.Yaw, 0, "blade must point inward")
                self.assertLess(guard_blade_mesh.RotOrigin.Roll, 0, "blade must angle its flat")
                # Hold the guard past the parry window, then eat one hit: mitigated, then consumed.
                secondary_guard._GUARD.started_at -= 0.60
                self.assertIs(
                    combat.on_player_take_damage_pre(
                        world.pawn,
                        guard_args,
                        None,
                        lambda *a: guard_re_calls.append(a),
                    ),
                    world.block_sentinel,
                )
                self.assertEqual(len(guard_re_calls), 1)
                self.assertAlmostEqual(
                    guard_args.Damage,
                    apply_guard_damage_reduction(1000.0, sabre_card_g.damage_reduction_pct),
                )
                self.assertFalse(secondary_guard.is_guarding())
                self.assertGreater(secondary_guard.guard_cooldown_remaining(), 0.0)
                # ...and the blade mesh is restored exactly when the brace is consumed by a hit.
                self.assertEqual(guard_blade_mesh.RotOrigin.Pitch, 0)
                self.assertEqual(guard_blade_mesh.RotOrigin.Yaw, 0)
                self.assertEqual(guard_blade_mesh.RotOrigin.Roll, 0)
                # Dropping the guard restores the idle hands exactly (no drifting offsets).
                self.assertEqual(combat.active_first_person_pose(), "")
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Pitch, 0)
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Yaw, -16384)
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Roll, 0)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.X, 1.0)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Y, -2.0)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Z, 3.0)
                # ...and the part-driven cooldown refuses a fresh guard
                self.assertTrue(secondary_guard.try_begin_guard(sabre, sabre_inst_g, sabre_card_g))
                self.assertFalse(secondary_guard.is_guarding())

                # Perfect parry: inside 0.33s the hit is nullified and a melee blow is reflected
                # back at a forced critical.
                secondary_guard._GUARD_COOLDOWN_UNTIL = 0.0
                self.assertTrue(secondary_guard.try_begin_guard(sabre, sabre_inst_g, sabre_card_g))
                melee_attacker = types.SimpleNamespace(
                    Name="WillowAIPawn_Pyscho_Melee", ObjectFlags=0, Location=world._vec(140.0, 0.0, 64.0)
                )
                parry_args = FakeWrappedStruct(
                    "WillowPlayerPawn.TakeDamage",
                    Damage=1000.0,
                    DamageType=types.SimpleNamespace(_path_name=lambda: "WillowGame.WillowDmgSource_Melee"),
                    EventInstigator=types.SimpleNamespace(Pawn=melee_attacker),
                )
                world.caused_damages.clear()
                guard_re_calls.clear()
                combat.clear_all_pending_crit_feedback()
                self.assertIs(
                    combat.on_player_take_damage_pre(world.pawn, parry_args, None, lambda *a: guard_re_calls.append(a)),
                    world.block_sentinel,
                )
                # Zero damage: the blocked original call is never re-issued
                self.assertEqual(guard_re_calls, [])
                self.assertEqual(len(world.caused_damages), 1)
                reflected = world.caused_damages[0]
                self.assertIs(reflected["Target"], melee_attacker)
                parry_ctx = combat.read_player_melee_context(world.pawn, world.pc)
                blade_crit = compute_attack_damage(sabre_card_g, parry_ctx, is_crit=True).final_hit_damage
                self.assertEqual(
                    reflected["DamageAmount"],
                    float(compute_parry_reflect_damage(1000.0, sabre_card_g.crit_bonus_pct, blade_crit)),
                )
                # ...and the reflected hit is tagged so BL2 prints a CRITICAL popup
                self.assertIn(combat.cache_key(melee_attacker), combat._PENDING_CRIT_FEEDBACK)
                # The parry is VISIBLE + AUDIBLE: HUD callout, clang and a camera jolt.
                self.assertTrue(
                    any("PERFECT PARRY" in msg for _title, msg in world.hud_messages),
                    f"missing parry HUD callout: {world.hud_messages}",
                )
                self.assertTrue(any("Merc_impact" in path or "BladedWeap_hit" in path for path in world.played_ak))
                self.assertTrue(world.camera_anim_calls, "parry must kick the camera")
                combat.clear_all_pending_crit_feedback()
                self.assertFalse(secondary_guard.is_guarding())
                self.assertGreater(secondary_guard.guard_cooldown_remaining(), 0.0)

                # Reflected damage uses the equipped blade's critical damage.
                secondary_guard._GUARD_COOLDOWN_UNTIL = 0.0
                self.assertTrue(secondary_guard.try_begin_guard(sabre, sabre_inst_g, sabre_card_g))
                weak_args = FakeWrappedStruct(
                    "WillowPlayerPawn.TakeDamage",
                    Damage=20.0,
                    DamageType=types.SimpleNamespace(_path_name=lambda: "WillowGame.WillowDmgSource_Melee"),
                    EventInstigator=types.SimpleNamespace(Pawn=melee_attacker),
                )
                world.caused_damages.clear()
                world.hud_messages.clear()
                self.assertIs(
                    combat.on_player_take_damage_pre(world.pawn, weak_args, None, lambda *a: guard_re_calls.append(a)),
                    world.block_sentinel,
                )
                self.assertEqual(len(world.caused_damages), 1)
                weak_reflect = int(world.caused_damages[0]["DamageAmount"])
                self.assertGreaterEqual(weak_reflect, blade_crit)
                self.assertGreater(weak_reflect, compute_parry_reflect_damage(20.0, 0.0))
                self.assertTrue(any("PERFECT PARRY" in msg for _t, msg in world.hud_messages))
                combat.clear_all_pending_crit_feedback()
                self.assertFalse(secondary_guard.is_guarding())
                secondary_guard._GUARD_COOLDOWN_UNTIL = 0.0

                # A non-melee hit inside the parry window is still fully absorbed, but nothing is
                # reflected (only melee blows are parried back).
                secondary_guard._GUARD_COOLDOWN_UNTIL = 0.0
                self.assertTrue(secondary_guard.try_begin_guard(sabre, sabre_inst_g, sabre_card_g))
                ranged_args = FakeWrappedStruct(
                    "WillowPlayerPawn.TakeDamage",
                    Damage=1000.0,
                    DamageType=types.SimpleNamespace(_path_name=lambda: "WillowGame.WillowDmgSource_Bullet"),
                    EventInstigator=types.SimpleNamespace(Pawn=melee_attacker),
                )
                world.caused_damages.clear()
                guard_re_calls.clear()
                self.assertIs(
                    combat.on_player_take_damage_pre(
                        world.pawn, ranged_args, None, lambda *a: guard_re_calls.append(a)
                    ),
                    world.block_sentinel,
                )
                self.assertEqual(guard_re_calls, [])
                self.assertEqual(world.caused_damages, [])
                self.assertFalse(secondary_guard.is_guarding())

                # Release (StopZoom), co-op safety and weapon-swap auto-drop
                secondary_guard._GUARD_COOLDOWN_UNTIL = 0.0
                self.assertTrue(secondary_guard.try_begin_guard(sabre, sabre_inst_g, sabre_card_g))
                # A same-frame StartZoom/StopZoom pair is engine noise and must not drop the guard
                secondary_guard.on_melee_weapon_stop_zoom_pre(sabre, None, None, None)
                self.assertTrue(secondary_guard.is_guarding(sabre))
                secondary_guard._GUARD.started_at -= 0.40
                secondary_guard.on_melee_weapon_stop_zoom_pre(sabre, None, None, None)
                self.assertFalse(secondary_guard.is_guarding())
                self.assertGreater(secondary_guard.guard_cooldown_remaining(), 0.0)
                secondary_guard._GUARD_COOLDOWN_UNTIL = 0.0
                self.assertTrue(secondary_guard.try_begin_guard(sabre, sabre_inst_g, sabre_card_g))
                co_op_pawn = types.SimpleNamespace(Name="WillowPlayerPawn_CoOp", ObjectFlags=0, Weapon=sabre)
                self.assertIsNone(
                    combat.on_player_take_damage_pre(co_op_pawn, guard_args, None, lambda *a: guard_re_calls.append(a))
                )
                self.assertTrue(secondary_guard.is_guarding(sabre))
                world.pawn.Weapon = phaseblade
                secondary_lifecycle.tick_secondary_attacks()
                self.assertFalse(secondary_guard.is_guarding())
                world.pawn.Weapon = sabre
                secondary_guard._GUARD_COOLDOWN_UNTIL = 0.0
                secondary_lifecycle.reset_secondary_state()

                # 4I. Secondary Attack 2 — THROW (Bandit / Torgue): the blade leaves your hand for
                #     200% of its normal LMB hit, arcs by weight, rebounds on impact or arc expiry,
                #     and locks Primary Fire while airborne.
                world.pawn.Weapon = cleaver
                bl_inst, bl_card = resolver.resolve_melee_weapon(cleaver, sync_weapon_props=False)
                self.assertEqual(bl_card.secondary_attack.value, "throw")
                self.assertAlmostEqual(bl_card.throw_weight_kg, compute_throw_weight_kg(bl_inst))
                self.assertGreater(bl_card.throw_range_m, 0.0)
                self.assertGreater(bl_card.throw_arc_height_m, 0.0)
                self.assertGreater(bl_card.throw_flight_time_sec, 0.0)

                # Heavier part builds throw shorter/higher than lighter ones (manufacturer mass)
                light_parts = MeleeWeaponParts("Blade_Bandit_Machete", "Grip_Maliwan", "Acc_None", "Elem_None")
                heavy_parts = MeleeWeaponParts(
                    "Blade_Torgue_BlastCleaver", "Grip_Torgue", "Acc_HeavySpine", "Elem_Explosive"
                )
                light_w = compute_throw_weight_kg(MeleeWeaponInstance(1, 50, Rarity.RARE, light_parts))
                heavy_w = compute_throw_weight_kg(MeleeWeaponInstance(2, 50, Rarity.RARE, heavy_parts))
                self.assertLess(light_w, heavy_w)
                light_card = compute_card_stats(MeleeWeaponInstance(1, 50, Rarity.RARE, light_parts))
                heavy_card = compute_card_stats(MeleeWeaponInstance(2, 50, Rarity.RARE, heavy_parts))
                self.assertGreater(light_card.throw_range_m, heavy_card.throw_range_m)
                self.assertLess(light_card.throw_arc_height_m, heavy_card.throw_arc_height_m)

                # Maliwan never throws nor guards
                self.assertFalse(secondary_throw.begin_throw(phaseblade, sabre_inst_g, phase_card))
                self.assertFalse(secondary_throw.is_weapon_thrown(phaseblade))

                world.caused_damages.clear()
                world.caused_radius_damages.clear()
                combat.pose._LAST_SWING_AT = 0.0
                world.played_custom_anims.clear()
                # RMB dispatch: the throw starts with a real Bladed_Melee release motion
                self.assertTrue(combat._try_melee_secondary_attack(cleaver, bl_inst, bl_card))
                self.assertEqual(len(world.played_custom_anims), 1)
                self.assertEqual(world.played_custom_anims[0][1], "Bladed_Melee")
                # ...and a blade already in flight cannot be thrown again
                self.assertFalse(combat._try_melee_secondary_attack(cleaver, bl_inst, bl_card))
                self.assertTrue(secondary_throw.is_weapon_thrown(cleaver))
                live_state = secondary_lifecycle.describe_secondary_state()
                self.assertTrue(live_state["thrown"])
                self.assertEqual(live_state["throw_phase"], "outbound")
                self.assertIn("Throw", live_state["detail"])
                # A real flying actor carries the weapon's own mesh: attached to the pawn, using
                # the held weapon's SkeletalMesh + material instances (Tediore throw visual).
                self.assertTrue(live_state["thrown_visual"])
                self.assertTrue(secondary_vfx.has_thrown_blade_visual())
                self.assertEqual(len(world.attached_components), 1)
                self.assertEqual(len(world.thrown_blade_visuals), 1)
                blade_vis = world.thrown_blade_visuals[0]
                self.assertIs(blade_vis["mesh"], world.thrown_blade_source_mesh)
                self.assertIs(blade_vis["materials"][0], world.thrown_blade_source_material)
                self.assertEqual(blade_vis["location"], (8.0, 0.0, 64.0))
                self.assertIsNotNone(blade_vis.get("scale"), "the thrown blade copies the mesh scale")
                # The release swing still happens in the hand; the held blade is hidden once the
                # hands drop (no double blade, empty hand while it flies).
                self.assertFalse(cleaver.Mesh._state["hidden"])
                self.assertEqual(combat.active_first_person_pose(), "", "hands stay up for the release")

                # One clean flight frame with no enemies: the actor moves along the arc, tumbles,
                # and the first-person hands drop out of frame after the release swing.
                saved_pawn_list = world.world_info.PawnList
                world.world_info.PawnList = None
                world.next_center_trace_override = None
                secondary_throw._THROW.started_at -= 0.20
                secondary_lifecycle.tick_secondary_attacks()
                world.world_info.PawnList = saved_pawn_list
                self.assertGreaterEqual(blade_vis["moves"], 2)
                self.assertGreater(blade_vis["location"][0], 100.0)
                self.assertGreater(blade_vis["pitch"], 0, "the thrown blade must tumble")
                self.assertEqual(combat.active_first_person_pose(), combat.POSE_THROWN)
                self.assertLess(world.arms_skel_mesh.Origin.Z, -40.0)
                self.assertTrue(cleaver.Mesh._state["hidden"])
                # LMB is locked out while the blade is airborne — no swing, no damage, no anim
                world.played_custom_anims.clear()
                self.assertIs(
                    combat.on_weapon_instant_fire_pre(cleaver, None, None, None),
                    world.block_sentinel,
                )
                self.assertIsNone(combat.on_weapon_shared_fire_post(cleaver, None, None, None))
                self.assertEqual(world.caused_damages, [])
                self.assertEqual(world.played_custom_anims, [])

                # First arc segment connects with the target dummy -> exact 200% LMB damage
                throw_ctx = combat.read_player_melee_context(world.pawn, world.pc)
                expected_throw = compute_attack_damage(bl_card, throw_ctx, damage_scale=THROW_DAMAGE_MULTIPLIER)
                self.assertEqual(THROW_DAMAGE_MULTIPLIER, 1.0)
                world.next_center_trace_override = (dummy_pawn, world._vec(210.0, 0.0, 95.0), "None")
                secondary_lifecycle.tick_secondary_attacks()
                self.assertEqual(len(world.caused_damages), 1)
                self.assertIs(world.caused_damages[0]["Target"], dummy_pawn)
                self.assertAlmostEqual(world.caused_damages[0]["DamageAmount"], float(expected_throw.final_hit_damage))
                # The thrown blade deals the same direct damage as a primary hit.
                self.assertEqual(
                    expected_throw.final_hit_damage,
                    compute_attack_damage(bl_card, throw_ctx).final_hit_damage,
                )
                # Torgue's thrown slab detonates on impact too
                self.assertEqual(len(world.caused_radius_damages), 1)
                self.assertAlmostEqual(
                    world.caused_radius_damages[0]["DamageAmount"],
                    float(expected_throw.splash_hit_damage),
                )
                # The blade rebounds on impact and locks LMB until it is caught.
                self.assertEqual(secondary_throw._THROW.phase, "return")
                self.assertTrue(secondary_throw.is_weapon_thrown(cleaver))
                self.assertTrue(secondary_vfx.has_thrown_blade_visual())
                self.assertEqual(blade_vis["location"], (210.0, 0.0, 95.0), "rebound starts at the strike point")
                moves_before_return = blade_vis["moves"]
                throw_state = secondary_throw._THROW
                catch_point = secondary_throw._player_grab_point(throw_state)
                self.assertIsNotNone(catch_point)
                # Simulate 0.15 s of flight (the frame hook can run twice in one millisecond).
                throw_state.last_return_at -= 0.15
                secondary_lifecycle.tick_secondary_attacks()
                self.assertTrue(secondary_throw.is_weapon_thrown(cleaver))
                self.assertGreaterEqual(blade_vis["moves"], moves_before_return + 1)
                self.assertLess(blade_vis["location"][0], 210.0, "the blade flies back to the hand")
                self.assertGreater(blade_vis["location"][0], catch_point[0], "...but does not teleport past it")

                # 4I-1B. The rebound homes on the player's CURRENT position, Minecraft-trident
                #        style: move the pawn while the blade is flying back and it follows.
                before_move = blade_vis["location"]
                saved_loc = world.pawn.Location
                world.pawn.Location = world._vec(900.0, -400.0, 64.0)
                try:
                    for _ in range(6):
                        throw_state.last_return_at -= 0.15
                        secondary_lifecycle.tick_secondary_attacks()
                    after_move = blade_vis["location"]
                    self.assertGreater(after_move[0], before_move[0] + 100.0, "blade must chase the moved player")
                    self.assertLess(after_move[1], before_move[1], "and track the new direction")
                finally:
                    world.pawn.Location = saved_loc
                for _ in range(80):
                    if not secondary_throw.is_weapon_thrown(cleaver):
                        break
                    secondary_throw._THROW.last_return_at -= 0.15
                    secondary_lifecycle.tick_secondary_attacks()
                self.assertFalse(secondary_throw.is_weapon_thrown(cleaver))
                # Caught: the flying actor is detached/hidden and the hands come back.
                self.assertFalse(secondary_vfx.has_thrown_blade_visual())
                self.assertEqual(len(world.detached_components), 1)
                self.assertTrue(blade_vis["hidden"])
                self.assertFalse(cleaver.Mesh._state["hidden"], "the held blade reappears on catch")
                self.assertEqual(combat.active_first_person_pose(), "")
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Z, 3.0)
                self.assertEqual(world.played_custom_anims, [])

                # 4I-2. A throw that touches nothing must NOT stop dead in mid-air: past its
                #       planned arc it keeps falling (the "off a cliff" case) and only the fall /
                #       time fail-safes recall it. No damage is dealt along the way.
                world.caused_damages.clear()
                world.caused_radius_damages.clear()
                self.assertTrue(secondary_throw.begin_throw(cleaver, bl_inst, bl_card))
                # Aim away from every enemy so the swept-segment check finds nothing
                original_rot = world.pc.Rotation
                saved_walls = world.wall_blocked_names
                world.pc.Rotation = FakeWrappedStruct("Rotator", Pitch=0, Yaw=16384, Roll=0)
                world.next_center_trace_override = None
                world.wall_blocked_names = set()
                saved_pawns_4i2 = world.world_info.PawnList
                world.world_info.PawnList = None
                try:
                    st = secondary_throw._THROW
                    planned = st.plan.flight_time_sec
                    outbound_steps = 0
                    while (
                        outbound_steps < 400
                        and secondary_throw._THROW is not None
                        and secondary_throw._THROW.phase == "outbound"
                    ):
                        secondary_throw._THROW.started_at -= 0.10
                        outbound_steps += 1
                        secondary_lifecycle.tick_secondary_attacks()
                    self.assertEqual(world.caused_damages, [])
                    self.assertIsNotNone(secondary_throw._THROW)
                    self.assertEqual(secondary_throw._THROW.phase, "return")
                    self.assertEqual(secondary_throw._THROW.last_return_reason, "fell out of the world")
                    # ...and it really did fly far past the planned arc before giving up.
                    self.assertGreater(outbound_steps * 0.10, planned + 2.0)
                    for _ in range(80):
                        if not secondary_throw.is_any_weapon_thrown():
                            break
                        secondary_throw._THROW.last_return_at -= 0.20
                        secondary_lifecycle.tick_secondary_attacks()
                    self.assertFalse(secondary_throw.is_any_weapon_thrown())
                    self.assertEqual(world.caused_damages, [])
                finally:
                    world.pc.Rotation = original_rot
                    world.wall_blocked_names = saved_walls
                    world.world_info.PawnList = saved_pawns_4i2

                # 4I-5. Non-solid engine actors (and the thrower themselves) must never stop the
                #       blade: BL2's weapon trace reports volumes/triggers and the player's own
                #       arms mesh, which is not a valid collision target.
                world.pawn.Weapon = cleaver
                self.assertTrue(secondary_throw.begin_throw(cleaver, bl_inst, bl_card))
                try:
                    ghost = types.SimpleNamespace(
                        Name="PhysicsVolume_9", Class=types.SimpleNamespace(Name="PhysicsVolume")
                    )
                    world.next_center_trace_override = (ghost, world._vec(40.0, 0.0, 64.0), "None")
                    secondary_lifecycle.tick_secondary_attacks()
                    self.assertEqual(secondary_throw._THROW.phase, "outbound")
                    # ...and a hit right at the release point (own arms/weapon) is ignored too.
                    world.next_center_trace_override = (
                        world.pawn,
                        world._vec(2.0, 0.0, 64.0),
                        "None",
                    )
                    secondary_lifecycle.tick_secondary_attacks()
                    self.assertEqual(secondary_throw._THROW.phase, "outbound")
                    self.assertEqual(world.caused_damages, [])
                finally:
                    world.next_center_trace_override = None
                    secondary_lifecycle.reset_secondary_state()

                # 4I-7. Reaching the end of the planned arc must NOT teleport the blade onto a
                #       probed ground point (that read as "it stops in mid-air, then jumps to the
                #       ground and comes back"): it keeps flying and lets the trace land it.
                world.pawn.Weapon = cleaver
                self.assertTrue(secondary_throw.begin_throw(cleaver, bl_inst, bl_card))
                saved_pawns_4i7 = world.world_info.PawnList
                world.world_info.PawnList = None
                try:
                    st7 = secondary_throw._THROW
                    st7.started_at -= st7.plan.flight_time_sec + 0.01
                    vis7 = world.thrown_blade_visuals[-1]
                    world.next_center_trace_override = (
                        world.wall_actor,
                        world._vec(50.0, 0.0, -1053.0),
                        "None",
                    )
                    secondary_lifecycle.tick_secondary_attacks()
                    self.assertEqual(secondary_throw._THROW.phase, "outbound")
                    self.assertTrue(secondary_throw._THROW.arc_probe_logged)
                    self.assertGreater(vis7["location"][2], -500.0, "the blade must not snap down to the ground")
                    self.assertEqual(world.caused_damages, [])
                finally:
                    world.next_center_trace_override = None
                    world.world_info.PawnList = saved_pawns_4i7
                    secondary_lifecycle.reset_secondary_state()

                # 4I-6. A real solid hit still ends the throw exactly where it lands.
                world.pawn.Weapon = cleaver
                self.assertTrue(secondary_throw.begin_throw(cleaver, bl_inst, bl_card))
                try:
                    solid = world.wall_actor
                    world.next_center_trace_override = (
                        solid,
                        world._vec(300.0, 0.0, 64.0),
                        "None",
                    )
                    secondary_lifecycle.tick_secondary_attacks()
                    self.assertEqual(secondary_throw._THROW.phase, "return")
                    self.assertEqual(secondary_throw._THROW.last_return_reason, "impact")
                finally:
                    world.next_center_trace_override = None
                    secondary_lifecycle.reset_secondary_state()
                world.pawn.Weapon = cleaver
                # 4I-3. A physical Bandit Machete throw hits for 200% with no splash at all.
                world.ground_pickups.clear()
                commands.spawn_single_command("bandit", "purple", "")
                bandit_wpn = world.ground_pickups[-1].Inventory
                bandit_inst, bandit_card = resolver.resolve_melee_weapon(bandit_wpn, sync_weapon_props=False)
                self.assertEqual(bandit_card.secondary_attack.value, "throw")
                self.assertEqual(bandit_card.splash_damage, 0)
                world.pawn.Weapon = bandit_wpn
                world.caused_damages.clear()
                world.caused_radius_damages.clear()
                self.assertTrue(secondary_throw.begin_throw(bandit_wpn, bandit_inst, bandit_card))
                bandit_ctx = combat.read_player_melee_context(world.pawn, world.pc)
                expected_bandit = compute_attack_damage(bandit_card, bandit_ctx, damage_scale=THROW_DAMAGE_MULTIPLIER)
                world.next_center_trace_override = (dummy_pawn, world._vec(210.0, 0.0, 95.0), "None")
                secondary_lifecycle.tick_secondary_attacks()
                self.assertEqual(len(world.caused_damages), 1)
                self.assertAlmostEqual(
                    world.caused_damages[0]["DamageAmount"],
                    float(expected_bandit.final_hit_damage),
                )
                self.assertEqual(world.caused_radius_damages, [])
                self.assertTrue(secondary_vfx.has_thrown_blade_visual())
                secondary_lifecycle.reset_secondary_state()
                self.assertFalse(secondary_vfx.has_thrown_blade_visual())

                # 4I-4. The throw follows the camera pitch (and gravity): aim up -> the blade
                # leaves upward, aim down -> it leaves downward, always on an arc.
                world.pawn.Weapon = bandit_wpn
                saved_rot = world.pc.Rotation
                try:
                    world.pc.Rotation = FakeWrappedStruct("Rotator", Pitch=4096, Yaw=0, Roll=0)
                    self.assertTrue(secondary_throw.begin_throw(bandit_wpn, bandit_inst, bandit_card))
                    up_throw = secondary_throw._THROW
                    flat_throw = build_throw_flight_plan(bandit_card.throw_weight_kg)
                    self.assertGreater(up_throw.plan.launch_pitch_deg, flat_throw.launch_pitch_deg)
                    up_xyz = secondary_throw._throw_point(up_throw, 0.10)
                    self.assertGreater(up_xyz[2], up_throw.origin_xyz[2])
                    secondary_lifecycle.reset_secondary_state()

                    world.pc.Rotation = FakeWrappedStruct("Rotator", Pitch=-4096, Yaw=0, Roll=0)
                    self.assertTrue(secondary_throw.begin_throw(bandit_wpn, bandit_inst, bandit_card))
                    down_throw = secondary_throw._THROW
                    self.assertLess(down_throw.plan.launch_pitch_deg, flat_throw.launch_pitch_deg)
                    down_xyz = secondary_throw._throw_point(down_throw, 0.10)
                    self.assertLess(down_xyz[2], down_throw.origin_xyz[2])
                finally:
                    world.pc.Rotation = saved_rot
                    secondary_lifecycle.reset_secondary_state()

                world.pawn.Weapon = cleaver
                idle_state = secondary_lifecycle.describe_secondary_state()
                self.assertNotIn("thrown", idle_state)

                # 4J. Secondary attack state is part of the item card payload, and Maliwan shows none
                cleaver_payload = build_item_card_payload(bl_card)
                self.assertTrue(any("RMB Throw: 100% Damage" in line for line in cleaver_payload.fun_stats_lines))
                sabre_payload = build_item_card_payload(sabre_card_g)
                self.assertTrue(any("RMB Hold: Guard" in line for line in sabre_payload.fun_stats_lines))
                self.assertFalse(any("RMB" in line for line in build_item_card_payload(phase_card).fun_stats_lines))

                # 4K. The frame driver must run off a tick that actually fires in BL2.
                #     `Engine.GameViewportClient:Tick` is not a UFunction on every build, so the
                #     same body is mirrored on `WillowPlayerController:PlayerTick` (once per frame).
                runtime_mod = melee_arsenal.runtime
                self.assertIn(runtime_mod.hook_player_tick, runtime_mod.ALL_HOOKS)
                self.assertIn(runtime_mod.hook_viewport_tick, runtime_mod.ALL_HOOKS)
                # RMB release must not depend on the tick ever running: registered at load.
                self.assertIn(secondary_guard.on_melee_weapon_stop_zoom_pre, runtime_mod.ALL_HOOKS)
                self.assertIn(secondary_guard.on_melee_weapon_client_stop_zoom_pre, runtime_mod.ALL_HOOKS)
                runtime_mod._last_restored_pawn_key = "mock.pawn"
                ticks_before = runtime_mod._tick_counter
                with mock.patch.object(runtime_mod.time, "monotonic", return_value=1.0):
                    runtime_mod._last_frame_tick_at = 0.0
                    runtime_mod.hook_player_tick(None, None, None, None)
                    self.assertEqual(runtime_mod._tick_counter, ticks_before + 1)
                    # Both tick hooks at one timestamp must only run the body once.
                    runtime_mod.hook_viewport_tick(None, None, None, None)
                    self.assertEqual(runtime_mod._tick_counter, ticks_before + 1)

                # 4N. The map-restore pass keys the pawn by path name, not wrapper identity: an
                #     `id()` that churns re-ran the whole restore (and reset the secondary state,
                #     deleting an in-flight blade) about a second into a throw.
                runtime_mod._last_restored_pawn_key = "MockWorld.WillowPlayerPawn_0"
                runtime_mod._map_restore_ready_at = 0.0
                runtime_mod._tick_counter = 119
                runtime_mod._last_frame_tick_at = 0.0
                runtime_mod.hook_player_tick(None, None, None, None)
                self.assertEqual(runtime_mod._map_restore_ready_at, 0.0, "a stable pawn must not re-restore")

                # A live throw must survive wrapper churn on the *same* pawn, but a genuine
                #     pawn/map change must cancel it before sidecar restoration runs.
                world.pawn.Weapon = cleaver
                self.assertTrue(secondary_throw.begin_throw(cleaver, bl_inst, bl_card))
                try:
                    runtime_mod._last_restored_pawn_key = "MockWorld.WillowPlayerPawn_0"
                    runtime_mod._map_restore_ready_at = 0.0
                    runtime_mod._tick_counter = 59
                    runtime_mod._last_frame_tick_at = 0.0
                    runtime_mod.hook_player_tick(None, None, None, None)
                    self.assertTrue(
                        secondary_throw.is_any_weapon_thrown(),
                        "same-pawn wrapper churn must not delete a blade that is still in flight",
                    )
                    self.assertTrue(secondary_vfx.has_thrown_blade_visual())

                    runtime_mod._last_restored_pawn_key = "some.other.pawn"
                    runtime_mod._map_restore_ready_at = 0.0
                    runtime_mod._tick_counter = 119
                    runtime_mod._last_frame_tick_at = 0.0
                    runtime_mod.hook_player_tick(None, None, None, None)
                    self.assertFalse(
                        secondary_throw.is_any_weapon_thrown(),
                        "a pawn or map change must cancel the active throw before restore",
                    )
                    self.assertFalse(secondary_vfx.has_thrown_blade_visual())
                finally:
                    secondary_lifecycle.reset_secondary_state()
                    runtime_mod._last_restored_pawn_key = "MockWorld.WillowPlayerPawn_0"
                    runtime_mod._map_restore_ready_at = 0.0

                # 4L. Self-healing: a finished throw/guard can never leave the hands moved or a
                #     copy of the blade hanging in the air — even if a state ended abnormally.
                secondary_guard._GUARD = None
                secondary_throw._THROW = None
                self.assertTrue(combat.apply_first_person_pose(world.pc, combat.POSE_GUARD))
                self.assertGreater(world.arms_skel_mesh.RotOrigin.Pitch, 1000)

                # 4L-1. `Guard Blade Tilt (%)` is the in-game escape hatch if the RotOrigin
                #       sign convention reads backwards: -100% leans the blade the other way.
                self.assertIn(runtime_mod.opt_guard_pose, runtime_mod.ALL_OPTIONS)
                tilt_blade_mesh = world.pawn.Weapon.Mesh
                runtime_mod._on_guard_pose_changed(None, -100)
                combat.update_first_person_pose(world.pc)
                self.assertGreater(tilt_blade_mesh.RotOrigin.Yaw, 0, "negative tilt must lean the blade outward")
                runtime_mod._on_guard_pose_changed(None, 100)
                combat.update_first_person_pose(world.pc)
                self.assertLess(tilt_blade_mesh.RotOrigin.Yaw, 0)
                # The hands never slide sideways at either setting.
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Yaw, -16384)

                # 4L-2. Guarding over and over must never creep: every release restores the exact
                #       transform the hands had before the guard (players reported the view model
                #       drifting further up and to the left with every block).
                combat.reset_first_person_pose("rest before drift test")
                rest_pitch = world.arms_skel_mesh.RotOrigin.Pitch
                rest_yaw = world.arms_skel_mesh.RotOrigin.Yaw
                rest_z = world.arms_skel_mesh.Origin.Z
                self.assertEqual(rest_pitch, 0)
                for _ in range(8):
                    self.assertTrue(combat.apply_first_person_pose(world.pc, combat.POSE_GUARD))
                    combat.update_first_person_pose(world.pc)
                    combat.apply_first_person_pose(world.pc, combat.POSE_GUARD)
                    combat.update_first_person_pose(world.pc)
                    self.assertFalse(
                        combat.capture_first_person_rest_transform(),
                        "a live pose must never be captured as the rest transform",
                    )
                    combat.reset_first_person_pose("release")
                    self.assertEqual(world.arms_skel_mesh.RotOrigin.Pitch, rest_pitch)
                    self.assertEqual(world.arms_skel_mesh.RotOrigin.Yaw, rest_yaw)
                    self.assertAlmostEqual(world.arms_skel_mesh.Origin.Z, rest_z)
                    self.assertEqual(combat.active_first_person_pose(), "")
                    # The blade mesh must come back too — otherwise it would stay cocked over.
                    self.assertEqual(world.pawn.Weapon.Mesh.RotOrigin.Pitch, 0)
                    self.assertEqual(world.pawn.Weapon.Mesh.RotOrigin.Yaw, 0)
                    self.assertEqual(world.pawn.Weapon.Mesh.RotOrigin.Roll, 0)

                self.assertTrue(secondary_vfx.spawn_thrown_blade(world.pawn, cleaver, (10.0, 0.0, 64.0)))
                self.assertTrue(secondary_vfx.has_thrown_blade_visual())
                runtime_mod._last_frame_tick_at = 0.0
                runtime_mod.hook_player_tick(None, None, None, None)
                self.assertFalse(secondary_vfx.has_thrown_blade_visual(), "orphan blade must be removed")
                self.assertEqual(combat.active_first_person_pose(), "")
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Pitch, 0)
                self.assertEqual(world.arms_skel_mesh.RotOrigin.Yaw, -16384)
                self.assertAlmostEqual(world.arms_skel_mesh.Origin.Z, 3.0)

                # 5. Item Card GFx Hooks — MUST NOT call AdjustHeight()!
                top_rows_written: dict[int, tuple] = {}
                gfx_card = types.SimpleNamespace(
                    SetTopStat=lambda idx, lbl, val, arr, aux, ic: top_rows_written.__setitem__(
                        idx, (lbl, val, arr, ic)
                    ),
                    SetFunStats=lambda txt: None,
                    SetTitle=lambda *a: None,
                    AdjustHeight=lambda: (_ for _ in ()).throw(
                        AssertionError("AdjustHeight() must NEVER be called — it shrinks inventory cards!")
                    ),
                )
                ui_item_card.on_set_item_card_ex_pre(
                    gfx_card,
                    types.SimpleNamespace(InventoryItem=phaseblade, CompareAgainstInventoryItem=sabre),
                    None,
                    None,
                )
                ui_item_card.on_set_item_card_ex_post(gfx_card, None, None, None)
                self.assertEqual(top_rows_written[0][0], "Damage")
                self.assertEqual(top_rows_written[1][0], "Range")
                self.assertEqual(top_rows_written[2][0], "Swing Speed")
                self.assertEqual(top_rows_written[3][0], "Burn Dmg/s")
                self.assertEqual(top_rows_written[4][0], "Effect Chance")

                # 5B. Title colour must never render White for a higher-rarity Melee Weapon: the engine
                # can still hand us the stale Common level for items we re-assembled after init.
                title_calls: list[tuple] = []
                title_card = types.SimpleNamespace(
                    SetTitle=lambda *a: title_calls.append(a),
                    SetTopStat=lambda *a: None,
                    SetFunStats=lambda *a: None,
                )
                ui_item_card.on_set_item_card_ex_pre(
                    title_card,
                    types.SimpleNamespace(InventoryItem=phaseblade, CompareAgainstInventoryItem=None),
                    None,
                    None,
                )
                ui_item_card.on_set_title_pre(
                    title_card,
                    types.SimpleNamespace(
                        Manufacturer="Maliwan",
                        Rarity=1,
                        TypeIcon="pistol",
                        ElementalIcon="",
                        bIsReadied=False,
                    ),
                    None,
                    None,
                )
                self.assertEqual(len(title_calls), 1)
                self.assertEqual(title_calls[0][2], 4)  # promoted 1 (White) -> 4 (Purple)
                ui_item_card.on_set_item_card_ex_post(title_card, None, None, None)

                # 6. Sidecar Save/Restore
                saved = persistence_bridge.snapshot_player_melee_inventory(pc=world.pc)
                self.assertEqual(saved, 3)
                world.backpack.clear()
                restored = persistence_bridge.restore_player_melee_inventory(pc=world.pc)
                self.assertEqual(restored, 3)
                for _ in range(30):
                    resolver.tick_spawner_restore()
                self.assertEqual(len(world.backpack), 3)
