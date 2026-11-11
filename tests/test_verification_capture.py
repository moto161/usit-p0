from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from usit import verification_capture as capture


class VerificationCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="usit-vcapture-")
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.evidence_parent = self.base / "evidence"
        self.project.mkdir()
        self.evidence_parent.mkdir()
        (self.project / "input.txt").write_text("before\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_capture(self, code: str, **kwargs):
        return capture.capture_run(
            [sys.executable, "-c", code],
            project_root=self.project,
            evidence_parent=self.evidence_parent,
            files=["input.txt"],
            approval_ref="unit-test-authorization",
            **kwargs,
        )

    def test_verify_manifest_rejects_oversized_manifest_input(self):
        manifest = self.evidence_parent / "evidence_manifest.json"
        with manifest.open("wb") as stream:
            stream.truncate(50 * 1024 * 1024 + 1)
        result = capture.verify_manifest(self.evidence_parent)
        self.assertFalse(result["valid"])
        self.assertTrue(any("size limit" in error for error in result["errors"]))

    def test_success_captures_separate_streams_and_valid_manifest(self):
        result = self.run_capture(
            "import sys; print('only-out'); print('only-err', file=sys.stderr)"
        )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.evidence_complete)
        self.assertTrue(result.manifest_valid)
        self.assertEqual(capture.verify_manifest(result.evidence_dir)["valid"], True)
        evidence = Path(result.evidence_dir)
        self.assertEqual((evidence / "stdout.log").read_text(encoding="utf-8").strip(), "only-out")
        self.assertEqual((evidence / "stderr.log").read_text(encoding="utf-8").strip(), "only-err")
        self.assertTrue((evidence / "pre_snapshot.json").is_file())
        self.assertTrue((evidence / "post_snapshot.json").is_file())
        report = json.loads((evidence / "final_gate_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PASS")

    def test_nonzero_exit_is_fail_even_with_complete_evidence(self):
        result = self.run_capture("import sys; print('no'); sys.exit(7)")
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.exit_code, 7)
        self.assertTrue(result.manifest_valid)
        self.assertTrue(result.evidence_complete)

    def test_startup_failure_is_infrastructure_error(self):
        result = capture.capture_run(
            [str(self.base / "missing-executable.exe")],
            project_root=self.project,
            evidence_parent=self.evidence_parent,
            files=["input.txt"],
            approval_ref="unit-test-authorization",
        )
        self.assertEqual(result.status, "INFRASTRUCTURE_ERROR")
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.manifest_valid)
        self.assertTrue(result.evidence_complete)

    def test_timeout_terminates_child_and_records_real_returncode(self):
        result = self.run_capture("import time; time.sleep(10)", timeout=0.15, poll_interval=0.01)
        self.assertEqual(result.status, "FAIL")
        self.assertTrue(result.timed_out)
        self.assertIsNotNone(result.exit_code)
        self.assertTrue(result.manifest_valid)

    def test_interruption_terminates_child_and_records_real_returncode(self):
        event = threading.Event()
        timer = threading.Timer(0.15, event.set)
        timer.start()
        try:
            result = self.run_capture("import time; time.sleep(10)", interrupt_event=event, poll_interval=0.01)
        finally:
            timer.cancel()
        self.assertEqual(result.status, "INTERRUPTED")
        self.assertTrue(result.interrupted)
        self.assertIsNotNone(result.exit_code)
        self.assertTrue(result.manifest_valid)

    def test_keyboard_interrupt_terminates_child_and_records_returncode(self):
        # Interrupt only the polling loop. subprocess.wait() shares the time
        # module and must be allowed to wait for the terminated child.
        original_sleep = capture.time.sleep
        should_interrupt = True

        def interrupt_once(delay):
            nonlocal should_interrupt
            if should_interrupt:
                should_interrupt = False
                raise KeyboardInterrupt
            return original_sleep(delay)

        with patch.object(capture.time, "sleep", side_effect=interrupt_once):
            result = self.run_capture("import time; time.sleep(10)", poll_interval=0.01)
        self.assertEqual(result.status, "INTERRUPTED")
        self.assertTrue(result.interrupted)
        self.assertIsNotNone(result.exit_code)
        self.assertTrue(result.manifest_valid)

    def test_existing_run_directory_is_refused_without_overwrite(self):
        fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        fake_datetime = types.SimpleNamespace(now=lambda tz=None: fixed)
        expected = self.evidence_parent / ("run_20260102T030405Z_" + "a" * 32)
        expected.mkdir()
        marker = expected / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        with patch.object(capture, "datetime", fake_datetime), patch.object(capture.secrets, "token_hex", return_value="a" * 32):
            with self.assertRaises(FileExistsError):
                self.run_capture("pass")
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_specified_file_change_is_recorded_and_fails_gate(self):
        code = "from pathlib import Path; Path('input.txt').write_text('after\\n')"
        result = self.run_capture(code)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.status, "FAIL")
        diff = json.loads((Path(result.evidence_dir) / "diff.json").read_text(encoding="utf-8"))
        self.assertEqual([item["path"] for item in diff["changes"]], ["input.txt"])
        self.assertTrue(result.manifest_valid)

    def test_evidence_write_failure_never_passes_or_starts_command(self):
        with patch.object(capture, "_write_json_file", side_effect=OSError("injected evidence write failure")), \
             patch.object(capture.subprocess, "Popen") as popen:
            result = self.run_capture("pass")
        self.assertEqual(result.status, "INFRASTRUCTURE_ERROR")
        self.assertFalse(result.evidence_complete)
        self.assertFalse(result.manifest_valid)
        popen.assert_not_called()

    def test_read_failure_refuses_to_start_command(self):
        (self.project / "folder").mkdir()
        with patch.object(capture.subprocess, "Popen") as popen:
            result = capture.capture_run(
                [sys.executable, "-c", "pass"],
                project_root=self.project,
                evidence_parent=self.evidence_parent,
                files=["folder"],
                approval_ref="unit-test-authorization",
            )
        self.assertEqual(result.status, "NOT_VERIFIED")
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.manifest_valid)
        popen.assert_not_called()

    def test_parent_traversal_is_rejected_before_evidence_creation(self):
        with self.assertRaises(ValueError):
            capture.capture_run(
                [sys.executable, "-c", "pass"],
                project_root=self.project,
                evidence_parent=self.evidence_parent,
                files=["../outside.txt"],
                approval_ref="unit-test-authorization",
            )
        self.assertEqual(list(self.evidence_parent.iterdir()), [])

    def test_manifest_hash_mismatch_is_detected(self):
        result = self.run_capture("pass")
        log = Path(result.evidence_dir) / "stdout.log"
        log.write_text("tampered\n", encoding="utf-8")
        verified = capture.verify_manifest(result.evidence_dir)
        self.assertFalse(verified["valid"])
        self.assertTrue(any("mismatch" in error for error in verified["errors"]))

    def test_manifest_signature_status_without_key_is_not_checked(self):
        result = self.run_capture("pass")
        verified = capture.verify_manifest(result.evidence_dir)
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["signature_status"], "NOT_CHECKED")

    def test_signed_manifest_verifies_with_key_and_tamper_is_invalid(self):
        from usit.evidence_signing import generate_run_key, sign_document

        result = self.run_capture("pass")
        manifest_path = Path(result.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        key = generate_run_key()
        manifest["signature"] = sign_document(manifest, key)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        verified = capture.verify_manifest(result.evidence_dir, hmac_key=key)
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["signature_status"], "VALID")
        # Tampering with an artifact after signing breaks the artifact hash
        # and the signature is computed over the manifest content, so the
        # manifest itself is unchanged here; instead tamper the manifest's
        # recorded hash to break the signature.
        manifest["artifacts"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        verified = capture.verify_manifest(result.evidence_dir, hmac_key=key)
        self.assertFalse(verified["valid"])
        self.assertEqual(verified["signature_status"], "INVALID")
        self.assertTrue(any("signature is INVALID" in error for error in verified["errors"]))

    def test_manifest_omitting_required_artifact_is_detected(self):
        result = self.run_capture("pass")
        manifest_path = Path(result.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"] = [item for item in manifest["artifacts"] if item["path"] != "stderr.log"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        verified = capture.verify_manifest(result.evidence_dir)
        self.assertFalse(verified["valid"])
        self.assertTrue(any("omits required artifacts" in error for error in verified["errors"]))

    def test_evidence_parent_inside_project_is_rejected(self):
        with self.assertRaises(ValueError):
            capture.capture_run(
                [sys.executable, "-c", "pass"],
                project_root=self.project,
                evidence_parent=self.project,
                files=["input.txt"],
                approval_ref="unit-test-authorization",
            )

    def test_missing_approval_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            capture.capture_run(
                [sys.executable, "-c", "pass"],
                project_root=self.project,
                evidence_parent=self.evidence_parent,
                files=["input.txt"],
                approval_ref=" ",
            )
        self.assertEqual(list(self.evidence_parent.iterdir()), [])


    def test_explicit_child_environment_is_passed_without_shell(self):
        env = os.environ.copy()
        env["USIT_CAPTURE_ENV_TEST"] = "expected-value"
        result = capture.capture_run(
            [sys.executable, "-c", "import os; print(os.environ['USIT_CAPTURE_ENV_TEST'])"],
            project_root=self.project,
            evidence_parent=self.evidence_parent,
            files=["input.txt"],
            approval_ref="unit-test-authorization",
            env=env,
        )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(
            (Path(result.evidence_dir) / "stdout.log").read_text(encoding="utf-8").strip(),
            "expected-value",
        )


    def test_isolated_run_rejected_when_parent_is_not_root_or_not_posix(self):
        # Fail-closed: isolation needs a root parent on POSIX. Mock the
        # platform checks so no real process is ever started. The POSIX
        # name is mocked too: on Windows os.name is "nt", so the POSIX
        # check would fire before the root check; create=True because
        # os.geteuid does not exist on Windows at all.
        with patch.object(os, "geteuid", return_value=1000, create=True), \
             patch.object(os, "name", "posix"), \
             patch.object(capture.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "run as root"):
                self.run_capture("pass", user="nobody")
            popen.assert_not_called()
        self.assertEqual(list(self.evidence_parent.iterdir()), [])
        with patch.object(os, "name", "nt"), \
             patch.object(capture.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "POSIX"):
                self.run_capture("pass", user="nobody")
            popen.assert_not_called()
        self.assertEqual(list(self.evidence_parent.iterdir()), [])

    def test_unknown_runner_user_is_rejected_without_starting_process(self):
        # Mock a root parent on POSIX plus a pwd stand-in whose lookups
        # always miss, so the test reaches the "does not exist" check on
        # any platform: CI runners are non-root, and Windows has no pwd
        # module at all. Without these the parent/platform check fires
        # first and the expected message never appears.
        def _missing(name):
            raise KeyError(name)

        def _missing_uid(uid):
            raise KeyError(uid)

        fake_pwd = types.SimpleNamespace(getpwnam=_missing, getpwuid=_missing_uid)
        with patch.object(os, "geteuid", return_value=0, create=True), \
             patch.object(os, "name", "posix"), \
             patch.object(capture, "pwd", fake_pwd), \
             patch.object(capture.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "does not exist"):
                self.run_capture("pass", user="usit-definitely-does-not-exist-12345")
            popen.assert_not_called()
        self.assertEqual(list(self.evidence_parent.iterdir()), [])

    def test_root_runner_user_is_rejected_without_starting_process(self):
        # Same platform mocking as above, with a pwd stand-in that only
        # knows the root entry, so the test reaches the "must not be
        # root" check on any platform.
        root = types.SimpleNamespace(pw_name="root", pw_uid=0)

        def _getpwnam(name):
            if name == "root":
                return root
            raise KeyError(name)

        def _getpwuid(uid):
            if uid == 0:
                return root
            raise KeyError(uid)

        fake_pwd = types.SimpleNamespace(getpwnam=_getpwnam, getpwuid=_getpwuid)
        with patch.object(os, "geteuid", return_value=0, create=True), \
             patch.object(os, "name", "posix"), \
             patch.object(capture, "pwd", fake_pwd), \
             patch.object(capture.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "must not be root"):
                self.run_capture("pass", user=0)
            with self.assertRaisesRegex(ValueError, "must not be root"):
                self.run_capture("pass", user="root")
            popen.assert_not_called()
        self.assertEqual(list(self.evidence_parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
