"""Staged, modular Inkblade kit generator.

The kit is produced by a stack of independent, individually cacheable stages (see `stages.py`):

1. `geometry`  — final part meshes (R3 parametric shapes with the R4 revisions applied inline)
2. `uv`        — shared-density face projection, padded island packing, template reuse
3. `flat`      — flat palette colour per UV island (saved as PNG atlases)
4. `mottle`    — paint wash, deposits, wear, chips
5. `linework`  — the shipped kit's two ink passes, ported line for line (seedable)
6. `decals`    — optional external decal art (the template for future art passes)
7. `textures`  — padding fill inside each island rect, optional quantisation, final PNGs
8. `export`    — `<part>.glb`, combinations, UV layout JSON and `socket_manifest.json`
9. `render`    — optional preview PNGs (`--renders`), rendered without Blender

Public API::

    from tools.inkblade_gen import Pipeline, RunConfig
    result = Pipeline(RunConfig(out_dir="build/kit")).run()
"""
from __future__ import annotations

from .config import ELEMENTS, PALETTE, RunConfig
from .geometry import Part, build_parts
from .pipeline import Pipeline, PipelineError, PipelineResult
from .stages import KitState, Stage, StageReport, default_stages
from .uv import PartLayout, unwrap_part, unwrap_parts

__all__ = [
    "ELEMENTS",
    "PALETTE",
    "KitState",
    "Part",
    "PartLayout",
    "Pipeline",
    "PipelineError",
    "PipelineResult",
    "RunConfig",
    "Stage",
    "StageReport",
    "build_parts",
    "default_stages",
    "unwrap_part",
    "unwrap_parts",
]
