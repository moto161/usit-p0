from __future__ import annotations

import json
import hashlib
import os
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from usit.controlled_pytest import AUTH_SCHEMA, run_controlled_pytest
from usit.verification_capture import verify_manifest
from usit.test_result_importer import ingest_junit_results


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
            "import os\n"
            "def test_snapshot_execution():\n"
            "    assert 2 + 2 == 4\n"
            "    assert 'USIT_TEST_SECRET' not in os.environ\n"
            "    assert os.environ.get('PYTHONDONTWRITEBYTECODE') == '1'\n",
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
        with patch.dict(os.environ, {"USIT_TEST_SECRET": "must-not-reach-child"}):
            result = run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
                pytest_args=["tests/test_smoke.py"], timeout=60,
            )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["provenance_authenticity_status"], "UNAUTHENTICATED")
        self.assertEqual(result["execution_source_binding_status"], "STAGED_SNAPSHOT_USED_UNAUTHENTICATED")
        self.assertEqual(result["runtime_import_binding_status"], "RUNTIME_IMPORTS_MATCHED_STAGED_SNAPSHOT", result)
        runtime_path = Path(result["runtime_imports_manifest"])
        runtime_doc = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime_doc["schema_version"], "usit-runtime-imports-v1")
        self.assertGreater(runtime_doc["staged_module_count"], 0)
        self.assertEqual(runtime_doc["outside_snapshot_module_count"], 0)
        evidence = Path(result["evidence_dir"])
        self.assertTrue(verify_manifest(evidence)["valid"])
        junit = ET.parse(evidence / "junit.xml").getroot()
        testcase = junit.find(".//testcase")
        properties = {
            item.attrib.get("name"): item.attrib.get("value")
            for item in testcase.findall("./properties/property")
        }
        self.assertEqual(properties.get("usit_source_file"), "tests/test_smoke.py")
        self.assertEqual(properties.get("usit_source_line"), "2")
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
        with patch.dict(os.environ, {"USIT_TEST_SECRET": "must-not-reach-child"}):
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
        with patch.dict(os.environ, {"USIT_TEST_SECRET": "must-not-reach-child"}):
            result = run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
                pytest_args=["tests/test_smoke.py"], timeout=60,
            )
        self.assertEqual(result["status"], "FAIL")
        snapshot = Path(result["source_snapshot_root"]) / "tests" / "test_smoke.py"
        self.assertEqual(snapshot.read_text(encoding="utf-8"), original)
        self.assertTrue(result["evidence_manifest_valid"])


    def test_project_module_loaded_from_original_worktree_fails_runtime_binding(self):
        package = self.project / "shadow_pkg"
        package.mkdir()
        (package / "__init__.py").write_text("VALUE = 'original-worktree'\n", encoding="utf-8")
        self.git("add", "shadow_pkg/__init__.py")
        self.git("commit", "-q", "-m", "add shadow package fixture")
        source = self.project / "tests" / "test_smoke.py"
        source.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(self.project)!r})\n"
            "import shadow_pkg\n"
            "def test_imports_project_module_from_original_worktree():\n"
            "    assert shadow_pkg.VALUE == 'original-worktree'\n",
            encoding="utf-8",
        )
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture imports project module outside staged snapshot")
        with patch.dict(os.environ, {"USIT_TEST_SECRET": "must-not-reach-child"}):
            result = run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
                pytest_args=["tests/test_smoke.py"], timeout=60,
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["runtime_import_binding_status"], "RUNTIME_IMPORTS_NOT_BOUND")
        runtime_doc = json.loads(Path(result["runtime_imports_manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(runtime_doc["outside_snapshot_module_count"], 1)
        self.assertTrue(any(
            item["module"] == "shadow_pkg" and item["status"] == "OUTSIDE_SNAPSHOT"
            for item in runtime_doc["modules"]
        ))

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
        with patch.dict(os.environ, {"USIT_TEST_SECRET": "must-not-reach-child"}):
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


    def test_runner_bundle_imports_through_snapshot_and_business_mapping(self):
        with patch.dict(os.environ, {"USIT_TEST_SECRET": "must-not-reach-child"}):
            result = run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
                pytest_args=["tests/test_smoke.py"], timeout=60,
            )
        self.assertEqual(result["status"], "PASS", result)
        evidence = Path(result["evidence_dir"])
        snapshot = Path(result["source_snapshot_root"])
        report_root = ET.parse(evidence / "junit.xml").getroot()
        testcase = report_root.find(".//testcase")
        case_id = testcase.attrib.get("classname", "") + "::" + testcase.attrib["name"]
        source_path = snapshot / "tests" / "test_smoke.py"
        graph = {
            "schema_version": "usit-graph-v1",
            "business_understanding": {
                "business_function_views": [
                    {"business_function_id": "bf-smoke", "endpoint_id": "ep-smoke"}
                ]
            },
        }
        graph_path = self.base / "business_graph.json"
        graph_bytes = json.dumps(graph).encode("utf-8")
        graph_path.write_bytes(graph_bytes)
        source_manifest = json.loads((evidence / "source-manifest.json").read_text(encoding="utf-8"))
        source_entry = next(item for item in source_manifest["files"]
                            if item["path"] == "tests/test_smoke.py")
        mapping = {
            "schema_version": "usit-test-result-mapping-v1",
            "source_graph_sha256": hashlib.sha256(graph_bytes).hexdigest(),
            "mappings": [{
                "test_case_id": case_id,
                "business_function_id": "bf-smoke",
                "test_source_file": "tests/test_smoke.py",
                "test_source_line": 2,
                "test_source_file_sha256": source_entry["sha256"],
                "test_source_symbol": "test_snapshot_execution",
                "pytest_nodeid": "tests/test_smoke.py::test_snapshot_execution",
            }],
        }
        mapping_path = self.base / "mapping.json"
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
        imported = ingest_junit_results(
            evidence / "junit.xml", mapping_path, graph_path,
            project_root=snapshot,
            provenance_record_path=evidence / "execution-provenance.json",
            source_manifest_path=evidence / "source-manifest.json",
            evidence_dir=evidence,
        )
        self.assertEqual(
            imported["evidence_bundle_validation"]["status"],
            "EVIDENCE_BUNDLE_AND_SOURCE_SNAPSHOT_HASHES_VALIDATED",
        )
        self.assertEqual(
            imported["evidence_bundle_validation"]["runtime_import_validation"]["status"],
            "RUNTIME_IMPORTS_MATCHED_STAGED_SNAPSHOT",
        )
        smoke = next(item for item in imported["business_function_results"]
                     if item["business_function_id"] == "bf-smoke")
        mapped = smoke["mapped_test_cases"][0]
        self.assertEqual(smoke["execution_status"], "PASSED")
        self.assertEqual(mapped["test_source_snapshot_binding_status"], "TEST_SOURCE_SNAPSHOT_ENTRY_MATCHED")
        self.assertEqual(mapped["test_source_symbol_evidence"]["status"], "TEST_SOURCE_SYMBOL_DECLARATION_MATCHED")
        self.assertEqual(mapped["test_source_report_locator_status"], "REPORT_SOURCE_LOCATOR_MATCHED_UNAUTHENTICATED")
        self.assertEqual(imported["execution_provenance"]["provenance_authenticity_status"], "UNAUTHENTICATED")
        runtime_artifact = evidence / "runtime-imports.json"
        original_runtime_bytes = runtime_artifact.read_bytes()
        runtime_artifact.write_bytes(original_runtime_bytes + b" ")
        with self.assertRaisesRegex(ValueError, "evidence bundle manifest validation failed"):
            ingest_junit_results(
                evidence / "junit.xml", mapping_path, graph_path,
                project_root=snapshot,
                provenance_record_path=evidence / "execution-provenance.json",
                source_manifest_path=evidence / "source-manifest.json",
                evidence_dir=evidence,
            )
        runtime_artifact.write_bytes(original_runtime_bytes)
        (snapshot / "tests" / "test_smoke.py").write_text("tampered after execution\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "evidence bundle manifest validation failed"):
            ingest_junit_results(
                evidence / "junit.xml", mapping_path, graph_path,
                project_root=snapshot,
                provenance_record_path=evidence / "execution-provenance.json",
                source_manifest_path=evidence / "source-manifest.json",
                evidence_dir=evidence,
            )


if __name__ == "__main__":
    unittest.main()
