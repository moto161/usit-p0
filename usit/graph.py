"""Validation helpers for the backward-compatible usit-graph-v1 format."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .snapshot import LineCountSnapshot
from .safeio import read_bounded_no_follow

GRAPH_SCHEMA = "usit-graph-v1"
RELATION_TYPES = {"defines", "imports", "calls", "references"}
NODE_TYPES = {
    "file", "module", "class", "function", "method", "interface", "type",
    "object", "struct", "enum", "trait", "dependency", "unresolved",
    "candidate", "external",
}
CONFIDENCES = {"high", "medium", "low", "unknown"}
RESOLUTION_STATES = {"RESOLVED", "UNRESOLVED", "AMBIGUOUS", "DYNAMIC", "EXTERNAL", "UNKNOWN"}
SYMBOL_TYPES = {
    "class", "function", "method", "interface", "type", "object", "struct",
    "enum", "trait",
}


def _diagnostic(severity: str, code: str, location: str, reason: str,
                recommendation: str, relation_id: str | None = None) -> dict[str, Any]:
    item = {
        "severity": severity,
        "code": code,
        "location": location,
        "reason": reason,
        "recommendation": recommendation,
    }
    if relation_id is not None:
        item["relation_id"] = relation_id
    return item


def _path_issue(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return "path must be a non-empty string"
    path = PurePosixPath(value)
    win_path = PureWindowsPath(value)
    if path.is_absolute() or win_path.drive or win_path.root or ".." in path.parts:
        return "path must stay within the project and be relative"
    if "\\" in value or str(path) != value or value in {".", ""}:
        return "path must use normalized project-relative POSIX form"
    return None


def _project_path(project_root: str | Path, relative_path: str) -> tuple[Path | None, str | None]:
    root = Path(project_root).resolve()
    try:
        candidate = (root / Path(*PurePosixPath(relative_path).parts)).resolve()
    except OSError:
        return None, "unresolvable"
    if not candidate.is_relative_to(root):
        return None, "escape"
    return candidate, None


def _source_path(graph: dict[str, Any], node: dict[str, Any]) -> str | None:
    evidence = node.get("evidence")
    if isinstance(evidence, dict) and isinstance(evidence.get("path"), str):
        return evidence["path"]
    node_id = node.get("id")
    if node.get("type") == "file" and isinstance(node_id, str) and node_id.startswith("file:"):
        return node_id[5:]
    return None


def validate_graph(graph: Any, project_root: str | Path | None = None,
                   *, line_counts: dict[str, int] | None = None) -> dict[str, Any]:
    """Validate graph structure and evidence without mutating or repairing it.

    ``line_counts`` is an optional mapping of project-relative path to line count.
    When supplied it is authoritative and NO project file is opened for line
    checks.  When omitted, the historical by-path behaviour is preserved for
    direct callers such as the validation unit tests, which operate on temporary
    copies.

    .. warning::
       P0.7.10-Z: ``line_counts`` is a bare mapping and therefore carries **no
       provenance**.  A caller can pass an arbitrary or stale mapping and thereby
       steer the verdict (see ``SNAPSHOT_TRUST_BOUNDARY.md``).  Code that needs a
       trustworthy line check must call :func:`validate_graph_trusted` with a
       snapshot obtained from :func:`usit.snapshot.capture`.  The historical
       signature and behaviour of this function are intentionally unchanged.
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    if not isinstance(graph, dict):
        errors.append(_diagnostic("ERROR", "ROOT_TYPE", "$", "graph root must be an object",
                                  "Provide a JSON object using usit-graph-v1."))
        return _result(graph, errors, warnings, unknown, [], [], project_root)

    required = {"schema_version": str, "nodes": list, "edges": list}
    for key, expected in required.items():
        if key not in graph:
            errors.append(_diagnostic("ERROR", "MISSING_FIELD", f"$.{key}", f"required field {key!r} is missing",
                                      "Regenerate the graph with a supported USIT version."))
        elif not isinstance(graph[key], expected):
            errors.append(_diagnostic("ERROR", "FIELD_TYPE", f"$.{key}", f"{key} must be {expected.__name__}",
                                      "Correct the graph structure or regenerate it."))
    if isinstance(graph.get("schema_version"), str) and graph["schema_version"] != GRAPH_SCHEMA:
        errors.append(_diagnostic("ERROR", "SCHEMA_VERSION", "$.schema_version",
                                  f"unsupported schema version {graph['schema_version']!r}",
                                  f"Migrate to {GRAPH_SCHEMA}; no automatic migration was applied."))
    for key in ("edge_type_counts", "node_type_counts"):
        if key in graph and not isinstance(graph[key], dict):
            errors.append(_diagnostic("ERROR", "FIELD_TYPE", f"$.{key}", f"{key} must be an object",
                                      "Regenerate the graph with a supported USIT version."))
    if "call_relation_stats" in graph and not isinstance(graph["call_relation_stats"], dict):
        errors.append(_diagnostic("ERROR", "FIELD_TYPE", "$.call_relation_stats",
                                  "call_relation_stats must be an object", "Regenerate the graph."))
    if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        return _result(graph, errors, warnings, unknown, [], [], project_root)

    nodes = graph["nodes"]
    edges = graph["edges"]
    if not nodes and not edges:
        warnings.append(_diagnostic("WARNING", "EMPTY_GRAPH", "$", "graph contains no nodes or edges",
                                    "Confirm the project root and supported file types."))

    node_index: dict[str, dict[str, Any]] = {}
    file_paths: set[str] = set()
    defines: set[tuple[str, str]] = set()
    for index, node in enumerate(nodes):
        loc = f"$.nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(_diagnostic("ERROR", "NODE_TYPE", loc, "node must be an object", "Regenerate the graph."))
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            errors.append(_diagnostic("ERROR", "NODE_ID", f"{loc}.id", "node id must be a non-empty string",
                                      "Assign each node a stable non-empty ID."))
        elif node_id in node_index:
            errors.append(_diagnostic("ERROR", "DUPLICATE_NODE_ID", f"{loc}.id",
                                      f"duplicate node ID {node_id!r}", "Make node IDs unique."))
        else:
            node_index[node_id] = node
        node_type = node.get("type")
        if not isinstance(node_type, str) or not node_type:
            errors.append(_diagnostic("ERROR", "NODE_TYPE_FIELD", f"{loc}.type", "node type must be a non-empty string",
                                      "Set a supported node type."))
        elif node_type not in NODE_TYPES:
            warnings.append(_diagnostic("WARNING", "UNKNOWN_NODE_TYPE", f"{loc}.type",
                                        f"node type {node_type!r} is not recognized", "Review the node type or update the validator."))
        if not isinstance(node.get("label"), str):
            errors.append(_diagnostic("ERROR", "NODE_LABEL", f"{loc}.label", "node label must be a string",
                                      "Provide a display label."))
        if not isinstance(node.get("evidence"), dict):
            errors.append(_diagnostic("ERROR", "NODE_EVIDENCE", f"{loc}.evidence", "node evidence must be an object",
                                      "Attach source evidence or an explicit note."))
            continue
        ev = node["evidence"]
        path_value = _source_path(graph, node)
        if node_type == "file":
            path_value = ev.get("path")
            if node_id != f"file:{path_value}":
                errors.append(_diagnostic("ERROR", "FILE_NODE_ID_PATH", f"{loc}.id",
                                          "file node ID must match evidence.path", "Regenerate the file node from its normalized path."))
        if path_value is not None:
            issue = _path_issue(path_value)
            if issue:
                errors.append(_diagnostic("ERROR", "INVALID_PATH", f"{loc}.evidence.path", issue,
                                          "Use a normalized relative path that remains inside the project."))
            else:
                if node_type == "file":
                    file_paths.add(path_value)
                if project_root is not None:
                    candidate, path_error = _project_path(project_root, path_value)
                    if path_error == "escape":
                        errors.append(_diagnostic("ERROR", "PATH_ESCAPE", f"{loc}.evidence.path",
                                                  f"referenced path {path_value!r} resolves outside the project root",
                                                  "Remove the external reference and scan only the project tree."))
                    elif candidate is None or not candidate.is_file():
                        errors.append(_diagnostic("ERROR", "FILE_REFERENCE_MISSING", f"{loc}.evidence.path",
                                                  f"referenced project file {path_value!r} does not exist",
                                                  "Regenerate the graph from the same project snapshot."))
        if node_type in SYMBOL_TYPES:
            if path_value is None:
                errors.append(_diagnostic("ERROR", "SYMBOL_PATH_MISSING", f"{loc}.evidence.path",
                                          "symbol node has no source file path", "Attach the definition file path."))
        if "line" in ev:
            line = ev.get("line")
            if not isinstance(line, int) or isinstance(line, bool) or line < 1:
                errors.append(_diagnostic("ERROR", "INVALID_LINE", f"{loc}.evidence.line",
                                          "node evidence line must be a positive integer", "Use the one-based source line."))
            elif line_counts is not None and path_value is not None:
                # P0.7.10-V: trusted snapshot mapping is authoritative; no file
                # is opened.  A missing entry is reported, never read from disk.
                if path_value not in line_counts:
                    unknown.append(_diagnostic("UNKNOWN", "NODE_LINE_UNVERIFIED", f"{loc}.evidence.line",
                                               f"no captured line count for {path_value!r}",
                                               "Validate against a scan artifact that carries source_line_counts."))
                elif line > line_counts[path_value]:
                    errors.append(_diagnostic("ERROR", "LINE_OUT_OF_RANGE", f"{loc}.evidence.line",
                                              f"line {line} exceeds {path_value!r} ({line_counts[path_value]} lines)",
                                              "Regenerate the node with the correct source line."))
            elif project_root is not None and path_value is not None:
                source_file, _ = _project_path(project_root, path_value)
                if source_file is not None and source_file.is_file():
                    try:
                        count = len(source_file.read_text(encoding="utf-8", errors="replace").splitlines())
                        if line > count:
                            errors.append(_diagnostic("ERROR", "LINE_OUT_OF_RANGE", f"{loc}.evidence.line",
                                                      f"line {line} exceeds {path_value!r} ({count} lines)",
                                                      "Regenerate the node with the correct source line."))
                    except OSError:
                        unknown.append(_diagnostic("UNKNOWN", "NODE_LINE_UNVERIFIED", f"{loc}.evidence.line",
                                                   "source file could not be read to verify the node line",
                                                   "Check file access and rerun validation."))

    edge_ids: set[str] = set()
    edge_rows: list[tuple[str, dict[str, Any], dict[str, Any], str]] = []
    edge_count_by_type: dict[str, int] = {}
    node_count_by_type: dict[str, int] = {}
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("type"), str):
            node_count_by_type[node["type"]] = node_count_by_type.get(node["type"], 0) + 1
    for index, edge in enumerate(edges):
        ref = f"edge:{index + 1:06d}"
        loc = f"$.edges[{index}]"
        if not isinstance(edge, dict):
            errors.append(_diagnostic("ERROR", "EDGE_TYPE", loc, "edge must be an object", "Regenerate the graph."))
            continue
        if "id" in edge:
            edge_id = edge["id"]
            if not isinstance(edge_id, str) or not edge_id:
                errors.append(_diagnostic("ERROR", "EDGE_ID", f"{loc}.id", "edge id must be a non-empty string",
                                          "Assign a unique edge ID or omit it for legacy compatibility.", ref))
            elif edge_id in edge_ids:
                errors.append(_diagnostic("ERROR", "DUPLICATE_EDGE_ID", f"{loc}.id", f"duplicate edge ID {edge_id!r}",
                                          "Make edge IDs unique.", ref))
            else:
                edge_ids.add(edge_id)
            ref = edge_id if isinstance(edge_id, str) else ref
        source_id, target_id = edge.get("source"), edge.get("target")
        source = node_index.get(source_id) if isinstance(source_id, str) else None
        target = node_index.get(target_id) if isinstance(target_id, str) else None
        if source is None:
            errors.append(_diagnostic("ERROR", "MISSING_SOURCE", f"{loc}.source",
                                      f"source node {source_id!r} does not exist", "Add the referenced source node or correct the edge.", ref))
        if target is None:
            errors.append(_diagnostic("ERROR", "MISSING_TARGET", f"{loc}.target",
                                      f"target node {target_id!r} does not exist", "Add the referenced target node or correct the edge.", ref))
        rel_type = edge.get("type")
        if not isinstance(rel_type, str) or rel_type not in RELATION_TYPES:
            errors.append(_diagnostic("ERROR", "RELATION_TYPE", f"{loc}.type",
                                      f"unsupported relation type {rel_type!r}", "Use a documented relation type.", ref))
        else:
            edge_count_by_type[rel_type] = edge_count_by_type.get(rel_type, 0) + 1
        evidence = edge.get("evidence")
        if not isinstance(evidence, dict):
            errors.append(_diagnostic("ERROR", "EDGE_EVIDENCE", f"{loc}.evidence", "edge evidence must be an object",
                                      "Attach source path and line evidence.", ref))
        else:
            path_value, line = evidence.get("path"), evidence.get("line")
            issue = _path_issue(path_value)
            if issue:
                errors.append(_diagnostic("ERROR", "EDGE_EVIDENCE_PATH", f"{loc}.evidence.path", issue,
                                          "Use a normalized project-relative source path.", ref))
            elif path_value not in file_paths:
                errors.append(_diagnostic("ERROR", "EDGE_FILE_REFERENCE", f"{loc}.evidence.path",
                                          f"evidence path {path_value!r} has no file node", "Add its file node or correct the evidence path.", ref))
            elif project_root is not None:
                candidate, path_error = _project_path(project_root, path_value)
                if path_error == "escape":
                    errors.append(_diagnostic("ERROR", "PATH_ESCAPE", f"{loc}.evidence.path",
                                              f"evidence path {path_value!r} resolves outside the project root",
                                              "Use evidence located inside the project root.", ref))
                elif candidate is None or not candidate.is_file():
                    errors.append(_diagnostic("ERROR", "EDGE_FILE_MISSING", f"{loc}.evidence.path",
                                              f"evidence file {path_value!r} does not exist", "Regenerate the graph from the same project snapshot.", ref))
            if not isinstance(line, int) or isinstance(line, bool) or line < 1:
                errors.append(_diagnostic("ERROR", "INVALID_LINE", f"{loc}.evidence.line",
                                          "relationship line must be a positive integer", "Use a one-based source line.", ref))
            elif line_counts is not None and isinstance(path_value, str):
                # P0.7.10-V: same snapshot-first rule for edge evidence.
                if path_value not in line_counts:
                    unknown.append(_diagnostic("UNKNOWN", "LINE_UNVERIFIED", f"{loc}.evidence.line",
                                               f"no captured line count for {path_value!r}",
                                               "Validate against a scan artifact that carries source_line_counts.", ref))
                elif line > line_counts[path_value]:
                    errors.append(_diagnostic("ERROR", "LINE_OUT_OF_RANGE", f"{loc}.evidence.line",
                                              f"line {line} exceeds {path_value!r} ({line_counts[path_value]} lines)",
                                              "Regenerate the relationship with the correct source line.", ref))
            elif project_root is not None and isinstance(path_value, str):
                source_file, _ = _project_path(project_root, path_value)
                if source_file is not None and source_file.is_file():
                    try:
                        line_count = len(source_file.read_text(encoding="utf-8", errors="replace").splitlines())
                        if line > line_count:
                            errors.append(_diagnostic("ERROR", "LINE_OUT_OF_RANGE", f"{loc}.evidence.line",
                                                      f"line {line} exceeds {path_value!r} ({line_count} lines)",
                                                      "Regenerate the relationship with the correct source line.", ref))
                    except OSError:
                        unknown.append(_diagnostic("UNKNOWN", "LINE_UNVERIFIED", f"{loc}.evidence.line",
                                                   "source file could not be read to verify the line", "Check file access and rerun validation.", ref))
            if source is not None and source.get("type") != "file":
                errors.append(_diagnostic("ERROR", "RELATION_SOURCE_NOT_FILE", f"{loc}.source",
                                          "relationship source must be a file node in graph v1", "Link the relationship from its source file.", ref))
            elif source is not None and isinstance(path_value, str) and _source_path(graph, source) != path_value:
                errors.append(_diagnostic("ERROR", "RELATION_SOURCE_PATH_MISMATCH", f"{loc}.evidence.path",
                                          "relationship evidence path does not match its source file node",
                                          "Use the file path that contains the relationship evidence.", ref))
        for field in ("method", "confidence"):
            value = edge.get(field)
            if not isinstance(value, str) or not value:
                errors.append(_diagnostic("ERROR", "RELATION_PROVENANCE", f"{loc}.{field}",
                                          f"relationship {field} is required", "Record the analysis method and confidence.", ref))
        confidence = edge.get("confidence")
        if isinstance(confidence, str) and confidence not in CONFIDENCES:
            unknown.append(_diagnostic("UNKNOWN", "UNKNOWN_CONFIDENCE", f"{loc}.confidence",
                                       f"unrecognized confidence {confidence!r}", "Use high, medium, low, or unknown.", ref))
        if rel_type == "calls":
            candidate_targets = edge.get("candidate_targets")
            if candidate_targets is not None:
                if not isinstance(candidate_targets, list) or any(not isinstance(item, str) or item not in node_index for item in candidate_targets):
                    errors.append(_diagnostic("ERROR", "INVALID_CANDIDATE_TARGETS", f"{loc}.candidate_targets",
                                              "candidate_targets must list existing node IDs",
                                              "Retain only candidate symbol IDs present in the graph.", ref))
            resolution = edge.get("resolution_status")
            explicit_resolution = resolution is not None
            legacy_resolved = edge.get("resolved")
            if not isinstance(legacy_resolved, bool):
                errors.append(_diagnostic("ERROR", "CALL_RESOLVED_TYPE", f"{loc}.resolved",
                                          "legacy calls.resolved must remain a boolean", "Set it from the explicit resolution status.", ref))
            if resolution is None:
                resolution = "UNKNOWN"
                unknown.append(_diagnostic("UNKNOWN", "LEGACY_RESOLUTION_STATE", f"{loc}.resolution_status",
                                           "legacy call has no explicit resolution state; it is not counted as resolved",
                                           "Regenerate with explicit resolution_status.", ref))
            elif resolution not in RESOLUTION_STATES:
                unknown.append(_diagnostic("UNKNOWN", "UNKNOWN_RELATION_STATUS", f"{loc}.resolution_status",
                                           f"unrecognized relation state {resolution!r}", "Use a supported resolution status; do not count this call as resolved.", ref))
                resolution = "UNKNOWN"
            if explicit_resolution and isinstance(legacy_resolved, bool) and legacy_resolved != (resolution == "RESOLVED"):
                errors.append(_diagnostic("ERROR", "RESOLUTION_CONTRADICTION", f"{loc}.resolved",
                                          "resolved must be true exactly when resolution_status is RESOLVED",
                                          "Keep the legacy boolean synchronized with the explicit state.", ref))
            if "argument_binding_status" in edge or "argument_bindings" in edge:
                binding_status = edge.get("argument_binding_status")
                bindings = edge.get("argument_bindings")
                if resolution != "RESOLVED":
                    errors.append(_diagnostic("ERROR", "ARGUMENT_BINDING_ON_UNRESOLVED_CALL",
                                              f"{loc}.argument_binding_status",
                                              "argument mappings are valid only for uniquely RESOLVED calls",
                                              "Remove argument mapping evidence unless the call target is RESOLVED.", ref))
                if binding_status not in {"EXACT_STATIC_MAPPING", "UNSUPPORTED_ARGUMENT_SHAPE"}:
                    errors.append(_diagnostic("ERROR", "INVALID_ARGUMENT_BINDING_STATUS",
                                              f"{loc}.argument_binding_status",
                                              "argument binding status is missing or unsupported",
                                              "Use EXACT_STATIC_MAPPING or UNSUPPORTED_ARGUMENT_SHAPE.", ref))
                if not isinstance(bindings, list):
                    errors.append(_diagnostic("ERROR", "INVALID_ARGUMENT_BINDINGS",
                                              f"{loc}.argument_bindings",
                                              "argument_bindings must be an array",
                                              "Emit an array, including an empty array when no arguments are present.", ref))
                elif binding_status == "UNSUPPORTED_ARGUMENT_SHAPE" and bindings:
                    errors.append(_diagnostic("ERROR", "PARTIAL_UNSUPPORTED_ARGUMENT_BINDINGS",
                                              f"{loc}.argument_bindings",
                                              "unsupported argument shapes must not publish partial mappings",
                                              "Clear argument_bindings when the call shape is unsupported.", ref))
                elif isinstance(bindings, list):
                    seen_parameters: set[str] = set()
                    for binding_index, binding in enumerate(bindings):
                        binding_loc = f"{loc}.argument_bindings[{binding_index}]"
                        if not isinstance(binding, dict):
                            errors.append(_diagnostic("ERROR", "INVALID_ARGUMENT_BINDING",
                                                      binding_loc, "argument binding must be an object",
                                                      "Emit a structured argument-to-parameter mapping.", ref))
                            continue
                        arg_location = binding.get("argument_location")
                        param_location = binding.get("parameter_declaration")
                        valid = (
                            binding.get("argument_kind") in {"POSITIONAL", "KEYWORD"}
                            and isinstance(binding.get("argument_expression"), str)
                            and isinstance(binding.get("parameter_name"), str)
                            and bool(binding.get("parameter_name"))
                            and isinstance(binding.get("parameter_index"), int)
                            and not isinstance(binding.get("parameter_index"), bool)
                            and binding.get("parameter_index") >= 0
                            and isinstance(arg_location, dict)
                            and arg_location.get("path") == (evidence.get("path") if isinstance(evidence, dict) else None)
                            and isinstance(arg_location.get("line"), int)
                            and not isinstance(arg_location.get("line"), bool)
                            and arg_location.get("line") > 0
                            and isinstance(param_location, dict)
                            and isinstance(param_location.get("path"), str)
                            and bool(param_location.get("path"))
                            and isinstance(param_location.get("line"), int)
                            and not isinstance(param_location.get("line"), bool)
                            and param_location.get("line") > 0
                        )
                        if not valid:
                            errors.append(_diagnostic("ERROR", "MALFORMED_ARGUMENT_BINDING",
                                                      binding_loc,
                                                      "argument/parameter mapping lacks valid expression or source locations",
                                                      "Retain exact call-site and parameter-declaration evidence.", ref))
                        parameter_name = binding.get("parameter_name")
                        if isinstance(parameter_name, str):
                            if parameter_name in seen_parameters:
                                errors.append(_diagnostic("ERROR", "DUPLICATE_ARGUMENT_PARAMETER_BINDING",
                                                          binding_loc,
                                                          "multiple explicit arguments map to the same parameter",
                                                          "Reject duplicate or conflicting parameter mappings.", ref))
                            seen_parameters.add(parameter_name)
            if (resolution == "RESOLVED" or (not explicit_resolution and legacy_resolved is True)) and target is not None:
                target_type = target.get("type")
                definition_path = _source_path(graph, target)
                if target_type not in SYMBOL_TYPES:
                    errors.append(_diagnostic("ERROR", "RESOLVED_TARGET_NOT_SYMBOL", f"{loc}.target",
                                              "resolved call target is not a symbol node", "Link to the actual symbol definition node.", ref))
                if definition_path is None or not isinstance(target.get("evidence", {}).get("line"), int):
                    errors.append(_diagnostic("ERROR", "RESOLVED_TARGET_EVIDENCE", f"{loc}.target",
                                              "resolved call target lacks definition path/line evidence", "Attach the target definition location.", ref))
                if isinstance(target_id, str) and source_id and not any(
                    isinstance(other, dict) and other.get("type") == "defines" and other.get("source") == f"file:{definition_path}" and other.get("target") == target_id
                    for other in edges
                ):
                    errors.append(_diagnostic("ERROR", "RESOLVED_TARGET_UNDEFINED", f"{loc}.target",
                                              "resolved target has no defines relation from its definition file",
                                              "Link calls only to a symbol with a matching defines edge.", ref))
                caller_path = evidence.get("path") if isinstance(evidence, dict) else None
                if definition_path and caller_path and definition_path != caller_path:
                    import_evidence = evidence.get("import_evidence") if isinstance(evidence, dict) else None
                    import_line = import_evidence.get("line") if isinstance(import_evidence, dict) else None
                    import_path = import_evidence.get("path") if isinstance(import_evidence, dict) else None
                    if import_path != caller_path or not isinstance(import_line, int) or isinstance(import_line, bool) or import_line < 1:
                        errors.append(_diagnostic("ERROR", "CROSS_MODULE_IMPORT_EVIDENCE", f"{loc}.evidence.import_evidence",
                                                  "cross-file resolved call lacks a valid import-site path and line",
                                                  "Attach the explicit import statement that binds the target symbol.", ref))
                    elif not any(isinstance(other, dict) and other.get("type") == "imports" and other.get("source") == source_id
                                 and isinstance(other.get("evidence"), dict) and other["evidence"].get("path") == caller_path
                                 and other["evidence"].get("line") == import_line for other in edges):
                        errors.append(_diagnostic("ERROR", "CROSS_MODULE_IMPORT_EDGE_MISSING", f"{loc}.evidence.import_evidence",
                                                  "import evidence has no corresponding imports relation",
                                                  "Retain the import relation that supports cross-module resolution.", ref))
            elif resolution in {"UNRESOLVED", "AMBIGUOUS", "DYNAMIC", "EXTERNAL", "UNKNOWN"}:
                if resolution in {"AMBIGUOUS", "DYNAMIC", "UNKNOWN"}:
                    unknown.append(_diagnostic("UNKNOWN", f"CALL_{resolution}", loc,
                                               f"call resolution state is {resolution}", "Keep the call visible and do not count it as resolved.", ref))
        elif "resolved" in edge or "resolution_status" in edge:
            errors.append(_diagnostic("ERROR", "RESOLUTION_ON_NON_CALL", f"{loc}.resolved",
                                      "resolution fields are only defined for calls relations",
                                      "Remove call-resolution fields from this relation type.", ref))
        edge_rows.append((ref, edge, source or {}, target or {}))

    if "edge_type_counts" in graph and graph["edge_type_counts"] != edge_count_by_type:
        errors.append(_diagnostic("ERROR", "EDGE_COUNT_MISMATCH", "$.edge_type_counts",
                                  "stored edge counts do not match the edges array", "Regenerate summary counts from the graph."))
    if "node_type_counts" in graph and graph["node_type_counts"] != node_count_by_type:
        errors.append(_diagnostic("ERROR", "NODE_COUNT_MISMATCH", "$.node_type_counts",
                                  "stored node counts do not match the nodes array", "Regenerate summary counts from the graph."))
    return _result(graph, errors, warnings, unknown, edge_rows, nodes, project_root)


def _result(graph: Any, errors: list[dict[str, Any]], warnings: list[dict[str, Any]],
            unknown: list[dict[str, Any]], edge_rows: list[Any], nodes: list[Any],
            project_root: str | Path | None) -> dict[str, Any]:
    edges = graph.get("edges", []) if isinstance(graph, dict) and isinstance(graph.get("edges"), list) else []
    node_count = len(nodes) if isinstance(nodes, list) else 0
    edge_count = len(edges)
    node_ids = {n.get("id") for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)}
    endpoint_valid = sum(1 for e in edges if isinstance(e, dict) and e.get("source") in node_ids and e.get("target") in node_ids)
    evidence_complete = sum(1 for e in edges if isinstance(e, dict) and isinstance(e.get("evidence"), dict)
                            and isinstance(e["evidence"].get("path"), str) and isinstance(e["evidence"].get("line"), int)
                            and isinstance(e.get("method"), str) and bool(e.get("method"))
                            and isinstance(e.get("confidence"), str) and bool(e.get("confidence")))
    calls = [e for e in edges if isinstance(e, dict) and e.get("type") == "calls"]
    resolution_counts = {state: 0 for state in RESOLUTION_STATES}
    for edge in calls:
        state = edge.get("resolution_status")
        if state is None:
            state = "UNKNOWN"
        if state not in RESOLUTION_STATES:
            state = "UNKNOWN"
        resolution_counts[state] += 1
    status = "INVALID" if errors else ("VALID_WITH_ISSUES" if warnings or unknown else "VALID")
    quality = {
        "nodes_total": node_count,
        "edges_total": edge_count,
        "valid_endpoints": endpoint_valid,
        "endpoint_valid_rate": endpoint_valid / edge_count if edge_count else None,
        "endpoint_valid_denominator": edge_count,
        "evidence_complete_edges": evidence_complete,
        "evidence_complete_rate": evidence_complete / edge_count if edge_count else None,
        "evidence_complete_denominator": edge_count,
        "calls_total": len(calls),
        "resolved_calls": resolution_counts["RESOLVED"],
        "unresolved_calls": resolution_counts["UNRESOLVED"],
        "ambiguous_calls": resolution_counts["AMBIGUOUS"],
        "dynamic_calls": resolution_counts["DYNAMIC"],
        "external_calls": resolution_counts["EXTERNAL"],
        "unknown_calls": resolution_counts["UNKNOWN"],
        "resolution_counts_denominator": len(calls),
        "errors": len(errors),
        "warnings": len(warnings),
        "unknown": len(unknown),
    }
    return {
        "schema_version": "usit-graph-validation-v1",
        "graph_schema_version": graph.get("schema_version") if isinstance(graph, dict) else None,
        "status": status,
        "is_valid": not errors,
        "project_root": str(Path(project_root).resolve()) if project_root is not None else None,
        "quality": quality,
        "errors": errors,
        "warnings": warnings,
        "unknown": unknown,
    }


def render_validation_markdown(result: dict[str, Any]) -> str:
    q = result["quality"]
    def rate(numerator: int, denominator: int) -> str:
        return f"{numerator}/{denominator} ({numerator / denominator:.1%})" if denominator else "NOT MEASURED (denominator 0)"
    lines = [
        "# USIT Knowledge Graph Validation",
        "",
        f"- Status: **{result['status']}**",
        f"- Graph schema: `{result.get('graph_schema_version')}`",
        "- Validation is read-only; invalid relationships are reported, never repaired or removed.",
        "",
        "## Quality metrics",
        "",
        f"- Nodes: {q['nodes_total']}",
        f"- Edges: {q['edges_total']}",
        f"- Valid endpoints: {rate(q['valid_endpoints'], q['endpoint_valid_denominator'])}",
        f"- Complete edge evidence: {rate(q['evidence_complete_edges'], q['evidence_complete_denominator'])}",
        f"- Calls (denominator {q['resolution_counts_denominator']}): resolved {q['resolved_calls']}; unresolved {q['unresolved_calls']}; ambiguous {q['ambiguous_calls']}; dynamic {q['dynamic_calls']}; external {q['external_calls']}; unknown {q['unknown_calls']}.",
        f"- Diagnostics: {q['errors']} errors; {q['warnings']} warnings; {q['unknown']} unknowns.",
    ]
    for key, title in (("errors", "Errors"), ("warnings", "Warnings"), ("unknown", "Unknown / unverified")):
        lines.extend(["", f"## {title}", ""])
        items = result[key]
        if not items:
            lines.append("None.")
        else:
            for item in items:
                rel = f" ({item['relation_id']})" if "relation_id" in item else ""
                lines.append(f"- `{item['code']}` at `{item['location']}`{rel}: {item['reason']} Recommendation: {item['recommendation']}")
    return "\n".join(lines) + "\n"


def validate_graph_file(graph_path: str | Path, project_root: str | Path | None = None,
                        out_json: str | Path | None = None, out_md: str | Path | None = None,
                        *, trusted_snapshot: Any = None,
                        require_trusted: bool = False) -> dict[str, Any]:
    """Load and validate a graph file; optionally write JSON and Markdown diagnostics.

    Args:
        graph_path: Path to a usit-graph-v1 JSON file.
        project_root: Root used for the path/escape guards and line checks.
        out_json: Optional path to write the JSON result.
        out_md: Optional path to write the Markdown result.
        trusted_snapshot: Optional :class:`usit.snapshot.LineCountSnapshot`.  When
            given the file is validated through :func:`validate_graph_trusted` and
            the embedded ``source_line_counts`` field is ignored.
        require_trusted: When True and no snapshot is supplied, the embedded
            ``source_line_counts`` field is treated as **no evidence at all**: the
            validator receives an empty mapping and every line check degrades to
            ``*_UNVERIFIED``.  Use this to assert "this verdict is not built on a
            JSON field".

    Raises:
        TypeError: ``trusted_snapshot`` is supplied but is not a LineCountSnapshot.

    Note:
        Without ``trusted_snapshot`` or ``require_trusted`` this call keeps its
        historical convenience behaviour: the embedded ``source_line_counts``
        field is used as an **untrusted hint** so the CLI does not re-open the
        project tree.  A graph file is not a trust anchor -- P0.7.10-YR showed the
        field can be edited to suppress a genuine violation.  Pass a snapshot when
        the verdict has to be defensible.
    """
    path = Path(graph_path)
    try:
        graph = json.loads(read_bounded_no_follow(path, 256 * 1024 * 1024).decode("utf-8"))
        if trusted_snapshot is not None:
            result = validate_graph_trusted(graph, trusted_snapshot,
                                            project_root=project_root)
        else:
            # P0.7.10-V: the graph's own counts keep the CLI on the snapshot path.
            # P0.7.10-Z: they are a hint, not provenance -- require_trusted drops
            # them so the verdict cannot rest on an editable JSON field.
            captured = graph.get("source_line_counts") if isinstance(graph, dict) else None
            if require_trusted:
                line_counts: dict[str, int] = {}
            else:
                line_counts = captured if isinstance(captured, dict) else {}
            result = validate_graph(graph, project_root=project_root,
                                    line_counts=line_counts)
    except json.JSONDecodeError as exc:
        result = _result(None, [_diagnostic("ERROR", "INVALID_JSON", f"$@{exc.lineno}:{exc.colno}",
                                            exc.msg, "Fix the JSON syntax and rerun validation.")], [], [], [], [], project_root)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        result = _result(None, [_diagnostic("ERROR", "GRAPH_READ_ERROR", str(path), type(exc).__name__,
                                            "Check the graph path and file access.")], [], [], [], [], project_root)
    if out_json is not None:
        Path(out_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if out_md is not None:
        Path(out_md).write_text(render_validation_markdown(result), encoding="utf-8")
    return result


def validate_graph_trusted(graph: Any, snapshot: Any,
                           project_root: str | Path | None = None) -> dict[str, Any]:
    """Validate a graph against a *trusted* line-count snapshot.

    This is the strict entry point introduced by P0.7.10-Z.  Unlike
    :func:`validate_graph`, it does not accept a bare mapping: the only accepted
    source of line counts is a :class:`usit.snapshot.LineCountSnapshot` produced
    by :func:`usit.snapshot.capture` from bytes the caller had already read
    through its controlled capture path.  The embedded ``source_line_counts``
    field of a graph is **ignored here** -- a value read out of a JSON file is
    not evidence about the file's content.

    No project source content is ever read by this function: the line counts come
    from the snapshot, and the only filesystem access is ``stat``-level metadata
    used to detect replacement.

    Args:
        graph: The usit-graph-v1 graph to validate (any object; malformed graphs
            produce structural diagnostics rather than exceptions).
        snapshot: A live :class:`usit.snapshot.LineCountSnapshot`.
        project_root: Root to validate against.  Defaults to the snapshot's
            capture root; when supplied it must be the same directory the
            snapshot was captured from.

    Returns:
        The same result shape :func:`validate_graph` returns.  Diagnostics are
        unchanged: a file whose identity no longer matches its capture record is
        simply absent from the mapping, which surfaces as
        ``NODE_LINE_UNVERIFIED`` / ``LINE_UNVERIFIED`` rather than as a range
        verdict based on a stale count.

    Raises:
        TypeError: ``snapshot`` is not a :class:`LineCountSnapshot` (this covers
            ``None``, plain dicts, lists, graph fields read from JSON, and
            re-hydrated payloads).
        ValueError: the snapshot was closed, or ``project_root`` is a different
            directory from the one the snapshot was captured from.
    """
    if not isinstance(snapshot, LineCountSnapshot):
        raise TypeError(
            "validate_graph_trusted() requires a usit.snapshot.LineCountSnapshot; "
            f"got {type(snapshot).__name__}. Plain mappings, graph fields and "
            "deserialised JSON are not trusted evidence -- capture a snapshot instead.")
    if snapshot.version != 1:  # SNAPSHOT_VERSION; kept literal to avoid a cycle
        raise ValueError(f"unsupported snapshot version {snapshot.version}")
    if snapshot.state != "open":
        raise ValueError("the snapshot has been closed; capture a new one")

    root = Path(project_root) if project_root is not None else Path(snapshot.root)
    if not snapshot.belongs_to(root):
        raise ValueError(
            "the snapshot was captured from a different project root; refusing to "
            "apply one project's line counts to another project's files")

    # Only files whose capture-time identity still matches are handed over.  A
    # drifted or missing file is omitted, so the validator reports it as
    # unverified instead of range-checking it against a stale count, and never
    # falls back to reading it.
    verified = snapshot.verified_mapping(root)
    return validate_graph(graph, project_root=root, line_counts=verified)
