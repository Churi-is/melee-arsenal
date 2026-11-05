"""The in-game surface of `melee_arsenal_probe`: Mod Menu options, commands, and hook wiring.

Kept out of `__init__.py` so the package still imports outside Borderlands 2 (lint, CI, tooling)
where `mods_base` does not exist.
"""
from __future__ import annotations

from typing import Any

from mods_base import (  # type: ignore[import-not-found]
    BoolOption,
    ButtonOption,
    Game,
    SliderOption,
    build_mod,
    command,
)

from .probe import census, live, report
from .probe.common import JSON_PATH, REPORT_PATH, log


def _notify(title: str, message: str) -> None:
    """Best-effort HUD toast so a console command has visible feedback."""
    try:
        pc = live.local_player_controller()
        hud = pc.GetHUDMovie() if pc is not None else None
        if hud is not None:
            hud.AddTrainingText(message, title, 4.5, (), "", False, 0, pc.PlayerReplicationInfo, True)
    except Exception:
        pass
    log(f"{title}: {message}")


def _dump(sections: list[str] | None = None) -> dict[str, Any]:
    payload = report.run_sections(sections)
    paths = report.write(payload)
    summary = report.summarize(payload)
    if not paths.get("json"):
        log(f"probe dump could not be written ({summary})")
        _notify("Melee Arsenal Probe", "Dump failed — see the SDK console.")
        return payload
    log(f"wrote {paths['json']} ({summary})")
    _notify("Melee Arsenal Probe", f"{summary} -> ground_truth/")
    return payload


# Mod-menu options ---------------------------------------------------------------------------


def _on_census_changed(_opt: Any, new_val: bool) -> None:
    census.set_enabled(bool(new_val))
    if new_val:
        census.reset()
        log("hook census armed")


def _on_sample_limit_changed(_opt: Any, new_val: float) -> None:
    census.set_sample_limit(int(new_val))


def _on_dump_pressed(_opt: Any) -> None:
    _dump()


opt_census = BoolOption(
    "Record Hook Census",
    True,
    description=(
        "Count every call into the candidate engine hooks and keep the first call's arguments. "
        "This is what proves which of the paired hooks (tick, zoom, item card) are actually dead."
    ),
    on_change_anytime=_on_census_changed,
)

opt_samples = SliderOption(
    "Census Argument Samples",
    4,
    0,
    4,
    1,
    True,
    description="How many first-call argument dumps to keep per hook (0 disables argument capture).",
    on_change_anytime=_on_sample_limit_changed,
)

btn_dump = ButtonOption(
    "Dump Ground Truth",
    description="Run every probe section and write ground_truth/ground_truth.json + GROUND_TRUTH.md.",
    on_press=_on_dump_pressed,
)


# Console commands --------------------------------------------------------------------------


@command("probe_dump", description="Run every probe section and write ground_truth/ JSON + review sheet.")
def cmd_probe_dump(_args: Any) -> None:
    _dump()


@command("probe_schema", description="Dump UFunction parameters plus class, struct, and enum layouts.")
def cmd_probe_schema(_args: Any) -> None:
    _dump(["schema"])


@command("probe_assets", description="Dump loaded asset, firing-mode, and loot-pool catalogues.")
def cmd_probe_assets(_args: Any) -> None:
    _dump(["catalogue"])


@command("probe_live", description="Dump the current pawn, arms, anim slots, held weapon, and victim classes.")
def cmd_probe_live(_args: Any) -> None:
    _dump(["live"])


@command("probe_census", description="Dump hook firing counts, call order, arguments, and frame cadence.")
def cmd_probe_census(_args: Any) -> None:
    _dump(["census"])


@command("probe_reset", description="Clear the hook census counters and start measuring again.")
def cmd_probe_reset(_args: Any) -> None:
    census.reset()
    _notify("Melee Arsenal Probe", "Census cleared — play for a minute, then run probe_census.")


@command("probe_plan", description="Show which census hooks resolve on this build and which are dead.")
def cmd_probe_plan(_args: Any) -> None:
    plan = census.describe_plan()
    log(f"probe_plan: {plan}")
    missing = plan.get("missing") or {}
    dead = sum(len(names) for names in missing.values())
    _notify(
        "Melee Arsenal Probe",
        f"{plan.get('total_hooks', 0)} hooks armed, {dead} candidate targets missing on this build.",
    )


@command("probe_where", description="Print where the probe wrote its output files.")
def cmd_probe_where(_args: Any) -> None:
    log(f"probe output: {JSON_PATH} | {REPORT_PATH}")
    _notify("Melee Arsenal Probe", "Output: ground_truth/ground_truth.json")


ALL_COMMANDS = [
    cmd_probe_dump,
    cmd_probe_schema,
    cmd_probe_assets,
    cmd_probe_live,
    cmd_probe_census,
    cmd_probe_reset,
    cmd_probe_plan,
    cmd_probe_where,
]

ALL_OPTIONS = [opt_census, opt_samples, btn_dump]

HOOKS = census.build_hooks()


def _on_mod_disable() -> None:
    census.set_enabled(False)


def build_probe_mod(version: str, author: str) -> Any:
    return build_mod(
        name="Melee Arsenal Probe",
        author=author,
        version=version,
        description=(
            "Developer probe for Melee Arsenal: records the engine facts the mod currently "
            "rediscovers at run time (hook existence and firing order, class and struct layouts, "
            "enum values, exact asset paths, pool weights, item-card icons, animation sequences). "
            "Run probe_dump, then copy ground_truth/ into the repository."
        ),
        supported_games=Game.BL2,
        auto_enable=False,
        options=ALL_OPTIONS,
        hooks=HOOKS,
        commands=ALL_COMMANDS,
        on_disable=_on_mod_disable,
    )

