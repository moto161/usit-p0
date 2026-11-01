import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from usit.cli import main
from tests.test_graph_validation import FIXTURES


class GraphCliTrustDefaultsTests(unittest.TestCase):
    def test_embedded_line_counts_are_untrusted_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            graph = root / "graph.json"
            graph.write_text((FIXTURES / "valid_v1.json").read_text(encoding="utf-8"), encoding="utf-8")
            with patch("usit.cli.validate_graph_file", return_value={"is_valid": True}) as validate:
                rc = main([
                    "validate-graph", str(graph),
                    "--out-json", str(root / "result.json"),
                    "--out-md", str(root / "result.md"),
                ])
            self.assertEqual(rc, 0)
            self.assertTrue(validate.call_args.kwargs["require_trusted"])

    def test_embedded_line_counts_need_explicit_weak_mode_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            graph = root / "graph.json"
            graph.write_text((FIXTURES / "valid_v1.json").read_text(encoding="utf-8"), encoding="utf-8")
            with patch("usit.cli.validate_graph_file", return_value={"is_valid": True}) as validate:
                rc = main([
                    "validate-graph", str(graph),
                    "--out-json", str(root / "result.json"),
                    "--out-md", str(root / "result.md"),
                    "--allow-untrusted-embedded-line-counts",
                ])
            self.assertEqual(rc, 0)
            self.assertFalse(validate.call_args.kwargs["require_trusted"])


class AndroidOutputWriterTests(unittest.TestCase):
    def test_json_writer_emits_parseable_json_with_real_newline(self):
        from usit.cli import _write_json_output
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "artifact.json"
            _write_json_output(path, {"ok": True, "items": [1, 2]}, overwrite=False)
            raw = path.read_bytes()
            self.assertEqual(json.loads(raw.decode("utf-8")), {"ok": True, "items": [1, 2]})
            self.assertTrue(raw.endswith(b"\n"))

    def test_failed_exclusive_write_removes_partial_artifact(self):
        from usit.cli import _write_exclusive_bytes
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "partial.bin"
            with patch("usit.cli.os.write", side_effect=OSError("injected write failure")):
                with self.assertRaises(OSError):
                    _write_exclusive_bytes(path, b"payload")
            self.assertFalse(path.exists())


class VerificationCaptureBoundsTests(unittest.TestCase):
    def _project_and_parent(self, td: str):
        base = Path(td)
        project = base / "project"
        parent = base / "evidence"
        project.mkdir()
        parent.mkdir()
        (project / "stable.py").write_text("VALUE = 1\n", encoding="utf-8")
        return project, parent

    def test_capture_uses_a_finite_default_timeout(self):
        import sys
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            with patch.object(capture, "DEFAULT_COMMAND_TIMEOUT_SECONDS", 0.1):
                result = capture.capture_run(
                    [sys.executable, "-c", "import time; time.sleep(3)"],
                    project_root=project, evidence_parent=parent,
                    files=["stable.py"], approval_ref="unit-test-approval",
                )
            self.assertTrue(result.timed_out)
            self.assertEqual(result.status, "FAIL")
            self.assertLessEqual(result.captured_output_bytes, capture.MAX_CAPTURE_OUTPUT_BYTES)

    def test_capture_bounds_combined_stdout_and_stderr(self):
        import sys
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            with patch.object(capture, "MAX_CAPTURE_OUTPUT_BYTES", 64):
                result = capture.capture_run(
                    [sys.executable, "-c", "import sys; sys.stdout.write('x'*10000); sys.stderr.write('y'*10000)"],
                    project_root=project, evidence_parent=parent,
                    files=["stable.py"], approval_ref="unit-test-approval",
                )
            self.assertEqual(result.status, "FAIL")
            self.assertTrue(result.output_limit_exceeded)
            self.assertLessEqual(result.captured_output_bytes, 64)
            self.assertTrue(result.manifest_valid)
            evidence = Path(result.evidence_dir)
            self.assertLessEqual((evidence / "stdout.log").stat().st_size + (evidence / "stderr.log").stat().st_size, 64)

    def test_capture_rejects_unbounded_timeout_values(self):
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            with self.assertRaises(ValueError):
                capture.capture_run(
                    ["python", "-c", "pass"], project_root=project,
                    evidence_parent=parent, files=["stable.py"],
                    approval_ref="unit-test-approval",
                    timeout=capture.MAX_COMMAND_TIMEOUT_SECONDS + 1,
                )

    def test_capture_rejects_non_finite_timing_values(self):
        import math
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(timeout=value):
                    with self.assertRaises(ValueError):
                        capture.capture_run(
                            ["python", "-c", "pass"], project_root=project,
                            evidence_parent=parent, files=["stable.py"],
                            approval_ref="unit-test-approval", timeout=value,
                        )

    def test_capture_rejects_unresponsive_poll_interval(self):
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            with self.assertRaises(ValueError):
                capture.capture_run(
                    ["python", "-c", "pass"], project_root=project,
                    evidence_parent=parent, files=["stable.py"],
                    approval_ref="unit-test-approval",
                    poll_interval=capture.MAX_POLL_INTERVAL_SECONDS + 1,
                )

    def test_capture_bounds_command_arguments_and_approval_reference(self):
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            with self.assertRaises(ValueError):
                capture.capture_run(
                    ["python", "-c", "x" * (capture.MAX_COMMAND_ARG_BYTES + 1)],
                    project_root=project, evidence_parent=parent,
                    files=["stable.py"], approval_ref="unit-test-approval",
                )
            with self.assertRaises(ValueError):
                capture.capture_run(
                    ["python", "-c", "pass"], project_root=project,
                    evidence_parent=parent, files=["stable.py"],
                    approval_ref="a" * (capture.MAX_APPROVAL_REF_CHARS + 1),
                )

    def test_capture_snapshot_per_file_limit_fails_closed(self):
        import sys
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            with patch.object(capture, "MAX_SNAPSHOT_FILE_BYTES", 4):
                result = capture.capture_run(
                    [sys.executable, "-c", "print('should-not-run')"],
                    project_root=project, evidence_parent=parent,
                    files=["stable.py"], approval_ref="unit-test-approval",
                )
            self.assertEqual(result.status, "NOT_VERIFIED")
            self.assertEqual(result.exit_code, None)
            self.assertTrue(result.manifest_valid)
            self.assertTrue(any("per-file limit" in item for item in result.errors))

    def test_capture_snapshot_total_limit_fails_closed(self):
        import sys
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            with patch.object(capture, "MAX_SNAPSHOT_TOTAL_BYTES", 4):
                result = capture.capture_run(
                    [sys.executable, "-c", "print('should-not-run')"],
                    project_root=project, evidence_parent=parent,
                    files=["stable.py"], approval_ref="unit-test-approval",
                )
            self.assertEqual(result.status, "NOT_VERIFIED")
            self.assertIsNone(result.exit_code)
            self.assertTrue(result.manifest_valid)
            self.assertTrue(any("total limit" in item for item in result.errors))

    def test_capture_rejects_snapshot_file_count_over_limit(self):
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            with patch.object(capture, "MAX_SNAPSHOT_FILES", 0):
                with self.assertRaises(ValueError):
                    capture.capture_run(
                        ["python", "-c", "pass"], project_root=project,
                        evidence_parent=parent, files=["stable.py"],
                        approval_ref="unit-test-approval",
                    )

    def test_manifest_rejects_non_object_json_root(self):
        import sys
        from usit import verification_capture as capture
        with tempfile.TemporaryDirectory() as td:
            project, parent = self._project_and_parent(td)
            result = capture.capture_run(
                [sys.executable, "-c", "pass"], project_root=project,
                evidence_parent=parent, files=["stable.py"],
                approval_ref="unit-test-approval",
            )
            Path(result.manifest_path).write_text("[]", encoding="utf-8")
            verified = capture.verify_manifest(result.evidence_dir)
            self.assertFalse(verified["valid"])
            self.assertTrue(any("root must be an object" in item for item in verified["errors"]))


if __name__ == "__main__":
    unittest.main()
