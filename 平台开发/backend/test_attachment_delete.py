import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402


class AttachmentDeleteTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
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
            'admin-token': {
                'id': 1, 'username': 'admin', 'real_name': 'Admin',
                'role': 'admin', 'roles': ['admin'],
            },
            'operator-token': {
                'id': 2, 'username': 'operator', 'real_name': 'Operator',
                'role': 'operator', 'roles': ['operator'],
            },
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, real_name TEXT,
                    role TEXT, status TEXT DEFAULT 'active'
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT, UNIQUE(user_id, role));
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER, UNIQUE(user_id, site_id));
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE insp_plans (id INTEGER PRIMARY KEY, status TEXT, submitted_at TEXT);
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER NOT NULL, photo_urls TEXT,
                    review_status INTEGER DEFAULT 0, submitted_at TEXT,
                    execution_status TEXT DEFAULT 'active'
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, status TEXT,
                    images TEXT, site_id INTEGER
                );
                CREATE TABLE manual_reports (
                    id INTEGER PRIMARY KEY, site_id INTEGER, status TEXT, order_no TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, filename TEXT, stored_path TEXT,
                    thumbnail_path TEXT DEFAULT '', file_type TEXT DEFAULT 'image',
                    mime_type TEXT DEFAULT 'image/jpeg', file_size INTEGER DEFAULT 1,
                    description TEXT DEFAULT '', source_type TEXT DEFAULT '',
                    source_id INTEGER DEFAULT 0, site_id INTEGER, uploader_id INTEGER,
                    uploader_name TEXT DEFAULT '', gps_lat REAL, gps_lng REAL,
                    taken_at TEXT, category TEXT DEFAULT '', is_deleted INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, archived INTEGER DEFAULT 0,
                    archived_at TEXT, archived_by INTEGER, archive_reason TEXT DEFAULT '',
                    extra_json TEXT DEFAULT '',
                    review_required INTEGER DEFAULT 0, review_status TEXT DEFAULT 'pending',
                    reviewer_id INTEGER, reviewed_at TEXT, reject_reason TEXT DEFAULT '',
                    capture_source TEXT DEFAULT '', deleted_at TEXT, deleted_by INTEGER,
                    delete_reason TEXT DEFAULT '', evidence_qualification TEXT DEFAULT 'qualified'
                );
                CREATE TABLE attachment_deletion_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, attachment_id INTEGER UNIQUE,
                    original_filename TEXT, original_stored_path TEXT,
                    source_type TEXT, source_id INTEGER, site_id INTEGER,
                    operator_id INTEGER, operator_name TEXT, reason TEXT,
                    deleted_at TEXT, delete_before_state TEXT
                );
                CREATE TABLE operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT, action TEXT,
                    target_type TEXT, target_id INTEGER, operator TEXT,
                    operator_id INTEGER, details TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    source_type TEXT, source_id INTEGER, title TEXT, content TEXT,
                    is_read INTEGER DEFAULT 0, dedupe_key TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO users VALUES
                    (1, 'admin', 'Admin', 'admin', 'active'),
                    (2, 'operator', 'Operator', 'operator', 'active'),
                    (4, 'reviewer', 'Reviewer', 'reviewer', 'active');
                INSERT INTO user_roles VALUES (1, 'admin'), (2, 'operator'), (4, 'reviewer');
                INSERT INTO user_sites VALUES (2, 1), (4, 1);
                INSERT INTO sites VALUES (1, 'Test Station');
                INSERT INTO insp_plans VALUES (100, 'submitted', '2026-08-11 10:00:00');
                INSERT INTO insp_plan_items
                    (id, plan_id, site_id, photo_urls, review_status, submitted_at, execution_status)
                VALUES
                    (101, 100, 1, '/uploads/TEST_DELETE_FORMAL.jpg', 1, '2026-08-11 10:00:00', 'active');
                INSERT INTO work_orders VALUES
                    (50, 'WO-TEST-001', 'reviewing', '/uploads/TEST_DELETE_WORKORDER.jpg', 1);
                INSERT INTO operation_attachments
                    (id, filename, stored_path, source_type, source_id, site_id,
                     uploader_id, uploader_name, review_required, review_status, created_at)
                VALUES
                    (10, 'TEST_DELETE_TEMP_001.jpg', '/uploads/TEST_DELETE_TEMP_001.jpg',
                     'test', 0, 1, 2, 'Operator', 1, 'pending', '2026-08-11 09:00:00'),
                    (11, 'TEST_DELETE_FORMAL.jpg', '/uploads/TEST_DELETE_FORMAL.jpg',
                     'inspection', 101, 1, 2, 'Operator', 0, 'pending', '2026-08-11 09:01:00'),
                    (12, 'TEST_DELETE_WORKORDER.jpg', '/uploads/TEST_DELETE_WORKORDER.jpg',
                     'workorder', 50, 1, 2, 'Operator', 0, 'pending', '2026-08-11 09:02:00'),
                    (13, 'TEST_DELETE_SUBMITTED_LINK.jpg', '/uploads/TEST_DELETE_FORMAL.jpg',
                     'test', 0, 1, 2, 'Operator', 0, 'pending', '2026-08-11 09:03:00');
            ''')
            app_module._notify_attachment_reviewers(db, 1, 10, 'test')

        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def test_non_admin_is_403_and_has_zero_side_effect(self):
        response = self.client.delete(
            '/api/attachments/10', headers=self.headers('operator-token'),
            json={'reason': 'test'},
        )
        self.assertEqual(response.status_code, 403, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT is_deleted FROM operation_attachments WHERE id=10').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM attachment_deletion_audits').fetchone()[0], 0)

    def test_missing_reason_and_missing_record_have_clear_status(self):
        missing_reason = self.client.delete(
            '/api/attachments/10', headers=self.headers('admin-token'), json={'reason': '   '},
        )
        self.assertEqual(missing_reason.status_code, 400, missing_reason.json)
        self.assertEqual(missing_reason.json['code'], 'DELETE_REASON_REQUIRED')
        missing = self.client.delete(
            '/api/attachments/999', headers=self.headers('admin-token'),
            json={'reason': 'not found'},
        )
        self.assertEqual(missing.status_code, 404, missing.json)

    def test_formal_evidence_is_blocked_by_server_owned_links(self):
        for attachment_id in (11, 12):
            check = self.client.get(
                f'/api/attachments/{attachment_id}/delete-check',
                headers=self.headers('admin-token'),
            )
            self.assertEqual(check.status_code, 200, check.json)
            self.assertFalse(check.json['can_delete'])
            response = self.client.delete(
                f'/api/attachments/{attachment_id}', headers=self.headers('admin-token'),
                json={'reason': 'test'},
            )
            self.assertEqual(response.status_code, 409, response.json)
            self.assertEqual(response.json['error'], app_module.ATTACHMENT_DELETE_BLOCKED_MESSAGE)
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM attachment_deletion_audits'
            ).fetchone()[0], 0)

    def test_pending_inspection_and_manual_report_evidence_are_blocked(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO insp_plans (id, status, submitted_at) VALUES (200, 'draft', NULL)")
            db.execute("""INSERT INTO insp_plan_items
                (id, plan_id, site_id, photo_urls, review_status, submitted_at, execution_status)
                VALUES (201, 200, 1, '/uploads/TEST_DELETE_PENDING.jpg', 1, NULL, 'active')""")
            db.execute("""INSERT INTO manual_reports (id, site_id, status, order_no)
                VALUES (300, 1, 'dispatched', 'MR-300')""")
            db.execute("""INSERT INTO operation_attachments
                (id, filename, stored_path, source_type, source_id, site_id,
                 uploader_id, uploader_name, review_required, review_status, created_at)
                VALUES
                (14, 'TEST_DELETE_PENDING.jpg', '/uploads/TEST_DELETE_PENDING.jpg',
                 'test', 0, 1, 2, 'Operator', 1, 'pending', '2026-08-11 09:04:00'),
                (15, 'TEST_DELETE_REPORT.jpg', '/uploads/TEST_DELETE_REPORT.jpg',
                 'manual_report', 300, 1, 2, 'Operator', 0, 'pending', '2026-08-11 09:05:00')""")

        for attachment_id in (15,):
            check = self.client.get(
                f'/api/attachments/{attachment_id}/delete-check',
                headers=self.headers('admin-token'),
            )
            self.assertEqual(check.status_code, 200, check.json)
            self.assertFalse(check.json['can_delete'])
            response = self.client.delete(
                f'/api/attachments/{attachment_id}', headers=self.headers('admin-token'),
                json={'reason': 'test'},
            )
            self.assertEqual(response.status_code, 409, response.json)
            self.assertEqual(response.json['error'], app_module.ATTACHMENT_DELETE_BLOCKED_MESSAGE)

    def test_unbound_test_media_source_id_zero_or_null_is_ordinary_even_with_shared_path(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (id, filename, stored_path, source_type, source_id, site_id, uploader_id, uploader_name, review_status)
                VALUES (16, 'TEST_DELETE_NULL.jpg', '/uploads/TEST_DELETE_FORMAL.jpg', 'test', NULL, 1, 2, 'Operator', 'approved')""")
        for attachment_id in (13, 16):
            check = self.client.get(f'/api/attachments/{attachment_id}/delete-check', headers=self.headers('admin-token'))
            self.assertEqual(check.status_code, 200, check.json)
            self.assertTrue(check.json['can_delete'], check.json)
            response = self.client.delete(f'/api/attachments/{attachment_id}', headers=self.headers('admin-token'), json={'reason': 'unbound test media'})
            self.assertEqual(response.status_code, 200, response.json)

    def test_test_media_soft_delete_updates_views_notifications_and_audit(self):
        before_stats = self.client.get('/api/attachments/stats', headers=self.headers('admin-token'))
        self.assertEqual(before_stats.json['all_records'], 4)
        self.assertEqual(before_stats.json['total'], 0)
        self.assertEqual(before_stats.json['review_pending'], 1)
        before_notice = self.client.get('/api/notifications?status=unread', headers=self.headers('admin-token'))
        self.assertEqual(before_notice.json['unread_count'], 0, before_notice.json)

        response = self.client.delete(
            '/api/attachments/10', headers=self.headers('admin-token'),
            json={'reason': '测试误传，未绑定正式业务'},
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['deleted'])

        listing = self.client.get('/api/attachments?limit=100', headers=self.headers('admin-token'))
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertNotIn(10, [item['id'] for item in listing.json['items']])
        after_stats = self.client.get('/api/attachments/stats', headers=self.headers('admin-token'))
        self.assertEqual(after_stats.json['all_records'], 3)
        self.assertEqual(after_stats.json['total'], 0)
        self.assertEqual(after_stats.json['review_pending'], 0)
        after_notice = self.client.get('/api/notifications?status=unread', headers=self.headers('admin-token'))
        self.assertEqual(after_notice.json['unread_count'], 0, after_notice.json)

        with app_module.get_db() as db:
            attachment = db.execute(
                'SELECT is_deleted, stored_path, deleted_by, delete_reason FROM operation_attachments WHERE id=10'
            ).fetchone()
            self.assertEqual(attachment['is_deleted'], 1)
            self.assertEqual(attachment['stored_path'], '/uploads/TEST_DELETE_TEMP_001.jpg')
            self.assertEqual(attachment['deleted_by'], 1)
            self.assertEqual(attachment['delete_reason'], '测试误传，未绑定正式业务')
            audit = db.execute(
                'SELECT * FROM attachment_deletion_audits WHERE attachment_id=10'
            ).fetchone()
            self.assertEqual(audit['original_filename'], 'TEST_DELETE_TEMP_001.jpg')
            self.assertEqual(audit['original_stored_path'], '/uploads/TEST_DELETE_TEMP_001.jpg')
            self.assertEqual(audit['source_type'], 'test')
            self.assertEqual(audit['source_id'], 0)
            self.assertEqual(audit['site_id'], 1)
            self.assertEqual(audit['operator_id'], 1)
            self.assertEqual(audit['operator_name'], 'Admin')
            self.assertEqual(audit['reason'], '测试误传，未绑定正式业务')
            before_state = json.loads(audit['delete_before_state'])
            self.assertEqual(before_state['is_deleted'], 0)
            self.assertEqual(before_state['review_status'], 'pending')
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM operation_logs WHERE action='soft_delete' AND target_id=10"
            ).fetchone()[0], 1)

        repeated = self.client.delete('/api/attachments/10', headers=self.headers('admin-token'))
        self.assertEqual(repeated.status_code, 200, repeated.json)
        self.assertTrue(repeated.json['already_deleted'])
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM attachment_deletion_audits WHERE attachment_id=10'
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM operation_logs WHERE action='soft_delete' AND target_id=10"
            ).fetchone()[0], 1)

        refreshed = self.client.get('/api/attachments/10', headers=self.headers('admin-token'))
        self.assertEqual(refreshed.status_code, 404, refreshed.json)

if __name__ == '__main__':
    unittest.main()
