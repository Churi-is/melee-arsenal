# Melee Research: evidence collector

This is a separate, temporary BL2 mod. It drives the included RE Probe, collects
focused evidence, and writes **one JSON report to send back**. It does not change
Melee Arsenal or write game saves; the normal game/mod actions you perform can
of course save or change your inventory. Work in a safe test save. Tracing adds
hooks and can affect performance. Reports can contain object paths, player names,
file paths and sampled function arguments: review before sharing.

## Install and run

1. Copy `sdk_mods/melee_research` to the game's `sdk_mods`. Copy the included
   `sdk_mods/re_probe` folder too. Keep its `probe` subfolder intact.
   Enable RE Probe, Melee Research, and Melee Arsenal in the mod manager. If
   already installed, use the included RE Probe version, which exposes `driver`.
2. Load a character in a populated map with Melee Arsenal active and a blade
   equipped. Open the console and run `meleeprobe start`. It starts a full dump;
   the initial object enumeration may briefly hitch. Monitor `meleeprobe status`
   and wait until the job's state is `complete` (or inspect errors if it fails).
   Let the game continue running; dump work is done on ticks.
3. Run `meleeprobe snapshot` once after the dump. It searches for pool and
   weight objects and mod-created definitions, then captures up to five objects
   per search at depth 4. Some will not be loaded; this is recorded. This runs
   synchronously and can briefly hitch. For additional evidence, visit a shop
   or a map with loot and run it again.
4. Run `meleeprobe trace combat`, wait until `meleeprobe status` says trace
   `armed`, then swing and hit enemies, try elemental/explosive blades if
   available, and use the secondary/guard/throw controls. Run `meleeprobe stop`.
5. Run `meleeprobe trace inventory`, wait until armed, inspect a melee item card,
   switch weapons, pick up/drop a blade, and trigger an ordinary save or reload
   if practical. Run `meleeprobe stop`. Sessions can be repeated; each is
   included. Avoid quitting the game in the middle of a session because this
   runner holds its session index in memory.
6. Run `meleeprobe report`. Send the printed
   `sdk_mods/melee_research/results/<timestamp>/melee-research-report.json`
   here. Keep the larger `dump/` and `traces/` directories in case we need
   follow-up. If the report is too large, compress it. `meleeprobe cancel`
   cancels only this runner's work and leaves partial files on disk.

If RE Probe is already dumping/tracing, finish that work first; the runner
refuses to replace it. Enable/disable RE Probe only outside a research session.
`status` includes the installed tick hooks; if none are installed, the dump and
trace arming cannot progress. Dump/trace failures and hook truncation are not
negative engine evidence. Do not run another `reprobe start/trace` concurrently.

## Why these questions?

* `runtime/loot_pools.py` estimates probabilities from known initialization
  names, guesses formula and attribute values, and substitutes 1 for unknowns.
  Pool and weight object snapshots help determine whether there is a reliable
  engine-evaluated path or whether the fallback misrepresents weights. A CDO or
  a layout by itself cannot prove a runtime probability.
* `runtime/definitions/balances.py` cautiously copies manufacturer grades and
  weights; the struct layouts and actual donor values can distinguish fields
  from assumptions. `WeaponDefinitionData` and save/validation signatures
  illuminate the similarly guarded restore path.
* `resolver.sync_live_weapon_properties` guards many live writes; class chains,
  sampled instances and function signatures can identify candidates for smaller
  code. Reflection absence alone cannot rule out native members or prove writes
  succeed. `ENGINE_FACTS.md` explicitly warns about this.
* Item-card arrows, firing/impact/damage, zoom and item initialization traces
  explore redundant hooks and call relationships. Observing a call does not
  prove necessity; an unobserved call does not prove it is unused. Samples can
  be capped, hooks can fail, and calls entirely in native code are invisible.
* Object and asset searches investigate fallback chains and mod-created objects;
  absence from a particular map/load state is not global absence.

The report embeds the manifest, raw matching records, counters and truncation
flags, search terms, trace arm/stop/status metadata, sampled traces and snapshots.
`trace_object_context` joins function-level object counts to sampled weapon
definitions at dump time; it is not a per-edge count, and objects can change or
paths be reused between the dump and trace. An unmatched object is unknown.
It makes no automatic confirmed/refuted claims. Matching is against record
identity, not all property text. Sections have explicit caps; raw shards remain
available. RE Probe's default depth, array, instance, xref and property limits
still apply. RE Probe's programmatic `start_dump` uses its own default settings,
not the options UI. This runner uses no third-party packages.

### Repeating traces after the registration fix

Replace both `re_probe` and `melee_research` with the updated folders and fully
restart the game first: older RE Probe sessions may have left hooks installed in
that process. Run the same combat and inventory scenarios above and send the new
report. For each trace, check `status_before_stop`: `armed_hooks` should equal
`arm.matched_functions`, `pending_hooks` should be zero, and `failed_hooks`,
`errors`, and `stack_drift` should ideally be empty/zero. The report now records
installed function paths and registration failures. Stack drift can still occur
when another mod blocks a call, so even a clean installation does not make an
absent event or a call graph definitive. Please also compare item cards with a
clearly better, worse and equal stat to check the corrected arrow mapping.

If a prior report still showed large `stack_drift` despite all hooks arming,
update RE Probe and restart the game before repeating the traces. The tracer now
keeps bounded stacks keyed by native thread ID instead of Python thread-local
storage. `discarded_frames` and `open_frames` in status and trace output help
identify remaining unmatched or abandoned frames; neither is expected to stay
high in a clean trace. This is a diagnostic change, not proof of call order.
