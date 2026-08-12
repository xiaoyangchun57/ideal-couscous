import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager


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
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT, status TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, submitted_at TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_name TEXT, assignee_id INTEGER,
                    completion_rate REAL, status TEXT, plan_schedule_id INTEGER
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
                    dedupe_key TEXT, payload_json TEXT
                );
                INSERT INTO users VALUES (1, 'Reviewer', 'reviewer', 'active');
                INSERT INTO users VALUES (2, 'Operator', 'operator', 'active');
                INSERT INTO user_sites VALUES (1, 1);
                INSERT INTO sites VALUES (1, 'Site A');
                INSERT INTO insp_plans
                    (id,plan_name,assignee_id,completion_rate,status)
                    VALUES (10, 'Route A', 2, 100, 'completed');
                INSERT INTO insp_plan_items VALUES
                    (100, 10, 1, 'Analyzer', 2, 1, 'first item', 'normal', datetime('now'),
                     '["/uploads/a.jpg", "/uploads/b.jpg"]', 1, 'active', '', NULL, NULL, datetime('now'), NULL, NULL),
                    (101, 10, 1, 'Pump', 1, 1, 'second item', 'normal', datetime('now'),
                     '["/uploads/c.jpg"]', 1, 'active', '', NULL, NULL, datetime('now'), NULL, NULL);
                INSERT INTO operation_attachments VALUES
                    (200, '/uploads/a.jpg', 1, 2, 'Analyzer', 'a.jpg', 'pending', NULL, NULL, NULL,
                     'inspection', 100, 0, 1, datetime('now'), 'watermark-a', 'meter', '{"ocr_status":"verified"}',
                     datetime('now'), 1, 'blurred', 'sharpness', 'camera', 'image', '', '', NULL),
                    (201, '/uploads/b.jpg', 1, 2, 'Analyzer', 'b.jpg', 'pending', NULL, NULL, NULL,
                     'inspection', 100, 0, 1, datetime('now'), 'watermark-b', 'meter', '{"ocr_status":"verified"}',
                     datetime('now'), 0, '', '', 'camera', 'image', '', '', NULL),
                    (202, '/uploads/c.jpg', 1, 2, 'Pump', 'c.jpg', 'pending', NULL, NULL, NULL,
                     'inspection', 101, 0, 1, datetime('now'), 'watermark-c', 'pump', '{"ocr_status":"unreadable"}',
                     datetime('now'), 1, 'missing watermark', 'watermark', 'camera', 'image', '', '', NULL);
                ALTER TABLE operation_attachments ADD COLUMN evidence_qualification TEXT DEFAULT 'qualified';
                ALTER TABLE operation_attachments ADD COLUMN evidence_reason TEXT DEFAULT '';
                ALTER TABLE operation_attachments ADD COLUMN evidence_next_action TEXT DEFAULT '';
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
        for photo in card['attachment_details']:
            self.assertTrue(photo['item_id'])
            self.assertTrue(photo['item_name'])
        with app_module.get_db() as db:
            notices = db.execute("""SELECT COUNT(*) FROM notifications
                WHERE source_type='inspection_review_batch' AND source_id='insp_batch_10_1'""").fetchone()[0]
        self.assertEqual(notices, 1)

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
        response = self.client.post('/api/operation-attachments/review', headers=self.headers(), json={
            'approve_ids': [], 'reject_ids': [200, 201, 202],
            'reject_reason': 'Retake site evidence',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            notices = db.execute("""SELECT source_type,source_id,content FROM notifications
                WHERE user_id=2 AND is_read=0 ORDER BY id""").fetchall()
        self.assertEqual(len(notices), 1)
        self.assertEqual((notices[0]['source_type'], notices[0]['source_id']),
                         ('inspection_rework', '10'))
        self.assertIn('2', notices[0]['content'])

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
