import json
import io
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
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, username TEXT, role TEXT, openid TEXT DEFAULT '');
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
                    resolved_at TEXT, check_in_time TEXT, remark TEXT, review_submitted_at TEXT
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
                    reviewer_id INTEGER, reviewed_at TEXT, reject_reason TEXT,
                    capture_source TEXT, sha256_hash TEXT DEFAULT '', duplicate_of_id INTEGER,
                    perceptual_hash TEXT DEFAULT '', evidence_qualification TEXT DEFAULT 'review',
                    evidence_basis TEXT DEFAULT 'unknown', evidence_reason TEXT DEFAULT '',
                    evidence_next_action TEXT DEFAULT '', evidence_evaluated_at TEXT,
                    evidence_evaluation_id INTEGER
                );
                CREATE TABLE timeline_events (source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT);
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id TEXT, title TEXT, content TEXT, is_read INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE hotline_events (related_order_no TEXT, status TEXT);
                CREATE TABLE manual_reports (id INTEGER PRIMARY KEY, order_no TEXT, status TEXT, resolved_at TEXT);
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, status TEXT, resolved_at TEXT, resolve_reason TEXT, site_id INTEGER, metric TEXT);
                CREATE TABLE data_reviews (id INTEGER PRIMARY KEY, status TEXT, site_id INTEGER, metric TEXT);
                CREATE TABLE spare_parts_inventory (id INTEGER PRIMARY KEY, quantity REAL, updated_at TEXT);
                CREATE TABLE inventory_logs (part_id INTEGER, type TEXT, quantity REAL, ref_type TEXT, ref_id INTEGER, operator TEXT, remark TEXT);
                INSERT INTO users (id, real_name, username, role) VALUES (1, '管理员', 'admin', 'admin');
                INSERT INTO users (id, real_name, username, role) VALUES (2, '现场运维', 'operator', 'operator');
                INSERT INTO users (id, real_name, username, role) VALUES (3, '运维主管', 'manager', 'manager');
                INSERT INTO users (id, real_name, username, role) VALUES (4, '其他运维', 'other', 'operator');
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO sites VALUES (1, '测试站', 28.68, 115.73);
                INSERT INTO work_orders
                    (id, order_no, site_id, title, description, event_type, level, status,
                     images, assignee, created_at)
                VALUES (1, 'WO-TEST-001', 1, '处置测试', '测试描述', '', 'normal',
                        'in_progress', '[]', '现场运维', datetime('now'));
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

    def seed_workorder_review_notifications(self, with_evidence=False):
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='reviewing' WHERE order_no='WO-TEST-001'")
            if with_evidence:
                db.execute("""INSERT INTO operation_attachments
                    (filename,stored_path,file_type,source_type,source_id,site_id,uploader_id,
                     taken_at,review_status,evidence_qualification,evidence_basis)
                    VALUES ('review.jpg','/uploads/review.jpg','image','workorder',1,1,2,
                            datetime('now','localtime'),'pending','qualified','camera_session')""")
            db.executemany("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content)
                VALUES (?,'workorder_review','WO-TEST-001','待核验','现场处置已提交')""",
                [(1,), (3,)])

    def assert_other_reviewer_has_history_only(self):
        current = self.client.get('/api/notifications?status=unread',
                                  headers=self.headers('manager-token'))
        history = self.client.get('/api/notifications?status=read',
                                  headers=self.headers('manager-token'))
        self.assertEqual(current.status_code, 200, current.json)
        self.assertEqual(history.status_code, 200, history.json)
        self.assertFalse(any(item['source_type'] == 'workorder_review'
                             for item in current.json['notifications']))
        archived = [item for item in history.json['notifications']
                    if item['source_type'] == 'workorder_review']
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]['source_id'], 'WO-TEST-001')
        self.assertEqual(archived[0]['is_read'], 1)

    def test_approve_archives_review_notifications_for_every_reviewer(self):
        self.seed_workorder_review_notifications(with_evidence=True)
        response = self.client.post('/api/workorders/WO-TEST-001/approve',
                                    headers=self.headers('admin-token'), json={})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['status'], 'closed')
        self.assert_other_reviewer_has_history_only()

    def test_reject_archives_review_notifications_for_every_reviewer(self):
        self.seed_workorder_review_notifications()
        response = self.client.post('/api/workorders/WO-TEST-001/reject',
                                    headers=self.headers('admin-token'),
                                    json={'reason': '请补充仪表读数'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['status'], 'in_progress')
        self.assert_other_reviewer_has_history_only()

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
            self.assertTrue(all(row['taken_at'] is None for row in db.execute(
                "SELECT taken_at FROM operation_attachments WHERE is_deleted=0"
            )))

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
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET check_in_time=datetime('now','localtime') WHERE order_no='WO-TEST-001'")
            db.execute("""UPDATE operation_attachments
                SET evidence_qualification='qualified',evidence_basis='camera_session',
                    taken_at=datetime('now','localtime'),is_flagged=0,flag_reason=''
                WHERE source_type='workorder' AND source_id=1 AND is_deleted=0""")

        # 首次提交、退回、补充后再次提交并办结，工单不会因影像审核而丢失。
        submitted = self.client.post('/api/workorders/WO-TEST-001/submit-review', headers=self.headers('operator-token'), json={'client': 'mobile', 'resolution_note': '已检查并完成现场处置'})
        self.assertEqual(submitted.status_code, 200, submitted.json)
        rejected = self.client.post('/api/workorders/WO-TEST-001/reject', headers=self.headers('admin-token'), json={'reason': '请补拍仪表读数'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        self.assertEqual(rejected.json['status'], 'in_progress')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (filename,stored_path,file_type,source_type,source_id,site_id,uploader_id,
                 taken_at,review_status,evidence_qualification,evidence_basis)
                VALUES ('replacement.jpg','/uploads/replacement.jpg','image','workorder',1,1,2,
                        datetime('now','localtime'),'pending','qualified','camera_session')""")
        resubmitted = self.client.post('/api/workorders/WO-TEST-001/submit-review', headers=self.headers('operator-token'), json={'client': 'mobile', 'resolution_note': '补拍仪表读数后再次提交'})
        self.assertEqual(resubmitted.status_code, 200, resubmitted.json)
        approved = self.client.post('/api/workorders/WO-TEST-001/approve',
                                    headers=self.headers('manager-token'), json={})
        self.assertEqual(approved.status_code, 200, approved.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute("SELECT status FROM work_orders WHERE id=1").fetchone()['status'], 'closed')
            statuses = [r['review_status'] for r in db.execute("SELECT review_status FROM operation_attachments WHERE is_deleted=0")]
            self.assertEqual(statuses, ['rejected', 'rejected', 'approved'])

    def test_status_endpoint_cannot_bypass_review_for_closure(self):
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='reviewing' WHERE order_no='WO-TEST-001'")
        closed = self.client.put('/api/workorders/WO-TEST-001/status',
                                 headers=self.headers('manager-token'), json={'status': 'closed'})
        self.assertEqual(closed.status_code, 400, closed.json)
        self.assertIn('核验', closed.json['error'])

    def test_workorder_delete_is_retired_for_all_authenticated_roles_and_preserves_audit(self):
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='closed' WHERE order_no='WO-TEST-001'")
            db.execute("INSERT INTO timeline_events VALUES ('workorder','WO-TEST-001','closed','Admin','closed')")

        anonymous = self.client.delete('/api/workorders/WO-TEST-001')
        self.assertEqual(anonymous.status_code, 401, anonymous.json)
        denied = self.client.delete('/api/workorders/WO-TEST-001', headers=self.headers('other-operator-token'))
        self.assertEqual((denied.status_code, denied.json.get('code')), (409, 'WORKORDER_DELETE_RETIRED'))
        admin_denied = self.client.delete('/api/workorders/WO-TEST-001', headers=self.headers('admin-token'))
        self.assertEqual((admin_denied.status_code, admin_denied.json.get('code')), (409, 'WORKORDER_DELETE_RETIRED'))
        with app_module.get_db() as db:
            self.assertEqual(db.execute("SELECT status FROM work_orders WHERE order_no='WO-TEST-001'").fetchone()['status'], 'closed')
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM timeline_events WHERE source_id='WO-TEST-001'").fetchone()['c'], 1)

    def test_reused_web_upload_is_flagged_and_keeps_capture_time_unknown(self):
        encoded = base64.b64encode(b'same-image-bytes' * 32).decode('ascii')
        first = self.client.post('/api/workorders/WO-TEST-001/photos',
                                 headers=self.headers('operator-token'), json={'image': encoded})
        second = self.client.post('/api/workorders/WO-TEST-001/photos',
                                  headers=self.headers('operator-token'), json={'image': encoded})
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        with app_module.get_db() as db:
            reused = db.execute(
                "SELECT taken_at, duplicate_of_id, is_flagged, flag_reason FROM operation_attachments WHERE id=?",
                (second.json['id'],),
            ).fetchone()
            self.assertIsNone(reused['taken_at'])
            self.assertEqual(reused['duplicate_of_id'], first.json['id'])
            self.assertEqual(reused['is_flagged'], 1)
            self.assertIn('重复', reused['flag_reason'])

    def test_web_upload_uses_workorder_site_for_base64_and_multipart(self):
        base64_response = self.client.post(
            '/api/workorders/WO-TEST-001/photos',
            headers=self.headers('operator-token'),
            json={
                'image': base64.b64encode(b'base64-site-spoof' * 32).decode('ascii'),
                'site_id': 999,
            },
        )
        self.assertEqual(base64_response.status_code, 200, base64_response.json)

        multipart_response = self.client.post(
            '/api/workorders/WO-TEST-001/photos',
            headers=self.headers('operator-token'),
            data={
                'file': (io.BytesIO(b'multipart-site-spoof' * 32), 'evidence.jpg'),
                'site_id': '999',
            },
            content_type='multipart/form-data',
        )
        self.assertEqual(multipart_response.status_code, 200, multipart_response.json)

        with app_module.get_db() as db:
            rows = db.execute(
                """SELECT site_id, uploader_id, uploader_name FROM operation_attachments
                   WHERE source_type='workorder' AND source_id=1 ORDER BY id DESC LIMIT 2"""
            ).fetchall()
        self.assertEqual([row['site_id'] for row in rows], [1, 1])
        self.assertEqual([row['uploader_id'] for row in rows], [2, 2])
        self.assertTrue(all(row['uploader_name'] == '现场运维' for row in rows))

    def test_status_endpoint_cannot_bypass_review_submission(self):
        submitted = self.client.put('/api/workorders/WO-TEST-001/status',
                                    headers=self.headers('operator-token'), json={'status': 'reviewing'})
        self.assertEqual(submitted.status_code, 400, submitted.json)
        self.assertIn('提交审核', submitted.json['error'])

    def test_review_cannot_be_submitted_or_approved_without_evidence(self):
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET check_in_time=datetime('now','localtime') WHERE order_no='WO-TEST-001'")
        submitted = self.client.post('/api/workorders/WO-TEST-001/submit-review',
                                     headers=self.headers('operator-token'), json={'client': 'web', 'resolution_note': '现场处置完成'})
        self.assertEqual(submitted.status_code, 409, submitted.json)
        self.assertEqual(submitted.json['code'], 'QUALIFIED_EVIDENCE_REQUIRED')

        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='reviewing' WHERE order_no='WO-TEST-001'")
        approved = self.client.post('/api/workorders/WO-TEST-001/approve',
                                    headers=self.headers('manager-token'), json={})
        self.assertEqual(approved.status_code, 409, approved.json)
        self.assertEqual(approved.json['code'], 'QUALIFIED_EVIDENCE_REQUIRED')

    def test_operator_cannot_change_another_assignees_workorder(self):
        response = self.client.put('/api/workorders/WO-TEST-001/status',
                                   headers=self.headers('other-operator-token'),
                                   json={'status': 'accepted'})
        self.assertEqual(response.status_code, 403, response.json)

    def test_workorder_evidence_lifecycle_freezes_after_close_and_allows_review_supplement(self):
        encoded = base64.b64encode(b'new-evidence' * 32).decode('ascii')
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='reviewing' WHERE order_no='WO-TEST-001'")
            db.commit()
        supplemental = self.client.post('/api/workorders/WO-TEST-001/photos',
                                        headers=self.headers('operator-token'), json={'image': encoded})
        self.assertEqual(supplemental.status_code, 200, supplemental.json)
        locked_delete = self.client.post('/api/workorders/WO-TEST-001/photos',
                                         headers=self.headers('operator-token'),
                                         json={'delete_url': supplemental.json['url']})
        self.assertEqual(locked_delete.status_code, 409, locked_delete.json)
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='closed' WHERE order_no='WO-TEST-001'")
            db.commit()
        frozen = self.client.post('/api/workorders/WO-TEST-001/photos',
                                  headers=self.headers('operator-token'), json={'image': encoded})
        self.assertEqual(frozen.status_code, 409, frozen.json)
        self.assertEqual(frozen.json['code'], 'WORKORDER_CLOSED')


if __name__ == '__main__':
    unittest.main()
