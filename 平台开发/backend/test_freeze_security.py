import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class FreezeSecurityTest(unittest.TestCase):
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
        app_module._tokens.update({
            'admin': {'id': 1, 'role': 'admin', 'real_name': 'Admin'},
            'operator-1': {'id': 2, 'role': 'operator', 'real_name': 'Operator 1'},
            'operator-2': {'id': 3, 'role': 'operator', 'real_name': 'Operator 2'},
            'operator-none': {'id': 4, 'role': 'operator', 'real_name': 'Unassigned'},
            'reviewer-1': {'id': 5, 'role': 'reviewer', 'real_name': 'Reviewer 1'},
            'reviewer-2': {'id': 6, 'role': 'reviewer', 'real_name': 'Reviewer 2'},
        })
        app_module._site_ids_cache.clear()

        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, type TEXT,
                                    gps_lat REAL, gps_lng REAL);
                CREATE TABLE photo_requirements (
                    id INTEGER PRIMARY KEY, site_type TEXT, period TEXT,
                    item_name TEXT, photo_count INTEGER, review_required INTEGER,
                    seq INTEGER
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, filename TEXT, stored_path TEXT,
                    file_type TEXT, mime_type TEXT, file_size INTEGER,
                    description TEXT, source_type TEXT, source_id INTEGER,
                    site_id INTEGER, uploader_id INTEGER, uploader_name TEXT,
                    gps_lat REAL, gps_lng REAL, taken_at TEXT, category TEXT,
                    review_required INTEGER, requirement_id INTEGER,
                    is_deleted INTEGER DEFAULT 0, is_flagged INTEGER DEFAULT 0,
                    flag_reason TEXT DEFAULT '', flag_rule TEXT DEFAULT '',
                    review_status TEXT, reviewer_id INTEGER, reviewed_at TEXT,
                    reject_reason TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    evidence_qualification TEXT DEFAULT 'qualified',
                    evidence_reason TEXT DEFAULT '', evidence_next_action TEXT DEFAULT ''
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, site_id INTEGER,
                    title TEXT, description TEXT, event_type TEXT, level TEXT,
                    status TEXT, images TEXT, assignee TEXT, created_at TEXT
                );
                CREATE TABLE parts_requests (
                    id INTEGER PRIMARY KEY, work_order_no TEXT,
                    requested_part_name TEXT, created_at TEXT
                );
                CREATE TABLE parts_request_items (
                    request_id INTEGER, part_id INTEGER, part_sku TEXT, quantity INTEGER
                );
                CREATE TABLE spare_parts_inventory (id INTEGER PRIMARY KEY, part_name TEXT);
                CREATE TABLE spare_part_requests (
                    id INTEGER PRIMARY KEY, work_order_no TEXT, status TEXT, created_at TEXT
                );
                CREATE TABLE device_recycle (
                    id INTEGER PRIMARY KEY, work_order_no TEXT, created_at TEXT
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    source_type TEXT, source_id INTEGER, title TEXT, content TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_name TEXT, assignee TEXT,
                    assignee_id INTEGER, period TEXT, generate_date TEXT,
                    status TEXT, plan_schedule_id INTEGER, schedule_version INTEGER,
                    plan_snapshot TEXT, completion_rate REAL DEFAULT 0
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                    template_id INTEGER, item_name TEXT, category TEXT,
                    frequency TEXT, required_photos INTEGER DEFAULT 0,
                    actual_photos INTEGER DEFAULT 0, result TEXT,
                    execution_status TEXT DEFAULT 'active', review_status INTEGER,
                    review_comment TEXT, reviewer_id INTEGER, review_time TEXT,
                    photo_urls TEXT DEFAULT '[]', remark TEXT DEFAULT '',
                    completed_at TEXT, check_out_time TEXT
                );
                CREATE TABLE inspection_schedules (
                    id INTEGER PRIMARY KEY, site_id INTEGER, template_id INTEGER,
                    template_item_id INTEGER, status TEXT
                );
                CREATE TABLE inspection_template_items (
                    id INTEGER PRIMARY KEY, template_id INTEGER, item_name TEXT,
                    category TEXT
                );
            ''')
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [
                (2, 1), (3, 2), (5, 1), (6, 2),
            ])
            db.executemany('INSERT INTO sites VALUES (?,?,?,?,?)', [
                (1, 'Site 1', 'water_quality', 28.0, 115.0),
                (2, 'Site 2', 'water_quality', 28.1, 115.1),
            ])
            db.execute("INSERT INTO photo_requirements VALUES (100,'water_quality','weekly','Overview',1,0,1)")
            db.executemany('''
                INSERT INTO operation_attachments
                    (id,filename,stored_path,file_type,source_type,source_id,site_id,
                     uploader_id,description,requirement_id,is_deleted,review_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,0,?)
            ''', [
                (10, 'site1-a.jpg', '/uploads/site1-a.jpg', 'image', 'inspection', 100, 1, 2, 'Site 1 A', 100, 'pending'),
                (11, 'site1-b.jpg', '/uploads/site1-b.jpg', 'image', 'inspection', 100, 1, 2, 'Site 1 B', 100, 'pending'),
                (12, 'site2-a.jpg', '/uploads/site2-a.jpg', 'image', 'inspection', 101, 2, 3, 'Site 2 A', 100, 'pending'),
                (20, 'workorder.jpg', '/uploads/workorder.jpg', 'image', 'workorder', 1, 1, 2, 'Workorder photo', None, 'pending'),
            ])
            db.executemany('''
                INSERT INTO work_orders
                    (id,order_no,site_id,title,event_type,status,images,assignee,created_at)
                VALUES (?,?,?,?,?,?,?,?,datetime('now'))
            ''', [
                (1, 'WO-SITE-1', 1, 'Site 1 order', '', 'in_progress', '[]', 'Operator 1'),
                (2, 'WO-SITE-2', 2, 'Site 2 order', '', 'in_progress', '[]', 'Operator 2'),
            ])
            db.executemany('''
                INSERT INTO insp_plan_items
                    (id, plan_id, site_id, item_name, review_status, result)
                VALUES (?, 1, ?, ?, ?, ?)
            ''', [
                (100, 1, 'Site 1 pending', 1, 'normal'),
                (101, 2, 'Site 2 pending', 1, 'normal'),
                (102, 1, 'Site 1 approved', 2, 'normal'),
            ])
            db.execute("INSERT INTO insp_plans (id,plan_name,assignee_id,status) VALUES (1,'Review plan',2,'active')")
            db.execute('''
                INSERT INTO operation_attachments
                    (id,filename,stored_path,file_type,source_type,source_id,site_id,
                     uploader_id,description,requirement_id,is_deleted,review_status)
                VALUES (13,'deleted.jpg','/uploads/deleted.jpg','image','site_photo',0,1,
                        2,'Deleted photo',NULL,1,'pending')
            ''')
            db.execute('''
                INSERT INTO operation_attachments
                    (id,filename,stored_path,file_type,source_type,source_id,site_id,
                     uploader_id,description,requirement_id,is_deleted,review_status)
                VALUES (14,'processed.jpg','/uploads/processed.jpg','image','site_photo',0,1,
                        2,'Processed photo',NULL,0,'approved')
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def status(self, token, method, path, **kwargs):
        return getattr(self.client, method)(path, headers=self.headers(token), **kwargs)

    def attachment_statuses(self):
        with app_module.get_db() as db:
            return dict(db.execute(
                'SELECT id, review_status FROM operation_attachments ORDER BY id'
            ).fetchall())

    def test_unassigned_user_cannot_read_upload_or_review_site_photos(self):
        self.assertEqual(self.status('operator-none', 'get', '/api/inspection/photos/1').status_code, 403)
        self.assertEqual(self.status('operator-none', 'get', '/api/inspection/photos/1/check').status_code, 403)
        upload = self.status('operator-none', 'post', '/api/inspection/photos/upload', json={
            'site_id': 1, 'requirement_id': 100,
        })
        self.assertEqual(upload.status_code, 403)
        self.assertEqual(self.status('operator-none', 'post', '/api/inspection/photos/10/review', json={}).status_code, 403)
        self.assertEqual(self.status('operator-none', 'post', '/api/inspection/photos/batch-review', json={
            'photo_ids': [10],
        }).status_code, 403)

    def test_operator_and_reviewer_site_scope_and_admin_access(self):
        self.assertEqual(self.status('operator-1', 'get', '/api/inspection/photos/1').status_code, 200)
        self.assertEqual(self.status('operator-1', 'get', '/api/inspection/photos/2').status_code, 403)
        self.assertEqual(self.status('operator-1', 'post', '/api/inspection/photos/upload', json={
            'site_id': 2, 'requirement_id': 100,
        }).status_code, 403)
        self.assertEqual(self.status('reviewer-1', 'post', '/api/inspection/photos/upload', json={
            'site_id': 1, 'requirement_id': 100,
        }).status_code, 403)
        self.assertEqual(self.status('reviewer-1', 'post', '/api/inspection/photos/10/review', json={}).status_code, 200)
        self.assertEqual(self.status('reviewer-1', 'post', '/api/inspection/photos/12/review', json={}).status_code, 403)
        self.assertEqual(self.status('admin', 'get', '/api/inspection/photos/2').status_code, 200)
        self.assertEqual(self.status('admin', 'post', '/api/inspection/photos/12/review', json={}).status_code, 200)

    def test_batch_validation_is_atomic_for_missing_unauthorized_and_cross_site(self):
        before = self.attachment_statuses()
        missing = self.status('reviewer-1', 'post', '/api/inspection/photos/batch-review', json={
            'photo_ids': [10, 999],
        })
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.attachment_statuses(), before)

        unauthorized = self.status('reviewer-1', 'post', '/api/inspection/photos/batch-review', json={
            'photo_ids': [10, 12],
        })
        self.assertEqual(unauthorized.status_code, 403)
        self.assertEqual(self.attachment_statuses(), before)

        mixed_admin = self.status('admin', 'post', '/api/inspection/photos/batch-review', json={
            'photo_ids': [10, 12],
        })
        self.assertEqual(mixed_admin.status_code, 403)
        self.assertEqual(mixed_admin.json['code'], 'CROSS_SITE_BATCH')
        self.assertEqual(self.attachment_statuses(), before)

    def test_batch_same_site_review_and_noninspection_attachment_are_safe(self):
        response = self.status('reviewer-1', 'post', '/api/inspection/photos/batch-review', json={
            'photo_ids': [10, 11], 'action': 'approve',
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.attachment_statuses()[10], 'approved')
        self.assertEqual(self.attachment_statuses()[11], 'approved')

        noninspection = self.status('admin', 'post', '/api/inspection/photos/20/review', json={
            'action': 'reject', 'reject_reason': 'must remain workorder evidence',
        })
        self.assertEqual(noninspection.status_code, 404)
        self.assertEqual(self.attachment_statuses()[20], 'pending')

    def test_duplicate_batch_ids_update_and_notify_once(self):
        response = self.status('reviewer-1', 'post', '/api/inspection/photos/batch-review', json={
            'photo_ids': [10, 10], 'action': 'reject', 'reject_reason': 'image is unclear',
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['count'], 1)
        self.assertEqual(self.attachment_statuses()[10], 'rejected')
        with app_module.get_db() as db:
            notification_count = db.execute(
                "SELECT COUNT(*) FROM notifications WHERE source_type='photo_review' AND source_id=10"
            ).fetchone()[0]
        self.assertEqual(notification_count, 1)

    def test_invalid_action_and_empty_reject_reason_do_not_change_status(self):
        before = self.attachment_statuses()
        invalid_batch = self.status('reviewer-1', 'post', '/api/inspection/photos/batch-review', json={
            'photo_ids': [10], 'action': 'hold',
        })
        self.assertEqual(invalid_batch.status_code, 400)
        self.assertEqual(self.attachment_statuses(), before)

        invalid_single = self.status('reviewer-1', 'post', '/api/inspection/photos/10/review', json={
            'action': 'hold',
        })
        self.assertEqual(invalid_single.status_code, 400)
        self.assertEqual(self.attachment_statuses(), before)

        empty_batch = self.status('reviewer-1', 'post', '/api/inspection/photos/batch-review', json={
            'photo_ids': [10], 'action': 'reject', 'reject_reason': '  ',
        })
        self.assertEqual(empty_batch.status_code, 400)
        self.assertEqual(self.attachment_statuses(), before)

        empty_single = self.status('reviewer-1', 'post', '/api/inspection/photos/10/review', json={
            'action': 'reject', 'reject_reason': '',
        })
        self.assertEqual(empty_single.status_code, 400)
        self.assertEqual(self.attachment_statuses(), before)

    def test_workorder_photo_reads_check_existence_then_site_scope(self):
        paths = [
            '/api/workorders/WO-SITE-2/photo-templates',
            '/api/workorders/WO-SITE-2/photos',
            '/api/workorders/WO-SITE-2/photo-progress',
            '/api/workorders/WO-SITE-2/related',
        ]
        for path in paths:
            response = self.status('operator-1', 'get', path)
            self.assertEqual(response.status_code, 403, (path, response.json))

        self.assertEqual(self.status('operator-1', 'get', '/api/workorders/WO-SITE-1/photo-templates').status_code, 200)
        self.assertEqual(self.status('operator-1', 'get', '/api/workorders/WO-SITE-1/photos').status_code, 200)
        self.assertEqual(self.status('operator-1', 'get', '/api/workorders/WO-SITE-1/photo-progress').status_code, 200)
        self.assertEqual(self.status('operator-1', 'get', '/api/workorders/WO-SITE-1/related').status_code, 200)
        self.assertEqual(self.status('operator-1', 'get', '/api/workorders/UNKNOWN/photo-progress').status_code, 404)

    def test_inspection_item_review_requires_scope_and_rejects_invalid_batches_atomically(self):
        def item_statuses():
            with app_module.get_db() as db:
                return dict(db.execute(
                    'SELECT id, review_status FROM insp_plan_items ORDER BY id'
                ).fetchall())

        before = item_statuses()
        self.assertEqual(self.status('operator-1', 'put',
                                     '/api/inspection-v2/items/100/review',
                                     json={'action': 'approve'}).status_code, 403)
        self.assertEqual(self.status('reviewer-1', 'put',
                                     '/api/inspection-v2/items/101/review',
                                     json={'action': 'approve'}).status_code, 403)
        for payload, expected in [
            ({'approve_ids': [100, 999]}, 404),
            ({'approve_ids': [100, 101]}, 403),
            ({'approve_ids': [100, 102]}, 409),
        ]:
            response = self.status('reviewer-1', 'post',
                                   '/api/inspection-v2/items/batch-review', json=payload)
            self.assertEqual(response.status_code, expected, response.json)
            self.assertEqual(item_statuses(), before)

        valid = self.status('reviewer-1', 'post',
                            '/api/inspection-v2/items/batch-review',
                            json={'approve_ids': [100]})
        self.assertEqual(valid.status_code, 200, valid.json)
        self.assertEqual(item_statuses()[100], 2)

    def test_operation_attachment_batch_prevalidates_every_object_before_writing(self):
        def snapshot():
            with app_module.get_db() as db:
                statuses = {
                    row['id']: (row['review_status'], row['reviewer_id'])
                    for row in db.execute(
                        'SELECT id, review_status, reviewer_id FROM operation_attachments ORDER BY id'
                    ).fetchall()
                }
                notices = db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
            return statuses, notices

        for payload, expected in [
            ({'attachment_ids': [10, 999], 'action': 'approve'}, 404),
            ({'attachment_ids': [10, 12], 'action': 'approve'}, 403),
            ({'attachment_ids': [10, 13], 'action': 'approve'}, 409),
            ({'attachment_ids': [10, 14], 'action': 'approve'}, 409),
            ({'attachment_ids': [10, 20], 'action': 'approve'}, 400),
        ]:
            before = snapshot()
            response = self.status('reviewer-1', 'post',
                                   '/api/operation-attachments/review', json=payload)
            self.assertEqual(response.status_code, expected, response.json)
            self.assertEqual(snapshot(), before)

        allowed = self.status('admin', 'post', '/api/operation-attachments/review', json={
            'attachment_ids': [10, 12], 'action': 'approve',
        })
        self.assertEqual(allowed.status_code, 200, allowed.json)
        self.assertEqual(allowed.json['count'], 2)

    def test_parts_request_migration_creates_items_first_and_is_idempotent(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        original_get_db = app_module.get_db

        @contextmanager
        def fresh_db():
            db = sqlite3.connect(temp.name)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

        try:
            app_module.get_db = fresh_db
            app_module.migrate_parts_requests_v2()
            app_module.migrate_parts_requests_v2()
            with fresh_db() as db:
                tables = {row['name'] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                self.assertIn('parts_request_items', tables)
                self.assertIn('part_id', {
                    row['name'] for row in db.execute('PRAGMA table_info(parts_request_items)').fetchall()
                })
        finally:
            app_module.get_db = original_get_db
            os.unlink(temp.name)


if __name__ == '__main__':
    unittest.main()
