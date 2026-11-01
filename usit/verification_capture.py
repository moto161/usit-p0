"""Bounded, manifest-backed capture for explicitly requested verification runs.

This module does not decide whether a command is authorized. Callers must make
that decision before calling :func:`capture_run`. It never invokes a shell,
scans directories, or writes inside the project tree. Only explicitly listed
project-relative files are snapshotted.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

__all__ = ["STATUSES", "CaptureResult", "capture_run", "verify_manifest"]

STATUSES = frozenset({
    "PASS", "FAIL", "NOT_TESTED", "NOT_VERIFIED", "INTERRUPTED",
    "INFRASTRUCTURE_ERROR",
})
_MANIFEST_NAME = "evidence_manifest.json"
_REQUIRED_ARTIFACTS = frozenset({
    "stdout.log", "stderr.log", "pre_snapshot.json", "post_snapshot.json",
    "diff.json", "final_gate_report.json",
})
_RUN_ID_RE = re.compile(r"^run_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{32}$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
DEFAULT_COMMAND_TIMEOUT_SECONDS = 600
MAX_COMMAND_TIMEOUT_SECONDS = 600
MAX_POLL_INTERVAL_SECONDS = 1.0
MAX_CAPTURE_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_FILES = 5000
MAX_SNAPSHOT_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_TOTAL_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_VERIFIED_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_COMMAND_ARGS = 128
MAX_COMMAND_ARG_BYTES = 8192
MAX_COMMAND_TOTAL_BYTES = 64 * 1024
MAX_APPROVAL_REF_CHARS = 256


@dataclass(frozen=True)
class CaptureResult:
    """Outcome and evidence locations returned by :func:`capture_run`."""

    run_id: str
    status: str
    exit_code: int | None
    timed_out: bool
    interrupted: bool
    evidence_dir: str
    manifest_path: str | None
    report_path: str | None
    manifest_valid: bool
    evidence_complete: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    captured_output_bytes: int = 0
    output_limit_exceeded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "interrupted": self.interrupted,
            "evidence_dir": self.evidence_dir,
            "manifest_path": self.manifest_path,
            "report_path": self.report_path,
            "manifest_valid": self.manifest_valid,
            "evidence_complete": self.evidence_complete,
            "errors": list(self.errors),
            "captured_output_bytes": self.captured_output_bytes,
            "output_limit_exceeded": self.output_limit_exceeded,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _reject_reparse_components(path: Path) -> None:
    """Reject symlink/reparse components rather than following them silently."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        attrs = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attrs & _REPARSE_POINT:
            raise ValueError(f"reparse path component refused: {current}")


def _canonical_directory(value: str | os.PathLike[str], label: str) -> Path:
    path = Path(value).expanduser()
    _reject_reparse_components(path)
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} is not a directory")
    return resolved


def _safe_relative_file(root: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not rel or "\\" in rel:
        raise ValueError("snapshot paths must be non-empty POSIX relative paths")
    if len(rel.encode("utf-8")) > 1024:
        raise ValueError("snapshot path exceeds the 1024-byte path limit")
    pure = PurePosixPath(rel)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError(f"snapshot path escapes or is not normalised: {rel!r}")
    if pure.as_posix() != rel or ":" in pure.parts[0]:
        raise ValueError(f"snapshot path is not a normalised relative path: {rel!r}")
    path = root.joinpath(*pure.parts)
    _reject_reparse_components(path)
    resolved = path.resolve(strict=False)
    if not _is_within(resolved, root) or resolved == root:
        raise ValueError(f"snapshot path escapes project root: {rel!r}")
    return path


def _read_snapshot(root: Path, path: Path, rel: str, remaining_total_bytes: int) -> dict[str, Any]:
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return {"path": rel, "state": "MISSING", "size": None, "sha256": None}
    if not stat.S_ISREG(before.st_mode):
        raise OSError(f"snapshot target is not a regular file: {rel}")
    if before.st_size > MAX_SNAPSHOT_FILE_BYTES:
        raise OSError(f"snapshot target exceeds the {MAX_SNAPSHOT_FILE_BYTES}-byte per-file limit: {rel}")
    if before.st_size > remaining_total_bytes:
        raise OSError(f"snapshot set exceeds the {MAX_SNAPSHOT_TOTAL_BYTES}-byte total limit at: {rel}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(fd)
        _reject_reparse_components(path)
        resolved_after_open = path.resolve(strict=True)
        if not _is_within(resolved_after_open, root) or resolved_after_open == root:
            raise OSError(f"snapshot target escaped project root before read: {rel}")
        path_after_open = os.lstat(path)
        opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        path_identity_after_open = (path_after_open.st_dev, path_after_open.st_ino, path_after_open.st_size, path_after_open.st_mtime_ns, path_after_open.st_ctime_ns)
        if opened_identity != before_identity or opened_identity != path_identity_after_open:
            raise OSError(f"snapshot target changed before read: {rel}")
        while size <= MAX_SNAPSHOT_FILE_BYTES:
            block = os.read(fd, min(1024 * 1024, MAX_SNAPSHOT_FILE_BYTES + 1 - size))
            if not block:
                break
            size += len(block)
            if size > MAX_SNAPSHOT_FILE_BYTES or size > remaining_total_bytes:
                raise OSError(f"snapshot target exceeded its read budget: {rel}")
            digest.update(block)
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    after_path = os.lstat(path)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    identity_after = (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns, after_fd.st_ctime_ns)
    path_identity = (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns, after_path.st_ctime_ns)
    if identity_before != identity_after or identity_after != path_identity or size != after_fd.st_size:
        raise OSError(f"snapshot target changed during read: {rel}")
    return {"path": rel, "state": "PRESENT", "size": size, "sha256": digest.hexdigest()}


def _snapshot(root: Path, relative_paths: Sequence[str]) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    total_bytes = 0
    if len(relative_paths) > MAX_SNAPSHOT_FILES:
        message = f"snapshot file count exceeds {MAX_SNAPSHOT_FILES}"
        return ([{"path": rel, "state": "ERROR", "size": None, "sha256": None} for rel in relative_paths],
                [message])
    for rel in relative_paths:
        try:
            path = _safe_relative_file(root, rel)
            item = _read_snapshot(root, path, rel, MAX_SNAPSHOT_TOTAL_BYTES - total_bytes)
            entries.append(item)
            if item["state"] == "PRESENT":
                total_bytes += int(item["size"])
        except (OSError, ValueError, TypeError) as exc:
            entries.append({"path": rel, "state": "ERROR", "size": None, "sha256": None})
            errors.append(f"{rel}: {type(exc).__name__}: {exc}")
    return entries, errors


def _terminate_process_tree(process: subprocess.Popen) -> int:
    """Terminate the command and descendants created in its isolated process group."""
    if os.name == "nt":
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        except (OSError, ValueError):
            try:
                process.terminate()
            except OSError:
                pass
        try:
            return process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, shell=False, timeout=5, check=False)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass
            return process.wait()
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.wait()


def _drain_bounded(stream, sink, label: str, lock: threading.Lock, state: dict[str, int],
                   limit_event: threading.Event, reader_errors: list[str]) -> None:
    """Drain a child pipe while retaining no more than the shared output budget."""
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            with lock:
                remaining = max(0, MAX_CAPTURE_OUTPUT_BYTES - state["bytes"])
                if remaining:
                    sink.write(chunk[:remaining])
                    state["bytes"] += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    limit_event.set()
        sink.flush()
        os.fsync(sink.fileno())
    except Exception as exc:
        reader_errors.append(f"{label} capture failed: {type(exc).__name__}: {exc}")
    finally:
        try:
            stream.close()
        except OSError:
            pass

def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while creating evidence")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_file(path: Path, data: Mapping[str, Any]) -> None:
    payload = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _write_exclusive(path, payload)


def _read_verified_bytes(path: Path, max_bytes: int, label: str) -> bytes:
    """Read a bounded regular file through a no-follow descriptor and verify identity."""
    _reject_reparse_components(path)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise OSError(f"{label} is not a regular file")
    if before.st_size > max_bytes:
        raise OSError(f"{label} exceeds the {max_bytes}-byte read limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        _reject_reparse_components(path)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        if identity != opened_identity:
            raise OSError(f"{label} changed before read")
        chunks = []
        size = 0
        while size <= max_bytes:
            block = os.read(fd, min(1024 * 1024, max_bytes + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > max_bytes:
                raise OSError(f"{label} exceeded its read limit during read")
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    after_path = os.lstat(path)
    after_identity = (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns, after_fd.st_ctime_ns)
    path_identity = (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns, after_path.st_ctime_ns)
    raw = b"".join(chunks)
    if identity != after_identity or after_identity != path_identity or len(raw) != after_fd.st_size:
        raise OSError(f"{label} changed during read")
    return raw


def _artifact_record(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    _reject_reparse_components(path)
    resolved = path.resolve(strict=True)
    if not _is_within(resolved, root):
        raise OSError(f"invalid evidence artifact: {name}")
    raw = _read_verified_bytes(resolved, MAX_VERIFIED_ARTIFACT_BYTES, f"evidence artifact {name}")
    return {"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def verify_manifest(evidence_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Verify every artifact listed by an evidence manifest, without mutation."""
    errors: list[str] = []
    checked = 0
    try:
        root = _canonical_directory(evidence_dir, "evidence directory")
        manifest_path = root / _MANIFEST_NAME
        raw_manifest = _read_verified_bytes(manifest_path, MAX_MANIFEST_BYTES, "evidence manifest")
        manifest = json.loads(raw_manifest.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("evidence manifest root must be an object")
        if manifest.get("schema_version") != 1 or not _RUN_ID_RE.fullmatch(str(manifest.get("run_id", ""))):
            raise ValueError("unsupported or invalid manifest identity")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 32:
            raise ValueError("manifest artifacts must contain between 1 and 32 entries")
        seen: set[str] = set()
        for record in artifacts:
            name = record.get("path") if isinstance(record, dict) else None
            if not isinstance(name, str) or not name or "\\" in name:
                errors.append("invalid artifact path in manifest")
                continue
            pure = PurePosixPath(name)
            if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts) or pure.as_posix() != name:
                errors.append(f"unsafe artifact path: {name!r}")
                continue
            if name == _MANIFEST_NAME or name in seen:
                errors.append(f"duplicate or recursive manifest artifact: {name!r}")
                continue
            seen.add(name)
            artifact = root.joinpath(*pure.parts)
            _reject_reparse_components(artifact)
            resolved = artifact.resolve(strict=True)
            if not _is_within(resolved, root) or not resolved.is_file():
                errors.append(f"artifact missing or outside evidence directory: {name}")
                continue
            actual = _artifact_record(root, name)
            checked += 1
            if actual["size"] != record.get("size") or actual["sha256"] != record.get("sha256"):
                errors.append(f"artifact size/hash mismatch: {name}")
        missing = _REQUIRED_ARTIFACTS - seen
        if missing:
            errors.append("manifest omits required artifacts: " + ", ".join(sorted(missing)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"valid": not errors, "artifacts_checked": checked, "errors": errors}


def capture_run(
    command: Sequence[str],
    *,
    project_root: str | os.PathLike[str],
    evidence_parent: str | os.PathLike[str],
    files: Sequence[str],
    approval_ref: str,
    cwd: str | os.PathLike[str] | None = None,
    timeout: float | None = None,
    interrupt_event: threading.Event | None = None,
    poll_interval: float = 0.05,
) -> CaptureResult:
    """Run one argv command and capture bounded, explicitly named evidence.

    ``approval_ref`` is a required pointer to caller-validated authorization;
    this module records it but does not validate the external approval itself.
    The evidence parent must already exist outside the project. A fresh child
    directory is created exclusively for each run. Commands are passed directly
    to ``subprocess.Popen`` with ``shell=False`` and ``stdin=DEVNULL``.
    """
    if isinstance(command, (str, bytes)) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("command must be a non-empty sequence of non-empty strings")
    if not isinstance(approval_ref, str) or not approval_ref.strip() or len(approval_ref) > MAX_APPROVAL_REF_CHARS:
        raise ValueError(f"a caller-validated approval_ref of at most {MAX_APPROVAL_REF_CHARS} characters is required")
    if (not files or any(not isinstance(item, str) for item in files)
            or len(set(files)) != len(files)):
        raise ValueError("files must contain at least one unique project-relative path")
    if len(files) > MAX_SNAPSHOT_FILES:
        raise ValueError(f"files exceeds the {MAX_SNAPSHOT_FILES}-entry snapshot limit")
    if timeout is None:
        timeout = DEFAULT_COMMAND_TIMEOUT_SECONDS
    if len(command) > MAX_COMMAND_ARGS or any(len(item.encode("utf-8")) > MAX_COMMAND_ARG_BYTES for item in command) or sum(len(item.encode("utf-8")) for item in command) > MAX_COMMAND_TOTAL_BYTES:
        raise ValueError("command arguments exceed the configured count or byte limits")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_COMMAND_TIMEOUT_SECONDS):
        raise ValueError(f"timeout must be finite and between zero and {MAX_COMMAND_TIMEOUT_SECONDS} seconds")
    if (isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float))
            or not math.isfinite(poll_interval) or poll_interval <= 0
            or poll_interval > MAX_POLL_INTERVAL_SECONDS):
        raise ValueError(f"poll_interval must be finite and between zero and {MAX_POLL_INTERVAL_SECONDS} seconds")

    project = _canonical_directory(project_root, "project root")
    parent = _canonical_directory(evidence_parent, "evidence parent")
    if _is_within(parent, project):
        raise ValueError("evidence parent must be outside the project root")
    work = project if cwd is None else _canonical_directory(cwd, "working directory")
    if not _is_within(work, project):
        raise ValueError("working directory must remain inside the project root")
    for rel in files:
        _safe_relative_file(project, rel)

    now = datetime.now(timezone.utc)
    run_id = f"run_{now:%Y%m%dT%H%M%SZ}_{secrets.token_hex(16)}"
    evidence = parent / run_id
    evidence.mkdir(mode=0o700, exist_ok=False)
    stdout_path, stderr_path = evidence / "stdout.log", evidence / "stderr.log"
    manifest_path, report_path = evidence / _MANIFEST_NAME, evidence / "final_gate_report.json"
    started = _utc_now()
    errors: list[str] = []
    exit_code: int | None = None
    timed_out = False
    interrupted = False
    status = "NOT_TESTED"
    pre, pre_errors = _snapshot(project, files)
    evidence_complete = False
    manifest_valid = False
    process_started = False
    captured_output_bytes = 0
    output_limit_exceeded = False
    try:
        _write_json_file(evidence / "pre_snapshot.json", {"run_id": run_id, "captured_at": started, "files": pre, "errors": pre_errors})
        with stdout_path.open("xb") as out_stream, stderr_path.open("xb") as err_stream:
            if pre_errors:
                errors.extend(pre_errors)
                status = "NOT_VERIFIED"
            else:
                try:
                    popen_kwargs = {
                        "cwd": work, "stdin": subprocess.DEVNULL,
                        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                        "shell": False, "close_fds": True, "bufsize": 0,
                    }
                    if os.name == "nt":
                        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    else:
                        popen_kwargs["start_new_session"] = True
                    process = subprocess.Popen(list(command), **popen_kwargs)
                    process_started = True
                except OSError as exc:
                    errors.append(f"startup failed: {type(exc).__name__}: {exc}")
                    status = "INFRASTRUCTURE_ERROR"
                if process_started:
                    output_state = {"bytes": 0}
                    output_lock = threading.Lock()
                    output_limit_event = threading.Event()
                    reader_errors: list[str] = []
                    out_thread = threading.Thread(target=_drain_bounded,
                        args=(process.stdout, out_stream, "stdout", output_lock, output_state, output_limit_event, reader_errors), daemon=True)
                    err_thread = threading.Thread(target=_drain_bounded,
                        args=(process.stderr, err_stream, "stderr", output_lock, output_state, output_limit_event, reader_errors), daemon=True)
                    out_thread.start()
                    err_thread.start()
                    deadline = time.monotonic() + timeout
                    try:
                        while process.poll() is None:
                            if output_limit_event.is_set():
                                output_limit_exceeded = True
                                break
                            if interrupt_event is not None and interrupt_event.is_set():
                                interrupted = True
                                break
                            if time.monotonic() >= deadline:
                                timed_out = True
                                break
                            time.sleep(poll_interval)
                    except KeyboardInterrupt:
                        interrupted = True
                    if output_limit_event.is_set():
                        output_limit_exceeded = True
                    if interrupted or timed_out or output_limit_exceeded or reader_errors:
                        exit_code = _terminate_process_tree(process)
                    else:
                        exit_code = process.wait()
                    out_thread.join(timeout=10)
                    err_thread.join(timeout=10)
                    if output_limit_event.is_set():
                        output_limit_exceeded = True
                    captured_output_bytes = output_state["bytes"]
                    if out_thread.is_alive() or err_thread.is_alive():
                        reader_errors.append("output reader did not reach EOF after process termination")
                    errors.extend(reader_errors)
                    if output_limit_exceeded:
                        status = "FAIL"
                        errors.append(f"command output exceeded the {MAX_CAPTURE_OUTPUT_BYTES}-byte capture limit")
                    elif reader_errors:
                        status = "INFRASTRUCTURE_ERROR"
                    elif interrupted:
                        status = "INTERRUPTED"
                    elif timed_out:
                        status = "FAIL"
                        errors.append("command timed out")
                    elif exit_code != 0:
                        status = "FAIL"
                        errors.append(f"command returned non-zero exit code {exit_code}")
                    else:
                        status = "PASS"
        post, post_errors = _snapshot(project, files) if not pre_errors else ([], [])
        errors.extend(post_errors)
        diff: list[dict[str, Any]] = []
        pre_by_path = {entry["path"]: entry for entry in pre}
        for after in post:
            before = pre_by_path.get(after["path"], {})
            if (before.get("state"), before.get("sha256"), before.get("size")) != (after.get("state"), after.get("sha256"), after.get("size")):
                diff.append({"path": after["path"], "before": before, "after": after})
        if post_errors:
            status = "NOT_VERIFIED"
        elif diff and status == "PASS":
            status = "FAIL"
            errors.append("one or more specified project files changed")

        _write_json_file(evidence / "post_snapshot.json", {"run_id": run_id, "captured_at": _utc_now(), "files": post, "errors": post_errors})
        _write_json_file(evidence / "diff.json", {"run_id": run_id, "changes": diff})
        ended = _utc_now()
        report = {
            "schema_version": 1,
            "run_id": run_id,
            "status": status,
            "command": list(command),
            "approval_ref": approval_ref,
            "cwd": str(work),
            "started_at": started,
            "ended_at": ended,
            "process_started": process_started,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "interrupted": interrupted,
            "captured_output_bytes": captured_output_bytes,
            "output_limit_exceeded": output_limit_exceeded,
            # Snapshot/read completeness is known now; the caller receives the
            # final manifest-validation result in CaptureResult after hashing.
            "evidence_complete": not pre_errors and not post_errors,
            "evidence_manifest": _MANIFEST_NAME,
            "stdout": "stdout.log",
            "stderr": "stderr.log",
            "snapshot_files": list(files),
            "changes": diff,
            "errors": errors,
            "gate_rule": "PASS requires exit code 0, complete evidence, valid manifest, and no specified-file changes.",
        }
        # These files are exclusive-created; a collision or write error is fail-closed.
        artifact_names = ["stdout.log", "stderr.log", "pre_snapshot.json", "post_snapshot.json", "diff.json"]
        artifact_records = [_artifact_record(evidence, name) for name in artifact_names]
        _write_json_file(report_path, report)
        artifact_records.append(_artifact_record(evidence, "final_gate_report.json"))
        manifest = {"schema_version": 1, "run_id": run_id, "created_at": _utc_now(), "artifacts": artifact_records}
        _write_json_file(manifest_path, manifest)
        verification = verify_manifest(evidence)
        manifest_valid = bool(verification["valid"])
        evidence_complete = manifest_valid and not pre_errors and not post_errors
        if not manifest_valid:
            status = "NOT_VERIFIED"
            errors.extend(verification["errors"])
        if status == "PASS" and (not evidence_complete or not manifest_valid or diff):
            status = "NOT_VERIFIED"
        return CaptureResult(run_id, status, exit_code, timed_out, interrupted,
                             str(evidence), str(manifest_path), str(report_path),
                             manifest_valid, evidence_complete, tuple(errors),
                             captured_output_bytes, output_limit_exceeded)
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"evidence capture failed: {type(exc).__name__}: {exc}")
        # Do not fabricate a manifest or PASS when evidence writing is incomplete.
        return CaptureResult(run_id, "INFRASTRUCTURE_ERROR", exit_code, timed_out,
                             interrupted, str(evidence),
                             str(manifest_path) if manifest_path.exists() else None,
                             str(report_path) if report_path.exists() else None,
                             False, False, tuple(errors),
                             captured_output_bytes, output_limit_exceeded)
