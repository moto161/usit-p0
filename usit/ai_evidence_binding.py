"""Verify claim-ledger references against exact local artifact bytes.

This verifies byte identity and narrow locators only. It does not authenticate
artifact provenance, reviewer identity, or the truth of any claim.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .ai_claim_review import load_claim_ledger
from .test_result_importer import MAX_REPORT_BYTES, _parse_junit

BINDING_SCHEMA = "usit-ai-evidence-binding-v1"
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
TEST_STATUSES = {"PASSED", "FAILED", "SKIPPED"}


def _artifact_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("artifact_path must be a non-empty relative POSIX path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("artifact_path must not be absolute or contain traversal")
    candidate = root
    for part in pure.parts:
        candidate = candidate / part
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError:
            raise
        if candidate.is_symlink():
            raise ValueError("evidence artifact path must not contain symbolic links")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("evidence artifact path escapes evidence root")
    if not resolved.is_file():
        raise ValueError("evidence artifact must be a regular file")
    return resolved


def verify_evidence_bindings(ledger: dict[str, Any], evidence_root: str | Path,
                             ledger_sha256: str | None = None) -> dict[str, Any]:
    from .ai_claim_review import validate_claim_ledger

    validate_claim_ledger(ledger)
    root_input = Path(evidence_root).expanduser()
    if root_input.is_symlink():
        raise ValueError("evidence root must not be a symbolic link")
    root = root_input.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("evidence root must be a directory")

    catalog = {row["evidence_id"]: row for row in ledger.get("evidence_catalog", [])}
    bindings: dict[str, dict[str, Any]] = {}
    for evidence_id, entry in sorted(catalog.items()):
        row = {
            "evidence_id": evidence_id,
            "evidence_type": entry["evidence_type"],
            "artifact_path": entry.get("artifact_path"),
            "expected_sha256": entry["source_sha256"],
            "actual_sha256": None,
            "locator": entry["locator"],
            "binding_status": "UNVERIFIED_REFERENCE",
            "detail": "No local artifact_path was supplied; catalog presence is not artifact verification.",
        }
        relative = entry.get("artifact_path")
        if relative is None:
            bindings[evidence_id] = row
            continue
        try:
            artifact = _artifact_path(root, relative)
        except FileNotFoundError:
            row.update(binding_status="MISSING", detail="Referenced artifact does not exist under evidence_root.")
            bindings[evidence_id] = row
            continue
        raw = artifact.read_bytes()
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError(f"{evidence_id}: evidence artifact exceeds 50 MiB")
        actual = hashlib.sha256(raw).hexdigest()
        row["actual_sha256"] = actual
        if actual != entry["source_sha256"]:
            row.update(binding_status="STALE", detail="Artifact bytes do not match the catalog SHA-256.")
            bindings[evidence_id] = row
            continue

        kind = entry["evidence_type"]
        locator = entry["locator"]
        if kind == "SOURCE_CODE":
            prefix = relative + ":"
            line_text = locator[len(prefix):] if isinstance(locator, str) and locator.startswith(prefix) else ""
            if not line_text.isdigit() or int(line_text) < 1:
                row.update(binding_status="INVALID_LOCATOR", detail="SOURCE_CODE locator must be artifact_path:positive_line.")
            else:
                try:
                    line_count = len(raw.decode("utf-8").splitlines())
                except UnicodeDecodeError:
                    line_count = -1
                if line_count < 0 or int(line_text) > line_count:
                    row.update(binding_status="INVALID_LOCATOR", detail="Source line is outside the UTF-8 artifact.")
                else:
                    row.update(binding_status="ARTIFACT_HASH_MATCH_AND_LOCATOR_VALID",
                               detail="Exact source bytes match SHA-256 and the referenced line exists.")
        elif kind == "TEST_RESULT":
            prefix = relative + "#"
            case_id = locator[len(prefix):] if isinstance(locator, str) and locator.startswith(prefix) else ""
            try:
                cases = _parse_junit(raw)
                case = next((item for item in cases if item["test_case_id"] == case_id), None)
            except ValueError:
                case = None
            if not case_id or case is None:
                row.update(binding_status="INVALID_LOCATOR", detail="JUnit locator does not identify an exact testcase in this artifact.")
            else:
                expected = entry.get("expected_test_status")
                if expected is not None and expected not in TEST_STATUSES:
                    raise ValueError(f"{evidence_id}: expected_test_status must be PASSED, FAILED or SKIPPED")
                if expected is not None and expected != case["status"]:
                    row.update(binding_status="CONFLICT", detail=f"Catalog expected {expected}, imported JUnit testcase is {case['status']}.")
                else:
                    row.update(binding_status="ARTIFACT_HASH_MATCH_AND_LOCATOR_VALID",
                               detail=f"Exact JUnit testcase exists with observed status {case['status']}.",
                               observed_test_status=case["status"])
        else:
            row.update(binding_status="UNVERIFIED_REFERENCE",
                       detail="Artifact bytes match, but this evidence_type has no supported locator verifier.")
        bindings[evidence_id] = row

    claims = []
    claim_counts: dict[str, int] = {}
    evidence_counts: dict[str, int] = {}
    for binding in bindings.values():
        key = binding["binding_status"]
        evidence_counts[key] = evidence_counts.get(key, 0) + 1
    for claim in ledger["claims"]:
        refs = claim.get("evidence_refs", [])
        statuses = [bindings[x]["binding_status"] for x in refs if x in bindings]
        unknown = sorted(set(refs) - set(bindings))
        if unknown:
            status = "UNKNOWN_REFERENCE"
        elif not refs:
            status = "NO_EVIDENCE_REFERENCES"
        elif "CONFLICT" in statuses:
            status = "CONFLICT"
        elif "STALE" in statuses:
            status = "STALE"
        elif "MISSING" in statuses:
            status = "MISSING"
        elif "INVALID_LOCATOR" in statuses:
            status = "INVALID_LOCATOR"
        elif "UNVERIFIED_REFERENCE" in statuses:
            status = "UNVERIFIED_REFERENCE"
        elif all(x == "ARTIFACT_HASH_MATCH_AND_LOCATOR_VALID" for x in statuses):
            status = "ALL_BINDINGS_RESOLVED_NOT_AUTHENTICATED"
        else:
            status = "UNVERIFIED_REFERENCE"
        claim_counts[status] = claim_counts.get(status, 0) + 1
        claims.append({
            "claim_id": claim["claim_id"],
            "evidence_refs": list(refs),
            "unknown_evidence_refs": unknown,
            "binding_status": status,
            "truth_status": "NOT_ESTABLISHED",
        })

    return {
        "schema": BINDING_SCHEMA,
        "input_schema": ledger["schema"],
        "input_sha256": ledger_sha256,
        "evidence_root": str(root),
        "evidence_count": len(bindings),
        "binding_status_counts": {key: evidence_counts[key] for key in sorted(evidence_counts)},
        "evidence_bindings": list(bindings.values()),
        "claim_count": len(claims),
        "claim_binding_status_counts": {key: claim_counts[key] for key in sorted(claim_counts)},
        "claims": claims,
        "verification_limits": [
            "A matching SHA-256 proves byte identity against the supplied catalog digest, not artifact provenance or authenticity.",
            "SOURCE_CODE verification checks an exact UTF-8 file and line existence; it does not prove runtime execution or semantic support.",
            "TEST_RESULT verification checks an exact JUnit testcase and optional expected outcome; it does not prove test adequacy or business correctness.",
            "Unsupported evidence types and references without artifact_path remain UNVERIFIED_REFERENCE.",
            "No model claim or reviewer attestation is promoted to VERIFIED_FACT.",
        ],
        "overall_truth_status": "NOT_ESTABLISHED",
    }


def verify_evidence_bindings_file(ledger_path: str | Path, evidence_root: str | Path) -> dict[str, Any]:
    ledger, digest = load_claim_ledger(ledger_path)
    return verify_evidence_bindings(ledger, evidence_root, digest)
