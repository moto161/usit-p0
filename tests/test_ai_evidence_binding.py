from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from usit.ai_evidence_binding import verify_evidence_bindings
from usit.cli import main
from usit.scan_attestation import create_scan_attestation, public_key_id
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


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
        self.attestation_private = Ed25519PrivateKey.generate()
        self.attestation_public = self.attestation_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.attestation_key_id = public_key_id(self.attestation_public)
        self.attestation_private_pem = self.attestation_private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
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

    def _attach_scan_manifest(self, *, recorded_sha=None, manifest_path="scan_manifest.json"):
        source_sha = self.ledger["evidence_catalog"][0]["source_sha256"]
        manifest = {
            "schema_version": "usit-manifest-v1",
            "project_root": "fixture-project",
            "files": [{
                "path": "src/photo.py",
                "sha256": recorded_sha or source_sha,
            }],
        }
        raw = __import__("json").dumps(manifest, sort_keys=True).encode("utf-8")
        (self.root / manifest_path).write_bytes(raw)
        entry = self.ledger["evidence_catalog"][0]
        entry["snapshot_manifest_path"] = manifest_path
        entry["snapshot_manifest_sha256"] = hashlib.sha256(raw).hexdigest()

    def test_source_is_bound_to_exact_scan_manifest_entry(self):
        self._attach_scan_manifest()
        result = verify_evidence_bindings(self.ledger, self.root)
        binding = result["evidence_bindings"][0]
        self.assertEqual(binding["snapshot_binding_status"], "MATCHED_SCAN_MANIFEST_ENTRY")
        self.assertEqual(result["claims"][0]["snapshot_binding_status"],
                         "ALL_SOURCE_ENTRIES_MATCHED_SCAN_MANIFEST")

    def test_scan_manifest_file_digest_mismatch_is_stale(self):
        self._attach_scan_manifest()
        self.ledger["evidence_catalog"][0]["snapshot_manifest_sha256"] = "0" * 64
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][0]["snapshot_binding_status"],
                         "MANIFEST_STALE")
        self.assertEqual(result["claims"][0]["snapshot_binding_status"], "CHECK_REQUIRED")

    def test_scan_manifest_recorded_source_digest_mismatch_is_rejected(self):
        self._attach_scan_manifest(recorded_sha="0" * 64)
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][0]["snapshot_binding_status"],
                         "FILE_HASH_MISMATCH")

    def test_missing_scan_manifest_is_not_treated_as_verified(self):
        entry = self.ledger["evidence_catalog"][0]
        entry["snapshot_manifest_path"] = "missing-manifest.json"
        entry["snapshot_manifest_sha256"] = "0" * 64
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][0]["snapshot_binding_status"],
                         "MANIFEST_MISSING")

    def _attach_scan_attestation(self, *, manifest_path="scan_manifest.json",
                                 attestation_path="scan_attestation.json"):
        entry = self.ledger["evidence_catalog"][0]
        manifest_file = self.root / manifest_path
        manifest_bytes = manifest_file.read_bytes()
        entry["snapshot_attestation_path"] = attestation_path
        (self.root / attestation_path).write_bytes(create_scan_attestation(
            manifest_bytes, self.attestation_private_pem,
            snapshot_id="snapshot-" + manifest_path,
            created_at="2026-11-01T12:00:00Z",
            scanner_version="0.1.0", source_revision="fixture-revision",
            manifest_path=manifest_path,
        ))

    def test_legacy_source_without_attestation_remains_compatible(self):
        self._attach_scan_manifest()
        result = verify_evidence_bindings(self.ledger, self.root)
        binding = result["evidence_bindings"][0]
        self.assertEqual(binding["attestation_status"], "ATTESTATION_NOT_PROVIDED")
        self.assertEqual(result["claims"][0]["snapshot_provenance_status"], "NOT_PROVIDED")
        self.assertEqual(result["overall_truth_status"], "NOT_ESTABLISHED")

    def test_trusted_scan_attestation_is_bound_to_source_evidence(self):
        self._attach_scan_manifest()
        self._attach_scan_attestation()
        result = verify_evidence_bindings(
            self.ledger, self.root, trusted_public_keys={self.attestation_key_id: self.attestation_public}
        )
        binding = result["evidence_bindings"][0]
        self.assertEqual(binding["attestation_status"], "ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY")
        self.assertEqual(result["claims"][0]["snapshot_provenance_status"],
                         "ALL_SOURCE_ATTESTATIONS_TRUSTED")
        self.assertEqual(result["claims"][0]["snapshot_provenance"][0]["snapshot_id"],
                         "snapshot-scan_manifest.json")
        self.assertEqual(result["claims"][0]["truth_status"], "NOT_ESTABLISHED")

    def test_expected_snapshot_identity_policy_mismatch_is_rejected(self):
        self._attach_scan_manifest()
        self._attach_scan_attestation()
        self.ledger["evidence_catalog"][0]["expected_snapshot_id"] = "caller-expected-other"
        result = verify_evidence_bindings(
            self.ledger, self.root, trusted_public_keys={self.attestation_key_id: self.attestation_public}
        )
        self.assertEqual(result["evidence_bindings"][0]["attestation_status"],
                         "ATTESTATION_POLICY_REJECTED")
        self.assertTrue(result["evidence_bindings"][0]["snapshot_attestation"]["signature_valid"])
        self.assertTrue(result["evidence_bindings"][0]["snapshot_attestation"]["trusted_key"])

    def test_valid_attestation_without_independent_trust_is_untrusted(self):
        self._attach_scan_manifest()
        self._attach_scan_attestation()
        result = verify_evidence_bindings(self.ledger, self.root)
        self.assertEqual(result["evidence_bindings"][0]["attestation_status"],
                         "ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY")
        self.assertEqual(result["claims"][0]["snapshot_provenance_status"], "CHECK_REQUIRED")

    def test_attestation_detects_manifest_bytes_changed_after_signing(self):
        self._attach_scan_manifest()
        self._attach_scan_attestation()
        manifest = self.root / "scan_manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        entry = self.ledger["evidence_catalog"][0]
        entry["snapshot_manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        result = verify_evidence_bindings(
            self.ledger, self.root, trusted_public_keys={self.attestation_key_id: self.attestation_public}
        )
        self.assertEqual(result["evidence_bindings"][0]["attestation_status"],
                         "ATTESTATION_MANIFEST_MISMATCH")

    def test_tampered_attestation_signature_is_rejected(self):
        self._attach_scan_manifest()
        self._attach_scan_attestation()
        sidecar = self.root / "scan_attestation.json"
        value = __import__("json").loads(sidecar.read_bytes())
        signature = bytearray(__import__("base64").b64decode(value["signature"]))
        signature[0] ^= 1
        value["signature"] = __import__("base64").b64encode(signature).decode("ascii")
        sidecar.write_text(__import__("json").dumps(value), encoding="utf-8")
        result = verify_evidence_bindings(
            self.ledger, self.root, trusted_public_keys={self.attestation_key_id: self.attestation_public}
        )
        self.assertEqual(result["evidence_bindings"][0]["attestation_status"],
                         "ATTESTATION_SIGNATURE_INVALID")

    def test_multiple_snapshot_provenance_records_are_not_collapsed(self):
        self._attach_scan_manifest()
        self._attach_scan_attestation()
        second_manifest = {
            "schema_version": "usit-manifest-v1",
            "project_root": "fixture-project-2",
            "files": [{"path": "src/photo.py",
                       "sha256": self.ledger["evidence_catalog"][0]["source_sha256"]}],
        }
        second_raw = __import__("json").dumps(second_manifest, sort_keys=True).encode("utf-8")
        (self.root / "scan_manifest_two.json").write_bytes(second_raw)
        second_entry = dict(self.ledger["evidence_catalog"][0])
        second_entry.update(
            evidence_id="src-second-snapshot",
            snapshot_manifest_path="scan_manifest_two.json",
            snapshot_manifest_sha256=hashlib.sha256(second_raw).hexdigest(),
            snapshot_attestation_path="scan_attestation_two.json",
        )
        (self.root / "scan_attestation_two.json").write_bytes(create_scan_attestation(
            second_raw, self.attestation_private_pem,
            snapshot_id="snapshot-two", created_at="2026-11-01T12:01:00Z",
            scanner_version="0.1.0", source_revision="fixture-revision",
            manifest_path="scan_manifest_two.json",
        ))
        self.ledger["evidence_catalog"].append(second_entry)
        self.ledger["claims"][0]["evidence_refs"].append("src-second-snapshot")
        result = verify_evidence_bindings(
            self.ledger, self.root, trusted_public_keys={self.attestation_key_id: self.attestation_public}
        )
        provenance = result["claims"][0]["snapshot_provenance"]
        self.assertEqual(len(provenance), 2)
        self.assertEqual({x["manifest_path"] for x in provenance},
                         {"scan_manifest.json", "scan_manifest_two.json"})
        self.assertEqual({x["snapshot_id"] for x in provenance},
                         {"snapshot-scan_manifest.json", "snapshot-two"})

    def test_cli_accepts_independently_provisioned_trust_key(self):
        self._attach_scan_manifest()
        self._attach_scan_attestation()
        ledger_path = self.root / "ledger.json"
        ledger_path.write_text(json.dumps(self.ledger), encoding="utf-8")
        trusted_path = self.root / "trusted-public-key.bin"
        trusted_path.write_bytes(self.attestation_public)
        output = self.root / "binding-report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main([
                "verify-evidence-bindings", "--input", str(ledger_path),
                "--evidence-root", str(self.root),
                "--trusted-public-key", str(trusted_path),
                "--out", str(output),
            ])
        self.assertEqual(code, 0)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["evidence_bindings"][0]["attestation_status"],
                         "ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY")
        self.assertEqual(report["claims"][0]["truth_status"], "NOT_ESTABLISHED")

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
