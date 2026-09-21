import os
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock
from contextlib import contextmanager
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class MobileCheckinIntegrityTest(unittest.TestCase):
    def test_300m_geofence_checkin_workorder_capture_and_checkout_boundaries(self):
        self.assertEqual(app_module.SITE_GEOFENCE_M, 300)
        self.assertEqual(app_module.GPS_DEVIATION_M, app_module.SITE_GEOFENCE_M)
        with app_module.get_db() as db:
            db.executescript("""CREATE TABLE vehicle_applications
                (id INTEGER PRIMARY KEY, vehicle_id INTEGER, status TEXT, no_vehicle_required INTEGER,
                 vehicle_exception_reason TEXT, work_order_no TEXT, applicant_id INTEGER);
                INSERT INTO vehicle_applications VALUES (1,NULL,'approved',1,'步行','WO-1',2);""")
        headers = self.headers('operator-token')
        payload = {'site_id': 1, 'lat': 28.68, 'lng': 115.73}
        for distance in (300.01, 450, None):
            with self.subTest(distance=distance), mock.patch.object(app_module, '_checkin_distance_to_site', return_value=distance):
                for extra in ({}, {'order_no': 'WO-1'}):
                    response = self.client.post('/api/mobile/check-in', headers=headers, json=dict(payload, **extra))
                    self.assertIn(response.status_code, (400, 409), response.json)
                capture = self.client.post('/api/mobile/photo-capture-session', headers=headers,
                    json={'site_id': 1, 'plan_id': 20, 'item_id': 30, 'gps_lat': 28.68, 'gps_lng': 115.73})
                self.assertEqual(capture.status_code, 409, capture.json)
                with app_module.get_db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0], 0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0], 0)
                    self.assertIsNone(db.execute("SELECT check_in_time FROM work_orders WHERE order_no='WO-1'").fetchone()[0])
        for distance in (299.9, 300):
            with mock.patch.object(app_module, '_checkin_distance_to_site', return_value=distance):
                response = self.client.post('/api/mobile/check-in', headers=headers, json=payload)
                self.assertEqual(response.status_code, 200, response.json)
                workorder = self.client.post('/api/mobile/check-in', headers=headers, json=dict(payload, order_no='WO-1'))
                self.assertEqual(workorder.status_code, 200, workorder.json)
                capture = self.client.post('/api/mobile/photo-capture-session', headers=headers,
                    json={'site_id': 1, 'plan_id': 20, 'item_id': 30, 'gps_lat': 28.68, 'gps_lng': 115.73})
                self.assertEqual(capture.status_code, 200, capture.json)
        with app_module.get_db() as db:
            db.execute("UPDATE insp_plan_items SET result='normal' WHERE id=30")
        for distance in (300.01, None):
            with mock.patch.object(app_module, '_checkin_distance_to_site', return_value=distance):
                checkout = self.client.post('/api/mobile/execution-plans/20/sites/1/check-out', headers=headers, json=payload)
                self.assertIn(checkout.status_code, (400, 409), checkout.json)
                with app_module.get_db() as db:
                    self.assertIsNone(db.execute('SELECT check_out_time FROM insp_plan_items WHERE id=30').fetchone()[0])
        with mock.patch.object(app_module, '_checkin_distance_to_site', return_value=300):
            checkout = self.client.post('/api/mobile/execution-plans/20/sites/1/check-out', headers=headers, json=payload)
            self.assertEqual(checkout.status_code, 200, checkout.json)

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
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, site_id INTEGER, status TEXT, assignee TEXT,
                    check_in_lat REAL, check_in_lng REAL, check_in_time TEXT, check_in_user TEXT,
                    created_at TEXT, source TEXT
                );
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE insp_plans (id INTEGER PRIMARY KEY, assignee_id INTEGER, generate_date TEXT, status TEXT, plan_schedule_id INTEGER);
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                    execution_status TEXT, result TEXT, category TEXT, item_name TEXT,
                    frequency TEXT, remark TEXT, check_time TEXT, calibrator TEXT,
                    calibration_values TEXT, photo_urls TEXT, required_photos INTEGER DEFAULT 0,
                    actual_photos INTEGER DEFAULT 0, check_out_time TEXT, completed_at TEXT
                );
                CREATE TABLE inspection_checkins (site_id INTEGER, site_name TEXT, user_id INTEGER, user_name TEXT, check_time TEXT, lat REAL, lng REAL);
                CREATE TABLE photo_capture_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT UNIQUE,
                    user_id INTEGER, site_id INTEGER, plan_id INTEGER, item_id INTEGER,
                    work_order_id INTEGER, issued_at TEXT, expires_at TEXT,
                    used_at TEXT, attachment_id INTEGER, gps_lat REAL, gps_lng REAL,
                    distance_m REAL, rework_required_at TEXT, capture_source TEXT DEFAULT ''
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, item_id INTEGER, source_type TEXT, source_id INTEGER,
                    site_id INTEGER, uploader_id INTEGER, uploader_name TEXT,
                    is_deleted INTEGER DEFAULT 0, created_at TEXT, extra_json TEXT DEFAULT '{}',
                    recognized_category TEXT DEFAULT '', category TEXT DEFAULT ''
                );
                CREATE TABLE mobile_idempotency (idempotency_key TEXT PRIMARY KEY, endpoint TEXT, response_json TEXT, created_at TEXT);
                CREATE TABLE timeline_events (source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT);
                INSERT INTO sites (id,name,code,type,gps_lat,gps_lng) VALUES (1, '测试站一', 'S-1', 'water_quality', 28.6800, 115.7300);
                INSERT INTO sites (id,name,code,type,gps_lat,gps_lng) VALUES (2, '测试站二', 'S-2', 'water_quality', 28.6900, 115.7400);
                INSERT INTO sites (id,name,code,type,gps_lat,gps_lng) VALUES (3, '测试站三', 'S-3', 'water_quality', 28.7000, 115.7500);
                INSERT INTO sites (id,name,code,type,gps_lat,gps_lng) VALUES (4, '无坐标站', 'S-4', 'water_quality', NULL, NULL);
                INSERT INTO users VALUES (1, '管理员');
                INSERT INTO users VALUES (2, '现场运维');
                INSERT INTO users VALUES (3, '其他运维');
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO user_sites VALUES (3, 1);
                INSERT INTO plan_schedules VALUES (10, 'approved');
                INSERT INTO insp_plans VALUES (20, 2, '%s', 'active', 10);
                INSERT INTO insp_plan_items (id,plan_id,site_id,execution_status,result,item_name,category,frequency) VALUES (30, 20, 1, 'active', NULL, '水质检查', '设备', 'daily');
                INSERT INTO insp_plan_items (id,plan_id,site_id,execution_status,result,item_name,category,frequency) VALUES (31, 20, 2, 'active', NULL, '水质检查', '设备', 'daily');
                INSERT INTO insp_plan_items (id,plan_id,site_id,execution_status,result,item_name,category,frequency) VALUES (32, 20, 4, 'active', NULL, '水质检查', '设备', 'daily');
                INSERT INTO work_orders VALUES (1, 'WO-1', 1, 'in_progress', '现场运维', NULL, NULL, NULL, NULL, datetime('now','localtime'), 'manual');
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

    def _prepare_plan_vehicle(self, *, active_use=False):
        today = datetime.now().strftime('%Y-%m-%d')
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        with app_module.get_db() as db:
            db.execute("ALTER TABLE plan_schedules ADD COLUMN vehicle_days TEXT DEFAULT '{}'")
            db.execute('UPDATE plan_schedules SET vehicle_days=? WHERE id=10',
                       (json.dumps({today: 7}),))
            db.executescript('''
                CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY, status TEXT
                );
                CREATE TABLE vehicle_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_id INTEGER, document_type TEXT, valid_until TEXT
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER,
                    start_at TEXT, end_at TEXT, reason TEXT, status TEXT
                );
                CREATE TABLE vehicle_use_records (
                    id INTEGER PRIMARY KEY, application_id INTEGER,
                    returned_at TEXT, status TEXT
                );
                INSERT INTO vehicles (id,status) VALUES (7,'idle');
            ''')
            db.execute('''INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (50,7,2,?,?,?,'approved')''', (
                    today + ' 08:00:00', tomorrow + ' 18:00:00', '巡检计划#10用车',
                ))
            if active_use:
                db.execute("""INSERT INTO vehicle_use_records
                    (id,application_id,returned_at,status)
                    VALUES (60,50,NULL,'checked_out')""")
        return today, tomorrow

    def test_inspection_checkin_requires_assigned_site_and_nearby_position(self):
        remote = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'lat': 30.0, 'lng': 116.0,
        })
        self.assertEqual(remote.status_code, 400, remote.json)
        self.assertEqual(remote.json['code'], 'SITE_GEOFENCE_EXCEEDED')

        outside_package = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 3, 'lat': 28.7000, 'lng': 115.7500,
        })
        self.assertEqual(outside_package.status_code, 403, outside_package.json)

        checked = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': '测试站', 'lat': 28.6801, 'lng': 115.7301,
        })
        self.assertEqual(checked.status_code, 200, checked.json)
        self.assertTrue(checked.json['location_verified'])

    def test_inspection_vehicle_arrival_gate_blocks_direct_checkin_until_current_trip_exists(self):
        today, tomorrow = self._prepare_plan_vehicle()
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        headers = self.headers('operator-token')
        payload = {
            'site_id': 1, 'site_name': '测试站一', 'plan_id': 20,
            'lat': 28.6801, 'lng': 115.7301,
        }
        blocked_payload = dict(payload, _idempotency_key='blocked-vehicle-gate')
        blocked = self.client.post('/api/mobile/check-in', headers=headers, json=blocked_payload)
        self.assertEqual(blocked.status_code, 409, blocked.json)
        self.assertEqual(blocked.json['code'], 'VEHICLE_CHECKOUT_REQUIRED')
        legacy_bypass = self.client.post('/api/mobile/check-in', headers=headers, json={
            key: value for key, value in payload.items() if key != 'plan_id'
        })
        self.assertEqual(legacy_bypass.status_code, 409, legacy_bypass.json)
        self.assertEqual(legacy_bypass.json['code'], 'VEHICLE_CHECKOUT_REQUIRED')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0], 0)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM mobile_idempotency
                WHERE idempotency_key='2:blocked-vehicle-gate' AND endpoint='check-in'""").fetchone()[0], 0)

        exact = self.client.get('/api/mobile/execution-plans/20/sites/1', headers=headers)
        self.assertEqual(exact.status_code, 200, exact.json)
        self.assertFalse(exact.json['arrival_gate']['allowed'])
        self.assertEqual(exact.json['arrival_gate']['code'], 'VEHICLE_CHECKOUT_REQUIRED')

        with app_module.get_db() as db:
            db.execute("""INSERT INTO vehicle_use_records
                (id,application_id,returned_at,status) VALUES (60,50,NULL,'checked_out')""")
        exact = self.client.get('/api/mobile/execution-plans/20/sites/1', headers=headers)
        self.assertTrue(exact.json['arrival_gate']['allowed'])
        allowed = self.client.post('/api/mobile/check-in', headers=headers, json=payload)
        self.assertEqual(allowed.status_code, 200, allowed.json)

        with app_module.get_db() as db:
            db.execute('DELETE FROM inspection_checkins')
            db.execute("UPDATE vehicle_use_records SET returned_at=datetime('now'),status='returned' WHERE id=60")
        returned = self.client.post('/api/mobile/check-in', headers=headers, json=payload)
        self.assertEqual(returned.status_code, 409, returned.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0], 0)
            db.execute("UPDATE vehicle_applications SET status='cancelled' WHERE id=50")
        invalid = self.client.post('/api/mobile/check-in', headers=headers, json=payload)
        self.assertEqual(invalid.status_code, 409, invalid.json)
        self.assertEqual(invalid.json['code'], 'VEHICLE_APPLICATION_INVALID')

        with app_module.get_db() as db:
            db.execute("UPDATE plan_schedules SET vehicle_days='{}' WHERE id=10")
        no_vehicle = self.client.get('/api/mobile/execution-plans/20/sites/1', headers=headers)
        self.assertTrue(no_vehicle.json['arrival_gate']['allowed'])

        with app_module.get_db() as db:
            db.execute('UPDATE insp_plans SET generate_date=? WHERE id=20', (yesterday,))
            db.execute('UPDATE plan_schedules SET vehicle_days=? WHERE id=10',
                       (json.dumps({yesterday: 7}),))
            db.execute("UPDATE vehicle_applications SET status='approved',start_at=?,end_at=? WHERE id=50",
                       (yesterday + ' 08:00:00', tomorrow + ' 18:00:00'))
            db.execute("UPDATE vehicle_use_records SET returned_at=NULL,status='checked_out' WHERE id=60")
        carryover = self.client.get('/api/mobile/execution-plans/20/sites/1', headers=headers)
        self.assertTrue(carryover.json['arrival_gate']['allowed'])

        with app_module.get_db() as db:
            db.execute('UPDATE vehicle_applications SET end_at=? WHERE id=50',
                       (yesterday + ' 18:00:00',))
        expired = self.client.get('/api/mobile/execution-plans/20/sites/1', headers=headers)
        self.assertFalse(expired.json['arrival_gate']['allowed'])
        self.assertEqual(expired.json['arrival_gate']['code'], 'VEHICLE_EXTENSION_REQUIRED')

    def test_vehicle_state_cannot_write_between_gate_read_and_checkin_insert(self):
        self._prepare_plan_vehicle(active_use=True)
        original_gate = app_module._inspection_arrival_resource_state
        gate_read = threading.Event()
        release_gate = threading.Event()
        mutation_started = threading.Event()
        mutation_finished = threading.Event()
        response_box = {}

        def paused_gate(db, plan_id, user_id):
            result = original_gate(db, plan_id, user_id)
            gate_read.set()
            if not release_gate.wait(3):
                raise RuntimeError('test did not release the arrival gate')
            return result

        def request_checkin():
            client = app_module.app.test_client()
            response_box['response'] = client.post(
                '/api/mobile/check-in', headers=self.headers('operator-token'), json={
                    'site_id': 1, 'site_name': '测试站一', 'plan_id': 20,
                    'lat': 28.6801, 'lng': 115.7301,
                })

        def return_vehicle():
            db = sqlite3.connect(self.db_path, timeout=3)
            try:
                mutation_started.set()
                db.execute("""UPDATE vehicle_use_records
                    SET returned_at=datetime('now'),status='returned' WHERE id=60""")
                db.commit()
                mutation_finished.set()
            finally:
                db.close()

        app_module._inspection_arrival_resource_state = paused_gate
        request_thread = threading.Thread(target=request_checkin)
        mutation_thread = threading.Thread(target=return_vehicle)
        try:
            request_thread.start()
            self.assertTrue(gate_read.wait(2), 'check-in must reach the authoritative gate')
            mutation_thread.start()
            self.assertTrue(mutation_started.wait(1))
            self.assertFalse(mutation_finished.wait(0.2),
                             'vehicle mutation must wait for the check-in transaction')
            release_gate.set()
            request_thread.join(3)
            mutation_thread.join(3)
            self.assertFalse(request_thread.is_alive())
            self.assertFalse(mutation_thread.is_alive())
        finally:
            release_gate.set()
            app_module._inspection_arrival_resource_state = original_gate
            request_thread.join(3)
            mutation_thread.join(3)

        response = response_box['response']
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0], 1)
            use = db.execute(
                'SELECT status,returned_at FROM vehicle_use_records WHERE id=60').fetchone()
            self.assertEqual(use['status'], 'returned')
            self.assertTrue(use['returned_at'])

    def test_same_idempotency_key_concurrently_creates_one_inspection_checkin(self):
        original_get = app_module._mobile_idempotency_get
        first_cache_read = threading.Event()
        second_cache_read = threading.Event()
        release_first = threading.Event()
        calls_lock = threading.Lock()
        responses = {}
        call_count = 0

        def paused_get(db, key, endpoint):
            nonlocal call_count
            result = original_get(db, key, endpoint)
            with calls_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_cache_read.set()
                if not release_first.wait(3):
                    raise RuntimeError('test did not release the first idempotency read')
            else:
                second_cache_read.set()
            return result

        def request_checkin(name):
            client = app_module.app.test_client()
            responses[name] = client.post(
                '/api/mobile/check-in', headers=self.headers('operator-token'), json={
                    'site_id': 1, 'site_name': '测试站一',
                    'lat': 28.6801, 'lng': 115.7301,
                    '_idempotency_key': 'concurrent-checkin',
                })

        app_module._mobile_idempotency_get = paused_get
        first = threading.Thread(target=request_checkin, args=('first',))
        second = threading.Thread(target=request_checkin, args=('second',))
        try:
            first.start()
            self.assertTrue(first_cache_read.wait(2))
            second.start()
            self.assertFalse(second_cache_read.wait(0.2),
                             'the second request must not read idempotency before the first commits')
            release_first.set()
            first.join(3)
            second.join(3)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
        finally:
            release_first.set()
            app_module._mobile_idempotency_get = original_get
            first.join(3)
            second.join(3)

        self.assertEqual(responses['first'].status_code, 200, responses['first'].json)
        self.assertEqual(responses['second'].status_code, 200, responses['second'].json)
        self.assertEqual(responses['first'].json, responses['second'].json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0], 1)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM mobile_idempotency
                WHERE idempotency_key='2:concurrent-checkin' AND endpoint='check-in'""").fetchone()[0], 1)

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

    def test_calibration_requires_authorized_site_and_explicit_confirmation(self):
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

    def test_calibration_all_scoped_roles_without_today_task_and_failure_zero_write(self):
        with app_module.get_db() as db:
            db.execute('DELETE FROM insp_plan_items')
            db.execute('DELETE FROM insp_plans')
        for role in ('operator', 'reviewer', 'manager'):
            token = role + '-scoped'
            app_module._tokens[token] = {'id': 2, 'role': role, 'username': role}
            response = self.client.put('/api/sites/1/calibrate', headers=self.headers(token), json={
                'lat': 28.681, 'lng': 115.731, 'confirm': True, 'site_name': '测试站一'})
            self.assertEqual(response.status_code, 200, response.json)
            projected = self.client.get('/api/mobile/site-tasks/1', headers=self.headers(token))
            self.assertTrue(projected.json['site']['can_calibrate'])
        with app_module.get_db() as db:
            before = tuple(db.execute('SELECT gps_lat,gps_lng FROM sites WHERE id=1').fetchone())
            audits = db.execute("SELECT COUNT(*) FROM timeline_events WHERE event_type='calibrated'").fetchone()[0]
        for site_id, payload, status in (
            (3, {'lat': 28.68, 'lng': 115.73, 'confirm': True}, 403),
            (1, {'lat': 91, 'lng': 115.73, 'confirm': True}, 400),
            (1, {'lat': 28.68, 'lng': 115.73, 'confirm': True, 'site_name': '错误站'}, 409),
            (1, {'lat': 28.68, 'lng': 115.73}, 409),
        ):
            response = self.client.put(f'/api/sites/{site_id}/calibrate', headers=self.headers('operator-token'), json=payload)
            self.assertEqual(response.status_code, status, response.json)
        with app_module.get_db() as db:
            self.assertEqual(tuple(db.execute('SELECT gps_lat,gps_lng FROM sites WHERE id=1').fetchone()), before)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE event_type='calibrated'").fetchone()[0], audits)

    def test_item_submission_requires_a_same_day_site_checkin(self):
        response = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 31, 'plan_id': 20, 'result': 'normal', 'remark': '现场记录',
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn('打卡', response.json['error'])

    def test_photo_minimum_and_maximum_fail_before_result_review_or_binding_writes(self):
        with app_module.get_db() as db:
            db.execute('UPDATE insp_plan_items SET required_photos=4 WHERE id=30')
            db.execute("INSERT INTO inspection_checkins(site_id,user_id,check_time) VALUES (1,2,datetime('now','localtime'))")
        for count in (3,7):
            response = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
                'item_id':30, 'plan_id':20, 'result':'normal', 'remark':'现场记录',
                'photo_urls':json.dumps([f'/uploads/test-{index}.jpg' for index in range(count)]),
                '_idempotency_key':f'photo-limit-{count}'})
            self.assertEqual(response.status_code,400,response.json)
            with app_module.get_db() as db:
                self.assertEqual(tuple(db.execute('SELECT result,check_time,photo_urls,actual_photos FROM insp_plan_items WHERE id=30').fetchone()),(None,None,None,0))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM mobile_idempotency').fetchone()[0],0)

    def test_submit_item_consumes_calibration_only_for_qaqc_category(self):
        with app_module.get_db() as db:
            db.execute('ALTER TABLE insp_plan_items ADD COLUMN review_status INTEGER DEFAULT 0')
            db.execute('ALTER TABLE insp_plans ADD COLUMN completion_rate REAL DEFAULT 0')
        checked = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': '测试站一', 'lat': 28.6801, 'lng': 115.7301,
        })
        self.assertEqual(checked.status_code, 200, checked.json)

        calibration_only = self.client.post('/api/mobile/submit-item',
            headers=self.headers('operator-token'), json={
                'item_id': 30, 'plan_id': 20, 'result': 'normal',
                'calibrator': '伪造校准人', 'calibration_values': '7.00',
                '_idempotency_key': 'ordinary-calibration-only',
            })
        self.assertEqual(calibration_only.status_code, 400, calibration_only.json)
        with app_module.get_db() as db:
            ordinary = db.execute("""SELECT result,check_time,calibrator,calibration_values
                FROM insp_plan_items WHERE id=30""").fetchone()
            writes = db.execute("""SELECT COUNT(*) FROM mobile_idempotency
                WHERE endpoint='submit-item'""").fetchone()[0]
        self.assertEqual(tuple(ordinary), (None, None, None, None))
        self.assertEqual(writes, 0, 'invalid ordinary submission must be zero-write')

        with app_module.get_db() as db:
            db.executemany("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,execution_status,result,item_name,category,frequency)
                VALUES (?,?,1,'active',NULL,?,?,'daily')""", [
                    (33, 20, 'pH 标准液校准', 'qaqc_calibration'),
                    (34, 20, '剩余常规项', '设备'),
                ])

        ordinary_with_remark = self.client.post('/api/mobile/submit-item',
            headers=self.headers('operator-token'), json={
                'item_id': 30, 'plan_id': 20, 'result': 'normal', 'remark': '现场记录正常',
                'calibrator': '伪造校准人', 'calibration_values': '7.00',
                '_idempotency_key': 'ordinary-calibration-ignored',
            })
        self.assertEqual(ordinary_with_remark.status_code, 200, ordinary_with_remark.json)
        with app_module.get_db() as db:
            ordinary = db.execute("""SELECT result,remark,calibrator,calibration_values
                FROM insp_plan_items WHERE id=30""").fetchone()
        self.assertEqual(tuple(ordinary), ('normal', '现场记录正常', None, None))

        calibration = self.client.post('/api/mobile/submit-item',
            headers=self.headers('operator-token'), json={
                'item_id': 33, 'plan_id': 20, 'result': 'normal',
                'calibrator': '王工', 'calibration_values': '7.00',
                '_idempotency_key': 'qaqc-calibration',
            })
        self.assertEqual(calibration.status_code, 200, calibration.json)
        with app_module.get_db() as db:
            qaqc = db.execute("""SELECT result,calibrator,calibration_values
                FROM insp_plan_items WHERE id=33""").fetchone()
        self.assertEqual(tuple(qaqc), ('normal', '王工', '7.00'))

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
        self.assertLess(session['distance_m'], 300)
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

    def test_checkout_closes_shared_site_visit_until_a_new_checkin(self):
        today = datetime.now().strftime('%Y-%m-%d')
        with app_module.get_db() as db:
            db.execute("INSERT INTO insp_plans VALUES (21, 2, ?, 'active', 10)", (today,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,execution_status,result,item_name,category,frequency)
                VALUES (40,21,1,'active',NULL,'第二计划检查项','设备','daily')""")

        checked = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': '测试站一', 'lat': 28.6801, 'lng': 115.7301,
        })
        self.assertEqual(checked.status_code, 200, checked.json)
        for plan_id in (20, 21):
            detail = self.client.get(f'/api/mobile/execution-plans/{plan_id}/sites/1',
                                     headers=self.headers('operator-token'))
            self.assertEqual(detail.status_code, 200, detail.json)
            self.assertTrue(detail.json['checked_in'])

        with app_module.get_db() as db:
            db.execute("UPDATE insp_plan_items SET result='normal' WHERE id=30")
        closed = self.client.post('/api/mobile/execution-plans/20/sites/1/check-out',
            headers=self.headers('operator-token'), json={'lat': 28.6801, 'lng': 115.7301})
        self.assertEqual(closed.status_code, 200, closed.json)
        replay = self.client.post('/api/mobile/execution-plans/20/sites/1/check-out',
            headers=self.headers('operator-token'), json={'lat': 28.6801, 'lng': 115.7301})
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertTrue(replay.json['already_closed'])

        detail = self.client.get('/api/mobile/execution-plans/21/sites/1',
                                 headers=self.headers('operator-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertFalse(detail.json['checked_in'])
        site_tasks = self.client.get('/api/mobile/site-tasks/1',
                                     headers=self.headers('operator-token'))
        self.assertEqual(site_tasks.status_code, 200, site_tasks.json)
        self.assertFalse(site_tasks.json['site']['checked_in'])

        with app_module.get_db() as db:
            before = {
                'item': tuple(db.execute(
                    'SELECT result,check_time,photo_urls FROM insp_plan_items WHERE id=40').fetchone()),
                'sessions': db.execute('SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0],
                'attachments': db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0],
                'workorder': tuple(db.execute(
                    "SELECT status,check_in_time FROM work_orders WHERE order_no='WO-1'").fetchone()),
            }
        submitted = self.client.post('/api/mobile/submit-item',
            headers=self.headers('operator-token'), json={
                'item_id': 40, 'plan_id': 21, 'result': 'normal', 'remark': '不得写入',
            })
        self.assertEqual(submitted.status_code, 400, submitted.json)
        self.assertIn('打卡', submitted.json['error'])
        capture = self.client.post('/api/mobile/photo-capture-session',
            headers=self.headers('operator-token'), json={
                'site_id': 1, 'plan_id': 21, 'item_id': 40,
                'gps_lat': 28.6801, 'gps_lng': 115.7301,
            })
        self.assertEqual((capture.status_code, capture.json['code']),
                         (409, 'EVIDENCE_CHECKIN_REQUIRED'))
        workorder_capture = self.client.post('/api/mobile/photo-capture-session',
            headers=self.headers('operator-token'), json={
                'site_id': 1, 'order_no': 'WO-1',
                'gps_lat': 28.6801, 'gps_lng': 115.7301,
            })
        self.assertEqual((workorder_capture.status_code, workorder_capture.json['code']),
                         (409, 'EVIDENCE_CHECKIN_REQUIRED'))
        with app_module.get_db() as db:
            after = {
                'item': tuple(db.execute(
                    'SELECT result,check_time,photo_urls FROM insp_plan_items WHERE id=40').fetchone()),
                'sessions': db.execute('SELECT COUNT(*) FROM photo_capture_sessions').fetchone()[0],
                'attachments': db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0],
                'workorder': tuple(db.execute(
                    "SELECT status,check_in_time FROM work_orders WHERE order_no='WO-1'").fetchone()),
            }
            self.assertEqual(after, before)

            checkout_time = db.execute(
                'SELECT check_out_time FROM insp_plan_items WHERE id=30').fetchone()[0]
            db.execute("UPDATE inspection_checkins SET check_time=datetime(?,'-2 minutes')", (checkout_time,))
            db.execute("UPDATE insp_plan_items SET check_out_time=datetime(?,'-1 minute') WHERE id=30",
                       (checkout_time,))
        reopened = self.client.post('/api/mobile/check-in',
            headers=self.headers('operator-token'), json={
                'site_id': 1, 'site_name': '测试站一', 'lat': 28.6801, 'lng': 115.7301,
            })
        self.assertEqual(reopened.status_code, 200, reopened.json)
        detail = self.client.get('/api/mobile/execution-plans/21/sites/1',
                                 headers=self.headers('operator-token'))
        self.assertTrue(detail.json['checked_in'])

        with app_module.get_db() as db:
            latest_checkin = db.execute("""SELECT check_time FROM inspection_checkins
                WHERE user_id=2 AND site_id=1 ORDER BY datetime(check_time) DESC LIMIT 1""").fetchone()[0]
            db.execute("INSERT INTO insp_plans VALUES (22, 3, ?, 'active', 10)", (today,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,execution_status,result,item_name,category,frequency,check_out_time)
                VALUES (41,22,1,'active','normal','他人已离站','设备','daily',datetime(?,'+1 minute'))""",
                       (latest_checkin,))
            db.execute("INSERT INTO insp_plans VALUES (23, 2, ?, 'active', 10)", (today,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,execution_status,result,item_name,category,frequency,check_out_time)
                VALUES (42,23,2,'active','normal','他站已离站','设备','daily',datetime(?,'+1 minute'))""",
                       (latest_checkin,))
            self.assertEqual(app_module._inspection_effective_checkin_time(db, 1, 2), latest_checkin)

            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,execution_status,result,item_name,category,frequency,check_out_time)
                VALUES (43,21,1,'active','normal','等时离站','设备','daily',?)""",
                       (latest_checkin,))
            self.assertIsNone(app_module._inspection_effective_checkin_time(db, 1, 2))
            db.execute("UPDATE work_orders SET check_in_time=? WHERE order_no='WO-1'", (latest_checkin,))

        direct_workorder = self.client.post('/api/mobile/photo-capture-session',
            headers=self.headers('operator-token'), json={
                'site_id': 1, 'order_no': 'WO-1',
                'gps_lat': 28.6801, 'gps_lng': 115.7301,
            })
        self.assertEqual(direct_workorder.status_code, 200, direct_workorder.json)

    def test_legacy_item_schema_without_checkout_keeps_same_day_checkin(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE inspection_checkins (
                    site_id INTEGER, user_id INTEGER, check_time TEXT
                );
                CREATE TABLE insp_plan_items (id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER);
                INSERT INTO inspection_checkins VALUES (1, 2, datetime('now','localtime'));
            """)
            self.assertTrue(app_module._inspection_effective_checkin_time(db, 1, 2))
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
