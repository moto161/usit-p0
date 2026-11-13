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


def _reachable_symbols(edges: list[dict[str, Any]], root_symbol_id: str,
                       symbol_ids: set[str]) -> tuple[set[str], int]:
    """BFS over RESOLVED call edges from a root symbol.

    Returns (reachable set including the root, max depth reached). This is
    the set the endpoint's database/identity attachment is judged against,
    and it is deliberately separate from `_collect_call_chains`: enumerating
    chains explodes combinatorially and needs a depth/chain cap, while a
    visited-set BFS is linear in the graph and needs neither. The max depth
    is reported, not enforced. Unresolved/external edges are not followed,
    matching the chain walk's follow rule.
    """
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        caller = edge.get("caller_symbol_id")
        target = edge.get("target")
        if (edge.get("type") == "calls"
                and edge.get("resolution_status") == "RESOLVED"
                and isinstance(caller, str) and isinstance(target, str)
                and target in symbol_ids):
            adjacency.setdefault(caller, []).append(target)
    if not isinstance(root_symbol_id, str):
        return set(), 0
    reachable = {root_symbol_id}
    max_depth = 0
    queue: list[tuple[str, int]] = [(root_symbol_id, 0)]
    while queue:
        current, depth = queue.pop(0)
        for target in adjacency.get(current, []):
            if target in reachable:
                continue
            reachable.add(target)
            max_depth = max(max_depth, depth + 1)
            queue.append((target, depth + 1))
    return reachable, max_depth


def _build_endpoint_execution_paths(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize only explicit endpoint → handler → call → DB/auth/identity evidence."""
    paths: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("business_kind") != "endpoint":
            continue
        details = node.get("business_details")
        details = details if isinstance(details, Mapping) else {}
        evidence = node.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        association = node.get("handler_association")
        association = association if isinstance(association, Mapping) else {}
        authorization = node.get("authorization_evidence")
        authorization = authorization if isinstance(authorization, Mapping) else {}
        chains = association.get("call_chain_evidence")
        chains = chains if isinstance(chains, list) else []
        database = association.get("database_access_evidence")
        database = database if isinstance(database, list) else []
        identity = association.get("identity_dataflow_evidence")
        identity = identity if isinstance(identity, list) else []
        route_guards = authorization.get("route_middleware")
        route_guards = route_guards if isinstance(route_guards, list) else []
        function_guards = authorization.get("reachable_function_checks")
        function_guards = function_guards if isinstance(function_guards, list) else []

        call_steps: list[dict[str, Any]] = []
        unresolved_steps: list[dict[str, Any]] = []
        for chain_index, chain in enumerate(chains):
            if not isinstance(chain, Mapping):
                continue
            for step_index, step in enumerate(chain.get("steps", [])):
                if not isinstance(step, Mapping):
                    continue
                item = {
                    "chain_index": chain_index,
                    "step_index": step_index,
                    "caller_symbol_id": step.get("caller_symbol_id"),
                    "target_id": step.get("target_id"),
                    "resolution_status": step.get("resolution_status") or "UNKNOWN",
                    "call_site": step.get("call_site") or {},
                    "target_location": step.get("target_location"),
                    "method": step.get("method"),
                    "confidence": "STATIC_INFERENCE",
                }
                call_steps.append(item)
                if item["resolution_status"] != "RESOLVED":
                    unresolved_steps.append(item)
        call_steps.sort(key=lambda item: (
            item["chain_index"], item["step_index"],
            str(item["call_site"].get("path") or ""),
            int(item["call_site"].get("line") or 0),
            str(item.get("target_id") or ""),
        ))
        db_refs = [
            {"database_access_id": item.get("database_access_id"),
             "path": item.get("path"), "line": item.get("line"),
             "operation": item.get("operation"), "tables": item.get("tables", []),
             "columns": item.get("columns", []),
             "verification_status": item.get("verification_status") or "NOT_VERIFIED"}
            for item in database if isinstance(item, Mapping)
        ]
        auth_refs = [
            {"authorization_check_id": item.get("authorization_check_id"),
             "path": item.get("path"), "line": item.get("line"),
             "guard_names": item.get("guard_names", []),
             "kind": "route_middleware", "verification_status": "NOT_VERIFIED"}
            for item in route_guards if isinstance(item, Mapping)
        ] + [
            {"authorization_check_id": item.get("authorization_check_id"),
             "path": item.get("path"), "line": item.get("line"),
             "guard_names": item.get("guard_names", []),
             "kind": "reachable_function_check", "verification_status": "NOT_VERIFIED"}
            for item in function_guards if isinstance(item, Mapping)
        ]
        identity_refs = [
            {"identity_evidence_id": item.get("identity_evidence_id"),
             "kind": item.get("kind"), "path": item.get("path"),
             "line": item.get("line"), "scope": item.get("scope"),
             "verification_status": item.get("verification_status") or "NOT_VERIFIED"}
            for item in identity if isinstance(item, Mapping)
        ]
        handler_status = association.get("resolution_status") or "UNKNOWN"
        if association.get("call_chain_truncated"):
            call_status = "TRUNCATED"
        elif unresolved_steps:
            call_status = "PARTIAL"
        elif call_steps:
            call_status = "RESOLVED_STATIC_CHAIN"
        else:
            call_status = "NO_CALL_CHAIN_EVIDENCE"
        flow = {
            "endpoint_id": node.get("id"),
            "method": details.get("method"),
            "endpoint": details.get("endpoint") or node.get("label"),
            "source": {"path": evidence.get("path"), "line": evidence.get("line")},
            "handler": {
                "reference": association.get("reference"),
                "symbol_id": association.get("target_symbol_id"),
                "path": association.get("target_path"),
                "line": association.get("target_line"),
                "resolution_status": handler_status,
            },
            "call_chain": call_steps,
            "database_accesses": db_refs,
            "authorization": {
                "status": authorization.get("status") or "UNKNOWN",
                "evidence": auth_refs,
            },
            "identity_resource_flow": identity_refs,
            "identity_parameter_propagation": association.get("identity_parameter_propagation_evidence", []),
            "identity_sql_sink_bindings": association.get("identity_sql_sink_evidence", []),
            "stage_status": {
                "entrypoint": "SOURCE_IDENTIFIED",
                "handler": handler_status,
                "calls": call_status,
                "database": "LINKED" if db_refs else "NOT_LINKED_OR_UNKNOWN",
                "authorization": authorization.get("status") or "UNKNOWN",
                "identity_resource": "DETECTED" if identity_refs else "NOT_DETECTED_STATIC_SCAN",
                "identity_sql_sink_binding": (
                    "STATIC_PARAMETER_LINKED_TO_SQL_OWNER_BINDING"
                    if association.get("identity_sql_sink_evidence")
                    else "NOT_LINKED_OR_UNKNOWN"
                ),
                "identity_parameter_propagation": (
                    "STATIC_MAPPING_DETECTED"
                    if association.get("identity_parameter_propagation_evidence")
                    else "NOT_DETECTED_OR_UNKNOWN"
                ),
            },
            "unknowns": [
                "静态调用链不证明运行时执行、路由注册顺序或请求可达。",
                "数据库关联只证明有限静态调用点与函数符号的候选关联，不证明实际读写或事务结果。",
                "授权/身份线索不证明检查执行、身份可信、资源归属正确或拒绝行为有效。",
            ] + (["调用链包含未解析/外部/动态终止步骤，后续路径未知。"] if unresolved_steps else [])
              + (["调用链达到分析上限，后续调用未展开。"] if association.get("call_chain_truncated") else []),
            "confidence": "STATIC_INFERENCE",
            "verification_status": "NOT_VERIFIED",
        }
        flow["execution_path_id"] = _stable_id("endpoint-execution-path", {
            "endpoint_id": flow["endpoint_id"],
            "source": flow["source"],
            "handler": flow["handler"],
        })
        paths.append(flow)
    paths.sort(key=lambda item: (
        str(item.get("source", {}).get("path") or ""),
        int(item.get("source", {}).get("line") or 0),
        str(item.get("endpoint") or ""),
        str(item.get("execution_path_id") or ""),
    ))
    return paths


def _strip_url_suffix(skeleton_path: str) -> str:
    """Cut a URL skeleton at the first literal "?" or "#" outside "{?}"."""
    out: list[str] = []
    i = 0
    while i < len(skeleton_path):
        if skeleton_path.startswith("{?}", i):
            out.append("{?}")
            i += 3
            continue
        ch = skeleton_path[i]
        if ch in ("?", "#"):
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _is_route_param_segment(segment: str) -> bool:
    """True when a route path segment is a parameter, not a literal name."""
    if segment == "*":
        return True
    if segment.startswith(":") and len(segment) > 1:
        return True
    return segment.startswith("{") and segment.endswith("}") and len(segment) > 2


def _match_template_path(skeleton_path: str, route_path: str) -> bool:
    """Match a URL skeleton against a route pattern, segment by segment.

    A "{?}" skeleton segment matches exactly one route parameter segment
    (":id", "{id}" or "*"); literal segments must match exactly; a
    placeholder inside a segment ("/photos/prefix-{?}") never matches, and
    the segment counts must agree. A literal call path never matches a
    parameter segment here -- that is a different rule, not this one.
    """
    skeleton = _strip_url_suffix(skeleton_path)
    route = _strip_url_suffix(route_path)
    skeleton_segments = [seg for seg in skeleton.split("/") if seg]
    route_segments = [seg for seg in route.split("/") if seg]
    if len(skeleton_segments) != len(route_segments):
        return False
    for skeleton_segment, route_segment in zip(skeleton_segments, route_segments):
        if skeleton_segment == route_segment:
            continue
        if skeleton_segment == "{?}" and _is_route_param_segment(route_segment):
            continue
        return False
    return True


def _match_value_path(call_path: str, route_path: str) -> bool:
    """Match a literal call path against a route pattern, segment by segment.

    A literal call segment matches one route parameter segment (":id",
    "{id}" or "*"); literal segments must match exactly; the segment
    counts must agree. A literal segment and a parameter value are
    indistinguishable statically, so a match here is a STATIC_INFERENCE
    only -- never proof the call hit that route.
    """
    call = _strip_url_suffix(call_path)
    route = _strip_url_suffix(route_path)
    call_segments = [seg for seg in call.split("/") if seg]
    route_segments = [seg for seg in route.split("/") if seg]
    if len(call_segments) != len(route_segments):
        return False
    for call_segment, route_segment in zip(call_segments, route_segments):
        if call_segment == route_segment:
            continue
        if _is_route_param_segment(route_segment):
            continue
        return False
    return True


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
    symbols_by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if node.get("type") not in {"function", "method"}:
            continue
        evidence = node.get("evidence")
        path = evidence.get("path") if isinstance(evidence, Mapping) else None
        label = node.get("label")
        if isinstance(path, str) and isinstance(label, str) and path and label:
            symbols_by_file_name.setdefault((path, label), []).append(node)
            if isinstance(node.get("id"), str):
                symbols_by_id[node["id"]] = node

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
                    and edge.get("method") in {"typescript-route-handler-binding",
                                               "python-route-handler-binding"}
                    and (edge.get("evidence") or {}).get("path") == path
                    and (edge.get("evidence") or {}).get("line") == route_line
                    and (edge.get("evidence") or {}).get("handler_reference") == handler_ref
                ]
                if not matches and len(imported_matches) == 1:
                    imported_edge = imported_matches[0]
                    binding_kind = ("PYTHON" if imported_edge.get("method") == "python-route-handler-binding"
                                    else "TYPESCRIPT")
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
                                "reason": f"{binding_kind}_SYMBOL_HAS_MULTIPLE_DECLARATIONS",
                            }
                        continue
                    else:
                        handler_counts["UNKNOWN"] += 1
                        if endpoint_node is not None:
                            endpoint_node["handler_association"] = {
                                "reference": handler_ref, "resolution_status": imported_status,
                                "reason": f"{binding_kind}_IMPORT_BINDING_NOT_UNIQUELY_RESOLVED",
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
                "has_dynamic_parts": bool(raw.get("has_dynamic_parts", False)),
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

    # S2.9: project line-anchored identity/resource data-flow evidence.
    # A record is linked to a DB access on an exact same-file, same-line
    # match. In addition (P1), a handler-level record whose function owner
    # is resolved attaches to DB accesses owned by the same function
    # (SAME_FUNCTION_SCOPE, candidate only). Sink-binding records stay
    # line-exact: their propagation chain ends at a specific sink line, so
    # attaching them to other DB calls in the same function would be a
    # false association. No control-flow analysis is performed.
    _OWNER_SCOPE_IDENTITY_KINDS = {
        "authenticated_identity_source",
        "request_controlled_identity_input",
        "resource_owner_comparison",
    }
    identity_records = semantics.get("identity_evidence", [])
    if not isinstance(identity_records, list):
        raise ValueError("semantics.identity_evidence must be a list")
    identity_linked = 0
    identity_unlinked = 0
    identity_owner_scope_links = 0
    identity_nodes: list[dict[str, Any]] = []
    identity_by_database_id: dict[str, list[dict[str, Any]]] = {}
    database_nodes_by_location: dict[tuple[str, int], list[dict[str, Any]]] = {}
    database_nodes_by_owner: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in nodes:
        if item.get("business_kind") != "database_access":
            continue
        evidence = item.get("evidence") or {}
        if isinstance(evidence.get("path"), str) and isinstance(evidence.get("line"), int):
            database_nodes_by_location.setdefault((evidence["path"], evidence["line"]), []).append(item)
        item_details = item.get("business_details") or {}
        item_owner = item_details.get("owner_symbol_id")
        if isinstance(evidence.get("path"), str) and isinstance(item_owner, str):
            database_nodes_by_owner.setdefault((evidence["path"], item_owner), []).append(item)
    for raw in identity_records:
        if not isinstance(raw, Mapping):
            continue
        path = _source_path(raw)
        line = _source_line(raw)
        if not path or line is None or path not in file_ids:
            identity_unlinked += 1
            unlinked.append({"kind": "identity_evidence", "reason": "INVALID_OR_MISSING_SOURCE"})
            continue
        details = raw.get("details") if isinstance(raw.get("details"), Mapping) else {}
        raw_owner = raw.get("owner_symbol_id")
        owner_node = symbols_by_id.get(raw_owner) if isinstance(raw_owner, str) else None
        owner_evidence = owner_node.get("evidence") if isinstance(owner_node, Mapping) else {}
        owner_symbol_id = (
            raw_owner if isinstance(owner_evidence, Mapping) and owner_evidence.get("path") == path
            and raw.get("owner_resolution_status") == "RESOLVED" else None
        )
        identity_id = raw.get("evidence_id")
        if not isinstance(identity_id, str) or not identity_id:
            identity_id = _stable_id("identity-evidence", {
                "path": path, "line": line, "kind": raw.get("kind"), "details": dict(details),
            })
        identity_node = {
            "id": identity_id, "type": "candidate",
            "label": f"Identity evidence {raw.get('kind') or 'UNKNOWN'} @ {path}:{line}",
            "business_kind": "identity_evidence",
            "business_details": {
                "kind": raw.get("kind") or "UNKNOWN",
                "details": dict(details),
                "scope": raw.get("scope") or "UNKNOWN",
                "owner_symbol_id": owner_symbol_id,
                "owner_resolution_status": "RESOLVED" if owner_symbol_id else "UNKNOWN",
            },
            "evidence": {"path": path, "line": line, "confidence": "STATIC_INFERENCE"},
            "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
        }
        if identity_id not in existing_ids:
            nodes.append(identity_node)
            existing_ids.add(identity_id)
            edges.append({
                "source": file_ids[path], "target": identity_id, "type": "references",
                "evidence": {"path": path, "line": line, "identity_kind": raw.get("kind")},
                "method": "identity-evidence-exact-source-line", "confidence": "low",
                "candidate": True, "verified": False, "verification_status": "NOT_VERIFIED",
            })
        identity_nodes.append(identity_node)
        matching_db = database_nodes_by_location.get((path, line), [])
        matching_db_ids = {
            db_node.get("id") for db_node in matching_db
            if isinstance(db_node.get("id"), str)
        }
        for db_node in matching_db:
            db_id = db_node.get("id")
            if isinstance(db_id, str):
                identity_by_database_id.setdefault(db_id, []).append({
                    "identity_evidence_id": identity_id,
                    "kind": raw.get("kind") or "UNKNOWN",
                    "path": path, "line": line,
                    "details": dict(details),
                    "scope": raw.get("scope") or "UNKNOWN",
                    "link_method": "SAME_LINE",
                    "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED",
                })
                db_details = db_node.get("business_details")
                if isinstance(db_details, dict):
                    refs = db_details.setdefault("identity_evidence_ids", [])
                    if identity_id not in refs:
                        refs.append(identity_id)
        # P1: owner-scope linking for handler-level records only. A record
        # already linked same-line to a DB node is not linked twice.
        if (isinstance(owner_symbol_id, str)
                and raw.get("kind") in _OWNER_SCOPE_IDENTITY_KINDS):
            for db_node in database_nodes_by_owner.get((path, owner_symbol_id), []):
                db_id = db_node.get("id")
                if not isinstance(db_id, str) or db_id in matching_db_ids:
                    continue
                matching_db_ids.add(db_id)
                identity_by_database_id.setdefault(db_id, []).append({
                    "identity_evidence_id": identity_id,
                    "kind": raw.get("kind") or "UNKNOWN",
                    "path": path, "line": line,
                    "details": dict(details),
                    "scope": raw.get("scope") or "UNKNOWN",
                    "link_method": "SAME_FUNCTION_SCOPE",
                    "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED",
                })
                db_details = db_node.get("business_details")
                if isinstance(db_details, dict):
                    refs = db_details.setdefault("identity_evidence_ids", [])
                    if identity_id not in refs:
                        refs.append(identity_id)
                identity_owner_scope_links += 1
        identity_linked += 1

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
    # the endpoint's BFS-reachable symbol set. The set comes from
    # `_reachable_symbols`, not from the enumerated call chains, so database
    # evidence is not lost when chain enumeration truncates at its depth or
    # chain caps; the chain detail itself stays capped (see `call_chain_*`).
    symbol_ids = {
        item.get("id") for item in nodes
        if item.get("type") in {"function", "method"}
        and isinstance(item.get("id"), str)
    }
    for endpoint_node in nodes:
        association = endpoint_node.get("handler_association")
        if not isinstance(association, dict) or association.get("resolution_status") != "RESOLVED":
            continue
        reachable_symbols, reachable_max_depth = _reachable_symbols(
            edges, association.get("target_symbol_id"), symbol_ids)
        association["reachable_symbol_count"] = len(reachable_symbols)
        association["reachable_max_depth"] = reachable_max_depth
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

    # Attach exact same-line identity evidence to endpoints only through a DB
    # access already present in their resolved bounded call-chain association.
    for endpoint_node in nodes:
        association = endpoint_node.get("handler_association")
        if not isinstance(association, dict):
            continue
        db_ids = {
            item.get("database_access_id")
            for item in association.get("database_access_evidence", [])
            if isinstance(item, Mapping) and isinstance(item.get("database_access_id"), str)
        }
        related_identity = [
            record
            for db_id in sorted(db_ids)
            for record in identity_by_database_id.get(db_id, [])
        ]
        related_identity.sort(key=lambda item: (
            item["path"], item["line"], item["kind"], item["identity_evidence_id"]
        ))

        # S2.30-C: propagate identity expressions across exact, uniquely resolved
        # caller→callee parameter mappings. Each hop is recorded independently;
        # no return/field/collection flow or runtime execution is inferred.
        # The reachable set is the same BFS set as the database attachment
        # above, so propagation is not cut off by chain-enumeration caps.
        identity_parameter_evidence: list[dict[str, Any]] = []
        reachable_symbols, _ = _reachable_symbols(
            edges, association.get("target_symbol_id"), symbol_ids)

        call_edges_by_caller: dict[str, list[dict[str, Any]]] = {}
        for candidate_edge in edges:
            caller_id = candidate_edge.get("caller_symbol_id")
            if (candidate_edge.get("type") == "calls"
                    and candidate_edge.get("resolution_status") == "RESOLVED"
                    and candidate_edge.get("argument_binding_status") == "EXACT_STATIC_MAPPING"
                    and caller_id in reachable_symbols
                    and candidate_edge.get("target") in reachable_symbols):
                call_edges_by_caller.setdefault(caller_id, []).append(candidate_edge)

        for identity_node in identity_nodes:
            identity_details = identity_node.get("business_details") or {}
            identity_owner = identity_details.get("owner_symbol_id")
            if (identity_owner not in reachable_symbols
                    or identity_details.get("owner_resolution_status") != "RESOLVED"):
                continue
            source_details = identity_details.get("details") or {}
            source_variable = source_details.get("source_variable")
            source_expressions = (
                source_details.get("identity_expressions")
                or source_details.get("request_expressions") or []
            )
            accepted_expressions = {
                item.strip() for item in [source_variable, *source_expressions]
                if isinstance(item, str) and item.strip()
            }
            if not accepted_expressions:
                continue

            queue: list[tuple[str, str, frozenset[str], list[dict[str, Any]]]] = [
                (identity_owner, expression, frozenset({identity_owner}), [])
                for expression in sorted(accepted_expressions)
            ]
            seen_states: set[tuple[str, str, tuple[str, ...]]] = set()
            while queue:
                current_symbol, current_expression, visited, prior_chain = queue.pop(0)
                state_key = (current_symbol, current_expression, tuple(sorted(visited)))
                if state_key in seen_states:
                    continue
                seen_states.add(state_key)

                matching: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
                for call_edge in call_edges_by_caller.get(current_symbol, []):
                    for binding in call_edge.get("argument_bindings", []):
                        if not isinstance(binding, Mapping):
                            continue
                        argument_expression = binding.get("argument_expression")
                        if (isinstance(argument_expression, str)
                                and argument_expression.strip() == current_expression):
                            matching.append((call_edge, binding))

                by_callsite: dict[tuple[str, int], list[tuple[dict[str, Any], Mapping[str, Any]]]] = {}
                for call_edge, binding in matching:
                    call_evidence = call_edge.get("evidence") or {}
                    key = (str(call_evidence.get("path") or ""),
                           int(call_evidence.get("line") or 0))
                    by_callsite.setdefault(key, []).append((call_edge, binding))

                for callsite_key in sorted(by_callsite):
                    candidates = by_callsite[callsite_key]
                    target_ids = {edge.get("target") for edge, _ in candidates}
                    if len(target_ids) != 1 or len(candidates) != 1:
                        continue
                    call_edge, binding = candidates[0]
                    callee_symbol = call_edge.get("target")
                    parameter_name = binding.get("parameter_name")
                    if (not isinstance(callee_symbol, str)
                            or callee_symbol in visited
                            or not isinstance(parameter_name, str)
                            or not parameter_name.strip()):
                        continue
                    call_evidence = call_edge.get("evidence") or {}
                    hop_chain = prior_chain + [{
                        "caller_symbol_id": current_symbol,
                        "callee_symbol_id": callee_symbol,
                        "call_site": {
                            "path": call_evidence.get("path"),
                            "line": call_evidence.get("line"),
                        },
                        "argument_expression": current_expression,
                        "parameter_name": parameter_name.strip(),
                    }]
                    identity_parameter_evidence.append({
                        "identity_evidence_id": identity_node.get("id"),
                        "identity_kind": identity_details.get("kind") or "UNKNOWN",
                        "caller_symbol_id": current_symbol,
                        "callee_symbol_id": callee_symbol,
                        "call_site": {
                            "path": call_evidence.get("path"),
                            "line": call_evidence.get("line"),
                        },
                        "argument_expression": current_expression,
                        "argument_index": binding.get("argument_index"),
                        "argument_kind": binding.get("argument_kind"),
                        "parameter_name": parameter_name.strip(),
                        "parameter_index": binding.get("parameter_index"),
                        "parameter_declaration": binding.get("parameter_declaration"),
                        "propagation_depth": len(hop_chain),
                        "propagation_chain": hop_chain,
                        "propagation_status": (
                            "CALL_ARGUMENT_TO_PARAMETER_STATIC_MAPPING"
                            if len(hop_chain) == 1 else "MULTI_HOP_STATIC_PARAMETER_MAPPING"
                        ),
                        "confidence": "STATIC_INFERENCE",
                        "verification_status": "NOT_VERIFIED",
                    })
                    queue.append((
                        callee_symbol, parameter_name.strip(),
                        visited | {callee_symbol}, hop_chain,
                    ))

        identity_parameter_evidence.sort(key=lambda item: (
            int(item.get("propagation_depth") or 0),
            str(item.get("call_site", {}).get("path") or ""),
            int(item.get("call_site", {}).get("line") or 0),
            str(item.get("parameter_name") or ""),
            str(item.get("identity_evidence_id") or ""),
        ))
        association["identity_dataflow_evidence"] = related_identity
        association["identity_parameter_propagation_evidence"] = identity_parameter_evidence
        # Link a propagated callee parameter to a SQL owner-predicate binding
        # only when both pieces of evidence resolve to the same exact function
        # symbol and the binding argument is a bare identifier matching the
        # propagated parameter name.
        identity_sql_sink_evidence: list[dict[str, Any]] = []
        for sink_node in identity_nodes:
            sink_details = sink_node.get("business_details") or {}
            sink_owner = sink_details.get("owner_symbol_id")
            if (sink_details.get("kind") != "sql_owner_parameter_binding"
                    or sink_details.get("owner_resolution_status") != "RESOLVED"
                    or sink_owner not in reachable_symbols):
                continue
            sink_payload = sink_details.get("details") or {}
            binding_parameters = sink_payload.get("binding_parameters")
            if not isinstance(binding_parameters, list):
                continue
            sink_location = sink_node.get("evidence") or {}
            for propagation in identity_parameter_evidence:
                if (propagation.get("callee_symbol_id") != sink_owner
                        or propagation.get("parameter_name") not in binding_parameters):
                    continue
                identity_sql_sink_evidence.append({
                    "identity_evidence_id": propagation.get("identity_evidence_id"),
                    "sql_sink_evidence_id": sink_node.get("id"),
                    "sink_owner_symbol_id": sink_owner,
                    "path": sink_location.get("path"),
                    "line": sink_location.get("line"),
                    "resource_fields": sink_payload.get("resource_fields", []),
                    "binding_parameter": propagation.get("parameter_name"),
                    "binding_status": "PROPAGATED_PARAMETER_TO_SQL_OWNER_BINDING",
                    "propagation_depth": propagation.get("propagation_depth"),
                    "propagation_chain": propagation.get("propagation_chain", []),
                    "confidence": "STATIC_INFERENCE",
                    "verification_status": "NOT_VERIFIED",
                })
        identity_sql_sink_evidence.sort(key=lambda item: (
            str(item.get("path") or ""), int(item.get("line") or 0),
            str(item.get("binding_parameter") or ""),
            str(item.get("identity_evidence_id") or ""),
        ))
        association["identity_sql_sink_evidence"] = identity_sql_sink_evidence


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

    # S2.13-A: explicit JSX event-handler entrypoints; not yet linked to API routes.
    # V1: Vue SFC template bindings (vue-template-event-handler-binding) are
    # collected the same way; the join below only needs a RESOLVED handler
    # symbol id and call edges, which the Vue script analysis provides.
    frontend_interaction_bindings = []
    for edge in edges:
        if edge.get("method") not in {"typescript-jsx-event-handler-binding",
                                      "vue-template-event-handler-binding"}:
            continue
        evidence = edge.get("evidence") if isinstance(edge.get("evidence"), Mapping) else {}
        frontend_interaction_bindings.append({
            "source_file": evidence.get("path"), "line": evidence.get("line"),
            "event_name": edge.get("event_name"), "binding_expression": edge.get("binding_expression"),
            "handler_symbol_id": edge.get("target") if edge.get("resolution_status") == "RESOLVED" else None,
            "resolution_status": edge.get("resolution_status") or "UNKNOWN",
            "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
        })
    frontend_interaction_bindings.sort(key=lambda item: (
        str(item.get("source_file") or ""), int(item.get("line") or 0),
        str(item.get("event_name") or ""), str(item.get("binding_expression") or ""),
    ))
    # S2.13-B: exact frontend HTTP method/path literal -> documented/code endpoint candidates.
    # F2: template skeletons (TEMPLATE_METHOD_PATH) also match route patterns
    # segment by segment; a "{?}" segment matches one route parameter segment.
    endpoint_by_contract: dict[tuple[str, str], list[dict[str, Any]]] = {}
    endpoints_by_method: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for endpoint_node in nodes:
        if endpoint_node.get("business_kind") != "endpoint":
            continue
        details = endpoint_node.get("business_details")
        details = details if isinstance(details, Mapping) else {}
        method = str(details.get("method") or "").upper()
        path = str(details.get("endpoint") or details.get("path") or "")
        if method and path:
            endpoint_by_contract.setdefault((method, path), []).append(endpoint_node)
            endpoints_by_method.setdefault(method, []).append((path, endpoint_node))
    api_endpoint_links = []
    for edge in edges:
        if edge.get("method") != "typescript-api-client-call":
            continue
        method = str(edge.get("route_method") or "").upper()
        path = edge.get("route_path")
        matches = endpoint_by_contract.get((method, path), []) if method and isinstance(path, str) else []
        evidence = edge.get("evidence") if isinstance(edge.get("evidence"), Mapping) else {}
        if len(matches) == 1:
            endpoint = matches[0]
            api_endpoint_links.append({
                "api_call_node_id": edge.get("target"), "endpoint_node_id": endpoint.get("id"),
                "method": method, "path": path, "source_file": evidence.get("path"),
                "line": evidence.get("line"), "caller_symbol_id": edge.get("caller_symbol_id"),
                "resolution_status": "EXACT_MATCH", "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
            })
            continue
        template_matches: list[dict[str, Any]] = []
        if (edge.get("resolution_status") == "TEMPLATE_METHOD_PATH"
                and method and isinstance(path, str)):
            template_matches = [
                node for route_path, node in endpoints_by_method.get(method, [])
                if _match_template_path(path, route_path)
            ]
        if len(template_matches) == 1:
            endpoint = template_matches[0]
            api_endpoint_links.append({
                "api_call_node_id": edge.get("target"), "endpoint_node_id": endpoint.get("id"),
                "method": method, "path": path, "source_file": evidence.get("path"),
                "line": evidence.get("line"), "caller_symbol_id": edge.get("caller_symbol_id"),
                "resolution_status": "TEMPLATE_MATCH", "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
            })
            continue
        if template_matches:
            api_endpoint_links.append({
                "api_call_node_id": edge.get("target"), "method": method or None, "path": path,
                "source_file": evidence.get("path"), "line": evidence.get("line"),
                "caller_symbol_id": edge.get("caller_symbol_id"),
                "resolution_status": "AMBIGUOUS",
                "candidate_endpoint_ids": sorted(item.get("id") for item in template_matches),
                "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
            })
            continue
        # F3: a literal call segment fills one route parameter segment.
        value_matches: list[dict[str, Any]] = []
        if (edge.get("resolution_status") == "LITERAL_METHOD_PATH"
                and method and isinstance(path, str)):
            value_matches = [
                node for route_path, node in endpoints_by_method.get(method, [])
                if _match_value_path(path, route_path)
            ]
        if len(value_matches) == 1:
            endpoint = value_matches[0]
            api_endpoint_links.append({
                "api_call_node_id": edge.get("target"), "endpoint_node_id": endpoint.get("id"),
                "method": method, "path": path, "source_file": evidence.get("path"),
                "line": evidence.get("line"), "caller_symbol_id": edge.get("caller_symbol_id"),
                "resolution_status": "VALUE_MATCH", "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
            })
            continue
        if value_matches:
            api_endpoint_links.append({
                "api_call_node_id": edge.get("target"), "method": method or None, "path": path,
                "source_file": evidence.get("path"), "line": evidence.get("line"),
                "caller_symbol_id": edge.get("caller_symbol_id"),
                "resolution_status": "AMBIGUOUS",
                "candidate_endpoint_ids": sorted(item.get("id") for item in value_matches),
                "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
            })
            continue
        resolution = edge.get("resolution_status")
        if resolution in ("LITERAL_METHOD_PATH", "TEMPLATE_METHOD_PATH"):
            resolution = "NO_MATCH" if not matches else "AMBIGUOUS"
        api_endpoint_links.append({
            "api_call_node_id": edge.get("target"), "method": method or None, "path": path,
            "source_file": evidence.get("path"), "line": evidence.get("line"),
            "caller_symbol_id": edge.get("caller_symbol_id"),
            "resolution_status": resolution,
            "candidate_endpoint_ids": sorted(item.get("id") for item in matches),
            "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
        })
    # Join UI entrypoints to API endpoints only through resolved call-graph edges.
    calls_by_caller: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("type") == "calls" and edge.get("resolution_status") == "RESOLVED":
            caller = edge.get("caller_symbol_id")
            target = edge.get("target")
            if isinstance(caller, str) and isinstance(target, str):
                calls_by_caller.setdefault(caller, []).append(target)
    api_links_by_caller: dict[str, list[dict[str, Any]]] = {}
    for item in api_endpoint_links:
        caller = item.get("caller_symbol_id")
        if isinstance(caller, str):
            api_links_by_caller.setdefault(caller, []).append(item)
    frontend_business_chains = []
    for binding in frontend_interaction_bindings:
        start = binding.get("handler_symbol_id")
        if binding.get("resolution_status") != "RESOLVED" or not isinstance(start, str):
            continue
        queue = [(start, 0)]
        visited = set()
        found = []
        while queue and len(visited) < 100:
            symbol_id, depth = queue.pop(0)
            if symbol_id in visited or depth > 8:
                continue
            visited.add(symbol_id)
            found.extend(api_links_by_caller.get(symbol_id, []))
            if depth < 8:
                queue.extend((target, depth + 1) for target in sorted(calls_by_caller.get(symbol_id, [])))
        for link in found:
            if link.get("resolution_status") not in ("EXACT_MATCH", "TEMPLATE_MATCH", "VALUE_MATCH"):
                continue
            frontend_business_chains.append({
                "source_file": binding.get("source_file"), "event_line": binding.get("line"),
                "event_name": binding.get("event_name"), "handler_symbol_id": start,
                "endpoint_node_id": link.get("endpoint_node_id"),
                "api_call_node_id": link.get("api_call_node_id"), "method": link.get("method"),
                "path": link.get("path"), "api_call_file": link.get("source_file"),
                "api_call_line": link.get("line"), "resolution_status": link.get("resolution_status"),
                "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
                "call_graph_depth_limit": 8,
            })
    frontend_business_chains.sort(key=lambda item: (
        str(item.get("source_file") or ""), int(item.get("event_line") or 0),
        str(item.get("api_call_file") or ""), int(item.get("api_call_line") or 0),
        str(item.get("method") or ""), str(item.get("path") or ""),
    ))
    endpoint_execution_paths = _build_endpoint_execution_paths(nodes)

    # S2.14: endpoint-centred functional slices; never infer PRD feature relations.
    node_by_id = {item.get("id"): item for item in nodes if isinstance(item, Mapping)}
    entity_links_by_access: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        if edge.get("method") == "database-access-to-documented-entity" and edge.get("verification_status") == "NOT_VERIFIED":
            entity_links_by_access.setdefault(str(edge.get("source")), []).append({
                "entity_node_id": edge.get("target"),
                "entity_name": node_by_id.get(edge.get("target"), {}).get("label"),
                "evidence": edge.get("evidence") if isinstance(edge.get("evidence"), Mapping) else {},
                "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
            })
    frontend_by_endpoint: dict[str, list[dict[str, Any]]] = {}
    for chain in frontend_business_chains:
        if isinstance(chain.get("endpoint_node_id"), str):
            frontend_by_endpoint.setdefault(chain["endpoint_node_id"], []).append(chain)
    business_function_views = []
    for flow in endpoint_execution_paths:
        endpoint_id = flow.get("endpoint_id")
        frontend = sorted(frontend_by_endpoint.get(str(endpoint_id), []), key=lambda item: (
            str(item.get("source_file") or ""), int(item.get("event_line") or 0),
            str(item.get("api_call_file") or ""), int(item.get("api_call_line") or 0)))
        entity_links = []
        for access in flow.get("database_accesses", []):
            if isinstance(access, Mapping):
                entity_links.extend(entity_links_by_access.get(str(access.get("database_access_id")), []))
        unique_entities = {(item.get("entity_node_id"), json.dumps(item.get("evidence", {}), sort_keys=True)): item for item in entity_links}
        entity_links = [unique_entities[k] for k in sorted(unique_entities, key=lambda x: (str(x[0]), x[1]))]
        stages = flow.get("stage_status") if isinstance(flow.get("stage_status"), Mapping) else {}
        identity = {"endpoint_id": endpoint_id, "method": flow.get("method"), "endpoint": flow.get("endpoint"), "source": flow.get("source")}
        business_function_views.append({
            "business_function_id": _stable_id("business-function-view", identity),
            "basis": "ENDPOINT_CENTRED_STATIC_SLICE",
            "endpoint_id": endpoint_id, "method": flow.get("method"), "endpoint": flow.get("endpoint"),
            "source": flow.get("source"), "frontend_entrypoints": frontend,
            "frontend_entrypoint_count": len(frontend), "handler": flow.get("handler"),
            "call_chain": flow.get("call_chain", []), "database_accesses": flow.get("database_accesses", []),
            "documented_entity_links": entity_links, "authorization": flow.get("authorization"),
            "identity_resource_flow": flow.get("identity_resource_flow", []), "stage_status": dict(stages),
            "coverage": {"frontend_static_linkage": "LINKED" if frontend else "NOT_LINKED",
                         "database_entity_linkage": "LINKED" if entity_links else "NOT_LINKED_OR_UNKNOWN",
                         "test_design": "NOT_ASSESSED_BY_GRAPH", "test_execution": "NOT_MEASURED"},
            "unlinked_requirements": ["PRD feature ↔ endpoint mapping: NOT_LINKED_NO_EXPLICIT_FEATURE_ENDPOINT_RELATION",
                                      "Runtime page reachability and event execution: UNKNOWN",
                                      "Executed test coverage: NOT_MEASURED"],
            "confidence": "STATIC_INFERENCE", "verification_status": "NOT_VERIFIED",
        })
    business_function_views.sort(key=lambda item: (
        str(item.get("source", {}).get("path") or ""), int(item.get("source", {}).get("line") or 0),
        str(item.get("endpoint") or ""), str(item.get("business_function_id") or "")))
    documented_feature_linkage = [{
        "feature_name": item.get("name"), "module": item.get("module"), "source": item.get("source"),
        "line": item.get("line"), "linkage_status": "NOT_LINKED_NO_EXPLICIT_FEATURE_ENDPOINT_RELATION",
        "confidence": "UNKNOWN", "verification_status": "NOT_VERIFIED",
    } for item in semantics.get("features", []) if isinstance(item, Mapping)]
    endpoint_contract_alignment = semantics.get("endpoint_contract_alignment", [])
    if not isinstance(endpoint_contract_alignment, list):
        raise ValueError("semantics.endpoint_contract_alignment must be a list")
    result["nodes"] = nodes
    result["edges"] = edges
    result["business_understanding"] = {
        "frontend_interaction_bindings": frontend_interaction_bindings,
        "frontend_interaction_binding_count": len(frontend_interaction_bindings),
        "frontend_api_endpoint_links": api_endpoint_links,
        "frontend_api_endpoint_link_count": len(api_endpoint_links),
        "frontend_business_chains": frontend_business_chains,
        "frontend_business_chain_count": len(frontend_business_chains),
        "business_function_views": business_function_views,
        "business_function_view_count": len(business_function_views),
        "documented_feature_linkage": documented_feature_linkage,
        "documented_feature_linkage_count": len(documented_feature_linkage),
        "endpoint_execution_paths": endpoint_execution_paths,
        "endpoint_execution_path_count": len(endpoint_execution_paths),
        "endpoint_contract_alignment_count": len(endpoint_contract_alignment),
        "endpoint_contract_alignment": [
            dict(item) for item in endpoint_contract_alignment if isinstance(item, Mapping)
        ],
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
        "identity_evidence_records": identity_linked,
        "identity_evidence_unlinked": identity_unlinked,
        "identity_evidence_owner_scope_links": identity_owner_scope_links,
        "authorization_check_records": authorization_linked,
        "authorization_check_unowned": authorization_unowned,
        "limitations": [
            "业务记录只与精确来源文件关联；命名路由 handler 可通过同文件唯一符号或 TypeScript Compiler API 的导入符号证据建立候选引用。",
            "跨文件 handler 仅在编译器符号解析唯一且目标符号存在于当前图谱时关联；动态、外部、歧义或缺少符号证据时保留原状态，不按名称猜测。",
            "未建立功能到接口、接口到数据实体、角色到权限的推断关系。",
            "前端交互目前仅识别 JSX/TSX 的有限事件属性与唯一命名 handler；尚未连接前端 handler 到 API client 和后端 endpoint，也未覆盖 Vue、Angular、XML 或 Jetpack Compose UI 模型。",
            "candidate/verified=false 表示未验证，不代表运行时可达或功能已实现。",
            "调用链仅沿唯一 RESOLVED 的图谱符号边进行有界静态展开；循环会标记，深度/数量上限会截断，不证明运行时执行。",
            "数据库访问只识别字面量表达式（隐式拼接、`+` 链、多行与模板字面量；动态插值以 `?` 占位，仅静态骨架参与解析）；变量/调用/`.format()`/`%`/ORM/跨行变量传播仍不解析；表/列解析仅为静态候选，不证明实际读写、事务提交或字段数据流。",
            "权限证据仅识别有限的 route middleware 名称、显式权限调用名和简单 role/owner 条件；函数权限调用只有在同文件同调用行的唯一 caller_symbol_id 存在时才归属函数。未检测到权限线索不证明缺少授权，检测到线索也不证明运行时执行或检查正确。",
            "身份/资源数据流仅识别有限同一源码行模式与同一 Python 函数归属；多行数据库调用按括号配平拼接后判定 sink（至多 10 行，记录落调用起始行）；同行附着之外，handler 级身份线索（身份来源、请求控制身份输入、属主比较）在函数归属已解析时附着到同函数的数据库访问（SAME_FUNCTION_SCOPE，候选关联）；sink 绑定仍只认同行；JS/TS 暂无函数归属。未做跨函数返回值传播、ORM 映射或控制流分析；同行/同函数模式不证明运行时身份可信或资源授权正确。",
        ],
    }
    return result
