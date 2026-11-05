# Melee Arsenal Ground-Truth Probe

A developer mod that **measures Borderlands 2** so `melee_arsenal` can stop measuring it at run time.

`melee_arsenal` is 13,000 lines largely because it cannot see the engine while being written. Where
a fact was unknown it reaches for the safe option: `hasattr` before every assignment, a keyword
scorer ranks every loaded `ParticleSystem` to pick an explosion effect, `StartZoom` **and**
`ClientStartZoom` are both hooked, the item card *learns* Gearbox's Flash icon names by watching
vanilla cards go by. All of that is trial and error that runs on the player's machine, forever.

This probe runs the trial **once**, writes the answer down, and tells you which line of runtime code
the answer deletes.

---

## Install

```bash
./melee probe            # copies this folder into the game's sdk_mods/
```

Then in game: **MODS → Melee Arsenal Probe → Enabled**. It is `auto_enable = false` on purpose — a
probe should be a deliberate act.

It is standalone: it never imports `melee_arsenal`, so it still works when the mod under test fails
to load. It writes only to its own `ground_truth/` folder. To remove it, delete
`sdk_mods/melee_arsenal_probe/` from the game. It is never part of a release archive.

## Record a session

1. Enable the probe. Leave **Record Hook Census** on and **Census Argument Samples** at 4.
2. Load any gameplay map and play normally for about a minute, doing the things the runtime cares
   about:
   - swing a gun's melee (`V`) — *not* required, but harmless,
   - equip and swing **Melee Arsenal** weapons,
   - hold and release RMB (guard and throw),
   - open the backpack and hover a **vanilla** gun so its item card renders,
   - hit a target dummy in Sanctuary.
3. Run `probe_dump`.
4. Copy `ground_truth/ground_truth.json` and `ground_truth/GROUND_TRUTH.md` into the repository
   (or attach them to the issue you are working on).

`probe_dump` runs **census -> live -> schema -> catalogue** and writes each section to its own
`ground_truth/section_<name>.json` the moment it finishes, so a section that fails can never cost you
the ones before it. If the game does hard-crash, `ground_truth/last_section.txt` names the section
that was running, and everything before it is already on disk.

A note on what the probe will *not* do: it never calls `unrealsdk.load_package`. BL2's `LoadPackage`
takes the game down with a UE3 "Failed to find file for package ..." fatal error when handed a name
that is not a real `.upk` — a bad class path segment like `WillowGame` is exactly that, and it is not
a catchable Python exception. Function existence is checked with `find_object` / `Class._find` only.

## Commands

| command | answers |
| --- | --- |
| `probe_dump` | everything below, one JSON + one review sheet |
| `probe_schema` | which UFunctions exist, their real parameter names, class/struct/enum layouts, class-default values (no gameplay needed) |
| `probe_assets` | every loaded `ParticleSystem` / `AkEvent` / `CameraAnim` / `ImpactDefinition` / `DamageTypeDefinition` path, firing-mode behaviour graphs, loot pools with weights, `AttributeInitializationDefinition` constants |
| `probe_live` | current pawn, held weapon, `Pawn.Arms` mesh, `AnimNodeSlot` node names, melee animation sequences, material-array shape, victim class names, Tediore `Behavior_SpawnProjectile` providers |
| `probe_census` | per-hook call counts, call order, first-call argument dumps, frame cadence, and per-group verdicts |
| `probe_reset` | clear the counters and measure again |
| `probe_plan` | which census hooks resolve on this build, and which are already known-dead |
| `probe_where` | print the output paths |

## What each answer deletes

`ground_truth.json` carries the ledger (`targets`), and the review sheet renders it. Summary of the
~1,300 runtime lines the probe's sections unblock:

| probe section | runtime code it lets you delete or flatten |
| --- | --- |
| `schema.functions` / `census.groups` | duplicate hooks for one intent (`Engine.GameViewportClient:Tick` + `PlayerTick`, `StartZoom` + `ClientStartZoom`, `SetItemCard` + `SetItemCardEx`), the per-frame de-duplication guard, `getattr(args, …)` parameter probing |
| `schema.classes` | the ~890 lines of `if hasattr(obj, "X"): try: setattr(...) except: pass` in `definitions/`, plus `_clear_array_prop` / `_zero_vector_struct` guessers |
| `schema.enums` | `ui_item_card._discover_compare_enum` reflection + substring matching |
| `schema.structs` | `loot_pools` `AttributeInitializationData` field probing, `resolver` struct-name reflection |
| `assets.catalogues` | `resources._score_explosion_ps_path` / `_score_explosion_ak_path` / `_cache_torgue_explosion_assets` (141 lines) and the `or _safe_find(…)` candidate chains |
| `behaviours.explosion_donors` | the global `FiringModeDefinition` scan hunting a `Behavior_Explode` |
| `behaviours.attribute_init_defs` / `pools` | `_KNOWN_WEIGHT_INIT_DEFS` name matching, `_eval_prob_weight` fallback chain, `_DONOR_POOL_TEMPLATES`, `_find_pool` casing tolerance |
| `live.arms` | `_get_arms_anim_slots` global scan, the `("Bladed_Melee", "Melee")` retry loop, `_POSE_KIND` fallback branching |
| `live.held_weapon.mesh` | `secondary_vfx._material_of` / `_fallback_material` entry-shape guessing |
| `live.world_actors` | `attacks.is_target_dummy` path matching, `is_valid_melee_victim` substring class checks |
| `live.item_card_observed` | `observed_icons` / `observed_elem_icon` learning and `_ELEMENT_ICON_FALLBACK` |
| `live.frame_cadence` | `_DEFERRED_STEP_TICKS`, `_MISSION_RESTORE_TICKS`, `% 30` / `% 60` / `>= 90` magic numbers, replaced by real milliseconds |

Probing does not excuse *everything*. Genuine runtime variance stays defensive: custom object
construction, save/sidecar restore, and map-load ordering are different every session and no probe
can pin them down. The point is to stop being defensive about facts that never change.

## How it is verified without the game

`tools/probe_selftest.py` installs a miniature fake `unrealsdk` + `mods_base` describing a slice of
BL2 (a weapon, a firing mode carrying a `Behavior_Explode`, two item pools, an arms mesh with
`Bladed_Melee`, mixed material entries, a target dummy) and asserts the exact ground truth that comes
back. CI runs it on every push, so a probe that quietly returns nothing fails the build instead of
wasting a play session.

```bash
python3 tools/probe_selftest.py
```

## Layout

```
melee_arsenal_probe/
├── __init__.py        # package entry; imports the surface only inside the game
├── mod_surface.py     # Mod Menu options, console commands, hook wiring
└── probe/
    ├── common.py      # reflection + JSON helpers (read-only, never writes to an object)
    ├── schema.py      # functions, classes, structs, enums
    ├── assets.py      # asset catalogues, behaviour graphs, pool weights
    ├── census.py      # hook firing counts / order / arguments / frame cadence
    ├── live.py        # in-map snapshot: pawn, arms, materials, victims
    └── report.py      # orchestration, JSON output, review sheet, cleanup ledger
```

## Notes

- Every hook the census installs is a `PRE`/`POST` counter that returns `None`. Nothing is blocked,
  no property is written, no object is constructed.
- All scans are bounded (`MAX_FIND_ALL`, `MAX_POOLS`, `MAX_FRAME_SAMPLES`, per-hook sample caps) so
  a dump cannot stall a frame.
- A section that throws is recorded under `errors` and the rest of the run still completes.
- `probe_plan` before playing tells you which candidate hooks do not even exist on your build —
  those are deletable without a play session.
