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
                    check_in_lat REAL, check_in_lng REAL, check_in_time TEXT, check_in_user TEXT,
                    created_at TEXT
                );
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE insp_plans (id INTEGER PRIMARY KEY, assignee_id INTEGER, generate_date TEXT, status TEXT, plan_schedule_id INTEGER);
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                    execution_status TEXT, result TEXT, category TEXT, item_name TEXT,
                    frequency TEXT, remark TEXT, check_time TEXT, calibrator TEXT,
                    calibration_values TEXT, photo_urls TEXT, required_photos INTEGER DEFAULT 0,
                    actual_photos INTEGER DEFAULT 0, check_out_time TEXT
                );
                CREATE TABLE inspection_checkins (site_id INTEGER, site_name TEXT, user_id INTEGER, user_name TEXT, check_time TEXT, lat REAL, lng REAL);
                CREATE TABLE photo_capture_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT UNIQUE,
                    user_id INTEGER, site_id INTEGER, plan_id INTEGER, item_id INTEGER,
                    work_order_id INTEGER, issued_at TEXT, expires_at TEXT,
                    used_at TEXT, attachment_id INTEGER, gps_lat REAL, gps_lng REAL,
                    distance_m REAL, rework_required_at TEXT, capture_source TEXT DEFAULT ''
                );
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
                INSERT INTO work_orders VALUES ('WO-1', 1, 'in_progress', '现场运维', NULL, NULL, NULL, NULL, datetime('now','localtime'));
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

        admin_response = self.client.post('/api/mobile/check-in', headers=self.headers('admin-token'), json={
            'order_no': 'WO-1', 'lat': 28.6800, 'lng': 115.7300,
        })
        self.assertEqual(admin_response.status_code, 403, admin_response.json)
        with app_module.get_db() as db:
            row = db.execute(
                "SELECT check_in_lat,check_in_lng,check_in_time,check_in_user FROM work_orders WHERE order_no='WO-1'"
            ).fetchone()
            self.assertEqual(tuple(row), (None, None, None, None))

    def test_same_day_site_checkin_is_reused_by_assigned_workorder_only(self):
        checked = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': '测试站一', 'lat': 28.6801, 'lng': 115.7301,
        })
        self.assertEqual(checked.status_code, 200, checked.json)

        own_orders = self.client.get('/api/workorders', headers=self.headers('operator-token'))
        self.assertEqual(own_orders.status_code, 200, own_orders.json)
        self.assertTrue(own_orders.json[0]['checked_in'])
        self.assertTrue(own_orders.json[0]['effective_check_in_time'])
        self.assertTrue(own_orders.json[0]['can_operate'])

        other_orders = self.client.get('/api/workorders', headers=self.headers('other-token'))
        self.assertEqual(other_orders.status_code, 200, other_orders.json)
        self.assertFalse(other_orders.json[0]['checked_in'])
        self.assertFalse(other_orders.json[0]['can_operate'])

    def test_rework_item_keeps_original_station_arrival(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute('ALTER TABLE insp_plan_items ADD COLUMN review_status INTEGER DEFAULT 0')
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN rework_required_at TEXT DEFAULT ''")
            db.execute('UPDATE insp_plan_items SET review_status=3, rework_required_at=datetime(\'now\',\'localtime\') WHERE id=30')
            db.execute("INSERT INTO inspection_checkins (site_id,site_name,user_id,user_name,check_time,lat,lng) VALUES (1,'测试站一',2,'现场运维',datetime('now','localtime'),28.68,115.73)")
            db.commit()
        finally:
            db.close()

        detail = self.client.get('/api/mobile/execution-plans/20/sites/1',
                                 headers=self.headers('operator-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertTrue(detail.json['site']['checked_in'])
        self.assertFalse(detail.json['rework_checkin_required'])

    def test_checkin_rejects_site_without_coordinates(self):
        with app_module.get_db() as db:
            db.execute('INSERT INTO user_sites VALUES (2, 4)')
        response = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 4, 'lat': 28.6800, 'lng': 115.7300,
        })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn('坐标', response.json['error'])

    def test_calibration_requires_assigned_task_and_explicit_confirmation(self):
        operator_confirmed = self.client.put('/api/sites/1/calibrate', headers=self.headers('operator-token'), json={
            'lat': 28.6801, 'lng': 115.7301, 'confirm': True, 'site_name': '测试站一',
        })
        self.assertEqual(operator_confirmed.status_code, 200, operator_confirmed.json)

        denied = self.client.put('/api/sites/3/calibrate', headers=self.headers('operator-token'), json={
            'lat': 28.7001, 'lng': 115.7501, 'confirm': True, 'site_name': '测试站三',
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

    def test_photo_capture_session_rejects_a_stale_ordinary_checkin(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO inspection_checkins
                (site_id,site_name,user_id,user_name,check_time,lat,lng)
                VALUES (1,'测试站一',2,'现场运维',datetime('now','localtime','-1 day'),28.68,115.73)""")
        response = self.client.post('/api/mobile/photo-capture-session',
            headers=self.headers('operator-token'), json={
                'site_id': 1, 'plan_id': 20, 'item_id': 30,
                'gps_lat': 28.6801, 'gps_lng': 115.7301,
            })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'EVIDENCE_CHECKIN_REQUIRED')

    def test_photo_capture_session_reuses_only_the_rejected_items_execution_window(self):
        with app_module.get_db() as db:
            db.execute('ALTER TABLE insp_plan_items ADD COLUMN review_status INTEGER DEFAULT 0')
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN rework_required_at TEXT DEFAULT ''")
            db.execute("UPDATE insp_plans SET generate_date=date('now','localtime','-1 day') WHERE id=20")
            db.execute("""UPDATE insp_plan_items
                SET result='normal', review_status=3, evidence_status='supplement_required',
                    rework_required_at=datetime('now','localtime') WHERE id=30""")
            db.execute("""INSERT INTO inspection_checkins
                (site_id,site_name,user_id,user_name,check_time,lat,lng)
                VALUES (1,'测试站一',2,'现场运维',datetime('now','localtime','-20 hours'),28.68,115.73)""")
        response = self.client.post('/api/mobile/photo-capture-session',
            headers=self.headers('operator-token'), json={
                'site_id': 1, 'plan_id': 20, 'item_id': 30,
                'gps_lat': 28.6801, 'gps_lng': 115.7301,
            })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['capture_session'])
        with app_module.get_db() as db:
            session = db.execute('SELECT * FROM photo_capture_sessions').fetchone()
            checkins = db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0]
        self.assertEqual(session['capture_source'], 'camera')
        self.assertTrue(session['rework_required_at'])
        self.assertLess(session['distance_m'], 500)
        self.assertEqual(checkins, 1)

    def test_site_checkout_requires_completion_and_closes_the_loop(self):
        checked = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': '测试站点一', 'lat': 28.6801, 'lng': 115.7301,
        })
        self.assertEqual(checked.status_code, 200, checked.json)
        before_complete = self.client.post('/api/mobile/execution-plans/20/sites/1/check-out',
            headers=self.headers('operator-token'), json={'lat': 28.6801, 'lng': 115.7301})
        self.assertEqual(before_complete.status_code, 400, before_complete.json)

        db = sqlite3.connect(self.db_path)
        try:
            db.execute("UPDATE insp_plan_items SET result='normal' WHERE id=30")
            db.commit()
        finally:
            db.close()
        response = self.client.post('/api/mobile/execution-plans/20/sites/1/check-out',
            headers=self.headers('operator-token'), json={'lat': 28.6801, 'lng': 115.7301})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['check_out_time'])
        replay = self.client.post('/api/mobile/execution-plans/20/sites/1/check-out',
            headers=self.headers('operator-token'), json={'lat': 28.6801, 'lng': 115.7301})
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertTrue(replay.json['already_closed'])
        db = sqlite3.connect(self.db_path)
        try:
            self.assertIsNotNone(db.execute('SELECT check_out_time FROM insp_plan_items WHERE id=30').fetchone()[0])
        finally:
            db.close()

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

    def test_scheduled_and_legacy_carryover_remain_checkin_eligible(self):
        """Every carryover visible on the mobile home page must remain executable at the station."""
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("INSERT INTO sites VALUES (5, '历史结转站', 'S-5', 'water_quality', 28.7100, 115.7600)")
            db.execute("INSERT INTO sites VALUES (6, '兼容结转站', 'S-6', 'water_quality', 28.7200, 115.7700)")
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(2, 5), (2, 6)])
            db.execute("INSERT INTO insp_plans VALUES (23, 2, date('now','-1 day'), 'active', 10)")
            db.execute("INSERT INTO insp_plans VALUES (24, 2, date('now','-1 day'), 'active', NULL)")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,execution_status,result,item_name,category,frequency)
                VALUES (42,23,5,'active',NULL,'结转检查项','设备','daily')""")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,execution_status,result,item_name,category,frequency)
                VALUES (43,24,6,'active',NULL,'兼容结转检查项','设备','daily')""")
            db.commit()
        finally:
            db.close()

        for site_id, site_name, lat, lng in [
            (5, '历史结转站', 28.7101, 115.7601),
            (6, '兼容结转站', 28.7201, 115.7701),
        ]:
            detail = self.client.get(f'/api/mobile/site-tasks/{site_id}', headers=self.headers('operator-token'))
            self.assertEqual(detail.status_code, 200, detail.json)
            self.assertTrue(detail.json['site']['can_check_in'])
            self.assertTrue(detail.json['site']['has_carryover'])
            self.assertEqual(detail.json['site']['carryover_items'], 1)
            self.assertEqual(detail.json['site']['task_state'], 'carryover')

            checked = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
                'site_id': site_id, 'site_name': site_name, 'lat': lat, 'lng': lng,
            })
            self.assertEqual(checked.status_code, 200, checked.json)

    def test_revoked_site_scope_blocks_scheduled_and_legacy_field_actions(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO insp_plans VALUES (24, 2, date('now'), 'active', NULL)")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,execution_status,result,item_name,category,frequency)
                VALUES (43,24,1,'active',NULL,'旧计划检查项','设备','daily')""")
            db.executescript('''
                CREATE TABLE reagents (id INTEGER PRIMARY KEY, name TEXT, unit TEXT);
                CREATE TABLE reagent_inventory (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_id INTEGER,
                    current_qty REAL, qc_status TEXT, expected_duration_days INTEGER,
                    last_replaced_at TEXT, updated_at TEXT
                );
                CREATE TABLE reagent_records (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_name TEXT, usage_date TEXT,
                    replacement_date TEXT, operator TEXT, notes TEXT, old_qty REAL, new_qty REAL,
                    plan_id INTEGER
                );
                INSERT INTO reagents VALUES (1, '测试试剂', '瓶');
                INSERT INTO reagent_inventory VALUES (1, 1, 1, 2, 'pending', 30, '', '');
                DELETE FROM user_sites WHERE user_id=2 AND site_id=1;
            ''')

        for plan_id in (20, 24):
            detail = self.client.get(f'/api/mobile/execution-plans/{plan_id}/sites/1',
                                     headers=self.headers('operator-token'))
            self.assertEqual(detail.status_code, 404, detail.json)

            checked = self.client.post('/api/mobile/check-in',
                headers=self.headers('operator-token'), json={
                    'site_id': 1, 'plan_id': plan_id,
                    'lat': 28.6801, 'lng': 115.7301,
                })
            self.assertEqual(checked.status_code, 403, checked.json)

            replacement = self.client.post(
                f'/api/mobile/execution-plans/{plan_id}/sites/1/reagent-replacements',
                headers=self.headers('operator-token'),
                json={'reagent_id': 1, 'new_qty': 5})
            self.assertEqual(replacement.status_code, 404, replacement.json)

        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET result='normal', check_out_time=datetime('now','localtime')
                WHERE plan_id IN (20,24) AND site_id=1""")
            before = {
                'plans': [tuple(row) for row in db.execute(
                    'SELECT id,status FROM insp_plans WHERE id IN (20,24) ORDER BY id').fetchall()],
                'checkins': db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0],
                'records': db.execute('SELECT COUNT(*) FROM reagent_records').fetchone()[0],
                'quantity': db.execute(
                    'SELECT current_qty FROM reagent_inventory WHERE id=1').fetchone()[0],
            }

        for plan_id in (20, 24):
            checkout = self.client.post(
                f'/api/mobile/execution-plans/{plan_id}/sites/1/check-out',
                headers=self.headers('operator-token'),
                json={'lat': 28.6801, 'lng': 115.7301})
            self.assertEqual(checkout.status_code, 403, checkout.json)

        with app_module.get_db() as db:
            after = {
                'plans': [tuple(row) for row in db.execute(
                    'SELECT id,status FROM insp_plans WHERE id IN (20,24) ORDER BY id').fetchall()],
                'checkins': db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0],
                'records': db.execute('SELECT COUNT(*) FROM reagent_records').fetchone()[0],
                'quantity': db.execute(
                    'SELECT current_qty FROM reagent_inventory WHERE id=1').fetchone()[0],
            }
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
