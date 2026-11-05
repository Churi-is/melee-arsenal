"""Programmatic driver for RE Probe.

This module turns RE Probe from a console-command-only mod into a
library/SDK tool that other mods can drive directly:

    from re_probe.probe.driver import ProbeDriver

    driver = ProbeDriver()
    driver.start_dump("full")
    driver.trace_arm(["WillowGame.WillowWeapon:*"], max_hooks=384)
    # ... play the game ...
    driver.trace_stop()
    driver.cancel_dump()

Nothing here installs hooks or registers console commands on import — that
is the mod wrapper's job (``re_probe/__init__.py``).  The driver only talks
to the :class:`~re_probe.probe.state.ProbeState` singleton (``PROBE``), so a
runner mod can coordinate dumps and traces without ever touching the console.

All methods are safe to call from the game thread (console commands, hooks,
mod menu callbacks).  They degrade gracefully when RE Probe is not enabled
or ``unrealsdk`` is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import api
from .dump import DumpJob, DumpSettings, PROFILES
from .state import PROBE


class ProbeDriver:
    """Drive RE Probe programmatically.

    Parameters
    ----------
    auto_enable:
        If True (default), calling a driver method while RE Probe is disabled
        will enable it first.  Set False to require the user to enable RE
        Probe through the mod manager.
    """

    def __init__(self, auto_enable: bool = True) -> None:
        self.auto_enable = auto_enable

    # -- lifecycle ---------------------------------------------------------

    def _ensure_enabled(self) -> bool:
        if PROBE.enabled:
            return True
        if not self.auto_enable:
            return False
        try:
            PROBE.enable()
        except Exception:
            api.exception("could not enable RE Probe")
            return False
        return PROBE.enabled

    def is_enabled(self) -> bool:
        return bool(PROBE.enabled)

    def status(self) -> dict[str, Any]:
        """Full probe + dump + trace status."""
        if not self._ensure_enabled():
            return {"enabled": False, "error": "RE Probe is disabled"}
        return PROBE.status()

    # -- dumps -------------------------------------------------------------

    def start_dump(
        self,
        profile: str = "full",
        *,
        output_root: str | Path | None = None,
    ) -> dict[str, Any]:
        """Start an incremental dump (``core`` / ``definitions`` / ``full``).

        Returns ``{"ok": True, "dump_id": ..., "output": ..., ...}`` on success
        or ``{"ok": False, "error": ...}`` on failure.
        """
        if not self._ensure_enabled():
            return {"ok": False, "error": "RE Probe is disabled"}

        profile_name = (profile or "full").lower()
        if profile_name not in PROFILES:
            return {
                "ok": False,
                "error": f"unknown profile {profile_name!r}; use {sorted(PROFILES)}",
            }

        settings = DumpSettings(profile=profile_name)
        job = DumpJob(settings, output_root=Path(output_root) if output_root else None)
        PROBE.job = job
        return job.start(profile_name)

    def cancel_dump(self) -> dict[str, Any]:
        """Cancel the running dump, keeping shards written so far."""
        if not self._ensure_enabled():
            return {"ok": False, "error": "RE Probe is disabled"}
        if PROBE.job is None:
            return {"ok": False, "error": "no dump has been started"}
        return PROBE.job.cancel()

    # -- traces ------------------------------------------------------------

    def trace_arm(
        self,
        patterns: list[str],
        *,
        max_hooks: int | None = None,
    ) -> dict[str, Any]:
        """Arm a trace session on the given function patterns.

        Hooks are installed gradually over the next few frames.  Use
        :meth:`trace_status` to watch progress, then :meth:`trace_stop`
        when the behaviour you want to observe has been reproduced.
        """
        if not self._ensure_enabled():
            return {"ok": False, "error": "RE Probe is disabled"}
        return PROBE.tracer.arm(patterns, max_hooks=max_hooks)

    def trace_stop(
        self,
        *,
        directory: str | Path | None = None,
        write: bool = True,
    ) -> dict[str, Any]:
        """Disarm hooks and (optionally) write the trace aggregation to JSON."""
        if PROBE.tracer.session_id is None:
            return {"ok": False, "error": "no trace session has been recorded"}
        return PROBE.tracer.stop(
            write=write,
            directory=Path(directory) if directory else None,
        )

    def trace_write(self, directory: str | Path | None = None) -> str | None:
        """Re-write the current trace aggregation without stopping the session."""
        if PROBE.tracer.session_id is None:
            return None
        try:
            path = PROBE.tracer.write(Path(directory) if directory else None)
            return str(path)
        except Exception:
            api.exception("could not write trace")
            return None

    def trace_status(self) -> dict[str, Any]:
        """Current trace session state (arming / armed / stopped / idle)."""
        return PROBE.tracer.status()

    # -- ad-hoc object inspection ------------------------------------------

    def dump_object(self, path: str, depth: int = 3) -> str | None:
        """Dump one object (definition + live values) to ``dumps/adhoc/``.

        Returns the output file path, or None if the object was not found.
        """
        if not self._ensure_enabled():
            return None
        from .commands import _find_one, _package_of
        from .reflect import describe_object
        from .values import ValueSerializer

        obj = _find_one(path)
        if obj is None:
            api.warning(f"object not found: {path}")
            return None

        obj_path = api.path_of(obj)
        cls = api.class_of(obj)
        serializer = ValueSerializer(
            depth=max(0, min(8, int(depth))),
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
            "object": describe_object(obj, package_path=_package_of(obj)),
            "values": values,
        }
        if errors:
            document["errors"] = errors

        import re as _re
        from datetime import datetime, timezone

        safe_name = _re.sub(r"[^A-Za-z0-9_.-]+", "_", obj_path or "object")[:120]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = Path(__file__).resolve().parent.parent / "dumps" / "adhoc" / f"{stamp}-{safe_name}.json"
        api.write_json_atomic(out, document, indent=1)
        api.info(f"wrote {obj_path} (depth {depth}) -> {out}")
        return str(out)

    def find_objects(self, pattern: str, limit: int = 40) -> list[str]:
        """List object paths matching a substring or glob pattern."""
        if not self._ensure_enabled():
            return []
        from .commands import _find_many

        matches = _find_many(pattern, max(1, min(1000, int(limit))))
        paths = []
        for obj in matches:
            p = api.path_of(obj)
            if p:
                paths.append(p)
        return paths


# Singleton driver that mods can import and use directly.
driver = ProbeDriver()

__all__ = ["ProbeDriver", "driver"]
