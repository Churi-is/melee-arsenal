"""Pure-CPython UE3 3D vector, rotator, single-target ray, and frontal-cleave cone math.

Designed around BL2's verified pyunrealsdk constraints:
- Never rely on Python-called `Trace`/`FastTrace`/`LineOfSightTo` (broken in UE3 pyunrealsdk).
- Snapshot live enemy coordinates into `TargetCandidate` objects first, filter in pure Python,
  then emit exact `StrikeRay` endpoints for native `WillowWeapon.Behavior_Fire` so walls block
  strikes physically and weakspot hitboxes register natively.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .enums import TargetingMode
from .stats import ComputedCardStats

UE3_ROTATOR_UNITS_PER_CIRCLE: float = 65536.0
UE3_RAD_PER_ROT_UNIT: float = (2.0 * math.pi) / UE3_ROTATOR_UNITS_PER_CIRCLE


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
class Rotator:
    """Unreal Engine 3 Rotator (Pitch, Yaw, Roll in 65536 units = 360 degrees)."""

    pitch: int
    yaw: int
    roll: int = 0

    def forward_vector(self) -> Vec3:
        pitch_rad = self.pitch * UE3_RAD_PER_ROT_UNIT
        yaw_rad = self.yaw * UE3_RAD_PER_ROT_UNIT
        cp = math.cos(pitch_rad)
        sp = math.sin(pitch_rad)
        cy = math.cos(yaw_rad)
        sy = math.sin(yaw_rad)
        return Vec3(cp * cy, cp * sy, sp).normalized()

    def forward_xy(self) -> Vec3:
        yaw_rad = self.yaw * UE3_RAD_PER_ROT_UNIT
        return Vec3(math.cos(yaw_rad), math.sin(yaw_rad), 0.0)

    @classmethod
    def from_degrees(cls, pitch_deg: float, yaw_deg: float, roll_deg: float = 0.0) -> Rotator:
        scale = UE3_ROTATOR_UNITS_PER_CIRCLE / 360.0
        return cls(
            pitch=round(pitch_deg * scale),
            yaw=round(yaw_deg * scale),
            roll=round(roll_deg * scale),
        )


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


@dataclass(frozen=True, slots=True)
class StrikeRay:
    """A single bounded native trace/hit ray to execute via Behavior_Fire or hit handler."""

    origin: Vec3
    destination: Vec3
    direction: Vec3
    max_distance_uu: float
    intended_target_id: str | None = None
    horizontal_angle_deg: float = 0.0
    surface_distance_uu: float = 0.0


@dataclass(frozen=True, slots=True)
class SwingStrikePlan:
    """Complete geometric plan for one primary-fire melee weapon swing."""

    mode: TargetingMode
    rays: tuple[StrikeRay, ...]
    matched_target_ids: tuple[str, ...]


def horizontal_angle_degrees(forward_xy: Vec3, delta: Vec3) -> float:
    """Unsigned angle in degrees [0, 180] in the XY plane between view yaw and target."""
    d_xy_len = delta.length_xy()
    if d_xy_len <= 1e-6:
        return 0.0
    cos_theta = max(-1.0, min(1.0, (forward_xy.x * delta.x + forward_xy.y * delta.y) / d_xy_len))
    return math.degrees(math.acos(cos_theta))


def cone_angle_3d_degrees(forward: Vec3, delta: Vec3) -> float:
    """Unsigned 3D angle in degrees [0, 180] between view forward vector and target point."""
    mag = delta.length()
    if mag <= 1e-6:
        return 0.0
    cos_theta = max(-1.0, min(1.0, forward.dot(delta) / mag))
    return math.degrees(math.acos(cos_theta))


def plan_swing_strikes(
    eye_origin: Vec3,
    view_rot: Rotator,
    card: ComputedCardStats,
    candidates: Sequence[TargetCandidate],
) -> SwingStrikePlan:
    """Build the strike plan for either `SINGLE_TARGET` or `FRONTAL_CLEAVE` melee weapons.

    - `SINGLE_TARGET` (Hyperion Katana, Jakobs Sabre):
      Always emits exactly 1 crosshair ray of length `card.range_uu` directly along `view_rot`
      so the player aims weakspot critical hits with surgical precision (and hits destructibles/surfaces).
      Also identifies the closest-to-reticle candidate within `arc_degrees/2` if present for logging/assist.

    - `FRONTAL_CLEAVE` (Bandit Machete, Maliwan Phaseblade, Torgue Blast-Cleaver):
      Finds EVERY hostile candidate within `card.range_uu` (measured to the enemy's collision cylinder edge!)
      and within `±(card.cleave_arc_degrees / 2)` horizontal yaw & `±card.pitch_limit_degrees` vertical pitch.
      Emits one bounded ray per valid target aimed at its chest, sorted closest-first, capped at `max_targets`.
      If 0 enemies are in range, emits 1 short center feedback ray so hitting walls/barrels still sparks & clangs!
    """
    forward = view_rot.forward_vector()
    forward_xy = view_rot.forward_xy()
    reach = card.range_uu
    half_arc = card.cleave_arc_degrees * 0.5

    if card.targeting_mode == TargetingMode.SINGLE_TARGET:
        best_target: tuple[float, float, TargetCandidate] | None = None
        for cand in candidates:
            delta = cand.chest_point - eye_origin
            center_dist = delta.length()
            surface_dist = max(0.0, center_dist - cand.collision_radius)
            if surface_dist > reach:
                continue
            ang_3d = cone_angle_3d_degrees(forward, delta)
            if ang_3d <= half_arc:
                key = (ang_3d, surface_dist)
                if best_target is None or key < (best_target[0], best_target[1]):
                    best_target = (ang_3d, surface_dist, cand)

        dest = eye_origin + (forward * reach)
        matched_id = best_target[2].target_id if best_target is not None else None
        ray = StrikeRay(
            origin=eye_origin,
            destination=dest,
            direction=forward,
            max_distance_uu=reach,
            intended_target_id=matched_id,
            horizontal_angle_deg=round(best_target[0], 2) if best_target is not None else 0.0,
            surface_distance_uu=round(best_target[1], 1) if best_target is not None else reach,
        )
        return SwingStrikePlan(
            mode=TargetingMode.SINGLE_TARGET,
            rays=(ray,),
            matched_target_ids=(matched_id,) if matched_id else (),
        )

    # FRONTAL_CLEAVE: gather all enemies in frontal arc & range
    hits: list[tuple[float, float, TargetCandidate, Vec3]] = []
    for cand in candidates:
        aim_pt = cand.chest_point
        delta = aim_pt - eye_origin
        center_dist = delta.length()
        surface_dist = max(0.0, center_dist - cand.collision_radius)
        if surface_dist > reach:
            continue

        horiz_ang = horizontal_angle_degrees(forward_xy, delta)
        if horiz_ang > half_arc:
            continue

        # Vertical pitch angle relative to player's view pitch so looking up/down cleaves naturally
        ang_3d = cone_angle_3d_degrees(forward, delta)
        if ang_3d > max(half_arc, card.pitch_limit_degrees):
            continue

        hits.append((surface_dist, horiz_ang, cand, delta))

    hits.sort(key=lambda row: (row[0], row[1]))
    hits = hits[: card.max_targets]

    if not hits:
        # Swing into empty air / barrel / wall: 1 center feedback ray clamped to blade reach
        center_dest = eye_origin + (forward * reach)
        return SwingStrikePlan(
            mode=TargetingMode.FRONTAL_CLEAVE,
            rays=(
                StrikeRay(
                    origin=eye_origin,
                    destination=center_dest,
                    direction=forward,
                    max_distance_uu=reach,
                ),
            ),
            matched_target_ids=(),
        )

    rays: list[StrikeRay] = []
    matched_ids: list[str] = []
    for surface_dist, horiz_ang, cand, delta in hits:
        dir_vec = delta.normalized()
        # Allow the ray to reach the enemy's chest center (bounded by reach + collision_radius)
        ray_len = min(delta.length() + 12.0, reach + cand.collision_radius + 12.0)
        dest = eye_origin + (dir_vec * ray_len)
        rays.append(
            StrikeRay(
                origin=eye_origin,
                destination=dest,
                direction=dir_vec,
                max_distance_uu=round(ray_len, 1),
                intended_target_id=cand.target_id,
                horizontal_angle_deg=round(horiz_ang, 2),
                surface_distance_uu=round(surface_dist, 1),
            )
        )
        matched_ids.append(cand.target_id)

    return SwingStrikePlan(
        mode=TargetingMode.FRONTAL_CLEAVE,
        rays=tuple(rays),
        matched_target_ids=tuple(matched_ids),
    )
