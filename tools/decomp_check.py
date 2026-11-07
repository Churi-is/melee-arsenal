#!/usr/bin/env python3
"""Read a decom_bl2 output tree and answer melee-arsenal's static-only questions.

Stdlib only. Point it at the decom_bl2 `out/` directory (or straight at the
Ghidra `index.csv`) and it will:

  pseudo  list the functions whose pseudocode matters to this mod, grouped by
          why they matter, with the file each one lives in
  show    dump the pseudocode for one function (or a group of them)
  uc      pull the facts that only the recovered UnrealScript knows:
          exact function signatures, which functions are native, status-effect
          attribute names, rarity weight constants, part-selection entry points
  verify  re-run the evidence behind every recorded STATIC_FACTS entry
  schema  class/struct/enum layouts out of index.db (no JSON corpus needed)
          e.g. schema out TopStatData --body
               schema out WeaponDefinitionData
               schema out AttributeInitializationData
  find    just report where index.csv / pseudocode / script live

Covers the gaps in tools/re_probe_parse that a live reflection dump cannot
close:

  HOOK-SHAREDFIRE       does SharedFireAmmunition reach InstantFire?
  HOOK-ZOOM-QUAD        are all four zoom entry points real?
  HOOK-SAVE-AND-MISSION what does ValidateWeaponDefinition actually skip?
  STRUCT-WEIGHTEDROW    native ChooseRandomParts weight rule
  PROP-POOL / VALUE-*   real pool weights and rarity constants
  damage.py             the true TakeDamage parameter name (replaces the
                        five-name guess in _TAKE_DAMAGE_AMOUNT_FIELDS) and the
                        four-name guess in _STATUS_CHANCE_FIELDS

Usage:
  python3 decomp_check.py find   ~/Downloads/decom_bl2-1.3.2/out
  python3 decomp_check.py pseudo ~/Downloads/decom_bl2-1.3.2/out
  python3 decomp_check.py show   ~/Downloads/decom_bl2-1.3.2/out TakeDamage
  python3 decomp_check.py uc     ~/Downloads/decom_bl2-1.3.2/out signature TakeDamage
  python3 decomp_check.py uc     ~/Downloads/decom_bl2-1.3.2/out native WillowWeapon
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

MAX_ROWS = 40
MAX_SHOW_LINES = 300

# Why do we care? Group -> function-name substrings (case-insensitive).
GROUPS: dict[str, tuple[str, ...]] = {
    # The block-and-recall in runtime/combat/damage.py: is re-entering
    # TakeDamage safe, and where is the cleaner interception point?
    "damage": (
        "TakeDamage",
        "DisplayRecentDamage",
        "CauseDamage",
        "AdjustDamage",
        "ModifyDamage",
        "ApplyDamage",
    ),
    # STRUCT-WEIGHTEDROW: "native ChooseRandomParts substitutes flat weight 100
    # when Manufacturers is empty" -- and the out-of-range grade path.
    "parts": (
        "ChooseRandomParts",
        "ChooseParts",
        "InitializeParts",
        "BuildParts",
        "ApplyPartList",
    ),
    # core/stats.py apply_bl2_modifiers reimplements PreAdd -> Scale -> PostAdd,
    # and GUN_DAMAGE_L0_CONSTANT / BL2_LEVEL_GROWTH are guesses.
    "attributes": (
        "EvaluateExpression",
        "AttributeInit",
        "AttributeExpression",
        "EvaluateAttribute",
        "GetAttributeValue",
        "ApplyAttribute",
    ),
    "hooks": (
        "InstantFire",
        "SharedFireAmmunition",
        "ConsumeAmmo",
        "HasActiveAmmo",
        "StartZoom",
        "StopZoom",
        "ValidateWeaponDefinition",
        "ApplyWeaponSaveGameData",
        "ServerGrantMissionRewards",
        "SetItemCard",
        "SetBottomStat",
        "PlaceSpawnedItems",
        "ResetInventory",
    ),
}

# Facts the .uc tree can answer directly. name -> regex over the script tree.
UC_CHECKS: dict[str, str] = {
    # kills the five-name guess in damage.py:_TAKE_DAMAGE_AMOUNT_FIELDS
    # prefix match: "TakeDamage" also finds TakeDamageInternal, which is usually
    # where the real work (and the clean interception point) lives
    "signature": r"function\s+{pat}[A-Za-z0-9_]*\s*\(",
    # Which hooked functions are native (pseudocode) vs script (.uc body)?
    # decom_bl2 marks natives with a trailing comment, e.g.
    #   function bool ValidateWeaponDefinition(WeaponDefinitionData DefinitionData)  // native
    # not with a leading `native` keyword.
    "native": r"function\s+[^()=]*?\b{pat}[A-Za-z0-9_]*\s*\([^)]*\)\s*//\s*native",
    # kills the four-name guess in damage.py:_STATUS_CHANCE_FIELDS
    "status-chance": r"\b{pat}[A-Za-z]*",
    "weights": r"\b{pat}\b",
    "parts": r"{pat}",
    # substring match: StartZoom and ClientStartZoom both matter (HOOK-ZOOM-QUAD)
    "zoom": r"function\s+[A-Za-z0-9_]*{pat}[A-Za-z0-9_]*\s*\(",
    "itemcard": r"function\s+[A-Za-z0-9_]*{pat}[A-Za-z0-9_]*\s*\(",
}

# --------------------------------------------------------------------------- #
# Static findings: what the decom_bl2 corpus settled about melee-arsenal.
#
# Same shape as tools/re_probe_parse/assumptions.py so the two can be merged
# into one FINDINGS report later. `status` is the conclusion; `check` records
# the evidence, and `verify` re-runs it so a stale conclusion is caught when
# the decomp is rebuilt against a different install.
#
# check kinds:
#   {"cmd": "uc",    "what": <UC_CHECKS key>, "pattern": str, "expect": present|absent}
#   {"cmd": "raw",   "pattern": <regex>,                      "expect": present|absent}
#   {"cmd": "file",  "file": <basename>, "pattern": <regex>,  "expect": present|absent}
#   {"cmd": "body",  "pattern": str,                          "expect": present|absent}
#   None  -> informational, no mechanical check possible
# --------------------------------------------------------------------------- #
STATIC_FACTS: list[dict[str, object]] = [
    {
        "id": "STATIC-ROID-PATH",
        "category": "properties",
        "mod": "runtime/resources.py ATTR_ROID_BUFF",
        "assumption": "D_Attributes.DamageSourceModifiers.InstigatedMeleeDamageAddedBuff exists.",
        "status": "REFUTED",
        "evidence": (
            "No property named *InstigatedMeleeDamageAddedBuff* in 2,029 packages. "
            "WillowPawn.uc:187-189 declares InstigatedMeleeDamageAdded with the "
            "ModifierStack/BaseValue/value attribute triple."
        ),
        "action": (
            "Rename to ...InstigatedMeleeDamageAdded. Roid currently works only "
            "because _safe_find returns None and damage.py:124 falls back to "
            "reading that property off the pawn."
        ),
        "check": {"cmd": "uc", "what": "parts", "pattern": "InstigatedMeleeDamageAddedBuff",
                  "expect": "absent"},
    },
    {
        "id": "STATIC-TAKEDAMAGE-PARAM",
        "category": "properties",
        "mod": "runtime/combat/damage.py _TAKE_DAMAGE_AMOUNT_FIELDS",
        "assumption": "The incoming-damage float is called Damage on the pawn classes we hook.",
        "status": "CONFIRMED",
        "evidence": (
            "WillowPlayerPawn.uc:3268 and WillowPawn.uc:5284 both declare "
            "'float Damage'. DamageAmount/RawDamage belong to other classes; "
            "InDamage and BaseDamage appear nowhere in the game."
        ),
        "action": "Drop InDamage and BaseDamage from the five-name guess.",
        "check": {"cmd": "file", "file": "WillowPlayerPawn.uc",
                  "pattern": r"function TakeDamage\(.*float Damage\)", "expect": "present"},
    },
    {
        "id": "STATIC-HOOK-SHAREDFIRE",
        "category": "hooks",
        "mod": "assumptions.py HOOK-SHAREDFIRE",
        "assumption": "SharedFireAmmunition reaches InstantFire.",
        "status": "CONFIRMED",
        "evidence": (
            "WillowWeapon.uc:4287 SharedFireAmmunition() calls "
            "ConsumeAmmo(CurrentFireMode) then InstantFire() -- in that order."
        ),
        "action": (
            "Assumption held. ConsumeAmmo runs before InstantFire, so the POST "
            "refill lands before the (blocked) swing."
        ),
        "check": {"cmd": "body", "pattern": "SharedFireAmmunition", "expect": "present"},
    },
    {
        "id": "STATIC-HOOK-ITEMCARD",
        "category": "hooks",
        "mod": "assumptions.py HOOK-ITEMCARD",
        "assumption": "SetItemCard/SetItemCardEx/SetTitle/SetTopStat/SetFunStats resolve; SetBottomStat does not.",
        "status": "CONFIRMED",
        "evidence": "ItemCardGFxObject.uc declares 14 Set* functions; SetBottomStat is not among them.",
        "action": "Hook set is exactly right.",
        "check": {"cmd": "file", "file": "ItemCardGFxObject.uc",
                  "pattern": r"function SetBottomStat", "expect": "absent"},
    },
    {
        "id": "STATIC-PROP-WEAPONWRITES",
        "category": "properties",
        "mod": "runtime/resolver.py sync_live_weapon_properties",
        "assumption": "All 16 written properties exist (declared or inherited).",
        "status": "CONFIRMED",
        "evidence": (
            "StatusEffect*/StatusEffectChance* (WillowWeapon.uc:117-128), ReloadCnt/ClipSize*/"
            "ShotCost* (WillowWeapon.uc:180-193), and InstantHitDamage*/Spread*/FireInterval* "
            "on Engine.Weapon.uc:37-45."
        ),
        "action": "hasattr guards always pass and can be dropped (the try/except already covers failures).",
        "check": {"cmd": "file", "file": "Weapon.uc",
                  "pattern": r"var floatattribute FireInterval;", "expect": "present"},
    },
    {
        "id": "STATIC-FFYL-TAKEDAMAGE",
        "category": "hooks",
        "mod": "runtime/combat/damage.py on_player_take_damage_pre",
        "assumption": "TakeDamage fires while the player is in state injured (FFYL).",
        "status": "NO-IMPACT",
        "evidence": (
            "WillowPlayerPawn.uc:3500 overrides TakeDamage inside state injured. It never "
            "calls super and never touches health -- it only calls ClientDamageShake(Damage). "
            "No damage is applied in FFYL."
        ),
        "action": (
            "Withdrawn: nothing to reduce, and guard cannot be active (no RMB while downed). "
            "Residual: if the mod's hook binds to the state function, camera shake while "
            "downed is scaled by the blade's DR percentage -- cosmetic only."
        ),
        "check": None,
    },
    {
        "id": "STATIC-ATTR-CONTEXT",
        "category": "values",
        "mod": "runtime/combat/damage.py _eval_attr",
        "assumption": "attr_obj.GetValue(pawn) resolves for the four D_Attributes.DamageSourceModifiers paths.",
        "status": "UNVERIFIED",
        "evidence": (
            "All four attributes are declared on Engine.Controller (Controller.uc:86-118), "
            "not on any Pawn. StatusEffectChanceModifierResolver::GetAttributeContext and "
            "WillowPawn::GetAttributeContextSource are both native, so the .uc cannot say "
            "whether the resolver walks Pawn -> Controller."
        ),
        "action": (
            "Settle in game, not in Ghidra: take a known melee-damage bonus, enable Log Swing "
            "Damage Breakdown, swing, and read melee_damage_scale. A flat 1.0 means the read is broken."
        ),
        "check": {"cmd": "uc", "what": "native", "pattern": "GetAttributeContextSource",
                  "expect": "present"},
    },
    {
        "id": "STATIC-ENUM-ARROW",
        "category": "enums",
        "mod": "runtime/ui_item_card.py _ARROW_BYTES / core/item_card.py CompareArrow",
        "assumption": "ETopStatArrow is ETSA_Blank=0, ETSA_Up=1, ETSA_Down=2, ETSA_Same=3.",
        "status": "CONFIRMED",
        "evidence": (
            "index.db schemas body for WillowGame.InventoryCardGFx.ETopStatArrow: "
            '{"values": ["ETSA_Blank", "ETSA_Up", "ETSA_Down", "ETSA_Same", "ETSA_MAX"]} '
            "-- so 0/1/2/3, with ETSA_MAX=4 as the sentinel."
        ),
        "action": "Byte mapping is exact; no change.",
        "check": {"cmd": "db", "pattern": "ETopStatArrow", "kind": "Enum", "expect": "present"},
    },
    {
        "id": "STATIC-STRUCT-DEFINITIONDATA",
        "category": "structs",
        "mod": "runtime/resolver.py apply_instance_to_definition_data",
        "assumption": (
            "WeaponDefinitionData has all 17 named fields: WeaponTypeDefinition, BalanceDefinition, "
            "ManufacturerDefinition, ManufacturerGradeIndex, BodyPartDefinition, GripPartDefinition, "
            "BarrelPartDefinition, SightPartDefinition, StockPartDefinition, ElementalPartDefinition, "
            "Accessory1PartDefinition, Accessory2PartDefinition, MaterialPartDefinition, "
            "PrefixPartDefinition, TitlePartDefinition, GameStage, UniqueId."
        ),
        "status": "CONFIRMED",
        "evidence": (
            "index.db WillowGame.WillowWeaponTypes.WeaponDefinitionData lists exactly those 17 names. "
            "The index order is the serialized order (reverse of the assumption's) and the code writes "
            "by name, so order is irrelevant."
        ),
        "action": "No change; the setattr chain is safe.",
        "check": {"cmd": "db", "pattern": "WeaponDefinitionData", "kind": "ScriptStruct",
                  "expect": "present"},
    },
    {
        "id": "STATIC-STRUCT-WEIGHTEDROW-NAME",
        "category": "structs",
        "mod": "runtime/definitions/balances.py:135 unrealsdk.make_struct",
        "assumption": "The manufacturer-weight struct is named ManufacturerCustomPartWeightData.",
        "status": "REFUTED",
        "evidence": (
            "No schema named *ManufacturerCustomPartWeightData* in index.db (3,338 classes, 1,338 "
            "structs, 792 enums searched). make_struct with that name raises, is swallowed by the "
            "except, and leaves mfr_weight_one = None."
        ),
        "action": (
            "Latent, not live: the copy-from-donor-template path above it normally succeeds. But when "
            "the template is missing, row.Manufacturers ends up empty -- which is precisely the case "
            "where native ChooseRandomParts substitutes flat weight 100, the failure this code exists "
            "to prevent. Find the real struct name and fix make_struct."
        ),
        "check": {"cmd": "db", "pattern": "ManufacturerCustomPartWeightData", "expect": "absent"},
    },
    {
        "id": "STATIC-PROP-POOL",
        "category": "properties",
        "mod": "runtime/loot_pools.py",
        "assumption": (
            "ItemPoolDefinition declares BalancedItems/Quantity/MinGameStageRequirement/"
            "MaxGameStageRequirement/bAutoReadyItems/bShopsHaveInfiniteQuantity and no ItemPool property."
        ),
        "status": "CONFIRMED",
        "evidence": (
            "index.db WillowGame.ItemPoolDefinition declares all six plus four more "
            "(bEligibleForUncommonWeightMultiplier, bSupportsGameStageVariance, bDisablePhysicsDrop, "
            "CheatSpawnLevel). Min/MaxGameStageRequirement are ObjectProperty, not ints -- and "
            "loot_pools.py:204-205 sets them to None, which is correct for an object reference."
        ),
        "action": (
            "No change. Note CrossDLCItemPoolDefinition extends it and stores pool references as "
            "strings (BalancedInventoryDataMirror.ObjectPath), so check whether DLC maps route drops "
            "through that class -- object-reference injection would not reach it."
        ),
        "check": {"cmd": "db", "pattern": "ItemPoolDefinition", "kind": "Class", "expect": "present"},
    },
    {
        "id": "STATIC-WEIGHTS-NEED-JSON",
        "category": "values",
        "mod": "runtime/loot_pools.py _KNOWN_WEIGHT_INIT_DEFS, VANILLA_WEAPON_CLASS_SUM",
        "assumption": "The .uc corpus can confirm the rarity weights and pool weight totals.",
        "status": "UNVERIFIED",
        "evidence": (
            "grep for 'GD_Balance.Weighting' across out/script returns nothing: probabilities "
            "live in nested BalancedItems[] arrays, which decom_bl2 leaves as hex without JSON."
        ),
        "action": (
            "Either re-run the affected packages with --emit json, or read them live with "
            "'reprobe dump GD_Itempools.WeaponPools.Pool_Weapons_All 2' (minutes, not hours)."
        ),
        "check": {"cmd": "raw", "pattern": r"GD_Balance\.Weighting", "expect": "absent"},
    },
    {
        "id": "STATIC-DAMAGEADJUSTER-DEAD",
        "category": "hooks",
        "mod": "runtime/combat/damage.py (possible no-hook DR route)",
        "assumption": "Engine.DamageAdjuster is a pluggable damage-modifier chain the mod could register into.",
        "status": "REFUTED",
        "evidence": "Two hits in 2,029 packages, both in its own class file. Nothing constructs or iterates it.",
        "action": "Dead end -- DR stays a hook.",
        # only self-references: the class file mentions it twice, nothing else does
        "check": {"cmd": "raw", "pattern": r"DamageAdjuster", "expect": "present", "max": 4},
    },
]


STATUS_CHANCE_NAMES = (
    "BaseStatusEffectChanceModifier",
    "StatusEffectChanceModifier",
    "StatusEffectDamage",
    "InstigatedMeleeDamageAdded",
    "MeleeDamage",
    "MeleeModifier",
)

WEIGHT_NAMES = (
    "Weight_0_VeryCommon",
    "Weight_1_Common",
    "Weight_2_Uncommon",
    "Weight_3_Uncommoner",
    "Weight_4_Rare",
    "Weight_5_VeryRare",
    "Weight_6_Legendary",
)


# --------------------------------------------------------------------------- #
# locating the artifacts
# --------------------------------------------------------------------------- #
def _walk(root: Path, pattern: str) -> list[Path]:
    hits: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
        for fn in filenames:
            if fn == pattern or fn.endswith(pattern):
                hits.append(Path(dirpath) / fn)
    return sorted(hits)


def find_artifacts(root: Path) -> dict[str, object]:
    csvs = [p for p in _walk(root, "index.csv")]
    pseudo_dirs = sorted({p.parent for p in _walk(root, ".c")})
    if not pseudo_dirs:
        pseudo_dirs = sorted({p.parent for p in _walk(root, ".c.txt")})
    return {
        "root": str(root),
        "index_csv": [str(p) for p in csvs],
        "pseudocode_dirs": [str(p) for p in pseudo_dirs],
        "script_dir": str(root / "script") if (root / "script").is_dir() else None,
        "json_dir": str(root / "json") if (root / "json").is_dir() else None,
        "index_db": str(root / "index.db") if (root / "index.db").is_file() else None,
    }


def _pick_index_csv(root: Path) -> Path | None:
    csvs = [p for p in _walk(root, "index.csv")]
    if not csvs:
        return None
    for p in csvs:
        if "ghidra" in str(p).lower():
            return p
    # prefer the one with the most rows
    return max(csvs, key=lambda p: sum(1 for _ in p.open("r", errors="replace")))


# --------------------------------------------------------------------------- #
# index.csv reading
# --------------------------------------------------------------------------- #
_NAME_KEYS = ("name", "symbol", "function", "label")
_FILE_KEYS = ("file", "path", "output", "pseudo")
_ADDR_KEYS = ("address", "addr", "va", "ea", "entry", "offset")


def _col(fields: list[str], keys: tuple[str, ...]) -> str | None:
    for f in fields:
        low = f.strip().lower()
        if any(k in low for k in keys):
            return f
    return None


def read_index(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", newline="", errors="replace") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
        fh.seek(0)
        if has_header:
            reader = csv.DictReader(fh)
            fields = [f for f in (reader.fieldnames or [])]
            c_name = _col(fields, _NAME_KEYS)
            c_file = _col(fields, _FILE_KEYS)
            c_addr = _col(fields, _ADDR_KEYS)
            rows = []
            for row in reader:
                rows.append(
                    {
                        "name": (row.get(c_name) or "").strip() if c_name else "",
                        "file": (row.get(c_file) or "").strip() if c_file else "",
                        "addr": (row.get(c_addr) or "").strip() if c_addr else "",
                    }
                )
            return [r for r in rows if r["name"]]
        # headerless: assume name,addr,file (the order the export script writes)
        rows = []
        for parts in csv.reader(fh):
            if not parts:
                continue
            rows.append(
                {
                    "name": parts[0].strip(),
                    "addr": parts[1].strip() if len(parts) > 1 else "",
                    "file": parts[2].strip() if len(parts) > 2 else "",
                }
            )
        return [r for r in rows if r["name"]]


def _resolve_file(row: dict[str, str], csv_path: Path) -> Path | None:
    raw = row["file"]
    if not raw:
        return None
    cand = Path(raw)
    if cand.is_file():
        return cand
    for base in (csv_path.parent, csv_path.parent.parent, Path.cwd()):
        alt = base / raw
        if alt.is_file():
            return alt
    # last resort: match by basename anywhere under the csv's parent
    for p in _walk(csv_path.parent, cand.name):
        return p
    return None


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    low = name.lower()
    return any(p.lower() in low for p in patterns)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_find(args: argparse.Namespace) -> int:
    info = find_artifacts(args.out)
    print(f"root: {info['root']}")
    for key in ("index_db", "index_csv", "pseudocode_dirs", "script_dir", "json_dir"):
        val = info[key]
        if not val:
            print(f"{key:16} (none found)")
        elif isinstance(val, list):
            print(f"{key:16} {len(val)} found")
            for v in val[:5]:
                print(f"{'':16}   {v}")
        else:
            print(f"{key:16} {val}")
    return 0


def cmd_pseudo(args: argparse.Namespace) -> int:
    csv_path = _pick_index_csv(args.out)
    if csv_path is None:
        print("no index.csv under that path -- run `find` first, or point me at the file", file=sys.stderr)
        return 2
    rows = read_index(csv_path)
    groups = [args.group] if args.group else list(GROUPS)
    total = 0
    for g in groups:
        pats = GROUPS.get(g, (args.group,))
        hits = [r for r in rows if _matches(r["name"], pats)]
        if not hits and args.group:
            hits = [r for r in rows if _matches(r["name"], (args.group,))]
        print(f"\n== {g} ({len(hits)} hit(s)) ==")
        for r in hits[:MAX_ROWS]:
            resolved = _resolve_file(r, csv_path)
            where = str(resolved) if resolved else (r["file"] or "(no file column)")
            print(f"  {r['addr']:>12}  {r['name']:<44} {where}")
            total += 1
        if len(hits) > MAX_ROWS:
            print(f"  ... {len(hits) - MAX_ROWS} more")
    print(f"\n{csv_path}  ({len(rows)} rows, {total} listed)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    csv_path = _pick_index_csv(args.out)
    if csv_path is None:
        print("no index.csv under that path", file=sys.stderr)
        return 2
    rows = read_index(csv_path)
    pats = GROUPS.get(args.pattern, (args.pattern,))
    hits = [r for r in rows if _matches(r["name"], pats)]
    if not hits:
        print(f"nothing matched {args.pattern!r}")
        return 1
    for r in hits[: args.limit]:
        path = _resolve_file(r, csv_path)
        print(f"\n===== {r['name']}  @ {r['addr']}  ({path or 'file not found'}) =====")
        if path is None:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            print(f"  cannot read: {exc}")
            continue
        lines = text.splitlines()
        for line in lines[:MAX_SHOW_LINES]:
            print(line)
        if len(lines) > MAX_SHOW_LINES:
            print(f"  ... {len(lines) - MAX_SHOW_LINES} more lines (truncated)")
    return 0


def _extract_body(lines: list[str], start_idx: int, max_lines: int) -> list[str]:
    """Return a function body starting at `start_idx`, brace-balanced."""
    depth = 0
    opened = False
    out: list[str] = []
    for line in lines[start_idx : start_idx + max_lines]:
        out.append(line)
        depth += line.count("{") - line.count("}")
        if "{" in line:
            opened = True
        if opened and depth <= 0:
            break
    if opened and depth > 0:
        out.append(f"    // ... unbalanced at {max_lines}-line cap")
    return out


def cmd_body(args: argparse.Namespace) -> int:
    """Print the recovered UnrealScript body of a function, from every .uc that declares it.

    This is the static counterpart to `reprobe trace`: where the trace can only show
    edges that happened to fire, the body shows every branch that *can* fire.
    """
    script_root = args.out / "script"
    if not script_root.is_dir():
        script_root = args.out
    rx = _body_decl_rx(args.pattern)
    found = 0
    for path in _walk(script_root, ".uc"):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            found += 1
            if found > args.limit:
                print(f"  ... more matches suppressed (--limit {args.limit})")
                return 0
            print(f"\n===== {path}:{i + 1} =====")
            for bl in _extract_body(lines, i, args.max_lines):
                print(bl)
    if not found:
        print(f"no function matching {args.pattern!r} in {script_root}")
        return 1
    return 0


def _script_root(out: Path) -> Path:
    return out / "script" if (out / "script").is_dir() else out


def _body_decl_rx(pattern: str) -> re.Pattern:
    # [^()]*? covers the optional return type: `function bool SharedFireAmmunition(`
    return re.compile(r"function\s+[^()]*?\b" + re.escape(pattern) + r"[A-Za-z0-9_]*\s*\(", re.I)


def _count_check(out: Path, check: dict[str, object] | None) -> int | None:
    """Re-run one fact's check; return the hit count, or None if uncheckable."""
    if not check:
        return None
    root = _script_root(out)
    cmd = check.get("cmd")
    if cmd == "uc":
        what = str(check.get("what", "parts"))
        rx = UC_CHECKS.get(what, r"{pat}").format(pat=re.escape(str(check.get("pattern", ""))))
        return len(_grep(root, rx, 5000))
    if cmd == "raw":
        return len(_grep(root, str(check.get("pattern", "")), 5000))
    if cmd == "file":
        want = str(check.get("file", ""))
        rx = re.compile(str(check.get("pattern", "")))
        hits = 0
        for path in _walk(root, ".uc"):
            if path.name != want:
                continue
            try:
                hits += sum(1 for line in path.read_text(errors="replace").splitlines() if rx.search(line))
            except OSError:
                continue
        return hits
    if cmd == "db":
        db_path = out / "index.db"
        if not db_path.is_file():
            found = _walk(out, "index.db")
            if not found:
                return None
            db_path = found[0]
        rows = _schema_rows(
            db_path, str(check.get("pattern", "")), check.get("kind"),  # type: ignore[arg-type]
            int(check.get("limit", 50)),
        )
        return len(rows)
    if cmd == "body":
        rx = _body_decl_rx(str(check.get("pattern", "")))
        hits = 0
        for path in _walk(root, ".uc"):
            try:
                hits += sum(
                    1 for line in path.read_text(errors="replace").splitlines() if rx.search(line)
                )
            except OSError:
                continue
        return hits
    return None


def _schema_rows(db_path: Path, pattern: str, kind: str | None, limit: int) -> list[dict[str, object]]:
    """Read class/struct/enum layouts straight out of the pass-1 index.

    index.db is built regardless of --emit, so this answers the struct and enum
    assumptions without the multi-GB JSON corpus.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cols = [r[1] for r in conn.execute("pragma table_info(schemas)")]
    like = f"%{pattern}%"
    sql = f"select {', '.join(cols)} from schemas where (path like ? or name like ?)"
    params: list[object] = [like, like]
    if kind:
        sql += " and kind = ?"
        params.append(kind)
    sql += " order by kind, path limit ?"
    params.append(limit)
    out: list[dict[str, object]] = []
    for row in conn.execute(sql, params):
        d = dict(zip(cols, row))
        d["_raw_fields"] = d.get("fields")
        try:
            d["fields"] = json.loads(d.get("fields") or "[]")
        except Exception:
            d["fields"] = []
        out.append(d)
    conn.close()
    return out


def cmd_schema(args: argparse.Namespace) -> int:
    db_path = args.out / "index.db"
    if not db_path.is_file():
        found = _walk(args.out, "index.db")
        if not found:
            print(f"no index.db under {args.out} -- run the index pass first", file=sys.stderr)
            return 2
        db_path = found[0]
    rows = _schema_rows(db_path, args.pattern, args.kind, args.limit)
    if not rows:
        print(f"no schema matching {args.pattern!r}" + (f" (kind={args.kind})" if args.kind else ""))
        return 1
    for d in rows:
        print(f"\n{d['kind']} {d['path']}   (package={d['package']}, super={d['super']})")
        for f in d["fields"] or []:
            if not isinstance(f, dict):
                print(f"    {f}")
                continue
            extra = f"  enum={f['enum']}" if f.get("enum") else ""
            flags = ",".join(f.get("flag_names") or []) if f.get("flag_names") else ""
            print(f"    {str(f.get('name')):<38} {str(f.get('type'))}{extra}{'  [' + flags + ']' if flags else ''}")
        body = (d.get("body") or "").strip()
        if body and args.body:
            print(f"    --- body ({len(body)} chars) ---")
            for line in body.splitlines()[:60]:
                print(f"    {line}")
    print(f"\n{len(rows)} row(s) from {db_path}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-run every static fact's evidence against this decomp tree.

    Reports whether the recorded evidence still holds -- a conclusion is only as
    good as the evidence behind it, and the decomp gets rebuilt.
    """
    if not any(_walk(_script_root(args.out), ".uc")):
        print(f"no .uc files under {_script_root(args.out)}", file=sys.stderr)
        return 2
    stale = 0
    for fact in STATIC_FACTS:
        check = fact.get("check")
        count = _count_check(args.out, check)  # type: ignore[arg-type]
        status = str(fact["status"])
        if count is None:
            verdict = "no mechanical check"
        else:
            expect = str((check or {}).get("expect", "present"))
            cap = (check or {}).get("max")
            if cap is not None:
                holds = count <= int(cap)
            elif expect == "absent":
                holds = count == 0
            else:
                holds = count > 0
            verdict = f"evidence holds ({count} hit(s))" if holds else f"STALE ({count} hit(s))"
            stale += 0 if holds else 1
        print(f"{fact['id']:<32} {status:<12} {verdict}")
        if args.verbose:
            print(f"    mod: {fact['mod']}")
            print(f"    {fact['evidence']}")
            print(f"    -> {fact['action']}")
    print(f"\n{len(STATIC_FACTS)} static facts, {stale} with stale evidence")
    return 1 if stale else 0


def _grep(root: Path, pattern: str, limit: int) -> list[tuple[str, int, str]]:
    rx = re.compile(pattern)
    out: list[tuple[str, int, str]] = []
    for path in _walk(root, ".uc"):
        try:
            for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    out.append((str(path), i, line.strip()))
                    if len(out) >= limit:
                        return out
        except OSError:
            continue
    return out


def cmd_uc(args: argparse.Namespace) -> int:
    script_root = args.out / "script"
    if not script_root.is_dir():
        script_root = args.out
    if not any(_walk(script_root, ".uc")):
        print(f"no .uc files under {script_root}", file=sys.stderr)
        return 2

    if args.what == "status-chance":
        for name in STATUS_CHANCE_NAMES:
            hits = _grep(script_root, UC_CHECKS["status-chance"].format(pat=re.escape(name)), args.limit)
            print(f"\n-- {name} ({len(hits)} hit(s)) --")
            for f, i, line in hits:
                print(f"  {f}:{i}: {line}")
        return 0

    if args.what == "weights":
        for name in WEIGHT_NAMES:
            hits = _grep(script_root, UC_CHECKS["weights"].format(pat=re.escape(name)), args.limit)
            print(f"\n-- {name} ({len(hits)} hit(s)) --")
            for f, i, line in hits:
                print(f"  {f}:{i}: {line}")
        return 0

    if args.what == "native":
        hits = _grep(script_root, UC_CHECKS["native"].format(pat=re.escape(args.pattern)), args.limit)
        print(f"\n-- native functions matching {args.pattern!r} --")
        for f, i, line in hits:
            print(f"  {f}:{i}: {line.strip()}")
        return 0

    pattern = UC_CHECKS.get(args.what, r"{pat}").format(pat=re.escape(args.pattern))
    hits = _grep(script_root, pattern, args.limit)
    print(f"-- {args.what} / {args.pattern!r} ({len(hits)} hit(s)) --")
    for f, i, line in hits:
        print(f"  {f}:{i}: {line}")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="decomp_check", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("find", help="locate index.csv / pseudocode / script in a decom_bl2 out/")
    p.add_argument("out", type=Path)
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("pseudo", help="list pseudocode that matters, grouped by why")
    p.add_argument("out", type=Path)
    p.add_argument("group", nargs="?", help="one group or a bare function-name substring")
    p.set_defaults(func=cmd_pseudo)

    p = sub.add_parser("show", help="dump pseudocode for a function or group")
    p.add_argument("out", type=Path)
    p.add_argument("pattern", help="function name substring, or a group name")
    p.add_argument("--limit", type=int, default=4)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("uc", help="grep facts out of the recovered UnrealScript")
    p.add_argument("out", type=Path)
    p.add_argument("what", choices=sorted(UC_CHECKS))
    p.add_argument("pattern", nargs="?", default="", help="function/property name to look for")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_uc)

    p = sub.add_parser("schema", help="class/struct/enum layouts from out/index.db")
    p.add_argument("out", type=Path)
    p.add_argument("pattern", help="path or name substring, e.g. TopStatData, WeaponDefinitionData")
    p.add_argument("--kind", choices=("Class", "ScriptStruct", "Enum", "State"))
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--body", action="store_true", help="also print the body column (enum members?)")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("verify", help="re-check the recorded static findings against this tree")
    p.add_argument("out", type=Path)
    p.add_argument("-v", "--verbose", action="store_true", help="print evidence and action per fact")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("body", help="print a function's recovered UnrealScript body")
    p.add_argument("out", type=Path)
    p.add_argument("pattern", help="function name (substring), e.g. SharedFireAmmunition")
    p.add_argument("--limit", type=int, default=6, help="max declarations to print")
    p.add_argument("--max-lines", type=int, default=150, help="max lines per body")
    p.set_defaults(func=cmd_body)

    args = ap.parse_args(argv)
    if not args.out.is_dir():
        print(f"not a directory: {args.out}", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
