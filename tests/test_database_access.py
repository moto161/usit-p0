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


if __name__ == "__main__":
    unittest.main()
