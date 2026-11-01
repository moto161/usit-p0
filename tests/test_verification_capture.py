from __future__ import annotations

import json
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
        # Raise only for the capture polling loop. subprocess.wait() also uses
        # the shared time module and must be allowed to wait for child exit.
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
