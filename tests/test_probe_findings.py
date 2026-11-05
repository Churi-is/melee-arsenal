"""Regression tests against overstating RE Probe findings."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.re_probe_parse.report import build_findings


class FindingsTests(unittest.TestCase):
    def test_limited_evidence_does_not_confirm_runtime_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            functions = [
                {"path": "WillowGame.WillowWeapon:InstantFire", "params": []},
                {"path": "WillowGame.WillowPlayerController:PlayerTick", "params": []},
            ]
            (root / "manifest.json").write_text(json.dumps({"status": "complete", "sections": {
                "functions": {"shards": [{"path": "functions-0000.json"}]}}}))
            (root / "dump.json").write_text("{}")
            (root / "functions-0000.json").write_text(json.dumps({"items": functions}))
            trace_path = root / "trace.json"
            trace_path.write_text(json.dumps({"session": {"stack_drift": 0, "errors": 0, "dropped_edges": 0},
                "functions": [{"path": "WillowGame.WillowWeapon:InstantFire", "calls": 10}], "edges": []}))
            report = build_findings(str(root), str(trace_path))
            findings = {x["id"]: x for x in report["findings"]}
            self.assertEqual(findings["HOOK-INSTANTFIRE"]["status"], "PARTIAL")
            self.assertEqual(findings["HOOK-PLAYERTICK"]["status"], "PARTIAL")
            self.assertEqual(findings["PROP-WEAPONNODECL"]["status"], "UNVERIFIED")
            self.assertEqual(findings["VALUE-CLASS-SUM"]["status"], "UNVERIFIED")
            self.assertNotIn("trace not supplied", " ".join(findings["TRACE-CALLGRAPH"]["evidence"]).lower())


if __name__ == "__main__":
    unittest.main()
