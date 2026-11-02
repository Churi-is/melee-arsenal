"""Texture stage helpers: flat colours, mottling, bold linework and decals.

Each helper paints *one layer* onto an existing atlas and is deliberately independent of the others,
so the pipeline can stack, reorder, cache or extend them (a decals/stencil/grime pass is just another
function with the same shape).

Everything is Pillow-only and deterministic: all randomness comes from `random.Random` seeded from the
part id plus a stage salt, never from global state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import random
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .config import (
    ATLAS_BACKGROUND,
    ELEMENTS,
    HATCH_RGB,
    INK_RGB,
    INK_SOFT_RGB,
    PALETTE,
    SHADOW_RGB,
    hex_to_rgb,
)
from .geometry import Part
from .uv import Island, PartLayout, Vec2

#: `(r, g, b)`
RGB = tuple[int, int, int]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def stage_rng(part_id: str, salt: str) -> random.Random:
    """Stable RNG for `(part, stage)` — independent of stage ordering or call counts."""
    digest = hashlib.sha256(f"{part_id}|{salt}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "little"))


# ---------------------------------------------------------------------------
# Per-island role colours
# ---------------------------------------------------------------------------
def role_colours(part: Part, layout: PartLayout, cfg_seed: str) -> dict[int, RGB]:
    """Resolve the flat base colour of every owned island.

    Tags address the manufacturer palette exactly like R3 did (`paint` / `metal` / `dark` / `trim` /
    `edge` / `accent`), with the per-island brightness jitter and the 25%-toward-white `edge` rule.
    """
    palette = PALETTE.get(part.manufacturer, PALETTE["Universal"])
    element_hex = str(part.meta.get("element") or "")
    accent = hex_to_rgb(ELEMENTS[element_hex]) if element_hex in ELEMENTS else hex_to_rgb(palette["accent"])
    rng = stage_rng(part.part_id, f"colours|{cfg_seed}")
    colours: dict[int, RGB] = {}
    for island in layout.islands:
        if not island.owner:
            continue
        if island.role == "accent":
            base = accent
        else:
            base = hex_to_rgb(palette.get(island.role, palette["metal"]))
        factor = rng.uniform(0.90, 1.06)
        colour = tuple(min(255, int(c * factor)) for c in base)
        if island.role == "edge":
            colour = tuple(min(255, int(v * 0.75 + 255 * 0.25)) for v in colour)
        colours[island.index] = colour  # type: ignore[assignment]
    return colours


# ---------------------------------------------------------------------------
# Canvas + island patch helpers
# ---------------------------------------------------------------------------
def new_canvas(layout: PartLayout) -> Image.Image:
    return Image.new("RGB", layout.atlas_size, ATLAS_BACKGROUND)


def _local_polygon(island: Island, origin: tuple[int, int]) -> list[Vec2]:
    ox, oy = origin
    return [(x - ox, y - oy) for x, y in island.polygon]


def _island_box(
    island: Island, layout: PartLayout, grow: int
) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    width, height = layout.atlas_size
    x, y, w, h = island.rect
    box = (max(0, x - grow), max(0, y - grow), min(width, x + w + grow), min(height, y + h + grow))
    return box, (box[0], box[1])


def _island_mask(island: Island, size: tuple[int, int], origin: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(_local_polygon(island, origin), fill=255)
    return mask


def edit_island(
    canvas: Image.Image,
    island: Island,
    layout: PartLayout,
    fn: Callable[[Image.Image, Image.Image, list[Vec2]], Image.Image],
    grow: int = 0,
) -> None:
    """Run `fn(patch_rgb, mask, local_polygon)` on a cropped island and paste the result back."""
    box, origin = _island_box(island, layout, grow)
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    patch = canvas.crop(box)
    size = (box[2] - box[0], box[3] - box[1])
    mask = _island_mask(island, size, origin)
    canvas.paste(fn(patch, mask, _local_polygon(island, origin)), box[:2])


def draw_island(
    canvas: Image.Image,
    island: Island,
    layout: PartLayout,
    painter: Callable[[ImageDraw.ImageDraw, list[Vec2], tuple[int, int]], None],
) -> None:
    """Draw a transparent overlay clipped to the island and composite it in one go."""
    box, origin = _island_box(island, layout, 0)
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    size = (box[2] - box[0], box[3] - box[1])
    poly = _local_polygon(island, origin)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    painter(ImageDraw.Draw(layer), poly, size)
    mask = _island_mask(island, size, origin)
    masked = Image.composite(layer, Image.new("RGBA", size, (0, 0, 0, 0)), mask)
    out = Image.alpha_composite(canvas.crop(box).convert("RGBA"), masked)
    canvas.paste(out.convert("RGB"), box[:2])


# ---------------------------------------------------------------------------
# Deterministic gaussian fields (Pillow-only replacement for numpy noise)
# ---------------------------------------------------------------------------
_FIELD_SIGMA = 32.0  # a signed field of +/-1 sigma spans 32 grey levels around 128


def _unit_field(size: tuple[int, int], cells: float, rng: random.Random) -> Image.Image:
    """A smooth, zero-mean field rendered as `128 + sigma * N(0, 1)` in 8-bit grey."""
    width, height = size
    cells = max(1.0, float(cells))
    small_w = max(2, int(math.ceil(width / cells)) + 1)
    small_h = max(2, int(math.ceil(height / cells)) + 1)
    noise = Image.frombytes("L", (small_w, small_h), rng.randbytes(small_w * small_h))
    field = noise.resize(size, Image.Resampling.BICUBIC)
    # Uniform bytes have sigma ~= 73.9; rescale so the field carries exactly `_FIELD_SIGMA`.
    gain = _FIELD_SIGMA / 73.9
    return field.point([max(0, min(255, int(round(128 + (v - 128) * gain)))) for v in range(256)])


def _signed_field(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """R3's three-octave mottle: 5.2% broad wash + 3.0% broken mid + 0.7% fine tooth."""

    def scaled(field: Image.Image, weight: float) -> Image.Image:
        return field.point([max(0, min(255, int(round(128 + (v - 128) * weight)))) for v in range(256)])

    combined = ImageChops.add(
        ImageChops.add(
            scaled(_unit_field(size, 36, rng), 0.052),
            scaled(_unit_field(size, 10, rng), 0.030),
            scale=1,
            offset=-128,
        ),
        scaled(_unit_field(size, 1, rng), 0.007),
        scale=1,
        offset=-128,
    )
    return combined


def apply_grain(patch: Image.Image, mask: Image.Image, rng: random.Random) -> Image.Image:
    """Multiplicative `pixel * (1 + noise)` that works in 8-bit through add/subtract chops."""
    field = _signed_field(patch.size, rng)
    # Split the signed field into a brighten gain and a darken gain.
    pos = field.point([max(0, min(255, int(round(255 * max(0.0, (v - 128) / _FIELD_SIGMA))))) for v in range(256)])
    neg = field.point([max(0, min(255, int(round(255 * max(0.0, (128 - v) / _FIELD_SIGMA))))) for v in range(256)])
    brightened = ImageChops.add(patch, ImageChops.multiply(patch, pos.convert("RGB")))
    out = ImageChops.subtract(brightened, ImageChops.multiply(patch, neg.convert("RGB")))
    return Image.composite(out, patch, mask)


# ---------------------------------------------------------------------------
# Stage: flat base colours
# ---------------------------------------------------------------------------
def paint_flat_colours(
    part: Part, layout: PartLayout, canvas: Image.Image, cfg_seed: str = "inkblade"
) -> Image.Image:
    """Fill every owned island polygon with its palette colour (the 'flat texture' stage)."""
    colours = role_colours(part, layout, cfg_seed)
    draw = ImageDraw.Draw(canvas)
    for island in layout.islands:
        if not island.owner:
            continue
        draw.polygon(island.polygon, fill=colours[island.index])
    return canvas


# ---------------------------------------------------------------------------
# Stage: mottling / deposits / wear (R3 painter's detail pass)
# ---------------------------------------------------------------------------
def paint_mottle(part: Part, layout: PartLayout, canvas: Image.Image) -> Image.Image:
    """Broad paint wash, deposits, scratches, chipped paint, gouges and micro ink marks."""
    rng = stage_rng(part.part_id, "mottle")

    for island in layout.islands:
        if not island.owner:
            continue
        seed = rng.randrange(2**32)
        area = max(1, island.rect[2] * island.rect[3])

        def painter(draw: ImageDraw.ImageDraw, poly: list[Vec2], size: tuple[int, int], *, seed=seed, area=area) -> None:
            local = random.Random(seed)
            w, h = size
            # Irregular faint deposits instead of a repeated camouflage pattern.
            for _ in range(max(1, area // 2500)):
                cx, cy = local.randrange(w), local.randrange(h)
                r = local.uniform(3, 10)
                pts = []
                for k in range(7):
                    angle = k * math.pi * 2 / 7
                    radius = r * local.uniform(0.45, 1.3)
                    pts.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius * 0.5))
                draw.polygon(pts, fill=(37, 34, 27, local.randrange(12, 29)))
            # Dry-brush streaks.
            for j in range(max(1, area // 650)):
                x, y = local.randrange(w), local.randrange(h)
                dx, dy = local.randrange(5, 22), local.randrange(-5, 6)
                colour = (244, 237, 210, local.randrange(42, 84)) if j % 3 == 0 else (31, 36, 39, local.randrange(30, 63))
                draw.line(
                    [(x, y), (x + dx * 0.45, y + dy * 0.5 + local.choice([-1, 0, 1])), (x + dx, y + dy)],
                    fill=colour,
                    width=1,
                )
            # Paired gouges with a warm exposed highlight.
            for _ in range(max(1, area // 2200)):
                x, y = local.randrange(w), local.randrange(h)
                dx, dy = local.randrange(9, 31), local.randrange(-7, 8)
                draw.line([(x, y), (x + dx * 0.42, y + dy * 0.4)], fill=(26, 30, 34, 115), width=1)
                draw.line([(x + dx * 0.60, y + dy * 0.6), (x + dx, y + dy)], fill=(26, 30, 34, 90), width=1)
                draw.line([(x + 1, y + 2), (x + dx * 0.7, y + dy + 2)], fill=(244, 233, 199, 90), width=1)
            # Chipped paint: dark undercut + warm exposed metal.
            for _ in range(max(1, area // 2000)):
                x, y = local.randrange(w), local.randrange(h)
                r = local.randrange(2, 6)
                chip = [(x, y), (x + r, y - 1), (x + r + 2, y + 1), (x + r - 1, y + 3), (x - 1, y + 2)]
                draw.polygon([(xx, yy + 1) for xx, yy in chip], fill=(28, 32, 34, 160))
                draw.polygon(chip, fill=(213, 218, 201, 135))
            # Tiny tally / fastener marks on broad panels only.
            if min(w, h) > 55:
                for j in range(max(1, area // 11000)):
                    x = local.randrange(18, max(19, w - 20))
                    y = local.randrange(18, max(19, h - 20))
                    if j % 2:
                        for k in range(3):
                            draw.line([(x + k * 4, y), (x + k * 4 - 1, y + 7)], fill=(26, 32, 37, 150), width=1)
                        draw.line([(x - 2, y + 6), (x + 10, y + 1)], fill=(26, 32, 37, 135), width=1)
                    else:
                        draw.ellipse([(x - 2, y - 2), (x + 2, y + 2)], outline=(29, 34, 38, 155), width=1)
                        draw.line([(x - 1, y), (x + 1, y)], fill=(233, 225, 196, 160), width=1)
            # Chips just inside the island edges (the ink stage restores the black perimeter).
            if len(poly) >= 3:
                cx = sum(p[0] for p in poly) / len(poly)
                cy = sum(p[1] for p in poly) / len(poly)
                for a, b in zip(poly, poly[1:] + poly[:1]):
                    for _ in range(int(math.dist(a, b) / 45)):
                        t = local.uniform(0.08, 0.92)
                        x = a[0] + (b[0] - a[0]) * t
                        y = a[1] + (b[1] - a[1]) * t
                        vx, vy = cx - x, cy - y
                        norm = max(1.0, math.hypot(vx, vy))
                        x += vx / norm * 6
                        y += vy / norm * 6
                        draw.line([(x - 2, y), (x + 3, y - 1)], fill=(240, 233, 204, 155), width=2)

        edit_island(canvas, island, layout, lambda patch, mask, poly, p=painter: _grain_and_overlay(patch, mask, poly, p, seed), grow=0)
    return canvas


def _grain_and_overlay(
    patch: Image.Image,
    mask: Image.Image,
    poly: list[Vec2],
    painter: Callable,
    seed: int,
) -> Image.Image:
    """Multiplicative grain, then the transparent detail overlay, both clipped to the island."""
    grained = apply_grain(patch, mask, random.Random(seed ^ 0x5EED))
    layer = Image.new("RGBA", patch.size, (0, 0, 0, 0))
    painter(ImageDraw.Draw(layer), poly, patch.size)
    masked = Image.composite(layer, Image.new("RGBA", patch.size, (0, 0, 0, 0)), mask)
    return Image.alpha_composite(grained.convert("RGBA"), masked).convert("RGB")


# ---------------------------------------------------------------------------
# Stage: bold linework (R5 / Bold ink pass)
# ---------------------------------------------------------------------------
def _polygon_area(poly: Sequence[Vec2]) -> float:
    area = 0.0
    for (x0, y0), (x1, y1) in zip(poly, list(poly[1:]) + [poly[0]]):
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0


def paint_linework(part: Part, layout: PartLayout, canvas: Image.Image, ink_px: int = 6) -> Image.Image:
    """Weighted contours, angular shadow planes, corner hatching and directional scratches."""
    rng = stage_rng(part.part_id, "linework")
    stroke_large = max(2, int(ink_px))
    stroke_medium = max(2, int(ink_px) - 1)
    stroke_small = max(1, int(ink_px) - 3)

    for island in layout.islands:
        if not island.owner:
            continue
        _, _, w, h = island.rect
        area = _polygon_area(island.polygon)
        if area < 1.0 or min(w, h) < 2:
            continue
        seed = rng.randrange(2**32)

        def painter(draw: ImageDraw.ImageDraw, poly: list[Vec2], size: tuple[int, int], *, seed=seed, area=area, w=w, h=h) -> None:
            local = random.Random(seed)
            if len(poly) < 3:
                return
            short = min(w, h)
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)

            # Two bands of flat, translucent shadow form angular comic-book value planes.
            if area > 1100:
                candidates = sorted(
                    range(len(poly)),
                    key=lambda i: math.dist(poly[i], poly[(i + 1) % len(poly)]),
                    reverse=True,
                )
                for index in candidates[: 1 if area < 7000 else 2]:
                    p = poly[index]
                    q = poly[(index + 1) % len(poly)]
                    centre = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                    tx, ty = cx - centre[0], cy - centre[1]
                    norm = math.hypot(tx, ty) + 1e-9
                    tx, ty = tx / norm, ty / norm
                    inset = min(20.0, max(5.0, short * 0.13))
                    shallow = inset * 0.25
                    draw.polygon(
                        [
                            (p[0] + tx * shallow, p[1] + ty * shallow),
                            (q[0] + tx * shallow, q[1] + ty * shallow),
                            (q[0] + tx * inset, q[1] + ty * inset),
                            (p[0] + tx * inset, p[1] + ty * inset),
                        ],
                        fill=(*SHADOW_RGB, local.randint(28, 52)),
                    )

            # Weighted dark perimeter: retraces the island boundary only, never triangle diagonals.
            weight = stroke_small if short < 22 else stroke_medium if short < 65 else stroke_large
            draw.line(list(poly) + [poly[0]], fill=(*INK_SOFT_RGB, 230), width=weight, joint="curve")

            # Broken second pen-strokes follow the edges with a little variation in placement.
            for i, p in enumerate(poly):
                q = poly[(i + 1) % len(poly)]
                length = math.dist(p, q)
                if length < 25 or local.random() > 0.82:
                    continue
                ix, iy = cx - (p[0] + q[0]) * 0.5, cy - (p[1] + q[1]) * 0.5
                norm = math.hypot(ix, iy) + 1e-9
                ix, iy = ix / norm, iy / norm
                a = (
                    p[0] + (q[0] - p[0]) * local.uniform(0.06, 0.2) + ix * local.uniform(5, 9),
                    p[1] + (q[1] - p[1]) * local.uniform(0.06, 0.2) + iy * local.uniform(5, 9),
                )
                z = (
                    p[0] + (q[0] - p[0]) * local.uniform(0.68, 0.94) + ix * local.uniform(5, 9),
                    p[1] + (q[1] - p[1]) * local.uniform(0.68, 0.94) + iy * local.uniform(5, 9),
                )
                mid = (
                    (a[0] + z[0]) * 0.5 + ix * local.uniform(-1.2, 1.2),
                    (a[1] + z[1]) * 0.5 + iy * local.uniform(-1.2, 1.2),
                )
                draw.line([a, mid, z], fill=(17, 17, 21, local.randint(150, 220)), width=local.choice([2, 2, 3]))

            # Corner shadows under clustered, uneven hatch strokes.
            if area > 550:
                eligible = []
                for i, corner in enumerate(poly):
                    la = math.dist(poly[i - 1], corner)
                    lb = math.dist(poly[(i + 1) % len(poly)], corner)
                    if min(la, lb) >= 12:
                        eligible.append(i)
                local.shuffle(eligible)
                count = min(len(eligible), 2 if area < 3500 else 3 if area < 14000 else 5)
                for i in eligible[:count]:
                    corner = poly[i]
                    px, py = poly[i - 1][0] - corner[0], poly[i - 1][1] - corner[1]
                    qx, qy = poly[(i + 1) % len(poly)][0] - corner[0], poly[(i + 1) % len(poly)][1] - corner[1]
                    la = math.hypot(px, py) or 1.0
                    lb = math.hypot(qx, qy) or 1.0
                    px, py = px / la, py / la
                    qx, qy = qx / lb, qy / lb
                    span = min(105.0, max(16.0, math.sqrt(area) * 0.42), la * 0.5, lb * 0.5)
                    draw.polygon(
                        [
                            corner,
                            (corner[0] + px * span * 0.9, corner[1] + py * span * 0.9),
                            (corner[0] + qx * span * 0.9, corner[1] + qy * span * 0.9),
                        ],
                        fill=(*SHADOW_RGB, local.randint(35, 66)),
                    )
                    steps = max(4, min(13, int(span / 6)))
                    for j in range(1, steps + 1):
                        f = (j + 0.4) / float(steps + 2)
                        jitter = local.uniform(-1.0, 1.0)
                        u = span * f
                        start = (corner[0] + px * u + qx * 2, corner[1] + py * u + qy * 2)
                        end = (
                            corner[0] + qx * (u * local.uniform(0.75, 1.05)) + px * 2,
                            corner[1] + qy * (u * local.uniform(0.75, 1.05)) + py * 2,
                        )
                        mid = ((start[0] + end[0]) * 0.5 + jitter, (start[1] + end[1]) * 0.5 - jitter)
                        draw.line(
                            [start, mid, end],
                            fill=(*HATCH_RGB, local.randint(180, 240)),
                            width=local.choice([2, 2, 3]),
                        )
                    if span > 30 and local.random() < 0.55:
                        p = (corner[0] + px * span * 0.66 + qx * span * 0.1, corner[1] + py * span * 0.66 + qy * span * 0.1)
                        q = (p[0] + qx * min(14, span * 0.22), p[1] + qy * min(14, span * 0.22))
                        draw.line([p, q], fill=(17, 17, 22, 185), width=2)

            # Grouped directional scratches, including offset highlight nicks.
            if area > 2500:
                x0, y0 = min(p[0] for p in poly), min(p[1] for p in poly)
                for _ in range(min(22, int(area / 5000) + 4)):
                    px = local.uniform(x0 + 1, x0 + w - 2)
                    py = local.uniform(y0 + 1, y0 + h - 2)
                    angle = local.uniform(-0.35, 0.35) if w >= h else local.uniform(1.18, 1.94)
                    length = local.uniform(5, min(45, max(7, max(w, h) * 0.10)))
                    dx, dy = math.cos(angle) * length, math.sin(angle) * length
                    draw.line(
                        [(px, py), (px + dx * 0.55, py + dy * 0.55), (px + dx, py + dy)],
                        fill=(20, 19, 23, local.randint(125, 205)),
                        width=local.choice([1, 2, 2]),
                    )
                    if local.random() < 0.35:
                        draw.line([(px + 1, py + 3), (px + dx * 0.6, py + dy * 0.6 + 3)], fill=(255, 240, 210, 130), width=1)

        draw_island(canvas, island, layout, painter)
    return canvas


# ---------------------------------------------------------------------------
# Stage: decals (extension point)
# ---------------------------------------------------------------------------
def paint_decals(
    part: Part,
    layout: PartLayout,
    canvas: Image.Image,
    decal_dir: Path,
    fit: float = 0.62,
) -> int:
    """Stamp `<decal_dir>/<part_id>/*.png` onto paint islands. Returns the number of decals applied.

    This stage is the template for future art passes: it consumes the same layout, respects the same
    island masks, and stays a no-op when no art is supplied, so the stack keeps its shape.
    """
    folder = Path(decal_dir) / part.part_id
    if not folder.is_dir():
        return 0
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".png")
    if not files:
        return 0
    targets = [isl for isl in layout.islands if isl.owner and isl.role == "paint" and min(isl.rect[2], isl.rect[3]) >= 24]
    if not targets:
        return 0
    applied = 0
    for file_index, path in enumerate(files):
        island = targets[file_index % len(targets)]
        decal = Image.open(path).convert("RGBA")
        box, origin = _island_box(island, layout, 0)
        size = (box[2] - box[0], box[3] - box[1])
        scale = max(0.05, min(1.0, fit))
        target = (max(4, int(size[0] * scale)), max(4, int(size[1] * scale)))
        decal = decal.resize(target, Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        layer.paste(decal, ((size[0] - target[0]) // 2, (size[1] - target[1]) // 2), decal)
        mask = _island_mask(island, size, origin)
        out = Image.alpha_composite(canvas.crop(box).convert("RGBA"), Image.composite(layer, Image.new("RGBA", size, (0, 0, 0, 0)), mask))
        canvas.paste(out.convert("RGB"), box[:2])
        applied += 1
    return applied


# ---------------------------------------------------------------------------
# Finalise: seal island colours into the padding, then quantise
# ---------------------------------------------------------------------------
def seal_padding(canvas: Image.Image, layout: PartLayout, colours: dict[int, RGB]) -> Image.Image:
    """Dilate each island's own art into its padding so bilinear filtering never pulls pale seams."""
    width, height = layout.atlas_size
    pad = Image.new("RGB", (width, height), ATLAS_BACKGROUND)
    for island in layout.islands:
        if not island.owner:
            continue
        colour = colours.get(island.index, ATLAS_BACKGROUND)
        box, origin = _island_box(island, layout, max(2, layout.padding))
        size = (box[2] - box[0], box[3] - box[1])
        mask = _island_mask(island, size, origin)
        grown = mask.filter(ImageFilter.MaxFilter(7))
        patch = canvas.crop(box)
        fill = Image.new("RGB", size, colour)
        pad.paste(Image.composite(patch, fill, grown), box[:2])
    return pad


def finalise_atlas(
    canvas: Image.Image,
    layout: PartLayout,
    colours: dict[int, RGB],
    quantize_colors: int | None = 64,
) -> Image.Image:
    """Seal padding and apply the shipped indexed-palette quantisation."""
    sealed = seal_padding(canvas, layout, colours)
    if quantize_colors:
        return sealed.quantize(colors=quantize_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    return sealed


def recolor_accent(
    atlas: Image.Image,
    layout: PartLayout,
    hex_colour: str,
) -> Image.Image:
    """Bake an element tint into the `accent` islands only (keeps ink and `SURFACE` untouched)."""
    rgb = hex_to_rgb(hex_colour)
    out = atlas.convert("RGB").copy()
    for island in layout.islands:
        if island.role != "accent":
            continue
        x, y, w, h = island.rect
        patch = out.crop((x, y, x + w, y + h))
        pixels = patch.load()
        for py in range(h):
            for px in range(w):
                value = pixels[px, py]
                lum = max(value) / 255.0
                if lum > 0.22:
                    pixels[px, py] = tuple(int(min(255, c * 255 * lum)) for c in rgb)
        out.paste(patch, (x, y))
    return out
