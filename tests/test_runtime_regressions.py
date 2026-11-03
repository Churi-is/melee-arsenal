"""Focused regressions for lifecycle, identity, persistence, and shared attack policy.

These reuse the strict BL2 mock from `test_runtime_mock` but keep each scenario small enough to
fail with a precise assertion instead of burying the regression in the full-lifecycle test.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from dataclasses import replace
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
SDK_MODS_DIR = ROOT_DIR / "sdk_mods"
if str(SDK_MODS_DIR) not in sys.path:
    sys.path.insert(0, str(SDK_MODS_DIR))
if str(ROOT_DIR / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "tests"))

from test_runtime_mock import MockBL2World  # noqa: E402


@contextmanager
def runtime_env(custom: bool = False):
    with tempfile.TemporaryDirectory() as tmp:
        world = MockBL2World(Path(tmp))
        if custom:
            world.populate_custom_inkblade_packages()
        sdk_mods = world.make_sdk_modules()
        for mod_name in list(sys.modules):
            if mod_name == "melee_arsenal" or mod_name.startswith("melee_arsenal.runtime"):
                sys.modules.pop(mod_name, None)
        with mock.patch.dict(sys.modules, sdk_mods):
            import melee_arsenal  # noqa: F401
            from melee_arsenal.runtime import definitions
            assert definitions.ensure_definitions_built()
            yield world


class RuntimeRegressionTests(unittest.TestCase):
    def test_zero_id_snapshot_assigns_distinct_ids_and_restore_is_idempotent(self):
        with runtime_env() as world:
            from melee_arsenal.core.persistence import read_save_records, save_record_path
            from melee_arsenal.runtime import commands, persistence_bridge, resolver

            commands.spawn_single_command("hyperion", "purple", "fire")
            commands.spawn_single_command("jakobs", "purple", "")
            a = world.ground_pickups[-2].Inventory
            b = world.ground_pickups[-1].Inventory
            for wpn in (a, b):
                dd = wpn.DefinitionData
                dd.UniqueId = 0
                wpn.DefinitionData = dd
            world.backpack.extend([a, b])
            world.pc.SaveGameName = "Save0001.sav"

            self.assertEqual(persistence_bridge.snapshot_player_melee_inventory(pc=world.pc), 2)
            ids = {int(a.DefinitionData.UniqueId), int(b.DefinitionData.UniqueId)}
            self.assertEqual(len(ids), 2)
            self.assertTrue(all(i > 0 for i in ids))
            target = save_record_path(persistence_bridge.SAVES_DIR, "Save0001")
            records = read_save_records(target)
            self.assertEqual({int(k) for k in records if not k.startswith("_")}, ids)

            world.backpack.clear()
            self.assertEqual(persistence_bridge.restore_player_melee_inventory(pc=world.pc), 2)
            # A second restore while the first batch is pending must not duplicate it.
            self.assertEqual(persistence_bridge.restore_player_melee_inventory(pc=world.pc), 0)
            for _ in range(40):
                resolver.tick_spawner_restore()
            self.assertEqual(len(world.backpack), 2)
            persistence_bridge.acknowledge_restored_inventory(pc=world.pc)
            self.assertEqual(persistence_bridge.restore_player_melee_inventory(pc=world.pc), 0)
            self.assertEqual(len(world.backpack), 2)

    def test_legacy_zero_sidecar_migrates_without_dropping_other_records(self):
        with runtime_env() as world:
            from melee_arsenal.core.persistence import read_save_records, save_record_path, serialize_weapon
            from melee_arsenal.core.loot_roller import roll_melee_weapon
            from melee_arsenal.core.enums import Manufacturer, Rarity
            from melee_arsenal.core.persistence import write_save_records_atomic
            from melee_arsenal.runtime import persistence_bridge, resolver

            legacy = roll_melee_weapon(game_stage=30, rarity=Rarity.RARE, manufacturer=Manufacturer.HYPERION, seed="legacy", unique_id=1)
            other = roll_melee_weapon(game_stage=30, rarity=Rarity.RARE, manufacturer=Manufacturer.JAKOBS, seed="other", unique_id=424242)
            target = save_record_path(persistence_bridge.SAVES_DIR, "Save0002")
            rec0 = serialize_weapon(legacy)
            rec0["unique_id"] = 0
            rec0["carried_slot"] = "backpack"
            rec1 = serialize_weapon(other)
            rec1["carried_slot"] = "backpack"
            write_save_records_atomic(target, {"0": rec0, "424242": rec1})
            world.pc.SaveGameName = "Save0002.sav"

            self.assertEqual(persistence_bridge.restore_player_melee_inventory(pc=world.pc), 2)
            for _ in range(40):
                resolver.tick_spawner_restore()
            records = read_save_records(target)
            self.assertIn("424242", records)
            self.assertEqual(records["0"]["carried_slot"], "")
            positive = [int(k) for k in records if not k.startswith("_") and int(k) > 0]
            self.assertIn(424242, positive)
            self.assertEqual(len(positive), 2)
            self.assertEqual(len(world.backpack), 2)

    def test_wrapper_identity_helpers_and_ui_clear(self):
        with runtime_env():
            from melee_arsenal.runtime import combat, ui_item_card
            from melee_arsenal.runtime.object_identity import cache_key, same_uobject

            class Wrapper:
                def __init__(self, path):
                    self._path = path
                def _path_name(self):
                    return self._path

            w1 = Wrapper("MockWorld.Pawn_0")
            w2 = Wrapper("MockWorld.Pawn_0")
            self.assertIsNot(w1, w2)
            self.assertTrue(same_uobject(w1, w2))
            self.assertEqual(cache_key(w1), cache_key(w2))
            combat._PENDING_CRIT_FEEDBACK[cache_key(w1)] = 1.0
            combat._clear_pending_crit_feedback(w2)
            self.assertNotIn(cache_key(w1), combat._PENDING_CRIT_FEEDBACK)

            ui_item_card._ACTIVE_CARDS[cache_key(w1)] = object()
            ui_item_card.clear_active_card_overrides()
            self.assertFalse(ui_item_card._ACTIVE_CARDS)

    def test_disable_cancels_pending_grants_and_removes_loot_entries(self):
        with runtime_env():
            import melee_arsenal
            from melee_arsenal.core.enums import Manufacturer
            from melee_arsenal.core.loot_roller import roll_melee_weapon
            from melee_arsenal.core.enums import Rarity
            from melee_arsenal.runtime import loot_pools, resolver, state

            self.assertTrue(loot_pools.sync_loot_pools())
            self.assertTrue(state.STATE.pools_injected)
            inst = roll_melee_weapon(game_stage=30, rarity=Rarity.RARE, manufacturer=Manufacturer.BANDIT, seed="pending")
            resolver._PENDING_GRANTS[Manufacturer.BANDIT].append(inst)
            resolver._STAMPED_UIDS.add(inst.unique_id)
            self.assertTrue(resolver.has_pending_grants())

            melee_arsenal.runtime._on_mod_disable()
            self.assertFalse(resolver.has_pending_grants())
            self.assertFalse(resolver._STAMPED_UIDS)
            self.assertFalse(state.STATE.pools_injected)
            self.assertFalse(resolver.grant_was_stamped(inst.unique_id))

    def test_debug_spawn_rejects_incompatible_parts_and_unknown_tokens(self):
        with runtime_env() as world:
            from melee_arsenal.runtime import commands

            self.assertEqual(commands.spawn_single_command("hyperion", "purple", "explosive"), 0)
            self.assertEqual(commands.spawn_single_command("jakobs", "purple", "", "flux"), 0)
            self.assertEqual(commands.spawn_single_command("hyperion", "purple", "bogus"), 0)
            self.assertEqual(commands.spawn_single_command("hyperion", "purple", "fire", "bogus"), 0)
            self.assertEqual(commands.spawn_single_command("nobody", "purple"), 0)
            self.assertEqual(commands.spawn_all_five_archetypes("mythic"), 0)
            self.assertEqual(len(world.ground_pickups), 0)
            self.assertEqual(commands.spawn_single_command("maliwan", "purple", "fire", "coil"), 1)

    def test_shared_status_policy_and_zero_camera_shake(self):
        with runtime_env():
            from melee_arsenal.core.enums import Element
            from melee_arsenal.core.stats import compute_attack_damage, compute_card_stats
            from melee_arsenal.core.schemas import PlayerMeleeContext
            from melee_arsenal.runtime import combat

            # Force an elemental Maliwan card for the proc test.
            from melee_arsenal.core.loot_roller import roll_melee_weapon
            from melee_arsenal.core.enums import Manufacturer, Rarity
            inst = roll_melee_weapon(game_stage=30, rarity=Rarity.VERY_RARE, manufacturer=Manufacturer.MALIWAN, seed="status-fire")
            # Pick a fire variant deterministically by rebuilding parts if needed.
            from melee_arsenal.core.catalog import ELEMENT_PART_BY_TYPE
            from melee_arsenal.core.schemas import MeleeWeaponParts
            inst = replace(inst, parts=MeleeWeaponParts(
                blade_id=inst.parts.blade_id, grip_id=inst.parts.grip_id,
                accessory_id=inst.parts.accessory_id, element_id=ELEMENT_PART_BY_TYPE[Element.FIRE].part_id,
            ))
            card = compute_card_stats(inst)
            ctx = PlayerMeleeContext(melee_damage_scale=2.0, status_chance_scale=2.0, status_damage_scale=2.0)
            breakdown = compute_attack_damage(card, ctx)
            wpn = types.SimpleNamespace()
            with mock.patch.object(combat.random, "random", return_value=0.0):
                dt, proc = combat.select_attack_damage_type(wpn, card, breakdown)
            self.assertTrue(proc)
            self.assertEqual(wpn.BaseStatusEffectChanceModifier, 50.0)
            combat.sync_weapon_status_damage(wpn, breakdown)
            self.assertAlmostEqual(wpn.StatusEffectDamage, breakdown.effective_dot_dps)

            calls = []
            pc = types.SimpleNamespace(ClientPlayCameraAnim=lambda *a, **k: calls.append(a))
            canim = object()
            self.assertFalse(combat.play_camera_anim(pc, canim, 0.0, 1.0, 0.0, 0.0))
            self.assertFalse(calls)
            self.assertTrue(combat.play_camera_anim(pc, canim, 0.2, 1.0, 0.0, 0.0))
            self.assertEqual(len(calls), 1)
            self.assertAlmostEqual(calls[0][1], 0.2)

    def test_guard_toggle_disables_mitigation_but_not_pose(self):
        with runtime_env() as world:
            from melee_arsenal.runtime import secondary, state
            from melee_arsenal.core.loot_roller import roll_melee_weapon
            from melee_arsenal.core.enums import Manufacturer, Rarity
            from melee_arsenal.core.stats import compute_card_stats

            inst = roll_melee_weapon(game_stage=30, rarity=Rarity.VERY_RARE, manufacturer=Manufacturer.HYPERION, seed="guard")
            card = compute_card_stats(inst)
            commands = __import__("melee_arsenal.runtime.commands", fromlist=["spawn_single_command"])
            commands.spawn_single_command("hyperion", "purple", "")
            wpn = world.ground_pickups[-1].Inventory
            world.pawn.Weapon = wpn
            self.assertTrue(secondary.try_begin_guard(wpn, inst, card))
            args = types.SimpleNamespace(Damage=1000.0, DamageType=None, InstigatedBy=None, DamageCauser=None)
            state.STATE.guard_damage_reduction_enabled = True
            self.assertTrue(secondary.handle_guard_incoming_damage(world.pawn, args, None))
            state.STATE.guard_damage_reduction_enabled = False
            args.Damage = 1000.0
            self.assertFalse(secondary.handle_guard_incoming_damage(world.pawn, args, None))
            self.assertEqual(args.Damage, 1000.0)
            secondary.reset_secondary_state()

    def test_asset_library_registration_matches_frozen_manifest(self):
        with runtime_env(custom=True):
            from melee_arsenal.runtime import asset_library, definitions, state

            captured: dict[str, list[str]] = {}
            real = asset_library.register_assets_in_library
            def cap(class_name, assets, set_id=0):
                captured.setdefault(class_name, []).extend(asset_library.obj_path(a) for a in assets if a is not None)
                return real(class_name, assets, set_id)
            # Definitions are already built; re-register through the same path to capture order.
            definitions.register_assets_in_library = cap
            state.STATE.initialized = False
            self.assertTrue(definitions.ensure_definitions_built())
            manifest = json.loads((SDK_MODS_DIR / "melee_arsenal" / "data" / "asset_library_manifest.json").read_text(encoding="utf-8"))
            for cls, paths in manifest["libraries"].items():
                self.assertEqual(captured.get(cls, []), paths, f"{cls} registration order drifted")


if __name__ == "__main__":
    unittest.main()
