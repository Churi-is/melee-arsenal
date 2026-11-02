"""SDK-independent math for Melee Weapon secondary attacks (RMB / Left Trigger).

Two manufacturer-locked secondary attacks share this module:

1. **Throw** (Bandit, Torgue) — the blade is hurled along a ballistic arc whose launch speed,
   loft angle, flight time and range all come from the weapon's *weight*. Weight is defined by
   the manufacturers of the actual parts: every manufacturer's blade/grip hardware has a mass,
   and accessories add their own. A thrown blade strikes for exactly `200%` of the same normal
   LMB hit, then rebounds to your hand after a short return flight (or after its arc expires if
   it never touches a wall or an enemy). Primary Fire is locked out while it is airborne.

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

from .catalog import ACCESSORIES, BLADES, GRIPS, MANUFACTURERS
from .enums import Manufacturer, SecondaryAttack
from .schemas import MeleeWeaponInstance

UU_PER_METER: float = 100.0


# ---------------------------------------------------------------------------
# Throw: weapon mass, ballistic arc & hit budget
# ---------------------------------------------------------------------------

# A thrown blade deals exactly 200% of the normal LMB melee hit ("hits hit with normal LMB
# melee but 200% damage").
THROW_DAMAGE_MULTIPLIER: float = 2.0

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

# Flight is normally solved from the arc itself; this is only the safety cap so a blade can never
# stay airborne indefinitely.
THROW_MAX_FLIGHT_SEC: float = 1.30
# Rebound flight back into the player's hand (no damage, LMB stays locked until it lands).
THROW_RETURN_SEC: float = 0.35
# The release swing plays first, then the first-person hands drop out of frame so the blade is
# visibly gone from them for the rest of the flight (and while it flies back).
THROW_HAND_EMPTY_DELAY_SEC: float = 0.12
# Extra forgiveness on the swept-segment hit test so a thrown blade cannot slip between legs.
THROW_HIT_PADDING_UU: float = 22.0

# Shared fraction of the weapon's total mass contributed by the blade and the grip. The blade is
# the bulk of the weapon; the grip (and its manufacturer) supplies the rest.
THROW_BLADE_MASS_FRACTION: float = 0.72
THROW_GRIP_MASS_FRACTION: float = 0.28

# Add-on mass per accessory (kg). A reinforced weighted spine makes a heavy throw heavier; a
# balanced pommel keeps it nimble.
ACCESSORY_WEIGHT_KG: dict[str, float] = {
    "Acc_None": 0.0,
    "Acc_Serrated": 0.10,
    "Acc_Counterweight": 0.18,
    "Acc_ExtendedTang": 0.15,
    "Acc_RazorFuller": 0.08,
    "Acc_BroadSweep": 0.22,
    "Acc_HeavySpine": 0.32,
    "Acc_FluxCapacitor": 0.20,
}


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

    @property
    def range_meters(self) -> float:
        return round(self.range_uu / UU_PER_METER, 2)

    @property
    def apex_meters(self) -> float:
        return round(self.apex_uu / UU_PER_METER, 2)

    def offset_at(self, elapsed_sec: float) -> tuple[float, float]:
        """Return `(forward_uu, height_uu)` of the blade `elapsed_sec` into the throw.

        The vertical term is a plain projectile integral, so the arc always matches the plan the
        item card prints.
        """
        t = max(0.0, min(float(elapsed_sec), self.flight_time_sec))
        forward = self.horizontal_speed_uu_s * t
        height = self.vertical_speed_uu_s * t - 0.5 * THROW_GRAVITY_Z * t * t
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
        + ACCESSORY_WEIGHT_KG.get(weapon.parts.accessory_id, 0.0)
    )
    return round(max(0.5, weight), 3)


def build_throw_flight_plan(weight_kg: float) -> ThrowFlightPlan:
    """Solve the throw arc for a blade of `weight_kg`.

    Heavier blades leave the hand slower *and* are lofted higher, so weight changes the shape of
    the arc (height, flight time, reach), not just its total distance.
    """
    weight = max(0.5, float(weight_kg))
    speed = THROW_LAUNCH_SPEED_UU_S * (THROW_REFERENCE_WEIGHT_KG / weight) ** THROW_SPEED_WEIGHT_EXPONENT
    speed = min(THROW_LAUNCH_SPEED_MAX_UU_S, max(THROW_LAUNCH_SPEED_MIN_UU_S, speed))

    loft_span = max(1e-6, THROW_LOFT_WEIGHT_MAX_KG - THROW_LOFT_WEIGHT_MIN_KG)
    loft_norm = min(1.0, max(0.0, (weight - THROW_LOFT_WEIGHT_MIN_KG) / loft_span))
    pitch_deg = THROW_LOFT_MIN_DEG + (THROW_LOFT_MAX_DEG - THROW_LOFT_MIN_DEG) * loft_norm
    pitch_rad = math.radians(pitch_deg)

    horizontal = speed * math.cos(pitch_rad)
    vertical = speed * math.sin(pitch_rad)

    # Time until the arc returns to launch height (a full lob and fall).
    air_time = (2.0 * vertical) / THROW_GRAVITY_Z
    flight_time = min(THROW_MAX_FLIGHT_SEC, max(0.12, air_time))
    apex = (vertical * vertical) / (2.0 * THROW_GRAVITY_Z)

    return ThrowFlightPlan(
        weight_kg=round(weight, 3),
        launch_speed_uu_s=round(speed, 1),
        launch_pitch_deg=round(pitch_deg, 2),
        horizontal_speed_uu_s=round(horizontal, 1),
        vertical_speed_uu_s=round(vertical, 1),
        flight_time_sec=round(flight_time, 4),
        range_uu=round(horizontal * flight_time, 1),
        apex_uu=round(apex, 1),
        return_time_sec=THROW_RETURN_SEC,
    )


def compute_throw_damage_scale() -> float:
    """Multiplier applied to the normal LMB breakdown for a thrown hit (200%)."""
    return THROW_DAMAGE_MULTIPLIER


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


# ---------------------------------------------------------------------------
# Guard: braced damage reduction, parry window & part-driven cooldown
# ---------------------------------------------------------------------------

# A hit inside this window after raising the guard is a perfect parry: zero damage taken and the
# melee damage is returned to the attacker as a forced critical hit.
GUARD_PARRY_WINDOW_SEC: float = 0.33

# Some engine builds report the RMB release in the same frame as the press. A guard dropped inside
# this window is treated as engine noise instead of a real release (the cooldown is >= 1.0 s, so a
# player can never legitimately re-guard this fast).
GUARD_RELEASE_DEBOUNCE_SEC: float = 0.05

# The guard applies the blade's rolled card Damage Reduction this many times
# (`(damage * card DR) * card DR`).
GUARD_DAMAGE_REDUCTION_APPLICATIONS: int = 2

# A parried blow is reflected as a forced critical: BL2's base 2.0x crit multiplier amplified by
# the blade's own card crit bonus (a Jakobs Sabre's +100% turns a parried hit into 4x reflected).
PARRY_REFLECT_BASE_CRIT_MULT: float = 2.0

# Safety valve: if the engine never reports the RMB release (missing StopZoom hook), a held guard
# still drops after this long so the player can never be stuck bracing forever.
GUARD_MAX_HOLD_SEC: float = 6.0

# Base guard cooldown per blade manufacturer (seconds). Hyperion's corporate tech recovers fast;
# Jakobs' heavy frontier steel needs a moment to reset.
GUARD_COOLDOWN_BY_MANUFACTURER: dict[Manufacturer, float] = {
    Manufacturer.HYPERION: 1.60,
    Manufacturer.JAKOBS: 2.20,
}
GUARD_COOLDOWN_DEFAULT_SEC: float = 2.00
GUARD_COOLDOWN_MIN_SEC: float = 1.00
GUARD_COOLDOWN_MAX_SEC: float = 4.00
# Heavier builds take longer to reset the stance.
GUARD_COOLDOWN_WEIGHT_REFERENCE_KG: float = 2.40
GUARD_COOLDOWN_WEIGHT_SPAN: float = 0.25
# A matching-manufacturer grip balances the weapon, shortening the recovery.
GUARD_COOLDOWN_MATCHING_GRIP_SCALE: float = 0.85
# Per-accessory cooldown deltas (seconds).
ACCESSORY_GUARD_COOLDOWN_DELTA: dict[str, float] = {
    "Acc_None": 0.0,
    "Acc_Serrated": 0.0,
    "Acc_Counterweight": -0.10,
    "Acc_ExtendedTang": 0.05,
    "Acc_RazorFuller": 0.0,
    "Acc_BroadSweep": 0.10,
    "Acc_HeavySpine": 0.25,
    "Acc_FluxCapacitor": 0.05,
}


def compute_guard_cooldown_sec(weapon: MeleeWeaponInstance) -> float:
    """Part-driven guard cooldown in seconds (roughly 1.0 s to 2.6 s).

    Blade manufacturer sets the base recovery, weapon mass (blade + grip manufacturer + accessory)
    stretches it, and a matching-manufacturer grip shortens it — so a light Hyperion Katana with a
    matching grip re-braces far sooner than a heavy Jakobs Sabre with a Torgue grip.
    """
    blade = BLADES[weapon.parts.blade_id]
    grip = GRIPS[weapon.parts.grip_id]
    accessory = ACCESSORIES[weapon.parts.accessory_id]

    base = GUARD_COOLDOWN_BY_MANUFACTURER.get(blade.manufacturer, GUARD_COOLDOWN_DEFAULT_SEC)
    weight = compute_throw_weight_kg(weapon)
    cooldown = base * (
        1.0 - GUARD_COOLDOWN_WEIGHT_SPAN
        + GUARD_COOLDOWN_WEIGHT_SPAN * (weight / GUARD_COOLDOWN_WEIGHT_REFERENCE_KG)
    )
    if grip.manufacturer == blade.manufacturer:
        cooldown *= GUARD_COOLDOWN_MATCHING_GRIP_SCALE
    cooldown += ACCESSORY_GUARD_COOLDOWN_DELTA.get(accessory.part_id, 0.0)
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
    """Damage reflected by a perfect parry: a forced critical hit at the blade's own scale.

    The old formula scaled only the *incoming* hit (`incoming * 2.0 * crit bonus`), so parrying a
    weak melee poke with a 112-damage blade reflected a number in the 50s even though the mod
    advertised a critical hit. The reflect now takes the larger of:

      * the incoming melee blow, forced to critical, and
      * the blade's own rolled critical hit (`compute_attack_damage(..., is_crit=True)`),

    so a parry always reads as a real critical hit from the weapon you are holding.
    """
    incoming = max(0.0, float(incoming_damage))
    blade_crit = max(0.0, float(blade_crit_damage))
    if incoming <= 0.0 and blade_crit <= 0.0:
        return 0
    crit_mult = PARRY_REFLECT_BASE_CRIT_MULT * (1.0 + max(0.0, float(crit_bonus_pct)) / 100.0)
    return max(1, int(round(max(incoming * crit_mult, blade_crit))))


def secondary_attack_for(manufacturer: Manufacturer) -> SecondaryAttack:
    """The manufacturer's RMB attack identity (`NONE` for Maliwan)."""
    spec = MANUFACTURERS.get(manufacturer)
    return spec.secondary_attack if spec is not None else SecondaryAttack.NONE
