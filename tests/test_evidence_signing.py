from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from usit.evidence_signing import (
    generate_run_key, key_fingerprint, key_file_name, read_key_file,
    sign_document, verify_document_signature, write_key_file,
)


class EvidenceSigningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="usit-evidence-signing-")
        self.base = Path(self.temp.name)
        self.key = generate_run_key()
        self.other_key = generate_run_key()
        self.doc = {"schema_version": 1, "run_id": "run_20260101T000000Z_" + "a" * 32,
                    "files": [{"path": "a.py", "sha256": "b" * 64}]}

    def tearDown(self):
        self.temp.cleanup()

    def test_sign_verify_roundtrip_is_valid(self):
        signed = dict(self.doc)
        signed["signature"] = sign_document(signed, self.key)
        self.assertEqual(verify_document_signature(signed, self.key), "VALID")

    def test_tampered_content_is_invalid(self):
        signed = dict(self.doc)
        signed["signature"] = sign_document(signed, self.key)
        signed["files"] = [{"path": "a.py", "sha256": "c" * 64}]
        self.assertEqual(verify_document_signature(signed, self.key), "INVALID")

    def test_missing_signature_is_missing(self):
        self.assertEqual(verify_document_signature(self.doc, self.key), "MISSING")

    def test_wrong_key_is_distinguished_from_tampering(self):
        signed = dict(self.doc)
        signed["signature"] = sign_document(signed, self.key)
        self.assertEqual(verify_document_signature(signed, self.other_key), "WRONG_KEY")

    def test_malformed_signature_forms_are_malformed(self):
        for bad in (
            {"algorithm": "SHA256", "key_fingerprint": "a" * 64, "value": "b" * 64},
            {"algorithm": "HMAC-SHA256", "key_fingerprint": "not-hex", "value": "b" * 64},
            {"algorithm": "HMAC-SHA256", "key_fingerprint": "a" * 64, "value": "short"},
            "not-a-dict",
        ):
            signed = dict(self.doc)
            signed["signature"] = bad
            self.assertEqual(
                verify_document_signature(signed, self.key), "MALFORMED", bad
            )

    def test_signature_covers_content_not_itself(self):
        # Re-signing after an edit must produce a new valid signature, and the
        # old signature must not validate the edited document.
        signed = dict(self.doc)
        signed["signature"] = sign_document(signed, self.key)
        old_signature = dict(signed["signature"])
        signed["files"] = [{"path": "a.py", "sha256": "c" * 64}]
        signed["signature"] = old_signature
        self.assertEqual(verify_document_signature(signed, self.key), "INVALID")
        signed["signature"] = sign_document(signed, self.key)
        self.assertEqual(verify_document_signature(signed, self.key), "VALID")

    def test_short_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least 16 bytes"):
            sign_document(self.doc, b"short")
        with self.assertRaisesRegex(ValueError, "at least 16 bytes"):
            verify_document_signature(self.doc, b"short")

    def test_key_fingerprint_matches_signature_block(self):
        signed = dict(self.doc)
        signed["signature"] = sign_document(signed, self.key)
        self.assertEqual(signed["signature"]["key_fingerprint"], key_fingerprint(self.key))

    def test_key_file_roundtrip_with_0600_permissions(self):
        run_id = "run_20260101T000000Z_" + "a" * 32
        path = self.base / key_file_name(run_id)
        written = write_key_file(path, run_id, self.key)
        self.assertEqual(written, path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        found_run_id, found_key = read_key_file(path)
        self.assertEqual(found_run_id, run_id)
        self.assertEqual(found_key, self.key)

    def test_key_file_refuses_overwrite(self):
        run_id = "run_20260101T000000Z_" + "a" * 32
        path = self.base / key_file_name(run_id)
        write_key_file(path, run_id, self.key)
        with self.assertRaises(FileExistsError):
            write_key_file(path, run_id, self.other_key)
        # The original key is untouched.
        self.assertEqual(read_key_file(path)[1], self.key)

    def test_key_file_rejects_bad_run_id_format_and_bad_hex(self):
        run_id = "run_20260101T000000Z_" + "a" * 32
        path = self.base / key_file_name(run_id)
        write_key_file(path, run_id, self.key)
        doc = json.loads(path.read_text(encoding="utf-8"))
        # A different but well-formed run_id is returned as-is; the caller
        # must compare it with the evidence dir it verifies (a wrong run's
        # key fails signature verification as WRONG_KEY anyway).
        doc["run_id"] = "run_20260102T000000Z_" + "b" * 32
        path.write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(read_key_file(path)[0], "run_20260102T000000Z_" + "b" * 32)
        doc["run_id"] = "not-a-run-id"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "run_id"):
            read_key_file(path)
        doc["run_id"] = run_id
        doc["key_hex"] = "not-hex"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hex"):
            read_key_file(path)

    def test_canonical_json_matches_test_execution_provenance(self):
        # evidence_signing intentionally duplicates canonicalization so the
        # trust root stays dependency-free; the two must never drift apart.
        from usit.test_execution_provenance import canonical_json_bytes as other

        from usit.evidence_signing import canonical_json_bytes as mine

        sample = {"b": [1, 2, {"z": None}], "a": "ünïcode", "c": 3.5}
        self.assertEqual(mine(sample), other(sample))


if __name__ == "__main__":
    unittest.main()
