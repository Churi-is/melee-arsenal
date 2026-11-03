"""Unit tests for pure-Python UE3 LZO/zlib Startup.upk decompression, UPK codec, and r3->r4 Inkblade compiler."""
from __future__ import annotations

import ctypes
import ctypes.util
import io
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import zlib

from PIL import Image

from tools.inkblade_upk.archive import PACKAGE_FILE_TAG, UE3PackageArchive
from tools.inkblade_upk.codec import (
    SkeletalMeshBinary,
    build_texture_upk,
    encode_pil_to_dxt_mips,
    make_flat_normal_spec,
)
from tools.inkblade_upk.kit import (
    InkbladeModularKit,
    _repair_outward_manifold_winding,
    compile_inkblade_kit_to_upks,
    kit_fingerprint,
)
from tools.inkblade_upk.lzo1x import lzo1x_decompress

REPO_ROOT = Path(__file__).resolve().parents[1]


def _find_optional_fixture(*rel_candidates: str) -> Path | None:
    for rel in rel_candidates:
        for base in (REPO_ROOT, REPO_ROOT / "uploads", REPO_ROOT.parent / "uploads"):
            cand = base / rel
            if cand.is_file():
                return cand
    return None


def _wrap_fully_compressed_ue3(raw_upk: bytes, use_lzo: bool = True, block_size: int = 0x20000) -> bytes:
    """Wrap raw `.upk` bytes in UE3's `Startup.upk` fully-compressed `FCompressedChunkHeader` framing."""
    lzo_path = ctypes.util.find_library("lzo2")
    lzo = ctypes.CDLL(lzo_path) if (use_lzo and lzo_path) else None
    if lzo is not None:
        lzo.lzo1x_1_compress.argtypes = [
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
        ]
        lzo.lzo1x_1_compress.restype = ctypes.c_int

    blocks_meta: list[tuple[int, int]] = []
    blocks_data = bytearray()
    for off in range(0, len(raw_upk), block_size):
        sub = raw_upk[off : off + block_size]
        if lzo is not None:
            wrk = ctypes.create_string_buffer(16384 * 8)
            cap = len(sub) + len(sub) // 16 + 64 + 3
            dst = ctypes.create_string_buffer(cap)
            olen = ctypes.c_size_t(cap)
            rc = lzo.lzo1x_1_compress(sub, len(sub), dst, ctypes.byref(olen), wrk)
            assert rc == 0
            comp = dst.raw[: olen.value]
        else:
            comp = zlib.compress(sub, 6)
        blocks_meta.append((len(comp), len(sub)))
        blocks_data.extend(comp)

    sum_comp = sum(c for c, _ in blocks_meta)
    sum_uncomp = sum(u for _, u in blocks_meta)
    out = bytearray(struct.pack("<IIII", PACKAGE_FILE_TAG, block_size, sum_comp, sum_uncomp))
    for c_sz, u_sz in blocks_meta:
        out.extend(struct.pack("<II", c_sz, u_sz))
    out.extend(blocks_data)
    return bytes(out)


class TestInkbladeUpkPipeline(unittest.TestCase):
    def test_lzo_and_zlib_fully_compressed_startup_upk_reader(self) -> None:
        spec_alb = encode_pil_to_dxt_mips(
            Image.new("RGB", (64, 64), (255, 98, 43)),
            name="T_Test_Swatch",
        )
        spec_nrm = make_flat_normal_spec("T_Test_Normal")
        raw_upk = build_texture_upk([spec_alb, spec_nrm], package_guid_seed="UnitTestTex")

        for use_lzo in (True, False):
            comp_upk = _wrap_fully_compressed_ue3(raw_upk, use_lzo=use_lzo, block_size=1024)
            with UE3PackageArchive(comp_upk) as ar:
                self.assertTrue(ar.is_fully_compressed)
                self.assertEqual(ar.uncompressed_size, len(raw_upk))
                self.assertEqual(ar.read_range(0, len(raw_upk)), raw_upk)
                exp = ar.find_export("T_Test_Normal", class_name="Texture2D")
                self.assertIsNotNone(exp)
                assert exp is not None
                self.assertEqual(exp.serial_size, 606)

    def test_lzo1x_edge_literals_and_overlapping_matches(self) -> None:
        lzo_path = ctypes.util.find_library("lzo2")
        if not lzo_path:
            self.skipTest("liblzo2 not installed")
        lzo = ctypes.CDLL(lzo_path)
        lzo.lzo1x_1_compress.argtypes = [
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
        ]
        lzo.lzo1x_1_compress.restype = ctypes.c_int

        for payload in (b"X", b"XY", b"XYZ", b"XYZW", b"\x00" * 65536, b"INKBLADE" * 5000):
            wrk = ctypes.create_string_buffer(16384 * 8)
            cap = len(payload) + 256
            dst = ctypes.create_string_buffer(cap)
            olen = ctypes.c_size_t(cap)
            self.assertEqual(lzo.lzo1x_1_compress(payload, len(payload), dst, ctypes.byref(olen), wrk), 0)
            self.assertEqual(lzo1x_decompress(dst.raw[: olen.value], len(payload)), payload)

    def test_manifold_winding_repair_and_donor_roundtrip(self) -> None:
        # Build a watertight cube where 2 of the 6 faces (+Z and -Z) are deliberately wound inward!
        verts = [
            (-1.0, -1.0, -1.0),
            (1.0, -1.0, -1.0),
            (1.0, 1.0, -1.0),
            (-1.0, 1.0, -1.0),
            (-1.0, -1.0, 1.0),
            (1.0, -1.0, 1.0),
            (1.0, 1.0, 1.0),
            (-1.0, 1.0, 1.0),
        ]
        # Outward CCW except top (+Z) and bottom (-Z) which are deliberately inverted:
        quads = [
            (0, 1, 2, 3),  # -Z INVERTED (points +Z inward!)
            (4, 7, 6, 5),  # +Z INVERTED (points -Z inward!)
            (0, 1, 5, 4),  # -Y outward
            (1, 2, 6, 5),  # +X outward
            (2, 3, 7, 6),  # +Y outward
            (3, 0, 4, 7),  # -X outward
        ]
        tris = []
        for a, b, c, d in quads:
            tris.append((a, b, c))
            tris.append((a, c, d))
        nrms = [(0.0, 0.0, 1.0)] * len(verts)
        uvs = [(0.0, 0.0)] * len(verts)
        sides = [0] * len(verts)
        chans = ["SURFACE"] * len(tris)
        ds = [True] * len(tris)

        r_pos, r_nrm, _, _, r_tris, _ = _repair_outward_manifold_winding(
            verts, nrms, uvs, sides, tris, chans, ds
        )
        self.assertEqual(len(r_tris), 12)
        vol = 0.0
        for i0, i1, i2 in r_tris:
            p0, p1, p2 = r_pos[i0], r_pos[i1], r_pos[i2]
            vol += (
                p0[0] * (p1[1] * p2[2] - p1[2] * p2[1])
                - p0[1] * (p1[0] * p2[2] - p1[2] * p2[0])
                + p0[2] * (p1[0] * p2[1] - p1[1] * p2[0])
            ) / 6.0
            # Every vertex normal must point outward (dot(pos, nrm) > 0 on centered cube)
            self.assertGreater(p0[0] * r_nrm[i0][0] + p0[1] * r_nrm[i0][1] + p0[2] * r_nrm[i0][2], 0.0)
        self.assertAlmostEqual(vol, 8.0, places=5)

        donor_upk = REPO_ROOT / "tools" / "inkblade_upk" / "donors" / "weap_pistol_donor.upk"
        self.assertTrue(donor_upk.is_file())
        with UE3PackageArchive(donor_upk) as ar:
            sk = SkeletalMeshBinary.from_archive_export(ar, ar.exports[0])
            self.assertTrue(any(b.name == "WeaponOffset" for b in sk.ref_skeleton))

    def test_r3_kit_and_simulated_r4_divergent_sockets(self) -> None:
        kit_zip = _find_optional_fixture(
            "Inkblade_Modular_Kit_r3.zip",
            "Inkblade_Modular_Kit_r3.zip.txt",
        )
        if kit_zip is None:
            self.skipTest("Optional Inkblade_Modular_Kit_r3 archive not in uploads/")

        kit = InkbladeModularKit(kit_zip)
        self.assertEqual(len(kit.parts), 22)
        r3_variants = kit.plan_socket_baked_variants()
        # In r3 all blades share identical socket coordinates, so 22 parts -> 22 unique fragments!
        self.assertEqual(len(r3_variants), 22)

        # Simulate r4 moving Torgue's accessory socket and Bandit's grip pommel socket!
        torgue_blade = kit.parts["blade_torgue"]
        torgue_blade.sockets_gltf_m["SOCKET_ACCESSORY"] = (0.0, 0.24, 0.095)
        bandit_grip = kit.parts["grip_bandit"]
        bandit_grip.sockets_gltf_m["SOCKET_POMMEL"] = (0.0, -0.58, 0.0)

        r4_variants = kit.plan_socket_baked_variants()
        # 6 non-pommel accessories gain a 2nd socket variant (`+6`) and 1 pommel accessory gains a 2nd variant (`+1`) = 29!
        self.assertEqual(len(r4_variants), 29)
        torgue_serrated = [
            v
            for v in r4_variants
            if v.part_id == "accessory_serrated_edge" and "blade_torgue" in v.parent_part_ids
        ]
        self.assertEqual(len(torgue_serrated), 1)
        self.assertEqual(torgue_serrated[0].fragment_name, "Ink_accessory_serrated_edge__s1")
        self.assertEqual(torgue_serrated[0].offset_gltf_m, (0.0, 0.24, 0.095))

    def test_end_to_end_upk_compile_with_donor_mesh(self) -> None:
        ribs_p1 = _find_optional_fixture("x01.txt")
        ribs_p2 = _find_optional_fixture("x02.txt")
        r3_zip = _find_optional_fixture("Inkblade_Modular_Kit_r3.zip", "Inkblade_Modular_Kit_r3.zip.txt")
        if not (ribs_p1 and ribs_p2 and r3_zip):
            self.skipTest("Optional Ribs Arsenal or r3 kit archive not in uploads/")

        zf = zipfile.ZipFile(io.BytesIO(ribs_p1.read_bytes() + ribs_p2.read_bytes()))
        with UE3PackageArchive(zf.read("WillowGame/CookedPCConsole/MaliwanBarrelMeshes.upk")) as ar:
            sk = SkeletalMeshBinary.from_archive_export(ar, ar.exports[0])
            bbounds = sk.compute_fragment_bounds(0, 60)
            self.assertGreater(bbounds["vertex_count"], 0)
            sk.strip_to_donor()
            donor_bytes = sk.to_package_bytes("DonorPistolStandIn")

        with tempfile.TemporaryDirectory() as tmp:
            mf = compile_inkblade_kit_to_upks(
                kit_path=r3_zip,
                donor_upk_path_or_bytes=donor_bytes,
                out_meshes_upk=Path(tmp) / "MeleeArsenalMeshes.upk",
                out_textures_upk=Path(tmp) / "MeleeArsenalTextures.upk",
                out_manifest_json=Path(tmp) / "manifest.json",
                atlas_size=1024,
            )
            self.assertEqual(mf["total_triangles"], 3188)
            self.assertEqual(len(mf["fragments"]), 22)
            # The manifest records what the packages were built from, so install can tell a repainted
            # kit from an untouched one.
            self.assertRegex(mf["kit_fingerprint"], r"^[0-9a-f]{32}$")
            self.assertEqual(mf["kit_fingerprint"], kit_fingerprint(r3_zip))
            meshes_raw = (Path(tmp) / "MeleeArsenalMeshes.upk").read_bytes()
            textures_raw = (Path(tmp) / "MeleeArsenalTextures.upk").read_bytes()
            self.assertEqual(struct.unpack_from("<I", meshes_raw, 29)[0], 129)
            self.assertEqual(struct.unpack_from("<I", textures_raw, 29)[0], 129)
            with UE3PackageArchive(meshes_raw) as out_ar:
                out_sk = SkeletalMeshBinary.from_archive_export(out_ar, out_ar.exports[0])
                self.assertEqual(len(out_sk.indices) // 3, 3188)

            r4_zip = _find_optional_fixture(
                "Inkblade_Modular_Kit_r4_final.zip",
                "Inkblade_Modular_Kit_r4_final.zip.txt",
            )
            if r4_zip is not None:
                mf_r4 = compile_inkblade_kit_to_upks(
                    kit_path=r4_zip,
                    donor_upk_path_or_bytes=donor_bytes,
                    out_meshes_upk=Path(tmp) / "MeleeArsenalMeshes_r4.upk",
                    out_textures_upk=Path(tmp) / "MeleeArsenalTextures_r4.upk",
                    out_manifest_json=Path(tmp) / "manifest_r4.json",
                    atlas_size=1024,
                )
                self.assertEqual(mf_r4["kit_revision"], "r4_final_slim_spine_hex_grip_uv3")
                self.assertEqual(len(mf_r4["fragments"]), 42)
                self.assertEqual(mf_r4["total_triangles"], 10020)
                self.assertLess(mf_r4["total_gpu_vertices"], 22000)
                r4_meshes_raw = (Path(tmp) / "MeleeArsenalMeshes_r4.upk").read_bytes()
                self.assertEqual(struct.unpack_from("<I", r4_meshes_raw, 29)[0], 129)

    def test_real_pistol_donor_weapon_offset_skinning_and_sockets(self) -> None:
        donor_upk = REPO_ROOT / "tools" / "inkblade_upk" / "donors" / "weap_pistol_donor.upk"
        kit_zip = _find_optional_fixture(
            "InkBlade.zip1.txt",
            "InkBlade.zip",
            "Inkblade_Bold.zip",
            "Inkblade_Modular_Kit_r4_final.zip",
            "Inkblade_Modular_Kit_r4_final.zip.txt",
        )
        if not donor_upk.is_file() or kit_zip is None:
            self.skipTest("Optional r4_final / r5 InkBlade Bold archive not in uploads/")

        kit = InkbladeModularKit(kit_zip)
        placements_2k = kit.pack_atlas_layout(atlas_size=2048)
        for pid, pl in placements_2k.items():
            if kit.parts[pid].slot in ("BarrelPartDefinition", "GripPartDefinition"):
                self.assertGreaterEqual(
                    pl.width,
                    512,
                    f"{pid} packed below 512px width ({pl.width}x{pl.height}) in 2048 atlas",
                )

        with tempfile.TemporaryDirectory() as tmp:
            mf = compile_inkblade_kit_to_upks(
                kit_path=kit_zip,
                donor_upk_path_or_bytes=donor_upk,
                out_meshes_upk=Path(tmp) / "MeleeArsenalMeshes.upk",
                out_textures_upk=Path(tmp) / "MeleeArsenalTextures.upk",
                out_manifest_json=Path(tmp) / "manifest.json",
                atlas_size=1024,
            )
            self.assertEqual(mf["ref_bone_index"], 1)
            self.assertEqual(mf["ref_bone_name"], "WeaponOffset")
            self.assertEqual(len(mf["fragments"]), 42)
            self.assertEqual(mf["total_triangles"], 10020)

            textures_raw = (Path(tmp) / "MeleeArsenalTextures.upk").read_bytes()
            self.assertEqual(struct.unpack_from("<I", textures_raw, 29)[0], 129)
            with UE3PackageArchive(textures_raw) as tex_ar:
                self.assertEqual(len(tex_ar.exports), 7)

            meshes_raw = (Path(tmp) / "MeleeArsenalMeshes.upk").read_bytes()
            self.assertEqual(struct.unpack_from("<I", meshes_raw, 29)[0], 129)
            with UE3PackageArchive(meshes_raw) as out_ar:
                self.assertEqual(len(out_ar.exports), 61)  # 1 SkeletalMesh + 60 SkeletalMeshSockets
                sk = SkeletalMeshBinary.from_archive_export(out_ar, out_ar.exports[0])
                self.assertEqual(sk.chunks[0].bone_map, [0, 1])
                self.assertEqual(sk.gpu_vertices[0].influence_bones, (1, 0, 0, 0))
                self.assertEqual(sk.gpu_vertices[0].influence_weights, (255, 0, 0, 0))
                lod_prop = next(p for p in sk.props if p.name == "LODInfo")
                shadow_prop = next(
                    sp for sp in lod_prop.struct_array_items[0] if sp.name == "bEnableShadowCasting"
                )
                self.assertEqual(shadow_prop.raw_payload, b"\x01\x00\x00\x00\x01")

                # Verify bilateral D3D9 front-face symmetry on every single base fragment
                # (regression test against inward-wound back bevels & Y-cylinders in r3/r4 GLBs!)
                for frag in mf["fragments"]:
                    if frag["variant_index"] != 0:
                        continue
                    s, c = frag["start_index"], frag["num_triangles"] * 3
                    vis_front = 0
                    vis_back = 0
                    for k in range(s, s + c, 3):
                        p0 = sk.gpu_vertices[sk.indices[k]].position
                        p1 = sk.gpu_vertices[sk.indices[k + 1]].position
                        p2 = sk.gpu_vertices[sk.indices[k + 2]].position
                        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
                        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
                        cx = e1[1] * e2[2] - e1[2] * e2[1]
                        if cx > 1e-6:
                            vis_front += 1
                        elif cx < -1e-6:
                            vis_back += 1
                    self.assertGreater(vis_front, 0, frag["fragment_name"])
                    self.assertEqual(
                        vis_front,
                        vis_back,
                        f"Asymmetric front/back face culling on {frag['fragment_name']}: {vis_front} vs {vis_back}",
                    )


class KitFingerprintTests(unittest.TestCase):
    """The kit fingerprint is what tells `melee install` that the generator repainted the atlases."""

    def _make_kit(self, root: Path) -> Path:
        kit = root / "kit"
        (kit / "textures").mkdir(parents=True)
        (kit / "socket_manifest.json").write_text('{"schema": "inkblade.socket-kit/1.0"}')
        (kit / "textures/blade_bandit_basecolor.png").write_bytes(b"png-one")
        (kit / "textures/blade_bandit_uv_layout.json").write_text("{}")
        return kit

    def test_fingerprint_tracks_texture_content_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kit = self._make_kit(Path(tmp))
            first = kit_fingerprint(kit)
            self.assertEqual(first, kit_fingerprint(kit))
            # Unrelated files in the folder must not force a recompile...
            (kit / "notes.txt").write_text("scratch")
            (kit / "README.md").write_text("docs")
            self.assertEqual(first, kit_fingerprint(kit))
            # ...but repainted pixels must.
            (kit / "textures/blade_bandit_basecolor.png").write_bytes(b"png-two")
            self.assertNotEqual(first, kit_fingerprint(kit))
            # And so must a moved layout.
            (kit / "textures/blade_bandit_uv_layout.json").write_text('{"islands": []}')
            self.assertNotEqual(first, kit_fingerprint(kit))

    def test_fingerprint_matches_between_directory_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kit = self._make_kit(Path(tmp))
            archive = Path(tmp) / "kit.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for path in sorted(kit.rglob("*")):
                    if path.is_file():
                        zf.write(path, path.relative_to(kit).as_posix())
            self.assertEqual(kit_fingerprint(kit), kit_fingerprint(archive))


if __name__ == "__main__":
    unittest.main()
