from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from usit.controlled_pytest import AUTH_SCHEMA, run_controlled_pytest
from usit.verification_capture import verify_manifest


class ControlledPytestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="usit-controlled-pytest-")
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.evidence = self.base / "evidence"
        self.project.mkdir()
        self.evidence.mkdir()
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_smoke.py").write_text(
            "def test_snapshot_execution():\n    assert 2 + 2 == 4\n",
            encoding="utf-8",
        )
        self.git("init", "-q")
        self.git("config", "user.email", "usit-tests@example.invalid")
        self.git("config", "user.name", "USIT Test")
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture")
        now = datetime.now(timezone.utc)
        self.authorization = self.base / "authorization.json"
        self.authorization.write_text(json.dumps({
            "schema_version": AUTH_SCHEMA,
            "project_root": str(self.project.resolve()),
            "evidence_parent": str(self.evidence.resolve()),
            "allowed_actions": ["run_pytest_on_git_snapshot"],
            "acknowledges_project_code_execution": True,
            "approval_ref": "test-authorization-001",
            "authorized_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.project), *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_runs_pytest_from_staged_git_snapshot_and_binds_evidence(self):
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["provenance_authenticity_status"], "UNAUTHENTICATED")
        self.assertEqual(result["execution_source_binding_status"], "STAGED_SNAPSHOT_USED_UNAUTHENTICATED")
        evidence = Path(result["evidence_dir"])
        self.assertTrue(verify_manifest(evidence)["valid"])
        junit = ET.parse(evidence / "junit.xml").getroot()
        testcase = junit.find(".//testcase")
        properties = {
            item.attrib.get("name"): item.attrib.get("value")
            for item in testcase.findall("./properties/property")
        }
        self.assertEqual(properties.get("usit_source_file"), "tests/test_smoke.py")
        self.assertEqual(properties.get("usit_source_line"), "1")
        self.assertEqual(properties.get("usit_source_symbol"), "test_snapshot_execution")
        self.assertEqual(properties.get("usit_nodeid"), "tests/test_smoke.py::test_snapshot_execution")
        self.assertTrue(Path(result["runner_plugin"]).is_file())
        snapshot_root = Path(result["source_snapshot_root"])
        self.assertEqual(
            (snapshot_root / "tests" / "test_smoke.py").read_bytes(),
            (self.project / "tests" / "test_smoke.py").read_bytes(),
        )
        provenance = json.loads((evidence / "execution-provenance.json").read_text(encoding="utf-8"))
        manifest = json.loads((evidence / "source-manifest.json").read_text(encoding="utf-8"))
        report = (evidence / "junit.xml").read_bytes()
        from usit.test_execution_provenance import validate_execution_provenance
        checked = validate_execution_provenance(
            json.dumps(provenance).encode(), report,
            json.dumps(manifest).encode(),
        )
        self.assertEqual(checked["provenance_authenticity_status"], "UNAUTHENTICATED")
        self.assertEqual(provenance["source_snapshot"]["capture_method"], "GIT_ARCHIVE_HEAD_STAGED_COPY")
        self.assertEqual(result["git_commit"], self.git("rev-parse", "HEAD").stdout.decode().strip())

    def test_dirty_worktree_is_refused_before_test_execution(self):
        (self.project / "tests" / "test_smoke.py").write_text("def test_changed(): assert True\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "worktree is not clean"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
            )
        self.assertEqual(list(self.evidence.iterdir()), [])

    def test_untracked_files_are_refused(self):
        (self.project / "untracked.py").write_text("secret = True\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "worktree is not clean"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
            )

    def test_execution_requires_explicit_acknowledgement(self):
        value = json.loads(self.authorization.read_text(encoding="utf-8"))
        value["acknowledges_project_code_execution"] = False
        self.authorization.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "acknowledge execution"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
            )

    def test_reserved_pytest_report_options_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "reserved by USIT"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
                pytest_args=["--junitxml=attacker.xml"],
            )


    def test_failed_pytest_keeps_report_and_never_passes_gate(self):
        (self.project / "tests" / "test_smoke.py").write_text(
            "def test_failure():\n    assert False, 'expected fixture failure'\n",
            encoding="utf-8",
        )
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "make fixture test fail")
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(Path(result["pytest_report"]).is_file())
        self.assertTrue(result["evidence_manifest_valid"])
        self.assertEqual(result["provenance_authenticity_status"], "UNAUTHENTICATED")

    def test_source_mutation_during_test_fails_gate_and_preserves_original_snapshot(self):
        source = self.project / "tests" / "test_smoke.py"
        original = (
            "from pathlib import Path\n"
            "def test_mutates_staged_source():\n"
            "    Path(__file__).write_text('def test_replaced(): assert True\\\\n')\n"
        )
        source.write_text(original, encoding="utf-8")
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture attempts source mutation")
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "FAIL")
        snapshot = Path(result["source_snapshot_root"]) / "tests" / "test_smoke.py"
        self.assertEqual(snapshot.read_text(encoding="utf-8"), original)
        self.assertTrue(result["evidence_manifest_valid"])


    def test_untracked_file_created_during_test_fails_gate(self):
        source = self.project / "tests" / "test_smoke.py"
        source.write_text(
            "from pathlib import Path\n"
            "def test_creates_untracked_file():\n"
            "    Path('generated-during-test.py').write_text('not in source manifest')\n",
            encoding="utf-8",
        )
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture creates an untracked file")
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["source_tree_inventory_status"], "CHANGED_OR_UNSAFE")
        self.assertTrue(any("generated-during-test.py" in item
                            for item in result["source_tree_inventory_errors"]))
        self.assertTrue(result["evidence_manifest_valid"])


if __name__ == "__main__":
    unittest.main()
