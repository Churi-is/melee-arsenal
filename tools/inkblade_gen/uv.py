"""UV stage: shared-density face projection, padded island packing, and template reuse.

This is the R3 `physical_uv.py` projection/packing code (orthonormal per-face projection at one
global pixels-per-metre density, variable-size padded packing, no island normalisation) with the
R4 UV rules folded in:

* rear/mirrored detail faces copy their template island's UVs verbatim (`uv_flip` reverses them,
  exactly like `r4_builder`'s `list(reversed(uv[j]))`),
* the new hexagonal Torgue faces reuse a painted template island scaled to their bounding box
  (`uv_mode="fit"`), preserving the approved look without a second unwrap pass.

Pure Python + Pillow: no Blender, `mathutils` or numpy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Sequence

from .config import RunConfig
from .geometry import Face, Part

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


# ---------------------------------------------------------------------------
# Small vector helpers (replacing `mathutils.Vector`)
# ---------------------------------------------------------------------------
def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _length(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Vec3) -> Vec3:
    length = _length(a)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def newell_normal(verts: Sequence[Vec3]) -> Vec3:
    """Area-weighted polygon normal."""
    nx = ny = nz = 0.0
    for i, current in enumerate(verts):
        nxt = verts[(i + 1) % len(verts)]
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return _unit((nx, ny, nz))


# ---------------------------------------------------------------------------
# Projection & packing
# ---------------------------------------------------------------------------
def pow2(value: float) -> int:
    return 2 ** math.ceil(math.log2(max(32.0, value)))


@dataclass
class Projection:
    """One face projected into texture pixels (origin at the island's top-left, padding included)."""

    points: list[Vec2]      # pixel positions relative to the island rect origin
    width: int
    height: int


def project_face(verts: Sequence[Vec3], density: float, padding: int) -> Projection:
    """Orthonormal projection of a planar polygon at the shared physical texel density."""
    vs = [tuple(map(float, v)) for v in verts]
    normal = newell_normal(vs)
    if _length(normal) < 1e-10:
        normal = _unit(_cross(_sub(vs[1], vs[0]), _sub(vs[2], vs[0])))
    edges = [_sub(b, a) for a, b in zip(vs, vs[1:] + vs[:1], strict=False)]
    u = _unit(max(edges, key=_length))
    v = _unit(_cross(normal, u))
    pts = [
        (_dot(_sub(p, vs[0]), u) * density, _dot(_sub(p, vs[0]), v) * density)
        for p in vs
    ]
    min_u = min(p[0] for p in pts)
    min_v = min(p[1] for p in pts)
    pts = [(x - min_u, y - min_v) for x, y in pts]
    w = max(x for x, _ in pts)
    h = max(y for _, y in pts)
    if h > w:
        pts = [(y, x) for x, y in pts]
        w, h = h, w
    return Projection(
        points=[(x + padding, y + padding) for x, y in pts],
        width=math.ceil(w) + padding * 2 + 1,
        height=math.ceil(h) + padding * 2 + 1,
    )


@dataclass
class Island:
    """A packed face: its atlas rectangle, pixel polygon and the UV rectangle it occupies."""

    index: int
    face: int
    role: str
    rect: tuple[int, int, int, int]      # x, y, w, h in atlas pixels
    polygon: list[Vec2]                   # closed texture-space polygon (pixels)
    owner: bool = True                    # False when the face reuses a template's texels
    template: int | None = None

    def uv_points(self, atlas_size: tuple[int, int]) -> list[Vec2]:
        """Island polygon in glTF texture space (`v` grows downwards from the image's top row)."""
        width, height = atlas_size
        return [(x / width, y / height) for x, y in self.polygon]

    def to_json(self) -> dict:
        return {
            "face": self.face,
            "role": self.role,
            "rect_px": list(self.rect),
            "owner": self.owner,
            "template": self.template,
        }


@dataclass
class PartLayout:
    """Packed atlas + per-face UVs for one part, ready to serialise as `<id>_uv_layout.json`."""

    part_id: str
    atlas_size: tuple[int, int]
    islands: list[Island]
    face_uvs: list[list[Vec2]]
    density: float
    padding: int
    ink_px: int
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        width, height = self.atlas_size
        return {
            "schema": "inkblade.physical-uv/2.0",
            "part_id": self.part_id,
            "pixels_per_meter": self.density,
            "outline_stroke_px": self.ink_px,
            "outline_full_stroke_m": round(self.ink_px / self.density, 6),
            "padding_px": self.padding,
            "atlas_px": [width, height],
            "uv_layer": "UV_PhysicalScale",
            "islands": [isl.to_json() for isl in self.islands],
            "uvs": [[[round(u, 7), round(v, 7)] for u, v in face] for face in self.face_uvs],
            **self.extra,
        }


def _fit_uv_from_template(verts: Sequence[Vec3], template_uv: Sequence[Vec2]) -> list[Vec2]:
    """Scale a template's UV rectangle onto a differently-shaped face (R4's hex-face reuse)."""
    u0 = min(u for u, _ in template_uv)
    u1 = max(u for u, _ in template_uv)
    v0 = min(v for _, v in template_uv)
    v1 = max(v for _, v in template_uv)
    if len(verts) == 4:
        # Keep the long texture direction aligned with the long physical edge.
        a = _sub(verts[1], verts[0])
        b = _sub(verts[2], verts[1])
        corners = [(u0, v0), (u0, v1), (u1, v1), (u1, v0)]
        if _dot(b, b) > _dot(a, a):
            corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        return corners
    normal = newell_normal(verts)
    axis = max(range(3), key=lambda i: abs(normal[i]))
    axes = [i for i in range(3) if i != axis]
    lo = [min(p[a] for p in verts) for a in axes]
    hi = [max(p[a] for p in verts) for a in axes]
    return [
        (
            u0 + (p[axes[0]] - lo[0]) / max(hi[0] - lo[0], 1e-9) * (u1 - u0),
            v0 + (p[axes[1]] - lo[1]) / max(hi[1] - lo[1], 1e-9) * (v1 - v0),
        )
        for p in verts
    ]


def unwrap_part(part: Part, cfg: RunConfig) -> PartLayout:
    """Project, pack and resolve template reuse for every face of one part."""
    faces: list[Face] = part.faces
    projections = [project_face(face.verts, cfg.density, cfg.padding) for face in faces]

    owner_ids = [i for i, face in enumerate(faces) if face.uv_template is None]
    total_area = sum(projections[i].width * projections[i].height for i in owner_ids)
    atlas_width = pow2(
        max(
            max((projections[i].width for i in owner_ids), default=64),
            math.sqrt(max(1, total_area)) * 1.1,
        )
    )

    placements: dict[int, tuple[int, int]] = {}
    x = y = row_height = 0
    for idx in sorted(owner_ids, key=lambda i: projections[i].height, reverse=True):
        proj = projections[idx]
        if x + proj.width > atlas_width:
            x = 0
            y += row_height
            row_height = 0
        placements[idx] = (x, y)
        x += proj.width
        row_height = max(row_height, proj.height)
    atlas_height = pow2(y + row_height)

    islands: list[Island] = []
    for idx, face in enumerate(faces):
        if face.uv_template is None:
            ox, oy = placements[idx]
            proj = projections[idx]
            polygon = [(ox + px, oy + py) for px, py in proj.points]
            islands.append(
                Island(
                    index=idx,
                    face=idx,
                    role=face.tag,
                    rect=(ox, oy, proj.width, proj.height),
                    polygon=polygon,
                    owner=True,
                )
            )
        else:
            template_island = islands[face.uv_template]
            islands.append(
                Island(
                    index=idx,
                    face=idx,
                    role=face.tag,
                    rect=template_island.rect,
                    polygon=list(template_island.polygon),
                    owner=False,
                    template=face.uv_template,
                )
            )

    atlas_size = (atlas_width, atlas_height)
    face_uvs: list[list[Vec2]] = []
    for idx, face in enumerate(faces):
        if face.uv_template is None:
            face_uvs.append(islands[idx].uv_points(atlas_size))
        elif face.uv_mode == "fit":
            template_uv = islands[face.uv_template].uv_points(atlas_size)
            face_uvs.append(_fit_uv_from_template(face.verts, template_uv))
        else:
            template_uv = islands[face.uv_template].uv_points(atlas_size)
            face_uvs.append(list(reversed(template_uv)) if face.uv_flip else list(template_uv))

    return PartLayout(
        part_id=part.part_id,
        atlas_size=atlas_size,
        islands=islands,
        face_uvs=face_uvs,
        density=cfg.density,
        padding=cfg.padding,
        ink_px=cfg.ink_px,
        extra={"bifacial_fit": part.bifacial},
    )


def unwrap_parts(parts: dict[str, Part], cfg: RunConfig) -> dict[str, PartLayout]:
    return {part_id: unwrap_part(part, cfg) for part_id, part in parts.items() if cfg.part_enabled(part_id)}


def owned_islands(layout: PartLayout) -> Iterable[Island]:
    return (isl for isl in layout.islands if isl.owner)
