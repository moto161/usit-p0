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



def _collect_call_chains(edges: list[dict[str, Any]], root_symbol_id: str,
                         symbol_ids: set[str], *, max_depth: int = 8,
                         max_chains: int = 100) -> tuple[list[dict[str, Any]], bool]:
    """Expand statically resolved caller→callee evidence with cycle/size guards."""
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        caller = edge.get("caller_symbol_id")
        if edge.get("type") == "calls" and isinstance(caller, str):
            adjacency.setdefault(caller, []).append(edge)

    def sort_key(edge: Mapping[str, Any]) -> tuple[str, int, str]:
        evidence = edge.get("evidence") or {}
        return (
            str(evidence.get("path") or ""),
            int(evidence.get("line") or 0),
            str(edge.get("target") or ""),
        )

    for rows in adjacency.values():
        rows.sort(key=sort_key)

    chains: list[dict[str, Any]] = []
    truncated = False

    def step_for(edge: Mapping[str, Any]) -> dict[str, Any]:
        evidence = edge.get("evidence") or {}
        return {
            "caller_symbol_id": edge.get("caller_symbol_id"),
            "target_id": edge.get("target"),
            "resolution_status": edge.get("resolution_status") or "UNKNOWN",
            "call_site": {"path": evidence.get("path"), "line": evidence.get("line")},
            "target_location": edge.get("target_location"),
            "method": edge.get("method"),
            "confidence": edge.get("confidence"),
        }

    def walk(caller: str, steps: list[dict[str, Any]], visited: set[str]) -> None:
        nonlocal truncated
        outgoing = adjacency.get(caller, [])
        if not outgoing:
            if steps and len(chains) < max_chains:
                chains.append({"steps": steps})
            return
        for edge in outgoing:
            if len(chains) >= max_chains:
                truncated = True
                return
            next_steps = steps + [step_for(edge)]
            target = edge.get("target")
            if edge.get("resolution_status") != "RESOLVED" or target not in symbol_ids:
                chains.append({"steps": next_steps})
                continue
            if target in visited:
                chains.append({"steps": next_steps, "cycle_detected": True})
                continue
            if len(next_steps) >= max_depth:
                has_more = bool(adjacency.get(target))
                chains.append({"steps": next_steps, "depth_limit_reached": has_more})
                truncated = truncated or has_more
                continue
            if adjacency.get(target):
                walk(target, next_steps, visited | {target})
            else:
                chains.append({"steps": next_steps})

    walk(root_symbol_id, [], {root_symbol_id})
    chains.sort(key=lambda chain: tuple(
        (step.get("call_site", {}).get("path") or "",
         step.get("call_site", {}).get("line") or 0,
         step.get("target_id") or "")
        for step in chain["steps"]
    ))
    return chains, truncated


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

    # Only exact, unique function/method declarations in the route's own file
    # are eligible. Imported/member handlers remain UNKNOWN in this slice.
    symbols_by_file_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for node in nodes:
        if node.get("type") not in {"function", "method"}:
            continue
        evidence = node.get("evidence")
        path = evidence.get("path") if isinstance(evidence, Mapping) else None
        label = node.get("label")
        if isinstance(path, str) and isinstance(label, str) and path and label:
            symbols_by_file_name.setdefault((path, label), []).append(node)

    linked_counts = {"features": 0, "entities": 0, "endpoints": 0}
    linked = 0
    unlinked: list[dict[str, str]] = []
    handler_counts = {"RESOLVED": 0, "AMBIGUOUS": 0, "UNKNOWN": 0, "NOT_ANALYZED": 0}
    handler_downstream_calls = 0
    handler_call_chains = 0
    handler_call_chains_truncated = 0
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
                           "code_auth": raw.get("code_auth") or "UNKNOWN",
                           "authorization_status": raw.get("authorization_status") or "UNKNOWN",
                           "handler_reference": raw.get("handler_reference") or ""}
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

            if kind == "endpoints":
                endpoint_node = next((item for item in nodes if item.get("id") == node_id), None)
                handler_ref = raw.get("handler_reference")
                if not isinstance(handler_ref, str) or not handler_ref.strip():
                    handler_counts["NOT_ANALYZED"] += 1
                    if endpoint_node is not None:
                        endpoint_node["handler_association"] = {
                            "reference": None, "resolution_status": "UNKNOWN",
                            "reason": "HANDLER_REFERENCE_NOT_EXTRACTED",
                        }
                    continue
                handler_ref = handler_ref.strip()
                simple_identifier = re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", handler_ref)
                simple_member = re.fullmatch(
                    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+",
                    handler_ref,
                )
                if not simple_identifier and not simple_member:
                    handler_counts["UNKNOWN"] += 1
                    if endpoint_node is not None:
                        endpoint_node["handler_association"] = {
                            "reference": handler_ref, "resolution_status": "UNKNOWN",
                            "reason": "COMPLEX_OR_DYNAMIC_HANDLER_NOT_RESOLVED",
                        }
                    continue
                # Member expressions are accepted only through an exact
                # compiler-produced route binding for this source line.
                matches = (symbols_by_file_name.get((path, handler_ref), [])
                           if simple_identifier else [])
                route_line = line
                imported_matches = [
                    edge for edge in edges
                    if edge.get("type") == "references"
                    and edge.get("method") == "typescript-route-handler-binding"
                    and (edge.get("evidence") or {}).get("path") == path
                    and (edge.get("evidence") or {}).get("line") == route_line
                    and (edge.get("evidence") or {}).get("handler_reference") == handler_ref
                ]
                if not matches and len(imported_matches) == 1:
                    imported_edge = imported_matches[0]
                    imported_status = imported_edge.get("resolution_status", "UNKNOWN")
                    imported_target = imported_edge.get("target")
                    if imported_status == "RESOLVED" and imported_target in {n.get("id") for n in nodes}:
                        matches = [next(n for n in nodes if n.get("id") == imported_target)]
                    elif imported_status == "AMBIGUOUS":
                        handler_counts["AMBIGUOUS"] += 1
                        if endpoint_node is not None:
                            endpoint_node["handler_association"] = {
                                "reference": handler_ref, "resolution_status": "AMBIGUOUS",
                                "candidate_targets": imported_edge.get("candidate_targets", []),
                                "reason": "TYPESCRIPT_SYMBOL_HAS_MULTIPLE_DECLARATIONS",
                            }
                        continue
                    else:
                        handler_counts["UNKNOWN"] += 1
                        if endpoint_node is not None:
                            endpoint_node["handler_association"] = {
                                "reference": handler_ref, "resolution_status": imported_status,
                                "reason": "TYPESCRIPT_IMPORT_BINDING_NOT_UNIQUELY_RESOLVED",
                                "evidence": imported_edge.get("evidence_chain", []),
                            }
                        continue
                elif not matches and len(imported_matches) > 1:
                    handler_counts["AMBIGUOUS"] += 1
                    if endpoint_node is not None:
                        endpoint_node["handler_association"] = {
                            "reference": handler_ref, "resolution_status": "AMBIGUOUS",
                            "reason": "MULTIPLE_ROUTE_HANDLER_BINDING_RECORDS",
                        }
                    continue
                if len(matches) == 1:
                    target = matches[0]
                    handler_counts["RESOLVED"] += 1
                    if endpoint_node is not None:
                        target_evidence = target.get("evidence") or {}
                        downstream = []
                        for call_edge in edges:
                            if call_edge.get("type") != "calls" or call_edge.get("caller_symbol_id") != target["id"]:
                                continue
                            call_evidence = call_edge.get("evidence") or {}
                            downstream.append({
                                "target_id": call_edge.get("target"),
                                "resolution_status": call_edge.get("resolution_status") or "UNKNOWN",
                                "call_site": {
                                    "path": call_evidence.get("path"),
                                    "line": call_evidence.get("line"),
                                },
                                "target_location": call_edge.get("target_location"),
                                "method": call_edge.get("method"),
                                "confidence": call_edge.get("confidence"),
                            })
                        downstream.sort(key=lambda item: (
                            item["call_site"].get("path") or "",
                            item["call_site"].get("line") or 0,
                            item.get("target_id") or "",
                        ))
                        handler_downstream_calls += len(downstream)
                        symbol_ids = {
                            item.get("id") for item in nodes
                            if item.get("type") in {"function", "method"}
                            and isinstance(item.get("id"), str)
                        }
                        call_chains, chains_truncated = _collect_call_chains(
                            edges, target["id"], symbol_ids,
                        )
                        handler_call_chains += len(call_chains)
                        if chains_truncated:
                            handler_call_chains_truncated += 1
                        endpoint_node["handler_association"] = {
                            "reference": handler_ref, "resolution_status": "RESOLVED",
                            "target_symbol_id": target["id"],
                            "target_path": target_evidence.get("path"),
                            "target_line": target_evidence.get("line"),
                            "confidence": "STATIC_INFERENCE",
                            "downstream_call_evidence": downstream,
                            "call_chain_evidence": call_chains,
                            "call_chain_truncated": chains_truncated,
                            "call_chain_max_depth": 8,
                        }
                    relation = {
                        "source": node_id, "target": target["id"], "type": "references",
                        "evidence": {"path": path, **({"line": line} if line else {})},
                        "method": "route-handler-static-symbol-match",
                        "confidence": "medium", "candidate": True, "verified": False,
                        "resolution_status": "RESOLVED",
                        "verification_status": "NOT_VERIFIED",
                    }
                    if not any(e.get("source") == node_id and e.get("target") == target["id"]
                               and e.get("method") == relation["method"] for e in edges):
                        edges.append(relation)
                elif len(matches) > 1:
                    handler_counts["AMBIGUOUS"] += 1
                    if endpoint_node is not None:
                        endpoint_node["handler_association"] = {
                            "reference": handler_ref, "resolution_status": "AMBIGUOUS",
                            "candidate_targets": sorted(item["id"] for item in matches),
                            "reason": "MULTIPLE_SAME_FILE_SYMBOLS",
                        }
                else:
                    handler_counts["UNKNOWN"] += 1
                    if endpoint_node is not None:
                        endpoint_node["handler_association"] = {
                            "reference": handler_ref, "resolution_status": "UNKNOWN",
                            "reason": "NO_UNIQUE_SAME_FILE_SYMBOL",
                        }

    # S2.6: link a literal SQL execution site to its exact function owner.
    # No owner is inferred from nearby names or global symbol search.
    database_access_records = semantics.get("database_accesses", [])
    if not isinstance(database_access_records, list):
        raise ValueError("semantics.database_accesses must be a list")
    database_access_linked = 0
    database_access_unowned = 0
    database_table_links = 0
    entity_nodes_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in nodes:
        if item.get("business_kind") == "entity":
            entity_nodes_by_name.setdefault(str(item.get("label") or "").lower(), []).append(item)
    for raw in database_access_records:
        if not isinstance(raw, Mapping):
            continue
        path = _source_path(raw)
        line = _source_line(raw)
        if not path or line is None or path not in file_ids:
            unlinked.append({"kind": "database_access", "reason": "INVALID_OR_MISSING_SOURCE"})
            continue
        matching_calls = [
            edge for edge in edges
            if edge.get("type") == "calls"
            and (edge.get("evidence") or {}).get("path") == path
            and (edge.get("evidence") or {}).get("line") == line
        ]
        owners = sorted({edge.get("caller_symbol_id") for edge in matching_calls
                         if isinstance(edge.get("caller_symbol_id"), str)})
        owner_id = owners[0] if len(owners) == 1 else None
        if owner_id is None:
            database_access_unowned += 1
        identity = {
            "path": path, "line": line, "callee": raw.get("callee"),
            "operation": raw.get("operation"), "tables": raw.get("tables", []),
            "columns": raw.get("columns", []), "owner": owner_id,
        }
        access_id = _stable_id("database-access", identity)
        details = {
                "operation": raw.get("operation") or "UNKNOWN",
                "tables": raw.get("tables") if isinstance(raw.get("tables"), list) else [],
                "columns": raw.get("columns") if isinstance(raw.get("columns"), list) else [],
                "callee": raw.get("callee") or "",
                "owner_symbol_id": owner_id,
                "owner_resolution_status": "RESOLVED" if owner_id else "UNKNOWN",
                "parse_status": raw.get("parse_status") or "UNKNOWN",
                "parser": raw.get("parser") or "UNKNOWN",
            }
        if access_id not in existing_ids:
            nodes.append({
                "id": access_id, "type": "candidate", "label": f"DB {details['operation']} @ {path}:{line}",
                "business_kind": "database_access", "business_details": details,
                "evidence": {"path": path, "line": line, "confidence": "STATIC_INFERENCE"},
                "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
            })
            existing_ids.add(access_id)
            edges.append({
                "source": file_ids[path], "target": access_id, "type": "references",
                "evidence": {"path": path, "line": line, "callee": details["callee"],
                             "owner_symbol_id": owner_id},
                "method": "database-access-static-callsite", "confidence": "medium",
                "candidate": True, "verified": False, "verification_status": "NOT_VERIFIED",
            })
        database_access_linked += 1
        tables = details["tables"] if access_id in existing_ids else raw.get("tables", [])
        for table in tables if isinstance(tables, list) else []:
            matches = entity_nodes_by_name.get(str(table).lower(), [])
            if len(matches) == 1:
                edges.append({
                    "source": access_id, "target": matches[0]["id"], "type": "references",
                    "evidence": {"path": path, "line": line, "table": table,
                                 "sql_operation": raw.get("operation")},
                    "method": "database-access-to-documented-entity", "confidence": "low",
                    "candidate": True, "verified": False,
                    "verification_status": "NOT_VERIFIED",
                })
                database_table_links += 1

    # S2.7: project explicit authorization guard/check evidence.
    authorization_records = semantics.get("authorization_checks", [])
    if not isinstance(authorization_records, list):
        raise ValueError("semantics.authorization_checks must be a list")
    authorization_linked = 0
    authorization_unowned = 0
    authorization_nodes: list[dict[str, Any]] = []
    for raw in authorization_records:
        if not isinstance(raw, Mapping):
            continue
        path = _source_path(raw)
        line = _source_line(raw)
        if not path or line is None or path not in file_ids:
            unlinked.append({"kind": "authorization_check", "reason": "INVALID_OR_MISSING_SOURCE"})
            continue
        matching_calls = [
            edge for edge in edges
            if edge.get("type") == "calls"
            and (edge.get("evidence") or {}).get("path") == path
            and (edge.get("evidence") or {}).get("line") == line
        ]
        owners = sorted({edge.get("caller_symbol_id") for edge in matching_calls
                         if isinstance(edge.get("caller_symbol_id"), str)})
        owner_id = owners[0] if len(owners) == 1 and raw.get("kind") == "authorization_call" else None
        if raw.get("kind") == "authorization_call" and owner_id is None:
            authorization_unowned += 1
        identity = {"path": path, "line": line, "kind": raw.get("kind"),
                    "guard_names": raw.get("guard_names", []), "role_value": raw.get("role_value"),
                    "owner_condition": raw.get("owner_condition"), "owner_symbol_id": owner_id}
        auth_id = _stable_id("authorization-check", identity)
        details = {
            "kind": raw.get("kind") or "UNKNOWN",
            "guard_names": raw.get("guard_names") if isinstance(raw.get("guard_names"), list) else [],
            "role_value": raw.get("role_value"),
            "owner_condition": bool(raw.get("owner_condition")),
            "owner_symbol_id": owner_id,
            "owner_resolution_status": "RESOLVED" if owner_id else "UNKNOWN",
            "parser": raw.get("parser") or "UNKNOWN",
            "evidence_text": raw.get("evidence_text") or "",
        }
        if auth_id not in existing_ids:
            auth_node = {
                "id": auth_id, "type": "candidate",
                "label": f"Authorization {details['kind']} @ {path}:{line}",
                "business_kind": "authorization_check", "business_details": details,
                "evidence": {"path": path, "line": line, "confidence": "STATIC_INFERENCE"},
                "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
            }
            nodes.append(auth_node)
            existing_ids.add(auth_id)
            edges.append({
                "source": file_ids[path], "target": auth_id, "type": "references",
                "evidence": {"path": path, "line": line, "owner_symbol_id": owner_id},
                "method": "authorization-source-line-candidate", "confidence": "low",
                "candidate": True, "verified": False, "verification_status": "NOT_VERIFIED",
            })
        else:
            auth_node = next(item for item in nodes if item.get("id") == auth_id)
        authorization_nodes.append(auth_node)
        authorization_linked += 1

    # Attach database evidence to endpoints only when its owner is present in
    # the endpoint's already-resolved, bounded call-chain symbol sequence.
    for endpoint_node in nodes:
        association = endpoint_node.get("handler_association")
        if not isinstance(association, dict) or association.get("resolution_status") != "RESOLVED":
            continue
        reachable_symbols = {association.get("target_symbol_id")}
        for chain in association.get("call_chain_evidence", []):
            for step in chain.get("steps", []):
                if isinstance(step.get("caller_symbol_id"), str):
                    reachable_symbols.add(step["caller_symbol_id"])
                if isinstance(step.get("target_id"), str):
                    reachable_symbols.add(step["target_id"])
        related = []
        for access in nodes:
            details = access.get("business_details", {})
            if access.get("business_kind") == "database_access" and details.get("owner_symbol_id") in reachable_symbols:
                related.append({"database_access_id": access.get("id"),
                                "owner_symbol_id": details.get("owner_symbol_id"),
                                "path": (access.get("evidence") or {}).get("path"),
                                "line": (access.get("evidence") or {}).get("line"),
                                "operation": details.get("operation"),
                                "tables": details.get("tables", []),
                                "columns": details.get("columns", []),
                                "confidence": "STATIC_INFERENCE",
                                "verification_status": "NOT_VERIFIED"})
        related.sort(key=lambda item: (item["path"] or "", item["line"] or 0, item["database_access_id"]))
        association["database_access_evidence"] = related

    # Route middleware evidence is file/line anchored; function-level guard
    # calls are attached only when their exact caller symbol is reachable.
    for endpoint_node in nodes:
        if endpoint_node.get("business_kind") != "endpoint":
            continue
        endpoint_evidence = endpoint_node.get("evidence") or {}
        endpoint_path = endpoint_evidence.get("path")
        association = endpoint_node.get("handler_association")
        reachable_symbols = set()
        if isinstance(association, dict) and association.get("resolution_status") == "RESOLVED":
            reachable_symbols.add(association.get("target_symbol_id"))
            for chain in association.get("call_chain_evidence", []):
                for step in chain.get("steps", []):
                    if isinstance(step.get("caller_symbol_id"), str):
                        reachable_symbols.add(step["caller_symbol_id"])
                    if isinstance(step.get("target_id"), str):
                        reachable_symbols.add(step["target_id"])
        route_guards = []
        function_guards = []
        for auth_node in authorization_nodes:
            details = auth_node.get("business_details", {})
            auth_evidence = auth_node.get("evidence", {})
            if details.get("kind") == "route_middleware" and auth_evidence.get("path") == endpoint_path:
                route_guards.append({"authorization_check_id": auth_node["id"],
                                     "path": endpoint_path, "line": auth_evidence.get("line"),
                                     "guard_names": details.get("guard_names", []),
                                     "verification_status": "NOT_VERIFIED"})
            elif details.get("owner_symbol_id") in reachable_symbols and details.get("owner_symbol_id"):
                function_guards.append({"authorization_check_id": auth_node["id"],
                                        "owner_symbol_id": details.get("owner_symbol_id"),
                                        "path": endpoint_path, "line": auth_evidence.get("line"),
                                        "guard_names": details.get("guard_names", []),
                                        "role_value": details.get("role_value"),
                                        "verification_status": "NOT_VERIFIED"})
        route_guards.sort(key=lambda item: (item["line"] or 0, item["authorization_check_id"]))
        function_guards.sort(key=lambda item: (item["line"] or 0, item["authorization_check_id"]))
        endpoint_node["authorization_evidence"] = {
            "status": "DETECTED" if route_guards or function_guards else "NOT_DETECTED_STATIC_SCAN",
            "route_middleware": route_guards,
            "reachable_function_checks": function_guards,
            "limitations": [
                "检测到 guard 标识或权限条件仅代表静态代码线索，不证明该检查在所有请求路径上执行或正确拒绝未授权请求。",
                "未检测到不等于缺少权限控制；动态注册、封装、中间件顺序、框架插件及跨语言边界可能未覆盖。",
            ],
        }

    result["nodes"] = nodes
    result["edges"] = edges
    result["business_understanding"] = {
        "schema_version": _SCHEMA,
        "status": "PARTIAL_EVIDENCE_LINKS",
        "linked_record_counts": linked_counts,
        "linked_records": linked,
        "unlinked_records": unlinked,
        "route_handler_associations": handler_counts,
        "handler_downstream_call_evidence": handler_downstream_calls,
        "handler_call_chains": handler_call_chains,
        "handler_call_chains_truncated": handler_call_chains_truncated,
        "database_access_records": database_access_linked,
        "database_access_unowned": database_access_unowned,
        "database_access_table_links": database_table_links,
        "authorization_check_records": authorization_linked,
        "authorization_check_unowned": authorization_unowned,
        "limitations": [
            "业务记录只与精确来源文件关联；命名路由 handler 可通过同文件唯一符号或 TypeScript Compiler API 的导入符号证据建立候选引用。",
            "跨文件 handler 仅在编译器符号解析唯一且目标符号存在于当前图谱时关联；动态、外部、歧义或缺少符号证据时保留原状态，不按名称猜测。",
            "未建立功能到接口、接口到数据实体、角色到权限的推断关系。",
            "candidate/verified=false 表示未验证，不代表运行时可达或功能已实现。",
            "调用链仅沿唯一 RESOLVED 的图谱符号边进行有界静态展开；循环会标记，深度/数量上限会截断，不证明运行时执行。",
            "数据库访问只识别单行直接 SQL 字符串字面量；owner 必须由同路径同调用行的 caller_symbol_id 唯一绑定。动态/拼接/多行 SQL 不解析；表/列解析仅为静态候选，不证明实际读写、事务提交或字段数据流。",
            "权限证据仅识别有限的 route middleware 名称、显式权限调用名和简单 role/owner 条件；函数权限调用只有在同文件同调用行的唯一 caller_symbol_id 存在时才归属函数。未检测到权限线索不证明缺少授权，检测到线索也不证明运行时执行或检查正确。",
        ],
    }
    return result
