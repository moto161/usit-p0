from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from usit.controlled_pytest import (
    AUTH_SCHEMA, MAX_REPORT_BYTES, MAX_RUNTIME_IMPORTS_BYTES,
    _read_execution_artifact, run_controlled_pytest,
)
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

    def test_controlled_run_signs_provenance_and_manifest_with_hmac(self):
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["provenance_signature_status"], "VALID")
        self.assertEqual(result["evidence_manifest_signature_status"], "VALID")
        # Runner authenticity is unchanged by signing: the signature proves
        # the parent produced the documents, not that the tests executed.
        self.assertEqual(result["provenance_authenticity_status"], "UNAUTHENTICATED")
        evidence = Path(result["evidence_dir"])
        provenance = json.loads((evidence / "execution-provenance.json").read_text(encoding="utf-8"))
        manifest = json.loads((evidence / "evidence_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["signature"]["algorithm"], "HMAC-SHA256")
        self.assertEqual(manifest["signature"]["algorithm"], "HMAC-SHA256")
        self.assertEqual(
            provenance["signature"]["key_fingerprint"],
            manifest["signature"]["key_fingerprint"],
        )
        # The key file lives OUTSIDE the evidence dir, mode 0600.
        key_path = Path(result["hmac_key_file"])
        self.assertTrue(key_path.is_file())
        self.assertNotEqual(key_path.parent.resolve(), evidence.resolve())
        self.assertEqual(key_path.parent.resolve(), self.evidence.resolve())
        self.assertEqual(stat.S_IMODE(os.stat(key_path).st_mode), 0o600)
        # Post-hoc verification with the key file re-checks both signatures
        # and every manifest artifact, without the parent process.
        from usit.evidence_signing import read_key_file, verify_evidence_directory

        run_id, key = read_key_file(key_path)
        self.assertEqual(run_id, Path(result["evidence_dir"]).name)
        checked = verify_evidence_directory(evidence, key)
        self.assertEqual(checked["provenance_signature_status"], "VALID")
        self.assertEqual(checked["evidence_manifest_signature_status"], "VALID")
        self.assertTrue(checked["manifest_valid"])
        # Tampering with the provenance after the run is caught post-hoc.
        provenance["execution_id"] = "run-forged"
        (evidence / "execution-provenance.json").write_text(
            json.dumps(provenance), encoding="utf-8"
        )
        checked = verify_evidence_directory(evidence, key)
        self.assertEqual(checked["provenance_signature_status"], "INVALID")

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

    def test_execution_authorization_rejects_oversized_file(self):
        with self.authorization.open("wb") as stream:
            stream.truncate(16 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "size limit"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
            )
        self.assertEqual(list(self.evidence.iterdir()), [])

    def test_execution_authorization_rejects_symlink(self):
        link = self.base / "authorization-link.json"
        try:
            link.symlink_to(self.authorization)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "absolute regular file"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=link,
            )
        self.assertEqual(list(self.evidence.iterdir()), [])

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

    def test_deleted_staged_import_is_still_recorded_by_audit_hook(self):
        package = self.project / "audit_pkg"
        package.mkdir()
        (package / "__init__.py").write_text("VALUE = 'staged'\n", encoding="utf-8")
        self.git("add", "audit_pkg/__init__.py")
        self.git("commit", "-q", "-m", "add audit package fixture")
        source = self.project / "tests" / "test_smoke.py"
        source.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
            "import audit_pkg\n"
            "def test_import_then_delete_from_sys_modules():\n"
            "    assert audit_pkg.VALUE == 'staged'\n"
            "    del sys.modules['audit_pkg']\n",
            encoding="utf-8",
        )
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture deletes staged import mid-run")
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["runtime_import_binding_status"], "RUNTIME_IMPORTS_MATCHED_STAGED_SNAPSHOT", result)
        runtime_doc = json.loads(Path(result["runtime_imports_manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(runtime_doc["audit_hook"], "ACTIVE")
        self.assertGreaterEqual(runtime_doc["audit_hook_only_module_count"], 1)
        self.assertTrue(any(
            item["module"] == "audit_pkg" and item["status"] == "STAGED_SOURCE"
            for item in runtime_doc["modules"]
        ))

    def test_deleted_outside_import_is_still_reported_outside_snapshot(self):
        # Without the audit hook this import would vanish from the
        # session-end sys.modules snapshot and the gate would wrongly pass.
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
            "def test_imports_then_deletes_outside_module():\n"
            "    assert shadow_pkg.VALUE == 'original-worktree'\n"
            "    del sys.modules['shadow_pkg']\n",
            encoding="utf-8",
        )
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture deletes outside import mid-run")
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

    def test_deleted_import_is_caught_even_when_finder_is_removed(self):
        # The meta path finder can be removed by test code, but the
        # append-only audit hook still has the module name, so sessionfinish
        # resolves it again and the outside import is still reported.
        package = self.project / "shadow_pkg"
        package.mkdir()
        (package / "__init__.py").write_text("VALUE = 'original-worktree'\n", encoding="utf-8")
        self.git("add", "shadow_pkg/__init__.py")
        self.git("commit", "-q", "-m", "add shadow package fixture")
        source = self.project / "tests" / "test_smoke.py"
        source.write_text(
            "import sys\n"
            "def test_imports_after_removing_finder_then_deletes():\n"
            f"    sys.path.insert(0, {str(self.project)!r})\n"
            "    sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != '_UsitImportRecorder']\n"
            "    import shadow_pkg\n"
            "    assert shadow_pkg.VALUE == 'original-worktree'\n"
            "    del sys.modules['shadow_pkg']\n",
            encoding="utf-8",
        )
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture removes finder then deletes outside import")
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


    def test_modify_execute_restore_is_detected_by_ctime(self):
        """Modify-then-restore must fail the gate even with bytes+mtime restored.

        A test that rewrites an already-imported source file, executes the
        modified code, then restores the original bytes and mtime (os.utime)
        leaves no content trace -- but POSIX ctime cannot be reset by a
        non-privileged process, so the pre/post snapshot diff must catch it.

        POSIX-only: on Windows st_ctime is the file creation time, which a
        write does not change, so this detection does not apply there.
        """
        import sys
        if sys.platform == "win32":
            self.skipTest("POSIX ctime does not exist on Windows (st_ctime is creation time)")
        impl = self.project / "impl.py"
        impl.write_text("VALUE = 'original'\n", encoding="utf-8")
        source = self.project / "tests" / "test_smoke.py"
        source.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "import impl\n"
            "def test_modify_execute_restore():\n"
            "    target = Path(impl.__file__)\n"
            "    original = target.read_bytes()\n"
            "    stat = target.stat()\n"
            "    target.write_bytes(b\"VALUE = 'tampered'\\n\")\n"
            "    assert impl.VALUE == 'original'\n"
            "    target.write_bytes(original)\n"
            "    os.utime(target, (stat.st_atime_ns, stat.st_mtime_ns), ns=(stat.st_atime_ns, stat.st_mtime_ns))\n",
            encoding="utf-8",
        )
        self.git("add", "impl.py", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture modifies and restores source")
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "FAIL", result)
        gate = json.loads((Path(result["evidence_dir"]) / "final_gate_report.json").read_text(encoding="utf-8"))
        self.assertTrue(gate["changes"])
        self.assertTrue(any(
            change["path"] == "impl.py" for change in gate["changes"]
        ))

    def test_forged_junit_counts_fail_terminal_summary_cross_check(self):
        """A JUnit file rewritten after pytest must not survive the cross-check.

        The fixture test really passes (exit code 0), but a conftest
        sessionfinish hook overwrites the JUnit report claiming two passed
        tests instead of one. The parent-captured stdout summary still says
        "1 passed", so the counts disagree and the gate must fail.
        """
        conftest = self.project / "tests" / "conftest.py"
        conftest.write_text(
            "import pytest\n"
            "from pathlib import Path\n"
            "@pytest.hookimpl(trylast=True)\n"
            "def pytest_sessionfinish(session, exitstatus):\n"
            "    junit = Path(session.config.rootpath) / '.usit-junit.xml'\n"
            "    junit.write_text(\n"
            "        '<?xml version=\"1.0\" encoding=\"utf-8\"?>'\n"
            "        '<testsuites><testsuite name=\"pytest\" tests=\"2\" failures=\"0\" errors=\"0\" skipped=\"0\">'\n"
            "        '<testcase classname=\"tests.test_smoke\" name=\"test_snapshot_execution\" />'\n"
            "        '<testcase classname=\"tests.test_smoke\" name=\"test_phantom\" />'\n"
            "        '</testsuite></testsuites>', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self.git("add", "tests/conftest.py")
        self.git("commit", "-q", "-m", "fixture forges junit after pytest")
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "FAIL", result)
        self.assertTrue(any(
            "JUnit counts disagree with pytest terminal summary" in item
            for item in result["source_tree_inventory_errors"]
        ))

    def test_staging_parent_does_not_expose_evidence_dir(self):
        """The test process must not see the evidence run directory via '..'.

        Staging is created in the system temp root, not inside
        evidence_parent, so listing the staging parent must not reveal any
        run_* evidence directory.
        """
        source = self.project / "tests" / "test_smoke.py"
        source.write_text(
            "from pathlib import Path\n"
            "def test_parent_has_no_evidence_run_dir():\n"
            "    entries = {p.name for p in Path.cwd().parent.iterdir()}\n"
            "    assert not any(name.startswith('run_') for name in entries)\n",
            encoding="utf-8",
        )
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture checks staging parent isolation")
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "PASS", result)

    def test_provenance_records_parent_observed_exit_code(self):
        """Provenance must carry the parent-observed pytest exit code."""
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
        )
        self.assertEqual(result["status"], "PASS", result)
        provenance = json.loads(
            (Path(result["evidence_dir"]) / "execution-provenance.json").read_text(encoding="utf-8")
        )
        self.assertEqual(provenance["pytest_exit_code"], 0)
        self.assertEqual(provenance["pytest_exit_status"], "PASS")

    def test_execution_artifact_reader_enforces_size_limit(self):
        artifact = self.base / "oversized-artifact.bin"
        artifact.write_bytes(b"12345")
        with self.assertRaisesRegex(ValueError, "size limit"):
            _read_execution_artifact(artifact, 4, "test artifact")
        self.assertEqual(MAX_REPORT_BYTES, 50 * 1024 * 1024)
        self.assertEqual(MAX_RUNTIME_IMPORTS_BYTES, 50 * 1024 * 1024)

    def test_execution_artifact_reader_rejects_symlink(self):
        target = self.base / "artifact-target.bin"
        target.write_bytes(b"safe")
        link = self.base / "artifact-link.bin"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "bounded regular file"):
            _read_execution_artifact(link, 1024, "test artifact")


    def _runner_user_for_isolation(self):
        """Return the dedicated runner user name, or None when isolation tests must skip.

        UID isolation integration tests need a root parent and an existing
        user named by USIT_TEST_RUNNER_USER. CI runs without the variable,
        so the tests skip there instead of failing.
        """
        geteuid = getattr(os, "geteuid", lambda: -1)
        if geteuid() != 0:
            return None
        name = os.environ.get("USIT_TEST_RUNNER_USER")
        if not name:
            return None
        try:
            import pwd
            pwd.getpwnam(name)
        except KeyError:
            return None
        return name

    def _write_authorization(self, runner_user=None):
        now = datetime.now(timezone.utc)
        auth = {
            "schema_version": AUTH_SCHEMA,
            "project_root": str(self.project.resolve()),
            "evidence_parent": str(self.evidence.resolve()),
            "allowed_actions": ["run_pytest_on_git_snapshot"],
            "acknowledges_project_code_execution": True,
            "approval_ref": "test-authorization-001",
            "authorized_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        }
        if runner_user is not None:
            auth["runner_user"] = runner_user
        self.authorization.write_text(json.dumps(auth), encoding="utf-8")

    def test_isolated_run_passes_and_records_runner_isolation(self):
        runner_user = self._runner_user_for_isolation()
        if runner_user is None:
            self.skipTest("UID isolation integration tests need root and USIT_TEST_RUNNER_USER pointing at an existing user")
        import pwd
        expected_uid = pwd.getpwnam(runner_user).pw_uid
        self._write_authorization(runner_user=runner_user)
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
            runner_user=runner_user,
        )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["runner_isolation"], f"POSIX_USER:{runner_user}")
        self.assertEqual(result["provenance_signature_status"], "VALID")
        self.assertEqual(result["evidence_manifest_signature_status"], "VALID")
        # Isolation does not authenticate the runner: the signature proves
        # the parent produced the documents, not that the tests executed.
        self.assertEqual(result["provenance_authenticity_status"], "UNAUTHENTICATED")
        self.assertEqual(result["capture"]["runner_user"], runner_user)
        self.assertEqual(result["capture"]["runner_uid"], expected_uid)
        evidence = Path(result["evidence_dir"])
        provenance = json.loads((evidence / "execution-provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["runner_isolation"], f"POSIX_USER:{runner_user}")
        gate = json.loads((evidence / "final_gate_report.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["runner_isolation"], f"POSIX_USER:{runner_user}")
        self.assertTrue(any("dedicated POSIX user" in item for item in gate["limitations"]))

    def test_isolated_test_process_cannot_write_original_project_tree(self):
        runner_user = self._runner_user_for_isolation()
        if runner_user is None:
            self.skipTest("UID isolation integration tests need root and USIT_TEST_RUNNER_USER pointing at an existing user")
        import pwd
        expected_uid = pwd.getpwnam(runner_user).pw_uid
        self._write_authorization(runner_user=runner_user)
        source = self.project / "tests" / "test_smoke.py"
        source.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "def test_isolated_uid_and_original_tree_readonly():\n"
            f"    assert os.getuid() == {expected_uid}\n"
            "    assert os.getuid() != 0\n"
            "    original = Path(os.environ['USIT_ORIGINAL_PROJECT_ROOT'])\n"
            "    target = original / 'tests' / 'test_smoke.py'\n"
            "    try:\n"
            "        target.write_text('tampered')\n"
            "    except PermissionError:\n"
            "        pass\n"
            "    else:\n"
            "        raise AssertionError('test process could write the original project tree')\n",
            encoding="utf-8",
        )
        self.git("add", "tests/test_smoke.py")
        self.git("commit", "-q", "-m", "fixture proves isolation uid and original tree readonly")
        before = source.read_bytes()
        result = run_controlled_pytest(
            self.project, evidence_parent=self.evidence,
            authorization_file=self.authorization,
            pytest_args=["tests/test_smoke.py"], timeout=60,
            runner_user=runner_user,
        )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(source.read_bytes(), before)

    def test_runner_user_authorization_mismatch_is_rejected(self):
        runner_user = self._runner_user_for_isolation()
        if runner_user is None:
            self.skipTest("UID isolation integration tests need root and USIT_TEST_RUNNER_USER pointing at an existing user")
        # Caller requests isolation but the authorization file names no runner user.
        with self.assertRaisesRegex(ValueError, "runner_user does not match"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
                pytest_args=["tests/test_smoke.py"], timeout=60,
                runner_user=runner_user,
            )
        # Authorization names a different runner user than the caller.
        self._write_authorization(runner_user="somebody-else")
        with self.assertRaisesRegex(ValueError, "runner_user does not match"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
                pytest_args=["tests/test_smoke.py"], timeout=60,
                runner_user=runner_user,
            )
        # Authorization demands isolation but the caller passes none.
        self._write_authorization(runner_user=runner_user)
        with self.assertRaisesRegex(ValueError, "runner_user does not match"):
            run_controlled_pytest(
                self.project, evidence_parent=self.evidence,
                authorization_file=self.authorization,
                pytest_args=["tests/test_smoke.py"], timeout=60,
            )
        # A non-root parent cannot request isolation even with matching authorization.
        with patch.object(os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(ValueError, "run as root"):
                run_controlled_pytest(
                    self.project, evidence_parent=self.evidence,
                    authorization_file=self.authorization,
                    pytest_args=["tests/test_smoke.py"], timeout=60,
                    runner_user=runner_user,
                )


if __name__ == "__main__":
    unittest.main()
