"""Process-wide probe state and the game-tick driver."""

from __future__ import annotations

from typing import Any

from . import api
from .hook_registration import install
from .dump import DumpJob, DumpSettings
from .trace import CallTracer, TraceSettings

try:  # pragma: no cover - only outside the game
    from unrealsdk.commands import add_command, remove_command  # type: ignore[import-not-found]
    from unrealsdk.hooks import Type, add_hook, has_hook, remove_hook  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    add_command = None  # type: ignore[assignment]
    remove_command = None  # type: ignore[assignment]
    Type = None  # type: ignore[assignment]
    add_hook = None  # type: ignore[assignment]
    remove_hook = None  # type: ignore[assignment]
    has_hook = None  # type: ignore[assignment]

TICK_HOOKS: tuple[str, ...] = (
    # PlayerTick is the cheapest reliable tick driver in BL2 (the mod guide in
    # the SDK docs measured ~12k calls per session).  The viewport tick is a
    # belt-and-braces fallback for menus/loading screens.
    "WillowGame.WillowPlayerController:PlayerTick",
    "WillowGame.WillowGameViewportClient:Tick",
)

COMMAND_NAME = "reprobe"


class ProbeState:
    def __init__(self) -> None:
        self.settings = DumpSettings()
        self.trace_settings = TraceSettings()
        self.job: DumpJob | None = None
        self.tracer = CallTracer(self.trace_settings)
        self.enabled = False
        self._tick_ids: list[tuple[str, str]] = []
        self._command_registered = False

    # -- lifecycle ----------------------------------------------------------

    def enable(self) -> None:
        if self.enabled:
            return
        self.enabled = True

        if Type is not None and add_hook is not None and has_hook is not None and remove_hook is not None:
            for index, path in enumerate(TICK_HOOKS):
                identifier = f"re_probe.tick.{index}"
                try:
                    if install(add_hook, has_hook, remove_hook, path, Type.POST_UNCONDITIONAL, identifier, self._on_tick):
                        self._tick_ids.append((path, identifier))
                except Exception as exc:
                    api.exception(f"could not install tick hook {path}", exc)

        if add_command is not None and not self._command_registered:
            from .commands import handle_command

            try:
                add_command(COMMAND_NAME, handle_command)
                self._command_registered = True
            except Exception as exc:
                api.exception("could not register the reprobe command", exc)

        api.info("enabled — run 'reprobe help' in the console")

    def disable(self) -> None:
        if not self.enabled:
            return
        self.enabled = False

        if remove_command is not None and self._command_registered:
            try:
                remove_command(COMMAND_NAME)
            except Exception:
                pass
            self._command_registered = False

        if Type is not None and remove_hook is not None:
            for path, identifier in self._tick_ids:
                try:
                    remove_hook(path, Type.POST_UNCONDITIONAL, identifier)
                except Exception:
                    pass
        self._tick_ids = []

        if self.job is not None and self.job.state in {"running", "collecting"}:
            try:
                self.job.cancel()
            except Exception:
                pass

        try:
            self.tracer.disarm(remove_hooks=True)
        except Exception:
            pass
        api.info("disabled")

    # -- tick ---------------------------------------------------------------

    def _on_tick(self, obj: Any, args: Any, ret: Any, func: Any) -> None:
        self.tick()
        return None

    def tick(self) -> None:
        if not self.enabled:
            return
        if self.job is not None and self.job.state == "running":
            try:
                self.job.tick()
            except Exception as exc:  # pragma: no cover - keep the game alive
                api.exception("dump tick failed", exc)
        if self.tracer.state == "arming":
            try:
                self.tracer.tick()
            except Exception as exc:  # pragma: no cover
                api.exception("trace hook installation failed", exc)

    # -- operations ---------------------------------------------------------

    def start_dump(self, profile: str | None = None) -> dict[str, Any]:
        if self.job is not None and self.job.state == "running":
            return {"ok": False, "error": "a dump is already running"}
        if profile:
            self.settings.profile = profile
        self.job = DumpJob(self.settings)
        return self.job.start(self.settings.profile)

    def cancel_dump(self) -> dict[str, Any]:
        if self.job is None:
            return {"ok": False, "error": "no dump has been started"}
        return self.job.cancel()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "command_registered": self._command_registered,
            "tick_hooks": [path for path, _ in self._tick_ids],
            "job": self.job.status() if self.job is not None else None,
            "trace": self.tracer.status(),
        }

    def all_objects(self) -> list[Any]:
        # Always re-fetch.  Cached wrappers go stale as the game creates and
        # destroys objects, and dereferencing one crashes the process.
        return api.find_all("Object")


PROBE = ProbeState()
