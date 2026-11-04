"""Helpers shared by the secondary-attack systems."""

from __future__ import annotations

from typing import Any

from ..object_identity import deref_weak, same_uobject


def _combat() -> Any:
    """Import the primary combat package lazily to avoid initialization cycles."""
    from .. import combat

    return combat


def _same_object(ref: Any, obj: Any) -> bool:
    if ref is None or obj is None:
        return False
    return same_uobject(deref_weak(ref), obj)


def _clear_pose(pose_kind: str) -> None:
    """Restore a pose without depending on the associated throw/guard state."""
    try:
        _combat().reset_first_person_pose(pose_kind)
    except Exception:
        pass
