import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class MobileMyTodayScopeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_cache = dict(app_module._site_ids_cache)

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
        app_module._tokens['operator-token'] = {
            'id': 2, 'role': 'operator', 'real_name': '甲运维', 'username': 'operator-a'
        }
        today = datetime.now().strftime('%Y-%m-%d')
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, code TEXT, gps_lat REAL, gps_lng REAL, type TEXT
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, plan_data TEXT,
                    vehicle_days TEXT, spare_parts TEXT, work_order_ids TEXT, version INTEGER, remarks TEXT,
                    period_start TEXT, period_end TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, assignee_id INTEGER, plan_schedule_id INTEGER,
                    generate_date TEXT, status TEXT, completion_rate REAL
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, item_name TEXT,
                    category TEXT, frequency TEXT, result TEXT, calibrator TEXT,
                    calibration_values TEXT, photo_urls TEXT, remark TEXT, execution_status TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER, title TEXT, status TEXT,
                    source TEXT, level TEXT, assignee TEXT, created_at TEXT, sla_deadline TEXT
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, metric TEXT, level TEXT,
                    message TEXT, status TEXT, created_at TEXT
                );
                CREATE TABLE plan_departure_confirmations (
                    schedule_id INTEGER, user_id INTEGER, work_date TEXT,
                    vehicle_confirmed INTEGER, parts_confirmed INTEGER, note TEXT, confirmed_at TEXT
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?)', [(2, '甲运维', 'operator'), (3, '乙运维', 'operator')])
            db.execute('INSERT INTO user_sites VALUES (2, 1)')
            db.execute("INSERT INTO sites VALUES (1, '测试站', 'S-01', 28.6, 115.7, 'water_quality')")
            db.executemany('INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?)', [
                (11, 2, 'approved', '{}', '{}', '[]', '[]', 1, '', today, today),
                (12, 3, 'approved', '{}', '{}', '[]', '[]', 1, '', today, today),
            ])
            db.executemany('INSERT INTO insp_plans VALUES (?,?,?,?,?,?)', [
                (101, 2, 11, today, 'active', 0),
                (102, 3, 12, today, 'active', 0),
            ])
            db.executemany('INSERT INTO insp_plan_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [
                (1001, 101, 1, '甲的检查项', '设备', 'weekly', None, '', '', '[]', '', 'active'),
                (1002, 102, 1, '乙的检查项', '设备', 'weekly', None, '', '', '[]', '', 'active'),
            ])
            db.executemany('INSERT INTO work_orders VALUES (?,?,?,?,?,?,?,?,?,?)', [
                (1, 'WO-A', 1, '甲的工单', 'in_progress', 'manual', 'normal', '甲运维', today, ''),
                (2, 'WO-B', 1, '乙的工单', 'in_progress', 'manual', 'normal', '乙运维', today, ''),
            ])
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    def test_homepage_returns_only_current_operators_tasks_and_workorders(self):
        response = self.client.get('/api/mobile/my-today', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['summary']['total_items'], 1)
        self.assertEqual(response.json['sites'][0]['pending_items'], 1)
        self.assertEqual([item['order_no'] for item in response.json['workorders']], ['WO-A'])


if __name__ == '__main__':
    unittest.main()
