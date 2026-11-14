"""Conservative database access evidence extraction.

This module never executes SQL or target code. It recognizes SQL passed as a
literal expression to common execute/query/raw methods: single string
literals, implicit concatenation, ``+`` chains of literals, multiline
(triple-quoted / template) literals, and f-string / template literals whose
static skeleton survives with ``?`` placeholders for interpolations. Any
operand that is not a literal (a variable, a call, ``.format()``, ``%``)
leaves the statement UNKNOWN -- the static skeleton is never guessed.
Table names take the last segment of a possibly schema-qualified
reference (``catalog.schema.table`` -> ``table``), with quoted
identifiers unquoted; a segment that is not a plain identifier is
dropped, never guessed. Comma-joined tables in a FROM clause are
recognized together (subquery items and table-function calls excepted).
A sentence-initial WITH clause is stripped (its CTE names are excluded
from tables); WITH clauses inside subqueries (possibly nested) are
likewise collected and their CTE names excluded; UNION/INTERSECT/EXCEPT
branches are recognized together; `AS [NOT] MATERIALIZED` is accepted.
A literal holding several statements separated by top-level semicolons
yields one record per SELECT/INSERT/UPDATE/DELETE statement (same source
line); other fragments (BEGIN/COMMIT/SET/...) yield no record.
SQLAlchemy-style ``session.query(Model)`` calls are typed from the
project's own model classes: a model whose class body directly assigns
``__tablename__ = '<table>'`` maps to that table, and a ``query()`` whose
arguments are all such bare model names yields one SELECT record with
``parse_status`` ``ORM_MODEL``. A model without a resolvable table, an
ambiguous class name, or a non-model argument leaves the call UNKNOWN.
Django-style ``Model.objects.filter/get/exclude/all/create`` calls are
typed the same way from the project's own model classes: a model whose
nested ``Meta`` class directly assigns ``db_table = '<table>'`` maps to
that table. Otherwise a Django model (a ``models.Model`` subclass inside
a Django app, neither abstract nor a proxy) gets Django's default table
``<app_label>_<classname lowercased>``, with the app label from
``Meta.app_label``, the app's ``apps.py`` ``AppConfig`` label, or the
app directory name, in that order. Chained ``.update(...)`` / ``.delete()`` calls whose chain
bottoms out at such a ``Model.objects.<read method>`` call yield one
UPDATE / DELETE record instead of the base SELECT record.
"""
from __future__ import annotations

import ast
import re
from typing import Any

_DB_METHODS = {"execute", "query", "raw"}
# One identifier segment: a bare identifier, or a quoted identifier
# (double quotes with "" escapes, backticks, or square brackets).
_TABLE_SEGMENT = r'(?:[A-Za-z_][A-Za-z0-9_$]*|"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\])'
_TABLE_SEGMENT_RE = re.compile(_TABLE_SEGMENT)
# A table reference: one segment, optionally dot-qualified
# (catalog.schema.table, public."photos", "my schema".photos).
# All groups are non-capturing, so the capture-group numbers of the
# regexes below (table reference = 1, columns = 1) stay unchanged.
_TABLE_REF = _TABLE_SEGMENT + r"(?:\s*\.\s*" + _TABLE_SEGMENT + r")*"
_SQL_TABLE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(" + _TABLE_REF + r")", re.I)
_SQL_SELECT = re.compile(r"^\s*SELECT\s+(.*?)\s+FROM\s+", re.I | re.S)
_SQL_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+" + _TABLE_REF + r"\s*\(([^)]*)\)", re.I | re.S)
_SQL_UPDATE = re.compile(r"^\s*UPDATE\s+" + _TABLE_REF + r"\s+SET\s+(.*?)(?:\s+WHERE\s+|$)", re.I | re.S)
# First word of a statement that yields a record: the four DML operations
# plus WITH, whose main query is parsed by _parse_sql.
_DML_FIRST = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.I)
_JS_CALL = re.compile(
    r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.(execute|query|raw)\s*\("
)


def _simple_columns(value: str) -> list[str]:
    result: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item or item == "*" or "(" in item or ")" in item:
            return []
        item = re.sub(r"\s+AS\s+.+$", "", item, flags=re.I).strip()
        item = item.split(".")[-1].strip().strip("`\"'[]")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", item):
            return []
        result.append(item)
    return sorted(set(result))


def _normalize_table(reference: str) -> str | None:
    """Take the last segment of a (possibly qualified/quoted) table reference.

    Schema/catalog qualifiers are dropped; quoted segments are unquoted
    ("" inside double quotes becomes "). Returns None when the last
    segment is not a plain identifier, so an unusable table name is
    dropped instead of guessed.
    """
    segments = _TABLE_SEGMENT_RE.findall(reference)
    if not segments:
        return None
    last = segments[-1]
    if len(last) >= 2 and last[0] == '"' and last[-1] == '"':
        name = last[1:-1].replace('""', '"')
    elif len(last) >= 2 and last[0] == "`" and last[-1] == "`":
        name = last[1:-1]
    elif len(last) >= 2 and last[0] == "[" and last[-1] == "]":
        name = last[1:-1]
    else:
        name = last
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) else None


_FROM_CLAUSE_END = re.compile(
    r"\b(?:WHERE|GROUP|ORDER|LIMIT|HAVING|JOIN|ON|UNION|INTERSECT|EXCEPT)\b|;", re.I)
_TABLE_REF_AT_START = re.compile(r"^\s*(" + _TABLE_REF + r")")


def _split_top_level_commas(segment: str) -> list[str]:
    """Split on commas that are not inside parentheses or quotes.

    Tracks paren depth and single/double/backtick/bracket quoting (with
    '' and "" escapes); a comma inside any of those does not split.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(segment):
        ch = segment[i]
        if quote is not None:
            current.append(ch)
            if quote in ("'", '"') and ch == quote:
                if i + 1 < len(segment) and segment[i + 1] == quote:
                    current.append(quote)
                    i += 2
                    continue
                quote = None
            elif quote == "`" and ch == "`":
                quote = None
            elif quote == "]" and ch == "]":
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            current.append(ch)
        elif ch == "[":
            quote = "]"
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def _split_top_level_statements(text: str) -> list[str]:
    """Split on semicolons at paren depth 0, outside quotes and comments.

    Same single-pass scan shape as _split_top_level_commas, plus ``--``
    line comments and ``/* ... */`` block comments treated as opaque:
    a semicolon inside a string literal, a comment, or parentheses does
    not split. Empty fragments (leading/trailing/double semicolons, blank
    text) are dropped.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    comment: str | None = None  # "--" or "/*"
    i = 0
    while i < len(text):
        ch = text[i]
        if comment == "--":
            current.append(ch)
            if ch == "\n":
                comment = None
            i += 1
            continue
        if comment == "/*":
            current.append(ch)
            if ch == "*" and i + 1 < len(text) and text[i + 1] == "/":
                current.append("/")
                comment = None
                i += 2
                continue
            i += 1
            continue
        if quote is not None:
            current.append(ch)
            if quote in ("'", '"') and ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    current.append(quote)
                    i += 2
                    continue
                quote = None
            elif quote == "`" and ch == "`":
                quote = None
            elif quote == "]" and ch == "]":
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            current.append(ch)
        elif ch == "[":
            quote = "]"
            current.append(ch)
        elif ch == "-" and i + 1 < len(text) and text[i + 1] == "-":
            comment = "--"
            current.append("-")
            current.append("-")
            i += 2
            continue
        elif ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            comment = "/*"
            current.append("/")
            current.append("*")
            i += 2
            continue
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == ";" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return [p for p in parts if p.strip()]


def _from_clause_tables(text: str) -> set[str]:
    """Tables listed after FROM as comma-separated items.

    Supplements the keyword scan: 'FROM photos, albums' yields both.
    Subquery items and table-function calls are skipped -- their inner
    tables are picked up by the per-FROM scan and the keyword scan.
    """
    found: set[str] = set()
    for from_match in re.finditer(r"\bFROM\b", text, re.I):
        start = from_match.end()
        end_match = _FROM_CLAUSE_END.search(text, start)
        segment = text[start:end_match.start() if end_match else len(text)]
        for item in _split_top_level_commas(segment):
            item = item.strip()
            if not item or item.startswith("("):
                continue
            ref_match = _TABLE_REF_AT_START.match(item)
            if ref_match is None:
                continue
            if item[ref_match.end():].lstrip().startswith("("):
                continue  # table function call, e.g. generate_series(1, 10)
            name = _normalize_table(ref_match.group(1))
            if name is not None:
                found.add(name)
    return found


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _scan_balanced_parens(text: str, start: int) -> int | None:
    """Index just past the ')' matching the '(' at start, or None.

    Quote-aware: a ')' inside a single/double/backtick/bracket quoted
    region does not close the parens.
    """
    if start >= len(text) or text[start] != "(":
        return None
    depth = 0
    quote: str | None = None
    i = start
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if quote in ("'", '"') and ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            elif quote == "`" and ch == "`":
                quote = None
            elif quote == "]" and ch == "]":
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch == "[":
            quote = "]"
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _parse_cte_clause(text: str, start: int) -> tuple[set[str], int, list[tuple[int, int]]] | None:
    """Parse a WITH clause starting at start.

    Returns (cte_names, end_pos, bodies), where end_pos is the index
    just past the clause (start of the main query) and bodies are the
    (start, end) spans of the CTE subquery bodies, or None when the
    clause does not parse (AS MATERIALIZED without parens, unbalanced
    parens, a missing AS, ...).
    """
    pos = start
    with_match = re.match(r"WITH\b", text[pos:], re.I)
    if with_match is None:
        return None
    pos += with_match.end()
    recursive_match = re.match(r"\s+RECURSIVE\b", text[pos:], re.I)
    if recursive_match is not None:
        pos += recursive_match.end()
    cte_names: set[str] = set()
    bodies: list[tuple[int, int]] = []
    while True:
        pos = _skip_ws(text, pos)
        name_match = _TABLE_SEGMENT_RE.match(text, pos)
        if name_match is None:
            return None
        name = _normalize_table(name_match.group(0))
        if name is not None:
            cte_names.add(name)
        pos = _skip_ws(text, name_match.end())
        # Optional column list "(...)" -- only a column list when AS follows it.
        if pos < len(text) and text[pos] == "(":
            close = _scan_balanced_parens(text, pos)
            if close is None:
                return None
            if not re.match(r"AS\b", text[close:].lstrip(), re.I):
                return None
            pos = _skip_ws(text, close)
        as_match = re.match(r"AS\b", text[pos:], re.I)
        if as_match is None:
            return None
        pos = _skip_ws(text, pos + as_match.end())
        # AS [NOT] MATERIALIZED only changes execution, not table identity.
        materialized_match = re.match(r"(?:NOT\s+)?MATERIALIZED\b", text[pos:], re.I)
        if materialized_match is not None:
            pos = _skip_ws(text, pos + materialized_match.end())
        body_start = pos
        body_close = _scan_balanced_parens(text, pos)
        if body_close is None:
            return None
        bodies.append((body_start, body_close))
        pos = _skip_ws(text, body_close)
        if pos < len(text) and text[pos] == ",":
            pos += 1
            continue
        break
    return cte_names, pos, bodies


def _collect_all_cte_names(text: str) -> set[str] | None:
    """Collect every CTE name defined by any WITH clause in text.

    Worklist over all ``\\bWITH\\b`` positions: each position is parsed
    once; CTE bodies are scanned for further (nested) WITH clauses.
    Returns None when any WITH clause fails to parse (fail closed).
    """
    cte_names: set[str] = set()
    pending: list[int] = [m.start() for m in re.finditer(r"\bWITH\b", text, re.I)]
    seen: set[int] = set()
    while pending:
        start = pending.pop()
        if start in seen:
            continue
        seen.add(start)
        parsed = _parse_cte_clause(text, start)
        if parsed is None:
            return None
        names, _end, bodies = parsed
        cte_names |= names
        for body_start, body_end in bodies:
            for match in re.finditer(r"\bWITH\b", text[body_start:body_end], re.I):
                pending.append(body_start + match.start())
    return cte_names


def _parse_sql(sql: str) -> dict[str, Any]:
    text = sql.strip()
    if not text:
        return {"operation": "UNKNOWN", "tables": [], "columns": [], "parse_status": "UNKNOWN"}
    cte_names: set[str] = set()
    main_text = text
    if re.search(r"\bWITH\b", text, re.I):
        collected = _collect_all_cte_names(text)
        if collected is None:
            return {"operation": "UNKNOWN", "tables": [], "columns": [], "parse_status": "UNKNOWN"}
        cte_names = collected
        if re.match(r"WITH\b", text, re.I):
            parsed = _parse_cte_clause(text, 0)
            if parsed is None:
                return {"operation": "UNKNOWN", "tables": [], "columns": [], "parse_status": "UNKNOWN"}
            main_text = text[parsed[1]:]
    first = re.match(r"^(SELECT|INSERT|UPDATE|DELETE)\b", main_text, re.I)
    if not first:
        return {"operation": "UNKNOWN", "tables": [], "columns": [], "parse_status": "UNKNOWN"}
    operation = first.group(1).upper()
    table_names: set[str] = set()
    for match in _SQL_TABLE.finditer(text):
        name = _normalize_table(match.group(1))
        if name is not None:
            table_names.add(name)
    table_names |= _from_clause_tables(text)
    if cte_names:
        lowered_cte = {name.lower() for name in cte_names}
        table_names = {name for name in table_names if name.lower() not in lowered_cte}
    tables = sorted(table_names)
    columns: list[str] = []
    if operation == "SELECT":
        match = _SQL_SELECT.match(main_text)
        if match:
            columns = _simple_columns(match.group(1))
    elif operation == "INSERT":
        match = _SQL_INSERT.match(main_text)
        if match:
            columns = _simple_columns(match.group(1))
    elif operation == "UPDATE":
        match = _SQL_UPDATE.match(main_text)
        if match:
            names = re.findall(r"(?:^|,)\s*([A-Za-z_][A-Za-z0-9_$]*)\s*=", match.group(1))
            columns = sorted(set(names))
    status = "RESOLVED" if tables else "UNKNOWN"
    return {"operation": operation, "tables": tables, "columns": columns,
            "parse_status": status}


def _resolve_python_sql(node: ast.AST) -> tuple[str, bool] | None:
    """Resolve a Python SQL string expression to (text, has_dynamic).

    Returns None when the expression is not a literal expression: a plain
    variable, a call, ``.format()``, ``%`` formatting and anything else is
    left UNKNOWN rather than guessed. Interpolations in f-strings become
    ``?`` placeholders; ``?`` is not an identifier character, so a table or
    column position filled by an interpolation can never be misread as a
    name.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        dynamic = False
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("?")
                dynamic = True
            else:
                return None
        return "".join(parts), dynamic
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_python_sql(node.left)
        right = _resolve_python_sql(node.right)
        if left is None or right is None:
            return None
        return left[0] + right[0], left[1] or right[1]
    return None


def orm_model_tables(code_by_file: dict[str, str]) -> dict[str, str]:
    """Map SQLAlchemy model class names to tables from ``__tablename__``.

    Pure AST scan of the project's own ``.py`` files: a class whose body
    directly assigns ``__tablename__ = '<table>'`` (plain ``Assign`` or
    annotated ``AnnAssign`` with a string constant) maps its class name to
    that table. A table name must be a plain identifier; anything else is
    ignored. A class name mapping to more than one distinct table is
    ambiguous and dropped entirely. No inheritance is followed, no default
    table name is guessed, and no target code is imported or executed.
    """
    candidates: dict[str, set[str]] = {}
    for path, source in sorted(code_by_file.items()):
        if not path.lower().endswith(".py"):
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                target: ast.expr | None = None
                value: ast.expr | None = None
                if isinstance(stmt, ast.Assign):
                    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                        continue
                    target, value = stmt.targets[0], stmt.value
                elif isinstance(stmt, ast.AnnAssign):
                    if not isinstance(stmt.target, ast.Name) or stmt.value is None:
                        continue
                    target, value = stmt.target, stmt.value
                else:
                    continue
                if target.id != "__tablename__":
                    continue
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    continue
                # The whole value must be one plain identifier: unlike a
                # qualified SQL reference, 'my schema' must not silently
                # become 'schema' (that is what _normalize_table would do
                # with its last-segment rule).
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value.value):
                    continue
                table = value.value
                candidates.setdefault(node.name, set()).add(table)
    return {name: next(iter(tables)) for name, tables in candidates.items()
            if len(tables) == 1}


def _django_meta_values(class_node: ast.ClassDef) -> dict[str, Any]:
    """String/bool constants assigned in a directly nested ``Meta`` class."""
    values: dict[str, Any] = {}
    for stmt in class_node.body:
        if not isinstance(stmt, ast.ClassDef) or stmt.name != "Meta":
            continue
        for meta_stmt in stmt.body:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(meta_stmt, ast.Assign):
                if len(meta_stmt.targets) != 1 or not isinstance(meta_stmt.targets[0], ast.Name):
                    continue
                target, value = meta_stmt.targets[0], meta_stmt.value
            elif isinstance(meta_stmt, ast.AnnAssign):
                if not isinstance(meta_stmt.target, ast.Name) or meta_stmt.value is None:
                    continue
                target, value = meta_stmt.target, meta_stmt.value
            else:
                continue
            if isinstance(value, ast.Constant):
                values[target.id] = value.value
    return values


def _django_app_dirs(code_by_file: dict[str, str]) -> set[str]:
    """Directories that look like Django app roots.

    A directory counts when it holds a ``models.py`` file or a ``models/``
    subpackage: the app's models live there (or below it).
    """
    dirs: set[str] = set()
    for path in sorted(code_by_file):
        if not path.lower().endswith(".py"):
            continue
        parts = path.replace("\\", "/").split("/")
        if parts[-1] == "models.py" and len(parts) >= 2:
            dirs.add("/".join(parts[:-1]))
        elif "models" in parts[:-1]:
            idx = parts.index("models")
            if idx > 0:
                dirs.add("/".join(parts[:idx]))
    return dirs


def _django_app_dir_for_file(path: str, app_dirs: set[str]) -> str | None:
    """Nearest ancestor directory of ``path`` that is a Django app root."""
    parts = path.replace("\\", "/").split("/")[:-1]
    for i in range(len(parts), 0, -1):
        candidate = "/".join(parts[:i])
        if candidate in app_dirs:
            return candidate
    return None


def _django_config_label(apps_source: str, app_dir_name: str) -> tuple[str | None, bool]:
    """Return (label, ambiguous) from an ``apps.py`` source.

    Collects explicit string ``label`` assignments from ``AppConfig``
    subclasses (bare ``AppConfig`` or ``*.AppConfig`` base). A class with
    an explicit ``name`` whose last dotted component differs from the app
    directory name is ignored (it configures another app). Exactly one
    distinct label wins; none means "no custom label"; more than one
    means ambiguous.
    """
    try:
        tree = ast.parse(apps_source)
    except (SyntaxError, ValueError):
        return None, False
    labels: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any((isinstance(b, ast.Name) and b.id == "AppConfig") or
                   (isinstance(b, ast.Attribute) and b.attr == "AppConfig")
                   for b in node.bases):
            continue
        label: str | None = None
        name: str | None = None
        for stmt in node.body:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(stmt, ast.Assign):
                if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                    continue
                target, value = stmt.targets[0], stmt.value
            elif isinstance(stmt, ast.AnnAssign):
                if not isinstance(stmt.target, ast.Name) or stmt.value is None:
                    continue
                target, value = stmt.target, stmt.value
            else:
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            if target.id == "label":
                label = value.value
            elif target.id == "name":
                name = value.value
        if label is None:
            continue
        if name is not None and name.split(".")[-1] != app_dir_name:
            continue
        labels.add(label)
    if len(labels) == 1:
        return next(iter(labels)), False
    if len(labels) == 0:
        return None, False
    return None, True


def django_model_tables(code_by_file: dict[str, str]) -> dict[str, str]:
    """Map Django model class names to tables from ``Meta.db_table`` or the default rule.

    Pure AST scan of the project's own ``.py`` files. A class whose body
    directly contains a nested ``Meta`` class that assigns
    ``db_table = '<table>'`` (plain ``Assign`` or annotated ``AnnAssign``
    with a string constant) maps its class name to that table; the
    explicit table always wins for that class. Otherwise, a Django model
    -- a class whose bases include ``Model`` (bare name or ``*.Model``
    attribute), or that subclasses another Django model found in the same
    scan -- with no explicit table gets Django's default table
    ``<app_label>_<classname lowercased>`` (the class name is lowercased
    whole, so ``PhotoAlbum`` becomes ``photoalbum``), provided the file
    sits inside a Django app (its nearest ancestor directory holding a
    ``models.py`` or a ``models/`` subpackage) and the model is neither
    abstract nor a proxy (``Meta.abstract`` / ``Meta.proxy``). The app
    label comes from ``Meta.app_label`` first, then an explicit ``label``
    on the app's ``apps.py`` ``AppConfig``, then the app directory name.
    A table name must be a plain identifier; anything else is ignored. A
    class name mapping to more than one distinct table is ambiguous and
    dropped entirely. No target code is imported or executed.
    """
    parsed: dict[str, ast.Module] = {}
    for path, source in sorted(code_by_file.items()):
        if not path.lower().endswith(".py"):
            continue
        try:
            parsed[path] = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
    app_dirs = _django_app_dirs(code_by_file)
    classes: list[tuple[str, str, ast.ClassDef]] = []
    for path, tree in sorted(parsed.items()):
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.append((node.name, path, node))
    django_names: set[str] = set()
    for name, _path, node in classes:
        if any((isinstance(b, ast.Name) and b.id == "Model") or
               (isinstance(b, ast.Attribute) and b.attr == "Model")
               for b in node.bases):
            django_names.add(name)
    # Inheritance: iterate to a fixpoint so a subclass defined before its
    # parent (or in another file) is still recognized.
    changed = True
    while changed:
        changed = False
        for name, _path, node in classes:
            if name in django_names:
                continue
            if any(isinstance(b, ast.Name) and b.id in django_names for b in node.bases):
                django_names.add(name)
                changed = True
    config_labels: dict[str, tuple[str | None, bool]] = {}
    candidates: dict[str, set[str]] = {}
    for name, path, node in classes:
        meta = _django_meta_values(node)
        db_table = meta.get("db_table")
        if isinstance(db_table, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", db_table):
            candidates.setdefault(name, set()).add(db_table)
            continue
        if name not in django_names:
            continue
        if meta.get("abstract") is True or meta.get("proxy") is True:
            continue
        app_dir = _django_app_dir_for_file(path, app_dirs)
        if app_dir is None:
            continue
        app_label = meta.get("app_label")
        if not isinstance(app_label, str):
            app_label = None
        if app_label is None:
            if app_dir not in config_labels:
                apps_source = code_by_file.get(f"{app_dir}/apps.py")
                config_labels[app_dir] = (
                    _django_config_label(apps_source, app_dir.rsplit("/", 1)[-1])
                    if apps_source is not None else (None, False))
            custom_label, ambiguous = config_labels[app_dir]
            if ambiguous:
                continue
            app_label = custom_label if custom_label is not None else app_dir.rsplit("/", 1)[-1]
        table = f"{app_label}_{name.lower()}"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", table):
            continue
        candidates.setdefault(name, set()).add(table)
    return {name: next(iter(tables)) for name, tables in candidates.items()
            if len(tables) == 1}


_DJANGO_READ_METHODS = {"filter", "exclude", "get", "all"}
_DJANGO_WRITE_METHODS = {"create"}
_DJANGO_TERMINAL_METHODS = {"update": "UPDATE", "delete": "DELETE"}
_DJANGO_OPERATION = {"filter": "SELECT", "exclude": "SELECT", "get": "SELECT",
                     "all": "SELECT", "create": "INSERT",
                     "update": "UPDATE", "delete": "DELETE"}


def _django_objects_base(node: ast.Call) -> tuple[str, str] | None:
    """Return (model, method) for a ``Model.objects.<method>(...)`` call.

    ``Model`` must be a bare name and ``objects`` a literal attribute.
    Anything else (a custom manager, a chained receiver, a non-name
    model) returns None.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = func.value
    if not isinstance(receiver, ast.Attribute) or receiver.attr != "objects":
        return None
    if not isinstance(receiver.value, ast.Name):
        return None
    return receiver.value.id, func.attr


def _extract_python(source: str, orm_tables: dict[str, str] | None = None, django_tables: dict[str, str] | None = None) -> list[tuple[int, str, str | None, str, bool, list[str] | None, str | None]]:
    """Extract (line, callee, sql, parser, has_dynamic, orm_models, orm_kind) from one Python file.

    ``orm_models`` is the list of model class names when the call is a
    ``.query(Model, ...)`` whose arguments are all bare names resolved by
    ``orm_tables`` (``orm_kind`` ``"sqlalchemy"``), or a Django
    ``Model.objects.<method>`` call whose model is resolved by
    ``django_tables`` (``orm_kind`` ``"django"``); otherwise None.
    """
    found: list[tuple[int, str, str | None, str, bool, list[str] | None, str | None]] = []
    consumed: set[int] = set()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        tree = None
    if tree is not None:
        class Visitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                if id(node) in consumed:
                    self.generic_visit(node)
                    return
                if isinstance(func, ast.Attribute) and func.attr in _DB_METHODS:
                    receiver = ast.unparse(func.value) if hasattr(ast, "unparse") else ""
                    orm_models: list[str] | None = None
                    orm_kind: str | None = None
                    if (func.attr == "query" and orm_tables and node.args
                            and all(isinstance(arg, ast.Name) for arg in node.args)):
                        names = [arg.id for arg in node.args]
                        if all(name in orm_tables for name in names):
                            orm_models = names
                            orm_kind = "sqlalchemy"
                    resolved = _resolve_python_sql(node.args[0]) if node.args else None
                    if resolved is None:
                        sql, dynamic = None, False
                    else:
                        sql, dynamic = resolved
                    parser = ("python-ast-literal-expression-dynamic" if dynamic
                              else "python-ast-literal-expression")
                    found.append((node.lineno, receiver + "." + func.attr, sql, parser, dynamic, orm_models, orm_kind))
                elif isinstance(func, ast.Attribute) and func.attr in _DJANGO_TERMINAL_METHODS and isinstance(func.value, ast.Call):
                    # Chained .update()/.delete(): walk down to the base
                    # Model.objects.<read method> call and report the
                    # terminal operation once, consuming the base call.
                    base = func.value
                    while isinstance(base.func, ast.Attribute) and isinstance(base.func.value, ast.Call):
                        base = base.func.value
                    base_info = _django_objects_base(base)
                    if base_info is not None and base_info[1] in _DJANGO_READ_METHODS:
                        consumed.add(id(base))
                        model = base_info[0]
                        receiver = ast.unparse(func.value) if hasattr(ast, "unparse") else ""
                        if django_tables and model in django_tables:
                            found.append((node.lineno, receiver + "." + func.attr, None,
                                          "python-ast-literal-expression", False, [model], "django"))
                        else:
                            found.append((node.lineno, receiver + "." + func.attr, None,
                                          "python-ast-literal-expression", False, None, None))
                elif isinstance(func, ast.Attribute):
                    base_info = _django_objects_base(node)
                    if base_info is not None and base_info[1] in _DJANGO_READ_METHODS | _DJANGO_WRITE_METHODS:
                        model = base_info[0]
                        receiver = ast.unparse(func.value) if hasattr(ast, "unparse") else ""
                        if django_tables and model in django_tables:
                            found.append((node.lineno, receiver + "." + func.attr, None,
                                          "python-ast-literal-expression", False, [model], "django"))
                        else:
                            found.append((node.lineno, receiver + "." + func.attr, None,
                                          "python-ast-literal-expression", False, None, None))
                self.generic_visit(node)
        Visitor().visit(tree)
        return found
    # The file does not parse as a whole: fall back to the old per-line
    # scan so single-line calls in an otherwise broken file still produce
    # records with the previous parser label.
    for line_no, line in enumerate(source.splitlines(), 1):
        try:
            line_tree = ast.parse(line.lstrip())
        except (SyntaxError, ValueError):
            continue
        if line_tree is None:
            continue
        class LineVisitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in _DB_METHODS:
                    receiver = ast.unparse(func.value) if hasattr(ast, "unparse") else ""
                    sql = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else None
                    found.append((line_no, receiver + "." + func.attr, sql, "python-ast-single-line", False, None, None))
                self.generic_visit(node)
        LineVisitor().visit(line_tree)
    return found


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    return pos


def _skip_js_string(text: str, pos: int) -> int | None:
    """Skip one JS string literal starting at pos; return the end index or None."""
    if pos >= len(text):
        return None
    quote = text[pos]
    if quote in ("'", '"'):
        i = pos + 1
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                return i + 1
            if ch == "\n":
                return None
            i += 1
        return None
    if quote == "`":
        i = pos + 1
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                return i + 1
            if ch == "$" and i + 1 < len(text) and text[i + 1] == "{":
                depth = 1
                j = i + 2
                while j < len(text) and depth > 0:
                    c = text[j]
                    if c in ("'", '"', "`"):
                        nxt = _skip_js_string(text, j)
                        if nxt is None:
                            return None
                        j = nxt
                        continue
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    j += 1
                if depth != 0:
                    return None
                i = j
                continue
            i += 1
        return None
    return None


def _parse_js_quoted(text: str, pos: int) -> tuple[str, bool, int] | None:
    """Parse a single/double-quoted JS string at pos; return (text, False, end)."""
    quote = text[pos]
    end = _skip_js_string(text, pos)
    if end is None:
        return None
    raw = text[pos + 1:end - 1]
    # A backslash-newline is a JS line continuation; drop both characters.
    raw = raw.replace("\\\r\n", "").replace("\\\n", "")
    try:
        value = ast.literal_eval(quote + raw + quote)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, str):
        return None
    # In '...'/"..." there is no interpolation: '${' is literal text.
    return value, False, end


def _parse_js_template(text: str, pos: int) -> tuple[str, bool, int] | None:
    """Parse a template literal at pos; interpolations become '?' placeholders."""
    parts: list[str] = []
    buf: list[str] = []
    dynamic = False
    i = pos + 1
    while i < len(text):
        ch = text[i]
        if ch == "`":
            parts.append("".join(buf))
            return "".join(parts), dynamic, i + 1
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "`":
                buf.append("`")
            elif nxt == "$":
                buf.append("$")
            elif nxt == "\\":
                buf.append("\\")
            elif nxt == "n":
                buf.append("\n")
            elif nxt == "r":
                buf.append("\r")
            elif nxt == "t":
                buf.append("\t")
            else:
                # Any other escape is left verbatim; it can only land in a
                # position where the SQL parse fails closed to UNKNOWN.
                buf.append(ch)
                buf.append(nxt)
            i += 2
            continue
        if ch == "$" and i + 1 < len(text) and text[i + 1] == "{":
            depth = 1
            j = i + 2
            while j < len(text) and depth > 0:
                c = text[j]
                if c in ("'", '"', "`"):
                    nxt = _skip_js_string(text, j)
                    if nxt is None:
                        return None
                    j = nxt
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                j += 1
            if depth != 0:
                return None
            parts.append("".join(buf))
            buf = []
            parts.append("?")
            dynamic = True
            i = j
            continue
        buf.append(ch)
        i += 1
    return None


def _parse_js_string_term(text: str, pos: int) -> tuple[str, bool, int] | None:
    if pos >= len(text):
        return None
    if text[pos] in ("'", '"'):
        return _parse_js_quoted(text, pos)
    if text[pos] == "`":
        return _parse_js_template(text, pos)
    return None


def _parse_js_string_expr(text: str, pos: int) -> tuple[str, bool, int] | None:
    """Parse a JS string expression (terms joined by '+') at pos."""
    parts: list[str] = []
    dynamic = False
    pos = _skip_ws(text, pos)
    while True:
        term = _parse_js_string_term(text, pos)
        if term is None:
            return None
        term_text, term_dynamic, pos = term
        parts.append(term_text)
        dynamic = dynamic or term_dynamic
        pos = _skip_ws(text, pos)
        if pos < len(text) and text[pos] == "+":
            pos = _skip_ws(text, pos + 1)
            continue
        return "".join(parts), dynamic, pos


def _extract_js(source: str) -> list[tuple[int, str, str | None, str, bool, list[str] | None, str | None]]:
    """Extract (line, callee, sql, parser, has_dynamic, orm_models, orm_kind) from one JS/TS file.

    JS/TS has no ORM model mapping in this increment, so ``orm_models``
    and ``orm_kind`` are always None here.
    """
    found: list[tuple[int, str, str | None, str, bool, list[str] | None, str | None]] = []
    for match in _JS_CALL.finditer(source):
        receiver, method = match.group(1), match.group(2)
        line_no = source.count("\n", 0, match.start()) + 1
        parsed = _parse_js_string_expr(source, match.end())
        if parsed is None:
            found.append((line_no, receiver + "." + method, None,
                          "js-ts-literal-expression", False, None, None))
        else:
            sql, dynamic, _end = parsed
            parser = ("js-ts-literal-expression-dynamic" if dynamic
                      else "js-ts-literal-expression")
            found.append((line_no, receiver + "." + method, sql, parser, dynamic, None, None))
    return found


def extract_database_accesses(code_by_file: dict[str, str]) -> list[dict[str, Any]]:
    """Extract SQL calls whose statement is a literal expression; preserve UNKNOWN otherwise.

    A literal holding several statements separated by top-level semicolons
    yields one record per SELECT/INSERT/UPDATE/DELETE statement (same
    source line); other fragments yield no record. A call with no DML
    fragment keeps today's single UNKNOWN record, parsed from the whole
    text, so the fail-closed shape is unchanged. A SQLAlchemy
    ``.query(Model, ...)`` call whose arguments are all bare model names
    resolved by the project's own ``__tablename__`` map yields one SELECT
    record with ``parse_status`` ``ORM_MODEL`` instead. A Django
    ``Model.objects.filter/get/exclude/all`` call resolved by the project's
    own ``Meta.db_table`` map yields one SELECT record the same way,
    ``.create`` yields INSERT, and a chained ``.update`` / ``.delete``
    yields UPDATE / DELETE; an unresolvable model leaves the call UNKNOWN.
    """
    records: list[dict[str, Any]] = []
    orm_tables = orm_model_tables(code_by_file)
    django_tables = django_model_tables(code_by_file)
    for path, source in sorted(code_by_file.items()):
        if path.lower().endswith(".py"):
            found = _extract_python(source, orm_tables, django_tables)
        else:
            found = _extract_js(source)
        for line_no, callee, sql, parser, dynamic, orm_models, orm_kind in found:
            if orm_models is not None:
                tables_map = django_tables if orm_kind == "django" else orm_tables
                method = callee.rsplit(".", 1)[-1]
                operation = _DJANGO_OPERATION.get(method, "SELECT") if orm_kind == "django" else "SELECT"
                records.append({
                    "source": path, "line": line_no, "callee": callee,
                    "access_method": method,
                    "operation": operation,
                    "tables": sorted({tables_map[name] for name in orm_models}),
                    "columns": [], "parse_status": "ORM_MODEL",
                    "orm_model": sorted(orm_models),
                    "parser": parser, "has_dynamic_parts": dynamic,
                    "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED",
                })
                continue
            if isinstance(sql, str):
                statements = _split_top_level_statements(sql)
                dml = [s for s in statements if _DML_FIRST.match(s)]
                parsed_list = [_parse_sql(s) for s in dml] if dml else [_parse_sql(sql)]
            else:
                parsed_list = [{
                    "operation": "UNKNOWN", "tables": [], "columns": [], "parse_status": "UNKNOWN"
                }]
            for parsed in parsed_list:
                records.append({
                    "source": path, "line": line_no, "callee": callee,
                    "access_method": callee.rsplit(".", 1)[-1],
                    **parsed, "parser": parser, "has_dynamic_parts": dynamic,
                    "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED",
                })
    return records
