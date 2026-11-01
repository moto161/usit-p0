"""Conservative source-line authorization evidence extraction.

This module records explicit guard middleware and named authorization calls.
It does not infer that an endpoint is secure or insecure from absence alone.
"""
from __future__ import annotations

import re
from typing import Any

_ROUTE = re.compile(r"\b(?:router|app)\.(?:get|post|put|delete|patch|all)\s*\(")
_GUARD = re.compile(
    r"\b(authRequired|adminRequired|requireAuth|authenticate|authorize|"
    r"requireRole|requirePermission|hasPermission|canAccess|isOwner|"
    r"checkOwnership|assertOwner)\b"
)
_ROLE_CHECK = re.compile(r"\b(?:req\.)?(?:user|auth|principal)\.role\s*(?:===|==|!==|!=)\s*['\"]([^'\"]+)['\"]")
_OWNER_CHECK = re.compile(r"\b(?:owner_id|user_id|created_by)\s*(?:===|==|!==|!=)\s*(?:req\.)?(?:user\.)?id\b")


def extract_authorization_evidence(code_by_file: dict[str, str]) -> list[dict[str, Any]]:
    """Return line-anchored guard candidates without claiming enforcement."""
    records: list[dict[str, Any]] = []
    for path, source in sorted(code_by_file.items()):
        for line_no, line in enumerate(source.splitlines(), 1):
            names = sorted(set(_GUARD.findall(line)))
            role = _ROLE_CHECK.search(line)
            owner = _OWNER_CHECK.search(line)
            if not names and not role and not owner:
                continue
            is_route = bool(_ROUTE.search(line))
            kind = "route_middleware" if is_route else "authorization_call" if names else "authorization_condition"
            records.append({
                "source": path,
                "line": line_no,
                "kind": kind,
                "guard_names": names,
                "role_value": role.group(1) if role else None,
                "owner_condition": bool(owner),
                "evidence_text": line.strip()[:240],
                "parser": "bounded-source-line-patterns",
                "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
            })
    return records
