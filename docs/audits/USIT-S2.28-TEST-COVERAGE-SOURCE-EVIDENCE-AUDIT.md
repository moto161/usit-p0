# USIT S2.28 — Test / Coverage / Real Source Evidence-Chain Audit

- Audit target: `moto161/usit-p0`
- Audited PR: #2
- Audited HEAD: `1c678c1a7d62e697de53788e3da692254cdfca16`
- Audit mode: static repository/code review; no project code, tests, build, device, database, network target, or credentials were executed.
- PR state at audit: OPEN; not merged; `main` unchanged by this audit.

## 1. CI gate

The latest HEAD's pull-request workflow run `41573128044` completed successfully. All four jobs reported `success`:

- `windows-regression`
- `main-baseline-regression (ubuntu-latest)`
- `business-understanding`
- `main-baseline-regression (windows-latest)`

This clears the requested CI wait gate for this HEAD. It does not establish runtime correctness of any user project.

## 2. Test-result mapping chain

Reviewed implementation:

- `usit/test_result_importer.py`
- `usit/test_report_adapters.py`
- `usit/business_evidence_verifier.py`
- `tests/test_test_result_importer.py`

Confirmed controls:

1. Test-result mapping is explicit: exact `test_case_id → business_function_id`; no name similarity or endpoint inference.
2. Mapping JSON is bound to the exact business-graph file bytes through `source_graph_sha256`.
3. Unknown business-function IDs, duplicate mapping IDs, duplicate JUnit testcase IDs, malformed reports, DTD/entity declarations, and oversized reports are rejected.
4. A mapped testcase missing from the report produces `UNKNOWN`, not a false pass. A reported failure remains `FAILED`; skipped cases remain `UNKNOWN`.
5. The evidence verifier cross-checks testcase-level outcomes and summary counts to prevent a hand-edited PASS summary from overriding incomplete or failing testcase evidence.

Remaining evidence boundary:

- The current mapping proves which business function a report testcase was reviewed against, but it does not independently bind that testcase to the current bytes and line location of its test implementation.
- JUnit testcase identity alone is not a source-code locator. The current pytest JSON adapter consumes `nodeid` and `outcome`, but does not preserve pytest's optional source-location/line metadata as a separately verified source reference.
- Therefore test execution evidence and test-source provenance must remain distinct statuses.

Additional hardening observation:

- `test_result_importer._read_regular` rejects a symlink/reparse point at the final named path and uses a no-follow descriptor for the resolved file, but unlike the coverage importer's `_read`, it does not inspect every parent path component before resolution. A parent-directory symlink can therefore be traversed. This should be aligned with the stricter coverage input reader.

## 3. Coverage mapping chain

Reviewed implementation:

- `usit/coverage_result_importer.py`
- `usit/test_report_adapters.py` (`parse_coverage_py_json`, `parse_jacoco_xml`, `map_line_coverage_to_functions`)
- `usit/business_evidence_verifier.py`
- `tests/test_coverage_result_importer.py`
- `tests/test_test_report_adapters.py`

Confirmed controls:

1. Coverage reports, mapping JSON, and business graph are read as bounded regular files; the coverage reader checks every named path component for symlinks/reparse points and checks descriptor identity before/after reading.
2. Mapping is explicit and graph-hash-bound.
3. Each mapping requires an exact normalized relative `source_file`, a known `business_function_id`, and a positive `line_start..line_end` range.
4. Path traversal, duplicate identical mapping ranges, unknown function IDs, malformed coverage input, and XML DTD/entity declarations are rejected.
5. Missing coverage source files or absent executable-line data produce `UNKNOWN`, not zero coverage.
6. The evidence verifier cross-checks coverage percentages, line counts, status consistency, missing-source-file metadata, and mapping-range structure.

## 4. Critical gap: mapping declarations are not yet verified against real project source bytes

The coverage importer currently accepts only:

- coverage report path;
- reviewed mapping JSON;
- business graph path;
- report format.

It does not accept a project root or source snapshot/manifest. `map_line_coverage_to_functions` validates the mapping's relative path syntax and line-range shape, then intersects those declared ranges with line numbers found in the coverage report. It never opens the corresponding project source file.

Consequences:

- `source_file` is currently a report/mapping path label, not proof that the file exists in the target source tree.
- `line_end` is not checked against the actual source file's line count. The existing regression test intentionally accepts a range ending at `10**12`, demonstrating that this is a bounded report-set filter rather than a source-position verification.
- No per-source-file SHA-256 is checked, so the mapping cannot prove it refers to the same source bytes that were scanned or tested.
- A stale mapping can remain graph-hash-valid while the source file changes, because the business graph hash does not itself bind the mapping entry to current source bytes.
- The evidence verifier validates the shape of `mapping_ranges`, but cannot repair the missing source-file verification after import.

Accordingly, current coverage percentages may be described as **report lines intersecting reviewed path/range declarations**, but not yet as fully verified coverage of the current real source locations.

## 5. Required remediation before stronger evidence claims

Recommended next implementation slice:

1. Add an explicit `--project-root` input to coverage import; do not infer it from the current working directory.
2. Extend each reviewed coverage mapping entry with `source_file_sha256` (and, if useful for audit display, the reviewed source snapshot/manifest identity).
3. Resolve each mapping path beneath the explicit project root; reject absolute paths, traversal, symlink/reparse components, non-regular files, and paths escaping the root.
4. Read each source file with bounded no-follow I/O; verify SHA-256 against the mapping; decode under an explicit encoding policy; verify `line_end` does not exceed the actual source line count.
5. Emit per-file source-binding evidence and an explicit status such as `SOURCE_BYTES_MATCHED`, `SOURCE_FILE_MISSING`, `SOURCE_HASH_MISMATCH`, `SOURCE_LINE_RANGE_OUT_OF_BOUNDS`, or `SOURCE_BINDING_NOT_CHECKED`.
6. If no project root/source binding is supplied, retain a non-verified status; do not silently upgrade the current report/range intersection into real-source verification.
7. Extend `verify-evidence` to validate the new source-binding metadata and propagate conflicts to the affected business function.
8. Add adversarial tests for stale source bytes, nonexistent files, out-of-range line numbers, path traversal, parent symlinks/reparse points, duplicate paths, and a valid unchanged source file.
9. Align the test-result reader's parent-component symlink/reparse checks with the coverage reader. Separately extend pytest source-location preservation only where the report actually provides it; do not fabricate JUnit source locations.

## 6. Acceptance criteria

- A coverage mapping cannot claim current-source binding without the exact source file bytes matching the reviewed digest.
- A mapped line range cannot exceed the actual source file's line count.
- Missing, stale, or unsafe source files never produce a verified source-binding status.
- Graph, report, mapping, and source-file hashes remain independently visible in output.
- Existing conservative semantics remain: missing evidence is UNKNOWN/NOT_MEASURED, never an invented 0% or PASS.
- Linux and Windows CI both pass after implementation.
- PR #2 remains OPEN; no merge to `main` without a separate explicit authorization.

## 7. Audit conclusion

- Latest HEAD CI gate: PASS (4/4 jobs).
- Testcase-to-business-function mapping: structurally conservative and graph-hash-bound; test-source provenance remains incomplete.
- Coverage report-to-function mapping: explicit and graph-hash-bound; real-source byte/line binding is not implemented.
- Stronger claim "coverage evidence is bound to the current real source locations": **NOT ESTABLISHED**.
- Next action: implement source-byte and line-boundary verification, then add adversarial regression coverage and rerun Linux/Windows/Business Understanding CI.
