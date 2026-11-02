"""The stage stack: small, independently runnable and cacheable generation steps.

Every stage declares what it does, how to persist its output to the work directory and how to reload
it. That is what makes the pipeline modular:

* adding an art pass (decals, stencils, grime, ...) = add a `Stage` entry between `linework` and
  `textures`; nothing else changes,
* editing one stage only invalidates that stage and the ones downstream of it,
* each stage leaves inspectable artifacts on disk (`--work`).

Ordering note: an atlas cannot be *painted* before the island layout exists, so the texture stages
follow the UV stage. What *is* decoupled — and what the flat/mottle/linework split buys — is that
every painting pass is independent, re-runnable and stackable on the same UV layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import io
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image

from . import textures
from .config import ELEMENTS, RunConfig
from .geometry import (
    ACCESSORIES,
    ACCESSORY_FACE_CLIP_M,
    BLADE_CORE_DEPTH_MULTIPLIER,
    BLADES,
    COMBINATIONS,
    ELEMENT_RECEIVER_X,
    ELEMENT_RECEIVER_X_SHIFT,
    HEAVY_SPINE_DEPTH_DIVISOR,
    PART_ORDER,
    Face,
    Part,
    build_parts,
)
from .glb import build_part_glb, merge_glbs, read_glb, _to_gltf
from .uv import Island, PartLayout, unwrap_parts

MANIFEST_SCHEMA = "inkblade.socket-kit/1.0"
TEXTURE_NOTES = (
    "Staged atlas: flat palette, mottled wear, bold ink contours, polygonal shadow planes, "
    "hatch clusters and scratches baked into one RGB PNG."
)


# ---------------------------------------------------------------------------
# State container
# ---------------------------------------------------------------------------
@dataclass
class KitState:
    """Everything the stack produces; stages only touch the fields they own."""

    cfg: RunConfig
    parts: dict[str, Part] = field(default_factory=dict)
    layouts: dict[str, PartLayout] = field(default_factory=dict)
    canvases: dict[str, Image.Image] = field(default_factory=dict)
    finals: dict[str, Image.Image] = field(default_factory=dict)
    colours: dict[str, dict[int, tuple[int, int, int]]] = field(default_factory=dict)
    decals_applied: dict[str, int] = field(default_factory=dict)
    element_atlases: dict[tuple[str, str], Image.Image] = field(default_factory=dict)
    reports: list["StageReport"] = field(default_factory=list)
    renders: dict[str, int] = field(default_factory=dict)
    kit_dir: Path | None = None
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled_parts(self) -> list[str]:
        return [part_id for part_id in PART_ORDER if part_id in self.parts]


@dataclass
class StageReport:
    name: str
    title: str
    status: str            # "ran" | "cached"
    fingerprint: str
    seconds: float
    artifacts: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "title": self.title,
            "status": self.status,
            "fingerprint": self.fingerprint,
            "seconds": round(self.seconds, 3),
            "artifacts": self.artifacts,
        }


# ---------------------------------------------------------------------------
# Part / layout (de)serialisation so cached stages can be restored without re-deriving geometry
# ---------------------------------------------------------------------------
def part_to_json(part: Part) -> dict[str, Any]:
    return {
        "part_id": part.part_id,
        "slot": part.slot,
        "manufacturer": part.manufacturer,
        "sockets_blender": {name: list(xyz) for name, xyz in part.sockets_blender.items()},
        "meta": part.meta,
        "bifacial": part.bifacial,
        "fit_inner_shift_m": part.fit_inner_shift_m,
        "notes": part.notes,
        "faces": [
            {
                "verts": [list(v) for v in face.verts],
                "tag": face.tag,
                "uv_template": face.uv_template,
                "uv_flip": face.uv_flip,
                "uv_mode": face.uv_mode,
            }
            for face in part.faces
        ],
    }


def part_from_json(payload: dict[str, Any]) -> Part:
    faces = [
        Face(
            verts=tuple(tuple(float(c) for c in v) for v in item["verts"]),
            tag=str(item["tag"]),
            uv_template=item.get("uv_template"),
            uv_flip=bool(item.get("uv_flip", False)),
            uv_mode=str(item.get("uv_mode", "exact")),
        )
        for item in payload["faces"]
    ]
    return Part(
        part_id=str(payload["part_id"]),
        slot=str(payload["slot"]),
        manufacturer=str(payload["manufacturer"]),
        faces=faces,
        sockets_blender={
            name: tuple(float(c) for c in xyz)  # type: ignore[misc]
            for name, xyz in payload["sockets_blender"].items()
        },
        meta=dict(payload.get("meta", {})),
        bifacial=bool(payload.get("bifacial", False)),
        fit_inner_shift_m=float(payload.get("fit_inner_shift_m", 0.0)),
        notes=dict(payload.get("notes", {})),
    )


def layout_to_json(layout: PartLayout) -> dict[str, Any]:
    payload = layout.to_json()
    payload["islands"] = [
        {**island.to_json(), "polygon": [[round(x, 3), round(y, 3)] for x, y in island.polygon]}
        for island in layout.islands
    ]
    return payload


def layout_from_json(payload: dict[str, Any], part_id: str = "") -> PartLayout:
    islands = [
        Island(
            index=index,
            face=int(item["face"]),
            role=str(item["role"]),
            rect=tuple(int(v) for v in item["rect_px"]),  # type: ignore[arg-type]
            polygon=[(float(x), float(y)) for x, y in item.get("polygon", [])],
            owner=bool(item.get("owner", True)),
            template=item.get("template"),
        )
        for index, item in enumerate(payload["islands"])
    ]
    face_uvs = [[(float(u), float(v)) for u, v in face] for face in payload.get("uvs", [])]
    width, height = (int(v) for v in payload["atlas_px"])
    return PartLayout(
        part_id=str(payload.get("part_id") or part_id),
        atlas_size=(width, height),
        islands=islands,
        face_uvs=face_uvs,
        density=float(payload.get("pixels_per_meter", 0.0)),
        padding=int(payload.get("padding_px", 0)),
        ink_px=int(payload.get("outline_stroke_px", 0)),
    )


# ---------------------------------------------------------------------------
# Stage definition
# ---------------------------------------------------------------------------
@dataclass
class Stage:
    name: str
    title: str
    run: Callable[[KitState], None]
    save: Callable[[KitState, Path], int] | None = None
    load: Callable[[KitState, Path], int] | None = None
    #: Extra fingerprint material, e.g. the decal directory contents.
    extra_key: Callable[[KitState], Any] | None = None
    cacheable: bool = True

    def fingerprint(self, cfg: RunConfig, upstream: str, extra: Any = None) -> str:
        digest = hashlib.sha256()
        digest.update(json.dumps(cfg.to_json(), sort_keys=True).encode())
        digest.update(upstream.encode())
        try:
            digest.update(inspect.getsource(self.run).encode())
        except (OSError, TypeError):  # pragma: no cover - source is always available in-repo
            pass
        if extra is not None:
            digest.update(json.dumps(extra, sort_keys=True, default=str).encode())
        return digest.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Stage: geometry
# ---------------------------------------------------------------------------
def _run_geometry(state: KitState) -> None:
    state.parts = build_parts(state.cfg.parts)


def _save_geometry(state: KitState, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    for part_id, part in state.parts.items():
        (folder / f"{part_id}.json").write_text(json.dumps(part_to_json(part), indent=1))
    return len(state.parts)


def _load_geometry(state: KitState, folder: Path) -> int:
    state.parts = {}
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        state.parts[path.stem] = part_from_json(json.loads(path.read_text()))
    return len(state.parts)


# ---------------------------------------------------------------------------
# Stage: uv
# ---------------------------------------------------------------------------
def _run_uv(state: KitState) -> None:
    state.layouts = unwrap_parts(state.parts, state.cfg)


def _save_uv(state: KitState, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    for part_id, layout in state.layouts.items():
        (folder / f"{part_id}.json").write_text(json.dumps(layout_to_json(layout), indent=1))
    return len(state.layouts)


def _load_uv(state: KitState, folder: Path) -> int:
    state.layouts = {}
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        state.layouts[path.stem] = layout_from_json(json.loads(path.read_text()), path.stem)
    return len(state.layouts)


# ---------------------------------------------------------------------------
# Stage: texture canvases (flat / mottle / linework / decals all share one persistence format)
# ---------------------------------------------------------------------------
def _ensure_canvases(state: KitState) -> None:
    for part_id, layout in state.layouts.items():
        state.canvases.setdefault(part_id, textures.new_canvas(layout))


def _run_flat(state: KitState) -> None:
    _ensure_canvases(state)
    for part_id, layout in state.layouts.items():
        state.canvases[part_id] = textures.paint_flat_colours(
            state.parts[part_id], layout, state.canvases[part_id], state.cfg.seed
        )
    state.colours = {
        part_id: textures.role_colours(state.parts[part_id], layout, state.cfg.seed)
        for part_id, layout in state.layouts.items()
    }


def _run_mottle(state: KitState) -> None:
    _ensure_canvases(state)
    for part_id, layout in state.layouts.items():
        state.canvases[part_id] = textures.paint_mottle(
            state.parts[part_id], layout, state.canvases[part_id]
        )


def _run_linework(state: KitState) -> None:
    _ensure_canvases(state)
    for part_id, layout in state.layouts.items():
        state.canvases[part_id] = textures.paint_linework(
            state.parts[part_id],
            layout,
            state.canvases[part_id],
            ink_px=state.cfg.ink_px,
            seed_salt=state.cfg.linework_seed,
        )


def _run_decals(state: KitState) -> None:
    _ensure_canvases(state)
    state.decals_applied = {}
    for part_id, layout in state.layouts.items():
        if state.cfg.decal_dir is None:
            state.decals_applied[part_id] = 0
            continue
        state.decals_applied[part_id] = textures.paint_decals(
            state.parts[part_id],
            layout,
            state.canvases[part_id],
            state.cfg.decal_dir,
            state.cfg.decal_fit,
        )


def _decals_key(state: KitState) -> Any:
    folder = state.cfg.decal_dir
    if folder is None:
        return None
    folder = Path(folder)
    if not folder.is_dir():
        return None
    return sorted(
        (str(path.relative_to(folder)), path.stat().st_size, int(path.stat().st_mtime))
        for path in folder.rglob("*.png")
    )


def _save_canvases(state: KitState, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    for part_id, canvas in state.canvases.items():
        canvas.save(folder / f"{part_id}.png", optimize=True)
    (folder / "_colours.json").write_text(
        json.dumps({pid: {str(k): list(v) for k, v in cols.items()} for pid, cols in state.colours.items()})
    )
    return len(state.canvases)


def _load_canvases(state: KitState, folder: Path) -> int:
    state.canvases = {}
    for path in sorted(folder.glob("*.png")):
        state.canvases[path.stem] = Image.open(path).convert("RGB")
    colour_file = folder / "_colours.json"
    if colour_file.is_file():
        state.colours = {
            pid: {int(k): tuple(v) for k, v in cols.items()}
            for pid, cols in json.loads(colour_file.read_text()).items()
        }
    return len(state.canvases)


# ---------------------------------------------------------------------------
# Stage: textures (seal padding, quantise, write the final PNGs)
# ---------------------------------------------------------------------------
def _restore_colours(state: KitState) -> None:
    if state.colours:
        return
    state.colours = {
        part_id: textures.role_colours(state.parts[part_id], layout, state.cfg.seed)
        for part_id, layout in state.layouts.items()
    }


def _run_textures(state: KitState) -> None:
    _ensure_canvases(state)
    _restore_colours(state)
    state.finals = {
        part_id: textures.finalise_atlas(
            state.canvases[part_id],
            layout,
            state.colours.get(part_id, {}),
            state.cfg.quantize_colors,
        )
        for part_id, layout in state.layouts.items()
    }
    state.element_atlases.clear()


def _save_finals(state: KitState, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    for part_id, image in state.finals.items():
        image.save(folder / f"{part_id}_basecolor.png", optimize=True)
    return len(state.finals)


def _load_finals(state: KitState, folder: Path) -> int:
    state.finals = {
        path.stem.removesuffix("_basecolor"): Image.open(path).copy()
        for path in sorted(folder.glob("*_basecolor.png"))
    }
    return len(state.finals)


# ---------------------------------------------------------------------------
# Stage: export (GLBs + UV layouts + socket manifest)
# ---------------------------------------------------------------------------
def _sockets_gltf(part: Part) -> dict[str, list[float]]:
    return {name: [round(c, 7) for c in _to_gltf(position)] for name, position in part.sockets_blender.items()}


def build_manifest(state: KitState) -> dict[str, Any]:
    """Assemble the `socket_manifest.json` the UE3 compiler and runtime consume."""
    cfg = state.cfg
    parts: dict[str, Any] = {}
    for part_id in state.enabled_parts:
        part = state.parts[part_id]
        layout = state.layouts[part_id]
        entry: dict[str, Any] = {
            "file": f"models/parts/{part_id}.glb",
            "slot": part.slot,
            "manufacturer": part.manufacturer,
            "texture": f"textures/{part_id}_basecolor.png",
            "uv_layout": f"textures/{part_id}_uv_layout.json",
            "pixels_per_meter": layout.density,
            "vertices": part.vertex_count,
            "triangles": part.triangles,
            "sockets_blender_m": {n: list(v) for n, v in part.sockets_blender.items()},
            "sockets_gltf_m": _sockets_gltf(part),
            "materials": ["SURFACE"] + (["ELEMENT_ACCENT"] if any(f.tag == "accent" for f in part.faces) else []),
            "bifacial_fit": part.bifacial,
            "atlas_px": list(layout.atlas_size),
            "texture_notes": TEXTURE_NOTES,
        }
        if part.bifacial:
            entry["fit_inner_shift_m"] = round(part.fit_inner_shift_m, 7)
        entry.update(part.meta)
        entry.update(part.notes)
        parts[part_id] = entry

    return {
        "schema": MANIFEST_SCHEMA,
        "revision": cfg.revision,
        "units": "meters",
        "coordinates": {
            "working": "right-handed; +Z blade tip, -Y display/front face, +X guard right",
            "gltf": "standard glTF right-handed Y-up",
            "socket_rule": "world_child = world_socket @ inverse(child_mount) ; exported child mounts are identity",
        },
        "generator": {
            "pipeline": "tools/inkblade_gen",
            "density_px_per_m": cfg.density,
            "seed": cfg.seed,
            "stages": [report.to_json() for report in state.reports],
        },
        "revision_notes": {
            "blade_core_depth_multiplier": BLADE_CORE_DEPTH_MULTIPLIER,
            "accessory_clip_m": ACCESSORY_FACE_CLIP_M,
            "element_center_x_m": round(ELEMENT_RECEIVER_X + ELEMENT_RECEIVER_X_SHIFT, 6),
            "heavy_spine_depth_multiplier": 1.0 / HEAVY_SPINE_DEPTH_DIVISOR,
            "torgue_grip": "hexagonal shell with four continuous six-sided ribs",
            "textures": TEXTURE_NOTES,
            "stages": [report.name for report in state.reports],
        },
        "parts": parts,
        "elements": dict(ELEMENTS),
        "combinations": [],
        "rarity": {
            "accessory_probability": {"Common": 0.20, "Uncommon": 0.55, "Rare": 0.85, "Very Rare": 1.0},
            "material_slot": "MaterialPartDefinition",
            "rarity_tints": {
                "Common": "#bbc2c7",
                "Uncommon": "#63b65c",
                "Rare": "#4f8bcc",
                "Very Rare": "#9d64c5",
            },
        },
        "locked_slots": {
            "BodyPartDefinition": "LOGICAL_HOST_MATCHES_BLADE_MANUFACTURER_NO_EXTRA_MESH",
            "SightPartDefinition": None,
            "StockPartDefinition": None,
        },
        "matching_bonus": {
            maker: {
                "base_damage": damage,
                "swing_speed": speed,
                "signature_title": BLADES[maker]["signature_title"],
            }
            for maker, damage, speed in (
                ("Hyperion", 0.10, 0.12),
                ("Bandit", 0.14, 0.08),
                ("Jakobs", 0.12, 0.10),
                ("Maliwan", 0.10, 0.12),
                ("Torgue", 0.16, 0.08),
            )
        },
        "naming": {
            "unmatched_title": {maker: BLADES[maker]["archetype"] for maker in BLADES},
            "prefixes": {
                maker: dict(zip(ACCESSORIES, words))
                for maker, words in {
                    "Hyperion": ["Indexed", "Gyroscopic", "Telescopic", "Calibrated", "Panoramic", "Armored", "Overdriven"],
                    "Bandit": ["Bitey", "Quickn", "Longboi", "Eye Poker", "Wide Swing", "Bigboned", "Zappy"],
                    "Jakobs": ["Sawtooth", "Quickdraw", "Longreach", "Deadeye", "Sweeping", "Ironbound", "Stormbound"],
                    "Maliwan": ["Lacerating", "Harmonic", "Elongated", "Focused", "Radiant", "Fortified", "Catalytic"],
                    "Torgue": ["RIPPER", "FAST HANDS", "LONG SHOT", "CRIT MACHINE", "WIDE LOAD", "HEAVY METAL", "HOT WIRED"],
                }.items()
            },
        },
    }


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _atlas_for_element(state: KitState, part_id: str, element: str | None) -> Image.Image:
    """Atlas for a combination: accent islands baked to the element tint (R3 behaviour)."""
    atlas = state.finals[part_id]
    if not element:
        return atlas
    layout = state.layouts[part_id]
    if not any(island.role == "accent" and island.owner for island in layout.islands):
        return atlas
    key = (part_id, element)
    if key not in state.element_atlases:
        state.element_atlases[key] = textures.recolor_accent(
            atlas.convert("RGB"), layout, ELEMENTS[element]
        )
    return state.element_atlases[key]


def _run_export(state: KitState) -> None:
    cfg = state.cfg
    kit_dir = Path(cfg.out_dir)
    models = kit_dir / "models"
    (models / "parts").mkdir(parents=True, exist_ok=True)
    (models / "combinations").mkdir(parents=True, exist_ok=True)
    (kit_dir / "textures").mkdir(parents=True, exist_ok=True)
    (kit_dir / "docs").mkdir(parents=True, exist_ok=True)

    available: set[str] = set()
    for part_id in state.enabled_parts:
        part = state.parts[part_id]
        layout = state.layouts[part_id]
        png = _png_bytes(state.finals[part_id])
        (kit_dir / "textures" / f"{part_id}_basecolor.png").write_bytes(png)
        (kit_dir / "textures" / f"{part_id}_uv_layout.json").write_text(
            json.dumps(layout_to_json(layout), indent=1)
        )
        (models / "parts" / f"{part_id}.glb").write_bytes(
            build_part_glb(part, layout, png, revision=cfg.revision)
        )
        available.add(part_id)

    manifest = build_manifest(state)

    if cfg.bake_combinations:
        for name, blade, grip, accessory, element in COMBINATIONS:
            blade_id = f"blade_{blade.lower()}"
            grip_id = f"grip_{grip.lower()}"
            if blade_id not in available or grip_id not in available:
                continue
            pieces: list[tuple[dict[str, Any], bytes, Sequence[float], str]] = []

            def add(part_id: str, position: Sequence[float]) -> None:
                part = state.parts[part_id]
                layout = state.layouts[part_id]
                png = _png_bytes(_atlas_for_element(state, part_id, element))
                doc, binary = read_glb(build_part_glb(part, layout, png, revision=cfg.revision))
                pieces.append((doc, bytes(binary), tuple(float(c) for c in position), part_id))

            add(blade_id, (0.0, 0.0, 0.0))
            add(grip_id, (0.0, 0.0, 0.0))
            if accessory:
                accessory_id = f"accessory_{accessory}"
                if accessory_id in available:
                    target = str(state.parts[accessory_id].meta.get("target_socket", "SOCKET_ACCESSORY"))
                    host = state.parts[grip_id] if target == "SOCKET_POMMEL" else state.parts[blade_id]
                    add(accessory_id, _to_gltf(host.sockets_blender[target]))
            if element:
                element_id = f"element_{element.lower()}"
                if element_id in available:
                    add(element_id, _to_gltf(state.parts[blade_id].sockets_blender["SOCKET_ELEMENT"]))

            (models / "combinations" / f"{name}.glb").write_bytes(merge_glbs(pieces))
            manifest["combinations"].append(
                {
                    "id": name,
                    "blade": blade,
                    "grip": grip,
                    "accessory": accessory,
                    "element": element,
                    "matching": blade == grip,
                    "file": f"models/combinations/{name}.glb",
                }
            )

    (kit_dir / "socket_manifest.json").write_text(json.dumps(manifest, indent=2))
    if cfg.render_enabled:
        (kit_dir / "docs" / "renders.json").write_text(
            json.dumps({"directory": str(cfg.render_dir), **state.renders}, indent=2)
        )
    (kit_dir / "docs" / "stages.json").write_text(
        json.dumps(
            {
                "revision": cfg.revision,
                "config": cfg.to_json(),
                "stages": [report.to_json() for report in state.reports],
            },
            indent=2,
        )
    )
    (kit_dir / "README.md").write_text(_kit_readme(state))
    state.manifest = manifest
    state.kit_dir = kit_dir


def _kit_readme(state: KitState) -> str:
    cfg = state.cfg
    lines = [
        f"# Inkblade modular kit — `{cfg.revision}`",
        "",
        "Generated by `tools/inkblade_gen` (pure Python + Pillow; no Blender required).",
        "",
        "## Contents",
        "",
        f"- `models/parts/`: {len(state.enabled_parts)} standalone GLB parts.",
        "- `models/combinations/`: assembled example weapons.",
        "- `textures/`: base-colour atlases and their UV layout JSON.",
        "- `socket_manifest.json`: sockets, fit adjustments, policies, recipes and pipeline metadata.",
        "- `docs/stages.json`: which stage produced what, and the config it ran with.",
        "",
        "## Stage stack",
        "",
    ]
    for report in state.reports:
        lines.append(f"1. **{report.name}** — {report.title} ({report.status}, {report.seconds:.2f}s)")
    lines += [
        "",
        "## Notes",
        "",
        "- Blade-slot depth is halved and front-only detail is mirrored onto the rear face.",
        "- Bifacial accessories/elements export `FACE_FRONT` / `FACE_BACK` instances; move them by the",
        "  blade's `accessory_face_gap_m` / `element_face_gap_m` when assembling.",
        "- The Torgue grip is a hexagonal shell with four continuous wraparound ribs.",
        "- Textures are painted as a stack of independent layers (flat colours -> mottle/wear -> bold",
        "  ink -> optional decals), so new art passes can be inserted without touching older ones.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage: preview renders
# ---------------------------------------------------------------------------
def _run_render(state: KitState) -> None:
    """Render part/combination previews with the built-in rasteriser (no Blender needed)."""
    from .render import render_kit

    cfg = state.cfg
    if not cfg.render_enabled:
        return
    counts = render_kit(
        state,
        Path(cfg.render_dir),
        size=(int(cfg.render_size), int(cfg.render_size)),
        supersample=int(cfg.render_supersample),
    )
    state.renders = counts


# ---------------------------------------------------------------------------
# Stage catalogue
# ---------------------------------------------------------------------------
def default_stages() -> list[Stage]:
    """The ordered stack. Insert new art passes anywhere between `linework` and `textures`."""
    return [
        Stage("geometry", "Build final part geometry", _run_geometry, _save_geometry, _load_geometry),
        Stage("uv", "Project faces and pack padded UV islands", _run_uv, _save_uv, _load_uv),
        Stage(
            "flat",
            "Fill each island with its flat palette colour",
            _run_flat,
            _save_canvases,
            _load_canvases,
        ),
        Stage("mottle", "Paint wash, deposits, wear and chips", _run_mottle, _save_canvases, _load_canvases),
        Stage(
            "linework",
            "Draw bold contours, shading and hatching",
            _run_linework,
            _save_canvases,
            _load_canvases,
        ),
        Stage(
            "decals",
            "Stamp optional external decal art",
            _run_decals,
            _save_canvases,
            _load_canvases,
            extra_key=_decals_key,
        ),
        Stage("textures", "Seal padding and write the final PNGs", _run_textures, _save_finals, _load_finals),
        Stage(
            "export",
            "Write GLBs, UV layouts and the socket manifest",
            _run_export,
            cacheable=False,
        ),
        Stage(
            "render",
            "Render part and combination previews (no Blender)",
            _run_render,
            cacheable=False,
        ),
    ]
