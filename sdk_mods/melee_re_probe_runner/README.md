# Melee RE Probe Runner

Third mod in the melee-arsenal analysis stack:

- **melee-arsenal** — the mod under analysis (`sdk_mods/melee_arsenal/`)
- **re-probe** — reflection dumper, now library-driveable (`sdk_mods/re_probe/`)
- **melee-re-probe-runner** — this mod: tells RE Probe what to dump/trace, and parses results

## Install

Copy all three into the game's `sdk_mods/` directory:

```
Borderlands 2/
└── sdk_mods/
    ├── melee_arsenal/
    ├── re_probe/
    └── melee_re_probe_runner/
```

Enable **RE Probe** first, then **Melee RE Probe Runner**.

## Commands (console `~`)

Type `mrp help` for the full list. Key commands:

| Command | What it does |
|---|---|
| `mrp_start_dump [profile]` | Start a RE Probe dump (core/definitions/full) |
| `mrp_cancel_dump` | Cancel the running dump |
| `mrp_status` | Print RE Probe dump + trace status |
| `mrp_trace_arm [pattern ...] [max_hooks]` | Arm a trace (default: melee combat set) |
| `mrp_trace_stop` | Disarm hooks + write trace JSON |
| `mrp_trace_write` | Re-write current trace without stopping |
| `mrp_trace_status` | Print trace session state |
| `mrp_dump_held [depth]` | Dump the currently held weapon (ad-hoc) |
| `mrp_dump_object <path> [depth]` | Dump any loaded object by path |
| `mrp_find <pattern> [limit]` | List matching object paths |
| `mrp_reproduce [profile]` | Start dump + arm melee trace in one command |
| `mrp_parse <dump_dir>` | Parse a dump dir → findings report |
| `mrp_help` | Show this help |

## Typical workflow

1. Load into a character with a Melee Weapon equipped.
2. `mrp_reproduce full` — starts a full dump and arms the melee trace (384 hooks).
3. Play: swing (LMB), throw (Bandit/Torgue RMB), guard (Hyperion/Jakobs RMB), take a hit, open a melee item card.
4. `mrp_trace_stop` — writes `sdk_mods/re_probe/dumps/<id>/traces/trace-<session>.json`.
5. Wait for the dump to finish (`mrp_status`), note the dump directory.
6. `mrp_parse <dump_dir>` — writes `MRP_FINDINGS.md` + `MRP_FINDINGS.json` next to the dump.

Send the dump directory (or the `MRP_FINDINGS.*` files) to the analyst; the findings report lists every melee-arsenal engine assumption as CONFIRMED / PARTIAL / REFUTED with evidence.

## Mod menu options

| Option | Default | Effect |
|---|---|---|
| Enable Runner | On | `mrp_*` commands active when on |
| Auto-arm melee trace on enable | Off | Arms the melee trace set when the runner is enabled |
| Auto-start full dump on enable | Off | Starts a RE Probe full dump when the runner is enabled |
| Trace hook limit | 384 | Max functions hooked per trace session |

## How it works

The runner imports RE Probe as a library (`from re_probe import PROBE, driver`) and calls its programmatic `ProbeDriver` API — no console `reprobe ...` typing required. RE Probe stays a general SDK tool; the runner supplies melee-specific patterns (which functions to trace, when to dump) and parses the output into findings.

## Files

- `__init__.py` — mod wrapper, options, lifecycle
- `commands.py` — `mrp_*` console commands + standard melee trace pattern set
- `parser.py` — stdlib-only RE Probe dump reader (runs in-game)
- `findings.py` — CONFIRMED/PARTIAL/REFUTED findings builder
