from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
