from __future__ import annotations

import unittest

from usit.database_access import django_model_tables, extract_database_accesses


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

    def test_intersect_and_except_tables_are_resolved(self):
        # F7 changed this expectation: INTERSECT/EXCEPT branches are now
        # recognized like UNION branches.
        for keyword in ("INTERSECT", "EXCEPT"):
            rows = extract_database_accesses({
                "repo/photo_repo.py": f'cursor.execute("SELECT id FROM photos {keyword} SELECT id FROM albums")\n'
            })
            self.assertEqual(len(rows), 1, keyword)
            self.assertEqual(rows[0]["tables"], ["albums", "photos"], keyword)
            self.assertEqual(rows[0]["columns"], ["id"], keyword)
            self.assertEqual(rows[0]["parse_status"], "RESOLVED", keyword)

    def test_subquery_leading_with_tables_are_resolved(self):
        # F7 changed this expectation: a WITH inside a subquery is now
        # collected and only its CTE name is excluded.
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM (WITH x AS (SELECT id FROM albums) SELECT id FROM x) AS s")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_dynamic_table_inside_cte_is_dropped(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": "const rows = await pool.query(`WITH x AS (SELECT id FROM ${table}) SELECT id FROM x`);\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], [])
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertTrue(rows[0]["has_dynamic_parts"])

    def test_except_basic(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos EXCEPT SELECT id FROM albums")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["columns"], ["id"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_intersect_all_with_where_clauses(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos WHERE id = 1 INTERSECT ALL SELECT id FROM albums WHERE id = 2")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_union_intersect_except_mixed_chain(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos UNION SELECT id FROM albums INTERSECT SELECT id FROM users")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos", "users"])

    def test_intersect_mixed_with_comma_join(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM photos, albums INTERSECT SELECT * FROM users")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos", "users"])

    def test_intersect_inside_cte_body(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH x AS (SELECT id FROM photos INTERSECT SELECT id FROM albums) SELECT id FROM x")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_intersect_columns_come_from_first_branch(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id, owner_id FROM photos INTERSECT SELECT id FROM albums")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["columns"], ["id", "owner_id"])
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])

    def test_with_inside_join_subquery(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM photos JOIN (WITH y AS (SELECT id FROM albums) SELECT id FROM y) AS s ON photos.id = s.id")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_nested_with_inside_cte_body(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH a AS (WITH b AS (SELECT id FROM albums) SELECT id FROM b) SELECT id FROM a")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_two_subqueries_each_with_a_cte(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM (WITH x AS (SELECT id FROM albums) SELECT id FROM x) AS s1 JOIN (WITH y AS (SELECT id FROM users) SELECT id FROM y) AS s2 ON s1.id = s2.id")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "users"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_non_initial_with_shadowing_a_real_table(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM (WITH photos AS (SELECT id FROM albums) SELECT id FROM photos) AS s")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_non_initial_with_with_comma_join(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM (WITH x AS (SELECT id FROM albums) SELECT id FROM x) AS s, photos")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_as_materialized(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH x AS MATERIALIZED (SELECT id FROM photos) SELECT id FROM x")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_as_not_materialized(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH x AS NOT MATERIALIZED (SELECT id FROM photos) SELECT id FROM x")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_non_initial_with_as_materialized(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM (WITH x AS MATERIALIZED (SELECT id FROM albums) SELECT id FROM x) AS s")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_materialized_without_parens_stays_unknown(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT * FROM (WITH x AS MATERIALIZED SELECT id FROM albums) AS s")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])

    def test_dynamic_table_inside_non_initial_with_is_dropped(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": "const rows = await pool.query(`SELECT * FROM (WITH x AS (SELECT id FROM ${table}) SELECT id FROM x) AS s`);\n"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], [])
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertTrue(rows[0]["has_dynamic_parts"])

    # F8: multi-statement literals split on top-level semicolons.

    def test_two_select_statements_yield_two_records(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos; SELECT id FROM albums")\n'
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["operation"] for r in rows], ["SELECT", "SELECT"])
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[1]["tables"], ["albums"])
        self.assertEqual(rows[0]["columns"], ["id"])
        self.assertEqual(rows[1]["columns"], ["id"])
        self.assertEqual(rows[0]["line"], rows[1]["line"])
        self.assertEqual(rows[0]["callee"], rows[1]["callee"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")
        self.assertEqual(rows[1]["parse_status"], "RESOLVED")

    def test_select_then_insert_yields_two_records(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos; INSERT INTO albums (title) VALUES (%s)")\n'
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["id"])
        self.assertEqual(rows[1]["operation"], "INSERT")
        self.assertEqual(rows[1]["tables"], ["albums"])
        self.assertEqual(rows[1]["columns"], ["title"])

    def test_update_then_delete_yields_two_records(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("UPDATE photos SET title=%s; DELETE FROM albums WHERE id=%s")\n'
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["operation"], "UPDATE")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["columns"], ["title"])
        self.assertEqual(rows[1]["operation"], "DELETE")
        self.assertEqual(rows[1]["tables"], ["albums"])
        self.assertEqual(rows[1]["columns"], [])

    def test_begin_before_select_is_skipped_not_unknown(self):
        # Headline F8 fix: today this whole call is one UNKNOWN record.
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("BEGIN; SELECT id FROM photos")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")

    def test_set_and_commit_fragments_yield_no_record(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SET x = 1; SELECT id FROM photos; COMMIT")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])

    def test_three_selects_with_comma_join_in_third(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos; SELECT name FROM albums; SELECT * FROM users, logs")\n'
        })
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[1]["tables"], ["albums"])
        self.assertEqual(rows[2]["tables"], ["logs", "users"])

    def test_cte_scope_is_isolated_per_statement(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM recent; WITH recent AS (SELECT id FROM logs) SELECT id FROM recent")\n'
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tables"], ["recent"])
        self.assertEqual(rows[1]["tables"], ["logs"])

    def test_trailing_and_double_semicolons_yield_no_empty_record(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos;")\n'
        })
        self.assertEqual(len(rows), 1)
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos;; SELECT id FROM albums;")\n'
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[1]["tables"], ["albums"])

    def test_semicolon_inside_string_literal_does_not_split(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("INSERT INTO logs (note) VALUES (\'a;b\')")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "INSERT")
        self.assertEqual(rows[0]["tables"], ["logs"])

    def test_semicolon_inside_line_comment_does_not_split(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("""SELECT id FROM photos -- 备注; 换行\nWHERE id = 1; SELECT id FROM albums""")\n'
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[1]["tables"], ["albums"])

    def test_dynamic_second_statement_is_unknown_not_guessed(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": "const rows = await pool.query(`SELECT id FROM photos; SELECT id FROM ${t}`);\n"
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "RESOLVED")
        self.assertTrue(rows[1]["has_dynamic_parts"])
        self.assertEqual(rows[1]["tables"], [])
        self.assertEqual(rows[1]["parse_status"], "UNKNOWN")

    def test_pure_non_dml_keeps_one_unknown_record(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("BEGIN; COMMIT")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")

    def test_javascript_multi_statement_yields_two_records(self):
        rows = extract_database_accesses({
            "repo/photoRepo.js": 'await pool.query("SELECT id FROM photos; SELECT id FROM albums");\n'
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[1]["tables"], ["albums"])

    def test_with_leading_statement_then_select(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("WITH recent AS (SELECT id FROM photos) SELECT id FROM recent; SELECT id FROM albums")\n'
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[1]["tables"], ["albums"])

    def test_drop_fragment_yields_no_record(self):
        rows = extract_database_accesses({
            "repo/photo_repo.py": 'cursor.execute("SELECT id FROM photos; DROP TABLE albums")\n'
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])


class OrmModelTableTests(unittest.TestCase):
    def test_query_with_tablename_resolves_select(self):
        rows = extract_database_accesses({
            "server/models.py": "class Photo:\n    __tablename__ = 'photos'\n",
            "server/repos/photo_repo.py": "def load(session):\n    return session.query(Photo).all()\n",
        })
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "server/repos/photo_repo.py")
        self.assertEqual(row["line"], 2)
        self.assertEqual(row["callee"], "session.query")
        self.assertEqual(row["operation"], "SELECT")
        self.assertEqual(row["tables"], ["photos"])
        self.assertEqual(row["columns"], [])
        self.assertEqual(row["parse_status"], "ORM_MODEL")
        self.assertEqual(row["orm_model"], ["Photo"])
        self.assertEqual(row["confidence"], "STATIC_INFERENCE")
        self.assertEqual(row["verification_status"], "NOT_VERIFIED")

    def test_query_with_two_models_resolves_both_tables_sorted(self):
        rows = extract_database_accesses({
            "server/models.py": (
                "class Photo:\n    __tablename__ = 'photos'\n"
                "class Album:\n    __tablename__ = 'albums'\n"
            ),
            "server/repos/photo_repo.py": "session.query(Photo, Album).all()\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["albums", "photos"])
        self.assertEqual(rows[0]["orm_model"], ["Album", "Photo"])
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")

    def test_query_with_unmapped_model_stays_unknown(self):
        rows = extract_database_accesses({
            "server/models.py": "class Photo:\n    pass\n",
            "server/repos/photo_repo.py": "session.query(Photo).all()\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertNotIn("orm_model", rows[0])

    def test_ambiguous_class_name_is_dropped(self):
        rows = extract_database_accesses({
            "server/models_a.py": "class Photo:\n    __tablename__ = 'photos'\n",
            "server/models_b.py": "class Photo:\n    __tablename__ = 'pictures'\n",
            "server/repos/photo_repo.py": "session.query(Photo).all()\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])

    def test_same_class_name_same_table_resolves(self):
        rows = extract_database_accesses({
            "server/models_a.py": "class Photo:\n    __tablename__ = 'photos'\n",
            "server/models_b.py": "class Photo:\n    __tablename__ = 'photos'\n",
            "server/repos/photo_repo.py": "session.query(Photo).all()\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")

    def test_query_with_column_argument_stays_unknown(self):
        rows = extract_database_accesses({
            "server/models.py": "class Photo:\n    __tablename__ = 'photos'\n",
            "server/repos/photo_repo.py": "session.query(Photo.id).all()\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])

    def test_query_with_no_arguments_stays_unknown(self):
        rows = extract_database_accesses({
            "server/repos/photo_repo.py": "session.query().all()\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")

    def test_tablename_must_be_a_plain_identifier_string(self):
        rows = extract_database_accesses({
            "server/models.py": (
                "class Photo:\n    __tablename__ = 42\n"
                "class Album:\n    __tablename__ = 'my schema'\n"
            ),
            "server/repos/photo_repo.py": (
                "session.query(Photo).all()\n"
                "session.query(Album).all()\n"
            ),
        })
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["parse_status"], "UNKNOWN")
            self.assertEqual(row["tables"], [])

    def test_annotated_tablename_resolves(self):
        rows = extract_database_accesses({
            "server/models.py": "class Photo:\n    __tablename__: str = 'photos'\n",
            "server/repos/photo_repo.py": "session.query(Photo).all()\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")

    def test_literal_sql_still_resolved_beside_orm_query(self):
        rows = extract_database_accesses({
            "server/models.py": "class Photo:\n    __tablename__ = 'photos'\n",
            "server/repos/photo_repo.py": (
                "session.query(Photo).all()\n"
                "cursor.execute(\"SELECT id FROM photos\")\n"
            ),
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")
        self.assertEqual(rows[1]["parse_status"], "RESOLVED")
        self.assertEqual(rows[1]["tables"], ["photos"])

    def test_module_level_tablename_is_ignored(self):
        rows = extract_database_accesses({
            "server/models.py": "__tablename__ = 'photos'\n",
            "server/repos/photo_repo.py": "session.query(Photo).all()\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])


class DjangoModelTableTests(unittest.TestCase):
    def test_meta_db_table_maps_model_to_table(self):
        tables = django_model_tables({
            "server/models.py": "class Photo:\n    class Meta:\n        db_table = 'photos'\n",
        })
        self.assertEqual(tables, {"Photo": "photos"})

    def test_meta_db_table_annotated_assignment_maps(self):
        tables = django_model_tables({
            "server/models.py": "class Photo:\n    class Meta:\n        db_table: str = 'photos'\n",
        })
        self.assertEqual(tables, {"Photo": "photos"})

    def test_meta_db_table_with_space_does_not_map(self):
        tables = django_model_tables({
            "server/models.py": "class Photo:\n    class Meta:\n        db_table = 'my schema'\n",
        })
        self.assertEqual(tables, {})

    def test_same_model_name_mapping_to_two_tables_is_dropped(self):
        tables = django_model_tables({
            "server/models.py": "class Photo:\n    class Meta:\n        db_table = 'photos'\n",
            "server/other.py": "class Photo:\n    class Meta:\n        db_table = 'images'\n",
        })
        self.assertEqual(tables, {})

    def test_model_without_meta_or_db_table_does_not_map(self):
        tables = django_model_tables({
            "server/models.py": (
                "class Photo:\n    pass\n"
                "class Album:\n    class Meta:\n        verbose_name = 'album'\n"
            ),
        })
        self.assertEqual(tables, {})

    def test_default_table_from_app_directory(self):
        tables = django_model_tables({
            "photos/models.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {"Photo": "photos_photo"})

    def test_default_table_from_nested_app_directory(self):
        tables = django_model_tables({
            "server/photos/models.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {"Photo": "photos_photo"})

    def test_default_table_from_models_package(self):
        tables = django_model_tables({
            "photos/models/photo.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {"Photo": "photos_photo"})

    def test_meta_app_label_overrides_directory(self):
        tables = django_model_tables({
            "photos/models.py": (
                "from django.db import models\n\n\n"
                "class Photo(models.Model):\n"
                "    class Meta:\n"
                "        app_label = 'gallery'\n"
            ),
        })
        self.assertEqual(tables, {"Photo": "gallery_photo"})

    def test_apps_py_config_label_overrides_directory(self):
        tables = django_model_tables({
            "photos/apps.py": (
                "from django.apps import AppConfig\n\n\n"
                "class PhotosConfig(AppConfig):\n"
                "    name = 'photos'\n"
                "    label = 'custom'\n"
            ),
            "photos/models.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {"Photo": "custom_photo"})

    def test_non_model_base_gets_no_default_table(self):
        tables = django_model_tables({
            "photos/models.py": "class Photo(Base):\n    pass\n",
        })
        self.assertEqual(tables, {})

    def test_model_in_another_file_of_the_app_gets_default_table(self):
        tables = django_model_tables({
            "photos/models.py": "from django.db import models\n",
            "photos/views.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {"Photo": "photos_photo"})

    def test_model_outside_any_app_gets_no_default_table(self):
        tables = django_model_tables({
            "other/views.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {})

    def test_abstract_and_proxy_models_get_no_default_table(self):
        tables = django_model_tables({
            "photos/models.py": (
                "from django.db import models\n\n\n"
                "class Base(models.Model):\n"
                "    class Meta:\n"
                "        abstract = True\n\n\n"
                "class PhotoView(models.Model):\n"
                "    class Meta:\n"
                "        proxy = True\n"
            ),
        })
        self.assertEqual(tables, {})

    def test_explicit_db_table_wins_over_default(self):
        tables = django_model_tables({
            "photos/models.py": (
                "from django.db import models\n\n\n"
                "class Photo(models.Model):\n"
                "    class Meta:\n"
                "        db_table = 'images'\n"
            ),
        })
        self.assertEqual(tables, {"Photo": "images"})

    def test_same_model_name_with_different_default_tables_is_dropped(self):
        tables = django_model_tables({
            "photos/models.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
            "gallery/models.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {})

    def test_multi_word_model_name_is_lowercased_whole(self):
        tables = django_model_tables({
            "photos/models.py": "from django.db import models\n\n\nclass PhotoAlbum(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {"PhotoAlbum": "photos_photoalbum"})

    def test_two_distinct_config_labels_skip_default(self):
        tables = django_model_tables({
            "photos/apps.py": (
                "from django.apps import AppConfig\n\n\n"
                "class AConfig(AppConfig):\n"
                "    name = 'photos'\n"
                "    label = 'one'\n\n\n"
                "class BConfig(AppConfig):\n"
                "    name = 'photos'\n"
                "    label = 'two'\n"
            ),
            "photos/models.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
        })
        self.assertEqual(tables, {})


class DjangoObjectsTests(unittest.TestCase):
    _MODELS = (
        "class Photo:\n    class Meta:\n        db_table = 'photos'\n"
        "class Album:\n    pass\n"
    )

    def _rows(self, view_source):
        return extract_database_accesses({
            "server/models.py": self._MODELS,
            "server/views.py": view_source,
        })

    def test_filter_resolves_select(self):
        rows = self._rows("def load():\n    return Photo.objects.filter(owner_id=1)\n")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "server/views.py")
        self.assertEqual(row["line"], 2)
        self.assertEqual(row["callee"], "Photo.objects.filter")
        self.assertEqual(row["operation"], "SELECT")
        self.assertEqual(row["tables"], ["photos"])
        self.assertEqual(row["columns"], [])
        self.assertEqual(row["parse_status"], "ORM_MODEL")
        self.assertEqual(row["orm_model"], ["Photo"])
        self.assertEqual(row["confidence"], "STATIC_INFERENCE")
        self.assertEqual(row["verification_status"], "NOT_VERIFIED")

    def test_get_resolves_select(self):
        rows = self._rows("def load():\n    return Photo.objects.get(id=1)\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")

    def test_exclude_resolves_select(self):
        rows = self._rows("def load():\n    return Photo.objects.exclude(owner_id=1)\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])

    def test_all_resolves_select(self):
        rows = self._rows("def load():\n    return Photo.objects.all()\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])

    def test_create_resolves_insert(self):
        rows = self._rows("def load():\n    return Photo.objects.create(owner_id=1)\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "INSERT")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")
        self.assertEqual(rows[0]["orm_model"], ["Photo"])

    def test_unmapped_model_filter_is_unknown(self):
        rows = self._rows("def load():\n    return Album.objects.filter(id=1)\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["parse_status"], "UNKNOWN")
        self.assertEqual(rows[0]["tables"], [])
        self.assertEqual(rows[0]["callee"], "Album.objects.filter")
        self.assertNotIn("orm_model", rows[0])

    def test_chained_update_yields_one_update_record(self):
        rows = self._rows("def load():\n    return Photo.objects.filter(owner_id=1).update(name=\"x\")\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "UPDATE")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")
        self.assertEqual(rows[0]["orm_model"], ["Photo"])

    def test_chained_delete_yields_one_delete_record(self):
        rows = self._rows("def load():\n    return Photo.objects.filter(owner_id=1).delete()\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "DELETE")
        self.assertEqual(rows[0]["tables"], ["photos"])
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")

    def test_multi_segment_chain_update_yields_one_update_record(self):
        rows = self._rows("def load():\n    return Photo.objects.filter(a=1).exclude(b=2).update(name=\"x\")\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "UPDATE")
        self.assertEqual(rows[0]["tables"], ["photos"])

    def test_chained_first_keeps_base_select_record(self):
        rows = self._rows("def load():\n    return Photo.objects.filter(a=1).first()\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos"])

    def test_get_or_create_is_not_recognized(self):
        rows = self._rows("def load():\n    return Photo.objects.get_or_create(owner_id=1)\n")
        self.assertEqual(rows, [])

    def test_filter_on_default_mapped_model_resolves_select(self):
        rows = extract_database_accesses({
            "photos/models.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
            "photos/views.py": "def load():\n    return Photo.objects.filter(owner_id=1)\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "SELECT")
        self.assertEqual(rows[0]["tables"], ["photos_photo"])
        self.assertEqual(rows[0]["parse_status"], "ORM_MODEL")
        self.assertEqual(rows[0]["orm_model"], ["Photo"])

    def test_chained_update_on_default_mapped_model(self):
        rows = extract_database_accesses({
            "photos/models.py": "from django.db import models\n\n\nclass Photo(models.Model):\n    pass\n",
            "photos/views.py": "def load():\n    return Photo.objects.filter(owner_id=1).update(name=\"x\")\n",
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["operation"], "UPDATE")
        self.assertEqual(rows[0]["tables"], ["photos_photo"])


if __name__ == "__main__":
    unittest.main()
