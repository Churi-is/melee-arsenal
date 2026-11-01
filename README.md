# Melee Arsenal — Borderlands 2 PythonSDK Mod & UPK Pipeline

**Melee Arsenal (`v0.3.4`)** adds a standalone **Melee Weapon** class to *Borderlands 2* (`pyunrealsdk` 1.10 / `unrealsdk` 3.2 / `mods_base` 1.12+) powered by a custom 22-part modular 3D **InkBlade Bold (`r5`)** Gestalt mesh and six 2048×2048 elemental texture atlases compiled via a pure-Python Unreal Engine 3 `.upk` pipeline.

Primary Fire swings your equipped Melee Weapon (precision single-target strikes or frontal cleave arcs) scaling with gun-level base damage, weakspot crits, elemental DoT/splash, **player Melee Damage bonuses**, and **Roid shield buffs**, while `[V]` remains your vanilla character melee.

Every Melee Weapon also rolls **50-70% Damage Reduction** that protects you while that blade is the weapon in hand — the trade-off for committing to close-quarters combat.

---

## Weapon Archetypes

| Manufacturer | Archetype | Targeting Profile | Element Policy | Signature Trait |
| :--- | :--- | :--- | :--- | :--- |
| **Hyperion** | **Katana** | Single-Target | Optional (Fire / Shock / Corrosive / Slag) | Fast precision strikes & weakspot crits |
| **Jakobs** | **Sabre** | Single-Target | Non-Elemental | High base damage & massive weakspot crit bonus |
| **Bandit** | **Machete** | Frontal Cleave | Optional (Fire / Shock / Corrosive / Slag) | Wide multi-target cleave arc |
| **Maliwan** | **Phaseblade** | Frontal Cleave | Always Elemental (Fire / Shock / Corrosive / Slag) | High elemental DoT & status chance |
| **Torgue** | **Blast-Cleaver** | Frontal Cleave | Always Explosive | Heavy cleave + gyrojet explosive splash |

Each weapon rolls a **Blade** (defines manufacturer & archetype), **Grip** (matching-grip bonus + handling stats), optional **Accessory** (7 physical mesh attachments with socket/bifacial twin fitting; guaranteed on Purple `VeryRare`), and **Element** (with matching elemental mesh emitter, atlas palette, and White/Green/Blue/Purple loot beam).

---

## Item Cards & Blade Guard (Damage Reduction)

Item cards (`ItemCardGFxObject`) show **Damage** (gun-scaled integer), **Range** (meters), **Swing Speed** (swings/sec), plus **Burn/Electrocute/Corrode/Slag Dmg/s & Effect Chance** (or Torgue **Splash Dmg & Blast Radius**), with Accuracy / Reload Speed / Magazine Size hidden.

The first FunStats line is the blade's rolled **`Damage Reduction NN% (Equipped)`**, where `NN` is a stable `50-70%` value derived from the weapon's construction (Blade + Grip + Accessory + Element + rarity) — never from `UniqueId` or `GameStage`, which BL2 can hand back zeroed for re-assembled items. The card, `melee_dump_held`, and the live combat hook therefore always agree, and the value survives save/load.

While a Melee Weapon is your held `Pawn.Weapon`, incoming damage on `WillowGame.WillowPlayerPawn:TakeDamage` is blocked and re-issued at `Damage x (1 - NN/100)` inside `prevent_hooking_direct_calls()`, so BL2 still performs the normal shield/health split, flinch, hit reactions, and Fight For Your Life. Only your own local pawn is ever modified (co-op safe), vanilla guns in hand grant nothing, and any unexpected state falls through to untouched vanilla damage. Toggle it off with **MODS -> Melee Arsenal -> `Melee Weapon Damage Reduction`**.

*(The old green `Stacks with Player Melee & Roid Bonuses` line was removed in `v0.3.4`: it consistently clipped into the manufacturer logo and restated behaviour every Melee Weapon already has.)*

---

## Quick Start (`./melee` CLI)

```bash
# 1. Create local .venv & install Pillow (for UPK texture atlas compilation)
./melee setup

# 2. Check repository, kit, cooked UPK, and Borderlands 2 install status
./melee status

# 3. Compile ./assets/inkblade_bold_kit -> CookedPCConsole/*.upk + manifest JSON
./melee build

# 4. Run syntax verification & full unit test suite
./melee test

# 5. Install UPKs + sdk_mods/melee_arsenal into Borderlands 2
#    (auto-detects Steam / Proton / CrossOver / Whisky / WSL2, or caches --bl2-dir in .bl2_dir)
./melee install
# or specify your Borderlands 2 root once:
./melee install --bl2-dir "/path/to/Borderlands 2"
```

### All `./melee` Commands

| Command | Description |
| :--- | :--- |
| `./melee setup` | Create `.venv/` and install `Pillow` for UPK compilation |
| `./melee build [--kit <path>]` | Compile modular kit (default `./assets/inkblade_bold_kit`) into `WillowGame/CookedPCConsole/MeleeArsenal{Meshes,Textures}.upk`, update `inkblade_compiled_manifest.json`, and sync `sdk_mods/melee_arsenal/upk/` |
| `./melee test [args]` | Byte-compile all modules and run `unittest` (e.g. `./melee test -q`, `./melee test -k world_drop`) |
| `./melee preview` | Roll sample Level 50 Melee Arsenal weapons and print item cards, rolled 50-70% Damage Reduction, and Roid/Crit/UVHM hit math |
| `./melee package [--build]` | Build `dist/melee_arsenal.zip`, `dist/melee_arsenal-v0.3.4.zip`, and `dist/source.zip` |
| `./melee install [--bl2-dir <dir>] [--probe]` | Stage `.upk` packages and sync `sdk_mods/melee_arsenal/` into Borderlands 2 (preserving `_melee_arsenal_saves` and `melee_arsenal.log`) |
| `./melee dev` | Fast iteration loop: run quiet tests, then install to Borderlands 2 |
| `./melee status` | Show mod version, Python/Pillow state, kit & UPK sizes, BL2 sync status, and sidecar save count |
| `./melee logs [-f] [-n <lines>]` | View or tail live `<BL2>/sdk_mods/melee_arsenal/melee_arsenal.log` |
| `./melee uninstall [--purge-saves]` | Remove mod & UPKs from Borderlands 2 (keeps `_melee_arsenal_saves` unless `--purge-saves` is passed) |
| `./melee clean` | Remove `__pycache__`, `.pyc`, probe scratch reports, and `dist/` |
| `./melee all` | Run `build -> test -> package -> install` end-to-end |

---

## Repository Layout

```text
.
├── melee                           # Project build, test, package & BL2 deployment CLI
├── assets/
│   └── inkblade_bold_kit/          # Modular 3D GLBs, bold-inked textures, and socket_manifest.json
├── WillowGame/
│   └── CookedPCConsole/            # Cooked UE3 packages (MeleeArsenalMeshes.upk & MeleeArsenalTextures.upk)
├── sdk_mods/
│   ├── melee_arsenal/              # Main Borderlands 2 PythonSDK mod
│   │   ├── core/                   # Pure-Python catalog, schemas, card/hit math, targeting, persistence
│   │   ├── data/                   # Compiler-generated Gestalt fragment/bounds/atlas manifest JSON
│   │   ├── runtime/                # Live UE3 definitions, Gestalt/MIC binding, combat, GFx cards, loot pools
│   │   └── upk/                    # Bundled fallback MeleeArsenalMeshes.upk auto-stager
│   └── melee_arsenal_probe/        # Optional in-game UE3 diagnostics & Startup.upk donor extractor
├── tools/
│   ├── compile_inkblade_upk.py     # Offline GLB + PNG -> cooked BL2 UE3 .upk compiler CLI
│   ├── preview_arsenal.py          # Offline weapon roller & damage calculator CLI
│   └── inkblade_upk/               # Pure-Python UE3 LZO1X/zlib archive reader, SkeletalMesh/DXT1 codec & donor
└── tests/                          # CPython unittest suites (core math, strict mock SDK runtime, UPK codec, probe)
```

---

## World Drops & Mod Menu Options

Melee Arsenal injects proportionally across **23 weapon loot pools** with idempotent map-load re-syncing (preventing `SparkLevelPatchEntry`, BLCMM, or UCP level patches from overwriting drops):

- **Global World-Drop Rarity Pools**: `GD_Itempools.WeaponPools.Pool_Weapons_All_{01_Common, 02_Uncommon, 04_Rare, 05_VeryRare}`
- **Standard Enemy / Badass / Boss Death Pools**: `GD_Itempools.EnemyDropPools.Pool_GunsAndGear_{01_Common, 02_Uncommon, 04_Rare, 05_VeryRare}` *(the tiered pools a dying enemy actually rolls, each mapped to its matching rarity sub-pool)*
- **Class-Biased Enemy Drop Pools**: `GD_Itempools.EnemyDropPools.Pool_GunsAndGear_Weighted{Pistols, SMGs, Shotguns, Sniper}`
- **Regular & Red Epic Weapon Chests**: `GD_Itempools.Treasure_ChestPools.Pool_{WeaponChest, EpicChest}_Weapons_{LongGuns, Pistols}` *(Red Chests exclude White Commons)*
- **Interactive Containers**: `GD_Itempools.LootablePools.{Pool_Locker_Items_SMGsAndPistols, Pool_Dumpster_Guns}`
- **Marcus Weapon Vendors & Item of the Day**: `GD_ItemPools_Shop.WeaponPools.{ShopPool_Weapons_Pistols, ShopPool_Weapons_SMG, ShopPool_Weapons_AssaultRifles, ShopPool_Weapons_Shotguns, Shoppool_FeaturedItem_WeaponMachine}`
- *(AI `GD_ItempoolsEnemyUse.*` pools are never modified so NPC enemies never spawn wielding player melee blades.)*

Pool lookup tolerates BL2 patch/DLC naming drift (exact paths → case-insensitive live-object resolution → alternate pistol/SMG/AR/shotgun donor pools), and the first successful injection re-rolls any already-stocked `WillowVendingMachine` shelves so Marcus machines show Melee Weapons without a map transition.

Every Melee Weapon's Material part is stamped with its exact rarity level (`1` White / `2` Green / `3` Blue / `4` Purple), and dropped pickups / inventory items get matching `WillowPickup.InventoryRarityLevel` / `WillowInventory.RarityLevel` values — so ground loot beams, item-card titles and sell values are always the rolled rarity's colour.

Under **MODS -> Melee Arsenal**, **`World Drop Class Weight`** defaults to **`100`** (`100 / 620 = ~16.1%` of weapon drops — 1:1 parity with Pistols, SMGs, Assault Rifles, and Shotguns) and can be tuned anywhere from `10` up to `500` (or toggled off via **`Enable World Drops`**). **`Melee Weapon Damage Reduction`** (on by default) toggles the equipped blade's rolled `50-70%` incoming-damage reduction, and **`Log Swing Damage Breakdown`** additionally logs each reduced hit to `melee_arsenal.log`.

---

## In-Game Console Commands

Open the PythonSDK console (`~`) in *Borderlands 2*:

- `melee_spawn [maker] [rarity] [element] [accessory]` — Drop a customized Melee Weapon in front of you (e.g. `melee_spawn hyperion purple shock serrated`)
- `melee_spawn_all [white|green|blue|purple]` — Drop all 5 manufacturer archetypes in front of you
- `melee_check_pools` — Re-sync, re-roll every gun vendor's shelf from the injected pools, and print live per-pool Melee Arsenal weights, drop share `%`, and any pool paths the current map has not loaded
- `melee_dump_held` *(alias `melee_debug_swing`)* — Inspect the currently held Melee Weapon's parts, Gestalt fragments, card stats (including its rolled `damage_reduction_pct`), and live player melee/Roid multiplier math
- `melee_save_now` — Manually snapshot carried Melee Arsenal weapons to `_melee_arsenal_saves`
- `melee_preview [roll_deg] [scale] [focus_radius] [pitch_deg] [yaw_deg] [bounds_z]` — Live-tune 3D inventory/inspect framing
