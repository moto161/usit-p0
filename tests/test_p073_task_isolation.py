"""P0.7.3 P1: parser-task isolation, timeouts and cancellation honesty.

Every hang is produced by a controllable mock. No test waits out a real 45 s
budget, and no in-process task is ever described as successfully cancelled.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import usit.limits as limits
import usit.scanner as scanner
from usit.limits import ParserTaskSupervisor, verify_safety_limits
from usit.safety import ScanBlocked, authorization_record
from usit.scanner import scan_project

TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


class _SyntheticProject:
    def __init__(self, case: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p073-iso-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
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

    def terminal_status(self):
        files = sorted(self.base.glob("usit-status-*.json"))
        return json.loads(files[-1].read_text(encoding="utf-8"))["status"] if files else None


def _resolve_worker_script(project, resolve_body: str) -> str:
    """Write a worker whose ``resolve`` action runs ``resolve_body`` and whose
    ``analyze`` action returns an empty row set.

    P0.7.4: cross-file resolution is an isolated subprocess, so fault injection
    moves to the worker boundary (a controlled argv script) instead of patching
    an in-process callable.  ``analyze`` is kept trivial so the scan reaches the
    resolution stage quickly; the injected behaviour then applies to ``resolve``.
    """
    script = project.base / "fake_resolve_worker.py"
    body = "\n".join("    " + line for line in resolve_body.strip().splitlines())
    script.write_text(
        "import sys, time\n"
        "action = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "if action == 'resolve':\n"
        "    sys.stdin.read()\n"
        + body + "\n"
        "else:\n"
        "    sys.stdin.read()\n"
        "    sys.stdout.write('{\"rows\": []}')\n",
        encoding="utf-8")
    return str(script)


class CrossFileResolutionIsolationTests(unittest.TestCase):
    """P0.7.4: cross-file resolution is an isolated, killable subprocess task."""

    def test_cross_file_resolution_hang_is_killed_verified_and_blocks(self):
        project = _SyntheticProject(self)
        project.write("main.py", "from lib import util\n\ndef go():\n    return util.value()\n")
        project.write("lib.py", "def value():\n    return 1\n")
        worker = _resolve_worker_script(project, "time.sleep(600)")
        shared = ParserTaskSupervisor()
        # Inject a short deadline: the gate is proved in well under a second
        # instead of waiting out the real 45 s protocol limit. The timeout is
        # long enough that ``analyze`` still completes, so the *resolution* task
        # is the one that times out.
        with patch.object(scanner, "PYTHON_WORKER", worker), \
             patch.object(scanner, "ParserTaskSupervisor", lambda: shared), \
             patch.object(limits, "MAX_PARSER_TASK_SECONDS", 3.0):
            started = time.monotonic()
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
            elapsed = time.monotonic() - started
        self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
        self.assertLess(elapsed, 12.0, "The cross-file task must not wait out the real budget.")
        resolve_records = [r for r in shared.records if r.name == "resolve-python-calls"]
        self.assertTrue(resolve_records, "The resolution task must have run as a supervised task.")
        self.assertEqual(resolve_records[-1].outcome, "TIMEOUT")
        self.assertEqual(resolve_records[-1].cancellation, "killed_and_verified")
        self.assertEqual(project.published(), [], "A timed-out cross-file task must not publish a graph.")
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_cross_file_resolution_runs_as_a_supervised_subprocess(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        shared = ParserTaskSupervisor()
        with patch.object(scanner, "ParserTaskSupervisor", lambda: shared):
            summary = scan_project(str(project.root), str(project.out), force=True,
                                   authorization=project.authorization())
        audit = summary["parser_task_audit"]
        self.assertEqual(audit["concurrency_limit"], 1)
        self.assertEqual(audit["concurrency_peak_observed"], 1)
        self.assertEqual(audit["concurrency_rejections"], 0)
        kinds = {record["kind"] for record in audit["records"]}
        self.assertEqual(kinds, {"subprocess"}, "Every parser task must be an isolated subprocess.")
        names = {record["name"].split(":", 1)[0] for record in audit["records"]}
        self.assertIn("resolve-python-calls", names)

    def test_cross_file_resolution_failure_fails_closed(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        worker = _resolve_worker_script(project, "sys.exit(3)")
        with patch.object(scanner, "PYTHON_WORKER", worker):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "PARSER_CRASHED")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")



class CancellationHonestyTests(unittest.TestCase):
    def test_inprocess_timeout_is_recorded_as_abandoned_not_cancelled(self):
        supervisor = ParserTaskSupervisor()
        release = threading.Event()
        try:
            with self.assertRaises(ScanBlocked):
                supervisor.run_inprocess("hang", lambda: release.wait(30), timeout=0.2)
        finally:
            release.set()
        record = next(r for r in supervisor.records if r.outcome == "TIMEOUT")
        self.assertEqual(record.cancellation, "abandoned_unreliable")
        audit = supervisor.audit()
        self.assertEqual(audit["cancellations"]["abandoned_unreliable"], 1)
        self.assertNotIn("killed_and_verified", audit["cancellations"])

    def test_subprocess_timeout_is_recorded_as_killed_and_verified(self):
        project = _SyntheticProject(self)
        sleeper = project.base / "sleeper.py"
        sleeper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        supervisor = ParserTaskSupervisor()
        with self.assertRaises(ScanBlocked) as blocked:
            supervisor.run_subprocess("sleep", [sys.executable, str(sleeper)], timeout=0.4)
        self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
        record = supervisor.records[-1]
        self.assertEqual(record.cancellation, "killed_and_verified")
        self.assertLess(record.elapsed_seconds, 5.0)

    def test_abandoned_thread_does_not_block_interpreter_exit(self):
        project = _SyntheticProject(self)
        script = project.base / "abandon.py"
        script.write_text(
            "import sys, threading\n"
            "sys.path.insert(0, %r)\n" % str(Path(scanner.__file__).resolve().parents[0].parent) +
            "from usit.limits import ParserTaskSupervisor\n"
            "from usit.safety import ScanBlocked\n"
            "release = threading.Event()\n"
            "try:\n"
            "    ParserTaskSupervisor().run_inprocess('hang', lambda: release.wait(30), timeout=0.2)\n"
            "except ScanBlocked as exc:\n"
            "    print('BLOCKED', exc.code)\n"
            "print('exiting')\n", encoding="utf-8")
        started = time.monotonic()
        completed = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30, check=False)
        elapsed = time.monotonic() - started
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("BLOCKED PARSER_TASK_TIMEOUT", completed.stdout)
        self.assertIn("exiting", completed.stdout)
        self.assertLess(elapsed, 20.0, "An abandoned daemon thread must not block interpreter exit.")


class TaskIsolationAuditTests(unittest.TestCase):
    """The audit table itself must be reproducible from the running code."""

    def test_limits_match_the_protocol(self):
        verify_safety_limits()

    def test_every_scan_task_is_supervised_and_recorded(self):
        project = _SyntheticProject(self)
        project.write("main.py", "from lib import util\n\ndef go():\n    return util.value()\n")
        project.write("lib.py", "def value():\n    return 1\n")
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        audit = summary["parser_task_audit"]
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertEqual(audit["concurrency_limit"], 1)
        self.assertEqual(audit["concurrency_peak_observed"], 1)
        self.assertEqual(audit["concurrency_rejections"], 0)
        self.assertGreaterEqual(audit["task_count"], 3)
        kinds = {record["name"].split(":", 1)[0] for record in audit["records"]}
        self.assertIn("analyze", kinds)
        self.assertIn("resolve-python-calls", kinds)
        self.assertEqual(audit["outcomes"].get("TIMEOUT", 0), 0)


if __name__ == "__main__":
    unittest.main()
