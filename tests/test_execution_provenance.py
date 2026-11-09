from __future__ import annotations

import json
import unittest

from usit.test_execution_provenance import (
    SCHEMA_VERSION,
    canonical_json_bytes,
    sha256_hex,
    validate_execution_provenance,
)


class TestExecutionProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.report = b'{"tests":[{"nodeid":"tests/test_x.py::test_ok","outcome":"passed"}]}'
        self.manifest = {"schema_version": "usit-manifest-v1", "files": [
            {"path": "tests/test_x.py", "sha256": "a" * 64}
        ]}
        self.manifest_bytes = json.dumps(self.manifest).encode()
        self.record = {
            "schema_version": SCHEMA_VERSION,
            "execution_id": "run-001",
            "started_at": "2026-11-03T10:00:00Z",
            "completed_at": "2026-11-03T10:00:01Z",
            "runner": {"name": "pytest", "version": "8.3.5"},
            "report_format": "PYTEST_JSON",
            "report_relative_path": "reports/pytest.json",
            "report_sha256": sha256_hex(self.report),
            "source_snapshot": {
                "manifest_schema_version": "usit-manifest-v1",
                "manifest_sha256": sha256_hex(canonical_json_bytes(self.manifest)),
                "file_count": 1,
                "capture_method": "PRE_EXECUTION_READ_ONLY_CAPTURE",
            },
            "working_tree_state": {"state": "CLEAN", "untracked_files": "INCLUDED"},
            "command_fingerprint": "b" * 64,
            "environment_fingerprint": "c" * 64,
        }

    def validate(self):
        return validate_execution_provenance(
            json.dumps(self.record).encode(), self.report, self.manifest_bytes
        )

    def test_valid_record_reports_integrity_but_not_authenticity(self):
        result = self.validate()
        self.assertEqual(result["report_integrity_status"], "MATCHED")
        self.assertEqual(result["source_manifest_integrity_status"], "MATCHED")
        self.assertEqual(result["provenance_authenticity_status"], "UNAUTHENTICATED")
        self.assertEqual(result["execution_reproducibility_status"], "CANDIDATE_ONLY")

    def test_report_input_must_be_bounded_bytes(self):
        from unittest.mock import patch
        for invalid in ("not-bytes", bytearray(self.report)):
            with self.subTest(value_type=type(invalid).__name__):
                with self.assertRaisesRegex(ValueError, "test report must be bytes"):
                    validate_execution_provenance(
                        json.dumps(self.record).encode(), invalid, self.manifest_bytes
                    )
        with patch("usit.test_execution_provenance.MAX_REPORT_BYTES", len(self.report) - 1):
            with self.assertRaisesRegex(ValueError, "test report exceeds size limit"):
                self.validate()

    def test_report_tampering_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "report_sha256"):
            validate_execution_provenance(
                json.dumps(self.record).encode(), self.report + b" ", self.manifest_bytes
            )

    def test_manifest_tampering_is_rejected(self):
        manifest = dict(self.manifest, files=[])
        self.record["source_snapshot"]["manifest_sha256"] = "d" * 64
        with self.assertRaisesRegex(ValueError, "manifest_sha256"):
            validate_execution_provenance(
                json.dumps(self.record).encode(), self.report, json.dumps(manifest).encode()
            )

    def test_duplicate_record_keys_are_rejected(self):
        raw = json.dumps(self.record).encode()
        duplicate = raw[:-1] + b',"execution_id":"other"}'
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            validate_execution_provenance(duplicate, self.report, self.manifest_bytes)

    def test_non_utc_timestamps_are_rejected(self):
        self.record["started_at"] = "2026-11-03T10:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "ending in Z"):
            self.validate()

    def test_completed_before_started_is_rejected(self):
        self.record["completed_at"] = "2026-11-03T09:59:59Z"
        with self.assertRaisesRegex(ValueError, "must not precede"):
            self.validate()

    def test_unsafe_report_paths_are_rejected(self):
        for path in ("../report.json", "/tmp/report.json", "C:/report.json", "reports\\\\x.json"):
            with self.subTest(path=path):
                self.record["report_relative_path"] = path
                with self.assertRaisesRegex(ValueError, "normalized relative"):
                    self.validate()

    def test_dirty_or_unknown_tree_never_claims_reproducibility(self):
        self.record["working_tree_state"] = {"state": "DIRTY", "untracked_files": "UNKNOWN"}
        self.assertEqual(self.validate()["execution_reproducibility_status"], "NOT_ESTABLISHED")

    def test_unknown_untracked_policy_is_not_reproducible(self):
        self.record["working_tree_state"]["untracked_files"] = "UNKNOWN"
        self.assertEqual(self.validate()["execution_reproducibility_status"], "NOT_ESTABLISHED")

    def test_non_finite_manifest_value_is_rejected(self):
        manifest = {"schema_version": "usit-manifest-v1", "files": [], "invalid": float("nan")}
        self.record["source_snapshot"]["manifest_sha256"] = "d" * 64
        with self.assertRaisesRegex(ValueError, "canonical-JSON"):
            validate_execution_provenance(
                json.dumps(self.record).encode(), self.report, json.dumps(manifest).encode()
            )


    def test_duplicate_manifest_paths_are_rejected(self):
        manifest = {"schema_version": "usit-manifest-v1", "files": [
            {"path": "tests/test_x.py", "sha256": "a" * 64},
            {"path": "tests/test_x.py", "sha256": "b" * 64},
        ]}
        self.record["source_snapshot"]["file_count"] = 2
        self.record["source_snapshot"]["manifest_sha256"] = sha256_hex(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(ValueError, "duplicate source manifest path"):
            validate_execution_provenance(
                json.dumps(self.record).encode(), self.report, json.dumps(manifest).encode()
            )

    def test_case_colliding_manifest_paths_are_rejected(self):
        manifest = {"schema_version": "usit-manifest-v1", "files": [
            {"path": "Tests/test_x.py", "sha256": "a" * 64},
            {"path": "tests/test_x.py", "sha256": "b" * 64},
        ]}
        self.record["source_snapshot"]["file_count"] = 2
        self.record["source_snapshot"]["manifest_sha256"] = sha256_hex(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(ValueError, "case-colliding"):
            validate_execution_provenance(
                json.dumps(self.record).encode(), self.report, json.dumps(manifest).encode()
            )

    def test_manifest_file_digest_and_path_are_validated(self):
        invalid_manifests = [
            {"schema_version": "usit-manifest-v1", "files": [
                {"path": "../outside.py", "sha256": "a" * 64}
            ]},
            {"schema_version": "usit-manifest-v1", "files": [
                {"path": "tests/test_x.py", "sha256": "A" * 64}
            ]},
        ]
        for manifest in invalid_manifests:
            with self.subTest(manifest=manifest):
                self.record["source_snapshot"]["file_count"] = 1
                self.record["source_snapshot"]["manifest_sha256"] = sha256_hex(canonical_json_bytes(manifest))
                with self.assertRaises(ValueError):
                    validate_execution_provenance(
                        json.dumps(self.record).encode(), self.report, json.dumps(manifest).encode()
                    )


    def test_malformed_enum_fields_raise_value_error_not_type_error(self):
        for field, value in (("report_format", []), ("report_format", {})):
            with self.subTest(field=field, value=value):
                self.record[field] = value
                with self.assertRaisesRegex(ValueError, "report_format"):
                    self.validate()
        self.record["report_format"] = "PYTEST_JSON"
        for field, value in (("state", []), ("state", {}), ("untracked_files", []), ("untracked_files", {})):
            with self.subTest(field=field, value=value):
                self.record["working_tree_state"] = {"state": "CLEAN", "untracked_files": "INCLUDED"}
                self.record["working_tree_state"][field] = value
                with self.assertRaisesRegex(ValueError, "working_tree_state"):
                    self.validate()


if __name__ == "__main__":
    unittest.main()
