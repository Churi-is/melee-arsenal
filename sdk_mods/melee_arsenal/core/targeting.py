"""Pure-Python geometry used by melee targeting: a vector, a pawn snapshot, and a radius.

The runtime snapshots live enemies into `TargetCandidate` objects and filters them in Python
rather than calling `Trace`/`LineOfSightTo` from script, which is unreliable under pyunrealsdk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Vec3:
    x: float
    y: float
    z: float

    def __add__(self, other: Vec3) -> Vec3:
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vec3) -> Vec3:
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> Vec3:
        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)

    def dot(self, other: Vec3) -> float:
        return self.x * other.x + self.y * other.y + self.z * other.z

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def length_xy(self) -> float:
        return math.hypot(self.x, self.y)

    def normalized(self) -> Vec3:
        mag = self.length()
        if mag <= 1e-8:
            return Vec3(1.0, 0.0, 0.0)
        return Vec3(self.x / mag, self.y / mag, self.z / mag)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    """Immutable snapshot of a live hostile pawn collected safely from WorldInfo.PawnList."""

    target_id: str                # e.g. pawn._path_name()
    location: Vec3                # Actor center location in world UU
    collision_radius: float = 42.0
    collision_half_height: float = 65.0
    aim_height_offset: float = 28.0  # Chest/upper-torso offset above Location.Z

    @property
    def chest_point(self) -> Vec3:
        return Vec3(self.location.x, self.location.y, self.location.z + self.aim_height_offset)
