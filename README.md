# Melee Arsenal — Borderlands 2 PythonSDK Mod

**Melee Arsenal (`v0.4.5`)** adds a standalone **Melee Weapon** class to *Borderlands 2* — Willow2 Mod Manager v3.8+ (mods_base 1.12+ / unrealsdk 3.2 / pyunrealsdk 1.10) — powered by a custom 22-part modular 3D **InkBlade** Gestalt mesh and six 2048×2048 elemental texture atlases compiled via a pure-Python Unreal Engine 3 `.upk` pipeline.

Primary Fire swings your equipped Melee Weapon (precision single-target strikes or frontal cleave arcs) scaling with gun-level base damage, elemental DoT/splash, **player Melee Damage bonuses**, and **Roid shield buffs**, while `[V]` remains your vanilla character melee. **Right Mouse Button / Left Trigger fires each manufacturer's secondary attack** (ADS zoom stays suppressed): **Bandit and Torgue throw their blade**, **Hyperion and Jakobs hold to guard**, and **Maliwan gets nothing**.

Every Melee Weapon also rolls **level-scaled Damage Reduction** (~30% at Lv 1 → 70% by Lv 80/OP10, 76% on uniques) while it is the weapon in hand — the trade-off for close-quarters combat.

---

## Requirements

- **Game:** Borderlands 2 (Steam, any region)
- **SDK:** [Willow2 Mod Manager v3.8+](https://github.com/bl-sdk/willow2-mod-manager/releases) (bundles mods_base 1.12+, unrealsdk 3.2, pyunrealsdk 1.10)
- **OS:** Windows or Linux via Proton/Wine (auto-detected by the installer)
- **Python (developers only):** 3.11+ and Pillow — see [Dependencies](#dependencies)

---

## Installation (Players)

1. Download `melee_arsenal.zip` (or `melee_arsenal-v0.4.5.zip`) from the Releases page.
2. Extract it to your Borderlands 2 install — `WillowGame/CookedPCConsole/MeleeArsenal*.upk` and `sdk_mods/melee_arsenal/` should appear.
3. Launch the game, open **MODS → Melee Arsenal → Enabled**.
4. Load any map to find Melee Weapons in-world. Check **MODS → Melee Arsenal** for tuning dials.

To uninstall, run `./melee uninstall` or delete `sdk_mods/melee_arsenal/` and `WillowGame/CookedPCConsole/MeleeArsenal*.upk`. Save files at `sdk_mods/_melee_arsenal_saves/` are kept by default.

## Weapon Archetypes

| Manufacturer | Archetype | Targeting | Element | Signature | Secondary (RMB / LT) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Hyperion** | **Katana** | Single-Target | Optional | Fast precision + crits | **Guard** |
| **Jakobs** | **Sabre** | Single-Target | Non-Elemental | Fastest cadence, 4× crit | **Guard** |
| **Bandit** | **Machete** | Frontal Cleave | Optional | Wide multi-target arc | **Throw** |
| **Maliwan** | **Phaseblade** | Frontal Cleave | Always Elemental | High DoT & status | *None* |
| **Torgue** | **Blast-Cleaver** | Frontal Cleave | Always Explosive | Heavy + splash | **Throw** |

Each weapon rolls a **Blade**, **Grip**, optional **Accessory**, and **Element**, with matching 3D mesh, palette, and loot-beam colour.

---

## Controls

### Primary Fire — Swing

- **LMB / RT** swings your Melee Weapon. Single-target blades seek the crosshair with a small assist; cleave blades hit every valid target in range and arc, blocked only by walls.
- Damage scales with the weapon's level (gun-level base), your Melee Damage skills, Roid shields, and elemental multipliers. Faster blades hit softer per swing to keep DPS balanced.
- `[V]` is still your character's vanilla melee — Maya Scorn, Zer0 Execute, etc. are untouched.

### Secondary Attacks — RMB / LT (no ADS on blades)

**Throw — Bandit & Torgue**

Press RMB to hurl the blade. It arcs through the air, hits like a normal swing (with elemental DoT and Torgue splash), then homes back to your hand. Primary Fire is locked while airborne. Heavier Torgue slabs lob shorter and higher than lighter Bandit blades.

**Guard — Hyperion & Jakobs**

Hold RMB to brace. The next incoming hit is reduced twice by the blade's rolled Damage Reduction; a hit within the first 0.33 s is a **perfect parry** — no damage, and melee attackers take reflected crit damage with a `PERFECT PARRY!` callout.

**Maliwan — no secondary.** RMB is inert; the Phaseblade's identity is pure elemental cleave.

---

## Item Cards & Damage Reduction

Item cards show **Damage**, **Range** (meters), **Swing Speed** (/sec), and elemental or Torgue splash stats. The first stat line is **`Damage Reduction NN% (Equipped)`** — incoming damage while this weapon is held is reduced by NN%.

- Starts near 30% at Lv 1, climbs to 70% by Lv 80/OP10 (76% on Legendary/Seraph/Pearlescent).
- Only your held weapon grants it; only your own pawn is affected (co-op safe).
- Primary-blade DR toggle: **MODS → Melee Arsenal → Melee Weapon Damage Reduction**. Guard DR can be independently disabled with **Guard Damage Reduction**; the guard pose and parry still work.

---

## Mod Menu Options

Open **MODS → Melee Arsenal**:

| Option | Default | Effect |
| :--- | :--- | :--- |
| **Enable World Drops** | On | Inject Melee Weapons into world, enemy, chest, and vendor pools. |
| **World Drop Class Weight** | 100 | Share of weapon drops (~16% at 100 = parity with each gun class; 10–500). |
| **1st-Person Slash Motion (%)** | 100 | Slash animation intensity. |
| **Swing Camera Shake (%)** | 50 | Camera bump per swing (0 = off, up to 150). |
| **Guard Blade Tilt (%)** | 100 | Visual guard angle; negative flips direction. |
| **Melee Weapon Damage Reduction** | On | Enable the equipped-blade damage-reduction perk. |
| **Guard Damage Reduction** | On | Enable mitigation while guarding; disabling it leaves the guard pose and parry active. |
| **Log Swing Damage Breakdown** | Off | Debug log per hit to `melee_arsenal.log`. |
| **Spawn All 5 Melee Archetypes** | Button | Drops the five archetypes in front of you. |

---

## Console Commands

Open the SDK console (`~`):

- `melee_spawn [maker] [rarity] [element] [accessory]` — Drop a tuned blade (e.g. `melee_spawn hyperion purple shock serrated`).
- `melee_spawn_all [white|green|blue|purple]` — Drop all five archetypes.
- `melee_check_pools` — Re-sync pools, re-roll vendor shelves, and report Melee share.
- `melee_dump_held` — Inspect the held blade's parts, stats, and live bonuses.
- `melee_secondary` — Print throw arc / guard state.
- `melee_save_now` — Snapshot carried blades to sidecar storage.
- `melee_preview [roll_deg] [scale] [focus_radius] [pitch_deg] [yaw_deg] [bounds_z]` — Tune inventory 3D framing.

---

## Troubleshooting

**Melee Weapons don't drop / pools stay vanilla**
- Open the console and run `melee_check_pools`. If it reports `0/… pools injected`, another mod (BLCMM/UCP) may be overwriting pools. The mod re-syncs every map load; reload the map or restart.
- Ensure `WillowGame/CookedPCConsole/MeleeArsenal*.upk` exists in your BL2 install and version matches `sdk_mods/melee_arsenal/data/inkblade_compiled_manifest.json` — run `./melee status` to check.

**Blade throw leaves hands empty or sticks in mid-air**
- Check `sdk_mods/melee_arsenal/melee_arsenal.log` for errors. The throw has a 30 s fail-safe so you are never permanently disarmed; re-equip the weapon to reset.

**Guard pose sticks / hands drift**
- The mod snapshots the hands on every pose and force-restores after one frame or 12 s. If a pose sticks after swapping weapons, toggle the weapon or quick-save. Logs with `Log Swing Damage Breakdown` on can help diagnose.

**Vendors don't show Melee Weapons**
- Vendor shelves are built at map load before pools exist. Moving to a new map after enabling the mod, or running `melee_check_pools`, re-rolls shelves.

For deeper diagnostics, enable **Log Swing Damage Breakdown** and tail the log:

```bash
./melee logs -f
```

---

## Dependencies

- **Runtime:** Borderlands 2 + Willow2 Mod Manager v3.8+ — no Python packages required to play.
- **Build-time (kit & UPK):** Pillow — see [requirements.txt](requirements.txt) for the exact reproducibility pin (`Pillow==13.0.0`).

Install with:

```bash
./melee setup              # or: pip install -r requirements.txt
```

---

## License & Asset Provenance

- **Code:** [MIT License](LICENSE) — see `LICENSE`.
- **3D & textures:** All InkBlade meshes and texture atlases are procedurally generated in pure Python (no extracted BL2 meshes). The only donor asset is the stripped `Weap_Pistol` donor UPK used as a UE3 header template (see [docs/ASSET_PROVENANCE.md](docs/ASSET_PROVENANCE.md)).
- **Third-party:** Pillow (HPND), Willow2 Mod Manager (GPLv3).

## Version

`v0.4.5` — Willow2 Mod Manager v3.8+ (mods_base 1.12+ / unrealsdk 3.2 / pyunrealsdk 1.10)
