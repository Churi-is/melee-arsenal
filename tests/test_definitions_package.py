"""Structural tests for the UE3 definition builders."""
from __future__ import annotations

import contextlib
import importlib
import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
MOD_DIR = ROOT_DIR / "sdk_mods" / "melee_arsenal"
RUNTIME_DIR = MOD_DIR / "runtime"
DEFINITIONS_DIR = RUNTIME_DIR / "definitions"

EXPECTED_MODULES = (
    "helpers",
    "donors",
    "resources",
    "context",
    "firing_modes",
    "name_parts",
    "weapon_types",
    "parts",
    "balances",
)

EXPECTED_BUILDERS = (
    "name_parts.build_name_parts",
    "firing_modes.build_element_firing_modes",
    "weapon_types.build_weapon_types",
    "parts.build_body_parts",
    "parts.build_blade_parts",
    "parts.build_grip_parts",
    "parts.build_accessory_parts",
    "parts.build_element_parts",
    "parts.build_material_parts",
    "balances.build_balances",
)

PART_BUILDERS = (
    "build_body_parts",
    "build_blade_parts",
    "build_grip_parts",
    "build_accessory_parts",
    "build_element_parts",
    "build_material_parts",
)


@contextlib.contextmanager
def load_definitions():
    """Import the definitions package without loading the in-game runtime entry point."""
    top = types.ModuleType("melee_arsenal")
    top.__path__ = [str(MOD_DIR)]
    runtime_pkg = types.ModuleType("melee_arsenal.runtime")
    runtime_pkg.__path__ = [str(RUNTIME_DIR)]
    with mock.patch.dict(sys.modules, {"melee_arsenal": top, "melee_arsenal.runtime": runtime_pkg}):
        for name in list(sys.modules):
            if name.startswith("melee_arsenal.runtime.definitions") or name == "melee_arsenal.runtime.asset_library":
                sys.modules.pop(name, None)
        definitions = importlib.import_module("melee_arsenal.runtime.definitions")
        yield definitions


class TestDefinitionsPackage(unittest.TestCase):
    def test_builder_modules_exist(self) -> None:
        for name in EXPECTED_MODULES:
            with self.subTest(module=name):
                self.assertTrue((DEFINITIONS_DIR / f"{name}.py").is_file())

    def test_one_builder_per_part_type(self) -> None:
        with load_definitions():
            parts = importlib.import_module("melee_arsenal.runtime.definitions.parts")
            source = (DEFINITIONS_DIR / "parts.py").read_text(encoding="utf-8")
            for name in PART_BUILDERS:
                with self.subTest(builder=name):
                    self.assertTrue(callable(getattr(parts, name)))
                    self.assertIn(f"def {name}(ctx: BuildContext)", source)

    def test_orchestrator_calls_builders_in_order(self) -> None:
        with load_definitions() as definitions:
            source = inspect.getsource(definitions.ensure_definitions_built)
            positions = [source.index(f"{name}(ctx)") for name in EXPECTED_BUILDERS]
            self.assertEqual(positions, sorted(positions))
            self.assertIn("asset_library.register_assets_in_library(", source)
            self.assertIn("BuildContext.create(donor_assets)", source)


if __name__ == "__main__":
    unittest.main()
