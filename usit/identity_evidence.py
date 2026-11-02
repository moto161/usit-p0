"""Conservative identity-to-resource data-flow evidence.

Only same-line, syntactically explicit patterns are emitted. This is not a
general taint engine and never proves runtime identity or authorization.
"""
from __future__ import annotations

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
            kinds: list[tuple[str, dict[str, Any]]] = []
            if trusted:
                kinds.append(("authenticated_identity_source", {"identity_expressions": trusted}))
            if request_values:
                kinds.append(("request_controlled_identity_input", {"request_expressions": request_values}))
            if comparisons:
                kinds.append(("resource_owner_comparison", {"comparisons": comparisons}))

            sink = bool(_DB_CALL.search(line) and _OWNER_PREDICATE.search(line))
            if sink:
                if trusted:
                    kinds.append(("direct_authenticated_identity_to_sql_owner_binding", {
                        "identity_expressions": trusted, "resource_fields": owner_fields,
                        "binding_status": "SAME_LINE_PATTERN_ONLY",
                    }))
                if request_values:
                    kinds.append(("direct_request_identity_to_sql_owner_binding", {
                        "request_expressions": request_values, "resource_fields": owner_fields,
                        "binding_status": "SAME_LINE_PATTERN_ONLY",
                    }))
                # Resolve only identifiers in the SQL call's parameter expression.
                # We intentionally do not infer aliases from arbitrary expressions.
                call_start = _DB_CALL.search(line).start()
                parameter_text = line[call_start:]
                used_names = set(identifier.findall(parameter_text))
                for name in sorted(used_names):
                    origin = aliases.get(name)
                    if not origin:
                        continue
                    if origin["kind"] == "trusted":
                        kinds.append(("direct_authenticated_identity_to_sql_owner_binding", {
                            "identity_expressions": [origin["expression"]],
                            "resource_fields": owner_fields,
                            "binding_status": "INTRAPROCEDURAL_LINEAR_ASSIGNMENT",
                            "propagation": [{"path": path, "line": origin["line"], "variable": name},
                                            {"path": path, "line": line_no, "sink": "SQL_OWNER_PREDICATE"}],
                        }))
                    elif origin["kind"] == "request":
                        kinds.append(("direct_request_identity_to_sql_owner_binding", {
                            "request_expressions": [origin["expression"]],
                            "resource_fields": owner_fields,
                            "binding_status": "INTRAPROCEDURAL_LINEAR_ASSIGNMENT",
                            "propagation": [{"path": path, "line": origin["line"], "variable": name},
                                            {"path": path, "line": line_no, "sink": "SQL_OWNER_PREDICATE"}],
                        }))

            for kind, details in kinds:
                scope = "INTRAPROCEDURAL_LINEAR_ASSIGNMENT" if details.get("binding_status") == "INTRAPROCEDURAL_LINEAR_ASSIGNMENT" else "SAME_LINE_PATTERN"
                row = {
                    "evidence_id": "", "kind": kind, "source": path, "line": line_no,
                    "details": details, "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED", "scope": scope,
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
                    aliases[name] = {"kind": "trusted", "expression": rhs, "line": line_no}
                elif _REQUEST.fullmatch(rhs):
                    aliases[name] = {"kind": "request", "expression": rhs, "line": line_no}
                elif re.fullmatch(r"[A-Za-z_$][\w$]*", rhs) and rhs in aliases:
                    aliases[name] = {**aliases[rhs], "line": line_no}
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
