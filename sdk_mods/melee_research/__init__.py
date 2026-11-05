"""Interactive RE Probe evidence collector for Melee Arsenal (BL2)."""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

from .report import build_report, write_report

ROOT = Path(__file__).resolve().parent / "results"
SCENARIOS = {
    "combat": ["WillowGame.WillowWeapon:ConsumeAmmo", "WillowGame.WillowWeapon:SharedFireAmmunition", "WillowGame.WillowWeapon:InstantFire", "WillowGame.WillowWeapon:FiringModeDefinitionFire", "WillowGame.WillowWeapon:HasActiveAmmo", "WillowGame.WillowAIPawn:TakeDamage", "WillowGame.WillowPlayerPawn:TakeDamage", "WillowGame.Behavior_CauseDamage:*", "WillowGame.Behavior_Explode:*", "WillowGame.WillowWeapon:StartZoom", "WillowGame.WillowWeapon:ClientStartZoom", "WillowGame.WillowWeapon:StopZoom", "WillowGame.WillowWeapon:ClientStopZoom"],
    "inventory": ["WillowGame.WillowWeapon:InitializeInternal", "WillowGame.WillowWeapon:ChooseRandomNameParts", "WillowGame.WillowWeapon:InitializeFromDefinitionData", "WillowGame.WillowSaveGameManager:SaveGame", "WillowGame.WillowPlayerController:ValidateWeaponDefinition", "WillowGame.WillowPlayerController:ServerGrantMissionRewards", "WillowGame.ItemCardGFxObject:Set*"],
}
SNAPSHOT_SEARCHES = ["Pool_Weapons_All", "Weight_0_VeryCommon", "Weight_1_Common", "Weight_2_Uncommon", "Weight_3_Uncommoner", "Weight_4_Rare", "Weight_5_VeryRare", "Weight_6_Legendary", "MeleeArsenalDev"]
run = None


def say(message: str) -> None:
    print("[Melee Research] " + message)


def probe():
    # Require the mod to be enabled explicitly; never seize its lifecycle.
    from re_probe import PROBE, driver
    if PROBE is None or driver is None or not PROBE.enabled:
        raise RuntimeError("Enable RE Probe first (folder must be named re_probe, without a trailing space).")
    return PROBE, driver


def command(line: str, cmd_len: int) -> None:
    global run
    try:
        args = shlex.split(line[cmd_len:])
        action = args[0].lower() if args else "help"
        if action == "help":
            say("Commands: meleeprobe start | status | trace combat/inventory | stop | snapshot | report | cancel")
            return
        state, driver = probe()
        if action == "start":
            if run is not None:
                raise RuntimeError("A research session already exists; report or cancel it first.")
            if state.job is not None and state.job.state in ("running", "collecting"):
                raise RuntimeError("RE Probe already has a running dump; wait or cancel it yourself.")
            if state.tracer.state in ("arming", "armed"):
                raise RuntimeError("RE Probe already has an active trace; stop it yourself first.")
            ROOT.mkdir(parents=True, exist_ok=True)
            folder = ROOT / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            folder.mkdir()
            started = driver.start_dump("full", output_root=folder / "dump")
            if not started.get("ok"):
                raise RuntimeError(str(started))
            run = {"folder": folder, "dump": Path(started["output"]), "traces": [], "snapshots": [], "active": None}
            say("Full dump started. Use meleeprobe status; output: " + str(folder))
        elif action == "status":
            say(json.dumps({"session": str(run["folder"]) if run else None, "probe": state.status()}, default=str))
        elif action == "trace":
            if run is None or len(args) != 2 or args[1] not in SCENARIOS:
                raise RuntimeError("Start a session first; use trace combat or trace inventory.")
            if run["active"] or state.tracer.state in ("arming", "armed"):
                raise RuntimeError("A trace is already active; stop it first.")
            result = driver.trace_arm(SCENARIOS[args[1]], max_hooks=128)
            if not result.get("ok"):
                raise RuntimeError(str(result))
            run["active"] = {"scenario": args[1], "arm": result}
            say("Trace arming: " + json.dumps(result) + ". Wait for state armed in status, then perform the scenario and use meleeprobe stop.")
        elif action == "stop":
            if run is None or not run["active"]:
                raise RuntimeError("No research trace active.")
            before = driver.trace_status()
            if before.get("session_id") != run["active"]["arm"].get("session_id"):
                raise RuntimeError("RE Probe trace was replaced externally; refusing to stop it.")
            result = driver.trace_stop(directory=run["folder"] / "traces")
            trace = {**run["active"], "status_before_stop": before, "stop": result}
            run["active"] = None
            if result.get("path"):
                with open(result["path"], encoding="utf-8") as f:
                    trace["data"] = json.load(f)
            run["traces"].append(trace)
            say("Trace stopped: " + json.dumps(result))
        elif action == "snapshot":
            if run is None:
                raise RuntimeError("Start a session first.")
            # Limit work and report missing searches explicitly. dump_object writes under RE Probe.
            for term in SNAPSHOT_SEARCHES:
                paths = driver.find_objects(term, limit=5)
                entry = {"search": term, "matches": paths, "objects": []}
                for path in paths:
                    out = driver.dump_object(path, depth=4)
                    if out:
                        with open(out, encoding="utf-8") as f:
                            entry["objects"].append(json.load(f))
                run["snapshots"].append(entry)
            say("Snapshots captured. Missing matches may simply not be loaded; limits are 5 per search.")
        elif action == "report":
            if run is None:
                raise RuntimeError("No research session. Start one first.")
            if run["active"]:
                raise RuntimeError("Stop the research trace first.")
            if state.job and state.job.state in ("running", "collecting"):
                raise RuntimeError("Dump still running; check status and wait.")
            report = build_report(run["dump"], run["traces"], run["snapshots"])
            out = run["folder"] / "melee-research-report.json"
            write_report(out, report)
            say("Report written: " + str(out) + ". Send this file. Keep raw dump and traces for follow-up.")
            run = None
        elif action == "cancel":
            if run is None:
                raise RuntimeError("No research session.")
            if run["active"] and driver.trace_status().get("session_id") == run["active"]["arm"].get("session_id"):
                driver.trace_stop(directory=run["folder"] / "traces")
            if state.job and state.job.state in ("running", "collecting") and str(state.job.root) == str(run["dump"]):
                driver.cancel_dump()
            say("Session cancelled; partial files remain at " + str(run["folder"]))
            run = None
        else:
            say("Unknown command; use meleeprobe help.")
    except Exception as exc:
        say("Error: " + repr(exc))


try:
    import mods_base
    from unrealsdk.commands import add_command, remove_command
except ImportError:
    mod = None
else:
    def enable():
        add_command("meleeprobe", command)
        say("Enabled. Enable RE Probe, then use meleeprobe help.")

    def disable():
        # Do not cancel unrelated RE Probe work.
        global run
        if run is not None:
            try:
                command("meleeprobe cancel", len("meleeprobe"))
            except Exception as exc:
                say("Cleanup failed: " + repr(exc))
        remove_command("meleeprobe")

    mod = mods_base.build_mod(name="Melee Research", author="Melee Arsenal", version="0.1.0", description="Collect focused RE Probe evidence for Melee Arsenal.", supported_games=mods_base.Game.BL2, on_enable=enable, on_disable=disable)
