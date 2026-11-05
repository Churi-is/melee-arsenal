# Ground truth: how much of the caution can go?

An audit of `sdk_mods/melee_arsenal` for *defensive code that exists only because a fact was unknown
at authoring time*, what the probe mod measures, and how many lines each answer removes.

The short version: **about 1,300 of the 12,981 runtime lines (≈10%) are pure rediscovery** and can be
deleted outright once measured. A further ~250 lines are structural scaffolding that collapses but
does not disappear. Roughly 40% of the 262 `try` blocks shrink to a single line.

Measured baseline (`sdk_mods/melee_arsenal`, excluding `core/` data tables):

| signal | count |
| --- | --- |
| Python lines | 12,981 |
| `try:` / `except` blocks | 262 |
| `except Exception: pass` | 108 |
| `hasattr(...)` guards | 69 (≈889 lines of guard scaffolding) |
| `getattr(..., default)` | 331 |
| `log_debug_once(...)` "this might fail" sites | 43 |
| hooks registered | 37, across 32 distinct functions (4 pairs serve one intent) |

---

## The three kinds of caution

Not all defensiveness is waste. The audit separates three kinds, because only the first two can be
measured away.

### 1. A constant fact that was never looked up — measurable, deletable

The engine's answers here do not vary between sessions, installs, or players:

- does `WillowGame.WillowWeapon:InitializeInternal` exist, and what are its parameters called?
- does `FiringModeDefinition` have a `SoundDefinition` property?
- what integer is `ECompareStat.ECS_Better`?
- which loaded `ParticleSystem` is the real explosive one?
- which Flash icon string does slot 2 carry?
- what field names does `AttributeInitializationData` have?

Every one of these is currently answered at run time, on every player's machine, forever. This is the
bulk of the cleanup.

### 2. A fact that varies by build — measurable *for your build*, then assert

`Engine.GameViewportClient:Tick` is "not a UFunction on every BL2 build" per `_run_frame_tick`'s own
docstring. That belief produced a two-hook tick driver and a per-frame de-duplication guard. The probe
does not make the fact universal — it makes it *known for the build being shipped*, so the fallback
can be conditional on a single checked constant rather than living in the hot path.

`_DONOR_POOL_TEMPLATES` (six spelled pool paths, "drifts between BL2 patches, DLC packages, and UCP")
is the same shape: probe the loaded pools once, ship the real ones, keep one fallback rather than six.

### 3. A fact that genuinely varies at run time — must stay defensive

These are not probe-able and should keep their guards:

- **Object lifetime.** A `WillowWeapon` wrapper can go stale between two frames; `is_dead()`,
  `deref_weak()`, and the `except` blocks around engine calls are correctness, not caution.
- **Construction and GC.** `construct_object` + `keep_alive` + rooting order is the mod's own
  invariant; it cannot be pre-measured.
- **Save/load and sidecar restore.** Inventory state depends on the player's file, difficulty, DLC
  set, and whether a mod manager patched the level. `persistence_bridge.py` stays defensive.
- **Map-load races.** Pools rebuilt by SparkLevelPatchEntry / BLCMM / UCP after load mean
  "re-sync every map load" is a requirement, not hedging.
- **Taste.** Camera shake falloff, guard tilt, slash duration. Measured only by a human looking at it.

The probe's ledger deliberately names only category 1 and 2 sites.

---

## What the probe answers, and what it deletes

Line counts are `func end - func start` from the current tree.

| # | Question | Runtime code it retires | ~lines |
| --- | --- | --- | --- |
| 1 | Which candidate hooks exist, and their real parameter names? | 37 registrations across 32 functions (a dead `Engine.GameViewportClient:Tick` costs 2); `getattr(args, …, default)` probing in 12 callbacks; the `InitializeFromDefinitionData`/`InitializeInternal` bisection | 60 |
| 2 | Which of the paired hooks actually fire, in what order? | `_run_frame_tick`'s `if now - _last_frame_tick_at < 0.002` guard; `StartZoom`+`ClientStartZoom`, `StopZoom`+`ClientStopZoom` pairs; the card `depth` counter between `SetItemCard` and `SetItemCardEx` | 45 |
| 3 | Which properties exist, and their class defaults? | 560 lines of `if hasattr(obj, "X"): try: setattr(...) except Exception: pass` in `definitions/balances.py` (230), `name_parts.py` (191), `weapon_types.py` (133), `parts.py`, `loot_pools.py`; `_clear_array_prop` / `_zero_vector_struct` / `_dump_uobject_fields_summary` guessers | 560 |
| 4 | What are the enum literals and their integer values? | `ui_item_card._discover_compare_enum` (40) reflect-scanning `SetTopStat` for a `CompareArrow` property and substring-matching `("none"\|"blank"\|"better"\|"higher"\|…)`, plus `_arrow_byte`'s fallback map | 45 |
| 5 | What is the field layout of `AttributeInitializationData` and friends? | `_eval_prob_weight`'s four-step probing (58); `resolver._apply_behavior_to_context` / `_set_spawner_custom_location` struct-name reflection | 40 |
| 6 | What exact explosion `ParticleSystem` / `AkEvent` paths exist? | `_score_explosion_ps_path` (48) + `_score_explosion_ak_path` (35) + `_cache_torgue_explosion_assets` (58) keyword scoring and the `find_all` fallback scan; the `or _safe_find(…) or _safe_find(…)` chains; `secondary_vfx._fallback_material` | 190 |
| 7 | Which firing mode actually carries a clonable `Behavior_Explode`? | `_attach_torgue_explode_on_impact`'s `find_all("FiringModeDefinition")` donor hunt | 40 |
| 8 | What is the real constant behind each `AttributeInitializationDefinition`? | `_KNOWN_WEIGHT_INIT_DEFS` name matching (`Weight_4_Rare` → `1.0`), `_eval_prob_weight` fallback chain | 60 |
| 9 | Which weapon pools are loaded, with what entries and gates? | `_DONOR_POOL_TEMPLATES` (6 spelled paths), `_find_pool` casing/package tolerance, `_iter_loaded_pools` `find_all` feature test, `_sanitize_pool_object` `hasattr` cluster | 50 |
| 10 | Which `AnimNodeSlot` nodes does `Pawn.Arms` own? | `_get_arms_anim_slots`' global `find_all("AnimNodeSlot", exact=False)` + ownership match; `_POSE_KIND` / fallback-arc branching | 45 |
| 11 | Is `Bladed_Melee` a real sequence? | the `for seq_name in ("Bladed_Melee", "Melee")` retry loop and its nested `try` | 20 |
| 12 | Are `Mesh.Materials` entries materials or `{Material, …}` structs? | `secondary_vfx._material_of` (7) + `_fallback_material` (13) slot probing | 25 |
| 13 | Which class names do melee victims actually have? | `attacks.is_target_dummy` path-string matching; `is_valid_melee_victim`'s `"InteractiveObject" in cls_name or "Destructible" in …` chain | 35 |
| 14 | What icon strings and labels does a vanilla card send? | `observed_icons` / `observed_elem_icon` dynamic learning, `_ELEMENT_ICON_FALLBACK`, `or "pistol"` / `or ""` argument defaults | 70 |
| 15 | What is the real frame interval? | `_DEFERRED_STEP_TICKS = 3`, `_MISSION_RESTORE_TICKS`, `% 30`, `% 60`, `>= 90` — replaced by named milliseconds | 25 |
| 16 | Where does BL2 keep `Behavior_SpawnProjectile` for the throw VFX? | the Tediore donor path folklore and the shape probes around it | 15 |
| | **total** | | **≈1,325** |

Beyond line count, three structural wins that are worth more than the diff:

- **One hook per intent.** `HOOKS` becomes a list where no two entries serve the same purpose, so the
  "which of these won" logic disappears from every callback.
- **Constants become inspectable.** `VERIFIED_*` modules of paths, enums, icons, and property names can
  be read, reviewed, and diffed — unlike the current behaviour, which only exists at run time.
- **Failures become loud.** Today a wrong guess is silently caught (`log_debug_once` at 43 sites). With
  a constant, a wrong value throws on the first call in testing.

---

## What does *not* shrink (be honest with yourself)

| stays | why |
| --- | --- |
| `object_identity.py` (87 lines) | wrapper churn is real; path identity is required |
| `persistence_bridge.py` (371 lines) | depends on the player's save, DLC set, and other mods |
| `state.keep_alive` / rooting | GC order is the mod's own invariant |
| ~120 of 262 `try` blocks | engine calls genuinely fail on dead objects and unloaded packages |
| `commands.py` parsing / validation | user input, not engine facts |
| tuning constants (`pose.py`, camera shake) | needs eyes, not a probe |

So the realistic ceiling is **≈10% of lines and ≈25% of the branching**, not a rewrite. The benefit is
concentrated: the files that are hardest to read today (`definitions/balances.py`,
`definitions/name_parts.py`, `resources.py`, `loot_pools.py`, `ui_item_card.py`) are exactly the files
the ledger targets.

---

## Suggested migration order

Each step is independently shippable and testable in game.

1. **Freeze the facts (no runtime change).** Run the probe, commit
   `sdk_mods/melee_arsenal/data/verified_*.json` (or a `runtime/verified.py`), and add a
   `./melee verify` check that fails when a probe dump disagrees. Zero risk.
2. **Delete rediscovery in `resources.py` + `firing_modes.py`** (~230 lines). Replace the scorers with
   the verified path constants; keep one `or` fallback. This is the largest single win per unit of risk.
3. **Flatten the `hasattr` scaffolding in `definitions/`** (~560 lines). Do one file per PR:
   `balances.py`, `name_parts.py`, `weapon_types.py`, `parts.py`. Assign directly; let a `try` at the
   outermost build step catch the truly-unknown.
4. **Collapse the hook pairs** (~105 lines). Use `probe_plan` for dead functions, `probe_census` for
   ordering, then delete one hook of each pair and the de-duplication logic.
5. **Convert the self-learning** (icons, anim slots, materials, victims, timings) (~250 lines) into
   constants with a single assertion on first use.
6. **Re-run the probe** on a build with DLC and UCP installed to confirm the "varies by build" set, and
   record the deltas as a small compatibility table instead of per-call tolerance.

## Running the probe

See [`sdk_mods/melee_arsenal_probe/README.md`](../sdk_mods/melee_arsenal_probe/README.md).

```bash
./melee probe          # install into the game's sdk_mods/
# in game: enable "Melee Arsenal Probe", play a minute, run probe_dump
python3 tools/probe_selftest.py   # offline verification of the probe itself
```

`probe_dump` writes each section to `ground_truth/section_<name>.json` as it completes (census ->
live -> schema -> catalogue), so a failing section cannot discard the ones before it.
