"""Structural tests for the `runtime/definitions/` package split.

The old 1,550-line `runtime/definitions.py` is now a package with one module per
concern and one builder function per part type. These tests keep the layout and
the backwards-compatible public API from drifting; behaviour is covered by the
strict mock-SDK suite (`test_runtime_mock.py`, `test_runtime_regressions.py`).
"""
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
    "build_name_parts",
    "build_element_firing_modes",
    "build_weapon_types",
    "build_body_parts",
    "build_blade_parts",
    "build_grip_parts",
    "build_accessory_parts",
    "build_element_parts",
    "build_material_parts",
    "build_balances",
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
    """Import the definitions package offline without executing `runtime/__init__.py`."""
    top = types.ModuleType("melee_arsenal")
    top.__path__ = [str(MOD_DIR)]
    runtime_pkg = types.ModuleType("melee_arsenal.runtime")
    runtime_pkg.__path__ = [str(RUNTIME_DIR)]
    with mock.patch.dict(sys.modules, {"melee_arsenal": top, "melee_arsenal.runtime": runtime_pkg}):
        for name in list(sys.modules):
            if name.startswith("melee_arsenal.runtime.definitions"):
                sys.modules.pop(name, None)
        definitions = importlib.import_module("melee_arsenal.runtime.definitions")
        yield definitions


class TestDefinitionsPackageLayout(unittest.TestCase):
    def test_definitions_is_a_package_not_a_monolith(self) -> None:
        self.assertTrue((DEFINITIONS_DIR / "__init__.py").is_file())
        self.assertFalse((RUNTIME_DIR / "definitions.py").exists(), "old monolith should be gone")
        for name in EXPECTED_MODULES:
            path = DEFINITIONS_DIR / f"{name}.py"
            with self.subTest(module=name):
                self.assertTrue(path.is_file(), f"missing definitions/{name}.py")
                self.assertLessEqual(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    500,
                    f"definitions/{name}.py grew back into a monolith",
                )

    def test_one_builder_per_part_type(self) -> None:
        with load_definitions():
            parts = importlib.import_module("melee_arsenal.runtime.definitions.parts")
            for name in PART_BUILDERS:
                with self.subTest(builder=name):
                    self.assertTrue(callable(getattr(parts, name)))
                    self.assertIn(f"def {name}(ctx: BuildContext)",
                                  (DEFINITIONS_DIR / "parts.py").read_text(encoding="utf-8"))

    def test_orchestrator_calls_builders_in_order(self) -> None:
        with load_definitions() as definitions:
            source = inspect.getsource(definitions.ensure_definitions_built)
            positions = [source.index(f"{name}(ctx)") for name in EXPECTED_BUILDERS]
            self.assertEqual(positions, sorted(positions), "builder call order changed")
            self.assertIn("register_assets_in_library(", source)
            self.assertIn("BuildContext.create(donors)", source)

    def test_public_api_still_resolves_to_the_split_modules(self) -> None:
        with load_definitions() as definitions:
            from melee_arsenal.runtime import asset_library

            self.assertIs(definitions.register_assets_in_library, asset_library.register_assets_in_library)
            for name in EXPECTED_BUILDERS:
                with self.subTest(builder=name):
                    self.assertTrue(callable(getattr(definitions, name)))
            for name in (
                "ensure_combat_resources_cached",
                "get_or_create_title_part",
                "get_or_create_prefix_part",
                "apply_ui_preview_settings_to_all_wts",
                "MELEE_RAY_SPEED",
                "MELEE_CLIP_SIZE",
                "BuildContext",
            ):
                with self.subTest(name=name):
                    self.assertTrue(hasattr(definitions, name))
            self.assertEqual(definitions.MELEE_RAY_SPEED, 9000.0)
            self.assertEqual(definitions.MELEE_CLIP_SIZE, 100)

    def test_helpers_and_donors_are_reexported_for_compat(self) -> None:
        with load_definitions() as definitions:
            helpers = importlib.import_module("melee_arsenal.runtime.definitions.helpers")
            donors = importlib.import_module("melee_arsenal.runtime.definitions.donors")
            self.assertIs(definitions._find, helpers._find)
            self.assertIs(definitions._ensure_subpackage, helpers._ensure_subpackage)
            self.assertIs(definitions._harvest_pistol_donors, donors._harvest_pistol_donors)
            self.assertIs(definitions._RARITY_DONOR_SUFFIX, donors._RARITY_DONOR_SUFFIX)


if __name__ == "__main__":
    unittest.main()
