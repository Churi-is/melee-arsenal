"""Packaging and deployment use matching generated assets, not stale source copies."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
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
                self.assertEqual(archive.read("sdk_mods/melee_arsenal/upk/MeleeArsenalTextures.upk"), b"textures")
            game = Path(tmp) / "game"
            (game / "WillowGame/CookedPCConsole").mkdir(parents=True)
            subprocess.run([str(repo / "melee"), "install"], cwd=repo, check=True,
                           capture_output=True, env={**os.environ, "BL2_DIR": str(game)})
            installed = game / "sdk_mods/melee_arsenal"
            self.assertEqual((installed / "data/inkblade_compiled_manifest.json").read_text(), "generated")
            self.assertEqual((installed / "upk/MeleeArsenalMeshes.upk").read_bytes(), b"mesh")
            self.assertEqual((installed / "upk/MeleeArsenalTextures.upk").read_bytes(), b"textures")
            self.assertEqual((game / "WillowGame/CookedPCConsole/MeleeArsenalTextures.upk").read_bytes(), b"textures")

    def test_install_recompiles_the_upks_when_the_kit_changed(self):
        """Rebuild the packages when their recorded kit fingerprint differs from the current kit."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "tools").mkdir(parents=True)
            (repo / "build/inkblade_kit").mkdir(parents=True)
            shutil.copy2(ROOT / "melee", repo / "melee")
            (repo / ".gitignore").write_text("build/\n")
            mod = repo / "sdk_mods/melee_arsenal"
            (mod / "data").mkdir(parents=True)
            (mod / "upk").mkdir()
            (mod / "__init__.py").write_text('__version__ = "1.0"\n')
            (repo / "build/inkblade_kit/socket_manifest.json").write_text("{}")

            # Stand-in compiler: prints the kit fingerprint, otherwise writes new packages.
            (repo / "tools/compile_inkblade_upk.py").write_text(
                "import sys, json\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "if '--fingerprint-only' in args:\n"
                "    print('fresh-kit')\n"
                "    raise SystemExit(0)\n"
                "cooked = Path('build/WillowGame/CookedPCConsole')\n"
                "cooked.mkdir(parents=True, exist_ok=True)\n"
                "(cooked / 'MeleeArsenalMeshes.upk').write_bytes(b'rebuilt-mesh')\n"
                "(cooked / 'MeleeArsenalTextures.upk').write_bytes(b'rebuilt-textures')\n"
                "Path('build/inkblade_compiled_manifest.json').write_text("
                "json.dumps({'kit_fingerprint': 'fresh-kit'}))\n"
            )

            # Packages whose recorded fingerprint differs from the current kit.
            cooked = repo / "build/WillowGame/CookedPCConsole"
            cooked.mkdir(parents=True)
            (cooked / "MeleeArsenalMeshes.upk").write_bytes(b"old-mesh")
            (cooked / "MeleeArsenalTextures.upk").write_bytes(b"old-textures")
            (repo / "build/inkblade_compiled_manifest.json").write_text(
                '{"kit_fingerprint": "stale-kit"}')

            game = Path(tmp) / "game"
            (game / "WillowGame/CookedPCConsole").mkdir(parents=True)
            env = {**os.environ, "BL2_DIR": str(game), "PYTHON": sys.executable}
            result = subprocess.run([str(repo / "melee"), "install"], cwd=repo,
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("recompiling", result.stdout)
            self.assertEqual((game / "WillowGame/CookedPCConsole/MeleeArsenalTextures.upk").read_bytes(),
                             b"rebuilt-textures")
            self.assertEqual((game / "sdk_mods/melee_arsenal/upk/MeleeArsenalMeshes.upk").read_bytes(),
                             b"rebuilt-mesh")

            # `--no-rebuild` keeps the packages on disk but says so, loudly.
            (repo / "build/inkblade_compiled_manifest.json").write_text(
                '{"kit_fingerprint": "stale-again"}')
            result = subprocess.run([str(repo / "melee"), "install", "--no-rebuild"], cwd=repo,
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("do not match the kit", result.stdout + result.stderr)

    def test_status_detects_partial_install_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            shutil.copy2(ROOT / "melee", repo / "melee")
            shutil.copytree(ROOT / "tools", repo / "tools", ignore=shutil.ignore_patterns("__pycache__"))
            (repo / ".gitignore").write_text("build/\n")
            mod = repo / "sdk_mods/melee_arsenal"
            (mod / "data").mkdir(parents=True)
            (mod / "__init__.py").write_text('__version__ = "1.0"\n')
            (mod / "runtime.py").write_text("print('new')\n")
            cooked = repo / "build/WillowGame/CookedPCConsole"
            cooked.mkdir(parents=True)
            (cooked / "MeleeArsenalMeshes.upk").write_bytes(b"mesh")
            (cooked / "MeleeArsenalTextures.upk").write_bytes(b"textures")
            (repo / "build/inkblade_compiled_manifest.json").write_text('{"kit_fingerprint":"fp"}')
            game = Path(tmp) / "game"
            installed = game / "sdk_mods/melee_arsenal"
            installed.mkdir(parents=True)
            (game / "WillowGame/CookedPCConsole").mkdir(parents=True)
            # Old __init__ matches, but runtime.py / manifest / UPKs are missing.
            (installed / "__init__.py").write_text('__version__ = "1.0"\n')
            env = {**os.environ, "BL2_DIR": str(game), "PYTHON": sys.executable}
            result = subprocess.run([str(repo / "melee"), "status"], cwd=repo,
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("out of sync", result.stdout)
            self.assertNotIn("installed & in sync", result.stdout)

    def test_install_no_rebuild_requires_build_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            shutil.copy2(ROOT / "melee", repo / "melee")
            (repo / ".gitignore").write_text("build/\n")
            mod = repo / "sdk_mods/melee_arsenal"
            mod.mkdir(parents=True)
            (mod / "__init__.py").write_text('__version__ = "1.0"\n')
            game = Path(tmp) / "game"
            (game / "WillowGame/CookedPCConsole").mkdir(parents=True)
            env = {**os.environ, "BL2_DIR": str(game), "PYTHON": sys.executable}
            result = subprocess.run([str(repo / "melee"), "install", "--no-rebuild"], cwd=repo,
                                    capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing cooked UPKs or manifest", result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
