import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402


class AttachmentVoidClosureTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(prefix='TEST_MEDIA_FIX_', suffix='.db', delete=False)
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
            except Exception:
                db.rollback()
                raise
            else:
                db.commit()
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'admin-token': {'id': 1, 'username': 'admin', 'real_name': '管理员', 'role': 'admin', 'roles': ['admin']},
            'operator-token': {'id': 2, 'username': 'operator', 'real_name': '执行人', 'role': 'operator', 'roles': ['operator']},
            'reviewer-token': {'id': 3, 'username': 'reviewer', 'real_name': '审核员', 'role': 'reviewer', 'roles': ['reviewer']},
            'cross-reviewer-token': {'id': 5, 'username': 'cross', 'real_name': '跨站审核员', 'role': 'reviewer', 'roles': ['reviewer']},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, real_name TEXT,
                    role TEXT, status TEXT DEFAULT 'active');
                CREATE TABLE user_roles (user_id INTEGER, role TEXT, UNIQUE(user_id, role));
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER, UNIQUE(user_id, site_id));
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, code TEXT);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER
                );
                CREATE TABLE insp_plans (id INTEGER PRIMARY KEY, plan_name TEXT, status TEXT,
                    assignee_id INTEGER, completion_rate REAL DEFAULT 100);
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, item_name TEXT,
                    category TEXT DEFAULT '现场', photo_urls TEXT DEFAULT '[]', result TEXT,
                    review_status INTEGER DEFAULT 2, review_comment TEXT DEFAULT '',
                    reviewer_id INTEGER, review_time TEXT, actual_photos INTEGER DEFAULT 0,
                    required_photos INTEGER DEFAULT 1, execution_status TEXT DEFAULT 'active',
                    completed_at TEXT, check_out_time TEXT, evidence_status TEXT DEFAULT '',
                    supplement_required_at TEXT DEFAULT '', supplement_source_attachment_id INTEGER,
                    supplement_reason TEXT DEFAULT '', rework_required_at TEXT DEFAULT '');
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, filename TEXT, stored_path TEXT, thumbnail_path TEXT DEFAULT '',
                    file_type TEXT DEFAULT 'image', mime_type TEXT DEFAULT 'image/jpeg', file_size INTEGER DEFAULT 1,
                    description TEXT DEFAULT '', source_type TEXT DEFAULT '', source_id INTEGER DEFAULT 0,
                    plan_id INTEGER, item_id INTEGER, item_name TEXT DEFAULT '',
                    site_id INTEGER, uploader_id INTEGER, uploader_name TEXT DEFAULT '',
                    gps_lat REAL, gps_lng REAL, taken_at TEXT, category TEXT DEFAULT '', is_deleted INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, archived INTEGER DEFAULT 0, archived_at TEXT,
                    archived_by INTEGER, archive_reason TEXT DEFAULT '', watermark_text TEXT DEFAULT '',
                    recognized_category TEXT DEFAULT '', match_status TEXT DEFAULT 'auto', match_confidence REAL,
                    review_required INTEGER DEFAULT 1, extra_json TEXT DEFAULT '', review_action TEXT DEFAULT '',
                    is_flagged INTEGER DEFAULT 0, flag_reason TEXT DEFAULT '', flag_rule TEXT DEFAULT '',
                    capture_source TEXT DEFAULT 'camera', sha256_hash TEXT DEFAULT '', duplicate_of_id INTEGER,
                    perceptual_hash TEXT DEFAULT '', watermark_code TEXT DEFAULT '', review_status TEXT DEFAULT 'pending',
                    evidence_qualification TEXT DEFAULT 'qualified', evidence_basis TEXT DEFAULT 'camera_session',
                    evidence_reason TEXT DEFAULT '', evidence_next_action TEXT DEFAULT '',
                    reviewer_id INTEGER, reviewed_at TEXT, reject_reason TEXT DEFAULT '', requirement_id INTEGER,
                    deleted_at TEXT, deleted_by INTEGER, delete_reason TEXT DEFAULT '', archive_name TEXT DEFAULT '',
                    voided_at TEXT, voided_by INTEGER, void_reason TEXT DEFAULT '');
                CREATE TABLE attachment_void_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, attachment_id INTEGER UNIQUE,
                    original_source_type TEXT DEFAULT '', original_source_id INTEGER, original_site_id INTEGER,
                    original_plan_id INTEGER, original_item_id INTEGER, original_item_name TEXT DEFAULT '',
                    original_site_name TEXT DEFAULT '', original_uploader_id INTEGER,
                    original_uploader_name TEXT DEFAULT '', original_archive_name TEXT DEFAULT '',
                    original_review_status TEXT DEFAULT '', original_reviewer_id INTEGER, original_reviewed_at TEXT,
                    original_risk_snapshot TEXT DEFAULT '{}', original_state TEXT DEFAULT '{}',
                    replacement_item_id INTEGER, replacement_plan_id INTEGER, replacement_required INTEGER DEFAULT 1,
                    operator_id INTEGER, operator_name TEXT DEFAULT '', reason TEXT, voided_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT, action TEXT, target_type TEXT,
                    target_id INTEGER, operator TEXT, operator_id INTEGER, details TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT, source_id INTEGER,
                    title TEXT, content TEXT, is_read INTEGER DEFAULT 0, dedupe_key TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (1, 'admin', '管理员', 'admin', 'active');
                INSERT INTO users VALUES (2, 'operator', '执行人', 'operator', 'active');
                INSERT INTO users VALUES (3, 'reviewer', '审核员', 'reviewer', 'active');
                INSERT INTO users VALUES (5, 'cross', '跨站审核员', 'reviewer', 'active');
                INSERT INTO user_roles VALUES (1, 'admin');
                INSERT INTO user_roles VALUES (3, 'reviewer');
                INSERT INTO user_roles VALUES (5, 'reviewer');
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO user_sites VALUES (3, 1);
                INSERT INTO user_sites VALUES (5, 2);
                INSERT INTO sites VALUES (1, 'TEST_MEDIA_FIX_站点362', 'TEST_MEDIA_FIX_362');
                INSERT INTO sites VALUES (2, 'TEST_MEDIA_FIX_站点999', 'TEST_MEDIA_FIX_999');
                INSERT INTO insp_plans VALUES (100, 'TEST_MEDIA_FIX_巡检计划', 'active', 2, 100);
                INSERT INTO insp_plans VALUES (200, 'TEST_MEDIA_FIX_已关单计划', 'closed', 2, 100);
                INSERT INTO insp_plan_items
                    (id, plan_id, site_id, item_name, photo_urls, result, review_status, actual_photos)
                    VALUES (101, 100, 1, '室内定位测试站-温度',
                            '["/uploads/TEST_MEDIA_FIX_original.jpg","/uploads/TEST_MEDIA_FIX_other.jpg"]',
                            'normal', 2, 2);
                INSERT INTO insp_plan_items
                    (id, plan_id, site_id, item_name, photo_urls, result, review_status, actual_photos)
                    VALUES (102, 100, 1, '室内定位测试站-流量', '[]', 'normal', 2, 0);
                INSERT INTO insp_plan_items
                    (id, plan_id, site_id, item_name, photo_urls, result, review_status, actual_photos)
                    VALUES (201, 200, 1, '已关单检查项', '["/uploads/TEST_MEDIA_FIX_closed.jpg"]', 'normal', 2, 1);
            ''')
            db.executemany('''
                    INSERT INTO operation_attachments
                    (id, filename, stored_path, description, source_type, source_id, site_id,
                     uploader_id, uploader_name, category, review_required, review_status, reviewer_id,
                     reviewed_at, is_flagged, flag_reason, archive_name)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', [
                    (10, 'TEST_MEDIA_FIX_original.jpg', '/uploads/TEST_MEDIA_FIX_original.jpg', '温度', 'inspection', 101, 1, 2, '执行人', '现场照片', 1, 'approved', 3, '2026-08-11 09:00:00', 1, 'GPS风险', 'TEST_MEDIA_FIX_站点362 · 室内定位测试站-温度 · 现场照片'),
                    (11, 'TEST_MEDIA_FIX_other.jpg', '/uploads/TEST_MEDIA_FIX_other.jpg', '温度', 'inspection', 101, 1, 2, '执行人', '现场照片', 1, 'approved', 3, '2026-08-11 09:01:00', 0, '', 'TEST_MEDIA_FIX_站点362 · 室内定位测试站-温度 · 现场照片'),
                    (12, 'TEST_MEDIA_FIX_pending.jpg', '/uploads/TEST_MEDIA_FIX_pending.jpg', '流量', 'inspection', 102, 1, 2, '执行人', '现场照片', 1, 'pending', None, None, 0, '', 'TEST_MEDIA_FIX_站点362 · 室内定位测试站-流量 · 现场照片'),
                    (13, 'TEST_MEDIA_FIX_rejected.jpg', '/uploads/TEST_MEDIA_FIX_rejected.jpg', '流量', 'inspection', 102, 1, 2, '执行人', '现场照片', 1, 'rejected', 3, '2026-08-11 09:02:00', 0, '', 'TEST_MEDIA_FIX_站点362 · 室内定位测试站-流量 · 现场照片'),
                    (14, 'TEST_MEDIA_FIX_closed.jpg', '/uploads/TEST_MEDIA_FIX_closed.jpg', '已关单检查项', 'inspection', 201, 1, 2, '执行人', '现场照片', 1, 'approved', 3, '2026-08-11 09:03:00', 0, '', 'TEST_MEDIA_FIX_站点362 · 已关单检查项 · 现场照片'),
                    (15, 'TEST_MEDIA_FIX_cross.jpg', '/uploads/TEST_MEDIA_FIX_cross.jpg', '跨站', 'inspection', 201, 2, 2, '执行人', '现场照片', 1, 'approved', 3, '2026-08-11 09:04:00', 0, '', 'TEST_MEDIA_FIX_站点999 · 已关单检查项 · 现场照片')
                ])
            db.execute("""UPDATE operation_attachments SET plan_id=100, item_id=101,
                item_name='室内定位测试站-温度' WHERE id IN (10,11)""")
            db.execute("""UPDATE operation_attachments SET plan_id=100, item_id=102,
                item_name='室内定位测试站-流量' WHERE id IN (12,13)""")
            db.execute("""UPDATE operation_attachments SET plan_id=200, item_id=201,
                item_name='已关单检查项' WHERE id IN (14,15)""")
            db.execute('CREATE UNIQUE INDEX uq_test_notification_dedupe ON notifications(user_id, dedupe_key) WHERE is_read=0 AND dedupe_key != ""')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def db_value(self, sql, params=()):
        with app_module.get_db() as db:
            return db.execute(sql, params).fetchone()

    def test_void_permission_reason_scope_and_status_guards(self):
        self.assertEqual(self.client.post('/api/attachments/10/void', headers=self.headers('operator-token'), json={'reason': 'TEST_MEDIA_FIX_误传'}).status_code, 403)
        self.assertEqual(self.client.post('/api/attachments/10/void', headers=self.headers('reviewer-token'), json={'reason': '  '}).status_code, 400)
        cross = self.client.post('/api/attachments/15/void', headers=self.headers('reviewer-token'), json={'reason': 'TEST_MEDIA_FIX_跨站'});
        self.assertEqual(cross.status_code, 403, cross.json)
        for aid, status in ((12, 409), (13, 409), (14, 409)):
            response = self.client.post(f'/api/attachments/{aid}/void', headers=self.headers('admin-token'), json={'reason': 'TEST_MEDIA_FIX_限制'});
            self.assertEqual(response.status_code, status, response.json)
        row = self.db_value('SELECT review_status, voided_at FROM operation_attachments WHERE id=10')
        self.assertEqual((row['review_status'], row['voided_at']), ('approved', None))

    def test_void_preserves_authoritative_link_reopens_only_item_and_is_idempotent(self):
        with app_module.get_db() as db:
            db.execute("""
                INSERT INTO operation_attachments
                (id, filename, stored_path, source_type, source_id, site_id, uploader_id,
                 uploader_name, review_required, review_status)
                VALUES (16, 'TEST_MEDIA_FIX_legacy.jpg', '/uploads/TEST_MEDIA_FIX_legacy.jpg',
                        'site_photo', 0, 1, 2, '执行人', 0, 'approved')
            """)
        response = self.client.post('/api/attachments/10/void', headers=self.headers('reviewer-token'), json={'reason': 'TEST_MEDIA_FIX_错误检查项照片'})
        self.assertEqual(response.status_code, 200, response.json)
        repeated = self.client.post('/api/attachments/10/void', headers=self.headers('reviewer-token'), json={'reason': 'TEST_MEDIA_FIX_重复请求'})
        self.assertEqual(repeated.status_code, 200, repeated.json)
        self.assertTrue(repeated.json['idempotent'])
        att = self.db_value('SELECT source_type, source_id, plan_id, item_id, item_name, site_id, uploader_id, review_status, void_reason FROM operation_attachments WHERE id=10')
        self.assertEqual((att['source_type'], att['source_id'], att['plan_id'], att['item_id'], att['item_name'], att['site_id'], att['uploader_id']), ('inspection', 101, 100, 101, '室内定位测试站-温度', 1, 2))
        self.assertEqual((att['review_status'], att['void_reason']), ('voided', 'TEST_MEDIA_FIX_错误检查项照片'))
        item = self.db_value('SELECT plan_id, site_id, item_name, evidence_status, supplement_source_attachment_id, actual_photos, photo_urls, review_status FROM insp_plan_items WHERE id=101')
        self.assertEqual((item['plan_id'], item['site_id'], item['item_name']), (100, 1, '室内定位测试站-温度'))
        self.assertEqual((item['evidence_status'], item['supplement_source_attachment_id'], item['actual_photos'], item['review_status']), ('supplement_required', 10, 1, 2))
        self.assertEqual(json.loads(item['photo_urls']), ['/uploads/TEST_MEDIA_FIX_other.jpg'])
        audit = self.db_value('SELECT original_plan_id, original_item_id, original_item_name, original_reviewer_id, original_risk_snapshot, reason FROM attachment_void_audits WHERE attachment_id=10')
        self.assertEqual((audit['original_plan_id'], audit['original_item_id'], audit['original_item_name'], audit['original_reviewer_id']), (100, 101, '室内定位测试站-温度', 3))
        self.assertIn('GPS风险', audit['original_risk_snapshot'])
        self.assertEqual(audit['reason'], 'TEST_MEDIA_FIX_错误检查项照片')
        self.assertEqual(self.db_value("SELECT COUNT(*) AS c FROM attachment_void_audits WHERE attachment_id=10")['c'], 1)
        self.assertEqual(self.db_value("SELECT COUNT(*) AS c FROM operation_logs WHERE action='void' AND target_id=10")['c'], 1)
        self.assertEqual(self.db_value("SELECT COUNT(*) AS c FROM notifications WHERE source_type='attachment_void' AND source_id=10")['c'], 1)
        notification = self.db_value("SELECT content, payload_json FROM notifications WHERE source_type='attachment_void' AND source_id=10")
        payload = json.loads(notification['payload_json'])
        self.assertEqual((payload['plan_id'], payload['item_id'], payload['site_id']), (100, 101, 1))
        self.assertNotIn('。。', notification['content'])

        listing = self.client.get('/api/attachments', headers=self.headers('admin-token'))
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertNotIn(10, [row['id'] for row in listing.json['items']])
        legacy = next(row for row in listing.json['items'] if row['id'] == 16)
        self.assertEqual((legacy['item_name'], legacy['association_status']), ('检查项待确认', 'unlinked'))
        history = self.client.get('/api/attachments?include_voided=1&review_status=voided', headers=self.headers('admin-token'))
        self.assertEqual(history.status_code, 200, history.json)
        historical = next(row for row in history.json['items'] if row['id'] == 10)
        self.assertEqual((historical['item_id'], historical['item_name'], historical['review_status'], historical['risk_label']), (101, '室内定位测试站-温度', 'voided', ''))
        self.assertEqual(self.client.get('/api/attachments/10', headers=self.headers('admin-token')).json['item_id'], 101)
        stats = self.client.get('/api/attachments/stats', headers=self.headers('admin-token')).json
        self.assertNotIn('voided', stats['by_source'])

    def test_failed_notification_rolls_back_everything(self):
        original = app_module._upsert_unread_notification

        def fail_notification(*args, **kwargs):
            raise RuntimeError('TEST_MEDIA_FIX_notification_failure')

        app_module._upsert_unread_notification = fail_notification
        try:
            response = self.client.post('/api/attachments/10/void', headers=self.headers('reviewer-token'), json={'reason': 'TEST_MEDIA_FIX_事务失败'})
        finally:
            app_module._upsert_unread_notification = original
        self.assertEqual(response.status_code, 500)
        att = self.db_value('SELECT review_status, voided_at FROM operation_attachments WHERE id=10')
        item = self.db_value('SELECT evidence_status, supplement_source_attachment_id FROM insp_plan_items WHERE id=101')
        self.assertEqual((att['review_status'], att['voided_at']), ('approved', None))
        self.assertEqual((item['evidence_status'], item['supplement_source_attachment_id']), ('', None))
        self.assertEqual(self.db_value('SELECT COUNT(*) AS c FROM attachment_void_audits')['c'], 0)

    def test_void_notification_does_not_duplicate_terminal_reason_punctuation(self):
        response = self.client.post('/api/attachments/10/void', headers=self.headers('reviewer-token'),
                                    json={'reason': 'TEST_MEDIA_FIX_reason.'})
        self.assertEqual(response.status_code, 200, response.json)
        notification = self.db_value("SELECT content, payload_json FROM notifications WHERE source_type='attachment_void' AND source_id=10")
        self.assertIn('TEST_MEDIA_FIX_reason.', notification['content'])
        self.assertNotIn('TEST_MEDIA_FIX_reason..', notification['content'])
        self.assertEqual(json.loads(notification['payload_json'])['item_id'], 101)

    def test_approved_replacement_closes_only_target_supplement(self):
        self.assertEqual(self.client.post('/api/attachments/10/void', headers=self.headers('reviewer-token'), json={'reason': 'TEST_MEDIA_FIX_需要替代证据'}).status_code, 200)
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET evidence_status='replacement_submitted', review_status=1,
                    photo_urls='["/uploads/TEST_MEDIA_FIX_other.jpg","/uploads/TEST_MEDIA_FIX_replacement.jpg"]'
                WHERE id=101""")
            db.execute('''
                INSERT INTO operation_attachments
                (id, filename, stored_path, description, source_type, source_id, site_id,
                 uploader_id, uploader_name, category, review_required, review_status)
                VALUES (20, 'TEST_MEDIA_FIX_replacement.jpg', '/uploads/TEST_MEDIA_FIX_replacement.jpg',
                        '温度', 'inspection', 101, 1, 2, '执行人', '现场照片', 1, 'pending')
            ''')
        response = self.client.post('/api/operation-attachments/review', headers=self.headers('reviewer-token'), json={
            'approve_ids': [20],
        })
        self.assertEqual(response.status_code, 200, response.json)
        item = self.db_value('SELECT evidence_status, review_status, actual_photos FROM insp_plan_items WHERE id=101')
        self.assertEqual((item['evidence_status'], item['review_status'], item['actual_photos']), ('effective', 2, 2))
        self.assertEqual(self.db_value('SELECT review_status FROM operation_attachments WHERE id=10')['review_status'], 'voided')

    def test_rejected_replacement_keeps_item_out_of_reviewer_state(self):
        self.assertEqual(self.client.post('/api/attachments/10/void', headers=self.headers('reviewer-token'), json={
            'reason': 'TEST_MEDIA_FIX_需要替代证据'}).status_code, 200)
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET evidence_status='replacement_submitted', review_status=1,
                    photo_urls='["/uploads/TEST_MEDIA_FIX_other.jpg","/uploads/TEST_MEDIA_FIX_replacement.jpg"]'
                WHERE id=101""")
            db.execute('''
                INSERT INTO operation_attachments
                (id, filename, stored_path, description, source_type, source_id, site_id,
                 uploader_id, uploader_name, category, review_required, review_status)
                VALUES (20, 'TEST_MEDIA_FIX_replacement.jpg', '/uploads/TEST_MEDIA_FIX_replacement.jpg',
                        '温度', 'inspection', 101, 1, 2, '执行人', '现场照片', 1, 'pending')
            ''')
        response = self.client.post('/api/operation-attachments/review', headers=self.headers('reviewer-token'), json={
            'reject_ids': [20], 'approve_ids': [], 'reject_reason': 'TEST_MEDIA_FIX_仍需重拍',
        })
        self.assertEqual(response.status_code, 200, response.json)
        item = self.db_value('SELECT evidence_status, review_status FROM insp_plan_items WHERE id=101')
        self.assertEqual((item['evidence_status'], item['review_status']), ('supplement_required', 3))


if __name__ == '__main__':
    unittest.main()
