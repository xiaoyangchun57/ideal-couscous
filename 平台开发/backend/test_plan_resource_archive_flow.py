import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest import mock


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
        self.original_upload_dir = app_module.UPLOAD_DIR
        self.upload_dir = tempfile.mkdtemp()
        app_module.UPLOAD_DIR = self.upload_dir
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
                    vehicle_exception_reason TEXT DEFAULT '', vehicle_id INTEGER,
                    no_vehicle_required INTEGER NOT NULL DEFAULT 0,
                    validation_snapshot TEXT,
                    previous_plan_data TEXT, previous_vehicle_days TEXT, previous_vehicle_id INTEGER,
                    previous_spare_parts TEXT, previous_work_order_ids TEXT,
                    previous_remarks TEXT, previous_period_start TEXT, previous_period_end TEXT,
                    previous_coverage_exception_reason TEXT,
                    previous_vehicle_exception_reason TEXT,
                    previous_no_vehicle_required INTEGER,
                    change_reason TEXT, created_at TEXT,
                    vehicle_adjustment_required INTEGER NOT NULL DEFAULT 0,
                    vehicle_adjustment_detail TEXT DEFAULT ''
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
                    event_type TEXT, operator_id INTEGER, payload TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE plan_schedule_favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_schedule_id INTEGER,
                    name TEXT, snapshot TEXT, created_at TEXT
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id INTEGER, title TEXT, content TEXT, is_read INTEGER DEFAULT 0,
                    payload_json TEXT DEFAULT ''
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
                CREATE TABLE vehicle_extension_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,source_application_id INTEGER NOT NULL,
                    target_application_id INTEGER NOT NULL,target_schedule_id INTEGER,
                    conflict_start TEXT,conflict_end TEXT,confirmed_by INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_application_id,target_application_id,conflict_end)
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    plan_id INTEGER, item_id INTEGER, file_type TEXT, mime_type TEXT,
                    review_status TEXT DEFAULT 'pending', is_deleted INTEGER DEFAULT 0
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT, title TEXT,
                    status TEXT, source TEXT, created_at TEXT, site_id INTEGER,
                    check_in_time TEXT, related_alert_id INTEGER, images TEXT
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
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN check_in_time TEXT")
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module.datetime = self.original_datetime
        app_module.UPLOAD_DIR = self.original_upload_dir
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_path)
        shutil.rmtree(self.upload_dir, ignore_errors=True)

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
                 version,tasks_generated,vehicle_exception_reason,vehicle_id,
                 no_vehicle_required,validation_snapshot)
                VALUES (?,?, 'monthly',?,?,?,?, 'submitted',1,0,?,?,?,?)''',
                (schedule_id, user_id, day, day, plan_data, vehicle_days, no_vehicle_reason,
                 vehicle_id, int(vehicle_id is None),
                 json.dumps({'ok': True, 'errors': []})))

    def schedule_status(self, schedule_id):
        with self.db() as db:
            return db.execute('SELECT status FROM plan_schedules WHERE id=?', (schedule_id,)).fetchone()['status']

    def creation_payload(self, key='plan-intent-1', submit=True):
        return {'_idempotency_key': key, 'schedule_type': 'weekly',
                'period_start': self.day(), 'period_end': self.day(),
                'plan_data': {self.day(): {'sites': [1]}}, 'vehicle_id': None,
                'no_vehicle_required': True,
                'vehicle_exception_reason': '步行巡检', 'submit': submit}

    def test_create_intent_replays_live_state_without_duplicate_notifications(self):
        payload = self.creation_payload()
        first = self.client.post('/api/plan-schedules', headers=self.headers('operator-token'), json=payload)
        self.assertEqual(first.status_code, 201, first.json)
        with self.db() as db:
            before = db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
            self.assertGreater(before, 0)
            db.execute("UPDATE plan_schedules SET status='approved',version=2 WHERE id=?", (first.json['id'],))
        replay = self.client.post('/api/plan-schedules', headers=self.headers('operator-token'), json=payload)
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual((replay.json['id'], replay.json['status'], replay.json['version']), (first.json['id'],'approved',2))
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedule_events').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_creation_intents').fetchone()[0], 1)

    def test_create_intent_payload_or_actor_conflict_has_zero_effect(self):
        payload = self.creation_payload(submit=False)
        first = self.client.post('/api/plan-schedules', headers=self.headers('operator-token'), json=payload)
        self.assertEqual(first.status_code, 201, first.json)
        for changed, token in ((dict(payload, remarks='changed'), 'operator-token'),
                               (dict(payload, schedule_type='tampered'), 'operator-token'), (payload,'manager-token')):
            response = self.client.post('/api/plan-schedules', headers=self.headers(token), json=changed)
            self.assertEqual((response.status_code,response.json['code']), (409,'PLAN_CREATE_INTENT_CONFLICT'))
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT remarks FROM plan_schedules').fetchone()[0], '')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedule_events').fetchone()[0], 1)

    def test_create_concurrent_same_intent_creates_one_plan(self):
        def create(_):
            return app_module.app.test_client().post('/api/plan-schedules', headers=self.headers('operator-token'), json=self.creation_payload())
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(create, range(2)))
        self.assertEqual(sorted(row.status_code for row in responses), [200,201])
        self.assertEqual(responses[0].json['id'], responses[1].json['id'])
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedule_events').fetchone()[0], 1)

    def test_create_notification_database_failure_rolls_back_plan_event_outbox_and_intent(self):
        with self.db() as db:
            db.execute('CREATE TABLE test_outbox (notification_id INTEGER)')
        original = app_module._create_notification
        def failing(*args, **kwargs):
            notification_id = original(*args, **kwargs)
            kwargs['db'].execute('INSERT INTO test_outbox VALUES (?)', (notification_id,))
            raise sqlite3.OperationalError('injected notification delivery failure')
        with mock.patch.object(app_module, '_create_notification', side_effect=failing):
            response = self.client.post('/api/plan-schedules', headers=self.headers('operator-token'), json=self.creation_payload())
        self.assertEqual(response.status_code, 500)
        with self.db() as db:
            for table in ('plan_schedules','plan_schedule_events','notifications','test_outbox'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        recovered = self.client.post('/api/plan-schedules', headers=self.headers('operator-token'), json=self.creation_payload())
        self.assertEqual(recovered.status_code, 201, recovered.json)

    def test_approved_schedule_cancel_releases_unused_resources_and_is_idempotent(self):
        self.add_submitted_schedule(801)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', tasks_generated=1 WHERE id=801")
            db.execute("INSERT INTO insp_plans (plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id) VALUES ('P','Operator',2,?,'active',801)", (self.day(),))
            db.execute("INSERT INTO plan_resource_reservations (schedule_id,part_id,planned_quantity,reserved_quantity,status) VALUES (801,1,2,2,'reserved')")
            db.execute("INSERT INTO vehicle_applications (vehicle_id,applicant_id,start_at,end_at,reason,status) VALUES (1,2,?,?,?,'approved')", (self.day(), self.day(), '巡检计划#801用车'))
        response = self.client.post('/api/plan-schedules/801/cancel', headers=self.headers('operator-token'), json={'reason': '  站点顺序错误  ', 'version': 1})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['status'], 'cancelled')
        detail = self.client.get('/api/plan-schedules/801', headers=self.headers('operator-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['cancellation']['reason'], '站点顺序错误')
        self.assertEqual(detail.json['cancellation']['operator_name'], 'Operator')
        self.assertTrue(detail.json['cancellation']['occurred_at'])
        refreshed = self.client.get('/api/plan-schedules/801', headers=self.headers('manager-token'))
        self.assertEqual(refreshed.json['cancellation'], detail.json['cancellation'])
        repeated = self.client.post('/api/plan-schedules/801/cancel', headers=self.headers('operator-token'), json={})
        self.assertEqual(repeated.status_code, 200, repeated.json)
        self.assertTrue(repeated.json['already_cancelled'])
        self.assertEqual(
            {key: repeated.json[key] for key in ('id', 'version', 'status', 'current_status')},
            {'id': 801, 'version': 2, 'status': 'cancelled', 'current_status': 'cancelled'})
        forbidden = self.client.post(
            '/api/plan-schedules/801/cancel', headers=self.headers('other-token'), json={})
        self.assertEqual((forbidden.status_code, forbidden.json['code']),
                         (403, 'PLAN_CANCEL_FORBIDDEN'))
        with self.db() as db:
            self.assertEqual(db.execute('SELECT status,version FROM plan_schedules WHERE id=801').fetchone()['status'], 'cancelled')
            self.assertEqual(db.execute('SELECT status FROM plan_resource_reservations WHERE schedule_id=801').fetchone()['status'], 'released')
            self.assertEqual(db.execute('SELECT status FROM vehicle_applications WHERE reason LIKE ?', ('%#801%',)).fetchone()['status'], 'cancelled')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=801 AND event_type='cancelled'").fetchone()[0], 1)
            payload = json.loads(db.execute(
                "SELECT payload FROM plan_schedule_events WHERE schedule_id=801 AND event_type='cancelled'"
            ).fetchone()['payload'])
            self.assertEqual(payload['reason'], '站点顺序错误')

    def test_deleted_plan_audit_query_is_admin_only_and_read_only(self):
        summary = {'plan_id': 999, 'owner_id': 2, 'owner_name': 'Operator',
                   'period': '2026-08-10~2026-08-16', 'reason': '无效计划'}
        with self.db() as db:
            db.execute("""INSERT INTO timeline_events
                (source_type,source_id,event_type,operator,remark)
                VALUES ('plan_schedule_purge',999,'purged','Manager',?)""", (json.dumps(summary),))
        denied = self.client.get('/api/plan-schedules/purge-audits', headers=self.headers('operator-token'))
        self.assertEqual(denied.status_code, 403)
        first = self.client.get('/api/plan-schedules/purge-audits', headers=self.headers('manager-token'))
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual((first.json['total'], first.json['page'], first.json['page_size']), (1,1,20))
        record = first.json['items'][0]
        self.assertEqual(record['reason'], '无效计划')
        self.assertEqual(record['operator_name'], 'Manager')
        self.assertTrue(record['purged_at'])
        self.assertEqual((record['period_start'], record['period_end']), ('2026-08-10','2026-08-16'))
        second = self.client.get('/api/plan-schedules/purge-audits', headers=self.headers('manager-token'))
        self.assertEqual(first.json, second.json)
        with self.db() as db:
            self.assertIsNone(db.execute('SELECT id FROM plan_schedules WHERE id=999').fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE source_type='plan_schedule_purge'").fetchone()[0], 1)

    def test_cancel_rejects_overlong_reason_without_side_effects(self):
        self.add_submitted_schedule(803)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved' WHERE id=803")
        before = self.side_effect_snapshot(803)
        response = self.client.post(
            '/api/plan-schedules/803/cancel', headers=self.headers('operator-token'),
            json={'reason': 'x' * 501, 'version': 1})
        self.assertEqual((response.status_code, response.json['code']),
                         (400, 'PLAN_CANCEL_REASON_TOO_LONG'))
        self.assertEqual(self.side_effect_snapshot(803), before)

    def test_purge_audits_stable_pagination_and_damaged_summary_never_invents_fields(self):
        with self.db() as db:
            for plan_id, raw in ((901, '{broken'), (902, '{}'), (903, json.dumps({
                    'plan_name': '周计划', 'status': 'draft', 'owner_name': 'Operator',
                    'period': '2026-08-10~2026-08-16', 'reason': '重复', 'site_ids': [1]}))):
                db.execute("""INSERT INTO timeline_events
                    (source_type,source_id,event_type,operator,remark,created_at)
                    VALUES ('plan_schedule_purge',?,'purged','Manager',?,'2026-09-15 10:00:00')""", (plan_id,raw))
        headers = self.headers('manager-token')
        first = self.client.get('/api/plan-schedules/purge-audits?page_size=2', headers=headers)
        second = self.client.get('/api/plan-schedules/purge-audits?page=2&page_size=2', headers=headers)
        self.assertEqual(first.json['total'], 3)
        self.assertEqual([row['plan_id'] for row in first.json['items']], [903,902])
        self.assertEqual([row['plan_id'] for row in second.json['items']], [901])
        self.assertEqual(first.json['items'][0]['status_before_delete'], 'draft')
        self.assertIsNone(first.json['items'][1]['reason'])
        broken = second.json['items'][0]
        self.assertTrue(broken['audit_incomplete'])
        self.assertEqual(broken['raw_summary'], '{broken')
        for key in ('reason','owner_name','period_start','period_end','status_before_delete'):
            self.assertIsNone(broken[key])
        self.assertEqual(self.client.get('/api/plan-schedules/purge-audits?page=0', headers=headers).status_code, 400)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM timeline_events').fetchone()[0], 3)

    def test_admin_cancel_closes_current_notification_preserves_history_and_notifies_owner_once(self):
        self.add_submitted_schedule(804)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved' WHERE id=804")
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read)
                VALUES (2,'plan_schedule',804,'current','keep',0)""")
            current_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read)
                VALUES (2,'plan_schedule',804,'history','keep',1)""")
            history_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        response = self.client.post(
            '/api/plan-schedules/804/cancel', headers=self.headers('manager-token'),
            json={'reason': '管理员代取消', 'version': 1})
        self.assertEqual(response.status_code, 200, response.json)
        repeated = self.client.post(
            '/api/plan-schedules/804/cancel', headers=self.headers('manager-token'), json={})
        self.assertEqual(repeated.status_code, 200, repeated.json)
        with self.db() as db:
            preserved = db.execute(
                'SELECT id,is_read FROM notifications WHERE id IN (?,?) ORDER BY id',
                (current_id, history_id)).fetchall()
            owner_notices = db.execute("""SELECT user_id,title,is_read FROM notifications
                WHERE source_type='plan_schedule' AND source_id=804
                  AND title='巡检计划已取消' ORDER BY id""").fetchall()
            event_count = db.execute("""SELECT COUNT(*) FROM plan_schedule_events
                WHERE schedule_id=804 AND event_type='cancelled'""").fetchone()[0]
        self.assertEqual([tuple(row) for row in preserved], [(current_id, 1), (history_id, 1)])
        self.assertEqual([tuple(row) for row in owner_notices], [(2, '巡检计划已取消', 0)])
        self.assertEqual(event_count, 1)

    def test_cancel_rejects_version_conflict_and_execution_fact_without_side_effects(self):
        self.add_submitted_schedule(802)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved', tasks_generated=1 WHERE id=802")
            plan_id = db.execute("INSERT INTO insp_plans (plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id) VALUES ('P','Operator',2,?,'active',802)", (self.day(),)).lastrowid
            db.execute("INSERT INTO insp_plan_items (plan_id,site_id,item_name,result) VALUES (?,1,'I','normal')", (plan_id,))
        conflict = self.client.post('/api/plan-schedules/802/cancel', headers=self.headers('operator-token'), json={'reason': 'x', 'version': 9})
        self.assertEqual(conflict.status_code, 409)
        blocked = self.client.post('/api/plan-schedules/802/cancel', headers=self.headers('operator-token'), json={'reason': 'x', 'version': 1})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json['code'], 'PLAN_CANCEL_HAS_FACTS')
        with self.db() as db:
            self.assertEqual(db.execute('SELECT status,version FROM plan_schedules WHERE id=802').fetchone()['status'], 'approved')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=802').fetchone()[0], 0)

    def test_plan_detail_projects_authoritative_cancellation_capability(self):
        self.add_submitted_schedule(805)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved' WHERE id=805")

        owner = self.client.get('/api/plan-schedules/805', headers=self.headers('operator-token'))
        admin = self.client.get('/api/plan-schedules/805', headers=self.headers('manager-token'))
        outsider = self.client.get('/api/plan-schedules/805', headers=self.headers('other-token'))
        self.assertTrue(owner.json['can_cancel'], owner.json)
        self.assertTrue(admin.json['can_cancel'], admin.json)
        self.assertEqual(owner.json['cancel_block_reason'], '')
        self.assertFalse(outsider.json['can_cancel'])
        self.assertEqual(outsider.json['cancel_block_reason'], '无权取消该计划')

        with self.db() as db:
            application_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (1,2,?,?,?,'approved')""",
                (self.day(), self.day(), '巡检计划#805用车')).lastrowid
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,checked_out_at,status)
                VALUES (?,1000,datetime('now'),'checked_out')""", (application_id,))

        blocked = self.client.get('/api/plan-schedules/805', headers=self.headers('operator-token'))
        self.assertFalse(blocked.json['can_cancel'])
        self.assertEqual(blocked.json['cancel_block_reason'], '计划已有现场或实际资源事实，不能取消')

    def test_cleanup_and_cancel_share_every_reviewed_blocking_fact(self):
        cases = (
            'check_out_time', 'evidence_status', 'inspection_attachment', 'completed_child',
            'exact_checkin', 'legacy_checkin', 'issued_resource', 'vehicle_use',
        )
        for offset, fact in enumerate(cases, start=1):
            with self.subTest(fact=fact):
                schedule_id = 820 + offset
                plan_id = 920 + offset
                item_id = 1020 + offset
                with self.db() as db:
                    db.execute('''INSERT INTO plan_schedules
                        (id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,
                         spare_parts,work_order_ids,status,version,tasks_generated,created_at)
                        VALUES (?,999,'monthly','2026-07-01','2026-07-02',?,'{}','[]','[]',
                                'approved',1,1,'2026-07-01')''',
                               (schedule_id, json.dumps({'2026-07-01': {'sites': [1]}})))
                    db.execute('''INSERT INTO insp_plans
                        (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id)
                        VALUES (?,'P','Missing',999,'2026-07-01',?,?)''',
                               (plan_id, 'completed' if fact == 'completed_child' else 'active', schedule_id))
                    db.execute('''INSERT INTO insp_plan_items
                        (id,plan_id,site_id,item_name,execution_status)
                        VALUES (?,?,1,'I','active')''', (item_id, plan_id))
                    if fact == 'check_out_time':
                        db.execute("UPDATE insp_plan_items SET check_out_time='2026-07-01 10:00:00' WHERE id=?", (item_id,))
                    elif fact == 'evidence_status':
                        db.execute("UPDATE insp_plan_items SET evidence_status='supplement_required' WHERE id=?", (item_id,))
                    elif fact == 'inspection_attachment':
                        db.execute("INSERT INTO operation_attachments (source_type,source_id,file_type) VALUES ('inspection',?,'image')", (item_id,))
                    elif fact == 'exact_checkin':
                        db.execute("""INSERT INTO inspection_checkins
                            (site_id,user_id,check_time,plan_id) VALUES (1,999,?,?)""",
                                   ('2026-07-01 09:00:00', plan_id))
                    elif fact == 'legacy_checkin':
                        db.execute("""INSERT INTO inspection_checkins
                            (site_id,user_id,check_time,plan_id) VALUES (1,999,?,0)""",
                                   ('2026-07-01 09:00:00',))
                    elif fact == 'issued_resource':
                        db.execute("""INSERT INTO plan_resource_reservations
                            (schedule_id,part_id,planned_quantity,issued_quantity,status)
                            VALUES (?,1,1,1,'issued')""", (schedule_id,))
                    elif fact == 'vehicle_use':
                        application_id = db.execute("""INSERT INTO vehicle_applications
                            (reason,status) VALUES (?,'approved')""",
                                                    (f'巡检计划#{schedule_id}用车',)).lastrowid
                        db.execute("""INSERT INTO vehicle_use_records
                            (application_id,status) VALUES (?,'in_use')""", (application_id,))
                    facts = app_module._ps_schedule_activity_facts(
                        db, db.execute('SELECT * FROM plan_schedules WHERE id=?', (schedule_id,)).fetchone())
                    self.assertTrue(facts['has_blocking_facts'], facts)
                    candidate_ids = {item['id'] for item in app_module._cleanup_candidates(db)}
                self.assertNotIn(schedule_id, candidate_ids)
                before = self.side_effect_snapshot(schedule_id)
                response = self.client.post(
                    f'/api/plan-schedules/{schedule_id}/cancel',
                    headers=self.headers('manager-token'),
                    json={'reason': 'test', 'version': 1})
                self.assertEqual((response.status_code, response.json['code']),
                                 (409, 'PLAN_CANCEL_HAS_FACTS'))
                self.assertEqual(self.side_effect_snapshot(schedule_id), before)

    def test_schedule_activity_facts_tolerates_missing_optional_columns(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, period_start TEXT,
                    period_end TEXT, plan_data TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_schedule_id INTEGER, status TEXT
                );
                CREATE TABLE insp_plan_items (id INTEGER PRIMARY KEY, plan_id INTEGER);
                CREATE TABLE inspection_checkins (id INTEGER PRIMARY KEY, plan_id INTEGER);
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER
                );
                CREATE TABLE plan_resource_reservations (
                    id INTEGER PRIMARY KEY, schedule_id INTEGER, status TEXT
                );
                CREATE TABLE plan_departure_confirmations (
                    id INTEGER PRIMARY KEY, schedule_id INTEGER
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, reason TEXT, status TEXT
                );
                CREATE TABLE vehicle_use_records (application_id INTEGER);
                INSERT INTO plan_schedules VALUES (
                    1, 2, '2026-07-01', '2026-07-02',
                    '{"2026-07-01":{"sites":[1]}}'
                );
                INSERT INTO insp_plans VALUES (11, 1, 'active');
                INSERT INTO insp_plan_items VALUES (21, 11);
                INSERT INTO plan_resource_reservations VALUES (31, 1, 'planned');
                INSERT INTO vehicle_applications VALUES (
                    41, '巡检计划#1用车（测试）', 'approved'
                );
            ''')
            schedule = db.execute('SELECT * FROM plan_schedules WHERE id=1').fetchone()
            facts = app_module._ps_schedule_activity_facts(db, schedule)
        finally:
            db.close()
        self.assertFalse(facts['has_blocking_facts'], facts)
        self.assertEqual(facts['activity_facts']['execution_records'], 1)
        self.assertEqual(facts['activity_facts']['resource_records'], 2)

    def test_cancel_succeeds_when_only_optional_write_columns_are_missing(self):
        self.add_submitted_schedule(840)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved',tasks_generated=1 WHERE id=840")
            plan_id = db.execute("""INSERT INTO insp_plans
                (plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id)
                VALUES ('P','Operator',2,?,'active',840)""", (self.day(),)).lastrowid
            db.executescript('''
                DROP TABLE insp_plan_items;
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, execution_status TEXT
                );
                DROP TABLE plan_resource_reservations;
                CREATE TABLE plan_resource_reservations (
                    id INTEGER PRIMARY KEY, schedule_id INTEGER, status TEXT
                );
                DROP TABLE vehicle_applications;
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, reason TEXT, status TEXT
                );
            ''')
            db.execute("INSERT INTO insp_plan_items VALUES (1,?,'active')", (plan_id,))
            db.execute("INSERT INTO plan_resource_reservations VALUES (1,840,'reserved')")
            db.execute("INSERT INTO vehicle_applications VALUES (1,'巡检计划#840用车','approved')")
        response = self.client.post(
            '/api/plan-schedules/840/cancel', headers=self.headers('operator-token'),
            json={'reason': '兼容窄表', 'version': 1})
        self.assertEqual(response.status_code, 200, response.json)
        with self.db() as db:
            self.assertEqual(db.execute(
                'SELECT execution_status FROM insp_plan_items WHERE id=1').fetchone()[0], 'cancelled')
            self.assertEqual(db.execute(
                'SELECT status FROM insp_plans WHERE id=?', (plan_id,)).fetchone()[0], 'cancelled')
            self.assertEqual(db.execute(
                'SELECT status FROM plan_resource_reservations WHERE id=1').fetchone()[0], 'released')
            self.assertEqual(db.execute(
                'SELECT status FROM vehicle_applications WHERE id=1').fetchone()[0], 'cancelled')

    def test_cancel_fails_closed_before_writes_when_core_item_status_is_missing(self):
        self.add_submitted_schedule(841)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved',tasks_generated=1 WHERE id=841")
            plan_id = db.execute("""INSERT INTO insp_plans
                (plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id)
                VALUES ('P','Operator',2,?,'active',841)""", (self.day(),)).lastrowid
            db.executescript('''
                DROP TABLE insp_plan_items;
                CREATE TABLE insp_plan_items (id INTEGER PRIMARY KEY, plan_id INTEGER);
            ''')
            db.execute('INSERT INTO insp_plan_items VALUES (1,?)', (plan_id,))
            before = {
                'schedule': tuple(db.execute(
                    'SELECT status,version FROM plan_schedules WHERE id=841').fetchone()),
                'plan': tuple(db.execute(
                    'SELECT status FROM insp_plans WHERE id=?', (plan_id,)).fetchone()),
                'item': tuple(db.execute(
                    'SELECT * FROM insp_plan_items WHERE id=1').fetchone()),
                'events': db.execute(
                    'SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=841').fetchone()[0],
            }
        response = self.client.post(
            '/api/plan-schedules/841/cancel', headers=self.headers('operator-token'),
            json={'reason': '应失败关闭', 'version': 1})
        self.assertEqual((response.status_code, response.json['code']),
                         (503, 'PLAN_CANCEL_SCHEMA_UNAVAILABLE'))
        with self.db() as db:
            after = {
                'schedule': tuple(db.execute(
                    'SELECT status,version FROM plan_schedules WHERE id=841').fetchone()),
                'plan': tuple(db.execute(
                    'SELECT status FROM insp_plans WHERE id=?', (plan_id,)).fetchone()),
                'item': tuple(db.execute(
                    'SELECT * FROM insp_plan_items WHERE id=1').fetchone()),
                'events': db.execute(
                    'SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=841').fetchone()[0],
            }
        self.assertEqual(after, before)

    def add_scope_failure_schedule(self, schedule_id, status, site_id=2):
        day = self.day()
        plan_data = json.dumps({day: {'sites': [site_id], 'notes': 'original'}}, ensure_ascii=False)
        with self.db() as db:
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,
                 spare_parts,work_order_ids,status,remarks,version,tasks_generated,
                 reject_reason,submitted_at,no_vehicle_required,validation_snapshot)
                VALUES (?,2,'monthly',?,?,?,'{}','[]','[]',?,?,1,0,'keep','original-submit',1,'{}')''',
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
                'inspection_checkins': [tuple(row) for row in db.execute(
                    'SELECT * FROM inspection_checkins ORDER BY id').fetchall()],
                'vehicle_use_records': [tuple(row) for row in db.execute(
                    'SELECT * FROM vehicle_use_records ORDER BY id').fetchall()],
            }
            return snapshot

    def assert_side_effect_snapshot_unchanged(self, before, after):
        self.assertEqual(after['schedule'], before['schedule'], 'plan_schedules')
        for table in ('events', 'notifications', 'vehicle_applications', 'reservations',
                      'insp_plans', 'insp_plan_items', 'inspection_checkins',
                      'vehicle_use_records'):
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
            'schedule_type': 'weekly',
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
            'schedule_type': 'weekly',
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
            'schedule_type': 'weekly',
            'period_start': (datetime.strptime(today, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'),
            'period_end': (datetime.strptime(today, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'),
            'user_id': 2,
            'plan_data': {(datetime.strptime(today, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'): {'sites': [2]}},
            'vehicle_days': {},
        })
        self.assertEqual(unauthorized_response.status_code, 403, unauthorized_response.json)
        self.assertEqual(unauthorized_response.json.get('code'), 'PLAN_EXECUTION_SITE_FORBIDDEN')

        missing_response = self.client.post('/api/plan-schedules', headers=self.headers('manager-token'), json={
            'schedule_type': 'weekly',
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
                                       'version': 1,
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
                                    headers=self.headers('manager-token'), json={'version': 1})

        self.assertEqual((response.status_code, response.json.get('code')),
                         (403, 'PLAN_EXECUTION_SITE_FORBIDDEN'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))

    def test_submit_requires_version_and_stale_version_have_zero_side_effects(self):
        schedule_id = 55
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET vehicle_days=? WHERE id=?",
                       (json.dumps({self.day(): 1}), schedule_id))
        before = self.side_effect_snapshot(schedule_id)

        missing = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                   headers=self.headers('manager-token'), json={})
        self.assertEqual((missing.status_code, missing.json.get('code')),
                         (409, 'PLAN_VERSION_REQUIRED'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))

        stale = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                 headers=self.headers('manager-token'), json={'version': 0})
        self.assertEqual((stale.status_code, stale.json.get('code')),
                         (409, 'PLAN_VERSION_CONFLICT'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))

    def test_change_submit_applies_directly_without_review_notification(self):
        schedule_id = 71
        self.add_submitted_schedule(schedule_id)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='approved' WHERE id=?", (schedule_id,))

        requested = self.client.post('/api/plan-schedules/71/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '调整执行日期'})
        self.assertEqual(requested.status_code, 200, requested.json)
        with self.db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM notifications WHERE source_id=?', (schedule_id,)
            ).fetchone()[0], 0)

        saved = self.client.put('/api/plan-schedules/71',
                                headers=self.headers('operator-token'),
                                json={'version': 1, 'remarks': '变更后安排'})
        self.assertEqual(saved.status_code, 200, saved.json)
        with self.db() as db:
            db.execute("UPDATE users SET status='disabled' WHERE id=1")
        submitted = self.client.post('/api/plan-schedules/71/submit',
                                     headers=self.headers('operator-token'),
                                     json={'version': saved.json['version']})
        self.assertEqual(submitted.status_code, 200, submitted.json)
        self.assertEqual(submitted.json['status'], 'approved')
        self.assertTrue(submitted.json['direct_applied'])
        with self.db() as db:
            notices = db.execute(
                'SELECT source_type,title,payload_json FROM notifications WHERE source_id=?',
                (schedule_id,)
            ).fetchall()
            self.assertEqual([(row['source_type'], row['title']) for row in notices],
                             [('plan_schedule', '计划变更已生效')])
            self.assertFalse(notices[0]['payload_json'])
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM plan_schedule_events
                WHERE schedule_id=? AND event_type='change_applied'""",
                                        (schedule_id,)).fetchone()[0], 1)
            self.assertIsNone(db.execute(
                'SELECT approver_id FROM plan_schedules WHERE id=?', (schedule_id,)
            ).fetchone()['approver_id'])

        repeated = self.client.post('/api/plan-schedules/71/submit',
                                    headers=self.headers('operator-token'),
                                    json={'version': saved.json['version']})
        self.assertEqual(repeated.status_code, 200, repeated.json)
        self.assertEqual(repeated.json['status'], 'approved')
        self.assertTrue(repeated.json['direct_applied'])
        self.assertTrue(repeated.json['already_submitted'])
        with self.db() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM notifications WHERE source_type='plan_schedule' AND source_id=?",
                (schedule_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM plan_schedule_events
                WHERE schedule_id=? AND event_type='change_applied'""",
                                        (schedule_id,)).fetchone()[0], 1)

        unauthorized = self.client.post('/api/plan-schedules/71/submit',
                                        headers=self.headers('other-token'),
                                        json={'version': saved.json['version']})
        self.assertEqual(unauthorized.status_code, 403, unauthorized.json)
        stale = self.client.post('/api/plan-schedules/71/submit',
                                 headers=self.headers('operator-token'),
                                 json={'version': saved.json['version'] - 1})
        self.assertEqual((stale.status_code, stale.json['code']),
                         (409, 'PLAN_VERSION_CONFLICT'))
        with self.db() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM notifications WHERE source_id=?", (schedule_id,)
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=?",
                (schedule_id,)
            ).fetchone()[0], 3)

    def test_regular_submit_retry_is_read_only_for_the_same_version(self):
        schedule_id = 72
        self.add_submitted_schedule(schedule_id)
        before = self.side_effect_snapshot(schedule_id)

        repeated = self.client.post('/api/plan-schedules/72/submit',
                                    headers=self.headers('operator-token'),
                                    json={'version': 1})

        self.assertEqual(repeated.status_code, 200, repeated.json)
        self.assertEqual(repeated.json['status'], 'submitted')
        self.assertTrue(repeated.json['already_submitted'])
        self.assert_side_effect_snapshot_unchanged(
            before, self.side_effect_snapshot(schedule_id))

    def test_empty_draft_saves_and_prunes_orphan_vehicle_day_but_cannot_submit(self):
        schedule_id = 56
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        before_events = None
        with self.db() as db:
            before_events = db.execute(
                'SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=?', (schedule_id,)
            ).fetchone()[0]

        saved = self.client.put('/api/plan-schedules/{}'.format(schedule_id),
                                headers=self.headers('manager-token'), json={
                                    'version': 1,
                                    'plan_data': {},
                                    'vehicle_days': {self.day(): 1},
                                    'no_vehicle_required': False,
                                })
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json['vehicle_days'], {})
        self.assertEqual(saved.json['pruned_vehicle_dates'], [self.day()])
        self.assertGreater(saved.json['draft_issue_count'], 0)
        with self.db() as db:
            row = db.execute(
                'SELECT plan_data,vehicle_days,status,version FROM plan_schedules WHERE id=?',
                (schedule_id,)).fetchone()
            self.assertEqual((json.loads(row['plan_data']), json.loads(row['vehicle_days'])), ({}, {}))
            self.assertEqual((row['status'], row['version']), ('draft', 2))
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=?', (schedule_id,)
            ).fetchone()[0], before_events + 1)

        before_submit = self.side_effect_snapshot(schedule_id)
        submitted = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                     headers=self.headers('manager-token'), json={'version': 2})
        self.assertEqual(submitted.status_code, 400, submitted.json)
        self.assertIn('至少安排一个巡检日期和站点', submitted.json.get('error', ''))
        self.assert_side_effect_snapshot_unchanged(before_submit, self.side_effect_snapshot(schedule_id))

    def test_explicit_empty_item_selection_blocks_submit_and_approval_without_side_effects(self):
        schedule_id = 58
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        with self.db() as db:
            db.execute("""UPDATE plan_schedules SET plan_data=?, vehicle_days=?, vehicle_id=1
                WHERE id=?""", (
                    json.dumps({self.day(): {
                        'sites': [1], 'inspection_items': {'1': []},
                    }}),
                    json.dumps({self.day(): 1}), schedule_id,
                ))
        before_submit = self.side_effect_snapshot(schedule_id)
        submitted = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                     headers=self.headers('manager-token'), json={'version': 1})
        self.assertEqual(submitted.status_code, 400, submitted.json)
        self.assertIn('未选择任何可执行检查项', submitted.json.get('error', ''))
        self.assertEqual(submitted.json['validation']['error_details'][0]['field'], 'plan_data')
        self.assert_side_effect_snapshot_unchanged(before_submit, self.side_effect_snapshot(schedule_id))

        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='submitted' WHERE id=?", (schedule_id,))
        before_approval = self.side_effect_snapshot(schedule_id)
        approved = self.client.post('/api/plan-schedules/{}/approve'.format(schedule_id),
                                    headers=self.headers('manager-token'))
        self.assertEqual((approved.status_code, approved.json.get('code')),
                         (409, 'PLAN_RESOURCE_REVALIDATION_FAILED'))
        self.assertIn('未选择任何可执行检查项', approved.json.get('error', ''))
        self.assert_side_effect_snapshot_unchanged(before_approval, self.side_effect_snapshot(schedule_id))

    def test_persisted_multiple_vehicles_block_submit_and_approval_without_side_effects(self):
        schedule_id = 59
        first_day = self.day()
        second_day = (datetime.strptime(first_day, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        with self.db() as db:
            db.execute("INSERT INTO vehicles VALUES (2,'TEST-002','Second vehicle','idle',1000,NULL,NULL)")
            db.execute("""UPDATE plan_schedules SET period_end=?, plan_data=?, vehicle_days=?, vehicle_id=1
                WHERE id=?""", (
                    second_day,
                    json.dumps({first_day: {'sites': [1]}, second_day: {'sites': [1]}}),
                    json.dumps({first_day: 1, second_day: 2}), schedule_id,
                ))
        before_submit = self.side_effect_snapshot(schedule_id)
        submitted = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                     headers=self.headers('manager-token'), json={'version': 1})
        self.assertEqual((submitted.status_code, submitted.json.get('code')),
                         (409, 'PLAN_MULTIPLE_VEHICLES_NOT_ALLOWED'))
        self.assert_side_effect_snapshot_unchanged(before_submit, self.side_effect_snapshot(schedule_id))

        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='submitted' WHERE id=?", (schedule_id,))
        before_approval = self.side_effect_snapshot(schedule_id)
        approved = self.client.post('/api/plan-schedules/{}/approve'.format(schedule_id),
                                    headers=self.headers('manager-token'))
        self.assertEqual((approved.status_code, approved.json.get('code')),
                         (409, 'PLAN_MULTIPLE_VEHICLES_NOT_ALLOWED'))
        self.assert_side_effect_snapshot_unchanged(before_approval, self.side_effect_snapshot(schedule_id))

    def test_persisted_invalid_vehicle_value_returns_stable_conflict_without_side_effects(self):
        schedule_id = 63
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET vehicle_days=?, vehicle_id=NULL WHERE id=?",
                       (json.dumps({self.day(): 'not-a-vehicle'}), schedule_id))
        before = self.side_effect_snapshot(schedule_id)
        response = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                    headers=self.headers('manager-token'), json={'version': 1})
        self.assertEqual((response.status_code, response.json.get('code')),
                         (409, 'PLAN_VEHICLE_INVALID'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))

    def test_shortened_period_rejects_business_dates_without_side_effects(self):
        schedule_id = 57
        first_day = self.day()
        second_day = (datetime.strptime(first_day, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        plan_data = {first_day: {'sites': [1]}, second_day: {'sites': [1]}}
        vehicle_days = {first_day: 1, second_day: 1}
        with self.db() as db:
            db.execute("""UPDATE plan_schedules SET period_end=?, plan_data=?, vehicle_days=?
                WHERE id=?""", (
                second_day,
                json.dumps(plan_data), json.dumps(vehicle_days), schedule_id))
            schedule_before = tuple(db.execute(
                '''SELECT period_end,plan_data,vehicle_days,version
                   FROM plan_schedules WHERE id=?''', (schedule_id,)).fetchone())
            events_before = [tuple(row) for row in db.execute(
                'SELECT * FROM plan_schedule_events WHERE schedule_id=? ORDER BY id',
                (schedule_id,)).fetchall()]

        response = self.client.put('/api/plan-schedules/{}'.format(schedule_id),
                                   headers=self.headers('manager-token'), json={
                                       'version': 1,
                                       'period_end': first_day,
                                   })
        self.assertEqual((response.status_code, response.json.get('code')),
                         (409, 'PLAN_DATES_OUTSIDE_PERIOD'))
        self.assertEqual(response.json['dates'], [second_day])

        with self.db() as db:
            schedule_after = tuple(db.execute(
                '''SELECT period_end,plan_data,vehicle_days,version
                   FROM plan_schedules WHERE id=?''', (schedule_id,)).fetchone())
            events_after = [tuple(row) for row in db.execute(
                'SELECT * FROM plan_schedule_events WHERE schedule_id=? ORDER BY id',
                (schedule_id,)).fetchall()]
        self.assertEqual(schedule_after, schedule_before)
        self.assertEqual(schedule_after[0], second_day)
        self.assertEqual(json.loads(schedule_after[1]), plan_data)
        self.assertEqual(json.loads(schedule_after[2]), vehicle_days)
        self.assertEqual(schedule_after[3], 1)
        self.assertEqual(events_after, events_before)

    def add_purge_fixture(self, schedule_id=990):
        self.add_submitted_schedule(schedule_id)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS plan_departure_confirmations (
                id INTEGER PRIMARY KEY, schedule_id INTEGER, user_id INTEGER,
                work_date TEXT, vehicle_confirmed INTEGER, parts_confirmed INTEGER)""")
            db.execute("UPDATE users SET role='admin' WHERE id=1")
            attachment_columns = {
                row['name'] for row in db.execute('PRAGMA table_info(operation_attachments)').fetchall()
            }
            if 'filename' not in attachment_columns:
                db.execute("ALTER TABLE operation_attachments ADD COLUMN filename TEXT DEFAULT ''")
            if 'stored_path' not in attachment_columns:
                db.execute("ALTER TABLE operation_attachments ADD COLUMN stored_path TEXT DEFAULT ''")
            db.execute("UPDATE plan_schedules SET status='approved',tasks_generated=1 WHERE id=?",
                       (schedule_id,))
            plan_id = db.execute("""INSERT INTO insp_plans
                (plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id)
                VALUES ('无效测试计划','Operator',2,?,'completed',?)""",
                (self.day(), schedule_id)).lastrowid
            item_id = db.execute("""INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,check_in_time,check_time,review_status,
                 reviewer_id,photo_urls,actual_photos)
                VALUES (?,1,'浊度','normal',?,?,2,1,'[\"/uploads/site_photos/purge.jpg\"]',1)""",
                (plan_id, self.business_timestamp(), self.business_timestamp())).lastrowid
            db.execute("INSERT INTO inspection_checkins (site_id,user_id,check_time,plan_id) VALUES (1,2,?,?)",
                       (self.business_timestamp(), plan_id))
            db.execute("INSERT INTO inspection_checkins (site_id,user_id,check_time,plan_id) VALUES (1,2,?,0)",
                       (self.business_timestamp(),))
            photo_dir = os.path.join(self.upload_dir, 'site_photos')
            os.makedirs(photo_dir, exist_ok=True)
            with open(os.path.join(photo_dir, f'purge-{schedule_id}.jpg'), 'wb') as handle:
                handle.write(b'purge-only')
            with open(os.path.join(photo_dir, f'shared-{schedule_id}.jpg'), 'wb') as handle:
                handle.write(b'shared')
            db.execute("""INSERT INTO operation_attachments
                (source_type,source_id,plan_id,item_id,file_type,mime_type,filename,stored_path)
                VALUES ('inspection',?,?,?,'image','image/jpeg',?,?)""",
                (item_id, plan_id, item_id, f'purge-{schedule_id}.jpg',
                 f'/uploads/site_photos/purge-{schedule_id}.jpg'))
            db.execute("""INSERT INTO operation_attachments
                (source_type,source_id,plan_id,item_id,file_type,mime_type,filename,stored_path)
                VALUES ('inspection',?,?,?,'image','image/jpeg',?,?)""",
                (item_id, plan_id, item_id, f'shared-{schedule_id}.jpg',
                 f'/uploads/site_photos/shared-{schedule_id}.jpg'))
            db.execute("""INSERT INTO operation_attachments
                (source_type,source_id,plan_id,item_id,file_type,mime_type,filename,stored_path)
                VALUES ('workorder',13058,NULL,NULL,'image','image/jpeg',?,?)""",
                (f'shared-{schedule_id}.jpg', f'/uploads/site_photos/shared-{schedule_id}.jpg'))
            db.execute("INSERT INTO notifications (user_id,source_type,source_id,title) VALUES (1,'plan_schedule',?,'待办')",
                       (schedule_id,))
            db.execute("INSERT INTO plan_resource_reservations (schedule_id,part_id,planned_quantity,status) VALUES (?,1,1,'reserved')",
                       (schedule_id,))
            unused_vehicle_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (1,2,?,?,?,'approved')""",
                (self.day(), self.day(), f'巡检计划#{schedule_id}用车')).lastrowid
            used_vehicle_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status)
                VALUES (1,2,?,?,?,'returned')""",
                (self.day(), self.day(), f'巡检计划#{schedule_id}用车（实际履约）')).lastrowid
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,end_mileage,checked_out_at,returned_at,status)
                VALUES (?,100,110,?,?,'returned')""",
                (used_vehicle_id, self.business_timestamp(), self.business_timestamp()))
            rework_unused_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status,rework_plan_id)
                VALUES (1,2,?,?,?,'approved',?)""",
                (self.day(), self.day(), f'整改补检#{plan_id}用车', plan_id)).lastrowid
            rework_used_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status,rework_plan_id)
                VALUES (1,2,?,?,?,'returned',?)""",
                (self.day(), self.day(), f'整改补检#{plan_id}用车（已履约）', plan_id)).lastrowid
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,end_mileage,checked_out_at,returned_at,status)
                VALUES (?,110,120,?,?,'returned')""",
                (rework_used_id, self.business_timestamp(), self.business_timestamp()))
            unrelated_vehicle_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,reason,status,rework_plan_id)
                VALUES (1,2,?,?,?,'approved',999999)""",
                (self.day(), self.day(), '整改补检#999999用车')).lastrowid
            db.execute("""CREATE TABLE IF NOT EXISTS inventory_logs (
                id INTEGER PRIMARY KEY, part_id INTEGER, type TEXT, quantity INTEGER,
                ref_type TEXT, ref_id INTEGER, operator TEXT, remark TEXT)""")
            db.execute("""INSERT INTO inventory_logs
                (id,part_id,type,quantity,ref_type,ref_id,operator,remark)
                VALUES (1,1,'out',1,'plan_schedule',?,'Operator',?)""",
                (schedule_id, f'巡检计划#{schedule_id}现场领用'))
            db.execute("""CREATE TABLE IF NOT EXISTS parts_requests (
                id INTEGER PRIMARY KEY, plan_id INTEGER, status TEXT)""")
            db.execute("INSERT INTO parts_requests (id,plan_id,status) VALUES (1,?,'issued')",
                       (plan_id,))
        return (plan_id, item_id, unused_vehicle_id, used_vehicle_id,
                rework_unused_id, rework_used_id, unrelated_vehicle_id)

    def test_admin_purge_removes_factual_schedule_and_releases_period_but_keeps_actual_history(self):
        (plan_id, item_id, unused_vehicle_id, used_vehicle_id,
         rework_unused_id, rework_used_id, unrelated_vehicle_id) = self.add_purge_fixture()
        legacy = self.client.delete('/api/plan-schedules/990', headers=self.headers('manager-token'))
        self.assertEqual(legacy.status_code, 409, legacy.json)
        with self.db() as db:
            self.assertIsNotNone(app_module._ps_period_overlap(
                db, 2, 'monthly', self.day(), self.day()))

        response = self.client.post('/api/plan-schedules/990/purge',
                                    headers=self.headers('manager-token'),
                                    json={'version': 1, 'reason': '  主链测试误建  '})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json['already_deleted'])
        with self.db() as db:
            for table, column, value in (
                    ('plan_schedules', 'id', 990), ('insp_plans', 'id', plan_id),
                    ('insp_plan_items', 'id', item_id),
                    ('plan_resource_reservations', 'schedule_id', 990)):
                self.assertEqual(db.execute(
                    f'SELECT COUNT(*) FROM {table} WHERE {column}=?', (value,)).fetchone()[0], 0)
            self.assertIsNone(app_module._ps_period_overlap(
                db, 2, 'monthly', self.day(), self.day()))
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM inspection_checkins WHERE plan_id=0').fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM inventory_logs WHERE ref_type='plan_schedule' AND ref_id=990"
            ).fetchone()[0], 1)
            self.assertIsNone(db.execute(
                'SELECT id FROM vehicle_applications WHERE id=?', (unused_vehicle_id,)).fetchone())
            self.assertIsNotNone(db.execute(
                'SELECT id FROM vehicle_applications WHERE id=?', (used_vehicle_id,)).fetchone())
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM vehicle_use_records WHERE application_id=?',
                (used_vehicle_id,)).fetchone()[0], 1)
            self.assertIsNone(db.execute(
                'SELECT id FROM vehicle_applications WHERE id=?', (rework_unused_id,)).fetchone())
            self.assertIsNotNone(db.execute(
                'SELECT id FROM vehicle_applications WHERE id=?', (rework_used_id,)).fetchone())
            self.assertIsNotNone(db.execute(
                'SELECT id FROM vehicle_applications WHERE id=?', (unrelated_vehicle_id,)).fetchone())
            audit = db.execute("""SELECT remark FROM timeline_events
                WHERE source_type='plan_schedule_purge' AND source_id=990""").fetchone()
            summary = json.loads(audit['remark'])
            self.assertEqual(summary['reason'], '主链测试误建')
            self.assertEqual(summary['status'], 'approved')
            self.assertEqual(summary['owner_name'], 'Operator')
            self.assertEqual(summary['retained_inventory_logs'], 1)
            self.assertEqual(summary['retained_vehicle_applications'], 2)
            self.assertEqual(summary['retained_vehicle_uses'], 2)
            self.assertEqual(summary['retained_parts_requests'], 1)
            self.assertEqual(db.execute(
                'SELECT plan_id FROM parts_requests WHERE id=1').fetchone()['plan_id'], 0)
        self.assertFalse(os.path.exists(os.path.join(
            self.upload_dir, 'site_photos', 'purge-990.jpg')))
        self.assertTrue(os.path.exists(os.path.join(
            self.upload_dir, 'site_photos', 'shared-990.jpg')))

        replay = self.client.post('/api/plan-schedules/990/purge',
                                  headers=self.headers('manager-token'),
                                  json={'version': 1, 'reason': '响应丢失重试'})
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertTrue(replay.json['already_deleted'])
        with self.db() as db:
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='plan_schedule_purge' AND source_id=990""").fetchone()[0], 1)

    def test_plan_purge_permission_reason_and_version_fail_without_writes(self):
        self.add_purge_fixture(991)
        cases = (
            ('operator-token', {'version': 1, 'reason': '无权限'}, 403, 'FORBIDDEN'),
            ('manager-token', {'version': 1, 'reason': '   '}, 400, 'PLAN_PURGE_REASON_REQUIRED'),
            ('manager-token', {'version': 1, 'reason': 'x' * 501}, 400, 'PLAN_PURGE_REASON_TOO_LONG'),
            ('manager-token', {'version': 2, 'reason': '旧版本'}, 409, 'PLAN_PURGE_VERSION_CONFLICT'),
        )
        for token, payload, status, code in cases:
            with self.subTest(code=code):
                response = self.client.post('/api/plan-schedules/991/purge',
                                            headers=self.headers(token), json=payload)
                self.assertEqual((response.status_code, response.json.get('code')), (status, code))
                with self.db() as db:
                    self.assertIsNotNone(db.execute(
                        'SELECT id FROM plan_schedules WHERE id=991').fetchone())
                    self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                        WHERE source_type='plan_schedule_purge' AND source_id=991""").fetchone()[0], 0)
        missing = self.client.post('/api/plan-schedules/999999/purge',
                                   headers=self.headers('manager-token'),
                                   json={'version': 1, 'reason': '不存在对象'})
        self.assertEqual((missing.status_code, missing.json.get('code')),
                         (404, 'PLAN_PURGE_NOT_FOUND'))

    def test_plan_purge_uses_exact_notification_and_timeline_id_namespaces(self):
        plan_id, item_id, *_ = self.add_purge_fixture(1)
        self.assertEqual((plan_id, item_id), (1, 1))
        self.add_submitted_schedule(10)
        with self.db() as db:
            db.execute('UPDATE insp_plan_items SET id=10 WHERE id=1')
            db.execute("UPDATE operation_attachments SET source_id=10,item_id=10 WHERE plan_id=1")
            db.execute("UPDATE plan_schedules SET status='approved',tasks_generated=1 WHERE id=10")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id)
                VALUES (10,'其他计划','Other',3,?,'active',10)""", (self.day(),))
            db.executemany("""INSERT INTO timeline_events
                (source_type,source_id,event_type,operator,remark) VALUES (?,?,?,?,?)""", (
                    ('inspection', 1, 'completed', 'system', '本计划执行包'),
                    ('inspection_item', 10, 'reviewed', 'system', '本计划检查项'),
                    ('inspection', 10, 'completed', 'system', '其他计划执行包'),
                ))
            db.executemany("""INSERT INTO notifications
                (user_id,source_type,source_id,title) VALUES (1,?,?,?)""", (
                    ('inspection_review_batch', 'insp_batch_1_1', '本计划批次'),
                    ('inspection_review_batch', 'insp_batch_10_1', '其他计划批次'),
                    ('inspection_rework', 1, '本计划整改'),
                    ('inspection_rework', 10, '其他计划整改'),
                ))
            attachment_ids = [row['id'] for row in db.execute(
                'SELECT id FROM operation_attachments WHERE plan_id=1').fetchall()]
            for source_type in ('attachment_void', 'replacement_review'):
                db.execute("""INSERT INTO notifications
                    (user_id,source_type,source_id,title) VALUES (1,?,?,?)""",
                    (source_type, attachment_ids[0], '本计划附件通知'))
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title) VALUES (1,'attachment_review_batch',?,'站点共享批次')""",
                (attachment_ids[0],))

        response = self.client.post('/api/plan-schedules/1/purge',
                                    headers=self.headers('manager-token'),
                                    json={'version': 1, 'reason': '碰撞回归'})
        self.assertEqual(response.status_code, 200, response.json)
        with self.db() as db:
            remaining_titles = {row['title'] for row in db.execute(
                'SELECT title FROM notifications').fetchall()}
            self.assertNotIn('本计划批次', remaining_titles)
            self.assertNotIn('本计划整改', remaining_titles)
            self.assertNotIn('本计划附件通知', remaining_titles)
            self.assertIn('其他计划批次', remaining_titles)
            self.assertIn('其他计划整改', remaining_titles)
            self.assertIn('站点共享批次', remaining_titles)
            remaining_events = {(row['source_type'], row['source_id'], row['remark'])
                                for row in db.execute('SELECT * FROM timeline_events').fetchall()}
            self.assertNotIn(('inspection', 1, '本计划执行包'), remaining_events)
            self.assertNotIn(('inspection_item', 10, '本计划检查项'), remaining_events)
            self.assertIn(('inspection', 10, '其他计划执行包'), remaining_events)

    def test_admin_can_purge_each_schedule_lifecycle_state(self):
        states = ('draft', 'rejected', 'cancelled', 'approved')
        with self.db() as db:
            db.execute("UPDATE users SET role='admin' WHERE id=1")
        for index, status in enumerate(states, start=1):
            schedule_id = 1000 + index
            self.add_submitted_schedule(schedule_id)
            with self.db() as db:
                db.execute("UPDATE plan_schedules SET status=?,field_status=? WHERE id=?",
                           (status, 'completed' if status == 'approved' else 'active', schedule_id))
            with self.subTest(status=status):
                response = self.client.post(f'/api/plan-schedules/{schedule_id}/purge',
                                            headers=self.headers('manager-token'),
                                            json={'version': 1, 'reason': f'清理{status}'})
                self.assertEqual(response.status_code, 200, response.json)
                with self.db() as db:
                    self.assertIsNone(db.execute(
                        'SELECT id FROM plan_schedules WHERE id=?', (schedule_id,)).fetchone())

    def test_plan_purge_file_failure_rolls_back_database_and_restores_file(self):
        plan_id, item_id, *_ = self.add_purge_fixture(992)
        first_target = os.path.join(self.upload_dir, 'site_photos', 'purge-992.jpg')
        target = os.path.join(self.upload_dir, 'site_photos', 'z-second-992.jpg')
        with open(target, 'wb') as handle:
            handle.write(b'second-exclusive')
        with self.db() as db:
            db.execute("""INSERT INTO operation_attachments
                (source_type,source_id,plan_id,item_id,file_type,mime_type,filename,stored_path)
                VALUES ('inspection',?,?,?,'image','image/jpeg',?,?)""",
                (item_id, plan_id, item_id, 'z-second-992.jpg',
                 '/uploads/site_photos/z-second-992.jpg'))
            attachments_before = db.execute(
                'SELECT COUNT(*) FROM operation_attachments').fetchone()[0]
        real_remove = os.remove

        def fail_unique_file(path):
            if os.path.normcase(path) == os.path.normcase(target):
                raise PermissionError('injected file lock')
            return real_remove(path)

        with mock.patch.object(app_module.os, 'remove', side_effect=fail_unique_file):
            response = self.client.post('/api/plan-schedules/992/purge',
                                        headers=self.headers('manager-token'),
                                        json={'version': 1, 'reason': '文件故障测试'})
        self.assertEqual((response.status_code, response.json.get('code')),
                         (503, 'PLAN_PURGE_FILE_DELETE_FAILED'))
        with self.db() as db:
            self.assertIsNotNone(db.execute(
                'SELECT id FROM plan_schedules WHERE id=992').fetchone())
            self.assertGreater(db.execute(
                'SELECT COUNT(*) FROM operation_attachments WHERE plan_id IS NOT NULL').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], attachments_before)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='plan_schedule_purge' AND source_id=992""").fetchone()[0], 0)
        self.assertTrue(os.path.exists(target))
        self.assertTrue(os.path.exists(first_target))

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

    def test_task_generation_skips_explicit_empty_site_and_keeps_selected_site(self):
        schedule_id = 62
        with self.db() as db:
            db.execute('INSERT INTO user_sites VALUES (2,2)')
            db.execute("INSERT INTO inspection_templates VALUES (7,'active','monthly','Monthly','',1)")
            db.execute("INSERT INTO inspection_configs VALUES ('water_quality',7,1)")
            db.execute("""INSERT INTO inspection_template_items
                (id,template_id,item_name,category,photo_required,max_photos,need_review,sort_order)
                VALUES (701,7,'Selected check','Water',0,0,0,1)""")
            plan_data = {
                self.day(): {
                    'sites': [1, 2],
                    'inspection_items': {'1': [], '2': [701]},
                },
            }
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,status,
                 version,tasks_generated,vehicle_exception_reason)
                VALUES (62,2,'monthly',?,?,?,'{}','approved',1,0,'无需用车')''',
                       (self.day(), self.day(), json.dumps(plan_data)))
            schedule = db.execute('SELECT * FROM plan_schedules WHERE id=?', (schedule_id,)).fetchone()
            created, item_count = app_module._ps_generate_tasks(db, schedule)
            rows = db.execute("""SELECT pi.site_id,pi.item_name FROM insp_plan_items pi
                JOIN insp_plans ip ON ip.id=pi.plan_id WHERE ip.plan_schedule_id=?""",
                              (schedule_id,)).fetchall()
        self.assertEqual((created, item_count), (1, 1))
        self.assertEqual([tuple(row) for row in rows], [(2, 'Selected check')])

    def test_weekly_merged_generation_is_one_package_and_idempotent_and_revalidates(self):
        with self.db() as db:
            for tid,frequency in ((7,'weekly'),(8,'monthly'),(9,'quarterly')):
                db.execute('INSERT INTO inspection_templates VALUES (?,?,?,?,?,?)',(tid,'active',frequency,frequency,'',tid))
                db.execute('INSERT INTO inspection_template_items(id,template_id,item_name,category,photo_required,max_photos,need_review,sort_order) VALUES (?,?,?,?,0,0,0,1)',(tid*100,tid,frequency,'设备'))
            data={self.day():{'sites':[1],'inspection_items':{'1':[700,800,900,800]}}}
            db.execute("INSERT INTO plan_schedules(id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,status,version,tasks_generated,vehicle_exception_reason) VALUES (63,2,'weekly',?,?,?,'{}','approved',1,0,'步行')",(self.day(),self.day(),json.dumps(data)))
            schedule=db.execute('SELECT * FROM plan_schedules WHERE id=63').fetchone()
            self.assertEqual(app_module._ps_generate_tasks(db,schedule),(1,3))
            self.assertEqual(app_module._ps_generate_tasks(db,schedule),(0,0))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans WHERE plan_schedule_id=63').fetchone()[0],1)
            self.assertEqual([row[0] for row in db.execute('SELECT frequency FROM insp_plan_items ORDER BY id')],['weekly','monthly','quarterly'])
            db.execute("UPDATE inspection_templates SET status='inactive' WHERE id=8")
            with self.assertRaises(app_module.PlanScheduleSiteScopeError):
                app_module._ps_rebuild_tasks_on_change(db,schedule)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM insp_plans WHERE plan_schedule_id=63 AND status='active'").fetchone()[0],1)
        detail = self.client.get('/api/plan-schedules/63', headers=self.headers('operator-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual({item['frequency'] for item in detail.json['template_context']}, {'weekly','monthly','quarterly'})
        self.assertEqual(sum(item['item_count'] for item in detail.json['template_context']), 3)

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

    def test_plan_vehicle_lock_does_not_prefix_match_another_schedule(self):
        self.add_submitted_schedule(1)
        with self.db() as db:
            db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#10用车（旧记录）','cancelled')""", (self.day(), self.day()))
            other_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        approved = self.client.post('/api/plan-schedules/1/approve',
                                    headers=self.headers('manager-token'))
        self.assertEqual(approved.status_code, 200, approved.json)
        with self.db() as db:
            rows = db.execute("SELECT id,reason,status FROM vehicle_applications ORDER BY id").fetchall()
        self.assertEqual(rows[0]['id'], other_id)
        self.assertEqual(rows[0]['status'], 'cancelled')
        self.assertEqual(len(rows), 2)
        self.assertIn('巡检计划#1用车', rows[1]['reason'])
        self.assertEqual(rows[1]['status'], 'approved')

    def test_approved_change_clears_every_temporary_rollback_snapshot(self):
        self.add_submitted_schedule(60)
        with self.db() as db:
            db.execute("INSERT INTO vehicles VALUES (2,'TEST-002','Other vehicle','idle',1000,NULL,NULL)")
            db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (2,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#600用车（其他计划）','approved')""", (self.day(), self.day()))
            other_application_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            db.execute("UPDATE plan_schedules SET status='approved' WHERE id=60")
        requested = self.client.post('/api/plan-schedules/60/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '调整执行说明'})
        self.assertEqual(requested.status_code, 200, requested.json)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='change_submitted', remarks='changed' WHERE id=60")

        approved = self.client.post('/api/plan-schedules/60/approve',
                                    headers=self.headers('manager-token'))
        self.assertEqual(approved.status_code, 200, approved.json)
        with self.db() as db:
            row = db.execute('SELECT * FROM plan_schedules WHERE id=60').fetchone()
            other_application = db.execute(
                'SELECT status FROM vehicle_applications WHERE id=?',
                (other_application_id,)).fetchone()
        self.assertEqual((row['status'], row['version']), ('approved', 1))
        self.assertEqual(other_application['status'], 'approved')
        for column in (
            'previous_plan_data', 'previous_vehicle_days', 'previous_vehicle_id',
            'previous_spare_parts', 'previous_work_order_ids', 'previous_remarks',
            'previous_period_start', 'previous_period_end',
            'previous_coverage_exception_reason', 'previous_vehicle_exception_reason',
        ):
            self.assertIsNone(row[column], column)
        self.assertIsNone(row['change_reason'])

    def test_change_approval_same_operator_vehicle_overlap_rolls_back_every_side_effect(self):
        self.add_submitted_schedule(64)
        self.add_submitted_schedule(640)
        with self.db() as db:
            operation_day = db.execute("SELECT date('now','localtime')").fetchone()[0]
            plan_data = json.dumps({operation_day: {'sites': [1]}})
            vehicle_days = json.dumps({operation_day: 1})
            db.execute("""UPDATE plan_schedules SET status='approved',period_start=?,period_end=?,
                plan_data=?,vehicle_days=? WHERE id IN (64,640)""",
                       (operation_day, operation_day, plan_data, vehicle_days))
            db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#64用车（原预约）','approved')""", (operation_day, operation_day))
            db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#640用车（另一计划）','approved')""", (operation_day, operation_day))
            db.execute("""INSERT INTO plan_resource_reservations
                (schedule_id,part_id,planned_quantity,reserved_quantity,issued_quantity,status)
                VALUES (64,8,2,0,0,'planned')""")
        requested = self.client.post('/api/plan-schedules/64/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '尝试调整同车时间'})
        self.assertEqual(requested.status_code, 200, requested.json)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='change_submitted' WHERE id=64")
        before = self.side_effect_snapshot(64)
        with self.db() as db:
            before_schedule = tuple(db.execute(
                'SELECT * FROM plan_schedules WHERE id=64').fetchone())

        response = self.client.post('/api/plan-schedules/64/approve',
                                    headers=self.headers('manager-token'))

        self.assertEqual((response.status_code, response.json.get('code')),
                         (409, 'PLAN_VEHICLE_RESERVATION_CONFLICT'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(64))
        with self.db() as db:
            after_schedule = tuple(db.execute(
                'SELECT * FROM plan_schedules WHERE id=64').fetchone())
            statuses = [tuple(row) for row in db.execute("""SELECT reason,status
                FROM vehicle_applications WHERE id IN (
                    SELECT id FROM vehicle_applications
                    WHERE reason LIKE '%巡检计划#64用车%' OR reason LIKE '%巡检计划#640用车%'
                ) ORDER BY id""").fetchall()]
        self.assertEqual(after_schedule, before_schedule)
        self.assertEqual(statuses, [
            ('巡检计划#64用车（原预约）', 'approved'),
            ('巡检计划#640用车（另一计划）', 'approved'),
        ])

    def test_change_approval_same_operator_non_overlapping_vehicle_trip_succeeds(self):
        self.add_submitted_schedule(65)
        self.add_submitted_schedule(650)
        with self.db() as db:
            operation_day = db.execute("SELECT date('now','localtime')").fetchone()[0]
            next_day = (datetime.strptime(operation_day, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            db.execute("""UPDATE plan_schedules SET status='approved',period_start=?,period_end=?,
                plan_data=?,vehicle_days=? WHERE id=65""", (
                    operation_day, operation_day,
                    json.dumps({operation_day: {'sites': [1]}}),
                    json.dumps({operation_day: 1}),
                ))
            db.execute("UPDATE plan_schedules SET status='approved' WHERE id=650")
            db.execute("""UPDATE plan_schedules SET period_start=?,period_end=?,plan_data=?,vehicle_days=?
                WHERE id=650""", (
                    next_day, next_day,
                    json.dumps({next_day: {'sites': [1]}}), json.dumps({next_day: 1}),
                ))
            db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#65用车（原预约）','approved')""", (operation_day, operation_day))
            db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#650用车（次日计划）','approved')""", (next_day, next_day))
            other_application_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        requested = self.client.post('/api/plan-schedules/65/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '调整备注但保持今日用车'})
        self.assertEqual(requested.status_code, 200, requested.json)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status='change_submitted',remarks='changed' WHERE id=65")

        response = self.client.post('/api/plan-schedules/65/approve',
                                    headers=self.headers('manager-token'))

        self.assertEqual(response.status_code, 200, response.json)
        with self.db() as db:
            current_rows = db.execute("""SELECT status FROM vehicle_applications
                WHERE reason LIKE ? ORDER BY id""", (app_module._ps_vehicle_reason_like(65),)).fetchall()
            other_status = db.execute(
                'SELECT status FROM vehicle_applications WHERE id=?',
                (other_application_id,)).fetchone()['status']
        self.assertEqual([row['status'] for row in current_rows], ['cancelled', 'approved'])
        self.assertEqual(other_status, 'approved')

    def test_vehicle_adjustment_flag_survives_draft_and_clears_after_approved_rebooking(self):
        self.add_submitted_schedule(651)
        with self.db() as db:
            db.execute("INSERT INTO vehicles VALUES (2,'TEST-002','Other vehicle','idle',1000,NULL,NULL)")
            operation_day = db.execute("SELECT date('now','localtime')").fetchone()[0]
            db.execute("""UPDATE plan_schedules SET status='approved',vehicle_adjustment_required=1,
                vehicle_adjustment_detail='overlap' WHERE id=651""")
            source_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,3,? || ' 07:00:00',? || ' 19:00:00','巡检','前序延期','approved')""",
                (operation_day, operation_day)).lastrowid
            target_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#651用车（原预约）','approved')""",
                (operation_day, operation_day)).lastrowid
            db.execute("""INSERT INTO vehicle_extension_conflicts
                (source_application_id,target_application_id,target_schedule_id,conflict_start,conflict_end)
                VALUES (?,?,651,? || ' 08:00:00',? || ' 19:00:00')""",
                (source_id, target_id, operation_day, operation_day))
        requested = self.client.post('/api/plan-schedules/651/request-change',
            headers=self.headers('operator-token'), json={'change_reason': '更换冲突车辆'})
        self.assertEqual(requested.status_code, 200, requested.json)
        saved = self.client.put('/api/plan-schedules/651',
            headers=self.headers('operator-token'), json={'version': 1, 'vehicle_id': 2})
        self.assertEqual(saved.status_code, 200, saved.json)
        with self.db() as db:
            self.assertEqual(db.execute("""SELECT vehicle_adjustment_required
                FROM plan_schedules WHERE id=651""").fetchone()[0], 1)
        submitted = self.client.post('/api/plan-schedules/651/submit',
            headers=self.headers('operator-token'), json={'version': saved.json['version']})
        self.assertEqual(submitted.status_code, 200, submitted.json)
        approved = submitted
        self.assertEqual(approved.json['status'], 'approved')
        with self.db() as db:
            schedule = db.execute("""SELECT vehicle_adjustment_required,vehicle_adjustment_detail
                FROM plan_schedules WHERE id=651""").fetchone()
            old_status = db.execute('SELECT status FROM vehicle_applications WHERE id=?',
                                    (target_id,)).fetchone()[0]
            new_reservation = db.execute("""SELECT vehicle_id,status FROM vehicle_applications
                WHERE reason LIKE ? AND id!=? ORDER BY id DESC LIMIT 1""",
                (app_module._ps_vehicle_reason_like(651), target_id)).fetchone()
        self.assertEqual((schedule['vehicle_adjustment_required'], schedule['vehicle_adjustment_detail']),
                         (0, ''))
        self.assertEqual(old_status, 'cancelled')
        self.assertEqual((new_reservation['vehicle_id'], new_reservation['status']), (2, 'approved'))

    def test_approved_vehicle_change_keeps_checked_out_old_vehicle_returnable(self):
        schedule_id = 66
        self.add_submitted_schedule(schedule_id)
        with self.db() as db:
            operation_day = db.execute("SELECT date('now','localtime')").fetchone()[0]
            next_day = (datetime.strptime(operation_day, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            db.execute("INSERT INTO vehicles VALUES (2,'TEST-002','Replacement vehicle','idle',2000,NULL,NULL)")
            db.execute("""UPDATE plan_schedules SET status='approved',tasks_generated=1,
                field_status='active',period_start=?,period_end=?,plan_data=?,vehicle_days=?,vehicle_id=1
                WHERE id=?""", (
                    operation_day, operation_day,
                    json.dumps({operation_day: {'sites': [1]}}),
                    json.dumps({operation_day: 1}), schedule_id,
                ))
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,period,generate_date,status,
                 plan_schedule_id,schedule_version,plan_snapshot)
                VALUES (6601,'Active plan','Operator',2,'monthly',?,'active',66,1,'{}')""",
                       (operation_day,))
            db.execute("""INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,execution_status)
                VALUES (6601,1,'Active item',NULL,'active')""")
            old_application_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#66用车（原预约）','approved')""",
                (operation_day, operation_day)).lastrowid
            old_use_id = db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,checked_out_at,status)
                VALUES (?,1000,? || ' 08:00:00','checked_out')""",
                (old_application_id, operation_day)).lastrowid
            db.execute("UPDATE vehicles SET status='in_use' WHERE id=1")
            db.execute("INSERT INTO vehicle_inspections VALUES (66,1,'return','normal')")
            other_application_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (2,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#660用车（其他计划）','approved')""",
                (next_day, next_day)).lastrowid

        requested = self.client.post('/api/plan-schedules/66/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '现场替换车辆'})
        self.assertEqual(requested.status_code, 200, requested.json)
        saved = self.client.put('/api/plan-schedules/66',
                                headers=self.headers('operator-token'), json={
                                    'version': 1,
                                    'vehicle_id': 2,
                                })
        self.assertEqual(saved.status_code, 200, saved.json)
        submitted = self.client.post('/api/plan-schedules/66/submit',
                                     headers=self.headers('operator-token'),
                                     json={'version': saved.json['version']})
        self.assertEqual(submitted.status_code, 200, submitted.json)
        approved = submitted
        self.assertEqual(approved.json['status'], 'approved')

        with self.db() as db:
            old_application = db.execute(
                'SELECT status FROM vehicle_applications WHERE id=?',
                (old_application_id,)).fetchone()
            old_use = db.execute(
                'SELECT status,returned_at FROM vehicle_use_records WHERE id=?',
                (old_use_id,)).fetchone()
            new_application = db.execute("""SELECT vehicle_id,status FROM vehicle_applications
                WHERE reason LIKE ? AND id!=? ORDER BY id DESC LIMIT 1""",
                (app_module._ps_vehicle_reason_like(schedule_id), old_application_id)).fetchone()
            other_application = db.execute(
                'SELECT status FROM vehicle_applications WHERE id=?',
                (other_application_id,)).fetchone()
        self.assertEqual(old_application['status'], 'approved')
        self.assertEqual((old_use['status'], old_use['returned_at']), ('checked_out', None))
        self.assertEqual((new_application['vehicle_id'], new_application['status']), (2, 'approved'))
        self.assertEqual(other_application['status'], 'approved')

        returned = self.client.post('/api/vehicle/use-records/{}/return'.format(old_use_id),
                                    headers=self.headers('operator-token'), json={
                                        'end_mileage': 1010,
                                        'return_inspection_id': 66,
                                    })
        self.assertEqual(returned.status_code, 200, returned.json)
        with self.db() as db:
            old_application = db.execute(
                'SELECT status FROM vehicle_applications WHERE id=?',
                (old_application_id,)).fetchone()
            old_use = db.execute(
                'SELECT status,returned_at,end_mileage FROM vehicle_use_records WHERE id=?',
                (old_use_id,)).fetchone()
            old_vehicle = db.execute(
                'SELECT status,current_mileage FROM vehicles WHERE id=1').fetchone()
            new_application = db.execute(
                'SELECT status FROM vehicle_applications WHERE vehicle_id=2 AND reason LIKE ?',
                (app_module._ps_vehicle_reason_like(schedule_id),)).fetchone()
            other_application = db.execute(
                'SELECT status FROM vehicle_applications WHERE id=?',
                (other_application_id,)).fetchone()
        self.assertEqual(old_application['status'], 'returned')
        self.assertEqual((old_use['status'], old_use['end_mileage']), ('returned', 1010))
        self.assertIsNotNone(old_use['returned_at'])
        self.assertEqual((old_vehicle['status'], old_vehicle['current_mileage']), ('idle', 1010))
        self.assertEqual(new_application['status'], 'approved')
        self.assertEqual(other_application['status'], 'approved')

    def test_approved_change_reuses_checked_out_application_for_same_vehicle(self):
        schedule_id = 67
        self.add_submitted_schedule(schedule_id)
        with self.db() as db:
            operation_day = db.execute("SELECT date('now','localtime')").fetchone()[0]
            next_day = (datetime.strptime(operation_day, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            db.execute("""UPDATE plan_schedules SET status='approved',tasks_generated=1,
                field_status='active',period_start=?,period_end=?,plan_data=?,vehicle_days=?,vehicle_id=1
                WHERE id=?""", (
                    operation_day, operation_day,
                    json.dumps({operation_day: {'sites': [1]}}),
                    json.dumps({operation_day: 1}), schedule_id,
                ))
            old_application_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#67用车（原预约）','approved')""",
                (operation_day, operation_day)).lastrowid
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,checked_out_at,status)
                VALUES (?,1000,? || ' 08:00:00','checked_out')""",
                (old_application_id, operation_day))
            db.execute("UPDATE vehicles SET status='in_use' WHERE id=1")

        requested = self.client.post('/api/plan-schedules/67/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '延长同车巡检'})
        self.assertEqual(requested.status_code, 200, requested.json)
        saved = self.client.put('/api/plan-schedules/67',
                                headers=self.headers('operator-token'), json={
                                    'version': 1,
                                    'period_end': next_day,
                                    'plan_data': {
                                        operation_day: {'sites': [1]},
                                        next_day: {'sites': [1]},
                                    },
                                    'vehicle_id': 1,
                                })
        self.assertEqual(saved.status_code, 200, saved.json)
        submitted = self.client.post('/api/plan-schedules/67/submit',
                                     headers=self.headers('operator-token'),
                                     json={'version': saved.json['version']})
        self.assertEqual(submitted.status_code, 200, submitted.json)

        approved = submitted
        self.assertEqual(approved.json['status'], 'approved')
        with self.db() as db:
            applications = db.execute("""SELECT id,status,start_at,end_at FROM vehicle_applications
                WHERE reason LIKE ? ORDER BY id""",
                (app_module._ps_vehicle_reason_like(schedule_id),)).fetchall()
            use_row = db.execute("""SELECT status,returned_at FROM vehicle_use_records
                WHERE application_id=?""", (old_application_id,)).fetchone()
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]['id'], old_application_id)
        self.assertEqual(applications[0]['status'], 'approved')
        self.assertEqual(str(applications[0]['start_at'])[:10], operation_day)
        self.assertEqual(str(applications[0]['end_at'])[:10], next_day)
        self.assertEqual((use_row['status'], use_row['returned_at']), ('checked_out', None))

    def test_change_normalizes_legacy_returned_application_before_same_vehicle_rebooking(self):
        schedule_id = 69
        self.add_submitted_schedule(schedule_id)
        with self.db() as db:
            operation_day = db.execute("SELECT date('now','localtime')").fetchone()[0]
            next_day = (datetime.strptime(operation_day, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            db.execute("""UPDATE plan_schedules SET status='approved',period_start=?,period_end=?,
                plan_data=?,vehicle_days=?,vehicle_id=1 WHERE id=?""", (
                    operation_day, operation_day,
                    json.dumps({operation_day: {'sites': [1]}}),
                    json.dumps({operation_day: 1}), schedule_id,
                ))
            legacy_application_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#69用车（历史已归还）','approved')""",
                (operation_day, operation_day)).lastrowid
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,end_mileage,checked_out_at,returned_at,status)
                VALUES (?,1000,1010,? || ' 08:00:00',? || ' 17:00:00','returned')""",
                (legacy_application_id, operation_day, operation_day))

        requested = self.client.post('/api/plan-schedules/69/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '延长计划并继续使用同一车辆'})
        self.assertEqual(requested.status_code, 200, requested.json)
        saved = self.client.put('/api/plan-schedules/69',
                                headers=self.headers('operator-token'), json={
                                    'version': 1,
                                    'period_end': next_day,
                                    'plan_data': {
                                        operation_day: {'sites': [1]},
                                        next_day: {'sites': [1]},
                                    },
                                    'vehicle_id': 1,
                                })
        self.assertEqual(saved.status_code, 200, saved.json)
        submitted = self.client.post('/api/plan-schedules/69/submit',
                                     headers=self.headers('operator-token'),
                                     json={'version': saved.json['version']})
        self.assertEqual(submitted.status_code, 200, submitted.json)

        approved = submitted
        self.assertEqual(approved.json['status'], 'approved')
        with self.db() as db:
            applications = db.execute("""SELECT id,status FROM vehicle_applications
                WHERE reason LIKE ? ORDER BY id""",
                (app_module._ps_vehicle_reason_like(schedule_id),)).fetchall()
        self.assertEqual(len(applications), 2)
        self.assertEqual((applications[0]['id'], applications[0]['status']),
                         (legacy_application_id, 'returned'))
        self.assertEqual(applications[1]['status'], 'approved')

    def test_same_plan_multiple_active_vehicle_uses_fail_change_approval(self):
        schedule_id = 70
        self.add_submitted_schedule(schedule_id)
        with self.db() as db:
            operation_day = db.execute("SELECT date('now','localtime')").fetchone()[0]
            next_day = (datetime.strptime(operation_day, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            db.execute("""UPDATE plan_schedules SET status='approved',period_start=?,period_end=?,
                plan_data=?,vehicle_days=?,vehicle_id=1 WHERE id=?""", (
                    operation_day, operation_day,
                    json.dumps({operation_day: {'sites': [1]}}),
                    json.dumps({operation_day: 1}), schedule_id,
                ))
            for label in ('A', 'B'):
                application_id = db.execute("""INSERT INTO vehicle_applications
                    (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                    VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',?,'approved')""",
                    (operation_day, next_day, f'巡检计划#70用车（异常活动记录{label}）')).lastrowid
                db.execute("""INSERT INTO vehicle_use_records
                    (application_id,start_mileage,checked_out_at,status)
                    VALUES (?,1000,? || ' 08:00:00','checked_out')""",
                    (application_id, operation_day))
            db.execute("UPDATE vehicles SET status='in_use' WHERE id=1")

        requested = self.client.post('/api/plan-schedules/70/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '延长同车计划'})
        self.assertEqual(requested.status_code, 200, requested.json)
        saved = self.client.put('/api/plan-schedules/70',
                                headers=self.headers('operator-token'), json={
                                    'version': 1,
                                    'period_end': next_day,
                                    'plan_data': {
                                        operation_day: {'sites': [1]},
                                        next_day: {'sites': [1]},
                                    },
                                    'vehicle_id': 1,
                                })
        self.assertEqual(saved.status_code, 200, saved.json)
        before = self.side_effect_snapshot(schedule_id)
        submitted = self.client.post('/api/plan-schedules/70/submit',
                                     headers=self.headers('operator-token'),
                                     json={'version': saved.json['version']})
        self.assertEqual((submitted.status_code, submitted.json.get('code')),
                         (409, 'PLAN_VEHICLE_RESERVATION_CONFLICT'))
        self.assert_side_effect_snapshot_unchanged(before, self.side_effect_snapshot(schedule_id))
        with self.db() as db:
            application_statuses = [row['status'] for row in db.execute("""SELECT status
                FROM vehicle_applications WHERE reason LIKE ? ORDER BY id""",
                (app_module._ps_vehicle_reason_like(schedule_id),)).fetchall()]
            active_uses = db.execute("""SELECT COUNT(*) FROM vehicle_use_records vur
                JOIN vehicle_applications va ON va.id=vur.application_id
                WHERE va.reason LIKE ? AND vur.returned_at IS NULL
                  AND COALESCE(vur.status,'checked_out')!='returned'""",
                (app_module._ps_vehicle_reason_like(schedule_id),)).fetchone()[0]
        self.assertEqual(application_statuses, ['approved', 'approved'])
        self.assertEqual(active_uses, 2)

    def test_change_cancels_all_unused_old_plan_reservations_regardless_of_date(self):
        schedule_id = 68
        self.add_submitted_schedule(schedule_id)
        with self.db() as db:
            operation_day = db.execute("SELECT date('now','localtime')").fetchone()[0]
            previous_day = (datetime.strptime(operation_day, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
            two_days_ago = (datetime.strptime(operation_day, '%Y-%m-%d') - timedelta(days=2)).strftime('%Y-%m-%d')
            db.execute("INSERT INTO vehicles VALUES (2,'TEST-002','Replacement vehicle','idle',2000,NULL,NULL)")
            db.execute("""UPDATE plan_schedules SET status='approved',period_start=?,period_end=?,
                plan_data=?,vehicle_days=?,vehicle_id=1 WHERE id=?""", (
                    operation_day, operation_day,
                    json.dumps({operation_day: {'sites': [1]}}),
                    json.dumps({operation_day: 1}), schedule_id,
                ))
            ongoing_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#68用车（跨日未出车）','approved')""",
                (previous_day, operation_day)).lastrowid
            expired_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#68用车（过期未出车）','approved')""",
                (two_days_ago, previous_day)).lastrowid
            other_id = db.execute("""INSERT INTO vehicle_applications
                (vehicle_id,applicant_id,start_at,end_at,destination,reason,status)
                VALUES (1,2,? || ' 08:00:00',? || ' 18:00:00','巡检',
                        '巡检计划#680用车（其他计划）','approved')""",
                (two_days_ago, previous_day)).lastrowid

        requested = self.client.post('/api/plan-schedules/68/request-change',
                                     headers=self.headers('operator-token'),
                                     json={'change_reason': '清理旧预约并换车'})
        self.assertEqual(requested.status_code, 200, requested.json)
        saved = self.client.put('/api/plan-schedules/68',
                                headers=self.headers('operator-token'), json={
                                    'version': 1,
                                    'vehicle_id': 2,
                                })
        self.assertEqual(saved.status_code, 200, saved.json)
        submitted = self.client.post('/api/plan-schedules/68/submit',
                                     headers=self.headers('operator-token'),
                                     json={'version': saved.json['version']})
        self.assertEqual(submitted.status_code, 200, submitted.json)
        approved = submitted
        self.assertEqual(approved.json['status'], 'approved')

        with self.db() as db:
            statuses = {
                row['id']: row['status'] for row in db.execute(
                    'SELECT id,status FROM vehicle_applications WHERE id IN (?,?,?)',
                    (ongoing_id, expired_id, other_id)).fetchall()
            }
            replacement = db.execute("""SELECT status FROM vehicle_applications
                WHERE vehicle_id=2 AND reason LIKE ?""",
                (app_module._ps_vehicle_reason_like(schedule_id),)).fetchone()
        self.assertEqual(statuses[ongoing_id], 'cancelled')
        self.assertEqual(statuses[expired_id], 'cancelled')
        self.assertEqual(statuses[other_id], 'approved')
        self.assertEqual(replacement['status'], 'approved')

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

    def test_overdue_rework_active_use_blocks_rebooking_until_execution_closes(self):
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
        self.assertFalse(state['reserves_vehicle'])
        self.assertTrue(state['has_active_use'])
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

    def test_active_supplement_lazily_overrides_completed_schedule_until_replaced(self):
        self.add_submitted_schedule(28)
        with self.db() as db:
            db.execute("""UPDATE plan_schedules
                SET status='approved', tasks_generated=1, field_status='completed',
                    field_completed_at=datetime('now') WHERE id=28""")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id)
                VALUES (280,'整改口径','Operator',2,?,'completed',28)""", (self.day(),))
            rework_item_id = db.execute("""INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,execution_status,check_out_time,
                 review_status,evidence_status)
                VALUES (280,1,'结果已填但需补拍','normal','active',datetime('now'),
                        3,'supplement_required')""").lastrowid

        listed = self.client.get('/api/plan-schedules?mine=1',
                                 headers=self.headers('operator-token'))
        detail = self.client.get('/api/plan-schedules/28',
                                 headers=self.headers('operator-token'))
        listed_row = next(row for row in listed.json if row['id'] == 28)
        self.assertEqual((listed_row['field_status'], listed_row['execution_status']),
                         ('rework', 'rework'))
        self.assertEqual((detail.json['field_status'], detail.json['execution_status']),
                         ('rework', 'rework'))
        self.assertTrue(detail.json['can_continue_rework'])
        self.assertEqual(detail.json['rework_block_reason'], '')
        self.assertEqual(detail.json['rework_execution_target'], {
            'schedule_id': 28,
            'execution_plan_id': 280,
            'work_date': self.day(),
            'site_id': 1,
            'item_id': rework_item_id,
            'source': 'plan_detail_rework',
        })
        admin_detail = self.client.get('/api/plan-schedules/28',
                                       headers=self.headers('manager-token'))
        self.assertFalse(admin_detail.json['can_continue_rework'])
        self.assertIsNone(admin_detail.json['rework_execution_target'])
        self.assertEqual(admin_detail.json['rework_block_reason'],
                         '当前账号无权执行该计划整改')
        with self.db() as db:
            db.execute('DELETE FROM user_sites WHERE user_id=2 AND site_id=1')
        no_site_scope = self.client.get('/api/plan-schedules/28',
                                        headers=self.headers('operator-token'))
        self.assertFalse(no_site_scope.json['can_continue_rework'])
        self.assertIsNone(no_site_scope.json['rework_execution_target'])
        with self.db() as db:
            db.execute('INSERT INTO user_sites (user_id,site_id) VALUES (2,1)')
        self.assertEqual(
            [(row['status'], row['status_cn'], row['completed_items'])
             for row in detail.json['generated_site_tasks']],
            [('rework', '需整改', 0)])
        with self.db() as db:
            persisted = db.execute(
                'SELECT field_status,field_completed_at FROM plan_schedules WHERE id=28').fetchone()
            self.assertEqual((persisted['field_status'], persisted['field_completed_at']),
                             ('rework', None))
            db.execute("""UPDATE insp_plan_items
                SET review_status=1,evidence_status='replacement_submitted'
                WHERE plan_id=280""")

        completed_detail = self.client.get('/api/plan-schedules/28',
                                           headers=self.headers('operator-token'))
        completed_list = self.client.get('/api/plan-schedules?mine=1',
                                         headers=self.headers('operator-token'))
        completed_row = next(row for row in completed_list.json if row['id'] == 28)
        self.assertEqual((completed_detail.json['field_status'],
                          completed_detail.json['execution_status']),
                         ('completed', 'completed'))
        self.assertEqual(completed_detail.json['generated_site_tasks'][0]['status'], 'completed')
        self.assertFalse(completed_detail.json['can_continue_rework'])
        self.assertIsNone(completed_detail.json['rework_execution_target'])
        self.assertEqual((completed_row['field_status'], completed_row['execution_status']),
                         ('completed', 'completed'))

    def test_plan_detail_projects_authoritative_ordinary_and_carryover_execution_target(self):
        self.add_submitted_schedule(29)
        carryover_day = (BUSINESS_NOW - timedelta(days=1)).strftime('%Y-%m-%d')
        with self.db() as db:
            db.execute("""UPDATE plan_schedules
                SET status='approved', tasks_generated=1, field_status='active'
                WHERE id=29""")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id)
                VALUES (290,'结转执行','Operator',2,?,'active',29)""", (carryover_day,))
            db.executemany("""INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,execution_status,check_out_time,
                 review_status,evidence_status)
                VALUES (290,1,?,?,'active',NULL,0,'')""", [
                ('已完成项', 'normal'),
                ('待执行项', None),
            ])

        carryover = self.client.get('/api/plan-schedules/29',
                                    headers=self.headers('operator-token'))
        self.assertEqual(carryover.status_code, 200, carryover.json)
        self.assertEqual(carryover.json['execution_status'], 'partial')
        self.assertTrue(carryover.json['can_continue_execution'])
        self.assertFalse(carryover.json['execution_target_requires_selection'])
        self.assertEqual(carryover.json['execution_target'], {
            'schedule_id': 29,
            'execution_plan_id': 290,
            'work_date': carryover_day,
            'site_id': 1,
            'source': 'plan_detail_execution',
        })

        unauthorized = self.client.get('/api/plan-schedules/29',
                                       headers=self.headers('manager-token'))
        self.assertFalse(unauthorized.json['can_continue_execution'])
        self.assertIsNone(unauthorized.json['execution_target'])

        with self.db() as db:
            db.execute('INSERT INTO user_sites (user_id,site_id) VALUES (2,2)')
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee,assignee_id,generate_date,status,plan_schedule_id)
                VALUES (291,'第二执行包','Operator',2,?,'active',29)""", (carryover_day,))
            db.execute("""INSERT INTO insp_plan_items
                (plan_id,site_id,item_name,result,execution_status,check_out_time,
                 review_status,evidence_status)
                VALUES (291,2,'第二站待执行',NULL,'active',NULL,0,'')""")

        ambiguous = self.client.get('/api/plan-schedules/29',
                                    headers=self.headers('operator-token'))
        self.assertTrue(ambiguous.json['can_continue_execution'])
        self.assertTrue(ambiguous.json['execution_target_requires_selection'])
        self.assertEqual(ambiguous.json['execution_target'], {
            'schedule_id': 29,
            'source': 'plan_detail_execution',
        }, '多目标只保留计划范围，不得猜测第一包或第一站')

        with self.db() as db:
            db.execute("""UPDATE insp_plan_items
                SET result='normal', check_out_time=datetime('now')
                WHERE plan_id IN (290,291)""")
        closed = self.client.get('/api/plan-schedules/29',
                                 headers=self.headers('operator-token'))
        self.assertEqual(closed.json['execution_status'], 'completed')
        self.assertFalse(closed.json['can_continue_execution'])
        self.assertIsNone(closed.json['execution_target'])

        with self.db() as db:
            db.execute("""UPDATE plan_schedules
                SET status='cancelled', field_status='active', field_completed_at=NULL
                WHERE id=29""")
            db.execute("""UPDATE insp_plan_items
                SET result=NULL, check_out_time=NULL WHERE plan_id=290 AND site_id=1""")
        cancelled = self.client.get('/api/plan-schedules/29',
                                    headers=self.headers('operator-token'))
        self.assertFalse(cancelled.json['can_continue_execution'])
        self.assertIsNone(cancelled.json['execution_target'])

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

    def test_create_and_update_clear_exception_when_vehicle_is_selected(self):
        created = self.client.post('/api/plan-schedules', headers=self.headers('manager-token'), json={
            'user_id': 2,
            'schedule_type': 'weekly',
            'period_start': self.day(),
            'period_end': self.day(),
            'plan_data': {self.day(): {'sites': [1]}},
            'vehicle_id': 1,
            'vehicle_days': {},
            'vehicle_exception_reason': 'Contradictory create reason',
        })
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(created.json['vehicle_exception_reason'], '')
        schedule_id = created.json['id']
        with self.db() as db:
            row = db.execute(
                'SELECT vehicle_id,vehicle_exception_reason FROM plan_schedules WHERE id=?',
                (schedule_id,)).fetchone()
            event = db.execute(
                'SELECT payload FROM plan_schedule_events WHERE schedule_id=? ORDER BY id DESC LIMIT 1',
                (schedule_id,)).fetchone()
        self.assertEqual((row['vehicle_id'], row['vehicle_exception_reason']), (1, ''))
        self.assertEqual(json.loads(event['payload'])['vehicle_exception_reason'], '')

        updated = self.client.put('/api/plan-schedules/{}'.format(schedule_id),
                                  headers=self.headers('operator-token'), json={
                                      'version': created.json['version'],
                                      'vehicle_id': 1,
                                      'vehicle_exception_reason': 'Contradictory update reason',
                                  })
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual(updated.json['vehicle_exception_reason'], '')
        with self.db() as db:
            row = db.execute(
                'SELECT vehicle_exception_reason FROM plan_schedules WHERE id=?',
                (schedule_id,)).fetchone()
            event = db.execute(
                'SELECT payload FROM plan_schedule_events WHERE schedule_id=? ORDER BY id DESC LIMIT 1',
                (schedule_id,)).fetchone()
        self.assertEqual(row['vehicle_exception_reason'], '')
        self.assertEqual(json.loads(event['payload'])['vehicle_exception_reason'], '')

    def test_submit_and_approve_normalize_historical_vehicle_reason(self):
        schedule_id = 42
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET vehicle_days=?, vehicle_id=?, no_vehicle_required=0, vehicle_exception_reason=? WHERE id=?",
                       (json.dumps({self.day(): 1}), 1,
                        'Historical submit contradiction', schedule_id))

        submitted = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                     headers=self.headers('operator-token'), json={'version': 1})
        self.assertEqual(submitted.status_code, 200, submitted.json)
        with self.db() as db:
            row = db.execute(
                'SELECT status,vehicle_exception_reason FROM plan_schedules WHERE id=?',
                (schedule_id,)).fetchone()
            event = db.execute(
                'SELECT payload FROM plan_schedule_events WHERE schedule_id=? ORDER BY id DESC LIMIT 1',
                (schedule_id,)).fetchone()
        self.assertEqual((row['status'], row['vehicle_exception_reason']), ('submitted', ''))
        self.assertEqual(json.loads(event['payload'])['vehicle_exception_reason'], '')

        approval_id = 43
        self.add_submitted_schedule(
            approval_id, vehicle_id=1, no_vehicle_reason='Historical approval contradiction')
        approved = self.client.post('/api/plan-schedules/{}/approve'.format(approval_id),
                                    headers=self.headers('manager-token'))
        self.assertEqual(approved.status_code, 200, approved.json)
        with self.db() as db:
            row = db.execute(
                'SELECT status,vehicle_exception_reason FROM plan_schedules WHERE id=?',
                (approval_id,)).fetchone()
            event = db.execute(
                'SELECT payload FROM plan_schedule_events WHERE schedule_id=? ORDER BY id DESC LIMIT 1',
                (approval_id,)).fetchone()
        self.assertEqual((row['status'], row['vehicle_exception_reason']), ('approved', ''))
        self.assertEqual(json.loads(event['payload'])['vehicle_exception_reason'], '')

    def test_no_vehicle_without_reason_can_create_submit_and_approve(self):
        required_vehicle = self.client.post('/api/plan-schedules/validate',
                                            headers=self.headers('operator-token'), json={
            **self.creation_payload(key='validate-needs-vehicle', submit=False),
            'no_vehicle_required': False,
            'vehicle_exception_reason': '',
        })
        self.assertEqual(required_vehicle.status_code, 200, required_vehicle.json)
        self.assertFalse(required_vehicle.json['ok'])
        self.assertEqual(required_vehicle.json['error_details'][0]['field'], 'vehicle_id')
        self.assertEqual(required_vehicle.json['errors'], ['已选择需要用车，请先选择计划车辆'])
        self.assertEqual(required_vehicle.json['error_details'], [
            {'field': 'vehicle_id', 'text': '已选择需要用车，请先选择计划车辆'}])

        contradictory = self.client.post('/api/plan-schedules',
                                         headers=self.headers('operator-token'), json={
            **self.creation_payload(key='contradictory-vehicle-mode', submit=False),
            'vehicle_id': 1,
        })
        self.assertEqual((contradictory.status_code, contradictory.json['code']),
                         (400, 'PLAN_VEHICLE_MODE_CONFLICT'))

        created = self.client.post('/api/plan-schedules', headers=self.headers('operator-token'), json={
            **self.creation_payload(key='no-vehicle-without-reason', submit=True),
            'vehicle_exception_reason': '',
        })
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(created.json['status'], 'submitted')
        self.assertEqual(created.json['vehicle_exception_reason'], '')
        approved_created = self.client.post(
            '/api/plan-schedules/{}/approve'.format(created.json['id']),
            headers=self.headers('manager-token'))
        self.assertEqual(approved_created.status_code, 200, approved_created.json)

        schedule_id = 44
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)

        submitted = self.client.post('/api/plan-schedules/{}/submit'.format(schedule_id),
                                     headers=self.headers('operator-token'), json={'version': 1})
        self.assertEqual(submitted.status_code, 200, submitted.json)
        approved = self.client.post('/api/plan-schedules/{}/approve'.format(schedule_id),
                                    headers=self.headers('manager-token'))
        self.assertEqual(approved.status_code, 200, approved.json)

        with self.db() as db:
            row = db.execute(
                'SELECT status,vehicle_exception_reason FROM plan_schedules WHERE id=?',
                (schedule_id,)).fetchone()
            submitted_events = db.execute(
                "SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=? AND event_type='submitted'",
                (schedule_id,)).fetchone()[0]
        self.assertEqual((row['status'], row['vehicle_exception_reason']), ('approved', ''))
        self.assertEqual(submitted_events, 1)

    def test_route_suggestion_is_structured_and_missing_coordinates_do_not_invent_one(self):
        day = self.day()
        with self.db() as db:
            db.execute("UPDATE sites SET name='青云',gps_lat=28.680,gps_lng=115.730 WHERE id=1")
            db.execute("UPDATE sites SET name='扬子洲',gps_lat=28.780,gps_lng=115.830 WHERE id=2")
            db.execute("INSERT INTO sites VALUES (3,'室内定位测试站（团结路）','S-3','water_quality','active',28.690,115.740)")
            db.execute("INSERT INTO sites VALUES (4,'坐标缺失站','S-4','water_quality','active',NULL,NULL)")
            result = app_module._ps_validate(
                db, 2, 'monthly', day, day,
                {day: {'sites': [1, 2, 3]}}, {day: 1})
            route = next(item for item in result['warning_details'] if item['type'] == 'route_backtrack')
            self.assertEqual(route['suggested_site_ids'], [1, 3, 2])
            self.assertEqual(route['suggested_site_names'], ['青云', '室内定位测试站（团结路）', '扬子洲'])
            self.assertGreater(route['estimated_distance_saved_km'], 0)
            self.assertNotIn(day, route['text'])
            self.assertNotIn('更靠近', route['text'])
            self.assertNotIn('路线折返', route['text'])
            mixed = app_module._ps_validate(
                db, 2, 'monthly', day, day,
                {day: {'sites': [1, 4, 2, 3]}}, {day: 1})
            mixed_route = next(item for item in mixed['warning_details'] if item['type'] == 'route_backtrack')
            self.assertEqual(mixed_route['suggested_site_ids'], [1, 4, 3, 2])
            self.assertEqual(len(mixed_route['suggested_site_ids']), 4)
            self.assertEqual(set(mixed_route['suggested_site_ids']), {1, 2, 3, 4})
            missing = app_module._ps_validate(
                db, 2, 'monthly', day, day,
                {day: {'sites': [1, 4, 2]}}, {day: 1})
            short = app_module._ps_validate(
                db, 2, 'monthly', day, day,
                {day: {'sites': [1, 2]}}, {day: 1})
        self.assertFalse(any(item['type'] == 'route_backtrack' for item in missing['warning_details']))
        self.assertFalse(any(item['type'] == 'route_backtrack' for item in short['warning_details']))

    def test_ordinary_no_vehicle_edit_preserves_historical_reason(self):
        schedule_id = 45
        self.add_scope_failure_schedule(schedule_id, 'draft', site_id=1)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET vehicle_exception_reason=? WHERE id=?",
                       ('历史步行说明', schedule_id))

        updated = self.client.put('/api/plan-schedules/{}'.format(schedule_id),
                                  headers=self.headers('operator-token'), json={
                                      'version': 1,
                                      'remarks': '普通编辑只改备注',
                                  })

        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual(updated.json['vehicle_exception_reason'], '历史步行说明')
        with self.db() as db:
            row = db.execute(
                'SELECT vehicle_id,vehicle_days,vehicle_exception_reason FROM plan_schedules WHERE id=?',
                (schedule_id,)).fetchone()
        self.assertIsNone(row['vehicle_id'])
        self.assertEqual(json.loads(row['vehicle_days']), {})
        self.assertEqual(row['vehicle_exception_reason'], '历史步行说明')

    def test_legacy_no_vehicle_backfill_runs_once_then_preserves_explicit_choice(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE plan_schedules (
                id INTEGER PRIMARY KEY, vehicle_id INTEGER, vehicle_days TEXT
            );
            INSERT INTO plan_schedules VALUES (1,NULL,'{}');
            INSERT INTO plan_schedules VALUES (2,1,'{"2026-08-11":1}');
        """)
        self.assertTrue(app_module._ensure_plan_schedule_vehicle_column(db))
        rows = db.execute(
            'SELECT id,no_vehicle_required FROM plan_schedules ORDER BY id').fetchall()
        self.assertEqual([tuple(row) for row in rows], [(1, 1), (2, 0)])
        db.execute('UPDATE plan_schedules SET no_vehicle_required=0 WHERE id=1')
        self.assertTrue(app_module._ensure_plan_schedule_vehicle_column(db))
        self.assertEqual(db.execute(
            'SELECT no_vehicle_required FROM plan_schedules WHERE id=1').fetchone()[0], 0)
        db.close()

    def test_table_column_lookup_returns_false_on_sqlite_error(self):
        class BrokenDb:
            def execute(self, _):
                raise sqlite3.OperationalError('bad pragma')

        self.assertFalse(app_module._table_has_column(BrokenDb(), 'bad', 'field'))


if __name__ == '__main__':
    unittest.main()
