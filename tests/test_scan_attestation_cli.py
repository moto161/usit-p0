from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from usit.cli import main
from usit.scan_attestation import create_scan_attestation


class ScanAttestationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = self.root / "scan_manifest.json"
        self.sidecar = self.root / "scan_attestation.json"
        self.trusted = self.root / "trusted-public-key.bin"
        self.output = self.root / "report.json"
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        private_pem = self.private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        manifest_bytes = b'{"schema_version":"usit-manifest-v1","files":[]}'
        self.manifest.write_bytes(manifest_bytes)
        self.sidecar.write_bytes(create_scan_attestation(
            manifest_bytes, private_pem, snapshot_id="cli-snapshot-1",
            created_at="2026-11-01T12:00:00Z", scanner_version="0.1.0",
            source_revision="fixture-revision",
        ))
        self.trusted.write_bytes(self.public)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, output=None, key=None):
        return [
            "verify-scan-attestation",
            "--manifest", str(self.manifest),
            "--attestation", str(self.sidecar),
            "--trusted-public-key", str(self.trusted if key is None else key),
            "--out", str(self.output if output is None else output),
        ]

    def test_cli_accepts_trusted_key_and_writes_report(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(self.command())
        self.assertEqual(code, 0)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY")
        self.assertTrue(report["trusted_key"])

    def test_cli_rejects_untrusted_key(self):
        other = Ed25519PrivateKey.generate().public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        other_path = self.root / "other-public-key.bin"
        other_path.write_bytes(other)
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(self.command(key=other_path))
        self.assertEqual(code, 2)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY")

    def test_cli_never_overwrites_existing_report(self):
        self.output.write_text("keep", encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(self.command())
        self.assertEqual(code, 2)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "keep")

    def test_cli_rejects_symlinked_manifest(self):
        link = self.root / "manifest-link.json"
        try:
            link.symlink_to(self.manifest)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        args = self.command()
        args[args.index("--manifest") + 1] = str(link)
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(args)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
