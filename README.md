# Melee Arsenal — Borderlands 2 PythonSDK Mod & UPK Pipeline

Source repository for **`melee_arsenal` (`v0.3.3`)**, the in-game engine/donor probe mod **`melee_arsenal_probe`**, the pure-Python UE3 `.upk` compiler **`tools/inkblade_upk`**, and the CPython `unittest` suite.

## Repository Layout

- `sdk_mods/melee_arsenal/` — Main Borderlands 2 PythonSDK (`pyunrealsdk` 1.10 / `unrealsdk` 3.2) mod:
  - `core/` — Pure-Python weapon schemas, 5-manufacturer Blade/Grip/Accessory/Element catalog, damage/DoT/card math, targeting cones, loot roller, and save-persistence codec.
  - `runtime/` — Live UE3 registration (`WeaponTypeDefinition`, `WeaponBalanceDefinition`, `WeaponPartDefinition`, `MaterialInstanceConstant`, `GestaltPartBounds` preview framing, `Bladed_Melee` 1P combat & cone/AoE hits, GFx item card rewrite, loot pool injection, and console commands).
  - `data/inkblade_compiled_manifest.json` — Compiler-generated Gestalt bounds, socket deltas, bifacial twin variants, and element atlas manifest consumed automatically at runtime.
- `sdk_mods/melee_arsenal_probe/` — Optional live BL2 diagnostic & donor-extraction SDK mod (`MeleeProbeTex.upk` + `Startup.upk` reader).
- `tools/` — Offline asset & preview toolchain:
  - `compile_inkblade_upk.py` — Compiles an `Inkblade_Modular_Kit` archive, split `.zip1/.zip2` parts, or directory (`r3`, `r4`, `r5` InkBlade Bold, ...) into cooked BL2 `MeleeArsenalMeshes.upk` + `MeleeArsenalTextures.upk` (`ArVer=832, LicenseeVer=46`, 129-byte `FPackageFileSummary`, outward manifold winding repair, MaxRects 2048×2048 DXT1 mipmapped element atlases) and updates `sdk_mods/melee_arsenal/data/inkblade_compiled_manifest.json`.
  - `preview_arsenal.py` — Offline weapon roll & item-card simulator CLI.
  - `inkblade_upk/` — Pure-Python UE3 LZO1X/zlib archive reader, `USkeletalMesh` & `UTexture2D` binary codec, kit compiler, and stripped `donors/weap_pistol_donor.upk` (`Weap_Pistol` skeleton + `WeaponOffset`).
- `tests/` — CPython `unittest` suites covering core math/schemas, mocked `unrealsdk` runtime hooks, probe mod logic, and UPK/manifold compilation.

## Requirements (Offline Tools & Tests)

- Python `>= 3.10`
- `Pillow` (`pip install Pillow`) for DXT1 texture atlas compilation in `tools/compile_inkblade_upk.py` and `tests/test_inkblade_upk.py`.

## Running Tests

From the repository root:

```bash
python3 -m unittest discover -s tests -v
```

*(Optional fixture tests that exercise full `Inkblade_Modular_Kit_r3` / `r4_final` archives automatically run if those zips are placed in `./uploads/`, and skip cleanly otherwise.)*

## Compiling Updated `Inkblade_Modular_Kit` Assets

When a new revision of `Inkblade_Modular_Kit` is exported (same manifest/GLB layout):

```bash
python3 tools/compile_inkblade_upk.py \
  --kit path/to/Inkblade_Modular_Kit_r4_final.zip
```

This writes:
1. `WillowGame/CookedPCConsole/MeleeArsenalMeshes.upk`
2. `WillowGame/CookedPCConsole/MeleeArsenalTextures.upk`
3. `sdk_mods/melee_arsenal/data/inkblade_compiled_manifest.json`

Copy `WillowGame/CookedPCConsole/MeleeArsenal*.upk` into `<Borderlands 2>/WillowGame/CookedPCConsole/` (and optionally copy `MeleeArsenalMeshes.upk` to `sdk_mods/melee_arsenal/upk/MeleeArsenalMeshes.upk` if bundling a release zip) alongside `sdk_mods/melee_arsenal/` in `<Borderlands 2>/sdk_mods/`.

## World Drops & Console Commands (`v0.3.3`)

Melee Arsenal injects proportionally across all primary weapon loot sources (default **`World Drop Class Weight = 100`**, matching Pistols / SMGs / ARs / Shotguns at `~16.1%` of weapon drops, adjustable up to `500` in **MODS -> Melee Arsenal**):
- Global enemy/pile weapon rarity pools (`GD_Itempools.WeaponPools.Pool_Weapons_All_{01_Common,02_Uncommon,04_Rare,05_VeryRare}`)
- Class-biased humanoid/bandit enemy death pools (`GD_Itempools.EnemyDropPools.Pool_GunsAndGear_Weighted{Pistols,SMGs,Shotguns,Sniper}`)
- Regular & Red Epic weapon chests (`GD_Itempools.Treasure_ChestPools.Pool_{WeaponChest,EpicChest}_Weapons_{LongGuns,Pistols}`)
- Lockers & dumpsters (`GD_Itempools.LootablePools.{Pool_Locker_Items_SMGsAndPistols,Pool_Dumpster_Guns}`)
- Marcus weapon vending machines & Item of the Day (`GD_ItemPools_Shop.WeaponPools.*`)
- AI `GD_ItempoolsEnemyUse.*` pools are intentionally left untouched so enemy NPCs never spawn wielding player melee blades.
- Run `melee_check_pools` in the PythonSDK console at any time to re-sync and inspect live per-pool melee drop shares.

## Previewing Weapon Rolls Offline

```bash
python3 tools/preview_arsenal.py
```
