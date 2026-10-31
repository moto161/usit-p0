"""Explicit read-only adapters from legacy USIT schemas to the enhancement model."""
from __future__ import annotations

from typing import Any, Mapping

from .understanding_model import (
    SCHEMA_VERSION, CollectionStatus, ConclusionRecord, ConclusionType,
    EvidenceReference, ExecutionRecord, ExecutionStatus, SourceType, TestSpecification,
    TrustLevel, UncertaintyRecord, _stable_id, _canon_json,
    _dedupe_conclusions_with_conflict, _freeze_json, InvalidModelInputError,
)

TEST_PLAN_SCHEMA = "usit-test-plan-v1"
GRAPH_SCHEMA = "usit-graph-v1"
RESOLUTION_STATES = {"RESOLVED", "UNRESOLVED", "AMBIGUOUS", "DYNAMIC", "EXTERNAL", "UNKNOWN"}
NON_SUCCESS_STATES = {"UNRESOLVED", "AMBIGUOUS", "DYNAMIC", "EXTERNAL", "UNKNOWN"}


class UnsupportedSchemaError(ValueError):
    """Raised when an input schema is unknown or malformed."""


def _expect_schema(source: Mapping[str, Any]) -> str:
    if not isinstance(source, Mapping):
        raise UnsupportedSchemaError("legacy input must be a mapping")
    version = source.get("schema_version")
    # Type must be checked before any set/enum membership test, otherwise an
    # unhashable value (e.g. a list) leaks a raw TypeError.
    if not isinstance(version, str):
        raise UnsupportedSchemaError("schema_version must be a string")
    if version not in {TEST_PLAN_SCHEMA, GRAPH_SCHEMA}:
        raise UnsupportedSchemaError(f"unsupported legacy schema: {version!r}")
    return version


def _evidence(source_schema: str, source_id: str | None, raw: Any,
              *, locator: str, path: Any = None, line: Any = None,
              symbol: Any = None, summary: Any = None) -> EvidenceReference:
    valid_line = line if isinstance(line, int) and not isinstance(line, bool) and line > 0 else None
    return EvidenceReference(
        source_schema=source_schema,
        source_id=source_id,
        relative_path=path if isinstance(path, str) and path else None,
        line=valid_line,
        symbol=symbol if isinstance(symbol, str) and symbol else None,
        locator=locator,
        summary=summary if isinstance(summary, str) and summary else None,
        collection_status=CollectionStatus.PRESENT,
        trust_level=TrustLevel.NOT_VERIFIED,
        content_present=True,
        digest_expected=None,
        digest_match=None,
        source_trusted=False,
        execution_verified=False,
        raw_value=raw,
        mapping_method="explicit-legacy-field-map",
    )


def adapt_legacy(source: Mapping[str, Any]) -> dict[str, Any]:
    """Map an explicitly supplied legacy plan or graph without mutating it.

    The returned object is a separate enhancement document. No filesystem
    reads, test execution, trust establishment, or write-back are performed.
    All generated IDs are deterministic and content-derived (stable), so the
    same legacy input maps to the same IDs across runs.
    """
    version = _expect_schema(source)
    try:
        raw_input = _freeze_json(dict(source))
    except (InvalidModelInputError, TypeError, ValueError) as exc:
        raise UnsupportedSchemaError(f"legacy input is not stable JSON data: {exc}") from exc
    conclusions: list[ConclusionRecord] = []
    evidence: list[EvidenceReference] = []
    specs: list[TestSpecification] = []
    uncertainties: list[UncertaintyRecord] = []

    if version == TEST_PLAN_SCHEMA:
        items = source.get("items")
        if not isinstance(items, list):
            raise UnsupportedSchemaError("usit-test-plan-v1 requires items list")
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                uncertainties.append(UncertaintyRecord(
                    uncertainty_id=_stable_id("unc", version, str(index), "not-an-object"),
                    reason=f"test plan item {index} is not an object",
                    missing_evidence=(f"items[{index}] object",),
                    minimum_resolution="Provide a valid item mapping."))
                continue
            item_raw = _freeze_json(item)
            item_id = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else None
            ev = _evidence(version, item_id, item_raw, locator=f"$.items[{index}]",
                           summary=item.get("goal") if isinstance(item.get("goal"), str) else None)
            evidence.append(ev)
            missing = tuple(name for name, value in (
                ("target", item.get("type")), ("purpose", item.get("goal")),
                ("preconditions", None), ("test_identity_and_data", None),
                ("steps", item.get("suggested_action")), ("expected_result", None),
                ("cleanup_strategy", None), ("evidence_requirements", item.get("evidence")),
            ) if value is None or value == "" or value == [])
            status = item.get("status")
            if status != "NOT_RUN":
                missing += ("legacy execution status is not imported as an actual run",)
            spec = TestSpecification(
                specification_id=_stable_id("legacy-plan", version, item_id, _canon_json(item_raw)),
                target=item.get("type") if isinstance(item.get("type"), str) else None,
                purpose=item.get("goal") if isinstance(item.get("goal"), str) else None,
                steps=(item["suggested_action"],) if isinstance(item.get("suggested_action"), str) else (),
                evidence_requirements=tuple(x for x in item.get("evidence", []) if isinstance(x, str)) if isinstance(item.get("evidence"), list) else (),
                execution=ExecutionRecord(status=ExecutionStatus.NOT_RUN),
                evidence_refs=(ev,), raw_source=item_raw,
                mapping_method="usit-test-plan-v1:items[].{id,type,goal,suggested_action,evidence}",
                incomplete_reasons=missing,
            )
            spec.validate()
            specs.append(spec)
            if missing:
                uncertainties.append(UncertaintyRecord(
                    uncertainty_id=_stable_id("unc", version, index, item_id, *missing),
                    reason=f"legacy test item at index {index} lacks executable test conditions",
                    missing_evidence=missing,
                    minimum_resolution="Specify preconditions, identity/data, expected result, cleanup, and evidence requirements."))
    else:
        nodes = source.get("nodes")
        edges = source.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise UnsupportedSchemaError("usit-graph-v1 requires nodes and edges lists")
        for index, edge in enumerate(edges):
            if not isinstance(edge, Mapping):
                uncertainties.append(UncertaintyRecord(
                    uncertainty_id=_stable_id("unc", version, str(index), "not-an-object"),
                    reason=f"graph edge {index} is not an object",
                    missing_evidence=(f"edges[{index}] object",),
                    minimum_resolution="Provide a valid edge mapping."))
                continue
            edge_raw = _freeze_json(edge)
            edge_id = edge.get("id") if isinstance(edge.get("id"), str) and edge.get("id") else None
            ev_data = edge.get("evidence") if isinstance(edge.get("evidence"), Mapping) else {}
            ev = _evidence(version, edge_id, edge_raw, locator=f"$.edges[{index}]",
                           path=ev_data.get("path"), line=ev_data.get("line"),
                           symbol=ev_data.get("symbol"), summary=ev_data.get("excerpt") or ev_data.get("note"))
            evidence.append(ev)
            state = edge.get("resolution_status")
            # Type-checked before set membership so unhashable states (e.g. a
            # list) do not leak a raw TypeError.
            if not isinstance(state, str) or state not in RESOLUTION_STATES:
                ctype = ConclusionType.UNKNOWN
                limitations = ("resolution state is missing or unsupported", "legacy graph assertions are not runtime proof")
            elif state in NON_SUCCESS_STATES:
                ctype = ConclusionType.UNKNOWN
                limitations = (f"legacy resolution state is {state}", "not counted as verified success")
            else:
                ctype = ConclusionType.STATIC_INFERENCE
                limitations = ("RESOLVED is a graph resolution label, not runtime behavior verification",)
            content = f"Legacy graph edge (id={edge_id!r}): {edge.get('source')!r} -[{edge.get('type')!r}]-> {edge.get('target')!r}; resolution={state!r}."
            semantic_edge = {key: value for key, value in edge_raw.items() if key != "evidence"}
            conclusion = ConclusionRecord(
                conclusion_id=_stable_id("con", version, edge_id, _canon_json(semantic_edge)),
                content=content, conclusion_type=ctype,
                evidence_refs=(ev,), source_type=SourceType.GRAPH,
                confidence=None, scope="supplied graph record",
                limitations=limitations, raw_source=edge_raw,
                mapping_method="usit-graph-v1:edges[].{source,type,target,resolution_status,evidence}")
            conclusion.validate()
            conclusions.append(conclusion)
            if ctype is ConclusionType.UNKNOWN:
                uncertainties.append(UncertaintyRecord(
                    uncertainty_id=_stable_id("unc", version, index, edge_id, _canon_json(state), *limitations),
                    reason=f"edge at index {index} resolution is not verified",
                    missing_evidence=("trusted source capture", "runtime execution evidence"),
                    minimum_resolution="Obtain independently trusted source evidence and, if needed, an actual controlled execution."))

    conclusions = _dedupe_conclusions_with_conflict(
        conclusions, uncertainties=uncertainties
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "source_schema": version,
        "source_trust": "NOT_VERIFIED",
        "raw_input": raw_input,
        "conclusions": conclusions,
        "evidence": evidence,
        "test_specifications": specs,
        "uncertainties": uncertainties,
        "mapping_method": "explicit-read-only-adapter-v1",
    }
