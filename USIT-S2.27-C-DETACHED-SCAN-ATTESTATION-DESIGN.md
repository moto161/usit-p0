# USIT S2.27-C — Detached scan attestation and provenance trust model

- Status: DESIGN / implementation not yet enabled
- Target branch: `feature/understanding-to-test-v1`
- Baseline: S2.27-B (`usit-manifest-v1` byte and source-entry cross-binding)
- Security posture: read-only verification; fail closed; no automatic claim promotion

## 1. Problem statement

S2.27-B proves that (a) the manifest bytes match a digest supplied by the evidence ledger and (b) a source artifact matches the unique path/hash entry in that manifest. It does **not** prove who created the manifest. An actor able to replace both the manifest and the ledger declaration can create a self-consistent replacement.

S2.27-C therefore adds a separate provenance layer. It must not reinterpret a successful S2.27-B digest comparison as source authentication.

## 2. Repository facts established by code inspection

- `pyproject.toml` declares `dependencies = []`; there is no currently declared Python cryptographic dependency.
- Scanner output currently creates `usit-manifest-v1` with `schema_version`, absolute `project_root`, `files`, `summary`, `errors`, and `incremental`. The manifest is written as a regular scan artifact.
- Each file row includes `path`, `language`, `category`, `size_bytes`, `sha256`, `readable_text`, and analysis records.
- The inspected manifest construction does not establish a cryptographically protected signer identity or trusted timestamp.
- The evidence verifier currently accepts the manifest digest from the caller's evidence catalog; this is integrity cross-binding, not an external trust anchor.

These are source-code observations, not a claim that a production scan was executed during this design work.

## 3. Required architecture

### 3.1 Separate three assertions

1. **Artifact integrity** — exact bytes match a declared SHA-256.
2. **Snapshot consistency** — source artifact matches exactly one path/hash entry in the referenced USIT manifest.
3. **Snapshot provenance** — a detached signature over the manifest identity and scan metadata verifies under a public key that the verifier independently trusts.

The first two must remain usable without a signature, but the result must remain explicitly unauthenticated. Only the third may produce a trusted-key provenance status.

### 3.2 Detached attestation

Proposed sidecar: `scan_attestation.json`. It is separate from `scan_manifest.json`; neither file embeds or self-asserts trust in the other.

Version-1 logical payload (illustrative contract; exact serialization is to be frozen with implementation tests):

```json
{
  "schema": "usit-scan-attestation-v1",
  "algorithm": "Ed25519",
  "key_id": "sha256:<lowercase-public-key-digest>",
  "snapshot_id": "<random scan identifier>",
  "created_at": "<UTC RFC3339 timestamp>",
  "scanner": {
    "name": "USIT",
    "version": "<reported version>",
    "source_revision": "<commit or UNKNOWN>"
  },
  "manifest": {
    "path": "scan_manifest.json",
    "sha256": "<digest of exact manifest bytes>"
  },
  "signature": "<base64 Ed25519 signature over the defined canonical payload>"
}
```

The signed bytes must be specified unambiguously: UTF-8 canonical JSON of every field except `signature`, with deterministic key ordering, separators, Unicode handling, and no trailing newline. The implementation must sign/verify the exact same byte representation and include test vectors. No verifier may silently normalize or reserialize arbitrary input differently.

The attestation must not include secrets. Avoid publishing an absolute local project path by default; if a project identity is needed, use an optional user-supplied opaque identifier. The manifest digest binds the exact manifest bytes, which in turn binds the manifest's project-root field and file inventory.

### 3.3 Trust anchor

- The signing private key is generated and held outside the scanned project and evidence directory.
- The verifier receives an independently provisioned trusted public key or trust-store entry. A public key shipped only beside the evidence is **not** trusted merely because its fingerprint matches the attestation.
- `key_id` is the SHA-256 fingerprint of the raw Ed25519 public key; it is an identifier, not proof of trust.
- Trust-store changes require an explicit administrative/user action and audit trail. No automatic trust-on-first-use.
- Private key protection is platform-specific and must be delegated to OS facilities or a maintained cryptographic library; the scanner must never print, export, or copy private key material into artifacts.
- Do not implement cryptography from scratch. Since current dependencies are empty, implementation must select and pin a maintained Ed25519 provider (or a separately installed, explicitly detected provider) before coding. No silent fallback to HMAC, unsigned mode, or an alternate algorithm.

### 3.4 Verification states

Attestation verification is independent of S2.27-B snapshot binding. Proposed states:

- `ATTESTATION_NOT_PROVIDED`
- `ATTESTATION_MISSING`
- `ATTESTATION_INVALID_FORMAT`
- `ATTESTATION_MANIFEST_MISMATCH`
- `ATTESTATION_SIGNATURE_INVALID`
- `ATTESTATION_KEY_UNKNOWN`
- `ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY`
- `ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY`
- `ATTESTATION_ALGORITHM_UNSUPPORTED`
- `ATTESTATION_POLICY_REJECTED`

Only `ATTESTATION_SIGNATURE_VALID_TRUSTED_KEY` establishes that the exact manifest digest was signed by the holder of the corresponding trusted private key. It still does not prove that the scanner binary/source was uncompromised, that the scan captured every relevant file, that the clock was correct, or that source semantics are true.

A valid signature under an untrusted key is not an authenticated provenance result. Unknown/missing/invalid states must never degrade to success.

### 3.5 Freshness, replay, and revocation

- `created_at` is signer-supplied metadata, not a trusted timestamp.
- A signature alone does not prove recency or prevent replay. Freshness policy must be caller-supplied (e.g. expected snapshot ID, maximum age under a trusted clock, or an externally recorded accepted snapshot).
- Key revocation/rotation is an external trust-store policy. A key that verifies cryptographically may still be rejected by policy.
- Do not compare local wall-clock time and claim trusted time.

## 4. Evidence verifier integration contract

S2.27-C must add provenance output without changing existing artifact-binding meanings:

- Preserve `binding_status` and `snapshot_binding_status` as-is.
- Add a separate `attestation_status` and structured `attestation` result for each source snapshot.
- Add claim-level `snapshot_provenance_status`; it must not overwrite `truth_status`.
- `truth_status` remains `NOT_ESTABLISHED` for every AI claim. A trusted signature authenticates a manifest digest, not the semantic correctness of an AI claim.
- If several source references point to different manifests, report each independently; do not collapse conflicting snapshot identities into one successful aggregate.
- Missing signature must remain compatible with existing unsigned evidence and be reported as unauthenticated, not as a hard error unless an explicit policy requires signatures.

## 5. Security and failure requirements

- Reject absolute paths, traversal, symlink/reparse-point escapes, oversized files, malformed UTF-8/JSON, duplicate/ambiguous manifest references, malformed digests, unsupported algorithms, and malformed base64.
- Compare the attested manifest digest against the exact bytes read by the verifier, not a parsed-and-reserialized manifest.
- Bind algorithm, key ID, snapshot ID, timestamp, scanner identity/revision, and manifest digest inside the signed payload to prevent field substitution.
- Reject duplicate JSON keys (or define and test a deterministic rejection rule).
- Never fetch keys from URLs or make network requests during verification.
- No project code execution, builds, tests, logins, or writes to the scanned project.
- Attestation generation is an explicit operation; do not silently sign every scan with an implicit machine key.

## 6. Test contract (synthetic fixtures only)

Required tests before implementation can be marked complete:

1. trusted public key + unchanged manifest → trusted signature valid;
2. manifest byte changed → manifest mismatch;
3. signature byte changed/truncated → signature invalid or invalid format;
4. trusted key replaced by another key → key unknown/untrusted, never trusted;
5. valid signature from untrusted key → valid-untrusted state;
6. signed metadata field altered (snapshot ID, timestamp, scanner revision, key ID) → signature invalid;
7. unsupported algorithm → explicit unsupported;
8. duplicate JSON keys / malformed JSON / invalid base64 → rejected;
9. traversal/symlink/oversized sidecar and manifest → rejected safely;
10. missing sidecar with unsigned legacy ledger → existing S2.27-A/B results unchanged, provenance not authenticated;
11. two source entries bound to different manifests → per-entry statuses preserved; aggregate requires review;
12. replay/freshness policy absent → no claim of freshness; policy rejection test when an expected snapshot ID or age bound is supplied;
13. private key never appears in report, stdout, generated artifacts, or test logs.

CI must run the focused tests on Linux and Windows where applicable, plus the existing business-understanding suite and self-test. All key material must be generated ephemerally inside tests; no real user key or secret enters Git history or CI logs.

## 7. Explicit non-goals

- No self-signed key automatically becoming trusted.
- No claim of trusted timestamp, complete capture, tamper-proof machine, or semantic truth.
- No automatic `VERIFIED_FACT` promotion.
- No private-key export, cloud upload, external key service, or network verification.
- No change to the scanner's read-only project boundary.
- No new runtime dependency until the cryptographic provider and packaging strategy are reviewed and pinned.

## 8. Implementation gate / acceptance

Implementation may start only after the repository selects a maintained Ed25519 provider and specifies key enrollment/storage UX. The implementation PR must include the canonical serialization test vectors, trust-store format and lifecycle, CLI contract, negative tests above, and a security review of path/key handling.

Until that gate is met, S2.27-C is **DESIGN COMPLETE / IMPLEMENTATION NOT STARTED**. S2.27-B remains the strongest available check and must continue to be labelled integrity cross-binding, not authenticated provenance.
