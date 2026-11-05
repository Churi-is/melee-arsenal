# `tools/re_probe_parse` — RE Probe dump parsers for melee-arsenal

Stdlib-only parsers that read RE Probe (`re_probe.v1`) dump directories and
check the assumptions baked into the melee-arsenal mod. Runs on any machine
that has a copy of the dump directory — no Borderlands 2, unrealsdk, or
mods_base required.

## Step 1 — run RE Probe in-game

### A. Full reflection dump (do this once, any loaded map)

Open the SDK console (`~`) and run:

```
reprobe start full
reprobe status            # repeat until it reports complete
```

When finished the console prints the dump directory, e.g.

```
sdk_mods/re_probe/dumps/20261105-143012/
```

`manifest.json` inside it is the authoritative section index.

### B. Runtime caller→callee trace (do this in gameplay)

Load into a character with a Melee Weapon equipped, then:

```
reprobe trace arm WillowGame.WillowWeapon:* 384
reprobe trace arm WillowGame.WillowPlayerPawn:TakeDamage
reprobe trace arm WillowGame.WillowAIPawn:TakeDamage
reprobe trace arm WillowGame.WillowPlayerController:PlayerTick
reprobe trace arm WillowGame.WillowPlayerController:ServerGrantMissionRewards
reprobe trace arm WillowGame.WillowPlayerController:ValidateWeaponDefinition
reprobe trace arm WillowGame.WillowPlayerController:ApplyWeaponSaveGameData
reprobe trace arm WillowGame.ItemCardGFxObject:SetTopStat
reprobe trace arm WillowGame.ItemCardGFxObject:SetTitle
reprobe trace arm WillowGame.ItemCardGFxObject:SetItemCard
reprobe trace arm WillowGame.ItemCardGFxObject:SetItemCardEx
reprobe trace arm WillowGame.ItemCardGFxObject:SetFunStats
reprobe trace arm WillowGame.WillowDamageTypeDefinition:DisplayRecentDamageForPlayer
reprobe trace arm WillowGame.WillowHUDMovieBase:AddTrainingText
reprobe trace arm WillowGame.Behavior_SpawnLootAroundPoint:PlaceSpawnedItems
reprobe trace arm WillowGame.WillowVendingMachine:ResetInventory
reprobe trace arm WillowGame.WillowSaveGameManager:SaveGame
```

Play for a minute: swing melee (LMB), throw (Bandit/Torgue RMB), guard (Hyperion/Jakobs RMB), take a hit, and open a melee weapon's item card. Then:

```
reprobe trace stop
```

This writes `sdk_mods/re_probe/dumps/<id>/traces/trace-<session>.json`.

> Tip: copy the whole dump directory (and the trace file) out of the game install
> before running the parsers — they only read files.

## Step 2 — run the parsers

From the melee-arsenal repo root:

```bash
# Everything at once: writes re_probe_reports/FINDINGS.md + FINDINGS.json plus
# one raw JSON per section (functions, classes, enums, objects, values, trace).
python -m re_probe_parse all <dump_dir> [<trace.json>] --out-dir re_probe_reports

# Or one section at a time:
python -m re_probe_parse functions <dump_dir> --out re_probe_reports/functions.json
python -m re_probe_parse classes <dump_dir>
python -m re_probe_parse enums <dump_dir>
python -m re_probe_parse objects <dump_dir>
python -m re_probe_parse values <dump_dir>
python -m re_probe_parse xrefs <dump_dir>
python -m re_probe_parse traces <trace.json>
```

Each subcommand prints a human-readable summary to stdout; `--out` (or `--out-dir`
for `all`/`report`) writes machine-readable JSON.

## What the report tells you

`re_probe_reports/FINDINGS.md` lists every assumption in the mod
(`runtime/__init__.py`, `resolver.py`, `loot_pools.py`, `definitions/balances.py`,
`ui_item_card.py`, `combat/hooks.py`, `docs/ENGINE_FACTS.md`, …) with a status:

- **CONFIRMED** — the dump matches the assumption; the code can be simplified.
- **PARTIAL** — partly matches; verify before changing.
- **REFUTED** — the assumption is broken in this build; do not touch the related
  code until it is rewritten.
- **UNVERIFIED** — could not be checked (usually because no trace was supplied).

## What to send back

Send me the `re_probe_reports/` directory (or paste the contents of
`FINDINGS.md` plus the raw `functions.json` / `classes.json` / `trace.json`
sections you ran). I'll then propose the concrete code changes: hooks to drop,
guards to remove, hard-coded constants to replace with dumped offsets, and any
new safety checks the dump reveals are needed.

## Files

- `loader.py` — dump/shard reading (manifest-authoritative, glob fallback)
- `functions.py` — UFunction signatures for every function the mod hooks/calls
- `classes.py` — class property checks (declared + inherited via super_chain)
  and struct layouts (WeaponDefinitionData, WeightedInventoryBalanceData, …)
- `enums.py` — ETopStatArrow etc. (CompareArrow byte mapping)
- `objects.py` — donor path / asset presence checks
- `values.py` — CDO defaults, live instances, globals
- `xrefs.py` — static object reference edges
- `traces.py` — observed caller→callee edges from the runtime tracer
- `assumptions.py` — the master assumption list mapped to checks
- `report.py` — aggregate CONFIRMED/PARTIAL/REFUTED findings
- `cli.py` — `python -m re_probe_parse` entry point
