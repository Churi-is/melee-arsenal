"""Console commands for the Melee RE Probe Runner.

All commands are registered under the ``mrp`` prefix and drive RE Probe
programmatically through its :class:`~re_probe.probe.driver.ProbeDriver` API,
so the player never needs to type ``reprobe ...`` by hand.

Commands
--------
mrp_start_dump [profile]
    Start an RE Probe dump (core / definitions / full). Default: full.

mrp_cancel_dump
    Cancel the running dump.

mrp_status
    Print RE Probe dump + trace status.

mrp_trace_arm [pattern ...] [max_hooks]
    Arm a trace session on the given function patterns.
    Examples:
        mrp_trace_arm WillowGame.WillowWeapon:* 384
        mrp_trace_arm *:ConsumeAmmo WillowGame.WillowPlayerController:PlayerTick
    If no patterns are given, the standard melee combat set is armed.

mrp_trace_stop
    Disarm hooks and write the trace aggregation.

mrp_trace_write
    Re-write the current trace aggregation without stopping.

mrp_trace_status
    Print trace session state.

mrp_dump_held [depth]
    Dump the currently held weapon (ad-hoc, depth 0-8, default 3).

mrp_dump_object <path> [depth]
    Dump any loaded object by path.

mrp_find <substring|glob> [limit]
    List matching object paths.

mrp_reproduce [profile]
    Run a standard melee profiling session: start a full dump (or the given
    profile) and arm the standard melee combat trace in one command.

mrp_parse <dump_dir>
    Parse a completed RE Probe dump directory and write a findings report
    (markdown + json) next to it.

mrp_help
    Show this help.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # in-game path
    import mods_base  # type: ignore[import-not-found]
    import unrealsdk  # type: ignore[import-not-found]
    from unrealsdk.hooks import Type  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - offline inspection / tooling
    mods_base = None  # type: ignore[assignment]
    unrealsdk = None  # type: ignore[assignment]
    Type = None  # type: ignore[assignment]

try:  # re_probe is the library this runner drives
    from re_probe import PROBE, driver  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - re_probe not installed yet
    PROBE = None  # type: ignore[assignment]
    driver = None  # type: ignore[assignment]


MOD_NAME = "Melee RE Probe Runner"
COMMAND_PREFIX = "mrp"

# Standard melee-relevant trace set.  Hooking all of these at once stays well
# under the default 512-hook cap and covers the whole melee call flow:
#   * weapon lifecycle & firing (InstantFire, ConsumeAmmo, ...)
#   * pawn combat (TakeDamage, Behavior_CauseDamage, IsEnemy, ...)
#   * player controller & save (PlayerTick, SaveGame, mission rewards, ...)
#   * item card rendering (SetTopStat, SetTitle, ...)
#   * loot / spawning (PlaceSpawnedItems, ResetInventory, ...)
MELEE_TRACE_PATTERNS: list[str] = [
    "WillowGame.WillowWeapon:InstantFire",
    "WillowGame.WillowWeapon:ConsumeAmmo",
    "WillowGame.WillowWeapon:SharedFireAmmunition",
    "WillowGame.WillowWeapon:FiringModeDefinitionFire",
    "WillowGame.WillowWeapon:PlayFireEffects",
    "WillowGame.WillowWeapon:HasActiveAmmo",
    "WillowGame.WillowWeapon:PlayAnimation",
    "WillowGame.WillowWeapon:StartZoom",
    "WillowGame.WillowWeapon:StopZoom",
    "WillowGame.WillowWeapon:ClientStartZoom",
    "WillowGame.WillowWeapon:ClientStopZoom",
    "WillowGame.WillowWeapon:InitializeInternal",
    "WillowGame.WillowWeapon:ChooseRandomNameParts",
    "WillowGame.WillowWeapon:InitConstructPreview",
    "WillowGame.WillowPawn:Behavior_CauseDamage",
    "WillowGame.WillowPawn:Behavior_CauseRadiusDamage",
    "WillowGame.WillowPawn:IsEnemy",
    "WillowGame.WillowPawn:IsAliveAndWell",
    "WillowGame.WillowPawn:PlayAkEvent",
    "WillowGame.WillowPawn:WeaponFired",
    "WillowGame.WillowAIPawn:TakeDamage",
    "WillowGame.WillowPlayerPawn:TakeDamage",
    "WillowGame.WillowPlayerController:PlayerTick",
    "WillowGame.WillowPlayerController:ServerGrantMissionRewards",
    "WillowGame.WillowPlayerController:ValidateWeaponDefinition",
    "WillowGame.WillowPlayerController:ApplyWeaponSaveGameData",
    "WillowGame.WillowSaveGameManager:SaveGame",
    "WillowGame.ItemCardGFxObject:SetTopStat",
    "WillowGame.ItemCardGFxObject:SetTitle",
    "WillowGame.ItemCardGFxObject:SetItemCard",
    "WillowGame.ItemCardGFxObject:SetItemCardEx",
    "WillowGame.ItemCardGFxObject:SetFunStats",
    "WillowGame.WillowDamageTypeDefinition:DisplayRecentDamageForPlayer",
    "WillowGame.WillowHUDMovieBase:AddTrainingText",
    "WillowGame.Behavior_SpawnLootAroundPoint:PlaceSpawnedItems",
    "WillowGame.WillowVendingMachine:ResetInventory",
]


def _log(message: str) -> None:
    if mods_base is not None:
        logger = getattr(mods_base, "logging", None)
        if logger is not None:
            fn = getattr(logger, "info", None)
            if callable(fn):
                try:
                    fn(f"[{MOD_NAME}] {message}")
                    return
                except Exception:
                    pass
    print(f"[{MOD_NAME}] {message}")


def _warn(message: str) -> None:
    if mods_base is not None:
        logger = getattr(mods_base, "logging", None)
        if logger is not None:
            fn = getattr(logger, "warning", None)
            if callable(fn):
                try:
                    fn(f"[{MOD_NAME}] {message}")
                    return
                except Exception:
                    pass
    print(f"[{MOD_NAME}] {message}", flush=True)


# -- command registry ------------------------------------------------------

COMMANDS: dict[str, Any] = {}


def command(name: str, description: str = ""):
    """Decorator: register a console command handler ``mrp_<name>``."""

    def decorator(fn):
        COMMANDS[name] = fn
        fn._mrp_command_name = name
        fn._mrp_command_description = description
        return fn

    return decorator


def handle_command(line: str, cmd_len: int) -> None:
    """Entry point wired into the mod manager's command system."""
    raw = line[cmd_len:].strip()
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        _warn(f"could not parse command: {exc}")
        return

    if not argv:
        _log_block(HELP)
        return

    sub = argv[0].lower()
    args = argv[1:]
    handler = COMMANDS.get(sub)
    if handler is None:
        _warn(f"unknown command {sub!r}; type 'mrp help'")
        _log_block(HELP)
        return
    try:
        handler(args)
    except Exception as exc:  # pragma: no cover - command must never break game
        import traceback

        _warn(f"mrp {sub} failed: {exc!r}")
        _log_block(traceback.format_exc())


def _log_block(text: str) -> None:
    for line in text.splitlines():
        _log(line)


HELP = f"""\
{MOD_NAME} — drives RE Probe for melee-arsenal analysis

  mrp_start_dump [profile]      Start a dump (core/definitions/full, default full)
  mrp_cancel_dump               Cancel the running dump
  mrp_status                    Print RE Probe dump + trace status
  mrp_trace_arm [pattern ...] [max_hooks]   Arm a trace (default: melee combat set)
  mrp_trace_stop                Disarm hooks + write trace JSON
  mrp_trace_write               Re-write current trace without stopping
  mrp_trace_status              Print trace session state
  mrp_dump_held [depth]         Dump the currently held weapon (depth 0-8)
  mrp_dump_object <path> [depth] Dump any loaded object by path
  mrp_find <substring|glob> [limit]  List matching object paths
  mrp_reproduce [profile]       Start dump + arm melee trace in one command
  mrp_parse <dump_dir>          Parse a dump dir and write a findings report
  mrp_help                      Show this help
""".strip()


# -- get_local_player_controller ------------------------------------------

def _get_pc():
    if mods_base is None:
        return None
    try:
        from mods_base import get_pc  # type: ignore[import-not-found]

        return get_pc()
    except Exception:
        pass
    if unrealsdk is None:
        return None
    try:
        eng = unrealsdk.find_class("Engine").ClassDefaultObject.GetEngine()
        for lp in getattr(eng, "GamePlayers", []) or []:
            actor = getattr(lp, "Actor", None)
            if actor is not None:
                return actor
    except Exception:
        pass
    return None


def _held_weapon():
    pc = _get_pc()
    if pc is None:
        return None
    pawn = getattr(pc, "Pawn", None)
    if pawn is None:
        return None
    return getattr(pawn, "Weapon", None)


# -- commands --------------------------------------------------------------

@command("start_dump", "Start an RE Probe dump")
def cmd_start_dump(args):
    profile = args[0].lower() if args else "full"
    result = driver.start_dump(profile)
    if not result.get("ok"):
        _warn(f"dump not started: {result.get('error')}")
        return
    _log(
        f"dump {result['dump_id']} started ({result['profile']}) "
        f"({result['objects']} objects, {result['classes']} classes, "
        f"{result['functions']} functions) -> {result['output']}"
    )
    _log_block("Phases: " + " -> ".join(result["phases"]))


@command("cancel_dump", "Cancel the running dump")
def cmd_cancel_dump(args):
    result = driver.cancel_dump()
    if result.get("ok"):
        _log(f"dump cancelled; partial output in {result.get('output')}")
    else:
        _warn(f"nothing cancelled: {result.get('error')}")


@command("status", "Print RE Probe dump + trace status")
def cmd_status(args):
    status = driver.status()
    if not status.get("enabled"):
        _log("RE Probe is disabled.")
        return
    job = status.get("job")
    trace = status.get("trace") or {}
    if job is None:
        _log("dump: none started")
    else:
        pc_ = job.get("phase_count") or 0
        pi = job.get("phase_index") or 0
        _log(
            f"dump {job.get('dump_id')} [{job.get('state')}] "
            f"phase {pi}/{pc_} {job.get('phase') or '-'} "
            f"({job.get('phase_items')} items this phase)"
        )
        _log(f"output: {job.get('output')}")
        if job.get("objects_by_kind"):
            kinds = ", ".join(f"{k}={v}" for k, v in sorted(job["objects_by_kind"].items()))
            _log(f"objects by kind: {kinds}")
        if job.get("counts"):
            sections = ", ".join(
                f"{n}={d.get('count', 0)}" for n, d in sorted(job["counts"].items())
            )
            _log(f"sections: {sections}")
        _log(f"errors: {job.get('errors')}  xrefs: {job.get('xrefs')}")
        if job.get("error"):
            _warn(f"last error: {job['error']}")
    _log(
        f"trace: {trace.get('state')} session={trace.get('session_id')} "
        f"hooks={trace.get('armed_hooks')} armed / {trace.get('pending_hooks')} pending, "
        f"calls={trace.get('calls')} edges={trace.get('edges')} errors={trace.get('errors')}"
    )
    if trace.get("patterns"):
        _log(f"trace patterns: {trace['patterns']}")


@command("trace_arm", "Arm a trace session on function patterns")
def cmd_trace_arm(args):
    max_hooks = None
    patterns = list(args)
    if args:
        try:
            max_hooks = max(1, min(4096, int(args[-1])))
            patterns = args[:-1]
        except ValueError:
            patterns = args
    if not patterns:
        patterns = list(MELEE_TRACE_PATTERNS)
    result = driver.trace_arm(patterns, max_hooks=max_hooks)
    if not result.get("ok"):
        _warn(f"trace not armed: {result.get('error')}")
        return
    _log(
        f"trace {result['session_id']} arming {result['matched_functions']} hooks "
        f"(max {result.get('matched_total', len(patterns))}); patterns={result['patterns']}"
    )
    if result.get("truncated"):
        _warn("hook selection hit max_hooks; narrow the pattern or raise the limit")


@command("trace_stop", "Disarm hooks and write the trace")
def cmd_trace_stop(args):
    result = driver.trace_stop()
    if not result.get("ok"):
        _warn(f"trace stop failed: {result.get('error')}")
        return
    _log(
        f"trace {result.get('session_id')} stopped: {result.get('hooks')} hooks, "
        f"{result.get('calls')} calls, {result.get('edges')} edges, "
        f"{result.get('errors')} errors"
    )
    if result.get("path"):
        _log(f"trace written -> {result['path']} ({result.get('bytes', 0)} bytes)")


@command("trace_write", "Re-write current trace without stopping")
def cmd_trace_write(args):
    path = driver.trace_write()
    if path is None:
        _warn("no trace session has been recorded")
    else:
        _log(f"trace written -> {path}")


@command("trace_status", "Print trace session state")
def cmd_trace_status(args):
    trace = driver.trace_status()
    _log(
        f"trace {trace['state']}: session={trace['session_id']} "
        f"armed={trace['armed_hooks']} pending={trace['pending_hooks']} "
        f"calls={trace['calls']} edges={trace['edges']} "
        f"functions={trace['functions']} samples={trace['samples']} "
        f"errors={trace['errors']} drift={trace['stack_drift']}"
    )
    if trace.get("hook_error"):
        _warn(f"trace hook error: {trace['hook_error']}")


@command("dump_held", "Dump the currently held weapon (ad-hoc)")
def cmd_dump_held(args):
    depth = 3
    if args:
        try:
            depth = max(0, min(8, int(args[0])))
        except ValueError:
            _warn(f"invalid depth {args[0]!r}; using 3")
            depth = 3
    weapon = _held_weapon()
    if weapon is None:
        _warn("no held weapon found; load into a character first")
        return
    path = driver.dump_object(_get_path(weapon), depth=depth)
    if path is None:
        _warn("dump_held failed")


@command("dump_object", "Dump any loaded object by path")
def cmd_dump_object(args):
    if not args:
        _warn("usage: mrp_dump_object <path> [depth]")
        return
    depth = 3
    if len(args) > 1:
        try:
            depth = max(0, min(8, int(args[-1])))
            path_arg = " ".join(args[:-1])
        except ValueError:
            path_arg = " ".join(args)
    else:
        path_arg = args[0]
    driver.dump_object(path_arg, depth=depth)


@command("find", "List matching object paths")
def cmd_find(args):
    if not args:
        _warn("usage: mrp_find <substring|glob> [limit]")
        return
    limit = 40
    if len(args) > 1:
        try:
            limit = max(1, min(1000, int(args[-1])))
            pattern = " ".join(args[:-1])
        except ValueError:
            pattern = " ".join(args)
    else:
        pattern = args[0]
    paths = driver.find_objects(pattern, limit=limit)
    if not paths:
        _log(f"no objects matched {pattern!r}")
        return
    _log(f"{len(paths)} match(es) for {pattern!r}:")
    for p in paths:
        _log(f"  {p}")


@command("reproduce", "Start a dump and arm the melee combat trace")
def cmd_reproduce(args):
    profile = args[0].lower() if args else "full"
    result = driver.start_dump(profile)
    if not result.get("ok"):
        _warn(f"dump not started: {result.get('error')}")
        return
    _log(
        f"dump {result['dump_id']} started ({result['profile']}) -> {result['output']}"
    )
    trace_result = driver.trace_arm(MELEE_TRACE_PATTERNS, max_hooks=384)
    if not trace_result.get("ok"):
        _warn(f"trace not armed: {trace_result.get('error')}")
    else:
        _log(
            f"trace {trace_result['session_id']} arming {trace_result['matched_functions']} hooks"
        )


@command("parse", "Parse a dump directory and write a findings report")
def cmd_parse(args):
    if not args:
        _warn("usage: mrp_parse <dump_dir>")
        return
    dump_dir = args[0]
    from .parser import parse_dump
    from .findings import build_findings_report

    try:
        parsed = parse_dump(dump_dir)
    except Exception as exc:
        _warn(f"parse failed: {exc!r}")
        return
    report = build_findings_report(parsed, dump_dir)
    out_dir = Path(dump_dir)
    md_path = out_dir / "MRP_FINDINGS.md"
    json_path = out_dir / "MRP_FINDINGS.json"
    try:
        md_path.write_text(report["markdown"], encoding="utf-8")
        json_path.write_text(json.dumps(report["json"], indent=2, default=str), encoding="utf-8")
        _log(f"findings report written -> {md_path}")
        _log(f"json report written -> {json_path}")
    except Exception as exc:
        _warn(f"could not write findings: {exc!r}")


@command("help", "Show this help")
def cmd_help(args):
    _log_block(HELP)


# -- helpers ---------------------------------------------------------------

def _get_path(obj: Any) -> str:
    if obj is None:
        return ""
    path_fn = getattr(obj, "_path_name", None)
    if callable(path_fn):
        try:
            p = str(path_fn())
            if p:
                return p
        except Exception:
            pass
    return str(getattr(obj, "Name", "") or "")
