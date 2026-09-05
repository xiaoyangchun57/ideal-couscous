import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock

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
        self.original_upload_dir = app_module.UPLOAD_DIR
        self.upload_dir = tempfile.mkdtemp()
        app_module.UPLOAD_DIR = self.upload_dir

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
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, status TEXT, submitted_at TEXT,
                    assignee_id INTEGER, plan_name TEXT, plan_schedule_id INTEGER
                );
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER NOT NULL, photo_urls TEXT,
                    review_status INTEGER DEFAULT 0, submitted_at TEXT,
                    execution_status TEXT DEFAULT 'active', item_name TEXT,
                    actual_photos INTEGER DEFAULT 0, evidence_status TEXT DEFAULT '',
                    supplement_required_at TEXT, rework_required_at TEXT
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
                    source_id INTEGER DEFAULT 0, plan_id INTEGER, item_id INTEGER,
                    site_id INTEGER, uploader_id INTEGER,
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
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT
                );
                INSERT INTO users VALUES
                    (1, 'admin', 'Admin', 'admin', 'active'),
                    (2, 'operator', 'Operator', 'operator', 'active'),
                    (4, 'reviewer', 'Reviewer', 'reviewer', 'active');
                INSERT INTO user_roles VALUES (1, 'admin'), (2, 'operator'), (4, 'reviewer');
                INSERT INTO user_sites VALUES (2, 1), (4, 1);
                INSERT INTO sites VALUES (1, 'Test Station');
                INSERT INTO plan_schedules VALUES (90, 'approved');
                INSERT INTO insp_plans VALUES
                    (100, 'active', '2026-08-11 10:00:00', 2, 'Test Plan', 90);
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
        app_module.UPLOAD_DIR = self.original_upload_dir
        os.unlink(self.db_path)
        shutil.rmtree(self.upload_dir, ignore_errors=True)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def add_rejected_attachment(self, attachment_id=20, *, uploader_id=2,
                                 review_status='rejected', source_id=101,
                                 plan_id=100, item_id=101, site_id=1,
                                 material_role='formal', shared=False,
                                 reject_reason='照片内容未通过审核'):
        photo_dir = os.path.join(self.upload_dir, 'rejected')
        os.makedirs(photo_dir, exist_ok=True)
        stored = f'/uploads/rejected/{attachment_id}-original.jpg'
        thumb = f'/uploads/rejected/{attachment_id}-thumb.jpg'
        for filename in (f'{attachment_id}-original.jpg', f'{attachment_id}-thumb.jpg'):
            with open(os.path.join(photo_dir, filename), 'wb') as handle:
                handle.write(filename.encode())
        with app_module.get_db() as db:
            item_row = db.execute('SELECT photo_urls FROM insp_plan_items WHERE id=?', (item_id,)).fetchone()
            existing_urls = app_module._attachment_purge_urls(item_row['photo_urls']) if item_row else []
            db.execute("""UPDATE insp_plan_items SET photo_urls=?,actual_photos=?,
                evidence_status='supplement_required',supplement_required_at='2026-08-11 11:00:00',
                item_name=COALESCE(NULLIF(item_name,''),'浊度') WHERE id=?""",
                (json.dumps(existing_urls + [stored]), len(existing_urls) + 1, item_id))
            db.execute("""INSERT INTO operation_attachments
                (id,filename,stored_path,thumbnail_path,source_type,source_id,plan_id,item_id,
                  site_id,uploader_id,uploader_name,review_status,reject_reason,
                  evidence_qualification,extra_json)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (attachment_id, f'{attachment_id}.jpg', stored, thumb, 'inspection', source_id,
                  plan_id, item_id, site_id, uploader_id, 'Operator', review_status, reject_reason, 'qualified',
                  json.dumps({'material_role': material_role})))
            if shared:
                db.execute("""INSERT INTO operation_attachments
                    (id,filename,stored_path,source_type,source_id,site_id,uploader_id,review_status,extra_json)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (attachment_id + 1000, 'shared.jpg', stored, 'workorder', 50, 1, 1,
                     'approved', '{}'))
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title) VALUES (2,'attachment_void',?,'replace')""",
                (attachment_id,))
        return stored, thumb

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

    def test_admin_purges_one_rejected_photo_and_keeps_rework_state(self):
        stored, thumb = self.add_rejected_attachment()
        response = self.client.post('/api/attachments/20/purge-rejected',
                                    headers=self.headers('admin-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['photo_urls'], [])
        self.assertEqual(response.json['actual_photos'], 0)
        self.assertEqual(response.json['evidence_status'], 'supplement_required')
        with app_module.get_db() as db:
            self.assertIsNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=20').fetchone())
            item = db.execute('SELECT * FROM insp_plan_items WHERE id=101').fetchone()
            self.assertEqual((json.loads(item['photo_urls']), item['actual_photos'],
                              item['evidence_status']), ([], 0, 'supplement_required'))
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM notifications WHERE source_id=20").fetchone()[0], 0)
            summary = json.loads(db.execute("""SELECT remark FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id=20""").fetchone()['remark'])
            self.assertEqual((summary['plan_id'], summary['item_id'], summary['site_id']), (100, 101, 1))
            self.assertEqual(summary['reason'], '照片内容未通过审核')
        for path in (stored, thumb):
            self.assertFalse(os.path.exists(os.path.join(
                self.upload_dir, path[len('/uploads/'):].replace('/', os.sep))))
        replay = self.client.post('/api/attachments/20/purge-rejected',
                                  headers=self.headers('admin-token'))
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertTrue(replay.json['already_deleted'])

    def test_rejected_purge_ignores_client_reason_and_falls_back_for_legacy_blank_reason(self):
        self.add_rejected_attachment(21, reject_reason='审核记录原因')
        forged = self.client.post('/api/attachments/21/purge-rejected',
                                  headers=self.headers('admin-token'),
                                  json={'reason': '客户端伪造原因'})
        self.assertEqual(forged.status_code, 200, forged.json)
        self.add_rejected_attachment(22, reject_reason='   ')
        legacy = self.client.post('/api/attachments/22/purge-rejected',
                                  headers=self.headers('admin-token'), json={})
        self.assertEqual(legacy.status_code, 200, legacy.json)
        with app_module.get_db() as db:
            summaries = {row['source_id']: json.loads(row['remark']) for row in db.execute("""
                SELECT source_id,remark FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id IN (21,22)""").fetchall()}
        self.assertEqual(summaries[21]['reason'], '审核记录原因')
        self.assertEqual(summaries[22]['reason'], '清理已驳回影像')

    def test_batch_preview_admin_success_per_photo_reason_and_idempotent_replay(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,photo_urls,review_status,execution_status,item_name,
                 evidence_status,supplement_required_at)
                VALUES (102,100,1,'[]',3,'active','电导率','supplement_required','2026-08-11 11:00:00')""")
        self.add_rejected_attachment(60, reject_reason='照片一模糊')
        self.add_rejected_attachment(61, reject_reason='   ')
        self.add_rejected_attachment(62, source_id=102, item_id=102, reject_reason='照片三缺少水印')
        with app_module.get_db() as db:
            db.execute('UPDATE operation_attachments SET plan_id=NULL,item_id=NULL WHERE id=61')
        preview = self.client.get('/api/attachments/60/purge-rejected-batch',
                                  headers=self.headers('admin-token'))
        self.assertEqual((preview.status_code, preview.json['count'], preview.json['plan_id'],
                          preview.json['site_id']), (200, 3, 100, 1))
        response = self.client.post('/api/attachments/60/purge-rejected-batch',
                                    headers=self.headers('admin-token'),
                                    json={'reason': '客户端伪造整包原因', 'attachment_ids': [60]})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual((response.json['count'], response.json['attachment_ids']), (3, [60, 61, 62]))
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments WHERE id IN (60,61,62)').fetchone()[0], 0)
            item_states = {row['id']: (json.loads(row['photo_urls']), row['actual_photos'])
                           for row in db.execute(
                               'SELECT id,photo_urls,actual_photos FROM insp_plan_items WHERE id IN (101,102)')}
            summaries = {row['source_id']: json.loads(row['remark']) for row in db.execute("""
                SELECT source_id,remark FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id IN (60,61,62)""")}
        self.assertEqual(item_states, {101: ([], 0), 102: ([], 0)})
        self.assertEqual([summaries[value]['reason'] for value in (60, 61, 62)],
                         ['照片一模糊', '清理已驳回影像', '照片三缺少水印'])
        self.assertEqual(len({summaries[value]['batch_key'] for value in summaries}), 1)
        replay = self.client.post('/api/attachments/60/purge-rejected-batch',
                                  headers=self.headers('admin-token'))
        self.assertEqual((replay.status_code, replay.json['already_deleted'], replay.json['count']),
                         (200, True, 3))
        self.assertEqual(replay.json['attachment_ids'], response.json['attachment_ids'])
        self.assertEqual(replay.json['items'], response.json['items'])
        self.assertEqual({row['item_id'] for row in replay.json['items']}, {101, 102})
        with app_module.get_db() as db:
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id IN (60,61,62)""").fetchone()[0], 3)

    def test_batch_shared_paths_are_deleted_once_or_retained_for_external_references(self):
        first_paths = self.add_rejected_attachment(130)
        self.add_rejected_attachment(131)
        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET stored_path=?,thumbnail_path=? WHERE id=131",
                       first_paths)
        removed = []
        real_remove = os.remove
        with mock.patch.object(app_module.os, 'remove', side_effect=lambda value: (
                removed.append(value), real_remove(value))[1]):
            response = self.client.post('/api/attachments/130/purge-rejected-batch',
                                        headers=self.headers('admin-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(removed), 2)
        self.assertEqual(len({os.path.normcase(value) for value in removed}), 2)

        shared_paths = self.add_rejected_attachment(132)
        self.add_rejected_attachment(133)
        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET stored_path=?,thumbnail_path=? WHERE id=133",
                       shared_paths)
            db.execute("""INSERT INTO operation_attachments
                (id,filename,thumbnail_path,source_type,source_id,site_id,uploader_id,
                 review_status,is_deleted,extra_json) VALUES (1132,'external.jpg',?,'workorder',50,1,1,
                 'approved',0,'{}')""", (shared_paths[0],))
        retained = self.client.post('/api/attachments/132/purge-rejected-batch',
                                    headers=self.headers('admin-token'))
        self.assertEqual(retained.status_code, 200, retained.json)
        with app_module.get_db() as db:
            summaries = [json.loads(row['remark']) for row in db.execute("""SELECT remark
                FROM timeline_events WHERE source_type='rejected_attachment_purge'
                  AND source_id IN (132,133) ORDER BY source_id""")]
        self.assertTrue(all(value['shared_file_retained'] for value in summaries))
        self.assertTrue(os.path.exists(os.path.join(
            self.upload_dir, shared_paths[0][len('/uploads/'):].replace('/', os.sep))))

    def test_batch_operator_prevalidates_every_photo_and_link_before_writing(self):
        self.add_rejected_attachment(63)
        self.add_rejected_attachment(64, uploader_id=4)
        denied = self.client.post('/api/attachments/63/purge-rejected-batch',
                                  headers=self.headers('operator-token'))
        self.assertEqual((denied.status_code, denied.json.get('code')),
                         (403, 'REJECTED_ATTACHMENT_PURGE_FORBIDDEN'))
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments WHERE id IN (63,64)').fetchone()[0], 2)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id IN (63,64)""").fetchone()[0], 0)
            db.execute('UPDATE operation_attachments SET uploader_id=2,plan_id=NULL,item_id=NULL WHERE id=64')
        legacy_denied = self.client.post('/api/attachments/63/purge-rejected-batch',
                                         headers=self.headers('operator-token'))
        self.assertEqual((legacy_denied.status_code, legacy_denied.json.get('code')),
                         (403, 'REJECTED_ATTACHMENT_PURGE_FORBIDDEN'))
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments WHERE id IN (63,64)').fetchone()[0], 2)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id IN (63,64)""").fetchone()[0], 0)
            db.execute('UPDATE operation_attachments SET plan_id=100,item_id=101 WHERE id=64')
        allowed = self.client.post('/api/attachments/63/purge-rejected-batch',
                                   headers=self.headers('operator-token'))
        self.assertEqual((allowed.status_code, allowed.json['count']), (200, 2))

        self.add_rejected_attachment(65)
        self.add_rejected_attachment(66, plan_id=999)
        invalid = self.client.post('/api/attachments/65/purge-rejected-batch',
                                   headers=self.headers('admin-token'))
        self.assertEqual((invalid.status_code, invalid.json.get('code')),
                         (409, 'REJECTED_ATTACHMENT_PURGE_LINK_INVALID'))
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments WHERE id IN (65,66)').fetchone()[0], 2)

    def test_batch_anchor_scope_precedes_state_and_link_details_for_non_admin(self):
        self.add_rejected_attachment(67)
        with app_module.get_db() as db:
            db.execute('DELETE FROM user_sites WHERE user_id=2 AND site_id=1')
        no_site = self.client.get('/api/attachments/67/purge-rejected-batch',
                                  headers=self.headers('operator-token'))
        self.assertEqual((no_site.status_code, no_site.json.get('code')),
                         (403, 'REJECTED_ATTACHMENT_PURGE_FORBIDDEN'))
        with app_module.get_db() as db:
            db.execute('INSERT INTO user_sites VALUES (2,1)')
            db.execute('UPDATE insp_plans SET assignee_id=4 WHERE id=100')
            db.execute("UPDATE operation_attachments SET review_status='approved' WHERE id=67")
        wrong_assignee = self.client.get('/api/attachments/67/purge-rejected-batch',
                                         headers=self.headers('operator-token'))
        self.assertEqual((wrong_assignee.status_code, wrong_assignee.json.get('code')),
                         (403, 'REJECTED_ATTACHMENT_PURGE_FORBIDDEN'))
        missing = self.client.get('/api/attachments/9999/purge-rejected-batch',
                                  headers=self.headers('operator-token'))
        self.assertEqual((missing.status_code, missing.json.get('code')),
                         (403, 'REJECTED_ATTACHMENT_PURGE_FORBIDDEN'))
        with app_module.get_db() as db:
            db.execute('UPDATE insp_plans SET assignee_id=2 WHERE id=100')
        authorized_state = self.client.get('/api/attachments/67/purge-rejected-batch',
                                           headers=self.headers('operator-token'))
        self.assertEqual((authorized_state.status_code, authorized_state.json.get('code')),
                         (409, 'REJECTED_ATTACHMENT_PURGE_STATE_INVALID'))
        admin_missing = self.client.get('/api/attachments/9999/purge-rejected-batch',
                                        headers=self.headers('admin-token'))
        self.assertEqual((admin_missing.status_code, admin_missing.json.get('code')),
                         (404, 'ATTACHMENT_NOT_FOUND'))

    def test_batch_discovery_rejects_explicit_package_rows_with_broken_item_links(self):
        anchor_paths = self.add_rejected_attachment(68)
        broken_paths = self.add_rejected_attachment(69, source_id=999, plan_id=100, item_id=101)
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plans
                (id,status,assignee_id,plan_name) VALUES (200,'active',2,'Other Plan')""")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,photo_urls,review_status,execution_status,item_name)
                VALUES (200,200,1,'[]',3,'active','Other Item')""")
        cross_paths = self.add_rejected_attachment(
            74, source_id=200, plan_id=100, item_id=101)
        response = self.client.post('/api/attachments/68/purge-rejected-batch',
                                    headers=self.headers('admin-token'))
        self.assertEqual((response.status_code, response.json.get('code')),
                         (409, 'REJECTED_ATTACHMENT_PURGE_LINK_INVALID'))
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments WHERE id IN (68,69,74)').fetchone()[0], 3)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id IN (68,69,74)""").fetchone()[0], 0)
        for path in anchor_paths + broken_paths + cross_paths:
            target = os.path.join(self.upload_dir, path[len('/uploads/'):].replace('/', os.sep))
            self.assertTrue(os.path.exists(target))

    def test_batch_file_and_database_failures_restore_every_file_and_row(self):
        for mode in ('file', 'database'):
            with self.subTest(mode=mode):
                attachment_ids = (70, 71) if mode == 'file' else (72, 73)
                paths = []
                for attachment_id in attachment_ids:
                    paths.extend(self.add_rejected_attachment(attachment_id))
                absolute_paths = sorted(os.path.join(
                    self.upload_dir, path[len('/uploads/'):].replace('/', os.sep)) for path in paths)
                if mode == 'file':
                    real_remove = os.remove
                    def fail_second(path):
                        if os.path.normcase(path) == os.path.normcase(absolute_paths[1]):
                            raise PermissionError('second batch target locked')
                        return real_remove(path)
                    patcher = mock.patch.object(app_module.os, 'remove', side_effect=fail_second)
                else:
                    patcher = mock.patch.object(
                        app_module, '_ps_purge_delete_ids',
                        side_effect=sqlite3.IntegrityError('forced batch database failure'))
                with patcher:
                    response = self.client.post(
                        f'/api/attachments/{attachment_ids[0]}/purge-rejected-batch',
                        headers=self.headers('admin-token'))
                self.assertEqual(response.status_code, 503, response.json)
                self.assertTrue(all(os.path.exists(path) for path in absolute_paths))
                with app_module.get_db() as db:
                    placeholders = ','.join('?' for _ in attachment_ids)
                    self.assertEqual(db.execute(
                        f'SELECT COUNT(*) FROM operation_attachments WHERE id IN ({placeholders})',
                        attachment_ids).fetchone()[0], 2)
                    self.assertEqual(db.execute(f"""SELECT COUNT(*) FROM timeline_events
                        WHERE source_type='rejected_attachment_purge'
                          AND source_id IN ({placeholders})""", attachment_ids).fetchone()[0], 0)

    def test_batch_commit_failure_restores_files_and_rolls_back_every_database_change(self):
        paths = self.add_rejected_attachment(134) + self.add_rejected_attachment(135)
        absolute_paths = [os.path.join(
            self.upload_dir, value[len('/uploads/'):].replace('/', os.sep)) for value in paths]
        original_get_db = app_module.get_db

        class CommitFailureConnection:
            def __init__(self, connection):
                self.connection = connection
                self.failed = False

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                if not self.failed:
                    self.failed = True
                    raise sqlite3.OperationalError('forced commit failure')
                return self.connection.commit()

        @contextmanager
        def failing_commit_db():
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            proxy = CommitFailureConnection(connection)
            try:
                yield proxy
            finally:
                connection.close()

        app_module.get_db = failing_commit_db
        try:
            response = self.client.post('/api/attachments/134/purge-rejected-batch',
                                        headers=self.headers('admin-token'))
        finally:
            app_module.get_db = original_get_db
        self.assertEqual((response.status_code, response.json.get('code')),
                         (503, 'REJECTED_ATTACHMENT_PURGE_FAILED'))
        self.assertTrue(all(os.path.exists(value) for value in absolute_paths))
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments WHERE id IN (134,135)').fetchone()[0], 2)
            item = db.execute('SELECT photo_urls,actual_photos FROM insp_plan_items WHERE id=101').fetchone()
            self.assertEqual((len(_attachment_urls := app_module._attachment_purge_urls(item['photo_urls'])),
                              item['actual_photos']), (2, 2))
            self.assertTrue(all(path in _attachment_urls for path in (paths[0], paths[2])))
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id IN (134,135)""").fetchone()[0], 0)

    def test_batch_snapshot_failure_happens_before_commit_and_restores_everything(self):
        paths = self.add_rejected_attachment(136) + self.add_rejected_attachment(137)
        absolute_paths = [os.path.join(
            self.upload_dir, value[len('/uploads/'):].replace('/', os.sep)) for value in paths]
        with mock.patch.object(
                app_module, '_rejected_attachment_item_snapshots',
                side_effect=sqlite3.OperationalError('forced snapshot failure')):
            response = self.client.post('/api/attachments/136/purge-rejected-batch',
                                        headers=self.headers('admin-token'))
        self.assertEqual((response.status_code, response.json.get('code')),
                         (503, 'REJECTED_ATTACHMENT_PURGE_FAILED'))
        self.assertTrue(all(os.path.exists(value) for value in absolute_paths))
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM operation_attachments WHERE id IN (136,137)').fetchone()[0], 2)
            item = db.execute('SELECT photo_urls,actual_photos FROM insp_plan_items WHERE id=101').fetchone()
            urls = app_module._attachment_purge_urls(item['photo_urls'])
            self.assertEqual((len(urls), item['actual_photos']), (2, 2))
            self.assertTrue(all(path in urls for path in (paths[0], paths[2])))
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id IN (136,137)""").fetchone()[0], 0)

    def test_operator_requires_assignee_uploader_site_and_rework_context(self):
        self.add_rejected_attachment()
        success = self.client.post('/api/attachments/20/purge-rejected',
                                   headers=self.headers('operator-token'),
                                   json={'reason': '本人返场错误照片'})
        self.assertEqual(success.status_code, 200, success.json)

        for label, setup in (
                ('not-uploader', "UPDATE operation_attachments SET uploader_id=4 WHERE id=20"),
                ('not-assignee', "UPDATE insp_plans SET assignee_id=4 WHERE id=100"),
                ('no-site', "DELETE FROM user_sites WHERE user_id=2 AND site_id=1"),
                ('no-rework', "UPDATE insp_plan_items SET evidence_status='',supplement_required_at=NULL WHERE id=101")):
            with self.subTest(label=label):
                # Each subtest restores the same rejected row after the preceding attempt.
                with app_module.get_db() as db:
                    db.execute("DELETE FROM timeline_events WHERE source_type='rejected_attachment_purge'")
                    db.execute("DELETE FROM operation_attachments WHERE id=20")
                    db.execute("UPDATE insp_plans SET assignee_id=2 WHERE id=100")
                    db.execute("INSERT OR IGNORE INTO user_sites VALUES (2,1)")
                self.add_rejected_attachment()
                with app_module.get_db() as db:
                    db.execute(setup)
                denied = self.client.post('/api/attachments/20/purge-rejected',
                                          headers=self.headers('operator-token'),
                                          json={'reason': '不应成功'})
                self.assertEqual(denied.status_code, 403, denied.json)
                with app_module.get_db() as db:
                    self.assertIsNotNone(db.execute(
                        'SELECT id FROM operation_attachments WHERE id=20').fetchone())

    def test_operator_requires_current_execution_lifecycle_but_admin_can_purge_history(self):
        cases = (
            ('cancelled-plan', "UPDATE insp_plans SET status='cancelled' WHERE id=100"),
            ('archived-plan', "UPDATE insp_plans SET status='archived' WHERE id=100"),
            ('closed-plan', "UPDATE insp_plans SET status='closed' WHERE id=100"),
            ('cancelled-item', "UPDATE insp_plan_items SET execution_status='cancelled' WHERE id=101"),
            ('unapproved-schedule', "UPDATE plan_schedules SET status='cancelled' WHERE id=90"),
            ('missing-schedule', "DELETE FROM plan_schedules WHERE id=90"),
        )
        for index, (label, setup) in enumerate(cases):
            attachment_id = 80 + index
            with self.subTest(label=label):
                with app_module.get_db() as db:
                    db.execute("UPDATE insp_plans SET status='active',plan_schedule_id=90 WHERE id=100")
                    db.execute("UPDATE insp_plan_items SET execution_status='active' WHERE id=101")
                    db.execute("INSERT OR REPLACE INTO plan_schedules VALUES (90,'approved')")
                self.add_rejected_attachment(attachment_id)
                with app_module.get_db() as db:
                    db.execute(setup)
                denied = self.client.post(
                    f'/api/attachments/{attachment_id}/purge-rejected',
                    headers=self.headers('operator-token'), json={'reason': '历史对象不应由执行人删除'})
                self.assertEqual((denied.status_code, denied.json.get('code')),
                                 (403, 'REJECTED_ATTACHMENT_PURGE_FORBIDDEN'))
                allowed = self.client.post(
                    f'/api/attachments/{attachment_id}/purge-rejected',
                    headers=self.headers('admin-token'), json={'reason': '管理员清理驳回历史照片'})
                self.assertEqual(allowed.status_code, 200, allowed.json)

    def test_operator_requires_exact_modern_links_admin_only_allows_unambiguous_legacy_links(self):
        for index, (plan_id, item_id) in enumerate(((None, None), (0, 0))):
            attachment_id = 90 + index
            with self.subTest(plan_id=plan_id, item_id=item_id):
                self.add_rejected_attachment(attachment_id, plan_id=plan_id, item_id=item_id)
                denied = self.client.post(
                    f'/api/attachments/{attachment_id}/purge-rejected',
                    headers=self.headers('operator-token'), json={'reason': '残缺关联'})
                self.assertEqual((denied.status_code, denied.json.get('code')),
                                 (403, 'REJECTED_ATTACHMENT_PURGE_FORBIDDEN'))
                allowed = self.client.post(
                    f'/api/attachments/{attachment_id}/purge-rejected',
                    headers=self.headers('admin-token'), json={'reason': '管理员兼容历史残缺关联'})
                self.assertEqual(allowed.status_code, 200, allowed.json)

        self.add_rejected_attachment(92, plan_id=None, item_id=100)
        collision = self.client.post(
            '/api/attachments/92/purge-rejected', headers=self.headers('admin-token'),
            json={'reason': '数值碰撞不能视为有效关联'})
        self.assertEqual((collision.status_code, collision.json.get('code')),
                         (409, 'REJECTED_ATTACHMENT_PURGE_LINK_INVALID'))

    def test_rejected_purge_protects_other_states_materials_and_bad_links(self):
        cases = (
            ('pending', 'formal', 101, 100, 101),
            ('approved', 'formal', 101, 100, 101),
            ('voided', 'formal', 101, 100, 101),
            ('superseded', 'formal', 101, 100, 101),
            ('rejected', 'supplement', 101, 100, 101),
            ('rejected', 'formal', 999, 100, 101),
            ('rejected', 'formal', 101, 999, 101),
            ('rejected', 'formal', 101, 100, 999),
        )
        for index, (status, role, source_id, plan_id, item_id) in enumerate(cases):
            attachment_id = 30 + index
            with self.subTest(status=status, role=role, index=index):
                self.add_rejected_attachment(attachment_id, review_status=status,
                                             material_role=role, source_id=source_id,
                                             plan_id=plan_id, item_id=item_id)
                response = self.client.post(f'/api/attachments/{attachment_id}/purge-rejected',
                                            headers=self.headers('admin-token'),
                                            json={'reason': '保护边界'})
                self.assertEqual(response.status_code, 409, response.json)
                with app_module.get_db() as db:
                    self.assertIsNotNone(db.execute(
                        'SELECT id FROM operation_attachments WHERE id=?',
                        (attachment_id,)).fetchone())

    def test_shared_file_is_retained_and_second_file_failure_rolls_back(self):
        stored, _ = self.add_rejected_attachment(40, shared=True)
        shared = self.client.post('/api/attachments/40/purge-rejected',
                                  headers=self.headers('admin-token'),
                                  json={'reason': '共享文件测试'})
        self.assertEqual(shared.status_code, 200, shared.json)
        self.assertTrue(shared.json['shared_file_retained'])
        self.assertTrue(os.path.exists(os.path.join(
            self.upload_dir, stored[len('/uploads/'):].replace('/', os.sep))))

        stored, thumb = self.add_rejected_attachment(41)
        original_file = os.path.join(self.upload_dir, stored[len('/uploads/'):].replace('/', os.sep))
        thumbnail_file = os.path.join(self.upload_dir, thumb[len('/uploads/'):].replace('/', os.sep))
        second_target = max((original_file, thumbnail_file))
        first_target = min((original_file, thumbnail_file))
        real_remove = os.remove

        def fail_second(path):
            if os.path.normcase(path) == os.path.normcase(second_target):
                raise PermissionError('second target locked')
            return real_remove(path)

        with mock.patch.object(app_module.os, 'remove', side_effect=fail_second):
            failed = self.client.post('/api/attachments/41/purge-rejected',
                                      headers=self.headers('admin-token'),
                                      json={'reason': '文件失败'})
        self.assertEqual((failed.status_code, failed.json.get('code')),
                         (503, 'REJECTED_ATTACHMENT_PURGE_FILE_FAILED'))
        self.assertTrue(os.path.exists(first_target))
        self.assertTrue(os.path.exists(second_target))
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute(
                'SELECT id FROM operation_attachments WHERE id=41').fetchone())
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='rejected_attachment_purge' AND source_id=41""").fetchone()[0], 0)

    def test_cross_column_and_soft_deleted_references_retain_shared_files(self):
        cases = (
            ('stored-to-thumbnail', 'stored_path', 'thumbnail_path', 0),
            ('thumbnail-to-stored', 'thumbnail_path', 'stored_path', 0),
            ('soft-deleted-reference', 'stored_path', 'stored_path', 1),
        )
        for index, (label, target_column, reference_column, is_deleted) in enumerate(cases):
            attachment_id = 110 + index
            with self.subTest(label=label):
                stored, thumb = self.add_rejected_attachment(attachment_id)
                target_path = stored if target_column == 'stored_path' else thumb
                with app_module.get_db() as db:
                    db.execute(f"""INSERT INTO operation_attachments
                        (id,filename,{reference_column},source_type,source_id,site_id,uploader_id,
                         review_status,is_deleted,extra_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (attachment_id + 1000, f'{label}.jpg', target_path, 'workorder', 50,
                         1, 1, 'approved', is_deleted, '{}'))
                response = self.client.post(
                    f'/api/attachments/{attachment_id}/purge-rejected',
                    headers=self.headers('admin-token'), json={'reason': '共享路径保护'})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertTrue(response.json['shared_file_retained'])
                target_file = os.path.join(
                    self.upload_dir, target_path[len('/uploads/'):].replace('/', os.sep))
                self.assertTrue(os.path.exists(target_file))

    def test_supplement_remaining_url_is_not_counted_as_effective_evidence(self):
        rejected_path, _ = self.add_rejected_attachment(120)
        supplement_path = '/uploads/rejected/120-supplement.jpg'
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,plan_id,item_id,site_id,uploader_id,
                 review_status,evidence_qualification,extra_json)
                VALUES (121,'supplement.jpg',?,'inspection',101,100,101,1,2,
                        'approved','qualified',?)""",
                (supplement_path, json.dumps({'material_role': 'supplement'})))
            db.execute('UPDATE insp_plan_items SET photo_urls=?,actual_photos=2 WHERE id=101',
                       (json.dumps([rejected_path, supplement_path]),))
        response = self.client.post(
            '/api/attachments/120/purge-rejected', headers=self.headers('admin-token'),
            json={'reason': '删除驳回照片后统一重算'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['photo_urls'], [supplement_path])
        self.assertEqual(response.json['actual_photos'], 0)
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT actual_photos FROM insp_plan_items WHERE id=101').fetchone()[0], 0)

if __name__ == '__main__':
    unittest.main()
