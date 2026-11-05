"""Hook census: which candidate hooks actually fire, in what order, with what arguments.

The runtime registers several hooks per intent because it could not observe the engine:

* both `Engine.GameViewportClient:Tick` and `WillowPlayerController:PlayerTick` drive one frame body,
* `StartZoom` *and* `ClientStartZoom` (same for stop) mark the secondary-attack input,
* `SetItemCard` *and* `SetItemCardEx` *and* `SetTitle` / `SetTopStat` / `SetFunStats` compose the card,
* `InitializeFromDefinitionData` *and* `InitializeInternal` bisect weapon construction.

Every one of those is a measurement, not a design choice. This module takes the measurement and
leaves a call count, a hit order and a first-call argument dump behind. A hook whose count stays 0
is provably dead and can be deleted; a hook pair with a stable order can be collapsed to one branch.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .common import (
    MAX_SAMPLES_PER_HOOK,
    describe_value,
    func_params,
    log,
    obj_path,
    safe_get,
    same_uobject,
)

try:
    import unrealsdk  # type: ignore[import-not-found]
    from unrealsdk.hooks import Type  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - only outside the game
    unrealsdk = None  # type: ignore[assignment]
    Type = None  # type: ignore[assignment]


# One logical engine event -> every hook the runtime (or a cleanup candidate) might use for it.
# Groups exist so the census can answer "which one of these really fires?" instead of "does it exist?".
CENSUS_GROUPS: dict[str, tuple[str, ...]] = {
    "frame_tick": (
        "Engine.GameViewportClient:Tick",
        "WillowGame.WillowGameViewportClient:Tick",
        "WillowGame.WillowPlayerController:PlayerTick",
    ),
    "weapon_init": (
        "WillowGame.WillowWeapon:InitializeFromDefinitionData",
        "WillowGame.WillowWeapon:InitializeInternal",
        "WillowGame.WillowWeapon:InitConstructPreview",
        "WillowGame.WillowWeapon:ChooseRandomNameParts",
    ),
    "weapon_fire": (
        "WillowGame.WillowPawn:WeaponFired",
        "WillowGame.WillowWeapon:InstantFire",
        "WillowGame.WillowWeapon:FiringModeDefinitionFire",
        "WillowGame.WillowWeapon:PlayFireEffects",
        "WillowGame.WillowWeapon:ConsumeAmmo",
        "WillowGame.WillowWeapon:SharedFireAmmunition",
    ),
    "zoom": (
        "WillowGame.WillowWeapon:StartZoom",
        "WillowGame.WillowWeapon:ClientStartZoom",
        "WillowGame.WillowWeapon:StopZoom",
        "WillowGame.WillowWeapon:ClientStopZoom",
    ),
    "item_card": (
        "WillowGame.ItemCardGFxObject:SetItemCard",
        "WillowGame.ItemCardGFxObject:SetItemCardEx",
        "WillowGame.ItemCardGFxObject:SetTitle",
        "WillowGame.ItemCardGFxObject:SetTopStat",
        "WillowGame.ItemCardGFxObject:SetBottomStat",
        "WillowGame.ItemCardGFxObject:SetFunStats",
        "WillowGame.ItemCardGFxObject:AdjustHeight",
    ),
    "damage": (
        "WillowGame.WillowAIPawn:TakeDamage",
        "WillowGame.WillowPlayerPawn:TakeDamage",
        "WillowGame.WillowDamageTypeDefinition:DisplayRecentDamageForPlayer",
    ),
    "persistence": (
        "WillowGame.WillowPlayerController:ValidateWeaponDefinition",
        "WillowGame.WillowPlayerController:ApplyWeaponSaveGameData",
        "WillowGame.WillowSaveGameManager:SaveGame",
    ),
    "loot_spawn": (
        "WillowGame.Behavior_SpawnLootAroundPoint:PlaceSpawnedItems",
    ),
}


class _HookStats:
    """Mutable counter block for one (group, hook, type) triple."""

    __slots__ = ("group", "hook", "kind", "count", "first_at", "last_at", "samples", "param_names")

    def __init__(self, group: str, hook: str, kind: str) -> None:
        self.group = group
        self.hook = hook
        self.kind = kind
        self.count = 0
        self.first_at = 0.0
        self.last_at = 0.0
        self.samples: list[dict[str, Any]] = []
        self.param_names: list[str] = []

    def as_dict(self) -> dict[str, Any]:
        return {
            "hook": self.hook,
            "type": self.kind,
            "count": self.count,
            "first_seen_at": round(self.first_at, 3),
            "last_seen_at": round(self.last_at, 3),
            "param_names": self.param_names,
            "samples": self.samples,
        }


_STATS: dict[str, _HookStats] = {}
# First-fire order across every watched hook: answers "which of these runs first?" without logging
# every frame. Bounded by the number of hooks, so per-frame hooks cost nothing here.
_FIRST_ORDER: list[list[Any]] = []
_FRAME_TIMES: dict[str, list[float]] = {}
_FRAME_LAST: dict[str, float] = {}
MAX_FRAME_SAMPLES = 400
_ENABLED: bool = True
_SAMPLE_LIMIT: int = MAX_SAMPLES_PER_HOOK
_STARTED_AT: float = time.monotonic()

# Item-card ground truth: the exact Flash icon strings and stat labels BL2 sends for vanilla guns.
_CARD_TITLE: dict[str, Any] = {}
_CARD_TOP_STATS: dict[int, dict[str, Any]] = {}
_CARD_BOTTOM_STAT_LIMIT = 60
_CARD_BOTTOM_STATS: list[dict[str, Any]] = []


def set_enabled(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = bool(enabled)


def set_sample_limit(limit: int) -> None:
    global _SAMPLE_LIMIT
    _SAMPLE_LIMIT = max(0, min(MAX_SAMPLES_PER_HOOK, int(limit)))


def is_enabled() -> bool:
    return _ENABLED


# Hook names --------------------------------------------------------------------------------


def _function_exists(hook_name: str) -> bool:
    """True when the UFunction resolves. A UFunction's outer is its class: `Package.Class:Function`."""
    if unrealsdk is None:
        return False
    class_path, _, func_name = hook_name.partition(":")
    if not class_path or not func_name:
        return False
    for path in (f"{class_path}:{func_name}", f"{class_path}.{func_name}"):
        try:
            if unrealsdk.find_object("Function", path) is not None:
                return True
        except Exception:
            continue
    return False


def resolvable_hooks() -> dict[str, list[str]]:
    """Group -> hook names whose UFunction exists on this build."""
    out: dict[str, list[str]] = {}
    for group, hooks in CENSUS_GROUPS.items():
        live = [hook for hook in hooks if _function_exists(hook)]
        if live:
            out[group] = live
    return out


def missing_hooks() -> dict[str, list[str]]:
    """Group -> hook names that do NOT exist. Each one is deletable work in the runtime."""
    out: dict[str, list[str]] = {}
    for group, hooks in CENSUS_GROUPS.items():
        dead = [hook for hook in hooks if not _function_exists(hook)]
        if dead:
            out[group] = dead
    return out


# Recording ---------------------------------------------------------------------------------


def _stats_for(group: str, hook: str, kind: str) -> _HookStats:
    key = f"{kind}|{hook}"
    stats = _STATS.get(key)
    if stats is None:
        stats = _HookStats(group, hook, kind)
        _STATS[key] = stats
    return stats


def _describe_args(args: Any, func: Any) -> dict[str, Any]:
    """Named argument dump for the first calls of a hook."""
    out: dict[str, Any] = {}
    names: list[str] = []
    for prop in func_params(func):
        name = prop.get("name", "")
        if name:
            names.append(name)
    if not names:
        for name in ("ReturnValue", "InventoryItem", "CompareAgainstInventoryItem"):
            if safe_get(args, name, None) is not None:
                names.append(name)
    for name in names[:16]:
        value = safe_get(args, name, None)
        if value is None:
            continue
        out[name] = describe_value(value, depth=1)
    return out


def record(group: str, hook: str, kind: str, args: Any, func: Any, obj: Any = None, ret: Any = None) -> None:
    """Count one call. Deliberately cheap on the hot path and never raises."""
    if not _ENABLED:
        return
    try:
        now = time.monotonic() - _STARTED_AT
        stats = _stats_for(group, hook, kind)
        stats.count += 1
        if not stats.first_at:
            stats.first_at = now
            stats.param_names = [prop.get("name", "") for prop in func_params(func)]
        stats.last_at = now

        if len(stats.samples) < _SAMPLE_LIMIT:
            sample: dict[str, Any] = {
                "at": round(now, 3),
                "self": obj_path(obj) or describe_value(obj),
            }
            described_args = _describe_args(args, func)
            if described_args:
                sample["args"] = described_args
            if ret is not None:
                sample["ret"] = describe_value(ret, depth=1)
            stats.samples.append(sample)

        if stats.count == 1:
            _FIRST_ORDER.append([round(now, 4), group, hook, kind])

        if group == "frame_tick":
            _record_frame_interval(hook, now)

        if group == "item_card":
            _record_card_call(hook, args)
    except Exception:
        pass


def _record_frame_interval(hook: str, now: float) -> None:
    """Frame cadence per tick hook: proves whether 3 ticks is really ~50 ms."""
    previous = _FRAME_LAST.get(hook)
    _FRAME_LAST[hook] = now
    if previous is None:
        return
    samples = _FRAME_TIMES.setdefault(hook, [])
    if len(samples) < MAX_FRAME_SAMPLES:
        samples.append(now - previous)


def frame_stats() -> dict[str, Any]:
    """Mean / min / max frame interval per firing tick hook."""
    out: dict[str, Any] = {}
    for hook, samples in _FRAME_TIMES.items():
        real = [value for value in samples if value > 0.0]
        if not real:
            continue
        mean = sum(real) / len(real)
        out[hook] = {
            "samples": len(real),
            "mean_ms": round(mean * 1000.0, 2),
            "min_ms": round(min(real) * 1000.0, 2),
            "max_ms": round(max(real) * 1000.0, 2),
            "implied_hz": round(1.0 / mean, 1) if mean > 0.0 else 0.0,
            "ms_per_3_ticks": round(mean * 3000.0, 1),
        }
    return out


def _record_card_call(hook: str, args: Any) -> None:
    """Keep the distinct icon / label tuples BL2 sends, so the card code can use literals."""
    func_name = hook.rsplit(":", 1)[-1]
    if func_name == "SetTitle":
        if _CARD_TITLE:
            return
        for name in ("Manufacturer", "Title", "Rarity", "TypeIcon", "ElementalIcon", "bIsReadied"):
            value = safe_get(args, name, None)
            if value is not None:
                _CARD_TITLE[name] = describe_value(value, depth=0)
        _CARD_TITLE["$self"] = obj_path(None)
        return

    if func_name == "SetTopStat":
        index = safe_get(args, "StatIndex", None)
        try:
            index = int(index)
        except Exception:
            return
        if index in _CARD_TOP_STATS:
            return
        entry: dict[str, Any] = {"StatIndex": index}
        for name in ("Label", "Value", "CompareArrow", "AuxValue", "IconName"):
            value = safe_get(args, name, None)
            if value is not None:
                entry[name] = describe_value(value, depth=0)
        _CARD_TOP_STATS[index] = entry
        return

    if func_name == "SetBottomStat" and len(_CARD_BOTTOM_STATS) < _CARD_BOTTOM_STAT_LIMIT:
        entry = {}
        for name in ("StatIndex", "Label", "Value", "CompareArrow", "IconName"):
            value = safe_get(args, name, None)
            if value is not None:
                entry[name] = describe_value(value, depth=0)
        if entry:
            _CARD_BOTTOM_STATS.append(entry)


# Snapshot ----------------------------------------------------------------------------------


def snapshot() -> dict[str, Any]:
    """Everything the census observed. Returned as plain JSON-safe data."""
    by_group: dict[str, list[dict[str, Any]]] = {}
    for stats in _STATS.values():
        by_group.setdefault(stats.group, []).append(stats.as_dict())
    for rows in by_group.values():
        rows.sort(key=lambda row: (row["first_seen_at"], row["hook"]))
    return {
        "enabled": _ENABLED,
        "elapsed_sec": round(time.monotonic() - _STARTED_AT, 2),
        "groups": by_group,
        "first_fire_order": list(_FIRST_ORDER),
        "resolvable_hooks": resolvable_hooks(),
        "missing_hooks": missing_hooks(),
        "frame_stats": frame_stats(),
        "item_card": {
            "title": _CARD_TITLE,
            "top_stats": {str(key): value for key, value in sorted(_CARD_TOP_STATS.items())},
            "bottom_stats": _CARD_BOTTOM_STATS,
        },
        "verdicts": _verdicts(by_group),
    }


def _verdicts(by_group: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """One-line conclusions a cleanup can act on directly."""
    verdicts: dict[str, Any] = {}
    for group, rows in by_group.items():
        firing = [row for row in rows if row["count"] > 0]
        silent = [row for row in rows if row["count"] == 0]
        verdict: dict[str, Any] = {
            "firing": [f"{row['hook']} [{row['type']}] x{row['count']}" for row in firing],
            "silent": [f"{row['hook']} [{row['type']}]" for row in silent],
        }
        if group == "frame_tick" and len(firing) > 1:
            verdict["action"] = (
                "More than one tick hook fires; keep the single cheapest one and delete the "
                "per-frame de-duplication guard in `_run_frame_tick`."
            )
        elif silent and firing:
            verdict["action"] = "Delete the silent hooks: they cannot run on this build."
        verdicts[group] = verdict
    return verdicts


def reset() -> None:
    global _STARTED_AT
    _STATS.clear()
    _FIRST_ORDER.clear()
    _STARTED_AT = time.monotonic()
    _CARD_TITLE.clear()
    _CARD_TOP_STATS.clear()
    _CARD_BOTTOM_STATS.clear()
    _FRAME_TIMES.clear()
    _FRAME_LAST.clear()


# Hook construction -------------------------------------------------------------------------


def _make_callback(group: str, hook_name: str, kind: str) -> Callable[..., Any]:
    def _callback(obj: Any, args: Any, ret: Any, func: Any) -> None:
        record(group, hook_name, kind, args, func, obj=obj, ret=ret)
        return None

    _callback.__name__ = f"probe_{group}_{hook_name.rsplit(':', 1)[-1]}_{kind}"
    return _callback


def build_hooks() -> list[Any]:
    """Hook objects for `build_mod(hooks=...)`: PRE and POST for every *existing* candidate."""
    if Type is None:
        return []
    try:
        from mods_base import hook  # type: ignore[import-not-found]
    except ImportError:
        return []

    hooks: list[Any] = []
    resolvable = resolvable_hooks()
    for group, hook_names in resolvable.items():
        for hook_name in hook_names:
            for kind_name, kind in (("PRE", Type.PRE), ("POST", Type.POST)):
                try:
                    hooks.append(hook(hook_name, kind)(_make_callback(group, hook_name, kind_name)))
                except Exception as exc:
                    log(f"census hook {hook_name} [{kind_name}] failed: {exc!r}")
    return hooks


def describe_plan() -> dict[str, Any]:
    """What the census will watch versus what it already knows is missing."""
    return {
        "groups": {group: list(hooks) for group, hooks in CENSUS_GROUPS.items()},
        "resolvable": resolvable_hooks(),
        "missing": missing_hooks(),
        "total_hooks": sum(len(values) for values in resolvable_hooks().values()) * 2,
        "self_check": {
            "unrealsdk_available": unrealsdk is not None,
            "hook_type_available": Type is not None,
            "same_uobject_works": same_uobject(None, None) is False,
        },
    }
