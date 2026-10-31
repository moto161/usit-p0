"""Runtime resource limits, concurrency and fail-closed cancellation (P0.7.2).

This module turns the numeric caps in ``PILOT-READONLY-PROTOCOL.md`` into
program-enforced behaviour. It deliberately does not rewrite any parser or the
graph schema; it only wraps the existing parser entry points.

Cancellation boundary (stated honestly, see the P0.7.2 execution report):

* **Subprocess parser tasks** are cancellable. The supervisor kills the child,
  waits for it, and *verifies* the process is gone before reporting success. A
  timeout that cannot be verified raises ``PARSER_CANCELLATION_FAILED``.
* **In-process parser tasks** are *not* interruptible on any supported
  platform. The supervisor observes the deadline, aborts the scan
  (fail-closed) and runs the work on a daemon thread so the interpreter can
  still exit. The abandoned computation is recorded as
  ``cancellation="abandoned_unreliable"`` and is never reported as cancelled.

Nothing in this module relaxes a limit, skips a check, or degrades a failure
into a successful scan.
"""
from __future__ import annotations

from dataclasses import dataclass
import subprocess
import threading
import time
from typing import Any, Callable

from os import PathLike

from .safety import (
    MAX_CONCURRENCY,
    MAX_PARSER_TASK_SECONDS,
    ScanBlocked,
)

# The protocol values. ``verify_safety_limits`` fails closed when the imported
# constants no longer match, so a monkey-patched or edited limit cannot silently
# disable a gate.
PROTOCOL_LIMITS: dict[str, int] = {
    "MAX_FILES": 5_000,
    "MAX_FILE_BYTES": 1_048_576,
    "MAX_TOTAL_READ_BYTES": 268_435_456,
    "MAX_SCAN_SECONDS": 600,
    "MAX_PARSER_TASK_SECONDS": 45,
    "MAX_CONCURRENCY": 1,
    "MAX_OUTPUT_BYTES": 268_435_456,
    "MAX_AUTHORIZATION_BYTES": 16_384,
}


def verify_safety_limits() -> None:
    """Fail closed unless every protocol limit exists and equals the required value."""
    from . import safety

    problems: list[dict[str, Any]] = []
    for name, expected in PROTOCOL_LIMITS.items():
        actual = getattr(safety, name, None)
        if isinstance(actual, bool) or not isinstance(actual, int) or actual != expected:
            problems.append({"limit": name, "expected": expected, "actual": actual})
    if problems:
        joined = ", ".join(f"{p['limit']}={p['actual']!r}" for p in problems)
        raise ScanBlocked(
            "SAFETY_LIMIT_CONFIGURATION_INVALID",
            "Required runtime limits are missing or altered; refusing to scan: " + joined,
        )


class ConcurrencyGuard:
    """A real, countable limiter for concurrent parser tasks."""

    def __init__(self, limit: int = MAX_CONCURRENCY):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ScanBlocked("SAFETY_LIMIT_CONFIGURATION_INVALID", "Concurrency limit must be a positive integer.")
        self._limit = limit
        self._lock = threading.Lock()
        self._active = 0
        self.peak_active = 0
        self.rejected = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def __enter__(self) -> "ConcurrencyGuard":
        with self._lock:
            if self._active >= self._limit:
                self.rejected += 1
                raise ScanBlocked(
                    "CONCURRENCY_LIMIT_EXCEEDED",
                    f"At most {self._limit} parser task(s) may run concurrently.",
                )
            self._active += 1
            self.peak_active = max(self.peak_active, self._active)
        return self

    def __exit__(self, *_exc: Any) -> bool:
        with self._lock:
            self._active -= 1
        return False


@dataclass(frozen=True)
class ParserTaskRecord:
    """One auditable parser-task outcome. Never mutated after creation."""

    name: str
    kind: str                      # inprocess | subprocess
    outcome: str                   # COMPLETED | TIMEOUT | FAILED | START_FAILED | CANCELLATION_FAILED
    timeout_seconds: float
    elapsed_seconds: float
    cancellation: str              # not_applicable | killed_and_verified | abandoned_unreliable | unverified
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "outcome": self.outcome,
            "timeout_seconds": self.timeout_seconds,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "cancellation": self.cancellation,
            "detail": self.detail,
        }


class ParserTaskSupervisor:
    """Runs parser tasks under the per-task timeout and the concurrency cap."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, guard: ConcurrencyGuard | None = None):
        self._clock = clock
        self.guard = guard if guard is not None else ConcurrencyGuard(MAX_CONCURRENCY)
        self._records: list[ParserTaskRecord] = []
        self._records_lock = threading.Lock()

    # ---------------------------------------------------------------- records
    @property
    def records(self) -> tuple[ParserTaskRecord, ...]:
        with self._records_lock:
            return tuple(self._records)

    def _record(self, record: ParserTaskRecord) -> None:
        with self._records_lock:
            self._records.append(record)

    def audit(self) -> dict[str, Any]:
        rows = [record.as_dict() for record in self.records]
        outcomes: dict[str, int] = {}
        cancellations: dict[str, int] = {}
        for row in rows:
            outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
            cancellations[row["cancellation"]] = cancellations.get(row["cancellation"], 0) + 1
        return {
            "task_count": len(rows),
            "outcomes": outcomes,
            "cancellations": cancellations,
            "concurrency_limit": self.guard.limit,
            "concurrency_peak_observed": self.guard.peak_active,
            "concurrency_rejections": self.guard.rejected,
            "records": rows,
        }

    # ----------------------------------------------------------- in-process
    def run_inprocess(self, name: str, fn: Callable[[], Any], *, timeout: float | None = None) -> Any:
        """Run ``fn`` under the per-task deadline.

        In-process work cannot be interrupted, so a timeout aborts the scan and
        abandons the daemon thread. This is recorded as ``abandoned_unreliable``
        and is never described as a successful cancellation.
        """
        limit = MAX_PARSER_TASK_SECONDS if timeout is None else timeout
        started = self._clock()
        box: dict[str, Any] = {}

        def runner() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised verbatim below
                box["error"] = exc

        with self.guard:
            thread = threading.Thread(target=runner, name=f"usit-parser-{name}", daemon=True)
            thread.start()
            thread.join(limit)
            elapsed = self._clock() - started
            if thread.is_alive():
                self._record(ParserTaskRecord(
                    name=name, kind="inprocess", outcome="TIMEOUT", timeout_seconds=limit,
                    elapsed_seconds=elapsed, cancellation="abandoned_unreliable",
                    detail="In-process work is not interruptible; the daemon thread is abandoned and the scan is aborted.",
                ))
                raise ScanBlocked(
                    "PARSER_TASK_TIMEOUT",
                    f"Parser task {name} exceeded the {limit}s limit; the scan was aborted.",
                )
            if "error" in box:
                self._record(ParserTaskRecord(
                    name=name, kind="inprocess", outcome="FAILED", timeout_seconds=limit,
                    elapsed_seconds=elapsed, cancellation="not_applicable", detail=type(box["error"]).__name__,
                ))
                raise box["error"]
            self._record(ParserTaskRecord(
                name=name, kind="inprocess", outcome="COMPLETED", timeout_seconds=limit,
                elapsed_seconds=elapsed, cancellation="not_applicable", detail="",
            ))
            return box["value"]

    # ------------------------------------------------------------ subprocess
    def run_subprocess(
        self,
        name: str,
        argv: list[str],
        *,
        timeout: float | None = None,
        cwd: str | PathLike | None = None,
        input_text: str | None = None,
    ) -> dict[str, Any]:
        """Run an argv-only subprocess parser task and verify cancellation."""
        limit = MAX_PARSER_TASK_SECONDS if timeout is None else timeout
        started = self._clock()
        with self.guard:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=None if cwd is None else str(cwd),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                )
            except OSError as exc:
                self._record(ParserTaskRecord(
                    name=name, kind="subprocess", outcome="START_FAILED", timeout_seconds=limit,
                    elapsed_seconds=self._clock() - started, cancellation="not_applicable",
                    detail=type(exc).__name__,
                ))
                raise ScanBlocked(
                    "PARSER_START_FAILED",
                    f"Parser task {name} could not start ({type(exc).__name__}).",
                ) from exc
            try:
                stdout, stderr = process.communicate(input=input_text, timeout=limit)
            except subprocess.TimeoutExpired:
                terminated = self._terminate_and_verify(process)
                elapsed = self._clock() - started
                if not terminated:
                    self._record(ParserTaskRecord(
                        name=name, kind="subprocess", outcome="CANCELLATION_FAILED", timeout_seconds=limit,
                        elapsed_seconds=elapsed, cancellation="unverified",
                        detail="Child process could not be confirmed terminated.",
                    ))
                    raise ScanBlocked(
                        "PARSER_CANCELLATION_FAILED",
                        f"Parser task {name} timed out and the child process could not be terminated.",
                    )
                self._record(ParserTaskRecord(
                    name=name, kind="subprocess", outcome="TIMEOUT", timeout_seconds=limit,
                    elapsed_seconds=elapsed, cancellation="killed_and_verified",
                    detail="Child process was killed and its exit was confirmed.",
                ))
                raise ScanBlocked(
                    "PARSER_TASK_TIMEOUT",
                    f"Parser task {name} exceeded the {limit}s limit; the child process was terminated.",
                )
            self._record(ParserTaskRecord(
                name=name, kind="subprocess",
                outcome="COMPLETED" if process.returncode == 0 else "FAILED",
                timeout_seconds=limit, elapsed_seconds=self._clock() - started, cancellation="not_applicable",
                detail="" if process.returncode == 0 else "exit_code=" + str(process.returncode),
            ))
            return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr}

    @staticmethod
    def _terminate_and_verify(process: subprocess.Popen) -> bool:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return False
        return process.poll() is not None
