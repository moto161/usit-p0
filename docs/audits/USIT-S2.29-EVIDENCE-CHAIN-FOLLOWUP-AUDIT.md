# USIT S2.29 — Evidence-chain follow-up audit (post source-binding implementation)

- Repository: `moto161/usit-p0`
- PR: #2 (must remain OPEN; do not merge)
- Audited HEAD: `74cdf17df1d97005e8e667eba7ffb687c7d61d71`
- Base: `main` at `02719397c8a9af2673b39f39d4295d861cd21264`
- Mode: static source review plus inspection of GitHub Actions job results/logs. No user project, device, database, external target, or credentials were accessed.
- This report supersedes the implementation-state conclusions in S2.28 where explicitly noted below.

## 1. Latest required CI gate

Pull-request workflow run: [41602238881](https://github.com/moto161/usit-p0/actions/runs/41602238881)

All four required jobs completed successfully on the audited HEAD:

| Job | Result |
|---|---|
| windows-regression | PASS |
| business-understanding | PASS |
| main-baseline-regression (ubuntu-latest) | PASS as a workflow job |
| main-baseline-regression (windows-latest) | PASS as a workflow job |

Important qualification: the two main-baseline jobs run a diagnostic full-repository suite as a non-gating step. Their job-level success is not a claim that those diagnostic suites passed. The inspected diagnostic logs report:
- Ubuntu: `FAILED (failures=4, errors=4, skipped=16)`.
- Windows: `FAILED (failures=3, errors=3, skipped=3)`.
- Observed causes include missing historical `outputs/p0710...` evidence artifacts, frozen-test byte-hash mismatches, Linux case-sensitive path assumptions (for example `/TMP`), and a same-size/same-mtime rewrite expectation failure.
These are diagnostic baseline failures, not failures of the four required workflow gates. They remain visible technical debt and must not be reported as full-suite PASS.

The required Windows regression and Business Understanding jobs completed successfully. Their logs show the USIT self-test result `PASS (6/6)`; the Windows job also completed its trust-boundary tests successfully.

## 2. S2.28 implementation gaps now closed

S2.28 correctly identified that coverage mapping was graph-hash-bound but not bound to current source bytes. On audited HEAD this implementation gap has been addressed:

- `ingest_coverage_results(..., project_root=...)` accepts an explicit source root; it does not infer one.
- `_verify_source_bindings` checks the project root and every source path component for symlink/reparse points, reads bounded source bytes through the no-follow reader, computes SHA-256, compares the reviewed digest, decodes UTF-8 (with BOM support), and checks the mapped line-end boundary.
- Source binding emits per-mapping statuses including `SOURCE_BYTES_MATCHED`, `SOURCE_FILE_MISSING`, `SOURCE_HASH_MISMATCH`, `SOURCE_ENCODING_UNSUPPORTED`, and `SOURCE_LINE_RANGE_OUT_OF_BOUNDS`.
- If the project root is absent, binding evidence is absent, or any mapping for a function is not byte-matched, coverage is downgraded to `UNKNOWN` with no percentage.
- The evidence verifier independently validates source hashes, line counts, line ranges, and equality between mapping ranges and source-binding ranges. Invalid/unverified source evidence cannot preserve a measured coverage status.

The JUnit result reader was also aligned with the coverage reader to inspect all named path components before resolving/opening. Project-root and source-path symlink/reparse checks are present.

## 3. Additional path hardening added after S2.28

The audited branch now rejects colon-containing source paths in coverage report adapters, coverage source mappings, and test-source mappings. This closes Windows drive-relative and NTFS Alternate Data Stream path forms such as `C:outside.py` and `src/file.py:stream`.

Regression coverage was added for:
- parent-directory symlink traversal in test-report input;
- symlink project roots in test-source binding;
- Windows ADS-style coverage mapping paths;
- Windows ADS-style test-source mapping paths.

The fixture newline escaping was corrected before the final audited HEAD. These changes are included in the passing required CI run.

## 4. Remaining evidence-chain limitations

### 4.1 Testcase identity is not yet a proven test-symbol binding

The importer can bind a reviewed mapping entry to an exact test-source file digest and a valid line number. The evidence verifier checks digest equality, declared-versus-returned file/line consistency, and line bounds.

That proves the referenced file bytes and line exist. It does **not** prove:
- the declared line is the start of the named test function/method;
- the JUnit testcase identity actually originated from that source symbol;
- the executed test process loaded those exact source bytes;
- the test exercises the intended business behavior rather than merely existing or passing trivially.

JUnit XML generally does not carry a portable source symbol locator. The pytest adapter currently normalizes testcase `nodeid` and outcome; a separate source-location field must only be populated when the report or an independently captured execution artifact actually provides it.

Required status semantics: retain test execution result and source provenance as separate evidence dimensions. Do not promote `TEST_SOURCE_BYTES_MATCHED` into “test implementation verified” or “this exact source symbol executed”.

### 4.2 Coverage source binding is byte-and-range provenance, not semantic correctness

The coverage importer proves that a reviewed path/range points to current source bytes and is within the file's line count. It does not prove that:
- the reviewed range corresponds semantically to the named business function;
- the coverage report was generated from the same source snapshot as the supplied project root;
- the test suite exercised the intended business scenario;
- line coverage establishes branch, requirement, or behavioral correctness.

The report, mapping, graph, and source hashes are independently emitted. However, the caller remains responsible for ensuring the report-generation source snapshot is the same snapshot represented by the explicit project root and mapping. Without a trustworthy execution-time source snapshot identity, this cross-artifact relation remains unproven.

### 4.3 Diagnostic baseline suite remains red but non-gating

The main-baseline diagnostic suites have multiple failures/errors as recorded in section 1. Do not silently reclassify these as PASS because the workflow jobs are configured non-gating. A separate baseline cleanup/audit is required before claiming full repository regression success.

## 5. Acceptance criteria for the next slice

1. Add a versioned execution provenance record that binds report generation to a source snapshot/manifest digest, without guessing from the current checkout.
2. For supported frameworks, preserve report-provided source locations and verify the referenced symbol using the appropriate language parser; unsupported formats/languages remain `NOT_VERIFIED`.
3. Keep execution outcome, source-byte provenance, source-symbol provenance, and business-function mapping as separate fields/statuses.
4. Add adversarial tests: a valid digest pointing at the wrong line; a valid line pointing at a different test symbol; testcase IDs mismatched to declared source symbols; source mutation between test execution and import; report/source snapshot mismatch; and unsupported language/report metadata.
5. Define and test a trustworthy source snapshot handoff from test execution to report import. A digest supplied only by the mapping author is not independent execution evidence.
6. Audit the non-gating main-baseline failures separately; do not broaden exclusions or rewrite frozen hashes merely to obtain a green result.
7. Re-run Linux, Windows, Business Understanding, and self-test gates after changes.
8. Keep PR #2 OPEN and do not merge main without separate explicit authorization.

## 6. Conclusion

- Required PR CI gates on HEAD `74cdf17`: **PASS (4/4 jobs)**.
- Diagnostic full-repository baseline suites: **FAIL** on both Ubuntu and Windows, but configured non-gating; details retained above.
- Coverage mapping to current source bytes and bounded line ranges: **IMPLEMENTED**, subject to the source-snapshot provenance limitation.
- Testcase mapping to exact source bytes/line: **IMPLEMENTED as locator provenance only**; exact test-symbol identity and execution-time source binding are **NOT ESTABLISHED**.
- Business-function semantic correctness and adequacy of test coverage: **NOT ESTABLISHED by these artifacts alone**.
- PR #2 remains OPEN; `main` remains unchanged by this work.
