"""Fail-closed authorization, path and resource gates for read-only scans."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from typing import Any

MAX_FILES = 5_000
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_READ_BYTES = 268_435_456
MAX_SCAN_SECONDS = 600
MAX_PARSER_TASK_SECONDS = 45
MAX_CONCURRENCY = 1
MAX_OUTPUT_BYTES = 268_435_456
MAX_AUTHORIZATION_BYTES = 16_384
ALLOWED_ACTION = "static_read_only_scan"
REQUIRED_FORBIDDEN_ACTIONS = frozenset({
    "execute_project_code", "build_or_test_project", "install_dependencies",
    "network_or_service_access", "database_access", "read_credentials",
    "modify_project_files",
})


class ScanBlocked(ValueError):
    """A safety precondition failed; callers must not continue or emit a graph."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.status = "BLOCKED"


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ScanBlocked("AUTHORIZATION_INVALID", f"Authorization field {field} is required.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScanBlocked("AUTHORIZATION_INVALID", f"Authorization field {field} must be ISO-8601.") from exc
    if parsed.tzinfo is None:
        raise ScanBlocked("AUTHORIZATION_INVALID", f"Authorization field {field} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def is_reparse_point(path: Path) -> bool:
    """Detect symbolic links and Windows junction/reparse points without following them."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        info = path.stat(follow_symlinks=False)
        attrs = getattr(info, "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ScanBlocked("PATH_CHECK_FAILED", f"Could not safely inspect a path component ({type(exc).__name__}).") from exc


def reject_reparse_components(path: Path) -> None:
    """Fail closed if any existing component from the volume root is a link/junction."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    parts = list(reversed(absolute.parents)) + [absolute]
    for part in parts:
        # Do not gate on Path.exists(): it follows links and returns False for a
        # dangling symlink, which must still be rejected before resolve/open.
        if is_reparse_point(part):
            raise ScanBlocked("REPARSE_POINT_REJECTED", "Symbolic links, junctions and reparse points are not accepted.")


def canonical_project(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise ScanBlocked("PROJECT_PATH_MUST_BE_ABSOLUTE", "An explicitly authorized absolute project root is required.")
    reject_reparse_components(raw)
    try:
        root = raw.resolve(strict=True)
    except OSError as exc:
        raise ScanBlocked("PROJECT_PATH_INVALID", f"Project root cannot be resolved ({type(exc).__name__}).") from exc
    if not root.is_dir():
        raise ScanBlocked("PROJECT_ROOT_NOT_DIRECTORY", "The authorized project root must be a directory.")
    return root


def canonical_output(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise ScanBlocked("OUTPUT_PATH_MUST_BE_ABSOLUTE", "An explicitly authorized absolute output path is required.")
    reject_reparse_components(raw)
    try:
        output = raw.resolve(strict=False)
        parent = output.parent.resolve(strict=True)
    except OSError as exc:
        raise ScanBlocked("OUTPUT_PATH_INVALID", f"Output path or parent cannot be resolved ({type(exc).__name__}).") from exc
    if not parent.is_dir():
        raise ScanBlocked("OUTPUT_PARENT_INVALID", "The output parent must already exist and be a directory.")
    return output


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def overlaps(left: Path, right: Path) -> bool:
    a, b = _norm(left), _norm(right)
    try:
        return os.path.commonpath((a, b)) in (a, b)
    except ValueError:
        return False


@dataclass(frozen=True)
class ScanAuthorization:
    project_root: Path
    output_dir: Path
    allowed_actions: tuple[str, ...]
    forbidden_actions: frozenset[str]
    authorized_at: datetime
    expires_at: datetime

    @classmethod
    def from_mapping(cls, value: Any) -> "ScanAuthorization":
        if not isinstance(value, dict):
            raise ScanBlocked("AUTHORIZATION_MISSING", "A structured authorization record is required.")
        required = {"project_root", "output_dir", "allowed_actions", "forbidden_actions", "authorized_at", "expires_at"}
        if not required.issubset(value):
            raise ScanBlocked("AUTHORIZATION_INVALID", "Authorization is missing required project/action/time/output fields.")
        if not isinstance(value["allowed_actions"], list) or not isinstance(value["forbidden_actions"], list):
            raise ScanBlocked("AUTHORIZATION_INVALID", "Allowed and forbidden actions must be explicit lists.")
        if ALLOWED_ACTION not in value["allowed_actions"]:
            raise ScanBlocked("ACTION_NOT_AUTHORIZED", "The authorization does not allow static read-only scanning.")
        forbidden = frozenset(str(x) for x in value["forbidden_actions"])
        if not REQUIRED_FORBIDDEN_ACTIONS.issubset(forbidden):
            raise ScanBlocked("FORBIDDEN_ACTIONS_INCOMPLETE", "The authorization must explicitly forbid execution, builds/tests, installs, external access, credentials and project writes.")
        return cls(
            project_root=Path(value["project_root"]).expanduser(),
            output_dir=Path(value["output_dir"]).expanduser(),
            allowed_actions=tuple(str(x) for x in value["allowed_actions"]),
            forbidden_actions=forbidden,
            authorized_at=_aware_datetime(value["authorized_at"], "authorized_at"),
            expires_at=_aware_datetime(value["expires_at"], "expires_at"),
        )

    def validate(self, project: Path, output: Path, *, now: datetime | None = None) -> None:
        expected_project = canonical_project(project)
        expected_output = canonical_output(output)
        recorded_project = canonical_project(self.project_root)
        recorded_output = canonical_output(self.output_dir)
        if _norm(recorded_project) != _norm(expected_project):
            raise ScanBlocked("PROJECT_SCOPE_MISMATCH", "The requested project root differs from the authorized root.")
        if _norm(recorded_output) != _norm(expected_output):
            raise ScanBlocked("OUTPUT_SCOPE_MISMATCH", "The requested output path differs from the authorized output path.")
        if overlaps(expected_project, expected_output):
            raise ScanBlocked("OUTPUT_OVERLAPS_PROJECT", "The output path must not overlap the project tree.")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if self.authorized_at > current or self.expires_at <= current or self.expires_at <= self.authorized_at:
            raise ScanBlocked("AUTHORIZATION_EXPIRED", "Authorization is not currently valid.")


def load_authorization_file(path: str | Path, project: str | Path, output: str | Path) -> ScanAuthorization:
    auth_path = Path(path).expanduser()
    if not auth_path.is_absolute():
        raise ScanBlocked("AUTHORIZATION_FILE_MUST_BE_ABSOLUTE", "Authorization file path must be absolute.")
    reject_reparse_components(auth_path)
    try:
        resolved = auth_path.resolve(strict=True)
        if overlaps(resolved, canonical_project(project)):
            raise ScanBlocked("AUTHORIZATION_FILE_IN_PROJECT", "Authorization records must be stored outside the scanned project.")
        if resolved.stat().st_size > MAX_AUTHORIZATION_BYTES:
            raise ScanBlocked("AUTHORIZATION_FILE_TOO_LARGE", "Authorization record exceeds the 16 KiB limit.")
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except ScanBlocked:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScanBlocked("AUTHORIZATION_FILE_INVALID", f"Authorization file could not be safely read ({type(exc).__name__}).") from exc
    authorization = ScanAuthorization.from_mapping(data)
    authorization.validate(canonical_project(project), canonical_output(output))
    return authorization


def authorization_record(project: Path, output: Path, *, now: datetime | None = None, lifetime_seconds: int = 3600) -> dict[str, Any]:
    """Construct an explicit authorization record for generated USIT-only fixtures."""
    current = now or datetime.now(timezone.utc)
    current = current.astimezone(timezone.utc)
    return {
        "project_root": str(project), "output_dir": str(output),
        "allowed_actions": [ALLOWED_ACTION, "overwrite_existing_output"], "authorized_at": current.isoformat(),
        "expires_at": datetime.fromtimestamp(current.timestamp() + lifetime_seconds, timezone.utc).isoformat(),
        "forbidden_actions": sorted(REQUIRED_FORBIDDEN_ACTIONS),
    }
