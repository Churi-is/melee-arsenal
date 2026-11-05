"""Thin, defensive wrapper around ``unrealsdk``.

The rest of the probe never imports ``unrealsdk`` directly.  Everything that can
vary between SDK/game versions is funnelled through this module:

* the module may not be importable at all (offline tooling, linting, inspection),
* some reflection helpers are only present in newer pyunrealsdk builds,
* property/function attribute lookups can raise for exotic property types,
* path names need a stable fallback if ``_path_name`` is missing.

Keeping those details here means the dump logic can be written against one
small, predictable surface.
"""

from __future__ import annotations

import json
import platform
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc

try:  # pragma: no cover - the in-game path
    import unrealsdk  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - offline imports/tooling
    unrealsdk = None  # type: ignore[assignment]

AVAILABLE: bool = unrealsdk is not None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(level: str, message: str) -> None:
    """Writes a message to the SDK logger, falling back to stdout/stderr."""
    if unrealsdk is not None:
        logger = getattr(unrealsdk, "logging", None)
        if logger is not None:
            fn = getattr(logger, level, None)
            if callable(fn):
                try:
                    fn(f"[RE Probe] {message}")
                    return
                except Exception:  # pragma: no cover - logger must never break a dump
                    pass
    stream = sys.stderr if level in {"error", "warning"} else sys.stdout
    print(f"[RE Probe] {message}", file=stream)


def debug(message: str) -> None:
    _log("debug", message)


def info(message: str) -> None:
    _log("info", message)


def warning(message: str) -> None:
    _log("warning", message)


def error(message: str) -> None:
    _log("error", message)


def exception(message: str, exc: BaseException) -> None:
    error(f"{message}: {exc!r}")


# ---------------------------------------------------------------------------
# Time / environment
# ---------------------------------------------------------------------------


def utc_now() -> str:
    """ISO-8601 UTC timestamp, second precision (stable and easy to sort)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def monotonic() -> float:
    return time.perf_counter()


def environment() -> dict[str, Any]:
    """Records the versions that affect what the dump means."""
    result: dict[str, Any] = {
        "python": sys.version.split()[0],
        "python_bits": struct.calcsize("P") * 8,
        "platform": platform.platform(),
        "executable": Path(sys.executable).name,
    }

    # unrealsdk exposes a version() helper in recent builds.  Do not require it.
    version_fn = getattr(unrealsdk, "version", None)
    if callable(version_fn):
        try:
            result["unrealsdk"] = str(version_fn())
        except Exception:
            pass

    for module_name in ("unrealsdk", "pyunrealsdk", "mods_base"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        value = getattr(module, "__version__", None)
        if value is not None:
            result[module_name] = str(value)

    try:
        from mods_base import Game  # type: ignore[import-not-found]

        result["game"] = Game.get_current().name
        result["game_tree"] = Game.get_tree().name
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Reflection helpers
# ---------------------------------------------------------------------------


def safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr`` which never raises for absent/throwing unreal fields."""
    if obj is None:
        return default
    try:
        return getattr(obj, name)
    except Exception:
        return default


def call(obj: Any, name: str, *args: Any, default: Any = None) -> Any:
    """Calls a zero/one-arg helper (e.g. ``_properties``) without raising."""
    fn = safe_getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn(*args)
    except Exception:
        return default


def iterable(value: Any) -> Iterable[Any]:
    """Yields from an SDK iterator/list, swallowing mid-iteration failure."""
    if value is None:
        return
    try:
        for item in value:
            yield item
    except Exception:
        return


def path_of(obj: Any) -> str | None:
    """Canonical object path (``Package.Group.Object`` / ``Package.Class:Func``)."""
    if obj is None:
        return None

    path_fn = safe_getattr(obj, "_path_name", None)
    if callable(path_fn):
        try:
            path = str(path_fn())
            if path:
                return path
        except Exception:
            pass

    # Fallback for exotic field wrappers: reconstruct from Outer/Name.
    name = safe_getattr(obj, "Name", None)
    if name is None:
        return None
    name = str(name)

    outer = safe_getattr(obj, "Outer", None)
    if outer is None:
        return name

    outer_path = path_of(outer)
    if not outer_path:
        return name

    # UE3 uses ':' between a class and its members, '.' everywhere else.
    outer_class = safe_getattr(outer, "Class", None)
    outer_class_name = str(safe_getattr(outer_class, "Name", ""))
    outer_outer = safe_getattr(outer, "Outer", None)
    outer_outer_class = safe_getattr(outer_outer, "Class", None)
    outer_outer_class_name = str(safe_getattr(outer_outer_class, "Name", ""))
    separator = ":" if outer_class_name != "Package" and outer_outer_class_name == "Package" else "."
    return f"{outer_path}{separator}{name}"


def name_of(obj: Any) -> str | None:
    value = safe_getattr(obj, "Name", None)
    if value is None:
        return path_of(obj)
    try:
        return str(value)
    except Exception:
        return None


def class_of(obj: Any) -> Any:
    return safe_getattr(obj, "Class", None)


def class_name_of(obj: Any) -> str:
    cls = class_of(obj)
    try:
        return str(safe_getattr(cls, "Name", "") or "")
    except Exception:
        return ""


def class_path_of(obj: Any) -> str | None:
    return path_of(class_of(obj))


def super_struct(obj: Any) -> Any:
    """``SuperField`` (the inheritance pointer of a UStruct)."""
    return safe_getattr(obj, "SuperField", None)


def class_chain_names(obj: Any, limit: int = 64) -> list[str]:
    """Names of the object's class and its superclasses, most-derived first."""
    names: list[str] = []
    cls = class_of(obj)
    seen: set[int] = set()
    while cls is not None and len(names) < limit:
        marker = id(cls)
        if marker in seen:
            break
        seen.add(marker)
        cls_name = safe_getattr(cls, "Name", None)
        if cls_name is not None:
            names.append(str(cls_name))
        cls = super_struct(cls)
    return names


def struct_chain_names(struct: Any, limit: int = 64) -> list[str]:
    """Names of a UStruct and its SuperField chain, most-derived first."""
    names: list[str] = []
    current = struct
    seen: set[int] = set()
    while current is not None and len(names) < limit:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        name = safe_getattr(current, "Name", None)
        if name is not None:
            names.append(str(name))
        current = super_struct(current)
    return names


def inherits_class(obj: Any, base_name: str) -> bool:
    return base_name in class_chain_names(obj)


def find_all(class_name: str, *, exact: bool = False) -> list[Any]:
    """``unrealsdk.find_all`` with compatibility for older signatures."""
    if unrealsdk is None:
        return []
    finder = getattr(unrealsdk, "find_all", None)
    if not callable(finder):
        return []
    try:
        return list(finder(class_name, exact=exact))
    except TypeError:
        try:
            return list(finder(class_name))
        except Exception:
            return []
    except Exception:
        return []


def find_class(class_name: str) -> Any:
    if unrealsdk is None:
        return None
    finder = getattr(unrealsdk, "find_class", None)
    if not callable(finder):
        return None
    try:
        return finder(class_name)
    except Exception:
        return None


def find_object(class_name: str, object_path: str) -> Any:
    if unrealsdk is None:
        return None
    finder = getattr(unrealsdk, "find_object", None)
    if not callable(finder):
        return None
    try:
        return finder(class_name, object_path)
    except Exception:
        return None


def find_enum(enum_name: str) -> Any:
    if unrealsdk is None:
        return None
    finder = getattr(unrealsdk, "find_enum", None)
    if not callable(finder):
        return None
    try:
        return finder(enum_name)
    except Exception:
        return None


def object_by_path(object_path: str, objects: Iterable[Any] | None = None) -> Any:
    """Finds an object by exact path, preferring a supplied object list."""
    # Fast path: find_object("Object", path) resolves via GObjects in C++.
    found = find_object("Object", object_path)
    if found is not None and path_of(found) == object_path:
        return found

    for obj in objects if objects is not None else find_all("Object"):
        if path_of(obj) == object_path:
            return obj
    return None


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------


def property_type_name(prop: Any) -> str:
    return class_name_of(prop) or "UnknownProperty"


def property_owner_path(prop: Any) -> str | None:
    """The UStruct (class/function/struct) that *declares* this property."""
    owner = safe_getattr(prop, "Outer", None)
    if owner is None:
        owner = safe_getattr(prop, "Owner", None)
    return path_of(owner)


def properties_of(struct: Any) -> list[Any]:
    """All properties visible on a UStruct, including inherited ones."""
    props = call(struct, "_properties", default=None)
    return list(iterable(props))


def fields_of(struct: Any) -> list[Any]:
    fields = call(struct, "_fields", default=None)
    return list(iterable(fields))


def struct_size(struct: Any) -> int | None:
    size = call(struct, "_get_struct_size", default=None)
    if isinstance(size, int):
        return size
    value = safe_getattr(struct, "PropertySize", None)
    return int(value) if isinstance(value, int) else None


def int_attr(obj: Any, name: str) -> int | None:
    value = safe_getattr(obj, name, None)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return None


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def shorten(value: Any, limit: int) -> Any:
    """Truncates long strings, marking the truncation in-band."""
    if isinstance(value, str) and limit > 0 and len(value) > limit:
        return value[:limit] + "…<truncated>"
    return value


def json_default(value: Any) -> Any:
    """``json.dumps(default=...)`` fallback for stray SDK wrapper types."""
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    try:
        return str(value)
    except Exception:
        return "<unserializable>"


def dumps(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":") if indent is None else None,
        indent=indent,
        default=json_default,
    )


def write_json_atomic(path: Path, value: Any, *, indent: int = 2) -> None:
    """Writes a small JSON document without ever leaving a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps(value, indent=indent) + "\n", encoding="utf-8")
    tmp.replace(path)


def glob_match(pattern: str, value: str) -> bool:
    """Case-insensitive glob, with '*' and '?' (fnmatch without importing it)."""
    import fnmatch

    return fnmatch.fnmatchcase(value.lower(), pattern.lower())
