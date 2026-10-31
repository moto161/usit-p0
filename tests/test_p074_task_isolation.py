"""P0.7.4 P0: parser-task process isolation, timeouts and honest cancellation.

Python per-file analysis and cross-file resolution now run as supervised
subprocesses, so a stuck task is a child the supervisor can kill and *verify*,
not an uninterruptible thread.  Every hang is produced by a controllable argv
worker; no test waits out the real 45 s protocol limit, and no task is ever
reported as cancelled without evidence.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import usit.limits as limits
import usit.scanner as scanner
from usit.limits import ParserTaskSupervisor, verify_safety_limits
from usit.safety import ScanBlocked, authorization_record
from usit.scanner import scan_project


def _now():
    return datetime.now(timezone.utc)


class _SyntheticProject:
    def __init__(self, case: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p074-iso-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "project"
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

    def staging(self) -> list[Path]:
        return list(self.base.glob(".usit-stage-*"))

    def terminal_status(self) -> str | None:
        files = sorted(self.base.glob("usit-status-*.json"))
        if not files:
            return None
        return json.loads(files[-1].read_text(encoding="utf-8"))["status"]


def _analyze_worker(project: _SyntheticProject, body: str) -> str:
    """A worker whose (single) action runs ``body`` after draining stdin."""
    script = project.base / "fake_analyze_worker.py"
    lines = "\n".join(line for line in body.strip().splitlines())
    script.write_text("import sys, time\nsys.stdin.read()\n" + lines + "\n", encoding="utf-8")
    return str(script)


class AnalyzerSubprocessTests(unittest.TestCase):
    def test_analyzer_timeout_is_killed_verified_and_blocks(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        worker = _analyze_worker(project, "time.sleep(600)")
        shared = ParserTaskSupervisor()
        with patch.object(scanner, "PYTHON_WORKER", worker), \
             patch.object(scanner, "ParserTaskSupervisor", lambda: shared), \
             patch.object(limits, "MAX_PARSER_TASK_SECONDS", 2.0):
            started = time.monotonic()
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
            elapsed = time.monotonic() - started
        self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
        self.assertLess(elapsed, 12.0, "The gate must not wait out the real 45 s budget.")
        record = shared.records[-1]
        self.assertEqual(record.kind, "subprocess")
        self.assertEqual(record.outcome, "TIMEOUT")
        self.assertEqual(record.cancellation, "killed_and_verified")
        self.assertEqual(project.published(), [], "A timed-out analyzer must not publish a graph.")
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_analyzer_crash_fails_closed(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        worker = _analyze_worker(project, "sys.exit(5)")
        with patch.object(scanner, "PYTHON_WORKER", worker):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "PARSER_CRASHED")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_analyzer_corrupt_output_fails_closed(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        worker = _analyze_worker(project, "sys.stdout.write('not json at all')")
        with patch.object(scanner, "PYTHON_WORKER", worker):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "PARSER_OUTPUT_INVALID")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")


class CancellationHonestyTests(unittest.TestCase):
    def test_subprocess_timeout_is_recorded_as_killed_and_verified(self):
        project = _SyntheticProject(self)
        sleeper = project.base / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
        supervisor = ParserTaskSupervisor()
        with self.assertRaises(ScanBlocked) as blocked:
            supervisor.run_subprocess("sleep", [sys.executable, str(sleeper)], timeout=2.0)
        self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
        record = supervisor.records[-1]
        self.assertEqual(record.outcome, "TIMEOUT")
        self.assertEqual(record.cancellation, "killed_and_verified")

    def test_unverifiable_termination_fails_closed_as_cancellation_failed(self):
        project = _SyntheticProject(self)
        sleeper = project.base / "sleeper.py"
        # Short sleep: this child is deliberately never killed (termination is
        # faked as failed), so it must not linger after the test.
        sleeper.write_text("import time\ntime.sleep(8)\n", encoding="utf-8")
        supervisor = ParserTaskSupervisor()
        with patch.object(ParserTaskSupervisor, "_terminate_and_verify", staticmethod(lambda process: False)):
            with self.assertRaises(ScanBlocked) as blocked:
                supervisor.run_subprocess("unkillable", [sys.executable, str(sleeper)], timeout=2.0)
        self.assertEqual(blocked.exception.code, "PARSER_CANCELLATION_FAILED")
        record = supervisor.records[-1]
        self.assertEqual(record.outcome, "CANCELLATION_FAILED")
        self.assertEqual(record.cancellation, "unverified")

    def test_killed_worker_is_verified_terminated_by_heartbeat(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        # A startup marker written by the worker's *first* statement, before it
        # blocks on stdin, decouples "the child process actually executed" from
        # the stdin-drain race that previously made this test flaky: a worker
        # killed before it finished reading stdin still leaves the marker on disk,
        # so the test no longer mistakes "killed mid-read" for "never ran".
        marker = project.base / "started.txt"
        heartbeat = project.base / "heartbeat.txt"
        worker = project.base / "hb_worker.py"
        worker.write_text(
            "import sys, time, os\n"
            "with open(" + repr(str(marker)) + ", 'a') as fh:\n"
            "    fh.write('started\\n')\n"
            "sys.stdin.read()\n"
            "with open(" + repr(str(heartbeat)) + ", 'w', buffering=1) as fh:\n"
            "    while True:\n"
            "        fh.write('x')\n"
            "        time.sleep(0.05)\n",
            encoding="utf-8")
        # Inject a shared supervisor so the test can read the auditable record and
        # distinguish the four worker states from the produced evidence.
        supervisor = ParserTaskSupervisor()
        with patch.object(scanner, "PYTHON_WORKER", str(worker)), \
             patch.object(scanner, "ParserTaskSupervisor", lambda: supervisor), \
             patch.object(limits, "MAX_PARSER_TASK_SECONDS", 3.0):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        # (1) Fail closed: timed out, no graph published, terminal status BLOCKED.
        self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
        self.assertEqual(project.published(), [], "A timed-out analyzer must not publish a graph.")
        self.assertEqual(project.terminal_status(), "BLOCKED")
        # (2) The worker genuinely started: a marker written before the worker
        #     blocks on stdin proves the child executed, independent of how fast
        #     the parent drains stdin.
        self.assertTrue(marker.exists(),
                        "The worker process must have started before it was killed.")
        # (3) Termination was *verified*, not assumed: the supervisor records a
        #     killed_and_verified cancellation, which is the reliable evidence the
        #     child is actually gone.  An unverified termination would surface as
        #     PARSER_CANCELLATION_FAILED instead.
        record = supervisor.records[-1]
        self.assertEqual(record.kind, "subprocess")
        self.assertEqual(record.outcome, "TIMEOUT")
        self.assertEqual(record.cancellation, "killed_and_verified",
                         "The supervisor must have verified the kill; an unverified "
                         "termination must not be reported as killed_and_verified.")
        # (4) If a heartbeat was produced, it must stop growing after the kill: a
        #     second, independent confirmation that the worker is dead.  This is
        #     asserted only when a heartbeat exists, so a worker killed mid-read
        #     (no heartbeat yet) is no longer mis-flagged as "never ran".
        if heartbeat.exists():
            size_after_kill = heartbeat.stat().st_size
            time.sleep(1.0)
            self.assertEqual(heartbeat.stat().st_size, size_after_kill,
                             "A killed worker must stop producing output; a growing "
                             "heartbeat means it is still running.")


class ResourceCleanupTests(unittest.TestCase):
    def test_repeated_timed_out_tasks_release_the_slot_and_verify_each_kill(self):
        project = _SyntheticProject(self)
        sleeper = project.base / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
        supervisor = ParserTaskSupervisor()
        for index in range(3):
            with self.assertRaises(ScanBlocked) as blocked:
                supervisor.run_subprocess(f"sleep-{index}", [sys.executable, str(sleeper)], timeout=2.0)
            self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
            self.assertEqual(supervisor.guard.active, 0, "The concurrency slot must be released after each kill.")
        audit = supervisor.audit()
        self.assertEqual(audit["task_count"], 3)
        self.assertEqual(audit["cancellations"].get("killed_and_verified"), 3)
        self.assertEqual(audit["cancellations"].get("unverified", 0), 0)
        self.assertEqual(supervisor.guard.peak_active, 1)

    def test_repeated_timed_out_scans_publish_nothing_and_clean_staging(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        worker = _analyze_worker(project, "time.sleep(600)")
        with patch.object(scanner, "PYTHON_WORKER", worker), \
             patch.object(limits, "MAX_PARSER_TASK_SECONDS", 2.0):
            for _ in range(3):
                with self.assertRaises(ScanBlocked) as blocked:
                    scan_project(str(project.root), str(project.out), force=True,
                                 authorization=project.authorization())
                self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
                self.assertEqual(project.published(), [], "No graph may be published after a timeout.")
                self.assertEqual(project.staging(), [], "Staging must be cleaned up.")
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_concurrency_limit_rejects_a_second_supervised_task(self):
        project = _SyntheticProject(self)
        sleeper = project.base / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
        supervisor = ParserTaskSupervisor()
        holder: dict[str, str] = {}

        def run_first():
            try:
                supervisor.run_subprocess("hold", [sys.executable, str(sleeper)], timeout=4.0)
            except ScanBlocked as exc:
                holder["code"] = exc.code

        thread = threading.Thread(target=run_first, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while supervisor.guard.active == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(supervisor.guard.active, 1)
        with self.assertRaises(ScanBlocked) as blocked:
            supervisor.run_subprocess("second", [sys.executable, str(sleeper)], timeout=5)
        self.assertEqual(blocked.exception.code, "CONCURRENCY_LIMIT_EXCEEDED")
        self.assertEqual(supervisor.guard.peak_active, 1)
        self.assertEqual(supervisor.guard.rejected, 1)
        # The holder times out and is killed, releasing the slot for reuse.
        thread.join(timeout=20)
        self.assertEqual(holder.get("code"), "PARSER_TASK_TIMEOUT")
        self.assertEqual(supervisor.guard.active, 0)


class ConfigurationAndEntrypointTests(unittest.TestCase):
    def test_limits_match_the_protocol(self):
        verify_safety_limits()

    def test_benchmark_module_is_not_a_scan_entry(self):
        import usit.benchmark as benchmark
        source = Path(benchmark.__file__).read_text(encoding="utf-8")
        # benchmark.py reads checked-in fixtures only; it never starts a scan and
        # therefore never bypasses the authorization/supervision path.
        self.assertNotIn("scan_project", source)
        self.assertIn("STATIC_ONLY_NO_FIXTURE_EXECUTION", source)
        repo_fixtures = (Path(benchmark.__file__).resolve().parents[1] / "tests" / "fixtures").resolve()
        self.assertTrue(Path(benchmark.FIXTURE_ROOT).resolve().is_relative_to(repo_fixtures))

    def test_association_benchmark_only_scans_a_synthetic_fixture_copy(self):
        import usit.association_benchmark as association
        source = Path(association.__file__).read_text(encoding="utf-8")
        self.assertIn("scan_project", source)
        self.assertIn("TemporaryDirectory", source)
        repo_fixtures = (Path(association.__file__).resolve().parents[1] / "tests" / "fixtures").resolve()
        self.assertTrue(Path(association.FIXTURE).resolve().is_relative_to(repo_fixtures))

    def test_worker_never_uses_eval_exec_or_a_shell(self):
        import usit.python_worker as worker
        source = Path(worker.__file__).read_text(encoding="utf-8")
        for forbidden in ("eval(", "exec(", "os.system", "subprocess.", "shell=True"):
            self.assertNotIn(forbidden, source)
        scanner_source = Path(scanner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", scanner_source)


if __name__ == "__main__":
    unittest.main()
