import os
import sqlite3
import sys
import unittest
import uuid
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class MultiRoleCrossModuleAccessTest(unittest.TestCase):
    def setUp(self):
        self.db_uri = f'file:multirole-{uuid.uuid4().hex}?mode=memory&cache=shared'
        self.keeper = sqlite3.connect(self.db_uri, uri=True)
        self.keeper.row_factory = sqlite3.Row
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_site_cache = dict(app_module._site_ids_cache)

        @contextmanager
        def memory_db():
            db = sqlite3.connect(self.db_uri, uri=True)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

        self.memory_db = memory_db
        app_module.get_db = memory_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'secondary-admin-token': {
                'id': 1, 'role': 'operator', 'roles': ['admin', 'operator'],
                'real_name': 'dual role', 'username': 'dual-role',
            },
            'operator-token': {
                'id': 2, 'role': 'operator', 'roles': ['operator'],
                'real_name': 'operator', 'username': 'operator',
            },
            'owner-token': {
                'id': 3, 'role': 'operator', 'roles': ['operator'],
                'real_name': 'owner', 'username': 'owner',
            },
        })

        with memory_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, gps_lat REAL, gps_lng REAL);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, status TEXT, related_alert_id INTEGER,
                    used_parts TEXT, site_id INTEGER, check_in_time TEXT, assignee TEXT,
                    remark TEXT, conclusion TEXT, satisfaction TEXT, images TEXT
                );
                CREATE TABLE timeline_events (
                    source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT
                );
                CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY, plate_no TEXT, model TEXT,
                    status TEXT DEFAULT 'idle'
                );
                CREATE TABLE vehicle_documents (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER,
                    document_type TEXT, valid_until TEXT
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER,
                    start_at TEXT, end_at TEXT, destination TEXT, reason TEXT, status TEXT,
                    created_at TEXT, application_id INTEGER
                );
                CREATE TABLE vehicle_use_records (
                    id INTEGER PRIMARY KEY, application_id INTEGER, returned_at TEXT
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, approver_id INTEGER,
                    schedule_type TEXT, period_start TEXT, period_end TEXT,
                    plan_data TEXT DEFAULT '{}', vehicle_days TEXT DEFAULT '{}',
                    spare_parts TEXT DEFAULT '[]', work_order_ids TEXT DEFAULT '[]',
                    previous_plan_data TEXT, previous_vehicle_days TEXT,
                    previous_spare_parts TEXT, previous_work_order_ids TEXT,
                    status TEXT, remarks TEXT DEFAULT '', coverage_exception_reason TEXT DEFAULT '',
                    vehicle_exception_reason TEXT DEFAULT '', reject_reason TEXT,
                    validation_snapshot TEXT, tasks_generated INTEGER DEFAULT 0,
                    version INTEGER DEFAULT 1, created_at TEXT
                );
                CREATE TABLE plan_schedule_events (
                    schedule_id INTEGER, version INTEGER, event_type TEXT,
                    operator_id INTEGER, payload TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_schedule_id INTEGER
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER,
                    result TEXT, execution_status TEXT
                );

                INSERT INTO users VALUES (1,'dual role','operator');
                INSERT INTO users VALUES (2,'operator','operator');
                INSERT INTO users VALUES (3,'owner','operator');
                INSERT INTO user_roles VALUES (1,'operator'), (1,'admin'), (2,'operator'), (3,'operator');
                INSERT INTO sites VALUES (1,'site one',NULL,NULL), (2,'site two',NULL,NULL);
                INSERT INTO user_sites VALUES (1,1), (2,1), (3,2);

                INSERT INTO work_orders
                    (id,order_no,status,site_id,assignee,used_parts,images)
                    VALUES (1,'WO-CROSS-1','pending',2,'owner','[]','[]');

                INSERT INTO vehicles VALUES
                    (1,'CAR-1','SUV','idle'), (2,'CAR-2','SUV','idle');
                INSERT INTO vehicle_applications
                    (id,vehicle_id,applicant_id,start_at,end_at,destination,reason,status,created_at)
                    VALUES (1,1,1,'2099-01-01','2099-01-02','A','general','approved','2026-01-01');
                INSERT INTO vehicle_applications
                    (id,vehicle_id,applicant_id,start_at,end_at,destination,reason,status,created_at)
                    VALUES (2,2,3,'2099-01-03','2099-01-04','B','general','approved','2026-01-02');

                INSERT INTO plan_schedules
                    (id,user_id,schedule_type,period_start,period_end,status,remarks,created_at)
                    VALUES (1,1,'weekly','2099-01-01','2099-01-07','draft','own','2026-01-01');
                INSERT INTO plan_schedules
                    (id,user_id,schedule_type,period_start,period_end,status,remarks,created_at)
                    VALUES (2,3,'weekly','2099-01-08','2099-01-14','draft','other','2026-01-02');
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        self.keeper.close()

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def test_secondary_admin_cannot_perform_field_workorder_actions(self):
        response = self.client.put('/api/workorders/WO-CROSS-1/status',
                                   headers=self.headers('secondary-admin-token'),
                                   json={'status': 'accepted'})
        self.assertEqual(response.status_code, 403, response.json)
        with self.memory_db() as db:
            self.assertEqual(db.execute(
                "SELECT status FROM work_orders WHERE order_no='WO-CROSS-1'"
            ).fetchone()['status'], 'pending')

    def test_operator_cannot_operate_another_users_workorder(self):
        response = self.client.put('/api/workorders/WO-CROSS-1/status',
                                   headers=self.headers('operator-token'),
                                   json={'status': 'accepted'})
        self.assertEqual(response.status_code, 403, response.json)

    def test_secondary_admin_can_operate_another_users_vehicle_application(self):
        with self.memory_db() as db:
            application = db.execute('SELECT * FROM vehicle_applications WHERE id=2').fetchone()
        self.assertTrue(app_module._vehicle_user_can_operate(
            application, app_module._tokens['secondary-admin-token']))

    def test_secondary_admin_vehicle_application_list_has_team_scope(self):
        response = self.client.get('/api/vehicle/applications',
                                   headers=self.headers('secondary-admin-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual({row['id'] for row in response.json}, {1, 2})

    def test_operator_vehicle_application_scope_stays_personal(self):
        with self.memory_db() as db:
            application = db.execute('SELECT * FROM vehicle_applications WHERE id=2').fetchone()
        self.assertFalse(app_module._vehicle_user_can_operate(
            application, app_module._tokens['operator-token']))
        response = self.client.get('/api/vehicle/applications', headers=self.headers('operator-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json, [])

    def test_secondary_admin_plan_list_includes_team_scope(self):
        response = self.client.get('/api/plan-schedules',
                                   headers=self.headers('secondary-admin-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual({row['id'] for row in response.json}, {1, 2})

    def test_secondary_admin_is_included_in_plan_approvers(self):
        with self.memory_db() as db:
            self.assertEqual(app_module._ps_approver_ids(db), [1])

    def test_secondary_admin_can_update_another_users_draft(self):
        response = self.client.put('/api/plan-schedules/2',
                                   headers=self.headers('secondary-admin-token'),
                                   json={'version': 1, 'remarks': 'managed by secondary admin'})
        self.assertEqual(response.status_code, 200, response.json)
        with self.memory_db() as db:
            self.assertEqual(db.execute(
                'SELECT remarks FROM plan_schedules WHERE id=2'
            ).fetchone()['remarks'], 'managed by secondary admin')

    def test_plan_update_rejects_stale_version_without_side_effects(self):
        first = self.client.put('/api/plan-schedules/2',
                                headers=self.headers('secondary-admin-token'),
                                json={'version': 1, 'remarks': 'first edit'})
        self.assertEqual((first.status_code, first.json['version']), (200, 2))
        stale = self.client.put('/api/plan-schedules/2',
                                headers=self.headers('secondary-admin-token'),
                                json={'version': 1, 'remarks': 'stale edit'})
        self.assertEqual((stale.status_code, stale.json.get('code')),
                         (409, 'PLAN_VERSION_CONFLICT'))
        with self.memory_db() as db:
            row = db.execute('SELECT remarks,version FROM plan_schedules WHERE id=2').fetchone()
            self.assertEqual((row['remarks'], row['version']), ('first edit', 2))
            events = db.execute(
                "SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=2 AND event_type='updated'"
            ).fetchone()[0]
            self.assertEqual(events, 1)

    def test_plan_update_requires_version_without_side_effects(self):
        with self.memory_db() as db:
            before = tuple(db.execute(
                'SELECT remarks,status,version FROM plan_schedules WHERE id=2'
            ).fetchone())
        response = self.client.put('/api/plan-schedules/2',
                                   headers=self.headers('secondary-admin-token'),
                                   json={'remarks': 'missing version'})
        self.assertEqual((response.status_code, response.json.get('code')),
                         (409, 'PLAN_VERSION_REQUIRED'))
        with self.memory_db() as db:
            after = tuple(db.execute(
                'SELECT remarks,status,version FROM plan_schedules WHERE id=2'
            ).fetchone())
            event_count = db.execute(
                'SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=2'
            ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(event_count, 0)

    def test_operator_plan_list_and_update_stay_personal(self):
        listed = self.client.get('/api/plan-schedules', headers=self.headers('operator-token'))
        self.assertEqual(listed.status_code, 200, listed.json)
        self.assertEqual(listed.json, [])
        updated = self.client.put('/api/plan-schedules/2',
                                  headers=self.headers('operator-token'),
                                  json={'remarks': 'forbidden'})
        self.assertEqual(updated.status_code, 403, updated.json)


if __name__ == '__main__':
    unittest.main()
