# Melee Arsenal — Borderlands 2 PythonSDK Mod & UPK Pipeline

**Melee Arsenal (`v0.3.5`)** adds a standalone **Melee Weapon** class to *Borderlands 2* (`pyunrealsdk` 1.10 / `unrealsdk` 3.2 / `mods_base` 1.12+) powered by a custom 22-part modular 3D **InkBlade Bold (`r5`)** Gestalt mesh and six 2048×2048 elemental texture atlases compiled via a pure-Python Unreal Engine 3 `.upk` pipeline.

Primary Fire swings your equipped Melee Weapon (precision single-target strikes or frontal cleave arcs) scaling with gun-level base damage, weakspot crits, elemental DoT/splash, **player Melee Damage bonuses**, and **Roid shield buffs**, while `[V]` remains your vanilla character melee. **Right Mouse Button is deliberately inert** on Melee Weapons in `v0.3.5` (no ADS zoom); the hook is kept as a stub for possible future secondary attacks.

Every Melee Weapon also rolls **level-scaled Damage Reduction** (`~30%` at Lv 1 climbing to `70%` by Lv 80 / OP10, `76%` on uniques) that protects you while that blade is the weapon in hand — the trade-off for committing to close-quarters combat.

---

## Weapon Archetypes

| Manufacturer | Archetype | Targeting Profile | Element Policy | Signature Trait |
| :--- | :--- | :--- | :--- | :--- |
| **Hyperion** | **Katana** | Single-Target | Optional (Fire / Shock / Corrosive / Slag) | Fast precision strikes & weakspot crits |
| **Jakobs** | **Sabre** | Single-Target | Non-Elemental | Fastest cadence & `x4` weakspot crit multiplier |
| **Bandit** | **Machete** | Frontal Cleave | Optional (Fire / Shock / Corrosive / Slag) | Wide multi-target cleave arc |
| **Maliwan** | **Phaseblade** | Frontal Cleave | Always Elemental (Fire / Shock / Corrosive / Slag) | High elemental DoT & status chance |
| **Torgue** | **Blast-Cleaver** | Frontal Cleave | Always Explosive | Slow, heaviest per-swing damage + gyrojet explosive splash |

Each weapon rolls a **Blade** (defines manufacturer & archetype), **Grip** (matching-grip bonus + handling stats), optional **Accessory** (7 physical mesh attachments with socket/bifacial twin fitting; guaranteed on Purple `VeryRare`), and **Element** (with matching elemental mesh emitter, atlas palette, and White/Green/Blue/Purple loot beam).

---

## Item Cards & Blade Guard (Damage Reduction)

Item cards (`ItemCardGFxObject`) show **Damage** (gun-scaled integer), **Range** (meters), **Swing Speed** (swings/sec), plus **Burn/Electrocute/Corrode/Slag Dmg/s & Effect Chance** (or Torgue **Splash Dmg & Blast Radius**), with Accuracy / Reload Speed / Magazine Size hidden.

The first FunStats line is the blade's rolled **`Damage Reduction NN% (Equipped)`**, where `NN` is a stable value derived from the weapon's construction (Blade + Grip + Accessory + Element + rarity) and its **level**. The curve starts near `30%` of the Lv 1 floor (parts only nudge it `+0-4%`), widens with level, and tops out at `70%` by Lv 80 / OP10 — `76%` for Legendary / Seraph / Pearlescent blades, which keep a level-scaled headroom past the normal cap. It is never derived from `UniqueId`, which BL2 can hand back zeroed for re-assembled items. The card, `melee_dump_held`, and the live combat hook therefore always agree, and the value survives save/load.

While a Melee Weapon is your held `Pawn.Weapon`, incoming damage on `WillowGame.WillowPlayerPawn:TakeDamage` is blocked and re-issued at `Damage x (1 - NN/100)` inside `prevent_hooking_direct_calls()`, so BL2 still performs the normal shield/health split, flinch, hit reactions, and Fight For Your Life. Only your own local pawn is ever modified (co-op safe), vanilla guns in hand grant nothing, and any unexpected state falls through to untouched vanilla damage. Toggle it off with **MODS -> Melee Arsenal -> `Melee Weapon Damage Reduction`**.

*(The old green `Stacks with Player Melee & Roid Bonuses` line was removed in `v0.3.4`: it consistently clipped into the manufacturer logo and restated behaviour every Melee Weapon already has.)*

---

## Combat Feel (`v0.3.5`)

- **Weakspot crits show the vanilla `CRITICAL!` popup.** `Behavior_CauseDamage` never reports a hit region, so BL2's recent-damage tracker cannot flag our strikes on its own; the mod tags the damaged actor for `0.75 s` and re-issues `WillowDamageTypeDefinition:DisplayRecentDamageForPlayer` with `DamageEventFlags |= DAMAGE_EVENT_FLAG_CRIT`. Only your own local player's floating numbers are affected (co-op safe), stale tags expire, and any unexpected state falls back to untouched vanilla display.
- **Camera shake is a slow, eased bump — not a flick.** Playback runs at `0.65–0.70x` so even the anim's own opening spike is stretched out, the blend-in window is `0.30 s` (a `0.10 s` ramp simply read as a jump: the old curve eased out but never in) and the fade-out matches it at `0.32 s`. Authoring amplitudes stay `0.50` (Torgue) / `0.30` (cleave) / `0.18` (single-target), damped by swing cadence (`min(1, max(0.45, 1.45 / swings-per-second))`) and by the **`Swing Camera Shake (%)`** dial, which is rescaled by `0.20` — so the **50%** default now equals what the old curve's **10%** felt like, `100%` is twice that and `150%` three times. `0%` is a true off switch.
- **Faster blades hit softer.** Per-hit damage is scaled by `min(1.35, max(0.60, (1.50 / swing_speed) ** 0.55))` so a `2.35/s` Jakobs Sabre trades card damage for cadence while a `0.92/s` Torgue Blast-Cleaver trades cadence for damage — fast blades no longer out-DPS their slower peers while also being easiest to land.
- **Right Mouse Button does nothing.** ADS zoom inherited from the donor pistol weapon type is fully suppressed at `WillowWeapon:StartZoom` / `ClientStartZoom`; `_try_melee_secondary_attack()` is the (currently disabled) stub for future unique-only secondary attacks.

---

## Quick Start (`./melee` CLI)

```bash
# 1. Create local .venv & install Pillow (for UPK texture atlas compilation)
./melee setup

# 2. Check repository, kit, cooked UPK, and Borderlands 2 install status
./melee status

# 3. (Optional) Regenerate the modular kit through the staged pipeline -> build/inkblade_kit
./melee generate

# 4. Compile ./assets/inkblade_bold_kit -> CookedPCConsole/*.upk + manifest JSON
./melee build

# 5. Run syntax verification & full unit test suite
./melee test

# 6. Install UPKs + sdk_mods/melee_arsenal into Borderlands 2
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
| `./melee generate [options]` | Regenerate the Inkblade kit through the staged pipeline (`geometry -> uv -> flat -> mottle -> linework -> decals -> textures -> export`) into `build/inkblade_kit`, with a per-stage cache in `build/inkblade_stages`. Supports `--list-stages`, `--only`, `--from`, `--force`, `--decals`, `--parts`, `--out <dir\|zip>` |
| `./melee test [args]` | Byte-compile all modules and run `unittest` (e.g. `./melee test -q`, `./melee test -k world_drop`) |
| `./melee preview` | Roll sample Level 50 Melee Arsenal weapons and print item cards, level-scaled Damage Reduction, and Roid/Crit/UVHM hit math |
| `./melee package [--build]` | Build `dist/melee_arsenal.zip`, `dist/melee_arsenal-v0.3.5.zip`, and `dist/source.zip` |
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
│   ├── generate_inkblade_kit.py    # Staged Inkblade kit generator CLI (pure Python + Pillow)
│   ├── inkblade_gen/               # Stage stack: geometry, uv, flat/mottle/linework/decals, export
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

Under **MODS -> Melee Arsenal**, **`Swing Camera Shake (%)`** defaults to **`50`** (`0%` removes the shake entirely, `150%` is three times the default) and **`1st-Person Slash Motion (%)`** likewise dials the arms' slash animation, so the swing feel can be tuned without restarting the game. **`World Drop Class Weight`** defaults to **`100`** (`100 / 620 = ~16.1%` of weapon drops — 1:1 parity with Pistols, SMGs, Assault Rifles, and Shotguns) and can be tuned anywhere from `10` up to `500` (or toggled off via **`Enable World Drops`**). **`Melee Weapon Damage Reduction`** (on by default) toggles the equipped blade's rolled level-scaled incoming-damage reduction (`~30%` at Lv 1 -> `70%` at Lv 80 / OP10), and **`Log Swing Damage Breakdown`** additionally logs each reduced hit to `melee_arsenal.log`.

---

## In-Game Console Commands

Open the PythonSDK console (`~`) in *Borderlands 2*:

- `melee_spawn [maker] [rarity] [element] [accessory]` — Drop a customized Melee Weapon in front of you (e.g. `melee_spawn hyperion purple shock serrated`)
- `melee_spawn_all [white|green|blue|purple]` — Drop all 5 manufacturer archetypes in front of you
- `melee_check_pools` — Re-sync, re-roll every gun vendor's shelf from the injected pools, and print live per-pool Melee Arsenal weights, drop share `%`, and any pool paths the current map has not loaded
- `melee_dump_held` *(alias `melee_debug_swing`)* — Inspect the currently held Melee Weapon's parts, Gestalt fragments, card stats (including its rolled `damage_reduction_pct`), and live player melee/Roid multiplier math
- `melee_save_now` — Manually snapshot carried Melee Arsenal weapons to `_melee_arsenal_saves`
- `melee_preview [roll_deg] [scale] [focus_radius] [pitch_deg] [yaw_deg] [bounds_z]` — Live-tune 3D inventory/inspect framing
