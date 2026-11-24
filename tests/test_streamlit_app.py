"""Reader boundary tests; no external database or writes are needed."""
import importlib.util
import os
import re
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("league_app", ROOT / "app/streamlit_app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class ReaderTests(unittest.TestCase):
    def test_owner_url_is_rejected_without_connecting(self):
        with patch.object(app, "load_dotenv"), patch.dict(os.environ, {
            "APP_DATABASE_URL": "postgresql://postgres:private@example.test/db"
        }), patch.object(app.psycopg, "connect") as connect:
            with self.assertRaises(app.DataUnavailable):
                with app.reader():
                    self.fail("Owner credentials must never reach application queries")
            connect.assert_not_called()

    def test_missing_reader_url_does_not_fall_back_to_owner(self):
        with patch.object(app, "load_dotenv"), patch.dict(os.environ, {
            "APP_DATABASE_URL": "", "DATABASE_URL": "postgresql://postgres:private@example.test/db"
        }), patch.object(app.psycopg, "connect") as connect:
            with self.assertRaises(app.DataUnavailable):
                with app.reader():
                    self.fail("Missing reader must stop")
            connect.assert_not_called()

    def test_actual_identity_checked_before_yield(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"session_user": "postgres", "current_user": "app_reader"}
        with patch.object(app, "database_url", return_value="reader-url"), patch.object(app.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = conn
            with self.assertRaises(app.DataUnavailable):
                with app.reader():
                    self.fail("Session impersonation must be rejected")
            self.assertEqual(conn.execute.call_count, 2)

    def test_read_only_transaction_and_connection(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"session_user": "app_reader", "current_user": "app_reader"}
        with patch.object(app, "database_url", return_value="reader-url"), patch.object(app.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = conn
            with app.reader() as actual:
                self.assertIs(actual, conn)
            self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])
            self.assertEqual(connect.call_args.kwargs["sslmode"], "require")
            self.assertEqual(conn.execute.call_args_list[0].args[0],
                             "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

    def test_driver_error_is_sanitized(self):
        with patch.object(app, "database_url", return_value="reader-url"), \
             patch.object(app.psycopg, "connect", side_effect=RuntimeError("private-password")):
            with self.assertRaises(app.DataUnavailable) as caught:
                with app.reader():
                    pass
            self.assertEqual(str(caught.exception), "")
            self.assertTrue(caught.exception.__suppress_context__)

    def test_all_five_query_definitions_are_loaded(self):
        self.assertEqual(list(app.query_source()), ["Q1", "Q2", "Q3", "Q4", "Q5"])

    def test_comment_percentages_do_not_become_driver_placeholders(self):
        for sql in app.query_source().values():
            self.assertNotIn("%", re.sub(r"%\([a-z_]+\)s", "", sql))
        self.assertIn("%(snapshot_at)s", app.query_source()["Q5"])


if __name__ == "__main__":
    unittest.main()
