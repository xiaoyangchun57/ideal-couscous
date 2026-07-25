import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class DepartureConfirmationRouteTest(unittest.TestCase):
    def setUp(self):
        temporary_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temporary_file.close()
        self.db_path = temporary_file.name
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_site_cache = dict(app_module._site_ids_cache)

        @contextmanager
        def temporary_db():
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'owner-token': {'id': 9, 'role': 'operator', 'real_name': '执行人员'},
            'other-token': {'id': 10, 'role': 'operator', 'real_name': '其他人员'},
        })
        today = datetime.now().strftime('%Y-%m-%d')
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT, status TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, status TEXT, version INTEGER
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_schedule_id INTEGER, assignee_id INTEGER,
                    generate_date TEXT, status TEXT, completion_rate REAL DEFAULT 0
                );
                CREATE TABLE plan_departure_confirmations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL, work_date TEXT NOT NULL,
                    vehicle_confirmed INTEGER NOT NULL DEFAULT 0,
                    parts_confirmed INTEGER NOT NULL DEFAULT 0, note TEXT DEFAULT '',
                    confirmed_at TEXT, UNIQUE(schedule_id, user_id, work_date)
                );
                CREATE TABLE plan_schedule_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL,
                    version INTEGER NOT NULL, event_type TEXT NOT NULL,
                    operator_id INTEGER, payload TEXT DEFAULT '{}'
                );
                CREATE TABLE vehicles (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE spare_parts_inventory (id INTEGER PRIMARY KEY, quantity INTEGER);
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?)', [
                (9, '执行人员', 'operator', 'active'), (10, '其他人员', 'operator', 'active'),
            ])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(9, 1), (10, 2)])
            db.execute("INSERT INTO plan_schedules VALUES (5, 'approved', 3)")
            db.execute('INSERT INTO insp_plans VALUES (42, 5, 9, ?, \'active\', 0)', (today,))
            db.execute("INSERT INTO vehicles VALUES (1, 'available')")
            db.execute('INSERT INTO spare_parts_inventory VALUES (1, 12)')
        self.client = app_module.app.test_client()
        self.owner_headers = {'Authorization': 'Bearer owner-token'}
        self.other_headers = {'Authorization': 'Bearer other-token'}

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        os.unlink(self.db_path)

    def test_confirmation_is_idempotent_and_does_not_change_resources_or_execution(self):
        url = '/api/mobile/execution-plans/42/departure-confirmation'
        first = self.client.post(url, headers=self.owner_headers, json={
            'vehicle_confirmed': True, 'parts_confirmed': True, 'note': '出发前核验完成'
        })
        second = self.client.post(url, headers=self.owner_headers, json={
            'vehicle_confirmed': True, 'parts_confirmed': True, 'note': '出发前核验完成'
        })

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json['confirmation'], second.json['confirmation'])
        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_departure_confirmations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedule_events').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT version FROM plan_schedule_events').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT status FROM insp_plans WHERE id=42').fetchone()[0], 'active')
            self.assertEqual(db.execute('SELECT status FROM vehicles WHERE id=1').fetchone()[0], 'available')
            self.assertEqual(db.execute('SELECT quantity FROM spare_parts_inventory WHERE id=1').fetchone()[0], 12)
        finally:
            db.close()

    def test_other_operator_cannot_confirm_someone_elses_execution_package(self):
        response = self.client.post(
            '/api/mobile/execution-plans/42/departure-confirmation',
            headers=self.other_headers,
            json={'vehicle_confirmed': True, 'parts_confirmed': True},
        )

        self.assertEqual(response.status_code, 404)
        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_departure_confirmations').fetchone()[0], 0)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
