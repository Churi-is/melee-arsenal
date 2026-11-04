"""Tests for UE3 package decoding, mesh compilation, and kit fingerprints."""
from __future__ import annotations

import ctypes
import ctypes.util
import json
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
    GlbPartMesh,
    InkbladeModularKit,
    _repair_outward_manifold_winding,
    compile_inkblade_kit_to_upks,
    kit_fingerprint,
)
from tools.inkblade_upk.lzo1x import lzo1x_decompress

REPO_ROOT = Path(__file__).resolve().parents[1]


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

    def test_manifold_winding_repair_and_donor_skeleton(self) -> None:
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

    def test_socket_baked_variants_group_matching_sockets(self) -> None:
        def part(
            part_id: str,
            slot: str,
            sockets: dict[str, tuple[float, float, float]],
        ) -> GlbPartMesh:
            return GlbPartMesh(
                part_id=part_id,
                slot=slot,
                manufacturer="Universal",
                positions=[],
                normals=[],
                tangents=[],
                uvs=[],
                triangles=[],
                triangle_channels=[],
                sockets_gltf_m=sockets,
                texture_rel_path="",
                uv_layout={},
            )

        kit = object.__new__(InkbladeModularKit)
        kit.manifest = {
            "parts": {
                "accessory_serrated_edge": {"target_socket": "SOCKET_ACCESSORY"},
                "accessory_balanced_pommel": {"target_socket": "SOCKET_POMMEL"},
            }
        }
        kit.parts = {
            "blade_bandit": part(
                "blade_bandit",
                "BarrelPartDefinition",
                {"MOUNT": (0.0, 0.0, 0.0), "SOCKET_GRIP": (0.0, -0.3, 0.0), "SOCKET_ACCESSORY": (0.0, 0.16, 0.0)},
            ),
            "blade_torgue": part(
                "blade_torgue",
                "BarrelPartDefinition",
                {"MOUNT": (0.0, 0.0, 0.0), "SOCKET_GRIP": (0.0, -0.3, 0.0), "SOCKET_ACCESSORY": (0.0, 0.16, 0.0)},
            ),
            "grip_bandit": part(
                "grip_bandit",
                "GripPartDefinition",
                {"MOUNT": (0.0, 0.0, 0.0), "SOCKET_POMMEL": (0.0, -0.55, 0.0)},
            ),
            "accessory_serrated_edge": part(
                "accessory_serrated_edge", "Accessory1PartDefinition", {"MOUNT": (0.0, 0.0, 0.0)}
            ),
            "accessory_balanced_pommel": part(
                "accessory_balanced_pommel", "Accessory1PartDefinition", {"MOUNT": (0.0, 0.0, 0.0)}
            ),
        }

        variants = kit.plan_socket_baked_variants()
        edge_variants = [v for v in variants if v.part_id == "accessory_serrated_edge"]
        pommel_variants = [v for v in variants if v.part_id == "accessory_balanced_pommel"]
        self.assertEqual(len(edge_variants), 1)
        self.assertEqual(set(edge_variants[0].parent_part_ids), {"blade_bandit", "blade_torgue"})
        self.assertEqual(len(pommel_variants), 1)

        kit.parts["blade_torgue"].sockets_gltf_m["SOCKET_ACCESSORY"] = (0.0, 0.24, 0.095)
        edge_variants = [
            v for v in kit.plan_socket_baked_variants() if v.part_id == "accessory_serrated_edge"
        ]
        self.assertEqual(len(edge_variants), 2)
        torgue_variant = next(v for v in edge_variants if "blade_torgue" in v.parent_part_ids)
        self.assertEqual(torgue_variant.offset_gltf_m, (0.0, 0.24, 0.095))

    def test_generated_kit_compiles_with_donor_mesh(self) -> None:
        from tools.inkblade_gen.config import RunConfig
        from tools.inkblade_gen.pipeline import Pipeline

        donor_upk = REPO_ROOT / "tools" / "inkblade_upk" / "donors" / "weap_pistol_donor.upk"
        self.assertTrue(donor_upk.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kit_dir = root / "kit"
            config = RunConfig(
                out_dir=kit_dir,
                work_dir=root / "stages",
                density=256.0,
                ink_px=3,
                padding=4,
                parts=("blade_bandit", "grip_bandit"),
                bake_combinations=False,
            )
            Pipeline(config).run()

            kit = InkbladeModularKit(kit_dir)
            self.assertEqual(set(kit.parts), {"blade_bandit", "grip_bandit"})
            manifest = compile_inkblade_kit_to_upks(
                kit_path=kit_dir,
                donor_upk_path_or_bytes=donor_upk,
                out_meshes_upk=root / "MeleeArsenalMeshes.upk",
                out_textures_upk=root / "MeleeArsenalTextures.upk",
                out_manifest_json=root / "manifest.json",
                atlas_size=512,
            )
            self.assertEqual(manifest["ref_bone_index"], 1)
            self.assertEqual(manifest["ref_bone_name"], "WeaponOffset")
            self.assertGreater(manifest["total_triangles"], 0)
            self.assertEqual(len(manifest["fragments"]), 2)
            self.assertEqual(manifest["kit_fingerprint"], kit_fingerprint(kit_dir))

            textures_raw = (root / "MeleeArsenalTextures.upk").read_bytes()
            self.assertEqual(struct.unpack_from("<I", textures_raw, 29)[0], 129)
            with UE3PackageArchive(textures_raw) as texture_archive:
                self.assertEqual(len(texture_archive.exports), 7)

            meshes_raw = (root / "MeleeArsenalMeshes.upk").read_bytes()
            self.assertEqual(struct.unpack_from("<I", meshes_raw, 29)[0], 129)
            with UE3PackageArchive(meshes_raw) as mesh_archive:
                mesh_export = next(
                    export for export in mesh_archive.exports
                    if mesh_archive.export_class_name(export) == "SkeletalMesh"
                )
                mesh = SkeletalMeshBinary.from_archive_export(mesh_archive, mesh_export)
                self.assertEqual(mesh.chunks[0].bone_map, [0, 1])
                self.assertEqual(mesh.gpu_vertices[0].influence_bones, (1, 0, 0, 0))
                self.assertEqual(mesh.gpu_vertices[0].influence_weights, (255, 0, 0, 0))


class KitFingerprintTests(unittest.TestCase):
    """The kit fingerprint covers generated mesh, texture, and manifest inputs."""

    def _make_kit(self, root: Path) -> Path:
        kit = root / "kit"
        (kit / "textures").mkdir(parents=True)
        (kit / "socket_manifest.json").write_text('{"schema": "inkblade.socket-kit/1.0"}')
        (kit / "textures/blade_bandit_basecolor.png").write_bytes(b"png-one")
        (kit / "textures/blade_bandit_uv_layout.json").write_text("{}")
        return kit

    def test_fingerprint_tracks_compiler_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kit = self._make_kit(Path(tmp))
            first = kit_fingerprint(kit)
            self.assertEqual(first, kit_fingerprint(kit))
            # Non-asset files are outside the compiler fingerprint.
            (kit / "notes.txt").write_text("scratch")
            (kit / "README.md").write_text("docs")
            self.assertEqual(first, kit_fingerprint(kit))
            # Texture content contributes to the fingerprint.
            (kit / "textures/blade_bandit_basecolor.png").write_bytes(b"png-two")
            self.assertNotEqual(first, kit_fingerprint(kit))
            # UV layout content contributes to the fingerprint.
            (kit / "textures/blade_bandit_uv_layout.json").write_text('{"islands": []}')
            self.assertNotEqual(first, kit_fingerprint(kit))

    def test_generated_stage_report_does_not_change_compiled_asset_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kit = self._make_kit(Path(tmp))
            docs = kit / "docs"
            docs.mkdir()
            report = docs / "stages.json"
            report.write_text('{"stages": [{"seconds": 1.0, "status": "ran"}]}')
            first = kit_fingerprint(kit)
            report.write_text('{"stages": [{"seconds": 99.0, "status": "cached"}]}')
            self.assertEqual(first, kit_fingerprint(kit))

    def test_socket_manifest_timing_does_not_change_compiled_asset_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            kit = self._make_kit(Path(tmp))
            manifest = {
                "schema": "inkblade.socket-kit/1.0",
                "revision": "baseline",
                "generator": {"seed": "default", "stages": [{"stage": "geometry", "fingerprint": "abc", "status": "ran", "seconds": 1.25}]},
            }
            socket_manifest = kit / "socket_manifest.json"
            socket_manifest.write_text(json.dumps(manifest))
            first = kit_fingerprint(kit)
            manifest["generator"]["stages"][0].update(status="cached", seconds=99.5)
            socket_manifest.write_text(json.dumps(manifest))
            self.assertEqual(first, kit_fingerprint(kit))
            manifest["revision"] = "alternate"
            socket_manifest.write_text(json.dumps(manifest))
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
