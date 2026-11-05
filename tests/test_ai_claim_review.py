from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from usit.ai_claim_review import (
    LEDGER_SCHEMA, REVIEW_SCHEMA, load_claim_ledger, review_claim_ledger,
    review_claim_ledger_file, validate_claim_ledger,
)


class AIClaimReviewTests(unittest.TestCase):
    def setUp(self):
        self.ledger = {
            "schema": LEDGER_SCHEMA,
            "evidence_catalog": [
                {
                    "evidence_id": "src-1",
                    "evidence_type": "SOURCE_CODE",
                    "source_sha256": "a" * 64,
                    "locator": "src/photo.py:10",
                },
                {
                    "evidence_id": "test-1",
                    "evidence_type": "TEST_RESULT",
                    "source_sha256": "b" * 64,
                    "locator": "test-results.xml#case-1",
                },
            ],
            "claims": [
                {
                    "claim_id": "claim-a",
                    "model_id": "model-a",
                    "task": "defect",
                    "claim_text": "Photo access checks owner identity",
                    "comparison_group_id": "photo-owner-check",
                    "evidence_refs": ["src-1"],
                    "uncertainty": ["static-only", "runtime behavior unknown"],
                    "confidence": 0.72,
                    "generated_at": "2026-11-01T12:00:00+08:00",
                },
                {
                    "claim_id": "claim-b",
                    "model_id": "model-b",
                    "task": "review",
                    "claim_text": "Photo access checks owner identity",
                    "comparison_group_id": "photo-owner-check",
                    "evidence_refs": ["src-1", "missing-ref"],
                    "uncertainty": "No runtime evidence",
                    "generated_at": "2026-11-01T12:01:00+08:00",
                },
                {
                    "claim_id": "claim-c",
                    "model_id": "model-c",
                    "task": "review",
                    "claim_text": "The endpoint has no owner check",
                    "comparison_group_id": "photo-owner-check",
                    "evidence_refs": [],
                    "uncertainty": "Source context may be incomplete",
                    "generated_at": "2026-11-01T12:02:00+08:00",
                },
            ],
            "reviews": [
                {
                    "claim_id": "claim-a",
                    "decision": "SUPPORTS_CLAIM",
                    "reviewed_by": "human-reviewer",
                    "reviewed_at": "2026-11-01T13:00:00+08:00",
                    "rationale": "Static source reference was inspected; runtime behavior remains unknown.",
                    "evidence_refs": ["src-1"],
                }
            ],
        }

    def test_identical_claims_align_but_never_become_facts(self):
        result = review_claim_ledger(self.ledger)
        group = result["comparison_groups"][0]
        self.assertEqual(group["comparison_status"], "DIVERGENT_TEXT_REQUIRES_HUMAN_REVIEW")
        self.assertEqual(group["truth_status"], "NOT_ESTABLISHED")
        self.assertFalse(group["majority_vote_used"])
        for claim in result["claims"]:
            self.assertEqual(claim["claim_status"], "MODEL_HYPOTHESIS")
            self.assertEqual(claim["truth_status"], "NOT_ESTABLISHED")
        self.assertFalse(result["fusion_policy"]["automatic_verified_fact_promotion"])

    def test_exact_text_alignment_is_not_verification(self):
        ledger = dict(self.ledger)
        ledger["claims"] = [dict(self.ledger["claims"][0]), dict(self.ledger["claims"][1])]
        ledger["claims"][1]["claim_text"] = "  Photo access checks owner identity  "
        result = review_claim_ledger(ledger)
        group = result["comparison_groups"][0]
        self.assertEqual(
            group["comparison_status"],
            "TEXT_IDENTICAL_MULTI_MODEL_ALIGNMENT_NOT_VERIFICATION",
        )

    def test_review_attestation_and_unknown_references_are_preserved(self):
        result = review_claim_ledger(self.ledger)
        by_id = {row["claim_id"]: row for row in result["claims"]}
        self.assertEqual(by_id["claim-a"]["review_status"], "REVIEWED_SUPPORT_CANDIDATE")
        self.assertFalse(by_id["claim-a"]["review_attestation"]["identity_verified"])
        self.assertEqual(by_id["claim-b"]["unknown_evidence_refs"], ["missing-ref"])
        self.assertEqual(by_id["claim-b"]["evidence_reference_status"], "UNKNOWN_REFERENCES_PRESENT")
        self.assertEqual(by_id["claim-c"]["review_status"], "REVIEW_REQUIRED")

    def test_no_evidence_is_not_treated_as_negative_evidence(self):
        result = review_claim_ledger(self.ledger)
        by_id = {row["claim_id"]: row for row in result["claims"]}
        self.assertEqual(by_id["claim-c"]["evidence_reference_status"], "NO_EVIDENCE_REFERENCES")
        self.assertEqual(by_id["claim-c"]["truth_status"], "NOT_ESTABLISHED")

    def test_rejects_duplicate_claim_and_evidence_ids(self):
        ledger = dict(self.ledger)
        ledger["claims"] = self.ledger["claims"] + [dict(self.ledger["claims"][0])]
        with self.assertRaisesRegex(ValueError, "duplicate claim_id"):
            validate_claim_ledger(ledger)
        ledger = dict(self.ledger)
        ledger["evidence_catalog"] = self.ledger["evidence_catalog"] + [dict(self.ledger["evidence_catalog"][0])]
        with self.assertRaisesRegex(ValueError, "duplicate evidence_id"):
            validate_claim_ledger(ledger)

    def test_rejects_bad_hash_timestamp_confidence_and_review_reference(self):
        for mutate, message in [
            (lambda x: x["evidence_catalog"][0].update(source_sha256="bad"), "SHA-256"),
            (lambda x: x["claims"][0].update(generated_at="2026-11-01T12:00:00"), "timezone"),
            (lambda x: x["claims"][0].update(confidence=True), "self-reported"),
            (lambda x: x["reviews"][0].update(claim_id="unknown"), "unknown claim_id"),
            (lambda x: x["reviews"][0].update(decision="AUTO_VERIFY"), "unsupported review decision"),
        ]:
            ledger = json.loads(json.dumps(self.ledger))
            mutate(ledger)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_claim_ledger(ledger)

    def test_rejects_unknown_schema_and_invalid_uncertainty(self):
        ledger = dict(self.ledger)
        ledger["schema"] = "wrong"
        with self.assertRaisesRegex(ValueError, "schema"):
            validate_claim_ledger(ledger)
        ledger = json.loads(json.dumps(self.ledger))
        ledger["claims"][0]["uncertainty"] = {"guess": True}
        with self.assertRaisesRegex(ValueError, "uncertainty"):
            validate_claim_ledger(ledger)

    def test_file_loader_binds_exact_input_hash_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "claims.json"
            raw = json.dumps(self.ledger, ensure_ascii=False).encode("utf-8")
            source.write_bytes(raw)
            loaded, digest = load_claim_ledger(source)
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            result = review_claim_ledger_file(source)
            self.assertEqual(result["schema"], REVIEW_SCHEMA)
            self.assertEqual(result["input_sha256"], digest)
            link = Path(td) / "link.json"
            try:
                link.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this runner")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                load_claim_ledger(link)


    def test_file_loader_rejects_oversized_input_before_reading(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "oversized-claims.json"
            with source.open("wb") as stream:
                stream.truncate(10 * 1024 * 1024 + 1)
            with self.assertRaises((OSError, ValueError)):
                load_claim_ledger(source)

    def test_support_decision_without_resolved_review_evidence_stays_unverified(self):
        ledger = json.loads(json.dumps(self.ledger))
        ledger["reviews"][0]["evidence_refs"] = []
        result = review_claim_ledger(ledger)
        claim = next(row for row in result["claims"] if row["claim_id"] == "claim-a")
        self.assertEqual(claim["review_status"], "NEEDS_MORE_EVIDENCE")
        self.assertEqual(claim["truth_status"], "NOT_ESTABLISHED")

    def test_unknown_review_reference_is_counted_and_blocks_support_candidate(self):
        ledger = json.loads(json.dumps(self.ledger))
        ledger["reviews"][0]["evidence_refs"] = ["not-in-catalog"]
        result = review_claim_ledger(ledger)
        claim = next(row for row in result["claims"] if row["claim_id"] == "claim-a")
        self.assertEqual(claim["review_status"], "REVIEW_REQUIRED_UNKNOWN_EVIDENCE_REFERENCE")
        self.assertEqual(result["unknown_evidence_reference_count"], 2)

    def test_same_model_repeated_claim_is_not_called_multi_model_alignment(self):
        ledger = json.loads(json.dumps(self.ledger))
        ledger["claims"] = [dict(self.ledger["claims"][0]), dict(self.ledger["claims"][0])]
        ledger["claims"][1]["claim_id"] = "claim-a-copy"
        result = review_claim_ledger(ledger)
        self.assertEqual(
            result["comparison_groups"][0]["comparison_status"],
            "SAME_MODEL_REPEATED_CLAIM_REQUIRES_REVIEW",
        )

    def test_cli_writes_report_and_refuses_overwrite(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from usit import cli

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "ledger.json"
            output = Path(td) / "review.json"
            source.write_text(json.dumps(self.ledger), encoding="utf-8")
            with redirect_stdout(StringIO()):
                code = cli.main(["review-ai-claims", "--input", str(source), "--out", str(output)])
            self.assertEqual(code, 2)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], REVIEW_SCHEMA)
            self.assertEqual(report["overall_truth_status"], "NOT_ESTABLISHED")
            with redirect_stdout(StringIO()):
                code = cli.main(["review-ai-claims", "--input", str(source), "--out", str(output)])
            self.assertEqual(code, 2)


    def test_contradiction_and_conflict_without_evidence_stay_unverified(self):
        for decision in ("CONTRADICTS_CLAIM", "CONFLICTING_EVIDENCE"):
            ledger = json.loads(json.dumps(self.ledger))
            ledger["reviews"] = [{
                "claim_id": "claim-a",
                "decision": decision,
                "reviewed_by": "human-reviewer",
                "reviewed_at": "2026-11-01T13:00:00+08:00",
                "rationale": "Decision supplied without a supporting evidence reference.",
                "evidence_refs": [],
            }]
            result = review_claim_ledger(ledger)
            claim = next(row for row in result["claims"] if row["claim_id"] == "claim-a")
            self.assertEqual(claim["review_status"], "NEEDS_MORE_EVIDENCE")
            self.assertEqual(claim["truth_status"], "NOT_ESTABLISHED")

if __name__ == "__main__":
    unittest.main()
