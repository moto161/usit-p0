"""Fix loop, first increment (R1): propose -> approve -> apply -> verify.

Why this module exists
----------------------
USIT's stated goal (AGENTS.md §1) ends with "propose fixes -> authorized
fix -> regression verification", but until R1 the codebase could only
*report* gaps (the verifier and the ``analyze-gaps`` backlog). This module
builds the machinery that closes a gap, in four stages:

1. Propose: from a gap backlog + business semantics, emit structured fix
   proposals. R1 has exactly one automatic fix kind (documenting an
   undocumented code endpoint); everything else is a NEEDS_HUMAN_DECISION
   proposal with no change payload, never auto-applicable.
2. Approve: a review manifest reusing the ring-1 review pattern
   (reviewed_by / reviewed_at / rationale + decision), bound to the exact
   proposals document by SHA-256. Unapproved proposals are never applied
   and never counted.
3. Apply: only into a work copy under the output directory, by exact
   anchor match (fail closed when the document moved on), with the
   original bytes snapshotted for rollback. The original project is
   never written.
4. Verify: re-run the S2.11 exact method/path reconciliation on the
   fixed work copy; the fix counts as VERIFIED only when the target row
   becomes EXACT_MATCH and no new mismatch appears. This is the targeted
   check for this fix, not a full re-scan or a full gate re-run.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .safety import canonical_output, canonical_project, overlaps

DOC_ENDPOINT_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/api/[\w/:{}_.-]+)")
DOC_PATH_RE = re.compile(r"^/api/[\w/:{}_.-]+$")
MAX_DOC_BYTES = 1024 * 1024

# Gap kinds the R1 proposer understands. Other kinds are recorded in
# skipped_kinds and get no proposal in this increment.
_API_CONTRACT_KINDS = {
    "UNDOCUMENTED_CODE_ENDPOINT",
    "DOCUMENTED_ENDPOINT_UNMATCHED",
    "AMBIGUOUS_ENDPOINT_CONTRACT",
}


def _canonical_json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _proposal_id(gap_id: str, change: dict[str, Any] | None) -> str:
    return hashlib.sha256(_canonical_json({"gap_id": gap_id, "change": change})).hexdigest()


def _doc_files_with_endpoints(semantics: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for row in semantics.get("endpoint_contract_alignment", []) or []:
        if not isinstance(row, dict):
            continue
        doc = row.get("document_evidence")
        if isinstance(doc, dict) and isinstance(doc.get("source"), str) and doc["source"]:
            if doc["source"] not in files:
                files.append(doc["source"])
    return sorted(files)


def _last_endpoint_line(text: str) -> tuple[int, str] | None:
    """(1-based line number, exact line text) of the last documented endpoint, or None."""
    found: tuple[int, str] | None = None
    for number, line in enumerate(text.split("\n"), start=1):
        if DOC_ENDPOINT_RE.search(line):
            found = (number, line)
    return found


def propose_fixes(
    gap_backlog: dict[str, Any], semantics: dict[str, Any], project_root: str | Path
) -> dict[str, Any]:
    """Generate fix proposals from a gap backlog (read-only).

    Only the API-contract family is considered in R1. The single automatic
    kind is UNDOCUMENTED_CODE_ENDPOINT with a /api/ path and exactly one
    document holding documented endpoints; everything else that is
    understood becomes NEEDS_HUMAN_DECISION with no change payload.
    """
    project = canonical_project(project_root)
    doc_files = _doc_files_with_endpoints(semantics)
    proposals: list[dict[str, Any]] = []
    skipped_kinds: set[str] = set()
    for gap in gap_backlog.get("gaps", []) or []:
        if not isinstance(gap, dict):
            continue
        kind = gap.get("gap_kind")
        if not isinstance(kind, str) or kind not in _API_CONTRACT_KINDS:
            if isinstance(kind, str):
                skipped_kinds.add(kind)
            continue
        gap_id = gap.get("gap_id")
        target = gap.get("target")
        if not isinstance(gap_id, str) or not isinstance(target, str):
            continue
        evidence = gap.get("evidence") if isinstance(gap.get("evidence"), list) else []
        rationale = {
            "reason": gap.get("reason"),
            "action": gap.get("action"),
            "evidence": [e for e in evidence if isinstance(e, dict)],
        }
        change: dict[str, Any] | None = None
        status = "NEEDS_HUMAN_DECISION"
        expected = "Human decision required; no automatic change is proposed."
        method = path = ""
        if kind == "UNDOCUMENTED_CODE_ENDPOINT":
            parts = target.split(" ", 1)
            if len(parts) == 2:
                method, path = parts[0].upper(), parts[1]
            if method and DOC_PATH_RE.match(path) and len(doc_files) == 1:
                doc_rel = doc_files[0]
                doc_path = project / doc_rel
                try:
                    if doc_path.stat().st_size > MAX_DOC_BYTES:
                        raise OSError("document too large")
                    text = doc_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    text = ""
                anchor = _last_endpoint_line(text) if text else None
                if anchor is not None:
                    anchor_line, anchor_text = anchor
                    change = {
                        "kind": "insert_after_line",
                        "file": doc_rel,
                        "anchor_line": anchor_line,
                        "anchor_text": anchor_text,
                        "new_text": f"{method} {path}",
                    }
                    status = "PROPOSED"
                    expected = (
                        f"Alignment row {method} {path} becomes EXACT_MATCH "
                        "with no new mismatch."
                    )
        proposals.append({
            "proposal_id": _proposal_id(gap_id, change),
            "gap_id": gap_id,
            "gap_kind": kind,
            "target": target,
            "method": method,
            "path": path,
            "change": change,
            "status": status,
            "rationale": rationale,
            "expected_verification": expected,
        })
    proposals.sort(key=lambda p: (p["gap_kind"], p["target"], p["proposal_id"]))
    return {
        "schema_version": 1,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "skipped_kinds": sorted(skipped_kinds),
        "limitations": [
            "R1 proposes automatic changes only for undocumented code endpoints with a /api/ path and a single API document; all other cases need a human decision.",
            "A proposal never modifies the original project; application happens only in a work copy under the output directory.",
            "Regression verification re-runs only the S2.11 reconciliation for the fixed row; it is not a full re-scan or gate re-run.",
        ],
    }


def review_fix_proposals(
    proposals: dict[str, Any], decisions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build an approval manifest bound to the exact proposals document.

    decisions: [{proposal_id, decision: APPROVED|REJECTED, reviewed_by,
    reviewed_at, rationale}]. Every decision is validated; an unknown
    proposal, a missing field, or a duplicate decision fails closed.
    """
    known = {p["proposal_id"] for p in proposals.get("proposals", []) or [] if isinstance(p, dict)}
    reviews: list[dict[str, Any]] = []
    seen: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("each review decision must be an object")
        proposal_id = decision.get("proposal_id")
        verdict = decision.get("decision")
        reviewed_by = decision.get("reviewed_by")
        reviewed_at = decision.get("reviewed_at")
        rationale = decision.get("rationale")
        if not isinstance(proposal_id, str) or proposal_id not in known:
            raise ValueError(f"review names an unknown proposal: {proposal_id!r}")
        if proposal_id in seen:
            raise ValueError(f"duplicate review for proposal: {proposal_id!r}")
        if verdict not in {"APPROVED", "REJECTED"}:
            raise ValueError(f"review decision must be APPROVED or REJECTED, got {verdict!r}")
        for field, value in (("reviewed_by", reviewed_by), ("reviewed_at", reviewed_at), ("rationale", rationale)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"review field {field} must be a non-empty string")
        seen.add(proposal_id)
        reviews.append({
            "proposal_id": proposal_id,
            "decision": verdict,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
            "rationale": rationale,
        })
    reviews.sort(key=lambda r: r["proposal_id"])
    return {
        "schema_version": 1,
        "proposals_sha256": hashlib.sha256(_canonical_json(proposals)).hexdigest(),
        "reviews": reviews,
    }


def apply_fix(
    proposals: dict[str, Any],
    manifest: dict[str, Any],
    proposal_id: str,
    project_root: str | Path,
    work_dir: str | Path,
) -> dict[str, Any]:
    """Apply one APPROVED proposal into a work copy (never the project).

    Fails closed when the manifest does not match the proposals document,
    the proposal is not APPROVED, the change is absent, the anchor line
    moved, or any path would escape its root. Returns the applied record
    (work-copy path + snapshot) for rollback and verification.
    """
    if manifest.get("proposals_sha256") != hashlib.sha256(_canonical_json(proposals)).hexdigest():
        raise ValueError("approval manifest does not match the proposals document")
    proposal = next(
        (p for p in proposals.get("proposals", []) or [] if isinstance(p, dict) and p.get("proposal_id") == proposal_id),
        None,
    )
    if proposal is None:
        raise ValueError(f"unknown proposal: {proposal_id!r}")
    review = next(
        (r for r in manifest.get("reviews", []) or [] if isinstance(r, dict) and r.get("proposal_id") == proposal_id),
        None,
    )
    if review is None or review.get("decision") != "APPROVED":
        raise ValueError("proposal is not APPROVED and must not be applied")
    change = proposal.get("change")
    if not isinstance(change, dict) or change.get("kind") != "insert_after_line":
        raise ValueError("proposal carries no applicable change")
    project = canonical_project(project_root)
    out = canonical_output(work_dir)
    if overlaps(out, project) or overlaps(project, out):
        raise ValueError("work copy must live outside the project root")
    rel = change["file"]
    if not isinstance(rel, str) or not rel:
        raise ValueError("change file must be a non-empty relative path")
    source = (project / rel).resolve()
    if not overlaps(project, source) or source == project:
        raise ValueError("change file must stay inside the project root")
    try:
        original = source.read_bytes()
        raw = original.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read change target: {exc}") from exc
    # Line endings: the proposal's anchor text comes from a universal-newline
    # read (propose_fixes), but the bytes decoded here keep CRLF on Windows
    # (write_text translates \n to os.linesep). Normalize for the anchor
    # comparison and write back in the file's own line-ending style, so a
    # CRLF document applies and verifies on every platform.
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    anchor_line = change.get("anchor_line")
    anchor_text = change.get("anchor_text")
    new_text = change.get("new_text")
    if (
        not isinstance(anchor_line, int)
        or not 1 <= anchor_line <= len(lines)
        or not isinstance(anchor_text, str)
        or lines[anchor_line - 1] != anchor_text
        or not isinstance(new_text, str)
        or not new_text
    ):
        raise ValueError("anchor line does not match the current document; refusing to apply")
    lines.insert(anchor_line, new_text)
    fixed_text = newline.join(lines)
    work_file = (out / rel).resolve()
    if not overlaps(out, work_file):
        raise ValueError("work-copy path must stay inside the output directory")
    work_file.parent.mkdir(parents=True, exist_ok=True)
    work_file.write_bytes(fixed_text.encode("utf-8"))
    snapshot_dir = out / ".usit-fix-snapshots" / proposal_id
    snapshot_file = (snapshot_dir / rel).resolve()
    if not overlaps(out, snapshot_file):
        raise ValueError("snapshot path must stay inside the output directory")
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_file.write_bytes(original)
    return {
        "applied": True,
        "proposal_id": proposal_id,
        "gap_id": proposal.get("gap_id"),
        "work_copy": str(work_file),
        "file": rel,
        "snapshot_path": str(snapshot_file),
        "snapshot_sha256": hashlib.sha256(original).hexdigest(),
    }


def rollback_fix(applied: dict[str, Any], work_dir: str | Path) -> dict[str, Any]:
    """Restore a work-copy file from its snapshot bytes. Verifies the hash."""
    out = canonical_output(work_dir)
    work_file = Path(str(applied["work_copy"])).resolve()
    snapshot_file = Path(str(applied["snapshot_path"])).resolve()
    if not overlaps(out, work_file) or not overlaps(out, snapshot_file):
        raise ValueError("rollback paths must stay inside the output directory")
    original = snapshot_file.read_bytes()
    if hashlib.sha256(original).hexdigest() != applied["snapshot_sha256"]:
        raise ValueError("snapshot bytes do not match the recorded hash; refusing rollback")
    work_file.write_bytes(original)
    return {"rolled_back": True, "proposal_id": applied["proposal_id"], "work_copy": str(work_file)}


def _recompute_alignment(
    doc_endpoints: set[tuple[str, str]], code_endpoints: list[tuple[str, str]]
) -> list[dict[str, str]]:
    """S2.11 exact method/path reconciliation over endpoint sets."""
    rows: list[dict[str, str]] = []
    matched: set[tuple[str, str]] = set()
    for method, path in sorted(doc_endpoints):
        count = sum(1 for ep in code_endpoints if ep == (method, path))
        if count == 1:
            status = "EXACT_MATCH"
        elif count > 1:
            status = "MULTIPLE_CODE_MATCHES"
        else:
            status = "DOCUMENTED_NOT_FOUND_IN_CODE"
        rows.append({"method": method, "path": path, "status": status})
        if count:
            matched.add((method, path))
    for method, path in sorted({ep for ep in code_endpoints if ep not in matched}):
        rows.append({"method": method, "path": path, "status": "CODE_ENDPOINT_NOT_DOCUMENTED"})
    rows.sort(key=lambda r: (r["method"], r["path"], r["status"]))
    return rows


def verify_fix(
    proposal: dict[str, Any], work_dir: str | Path, semantics: dict[str, Any]
) -> dict[str, Any]:
    """Re-run the S2.11 reconciliation on the fixed work copy.

    VERIFIED only when the proposal's row becomes EXACT_MATCH and no new
    non-EXACT_MATCH row appears. This is the targeted check for this fix,
    not a full re-scan.
    """
    change = proposal.get("change")
    if not isinstance(change, dict) or change.get("kind") != "insert_after_line":
        raise ValueError("proposal carries no applicable change to verify")
    out = canonical_output(work_dir)
    rel = change["file"]
    work_file = (out / rel).resolve()
    if not overlaps(out, work_file):
        raise ValueError("work-copy path must stay inside the output directory")
    fixed_text = work_file.read_text(encoding="utf-8")
    fixed_doc_endpoints = {
        (m.group(1).upper(), m.group(2)) for m in DOC_ENDPOINT_RE.finditer(fixed_text)
    }
    baseline_rows = [
        row for row in semantics.get("endpoint_contract_alignment", []) or [] if isinstance(row, dict)
    ]
    baseline_doc: set[tuple[str, str]] = set()
    code_endpoints: list[tuple[str, str]] = []
    for row in baseline_rows:
        doc = row.get("document_evidence")
        if isinstance(doc, dict) and isinstance(doc.get("source"), str) and doc["source"]:
            if isinstance(row.get("method"), str) and isinstance(row.get("path"), str):
                baseline_doc.add((row["method"].upper(), row["path"]))
        for item in row.get("code_evidence", []) or []:
            if isinstance(item, dict) and isinstance(item.get("method"), str) and isinstance(item.get("path"), str):
                code_endpoints.append((item["method"].upper(), item["path"]))
    old_target_doc = {
        (method, path)
        for (method, path) in baseline_doc
        if any(
            isinstance(row.get("document_evidence"), dict)
            and row["document_evidence"].get("source") == rel
            and str(row.get("method") or "").upper() == method
            and row.get("path") == path
            for row in baseline_rows
        )
    }
    new_doc = (baseline_doc - old_target_doc) | fixed_doc_endpoints
    recomputed = _recompute_alignment(new_doc, code_endpoints)
    method = str(proposal.get("method") or "").upper()
    path = str(proposal.get("path") or "")
    gap_closed = any(
        row["method"] == method and row["path"] == path and row["status"] == "EXACT_MATCH"
        for row in recomputed
    )
    baseline_non_exact = {
        (row.get("method"), row.get("path"), row.get("status"))
        for row in baseline_rows
        if row.get("status") != "EXACT_MATCH"
    }
    recomputed_non_exact = {
        (row["method"], row["path"], row["status"])
        for row in recomputed
        if row["status"] != "EXACT_MATCH"
    }
    new_mismatches = sorted(
        {"method": m, "path": p, "status": s} for (m, p, s) in (recomputed_non_exact - baseline_non_exact)
    )
    verified = gap_closed and not new_mismatches
    return {
        "status": "VERIFIED" if verified else "NOT_VERIFIED",
        "gap_closed": gap_closed,
        "new_mismatches": new_mismatches,
        "method": method,
        "path": path,
    }
