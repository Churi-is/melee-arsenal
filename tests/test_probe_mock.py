"""Mock-execution tests for the in-game asset probe."""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
import json
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
def _wrap_lzo_startup_upk(raw_upk: bytes, block_size: int = 0x20000) -> bytes:
    lzo_path = ctypes.util.find_library("lzo2")
    lzo = ctypes.CDLL(lzo_path) if lzo_path else None
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
        chunk = raw_upk[off : off + block_size]
        if lzo is not None:
            wrk = ctypes.create_string_buffer(16384 * 8)
            cap = len(chunk) + len(chunk) // 16 + 67
            dst = ctypes.create_string_buffer(cap)
            olen = ctypes.c_size_t(cap)
            rc = lzo.lzo1x_1_compress(chunk, len(chunk), dst, ctypes.byref(olen), wrk)
            if rc != 0:
                raise RuntimeError(f"lzo1x_1_compress failed: {rc}")
            comp = dst.raw[: olen.value]
        else:
            import zlib

            comp = zlib.compress(chunk)
        blocks_meta.append((len(comp), len(chunk)))
        blocks_data.extend(comp)

    sum_comp = sum(c for c, _ in blocks_meta)
    sum_uncomp = sum(u for _, u in blocks_meta)
    out = bytearray(struct.pack("<IIII", 0x9E2A83C1, block_size, sum_comp, sum_uncomp))
    for c_sz, u_sz in blocks_meta:
        out.extend(struct.pack("<II", c_sz, u_sz))
    out.extend(blocks_data)
    return bytes(out)


class TestProbeAssetAndDonorSuite(unittest.TestCase):
    def test_probe_executes_sections_and_extracts_compressed_startup_donor(self) -> None:
        from tools.inkblade_upk.archive import UE3PackageArchive
        from tools.inkblade_upk.codec import (
            CustomFragmentInput,
            SkeletalMeshBinary,
            replace_with_custom_fragments,
        )

        donor_path = ROOT_DIR / "tools" / "inkblade_upk" / "donors" / "weap_pistol_donor.upk"
        with UE3PackageArchive(donor_path) as ar:
            sk = SkeletalMeshBinary.from_archive_export(ar, ar.exports[0])
        positions = [(float(i % 7), -float(i), float(i % 5)) for i in range(160)]
        normals = [(0.0, 0.0, 1.0)] * 160
        tangents = [(1.0, 0.0, 0.0, 1.0)] * 160
        uvs = [(0.5, 0.5)] * 160
        triangles = [(i, i + 1, i + 2) for i in range(150)]
        replace_with_custom_fragments(
            sk,
            [
                CustomFragmentInput(
                    name="SyntheticGestalt",
                    positions=positions,
                    normals=normals,
                    tangents=tangents,
                    uvs=uvs,
                    triangles=triangles,
                    ref_bone_index=1,
                )
            ],
        )
        raw_skel_upk = sk.to_package_bytes("SG_TestMesh")
        compressed_startup = _wrap_lzo_startup_upk(raw_skel_upk)

        def vec(x=0.0, y=0.0, z=0.0):
            return types.SimpleNamespace(X=float(x), Y=float(y), Z=float(z))
        def rot(p=0, y=0, r=0):
            return types.SimpleNamespace(Pitch=int(p), Yaw=int(y), Roll=int(r))

        t_alb = types.SimpleNamespace(
            SizeX=64,
            SizeY=64,
            Format="PF_DXT1",
            NeverStream=True,
            LODGroup="TEXTUREGROUP_Weapon",
            _path_name=lambda: "MeleeProbeTex.T_Probe_Inkblade",
        )
        t_nrm = types.SimpleNamespace(
            SizeX=4,
            SizeY=4,
            SRGB=False,
            CompressionSettings="TC_Normalmap",
            _path_name=lambda: "MeleeProbeTex.T_Probe_Normal_Flat",
        )
        master_cm = types.SimpleNamespace(
            Class=types.SimpleNamespace(Name="Material"),
            Parent=None,
            TextureParameterValues=[],
            VectorParameterValues=[],
            ScalarParameterValues=[],
            _path_name=lambda: "Item_ClassMods.Mat.Master_ClassMod",
        )
        gestalt_mesh = types.SimpleNamespace(
            Origin=vec(0.0, 0.0, 0.0),
            RotOrigin=rot(0, 0, 0),
            Materials=[master_cm],
            Sockets=[
                types.SimpleNamespace(
                    SocketName="Muzzle",
                    BoneName="Barrel",
                    RelativeLocation=vec(0.0, -35.0, 4.0),
                    RelativeRotation=rot(0, 0, 0),
                    RelativeScale=vec(1.0, 1.0, 1.0),
                )
            ],
            _path_name=lambda: "Startup.SG_TestMesh",
        )
        gestalt_def = types.SimpleNamespace(
            GestaltSkeletalMesh=gestalt_mesh,
            GestaltInfos=[
                types.SimpleNamespace(
                    Parts=[
                        types.SimpleNamespace(SkeletalMeshFragmentName="Pistol_Grip_Hyperion", FirstIndex=0, NumPrimitives=90),
                        types.SimpleNamespace(SkeletalMeshFragmentName="Acc_Barrel_Blade1", FirstIndex=270, NumPrimitives=60),
                    ]
                )
            ],
            GestaltPartBounds=[
                types.SimpleNamespace(
                    SkeletalMeshFragmentName="Pistol_Grip_Hyperion",
                    ReferencePoseBounds=types.SimpleNamespace(
                        Origin=vec(0.0, -10.0, 0.0),
                        BoxExtent=vec(6.0, 18.0, 9.0),
                        SphereRadius=21.0,
                    ),
                )
            ],
            GestaltSocketMappings=[
                types.SimpleNamespace(
                    SkeletalMeshFragmentName="Pistol_Grip_Hyperion",
                    OriginalSocketName="Muzzle",
                    MangledSocketName="Pistol_Grip_Hyperion_Muzzle",
                )
            ],
            _path_name=lambda: "Weap_Pistol.GestaltDef_Pistol",
        )

        arms = types.SimpleNamespace(
            Scale=1.0,
            Scale3D=vec(1.0, 1.0, 1.0),
            Translation=vec(0.0, 0.0, 0.0),
            Rotation=rot(0, 0, 0),
            FOV=70.0,
            SkeletalMesh=types.SimpleNamespace(
                Sockets=[types.SimpleNamespace(SocketName="Weapon")],
                _path_name=lambda: "Hands_Siren.Meshes.Hands",
            ),
            GetSocketWorldLocationAndRotation=lambda s: (True, vec(22.0, 8.0, 56.0), rot(0, 0, 0)),
            _path_name=lambda: "Sanctuary_P.TheWorld:PersistentLevel.WillowPlayerPawn_0.Arms",
        )
        slot_node = types.SimpleNamespace(
            SkelComponent=arms,
            NodeName="WeaponAction",
            PlayCustomAnim=lambda seq, *a: 0.6333,
        )
        pawn = types.SimpleNamespace(
            Location=vec(0.0, 0.0, 0.0),
            EyeHeight=64.0,
            Arms=arms,
            Weapon=None,
            Mesh=types.SimpleNamespace(
                Scale=1.0,
                SkeletalMesh=types.SimpleNamespace(Sockets=[], _path_name=lambda: "Siren.Mesh"),
            ),
        )
        pc = types.SimpleNamespace(
            Pawn=pawn,
            FOVAngle=90.0,
            PlayerReplicationInfo=None,
            WorldInfo=types.SimpleNamespace(GetStreamingPersistentMapName=lambda: "Sanctuary_P"),
            GetPlayerViewPoint=lambda: (vec(0.0, 0.0, 64.0), rot(0, 0, 0)),
            GetHUDMovie=lambda: None,
        )

        created_objs: dict[str, types.SimpleNamespace] = {}

        def _construct_obj(cls_name: str, outer: object, name: str, flags: int = 0, template: object = None):
            outer_p = outer._path_name() if outer is not None and hasattr(outer, "_path_name") else ""
            full = f"{outer_p}.{name}" if outer_p else name
            ns = types.SimpleNamespace(
                ObjectFlags=flags,
                Parent=None,
                TextureParameterValues=[],
                SetParent=lambda p: setattr(ns, "Parent", p),
                SetTextureParameterValue=lambda k, v: ns.TextureParameterValues.append(
                    types.SimpleNamespace(ParameterName=k, ParameterValue=v)
                ),
                _path_name=lambda f=full: f,
            )
            created_objs[full] = ns
            return ns

        known = {
            "MeleeProbeTex.T_Probe_Inkblade": t_alb,
            "MeleeProbeTex.T_Probe_Normal_Flat": t_nrm,
            "Item_ClassMods.Mat.Master_ClassMod": master_cm,
            "Weap_Pistol.GestaltDef_Pistol": gestalt_def,
        }

        mock_sdk = types.ModuleType("unrealsdk")
        mock_sdk.load_package = lambda name: types.SimpleNamespace(ObjectFlags=0, _path_name=lambda n=name: n)
        mock_sdk.find_object = lambda cls_n, path: known.get(path) or created_objs.get(path)
        mock_sdk.construct_object = _construct_obj
        mock_sdk.find_all = lambda cls_n, exact=True: (
            [slot_node]
            if cls_n == "AnimNodeSlot"
            else [types.SimpleNamespace(_path_name=lambda: "FX_ENV_Misc.Particles.Part_Grenade_Explosion")]
            if cls_n == "ParticleSystem"
            else [types.SimpleNamespace(_path_name=lambda: "Ake_Wep_Explosive.Explosions.Ak_Play_Wep_Grenade_Explosion")]
            if cls_n == "AkEvent"
            else []
        )
        mock_sdk.logging = types.SimpleNamespace(info=lambda *a: None)

        mock_hooks = types.ModuleType("unrealsdk.hooks")
        mock_hooks.Type = types.SimpleNamespace(PRE="PRE", POST="POST")

        mock_mods_base = types.ModuleType("mods_base")
        mock_mods_base.ButtonOption = lambda *a, **kw: types.SimpleNamespace()
        mock_mods_base.Game = types.SimpleNamespace()
        mock_mods_base.build_mod = lambda **kw: None
        mock_mods_base.get_pc = lambda: pc
        mock_mods_base.command = lambda *a, **kw: (lambda fn: fn)
        mock_mods_base.hook = lambda *a, **kw: (
            lambda fn: (setattr(fn, "enable", lambda: None), setattr(fn, "disable", lambda: None), fn)[2]
        )

        with tempfile.TemporaryDirectory() as td:
            game_root = Path(td)
            cooked_dir = game_root / "WillowGame" / "CookedPCConsole"
            cooked_dir.mkdir(parents=True)
            (cooked_dir / "Startup.upk").write_bytes(compressed_startup)
            fake_exe = game_root / "Binaries" / "Win32" / "Borderlands2.exe"
            fake_exe.parent.mkdir(parents=True)
            fake_exe.write_bytes(b"MZ")

            rep_dir = Path(td) / "reports"
            sys.modules.pop("sdk_mods.melee_arsenal_probe", None)
            with mock.patch.dict(
                sys.modules,
                {
                    "unrealsdk": mock_sdk,
                    "unrealsdk.hooks": mock_hooks,
                    "mods_base": mock_mods_base,
                },
            ), mock.patch.object(sys, "executable", str(fake_exe)):
                import sdk_mods.melee_arsenal_probe as probe_mod

                probe_mod.REPORT_DIR = rep_dir
                probe_mod.REPORT_FILE = rep_dir / "melee_asset_probe_report.json"
                probe_mod.DONOR_UPK_FILE = rep_dir / "weap_pistol_donor.upk"

                report = probe_mod.run_asset_probe()
                self.assertTrue(probe_mod.REPORT_FILE.exists())
                saved = json.loads(probe_mod.REPORT_FILE.read_text(encoding="utf-8"))

                self.assertEqual(saved["p01_custom_upk_and_shader_smoke"]["mic_smoke_test"]["status"], "OK")
                self.assertTrue((cooked_dir / "MeleeProbeTex.upk").exists())
                self.assertEqual(len(saved["p02_gestalt_def_pistol_live"]["parts"]), 2)
                self.assertEqual(saved["p03_startup_upk_pistol_donor"]["status"], "OK")
                self.assertTrue(saved["p03_startup_upk_pistol_donor"]["is_fully_compressed"])
                self.assertIn("Pistol_Grip_Hyperion", saved["p03_startup_upk_pistol_donor"]["fragment_vertex_bounds"])

                donor_raw = base64.b64decode(saved["p03_startup_upk_pistol_donor"]["donor_upk_base64"])
                self.assertGreater(len(donor_raw), 4000)
                self.assertEqual(
                    saved["p05_live_poses_and_swing_arc"]["idle_pose"]["arms"]["sockets"]["Weapon"]["cam_local_loc"],
                    {"forward_uu": 22.0, "right_uu": 8.0, "up_uu": -8.0},
                )
                self.assertGreaterEqual(len(saved["p06_explosion_vfx_and_audio"]["explosion_particle_systems"]), 1)
                self.assertIsNotNone(report)


if __name__ == "__main__":
    unittest.main()
