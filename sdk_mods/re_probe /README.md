# RE Probe

RE Probe is a read-only Borderlands 2 reflection dumper for
[Willow2 Mod Manager](https://github.com/bl-sdk/willow2-mod-manager) /
[mods_base](https://github.com/bl-sdk/mods_base) / pyunrealsdk.

It walks the live Unreal Engine 3 object graph and writes strict, sharded JSON
covering:

* every loaded `UObject` and its path, class, outer and package;
* every `UClass`, its super chain, subclasses, declared properties and functions;
* every `UFunction` with **parameter names, types, offsets, flags, out/return
  markers and a readable signature**;
* every `UScriptStruct` with a byte-offset layout;
* `UEnum` members, `UConst` values, CDO defaults, live instance values and
  globals;
* asset paths and packages;
* object cross-references;
* **observed caller → callee edges** from an on-demand runtime tracer.

The complete schema and query cookbook is in
[`docs/JSON_FORMAT.md`](docs/JSON_FORMAT.md). That is the only document you need
for the JSON.

---

## Requirements

* Borderlands 2 (the mod declares `Game.BL2`).
* A mod-manager install that provides `mods_base` and `pyunrealsdk`
  (Willow2 Mod Manager / the BL2 SDK setup).
* Python 3.10+ inside the game (pyunrealsdk bundles its own interpreter).
* No extra Python packages. Everything uses the standard library plus
  `unrealsdk` / `mods_base`.

This repository is the mod itself. Copy `sdk_mods/re_probe` into the game's
`sdk_mods` directory, so the final layout is:

```
Borderlands 2/
└── sdk_mods/
    └── re_probe/
        ├── __init__.py
        ├── probe/
        └── docs/
```

Enable **RE Probe** in the mod manager. Enabling installs the tick driver and
the `reprobe` console command; it does not start a dump automatically unless you
turn on **Auto-start full dump**.

---

## Quick start

1. Load into the map you want to inspect (the character/menu state affects what
   objects exist).
2. Open the console (`~` or the console key) and run:

   ```
   reprobe start full
   ```

3. Watch progress with:

   ```
   reprobe status
   ```

4. When it completes, the console prints the dump directory. It is also shown by
   `status`:

   ```
   sdk_mods/re_probe/dumps/20261105-143012/
   ```

5. Read `manifest.json` first, then follow
   [`docs/JSON_FORMAT.md`](docs/JSON_FORMAT.md).

A full dump does its work in small slices on game ticks. It is deliberately
incremental so the game keeps running. Expect it to take from seconds to a few
minutes depending on how much is loaded and the tick budget.

---

## Console commands

```
reprobe help
reprobe start [full|definitions|core]
reprobe status
reprobe cancel
reprobe dump <object path> [depth]
reprobe find <substring|glob> [limit]
reprobe trace arm [pattern ...] [max_hooks]
reprobe trace status
reprobe trace stop
reprobe trace write
```

### `start`

| Profile | Sections | Use when |
| :--- | :--- | :--- |
| `core` | classes, functions, structs, enums, consts | You only need the reflection/type dictionary. Smallest and fastest. |
| `definitions` | core + objects, packages, assets | You need every object path and asset type, but not values. |
| `full` | definitions + defaults, instances, globals, xrefs | You want values and references as well. This is the default. |

Only one dump runs at a time. `start` while a job is running is rejected; use
`cancel` first if you want to restart.

### `dump <object path> [depth]`

Dumps one object immediately to `dumps/adhoc/`, including its class definition
and its live property values. Depth is clamped to 0–8 (default 3). Paths with
spaces must be quoted. Examples:

```
reprobe dump WillowGame.Default__WillowWeapon 4
reprobe dump "WillowGame.WillowWeapon_0"
```

### `find <substring|glob> [limit]`

Case-insensitive substring match, or a `*`/`?` glob, over all object paths.
Default limit 40. Example:

```
reprobe find WillowWeapon
reprobe find *ConsumeAmmo*
```

### `trace` — caller/callee capture

BL2 ships without UnrealScript bytecode, so a static call graph cannot be
recovered from reflection. RE Probe instead hooks selected `UFunction`s and
records caller → callee edges while you play.

```
reprobe trace arm WillowGame.WillowWeapon:*
reprobe trace arm *:ConsumeAmmo 256
reprobe trace arm WillowGame.WillowPlayerController:PlayerTick
reprobe trace status
reprobe trace stop
```

* Patterns match the full path, `Class:Function`, or a bare function name;
  `*` and `?` are supported.
* The default hook cap is 512 (configurable 16–4096). Arming everything is not
  useful and will slow the game down.
* Hooks are installed gradually over the next few frames to avoid a hitch.
* `trace stop` removes the hooks and writes
  `dumps/traces/trace-<session>.json`.
* `trace write` writes the current aggregation without stopping (useful for a
  checkpoint mid-session).
* A `caller` of `null` means the call came from outside the hooked set (native
  code, engine tick, or a function you did not arm).

### `status` / `cancel`

`status` prints the current phase, counts, elapsed time, trace session state and
output directory. `cancel` stops the job, closes the current shard, and writes a
`cancelled` manifest so the partial dump is still navigable. Shards that were
closed before the cancel are valid; the section that was in flight may be
shorter than the journal's latest count.

---

## Options

The mod options menu exposes the same settings. Options take effect on the next
dump (a running job keeps the settings it started with, except trace limits).

| Option | Default | Meaning |
| :--- | :--- | :--- |
| Dump profile | `full` | `full`, `definitions`, or `core`. |
| Capture CDO defaults | on | Read default values from class default objects. |
| Capture live instances | on | Read values from a capped sample of live non-definition objects. |
| Capture globals | on | Capture `UConst`s and live engine/game-info singletons. |
| Include inherited CDO values | off | Off = each class's defaults contain only its declared properties; walk `super_chain` for the rest. On = every effective property. |
| Value depth | 2 | Nested struct/array depth in value sections (1–6). |
| Array item limit | 24 | Max elements per array/static array (0–256; 0 = unlimited). |
| String length limit | 384 | Max characters per string (64–4096). |
| Live instance limit | 1000 | Max live objects whose values are captured (0–10000). |
| Cross-reference limit | 200000 | Max distinct `xrefs` edges (10000–1000000). |
| Tick budget (ms) | 6 | Dump work per game tick (1–30 ms). Lower is smoother. |
| Trace hook limit | 512 | Max functions hooked by one trace session (16–4096). |
| Auto-start full dump | off | Start `full` as soon as the mod is enabled. |
| Start full dump / Start definitions dump / Cancel dump | — | Buttons for the same operations. |
| Write trace | — | Writes the current trace aggregation. |

There is also an unbound keybind, **Start full dump**. Bind it in the mod
manager's keybind UI if you prefer not to type the command.

---

## What is in a dump

```
sdk_mods/re_probe/dumps/<dump_id>/
├── dump.json              live journal: status, phase, section counts, errors
├── manifest.json          written last; authoritative section/shard index
├── classes-*.json
├── functions-*.json
├── structs-*.json
├── enums-*.json
├── consts-*.json
├── objects-*.json
├── packages-*.json
├── assets-*.json
├── defaults-*.json
├── instances-*.json
├── globals-*.json
├── xrefs-*.json
└── traces/
    └── trace-*.json
```

Every shard is `{"schema", "section", "shard", "items": [...]}` and is valid
JSON on its own. `manifest.json` lists exactly which shards were completed, item
counts, byte sizes, the profile/limits used, the environment, and any errors.

Read [`docs/JSON_FORMAT.md`](docs/JSON_FORMAT.md) for the schema of each section,
the meaning of `$ref`/`$type`/`$truncated` and friends, and `jq`/Python/DuckDB
recipes.

### Mapping the original questions to sections

| Question | Section |
| :--- | :--- |
| What objects are loaded? | `objects` |
| What classes exist, and what is their inheritance? | `classes` (`super`, `super_chain`, `subclasses`) |
| What functions exist, with parameter names/types? | `functions` (`params`, `display_signature`) |
| What does this class declare? | `classes[].properties` / `[].functions` |
| What is the memory layout of a struct? | `structs[].properties` (`offset`, `size`) |
| What are the enum values? | `enums` |
| What are the global constants/singletons? | `consts`, `globals` |
| What are the default values? | `defaults` (CDOs) |
| What are the live values on this object? | `instances` or `reprobe dump` |
| What references this object/function? | `xrefs` |
| **Who calls this function / what does it call?** | `traces/trace-*.json` (runtime) |
| What asset paths exist? | `objects`, `assets`, `packages` |

---

## Important limits

* **Call graphs are dynamic.** BL2 has no script bytecode. The tracer observes
  calls that pass through UE3 `ProcessEvent`/`CallFunction`; calls made entirely
  inside native C++ code are invisible. Arm a focused set, reproduce the
  behaviour, then stop.
* **Definitions are declared-only.** Inherited properties are not repeated in
  every class; walk `super_chain`.
* **Defaults are direct-only by default.** Enable *Include inherited CDO values*
  for the full effective state.
* **Instances are a sample.** `manifest.summary.instance_candidates` vs
  `instances` shows how many were available versus written.
* **Values are capped.** Arrays, strings, depth, property count and xrefs all
  have limits; the markers in the JSON tell you what was elided.
* **Addresses/indices are process-local.** `address` is useful only against the
  same running process; `path` is the stable key.
* **The mod is read-only.** It never writes to saves, packages or object state.

If a dump feels too large, use `core` or `definitions`, lower the instance/xref
limits, or raise the tick budget to finish faster. If values are missing, raise
the array/string/depth/property limits and re-dump.

---

## Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| No `reprobe` command | The mod is not enabled, or `mods_base` did not load. Check the mod manager log. |
| Dump sits at `collect` | The tick driver hooks `WillowPlayerController:PlayerTick`. Load into gameplay (not the main menu) and wait a frame. |
| `status` says running but counts do not move | Raise *Tick budget*, or check the log for repeated read errors; the job continues past individual field errors. |
| Game hitches while dumping | Lower *Tick budget* to 2–3 ms and lower *Live instance limit* / *Xref limit*. |
| Start pauses for a few seconds | Normal on a loaded save: collection snapshots every object's path and class once so later phases never touch stale wrappers. The pause is longer with hundreds of thousands of objects. |
| Trace shows only `null` callers | Only armed functions are tracked; add the caller's class/pattern. |
| Trace file is huge / game slows while armed | Lower *Trace hook limit*; narrow the pattern; stop the trace when done. |
| `manifest.json` absent | The dump was cancelled or failed. Read `dump.json` for the last phase/status; closed shards are still valid. |
| Fatal error / `(filename not found)` during a dump | Older builds held live UObject wrappers for the whole dump, and a destroyed object could fault natively. 1.0.1 snapshots only path/class metadata at collection, drops the wrappers, and re-resolves each object immediately before reading it; destroyed objects are skipped. If it still happens, disable other mods, run `reprobe start definitions` to isolate the phase, and send the last lines of `unrealsdk.log`. |

---

## Development

The code is intentionally dependency-free and import-guarded, so it can be
compiled outside the game:

```bash
python3 -m compileall -q sdk_mods
PYTHONPATH=sdk_mods python3 -c "import re_probe; print(re_probe.__version__)"
```

`re_probe/__init__.py` sets `mod = None` when `mods_base` is unavailable, which
lets static analysis and packaging tools import the package without the game.

---

## Version

RE Probe 1.0.1 — JSON schema version 1.
