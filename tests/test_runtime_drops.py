from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.runtime_mock_support import FakeWrappedStruct, MockBL2World, SDK_MODS_DIR


class TestRuntimeDrops(unittest.TestCase):
    def test_world_drop_pools_hotfix_recovery_and_natural_drop_finalization(self) -> None:
        if str(SDK_MODS_DIR) not in sys.path:
            sys.path.insert(0, str(SDK_MODS_DIR))

        with tempfile.TemporaryDirectory() as tmp:
            world = MockBL2World(Path(tmp))
            world.populate_custom_inkblade_packages()
            sdk_mods = world.make_sdk_modules()

            for mod_name in list(sys.modules):
                if mod_name == "melee_arsenal" or mod_name.startswith("melee_arsenal.runtime"):
                    sys.modules.pop(mod_name, None)

            with mock.patch.dict(sys.modules, sdk_mods):
                import melee_arsenal  # type: ignore[import-not-found]
                from melee_arsenal.core.enums import Manufacturer, Rarity
                from melee_arsenal.runtime import definitions, loot_pools, persistence_bridge, resolver, state

                self.assertIsNotNone(melee_arsenal.mod)
                self.assertTrue(definitions.ensure_definitions_built())
                self.assertTrue(loot_pools.sync_loot_pools())

                # 1. Verify ALL custom melee pools stripped donor `Gamestage_02`/`Gamestage_99` gates & `Quantity=1`
                self.assertGreaterEqual(len(state.STATE.custom_melee_pools), 7)
                for pname, pobj in state.STATE.custom_melee_pools.items():
                    self.assertIsNone(pobj.MinGameStageRequirement, f"{pname} kept MinGameStageRequirement!")
                    self.assertIsNone(pobj.MaxGameStageRequirement, f"{pname} kept MaxGameStageRequirement!")
                    self.assertFalse(pobj.bAutoReadyItems)
                    self.assertAlmostEqual(pobj.Quantity.BaseValueConstant, 1.0)
                for dpool in state.STATE.balance_drop_pools.values():
                    self.assertIsNone(dpool.MinGameStageRequirement)
                    self.assertIsNone(dpool.MaxGameStageRequirement)

                # 2. Verify `Pool_Weapons_Melee_Epic` excludes White Commons (`01_Common`)
                epic_sub = state.STATE.custom_melee_pools["Pool_Weapons_Melee_Epic"]
                epic_child_names = [it.ItmPoolDefinition.Name for it in epic_sub.BalancedItems]
                self.assertNotIn("Pool_Weapons_Melee_01_Common", epic_child_names)
                self.assertIn("Pool_Weapons_Melee_04_Rare", epic_child_names)
                self.assertIn("Pool_Weapons_Melee_05_VeryRare", epic_child_names)

                # 3. Verify proportional 16.13% (`100 / 620`) class share across world, enemy, chest, locker & shop pools
                report = loot_pools.inspect_loot_pools(resync_first=False)
                self.assertTrue(report["subpool_stage_gates_cleared"])
                # 4 world + 4 real enemy death-drop tiers + 4 class-weighted + chest/container/shop
                self.assertEqual(report["injected_pools"], 14)
                self.assertAlmostEqual(report["avg_weapon_share_pct"], 16.13, places=1)

                # Standard enemies roll `Pool_GunsAndGear_<tier>` on death, so Melee Weapons must be
                # present there (mapped to the matching rarity sub-pool), not just in the AI-weighted pools.
                for tier, melee_key in (
                    ("01_Common", "01_Common"),
                    ("02_Uncommon", "02_Uncommon"),
                    ("04_Rare", "04_Rare"),
                    ("05_VeryRare", "05_VeryRare"),
                ):
                    enemy_tier_pool = world.find_object(
                        "ItemPoolDefinition", f"GD_Itempools.EnemyDropPools.Pool_GunsAndGear_{tier}"
                    )
                    tier_melee = [
                        it
                        for it in enemy_tier_pool.BalancedItems
                        if "Pool_Weapons_Melee_" in it.ItmPoolDefinition._path_name()
                    ]
                    self.assertEqual(len(tier_melee), 1, f"Pool_GunsAndGear_{tier} has no melee entry!")
                    self.assertTrue(
                        tier_melee[0].ItmPoolDefinition._path_name().endswith(f"Pool_Weapons_Melee_{melee_key}")
                    )

                # Vendors already stocked before injection must be re-rolled once so shelves show melee
                self.assertGreaterEqual(world.vendor_resets, 1)

                # Verify `Pool_GunsAndGear_WeightedPistols` scaled against weapon weight (100), NOT shield weight (85)!
                gg_pool = world.find_object(
                    "ItemPoolDefinition", "GD_Itempools.EnemyDropPools.Pool_GunsAndGear_WeightedPistols"
                )
                gg_melee = [
                    it for it in gg_pool.BalancedItems if "Pool_Weapons_Melee_" in it.ItmPoolDefinition._path_name()
                ]
                self.assertEqual(len(gg_melee), 1)
                self.assertAlmostEqual(gg_melee[0].Probability.BaseValueConstant, 100.0 * (100.0 / 520.0), places=3)

                # Verify `Pool_Locker_Items_SMGsAndPistols` evaluated `Weight_2_Uncommon(10) + Weight_3_Uncommoner(5) = 15`
                locker_pool = world.find_object(
                    "ItemPoolDefinition", "GD_Itempools.LootablePools.Pool_Locker_Items_SMGsAndPistols"
                )
                locker_melee = [
                    it for it in locker_pool.BalancedItems if "Pool_Weapons_Melee_" in it.ItmPoolDefinition._path_name()
                ]
                self.assertEqual(len(locker_melee), 1)
                self.assertAlmostEqual(locker_melee[0].Probability.BaseValueConstant, 15.0 * (100.0 / 520.0), places=3)

                # Verify AI `GD_ItempoolsEnemyUse` pool was NEVER touched!
                ai_pool = world.find_object(
                    "ItemPoolDefinition", "GD_ItempoolsEnemyUse.Weapons.Pool_Weapons_Pistols_EnemyUse"
                )
                self.assertEqual(len(ai_pool.BalancedItems), 1)

                # Simulate a hotfix removing Melee Arsenal from a vanilla pool after menu initialization.
                all_common = world.find_object(
                    "ItemPoolDefinition", "GD_Itempools.WeaponPools.Pool_Weapons_All_01_Common"
                )
                all_common.BalancedItems = [all_common.BalancedItems[0]]  # Wiped back to vanilla!
                self.assertEqual(len(all_common.BalancedItems), 1)
                # Map-load `sync_loot_pools()` restores `Pool_Weapons_Melee_01_Common` without duplicating other pools!
                self.assertTrue(loot_pools.sync_loot_pools())
                self.assertEqual(len(all_common.BalancedItems), 2)
                self.assertEqual(len(gg_pool.BalancedItems), 3)

                # 5. Verify `PLC_Melee_*` `WeightedParts` carry non-empty `Manufacturers` (`DefaultWeightIndex -> 1.0`) & `MaxGameStage=10000`
                mal_bal = state.STATE.balances[(Manufacturer.MALIWAN, Rarity.VERY_RARE)]
                self.assertEqual(mal_bal.Manufacturers[0].Grades[0].GameStageRequirement.MaxGameStage, 10000)
                mal_plc = mal_bal.RuntimePartListCollection
                init_table = list(mal_plc.ConsolidatedAttributeInitData)
                for wp in mal_plc.ElementalPartData.WeightedParts:
                    self.assertEqual(len(wp.Manufacturers), 1)
                    self.assertIsNone(wp.Manufacturers[0].Manufacturer)
                    self.assertEqual(init_table[wp.Manufacturers[0].DefaultWeightIndex].BaseValueConstant, 1.0)
                    self.assertEqual(init_table[wp.MaxGameStageIndex].BaseValueConstant, 10000.0)

                # 6. Simulate a 100% NATURAL enemy world drop (NO `_PENDING_DROP_INSTANCES`, `UniqueId=0`):
                # Native `SpawnBalancedInventoryFromPool` rolls `Balance_Melee_Maliwan_VERY_RARE` with `Grip_Bandit`,
                # base `Acc_Serrated` (needing Maliwan blade-fitted `__s3` twin!), and `Elem_Shock` at OP10 stage 90.
                natural_dd = FakeWrappedStruct(
                    "WeaponDefinitionData",
                    WeaponTypeDefinition=mal_bal.InventoryDefinition,
                    BalanceDefinition=mal_bal,
                    ManufacturerDefinition=mal_bal.Manufacturers[0].Manufacturer,
                    ManufacturerGradeIndex=90,
                    BodyPartDefinition=state.STATE.body_parts[Manufacturer.MALIWAN],
                    GripPartDefinition=state.STATE.grip_parts["Grip_Bandit"],
                    BarrelPartDefinition=state.STATE.blade_parts["Blade_Maliwan_Phaseblade"],
                    SightPartDefinition=None,
                    StockPartDefinition=None,
                    ElementalPartDefinition=state.STATE.element_parts["Elem_Shock"],
                    Accessory1PartDefinition=state.STATE.accessory_parts["Acc_Serrated"],
                    Accessory2PartDefinition=None,
                    MaterialPartDefinition=mal_plc.MaterialPartData.WeightedParts[0].Part,
                    PrefixPartDefinition=None,
                    TitlePartDefinition=None,
                    GameStage=90,
                    UniqueId=0,
                )
                natural_wpn = world._make_live_weapon(natural_dd)
                resolver.on_choose_random_name_parts_post(natural_wpn, None, None, None)

                # Must immediately finalize Shock VeryRare material (`Rarity == 4.0` Purple beam),
                # Maliwan-fitted `__s3` serrated edge twin, exact Prefix/Title, and non-zero `UniqueId`!
                self.assertTrue(
                    natural_wpn.DefinitionData.MaterialPartDefinition._path_name().endswith(
                        "Mat_Inkblade_Shock_VeryRare"
                    )
                )
                self.assertEqual(
                    natural_wpn.DefinitionData.MaterialPartDefinition.Rarity.BaseValueConstant,
                    4.0,
                )
                self.assertEqual(
                    natural_wpn.DefinitionData.Accessory1PartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_accessory_serrated_edge__s3",
                )
                self.assertIsNotNone(natural_wpn.DefinitionData.TitlePartDefinition)
                self.assertIsNotNone(natural_wpn.DefinitionData.PrefixPartDefinition)
                self.assertGreater(natural_wpn.DefinitionData.UniqueId, 0)
                self.assertEqual(natural_wpn.ReloadCnt, 100)

                # Natural drop persists cleanly in sidecar save/load
                world.backpack.clear()
                world.backpack.append(natural_wpn)
                self.assertEqual(persistence_bridge.snapshot_player_melee_inventory(pc=world.pc), 1)

                # Toggle Enable World Drops = False removes melee entries cleanly
                state.STATE.world_drops_enabled = False
                self.assertTrue(loot_pools.sync_loot_pools())
                self.assertEqual(len(all_common.BalancedItems), 1)
                self.assertFalse(state.STATE.pools_injected)
