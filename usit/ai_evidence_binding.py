"""Verify claim-ledger references against exact local artifact bytes.

This verifies byte identity and narrow locators only. It does not authenticate
artifact provenance, reviewer identity, or the truth of any claim.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .ai_claim_review import load_claim_ledger
from .test_result_importer import _parse_junit, _read_regular
from .scan_attestation import verify_scan_attestation
from .safeio import is_reparse

BINDING_SCHEMA = "usit-ai-evidence-binding-v1"
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
TEST_STATUSES = {"PASSED", "FAILED", "SKIPPED"}


def _artifact_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("artifact_path must be a non-empty relative POSIX path")
    raw_parts = relative.split("/")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or PureWindowsPath(relative).drive or any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("artifact_path must not be absolute or contain traversal")
    candidate = root
    for part in pure.parts:
        candidate = candidate / part
        try:
            candidate_info = candidate.lstat()
        except FileNotFoundError:
            raise
        if candidate.is_symlink() or is_reparse(candidate_info):
            raise ValueError("evidence artifact path must not contain symbolic links or reparse points")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("evidence artifact path escapes evidence root")
    if not resolved.is_file():
        raise ValueError("evidence artifact must be a regular file")
    return resolved


def _verify_scan_manifest_integrity(root: Path, entry: dict[str, Any], source_path: str,
                                    source_sha256: str) -> dict[str, Any]:
    manifest_path = entry.get("snapshot_manifest_path")
    manifest_sha = entry.get("snapshot_manifest_sha256")
    if manifest_path is None and manifest_sha is None:
        return {"status": "NOT_PROVIDED", "manifest_path": None,
                "manifest_sha256": None,
                "detail": "No scan manifest identity was supplied; source snapshot provenance is unverified."}
    if manifest_path is None or manifest_sha is None:
        return {"status": "INCOMPLETE_DECLARATION", "manifest_path": manifest_path,
                "manifest_sha256": None,
                "detail": "snapshot_manifest_path and snapshot_manifest_sha256 must be supplied together."}
    if not isinstance(manifest_sha, str) or len(manifest_sha) != 64 or any(x not in "0123456789abcdef" for x in manifest_sha):
        return {"status": "INVALID_DECLARATION", "manifest_path": manifest_path,
                "manifest_sha256": None, "detail": "snapshot_manifest_sha256 must be a lowercase SHA-256 digest."}
    try:
        path = _artifact_path(root, manifest_path)
    except FileNotFoundError:
        return {"status": "MANIFEST_MISSING", "manifest_path": manifest_path,
                "manifest_sha256": None, "detail": "Referenced scan manifest is missing."}
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("scan manifest exceeds 50 MiB")
    raw = _read_regular(path, MAX_ARTIFACT_BYTES)
    actual_manifest_sha = hashlib.sha256(raw).hexdigest()
    if actual_manifest_sha != manifest_sha:
        return {"status": "MANIFEST_STALE", "manifest_path": manifest_path,
                "manifest_sha256": actual_manifest_sha,
                "detail": "Scan manifest bytes do not match the declared manifest SHA-256."}
    def reject_duplicate_manifest_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("duplicate JSON object key")
            parsed[key] = value
        return parsed

    try:
        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_manifest_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {"status": "MANIFEST_INVALID", "manifest_path": manifest_path,
                "manifest_sha256": actual_manifest_sha,
                "detail": "Scan manifest is not valid UTF-8 JSON."}
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "usit-manifest-v1" or not isinstance(manifest.get("files"), list):
        return {"status": "MANIFEST_INVALID", "manifest_path": manifest_path,
                "manifest_sha256": actual_manifest_sha,
                "detail": "Manifest schema must be usit-manifest-v1 with a files array."}
    snapshot_file_path = entry.get("snapshot_file_path", source_path)
    matches = [row for row in manifest["files"] if isinstance(row, dict) and row.get("path") == snapshot_file_path]
    if not matches:
        return {"status": "FILE_NOT_IN_MANIFEST", "manifest_path": manifest_path,
                "manifest_sha256": actual_manifest_sha,
                "snapshot_file_path": snapshot_file_path,
                "detail": "Source file path is not present in the supplied scan manifest."}
    if len(matches) != 1:
        return {"status": "MANIFEST_DUPLICATE_PATH", "manifest_path": manifest_path,
                "manifest_sha256": actual_manifest_sha,
                "snapshot_file_path": snapshot_file_path,
                "detail": "Scan manifest contains duplicate entries for the source file path."}
    recorded_sha = matches[0].get("sha256")
    if recorded_sha != source_sha256:
        return {"status": "FILE_HASH_MISMATCH", "manifest_path": manifest_path,
                "manifest_sha256": actual_manifest_sha,
                "snapshot_file_path": snapshot_file_path,
                "manifest_file_sha256": recorded_sha,
                "detail": "Scan manifest file digest does not match the verified source artifact bytes."}
    return {"status": "MATCHED_SCAN_MANIFEST_ENTRY", "manifest_path": manifest_path,
            "manifest_sha256": actual_manifest_sha,
            "snapshot_file_path": snapshot_file_path,
            "manifest_file_sha256": recorded_sha,
            "detail": "Manifest bytes match the declared digest and its exact file entry matches the verified source bytes."}


def _verify_scan_manifest(root: Path, entry: dict[str, Any], source_path: str,
                          source_sha256: str,
                          trusted_public_keys: dict[str, bytes] | None = None) -> dict[str, Any]:
    snapshot = _verify_scan_manifest_integrity(root, entry, source_path, source_sha256)
    attestation_path = entry.get("snapshot_attestation_path")
    snapshot["attestation_status"] = "ATTESTATION_NOT_PROVIDED"
    snapshot["attestation"] = None
    if attestation_path is None:
        return snapshot
    if snapshot.get("status") in {"NOT_PROVIDED", "MANIFEST_MISSING", "MANIFEST_STALE", "MANIFEST_INVALID",
                                  "INCOMPLETE_DECLARATION", "INVALID_DECLARATION"}:
        snapshot["attestation_status"] = "ATTESTATION_MANIFEST_NOT_VERIFIABLE"
        return snapshot
    try:
        manifest_file = _artifact_path(root, entry.get("snapshot_manifest_path"))
        sidecar_file = _artifact_path(root, attestation_path)
    except FileNotFoundError:
        snapshot["attestation_status"] = "ATTESTATION_MISSING"
        return snapshot
    try:
        sidecar_bytes = _read_regular(sidecar_file, 64 * 1024)
        manifest_bytes = _read_regular(manifest_file, MAX_ARTIFACT_BYTES)
    except ValueError as exc:
        snapshot["attestation_status"] = "ATTESTATION_INVALID_FORMAT"
        snapshot["attestation"] = {
            "status": "ATTESTATION_INVALID_FORMAT",
            "detail": str(exc),
        }
        return snapshot
    if hashlib.sha256(manifest_bytes).hexdigest() != snapshot.get("manifest_sha256"):
        snapshot["attestation_status"] = "ATTESTATION_MANIFEST_CHANGED_DURING_VERIFICATION"
        snapshot["attestation"] = {
            "status": "ATTESTATION_MANIFEST_CHANGED_DURING_VERIFICATION",
            "detail": "Manifest bytes changed between snapshot-integrity and attestation verification.",
        }
        return snapshot
    attestation = verify_scan_attestation(
        manifest_bytes, sidecar_bytes,
        trusted_public_keys or {},
        expected_snapshot_id=entry.get("expected_snapshot_id"),
    )
    if (attestation.get("signature_valid") and
            attestation.get("status") in {"ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY",
                                          "ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY"} and
            attestation.get("manifest_path") != entry.get("snapshot_manifest_path")):
        attestation = {**attestation, "status": "ATTESTATION_MANIFEST_PATH_MISMATCH",
                       "detail": "Signed manifest path does not match the evidence catalog path."}
    snapshot["attestation"] = attestation
    snapshot["attestation_status"] = attestation["status"]
    return snapshot


def verify_evidence_bindings(ledger: dict[str, Any], evidence_root: str | Path,
                             ledger_sha256: str | None = None,
                             trusted_public_keys: dict[str, bytes] | None = None) -> dict[str, Any]:
    from .ai_claim_review import validate_claim_ledger

    validate_claim_ledger(ledger)
    root_input = Path(evidence_root).expanduser()
    root_info = root_input.lstat()
    if root_input.is_symlink() or is_reparse(root_info):
        raise ValueError("evidence root must not be a symbolic link or reparse point")
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
            "snapshot_binding_status": "NOT_APPLICABLE",
            "snapshot_binding": None,
            "attestation_status": "NOT_APPLICABLE",
            "snapshot_attestation": None,
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
        if artifact.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError(f"{evidence_id}: evidence artifact exceeds 50 MiB")
        raw = _read_regular(artifact, MAX_ARTIFACT_BYTES)
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
                    snapshot = _verify_scan_manifest(root, entry, relative, actual, trusted_public_keys)
                    row["snapshot_binding"] = snapshot
                    row["snapshot_binding_status"] = snapshot["status"]
                    row["attestation_status"] = snapshot["attestation_status"]
                    row["snapshot_attestation"] = snapshot["attestation"]
                    row.update(binding_status="ARTIFACT_HASH_MATCH_AND_LOCATOR_VALID",
                               detail="Exact source bytes match SHA-256 and the referenced line exists.")
        elif kind == "TEST_RESULT":
            prefix = relative + "#"
            case_id = locator[len(prefix):] if isinstance(locator, str) and locator.startswith(prefix) else ""
            try:
                cases, _ = _parse_junit(raw)
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
        source_bindings = [
            bindings[x] for x in refs if x in bindings and bindings[x]["evidence_type"] == "SOURCE_CODE"
        ]
        source_snapshot_states = [x.get("snapshot_binding_status", "NOT_PROVIDED") for x in source_bindings]
        if not source_snapshot_states:
            snapshot_status = "NOT_APPLICABLE"
        elif all(x == "MATCHED_SCAN_MANIFEST_ENTRY" for x in source_snapshot_states):
            snapshot_status = "ALL_SOURCE_ENTRIES_MATCHED_SCAN_MANIFEST"
        elif all(x == "NOT_PROVIDED" for x in source_snapshot_states):
            snapshot_status = "NOT_PROVIDED"
        else:
            snapshot_status = "CHECK_REQUIRED"
        provenance = []
        for item in source_bindings:
            snap = item.get("snapshot_binding") or {}
            provenance.append({
                "evidence_id": item["evidence_id"],
                "manifest_path": snap.get("manifest_path"),
                "manifest_sha256": snap.get("manifest_sha256"),
                "snapshot_binding_status": item.get("snapshot_binding_status"),
                "attestation_status": item.get("attestation_status", "ATTESTATION_NOT_PROVIDED"),
                "snapshot_id": (item.get("snapshot_attestation") or {}).get("snapshot_id"),
            })
        attestation_states = [x["attestation_status"] for x in provenance]
        if not attestation_states:
            provenance_status = "NOT_APPLICABLE"
        elif (all(x == "ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY" for x in attestation_states)
              and all(x == "MATCHED_SCAN_MANIFEST_ENTRY" for x in source_snapshot_states)):
            provenance_status = "ALL_SOURCE_ATTESTATIONS_TRUSTED"
        elif all(x == "ATTESTATION_NOT_PROVIDED" for x in attestation_states):
            provenance_status = "NOT_PROVIDED"
        else:
            provenance_status = "CHECK_REQUIRED"
        claims.append({
            "claim_id": claim["claim_id"],
            "snapshot_binding_status": snapshot_status,
            "snapshot_provenance_status": provenance_status,
            "snapshot_provenance": provenance,
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
        "attestation_status_counts": {
            key: sum(1 for item in bindings.values() if item.get("attestation_status") == key)
            for key in sorted({item.get("attestation_status") for item in bindings.values()
                               if item.get("attestation_status") not in (None, "NOT_APPLICABLE")})
        },
        "snapshot_binding_status_counts": {
            key: sum(1 for item in bindings.values() if item.get("snapshot_binding_status") == key)
            for key in sorted({item.get("snapshot_binding_status") for item in bindings.values()
                               if item.get("snapshot_binding_status") not in (None, "NOT_APPLICABLE")})
        },
        "claim_count": len(claims),
        "claim_binding_status_counts": {key: claim_counts[key] for key in sorted(claim_counts)},
        "claims": claims,
        "verification_limits": [
            "A matching SHA-256 proves byte identity against the supplied catalog digest, not artifact provenance or authenticity.",
            "SOURCE_CODE verification checks an exact UTF-8 file and line existence; it does not prove runtime execution or semantic support.",
            "An optional usit-manifest-v1 binding checks the manifest digest and exact path-to-file SHA-256 entry; it does not authenticate who produced the manifest or prove the project snapshot was independently captured.",
            "A detached Ed25519 attestation is trusted only when its key is independently supplied by the caller; missing, unknown, or invalid attestations never promote claims to facts.",
            "TEST_RESULT verification checks an exact JUnit testcase and optional expected outcome; it does not prove test adequacy or business correctness.",
            "Unsupported evidence types and references without artifact_path remain UNVERIFIED_REFERENCE.",
            "No model claim or reviewer attestation is promoted to VERIFIED_FACT.",
        ],
        "overall_truth_status": "NOT_ESTABLISHED",
    }


def verify_evidence_bindings_file(ledger_path: str | Path, evidence_root: str | Path,
                                  trusted_public_keys: dict[str, bytes] | None = None) -> dict[str, Any]:
    ledger, digest = load_claim_ledger(ledger_path)
    return verify_evidence_bindings(ledger, evidence_root, digest, trusted_public_keys)
