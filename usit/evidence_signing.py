"""HMAC-SHA256 signing for evidence documents produced by the parent process.

The parent process generates one random 32-byte key per run, keeps it in
memory only while the test process runs, signs the execution provenance
record and the final evidence manifest after the test process exits, and
then writes the key to a 0600 file OUTSIDE the evidence directory so a
later, independent verification can re-check the signatures.

What a VALID signature proves: the document was produced by the process
that held the per-run key (the parent), and its content has not changed
since signing. What it does NOT prove: that the test process actually
executed the tests it reports -- that is runner authenticity, which stays
UNAUTHENTICATED until OS-level UID isolation exists. A test process that
steals the key (same-UID memory read on platforms that allow it) could
forge a signature, so this raises the forgery bar from "rewrite a JSON
file" to "steal a key the parent never writes to disk during the run";
it does not replace isolation.

This module is deliberately dependency-free (stdlib only): it is the trust
root for the documents it authenticates, so it must not import the modules
whose output it signs. Its canonical-JSON helper intentionally duplicates
the one in test_execution_provenance for the same reason; a test asserts
both produce identical bytes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Mapping

SIGNATURE_ALGORITHM = "HMAC-SHA256"
KEY_SCHEMA_VERSION = "usit-run-key-v1"
KEY_BYTES = 32
MIN_KEY_BYTES = 16
_SIGNATURE_FIELD = "signature"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^run_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{32}$")

# Signature verification outcomes. WRONG_KEY is distinct from INVALID: a
# fingerprint mismatch means the verifier was given the wrong run's key,
# while INVALID means the signed content changed after signing.
SIGNATURE_STATUSES = frozenset(
    {"VALID", "INVALID", "MISSING", "MALFORMED", "WRONG_KEY", "NOT_CHECKED"}
)


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic UTF-8 JSON bytes; reject non-finite numbers."""
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not canonical-JSON serializable: {exc}") from exc


def generate_run_key() -> bytes:
    """One random per-run HMAC key; the parent keeps it in memory only."""
    return secrets.token_bytes(KEY_BYTES)


def key_fingerprint(key: bytes) -> str:
    """SHA-256 of the key, stored in the signature so a wrong key is diagnosable."""
    return hashlib.sha256(_require_key(key)).hexdigest()


def _require_key(key: Any) -> bytes:
    if not isinstance(key, (bytes, bytearray)) or len(key) < MIN_KEY_BYTES:
        raise ValueError(f"HMAC key must be bytes of at least {MIN_KEY_BYTES} bytes")
    return bytes(key)


def sign_document(document: Mapping[str, Any], key: bytes) -> dict[str, Any]:
    """Return the signature block for a document (does not mutate it).

    The HMAC covers the canonical JSON of the document WITHOUT its
    signature field, so a signed document can be verified by removing the
    field and recomputing. Re-signing after an edit replaces the block.
    """
    key = _require_key(key)
    if not isinstance(document, Mapping):
        raise ValueError("signed document must be a mapping")
    payload = {k: v for k, v in document.items() if k != _SIGNATURE_FIELD}
    digest = hmac.new(key, canonical_json_bytes(payload), hashlib.sha256).hexdigest()
    return {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_fingerprint": hashlib.sha256(key).hexdigest(),
        "value": digest,
    }


def verify_document_signature(document: Mapping[str, Any], key: bytes) -> str:
    """Verify a document's signature block against a key.

    Returns VALID / INVALID / MISSING / MALFORMED / WRONG_KEY. Never raises
    for a bad signature: callers decide whether a non-VALID outcome is a
    hard failure (parent verification) or a reported status (post-hoc check).
    """
    key = _require_key(key)
    if not isinstance(document, Mapping):
        return "MALFORMED"
    signature = document.get(_SIGNATURE_FIELD)
    if signature is None:
        return "MISSING"
    if not isinstance(signature, dict):
        return "MALFORMED"
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        return "MALFORMED"
    fingerprint = signature.get("key_fingerprint")
    value = signature.get("value")
    if not isinstance(fingerprint, str) or not _HEX64.fullmatch(fingerprint):
        return "MALFORMED"
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        return "MALFORMED"
    if fingerprint != hashlib.sha256(key).hexdigest():
        return "WRONG_KEY"
    expected = sign_document(document, key)["value"]
    return "VALID" if hmac.compare_digest(expected, value) else "INVALID"


def key_file_name(run_id: str) -> str:
    """Sibling key-file name for a run, e.g. ``run_....hmac-key``."""
    return f"{run_id}.hmac-key"


def write_key_file(path: str | os.PathLike[str], run_id: str, key: bytes) -> Path:
    """Write a run key file (O_EXCL, mode 0600) outside the evidence dir.

    The file is JSON so a later verifier can check the run_id and algorithm
    before using the key. Call only after the test process has exited: the
    key must never exist on disk while the test process runs.
    """
    key = _require_key(key)
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id must be a valid run identifier")
    target = Path(path)
    payload = (json.dumps({
        "schema_version": KEY_SCHEMA_VERSION,
        "run_id": run_id,
        "algorithm": SIGNATURE_ALGORITHM,
        "key_hex": key.hex(),
    }, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(target, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while creating run key file")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def read_key_file(path: str | os.PathLike[str]) -> tuple[str, bytes]:
    """Read and validate a run key file; returns ``(run_id, key)``."""
    raw = Path(path).read_bytes()
    if len(raw) > 4096:
        raise ValueError("run key file exceeds size limit (4096 bytes)")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid run key file: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != KEY_SCHEMA_VERSION:
        raise ValueError("unsupported run key schema_version")
    run_id = doc.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run key file has an invalid run_id")
    if doc.get("algorithm") != SIGNATURE_ALGORITHM:
        raise ValueError("unsupported run key algorithm")
    key_hex = doc.get("key_hex")
    if not isinstance(key_hex, str):
        raise ValueError("run key file key_hex must be a hex string")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise ValueError("run key file key_hex is not valid hex") from exc
    if len(key) != KEY_BYTES:
        raise ValueError(f"run key must be {KEY_BYTES} bytes")
    return run_id, key


def verify_evidence_directory(
    evidence_dir: str | os.PathLike[str], key: bytes
) -> dict[str, Any]:
    """Post-hoc signature + artifact verification of a completed evidence dir.

    Verifies the evidence manifest signature and the execution provenance
    signature with the supplied key, and re-checks every manifest artifact.
    This is the independent re-check the key file exists for: it needs no
    access to the parent process that produced the evidence.
    """
    # Local import: verification_capture imports this module for
    # verify_manifest's key parameter, so a top-level import would cycle.
    from .verification_capture import verify_manifest

    key = _require_key(key)
    root = Path(evidence_dir)
    manifest_result = verify_manifest(root, hmac_key=key)
    provenance_status = "MISSING"
    provenance_path = root / "execution-provenance.json"
    try:
        provenance = json.loads(provenance_path.read_bytes().decode("utf-8"))
        provenance_status = verify_document_signature(provenance, key)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        provenance_status = "MALFORMED"
    return {
        "evidence_manifest_signature_status": manifest_result.get(
            "signature_status", "NOT_CHECKED"
        ),
        "provenance_signature_status": provenance_status,
        "manifest_valid": manifest_result["valid"],
        "manifest_artifacts_checked": manifest_result["artifacts_checked"],
        "manifest_errors": manifest_result["errors"],
    }
