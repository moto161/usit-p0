from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from usit.ai_evidence_binding import verify_evidence_bindings


class AIEvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "src" / "photo.py"
        source.parent.mkdir()
        source.write_text("def view():\n    return True\n", encoding="utf-8")
        report = self.root / "test-results.xml"
        report.write_text(
            '<testsuite><testcase classname="PhotoTests" name="test_owner">'
            '</testcase></testsuite>', encoding="utf-8"
        )
        source_bytes = source.read_bytes()
        report_bytes = report.read_bytes()
        self.ledger = {
            "schema": "usit-ai-claim-ledger-v1",
            "evidence_catalog": [
                {
                    "evidence_id": "src",
                    "evidence_type": "SOURCE_CODE",
                    "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "locator": "src/photo.py:2",
                    "artifact_path": "src/photo.py",
                },
                {
                    "evidence_id": "test",
                    "evidence_type": "TEST_RESULT",
                    "source_sha256": hashlib.sha256(report_bytes).hexdigest(),
                    "locator": "test-results.xml#PhotoTests::test_owner",
                    "artifact_path": "test-results.xml",
                    "expected_test_status": "PASSED",
                },
            ],
            "claims": [{
                "claim_id": "claim-1",
                "model_id": "model-a",
                "task": "review",
                "claim_text": "The source and test evidence are available.",
                "evidence_refs": ["src", "test"],
                "generated_at": "2026-11-01T12:00:00+08:00",
            }],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_source_and_junit_bytes_and_locators_resolve(self):
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["binding_status_counts"], {
            "ARTIFACT_HASH_MATCH_AND_LOCATOR_VALID": 2
        })
        self.assertEqual(
            result["claims"][0]["binding_status"],
            "ALL_BINDINGS_RESOLVED_NOT_AUTHENTICATED",
        )
        self.assertEqual(result["overall_truth_status"], "NOT_ESTABLISHED")
        self.assertEqual(result["claims"][0]["truth_status"], "NOT_ESTABLISHED")

    def test_hash_mismatch_is_stale(self):
        self.ledger["evidence_catalog"][0]["source_sha256"] = "0" * 64
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][0]["binding_status"], "STALE")
        self.assertEqual(result["claims"][0]["binding_status"], "STALE")

    def test_missing_artifact_is_missing(self):
        self.ledger["evidence_catalog"][0]["artifact_path"] = "src/missing.py"
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][0]["binding_status"], "MISSING")

    def test_invalid_source_line_is_rejected(self):
        self.ledger["evidence_catalog"][0]["locator"] = "src/photo.py:99"
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][0]["binding_status"], "INVALID_LOCATOR")

    def test_test_outcome_mismatch_is_conflict(self):
        self.ledger["evidence_catalog"][1]["expected_test_status"] = "FAILED"
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][1]["binding_status"], "CONFLICT")
        self.assertEqual(result["claims"][0]["binding_status"], "CONFLICT")

    def test_legacy_catalog_entry_without_artifact_path_is_unverified(self):
        del self.ledger["evidence_catalog"][0]["artifact_path"]
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][0]["binding_status"], "UNVERIFIED_REFERENCE")
        self.assertEqual(result["claims"][0]["binding_status"], "UNVERIFIED_REFERENCE")

    def test_unknown_evidence_id_is_not_silently_accepted(self):
        self.ledger["claims"][0]["evidence_refs"].append("not-in-catalog")
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["claims"][0]["binding_status"], "UNKNOWN_REFERENCE")
        self.assertEqual(result["claims"][0]["unknown_evidence_refs"], ["not-in-catalog"])

    def test_path_traversal_and_symlink_are_rejected(self):
        self.ledger["evidence_catalog"][0]["artifact_path"] = "../outside.py"
        with self.assertRaisesRegex(ValueError, "traversal"):
            verify_evidence_bindings(self.ledger, self.root)
        outside = self.root / "outside.py"
        outside.write_text("x\n", encoding="utf-8")
        link = self.root / "link.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.ledger["evidence_catalog"][0]["artifact_path"] = "link.py"
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            verify_evidence_bindings(self.ledger, self.root)


if __name__ == "__main__":
    unittest.main()
