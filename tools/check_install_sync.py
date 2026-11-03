#!/usr/bin/env python3
"""Compare a repository/build payload against an installed Melee Arsenal copy.

Used by `./melee status` so a partially installed mod (old Python, missing UPK, stale manifest)
is reported as out-of-sync instead of "installed & in sync".
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SKIP_DIRS = {"__pycache__", "reports", ".git", ".venv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_payload(repo: Path) -> dict[str, Path]:
    """Return installed-relative path -> source file for the complete release payload."""
    mod = repo / "sdk_mods" / "melee_arsenal"
    cooked = repo / "build" / "WillowGame" / "CookedPCConsole"
    manifest = repo / "build" / "inkblade_compiled_manifest.json"
    payload: dict[str, Path] = {}
    for f in sorted(mod.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(mod)
        if any(p in SKIP_DIRS or p.startswith(".") for p in rel.parts):
            continue
        if rel.parts[0] == "upk" or f.suffix in SKIP_SUFFIXES or f.name == ".DS_Store":
            continue
        if rel.as_posix() == "data/inkblade_compiled_manifest.json":
            continue
        payload[f"sdk_mods/melee_arsenal/{rel.as_posix()}"] = f
    if manifest.is_file():
        payload["sdk_mods/melee_arsenal/data/inkblade_compiled_manifest.json"] = manifest
    for name in ("MeleeArsenalMeshes.upk", "MeleeArsenalTextures.upk"):
        src = cooked / name
        if src.is_file():
            payload[f"WillowGame/CookedPCConsole/{name}"] = src
            payload[f"sdk_mods/melee_arsenal/upk/{name}"] = src
    return payload


def compare_install(repo: Path, sdk_dir: Path, cooked_dir: Path) -> dict[str, object]:
    payload = expected_payload(repo)
    missing: list[str] = []
    different: list[str] = []
    for rel, src in payload.items():
        if rel.startswith("sdk_mods/"):
            dst = sdk_dir / rel.removeprefix("sdk_mods/")
        else:
            dst = cooked_dir / Path(rel).name
        if not dst.is_file():
            missing.append(rel)
        elif _sha256(src) != _sha256(dst):
            different.append(rel)
    return {
        "checked": len(payload),
        "missing": missing,
        "different": different,
        "in_sync": not missing and not different and bool(payload),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--sdk-dir", type=Path, required=True)
    ap.add_argument("--cooked-dir", type=Path, required=True)
    args = ap.parse_args()
    result = compare_install(args.repo, args.sdk_dir, args.cooked_dir)
    print(json.dumps(result, indent=2))
    return 0 if result["in_sync"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
