# Melee Arsenal — Developer Guide

This document holds implementation notes that were trimmed from the player-facing [README](../README.md). For the art pipeline, see [tools/inkblade_gen/README.md](../tools/inkblade_gen/README.md).

---

## Contents

- [Architecture](#architecture)
- [SDK Requirements](#sdk-requirements)
- [Combat Engine](#combat-engine)
- [Secondary Attacks](#secondary-attacks)
- [First-Person Animation & Posing](#first-person-animation--posing)
- [Damage Reduction](#damage-reduction)
- [Loot Pool Injection](#loot-pool-injection)
- [Persistence & Save/Load](#persistence--saveload)
- [InkBlade Kit & UPK Pipeline](#inkblade-kit--upk-pipeline)
- [Testing](#testing)
- [CLI Reference](#cli-reference)
- [Historical Notes](#historical-notes)

---

## Architecture

```
sdk_mods/melee_arsenal/
├── core/       # Pure Python — catalog, stats, targeting, loot roller, persistence
│               # Zero unrealsdk imports; fully unit-testable under CPython
├── data/       # Compiler-generated Gestalt manifest JSON
└── runtime/    # Live UE3 — definitions, asset library, combat, secondary, cards, pools

tools/
├── inkblade_gen/   # 10-stage procedural kit generator (Pillow only)
├── inkblade_upk/   # Pure-Python UE3 UPK reader/compiler (LZO1X, DXT1, SkeletalMesh)
└── compile_inkblade_upk.py / generate_inkblade_kit.py / preview_arsenal.py
```

**Core vs Runtime boundary:** `core` owns all math: catalog (Blade→Manufacturer mapping), `compute_card_stats`, `compute_attack_damage`, `apply_incoming_damage_reduction`, targeting helpers (`Vec3`, `TargetCandidate`), throw flight plans (`build_throw_flight_plan`), and guard cooldown math. `runtime` owns UE3 object construction, hook dispatch, and side-effects. No `core` module imports `unrealsdk`.

**State singleton:** `runtime/state.py:RuntimeRegistry` holds rooted UObject caches, manufacturer→definition maps, and live mod option mirrors (`STATE.world_drops_enabled`, `STATE.guard_pose_scale`, etc.). Option changes sync into `STATE` via `on_change_anytime` callbacks in `runtime/__init__.py`. UObject equality/cache keys are path-first (`runtime/object_identity.py`) because Python wrapper identity is not stable across SDK calls. Disable teardown cancels pending grants/restores, removes injected loot entries, clears card overrides and crit/pose/debug caches, and resets secondary attacks; pawn changes begin a new restore epoch.

---

## SDK Requirements

- **Willow2 Mod Manager v3.8+** — bundles **mods_base 1.12+**, **unrealsdk 3.2.0**, **pyunrealsdk 1.10.0**.
  - The package docstring and README must agree; the canonical statement is:
    `Willow2 Mod Manager v3.8+ (mods_base 1.12+ / unrealsdk 3.2 / pyunrealsdk 1.10)`.
  - Earlier docstrings mistakenly wrote `mods_base 3.8` — that conflates the manager's release tag with the `Base` component version; it is corrected to `1.12+`.
- **Python 3.11+** for offline tooling; the in-game SDK runs Python 3.14 inside BL2.
- **Pillow** for kit generation & UPK texture compilation — see [requirements.txt](../requirements.txt).

---

## Combat Engine

**File:** `runtime/combat.py`

Built on empirical capture `melee_combat_probe_report.json` (Sanctuary):

### Hit Detection

- **`WillowWeapon.CalcWeaponFire(StartTrace, EndTrace, ImpactList=[], Extent=(0,0,0), bTestTrace=False)`** returns `(ImpactInfo, ImpactList)` synchronously on frame 0 with `HitActor`, `HitLocation`, and `HitInfo.BoneName`. No reliance on `WillowLightProjectileManager` (lazy-spawned after gun fire).
- **Single-Target (Hyperion, Jakobs):** exact crosshair ray + 22.5° half-angle assist (45° total, matching `BehaviorData_PlayerMeleeAttackWithBladedWeapon`) so close slashes never whiff between limbs.
- **Frontal Cleave (Bandit 95°, Maliwan 110°, Torgue 85°):** strikes every living hostile + Target Dummy inside `card.range_uu` and `card.cleave_arc_degrees` with a per-candidate `CalcWeaponFire` wall check.

Candidate snapshot caps at 800 `PawnList` steps with a seen-set to avoid loops (Gotcha #13). Non-solid actors (physics volumes, triggers, emitters) and faces within 1.5 m are ignored.

### Damage

- **`WillowPawn.Behavior_CauseDamage` with `WillowDmgSource_Bullet`** (never `WillowDmgSource_Melee`, which freezes) applies instant shield/health damage, floating numbers, and native flesh/armor/shield multipliers (e.g. 1.5× Fire on Flesh verified to 4 decimals), kill skills, and XP.
- Rolls `card.elemental_chance_pct` per target between `DmgType_<Elem>_Impact` (DoT proc) and `DmgType_<Elem>_Impact_NoDoT` (elemental hit without DoT). Weapon `BaseStatusEffectChanceModifier` is forced to `2500.0` on proc so BL2's internal roll succeeds.
- **Torgue splash:** `WillowPawn.Behavior_CauseRadiusDamage` with `WillowDmgSource_Grenade` + `DmgType_Explosive_Impact_ForceFlinch` at 180 UU / 50% damage, plus `MyEmitterPool.SpawnEmitter` + `AkEvent` + `CameraAnim`.

### Weakspot Crit Feedback

`Behavior_CauseDamage` has no `HitInfo`, so BL2's `FPlayerRecentDamageEventData.DamageEventFlags` never sets the crit bit. The mod tags victims for 0.75 s and re-issues `WillowDamageTypeDefinition:DisplayRecentDamageForPlayer` with `DAMAGE_EVENT_FLAG_CRIT` OR-ed, then blocks the vanilla display (co-op safe — only local PC).

### Speed Scaling & Camera

- Per-hit damage: `min(1.35, max(0.60, (1.50 / swing_speed) ** 0.55))` — fast blades trade damage for cadence.
- Camera shake: playback 0.65–0.70×, blend-in 0.30 s / blend-out 0.32 s, amplitude 0.50 (Torgue) / 0.30 (cleave) / 0.18 (single-target), cadence-damped `min(1, max(0.45, 1.45 / sps))`, final scale `* shake_scale * 0.20`. The final multiplier is proportional to the user slider. A value at or below 0.1% skips playback entirely; there is no fallback call at unmodified scale.
- See `GUARD_BLADE_PITCH/YAW/ROLL` and `CAMERA_SHAKE_*` constants for the tuned values.

### Frame Driver

Both `Engine.GameViewportClient:Tick` and `WillowGame.WillowPlayerController:PlayerTick` drive the same per-frame body (de-duplicated to 2 ms). The viewport tick is not a UFunction on every BL2 build, so the player tick guarantees thrown blades and poses advance. RMB-release hooks (`StopZoom` / `ClientStopZoom`) are registered at load, not lazily.

**Historical:** Up to v0.2.6 the combat path was synchronous. The obsolete `tick_melee_projectiles()` hook has been removed; frame-driven work is owned by the runtime frame driver.

---

## Secondary Attacks

**Files:** `runtime/secondary.py`, `runtime/secondary_vfx.py`, `core/secondary.py`

RMB/LT is free because ADS zoom is suppressed at `WillowWeapon:StartZoom` / `ClientStartZoom`; `_try_melee_secondary_attack` dispatches `THROW`, `GUARD`, or `NONE`.

### Throw (Bandit Machete, Torgue Blast-Cleaver)

- **Weight model:** blade manufacturer mass (Torgue 3.8 kg > Bandit 2.9 kg > Jakobs 2.1 kg > Hyperion 1.4 kg > Maliwan 1.2 kg), blade 72% + grip 28% + accessory 0.08–0.32 kg. Heavy slabs lob shorter/higher (~9–10 m, 0.95 m apex) vs light blades flatter/further (~11.5–12 m, 0.7 m apex).
- **Aim-relative:** weight-driven loft is added on top of camera pitch; item card prints the flat-ground reference arc.
- **Retimed flight:** `THROW_TIME_SCALE 1.75×` — same distance/apex, slower traversal so the throw reads visually.
- **Ground handling:** the planned arc is prediction only; the blade flies its real parabola per-frame and lands on whatever the trace actually hits. Not teleported to probed ground (which read as "stops mid-air, then jumps"). 30 s fail-safe + 8 s homing timeout; fall limit off cliffs.
- **Swept collision:** `CalcWeaponFire` trace + pure-Python swept-segment check prevents tunneling.
- **Visuals:** release slash → hands drop out of frame (`POSE_THROWN`) → actor carrying `SkeletalMesh` + materials flies the arc (Tediore-style) tumbling, then homes on hand (`~0.35 s` to catch, faster if behind).
- **Damage:** shared LMB/throw attack-damage and elemental proc policy (melee bonuses, Roid, elemental DoT/chance, Torgue splash) at normal hit damage (was 200% in v0.4.3, out-damaged the kit). The native UVHM slag interaction has not been verified in a live BL2 session; do not assume the Python-side hit path reproduces the game’s slag multiplier.
- **Lockout:** Primary Fire blocked while airborne; cannot throw twice.

### Guard (Hyperion Katana, Jakobs Sabre)

- **Effect:** next incoming hit has card DR applied twice: `damage * DR * DR` (e.g. 60% card → 84% braced, 1000 → 160).
- **Parry window:** hit within 0.33 s of raise = zero damage + melee reflection as forced critical hit with vanilla `CRITICAL` popup, `PERFECT PARRY!` HUD, clang, and camera jolt. Reflect is `max(incoming forced-crit, blade's own crit)` via `compute_attack_damage(..., is_crit=True)` so a 112-card Sabre's 4× Jakobs crit scale wins.
- **Visible brace:** holds `WillowWeapon.Mesh.RotOrigin` by ~14° up / 8° toward crosshair / 7° roll (small — arms mesh pivots behind hands, larger offsets swing hands out of frame), with a 6.2 rad/s bob. Earlier builds rotated `Arms.SkeletalMesh`.
- **Pose integrity:** every pose snapshots the live `RotOrigin`/`Origin` at apply time and restores by writing it back. Restores are unconditional (hit-consumed brace restores even without RMB release), self-healing (every tick checks state), and force-restored after 1 frame idle or 12 s absolute. Guard also drops on `StopZoom`, weapon swap, or 6 s safety hold.
- **Cooldown:** `~1.0–2.6 s` — blade manufacturer base (Hyperion faster than Jakobs), mass-stretched, matching-grip −15%, Heavy Spine +0.25 s, Balanced Pommel −0.10 s.

### Maliwan

No secondary; RMB inert, no card line.

---

## First-Person Animation & Posing

- **Swing:** Gearbox's `Anim_1st_Person.1st_Person_Pistol: Bladed_Melee` (0.6333 s, present on all 6 Vault Hunters; 65.2 UU sweep) played on `Pawn.Arms` `AnimNodeSlot` `WeaponAction`/`CustomAnimSlot`, rate `BLADED_MELEE_SEQ_LENGTH / (interval * 0.92)` clamped 0.68–2.35. Suppresses `WeaponRecoil`/`PlayFireEffects`/`WeaponFired`.
- **Fallback:** if no `AnimNodeSlot`, procedural `Arms.SkeletalMesh.RotOrigin` + `ForceUpdate(False)` with alternating left/right `swing_sign` over `min(0.55, interval * 0.88)`.
- **Pose system (`POSE_GUARD` / `POSE_THROWN`):** shares `_ARMS_REST_BASELINE` with fallback. Guard blade tilt on `WillowWeapon.Mesh` (pivot at hand) vs arms mesh (pivot behind). `GUARD_BLADE_TILT (%)` scales all three rotator axes (−1..1, negative mirrors).
- **Capture hygiene:** `capture_first_person_rest_transform()` runs every 30 ticks but only when idle; `_snapshot_hands` / `_capture_arms_rest_transform` check `_POSE_KIND` so a displaced pose never bakes as rest. `WeakPointer` caching with path-name identity handles wrapper churn.

---

## Damage Reduction

- **Roll:** stable from weapon construction (Blade + Grip + Accessory + Element + rarity) + level, never `UniqueId` (zeroed on re-assembly). Curve ~30% Lv 1 (parts nudge 0–4%) → 70% cap Lv 80/OP10, 76% for unique rarities with headroom.
- **Application:** `WillowPlayerPawn:TakeDamage` PRE checks local pawn + `resolve_melee_weapon`, reads the field via `_read_take_damage_amount` (tries `Damage`, `DamageAmount`, `InDamage`, etc. for build drift), blocks original hit, re-calls with `Damage * (1 - pct/100)` inside `prevent_hooking_direct_calls()` so shield/health split, flinch, and FFYL remain vanilla. Only local pawn, co-op safe, any unexpected state falls through.
- **Item card:** `ItemCardGFxObject` `SetFunStats` injects `Damage Reduction NN% (Equipped)`. Old green "Stacks with Player Melee & Roid Bonuses" removed in v0.3.4 (clipped into logo, redundant).
- **Toggle:** `Melee Weapon Damage Reduction` controls the equipped-blade perk. `Guard Damage Reduction` independently gates guard mitigation and parry damage reduction; disabling it does not disable the guard pose, attack-blocking window, or parry response.

---

## Loot Pool Injection

**File:** `runtime/loot_pools.py`

Injects proportionally across 23 weapon pools, idempotent per-map:

- `GD_Itempools.WeaponPools.Pool_Weapons_All_{01_Common,02_Uncommon,04_Rare,05_VeryRare}`
- `GD_Itempools.EnemyDropPools.Pool_GunsAndGear_{01_Common,02_Uncommon,04_Rare,05_VeryRare}`
- `Pool_GunsAndGear_Weighted{Pistols,SMGs,Shotguns,Sniper}`
- `GD_Itempools.Treasure_ChestPools.Pool_{WeaponChest,EpicChest}_Weapons_{LongGuns,Pistols}`
- `GD_Itempools.LootablePools.{Pool_Locker_Items_SMGsAndPistols, Pool_Dumpster_Guns}`
- `GD_ItemPools_Shop.WeaponPools.{ShopPool_Weapons_{Pistols,SMG,AssaultRifles,Shotguns}, Shoppool_FeaturedItem_WeaponMachine}`
- Never `GD_ItempoolsEnemyUse.*` (prevents AI wielding melee).

- Clears `Min/MaxGameStageRequirement` from donor pistol sub-pools so hub maps don't zero weights.
- Scales melee weight `weapon_sum * active_weight / 520.0` (100 → 100/620 ≈16.1% parity).
- Uses `WrappedArray` `.clear()`+`.append()` with read-back; tollerates naming drift (exact → case-insensitive → alternate donor pools).
- First successful injection re-rolls `WillowVendingMachine` shelves (vendors stock at map load before mod hooks).
- Re-syncs every map load so `SparkLevelPatchEntry` / BLCMM / UCP patches never overwrite.

---

## Persistence & Save/Load

- Sidecar saves at `sdk_mods/_melee_arsenal_saves/` (JSON per-character, keyed by save slot).
- Hooks `WillowPlayerController:ValidateWeaponDefinition` / `ApplyWeaponSaveGameData` and `WillowSaveGameManager:SaveGame` to snapshot/restore inventory across map changes and save/load.
- Validation rejects zeroed-`UniqueId` pitfalls; `TickSpawnerRestore` staggers multi-weapon grants (1 per 3 ticks) to avoid the "Choose 1 of 2" mission-reward prompt (single-item `ServerGrantMissionRewards` with `[bal]` length 1 goes directly to backpack).

---

## InkBlade Kit & UPK Pipeline

- **Generator:** 10-stage stack `geometry → uv → flat → mottle → detail → decals → linework → textures → export → render` (see `tools/inkblade_gen/README.md` for stage table, caching fingerprints, and `--only`/`--from` flags).
- **Compiler:** `tools/compile_inkblade_upk.py` merges GLB + PNG → `MeleeArsenalMeshes.upk` / `MeleeArsenalTextures.upk` + `inkblade_compiled_manifest.json` with `kit_fingerprint` (hash of textures/UV/meshes). `melee install`/`dev` compare fingerprints and auto-recompile stale packages — hence `generate && dev` suffices.
- **Gestalt:** unified 22-part mesh + bifacial sockets + elemental atlas materials built in `runtime/custom_assets.py` / `asset_library.py`.
- **AssetLibrary registration:** `data/asset_library_manifest.json` freezes the current per-class registration order; additions append after the frozen prefix rather than alphabetically reshuffling prior IDs. The in-game `melee_export_asset_registry` command exports the observed order for deliberate baseline updates. The mock suite verifies ordering, but a live BL2 save/bank compatibility test remains required before changing registrations.
- **Testing:** `tests/test_inkblade_generator.py` (stage contract, cache chaining, geometry/texture regression hashes, bifacial, render), `tests/test_inkblade_upk.py` (UE3 codec, archive).

---

## Testing

```bash
./melee test          # byte-compile + unittest -v
./melee test -k throw # filter
```

Suites: `test_melee_core` (core math), `test_runtime_mock` (strict typed mock SDK), `test_runtime_regressions` (identity, persistence, lifecycle, options), `test_inkblade_generator`, `test_inkblade_upk`, `test_probe_mock`, `test_melee_cli`. The full suite has 4 optional donor/library-dependent skips; live BL2 save/bank, co-op, controller, and visual/performance checks are not simulated.

Mock SDK: fake `unrealsdk.find_object`, `find_class`, `construct_object`, `make_struct`, `WeakPointer`; validates `build_mod` options/hooks/commands and combat dispatch without a game install.

### Defensive Exception Handling

Runtime code interacts with live UE3 UObjects via `unrealsdk` — many calls can fail across builds (missing packages, dead wrappers, renamed fields). The mod uses broad `except Exception` intentionally to fall back to vanilla behavior rather than crash. Where an exception previously disappeared entirely, it now logs at debug level:

- `sdk_mods/melee_arsenal/runtime/log.py` provides `log_debug(msg)` (gated by `STATE.debug_attack_log`) and `log_debug_once(key, msg)` (once-per-site, avoids per-frame spam).
- Hot paths (per-frame ticks, per-hit damage, `WrappedArray` scans) use `log_debug_once` so a flaky SDK call does not flood `melee_arsenal.log`.
- Init paths (`_harvest_pistol_donors`, `_find`, package loading) use `log_debug` directly.
- Existing `log_error`/`log_warn` paths are preserved for actionable failures (e.g. `TakeDamage` re-call, `Behavior_CauseDamage`).

This keeps the defensive safety of broad catches while making real regressions visible when **MODS → Log Swing Damage Breakdown** is enabled.

---

## CLI Reference

| Command | Description |
| :--- | :--- |
| `./melee setup` | Create `.venv/` and install pinned Pillow (see `requirements.txt`) |
| `./melee generate [options]` | Regenerate kit (stages, `--list-stages`, `--only`, `--from`, `--force`, `--renders`, etc.) |
| `./melee build [--kit PATH]` | Compile kit → `build/WillowGame/CookedPCConsole/` + manifest |
| `./melee test [args]` | Byte-compile + unittest |
| `./melee preview` | Roll sample Lv 50 weapons, print cards & hit math |
| `./melee package [--build]` | Build `build/dist/{melee_arsenal.zip,melee_arsenal-vVERSION.zip,source.zip}` |
| `./melee install [--bl2-dir DIR] [--probe] [--no-rebuild]` | Stage UPKs + sync `sdk_mods/melee_arsenal` to BL2 |
| `./melee dev` | Quiet test + install (auto-recompiles stale UPKs) |
| `./melee status` | Repo, kit, full installed-payload hash sync (including both UPKs), sidecar counts |
| `./melee logs [-f] [-n LINES]` | Tail `sdk_mods/melee_arsenal/melee_arsenal.log` |
| `melee_export_asset_registry` (in game) | Export observed AssetLibrary registration order to the installed mod’s `data/asset_library_manifest.json`; copy it back to the source tree when intentionally freezing a new baseline. |
| `./melee uninstall [--purge-saves]` | Remove mod & UPKs |
| `./melee clean` | Remove bytecode caches, probe reports, `build/` |
| `./melee all` | `build → test → package → install` |

Environment: `BL2_DIR` (or cached `.bl2_dir` via `--bl2-dir`), `PYTHON` override.

---

## Historical Notes

- **Camera shake:** earlier blends were shorter and weaker; current camera playback is centralized, user-scaled, and skipped entirely at zero (no nonzero fallback).
- **Guard pose:** earlier rotated `Arms.SkeletalMesh`; now tilts `WillowWeapon.Mesh`. Restore was once lazy and drifted; now snapshot-at-apply + unconditional restore (v0.4.4).
- **Throw:** once read as "stops in mid-air, then jumps"; now continuous parabola with fallback ground trace disabled in favor of pure sweep. Damage was 200% in v0.3.3–v0.4.3.
- **FunStats:** green "Stacks with Player Melee & Roid Bonuses" line removed in v0.3.4.
- **Version labels:** `combat.py` carried `v0.2.6` long after mod hit `v0.4.5`; now unversioned.
- **SDK version:** `mods_base 3.8` was the manager tag, not the Base component (1.12); docstring corrected.
