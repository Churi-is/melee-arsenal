"""Decal art: manufacturer emblems, engravings, blood and dirt.

The `decals` stage paints this module *before* `linework`, so every marking ends up underneath the
bold ink pass: contours, hatch clusters and scratches are drawn over the prints, which reads as paint
and etching on the metal instead of a sticker floating on top of the art.

Everything here is procedural and Pillow-only:

* a tiny stroke font (`GLYPHS`) draws the stencilled/block lettering (no TTF required, so the kit
  renders identically on machines with no fonts installed) - used sparingly, because in this world
  only **Hyperion** puts its own name on a gun; everyone else marks hardware with a badge, an
  engraving or a scratch,
* the emblems are hand-authored polygons/curves modelled on the in-game manufacturer marks,
* blood, dirt, rust and fingerprints are seeded noise clusters,

and all of it is placed through a *physical* frame derived from the mesh.  `island_frame()` recovers,
for any UV island, which way is "up the blade" and which way the outward face looks, so a 0.24 m wide
marking on the 1.68 m blade is stamped at the right size, the right rotation and the right handedness
on both the front and the rear face.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter

from .geometry import Part
from .uv import Island, PartLayout, Vec2, newell_normal

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]


def stage_seed(part_id: str, salt: str = "decals") -> random.Random:
    """Stable RNG for `(part, stage)` so the markings never depend on call order."""
    digest = hashlib.sha256(f"{part_id}|{salt}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "little"))

BLOOD = (122, 18, 16)
BLOOD_DARK = (58, 8, 10)
BLOOD_BRIGHT = (176, 38, 30)
DUST = (58, 46, 33)
RUST = (122, 62, 26)
GREASE = (34, 30, 26)
GOLD = (206, 160, 62)
INK = (16, 19, 25)


# ---------------------------------------------------------------------------
# Small linear algebra (decals are placed through a solved affine map)
# ---------------------------------------------------------------------------
def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gauss-Jordan with partial pivoting; `None` when the system is singular."""
    n = len(matrix)
    rows = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        if abs(rows[col][col]) < 1e-12:
            return None
        for r in range(n):
            if r == col:
                continue
            factor = rows[r][col] / rows[col][col]
            for k in range(col, n + 1):
                rows[r][k] -= factor * rows[col][k]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def _fit_plane_map(verts: Sequence[Sequence[float]], pixels: Sequence[Vec2]):
    """Least-squares `pixel = A @ vert + b` for one UV owner island.

    Face projections are orthonormal and planar, so this affine map is *the* projection the UV stage
    used; solving it (with a tiny ridge term, because a planar face makes the 4x4 system singular)
    recovers the physical axes inside the atlas without duplicating the projection code.
    """
    normal = [[0.0] * 4 for _ in range(4)]
    bx = [0.0] * 4
    by = [0.0] * 4
    for vert, pixel in zip(verts, pixels, strict=False):
        h = (float(vert[0]), float(vert[1]), float(vert[2]), 1.0)
        for i in range(4):
            for j in range(4):
                normal[i][j] += h[i] * h[j]
            bx[i] += h[i] * pixel[0]
            by[i] += h[i] * pixel[1]
    ridge = max(1e-9, sum(normal[i][i] for i in range(4)) / 4.0 * 1e-6)
    for i in range(4):
        normal[i][i] += ridge
    cx = _solve([row[:] for row in normal], bx)
    cy = _solve([row[:] for row in normal], by)
    if cx is None or cy is None:  # pragma: no cover - degenerate face
        return None
    return (cx[:3], cy[:3], (cx[3], cy[3]))


# ---------------------------------------------------------------------------
# Physical frame of a UV island
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IslandFrame:
    """Where an island sits on the part, and how to lay art onto it.

    `right` / `up` are image-space vectors per metre: `right` points along the viewer's right
    (already mirrored for rear faces) and `up` points toward the blade tip.  `centre` is the image
    position of the part-space origin, so `image = centre + a * right + b * up`.
    """

    centre: Vec2
    right: Vec2
    up: Vec2
    px_per_m: float
    front: bool

    def to_image(self, a: float, b: float) -> Vec2:
        return (self.centre[0] + a * self.right[0] + b * self.up[0],
                self.centre[1] + a * self.right[1] + b * self.up[1])

    def to_frame(self, x: float, y: float) -> Vec2:
        dx, dy = x - self.centre[0], y - self.centre[1]
        det = self.right[0] * self.up[1] - self.right[1] * self.up[0]
        if abs(det) < 1e-9:  # pragma: no cover - degenerate frame
            return (0.0, 0.0)
        a = (dx * self.up[1] - dy * self.up[0]) / det
        b = (self.right[0] * dy - self.right[1] * dx) / det
        return (a, b)


def island_frame(part: Part, island: Island) -> IslandFrame | None:
    """Recover the physical frame of an owner island (None when it cannot be solved)."""
    if not island.owner or island.face >= len(part.faces):
        return None
    verts = part.faces[island.face].verts
    if len(verts) < 3 or len(verts) != len(island.polygon):
        return None
    fitted = _fit_plane_map(verts, island.polygon)
    if fitted is None:
        return None
    ax, ay, offset = fitted
    normal = newell_normal(verts)
    # Viewer-right for an outward normal n with world up +Z is  cross(-n, +Z).
    viewer_right_sign = -1.0 if normal[1] > 0.0 else 1.0
    right = (ax[0] * viewer_right_sign, ay[0] * viewer_right_sign)
    up = (ax[2], ay[2])
    scale = (math.hypot(*right) + math.hypot(*up)) / 2.0
    big, small = max(math.hypot(*right), math.hypot(*up)), min(math.hypot(*right), math.hypot(*up))
    # Two degenerate cases are left to the ink pass: a face pointing along the blade axis (a pommel
    # cap, an emitter's end plate) has no meaningful "up the blade" direction, and a warped quad can
    # make the fitted axes nearly parallel — both smear any art laid on them.
    if scale < 1e-6 or small < 0.35 * big:
        return None
    if abs(right[0] * up[1] - right[1] * up[0]) < 0.35 * big * small:
        return None
    return IslandFrame(centre=offset, right=right, up=up, px_per_m=scale, front=normal[1] <= 0.0)


def frame_bounds(polygon: Sequence[Vec2], frame: IslandFrame) -> tuple[float, float, float, float]:
    """Island polygon bounds in frame metres: `(a_min, a_max, b_min, b_max)`."""
    points = [frame.to_frame(x, y) for x, y in polygon]
    return (
        min(p[0] for p in points),
        max(p[0] for p in points),
        min(p[1] for p in points),
        max(p[1] for p in points),
    )


# ---------------------------------------------------------------------------
# Drawing layer: supersampled, drawn in final pixel coordinates
# ---------------------------------------------------------------------------
class Layer:
    """An RGBA art layer drawn at `supersample`x and averaged down on `finish()`."""

    def __init__(self, size: tuple[int, int], supersample: int = 3) -> None:
        self.size = (max(2, int(size[0])), max(2, int(size[1])))
        self.ss = max(1, int(supersample))
        self.image = Image.new("RGBA", (self.size[0] * self.ss, self.size[1] * self.ss), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

    # -- primitives (final-resolution coordinates) ------------------------------------------------
    def line(self, points: Sequence[Vec2], width: float = 1.0, fill: RGBA = INK + (255,)) -> None:
        pts = [(x * self.ss, y * self.ss) for x, y in points]
        self.draw.line(pts, fill=fill, width=max(1, int(round(width * self.ss))), joint="curve")
        if len(pts) >= 2 and width * self.ss > 3:
            for x, y in (pts[0], pts[-1]):
                r = width * self.ss / 2.0
                self.draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def poly(self, points: Sequence[Vec2], fill: RGBA | None = None, outline: RGBA | None = None,
             width: float = 1.0) -> None:
        pts = [(x * self.ss, y * self.ss) for x, y in points]
        self.draw.polygon(pts, fill=fill)
        if outline is not None:
            self.draw.line(pts + [pts[0]], fill=outline, width=max(1, int(round(width * self.ss))),
                           joint="curve")

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, fill: RGBA,
                outline: RGBA | None = None, width: float = 1.0) -> None:
        box = [(cx - rx) * self.ss, (cy - ry) * self.ss, (cx + rx) * self.ss, (cy + ry) * self.ss]
        self.draw.ellipse(box, fill=fill, outline=outline,
                          width=max(1, int(round(width * self.ss))))

    def blot(self, cx: float, cy: float, radius: float, rng: random.Random, fill: RGBA,
             lobes: int = 7, squash: float = 0.6) -> None:
        """An irregular ink/splatter blob."""
        points = []
        for k in range(lobes):
            angle = k * math.tau / lobes
            distance = radius * rng.uniform(0.55, 1.25)
            points.append((cx + math.cos(angle) * distance,
                           cy + math.sin(angle) * distance * squash))
        self.poly(points, fill)

    def finish(self) -> Image.Image:
        if self.ss == 1:
            return self.image
        return self.image.resize(self.size, Image.Resampling.LANCZOS)


# ---------------------------------------------------------------------------
# Stroke font (4 x 6 unit grid, y down) — blocky industrial lettering
# ---------------------------------------------------------------------------
GLYPHS: dict[str, tuple[tuple[Vec2, ...], ...]] = {
    "A": (((0, 6), (0, 1.6), (1.2, 0), (2.8, 0), (4, 1.6), (4, 6)), ((0, 3.9), (4, 3.9))),
    "B": (((0, 0), (0, 6)), ((0, 0), (2.9, 0), (4, 1.0), (4, 2.2), (2.9, 3.0), (0, 3.0)),
          ((2.9, 3.0), (4, 3.9), (4, 5.0), (2.9, 6.0), (0, 6.0))),
    "C": (((4, 1.0), (3.0, 0), (1.0, 0), (0, 1.0), (0, 5.0), (1.0, 6), (3.0, 6), (4, 5.0)),),
    "D": (((0, 0), (0, 6)), ((0, 0), (2.8, 0), (4, 1.2), (4, 4.8), (2.8, 6), (0, 6))),
    "E": (((4, 0), (0, 0), (0, 6), (4, 6)), ((0, 3), (3, 3))),
    "F": (((4, 0), (0, 0), (0, 6)), ((0, 3), (3, 3))),
    "G": (((4, 1.0), (3.0, 0), (1.0, 0), (0, 1.0), (0, 5.0), (1.0, 6), (3.2, 6), (4, 5.0),
           (4, 3.4), (2.3, 3.4)),),
    "H": (((0, 0), (0, 6)), ((4, 0), (4, 6)), ((0, 3), (4, 3))),
    "I": (((1, 0), (3, 0)), ((2, 0), (2, 6)), ((1, 6), (3, 6))),
    "J": (((4, 0), (4, 4.8), (3.0, 6), (1.2, 6), (0.1, 4.9)),),
    "K": (((0, 0), (0, 6)), ((4, 0), (0, 3.6)), ((1.3, 2.4), (4, 6))),
    "L": (((0, 0), (0, 6), (4, 6)),),
    "M": (((0, 6), (0, 0), (2, 3.2), (4, 0), (4, 6)),),
    "N": (((0, 6), (0, 0), (4, 6), (4, 0)),),
    "O": (((1.0, 0), (3.0, 0), (4, 1.0), (4, 5.0), (3.0, 6), (1.0, 6), (0, 5.0), (0, 1.0),
           (1.0, 0)),),
    "P": (((0, 6), (0, 0), (2.9, 0), (4, 1.0), (4, 2.4), (2.9, 3.4), (0, 3.4)),),
    "Q": (((1.0, 0), (3.0, 0), (4, 1.0), (4, 5.0), (3.0, 6), (1.0, 6), (0, 5.0), (0, 1.0),
           (1.0, 0)), ((2.4, 4.2), (4.2, 6.2))),
    "R": (((0, 6), (0, 0), (2.9, 0), (4, 1.0), (4, 2.4), (2.9, 3.4), (0, 3.4)), ((1.5, 3.4), (4, 6))),
    "S": (((4, 0.9), (3.0, 0), (1.0, 0), (0, 1.0), (0, 2.0), (1.0, 2.9), (3.0, 3.1), (4, 4.0),
           (4, 5.0), (3.0, 6), (1.0, 6), (0, 5.1)),),
    "T": (((0, 0), (4, 0)), ((2, 0), (2, 6))),
    "U": (((0, 0), (0, 5.0), (1.0, 6), (3.0, 6), (4, 5.0), (4, 0)),),
    "V": (((0, 0), (2, 6), (4, 0)),),
    "W": (((0, 0), (0.8, 6), (2, 1.8), (3.2, 6), (4, 0)),),
    "X": (((0, 0), (4, 6)), ((4, 0), (0, 6))),
    "Y": (((0, 0), (2, 3), (4, 0)), ((2, 3), (2, 6))),
    "Z": (((0, 0), (4, 0), (0, 6), (4, 6)),),
    "0": (((1.0, 0), (3.0, 0), (4, 1.0), (4, 5.0), (3.0, 6), (1.0, 6), (0, 5.0), (0, 1.0),
           (1.0, 0)), ((0.7, 4.8), (3.3, 1.2))),
    "1": (((1.2, 0.9), (2.2, 0), (2.2, 6)), ((1.2, 6), (3.2, 6))),
    "2": (((0, 1.0), (1.0, 0), (3.0, 0), (4, 1.0), (4, 1.9), (0, 6), (4, 6)),),
    "3": (((0, 0.9), (1.0, 0), (3.0, 0), (4, 1.0), (4, 2.0), (3.0, 2.9), (4, 3.8), (4, 5.0),
           (3.0, 6), (1.0, 6), (0, 5.1)),),
    "4": (((3, 6), (3, 0), (0, 4.2), (4, 4.2)),),
    "5": (((4, 0), (0, 0), (0, 2.7), (2.9, 2.7), (4, 3.7), (4, 5.0), (3.0, 6), (1.0, 6),
           (0, 5.1)),),
    "6": (((3.6, 0.2), (1.2, 0.4), (0, 2.0), (0, 5.0), (1.0, 6), (3.0, 6), (4, 5.0), (4, 3.9),
           (3.0, 3.0), (0, 3.0)),),
    "7": (((0, 0), (4, 0), (1.7, 6)),),
    "8": (((1.0, 0), (3.0, 0), (4, 0.9), (4, 2.0), (3.0, 2.9), (1.0, 2.9), (0, 2.0), (0, 0.9),
           (1.0, 0)), ((1.0, 2.9), (0, 3.8), (0, 5.0), (1.0, 6), (3.0, 6), (4, 5.0), (4, 3.8),
                       (3.0, 2.9))),
    "9": (((0.4, 5.8), (2.8, 5.6), (4, 4.0), (4, 1.0), (3.0, 0), (1.0, 0), (0, 1.0), (0, 2.1),
           (1.0, 3.0), (4, 3.0)),),
    "-": (((0.9, 3), (3.1, 3)),),
    ".": (((2, 5.5), (2.05, 5.55)),),
    "/": (((0.2, 6), (3.8, 0)),),
    ":": (((2, 2.0), (2.05, 2.05)), ((2, 4.6), (2.05, 4.65))),
    "'": (((2, 0), (2, 1.6)),),
    "!": (((2, 0), (2, 4.2)), ((2, 5.4), (2.05, 5.45))),
    "+": (((2, 1.6), (2, 4.4)), ((0.8, 3), (3.2, 3))),
}

ADVANCE = 5.0      # units per glyph (4 wide + 1 gap)
SPACE = 3.0
UNIT = 6.0         # glyph is 6 units tall


def text_width(text: str, height: float, tracking: float = 0.0) -> float:
    """Width in pixels of `text` rendered at `height` pixels tall."""
    scale = height / UNIT
    total = 0.0
    for index, char in enumerate(text):
        if char == " ":
            total += SPACE * scale
            continue
        total += ADVANCE * scale + tracking
        if index == len(text) - 1:
            total -= 1.0 * scale
    return total


def draw_text(
    layer: Layer,
    text: str,
    x: float,
    y: float,
    height: float,
    fill: RGBA,
    *,
    stroke: float | None = None,
    tracking: float = 0.0,
    shear: float = 0.0,
    rng: random.Random | None = None,
    jitter: float = 0.0,
    align: str = "left",
) -> float:
    """Draw stroke-font `text` with its cap height at `y`; returns the advance width.

    `shear` slants the glyphs (positive leans right, italic-style), `rng`/`jitter` roughens the
    lettering so it reads as hand-stencilled rather than typeset.
    """
    scale = height / UNIT
    width = text_width(text, height, tracking)
    cursor = x - (width / 2.0 if align == "center" else width if align == "right" else 0.0)
    weight = stroke if stroke is not None else max(1.0, height * 0.20)
    for char in text:
        if char == " ":
            cursor += SPACE * scale
            continue
        glyph = GLYPHS.get(char.upper())
        if glyph:
            for stroke_points in glyph:
                points = []
                for gx, gy in stroke_points:
                    px = cursor + gx * scale
                    py = y + gy * scale
                    if shear:
                        px += (gy - 3.0) * scale * shear
                    if rng is not None and jitter:
                        px += rng.uniform(-jitter, jitter)
                        py += rng.uniform(-jitter, jitter)
                    points.append((px, py))
                layer.line(points, width=weight, fill=fill)
        else:  # unknown character: draw a stubby bar so the text stays legible as a code
            layer.line([(cursor + 1.0 * scale, y + 2.0 * scale), (cursor + 3.0 * scale, y + 4.0 * scale)],
                       width=weight, fill=fill)
        cursor += ADVANCE * scale + tracking
    return width


# ---------------------------------------------------------------------------
# Curve helpers (emblems are authored as paths, like the originals)
# ---------------------------------------------------------------------------
def bezier(points: Sequence[Vec2], steps: int = 18) -> list[Vec2]:
    """Flatten a quadratic (3 points) or cubic (4 points) Bézier into a polyline."""
    out: list[Vec2] = []
    for i in range(steps + 1):
        t = i / steps
        if len(points) == 3:
            (x0, y0), (x1, y1), (x2, y2) = points
            u = 1 - t
            out.append((u * u * x0 + 2 * u * t * x1 + t * t * x2,
                        u * u * y0 + 2 * u * t * y1 + t * t * y2))
        else:
            (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
            u = 1 - t
            out.append((u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
                        u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3))
    return out


def hexagon(cx: float, cy: float, rx: float, ry: float, angle: float = 0.0) -> list[Vec2]:
    return [(cx + math.cos(angle + i * math.tau / 6) * rx,
             cy + math.sin(angle + i * math.tau / 6) * ry) for i in range(6)]


def skew_box(x: float, y: float, w: float, h: float, shear: float) -> list[Vec2]:
    """A parallelogram (the Hyperion band/diamond silhouette)."""
    return [(x + shear, y), (x + w, y), (x + w - shear, y + h), (x, y + h)]


# ---------------------------------------------------------------------------
# Manufacturer emblems
# ---------------------------------------------------------------------------
def torgue_emblem(layer: Layer, cx: float, cy: float, width: float, alpha: int = 255) -> None:
    """Torgue's badge: a black hexagon plate with an orange rim and a detonation star.

    Deliberately *nameless* - the house stamps this monogram on its hardware, not its own name.
    """
    height = width * 0.46
    plate = hexagon(cx, cy, width / 2.0, height / 2.0, angle=0.0)
    layer.poly(plate, fill=(28, 26, 28, alpha), outline=(214, 96, 34, alpha), width=width * 0.022)
    inner = hexagon(cx, cy, width * 0.44, height * 0.40, angle=0.0)
    layer.poly(inner, fill=(38, 36, 40, alpha), outline=(196, 150, 52, alpha), width=width * 0.008)
    # The mark itself: a chunky six-point blast (fat rays read as a badge at 30 px, thin ones read
    # as a sun) with a dark vented core.
    burst = []
    for i in range(12):
        angle = -math.pi / 2.0 + i * math.tau / 12.0
        r = height * 0.44 if i % 2 == 0 else height * 0.26
        burst.append((cx + math.cos(angle) * r * 1.42, cy + math.sin(angle) * r))
    layer.poly(burst, fill=(232, 186, 74, alpha))
    core = hexagon(cx, cy, height * 0.20, height * 0.20, angle=math.pi / 6.0)
    layer.poly(core, fill=(30, 28, 30, alpha), outline=(214, 96, 34, alpha),
               width=max(1.0, width * 0.010))
    # Detonator tick marks along the bottom of the badge.
    for i in range(7):
        x = cx - width * 0.30 + i * width * 0.10
        layer.line([(x, cy + height * 0.22), (x, cy + height * 0.32)],
                   width=width * 0.012, fill=(214, 96, 34, alpha))


def maliwan_emblem(layer: Layer, cx: float, cy: float, width: float, alpha: int = 255) -> None:
    """The Maliwan droplet-and-rings mark in deep navy with a teal rim."""
    height = width * 0.55
    navy = (24, 44, 62, alpha)
    teal = (74, 204, 220, alpha)
    # A real teardrop: a point at the top, straight-ish shoulders and a round bulb at the bottom.
    # (Two mirrored beziers make a lens, which reads as an eye rather than a droplet.)
    bulb_r = width * 0.26
    bulb_y = cy + height * 0.16
    drop = [(cx, cy - height * 0.56)]
    drop += bezier([(cx, cy - height * 0.56),
                    (cx + bulb_r * 1.10, bulb_y - height * 0.34),
                    (cx + bulb_r, bulb_y)], steps=14)
    for i in range(1, 14):
        angle = math.pi * i / 14.0
        drop.append((cx + math.cos(angle) * bulb_r, bulb_y + math.sin(angle) * bulb_r))
    drop += bezier([(cx - bulb_r, bulb_y),
                    (cx - bulb_r * 1.10, bulb_y - height * 0.34),
                    (cx, cy - height * 0.56)], steps=14)
    layer.poly(drop, fill=navy, outline=teal, width=width * 0.014)
    layer.ellipse(cx, cy + height * 0.10, width * 0.085, width * 0.085, fill=teal)
    for sign in (-1.0, 1.0):
        ring = bezier([(cx + sign * width * 0.26, cy - height * 0.30),
                       (cx + sign * width * 0.38, cy),
                       (cx + sign * width * 0.26, cy + height * 0.34)])
        layer.line(ring, width=width * 0.012, fill=gold_tint(navy, 0.35, alpha))


def gold_tint(base: RGB | RGBA, amount: float, alpha: int = 255) -> RGBA:
    """Blend `base` toward gold (used for engraved rims on the Maliwan mark)."""
    r = int(base[0] * (1 - amount) + GOLD[0] * amount)
    g = int(base[1] * (1 - amount) + GOLD[1] * amount)
    b = int(base[2] * (1 - amount) + GOLD[2] * amount)
    return (r, g, b, alpha)


# ---------------------------------------------------------------------------
# Natural media: blood, dirt, rust, grease
# ---------------------------------------------------------------------------
def blood_splatter(
    layer: Layer,
    rng: random.Random,
    cx: float,
    cy: float,
    radius: float,
    alpha: int = 235,
    drip: Vec2 = (0.0, 1.0),
) -> None:
    """A directional spatter: a soaked core, satellite droplets and gravity drips.

    `drip` is the unit direction blood runs in (art-space), so a blade plate can pull its drips back
    toward the hilt instead of across the steel.
    """
    for _ in range(rng.randint(4, 9)):
        bx = cx + rng.uniform(-radius * 1.4, radius * 1.4)
        by = cy + rng.uniform(-radius * 1.1, radius * 1.1)
        r = radius * rng.uniform(0.20, 0.55)
        lobes = rng.randint(6, 10)
        squash = rng.uniform(0.45, 0.95)
        # A dark wet edge, the body of the stain, then a thin highlight: the outline is what keeps
        # blood legible on top of red paint as well as on bare steel.
        layer.blot(bx, by, r * 1.26, rng, (BLOOD_DARK[0], BLOOD_DARK[1], BLOOD_DARK[2], alpha),
                   lobes=lobes, squash=squash)
        layer.blot(bx, by, r, rng, (BLOOD[0], BLOOD[1], BLOOD[2], alpha), lobes=lobes, squash=squash)
        layer.ellipse(bx - r * 0.25, by - r * 0.25, r * 0.26, r * 0.15,
                      fill=(BLOOD_BRIGHT[0], BLOOD_BRIGHT[1], BLOOD_BRIGHT[2], alpha))
        # A dragged smear leaving the stain, the way a swung blade throws blood.
        if rng.random() < 0.55:
            angle = rng.uniform(0, math.tau)
            length = r * rng.uniform(1.4, 2.8)
            layer.line([(bx, by), (bx + math.cos(angle) * length * 0.5,
                                   by + math.sin(angle) * length * 0.5),
                        (bx + math.cos(angle) * length, by + math.sin(angle) * length)],
                       width=r * rng.uniform(0.30, 0.55),
                       fill=(BLOOD[0], BLOOD[1], BLOOD[2], int(alpha * 0.75)))
    # Throw streaks: droplets dragged into a streak by the swing.
    for _ in range(rng.randint(3, 7)):
        angle = rng.gauss(0.0, 0.5)
        start = radius * rng.uniform(0.1, 0.9)
        length = radius * rng.uniform(0.25, 0.75)
        x0 = cx + math.cos(angle) * start
        y0 = cy + math.sin(angle) * start
        layer.line([(x0, y0), (x0 + math.cos(angle) * length, y0 + math.sin(angle) * length)],
                   width=radius * rng.uniform(0.05, 0.12),
                   fill=(BLOOD_DARK[0], BLOOD_DARK[1], BLOOD_DARK[2], alpha))
        layer.line([(x0, y0), (x0 + math.cos(angle) * length * 0.7, y0 + math.sin(angle) * length * 0.7)],
                   width=radius * rng.uniform(0.03, 0.07),
                   fill=(BLOOD[0], BLOOD[1], BLOOD[2], alpha))
    # Fine spray, biased in the swing direction.
    for _ in range(rng.randint(30, 70)):
        angle = rng.gauss(0.0, 0.85)
        distance = abs(rng.gauss(0.0, radius * 0.75)) + radius * 0.15
        x = cx + math.cos(angle) * distance + rng.uniform(-radius * 0.5, radius * 0.5)
        y = cy + math.sin(angle) * distance * 0.75
        r = radius * rng.uniform(0.012, 0.055)
        layer.ellipse(x, y, r, r * rng.uniform(0.6, 1.5),
                      fill=(BLOOD_BRIGHT[0], BLOOD_BRIGHT[1], BLOOD_BRIGHT[2],
                            int(alpha * rng.uniform(0.55, 1.0))))
    # Drips running with gravity (toward the hilt on a blade plate).
    dx_unit, dy_unit = drip
    norm = math.hypot(dx_unit, dy_unit) or 1.0
    dx_unit, dy_unit = dx_unit / norm, dy_unit / norm
    side = (-dy_unit, dx_unit)
    for _ in range(rng.randint(2, 5)):
        x = cx + rng.uniform(-radius * 0.9, radius * 0.9)
        y = cy + rng.uniform(-radius * 0.3, radius * 0.5)
        length = radius * rng.uniform(0.9, 2.6)
        width = radius * rng.uniform(0.045, 0.12)
        drift = rng.uniform(-width, width)
        layer.line(
            [(x, y),
             (x + dx_unit * length * 0.55 + side[0] * drift, y + dy_unit * length * 0.55 + side[1] * drift),
             (x + dx_unit * length + side[0] * drift, y + dy_unit * length + side[1] * drift)],
            width=width, fill=(BLOOD_DARK[0], BLOOD_DARK[1], BLOOD_DARK[2], alpha))
        layer.ellipse(x + dx_unit * length, y + dy_unit * length, width * 0.85, width * 1.15,
                      fill=(BLOOD[0], BLOOD[1], BLOOD[2], alpha))


def grime_streaks(
    layer: Layer, rng: random.Random, cx: float, cy: float, width: float, height: float,
    alpha: int = 120, horizontal: bool = False,
) -> None:
    """Dry dirt drags and grit blotches (dragged along whichever axis `horizontal` selects)."""
    for _ in range(rng.randint(10, 26)):
        x = cx + rng.uniform(-width * 0.5, width * 0.5)
        y = cy + rng.uniform(-height * 0.5, height * 0.5)
        if horizontal:
            dx, dy = rng.uniform(-width * 0.45, width * 0.45), rng.uniform(-2, 2)
        else:
            dx, dy = rng.uniform(-2, 2), rng.uniform(-height * 0.45, height * 0.45)
        shade = rng.choice([DUST, RUST, GREASE])
        layer.line([(x, y), (x + dx * 0.5 + rng.uniform(-3, 3), y + dy * 0.5 + rng.uniform(-3, 3)),
                    (x + dx, y + dy)],
                   width=rng.uniform(width * 0.004, width * 0.012),
                   fill=(shade[0], shade[1], shade[2], int(alpha * rng.uniform(0.5, 1.0))))
    for _ in range(rng.randint(14, 34)):
        x = cx + rng.uniform(-width * 0.5, width * 0.5)
        y = cy + rng.uniform(-height * 0.5, height * 0.5)
        r = rng.uniform(width * 0.004, width * 0.018)
        shade = rng.choice([DUST, RUST])
        layer.ellipse(x, y, r, r * rng.uniform(0.6, 1.4),
                      fill=(shade[0], shade[1], shade[2], int(alpha * rng.uniform(0.35, 0.8))))


def fingerprints(layer: Layer, rng: random.Random, cx: float, cy: float, size: float) -> None:
    """Smudged, greasy thumb/finger prints (Bandit grips are *handled*)."""
    for _ in range(rng.randint(2, 4)):
        fx = cx + rng.uniform(-size, size)
        fy = cy + rng.uniform(-size, size)
        w = size * rng.uniform(0.22, 0.34)
        h = size * rng.uniform(0.5, 0.8)
        angle = rng.uniform(-0.6, 0.6)
        for ring in range(1, 4):
            points = []
            for step in range(16):
                t = step / 15.0
                px = fx + math.cos(t * math.tau) * w * ring / 4.0
                py = fy + math.sin(t * math.tau) * h * ring / 4.0
                rx = fx + (px - fx) * math.cos(angle) - (py - fy) * math.sin(angle)
                ry = fy + (px - fx) * math.sin(angle) + (py - fy) * math.cos(angle)
                points.append((rx, ry))
            layer.line(points + [points[0]], width=max(1.0, size * 0.012),
                       fill=(GREASE[0], GREASE[1], GREASE[2], 70))


def tally_marks(layer: Layer, rng: random.Random, cx: float, cy: float, height: float,
                colour: RGBA, groups: int = 3, tilt: float = 0.0) -> None:
    """Crude four-stroke tallies with a slash, the way a Bandit counts kills.

    `tilt` (radians) leans *one* of the groups, because nobody scratches a kill count with a set
    square and a ruler: the rest stay upright, so the tilted one reads as hand-slanted rather than
    as a different kind of mark.
    """
    cursor = cx
    leaning = rng.randrange(groups) if (tilt > 0.0 and groups > 0) else -1
    for group in range(groups):
        angle = rng.uniform(0.55, 1.0) * tilt if group == leaning else 0.0
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        pivot = (cursor + height * 0.22, cy)

        def lean(point: tuple[float, float], pivot=pivot, cos_a=cos_a, sin_a=sin_a):
            dx, dy = point[0] - pivot[0], point[1] - pivot[1]
            return (pivot[0] + dx * cos_a - dy * sin_a, pivot[1] + dx * sin_a + dy * cos_a)

        for stroke in range(4):
            x = cursor + stroke * height * 0.16
            layer.line([lean((x, cy - height * rng.uniform(0.45, 0.55))),
                        lean((x + rng.uniform(-height * 0.05, height * 0.05), cy + height * 0.5))],
                       width=max(1.0, height * 0.06), fill=colour)
        layer.line([lean((cursor - height * 0.06, cy + height * 0.16)),
                    lean((cursor + height * 0.5, cy - height * 0.20))],
                   width=max(1.0, height * 0.055), fill=colour)
        cursor += height * 0.85


# ---------------------------------------------------------------------------
# Island painting: resample an art layer onto one island through its frame
# ---------------------------------------------------------------------------
def island_mask(polygon: Sequence[Vec2], size: tuple[int, int], origin: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon([(x - origin[0], y - origin[1]) for x, y in polygon], fill=255)
    return mask


def stamp(
    canvas: Image.Image,
    island: Island,
    art: Image.Image,
    frame: IslandFrame,
    anchor: Vec2,
    size_m: tuple[float, float],
    orientation: str = "across",
    inset: int = 1,
) -> bool:
    """Composite `art` onto `canvas` inside `island`, centred on frame-metre `anchor`.

    `orientation="along"` lays the art's +x along the blade (art authored the way an etching runs up
    a blade: read left-to-right from hilt toward tip), `"across"` lays it along the viewer's right.
    `size_m` is always `(art_width_m, art_height_m)` in the art's own axes.
    """
    width_m, height_m = size_m
    if width_m <= 0 or height_m <= 0:
        return False
    target = (max(2, int(round(width_m * frame.px_per_m))),
              max(2, int(round(height_m * frame.px_per_m))))
    if art.size != target:
        art = art.resize(target, Image.Resampling.LANCZOS)
    x, y, w, h = island.rect
    box = (x, y, x + w, y + h)
    centre = frame.to_image(anchor[0], anchor[1])
    rx, ry = frame.right
    ux, uy = frame.up
    scale_r = math.hypot(rx, ry) or 1.0
    scale_u = math.hypot(ux, uy) or 1.0
    right_dir = (rx / scale_r, ry / scale_r)
    down_dir = (-ux / scale_u, -uy / scale_u)
    if orientation == "along":
        # Read from the hilt toward the point with the glyph tops facing the spine, exactly like an
        # etched maker's mark, which is a half turn away from the plain `right`/`down` basis.
        art_x_dir = (-ux / scale_u, -uy / scale_u)
        art_y_dir = (-right_dir[0], -right_dir[1])
    else:
        art_x_dir, art_y_dir = right_dir, down_dir

    # `image = centre + u * art_x_dir + v * art_y_dir`, solved for the art pixel `(u, v)`.
    det = art_x_dir[0] * art_y_dir[1] - art_x_dir[1] * art_y_dir[0]
    if abs(det) < 1e-6:  # pragma: no cover - degenerate frame
        return False
    a00 = art_y_dir[1] / det
    a01 = -art_y_dir[0] / det
    a10 = -art_x_dir[1] / det
    a11 = art_x_dir[0] / det
    half_w = art.size[0] / 2.0
    half_h = art.size[1] / 2.0
    c0 = a00 * (box[0] - centre[0]) + a01 * (box[1] - centre[1]) + half_w
    c1 = a10 * (box[0] - centre[0]) + a11 * (box[1] - centre[1]) + half_h
    warped = art.transform(
        (w, h), Image.Transform.AFFINE, (a00, a01, c0, a10, a11, c1),
        resample=Image.Resampling.BICUBIC,
    )
    mask = island_mask(island.polygon, (w, h), (x, y))
    if inset > 0:
        mask = mask.filter(ImageFilter.MinFilter(3))
    clipped = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    clipped.paste(warped, (0, 0),
                  Image.composite(mask, Image.new("L", (w, h), 0), warped.getchannel("A")))
    canvas.paste(Image.alpha_composite(canvas.crop(box).convert("RGBA"), clipped).convert("RGB"),
                 box[:2])
    return True


# ---------------------------------------------------------------------------
# Art plates
#
# Blade plates are authored in **along-the-blade** space: `x` runs from the hilt (0) to the tip (`w`)
# and `y` runs across the blade, so a wordmark reads from the hilt toward the point exactly as it
# would if it were etched into the steel.  `h` (the blade width) is the natural size unit there, so
# every element is scaled from `h` and stays legible whether the plate lands on a wide machete or a
# slim katana.
#
# Two rules keep the art visible on the model:
#
# * the **middle band** of a blade face is usually where a raised fuller or a bolted-on panel sits, so
#   primary plates keep their art in the outer bands (`y` around 0.22 and 0.70) and leave the centre
#   alone,
# * an inset panel that covers a big share of the face (Bandit's red slab, Torgue's orange plate) gets
#   its own `variant="panel"` plate, so the art lands on the surface actually seen.
#
# Grip plates stay across-the-grip (`x` = around the handle, `y` = along it) and are pattern-led:
# a hex facet is only a few centimetres wide, so fine art there would just be mud.  Hyperion is the
# only house whose wordmark survives the trip around a handle; the rest get a badge or an engraving.
# ---------------------------------------------------------------------------
Variant = str


def _rivet_row(layer: Layer, xs: Sequence[float], y: float, size: float, colour: RGBA) -> None:
    for x in xs:
        layer.ellipse(x, y, size, size, fill=colour)


def fit_height(text: str, height: float, max_width: float | None, tracking: float = 0.0) -> float:
    """Shrink a cap height until `text` fits `max_width` pixels (a facet is only so wide)."""
    if not max_width or max_width <= 0:
        return height
    width = text_width(text, height, tracking)
    if width <= max_width:
        return height
    return max(1.0, height * max_width / width)


def _blade_hyperion(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    if variant != "primary":
        return layer.finish()
    # The corporate band runs the length of the blade; the wordmark is set *along* it, the way an
    # etched maker's name runs on a slim blade (a crosswise logo would be two letters wide here).
    band_top, band_bottom = h * 0.30, h * 0.60
    layer.poly([(w * 0.04, band_top), (w * 0.96, band_top), (w * 0.96, band_bottom),
                (w * 0.04, band_bottom)], fill=(228, 188, 46, 238))
    layer.line([(w * 0.04, band_bottom), (w * 0.96, band_bottom)], width=max(1.0, h * 0.020),
               fill=(26, 26, 26, 240))
    layer.line([(w * 0.04, band_top), (w * 0.96, band_top)], width=max(1.0, h * 0.014),
               fill=(26, 26, 26, 200))
    draw_text(layer, "HYPERION", w * 0.56, band_top + h * 0.070, h * 0.185, (20, 20, 20, 250),
              stroke=h * 0.045, shear=0.30, tracking=h * 0.048, align="center")
    # The four-diamond cluster at the hilt end, then a precision vent row and serial stencil.
    for dx, dy in ((0.0, 0.0), (1.0, 0.0), (0.34, 0.80), (1.34, 0.80)):
        square = h * 0.095
        layer.poly(skew_box(w * 0.05 + dx * square, band_top + h * 0.035 + dy * square, square,
                            square, shear=square * 0.35), fill=(24, 24, 24, 240))
    for i in range(7):
        x = w * (0.16 + i * 0.112)
        layer.poly(hexagon(x, h * 0.80, h * 0.070, h * 0.055), fill=(76, 94, 106, 210))
        layer.poly(hexagon(x, h * 0.80, h * 0.038, h * 0.028), fill=(20, 26, 32, 220))
    draw_text(layer, "SERIAL 4-23A", w * 0.84, h * 0.86, h * 0.100, (52, 64, 76, 240),
              stroke=h * 0.026, tracking=h * 0.022, align="center")
    layer.line([(w * 0.04, h * 0.70), (w * 0.96, h * 0.70)], width=max(1.0, h * 0.016),
               fill=(62, 74, 86, 165))
    for _ in range(7):
        x = rng.uniform(w * 0.06, w * 0.94)
        y = h * rng.uniform(0.06, 0.26)
        layer.line([(x, y), (x + w * rng.uniform(0.01, 0.035), y + h * rng.uniform(-0.04, 0.04))],
                   width=max(1.0, h * 0.012), fill=(240, 238, 226, 130))
    return layer.finish()


def _blade_jakobs(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    if variant != "primary":
        return layer.finish()
    # Watered-steel etch: wavering engraver's lines across the whole face (they stay thin, so they
    # survive the fuller running over them).
    for i in range(11):
        y = h * (0.08 + i * 0.085)
        points = [(x, y + math.sin(x / max(6.0, w * 0.045) + i * 1.7) * h * 0.030)
                  for x in range(0, int(w) + 1, max(3, int(w / 80)))]
        layer.line(points, width=max(1.0, h * 0.010), fill=(66, 50, 34, 135 if i % 2 else 165))
    # Gold pinline and inlay pins along the spine side.
    layer.line([(w * 0.04, h * 0.17), (w * 0.96, h * 0.17)], width=max(1.0, h * 0.022),
               fill=GOLD + (215,))
    for i in range(11):
        layer.ellipse(w * (0.08 + i * 0.084), h * 0.17, h * 0.030, h * 0.030, fill=GOLD + (235,))
    # Engraved scrollwork in the outer band, with a gilt highlight on every stroke.
    for i in range(4):
        x0 = w * (0.17 + i * 0.17)
        sign = 1.0 if i % 2 == 0 else -1.0
        curl = bezier([(x0, h * 0.30), (x0 + w * 0.050, h * 0.30 + h * 0.20 * sign),
                       (x0 + w * 0.100, h * 0.30 - h * 0.22 * sign),
                       (x0 + w * 0.150, h * 0.30)], steps=22)
        layer.line(curl, width=max(1.0, h * 0.028), fill=(58, 42, 28, 215))
        layer.line([(x, y - h * 0.016) for x, y in curl], width=max(1.0, h * 0.012),
                   fill=GOLD + (170,))
    # Proof stamps instead of a name: small hex punches down the flank band opposite the fuller (a
    # mark in the middle of the face would disappear under the inlay).  Jakobs signs its work with a
    # proof mark and gilt engraving, not with a printed plaque.
    for i in range(3):
        px, py = w * (0.30 + i * 0.19), h * 0.78
        layer.poly(hexagon(px, py, h * 0.070, h * 0.070, angle=math.pi / 6.0),
                   outline=GOLD + (205,), width=max(1.0, h * 0.013))
        layer.poly(hexagon(px, py, h * 0.042, h * 0.042, angle=math.pi / 6.0),
                   outline=(72, 54, 34, 185), width=max(1.0, h * 0.009))
        layer.ellipse(px, py, h * 0.013, h * 0.013, fill=(232, 214, 168, 195))
    # A repeating gilt lozenge motif along the tip end of the spine band: the family's fine detail.
    for i in range(5):
        x = w * (0.60 + i * 0.078)
        y = h * 0.30
        layer.poly([(x, y - h * 0.060), (x + w * 0.013, y), (x, y + h * 0.060),
                    (x - w * 0.013, y)], outline=GOLD + (200,), width=max(1.0, h * 0.014))
        layer.ellipse(x, y, h * 0.010, h * 0.010, fill=GOLD + (225,))
    # Etched border line just inside the cutting edge.
    layer.line([(w * 0.03, h * 0.90), (w * 0.97, h * 0.90)], width=max(1.0, h * 0.010),
               fill=(70, 54, 36, 170))
    # A dark ricasso plate at the hilt end, the way the family firm seats its maker's stamp.
    layer.poly([(w * 0.02, h * 0.60), (w * 0.12, h * 0.60), (w * 0.12, h * 0.94), (w * 0.02, h * 0.94)],
               fill=(58, 42, 28, 90), outline=GOLD + (170,), width=max(1.0, h * 0.014))
    # Honed wire along the cutting edge.
    layer.line([(w * 0.03, h * 0.945), (w * 0.97, h * 0.945)], width=max(1.5, h * 0.026),
               fill=(232, 230, 214, 150))
    return layer.finish()


def _blade_torgue(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    if variant == "panel":
        # The bolted-on orange plate: the house badge and a hazard border.  No wordmark - Torgue
        # stamps its monogram, never its name.
        layer.poly([(w * 0.03, h * 0.06), (w * 0.97, h * 0.06), (w * 0.97, h * 0.94),
                    (w * 0.03, h * 0.94)], outline=(32, 28, 30, 220), width=max(1.0, h * 0.03))
        for i in range(9):
            x = w * (0.06 + i * 0.11)
            layer.poly([(x, h * 0.06), (x + w * 0.035, h * 0.06), (x + w * 0.012, h * 0.20),
                        (x - w * 0.023, h * 0.20)], fill=(32, 28, 30, 180))
            layer.poly([(x, h * 0.94), (x + w * 0.035, h * 0.94), (x + w * 0.012, h * 0.80),
                        (x - w * 0.023, h * 0.80)], fill=(32, 28, 30, 180))
        # The badge sits at the forte end of the plate: the four dark cleats are bolted down the
        # middle and the hilt end, and a monogram behind a cleat would never be read.
        torgue_emblem(layer, w * 0.23, h * 0.50, h * 0.82, alpha=250)
        for i in range(3):
            x = w * (0.10 + i * 0.11)
            layer.poly([(x, h * 0.30), (x + w * 0.045, h * 0.30), (x + w * 0.030, h * 0.70),
                        (x - w * 0.015, h * 0.70)], fill=(32, 28, 30, 170))
        return layer.finish()
    # Primary: the steel slab around the orange plate.  Once the plate is bolted on, only the two
    # thin flanks show, so the signage lives in those bands and the middle is left clean.
    for edge in (0.055, 0.845):
        layer.line([(w * 0.04, h * (edge + 0.055)), (w * 0.96, h * (edge + 0.055))],
                   width=max(1.0, h * 0.012), fill=(54, 46, 42, 190))
        xs = [w * (0.06 + i * 0.11) for i in range(9)]
        _rivet_row(layer, xs, h * edge, h * 0.024, (96, 84, 70, 215))
        # Hazard band: diagonal stripes marching along both flanks.
        for i in range(9):
            x = w * (0.05 + i * 0.11)
            for base in (0.075, 0.865):
                layer.poly([(x, h * base), (x + w * 0.042, h * base),
                            (x + w * 0.028, h * (base + 0.055)),
                            (x - w * 0.014, h * (base + 0.055))], fill=(30, 26, 28, 205))
    # A pair of hazard triangles at the hilt end of the flanks - signage, not a wordmark.
    for cx, cy in ((w * 0.09, 0.30), (w * 0.90, 0.70)):
        triangle = [(w * cx, h * (cy - 0.10)), (w * cx + h * 0.13, h * (cy + 0.09)),
                    (w * cx - h * 0.13, h * (cy + 0.09))]
        layer.poly(triangle, fill=(246, 226, 170, 230), outline=(34, 28, 28, 240),
                   width=max(1.0, h * 0.018))
    return layer.finish()


def _blade_bandit(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    if variant == "panel":
        # The red slab is bolted down its tip half: the three modelled bolts sit at x=0.13..0.52 of
        # the plate, spanning y=0.33..0.82.  Bandits do not brand their work with a name (only
        # Hyperion does that), so the slab carries a weld seam and kill tallies instead - and the
        # tallies are scratched in by hand, so one group leans.
        blood_splatter(layer, rng, w * 0.86, h * 0.74, h * 0.30, alpha=215, drip=(-1.0, 0.15))
        blood_splatter(layer, rng, w * 0.34, h * 0.80, h * 0.26, alpha=200, drip=(-1.0, 0.0))
        blood_splatter(layer, rng, w * 0.62, h * 0.30, h * 0.18, alpha=165, drip=(-1.0, 0.0))
        grime_streaks(layer, rng, w * 0.5, h * 0.5, w * 0.9, h * 0.9, alpha=150, horizontal=True)
        # A rough weld bead laid along the spine edge, with its spatter.
        seam = [(w * 0.05 + i * w * 0.028, h * (0.19 + 0.014 * math.sin(i * 1.6))) for i in range(28)]
        layer.line(seam, width=max(1.5, h * 0.045), fill=(52, 32, 26, 195))
        layer.line([(x, y - h * 0.016) for x, y in seam], width=max(1.0, h * 0.018),
                   fill=(198, 172, 142, 130))
        for _ in range(9):
            i = rng.randrange(len(seam))
            layer.ellipse(seam[i][0], seam[i][1] + h * rng.uniform(-0.03, 0.03),
                          h * rng.uniform(0.010, 0.022), h * rng.uniform(0.010, 0.022),
                          fill=(46, 34, 28, 165))
        # Kill tallies in the hilt half (clear of the bolts), hand-scratched: one group leans.
        tally_marks(layer, rng, w * 0.58, h * 0.62, h * 0.26, (26, 20, 20, 230), groups=2,
                    tilt=0.26)
        tally_marks(layer, rng, w * 0.16, h * 0.14, h * 0.22, (26, 20, 20, 225), groups=2,
                    tilt=0.22)
        return layer.finish()
    # Primary: the steel body around the slab - blood, welds, scrapes and a scratched warning.
    blood_splatter(layer, rng, w * 0.16, h * 0.62, h * 0.34, alpha=205, drip=(-1.0, 0.3))
    blood_splatter(layer, rng, w * 0.50, h * 0.28, h * 0.24, alpha=185, drip=(-1.0, 0.0))
    blood_splatter(layer, rng, w * 0.86, h * 0.72, h * 0.20, alpha=175, drip=(-1.0, 0.2))
    grime_streaks(layer, rng, w * 0.5, h * 0.5, w * 0.92, h * 0.9, alpha=150, horizontal=True)
    tally_marks(layer, rng, w * 0.10, h * 0.72, h * 0.20, (28, 22, 22, 220), groups=3, tilt=0.24)
    # A spray-painted arrow pointing up the blade rather than a painted slogan: Bandit daubs its
    # work, it does not letter it (Hyperion is the only house that prints words on hardware).
    arrow_x, arrow_y = w * 0.50, h * 0.26
    layer.line([(arrow_x, arrow_y), (arrow_x + w * 0.105, arrow_y)], width=max(1.5, h * 0.045),
               fill=(34, 26, 24, 205))
    layer.poly([(arrow_x + w * 0.100, arrow_y - h * 0.080),
                (arrow_x + w * 0.150, arrow_y + h * 0.006),
                (arrow_x + w * 0.100, arrow_y + h * 0.092)], fill=(34, 26, 24, 205))
    for _ in range(11):
        layer.blot(rng.uniform(w * 0.06, w * 0.94), h * rng.uniform(0.12, 0.88),
                   h * rng.uniform(0.03, 0.070), rng, (46, 40, 36, 150), lobes=6)
    for _ in range(9):
        x = rng.uniform(w * 0.06, w * 0.94)
        y = h * rng.uniform(0.10, 0.90)
        layer.line([(x, y), (x + w * rng.uniform(0.02, 0.06), y + h * rng.uniform(-0.10, 0.10))],
                   width=max(1.0, h * 0.014), fill=(238, 234, 218, 130))
    return layer.finish()


def _blade_maliwan(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    if variant != "primary":
        return layer.finish()
    for _i, y in enumerate((0.14, 0.86)):
        layer.line([(w * 0.04, h * y), (w * 0.96, h * y)], width=max(1.0, h * 0.012),
                   fill=(44, 60, 76, 185))
        for k in range(8):
            layer.ellipse(w * (0.10 + k * 0.108), h * y, h * 0.020, h * 0.020,
                          fill=(74, 204, 220, 200))
    # The blade's flanks are too thin for the droplet sigil (it lands half under the emissive inlay
    # and reads as a clipped blob), so the blade is signed in fine cyan work only - hairlines, pips
    # and diamonds.  The sigil itself goes on the grip, where the facet is big enough to carry it.
    for y in (0.405, 0.595):
        layer.line([(w * 0.06, h * y), (w * 0.94, h * y)], width=max(1.0, h * 0.008),
                   fill=(74, 204, 220, 175))
    for i in range(3):
        x = w * (0.44 + i * 0.10)
        layer.poly([(x, h * 0.16), (x + w * 0.013, h * 0.22), (x, h * 0.28),
                    (x - w * 0.013, h * 0.22)], outline=(74, 204, 220, 190),
                   width=max(1.0, h * 0.010))
    return layer.finish()


BLADE_ART = {
    "Hyperion": _blade_hyperion,
    "Jakobs": _blade_jakobs,
    "Torgue": _blade_torgue,
    "Bandit": _blade_bandit,
    "Maliwan": _blade_maliwan,
}


def _grip_bandit(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    unit = min(w, h)
    grime_streaks(layer, rng, w * 0.5, h * 0.5, w, h, alpha=115 if variant != "panel" else 145)
    if variant != "panel":
        # Working dirt only: the wrap is handled every day, so the grime sits in the seams and a
        # couple of bare-metal gouges cross the paint.
        for edge_y, flip in ((0.0, 1.0), (h, -1.0)):
            for _ in range(4):
                x = rng.uniform(0, w)
                layer.line([(x, edge_y), (x + rng.uniform(-w * 0.1, w * 0.1),
                                          edge_y + flip * h * rng.uniform(0.08, 0.24))],
                           width=max(1.0, unit * 0.05), fill=(46, 40, 34, 120))
        for _ in range(5):
            x, y = rng.uniform(0, w), rng.uniform(0, h)
            layer.line([(x, y), (x + w * rng.uniform(0.1, 0.45), y + h * rng.uniform(-0.2, 0.2))],
                       width=max(1.0, unit * 0.035), fill=(240, 236, 218, 120))
        grime_streaks(layer, rng, w * 0.5, h * 0.5, w, h, alpha=125)
        for _ in range(4):
            layer.blot(rng.uniform(w * 0.1, w * 0.9), rng.uniform(h * 0.1, h * 0.9),
                       unit * rng.uniform(0.06, 0.14), rng, (44, 38, 32, 130), lobes=7)
        return layer.finish()
    fingerprints(layer, rng, w * 0.5, h * 0.55, unit * 0.60)
    blood_splatter(layer, rng, w * 0.40, h * 0.28, unit * 0.26, alpha=185, drip=(0.0, 1.0))
    tally_marks(layer, rng, w * 0.16, h * 0.14, unit * 0.16, (28, 24, 22, 210), groups=2,
                tilt=0.22)
    # A scrap of dirty tape, torn at both ends.
    layer.poly([(w * 0.12, h * 0.60), (w * 0.88, h * 0.56), (w * 0.92, h * 0.70), (w * 0.08, h * 0.74)],
               fill=(156, 146, 122, 170), outline=(48, 42, 36, 145), width=max(1.0, unit * 0.02))
    for _ in range(6):
        x = rng.uniform(w * 0.14, w * 0.86)
        layer.line([(x, h * 0.59), (x + w * 0.02, h * 0.72)], width=max(1.0, unit * 0.014),
                   fill=(72, 64, 52, 100))
    # No wordmark on the wrap: Bandit counts kills, it does not print its name.  A second tally
    # group goes on the wide facets instead.
    if w >= 120:
        tally_marks(layer, rng, w * 0.14, h * 0.84, unit * 0.14, (30, 24, 22, 220), groups=3,
                    tilt=0.24)
    return layer.finish()


def _grip_hyperion(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    unit = min(w, h)
    if variant != "panel":
        # A clean corporate handle: one thin compliance stripe and a hex vent.
        layer.poly([(w * 0.06, h * 0.12), (w * 0.94, h * 0.12), (w * 0.94, h * 0.22), (w * 0.06, h * 0.22)],
                   fill=(228, 188, 46, 210))
        layer.poly(hexagon(w * 0.5, h * 0.62, unit * 0.16, unit * 0.16),
                   outline=(40, 48, 58, 210), width=max(1.0, unit * 0.030))
        return layer.finish()
    layer.poly([(w * 0.05, h * 0.08), (w * 0.95, h * 0.08), (w * 0.95, h * 0.26), (w * 0.05, h * 0.26)],
               fill=(228, 188, 46, 232))
    for i in range(3):
        layer.poly(hexagon(w * (0.26 + i * 0.24), h * 0.17, unit * 0.065, unit * 0.065),
                   fill=(26, 26, 26, 235))
    # Barcode-style serial strip: reads as fine detail at any size.
    for i in range(9):
        x = w * (0.16 + i * 0.085)
        layer.line([(x, h * 0.52), (x, h * 0.74)], width=max(1.0, unit * (0.014 if i % 3 else 0.028)),
                   fill=(34, 40, 48, 220))
    for i in range(3):
        layer.poly(hexagon(w * (0.28 + i * 0.22), h * 0.88, unit * 0.055, unit * 0.055),
                   outline=(40, 48, 58, 210), width=max(1.0, unit * 0.014))
    if w >= 120:
        height = fit_height("HYPERION", unit * 0.13, w * 0.86, unit * 0.020)
        draw_text(layer, "HYPERION", w * 0.5, h * 0.105, height, (24, 24, 24, 245),
                  stroke=height * 0.24, tracking=unit * 0.020, shear=0.22, align="center")
    return layer.finish()


def _grip_jakobs(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    unit = min(w, h)
    # Hand-cut diamond chequering over the whole wrap (the family's grip pattern).
    step = unit / (3.4 if variant == "panel" else 4.6)
    row = 0
    y = step * 0.4
    while y < h + step:
        x = 0.0 if row % 2 else step * 0.5
        while x < w + step:
            layer.poly([(x, y - step * 0.42), (x + step * 0.42, y), (x, y + step * 0.42),
                        (x - step * 0.42, y)], outline=(52, 36, 24, 105),
                       width=max(1.0, step * 0.060))
            x += step
        y += step * 0.55
        row += 1
    if variant != "panel":
        return layer.finish()
    layer.line([(w * 0.5, h * 0.03), (w * 0.5, h * 0.97)], width=max(1.0, unit * 0.030),
               fill=GOLD + (180,))
    # A gilt pinline with inlay pins instead of the family name (Jakobs engraves, it does not print).
    for edge in (0.10, 0.90):
        layer.line([(w * 0.06, h * edge), (w * 0.94, h * edge)], width=max(1.0, unit * 0.014),
                   fill=GOLD + (185,))
    if w >= 85:
        # Gilt lozenges and inlay pins: at facet size the full escutcheon just reads as a smudge.
        for i in range(3):
            cx = w * (0.30 + i * 0.20)
            layer.poly([(cx, h * 0.34), (cx + w * 0.030, h * 0.50), (cx, h * 0.66),
                        (cx - w * 0.030, h * 0.50)], outline=GOLD + (215,),
                       width=max(1.0, unit * 0.018))
            layer.ellipse(cx, h * 0.50, unit * 0.014, unit * 0.014, fill=(232, 214, 168, 225))
    return layer.finish()


def _grip_torgue(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    unit = min(w, h)
    band = unit * 0.30
    y = h * 0.06
    while y < h:
        layer.poly([(0, y), (w, y - band * 0.30), (w, y + band * 0.70), (0, y + band)],
                   fill=(28, 26, 28, 150))
        y += band * 1.6
    layer.line([(0, h * 0.06), (w, h * 0.06 - band * 0.3)], width=max(2.0, unit * 0.040),
               fill=(214, 118, 38, 215))
    # The badge, not the name: a facet that is roughly square gets the hex monogram, a long rib
    # facet gets hazard chevrons instead (a badge squeezed into a rib just reads as a smudge).
    if variant == "panel" and w >= 85:
        if w <= h * 1.9:
            torgue_emblem(layer, w * 0.5, h * 0.48, unit * 0.86, alpha=240)
        else:
            step = unit * 0.30
            x = w * 0.08
            while x < w * 0.92:
                layer.poly([(x, h * 0.22), (x + step * 0.55, h * 0.22),
                            (x + step * 0.95, h * 0.74), (x + step * 0.40, h * 0.74)],
                           fill=(214, 118, 38, 210))
                x += step * 1.6
    return layer.finish()


def _grip_maliwan(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    layer = Layer(size)
    w, h = layer.size
    unit = min(w, h)
    for i in range(3):
        y = h * (0.18 + i * 0.28)
        layer.line([(w * 0.08, y), (w * 0.92, y)], width=max(1.0, unit * 0.020),
                   fill=(74, 204, 220, 195))
    if variant == "panel":
        maliwan_emblem(layer, w * 0.5, h * 0.52, unit * 0.46, alpha=240)
    return layer.finish()


GRIP_ART = {
    "Hyperion": _grip_hyperion,
    "Jakobs": _grip_jakobs,
    "Torgue": _grip_torgue,
    "Bandit": _grip_bandit,
    "Maliwan": _grip_maliwan,
}


def _accessory_strip(size: tuple[int, int], rng: random.Random, variant: Variant = "primary") -> Image.Image:
    """Fittings are manufacturer-neutral, so they get honest hardware detail: milled grooves, rivets,
    a stamped cartouche and a serial tick strip.  Compact modules (pommels, guards) stay clean."""
    layer = Layer(size)
    w, h = layer.size
    if w < h * 1.5:
        return layer.finish()
    unit = h
    for i in range(4):
        y = h * (0.24 + i * 0.17)
        layer.line([(w * 0.03, y), (w * 0.97, y)], width=max(1.0, h * 0.035),
                   fill=(52, 60, 68, 120))
        layer.line([(w * 0.03, y + h * 0.05), (w * 0.97, y + h * 0.05)], width=max(1.0, h * 0.022),
                   fill=(240, 238, 226, 90))
    for i in range(6):
        x = w * (0.08 + i * 0.165)
        layer.ellipse(x, h * 0.10, unit * 0.045, unit * 0.045, fill=(96, 104, 110, 215))
        layer.ellipse(x, h * 0.90, unit * 0.045, unit * 0.045, fill=(96, 104, 110, 215))
    # A stamped cartouche rather than a word: these strips are often saw-toothed, so lettering
    # would be chewed up by the silhouette.
    layer.poly([(w * 0.40, h * 0.28), (w * 0.52, h * 0.28), (w * 0.52, h * 0.70), (w * 0.40, h * 0.70)],
               outline=(52, 60, 68, 200), width=max(1.0, h * 0.035))
    layer.ellipse(w * 0.46, h * 0.49, h * 0.055, h * 0.055, fill=(52, 60, 68, 210))
    for i in range(9):
        x = w * (0.34 + i * 0.035)
        layer.line([(x, h * 0.60), (x, h * 0.68)], width=max(1.0, h * 0.018),
                   fill=(40, 46, 52, 190))
    return layer.finish()


#: Minimum island area (in atlas pixels) before a face is worth decorating at all.
MIN_ART_AREA = 1800
#: Grip facets at least this wide (in art pixels, i.e. physically) can carry the branded plate - the
#: badge, escutcheon or (Hyperion only) the wordmark.  Narrower facets receive only pattern and grime.
GRIP_LETTER_MIN_PX = 85
#: ... and no longer than this (metres) along the handle, which excludes the wrap strips: those are
#: several modelled faces sharing one island, so any plate laid across one would be cut by the seams.
GRIP_LETTER_MAX_LENGTH_M = 0.22


def _target_islands(layout: PartLayout) -> list[Island]:
    islands = [isl for isl in layout.islands if isl.owner and isl.rect[2] * isl.rect[3] >= MIN_ART_AREA]
    islands.sort(key=lambda isl: isl.rect[2] * isl.rect[3], reverse=True)
    return islands


#: Part slots (as exported in the socket manifest) -> the art family that decorates them.
SLOT_ART_ROLE = {
    "BarrelPartDefinition": "blade",
    "GripPartDefinition": "grip",
    "Accessory1PartDefinition": "accessory",
}


def slot_role(part: Part) -> str:
    """`blade` / `grip` / `accessory` / `` for a part, from its UE3 slot (id prefix as fallback)."""
    role = SLOT_ART_ROLE.get(part.slot)
    if role:
        return role
    for prefix in ("blade", "grip", "accessory"):
        if part.part_id.startswith(prefix + "_"):
            return prefix
    return ""


def _art_function(part: Part):
    """The plate composer for a part's slot/manufacturer (None = nothing to stamp)."""
    role = slot_role(part)
    if role == "blade":
        return BLADE_ART.get(part.manufacturer)
    if role == "grip":
        return GRIP_ART.get(part.manufacturer)
    if role == "accessory":
        return _accessory_strip
    return None


def paint_manufacturer_decals(
    part: Part,
    layout: PartLayout,
    canvas: Image.Image,
    rng: random.Random | None = None,
) -> int:
    """Stamp the manufacturer's own art onto its faces. Returns the number of plates applied.

    Blades take one plate per broad face (front and rear), authored along the blade so the art reads
    from hilt to point.  Grips take a smaller wrap plate on each of their largest side facets.
    Everything is sized from the face's own physical measurements, so a marking is the same size in
    metres no matter which part or atlas resolution it lands on.
    """
    rng = rng or random.Random(0)
    art_fn = _art_function(part)
    role = slot_role(part)
    targets = _target_islands(layout)
    if art_fn is None or role not in ("blade", "grip", "accessory") or not targets:
        return 0
    largest = targets[0].rect[2] * targets[0].rect[3]
    applied = 0

    for island in targets:
        area = island.rect[2] * island.rect[3]
        frame = island_frame(part, island)
        if frame is None:
            continue
        a_min, a_max, b_min, b_max = frame_bounds(island.polygon, frame)
        across_m, along_m = a_max - a_min, b_max - b_min
        if across_m <= 0 or along_m <= 0:
            continue
        # Sanity: an island can never be much bigger than its own face, so a wild bounding box means
        # the fitted frame is unreliable — skip rather than paint a plate across the whole atlas.
        verts = part.faces[island.face].verts
        extent = max(
            math.dist(a, b) for a in verts for b in verts
        ) if len(verts) > 1 else 0.0
        if max(across_m, along_m) > max(extent, 1e-3) * 1.9:
            continue
        anchor = ((a_min + a_max) / 2.0, (b_min + b_max) / 2.0)

        if role == "blade":
            # Broad faces only: the blade's own skin, plus any bolted-on panel that covers a big
            # share of it (that panel is usually the surface you actually see).
            if area < largest * 0.40 or along_m < across_m * 1.2:
                continue
            variant = "primary" if area >= largest * 0.80 else "panel"
            art_w, art_h = along_m * 0.92, across_m * 0.88
            art = art_fn((max(64, int(art_w * frame.px_per_m)),
                          max(48, int(art_h * frame.px_per_m))), rng, variant)
            if art.getbbox() is None:  # this part has no plate for that variant
                continue
            applied += stamp(canvas, island, art, frame, anchor, (art_w, art_h), orientation="along")
        elif role == "accessory":
            # Only the blade-mounted strips are long enough to etch; compact modules stay clean.
            if area < largest * 0.45 or along_m < across_m * 2.0:
                continue
            art_w, art_h = along_m * 0.94, across_m * 0.90
            art = art_fn((max(64, int(art_w * frame.px_per_m)),
                          max(32, int(art_h * frame.px_per_m))), rng, "primary")
            if art.getbbox() is None:
                continue
            applied += stamp(canvas, island, art, frame, anchor, (art_w, art_h), orientation="along")
        else:
            # Grips are wrapped, so every facet that can hold paint is painted. A facet wide enough
            # to carry the branded plate gets it; the narrow ones only get working grime (a badge a
            # few millimetres across would just read as a smudge).
            if area < max(700, largest * 0.06) or min(across_m, along_m) < 0.010:
                continue
            art_w, art_h = across_m * 0.94, along_m * 0.94
            # Only the small, self-contained facets (ribs, end caps) can carry a plate: the long
            # wrap strips are several modelled faces sharing one island, so anything laid across one
            # would be cut into fragments by the seams.
            big_facet = (art_w * frame.px_per_m >= GRIP_LETTER_MIN_PX
                         and along_m <= GRIP_LETTER_MAX_LENGTH_M)
            art = art_fn((max(48, int(art_w * frame.px_per_m)),
                          max(48, int(art_h * frame.px_per_m))), rng,
                         "panel" if big_facet else "primary")
            applied += stamp(canvas, island, art, frame, anchor, (art_w, art_h), orientation="across")
    return applied
