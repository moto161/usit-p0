"""Conservative identity-to-resource data-flow evidence.

Only same-line, syntactically explicit patterns are emitted. This is not a
general taint engine and never proves runtime identity or authorization.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

_TRUSTED = re.compile(
    r"\b(?:req|request)\.(?:user|auth\.user)\.(?:id|userId)\b"
    r"|\b(?:session\.user|ctx\.state\.user|currentUser|auth)\.(?:id|userId)\b"
)
_REQUEST = re.compile(
    r"\b(?:req|request)\.(?:body|params|query)\."
    r"(?:user_?id|owner_?id|account_?id|created_?by)\b"
)
_OWNER_FIELD = re.compile(r"\b(owner_?id|user_?id|created_?by|account_?id)\b", re.I)
_DB_CALL = re.compile(r"\.(?:execute|query|raw)\s*\(")
_OWNER_PREDICATE = re.compile(
    r"\b(?:owner_?id|user_?id|created_?by|account_?id)\s*=\s*(?:\?|\$\d+|:[A-Za-z_]\w*)",
    re.I,
)
_COMPARE = re.compile(
    r"\b(owner_?id|user_?id|created_?by|account_?id)\s*(?:===|==|!==|!=)\s*"
    r"(req\.(?:user|auth\.user)\.(?:id|userId)|request\.(?:user|auth\.user)\.(?:id|userId)|"
    r"session\.user\.(?:id|userId)|ctx\.state\.user\.(?:id|userId)|currentUser\.(?:id|userId))\b",
    re.I,
)


def _id(record: Mapping[str, Any]) -> str:
    raw = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "identity-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]



def _call_parameter_code(line: str, call_start: int) -> str:
    """Return code-only text from positional arguments after the SQL string argument.

    String literals and comments are excluded. Nested arrays/objects/calls are
    retained. Unsupported or unbalanced calls return an empty string.
    """
    stack = ["("]
    args: list[str] = []
    start = call_start + 1
    quote: str | None = None
    escaped = False
    index = start
    while index < len(line):
        char = line[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'} or char == chr(96):
            quote = char
            index += 1
            continue
        if line.startswith("//", index) or char == "#":
            break
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack:
                return ""
            expected = {")": "(", "]": "[", "}": "{"}[char]
            if stack[-1] != expected:
                return ""
            stack.pop()
            if not stack:
                args.append(line[start:index])
                break
        elif char == "," and len(stack) == 1 and stack[0] == "(":
            args.append(line[start:index])
            start = index + 1
        index += 1
    if stack or len(args) < 2:
        return ""

    code_parts: list[str] = []
    for argument in args[1:]:
        quote = None
        escaped = False
        index = 0
        while index < len(argument):
            char = argument[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                code_parts.append(" ")
                index += 1
                continue
            if char in {"'", '"'} or char == chr(96):
                quote = char
                code_parts.append(" ")
                index += 1
                continue
            if argument.startswith("//", index) or char == "#":
                break
            code_parts.append(char)
            index += 1
    return "".join(code_parts)

def _call_parameter_expressions(line: str, call_start: int) -> list[str]:
    """Return cleaned positional binding arguments after the SQL string argument."""
    stack = ["("]
    args: list[str] = []
    start = call_start + 1
    quote: str | None = None
    escaped = False
    index = start
    while index < len(line):
        char = line[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'} or char == chr(96):
            quote = char
            index += 1
            continue
        if line.startswith("//", index) or char == "#":
            break
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack:
                return []
            expected = {")": "(", "]": "[", "}": "{"}[char]
            if stack[-1] != expected:
                return []
            stack.pop()
            if not stack:
                args.append(line[start:index])
                break
        elif char == "," and len(stack) == 1 and stack[0] == "(":
            args.append(line[start:index])
            start = index + 1
        index += 1
    if stack or len(args) < 2:
        return []

    cleaned: list[str] = []
    for argument in args[1:]:
        output: list[str] = []
        quote = None
        escaped = False
        index = 0
        while index < len(argument):
            char = argument[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\\\":
                    escaped = True
                elif char == quote:
                    quote = None
                output.append(" ")
                index += 1
                continue
            if char in {"'", '"'} or char == chr(96):
                quote = char
                output.append(" ")
                index += 1
                continue
            if argument.startswith("//", index) or char == "#":
                break
            output.append(char)
            index += 1
        value = "".join(output).strip()
        if value:
            cleaned.append(value)
    return cleaned


def _python_function_owners(path: str, source: str) -> dict[int, str]:
    """Map source lines to the innermost AST-proven Python function symbol.

    Non-Python files and syntax-invalid Python deliberately produce no owner.
    """
    if not path.lower().endswith(".py"):
        return {}
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return {}
    scopes: list[tuple[int, int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(node, "end_lineno", None)
            if isinstance(end_line, int) and end_line >= node.lineno:
                scopes.append((node.lineno, end_line, end_line - node.lineno,
                               f"symbol:{path}#{node.name}:{node.lineno}"))
    owners: dict[int, str] = {}
    for start, end, _span, symbol_id in sorted(scopes, key=lambda item: (item[2], item[0], item[3])):
        for line_no in range(start, end + 1):
            owners.setdefault(line_no, symbol_id)
    return owners


def extract_identity_evidence(code_by_file: dict[str, str]) -> list[dict[str, Any]]:
    """Extract identity evidence plus bounded, same-scope local-variable propagation.

    Propagation is intentionally limited to straight-line assignments within one
    brace/indent scope. Entering or leaving a block resets aliases; function and
    class declarations reset them too. This is not interprocedural taint analysis.
    """
    records: list[dict[str, Any]] = []
    assignment = re.compile(
        r"^\s*(?:(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*=\s*(.*?)\s*;?\s*$"
    )
    identifier = re.compile(r"\b[A-Za-z_$][\w$]*\b")
    for path, source in sorted(code_by_file.items()):
        function_owners = _python_function_owners(path, source)
        aliases: dict[str, dict[str, Any]] = {}
        brace_depth = 0
        previous_depth = 0
        previous_indent = 0
        python_scope_indent: int | None = None
        is_python = path.lower().endswith(".py")
        for line_no, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if is_python:
                if re.match(r"(?:async\s+)?(?:def|class)\s+", stripped):
                    aliases.clear()
                    python_scope_indent = indent
                elif python_scope_indent is not None and stripped and indent <= python_scope_indent:
                    aliases.clear()
                    python_scope_indent = None
                elif stripped and indent < previous_indent:
                    aliases.clear()
                current_scope = python_scope_indent if python_scope_indent is not None else 0
            else:
                if brace_depth != previous_depth:
                    aliases.clear()
                if re.search(r"\b(?:function|class)\b|=>\s*\{", line):
                    aliases.clear()
                current_scope = brace_depth

            trusted = sorted(set(match.group(0) for match in _TRUSTED.finditer(line)))
            request_values = sorted(set(match.group(0) for match in _REQUEST.finditer(line)))
            owner_fields = sorted(set(match.group(1).lower() for match in _OWNER_FIELD.finditer(line)))
            comparisons = []
            for match in _COMPARE.finditer(line):
                comparisons.append({
                    "resource_field": match.group(1).lower(),
                    "identity_expression": match.group(2),
                    "operator": re.search(r"(?:===|==|!==|!=)", match.group(0)).group(0),
                })
            assignment_match = assignment.match(line)
            assigned_variable = None
            if assignment_match:
                candidate_name, candidate_rhs = assignment_match.groups()
                candidate_rhs = candidate_rhs.strip().rstrip(";")
                if _TRUSTED.fullmatch(candidate_rhs) or _REQUEST.fullmatch(candidate_rhs):
                    assigned_variable = candidate_name

            kinds: list[tuple[str, dict[str, Any]]] = []
            if trusted:
                kinds.append(("authenticated_identity_source", {"identity_expressions": trusted}))
            if request_values:
                kinds.append(("request_controlled_identity_input", {"request_expressions": request_values}))
            if comparisons:
                kinds.append(("resource_owner_comparison", {"comparisons": comparisons}))

            sink = bool(_DB_CALL.search(line) and _OWNER_PREDICATE.search(line))
            if sink:
                call_match = _DB_CALL.search(line)
                parameter_code = _call_parameter_code(line, call_match.end() - 1)
                parameter_expressions = _call_parameter_expressions(line, call_match.end() - 1)
                parameter_trusted = sorted(set(
                    match.group(0) for match in _TRUSTED.finditer(parameter_code)
                ))
                parameter_request = sorted(set(
                    match.group(0) for match in _REQUEST.finditer(parameter_code)
                ))
                # Record only binding arguments that are exactly a bare identifier
                # (optionally wrapped as a one-item list/tuple). Compound expressions
                # are deliberately excluded from interprocedural matching.
                binding_parameters = sorted({
                    match.group(1)
                    for expression in parameter_expressions
                    for match in [re.fullmatch(
                        r"[\\[\\(\\s]*([A-Za-z_$][\\w$]*)[\\]\\),\\s]*",
                        expression,
                    )]
                    if match
                })
                if binding_parameters:
                    kinds.append(("sql_owner_parameter_binding", {
                        "resource_fields": owner_fields,
                        "binding_parameters": binding_parameters,
                        "binding_status": "EXACT_BARE_IDENTIFIER_ARGUMENT",
                    }))
                if parameter_trusted:
                    kinds.append(("direct_authenticated_identity_to_sql_owner_binding", {
                        "identity_expressions": parameter_trusted, "resource_fields": owner_fields,
                        "binding_status": "SAME_LINE_PARAMETER_EXPRESSION",
                    }))
                if parameter_request:
                    kinds.append(("direct_request_identity_to_sql_owner_binding", {
                        "request_expressions": parameter_request, "resource_fields": owner_fields,
                        "binding_status": "SAME_LINE_PARAMETER_EXPRESSION",
                    }))
                # Resolve aliases only in non-literal positional arguments after
                # the SQL argument; names in SQL text or trailing expressions do not count.
                used_names = set(identifier.findall(parameter_code))
                for name in sorted(used_names):
                    origin = aliases.get(name)
                    if not origin:
                        continue
                    if origin["kind"] == "trusted":
                        kinds.append(("direct_authenticated_identity_to_sql_owner_binding", {
                            "identity_expressions": [origin["expression"]],
                            "resource_fields": owner_fields,
                            "binding_status": "INTRAPROCEDURAL_LINEAR_ASSIGNMENT",
                            "propagation": origin["propagation"] + [
                                {"path": path, "line": line_no, "sink": "SQL_OWNER_PREDICATE"}
                            ],
                        }))
                    elif origin["kind"] == "request":
                        kinds.append(("direct_request_identity_to_sql_owner_binding", {
                            "request_expressions": [origin["expression"]],
                            "resource_fields": owner_fields,
                            "binding_status": "INTRAPROCEDURAL_LINEAR_ASSIGNMENT",
                            "propagation": origin["propagation"] + [
                                {"path": path, "line": line_no, "sink": "SQL_OWNER_PREDICATE"}
                            ],
                        }))

            for kind, details in kinds:
                if assigned_variable and kind in {
                    "authenticated_identity_source", "request_controlled_identity_input"
                }:
                    details["source_variable"] = assigned_variable
                scope = "INTRAPROCEDURAL_LINEAR_ASSIGNMENT" if details.get("binding_status") == "INTRAPROCEDURAL_LINEAR_ASSIGNMENT" else "SAME_LINE_PATTERN"
                row = {
                    "evidence_id": "", "kind": kind, "source": path, "line": line_no,
                    "details": details, "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED", "scope": scope,
                    "owner_symbol_id": function_owners.get(line_no),
                    "owner_resolution_status": "RESOLVED" if line_no in function_owners else "UNKNOWN",
                }
                row["evidence_id"] = _id(row)
                records.append(row)

            # Track only simple direct assignments. Reassignment to an unknown
            # expression invalidates the alias instead of retaining stale identity.
            match = assignment.match(line)
            if match and not sink:
                name, rhs = match.groups()
                rhs = rhs.strip().rstrip(";")
                if _TRUSTED.fullmatch(rhs):
                    aliases[name] = {"kind": "trusted", "expression": rhs, "line": line_no,
                                     "propagation": [{"path": path, "line": line_no, "variable": name}]}
                elif _REQUEST.fullmatch(rhs):
                    aliases[name] = {"kind": "request", "expression": rhs, "line": line_no,
                                     "propagation": [{"path": path, "line": line_no, "variable": name}]}
                elif re.fullmatch(r"[A-Za-z_$][\w$]*", rhs) and rhs in aliases:
                    aliases[name] = {**aliases[rhs],
                                     "propagation": aliases[rhs]["propagation"] + [
                                         {"path": path, "line": line_no, "variable": name}
                                     ]}
                else:
                    aliases.pop(name, None)

            if not is_python:
                previous_depth = brace_depth
                brace_depth += line.count("{") - line.count("}")
                if brace_depth != previous_depth:
                    aliases.clear()
            previous_indent = indent
    records.sort(key=lambda item: (item["source"], item["line"], item["kind"], item["evidence_id"]))
    return records
