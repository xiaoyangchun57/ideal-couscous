import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class MobileCheckinIntegrityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)

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
        app_module._tokens.update({
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': '现场运维'},
            'other-token': {'id': 3, 'role': 'operator', 'real_name': '其他运维'},
        })
        today = datetime.now().strftime('%Y-%m-%d')
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE sites (id INTEGER PRIMARY KEY, gps_lat REAL, gps_lng REAL);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE work_orders (
                    order_no TEXT PRIMARY KEY, site_id INTEGER, status TEXT, assignee TEXT,
                    check_in_lat REAL, check_in_lng REAL, check_in_time TEXT, check_in_user TEXT
                );
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE insp_plans (id INTEGER PRIMARY KEY, assignee_id INTEGER, generate_date TEXT, status TEXT, plan_schedule_id INTEGER);
                CREATE TABLE insp_plan_items (id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, execution_status TEXT, result TEXT);
                CREATE TABLE inspection_checkins (site_id INTEGER, site_name TEXT, user_id INTEGER, user_name TEXT, check_time TEXT, lat REAL, lng REAL);
                CREATE TABLE mobile_idempotency (idempotency_key TEXT PRIMARY KEY, endpoint TEXT, response_json TEXT, created_at TEXT);
                INSERT INTO sites VALUES (1, 28.6800, 115.7300);
                INSERT INTO sites VALUES (2, 28.6900, 115.7400);
                INSERT INTO sites VALUES (3, 28.7000, 115.7500);
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO user_sites VALUES (3, 1);
                INSERT INTO plan_schedules VALUES (10, 'approved');
                INSERT INTO insp_plans VALUES (20, 2, '%s', 'active', 10);
                INSERT INTO insp_plan_items VALUES (30, 20, 1, 'active', NULL);
                INSERT INTO insp_plan_items VALUES (31, 20, 2, 'active', NULL);
                INSERT INTO work_orders VALUES ('WO-1', 1, 'in_progress', '现场运维', NULL, NULL, NULL, NULL);
            ''' % today)
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': 'Bearer ' + token}

    def test_inspection_checkin_requires_assigned_site_and_nearby_position(self):
        remote = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'lat': 30.0, 'lng': 116.0,
        })
        self.assertEqual(remote.status_code, 400, remote.json)

        outside_package = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 3, 'lat': 28.7000, 'lng': 115.7500,
        })
        self.assertEqual(outside_package.status_code, 403, outside_package.json)

        checked = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': '测试站', 'lat': 28.6801, 'lng': 115.7301,
        })
        self.assertEqual(checked.status_code, 200, checked.json)
        self.assertTrue(checked.json['location_verified'])

    def test_workorder_checkin_requires_assignee(self):
        response = self.client.post('/api/mobile/check-in', headers=self.headers('other-token'), json={
            'order_no': 'WO-1', 'lat': 28.6800, 'lng': 115.7300,
        })
        self.assertEqual(response.status_code, 403, response.json)

    def test_item_submission_requires_a_same_day_site_checkin(self):
        response = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 31, 'plan_id': 20, 'result': 'normal', 'remark': '现场记录',
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn('打卡', response.json['error'])


if __name__ == '__main__':
    unittest.main()
