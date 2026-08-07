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
            'admin-token': {'id': 1, 'role': 'admin', 'roles': ['admin'], 'real_name': '管理员', 'username': 'admin'},
        })
        today = datetime.now().strftime('%Y-%m-%d')
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, code TEXT, type TEXT, gps_lat REAL, gps_lng REAL);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE work_orders (
                    order_no TEXT PRIMARY KEY, site_id INTEGER, status TEXT, assignee TEXT,
                    check_in_lat REAL, check_in_lng REAL, check_in_time TEXT, check_in_user TEXT
                );
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE insp_plans (id INTEGER PRIMARY KEY, assignee_id INTEGER, generate_date TEXT, status TEXT, plan_schedule_id INTEGER);
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                    execution_status TEXT, result TEXT, category TEXT, item_name TEXT,
                    frequency TEXT, remark TEXT, check_time TEXT, calibrator TEXT,
                    calibration_values TEXT, photo_urls TEXT, required_photos INTEGER DEFAULT 0,
                    actual_photos INTEGER DEFAULT 0
                );
                CREATE TABLE inspection_checkins (site_id INTEGER, site_name TEXT, user_id INTEGER, user_name TEXT, check_time TEXT, lat REAL, lng REAL);
                CREATE TABLE mobile_idempotency (idempotency_key TEXT PRIMARY KEY, endpoint TEXT, response_json TEXT, created_at TEXT);
                CREATE TABLE timeline_events (source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT);
                INSERT INTO sites (id,name,code,type,gps_lat,gps_lng) VALUES (1, '测试站一', 'S-1', 'water_quality', 28.6800, 115.7300);
                INSERT INTO sites (id,name,code,type,gps_lat,gps_lng) VALUES (2, '测试站二', 'S-2', 'water_quality', 28.6900, 115.7400);
                INSERT INTO sites (id,name,code,type,gps_lat,gps_lng) VALUES (3, '测试站三', 'S-3', 'water_quality', 28.7000, 115.7500);
                INSERT INTO sites (id,name,code,type,gps_lat,gps_lng) VALUES (4, '无坐标站', 'S-4', 'water_quality', NULL, NULL);
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO user_sites VALUES (3, 1);
                INSERT INTO plan_schedules VALUES (10, 'approved');
                INSERT INTO insp_plans VALUES (20, 2, '%s', 'active', 10);
                INSERT INTO insp_plan_items (id,plan_id,site_id,execution_status,result,item_name,category,frequency) VALUES (30, 20, 1, 'active', NULL, '水质检查', '设备', 'daily');
                INSERT INTO insp_plan_items (id,plan_id,site_id,execution_status,result,item_name,category,frequency) VALUES (31, 20, 2, 'active', NULL, '水质检查', '设备', 'daily');
                INSERT INTO insp_plan_items (id,plan_id,site_id,execution_status,result,item_name,category,frequency) VALUES (32, 20, 4, 'active', NULL, '水质检查', '设备', 'daily');
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

    def test_checkin_rejects_site_without_coordinates(self):
        response = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 4, 'lat': 28.6800, 'lng': 115.7300,
        })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn('坐标', response.json['error'])

    def test_calibration_requires_admin_and_explicit_confirmation(self):
        denied = self.client.put('/api/sites/1/calibrate', headers=self.headers('operator-token'), json={
            'lat': 28.6801, 'lng': 115.7301, 'confirm': True, 'site_name': '测试站一',
        })
        self.assertEqual(denied.status_code, 403, denied.json)

        missing_confirmation = self.client.put('/api/sites/1/calibrate', headers=self.headers('admin-token'), json={
            'lat': 28.6801, 'lng': 115.7301, 'site_name': '测试站一',
        })
        self.assertEqual(missing_confirmation.status_code, 409, missing_confirmation.json)

        confirmed = self.client.put('/api/sites/1/calibrate', headers=self.headers('admin-token'), json={
            'lat': 28.6801, 'lng': 115.7301, 'confirm': True, 'site_name': '测试站一',
        })
        self.assertEqual(confirmed.status_code, 200, confirmed.json)

    def test_item_submission_requires_a_same_day_site_checkin(self):
        response = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 31, 'plan_id': 20, 'result': 'normal', 'remark': '现场记录',
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn('打卡', response.json['error'])

    def test_site_tasks_are_scoped_to_user_and_unfinished_carryover(self):
        today = datetime.now().strftime('%Y-%m-%d')
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("INSERT INTO insp_plans VALUES (21, 3, ?, 'active', 10)", (today,))
            db.execute("INSERT INTO insp_plan_items (id,plan_id,site_id,execution_status,result,item_name,category,frequency) VALUES (40,21,1,'active',NULL,'他人检查项','设备','daily')")
            db.execute("INSERT INTO insp_plans VALUES (22, 2, date(?,'-1 day'), 'active', 10)", (today,))
            db.execute("INSERT INTO insp_plan_items (id,plan_id,site_id,execution_status,result,item_name,category,frequency) VALUES (41,22,1,'active',NULL,'结转检查项','设备','daily')")
            db.commit()
        finally:
            db.close()
        response = self.client.get('/api/mobile/site-tasks/1', headers=self.headers('operator-token'))
        self.assertEqual(response.status_code, 200, response.json)
        item_ids = [item['item_id'] for cat in response.json['categories'] for item in cat['items']]
        self.assertIn(30, item_ids)
        self.assertIn(41, item_ids)
        self.assertNotIn(40, item_ids)


if __name__ == '__main__':
    unittest.main()
