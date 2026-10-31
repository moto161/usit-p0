"""USIT S1-C: file-level understanding conclusion layer (pure logic, no I/O).

Builds per-file conclusions, project-level diagnostics, unmatched records and
totals from a scanner file manifest + a batch of parser-contract records,
following the frozen DESIGN-10..14 specification. This module is pure: no file
I/O, no scan, no build, no network, no external AI. It is NOT wired into
scanner/CLI in this phase; it only provides the construction/serialization
capability for a later, separately-authorized integration ticket.

Frozen invariants (enforced here, verified by tests):
- Every manifest file yields exactly one file_conclusion (incl. zero-record).
- Every raw input record is attributed to exactly one category
  (matched_file / project_diagnostic / unmatched) BEFORE projection; projection
  success/failure never changes the category (=> no undercount).
- Static inference / AI hypothesis are NEVER upgraded to a verified fact;
  MEANINGFUL_EVIDENCE requires excerpt_verified and is FUTURE (unreachable).
- A project-level diagnostic requires verifiable identity (node_type ==
  "diagnostic" AND a "diagnostic" evidence field), not an empty path alone.
- Conflict synthesis items are附加 projections, not raw input records;
  projected_items == Nr + cf and conflict items never enter n_records_total.
- File context uses tri-state (True/False/None=unknown); unknown is never
  treated as a negative fact (empty / no-symbols / not-degraded).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .understanding_model import ConclusionType
from .understanding_projection import (
    NON_SUCCESS,
    PROJECTION_SCHEMA,
    ProjectionResult,
    project_record,
    project_records,
    _source_location,
)

__all__ = [
    "FILE_UNDERSTANDING_SCHEMA",
    "FileConclusionType",
    "FileContext",
    "FileConclusion",
    "build_file_understanding",
    "decide_file_conclusion",
    "verify_understanding_identities",
]

FILE_UNDERSTANDING_SCHEMA = "usit-file-understanding-v1"

# Branch numbers (ordered, first-match wins). See DESIGN-12/13 decision table.
_B_EMPTY = 0
_B_NO_RECORDS = 1
_B_UNKNOWN_RESOLUTION = 2
_B_CONFLICT_ONLY = 3
_B_DEGRADED_PARSE = 4
_B_MEANINGFUL = 6  # FUTURE (requires excerpt_verified); structurally unreachable
_B_INSUFFICIENT = 7
_B_UNKNOWN = 8

# N_* count keys carried per file (additive projection stats from project_records).
_COUNT_KEYS = (
    "N_raw", "N_candidate", "N_non_candidate", "N_construct_ok", "N_construct_fail",
    "N_not_attempted", "N_unexpected_error", "N_evidence_none", "Nr",
)


class FileConclusionType(str, Enum):
    """8-value file-level conclusion enum (orthogonal to item.complete).

    There is deliberately NO VERIFIED_FACT member: nothing in this build may
    assert a verified conclusion. MEANINGFUL_EVIDENCE is reserved for a future
    authorized excerpt_verified protocol and is unreachable today.
    """

    NO_SEMANTIC_CONTENT = "NO_SEMANTIC_CONTENT"
    NO_RECORDS = "NO_RECORDS"
    UNKNOWN_RESOLUTION = "UNKNOWN_RESOLUTION"
    CONFLICT_ONLY = "CONFLICT_ONLY"
    DEGRADED_PARSE = "DEGRADED_PARSE"
    MEANINGFUL_EVIDENCE = "MEANINGFUL_EVIDENCE"  # FUTURE
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNKNOWN = "UNKNOWN"


def _tri(value: Any) -> str:
    """Tri-state -> JSON string. True/False/None(unknown)."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


@dataclass(frozen=True)
class FileContext:
    """Scanner file metadata signals. ``None`` == unknown (never a negative default)."""

    size_bytes: int | None = None
    readable_text: bool | None = None
    has_symbols: bool | None = None
    file_degraded: bool | None = None


@dataclass(frozen=True)
class FileConclusion:
    """One file-level conclusion (the understanding-layer verdict for a file)."""

    file_path: str
    file_conclusion: FileConclusionType
    branch: int
    reasons: tuple[str, ...]
    has_conflict: bool
    conflict_count: int
    file_degraded: bool | None
    content_status: str
    readable_text: bool | None
    n_records: int
    resolved_ev_count: int
    meaningful_count: int
    counts: dict[str, int]
    evidence_refs: tuple[str, ...]
    conflicting_refs: tuple[str, ...]
    projection_schema_version: str = PROJECTION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_conclusion": self.file_conclusion.value,
            "branch": self.branch,
            "reasons": list(self.reasons),
            "has_conflict": self.has_conflict,
            "conflict_count": self.conflict_count,
            "file_degraded": _tri(self.file_degraded),
            "content_status": self.content_status,
            "readable_text": _tri(self.readable_text),
            "n_records": self.n_records,
            "resolved_ev_count": self.resolved_ev_count,
            "meaningful_count": self.meaningful_count,
            "counts": {k: self.counts[k] for k in _COUNT_KEYS},
            "evidence_refs": list(self.evidence_refs),
            # NOTE: these reference projection_id values (content ids), NOT
            # conclusion_id. The source UncertaintyRecord field is misleadingly
            # named ``conflicting_conclusion_ids`` but holds projection ids.
            "conflicting_refs": list(self.conflicting_refs),
            "projection_schema_version": self.projection_schema_version,
        }


# ---------------------------------------------------------------------------
# File-context / content-status helpers
# ---------------------------------------------------------------------------

def _content_status(fc: FileContext) -> str:
    """empty / unreadable / readable / unknown (DESIGN-12/13).

    Only ``size_bytes == 0`` (definite) is "empty"; a non-empty but unreadable
    file is "unreadable", never "empty". Missing/type-error size or readable is
    "unknown" (never a negative default).
    """
    size = fc.size_bytes
    if isinstance(size, bool) or not isinstance(size, int):
        return "unknown"  # size_bytes missing/type-error -> cannot assert empty
    if size == 0:
        return "empty"
    readable = fc.readable_text
    if readable is True:
        return "readable"
    if readable is False:
        return "unreadable"
    return "unknown"  # size>0 but readable missing/type-error


def _file_context_from_entry(entry: Mapping[str, Any]) -> FileContext:
    """Extract tri-state FileContext from a scanner manifest file entry."""
    size = entry.get("size_bytes")
    size = size if (isinstance(size, int) and not isinstance(size, bool)) else None
    readable = entry.get("readable_text")
    readable = readable if isinstance(readable, bool) else None
    symbols = entry.get("symbols")
    has_symbols = bool(symbols) if isinstance(symbols, list) else None
    degradation = entry.get("analysis_degradation")
    file_degraded: bool | None = None
    if isinstance(degradation, Mapping):
        d = degradation.get("degraded")
        if isinstance(d, bool):
            file_degraded = d
    return FileContext(size_bytes=size, readable_text=readable,
                       has_symbols=has_symbols, file_degraded=file_degraded)


# ---------------------------------------------------------------------------
# Branch decision
# ---------------------------------------------------------------------------

def decide_file_conclusion(file_path: str, fc: FileContext, projection: ProjectionResult) -> FileConclusion:
    """Apply the frozen ordered branch rules to one file's projection.

    Order (first match wins, mutually exclusive):
      0 empty -> 1 no_records -> 2 (all_non_success | all_evidence_empty) ->
      3 conflict_only -> 4 degraded -> 6 meaningful[FUTURE] ->
      7 insufficient -> 8 unknown.
    """
    stats = projection.stats
    n_records = stats["N_raw"]
    conflict_count = stats["conflict"]  # cf (one per conflict group)
    has_conflict = conflict_count > 0

    # Per-record items vs conflict synthesis items (conflicts are附加, not records).
    record_items = [i for i in projection.items if i.conclusion_type is not ConclusionType.CONFLICT]
    conflict_items = [i for i in projection.items if i.conclusion_type is ConclusionType.CONFLICT]

    content_status = _content_status(fc)
    all_non_success = bool(record_items) and all(i.resolution_status in NON_SUCCESS for i in record_items)
    all_evidence_empty = stats["N_candidate"] == 0
    # resolved_ev_count: RESOLVED AND STATIC_INFERENCE (excludes AI_HYPOTHESIS/UNKNOWN/CONFLICT).
    resolved_ev_count = sum(
        1 for i in record_items
        if i.resolution_status == "RESOLVED" and i.conclusion_type is ConclusionType.STATIC_INFERENCE
    )
    meaningful_count = 0  # FUTURE (excerpt_verified); structurally unreachable

    evidence_refs = tuple(i.projection_id for i in record_items)
    conflicting_refs = tuple(sorted({
        pid
        for ci in conflict_items
        for pid in (ci.uncertainty.conflicting_conclusion_ids if ci.uncertainty is not None else ())
    }))

    def _finish(conclusion: FileConclusionType, branch: int, reasons: list[str]) -> FileConclusion:
        return FileConclusion(
            file_path=file_path,
            file_conclusion=conclusion,
            branch=branch,
            reasons=tuple(reasons),
            has_conflict=has_conflict,
            conflict_count=conflict_count,
            file_degraded=fc.file_degraded,
            content_status=content_status,
            readable_text=fc.readable_text,
            n_records=n_records,
            resolved_ev_count=resolved_ev_count,
            meaningful_count=meaningful_count,
            counts={k: stats[k] for k in _COUNT_KEYS},
            evidence_refs=evidence_refs,
            conflicting_refs=conflicting_refs,
        )

    if content_status == "empty":
        return _finish(FileConclusionType.NO_SEMANTIC_CONTENT, _B_EMPTY, ["empty_or_no_semantic_content"])
    if n_records == 0:
        if fc.has_symbols is False:
            reason = "no_symbols"
        elif fc.has_symbols is True:
            reason = "symbols_no_records"
        else:
            reason = "symbol_status_unknown"
        return _finish(FileConclusionType.NO_RECORDS, _B_NO_RECORDS, [reason])
    if all_non_success or all_evidence_empty:
        return _finish(FileConclusionType.UNKNOWN_RESOLUTION, _B_UNKNOWN_RESOLUTION,
                       ["all_records_non_success_or_no_evidence"])
    if has_conflict and meaningful_count == 0:
        return _finish(FileConclusionType.CONFLICT_ONLY, _B_CONFLICT_ONLY, ["conflict_pending"])
    if fc.file_degraded is True and meaningful_count == 0:
        return _finish(FileConclusionType.DEGRADED_PARSE, _B_DEGRADED_PARSE, ["degraded_evidence"])
    if meaningful_count >= 1:
        # FUTURE: reachable only with an authorized excerpt_verified protocol.
        reasons = ["has_meaningful_evidence"]
        if fc.file_degraded is True:
            reasons.append("degraded_evidence")
        return _finish(FileConclusionType.MEANINGFUL_EVIDENCE, _B_MEANINGFUL, reasons)
    if resolved_ev_count >= 1:
        return _finish(FileConclusionType.INSUFFICIENT_EVIDENCE, _B_INSUFFICIENT, ["missing_or_empty_evidence"])
    return _finish(FileConclusionType.UNKNOWN, _B_UNKNOWN,
                   ["indeterminate_resolution_without_usable_evidence"])


# ---------------------------------------------------------------------------
# Record categorization (pre-projection; exhaustive + mutually exclusive)
# ---------------------------------------------------------------------------

def _record_group_key(record: Any) -> Any:
    """Grouping/identity key = source_location.file_path (matches _detect_conflicts)."""
    if not isinstance(record, Mapping):
        return None
    return _source_location(record).get("file_path")


def _is_confirmed_project_diagnostic(record: Mapping[str, Any]) -> bool:
    """A project-level diagnostic requires verifiable identity, not empty path alone."""
    if record.get("node_type") != "diagnostic":
        return False
    evidence = record.get("evidence")
    return isinstance(evidence, Mapping) and "diagnostic" in evidence


def _categorize_record(record: Any, manifest_paths: set[str]) -> tuple[str, Any]:
    """Return (category, key_or_reason).

    category ∈ {"matched_file", "project_diagnostic", "unmatched"}.
    For "unmatched", the second value is the reason code.
    """
    if not isinstance(record, Mapping):
        return ("unmatched", "invalid_or_missing_file_path")
    key = _record_group_key(record)
    if isinstance(key, str) and key != "":
        if key in manifest_paths:
            return ("matched_file", key)
        return ("unmatched", "no_matching_manifest_file")
    if key == "":
        if _is_confirmed_project_diagnostic(record):
            return ("project_diagnostic", "")
        return ("unmatched", "empty_path_not_confirmed_project_diagnostic")
    return ("unmatched", "invalid_or_missing_file_path")


# ---------------------------------------------------------------------------
# Output entry builders
# ---------------------------------------------------------------------------

def _identity_basis(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        return {"node_type": None, "has_diagnostic_field": False, "file_path": None}
    evidence = record.get("evidence")
    return {
        "node_type": record.get("node_type") if isinstance(record.get("node_type"), str) else None,
        "has_diagnostic_field": isinstance(evidence, Mapping) and "diagnostic" in evidence,
        "file_path": _record_group_key(record) if isinstance(_record_group_key(record), str) else None,
    }


def _project_diagnostic_entry(record: Any) -> dict[str, Any]:
    evidence = record.get("evidence") if isinstance(record, Mapping) else None
    diag_code = evidence.get("diagnostic") if isinstance(evidence, Mapping) else None
    return {
        "file_path": "",
        "qualified_name": record.get("qualified_name") if isinstance(record, Mapping) else None,
        "diagnostic_code": diag_code,
        "source": "unknown",  # records carry no verifiable source marker (NOT_VERIFIED)
        "projection": project_record(record).to_dict(),
    }


def _unmatched_entry(record: Any, reason: str) -> dict[str, Any]:
    basis = _identity_basis(record)
    file_path = basis["file_path"] if isinstance(basis["file_path"], str) else ""
    return {
        "file_path": file_path,
        "reason": reason,
        "source": "unknown",  # records carry no verifiable source marker (NOT_VERIFIED)
        "projection": project_record(record).to_dict(),  # preserved; failure still counted
        "identity_basis": basis,
    }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_file_understanding(
    manifest_files: Sequence[Mapping[str, Any]],
    records: Sequence[Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Build the ``usit-file-understanding-v1`` understanding payload (pure).

    Args:
        manifest_files: scanner ``scan_manifest.json`` "files" entries (each a
            mapping with a "path" and optional size_bytes/readable_text/symbols/
            analysis_degradation). This is the authoritative file list.
        records: the parser-contract record batch (adapt_graph_v1 output +
            appended js-ts / analysis diagnostics).
        strict: if True, any unmatched record is a blocking error.

    Raises:
        TypeError: manifest_files / records are not valid sequences, or a
            manifest entry is malformed (no/非-string "path", duplicate path).
        ValueError: systemic mismatch (records carry non-empty paths but none
            match the manifest) or strict-mode unmatched records.

    Never emits VERIFIED_FACT; MEANINGFUL_EVIDENCE is unreachable.
    """
    if isinstance(manifest_files, (str, bytes, Mapping)) or not isinstance(manifest_files, Sequence):
        raise TypeError("manifest_files must be a sequence of file entries")
    if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Sequence):
        raise TypeError("records must be a sequence of record mappings")

    # Authoritative file list (preserve manifest order); reject malformed/duplicate.
    manifest_paths: list[str] = []
    file_contexts: dict[str, FileContext] = {}
    for entry in manifest_files:
        if not isinstance(entry, Mapping) or "path" not in entry:
            raise TypeError("each manifest file entry must be a mapping with 'path'")
        path = entry["path"]
        if not isinstance(path, str):
            raise TypeError("manifest file 'path' must be a string")
        if path in file_contexts:
            raise ValueError(f"duplicate manifest file path: {path!r}")
        manifest_paths.append(path)
        file_contexts[path] = _file_context_from_entry(entry)
    manifest_path_set = set(manifest_paths)

    # Categorize every raw record BEFORE projection (exhaustive + exclusive).
    matched_groups: dict[str, list[Any]] = {p: [] for p in manifest_paths}
    project_diagnostics: list[Any] = []
    unmatched: list[tuple[Any, str]] = []
    for record in records:
        category, key = _categorize_record(record, manifest_path_set)
        if category == "matched_file":
            matched_groups[key].append(record)
        elif category == "project_diagnostic":
            project_diagnostics.append(record)
        else:
            unmatched.append((record, key))  # key is the reason code

    # One conclusion per manifest file (incl. zero-record files).
    files: list[FileConclusion] = []
    for path in manifest_paths:
        projection = project_records(matched_groups[path])
        files.append(decide_file_conclusion(path, file_contexts[path], projection))

    project_diag_entries = [_project_diagnostic_entry(r) for r in project_diagnostics]
    unmatched_entries = [_unmatched_entry(r, reason) for r, reason in unmatched]

    totals = {
        "files": len(files),
        "by_conclusion": _histogram(files),
        "n_records_total": sum(f.n_records for f in files),
        "conflict_total": sum(f.conflict_count for f in files),
        "unmatched_total": len(unmatched_entries),
        "project_diagnostic_total": len(project_diag_entries),
    }

    # Blocking conditions (DESIGN-13 §1.3).
    if strict and unmatched_entries:
        raise ValueError(f"strict mode: {len(unmatched_entries)} unmatched record(s)")
    nonempty_path_records = sum(
        1 for r in records
        if isinstance(r, Mapping) and isinstance(_record_group_key(r), str) and _record_group_key(r) != ""
    )
    if nonempty_path_records and totals["n_records_total"] == 0:
        raise ValueError(
            "systemic mismatch: records carry non-empty paths but none match the manifest"
        )

    result = {
        "schema_version": FILE_UNDERSTANDING_SCHEMA,
        "projection_schema_version": PROJECTION_SCHEMA,
        "generated_from": {"manifest_schema": "usit-manifest-v1", "contract_version": "usit-parser-result-v1"},
        "files": [f.to_dict() for f in files],
        "project_diagnostics": project_diag_entries,
        "unmatched_records": unmatched_entries,
        "totals": totals,
    }

    verify_understanding_identities(result, total_input_records=len(records))
    return result


def _histogram(files: list[FileConclusion]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for f in files:
        key = f.file_conclusion.value
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items()))


def verify_understanding_identities(result: dict[str, Any], *, total_input_records: int) -> None:
    """Fail-fast check of the frozen summary identities K1/K2/K3/K4/K5/K6/K7.

    These hold by construction; a violation indicates a categorization/counting
    bug. (K8, projected_items == Nr + cf, is a projection-level identity verified
    by the projection test-suite.)  Also asserts no VERIFIED_FACT conclusion.
    """
    files = result["files"]
    totals = result["totals"]
    problems: list[str] = []

    if totals["files"] != len(files):  # K5
        problems.append("K5 totals.files != len(files)")
    if sum(totals["by_conclusion"].values()) != totals["files"]:  # K6
        problems.append("K6 sum(by_conclusion) != totals.files")
    matched = sum(f["n_records"] for f in files)
    if totals["n_records_total"] != matched:  # K2
        problems.append("K2 n_records_total != sum(files.n_records)")
    if totals["conflict_total"] != sum(f["conflict_count"] for f in files):  # K7
        problems.append("K7 conflict_total != sum(files.conflict_count)")
    if totals["unmatched_total"] != len(result["unmatched_records"]):  # K3
        problems.append("K3 unmatched_total != len(unmatched_records)")
    if totals["project_diagnostic_total"] != len(result["project_diagnostics"]):  # K4
        problems.append("K4 project_diagnostic_total != len(project_diagnostics)")
    if total_input_records != (
        totals["n_records_total"] + totals["project_diagnostic_total"] + totals["unmatched_total"]
    ):  # K1
        problems.append("K1 total_input_records != matched + project_diagnostic + unmatched")
    for f in files:
        if f["file_conclusion"] == "VERIFIED_FACT":
            problems.append("VERIFIED_FACT must never be emitted")

    if problems:
        raise RuntimeError("understanding identity violation: " + "; ".join(problems))
