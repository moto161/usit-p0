from __future__ import annotations

import unittest

from usit.database_access import extract_database_accesses
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

    def test_python_identity_source_has_ast_proven_function_owner_and_variable(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    uid = request.user.id\n"
                "    return uid\n"
            )
        })
        source = next(row for row in rows if row["kind"] == "authenticated_identity_source")
        self.assertEqual(source["owner_symbol_id"], "symbol:repo/photos.py#load:1")
        self.assertEqual(source["owner_resolution_status"], "RESOLVED")
        self.assertEqual(source["details"]["source_variable"], "uid")

    def test_non_python_identity_source_owner_remains_unknown(self):
        rows = extract_identity_evidence({"repo/photos.ts": "function load(req) {\n  const uid = req.user.id;\n}\n"})
        source = next(row for row in rows if row["kind"] == "authenticated_identity_source")
        self.assertIsNone(source["owner_symbol_id"])
        self.assertEqual(source["owner_resolution_status"], "UNKNOWN")

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
        self.assertEqual(binding["details"]["binding_status"], "SAME_LINE_PARAMETER_EXPRESSION")

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

    def test_does_not_infer_identity_across_scope_boundary(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\n"
                "  const uid = req.user.id;\n"
                "}\n"
                'db.query("SELECT id FROM photos WHERE owner_id = ?", [uid]);\n'
            )
        })
        propagated = [row for row in rows
                      if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                      and row["scope"] == "INTRAPROCEDURAL_LINEAR_ASSIGNMENT"]
        self.assertEqual(propagated, [])

    def test_extracts_exact_bare_parameter_used_in_sql_owner_binding(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def query_photos(owner_id):\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = ?", [owner_id])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "sql_owner_parameter_binding")
        self.assertEqual(binding["details"]["binding_parameters"], ["owner_id"])
        self.assertEqual(binding["details"]["resource_fields"], ["owner_id"])
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")
        self.assertEqual(binding["owner_symbol_id"], "symbol:repo/photos.py#query_photos:1")
        self.assertEqual(binding["verification_status"], "NOT_VERIFIED")

    def test_tracks_trusted_identity_through_local_variable_to_sql_sink(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\n"
                "  const uid = req.user.id;\n"
                '  db.query("SELECT id FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["scope"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
        self.assertEqual(binding["details"]["binding_status"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [2, 3])
        self.assertEqual(binding["verification_status"], "NOT_VERIFIED")

    def test_preserves_each_local_alias_hop_in_dataflow_evidence(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\n"
                "  const uid = req.user.id;\n"
                "  const owner = uid;\n"
                '  db.query("SELECT id FROM photos WHERE owner_id = ?", [owner]);\n'
                "}\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item.get("line") for item in binding["details"]["propagation"]], [2, 3, 4])
        self.assertEqual([item.get("variable") for item in binding["details"]["propagation"][:2]],
                         ["uid", "owner"])

    def test_tracks_request_identity_through_local_alias_to_sql_sink(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\n"
                "  const requestedOwner = req.body.user_id;\n"
                '  db.query("SELECT id FROM photos WHERE owner_id = ?", [requestedOwner]);\n'
                "}\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["binding_status"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")

    def test_tracks_python_local_identity_assignment_to_sql_sink(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    uid = request.user.id\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["scope"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [2, 3])

    def test_reassignment_to_unknown_value_invalidates_identity_alias(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\n"
                "  let uid = req.user.id;\n"
                "  uid = getFallbackId();\n"
                '  db.query("SELECT id FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
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


    def test_does_not_bind_identity_mentioned_only_inside_sql_string(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": 'db.query("SELECT req.user.id FROM photos WHERE owner_id = ?", [otherId]);\n'
        })
        bindings = [row for row in rows if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"]
        self.assertEqual(bindings, [])

    def test_does_not_bind_identity_used_only_after_sql_call(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": 'db.query("SELECT id FROM photos WHERE owner_id = ?", [otherId]); audit(req.user.id);\n'
        })
        bindings = [row for row in rows if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"]
        self.assertEqual(bindings, [])

    def test_alias_must_appear_in_sql_parameter_argument_not_sql_text(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": (
                "function load(req) {\n"
                "  const uid = req.user.id;\n"
                '  db.query("SELECT uid FROM photos WHERE owner_id = ?", [otherId]);\n'
                "}\n"
            )
        })
        propagated = [row for row in rows
                      if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                      and row["scope"] == "INTRAPROCEDURAL_LINEAR_ASSIGNMENT"]
        self.assertEqual(propagated, [])

    def test_direct_request_identity_requires_parameter_expression(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": 'db.query("SELECT id FROM photos WHERE owner_id = ?", [otherId, req.body.user_id]);\n'
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "SAME_LINE_PARAMETER_EXPRESSION")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])

    def test_does_not_infer_binding_when_sql_call_has_no_parameter_argument(self):
        rows = extract_identity_evidence({
            "repo/photos.ts": 'db.query("SELECT id FROM photos WHERE owner_id = ?");\n'
        })
        bindings = [row for row in rows if row["kind"].endswith("_to_sql_owner_binding")]
        self.assertEqual(bindings, [])

    # P2: multi-line database calls joined on balanced parens.

    def test_python_multiline_call_binds_aliased_identity(self):
        source = (
            "def load(request):\n"
            "    uid = request.user.id\n"
            "    cursor.execute(\n"
            '        "SELECT id FROM photos WHERE owner_id = %s",\n'
            "        (uid,),\n"
            "    )\n"
        )
        rows = extract_identity_evidence({"repo/photos.py": source})
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 3)
        self.assertEqual(binding["scope"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
        self.assertTrue(binding["details"]["multi_line_call"])
        self.assertEqual(binding["details"]["call_lines"], [3, 6])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [2, 3])
        # The sink record lands on the DB access record's line, so S2.9 can attach it.
        db = extract_database_accesses({"repo/photos.py": source})
        self.assertEqual(len(db), 1)
        self.assertEqual(db[0]["line"], 3)

    def test_python_multiline_call_binds_direct_identity_expression(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    cursor.execute(\n"
                '        "SELECT id FROM photos WHERE owner_id = %s",\n'
                "        (request.user.id,),\n"
                "    )\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 2)
        self.assertEqual(binding["scope"], "MULTI_LINE_CALL")
        self.assertEqual(binding["details"]["binding_status"],
                         "MULTI_LINE_CALL_PARAMETER_EXPRESSION")
        self.assertTrue(binding["details"]["multi_line_call"])
        self.assertEqual(binding["details"]["call_lines"], [2, 5])

    def test_javascript_multiline_call_binds_aliased_identity(self):
        rows = extract_identity_evidence({
            "repo/photos.js": (
                "async function load(req) {\n"
                "  const uid = req.user.id;\n"
                "  const rows = await pool.query(\n"
                '    "SELECT id FROM photos WHERE owner_id = ?",\n'
                "    [uid]\n"
                "  );\n"
                "}\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 3)
        self.assertTrue(binding["details"]["multi_line_call"])
        self.assertEqual(binding["details"]["call_lines"], [3, 6])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [2, 3])

    def test_single_line_call_is_not_marked_multiline(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (request.user.id,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 2)
        self.assertFalse(binding["details"]["multi_line_call"])
        self.assertEqual(binding["scope"], "SAME_LINE_PATTERN")
        self.assertEqual(binding["details"]["binding_status"], "SAME_LINE_PARAMETER_EXPRESSION")
        self.assertNotIn("call_lines", binding["details"])

    def test_call_spanning_more_than_ten_lines_is_not_joined(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    uid = request.user.id\n"
                "    cursor.execute(\n"
                '        "SELECT id FROM photos WHERE owner_id = %s",\n'
                "        (uid,),\n"
                "        # pad 1\n"
                "        # pad 2\n"
                "        # pad 3\n"
                "        # pad 4\n"
                "        # pad 5\n"
                "        # pad 6\n"
                "        # pad 7\n"
                "    )\n"
            )
        })
        bindings = [row for row in rows if row["kind"].endswith("_to_sql_owner_binding")
                    or row["kind"] == "sql_owner_parameter_binding"]
        self.assertEqual(bindings, [])

    def test_paren_inside_string_literal_does_not_break_join(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    uid = request.user.id\n"
                "    cursor.execute(\n"
                '        "SELECT id FROM photos WHERE note = \'(\' "\n'
                '        "AND owner_id = %s",\n'
                "        (uid,),\n"
                "    )\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 3)
        self.assertTrue(binding["details"]["multi_line_call"])
        self.assertEqual(binding["details"]["call_lines"], [3, 7])

    def test_multiline_call_without_owner_predicate_yields_no_binding(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    cursor.execute(\n"
                '        "SELECT id FROM photos WHERE title = %s",\n'
                "        (title,),\n"
                "    )\n"
            )
        })
        bindings = [row for row in rows if row["kind"].endswith("_to_sql_owner_binding")
                    or row["kind"] == "sql_owner_parameter_binding"]
        self.assertEqual(bindings, [])

    def test_python_multiline_call_binds_request_identity(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    cursor.execute(\n"
                '        "SELECT id FROM photos WHERE owner_id = %s",\n'
                "        (request.body.user_id,),\n"
                "    )\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 2)
        self.assertTrue(binding["details"]["multi_line_call"])
        self.assertEqual(binding["details"]["binding_status"],
                         "MULTI_LINE_CALL_PARAMETER_EXPRESSION")
        self.assertEqual(binding["details"]["request_expressions"], ["request.body.user_id"])

    # P3: TS/JS function owners supplied by the caller (TS parser ranges).

    _P3_TS_SOURCE = (
        "import { db } from './db';\n"
        "\n"
        "export async function loadPhotos(req: any) {\n"
        "  const photo = await db.getPhoto(req.params.id);\n"
        "  if (photo.owner_id === req.user.id) {\n"
        "    const rows = await db.query(\"SELECT id FROM photos WHERE owner_id = ?\", [req.user.id]);\n"
        "    return rows;\n"
        "  }\n"
        "  return [];\n"
        "}\n"
    )
    _P3_TS_OWNERS = {"web/photos.ts": {
        line: "symbol:web/photos.ts#loadPhotos:3" for line in range(3, 11)
    }}

    def test_typescript_comparison_resolves_function_owner_from_supplied_map(self):
        rows = extract_identity_evidence(
            {"web/photos.ts": self._P3_TS_SOURCE}, self._P3_TS_OWNERS)
        comparison = next(row for row in rows
                          if row["kind"] == "resource_owner_comparison")
        self.assertEqual(comparison["line"], 5)
        self.assertEqual(comparison["owner_symbol_id"],
                         "symbol:web/photos.ts#loadPhotos:3")
        self.assertEqual(comparison["owner_resolution_status"], "RESOLVED")

    def test_typescript_record_outside_any_function_stays_unknown(self):
        rows = extract_identity_evidence(
            {"web/photos.ts": "const uid = req.user.id;\n" + self._P3_TS_SOURCE},
            {"web/photos.ts": {
                line: "symbol:web/photos.ts#loadPhotos:4" for line in range(4, 12)
            }})
        top_level = next(row for row in rows
                         if row["kind"] == "authenticated_identity_source"
                         and row["line"] == 1)
        self.assertIsNone(top_level["owner_symbol_id"])
        self.assertEqual(top_level["owner_resolution_status"], "UNKNOWN")
        # Without a map the same file stays fully owner-less, as before P3.
        unmapped = extract_identity_evidence({"web/photos.ts": self._P3_TS_SOURCE})
        comparison = next(row for row in unmapped
                          if row["kind"] == "resource_owner_comparison")
        self.assertIsNone(comparison["owner_symbol_id"])
        self.assertEqual(comparison["owner_resolution_status"], "UNKNOWN")

    def test_python_owners_ignore_supplied_map(self):
        rows = extract_identity_evidence(
            {"repo/photos.py": (
                "def load(request):\n"
                "    uid = request.user.id\n"
                "    return uid\n"
            )},
            {"repo/photos.py": {2: "symbol:repo/photos.py#bogus:99"}},
        )
        source = next(row for row in rows if row["kind"] == "authenticated_identity_source")
        self.assertEqual(source["owner_symbol_id"], "symbol:repo/photos.py#load:1")
        self.assertEqual(source["owner_resolution_status"], "RESOLVED")


class IdentityReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    def test_helper_returning_trusted_identity_binds_through_assignment(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["scope"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [2, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["details"]["propagation"][1]["variable"], "uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:repo/photos.py#load:5")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")
        self.assertEqual(binding["verification_status"], "NOT_VERIFIED")

    def test_helper_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def get_owner(req):\n"
                "    return req.body.user_id\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    owner = get_owner(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])

    def test_helper_with_two_identical_returns_binds(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def get_uid(req):\n"
                "    if req:\n"
                "        return req.user.id\n"
                "    return req.user.id\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 4, 8, 9])

    def test_helper_mixed_with_none_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def get_uid(req):\n"
                "    if req:\n"
                "        return req.user.id\n"
                "    return None\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_helper_mixed_kinds_does_not_bind(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def get_uid(req):\n"
                "    if req:\n"
                "        return req.user.id\n"
                "    return req.body.user_id\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_helper_without_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def get_uid(req):\n"
                "    pass\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_helper_with_differing_identity_expressions_does_not_bind(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def get_uid(req):\n"
                "    if req:\n"
                "        return req.user.id\n"
                "    return session.user.id\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_helper_from_another_file_does_not_bind(self):
        # P5 moved the from-import case to binding; this test now locks the
        # remaining boundary: an attribute call into another module does not
        # bind (module-attribute helpers are a later increment).
        rows = extract_identity_evidence({
            "repo/helpers.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "repo/photos.py": (
                "import repo.helpers\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = repo.helpers.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_non_helper_call_assignment_does_not_bind(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    uid = compute_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_helper_defined_after_call_site_still_binds(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
                "\n"
                "\n"
                "def get_uid(req):\n"
                "    return req.user.id\n"
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [7, 2, 3])

    def test_reassignment_after_helper_call_invalidates_alias(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                "    uid = get_fallback()\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_method_call_does_not_bind(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "class Repo:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = Repo().get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_direct_assignment_still_uses_linear_assignment_status(self):
        rows = extract_identity_evidence({
            "repo/photos.py": (
                "def load(request):\n"
                "    uid = request.user.id\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTRAPROCEDURAL_LINEAR_ASSIGNMENT")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [2, 3])


class IdentityCrossFileReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    def test_from_import_helper_binds_with_definition_file_evidence(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 6)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["scope"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")
        self.assertEqual(binding["details"]["propagation"][0]["line"], 2)
        self.assertEqual(binding["details"]["propagation"][0]["function"], "server.utils.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["details"]["propagation"][1]["variable"], "uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/repos/photo_repo.py#load:4")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_from_import_alias_request_helper_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_owner(req):\n"
                "    return req.body.user_id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import get_owner as fetch_owner\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    owner = fetch_owner(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "server.utils.get_owner")

    def test_relative_import_helper_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from ..utils import get_uid as rel_uid\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = rel_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "server.utils.get_uid")

    def test_import_from_missing_module_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_imported_name_that_is_not_an_identity_helper_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return None\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_imported_name_undefined_in_target_file_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def other(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_star_import_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import *\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_local_definition_shadows_import(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def get_uid(req):\n"
                "    return None\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_module_attribute_call_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "import server.utils\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = server.utils.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_ambiguous_module_mapping_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/utils/__init__.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_reexported_helper_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/other.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/utils.py": "from server.other import get_uid\n",
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_imported_helper_with_two_identical_returns_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    if req:\n"
                "        return req.user.id\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(
            [item["line"] for item in binding["details"]["propagation"]], [3, 4, 5, 6])
        self.assertTrue(all(item["path"] == "server/utils.py"
                            for item in binding["details"]["propagation"][:2]))

    def test_same_file_helper_still_binds_after_merge(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server.utils import get_uid\n"
                "\n"
                "\n"
                "def get_local(req):\n"
                "    return req.user.id\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = get_local(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_local")
        self.assertEqual(binding["details"]["propagation"][0]["path"],
                         "server/repos/photo_repo.py")


if __name__ == "__main__":
    unittest.main()
