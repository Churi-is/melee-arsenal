"""Melee Arsenal — Phase 3 Custom Part Assets, Donor Mesh, Socket & Shader Probe (v0.3.1).

Fixes the `v0.3.0` `LoadPackage("MeleeProbeTex")` crash (`FPackageFileSummary` was 121B instead of
129B — missing the 8 zero bytes for `AdditionalPackagesToCook` + `TextureAllocations`, which caused
UE3 to misread `NameTable[0]` as `AdditionalPackagesToCook.Num = 5`).

Also adds `os.fsync` step-by-step progress logging (`reports/probe_progress.log`), eliminates any
blind `load_package` calls on non-existent `.upk` names, and saves `melee_asset_probe_report.json` +
`weap_pistol_donor.upk` to disk *before* running the `MeleeProbeTex.upk` smoke test (then updates the
report immediately after).

Output file to share back:
  `<Borderlands 2>/sdk_mods/melee_arsenal_probe/reports/melee_asset_probe_report.json`
"""
from __future__ import annotations

import base64
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import time
import traceback
from typing import Any

try:
    import unrealsdk  # type: ignore[import-not-found]
    from mods_base import ButtonOption, Game, build_mod, command, get_pc, hook  # type: ignore[import-not-found]
    from unrealsdk import logging as sdk_logging  # type: ignore[import-not-found]
    from unrealsdk.hooks import Type  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - offline CPython unit testing
    unrealsdk = None  # type: ignore[assignment]
    sdk_logging = None  # type: ignore[assignment]
    ButtonOption = None  # type: ignore[assignment]
    Game = None  # type: ignore[assignment]
    build_mod = None  # type: ignore[assignment]
    get_pc = lambda: None  # type: ignore[assignment]

    class Type:  # type: ignore[no-redef]
        PRE = "PRE"
        POST = "POST"

    def command(*_a: Any, **_kw: Any) -> Any:
        def _dec(fn: Any) -> Any:
            return fn

        return _dec

    def hook(*_a: Any, **_kw: Any) -> Any:
        def _dec(fn: Any) -> Any:
            fn.enable = lambda: None
            fn.disable = lambda: None
            return fn

        return _dec

from .probe_upk_reader import UE3PackageArchive, extract_pistol_donor_and_bounds

__version__ = "0.3.1"
__author__ = "MeleeArsenal"

MOD_DIR = Path(__file__).resolve().parent
REPORT_DIR = MOD_DIR / "reports"
REPORT_FILE = REPORT_DIR / "melee_asset_probe_report.json"
DONOR_UPK_FILE = REPORT_DIR / "weap_pistol_donor.upk"
PROGRESS_LOG_FILE = REPORT_DIR / "probe_progress.log"
BUNDLED_PROBE_TEX = MOD_DIR / "MeleeProbeTex.upk"

KEEP_ALIVE_FLAGS = 0x4000
_KEEP_ALIVE: list[Any] = []

_SWING_SAMPLER: dict[str, Any] = {
    "active": False,
    "started_at": 0.0,
    "last_sample_at": -1.0,
    "duration": 0.72,
    "samples": [],
    "report_data": None,
}


def _log(msg: str) -> None:
    text = f"[MeleeAssetProbe v{__version__}] {msg}"
    if sdk_logging is not None:
        try:
            sdk_logging.info(text)
        except Exception:
            pass
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with PROGRESS_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {text}\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


def _path(obj: Any) -> str:
    if obj is None:
        return "None"
    try:
        return str(obj._path_name())
    except Exception:
        return repr(obj)


def _vec_list(v: Any) -> list[float] | None:
    if v is None:
        return None
    try:
        return [round(float(v.X), 4), round(float(v.Y), 4), round(float(v.Z), 4)]
    except Exception:
        return None


def _rot_dict(r: Any) -> dict[str, Any] | None:
    if r is None:
        return None
    try:
        pitch = int(r.Pitch)
        yaw = int(r.Yaw)
        roll = int(r.Roll)
        scale = 360.0 / 65536.0
        return {
            "uru": [pitch, yaw, roll],
            "deg": [round(pitch * scale, 2), round(yaw * scale, 2), round(roll * scale, 2)],
        }
    except Exception:
        return None


def _rot_axes(rot: Any) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Convert UE3 `FRotator` (`Pitch`, `Yaw`, `Roll` in `65536 = 360°`) into `(forward, right, up)` unit vectors."""
    uru_to_rad = (2.0 * math.pi) / 65536.0
    p = float(getattr(rot, "Pitch", 0)) * uru_to_rad
    y = float(getattr(rot, "Yaw", 0)) * uru_to_rad
    r = float(getattr(rot, "Roll", 0)) * uru_to_rad

    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    cr, sr = math.cos(r), math.sin(r)

    fwd = (cp * cy, cp * sy, sp)
    right = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = (-(cr * sp * cy + sr * sy), -cr * sp * sy + sr * cy, cr * cp)
    return fwd, right, up


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _world_to_cam_local(
    world_pt: tuple[float, float, float],
    cam_loc: tuple[float, float, float],
    cam_axes: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> dict[str, float]:
    delta = (world_pt[0] - cam_loc[0], world_pt[1] - cam_loc[1], world_pt[2] - cam_loc[2])
    fwd, right, up = cam_axes
    return {
        "forward_uu": round(_dot(delta, fwd), 3),
        "right_uu": round(_dot(delta, right), 3),
        "up_uu": round(_dot(delta, up), 3),
    }


def _find(cls_name: str, obj_full_path: str) -> Any:
    if unrealsdk is None:
        return None
    try:
        return unrealsdk.find_object(cls_name, obj_full_path)
    except Exception:
        return None


def _safe_load_upk_if_on_disk(cooked_dir: Path | None, pkg_name: str) -> Any:
    """Only call `unrealsdk.load_package` if not already loaded AND `<pkg_name>.upk` exists on disk."""
    if unrealsdk is None:
        return None
    existing = _find("Package", pkg_name)
    if existing is not None:
        return existing
    if cooked_dir is not None and not (cooked_dir / f"{pkg_name}.upk").is_file():
        return None
    try:
        return unrealsdk.load_package(pkg_name)
    except Exception:
        return None


def _locate_cooked_pc_console() -> Path | None:
    candidates: list[Path] = []
    if sys.executable:
        exe_p = Path(sys.executable).resolve()
        for anc in exe_p.parents:
            candidates.append(anc / "WillowGame" / "CookedPCConsole")
    for anc in MOD_DIR.parents:
        candidates.append(anc / "WillowGame" / "CookedPCConsole")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _notify_hud(pc: Any, title: str, message: str, duration: float = 5.0) -> None:
    if pc is None:
        return
    try:
        hud = pc.GetHUDMovie()
        if hud is not None:
            hud.ClearTrainingText()
            hud.AddTrainingText(message, title, duration, (), "", False, 0, pc.PlayerReplicationInfo, True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Probe Section 1: Custom `.upk` Texture2D + `Master_ClassMod` Smoke Test
# ---------------------------------------------------------------------------


def _probe_custom_upk_and_shader(cooked_dir: Path | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cooked_dir": str(cooked_dir) if cooked_dir else None,
        "bundled_probe_tex_exists": BUNDLED_PROBE_TEX.exists(),
    }
    if unrealsdk is None:
        out["status"] = "offline_mock"
        return out

    try:
        if BUNDLED_PROBE_TEX.exists():
            raw_hdr = BUNDLED_PROBE_TEX.read_bytes()[:40]
            noff = struct.unpack_from("<I", raw_hdr, 29)[0] if len(raw_hdr) >= 33 else 0
            out["bundled_name_offset"] = noff
            if noff != 129:
                out["status"] = f"aborted_invalid_summary_name_offset_{noff}"
                return out

        if cooked_dir is not None and BUNDLED_PROBE_TEX.exists():
            dst_upk = cooked_dir / "MeleeProbeTex.upk"
            # Always overwrite if bytes differ (e.g. replacing v0.3.0's 121B-header file with v0.3.1's 129B file!)
            if not dst_upk.exists() or dst_upk.read_bytes() != BUNDLED_PROBE_TEX.read_bytes():
                shutil.copy2(BUNDLED_PROBE_TEX, dst_upk)
                out["copied_probe_upk_to"] = str(dst_upk)
            else:
                out["existing_probe_upk"] = str(dst_upk)

        _log("p01: loading Item_ClassMods (if needed) and MeleeProbeTex...")
        for pkg_name in ("Item_ClassMods", "MeleeProbeTex"):
            lp = _safe_load_upk_if_on_disk(cooked_dir, pkg_name)
            if lp is not None:
                try:
                    lp.ObjectFlags |= KEEP_ALIVE_FLAGS
                    _KEEP_ALIVE.append(lp)
                except Exception:
                    pass
            out[f"loaded_{pkg_name}"] = _path(lp)

        t_alb = _find("Texture2D", "MeleeProbeTex.T_Probe_Inkblade")
        t_nrm = _find("Texture2D", "MeleeProbeTex.T_Probe_Normal_Flat")
        out["T_Probe_Inkblade"] = (
            {
                "path": _path(t_alb),
                "SizeX": int(getattr(t_alb, "SizeX", -1)),
                "SizeY": int(getattr(t_alb, "SizeY", -1)),
                "Format": str(getattr(t_alb, "Format", "")),
                "NeverStream": bool(getattr(t_alb, "NeverStream", False)),
                "LODGroup": str(getattr(t_alb, "LODGroup", "")),
            }
            if t_alb is not None
            else None
        )
        out["T_Probe_Normal_Flat"] = (
            {
                "path": _path(t_nrm),
                "SizeX": int(getattr(t_nrm, "SizeX", -1)),
                "SizeY": int(getattr(t_nrm, "SizeY", -1)),
                "SRGB": bool(getattr(t_nrm, "SRGB", True)),
                "CompressionSettings": str(getattr(t_nrm, "CompressionSettings", "")),
            }
            if t_nrm is not None
            else None
        )

        parent_mat = _find("MaterialInterface", "Item_ClassMods.Mat.Master_ClassMod")
        out["Master_ClassMod"] = _path(parent_mat)
        if parent_mat is not None and t_alb is not None and t_nrm is not None:
            pkg = _find("Package", "MeleeArsenalProbePkg")
            if pkg is None:
                pkg = unrealsdk.construct_object("Package", None, "MeleeArsenalProbePkg", KEEP_ALIVE_FLAGS, None)
                _KEEP_ALIVE.append(pkg)
            mic = _find("MaterialInstanceConstant", "MeleeArsenalProbePkg.MIC_Probe_Inkblade")
            if mic is None:
                mic = unrealsdk.construct_object(
                    "MaterialInstanceConstant", pkg, "MIC_Probe_Inkblade", KEEP_ALIVE_FLAGS, None
                )
                _KEEP_ALIVE.append(mic)
            mic.SetParent(parent_mat)
            mic.SetTextureParameterValue("p_Diffuse", t_alb)
            mic.SetTextureParameterValue("p_Normal", t_nrm)
            bound = [
                {
                    "name": str(getattr(tp, "ParameterName", "")),
                    "value": _path(getattr(tp, "ParameterValue", None)),
                }
                for tp in (getattr(mic, "TextureParameterValues", None) or [])
            ]
            out["mic_smoke_test"] = {
                "mic_path": _path(mic),
                "parent": _path(getattr(mic, "Parent", None)),
                "texture_params": bound,
                "status": "OK",
            }
        else:
            out["mic_smoke_test"] = {"status": "missing_parent_or_textures"}
    except Exception as exc:
        out["error"] = f"{exc!r}\n{traceback.format_exc()}"
    return out


# ---------------------------------------------------------------------------
# Probe Section 2: Live `Weap_Pistol.GestaltDef_Pistol` & Stock Part Mappings
# ---------------------------------------------------------------------------


def _probe_gestalt_def_pistol_live(cooked_dir: Path | None) -> tuple[dict[str, Any], list[tuple[str, int, int]]]:
    out: dict[str, Any] = {}
    frag_ranges: list[tuple[str, int, int]] = []
    if unrealsdk is None:
        return {"status": "offline_mock"}, frag_ranges

    try:
        _safe_load_upk_if_on_disk(cooked_dir, "GD_Weap_Pistol")

        gestalt = _find("GestaltSkeletalMeshDefinition", "Weap_Pistol.GestaltDef_Pistol")
        if gestalt is None:
            for g in unrealsdk.find_all("GestaltSkeletalMeshDefinition") or []:
                if "pistol" in _path(g).lower() and "gestaltdef_pistol" in _path(g).lower():
                    gestalt = g
                    break

        if gestalt is None:
            out["status"] = "GestaltDef_Pistol_not_found"
            return out, frag_ranges

        sk_mesh = getattr(gestalt, "GestaltSkeletalMesh", None)
        out["gestalt_path"] = _path(gestalt)
        out["skeletal_mesh_path"] = _path(sk_mesh)

        infos = list(getattr(gestalt, "GestaltInfos", None) or [])
        out["gestalt_infos_count"] = len(infos)
        parts_dump: list[dict[str, Any]] = []
        if infos:
            info0 = infos[0]
            info_props = []
            try:
                info_props = [str(p.Name) for p in info0._type._properties()]
            except Exception:
                pass
            out["gestalt_info0_fields"] = info_props
            for entry in getattr(info0, "Parts", None) or []:
                fname = str(getattr(entry, "SkeletalMeshFragmentName", ""))
                fidx = int(getattr(entry, "FirstIndex", 0))
                nprims = int(getattr(entry, "NumPrimitives", 0))
                row: dict[str, Any] = {
                    "fragment": fname,
                    "first_index": fidx,
                    "num_primitives": nprims,
                }
                for extra_field in ("PartGroupIndex", "IndexBufferIndex", "SectionIndex", "BoneIndex"):
                    if hasattr(entry, extra_field):
                        try:
                            row[extra_field] = int(getattr(entry, extra_field))
                        except Exception:
                            row[extra_field] = str(getattr(entry, extra_field))
                parts_dump.append(row)
                if fname and fname != "None" and nprims > 0:
                    frag_ranges.append((fname, fidx, nprims))
        out["parts"] = parts_dump

        bounds_dump: list[dict[str, Any]] = []
        for b in getattr(gestalt, "GestaltPartBounds", None) or []:
            box = getattr(b, "ReferencePoseBounds", None)
            bounds_dump.append(
                {
                    "fragment": str(getattr(b, "SkeletalMeshFragmentName", "")),
                    "origin": _vec_list(getattr(box, "Origin", None)),
                    "extent": _vec_list(getattr(box, "BoxExtent", None)),
                    "radius": round(float(getattr(box, "SphereRadius", 0.0)), 3) if box is not None else 0.0,
                }
            )
        out["part_bounds"] = bounds_dump

        sock_maps: list[dict[str, str]] = []
        for sm in getattr(gestalt, "GestaltSocketMappings", None) or []:
            sock_maps.append(
                {
                    "fragment": str(getattr(sm, "SkeletalMeshFragmentName", "")),
                    "original_socket": str(getattr(sm, "OriginalSocketName", "")),
                    "mangled_socket": str(getattr(sm, "MangledSocketName", "")),
                }
            )
        out["socket_mappings"] = sock_maps

        mesh_sockets: list[dict[str, Any]] = []
        if sk_mesh is not None:
            out["mesh_origin"] = _vec_list(getattr(sk_mesh, "Origin", None))
            out["mesh_rot_origin"] = _rot_dict(getattr(sk_mesh, "RotOrigin", None))
            out["mesh_materials"] = [_path(m) for m in (getattr(sk_mesh, "Materials", None) or [])]
            for s in getattr(sk_mesh, "Sockets", None) or []:
                mesh_sockets.append(
                    {
                        "socket_name": str(getattr(s, "SocketName", "")),
                        "bone_name": str(getattr(s, "BoneName", "")),
                        "relative_location": _vec_list(getattr(s, "RelativeLocation", None)),
                        "relative_rotation": _rot_dict(getattr(s, "RelativeRotation", None)),
                        "relative_scale": _vec_list(getattr(s, "RelativeScale", None)),
                    }
                )
        out["mesh_sockets"] = mesh_sockets

        stock_part_frags: list[dict[str, Any]] = []
        for wp in unrealsdk.find_all("WeaponPartDefinition") or []:
            wpath = _path(wp)
            if not wpath.startswith("GD_Weap_Pistol."):
                continue
            gname = str(getattr(wp, "GestaltModeSkeletalMeshName", "None"))
            addl = [str(x) for x in (getattr(wp, "AdditionalGestaltModeSkeletalMeshNames", None) or [])]
            stock_part_frags.append(
                {
                    "part": wpath,
                    "gestalt_fragment": gname,
                    "additional_fragments": addl,
                }
            )
        out["stock_pistol_parts"] = stock_part_frags
    except Exception as exc:
        out["error"] = f"{exc!r}\n{traceback.format_exc()}"

    return out, frag_ranges


# ---------------------------------------------------------------------------
# Probe Section 3: Pure-Python `Startup.upk` Donor Extraction & Vertex Bounds
# ---------------------------------------------------------------------------


def _probe_startup_upk_pistol_donor(
    cooked_dir: Path | None,
    frag_ranges: list[tuple[str, int, int]],
    target_mesh_path: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"requested_mesh_path": target_mesh_path}
    if cooked_dir is None:
        return {"status": "cooked_dir_not_found"}

    candidate_upks: list[Path] = []
    if target_mesh_path and "." in target_mesh_path:
        pkg_prefix = target_mesh_path.split(".", 1)[0]
        candidate_upks.append(cooked_dir / f"{pkg_prefix}.upk")
    candidate_upks.append(cooked_dir / "Startup.upk")
    candidate_upks.append(cooked_dir / "Weap_Pistol.upk")

    seen_upks: set[Path] = set()
    existing_upks: list[Path] = []
    for p in candidate_upks:
        if p not in seen_upks and p.exists():
            seen_upks.add(p)
            existing_upks.append(p)

    if not existing_upks:
        return {
            "status": "Startup.upk_not_found",
            "checked_paths": [str(p) for p in candidate_upks],
        }

    target_leaf = (
        target_mesh_path.rsplit(".", 1)[-1].lower()
        if target_mesh_path
        else "gestaltdef_pistol_gestaltskeletalmesh"
    )
    t0 = time.perf_counter()
    try:
        all_seen_meshes: list[dict[str, Any]] = []
        for upk_path in existing_upks:
            with UE3PackageArchive(upk_path, max_cached_blocks=48) as ar:
                skel_exports = [e for e in ar.exports if ar.export_class(e) == "SkeletalMesh"]
                for e in skel_exports:
                    all_seen_meshes.append(
                        {
                            "upk": upk_path.name,
                            "path": ar.resolve_full_path(e.index + 1),
                            "serial_size": e.serial_size,
                        }
                    )

                target_exp = None
                for e in skel_exports:
                    fp = ar.resolve_full_path(e.index + 1).lower()
                    if e.object_name.lower() == target_leaf or target_leaf in fp:
                        target_exp = e
                        break
                if target_exp is None:
                    for e in skel_exports:
                        fp = ar.resolve_full_path(e.index + 1).lower()
                        if "gestaltdef_pistol_gestaltskeletalmesh" in fp or "pistol" in fp:
                            target_exp = e
                            break

                if target_exp is None:
                    continue

                out["source_upk_path"] = str(upk_path)
                out["file_size_bytes"] = upk_path.stat().st_size
                out["uncompressed_size_bytes"] = ar.uncompressed_size
                out["is_fully_compressed"] = ar.is_fully_compressed
                out["is_chunk_compressed"] = ar.is_chunk_compressed
                out["name_count"] = len(ar.names)
                out["import_count"] = len(ar.imports)
                out["export_count"] = len(ar.exports)
                out["all_startup_skeletal_meshes"] = all_seen_meshes

                extracted = extract_pistol_donor_and_bounds(ar, target_exp, frag_ranges)
                donor_bytes: bytes = extracted.pop("donor_upk_bytes")
                REPORT_DIR.mkdir(parents=True, exist_ok=True)
                DONOR_UPK_FILE.write_bytes(donor_bytes)

                extracted["donor_upk_file"] = str(DONOR_UPK_FILE)
                extracted["donor_upk_size_bytes"] = len(donor_bytes)
                extracted["donor_upk_base64"] = base64.b64encode(donor_bytes).decode("ascii")
                extracted["blocks_decompressed"] = ar.blocks_decompressed
                extracted["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
                out.update(extracted)
                out["status"] = "OK"
                return out

        out["all_startup_skeletal_meshes"] = all_seen_meshes
        out["status"] = "GestaltDef_Pistol_GestaltSkeletalMesh_not_in_Startup"
    except Exception as exc:
        out["status"] = "error"
        out["error"] = f"{exc!r}\n{traceback.format_exc()}"
    return out


# ---------------------------------------------------------------------------
# Probe Section 4: Master Materials & Elemental Weapon Shaders
# ---------------------------------------------------------------------------


def _dump_material_chain(mat: Any, max_depth: int = 5) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    cur = mat
    seen: set[str] = set()
    depth = 0
    while cur is not None and depth < max_depth:
        p = _path(cur)
        if p in seen:
            break
        seen.add(p)
        depth += 1
        tex_p = [
            {
                "name": str(getattr(tp, "ParameterName", "")),
                "value": _path(getattr(tp, "ParameterValue", None)),
            }
            for tp in (getattr(cur, "TextureParameterValues", None) or [])
        ]
        vec_p = []
        for vp in getattr(cur, "VectorParameterValues", None) or []:
            pv = getattr(vp, "ParameterValue", None)
            vec_p.append(
                {
                    "name": str(getattr(vp, "ParameterName", "")),
                    "rgba": [
                        round(float(getattr(pv, "R", 0.0)), 4),
                        round(float(getattr(pv, "G", 0.0)), 4),
                        round(float(getattr(pv, "B", 0.0)), 4),
                        round(float(getattr(pv, "A", 0.0)), 4),
                    ]
                    if pv is not None
                    else None,
                }
            )
        sca_p = [
            {
                "name": str(getattr(sp, "ParameterName", "")),
                "value": round(float(getattr(sp, "ParameterValue", 0.0)), 4),
            }
            for sp in (getattr(cur, "ScalarParameterValues", None) or [])
        ]
        chain.append(
            {
                "path": p,
                "class": str(getattr(getattr(cur, "Class", None), "Name", "")),
                "textures": tex_p,
                "vectors": vec_p,
                "scalars": sca_p,
            }
        )
        cur = getattr(cur, "Parent", None)
    return chain


def _probe_master_materials() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if unrealsdk is None:
        return {"status": "offline_mock"}
    try:
        cm = _find("MaterialInterface", "Item_ClassMods.Mat.Master_ClassMod")
        out["Master_ClassMod_chain"] = _dump_material_chain(cm)

        pistol_mats: dict[str, Any] = {}
        for wp in unrealsdk.find_all("WeaponPartDefinition") or []:
            wpath = _path(wp)
            if "GD_Weap_Pistol.ManufacturerMaterials." in wpath and getattr(wp, "Material", None) is not None:
                short = wpath.rsplit(".", 1)[-1]
                if len(pistol_mats) < 10:
                    pistol_mats[short] = _dump_material_chain(wp.Material)
        out["pistol_manufacturer_materials"] = pistol_mats
    except Exception as exc:
        out["error"] = repr(exc)
    return out


# ---------------------------------------------------------------------------
# Probe Section 5: 1st-Person, 3rd-Person, Pickup & `Bladed_Melee` Swing Arc
# ---------------------------------------------------------------------------


def _query_socket_world(skel_comp: Any, sock_name: str, bone_fallback: str | None = None) -> tuple[Any, Any]:
    """Query `(world_vec, world_rot)` on `skel_comp` safely across `pyunrealsdk` signatures."""
    try:
        res = skel_comp.GetSocketWorldLocationAndRotation(sock_name)
        if isinstance(res, tuple) and len(res) == 3 and res[0]:
            return res[1], res[2]
        if isinstance(res, tuple) and len(res) == 2:
            return res[0], res[1]
    except Exception:
        pass
    if bone_fallback and hasattr(skel_comp, "GetBoneLocation"):
        try:
            bloc = skel_comp.GetBoneLocation(bone_fallback, 0)
            return bloc, None
        except Exception:
            pass
    return None, None


def _sample_arms_and_weapon_pose(pc: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {}
    if pc is None:
        return snap
    pawn = getattr(pc, "Pawn", None)
    if pawn is None:
        return snap

    try:
        cam_v, cam_r = pc.GetPlayerViewPoint()
        cam_loc = (float(cam_v.X), float(cam_v.Y), float(cam_v.Z))
        cam_axes = _rot_axes(cam_r)
        snap["camera"] = {
            "location": [round(x, 3) for x in cam_loc],
            "rotation": _rot_dict(cam_r),
            "fov": round(float(getattr(pc, "FOVAngle", 90.0)), 2),
        }
    except Exception:
        ploc = getattr(pawn, "Location", None)
        cam_loc = (
            float(getattr(ploc, "X", 0.0)),
            float(getattr(ploc, "Y", 0.0)),
            float(getattr(ploc, "Z", 0.0)) + float(getattr(pawn, "EyeHeight", 64.0)),
        )
        cam_axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    arms = getattr(pawn, "Arms", None)
    if arms is not None:
        sm = getattr(arms, "SkeletalMesh", None)
        sock_defs = []
        sock_bone_map: dict[str, str] = {}
        for s in getattr(sm, "Sockets", None) or []:
            sn = str(getattr(s, "SocketName", ""))
            bn = str(getattr(s, "BoneName", ""))
            sock_bone_map[sn] = bn
            sock_defs.append(
                {
                    "socket_name": sn,
                    "bone_name": bn,
                    "relative_location": _vec_list(getattr(s, "RelativeLocation", None)),
                    "relative_rotation": _rot_dict(getattr(s, "RelativeRotation", None)),
                    "relative_scale": _vec_list(getattr(s, "RelativeScale", None)),
                }
            )
        arms_info: dict[str, Any] = {
            "path": _path(arms),
            "skeletal_mesh": _path(sm),
            "mesh_origin": _vec_list(getattr(sm, "Origin", None)) if sm is not None else None,
            "mesh_rot_origin": _rot_dict(getattr(sm, "RotOrigin", None)) if sm is not None else None,
            "scale": round(float(getattr(arms, "Scale", 1.0)), 4),
            "scale3d": _vec_list(getattr(arms, "Scale3D", None)),
            "translation": _vec_list(getattr(arms, "Translation", None)),
            "rotation": _rot_dict(getattr(arms, "Rotation", None)),
            "fov": round(float(getattr(arms, "FOV", 0.0)), 2) if hasattr(arms, "FOV") else None,
            "socket_defs": sock_defs,
            "sockets": {},
        }
        for sname in [d["socket_name"] for d in sock_defs] or ["Weapon"]:
            if not sname:
                continue
            sloc, srot = _query_socket_world(arms, sname, sock_bone_map.get(sname))
            if sloc is not None:
                wpt = (float(sloc.X), float(sloc.Y), float(sloc.Z))
                entry_s: dict[str, Any] = {
                    "world_loc": [round(x, 3) for x in wpt],
                    "cam_local_loc": _world_to_cam_local(wpt, cam_loc, cam_axes),
                    "world_rot": _rot_dict(srot),
                }
                if srot is not None:
                    s_fwd, s_right, s_up = _rot_axes(srot)
                    entry_s["socket_axes_in_cam"] = {
                        "socket_X_in_cam": [
                            round(_dot(s_fwd, cam_axes[0]), 4),
                            round(_dot(s_fwd, cam_axes[1]), 4),
                            round(_dot(s_fwd, cam_axes[2]), 4),
                        ],
                        "socket_Y_in_cam": [
                            round(_dot(s_right, cam_axes[0]), 4),
                            round(_dot(s_right, cam_axes[1]), 4),
                            round(_dot(s_right, cam_axes[2]), 4),
                        ],
                        "socket_Z_in_cam": [
                            round(_dot(s_up, cam_axes[0]), 4),
                            round(_dot(s_up, cam_axes[1]), 4),
                            round(_dot(s_up, cam_axes[2]), 4),
                        ],
                    }
                arms_info["sockets"][sname] = entry_s
        snap["arms"] = arms_info

    wpn = getattr(pawn, "Weapon", None)
    if wpn is not None:
        wpn_info: dict[str, Any] = {
            "path": _path(wpn),
            "name": str(wpn.GetHumanReadableName()) if hasattr(wpn, "GetHumanReadableName") else None,
        }
        for mfield in ("FirstPersonMesh", "ThirdPersonMesh", "Mesh"):
            mc = getattr(wpn, mfield, None)
            if mc is None:
                continue
            m_entry: dict[str, Any] = {
                "path": _path(mc),
                "skeletal_mesh": _path(getattr(mc, "SkeletalMesh", None)),
                "scale": round(float(getattr(mc, "Scale", 1.0)), 4),
                "scale3d": _vec_list(getattr(mc, "Scale3D", None)),
                "translation": _vec_list(getattr(mc, "Translation", None)),
                "rotation": _rot_dict(getattr(mc, "Rotation", None)),
                "fov": round(float(getattr(mc, "FOV", 0.0)), 2) if hasattr(mc, "FOV") else None,
                "sockets": {},
            }
            for wsock in ("Muzzle", "RearSight", "EjectPort", "FrontSight"):
                wsloc, wsrot = _query_socket_world(mc, wsock)
                if wsloc is not None:
                    wpt = (float(wsloc.X), float(wsloc.Y), float(wsloc.Z))
                    m_entry["sockets"][wsock] = {
                        "cam_local_loc": _world_to_cam_local(wpt, cam_loc, cam_axes),
                        "world_rot": _rot_dict(wsrot),
                    }
            wpn_info[mfield] = m_entry
        snap["equipped_weapon"] = wpn_info

    body_mesh = getattr(pawn, "Mesh", None)
    if body_mesh is not None:
        bsm = getattr(body_mesh, "SkeletalMesh", None)
        bsocks = [
            {
                "socket_name": str(getattr(s, "SocketName", "")),
                "bone_name": str(getattr(s, "BoneName", "")),
                "relative_location": _vec_list(getattr(s, "RelativeLocation", None)),
                "relative_rotation": _rot_dict(getattr(s, "RelativeRotation", None)),
                "relative_scale": _vec_list(getattr(s, "RelativeScale", None)),
            }
            for s in (getattr(bsm, "Sockets", None) or [])
        ]
        snap["third_person_body_mesh"] = {
            "skeletal_mesh": _path(bsm),
            "scale": round(float(getattr(body_mesh, "Scale", 1.0)), 4),
            "sockets": bsocks,
        }

    if unrealsdk is not None:
        pickups_out: list[dict[str, Any]] = []
        for pk in (unrealsdk.find_all("WillowPickup") or [])[:5]:
            try:
                pmesh = getattr(pk, "Mesh", None) or getattr(pk, "PickupMesh", None)
                pickups_out.append(
                    {
                        "pickup": _path(pk),
                        "inventory": _path(getattr(pk, "Inventory", None)),
                        "location": _vec_list(getattr(pk, "Location", None)),
                        "rotation": _rot_dict(getattr(pk, "Rotation", None)),
                        "draw_scale": round(float(getattr(pk, "DrawScale", 1.0)), 4),
                        "mesh_component": _path(pmesh),
                        "mesh_scale": round(float(getattr(pmesh, "Scale", 1.0)), 4) if pmesh is not None else None,
                        "mesh_translation": _vec_list(getattr(pmesh, "Translation", None))
                        if pmesh is not None
                        else None,
                    }
                )
            except Exception:
                continue
        snap["world_pickups_sample"] = pickups_out

    return snap


def _trigger_bladed_melee_swing(pc: Any) -> dict[str, Any]:
    if pc is None or unrealsdk is None:
        return {"triggered": False}
    pawn = getattr(pc, "Pawn", None)
    arms = getattr(pawn, "Arms", None) if pawn is not None else None
    if arms is None:
        return {"triggered": False, "reason": "no_arms"}

    for slot in unrealsdk.find_all("AnimNodeSlot", exact=False) or []:
        try:
            if getattr(slot, "SkelComponent", None) == arms and str(getattr(slot, "NodeName", "")) in (
                "WeaponAction",
                "HandSlot",
            ):
                dur = slot.PlayCustomAnim("Bladed_Melee", 1.0, 0.06, 0.10, False, True)
                if dur and float(dur) > 0.0:
                    return {
                        "triggered": True,
                        "slot_node": str(getattr(slot, "NodeName", "")),
                        "duration": round(float(dur), 4),
                    }
        except Exception:
            continue
    return {"triggered": False, "reason": "no_matching_slot_or_sequence"}


# ---------------------------------------------------------------------------
# Probe Section 6: Explosion VFX (`ParticleSystem`) & Audio (`AkEvent`) Catalog
# ---------------------------------------------------------------------------


def _probe_explosion_vfx_and_audio() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if unrealsdk is None:
        return {"status": "offline_mock"}

    keywords = ("explo", "grenade", "torgue", "fireball", "boom", "blast", "rocket")
    try:
        emitters: list[str] = []
        for ps in unrealsdk.find_all("ParticleSystem") or []:
            p = _path(ps)
            pl = p.lower()
            if any(k in pl for k in keywords) and "ui_" not in pl:
                emitters.append(p)
                if len(emitters) >= 60:
                    break
        out["explosion_particle_systems"] = emitters

        ak_events: list[str] = []
        for ak in unrealsdk.find_all("AkEvent") or []:
            p = _path(ak)
            pl = p.lower()
            if any(k in pl for k in keywords) and "play" in pl:
                ak_events.append(p)
                if len(ak_events) >= 60:
                    break
        out["explosion_ak_events"] = ak_events

        impacts: list[dict[str, Any]] = []
        for imp in unrealsdk.find_all("ImpactDefinition") or []:
            p = _path(imp)
            if "explo" in p.lower() or "grenade" in p.lower():
                impacts.append({"path": p})
                if len(impacts) >= 25:
                    break
        out["explosion_impact_definitions"] = impacts
    except Exception as exc:
        out["error"] = repr(exc)
    return out


# ---------------------------------------------------------------------------
# Report Writer & `Bladed_Melee` Swing Tick Sampler
# ---------------------------------------------------------------------------


def _write_report(report_data: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_data["finished_iso"] = datetime.now().isoformat()
    payload = json.dumps(report_data, indent=2)
    with REPORT_FILE.open("w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    _log(f"Saved report -> {REPORT_FILE} ({len(payload)} bytes)")
    return REPORT_FILE


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def _swing_sampler_tick(obj: Any, _args: Any, _ret: Any, _func: Any) -> None:
    if not _SWING_SAMPLER["active"]:
        return
    now = time.perf_counter()
    elapsed = now - float(_SWING_SAMPLER["started_at"])
    if now - float(_SWING_SAMPLER["last_sample_at"]) >= 0.032:
        _SWING_SAMPLER["last_sample_at"] = now
        pose = _sample_arms_and_weapon_pose(obj)
        arms_sock = ((pose.get("arms") or {}).get("sockets") or {}).get("Weapon")
        _SWING_SAMPLER["samples"].append(
            {
                "t_sec": round(elapsed, 4),
                "weapon_socket": arms_sock,
            }
        )
    if elapsed >= float(_SWING_SAMPLER["duration"]):
        _SWING_SAMPLER["active"] = False
        try:
            _swing_sampler_tick.disable()
        except Exception:
            pass
        rep = _SWING_SAMPLER.get("report_data")
        if isinstance(rep, dict):
            rep.setdefault("p05_live_poses_and_swing_arc", {})["swing_trajectory_samples"] = list(
                _SWING_SAMPLER["samples"]
            )
            _write_report(rep)
        _notify_hud(
            obj,
            "Melee Asset Probe v0.3.1: COMPLETE!",
            f"Saved report:\n{REPORT_FILE.name}\n(Ready to share back!)",
            6.5,
        )


def run_asset_probe() -> dict[str, Any]:
    """Execute all Phase 3 Asset, Donor, Socket, Shader, Pose & VFX probes safely."""
    _log("=== Starting Phase 3 Asset & Socket Probe v0.3.1 ===")
    pc = get_pc()
    cooked_dir = _locate_cooked_pc_console()
    _log(f"cooked_dir={cooked_dir}")

    _log("Step 1/6: p02_gestalt_def_pistol_live...")
    p02, frag_ranges = _probe_gestalt_def_pistol_live(cooked_dir)
    _log(f"p02 done: {len(frag_ranges)} fragments")

    _log("Step 2/6: p03_startup_upk_pistol_donor...")
    p03 = _probe_startup_upk_pistol_donor(cooked_dir, frag_ranges, p02.get("skeletal_mesh_path"))
    _log(f"p03 done: status={p03.get('status')}")

    _log("Step 3/6: p04_master_materials_and_shaders...")
    p04 = _probe_master_materials()
    _log("p04 done")

    _log("Step 4/6: p05_live_poses (idle)...")
    idle_pose = _sample_arms_and_weapon_pose(pc)
    _log("p05 idle done")

    _log("Step 5/6: p06_explosion_vfx_and_audio...")
    p06 = _probe_explosion_vfx_and_audio()
    _log("p06 done")

    report_data: dict[str, Any] = {
        "probe_version": __version__,
        "started_iso": datetime.now().isoformat(),
        "map_name": (
            str(pc.WorldInfo.GetStreamingPersistentMapName())
            if pc is not None and getattr(pc, "WorldInfo", None) is not None
            else "menu_or_offline"
        ),
        "p01_custom_upk_and_shader_smoke": {"status": "pending"},
        "p02_gestalt_def_pistol_live": p02,
        "p03_startup_upk_pistol_donor": p03,
        "p04_master_materials_and_shaders": p04,
        "p05_live_poses_and_swing_arc": {
            "idle_pose": idle_pose,
            "bladed_melee_trigger": None,
            "swing_trajectory_samples": [],
        },
        "p06_explosion_vfx_and_audio": p06,
    }

    # Flush full core report (including donor_upk_base64 & 3D bounds) BEFORE loading MeleeProbeTex.upk!
    _write_report(report_data)

    _log("Step 6/6: p01_custom_upk_and_shader_smoke...")
    p01 = _probe_custom_upk_and_shader(cooked_dir)
    report_data["p01_custom_upk_and_shader_smoke"] = p01
    _log(f"p01 done: mic_smoke_test={p01.get('mic_smoke_test')}")

    swing_trig = _trigger_bladed_melee_swing(pc)
    report_data["p05_live_poses_and_swing_arc"]["bladed_melee_trigger"] = swing_trig
    _write_report(report_data)

    if swing_trig.get("triggered") and pc is not None:
        _SWING_SAMPLER["active"] = True
        _SWING_SAMPLER["started_at"] = time.perf_counter()
        _SWING_SAMPLER["last_sample_at"] = -1.0
        _SWING_SAMPLER["duration"] = max(0.68, float(swing_trig.get("duration", 0.65)) + 0.06)
        _SWING_SAMPLER["samples"] = []
        _SWING_SAMPLER["report_data"] = report_data
        try:
            _swing_sampler_tick.enable()
        except Exception:
            pass
        _notify_hud(
            pc,
            "Melee Asset Probe v0.3.1",
            "Sampling Bladed_Melee swing arc (~0.7s)...",
            2.0,
        )
    else:
        _notify_hud(
            pc,
            "Melee Asset Probe v0.3.1: COMPLETE!",
            f"Saved report:\n{REPORT_FILE.name}",
            6.0,
        )
    return report_data


@command("melee_asset_probe", description="Run Phase 3 Custom Asset, Startup.upk Donor, Socket & Pose Probe.")
def _cmd_melee_asset_probe(_args: Any) -> None:
    run_asset_probe()


@command("melee_probe_test", description="Alias for melee_asset_probe (Phase 3 Asset & Socket Probe).")
def _cmd_melee_probe_test_alias(_args: Any) -> None:
    run_asset_probe()


if build_mod is not None and ButtonOption is not None:
    _btn_run = ButtonOption(
        "Run Phase 3 Asset & Socket Probe",
        on_press=lambda _opt: run_asset_probe(),
        description="Extracts Weap_Pistol donor template, socket bounds, custom UPK smoke test & swing poses.",
    )
    build_mod(
        options=[_btn_run],
        commands=[_cmd_melee_asset_probe, _cmd_melee_probe_test_alias],
        hooks=[_swing_sampler_tick],
    )
    try:
        _swing_sampler_tick.disable()
    except Exception:
        pass
    _log("Armed and ready (type 'melee_asset_probe' or 'melee_probe_test').")
