"""RE Probe — Borderlands 2 reflection dumper.

Enable the mod, then run ``reprobe start`` in the console (or use the options
menu).  A frame-budgeted dump job walks UE3 reflection and writes a directory of
strict-JSON shards plus a manifest.  See docs/JSON_FORMAT.md for the schema and
query recipes.

This module is deliberately small: all probe logic lives in ``probe/`` so it can
be imported and inspected outside the game.

Library use (other mods drive RE Probe directly, no console needed)::

    from re_probe import PROBE, driver

    driver.start_dump("full")
    driver.trace_arm(["WillowGame.WillowWeapon:*"], max_hooks=384)
    # ... play ...
    driver.trace_stop()
    driver.cancel_dump()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__version__ = "1.0.1"
__author__ = "RE Probe contributors"
__description__ = (
    "Reverse-engineering probe: dumps UE3 objects, classes, functions, parameters, "
    "properties, structs, enums, globals, asset paths and live call graphs as JSON."
)

try:
    import mods_base  # type: ignore[import-not-found]
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - offline import / tooling
    mods_base = None  # type: ignore[assignment]
    unrealsdk = None  # type: ignore[assignment]
    mod = None  # type: ignore[assignment]
    PROBE = None  # type: ignore[assignment]
    driver = None  # type: ignore[assignment]

else:
    from .probe.dump import DumpSettings
    from .probe.driver import ProbeDriver
    from .probe.state import PROBE

    driver = ProbeDriver()

    # -- options ------------------------------------------------------------
    # The settings objects are the runtime source of truth; options update them
    # through callbacks, and _sync_settings() re-applies everything on enable
    # (so settings restored from disk are honoured too).

    def _set_dump(name: str):
        def _callback(option: Any, value: Any) -> None:
            setattr(PROBE.settings, name, value)

        return _callback

    def _set_trace(name: str):
        def _callback(option: Any, value: Any) -> None:
            setattr(PROBE.trace_settings, name, value)

        return _callback

    profile_option = mods_base.DropdownOption(
        "Dump profile",
        "full",
        choices=["full", "definitions", "core"],
        display_name="Dump profile",
        description=(
            "full = definitions + CDO values + live instances + globals + xrefs. "
            "definitions = reflection + object index + packages + assets. "
            "core = classes/functions/structs/enums/consts only."
        ),
        on_change_anytime=_set_dump("profile"),
    )
    capture_defaults_option = mods_base.BoolOption(
        "Capture CDO defaults",
        True,
        display_name="Capture CDO defaults",
        description="Read default property values off every class default object.",
        on_change_anytime=_set_dump("capture_defaults"),
    )
    capture_instances_option = mods_base.BoolOption(
        "Capture live instances",
        True,
        display_name="Capture live instances",
        description="Read values from a capped, priority-sorted sample of live objects.",
        on_change_anytime=_set_dump("capture_instances"),
    )
    capture_globals_option = mods_base.BoolOption(
        "Capture globals",
        True,
        display_name="Capture globals",
        description="Capture UConst values and live engine/game-info singletons.",
        on_change_anytime=_set_dump("capture_globals"),
    )
    inherited_defaults_option = mods_base.BoolOption(
        "Include inherited defaults",
        False,
        display_name="Include inherited CDO values",
        description=(
            "When off, each class's defaults contain only properties declared on that "
            "class; walk super_chain to see inherited values."
        ),
        on_change_anytime=_set_dump("include_inherited_defaults"),
    )
    depth_option = mods_base.SliderOption(
        "Value depth",
        2,
        min_value=1,
        max_value=6,
        step=1,
        is_integer=True,
        display_name="Value depth",
        description="How many nested struct/array levels to serialize in value sections.",
        on_change_anytime=_set_dump("value_depth"),
    )
    array_limit_option = mods_base.SliderOption(
        "Array item limit",
        24,
        min_value=0,
        max_value=256,
        step=1,
        is_integer=True,
        display_name="Array item limit",
        description="Maximum array/static-array elements per value (0 = unlimited).",
        on_change_anytime=_set_dump("array_limit"),
    )
    string_limit_option = mods_base.SliderOption(
        "String length limit",
        384,
        min_value=64,
        max_value=4096,
        step=32,
        is_integer=True,
        display_name="String length limit",
        description="Maximum characters retained per string value.",
        on_change_anytime=_set_dump("string_limit"),
    )
    instance_limit_option = mods_base.SliderOption(
        "Live instance limit",
        1000,
        min_value=0,
        max_value=10000,
        step=50,
        is_integer=True,
        display_name="Live instance limit",
        description="Maximum number of live objects whose values are captured.",
        on_change_anytime=_set_dump("max_instances"),
    )
    xref_limit_option = mods_base.SliderOption(
        "Xref limit",
        200000,
        min_value=10000,
        max_value=1000000,
        step=10000,
        is_integer=True,
        display_name="Cross-reference limit",
        description="Maximum distinct object reference edges retained for xrefs.json.",
        on_change_anytime=_set_dump("max_xrefs"),
    )
    tick_budget_option = mods_base.SliderOption(
        "Tick budget (ms)",
        6,
        min_value=1,
        max_value=30,
        step=1,
        is_integer=True,
        display_name="Tick budget (ms)",
        description="Milliseconds of dump work allowed per game tick. Lower = smoother game.",
        on_change_anytime=_set_dump("tick_budget_ms"),
    )
    trace_hooks_option = mods_base.SliderOption(
        "Trace hook limit",
        512,
        min_value=16,
        max_value=4096,
        step=16,
        is_integer=True,
        display_name="Trace hook limit",
        description="Maximum number of UFunctions a single trace session may hook.",
        on_change_anytime=_set_trace("max_hooks"),
    )
    auto_start_option = mods_base.BoolOption(
        "Auto-start full dump",
        False,
        display_name="Auto-start full dump on enable",
        description="Starts a full dump as soon as the mod is enabled (e.g. on game launch).",
    )
    start_full_button = mods_base.ButtonOption(
        "Start full dump",
        display_name="Start full dump",
        description="Run the full profile: definitions, defaults, live instances, globals, xrefs.",
        on_press=lambda option: PROBE.start_dump("full"),
    )
    start_definitions_button = mods_base.ButtonOption(
        "Start definitions dump",
        display_name="Start definitions dump",
        description="Definitions, object index, packages and assets only (faster, smaller).",
        on_press=lambda option: PROBE.start_dump("definitions"),
    )
    cancel_button = mods_base.ButtonOption(
        "Cancel dump",
        display_name="Cancel running dump",
        description="Stop the job and keep the shards written so far.",
        on_press=lambda option: PROBE.cancel_dump(),
    )
    write_trace_button = mods_base.ButtonOption(
        "Write trace",
        display_name="Write current trace",
        description="Write the current trace aggregation to dumps/traces/.",
        on_press=lambda option: PROBE.tracer.write(
            Path(__file__).resolve().parent / "dumps" / "traces"
        ),
    )

    options = [
        profile_option,
        capture_defaults_option,
        capture_instances_option,
        capture_globals_option,
        inherited_defaults_option,
        depth_option,
        array_limit_option,
        string_limit_option,
        instance_limit_option,
        xref_limit_option,
        tick_budget_option,
        trace_hooks_option,
        auto_start_option,
        start_full_button,
        start_definitions_button,
        cancel_button,
        write_trace_button,
    ]

    keybinds = [
        mods_base.keybind(
            "Start full dump",
            None,
            callback=lambda: PROBE.start_dump("full"),
            display_name="Start full dump",
            description="Starts a full reflection dump. Unbound by default.",
        ),
    ]

    def _sync_settings() -> None:
        PROBE.settings = DumpSettings(
            profile=profile_option.value,
            include_inherited_defaults=inherited_defaults_option.value,
            capture_defaults=capture_defaults_option.value,
            capture_instances=capture_instances_option.value,
            capture_globals=capture_globals_option.value,
            value_depth=int(depth_option.value),
            array_limit=int(array_limit_option.value),
            string_limit=int(string_limit_option.value),
            max_instances=int(instance_limit_option.value),
            max_xrefs=int(xref_limit_option.value),
            tick_budget_ms=float(tick_budget_option.value),
        )
        PROBE.trace_settings.max_hooks = int(trace_hooks_option.value)

    def _on_enable() -> None:
        _sync_settings()
        PROBE.enable()
        if auto_start_option.value:
            PROBE.start_dump(profile_option.value)

    def _on_disable() -> None:
        PROBE.disable()

    mod = mods_base.build_mod(
        name="RE Probe",
        author=__author__,
        description=(
            "Dumps every reachable UE3 object, class, UFunction (with parameter names/types), "
            "UProperty, struct, enum, CDO default, global, asset path and value cross-reference "
            "to parseable JSON. Adds an on-demand runtime call tracer."
        ),
        version=__version__,
        supported_games=mods_base.Game.BL2,
        options=options,
        keybinds=keybinds,
        on_enable=_on_enable,
        on_disable=_on_disable,
    )

__all__ = ["__version__", "mod", "PROBE", "driver"]
