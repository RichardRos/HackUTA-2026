import unittest
from unittest.mock import patch

from search_service import _validate_select, agentic_search, describe_dataset


class SQLValidationTests(unittest.TestCase):
    def test_timescale_select_and_cte_are_accepted(self):
        sql = (
            "WITH hourly AS (SELECT time_bucket('1 hour', period) AS bucket, "
            "avg(value_mwh) AS demand FROM grid_hourly WHERE ba_code = 'ERCO' "
            "GROUP BY bucket) SELECT * FROM hourly ORDER BY bucket LIMIT 24;"
        )
        self.assertEqual(_validate_select(sql), sql[:-1])

    def test_unsafe_statements_are_rejected(self):
        cases = (
            "SELECT 1; SELECT 2",
            "INSERT INTO grid_hourly VALUES (now(), 'ERCO', 'D', 1, now())",
            "WITH gone AS (DELETE FROM grid_hourly RETURNING *) SELECT * FROM gone",
            "SELECT * INTO copied_grid FROM grid_hourly",
            "SELECT * FROM grid_hourly FOR UPDATE",
            "EXPLAIN SELECT * FROM grid_hourly",
        )
        for sql in cases:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                _validate_select(sql)

    def test_catalog_contains_timescale_schema_and_relationships(self):
        catalog = describe_dataset()
        self.assertIn("grid_hourly", catalog["sql_relations"])
        self.assertIn("time_bucket", catalog["sql_relations"]["timescale_functions"])
        self.assertTrue(catalog["relationships"])


class AgenticSearchTests(unittest.TestCase):
    @patch("search_service._query", return_value=[{"demand_mwh": 50000.0}])
    @patch("search_service.gemini_json")
    def test_generated_sql_is_validated_and_capped(self, gemini, query):
        gemini.return_value = {
            "sql": "SELECT value_mwh AS demand_mwh FROM grid_hourly "
                   "WHERE ba_code = 'ERCO' ORDER BY period DESC LIMIT 25",
            "explanation": "Latest ERCOT demand.",
        }
        result = agentic_search("latest ERCOT demand", max_rows=10)
        self.assertEqual(result["rows"], [{"demand_mwh": 50000.0}])
        self.assertFalse(result["truncated"])
        sql, params = query.call_args.args
        self.assertIn("SELECT * FROM (SELECT value_mwh", sql)
        self.assertEqual(params, (10,))
        self.assertEqual(gemini.call_args.args[2], "response_json_schema")

    def test_invalid_request_never_calls_gemini(self):
        with patch("search_service.gemini_json") as gemini:
            for request, max_rows in (("", 10), ("x" * 2001, 10), ("valid", 0), ("valid", 1001)):
                with self.subTest(max_rows=max_rows), self.assertRaises(ValueError):
                    agentic_search(request, max_rows)
        gemini.assert_not_called()

    @patch("search_service.gemini_json")
    def test_generated_write_never_reaches_database(self, gemini):
        gemini.return_value = {"sql": "DELETE FROM grid_hourly", "explanation": "bad"}
        with patch("search_service._query") as query:
            with self.assertRaises(ValueError):
                agentic_search("delete grid data")
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
