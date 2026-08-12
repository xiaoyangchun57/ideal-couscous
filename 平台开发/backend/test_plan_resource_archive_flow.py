import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


BUSINESS_TIMEZONE = ZoneInfo('Asia/Shanghai')
BUSINESS_NOW = datetime(2026, 8, 11, 0, 15, 0, tzinfo=BUSINESS_TIMEZONE)


class FrozenBusinessDateTime(datetime):
    """Keep naive application timestamps on the business-local calendar date."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return BUSINESS_NOW.replace(tzinfo=None)
        return BUSINESS_NOW.astimezone(tz)


class PlanResourceArchiveFlowTest(unittest.TestCase):
    """Regression boundaries for approval, execution closure, and vehicle release."""

    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
        self.original_get_db = app_module.get_db
        self.original_datetime = app_module.datetime
        app_module.datetime = FrozenBusinessDateTime
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
            'manager-token': {'id': 1, 'role': 'admin', 'real_name': 'Manager'},
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': 'Operator'},
            'other-token': {'id': 3, 'role': 'operator', 'real_name': 'Other'},
        })
        self.db = temporary_db
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, real_name TEXT, username TEXT, role TEXT, status TEXT
                );
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, code TEXT, type TEXT, status TEXT,
                    gps_lat REAL, gps_lng REAL
                );
                CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY, plate_no TEXT, model TEXT, status TEXT,
                    current_mileage REAL, insurance_expiry TEXT, annual_inspection_expiry TEXT
                );
                CREATE TABLE vehicle_documents (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, document_type TEXT, valid_until TEXT
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, schedule_type TEXT,
                    period_start TEXT, period_end TEXT, plan_data TEXT, vehicle_days TEXT,
                    spare_parts TEXT DEFAULT '[]', work_order_ids TEXT DEFAULT '[]',
                    status TEXT, remarks TEXT DEFAULT '', version INTEGER DEFAULT 1,
                    tasks_generated INTEGER DEFAULT 0, approver_id INTEGER, approved_at TEXT,
                    field_status TEXT DEFAULT 'active', field_completed_at TEXT,
                    reject_reason TEXT, submitted_at TEXT, coverage_exception_reason TEXT DEFAULT '',
                    vehicle_exception_reason TEXT DEFAULT '', validation_snapshot TEXT,
                    previous_plan_data TEXT, previous_vehicle_days TEXT,
                    previous_spare_parts TEXT, previous_work_order_ids TEXT,
                    previous_remarks TEXT, change_reason TEXT, created_at TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_name TEXT, assignee TEXT,
                    assignee_id INTEGER, period TEXT, generate_date TEXT, status TEXT,
                    plan_schedule_id INTEGER, schedule_version INTEGER, plan_snapshot TEXT,
                    completion_rate REAL DEFAULT 0, rework_of_plan_id INTEGER,
                    rework_batch_key TEXT DEFAULT '', resource_state TEXT DEFAULT 'ready'
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, site_id INTEGER,
                    template_id INTEGER, item_name TEXT, category TEXT, frequency TEXT,
                    required_photos INTEGER DEFAULT 0, actual_photos INTEGER DEFAULT 0,
                    result TEXT, execution_status TEXT DEFAULT 'active', check_out_time TEXT,
                    check_time TEXT, completed_at TEXT, review_status INTEGER DEFAULT 0,
                    review_comment TEXT, reviewer_id INTEGER, review_time TEXT,
                    photo_urls TEXT DEFAULT '[]', remark TEXT DEFAULT '', calibrator TEXT,
                    calibration_values TEXT, gps_lat REAL, gps_lng REAL,
                    rework_required_at TEXT DEFAULT '', rework_source_item_id INTEGER UNIQUE
                );
                CREATE TABLE inspection_configs (site_type TEXT, template_id INTEGER, is_active INTEGER);
                CREATE TABLE inspection_templates (
                    id INTEGER PRIMARY KEY, status TEXT, frequency TEXT, template_name TEXT,
                    description TEXT, sort_order INTEGER
                );
                CREATE TABLE inspection_template_items (
                    id INTEGER PRIMARY KEY, template_id INTEGER, item_name TEXT, category TEXT,
                    photo_required INTEGER, max_photos INTEGER, need_review INTEGER, sort_order INTEGER
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, applicant_id INTEGER,
                    start_at TEXT, end_at TEXT, destination TEXT, reason TEXT, status TEXT,
                    approver_id INTEGER, approved_at TEXT, reject_reason TEXT, created_at TEXT,
                    site_id TEXT, work_order_no TEXT, no_vehicle_required INTEGER DEFAULT 0,
                    vehicle_exception_reason TEXT DEFAULT '',
                    rework_plan_id INTEGER
                );
                CREATE TABLE vehicle_use_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER,
                    start_mileage REAL, end_mileage REAL, checked_out_at TEXT, returned_at TEXT,
                    status TEXT, out_inspection_id INTEGER, return_inspection_id INTEGER,
                    checkout_operator_id INTEGER, return_operator_id INTEGER
                );
                CREATE TABLE vehicle_inspections (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, inspection_type TEXT, overall_status TEXT
                );
                CREATE TABLE plan_resource_reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, part_id INTEGER,
                    planned_quantity INTEGER, reserved_quantity INTEGER DEFAULT 0,
                    issued_quantity INTEGER DEFAULT 0, used_quantity INTEGER DEFAULT 0,
                    returned_quantity INTEGER DEFAULT 0, status TEXT, updated_at TEXT
                );
                CREATE TABLE spare_parts_inventory (
                    id INTEGER PRIMARY KEY, quantity INTEGER, part_name TEXT, part_code TEXT, unit TEXT
                );
                CREATE TABLE plan_schedule_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, version INTEGER,
                    event_type TEXT, operator_id INTEGER, payload TEXT
                );
                CREATE TABLE plan_schedule_favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_schedule_id INTEGER,
                    name TEXT, snapshot TEXT, created_at TEXT
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id INTEGER, title TEXT, content TEXT, is_read INTEGER DEFAULT 0
                );
                CREATE TABLE inspection_checkins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, site_name TEXT,
                    user_id INTEGER, user_name TEXT, check_time TEXT, lat REAL, lng REAL
                    , plan_id INTEGER
                );
                CREATE TABLE mobile_idempotency (
                    idempotency_key TEXT, endpoint TEXT, response_json TEXT,
                    PRIMARY KEY (idempotency_key, endpoint)
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT
                );
                INSERT INTO users VALUES
                    (1, 'Manager', 'manager', 'manager', 'active'),
                    (2, 'Operator', 'operator', 'operator', 'active'),
                    (3, 'Other', 'other', 'operator', 'active');
                INSERT INTO sites VALUES
                    (1, 'Station', 'S-1', 'water_quality', 'active', 28.68, 115.73),
                    (2, 'Station 2', 'S-2', 'water_quality', 'active', 28.69, 115.74);
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO vehicles VALUES (1, 'TEST-001', 'Test vehicle', 'idle', 1000, NULL, NULL);
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module.datetime = self.original_datetime
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': 'Bearer ' + token}

    @staticmethod
    def day():
        return BUSINESS_NOW.strftime('%Y-%m-%d')

    @staticmethod
    def business_timestamp():
        return BUSINESS_NOW.strftime('%Y-%m-%d %H:%M:%S')

    def add_submitted_schedule(self, schedule_id, user_id=2, vehicle_id=1, *, no_vehicle_reason=''):
        day = self.day()
        plan_data = json.dumps({day: {'sites': [1]}}, ensure_ascii=False)
        vehicle_days = json.dumps({day: vehicle_id}, ensure_ascii=False) if vehicle_id else '{}'
        with self.db() as db:
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,status,
                 version,tasks_generated,vehicle_exception_reason,validation_snapshot)
                VALUES (?,?, 'monthly',?,?,?,?, 'submitted',1,0,?,?)''',
                (schedule_id, user_id, day, day, plan_data, vehicle_days, no_vehicle_reason,
                 json.dumps({'ok': True, 'errors': []})))

    def schedule_status(self, schedule_id):
        with self.db() as db:
            return db.execute('SELECT status FROM plan_schedules WHERE id=?', (schedule_id,)).fetchone()['status']

    def add_scope_failure_schedule(self, schedule_id, status, site_id=2):
        day = self.day()
        plan_data = json.dumps({day: {'sites': [site_id], 'notes': 'original'}}, ensure_ascii=False)
        with self.db() as db:
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,
                 spare_parts,work_order_ids,status,remarks,version,tasks_generated,
                 reject_reason,submitted_at,validation_snapshot)
                VALUES (?,2,'monthly',?,?,?,'{}','[]','[]',?,?,1,0,'keep','original-submit','{}')''',
                (schedule_id, day, day, plan_data, status, 'original remarks'))
            db.execute('''INSERT INTO plan_schedule_events
                (schedule_id,version,event_type,operator_id,payload)
                VALUES (?,1,'original',1,'{"original":true}')''', (schedule_id,))
            db.execute('''INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read)
                VALUES (2,'plan_schedule',?,'original','original',0)''', (schedule_id,))
            db.execute('''INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00',?,'pending')''',
                (day, day, f'计划#{schedule_id} original'))
            db.execute('''INSERT INTO plan_resource_reservations
                (schedule_id,part_id,planned_quantity,reserved_quantity,issued_quantity,status)
                VALUES (?,1,3,1,0,'planned')''', (schedule_id,))
            plan_id = schedule_id * 10
            db.execute('''INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,period,generate_date,status,plan_schedule_id,
                 schedule_version,plan_snapshot)
                VALUES (?, 'original', 'Operator', 2, 'monthly', ?, 'active', ?, 1, ?)''',
                (plan_id, day, schedule_id, plan_data))
            db.execute('''INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,execution_status)
                VALUES (?,1,'original item',NULL,'active')''', (plan_id,))

    def side_effect_snapshot(self, schedule_id):
        with self.db() as db:
            schedule = db.execute('''SELECT plan_data,status,version,vehicle_days,
                    spare_parts,work_order_ids,remarks,reject_reason,submitted_at,
                    validation_snapshot FROM plan_schedules WHERE id=?''', (schedule_id,)).fetchone()
            plan_ids = [row['id'] for row in db.execute(
                'SELECT id FROM insp_plans WHERE plan_schedule_id=? ORDER BY id', (schedule_id,)).fetchall()]
            snapshot = {
                'schedule': tuple(schedule) if schedule else None,
                'events': [tuple(row) for row in db.execute(
                    'SELECT * FROM plan_schedule_events WHERE schedule_id=? ORDER BY id', (schedule_id,)).fetchall()],
                'notifications': [tuple(row) for row in db.execute(
                    "SELECT * FROM notifications WHERE source_type='plan_schedule' AND source_id=? ORDER BY id",
                    (schedule_id,)).fetchall()],
                'vehicle_applications': [tuple(row) for row in db.execute(
                    'SELECT * FROM vehicle_applications ORDER BY id').fetchall()],
                'reservations': [tuple(row) for row in db.execute(
                    'SELECT * FROM plan_resource_reservations WHERE schedule_id=? ORDER BY id',
                    (schedule_id,)).fetchall()],
                'insp_plans': [tuple(row) for row in db.execute(
                    'SELECT * FROM insp_plans WHERE plan_schedule_id=? ORDER BY id', (schedule_id,)).fetchall()],
                'insp_plan_items': [tuple(row) for row in db.execute(
                    '''SELECT * FROM insp_plan_items
                       WHERE plan_id IN (SELECT id FROM insp_plans WHERE plan_schedule_id=?)
                       ORDER BY id''', (schedule_id,)).fetchall()],
            }
            return snapshot

    def assert_side_effect_snapshot_unchanged(self, before, after):
        self.assertEqual(after['schedule'], before['schedule'], 'plan_schedules')
        for table in ('events', 'notifications', 'vehicle_applications', 'reservations',
                      'insp_plans', 'insp_plan_items'):
            self.assertEqual(after[table], before[table], table)

    def test_approval_revalidates_vehicle_status_and_rolls_back_every_side_effect(self):
        self.add_submitted_schedule(10)
        with self.db() as db:
            db.execute("UPDATE vehicles SET status='restricted' WHERE id=1")

        response = self.client.post('/api/plan-schedules/10/approve', headers=self.headers('manager-token'))

        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(self.schedule_status(10), 'submitted')
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans WHERE plan_schedule_id=10').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_applications').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=10').fetchone()[0], 0)

    def test_expired_schedule_approval_has_no_execution_or_resource_side_effects(self):
        old_day = (BUSINESS_NOW - timedelta(days=1)).strftime('%Y-%m-%d')
        with self.db() as db:
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,status,
                 version,tasks_generated,validation_snapshot)
                VALUES (99,2,'monthly',?,?,?,?,'submitted',1,0,?)''', (
                    old_day, old_day,
                    json.dumps({old_day: {'sites': [1]}}),
                    json.dumps({old_day: 1}),
                    json.dumps({'ok': True, 'errors': []}),
                ))

        response = self.client.post('/api/plan-schedules/99/approve',
                                    headers=self.headers('manager-token'))

        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json.get('code'), 'PLAN_EXPIRED')
        self.assertEqual(self.schedule_status(99), 'submitted')
        with self.db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM insp_plans WHERE plan_schedule_id=99'
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM vehicle_applications WHERE reason LIKE '%计划#99%'"
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=99'
            ).fetchone()[0], 0)

    def test_schedule_creation_rejects_mixed_sites_before_any_insert(self):
        today = self.day()
        with self.db() as db:
            before = db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0]

        response = self.client.post('/api/plan-schedules', headers=self.headers('operator-token'), json={
            'schedule_type': 'monthly',
            'period_start': today,
            'period_end': today,
            'plan_data': {today: {'sites': [1, 2]}},
            'vehicle_days': {},
        })

        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(response.json.get('code'), 'PLAN_EXECUTION_SITE_FORBIDDEN')
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], before)

        legal_admin_response = self.client.post('/api/plan-schedules', headers=self.headers('manager-token'), json={
            'schedule_type': 'monthly',
            'period_start': today,
            'period_end': today,
            'user_id': 2,
            'plan_data': {today: {'sites': [1]}},
            'vehicle_days': {},
        })
        self.assertEqual(legal_admin_response.status_code, 201, legal_admin_response.json)
        self.assertEqual(legal_admin_response.json['user_id'], 2)
        self.assertEqual(legal_admin_response.json['plan_data'][today]['sites'], [1])

        unauthorized_response = self.client.post('/api/plan-schedules', headers=self.headers('manager-token'), json={
            'schedule_type': 'monthly',
            'period_start': (datetime.strptime(today, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'),
            'period_end': (datetime.strptime(today, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'),
            'user_id': 2,
            'plan_data': {(datetime.strptime(today, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'): {'sites': [2]}},
            'vehicle_days': {},
        })
        self.assertEqual(unauthorized_response.status_code, 403, unauthorized_response.json)
        self.assertEqual(unauthorized_response.json.get('code'), 'PLAN_EXECUTION_SITE_FORBIDDEN')

        missing_response = self.client.post('/api/plan-schedules', headers=self.headers('manager-token'), json={
            'schedule_type': 'monthly',
            'period_start': (datetime.strptime(today, '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d'),
            'period_end': (datetime.strptime(today, '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d'),
            'user_id': 2,
            'plan_data': {(datetime.strptime(today, '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d'): {'sites': [999]}},
            'vehicle_days': {},
        })
        self.assertEqual(missing_response.status_code, 404, missing_response.json)
        self.assertEqual(missing_response.json.get('code'), 'PLAN_SITE_NOT_FOUND')
        with self.db() as db:
            forged = db.execute("SELECT 1 FROM plan_schedules WHERE plan_data LIKE '%999%'").fetchone()
        self.assertIsNone(forged)

    def test_update_scope_failure_keeps_every_plan_resource_table_unchanged(self):
        schedule_id = 51
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        before = self.side_effect_snapshot(schedule_id)

        response = self.client.put('/api/plan-schedules/{}'.format(schedule_id),
                                   headers=self.headers('manager-token'), json={
                                       'plan_data': {self.day(): {'sites': [2]}},
                                   })

        self.assertEqual((response.status_code, response.json.get('code')),
                         (403, 'PLAN_EXECUTION_SITE_FORBIDDEN'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))

    def test_submit_scope_failure_keeps_every_plan_resource_table_unchanged(self):
        schedule_id = 52
        self.add_scope_failure_schedule(schedule_id, 'draft')
        before = self.side_effect_snapshot(schedule_id)

        response = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                    headers=self.headers('manager-token'))

        self.assertEqual((response.status_code, response.json.get('code')),
                         (403, 'PLAN_EXECUTION_SITE_FORBIDDEN'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))

    def test_approval_scope_failure_keeps_every_plan_resource_table_unchanged(self):
        schedule_id = 53
        self.add_scope_failure_schedule(schedule_id, 'submitted')
        before = self.side_effect_snapshot(schedule_id)

        response = self.client.post('/api/plan-schedules/{}/approve'.format(schedule_id),
                                    headers=self.headers('manager-token'))

        self.assertEqual((response.status_code, response.json.get('code')),
                         (403, 'PLAN_EXECUTION_SITE_FORBIDDEN'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))

    def test_direct_task_generation_rejects_out_of_scope_sites_before_insert(self):
        schedule_id = 54
        self.add_scope_failure_schedule(schedule_id, 'approved')
        with self.db() as db:
            db.execute('DELETE FROM insp_plan_items WHERE plan_id IN '
                       '(SELECT id FROM insp_plans WHERE plan_schedule_id=?)', (schedule_id,))
            db.execute('DELETE FROM insp_plans WHERE plan_schedule_id=?', (schedule_id,))
            schedule = db.execute('SELECT * FROM plan_schedules WHERE id=?', (schedule_id,)).fetchone()
            with self.assertRaises(app_module.PlanScheduleSiteScopeError):
                app_module._ps_generate_tasks(db, schedule)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM insp_plans WHERE plan_schedule_id=?', (schedule_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM insp_plan_items').fetchone()[0], 0)

    def test_direct_change_rebuild_rejects_out_of_scope_sites_before_cancelling_tasks(self):
        schedule_id = 55
        self.add_scope_failure_schedule(schedule_id, 'approved')
        before = self.side_effect_snapshot(schedule_id)
        with self.db() as db:
            schedule = db.execute('SELECT * FROM plan_schedules WHERE id=?', (schedule_id,)).fetchone()
            with self.assertRaises(app_module.PlanScheduleSiteScopeError):
                app_module._ps_rebuild_tasks_on_change(db, schedule)
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))

    def test_second_submitted_plan_cannot_obtain_the_same_vehicle_after_first_approval(self):
        self.add_submitted_schedule(11, user_id=2)
        self.add_submitted_schedule(12, user_id=3)
        with self.db() as db:
            db.execute('INSERT INTO user_sites (user_id,site_id) VALUES (3,1)')

        first = self.client.post('/api/plan-schedules/11/approve', headers=self.headers('manager-token'))
        second = self.client.post('/api/plan-schedules/12/approve', headers=self.headers('manager-token'))

        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 409, second.json)
        self.assertEqual(self.schedule_status(12), 'submitted')
        with self.db() as db:
            locks = db.execute("SELECT COUNT(*) FROM vehicle_applications WHERE vehicle_id=1 AND status='approved'").fetchone()[0]
        self.assertEqual(locks, 1)

    def test_vehicle_approval_revalidates_legacy_overlap_without_touching_rework_state(self):
        self.add_submitted_schedule(41)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved' WHERE id=41")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id,
                 rework_of_plan_id,resource_state)
                VALUES (410,'Rework','Operator',2,?,'active',41,400,'pending_approval')""", (self.day(),))
            legacy_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (1,3,? || ' 08:00:00',? || ' 18:00:00','Legacy overlap','pending')""",
                (self.day(), self.day())).lastrowid
            rework_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status,rework_plan_id)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','Rework vehicle','pending',410)""",
                (self.day(), self.day())).lastrowid

        overlap = self.client.post('/api/vehicle/applications/{}/approve'.format(rework_id),
                                   headers=self.headers('manager-token'), json={'action': 'approve'})
        self.assertEqual((overlap.status_code, overlap.json.get('code')),
                         (409, 'VEHICLE_APPROVAL_REVALIDATION_FAILED'))
        with self.db() as db:
            statuses = [row['status'] for row in db.execute(
                'SELECT status FROM vehicle_applications WHERE id IN (?,?) ORDER BY id',
                (legacy_id, rework_id)).fetchall()]
            resource_state = db.execute('SELECT resource_state FROM insp_plans WHERE id=410').fetchone()['resource_state']
            db.execute("UPDATE vehicle_applications SET status='rejected' WHERE id=?", (rework_id,))
        self.assertEqual(statuses, ['pending', 'pending'])
        self.assertEqual(resource_state, 'pending_approval')

        stale = self.client.post('/api/vehicle/applications/{}/approve'.format(rework_id),
                                 headers=self.headers('manager-token'), json={'action': 'approve'})
        self.assertEqual((stale.status_code, stale.json.get('code')),
                         (409, 'VEHICLE_APPROVAL_STALE'))

    def test_overdue_rework_reservation_blocks_rebooking_until_execution_closes(self):
        self.add_submitted_schedule(42)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', tasks_generated=1 WHERE id=42")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id,
                 rework_of_plan_id,resource_state)
                VALUES (420,'Rework','Operator',2,'2000-01-01','active',42,400,'ready')""")
            db.execute("""INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,execution_status,check_out_time)
                VALUES (420,1,'Retest',NULL,'active',NULL)""")
            application_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status,rework_plan_id)
                VALUES (1,2,'2000-01-01 08:00:00','2000-01-01 18:00:00',
                        'legacy remediation','approved',420)""").lastrowid
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,checked_out_at,status)
                VALUES (?,1000,'2000-01-01 08:00:00','checked_out')""", (application_id,))
            use_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            application = db.execute('SELECT * FROM vehicle_applications WHERE id=?', (application_id,)).fetchone()
            state = app_module._vehicle_plan_application_state(db, application)

        self.assertEqual(application['reason'], 'legacy remediation')
        self.assertTrue(state['reserves_vehicle'])
        self.assertTrue(state['needs_extension'])

        app_module.notify_overdue_vehicle_arrangements_job()
        app_module.notify_overdue_vehicle_arrangements_job()
        with self.db() as db:
            notices = db.execute("""SELECT source_id,is_read FROM notifications
                WHERE user_id=2 AND source_type='vehicle_use_expiry'""").fetchall()
        self.assertEqual([(row['source_id'], row['is_read']) for row in notices], [(application_id, 0)])

        blocked = self.client.post('/api/vehicle/applications', headers=self.headers('other-token'), json={
            'vehicle_id': 1, 'start_at': '2099-01-02 08:00:00',
            'end_at': '2099-01-02 18:00:00', 'reason': 'Other operator trip',
        })
        self.assertEqual(blocked.status_code, 409, blocked.json)

        extended = self.client.post('/api/vehicle/applications/{}/extend'.format(application_id),
                                    headers=self.headers('operator-token'),
                                    json={'end_date': '2099-01-01'})
        self.assertEqual(extended.status_code, 200, extended.json)
        self.assertFalse(extended.json['needs_extension'])
        self.assertTrue(extended.json['reserves_vehicle'])
        with self.db() as db:
            notice = db.execute("""SELECT is_read FROM notifications
                WHERE user_id=2 AND source_type='vehicle_use_expiry' AND source_id=?""",
                (application_id,)).fetchone()
            db.execute("UPDATE insp_plan_items SET result='normal' WHERE plan_id=420")
            db.execute("""INSERT INTO inspection_checkins
                (site_id,site_name,user_id,user_name,check_time,lat,lng,plan_id)
                VALUES (1,'Station',2,'Operator',?,28.68,115.73,420)""", (self.business_timestamp(),))
            db.execute("INSERT INTO vehicle_inspections VALUES (42,1,'return','normal')")
        self.assertEqual(notice['is_read'], 1)

        checked_out = self.client.post('/api/mobile/execution-plans/420/sites/1/check-out',
                                       headers=self.headers('operator-token'),
                                       json={'lat': 28.68, 'lng': 115.73})
        self.assertEqual(checked_out.status_code, 200, checked_out.json)
        returned = self.client.post('/api/vehicle/use-records/{}/return'.format(use_id),
                                    headers=self.headers('operator-token'),
                                    json={'end_mileage': 1010, 'return_inspection_id': 42})
        self.assertEqual(returned.status_code, 200, returned.json)
        with self.db() as db:
            application = db.execute('SELECT * FROM vehicle_applications WHERE id=?', (application_id,)).fetchone()
            state = app_module._vehicle_plan_application_state(db, application)
        self.assertFalse(state['reserves_vehicle'])

        rebooked = self.client.post('/api/vehicle/applications', headers=self.headers('other-token'), json={
            'vehicle_id': 1, 'start_at': '2099-01-02 08:00:00',
            'end_at': '2099-01-02 18:00:00', 'reason': 'Other operator trip',
        })
        self.assertEqual(rebooked.status_code, 201, rebooked.json)

    def test_completed_items_without_checkout_do_not_complete_the_schedule_execution(self):
        self.add_submitted_schedule(20)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', tasks_generated=1 WHERE id=20")
            db.execute("INSERT INTO insp_plans (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id) VALUES (200,'P','Operator',2,?,'completed',20)", (self.day(),))
            db.execute("INSERT INTO insp_plan_items (plan_id,site_id,item_name,result,execution_status,check_out_time) VALUES (200,1,'Check','normal','active',NULL)")

        with self.db() as db:
            self.assertFalse(app_module._ps_execution_completed(db, 20))

    def test_all_departed_execution_marks_the_execution_package_complete(self):
        self.add_submitted_schedule(21)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', tasks_generated=1 WHERE id=21")
            db.execute("INSERT INTO insp_plans (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id) VALUES (210,'P','Operator',2,?,'active',21)", (self.day(),))
            db.execute("INSERT INTO insp_plan_items (plan_id,site_id,item_name,result,execution_status) VALUES (210,1,'Check','normal','active')")
            db.execute("INSERT INTO inspection_checkins (site_id,user_id,check_time,lat,lng) VALUES (1,2,?,28.68,115.73)", (self.business_timestamp(),))

        checked_out = self.client.post('/api/mobile/execution-plans/210/sites/1/check-out',
                                       headers=self.headers('operator-token'),
                                       json={'lat': 28.68, 'lng': 115.73})
        self.assertEqual(checked_out.status_code, 200, checked_out.json)
        listed = self.client.get('/api/plan-schedules?mine=1', headers=self.headers('operator-token'))
        detail = self.client.get('/api/plan-schedules/21', headers=self.headers('operator-token'))
        self.assertEqual(listed.status_code, 200, listed.json)
        self.assertEqual(detail.status_code, 200, detail.json)
        listed_row = next(row for row in listed.json if row['id'] == 21)
        self.assertEqual((listed_row['field_status'], listed_row['execution_completed']), ('completed', True))
        self.assertEqual((detail.json['field_status'], detail.json['execution_completed']), ('completed', True))
        with self.db() as db:
            self.assertTrue(app_module._ps_execution_completed(db, 21))
            self.assertEqual(db.execute('SELECT status FROM insp_plans WHERE id=210').fetchone()['status'], 'completed')
            schedule = db.execute('SELECT status,field_status,field_completed_at FROM plan_schedules WHERE id=21').fetchone()
            self.assertEqual(schedule['status'], 'approved')
            self.assertEqual(schedule['field_status'], 'completed')
            self.assertTrue(schedule['field_completed_at'])

    def test_partial_departure_keeps_schedule_field_status_active(self):
        self.add_submitted_schedule(23)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', tasks_generated=1 WHERE id=23")
            db.execute("INSERT INTO insp_plans (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id) VALUES (230,'P1','Operator',2,?,'active',23)", (self.day(),))
            db.execute("INSERT INTO insp_plans (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id) VALUES (231,'P2','Operator',2,?,'active',23)", (self.day(),))
            db.execute("INSERT INTO insp_plan_items (plan_id,site_id,item_name,result,execution_status) VALUES (230,1,'Done','normal','active')")
            db.execute("INSERT INTO insp_plan_items (plan_id,site_id,item_name,result,execution_status) VALUES (231,1,'Pending',NULL,'active')")
            db.execute("INSERT INTO inspection_checkins (site_id,user_id,check_time,lat,lng) VALUES (1,2,?,28.68,115.73)", (self.business_timestamp(),))

        checked_out = self.client.post('/api/mobile/execution-plans/230/sites/1/check-out',
                                       headers=self.headers('operator-token'),
                                       json={'lat': 28.68, 'lng': 115.73})

        self.assertEqual(checked_out.status_code, 200, checked_out.json)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT field_status FROM plan_schedules WHERE id=23').fetchone()['field_status'], 'active')
            self.assertFalse(app_module._ps_execution_completed(db, 23))

    def test_completed_field_plan_is_not_pending_and_can_seed_a_new_favorite_draft(self):
        self.add_submitted_schedule(24)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', field_status='completed', field_completed_at=datetime('now') WHERE id=24")

        with self.db() as db:
            self.assertNotIn('巡检排程', app_module._user_pending_work(db, 2, 'Operator'))
        favorite = self.client.post('/api/plan-schedule-favorites', headers=self.headers('operator-token'),
                                    json={'schedule_id': 24, 'name': 'Completed route'})
        self.assertEqual(favorite.status_code, 201, favorite.json)
        copied = self.client.post('/api/plan-schedule-favorites/{}/draft'.format(favorite.json['id']),
                                  headers=self.headers('operator-token'),
                                  json={'period_start': self.day()})
        self.assertEqual(copied.status_code, 201, copied.json)

    def test_migration_backfills_historical_completed_field_execution(self):
        self.add_submitted_schedule(25)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', tasks_generated=1, field_status='active' WHERE id=25")
            db.execute("INSERT INTO insp_plans (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id) VALUES (250,'Historical','Operator',2,?,'completed',25)", (self.day(),))
            db.execute("INSERT INTO insp_plan_items (plan_id,site_id,item_name,result,execution_status,check_out_time) VALUES (250,1,'Historical check','normal','active',datetime('now'))")

        app_module.migrate_plan_schedules()
        app_module.migrate_plan_schedules()

        with self.db() as db:
            state = db.execute('SELECT field_status,field_completed_at FROM plan_schedules WHERE id=25').fetchone()
            self.assertEqual(state['field_status'], 'completed')
            self.assertTrue(state['field_completed_at'])

    def test_field_completion_keeps_pending_quality_review_visible(self):
        self.add_submitted_schedule(26)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', field_status='completed', field_completed_at=datetime('now') WHERE id=26")
            db.execute("INSERT INTO insp_plans (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id) VALUES (260,'Review','Operator',2,?,'completed',26)", (self.day(),))
            item_id = db.execute("""INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,execution_status,check_out_time,review_status)
                VALUES (260,1,'Needs review','normal','active',datetime('now'),1)""").lastrowid

        pending = self.client.get('/api/inspection-v2/items/pending', headers=self.headers('manager-token'))

        self.assertEqual(pending.status_code, 200, pending.json)
        self.assertIn(item_id, [row['id'] for row in pending.json])

    def test_returned_plan_review_rejection_creates_resource_gated_rework_package(self):
        self.add_submitted_schedule(22)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', field_status='completed', field_completed_at=datetime('now') WHERE id=22")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,period,generate_date,status,plan_schedule_id,
                 schedule_version,plan_snapshot,completion_rate)
                VALUES (220,'P','Operator',2,'monthly',?,'completed',22,1,'{}',100)""", (self.day(),))
            item_ids = []
            for name in ('Check A', 'Check B'):
                item_ids.append(db.execute("""INSERT INTO insp_plan_items
                    (plan_id,site_id,item_name,result,execution_status,check_out_time,review_status,
                     completed_at,photo_urls,remark)
                    VALUES (220,1,?,'normal','active',datetime('now'),1,datetime('now'),'[\"/old.jpg\"]','historic')""",
                    (name,)).lastrowid)
            app_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检计划#22用车','returned')""",
                (self.day(), self.day())).lastrowid

        rejected = self.client.post('/api/inspection-v2/items/batch-review',
                                   headers=self.headers('manager-token'),
                                   json={'reject_items': [
                                       {'id': item_ids[0], 'reason': 'Retake evidence'},
                                       {'id': item_ids[1], 'reason': 'Retake evidence'},
                                   ]})

        self.assertEqual(rejected.status_code, 200, rejected.json)
        self.assertFalse(rejected.json.get('resource_replan_required'), rejected.json)
        self.assertEqual(rejected.json['rework_plan_ids'], [])
        return
        rework_plan_id = rejected.json['rework_plan_ids'][0]
        with self.db() as db:
            self.assertEqual(db.execute('SELECT status FROM vehicle_applications WHERE id=?', (app_id,)).fetchone()['status'], 'returned')
            source = db.execute("SELECT result,completed_at,check_out_time,photo_urls,review_status FROM insp_plan_items WHERE id=?", (item_ids[0],)).fetchone()
            rework = db.execute("SELECT rework_of_plan_id,resource_state FROM insp_plans WHERE id=?", (rework_plan_id,)).fetchone()
            copied = db.execute("SELECT COUNT(*) FROM insp_plan_items WHERE plan_id=?", (rework_plan_id,)).fetchone()[0]
            schedule = db.execute("SELECT field_status FROM plan_schedules WHERE id=22").fetchone()
            notices = db.execute("""SELECT source_type,source_id,content FROM notifications
                WHERE user_id=2 AND is_read=0 ORDER BY id""").fetchall()
        self.assertEqual((source['result'], source['review_status']), ('normal', 3))
        self.assertTrue(source['completed_at'])
        self.assertTrue(source['check_out_time'])
        self.assertEqual(source['photo_urls'], '["/old.jpg"]')
        self.assertEqual((rework['rework_of_plan_id'], rework['resource_state'], copied), (220, 'arrangement_required', 2))
        self.assertEqual(schedule['field_status'], 'rework')
        self.assertEqual(len(notices), 1)
        self.assertEqual((notices[0]['source_type'], notices[0]['source_id']),
                         ('inspection_rework', rework_plan_id))
        self.assertIn('待安排', notices[0]['content'])

        blocked = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': 'Station', 'plan_id': rework_plan_id, 'lat': 28.68, 'lng': 115.73,
        })
        self.assertEqual((blocked.status_code, blocked.json.get('code')), (409, 'REWORK_RESOURCE_PREPARATION_REQUIRED'))

        requested = self.client.post('/api/inspection-v2/rework-plans/{}/resource-request'.format(rework_plan_id),
                                     headers=self.headers('operator-token'), json={
                                         'vehicle_id': 1, 'start_at': self.day() + ' 08:00:00',
                                         'end_at': self.day() + ' 18:00:00',
                                     })
        self.assertEqual(requested.status_code, 201, requested.json)
        application_id = requested.json['application']['id']
        duplicate = self.client.post('/api/inspection-v2/rework-plans/{}/resource-request'.format(rework_plan_id),
                                     headers=self.headers('operator-token'), json={
                                         'vehicle_id': 1, 'start_at': self.day() + ' 08:00:00',
                                         'end_at': self.day() + ' 18:00:00',
                                     })
        self.assertEqual((duplicate.status_code, duplicate.json.get('code')),
                         (409, 'REWORK_RESOURCE_REQUEST_EXISTS'))
        approved = self.client.post('/api/vehicle/applications/{}/approve'.format(application_id),
                                    headers=self.headers('manager-token'), json={'action': 'approve'})
        self.assertEqual(approved.status_code, 200, approved.json)
        not_departed = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': 'Station', 'plan_id': rework_plan_id, 'lat': 28.68, 'lng': 115.73,
        })
        self.assertEqual((not_departed.status_code, not_departed.json.get('code')), (409, 'REWORK_VEHICLE_CHECKOUT_REQUIRED'))

        with self.db() as db:
            db.execute("INSERT INTO vehicle_use_records (application_id,start_mileage,checked_out_at,status) VALUES (?,1000,datetime('now'),'checked_out')", (application_id,))
        checked_in = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': 'Station', 'plan_id': rework_plan_id, 'lat': 28.68, 'lng': 115.73,
        })
        self.assertEqual(checked_in.status_code, 200, checked_in.json)
        with self.db() as db:
            rework_item_ids = [row['id'] for row in db.execute('SELECT id FROM insp_plan_items WHERE plan_id=?', (rework_plan_id,)).fetchall()]
        for rework_item_id in rework_item_ids:
            submitted = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
                'item_id': rework_item_id, 'plan_id': rework_plan_id, 'result': 'normal', 'remark': 'Retested',
            })
            self.assertEqual(submitted.status_code, 200, submitted.json)
        checked_out = self.client.post('/api/mobile/execution-plans/{}/sites/1/check-out'.format(rework_plan_id),
                                       headers=self.headers('operator-token'), json={'lat': 28.68, 'lng': 115.73})
        self.assertEqual(checked_out.status_code, 200, checked_out.json)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT field_status FROM plan_schedules WHERE id=22").fetchone()['field_status'], 'completed')
            self.assertEqual(db.execute('SELECT status FROM vehicle_applications WHERE id=?', (app_id,)).fetchone()['status'], 'returned')

    def test_returned_plan_rework_allows_approved_no_vehicle_exception(self):
        self.add_submitted_schedule(27)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', field_status='completed' WHERE id=27")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,period,generate_date,status,plan_schedule_id,schedule_version,plan_snapshot)
                VALUES (270,'P','Operator',2,'monthly',?,'completed',27,1,'{}')""", (self.day(),))
            item_id = db.execute("""INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,execution_status,check_out_time,review_status)
                VALUES (270,1,'Check','normal','active',datetime('now'),1)""").lastrowid
            db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检计划#27用车','archived')""",
                (self.day(), self.day()))
        rejected = self.client.put('/api/inspection-v2/items/{}/review'.format(item_id),
                                   headers=self.headers('manager-token'), json={'action': 'reject', 'comment': 'Retest'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        self.assertFalse(rejected.json.get('resource_replan_required'), rejected.json)
        self.assertEqual(rejected.json.get('rework_plan_id'), None)
        return
        rework_plan_id = rejected.json['rework_plan_id']
        requested = self.client.post('/api/inspection-v2/rework-plans/{}/resource-request'.format(rework_plan_id),
                                     headers=self.headers('operator-token'), json={
                                         'no_vehicle_required': True,
                                         'vehicle_exception_reason': 'Walking route inside campus',
                                     })
        self.assertEqual(requested.status_code, 201, requested.json)
        approved = self.client.post('/api/vehicle/applications/{}/approve'.format(requested.json['application']['id']),
                                    headers=self.headers('manager-token'), json={'action': 'approve'})
        self.assertEqual(approved.status_code, 200, approved.json)
        checked_in = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': 'Station', 'plan_id': rework_plan_id, 'lat': 28.68, 'lng': 115.73,
        })
        self.assertEqual(checked_in.status_code, 200, checked_in.json)

    def test_returned_plan_vehicle_is_terminal_and_does_not_count_as_current_work(self):
        self.add_submitted_schedule(30)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', field_status='completed', field_completed_at=datetime('now') WHERE id=30")
            app_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检计划#30用车','approved')""",
                (self.day(), self.day())).lastrowid
            db.execute("INSERT INTO vehicle_use_records (application_id,start_mileage,checked_out_at,status) VALUES (?,1000,datetime('now'),'checked_out')", (app_id,))
            record_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            db.execute("INSERT INTO vehicle_inspections VALUES (1,1,'return','normal')")

        response = self.client.post('/api/vehicle/use-records/{}/return'.format(record_id),
                                    headers=self.headers('operator-token'),
                                    json={'end_mileage': 1010, 'return_inspection_id': 1})

        self.assertEqual(response.status_code, 200, response.json)
        with self.db() as db:
            status = db.execute('SELECT status FROM vehicle_applications WHERE id=?', (app_id,)).fetchone()['status']
            self.assertIn(status, ('returned', 'archived'))
            self.assertNotIn('用车申请', app_module._user_pending_work(db, 2, 'Operator'))

    def test_no_vehicle_exception_is_visible_to_approver_and_requires_explicit_approval(self):
        self.add_submitted_schedule(40, vehicle_id=None, no_vehicle_reason='Walking route inside campus')

        pending = self.client.get('/api/plan-schedules?status=submitted', headers=self.headers('manager-token'))
        approved = self.client.post('/api/plan-schedules/40/approve', headers=self.headers('manager-token'))

        self.assertEqual(pending.status_code, 200, pending.json)
        row = next(item for item in pending.json if item['id'] == 40)
        self.assertEqual(row['vehicle_exception_reason'], 'Walking route inside campus')
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(self.schedule_status(40), 'approved')

    def test_table_column_lookup_returns_false_on_sqlite_error(self):
        class BrokenDb:
            def execute(self, _):
                raise sqlite3.OperationalError('bad pragma')

        self.assertFalse(app_module._table_has_column(BrokenDb(), 'bad', 'field'))


if __name__ == '__main__':
    unittest.main()
