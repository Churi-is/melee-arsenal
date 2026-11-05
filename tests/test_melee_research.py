import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "sdk_mods/melee_research/report.py"
spec = importlib.util.spec_from_file_location("research_report", path)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class EvidenceTests(unittest.TestCase):
    def test_manifest_selection_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {"status": "complete", "sections": {"structs": {"shards": [{"path": "structs-0000.json"}, {"path": "missing.json"}]}}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            (root / "structs-0000.json").write_text(json.dumps({"section": "structs", "items": [{"path": "WillowGame.WeaponDefinitionData", "properties": [{"name": "UniqueId"}]}, {"path": "Other"}]}))
            result = report.build_report(root, [{"scenario": "combat"}], [])
            self.assertEqual(result["sections"]["structs"]["matched"], 1)
            self.assertEqual(result["sections"]["structs"]["items"][0]["properties"][0]["name"], "UniqueId")
            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(result["traces"][0]["scenario"], "combat")
            out = root / "result.json"
            report.write_report(out, result)
            self.assertEqual(json.loads(out.read_text())["manifest"]["status"], "complete")

    def test_cap_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(json.dumps({"sections": {"enums": {"shards": [{"path": "enums.json"}]}}}))
            (root / "enums.json").write_text(json.dumps({"section": "enums", "items": [{"path": f"ETopStatArrow{i}"} for i in range(55)]}))
            section = report.build_report(root, [], [])["sections"]["enums"]
            self.assertEqual(section["matched"], 55)
            self.assertEqual(section["included"], 50)
            self.assertTrue(section["truncated"])


if __name__ == "__main__":
    unittest.main()
