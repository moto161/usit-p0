"""Controlled pytest execution against an immutable Git-archive staging copy.

The runner refuses dirty/untracked Git worktrees, executes only the committed HEAD
snapshot in a separate staging tree, records pre/post hashes, and keeps runner
authenticity explicitly UNAUTHENTICATED. It is not an OS sandbox: project tests
execute code and must be explicitly authorized by an external authorization file.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from xml.etree import ElementTree as ET

from .evidence_signing import (
    generate_run_key, key_file_name, sign_document, verify_document_signature,
    write_key_file,
)
from .safeio import read_bounded_no_follow
from .test_execution_provenance import (
    MAX_REPORT_BYTES, SCHEMA_VERSION, canonical_json_bytes, sha256_hex,
    validate_execution_provenance,
)
from .verification_capture import _resolve_runner_user, capture_run, verify_manifest

AUTH_SCHEMA = "usit-test-execution-authorization-v1"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FILES = 50_000
MAX_RUNTIME_IMPORTS_BYTES = 50 * 1024 * 1024
MAX_EXECUTION_ARTIFACT_BYTES = 100 * 1024 * 1024


def _read_execution_artifact(path: str | os.PathLike[str], limit: int, label: str) -> bytes:
    """Read a runner-produced artifact with a hard cap and final-component no-follow."""
    try:
        return read_bounded_no_follow(path, limit)
    except (OSError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        raise ValueError(f"{label} is not a bounded regular file: {detail}") from exc


# pytest terminal summary line: "=== 3 passed, 1 failed in 0.42s ===" (normal;
# pytest pads the '=' run to terminal width, so match one-or-more '=')
# or "3 passed, 1 failed in 0.42s" (quiet, -q). Only the final summary line
# is used. Counts that pytest reports separately from JUnit (xfailed/xpassed
# are folded into JUnit "skipped"/"passed") are normalised before comparing.
_SUMMARY_RE = re.compile(
    r"(?:^|\n)=+\s+(?P<body>.+?)\s+in\s+[\d.]+s\s+=+"
    r"|(?:^|\n)(?P<quiet>(?:\d+\s+\w+(?:,\s*)?)+)\s+in\s+[\d.]+s\s*$",
    re.MULTILINE,
)
_SUMMARY_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|errors?|xfailed|xpassed|deselected)")
_COUNT_KEYS = ("passed", "failed", "skipped", "errors", "xfailed", "xpassed", "deselected")


def _pytest_terminal_summary_counts(stdout_bytes: bytes) -> dict[str, int] | None:
    """Parse the last pytest terminal summary line, or None when absent.

    Returns a normalised count dict. A missing summary is not an error: some
    invocations (collection errors, crashes, -q edge cases) print none, and
    the exit code already covers those paths.
    """
    try:
        text = stdout_bytes.decode("utf-8", "replace")
    except Exception:
        return None
    matches = list(_SUMMARY_RE.finditer(text))
    if not matches:
        return None
    body = matches[-1].group("body") or matches[-1].group("quiet") or ""
    counts: dict[str, int] = {key: 0 for key in _COUNT_KEYS}
    for number, label in _SUMMARY_COUNT_RE.findall(body):
        key = "errors" if label in {"error", "errors"} else label
        counts[key] += int(number)
    # pytest folds xfailed into JUnit "skipped" and xpassed into JUnit
    # "passed", so normalise to JUnit's view for comparison.
    counts["skipped"] += counts["xfailed"]
    counts["passed"] += counts["xpassed"]
    return counts


def _junit_counts(root: ET.Element) -> dict[str, int] | None:
    """Sum tests/failures/errors/skipped across JUnit testsuite elements."""
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None
    total = failed = errors = skipped = 0
    try:
        for suite in suites:
            total += int(suite.attrib.get("tests", "0"))
            failed += int(suite.attrib.get("failures", "0"))
            errors += int(suite.attrib.get("errors", "0"))
            skipped += int(suite.attrib.get("skipped", "0"))
    except ValueError:
        return None
    return {
        "tests": total,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "passed": total - failed - errors - skipped,
    }


MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024

_RUNNER_PLUGIN = """\\
from pathlib import Path
import hashlib
import json
import os
import sys
import pytest

# Two complementary import trails, both recorded as imports happen:
# - _AUDIT_HOOK_NAMES: module names from sys.addaudithook("import"). Audit
#   hooks are append-only (test code cannot unregister one), so a name
#   recorded here survives a later `del sys.modules[name]`. NOTE: the import
#   event fires BEFORE the finder resolves the file, so its filename is
#   always None on CPython 3.12 -- the hook is a name tripwire only, and the
#   resolved path comes from the finder below.
# - _FINDER_IMPORTS: module name -> resolved file, recorded by the meta path
#   finder at resolution time. A finder CAN be removed from sys.meta_path,
#   so if it is gone the audit-hook names are resolved again at
#   sessionfinish instead (see pytest_sessionfinish).
_AUDIT_HOOK_NAMES = []
_FINDER_IMPORTS = {}
_AUDIT_HOOK_ACTIVE = False

def _audit_import_hook(event, args):
    # The hook must never raise -- an exception would abort the import it
    # observes.
    if event != "import":
        return
    try:
        name = args[0]
    except (IndexError, TypeError):
        return
    if not isinstance(name, str):
        return
    try:
        _AUDIT_HOOK_NAMES.append(name)
    except Exception:
        return

try:
    sys.addaudithook(_audit_import_hook)
    _AUDIT_HOOK_ACTIVE = True
except Exception:
    _AUDIT_HOOK_ACTIVE = False


class _UsitImportRecorder:
    # A meta path finder that records the resolved file of every import as
    # it happens. It delegates to PathFinder and returns its spec unchanged,
    # so import behaviour is identical; it only observes. Any failure falls
    # through to the next finder (return None) so recording can never break
    # an import; the audit hook still has the module name in that case.
    def find_spec(self, fullname, path, target=None):
        try:
            import importlib.machinery
            spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        except Exception:
            return None
        try:
            if spec is not None:
                origin = getattr(spec, "origin", None)
                if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
                    _FINDER_IMPORTS.setdefault(fullname, origin)
        except Exception:
            pass
        return spec

try:
    sys.meta_path.insert(0, _UsitImportRecorder())
except Exception:
    pass

@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    if call.when == "call":
        function = getattr(item, "function", None)
        code = getattr(function, "__code__", None)
        if code is not None:
            try:
                root = Path(str(item.config.rootpath)).resolve()
                source = Path(code.co_filename).resolve()
                relative = source.relative_to(root).as_posix()
            except (OSError, ValueError):
                relative = None
            if relative and not relative.startswith("../") and relative != "..":
                properties = [
                    ("usit_source_file", relative),
                    ("usit_source_line", str(code.co_firstlineno)),
                    ("usit_source_symbol", getattr(function, "__qualname__", getattr(function, "__name__", ""))),
                    ("usit_nodeid", str(item.nodeid)),
                ]
                item.user_properties = list(getattr(item, "user_properties", [])) + properties
    outcome = yield
    report = outcome.get_result()
    if call.when == "call":
        report.user_properties = list(getattr(item, "user_properties", []))

def _classify_runtime_module(name, origin, root, roots, original_root):
    # Classify one imported module against the staged snapshot. Returns an
    # entry dict, or None when the module is not a project module (stdlib /
    # third-party / built-in) and carries no project evidence. A module whose
    # file cannot be read is reported SOURCE_UNREADABLE, never silently
    # dropped: an import that cannot be verified must fail binding.
    if not isinstance(origin, str) or origin in {"built-in", "frozen"}:
        return None
    try:
        source = Path(origin).resolve(strict=True)
        if not source.is_file():
            return None
        declared_project_module = name.split(".", 1)[0] in roots
        loaded_from_original_project = False
        if original_root is not None:
            try:
                source.relative_to(original_root)
                loaded_from_original_project = True
            except ValueError:
                pass
        if not declared_project_module and not loaded_from_original_project:
            return None
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        try:
            relative = source.relative_to(root).as_posix()
            status = "STAGED_SOURCE"
        except ValueError:
            relative = None
            status = "OUTSIDE_SNAPSHOT"
        return {
            "module": name, "status": status, "path": relative,
            "origin_basename": source.name, "sha256": digest, "size": len(raw),
        }
    except (OSError, RuntimeError):
        return {
            "module": name, "status": "SOURCE_UNREADABLE", "path": None,
            "origin_basename": Path(origin).name, "sha256": None, "size": None,
        }


def pytest_sessionfinish(session, exitstatus):
    root = Path(str(session.config.rootpath)).resolve()
    roots = set(json.loads(os.environ.get("USIT_SOURCE_MODULE_ROOTS", "[]")))
    original_root_raw = os.environ.get("USIT_ORIGINAL_PROJECT_ROOT", "")
    try:
        original_root = Path(original_root_raw).resolve(strict=True) if original_root_raw else None
    except OSError:
        original_root = None
    entries = []
    for name, module in sorted(tuple(sys.modules.items())):
        if not isinstance(name, str) or name == "_usit_runner_plugin":
            continue
        spec = getattr(module, "__spec__", None)
        origin = getattr(module, "__file__", None) or getattr(spec, "origin", None)
        entry = _classify_runtime_module(name, origin, root, roots, original_root)
        if entry is not None:
            entries.append(entry)
    # Audit-trail merge: the session-end sys.modules snapshot misses modules
    # imported and then removed (del sys.modules[name]). Two trails cover
    # that: the meta path finder recorded the resolved file at import time,
    # and the audit hook recorded every import's module name. Modules
    # already in the snapshot keep their snapshot entry; trail-only modules
    # are classified the same way, so a smuggled import deleted mid-run is
    # still reported OUTSIDE_SNAPSHOT instead of vanishing.
    seen_modules = {item["module"] for item in entries}
    audit_only = 0
    for audited_name, audited_origin in sorted(_FINDER_IMPORTS.items()):
        if audited_name in seen_modules or audited_name == "_usit_runner_plugin":
            continue
        seen_modules.add(audited_name)
        entry = _classify_runtime_module(audited_name, audited_origin, root, roots, original_root)
        if entry is not None:
            entries.append(entry)
            audit_only += 1
    # Fallback for names the audit hook saw but the finder did not record
    # (e.g. the finder was removed from sys.meta_path mid-run): resolve the
    # top-level name now. A project-root name that cannot be resolved to a
    # file is reported SOURCE_UNREADABLE -- an import the hook observed but
    # that cannot be verified must fail binding, not vanish. Only top-level
    # names are resolved here: resolving a submodule could execute its
    # parent package, and the parent name is in the hook log as well.
    for audited_name in _AUDIT_HOOK_NAMES:
        if audited_name in seen_modules or audited_name == "_usit_runner_plugin":
            continue
        if "." in audited_name or audited_name.split(".", 1)[0] not in roots:
            continue
        seen_modules.add(audited_name)
        origin = None
        try:
            import importlib.util
            spec = importlib.util.find_spec(audited_name)
            if spec is not None:
                origin = getattr(spec, "origin", None)
        except Exception:
            origin = None
        if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
            entry = _classify_runtime_module(audited_name, origin, root, roots, original_root)
        else:
            entry = {
                "module": audited_name, "status": "SOURCE_UNREADABLE", "path": None,
                "origin_basename": None, "sha256": None, "size": None,
            }
        if entry is not None:
            entries.append(entry)
            audit_only += 1
    entries.sort(key=lambda item: item["module"])
    staged = sum(item["status"] == "STAGED_SOURCE" for item in entries)
    outside = sum(item["status"] == "OUTSIDE_SNAPSHOT" for item in entries)
    unreadable = sum(item["status"] == "SOURCE_UNREADABLE" for item in entries)
    status = "RUNTIME_IMPORTS_MATCHED_STAGED_SNAPSHOT" if entries and not outside and not unreadable else "RUNTIME_IMPORTS_NOT_BOUND"
    record = {
        "schema_version": "usit-runtime-imports-v1", "status": status,
        "project_module_roots": sorted(roots), "module_count": len(entries),
        "staged_module_count": staged, "outside_snapshot_module_count": outside,
        "unreadable_module_count": unreadable, "modules": entries,
        "audit_hook": "ACTIVE" if _AUDIT_HOOK_ACTIVE else "NOT_REGISTERED",
        "audit_hook_import_event_count": len(_AUDIT_HOOK_NAMES),
        "audit_hook_only_module_count": audit_only,
        "limitations": [
            "The import audit event fires before the finder resolves the file, so it carries the module name only; the resolved path is recorded by the meta path finder. If test code removes that finder from sys.meta_path, trail-only names are resolved again at sessionfinish against the then-current sys.path and file bytes.",
            "Code loaded without the import system (ctypes/CDLL, exec() of a string that imports nothing, importlib loader APIs used without registering in sys.modules) raises no import event and remains invisible.",
            "The hook is registered when the runner plugin loads; imports during pytest startup before that point are visible only through the session-end sys.modules snapshot.",
            "The hook itself cannot be unregistered by test code, but the in-memory audit log lives in the same process as the tests. UID isolation (POSIX_USER) keeps the test process out of the parent process memory and the evidence files, but the log itself is still written by the test process, so only a parent-held output channel would authenticate it.",
        ],
    }
    Path(os.environ["USIT_RUNTIME_IMPORTS_PATH"]).write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\\n",
        encoding="utf-8",
    )
"""

_ALLOWED_PYTEST_OPTIONS = {"-q", "-v", "-vv", "-x", "--maxfail=1", "--maxfail=2",
                           "--maxfail=3", "--tb=short", "--tb=long", "--tb=line",
                           "--tb=native", "--tb=no", "--disable-warnings"}
_BLOCKED_PREFIXES = ("--junitxml", "--rootdir", "--confcutdir", "--override-ini",
                     "--import-mode", "--basetemp", "--pyargs", "-c", "--config-file")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_dir(value: str | os.PathLike[str], label: str) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    current = Path(raw.anchor)
    for part in raw.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"{label} contains a symlink/reparse component")
    resolved = raw.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"{label} is not a directory")
    return resolved


def _load_authorization(path: str | os.PathLike[str], project: Path, evidence_parent: Path,
                        runner_user: str | None = None) -> dict[str, Any]:
    auth_path = Path(path).expanduser()
    if not auth_path.is_absolute() or auth_path.is_symlink():
        raise ValueError("authorization file must be an absolute regular file outside the project")
    resolved = auth_path.resolve(strict=True)
    if resolved.is_relative_to(project):
        raise ValueError("authorization file must be outside the project")
    value = json.loads(read_bounded_no_follow(auth_path, 16 * 1024).decode("utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != AUTH_SCHEMA:
        raise ValueError("unsupported test execution authorization schema")
    if Path(value.get("project_root", "")).resolve(strict=True) != project:
        raise ValueError("authorization project_root does not match requested project")
    if Path(value.get("evidence_parent", "")).resolve(strict=True) != evidence_parent:
        raise ValueError("authorization evidence_parent does not match requested evidence directory")
    actions = value.get("allowed_actions")
    if not isinstance(actions, list) or not all(isinstance(item, str) for item in actions):
        raise ValueError("authorization allowed_actions must be an explicit string list")
    if "run_pytest_on_git_snapshot" not in actions:
        raise ValueError("authorization does not allow run_pytest_on_git_snapshot")
    if value.get("acknowledges_project_code_execution") is not True:
        raise ValueError("authorization must explicitly acknowledge execution of project code")
    approval_ref = value.get("approval_ref")
    if not isinstance(approval_ref, str) or not approval_ref.strip() or len(approval_ref) > 256:
        raise ValueError("authorization approval_ref is required")
    # UID isolation is a permission decision, so it must be bound by the
    # authorization file exactly like project_root / evidence_parent: when
    # either side names a runner user, both must agree. A caller may not
    # unilaterally request isolation, and a run may not silently drop
    # isolation the authorization demands.
    auth_runner_user = value.get("runner_user")
    if auth_runner_user is not None and (not isinstance(auth_runner_user, str) or not auth_runner_user):
        raise ValueError("authorization runner_user must be a non-empty string")
    if runner_user != auth_runner_user:
        raise ValueError("authorization runner_user does not match requested runner_user")
    def parse_time(field: str) -> datetime:
        raw = value.get(field)
        if not isinstance(raw, str) or not raw.endswith("Z"):
            raise ValueError(f"authorization {field} must be UTC ISO-8601 ending in Z")
        return datetime.fromisoformat(raw[:-1] + "+00:00")
    now = datetime.now(timezone.utc)
    authorized_at = parse_time("authorized_at")
    expires_at = parse_time("expires_at")
    if authorized_at > now or expires_at <= now or expires_at <= authorized_at:
        raise ValueError("test execution authorization is not currently valid")
    return value


def _git(project: Path, *args: str, limit: int = MAX_ARCHIVE_BYTES) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(project), *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace")[-1000:]
        raise ValueError(f"git {' '.join(args[:2])} failed ({result.returncode}): {message}")
    if len(result.stdout) > limit:
        raise ValueError("Git output exceeds configured size limit")
    return result.stdout


def _safe_archive_path(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Git archive contains an invalid path")
    pure = PurePosixPath(name)
    if pure.is_absolute() or ":" in pure.parts[0] or any(part in ("", ".", "..") for part in pure.parts) or pure.as_posix() != name:
        raise ValueError(f"Git archive contains unsafe path: {name!r}")
    return name


def _extract_archive(archive_bytes: bytes, destination: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) > MAX_FILES * 2:
            raise ValueError("Git archive contains too many entries")
        seen: set[str] = set()
        for member in members:
            name = _safe_archive_path(member.name.rstrip("/"))
            if name in seen:
                raise ValueError(f"Git archive contains duplicate path: {name}")
            seen.add(name)
            target = destination.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"Git archive contains a link or special file: {name}")
            if member.size < 0 or member.size > MAX_FILE_BYTES:
                raise ValueError(f"Git archive file exceeds size limit: {name}")
            total += member.size
            if total > MAX_TOTAL_BYTES:
                raise ValueError("Git archive exceeds total extracted size limit")
            if len(files) >= MAX_FILES:
                raise ValueError("Git archive contains too many regular files")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"Git archive file cannot be read: {name}")
            digest = hashlib.sha256()
            size = 0
            with target.open("xb") as output:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    size += len(block)
            if size != member.size:
                raise ValueError(f"Git archive file size changed during extraction: {name}")
            files.append({"path": name, "size": size, "sha256": digest.hexdigest()})
    if not files:
        raise ValueError("Git archive contains no regular files")
    return sorted(files, key=lambda item: item["path"])


def _validate_pytest_args(args: Sequence[str]) -> list[str]:
    if isinstance(args, (str, bytes)) or not all(isinstance(x, str) and x for x in args):
        raise ValueError("pytest arguments must be non-empty strings")
    for arg in args:
        if arg in _ALLOWED_PYTEST_OPTIONS:
            continue
        if arg.startswith(_BLOCKED_PREFIXES):
            raise ValueError(f"pytest option is reserved by USIT: {arg}")
        if arg.startswith("-k=") or arg.startswith("-m="):
            if len(arg) > 3:
                continue
        if arg.startswith("-"):
            raise ValueError(f"pytest option is not in the controlled allow-list: {arg}")
        _safe_archive_path(arg.split("::", 1)[0])
    return list(args)


def _staging_inventory_errors(staging: Path, source_files: Sequence[dict[str, Any]]) -> list[str]:
    """Reject files added outside the runner-owned report/temp locations."""
    expected = {item["path"] for item in source_files}
    expected_directories: set[str] = set()
    for relative in expected:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() not in {".", ""}:
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    errors: list[str] = []
    for current, directories, filenames in os.walk(staging, topdown=True, followlinks=False):
        base = Path(current)
        for name in list(directories):
            candidate = base / name
            relative_dir = candidate.relative_to(staging).as_posix()
            info = candidate.lstat()
            if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                errors.append(f"reparse directory created during test: {relative_dir}")
                directories.remove(name)
            elif (relative_dir not in expected_directories
                  and relative_dir not in {".usit-home", ".usit-tmp"}
                  and not relative_dir.startswith((".usit-home/", ".usit-tmp/"))):
                errors.append(f"unexpected directory created during test: {relative_dir}")
        for name in filenames:
            candidate = base / name
            relative = candidate.relative_to(staging).as_posix()
            info = candidate.lstat()
            if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400 or not candidate.is_file():
                errors.append(f"unsafe non-regular file created during test: {relative}")
                continue
            if relative in expected or relative in {".usit-junit.xml", "_usit_runner_plugin.py", ".usit-runtime-imports.json"}:
                continue
            if relative.startswith(".usit-home/") or relative.startswith(".usit-tmp/"):
                continue
            errors.append(f"unexpected file created during test: {relative}")
    return errors


def _artifact_record(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    raw = _read_execution_artifact(path, MAX_EXECUTION_ARTIFACT_BYTES, f"artifact {name}")
    return {"path": name, "size": len(raw), "sha256": sha256_hex(raw)}


def _chown_tree(root: Path, uid: int) -> None:
    """Recursively transfer a staging tree to the runner UID (A1: ownership only).

    Modes are left unchanged (the staging top stays 0700, now owned by the
    runner user). Any failure is fail-closed: the caller must refuse the run
    rather than execute it with the wrong owner.
    """
    try:
        for current, directories, filenames in os.walk(root, topdown=False, followlinks=False):
            for name in filenames:
                os.chown(Path(current) / name, uid, -1, follow_symlinks=False)
            for name in directories:
                os.chown(Path(current) / name, uid, -1, follow_symlinks=False)
        os.chown(root, uid, -1, follow_symlinks=False)
    except OSError as exc:
        raise ValueError(f"staging ownership transfer failed: {type(exc).__name__}: {exc}") from exc


# Staging paths the test process legitimately writes. Everything else in
# staging becomes read-only under A2 (see _make_staging_read_only).
_WRITABLE_STAGING_FILES = frozenset({".usit-junit.xml", ".usit-runtime-imports.json"})
_WRITABLE_STAGING_DIRS = frozenset({".usit-home", ".usit-tmp"})


def _make_staging_read_only(root: Path) -> None:
    """Make a staging tree read-only for the runner user (A2).

    Only called for isolated runs (the caller checks runner_uid). Sources
    become 0444 and directories 0555, including the staging top itself, so
    the test process can neither modify existing sources nor create new
    files in the tree. Files that were executable in the archive keep
    their execute bit (0555) so scripts in the tree still run. The four
    output locations the test process legitimately writes stay writable
    (see _WRITABLE_STAGING_FILES / _WRITABLE_STAGING_DIRS). Symlinks are
    skipped: chmod follows links on Linux, so touching one could change a
    file outside staging. Any failure is fail-closed: the caller must
    refuse the run rather than execute it with a writable tree.
    """
    try:
        for current, directories, filenames in os.walk(root, topdown=False, followlinks=False):
            current_path = Path(current)
            for name in filenames:
                path = current_path / name
                if path.is_symlink():
                    continue
                if current_path == root and name in _WRITABLE_STAGING_FILES:
                    os.chmod(path, 0o644)
                    continue
                mode = 0o555 if stat.S_IMODE(path.stat().st_mode) & 0o111 else 0o444
                os.chmod(path, mode)
            for name in directories:
                path = current_path / name
                if path.is_symlink():
                    continue
                if current_path == root and name in _WRITABLE_STAGING_DIRS:
                    os.chmod(path, 0o755)
                    continue
                os.chmod(path, 0o555)
        os.chmod(root, 0o555)
    except OSError as exc:
        raise ValueError(f"staging read-only hardening failed: {type(exc).__name__}: {exc}") from exc


def _minimal_environment(staging: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "LANG", "LC_ALL", "TZ"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    home = staging / ".usit-home"
    temp = staging / ".usit-tmp"
    home.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "TMP": str(temp), "TEMP": str(temp),
        "TMPDIR": str(temp), "PYTHONPATH": str(staging), "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    return env


def run_controlled_pytest(
    project_root: str | os.PathLike[str],
    *,
    evidence_parent: str | os.PathLike[str],
    authorization_file: str | os.PathLike[str],
    pytest_args: Sequence[str] = (),
    timeout: float = 1800,
    runner_user: str | None = None,
) -> dict[str, Any]:
    """Execute pytest against a clean, staged Git HEAD and produce bound evidence.

    This is an execution boundary, not an OS sandbox. The authorization must
    explicitly acknowledge arbitrary project-code execution. Runner authenticity
    remains UNAUTHENTICATED until a platform-backed attestation is implemented.

    ``runner_user`` optionally runs the test process as a dedicated POSIX user
    (UID isolation A, Linux only): the parent must run as root, the user must
    already exist, and the authorization file must name the same user. Any
    refusal raises ValueError before execution; there is no silent fallback
    to running as the parent user.
    """
    project = _canonical_dir(project_root, "project root")
    parent = _canonical_dir(evidence_parent, "evidence parent")
    if parent == project or project.is_relative_to(parent) or parent.is_relative_to(project):
        raise ValueError("evidence_parent and project_root must not overlap")
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 24 * 60 * 60:
        raise ValueError("timeout must be finite, greater than zero and no more than 24 hours")
    if runner_user is not None and (not isinstance(runner_user, str) or not runner_user):
        raise ValueError("runner_user must be a non-empty username string")
    authorization = _load_authorization(authorization_file, project, parent, runner_user=runner_user)
    # Resolve early (before any Git work or staging) so a refused isolation
    # request fails fast and never reaches execution.
    runner_user_name, runner_uid = _resolve_runner_user(runner_user)
    runner_isolation = f"POSIX_USER:{runner_user_name}" if runner_user_name is not None else "NONE"
    # A2: the staged source tree is read-only exactly when the run is
    # isolated (see _make_staging_read_only); non-isolated runs keep a
    # writable staging tree.
    staging_source_mode = "READ_ONLY" if runner_user_name is not None else "WRITABLE"
    if runner_isolation == "NONE":
        isolation_limitation = (
            "No UID isolation was requested, so the test process ran as the parent user: "
            "it could read any parent-owned file the OS permits, and the HMAC key file was "
            "protected only by its 0600 mode and by being written after the test process exited."
        )
    else:
        isolation_limitation = (
            f"The test process ran as dedicated POSIX user {runner_user_name}: it cannot write "
            "the evidence directory, the HMAC key file, the original project tree, or the "
            "staged source tree (staging is read-only except for the JUnit report, the "
            "runtime-imports manifest, and its HOME/TMPDIR inside staging). It can still "
            "read world-readable files, reach the network, and consume host resources, so "
            "this is not a sandbox and provenance_authenticity_status stays UNAUTHENTICATED."
        )
    selected_args = _validate_pytest_args(pytest_args)
    status = _git(project, "status", "--porcelain=v1", "--untracked-files=all", limit=4 * 1024 * 1024)
    if status.strip():
        raise ValueError("project Git worktree is not clean; commit or separately review all tracked/untracked changes first")
    index_entries = _git(project, "ls-files", "--stage", "-z", limit=8 * 1024 * 1024)
    if any(entry.startswith(b"160000 ") for entry in index_entries.split(b"\\0") if entry):
        raise ValueError("Git submodules are not supported by the staged snapshot runner")
    head = _git(project, "rev-parse", "HEAD", limit=4096).decode("ascii").strip()
    archive = _git(project, "archive", "--format=tar", "HEAD")
    # Staging lives in the system temp root, NOT inside evidence_parent: the
    # test process runs with cwd=staging, so a staging dir sibling to the
    # evidence dir would let it enumerate ".." and reach run_* evidence.
    # (Depth-in-depth only: /proc can still reveal the parent, so this does
    # not replace UID isolation; it just closes the trivial enumeration path.)
    staging = Path(tempfile.mkdtemp(prefix=".usit-stage-")).resolve()
    try:
        source_files = _extract_archive(archive, staging)
        source_manifest = {"schema_version": "usit-manifest-v1", "git_commit": head, "files": source_files}
        source_manifest_bytes = canonical_json_bytes(source_manifest)
        manifest_hash = sha256_hex(source_manifest_bytes)
        pytest_report = staging / ".usit-junit.xml"
        plugin_path = staging / "_usit_runner_plugin.py"
        plugin_path.write_text(_RUNNER_PLUGIN, encoding="utf-8")
        plugin_digest = sha256_hex(plugin_path.read_bytes())
        command = [
            sys.executable, "-m", "pytest", "-p", "_usit_runner_plugin", "--junitxml", str(pytest_report),
            "--assert=plain", "-p", "no:cacheprovider", *selected_args,
        ]
        env = _minimal_environment(staging)
        # A2: pre-create the two output files the test process writes, so
        # they exist (and are owned by the runner user after the chown
        # below) before the tree is made read-only. Both are opened with
        # "w" by pytest / the runner plugin, so an empty pre-created file
        # is truncated and rewritten; if the run crashes before that, the
        # parent reads an empty file and records an error, never a pass.
        (staging / ".usit-junit.xml").write_text("", encoding="utf-8")
        (staging / ".usit-runtime-imports.json").write_text("", encoding="utf-8")
        module_roots = set()
        for source_item in source_files:
            source_path = PurePosixPath(source_item["path"])
            if len(source_path.parts) > 1:
                module_roots.add(source_path.parts[0])
            if source_path.suffix == ".py" and source_path.stem != "__init__":
                module_roots.add(source_path.stem)
        env["USIT_SOURCE_MODULE_ROOTS"] = json.dumps(sorted(module_roots))
        env["USIT_ORIGINAL_PROJECT_ROOT"] = str(project)
        env["USIT_RUNTIME_IMPORTS_PATH"] = str(staging / ".usit-runtime-imports.json")
        normalized_command = [
            sys.executable, "-m", "pytest", "-p", "_usit_runner_plugin",
            "--junitxml", "<STAGING>/.usit-junit.xml",
            "--assert=plain", "-p", "no:cacheprovider", *selected_args,
        ]
        command_fingerprint = sha256_hex(canonical_json_bytes(normalized_command))
        normalized_env = {
            key: value.replace(str(staging), "<STAGING>")
            for key, value in env.items()
        }
        environment_fingerprint = sha256_hex(canonical_json_bytes({
            "python": sys.version, "platform": platform.platform(),
            "environment_sha256": sha256_hex(canonical_json_bytes(normalized_env)),
            "environment_keys": sorted(env),
        }))
        runner_version = "pytest"
        try:
            import importlib.metadata
            runner_version = importlib.metadata.version("pytest")
        except importlib.metadata.PackageNotFoundError:
            runner_version = "NOT_INSTALLED"
        # One random HMAC key for this run, held in parent memory only. It
        # is never passed to the test process (not in env, not on disk) and
        # is written to a 0600 key file outside the evidence directory only
        # after the test process has exited, so the test process cannot read
        # it and forge signatures during the run.
        run_key = generate_run_key()
        # A1: hand the whole staging tree to the runner user (top stays
        # 0700, now owned by the runner user) before the test process
        # starts, so it can write its reports inside staging but nothing
        # outside it. Any chown failure refuses the run (fail-closed).
        # A2: then make the staged source tree read-only for the runner
        # user (sources 0444, directories 0555, top 0555), keeping only
        # the pre-created output files and HOME/TMPDIR writable. Any
        # chmod failure refuses the run (fail-closed).
        if runner_uid is not None:
            _chown_tree(staging, runner_uid)
            _make_staging_read_only(staging)
        capture = capture_run(
            command, project_root=staging, evidence_parent=parent,
            files=[item["path"] for item in source_files],
            approval_ref=authorization["approval_ref"], cwd=staging, env=env, timeout=timeout,
            user=runner_user,
        )
        evidence = Path(capture.evidence_dir)
        inventory_errors = _staging_inventory_errors(staging, source_files)
        try:
            pre_snapshot = json.loads(_read_execution_artifact(
                evidence / "pre_snapshot.json", MAX_EXECUTION_ARTIFACT_BYTES, "pre-execution snapshot"
            ).decode("utf-8"))
            pre_entries = {item.get("path"): item for item in pre_snapshot.get("files", [])}
            for item in source_files:
                observed = pre_entries.get(item["path"])
                if (not isinstance(observed, dict) or observed.get("state") != "PRESENT"
                        or observed.get("sha256") != item["sha256"]
                        or observed.get("size") != item["size"]):
                    inventory_errors.append(f"pre-execution snapshot does not match Git archive: {item['path']}")
        except (OSError, ValueError, TypeError) as exc:
            inventory_errors.append(f"pre-execution snapshot cannot be verified: {type(exc).__name__}")
        report_source = pytest_report
        if not report_source.is_file():
            return {
                "status": "NOT_VERIFIED", "capture": capture.to_dict(),
                "evidence_dir": str(evidence), "reason": "pytest did not produce a JUnit report",
                "provenance_authenticity_status": "UNAUTHENTICATED",
            }
        report_bytes = _read_execution_artifact(report_source, MAX_REPORT_BYTES, "JUnit report")
        runtime_path = staging / ".usit-runtime-imports.json"
        runtime_imports = None
        runtime_imports_bytes = b""
        runtime_imports_errors: list[str] = []
        try:
            runtime_imports_bytes = _read_execution_artifact(
                runtime_path, MAX_RUNTIME_IMPORTS_BYTES, "runtime imports manifest"
            )
            runtime_imports = json.loads(runtime_imports_bytes.decode("utf-8"))
            if not isinstance(runtime_imports, dict) or runtime_imports.get("schema_version") != "usit-runtime-imports-v1":
                raise ValueError("unsupported runtime imports schema")
            runtime_modules = runtime_imports.get("modules")
            if not isinstance(runtime_modules, list):
                raise ValueError("runtime imports modules must be a list")
            source_hashes = {item["path"]: item["sha256"] for item in source_files}
            for module in runtime_modules:
                if not isinstance(module, dict):
                    runtime_imports_errors.append("invalid runtime module entry")
                    continue
                if module.get("status") == "STAGED_SOURCE":
                    relative = module.get("path")
                    if not isinstance(relative, str) or source_hashes.get(relative) != module.get("sha256"):
                        runtime_imports_errors.append(f"runtime module does not match source snapshot: {module.get('module')}")
                elif module.get("status") in {"OUTSIDE_SNAPSHOT", "SOURCE_UNREADABLE"}:
                    runtime_imports_errors.append(f"runtime module source is not bound to snapshot: {module.get('module')}")
            if runtime_imports.get("status") != "RUNTIME_IMPORTS_MATCHED_STAGED_SNAPSHOT":
                runtime_imports_errors.append("runtime imports are not fully bound to staged snapshot")
            if not runtime_modules:
                runtime_imports_errors.append("no project runtime modules were observed")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            runtime_imports_errors.append(f"runtime imports manifest unavailable or invalid: {type(exc).__name__}")
        try:
            root = ET.fromstring(report_bytes)
            if root.tag not in {"testsuite", "testsuites"}:
                raise ValueError("unexpected JUnit root element")
        except (ET.ParseError, ValueError) as exc:
            return {
                "status": "NOT_VERIFIED", "capture": capture.to_dict(),
                "evidence_dir": str(evidence), "reason": f"JUnit report is invalid: {exc}",
                "provenance_authenticity_status": "UNAUTHENTICATED",
            }
        # Cross-check the JUnit counts against the pytest terminal summary in
        # the parent-captured stdout.log. The JUnit file lives in staging and
        # can be rewritten by test code after pytest writes it; stdout.log is
        # written by the parent process, so a forged JUnit that disagrees
        # with the terminal summary is caught here. A missing summary line
        # (collection error, crash) is not itself a failure: the exit code
        # already covers those paths.
        try:
            stdout_bytes = _read_execution_artifact(
                evidence / "stdout.log", MAX_EXECUTION_ARTIFACT_BYTES, "pytest stdout log"
            )
            terminal = _pytest_terminal_summary_counts(stdout_bytes)
            junit = _junit_counts(root)
            if terminal is not None and junit is not None:
                terminal_total = (
                    terminal["passed"] + terminal["failed"] + terminal["skipped"]
                    + terminal["errors"]
                )
                if (terminal["failed"] != junit["failed"]
                        or terminal["errors"] != junit["errors"]
                        or terminal_total != junit["tests"]):
                    inventory_errors.append(
                        "JUnit counts disagree with pytest terminal summary: "
                        f"terminal(passed={terminal['passed']}, failed={terminal['failed']}, "
                        f"skipped={terminal['skipped']}, errors={terminal['errors']}) vs "
                        f"JUnit(tests={junit['tests']}, failures={junit['failed']}, "
                        f"errors={junit['errors']}, skipped={junit['skipped']})"
                    )
        except (OSError, ValueError) as exc:
            inventory_errors.append(f"pytest terminal summary cannot be cross-checked: {type(exc).__name__}")
        snapshot_root = evidence / "source_snapshot"
        snapshot_root.mkdir(mode=0o700, exist_ok=False)
        archived_files = _extract_archive(archive, snapshot_root)
        if archived_files != source_files:
            raise ValueError("preserved source snapshot differs from the executed Git archive")
        (evidence / "junit.xml").write_bytes(report_bytes)
        (evidence / "runner-plugin.py").write_text(_RUNNER_PLUGIN, encoding="utf-8")
        if runtime_imports is not None:
            (evidence / "runtime-imports.json").write_bytes(runtime_imports_bytes)
        (evidence / "source-manifest.json").write_bytes(
            (json.dumps(source_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        )
        # The test process has exited (capture_run returned), so the run key
        # can now be persisted outside the evidence directory for post-hoc
        # verification. Written before signing so a later failure cannot
        # leave signed evidence without its key.
        key_path = write_key_file(parent / key_file_name(capture.run_id), capture.run_id, run_key)
        gate_path = evidence / "final_gate_report.json"
        gate = json.loads(_read_execution_artifact(
            gate_path, MAX_EXECUTION_ARTIFACT_BYTES, "final gate report"
        ).decode("utf-8"))
        provenance = {
            "schema_version": SCHEMA_VERSION,
            "execution_id": capture.run_id,
            "started_at": gate["started_at"],
            "completed_at": gate["ended_at"],
            "runner": {"name": "pytest", "version": runner_version, "plugin_sha256": plugin_digest},
            "report_format": "JUNIT_XML",
            "report_relative_path": "junit.xml",
            "report_sha256": sha256_hex(report_bytes),
            # Parent-observed process outcome, independent of the JUnit file
            # the test process could rewrite: importers must require exit
            # code 0 before treating any PASSED in the report as executed.
            "pytest_exit_code": capture.exit_code,
            "pytest_exit_status": capture.status,
            # The controlled runner never distributes tests across workers:
            # its allowed pytest option list contains no -n/--numprocesses,
            # so the summed case durations must fit inside the wall clock.
            "worker_count": 1,
            # UID isolation A: "POSIX_USER:<name>" when the test process ran
            # as a dedicated POSIX user, else "NONE". Isolation keeps the
            # test process out of the evidence directory, the HMAC key file,
            # and the original project tree. A2 additionally makes the
            # staged source tree read-only for isolated runs (see
            # staging_source_mode); the JUnit report and runtime-imports
            # manifest stay writable by the test process, so authenticity
            # stays UNAUTHENTICATED (see limitations).
            "runner_isolation": runner_isolation,
            # A2: "READ_ONLY" when the staged source tree was made
            # read-only for an isolated run, else "WRITABLE".
            "staging_source_mode": staging_source_mode,
            "source_snapshot": {
                "manifest_schema_version": source_manifest["schema_version"],
                "manifest_sha256": manifest_hash,
                "file_count": len(source_files),
                "capture_method": "GIT_ARCHIVE_HEAD_STAGED_COPY",
            },
            "working_tree_state": {"state": "CLEAN", "untracked_files": "INCLUDED"},
            "command_fingerprint": command_fingerprint,
            "environment_fingerprint": environment_fingerprint,
            "runtime_imports": {
                "path": "runtime-imports.json" if runtime_imports is not None else None,
                "sha256": sha256_hex(runtime_imports_bytes) if runtime_imports is not None else None,
                "module_count": runtime_imports.get("module_count") if runtime_imports is not None else 0,
                "status": runtime_imports.get("status") if runtime_imports is not None else "NOT_AVAILABLE",
            },
        }
        # Sign the provenance record with the parent-held run key, then
        # validate structure AND signature. A VALID signature proves the
        # parent produced this record; runner authenticity stays
        # UNAUTHENTICATED (a signature does not prove the test process
        # actually executed the reported tests).
        provenance["signature"] = sign_document(provenance, run_key)
        validation = validate_execution_provenance(
            json.dumps(provenance).encode("utf-8"), report_bytes, source_manifest_bytes,
            hmac_key=run_key,
        )
        provenance_signature_status = validation["provenance_signature_status"]
        provenance_path = evidence / "execution-provenance.json"
        provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        gate["git_commit"] = head
        gate["source_snapshot_method"] = "GIT_ARCHIVE_HEAD_STAGED_COPY"
        gate["provenance_authenticity_status"] = "UNAUTHENTICATED"
        gate["execution_source_binding_status"] = "STAGED_SNAPSHOT_USED_UNAUTHENTICATED"
        gate["provenance_record_validation"] = "STRUCTURE_AND_DIGESTS_VALIDATED"
        gate["provenance_signature_status"] = provenance_signature_status
        gate["hmac_key_file"] = f"../{key_file_name(capture.run_id)}"
        gate["pytest_report"] = "junit.xml"
        gate["pytest_report_sha256"] = validation["report_sha256"]
        gate["source_manifest"] = "source-manifest.json"
        gate["source_snapshot_root"] = "source_snapshot/"
        gate["source_manifest_sha256"] = validation["source_manifest_sha256"]
        gate["pytest_exit_status"] = capture.status
        gate["runner_isolation"] = runner_isolation
        gate["staging_source_mode"] = staging_source_mode
        gate.setdefault("limitations", []).append(isolation_limitation)
        gate["source_tree_inventory_status"] = "CLEAN" if not inventory_errors else "CHANGED_OR_UNSAFE"
        gate["source_tree_inventory_errors"] = inventory_errors
        gate["runtime_import_binding_status"] = (
            "RUNTIME_IMPORTS_MATCHED_STAGED_SNAPSHOT" if not runtime_imports_errors
            else "RUNTIME_IMPORTS_NOT_BOUND"
        )
        gate["runtime_import_binding_errors"] = runtime_imports_errors
        if (inventory_errors or runtime_imports_errors) and gate.get("status") == "PASS":
            gate["status"] = "FAIL"
            gate.setdefault("errors", []).extend(inventory_errors)
        gate_path.write_text(json.dumps(gate, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        manifest_path = evidence / "evidence_manifest.json"
        manifest = json.loads(_read_execution_artifact(
            manifest_path, MAX_EXECUTION_ARTIFACT_BYTES, "evidence manifest"
        ).decode("utf-8"))
        names = {item["path"] for item in manifest["artifacts"]}
        for name in ("junit.xml", "source-manifest.json", "execution-provenance.json", "runner-plugin.py", "runtime-imports.json"):
            if (evidence / name).is_file() and name not in names:
                manifest["artifacts"].append(_artifact_record(evidence, name))
        for item in source_files:
            snapshot_name = "source_snapshot/" + item["path"]
            manifest["artifacts"].append(_artifact_record(evidence, snapshot_name))
        for item in manifest["artifacts"]:
            if item["path"] == "final_gate_report.json":
                item.update(_artifact_record(evidence, "final_gate_report.json"))
        manifest["artifacts"].sort(key=lambda item: item["path"])
        # Sign the completed manifest and verify the signature in memory so
        # its status can be recorded in the gate report. The gate report is
        # rewritten below with that status, which changes its hash, so the
        # manifest is re-signed after refreshing its gate-report record.
        manifest["signature"] = sign_document(manifest, run_key)
        manifest_signature_status = verify_document_signature(manifest, run_key)
        gate["evidence_manifest_signature_status"] = manifest_signature_status
        gate_path.write_text(json.dumps(gate, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        for item in manifest["artifacts"]:
            if item["path"] == "final_gate_report.json":
                item.update(_artifact_record(evidence, "final_gate_report.json"))
        manifest["signature"] = sign_document(manifest, run_key)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        verified = verify_manifest(evidence, hmac_key=run_key)
        final_status = capture.status if verified["valid"] else "NOT_VERIFIED"
        if (inventory_errors or runtime_imports_errors) and final_status == "PASS":
            final_status = "FAIL"
        return {
            "status": final_status,
            "capture": capture.to_dict(),
            "evidence_dir": str(evidence),
            "git_commit": head,
            "source_file_count": len(source_files),
            "pytest_report": str(evidence / "junit.xml"),
            "provenance_record": str(provenance_path),
            "runner_plugin": str(evidence / "runner-plugin.py"),
            "source_manifest": str(evidence / "source-manifest.json"),
            "source_snapshot_root": str(snapshot_root),
            "evidence_manifest_valid": verified["valid"],
            "evidence_manifest_errors": verified["errors"],
            "evidence_manifest_signature_status": manifest_signature_status,
            "provenance_signature_status": provenance_signature_status,
            "hmac_key_file": str(key_path),
            "source_tree_inventory_status": "CLEAN" if not inventory_errors else "CHANGED_OR_UNSAFE",
            "source_tree_inventory_errors": inventory_errors,
            "provenance_authenticity_status": "UNAUTHENTICATED",
            "execution_source_binding_status": "STAGED_SNAPSHOT_USED_UNAUTHENTICATED",
            "execution_provenance_validation": "STRUCTURE_AND_DIGESTS_VALIDATED",
            "runner_isolation": runner_isolation,
            "staging_source_mode": staging_source_mode,
            "runtime_import_binding_status": (
                "RUNTIME_IMPORTS_MATCHED_STAGED_SNAPSHOT" if not runtime_imports_errors
                else "RUNTIME_IMPORTS_NOT_BOUND"
            ),
            "runtime_import_binding_errors": runtime_imports_errors,
            "runtime_imports_manifest": str(evidence / "runtime-imports.json") if runtime_imports is not None else None,
            "limitations": [
                "Project code executes; this runner is not an OS sandbox and does not block network or host-resource access.",
                "Git worktree must be clean; untracked/modified files are refused rather than silently omitted.",
                "The test process runs from a staged Git archive, not the original project directory.",
                "Runtime import paths and hashes are collected but remain unauthenticated; OS-level isolation and runner authenticity are not established.",
                "A passing pytest report does not establish business-function correctness without reviewed mappings and independent evidence verification.",
                "HMAC signatures prove the parent process produced the provenance record and evidence manifest, not that the test process executed the reported tests. The per-run key is held in parent memory during the run and stored in a 0600 key file outside the evidence directory after the test process exits; a same-user test process that can read parent memory could steal it, so signatures raise the forgery bar but do not replace UID isolation.",
                isolation_limitation,
            ],
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)
