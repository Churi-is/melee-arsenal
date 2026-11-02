"""Shared palette, part metadata and run configuration for the staged Inkblade generator.

Everything that used to be a module-level constant in the R3 (`generate_blender.py` /
`physical_uv.py`) and R5/Bold (`reink_bold.py`) scripts lives here exactly once, so every stage
reads the same numbers instead of carrying its own copy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Manufacturer / element palettes (unchanged since R3; the R4 revision and the R5 Bold paint passes
# both consumed these same tables, so they are shared rather than duplicated per revision).
# ---------------------------------------------------------------------------
PALETTE: dict[str, dict[str, str]] = {
    "Hyperion": {"paint": "#dfb831", "metal": "#b5c3c7", "dark": "#303743", "trim": "#eee5bf", "accent": "#a6dcf1"},
    "Bandit": {"paint": "#b83932", "metal": "#9caaa6", "dark": "#343037", "trim": "#e2d0a0", "accent": "#eea84d"},
    "Jakobs": {"paint": "#72432e", "metal": "#c7c7b3", "dark": "#2e2b29", "trim": "#bc8e42", "accent": "#c7b374"},
    "Maliwan": {"paint": "#e0e6d5", "metal": "#9eb6bd", "dark": "#293745", "trim": "#dc702f", "accent": "#42cbd6"},
    "Torgue": {"paint": "#d97727", "metal": "#aab0ad", "dark": "#353139", "trim": "#e4c14e", "accent": "#f8b644"},
    "Universal": {"paint": "#789b91", "metal": "#b8c2be", "dark": "#30343c", "trim": "#d0a956", "accent": "#d1ded8"},
}

ELEMENTS: dict[str, str] = {
    "Fire": "#ff622b",
    "Shock": "#40baff",
    "Corrosive": "#a0ed32",
    "Slag": "#bc53f3",
    "Explosive": "#ffce36",
}

RARITY_TINTS: dict[str, str] = {
    "Common": "#bbc2c7",
    "Uncommon": "#63b65c",
    "Rare": "#4f8bcc",
    "Very Rare": "#9d64c5",
}

# Atlas background (also the colour bilinear filtering sees between islands).
ATLAS_BACKGROUND: tuple[int, int, int] = (13, 17, 23)
# Ink colour used by every line stage, and the charcoal used by the shading/hatch stage.
INK_RGB: tuple[int, int, int] = (11, 15, 21)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """`'#rrggbb'` -> `(r, g, b)` in 0..255."""
    raw = value.lstrip("#")
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def rgb_unit(value: str) -> tuple[float, float, float]:
    """`'#rrggbb'` -> `(r, g, b)` in 0..1 (used for glTF material factors)."""
    r, g, b = hex_to_rgb(value)
    return (r / 255.0, g / 255.0, b / 255.0)


# ---------------------------------------------------------------------------
# Run configuration
# ---------------------------------------------------------------------------
@dataclass
class RunConfig:
    """Everything a stage may need to know about *how* this run should look.

    The stage stack itself is intentionally config-driven: new art passes (decals, stencils,
    grime, ...) only need their own key here rather than edits to older stages.
    """

    out_dir: Path = field(default_factory=lambda: Path("build/inkblade_kit"))
    work_dir: Path = field(default_factory=lambda: Path("build/inkblade_stages"))
    # Physical texel density of the unwrap (pixels per metre), exactly like R3's `DENSITY`.
    density: float = 768.0
    # Ink stroke width in texture pixels (`INK_PX`) and island padding (`PADDING`).
    ink_px: int = 6
    padding: int = 9
    # Deterministic master seed mixed into every per-face / per-stage RNG.
    seed: str = "inkblade"
    # Optional indexed-palette quantisation of the finished atlas. Off by default: the shipped kit's
    # atlases are RGB PNGs, so a palette would only add snapping on top of the painted art.
    quantize_colors: int | None = None
    # Optional decal directory (`<decal_dir>/<part_id>/*.png`); empty means the decals stage is a
    # no-op passthrough, which keeps the stage in the stack without changing existing art.
    decal_dir: Path | None = None
    decal_fit: float = 0.62
    # Bake per-element accent recolours into the example combinations (R3 behaviour).
    bake_combinations: bool = True
    # Restrict generation to a subset of parts (None = every part in the catalogue).
    parts: tuple[str, ...] | None = None
    # Preview renders (`<render_dir>/{parts,combinations}/` + overview sheets). Off by default so
    # headless kit builds stay fast; `--renders` turns the stage on.
    render_enabled: bool = False
    render_dir: Path = field(default_factory=lambda: Path("build/inkblade_renders"))
    render_size: int = 720
    render_supersample: int = 2
    revision: str = "r6_staged"

    def part_enabled(self, part_id: str) -> bool:
        return self.parts is None or part_id in self.parts

    def to_json(self) -> dict[str, Any]:
        """Stable, JSON-serialisable view used for stage cache fingerprints."""
        return {
            "density": self.density,
            "ink_px": self.ink_px,
            "padding": self.padding,
            "seed": self.seed,
            "quantize_colors": self.quantize_colors,
            "decals": str(self.decal_dir) if self.decal_dir else "",
            "decal_fit": self.decal_fit,
            "bake_combinations": self.bake_combinations,
            "parts": list(self.parts) if self.parts else [],
            "renders": bool(self.render_enabled),
            "render_size": self.render_size,
            "render_supersample": self.render_supersample,
            "revision": self.revision,
        }
