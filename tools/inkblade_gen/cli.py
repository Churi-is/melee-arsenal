"""Command line entry point for the staged Inkblade generator.

Examples
--------
Generate the whole kit into `build/inkblade_kit`::

    python tools/generate_inkblade_kit.py

Rerun only the ink + downstream stages after tweaking the linework::

    python tools/generate_inkblade_kit.py --only linework

Swap in decal art without touching geometry/UV work::

    python tools/generate_inkblade_kit.py --decals path/to/inkblade_decals

Produce a zip the UE3 compiler can consume directly::

    python tools/generate_inkblade_kit.py --out build/dist/Inkblade_Modular_Kit.zip
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

from .config import RunConfig
from .pipeline import Pipeline, PipelineError
from .stages import default_stages

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "build" / "inkblade_kit"
DEFAULT_WORK = REPO_ROOT / "build" / "inkblade_stages"
DEFAULT_RENDERS = REPO_ROOT / "build" / "inkblade_renders"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_inkblade_kit",
        description="Generate the modular Inkblade kit through a stack of independent stages.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output kit directory or .zip")
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK, help="intermediate stage cache directory")
    parser.add_argument("--density", type=float, default=768.0, help="pixels per metre for the UV unwrap")
    parser.add_argument("--ink-px", type=int, default=6, help="base ink stroke width in texture pixels")
    parser.add_argument("--padding", type=int, default=9, help="UV island padding in pixels")
    parser.add_argument("--seed", default="inkblade", help="master determinism seed")
    parser.add_argument(
        "--no-decals-art",
        dest="decals_art",
        action="store_false",
        help="skip the built-in manufacturer markings (wordmarks, emblems, blood, dirt)",
    )
    parser.add_argument("--decals", type=Path, default=None, help="optional decal art root (<root>/<part_id>/*.png)")
    parser.add_argument("--decal-fit", type=float, default=0.62, help="decal size as a fraction of its island")
    parser.add_argument("--parts", default="", help="comma-separated part ids to generate (default: all)")
    parser.add_argument("--only", default="", help="comma-separated stages to run (prerequisites are added)")
    parser.add_argument("--from", dest="start", default="", help="run the stack from this stage onwards")
    parser.add_argument("--force", default="", help="comma-separated stages to ignore cached output for")
    parser.add_argument("--no-combinations", action="store_true", help="skip the assembled example GLBs")
    parser.add_argument(
        "--linework-seed",
        default="",
        metavar="TEXT",
        help="re-roll the ink layout (hatch corners, stroke breaks, scratches); empty = default seed",
    )
    parser.add_argument(
        "--quantise",
        type=int,
        default=None,
        metavar="COLORS",
        help="quantise finished atlases to an indexed palette (e.g. 64); off by default",
    )
    parser.add_argument(
        "--renders",
        nargs="?",
        const=str(DEFAULT_RENDERS),
        default=None,
        metavar="DIR",
        help=f"also render part/combination previews (default {DEFAULT_RENDERS})",
    )
    parser.add_argument("--render-size", type=int, default=720, help="preview render edge in pixels")
    parser.add_argument(
        "--render-supersample",
        type=int,
        default=2,
        help="preview render supersampling factor (1 = fast draft)",
    )
    parser.add_argument("--revision", default="r6_staged", help="revision label written into the manifest")
    parser.add_argument("--list-stages", action="store_true", help="print the stack and exit")
    return parser


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def config_from_args(args: argparse.Namespace) -> RunConfig:
    return RunConfig(
        out_dir=Path(args.out),
        work_dir=Path(args.work),
        density=float(args.density),
        ink_px=int(args.ink_px),
        padding=int(args.padding),
        seed=str(args.seed),
        linework_seed=str(args.linework_seed),
        quantize_colors=int(args.quantise) if args.quantise else None,
        decal_art=bool(args.decals_art),
        decal_dir=Path(args.decals) if args.decals else None,
        decal_fit=float(args.decal_fit),
        bake_combinations=not args.no_combinations,
        parts=_csv(args.parts) or None,
        render_enabled=args.renders is not None,
        render_dir=Path(args.renders) if args.renders else DEFAULT_RENDERS,
        render_size=int(args.render_size),
        render_supersample=int(args.render_supersample),
        revision=str(args.revision),
    )


def _zip_kit(kit_dir: Path, out_zip: Path) -> Path:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(kit_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path(kit_dir.name) / path.relative_to(kit_dir))
    return out_zip


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stages = default_stages()

    if args.list_stages:
        print("Inkblade generation stage stack:")
        for index, stage in enumerate(stages, start=1):
            kind = "cacheable" if stage.cacheable else "always runs"
            print(f"  {index}. {stage.name:<10} {stage.title} ({kind})")
        return 0

    cfg = config_from_args(args)
    out_is_zip = Path(cfg.out_dir).suffix.lower() == ".zip"
    temp_dir: Path | None = None
    if out_is_zip:
        temp_dir = Path(tempfile.mkdtemp(prefix="inkblade_kit_", dir=str(Path(cfg.work_dir).parent)))
        cfg.out_dir = temp_dir / "Inkblade_Modular_Kit"

    pipeline = Pipeline(cfg, stages)
    try:
        result = pipeline.run(
            only=_csv(args.only) or None,
            start=args.start or None,
            force=_csv(args.force) or None,
        )
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Inkblade kit -> {result.kit_dir}")
    if cfg.render_enabled:
        print(f"Inkblade renders -> {cfg.render_dir}")
    for report in result.reports:
        print(
            f"  [{report.status:>6}] {report.name:<10} {report.title:<52} "
            f"{report.seconds:6.2f}s  {report.artifacts:>3} artifact(s)"
        )
    if result.kit_dir is not None:
        manifest_path = result.kit_dir / "socket_manifest.json"
        if manifest_path.is_file():
            import json

            manifest = json.loads(manifest_path.read_text())
            print(
                f"  parts: {len(manifest.get('parts', {}))}, "
                f"combinations: {len(manifest.get('combinations', []))}, "
                f"revision: {manifest.get('revision')}"
            )

    if out_is_zip:
        assert temp_dir is not None and result.kit_dir is not None
        written = _zip_kit(result.kit_dir, Path(args.out))
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"  packaged: {written}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
