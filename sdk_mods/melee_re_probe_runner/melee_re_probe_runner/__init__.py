"""Melee RE Probe Runner — drives RE Probe for melee-arsenal analysis.

This mod is the bridge between melee-arsenal and RE Probe:

* It imports RE Probe as a library (``from re_probe import PROBE, driver``)
  and exposes RE Probe's dump/trace operations as ``mrp_*`` console commands.
* It provides a standard melee profiling session (``mrp_reproduce``) that
  starts a full dump and arms the melee combat trace in one command.
* It can parse a completed RE Probe dump and write a CONFIRMED/PARTIAL/
  REFUTED findings report (``mrp_parse <dump_dir>``).

Install all three mods: melee-arsenal, re_probe, melee_re_probe_runner.
Enable RE Probe first, then this runner.  Run ``mrp help`` for the command list.

Three-mod layout in the game's ``sdk_mods/``::

    Borderlands 2/
    └── sdk_mods/
        ├── melee_arsenal/          # the mod under analysis
        ├── re_probe/               # reflection dumper (library-driveable)
        └── melee_re_probe_runner/  # this mod: tells RE Probe what to do
"""

from __future__ import annotations

from typing import Any

try:
    import mods_base  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - offline inspection / tooling
    mods_base = None  # type: ignore[assignment]
    mod = None  # type: ignore[assignment]
else:
    from . import commands

    __version__ = "0.1.0"
    __author__ = "MeleeArsenal"
    mod = None  # set below after build_mod

    # -- options -----------------------------------------------------------

    def _on_enabled_changed(option, value):
        pass  # runner is enabled via the mod manager toggle itself

    opt_enable_runner = mods_base.BoolOption(
        "Enable Runner",
        True,
        display_name="Enable Melee RE Probe Runner",
        description="When on, mrp_* console commands are active. Off = commands are no-ops.",
    )

    opt_auto_trace = mods_base.BoolOption(
        "Auto-arm melee trace",
        False,
        display_name="Auto-arm melee combat trace on enable",
        description="Arms the standard melee trace set (InstantFire, ConsumeAmmo, TakeDamage, ...)"
        " as soon as this runner is enabled.",
    )

    opt_auto_dump = mods_base.BoolOption(
        "Auto-full-dump on enable",
        False,
        display_name="Auto-start full dump on enable",
        description="Starts a RE Probe full dump as soon as this runner is enabled.",
    )

    opt_trace_hooks = mods_base.SliderOption(
        "Trace hook limit",
        384,
        16,
        4096,
        16,
        True,
        display_name="Trace hook limit",
        description="Max functions hooked by one melee trace session (default 384 covers the melee set).",
        on_change_anytime=lambda o, v: None,
    )

    options = [
        opt_enable_runner,
        opt_auto_trace,
        opt_auto_dump,
        opt_trace_hooks,
    ]

    # -- lifecycle ---------------------------------------------------------

    def _on_enable() -> None:
        if not opt_enable_runner.value:
            return
        # Confirm RE Probe is present and enabled.
        try:
            from re_probe import PROBE as _RPROBE, driver as _RDRIVER  # type: ignore[import-not-found]
        except ImportError:
            _write_log(
                "[Melee RE Probe Runner] re_probe is not installed/enabled; "
                "install sdk_mods/re_probe and enable it first."
            )
            return

        if _RPROBE is not None and not _RPROBE.enabled:
            _RPROBE.enable()

        if opt_auto_trace.value and _RPROBE is not None:
            _RPROBE.tracer.arm(commands.MELEE_TRACE_PATTERNS, max_hooks=int(opt_trace_hooks.value))

        if opt_auto_dump.value and _RPROBE is not None:
            _RPROBE.start_dump("full")

        _write_log("[Melee RE Probe Runner] enabled — type 'mrp help' for commands")

    def _on_disable() -> None:
        try:
            from re_probe import PROBE as _RPROBE  # type: ignore[import-not-found]

            if _RPROBE is not None and _RPROBE.enabled:
                _RPROBE.tracer.disarm(remove_hooks=True)
        except ImportError:
            pass


def _write_log(message: str) -> None:
    if mods_base is None:
        return
    logger = getattr(mods_base, "logging", None)
    if logger is not None:
        fn = getattr(logger, "info", None)
        if callable(fn):
            try:
                fn(message)
                return
            except Exception:
                pass
    print(message)


# Build and register the mod with the mod menu (module level, not inside
# _write_log — otherwise build_mod never runs at import time).
mod = mods_base.build_mod(
    name="Melee RE Probe Runner",
    author=__author__,
    version=__version__,
    description=(
        "Drives RE Probe for melee-arsenal analysis: starts dumps, arms the "
        "melee combat trace, dumps held weapons, and parses dump results into "
        "CONFIRMED/PARTIAL/REFUTED findings. Use 'mrp help' for commands."
    ),
    supported_games=mods_base.Game.BL2,
    options=options,
    on_enable=_on_enable,
    on_disable=_on_disable,
)

# Register the mrp_* console commands after the mod exists.
if mods_base is not None:
    from unrealsdk.commands import add_command  # type: ignore[import-not-found]

    add_command("mrp", commands.handle_command)

__all__ = ["mod", "__version__", "__author__"]
