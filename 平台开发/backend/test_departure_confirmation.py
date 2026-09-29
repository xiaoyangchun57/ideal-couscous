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
            'reviewer-token': {'id': 11, 'role': 'reviewer', 'real_name': '审核人员'},
        })
        today = datetime.now().strftime('%Y-%m-%d')
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, real_name TEXT,
                    role TEXT, status TEXT
                );
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, status TEXT, version INTEGER
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_schedule_id INTEGER, assignee_id INTEGER,
                    generate_date TEXT, status TEXT, completion_rate REAL DEFAULT 0
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                    result TEXT,
                    execution_status TEXT DEFAULT 'active'
                );
                CREATE TABLE reagents (id INTEGER PRIMARY KEY, name TEXT, unit TEXT);
                CREATE TABLE reagent_inventory (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_id INTEGER,
                    current_qty REAL, low_stock_threshold REAL, qc_status TEXT,
                    expected_duration_days INTEGER, warning_days INTEGER,
                    batch_no TEXT, last_replaced_at TEXT, updated_at TEXT
                );
                CREATE TABLE reagent_records (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_name TEXT,
                    reagent_type TEXT, usage_date TEXT, replacement_date TEXT,
                    operator TEXT, operator_id INTEGER, notes TEXT,
                    old_batch_no TEXT, new_batch_no TEXT, old_qty REAL,
                    new_qty REAL, plan_id INTEGER
                );
                CREATE TABLE reagent_qc_records (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_id INTEGER, standard_value REAL,
                    measured_value REAL, deviation REAL, passed INTEGER, fail_action TEXT,
                    operator TEXT, operator_id INTEGER, qc_time TEXT, remark TEXT, plan_id INTEGER
                );
                CREATE TABLE reagent_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER,
                    reagent_id INTEGER, alert_type TEXT, current_qty REAL,
                    threshold_qty REAL, handled INTEGER DEFAULT 0, handled_at TEXT
                );
                CREATE TABLE reagent_idempotency (
                    operator_id INTEGER NOT NULL, endpoint TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL, status_code INTEGER NOT NULL,
                    created_at TEXT, PRIMARY KEY (operator_id, endpoint, idempotency_key)
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    source_type TEXT, source_id INTEGER, title TEXT, content TEXT
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
            db.executemany('INSERT INTO users VALUES (?,?,?,?,?)', [
                (1, 'admin', '管理员', 'admin', 'active'),
                (9, 'owner', '执行人员', 'operator', 'active'),
                (10, 'other', '其他人员', 'operator', 'active'),
                (11, 'reviewer', '审核人员', 'reviewer', 'active'),
            ])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(9, 1), (10, 2), (11, 1)])
            db.executemany('INSERT INTO sites VALUES (?,?)', [(1, '测试站点'), (2, '其他站点')])
            db.execute("INSERT INTO plan_schedules VALUES (5, 'approved', 3)")
            db.execute('INSERT INTO insp_plans VALUES (42, 5, 9, ?, \'active\', 0)', (today,))
            db.execute('INSERT INTO insp_plans VALUES (43, 5, 11, ?, \'active\', 0)', (today,))
            db.execute("""INSERT INTO insp_plan_items
                (id, plan_id, site_id, result, execution_status)
                VALUES (1, 42, 1, NULL, 'active')""")
            db.execute("""INSERT INTO insp_plan_items
                (id, plan_id, site_id, result, execution_status)
                VALUES (2, 43, 1, NULL, 'active')""")
            db.execute("INSERT INTO reagents VALUES (3, '氨氮试剂A', '瓶')")
            db.execute("""INSERT INTO reagent_inventory VALUES
                (1, 1, 3, 2, 1, 'pending', 30, 7, 'OLD-1', '', '')""")
            db.execute("INSERT INTO vehicles VALUES (1, 'available')")
            db.execute('INSERT INTO spare_parts_inventory VALUES (1, 12)')
        self.client = app_module.app.test_client()
        self.owner_headers = {'Authorization': 'Bearer owner-token'}
        self.other_headers = {'Authorization': 'Bearer other-token'}
        self.reviewer_headers = {'Authorization': 'Bearer reviewer-token'}

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        os.unlink(self.db_path)

    def db_value(self, query, params=()):
        db = sqlite3.connect(self.db_path)
        try:
            return db.execute(query, params).fetchone()[0]
        finally:
            db.close()

    def db_row(self, query, params=()):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute(query, params).fetchone()
            return dict(row) if row else None
        finally:
            db.close()

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

    def test_vehicle_and_parts_confirmations_preserve_the_unsubmitted_field(self):
        url = '/api/mobile/execution-plans/42/departure-confirmation'

        vehicle = self.client.post(
            url, headers=self.owner_headers, json={'vehicle_confirmed': True})
        parts = self.client.post(
            url, headers=self.owner_headers, json={'parts_confirmed': True})
        stale_vehicle_retry = self.client.post(
            url, headers=self.owner_headers, json={'vehicle_confirmed': True})

        self.assertEqual(vehicle.status_code, 200)
        self.assertEqual(vehicle.json['confirmation']['vehicle_confirmed'], 1)
        self.assertEqual(vehicle.json['confirmation']['parts_confirmed'], 0)
        self.assertEqual(parts.status_code, 200)
        self.assertEqual(parts.json['confirmation']['vehicle_confirmed'], 1)
        self.assertEqual(parts.json['confirmation']['parts_confirmed'], 1)
        self.assertEqual(stale_vehicle_retry.status_code, 200)
        self.assertEqual(stale_vehicle_retry.json['confirmation']['vehicle_confirmed'], 1)
        self.assertEqual(stale_vehicle_retry.json['confirmation']['parts_confirmed'], 1)

        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute(
                'SELECT vehicle_confirmed FROM plan_departure_confirmations').fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT parts_confirmed FROM plan_departure_confirmations').fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM plan_schedule_events').fetchone()[0], 2)
        finally:
            db.close()

    def test_reagent_inventory_is_only_visible_inside_own_execution_site(self):
        response = self.client.get(
            '/api/mobile/execution-plans/42/sites/1/reagents', headers=self.owner_headers)
        outside = self.client.get(
            '/api/mobile/execution-plans/42/sites/2/reagents', headers=self.owner_headers)

        self.assertEqual(response.status_code, 200)
        item = response.json['items'][0]
        self.assertEqual(item['reagent_name'], '氨氮试剂A')
        self.assertEqual(item['qc_status'], 'pending')
        self.assertEqual(item['status'], '正常')
        self.assertEqual(item['attention_reasons'], ['pending_qc'])
        self.assertTrue(item['can_replace'])
        self.assertTrue(item['can_calibrate'])
        self.assertIn('remaining_days', item)
        self.assertIn('expires_at', item)
        self.assertEqual(outside.status_code, 404)

    def test_reagent_inventory_projects_reviewer_as_read_only(self):
        base = '/api/mobile/execution-plans/43/sites/1'
        response = self.client.get(base + '/reagents', headers=self.reviewer_headers)
        replacement = self.client.post(
            base + '/reagent-replacements', headers=self.reviewer_headers, json={
                'reagent_id': 3, 'new_qty': 5, 'expected_duration_days': 30,
            })
        qc = self.client.post(base + '/reagent-qc', headers=self.reviewer_headers, json={
            'reagent_id': 3, 'standard_value': 10, 'measured_value': 10, 'passed': True,
        })

        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json['items'][0]['can_replace'])
        self.assertFalse(response.json['items'][0]['can_calibrate'])
        self.assertEqual((replacement.status_code, replacement.json['code']),
                         (403, 'REAGENT_WRITE_FORBIDDEN'))
        self.assertEqual((qc.status_code, qc.json['code']),
                         (403, 'REAGENT_WRITE_FORBIDDEN'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_records'), 0)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_qc_records'), 0)

    def test_reagent_replacement_then_qc_are_linked_to_execution_plan(self):
        base = '/api/mobile/execution-plans/42/sites/1'
        replacement = self.client.post(base + '/reagent-replacements', headers=self.owner_headers, json={
            'reagent_id': 3, 'new_qty': 5, 'expected_duration_days': 40,
            'new_batch_no': 'NEW-1', 'replaced_at': '2026-09-29 08:30:00',
            '_idempotency_key': 'mobile-replace-link',
        })
        qc = self.client.post(base + '/reagent-qc', headers=self.owner_headers, json={
            'reagent_id': 3, 'standard_value': 10, 'measured_value': 10.2, 'passed': True,
            'qc_time': '2026-09-29 08:45:00', '_idempotency_key': 'mobile-qc-link',
        })

        self.assertEqual(replacement.status_code, 200)
        self.assertEqual(replacement.json['qc_status'], 'pending')
        self.assertEqual(qc.status_code, 200)
        self.assertEqual(qc.json['qc_status'], 'passed')
        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute('SELECT plan_id FROM reagent_records').fetchone()[0], 42)
            self.assertEqual(db.execute('SELECT plan_id FROM reagent_qc_records').fetchone()[0], 42)
            self.assertEqual(db.execute('SELECT current_qty FROM reagent_inventory').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT qc_status FROM reagent_inventory').fetchone()[0], 'passed')
        finally:
            db.close()

    def test_reagent_replacement_requires_strictly_positive_finite_quantity(self):
        url = '/api/mobile/execution-plans/42/sites/1/reagent-replacements'
        invalid_values = [None, '', 0, -1, True, float('nan'), float('inf')]
        for value in invalid_values:
            with self.subTest(new_qty=value):
                response = self.client.post(url, headers=self.owner_headers, json={
                    'reagent_id': 3, 'new_qty': value, 'expected_duration_days': 30,
                })
                self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_records'), 0)
        self.assertEqual(self.db_value('SELECT current_qty FROM reagent_inventory'), 2)

    def test_reagent_replacement_is_stable_idempotent_and_rejects_key_reuse(self):
        url = '/api/mobile/execution-plans/42/sites/1/reagent-replacements'
        payload = {
            'reagent_id': 3, 'new_qty': 6, 'expected_duration_days': 45,
            'new_batch_no': 'NEW-2', 'replaced_at': '2026-09-29 09:00:00',
            'remark': '现场换新', '_idempotency_key': 'mobile-replace-idem',
        }
        first = self.client.post(url, headers=self.owner_headers, json=payload)
        replay = self.client.post(url, headers=self.owner_headers, json=payload)
        conflict = self.client.post(url, headers=self.owner_headers,
                                    json={**payload, 'new_qty': 7})

        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(first.json, replay.json)
        self.assertEqual((conflict.status_code, conflict.json['code']),
                         (409, 'IDEMPOTENCY_KEY_REUSED'))
        record = self.db_row('SELECT * FROM reagent_records')
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_records'), 1)
        self.assertEqual(record['old_qty'], 2)
        self.assertEqual(record['new_qty'], 6)
        self.assertEqual(record['old_batch_no'], 'OLD-1')
        self.assertEqual(record['new_batch_no'], 'NEW-2')
        self.assertEqual(record['operator_id'], 9)
        self.assertEqual(record['plan_id'], 42)
        self.assertEqual(record['replacement_date'], '2026-09-29 09:00:00')
        inventory = self.db_row('SELECT * FROM reagent_inventory')
        self.assertEqual(inventory['current_qty'], 6)
        self.assertEqual(inventory['batch_no'], 'NEW-2')
        self.assertEqual(inventory['expected_duration_days'], 45)
        self.assertEqual(inventory['qc_status'], 'pending')
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 1)

    def test_reagent_replacement_failure_rolls_back_every_write(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("""INSERT INTO reagent_alerts
                (site_id,reagent_id,alert_type,current_qty,threshold_qty,handled)
                VALUES (1,3,'low_stock',2,1,0)""")
            db.execute('''CREATE TRIGGER fail_mobile_reagent_idempotency
                BEFORE INSERT ON reagent_idempotency
                BEGIN SELECT RAISE(ABORT, 'forced idempotency failure'); END''')
            db.commit()
        finally:
            db.close()
        response = self.client.post(
            '/api/mobile/execution-plans/42/sites/1/reagent-replacements',
            headers=self.owner_headers, json={
                'reagent_id': 3, 'new_qty': 8, 'expected_duration_days': 50,
                'new_batch_no': 'ROLLBACK', '_idempotency_key': 'mobile-replace-rollback',
            })

        self.assertEqual((response.status_code, response.json['code']),
                         (503, 'REAGENT_RETRYABLE'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_records'), 0)
        self.assertEqual(self.db_value('SELECT current_qty FROM reagent_inventory'), 2)
        self.assertEqual(self.db_value('SELECT batch_no FROM reagent_inventory'), 'OLD-1')
        self.assertEqual(self.db_value('SELECT handled FROM reagent_alerts'), 0)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 0)

    def test_reagent_qc_is_stable_idempotent_and_notifies_on_failure(self):
        url = '/api/mobile/execution-plans/42/sites/1/reagent-qc'
        payload = {
            'reagent_id': 3, 'standard_value': 10, 'measured_value': 12,
            'passed': False, 'fail_action': 'repair',
            'qc_time': '2026-09-29 09:30:00', 'remark': '偏差过大',
            '_idempotency_key': 'mobile-qc-idem',
        }
        first = self.client.post(url, headers=self.owner_headers, json=payload)
        replay = self.client.post(url, headers=self.owner_headers, json=payload)
        conflict = self.client.post(url, headers=self.owner_headers,
                                    json={**payload, 'measured_value': 11})

        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(first.json, replay.json)
        self.assertEqual((conflict.status_code, conflict.json['code']),
                         (409, 'IDEMPOTENCY_KEY_REUSED'))
        qc = self.db_row('SELECT * FROM reagent_qc_records')
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_qc_records'), 1)
        self.assertEqual(qc['plan_id'], 42)
        self.assertEqual(qc['operator_id'], 9)
        self.assertEqual(qc['fail_action'], 'repair')
        self.assertEqual(qc['qc_time'], '2026-09-29 09:30:00')
        self.assertEqual(self.db_value('SELECT qc_status FROM reagent_inventory'), 'failed')
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM notifications'), 1)
        self.assertEqual(self.db_value('SELECT user_id FROM notifications'), 1)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 1)

    def test_reagent_qc_notification_failure_rolls_back_state(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute('''CREATE TRIGGER fail_mobile_reagent_notification
                BEFORE INSERT ON notifications
                BEGIN SELECT RAISE(ABORT, 'forced notification failure'); END''')
            db.commit()
        finally:
            db.close()
        response = self.client.post(
            '/api/mobile/execution-plans/42/sites/1/reagent-qc',
            headers=self.owner_headers, json={
                'reagent_id': 3, 'standard_value': 10, 'measured_value': 12,
                'passed': False, 'fail_action': 'calibrate',
                '_idempotency_key': 'mobile-qc-rollback',
            })

        self.assertEqual((response.status_code, response.json['code']),
                         (503, 'REAGENT_RETRYABLE'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_qc_records'), 0)
        self.assertEqual(self.db_value('SELECT qc_status FROM reagent_inventory'), 'pending')
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM notifications'), 0)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 0)

    def test_reagent_qc_idempotency_failure_rolls_back_passed_state(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute('''CREATE TRIGGER fail_mobile_qc_idempotency
                BEFORE INSERT ON reagent_idempotency
                BEGIN SELECT RAISE(ABORT, 'forced idempotency failure'); END''')
            db.commit()
        finally:
            db.close()
        response = self.client.post(
            '/api/mobile/execution-plans/42/sites/1/reagent-qc',
            headers=self.owner_headers, json={
                'reagent_id': 3, 'standard_value': 10, 'measured_value': 10,
                'passed': True, '_idempotency_key': 'mobile-qc-idem-rollback',
            })

        self.assertEqual((response.status_code, response.json['code']),
                         (503, 'REAGENT_RETRYABLE'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_qc_records'), 0)
        self.assertEqual(self.db_value('SELECT qc_status FROM reagent_inventory'), 'pending')
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 0)

    def test_reagent_replacement_and_qc_reject_incomplete_business_inputs(self):
        base = '/api/mobile/execution-plans/42/sites/1'
        for payload in (
            {'reagent_id': 3, 'new_qty': 5},
            {'reagent_id': 3, 'new_qty': 5, 'expected_duration_days': 0},
            {'reagent_id': 3, 'new_qty': 5, 'expected_duration_days': 30,
             'replaced_at': 'not-a-time'},
        ):
            response = self.client.post(
                base + '/reagent-replacements', headers=self.owner_headers, json=payload)
            self.assertEqual(response.status_code, 400, response.json)
        for payload in (
            {'reagent_id': 3, 'standard_value': 10, 'measured_value': 10},
            {'reagent_id': 3, 'standard_value': float('nan'),
             'measured_value': 10, 'passed': True},
            {'reagent_id': 3, 'standard_value': 10,
             'measured_value': float('inf'), 'passed': True},
            {'reagent_id': 3, 'standard_value': 10,
             'measured_value': 12, 'passed': False},
            {'reagent_id': 3, 'standard_value': 10,
             'measured_value': 10, 'passed': True, 'qc_time': 'not-a-time'},
        ):
            response = self.client.post(
                base + '/reagent-qc', headers=self.owner_headers, json=payload)
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_records'), 0)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_qc_records'), 0)


if __name__ == '__main__':
    unittest.main()
