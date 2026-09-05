import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta

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
                    item_name TEXT, category TEXT, result TEXT, execution_status TEXT, required_photos INTEGER,
                    actual_photos INTEGER, review_status INTEGER, review_comment TEXT, reviewer_id INTEGER,
                    review_time TEXT, check_time TEXT, completed_at TEXT, photo_urls TEXT, remark TEXT,
                    calibrator TEXT, calibration_values TEXT, gps_lat REAL, gps_lng REAL
                    , check_out_time TEXT, rework_required_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, stored_path TEXT, site_id INTEGER, uploader_id INTEGER,
                    description TEXT, filename TEXT, review_status TEXT, reviewer_id INTEGER,
                    reviewed_at TEXT, reject_reason TEXT, source_type TEXT DEFAULT 'site_photo',
                    source_id INTEGER DEFAULT 0, extra_json TEXT DEFAULT '{}',
                    recognized_category TEXT DEFAULT '', category TEXT DEFAULT '',
                    is_deleted INTEGER DEFAULT 0, review_required INTEGER DEFAULT 1,
                    evidence_qualification TEXT DEFAULT 'qualified', evidence_reason TEXT DEFAULT '',
                    evidence_next_action TEXT DEFAULT ''
                );
                CREATE TABLE inspection_template_items (id INTEGER PRIMARY KEY, template_id INTEGER, item_name TEXT, need_review INTEGER);
                CREATE TABLE mobile_idempotency (idempotency_key TEXT, endpoint TEXT, response_json TEXT);
                CREATE TABLE photo_capture_sessions (
                    id INTEGER PRIMARY KEY, token_hash TEXT, user_id INTEGER, site_id INTEGER,
                    plan_id INTEGER, item_id INTEGER, work_order_id INTEGER, issued_at TEXT,
                    expires_at TEXT, used_at TEXT, attachment_id INTEGER,
                    rework_required_at TEXT, capture_source TEXT
                );
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
                  (id, plan_id, site_id, template_id, item_name, category, result, execution_status,
                   required_photos, actual_photos, review_status, review_comment, reviewer_id,
                   review_time, check_time, completed_at, photo_urls, remark, calibrator,
                   calibration_values, gps_lat, gps_lng)
                VALUES
                  (100, 10, 1, 7, '仪表读数', NULL, NULL, 'active', 1, 0, 0, '', NULL,
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
        self.seed_evidence('/uploads/inspection/reading.jpg')
        return self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal',
            'photo_urls': json.dumps(['/uploads/inspection/reading.jpg']), 'remark': '读数正常',
        })

    def seed_evidence(self, path):
        with app_module.get_db() as db:
            if not db.execute('SELECT 1 FROM operation_attachments WHERE stored_path=?', (path,)).fetchone():
                db.execute("""INSERT INTO operation_attachments
                    (stored_path,site_id,uploader_id,description,filename,review_status,
                     evidence_qualification,review_required,extra_json)
                    VALUES (?,1,2,'测试证据',?,'pending','qualified',0,
                            '{"material_role":"pending_inspection","plan_id":10,"item_id":100}')""",
                    (path, os.path.basename(path)))

    def test_photo_projection_accepts_real_attachment_query_rows(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("UPDATE insp_plan_items SET evidence_status='supplement_required', required_photos=4 WHERE id=100")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,filename,review_status,source_type,source_id,
                 evidence_qualification,extra_json)
                VALUES (220,'/uploads/inspection/approved.jpg',1,2,'approved.jpg','approved',
                        'inspection',100,'qualified','{\"material_role\":\"formal\"}')""")
            item = db.execute('SELECT * FROM insp_plan_items WHERE id=100').fetchone()
            evidence = db.execute("""SELECT oa.*, 1 AS is_effective_evidence
                FROM operation_attachments oa WHERE source_id=100""").fetchall()

        state = app_module._mobile_inspection_photo_state(item, evidence)

        self.assertEqual(state['retained_photo_count'], 1)
        self.assertEqual(state['replacement_required_photos'], 3)
        self.assertTrue(state['replacement_submission_allowed'])

    def test_normal_item_cannot_use_calibration_fields_as_its_only_record(self):
        with app_module.get_db() as db:
            db.execute("UPDATE insp_plan_items SET required_photos=0 WHERE id=100")
        response = self.client.post('/api/mobile/submit-item',
                                    headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'photo_urls': '[]',
            'calibrator': '伪造校准人', 'calibration_values': '7.00',
        })
        self.assertEqual(response.status_code, 400, response.json)
        with app_module.get_db() as db:
            item = db.execute("""SELECT result,calibrator,calibration_values
                FROM insp_plan_items WHERE id=100""").fetchone()
        self.assertEqual((item['result'], item['calibrator'], item['calibration_values']),
                         (None, '', ''))

    def test_normal_item_ignores_injected_calibration_fields(self):
        with app_module.get_db() as db:
            db.execute("UPDATE insp_plan_items SET required_photos=0 WHERE id=100")
        response = self.client.post('/api/mobile/submit-item',
                                    headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'photo_urls': '[]',
            'remark': '现场读数正常', 'calibrator': '不应落库', 'calibration_values': '7.00',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            item = db.execute("""SELECT result,remark,calibrator,calibration_values
                FROM insp_plan_items WHERE id=100""").fetchone()
        self.assertEqual((item['result'], item['remark']), ('normal', '现场读数正常'))
        self.assertEqual((item['calibrator'], item['calibration_values']), ('', ''))

    def test_calibration_item_keeps_calibration_submission(self):
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET category='qaqc_calibration',required_photos=0 WHERE id=100""")
        response = self.client.post('/api/mobile/submit-item',
                                    headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'photo_urls': '[]',
            'calibrator': '王工', 'calibration_values': '7.00',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            item = db.execute("""SELECT result,calibrator,calibration_values
                FROM insp_plan_items WHERE id=100""").fetchone()
        self.assertEqual((item['result'], item['calibrator'], item['calibration_values']),
                         ('normal', '王工', '7.00'))

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
        self.assertEqual(item['result'], 'normal')
        self.assertEqual(item['review_status'], 3)
        self.assertIn('重拍', item['review_comment'])
        self.assertEqual((plan['status'], plan['completion_rate']), ('completed', 100))

        self.seed_evidence('/uploads/inspection/reading-retake.jpg')
        with app_module.get_db() as db:
            required_at = db.execute(
                'SELECT rework_required_at FROM insp_plan_items WHERE id=100').fetchone()['rework_required_at']
            attachment_id = db.execute(
                "SELECT id FROM operation_attachments WHERE stored_path='/uploads/inspection/reading-retake.jpg'"
            ).fetchone()['id']
            db.execute("""UPDATE operation_attachments
                SET source_type='inspection',source_id=100 WHERE id=?""", (attachment_id,))
            db.execute("""INSERT INTO photo_capture_sessions
                (id,token_hash,user_id,site_id,plan_id,item_id,issued_at,expires_at,used_at,
                 attachment_id,rework_required_at,capture_source)
                VALUES (1,'retake',2,1,10,100,datetime('now','localtime'),
                        datetime('now','localtime','+15 minute'),datetime('now','localtime'),
                        ?,?,'camera')""", (attachment_id, required_at))
        resubmitted = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps(['/uploads/inspection/reading.jpg',
                                      '/uploads/inspection/reading-retake.jpg']),
        })
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
            notification = db.execute("""SELECT user_id, source_type FROM notifications
                WHERE source_type='inspection_rework'""").fetchone()
        self.assertEqual((item['result'], item['review_status']), ('normal', 3))
        self.assertEqual((plan['status'], plan['completion_rate']), ('completed', 100))
        self.assertEqual((notification['user_id'], notification['source_type']), (2, 'inspection_rework'))

    def test_rejected_field_photo_preserves_original_visit_and_sibling_item(self):
        self.assertEqual(self.submit().status_code, 200)
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("UPDATE insp_plan_items SET check_out_time='2026-08-07 10:00:00' WHERE id=100")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,template_id,item_name,result,execution_status,required_photos,
                 actual_photos,review_status,photo_urls,remark,check_out_time)
                VALUES (101,10,1,7,'站房环境','normal','active',1,1,2,
                        '[\"/uploads/inspection/environment.jpg\"]','正常','2026-08-07 10:00:00')""")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,
                 source_type,source_id,evidence_qualification)
                VALUES (220,'/uploads/inspection/reading.jpg',1,2,'仪表读数','reading.jpg',
                        'pending','inspection',100,'qualified')""")
            original_checkins = db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0]
        rejected = self.client.post(
            '/api/operation-attachments/review', headers=self.headers('admin-token'), json={
                'attachment_ids': [220], 'action': 'reject', 'reject_reason': '画面模糊',
            })
        self.assertEqual(rejected.status_code, 200, rejected.json)
        with app_module.get_db() as db:
            rows = {row['id']: row for row in db.execute("""SELECT id,result,review_status,
                evidence_status,check_out_time,rework_required_at FROM insp_plan_items
                WHERE id IN (100,101)""").fetchall()}
            plan = db.execute('SELECT status,completion_rate FROM insp_plans WHERE id=10').fetchone()
            current_checkins = db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0]
        self.assertEqual((rows[100]['result'], rows[100]['review_status'], rows[100]['evidence_status']),
                         ('normal', 3, 'supplement_required'))
        self.assertEqual(rows[100]['check_out_time'], '2026-08-07 10:00:00')
        self.assertTrue(rows[100]['rework_required_at'])
        self.assertEqual((rows[101]['result'], rows[101]['review_status'], rows[101]['check_out_time']),
                         ('normal', 2, '2026-08-07 10:00:00'))
        self.assertEqual((plan['status'], plan['completion_rate']), ('completed', 100))
        self.assertEqual(current_checkins, original_checkins)

    def test_selective_photo_review_rejects_selected_and_approves_the_rest(self):
        self.assertEqual(self.submit().status_code, 200)
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,
                 source_type,source_id,evidence_qualification)
                VALUES (?, ?, 1, 2, ?, ?, 'pending', 'inspection', 100, 'qualified')""", [
                (200, '/uploads/inspection/reading-detail.jpg', '仪表读数', 'reading-detail.jpg'),
                (201, '/uploads/inspection/overview.jpg', '站点全景', 'overview.jpg'),
            ])
            db.execute("""UPDATE insp_plan_items SET actual_photos=2,
                photo_urls='["/uploads/inspection/reading-detail.jpg","/uploads/inspection/overview.jpg"]'
                WHERE id=100""")
        response = self.client.post('/api/operation-attachments/review',
                                    headers=self.headers('admin-token'), json={
            'approve_ids': [201], 'reject_ids': [200], 'reject_reason': '读数模糊',
        })

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual((response.json['approved'], response.json['rejected']), (1, 1))
        with app_module.get_db() as db:
            statuses = {row['id']: row['review_status'] for row in db.execute(
                'SELECT id, review_status FROM operation_attachments WHERE id>=200').fetchall()}
            item = db.execute('SELECT result, review_status FROM insp_plan_items WHERE id=100').fetchone()
            notifications = db.execute(
                'SELECT source_type,source_id FROM notifications WHERE user_id=2').fetchall()
        self.assertEqual(statuses, {200: 'rejected', 201: 'approved'})
        self.assertEqual((item['result'], item['review_status']), ('normal', 3))
        self.assertEqual([(row['source_type'], row['source_id']) for row in notifications],
                         [('inspection_rework', 10)])

    def test_rejecting_directly_bound_photo_reopens_only_its_check_item(self):
        self.assertEqual(self.submit().status_code, 200)
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,template_id,item_name,result,execution_status,required_photos,
                 actual_photos,review_status,photo_urls,remark)
                VALUES (101,10,1,7,'站房环境','normal','active',1,1,1,'[\"/uploads/inspection/environment.jpg\"]','正常')""")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,source_type,source_id)
                VALUES (210,'/uploads/inspection/reading.jpg',1,2,'仪表读数','reading.jpg','pending','inspection',100)""")
        response = self.client.post('/api/operation-attachments/review',
                                    headers=self.headers('admin-token'), json={
            'attachment_ids': [210], 'action': 'reject', 'reject_reason': '读数不清晰',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            rows = {row['id']: row for row in db.execute(
                'SELECT id,result,review_status FROM insp_plan_items WHERE id IN (100,101)').fetchall()}
        self.assertEqual((rows[100]['result'], rows[100]['review_status']), ('normal', 3))
        self.assertEqual((rows[101]['result'], rows[101]['review_status']), ('normal', 1))

    def test_rework_checkin_never_resets_other_item_results(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plans ADD COLUMN generate_date TEXT")
            db.execute("UPDATE insp_plans SET generate_date=date('now','localtime') WHERE id=10")
            db.execute("ALTER TABLE inspection_checkins ADD COLUMN site_name TEXT")
            db.execute("ALTER TABLE inspection_checkins ADD COLUMN user_name TEXT")
            db.execute("ALTER TABLE inspection_checkins ADD COLUMN lat REAL")
            db.execute("ALTER TABLE inspection_checkins ADD COLUMN lng REAL")
            db.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY,name TEXT,gps_lat REAL,gps_lng REAL)")
            db.execute("INSERT INTO sites VALUES (1,'测试站',28.071303,115.539684)")
            db.execute("""UPDATE insp_plan_items SET result=NULL,review_status=3,
                       rework_required_at=datetime('now','localtime') WHERE id=100""")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,template_id,item_name,result,execution_status,required_photos,
                 actual_photos,review_status,photo_urls,remark)
                VALUES (101,10,1,7,'站房环境','normal','active',1,1,1,'[\"/uploads/inspection/environment.jpg\"]','正常')""")
        response = self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'site_id': 1, 'site_name': '测试站', 'lat': 28.071303, 'lng': 115.539684,
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            rows = {row['id']: row for row in db.execute(
                'SELECT id,result,review_status FROM insp_plan_items WHERE id IN (100,101)').fetchall()}
        self.assertEqual((rows[100]['result'], rows[100]['review_status']), (None, 3))
        self.assertEqual((rows[101]['result'], rows[101]['review_status']), ('normal', 1))

    def test_pending_item_accepts_only_new_supplemental_evidence(self):
        first = self.submit()
        self.assertEqual(first.status_code, 200, first.json)
        duplicate = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal',
            'photo_urls': json.dumps(['/uploads/inspection/reading.jpg']), 'remark': '重复提交',
        })
        self.assertEqual(duplicate.status_code, 409, duplicate.json)
        self.seed_evidence('/uploads/inspection/extra.jpg')
        supplement = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps(['/uploads/inspection/reading.jpg', '/uploads/inspection/extra.jpg']),
        })
        self.assertEqual(supplement.status_code, 200, supplement.json)
        self.assertTrue(supplement.json['supplemented'])
        with app_module.get_db() as db:
            item = db.execute('SELECT result, review_status, actual_photos, photo_urls FROM insp_plan_items WHERE id=100').fetchone()
        self.assertEqual((item['result'], item['review_status'], item['actual_photos']), ('normal', 1, 2))
        same = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps(['/uploads/inspection/reading.jpg', '/uploads/inspection/extra.jpg']),
        })
        self.assertEqual(same.status_code, 409, same.json)

    def test_voided_approved_item_accepts_server_required_replacement(self):
        self.assertEqual(self.submit().status_code, 200)
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("""UPDATE insp_plan_items
                SET review_status=2, evidence_status='supplement_required'
                WHERE id=100""")
        self.seed_evidence('/uploads/inspection/replacement.jpg')

        replacement = self.client.post(
            '/api/mobile/submit-item',
            headers=self.headers('operator-token'),
            json={
                'item_id': 100,
                'plan_id': 10,
                'result': 'normal',
                'supplement': True,
                'photo_urls': json.dumps([
                    'http://127.0.0.1:5020/uploads/inspection/reading.jpg?display=1',
                    'http://127.0.0.1:5020/uploads/inspection/replacement.jpg',
                ]),
            },
        )

        self.assertEqual(replacement.status_code, 200, replacement.json)
        self.assertEqual(replacement.json['added_photos'], 1)
        self.assertEqual(
            (replacement.json['review_status'], replacement.json['evidence_status']),
            (1, 'replacement_submitted'),
        )
        with app_module.get_db() as db:
            item = db.execute(
                'SELECT review_status, evidence_status, photo_urls FROM insp_plan_items WHERE id=100'
            ).fetchone()
        self.assertEqual((item['review_status'], item['evidence_status']), (1, 'replacement_submitted'))
        self.assertEqual(json.loads(item['photo_urls']), [
            '/uploads/inspection/reading.jpg',
            '/uploads/inspection/replacement.jpg',
        ])

    def test_rejected_replacement_is_not_revived_when_submitting_next_retake(self):
        self.assertEqual(self.submit().status_code, 200)
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("""UPDATE insp_plan_items SET review_status=3,
                evidence_status='supplement_required',
                rework_required_at=datetime('now','localtime') WHERE id=100""")
            db.execute("UPDATE operation_attachments SET review_status='rejected', reject_reason='旧照片需重拍' WHERE source_type='inspection' AND source_id=100")
        self.seed_evidence('/uploads/inspection/replacement-next.jpg')
        with app_module.get_db() as db:
            item = db.execute('SELECT rework_required_at FROM insp_plan_items WHERE id=100').fetchone()
            attachment = db.execute("""SELECT id FROM operation_attachments
                WHERE stored_path='/uploads/inspection/replacement-next.jpg'""").fetchone()
            db.execute("""UPDATE operation_attachments SET source_type='inspection',source_id=100
                WHERE id=?""", (attachment['id'],))
            db.execute("""INSERT INTO photo_capture_sessions
                (id,token_hash,user_id,site_id,plan_id,item_id,issued_at,expires_at,used_at,
                 attachment_id,rework_required_at,capture_source)
                VALUES (2,'next-cycle',2,1,10,100,datetime('now','localtime'),
                        datetime('now','localtime','+15 minute'),datetime('now','localtime'),
                        ?,?,'camera')""", (attachment['id'], item['rework_required_at']))

        replacement = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 100,
            'plan_id': 10,
            'result': 'normal',
            'supplement': True,
            'photo_urls': json.dumps([
                '/uploads/inspection/reading.jpg',
                '/uploads/inspection/replacement-next.jpg',
            ]),
        })
        self.assertEqual(replacement.status_code, 200, replacement.json)
        with app_module.get_db() as db:
            rows = db.execute(
                'SELECT stored_path, review_status FROM operation_attachments WHERE source_type="inspection" AND source_id=100 ORDER BY stored_path'
            ).fetchall()
            item = db.execute('SELECT review_status, evidence_status FROM insp_plan_items WHERE id=100').fetchone()
        self.assertEqual({row['stored_path']: row['review_status'] for row in rows}, {
            '/uploads/inspection/reading.jpg': 'rejected',
            '/uploads/inspection/replacement-next.jpg': 'pending',
        })
        self.assertEqual((item['review_status'], item['evidence_status']), (1, 'replacement_submitted'))

    def test_retake_cycle_mismatch_has_zero_submission_side_effects(self):
        self.assertEqual(self.submit().status_code, 200)
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("""UPDATE insp_plan_items SET review_status=3,
                evidence_status='supplement_required', rework_required_at='2026-08-14 10:00:00',
                check_out_time='2026-08-14 09:00:00' WHERE id=100""")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,
                 source_type,source_id,evidence_qualification)
                VALUES (230,'/uploads/inspection/wrong-cycle.jpg',1,2,'仪表读数',
                        'wrong-cycle.jpg','pending','inspection',100,'qualified')""")
            db.execute("""INSERT INTO photo_capture_sessions
                (id,token_hash,user_id,site_id,plan_id,item_id,issued_at,expires_at,used_at,
                 attachment_id,rework_required_at,capture_source)
                VALUES (3,'old-cycle',2,1,10,100,'2026-08-14 09:15:00',
                        '2026-08-14 09:30:00','2026-08-14 09:16:00',230,
                        '2026-08-14 09:10:00','camera')""")
            before_item = tuple(db.execute("""SELECT result,review_status,evidence_status,
                rework_required_at,check_out_time,photo_urls,actual_photos
                FROM insp_plan_items WHERE id=100""").fetchone())
            before_attachment = tuple(db.execute("""SELECT source_type,source_id,review_status,
                evidence_qualification FROM operation_attachments WHERE id=230""").fetchone())
            before_session = tuple(db.execute("""SELECT used_at,attachment_id,rework_required_at
                FROM photo_capture_sessions WHERE id=3""").fetchone())

        response = self.client.post('/api/mobile/submit-item',
                                    headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps(['/uploads/inspection/wrong-cycle.jpg']),
        })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'RETAKE_EVIDENCE_CYCLE_MISMATCH')
        with app_module.get_db() as db:
            after_item = tuple(db.execute("""SELECT result,review_status,evidence_status,
                rework_required_at,check_out_time,photo_urls,actual_photos
                FROM insp_plan_items WHERE id=100""").fetchone())
            after_attachment = tuple(db.execute("""SELECT source_type,source_id,review_status,
                evidence_qualification FROM operation_attachments WHERE id=230""").fetchone())
            after_session = tuple(db.execute("""SELECT used_at,attachment_id,rework_required_at
                FROM photo_capture_sessions WHERE id=3""").fetchone())
        self.assertEqual(after_item, before_item)
        self.assertEqual(after_attachment, before_attachment)
        self.assertEqual(after_session, before_session)

    def test_rejecting_a_replacement_advances_cycle_and_invalidates_prior_authorization(self):
        self.assertEqual(self.submit().status_code, 200)
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("""UPDATE insp_plan_items SET review_status=1,
                evidence_status='replacement_submitted',
                rework_required_at='2026-08-13 08:00:00',
                check_out_time='2026-08-13 07:30:00',
                actual_photos=2,
                photo_urls='["/uploads/inspection/reading.jpg","/uploads/inspection/replacement-cycle-one.jpg"]'
                WHERE id=100""")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,
                 source_type,source_id,evidence_qualification)
                VALUES (240,'/uploads/inspection/replacement-cycle-one.jpg',1,2,'仪表读数',
                        'replacement-cycle-one.jpg','pending','inspection',100,'qualified')""")
            db.execute("""INSERT INTO photo_capture_sessions
                (id,token_hash,user_id,site_id,plan_id,item_id,issued_at,expires_at,used_at,
                 attachment_id,rework_required_at,capture_source)
                VALUES (4,?,2,1,10,100,datetime('now','localtime'),
                        datetime('now','localtime','+15 minute'),NULL,NULL,
                        '2026-08-13 08:00:00','camera')""",
                (app_module._hash_token('prior-cycle-token'),))

        response = self.client.post('/api/operation-attachments/review',
                                    headers=self.headers('admin-token'), json={
            'attachment_ids': [240], 'action': 'reject', 'reject_reason': '仍然模糊',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            item = db.execute("""SELECT result,review_status,evidence_status,
                rework_required_at,check_out_time FROM insp_plan_items WHERE id=100""").fetchone()
            attachment = db.execute(
                'SELECT review_status,reject_reason FROM operation_attachments WHERE id=240'
            ).fetchone()
            stale_session = app_module._capture_session_row(
                db, 'prior-cycle-token', user_id=2, site_id=1, plan_id=10, item_id=100,
                capture_source='camera', rework_required_at=item['rework_required_at'])
            notifications = db.execute(
                'SELECT source_type,source_id,content FROM notifications WHERE user_id=2').fetchall()
        self.assertEqual((item['result'], item['review_status'], item['evidence_status']),
                         ('normal', 3, 'supplement_required'))
        self.assertNotEqual(item['rework_required_at'], '2026-08-13 08:00:00')
        self.assertEqual(item['check_out_time'], '2026-08-13 07:30:00')
        self.assertEqual((attachment['review_status'], attachment['reject_reason']),
                         ('rejected', '仍然模糊'))
        self.assertIsNone(stale_session)
        self.assertEqual([(row['source_type'], row['source_id']) for row in notifications],
                         [('inspection_rework', 10)])
        self.assertIn('1 个检查项、1 张照片', notifications[0]['content'])

    def test_approved_item_is_frozen(self):
        self.assertEqual(self.submit().status_code, 200)
        with app_module.get_db() as db:
            db.execute('UPDATE insp_plan_items SET review_status=2 WHERE id=100')
            db.commit()
        response = self.client.post('/api/mobile/submit-item', headers=self.headers('operator-token'), json={
            'item_id': 100, 'plan_id': 10, 'result': 'normal', 'supplement': True,
            'photo_urls': json.dumps(['/uploads/inspection/approved-extra.jpg']),
        })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'INSPECTION_ITEM_APPROVED')
        with app_module.get_db() as db:
            db.execute("UPDATE insp_plan_items SET check_out_time='2026-08-07 10:00:00' WHERE id=100")
            db.execute("UPDATE insp_plans SET status='completed', completion_rate=100 WHERE id=10")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,
                 source_type,source_id,evidence_qualification)
                VALUES (200, '/uploads/inspection/reading.jpg', 1, 2,
                        '仪表读数', 'reading.jpg', 'pending', 'inspection', 100, 'qualified')""")
            db.commit()

        rejected = self.client.post('/api/operation-attachments/review', headers=self.headers('admin-token'), json={
            'attachment_ids': [200], 'action': 'reject', 'reject_reason': '缺少水印',
        })
        self.assertEqual(rejected.status_code, 200, rejected.json)
        with app_module.get_db() as db:
            item = db.execute("SELECT result, review_status, review_comment, check_out_time, rework_required_at FROM insp_plan_items WHERE id=100").fetchone()
            plan = db.execute('SELECT status, completion_rate FROM insp_plans WHERE id=10').fetchone()
        self.assertEqual(item['result'], 'normal')
        self.assertEqual(item['review_status'], 3)
        self.assertIn('水印', item['review_comment'])
        self.assertEqual(item['check_out_time'], '2026-08-07 10:00:00')
        self.assertTrue(item['rework_required_at'])
        self.assertEqual((plan['status'], plan['completion_rate']), ('completed', 100))

        resubmitted = self.submit()
        self.assertEqual(resubmitted.status_code, 409, resubmitted.json)
        self.assertEqual(resubmitted.json['code'], 'INSPECTION_ITEM_NO_NEW_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
