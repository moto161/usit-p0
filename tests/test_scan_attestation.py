from __future__ import annotations

import base64
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from usit.scan_attestation import (
    create_scan_attestation,
    public_key_id,
    verify_scan_attestation,
)


class ScanAttestationTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        self.private_pem = self.private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.key_id = public_key_id(self.public)
        self.manifest = b'{"schema_version":"usit-manifest-v1","files":[]}'
        self.attestation = create_scan_attestation(
            self.manifest, self.private_pem,
            snapshot_id="snapshot-test-001",
            created_at="2026-11-01T12:00:00Z",
            scanner_version="0.1.0",
            source_revision="abc123",
        )

    def verify(self, attestation=None, manifest=None, trust=None, expected=None):
        return verify_scan_attestation(
            self.manifest if manifest is None else manifest,
            self.attestation if attestation is None else attestation,
            {self.key_id: self.public} if trust is None else trust,
            expected_snapshot_id=expected,
        )

    def mutate(self, **changes):
        value = json.loads(self.attestation)
        value.update(changes)
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    def test_trusted_key_and_unchanged_manifest(self):
        result = self.verify()
        self.assertEqual(result["status"], "ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY")
        self.assertTrue(result["signature_valid"])
        self.assertTrue(result["trusted_key"])
        self.assertEqual(result["freshness"], "NOT_ASSESSED")

    def test_manifest_byte_change_is_rejected_before_signature_acceptance(self):
        result = self.verify(manifest=self.manifest + b" ")
        self.assertEqual(result["status"], "ATTESTATION_MANIFEST_MISMATCH")
        self.assertFalse(result["signature_valid"])

    def test_signature_tampering_is_rejected(self):
        value = json.loads(self.attestation)
        signature = bytearray(base64.b64decode(value["signature"]))
        signature[0] ^= 1
        value["signature"] = base64.b64encode(signature).decode()
        result = self.verify(attestation=json.dumps(value).encode())
        self.assertEqual(result["status"], "ATTESTATION_SIGNATURE_INVALID")

    def test_valid_signature_from_untrusted_key_is_not_trusted(self):
        result = self.verify(trust={})
        self.assertEqual(result["status"], "ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY")
        self.assertTrue(result["signature_valid"])
        self.assertFalse(result["trusted_key"])

    def test_unknown_replacement_key_never_becomes_trusted(self):
        other = Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        result = self.verify(trust={public_key_id(other): other})
        self.assertEqual(result["status"], "ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY")

    def test_signed_snapshot_id_mutation_invalidates_signature(self):
        result = self.verify(attestation=self.mutate(snapshot_id="snapshot-substituted"))
        self.assertEqual(result["status"], "ATTESTATION_SIGNATURE_INVALID")

    def test_signed_timestamp_mutation_invalidates_signature(self):
        result = self.verify(attestation=self.mutate(created_at="2026-11-02T12:00:00Z"))
        self.assertEqual(result["status"], "ATTESTATION_SIGNATURE_INVALID")

    def test_signed_scanner_revision_mutation_invalidates_signature(self):
        value = json.loads(self.attestation)
        value["scanner"]["source_revision"] = "other-revision"
        result = self.verify(attestation=json.dumps(value).encode())
        self.assertEqual(result["status"], "ATTESTATION_SIGNATURE_INVALID")

    def test_key_id_substitution_is_rejected(self):
        result = self.verify(attestation=self.mutate(key_id="sha256:" + "0" * 64))
        self.assertEqual(result["status"], "ATTESTATION_INVALID_FORMAT")

    def test_unsupported_algorithm_is_explicit(self):
        result = self.verify(attestation=self.mutate(algorithm="RSA"))
        self.assertEqual(result["status"], "ATTESTATION_ALGORITHM_UNSUPPORTED")

    def test_duplicate_json_keys_are_rejected(self):
        raw = self.attestation[:-1] + b',"algorithm":"Ed25519"}'
        result = self.verify(attestation=raw)
        self.assertEqual(result["status"], "ATTESTATION_INVALID_FORMAT")

    def test_malformed_base64_is_rejected(self):
        result = self.verify(attestation=self.mutate(signature="%%%"))
        self.assertEqual(result["status"], "ATTESTATION_INVALID_FORMAT")

    def test_expected_snapshot_policy_is_enforced(self):
        result = self.verify(expected="different-snapshot")
        self.assertEqual(result["status"], "ATTESTATION_POLICY_REJECTED")
        self.assertTrue(result["signature_valid"])
        self.assertTrue(result["trusted_key"])

    def test_private_key_is_not_embedded_in_attestation_or_report(self):
        self.assertNotIn(self.private_pem, self.attestation)
        result = self.verify()
        self.assertNotIn("private_key", json.dumps(result))

    def test_trusted_public_key_loader_rejects_symlink(self):
        import tempfile
        from pathlib import Path
        from usit.scan_attestation import load_trusted_public_key

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "trusted.bin"
            target.write_bytes(self.public)
            link = root / "trusted-link.bin"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises((OSError, ValueError)):
                load_trusted_public_key(str(link))

    def test_trusted_public_key_loader_rejects_oversized_file(self):
        import tempfile
        from pathlib import Path
        from usit.scan_attestation import load_trusted_public_key

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized-key.bin"
            with path.open("wb") as stream:
                stream.truncate(16 * 1024 + 1)
            with self.assertRaises((OSError, ValueError)):
                load_trusted_public_key(str(path))

    def test_manifest_path_traversal_is_rejected_during_creation(self):
        with self.assertRaisesRegex(ValueError, "traversal"):
            create_scan_attestation(
                self.manifest, self.private_pem,
                snapshot_id="s", created_at="2026-11-01T12:00:00Z",
                scanner_version="0.1.0", source_revision="abc",
                manifest_path="../outside.json",
            )


if __name__ == "__main__":
    unittest.main()
