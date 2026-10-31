"""Independent, versioned representation for traceable USIT conclusions.

This module models supplied evidence.  It does not capture trusted inputs,
execute tests, or change either legacy schema.

Security invariants (P0.7.18):
* No ``VERIFIED_FACT`` may be produced by ordinary construction until a trusted
  external verification protocol is authorized (it is not, in this build).
* ``PASS`` execution status requires an externally verified execution record,
  which also does not exist yet; hand-crafted PASS is therefore rejected.
* IDs are deterministic and content-derived (stable), never random.
* Nested JSON values are deep-copied and frozen at the model boundary so a
  caller cannot mutate a stored record through an alias.
* Illegal enums / unhashable inputs raise a clean ``ValueError`` /
  ``UnsupportedSchemaError`` — never a raw ``TypeError``.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

SCHEMA_VERSION = "usit-understanding-enhancement-v1"


class ConclusionType(str, Enum):
    VERIFIED_FACT = "VERIFIED_FACT"
    STATIC_INFERENCE = "STATIC_INFERENCE"
    AI_HYPOTHESIS = "AI_HYPOTHESIS"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class SourceType(str, Enum):
    TEST_PLAN = "TEST_PLAN"
    GRAPH = "GRAPH"
    INPUT_MANIFEST = "INPUT_MANIFEST"
    TEST_RESULT = "TEST_RESULT"
    CALLER_PROVIDED = "CALLER_PROVIDED"
    UNKNOWN = "UNKNOWN"


class CollectionStatus(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    NOT_COLLECTED = "NOT_COLLECTED"
    NOT_VERIFIED = "NOT_VERIFIED"


class TrustLevel(str, Enum):
    UNTRUSTED = "UNTRUSTED"
    NOT_VERIFIED = "NOT_VERIFIED"
    TRUSTED = "TRUSTED"


class ExecutionStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_VERIFIED = "NOT_VERIFIED"


class InvalidModelInputError(ValueError):
    """Raised for unsupported JSON values or un-stably-normalizable model input."""


def _canon_json(value: Any) -> Any:
    """Canonical, hashable, JSON-equivalent form of a *supported* value.

    Supported: ``None``/``str``/``int``/``float``/``bool``, ``list``/``tuple``
    (recursed), ``dict`` (recursed, key-sorted). The model's own frozen
    dataclass instances are already immutable and pass through untouched.
    Anything else (``set``, ``bytes``, arbitrary mutable objects) is rejected so
    callers never see a silent mis-freeze.
    """
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidModelInputError("non-finite floats are not valid JSON values")
        return value
    if isinstance(value, (list, tuple)):
        return ["$array", [_canon_json(v) for v in value]]
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise InvalidModelInputError("JSON object keys must be strings")
        return ["$object", {k: _canon_json(value[k]) for k in sorted(value)}]
    if isinstance(value, Enum):
        return _canon_json(value.value)
    if is_dataclass(value):
        params = getattr(value, "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise InvalidModelInputError("mutable dataclass values are not supported")
        return ["$record", type(value).__qualname__, {
            f.name: _canon_json(getattr(value, f.name)) for f in fields(value)
        }]
    raise InvalidModelInputError(
        f"unsupported JSON value type for model boundary: {type(value).__name__}"
    )


def _freeze_json(value: Any, _active: set[int] | None = None, _path: str = "$") -> Any:
    """Deep-copy then recursively freeze a supported JSON value.

    The returned structure is immutable, so later caller-side mutation of the
    original input cannot alias into a stored record.
    """
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidModelInputError("non-finite floats are not valid JSON values")
        return value
    if _active is None:
        _active = set()
    tracked = isinstance(value, (list, tuple, Mapping)) or is_dataclass(value) or isinstance(value, Enum)
    marker = id(value)
    if tracked and marker in _active:
        raise InvalidModelInputError(f"cyclic JSON value at {_path}")
    if tracked:
        _active.add(marker)
    try:
        if isinstance(value, (list, tuple)):
            return tuple(_freeze_json(item, _active, f"{_path}[{index}]") for index, item in enumerate(value))
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise InvalidModelInputError(f"JSON object keys must be strings at {_path}")
            return MappingProxyType({
                key: _freeze_json(item, _active, f"{_path}[{key!r}]")
                for key, item in value.items()
            })
        if isinstance(value, Enum):
            return _freeze_json(value.value, _active, f"{_path}.value")
        if is_dataclass(value):
            params = getattr(value, "__dataclass_params__", None)
            if params is not None and params.frozen:
                return MappingProxyType({
                    "$type": type(value).__qualname__,
                    **{
                        item.name: _freeze_json(
                            getattr(value, item.name), _active, f"{_path}.{item.name}"
                        )
                        for item in fields(value)
                    },
                })
    finally:
        if tracked:
            _active.remove(marker)
    raise InvalidModelInputError(
        f"unsupported JSON value type for model boundary: {type(value).__name__}"
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    """Deterministic, content-derived ID.

    Same logical input maps to the same ID; different content maps to a
    different ID. Inputs that cannot be stably normalized raise
    ``InvalidModelInputError`` instead of falling back to a random ID.
    """
    if not isinstance(prefix, str) or not prefix:
        raise InvalidModelInputError("stable ID prefix must be a non-empty string")
    canonical = json.dumps(
        [_canon_json(p) for p in parts],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _content_digest(record: "ConclusionRecord") -> str:
    return hashlib.sha256(
        json.dumps(_canon_json(record.content), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _freeze_sequence(value: Any, field_name: str) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{field_name} must be a list or tuple")
    if all(
        is_dataclass(item)
        and getattr(item, "__dataclass_params__", None) is not None
        and item.__dataclass_params__.frozen
        for item in value
    ):
        return tuple(value)
    return tuple(_freeze_json(item) for item in value)


@dataclass(frozen=True)
class EvidenceReference:
    source_schema: str | None
    source_id: str | None
    relative_path: str | None = None
    line: int | None = None
    symbol: str | None = None
    locator: str | None = None
    summary: str | None = None
    collection_status: CollectionStatus = CollectionStatus.NOT_VERIFIED
    trust_level: TrustLevel = TrustLevel.NOT_VERIFIED
    content_present: bool = False
    digest_expected: str | None = None
    digest_match: bool | None = None
    source_trusted: bool = False
    execution_verified: bool = False
    raw_value: Any = None
    mapping_method: str = "explicit"

    def __post_init__(self) -> None:
        if not isinstance(self.source_trusted, bool) or not isinstance(self.execution_verified, bool):
            raise ValueError("trust and execution flags must be bool")
        if not isinstance(self.collection_status, CollectionStatus):
            raise ValueError("collection_status must be a supported CollectionStatus")
        if not isinstance(self.trust_level, TrustLevel):
            raise ValueError("trust_level must be a supported TrustLevel")
        if self.source_trusted or self.execution_verified or self.trust_level is TrustLevel.TRUSTED:
            raise ValueError("caller-supplied trust claims are not accepted")
        for name in ("source_schema", "source_id", "relative_path", "symbol", "locator", "summary", "digest_expected", "mapping_method"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")
        if self.line is not None and (not isinstance(self.line, int) or isinstance(self.line, bool) or self.line < 1):
            raise ValueError("line must be a positive one-based integer or None")
        if not isinstance(self.content_present, bool):
            raise ValueError("content_present must be bool")
        if self.digest_match is not None and not isinstance(self.digest_match, bool):
            raise ValueError("digest_match must be bool or None")
        object.__setattr__(self, "raw_value", _freeze_json(self.raw_value))

    def validate(self) -> None:
        for name in ("source_schema", "source_id", "relative_path", "symbol", "locator", "summary", "digest_expected", "mapping_method"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")
        if not isinstance(self.collection_status, CollectionStatus):
            raise ValueError("collection_status must be a supported CollectionStatus")
        if not isinstance(self.trust_level, TrustLevel):
            raise ValueError("trust_level must be a supported TrustLevel")
        if self.line is not None and (
            not isinstance(self.line, int) or isinstance(self.line, bool) or self.line < 1
        ):
            raise ValueError("line must be a positive one-based integer or None")
        if self.digest_match is True and not self.digest_expected:
            raise ValueError("digest_match cannot be true without an expected digest")
        if self.source_trusted and self.trust_level is not TrustLevel.TRUSTED:
            raise ValueError("source_trusted requires TRUSTED trust_level")
        if self.trust_level is TrustLevel.TRUSTED and not self.source_trusted:
            raise ValueError("TRUSTED trust_level requires source_trusted=true")
        if self.execution_verified and self.collection_status is not CollectionStatus.PRESENT:
            raise ValueError("execution_verified requires present evidence")


@dataclass(frozen=True)
class ConclusionRecord:
    conclusion_id: str
    content: str
    conclusion_type: ConclusionType
    evidence_refs: tuple[EvidenceReference, ...] = ()
    source_type: SourceType = SourceType.UNKNOWN
    confidence: float | None = None
    scope: str | None = None
    limitations: tuple[str, ...] = ()
    raw_source: Any = None
    mapping_method: str = "explicit"

    def __post_init__(self) -> None:
        if not isinstance(self.conclusion_type, ConclusionType):
            raise ValueError("conclusion_type must be a supported ConclusionType")
        if not isinstance(self.source_type, SourceType):
            raise ValueError("source_type must be a supported SourceType")
        if self.conclusion_type is ConclusionType.VERIFIED_FACT:
            raise ValueError("VERIFIED_FACT requires a trusted external verification protocol; none authorized")
        if not isinstance(self.conclusion_id, str) or not self.conclusion_id.strip():
            raise ValueError("conclusion_id is required")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content is required")
        if self.confidence is not None and (isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))):
            raise ValueError("confidence must be numeric or None")
        object.__setattr__(self, "evidence_refs", _freeze_sequence(self.evidence_refs, "evidence_refs"))
        if any(not isinstance(ref, EvidenceReference) for ref in self.evidence_refs):
            raise ValueError("evidence_refs entries must be EvidenceReference")
        object.__setattr__(self, "limitations", _freeze_sequence(self.limitations, "limitations"))
        if any(not isinstance(value, str) for value in self.limitations):
            raise ValueError("limitations entries must be strings")
        if self.scope is not None and not isinstance(self.scope, str):
            raise ValueError("scope must be a string or None")
        if not isinstance(self.mapping_method, str):
            raise ValueError("mapping_method must be a string")
        object.__setattr__(self, "raw_source", _freeze_json(self.raw_source))

    def validate(self) -> None:
        if not self.conclusion_id or not isinstance(self.conclusion_id, str):
            raise ValueError("conclusion_id is required")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content is required")
        if not isinstance(self.conclusion_type, ConclusionType):
            raise ValueError("conclusion_type must be a supported ConclusionType")
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("confidence must be a number between 0 and 1 or None")
        for ref in self.evidence_refs:
            ref.validate()
        # Hard block: no trusted external verification protocol is authorized in
        # this build, so VERIFIED_FACT may never be produced by construction.
        # We do NOT introduce another self-attested boolean to "enable" it.
        if self.conclusion_type is ConclusionType.VERIFIED_FACT:
            raise ValueError(
                "VERIFIED_FACT requires a trusted external verification protocol; "
                "none authorized in this build"
            )


@dataclass(frozen=True)
class ExecutionRecord:
    status: ExecutionStatus = ExecutionStatus.NOT_RUN
    run_id: str | None = None
    actual_result: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError("execution.status must be a supported ExecutionStatus")
        if self.status is not ExecutionStatus.NOT_RUN:
            raise ValueError("static model construction only permits NOT_RUN execution status")
        if self.run_id is not None and not isinstance(self.run_id, str):
            raise ValueError("run_id must be None for NOT_RUN execution")
        if self.run_id is not None or self.actual_result is not None:
            raise ValueError("NOT_RUN execution must have empty run_id and actual_result")
        object.__setattr__(self, "actual_result", _freeze_json(self.actual_result))

    def validate(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError("execution.status must be a supported ExecutionStatus")
        # No trusted external execution-verification protocol is authorized in
        # this build; a PASS claim without one is rejected, never counted as a
        # verified success.
        if self.status is ExecutionStatus.PASS:
            raise ValueError(
                "PASS requires an externally verified execution record; "
                "none authorized in this build"
            )


@dataclass(frozen=True)
class TestSpecification:
    specification_id: str
    target: str | None
    purpose: str | None
    preconditions: tuple[str, ...] = ()
    identity_and_data_requirements: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    expected_result: str | None = None
    cleanup_strategy: str | None = None
    evidence_requirements: tuple[str, ...] = ()
    execution: ExecutionRecord = field(default_factory=ExecutionRecord)
    evidence_refs: tuple[EvidenceReference, ...] = ()
    raw_source: Any = None
    mapping_method: str = "explicit"
    incomplete_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("preconditions", "identity_and_data_requirements", "steps", "evidence_requirements", "evidence_refs", "incomplete_reasons"):
            object.__setattr__(self, name, _freeze_sequence(getattr(self, name), name))
        for name in ("preconditions", "identity_and_data_requirements", "steps", "evidence_requirements", "incomplete_reasons"):
            if any(not isinstance(value, str) for value in getattr(self, name)):
                raise ValueError(f"{name} entries must be strings")
        if not isinstance(self.specification_id, str) or not self.specification_id.strip():
            raise ValueError("specification_id is required")
        if not isinstance(self.execution, ExecutionRecord):
            raise ValueError("execution must be an ExecutionRecord")
        for name in ("target", "purpose", "expected_result", "cleanup_strategy", "mapping_method"):
            value = getattr(self, name)
            if name == "mapping_method":
                if not isinstance(value, str):
                    raise ValueError("mapping_method must be a string")
            elif value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")
        if any(not isinstance(ref, EvidenceReference) for ref in self.evidence_refs):
            raise ValueError("evidence_refs entries must be EvidenceReference")
        object.__setattr__(self, "raw_source", _freeze_json(self.raw_source))
        # ``complete`` is derived from the real required fields, not merely from
        # a caller-supplied incomplete_reasons flag. The caller-supplied value
        # is preserved as an additional note and unioned with the derived set.
        derived: list[str] = []
        if not isinstance(self.target, str) or not self.target.strip():
            derived.append("target")
        if not isinstance(self.purpose, str) or not self.purpose.strip():
            derived.append("purpose")
        if not self.preconditions or any(not isinstance(x, str) or not x.strip() for x in self.preconditions):
            derived.append("preconditions")
        if not self.identity_and_data_requirements or any(not isinstance(x, str) or not x.strip() for x in self.identity_and_data_requirements):
            derived.append("identity_and_data_requirements")
        if not self.steps or any(not isinstance(x, str) or not x.strip() for x in self.steps):
            derived.append("steps")
        if not isinstance(self.expected_result, str) or not self.expected_result.strip():
            derived.append("expected_result")
        if not isinstance(self.cleanup_strategy, str) or not self.cleanup_strategy.strip():
            derived.append("cleanup_strategy")
        if not self.evidence_requirements or any(not isinstance(x, str) or not x.strip() for x in self.evidence_requirements):
            derived.append("evidence_requirements")
        merged = tuple(dict.fromkeys(list(derived) + list(self.incomplete_reasons)))
        object.__setattr__(self, "incomplete_reasons", merged)

    @property
    def complete(self) -> bool:
        return not self.incomplete_reasons

    def validate(self) -> None:
        if not self.specification_id or not isinstance(self.specification_id, str):
            raise ValueError("specification_id is required")
        if not isinstance(self.execution, ExecutionRecord):
            raise ValueError("execution is required")
        self.execution.validate()
        if not isinstance(self.execution.status, ExecutionStatus):
            raise ValueError("execution.status must be a supported ExecutionStatus")
        if self.execution.status is ExecutionStatus.NOT_RUN:
            if self.execution.run_id is not None or self.execution.actual_result is not None:
                raise ValueError("NOT_RUN specifications must have empty run_id and actual_result")
        for ref in self.evidence_refs:
            ref.validate()


@dataclass(frozen=True)
class UncertaintyRecord:
    uncertainty_id: str
    reason: str
    missing_evidence: tuple[str, ...] = ()
    conflicting_conclusion_ids: tuple[str, ...] = ()
    decision_required: str | None = None
    minimum_resolution: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "missing_evidence", _freeze_sequence(self.missing_evidence, "missing_evidence"))
        object.__setattr__(self, "conflicting_conclusion_ids", _freeze_sequence(self.conflicting_conclusion_ids, "conflicting_conclusion_ids"))
        if not isinstance(self.reason, str):
            raise ValueError("reason must be a string")
        if not isinstance(self.uncertainty_id, str) or not self.uncertainty_id.strip():
            raise ValueError("uncertainty_id is required")
        if any(not isinstance(value, str) for value in (*self.missing_evidence, *self.conflicting_conclusion_ids)):
            raise ValueError("uncertainty evidence and conclusion IDs must be strings")

    def validate(self) -> None:
        if not self.uncertainty_id or not self.reason.strip():
            raise ValueError("uncertainty_id and reason are required")


def _dedupe_conclusions_with_conflict(
    conclusions: list[ConclusionRecord],
    *,
    uncertainties: list[UncertaintyRecord] | None = None,
) -> list[ConclusionRecord]:
    """Remove exact same-ID duplicates and link only supported graph conflicts.

    A shared ID or a different payload alone does not prove contradiction.
    Distinct semantic records are retained; graph conflicts are linked only
    when the adapter supplied the same edge identity and relation endpoints
    but different targets.
    """
    out: list[ConclusionRecord] = []
    seen: set[tuple[str, str]] = set()
    graph_claim_indexes: dict[tuple[str, str], int] = {}
    for c in conclusions:
        signature = _semantic_signature(c)
        key = (c.conclusion_id, signature)
        if key in seen:
            continue
        seen.add(key)
        graph_key = _graph_claim_signature(c)
        if graph_key is not None and graph_key in graph_claim_indexes:
            position = graph_claim_indexes[graph_key]
            previous = out[position]
            refs = list(previous.evidence_refs)
            ref_signatures = {
                json.dumps(_canon_json(ref), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                for ref in refs
            }
            for ref in c.evidence_refs:
                ref_signature = json.dumps(
                    _canon_json(ref), sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                if ref_signature not in ref_signatures:
                    refs.append(ref)
                    ref_signatures.add(ref_signature)
            out[position] = replace(previous, evidence_refs=tuple(refs))
            continue
        if graph_key is not None:
            graph_claim_indexes[graph_key] = len(out)
        out.append(c)

    graph_claims: dict[tuple[str, str, str, str], list[tuple[str, ConclusionRecord]]] = {}
    for c in out:
        raw = c.raw_source
        if c.source_type is not SourceType.GRAPH or not isinstance(raw, Mapping):
            continue
        ref = next((r for r in c.evidence_refs if r.source_id), None)
        source_id = ref.source_id if ref else None
        source = raw.get("source")
        relation = raw.get("type")
        target = raw.get("target")
        if not all(isinstance(v, str) and v for v in (ref.source_schema if ref else None, source_id, source, relation, target)):
            continue
        identity = (ref.source_schema, source_id, source, relation)
        graph_claims.setdefault(identity, []).append((target, c))

    for identity, claims in graph_claims.items():
        ordered = sorted(claims, key=lambda item: (item[0], item[1].conclusion_id))
        for index, (left_target, left) in enumerate(ordered):
            for right_target, right in ordered[index + 1:]:
                if left_target == right_target:
                    continue
                left_id, right_id = sorted((left.conclusion_id, right.conclusion_id))
                reason = (
                    f"graph edge identity {identity[1]!r} asserts different targets "
                    f"for {identity[2]!r} -[{identity[3]!r}]->"
                )
                conflict, uncertainty = make_conflict(left, right, reason)
                conflict = replace(
                    conflict,
                    conclusion_id=_stable_id("con", left_id, right_id, reason),
                )
                if not any(existing.conclusion_id == conflict.conclusion_id for existing in out):
                    out.append(conflict)
                if uncertainties is not None and uncertainty not in uncertainties:
                    uncertainties.append(uncertainty)
    return out


def _semantic_signature(record: "ConclusionRecord") -> str:
    """Canonical signature of all fields that can change a conclusion's meaning."""
    value = {
        "content_digest": _content_digest(record),
        "conclusion_type": record.conclusion_type.value,
        "source_type": record.source_type.value,
        "confidence": record.confidence,
        "scope": record.scope,
        "limitations": record.limitations,
        "raw_source": record.raw_source,
        "evidence_refs": record.evidence_refs,
        "mapping_method": record.mapping_method,
    }
    return json.dumps(_canon_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _graph_claim_signature(record: "ConclusionRecord") -> tuple[str, str] | None:
    """Key equivalent graph claims while retaining all distinct evidence refs."""
    raw = record.raw_source
    if record.source_type is not SourceType.GRAPH or not isinstance(raw, Mapping):
        return None
    source_refs = sorted({
        (ref.source_schema, ref.source_id)
        for ref in record.evidence_refs
        if ref.source_schema and ref.source_id
    })
    if not source_refs:
        return None
    semantic_raw = {key: value for key, value in raw.items() if key != "evidence"}
    value = {
        "content_digest": _content_digest(record),
        "conclusion_type": record.conclusion_type.value,
        "source_type": record.source_type.value,
        "confidence": record.confidence,
        "scope": record.scope,
        "limitations": record.limitations,
        "raw_source": semantic_raw,
        "mapping_method": record.mapping_method,
    }
    signature = json.dumps(_canon_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    source_identity = json.dumps(source_refs, separators=(",", ":"), ensure_ascii=False)
    return record.conclusion_id, source_identity + "|" + signature


def make_conflict(
    left: ConclusionRecord,
    right: ConclusionRecord,
    reason: str,
) -> tuple[ConclusionRecord, UncertaintyRecord]:
    """Preserve both claims and create an explicit conflict record.

    The caller must supply the semantic conflict reason; this helper does not
    infer that differing values are contradictory.
    """
    if not reason or not reason.strip():
        raise ValueError("a conflict reason is required")
    merged: list[EvidenceReference] = []
    for ref in (*left.evidence_refs, *right.evidence_refs):
        # Preserve all references, including same-identity references whose
        # content may differ. Deduplicating by a caller-controlled identity can
        # silently discard one side of an evidence conflict.
        merged.append(ref)
    merged_refs = tuple(merged)
    conflict = ConclusionRecord(
        conclusion_id=_stable_id("con", left.conclusion_id, right.conclusion_id, reason),
        content=reason,
        conclusion_type=ConclusionType.CONFLICT,
        evidence_refs=merged_refs,
        source_type=SourceType.CALLER_PROVIDED,
        confidence=None,
        scope="caller-declared conflict; adjudication pending",
        limitations=("both source conclusions are retained unchanged",),
        raw_source={"left": {"id": left.conclusion_id, "content": left.content, "evidence_refs": left.evidence_refs},
                    "right": {"id": right.conclusion_id, "content": right.content, "evidence_refs": right.evidence_refs}},
        mapping_method="explicit-conflict-link",
    )
    uncertainty = UncertaintyRecord(
        uncertainty_id=_stable_id("unc", left.conclusion_id, right.conclusion_id, reason),
        reason=reason,
        conflicting_conclusion_ids=(left.conclusion_id, right.conclusion_id),
        minimum_resolution="Review both cited sources and record an independently supported resolution.",
    )
    conflict.validate()
    uncertainty.validate()
    return conflict, uncertainty


def validate_document(document: Mapping[str, Any]) -> None:
    """Validate a serialized enhancement document's required top-level shape."""
    if not isinstance(document, Mapping):
        raise ValueError("document must be a mapping")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported enhancement schema version")
    for key in ("conclusions", "evidence", "test_specifications", "uncertainties"):
        if key not in document or not isinstance(document[key], list):
            raise ValueError(f"{key} must be a required list")
