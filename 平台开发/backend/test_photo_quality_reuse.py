import sqlite3
import unittest
from photo_quality import R30ContractError, ensure_schema, record_photo_quality, current_photo_quality

class PhotoQualityReuseTest(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE sites (
                id INTEGER PRIMARY KEY, name TEXT, type TEXT
            );
            CREATE TABLE inspection_template_items (
                id INTEGER PRIMARY KEY, item_name TEXT, category TEXT
            );
            CREATE TABLE inspection_schedules (
                id INTEGER PRIMARY KEY, site_id INTEGER, template_id INTEGER,
                template_item_id INTEGER, frequency TEXT, next_due_date TEXT,
                last_completed_at TEXT, cycle_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE plan_schedules (
                id INTEGER PRIMARY KEY, user_id INTEGER, schedule_type TEXT,
                status TEXT, plan_data TEXT, version INTEGER DEFAULT 1,
                tasks_generated INTEGER DEFAULT 0, period_start TEXT, period_end TEXT
            );
            CREATE TABLE insp_plans (
                id INTEGER PRIMARY KEY, plan_schedule_id INTEGER, generate_date TEXT,
                status TEXT, assignee_id INTEGER, rework_of_plan_id INTEGER
            );
            CREATE TABLE insp_plan_items (
                id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                template_item_id INTEGER, item_name TEXT, result TEXT,
                execution_status TEXT DEFAULT 'active', completed_at TEXT,
                check_time TEXT, check_in_time TEXT, check_out_time TEXT,
                review_status INTEGER DEFAULT 0, review_comment TEXT DEFAULT '',
                evidence_status TEXT DEFAULT '', supplement_required_at TEXT DEFAULT '',
                supplement_reason TEXT DEFAULT '', rework_required_at TEXT DEFAULT ''
            );
            CREATE TABLE operation_attachments (
                id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER,
                item_id INTEGER, plan_id INTEGER, site_id INTEGER, uploader_id INTEGER,
                review_status TEXT DEFAULT 'pending', reject_reason TEXT DEFAULT '',
                reviewer_id INTEGER, reviewed_at TEXT, is_deleted INTEGER DEFAULT 0,
                evidence_qualification TEXT DEFAULT 'qualified', evidence_reason TEXT DEFAULT '',
                stored_path TEXT DEFAULT '', description TEXT DEFAULT ''
            );
            CREATE TABLE vehicle_applications (
                id INTEGER PRIMARY KEY, rework_plan_id INTEGER, status TEXT
            );
            CREATE TABLE parts_requests (
                id INTEGER PRIMARY KEY, plan_id INTEGER, status TEXT
            );
            CREATE TABLE work_orders (
                id INTEGER PRIMARY KEY, status TEXT
            );
            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER, is_read INTEGER
            );
        ''')
        self.db.executemany('INSERT INTO sites VALUES (?,?,?)', [
            (1, 'Alpha', 'water_quality'), (2, 'Beta', 'water_quality'),
        ])
        self.db.executemany('INSERT INTO inspection_template_items VALUES (?,?,?)', [
            (11, 'Meter', 'instrument'), (12, 'Cabinet', 'environment'),
        ])
        ensure_schema(self.db)

    def tearDown(self):
        self.db.close()

    def test_photo_rejudgment_keeps_field_fact_and_original_score_month(self):
        self.db.execute("INSERT INTO insp_plans VALUES (301,NULL,'2026-09-30','completed',8,NULL)")
        self.db.execute('''INSERT INTO insp_plan_items
            (id,plan_id,site_id,template_item_id,item_name,result,execution_status,
             completed_at,check_out_time)
            VALUES (3001,301,1,11,'Meter','normal','active','2026-09-30 23:30:00',
                    '2026-09-30 23:40:00')''')
        self.db.execute('''INSERT INTO operation_attachments
            (id,source_type,source_id,item_id,plan_id,site_id,uploader_id,review_status,
             evidence_qualification,stored_path,description)
            VALUES (4001,'inspection',3001,3001,301,1,8,'pending','qualified','/a.jpg','Meter')''')

        rejected = record_photo_quality(
            self.db, 4001, 'reject', 'blurred', 1, '2026-10-02 09:00:00',
            idempotency_key='quality-1', expected_current_status='pending')
        replay = record_photo_quality(
            self.db, 4001, 'reject', 'blurred', 1, '2026-10-02 09:00:00',
            idempotency_key='quality-1', expected_current_status='pending')
        self.assertEqual(rejected['score_month'], '2026-09')
        self.assertTrue(rejected['changed'])
        self.assertTrue(replay['idempotent_replay'])
        with self.assertRaises(R30ContractError) as conflict:
            record_photo_quality(
                self.db, 4001, 'approve', '', 1, '2026-10-02 09:10:00',
                idempotency_key='quality-1')
        self.assertEqual(conflict.exception.code, 'PHOTO_QUALITY_IDEMPOTENCY_CONFLICT')

        approved = record_photo_quality(
            self.db, 4001, 'approve', '', 1, '2026-10-03 10:00:00',
            idempotency_key='quality-2', expected_current_status='rejected',
            correction_type='mistaken_rejection')
        self.assertEqual(approved['previous_storage_status'], 'rejected')
        item = self.db.execute(
            'SELECT result,completed_at,check_out_time FROM insp_plan_items WHERE id=3001').fetchone()
        self.assertEqual(tuple(item), ('normal', '2026-09-30 23:30:00', '2026-09-30 23:40:00'))
        photos = current_photo_quality(
            self.db, 8, '2026-09-01 00:00:00', '2026-09-30 23:59:59')
        self.assertEqual((photos[0]['score_month'], photos[0]['deduction']), ('2026-09', 0))
        self.assertEqual([entry['status'] for entry in photos[0]['history']],
                         ['qualified', 'unqualified'])

    def test_legacy_check_time_and_source_void_keep_original_quality_month(self):
        self.db.execute("INSERT INTO insp_plans VALUES (301,NULL,'2026-09-30','completed',8,NULL)")
        self.db.execute('''INSERT INTO insp_plan_items
            (id,plan_id,site_id,item_name,result,check_time,check_out_time)
            VALUES (3001,301,1,'Meter','normal','2026-09-30 23:30:00','2026-09-30 23:40:00')''')
        self.db.execute('''INSERT INTO operation_attachments
            (id,source_type,source_id,item_id,plan_id,site_id,uploader_id)
            VALUES (4001,'inspection',3001,3001,301,1,8)''')
        decision = record_photo_quality(self.db, 4001, 'reject', 'blurred', 1, '2026-10-02 09:00:00')
        self.assertEqual(decision['score_month'], '2026-09')
        self.db.execute("UPDATE operation_attachments SET review_status='voided',evidence_qualification='invalid' WHERE id=4001")
        photos = current_photo_quality(self.db, 8, '2026-09-01 00:00:00', '2026-09-30 23:59:59')
        self.assertEqual((len(photos), photos[0]['deduction'], photos[0]['score_month']), (1, 1, '2026-09'))
        self.assertEqual(current_photo_quality(self.db, 8, '2026-10-01 00:00:00', '2026-10-31 23:59:59'), [])
