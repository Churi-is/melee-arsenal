"""Aggregate findings report: every melee-arsenal assumption vs the RE Probe dump.

Run after the dump + trace are collected:

    python -m re_probe_parse report <dump_dir> [<trace.json>] --out re_probe_reports/FINDINGS.md

Produces `FINDINGS.md` (human) and `FINDINGS.json` (machine) with one row per
assumption: CONFIRMED / PARTIAL / REFUTED / UNVERIFIED, plus evidence.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .assumptions import ASSUMPTIONS
from .classes import report_classes
from .enums import report_enums
from .functions import report_functions
from .loader import Dump
from .objects import report_objects
from .traces import report_trace
from .values import report_values


def _class_prop_found(classes_result: dict[str, Any], class_path: str, prop: str) -> bool:
    for entry in classes_result.get("classes") or []:
        if entry.get("path") == class_path:
            return prop in {p.get("name") for p in (entry.get("wanted_properties_found") or [])}
    return False


def _class_prop_missing(classes_result: dict[str, Any], class_path: str) -> list[str]:
    for entry in classes_result.get("classes") or []:
        if entry.get("path") == class_path:
            return list(entry.get("wanted_properties_missing") or [])
    return []


def _wanted_fn(functions_result: dict[str, Any], path: str) -> dict[str, Any] | None:
    for entry in functions_result.get("wanted") or []:
        if entry.get("path") == path:
            return entry
    return None


def _trace_called(traces_result: dict[str, Any] | None, callee: str) -> bool:
    if traces_result is None:
        return False
    for entry in traces_result.get("wanted") or []:
        if entry.get("path") == callee and entry.get("found"):
            return int(entry.get("calls") or 0) > 0
    return False


def _object_found(objects_result: dict[str, Any], path: str) -> bool:
    for entry in objects_result.get("wanted_objects") or []:
        if entry.get("path") == path:
            return bool(entry.get("found"))
    return False


def build_findings(dump_dir: str, trace_path: str | None = None) -> dict[str, Any]:
    dump = Dump(dump_dir)
    functions_result = report_functions(dump)
    classes_result = report_classes(dump)
    enums_result = report_enums(dump)
    objects_result = report_objects(dump)
    values_result = report_values(dump)
    xrefs_result = None  # xrefs rarely needed for assumptions; computed cheaply below
    from .xrefs import report_xrefs

    xrefs_result = report_xrefs(dump)
    traces_result = report_trace(trace_path) if trace_path else None

    findings: list[dict[str, Any]] = []

    for a in ASSUMPTIONS:
        aid = a["id"]
        cat = a["category"]
        status = "UNVERIFIED"
        evidence: list[str] = []

        if cat == "hooks":
            if aid == "HOOK-PLAYERTICK":
                f = _wanted_fn(functions_result, "WillowGame.WillowPlayerController:PlayerTick")
                g = _wanted_fn(functions_result, "Engine.GameViewportClient:Tick")
                pt_called = _trace_called(traces_result, "WillowGame.WillowPlayerController:PlayerTick")
                gv_called = traces_result and any(
                    e.get("path") == "Engine.GameViewportClient:Tick" and int(e.get("calls") or 0) > 0
                    for e in (traces_result.get("wanted") or [])
                )
                if bool(f and f.get("found")):
                    status = "CONFIRMED"
                    evidence.append("PlayerTick resolves as a UFunction")
                    if pt_called:
                        evidence.append(f"observed {f.get('calls')} calls in trace")
                    if gv_called:
                        status = "PARTIAL"
                        evidence.append("GameViewportClient:Tick IS called (contradicts 0-claim)")
                    elif g and not g.get("found"):
                        evidence.append("Engine.GameViewportClient:Tick absent from dump")
                else:
                    status = "REFUTED"
                    evidence.append("PlayerTick not found in functions dump")

            elif aid == "HOOK-ZOOM-QUAD":
                fns = [
                    "WillowGame.WillowWeapon:StartZoom",
                    "WillowGame.WillowWeapon:ClientStartZoom",
                    "WillowGame.WillowWeapon:StopZoom",
                    "WillowGame.WillowWeapon:ClientStopZoom",
                ]
                present = all(bool(_wanted_fn(functions_result, x) and _wanted_fn(functions_result, x).get("found")) for x in fns)
                observed = all(_trace_called(traces_result, x) for x in fns) if traces_result else False
                if present and observed:
                    status = "CONFIRMED"
                    evidence.append("all four zoom functions resolve and were observed called")
                elif present:
                    status = "PARTIAL"
                    evidence.append("all four resolve; not all observed in this trace session")
                else:
                    missing = [x for x in fns if not (_wanted_fn(functions_result, x) and _wanted_fn(functions_result, x).get("found"))]
                    status = "REFUTED"
                    evidence.append(f"missing from dump: {', '.join(missing)}")

            else:
                target = {
                    "HOOK-INSTANTFIRE": "WillowGame.WillowWeapon:InstantFire",
                    "HOOK-CONSUMEAMMO": "WillowGame.WillowWeapon:ConsumeAmmo",
                    "HOOK-SHAREDFIRE": "WillowGame.WillowWeapon:SharedFireAmmunition",
                    "HOOK-FX-FUNCS": "WillowGame.WillowWeapon:FiringModeDefinitionFire",
                    "HOOK-HASACTIVEAMMO": "WillowGame.WillowWeapon:HasActiveAmmo",
                    "HOOK-ITEMCARD": "WillowGame.ItemCardGFxObject:SetTopStat",
                    "HOOK-SAVE-AND-MISSION": "WillowGame.WillowSaveGameManager:SaveGame",
                }.get(aid)
                f = _wanted_fn(functions_result, target) if target else None
                if target and f and f.get("found"):
                    called = _trace_called(traces_result, target) if traces_result else None
                    status = "CONFIRMED" if called else "PARTIAL"
                    evidence.append(f"{target} resolves: {f.get('display_signature')}")
                    if called is True:
                        evidence.append(f"observed {f.get('calls')} calls in trace")
                    elif called is False:
                        evidence.append("defined but not observed in this trace session")
                else:
                    status = "REFUTED"
                    evidence.append(f"{target or aid} not found in functions dump")
                if aid == "HOOK-ITEMCARD":
                    bottom = _wanted_fn(functions_result, "WillowGame.ItemCardGFxObject:SetBottomStat")
                    if bottom and not bottom.get("found"):
                        evidence.append("SetBottomStat correctly absent (matches ENGINE_FACTS)")

        elif cat == "properties":
            mapping = {
                "PROP-WEAPONWRITES": ("WillowGame.WillowWeapon", [
                    "InstantHitDamageBaseValue", "FireIntervalBaseValue", "ShotCostBaseValue", "ReloadCnt",
                    "Spread", "StatusEffectDamage", "StatusEffectDamageBaseValue",
                    "BaseStatusEffectChanceModifier", "BaseStatusEffectChanceModifierBaseValue",
                    "StatusEffectChanceModifier", "StatusEffectChanceModifierBaseValue",
                    "ClipSizeBaseValue",
                ]),
                "PROP-WEAPONNODECL": ("WillowGame.WillowWeapon", ["Mesh", "WeaponTypeDefinition", "CurrentFiringModeDefinition", "bCanBeUsed"]),
                "PROP-BALANCE": ("WillowGame.WeaponBalanceDefinition", [
                    "WeaponPartListCollection", "RuntimePartListCollection", "InventoryDefinition",
                    "BaseDefinition", "Manufacturers", "PartListCollection", "bInterpolateExpLevel",
                ]),
                "PROP-POOL": ("WillowGame.ItemPoolDefinition", [
                    "BalancedItems", "Quantity", "MinGameStageRequirement", "MaxGameStageRequirement",
                    "bAutoReadyItems", "bShopsHaveInfiniteQuantity",
                ]),
            }
            class_path, props = mapping[aid]
            found_p = [p for p in props if _class_prop_found(classes_result, class_path, class_path and p)]
            missing_p = [p for p in props if p not in found_p]
            if not missing_p:
                status = "CONFIRMED"
                evidence.append(f"all {len(props)} properties present on {class_path} (declared or inherited)")
            elif found_p:
                status = "PARTIAL"
                evidence.append(f"{len(found_p)}/{len(props)} present; missing: {', '.join(missing_p)}")
            else:
                status = "REFUTED"
                evidence.append(f"none of {props} found on {class_path}")
            if aid == "PROP-WEAPONNODECL":
                # Negative assertion: these names must NOT exist anywhere up the chain.
                if found_p:
                    status = "REFUTED"
                    evidence = [f"unexpectedly present on {class_path}: {', '.join(found_p)}"]
                else:
                    status = "CONFIRMED"
                    evidence = [f"none of {props} exist on {class_path} (negative assertion confirmed)"]

        elif cat == "structs":
            structs_result = classes_result  # structs are inside the classes report
            target = {
                "STRUCT-DEFINITIONDATA": "WillowGame.WillowWeaponTypes:WeaponDefinitionData",
                "STRUCT-WEIGHTEDROW": "WillowGame.ItemPoolDefinition:BalancedInventoryData",
            }[aid]
            found_struct = False
            present_fields: list[str] = []
            for entry in (structs_result.get("structs") or []):
                if entry.get("path") == target and entry.get("found"):
                    found_struct = True
                    present_fields = [p.get("name") for p in (entry.get("properties") or [])]
                    break
            if aid == "STRUCT-DEFINITIONDATA":
                want = [
                    "WeaponTypeDefinition", "BalanceDefinition", "ManufacturerDefinition",
                    "ManufacturerGradeIndex", "BodyPartDefinition", "GripPartDefinition",
                    "BarrelPartDefinition", "ElementalPartDefinition", "Accessory1PartDefinition",
                    "Accessory2PartDefinition", "MaterialPartDefinition", "PrefixPartDefinition",
                    "TitlePartDefinition", "GameStage", "UniqueId",
                ]
            else:
                want = ["InvBalanceDefinition", "ItmPoolDefinition", "Probability", "bDropOnDeath"]
            if not found_struct:
                status = "UNVERIFIED"
                evidence.append(f"{target} struct not in dump")
            else:
                missing = [p for p in want if p not in present_fields]
                if not missing:
                    status = "PARTIAL" if aid == "STRUCT-WEIGHTEDROW" else "CONFIRMED"
                    evidence.append(f"{target} layout reflected: {', '.join(want)}")
                    if aid == "STRUCT-WEIGHTEDROW":
                        evidence.append("Manufacturer fields and native selection behavior are not established by this layout.")
                else:
                    status = "PARTIAL"
                    evidence.append(f"{target} partial: missing {', '.join(missing)}")

        elif cat == "enums":
            rec = None
            for e in (enums_result.get("enums") or []):
                if e.get("path") == "WillowGame.InventoryCardGFx:ETopStatArrow":
                    rec = e
                    break
            if not rec or not rec.get("found"):
                status = "UNVERIFIED"
                evidence.append("ETopStatArrow enum not in dump")
            else:
                amap = {v["name"]: v["value"] for v in (rec.get("values") or [])}
                expected = {"ETSA_Blank": 0, "ETSA_Up": 1, "ETSA_Down": 2, "ETSA_Same": 3}
                if all(amap.get(name) == value for name, value in expected.items()):
                    status = "CONFIRMED"
                    evidence.append(f"byte mapping matches mod: {amap}")
                else:
                    status = "PARTIAL"
                    evidence.append(f"observed mapping {amap} vs assumed {expected}")

        elif cat == "objects":
            wanted = objects_result.get("wanted_objects") or []
            donor_paths = [e for e in wanted if e["path"].startswith(("GD_", "GD_Episode01"))]
            asset_paths = [e for e in wanted if not e["path"].startswith(("GD_", "GD_Episode01"))]
            group = donor_paths if aid == "OBJ-DONORS" else asset_paths
            found_paths = [e["path"] for e in group if e.get("found")]
            missing = [e["path"] for e in group if not e.get("found")]
            mismatches = [e["path"] for e in group if e.get("found") and not e.get("class_matches_expectation")]
            if not missing and not mismatches and group:
                status = "CONFIRMED"
                evidence.append(f"all {len(group)} wanted paths present with expected classes")
            elif found_paths:
                status = "PARTIAL"
                evidence.append(f"{len(found_paths)}/{len(group)} found; missing: {', '.join(missing[:6])}{'...' if len(missing) > 6 else ''}")
            else:
                status = "REFUTED"
                evidence.append("none of the wanted paths found")

        elif cat == "values":
            total = values_result.get("defaults_checked", 0)
            found = values_result.get("defaults_found", 0)
            if found == total and total:
                status = "CONFIRMED"
                evidence.append(f"all {total} wanted CDO defaults present")
            elif found:
                status = "PARTIAL"
                evidence.append(f"{found}/{total} wanted defaults present")
            else:
                status = "REFUTED"
                evidence.append("no wanted CDO defaults found (defaults section empty?)")

        elif cat == "trace":
            status = "UNVERIFIED"
            evidence.append("trace not supplied — run `reprobe trace ...` then pass trace-*.json")

        findings.append(
            {
                "id": aid,
                "category": cat,
                "mod": a["mod"],
                "assumption": a["assumption"],
                "check": a["check"],
                "simplify_if_confirmed": a["simplify_if_confirmed"],
                "risk_if_refuted": a["risk_if_refuted"],
                "status": status,
                "evidence": evidence,
            }
        )

    summary: dict[str, int] = {}
    for fnd in findings:
        summary[fnd["status"]] = summary.get(fnd["status"], 0) + 1

    return {
        "dump_dir": dump_dir,
        "dump_id": dump.dump_id,
        "profile": dump.profile,
        "status": dump.status,
        "trace_path": trace_path,
        "summary": summary,
        "functions": functions_result,
        "classes": classes_result,
        "enums": enums_result,
        "objects": objects_result,
        "values": values_result,
        "xrefs": xrefs_result,
        "traces": traces_result,
        "findings": findings,
    }


def _md_table(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    lines.append("| id | category | status | evidence |")
    lines.append("|---|---|---|---|")
    for fnd in rows:
        ev = " / ".join(fnd["evidence"]) if fnd["evidence"] else "—"
        lines.append(f"| `{fnd['id']}` | {fnd['category']} | **{fnd['status']}** | {ev} |")
    return lines


def write_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Melee Arsenal — RE Probe findings")
    lines.append("")
    lines.append(f"- Dump: `{report['dump_dir']}` (id: {report['dump_id']}, profile: {report['profile']}, status: {report['status']})")
    if report["trace_path"]:
        lines.append(f"- Trace: `{report['trace_path']}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for status in ("CONFIRMED", "PARTIAL", "REFUTED", "UNVERIFIED"):
        lines.append(f"- {status}: {report['summary'].get(status, 0)}")
    lines.append(f"- Total assumptions checked: {len(report['findings'])}")
    lines.append("")
    lines.append("## Assumption-by-assumption findings")
    lines.append("")
    lines.extend(_md_table(report["findings"]))
    lines.append("")
    lines.append("## Recommended simplifications")
    lines.append("")
    confirmed = [f for f in report["findings"] if f["status"] == "CONFIRMED"]
    partial = [f for f in report["findings"] if f["status"] == "PARTIAL"]
    refuted = [f for f in report["findings"] if f["status"] == "REFUTED"]
    if confirmed:
        lines.append("### Safe to simplify (CONFIRMED)")
        for fnd in confirmed:
            lines.append(f"- **{fnd['id']}** — {fnd['simplify_if_confirmed']}")
        lines.append("")
    if partial:
        lines.append("### Verify before changing (PARTIAL)")
        for fnd in partial:
            lines.append(f"- **{fnd['id']}** — {fnd['simplify_if_confirmed']}  (evidence: {'; '.join(fnd['evidence'])})")
        lines.append("")
    if refuted:
        lines.append("### Do NOT touch yet (REFUTED — assumptions broken)")
        for fnd in refuted:
            lines.append(f"- **{fnd['id']}** — {fnd['risk_if_refuted']}  (evidence: {'; '.join(fnd['evidence'])})")
        lines.append("")
    lines.append("## Raw section summaries")
    lines.append("")
    fn = report["functions"]
    lines.append(f"- Functions: {fn['total_functions']} total; {fn['wanted_found']}/{fn['wanted_checked']} wanted found; missing: {', '.join(fn['wanted_missing']) or 'none'}")
    ob = report["objects"]
    lines.append(f"- Objects: {ob['total_objects']} indexed; wanted objects {ob['wanted_found']}/{len(ob['wanted_objects'])}; missing: {', '.join(ob['wanted_missing']) or 'none'}")
    vl = report["values"]
    lines.append(f"- Values: {vl['defaults_found']}/{vl['defaults_checked']} CDO defaults; {vl['instances_found']}/{len(vl['instances'])} live instances")
    tr = report["traces"]
    if tr:
        se = tr.get("session") or {}
        lines.append(f"- Trace: hooks={se.get('hooks')} calls={se.get('calls')} edges={se.get('edges')} functions={se.get('functions')}")
    else:
        lines.append("- Trace: not supplied")
    return "\n".join(lines)


def save_report(report: dict[str, Any], out_dir: str) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "FINDINGS.md")
    json_path = os.path.join(out_dir, "FINDINGS.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(write_markdown(report))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    return md_path, json_path
