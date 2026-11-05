"""Orchestration: run the probe sections, write `ground_truth.json`, and render a review sheet.

`TARGETS` is the other half of the probe: for every section it names the exact runtime symbols the
answer would let us delete or collapse. That keeps the probe honest — a section that resolves no
code is a section we should not have written.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import assets, census, live, schema
from .common import JSON_PATH, REPORT_PATH, SCHEMA_VERSION, log, write_json


# section key -> runtime symbols it unblocks. `saves` is a deliberately conservative line count.
TARGETS: list[dict[str, Any]] = [
    {
        "section": "schema.functions",
        "question": "Which hook targets are real UFunctions, and what are their parameter names?",
        "saves": 60,
        "resolves": [
            "runtime/__init__.py HOOKS: dead registrations (incl. Engine.GameViewportClient:Tick)",
            "runtime/__init__.py getattr(args, ...) parameter probing across 12 hook callbacks",
            "runtime/resolver.py on_weapon_initialize_internal_pre/post bisection",
        ],
    },
    {
        "section": "census.groups",
        "question": "Which of the paired hooks actually fire, in what order, and how often?",
        "saves": 45,
        "resolves": [
            "runtime/__init__.py _run_frame_tick per-frame de-duplication guard",
            "runtime/__init__.py StartZoom + ClientStartZoom / StopZoom + ClientStopZoom pairs",
            "runtime/ui_item_card.py depth counting between SetItemCard and SetItemCardEx",
        ],
    },
    {
        "section": "schema.classes",
        "question": "Which properties exist on each class, and what are their class-default values?",
        "saves": 560,
        "resolves": [
            "runtime/definitions/balances.py 230 lines of `if hasattr(...): try: setattr`",
            "runtime/definitions/name_parts.py 191 lines of the same shape",
            "runtime/definitions/weapon_types.py 133 lines of the same shape",
            "runtime/loot_pools.py _sanitize_pool_object hasattr cluster",
            "runtime/definitions/helpers.py _clear_array_prop / _zero_vector_struct guessers",
        ],
    },
    {
        "section": "schema.enums",
        "question": "What are the integer values of the enums passed into engine functions?",
        "saves": 45,
        "resolves": [
            "runtime/ui_item_card.py _discover_compare_enum substring matching",
            "runtime/ui_item_card.py _arrow_byte fallback map",
        ],
    },
    {
        "section": "schema.structs",
        "question": "What is the field layout of the structs the runtime fills in blind?",
        "saves": 40,
        "resolves": [
            "runtime/loot_pools.py AttributeInitializationData field probing",
            "runtime/resolver.py _apply_behavior_to_context / _set_spawner_custom_location reflection",
        ],
    },
    {
        "section": "assets.catalogues",
        "question": "What exact ParticleSystem / AkEvent / CameraAnim / Impact paths are loaded?",
        "saves": 190,
        "resolves": [
            "runtime/resources.py _score_explosion_ps_path + _score_explosion_ak_path keyword scoring",
            "runtime/resources.py _cache_torgue_explosion_assets find_all fallback scan",
            "runtime/resources.py multi-candidate `or _safe_find(...)` chains",
            "runtime/secondary_vfx.py _fallback_material slot probing",
        ],
    },
    {
        "section": "behaviours.explosion_donors",
        "question": "Which firing mode actually carries a Behavior_Explode we can clone?",
        "saves": 40,
        "resolves": [
            "runtime/definitions/firing_modes.py _attach_torgue_explode_on_impact donor scan",
        ],
    },
    {
        "section": "behaviours.attribute_init_defs",
        "question": "What is the real weight constant behind each AttributeInitializationDefinition?",
        "saves": 60,
        "resolves": [
            "runtime/loot_pools.py _KNOWN_WEIGHT_INIT_DEFS name matching",
            "runtime/loot_pools.py _eval_prob_weight ValueFormula fallback chain",
        ],
    },
    {
        "section": "pools",
        "question": "Which weapon pools exist, with what entries, gates and quantities?",
        "saves": 50,
        "resolves": [
            "runtime/loot_pools.py _DONOR_POOL_TEMPLATES drift-tolerant template list",
            "runtime/loot_pools.py _find_pool package/casing tolerance",
            "runtime/loot_pools.py _iter_loaded_pools find_all feature test",
        ],
    },
    {
        "section": "live.arms.anim_slots",
        "question": "Which AnimNodeSlot nodes does Pawn.Arms own, and do they expose PlayCustomAnim?",
        "saves": 45,
        "resolves": [
            "runtime/combat/pose.py _get_arms_anim_slots global find_all scan",
            "runtime/combat/pose.py _POSE_KIND / fallback-arc branching in play_swing",
        ],
    },
    {
        "section": "live.arms.melee_sequences",
        "question": "Is `Bladed_Melee` a real sequence, and what is the fallback actually called?",
        "saves": 20,
        "resolves": [
            "runtime/combat/pose.py `for seq_name in (\"Bladed_Melee\", \"Melee\")` retry loop",
        ],
    },
    {
        "section": "live.held_weapon.mesh",
        "question": "Are Mesh.Materials entries materials or {Material, ...} structs?",
        "saves": 25,
        "resolves": [
            "runtime/secondary_vfx.py _material_of entry-shape guessing",
            "runtime/secondary_vfx.py _fallback_material slot order",
        ],
    },
    {
        "section": "live.world_actors",
        "question": "Which class names do melee victims and target dummies actually have?",
        "saves": 35,
        "resolves": [
            "runtime/combat/attacks.py is_target_dummy path-string matching",
            "runtime/combat/attacks.py is_valid_melee_victim substring class checks",
        ],
    },
    {
        "section": "live.item_card_observed",
        "question": "What icon strings and labels does BL2 send for a vanilla gun card?",
        "saves": 70,
        "resolves": [
            "runtime/ui_item_card.py observed_icons / observed_elem_icon dynamic learning",
            "runtime/ui_item_card.py _ELEMENT_ICON_FALLBACK table",
            "runtime/ui_item_card.py `or \"pistol\"` / `or \"\"` argument defaults",
        ],
    },
    {
        "section": "live.frame_cadence",
        "question": "What is the real frame interval, so `3 ticks` can become milliseconds?",
        "saves": 25,
        "resolves": [
            "runtime/resolver.py _DEFERRED_STEP_TICKS / _MISSION_RESTORE_TICKS magic tick counts",
            "runtime/__init__.py `% 30` / `% 60` / `>= 90` scheduling magic numbers",
        ],
    },
    {
        "section": "live.spawn_projectile_providers",
        "question": "Where does BL2 keep Behavior_SpawnProjectile nodes for the throw VFX?",
        "saves": 15,
        "resolves": [
            "runtime/secondary_vfx.py Tediore donor path folklore in the module docstring",
        ],
    },
]


SECTIONS: dict[str, Callable[[], Any]] = {
    "schema": schema.probe_all,
    "catalogue": assets.probe_all,
    "live": live.probe_live,
}


def run_sections(selected: list[str] | None = None) -> dict[str, Any]:
    """Run the requested sections, isolating failures so one bad probe cannot lose the rest."""
    wanted = selected or ["schema", "catalogue", "census", "live"]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sections_run": [],
        "errors": {},
    }
    for name in wanted:
        runner = SECTIONS.get(name)
        if runner is None:
            continue
        try:
            payload[name] = runner()
            payload["sections_run"].append(name)
            log(f"section {name} complete")
        except Exception as exc:
            payload["errors"][name] = repr(exc)
            log(f"section {name} failed: {exc!r}")
    if "census" in wanted:
        try:
            payload["census"] = census.snapshot()
            payload["sections_run"].append("census")
        except Exception as exc:
            payload["errors"]["census"] = repr(exc)
    payload["targets"] = TARGETS
    payload["census_plan"] = census.describe_plan()
    return payload


def write(payload: dict[str, Any]) -> dict[str, str]:
    """Persist JSON + markdown review sheet; returns the paths written (empty when blocked)."""
    written = {"json": "", "markdown": ""}
    try:
        write_json(JSON_PATH, payload)
        written["json"] = str(JSON_PATH)
    except Exception as exc:
        log(f"JSON write failed ({JSON_PATH}): {exc!r}")
        return written
    try:
        REPORT_PATH.write_text(render_markdown(payload), encoding="utf-8")
        written["markdown"] = str(REPORT_PATH)
    except Exception as exc:
        log(f"markdown render failed: {exc!r}")
    return written


def render_markdown(payload: dict[str, Any]) -> str:
    """A short review sheet: what was measured, and what it lets us delete."""
    lines: list[str] = [
        "# Melee Arsenal ground truth",
        "",
        f"Generated {payload.get('generated_at', '')} by `melee_arsenal_probe`.",
        f"Sections: {', '.join(payload.get('sections_run', [])) or 'none'}",
        "",
    ]
    errors = payload.get("errors") or {}
    if errors:
        lines += ["## Probe errors", ""]
        lines += [f"- `{name}`: {message}" for name, message in errors.items()]
        lines += [""]

    for renderer in (
        _render_hook_section,
        _render_function_section,
        _render_asset_section,
        _render_weight_section,
        _render_card_section,
        _render_arms_section,
        _render_targets_section,
    ):
        lines += renderer(payload)
    return "\n".join(lines) + "\n"


def _render_hook_section(payload: dict[str, Any]) -> list[str]:
    census_data = payload.get("census") or {}
    verdicts = census_data.get("verdicts") or {}
    if not verdicts:
        return [
            "## Hook census",
            "",
            "Not run yet. Enable `Record Hook Census`, play for a minute, then run `probe_census`.",
            "",
        ]
    lines = ["## Hook census", ""]
    for group, verdict in verdicts.items():
        firing = verdict.get("firing") or []
        silent = verdict.get("silent") or []
        lines.append(f"### {group}")
        lines.append("")
        if firing:
            lines += [f"- fires: `{entry}`" for entry in firing]
        if silent:
            lines += [f"- never fires: `{entry}`" for entry in silent]
        action = verdict.get("action")
        if action:
            lines += ["", f"**Action:** {action}"]
        lines.append("")
    frame_stats = census_data.get("frame_stats") or {}
    if frame_stats:
        lines += ["### frame cadence", ""]
        for hook, stats in frame_stats.items():
            lines.append(
                f"- `{hook}`: {stats.get('mean_ms')} ms mean "
                f"(~{stats.get('implied_hz')} Hz, 3 ticks ~= {stats.get('ms_per_3_ticks')} ms)"
            )
        lines.append("")
    return lines


def _render_function_section(payload: dict[str, Any]) -> list[str]:
    schema_data = payload.get("schema") or {}
    functions = schema_data.get("functions") or {}
    if not functions:
        return []
    missing = [key for key, record in functions.items() if not record.get("exists")]
    if not missing:
        return [
            "## Hook targets",
            "",
            "Every configured hook target resolved to a live UFunction on this build.",
            "",
        ]
    lines = [
        "## Hook targets not found",
        "",
        f"{len(missing)} of {len(functions)} targets did not resolve. A target can also be missing "
        "because its package is not loaded yet (main menu) — re-run from a loaded map before "
        "deleting anything.",
        "",
    ]
    lines += [f"- `{key}`: `{functions[key].get('hook', '')}`" for key in missing]
    lines.append("")
    return lines


def _render_asset_section(payload: dict[str, Any]) -> list[str]:
    catalogue = (payload.get("catalogue") or {}).get("catalogues") or {}
    if not catalogue:
        return []
    lines = ["## Verified asset paths", ""]
    wanted = (
        ("particle_systems", "ParticleSystem", 10),
        ("ak_events", "AkEvent", 10),
        ("camera_anims", "CameraAnim", 6),
        ("impact_definitions", "ImpactDefinition", 4),
        ("damage_types", "DamageTypeDefinition", 8),
        ("explosion_collections", "ExplosionCollectionDefinition", 4),
    )
    for key, label, limit in wanted:
        section = catalogue.get(key) or {}
        matched = [row["path"] for row in section.get("matched") or []]
        if not matched:
            continue
        lines.append(f"**{label}** ({section.get('total_loaded', 0)} loaded)")
        lines.append("")
        lines += [f"- `{path}`" for path in matched[:limit]]
        if len(matched) > limit:
            lines.append(f"- ... {len(matched) - limit} more in `ground_truth.json`")
        lines.append("")

    donors = (payload.get("catalogue") or {}).get("behaviours", {}).get("explosion_donors") or {}
    if donors.get("count"):
        lines += ["**Behavior_Explode donors** (clone these instead of scanning)", ""]
        lines += [
            f"- `{row['firing_mode']}` via `{row['function_that_carries_it']}`"
            for row in donors.get("donors", [])[:10]
        ]
        lines.append("")
    return lines


def _render_weight_section(payload: dict[str, Any]) -> list[str]:
    behaviours = (payload.get("catalogue") or {}).get("behaviours") or {}
    init_defs = behaviours.get("attribute_init_defs") or []
    pools = ((payload.get("catalogue") or {}).get("pools") or {}).get("pools") or []
    if not init_defs and not pools:
        return []
    lines = ["## Loot weights", ""]
    if init_defs:
        lines += ["| init definition | multiplier constant |", "| --- | --- |"]
        for row in init_defs[:24]:
            lines.append(f"| `{row['name']}` | {row.get('multiplier_base_value_constant')} |")
        lines.append("")
    weapon_pools = [row for row in pools if row.get("entry_count")]
    if weapon_pools:
        lines.append(f"{len(pools)} `ItemPoolDefinition` objects loaded; {len(weapon_pools)} have entries.")
        lines.append("")
        for pool in weapon_pools[:5]:
            lines.append(f"- `{pool['path']}` ({pool['entry_count']} entries, quantity={pool.get('quantity')})")
            for entry in pool.get("entries", [])[:3]:
                probability = entry.get("probability") or {}
                lines.append(
                    f"  - {entry.get('item', '').rsplit('.', 1)[-1]}: "
                    f"init={probability.get('InitializationDefinitionName')} "
                    f"scale={probability.get('BaseValueScaleConstant')}"
                )
        lines.append("")
    return lines


def _render_card_section(payload: dict[str, Any]) -> list[str]:
    card = ((payload.get("census") or {}).get("item_card")) or {}
    top_stats = card.get("top_stats") or {}
    if not top_stats:
        return []
    lines = ["## Vanilla item-card icons", "", "| slot | label | icon |", "| --- | --- | --- |"]
    for index, entry in sorted(top_stats.items(), key=lambda item: int(item[0])):
        lines.append(f"| {index} | {entry.get('Label', '')} | `{entry.get('IconName', '')}` |")
    title = card.get("title") or {}
    if title:
        lines += ["", f"`SetTitle` arguments: `{title}`"]
    bottom = card.get("bottom_stats") or []
    if bottom:
        lines += ["", f"`SetBottomStat` sample: `{bottom[0]}`"]
    lines.append("")
    return lines


def _render_arms_section(payload: dict[str, Any]) -> list[str]:
    arms = ((payload.get("live") or {}).get("arms")) or {}
    if not arms.get("found"):
        return []
    slots = arms.get("anim_slots") or {}
    lines = ["## First-person arms", ""]
    lines.append(f"- anim slots: {slots.get('node_names', [])}")
    lines.append(f"- `WeaponAction` / `CustomAnimSlot`: {slots.get('has_weapon_action')} / {slots.get('has_custom_anim_slot')}")
    lines.append(f"- `Bladed_Melee` sequence present: {arms.get('bladed_melee_available')}")
    lines.append(f"- fallback `Melee` sequence present: {arms.get('melee_fallback_available')}")
    melee_sequences = arms.get("melee_sequences") or []
    if melee_sequences:
        lines.append(f"- melee sequences: {melee_sequences[:12]}")
    lines.append("")
    return lines


def _render_targets_section(_payload: dict[str, Any] | None = None) -> list[str]:
    total = sum(int(entry.get("saves", 0)) for entry in TARGETS)
    lines = [
        "## Cleanup ledger",
        "",
        f"Total line estimate unlocked by these sections: ~{total}.",
        "",
        "| section | saves | runtime symbols it unblocks |",
        "| --- | --- | --- |",
    ]
    for entry in TARGETS:
        symbols = "<br>".join(entry.get("resolves", []))
        lines.append(f"| `{entry['section']}` | ~{entry.get('saves', 0)} | {symbols} |")
    lines.append("")
    return lines


def summarize(payload: dict[str, Any]) -> str:
    """One-line summary for the HUD / console."""
    sections = len(payload.get("sections_run", []))
    errors = len(payload.get("errors", {}))
    missing = len(
        [
            key
            for key, record in ((payload.get("schema") or {}).get("functions") or {}).items()
            if not record.get("exists")
        ]
    )
    return f"{sections} sections, {missing} missing hook targets, {errors} errors"
