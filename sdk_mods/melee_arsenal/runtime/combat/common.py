"""Small UE3 object and transform helpers shared by combat modules."""

from __future__ import annotations

import copy
import math
from typing import Any

import unrealsdk

from ..log import log_debug_once


def _make_vec_from(template_vec: Any, x: float, y: float, z: float) -> Any:
    if template_vec is not None:
        try:
            v = copy.copy(template_vec)
            v.X = float(x)
            v.Y = float(y)
            v.Z = float(z)
            return v
        except Exception as exc:
            log_debug_once("combat._make_vec_from.copy", f"_make_vec_from copy failed: {exc!r}")
    try:
        return unrealsdk.make_struct("Vector", X=float(x), Y=float(y), Z=float(z))
    except Exception as exc:
        log_debug_once("combat._make_vec_from.make_struct", f"_make_vec_from make_struct failed: {exc!r}")
        return (float(x), float(y), float(z))


def _s16(val: int) -> int:
    v = int(val) & 0xFFFF
    return v - 0x10000 if (v & 0x8000) else v


def _rotator_add(base: int, delta: float) -> int:
    """Add a delta to a 16-bit UE3 rotator component, wrapping like the engine does."""
    return _s16(int(round(int(base) + float(delta))))


def _rot_to_forward(rot: Any) -> tuple[float, float, float]:
    pitch = _s16(getattr(rot, "Pitch", 0)) * (math.pi / 32768.0)
    yaw = _s16(getattr(rot, "Yaw", 0)) * (math.pi / 32768.0)
    cp = math.cos(pitch)
    return (cp * math.cos(yaw), cp * math.sin(yaw), math.sin(pitch))
