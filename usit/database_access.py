"""Conservative, line-bounded database access evidence extraction.

This module never executes SQL or target code. It recognizes only direct SQL
string literals passed to common execute/query/raw methods on a single source
line. Dynamic, concatenated and multiline statements remain UNKNOWN.
"""
from __future__ import annotations

import ast
import re
from typing import Any

_DB_METHODS = {"execute", "query", "raw"}
_SQL_TABLE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.I)
_SQL_SELECT = re.compile(r"^\s*SELECT\s+(.*?)\s+FROM\s+", re.I | re.S)
_SQL_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+[A-Za-z_][A-Za-z0-9_$]*\s*\(([^)]*)\)", re.I | re.S)
_SQL_UPDATE = re.compile(r"^\s*UPDATE\s+[A-Za-z_][A-Za-z0-9_$]*\s+SET\s+(.*?)(?:\s+WHERE\s+|$)", re.I | re.S)


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


def _parse_sql(sql: str) -> dict[str, Any]:
    text = sql.strip()
    if not text or re.search(r"\b(WITH|UNION|INTERSECT|EXCEPT)\b", text, re.I):
        return {"operation": "UNKNOWN", "tables": [], "columns": [], "parse_status": "UNKNOWN"}
    first = re.match(r"^(SELECT|INSERT|UPDATE|DELETE)\b", text, re.I)
    if not first:
        return {"operation": "UNKNOWN", "tables": [], "columns": [], "parse_status": "UNKNOWN"}
    operation = first.group(1).upper()
    tables = sorted(set(m.group(1) for m in _SQL_TABLE.finditer(text)))
    columns: list[str] = []
    if operation == "SELECT":
        match = _SQL_SELECT.match(text)
        if match:
            columns = _simple_columns(match.group(1))
    elif operation == "INSERT":
        match = _SQL_INSERT.match(text)
        if match:
            columns = _simple_columns(match.group(1))
    elif operation == "UPDATE":
        match = _SQL_UPDATE.match(text)
        if match:
            names = re.findall(r"(?:^|,)\s*([A-Za-z_][A-Za-z0-9_$]*)\s*=", match.group(1))
            columns = sorted(set(names))
    status = "RESOLVED" if tables else "UNKNOWN"
    return {"operation": operation, "tables": tables, "columns": columns,
            "parse_status": status}


def extract_database_accesses(code_by_file: dict[str, str]) -> list[dict[str, Any]]:
    """Extract single-line direct-literal SQL calls; preserve UNKNOWN otherwise."""
    records: list[dict[str, Any]] = []
    for path, source in sorted(code_by_file.items()):
        for line_no, line in enumerate(source.splitlines(), 1):
            found: list[tuple[str, str | None, str]] = []
            if path.lower().endswith(".py"):
                try:
                    tree = ast.parse(line.lstrip())
                except (SyntaxError, ValueError):
                    tree = None
                if tree is not None:
                    class Visitor(ast.NodeVisitor):
                        def visit_Call(self, node: ast.Call) -> None:
                            func = node.func
                            if isinstance(func, ast.Attribute) and func.attr in _DB_METHODS:
                                receiver = ast.unparse(func.value) if hasattr(ast, "unparse") else ""
                                sql = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else None
                                found.append((receiver + "." + func.attr, sql, "python-ast-single-line"))
                            self.generic_visit(node)
                    Visitor().visit(tree)
                # A recognized execution call split across lines is deliberately
                # not reconstructed; it will be reported as NOT_ANALYZED by limits.
            else:
                pattern = re.compile(r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.(execute|query|raw)\s*\(\s*(?:(['\"])(.*?)\3)?")
                for match in pattern.finditer(line):
                    receiver, method, quote, literal = match.groups()
                    sql = None
                    if quote and literal is not None and "${" not in literal and "\n" not in literal and "\r" not in literal:
                        try:
                            sql = ast.literal_eval(quote + literal + quote)
                        except (SyntaxError, ValueError):
                            sql = None
                    found.append((receiver + "." + method, sql, "js-ts-single-line-literal-or-dynamic"))
            for callee, sql, parser in found:
                parsed = _parse_sql(sql) if isinstance(sql, str) else {
                    "operation": "UNKNOWN", "tables": [], "columns": [], "parse_status": "UNKNOWN"
                }
                records.append({
                    "source": path, "line": line_no, "callee": callee,
                    "access_method": callee.rsplit(".", 1)[-1],
                    **parsed, "parser": parser, "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED",
                })
    return records
