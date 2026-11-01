"""Evidence-aware review of explicit multi-model claims; never establishes facts."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

LEDGER_SCHEMA = "usit-ai-claim-ledger-v1"
REVIEW_SCHEMA = "usit-ai-claim-review-v1"
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_CLAIMS = 5000
MAX_EVIDENCE = 20000
MAX_TEXT = 20000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVIEW_DECISIONS = {
    "SUPPORTS_CLAIM",
    "CONTRADICTS_CLAIM",
    "NEEDS_MORE_EVIDENCE",
    "CONFLICTING_EVIDENCE",
}


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be an ISO-8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} must be an ISO-8601 timestamp with timezone") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value


def _nonempty(value: Any, label: str, limit: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be a non-empty string of at most {limit} characters")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def load_claim_ledger(path: str | Path) -> tuple[dict[str, Any], str]:
    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError("claim ledger must not be a symbolic link")
    resolved = source.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("claim ledger must be a regular file")
    raw = resolved.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("claim ledger exceeds 10 MiB")
    try:
        ledger = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("claim ledger must be valid UTF-8 JSON") from None
    validate_claim_ledger(ledger)
    return ledger, hashlib.sha256(raw).hexdigest()


def validate_claim_ledger(ledger: Any) -> None:
    if not isinstance(ledger, dict) or ledger.get("schema") != LEDGER_SCHEMA:
        raise ValueError(f"claim ledger schema must be {LEDGER_SCHEMA}")
    claims = ledger.get("claims")
    evidence = ledger.get("evidence_catalog", [])
    reviews = ledger.get("reviews", [])
    if not isinstance(claims, list) or not claims or len(claims) > MAX_CLAIMS:
        raise ValueError(f"claims must contain 1..{MAX_CLAIMS} entries")
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        raise ValueError(f"evidence_catalog must contain at most {MAX_EVIDENCE} entries")
    if not isinstance(reviews, list) or len(reviews) > MAX_CLAIMS:
        raise ValueError("reviews must be a list with at most 5000 entries")

    evidence_ids: set[str] = set()
    for row in evidence:
        if not isinstance(row, dict):
            raise ValueError("each evidence catalog entry must be an object")
        evidence_id = _nonempty(row.get("evidence_id"), "evidence_id", 256)
        if evidence_id in evidence_ids:
            raise ValueError(f"duplicate evidence_id: {evidence_id}")
        evidence_ids.add(evidence_id)
        _nonempty(row.get("evidence_type"), f"{evidence_id}.evidence_type", 64)
        _sha256(row.get("source_sha256"), f"{evidence_id}.source_sha256")
        _nonempty(row.get("locator"), f"{evidence_id}.locator", 2048)

    claim_ids: set[str] = set()
    for row in claims:
        if not isinstance(row, dict):
            raise ValueError("each claim must be an object")
        claim_id = _nonempty(row.get("claim_id"), "claim_id", 256)
        if claim_id in claim_ids:
            raise ValueError(f"duplicate claim_id: {claim_id}")
        claim_ids.add(claim_id)
        _nonempty(row.get("model_id"), f"{claim_id}.model_id", 256)
        _nonempty(row.get("task"), f"{claim_id}.task", 64)
        _nonempty(row.get("claim_text"), f"{claim_id}.claim_text", MAX_TEXT)
        _timestamp(row.get("generated_at"), f"{claim_id}.generated_at")
        group = row.get("comparison_group_id")
        if group is not None:
            _nonempty(group, f"{claim_id}.comparison_group_id", 256)
        refs = row.get("evidence_refs", [])
        if not isinstance(refs, list) or any(not isinstance(x, str) or not x.strip() for x in refs):
            raise ValueError(f"{claim_id}.evidence_refs must be a list of non-empty strings")
        if len(refs) != len(set(refs)):
            raise ValueError(f"{claim_id}.evidence_refs must not contain duplicates")
        uncertainty = row.get("uncertainty", "")
        if not isinstance(uncertainty, (str, list)):
            raise ValueError(f"{claim_id}.uncertainty must be a string or list")
        if isinstance(uncertainty, list) and any(not isinstance(x, str) for x in uncertainty):
            raise ValueError(f"{claim_id}.uncertainty entries must be strings")
        confidence = row.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError(f"{claim_id}.confidence must be a self-reported number from 0 to 1")

    review_ids: set[str] = set()
    for row in reviews:
        if not isinstance(row, dict):
            raise ValueError("each review must be an object")
        claim_id = _nonempty(row.get("claim_id"), "review.claim_id", 256)
        if claim_id not in claim_ids:
            raise ValueError(f"review references unknown claim_id: {claim_id}")
        if claim_id in review_ids:
            raise ValueError(f"duplicate review for claim_id: {claim_id}")
        review_ids.add(claim_id)
        if row.get("decision") not in REVIEW_DECISIONS:
            raise ValueError(f"{claim_id}: unsupported review decision")
        _nonempty(row.get("reviewed_by"), f"{claim_id}.reviewed_by", 256)
        _timestamp(row.get("reviewed_at"), f"{claim_id}.reviewed_at")
        _nonempty(row.get("rationale"), f"{claim_id}.rationale", MAX_TEXT)
        refs = row.get("evidence_refs", [])
        if not isinstance(refs, list) or any(not isinstance(x, str) or not x.strip() for x in refs):
            raise ValueError(f"{claim_id}.review evidence_refs must be a list of non-empty strings")
        if len(refs) != len(set(refs)):
            raise ValueError(f"{claim_id}.review evidence_refs must not contain duplicates")


def review_claim_ledger(ledger: dict[str, Any], ledger_sha256: str | None = None) -> dict[str, Any]:
    validate_claim_ledger(ledger)
    catalog = {row["evidence_id"]: row for row in ledger.get("evidence_catalog", [])}
    review_by_claim = {row["claim_id"]: row for row in ledger.get("reviews", [])}
    groups: dict[str, list[dict[str, Any]]] = {}
    reviewed_claims = []

    for claim in ledger["claims"]:
        refs = claim.get("evidence_refs", [])
        unknown_refs = sorted(set(refs) - set(catalog))
        review = review_by_claim.get(claim["claim_id"])
        review_refs = review.get("evidence_refs", []) if review else []
        unknown_review_refs = sorted(set(review_refs) - set(catalog))
        decision = review.get("decision") if review else "NOT_REVIEWED"
        if not review:
            review_status = "REVIEW_REQUIRED"
        elif unknown_refs or unknown_review_refs:
            review_status = "REVIEW_REQUIRED_UNKNOWN_EVIDENCE_REFERENCE"
        elif decision == "SUPPORTS_CLAIM" and review_refs:
            review_status = "REVIEWED_SUPPORT_CANDIDATE"
        elif decision == "SUPPORTS_CLAIM":
            review_status = "NEEDS_MORE_EVIDENCE"
        elif decision == "CONTRADICTS_CLAIM" and review_refs:
            review_status = "REVIEWED_CONTRADICTION_CANDIDATE"
        elif decision == "CONFLICTING_EVIDENCE" and review_refs:
            review_status = "REVIEWED_CONFLICT"
        else:
            # A reviewer decision without any evidence reference is an
            # assertion only; it must not become a contradiction/conflict
            # candidate in the evidence-fusion report.
            review_status = "NEEDS_MORE_EVIDENCE"

        item = {
            "claim_id": claim["claim_id"],
            "model_id": claim["model_id"],
            "task": claim["task"],
            "claim_text": claim["claim_text"],
            "comparison_group_id": claim.get("comparison_group_id"),
            "evidence_refs": list(refs),
            "resolved_evidence_refs": sorted(set(refs) & set(catalog)),
            "unknown_evidence_refs": unknown_refs,
            "evidence_reference_status": "ALL_REFERENCES_RESOLVE" if refs and not unknown_refs else (
                "UNKNOWN_REFERENCES_PRESENT" if unknown_refs else "NO_EVIDENCE_REFERENCES"
            ),
            "uncertainty": claim.get("uncertainty", ""),
            "confidence": claim.get("confidence"),
            "confidence_semantics": "MODEL_SELF_REPORTED_NOT_CALIBRATED" if claim.get("confidence") is not None else "NOT_PROVIDED",
            "generated_at": claim["generated_at"],
            "claim_status": "MODEL_HYPOTHESIS",
            "review_status": review_status,
            "review_decision": decision,
            "review_attestation": ({
                "reviewed_by": review["reviewed_by"],
                "reviewed_at": review["reviewed_at"],
                "rationale": review["rationale"],
                "evidence_refs": list(review_refs),
                "unknown_evidence_refs": unknown_review_refs,
                "identity_verified": False,
            } if review else None),
            "truth_status": "NOT_ESTABLISHED",
        }
        reviewed_claims.append(item)
        group_id = claim.get("comparison_group_id") or f"UNGROUPED:{claim['claim_id']}"
        groups.setdefault(group_id, []).append(item)

    group_results = []
    for group_id, members in sorted(groups.items()):
        model_ids = sorted({row["model_id"] for row in members})
        normalized_texts = {" ".join(row["claim_text"].split()) for row in members}
        if len(members) == 1:
            comparison = "SINGLE_CLAIM"
        elif len(normalized_texts) == 1 and len(model_ids) > 1:
            comparison = "TEXT_IDENTICAL_MULTI_MODEL_ALIGNMENT_NOT_VERIFICATION"
        elif len(normalized_texts) == 1:
            comparison = "SAME_MODEL_REPEATED_CLAIM_REQUIRES_REVIEW"
        else:
            comparison = "DIVERGENT_TEXT_REQUIRES_HUMAN_REVIEW"
        evidence_sets = [set(row["resolved_evidence_refs"]) for row in members]
        common_refs = sorted(set.intersection(*evidence_sets)) if evidence_sets else []
        group_results.append({
            "comparison_group_id": group_id,
            "claim_count": len(members),
            "distinct_model_count": len(model_ids),
            "model_ids": model_ids,
            "comparison_status": comparison,
            "common_resolved_evidence_refs": common_refs,
            "claims_without_resolved_evidence": [
                row["claim_id"] for row in members if not row["resolved_evidence_refs"]
            ],
            "claims_with_unknown_evidence_refs": [
                row["claim_id"] for row in members if row["unknown_evidence_refs"]
            ],
            "truth_status": "NOT_ESTABLISHED",
            "majority_vote_used": False,
        })

    result = {
        "schema": REVIEW_SCHEMA,
        "input_schema": LEDGER_SCHEMA,
        "input_sha256": ledger_sha256,
        "claim_count": len(reviewed_claims),
        "comparison_group_count": len(group_results),
        "reviewed_claim_count": sum(
            row["review_attestation"] is not None
            and not row["review_attestation"]["unknown_evidence_refs"]
            and not row["unknown_evidence_refs"]
            for row in reviewed_claims
        ),
        "review_required_count": sum(
            row["review_status"].startswith("REVIEW_REQUIRED")
            or row["review_status"] in {"NEEDS_MORE_EVIDENCE", "REVIEWED_CONFLICT"}
            for row in reviewed_claims
        ),
        "unknown_evidence_reference_count": sum(
            len(row["unknown_evidence_refs"]) +
            len((row["review_attestation"] or {}).get("unknown_evidence_refs", []))
            for row in reviewed_claims
        ),
        "claims": reviewed_claims,
        "comparison_groups": group_results,
        "fusion_policy": {
            "model_outputs_are_hypotheses": True,
            "evidence_catalog_entries_are_not_independently_verified": True,
            "reviewer_attestation_is_not_identity_authentication": True,
            "no_semantic_similarity_matching": True,
            "no_majority_vote": True,
            "automatic_verified_fact_promotion": False,
        },
        "overall_truth_status": "NOT_ESTABLISHED",
    }
    return result


def review_claim_ledger_file(input_path: str | Path) -> dict[str, Any]:
    ledger, digest = load_claim_ledger(input_path)
    return review_claim_ledger(ledger, digest)
