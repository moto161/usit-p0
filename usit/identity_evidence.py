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
    """Extract source, request-controlled identity, owner comparison and direct SQL binding."""
    records: list[dict[str, Any]] = []
    for path, source in sorted(code_by_file.items()):
        for line_no, line in enumerate(source.splitlines(), 1):
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
                kinds.append(("authenticated_identity_source", {
                    "identity_expressions": trusted,
                }))
            if request_values:
                kinds.append(("request_controlled_identity_input", {
                    "request_expressions": request_values,
                }))
            if comparisons:
                kinds.append(("resource_owner_comparison", {
                    "comparisons": comparisons,
                }))
            if _DB_CALL.search(line) and _OWNER_PREDICATE.search(line):
                if trusted:
                    kinds.append(("direct_authenticated_identity_to_sql_owner_binding", {
                        "identity_expressions": trusted,
                        "resource_fields": owner_fields,
                        "binding_status": "SAME_LINE_PATTERN_ONLY",
                    }))
                if request_values:
                    kinds.append(("direct_request_identity_to_sql_owner_binding", {
                        "request_expressions": request_values,
                        "resource_fields": owner_fields,
                        "binding_status": "SAME_LINE_PATTERN_ONLY",
                    }))
            for kind, details in kinds:
                row = {
                    "evidence_id": "",
                    "kind": kind,
                    "source": path,
                    "line": line_no,
                    "details": details,
                    "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED",
                    "scope": "SAME_LINE_PATTERN",
                }
                row["evidence_id"] = _id(row)
                records.append(row)
    records.sort(key=lambda item: (item["source"], item["line"], item["kind"], item["evidence_id"]))
    return records
