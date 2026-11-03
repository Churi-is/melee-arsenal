"""Offline preview renders: a small software rasteriser, so previews need no Blender.

This module renders previews in pure Python without requiring Blender:

* flat per-triangle lighting from a three-light rig (key / fill / bounce), two-sided normals,
* back-face culled, z-buffered rasterisation with `ss`x supersampling for clean silhouettes,
* the painted atlas is sampled per pixel, so the ink linework and hatching show up in every preview,
* orthographic framing that fits each part to the frame, with 720x720 tiles by default.

Output layout::

    <out>/parts/<part_id>.png
    <out>/combinations/<recipe>.png
    <out>/overview_parts.png
    <out>/overview_combinations.png
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageFont

from .geometry import COMBINATIONS, PART_ORDER, Part
from .uv import PartLayout, Vec2, Vec3, project_face
from .glb import triangulate, _to_gltf

BACKGROUND: tuple[int, int, int] = (144, 151, 157)
TITLE_BAR: tuple[int, int, int] = (229, 172, 49)
SHEET_PAPER: tuple[int, int, int] = (232, 226, 211)
LABEL_BG: tuple[int, int, int] = (24, 24, 26)
LABEL_FG: tuple[int, int, int] = (240, 240, 240)

#: Key light (upper-left, in front), fill (right), bounce (from below/behind).
_LIGHTS: tuple[tuple[Vec3, float], ...] = (
    ((-0.46, 0.78, 0.62), 0.34),
    ((0.82, 0.18, 0.36), 0.16),
    ((0.05, -0.72, 0.42), 0.10),
)
_AMBIENT = 0.60


# ---------------------------------------------------------------------------
# Scene gathering
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Triangle:
    """One shaded triangle: glTF-space positions, atlas UVs, and its outward normal."""

    a: Vec3
    b: Vec3
    c: Vec3
    uv_a: Vec2
    uv_b: Vec2
    uv_c: Vec2
    normal: Vec3
    accent: bool = False
    material: int = 0


def _shift(point: Vec3, offset: Vec3) -> Vec3:
    return (point[0] + offset[0], point[1] + offset[1], point[2] + offset[2])


def _normal(pa: Vec3, pb: Vec3, pc: Vec3) -> Vec3:
    ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
    vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (nx / length, ny / length, nz / length)


def part_triangles(
    part: Part,
    layout: PartLayout,
    *,
    offset: Vec3 = (0.0, 0.0, 0.0),
    mirror: bool = False,
    material: int = 0,
) -> list[Triangle]:
    """Triangulate one part in glTF space, optionally mirrored through glTF Z (bifacial halves)."""
    out: list[Triangle] = []
    for face, uvs in zip(part.faces, layout.face_uvs, strict=False):
        projected = project_face(face.verts, 1.0, 0)
        if len(projected.points) < 3:
            continue
        for tri in triangulate(projected.points):
            positions: list[Vec3] = []
            for index in tri:
                x, y, z = _to_gltf(face.verts[index])
                if mirror:
                    z = -z
                positions.append((x + offset[0], y + offset[1], z + offset[2]))
            tex = [uvs[index] for index in tri]
            out.append(
                Triangle(
                    a=positions[0],
                    b=positions[1],
                    c=positions[2],
                    uv_a=tex[0],
                    uv_b=tex[1],
                    uv_c=tex[2],
                    normal=_normal(*positions),
                    accent=face.tag == "accent",
                    material=material,
                )
            )
    return out


def part_scene(part: Part, layout: PartLayout, gap: float = 0.0) -> list[Triangle]:
    """All geometry of one part, including both `FACE_FRONT` / `FACE_BACK` instances.

    `gap` is the host blade's `accessory_face_gap_m` / `element_face_gap_m`: combinations push the
    decorative plate onto the blade skin exactly like the exported GLBs do.
    """
    if not part.bifacial:
        return part_triangles(part, layout)
    shift = float(part.fit_inner_shift_m) + float(gap)
    return part_triangles(part, layout, offset=(0.0, 0.0, shift)) + part_triangles(
        part, layout, offset=(0.0, 0.0, -shift), mirror=True
    )


def assembly_scene(
    state,  # noqa: ANN001 - KitState, duck typed to keep this module dependency-free
    recipe: Sequence[str | None],
    atlas_for: Callable[[str], Image.Image] | None = None,
) -> tuple[list[Triangle], str, list[Image.Image]]:
    """Triangles plus one atlas per piece, positioned exactly like the exported GLB."""
    name, blade, grip, accessory, element = recipe
    blade_id = f"blade_{str(blade).lower()}"
    grip_id = f"grip_{str(grip).lower()}"
    triangles: list[Triangle] = []
    atlases: list[Image.Image] = []

    def add(part_id: str, offset: Vec3, gap: float = 0.0) -> None:
        part = state.parts[part_id]
        layout = state.layouts[part_id]
        index = len(atlases)
        atlases.append(state.finals[part_id] if atlas_for is None else atlas_for(part_id))
        for triangle in part_scene(part, layout, gap):
            triangles.append(
                replace(
                    triangle,
                    a=_shift(triangle.a, offset),
                    b=_shift(triangle.b, offset),
                    c=_shift(triangle.c, offset),
                    material=index,
                )
            )

    blade_notes = state.parts[blade_id].notes if blade_id in state.parts else {}
    accessory_gap = float(blade_notes.get("accessory_face_gap_m", 0.0))
    element_gap = float(blade_notes.get("element_face_gap_m", 0.0))

    if blade_id in state.parts:
        add(blade_id, (0.0, 0.0, 0.0))
    if grip_id in state.parts:
        add(grip_id, (0.0, 0.0, 0.0))
    if accessory:
        accessory_id = f"accessory_{accessory}"
        if accessory_id in state.parts:
            target = str(state.parts[accessory_id].meta.get("target_socket", "SOCKET_ACCESSORY"))
            host = state.parts[grip_id] if target == "SOCKET_POMMEL" else state.parts[blade_id]
            gap = 0.0 if target == "SOCKET_POMMEL" else accessory_gap
            add(accessory_id, _to_gltf(host.sockets_blender[target]), gap)
    if element:
        element_id = f"element_{str(element).lower()}"
        if element_id in state.parts:
            add(
                element_id,
                _to_gltf(state.parts[blade_id].sockets_blender["SOCKET_ELEMENT"]),
                element_gap,
            )
    return triangles, str(name), atlases


# ---------------------------------------------------------------------------
# Rasteriser
# ---------------------------------------------------------------------------
def _camera(yaw_deg: float, pitch_deg: float) -> tuple[Vec3, Vec3, Vec3]:
    """Camera basis `(right, up, forward)`; the eye sits along `-forward` in glTF space."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    eye_dir = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    forward = (-eye_dir[0], -eye_dir[1], -eye_dir[2])
    right = (forward[1] * 0.0 - forward[2] * 1.0, forward[2] * 0.0 - forward[0] * 0.0, forward[0] * 1.0 - forward[1] * 0.0)
    length = math.sqrt(sum(component * component for component in right)) or 1.0
    right = (right[0] / length, right[1] / length, right[2] / length)
    up = (
        right[1] * forward[2] - right[2] * forward[1],
        right[2] * forward[0] - right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    )
    return right, up, forward


def _shade(normal: Vec3, forward: Vec3) -> float:
    """Flat comic lighting; normals are made two-sided before shading."""
    nx, ny, nz = normal
    if nx * forward[0] + ny * forward[1] + nz * forward[2] > 0.0:
        nx, ny, nz = -nx, -ny, -nz
    value = _AMBIENT
    for (lx, ly, lz), weight in _LIGHTS:
        dot = nx * lx + ny * ly + nz * lz
        if dot > 0.0:
            value += weight * dot
    return value


def rasterise(
    triangles: Sequence[Triangle],
    atlas: Image.Image | Sequence[Image.Image],
    *,
    size: tuple[int, int] = (720, 720),
    supersample: int = 2,
    yaw_deg: float = 7.0,
    pitch_deg: float = 5.0,
    background: tuple[int, int, int] = BACKGROUND,
) -> Image.Image:
    """Orthographic, z-buffered render of one triangle soup.

    `atlas` is either a single image (one material) or a list indexed by `Triangle.material`, which
    is what an assembled combination needs: every socketed piece keeps its own atlas.
    

    No back-face culling: a z-buffer already resolves closed shells correctly, and drawing both
    sides makes the result immune to the winding order the source geometry happened to use.
    """
    width, height = size
    ss = max(1, int(supersample))
    canvas_w, canvas_h = width * ss, height * ss
    right, up, forward = _camera(yaw_deg, pitch_deg)

    # --- project & frame ---------------------------------------------------
    screen_triangles: list[
        tuple[tuple[Vec2, Vec2, Vec2], tuple[Vec2, Vec2, Vec2], tuple[float, float, float], float, int]
    ] = []
    xs: list[float] = []
    ys: list[float] = []
    for triangle in triangles:
        screen: list[Vec2] = []
        depths: list[float] = []
        for point in (triangle.a, triangle.b, triangle.c):
            screen.append(
                (
                    point[0] * right[0] + point[1] * right[1] + point[2] * right[2],
                    point[0] * up[0] + point[1] * up[1] + point[2] * up[2],
                )
            )
            depths.append(point[0] * forward[0] + point[1] * forward[1] + point[2] * forward[2])
        xs.extend(point[0] for point in screen)
        ys.extend(point[1] for point in screen)
        screen_triangles.append(
            (
                (screen[0], screen[1], screen[2]),
                (triangle.uv_a, triangle.uv_b, triangle.uv_c),
                (depths[0], depths[1], depths[2]),
                _shade(triangle.normal, forward),
                triangle.material,
            )
        )
    if not screen_triangles:
        return Image.new("RGB", size, background)

    span_x = max(max(xs) - min(xs), 1e-6)
    span_y = max(max(ys) - min(ys), 1e-6)
    scale = min(canvas_w * 0.90 / span_x, canvas_h * 0.90 / span_y)
    centre_x = (max(xs) + min(xs)) / 2.0
    centre_y = (max(ys) + min(ys)) / 2.0

    # --- rasterise ---------------------------------------------------------
    buffer = bytearray(bytes(background) * (canvas_w * canvas_h))
    depth_buffer = [1e30] * (canvas_w * canvas_h)
    atlases = (
        [atlas] if isinstance(atlas, Image.Image) else [image.convert("RGB") for image in atlas]
    )
    textures = [(image.size, image.tobytes()) for image in atlases]

    for screen, uvs, depths, light, material in screen_triangles:
        (tex_w, tex_h), tex_bytes = textures[material] if len(textures) > material else textures[0]
        (ax, ay), (bx, by), (cx, cy) = screen
        area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if abs(area) < 1e-9:
            continue
        inv_area = 1.0 / area

        placed = (
            ((ax - centre_x) * scale + canvas_w / 2.0, canvas_h / 2.0 - (ay - centre_y) * scale),
            ((bx - centre_x) * scale + canvas_w / 2.0, canvas_h / 2.0 - (by - centre_y) * scale),
            ((cx - centre_x) * scale + canvas_w / 2.0, canvas_h / 2.0 - (cy - centre_y) * scale),
        )
        (pax, pay), (pbx, pby), (pcx, pcy) = placed
        area = (pbx - pax) * (pcy - pay) - (pcx - pax) * (pby - pay)
        if abs(area) < 1e-9:
            continue
        inv_area = 1.0 / area

        ymin = max(0, int(math.floor(min(pay, pby, pcy))))
        ymax = min(canvas_h - 1, int(math.ceil(max(pay, pby, pcy))))
        if ymin > ymax:
            continue

        # Barycentric weights and their per-pixel/per-row gradients.
        w0_dx = (pby - pcy) * inv_area
        w1_dx = (pcy - pay) * inv_area
        w2_dx = -(w0_dx + w1_dx)

        (u0, v0), (u1, v1), (u2, v2) = uvs
        du_dx = u0 * w0_dx + u1 * w1_dx + u2 * w2_dx
        dv_dx = v0 * w0_dx + v1 * w1_dx + v2 * w2_dx
        dz_dx = depths[0] * w0_dx + depths[1] * w1_dx + depths[2] * w2_dx

        for py in range(ymin, ymax + 1):
            sy = py + 0.5
            crossings: list[float] = []
            for (x0, y0), (x1, y1) in (
                ((pax, pay), (pbx, pby)),
                ((pbx, pby), (pcx, pcy)),
                ((pcx, pcy), (pax, pay)),
            ):
                if (y0 <= sy < y1) or (y1 <= sy < y0):
                    crossings.append(x0 + (sy - y0) * (x1 - x0) / (y1 - y0))
            if len(crossings) < 2:
                continue
            x_start = max(0, int(math.ceil(min(crossings) - 0.5)))
            x_end = min(canvas_w - 1, int(math.floor(max(crossings) - 0.5)))
            if x_start > x_end:
                continue
            row_offset = py * canvas_w

            # Anchor the interpolation at the row's first pixel centre.
            px = x_start + 0.5
            pyc = sy
            w0 = ((pbx - px) * (pcy - pyc) - (pcx - px) * (pby - pyc)) * inv_area
            w1 = ((pcx - px) * (pay - pyc) - (pax - px) * (pcy - pyc)) * inv_area
            u = u0 * w0 + u1 * w1 + u2 * (1.0 - w0 - w1)
            v = v0 * w0 + v1 * w1 + v2 * (1.0 - w0 - w1)
            z = depths[0] * w0 + depths[1] * w1 + depths[2] * (1.0 - w0 - w1)

            for px_index in range(x_start, x_end + 1):
                cell = row_offset + px_index
                if z < depth_buffer[cell]:
                    depth_buffer[cell] = z
                    tx = int(u * tex_w)
                    ty = int(v * tex_h)
                    if 0 <= tx < tex_w and 0 <= ty < tex_h:
                        sample = (ty * tex_w + tx) * 3
                        target = cell * 3
                        red = tex_bytes[sample] * light
                        green = tex_bytes[sample + 1] * light
                        blue = tex_bytes[sample + 2] * light
                        buffer[target] = 255 if red > 255 else int(red)
                        buffer[target + 1] = 255 if green > 255 else int(green)
                        buffer[target + 2] = 255 if blue > 255 else int(blue)
                u += du_dx
                v += dv_dx
                z += dz_dx

    image = Image.frombytes("RGB", (canvas_w, canvas_h), bytes(buffer))
    if ss > 1:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------
def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:  # noqa: ANN001
    try:
        return int(draw.textlength(text, font=font))
    except AttributeError:  # pragma: no cover - very old Pillow
        return int(font.getlength(text)) if hasattr(font, "getlength") else 8 * len(text)


def contact_sheet(
    tiles: Sequence[tuple[str, Image.Image]],
    title: str,
    *,
    columns: int = 5,
    tile: int = 240,
    gutter: int = 18,
    label_height: int = 34,
) -> Image.Image:
    """Compose labelled tiles on paper, with a title bar — an overview sheet."""
    margin = 28
    rows = max(1, math.ceil(len(tiles) / columns))
    cell_w = tile
    cell_h = tile + label_height
    width = margin * 2 + columns * cell_w + (columns - 1) * gutter
    header = 92
    height = header + margin + rows * cell_h + (rows - 1) * gutter + margin
    sheet = Image.new("RGB", (width, height), SHEET_PAPER)
    draw = ImageDraw.Draw(sheet)
    draw.rectangle([0, 0, width, header - 16], fill=TITLE_BAR)
    title_font = _font(34)
    draw.text((margin, 22), title.upper(), fill=(28, 24, 16), font=title_font)
    sub_font = _font(16)
    draw.text((margin + 2, 62), f"{len(tiles)} tiles", fill=(70, 56, 28), font=sub_font)

    label_font = _font(15)
    for index, (label, image) in enumerate(tiles):
        column = index % columns
        row = index // columns
        x = margin + column * (cell_w + gutter)
        y = header + row * (cell_h + gutter)
        draw.rectangle([x, y, x + cell_w, y + tile], fill=BACKGROUND)
        icon = image.resize((tile, tile), Image.Resampling.LANCZOS) if image.size != (tile, tile) else image
        sheet.paste(icon, (x, y))
        draw.rectangle([x, y + tile, x + cell_w, y + cell_h], fill=LABEL_BG)
        text = label
        while _text_width(draw, text, label_font) > cell_w - 16 and len(text) > 4:
            text = text[:-2]
        draw.text((x + 8, y + tile + 8), text, fill=LABEL_FG, font=label_font)
        draw.rectangle([x, y, x + cell_w, y + cell_h], outline=(20, 20, 22), width=1)
    return sheet


# ---------------------------------------------------------------------------
# Kit render entry point
# ---------------------------------------------------------------------------
def render_kit(
    state,  # noqa: ANN001 - KitState, passed as a duck type to keep this module dependency-free
    out_dir: Path,
    *,
    size: tuple[int, int] = (720, 720),
    supersample: int = 2,
    yaw_deg: float = 7.0,
    pitch_deg: float = 5.0,
) -> dict[str, int]:
    """Render every part and combination plus the two overview sheets."""
    out_dir = Path(out_dir)
    parts_dir = out_dir / "parts"
    combos_dir = out_dir / "combinations"
    parts_dir.mkdir(parents=True, exist_ok=True)
    combos_dir.mkdir(parents=True, exist_ok=True)

    part_tiles: list[tuple[str, Image.Image]] = []
    for part_id in [pid for pid in PART_ORDER if pid in state.parts]:
        image = rasterise(
            part_scene(state.parts[part_id], state.layouts[part_id]),
            state.finals[part_id],
            size=size,
            supersample=supersample,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
        )
        image.save(parts_dir / f"{part_id}.png")
        part_tiles.append((part_id.replace("_", " ").title(), image))

    combo_tiles: list[tuple[str, Image.Image]] = []
    from .stages import _atlas_for_element  # local import: stages imports this module

    for recipe in COMBINATIONS:
        triangles, name, atlases = assembly_scene(
            state, recipe, atlas_for=lambda part_id, element=recipe[4]: _atlas_for_element(
                state, part_id, element
            )
        )
        if not triangles:
            continue
        image = rasterise(
            triangles,
            atlases,
            size=size,
            supersample=supersample,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
        )
        image.save(combos_dir / f"{name}.png")
        combo_tiles.append((name.replace("_", " "), image))

    if part_tiles:
        contact_sheet(part_tiles, "Inkblade / modular parts", columns=5).save(
            out_dir / "overview_parts.png"
        )
    if combo_tiles:
        contact_sheet(combo_tiles, "Inkblade / example assemblies", columns=4).save(
            out_dir / "overview_combinations.png"
        )

    (out_dir / "README.md").write_text(
        "# Inkblade previews\n\n"
        "Rendered offline by `tools/inkblade_gen/render.py` (pure Python + Pillow software "
        "rasteriser — no Blender required).\n\n"
        "- `parts/`: one 720x720 tile per part.\n"
        "- `combinations/`: the example assemblies.\n"
        "- `overview_parts.png`, `overview_combinations.png`: labelled contact sheets.\n"
    )
    return {"parts": len(part_tiles), "combinations": len(combo_tiles), "sheets": int(bool(part_tiles)) + int(bool(combo_tiles))}
