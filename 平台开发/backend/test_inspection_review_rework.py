import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class InspectionReviewReworkTest(unittest.TestCase):
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
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': '现场运维'},
            'admin-token': {'id': 1, 'role': 'admin', 'real_name': '管理员'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, assignee_id INTEGER, status TEXT, plan_schedule_id INTEGER,
                    completion_rate REAL, plan_name TEXT
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, template_id INTEGER,
                    item_name TEXT, result TEXT, execution_status TEXT, required_photos INTEGER,
                    actual_photos INTEGER, review_status INTEGER, review_comment TEXT, reviewer_id INTEGER,
                    review_time TEXT, check_time TEXT, completed_at TEXT, photo_urls TEXT, remark TEXT,
                    calibrator TEXT, calibration_values TEXT, gps_lat REAL, gps_lng REAL
                );
                CREATE TABLE inspection_template_items (id INTEGER PRIMARY KEY, template_id INTEGER, item_name TEXT, need_review INTEGER);
                CREATE TABLE mobile_idempotency (idempotency_key TEXT, endpoint TEXT, response_json TEXT);
                CREATE TABLE inspection_checkins (id INTEGER PRIMARY KEY, site_id INTEGER, user_id INTEGER, check_time TEXT);
                CREATE TABLE timeline_events (source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT);
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id INTEGER, title TEXT, content TEXT
                );
                INSERT INTO users VALUES (1, '管理员', 'admin');
                INSERT INTO users VALUES (2, '现场运维', 'operator');
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO plan_schedules VALUES (1, 'approved');
                INSERT INTO insp_plans VALUES (10, 2, 'active', 1, 0, '测试巡检');
                INSERT INTO inspection_checkins VALUES (1, 1, 2, datetime('now','localtime'));
                INSERT INTO inspection_template_items VALUES (1, 7, '仪表读数', 1);
                INSERT INTO insp_plan_items
                  (id, plan_id, site_id, template_id, item_name, result, execution_status,
                   required_photos, actual_photos, review_status, review_comment, reviewer_id,
                   review_time, check_time, completed_at, photo_urls, remark, calibrator,
                   calibration_values, gps_lat, gps_lng)
                VALUES
                  (100, 10, 1, 7, '仪表读数', NULL, 'active', 1, 0, 0, '', NULL,
                   NULL, NULL, NULL, '', '', '', '', NULL, NULL);
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

    def submit(self):
        return self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal',
            'photo_urls': json.dumps(['/uploads/inspection/reading.jpg']), 'remark': '读数正常',
        })

    def test_submission_is_reviewable_and_rejection_reopens_item_for_rework(self):
        first = self.submit()
        self.assertEqual(first.status_code, 200, first.json)
        with app_module.get_db() as db:
            row = db.execute('SELECT review_status, actual_photos, result FROM insp_plan_items WHERE id=100').fetchone()
        self.assertEqual((row['review_status'], row['actual_photos'], row['result']), (1, 1, 'normal'))

        rejected = self.client.put('/api/inspection-v2/items/100/review', headers=self.headers('admin-token'), json={
            'action': 'reject', 'comment': '请重拍仪表读数，画面模糊',
        })
        self.assertEqual(rejected.status_code, 200, rejected.json)
        with app_module.get_db() as db:
            item = db.execute('SELECT result, review_status, review_comment FROM insp_plan_items WHERE id=100').fetchone()
            plan = db.execute('SELECT status, completion_rate FROM insp_plans WHERE id=10').fetchone()
        self.assertIsNone(item['result'])
        self.assertEqual(item['review_status'], 3)
        self.assertIn('重拍', item['review_comment'])
        self.assertEqual((plan['status'], plan['completion_rate']), ('active', 0))

        resubmitted = self.submit()
        self.assertEqual(resubmitted.status_code, 200, resubmitted.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT review_status FROM insp_plan_items WHERE id=100').fetchone()['review_status'], 1)

    def test_batch_rejection_reopens_plan_and_notifies_assignee(self):
        self.assertEqual(self.submit().status_code, 200)
        rejected = self.client.post('/api/inspection-v2/items/batch-review',
                                    headers=self.headers('admin-token'), json={
                                        'reject_items': [{'id': 100, 'reason': '请补拍清晰读数'}],
                                    })
        self.assertEqual(rejected.status_code, 200, rejected.json)
        self.assertEqual(rejected.json['rejected'], 1)
        with app_module.get_db() as db:
            item = db.execute('SELECT result, review_status FROM insp_plan_items WHERE id=100').fetchone()
            plan = db.execute('SELECT status, completion_rate FROM insp_plans WHERE id=10').fetchone()
            notification = db.execute('SELECT user_id, source_type FROM notifications').fetchone()
        self.assertEqual((item['result'], item['review_status']), (None, 3))
        self.assertEqual((plan['status'], plan['completion_rate']), ('active', 0))
        self.assertEqual((notification['user_id'], notification['source_type']), (2, 'inspection_review'))


if __name__ == '__main__':
    unittest.main()
