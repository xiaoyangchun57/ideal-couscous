import os
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
import verify_station_concurrency as concurrency_verifier


class _RecordingConnection:
    def __init__(self):
        self.statements = []
        self.row_factory = None

    def execute(self, statement, *args):
        self.statements.append(statement)
        return self

    def fetchone(self):
        return ('wal',)

    def rollback(self):
        pass

    def close(self):
        pass


class SqliteConcurrencyControlsTest(unittest.TestCase):
    def test_isolated_load_tokens_map_to_distinct_admin_identities(self):
        identities = [
            concurrency_verifier._isolated_authenticate_token(f'isolated-user-{index}')
            for index in range(10)
        ]
        self.assertEqual(len({identity[0]['id'] for identity in identities}), 10)
        self.assertTrue(all(identity[0]['roles'] == ['admin'] for identity in identities))
        self.assertTrue(all(identity[1] == [] and identity[2] is None for identity in identities))
        self.assertEqual(
            concurrency_verifier._isolated_authenticate_token('invalid-token'),
            (None, [], 'AUTH_REQUIRED'),
        )

    def test_request_and_scheduler_connections_do_not_change_journal_mode(self):
        request_connection = _RecordingConnection()
        with mock.patch.object(web_app.sqlite3, 'connect', return_value=request_connection):
            with web_app.get_db():
                pass
        self.assertFalse(any(
            'journal_mode' in statement.lower()
            for statement in request_connection.statements
        ))

        scheduler_connection = _RecordingConnection()
        with mock.patch.object(web_app.sqlite3, 'connect', return_value=scheduler_connection):
            connection = web_app._scheduler_db()
            connection.close()
        self.assertFalse(any(
            'journal_mode' in statement.lower()
            for statement in scheduler_connection.statements
        ))

    def test_startup_gate_sets_and_confirms_wal_once(self):
        with tempfile.TemporaryDirectory() as root:
            database = os.path.join(root, 'water.db')
            original = web_app.DB_PATH
            web_app.DB_PATH = database
            try:
                web_app._ensure_sqlite_wal_mode()
                with closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(connection.execute(
                        'PRAGMA journal_mode'
                    ).fetchone()[0].lower(), 'wal')
            finally:
                web_app.DB_PATH = original

    def test_process_file_lock_allows_only_one_local_writer(self):
        with tempfile.TemporaryDirectory() as root:
            lock_path = os.path.join(root, 'notification.lock')
            with web_app._try_process_file_lock(lock_path) as first:
                self.assertTrue(first)
                with web_app._try_process_file_lock(lock_path) as second:
                    self.assertFalse(second)


if __name__ == '__main__':
    unittest.main()
