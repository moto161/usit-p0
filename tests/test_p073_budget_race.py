"""P0.7.3 P0: read budget enforcement and stat/read race behaviour.

Every input is synthetic. Races are produced by fault injection around the real
code path (the file really does grow / disappear / get substituted), never by
asserting on a variable name.

P0.7.4 note: ``open_input_file`` now opens through a platform no-follow handle
(``usit.safeio.open_no_follow``) instead of ``Path.open``, and accepts the
discovery identity as ``expected_identity``. The injection helpers below forward
``**kwargs`` and the open-failure tests inject at ``safeio.open_no_follow``;
the scenarios and limits they prove are unchanged.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import usit.scanner as scanner
from usit.safety import ScanBlocked, authorization_record
from usit.scanner import discover_files, open_input_file, scan_project

TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


class _SyntheticProject:
    def __init__(self, case: unittest.TestCase, name: str = "project"):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p073-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / name
        self.out = self.base / "out"
        self.root.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)

    def authorization(self, **overrides):
        record = authorization_record(self.root.resolve(), self.out.resolve(), now=_now())
        record.update(overrides)
        return record

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def published(self) -> list[str]:
        if not self.out.exists():
            return []
        return sorted(p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file())

    def status_files(self) -> list[Path]:
        return sorted(self.base.glob("usit-status-*.json"))

    def terminal_status(self) -> str | None:
        files = self.status_files()
        if not files:
            return None
        return json.loads(files[-1].read_text(encoding="utf-8"))["status"]


# --------------------------------------------------------------------------
# Audit: budget is enforced before AND during the read
# --------------------------------------------------------------------------
class ReadBudgetEnforcementTests(unittest.TestCase):
    def test_per_file_limit_is_enforced_on_bytes_actually_read(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        # Discovery must pass, but the read itself must come back oversize: this
        # isolates the read-loop check from the discovery-time check.
        def oversize(path, limit, **kwargs):
            return b"x" * (limit + 1)

        with patch.object(scanner, "open_input_file", oversize):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_FILE_SIZE_EXCEEDED")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_discovery_rejects_an_oversize_file_before_it_is_read(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        reads: list[str] = []
        real = scanner.open_input_file

        def instrumented(path, limit, **kwargs):
            reads.append(str(path))
            return real(path, limit, **kwargs)

        with patch.object(scanner, "open_input_file", instrumented), \
             patch.object(scanner, "MAX_FILE_BYTES", 4):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_FILE_SIZE_EXCEEDED")
        self.assertEqual(reads, [], "Discovery must reject the file before any content is read.")
        self.assertEqual(project.published(), [])

    def test_cumulative_budget_counts_actual_bytes_not_stat_sizes(self):
        project = _SyntheticProject(self)
        project.write("a.py", "12345")
        project.write("b.py", "67")
        total_seen: list[int] = []
        real = scanner.open_input_file

        def instrumented(path, limit, **kwargs):
            data = real(path, limit, **kwargs)
            total_seen.append(len(data))
            return data

        # 5 + 2 = 7 bytes actually read.
        with patch.object(scanner, "open_input_file", instrumented), patch.object(scanner, "MAX_TOTAL_READ_BYTES", 7):
            summary = scan_project(str(project.root), str(project.out), force=True, authorization=project.authorization())
        self.assertEqual(sum(total_seen), 7)
        self.assertEqual(summary["status"], "COMPLETE")


    def test_read_loop_budget_counts_actual_bytes_and_stops_the_scan(self):
        """The cumulative check in the read loop uses bytes actually returned.

        Discovery sees 5 + 2 = 7 bytes and accepts them (budget 9). The read
        then returns 5 bytes for the second file, so the read-loop total is 10,
        which exceeds the budget and must stop the scan even though discovery
        had already accepted the files.
        """
        project = _SyntheticProject(self)
        project.write("a.py", "12345")
        project.write("b.py", "67")
        total_seen: list[int] = []
        real = scanner.open_input_file

        def instrumented(path, limit, **kwargs):
            data = real(path, limit, **kwargs)
            if path.name == "b.py":
                data = b"12345"
            total_seen.append(len(data))
            return data

        with patch.object(scanner, "open_input_file", instrumented), \
             patch.object(scanner, "MAX_TOTAL_READ_BYTES", 9):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True, authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_TOTAL_READ_BYTES_EXCEEDED")
        self.assertEqual(total_seen, [5, 5])
        self.assertEqual(sum(total_seen), 10, "The budget counted bytes actually read, not the 7 bytes seen at discovery.")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_discovery_budget_stops_before_any_content_is_read(self):
        project = _SyntheticProject(self)
        project.write("a.py", "12345")
        project.write("b.py", "67")
        reads: list[str] = []
        real = scanner.open_input_file

        def instrumented(path, limit, **kwargs):
            reads.append(str(path))
            return real(path, limit, **kwargs)

        with patch.object(scanner, "open_input_file", instrumented), \
             patch.object(scanner, "MAX_TOTAL_READ_BYTES", 6):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_TOTAL_READ_BYTES_EXCEEDED")
        self.assertEqual(reads, [], "No content may be read once the cumulative budget is exceeded.")
        self.assertEqual(project.published(), [])


# --------------------------------------------------------------------------
# Races between stat and read
# --------------------------------------------------------------------------
class StatReadRaceTests(unittest.TestCase):
    def test_file_growing_during_the_read_window_is_blocked(self):
        project = _SyntheticProject(self)
        target = project.write("a.py", "x = 1\n")
        real = scanner.open_input_file

        def growing(path, limit, **kwargs):
            data = real(path, limit, **kwargs)
            # The file genuinely grows after it has been read: this is the race
            # the post-read stat is supposed to catch.
            with path.open("ab") as handle:
                handle.write(b"X" * 64)
            return data

        with patch.object(scanner, "open_input_file", growing):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "INPUT_CHANGED_DURING_SCAN")
        self.assertGreater(target.stat().st_size, 6, "The synthetic file really did grow.")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_file_disappearing_during_the_read_window_is_blocked(self):
        project = _SyntheticProject(self)
        target = project.write("a.py", "x = 1\n")
        real = scanner.open_input_file

        def vanishing(path, limit, **kwargs):
            data = real(path, limit, **kwargs)
            path.unlink()
            return data

        with patch.object(scanner, "open_input_file", vanishing):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "FILE_STAT_FAILED")
        self.assertFalse(target.exists())
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_file_substituted_between_discovery_and_open_is_blocked(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        real_fstat = os.fstat

        def substituted(fd):
            info = real_fstat(fd)
            values = list(info)
            values[1] = info.st_ino + 1  # st_ino: the opened file is a different file
            return os.stat_result(tuple(values))

        with patch.object(scanner.os, "fstat", substituted):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "FILE_SUBSTITUTED_DURING_SCAN")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_open_input_file_refuses_a_path_that_became_a_link(self):
        project = _SyntheticProject(self)
        target = project.write("a.py", "x = 1\n")
        external = project.base / "outside.py"
        external.write_text("secret = 1\n", encoding="utf-8")
        link = project.root / "linked.py"
        try:
            link.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("OS denied symlink creation")
        with self.assertRaises(ScanBlocked) as blocked:
            open_input_file(link, scanner.MAX_FILE_BYTES)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")


# --------------------------------------------------------------------------
# Read failures
# --------------------------------------------------------------------------
class ReadFailureTests(unittest.TestCase):
    def test_permission_denied_on_open_is_blocked(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        real_open = scanner.safeio.open_no_follow

        def denied(path):
            if Path(path).name == "a.py":
                raise PermissionError("synthetic permission denial")
            return real_open(path)

        with patch.object(scanner.safeio, "open_no_follow", denied):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "FILE_READ_FAILED")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_read_oserror_is_blocked_and_leaves_no_complete_looking_output(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        real_open = scanner.safeio.open_no_follow

        def failing(path):
            if Path(path).name == "a.py":
                raise OSError("synthetic read failure")
            return real_open(path)

        with patch.object(scanner.safeio, "open_no_follow", failing):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "FILE_READ_FAILED")
        self.assertEqual(project.published(), [])
        self.assertEqual(list(project.base.glob(".usit-stage-*")), [], "Staging must be cleaned up.")
        self.assertIn(project.terminal_status(), {"BLOCKED", "INCOMPLETE"})

    def test_output_write_failure_is_incomplete_and_staging_is_cleaned(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        with patch.object(Path, "write_bytes", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(project.published(), [])
        self.assertEqual(list(project.base.glob(".usit-stage-*")), [])
        self.assertEqual(project.terminal_status(), "INCOMPLETE")
        marker = project.status_files()[-1].read_text(encoding="utf-8")
        self.assertNotIn("synthetic disk error", marker, "Raw exception text must not leak into the status file.")

    def test_successful_scan_records_actual_read_budget_in_summary(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def run():\n    return 1\n")
        summary = scan_project(str(project.root), str(project.out), force=True, authorization=project.authorization())
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertEqual(summary["runtime_limits"]["max_file_bytes"], 1_048_576)
        self.assertEqual(summary["runtime_limits"]["max_total_read_bytes"], 268_435_456)
        self.assertIn("scan_status.json", project.published())
        status = json.loads((project.out / "scan_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "COMPLETE")
        self.assertFalse(status["incomplete"])
        self.assertFalse(status["blocked"])


if __name__ == "__main__":
    unittest.main()
