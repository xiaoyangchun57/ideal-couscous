import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock


sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class InspectionAuditUnitTest(unittest.TestCase):
    """Contract tests for the plan + site inspection review unit."""

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
        app_module._tokens['reviewer-token'] = {
            'id': 1, 'role': 'reviewer', 'real_name': 'Reviewer',
        }
        app_module._tokens['admin-token'] = {
            'id': 1, 'role': 'admin', 'roles': ['admin'], 'real_name': 'Reviewer',
        }
        app_module._tokens['operator-token'] = {
            'id': 2, 'role': 'operator', 'roles': ['operator'], 'real_name': 'Operator',
        }
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT, status TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, code TEXT, type TEXT,
                    gps_lat REAL, gps_lng REAL
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, submitted_at TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_name TEXT, assignee_id INTEGER,
                    completion_rate REAL, status TEXT, plan_schedule_id INTEGER,
                    generate_date TEXT
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, item_name TEXT,
                    actual_photos INTEGER, required_photos INTEGER, remark TEXT, result TEXT,
                    check_time TEXT, photo_urls TEXT, review_status INTEGER, execution_status TEXT,
                    review_comment TEXT, reviewer_id INTEGER, review_time TEXT, completed_at TEXT,
                    check_out_time TEXT, rework_required_at TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER, event_type TEXT, title TEXT,
                    description TEXT, remark TEXT, status TEXT, images TEXT, assignee TEXT,
                    created_at TEXT, check_in_time TEXT, review_submitted_at TEXT
                );
                CREATE TABLE parts_requests (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, work_order_no TEXT,
                    request_no TEXT, reason TEXT, fulfillment_type TEXT, requested_part_name TEXT,
                    specification TEXT, estimated_amount REAL, requester_id INTEGER, created_at TEXT,
                    status TEXT
                );
                CREATE TABLE parts_request_items (request_id INTEGER, part_sku TEXT, quantity REAL);
                CREATE TABLE spare_parts_inventory (part_code TEXT, part_name TEXT, manufacturer TEXT, model TEXT);
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER, start_at TEXT,
                    end_at TEXT, site_id INTEGER, destination TEXT, reason TEXT, created_at TEXT, status TEXT,
                    work_order_no TEXT, rework_plan_id INTEGER
                );
                CREATE TABLE vehicles (id INTEGER PRIMARY KEY, plate_no TEXT, model TEXT);
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, stored_path TEXT, site_id INTEGER, uploader_id INTEGER,
                    description TEXT, filename TEXT, review_status TEXT, reviewer_id INTEGER,
                    reviewed_at TEXT, reject_reason TEXT, source_type TEXT, source_id INTEGER,
                    is_deleted INTEGER, review_required INTEGER, created_at TEXT, watermark_text TEXT,
                    recognized_category TEXT, extra_json TEXT, taken_at TEXT, is_flagged INTEGER,
                    flag_reason TEXT, flag_rule TEXT, capture_source TEXT, file_type TEXT,
                    category TEXT, uploader_name TEXT, duplicate_of_id INTEGER
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id TEXT, title TEXT, content TEXT, is_read INTEGER DEFAULT 0,
                    dedupe_key TEXT, payload_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE inspection_checkins (
                    site_id INTEGER, site_name TEXT, user_id INTEGER, user_name TEXT,
                    check_time TEXT, lat REAL, lng REAL
                );
                CREATE TABLE timeline_events (
                    source_type TEXT, source_id INTEGER, event_type TEXT,
                    operator TEXT, remark TEXT
                );
                INSERT INTO users VALUES (1, 'Reviewer', 'reviewer', 'active');
                INSERT INTO users VALUES (2, 'Operator', 'operator', 'active');
                INSERT INTO users VALUES (3, 'Secondary Admin', 'operator', 'active');
                INSERT INTO users VALUES (4, 'Outside Reviewer', 'reviewer', 'active');
                INSERT INTO user_roles VALUES (3, 'admin'), (4, 'reviewer');
                INSERT INTO user_sites VALUES (1, 1);
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO sites VALUES (1, 'Site A', 'S-1', 'water_quality', 28.6800, 115.7300);
                INSERT INTO insp_plans
                    (id,plan_name,assignee_id,completion_rate,status,generate_date)
                    VALUES (10, 'Route A', 2, 100, 'completed', date('now'));
                INSERT INTO insp_plan_items VALUES
                    (100, 10, 1, 'Analyzer', 2, 1, 'first item', 'normal', datetime('now'),
                     '["/uploads/a.jpg", "/uploads/b.jpg"]', 1, 'active', '', NULL, NULL, datetime('now'), NULL, NULL),
                    (101, 10, 1, 'Pump', 1, 1, 'second item', 'normal', datetime('now'),
                     '["/uploads/c.jpg"]', 1, 'active', '', NULL, NULL, datetime('now'), NULL, NULL);
                INSERT INTO operation_attachments VALUES
                    (200, '/uploads/a.jpg', 1, 2, 'Analyzer', 'a.jpg', 'pending', NULL, NULL, NULL,
                     'inspection', 100, 0, 1, datetime('now'), 'watermark-a', 'meter', '{"ocr_status":"verified"}',
                     '2026-08-22 09:15:00', 1, 'blurred', 'sharpness', 'camera', 'image', '', 'Operator', NULL),
                    (201, '/uploads/b.jpg', 1, 2, 'Analyzer', 'b.jpg', 'pending', NULL, NULL, NULL,
                     'inspection', 100, 0, 1, datetime('now'), 'watermark-b', 'meter', '{"ocr_status":"verified"}',
                     NULL, 0, '', '', 'camera', 'image', '', '', NULL),
                    (202, '/uploads/c.jpg', 1, 2, 'Pump', 'c.jpg', 'pending', NULL, NULL, NULL,
                     'inspection', 101, 0, 1, datetime('now'), 'watermark-c', 'pump', '{"ocr_status":"unreadable"}',
                     datetime('now'), 1, 'missing watermark', 'watermark', 'camera', 'image', '', '', NULL);
                ALTER TABLE operation_attachments ADD COLUMN evidence_qualification TEXT DEFAULT 'qualified';
                ALTER TABLE operation_attachments ADD COLUMN evidence_reason TEXT DEFAULT '';
                ALTER TABLE operation_attachments ADD COLUMN evidence_next_action TEXT DEFAULT '';
                ALTER TABLE insp_plan_items ADD COLUMN need_review INTEGER DEFAULT 0;
                INSERT INTO inspection_checkins VALUES
                    (1, 'Site A', 2, 'Operator', datetime('now','localtime'), 28.6800, 115.7300);
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
    def headers():
        return {'Authorization': 'Bearer reviewer-token'}

    def test_plan_site_is_one_review_unit_with_traceable_attachments(self):
        response = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.json)
        inspection_cards = [card for card in response.json
                            if card['source_type'] in ('inspection_batch', 'photo_review')]
        self.assertEqual(len(inspection_cards), 1, inspection_cards)
        card = inspection_cards[0]
        self.assertEqual(card['source_type'], 'inspection_batch')
        self.assertEqual(card['item_ids'], [100, 101])
        self.assertEqual({photo['id'] for photo in card['attachment_details']}, {200, 201, 202})
        details = {photo['id']: photo for photo in card['attachment_details']}
        self.assertEqual((details[200]['taken_at'], details[200]['uploader_name']),
                         ('2026-08-22 09:15:00', 'Operator'))
        self.assertEqual((details[201]['taken_at'], details[201]['uploader_name']), (None, ''))
        for photo in card['attachment_details']:
            self.assertTrue(photo['item_id'])
            self.assertTrue(photo['item_name'])
        with app_module.get_db() as db:
            notices = db.execute("""SELECT COUNT(*) FROM notifications
                WHERE user_id=1 AND source_type='inspection_review_batch'
                  AND source_id='insp_batch_10_1'""").fetchone()[0]
        self.assertEqual(notices, 1)

    def test_cancelled_pending_items_do_not_diverge_stats_from_pending_cards(self):
        admin_headers = {'Authorization': 'Bearer admin-token'}
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET execution_status='cancelled', review_status=1
                WHERE plan_id=10 AND site_id=1""")

        for headers in (admin_headers, self.headers()):
            stats = self.client.get('/api/audit/stats', headers=headers)
            self.assertEqual(stats.status_code, 200, stats.json)
            self.assertEqual(stats.json['inspection_pending'], 0)
            pending = self.client.get('/api/audit/pending', headers=headers)
            self.assertEqual(pending.status_code, 200, pending.json)
            self.assertFalse(any(
                row['source_type'] == 'inspection_batch' for row in pending.json
            ), pending.json)

        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items SET execution_status='active'
                WHERE id=100""")

        for headers in (admin_headers, self.headers()):
            stats = self.client.get('/api/audit/stats', headers=headers)
            self.assertEqual(stats.status_code, 200, stats.json)
            self.assertEqual(stats.json['inspection_pending'], 1)
            pending = self.client.get('/api/audit/pending', headers=headers)
            self.assertEqual(pending.status_code, 200, pending.json)
            batches = [row for row in pending.json
                       if row['source_type'] == 'inspection_batch']
            self.assertEqual(len(batches), 1, batches)
            self.assertEqual(batches[0]['item_ids'], [100])

    def prepare_last_item_submission(self):
        with app_module.get_db() as db:
            db.execute("UPDATE insp_plans SET completion_rate=50,status='active' WHERE id=10")
            db.execute("""UPDATE insp_plan_items SET review_status=2,need_review=0,
                reviewer_id=NULL,review_time=NULL,check_out_time=NULL WHERE id=100""")
            db.execute("""UPDATE insp_plan_items SET result=NULL,review_status=0,need_review=0,
                check_time=NULL,completed_at=NULL,photo_urls='[]',actual_photos=0,
                reviewer_id=NULL,review_time=NULL,check_out_time=NULL WHERE id=101""")
            db.execute("""UPDATE operation_attachments SET review_status='pending',reviewer_id=NULL,
                reviewed_at=NULL,reject_reason=NULL,review_required=0 WHERE id IN (200,201)""")
            db.execute("""UPDATE operation_attachments SET source_type='site_photo',source_id=0,
                review_status='pending',reviewer_id=NULL,reviewed_at=NULL,reject_reason=NULL,
                review_required=0,
                extra_json='{"material_role":"pending_inspection","plan_id":10,"item_id":101}'
                WHERE id=202""")
            db.execute('DELETE FROM notifications')
            db.execute('DELETE FROM timeline_events')

    def submit_last_item(self, key='final-item-101'):
        return self.client.post(
            '/api/mobile/submit-item',
            headers={'Authorization': 'Bearer operator-token'},
            json={
                'item_id': 101, 'plan_id': 10, 'result': 'normal',
                'remark': 'final site item', 'photo_urls': '["/uploads/c.jpg"]',
                '_idempotency_key': key,
            },
        )

    def approve_current_batch(self):
        return self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'approve_ids': [200, 201, 202], 'reject_ids': [],
            'approve_item_ids': [100, 101],
        })

    def checkout_site(self):
        return self.client.post(
            '/api/mobile/execution-plans/10/sites/1/check-out',
            headers={'Authorization': 'Bearer operator-token'},
            json={'lat': 28.6800, 'lng': 115.7300},
        )

    def test_final_submit_opens_one_whole_site_review_before_pending_or_checkout(self):
        self.prepare_last_item_submission()
        submitted = self.submit_last_item()
        self.assertEqual(submitted.status_code, 200, submitted.json)
        self.assertEqual(submitted.json['review_status'], 1)

        with app_module.get_db() as db:
            statuses = [row['review_status'] for row in db.execute(
                'SELECT review_status FROM insp_plan_items ORDER BY id').fetchall()]
            recipients = [row['user_id'] for row in db.execute("""SELECT user_id FROM notifications
                WHERE source_type='inspection_review_batch' ORDER BY user_id""").fetchall()]
            plan = db.execute('SELECT completion_rate,status FROM insp_plans WHERE id=10').fetchone()
            checkout_count = db.execute("""SELECT COUNT(*) FROM insp_plan_items
                WHERE check_out_time IS NOT NULL""").fetchone()[0]
        self.assertEqual(statuses, [1, 1])
        self.assertEqual(recipients, [1, 3])
        self.assertEqual((plan['completion_rate'], plan['status']), (100, 'completed'))
        self.assertEqual(checkout_count, 0)

        stats = self.client.get('/api/audit/stats', headers=self.headers())
        self.assertEqual(stats.status_code, 200, stats.json)
        self.assertEqual(stats.json['inspection_pending'], 1)

        pending = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(pending.status_code, 200, pending.json)
        batches = [row for row in pending.json if row['source_type'] == 'inspection_batch']
        self.assertEqual(len(batches), 1, batches)
        self.assertEqual(set(batches[0]['item_ids']), {100, 101})
        self.assertEqual({row['id'] for row in batches[0]['attachment_details']}, {200, 201, 202})
        self.assertEqual({row['need_review'] for row in batches[0]['item_details']}, {0})

        replay = self.submit_last_item()
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertEqual(replay.json, submitted.json)
        second_pending = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(second_pending.status_code, 200, second_pending.json)
        with app_module.get_db() as db:
            notification_count = db.execute("""SELECT COUNT(*) FROM notifications
                WHERE source_type='inspection_review_batch'""").fetchone()[0]
        self.assertEqual(notification_count, 2)

    def test_final_submit_promotes_only_selected_pending_attachment(self):
        self.prepare_last_item_submission()
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,filename,review_status,source_type,source_id,
                 is_deleted,review_required,created_at,extra_json,file_type,evidence_qualification)
                VALUES (208,'/uploads/unselected.jpg',1,2,'unselected.jpg','pending','site_photo',0,
                        0,0,datetime('now'),
                        '{"material_role":"pending_inspection","plan_id":10,"item_id":101}',
                        'image','qualified')""")

        submitted = self.submit_last_item('selected-only-final-item')
        self.assertEqual(submitted.status_code, 200, submitted.json)

        with app_module.get_db() as db:
            selected = db.execute("""SELECT source_type,source_id,review_required,extra_json
                FROM operation_attachments WHERE id=202""").fetchone()
            unselected = db.execute("""SELECT source_type,source_id,review_required,extra_json
                FROM operation_attachments WHERE id=208""").fetchone()
        self.assertEqual((selected['source_type'], selected['source_id'], selected['review_required']),
                         ('inspection', 101, 1))
        self.assertEqual(json.loads(selected['extra_json'])['material_role'], 'formal')
        self.assertEqual((unselected['source_type'], unselected['source_id'],
                          unselected['review_required']), ('site_photo', 0, 0))
        self.assertEqual(json.loads(unselected['extra_json'])['material_role'], 'pending_inspection')

        pending = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(pending.status_code, 200, pending.json)
        batch = next(row for row in pending.json if row['source_type'] == 'inspection_batch')
        self.assertIn(202, batch['attachment_ids'])
        self.assertNotIn(208, batch['attachment_ids'])

    def test_checkout_after_approval_does_not_reopen_review(self):
        self.prepare_last_item_submission()
        self.assertEqual(self.submit_last_item().status_code, 200)
        approved = self.approve_current_batch()
        self.assertEqual(approved.status_code, 200, approved.json)

        checkout = self.checkout_site()
        self.assertEqual(checkout.status_code, 200, checkout.json)
        replay = self.checkout_site()
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertTrue(replay.json['already_closed'])

        with app_module.get_db() as db:
            item_statuses = [row['review_status'] for row in db.execute(
                'SELECT review_status FROM insp_plan_items ORDER BY id').fetchall()]
            photo_statuses = [row['review_status'] for row in db.execute(
                'SELECT review_status FROM operation_attachments WHERE id IN (200,201,202) ORDER BY id').fetchall()]
            unread = db.execute("""SELECT COUNT(*) FROM notifications
                WHERE source_type='inspection_review_batch' AND is_read=0""").fetchone()[0]
        self.assertEqual(item_statuses, [2, 2])
        self.assertEqual(photo_statuses, ['approved', 'approved', 'approved'])
        self.assertEqual(unread, 0)

    def test_checkout_before_review_keeps_whole_batch_reviewable(self):
        self.prepare_last_item_submission()
        self.assertEqual(self.submit_last_item().status_code, 200)
        checkout = self.checkout_site()
        self.assertEqual(checkout.status_code, 200, checkout.json)

        pending = self.client.get('/api/audit/pending', headers=self.headers())
        batches = [row for row in pending.json if row['source_type'] == 'inspection_batch']
        self.assertEqual(len(batches), 1, batches)
        approved = self.approve_current_batch()
        self.assertEqual(approved.status_code, 200, approved.json)
        with app_module.get_db() as db:
            statuses = [row['review_status'] for row in db.execute(
                'SELECT review_status FROM insp_plan_items ORDER BY id').fetchall()]
        self.assertEqual(statuses, [2, 2])

    def test_mixed_need_review_batch_keeps_every_item_and_formal_photo(self):
        with app_module.get_db() as db:
            db.execute('UPDATE insp_plan_items SET need_review=CASE WHEN id=100 THEN 1 ELSE 0 END')
            db.execute('UPDATE operation_attachments SET review_required=CASE WHEN id=200 THEN 1 ELSE 0 END')
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,filename,review_status,source_type,source_id,
                 is_deleted,review_required,created_at,extra_json,file_type,evidence_qualification)
                VALUES (203,'/uploads/supplement.jpg',1,2,'supplement.jpg','pending','inspection',100,
                        0,0,datetime('now'),'{"material_role":"supplement"}','image','qualified')""")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,filename,review_status,source_type,source_id,
                 is_deleted,review_required,created_at,extra_json,file_type,evidence_qualification)
                VALUES (204,'/uploads/unqualified.jpg',1,2,'unqualified.jpg','pending','inspection',101,
                        0,0,datetime('now'),'{}','image','rejected')""")

        response = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.json)
        batch = next(row for row in response.json if row['source_type'] == 'inspection_batch')
        self.assertEqual(batch['item_ids'], [100, 101])
        self.assertEqual(
            {row['id']: row['need_review'] for row in batch['item_details']},
            {100: 1, 101: 0},
        )
        self.assertEqual({row['id'] for row in batch['attachment_details']}, {200, 201, 202})

    def test_audit_excludes_formal_attachment_not_in_current_item_photo_urls(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,filename,review_status,source_type,source_id,
                 is_deleted,review_required,created_at,extra_json,file_type,evidence_qualification)
                VALUES (209,'/uploads/historical.jpg',1,2,'historical.jpg','pending','inspection',100,
                        0,1,datetime('now'),'{}','image','qualified')""")
        pending = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(pending.status_code, 200, pending.json)
        batch = next(row for row in pending.json if row['source_type'] == 'inspection_batch')
        self.assertNotIn(209, batch['attachment_ids'])
        self.assertNotIn(209, {row['id'] for row in batch['attachment_details']})
        rejected = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'attachment_ids': [209], 'action': 'reject', 'reject_reason': '不应进入当前集合',
        })
        self.assertEqual((rejected.status_code, rejected.json.get('code')),
                         (409, 'ATTACHMENT_NOT_IN_ITEM_SUBMISSION'))
        with app_module.get_db() as db:
            self.assertEqual(db.execute(
                'SELECT review_status FROM operation_attachments WHERE id=209').fetchone()[0], 'pending')

    def test_final_submit_review_creation_failure_rolls_back_every_write(self):
        self.prepare_last_item_submission()

        def fail_after_partial_notification(db, batch, strict=False):
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content)
                VALUES (1,'inspection_review_batch',?,'partial','partial')""", (batch['id'],))
            raise sqlite3.OperationalError('forced notification failure')

        with mock.patch.object(app_module, '_notify_inspection_batch_reviewers',
                               side_effect=fail_after_partial_notification):
            response = self.submit_last_item('rollback-final-item')
        self.assertEqual(response.status_code, 500, response.json)

        with app_module.get_db() as db:
            item = db.execute("""SELECT result,review_status,photo_urls,actual_photos
                FROM insp_plan_items WHERE id=101""").fetchone()
            plan = db.execute('SELECT completion_rate,status FROM insp_plans WHERE id=10').fetchone()
            attachment = db.execute("""SELECT source_type,source_id
                FROM operation_attachments WHERE id=202""").fetchone()
            notifications = db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
            events = db.execute('SELECT COUNT(*) FROM timeline_events').fetchone()[0]
            idempotency = db.execute('SELECT COUNT(*) FROM mobile_idempotency').fetchone()[0]
        self.assertEqual((item['result'], item['review_status'], item['photo_urls'], item['actual_photos']),
                         (None, 0, '[]', 0))
        self.assertEqual((plan['completion_rate'], plan['status']), (50, 'active'))
        self.assertEqual((attachment['source_type'], attachment['source_id']), ('site_photo', 0))
        self.assertEqual(notifications, 0)
        self.assertEqual(events, 0)
        self.assertEqual(idempotency, 0)

    def test_vehicle_pending_returns_only_verified_business_associations(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO plan_schedules VALUES (77,2,'approved',datetime('now'))")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee_id,completion_rate,status,plan_schedule_id)
                VALUES (11,'Repair Route',2,0,'active',77)""")
            db.execute("INSERT INTO vehicles VALUES (5,'赣A10001','Utility')")
            db.execute("""INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,start_at,end_at,site_id,destination,reason,
                 created_at,status,work_order_no,rework_plan_id)
                VALUES (50,5,2,'2026-08-11 08:00:00','2026-08-11 17:00:00',1,
                        'Site A','整改补检#11用车',datetime('now'),'pending',NULL,11)""")
            db.execute("""INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,start_at,end_at,site_id,destination,reason,
                 created_at,status,work_order_no,rework_plan_id)
                VALUES (51,5,2,'2026-08-12 08:00:00','2026-08-12 17:00:00',1,
                        'Site A','工单处置',datetime('now'),'pending','WO-51',NULL)""")

        response = self.client.get('/api/audit/pending', headers={
            'Authorization': 'Bearer admin-token',
        })
        self.assertEqual(response.status_code, 200, response.json)
        vehicles = [item for item in response.json
                    if item['source_type'] == 'vehicle_application']
        self.assertEqual(len(vehicles), 2, vehicles)

        rework = next(item for item in vehicles if item['rework_plan_id'] == 11)
        self.assertEqual(rework['work_order_no'], '')
        self.assertEqual(rework['plan_schedule_id'], 77)
        self.assertEqual(rework['plan_name'], 'Repair Route')

        workorder = next(item for item in vehicles if item['work_order_no'] == 'WO-51')
        self.assertIsNone(workorder['rework_plan_id'])
        self.assertIsNone(workorder['plan_schedule_id'])
        self.assertEqual(workorder['plan_name'], '')

    def test_one_selective_decision_updates_items_and_attachments_together(self):
        self.client.get('/api/audit/pending', headers=self.headers())
        response = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'approve_ids': [201, 202],
            'reject_ids': [200],
            'approve_item_ids': [101],
            'reject_reason': 'Retake the blurred meter photo',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            attachments = {row['id']: row['review_status'] for row in db.execute(
                'SELECT id, review_status FROM operation_attachments').fetchall()}
            items = {row['id']: row for row in db.execute(
                'SELECT id, result, review_status, rework_required_at FROM insp_plan_items').fetchall()}
            notification = db.execute("""SELECT is_read FROM notifications
                WHERE source_type='inspection_review_batch' AND source_id='insp_batch_10_1'""").fetchone()
        self.assertEqual(attachments, {200: 'rejected', 201: 'approved', 202: 'approved'})
        self.assertEqual((items[100]['result'], items[100]['review_status']), ('normal', 3))
        self.assertTrue(items[100]['rework_required_at'])
        self.assertEqual((items[101]['result'], items[101]['review_status']), ('normal', 2))
        self.assertEqual(notification['is_read'], 1)

    def test_review_batch_notification_stays_open_while_decisions_remain(self):
        self.client.get('/api/audit/pending', headers=self.headers())
        response = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'approve_ids': [201], 'reject_ids': [], 'approve_item_ids': [],
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            notification = db.execute("""SELECT is_read FROM notifications
                WHERE source_type='inspection_review_batch' AND source_id='insp_batch_10_1'""").fetchone()
        self.assertEqual(notification['is_read'], 0)

    def test_multiple_rejected_inspection_photos_create_one_actionable_rework_notice(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO user_roles VALUES (2,'admin')")
        response = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'approve_ids': [], 'reject_ids': [200, 200, 201, 202],
            'reject_reason': 'Retake site evidence',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            notices = db.execute("""SELECT source_type,source_id,content,payload_json FROM notifications
                WHERE user_id=2 AND is_read=0 ORDER BY id""").fetchall()
            reviewer_notices = db.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id=1 AND source_type='inspection_rework'"
            ).fetchone()[0]
        self.assertEqual(len(notices), 1)
        self.assertEqual((notices[0]['source_type'], notices[0]['source_id']),
                         ('inspection_rework', '10'))
        self.assertIn('2 个检查项、3 张照片', notices[0]['content'])
        self.assertEqual(json.loads(notices[0]['payload_json'])['photo_count'], 3)
        self.assertNotIn('photo_review', [row['source_type'] for row in notices])
        self.assertNotIn('replacement_review', [row['source_type'] for row in notices])
        self.assertEqual(reviewer_notices, 0)
        replay = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'approve_ids': [], 'reject_ids': [200, 200, 201, 202],
            'reject_reason': 'Retake site evidence',
        })
        self.assertEqual(replay.status_code, 409, replay.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM notifications
                WHERE user_id=2 AND source_type='inspection_rework' AND is_read=0""").fetchone()[0], 1)

    def test_notification_get_retires_only_redundant_linked_photo_notices(self):
        rejected = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'approve_ids': [], 'reject_ids': [200, 201], 'reject_reason': 'Retake',
        })
        self.assertEqual(rejected.status_code, 200, rejected.json)
        with app_module.get_db() as db:
            rework = db.execute("""SELECT content FROM notifications
                WHERE user_id=2 AND source_type='inspection_rework' AND is_read=0""").fetchone()
            self.assertIn('1 个检查项、2 张照片', rework['content'])
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,
                 source_type,source_id,is_deleted,review_required)
                VALUES (999,'/uploads/independent.jpg',1,2,'Independent','independent.jpg',
                        'rejected','site_photo',0,0,0)""")
            db.executemany("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read)
                VALUES (2,?,?,?, '',0)""", [
                ('photo_review', '200', 'legacy photo'),
                ('replacement_review', '201', 'legacy replacement'),
                ('photo_review', '999', 'independent photo'),
            ])

        headers = {'Authorization': 'Bearer operator-token'}
        first = self.client.get('/api/notifications?status=unread', headers=headers)
        second = self.client.get('/api/notifications?status=unread', headers=headers)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        with app_module.get_db() as db:
            states = {row['source_id']: row['is_read'] for row in db.execute(
                """SELECT source_id,is_read FROM notifications
                    WHERE source_type IN ('photo_review','replacement_review')""").fetchall()}
            rework_count = db.execute("""SELECT COUNT(*) FROM notifications
                WHERE user_id=2 AND source_type='inspection_rework' AND is_read=0""").fetchone()[0]
        self.assertEqual(states, {'200': 1, '201': 1, '999': 0})
        self.assertEqual(rework_count, 1)
        self.assertEqual(first.json['unread_count'], second.json['unread_count'])

    def test_rework_notifications_are_separate_across_plan_and_site(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO sites VALUES (2,'Site B','S-2','water_quality',28.7,115.8)")
            db.executemany("INSERT INTO user_sites VALUES (?,2)", [(1,), (2,)])
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee_id,completion_rate,status,generate_date)
                VALUES (20,'Route B',2,100,'completed',date('now'))""")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,actual_photos,required_photos,remark,result,
                 check_time,photo_urls,review_status,execution_status)
                VALUES (300,20,2,'Second site',1,1,'','normal',datetime('now'),
                        '["/uploads/site-b.jpg"]',1,'active')""")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,
                 source_type,source_id,is_deleted,review_required,evidence_qualification)
                VALUES (300,'/uploads/site-b.jpg',2,2,'Second site','site-b.jpg','pending',
                        'inspection',300,0,1,'qualified')""")
        response = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'approve_ids': [], 'reject_ids': [200, 300], 'reject_reason': 'Retake both',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            notices = db.execute("""SELECT dedupe_key FROM notifications
                WHERE user_id=2 AND source_type='inspection_rework' AND is_read=0
                ORDER BY dedupe_key""").fetchall()
        self.assertEqual([row['dedupe_key'] for row in notices],
                         ['inspection_rework:10:1', 'inspection_rework:20:2'])

    def test_rework_notification_failure_rolls_back_rejection_and_partial_notice(self):
        def fail_after_partial(db, *args, **kwargs):
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content)
                VALUES (2,'inspection_rework','10','partial','partial')""")
            raise sqlite3.OperationalError('forced rework notification failure')

        with mock.patch.object(app_module, '_notify_inspection_rework', side_effect=fail_after_partial):
            response = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
                'approve_ids': [], 'reject_ids': [200], 'reject_reason': 'Retake',
            })
        self.assertEqual(response.status_code, 500, response.json)
        with app_module.get_db() as db:
            attachment = db.execute(
                'SELECT review_status,reject_reason FROM operation_attachments WHERE id=200').fetchone()
            item = db.execute(
                'SELECT review_status,rework_required_at FROM insp_plan_items WHERE id=100').fetchone()
            notification_count = db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
        self.assertEqual((attachment['review_status'], attachment['reject_reason']), ('pending', None))
        self.assertEqual((item['review_status'], item['rework_required_at']), (1, None))
        self.assertEqual(notification_count, 0)

    def test_redundant_notice_retirement_failure_rolls_back_rework_notice_and_rejection(self):
        with mock.patch.object(
                app_module, '_retire_redundant_inspection_photo_notifications',
                side_effect=sqlite3.OperationalError('forced retirement failure')):
            response = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
                'approve_ids': [], 'reject_ids': [200], 'reject_reason': 'Retake',
            })
        self.assertEqual(response.status_code, 500, response.json)
        with app_module.get_db() as db:
            attachment = db.execute(
                'SELECT review_status,reject_reason FROM operation_attachments WHERE id=200').fetchone()
            item = db.execute(
                'SELECT review_status,rework_required_at FROM insp_plan_items WHERE id=100').fetchone()
            notification_count = db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]
        self.assertEqual((attachment['review_status'], attachment['reject_reason']), ('pending', None))
        self.assertEqual((item['review_status'], item['rework_required_at']), (1, None))
        self.assertEqual(notification_count, 0)

    def test_unlinked_noninspection_attachment_is_not_reviewable_or_listed(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,site_id,uploader_id,description,filename,review_status,
                 source_type,source_id,is_deleted,review_required,file_type,created_at)
                VALUES (203,'/uploads/unlinked.jpg',1,2,'Site note','unlinked.jpg','pending',
                        'site_photo',0,0,1,'image',datetime('now'))""")
        pending = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(pending.status_code, 200, pending.json)
        self.assertFalse(any(203 in (item.get('attachment_ids') or []) for item in pending.json))
        response = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'attachment_ids': [203], 'action': 'reject', 'reject_reason': 'Retake note image',
        })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'ATTACHMENT_ITEM_REQUIRED')
        with app_module.get_db() as db:
            status = db.execute('SELECT review_status FROM operation_attachments WHERE id=203').fetchone()[0]
        self.assertEqual(status, 'pending')

    def test_replacement_required_item_without_pending_attachment_is_not_a_review_batch(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN evidence_status TEXT DEFAULT ''")
            db.execute("UPDATE insp_plan_items SET review_status=1, evidence_status='supplement_required' WHERE id=100")
            db.execute("UPDATE operation_attachments SET review_status='rejected' WHERE source_type='inspection' AND source_id IN (100, 101)")
        response = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(any(
            card['source_type'] == 'inspection_batch' and card.get('plan_id') == 10
            for card in response.json
        ))

    def test_legacy_per_photo_notifications_are_archived_without_new_batch(self):
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO notifications
                (user_id, source_type, source_id, title, content, is_read)
                VALUES (1, 'attachment_review', ?, 'Image review pending', '', 0)""", [
                (200,), (201,), (999,),
            ])
            app_module._coalesce_pending_attachment_notifications(db)
        with app_module.get_db() as db:
            legacy_unread = db.execute("""SELECT COUNT(*) FROM notifications
                WHERE source_type='attachment_review' AND is_read=0""").fetchone()[0]
            batches = db.execute("""SELECT source_id, is_read FROM notifications
                WHERE source_type='attachment_review_batch'""").fetchall()
        self.assertEqual(legacy_unread, 0)
        self.assertEqual([(row['source_id'], row['is_read']) for row in batches], [])


if __name__ == '__main__':
    unittest.main()
