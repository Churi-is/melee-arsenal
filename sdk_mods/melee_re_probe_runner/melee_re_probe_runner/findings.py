"""Build a CONFIRMED / PARTIAL / REFUTED findings report from parsed dump data.

This mirrors the out-of-game toolkit in ``tools/re_probe_parse/`` but runs
in-game (stdlib only) so ``mrp_parse <dump_dir>`` produces a report without
leaving the game.

Each assumption has:
- id, category, mod location, assumption text, how to check it,
- how to simplify if confirmed, what breaks if refuted
- status + evidence drawn from the parsed dump sections
"""

from __future__ import annotations

import json
from typing import Any

from .parser import WANTED_FUNCTIONS, WANTED_CLASSES, WANTED_ENUMS, WANTED_OBJECTS

# Each entry: id, category, mod, assumption, check, simplify_if_confirmed,
# risk_if_refuted.
ASSUMPTIONS: list[dict[str, str]] = [
    {
        "id": "HOOK-INSTANTFIRE",
        "category": "hooks",
        "mod": "runtime/__init__.py HOOKS + combat/hooks.py",
        "assumption": "WillowWeapon:InstantFire resolves and fires once per melee swing; hooking it PRE + Block replaces the gun shot.",
        "check": "functions: InstantFire present; trace: callee observed with calls > 0.",
        "simplify_if_confirmed": "Keep the hook; drop the SharedFireAmmunition backup dedup if never observed.",
        "risk_if_refuted": "Primary melee attacks do nothing silently.",
    },
    {
        "id": "HOOK-CONSUMEAMMO",
        "category": "hooks",
        "mod": "runtime/__init__.py HOOKS + combat/hooks.py",
        "assumption": "WillowWeapon:ConsumeAmmo fires per swing (POST hook refills the magazine).",
        "check": "functions: ConsumeAmmo present; trace: callee observed.",
        "simplify_if_confirmed": "ConsumeAmmo hook is the right shot counter.",
        "risk_if_refuted": "Magazine refill and ammo tracking never run.",
    },
    {
        "id": "HOOK-SHAREDFIRE",
        "category": "hooks",
        "mod": "runtime/__init__.py HOOKS + combat/hooks.py",
        "assumption": "SharedFireAmmunition is a secondary firing path suppressed mid-throw (shot-sequence dedup).",
        "check": "trace: SharedFireAmmunition calls == 0 during melee-only session.",
        "simplify_if_confirmed": "Drop the _SHOT_SEQ / _HANDLED_SHOT_SEQ dedup entirely.",
        "risk_if_refuted": "Delayed swings fire after throw rebound.",
    },
    {
        "id": "HOOK-FX-FUNCS",
        "category": "hooks",
        "mod": "combat/hooks.py",
        "assumption": "FiringModeDefinitionFire, PlayFireEffects, WeaponFired, PlayAnimation(Fire_*) fire per swing and are safely blocked.",
        "check": "functions: all four present; trace: each callee called while swinging.",
        "simplify_if_confirmed": "Four-hook blocking strategy is justified.",
        "risk_if_refuted": "Muzzle flash/recoil animations leak on melee swings.",
    },
    {
        "id": "HOOK-PLAYERTICK",
        "category": "hooks",
        "mod": "runtime/__init__.py _run_frame_tick + docs/ENGINE_FACTS.md",
        "assumption": "PlayerTick is the only tick driver (~12k calls); Engine.GameViewportClient:Tick resolves but is never called.",
        "check": "functions: PlayerTick present; trace: PlayerTick calls > 0, GameViewportClient:Tick absent or 0.",
        "simplify_if_confirmed": "Single tick driver; no de-dup guard needed.",
        "risk_if_refuted": "If GameViewportClient:Tick fires and PlayerTick does not, frame work stalls.",
    },
    {
        "id": "HOOK-ZOOM-QUAD",
        "category": "hooks",
        "mod": "runtime/__init__.py HOOKS + docs/ENGINE_FACTS.md",
        "assumption": "StartZoom, ClientStartZoom, StopZoom, ClientStopZoom all four fire for RMB; not collapsible to one pair.",
        "check": "trace: all four callees observed with calls > 0 during RMB guard/throw.",
        "simplify_if_confirmed": "Keep all four hooks.",
        "risk_if_refuted": "Guard throw/release handling misses some builds.",
    },
    {
        "id": "HOOK-HASACTIVEAMMO",
        "category": "hooks",
        "mod": "runtime/__init__.py _on_has_active_ammo",
        "assumption": "WillowWeapon:HasActiveAmmo exists; returning Block + True gives infinite melee ammo.",
        "check": "functions: path present; trace: callee observed.",
        "simplify_if_confirmed": "Hook stays.",
        "risk_if_refuted": "Out-of-ammo handling fires on blades (dry clicks).",
    },
    {
        "id": "HOOK-ITEMCARD",
        "category": "hooks",
        "mod": "runtime/ui_item_card.py",
        "assumption": "SetItemCard / SetItemCardEx / SetTitle / SetTopStat / SetFunStats resolve; SetBottomStat does NOT.",
        "check": "functions: the five present, SetBottomStat absent.",
        "simplify_if_confirmed": "Card-override hook set is exactly right.",
        "risk_if_refuted": "Item card stats fail to render on some builds.",
    },
    {
        "id": "HOOK-SAVE-AND-MISSION",
        "category": "hooks",
        "mod": "runtime/persistence_bridge.py + resolver.py",
        "assumption": "WillowSaveGameManager:SaveGame PRE snapshots before save; ValidateWeaponDefinition PRE Block+True lets melee pass sanity; ServerGrantMissionRewards(mission, False) grants one item directly.",
        "check": "functions: all three paths present.",
        "simplify_if_confirmed": "Save/spawn flow assumptions hold.",
        "risk_if_refuted": "Sidecar restore and world drops break silently.",
    },
    {
        "id": "PROP-WEAPONWRITES",
        "category": "properties",
        "mod": "resolver.py sync_live_weapon_properties",
        "assumption": "sync_live_weapon_properties writes 14 properties on live WillowWeapon (InstantHitDamageBaseValue, FireIntervalBaseValue, ...). Most guarded by hasattr.",
        "check": "classes: WillowWeapon effective properties — which of the 14 exist (declared or inherited).",
        "simplify_if_confirmed": "Drop hasattr guards for confirmed properties; remove writes to absent ones.",
        "risk_if_refuted": "Card damage/fire interval/shot cost never reach the engine.",
    },
    {
        "id": "PROP-WEAPONNODECL",
        "category": "properties",
        "mod": "docs/ENGINE_FACTS.md",
        "assumption": "WillowWeapon declares no Mesh, no WeaponTypeDefinition, no CurrentFiringModeDefinition, no bCanBeUsed.",
        "check": "classes: those four names absent from WillowWeapon effective properties.",
        "simplify_if_confirmed": "Negative facts firm; no code touches them.",
        "risk_if_refuted": "Docs assumptions need revising.",
    },
    {
        "id": "PROP-BALANCE",
        "category": "properties",
        "mod": "definitions/balances.py",
        "assumption": "WeaponBalanceDefinition declares WeaponPartListCollection/RuntimePartListCollection/InventoryDefinition/BaseDefinition/Manufacturers/PartListCollection/bInterpolateExpLevel, but NOT MinGameStageIndex/MaxGameStageIndex/GameStageRequirement.",
        "check": "classes: balance effective properties match.",
        "simplify_if_confirmed": "GameStage gating on balances confirmed absent.",
        "risk_if_refuted": "Grade GameStage manipulation writes nonexistent fields.",
    },
    {
        "id": "PROP-POOL",
        "category": "properties",
        "mod": "loot_pools.py",
        "assumption": "ItemPoolDefinition declares BalancedItems/Quantity/MinGameStageRequirement/MaxGameStageRequirement/bAutoReadyItems/bShopsHaveInfiniteQuantity; no ItemPool property.",
        "check": "classes + structs: ItemPoolDefinition, WeightedInventoryBalanceData, AttributeInitializationData layouts.",
        "simplify_if_confirmed": "Pool entry field names confirmed; replace _eval_prob_weight name-guessing with offset reads.",
        "risk_if_refuted": "Loot injection writes wrong fields; pools silently ignored.",
    },
    {
        "id": "STRUCT-DEFINITIONDATA",
        "category": "structs",
        "mod": "resolver.py apply_instance_to_definition_data",
        "assumption": "WeaponDefinitionData has fields WeaponTypeDefinition, BalanceDefinition, ManufacturerDefinition, ManufacturerGradeIndex, BodyPartDefinition, GripPartDefinition, BarrelPartDefinition, ... UniqueId.",
        "check": "structs: WeaponDefinitionData property list matches.",
        "simplify_if_confirmed": "setattr chain confirmed safe; order-independent writes OK.",
        "risk_if_refuted": "Half-built DefinitionData; missing parts/names on spawned weapons.",
    },
    {
        "id": "STRUCT-WEIGHTEDROW",
        "category": "structs",
        "mod": "loot_pools.py / definitions/balances.py",
        "assumption": "WeightedInventoryBalanceData.Probability is AttributeInitializationData; ManufacturerCustomPartWeightData has Manufacturer + DefaultWeightIndex + Min/MaxGameStageIndex.",
        "check": "structs: those three layouts.",
        "simplify_if_confirmed": "Replace heuristic weight evaluation with exact BaseValueConstant reads.",
        "risk_if_refuted": "Pool weights miscalculated; wrong drop rarity share.",
    },
    {
        "id": "ENUM-ARROW",
        "category": "enums",
        "mod": "ui_item_card.py _ARROW_BYTES",
        "assumption": "ETopStatArrow enum values are NONE=0, BETTER=1, EQUAL=2, WORSE=3.",
        "check": "enums: ETopStatArrow values present and match.",
        "simplify_if_confirmed": "Read mapping from dump instead of hard-coding.",
        "risk_if_refuted": "Compare arrows render wrong.",
    },
    {
        "id": "OBJ-DONORS",
        "category": "objects",
        "mod": "definitions/donors.py + definitions/balances.py",
        "assumption": "Donor paths exist: WeaponType_<Maker>_Pistol, Manufacturers.<Maker>, Pistol_<Maker>[suffixes], firing modes, pools, M_Ep1_Champion.",
        "check": "objects: each wanted path present with expected class.",
        "simplify_if_confirmed": "Donor harvest can drop fallback chains for confirmed paths.",
        "risk_if_refuted": "Definitions build silently degrades (missing parts/balances).",
    },
    {
        "id": "OBJ-ASSETS",
        "category": "objects",
        "mod": "resources.py + docs/ENGINE_FACTS.md",
        "assumption": "Combat assets loaded: melee AkEvents, Canim CameraAnims, explosion Particles, Impact_MeleeGeneric, DmgType_*, Hands_Assassin.",
        "check": "objects: paths present with expected asset classes.",
        "simplify_if_confirmed": "Asset fallback chains can be shortened to the confirmed first hit.",
        "risk_if_refuted": "Swing audio, explosions, impact FX, or first-person hands silent/missing.",
    },
    {
        "id": "VALUE-RARITYWEIGHTS",
        "category": "values",
        "mod": "loot_pools.py _KNOWN_WEIGHT_INIT_DEFS + ENGINE_FACTS.md",
        "assumption": "Rarity weight init defs resolve to 200/100/10/5/1/0.1/0.01 (Weight_0_VeryCommon … Weight_6_Legendary); three named weights are 0.",
        "check": "values: donor ItemPoolDefinition CDOs / AttributeInitializationData constants.",
        "simplify_if_confirmed": "Hard-coded rarity table confirmed exact.",
        "risk_if_refuted": "World drop rarity distribution is off.",
    },
    {
        "id": "VALUE-CLASS-SUM",
        "category": "values",
        "mod": "loot_pools.py VANILLA_WEAPON_CLASS_SUM = 520.0",
        "assumption": "Vanilla weapon class weight total is 520.0 (Pistols/SMGs/ARs/Shotguns 100 each, Snipers 75, Launchers 45).",
        "check": "values: Pool_Weapons_All CDO entry weights per class pool.",
        "simplify_if_confirmed": "Constant confirmed; melee share math exact.",
        "risk_if_refuted": "Injected melee share differs from advertised ~16% at weight 100.",
    },
    {
        "id": "TRACE-CALLGRAPH",
        "category": "trace",
        "mod": "all combat modules",
        "assumption": "Observed edges match intended call flow: PlayerTick -> frame work; InstantFire/ConsumeAmmo per swing; Behavior_CauseDamage per hit; TakeDamage -> guard/parry.",
        "check": "trace: edges table matches expectations; caller:null only for engine/native entry points.",
        "simplify_if_confirmed": "Call flow documentation accurate; hooks cover all real entry points.",
        "risk_if_refuted": "Some combat path reached via unhooked native call (invisible to hooks).",
    },
]

DESC_BY_ID = {a["id"]: a for a in ASSUMPTIONS}


def _prop_found(class_reports, class_path, prop):
    for entry in class_reports:
        if entry.get("path") == class_path:
            return any(p.get("name") == prop for p in (entry.get("wanted_found") or []))
    return False


def _fn_entry(functions, path):
    for f in functions:
        if f.get("path") == path:
            return f
    return None


def _obj_entry(objects, path):
    for o in objects:
        if o.get("path") == path:
            return o
    return None


def _trace_called(trace, callee):
    if trace is None:
        return False
    for entry in trace.get("wanted") or []:
        if entry.get("path") == callee and entry.get("found"):
            return int(entry.get("calls") or 0) > 0
    return False


def build_findings_report(parsed: dict[str, Any], dump_dir: str) -> dict[str, Any]:
    """Return {"markdown": str, "json": {...}} findings report."""
    functions = parsed.get("functions") or []
    classes = parsed.get("classes") or []
    enums = parsed.get("enums") or []
    objects = parsed.get("objects") or []
    defaults = parsed.get("defaults") or []
    trace = parsed.get("trace")

    findings = []

    for a in ASSUMPTIONS:
        aid = a["id"]
        cat = a["category"]
        status = "UNVERIFIED"
        evidence: list[str] = []

        if cat == "hooks":
            target_map = {
                "HOOK-INSTANTFIRE": "WillowGame.WillowWeapon:InstantFire",
                "HOOK-CONSUMEAMMO": "WillowGame.WillowWeapon:ConsumeAmmo",
                "HOOK-SHAREDFIRE": "WillowGame.WillowWeapon:SharedFireAmmunition",
                "HOOK-FX-FUNCS": "WillowGame.WillowWeapon:FiringModeDefinitionFire",
                "HOOK-HASACTIVEAMMO": "WillowGame.WillowWeapon:HasActiveAmmo",
                "HOOK-ITEMCARD": "WillowGame.ItemCardGFxObject:SetTopStat",
                "HOOK-SAVE-AND-MISSION": "WillowGame.WillowSaveGameManager:SaveGame",
            }
            if aid == "HOOK-PLAYERTICK":
                f = _fn_entry(functions, "WillowGame.WillowPlayerController:PlayerTick")
                g = _fn_entry(functions, "Engine.GameViewportClient:Tick")
                pt_called = _trace_called(trace, "WillowGame.WillowPlayerController:PlayerTick")
                gv_called = trace is not None and any(
                    e.get("path") == "Engine.GameViewportClient:Tick" and int(e.get("calls") or 0) > 0
                    for e in (trace.get("wanted") or [])
                )
                if f and f.get("found"):
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
                present = all(
                    _fn_entry(functions, x) and _fn_entry(functions, x).get("found") for x in fns
                )
                observed = trace is not None and all(_trace_called(trace, x) for x in fns)
                if present and observed:
                    status = "CONFIRMED"
                    evidence.append("all four zoom functions resolve and were observed called")
                elif present:
                    status = "PARTIAL"
                    evidence.append("all four resolve; not all observed in this trace session")
                else:
                    missing = [x for x in fns if not (_fn_entry(functions, x) and _fn_entry(functions, x).get("found"))]
                    status = "REFUTED"
                    evidence.append(f"missing from dump: {', '.join(missing)}")
            else:
                target = target_map.get(aid)
                f = _fn_entry(functions, target) if target else None
                if target and f and f.get("found"):
                    called = _trace_called(trace, target) if trace else None
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
                    bottom = _fn_entry(functions, "WillowGame.ItemCardGFxObject:SetBottomStat")
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
            found_p = [p for p in props if _prop_found(classes, class_path, p)]
            missing_p = [p for p in props if p not in found_p]
            if aid == "PROP-WEAPONNODECL":
                # Negative assertion: these names must NOT exist.
                if found_p:
                    status = "REFUTED"
                    evidence = [f"unexpectedly present on {class_path}: {', '.join(found_p)}"]
                else:
                    status = "CONFIRMED"
                    evidence = [f"none of {props} exist on {class_path} (negative assertion confirmed)"]
            elif not missing_p:
                status = "CONFIRMED"
                evidence.append(f"all {len(props)} properties present on {class_path} (declared or inherited)")
            elif found_p:
                status = "PARTIAL"
                evidence.append(f"{len(found_p)}/{len(props)} present; missing: {', '.join(missing_p)}")
            else:
                status = "REFUTED"
                evidence.append(f"none of {props} found on {class_path}")

        elif cat == "structs":
            target_map = {
                "STRUCT-DEFINITIONDATA": "WillowGame.WeaponDefinitionData",
                "STRUCT-WEIGHTEDROW": "WillowGame.WeightedInventoryBalanceData",
            }
            target = target_map[aid]
            entry = None
            for s in parsed.get("structs") or []:
                if s.get("path") == target:
                    entry = s
                    break
            present_fields = [p.get("name") for p in (entry.get("properties") or [])] if entry else []
            if aid == "STRUCT-DEFINITIONDATA":
                want = [
                    "WeaponTypeDefinition", "BalanceDefinition", "ManufacturerDefinition",
                    "ManufacturerGradeIndex", "BodyPartDefinition", "GripPartDefinition",
                    "BarrelPartDefinition", "ElementalPartDefinition", "Accessory1PartDefinition",
                    "Accessory2PartDefinition", "MaterialPartDefinition", "PrefixPartDefinition",
                    "TitlePartDefinition", "GameStage", "UniqueId",
                ]
            else:
                want = ["InvBalanceDefinition", "ItmPoolDefinition", "Probability", "Manufacturer", "DefaultWeightIndex", "MinGameStageIndex", "MaxGameStageIndex"]
            if entry is None:
                status = "REFUTED"
                evidence.append(f"{target} struct not in dump")
            else:
                missing = [p for p in want if p not in present_fields]
                if not missing:
                    status = "CONFIRMED"
                    evidence.append(f"{target} layout confirmed: {', '.join(want)}")
                else:
                    status = "PARTIAL"
                    evidence.append(f"{target} partial: missing {', '.join(missing)}")

        elif cat == "enums":
            rec = None
            for e in enums:
                if e.get("path") == "WillowGame.ETopStatArrow":
                    rec = e
                    break
            if not rec or not rec.get("found"):
                status = "REFUTED"
                evidence.append("ETopStatArrow enum not in dump")
            else:
                amap = {v["name"]: v["value"] for v in (rec.get("values") or [])}
                expected = {"ETopStatArrow__None": 0, "ETopStatArrow__Better": 1, "ETopStatArrow__Equal": 2, "ETopStatArrow__Worse": 3}
                if amap == expected:
                    status = "CONFIRMED"
                    evidence.append(f"byte mapping matches mod: {amap}")
                else:
                    status = "PARTIAL"
                    evidence.append(f"observed mapping {amap} vs assumed {expected}")

        elif cat == "objects":
            group = objects
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
            total = len(defaults)
            found = sum(1 for d in defaults if d.get("found"))
            if found == total and total:
                status = "CONFIRMED"
                evidence.append(f"all {total} wanted CDO defaults present")
            elif found:
                status = "PARTIAL"
                evidence.append(f"{found}/{total} wanted defaults present")
            else:
                status = "REFUTED"
                evidence.append("no wanted CDO defaults found")

        elif cat == "trace":
            status = "UNVERIFIED"
            evidence.append("trace not supplied — run `mrp_trace_arm ...` then `mrp_trace_stop`")

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

    json_report = {
        "dump_dir": dump_dir,
        "dump_id": parsed.get("dump_id"),
        "status": parsed.get("status"),
        "summary": summary,
        "findings": findings,
        "raw": {
            "functions_total": len(functions),
            "functions_found": sum(1 for f in functions if f.get("found")),
            "classes_total": len(classes),
            "objects_total": len(objects),
            "objects_found": sum(1 for o in objects if o.get("found")),
            "defaults_found": sum(1 for d in defaults if d.get("found")),
            "defaults_checked": len(defaults),
        },
    }

    lines = []
    lines.append(f"# {MOD_DISPLAY} — RE Probe findings")
    lines.append("")
    lines.append(f"- Dump: `{dump_dir}` (id: {parsed.get('dump_id')}, status: {parsed.get('status')})")
    lines.append("")
    lines.append("## Summary")
    for st in ("CONFIRMED", "PARTIAL", "REFUTED", "UNVERIFIED"):
        lines.append(f"- {st}: {summary.get(st, 0)}")
    lines.append(f"- Total assumptions checked: {len(findings)}")
    lines.append("")
    lines.append("## Assumption-by-assumption findings")
    lines.append("")
    lines.append("| id | category | status | evidence |")
    lines.append("|---|---|---|---|")
    for fnd in findings:
        ev = " / ".join(fnd["evidence"]) if fnd["evidence"] else "—"
        lines.append(f"| `{fnd['id']}` | {fnd['category']} | **{fnd['status']}** | {ev} |")
    lines.append("")
    lines.append("## Recommended simplifications")
    confirmed = [f for f in findings if f["status"] == "CONFIRMED"]
    partial = [f for f in findings if f["status"] == "PARTIAL"]
    refuted = [f for f in findings if f["status"] == "REFUTED"]
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
    lines.append("## Raw section summary")
    raw = json_report["raw"]
    lines.append(f"- Functions: {raw['functions_found']}/{raw['functions_total']} wanted found")
    lines.append(f"- Objects: {raw['objects_found']}/{raw['objects_total']} wanted found")
    lines.append(f"- Defaults: {raw['defaults_found']}/{raw['defaults_checked']} wanted present")

    markdown = "\n".join(lines)
    return {"markdown": markdown, "json": json_report}


MOD_DISPLAY = "Melee RE Probe Runner"

__all__ = ["build_findings_report", "ASSUMPTIONS"]
