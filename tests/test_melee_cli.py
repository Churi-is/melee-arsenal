"""Packaging and deployment use matching generated assets, not stale source copies."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class BuildOutputTests(unittest.TestCase):
    def test_package_and_install_use_build_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            shutil.copy2(ROOT / "melee", repo / "melee")
            (repo / ".gitignore").write_text("build/\n")
            mod = repo / "sdk_mods/melee_arsenal"
            (mod / "data").mkdir(parents=True)
            (mod / "upk").mkdir()
            (mod / "__init__.py").write_text('__version__ = "1.0"\n')
            (mod / "data/inkblade_compiled_manifest.json").write_text("stale")
            (mod / "upk/MeleeArsenalMeshes.upk").write_bytes(b"stale")
            cooked = repo / "build/WillowGame/CookedPCConsole"
            cooked.mkdir(parents=True)
            (cooked / "MeleeArsenalMeshes.upk").write_bytes(b"mesh")
            (cooked / "MeleeArsenalTextures.upk").write_bytes(b"textures")
            (repo / "build/inkblade_compiled_manifest.json").write_text("generated")
            subprocess.run([str(repo / "melee"), "package"], cwd=repo, check=True, capture_output=True)
            with zipfile.ZipFile(repo / "build/dist/melee_arsenal.zip") as archive:
                names = archive.namelist()
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(archive.read("sdk_mods/melee_arsenal/data/inkblade_compiled_manifest.json"), b"generated")
                self.assertEqual(archive.read("sdk_mods/melee_arsenal/upk/MeleeArsenalMeshes.upk"), b"mesh")
            game = Path(tmp) / "game"
            (game / "WillowGame/CookedPCConsole").mkdir(parents=True)
            subprocess.run([str(repo / "melee"), "install"], cwd=repo, check=True,
                           capture_output=True, env={**os.environ, "BL2_DIR": str(game)})
            installed = game / "sdk_mods/melee_arsenal"
            self.assertEqual((installed / "data/inkblade_compiled_manifest.json").read_text(), "generated")
            self.assertEqual((installed / "upk/MeleeArsenalMeshes.upk").read_bytes(), b"mesh")
            self.assertEqual((game / "WillowGame/CookedPCConsole/MeleeArsenalTextures.upk").read_bytes(), b"textures")
