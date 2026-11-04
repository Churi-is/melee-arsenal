"""SDK-independent math for Melee Weapon secondary attacks (RMB / Left Trigger).

Two manufacturer-locked secondary attacks share this module:

1. **Throw** (Bandit, Torgue) — the blade is hurled along a ballistic arc whose launch speed,
   loft angle, flight time and range all come from the weapon's *weight*. Weight is defined by
   the manufacturers of the actual parts: every manufacturer's blade/grip hardware has a mass,
   and accessories add their own. A thrown blade strikes for exactly the same damage as a normal
   LMB hit (`100%`), then homes back into your hand even if you have moved away since the throw.
   Primary Fire is locked out while it is airborne.

2. **Guard** (Hyperion, Jakobs) — hold RMB to brace. The next incoming hit has the blade's rolled
   card Damage Reduction applied **twice** (`damage * (1 - DR) * (1 - DR)`), then the guard drops
   into a cooldown that depends on the weapon's parts. A hit landing inside the first
   `0.33 s` of the guard is a perfect **parry**: zero damage taken, and melee damage is reflected
   back at the attacker as a forced critical hit.

Maliwan gets neither attack — RMB simply stays inert on a Phaseblade.

Like the rest of `melee_arsenal.core` this module imports **zero** `unrealsdk` code, so every
value is unit-testable in plain CPython.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .parts.accessories import ACCESSORIES
from .parts.blades import BLADES
from .parts.grips import GRIPS
from .parts.manufacturers import MANUFACTURERS
from .parts.weapon import MeleeWeaponInstance

UU_PER_METER: float = 100.0


# Throw: weapon mass, ballistic arc & hit budget

# A thrown blade deals exactly the same damage as the normal LMB melee hit. (It started at 200%
# as a "thrown hits harder" pitch, but in play that made a single RMB the strongest attack in the
# whole kit — the throw's payoff is reach, not raw numbers.)
THROW_DAMAGE_MULTIPLIER: float = 1.0

# UE3/BL2 world gravity along Z (Unreal Units per second squared). The mod simulates the arc
# itself so the flight is identical on every framerate.
THROW_GRAVITY_Z: float = 980.0

# Launch speed at the reference mass. Heavier blades are launched slower: at 1.5x the reference
# mass a blade keeps ~66% of its speed (exponent 0.75), which is what makes Torgue throws read as
# short, high lobs while Bandit blades fly flatter and further.
THROW_LAUNCH_SPEED_UU_S: float = 1500.0
THROW_REFERENCE_WEIGHT_KG: float = 3.0
THROW_SPEED_WEIGHT_EXPONENT: float = 0.75
THROW_LAUNCH_SPEED_MIN_UU_S: float = 900.0
THROW_LAUNCH_SPEED_MAX_UU_S: float = 2200.0

# Heavier blades are also lofted higher so they still clear the ground on the way out.
THROW_LOFT_MIN_DEG: float = 6.0
THROW_LOFT_MAX_DEG: float = 24.0
THROW_LOFT_WEIGHT_MIN_KG: float = 1.5
THROW_LOFT_WEIGHT_MAX_KG: float = 4.5

# The loft is added *on top of where you are actually aiming*: look up and the blade leaves upward,
# look down and it leaves downward. These clamp the combined launch angle to something sane.
THROW_AIM_PITCH_MIN_DEG: float = -70.0
THROW_AIM_PITCH_MAX_DEG: float = 70.0

# The arc is traversed at this multiple of the "realistic" solve: the blade follows exactly the
# same path to exactly the same distance/apex, it just travels along it slower so the flight reads
# on screen instead of being a 0.7 s blur. Implemented by scaling time up and gravity down by the
# same factor squared, so the parabola is geometrically identical.
THROW_TIME_SCALE: float = 1.75

# Fail-safe ceiling, not a design target: a blade hurled off a cliff keeps falling until it hits
# something, and only gives up after this long.
THROW_MAX_FLIGHT_SEC: float = 30.0

# A downward throw never returns to launch height, so its flight time is solved from a descent
# budget instead: how far below the release point the blade is allowed to fall before it counts as
# landed (in Unreal Units). Terrain hits take priority — this only bounds an arc over open ground.
THROW_DESCENT_FLOOR_UU: float = 400.0

# After the planned arc is used up, look this far straight down: a hit means the blade landed
# there, a miss means it is over a drop (cliff/void) and keeps flying ballistically.
THROW_FALL_PROBE_UU: float = 600.0

# Hard limit on how far a blade may fall below the release point before it is recalled, so a throw
# into the void cannot leave you disarmed for the full 30 s fail-safe.
THROW_MAX_FALL_UU: float = 6000.0

# Rebound flight back into the player's hand (no damage, LMB stays locked until it lands). The
# blade homes on the player's *current* position (Minecraft-trident style), so you can keep moving.
THROW_RETURN_SEC: float = 0.35
# ...and this is only the give-up timer if the player somehow cannot be reached.
THROW_RETURN_MAX_SEC: float = 8.0
# Within this distance of the player the blade is considered caught.
THROW_CATCH_RADIUS_UU: float = 70.0
# The release swing plays first, then the first-person hands drop out of frame so the blade is
# visibly gone from them for the rest of the flight (and while it flies back).
THROW_HAND_EMPTY_DELAY_SEC: float = 0.12
# Extra forgiveness on the swept-segment hit test so a thrown blade cannot slip between legs.
THROW_HIT_PADDING_UU: float = 22.0

# Shared fraction of the weapon's total mass contributed by the blade and the grip. The blade is
# the bulk of the weapon; the grip (and its manufacturer) supplies the rest.
THROW_BLADE_MASS_FRACTION: float = 0.72
THROW_GRIP_MASS_FRACTION: float = 0.28



@dataclass(frozen=True, slots=True)
class ThrowFlightPlan:
    """Ballistic solution for one thrown Melee Weapon."""

    weight_kg: float
    launch_speed_uu_s: float
    launch_pitch_deg: float
    horizontal_speed_uu_s: float
    vertical_speed_uu_s: float
    flight_time_sec: float
    range_uu: float
    apex_uu: float
    return_time_sec: float
    # Effective gravity for THIS arc (`THROW_GRAVITY_Z / time_scale^2`), so the flight can be
    # slowed down without changing the distance it covers or how high it arcs.
    gravity_uu_s2: float = THROW_GRAVITY_Z
    # Fail-safe ceiling for the flight (see `THROW_MAX_FLIGHT_SEC`).
    max_flight_sec: float = THROW_MAX_FLIGHT_SEC

    @property
    def range_meters(self) -> float:
        return round(self.range_uu / UU_PER_METER, 2)

    @property
    def apex_meters(self) -> float:
        return round(self.apex_uu / UU_PER_METER, 2)

    def offset_at(self, elapsed_sec: float) -> tuple[float, float]:
        """Return `(forward_uu, height_uu)` of the blade `elapsed_sec` into the throw.

        The vertical term is a plain projectile integral, so the arc always matches the plan the
        item card prints. Time is deliberately *not* clamped to `flight_time_sec`: past the end of
        the planned arc the same parabola keeps being evaluated, which is what lets a blade thrown
        off a ledge keep falling instead of stopping in mid-air.
        """
        t = max(0.0, float(elapsed_sec))
        forward = self.horizontal_speed_uu_s * t
        height = self.vertical_speed_uu_s * t - 0.5 * self.gravity_uu_s2 * t * t
        return forward, height


def compute_throw_weight_kg(weapon: MeleeWeaponInstance) -> float:
    """Mass of a built Melee Weapon, driven by the manufacturers of its actual parts.

    Blade (72%) + Grip (28%) manufacturer hardware masses, plus a fixed add-on per accessory.
    Two Bandit Machetes with different grips therefore throw differently, and every Torgue
    Blast-Cleaver — a rocket-assisted slab — is heavier than any Bandit blade.
    """
    blade = BLADES[weapon.parts.blade_id]
    grip = GRIPS[weapon.parts.grip_id]
    blade_kg = MANUFACTURERS[blade.manufacturer].part_weight_kg
    grip_kg = MANUFACTURERS[grip.manufacturer].part_weight_kg
    weight = (
        blade_kg * THROW_BLADE_MASS_FRACTION
        + grip_kg * THROW_GRIP_MASS_FRACTION
        + ACCESSORIES[weapon.parts.accessory_id].throw_weight_kg
    )
    return round(max(0.5, weight), 3)


def build_throw_flight_plan(
    weight_kg: float, aim_pitch_deg: float = 0.0
) -> ThrowFlightPlan:
    """Solve the throw arc for a blade of `weight_kg`, launched from `aim_pitch_deg`.

    Heavier blades leave the hand slower *and* are lofted higher, so weight changes the shape of
    the arc (height, flight time, reach), not just its total distance. The weapon loft is added to
    the player's own aim angle, so looking up throws the blade up and looking down throws it down
    while gravity still bends every throw into an arc. The default `aim_pitch_deg=0.0` is the
    card's flat-ground reference throw.
    """
    weight = max(0.5, float(weight_kg))
    speed = THROW_LAUNCH_SPEED_UU_S * (THROW_REFERENCE_WEIGHT_KG / weight) ** THROW_SPEED_WEIGHT_EXPONENT
    speed = min(THROW_LAUNCH_SPEED_MAX_UU_S, max(THROW_LAUNCH_SPEED_MIN_UU_S, speed))

    loft_span = max(1e-6, THROW_LOFT_WEIGHT_MAX_KG - THROW_LOFT_WEIGHT_MIN_KG)
    loft_norm = min(1.0, max(0.0, (weight - THROW_LOFT_WEIGHT_MIN_KG) / loft_span))
    loft_deg = THROW_LOFT_MIN_DEG + (THROW_LOFT_MAX_DEG - THROW_LOFT_MIN_DEG) * loft_norm
    pitch_deg = min(
        THROW_AIM_PITCH_MAX_DEG,
        max(THROW_AIM_PITCH_MIN_DEG, loft_deg + float(aim_pitch_deg)),
    )
    pitch_rad = math.radians(pitch_deg)

    # Every blade is launched ballistically at `speed`, then the whole arc is *retimed*: the flight
    # takes `THROW_TIME_SCALE` times as long while covering the same ground, which is the same
    # thing as reducing both velocity components by k and gravity by k^2.
    k = max(0.05, THROW_TIME_SCALE)
    gravity = THROW_GRAVITY_Z / (k * k)
    horizontal = (speed * math.cos(pitch_rad)) / k
    vertical = (speed * math.sin(pitch_rad)) / k

    # Time until the arc returns to launch height (a full lob and fall). A downward launch never
    # returns, so its flight is solved from the descent budget instead — otherwise a slightly
    # downward aim ended the throw after a fraction of a second and the blade "stopped in mid-air".
    if vertical > 0.0:
        flight_time = (2.0 * vertical) / gravity
        apex = (vertical * vertical) / (2.0 * gravity)
    else:
        drop = max(1.0, THROW_DESCENT_FLOOR_UU)
        fall_speed = abs(vertical)
        flight_time = (
            -fall_speed + math.sqrt(fall_speed * fall_speed + 2.0 * gravity * drop)
        ) / gravity
        flight_time = max(flight_time, 0.30)
        apex = 0.0
    flight_time = min(THROW_MAX_FLIGHT_SEC, flight_time)

    return ThrowFlightPlan(
        weight_kg=round(weight, 3),
        launch_speed_uu_s=round(speed / k, 1),
        launch_pitch_deg=round(pitch_deg, 2),
        horizontal_speed_uu_s=round(horizontal, 1),
        vertical_speed_uu_s=round(vertical, 1),
        flight_time_sec=round(flight_time, 4),
        range_uu=round(horizontal * flight_time, 1),
        apex_uu=round(apex, 1),
        return_time_sec=THROW_RETURN_SEC,
        gravity_uu_s2=round(gravity, 2),
        max_flight_sec=THROW_MAX_FLIGHT_SEC,
    )


def point_segment_distance_uu(
    point: tuple[float, float, float],
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> float:
    """Shortest distance from `point` to the swept `start -> end` segment, in Unreal Units."""
    px, py, pz = point
    ax, ay, az = start
    bx, by, bz = end
    dx, dy, dz = bx - ax, by - ay, bz - az
    length_sq = dx * dx + dy * dy + dz * dz
    if length_sq <= 1e-6:
        return math.sqrt((px - ax) ** 2 + (py - ay) ** 2 + (pz - az) ** 2)
    t = ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / length_sq
    t = min(1.0, max(0.0, t))
    cx, cy, cz = ax + dx * t, ay + dy * t, az + dz * t
    return math.sqrt((px - cx) ** 2 + (py - cy) ** 2 + (pz - cz) ** 2)


# Guard: braced damage reduction, parry window & part-driven cooldown

# A hit inside this window after raising the guard is a perfect parry: zero damage taken and the
# melee damage is returned to the attacker as a forced critical hit.
GUARD_PARRY_WINDOW_SEC: float = 0.33

# Some engine builds report the RMB release in the same frame as the press. A guard dropped inside
# this window is treated as engine noise instead of a real release (the cooldown is >= 1.0 s, so a
# player can never legitimately re-guard this fast).
GUARD_RELEASE_DEBOUNCE_SEC: float = 0.05

# A parried blow is reflected as a forced critical: BL2's base 2.0x crit multiplier amplified by
# the blade's own card crit bonus (a Jakobs Sabre's +100% turns a parried hit into 4x reflected).
PARRY_REFLECT_BASE_CRIT_MULT: float = 2.0

# Safety valve: if the engine never reports the RMB release (missing StopZoom hook), a held guard
# still drops after this long so the player can never be stuck bracing forever.
GUARD_MAX_HOLD_SEC: float = 6.0

GUARD_COOLDOWN_MIN_SEC: float = 1.00
GUARD_COOLDOWN_MAX_SEC: float = 4.00
# Heavier builds take longer to reset the stance.
GUARD_COOLDOWN_WEIGHT_REFERENCE_KG: float = 2.40
GUARD_COOLDOWN_WEIGHT_SPAN: float = 0.25
# A matching-manufacturer grip balances the weapon, shortening the recovery.
GUARD_COOLDOWN_MATCHING_GRIP_SCALE: float = 0.85


def compute_guard_cooldown_sec(weapon: MeleeWeaponInstance) -> float:
    """Part-driven guard cooldown in seconds (roughly 1.0 s to 2.6 s).

    Blade manufacturer sets the base recovery, weapon mass (blade + grip manufacturer + accessory)
    stretches it, and a matching-manufacturer grip shortens it — so a light Hyperion Katana with a
    matching grip re-braces far sooner than a heavy Jakobs Sabre with a Torgue grip.
    """
    blade = BLADES[weapon.parts.blade_id]
    grip = GRIPS[weapon.parts.grip_id]
    accessory = ACCESSORIES[weapon.parts.accessory_id]

    base = MANUFACTURERS[blade.manufacturer].guard_cooldown_sec
    weight = compute_throw_weight_kg(weapon)
    cooldown = base * (
        1.0 - GUARD_COOLDOWN_WEIGHT_SPAN
        + GUARD_COOLDOWN_WEIGHT_SPAN * (weight / GUARD_COOLDOWN_WEIGHT_REFERENCE_KG)
    )
    if grip.manufacturer == blade.manufacturer:
        cooldown *= GUARD_COOLDOWN_MATCHING_GRIP_SCALE
    cooldown += accessory.guard_cooldown_delta_sec
    return round(min(GUARD_COOLDOWN_MAX_SEC, max(GUARD_COOLDOWN_MIN_SEC, cooldown)), 2)


def guard_effective_reduction_pct(card_damage_reduction_pct: float) -> int:
    """Effective guard mitigation after applying the card DR twice (for the item card)."""
    dr = min(100.0, max(0.0, float(card_damage_reduction_pct))) / 100.0
    return int(round((1.0 - (1.0 - dr) * (1.0 - dr)) * 100.0))


def compute_parry_reflect_damage(
    incoming_damage: float,
    crit_bonus_pct: float = 0.0,
    blade_crit_damage: float = 0.0,
) -> int:
    """Return the greater of the incoming forced critical and the blade's own critical damage."""
    incoming = max(0.0, float(incoming_damage))
    blade_crit = max(0.0, float(blade_crit_damage))
    if incoming <= 0.0 and blade_crit <= 0.0:
        return 0
    crit_mult = PARRY_REFLECT_BASE_CRIT_MULT * (1.0 + max(0.0, float(crit_bonus_pct)) / 100.0)
    return max(1, int(round(max(incoming * crit_mult, blade_crit))))
