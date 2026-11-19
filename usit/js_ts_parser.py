"""Safe, project-local TypeScript Compiler API bridge for static JS/TS analysis."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from . import safeio
from .limits import ParserTaskSupervisor
from .safety import MAX_PARSER_TASK_SECONDS, ScanBlocked

PARSER_NAME = "typescript-compiler-api"
# P0.7.2: the adapter is a subprocess parser task, so it must obey the single
# protocol limit. The local alias is kept for backwards compatibility and is
# asserted equal at import time.
MAX_ADAPTER_SECONDS = MAX_PARSER_TASK_SECONDS
if MAX_ADAPTER_SECONDS != MAX_PARSER_TASK_SECONDS:
    raise ScanBlocked("SAFETY_LIMIT_CONFIGURATION_INVALID", "Adapter timeout must equal the protocol parser-task limit.")
EXPECTED_TYPESCRIPT_VERSION = "6.0.3"
SOURCE_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue"}
USIT_ROOT = Path(__file__).resolve().parents[1]

# P3: only function-like symbols own source lines. Classes, interfaces,
# types and enums do not; lines inside them belong to their methods (or to
# no function at all when outside any method).
_FUNCTION_OWNER_KINDS = frozenset({"function", "method"})


def ts_function_owners(adapter_result: dict[str, Any]) -> dict[str, dict[int, str]]:
    """Map source lines to the innermost enclosing TS/JS function symbol.

    Pure function over an ``analyze_js_ts_project`` result (its ``files``
    mapping). A symbol owns the closed line interval ``[line, end_line]``;
    nested functions resolve to the innermost one (smallest span wins, then
    earliest start, then symbol id — the same rule as
    ``identity_evidence._python_function_owners``). Symbols without a
    usable ``end_line`` are skipped (fail closed). The owner id is built
    from the symbol's bare ``name`` and ``line`` (``symbol:<path>#<name>:<line>``):
    that is the form the scanner uses for graph symbol node ids and the
    adapter uses for ``caller_symbol_id``, so the owner matches the
    database-access owner and the graph's symbol lookup. The adapter's
    ``symbol_id`` field must NOT be reused here: it carries the qualified
    name (``Class.method``), which matches neither.
    """
    owners: dict[str, dict[int, str]] = {}
    files = adapter_result.get("files")
    if not isinstance(files, dict):
        return owners
    for path, parsed in files.items():
        if not isinstance(path, str) or not isinstance(parsed, dict):
            continue
        symbols = parsed.get("symbols")
        if not isinstance(symbols, list):
            continue
        scopes: list[tuple[int, int, int, str]] = []
        for symbol in symbols:
            if not isinstance(symbol, dict):
                continue
            if symbol.get("kind") not in _FUNCTION_OWNER_KINDS:
                continue
            start = symbol.get("line")
            end = symbol.get("end_line")
            name = symbol.get("name")
            if (not isinstance(start, int) or not isinstance(end, int)
                    or not isinstance(name, str) or not name
                    or end < start):
                continue
            scopes.append((start, end, end - start,
                           f"symbol:{path}#{name}:{start}"))
        if not scopes:
            continue
        file_owners: dict[int, str] = {}
        for start, end, _span, symbol_id in sorted(
                scopes, key=lambda item: (item[2], item[0], item[3])):
            for line_no in range(start, end + 1):
                file_owners.setdefault(line_no, symbol_id)
        owners[path] = file_owners
    return owners


def _canonical_key(path: Path) -> str:
    """Comparison key for canonical paths: resolved, separator- and case-normalised.

    Windows paths are case-insensitive and accept both separators, so a raw
    string comparison would let ``C:\\x\\Typescript`` masquerade as
    ``C:/x/typescript``.  Every trust decision below compares canonical keys.
    """
    return str(path).replace("\\", "/").rstrip("/").lower()


def _is_reparse(path: Path) -> bool:
    """True when ``path`` is a symbolic link, junction or other reparse point."""
    try:
        return safeio.is_reparse(os.lstat(path))
    except OSError:
        return False


def _realpath(path: Path) -> Path:
    """Canonicalise ``path``, resolving links/junctions/``..``."""
    return Path(os.path.realpath(str(path))).resolve()


def _within(root: Path, candidate: Path) -> bool:
    """True only when ``candidate`` lies strictly inside ``root``.

    Uses a relative-path computation rather than a string ``startswith`` so a
    sibling directory whose name merely shares a prefix (``typescript-evil``)
    can never be mistaken for a child of ``typescript``.
    """
    try:
        relative = os.path.relpath(str(candidate), str(root))
    except ValueError:
        return False
    return relative != ".." and not relative.startswith(".." + os.sep) and not os.path.isabs(relative)


def verify_ts_lib_trust(usit_root: Path | None = None) -> dict[str, Any]:
    """Prove the TypeScript install that would actually be loaded is trusted.

    A version-number match alone is NOT trust: P0.7.7 demonstrated that a
    junction redirecting ``node_modules/typescript`` to an out-of-tree copy of a
    valid 6.0.3 install was accepted and used to drive real analysis.  This
    helper therefore verifies the *canonical path and provenance* of the
    compiler before anything is loaded:

    1. the install directory exists and is a directory;
    2. it is not itself a link/junction/reparse point;
    3. its canonical (``realpath``) path equals its declared path, so no
       redirect, ``..`` escape or case/separator trick is in play;
    4. the canonical path of every key file (``package.json``,
       ``lib/typescript.js``) still resolves *inside* that install;
    5. only then is the declared version compared with the expected one.

    Returns a dict with ``trusted`` (bool), a machine-readable ``reason`` when
    untrusted, the declared and canonical paths, and the observed version.
    """
    root = (usit_root if usit_root is not None else USIT_ROOT).resolve()
    ts_lib = root / "node_modules" / "typescript"
    result: dict[str, Any] = {
        "trusted": False, "usit_root": str(root), "ts_lib": str(ts_lib),
        "real_path": None, "version": None, "reason": None,
    }

    if not ts_lib.exists():
        result["reason"] = "PROJECT_LOCAL_TYPESCRIPT_MISSING"
        return result
    if not ts_lib.is_dir():
        result["reason"] = "TYPESCRIPT_PATH_NOT_A_DIRECTORY"
        return result
    if _is_reparse(ts_lib):
        result["reason"] = "TS_LIB_ROOT_IS_REPARSE_POINT"
        return result

    try:
        real = _realpath(ts_lib)
    except OSError:
        result["reason"] = "TS_LIB_REALPATH_FAILED"
        return result
    result["real_path"] = str(real)
    if _canonical_key(real) != _canonical_key(ts_lib):
        result["reason"] = "TS_LIB_ROOT_REDIRECTED"
        return result

    for rel in ("package.json", os.path.join("lib", "typescript.js")):
        candidate = ts_lib / rel
        if not candidate.exists():
            result["reason"] = "TS_LIB_FILE_MISSING"
            result["detail"] = rel
            return result
        try:
            file_real = _realpath(candidate)
        except OSError:
            result["reason"] = "TS_LIB_FILE_UNRESOLVABLE"
            result["detail"] = rel
            return result
        if not _within(real, file_real):
            result["reason"] = "TS_LIB_FILE_OUTSIDE_INSTALL"
            result["detail"] = rel
            return result

    try:
        package = json.loads((ts_lib / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["reason"] = "TYPESCRIPT_PACKAGE_UNREADABLE"
        return result
    version = package.get("version")
    result["version"] = version
    if version != EXPECTED_TYPESCRIPT_VERSION:
        result["reason"] = "UNEXPECTED_TYPESCRIPT_VERSION"
        return result

    result["trusted"] = True
    return result


def parser_availability(project_root: Path) -> dict[str, Any]:
    """Report JS/TS parser availability using the same trust rule the loader uses.

    P0.7.8: availability is derived from :func:`verify_ts_lib_trust`, so the
    gate and the actual load path can never disagree — if the compiler's
    canonical path cannot be proven to live in the trusted USIT install, this
    reports unavailable (fail closed) instead of permitting a silent load.
    """
    root = project_root.resolve()
    node = shutil.which("node")
    if not node:
        return {"available": False, "reason": "NODE_RUNTIME_MISSING", "parser_name": PARSER_NAME}
    trust = verify_ts_lib_trust(USIT_ROOT)
    # P0.7.9: public capability output must not disclose absolute install paths.
    # Report only the trust decision; detailed paths remain internal to the
    # verifier and are never serialized into project-facing output.
    load_status = {
        "typescript_install": "PROJECT_LOCAL",
        "typescript_install_path_status": (
            "VERIFIED_WITHIN_USIT_ROOT" if trust["trusted"] else "NOT_VERIFIED"
        ),
    }
    if not trust["trusted"]:
        return {
            "available": False, "reason": trust["reason"], "node": node,
            "parser_name": PARSER_NAME, **load_status,
        }
    return {
        "available": True, "node": node, "version": trust["version"],
        "parser_name": PARSER_NAME, **load_status,
    }


def analyze_js_ts_project(project_root: Path, source_paths: list[Path], *, supervisor: ParserTaskSupervisor | None = None,
                          adapter: Path | None = None,
                          js_ts_source: dict[str, str] | None = None,
                          js_ts_config: dict[str, str] | None = None) -> dict[str, Any]:
    """Analyze discovered JS/TS paths with an argv-only subprocess and JSON stdin.

    P0.7.5: when ``js_ts_source`` is supplied it is a mapping of project-relative
    POSIX paths to the *already-decoded, parent-verified* source text.  That text
    is passed to the adapter over JSON/stdin, so the adapter reads source only
    from memory and never re-opens a project file by path (eliminating the
    check-then-read race).  ``js_ts_config`` optionally carries a root
    tsconfig/jsconfig text the same way.  When neither is supplied the parser
    falls back to the legacy disk mode (paths only) for backward compatibility
    with existing callers and tests.

    ``adapter`` exists so fault-injection tests can substitute a controllable
    script; production callers never pass it.
    """
    root = project_root.resolve(strict=True)
    supervisor = supervisor if supervisor is not None else ParserTaskSupervisor()
    availability = parser_availability(root)
    # Public parser output is a strict allow-list. Never forward verifier or
    # adapter internals such as absolute TypeScript install paths.
    public_availability = {
        key: availability[key]
        for key in ("available", "reason", "version", "parser_name",
                    "typescript_install", "typescript_install_path_status")
        if key in availability
    }
    if js_ts_source is not None:
        # Memory mode (P0.7.5): the relative path set is derived from the
        # already-verified source map (project-relative strings).  We never
        # resolve ``source_paths`` here, so a source file swapped for a
        # symlink/junction after capture cannot redirect or escape resolution —
        # the parser only ever reads the content handed to it by the parent.
        paths = sorted({rel for rel in js_ts_source
                        if Path(rel).suffix.lower() in SOURCE_EXTENSIONS})
    else:
        # Legacy disk mode (backward compatible): the adapter reads by path, so
        # every path must resolve strictly within the project root.
        paths = sorted({path.resolve(strict=True).relative_to(root).as_posix() for path in source_paths
                        if path.suffix.lower() in SOURCE_EXTENSIONS and path.is_file()})
    if not availability["available"]:
        return {
            "contract_version": "usit-parser-result-v1",
            "parser_name": PARSER_NAME,
            "parser_version": "UNAVAILABLE",
            "available": False,
            "availability": public_availability,
            "project_diagnostics": [{"code": availability["reason"], "severity": "UNKNOWN"}],
            "files": {}, "records": [],
        }

    adapter = Path(adapter) if adapter is not None else Path(__file__).with_name("js_ts_adapter.cjs").resolve(strict=True)
    if js_ts_source is not None:
        # Memory mode: the scanner already read and verified these files.
        files_payload = [{"rel": rel, "content": text} for rel, text in sorted(js_ts_source.items())
                         if Path(rel).suffix.lower() in SOURCE_EXTENSIONS]
        config_payload = None
        if js_ts_config:
            for name in ("tsconfig.json", "jsconfig.json"):
                if name in js_ts_config:
                    config_payload = {"name": name, "content": js_ts_config[name]}
                    break
        payload = json.dumps({"files": files_payload, "config": config_payload},
                             ensure_ascii=False, separators=(",", ":"))
    else:
        # Legacy disk mode (backward compatible): adapter reads by path.
        payload = json.dumps({"files": paths}, ensure_ascii=False, separators=(",", ":"))
    argv = [str(availability["node"]), str(adapter), str(root), str(USIT_ROOT)]
    try:
        # P0.7.2: the adapter runs under the supervisor so the 45s per-task limit
        # is enforced by a real kill plus termination verification, and so the
        # concurrency cap covers this parser task too.
        completed = supervisor.run_subprocess(
            "js_ts_adapter", argv, timeout=MAX_PARSER_TASK_SECONDS, cwd=str(root), input_text=payload,
        )
    except ScanBlocked as exc:
        code = "ADAPTER_TIMEOUT" if exc.code == "PARSER_TASK_TIMEOUT" else (
            "ADAPTER_CANCELLATION_FAILED" if exc.code == "PARSER_CANCELLATION_FAILED" else "ADAPTER_START_FAILED")
        return {
            "contract_version": "usit-parser-result-v1", "parser_name": PARSER_NAME,
            "parser_version": availability["version"], "available": True,
            "availability": availability,
            "project_diagnostics": [{"code": code, "severity": "ERROR", "cancellation":
                "killed_and_verified" if code == "ADAPTER_TIMEOUT" else "unverified"}],
            "files": {}, "records": [],
        }
    returncode = completed["returncode"]
    if returncode != 0:
        return {
            "contract_version": "usit-parser-result-v1", "parser_name": PARSER_NAME,
            "parser_version": availability["version"], "available": True,
            "availability": availability,
            "project_diagnostics": [{"code": "ADAPTER_FAILED", "severity": "ERROR", "message": "Node analyzer exited with code " + str(returncode)}],
            "stderr_excerpt": (completed.get("stderr") or "")[:500], "files": {}, "records": [],
        }
    try:
        result = json.loads(completed["stdout"])
    except json.JSONDecodeError:
        return {
            "contract_version": "usit-parser-result-v1", "parser_name": PARSER_NAME,
            "parser_version": availability["version"], "available": True,
            "availability": availability,
            "project_diagnostics": [{"code": "ADAPTER_INVALID_JSON", "severity": "ERROR"}],
            "files": {}, "records": [],
        }
    result["availability"] = public_availability
    return result
