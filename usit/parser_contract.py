"""Common, conservative contract for language parser observations.

The contract is additive. ``adapt_graph_v1`` emits records alongside the
existing usit-graph-v1 representation; it does not reinterpret legacy fields.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONTRACT_VERSION = "usit-parser-result-v1"
RESOLUTION_STATUSES = frozenset({
    "RESOLVED", "UNRESOLVED", "AMBIGUOUS", "DYNAMIC", "EXTERNAL", "UNKNOWN",
})

# These declarations describe what this installation can prove. File suffixes
# remain discovery hints and never imply semantic parsing support.
CAPABILITIES: dict[str, dict[str, Any]] = {
    "Python": {
        "parser_name": "python-ast-project",
        "parser_version": "stdlib-ast",
        "level": "PARTIAL_SEMANTIC",
        "supported": ["syntax symbols", "explicit imports", "conservative project-local calls"],
        "unsupported": ["runtime mutation", "reflection", "decorator effects", "star-import expansion", "conditional import certainty"],
        "degradation": "Syntax or encoding errors remain diagnostic UNKNOWN; no target code is executed.",
    },
    "JavaScript": {
        "parser_name": "typescript-compiler-api",
        "parser_version": "6.0.3",
        "level": "PARTIAL_SEMANTIC",
        "supported": ["JavaScript AST", "explicit imports/exports", "project-local symbol and call resolution", "JSX/MJS/CJS syntax parsing"],
        "unsupported": ["runtime evaluation", "reflection", "dynamic computed dispatch", "execution-based module behavior"],
        "degradation": "Without Node or the exact project-local TypeScript package, all semantic claims remain UNKNOWN.",
    },
    "JavaScript/JSX": {
        "parser_name": "typescript-compiler-api", "parser_version": "6.0.3", "level": "PARTIAL_SEMANTIC",
        "supported": ["JSX AST", "explicit imports/exports", "project-local symbol and call resolution"],
        "unsupported": ["runtime evaluation", "reflection", "dynamic computed dispatch"],
        "degradation": "Without Node or the exact project-local TypeScript package, semantic relationships are UNKNOWN.",
    },
    "TypeScript": {
        "parser_name": "typescript-compiler-api", "parser_version": "6.0.3", "level": "PARTIAL_SEMANTIC",
        "supported": ["TypeScript AST and syntax diagnostics", "explicit imports/exports", "project-local symbol and call resolution", "tsconfig paths and compiler module resolution"],
        "unsupported": ["runtime evaluation", "reflection", "dynamic computed dispatch", "runtime re-binding certainty"],
        "degradation": "Without Node or the exact project-local TypeScript package, semantic relationships are UNKNOWN.",
    },
    "TypeScript/JSX": {
        "parser_name": "typescript-compiler-api", "parser_version": "6.0.3", "level": "PARTIAL_SEMANTIC",
        "supported": ["TSX AST and syntax diagnostics", "explicit imports/exports", "project-local symbol and call resolution"],
        "unsupported": ["runtime evaluation", "reflection", "dynamic computed dispatch", "runtime re-binding certainty"],
        "degradation": "Without Node or the exact project-local TypeScript package, semantic relationships are UNKNOWN.",
    },
    "Java": {
        "parser_name": "heuristic-candidate", "parser_version": "1", "level": "DISCOVERY_ONLY",
        "supported": ["file discovery", "low-confidence textual candidates"],
        "unsupported": ["AST semantics", "package/module resolution", "call resolution"],
        "degradation": "All semantic relationships are UNKNOWN.",
    },
    "Kotlin": {
        "parser_name": "heuristic-candidate", "parser_version": "1", "level": "DISCOVERY_ONLY",
        "supported": ["file discovery", "low-confidence textual candidates"],
        "unsupported": ["AST semantics", "package/module resolution", "call resolution"],
        "degradation": "All semantic relationships are UNKNOWN.",
    },
}


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return contract violations; never repairs or upgrades records."""
    required = {
        "language", "file_path", "parser_name", "parser_version", "node_type",
        "qualified_name", "source_location", "relation_type", "resolution_status",
        "confidence", "evidence", "diagnostics",
    }
    problems = [f"missing:{key}" for key in sorted(required - record.keys())]
    if record.get("resolution_status") not in RESOLUTION_STATUSES:
        problems.append("invalid:resolution_status")
    if not isinstance(record.get("source_location"), dict):
        problems.append("invalid:source_location")
    elif record["source_location"].get("line") is not None and (
        not isinstance(record["source_location"]["line"], int)
        or record["source_location"]["line"] < 1
    ):
        problems.append("invalid:source_location.line")
    confidence = record.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        problems.append("invalid:confidence")
    if not isinstance(record.get("evidence"), dict) or not record.get("evidence"):
        problems.append("invalid:evidence")
    if not isinstance(record.get("diagnostics"), list):
        problems.append("invalid:diagnostics")
    return problems


def _classify_graph_evidence(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Classify a graph node/edge ``evidence`` field without fabricating content.

    Returns ``(evidence_mapping, note)`` where ``note`` is ``None`` only for a
    real, non-empty Mapping:

    * real, non-empty ``Mapping`` -> ``(dict(raw), None)`` — trusted as-is.
    * ``None`` / absent           -> ``({}, "missing_evidence")``.
    * empty ``Mapping``           -> ``({}, "empty_evidence")``.
    * non-``Mapping`` (str/list/) -> ``({}, "malformed_evidence:<type>")``.

    A synthetic placeholder (e.g. ``{"graph_node_id": id}``) is NEVER emitted:
    doing so would let a node with no real evidence masquerade as evidence and
    be promoted downstream. Missing/empty/malformed evidence stays an empty
    mapping so the projection classifies it as non-candidate (``UNKNOWN``).
    Node identity remains traceable via the record's ``graph_ref`` and the
    diagnostic ``note``. This never raises, even for non-Mapping input.
    """
    if raw is None:
        return {}, "missing_evidence"
    if not isinstance(raw, Mapping):
        return {}, f"malformed_evidence:{type(raw).__name__}"
    if not raw:
        return {}, "empty_evidence"
    return dict(raw), None


def adapt_graph_v1(graph: dict[str, Any]) -> dict[str, Any]:
    """Expose graph-v1 nodes/edges using the common contract without mutation.

    Evidence is never fabricated: a node/edge lacking a real, non-empty Mapping
    ``evidence`` field is emitted with an empty evidence mapping plus a
    diagnostic note, so downstream projection classifies it as non-candidate
    (``UNKNOWN``) instead of promoting a synthetic ``graph_node_id`` placeholder
    to a success. Node identity remains traceable via ``graph_ref``.
    """
    records: list[dict[str, Any]] = []
    nodes_by_id = {node.get("id"): node for node in graph.get("nodes", [])}
    for collection, is_edge in ((graph.get("nodes", []), False), (graph.get("edges", []), True)):
        for item in collection:
            evidence, evidence_note = _classify_graph_evidence(item.get("evidence"))
            path = evidence.get("path") or ""
            source_node = nodes_by_id.get(item.get("source"), {}) if is_edge else item
            if is_edge and not path:
                src_evidence, _ = _classify_graph_evidence(source_node.get("evidence"))
                path = src_evidence.get("path") or ""
            status = item.get("resolution_status", "UNKNOWN")
            # v1 resolved boolean is intentionally not sufficient evidence.
            if status not in RESOLUTION_STATUSES:
                status = "UNKNOWN"
            diagnostics: list[Any] = []
            if evidence_note is not None:
                diagnostics.append({"code": evidence_note, "graph_node_id": item.get("id")})
            records.append({
                "language": item.get("language") or evidence.get("language") or source_node.get("language") or "UNKNOWN",
                "file_path": path,
                "parser_name": item.get("method", "graph-v1-adapter"),
                "parser_version": "graph-v1",
                "node_type": item.get("type", "relation" if is_edge else "unknown"),
                "qualified_name": item.get("qualified_name") or item.get("label") or item.get("type", "unknown"),
                "source_location": {"file_path": path, "line": evidence.get("line"), "column": evidence.get("column")},
                "relation_type": item.get("type") if is_edge else None,
                "resolution_status": status,
                "confidence": {"high": 0.9, "medium": 0.6, "low": 0.25}.get(item.get("confidence"), 0.0),
                "evidence": evidence,
                "diagnostics": diagnostics,
                "graph_ref": item.get("id") or {"source": item.get("source"), "target": item.get("target")},
            })
    return {"contract_version": CONTRACT_VERSION, "source_schema": graph.get("schema_version"), "records": records}


def parser_capabilities() -> dict[str, Any]:
    return {"contract_version": CONTRACT_VERSION, "languages": CAPABILITIES}
