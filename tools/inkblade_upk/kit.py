"""Reusable `Inkblade_Modular_Kit` (`r3` -> `r4` -> `r5` Bold) loader, socket deduplicator, and unified-atlas UPK compiler."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import re
import struct
from typing import Any
import zipfile

from PIL import Image, ImageChops

from .archive import UE3PackageArchive
from .codec import (
    CustomFragmentInput,
    PackedFragmentRange,
    SkeletalMeshBinary,
    Texture2DSpec,
    build_texture_upk,
    encode_pil_to_dxt_mips,
    make_flat_normal_spec,
    quat_rotate_vec,
    replace_with_custom_fragments,
)

EXPECTED_SCHEMA = "inkblade.socket-kit/1.0"

ELEMENT_NAMES: tuple[str, ...] = (
    "None",
    "Fire",
    "Shock",
    "Corrosive",
    "Slag",
    "Explosive",
)


@dataclass(frozen=True)
class WeaponFrameCalibration:
    """Maps Inkblade glTF meter coordinates `(gx, gy, gz)` into BL2 Weapon `Root` UU space.

    glTF part coordinates (right-handed, meters):
      - `+gx`: guard right
      - `+gy`: blade tip (up along the blade; grip extends in `-gy` down to `-0.50`)
      - `+gz`: display/front face (`-Y` in Blender working space)

    BL2 Weapon `Root` coordinates (left-handed, UU = cm):
      - `+X`: weapon right
      - `-Y`: weapon forward (muzzle / reach)
      - `+Z`: weapon up

    Default `det = -1` reflection basis (`basis_x=(-1,0,0)` or `(1,0,0)` with `basis_y`/`basis_z`):
    By default, held upright like a sword/katana in the Pistol grip hand:
      - glTF `+gx` (guard right) -> Root `+X` `(1, 0, 0)` (or tilted forward along `-Y` via pitch!)
      - glTF `+gy` (blade spine toward tip) -> pitched forward/up in `(0, -sin(pitch), cos(pitch))`
      - glTF `+gz` (display face) -> cross-reflected so `det(M) == -1.0` (preserving UE3 front-face winding!).
    """

    scale_uu_per_meter: float = 34.0
    # Calibrated from live Phase-3 probe (`Weap_Pistol` grip center `(0, +6.7, -5.5)` & `Arms` `"Weapon"` socket):
    # Places `gy = 0` (`SOCKET_BLADE`) at `(0, +3.2, +2.3)`, `gy = -0.25` palm center at `(0, +6.66, -5.46)`,
    # and `gy = -0.50` pommel at `(0, +10.11, -13.23)`, keeping 100% of blade + tip inside 90-deg 1P FOV.
    grip_origin_uu: tuple[float, float, float] = (0.0, 3.2, 2.3)
    # Forward pitch angle in degrees from vertical `+Z` toward muzzle `-Y` (`24°` natural right-hand sword hold)
    blade_forward_pitch_deg: float = 24.0
    # Yaw twist around blade axis in degrees (`90°` = `+gx` cutting edge forward `-Y`, `-gx` spine/cartridge toward camera)
    blade_yaw_deg: float = 90.0

    def matrix3x3(self) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
        """Return columns `(col_gx, col_gy, col_gz)` in BL2 Root space with `det < 0` (left-handed reflection)."""
        pitch = math.radians(self.blade_forward_pitch_deg)
        yaw = math.radians(self.blade_yaw_deg)

        # Blade spine unit axis (`+gy` in glTF): tilts from `+Z` toward `-Y` (forward)
        sp_x = 0.0
        sp_y = -math.sin(pitch)
        sp_z = math.cos(pitch)

        # Base forward-in-plane orthogonal axis (`pitch + 90°` toward `-Y`/`-Z`):
        fw_x = 0.0
        fw_y = -math.cos(pitch)
        fw_z = -math.sin(pitch)

        # Base right axis `+X`: `(1, 0, 0)`
        # Apply yaw around spine so `yaw=90°` puts glTF `+gx` (cutting edge / guard width) into forward plane `fw`!
        # Wait: let's check Inkblade blade cross-section in glTF!
        # In `generate_blender.py`: `working: +Z blade tip, -Y display/front face, +X guard right` (blade profile is in `(X, Z)`!).
        # That means in glTF: `+gx` is the blade's edge/back profile width, `+gy` is blade tip, and `+gz` is the thin flat face normal!
        # So when holding a sword in your right hand ready to slash forward (`-Y`):
        #   - Leading cutting edge (`+gx` in glTF) points FORWARD (`fw = (0, -cos(pitch), -sin(pitch))`) when `yaw = 90°`!
        #   - Flat side face (`+gz` in glTF) points RIGHT (`+X = (1, 0, 0)`) so `det([col_gx, col_gy, col_gz]) = -1`!
        # Let's parameterize cleanly for any `yaw`:
        c_yaw = math.cos(yaw)
        s_yaw = math.sin(yaw)
        # At yaw=0: col_gx = (1, 0, 0), col_gz = -fw = (0, cos(pitch), sin(pitch)) -> det([X, spine, -fw]) = -1!
        # At yaw=90°: col_gx = fw = (0, -cos(pitch), -sin(pitch)), col_gz = (1, 0, 0) -> det([fw, spine, X]):
        #   det([[0, 0, 1], [-cos p, -sin p, 0], [-sin p, cos p, 0]]) = 1 * (-cos^2 p - sin^2 p) = -1!!
        col_gx = (
            c_yaw * 1.0 + s_yaw * fw_x,
            s_yaw * fw_y,
            s_yaw * fw_z,
        )
        col_gy = (sp_x, sp_y, sp_z)
        col_gz = (
            s_yaw * 1.0 - c_yaw * fw_x,
            -c_yaw * fw_y,
            -c_yaw * fw_z,
        )
        return (col_gx, col_gy, col_gz)

    def map_point(self, gltf_pt: tuple[float, float, float]) -> tuple[float, float, float]:
        c0, c1, c2 = self.matrix3x3()
        gx, gy, gz = gltf_pt
        s = self.scale_uu_per_meter
        ox, oy, oz = self.grip_origin_uu
        return (
            ox + s * (c0[0] * gx + c1[0] * gy + c2[0] * gz),
            oy + s * (c0[1] * gx + c1[1] * gy + c2[1] * gz),
            oz + s * (c0[2] * gx + c1[2] * gy + c2[2] * gz),
        )

    def map_dir(self, gltf_dir: tuple[float, float, float]) -> tuple[float, float, float]:
        c0, c1, c2 = self.matrix3x3()
        gx, gy, gz = gltf_dir
        rx = c0[0] * gx + c1[0] * gy + c2[0] * gz
        ry = c0[1] * gx + c1[1] * gy + c2[1] * gz
        rz = c0[2] * gx + c1[2] * gy + c2[2] * gz
        ln = math.sqrt(rx * rx + ry * ry + rz * rz)
        if ln > 1e-8:
            return (rx / ln, ry / ln, rz / ln)
        return (0.0, 0.0, 1.0)


@dataclass
class GlbPartMesh:
    part_id: str
    slot: str
    manufacturer: str
    positions: list[tuple[float, float, float]]  # glTF meters (after any node transforms)
    normals: list[tuple[float, float, float]]
    tangents: list[tuple[float, float, float, float]]
    uvs: list[tuple[float, float]]  # Part-local [0..1] top-left UVs
    triangles: list[tuple[int, int, int]]
    triangle_channels: list[str]  # "SURFACE" or "ELEMENT_ACCENT" per triangle
    sockets_gltf_m: dict[str, tuple[float, float, float]]
    texture_rel_path: str
    uv_layout: dict[str, Any]
    bifacial_fit: bool = False
    vertex_face_sides: list[int] | None = None  # +1 FACE_FRONT, -1 FACE_BACK, 0 static


@dataclass(frozen=True)
class AtlasPlacement:
    part_id: str
    ox: int
    oy: int
    width: int
    height: int
    src_width: int
    src_height: int


@dataclass(frozen=True)
class BakedPartVariant:
    """One deduplicated `(part_id, socket_offset_gltf_m, face_gap_m)` Gestalt fragment."""

    part_id: str
    variant_index: int
    fragment_name: str
    slot: str
    socket_name: str
    offset_gltf_m: tuple[float, float, float]
    parent_part_ids: tuple[str, ...]
    face_gap_m: float = 0.0


class _KitReader:
    """Uniform file reader over a `.zip` archive, split `.zip1/.zip2` parts, or a filesystem directory."""

    _SPLIT_PART_RE = re.compile(r"^(.*?\.(?:zip|part|z))0*1(\..+)?$", re.IGNORECASE)

    def __init__(self, kit_path: str | Path) -> None:
        self.path = Path(kit_path)
        self._zf: zipfile.ZipFile | None = None
        self._prefix = ""
        if self.path.is_dir():
            if (self.path / "socket_manifest.json").exists():
                self._root_dir: Path | None = self.path
            else:
                subs = [d for d in self.path.iterdir() if d.is_dir() and (d / "socket_manifest.json").exists()]
                if not subs:
                    raise FileNotFoundError(f"No socket_manifest.json found in {self.path}")
                self._root_dir = subs[0]
        else:
            self._root_dir = None
            m_split = self._SPLIT_PART_RE.match(self.path.name)
            part2: Path | None = None
            if m_split is not None:
                stem_pre, ext_suf = m_split.group(1), (m_split.group(2) or "")
                cand2 = self.path.parent / f"{stem_pre}2{ext_suf}"
                if cand2.is_file():
                    part2 = cand2
            if part2 is not None and m_split is not None:
                stem_pre, ext_suf = m_split.group(1), (m_split.group(2) or "")
                joined = bytearray()
                idx = 1
                while True:
                    seg = self.path.parent / f"{stem_pre}{idx}{ext_suf}"
                    if not seg.is_file():
                        break
                    joined.extend(seg.read_bytes())
                    idx += 1
                self._zf = zipfile.ZipFile(io.BytesIO(bytes(joined)), "r")
            else:
                self._zf = zipfile.ZipFile(self.path, "r")
            manifest_entries = [n for n in self._zf.namelist() if n.endswith("socket_manifest.json")]
            if not manifest_entries:
                raise FileNotFoundError(f"No socket_manifest.json inside {self.path}")
            m_entry = sorted(manifest_entries, key=len)[0]
            self._prefix = m_entry[: -len("socket_manifest.json")]

    def read_bytes(self, rel_path: str) -> bytes:
        rel_clean = rel_path.lstrip("/")
        if self._root_dir is not None:
            return (self._root_dir / rel_clean).read_bytes()
        assert self._zf is not None
        return self._zf.read(self._prefix + rel_clean)

    def exists(self, rel_path: str) -> bool:
        rel_clean = rel_path.lstrip("/")
        if self._root_dir is not None:
            return (self._root_dir / rel_clean).exists()
        assert self._zf is not None
        try:
            self._zf.getinfo(self._prefix + rel_clean)
            return True
        except KeyError:
            return False

    def names(self) -> list[str]:
        """Every file in the kit (relative to `socket_manifest.json`), sorted."""
        if self._root_dir is not None:
            return sorted(
                p.relative_to(self._root_dir).as_posix()
                for p in self._root_dir.rglob("*")
                if p.is_file()
            )
        assert self._zf is not None
        return sorted(
            name[len(self._prefix):]
            for name in self._zf.namelist()
            if name.startswith(self._prefix) and not name.endswith("/")
        )

    def close(self) -> None:
        if self._zf is not None:
            self._zf.close()


#: Compiler inputs: texture PNGs, model GLBs, the top-level socket manifest, and per-texture UV
#: layouts. Generated profiling/docs JSON is intentionally excluded from the freshness hash.
KIT_FINGERPRINT_SUFFIXES: tuple[str, ...] = (".png", ".glb")


def _is_compiler_input(name: str) -> bool:
    lower = name.lower()
    return (
        lower.endswith(KIT_FINGERPRINT_SUFFIXES)
        or lower == "socket_manifest.json"
        or (lower.startswith("textures/") and lower.endswith("_uv_layout.json"))
    )


def kit_fingerprint(kit_path: str | Path) -> str:
    """Content hash of a kit's compiled inputs.

    The compiler is deterministic, so "has the kit changed since the last build?" is exactly "does
    this hash still match the one the last build recorded?".  `melee install`/`dev` use it to notice
    that the generator has repainted the atlases, which is otherwise invisible: the UPKs on disk stay
    valid files, they just hold the *old* textures, and the game quietly shows them.
    """
    reader = _KitReader(kit_path)
    try:
        hasher = hashlib.sha256()
        for name in reader.names():
            if not _is_compiler_input(name):
                continue
            payload = reader.read_bytes(name)
            # The generator socket manifest embeds profiling data for humans. Stage elapsed times
            # and cache status vary on every run but do not affect the compiled assets, so exclude
            # those fields from the content fingerprint while retaining stage names/source hashes.
            if Path(name).name == "socket_manifest.json":
                try:
                    manifest = json.loads(payload)
                    stages = manifest.get("generator", {}).get("stages", [])
                    for stage in stages:
                        if isinstance(stage, dict):
                            stage.pop("seconds", None)
                            stage.pop("status", None)
                    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
                    pass
            hasher.update(name.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(hashlib.sha256(payload).digest())
        return hasher.hexdigest()[:32]
    finally:
        reader.close()


def _read_glb_accessor(
    jdoc: dict[str, Any],
    bin_chunk: memoryview,
    acc_idx: int,
) -> list[tuple[float, ...]] | list[int]:
    acc = jdoc["accessors"][acc_idx]
    bv = jdoc["bufferViews"][acc["bufferView"]]
    base_off = int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    count = int(acc["count"])
    ctype = int(acc["componentType"])
    atype = str(acc["type"])

    comp_map = {
        5120: ("b", 1),
        5121: ("B", 1),
        5122: ("h", 2),
        5123: ("H", 2),
        5125: ("I", 4),
        5126: ("f", 4),
    }
    num_map = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    fmt_char, csize = comp_map[ctype]
    ncomp = num_map[atype]
    elem_bytes = csize * ncomp
    stride = int(bv.get("byteStride", elem_bytes))
    fmt = f"<{ncomp}{fmt_char}"

    out: list[Any] = []
    for i in range(count):
        vals = struct.unpack_from(fmt, bin_chunk, base_off + i * stride)
        out.append(vals[0] if ncomp == 1 else vals)
    return out


def _compute_fallback_tangents(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    uvs: list[tuple[float, float]],
    triangles: list[tuple[int, int, int]],
) -> list[tuple[float, float, float, float]]:
    tan1 = [[0.0, 0.0, 0.0] for _ in range(len(positions))]
    for i0, i1, i2 in triangles:
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        u0, u1, u2 = uvs[i0], uvs[i1], uvs[i2]
        dx1, dy1, dz1 = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        dx2, dy2, dz2 = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        du1, dv1 = u1[0] - u0[0], u1[1] - u0[1]
        du2, dv2 = u2[0] - u0[0], u2[1] - u0[1]
        denom = du1 * dv2 - du2 * dv1
        if abs(denom) > 1e-10:
            r = 1.0 / denom
            tx = (dv2 * dx1 - dv1 * dx2) * r
            ty = (dv2 * dy1 - dv1 * dy2) * r
            tz = (dv2 * dz1 - dv1 * dz2) * r
            for vi in (i0, i1, i2):
                tan1[vi][0] += tx
                tan1[vi][1] += ty
                tan1[vi][2] += tz

    result: list[tuple[float, float, float, float]] = []
    for (nx, ny, nz), (tx, ty, tz) in zip(normals, tan1, strict=False):
        # Gram-Schmidt orthogonalize against normal
        ndott = nx * tx + ny * ty + nz * tz
        ox = tx - nx * ndott
        oy = ty - ny * ndott
        oz = tz - nz * ndott
        ln = math.sqrt(ox * ox + oy * oy + oz * oz)
        if ln > 1e-8:
            result.append((ox / ln, oy / ln, oz / ln, 1.0))
        else:
            # Pick arbitrary orthogonal vector
            if abs(nx) < 0.9:
                cx, cy, cz = 0.0, nz, -ny
            else:
                cx, cy, cz = -nz, 0.0, nx
            cln = math.sqrt(cx * cx + cy * cy + cz * cz) or 1.0
            result.append((cx / cln, cy / cln, cz / cln, 1.0))
    return result


def _repair_outward_manifold_winding(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    uvs: list[tuple[float, float]],
    face_sides: list[int],
    triangles: list[tuple[int, int, int]],
    tri_channels: list[str],
    tri_double_sided: list[bool],
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[float, float, float]],
    list[tuple[float, float]],
    list[int],
    list[tuple[int, int, int]],
    list[str],
]:
    """Ensure every connected watertight 2-manifold shell winds strictly outward (`signed_volume > 0`).

    `Inkblade_Modular_Kit` (`r3`/`r4`) exports `.glb` materials with `doubleSided: true`, masking
    inward-wound rear bevels (`Geo.profile` `sign=+1`), Y-axis cylinders (`Geo.cyl(..., axis='y')`),
    and reflected 2D UV ear-clipping triangles (`r4_builder.py` `triangulate(coords)`). Because BL2's
    `Master_ClassMod` weapon/item shader uses single-sided D3D9 backface culling (`TwoSided=False`),
    inward-wound faces vanish on the back side of blades and rear of accessories unless repaired here.
    """
    if not triangles:
        return positions, normals, uvs, face_sides, triangles, tri_channels

    qpos = [
        (face_sides[i], round(p[0], 6), round(p[1], 6), round(p[2], 6))
        for i, p in enumerate(positions)
    ]
    edge_to_tris: dict[tuple[Any, Any], list[tuple[int, int]]] = defaultdict(list)
    for ti, (a, b, c) in enumerate(triangles):
        qa, qb, qc = qpos[a], qpos[b], qpos[c]
        for u, v in ((qa, qb), (qb, qc), (qc, qa)):
            if u == v:
                continue
            if u < v:
                edge_to_tris[(u, v)].append((ti, 1))
            else:
                edge_to_tris[(v, u)].append((ti, -1))

    visited = [False] * len(triangles)
    flip = [False] * len(triangles)
    duplicate_backface = [False] * len(triangles)

    for start in range(len(triangles)):
        if visited[start]:
            continue
        comp: list[int] = []
        has_open_boundary = False
        queue: deque[int] = deque([start])
        visited[start] = True
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            a, b, c = triangles[cur]
            qa, qb, qc = qpos[a], qpos[b], qpos[c]
            for u, v in ((qa, qb), (qb, qc), (qc, qa)):
                if u == v:
                    continue
                key = (u, v) if u < v else (v, u)
                orig_sgn = 1 if u < v else -1
                my_sgn = -orig_sgn if flip[cur] else orig_sgn
                peers = edge_to_tris[key]
                if len(peers) == 1:
                    has_open_boundary = True
                    continue
                if len(peers) != 2:
                    continue
                for nb, nb_orig_sgn in peers:
                    if nb == cur:
                        continue
                    want_flip = nb_orig_sgn == my_sgn
                    if not visited[nb]:
                        visited[nb] = True
                        flip[nb] = want_flip
                        queue.append(nb)

        cx = sum(positions[v][0] for ti in comp for v in triangles[ti]) / (3.0 * len(comp))
        cy = sum(positions[v][1] for ti in comp for v in triangles[ti]) / (3.0 * len(comp))
        cz = sum(positions[v][2] for ti in comp for v in triangles[ti]) / (3.0 * len(comp))
        vol = 0.0
        for ti in comp:
            i0, i1, i2 = triangles[ti]
            if flip[ti]:
                i1, i2 = i2, i1
            p0 = (positions[i0][0] - cx, positions[i0][1] - cy, positions[i0][2] - cz)
            p1 = (positions[i1][0] - cx, positions[i1][1] - cy, positions[i1][2] - cz)
            p2 = (positions[i2][0] - cx, positions[i2][1] - cy, positions[i2][2] - cz)
            vol += (
                p0[0] * (p1[1] * p2[2] - p1[2] * p2[1])
                - p0[1] * (p1[0] * p2[2] - p1[2] * p2[0])
                + p0[2] * (p1[0] * p2[1] - p1[1] * p2[0])
            ) / 6.0

        if not has_open_boundary and abs(vol) > 1e-10:
            if vol < 0.0:
                for ti in comp:
                    flip[ti] = not flip[ti]
        else:
            # Open thin sheet fallback: emit explicit backfaces if glTF material was doubleSided
            for ti in comp:
                if tri_double_sided[ti]:
                    duplicate_backface[ti] = True

    out_pos: list[tuple[float, float, float]] = []
    out_nrm: list[tuple[float, float, float]] = []
    out_uv: list[tuple[float, float]] = []
    out_sides: list[int] = []
    out_tris: list[tuple[int, int, int]] = []
    out_channels: list[str] = []

    for ti, (a, b, c) in enumerate(triangles):
        i0, i1, i2 = (a, c, b) if flip[ti] else (a, b, c)
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        fnx = e1[1] * e2[2] - e1[2] * e2[1]
        fny = e1[2] * e2[0] - e1[0] * e2[2]
        fnz = e1[0] * e2[1] - e1[1] * e2[0]
        fn_len = math.sqrt(fnx * fnx + fny * fny + fnz * fnz)
        geom_n = (fnx / fn_len, fny / fn_len, fnz / fn_len) if fn_len > 1e-11 else None

        base = len(out_pos)
        for vi in (i0, i1, i2):
            out_pos.append(positions[vi])
            vn = normals[vi]
            if geom_n is not None:
                # Preserve smooth shading if authored normal already agrees with outward face normal,
                # otherwise align to exact outward face normal (fixing inverted/mirrored normals).
                if vn[0] * geom_n[0] + vn[1] * geom_n[1] + vn[2] * geom_n[2] < 0.0:
                    vn = (-vn[0], -vn[1], -vn[2])
                if vn[0] * geom_n[0] + vn[1] * geom_n[1] + vn[2] * geom_n[2] < 0.25:
                    vn = geom_n
            out_nrm.append(vn)
            out_uv.append(uvs[vi])
            out_sides.append(face_sides[vi])
        out_tris.append((base, base + 1, base + 2))
        out_channels.append(tri_channels[ti])

        if duplicate_backface[ti]:
            bbase = len(out_pos)
            for vi in (i0, i2, i1):
                out_pos.append(positions[vi])
                vn = out_nrm[base]
                out_nrm.append((-vn[0], -vn[1], -vn[2]))
                out_uv.append(uvs[vi])
                out_sides.append(face_sides[vi])
            out_tris.append((bbase, bbase + 1, bbase + 2))
            out_channels.append(tri_channels[ti])

    return out_pos, out_nrm, out_uv, out_sides, out_tris, out_channels


def parse_glb_part_bytes(
    glb_bytes: bytes,
    part_id: str,
    slot: str,
    manufacturer: str,
    sockets_gltf_m: dict[str, tuple[float, float, float]],
    texture_rel_path: str,
    uv_layout: dict[str, Any],
    bifacial_fit: bool = False,
) -> GlbPartMesh:
    magic, version, _total_len = struct.unpack_from("<III", glb_bytes, 0)
    if magic != 0x46546C67 or version != 2:
        raise ValueError(f"Invalid glTF 2.0 binary header for {part_id}")
    jlen, jtype = struct.unpack_from("<II", glb_bytes, 12)
    if jtype != 0x4E4F534A:
        raise ValueError(f"Missing JSON chunk in {part_id}")
    jdoc = json.loads(glb_bytes[20 : 20 + jlen].decode("utf-8"))
    bin_hdr_off = 20 + jlen
    blen, btype = struct.unpack_from("<II", glb_bytes, bin_hdr_off)
    if btype != 0x004E4942:
        raise ValueError(f"Missing BIN chunk in {part_id}")
    bin_mv = memoryview(glb_bytes)[bin_hdr_off + 8 : bin_hdr_off + 8 + blen]

    materials = jdoc.get("materials", [])
    nodes = jdoc.get("nodes", [])
    meshes = jdoc.get("meshes", [])

    # Accumulate node world translations/rotations/scales from scene roots
    scene_idx = int(jdoc.get("scene", 0))
    root_nodes = jdoc.get("scenes", [{"nodes": list(range(len(nodes)))}])[scene_idx].get("nodes", [])

    all_pos: list[tuple[float, float, float]] = []
    all_nrm: list[tuple[float, float, float]] = []
    all_tan: list[tuple[float, float, float, float]] = []
    all_uv: list[tuple[float, float]] = []
    all_face_sides: list[int] = []
    all_tris: list[tuple[int, int, int]] = []
    all_tri_channels: list[str] = []
    all_tri_double_sided: list[bool] = []

    def _visit_node(
        n_idx: int,
        p_tr: tuple[float, float, float],
        p_rot: tuple[float, float, float, float],
        p_sc: tuple[float, float, float],
        p_face_side: int = 0,
    ) -> None:
        nd = nodes[n_idx]
        nd_name = str(nd.get("name", ""))
        face_side = 1 if nd_name == "FACE_FRONT" else (-1 if nd_name == "FACE_BACK" else p_face_side)

        lt = tuple(float(x) for x in nd.get("translation", (0.0, 0.0, 0.0)))
        lr = tuple(float(x) for x in nd.get("rotation", (0.0, 0.0, 0.0, 1.0)))
        ls = tuple(float(x) for x in nd.get("scale", (1.0, 1.0, 1.0)))

        scaled_lt = (lt[0] * p_sc[0], lt[1] * p_sc[1], lt[2] * p_sc[2])
        rot_lt = quat_rotate_vec(p_rot, scaled_lt)
        w_tr = (p_tr[0] + rot_lt[0], p_tr[1] + rot_lt[1], p_tr[2] + rot_lt[2])
        ax, ay, az, aw = p_rot
        bx, by, bz, bw = lr
        w_rot = (
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )
        w_sc = (p_sc[0] * ls[0], p_sc[1] * ls[1], p_sc[2] * ls[2])
        neg_det = (w_sc[0] * w_sc[1] * w_sc[2]) < 0.0

        if "mesh" in nd:
            mdef = meshes[int(nd["mesh"])]
            for prim in mdef.get("primitives", []):
                attrs = prim["attributes"]
                raw_pos = _read_glb_accessor(jdoc, bin_mv, int(attrs["POSITION"]))
                raw_nrm = (
                    _read_glb_accessor(jdoc, bin_mv, int(attrs["NORMAL"]))
                    if "NORMAL" in attrs
                    else [(0.0, 1.0, 0.0)] * len(raw_pos)
                )
                raw_uv = (
                    _read_glb_accessor(jdoc, bin_mv, int(attrs["TEXCOORD_0"]))
                    if "TEXCOORD_0" in attrs
                    else [(0.0, 0.0)] * len(raw_pos)
                )
                if "indices" in prim:
                    flat_idx = [int(x) for x in _read_glb_accessor(jdoc, bin_mv, int(prim["indices"]))]
                else:
                    flat_idx = list(range(len(raw_pos)))

                prim_tris = [
                    (flat_idx[k], flat_idx[k + 2], flat_idx[k + 1])
                    if neg_det
                    else (flat_idx[k], flat_idx[k + 1], flat_idx[k + 2])
                    for k in range(0, len(flat_idx), 3)
                ]

                inv_sx = 1.0 / w_sc[0] if abs(w_sc[0]) > 1e-8 else 1.0
                inv_sy = 1.0 / w_sc[1] if abs(w_sc[1]) > 1e-8 else 1.0
                inv_sz = 1.0 / w_sc[2] if abs(w_sc[2]) > 1e-8 else 1.0

                tf_pos: list[tuple[float, float, float]] = []
                tf_nrm: list[tuple[float, float, float]] = []
                for p_xyz, n_xyz in zip(raw_pos, raw_nrm, strict=False):
                    sp = (float(p_xyz[0]) * w_sc[0], float(p_xyz[1]) * w_sc[1], float(p_xyz[2]) * w_sc[2])
                    rp = quat_rotate_vec(w_rot, sp)
                    tf_pos.append((w_tr[0] + rp[0], w_tr[1] + rp[1], w_tr[2] + rp[2]))
                    sn = (float(n_xyz[0]) * inv_sx, float(n_xyz[1]) * inv_sy, float(n_xyz[2]) * inv_sz)
                    rn = quat_rotate_vec(w_rot, sn)
                    nlen = math.sqrt(rn[0] ** 2 + rn[1] ** 2 + rn[2] ** 2) or 1.0
                    tf_nrm.append((rn[0] / nlen, rn[1] / nlen, rn[2] / nlen))

                tf_uv = [(float(u[0]), float(u[1])) for u in raw_uv]
                if "TANGENT" in attrs and not neg_det:
                    raw_tan = _read_glb_accessor(jdoc, bin_mv, int(attrs["TANGENT"]))
                    tf_tan: list[tuple[float, float, float, float]] = []
                    for t_xyzw in raw_tan:
                        rt = quat_rotate_vec(w_rot, (float(t_xyzw[0]), float(t_xyzw[1]), float(t_xyzw[2])))
                        tlen = math.sqrt(rt[0] ** 2 + rt[1] ** 2 + rt[2] ** 2) or 1.0
                        tf_tan.append((rt[0] / tlen, rt[1] / tlen, rt[2] / tlen, float(t_xyzw[3])))
                else:
                    tf_tan = _compute_fallback_tangents(tf_pos, tf_nrm, tf_uv, prim_tris)

                channel = "SURFACE"
                is_double_sided = False
                mat_idx = prim.get("material")
                if mat_idx is not None and 0 <= int(mat_idx) < len(materials):
                    mobj = materials[int(mat_idx)]
                    is_double_sided = bool(mobj.get("doubleSided", False))
                    extras = mobj.get("extras") or {}
                    mname = str(mobj.get("name", ""))
                    if extras.get("channel") == "ELEMENT_ACCENT" or "ELEMENT_ACCENT" in mname:
                        channel = "ELEMENT_ACCENT"

                v_base = len(all_pos)
                all_pos.extend(tf_pos)
                all_nrm.extend(tf_nrm)
                all_tan.extend(tf_tan)
                all_uv.extend(tf_uv)
                all_face_sides.extend([face_side] * len(tf_pos))
                for i0, i1, i2 in prim_tris:
                    all_tris.append((v_base + i0, v_base + i1, v_base + i2))
                    all_tri_channels.append(channel)
                    all_tri_double_sided.append(is_double_sided)

        for ch_idx in nd.get("children", []):
            _visit_node(int(ch_idx), w_tr, w_rot, w_sc, face_side)

    for rn in root_nodes:
        _visit_node(int(rn), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), 0)

    all_pos, all_nrm, all_uv, all_face_sides, all_tris, all_tri_channels = (
        _repair_outward_manifold_winding(
            all_pos,
            all_nrm,
            all_uv,
            all_face_sides,
            all_tris,
            all_tri_channels,
            all_tri_double_sided,
        )
    )
    all_tan = _compute_fallback_tangents(all_pos, all_nrm, all_uv, all_tris)

    # Lossless vertex weld for non-indexed triangle soups (cuts r4 GPU vertices by ~35% while keeping flat normals & UV seams)
    weld_map: dict[tuple[Any, ...], int] = {}
    w_pos: list[tuple[float, float, float]] = []
    w_nrm: list[tuple[float, float, float]] = []
    w_tan: list[tuple[float, float, float, float]] = []
    w_uv: list[tuple[float, float]] = []
    w_sides: list[int] = []
    remap: list[int] = []
    for p_v, n_v, t_v, u_v, s_v in zip(all_pos, all_nrm, all_tan, all_uv, all_face_sides, strict=False):
        wkey = (
            round(p_v[0], 6),
            round(p_v[1], 6),
            round(p_v[2], 6),
            round(n_v[0], 4),
            round(n_v[1], 4),
            round(n_v[2], 4),
            round(u_v[0], 5),
            round(u_v[1], 5),
            s_v,
        )
        widx = weld_map.get(wkey)
        if widx is None:
            widx = len(w_pos)
            weld_map[wkey] = widx
            w_pos.append(p_v)
            w_nrm.append(n_v)
            w_tan.append(t_v)
            w_uv.append(u_v)
            w_sides.append(s_v)
        remap.append(widx)
    w_tris = [(remap[i0], remap[i1], remap[i2]) for i0, i1, i2 in all_tris]

    has_bifacial_nodes = any(s != 0 for s in w_sides)
    return GlbPartMesh(
        part_id=part_id,
        slot=slot,
        manufacturer=manufacturer,
        positions=w_pos,
        normals=w_nrm,
        tangents=w_tan,
        uvs=w_uv,
        triangles=w_tris,
        triangle_channels=all_tri_channels,
        sockets_gltf_m=sockets_gltf_m,
        texture_rel_path=texture_rel_path,
        uv_layout=uv_layout,
        bifacial_fit=bool(bifacial_fit or has_bifacial_nodes),
        vertex_face_sides=w_sides,
    )


class InkbladeModularKit:
    """Loaded representation of `Inkblade_Modular_Kit` (`r3`, `r4`, or later revisions)."""

    def __init__(self, kit_path: str | Path) -> None:
        self._reader = _KitReader(kit_path)
        try:
            self.manifest: dict[str, Any] = json.loads(
                self._reader.read_bytes("socket_manifest.json").decode("utf-8")
            )
            schema = self.manifest.get("schema")
            if schema != EXPECTED_SCHEMA:
                raise ValueError(f"Unsupported socket_manifest schema {schema!r} (expected {EXPECTED_SCHEMA!r})")
            self.revision: str = str(self.manifest.get("revision", "unknown"))
            self.element_hex: dict[str, str] = dict(self.manifest.get("elements", {}))
            self.parts: dict[str, GlbPartMesh] = {}
            self.part_images_rgb: dict[str, Image.Image] = {}

            for part_id, pmeta in self.manifest["parts"].items():
                socks_raw = pmeta.get("sockets_gltf_m") or {}
                socks = {
                    sname: (float(xyz[0]), float(xyz[1]), float(xyz[2]))
                    for sname, xyz in socks_raw.items()
                }
                tex_rel = str(pmeta["texture"])
                uv_rel = str(pmeta.get("uv_layout") or tex_rel.replace("_basecolor.png", "_uv_layout.json"))
                uv_layout = (
                    json.loads(self._reader.read_bytes(uv_rel).decode("utf-8"))
                    if self._reader.exists(uv_rel)
                    else {"islands": []}
                )
                glb_bytes = self._reader.read_bytes(str(pmeta["file"]))
                self.parts[part_id] = parse_glb_part_bytes(
                    glb_bytes=glb_bytes,
                    part_id=part_id,
                    slot=str(pmeta["slot"]),
                    manufacturer=str(pmeta.get("manufacturer", "Universal")),
                    sockets_gltf_m=socks,
                    texture_rel_path=tex_rel,
                    uv_layout=uv_layout,
                    bifacial_fit=bool(pmeta.get("bifacial_fit", False)),
                )
                im = Image.open(io.BytesIO(self._reader.read_bytes(tex_rel))).convert("RGB")
                self.part_images_rgb[part_id] = im
        finally:
            self._reader.close()

    @staticmethod
    def _round_vec(v: tuple[float, float, float]) -> tuple[float, float, float]:
        return (round(v[0], 5), round(v[1], 5), round(v[2], 5))

    @staticmethod
    def _sub_vec(
        a: tuple[float, float, float], b: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def plan_socket_baked_variants(self) -> list[BakedPartVariant]:
        """Deduplicate `(part_id, rounded_socket_offset, face_gap_m)` across all blades & grips."""
        blades = [p for p in self.parts.values() if p.slot == "BarrelPartDefinition"]
        grips = [p for p in self.parts.values() if p.slot == "GripPartDefinition"]
        raw_parts_meta = self.manifest.get("parts") or {}

        variants: list[BakedPartVariant] = []
        for pid, part in self.parts.items():
            mount = part.sockets_gltf_m.get("MOUNT", (0.0, 0.0, 0.0))
            if part.slot == "BarrelPartDefinition":
                off = self._round_vec(self._sub_vec((0.0, 0.0, 0.0), mount))
                variants.append(
                    BakedPartVariant(
                        part_id=pid,
                        variant_index=0,
                        fragment_name=f"Ink_{pid}",
                        slot=part.slot,
                        socket_name="ROOT",
                        offset_gltf_m=off,
                        parent_part_ids=(pid,),
                        face_gap_m=0.0,
                    )
                )
            elif part.slot == "GripPartDefinition":
                by_key: dict[tuple[tuple[float, float, float], float], list[str]] = {}
                for bl in blades:
                    sock = bl.sockets_gltf_m.get("SOCKET_GRIP", (0.0, 0.0, 0.0))
                    off = self._round_vec(self._sub_vec(sock, mount))
                    by_key.setdefault((off, 0.0), []).append(bl.part_id)
                for v_idx, ((off, fgap), parents) in enumerate(by_key.items()):
                    fname = f"Ink_{pid}" if v_idx == 0 else f"Ink_{pid}__s{v_idx}"
                    variants.append(
                        BakedPartVariant(
                            part_id=pid,
                            variant_index=v_idx,
                            fragment_name=fname,
                            slot=part.slot,
                            socket_name="SOCKET_GRIP",
                            offset_gltf_m=off,
                            parent_part_ids=tuple(parents),
                            face_gap_m=fgap,
                        )
                    )
            elif part.slot == "Accessory1PartDefinition":
                pmeta = raw_parts_meta.get(pid) or {}
                target_sock = str(pmeta.get("target_socket") or ("SOCKET_POMMEL" if "pommel" in pid.lower() else "SOCKET_ACCESSORY"))
                is_pommel = target_sock == "SOCKET_POMMEL" or "pommel" in pid.lower()
                by_key = {}
                if is_pommel:
                    for bl in blades:
                        b_grip = bl.sockets_gltf_m.get("SOCKET_GRIP", (0.0, 0.0, 0.0))
                        for gr in grips:
                            g_mount = gr.sockets_gltf_m.get("MOUNT", (0.0, 0.0, 0.0))
                            g_pom = gr.sockets_gltf_m.get("SOCKET_POMMEL", (0.0, -0.5, 0.0))
                            world_pom = (
                                b_grip[0] - g_mount[0] + g_pom[0],
                                b_grip[1] - g_mount[1] + g_pom[1],
                                b_grip[2] - g_mount[2] + g_pom[2],
                            )
                            off = self._round_vec(self._sub_vec(world_pom, mount))
                            by_key.setdefault((off, 0.0), []).append(f"{bl.part_id}+{gr.part_id}")
                    sock_label = "SOCKET_POMMEL"
                else:
                    for bl in blades:
                        sock = bl.sockets_gltf_m.get("SOCKET_ACCESSORY", (0.0, 0.16, 0.0))
                        off = self._round_vec(self._sub_vec(sock, mount))
                        fgap = (
                            round(float((raw_parts_meta.get(bl.part_id) or {}).get("accessory_face_gap_m", 0.0)), 6)
                            if part.bifacial_fit
                            else 0.0
                        )
                        by_key.setdefault((off, fgap), []).append(bl.part_id)
                    sock_label = "SOCKET_ACCESSORY"

                for v_idx, ((off, fgap), parents) in enumerate(by_key.items()):
                    fname = f"Ink_{pid}" if v_idx == 0 else f"Ink_{pid}__s{v_idx}"
                    variants.append(
                        BakedPartVariant(
                            part_id=pid,
                            variant_index=v_idx,
                            fragment_name=fname,
                            slot=part.slot,
                            socket_name=sock_label,
                            offset_gltf_m=off,
                            parent_part_ids=tuple(parents),
                            face_gap_m=fgap,
                        )
                    )
            elif part.slot == "ElementalPartDefinition":
                by_key = {}
                for bl in blades:
                    sock = bl.sockets_gltf_m.get("SOCKET_ELEMENT", (-0.065, 0.12, 0.0))
                    off = self._round_vec(self._sub_vec(sock, mount))
                    fgap = (
                        round(float((raw_parts_meta.get(bl.part_id) or {}).get("element_face_gap_m", 0.0)), 6)
                        if part.bifacial_fit
                        else 0.0
                    )
                    by_key.setdefault((off, fgap), []).append(bl.part_id)
                for v_idx, ((off, fgap), parents) in enumerate(by_key.items()):
                    fname = f"Ink_{pid}" if v_idx == 0 else f"Ink_{pid}__s{v_idx}"
                    variants.append(
                        BakedPartVariant(
                            part_id=pid,
                            variant_index=v_idx,
                            fragment_name=fname,
                            slot=part.slot,
                            socket_name="SOCKET_ELEMENT",
                            offset_gltf_m=off,
                            parent_part_ids=tuple(parents),
                            face_gap_m=fgap,
                        )
                    )
        return variants

    _SLOT_PRIORITY: dict[str, int] = {
        "BarrelPartDefinition": 3,
        "GripPartDefinition": 2,
        "Accessory1PartDefinition": 1,
        "ElementalPartDefinition": 0,
    }
    _SLOT_TEXEL_WEIGHT: dict[str, float] = {
        "BarrelPartDefinition": 1.40,
        "GripPartDefinition": 1.25,
        "Accessory1PartDefinition": 1.15,
        "ElementalPartDefinition": 0.30,
    }

    def pack_atlas_layout(self, atlas_size: int = 2048) -> dict[str, AtlasPlacement]:
        """Pack all 22 part textures into a single `atlas_size x atlas_size` square with 16px DXT-safe alignment.

        Uses MaxRects Best-Short-Side-Fit 2D bin packing with slot-weighted texel-density halving so
        1.4m hero blades (`blade_bandit`) and grips (`grip_bandit`, `grip_hyperion`) keep full 512px
        tiles at `atlas_size=2048` rather than starving behind 5cm elemental cartridges.
        """
        if (atlas_size & (atlas_size - 1)) != 0 or atlas_size < 512:
            raise ValueError(f"atlas_size must be power-of-two >= 512, got {atlas_size}")

        dims: dict[str, tuple[int, int]] = {}
        max_tile = atlas_size // 2
        for pid, im in self.part_images_rgb.items():
            w, h = im.size
            while w > max_tile or h > max_tile:
                w = max(16, w // 2)
                h = max(16, h // 2)
            dims[pid] = (w, h)

        def _try_maxrects() -> dict[str, AtlasPlacement] | None:
            free: list[tuple[int, int, int, int]] = [(0, 0, atlas_size, atlas_size)]
            ordered = sorted(
                dims.items(),
                key=lambda kv: (max(kv[1]), kv[1][0] * kv[1][1], kv[1][1], kv[1][0], kv[0]),
                reverse=True,
            )
            out: dict[str, AtlasPlacement] = {}
            for pid, (w, h) in ordered:
                w16 = max(16, ((w + 15) // 16) * 16)
                h16 = max(16, ((h + 15) // 16) * 16)
                best: tuple[tuple[int, int, int, int], int, int, int, int] | None = None
                for fx, fy, fw, fh in free:
                    if fw >= w16 and fh >= h16:
                        score = (min(fw - w16, fh - h16), max(fw - w16, fh - h16), fy, fx)
                        if best is None or score < best[0]:
                            best = (score, fx, fy, w16, h16)
                if best is None:
                    return None
                _, bx, by, bw, bh = best
                src_w, src_h = self.part_images_rgb[pid].size
                out[pid] = AtlasPlacement(
                    part_id=pid,
                    ox=bx,
                    oy=by,
                    width=w,
                    height=h,
                    src_width=src_w,
                    src_height=src_h,
                )
                split_free: list[tuple[int, int, int, int]] = []
                for fx, fy, fw, fh in free:
                    if bx >= fx + fw or bx + bw <= fx or by >= fy + fh or by + bh <= fy:
                        split_free.append((fx, fy, fw, fh))
                        continue
                    if bx > fx:
                        split_free.append((fx, fy, bx - fx, fh))
                    if bx + bw < fx + fw:
                        split_free.append((bx + bw, fy, (fx + fw) - (bx + bw), fh))
                    if by > fy:
                        split_free.append((fx, fy, fw, by - fy))
                    if by + bh < fy + fh:
                        split_free.append((fx, by + bh, fw, (fy + fh) - (by + bh)))
                free = [
                    r
                    for i, r in enumerate(split_free)
                    if not any(
                        i != j
                        and r[0] >= c[0]
                        and r[1] >= c[1]
                        and r[0] + r[2] <= c[0] + c[2]
                        and r[1] + r[3] <= c[1] + c[3]
                        for j, c in enumerate(split_free)
                    )
                ]
            return out

        for _ in range(128):
            placed = _try_maxrects()
            if placed is not None:
                return placed
            candidates = [
                pid
                for pid, (w, h) in dims.items()
                if w * h > 32 * 32 and w >= 32 and h >= 32
            ]
            if not candidates:
                break
            victim = max(
                candidates,
                key=lambda pid: (
                    round(
                        (dims[pid][0] * dims[pid][1])
                        * (dims[pid][0] / self.part_images_rgb[pid].size[0])
                        / self._SLOT_TEXEL_WEIGHT.get(self.parts[pid].slot, 1.0),
                        4,
                    ),
                    -self._SLOT_PRIORITY.get(self.parts[pid].slot, 1),
                    pid,
                ),
            )
            vw, vh = dims[victim]
            dims[victim] = (max(16, vw // 2), max(16, vh // 2))

        raise RuntimeError(f"Could not pack {len(dims)} part textures into {atlas_size}x{atlas_size}")

    @staticmethod
    def _hex_to_unit_rgb(hex_color: str) -> tuple[float, float, float]:
        h = hex_color.lstrip("#")
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)

    def _recolor_part_for_element(self, part_id: str, element_name: str) -> Image.Image:
        """Recolor `ELEMENT_ACCENT` UV islands by max-channel brightness while preserving bold charcoal ink & `SURFACE`."""
        base = self.part_images_rgb[part_id].copy()
        if element_name == "None" or element_name not in self.element_hex:
            return base

        part = self.parts[part_id]
        islands = part.uv_layout.get("islands") or []
        accent_rects = [isl["rect_px"] for isl in islands if isl.get("role") == "accent"]
        if not accent_rects and "ELEMENT_ACCENT" not in part.triangle_channels:
            return base

        cr, cg, cb = self._hex_to_unit_rgb(self.element_hex[element_name])
        if accent_rects:
            # Pure-Pillow LUT ramp: preserve deep charcoal ink (lum <= 26/255) and smoothly blend
            # anti-aliased ink edges (26..61) into full max-channel element color (lum >= 61/255).
            alpha_lut = [
                0 if v <= 26 else (255 if v >= 61 else round((v - 26) * 255.0 / 35.0))
                for v in range(256)
            ]
            r_lut = [min(255, round(v * cr)) for v in range(256)]
            g_lut = [min(255, round(v * cg)) for v in range(256)]
            b_lut = [min(255, round(v * cb)) for v in range(256)]
            for rx, ry, rw, rh in accent_rects:
                crop = base.crop((rx, ry, rx + rw, ry + rh))
                rch, gch, bch = crop.split()
                lum = ImageChops.lighter(ImageChops.lighter(rch, gch), bch)
                tinted = Image.merge("RGB", (lum.point(r_lut), lum.point(g_lut), lum.point(b_lut)))
                mask_l = lum.point(alpha_lut)
                base.paste(Image.composite(tinted, crop, mask_l), (rx, ry))
        return base

    def build_element_atlases(self, atlas_size: int = 2048) -> tuple[dict[str, AtlasPlacement], dict[str, Image.Image]]:
        """Render the 6 unified-atlas `RGB` images (`None`, `Fire`, `Shock`, `Corrosive`, `Slag`, `Explosive`)."""
        placements = self.pack_atlas_layout(atlas_size=atlas_size)
        atlases: dict[str, Image.Image] = {}
        for elem_name in ELEMENT_NAMES:
            canvas = Image.new("RGB", (atlas_size, atlas_size), (13, 17, 23))
            for pid, pl in placements.items():
                part_img = self._recolor_part_for_element(pid, elem_name)
                if part_img.size != (pl.width, pl.height):
                    part_img = part_img.resize((pl.width, pl.height), Image.Resampling.LANCZOS)
                canvas.paste(part_img, (pl.ox, pl.oy))
            atlases[elem_name] = canvas
        return placements, atlases

    def build_gestalt_fragments(
        self,
        placements: dict[str, AtlasPlacement],
        atlas_size: int,
        calibration: WeaponFrameCalibration,
        ref_bone_index: int = 0,
    ) -> tuple[list[BakedPartVariant], list[CustomFragmentInput]]:
        """Transform all socket-deduplicated part meshes into BL2 Weapon reference-pose UU space with unified-atlas UVs."""
        variants = self.plan_socket_baked_variants()
        frag_inputs: list[CustomFragmentInput] = []

        for var in variants:
            part = self.parts[var.part_id]
            pl = placements[var.part_id]
            ox_m, oy_m, oz_m = var.offset_gltf_m
            face_sides = part.vertex_face_sides or ([0] * len(part.positions))

            pos_uu: list[tuple[float, float, float]] = []
            nrm_uu: list[tuple[float, float, float]] = []
            tan_uu: list[tuple[float, float, float, float]] = []
            remapped_uvs: list[tuple[float, float]] = []

            for p_xyz, n_xyz, t_xyzw, uv, fside in zip(
                part.positions, part.normals, part.tangents, part.uvs, face_sides, strict=False
            ):
                world_gltf = (
                    p_xyz[0] + ox_m,
                    p_xyz[1] + oy_m,
                    p_xyz[2] + oz_m + (var.face_gap_m * float(fside)),
                )
                pos_uu.append(calibration.map_point(world_gltf))
                nrm_uu.append(calibration.map_dir(n_xyz))
                td = calibration.map_dir((t_xyzw[0], t_xyzw[1], t_xyzw[2]))
                # Reflection `det=-1` flips binormal handedness sign `w`
                tan_uu.append((td[0], td[1], td[2], -t_xyzw[3]))
                u_atlas = (pl.ox + uv[0] * pl.width) / float(atlas_size)
                v_atlas = (pl.oy + uv[1] * pl.height) / float(atlas_size)
                remapped_uvs.append((u_atlas, v_atlas))

            frag_inputs.append(
                CustomFragmentInput(
                    name=var.fragment_name,
                    positions=pos_uu,
                    normals=nrm_uu,
                    tangents=tan_uu,
                    uvs=remapped_uvs,
                    triangles=part.triangles,
                    ref_bone_index=ref_bone_index,
                )
            )
        return variants, frag_inputs


def compile_inkblade_kit_to_upks(
    kit_path: str | Path,
    donor_upk_path_or_bytes: str | Path | bytes,
    out_meshes_upk: str | Path,
    out_textures_upk: str | Path,
    out_manifest_json: str | Path,
    mesh_export_name: str = "InkbladeGestaltMesh",
    atlas_size: int = 2048,
    calibration: WeaponFrameCalibration | None = None,
) -> dict[str, Any]:
    """End-to-end `r3`/`r4` compiler: `.glb` + `.png` + `socket_manifest.json` -> cooked BL2 `.upk`s + runtime JSON."""
    cal = calibration or WeaponFrameCalibration()
    kit = InkbladeModularKit(kit_path)
    placements, atlases = kit.build_element_atlases(atlas_size=atlas_size)

    with UE3PackageArchive(donor_upk_path_or_bytes) as donor_ar:
        sk_exp = next(e for e in donor_ar.exports if donor_ar.export_class_name(e) == "SkeletalMesh")
        skel = SkeletalMeshBinary.from_archive_export(donor_ar, sk_exp)

    # Weight rigid sword geometry to `WeaponOffset` (`bone 1` on `Weap_Pistol`, parent of all 17 weapon bones)
    # when present so 1st-person weapon AnimSets move the sword cleanly; fall back to `0` (`Root`) on minimal donors.
    ref_bone_idx = next(
        (i for i, b in enumerate(skel.ref_skeleton) if b.name == "WeaponOffset"),
        0,
    )
    ref_bone_name = skel.ref_skeleton[ref_bone_idx].name if skel.ref_skeleton else "Root"

    variants, frag_inputs = kit.build_gestalt_fragments(
        placements=placements,
        atlas_size=atlas_size,
        calibration=cal,
        ref_bone_index=ref_bone_idx,
    )

    packed_ranges: list[PackedFragmentRange] = replace_with_custom_fragments(
        skel,
        frag_inputs,
        keep_existing=False,
    )
    meshes_upk_bytes = skel.to_package_bytes(
        mesh_export_name,
        guid_seed=f"MeleeArsenalMeshes:{kit.revision}",
    )
    Path(out_meshes_upk).parent.mkdir(parents=True, exist_ok=True)
    Path(out_meshes_upk).write_bytes(meshes_upk_bytes)

    tex_specs: list[Texture2DSpec] = []
    for elem_name, atlas_im in atlases.items():
        tex_specs.append(
            encode_pil_to_dxt_mips(
                atlas_im,
                name=f"T_Inkblade_Atlas_{elem_name}",
                dxt_format="DXT1",
                is_normal_map=False,
            )
        )
    tex_specs.append(make_flat_normal_spec("T_Inkblade_Normal_Flat"))
    textures_upk_bytes = build_texture_upk(
        tex_specs,
        package_guid_seed=f"MeleeArsenalTextures:{kit.revision}",
    )
    Path(out_textures_upk).parent.mkdir(parents=True, exist_ok=True)
    Path(out_textures_upk).write_bytes(textures_upk_bytes)

    elem_sock_uu = cal.map_point((-0.065, 0.12, 0.0))
    muzzle_sock_uu = cal.map_point((0.0, 1.35, 0.0))

    range_by_name = {r.name: r for r in packed_ranges}
    compiled_manifest: dict[str, Any] = {
        "schema": "melee_arsenal.compiled_inkblade/1.0",
        "kit_revision": kit.revision,
        # What the packages were built from.  `melee install` compares this against a fresh
        # `kit_fingerprint()` and recompiles when the generator has repainted the atlases.
        "kit_fingerprint": kit_fingerprint(kit_path),
        "meshes_package": Path(out_meshes_upk).stem,
        "mesh_export": mesh_export_name,
        "textures_package": Path(out_textures_upk).stem,
        "normal_texture": "T_Inkblade_Normal_Flat",
        "element_textures": {
            elem_name: f"T_Inkblade_Atlas_{elem_name}" for elem_name in ELEMENT_NAMES
        },
        "atlas_size": atlas_size,
        "ref_bone_index": ref_bone_idx,
        "ref_bone_name": ref_bone_name,
        "sockets_uu": {
            "elemental": [round(x, 4) for x in elem_sock_uu],
            "muzzle": [round(x, 4) for x in muzzle_sock_uu],
        },
        "calibration": {
            "scale_uu_per_meter": cal.scale_uu_per_meter,
            "grip_origin_uu": list(cal.grip_origin_uu),
            "blade_forward_pitch_deg": cal.blade_forward_pitch_deg,
            "blade_yaw_deg": cal.blade_yaw_deg,
        },
        "total_gpu_vertices": skel.num_vertices,
        "total_triangles": len(skel.indices) // 3,
        "fragments": [
            {
                "part_id": v.part_id,
                "variant_index": v.variant_index,
                "fragment_name": v.fragment_name,
                "slot": v.slot,
                "socket_name": v.socket_name,
                "offset_gltf_m": list(v.offset_gltf_m),
                "face_gap_m": v.face_gap_m,
                "parent_part_ids": list(v.parent_part_ids),
                "start_index": range_by_name[v.fragment_name].start_index,
                "num_triangles": range_by_name[v.fragment_name].num_triangles,
                "num_vertices": range_by_name[v.fragment_name].num_vertices,
                "bounds_min": [round(x, 4) for x in range_by_name[v.fragment_name].bounds_min],
                "bounds_max": [round(x, 4) for x in range_by_name[v.fragment_name].bounds_max],
            }
            for v in variants
        ],
    }
    Path(out_manifest_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_manifest_json).write_text(json.dumps(compiled_manifest, indent=2), encoding="utf-8")
    return compiled_manifest
