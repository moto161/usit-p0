from __future__ import annotations

import unittest

from usit.identity_evidence import extract_identity_evidence


class IdentityEvidenceTests(unittest.TestCase):
    def test_detects_trusted_identity_source(self):
        rows = extract_identity_evidence({
            "routes/photos.ts": "const uid = req.user.id;\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "authenticated_identity_source")
        self.assertEqual(rows[0]["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual(rows[0]["scope"], "SAME_LINE_PATTERN")

    def test_detects_request_controlled_identity_input(self):
        rows = extract_identity_evidence({
            "routes/photos.ts": "const uid = req.body.user_id;\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "request_controlled_identity_input")

    def test_detects_direct_trusted_identity_to_sql_owner_binding(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": 'db.query("SELECT id FROM photos WHERE owner_id = ?", [req.user.id]);\n'
        })
        kinds = {row["kind"] for row in rows}
        self.assertIn("authenticated_identity_source", kinds)
        self.assertIn("direct_authenticated_identity_to_sql_owner_binding", kinds)
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "SAME_LINE_PATTERN_ONLY")

    def test_detects_direct_request_identity_to_sql_owner_binding_without_claiming_vulnerability(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": 'db.query("SELECT id FROM photos WHERE owner_id = ?", [req.body.user_id]);\n'
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["verification_status"], "NOT_VERIFIED")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])

    def test_detects_owner_comparison(self):
        rows = extract_identity_evidence({
            "services/photos.ts": "if (photo.owner_id === req.user.id) { return photo; }\n"
        })
        self.assertIn("resource_owner_comparison", {row["kind"] for row in rows})

    def test_does_not_infer_cross_line_flow(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": 'const uid = req.user.id;\ndb.query("SELECT id FROM photos WHERE owner_id = ?", [uid]);\n'
        })
        self.assertNotIn("direct_authenticated_identity_to_sql_owner_binding",
                         {row["kind"] for row in rows})

    def test_tracks_trusted_identity_through_local_variable_to_sql_sink(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\\n"
                "  const uid = req.user.id;\\n"
                '  db.query("SELECT id FROM photos WHERE owner_id = ?", [uid]);\\n'
                "}\\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["scope"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
        self.assertEqual(binding["details"]["binding_status"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [2, 3])
        self.assertEqual(binding["verification_status"], "NOT_VERIFIED")

    def test_tracks_request_identity_through_local_alias_to_sql_sink(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\\n"
                "  const requestedOwner = req.body.user_id;\\n"
                '  db.query("SELECT id FROM photos WHERE owner_id = ?", [requestedOwner]);\\n'
                "}\\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["binding_status"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")

    def test_tracks_python_local_identity_assignment_to_sql_sink(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\\n"
                "    uid = request.user.id\\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = ?", [uid])\\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["scope"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [2, 3])

    def test_reassignment_to_unknown_value_invalidates_identity_alias(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\\n"
                "  let uid = req.user.id;\\n"
                "  uid = getFallbackId();\\n"
                '  db.query("SELECT id FROM photos WHERE owner_id = ?", [uid]);\\n'
                "}\\n"
            )
        })
        propagated = [row for row in rows
                      if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                      and row["scope"] == "INTRAPROCEDURAL_LINEAR_ASSIGNMENT"]
        self.assertEqual(propagated, [])

    def test_deterministic_and_unrelated_lines_ignored(self):
        source = {"repo/photos.ts": "const count = 1;\n"}
        self.assertEqual(extract_identity_evidence(source), [])
        self.assertEqual(extract_identity_evidence(source), extract_identity_evidence(source))


if __name__ == "__main__":
    unittest.main()
