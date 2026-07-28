import json
import os
import sqlite3
import sys
import tempfile
import unittest
import base64
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class WorkorderEvidenceFlowTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_cache = dict(app_module._site_ids_cache)

        @contextmanager
        def temporary_db():
            db = sqlite3.connect(self.db_path, timeout=5)
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
            'admin-token': {'id': 1, 'role': 'admin', 'real_name': '管理员'},
            'manager-token': {'id': 3, 'role': 'manager', 'real_name': '运维主管'},
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': '现场运维'},
            'other-operator-token': {'id': 4, 'role': 'operator', 'real_name': '其他运维'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, username TEXT, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, gps_lat REAL, gps_lng REAL);
                CREATE TABLE photo_requirements (
                    id INTEGER PRIMARY KEY, site_type TEXT, period TEXT, item_name TEXT,
                    category TEXT, watermark_keyword TEXT, review_required INTEGER, seq INTEGER
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, site_id INTEGER, title TEXT,
                    description TEXT, event_type TEXT, level TEXT, status TEXT, images TEXT,
                    assignee TEXT, created_at TEXT, related_alert_id INTEGER, used_parts TEXT,
                    resolved_at TEXT, check_in_time TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, stored_path TEXT,
                    file_type TEXT, mime_type TEXT, file_size INTEGER, description TEXT,
                    source_type TEXT, source_id INTEGER, site_id INTEGER, uploader_id INTEGER,
                    uploader_name TEXT, gps_lat REAL, gps_lng REAL, taken_at TEXT, category TEXT,
                    watermark_text TEXT, recognized_category TEXT, match_status TEXT,
                    match_confidence REAL, review_required INTEGER, requirement_id INTEGER,
                    is_deleted INTEGER DEFAULT 0, is_flagged INTEGER DEFAULT 0,
                    flag_reason TEXT DEFAULT '', flag_rule TEXT DEFAULT '', review_status TEXT,
                    reviewer_id INTEGER, reviewed_at TEXT, reject_reason TEXT
                );
                CREATE TABLE timeline_events (source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT);
                CREATE TABLE notifications (user_id INTEGER, source_type TEXT, source_id TEXT, title TEXT, content TEXT);
                CREATE TABLE hotline_events (related_order_no TEXT, status TEXT);
                CREATE TABLE manual_reports (id INTEGER PRIMARY KEY, order_no TEXT, status TEXT, resolved_at TEXT);
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, status TEXT, resolved_at TEXT, resolve_reason TEXT, site_id INTEGER, metric TEXT);
                CREATE TABLE data_reviews (id INTEGER PRIMARY KEY, status TEXT, site_id INTEGER, metric TEXT);
                CREATE TABLE spare_parts_inventory (id INTEGER PRIMARY KEY, quantity REAL, updated_at TEXT);
                CREATE TABLE inventory_logs (part_id INTEGER, type TEXT, quantity REAL, ref_type TEXT, ref_id INTEGER, operator TEXT, remark TEXT);
                INSERT INTO users VALUES (1, '管理员', 'admin', 'admin');
                INSERT INTO users VALUES (2, '现场运维', 'operator', 'operator');
                INSERT INTO users VALUES (3, '运维主管', 'manager', 'manager');
                INSERT INTO users VALUES (4, '其他运维', 'other', 'operator');
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO sites VALUES (1, '测试站', 28.68, 115.73);
                INSERT INTO work_orders VALUES (1, 'WO-TEST-001', 1, '处置测试', '测试描述', '', 'normal', 'in_progress', '[]', '现场运维', datetime('now'), NULL, NULL, NULL, NULL);
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

    def test_evidence_append_delete_reject_resubmit_and_approve(self):
        # 同一秒内连续上传的三张影像必须保留三条附件和三条 images 缓存记录。
        with app_module.app.test_request_context('/', headers=self.headers('operator-token')):
            from flask import g
            g.current_user = app_module._tokens['operator-token']
            for suffix in ('a', 'b', 'c'):
                result = app_module._batch_link_wo_photos('WO-TEST-001', [f'/uploads/workorder_photos/{suffix}.jpg'])
                self.assertEqual(result['count'], 1)
        with app_module.get_db() as db:
            image_urls = json.loads(db.execute("SELECT images FROM work_orders WHERE id=1").fetchone()['images'])
            self.assertEqual(len(image_urls), 3)
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM operation_attachments WHERE is_deleted=0").fetchone()['c'], 3)

        # 删除仅删除指定照片，其他证据仍保留。
        response = self.client.post('/api/mobile/workorder/WO-TEST-001/image/delete', headers=self.headers('operator-token'), json={'url': image_urls[1]})
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM operation_attachments WHERE is_deleted=0").fetchone()['c'], 2)

        # 网页/API 兼容的 base64 分支也必须能上传并同步 images 缓存。
        base64_upload = self.client.post('/api/workorders/WO-TEST-001/photos', headers=self.headers('operator-token'), json={
            'image': base64.b64encode(b'x' * 256).decode('ascii'),
        })
        self.assertEqual(base64_upload.status_code, 200, base64_upload.json)
        self.assertEqual(len(base64_upload.json['images']), 3)
        cleanup_upload = self.client.post('/api/mobile/workorder/WO-TEST-001/image/delete', headers=self.headers('operator-token'), json={
            'url': base64_upload.json['url'],
        })
        self.assertEqual(cleanup_upload.status_code, 200, cleanup_upload.json)

        # 首次提交、退回、补充后再次提交并办结，工单不会因影像审核而丢失。
        submitted = self.client.post('/api/workorders/WO-TEST-001/submit-review', headers=self.headers('operator-token'), json={'client': 'mobile'})
        self.assertEqual(submitted.status_code, 200, submitted.json)
        rejected = self.client.post('/api/workorders/WO-TEST-001/reject', headers=self.headers('admin-token'), json={'reason': '请补拍仪表读数'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        self.assertEqual(rejected.json['status'], 'in_progress')
        resubmitted = self.client.post('/api/workorders/WO-TEST-001/submit-review', headers=self.headers('operator-token'), json={'client': 'mobile'})
        self.assertEqual(resubmitted.status_code, 200, resubmitted.json)
        approved = self.client.post('/api/workorders/WO-TEST-001/approve', headers=self.headers('manager-token'), json={})
        self.assertEqual(approved.status_code, 200, approved.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute("SELECT status FROM work_orders WHERE id=1").fetchone()['status'], 'closed')
            statuses = [r['review_status'] for r in db.execute("SELECT review_status FROM operation_attachments WHERE is_deleted=0")]
            self.assertEqual(statuses, ['approved', 'approved'])

    def test_manager_can_close_reviewing_workorder_through_status_endpoint(self):
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='reviewing' WHERE order_no='WO-TEST-001'")
        closed = self.client.put('/api/workorders/WO-TEST-001/status',
                                 headers=self.headers('manager-token'), json={'status': 'closed'})
        self.assertEqual(closed.status_code, 200, closed.json)
        self.assertEqual(closed.json['status'], 'closed')

    def test_operator_cannot_change_another_assignees_workorder(self):
        response = self.client.put('/api/workorders/WO-TEST-001/status',
                                   headers=self.headers('other-operator-token'),
                                   json={'status': 'accepted'})
        self.assertEqual(response.status_code, 403, response.json)


if __name__ == '__main__':
    unittest.main()
