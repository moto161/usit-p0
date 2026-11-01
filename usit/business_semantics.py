"""Business-semantics adapter — USIT P0.8 (the "understand the software" layer).

Turns USIT from a *symbol-level* scanner into a *semantic-level* understander.
Where ``usit.scanner`` stops at "file -> symbol -> call", this module extracts
the *business* shape of a project — entities (database tables and fields), API
endpoints and their authorization, and the documented-vs-code permission gaps —
and reports every finding with file:line evidence and a confidence level.

North-star context (from the project owner's brief): USIT's goal is not "emit
more JSON" or "pass more scanner tests"; it is to let an AI *understand, check
and verify* an existing software project.  The knowledge graph answers "what
symbols exist"; this layer answers "what is this software for, who can do what,
and does the code match what the docs claim".

Scope discipline (unchanged, fail-closed):
  * Read-only.  Never imports/executes target-project code, never runs a build,
    test, DB client, or network request.
  * Reuses ``safeio.open_no_follow``, ``safety.canonical_project``,
    ``safety.is_reparse_point`` and the byte/file budgets; an unsafe path is a
    hard ``SemanticsBlocked``, never a degraded guess.
  * Every claim carries a confidence level: ``VERIFIED_FACT`` (read from an
    artifact), ``STATIC_INFERENCE`` (inferred, not executed), ``UNKNOWN``,
    ``CONFLICT`` (sources disagree).
"""
from __future__ import annotations

import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import safety
from .safeio import open_no_follow

SCHEMA_VERSION = "usit-business-semantics-v1"

CONF_VERIFIED = "VERIFIED_FACT"
CONF_INFERRED = "STATIC_INFERENCE"
CONF_UNKNOWN = "UNKNOWN"
CONF_CONFLICT = "CONFLICT"

MAX_FILE_BYTES = safety.MAX_FILE_BYTES
MAX_TOTAL_READ_BYTES = safety.MAX_TOTAL_READ_BYTES
MAX_FILES = safety.MAX_FILES
MAX_SCAN_SECONDS = safety.MAX_SCAN_SECONDS

# Route-pattern matching for Node/Express style servers.
_ROUTE_LINE = re.compile(
    r"router\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]"
)
_AUTH_REQUIRED = re.compile(r"\bauthRequired\b")
_ADMIN_REQUIRED = re.compile(r"\badminRequired\b")
_ROLE_ADMIN = re.compile(r"role\s*[!=]==?\s*['\"]admin['\"]")
_SECRET_CHECK = re.compile(r"\bis_secret\s*[!=]==?\s*1\b")
_ONLY_SELF_CHECK = re.compile(r"\bis_only_self\s*[!=]==?\s*1\b")

# Markdown table row: `| a | b | c |`.
_MD_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_ALIGN_CELL = re.compile(r"^:?-{2,}:?$")

# Permission-semantics field names: a DB field whose name signals it gates
# visibility or access.  If such a field is documented but never referenced in
# code, that is a real "documented but unimplemented" finding.
#
# Deliberately narrow: only visibility/access-control semantics.  ``role`` and
# ``status`` are excluded — a role/status *column* being unreferenced does not by
# itself mean a permission feature is missing (they are values, not gates).
_PERMISSION_FIELD_RE = re.compile(
    r"(^|_)(is_[a-z]+|only_self|secret|private|public|visible|visibility|"
    r"permission|owner|access|scope|share|core)(_|$)", re.I)

# Feature-name -> permission-field mapping used to cross-check a PRD's "✅ DONE"
# visibility features against the code.  This is deliberately a small, explicit
# table of *known* visibility semantics, not a general NLP mapping: the point is
# evidence, not coverage.  Each entry maps a PRD feature label to the DB field
# that must be referenced somewhere in code for that feature to be real.
_FEATURE_FIELD_MAP: dict[str, str] = {
    "仅自己": "is_only_self",
    "秘密文件": "is_secret",
    "秘密": "is_secret",
}

# Endpoint paths that are public-by-design (no auth needed): login/register,
# health/ping, OAuth callbacks, static asset probes, password reset, and the
# captcha/verification-code gate.  These are NOT flagged as missing-auth.
_PUBLIC_ENDPOINT_RE = re.compile(
    r"(/login|/register|/signup|/signin|/signout|/logout|/health|/ping|"
    r"/captcha|/verif|/code|/otp|/reset|/forgot|/callback|/oauth|/public|"
    r"/status|/version|/favicon|/robots)(/|$)", re.I)

# Status markers used in the PRD/需求文档 (✅ done, 🔧 done-but-known-issues,
# ⏳ pending, ❓ uncertain).
_STATUS_MAP = {
    "✅": "DONE",
    "🔧": "DONE_WITH_ISSUES",
    "⏳": "PENDING",
    "❓": "UNCERTAIN",
    "❌": "NOT_DONE",
}
_STATUS_RE = re.compile(r"(✅|🔧|⏳|❓|❌)")
# Priority markers (P0 must-have ... P3 never).
_PRIORITY_RE = re.compile(r"\b(P0|P1|P2|P3)\b")

# SQL anchors for locating where a missing field *should* have been referenced.
# ``_SQL_TABLE_REF`` matches ``FROM contents`` / ``JOIN contents c`` / ``UPDATE
# contents`` / ``INSERT INTO contents`` so we can find the statements that touch
# the field's table.  ``_SQL_SELECT_LIST`` matches a ``SELECT ... FROM`` span so
# we can detect a sibling visibility field (is_secret) already present in the
# projection, i.e. the exact spot the missing is_only_self belongs.
_SQL_TABLE_REF = re.compile(
    r"(FROM|JOIN|UPDATE|INSERT\s+INTO)\s+([A-Za-z_][\w]*)\b", re.I)
_SQL_SELECT_LIST = re.compile(r"SELECT\s+(.*?)\s+FROM", re.I | re.S)
# Sibling visibility fields: when is_only_self is missing, is_secret is usually
# its sibling; when is_secret is missing, the reverse.  We locate the *sibling*
# reference line in code and report it as "the line where the missing check
# belongs alongside".
_SIBLING_FIELDS = {
    "is_only_self": ["is_secret"],
    "is_secret": ["is_only_self", "is_private"],
}
# Feature-module headings: `## 二、用户与家庭系统（范围层）P0`,
# `### 2.1 用户账号`, `## 〇、当前开发进度（v1.0.26）`.
# The leading ordinal (一、/ 2.1 / 〇、) is stripped so the module name is the
# substantive title only.
_MODULE_HEADING_RE = re.compile(
    r"^#{2,4}\s*"
    r"(?:[〇一二三四五六七八九十\d]+[、.．]\s*)?"   # optional ordinal prefix
    r"(?:\d+\.\d+\s*)?"                            # optional x.y numbering
    r"([^\s（(][^（(]*?)"                            # substantive title
    r"(?:\s*[（(][^）)]*[）)])?"                     # optional parenthetical
    r"(?:\s*(P0|P1|P2|P3))?\s*$")


class SemanticsBlocked(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Entity:
    name: str
    fields: list[dict[str, str]] = field(default_factory=list)
    source: str = ""
    confidence: str = CONF_VERIFIED


@dataclass
class Endpoint:
    method: str = ""
    path: str = ""
    code_auth: str = ""
    source: str = ""
    line: int = 0
    confidence: str = CONF_UNKNOWN


@dataclass
class Discrepancy:
    kind: str = ""
    message: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = CONF_INFERRED


@dataclass
class Feature:
    """One business feature extracted from the PRD/需求文档.

    ``status`` is one of DONE / DONE_WITH_ISSUES / PENDING / UNCERTAIN / NOT_DONE.
    ``priority`` is P0..P3 or empty.
    """
    name: str = ""
    module: str = ""
    status: str = CONF_UNKNOWN
    priority: str = ""
    source: str = ""
    line: int = 0
    confidence: str = CONF_VERIFIED


@dataclass
class FieldGap:
    """A concrete "where the fix should go" location for an unimplemented field.

    When a documented permission field is never referenced in code, this locates
    the *anchor points* that should have referenced it: the SQL statement(s) that
    read/write the field's table, the INSERT column list that should carry the
    field, and the sibling permission-field references that reveal the exact line
    where the missing ``is_only_self`` check belongs.
    """
    field_name: str = ""
    entity: str = ""
    anchors: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = CONF_INFERRED


def _is_skipped(rel: Path) -> bool:
    parts = rel.parts
    return any(
        seg == "node_modules" or seg.startswith(".git") or seg.startswith("__pycache__")
        for seg in parts
    )


class BusinessSemanticsAdapter:
    """Read-only extractor of business semantics."""

    def __init__(self, project_root: str | Path):
        self.root = safety.canonical_project(project_root)
        self._total_read_bytes = 0
        self._file_count = 0
        self._started_at = time.monotonic()
        self._discovered: set[str] = set()
        self._read_cache: dict[str, dict[str, Any]] = {}
        self._files_cache: tuple[Path, ...] | None = None

    def _check_budget(self) -> None:
        if time.monotonic() - self._started_at > MAX_SCAN_SECONDS:
            raise SemanticsBlocked("SCAN_TIME_BUDGET_EXCEEDED", f"Business analysis exceeded {MAX_SCAN_SECONDS} seconds.")
        if len(self._discovered) > MAX_FILES:
            raise SemanticsBlocked("FILE_BUDGET_EXCEEDED", f"Business analysis exceeded {MAX_FILES} discovered entries.")

    def _validate_project_path(self, path: Path) -> Path:
        """Reject reparse components and paths resolving outside the project."""
        absolute = Path(os.path.abspath(os.fspath(path)))
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            if safety.is_reparse_point(current):
                raise SemanticsBlocked("REPARSE_POINT_REJECTED", f"{current} is a link/junction/reparse point.")
        try:
            resolved = path.resolve(strict=False)
        except OSError as exc:
            raise SemanticsBlocked("PATH_CHECK_FAILED", f"Could not resolve {path}: {type(exc).__name__}") from exc
        if resolved != self.root and self.root not in resolved.parents:
            raise SemanticsBlocked("PATH_TRAVERSAL_REJECTED", f"{path} is outside the authorised project root.")
        return resolved

    def _count_discovery(self, path: Path) -> None:
        self._check_budget()
        key = os.path.normcase(os.path.abspath(os.fspath(path)))
        self._discovered.add(key)
        if len(self._discovered) > MAX_FILES:
            raise SemanticsBlocked("FILE_BUDGET_EXCEEDED", f"Business analysis exceeded {MAX_FILES} discovered entries.")
        self._validate_project_path(path)

    # ------------------------------------------------------------------ reading
    def _read_text(self, path: Path) -> dict[str, Any]:
        self._check_budget()
        self._validate_project_path(path)
        if not path.exists() or not path.is_file():
            return {"text": None, "read_ok": False, "error": "MISSING"}
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise SemanticsBlocked("PATH_CHECK_FAILED", f"Could not resolve {path}: {type(exc).__name__}") from exc
        if resolved != self.root and self.root not in resolved.parents:
            raise SemanticsBlocked("PATH_TRAVERSAL_REJECTED", f"{path} is outside the authorised project root.")
        cache_key = os.path.normcase(os.fspath(resolved))
        if cache_key in self._read_cache:
            return dict(self._read_cache[cache_key])
        if self._file_count >= MAX_FILES:
            raise SemanticsBlocked("FILE_BUDGET_EXCEEDED", f"Business analysis exceeds {MAX_FILES} file reads.")
        try:
            fd = open_no_follow(resolved)
        except OSError as exc:
            raise SemanticsBlocked("SAFE_OPEN_FAILED", f"Could not safely open {path}: {type(exc).__name__}") from exc
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise SemanticsBlocked("FILE_SIZE_LIMIT_EXCEEDED", f"{path} exceeds {MAX_FILE_BYTES} regular-file limit.")
            self._validate_project_path(path)
            path_before = os.stat(resolved, follow_symlinks=False)
            identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            if identity != (path_before.st_dev, path_before.st_ino, path_before.st_size,
                            path_before.st_mtime_ns, path_before.st_ctime_ns):
                raise SemanticsBlocked("FILE_CHANGED_DURING_OPEN", f"{path} changed while being opened.")
            if self._total_read_bytes + before.st_size > MAX_TOTAL_READ_BYTES:
                raise SemanticsBlocked("BYTE_BUDGET_EXCEEDED", f"Business analysis exceeds {MAX_TOTAL_READ_BYTES} total bytes.")
            chunks = []
            remaining = MAX_FILE_BYTES + 1
            while remaining > 0:
                block = os.read(fd, min(65536, remaining))
                if not block:
                    break
                chunks.append(block)
                remaining -= len(block)
            raw = b"".join(chunks)
            after = os.fstat(fd)
            self._validate_project_path(path)
            path_after = os.stat(resolved, follow_symlinks=False)
            before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            path_identity = (path_after.st_dev, path_after.st_ino, path_after.st_size,
                             path_after.st_mtime_ns, path_after.st_ctime_ns)
            if before_identity != after_identity or after_identity != path_identity:
                raise SemanticsBlocked("FILE_CHANGED_DURING_READ", f"{path} changed while being read.")
            if len(raw) != before.st_size or len(raw) > MAX_FILE_BYTES:
                raise SemanticsBlocked("FILE_SIZE_LIMIT_EXCEEDED", f"{path} exceeded its size budget during read.")
        finally:
            os.close(fd)
        self._file_count += 1
        self._total_read_bytes += len(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        result = {"text": text, "read_ok": True, "size": len(raw), "error": None}
        self._read_cache[cache_key] = result
        return dict(result)

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    # --------------------------------------------------------------- discovery
    def _project_files(self) -> tuple[Path, ...]:
        """Discover project files once, pruning skipped directories before descent."""
        if self._files_cache is not None:
            return self._files_cache

        found: list[Path] = []

        def on_walk_error(exc: OSError) -> None:
            raise SemanticsBlocked("DIRECTORY_READ_FAILED", f"Could not enumerate project directory: {type(exc).__name__}") from exc

        for current, dirnames, filenames in os.walk(
            self.root, topdown=True, followlinks=False, onerror=on_walk_error
        ):
            self._check_budget()
            base = Path(current)
            kept_dirs: list[str] = []
            for name in dirnames:
                child = base / name
                rel = child.relative_to(self.root)
                if _is_skipped(rel):
                    continue
                self._count_discovery(child)
                kept_dirs.append(name)
            dirnames[:] = kept_dirs

            for name in filenames:
                path = base / name
                rel = path.relative_to(self.root)
                if _is_skipped(rel):
                    continue
                self._count_discovery(path)
                found.append(path)

        self._files_cache = tuple(found)
        return self._files_cache

    def _find_docs(self) -> list[Path]:
        return [p for p in self._project_files() if p.suffix.lower() == ".md"]

    def _find_route_files(self) -> list[Path]:
        out: list[Path] = []
        for path in self._project_files():
            if path.suffix.lower() not in (".js", ".ts"):
                continue
            rel = path.relative_to(self.root)
            if "routes" in rel.parts or path.name in ("auth.js", "auth.ts", "mediaauth.js", "ratelimit.js"):
                out.append(path)
        return out

    def _find_code_files(self) -> list[Path]:
        """All source files whose contents we may grep for field references."""
        suffixes = {".js", ".ts", ".kt", ".java", ".py", ".sql", ".rb", ".go", ".cs", ".php"}
        return [p for p in self._project_files() if p.suffix.lower() in suffixes]

    # ------------------------------------------------- markdown table parsing
    def _parse_md_tables(self, text: str) -> list[dict[str, Any]]:
        """Yield {header:[...], rows:[[...]]} for each pipe table."""
        tables: list[dict[str, Any]] = []
        lines = text.splitlines()
        i = 0
        n = len(lines)
        while i < n:
            m = _MD_ROW.match(lines[i])
            if not m:
                i += 1
                continue
            header = [c.strip() for c in m.group(1).split("|")]
            # Header is valid only if the next line is an alignment row.
            if i + 1 < n:
                nxt = _MD_ROW.match(lines[i + 1])
                if nxt:
                    align = [c.strip() for c in nxt.group(1).split("|")]
                    if align and all(_ALIGN_CELL.match(c) for c in align if c):
                        rows: list[list[str]] = []
                        j = i + 2
                        while j < n:
                            r = _MD_ROW.match(lines[j])
                            if not r:
                                break
                            rows.append([c.strip() for c in r.group(1).split("|")])
                            j += 1
                        tables.append({"header": header, "rows": rows})
                        i = j
                        continue
            i += 1
        return tables

    # ------------------------------------------------------- entity extraction
    def _extract_entities(self, doc_path: Path, text: str) -> list[Entity]:
        """Map DB-design doc headings (``### 2.1 users - 用户表``) to tables.

        The doc layout is: a heading names a table, then one or more pipe tables
        describe its fields.  We walk lines; when a table-name heading is seen we
        remember it, and when a field table follows we bind its rows to that name.
        """
        entities: list[Entity] = []
        current_name: str | None = None
        lines = text.splitlines()
        i = 0
        n = len(lines)
        # Heading forms: "### 2.1 users - 用户表" or "### users".
        heading_re = re.compile(r"^#{2,4}\s*(?:\d+\.\d+\s+)?([\w.]+)\s*(?:-|—|$)")
        while i < n:
            line = lines[i]
            h = heading_re.match(line)
            if h:
                name = h.group(1).strip().rstrip(".")
                # Only a bare identifier is a table name; skip prose headings.
                if re.fullmatch(r"[A-Za-z_][\w]*", name):
                    current_name = name
                i += 1
                continue
            # A field table following a table heading.
            m = _MD_ROW.match(line)
            if m and current_name is not None and i + 1 < n:
                nxt = _MD_ROW.match(lines[i + 1])
                if nxt:
                    header = [c.strip() for c in m.group(1).split("|")]
                    align = [c.strip() for c in nxt.group(1).split("|")]
                    if align and all(_ALIGN_CELL.match(c) for c in align if c):
                        if any("字段" in c or c.lower() in ("field", "column", "column name") for c in header):
                            rows = []
                            j = i + 2
                            while j < n:
                                r = _MD_ROW.match(lines[j])
                                if not r:
                                    break
                                cells = [c.strip() for c in r.group(1).split("|")]
                                if cells and cells[0]:
                                    rows.append({"name": cells[0], "type": cells[1] if len(cells) > 1 else ""})
                                j += 1
                            entities.append(Entity(name=current_name, fields=rows,
                                                   source=self._rel(doc_path), confidence=CONF_VERIFIED))
                            i = j
                            continue
            i += 1
        return entities

    # ------------------------------------------------------ feature extraction
    def _extract_features(self, doc_path: Path, text: str) -> list[Feature]:
        """Extract the feature skeleton from a PRD/需求文档.

        A PRD lists features in Markdown tables with a status column (✅/⏳/🔧/❓)
        and a priority column (P0..P3).  We walk lines; a ``##``/``###`` heading
        names the current *module*, and a table row that carries a status marker
        is a *feature* belonging to that module.
        """
        features: list[Feature] = []
        current_module = ""
        lines = text.splitlines()
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            h = _MODULE_HEADING_RE.match(line)
            if h and line.lstrip().startswith("#"):
                name = h.group(1).strip()
                if name and not _MD_ROW.match(line):
                    current_module = name
                i += 1
                continue
            m = _MD_ROW.match(line)
            if m:
                cells = [c.strip() for c in m.group(1).split("|")]
                # A feature row: has a status marker OR a priority marker, and a
                # non-empty leading name cell that is not a header separator.
                joined = " ".join(cells)
                has_status = bool(_STATUS_RE.search(joined))
                has_priority = bool(_PRIORITY_RE.search(joined))
                if (has_status or has_priority) and cells and cells[0] and cells[0] not in ("项目", "功能", "模块", "场景", "类型"):
                    name = cells[0]
                    # Skip pure-ordinal rows: a bare number, "切片N", or "P0 N"
                    # is a list index, not a feature name.
                    if re.fullmatch(r"\d+", name):
                        i += 1
                        continue
                    if re.fullmatch(r"P[0-3]\s+\d+", name):
                        i += 1
                        continue
                    if name.startswith("切片") or re.fullmatch(r"切片\s*\d+", name):
                        i += 1
                        continue
                    status = CONF_UNKNOWN
                    for c in cells:
                        sm = _STATUS_RE.search(c)
                        if sm:
                            status = _STATUS_MAP.get(sm.group(1), CONF_UNKNOWN)
                            break
                    priority = ""
                    for c in cells:
                        pm = _PRIORITY_RE.search(c)
                        if pm:
                            priority = pm.group(1)
                            break
                    features.append(Feature(
                        name=name, module=current_module, status=status,
                        priority=priority, source=self._rel(doc_path), line=i + 1,
                        confidence=CONF_VERIFIED,
                    ))
            i += 1
        return features

    # ------------------------------------------------------ endpoint extraction
    def _extract_doc_endpoints(self, doc_path: Path, text: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for m in re.finditer(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/api/[\w/:{}_.-]+)", text):
            out.append({"method": m.group(1).upper(), "path": m.group(2), "source": self._rel(doc_path)})
        return out

    def _extract_code_endpoints(self, route_path: Path, text: str) -> list[Endpoint]:
        out: list[Endpoint] = []
        lines = text.splitlines()
        # Module-level middleware: ``router.use(authRequired, adminRequired)``
        # applies to every route defined in this file that does not override it.
        file_default = ""
        for line in lines:
            if re.search(r"router\.use\s*\(", line):
                if _ADMIN_REQUIRED.search(line) or _ROLE_ADMIN.search(line):
                    file_default = "admin"
                elif _AUTH_REQUIRED.search(line):
                    file_default = "auth"
        for i, line in enumerate(lines, start=1):
            m = _ROUTE_LINE.search(line)
            if not m:
                continue
            method, path = m.group(1).upper(), m.group(2)
            # Auth signal: the route's own line first (most specific), then the
            # file-level router.use default.
            auth = ""
            if _ADMIN_REQUIRED.search(line) or _ROLE_ADMIN.search(line):
                auth = "admin"
            elif _AUTH_REQUIRED.search(line):
                auth = "auth"
            if not auth:
                auth = file_default
            out.append(Endpoint(
                method=method, path=path, code_auth=auth,
                source=f"{self._rel(route_path)}:{i}", line=i,
                confidence=CONF_VERIFIED if auth else CONF_INFERRED,
            ))
        return out

    # ------------------------------------------------------------ orchestration
    def _locate_field_gap(self, entity: str, field_name: str,
                          code_blob_by_file: dict[str, str]) -> FieldGap:
        """Locate where a documented-but-unreferenced field *should* appear.

        Strategy (evidence-first, no guessing about semantics):
          1. Find SQL statements that touch the field's table (``FROM/JOIN/
             UPDATE/INSERT INTO <entity>``) — these are the read/write sites.
          2. In each such statement, check whether a *sibling* visibility field
             (e.g. ``is_secret`` for ``is_only_self``) is already referenced; if
             so, that line is the concrete "add the missing check here" anchor.
          3. If the table is never referenced in code at all, report that as its
             own anchor (the whole feature is absent, not just the field).
        Each anchor carries file:line and the matched context line.
        """
        gap = FieldGap(field_name=field_name, entity=entity)
        siblings = _SIBLING_FIELDS.get(field_name, [])
        table_touched = False
        # Pass 1: any line that references a sibling visibility field is a
        # "the missing check belongs here" anchor — sibling fields live in the
        # same WHERE/JOIN/projection, whether or not the table name is on the
        # same line (``WHERE c.is_secret = 0`` has no FROM on its line).
        for rel, text in code_blob_by_file.items():
            for i, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                # Skip pure comment lines — they document is_secret but are not
                # a code site where the missing check belongs.
                if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                    continue
                for sib in siblings:
                    if re.search(rf"\b{re.escape(sib)}\b", line):
                        gap.anchors.append({
                            "rel_path": rel, "line": i,
                            "reason": f"sibling '{sib}' referenced here — missing "
                                      f"'{field_name}' check belongs alongside",
                            "context": line.strip(),
                        })
                        break
        # Pass 2: is the field's table touched at all?  Record it (also used to
        # decide the "whole feature absent" fallback below).
        table_anchor_lines: list[tuple[str, int, str]] = []
        for rel, text in code_blob_by_file.items():
            for i, line in enumerate(text.splitlines(), start=1):
                tm = _SQL_TABLE_REF.search(line)
                if tm and tm.group(2).lower() == entity.lower():
                    table_touched = True
                    table_anchor_lines.append((rel, i, line.strip()))
        if not table_touched:
            gap.anchors.append({
                "rel_path": "", "line": 0,
                "reason": f"table '{entity}' is never referenced in any scanned "
                          f"source file — the entire {field_name} feature is absent",
                "context": "",
            })
        if not gap.anchors and table_anchor_lines:
            # Table touched but no sibling line found: report the first table
            # touch as the best available anchor.
            rel, i, ctx = table_anchor_lines[0]
            gap.anchors.append({
                "rel_path": rel, "line": i,
                "reason": f"table '{entity}' touched here; '{field_name}' is not "
                          f"referenced anywhere — add it here",
                "context": ctx,
            })
        # Dedupe anchors on (rel_path, line) while preserving order.
        seen: set[tuple[str, int]] = set()
        deduped: list[dict[str, Any]] = []
        for a in gap.anchors:
            k = (a["rel_path"], a["line"])
            if k in seen:
                continue
            seen.add(k)
            deduped.append(a)
        gap.anchors = deduped
        return gap

    def analyze(self) -> dict[str, Any]:
        self._check_budget()
        docs = self._find_docs()
        route_files = self._find_route_files()
        code_files = self._find_code_files()

        entities: list[Entity] = []
        features: list[Feature] = []
        doc_endpoints: list[dict[str, str]] = []
        endpoints: list[Endpoint] = []
        discrepancies: list[Discrepancy] = []

        for d in docs:
            data = self._read_text(d)
            if not data["read_ok"]:
                continue
            name = d.name.lower()
            if "需求" in name or "prd" in name or "spec" in name:
                features.extend(self._extract_features(d, data["text"]))
            elif "数据库" in name or "database" in name or "schema" in name:
                entities.extend(self._extract_entities(d, data["text"]))
            doc_endpoints.extend(self._extract_doc_endpoints(d, data["text"]))

        for rf in route_files:
            data = self._read_text(rf)
            if not data["read_ok"]:
                continue
            endpoints.extend(self._extract_code_endpoints(rf, data["text"]))

        # (1) Endpoints the code exposes with NO auth at all — but only flag the
        #     ones that are not public-by-design (login/register/health/...).
        for ep in endpoints:
            if ep.code_auth == "" and not _PUBLIC_ENDPOINT_RE.search(ep.path):
                discrepancies.append(Discrepancy(
                    kind="missing_auth",
                    message=f"{ep.method} {ep.path} has no authRequired/adminRequired and is not a public-by-design path.",
                    evidence=[{"rel_path": ep.source, "line": ep.line}],
                    confidence=CONF_INFERRED,
                ))

        # (2) Documented permission-semantics fields that the code never uses.
        #     A field like ``is_only_self`` in the DB doc but 0 code references is
        #     a "documented but unimplemented" permission feature.
        code_blob_by_file: dict[str, str] = {}
        for cf in code_files:
            data = self._read_text(cf)
            if data["read_ok"]:
                code_blob_by_file[self._rel(cf)] = data["text"]
        all_code = "\n".join(code_blob_by_file.values())

        # (3) PRD features marked ✅ DONE whose backing permission field is never
        #     referenced in code.  This is the strongest "documented-but-fake"
        #     signal: the docs claim the feature is done, but the field that would
        #     make it real appears nowhere in the scanned sources.
        for feat in features:
            field_name = _FEATURE_FIELD_MAP.get(feat.name)
            if field_name is None:
                continue
            if feat.status not in ("DONE", "DONE_WITH_ISSUES"):
                continue
            if re.search(rf"\b{re.escape(field_name)}\b", all_code):
                continue
            discrepancies.append(Discrepancy(
                kind="feature_documented_done_but_unimplemented",
                message=(
                    f"PRD 标记「{feat.name}」为 {feat.status}({feat.priority})，"
                    f"但代码全项目零引用其对应字段 '{field_name}'。"
                ),
                evidence=[{
                    "feature": feat.name,
                    "module": feat.module,
                    "status": feat.status,
                    "field": field_name,
                    "rel_path": feat.source,
                    "line": feat.line,
                }],
                confidence=CONF_CONFLICT,
            ))

        seen_fields: set[tuple[str, str]] = set()
        field_gaps: list[FieldGap] = []
        for e in entities:
            for f in e.fields:
                fname = f.get("name", "")
                if not _PERMISSION_FIELD_RE.search(fname):
                    continue
                key = (e.name, fname)
                if key in seen_fields:
                    continue
                seen_fields.add(key)
                if not re.search(rf"\b{re.escape(fname)}\b", all_code):
                    discrepancies.append(Discrepancy(
                        kind="documented_field_unimplemented",
                        message=(
                            f"Permission field '{e.name}.{fname}' is documented but "
                            f"never referenced in any scanned source file."
                        ),
                        evidence=[{"rel_path": e.source, "field": fname, "entity": e.name}],
                        confidence=CONF_INFERRED,
                    ))
                    field_gaps.append(
                        self._locate_field_gap(e.name, fname, code_blob_by_file))

        return {
            "schema_version": SCHEMA_VERSION,
            "model": "BusinessSemantics",
            "project_root": str(self.root),
            "entity_count": len(entities),
            "entities": [
                {"name": e.name, "field_count": len(e.fields), "fields": e.fields,
                 "source": e.source, "confidence": e.confidence}
                for e in entities
            ],
            "feature_count": len(features),
            "features": [
                {"name": f.name, "module": f.module, "status": f.status,
                 "priority": f.priority, "source": f.source, "line": f.line,
                 "confidence": f.confidence}
                for f in features
            ],
            "doc_endpoint_count": len(doc_endpoints),
            "endpoint_count": len(endpoints),
            "endpoints": [
                {"method": ep.method, "path": ep.path, "code_auth": ep.code_auth,
                 "source": ep.source, "confidence": ep.confidence}
                for ep in endpoints
            ],
            "discrepancy_count": len(discrepancies),
            "discrepancies": [
                {"kind": d.kind, "message": d.message, "evidence": d.evidence, "confidence": d.confidence}
                for d in discrepancies
            ],
            "field_gap_count": len(field_gaps),
            "field_gaps": [
                {"field": g.field_name, "entity": g.entity, "anchors": g.anchors,
                 "confidence": g.confidence}
                for g in field_gaps
            ],
        }


def analyze_business(project_root: str | Path) -> dict[str, Any]:
    return BusinessSemanticsAdapter(project_root).analyze()
