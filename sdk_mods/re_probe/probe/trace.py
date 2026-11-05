"""Runtime call tracing.

BL2 ships without UnrealScript bytecode (the UStruct script array is stripped),
so a static caller/callee graph cannot be recovered from reflection.  The only
honest way to answer "who calls this / what does it call" is to observe calls
while the game runs.

This tracer installs pyunrealsdk PRE + POST_UNCONDITIONAL hooks on a selected
set of UFunctions.  A thread-local stack turns the hook pairs into edges:

    caller  ->  callee  (count, object, optional sampled args)

Limitations are documented in docs/JSON_FORMAT.md; most importantly, hooks see
calls that go through UE3's ProcessEvent/CallFunction paths, not calls made
entirely inside native C++ code.
"""

from __future__ import annotations

import fnmatch
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import api
from .hook_registration import install

try:  # pragma: no cover - only outside the game
    from unrealsdk.hooks import Type, add_hook, has_hook, remove_hook  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    Type = None  # type: ignore[assignment]
    add_hook = None  # type: ignore[assignment]
    remove_hook = None  # type: ignore[assignment]
    has_hook = None  # type: ignore[assignment]

from .values import ValueSerializer


@dataclass
class TraceSettings:
    max_hooks: int = 512
    capture_args: bool = True
    max_samples_per_edge: int = 2
    max_edges: int = 100_000
    max_objects_per_function: int = 32
    measure_time: bool = False
    max_depth: int = 64
    install_batch: int = 32


@dataclass
class _Edge:
    caller: str | None
    callee: str
    count: int = 0
    max_depth: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)
    total_seconds: float = 0.0


@dataclass
class _Function:
    path: str
    calls: int = 0
    callers: dict[str | None, int] = field(default_factory=dict)
    objects: dict[str, int] = field(default_factory=dict)
    objects_truncated: bool = False


class CallTracer:
    def __init__(self, settings: TraceSettings | None = None) -> None:
        self.settings = settings or TraceSettings()
        self.state = "idle"  # idle | arming | armed | stopped
        self.session_id: str | None = None
        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.patterns: list[str] = []
        self.calls = 0
        self.errors = 0
        self.samples = 0
        self.dropped_edges = 0
        self.stack_drift = 0
        self._pending: list[str] = []
        self._installed: list[tuple[str, str, str]] = []
        self._failed_hooks: list[dict[str, str]] = []
        self._armed_history = 0
        self._hook_seq = 0
        self._hook_token = uuid.uuid4().hex
        self._edges: dict[tuple[str | None, str], _Edge] = {}
        self._functions: dict[str, _Function] = {}
        self._local = threading.local()
        self._lock = threading.RLock()
        self._path_cache: dict[int, str] = {}
        self._args_serializer = ValueSerializer(
            depth=1,
            array_limit=8,
            string_limit=192,
            max_props=32,
        )
        self._hook_error: str | None = None

    # -- public API ---------------------------------------------------------

    def arm(self, patterns: list[str], *, max_hooks: int | None = None) -> dict[str, Any]:
        if Type is None or add_hook is None or has_hook is None or remove_hook is None:
            return {"ok": False, "error": "pyunrealsdk hooks are not available"}
        if self.state in {"arming", "armed"}:
            return {"ok": False, "error": "a trace session is already active; stop it first"}

        self.disarm(remove_hooks=True)
        self.settings.max_hooks = max(1, min(4096, int(max_hooks or self.settings.max_hooks)))
        self.patterns = [pattern for pattern in patterns if pattern] or ["WillowGame.WillowPlayerController:*"]
        self.session_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        self.started_at = api.utc_now()
        self.stopped_at = None
        self.calls = 0
        self.errors = 0
        self.samples = 0
        self.dropped_edges = 0
        self.stack_drift = 0
        self._local = threading.local()
        self._hook_token = uuid.uuid4().hex
        self._pending = []
        self._installed = []
        self._failed_hooks = []
        self._armed_history = 0
        self._edges = {}
        self._functions = {}
        self._path_cache = {}
        self._hook_seq = 0
        self._hook_error = None
        self.state = "arming"

        candidates: list[str] = []
        for function in api.find_all("Function"):
            path = api.path_of(function)
            if not path:
                continue
            if any(_matches(path, pattern) for pattern in self.patterns):
                candidates.append(path)

        # Deterministic selection: GObjects order is not stable across builds,
        # so sorting keeps a capped trace reproducible.
        candidates = sorted(set(candidates))
        self._pending = candidates[: self.settings.max_hooks]

        if not self._pending:
            self.state = "idle"
            return {"ok": False, "error": f"no functions matched {self.patterns!r}"}

        api.info(
            f"trace session {self.session_id}: will install {len(self._pending)} of "
            f"{len(candidates)} matching hooks over the next few frames "
            f"(patterns={self.patterns})"
        )
        return {
            "ok": True,
            "session_id": self.session_id,
            "matched_functions": len(self._pending),
            "matched_total": len(candidates),
            "truncated": len(candidates) > len(self._pending),
            "patterns": list(self.patterns),
        }

    def tick(self) -> None:
        """Installs pending hooks in small batches, from the game thread."""
        if self.state != "arming":
            return
        batch = self._pending[: self.settings.install_batch]
        if not batch:
            self.state = "armed"
            api.info(f"trace session {self.session_id}: all {len(self._installed)} hooks armed")
            return

        del self._pending[: len(batch)]
        for path in batch:
            pre_installed = False
            try:
                pre_id = f"re_probe.trace.{self._hook_token}.{self._hook_seq}.pre"
                post_id = f"re_probe.trace.{self._hook_token}.{self._hook_seq}.post"
                self._hook_seq += 1
                if not install(add_hook, has_hook, remove_hook, path, Type.PRE, pre_id, self._pre):
                    self.errors += 1
                    self._failed_hooks.append({"path": path, "stage": "pre", "error": "hook not installed"})
                    continue
                pre_installed = True
                if not install(add_hook, has_hook, remove_hook, path, Type.POST_UNCONDITIONAL, post_id, self._post):
                    remove_hook(path, Type.PRE, pre_id)
                    self.errors += 1
                    self._failed_hooks.append({"path": path, "stage": "post", "error": "hook not installed"})
                    continue
                self._installed.append((path, pre_id, post_id))
            except Exception as exc:  # pragma: no cover - defensive
                # The pre hook may already have been installed when post failed.
                try:
                    if pre_installed:
                        remove_hook(path, Type.PRE, pre_id)
                except Exception:
                    pass
                self._hook_error = repr(exc)
                self._failed_hooks.append({"path": path, "stage": "post" if pre_installed else "pre", "error": repr(exc)})
                self.errors += 1

        if not self._pending:
            self.state = "armed"
            self._armed_history = len(self._installed)
            api.info(f"trace session {self.session_id}: {len(self._installed)} hooks armed")

    def stop(self, *, write: bool = True, directory: Path | None = None) -> dict[str, Any]:
        if self.session_id is None:
            return {"ok": False, "error": "no trace session has been recorded"}
        armed_hooks = len(self._installed)
        self.disarm(remove_hooks=True)
        self.stopped_at = api.utc_now()
        self.state = "stopped"
        result: dict[str, Any] = {
            "ok": True,
            "session_id": self.session_id,
            "hooks": armed_hooks,
            "calls": self.calls,
            "edges": len(self._edges),
            "errors": self.errors,
        }
        if write and self.session_id is not None:
            try:
                path = self.write(directory)
                result["path"] = str(path)
                result["bytes"] = path.stat().st_size
            except Exception as exc:
                result["ok"] = False
                result["error"] = repr(exc)
        return result

    def disarm(self, *, remove_hooks: bool = False) -> None:
        self._armed_history = max(self._armed_history, len(self._installed))
        if remove_hooks and Type is not None and remove_hook is not None:
            for path, pre_id, post_id in self._installed:
                try:
                    remove_hook(path, Type.PRE, pre_id)
                    remove_hook(path, Type.POST_UNCONDITIONAL, post_id)
                except Exception:
                    self.errors += 1
        self._installed = []
        self._pending = []
        if self.state in {"arming", "armed"}:
            self.state = "idle"

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "patterns": list(self.patterns),
            "armed_hooks": len(self._installed),
            "pending_hooks": len(self._pending),
            "installed_functions": [path for path, _, _ in self._installed],
            "failed_hooks": list(self._failed_hooks),
            "calls": self.calls,
            "edges": len(self._edges),
            "functions": len(self._functions),
            "samples": self.samples,
            "errors": self.errors,
            "dropped_edges": self.dropped_edges,
            "stack_drift": self.stack_drift,
            "hook_error": self._hook_error,
        }

    def write(self, directory: Path | None = None) -> Path:
        """Writes the aggregated session as one strict JSON document."""
        if directory is None:
            directory = Path(__file__).resolve().parent.parent / "dumps" / "traces"
        directory.mkdir(parents=True, exist_ok=True)
        session = self.session_id or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        path = directory / f"trace-{session}.json"

        edges = sorted(self._edges.values(), key=lambda edge: (-edge.count, edge.callee))
        functions = sorted(self._functions.values(), key=lambda fn: (-fn.calls, fn.path))

        document = {
            "schema": "re_probe.v1.trace",
            "session": {
                "session_id": session,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at or api.utc_now(),
                "patterns": list(self.patterns),
                "settings": {
                    "max_hooks": self.settings.max_hooks,
                    "capture_args": self.settings.capture_args,
                    "max_samples_per_edge": self.settings.max_samples_per_edge,
                    "max_edges": self.settings.max_edges,
                    "measure_time": self.settings.measure_time,
                },
                "hooks": self._armed_history,
                "failed_hooks": list(self._failed_hooks),
                "calls": self.calls,
                "edges": len(edges),
                "functions": len(functions),
                "samples": self.samples,
                "errors": self.errors,
                "dropped_edges": self.dropped_edges,
                "stack_drift": self.stack_drift,
            },
            "functions": [
                {
                    "path": fn.path,
                    "calls": fn.calls,
                    "callers": [
                        {"path": caller, "count": count}
                        for caller, count in sorted(
                            fn.callers.items(), key=lambda item: (-item[1], item[0] or "")
                        )
                    ],
                    "objects": [
                        {"path": obj, "count": count}
                        for obj, count in sorted(
                            fn.objects.items(), key=lambda item: (-item[1], item[0])
                        )
                    ],
                    "objects_truncated": fn.objects_truncated,
                }
                for fn in functions
            ],
            "edges": [
                {
                    "caller": edge.caller,
                    "callee": edge.callee,
                    "count": edge.count,
                    "max_depth": edge.max_depth,
                    "total_seconds": round(edge.total_seconds, 6) if self.settings.measure_time else None,
                    "samples": edge.samples[: self.settings.max_samples_per_edge],
                }
                for edge in edges
            ],
            "notes": [
                "Samples are capped snapshots of the callee's arguments, not a full log of every call.",
                "caller=null means the call entered from outside the hooked function set.",
                "Only calls routed through ProcessEvent/CallFunction are observable by pyunrealsdk hooks.",
            ],
        }
        api.write_json_atomic(path, document, indent=None)
        return path

    # -- hook callbacks -----------------------------------------------------

    def _pre(self, obj: Any, args: Any, ret: Any, func: Any) -> None:
        try:
            function = api.safe_getattr(func, "func", None)
            callee = self._function_path(function)
            obj_path = api.path_of(obj) or ""

            stack = self._stack()
            depth = len(stack)
            caller = stack[-1]["callee"] if stack else None

            sample = None
            if self.settings.capture_args and depth < self.settings.max_depth:
                edge = self._edges.get((caller, callee))
                if edge is None or len(edge.samples) < self.settings.max_samples_per_edge:
                    try:
                        sample = self._args_serializer.dump_struct(
                            args, via="args", from_path=obj_path, depth=1
                        )
                    except Exception:
                        sample = {"$error": "could not serialize args"}

            stack.append(
                {
                    "callee": callee,
                    "object": obj_path,
                    "caller": caller,
                    "start": time.perf_counter() if self.settings.measure_time else 0.0,
                    "sample": sample,
                    "depth": depth,
                }
            )

            with self._lock:
                self.calls += 1
                self._function_stats(callee).calls += 1
        except Exception:
            self.errors += 1
        return None

    def _post(self, obj: Any, args: Any, ret: Any, func: Any) -> None:
        try:
            function = api.safe_getattr(func, "func", None)
            callee = self._function_path(function)
            stack = self._stack()

            frame = None
            # Normally the top frame is ours.  If another mod blocked a call or
            # an exception skipped a pair, search a little before giving up.
            for index in range(len(stack) - 1, max(-1, len(stack) - 9), -1):
                if stack[index].get("callee") == callee:
                    frame = stack[index]
                    del stack[index:]
                    break

            if frame is None:
                with self._lock:
                    self.stack_drift += 1
                return None

            caller = frame.get("caller")
            depth = frame.get("depth", 0)
            elapsed = 0.0
            if self.settings.measure_time:
                elapsed = max(0.0, time.perf_counter() - float(frame.get("start", 0.0)))

            with self._lock:
                key = (caller, callee)
                edge = self._edges.get(key)
                if edge is None:
                    if len(self._edges) >= self.settings.max_edges:
                        self.dropped_edges += 1
                        return None
                    edge = _Edge(caller=caller, callee=callee)
                    self._edges[key] = edge

                edge.count += 1
                if depth > edge.max_depth:
                    edge.max_depth = depth
                if elapsed:
                    edge.total_seconds += elapsed

                sample = frame.get("sample")
                if sample is not None and len(edge.samples) < self.settings.max_samples_per_edge:
                    edge.samples.append(
                        {
                            "object": frame.get("object"),
                            "args": sample,
                        }
                    )
                    self.samples += 1

                stats = self._function_stats(callee)
                stats.callers[caller] = stats.callers.get(caller, 0) + 1
                obj_path = frame.get("object") or ""
                if obj_path:
                    if len(stats.objects) < self.settings.max_objects_per_function:
                        stats.objects[obj_path] = stats.objects.get(obj_path, 0) + 1
                    else:
                        stats.objects_truncated = True
        except Exception:
            self.errors += 1
        return None

    # -- internals ----------------------------------------------------------

    def _stack(self) -> list[dict[str, Any]]:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def _function_path(self, function: Any) -> str:
        if function is None:
            return "<unknown>"
        address = None
        getter = api.safe_getattr(function, "_get_address", None)
        if callable(getter):
            try:
                address = int(getter())
            except Exception:
                address = None
        if address is not None:
            cached = self._path_cache.get(address)
            if cached is not None:
                return cached
        path = api.path_of(function) or api.name_of(function) or "<unknown>"
        if address is not None:
            if len(self._path_cache) > 100_000:
                self._path_cache.clear()
            self._path_cache[address] = path
        return path

    def _function_stats(self, path: str) -> _Function:
        stats = self._functions.get(path)
        if stats is None:
            stats = _Function(path=path)
            self._functions[path] = stats
        return stats


def _matches(path: str, pattern: str) -> bool:
    """Matches a full function path, ``Class:Function``, or bare function name."""
    pattern = pattern.strip()
    if not pattern:
        return False
    candidates = [path]
    if ":" in path:
        class_and_func = path.rsplit(".", 1)[-1]
        candidates.append(class_and_func)
        candidates.append(path.rsplit(":", 1)[-1])
    return any(fnmatch.fnmatchcase(candidate.lower(), pattern.lower()) for candidate in candidates)
