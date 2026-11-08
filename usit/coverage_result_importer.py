"""Import coverage.py JSON or JaCoCo XML using reviewed line-range mappings."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .safeio import is_reparse, open_no_follow
from .test_report_adapters import parse_coverage_py_json, parse_jacoco_xml, map_line_coverage_to_functions, strict_json_loads

MAX_COVERAGE_BYTES = 50 * 1024 * 1024
MAX_MAPPING_BYTES = 2 * 1024 * 1024
MAX_GRAPH_BYTES = 50 * 1024 * 1024


def _read(path: str | Path, limit: int) -> bytes:
    """Read one bounded regular file through a no-follow descriptor."""
    source = Path(path).expanduser()
    absolute = source if source.is_absolute() else Path.cwd() / source

    # Reject links/reparse points in every named component before resolving.
    # The final open is still descriptor-bound and independently checked.
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        info = cursor.lstat()
        if cursor.is_symlink() or is_reparse(info):
            raise ValueError("input path must not contain symbolic links or reparse points")

    resolved = absolute.resolve(strict=True)
    fd = open_no_follow(resolved)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or is_reparse(before):
            raise ValueError("input must be a regular file")
        if before.st_size > limit:
            raise ValueError(f"input exceeds size limit ({limit} bytes)")

        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise ValueError(f"input exceeds size limit ({limit} bytes)")

        after = os.fstat(fd)
        identity_before = (
            int(before.st_dev), int(before.st_ino), int(before.st_size),
            int(before.st_mtime_ns), int(before.st_ctime_ns),
        )
        identity_after = (
            int(after.st_dev), int(after.st_ino), int(after.st_size),
            int(after.st_mtime_ns), int(after.st_ctime_ns),
        )
        if identity_before != identity_after:
            raise ValueError("input changed while being read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _verify_source_bindings(mapping: dict[str, Any], project_root: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Bind reviewed mapping ranges to exact source bytes beneath an explicit root."""
    root = Path(project_root).expanduser()
    if not root.is_absolute():
        raise ValueError("project_root must be an absolute path")
    # Inspect the root and all its ancestors without following links.
    absolute_root = Path(os.path.abspath(root))
    cursor = Path(absolute_root.anchor)
    for part in absolute_root.parts[1:]:
        cursor = cursor / part
        info = cursor.lstat()
        if cursor.is_symlink() or is_reparse(info):
            raise ValueError("project_root must not contain symbolic links or reparse points")
    resolved_root = absolute_root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError("project_root must be a directory")
    by_function: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(mapping.get("mappings", [])):
        if not isinstance(item, dict):
            raise ValueError(f"coverage mapping entry {index} must be an object")
        relative = item.get("source_file")
        if not isinstance(relative, str) or not relative:
            raise ValueError(f"coverage mapping entry {index} has invalid source_file")
        normalized = relative.replace(chr(92), "/")
        pure = PurePosixPath(normalized)
        if (pure.is_absolute() or ":" in normalized
                or (pure.parts and pure.parts[0].endswith(":"))
                or any(p in {"", ".", ".."} for p in pure.parts)):
            raise ValueError(f"coverage mapping entry {index} source_file must be a normalized relative path")
        function_id = item.get("business_function_id")
        expected = item.get("source_file_sha256")
        evidence = {"source_file": pure.as_posix(), "source_binding_status": "SOURCE_BINDING_NOT_CHECKED",
                    "expected_source_file_sha256": expected if isinstance(expected, str) else None,
                    "actual_source_file_sha256": None, "source_line_count": None,
                    "line_start": item.get("line_start"), "line_end": item.get("line_end")}
        if not isinstance(expected, str) or len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            evidence["source_binding_status"] = "SOURCE_BINDING_NOT_CHECKED"
        else:
            target = resolved_root.joinpath(*pure.parts)
            try:
                raw = _read(target, MAX_COVERAGE_BYTES)
            except FileNotFoundError:
                evidence["source_binding_status"] = "SOURCE_FILE_MISSING"
            else:
                actual = hashlib.sha256(raw).hexdigest()
                evidence["actual_source_file_sha256"] = actual
                if actual != expected:
                    evidence["source_binding_status"] = "SOURCE_HASH_MISMATCH"
                else:
                    try:
                        source_text = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        evidence["source_binding_status"] = "SOURCE_ENCODING_UNSUPPORTED"
                    else:
                        line_count = len(source_text.splitlines())
                        evidence["source_line_count"] = line_count
                        if item.get("line_end", 0) > line_count:
                            evidence["source_binding_status"] = "SOURCE_LINE_RANGE_OUT_OF_BOUNDS"
                        else:
                            evidence["source_binding_status"] = "SOURCE_BYTES_MATCHED"
        by_function.setdefault(function_id, []).append(evidence)
    return by_function


def ingest_coverage_results(report_path: str | Path, mapping_path: str | Path,
                            graph_path: str | Path, report_format: str,
                            project_root: str | Path | None = None) -> dict[str, Any]:
    report = _read(report_path, MAX_COVERAGE_BYTES)
    mapping_bytes = _read(mapping_path, MAX_MAPPING_BYTES)
    graph_bytes = _read(graph_path, MAX_GRAPH_BYTES)
    try:
        mapping, graph = strict_json_loads(mapping_bytes), json.loads(graph_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input: {exc}") from exc
    if not isinstance(graph, dict) or graph.get("schema_version") != "usit-graph-v1":
        raise ValueError("business graph schema_version must be usit-graph-v1")
    graph_sha256 = hashlib.sha256(graph_bytes).hexdigest()
    if not isinstance(mapping, dict) or mapping.get("schema_version") != "usit-coverage-mapping-v1":
        raise ValueError("coverage mapping schema_version must be usit-coverage-mapping-v1")
    if mapping.get("source_graph_sha256") != graph_sha256:
        raise ValueError("coverage mapping source_graph_sha256 must match business graph bytes")
    if report_format == "coverage-py-json":
        coverage = parse_coverage_py_json(report)
    elif report_format == "jacoco-xml":
        coverage = parse_jacoco_xml(report)
    else:
        raise ValueError("coverage format must be coverage-py-json or jacoco-xml")
    functions = map_line_coverage_to_functions(coverage, mapping, graph)
    source_bindings = (_verify_source_bindings(mapping, project_root) if project_root is not None else {})
    for function in functions:
        evidence = source_bindings.get(function["business_function_id"], [])
        function["source_binding_evidence"] = evidence
        if project_root is None or not evidence or any(
            item["source_binding_status"] != "SOURCE_BYTES_MATCHED" for item in evidence
        ):
            function["code_coverage_status"] = "UNKNOWN"
            function["line_coverage_percent"] = None
            function["verification_status"] = "SOURCE_BINDING_NOT_VERIFIED"
    return {
        "schema_version": "usit-code-coverage-results-v1",
        "source_format": report_format.upper().replace("-", "_"),
        "source_hashes": {
            "coverage_report_sha256": hashlib.sha256(report).hexdigest(),
            "mapping_json_sha256": hashlib.sha256(mapping_bytes).hexdigest(),
            "business_graph_sha256": graph_sha256,
            "source_files": {item["source_file"]: item["actual_source_file_sha256"] for rows in source_bindings.values() for item in rows if item["actual_source_file_sha256"]},
        },
        "business_function_count": len(functions),
        "coverage_status_counts": {
            key: sum(1 for item in functions if item["code_coverage_status"] == key)
            for key in ("COVERED", "PARTIAL", "NOT_COVERED", "UNKNOWN")
        },
        "business_function_coverage": functions,
        "limitations": [
            "覆盖率只按审阅过的精确 source_file + line_start/line_end 映射到业务功能；不根据名称、路径相似度或 endpoint 猜测。",
            "覆盖率报告未提供的文件/行范围标记 UNKNOWN，不当作 0% 或未覆盖。",
            "JaCoCo package/sourcefile 路径必须与 mapping 中的项目相对路径完全一致。",
            "代码行覆盖率不等于业务需求覆盖、分支覆盖或功能正确性。",
            "只有 source_file_sha256 与显式 project_root 下真实源码字节匹配且行范围有效时，覆盖率才保留已测状态；否则降级 UNKNOWN。",
        ],
    }
