import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager


sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class MobileExecutionPhotosTest(unittest.TestCase):
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
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'operator-token': {'id': 2, 'role': 'operator', 'roles': ['operator']},
            'reviewer-token': {'id': 3, 'role': 'reviewer', 'roles': ['reviewer']},
            'admin-token': {'id': 4, 'role': 'admin', 'roles': ['admin']},
            'dual-reviewer-token': {'id': 5, 'role': 'admin', 'roles': ['admin', 'reviewer']},
            'other-operator-token': {'id': 6, 'role': 'operator', 'roles': ['operator']},
            'unscoped-reviewer-token': {'id': 7, 'role': 'reviewer', 'roles': ['reviewer']},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, assignee_id INTEGER, status TEXT,
                    generate_date TEXT, plan_schedule_id INTEGER
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER,
                    item_name TEXT, category TEXT, execution_status TEXT,
                    rework_required_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, filename TEXT, stored_path TEXT,
                    source_type TEXT, source_id INTEGER, plan_id INTEGER, item_id INTEGER,
                    item_name TEXT, site_id INTEGER, uploader_id INTEGER, uploader_name TEXT,
                    taken_at TEXT, created_at TEXT, capture_source TEXT,
                    evidence_qualification TEXT, evidence_reason TEXT,
                    evidence_next_action TEXT, review_status TEXT, reject_reason TEXT,
                    is_deleted INTEGER DEFAULT 0, voided_at TEXT,
                    is_flagged INTEGER DEFAULT 0, flag_reason TEXT DEFAULT '',
                    extra_json TEXT DEFAULT '{}', category TEXT DEFAULT ''
                );
                INSERT INTO user_sites VALUES (2,10),(3,10),(5,10),(6,11);
                INSERT INTO sites VALUES (10,'城北水质站'),(11,'城南水质站');
                INSERT INTO insp_plans VALUES (100,2,'completed','2026-09-29',NULL);
                INSERT INTO insp_plan_items VALUES
                    (1000,100,10,'检查采水泵运行状态','设备巡检','active',''),
                    (1001,100,10,'检查仪表','设备巡检','active','2026-09-29 10:00:00'),
                    (1002,100,11,'跨站检查项','设备巡检','active',''),
                    (1003,100,10,'已取消历史项','设备巡检','cancelled','');
            ''')
            rows = [
                (1, 'pending.jpg', '/uploads/site_photos/pending.jpg', 'site_photo', 0, None, None,
                 '', 10, 2, '现场运维员', '2026-09-29 11:00:00', '2026-09-29 11:01:00',
                 'watermark_album', 'qualified', '时间和位置校验通过', '', 'pending', '', 0, None, 0, '',
                 {'plan_id': 100, 'item_id': 1000, 'material_role': 'pending_inspection'}, '设备巡检'),
                (2, 'approved.jpg', '/uploads/site_photos/approved.jpg', 'inspection', 1000, 100, 1000,
                 '检查采水泵运行状态', 10, 2, '现场运维员', '2026-09-29 12:00:00', '2026-09-29 12:01:00',
                 'camera', 'qualified', '现场拍摄校验通过', '', 'approved', '', 0, None, 0, '',
                 {'plan_id': 100, 'item_id': 1000, 'material_role': 'formal'}, '设备巡检'),
                (3, 'supplement.jpg', '/uploads/site_photos/supplement.jpg', 'site_photo', 0, None, None,
                 '', 10, 2, '现场运维员', None, '2026-09-29 10:30:00',
                 'watermark_album', 'review', '位置无法确认', '请现场重拍', 'pending', '', 0, None, 0, '',
                 {'plan_id': 100, 'item_id': 1000, 'material_role': 'supplement'}, '设备巡检'),
                (4, 'voided.jpg', '/uploads/site_photos/voided.jpg', 'inspection', 1000, 100, 1000,
                 '检查采水泵运行状态', 10, 2, '现场运维员', '2026-09-29 09:00:00', '2026-09-29 09:01:00',
                 'camera', 'qualified', '', '', 'voided', '', 0, '2026-09-29 13:00:00', 0, '',
                 {'plan_id': 100, 'item_id': 1000, 'material_role': 'formal'}, '设备巡检'),
                (5, 'deleted.jpg', '/uploads/site_photos/deleted.jpg', 'inspection', 1000, 100, 1000,
                 '检查采水泵运行状态', 10, 2, '现场运维员', '2026-09-29 08:00:00', '2026-09-29 08:01:00',
                 'camera', 'qualified', '', '', 'approved', '', 1, None, 0, '',
                 {'plan_id': 100, 'item_id': 1000, 'material_role': 'formal'}, '设备巡检'),
                (6, 'old-rework.jpg', '/uploads/site_photos/old-rework.jpg', 'inspection', 1001, 100, 1001,
                 '检查仪表', 10, 2, '现场运维员', '2026-09-29 09:00:00', '2026-09-29 09:01:00',
                 'watermark_album', 'qualified', '', '', 'rejected', '旧周期照片', 0, None, 0, '',
                 {'plan_id': 100, 'item_id': 1001, 'material_role': 'formal'}, '设备巡检'),
                (7, 'current-rework.jpg', '/uploads/site_photos/current-rework.jpg', 'inspection', 1001, 100, 1001,
                 '检查仪表', 10, 2, '现场运维员', '2026-09-29 10:30:00', '2026-09-29 10:31:00',
                 'watermark_album', 'qualified', '', '', 'rejected', '请重新拍摄', 0, None, 1, '位置风险',
                 {'plan_id': 100, 'item_id': 1001, 'material_role': 'formal'}, '设备巡检'),
                (8, 'cancelled-history.jpg', '/uploads/site_photos/cancelled-history.jpg', 'inspection', 1003, 100, 1003,
                 '已取消历史项', 10, 2, '现场运维员', '2026-09-29 07:00:00', '2026-09-29 07:01:00',
                 'camera', 'qualified', '', '', 'pending', '', 0, None, 0, '',
                 {'plan_id': 100, 'item_id': 1003, 'material_role': 'formal'}, '设备巡检'),
                (9, 'workorder.jpg', '/uploads/workorders/workorder.jpg', 'workorder', 900, 100, 1000,
                 '其他业务附件', 10, 2, '现场运维员', '2026-09-29 13:00:00', '2026-09-29 13:01:00',
                 'camera', 'qualified', '', '', 'approved', '', 0, None, 0, '',
                 {'plan_id': 100, 'item_id': 1000, 'material_role': 'formal'}, '工单附件'),
            ]
            db.executemany('''INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,plan_id,item_id,item_name,site_id,
                 uploader_id,uploader_name,taken_at,created_at,capture_source,evidence_qualification,
                 evidence_reason,evidence_next_action,review_status,reject_reason,is_deleted,voided_at,
                 is_flagged,flag_reason,extra_json,category)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [
                    tuple(list(row[:-2]) + [json.dumps(row[-2], ensure_ascii=False), row[-1]])
                    for row in rows
                ])
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
        return {'Authorization': 'Bearer ' + token}

    def get_photos(self, token='operator-token', query=''):
        return self.client.get(
            '/api/mobile/execution-plans/100/sites/10/photos' + query,
            headers=self.headers(token),
        )

    def test_operator_reads_current_photos_with_server_capabilities(self):
        response = self.get_photos(query='?item_id=1000')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item['id'] for item in response.json['items']], [2, 1, 3])
        self.assertEqual(response.json['pagination']['total'], 3)
        pending = next(item for item in response.json['items'] if item['id'] == 1)
        supplement = next(item for item in response.json['items'] if item['id'] == 3)
        self.assertTrue(pending['capabilities']['can_delete_pending'])
        self.assertFalse(pending['capabilities']['can_review'])
        self.assertEqual(supplement['material_role'], 'supplement')
        self.assertFalse(supplement['is_effective_evidence'])

    def test_reviewer_scope_and_pure_admin_role_are_enforced(self):
        reviewer = self.get_photos('reviewer-token', '?review_status=pending&material_role=formal')
        dual_reviewer = self.get_photos('dual-reviewer-token', '?review_status=pending&material_role=formal')
        admin = self.get_photos('admin-token')
        unscoped = self.get_photos('unscoped-reviewer-token')
        other_operator = self.get_photos('other-operator-token')
        self.assertEqual(reviewer.status_code, 200, reviewer.json)
        self.assertEqual(dual_reviewer.status_code, 200, dual_reviewer.json)
        self.assertTrue(reviewer.json['items'][0]['capabilities']['can_review'])
        self.assertEqual(admin.status_code, 403, admin.json)
        self.assertEqual(admin.json['code'], 'PHOTO_READ_ROLE_REQUIRED')
        self.assertEqual(unscoped.status_code, 404, unscoped.json)
        self.assertEqual(other_operator.status_code, 404, other_operator.json)

    def test_history_filters_pagination_and_rework_cycle_are_explicit(self):
        current_rework = self.get_photos(query='?item_id=1001')
        history = self.get_photos(query='?scope=history&item_id=1000&limit=2&page=2')
        deleted = self.get_photos(query='?scope=history&item_id=1000&review_status=approved')
        cancelled_current = self.get_photos(query='?item_id=1003')
        cancelled_history = self.get_photos(query='?scope=history&item_id=1003')
        self.assertEqual([item['id'] for item in current_rework.json['items']], [7])
        self.assertTrue(current_rework.json['items'][0]['capabilities']['can_retake'])
        self.assertEqual(history.status_code, 200, history.json)
        self.assertEqual(history.json['pagination'], {
            'page': 2, 'limit': 2, 'total': 5, 'has_more': True,
        })
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertEqual({item['id'] for item in deleted.json['items']}, {2, 5})
        self.assertTrue(next(item for item in deleted.json['items'] if item['id'] == 5)['deleted'])
        self.assertEqual(cancelled_current.status_code, 404, cancelled_current.json)
        self.assertEqual([item['id'] for item in cancelled_history.json['items']], [8])
        self.assertFalse(cancelled_history.json['items'][0]['capabilities']['can_review'])
        self.assertFalse(cancelled_history.json['items'][0]['capabilities']['can_delete_pending'])

    def test_invalid_filters_and_item_relationship_do_not_leak_objects(self):
        invalid = self.get_photos(query='?limit=51')
        wrong_item = self.get_photos(query='?item_id=1002')
        missing_auth = self.client.get('/api/mobile/execution-plans/100/sites/10/photos')
        self.assertEqual(invalid.status_code, 400, invalid.json)
        self.assertEqual(invalid.json['code'], 'INVALID_PHOTO_QUERY')
        self.assertEqual(wrong_item.status_code, 404, wrong_item.json)
        self.assertEqual(wrong_item.json['code'], 'EXECUTION_SITE_NOT_FOUND')
        self.assertEqual(missing_auth.status_code, 401, missing_auth.json)
        self.assertEqual(missing_auth.json['code'], 'AUTH_REQUIRED')

    def test_equal_capture_times_use_id_desc_before_upload_time(self):
        db = sqlite3.connect(self.db_path)
        try:
            rows = [
                (10, 'older-id.jpg', '/uploads/site_photos/older-id.jpg', 'inspection', 1000, 100, 1000,
                 '检查采水泵运行状态', 10, 2, '现场运维员', '2026-09-29 14:00:00', '2026-09-29 14:02:00'),
                (11, 'newer-id.jpg', '/uploads/site_photos/newer-id.jpg', 'inspection', 1000, 100, 1000,
                 '检查采水泵运行状态', 10, 2, '现场运维员', '2026-09-29 14:00:00', '2026-09-29 14:01:00'),
            ]
            db.executemany('''INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,plan_id,item_id,item_name,site_id,
                 uploader_id,uploader_name,taken_at,created_at,capture_source,evidence_qualification,
                 review_status,is_deleted,extra_json,category)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'camera','qualified','rejected',0,
                        '{"material_role":"formal"}','设备巡检')''', rows)
            db.commit()
        finally:
            db.close()
        response = self.get_photos(query='?item_id=1000&review_status=rejected')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item['id'] for item in response.json['items']], [11, 10])

    def test_physical_storage_path_is_never_exposed(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute('''INSERT INTO operation_attachments
                (id,filename,stored_path,source_type,source_id,plan_id,item_id,item_name,site_id,
                 uploader_id,uploader_name,taken_at,created_at,capture_source,evidence_qualification,
                 review_status,is_deleted,extra_json,category)
                VALUES (12,'legacy.jpg','C:\\private\\legacy.jpg','inspection',1000,100,1000,
                        '检查采水泵运行状态',10,2,'现场运维员','2026-09-29 06:00:00',
                        '2026-09-29 06:01:00','camera','qualified','superseded',0,
                        '{"material_role":"formal"}','设备巡检')''')
            db.commit()
        finally:
            db.close()
        response = self.get_photos(query='?scope=history&item_id=1000&review_status=superseded')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['items'][0]['url'], '')
        self.assertFalse(response.json['items'][0]['capabilities']['can_view_original'])


if __name__ == '__main__':
    unittest.main()
