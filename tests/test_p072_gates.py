"""P0.7.2 read-only pre-check gates and resource limits.

Every input in this module is either a checked-in synthetic fixture under
``tests/fixtures`` or a directory generated inside ``tempfile``. No real
business project is read, built, executed or contacted. Timeouts are proved
with controllable mock parsers and fault injection, never by waiting out a real
budget.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import usit.js_ts_parser as js_ts_parser
import usit.limits as limits
from usit import safety as safety_module
import usit.scanner as scanner
from usit.limits import ConcurrencyGuard, ParserTaskSupervisor, verify_safety_limits
from usit.safety import ScanBlocked, authorization_record
from usit.scanner import discover_files, scan_project

TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _SyntheticProject:
    """A disposable project built inside the OS temp directory."""

    def __init__(self, case: unittest.TestCase, name: str = "project"):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p072-")
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


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# A. Authorization and path gates
# --------------------------------------------------------------------------
class AuthorizationAndPathGateTests(unittest.TestCase):
    def test_relative_project_path_is_blocked(self):
        project = _SyntheticProject(self)
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project("relative/project", str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "PROJECT_PATH_MUST_BE_ABSOLUTE")

    def test_parent_traversal_project_path_is_blocked(self):
        project = _SyntheticProject(self)
        escaping = str(project.root) + os.sep + ".." + os.sep + ".."
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(escaping, str(project.out), authorization=project.authorization())
        self.assertIn(blocked.exception.code, {"PROJECT_PATH_INVALID", "PROJECT_SCOPE_MISMATCH", "PROJECT_ROOT_NOT_DIRECTORY"})

    def test_path_normalization_case_and_trailing_separators_reach_same_root(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        summary = scan_project(str(project.root), str(project.out), force=True, authorization=project.authorization())
        self.assertEqual(summary["files_scanned"], 1)
        # Same directory expressed with a trailing separator, a "." segment and
        # different casing must resolve to the same authorized root.
        variants = [str(project.root) + os.sep, str(project.root) + os.sep + "."]
        if os.name == "nt":
            variants.append(str(project.root).upper())
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual(scanner.canonical_project(variant), scanner.canonical_project(project.root))

    def test_output_inside_project_is_blocked(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        nested = (project.root / "out").resolve()
        record = authorization_record(project.root.resolve(), nested, now=_now())
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.root / "out"), authorization=record)
        self.assertEqual(blocked.exception.code, "OUTPUT_OVERLAPS_PROJECT")

    def test_output_overlapping_ancestor_of_project_is_blocked(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        record = project.authorization(output_dir=str(project.root.parent))
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.root.parent), authorization=record)
        self.assertEqual(blocked.exception.code, "OUTPUT_OVERLAPS_PROJECT")

    def test_missing_allowed_action_is_blocked(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        record = project.authorization(allowed_actions=["something_else"])
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.out), authorization=record)
        self.assertEqual(blocked.exception.code, "ACTION_NOT_AUTHORIZED")

    def test_incomplete_forbidden_actions_is_blocked(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        record = project.authorization(forbidden_actions=["execute_project_code"])
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.out), authorization=record)
        self.assertEqual(blocked.exception.code, "FORBIDDEN_ACTIONS_INCOMPLETE")

    def test_expired_authorization_is_blocked(self):
        from datetime import datetime, timedelta, timezone
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        record = authorization_record(project.root.resolve(), project.out.resolve(), now=past, lifetime_seconds=60)
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.out), authorization=record)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_EXPIRED")

    def test_excluded_directories_and_sensitive_names_leave_scope(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        for excluded in (".git/config", "node_modules/lib/index.js", "build/out.py", "vendor/x.py"):
            _write(project.root / excluded, "y = 2\n")
        for sensitive in (".env", "secret_token.txt", "id_rsa"):
            _write(project.root / sensitive, "TOKEN=must_not_be_read\n")
        names = {p.relative_to(project.root).as_posix() for p in discover_files(project.root)}
        self.assertEqual(names, {"a.py"})

    def test_oversize_sensitive_file_is_skipped_without_blocking(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        _write(project.root / ".env", "A" * (limits.PROTOCOL_LIMITS["MAX_FILE_BYTES"] + 10))
        names = {p.relative_to(project.root).as_posix() for p in discover_files(project.root)}
        self.assertEqual(names, {"a.py"})

    def test_symlink_is_rejected(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        external = project.base / "external"
        external.mkdir()
        _write(external / "outside.py", "z = 3\n")
        link = project.root / "linked"
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS denied symlink creation")
        with self.assertRaises(ScanBlocked) as blocked:
            discover_files(project.root)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")


# --------------------------------------------------------------------------
# B. File and byte budgets (boundary continues, excess stops before reading)
# --------------------------------------------------------------------------
class FileAndByteBudgetTests(unittest.TestCase):
    def test_file_count_boundary_continues_and_excess_blocks_before_any_read(self):
        project = _SyntheticProject(self)
        for index in range(3):
            _write(project.root / f"f{index}.py", "x = 1\n")
        reads: list[str] = []
        original = Path.read_bytes

        def counting_read(self, *args, **kwargs):
            reads.append(str(self))
            return original(self, *args, **kwargs)

        with patch.object(scanner, "MAX_FILES", 3), patch.object(Path, "read_bytes", counting_read):
            self.assertEqual(len(discover_files(project.root)), 3)
        _write(project.root / "f3.py", "x = 1\n")
        reads.clear()
        with patch.object(scanner, "MAX_FILES", 3), patch.object(Path, "read_bytes", counting_read):
            with self.assertRaises(ScanBlocked) as blocked:
                discover_files(project.root)
        self.assertEqual(blocked.exception.code, "MAX_FILE_COUNT_EXCEEDED")
        self.assertEqual(reads, [], "No file content may be read once the file-count budget is exceeded.")

    def test_per_file_size_boundary_continues_and_excess_blocks_before_reading(self):
        project = _SyntheticProject(self)
        _write(project.root / "small.py", "x = 1\n")
        _write(project.root / "big.py", "x = 1\n" + "#" * 4090)
        # Derive the boundary from the bytes actually on disk so the test does
        # not depend on newline translation.
        big_size = (project.root / "big.py").stat().st_size
        with patch.object(scanner, "MAX_FILE_BYTES", big_size):
            self.assertEqual(len(discover_files(project.root)), 2)
        reads: list[str] = []
        original = Path.read_bytes

        def counting_read(self, *args, **kwargs):
            reads.append(str(self))
            return original(self, *args, **kwargs)

        with patch.object(scanner, "MAX_FILE_BYTES", big_size - 1), patch.object(Path, "read_bytes", counting_read):
            with self.assertRaises(ScanBlocked) as blocked:
                discover_files(project.root)
        self.assertEqual(blocked.exception.code, "MAX_FILE_SIZE_EXCEEDED")
        self.assertEqual(reads, [], "No file content may be read once the per-file budget is exceeded.")

    def test_cumulative_read_budget_boundary_and_excess(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "123")
        _write(project.root / "b.py", "45")
        with patch.object(scanner, "MAX_TOTAL_READ_BYTES", 5):
            self.assertEqual(len(discover_files(project.root)), 2)
        with patch.object(scanner, "MAX_TOTAL_READ_BYTES", 4):
            with self.assertRaises(ScanBlocked) as blocked:
                discover_files(project.root)
        self.assertEqual(blocked.exception.code, "MAX_TOTAL_READ_BYTES_EXCEEDED")

    def test_scan_aborts_before_publishing_when_cumulative_budget_is_exceeded(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        _write(project.root / "b.py", "y = 2\n")
        with patch.object(scanner, "MAX_TOTAL_READ_BYTES", 4):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_TOTAL_READ_BYTES_EXCEEDED")
        self.assertFalse((project.out / "knowledge_graph.json").exists())


# --------------------------------------------------------------------------
# C. Time and concurrency limits (fault injection, no real waiting)
# --------------------------------------------------------------------------
class TimeAndConcurrencyLimitTests(unittest.TestCase):
    def test_total_deadline_fails_closed_without_waiting(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        with self.assertRaises(ScanBlocked) as blocked:
            discover_files(project.root, deadline=1, clock=lambda: 1)
        self.assertEqual(blocked.exception.code, "TOTAL_SCAN_TIMEOUT")

    def test_inprocess_task_timeout_aborts_scan_and_releases_the_slot(self):
        supervisor = ParserTaskSupervisor()
        release = threading.Event()

        def hanging():
            release.wait(30)
            return "never"

        started = time.monotonic()
        with self.assertRaises(ScanBlocked) as blocked:
            supervisor.run_inprocess("hanging-task", hanging, timeout=0.2)
        elapsed = time.monotonic() - started
        self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
        self.assertLess(elapsed, 5.0, "The timeout must be observed, not waited out.")
        # The concurrency slot is released, so the next task can run.
        self.assertEqual(supervisor.guard.active, 0)
        self.assertEqual(supervisor.run_inprocess("after-timeout", lambda: "ok", timeout=5), "ok")
        record = supervisor.records[-2]
        self.assertEqual(record.outcome, "TIMEOUT")
        self.assertEqual(record.cancellation, "abandoned_unreliable")
        self.assertIn("not interruptible", record.detail)
        release.set()

    def test_inprocess_timeout_is_never_reported_as_cancelled(self):
        supervisor = ParserTaskSupervisor()
        release = threading.Event()
        try:
            with self.assertRaises(ScanBlocked):
                supervisor.run_inprocess("hang", lambda: release.wait(30), timeout=0.2)
        finally:
            release.set()
        timeout_records = [r for r in supervisor.records if r.outcome == "TIMEOUT"]
        self.assertEqual(len(timeout_records), 1)
        self.assertNotEqual(timeout_records[0].cancellation, "killed_and_verified")

    def test_concurrency_limit_rejects_the_second_task(self):
        guard = ConcurrencyGuard(1)
        supervisor = ParserTaskSupervisor(guard=guard)
        release = threading.Event()
        results: dict[str, str] = {}

        def first():
            release.wait(10)
            return "first"

        def second():
            return "second"

        thread = threading.Thread(target=lambda: results.setdefault("first", supervisor.run_inprocess("t1", first, timeout=10)), daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while supervisor.guard.active == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(supervisor.guard.active, 1)
        with self.assertRaises(ScanBlocked) as blocked:
            supervisor.run_inprocess("t2", second, timeout=5)
        self.assertEqual(blocked.exception.code, "CONCURRENCY_LIMIT_EXCEEDED")
        self.assertEqual(supervisor.guard.rejected, 1)
        self.assertEqual(supervisor.guard.peak_active, 1)
        release.set()
        thread.join(timeout=10)

    def test_subprocess_timeout_kills_the_child_and_verifies_termination(self):
        project = _SyntheticProject(self)
        pid_file = project.base / "child.pid"
        heartbeat = project.base / "child.heartbeat"
        sleeper = project.base / "sleeper.py"
        # The child records its pid and then keeps writing a heartbeat, so the
        # test can prove the process really stopped instead of probing a pid
        # that the OS may already have recycled.
        _write(sleeper,
               "import os, sys, time\n"
               "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
               "with open(sys.argv[2], 'w', buffering=1) as handle:\n"
               "    while True:\n"
               "        handle.write('x')\n"
               "        time.sleep(0.05)\n")
        supervisor = ParserTaskSupervisor()
        with self.assertRaises(ScanBlocked) as blocked:
            # The timeout must be comfortably above this host's interpreter
            # start-up time (~1 s on this machine) so the child actually starts
            # and records its pid/heartbeat before the supervisor kills it; a
            # shorter trigger would kill the child before it could prove it ran,
            # which is a test-environment artefact, not a supervisor behaviour.
            supervisor.run_subprocess("sleeping-adapter",
                                      [sys.executable, str(sleeper), str(pid_file), str(heartbeat)],
                                      timeout=3.0)
        self.assertEqual(blocked.exception.code, "PARSER_TASK_TIMEOUT")
        record = supervisor.records[-1]
        self.assertEqual(record.outcome, "TIMEOUT")
        self.assertEqual(record.cancellation, "killed_and_verified")
        self.assertTrue(pid_file.exists(), "The child must have started before it was killed.")
        self.assertTrue(heartbeat.exists(), "The child must have been running before it was killed.")
        # Behavioural proof of termination: once killed, the child stops writing.
        size_after_kill = heartbeat.stat().st_size
        time.sleep(0.6)
        self.assertEqual(heartbeat.stat().st_size, size_after_kill,
                         "The killed child must stop producing output; a growing heartbeat means it is still running.")
        self.assertEqual(supervisor.guard.active, 0)

    def test_subprocess_cancellation_failure_blocks_instead_of_claiming_success(self):
        project = _SyntheticProject(self)
        sleeper = project.base / "sleeper.py"
        # Short sleep: this child is deliberately never killed (termination is
        # faked as failed), so it must not linger for minutes after the test.
        _write(sleeper, "import time\ntime.sleep(15)\n")
        supervisor = ParserTaskSupervisor()
        with patch.object(ParserTaskSupervisor, "_terminate_and_verify", staticmethod(lambda process: False)):
            with self.assertRaises(ScanBlocked) as blocked:
                supervisor.run_subprocess("unkillable-adapter", [sys.executable, str(sleeper)], timeout=0.4)
        self.assertEqual(blocked.exception.code, "PARSER_CANCELLATION_FAILED")
        record = supervisor.records[-1]
        self.assertEqual(record.outcome, "CANCELLATION_FAILED")
        self.assertEqual(record.cancellation, "unverified")

    def test_abandoned_daemon_thread_does_not_block_process_exit(self):
        """Prove resource release: the interpreter must exit despite an abandoned task."""
        project = _SyntheticProject(self)
        script = project.base / "abandon.py"
        _write(script,
               "import sys, threading\n"
               "sys.path.insert(0, %r)\n" % str(Path(scanner.__file__).resolve().parents[0].parent) +
               "from usit.limits import ParserTaskSupervisor\n"
               "from usit.safety import ScanBlocked\n"
               "release = threading.Event()\n"
               "supervisor = ParserTaskSupervisor()\n"
               "try:\n"
               "    supervisor.run_inprocess('hang', lambda: release.wait(60), timeout=0.2)\n"
               "except ScanBlocked as exc:\n"
               "    print('BLOCKED', exc.code)\n"
               "print('exiting')\n")
        started = time.monotonic()
        completed = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30, check=False)
        elapsed = time.monotonic() - started
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("BLOCKED PARSER_TASK_TIMEOUT", completed.stdout)
        self.assertIn("exiting", completed.stdout)
        self.assertLess(elapsed, 20.0, "An abandoned daemon thread must not block interpreter exit.")

    def test_js_ts_adapter_timeout_is_killed_verified_and_blocked(self):
        project = _SyntheticProject(self)
        _write(project.root / "main.ts", "export const f = () => 1\n")
        sleeper = project.base / "slow_adapter.py"
        _write(sleeper, "import sys, time\ntime.sleep(120)\n")
        availability = {"available": True, "node": sys.executable, "version": "6.0.3", "parser_name": js_ts_parser.PARSER_NAME}
        # Inject a short deadline: the gate must be proved in well under a
        # second instead of waiting out the real 45s protocol limit.
        with patch.object(js_ts_parser, "parser_availability", return_value=availability), \
             patch.object(js_ts_parser, "MAX_PARSER_TASK_SECONDS", 0.5):
            started = time.monotonic()
            result = js_ts_parser.analyze_js_ts_project(project.root, [project.root / "main.ts"],
                                                        adapter=sleeper, supervisor=ParserTaskSupervisor())
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 10.0, "The adapter timeout must be proved without waiting out the real limit.")
        codes = [d["code"] for d in result["project_diagnostics"]]
        self.assertIn("ADAPTER_TIMEOUT", codes)
        diagnostic = next(d for d in result["project_diagnostics"] if d["code"] == "ADAPTER_TIMEOUT")
        self.assertEqual(diagnostic["cancellation"], "killed_and_verified")
        self.assertFalse(result["files"], "A timed-out adapter must not contribute parsed files.")


# NOTE: a pid-based liveness probe is deliberately NOT used as gate evidence.
# Two independent reasons make it unreliable after the child has been reaped:
#   1. On Windows a terminated process object survives until every handle to it
#      is closed, so OpenProcess can succeed for a dead process;
#   2. the OS may recycle the pid for an unrelated process, so the probe can
#      report "alive" for a completely different program.
# The supervisor therefore verifies termination through its own Popen handle
# (``process.poll()`` after ``kill()`` + ``wait()``), and the tests additionally
# prove the child stopped by observing that its heartbeat file stops growing.


# --------------------------------------------------------------------------
# D. Output limits, isolation and terminal status
# --------------------------------------------------------------------------
class OutputLimitAndStatusTests(unittest.TestCase):
    def test_output_budget_blocks_before_publish_and_cleans_the_stage(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        with patch.object(scanner, "MAX_OUTPUT_BYTES", 1):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_OUTPUT_BYTES_EXCEEDED")
        self.assertFalse((project.out / "knowledge_graph.json").exists())
        self.assertEqual(list(project.base.glob(".usit-stage-*")), [], "The staging directory must be cleaned up.")
        statuses = list(project.base.glob("usit-status-*.json"))
        self.assertEqual(len(statuses), 1)
        self.assertEqual(json.loads(statuses[0].read_text(encoding="utf-8"))["status"], "BLOCKED")

    def test_staged_write_failure_is_incomplete_and_cleans_the_stage(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        with patch.object(Path, "write_bytes", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertFalse((project.out / "knowledge_graph.json").exists())
        self.assertEqual(list(project.base.glob(".usit-stage-*")), [])
        marker = list(project.base.glob("usit-status-*.json"))[-1]
        payload = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "INCOMPLETE")
        self.assertNotIn("synthetic disk error", marker.read_text(encoding="utf-8"))

    def test_published_output_carries_an_explicit_complete_status(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "def run():\n    return 1\n")
        scan_project(str(project.root), str(project.out), force=True, authorization=project.authorization())
        status_path = project.out / "scan_status.json"
        self.assertTrue(status_path.is_file())
        status = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "COMPLETE")
        self.assertFalse(status["incomplete"])
        self.assertFalse(status["blocked"])
        for required in ("report.md", "report.json", "scan_manifest.json", "knowledge_graph.json",
                         "test_plan.json", "parser_contract.json", "graph_validation.json",
                         "graph_validation.md", "cache/previous_manifest.json"):
            self.assertIn(required, status["published_files"])
        # scan_status.json is written last and deliberately cannot enumerate
        # itself; its existence and terminal value are asserted directly.
        self.assertTrue(status_path.is_file())
        summary = json.loads((project.out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertEqual(summary["runtime_limits"]["max_parser_task_seconds"], 45)
        self.assertEqual(summary["runtime_limits"]["max_concurrency"], 1)
        audit = summary["parser_task_audit"]
        self.assertEqual(audit["concurrency_limit"], 1)
        self.assertEqual(audit["concurrency_peak_observed"], 1)
        self.assertEqual(audit["concurrency_rejections"], 0)
        self.assertGreaterEqual(audit["task_count"], 1)
        self.assertEqual(audit["outcomes"].get("TIMEOUT", 0), 0)

    def test_sensitive_content_is_scrubbed_from_every_published_artifact(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "def run():\n    return 1\n")
        _write(project.root / "secret_token.txt", "FAKE_TOKEN_VALUE_abc123\n")
        _write(project.root / "虚构密钥文档_测试.md", "虚构内容：FAKE_SECRET_DO_NOT_LEAK=xxxx\n")
        scan_project(str(project.root), str(project.out), force=True, authorization=project.authorization())
        for name in ("report.json", "scan_manifest.json", "knowledge_graph.json", "test_plan.json",
                     "parser_contract.json", "graph_validation.json", "scan_status.json", "report.md",
                     "graph_validation.md"):
            blob = (project.out / name).read_text(encoding="utf-8")
            for leaked in ("secret_token", "虚构密钥文档_测试", "FAKE_TOKEN_VALUE_abc123", "FAKE_SECRET_DO_NOT_LEAK"):
                self.assertNotIn(leaked, blob, f"{leaked} leaked into {name}")

    def test_corrupt_binary_and_empty_inputs_do_not_crash_or_block(self):
        project = _SyntheticProject(self)
        _write(project.root / "good.py", "def run():\n    return 1\n")
        _write(project.root / "broken.py", "def broken(:\n    return\n")
        (project.root / "binary.bin").write_bytes(b"\x00\x01\x02\x03")
        (project.root / "empty.py").write_bytes(b"")
        (project.root / "invalid.py").write_bytes(b"\xff\xfe\x00bad")
        summary = scan_project(str(project.root), str(project.out), force=True, authorization=project.authorization())
        self.assertEqual(summary["files_scanned"], 5)
        self.assertEqual(summary["status"], "COMPLETE")
        diagnostics = {item["file"] for item in summary["unknown_items"]}
        self.assertIn("broken.py", diagnostics)

    def test_injected_parser_crash_blocks_publication(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "def run():\n    return 1\n")
        # P0.7.4: per-file analysis runs as an isolated subprocess, so a parser
        # crash is injected at the worker boundary (a worker that exits non-zero)
        # rather than by patching an in-process callable.
        crash = project.base / "crash_worker.py"
        crash.write_text("import sys\nsys.stdin.read()\nsys.exit(7)\n", encoding="utf-8")
        with patch.object(scanner, "PYTHON_WORKER", str(crash)):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "PARSER_CRASHED")
        self.assertFalse((project.out / "knowledge_graph.json").exists())
        statuses = list(project.base.glob("usit-status-*.json"))
        self.assertEqual(json.loads(statuses[-1].read_text(encoding="utf-8"))["status"], "BLOCKED")


# --------------------------------------------------------------------------
# E. Fail-closed configuration gates
# --------------------------------------------------------------------------
class FailClosedConfigurationTests(unittest.TestCase):
    def test_verify_safety_limits_accepts_the_protocol_values(self):
        verify_safety_limits()

    def test_altered_limit_configuration_is_blocked(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        with patch.object(safety_module, "MAX_FILES", 999_999):
            with self.assertRaises(ScanBlocked) as blocked:
                verify_safety_limits()
            self.assertEqual(blocked.exception.code, "SAFETY_LIMIT_CONFIGURATION_INVALID")
            with self.assertRaises(ScanBlocked) as scan_blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
            self.assertEqual(scan_blocked.exception.code, "SAFETY_LIMIT_CONFIGURATION_INVALID")
            self.assertFalse((project.out / "knowledge_graph.json").exists())

    def test_missing_limit_attribute_is_blocked(self):
        project = _SyntheticProject(self)
        _write(project.root / "a.py", "x = 1\n")
        real = safety_module.MAX_OUTPUT_BYTES
        del safety_module.MAX_OUTPUT_BYTES
        try:
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
            self.assertEqual(blocked.exception.code, "SAFETY_LIMIT_CONFIGURATION_INVALID")
        finally:
            safety_module.MAX_OUTPUT_BYTES = real

    def test_non_integer_limit_is_blocked(self):
        with patch.object(safety_module, "MAX_PARSER_TASK_SECONDS", "45"):
            with self.assertRaises(ScanBlocked) as blocked:
                verify_safety_limits()
        self.assertEqual(blocked.exception.code, "SAFETY_LIMIT_CONFIGURATION_INVALID")

    def test_zero_concurrency_limit_is_blocked(self):
        with self.assertRaises(ScanBlocked) as blocked:
            ConcurrencyGuard(0)
        self.assertEqual(blocked.exception.code, "SAFETY_LIMIT_CONFIGURATION_INVALID")


# --------------------------------------------------------------------------
# F. Test isolation
# --------------------------------------------------------------------------
class TestIsolationTests(unittest.TestCase):
    def test_every_generated_project_lives_in_the_temp_directory(self):
        project = _SyntheticProject(self)
        self.assertTrue(str(project.root.resolve()).startswith(str(TEMP_ROOT)))
        self.assertTrue(str(project.out.resolve()).startswith(str(TEMP_ROOT)))
        self.assertFalse(scanner.overlaps(project.root, project.out))

    def test_this_module_never_targets_a_real_business_project(self):
        raw = Path(__file__).read_text(encoding="utf-8")
        # Drop this method's own forbidden-token declaration so the check cannot
        # be satisfied by the assertion list itself.
        source = raw.split("def test_this_module_never_targets_a_real_business_project", 1)[0]
        for forbidden in ("家庭相册", "MyFamily", "smartalbum", "设备监控", "坦克大战"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        # Only the project drive letter may appear as a Windows drive prefix.
        for prefix in sorted(set(re.findall(r"[A-Za-z]:\\\\", source))):
            self.assertEqual(prefix.lower(), "c:\\", "No absolute business drive path may appear.")

    def test_no_absolute_business_path_literal_is_used(self):
        source = Path(__file__).read_text(encoding="utf-8").split("def test_this_module", 1)[0]
        for literal in re.findall(r"['\"]([A-Za-z]:\\\\[^'\"]+)['\"]", source):
            resolved = str(Path(literal).resolve())
            self.assertTrue(resolved.startswith(str(TEMP_ROOT)) or resolved.startswith(str(Path(__file__).resolve().parent)),
                            f"Unexpected absolute path literal: {literal}")

    def test_fixtures_used_are_checked_in_synthetic_fixtures(self):
        fixture_root = Path(__file__).parent / "fixtures"
        self.assertTrue(fixture_root.is_dir())
        for name in ("python_cross_module", "javascript_typescript", "java_kotlin", "graphs"):
            self.assertTrue((fixture_root / name).is_dir(), name)

    def test_no_network_connection_is_attempted_during_a_scan(self):
        project = _SyntheticProject(self)
        # The fixture *contains* code that would open a socket; the scanner must
        # never execute it, which this guard proves by failing the test instead.
        _write(project.root / "a.py", "import socket\nsocket.create_connection(('203.0.113.1', 80))\n")

        def forbidden_connect(self, *args, **kwargs):
            raise AssertionError("A scan must not open any network connection.")

        with patch("socket.socket.connect", forbidden_connect), \
             patch("socket.socket.connect_ex", forbidden_connect), \
             patch("socket.create_connection", forbidden_connect):
            summary = scan_project(str(project.root), str(project.out), force=True, authorization=project.authorization())
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertFalse(summary["safety"]["network_used"])


if __name__ == "__main__":
    unittest.main()
