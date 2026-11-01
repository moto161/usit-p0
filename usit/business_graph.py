"""Evidence-preserving projection of business semantics into the USIT graph.

Only exact source-file matches are linked. The projector deliberately does not
invent feature-to-endpoint, endpoint-to-entity, or role-to-permission edges.
Those require explicit evidence or a stronger semantic adapter.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

_SCHEMA = "usit-business-function-graph-v1"
_GRAPH_SCHEMA = "usit-graph-v1"
_ALLOWED_BUSINESS = ("features", "entities", "endpoints")


def _stable_id(kind: str, identity: Mapping[str, Any]) -> str:
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"business:{kind}:{digest}"


def _source_path(record: Mapping[str, Any]) -> str:
    source = record.get("source")
    if not isinstance(source, str):
        return ""
    path = source.strip().replace("\\", "/")
    # Code endpoint records encode source as "relative/path:line". Strip only
    # a numeric trailing line suffix before matching the graph file node.
    path = re.sub(r":([1-9][0-9]*)$", "", path)
    if not path or path.startswith("/") or ":" in path:
        return ""
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return ""
    return path


def _source_line(record: Mapping[str, Any]) -> int | None:
    line = record.get("line")
    if isinstance(line, int) and not isinstance(line, bool) and line > 0:
        return line
    source = record.get("source")
    if isinstance(source, str):
        match = re.search(r":([1-9][0-9]*)$", source.strip())
        if match:
            return int(match.group(1))
    return None


def project_business_graph(graph: Mapping[str, Any],
                           semantics: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copied graph with evidence-linked business candidate nodes.

    Links mean only that a business record was extracted from that exact file.
    They are marked candidate/unverified and must not be interpreted as runtime
    reachability or proof that the described feature is implemented.
    """
    if not isinstance(graph, Mapping) or graph.get("schema_version") != _GRAPH_SCHEMA:
        raise ValueError("graph must use usit-graph-v1")
    if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise ValueError("graph nodes and edges must be lists")
    if not isinstance(semantics, Mapping):
        raise TypeError("semantics must be a mapping")

    result = dict(graph)
    nodes = [dict(item) for item in graph["nodes"] if isinstance(item, Mapping)]
    edges = [dict(item) for item in graph["edges"] if isinstance(item, Mapping)]
    existing_ids = {item.get("id") for item in nodes if isinstance(item.get("id"), str)}
    file_ids: dict[str, str] = {}
    for node in nodes:
        if node.get("type") != "file":
            continue
        evidence = node.get("evidence")
        path = evidence.get("path") if isinstance(evidence, Mapping) else None
        if isinstance(path, str) and path:
            file_ids[path] = node["id"]

    linked_counts = {"features": 0, "entities": 0, "endpoints": 0}
    linked = 0
    unlinked: list[dict[str, str]] = []
    for kind in _ALLOWED_BUSINESS:
        records = semantics.get(kind, [])
        if not isinstance(records, list):
            raise ValueError(f"semantics.{kind} must be a list")
        for raw in records:
            if not isinstance(raw, Mapping):
                unlinked.append({"kind": kind, "reason": "MALFORMED_RECORD"})
                continue
            path = _source_path(raw)
            if not path:
                unlinked.append({"kind": kind, "reason": "INVALID_OR_MISSING_SOURCE"})
                continue
            if path not in file_ids:
                unlinked.append({"kind": kind, "reason": "SOURCE_FILE_NOT_IN_GRAPH", "path": path})
                continue

            if kind == "features":
                label = str(raw.get("name") or "").strip()
                details = {"module": raw.get("module") or "", "status": raw.get("status") or "UNKNOWN",
                           "priority": raw.get("priority") or "UNSPECIFIED"}
            elif kind == "entities":
                label = str(raw.get("name") or "").strip()
                fields = raw.get("fields", [])
                details = {"fields": [f.get("name") for f in fields
                                      if isinstance(f, Mapping) and isinstance(f.get("name"), str)]
                           if isinstance(fields, list) else []}
            else:
                method = str(raw.get("method") or "").strip().upper()
                endpoint = str(raw.get("path") or "").strip()
                label = f"{method} {endpoint}".strip()
                details = {"method": method, "endpoint": endpoint,
                           "code_auth": raw.get("code_auth") or "UNKNOWN"}
            if not label:
                unlinked.append({"kind": kind, "reason": "EMPTY_LABEL", "path": path})
                continue

            line = _source_line(raw)
            identity = {"kind": kind, "path": path, "label": label,
                        "line": line, "details": details}
            node_id = _stable_id(kind, identity)
            if node_id not in existing_ids:
                node = {
                    "id": node_id, "type": "candidate", "label": label,
                    "business_kind": {"features": "feature", "entities": "entity", "endpoints": "endpoint"}[kind],
                    "business_details": details,
                    "evidence": {"path": path, **({"line": line} if line else {}),
                                 "confidence": raw.get("confidence") or "UNKNOWN"},
                    "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED",
                }
                nodes.append(node)
                existing_ids.add(node_id)
                evidence = {"path": path}
                if line:
                    evidence["line"] = line
                edges.append({
                    "source": file_ids[path], "target": node_id, "type": "references",
                    "evidence": evidence, "method": "business-semantics-source-link",
                    "confidence": "medium", "candidate": True, "verified": False,
                    "verification_status": "NOT_VERIFIED",
                })
                linked += 1
            linked_counts[kind] += 1

    result["nodes"] = nodes
    result["edges"] = edges
    result["business_understanding"] = {
        "schema_version": _SCHEMA,
        "status": "PARTIAL_EVIDENCE_LINKS",
        "linked_record_counts": linked_counts,
        "linked_records": linked,
        "unlinked_records": unlinked,
        "limitations": [
            "仅建立业务记录与其精确来源文件之间的证据关联。",
            "未建立功能到接口、接口到数据实体、角色到权限的推断关系。",
            "candidate/verified=false 表示未验证，不代表运行时可达或功能已实现。",
        ],
    }
    return result
