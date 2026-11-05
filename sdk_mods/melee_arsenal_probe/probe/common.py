"""Shared, side-effect-free reflection helpers for the ground-truth probe.

The probe is deliberately standalone: it never imports `melee_arsenal`, so it keeps working when
the main mod fails to load, and it can be installed next to a released build. Everything in this
module is a read: no `construct_object`, no property writes, no gameplay hooks that Block.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

try:
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - only outside the game
    unrealsdk = None  # type: ignore[assignment]


SCHEMA_VERSION = "melee_arsenal_probe.ground_truth/1.0"

MOD_DIR: Path = Path(__file__).resolve().parents[1]
OUT_DIR: Path = MOD_DIR / "ground_truth"
JSON_PATH: Path = OUT_DIR / "ground_truth.json"
LOG_PATH: Path = OUT_DIR / "probe.log"
REPORT_PATH: Path = OUT_DIR / "GROUND_TRUTH.md"

# Hard ceilings so a probe run can never stall a frame for seconds.
MAX_FIND_ALL = 20_000
MAX_STRUCT_DEPTH = 2
MAX_SAMPLES_PER_HOOK = 4
MAX_DEFAULT_STR_LEN = 220

# Mirrors the EObjectFlags bits BL2 sets on destroyed / pending-kill objects.
DEAD_OBJECT_FLAGS = 0x0000200000000000 | 0x0000000200000000 | 0x8000 | 0x10000

# UE3 EPropertyFlags bits reported on a UFunction's parameter list.
CPF_OPTIONAL_PARM = 0x0000000000000010
CPF_OUT_PARM = 0x0000000000000100
CPF_RETURN_PARM = 0x0000000000000400


def log(msg: str) -> None:
    """Echo to the SDK console and to `ground_truth/probe.log`."""
    line = f"[Probe] {msg}"
    if unrealsdk is not None:
        try:
            unrealsdk.logging.info(line)
        except Exception:
            pass
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {line}\n")
    except Exception:
        pass


# Object identity ---------------------------------------------------------------------------


def obj_path(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        return str(obj._path_name())
    except Exception:
        return ""


def class_name(obj: Any) -> str:
    if obj is None:
        return ""
    try:
        cls_obj = getattr(obj, "Class", None)
        return str(getattr(cls_obj, "Name", "") or "")
    except Exception:
        return ""


def is_dead(obj: Any) -> bool:
    if obj is None:
        return True
    try:
        return bool(int(getattr(obj, "ObjectFlags", 0)) & DEAD_OBJECT_FLAGS)
    except Exception:
        return False


def same_uobject(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if a is b:
        return True
    path_a = obj_path(a)
    return bool(path_a) and path_a == obj_path(b)


def iter_all(cls_name_: str, *, exact: bool = False, limit: int = MAX_FIND_ALL) -> list[Any]:
    """Every loaded object of a class, bounded. Returns `[]` when the SDK cannot enumerate."""
    if unrealsdk is None:
        return []
    try:
        found: Iterator[Any] = iter(unrealsdk.find_all(cls_name_, exact=exact))
    except TypeError:
        try:
            found = iter(unrealsdk.find_all(cls_name_))
        except Exception:
            return []
    except Exception:
        return []
    out: list[Any] = []
    try:
        for obj in found:
            out.append(obj)
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def find_object(cls_name_: str, path: str) -> Any:
    if unrealsdk is None or not path:
        return None
    try:
        return unrealsdk.find_object(cls_name_, path)
    except Exception:
        return None


def find_class(name: str) -> Any:
    if unrealsdk is None or not name:
        return None
    finder = safe_get(unrealsdk, "find_class", None)
    if not callable(finder):
        return None
    try:
        return finder(name)
    except Exception:
        return None


def resolve_function(class_path: str, func_name: str) -> Any:
    """The UFunction named `func_name` on `Package.Class`, or None when it does not exist.

    `unrealsdk.load_package()` is deliberately never used here. BL2's `LoadPackage` fatals (a UE3
    "Failed to find file for package ..." crash, not a Python exception) when handed a name that is
    not a real `.upk` — and a class path's first segment ("WillowGame") is exactly that. Probing
    for a function that may not exist must therefore stay on `find_object` / `Class._find`.
    """
    if unrealsdk is None or not class_path or not func_name:
        return None

    # A UFunction's outer is its class, so the canonical path uses `:` before the function name.
    for path in (f"{class_path}:{func_name}", f"{class_path}.{func_name}"):
        found = find_object("Function", path)
        if found is not None:
            return found

    # Fallback: ask the owning class directly. This is the same call `runtime/resolver.py` makes.
    owner = find_class(class_path.rsplit(".", 1)[-1])
    finder = safe_get(owner, "_find", None)
    if not callable(finder):
        return None
    try:
        return finder(func_name)
    except Exception:
        return None


# Reflection --------------------------------------------------------------------------------


def props_of(obj_or_class: Any) -> list[Any]:
    """`_properties()` of an object, its class, or a function's parameter list."""
    if obj_or_class is None:
        return []
    for candidate in (obj_or_class, safe_get(obj_or_class, "Class", None)):
        if candidate is None:
            continue
        getter = safe_get(candidate, "_properties", None)
        if not callable(getter):
            continue
        try:
            return list(getter())
        except Exception:
            continue
    return []


def enum_names(enum_obj: Any) -> list[str] | None:
    """Enum literal names in declared order, or None when the enum object is opaque."""
    if enum_obj is None:
        return None
    getter = safe_get(enum_obj, "_names", None)
    if not callable(getter):
        return None
    try:
        return [str(name) for name in getter()]
    except Exception:
        return None


def prop_info(prop: Any) -> dict[str, Any]:
    """Name + shape of one UE3 property: the ground truth `hasattr` guards are guessing at."""
    info: dict[str, Any] = {"name": str(safe_get(prop, "Name", "") or "")}
    struct_obj = safe_get(prop, "Struct", None)
    if struct_obj is not None:
        info["struct"] = str(safe_get(struct_obj, "Name", "") or "")
    enum_obj = safe_get(prop, "Enum", None)
    if enum_obj is not None:
        info["enum"] = str(safe_get(enum_obj, "Name", "") or "")
        names = enum_names(enum_obj)
        if names:
            info["enum_values"] = names
    for extra in ("Type", "ArrayDim", "PropertyFlags", "ElementSize"):
        value = safe_get(prop, extra, None)
        if value is None:
            continue
        try:
            info[extra] = int(value)
        except Exception:
            text = str(value)
            if text:
                info[extra] = text
    if info.get("ArrayDim", 1) not in (0, 1, None):
        info["is_array"] = True
    flags = info.get("PropertyFlags")
    if isinstance(flags, int):
        if flags & CPF_OUT_PARM:
            info["is_out_param"] = True
        if flags & CPF_RETURN_PARM:
            info["is_return_param"] = True
        if flags & CPF_OPTIONAL_PARM:
            info["is_optional_param"] = True
    return info


def func_params(func: Any) -> list[dict[str, Any]]:
    """Parameter list of a UFunction, including `ReturnValue` when the engine exposes it."""
    params: list[dict[str, Any]] = []
    for prop in props_of(func):
        info = prop_info(prop)
        if info.get("name"):
            params.append(info)
    return params


def safe_get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    try:
        return getattr(obj, name)
    except Exception:
        return default


# Value serialisation -----------------------------------------------------------------------


def describe_value(value: Any, depth: int = 0) -> Any:
    """Best-effort JSON-safe description of an arbitrary UE3 / Python value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_DEFAULT_STR_LEN]
    if depth >= MAX_STRUCT_DEPTH:
        return {"$path": obj_path(value) or repr(value)[:MAX_DEFAULT_STR_LEN]}

    if isinstance(value, (list, tuple)):
        return [describe_value(item, depth + 1) for item in list(value)[:16]]

    path = obj_path(value)
    if path:
        # Rotator / Vector / other structs also expose a path-less shape; real UObjects do not.
        struct_props = props_of(value)
        if struct_props:
            return {
                "$path": path,
                "$fields": {
                    str(safe_get(prop, "Name", "")): describe_value(
                        safe_get(value, str(safe_get(prop, "Name", "")), None), depth + 1
                    )
                    for prop in struct_props[:24]
                },
            }
        return {"$path": path}

    # Struct exposed by value (BL2 `WrappedStruct`): no path, but named sub-fields.
    if hasattr(value, "Pitch") and hasattr(value, "Yaw") and hasattr(value, "Roll"):
        return {
            "Pitch": int(safe_get(value, "Pitch", 0) or 0),
            "Yaw": int(safe_get(value, "Yaw", 0) or 0),
            "Roll": int(safe_get(value, "Roll", 0) or 0),
        }
    if hasattr(value, "X") and hasattr(value, "Y") and hasattr(value, "Z"):
        return {
            "X": round(float(safe_get(value, "X", 0.0) or 0.0), 4),
            "Y": round(float(safe_get(value, "Y", 0.0) or 0.0), 4),
            "Z": round(float(safe_get(value, "Z", 0.0) or 0.0), 4),
        }
    struct_props = props_of(value)
    if struct_props:
        return {
            "$struct_fields": {
                str(safe_get(prop, "Name", "")): describe_value(
                    safe_get(value, str(safe_get(prop, "Name", "")), None), depth + 1
                )
                for prop in struct_props[:24]
            }
        }
    return repr(value)[:MAX_DEFAULT_STR_LEN]


def struct_shape(struct_obj: Any, depth: int = 0) -> dict[str, Any]:
    """Field list of a struct definition, recursing one level into nested structs."""
    shape: dict[str, Any] = {}
    for prop in props_of(struct_obj)[:64]:
        info = prop_info(prop)
        name = info.get("name", "")
        if not name or name.startswith("VfTable"):
            continue
        nested = safe_get(prop, "Struct", None)
        if nested is not None and depth < MAX_STRUCT_DEPTH:
            inner = struct_shape(nested, depth + 1)
            if inner:
                info["fields"] = inner
        shape[name] = info
    return shape


# Output ------------------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def keyword_match(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in keywords)
