from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.runtime_mock_support import FakeWrappedStruct, MockBL2World, SDK_MODS_DIR


class TestRuntimeAssets(unittest.TestCase):
    def test_custom_inkblade_gestalt_variants_and_element_atlases(self) -> None:
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
                from melee_arsenal.core.enums import Rarity
                from melee_arsenal.core.parts.weapon import MeleeWeaponInstance, MeleeWeaponParts
                from melee_arsenal.runtime import commands, custom_assets, definitions, resolver, state

                self.assertEqual(melee_arsenal.__version__, "0.4.5")
                self.assertTrue(definitions.ensure_definitions_built())
                self.assertTrue(state.STATE.custom_assets_active)
                import json as _json

                _tracked_manifest = _json.loads(
                    (SDK_MODS_DIR / "melee_arsenal" / "data" / "inkblade_compiled_manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(state.STATE.custom_kit_revision, _tracked_manifest["kit_revision"])
                self.assertEqual(len(state.STATE.element_material_parts_by_rarity), 24)

                # Every Inkblade Material part carries its exact BL2 rarity level (1 White … 4 Purple):
                # the Material part alone decides card colour, loot-beam colour (`InventoryRarityLevel`)
                # and sell value, so a stale/Common clone would render every Melee Weapon white.
                for (elem_id, rarity), mat_obj in state.STATE.element_material_parts_by_rarity.items():
                    self.assertAlmostEqual(
                        custom_assets.get_part_rarity(mat_obj),
                        float(int(rarity)),
                        msg=f"{elem_id} {rarity.name} material has wrong Rarity!",
                    )
                    self.assertIsNone(mat_obj.Rarity.InitializationDefinition)
                    self.assertIsNone(mat_obj.Rarity.BaseValueAttribute)
                    self.assertAlmostEqual(mat_obj.Rarity.BaseValueScaleConstant, 1.0)

                # Verify equal-size UPK restage detection compares 129B UPK summary/GUID header
                stored_upk = Path(tmp) / "stored.upk"
                candidate_upk = Path(tmp) / "candidate.upk"
                stored_upk.write_bytes(b"A" * 200)
                candidate_upk.write_bytes(b"A" * 45 + b"B" * 16 + b"A" * 139)
                self.assertEqual(stored_upk.stat().st_size, candidate_upk.stat().st_size)
                self.assertTrue(custom_assets._upk_needs_restage(candidate_upk, stored_upk))
                self.assertFalse(custom_assets._upk_needs_restage(candidate_upk, candidate_upk))

                # Only the None accessory and element definitions are hidden; physical parts stay visible.
                self.assertTrue(state.STATE.accessory_parts["Acc_None"].bHiddenNoMesh)
                for acc_id, apart in state.STATE.accessory_parts.items():
                    if acc_id != "Acc_None":
                        self.assertFalse(apart.bHiddenNoMesh, f"{acc_id} cloned from hidden None donor!")
                for (_aid, _pkid), vpart in state.STATE.accessory_variant_parts.items():
                    self.assertFalse(vpart.bHiddenNoMesh)

                self.assertTrue(state.STATE.element_parts["Elem_None"].bHiddenNoMesh)
                for elem_id, epart in state.STATE.element_parts.items():
                    if elem_id != "Elem_None":
                        self.assertFalse(epart.bHiddenNoMesh, f"{elem_id} cloned from hidden None donor!")

                # Verify VERY_RARE (purple) PLCs exclude `Acc_None` (100% guaranteed physical accessory!)
                none_acc_obj = state.STATE.accessory_parts["Acc_None"]
                for (_mkr, rar), bal_obj in state.STATE.balances.items():
                    plc_obj = bal_obj.RuntimePartListCollection
                    acc_parts_in_plc = [r.Part for r in plc_obj.Accessory1PartData.WeightedParts]
                    if rar == Rarity.VERY_RARE:
                        self.assertGreater(len(acc_parts_in_plc), 0)
                        self.assertNotIn(none_acc_obj, acc_parts_in_plc)

                # Verify the horizontal item preview and full-slot framing settings.
                for wt_obj in state.STATE.maker_to_wt.values():
                    self.assertEqual(wt_obj.UIMeshRotation.Roll, -12015)
                    self.assertEqual(wt_obj.UIMeshRotation.Yaw, 4733)
                    self.assertFalse(wt_obj.bUIMeshRotationNeedsTransformFixup)
                    self.assertAlmostEqual(wt_obj.FocusRadius, 22.0)
                    self.assertAlmostEqual(wt_obj.MinimumVerticalPercentage, 0.0)
                    self.assertAlmostEqual(wt_obj.MinimumHorizontalPercentage, 0.85)
                    self.assertAlmostEqual(wt_obj.ThirdPersonMeshScale, 1.05)
                    self.assertEqual(wt_obj.Presentation.Name, "Presentation_Melee")

                # Verify `GestaltPartBounds` were tightened around rotated horizontal sword center
                hyp_bound = next(
                    b
                    for b in state.STATE.custom_gestalt_def.GestaltPartBounds
                    if b.SkeletalMeshFragmentName == "Ink_blade_hyperion"
                )
                self.assertLess(hyp_bound.ReferencePoseBounds.BoxExtent.Y, 4.0)
                self.assertLess(hyp_bound.ReferencePoseBounds.BoxExtent.Z, 30.0)

                hyp_wt = next(iter(state.STATE.maker_to_wt.values()))
                preview_comp = types.SimpleNamespace(
                    Class=types.SimpleNamespace(Name="GestaltSkeletalMeshComponent"),
                    Scale=0.85,
                    Scale3D=world._vec(1.0, 1.0, 1.0),
                    Rotation=FakeWrappedStruct("Rotator", Pitch=0, Yaw=0, Roll=0),
                    Translation=world._vec(),
                )
                preview_comp.SetScale = lambda s: setattr(preview_comp, "Scale", float(s))
                # Static call (`obj = Default__WillowWeapon`, `args.InvDefinition = hyp_wt`)
                resolver.on_init_construct_preview_post(
                    types.SimpleNamespace(Name="Default__WillowWeapon"),
                    types.SimpleNamespace(Existing=None, InvDefinition=hyp_wt),
                    preview_comp,
                    None,
                )
                self.assertAlmostEqual(preview_comp.Scale, 2.05)

                # Vanilla pistol preview MUST NOT be altered!
                pistol_wt = world.find_object(
                    "WeaponTypeDefinition", "GD_Weap_Pistol.A_Weapons.WeaponType_Hyperion_Pistol"
                )
                pistol_preview = types.SimpleNamespace(
                    Class=types.SimpleNamespace(Name="GestaltSkeletalMeshComponent"),
                    Scale=0.85,
                )
                pistol_preview.SetScale = lambda s: setattr(pistol_preview, "Scale", float(s))
                resolver.on_init_construct_preview_post(
                    types.SimpleNamespace(Name="Default__WillowWeapon"),
                    types.SimpleNamespace(Existing=None, InvDefinition=pistol_wt),
                    pistol_preview,
                    None,
                )
                self.assertAlmostEqual(pistol_preview.Scale, 0.85)

                # Live `melee_preview` tuner updates all WTs + `GestaltPartBounds` dynamically
                cfg = commands.tune_ui_preview_command(
                    roll_deg="-68", scale="2.20", focus_radius="20", yaw_deg="28", bounds_z_scale="0.55"
                )
                self.assertAlmostEqual(cfg["scale"], 2.20)
                self.assertAlmostEqual(cfg["bounds_z_scale"], 0.55)
                self.assertEqual(hyp_wt.UIMeshRotation.Roll, int(round(-68.0 * 65536.0 / 360.0)))
                self.assertEqual(hyp_wt.UIMeshRotation.Yaw, int(round(28.0 * 65536.0 / 360.0)))
                self.assertAlmostEqual(hyp_wt.FocusRadius, 20.0)

                gestalt = state.STATE.custom_gestalt_def
                self.assertIsNotNone(gestalt)
                self.assertEqual(len(gestalt.GestaltInfos[0].Parts), 42)
                self.assertEqual(len(gestalt.GestaltPartBounds), 42)
                self.assertGreaterEqual(len(gestalt.GestaltSocketMappings), 15)

                # Every custom WeaponTypeDefinition points to Gestalt_Inkblade & every Body part has 'None' fragment
                for wt_obj in state.STATE.maker_to_wt.values():
                    self.assertEqual(wt_obj.GestaltMesh, gestalt)
                for body_part in state.STATE.body_parts.values():
                    self.assertEqual(body_part.GestaltModeSkeletalMeshName, "None")

                # Drop a Hyperion Shock Serrated Katana vs a Torgue Explosive Serrated Blast-Cleaver
                hyp_inst = MeleeWeaponInstance(
                    unique_id=9001,
                    game_stage=30,
                    rarity=Rarity.VERY_RARE,
                    parts=MeleeWeaponParts(
                        blade_id="Blade_Hyperion_Katana",
                        grip_id="Grip_Hyperion",
                        accessory_id="Acc_Serrated",
                        element_id="Elem_Shock",
                    ),
                )
                tor_inst = MeleeWeaponInstance(
                    unique_id=9002,
                    game_stage=30,
                    rarity=Rarity.VERY_RARE,
                    parts=MeleeWeaponParts(
                        blade_id="Blade_Torgue_BlastCleaver",
                        grip_id="Grip_Torgue",
                        accessory_id="Acc_Serrated",
                        element_id="Elem_Explosive",
                    ),
                )
                world.ground_pickups.clear()
                dropped = resolver.spawn_melee_weapon_batch([hyp_inst, tor_inst], pc=world.pc, delivery="drop")
                self.assertEqual(dropped, 2)
                hyp_wpn = world.ground_pickups[0].Inventory
                tor_wpn = world.ground_pickups[1].Inventory

                # Hyperion gets variant 0 (`Ink_accessory_serrated_edge`) + `Mat_Inkblade_Shock_VeryRare` (`Rarity == 4.0`)
                self.assertEqual(
                    hyp_wpn.DefinitionData.BarrelPartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_blade_hyperion",
                )
                self.assertEqual(
                    hyp_wpn.DefinitionData.Accessory1PartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_accessory_serrated_edge",
                )
                self.assertEqual(
                    hyp_wpn.DefinitionData.ElementalPartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_element_shock",
                )
                self.assertTrue(
                    hyp_wpn.DefinitionData.MaterialPartDefinition._path_name().endswith("Mat_Inkblade_Shock_VeryRare")
                )
                self.assertEqual(
                    hyp_wpn.DefinitionData.MaterialPartDefinition.Rarity.BaseValueConstant,
                    4.0,
                )

                # Loot beam + item-card colour levels must match the rolled tier on the live objects
                # (`WillowPickup.InventoryRarityLevel` / `WillowInventory.RarityLevel`).
                for pickup in world.ground_pickups:
                    self.assertAlmostEqual(pickup.InventoryRarityLevel, 4.0)
                    self.assertAlmostEqual(pickup.Inventory.RarityLevel, 4.0)

                # Torgue gets exact blade-fitted variant 4 (`Ink_accessory_serrated_edge__s4`) + `Mat_Inkblade_Explosive_VeryRare`
                self.assertEqual(
                    tor_wpn.DefinitionData.BarrelPartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_blade_torgue",
                )
                self.assertEqual(
                    tor_wpn.DefinitionData.Accessory1PartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_accessory_serrated_edge__s4",
                )
                self.assertEqual(
                    tor_wpn.DefinitionData.ElementalPartDefinition.GestaltModeSkeletalMeshName,
                    "Ink_element_explosive",
                )
                self.assertTrue(
                    tor_wpn.DefinitionData.MaterialPartDefinition._path_name().endswith(
                        "Mat_Inkblade_Explosive_VeryRare"
                    )
                )
                self.assertEqual(
                    tor_wpn.DefinitionData.MaterialPartDefinition.Rarity.BaseValueConstant,
                    4.0,
                )

                # Reverse-resolving `tor_wpn` maps `Part_Acc_Serrated__s4` cleanly back to `Acc_Serrated`!
                world.pawn.Weapon = tor_wpn
                dump = commands.dump_held_melee_weapon()
                self.assertIsNotNone(dump)
                self.assertEqual(dump["parts"]["accessory"], "Acc_Serrated")
                self.assertEqual(dump["visuals"]["accessory_fragment"], "Ink_accessory_serrated_edge__s4")
                self.assertTrue(dump["visuals"]["material_part"].endswith("Mat_Inkblade_Explosive_VeryRare"))
