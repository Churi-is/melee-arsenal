"""Final-part geometry: the R3 parametric meshes **with the R4 revisions baked in**.

The old pipeline built an R3 kit, zipped it, and then ran a second `r4_builder.py` pass that
re-parsed `generate_blender.py` with `ast` to re-create the same meshes and apply its revision on
top. That two-revision split is gone: `build_parts()` emits *final* geometry directly, so there is
exactly one place that defines a part and no revision stage that has to re-derive R3.

The R4 revision rules that used to live in `tools/r4_builder.py` are applied here, in
`apply_final_revisions()`:

* blade-slot geometry depth halved (including its decorative overlays),
* the element receiver moved from `x = -0.12` to `x = -0.065`,
* front-only grip/blade detail components mirrored onto the rear face with reversed winding,
* the Reinforced Heavy Spine reduced to one third of its depth,
* the Flared Cleave Guard centred as a single plate,
* Torgue's grip rebuilt as a hexagonal shell with four continuous wraparound ribs.

Coordinates here are Blender working space (metres, `+Z` blade tip, `-Y` display/front face);
`glb.py` performs the glTF conversion (`x, z, -y`) at export time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Sequence

Vec = tuple[float, float, float]

# ---------------------------------------------------------------------------
# Final-revision constants (R4 values, kept verbatim so the geometry matches the shipped kit)
# ---------------------------------------------------------------------------
BLADE_CORE_DEPTH_MULTIPLIER: float = 0.5      # blade slot depth (was 1.0 in R3)
BLADE_THICKNESS_M: dict[str, float] = {
    "Hyperion": 0.075,
    "Bandit": 0.11,
    "Jakobs": 0.065,
    "Maliwan": 0.095,
    "Torgue": 0.16,
}
HEAVY_SPINE_DEPTH_DIVISOR: float = 3.0
CLEAVE_GUARD_PLATE_SHIFT: float = 0.025       # centres the guard's continuous plate
ELEMENT_RECEIVER_X_SHIFT: float = 0.055       # -0.12 -> -0.065
ELEMENT_RECEIVER_X: float = -0.12
ELEMENT_FACE_GAP_M: float = 0.04375 - 0.001
ACCESSORY_FACE_CLIP_M: float = 0.0015
TORGUE_BAR_SOURCE_Y: float = -0.095
TORGUE_BAR_Y_CENTER: float = -0.087
TORGUE_BAR_Y_SCALE: float = 0.028 / 0.045
TORGUE_SHELL_Z: tuple[float, float] = (-0.415, -0.105)
TORGUE_SHELL_RADIUS: float = 0.094
TORGUE_RIB_RADIUS: float = 0.104
TORGUE_RIB_INNER_RADIUS: float = 0.092
TORGUE_RIB_HALF_HEIGHT: float = 0.015
TORGUE_SHELL_SIDES: int = 6
TORGUE_RIB_COUNT: int = 4

#: Parts whose decorative detail lives on the front skin only, exported as paired
#: `FACE_FRONT` / `FACE_BACK` node instances instead of mirrored triangles.
BIFACIAL_EXCLUDED: frozenset[str] = frozenset({"accessory_balanced_pommel", "accessory_flared_cleave_guard"})


def is_bifacial(part_id: str) -> bool:
    """True when a part mounts as two mirrored instances rather than mirrored geometry."""
    if part_id.startswith("element_"):
        return True
    return part_id.startswith("accessory_") and part_id not in BIFACIAL_EXCLUDED


# ---------------------------------------------------------------------------
# Mesh primitives
# ---------------------------------------------------------------------------
@dataclass
class Component:
    """One primitive call, so revision rules can target e.g. 'the Torgue grip's box body'."""

    kind: str
    start: int
    end: int
    args: tuple[Any, ...]


@dataclass
class Face:
    """A flat-shaded polygon plus the UV template it should be projected/unwrapped from."""

    verts: tuple[Vec, ...]
    tag: str
    uv_template: int | None = None       # face index to copy the painted island from
    uv_flip: bool = False                # reversed winding copies (mirrored detail)
    uv_mode: str = "exact"               # "exact" copies the template's UVs verbatim

    def mirrored(self) -> "Face":
        """Mirror across the `Y` (depth) axis, matching R4's rear-face duplication."""
        flipped = tuple((x, -y, z) for x, y, z in reversed(self.verts))
        return Face(flipped, self.tag, self.uv_template, not self.uv_flip, self.uv_mode)


@dataclass
class Part:
    """A final, socket-carrying part ready for UV unwrap and painting."""

    part_id: str
    slot: str
    manufacturer: str
    faces: list[Face]
    sockets_blender: dict[str, Vec]
    meta: dict[str, Any] = field(default_factory=dict)
    bifacial: bool = False
    fit_inner_shift_m: float = 0.0
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def triangles(self) -> int:
        return sum(len(f.verts) - 2 for f in self.faces)

    @property
    def vertex_count(self) -> int:
        return sum(len(f.verts) for f in self.faces)


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / max(1, len(items))


class Geo:
    """Minimal polygon soup builder (the R3 `Geo` class, minus its Blender dependency)."""

    def __init__(self) -> None:
        self.v: list[Vec] = []
        self.f: list[tuple[int, ...]] = []
        self.tags: list[str] = []
        self.components: list[Component] = []

    # -- primitives -------------------------------------------------------
    def poly(self, verts: Sequence[Sequence[float]], tag: str) -> None:
        start = len(self.v)
        self.v.extend((float(p[0]), float(p[1]), float(p[2])) for p in verts)
        self.f.append(tuple(range(start, start + len(verts))))
        self.tags.append(tag)

    def box(self, c: Sequence[float], s: Sequence[float], tag: str = "paint") -> None:
        start = len(self.f)
        x, y, z = c
        a, b, d = (q / 2.0 for q in s)
        corners = [
            (x + u * a, y + w * b, z + t * d)
            for u, w, t in (
                (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
            )
        ]
        for ids in ((0, 3, 2, 1), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (4, 5, 6, 7)):
            self.poly([corners[i] for i in ids], tag)
        self.components.append(Component("box", start, len(self.f), (tuple(c), tuple(s), tag)))

    def profile(
        self,
        pts: Sequence[Sequence[float]],
        thick: float = 0.07,
        y: float = 0.0,
        tag: str = "metal",
        bevel: float = 0.018,
        sharp: bool = False,
    ) -> None:
        start = len(self.f)
        cx = _mean(p[0] for p in pts)
        cz = _mean(p[1] for p in pts)
        inner = [
            (x + (cx - x) * (0.15 if sharp else 0.07), z + (cz - z) * (0.075 if sharp else 0.04))
            for x, z in pts
        ]
        n = len(pts)
        if sharp:
            bevel = thick * 0.46
        for sign in (-1, 1):
            cap = [(x, y + sign * thick / 2, z) for x, z in inner]
            self.poly(cap if sign < 0 else list(reversed(cap)), tag)
            for i in range(n):
                j = (i + 1) % n
                self.poly(
                    [
                        (inner[i][0], y + sign * thick / 2, inner[i][1]),
                        (inner[j][0], y + sign * thick / 2, inner[j][1]),
                        (pts[j][0], y + sign * (thick / 2 - bevel), pts[j][1]),
                        (pts[i][0], y + sign * (thick / 2 - bevel), pts[i][1]),
                    ],
                    "edge" if sharp else ("trim" if tag == "metal" else "dark"),
                )
        for i in range(n):
            j = (i + 1) % n
            self.poly(
                [
                    (pts[i][0], y - thick / 2 + bevel, pts[i][1]),
                    (pts[j][0], y - thick / 2 + bevel, pts[j][1]),
                    (pts[j][0], y + thick / 2 - bevel, pts[j][1]),
                    (pts[i][0], y + thick / 2 - bevel, pts[i][1]),
                ],
                "dark",
            )
        self.components.append(Component("profile", start, len(self.f), (tuple(map(tuple, pts)), thick, y, tag)))

    def cyl(
        self,
        c: Sequence[float],
        r: float,
        depth: float,
        tag: str = "metal",
        n: int = 8,
        axis: str = "z",
        r2: float | None = None,
    ) -> None:
        start = len(self.f)
        radius_top = r if r2 is None else r2

        def ring_point(angle: float, h: float, rad: float) -> Vec:
            xyz = (rad * math.cos(angle), rad * math.sin(angle), h)
            if axis == "y":
                xyz = (xyz[0], xyz[2], xyz[1])
            return tuple(xyz[i] + c[i] for i in range(3))  # type: ignore[return-value]

        rings = [
            [ring_point(2 * math.pi * i / n, h, rad) for i in range(n)]
            for h, rad in ((-depth / 2, r), (depth / 2, radius_top))
        ]
        self.poly(list(reversed(rings[0])), "dark")
        self.poly(rings[1], tag)
        for i in range(n):
            j = (i + 1) % n
            self.poly([rings[0][i], rings[0][j], rings[1][j], rings[1][i]], tag)
        self.components.append(Component("cyl", start, len(self.f), (tuple(c), r, depth, tag, n, axis)))


# ---------------------------------------------------------------------------
# R3 part shapes (verbatim silhouettes / dimensions)
# ---------------------------------------------------------------------------
def _blade(maker: str) -> Geo:
    g = Geo()
    g.box((0, 0, 0.075), (0.23, 0.15, 0.15), "paint")
    g.box((0, 0, -0.015), (0.31, 0.20, 0.055), "dark")
    # Universal front rail and element receiver give every accessory a supported mounting surface.
    g.box((0, -0.067, 0.16), (0.22, 0.045, 0.13), "paint")
    g.cyl((-0.12, -0.075, 0.12), 0.043, 0.025, "dark", axis="y")

    if maker == "Hyperion":
        pts = [(-0.12, 0.13), (0.12, 0.13), (0.16, 0.76), (0.07, 1.29), (-0.045, 1.45), (-0.10, 1.13)]
        g.profile(pts, 0.075, sharp=True)
        g.profile([(-0.09, 0.22), (-0.045, 0.25), (0.015, 1.24), (-0.025, 1.18)], 0.016, -0.048, "paint", 0.004)
        for z in (0.34, 0.44, 0.54):
            g.box((0.06, -0.047, z), (0.065, 0.018, 0.025), "accent")
        g.box((0, 0, 0.005), (0.44, 0.18, 0.065), "paint")
    elif maker == "Bandit":
        pts = [
            (-0.12, 0.14), (0.13, 0.14), (0.20, 0.45), (0.30, 0.62), (0.32, 1.10), (0.13, 1.25),
            (-0.30, 1.19), (-0.32, 0.96), (-0.23, 0.88), (-0.29, 0.77), (-0.20, 0.69), (-0.24, 0.56),
            (-0.14, 0.46),
        ]
        g.profile(pts, 0.11, sharp=True)
        g.profile([(-0.09, 0.23), (0.09, 0.25), (0.18, 1.09), (0.07, 1.15), (-0.10, 1.08)], 0.035, -0.065, "paint", 0.008)
        for x, z in ((0, 0.88), (0.03, 1.02), (0.04, 0.70)):
            g.cyl((x, -0.09, z), 0.036, 0.013, "dark", axis="y")
        g.box((0.02, 0, 0.0), (0.45, 0.2, 0.09), "paint")
    elif maker == "Jakobs":
        pts = [(-0.105, 0.12), (0.105, 0.12), (0.12, 0.53), (0.21, 0.98), (0.33, 1.39), (0.23, 1.28), (0.02, 0.94), (-0.08, 0.50)]
        g.profile(pts, 0.065, sharp=True)
        g.profile([(-0.04, 0.20), (-0.005, 0.23), (0.09, 0.88), (0.20, 1.20), (0.12, 0.86)], 0.012, -0.041, "trim", 0.003)
        g.profile([(-0.30, 0.06), (-0.31, -0.015), (-0.15, -0.075), (0.19, -0.035), (0.29, 0.05), (0.14, 0.015), (-0.1, 0.01)], 0.12, 0, "trim")
    elif maker == "Maliwan":
        pts = [(-0.11, 0.14), (0.11, 0.14), (0.17, 0.44), (0.23, 0.64), (0.31, 1.12), (0.18, 1.38), (0.17, 1.08), (-0.03, 0.87), (-0.15, 0.56)]
        g.profile(pts, 0.095, tag="paint", sharp=True)
        g.profile([(0.015, 0.25), (0.085, 0.28), (0.11, 0.57), (0.22, 1.08), (0.18, 1.20), (0.04, 0.70)], 0.026, -0.059, "accent", 0.005)
        g.profile([(-0.22, 0.04), (-0.14, -0.045), (0.16, -0.045), (0.24, 0.04), (0.12, 0.10), (-0.12, 0.10)], 0.15, tag="dark")
    else:  # Torgue
        pts = [
            (-0.14, 0.14), (0.14, 0.14), (0.20, 0.41), (0.29, 0.46), (0.23, 0.56), (0.32, 0.63),
            (0.25, 0.73), (0.34, 0.81), (0.28, 0.91), (0.36, 1.01), (0.26, 1.19), (-0.18, 1.19),
            (-0.24, 1.07), (-0.18, 0.82),
        ]
        g.profile(pts, 0.16, sharp=True)
        g.profile([(-0.1, 0.27), (0.08, 0.29), (0.15, 1.09), (-0.12, 1.09)], 0.045, -0.098, "paint")
        for z in (0.44, 0.58, 0.72, 0.86):
            g.box((0, -0.13, z), (0.12, 0.025, 0.06), "dark")
        for x in (-0.23, 0.23):
            g.cyl((x, 0, -0.01), 0.065, 0.16, "trim", axis="y")
        g.box((0, 0, -0.005), (0.45, 0.23, 0.095), "paint")
    return g


def _grip(maker: str) -> Geo:
    g = Geo()
    g.cyl((0, 0, -0.045), 0.09, 0.09, "metal")
    g.cyl((0, 0, -0.27), 0.069, 0.37, "dark", r2=0.076)
    if maker == "Hyperion":
        g.box((0, -0.045, -0.26), (0.12, 0.075, 0.31), "paint")
        for z in (-0.16, -0.23, -0.30, -0.37):
            g.box((0, -0.09, z), (0.1, 0.025, 0.024), "dark")
    elif maker == "Bandit":
        for i in range(6):
            g.cyl((0.005 * (i % 2), 0, -0.105 - i * 0.056), 0.083, 0.042, "trim" if i % 2 else "paint", n=6)
    elif maker == "Jakobs":
        g.cyl((0, 0, -0.26), 0.077, 0.32, "paint", n=8, r2=0.065)
        for z in (-0.11, -0.42):
            g.cyl((0, 0, z), 0.084, 0.035, "trim")
        for z in (-0.21, -0.32):
            g.cyl((0, -0.074, z), 0.019, 0.012, "trim", axis="y")
    elif maker == "Maliwan":
        g.profile([(-0.07, -0.10), (0.07, -0.1), (0.065, -0.40), (0, -0.44), (-0.065, -0.40)], 0.15, tag="paint")
        g.box((0, -0.087, -0.26), (0.032, 0.018, 0.21), "accent")
    else:  # Torgue
        g.box((0, 0, -0.26), (0.18, 0.16, 0.31), "paint")
        for z in (-0.16, -0.24, -0.32, -0.40):
            g.box((0, -0.095, z), (0.19, 0.045, 0.036), "dark")
    g.cyl((0, 0, -0.465), 0.09, 0.07, "trim")
    return g


def _accessory(acc_id: str) -> Geo:
    g = Geo()
    if acc_id == "balanced_pommel":
        g.cyl((0, 0, -0.025), 0.11, 0.05, "dark")
        g.cyl((0, 0, -0.09), 0.11, 0.09, "metal", n=6, r2=0.08)
        g.box((0, -0.086, -0.09), (0.085, 0.035, 0.05), "trim")
        return g

    g.box((0, -0.018, 0), (0.17, 0.05, 0.08), "dark")
    g.box((0, -0.049, 0), (0.13, 0.025, 0.06), "trim")
    if acc_id == "serrated_edge":
        pts = [(0.035, 0.02), (0.095, 0.02)]
        for i in range(5):
            pts.extend([(0.20, 0.07 + i * 0.09), (0.10, 0.105 + i * 0.09)])
        pts += [(0.07, 0.53), (0.025, 0.49)]
        g.profile(pts, 0.035, -0.04, "metal", 0.005)
    elif acc_id == "extended_blade_fuller":
        g.profile([(-0.035, 0.01), (0.035, 0.01), (0.048, 0.53), (0, 0.70), (-0.048, 0.53)], 0.04, -0.05, "metal", 0.006)
        g.box((0, -0.08, 0.30), (0.025, 0.018, 0.43), "trim")
    elif acc_id == "monofilament_honing":
        for x in (-0.057, 0.057):
            g.cyl((x, -0.042, 0.28), 0.014, 0.54, "trim", n=6)
        for z in (0.07, 0.49):
            g.box((0, -0.045, z), (0.16, 0.06, 0.045), "dark")
        g.box((0, -0.075, 0.28), (0.018, 0.015, 0.42), "accent")
    elif acc_id == "flared_cleave_guard":
        g.profile(
            [(-0.36, 0.08), (-0.30, -0.07), (-0.13, -0.12), (0.13, -0.12), (0.30, -0.07),
             (0.36, 0.08), (0.23, 0.035), (0.10, -0.035), (-0.1, -0.035), (-0.23, 0.035)],
            0.1, -0.025, "metal",
        )
        for x in (-0.27, 0.27):
            g.box((x, -0.09, 0.0), (0.065, 0.035, 0.045), "trim")
    elif acc_id == "reinforced_heavy_spine":
        g.box((0.09, -0.045, 0.27), (0.11, 0.10, 0.55), "paint")
        for z in (0.08, 0.22, 0.36, 0.50):
            g.box((0.09, -0.11, z), (0.15, 0.04, 0.06), "dark")
            g.cyl((0.09, -0.14, z), 0.022, 0.015, "trim", axis="y", n=6)
    else:  # overclocked_elemental_coil
        g.box((0.12, -0.05, 0.14), (0.14, 0.11, 0.25), "dark")
        for z in (0.06, 0.115, 0.17, 0.225):
            g.cyl((0.12, -0.05, z), 0.087, 0.028, "accent")
        g.box((0.12, -0.05, 0.29), (0.16, 0.15, 0.04), "trim")
    return g


def _element() -> Geo:
    g = Geo()
    g.cyl((0, -0.014, 0), 0.048, 0.036, "dark", axis="y")
    g.cyl((0, -0.04, 0), 0.037, 0.025, "accent", n=6, axis="y")
    g.box((0, -0.055, 0), (0.017, 0.014, 0.026), "trim")
    return g


# ---------------------------------------------------------------------------
# Catalogue metadata (shipped in `socket_manifest.json`)
# ---------------------------------------------------------------------------
BLADES: dict[str, dict[str, Any]] = {
    "Hyperion": {
        "archetype": "Precision tanto", "targeting_mode": "SINGLE_TARGET", "base_reach_m": 1.45,
        "base_damage": 82, "base_swings_per_second": 1.65,
        "element_policy": {"mode": "OPTIONAL", "allowed": ["Shock"]}, "signature_title": "Ronin",
    },
    "Bandit": {
        "archetype": "Scrap cleaver", "targeting_mode": "FRONTAL_CLEAVE", "base_reach_m": 1.25,
        "base_damage": 106, "base_swings_per_second": 1.05,
        "element_policy": {"mode": "OPTIONAL", "allowed": ["Fire", "Corrosive", "Slag"]},
        "signature_title": "Chopper",
    },
    "Jakobs": {
        "archetype": "Frontier sabre", "targeting_mode": "SINGLE_TARGET", "base_reach_m": 1.39,
        "base_damage": 98, "base_swings_per_second": 1.4,
        "element_policy": {"mode": "FORBIDDEN", "allowed": []}, "signature_title": "Cavalier",
    },
    "Maliwan": {
        "archetype": "Ion falchion", "targeting_mode": "FRONTAL_CLEAVE", "base_reach_m": 1.38,
        "base_damage": 72, "base_swings_per_second": 1.5,
        "element_policy": {"mode": "REQUIRED", "allowed": ["Fire", "Shock", "Corrosive", "Slag"]},
        "signature_title": "Seraphim",
    },
    "Torgue": {
        "archetype": "Breach cutter", "targeting_mode": "FRONTAL_CLEAVE", "base_reach_m": 1.19,
        "base_damage": 132, "base_swings_per_second": 0.82,
        "element_policy": {"mode": "OPTIONAL", "allowed": ["Explosive"]}, "signature_title": "Detonator",
    },
}

GRIPS: dict[str, dict[str, float]] = {
    "Hyperion": {"damage": -0.04, "speed": 0.10, "reach": 0.0, "crit": 0.12, "elemental": 0.0},
    "Bandit": {"damage": 0.12, "speed": -0.08, "reach": 0.05, "crit": -0.06, "elemental": 0.0},
    "Jakobs": {"damage": 0.08, "speed": 0.02, "reach": 0.0, "crit": 0.16, "elemental": -0.10},
    "Maliwan": {"damage": -0.06, "speed": 0.06, "reach": 0.02, "crit": 0.0, "elemental": 0.20},
    "Torgue": {"damage": 0.16, "speed": -0.12, "reach": 0.04, "crit": -0.08, "elemental": 0.05},
}

ACCESSORIES: dict[str, dict[str, Any]] = {
    "serrated_edge": {"name": "Serrated Edge", "modifier": {"damage": 0.12}, "restriction": "ANY"},
    "balanced_pommel": {"name": "Balanced Pommel", "modifier": {"speed": 0.12}, "restriction": "ANY", "target_socket": "SOCKET_POMMEL"},
    "extended_blade_fuller": {"name": "Extended Blade Fuller", "modifier": {"reach": 0.14}, "restriction": "ANY"},
    "monofilament_honing": {"name": "Monofilament Honing", "modifier": {"crit": 0.22}, "restriction": "SINGLE_TARGET"},
    "flared_cleave_guard": {"name": "Flared Cleave Guard", "modifier": {"cleave_arc_degrees": 20}, "restriction": "FRONTAL_CLEAVE"},
    "reinforced_heavy_spine": {"name": "Reinforced Heavy Spine", "modifier": {"damage": 0.22, "speed": -0.12}, "restriction": "ANY"},
    "overclocked_elemental_coil": {"name": "Overclocked Elemental Coil", "modifier": {"status_chance": 0.18, "dot": 0.20}, "restriction": "ELEMENT_REQUIRED"},
}

COMBINATIONS: tuple[tuple[str, str, str, str | None, str | None], ...] = (
    ("01_Ronin", "Hyperion", "Hyperion", "monofilament_honing", "Shock"),
    ("02_Chopper", "Bandit", "Bandit", "serrated_edge", "Fire"),
    ("03_Cavalier", "Jakobs", "Jakobs", "balanced_pommel", None),
    ("04_Seraphim", "Maliwan", "Maliwan", "overclocked_elemental_coil", "Corrosive"),
    ("05_Detonator", "Torgue", "Torgue", "reinforced_heavy_spine", "Explosive"),
    ("06_Crossbrand", "Hyperion", "Bandit", "extended_blade_fuller", None),
    ("07_Slag_Cleaver", "Bandit", "Maliwan", "flared_cleave_guard", "Slag"),
)

PART_ORDER: tuple[str, ...] = (
    *[f"blade_{m.lower()}" for m in BLADES],
    *[f"grip_{m.lower()}" for m in BLADES],
    *[f"accessory_{a}" for a in ACCESSORIES],
    *[f"element_{e.lower()}" for e in ("Fire", "Shock", "Corrosive", "Slag", "Explosive")],
)


# ---------------------------------------------------------------------------
# Final-revision assembly
# ---------------------------------------------------------------------------
def _torque_hex_faces(geo: Geo, comps: list[Component]) -> list[Face]:
    """Torgue's hexagonal grip shell + four continuous wraparound ribs (R4 geometry rule)."""
    bars = [c for c in comps if c.kind == "box" and abs(c.args[0][1] - TORGUE_BAR_SOURCE_Y) < 0.00001]

    def ring(radius: float, z: float) -> list[Vec]:
        return [
            (radius * math.cos(i * math.pi / 3), radius * math.sin(i * math.pi / 3), z)
            for i in range(TORGUE_SHELL_SIDES)
        ]

    faces: list[Face] = []
    lower = ring(TORGUE_SHELL_RADIUS, TORGUE_SHELL_Z[0])
    upper = ring(TORGUE_SHELL_RADIUS, TORGUE_SHELL_Z[1])
    faces.append(Face(tuple(reversed(lower)), "paint"))
    faces.append(Face(tuple(upper), "paint"))
    for i in range(TORGUE_SHELL_SIDES):
        j = (i + 1) % TORGUE_SHELL_SIDES
        faces.append(Face((lower[i], lower[j], upper[j], upper[i]), "paint"))

    for bar in bars:
        z = bar.args[0][2]
        outer_low = ring(TORGUE_RIB_RADIUS, z - TORGUE_RIB_HALF_HEIGHT)
        outer_high = ring(TORGUE_RIB_RADIUS, z + TORGUE_RIB_HALF_HEIGHT)
        inner_low = ring(TORGUE_RIB_INNER_RADIUS, z - TORGUE_RIB_HALF_HEIGHT)
        inner_high = ring(TORGUE_RIB_INNER_RADIUS, z + TORGUE_RIB_HALF_HEIGHT)
        for i in range(TORGUE_SHELL_SIDES):
            j = (i + 1) % TORGUE_SHELL_SIDES
            faces.append(Face((outer_low[i], outer_low[j], outer_high[j], outer_high[i]), "dark"))
            faces.append(Face((inner_high[i], outer_high[i], outer_high[j], inner_high[j]), "dark"))
            faces.append(Face((inner_low[j], outer_low[j], outer_low[i], inner_low[i]), "dark"))
            faces.append(Face((inner_low[j], inner_low[i], inner_high[i], inner_high[j]), "dark"))
    return faces


def apply_final_revisions(part_id: str, geo: Geo, bifacial: bool) -> tuple[list[Face], float]:
    """Turn an R3 primitive soup into final-part faces, applying every R4 rule in one pass.

    Returns `(faces, fit_inner_shift_m)`; the shift is only meaningful for bifacial parts, where it
    moves the decorative face so it sits `ACCESSORY_FACE_CLIP_M` inside the blade skin.
    """
    is_blade = part_id.startswith("blade_")
    faces: list[Face] = []
    #: original R3 face id -> index in the final face list (mirrored copies insert extra faces)
    owner_index: dict[int, int] = {}

    for component in geo.components:
        # Torgue's box primitive soup is fully superseded by the hexagonal shell + ribs below.
        if part_id == "grip_torgue" and component.kind == "box":
            continue
        face_ids = range(component.start, component.end)
        ys = [geo.v[i][1] for f in face_ids for i in geo.f[f]]
        lo, hi = min(ys), max(ys)
        front_only = (lo + hi) / 2 < -0.000001
        special_guard = part_id == "accessory_flared_cleave_guard" and component.kind == "profile"
        is_torgue_bar = (
            part_id == "grip_torgue"
            and component.kind == "box"
            and abs((lo + hi) / 2 - TORGUE_BAR_SOURCE_Y) < 0.0001
        )
        is_receiver = (
            is_blade and component.kind == "cyl" and abs(component.args[0][0] - ELEMENT_RECEIVER_X) < 1e-9
        )

        for face_id in face_ids:
            verts = [list(geo.v[i]) for i in geo.f[face_id]]
            for vert in verts:
                if is_torgue_bar:
                    vert[1] = TORGUE_BAR_Y_CENTER + (vert[1] - TORGUE_BAR_SOURCE_Y) * TORGUE_BAR_Y_SCALE
                if part_id == "accessory_reinforced_heavy_spine":
                    vert[1] /= HEAVY_SPINE_DEPTH_DIVISOR
                if special_guard:
                    vert[1] += CLEAVE_GUARD_PLATE_SHIFT
                if is_blade:
                    vert[1] *= BLADE_CORE_DEPTH_MULTIPLIER
                    if is_receiver:
                        vert[0] += ELEMENT_RECEIVER_X_SHIFT
            face = Face(tuple((float(x), float(y), float(z)) for x, y, z in verts), geo.tags[face_id])
            owner_index[face_id] = len(faces)
            faces.append(face)
            if not bifacial and front_only and not special_guard:
                # Rear copy reuses the front island's texels (reversed), exactly like R4 did.
                faces.append(Face(face.mirrored().verts, face.tag, owner_index[face_id], True, "exact"))

    if part_id == "grip_torgue":
        # The hex shell replaces the box primitive soup, so it owns its own islands.
        faces.extend(_torque_hex_faces(geo, geo.components))

    # Normalise one-sided accessories so their inner decorative surface sits at Z = 0, letting the
    # two FACE_FRONT / FACE_BACK instances land on opposite skins instead of floating.
    shift = 0.0
    if bifacial:
        candidates = geo.components[2:3] if part_id.startswith("accessory_") else geo.components
        shift = max(
            geo.v[i][1]
            for comp in candidates
            for face_index in range(comp.start, comp.end)
            for i in geo.f[face_index]
        )
        if part_id == "accessory_reinforced_heavy_spine":
            shift /= HEAVY_SPINE_DEPTH_DIVISOR
    return faces, shift


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------
def _sockets(part_id: str, slot: str) -> dict[str, Vec]:
    sockets: dict[str, Vec] = {"MOUNT": (0.0, 0.0, 0.0)}
    if slot == "BarrelPartDefinition":
        sockets.update(
            {
                "SOCKET_GRIP": (0.0, 0.0, 0.0),
                # R4 re-centred these on the thin blade core; they are no longer offset in Y.
                "SOCKET_ACCESSORY": (0.0, 0.0, 0.16),
                "SOCKET_ELEMENT": (ELEMENT_RECEIVER_X + ELEMENT_RECEIVER_X_SHIFT, 0.0, 0.12),
            }
        )
    if slot == "GripPartDefinition":
        sockets["SOCKET_POMMEL"] = (0.0, 0.0, -0.50)
    return sockets


def build_parts(part_ids: Iterable[str] | None = None) -> dict[str, Part]:
    """Build every final part (or just `part_ids`) as geometry suitable for the UV stage."""
    wanted = set(part_ids) if part_ids is not None else set(PART_ORDER)
    parts: dict[str, Part] = {}

    def add(part_id: str, geo: Geo, slot: str, maker: str, meta: dict[str, Any]) -> None:
        if part_id not in wanted:
            return
        bifacial = is_bifacial(part_id)
        faces, shift = apply_final_revisions(part_id, geo, bifacial)
        notes: dict[str, Any] = {}
        if part_id.startswith("blade_"):
            thickness = BLADE_THICKNESS_M[maker]
            notes["core_thickness_m"] = thickness / 2.0
            notes["accessory_face_gap_m"] = thickness / 4.0 - ACCESSORY_FACE_CLIP_M
            notes["element_face_gap_m"] = ELEMENT_FACE_GAP_M
        if part_id == "accessory_reinforced_heavy_spine":
            notes["depth_divisor"] = HEAVY_SPINE_DEPTH_DIVISOR
        if part_id == "grip_torgue":
            notes.update(
                {
                    "shell_sides": TORGUE_SHELL_SIDES,
                    "rib_count": TORGUE_RIB_COUNT,
                    "rib_wrap_degrees": 360,
                    "shell_radius_m": TORGUE_SHELL_RADIUS,
                    "rib_radius_m": TORGUE_RIB_RADIUS,
                }
            )
        parts[part_id] = Part(
            part_id=part_id,
            slot=slot,
            manufacturer=maker,
            faces=faces,
            sockets_blender=_sockets(part_id, slot),
            meta=meta,
            bifacial=bifacial,
            fit_inner_shift_m=shift,
            notes=notes,
        )

    for maker in BLADES:
        add(f"blade_{maker.lower()}", _blade(maker), "BarrelPartDefinition", maker, dict(BLADES[maker]))
        add(f"grip_{maker.lower()}", _grip(maker), "GripPartDefinition", maker,
            {"stat_modifiers_fraction": GRIPS[maker], "compatible_blades": "ALL"})
    for acc_id, info in ACCESSORIES.items():
        meta = dict(info)
        meta["target_socket"] = info.get("target_socket", "SOCKET_ACCESSORY")
        add(f"accessory_{acc_id}", _accessory(acc_id), "Accessory1PartDefinition", "Universal", meta)
    for element in ("Fire", "Shock", "Corrosive", "Slag", "Explosive"):
        add(f"element_{element.lower()}", _element(), "ElementalPartDefinition", "Universal",
            {"element": element, "target_socket": "SOCKET_ELEMENT"})

    return {part_id: parts[part_id] for part_id in PART_ORDER if part_id in parts}
