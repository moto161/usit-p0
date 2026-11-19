from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from usit.database_access import extract_database_accesses
from usit.identity_evidence import extract_identity_evidence
from usit.js_ts_parser import analyze_js_ts_project, ts_function_owners
from usit.scanner import discover_files
from usit.vue_parser import vue_script_for_adapter


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
        # P6 moved the module-attribute case to binding; this test now locks
        # the remaining boundary: a method/attribute call whose first
        # segment is not a module binding does not bind.
        rows = extract_identity_evidence({
            "repo/helpers.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "repo/photos.py": (
                "import repo.helpers\n"
                "\n"
                "\n"
                "def load(request, svc):\n"
                "    uid = svc.helpers.get_uid(request)\n"
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
        # P6 moved the module-attribute case to binding; this test now locks
        # the remaining boundary: a method/attribute call whose first
        # segment is not a module binding does not bind.
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "import server.utils\n"
                "\n"
                "\n"
                "def load(request, svc):\n"
                "    uid = svc.utils.get_uid(request)\n"
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


class IdentityAttributeCallReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    def test_import_module_attribute_call_binds(self):
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

    def test_from_import_module_attribute_call_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server import utils\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = utils.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "server.utils.get_uid")

    def test_import_module_as_alias_attribute_call_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "import server.utils as u\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = u.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "server.utils.get_uid")

    def test_from_import_module_as_alias_request_helper_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_owner(req):\n"
                "    return req.body.user_id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server import utils as u\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    owner = u.get_owner(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "server.utils.get_owner")

    def test_top_level_package_import_chain_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "import server\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = server.utils.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "server.utils.get_uid")

    def test_attribute_call_to_non_helper_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def other(req):\n"
                "    return None\n"
            ),
            "server/repos/photo_repo.py": (
                "import server.utils\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = server.utils.other(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_attribute_call_to_missing_module_does_not_bind(self):
        rows = extract_identity_evidence({
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

    def test_local_definition_shadows_module_binding(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from server import utils\n"
                "\n"
                "\n"
                "def utils():\n"
                "    return None\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = utils.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_relative_submodule_import_attribute_call_binds(self):
        rows = extract_identity_evidence({
            "server/repos/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "from . import utils\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = utils.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/repos/utils.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "server.repos.utils.get_uid")

    def test_attribute_call_helper_with_two_identical_returns_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    if req:\n"
                "        return req.user.id\n"
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
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(
            [item["line"] for item in binding["details"]["propagation"]], [3, 4, 5, 6])
        self.assertTrue(all(item["path"] == "server/utils.py"
                            for item in binding["details"]["propagation"][:2]))

    def test_attribute_reference_without_call_does_not_bind(self):
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
                "    uid = server.utils.get_uid\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_from_import_bare_call_still_binds(self):
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
        self.assertEqual(binding["details"]["propagation"][0]["function"], "server.utils.get_uid")

    def test_module_level_assignment_shadows_module_binding(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "def get_uid(req):\n"
                "    return req.user.id\n"
            ),
            "server/repos/photo_repo.py": (
                "import server.utils\n"
                "\n"
                "server = 5\n"
                "\n"
                "\n"
                "def load(request):\n"
                "    uid = server.utils.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class IdentitySelfMethodReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    def test_self_method_returning_trusted_identity_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["scope"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/photos.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["details"]["propagation"][1]["variable"], "uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:5")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")
        self.assertEqual(binding["verification_status"], "NOT_VERIFIED")

    def test_self_method_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_owner(self, req):\n"
                "        return req.body.user_id\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        owner = self.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_owner")

    def test_self_method_with_two_identical_returns_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        if req:\n"
                "            return req.user.id\n"
                "        return req.user.id\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [4, 5, 8, 9])

    def test_self_method_mixed_with_none_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        if req:\n"
                "            return req.user.id\n"
                "        return None\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_self_method_mixed_kinds_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        if req:\n"
                "            return req.user.id\n"
                "        return req.body.user_id\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_self_method_without_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        pass\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_self_attribute_chain_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_non_self_receiver_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = other.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_self_method_defined_in_another_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def list_photos(self, request):\n"
                "        uid = self.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cls_method_call_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    @classmethod\n"
                "    def get_uid(cls, req):\n"
                "        return req.user.id\n"
                "\n"
                "    @classmethod\n"
                "    def list_photos(cls, request):\n"
                "        uid = cls.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [4, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_self_call_outside_any_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "\n"
                "def load(self, request):\n"
                "    uid = self.get_uid(request)\n"
                '    cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_bare_name_helper_still_binds_after_profile_extraction(self):
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
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")


class IdentityInstanceAttributeReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    def test_instance_attribute_helper_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 11)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["scope"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 10, 11])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/photos.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["details"]["propagation"][1]["variable"], "uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:9")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")
        self.assertEqual(binding["verification_status"], "NOT_VERIFIED")

    def test_instance_attribute_helper_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_owner(self, req):\n"
                "        return req.body.user_id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")

    def test_attribute_bound_outside_init_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def setup(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_attribute_bound_to_two_classes_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class OtherHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = OtherHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_attribute_assigned_from_parameter_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self, helper):\n"
                "        self.helper = helper\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_attribute_constructing_unknown_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = MissingHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_unbound_attribute_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.other.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_four_segment_chain_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.a.b.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_target_method_mixed_with_none_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        if req:\n"
                "            return req.user.id\n"
                "        return None\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cls_attribute_chain_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    @classmethod\n"
                "    def list_photos(cls, request):\n"
                "        uid = cls.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_same_class_self_method_still_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_target_class_in_another_file_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/photos.py": (
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class TsThisMethodReturnPropagationTests(unittest.TestCase):
    def _rows(self, source, filename="auth.ts"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / filename).write_text(source, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            owners = ts_function_owners(parsed)
            return extract_identity_evidence({filename: source}, owners)

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _SERVICE = (
        "export class AuthService {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "  load(req: any) {\n"
        "    const uid = this.get_uid(req);\n"
        '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
        "  }\n"
        "}\n"
    )

    def test_this_method_returning_trusted_identity_binds(self):
        rows = self._rows(self._SERVICE)
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["scope"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthService.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["details"]["propagation"][1]["variable"], "uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:auth.ts#load:5")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")
        self.assertEqual(binding["verification_status"], "NOT_VERIFIED")

    def test_this_method_returning_request_identity_binds(self):
        rows = self._rows(
            "export class AuthService {\n"
            "  get_owner(req: any) {\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const owner = this.get_owner(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [owner]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthService.get_owner")

    def test_this_method_alias_feeds_two_sinks_in_one_method(self):
        rows = self._rows(
            "export class AuthService {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    const a = db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            '    return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        bindings = self._direct_bindings(rows)
        self.assertEqual([row["line"] for row in bindings], [7, 8])
        self.assertEqual([item["line"] for item in bindings[0]["details"]["propagation"]], [3, 6, 7])
        self.assertEqual([item["line"] for item in bindings[1]["details"]["propagation"]], [3, 6, 8])

    def test_this_method_with_non_identity_return_does_not_bind(self):
        rows = self._rows(
            "export class AuthService {\n"
            "  get_uid(req: any) {\n"
            "    return req.params.id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_this_method_with_mixed_returns_does_not_bind(self):
        rows = self._rows(
            "export class AuthService {\n"
            "  get_uid(req: any) {\n"
            "    if (req) {\n"
            "      return req.user.id;\n"
            "    }\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_this_method_without_return_does_not_bind(self):
        rows = self._rows(
            "export class AuthService {\n"
            "  get_uid(req: any) {\n"
            "    console.log(req.user.id);\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_this_call_outside_any_class_does_not_bind(self):
        rows = self._rows(
            "export class AuthService {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export function load(req: any) {\n"
            "  const uid = this.get_uid(req);\n"
            '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_same_method_name_in_two_classes_resolves_to_own_class(self):
        rows = self._rows(
            "export class A {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
            "export class B {\n"
            "  get_uid(req: any) {\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        trusted = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        requested = next(row for row in rows
                         if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(trusted["details"]["propagation"][0]["function"], "A.get_uid")
        self.assertEqual(requested["details"]["propagation"][0]["function"], "B.get_uid")

    def test_identity_return_inside_nested_arrow_does_not_count(self):
        # Sharp test: the nested arrow returns a DIFFERENT identity kind,
        # so leaking it in would mix kinds and disqualify the method. The
        # binding exists only when the nested return is excluded.
        rows = self._rows(
            "export class AuthService {\n"
            "  get_uid(req: any) {\n"
            "    const f = () => { return req.body.user_id; };\n"
            "    return req.user.id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])

    def test_identity_return_inside_next_line_arrow_body_does_not_count(self):
        rows = self._rows(
            "export class AuthService {\n"
            "  get_uid(req: any) {\n"
            "    const f = () =>\n"
            "    { return req.body.user_id; };\n"
            "    return req.user.id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])


class TsInstanceAttributeReturnPropagationTests(unittest.TestCase):
    def _rows_files(self, files):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                (root / name).write_text(source, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners)

    def _rows(self, source, filename="handlers.ts"):
        return self._rows_files({filename: source})

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _HELPER = (
        "export class AuthHelper {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "}\n"
    )

    def test_constructor_assigned_helper_binds(self):
        rows = self._rows(
            self._HELPER
            + "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = new AuthHelper();\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 12)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 11, 12])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][1]["variable"], "uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:10")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_field_declared_helper_binds(self):
        rows = self._rows(
            self._HELPER
            + "export class AlbumHandler {\n"
            "  private helper = new AuthHelper();\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 10)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 9, 10])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_helper_returning_request_identity_binds(self):
        rows = self._rows(
            "export class AuthHelper {\n"
            "  get_owner(req: any) {\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "}\n"
            "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = new AuthHelper();\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const owner = this.helper.get_owner(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [owner]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")

    def test_non_new_factory_assignment_does_not_bind(self):
        rows = self._rows(
            self._HELPER
            + "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = getHelper();\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_reassigning_to_non_construction_disqualifies(self):
        rows = self._rows(
            self._HELPER
            + "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = new AuthHelper();\n"
            "    this.helper = other;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_two_different_constructed_classes_disqualifies(self):
        rows = self._rows(
            self._HELPER
            + "export class OtherHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "}\n"
            "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = new AuthHelper();\n"
            "  }\n"
            "  setup() {\n"
            "    this.helper = new OtherHelper();\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_constructed_class_not_in_file_does_not_bind(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = new ExternalHelper();\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_constructor_parameter_property_does_not_bind(self):
        rows = self._rows(
            self._HELPER
            + "export class PhotoHandler {\n"
            "  constructor(private helper: AuthHelper) {\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_nested_function_assignment_ignored_but_arrow_counts(self):
        nested_function = (
            self._HELPER
            + "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    function init() {\n"
            "      this.helper = new AuthHelper();\n"
            "    }\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(self._rows(nested_function)), [])
        nested_arrow = (
            self._HELPER
            + "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    const init = () => {\n"
            "      this.helper = new AuthHelper();\n"
            "    };\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(len(self._direct_bindings(self._rows(nested_arrow))), 1)

class TsCrossFileClassReturnPropagationTests(unittest.TestCase):
    def _rows_files(self, files, ts_config=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            if ts_config is not None:
                (root / "tsconfig.json").write_text(ts_config, encoding="utf-8")
            # Memory mode, exactly like the scanner: a `.vue` file joins
            # through its line-padded `<script>` text (disk mode skips
            # `.vue` files and reports no symbols for them).
            js_ts_source = {}
            for name, source in files.items():
                if name.endswith(".vue"):
                    script = vue_script_for_adapter(source)
                    if script is not None:
                        js_ts_source[name] = script
                elif Path(name).suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                    js_ts_source[name] = source
            parsed = analyze_js_ts_project(root, [], js_ts_source=js_ts_source)
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners, ts_config=ts_config)

    def _rows(self, source, filename="handlers.ts"):
        return self._rows_files({filename: source})

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _HELPER = (
        "export class AuthHelper {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "}\n"
    )

    _HANDLER = (
        'import { AuthHelper } from "./auth";\n'
        "\n"
        "export class PhotoHandler {\n"
        "  constructor() {\n"
        "    this.helper = new AuthHelper();\n"
        "  }\n"
        "  load(req: any) {\n"
        "    const uid = this.helper.get_uid(req);\n"
        '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
        "  }\n"
        "}\n"
    )

    def test_imported_helper_binds(self):
        # Moved from T2 (test_helper_class_in_another_file_does_not_bind):
        # the same fixture now binds through the cross-file import.
        rows = self._rows_files({"auth.ts": self._HELPER, "handlers.ts": self._HANDLER})
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_field_declared_imported_helper_binds(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import { AuthHelper } from "./auth";\n'
                "\n"
                "export class AlbumHandler {\n"
                "  private helper = new AuthHelper();\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")

    def test_namespace_imported_helper_binds(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import * as auth from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new auth.AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_namespace_imported_helper_constructed_in_method_binds(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import * as auth from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  load(req: any) {\n"
                "    this.helper = new auth.AuthHelper();\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_namespace_imported_helper_field_binds(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import * as auth from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  private helper = new auth.AuthHelper();\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_namespace_imported_helper_inherited_method_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export class BaseAuth {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
                "export class AuthHelper extends BaseAuth {\n"
                "}\n"
            ),
            "handlers.ts": (
                'import * as auth from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new auth.AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_namespace_imported_helper_via_tsconfig_paths_binds(self):
        rows = self._rows_files({
            "src/auth.ts": self._HELPER,
            "src/handlers.ts": (
                'import * as auth from "@/auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new auth.AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        }, ts_config='{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n')
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_namespace_imported_helper_in_vue_options_binds(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "App.vue": (
                "<template><div /></template>\n"
                "<script lang=\"ts\">\n"
                'import * as auth from "./auth";\n'
                "export default {\n"
                "  created() {\n"
                "    this.helper = new auth.AuthHelper();\n"
                "  },\n"
                "  methods: {\n"
                "    load(req) {\n"
                "      const uid = this.helper.get_uid(req);\n"
                "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "    }\n"
                "  }\n"
                "}\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 11)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 10, 11])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:9")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_namespace_imported_helper_missing_target_does_not_bind(self):
        rows = self._rows_files({
            "handlers.ts": (
                'import * as auth from "./nope";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new auth.AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_namespace_imported_helper_missing_class_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import * as auth from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new auth.Other();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_three_segment_construction_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import * as a from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new a.b.AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_namespace_imported_helper_two_classes_disqualify(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import * as auth from "./auth";\n'
                "\n"
                "export class Other {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new auth.AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    this.helper = new Other();\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_default_imported_class_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import AuthHelper from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_default_imported_class_alias_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import Helper from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new Helper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_default_imported_class_inherited_method_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export class BaseAuth {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
                "export default class AuthHelper extends BaseAuth {\n"
                "}\n"
            ),
            "handlers.ts": (
                'import AuthHelper from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_default_imported_class_via_tsconfig_paths_binds(self):
        rows = self._rows_files({
            "src/auth.ts": (
                "export default class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "src/handlers.ts": (
                'import AuthHelper from "@/auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        }, ts_config='{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n')
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_default_imported_class_in_vue_options_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "App.vue": (
                "<template><div /></template>\n"
                "<script lang=\"ts\">\n"
                'import AuthHelper from "./auth";\n'
                "export default {\n"
                "  created() {\n"
                "    this.helper = new AuthHelper();\n"
                "  },\n"
                "  methods: {\n"
                "    load(req) {\n"
                "      const uid = this.helper.get_uid(req);\n"
                "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "    }\n"
                "  }\n"
                "}\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 11)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 10, 11])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:9")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_default_imported_class_constructed_in_method_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import AuthHelper from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  load(req: any) {\n"
                "    this.helper = new AuthHelper();\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_default_imported_class_missing_default_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import AuthHelper from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_anonymous_default_class_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default class {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import AuthHelper from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_default_imported_class_missing_method_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import AuthHelper from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.other(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_default_imported_class_two_classes_disqualify(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import AuthHelper from "./auth";\n'
                "\n"
                "export class Other {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    this.helper = new Other();\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_imported_helper_returning_request_identity_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export class AuthHelper {\n"
                "  get_owner(req: any) {\n"
                "    return req.body.user_id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import { AuthHelper } from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const owner = this.helper.get_owner(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [owner]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")

    def test_aliased_import_binds(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import { AuthHelper as Helper } from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new Helper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")

    def test_multiline_import_binds(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                "import {\n"
                "  AuthHelper,\n"
                '} from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(len(self._direct_bindings(rows)), 1)

    def test_imported_module_not_in_project_does_not_bind(self):
        rows = self._rows_files({
            "handlers.ts": (
                'import { AuthHelper } from "./missing";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_imported_name_not_a_class_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": (
                "export function AuthHelper(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "handlers.ts": self._HANDLER,
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_type_only_import_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "handlers.ts": (
                'import type { AuthHelper } from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_same_local_imported_from_two_modules_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._HELPER,
            "other.ts": (
                "export class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.body.user_id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import { AuthHelper } from "./auth";\n'
                'import { AuthHelper } from "./other";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_same_file_class_wins_over_import(self):
        rows = self._rows_files({
            "auth.ts": (
                "export class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.body.user_id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import { AuthHelper } from "./auth";\n'
                "export class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "handlers.ts")


class TsMultiNameSameTargetTests(unittest.TestCase):
    def _rows_files(self, files, ts_config=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            if ts_config is not None:
                (root / "tsconfig.json").write_text(ts_config, encoding="utf-8")
            js_ts_source = {}
            for name, source in files.items():
                if name.endswith(".vue"):
                    script = vue_script_for_adapter(source)
                    if script is not None:
                        js_ts_source[name] = script
                elif Path(name).suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                    js_ts_source[name] = source
            parsed = analyze_js_ts_project(root, [], js_ts_source=js_ts_source)
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners, ts_config=ts_config)

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _AUTH = (
        "export class AuthHelper {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "}\n"
    )

    _AUTH_DEFAULT = (
        "export default class AuthHelper {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "}\n"
    )

    def _handler(self, import_block, assign_ctor, assign_setup,
                 method="get_uid", tail="uid"):
        lines = import_block.split("\n") + [
            "",
            "export class PhotoHandler {",
            "  constructor() {",
            f"    this.helper = {assign_ctor};",
            "  }",
            "  setup() {",
            f"    this.helper = {assign_setup};",
            "  }",
            "  load(req: any) {",
        ]
        if tail == "uid":
            lines += [
                f"    const uid = this.helper.{method}(req);",
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);',
            ]
        else:
            lines += [
                f"    const owner = this.helper.{method}(req);",
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [owner]);',
            ]
        lines += ["  }", "}"]
        return "\n".join(lines) + "\n"

    def test_bare_named_and_dotted_namespace_mixed_binds(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": self._handler(
                'import { AuthHelper } from "./auth";\nimport * as auth from "./auth";',
                "new AuthHelper()", "new auth.AuthHelper()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 13)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 12, 13])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:11")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_bare_default_and_dotted_namespace_mixed_binds(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH_DEFAULT,
            "handlers.ts": self._handler(
                'import Helper from "./auth";\nimport * as auth from "./auth";',
                "new Helper()", "new auth.AuthHelper()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_relative_named_and_aliased_named_mixed_binds(self):
        rows = self._rows_files({
            "src/auth.ts": self._AUTH,
            "src/handlers.ts": self._handler(
                'import { AuthHelper } from "./auth";\n'
                'import { AuthHelper as AliasedHelper } from "@/auth";',
                "new AuthHelper()", "new AliasedHelper()"),
        }, ts_config='{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n')
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_mixed_returning_request_identity_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export class AuthHelper {\n"
                "  get_owner(req: any) {\n"
                "    return req.body.user_id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": self._handler(
                'import { AuthHelper } from "./auth";\nimport * as auth from "./auth";',
                "new AuthHelper()", "new auth.AuthHelper()",
                method="get_owner", tail="owner"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")

    def test_mixed_with_local_direct_class_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": (
                'import * as auth from "./auth";\n\n' + self._AUTH + "\n"
                + self._handler("", "new auth.AuthHelper()", "new AuthHelper()")
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_with_other_module_class_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "other.ts": (
                "export class OtherHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.session.id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": self._handler(
                'import * as auth from "./auth";\nimport { OtherHelper } from "./other";',
                "new auth.AuthHelper()", "new OtherHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_with_non_construction_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": self._handler(
                'import { AuthHelper } from "./auth";\nimport * as auth from "./auth";',
                "new AuthHelper()", "5"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_with_unresolvable_namespace_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": self._handler(
                'import { AuthHelper } from "./auth";\nimport * as external from "./external";',
                "new AuthHelper()", "new external.AuthHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_with_missing_class_in_target_file_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": self._handler(
                'import { AuthHelper } from "./auth";\nimport * as auth from "./auth";',
                "new AuthHelper()", "new auth.Other()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_with_ambiguous_import_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "other.ts": (
                "export class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.body.user_id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": self._handler(
                'import { AuthHelper } from "./auth";\n'
                'import { AuthHelper } from "./other";\n'
                'import * as auth from "./auth";',
                "new AuthHelper()", "new auth.AuthHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class TsInheritedMethodReturnPropagationTests(unittest.TestCase):
    def _rows_files(self, files):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                (root / name).write_text(source, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners)

    def _rows(self, source, filename="handlers.ts"):
        return self._rows_files({filename: source})

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _BASE = (
        "export class BaseAuth {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "}\n"
    )

    def test_inherited_method_binds(self):
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class PhotoHandler extends BaseAuth {\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 10)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 9, 10])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:8")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_imported_base_class_binds(self):
        rows = self._rows_files({
            "auth.ts": self._BASE,
            "handlers.ts": (
                'import { BaseAuth } from "./auth";\n'
                "\n"
                "export class PhotoHandler extends BaseAuth {\n"
                "  load(req: any) {\n"
                "    const uid = this.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 6)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 5, 6])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_inherited_request_identity_binds(self):
        rows = self._rows(
            "export class BaseAuth {\n"
            "  get_owner(req: any) {\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "}\n"
            "\n"
            "export class PhotoHandler extends BaseAuth {\n"
            "  load(req: any) {\n"
            "    const owner = this.get_owner(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [owner]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_owner")

    def test_overriding_with_non_identity_shadows_base(self):
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class PhotoHandler extends BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    return null;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_overriding_with_other_identity_binds_that_one(self):
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class PhotoHandler extends BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_super_call_as_alias_binds(self):
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class PhotoHandler extends BaseAuth {\n"
            "  load(req: any) {\n"
            "    const uid = super.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 10)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 9, 10])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        # NOTE: the override-delegating-through-super fixture that used
        # to assert no binding here moved to T5 (TsSuperDelegation-
        # ReturnPropagationTests.test_super_delegation_binds), where the
        # same fixture now binds.

    def test_grandparent_method_binds(self):
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class Middle extends BaseAuth {\n"
            "}\n"
            "\n"
            "export class Leaf extends Middle {\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 13)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 12, 13])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_cyclic_extends_terminates_without_binding(self):
        rows = self._rows(
            "export class A extends B {\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
            "export class B extends A {\n"
            "  other() {\n"
            "    return 1;\n"
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_expression_or_namespaced_base_does_not_bind(self):
        mixin = self._rows(
            self._BASE
            + "\n"
            + "export class PhotoHandler extends mixin(BaseAuth) {\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(mixin), [])
        namespaced = self._rows(
            self._BASE
            + "\n"
            + "export class PhotoHandler extends ns.BaseAuth {\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(namespaced), [])

    def test_helper_constructed_from_subclass_binds(self):
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class SubHandler extends BaseAuth {\n"
            "}\n"
            "\n"
            + "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = new SubHandler();\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 16)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 15, 16])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")


class TsSuperDelegationReturnPropagationTests(unittest.TestCase):
    def _rows_files(self, files):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                (root / name).write_text(source, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners)

    def _rows(self, source, filename="handlers.ts"):
        return self._rows_files({filename: source})

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _BASE = (
        "export class BaseAuth {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "}\n"
    )

    _DELEGATING = (
        "export class AlbumHandler extends BaseAuth {\n"
        "  get_uid(req: any) {\n"
        "    return super.get_uid(req);\n"
        "  }\n"
        "  load(req: any) {\n"
        "    const uid = this.get_uid(req);\n"
        '    return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid]);\n'
        "  }\n"
        "}\n"
    )

    def test_super_delegation_binds(self):
        # Moved from T4 (second fixture of test_super_call_as_alias_binds):
        # the same fixture now binds through the delegation chain.
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class PhotoHandler extends BaseAuth {\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
            + "\n"
            + self._DELEGATING
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 20)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 16, 19, 20])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "AlbumHandler.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:18")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_delegation_to_imported_base_binds(self):
        rows = self._rows_files({
            "auth.ts": self._BASE,
            "handlers.ts": (
                'import { BaseAuth } from "./auth";\n'
                "\n"
                + self._DELEGATING
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "AlbumHandler.get_uid")

    def test_delegation_of_request_identity_binds(self):
        rows = self._rows(
            "export class BaseAuth {\n"
            "  get_owner(req: any) {\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "}\n"
            "\n"
            "export class AlbumHandler extends BaseAuth {\n"
            "  get_owner(req: any) {\n"
            "    return super.get_owner(req);\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const owner = this.get_owner(req);\n"
            '    return db.query("SELECT * FROM albums WHERE owner_id = ?", [owner]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_owner")

    def test_delegation_to_non_identity_does_not_bind(self):
        rows = self._rows(
            "export class BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    return null;\n"
            "  }\n"
            "}\n"
            "\n"
            + self._DELEGATING
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_delegation_with_mismatched_expression_does_not_bind(self):
        rows = self._rows(
            "export class BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    return req.body.user_id;\n"
            "  }\n"
            "}\n"
            "\n"
            "export class AlbumHandler extends BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    if (req.flag) {\n"
            "      return super.get_uid(req);\n"
            "    }\n"
            "    return req.user.id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_direct_and_delegated_returns_do_not_bind(self):
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class AlbumHandler extends BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    if (req.flag) {\n"
            "      return super.get_uid(req);\n"
            "    }\n"
            "    return req.user.id;\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_delegation_to_two_different_targets_does_not_bind(self):
        rows = self._rows(
            "export class BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "  get_other(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "\n"
            "export class AlbumHandler extends BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    if (req.flag) {\n"
            "      return super.get_uid(req);\n"
            "    }\n"
            "    return super.get_other(req);\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_two_level_delegation_binds(self):
        rows = self._rows(
            "export class A {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "\n"
            "export class B extends A {\n"
            "  get_uid(req: any) {\n"
            "    return super.get_uid(req);\n"
            "  }\n"
            "}\n"
            "\n"
            "export class C extends B {\n"
            "  get_uid(req: any) {\n"
            "    return super.get_uid(req);\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 19)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 9, 15, 18, 19])
        self.assertEqual([item["function"] for item in binding["details"]["propagation"][:3]],
                         ["A.get_uid", "B.get_uid", "C.get_uid"])

    def test_cyclic_delegation_terminates_without_binding(self):
        rows = self._rows(
            "export class A extends B {\n"
            "  get_uid(req: any) {\n"
            "    return super.get_uid(req);\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
            "export class B extends A {\n"
            "  get_uid(req: any) {\n"
            "    return super.get_uid(req);\n"
            "  }\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_helper_method_delegating_to_base_binds(self):
        rows = self._rows(
            self._BASE
            + "\n"
            + "export class SubHandler extends BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    return super.get_uid(req);\n"
            "  }\n"
            "}\n"
            "\n"
            + "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = new SubHandler();\n"
            "  }\n"
            "  load(req: any) {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  }\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 19)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 9, 18, 19])
        self.assertEqual(binding["details"]["propagation"][1]["function"], "SubHandler.get_uid")


class IdentityInheritedMethodReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _BASE = (
        "class BaseAuth:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    def test_inherited_method_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth):\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.py#load:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_imported_base_class_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._BASE,
            "handlers.py": (
                "from auth import BaseAuth\n"
                "\n\n"
                "class PhotoHandler(BaseAuth):\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_inherited_request_identity_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                "class BaseAuth:\n"
                "    def get_owner(self, req):\n"
                "        return req.body.user_id\n"
                "\n\n"
                "class PhotoHandler(BaseAuth):\n"
                "    def load(self, req):\n"
                "        owner = self.get_owner(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [owner])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_owner")

    def test_overriding_with_non_identity_shadows_base(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth):\n"
                "    def get_uid(self, req):\n"
                "        return None\n"
                "\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_overriding_with_other_identity_binds_that_one(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth):\n"
                "    def get_uid(self, req):\n"
                "        return req.body.user_id\n"
                "\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_super_call_as_alias_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth):\n"
                "    def load(self, req):\n"
                "        uid = super().get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]], [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_grandparent_method_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class Middle(BaseAuth):\n"
                "    pass\n"
                "\n\n"
                "class Leaf(Middle):\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 13)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 12, 13])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_cyclic_extends_terminates_without_binding(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                "class A(B):\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
                "\n\n"
                "class B(A):\n"
                "    def other(self):\n"
                "        return 1\n"
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_expression_or_attribute_base_does_not_bind(self):
        mixin = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(mixin(BaseAuth)):\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        self.assertEqual(self._direct_bindings(mixin), [])
        attribute = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(auth.BaseAuth):\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        self.assertEqual(self._direct_bindings(attribute), [])
        # Boundary move (P10): the multiple-inheritance fixture that used
        # to assert no binding here moved to P10
        # (IdentityMultipleInheritanceReturnPropagationTests), where the
        # same fixture now binds via the C3 MRO.

    def test_super_delegation_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth):\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
                "\n\n"
                "class AlbumHandler(BaseAuth):\n"
                "    def get_uid(self, req):\n"
                "        return super().get_uid(req)\n"
                "\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 18)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 14, 17, 18])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "AlbumHandler.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.py#load:16")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")
        # P8 combination: the helper class itself inherits the method.
        helper = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class SubHandler(BaseAuth):\n"
                "    pass\n"
                "\n\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = SubHandler()\n"
                "\n"
                "    def load(self, req):\n"
                "        uid = self.helper.get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        helper_binding = next(row for row in helper
                              if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(helper_binding["line"], 16)
        self.assertEqual([item["line"] for item in helper_binding["details"]["propagation"]],
                         [3, 15, 16])
        self.assertEqual(helper_binding["details"]["propagation"][0]["function"],
                         "BaseAuth.get_uid")


class IdentityMultipleInheritanceReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _BASE = (
        "class BaseAuth:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    _MIXIN = (
        "class AuditMixin:\n"
        "    def get_uid(self, req):\n"
        "        return req.session.id\n"
    )

    _LOAD = (
        "    def load(self, req):\n"
        "        uid = self.get_uid(req)\n"
        '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
    )

    def test_multiple_inheritance_first_base_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + self._MIXIN
                + "\n\n"
                + "class PhotoHandler(BaseAuth, AuditMixin):\n"
                + self._LOAD
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 14)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 13, 14])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.py#load:12")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_multiple_inheritance_second_base_binds_when_first_lacks_method(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                "class AuditMixin:\n"
                "    pass\n"
                "\n\n"
                + self._BASE
                + "\n\n"
                + "class PhotoHandler(AuditMixin, BaseAuth):\n"
                + self._LOAD
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_multiple_inheritance_first_base_shadows_second(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._MIXIN
                + "\n\n"
                + self._BASE
                + "\n\n"
                + "class PhotoHandler(AuditMixin, BaseAuth):\n"
                + self._LOAD
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_diamond_mro_prefers_second_branch_over_shared_base(self):
        # C3 MRO of D is D, A, B, BaseAuth: B defines get_uid (non-identity)
        # and wins over BaseAuth. A naive depth-first walk would reach
        # BaseAuth first and bind -- this test discriminates C3 from DFS.
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class A(BaseAuth):\n"
                "    pass\n"
                "\n\n"
                + "class B(BaseAuth):\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
                + "\n\n"
                + "class D(A, B):\n"
                + self._LOAD
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_diamond_mro_falls_through_to_shared_base(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class A(BaseAuth):\n"
                "    pass\n"
                "\n\n"
                + "class B(BaseAuth):\n"
                "    pass\n"
                "\n\n"
                + "class D(A, B):\n"
                + self._LOAD
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_super_two_arg_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth):\n"
                "    def load(self, req):\n"
                "        uid = super(PhotoHandler, self).get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_super_two_arg_skips_named_base(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                "class BaseAuth:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
                "\n\n"
                "class AuditMixin:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n\n"
                + "class PhotoHandler(BaseAuth, AuditMixin):\n"
                "    def load(self, req):\n"
                "        uid = super(BaseAuth, self).get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        # super(BaseAuth, self) skips BaseAuth; AuditMixin defines an
        # identity get_uid, so the binding comes from the mixin.
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuditMixin.get_uid")

    def test_super_two_arg_unknown_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth):\n"
                "    def load(self, req):\n"
                "        uid = super(Unknown, self).get_uid(req)\n"
                '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_super_two_arg_delegation_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + self._MIXIN
                + "\n\n"
                + "class AlbumHandler(BaseAuth, AuditMixin):\n"
                "    def get_uid(self, req):\n"
                "        return super(AlbumHandler, self).get_uid(req)\n"
                "\n"
                "    def load(self, req):\n"
                "        uid = self.get_uid(req)\n"
                '        return db.query("SELECT * FROM albums WHERE owner_id = ?", [uid])\n'
            )
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 13, 16, 17])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "AlbumHandler.get_uid")

    def test_multiple_inheritance_unresolvable_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth, ExternalMixin):\n"
                + self._LOAD
            )
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cross_file_multiple_inheritance_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._BASE,
            "handlers.py": (
                "from auth import BaseAuth\n"
                "\n\n"
                + self._MIXIN
                + "\n\n"
                + "class PhotoHandler(BaseAuth, AuditMixin):\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")


class IdentityAttributeBaseReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _BASE = (
        "class BaseAuth:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    _MIXIN = (
        "class AuditMixin:\n"
        "    def get_uid(self, req):\n"
        "        return req.session.id\n"
    )

    _LOAD = (
        "    def load(self, req):\n"
        "        uid = self.get_uid(req)\n"
        '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
    )

    def test_plain_module_import_attribute_base_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._BASE,
            "handlers.py": (
                "import auth\n"
                "\n\n"
                "class PhotoHandler(auth.BaseAuth):\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_aliased_module_import_attribute_base_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._BASE,
            "handlers.py": (
                "import auth as a\n"
                "\n\n"
                "class PhotoHandler(a.BaseAuth):\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_relative_module_import_attribute_base_binds(self):
        rows = extract_identity_evidence({
            "pkg/__init__.py": "",
            "pkg/auth.py": self._BASE,
            "pkg/handlers.py": (
                "from . import auth\n"
                "\n\n"
                "class PhotoHandler(auth.BaseAuth):\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "pkg/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_from_package_import_attribute_base_binds(self):
        rows = extract_identity_evidence({
            "pkg/__init__.py": "",
            "pkg/auth.py": self._BASE,
            "pkg/handlers.py": (
                "from pkg import auth\n"
                "\n\n"
                "class PhotoHandler(auth.BaseAuth):\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "pkg/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_attribute_base_without_module_import_does_not_bind(self):
        rows = extract_identity_evidence({
            "auth.py": self._BASE,
            "handlers.py": (
                "from auth import BaseAuth\n"
                "\n\n"
                "class PhotoHandler(auth.BaseAuth):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_attribute_base_not_a_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "auth.py": "NOT_A_CLASS = 1\n",
            "handlers.py": (
                "import auth\n"
                "\n\n"
                "class PhotoHandler(auth.NotAClass):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_attribute_base_missing_module_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                "import auth\n"
                "\n\n"
                "class PhotoHandler(auth.BaseAuth):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_three_segment_attribute_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "pkg/__init__.py": "",
            "pkg/auth.py": self._BASE,
            "pkg/handlers.py": (
                "import pkg.auth\n"
                "\n\n"
                "class PhotoHandler(pkg.auth.BaseAuth):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_attribute_base_mixed_with_bare_base_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._BASE,
            "handlers.py": (
                "import auth\n"
                "\n\n"
                + self._MIXIN
                + "\n\n"
                "class PhotoHandler(auth.BaseAuth, AuditMixin):\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 12)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 11, 12])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")

    def test_attribute_base_first_base_shadows_second(self):
        rows = extract_identity_evidence({
            "auth.py": (
                "class AuditMixin:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
                "\n\n"
                + self._BASE
            ),
            "handlers.py": (
                "import auth\n"
                "\n\n"
                "class PhotoHandler(auth.AuditMixin, auth.BaseAuth):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_attribute_base_chain_binds_through_parent(self):
        rows = extract_identity_evidence({
            "auth.py": (
                self._BASE
                + "\n\n"
                + "class ExtendedAuth(BaseAuth):\n"
                "    pass\n"
            ),
            "handlers.py": (
                "import auth\n"
                "\n\n"
                "class PhotoHandler(auth.ExtendedAuth):\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")

    def test_shadowed_module_name_attribute_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "auth.py": self._BASE,
            "handlers.py": (
                "import auth\n"
                "\n\n"
                "class auth:\n"
                "    pass\n"
                "\n\n"
                "class PhotoHandler(auth.BaseAuth):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class IdentityExpressionBaseFailClosedTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _BASE = (
        "class BaseAuth:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    _MIXIN = (
        "class AuditMixin:\n"
        "    def get_uid(self, req):\n"
        "        return req.session.id\n"
    )

    _LOAD = (
        "    def load(self, req):\n"
        "        uid = self.get_uid(req)\n"
        '        return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid])\n'
    )

    def test_call_expression_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(mixin(BaseAuth)):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_called_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth()):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_subscript_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth[T]):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_conditional_expression_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + self._MIXIN
                + "\n\n"
                + "class PhotoHandler(BaseAuth if True else AuditMixin):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_attribute_call_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "auth.py": self._BASE,
            "handlers.py": (
                "import auth\n"
                "\n\n"
                "class PhotoHandler(auth.BaseAuth()):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_binary_op_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + self._MIXIN
                + "\n\n"
                + "class PhotoHandler(BaseAuth + AuditMixin):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_expression_base_poisons_whole_class(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + self._MIXIN
                + "\n\n"
                + "class PhotoHandler(BaseAuth, mixin(AuditMixin)):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_lambda_call_base_does_not_bind(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler((lambda: BaseAuth)()):\n"
                + self._LOAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_bare_base_control_still_binds(self):
        rows = extract_identity_evidence({
            "handlers.py": (
                self._BASE
                + "\n\n"
                + "class PhotoHandler(BaseAuth):\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")


class IdentityFactoryConstructedAttributeTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _HELPER = (
        "class AuthHelper:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    _OTHER = (
        "class OtherHelper:\n"
        "    def get_uid(self, req):\n"
        "        return req.session.id\n"
    )

    _HANDLER_HEAD = (
        "class PhotoHandler:\n"
        "    def __init__(self):\n"
        "        self.helper = make_auth_helper()\n"
        "\n"
        "    def list_photos(self, request):\n"
        "        uid = self.helper.get_uid(request)\n"
        '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
    )

    def test_factory_unique_return_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + "def make_auth_helper():\n"
                "    return AuthHelper()\n"
                "\n"
                + self._HANDLER_HEAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 14)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 13, 14])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:12")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_factory_return_with_arguments_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + "def make_auth_helper():\n"
                "    return AuthHelper('secret')\n"
                "\n"
                + self._HANDLER_HEAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_factory_helper_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                "class AuthHelper:\n"
                "    def get_owner(self, req):\n"
                "        return req.body.user_id\n"
                "\n"
                "def make_auth_helper():\n"
                "    return AuthHelper()\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")

    def test_factory_binding_outside_init_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + "def make_auth_helper():\n"
                "    return AuthHelper()\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        pass\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 17)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 16, 17])

    def test_factory_mixed_return_with_none_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + "def make_auth_helper(flag):\n"
                "    if flag:\n"
                "        return AuthHelper()\n"
                "    return None\n"
                "\n"
                + self._HANDLER_HEAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_factory_mixed_return_two_classes_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + self._OTHER
                + "\n"
                + "def make_auth_helper(flag):\n"
                "    if flag:\n"
                "        return AuthHelper()\n"
                "    return OtherHelper()\n"
                "\n"
                + self._HANDLER_HEAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_factory_without_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + "def make_auth_helper():\n"
                "    pass\n"
                "\n"
                + self._HANDLER_HEAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_factory_variable_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + "def make_auth_helper():\n"
                "    h = AuthHelper()\n"
                "    return h\n"
                "\n"
                + self._HANDLER_HEAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_async_factory_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + "async def make_auth_helper():\n"
                "    return AuthHelper()\n"
                "\n"
                + self._HANDLER_HEAD
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_factory_and_direct_different_classes_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + self._OTHER
                + "\n"
                + "def make_auth_helper():\n"
                "    return AuthHelper()\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = OtherHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_factory_and_direct_same_class_binds(self):
        rows = extract_identity_evidence({
            "server/photos.py": (
                self._HELPER
                + "\n"
                + "def make_auth_helper():\n"
                "    return AuthHelper()\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 17)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 16, 17])

    def test_cross_file_factory_from_unscanned_module_does_not_bind(self):
        # Boundary moved in P16: a cross-file factory whose class is
        # defined in the factory's own file now binds (see
        # IdentityCrossFileFactoryTests.test_cross_file_factory_binds);
        # this test now pins the next boundary, a factory imported
        # from a module outside the scan.
        rows = extract_identity_evidence({
            "server/photos.py": (
                "from external.utils import make_auth_helper\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class IdentityCrossFileInstanceAttributeTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _AUTH = (
        "class AuthHelper:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    def _photos(self, import_line, assign="self.helper = AuthHelper()"):
        return (
            import_line + "\n\n"
            "class PhotoHandler:\n"
            "    def __init__(self):\n"
            f"        {assign}\n"
            "\n"
            "    def list_photos(self, request):\n"
            "        uid = self.helper.get_uid(request)\n"
            '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
        )

    def test_from_import_class_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": self._photos("from server.auth import AuthHelper"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_cross_file_helper_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": (
                "class AuthHelper:\n"
                "    def get_owner(self, req):\n"
                "        return req.body.user_id\n"
            ),
            "server/photos.py": (
                "from server.auth import AuthHelper\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")

    def test_aliased_from_import_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": self._photos(
                "from server.auth import AuthHelper as AH", "self.helper = AH()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")

    def test_relative_from_import_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": self._photos("from .auth import AuthHelper"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")

    def test_cross_file_inherited_method_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": (
                "class BaseAuth:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/auth.py": (
                "from server.base import BaseAuth\n"
                "\n"
                "class AuthHelper(BaseAuth):\n"
                "    pass\n"
            ),
            "server/photos.py": self._photos("from server.auth import AuthHelper"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/base.py")

    def test_cross_file_super_delegation_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": (
                "class BaseAuth:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "class AuthHelper(BaseAuth):\n"
                "    def get_uid(self, req):\n"
                "        return super().get_uid(req)\n"
            ),
            "server/photos.py": self._photos("from server.auth import AuthHelper"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(
            [item["function"] for item in binding["details"]["propagation"][:2]],
            ["BaseAuth.get_uid", "AuthHelper.get_uid"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 7, 8, 9])

    def test_imported_name_not_a_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": (
                "def AuthHelper(req):\n"
                "    return req.user.id\n"
            ),
            "server/photos.py": self._photos("from server.auth import AuthHelper"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_imported_module_not_in_project_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/photos.py": self._photos("from server.missing import AuthHelper"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_same_local_name_from_two_modules_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/other.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
            ),
            "server/photos.py": self._photos(
                "from server.auth import AuthHelper\nfrom server.other import AuthHelper"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_same_file_class_shadows_import(self):
        rows = extract_identity_evidence({
            "server/auth.py": (
                "class AuthHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
            ),
            "server/photos.py": (
                "from server.auth import AuthHelper\n"
                "\n"
                + self._AUTH
                + "\n"
                + self._photos("from server.auth import AuthHelper").split("\n\n", 1)[1]
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/photos.py")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [5, 12, 13])

    def test_reexported_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/utils.py": "from server.auth import AuthHelper\n",
            "server/photos.py": self._photos("from server.utils import AuthHelper"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cross_file_binding_outside_init_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": (
                "from server.auth import AuthHelper\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        pass\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 12)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 11, 12])

    def test_cross_file_and_direct_different_classes_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": (
                "from server.auth import AuthHelper\n"
                "\n"
                "class OtherHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = OtherHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class IdentityModuleAttributeConstructionTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _AUTH = (
        "class AuthHelper:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    def _photos(self, import_line, assign="self.helper = auth.AuthHelper()"):
        return (
            import_line + "\n\n"
            "class PhotoHandler:\n"
            "    def __init__(self):\n"
            f"        {assign}\n"
            "\n"
            "    def list_photos(self, request):\n"
            "        uid = self.helper.get_uid(request)\n"
            '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
        )

    def test_plain_module_import_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._AUTH,
            "server/photos.py": self._photos("import auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_from_package_import_module_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": self._photos("from server import auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_dotted_module_import_with_alias_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": self._photos("import server.auth as auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_relative_module_import_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": self._photos("from . import auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_aliased_module_import_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._AUTH,
            "server/photos.py": self._photos(
                "import auth as a", "self.helper = a.AuthHelper()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_module_attr_inherited_method_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": (
                "class BaseAuth:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/auth.py": (
                "from server.base import BaseAuth\n"
                "\n"
                "class AuthHelper(BaseAuth):\n"
                "    pass\n"
            ),
            "server/photos.py": self._photos("from server import auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/base.py")

    def test_module_attr_helper_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "auth.py": (
                "class AuthHelper:\n"
                "    def get_owner(self, req):\n"
                "        return req.body.user_id\n"
            ),
            "server/photos.py": (
                "import auth\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = auth.AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")

    def test_three_segment_construction_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": self._photos(
                "import server.auth", "self.helper = server.auth.AuthHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_module_attr_target_not_a_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "auth.py": (
                "def AuthHelper(req):\n"
                "    return req.user.id\n"
            ),
            "server/photos.py": self._photos("import auth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_shadowed_module_name_does_not_bind(self):
        rows = extract_identity_evidence({
            "auth.py": self._AUTH,
            "server/photos.py": self._photos("import auth\n\nauth = 5"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_reexported_class_via_module_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/base.py": self._AUTH,
            "server/auth.py": "from server.base import AuthHelper\n",
            "server/photos.py": self._photos("from server import auth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_module_attr_and_direct_different_classes_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": (
                "from server import auth\n"
                "\n"
                "class OtherHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = auth.AuthHelper()\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = OtherHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_module_attr_factory_from_unscanned_module_does_not_bind(self):
        # Boundary moved in P17: a module-attribute factory whose
        # class is defined in the factory's own file now binds (see
        # IdentityModuleAttributeFactoryTests.test_module_attr_factory_binds);
        # this test now pins the next boundary, a factory accessed
        # through a module outside the scan.
        rows = extract_identity_evidence({
            "server/photos.py": self._photos(
                "import external", "self.helper = external.make_auth_helper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_class_import_only_module_attr_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/photos.py": self._photos(
                "from server.auth import AuthHelper", "self.helper = auth.AuthHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class IdentityCrossFileFactoryTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _AUTH = (
        "class AuthHelper:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    _FACTORY = (
        "def make_auth_helper():\n"
        "    return AuthHelper()\n"
    )

    def _utils(self, factory=_FACTORY, helper=_AUTH):
        return helper + "\n" + factory

    def _photos(self, import_line, assign="self.helper = make_auth_helper()"):
        return (
            import_line + "\n\n"
            "class PhotoHandler:\n"
            "    def __init__(self):\n"
            f"        {assign}\n"
            "\n"
            "    def list_photos(self, request):\n"
            "        uid = self.helper.get_uid(request)\n"
            '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
        )

    def test_cross_file_factory_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/photos.py": self._photos("from server.utils import make_auth_helper"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_cross_file_factory_with_arguments_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(
                "def make_auth_helper():\n    return AuthHelper('secret')\n"),
            "server/photos.py": self._photos("from server.utils import make_auth_helper"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")

    def test_aliased_factory_import_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/photos.py": self._photos(
                "from server.utils import make_auth_helper as make",
                "self.helper = make()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")

    def test_relative_factory_import_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/photos.py": self._photos("from .utils import make_auth_helper"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")

    def test_cross_file_factory_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(
                helper=(
                    "class AuthHelper:\n"
                    "    def get_owner(self, req):\n"
                    "        return req.body.user_id\n"
                )),
            "server/photos.py": (
                "from server.utils import make_auth_helper\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")

    def test_cross_file_factory_mixed_return_with_none_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(
                "def make_auth_helper(flag):\n"
                "    if flag:\n"
                "        return AuthHelper()\n"
                "    return None\n"),
            "server/photos.py": self._photos(
                "from server.utils import make_auth_helper",
                "self.helper = make_auth_helper(True)"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cross_file_factory_variable_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(
                "def make_auth_helper():\n"
                "    h = AuthHelper()\n"
                "    return h\n"),
            "server/photos.py": self._photos("from server.utils import make_auth_helper"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cross_file_factory_mixed_return_two_classes_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(
                "def make_auth_helper(flag):\n"
                "    if flag:\n"
                "        return AuthHelper()\n"
                "    return OtherHelper()\n",
                helper=(
                    self._AUTH
                    + "\n"
                    + "class OtherHelper:\n"
                    "    def get_uid(self, req):\n"
                    "        return req.session.id\n"
                )),
            "server/photos.py": self._photos(
                "from server.utils import make_auth_helper",
                "self.helper = make_auth_helper(True)"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cross_file_async_factory_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(
                "async def make_auth_helper():\n    return AuthHelper()\n"),
            "server/photos.py": self._photos("from server.utils import make_auth_helper"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_local_factory_shadows_import(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(
                helper=(
                    "class AuthHelper:\n"
                    "    def get_uid(self, req):\n"
                    "        return req.body.user_id\n"
                )),
            "server/photos.py": (
                "from server.utils import make_auth_helper\n"
                "\n"
                "class LocalHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.user.id\n"
                "\n"
                "def make_auth_helper():\n"
                "    return LocalHelper()\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/photos.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "LocalHelper.get_uid")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [5, 15, 16])

    def test_reexported_factory_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/mid.py": "from server.utils import make_auth_helper\n",
            "server/photos.py": self._photos("from server.mid import make_auth_helper"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_factory_returning_imported_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._AUTH,
            "server/utils.py": "from server.auth import AuthHelper\n\n" + self._FACTORY,
            "server/photos.py": self._photos("from server.utils import make_auth_helper"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cross_file_factory_and_direct_different_classes_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/photos.py": (
                "from server.utils import make_auth_helper\n"
                "\n"
                + self._AUTH
                + "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class IdentityModuleAttributeFactoryTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _AUTH = (
        "class AuthHelper:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    _FACTORY = (
        "def make_auth_helper():\n"
        "    return AuthHelper()\n"
    )

    def _auth(self, factory=_FACTORY, helper=_AUTH):
        return helper + "\n" + factory

    def _photos(self, import_line, assign="self.helper = auth.make_auth_helper()"):
        return (
            import_line + "\n\n"
            "class PhotoHandler:\n"
            "    def __init__(self):\n"
            f"        {assign}\n"
            "\n"
            "    def list_photos(self, request):\n"
            "        uid = self.helper.get_uid(request)\n"
            '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
        )

    def test_module_attr_factory_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._auth(),
            "server/photos.py": self._photos("import auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_from_package_import_module_factory_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(),
            "server/photos.py": self._photos("from server import auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_dotted_module_import_with_alias_factory_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(),
            "server/photos.py": self._photos("import server.auth as auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_aliased_module_factory_binds(self):
        rows = extract_identity_evidence({
            "auth.py": self._auth(),
            "server/photos.py": self._photos(
                "import auth as a", "self.helper = a.make_auth_helper()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_relative_module_factory_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(),
            "server/photos.py": self._photos("from . import auth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_module_attr_factory_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(
                helper=(
                    "class AuthHelper:\n"
                    "    def get_owner(self, req):\n"
                    "        return req.body.user_id\n"
                )),
            "server/photos.py": (
                "from server import auth\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = auth.make_auth_helper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")

    def test_module_attr_factory_returning_imported_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/base.py": self._AUTH,
            "server/auth.py": "from server.base import AuthHelper\n\n" + self._FACTORY,
            "server/photos.py": self._photos("from server import auth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_module_attr_factory_mixed_return_with_none_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(
                "def make_auth_helper(flag):\n"
                "    if flag:\n"
                "        return AuthHelper()\n"
                "    return None\n"),
            "server/photos.py": self._photos(
                "from server import auth",
                "self.helper = auth.make_auth_helper(True)"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_module_attr_factory_variable_return_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(
                "def make_auth_helper():\n"
                "    h = AuthHelper()\n"
                "    return h\n"),
            "server/photos.py": self._photos("from server import auth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_three_segment_factory_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(),
            "server/photos.py": self._photos(
                "import server.auth", "self.helper = server.auth.make_auth_helper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_reexported_factory_via_module_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(),
            "server/mid.py": "from server.auth import make_auth_helper\n",
            "server/photos.py": self._photos(
                "from server import mid", "self.helper = mid.make_auth_helper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_shadowed_module_name_factory_does_not_bind(self):
        rows = extract_identity_evidence({
            "auth.py": self._auth(),
            "server/photos.py": self._photos("import auth\n\nauth = 5"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_module_attr_factory_and_local_direct_class_does_not_bind(self):
        # Boundary moved in P18: a module-attribute factory mixed with
        # a module-attribute direct construction of the same class now
        # binds (see
        # IdentityMultiNameSameTargetTests.test_dotted_factory_and_dotted_direct_mixed_binds);
        # this test now pins the next boundary, a factory mixed with a
        # direct construction of a different class defined in this file.
        rows = extract_identity_evidence({
            "server/auth.py": self._auth(),
            "server/photos.py": (
                "from server import auth\n"
                "\n"
                + self._AUTH
                + "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = auth.make_auth_helper()\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class IdentityMultiNameSameTargetTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _AUTH = (
        "class AuthHelper:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    _FACTORY = (
        "def make_auth_helper():\n"
        "    return AuthHelper()\n"
    )

    def _utils(self, factory=_FACTORY, helper=_AUTH):
        return helper + "\n" + factory

    def _photos(self, import_line, assign_init, assign_setup):
        return (
            import_line + "\n\n"
            "class PhotoHandler:\n"
            "    def __init__(self):\n"
            f"        {assign_init}\n"
            "\n"
            "    def setup(self):\n"
            f"        {assign_setup}\n"
            "\n"
            "    def list_photos(self, request):\n"
            "        uid = self.helper.get_uid(request)\n"
            '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
        )

    def test_bare_factory_and_bare_class_mixed_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/photos.py": self._photos(
                "from server.utils import AuthHelper, make_auth_helper",
                "self.helper = make_auth_helper()",
                "self.helper = AuthHelper()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 12)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 11, 12])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:10")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_dotted_factory_and_dotted_direct_mixed_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._utils(),
            "server/photos.py": self._photos(
                "from server import auth",
                "self.helper = auth.make_auth_helper()",
                "self.helper = auth.AuthHelper()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_dotted_factory_and_bare_class_mixed_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._utils(),
            "server/photos.py": self._photos(
                "from server import auth\nfrom server.auth import AuthHelper",
                "self.helper = auth.make_auth_helper()",
                "self.helper = AuthHelper()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_bare_factory_and_dotted_direct_mixed_binds(self):
        rows = extract_identity_evidence({
            "server/auth.py": self._utils(),
            "server/photos.py": self._photos(
                "from server import auth\nfrom server.auth import make_auth_helper",
                "self.helper = make_auth_helper()",
                "self.helper = auth.AuthHelper()"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/auth.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")

    def test_mixed_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(
                helper=(
                    "class AuthHelper:\n"
                    "    def get_owner(self, req):\n"
                    "        return req.body.user_id\n"
                )),
            "server/photos.py": (
                "from server.utils import AuthHelper, make_auth_helper\n"
                "\n"
                "class PhotoHandler:\n"
                "    def __init__(self):\n"
                "        self.helper = make_auth_helper()\n"
                "\n"
                "    def setup(self):\n"
                "        self.helper = AuthHelper()\n"
                "\n"
                "    def list_photos(self, request):\n"
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_owner")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/utils.py")

    def test_mixed_factory_and_local_direct_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/photos.py": self._photos(
                "from server.utils import make_auth_helper\n\n" + self._AUTH,
                "self.helper = make_auth_helper()",
                "self.helper = AuthHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_factory_and_other_module_direct_class_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/other.py": (
                "class OtherHelper:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
            ),
            "server/photos.py": self._photos(
                "from server.utils import make_auth_helper\n"
                "from server.other import OtherHelper",
                "self.helper = make_auth_helper()",
                "self.helper = OtherHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_with_non_construction_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/photos.py": self._photos(
                "from server.utils import make_auth_helper",
                "self.helper = make_auth_helper()",
                "self.helper = 5"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_with_unresolvable_name_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/utils.py": self._utils(),
            "server/photos.py": self._photos(
                "from server.utils import make_auth_helper\nimport external",
                "self.helper = make_auth_helper()",
                "self.helper = external.AuthHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mixed_with_ambiguous_import_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/a.py": self._utils(),
            "server/b.py": self._utils(),
            "server/photos.py": self._photos(
                "from server.a import make_auth_helper\n"
                "from server.b import make_auth_helper\n"
                "from server.a import AuthHelper",
                "self.helper = make_auth_helper()",
                "self.helper = AuthHelper()"),
        })
        self.assertEqual(self._direct_bindings(rows), [])


class IdentitySecondHopReturnPropagationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _BASE = (
        "class BaseAuth:\n"
        "    def get_uid(self, req):\n"
        "        return req.user.id\n"
    )

    _BASE_OWNER = (
        "class BaseAuth:\n"
        "    def get_owner(self, req):\n"
        "        return req.body.user_id\n"
    )

    def _mid(self, import_block, assign_inner, body, method="get_uid"):
        return (
            import_block + "\n\n"
            "class MidAuth:\n"
            "    def __init__(self):\n"
            f"        {assign_inner}\n"
            "\n"
            f"    def {method}(self, req):\n"
            f"        {body}\n"
        )

    def _photos(self, import_block, assign_helper="self.helper = MidAuth()", tail="uid"):
        body = (
            import_block + "\n\n"
            "class PhotoHandler:\n"
            "    def __init__(self):\n"
            f"        {assign_helper}\n"
            "\n"
            "    def list_photos(self, request):\n"
        )
        if tail == "uid":
            body += (
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        else:
            body += (
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            )
        return body

    def test_cross_file_second_hop_via_instance_attribute_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE,
            "server/mid.py": self._mid(
                "from server.base import BaseAuth",
                "self.inner = BaseAuth()",
                "return self.inner.get_uid(req)"),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/base.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["details"]["propagation"][1]["path"], "server/mid.py")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_same_file_second_hop_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE,
            "server/photos.py": (
                self._mid("from server.base import BaseAuth",
                          "self.inner = BaseAuth()",
                          "return self.inner.get_uid(req)") + "\n"
                + self._photos("", "self.helper = MidAuth()")
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 17, 18])
        self.assertEqual(binding["details"]["propagation"][1]["path"], "server/photos.py")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth.get_uid")

    def test_second_hop_via_module_attribute_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE,
            "server/mid.py": self._mid(
                "from server import base",
                "self.inner = base.BaseAuth()",
                "return self.inner.get_uid(req)"),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][1]["path"], "server/mid.py")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth.get_uid")

    def test_second_hop_via_factory_attribute_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE + "\n\ndef make_base_auth():\n    return BaseAuth()\n",
            "server/mid.py": self._mid(
                "from server.base import make_base_auth",
                "self.inner = make_base_auth()",
                "return self.inner.get_uid(req)"),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][1]["path"], "server/mid.py")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth.get_uid")

    def test_second_hop_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE_OWNER,
            "server/mid.py": self._mid(
                "from server.base import BaseAuth",
                "self.inner = BaseAuth()",
                "return self.inner.get_owner(req)",
                method="get_owner"),
            "server/photos.py": self._photos("from server.mid import MidAuth", tail="owner"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth.get_owner")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth.get_owner")

    def test_intermediate_mixed_direct_and_delegation_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE,
            "server/mid.py": (
                "from server.base import BaseAuth\n\n"
                "class MidAuth:\n"
                "    def __init__(self):\n"
                "        self.inner = BaseAuth()\n\n"
                "    def get_uid(self, req):\n"
                "        if req is None:\n"
                "            return req.user.id\n"
                "        return self.inner.get_uid(req)\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_second_hop_attribute_two_classes_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE,
            "server/other.py": (
                "class OtherAuth:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.id\n"
            ),
            "server/mid.py": (
                "from server.base import BaseAuth\n"
                "from server.other import OtherAuth\n\n"
                "class MidAuth:\n"
                "    def __init__(self):\n"
                "        self.inner = BaseAuth()\n\n"
                "    def setup(self):\n"
                "        self.inner = OtherAuth()\n\n"
                "    def get_uid(self, req):\n"
                "        return self.inner.get_uid(req)\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_second_hop_attribute_non_construction_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE,
            "server/mid.py": self._mid(
                "from server.base import BaseAuth",
                "self.inner = 5",
                "return self.inner.get_uid(req)"),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_second_hop_target_not_identity_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/base.py": (
                "class BaseAuth:\n"
                "    def get_uid(self, req):\n"
                "        return req.session.token\n"
            ),
            "server/mid.py": self._mid(
                "from server.base import BaseAuth",
                "self.inner = BaseAuth()",
                "return self.inner.get_uid(req)"),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_intermediate_two_delegated_returns_to_same_target_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": self._BASE,
            "server/mid.py": (
                "from server.base import BaseAuth\n\n"
                "class MidAuth:\n"
                "    def __init__(self):\n"
                "        self.inner = BaseAuth()\n\n"
                "    def get_uid(self, req):\n"
                "        if req is None:\n"
                "            return self.inner.get_uid(req)\n"
                "        return self.inner.get_uid(req)\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 9, 10, 8, 9])
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth.get_uid")
        self.assertEqual(binding["details"]["propagation"][2]["function"], "MidAuth.get_uid")


class IdentitySelfMethodDelegationTests(unittest.TestCase):
    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    def _photos(self, import_block, assign_helper="self.helper = MidAuth()", tail="uid"):
        body = (
            import_block + "\n\n"
            "class PhotoHandler:\n"
            "    def __init__(self):\n"
            f"        {assign_helper}\n"
            "\n"
            "    def list_photos(self, request):\n"
        )
        if tail == "uid":
            body += (
                "        uid = self.helper.get_uid(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (uid,))\n'
            )
        else:
            body += (
                "        owner = self.helper.get_owner(request)\n"
                '        cursor.execute("SELECT id FROM photos WHERE owner_id = %s", (owner,))\n'
            )
        return body

    def test_self_method_delegation_binds(self):
        rows = extract_identity_evidence({
            "server/mid.py": (
                "class MidAuth:\n"
                "    def get_uid(self, req):\n"
                "        return self._get_uid(req)\n"
                "\n"
                "    def _get_uid(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [6, 3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/mid.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "MidAuth._get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["returns"], "req.user.id")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:server/photos.py#list_photos:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_self_method_delegation_to_base_class_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": (
                "class BaseAuth:\n"
                "    def _get_uid(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/mid.py": (
                "from server.base import BaseAuth\n\n"
                "class MidAuth(BaseAuth):\n"
                "    def get_uid(self, req):\n"
                "        return self._get_uid(req)\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 5, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "server/base.py")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth._get_uid")

    def test_self_method_delegation_returning_request_identity_binds(self):
        rows = extract_identity_evidence({
            "server/mid.py": (
                "class MidAuth:\n"
                "    def get_owner(self, req):\n"
                "        return self._get_owner(req)\n"
                "\n"
                "    def _get_owner(self, req):\n"
                "        return req.body.user_id\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth", tail="owner"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "MidAuth._get_owner")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth.get_owner")

    def test_two_step_self_method_delegation_binds(self):
        rows = extract_identity_evidence({
            "server/mid.py": (
                "class MidAuth:\n"
                "    def get_uid(self, req):\n"
                "        return self._a(req)\n"
                "\n"
                "    def _a(self, req):\n"
                "        return self._b(req)\n"
                "\n"
                "    def _b(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [9, 6, 3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "MidAuth._b")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth._a")

    def test_target_method_mixed_returns_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/mid.py": (
                "class MidAuth:\n"
                "    def get_uid(self, req):\n"
                "        return self._get_uid(req)\n"
                "\n"
                "    def _get_uid(self, req):\n"
                "        if req is None:\n"
                "            return req.user.id\n"
                "        return req.session.id\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_target_method_missing_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/mid.py": (
                "class MidAuth:\n"
                "    def get_uid(self, req):\n"
                "        return self._missing(req)\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_direct_and_self_delegation_mixed_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/mid.py": (
                "class MidAuth:\n"
                "    def get_uid(self, req):\n"
                "        if req is None:\n"
                "            return req.user.id\n"
                "        return self._get_uid(req)\n"
                "\n"
                "    def _get_uid(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_self_method_delegation_cycle_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/mid.py": (
                "class MidAuth:\n"
                "    def get_uid(self, req):\n"
                "        return self._get_uid(req)\n"
                "\n"
                "    def _get_uid(self, req):\n"
                "        return self.get_uid(req)\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_cls_method_delegation_does_not_bind(self):
        rows = extract_identity_evidence({
            "server/mid.py": (
                "class MidAuth:\n"
                "    def get_uid(self, req):\n"
                "        return cls._get_uid(req)\n"
                "\n"
                "    def _get_uid(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_self_method_delegation_to_super_delegated_method_binds(self):
        rows = extract_identity_evidence({
            "server/base.py": (
                "class BaseAuth:\n"
                "    def _get_uid(self, req):\n"
                "        return req.user.id\n"
            ),
            "server/mid.py": (
                "from server.base import BaseAuth\n\n"
                "class MidAuth(BaseAuth):\n"
                "    def get_uid(self, req):\n"
                "        return self._get_uid(req)\n"
                "\n"
                "    def _get_uid(self, req):\n"
                "        return super()._get_uid(req)\n"
            ),
            "server/photos.py": self._photos("from server.mid import MidAuth"),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 5, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "BaseAuth._get_uid")
        self.assertEqual(binding["details"]["propagation"][1]["function"], "MidAuth._get_uid")


class TsConfigPathsImportTests(unittest.TestCase):
    def _rows_files(self, files, ts_config=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            if ts_config is not None:
                (root / "tsconfig.json").write_text(ts_config, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners, ts_config=ts_config)

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _AUTH_CLASS = (
        "export class AuthHelper {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "}\n"
    )

    _HANDLER_ALIASED = (
        'import { AuthHelper } from "@/auth";\n'
        "\n"
        "export class PhotoHandler {\n"
        "  constructor() {\n"
        "    this.helper = new AuthHelper();\n"
        "  }\n"
        "  load(req: any) {\n"
        "    const uid = this.helper.get_uid(req);\n"
        '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
        "  }\n"
        "}\n"
    )

    _ALIASED = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n'

    def test_aliased_class_helper_binds(self):
        rows = self._rows_files(
            {"src/auth.ts": self._AUTH_CLASS, "src/handlers.ts": self._HANDLER_ALIASED},
            ts_config=self._ALIASED)
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:src/handlers.ts#load:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_exact_pattern_binds(self):
        rows = self._rows_files(
            {"src/auth.ts": self._AUTH_CLASS,
             "src/handlers.ts": self._HANDLER_ALIASED.replace('"@/auth"', '"@auth"')},
            ts_config='{"compilerOptions": {"paths": {"@auth": ["src/auth.ts"]}}}\n')
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")

    def test_base_url_relative_targets_bind(self):
        rows = self._rows_files(
            {"src/auth.ts": self._AUTH_CLASS, "src/handlers.ts": self._HANDLER_ALIASED},
            ts_config='{"compilerOptions": {"baseUrl": "src", "paths": {"@/*": ["*"]}}}\n')
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")

    def test_aliased_top_level_function_binds(self):
        rows = self._rows_files(
            {"src/auth.ts": (
                "export function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
             "src/handlers.ts": (
                'import { get_uid } from "@/auth";\n'
                "\n"
                "export function load(req: any) {\n"
                "  const uid = get_uid(req);\n"
                '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
            )},
            ts_config=self._ALIASED)
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 4, 5])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:src/handlers.ts#load:3")

    def test_aliased_namespace_import_binds(self):
        rows = self._rows_files(
            {"src/auth.ts": (
                "export function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
             "src/handlers.ts": (
                'import * as auth from "@/auth";\n'
                "\n"
                "export function load(req: any) {\n"
                "  const uid = auth.get_uid(req);\n"
                '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
            )},
            ts_config=self._ALIASED)
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 4, 5])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")

    def test_aliased_base_class_binds(self):
        rows = self._rows_files(
            {"src/base.ts": (
                "export class Base {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
             "src/handlers.ts": (
                'import { Base } from "@/base";\n'
                "\n"
                "export class PhotoHandler extends Base {\n"
                "  load(req: any) {\n"
                "    const uid = this.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            )},
            ts_config=self._ALIASED)
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 6)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 5, 6])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/base.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "Base.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:src/handlers.ts#load:4")

    def test_first_target_missing_falls_through_to_second(self):
        rows = self._rows_files(
            {"src/auth.ts": self._AUTH_CLASS, "src/handlers.ts": self._HANDLER_ALIASED},
            ts_config='{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["no/such/*", "src/*"]}}}\n')
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")

    def test_bare_package_without_mapping_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH_CLASS,
            "handlers.ts": (
                'import { AuthHelper } from "@acme/auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_mapped_target_missing_does_not_bind(self):
        rows = self._rows_files(
            {"src/auth.ts": self._AUTH_CLASS, "src/handlers.ts": self._HANDLER_ALIASED},
            ts_config='{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/no-such/*"]}}}\n')
        self.assertEqual(self._direct_bindings(rows), [])

    def test_commented_tsconfig_binds(self):
        rows = self._rows_files(
            {"src/auth.ts": self._AUTH_CLASS, "src/handlers.ts": self._HANDLER_ALIASED},
            ts_config=(
                "// project paths\n"
                '{"compilerOptions": {\n'
                '  "baseUrl": ".",\n'
                '  "paths": {"@/*": ["src/*"],},\n'
                "}}\n"
            ))
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")


class TsNamespaceImportReturnPropagationTests(unittest.TestCase):
    def _rows_files(self, files, ts_config=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            if ts_config is not None:
                (root / "tsconfig.json").write_text(ts_config, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners, ts_config=ts_config)

    def _rows_files_memory(self, files):
        # Memory mode, exactly like the scanner: a `.vue` file joins
        # through its line-padded `<script>` text (disk mode skips
        # `.vue` files and reports no symbols for them).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            js_ts_source = {}
            for name, source in files.items():
                if name.endswith(".vue"):
                    script = vue_script_for_adapter(source)
                    if script is not None:
                        js_ts_source[name] = script
                elif Path(name).suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                    js_ts_source[name] = source
            parsed = analyze_js_ts_project(root, [], js_ts_source=js_ts_source)
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners)

    def _rows(self, source, filename="handlers.ts"):
        return self._rows_files({filename: source})

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _AUTH = (
        "export function get_uid(req: any) {\n"
        "  return req.user.id;\n"
        "}\n"
    )

    _LOAD = (
        'import get_uid from "./auth";\n'
        "\n"
        "export function load(req: any) {\n"
        "  const uid = get_uid(req);\n"
        '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
        "}\n"
    )

    def test_same_file_top_level_function_binds(self):
        rows = self._rows(
            "export function get_uid(req: any) {\n"
            "  return req.user.id;\n"
            "}\n"
            "export function load(req: any) {\n"
            "  const uid = get_uid(req);\n"
            "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 6)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 5, 6])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "handlers.ts")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:4")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_same_file_top_level_function_request_identity_binds(self):
        rows = self._rows(
            "export function get_uid(req: any) {\n"
            "  return req.body.user_id;\n"
            "}\n"
            "export function load(req: any) {\n"
            "  const uid = get_uid(req);\n"
            "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_request_identity_to_sql_owner_binding"
                       and row["line"] == 6)
        self.assertEqual(binding["details"]["request_expressions"], ["req.body.user_id"])

    def test_top_level_function_mixed_returns_does_not_bind(self):
        rows = self._rows(
            "export function get_uid(req: any) {\n"
            "  if (req.user) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "  return req.body.user_id;\n"
            "}\n"
            "export function load(req: any) {\n"
            "  const uid = get_uid(req);\n"
            "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_namespace_import_binds(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": (
                "import * as auth from \"./auth\";\n"
                "export function load(req: any) {\n"
                "  const uid = auth.get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 4)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 3, 4])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:2")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_namespace_import_alias_binds(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": (
                "import * as a from \"./auth\";\n"
                "export function load(req: any) {\n"
                "  const uid = a.get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 4)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_namespace_import_non_identity_function_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": (
                "export function get_uid(req: any) {\n"
                "  return null;\n"
                "}\n"
            ),
            "handlers.ts": (
                "import * as auth from \"./auth\";\n"
                "export function load(req: any) {\n"
                "  const uid = auth.get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_namespace_import_package_name_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": (
                "import * as auth from \"@scope/auth\";\n"
                "export function load(req: any) {\n"
                "  const uid = auth.get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_named_import_top_level_function_binds(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": (
                "import { get_uid } from \"./auth\";\n"
                "export function load(req: any) {\n"
                "  const uid = get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 4)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 3, 4])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_named_import_alias_binds(self):
        rows = self._rows_files({
            "auth.ts": self._AUTH,
            "handlers.ts": (
                "import { get_uid as uid_of } from \"./auth\";\n"
                "export function load(req: any) {\n"
                "  const uid = uid_of(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 4)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_arrow_function_const_binds(self):
        # Boundary move (T8): out of scope for T6 (only `function`
        # declarations qualified), but T8 gives a const-bound arrow
        # function the same top-level profile, so it now binds.
        rows = self._rows(
            "export const get_uid = (req: any) => req.user.id;\n"
            "export function load(req: any) {\n"
            "  const uid = get_uid(req);\n"
            "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 4)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [1, 3, 4])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:2")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")
    def test_default_imported_function_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "handlers.ts": self._LOAD,
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 4, 5])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:3")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_default_imported_function_alias_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "handlers.ts": (
                'import uid_of from "./auth";\n'
                "export function load(req: any) {\n"
                "  const uid = uid_of(req);\n"
                '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_default_imported_async_function_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default async function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "handlers.ts": self._LOAD,
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_default_and_named_imports_coexist(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
                "export function other(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "handlers.ts": (
                'import get_uid, { other } from "./auth";\n'
                "export function load(req: any) {\n"
                "  const uid = get_uid(req);\n"
                "  const alt = other(req);\n"
                '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
            ),
        })
        bindings = self._direct_bindings(rows)
        self.assertEqual(len(bindings), 1)
        # The default part binds; the named part also resolves, but
        # only the uid sink is asserted here (the alt alias is unused).
        self.assertEqual(bindings[0]["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(bindings[0]["details"]["propagation"][0]["function"], "get_uid")

    def test_default_imported_function_via_tsconfig_paths_binds(self):
        rows = self._rows_files({
            "src/auth.ts": (
                "export default function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "src/handlers.ts": (
                'import get_uid from "@/auth";\n'
                "\n"
                "export function load(req: any) {\n"
                "  const uid = get_uid(req);\n"
                '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
            ),
        }, ts_config='{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n')
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "src/auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_default_imported_function_in_vue_binds(self):
        rows = self._rows_files_memory({
            "auth.ts": (
                "export default function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "App.vue": (
                "<template><div /></template>\n"
                "<script lang=\"ts\">\n"
                'import get_uid from "./auth";\n'
                "export default {\n"
                "  methods: {\n"
                "    load(req) {\n"
                "      const uid = get_uid(req);\n"
                "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "    }\n"
                "  }\n"
                "}\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 8)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 7, 8])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:6")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_default_imported_function_shadowed_by_local_binds_local(self):
        rows = self._rows_files({
            "auth.ts": (
                "export default function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "handlers.ts": (
                'import get_uid from "./auth";\n'
                "\n"
                "export function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
                "export function load(req: any) {\n"
                "  const uid = get_uid(req);\n"
                '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "handlers.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_reexported_identifier_default_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": (
                "const get_uid = (req: any) => req.user.id;\n"
                "export default get_uid;\n"
            ),
            "handlers.ts": self._LOAD,
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_anonymous_default_arrow_does_not_bind(self):
        rows = self._rows_files({
            "auth.ts": "export default (req: any) => req.user.id;\n",
            "handlers.ts": self._LOAD,
        })
        self.assertEqual(self._direct_bindings(rows), [])

    def test_default_imported_class_binds(self):
        # Boundary move (T11): out of scope for T10 (functions only),
        # but T11 gives a default-imported class the same cross-file
        # instance-attribute binding, so it now binds.
        rows = self._rows_files({
            "auth.ts": (
                "export default class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "handlers.ts": (
                'import AuthHelper from "./auth";\n'
                "\n"
                "export class PhotoHandler {\n"
                "  constructor() {\n"
                "    this.helper = new AuthHelper();\n"
                "  }\n"
                "  load(req: any) {\n"
                "    const uid = this.helper.get_uid(req);\n"
                '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "  }\n"
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 9)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 8, 9])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:7")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")


class TsClassFieldArrowReturnPropagationTests(unittest.TestCase):
    def _rows(self, source, filename="handlers.ts"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / filename).write_text(source, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            owners = ts_function_owners(parsed)
            return extract_identity_evidence({filename: source}, owners)

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _LOAD = (
        "  load(req: any) {\n"
        "    const uid = this.get_uid(req);\n"
        '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
        "  }\n"
    )

    def test_expression_body_binds(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  get_uid = (req: any) => req.user.id;\n"
            + self._LOAD +
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 4, 5])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "handlers.ts")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:3")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_block_body_binds(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  get_uid = (req: any) => {\n"
            "    return req.user.id;\n"
            "  };\n"
            + self._LOAD +
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_async_field_arrow_binds(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  get_uid = async (req: any) => req.user.id;\n"
            + self._LOAD +
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_bare_param_field_arrow_binds(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  get_uid = req => req.user.id;\n"
            + self._LOAD +
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_private_field_arrow_binds(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  private get_uid = (req: any) => req.user.id;\n"
            + self._LOAD +
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_readonly_field_arrow_binds(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  readonly get_uid = (req: any) => req.user.id;\n"
            + self._LOAD +
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "PhotoHandler.get_uid")

    def test_field_arrow_body_calling_helper_binds(self):
        rows = self._rows(
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export class PhotoHandler {\n"
            "  constructor() {\n"
            "    this.helper = new AuthHelper();\n"
            "  }\n"
            "  load2 = (req: any) => {\n"
            "    const uid = this.helper.get_uid(req);\n"
            '    return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
            "  };\n"
            "}\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 12)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 11, 12])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "handlers.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "AuthHelper.get_uid")
        # Boundary move (T13): the adapter now reports field arrows as
        # method symbols, so a sink inside a field-arrow body has a
        # resolved owner instead of UNKNOWN.
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load2:10")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_static_field_arrow_does_not_bind(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  static get_uid = (req: any) => req.user.id;\n"
            + self._LOAD +
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_multiline_expression_body_does_not_bind(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  get_uid = (req: any) =>\n"
            "    req.user.id;\n"
            + self._LOAD +
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_field_arrow_block_mixed_returns_do_not_bind(self):
        rows = self._rows(
            "export class PhotoHandler {\n"
            "  get_uid = (req: any) => {\n"
            "    if (req.user) {\n"
            "      return req.user.id;\n"
            "    }\n"
            "    return req.session.id;\n"
            "  };\n"
            + self._LOAD +
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_field_arrow_shadows_base_method(self):
        rows = self._rows(
            "export class BaseAuth {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export class PhotoHandler extends BaseAuth {\n"
            "  get_uid = (req: any) => req.session.id;\n"
            + self._LOAD +
            "}\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])


class TsConstFunctionReturnPropagationTests(unittest.TestCase):
    def _rows_files(self, files):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                (root / name).write_text(source, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners)

    def _rows(self, source, filename="handlers.ts"):
        return self._rows_files({filename: source})

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _LOAD = (
        "export function load(req: any) {\n"
        "  const uid = get_uid(req);\n"
        '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
        "}\n"
    )

    def test_arrow_const_expression_body_binds(self):
        rows = self._rows(
            "export const get_uid = (req: any) => req.user.id;\n"
            "\n"
            + self._LOAD
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [1, 4, 5])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "handlers.ts")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:3")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_arrow_const_block_body_binds(self):
        rows = self._rows(
            "export const get_uid = (req: any) => {\n"
            "  return req.user.id;\n"
            "};\n"
            "\n"
            + self._LOAD
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 6, 7])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_function_expression_const_binds(self):
        rows = self._rows(
            "const get_uid = function(req: any) {\n"
            "  return req.user.id;\n"
            "};\n"
            "\n"
            + self._LOAD
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 7)
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_async_arrow_const_binds(self):
        rows = self._rows(
            "export const get_uid = async (req: any) => req.user.id;\n"
            "\n"
            + self._LOAD
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_bare_param_arrow_const_binds(self):
        rows = self._rows(
            "const get_uid = req => req.user.id;\n"
            "\n"
            + self._LOAD
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_imported_arrow_const_binds(self):
        rows = self._rows_files({
            "auth.ts": "export const get_uid = (req: any) => req.user.id;\n",
            "handlers.ts": (
                'import { get_uid } from "./auth";\n'
                "\n"
                + self._LOAD
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [1, 4, 5])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:handlers.ts#load:3")

    def test_namespace_imported_arrow_const_binds(self):
        rows = self._rows_files({
            "auth.ts": "export const get_uid = (req: any) => req.user.id;\n",
            "handlers.ts": (
                'import * as auth from "./auth";\n'
                "\n"
                "export function load(req: any) {\n"
                "  const uid = auth.get_uid(req);\n"
                '  return db.query("SELECT * FROM photos WHERE owner_id = ?", [uid]);\n'
                "}\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(binding["line"], 5)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_arrow_const_non_identity_body_does_not_bind(self):
        rows = self._rows(
            "export const get_uid = (req: any) => req.session.id;\n"
            "\n"
            + self._LOAD
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_arrow_const_block_mixed_returns_do_not_bind(self):
        rows = self._rows(
            "export const get_uid = (req: any) => {\n"
            "  if (req.user) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "  return req.session.id;\n"
            "};\n"
            "\n"
            + self._LOAD
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_annotated_arrow_const_does_not_bind(self):
        rows = self._rows(
            "const get_uid: (req: any) => string = (req) => req.user.id;\n"
            "\n"
            + self._LOAD
        )
        self.assertEqual(self._direct_bindings(rows), [])


class VueFunctionOwnershipTests(unittest.TestCase):
    def _rows_files(self, files):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, source in files.items():
                (root / name).write_text(source, encoding="utf-8")
            # Memory mode, exactly like the scanner: a `.vue` file joins
            # through its line-padded `<script>` text.
            js_ts_source = {}
            for name, source in files.items():
                if name.endswith(".vue"):
                    script = vue_script_for_adapter(source)
                    if script is not None:
                        js_ts_source[name] = script
                elif Path(name).suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                    js_ts_source[name] = source
            parsed = analyze_js_ts_project(root, [], js_ts_source=js_ts_source)
            owners = ts_function_owners(parsed)
            return extract_identity_evidence(files, owners)

    def _rows(self, source, filename="App.vue"):
        return self._rows_files({filename: source})

    def _direct_bindings(self, rows):
        return [row for row in rows
                if row["kind"] in ("direct_authenticated_identity_to_sql_owner_binding",
                                   "direct_request_identity_to_sql_owner_binding")]

    _SETUP = (
        "<template>\n"
        "  <button @click=\"load\">Load</button>\n"
        "</template>\n"
        "<script setup lang=\"ts\">\n"
        "function get_uid(req: any) {\n"
        "  return req.user.id;\n"
        "}\n"
        "async function load(req: any) {\n"
        "  const uid = get_uid(req);\n"
        "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
        "}\n"
        "</script>\n"
    )

    _AUTH_VUE = (
        "<template>\n"
        "  <div>Auth</div>\n"
        "</template>\n"
        "<script setup lang=\"ts\">\n"
        "export function get_uid(req: any) {\n"
        "  return req.user.id;\n"
        "}\n"
        "</script>\n"
    )

    def test_script_setup_top_level_function_binds(self):
        rows = self._rows(self._SETUP)
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 10)
        self.assertEqual(binding["details"]["binding_status"], "INTERPROCEDURAL_SINGLE_HOP_RETURN")
        self.assertEqual(binding["details"]["identity_expressions"], ["req.user.id"])
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [6, 9, 10])
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "App.vue")
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:8")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_template_braces_do_not_corrupt_ownership(self):
        rows = self._rows(
            "<template>\n"
            "  <div>{{ user.name }}</div>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script setup lang=\"ts\">\n"
            "function get_uid(req: any) {\n"
            "  return req.user.id;\n"
            "}\n"
            "async function load(req: any) {\n"
            "  const uid = get_uid(req);\n"
            "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "}\n"
            "</script>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 11)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [7, 10, 11])
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:9")

    def test_style_braces_do_not_corrupt_ownership(self):
        rows = self._rows(
            self._SETUP
            + "<style>\n"
            ".btn { color: red; }\n"
            "</style>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 10)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [6, 9, 10])

    def test_plain_script_top_level_function_binds(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "export function get_uid(req: any) {\n"
            "  return req.user.id;\n"
            "}\n"
            "export function load(req: any) {\n"
            "  const uid = get_uid(req);\n"
            "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "}\n"
            "</script>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 10)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [6, 9, 10])
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:8")

    def test_named_import_from_ts_file_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export function get_uid(req: any) {\n"
                "  return req.user.id;\n"
                "}\n"
            ),
            "App.vue": (
                "<template>\n"
                "  <button @click=\"load\">Load</button>\n"
                "</template>\n"
                "<script setup lang=\"ts\">\n"
                "import { get_uid } from \"./auth\";\n"
                "async function load(req: any) {\n"
                "  const uid = get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 8)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [2, 7, 8])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_named_import_from_vue_file_binds(self):
        rows = self._rows_files({
            "auth.vue": self._AUTH_VUE,
            "App.vue": (
                "<template>\n"
                "  <button @click=\"load\">Load</button>\n"
                "</template>\n"
                "<script setup lang=\"ts\">\n"
                "import { get_uid } from \"./auth\";\n"
                "async function load(req: any) {\n"
                "  const uid = get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 8)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [6, 7, 8])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.vue")

    def test_named_import_from_vue_file_with_extension_binds(self):
        rows = self._rows_files({
            "auth.vue": self._AUTH_VUE,
            "App.vue": (
                "<template>\n"
                "  <button @click=\"load\">Load</button>\n"
                "</template>\n"
                "<script setup lang=\"ts\">\n"
                "import { get_uid } from \"./auth.vue\";\n"
                "async function load(req: any) {\n"
                "  const uid = get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 8)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.vue")

    def test_namespace_import_from_vue_file_binds(self):
        rows = self._rows_files({
            "auth.vue": self._AUTH_VUE,
            "App.vue": (
                "<template>\n"
                "  <button @click=\"load\">Load</button>\n"
                "</template>\n"
                "<script setup lang=\"ts\">\n"
                "import * as auth from \"./auth\";\n"
                "async function load(req: any) {\n"
                "  const uid = auth.get_uid(req);\n"
                "  return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "}\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 8)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [6, 7, 8])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.vue")
        self.assertEqual(binding["details"]["propagation"][0]["function"], "get_uid")

    def test_vue_without_script_produces_no_records(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
        )
        self.assertEqual(rows, [])

    def test_options_api_methods_bind(self):
        # V3 (moved boundary, was "does not bind" in V2): Options API
        # `methods:` scan as the synthetic class `__vue_options__`, so
        # `this.get_uid(req)` binds like a class method.
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "export default {\n"
            "  methods: {\n"
            "    get_uid(req: any) {\n"
            "      return req.user.id;\n"
            "    },\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 12)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [8, 11, 12])
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "__vue_options__.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "App.vue")
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:10")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_options_api_define_component_binds(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "import { defineComponent } from \"vue\";\n"
            "export default defineComponent({\n"
            "  methods: {\n"
            "    get_uid(req: any) {\n"
            "      return req.user.id;\n"
            "    },\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "});\n"
            "</script>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 13)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [9, 12, 13])
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "__vue_options__.get_uid")

    def test_options_api_mixed_returns_do_not_bind(self):
        # A second direct return that matches neither identity pattern
        # disqualifies the method. (A string-literal return would not:
        # `_strip_ts_line` removes string literals before the return
        # scan, so such a return is invisible -- the class scan has
        # the same behaviour, and V3 mirrors it.)
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "export default {\n"
            "  methods: {\n"
            "    get_uid(req: any) {\n"
            "      if (req.guest) {\n"
            "        return req.session.id;\n"
            "      }\n"
            "      return req.user.id;\n"
            "    },\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_options_api_profileless_method_shadows(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "export default {\n"
            "  methods: {\n"
            "    get_uid(req: any) {\n"
            "      return \"fixed\";\n"
            "    },\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_methods_in_non_default_object_do_not_bind(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "const options = {\n"
            "  methods: {\n"
            "    get_uid(req: any) {\n"
            "      return req.user.id;\n"
            "    },\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "export default options;\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_options_api_and_class_coexist(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  methods: {\n"
            "    get_uid(req: any) {\n"
            "      return req.user.id;\n"
            "    },\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 17)
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "__vue_options__.get_uid")
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:15")

    def test_options_api_collision_with_same_named_class_closes(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class __vue_options__ {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  methods: {\n"
            "    get_uid(req: any) {\n"
            "      return req.user.id;\n"
            "    },\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_options_api_nested_arrow_return_does_not_bind(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "export default {\n"
            "  methods: {\n"
            "    get_uid(req: any) {\n"
            "      const inner = () => {\n"
            "        return req.user.id;\n"
            "      };\n"
            "      return inner();\n"
            "    },\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_computed_methods_do_not_bind(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "export default {\n"
            "  computed: {\n"
            "    get_uid(req: any) {\n"
            "      return req.user.id;\n"
            "    },\n"
            "  },\n"
            "  methods: {\n"
            "    load(req: any) {\n"
            "      const uid = this.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_default_object_methods_in_ts_file_do_not_bind(self):
        rows = self._rows_files({
            "component.ts": (
                "export default {\n"
                "  methods: {\n"
                "    get_uid(req: any) {\n"
                "      return req.user.id;\n"
                "    },\n"
                "    load(req: any) {\n"
                "      const uid = this.get_uid(req);\n"
                "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "    },\n"
                "  },\n"
                "};\n"
            ),
        })
        self.assertEqual(self._direct_bindings(rows), [])

    _OPTIONS_HELPER = (
        "<template>\n"
        "  <button @click=\"load\">Load</button>\n"
        "</template>\n"
        "<script lang=\"ts\">\n"
        "class AuthHelper {\n"
        "  get_uid(req: any) {\n"
        "    return req.user.id;\n"
        "  }\n"
        "}\n"
        "export default {\n"
        "  created() {\n"
        "    this.helper = new AuthHelper();\n"
        "  },\n"
        "  methods: {\n"
        "    load(req: any) {\n"
        "      const uid = this.helper.get_uid(req);\n"
        "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
        "    },\n"
        "  },\n"
        "};\n"
        "</script>\n"
    )

    def test_options_api_created_helper_binds(self):
        rows = self._rows(self._OPTIONS_HELPER)
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 17)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [7, 16, 17])
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "AuthHelper.get_uid")
        self.assertEqual(binding["details"]["propagation"][0]["path"], "App.vue")
        self.assertEqual(binding["owner_symbol_id"], "symbol:App.vue#load:15")
        self.assertEqual(binding["owner_resolution_status"], "RESOLVED")

    def test_options_api_mounted_helper_binds(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  mounted() {\n"
            "    this.helper = new AuthHelper();\n"
            "  },\n"
            "  methods: {\n"
            "    load(req: any) {\n"
            "      const uid = this.helper.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 17)
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "AuthHelper.get_uid")

    def test_options_api_helper_bound_in_method_binds(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  methods: {\n"
            "    load(req: any) {\n"
            "      this.helper = new AuthHelper();\n"
            "      const uid = this.helper.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 15)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [7, 14, 15])

    def test_options_api_helper_bound_in_nested_arrow_binds(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  created() {\n"
            "    const bind = () => {\n"
            "      this.helper = new AuthHelper();\n"
            "    };\n"
            "    bind();\n"
            "  },\n"
            "  methods: {\n"
            "    load(req: any) {\n"
            "      const uid = this.helper.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 20)
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "AuthHelper.get_uid")

    def test_options_api_helper_bound_in_nested_function_does_not_bind(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  created() {\n"
            "    function bind() {\n"
            "      this.helper = new AuthHelper();\n"
            "    }\n"
            "    bind();\n"
            "  },\n"
            "  methods: {\n"
            "    load(req: any) {\n"
            "      const uid = this.helper.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_options_api_helper_bound_to_two_classes_does_not_bind(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "class OtherHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  created() {\n"
            "    this.helper = new AuthHelper();\n"
            "  },\n"
            "  mounted() {\n"
            "    this.helper = new OtherHelper();\n"
            "  },\n"
            "  methods: {\n"
            "    load(req: any) {\n"
            "      const uid = this.helper.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_options_api_helper_non_construction_binding_does_not_bind(self):
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  created() {\n"
            "    this.helper = getHelper();\n"
            "  },\n"
            "  methods: {\n"
            "    load(req: any) {\n"
            "      const uid = this.helper.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])

    def test_options_api_helper_from_ts_file_binds(self):
        rows = self._rows_files({
            "auth.ts": (
                "export class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
            ),
            "App.vue": (
                "<template>\n"
                "  <button @click=\"load\">Load</button>\n"
                "</template>\n"
                "<script lang=\"ts\">\n"
                "import { AuthHelper } from \"./auth\";\n"
                "export default {\n"
                "  created() {\n"
                "    this.helper = new AuthHelper();\n"
                "  },\n"
                "  methods: {\n"
                "    load(req: any) {\n"
                "      const uid = this.helper.get_uid(req);\n"
                "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "    },\n"
                "  },\n"
                "};\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 13)
        self.assertEqual([item["line"] for item in binding["details"]["propagation"]],
                         [3, 12, 13])
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.ts")
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "AuthHelper.get_uid")

    def test_options_api_helper_from_vue_file_binds(self):
        rows = self._rows_files({
            "auth.vue": (
                "<template>\n"
                "  <div>Auth</div>\n"
                "</template>\n"
                "<script lang=\"ts\">\n"
                "export class AuthHelper {\n"
                "  get_uid(req: any) {\n"
                "    return req.user.id;\n"
                "  }\n"
                "}\n"
                "</script>\n"
            ),
            "App.vue": (
                "<template>\n"
                "  <button @click=\"load\">Load</button>\n"
                "</template>\n"
                "<script lang=\"ts\">\n"
                "import { AuthHelper } from \"./auth\";\n"
                "export default {\n"
                "  created() {\n"
                "    this.helper = new AuthHelper();\n"
                "  },\n"
                "  methods: {\n"
                "    load(req: any) {\n"
                "      const uid = this.helper.get_uid(req);\n"
                "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
                "    },\n"
                "  },\n"
                "};\n"
                "</script>\n"
            ),
        })
        binding = next(row for row in rows
                       if row["kind"] == "direct_authenticated_identity_to_sql_owner_binding"
                       and row["line"] == 13)
        self.assertEqual(binding["details"]["propagation"][0]["path"], "auth.vue")
        self.assertEqual(binding["details"]["propagation"][0]["function"],
                         "AuthHelper.get_uid")

    def test_options_api_data_object_helper_does_not_bind(self):
        # Out of scope for V4: `data()` returning an object literal with
        # `helper: new AuthHelper()` is not a `this.helper = ...`
        # assignment, so no attribute binding is recorded.
        rows = self._rows(
            "<template>\n"
            "  <button @click=\"load\">Load</button>\n"
            "</template>\n"
            "<script lang=\"ts\">\n"
            "class AuthHelper {\n"
            "  get_uid(req: any) {\n"
            "    return req.user.id;\n"
            "  }\n"
            "}\n"
            "export default {\n"
            "  data() {\n"
            "    return { helper: new AuthHelper() };\n"
            "  },\n"
            "  methods: {\n"
            "    load(req: any) {\n"
            "      const uid = this.helper.get_uid(req);\n"
            "      return db.query(\"SELECT * FROM photos WHERE owner_id = ?\", [uid]);\n"
            "    },\n"
            "  },\n"
            "};\n"
            "</script>\n"
        )
        self.assertEqual(self._direct_bindings(rows), [])


if __name__ == "__main__":
    unittest.main()
