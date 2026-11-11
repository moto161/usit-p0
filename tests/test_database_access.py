from __future__ import annotations

import unittest

from usit.database_access import extract_database_accesses


class DatabaseAccessExtractionTests(unittest.TestCase):
    def test_python_select_extracts_table_and_simple_columns(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'def load():\n    cursor.execute("SELECT id, owner_id FROM photos WHERE id = 1")\n'
        })
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["line"], 2)
        self.assertEqual(row["operation"], "SELECT")
        self.assertEqual(row["tables"], ["photos"])
        self.assertEqual(row["columns"], ["id", "owner_id"])
        self.assertEqual(row["parse_status"], "RESOLVED")
        self.assertEqual(row["verification_status"], "NOT_VERIFIED")

    def test_python_dynamic_sql_is_unknown_not_guessed(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": "cursor.execute(sql)\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])

    def test_javascript_query_extracts_insert_table_and_columns(self):
        rows = extract_database_accesses({
            "repo/photoRepo.ts": 'await pool.query("INSERT INTO photos (id, owner_id) VALUES (?, ?)");\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "INSERT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])

    def test_javascript_dynamic_query_is_unknown(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": "db.query(buildSql());\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["operation"], "UNKNOWN")

    def test_cte_and_union_are_not_overclaimed(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH x AS (SELECT id FROM photos) SELECT id FROM x")\n'
        })
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])

    def test_python_implicit_concatenation_across_lines(self):
        source = (
            "def load():\n"
            "    cursor.execute(\"SELECT id, \"\n"
            "                     \"owner_id FROM photos WHERE id = 1\")\n"
        )
        rows = extract_database_accesses({"repo/photo_repo.py": source})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 2)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")
        self.assertFalse(rows[0]["has_dynamic_parts"])

    def test_python_plus_chain_across_lines(self):
        source = (
            "def load():\n"
            "    cursor.execute(\"SELECT id, owner_id \"\n"
            "                   + \"FROM photos \"\n"
            "                   + \"WHERE id = 1\")\n"
        )
        rows = extract_database_accesses({"repo/photo_repo.py": source})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 2)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")
        self.assertFalse(rows[0]["has_dynamic_parts"])

    def test_python_triple_quoted_multiline_sql(self):
        source = (
            "def load():\n"
            "    cursor.execute(\"\"\"SELECT id, owner_id\n"
            "                    FROM photos\n"
            "                    WHERE id = 1\"\"\")\n"
        )
        rows = extract_database_accesses({"repo/photo_repo.py": source})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 2)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_python_fstring_static_skeleton_with_dynamic_value(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'def load(user_id):\n    cursor.execute(f"SELECT id, owner_id FROM photos WHERE id = {user_id}")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")
        self.assertTrue(rows[0]["has_dynamic_parts"])
        self.assertEqual(rows[0]["parser"], "python-ast-literal-expression-dynamic")

    def test_js_template_literal_multiline_with_interpolation(self):
        source = (
            "async function load(userId) {\n"
            "  const rows = await pool.query(`SELECT id, owner_id\n"
            "    FROM photos\n"
            "    WHERE id = ${userId}`);\n"
            "}\n"
        )
        rows = extract_database_accesses({"repo/photoRepo.ts": source})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 2)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")
        self.assertTrue(rows[0]["has_dynamic_parts"])
        self.assertEqual(rows[0]["parser"], "js-ts-literal-expression-dynamic")

    def test_js_plus_chain_across_lines(self):
        source = (
            "async function load() {\n"
            "  const rows = await pool.query(\"SELECT id, owner_id \" +\n"
            "    `FROM photos ` +\n"
            "    \"WHERE id = 1\");\n"
            "}\n"
        )
        rows = extract_database_accesses({"repo/photoRepo.js": source})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 2)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")
        self.assertFalse(rows[0]["has_dynamic_parts"])

    def test_python_plus_chain_with_variable_is_unknown(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'def load(prefix):\n    cursor.execute("SELECT id FROM photos WHERE id = 1" + prefix)\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])

    def test_python_format_call_is_unknown(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'def load(user_id):\n    cursor.execute("SELECT id FROM photos WHERE id = {}".format(user_id))\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'def load(user_id):\n    cursor.execute("SELECT id FROM photos WHERE id = %s" % user_id)\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")

    def test_js_table_name_inside_interpolation_is_unknown(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": "async function load(table) {\n  const rows = await pool.query(`SELECT * FROM ${table}`);\n}\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])
        self.assertTrue(rows[0]["has_dynamic_parts"])

    def test_js_identifier_argument_is_unknown(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": "db.query(sql);\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])

    def test_python_syntax_error_elsewhere_falls_back_to_per_line(self):
        source = (
            "def broken(:\n"
            "    pass\n"
            "cursor.execute(\"SELECT id FROM photos WHERE id = 1\")\n"
        )
        rows = extract_database_accesses({"repo/photo_repo.py": source})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 3)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")
        self.assertEqual(rows[0]["parser"], "python-ast-single-line")

    def test_js_backslash_line_continuation_in_quoted_string(self):
        source = (
            'const rows = await pool.query("SELECT id, owner_id \\\n'
            '  FROM photos WHERE id = 1");\n'
        )
        rows = extract_database_accesses({"repo/photoRepo.js": source})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_python_empty_string_sql_is_unknown(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])


if __name__ == "__main__":
    unittest.main()
