"""USIT S1-A: read-only understanding projection.

This module converts existing parser-contract records (and scanner-shaped rows)
into unified understanding results WITHOUT modifying any input, without
triggering scans, builds, installs, network access or credential reads.

Design rules enforced here (and by the reused model guards):

* ``VERIFIED_FACT`` is never produced. ``understanding_model.ConclusionRecord``
  hard-blocks it because no trusted external verification protocol is
  authorized. Static parsing therefore maps to ``STATIC_INFERENCE``.
* Non-success resolution states (``UNRESOLVED``/``AMBIGUOUS``/``DYNAMIC``/
  ``EXTERNAL``/``UNKNOWN``) are preserved as ``UNKNOWN`` and never counted as
  resolved success.
* Missing or empty evidence yields ``UNKNOWN``; nothing is fabricated
  (no invented path, line, symbol or test result).
* Caller-supplied trust claims are structurally impossible:
  ``EvidenceReference`` rejects ``source_trusted``/``execution_verified``/
  ``TRUSTED`` at construction time.
* Output is deterministic: identifiers are content-derived (SHA-256) and the
  projection is a pure function of the input.

Nothing in this module writes to the project tree.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .parser_contract import CONTRACT_VERSION, RESOLUTION_STATUSES
from .schema_adapter import NON_SUCCESS_STATES
from .understanding_model import (
    CollectionStatus,
    ConclusionRecord,
    ConclusionType,
    EvidenceReference,
    SourceType,
    TrustLevel,
    UncertaintyRecord,
)

__all__ = [
    "PROJECTION_SCHEMA",
    "ProjectedItem",
    "ProjectionResult",
    "project_record",
    "project_records",
]

PROJECTION_SCHEMA = "usit-understanding-projection-v1"

_MAPPING_METHOD = "s1-a-readonly-projection"

#: States that must never be promoted to a resolved/verified conclusion.
NON_SUCCESS = frozenset(NON_SUCCESS_STATES)


def _canon(value: Any) -> Any:
    """Return a JSON-stable representation of ``value``.

    Unsupported objects fall back to their ``repr`` so hashing stays
    deterministic instead of raising mid-projection.
    """
    try:
        return json.loads(json.dumps(value, sort_keys=True, default=repr))
    except (TypeError, ValueError):
        return repr(value)


def _stable_id(prefix: str, *parts: Any) -> str:
    """Content-derived identifier (same input -> same id)."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(json.dumps(_canon(part), sort_keys=True, default=repr).encode("utf-8"))
        digest.update(b"\x00")
    return f"{prefix}:{digest.hexdigest()[:32]}"


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _as_line(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 1 else None


def _source_location(record: Mapping[str, Any]) -> Mapping[str, Any]:
    loc = record.get("source_location")
    return loc if isinstance(loc, Mapping) else {}


def _classify(record: Mapping[str, Any]) -> tuple[ConclusionType, tuple[str, ...]]:
    """Map one record to a conclusion type, with explicit reasons."""
    status = record.get("resolution_status")
    if status is None:
        return ConclusionType.UNKNOWN, ("missing_resolution_status",)
    if not isinstance(status, str):
        return ConclusionType.UNKNOWN, ("invalid_resolution_status_type",)
    if status not in RESOLUTION_STATUSES:
        return ConclusionType.UNKNOWN, (f"unsupported_resolution_status:{status}",)
    if status in NON_SUCCESS:
        return ConclusionType.UNKNOWN, (f"non_success_status:{status}",)

    evidence = record.get("evidence")
    if not isinstance(evidence, Mapping) or not evidence:
        return ConclusionType.UNKNOWN, ("missing_or_empty_evidence",)

    if record.get("hypothesis") is True:
        return ConclusionType.AI_HYPOTHESIS, ("declared_hypothesis",)

    return ConclusionType.STATIC_INFERENCE, ()


def _build_evidence(record: Mapping[str, Any]) -> EvidenceReference | None:
    """Build evidence only from fields actually present on the record."""
    evidence = record.get("evidence")
    if not isinstance(evidence, Mapping) or not evidence:
        return None
    loc = _source_location(record)
    path = _as_str(loc.get("file_path")) or _as_str(record.get("file_path"))
    line = _as_line(loc.get("line"))
    symbol = _as_str(record.get("qualified_name"))
    locator = _as_str(evidence.get("locator")) or _as_str(loc.get("locator"))
    if locator is None:
        locator = f"record#{_as_str(record.get('node_type')) or 'unknown'}"
    return EvidenceReference(
        source_schema=CONTRACT_VERSION,
        source_id=_as_str(record.get("qualified_name")),
        relative_path=path,
        line=line,
        symbol=symbol,
        locator=locator,
        summary=_as_str(evidence.get("summary")),
        collection_status=CollectionStatus.PRESENT,
        trust_level=TrustLevel.NOT_VERIFIED,
        content_present=True,
        source_trusted=False,
        execution_verified=False,
        raw_value=None,
        mapping_method=_MAPPING_METHOD,
    )


def project_record(record: Any) -> ProjectedItem:
    """Project a single record.

    Never raises for malformed records: those degrade to ``UNKNOWN`` with a
    diagnostic reason instead of silently becoming a success.
    """
    if not isinstance(record, Mapping):
        return ProjectedItem(
            projection_id=_stable_id("proj", {"record_not_a_mapping": repr(record)}),
            object_type="unknown",
            resolution_status="UNKNOWN",
            conclusion_type=ConclusionType.UNKNOWN,
            reasons=("record_not_a_mapping",),
            limitations=("input record is not a mapping; nothing inferred",),
            conclusion=None,
            uncertainty=UncertaintyRecord(
                uncertainty_id=_stable_id("unc", "record_not_a_mapping", repr(record)),
                reason="input record is not a mapping",
                missing_evidence=("mapping-shaped record",),
                minimum_resolution="Supply a parser-contract record mapping.",
            ),
        )

    status = record.get("resolution_status")
    status_text = status if isinstance(status, str) else "UNKNOWN"
    conclusion_type, reasons = _classify(record)
    object_type = _as_str(record.get("node_type")) or _as_str(record.get("relation_type")) or "unknown"

    evidence = _build_evidence(record)
    path = _as_str(_source_location(record).get("file_path")) or _as_str(record.get("file_path"))
    symbol = _as_str(record.get("qualified_name"))
    line = _as_line(_source_location(record).get("line"))

    limitations: list[str] = [
        "static projection only; no runtime execution evidence is attached",
    ]
    if evidence is None:
        limitations.append("no evidence present; conclusion is not verified")
    if conclusion_type is ConclusionType.AI_HYPOTHESIS:
        limitations.append("hypothesis only; not counted as verified fact")
    if status_text in NON_SUCCESS:
        limitations.append(f"resolution_status '{status_text}' is not a success state")

    projection_id = _stable_id(
        "proj",
        {
            "object_type": object_type,
            "path": path,
            "symbol": symbol,
            "line": line,
            "status": status_text,
            "reasons": list(reasons),
        },
    )
    content = (
        f"{object_type} {symbol or '<unnamed>'} "
        f"at {path or '<unknown-path>'}:{line if line is not None else '-'} "
        f"[{status_text}]"
    )

    conclusion: ConclusionRecord | None = None
    if conclusion_type is not ConclusionType.UNKNOWN:
        # ConclusionRecord itself hard-blocks VERIFIED_FACT; we never request it.
        conclusion = ConclusionRecord(
            conclusion_id=_stable_id("conc", projection_id, content),
            content=content,
            conclusion_type=conclusion_type,
            evidence_refs=(evidence,) if evidence is not None else (),
            source_type=SourceType.GRAPH,
            confidence=None,  # numeric confidence deliberately not assigned
            scope=path,
            limitations=tuple(limitations),
            raw_source=None,
            mapping_method=_MAPPING_METHOD,
        )

    uncertainty: UncertaintyRecord | None = None
    if conclusion_type is ConclusionType.UNKNOWN:
        uncertainty = UncertaintyRecord(
            uncertainty_id=_stable_id("unc", projection_id, tuple(reasons)),
            reason="; ".join(reasons) or "unknown",
            missing_evidence=tuple(reasons),
            minimum_resolution=(
                "Provide semantic evidence or an authorized verification protocol."
            ),
        )

    return ProjectedItem(
        projection_id=projection_id,
        object_type=object_type,
        resolution_status=status_text,
        conclusion_type=conclusion_type,
        reasons=reasons,
        limitations=tuple(limitations),
        conclusion=conclusion,
        uncertainty=uncertainty,
    )


def _detect_conflicts(items: Sequence[ProjectedItem], records: Sequence[Any]) -> tuple[ProjectedItem, ...]:
    """Flag same logical identity carrying different content as CONFLICT."""
    buckets: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for item, record in zip(items, records):
        if not isinstance(record, Mapping):
            continue
        key = (
            _as_str(_source_location(record).get("file_path")) or "",
            _as_str(record.get("qualified_name")) or "",
        )
        if not any(key):
            continue
        fingerprint = json.dumps(
            {
                "status": item.resolution_status,
                "reasons": list(item.reasons),
                "symbol": _as_str(record.get("qualified_name")),
            },
            sort_keys=True,
        )
        buckets.setdefault(key, []).append((item.projection_id, fingerprint))

    conflicts: list[ProjectedItem] = []
    for (path, symbol), entries in sorted(buckets.items()):
        distinct = {fp for _, fp in entries}
        if len(entries) > 1 and len(distinct) > 1:
            ids = tuple(sorted(pid for pid, _ in entries))
            conflicts.append(
                ProjectedItem(
                    projection_id=_stable_id("conflict", path, symbol, ids),
                    object_type="conflict",
                    resolution_status="CONFLICT",
                    conclusion_type=ConclusionType.CONFLICT,
                    reasons=("same_identity_different_content",),
                    limitations=(
                        "both sides retained; no side is selected automatically",
                    ),
                    conclusion=None,
                    uncertainty=UncertaintyRecord(
                        uncertainty_id=_stable_id("unc", "conflict", path, symbol, ids),
                        reason="same logical identity carries differing content",
                        missing_evidence=("authoritative resolution",),
                        conflicting_conclusion_ids=ids,
                        minimum_resolution="Resolve by independent adjudication.",
                    ),
                )
            )
    return tuple(conflicts)


@dataclass(frozen=True)
class ProjectedItem:
    """One projected understanding item."""

    projection_id: str
    object_type: str
    resolution_status: str
    conclusion_type: ConclusionType
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    conclusion: ConclusionRecord | None
    uncertainty: UncertaintyRecord | None

    @property
    def is_success(self) -> bool:
        """True only for states that may be counted as parsed success."""
        return self.conclusion_type is ConclusionType.STATIC_INFERENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "object_type": self.object_type,
            "resolution_status": self.resolution_status,
            "conclusion_type": self.conclusion_type.value,
            "is_success": self.is_success,
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
            "conclusion_id": self.conclusion.conclusion_id if self.conclusion else None,
            "evidence_count": len(self.conclusion.evidence_refs) if self.conclusion else 0,
        }


@dataclass(frozen=True)
class ProjectionResult:
    """Deterministic projection output."""

    items: tuple[ProjectedItem, ...]
    stats: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECTION_SCHEMA,
            "items": [item.to_dict() for item in self.items],
            "stats": dict(sorted(self.stats.items())),
        }


#: Per-record mutually-exclusive final categories. Every raw record is
#: classified into exactly one of these BEFORE any conflict items are added.
_NOT_ATTEMPTED = "not_attempted"
_CONSTRUCT_OK = "construct_ok"
_CONSTRUCT_FAIL = "construct_fail"
_EVIDENCE_NONE = "evidence_none"
_UNEXPECTED_ERROR = "unexpected_error"

#: Maps a final category to its additive stats key.
_CATEGORY_COUNT_KEY = {
    _NOT_ATTEMPTED: "N_not_attempted",
    _CONSTRUCT_OK: "N_construct_ok",
    _CONSTRUCT_FAIL: "N_construct_fail",
    _EVIDENCE_NONE: "N_evidence_none",
    _UNEXPECTED_ERROR: "N_unexpected_error",
}


def _intended_record_category(record: Any) -> str:
    """Category a record falls into when ``project_record`` does not raise.

    Returns exactly one of ``not_attempted`` / ``construct_ok`` /
    ``evidence_none``:

    * ``not_attempted`` — the record is not a Mapping; ``project_record``
      short-circuits at the top and never touches ``_build_evidence``.
    * ``construct_ok`` — a candidate: a Mapping whose ``evidence`` is a
      non-empty Mapping (``_build_evidence`` will construct a reference).
    * ``evidence_none`` — a Mapping whose ``evidence`` is missing/empty/
      non-Mapping (``_build_evidence`` returns ``None``).

    The two exception categories (``construct_fail`` / ``unexpected_error``)
    are never guessed here; they are decided by the caller from an exception
    that actually occurred.
    """
    if not isinstance(record, Mapping):
        return _NOT_ATTEMPTED
    evidence = record.get("evidence")
    if isinstance(evidence, Mapping) and evidence:
        return _CONSTRUCT_OK
    return _EVIDENCE_NONE


def _degraded_projected_item(record: Any, category: str, exc: BaseException) -> ProjectedItem:
    """Non-success ``UNKNOWN`` item for a record whose projection raised.

    Carries no conclusion, so a captured per-record exception can never be
    promoted to a success. ``category`` is ``construct_fail`` (a candidate
    whose construction raised) or ``unexpected_error`` (anything else).
    """
    key = {"category": category, "exc": type(exc).__name__, "record": repr(record)[:256]}
    return ProjectedItem(
        projection_id=_stable_id("proj", key),
        object_type="unknown",
        resolution_status="UNKNOWN",
        conclusion_type=ConclusionType.UNKNOWN,
        reasons=(f"{category}:{type(exc).__name__}",),
        limitations=(f"per-record projection raised {type(exc).__name__}; degraded to UNKNOWN",),
        conclusion=None,
        uncertainty=UncertaintyRecord(
            uncertainty_id=_stable_id("unc", key),
            reason=f"per-record projection failed ({category}): {type(exc).__name__}",
            missing_evidence=(category,),
            minimum_resolution="Repair the record or projection path; do not fabricate evidence.",
        ),
    )


def project_records(records: Any) -> ProjectionResult:
    """Project a sequence of parser-contract records.

    Each raw record is classified into exactly one mutually-exclusive final
    category (``_intended_record_category`` plus the two captured-exception
    categories), so the per-record counts are mutually exclusive and every
    record is accounted for. Only exceptions raised while projecting a single
    record are captured and attributed to that record; a batch-level failure
    (``records`` not being a sequence) raises before the loop and is never
    disguised as a per-record count.

    The additive ``N_*`` stats keys (and ``Nr``) expose the classification.
    Existing stats keys keep their meaning; ``conflict`` is the number of extra
    conflict projections ``cf`` (one per conflict group), so
    ``projected_items == Nr + conflict``.

    Raises:
        TypeError: if ``records`` is not a sequence (explicit refusal rather
            than a silent empty projection).
    """
    if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Sequence):
        raise TypeError("records must be a sequence of record mappings")

    category_counts = {key: 0 for key in _CATEGORY_COUNT_KEY.values()}

    projected: list[ProjectedItem] = []
    for record in records:
        try:
            item = project_record(record)
        except Exception as exc:  # captured + attributed to THIS record only
            intended = _intended_record_category(record)
            category = _CONSTRUCT_FAIL if intended == _CONSTRUCT_OK else _UNEXPECTED_ERROR
            category_counts[_CATEGORY_COUNT_KEY[category]] += 1
            projected.append(_degraded_projected_item(record, category, exc))
        else:
            category = _intended_record_category(record)
            category_counts[_CATEGORY_COUNT_KEY[category]] += 1
            projected.append(item)

    conflicts = _detect_conflicts(projected, records)
    projected.extend(conflicts)

    n_raw = len(records)
    n_original_projected = len(projected) - len(conflicts)  # one item per record
    n_construct_ok = category_counts["N_construct_ok"]
    n_construct_fail = category_counts["N_construct_fail"]
    n_evidence_none = category_counts["N_evidence_none"]

    stats = {
        "total_records": n_raw,
        "projected_items": len(projected),
        "static_inference": sum(1 for i in projected if i.conclusion_type is ConclusionType.STATIC_INFERENCE),
        "unknown": sum(1 for i in projected if i.conclusion_type is ConclusionType.UNKNOWN),
        "conflict": sum(1 for i in projected if i.conclusion_type is ConclusionType.CONFLICT),
        "ai_hypothesis": sum(1 for i in projected if i.conclusion_type is ConclusionType.AI_HYPOTHESIS),
        "verified_fact": 0,  # structurally unreachable in this build
        "success_counted": sum(1 for i in projected if i.is_success),
        # --- additive per-record classification counts (Task B) ---
        "N_raw": n_raw,
        "N_candidate": n_construct_ok + n_construct_fail,
        "N_non_candidate": n_evidence_none,
        "N_construct_ok": n_construct_ok,
        "N_construct_fail": n_construct_fail,
        "N_not_attempted": category_counts["N_not_attempted"],
        "N_unexpected_error": category_counts["N_unexpected_error"],
        "N_evidence_none": n_evidence_none,
        "Nr": n_original_projected,
    }
    return ProjectionResult(items=tuple(projected), stats=stats)
