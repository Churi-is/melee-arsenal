#!/usr/bin/env python3
"""CLI compiler turning an `Inkblade_Modular_Kit` (`r3`, `r4`, ...) into cooked Borderlands 2 `.upk` packages."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.inkblade_upk.kit import WeaponFrameCalibration, compile_inkblade_kit_to_upks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kit",
        required=True,
        help="Path to Inkblade_Modular_Kit_<rev>.zip or unpacked kit directory",
    )
    parser.add_argument(
        "--donor",
        default=str(REPO_ROOT / "tools" / "inkblade_upk" / "donors" / "weap_pistol_donor.upk"),
        help="Path to stripped Weap_Pistol donor SkeletalMesh .upk extracted by melee_arsenal_probe",
    )
    parser.add_argument(
        "--out-cooked-dir",
        default=str(REPO_ROOT / "build" / "WillowGame" / "CookedPCConsole"),
        help="Output directory for MeleeArsenalMeshes.upk and MeleeArsenalTextures.upk",
    )
    parser.add_argument(
        "--out-manifest",
        default=str(REPO_ROOT / "build" / "inkblade_compiled_manifest.json"),
        help="Output path for runtime Gestalt/material manifest JSON",
    )
    parser.add_argument("--atlas-size", type=int, default=2048, choices=(1024, 2048, 4096))
    parser.add_argument("--scale", type=float, default=34.0, help="UU per Inkblade meter")
    parser.add_argument(
        "--origin",
        type=float,
        nargs=3,
        default=(0.0, 3.2, 2.3),
        metavar=("X", "Y", "Z"),
        help="Grip/guard junction origin in BL2 Weapon Root UU coordinates",
    )
    parser.add_argument("--pitch-deg", type=float, default=24.0)
    parser.add_argument("--yaw-deg", type=float, default=90.0)
    args = parser.parse_args()

    cooked_dir = Path(args.out_cooked_dir)
    out_meshes = cooked_dir / "MeleeArsenalMeshes.upk"
    out_textures = cooked_dir / "MeleeArsenalTextures.upk"

    cal = WeaponFrameCalibration(
        scale_uu_per_meter=args.scale,
        grip_origin_uu=(float(args.origin[0]), float(args.origin[1]), float(args.origin[2])),
        blade_forward_pitch_deg=args.pitch_deg,
        blade_yaw_deg=args.yaw_deg,
    )

    t0 = time.perf_counter()
    manifest = compile_inkblade_kit_to_upks(
        kit_path=args.kit,
        donor_upk_path_or_bytes=args.donor,
        out_meshes_upk=out_meshes,
        out_textures_upk=out_textures,
        out_manifest_json=args.out_manifest,
        atlas_size=args.atlas_size,
        calibration=cal,
    )
    dt = time.perf_counter() - t0
    print(
        f"[compile_inkblade_upk] Compiled {manifest['kit_revision']} in {dt:.2f}s -> "
        f"{len(manifest['fragments'])} Gestalt fragments ({manifest['total_gpu_vertices']} verts, "
        f"{manifest['total_triangles']} tris)"
    )
    print(f"  Meshes UPK:   {out_meshes} ({out_meshes.stat().st_size / 1024:.1f} KB)")
    print(f"  Textures UPK: {out_textures} ({out_textures.stat().st_size / 1048576:.2f} MB)")
    print(f"  Manifest:     {args.out_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
