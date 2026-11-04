"""Stable UObject identity / weak-reference helpers shared by the live runtime.

pyunrealsdk may return a fresh Python wrapper for the same UE3 object.  Python ``is`` /
``id()`` are therefore only safe as a last-resort cache key, never as the primary identity
rule for gameplay state.  The full object path is the stable identity exposed by the SDK.
"""
from __future__ import annotations
from .sdk import unrealsdk

from typing import Any


from .log import log_debug_once

# UE3 EObjectFlags bits that mark an object as destroyed / pending kill.
DEAD_OBJECT_FLAGS = 0x0000200000000000 | 0x0000000200000000 | 0x8000 | 0x10000


def uobject_key(obj: Any) -> str:
    """Return the stable full path for a UObject wrapper, or ``""`` if unreadable."""
    if obj is None:
        return ""
    try:
        return str(obj._path_name())
    except Exception as exc:
        log_debug_once("object_identity.uobject_key", f"_path_name failed: {exc!r}")
        return ""


def cache_key(obj: Any) -> str:
    """Path-first cache key.

    Real UObjects always have a path.  The id fallback keeps offline/mock components that
    do not implement ``_path_name`` usable; it is deliberately namespaced so it can never
    collide with a real object path.
    """
    if obj is None:
        return ""
    return uobject_key(obj) or f"py:{id(obj)}"


def same_uobject(a: Any, b: Any) -> bool:
    """True when two wrappers refer to the same UE3 object."""
    if a is None or b is None:
        return False
    if a is b:
        return True
    ka, kb = uobject_key(a), uobject_key(b)
    return bool(ka) and ka == kb


def is_dead(obj: Any) -> bool:
    if obj is None:
        return True
    try:
        return bool(int(getattr(obj, "ObjectFlags", 0)) & DEAD_OBJECT_FLAGS)
    except Exception as exc:
        log_debug_once("object_identity.is_dead", f"ObjectFlags read failed: {exc!r}")
        return False


def wrap_weak(obj: Any) -> Any:
    """Wrap in the SDK WeakPointer when available; otherwise retain the object directly."""
    if obj is None:
        return None
    unreal_mod = getattr(unrealsdk, "unreal", None) if unrealsdk is not None else None
    wp_cls = getattr(unreal_mod, "WeakPointer", None) if unreal_mod is not None else None
    if wp_cls is not None:
        try:
            return wp_cls(obj)
        except Exception as exc:
            log_debug_once("object_identity.wrap_weak", f"WeakPointer failed: {exc!r}")
    return obj


def deref_weak(ref: Any) -> Any:
    """Dereference a WeakPointer/callable wrapper and filter destroyed objects."""
    if ref is None:
        return None
    if callable(ref) and not hasattr(ref, "_path_name") and not hasattr(ref, "PlayCustomAnim"):
        try:
            obj = ref()
        except Exception as exc:
            log_debug_once("object_identity.deref_weak", f"weak dereference failed: {exc!r}")
            return None
        return None if is_dead(obj) else obj
    return None if is_dead(ref) else ref
