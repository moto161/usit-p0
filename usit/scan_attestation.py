"""Detached Ed25519 attestations for exact USIT scan-manifest bytes.

This module authenticates a manifest digest only when the signing key is
independently present in the caller-supplied trust store. It does not establish
trusted time, scanner integrity, scan completeness, or claim truth.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ATTESTATION_SCHEMA = "usit-scan-attestation-v1"
ALGORITHM = "Ed25519"
MAX_MANIFEST_BYTES = 50 * 1024 * 1024
MAX_ATTESTATION_BYTES = 64 * 1024
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AttestationInputError(ValueError):
    """Raised when an attestation cannot safely be created from caller input."""


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _decode_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_ATTESTATION_BYTES:
        raise ValueError("attestation exceeds 64 KiB")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("attestation is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("attestation root must be a JSON object")
    return value


def _validate_relative_manifest_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AttestationInputError("manifest_path must be a non-empty relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or any(
        part in {"", ".", ".."} for part in value.split("/")
    ):
        raise AttestationInputError("manifest_path must not be absolute or contain traversal")
    return value


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AttestationInputError("created_at must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AttestationInputError("created_at must be a valid RFC3339 UTC timestamp") from exc
    if parsed.utcoffset() is None:
        raise AttestationInputError("created_at must include UTC timezone")
    return value


def _public_bytes(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_id(raw_public_key: bytes) -> str:
    if not isinstance(raw_public_key, bytes) or len(raw_public_key) != 32:
        raise AttestationInputError("Ed25519 raw public key must be exactly 32 bytes")
    return "sha256:" + hashlib.sha256(raw_public_key).hexdigest()


def create_scan_attestation(
    manifest_bytes: bytes,
    private_key_pem: bytes,
    *,
    snapshot_id: str,
    created_at: str,
    scanner_version: str,
    source_revision: str,
    manifest_path: str = "scan_manifest.json",
) -> bytes:
    """Create a detached signature; caller retains responsibility for key custody.

    The private key is accepted as in-memory PEM bytes and is never returned,
    persisted, logged, or copied into the attestation.
    """
    if not isinstance(manifest_bytes, bytes) or len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise AttestationInputError("manifest must be bytes no larger than 50 MiB")
    if not isinstance(private_key_pem, bytes) or not private_key_pem:
        raise AttestationInputError("private_key_pem must be non-empty PEM bytes")
    for name, value in (
        ("snapshot_id", snapshot_id),
        ("scanner_version", scanner_version),
        ("source_revision", source_revision),
    ):
        if not isinstance(value, str) or not value.strip():
            raise AttestationInputError(f"{name} must be a non-empty string")
    manifest_path = _validate_relative_manifest_path(manifest_path)
    created_at = _validate_timestamp(created_at)
    try:
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (ValueError, TypeError) as exc:
        raise AttestationInputError("private key must be an unencrypted PEM key") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise AttestationInputError("private key algorithm must be Ed25519")

    raw_public_key = _public_bytes(private_key.public_key())
    payload: dict[str, Any] = {
        "schema": ATTESTATION_SCHEMA,
        "algorithm": ALGORITHM,
        "key_id": public_key_id(raw_public_key),
        "public_key": base64.b64encode(raw_public_key).decode("ascii"),
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "scanner": {
            "name": "USIT",
            "version": scanner_version,
            "source_revision": source_revision,
        },
        "manifest": {
            "path": manifest_path,
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
    }
    signature = private_key.sign(_canonical_json(payload))
    attestation = {**payload, "signature": base64.b64encode(signature).decode("ascii")}
    return _canonical_json(attestation)


def verify_scan_attestation(
    manifest_bytes: bytes,
    attestation_bytes: bytes,
    trusted_public_keys: Mapping[str, bytes],
    *,
    expected_snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Verify detached signature and independently supplied trust anchor.

    trusted_public_keys maps key_id to 32-byte raw Ed25519 public key bytes.
    The public key embedded in the sidecar permits cryptographic verification,
    but never establishes trust by itself.
    """
    result: dict[str, Any] = {
        "schema": ATTESTATION_SCHEMA,
        "status": "ATTESTATION_INVALID_FORMAT",
        "key_id": None,
        "snapshot_id": None,
        "manifest_sha256": None,
        "signature_valid": False,
        "trusted_key": False,
        "freshness": "NOT_ASSESSED",
        "detail": "",
    }
    if not isinstance(manifest_bytes, bytes) or len(manifest_bytes) > MAX_MANIFEST_BYTES:
        result["detail"] = "Manifest must be bytes no larger than 50 MiB."
        return result
    try:
        att = _decode_json(attestation_bytes)
    except (ValueError, TypeError):
        result["detail"] = "Attestation is malformed, oversized, or contains duplicate JSON keys."
        return result

    required = {
        "schema", "algorithm", "key_id", "public_key", "snapshot_id",
        "created_at", "scanner", "manifest", "signature",
    }
    if set(att) != required:
        result["detail"] = "Attestation fields do not match the v1 schema."
        return result
    if att.get("schema") != ATTESTATION_SCHEMA:
        result["detail"] = "Unsupported attestation schema."
        return result
    if att.get("algorithm") != ALGORITHM:
        result.update(status="ATTESTATION_ALGORITHM_UNSUPPORTED",
                      detail="Only Ed25519 is supported.")
        return result

    try:
        key_id = att["key_id"]
        snapshot_id = att["snapshot_id"]
        created_at = _validate_timestamp(att["created_at"])
        scanner = att["scanner"]
        manifest = att["manifest"]
        if not isinstance(key_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", key_id):
            raise ValueError("invalid key_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("invalid snapshot_id")
        if not isinstance(scanner, dict) or set(scanner) != {"name", "version", "source_revision"}:
            raise ValueError("invalid scanner metadata")
        if any(not isinstance(scanner[x], str) or not scanner[x] for x in scanner):
            raise ValueError("invalid scanner metadata")
        if not isinstance(manifest, dict) or set(manifest) != {"path", "sha256"}:
            raise ValueError("invalid manifest metadata")
        _validate_relative_manifest_path(manifest["path"])
        if not isinstance(manifest["sha256"], str) or not _HEX_SHA256.fullmatch(manifest["sha256"]):
            raise ValueError("invalid manifest digest")
        embedded_public = base64.b64decode(att["public_key"], validate=True)
        signature = base64.b64decode(att["signature"], validate=True)
        if len(embedded_public) != 32 or len(signature) != 64:
            raise ValueError("invalid Ed25519 key or signature length")
        if public_key_id(embedded_public) != key_id:
            raise ValueError("public key fingerprint mismatch")
    except (KeyError, TypeError, ValueError, binascii.Error, AttestationInputError):
        result["detail"] = "Attestation metadata, key encoding, fingerprint, or signature encoding is invalid."
        return result

    result.update(key_id=key_id, snapshot_id=snapshot_id,
                  manifest_sha256=manifest["sha256"])
    actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_sha != manifest["sha256"]:
        result.update(status="ATTESTATION_MANIFEST_MISMATCH",
                      detail="Exact manifest bytes do not match the signed manifest SHA-256.")
        return result

    payload = {key: value for key, value in att.items() if key != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(embedded_public).verify(
            signature, _canonical_json(payload)
        )
    except (InvalidSignature, ValueError, TypeError):
        result.update(status="ATTESTATION_SIGNATURE_INVALID",
                      detail="Ed25519 signature does not verify for the signed payload.")
        return result
    result["signature_valid"] = True

    trusted = trusted_public_keys.get(key_id) if isinstance(trusted_public_keys, Mapping) else None
    if trusted is None:
        result.update(status="ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY",
                      detail="Signature is valid, but its public key is not in the independent trust store.")
        return result
    if not isinstance(trusted, bytes) or len(trusted) != 32 or trusted != embedded_public:
        result.update(status="ATTESTATION_POLICY_REJECTED",
                      detail="Trust-store key material does not match the attested public key.")
        return result

    if expected_snapshot_id is not None and snapshot_id != expected_snapshot_id:
        result.update(status="ATTESTATION_POLICY_REJECTED", trusted_key=True,
                      detail="Signature is valid under a trusted key, but snapshot_id violates caller policy.")
        return result
    result.update(status="ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY",
                  trusted_key=True,
                  detail="Exact manifest digest is signed by a key present in the independent trust store.")
    return result
