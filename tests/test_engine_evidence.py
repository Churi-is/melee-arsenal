"""Offline regressions for the live RE Probe findings and hook lifecycle."""
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk_mods"))
# The runtime package's __init__ requires the game; load its modules offline.
import melee_arsenal  # noqa: E402

runtime = types.ModuleType("melee_arsenal.runtime")
runtime.__path__ = [str(ROOT / "sdk_mods/melee_arsenal/runtime")]
sys.modules.setdefault("melee_arsenal.runtime", runtime)
loot = importlib.import_module("melee_arsenal.runtime.loot_pools")
ui = importlib.import_module("melee_arsenal.runtime.ui_item_card")
from melee_arsenal.core.item_card import CompareArrow  # noqa: E402
from re_probe.probe import trace  # noqa: E402
from re_probe.probe.hook_registration import install  # noqa: E402


class EngineEvidenceTests(unittest.TestCase):
    def test_arrow_mapping(self):
        self.assertEqual([ui._arrow_byte(x) for x in (CompareArrow.NONE, CompareArrow.BETTER, CompareArrow.WORSE, CompareArrow.EQUAL)], [0, 1, 2, 3])

    def test_parser_uses_nested_arrow_enum_path(self):
        from tools.re_probe_parse.enums import report_enums

        class FakeDump:
            dump_dir = "fixture"
            dump_id = "fixture"

            def index_by_path(self, section):
                self_path = "WillowGame.InventoryCardGFx:ETopStatArrow"
                return {self_path: {"path": self_path, "name": "ETopStatArrow", "values": [
                    {"name": "ETSA_Down", "value": 2}, {"name": "ETSA_Same", "value": 3}
                ]}}

        arrow = report_enums(FakeDump())["enums"][0]
        self.assertTrue(arrow["found"])
        self.assertEqual({v["name"]: v["value"] for v in arrow["values"]}["ETSA_Down"], 2)

    def test_weight_exact_names_and_scale(self):
        def prob(name, scale=1):
            return types.SimpleNamespace(InitializationDefinition=types.SimpleNamespace(Name=name), BaseValueScaleConstant=scale)
        self.assertEqual(loot._eval_prob_weight(prob("Weight_1_Common", 2)), 200)
        self.assertEqual(loot._eval_prob_weight(prob("Weight_1_Common_RareMod")), 0)
        self.assertEqual(loot._eval_prob_weight(prob("Weight_2_Uncommon_RareMod")), 0)
        self.assertEqual(loot._eval_prob_weight(prob("Weight_1_Common_unrecognized")), 1)


class FakeHooks:
    def __init__(self):
        self.hooks = {}
        self.fail_post = False

    def has(self, path, kind, name):
        return (path, kind, name) in self.hooks

    def add(self, path, kind, name, callback):
        self.hooks[path, kind, name] = callback
        if self.fail_post and kind == "post":
            raise RuntimeError("failed after inserting")
        return False  # Models SDK adding to an existing collection but returning false.

    def remove(self, path, kind, name):
        return self.hooks.pop((path, kind, name), None) is not None


class TraceLifecycleTests(unittest.TestCase):
    def test_registration_false_after_insert_and_existing_id(self):
        h = FakeHooks()
        self.assertTrue(install(h.add, h.has, h.remove, "f", "pre", "id", lambda: None))
        self.assertFalse(install(h.add, h.has, h.remove, "f", "pre", "id", lambda: None))
        self.assertEqual(len(h.hooks), 1)

    def test_trace_registers_cleans_and_does_not_leak_across_sessions(self):
        h = FakeHooks()
        kind = types.SimpleNamespace(PRE="pre", POST_UNCONDITIONAL="post")
        obj = types.SimpleNamespace(Name="Fire", _path_name=lambda: "WillowGame.WillowWeapon:Fire")
        with patch.multiple(trace, Type=kind, add_hook=h.add, has_hook=h.has, remove_hook=h.remove), patch.object(trace.api, "find_all", return_value=[obj]):
            tracer = trace.CallTracer()
            self.assertTrue(tracer.arm(["*:Fire"])["ok"])
            tracer.tick()
            self.assertEqual(tracer.status()["armed_hooks"], 1)
            self.assertEqual(len(h.hooks), 2)
            tracer.stop(write=False)
            self.assertFalse(h.hooks)
            self.assertTrue(tracer.arm(["*:Fire"])["ok"])
            tracer.tick()
            tracer.stop(write=False)
            self.assertFalse(h.hooks)

    def test_post_exception_rolls_back_both_hooks(self):
        h = FakeHooks()
        h.fail_post = True
        kind = types.SimpleNamespace(PRE="pre", POST_UNCONDITIONAL="post")
        obj = types.SimpleNamespace(Name="Fire", _path_name=lambda: "WillowGame.WillowWeapon:Fire")
        with patch.multiple(trace, Type=kind, add_hook=h.add, has_hook=h.has, remove_hook=h.remove), patch.object(trace.api, "find_all", return_value=[obj]):
            tracer = trace.CallTracer()
            tracer.arm(["*:Fire"])
            tracer.tick()
            self.assertEqual(tracer.status()["errors"], 1)
            self.assertFalse(h.hooks)


if __name__ == "__main__":
    unittest.main()
