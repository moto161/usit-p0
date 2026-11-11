"""Bounded, manifest-backed capture for explicitly requested verification runs.

This module does not decide whether a command is authorized. Callers must make
that decision before calling :func:`capture_run`. It never invokes a shell,
scans directories, or writes inside the project tree. Only explicitly listed
project-relative files are snapshotted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

try:
    import pwd
except ImportError:  # Non-POSIX platforms have no pwd module.
    pwd = None  # type: ignore[assignment]

from .safeio import read_bounded_no_follow

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
    runner_user: str | None = None
    runner_uid: int | None = None

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
            "runner_user": self.runner_user,
            "runner_uid": self.runner_uid,
        }


def _resolve_runner_user(user: str | int | None) -> tuple[str | None, int | None]:
    """Resolve a requested runner user to (name, uid); (None, None) when unset.

    Fail-closed: a non-POSIX platform, a non-root parent, an unknown name,
    or uid 0 is refused with ValueError. Callers must never silently fall
    back to running as the parent UID when isolation was requested.
    """
    if user is None:
        return None, None
    if isinstance(user, bool) or not isinstance(user, (str, int)):
        raise ValueError("runner user must be a username string or a UID integer")
    if os.name != "posix":
        raise ValueError("running as a dedicated user requires a POSIX platform")
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        raise ValueError("running as a dedicated user requires the parent process to run as root")
    if pwd is None:
        raise ValueError("running as a dedicated user requires the pwd module")
    if isinstance(user, str):
        if not user:
            raise ValueError("runner user name must not be empty")
        try:
            entry = pwd.getpwnam(user)
        except KeyError:
            raise ValueError(f"runner user does not exist: {user}") from None
        name, uid = entry.pw_name, entry.pw_uid
    else:
        uid = user
        try:
            name = pwd.getpwuid(uid).pw_name
        except KeyError:
            # A numeric UID needs no passwd entry for setuid to work.
            name = None
    if uid < 1:
        raise ValueError("runner user must not be root (uid 0)")
    return name, uid


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


def _read_snapshot(path: Path, rel: str) -> dict[str, Any]:
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return {"path": rel, "state": "MISSING", "size": None, "sha256": None, "ctime_ns": None}
    if not stat.S_ISREG(before.st_mode):
        raise OSError(f"snapshot target is not a regular file: {rel}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OSError(f"snapshot target changed before read: {rel}")
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
        after_fd = os.fstat(fd)
    finally:
        os.close(fd)
    after_path = os.lstat(path)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns)
    path_identity = (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns)
    if identity_before != identity_after or identity_after != path_identity or size != after_fd.st_size:
        raise OSError(f"snapshot target changed during read: {rel}")
    # ctime_ns is recorded so a modify-then-restore cycle is detectable on
    # POSIX: content and mtime can both be restored (os.utime), but a
    # non-privileged process cannot reset ctime, so any write leaves an
    # irreversible mark. On Windows st_ctime is the file creation time, which
    # a write does not change; there the field still catches delete-and-
    # recreate (creation time changes) but not in-place modify-restore.
    return {"path": rel, "state": "PRESENT", "size": size, "sha256": digest.hexdigest(),
            "ctime_ns": int(after_path.st_ctime_ns)}


def _snapshot(root: Path, relative_paths: Sequence[str]) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for rel in relative_paths:
        try:
            path = _safe_relative_file(root, rel)
            entries.append(_read_snapshot(path, rel))
        except (OSError, ValueError, TypeError) as exc:
            entries.append({"path": rel, "state": "ERROR", "size": None, "sha256": None, "ctime_ns": None})
            errors.append(f"{rel}: {type(exc).__name__}: {exc}")
    return entries, errors


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


def _artifact_record(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    _reject_reparse_components(path)
    resolved = path.resolve(strict=True)
    if not _is_within(resolved, root) or not resolved.is_file():
        raise OSError(f"invalid evidence artifact: {name}")
    digest = hashlib.sha256()
    size = 0
    with resolved.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"path": name, "size": size, "sha256": digest.hexdigest()}


def verify_manifest(
    evidence_dir: str | os.PathLike[str], hmac_key: bytes | None = None
) -> dict[str, Any]:
    """Verify every artifact listed by an evidence manifest, without mutation.

    When ``hmac_key`` is given, the manifest's HMAC signature is also
    verified: INVALID / WRONG_KEY / MALFORMED makes the manifest invalid
    (fail-closed), while a missing signature on an old manifest is reported
    as MISSING rather than an error. Without a key the signature is
    NOT_CHECKED. A VALID signature proves the parent process produced this
    manifest; it does not prove the test process executed the reported tests.
    """
    errors: list[str] = []
    checked = 0
    signature_status = "NOT_CHECKED"
    try:
        root = _canonical_directory(evidence_dir, "evidence directory")
        manifest_path = root / _MANIFEST_NAME
        _reject_reparse_components(manifest_path)
        manifest = json.loads(read_bounded_no_follow(manifest_path, 50 * 1024 * 1024).decode("utf-8"))
        if manifest.get("schema_version") != 1 or not _RUN_ID_RE.fullmatch(str(manifest.get("run_id", ""))):
            raise ValueError("unsupported or invalid manifest identity")
        if hmac_key is not None:
            from .evidence_signing import verify_document_signature

            signature_status = verify_document_signature(manifest, hmac_key)
            if signature_status in {"INVALID", "WRONG_KEY", "MALFORMED"}:
                errors.append(f"evidence manifest signature is {signature_status}")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError("manifest artifacts must be a non-empty list")
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
    return {
        "valid": not errors,
        "artifacts_checked": checked,
        "errors": errors,
        "signature_status": signature_status,
    }


def capture_run(
    command: Sequence[str],
    *,
    project_root: str | os.PathLike[str],
    evidence_parent: str | os.PathLike[str],
    files: Sequence[str],
    approval_ref: str,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    interrupt_event: threading.Event | None = None,
    poll_interval: float = 0.05,
    user: str | int | None = None,
) -> CaptureResult:
    """Run one argv command and capture bounded, explicitly named evidence.

    ``approval_ref`` is a required pointer to caller-validated authorization;
    this module records it but does not validate the external approval itself.
    The evidence parent must already exist outside the project. A fresh child
    directory is created exclusively for each run. Commands are passed directly
    to ``subprocess.Popen`` with ``shell=False`` and ``stdin=DEVNULL``.

    ``user`` optionally runs the command as a dedicated POSIX user
    (``subprocess.Popen(user=...)``): the parent must run as root, the user
    must exist, and uid 0 is refused. Any refusal raises ValueError before
    the process starts; there is no silent fallback to the parent UID.
    """
    if isinstance(command, (str, bytes)) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("command must be a non-empty sequence of non-empty strings")
    if not isinstance(approval_ref, str) or not approval_ref.strip():
        raise ValueError("a caller-validated approval_ref is required")
    if (not files or any(not isinstance(item, str) for item in files)
            or len(set(files)) != len(files)):
        raise ValueError("files must contain at least one unique project-relative path")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be greater than zero")
    runner_user, runner_uid = _resolve_runner_user(user)

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
    try:
        _write_json_file(evidence / "pre_snapshot.json", {"run_id": run_id, "captured_at": started, "files": pre, "errors": pre_errors})
        with stdout_path.open("xb") as out_stream, stderr_path.open("xb") as err_stream:
            if pre_errors:
                errors.extend(pre_errors)
                status = "NOT_VERIFIED"
            else:
                try:
                    process = subprocess.Popen(
                        list(command), cwd=work, stdin=subprocess.DEVNULL,
                        stdout=out_stream, stderr=err_stream, shell=False,
                        close_fds=True, env=None if env is None else dict(env),
                        user=runner_uid,
                    )
                    process_started = True
                except OSError as exc:
                    errors.append(f"startup failed: {type(exc).__name__}: {exc}")
                    status = "INFRASTRUCTURE_ERROR"
                if process_started:
                    deadline = None if timeout is None else time.monotonic() + timeout
                    try:
                        while process.poll() is None:
                            if interrupt_event is not None and interrupt_event.is_set():
                                interrupted = True
                                break
                            if deadline is not None and time.monotonic() >= deadline:
                                timed_out = True
                                break
                            time.sleep(poll_interval)
                    except KeyboardInterrupt:
                        interrupted = True
                    if interrupted or timed_out:
                        process.terminate()
                        try:
                            exit_code = process.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            exit_code = process.wait()
                    else:
                        exit_code = process.wait()
                    if interrupted:
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
            if (before.get("state"), before.get("sha256"), before.get("size"), before.get("ctime_ns")) != (after.get("state"), after.get("sha256"), after.get("size"), after.get("ctime_ns")):
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
                             runner_user, runner_uid)
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"evidence capture failed: {type(exc).__name__}: {exc}")
        # Do not fabricate a manifest or PASS when evidence writing is incomplete.
        return CaptureResult(run_id, "INFRASTRUCTURE_ERROR", exit_code, timed_out,
                             interrupted, str(evidence),
                             str(manifest_path) if manifest_path.exists() else None,
                             str(report_path) if report_path.exists() else None,
                             False, False, tuple(errors), runner_user, runner_uid)
