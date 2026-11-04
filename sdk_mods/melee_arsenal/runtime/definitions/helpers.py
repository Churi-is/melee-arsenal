"""UE3 object/struct helpers shared by every definition builder.

Property names and struct layouts here are verified against Borderlands 2, `ribs_arsenal`,
and `pyunrealsdk` 1.10 (`unrealsdk` 3.2.0): never assign a Python
`float`/`dict` to a `WrappedStruct` property — mutate sub-fields or `copy.copy()`
a donor struct first.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from ...core.enums import StatId
from ..log import log_debug_once, obj_path
from ..state import KEEP_ALIVE_FLAGS, STATE
from ._sdk import unrealsdk


def _sum_crit_bonus(modifiers: Any) -> float:
    total = 0.0
    for m in modifiers or ():
        if getattr(m, "stat", None) == StatId.CRIT_BONUS:
            total += float(getattr(m, "value", 0.0))
    return total


# 9,000 UU/s covers 300 UU in ~33ms (~2 frames at 60 FPS): fast enough to feel instantaneous while
# surviving `WillowLightProjectileManager`'s 1-frame `bPendingAdd` birth delay before collision tracing!


# 9,000 UU/s covers 300 UU in ~33ms (~2 frames at 60 FPS): fast enough to feel instantaneous while
# surviving `WillowLightProjectileManager`'s 1-frame `bPendingAdd` birth delay before collision tracing!
MELEE_RAY_SPEED: float = 9000.0
MELEE_CLIP_SIZE: int = 100


def _inherited_manufacturers(bal: Any) -> list[Any]:
    """Walk `BaseDefinition` chain — higher-rarity vanilla balances leave `Manufacturers` empty."""
    cur = bal
    steps = 0
    while cur is not None and steps < 12:
        steps += 1
        mfrs = list(getattr(cur, "Manufacturers", []) or [])
        if mfrs:
            return mfrs
        cur = getattr(cur, "BaseDefinition", None)
    return []


def _inherited_plc(bal: Any) -> Any:
    """Walk `BaseDefinition` chain to find the first non-null `PartListCollection`."""
    cur = bal
    steps = 0
    while cur is not None and steps < 12:
        steps += 1
        plc = getattr(cur, "RuntimePartListCollection", None) or getattr(
            cur, "WeaponPartListCollection", None
        )
        if plc is not None:
            return plc
        cur = getattr(cur, "BaseDefinition", None)
    return None


def _find(cls_name: str, path: str) -> Any:
    if unrealsdk is None or not path:
        return None
    try:
        return unrealsdk.find_object(cls_name, path)
    except Exception as exc:
        log_debug_once(f"definitions._find:{cls_name}:{path}", f"_find {cls_name} {path} failed: {exc!r}")
        return None


def _find_any(cls_names: tuple[str, ...], path: str) -> Any:
    for cls_name in cls_names:
        found = _find(cls_name, path)
        if found is not None:
            return found
    return None


def _sanitize_token(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip())
    return cleaned.strip("_") or "Default"


def _ensure_subpackage(parent: Any, name: str) -> Any:
    full = f"{obj_path(parent)}.{name}" if parent is not None else name
    existing = _find("Package", full)
    if existing is not None:
        return STATE.keep_alive(existing)
    pkg = unrealsdk.construct_object("Package", parent, name, KEEP_ALIVE_FLAGS, None)
    return STATE.keep_alive(pkg)


def _clear_array_prop(obj: Any, prop_name: str) -> None:
    if hasattr(obj, prop_name):
        try:
            arr = getattr(obj, prop_name)
            if hasattr(arr, "clear"):
                arr.clear()
            else:
                setattr(obj, prop_name, [])
        except Exception:
            pass


def _zero_vector_struct(obj: Any, field_name: str) -> None:
    if not hasattr(obj, field_name):
        return
    try:
        vec = getattr(obj, field_name)
        if vec is None:
            return
        vcopy = copy.copy(vec)
        vcopy.X = 0.0
        vcopy.Y = 0.0
        vcopy.Z = 0.0
        setattr(obj, field_name, vcopy)
    except Exception:
        try:
            vec = getattr(obj, field_name)
            vec.X = 0.0
            vec.Y = 0.0
            vec.Z = 0.0
        except Exception:
            pass


def _deg_to_rot_units(deg: float) -> int:
    return int(round(float(deg) * 65536.0 / 360.0))


def _dump_uobject_fields_summary(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if obj is None:
        return out
    try:
        cls = getattr(obj, "Class", None)
        if cls is None or not hasattr(cls, "_properties"):
            return out
        for prop in cls._properties():
            pname = str(getattr(prop, "Name", ""))
            if not pname or pname.startswith("VfTable"):
                continue
            try:
                val = getattr(obj, pname)
                if hasattr(val, "Pitch") and hasattr(val, "Yaw") and hasattr(val, "Roll"):
                    out[pname] = {"Pitch": int(val.Pitch), "Yaw": int(val.Yaw), "Roll": int(val.Roll)}
                elif hasattr(val, "X") and hasattr(val, "Y") and hasattr(val, "Z"):
                    out[pname] = {"X": round(float(val.X), 4), "Y": round(float(val.Y), 4), "Z": round(float(val.Z), 4)}
                elif isinstance(val, (int, float, bool, str)) or val is None:
                    out[pname] = val
                else:
                    out[pname] = obj_path(val) or repr(val)
            except Exception:
                continue
    except Exception:
        pass
    return out
