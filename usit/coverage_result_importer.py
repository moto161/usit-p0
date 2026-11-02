"""Import coverage.py JSON or JaCoCo XML using reviewed line-range mappings."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from .safeio import is_reparse, open_no_follow
from .test_report_adapters import parse_coverage_py_json, parse_jacoco_xml, map_line_coverage_to_functions

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


def ingest_coverage_results(report_path: str | Path, mapping_path: str | Path,
                            graph_path: str | Path, report_format: str) -> dict[str, Any]:
    report = _read(report_path, MAX_COVERAGE_BYTES)
    mapping_bytes = _read(mapping_path, MAX_MAPPING_BYTES)
    graph_bytes = _read(graph_path, MAX_GRAPH_BYTES)
    try:
        mapping, graph = json.loads(mapping_bytes), json.loads(graph_bytes)
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
    return {
        "schema_version": "usit-code-coverage-results-v1",
        "source_format": report_format.upper().replace("-", "_"),
        "source_hashes": {
            "coverage_report_sha256": hashlib.sha256(report).hexdigest(),
            "mapping_json_sha256": hashlib.sha256(mapping_bytes).hexdigest(),
            "business_graph_sha256": graph_sha256,
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
        ],
    }
