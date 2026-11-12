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

    def test_cte_tables_are_resolved_not_the_cte_name(self):
        # F6 changed this expectation: the CTE's inner table is now
        # recognized and only the CTE name itself is excluded.
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH x AS (SELECT id FROM photos) SELECT id FROM x")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

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

    def test_schema_qualified_table_takes_last_segment(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM catalog.dbo.photos WHERE id = 1")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_join_of_two_schema_qualified_tables(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT p.id FROM dbo.photos p JOIN public.albums a ON p.album_id = a.id")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_double_quoted_table_name(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute(\'SELECT id FROM "photos" WHERE id = 1\')\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_backtick_quoted_table_name(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM `photos` WHERE id = 1")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_bracket_quoted_table_name(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM [photos] WHERE id = 1")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_insert_into_schema_qualified_table_keeps_columns(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("INSERT INTO public.photos (id, url) VALUES (?, ?)")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "INSERT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id", "url"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_update_quoted_table_keeps_columns(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute(\'UPDATE "photos" SET url = ? WHERE id = ?\')\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "UPDATE")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["url"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_quoted_table_name_with_space_is_dropped_not_guessed(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute(\'SELECT id FROM "my photos" WHERE id = 1\')\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], [])
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")

    def test_comma_join_two_tables(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM photos, albums")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_comma_join_with_aliases(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos p, albums AS a WHERE p.id = a.photo_id")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["columns"], ["id"])

    def test_comma_join_qualified_and_quoted_tables(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute(\'SELECT * FROM public.photos, "albums"\')\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])

    def test_comma_join_three_tables(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM photos, albums, users")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos", "users"])

    def test_comma_join_mixed_with_join(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM photos, albums JOIN users ON users.id = albums.owner_id")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos", "users"])

    def test_comma_join_skips_subquery_item(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM photos, (SELECT id FROM albums) AS sub")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])

    def test_comma_join_skips_table_function_call(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM photos, generate_series(1, 10)")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])

    def test_comma_join_dynamic_placeholder_is_dropped(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": "const rows = await pool.query(`SELECT * FROM photos, ${table}`);\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertTrue(rows[0]["has_dynamic_parts"])

    def test_insert_select_from_comma_join(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("INSERT INTO archive SELECT * FROM photos, albums")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "INSERT")
        self.assertEqual(rows[0]["tables"], ["albums", "archive", "photos"])
        self.assertEqual(rows[0]["columns"], [])

    def test_union_two_branches(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos UNION SELECT id FROM albums")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["columns"], ["id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_union_all_with_where_clauses(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos WHERE id = 1 UNION ALL SELECT id FROM albums WHERE id = 2")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_union_three_branches(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos UNION SELECT id FROM albums UNION SELECT id FROM users")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos", "users"])

    def test_union_mixed_with_comma_join(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM photos, albums UNION SELECT * FROM users")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos", "users"])

    def test_cte_with_column_list(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH recent(id, owner_id) AS (SELECT id, owner_id FROM photos) SELECT id FROM recent")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_multiple_ctes_joined_in_main_query(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH a AS (SELECT id FROM photos), b AS (SELECT id FROM albums) SELECT id FROM a JOIN b ON a.id = b.id")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_cte_name_shadowing_a_real_table(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH photos AS (SELECT id FROM albums) SELECT id FROM photos")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_recursive_cte(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH RECURSIVE r AS (SELECT id FROM photos UNION ALL SELECT id FROM photos WHERE id > 1) SELECT id FROM r")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_with_clause_before_insert(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH x AS (SELECT id FROM photos) INSERT INTO archive SELECT id FROM x")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "INSERT")
        self.assertEqual(rows[0]["tables"], ["archive", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_intersect_and_except_stay_unknown(self):
        for keyword in ("INTERSECT", "EXCEPT"):
            rows = extract_database_accesses({
                "repo/photo_repo.py": f'cursor.execute("SELECT id FROM photos {keyword} SELECT id FROM albums")\n'
            })
            self.assertEqual(len(rows), 1, keyword)
            self.assertEqual(rows[0]["parse_status"], "UNKNOWN", keyword)
            self.assertEqual(rows[0]["tables"], [], keyword)

    def test_non_initial_with_stays_unknown(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM (WITH x AS (SELECT id FROM albums) SELECT id FROM x) AS s")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])

    def test_dynamic_table_inside_cte_is_dropped(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": "const rows = await pool.query(`WITH x AS (SELECT id FROM ${table}) SELECT id FROM x`);\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], [])
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertTrue(rows[0]["has_dynamic_parts"])


if __name__ == "__main__":
    unittest.main()
