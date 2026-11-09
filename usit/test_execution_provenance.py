"""Strict structural and digest validation for test execution provenance records.

This module verifies record integrity only. A JSON record containing hashes is
not an authenticated runner identity and MUST NOT establish trusted execution
provenance by itself.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

SCHEMA_VERSION = "usit-test-execution-provenance-v1"
MAX_RECORD_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 50 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_object(data: bytes, label: str, limit: int) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise ValueError(f"{label} must be bytes")
    if len(data) > limit:
        raise ValueError(f"{label} exceeds size limit ({limit} bytes)")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes; reject non-finite numbers."""
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not canonical-JSON serializable: {exc}") from exc


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _safe_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty normalized relative path")
    path = PurePosixPath(value)
    if (path.is_absolute() or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != value):
        raise ValueError(f"{field} must be a normalized relative POSIX path")
    return value


def _utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must be UTC")
    return parsed


def validate_execution_provenance(
    record_bytes: bytes,
    report_bytes: bytes,
    source_manifest_bytes: bytes,
) -> dict[str, Any]:
    """Validate record shape and digest consistency without asserting authenticity.

    The returned trust state is deliberately UNAUTHENTICATED: integrity hashes
    cannot prove who created the record or which files the test process loaded.
    """
    record = _json_object(record_bytes, "execution provenance record", MAX_RECORD_BYTES)
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")

    execution_id = record.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id.strip() or len(execution_id) > 128:
        raise ValueError("execution_id must be a non-empty opaque string of at most 128 characters")

    started = _utc_timestamp(record.get("started_at"), "started_at")
    completed = _utc_timestamp(record.get("completed_at"), "completed_at")
    if completed < started:
        raise ValueError("completed_at must not precede started_at")

    runner = record.get("runner")
    if (not isinstance(runner, dict)
            or not isinstance(runner.get("name"), str) or not runner["name"].strip()
            or not isinstance(runner.get("version"), str) or not runner["version"].strip()):
        raise ValueError("runner must contain non-empty name and version")

    report_format = record.get("report_format")
    if not isinstance(report_format, str) or report_format not in {"PYTEST_JSON", "JUNIT_XML"}:
        raise ValueError("report_format must be PYTEST_JSON or JUNIT_XML")
    report_path = _safe_relative_path(record.get("report_relative_path"), "report_relative_path")
    declared_report_hash = _require_hash(record.get("report_sha256"), "report_sha256")
    actual_report_hash = sha256_hex(report_bytes)
    if declared_report_hash != actual_report_hash:
        raise ValueError("report_sha256 does not match report bytes")

    snapshot = record.get("source_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("source_snapshot must be an object")
    manifest_schema = snapshot.get("manifest_schema_version")
    if not isinstance(manifest_schema, str) or not manifest_schema.strip():
        raise ValueError("source_snapshot.manifest_schema_version is required")
    declared_manifest_hash = _require_hash(
        snapshot.get("manifest_sha256"), "source_snapshot.manifest_sha256"
    )
    manifest = _json_object(source_manifest_bytes, "source manifest", MAX_MANIFEST_BYTES)
    if manifest.get("schema_version") != manifest_schema:
        raise ValueError("source manifest schema_version does not match provenance record")
    actual_manifest_hash = sha256_hex(canonical_json_bytes(manifest))
    if declared_manifest_hash != actual_manifest_hash:
        raise ValueError("source_snapshot.manifest_sha256 does not match canonical manifest")
    file_count = snapshot.get("file_count")
    if not isinstance(file_count, int) or isinstance(file_count, bool) or file_count < 0:
        raise ValueError("source_snapshot.file_count must be a non-negative integer")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != file_count:
        raise ValueError("source manifest files count does not match source_snapshot.file_count")
    source_file_hashes: dict[str, str] = {}
    folded_paths: dict[str, str] = {}
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise ValueError(f"source manifest files[{index}] must be an object")
        relative = _safe_relative_path(entry.get("path"), f"source manifest files[{index}].path")
        digest = _require_hash(entry.get("sha256"), f"source manifest files[{index}].sha256")
        folded = relative.casefold()
        if relative in source_file_hashes:
            raise ValueError(f"duplicate source manifest path: {relative}")
        if folded in folded_paths and folded_paths[folded] != relative:
            raise ValueError(
                f"case-colliding source manifest paths: {folded_paths[folded]} and {relative}"
            )
        folded_paths[folded] = relative
        source_file_hashes[relative] = digest
    capture_method = snapshot.get("capture_method")
    if not isinstance(capture_method, str) or not capture_method.strip():
        raise ValueError("source_snapshot.capture_method is required")

    tree = record.get("working_tree_state")
    if (not isinstance(tree, dict)
            or not isinstance(tree.get("state"), str)
            or tree.get("state") not in {"CLEAN", "DIRTY", "UNKNOWN"}):
        raise ValueError("working_tree_state.state must be CLEAN, DIRTY, or UNKNOWN")
    if (not isinstance(tree.get("untracked_files"), str)
            or tree.get("untracked_files") not in {"INCLUDED", "EXCLUDED", "UNKNOWN"}):
        raise ValueError("working_tree_state.untracked_files must be INCLUDED, EXCLUDED, or UNKNOWN")

    for field in ("command_fingerprint", "environment_fingerprint"):
        _require_hash(record.get(field), field)

    return {
        "schema_version": SCHEMA_VERSION,
        "execution_id": execution_id,
        "report_integrity_status": "MATCHED",
        "source_manifest_integrity_status": "MATCHED",
        "working_tree_state": tree["state"],
        "untracked_files": tree["untracked_files"],
        "execution_reproducibility_status": (
            "NOT_ESTABLISHED"
            if tree["state"] != "CLEAN" or tree["untracked_files"] != "INCLUDED"
            else "CANDIDATE_ONLY"
        ),
        "provenance_authenticity_status": "UNAUTHENTICATED",
        "report_relative_path": report_path,
        "report_sha256": actual_report_hash,
        "source_manifest_sha256": actual_manifest_hash,
        "source_file_count": file_count,
        "source_manifest_file_hashes": dict(sorted(source_file_hashes.items())),
        "limitations": [
            "Digest consistency does not authenticate the record creator.",
            "This validator does not prove the test process loaded the declared source snapshot.",
            "CANDIDATE_ONLY is not a reproducibility or execution-source verification claim.",
        ],
    }
