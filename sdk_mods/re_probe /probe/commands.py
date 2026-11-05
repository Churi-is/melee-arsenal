"""Console command surface.

Registered as one ``reprobe`` console command with subcommands, because UE3
console commands cannot contain spaces.  Everything is intentionally usable
without leaving the game.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import api, reflect
from .dump import PROFILES
from .state import PROBE
from .values import ValueSerializer

UTC = timezone.utc

DUMPS_ROOT = Path(__file__).resolve().parent.parent / "dumps"

HELP = """
reprobe — Borderlands 2 reflection dump probe

  reprobe start [full|definitions|core]
      Start an incremental dump. 'full' includes CDO values, live instances,
      globals and cross references; 'definitions' is reflection only;
      'core' stops after class/function/struct/enum/const definitions.

  reprobe status
      Current dump phase, counts, trace state and output directory.

  reprobe cancel
      Stop the running dump and finalize the shards written so far.

  reprobe dump <object path> [depth]
      Write one object (definition + live values) to dumps/adhoc/.

  reprobe find <substring|glob> [limit]
      List matching object paths (default limit 40).

  reprobe trace arm [pattern ...] [max_hooks]
      Hook a set of UFunctions and record caller -> callee edges.
      Patterns match the full path, 'Class:Function', or a bare function name.
      Examples:
        reprobe trace arm WillowGame.WillowWeapon:*
        reprobe trace arm *:ConsumeAmmo 256
        reprobe trace arm WillowGame.WillowPlayerController:PlayerTick

  reprobe trace status | stop | write
      Inspect, finish, or re-write the current trace.

  reprobe help
""".strip()


def handle_command(line: str, cmd_len: int) -> None:
    raw = line[cmd_len:].strip()
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        api.warning(f"could not parse command: {exc}")
        return

    if not argv:
        _log_block(HELP)
        return

    sub = argv[0].lower()
    args = argv[1:]
    try:
        if sub == "start":
            _cmd_start(args)
        elif sub == "status":
            _cmd_status()
        elif sub == "cancel":
            _cmd_cancel()
        elif sub == "dump":
            _cmd_dump(args)
        elif sub == "find":
            _cmd_find(args)
        elif sub == "trace":
            _cmd_trace(args)
        elif sub in {"help", "?"}:
            _log_block(HELP)
        else:
            api.warning(f"unknown subcommand {sub!r}")
            _log_block(HELP)
    except Exception as exc:  # pragma: no cover - command must never break the game
        api.exception(f"reprobe {sub} failed", exc)


# ---------------------------------------------------------------------------
# Dump commands
# ---------------------------------------------------------------------------


def _cmd_start(args: list[str]) -> None:
    profile = args[0].lower() if args else PROBE.settings.profile
    if profile not in PROFILES:
        api.warning(f"unknown profile {profile!r}; use one of {sorted(PROFILES)}")
        return
    result = PROBE.start_dump(profile)
    if not result.get("ok"):
        api.warning(f"dump not started: {result.get('error')}")
        return
    api.info(
        f"dump {result['dump_id']} started with profile {result['profile']} "
        f"({result['objects']} objects, {result['classes']} classes, "
        f"{result['functions']} functions) -> {result['output']}"
    )
    _log_block("Phases: " + " -> ".join(result["phases"]))


def _cmd_cancel() -> None:
    result = PROBE.cancel_dump()
    if result.get("ok"):
        api.info(f"dump {result.get('dump_id')} cancelled; partial output in {result.get('output')}")
    else:
        api.warning(f"nothing cancelled: {result.get('error')}")


def _cmd_status() -> None:
    status = PROBE.status()
    job = status.get("job")
    trace = status.get("trace") or {}

    if not status.get("enabled"):
        api.info("RE Probe is disabled.")
    if job is None:
        api.info("dump: none started")
    else:
        phase_count = job.get("phase_count") or 0
        phase_index = job.get("phase_index") or 0
        api.info(
            f"dump {job.get('dump_id')} [{job.get('state')}] "
            f"phase {phase_index}/{phase_count} {job.get('phase') or '-'} "
            f"({job.get('phase_items')} items this phase)"
        )
        api.info(f"output: {job.get('output')}")
        if job.get("objects_by_kind"):
            kinds = ", ".join(f"{k}={v}" for k, v in sorted(job["objects_by_kind"].items()))
            api.info(f"objects by kind: {kinds}")
        if job.get("counts"):
            sections = ", ".join(
                f"{name}={data.get('count', 0)}" for name, data in sorted(job["counts"].items())
            )
            api.info(f"sections: {sections}")
        api.info(f"errors: {job.get('errors')}  xrefs: {job.get('xrefs')}")
        if job.get("error"):
            api.warning(f"last error: {job['error']}")

    armed = trace.get("armed_hooks", 0)
    pending = trace.get("pending_hooks", 0)
    api.info(
        f"trace: {trace.get('state')} session={trace.get('session_id')} "
        f"hooks={armed} armed / {pending} pending, "
        f"calls={trace.get('calls')} edges={trace.get('edges')} errors={trace.get('errors')}"
    )
    if trace.get("patterns"):
        api.info(f"trace patterns: {trace['patterns']}")
    if trace.get("hook_error"):
        api.warning(f"trace hook error: {trace['hook_error']}")


def _cmd_dump(args: list[str]) -> None:
    if not args:
        api.warning("usage: reprobe dump <object path> [depth]")
        return

    depth = PROBE.settings.value_depth
    if len(args) > 1:
        try:
            depth = max(0, min(8, int(args[-1])))
            path_arg = " ".join(args[:-1])
        except ValueError:
            path_arg = " ".join(args)
    else:
        path_arg = args[0]

    obj = _find_one(path_arg)
    if obj is None:
        api.warning(f"object not found: {path_arg}")
        return

    obj_path = api.path_of(obj)
    cls = api.class_of(obj)
    serializer = ValueSerializer(
        depth=depth,
        array_limit=PROBE.settings.array_limit,
        string_limit=max(64, PROBE.settings.string_limit),
        max_props=PROBE.settings.max_props_per_object,
    )
    values, errors = serializer.dump_object_values(
        obj,
        api.properties_of(cls),
        from_path=obj_path,
    )

    document = {
        "schema": "re_probe.v1.object",
        "dumped_at": api.utc_now(),
        "depth": depth,
        "object": reflect.describe_object(obj, package_path=_package_of(obj)),
        "values": values,
    }
    if errors:
        document["errors"] = errors

    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", obj_path or "object")[:120]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = DUMPS_ROOT / "adhoc" / f"{stamp}-{safe_name}.json"
    api.write_json_atomic(out, document, indent=1)
    api.info(f"wrote {obj_path} (depth {depth}) -> {out}")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _cmd_find(args: list[str]) -> None:
    if not args:
        api.warning("usage: reprobe find <substring|glob> [limit]")
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

    matches = _find_many(pattern, limit)
    if not matches:
        api.info(f"no objects matched {pattern!r}")
        return

    api.info(f"{len(matches)} match(es) for {pattern!r}:")
    for obj in matches:
        api.info(f"  {api.path_of(obj)}  [{api.class_name_of(obj)}]")


def _find_one(path: str) -> Any:
    objects = PROBE.all_objects()
    for obj in objects:
        if api.path_of(obj) == path:
            return obj
    # Be forgiving about a missing package prefix.
    for obj in objects:
        obj_path = api.path_of(obj) or ""
        if obj_path.endswith(("." + path, ":" + path)):
            return obj
    return None


def _find_many(pattern: str, limit: int) -> list[Any]:
    glob = any(char in pattern for char in "*?[")
    needle = pattern.lower()
    results: list[Any] = []
    for obj in PROBE.all_objects():
        path = api.path_of(obj) or ""
        matched = (
            fnmatch.fnmatchcase(path.lower(), needle)
            if glob
            else needle in path.lower()
        )
        if matched:
            results.append(obj)
            if len(results) >= limit:
                break
    return results


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


def _cmd_trace(args: list[str]) -> None:
    if not args:
        _cmd_trace_status()
        return

    action = args[0].lower()
    rest = args[1:]
    if action == "arm":
        _cmd_trace_arm(rest)
    elif action == "stop":
        _cmd_trace_stop()
    elif action == "write":
        _cmd_trace_write()
    elif action == "status":
        _cmd_trace_status()
    else:
        api.warning(f"unknown trace action {action!r}; use arm, stop, write or status")


def _cmd_trace_arm(args: list[str]) -> None:
    max_hooks = PROBE.trace_settings.max_hooks
    if args:
        try:
            max_hooks = max(1, min(4096, int(args[-1])))
            patterns = args[:-1]
        except ValueError:
            patterns = args
    else:
        patterns = []

    result = PROBE.tracer.arm(patterns, max_hooks=max_hooks)
    if not result.get("ok"):
        api.warning(f"trace not armed: {result.get('error')}")
        return
    api.info(
        f"trace {result['session_id']} arming {result['matched_functions']} hooks "
        f"(max {max_hooks}); patterns={result['patterns']}"
    )
    if result.get("truncated"):
        api.warning("hook selection hit max_hooks; narrow the pattern or raise the limit")


def _cmd_trace_stop() -> None:
    result = PROBE.tracer.stop(directory=DUMPS_ROOT / "traces")
    if not result.get("ok"):
        api.warning(f"trace stop failed: {result.get('error')}")
        return
    api.info(
        f"trace {result.get('session_id')} stopped: {result.get('hooks')} hooks, "
        f"{result.get('calls')} calls, {result.get('edges')} edges, "
        f"{result.get('errors')} errors"
    )
    if result.get("path"):
        api.info(f"trace written -> {result['path']} ({result.get('bytes', 0)} bytes)")


def _cmd_trace_write() -> None:
    if PROBE.tracer.session_id is None:
        api.warning("no trace session has been recorded")
        return
    try:
        path = PROBE.tracer.write(DUMPS_ROOT / "traces")
    except Exception as exc:
        api.exception("could not write trace", exc)
        return
    api.info(f"trace written -> {path}")


def _cmd_trace_status() -> None:
    status = PROBE.tracer.status()
    api.info(
        f"trace {status['state']}: session={status['session_id']} "
        f"armed={status['armed_hooks']} pending={status['pending_hooks']} "
        f"calls={status['calls']} edges={status['edges']} "
        f"functions={status['functions']} samples={status['samples']} "
        f"errors={status['errors']} drift={status['stack_drift']}"
    )
    if status.get("patterns"):
        api.info(f"patterns: {status['patterns']}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _package_of(obj: Any) -> str | None:
    current = obj
    seen: set[int] = set()
    for _ in range(32):
        if current is None:
            break
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        if api.class_name_of(current) == "Package":
            return api.path_of(current)
        current = api.safe_getattr(current, "Outer", None)
    return None


def _log_block(text: str) -> None:
    for line in text.splitlines():
        api.info(line)
