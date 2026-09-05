import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class MultiRoleNotificationFlowTest(unittest.TestCase):
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

        self.temporary_db = temporary_db
        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'dual-admin-token': {'id': 1, 'role': 'admin', 'roles': ['admin', 'operator']},
            'secondary-reviewer-token': {'id': 2, 'role': 'operator', 'roles': ['operator', 'reviewer']},
            'out-of-scope-reviewer-token': {'id': 3, 'role': 'reviewer', 'roles': ['reviewer']},
            'operator-token': {'id': 4, 'role': 'operator', 'roles': ['operator']},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, role TEXT, real_name TEXT, status TEXT DEFAULT 'active'
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, code TEXT);
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, assignee_id INTEGER, completion_rate REAL, status TEXT
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, item_name TEXT,
                    result TEXT, review_status INTEGER, reviewer_id INTEGER, review_time TEXT,
                    execution_status TEXT, photo_urls TEXT, check_out_time TEXT, completed_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, stored_path TEXT, site_id INTEGER, uploader_id INTEGER,
                    description TEXT, filename TEXT, review_status TEXT, reviewer_id INTEGER,
                    reviewed_at TEXT, reject_reason TEXT, source_type TEXT, source_id INTEGER,
                    is_deleted INTEGER DEFAULT 0, review_required INTEGER DEFAULT 1
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, metric TEXT, level TEXT, status TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id INTEGER, title TEXT, content TEXT, is_read INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE data_reviews (
                    id INTEGER PRIMARY KEY, site_id INTEGER, metric TEXT, status TEXT,
                    auto_result TEXT, smart_result TEXT, manual_result TEXT, manual_reason TEXT,
                    reviewer_id INTEGER, reviewed_at TEXT, value REAL, recorded_at TEXT
                );
                INSERT INTO users (id,role,real_name) VALUES
                    (1,'admin','dual admin'), (2,'operator','secondary reviewer'),
                    (3,'reviewer','outside reviewer'), (4,'operator','operator');
                INSERT INTO user_roles VALUES
                    (1,'admin'), (1,'operator'), (2,'operator'), (2,'reviewer'),
                    (3,'reviewer'), (4,'operator');
                INSERT INTO user_sites VALUES (2,1), (3,2), (4,1);
                INSERT INTO sites (id,name) VALUES (1,'site one'), (2,'site two');
                INSERT INTO data_reviews (id,site_id,metric,status,auto_result,smart_result)
                    VALUES (100,1,'ph','smart_reviewed','reject','suspicious');
                INSERT INTO data_reviews (id,site_id,metric,status,auto_result,smart_result)
                    VALUES (200,2,'cod','smart_reviewed','reject','suspicious');
                INSERT INTO insp_plans VALUES (10,4,0,'active'), (20,4,0,'active'), (30,4,0,'active');
                INSERT INTO insp_plan_items VALUES
                    (10,10,1,'site one item','normal',1,NULL,NULL,'active','["/uploads/site-one.jpg"]',NULL,NULL),
                    (20,20,2,'site two item','normal',1,NULL,NULL,'active','["/uploads/site-two.jpg"]',NULL,NULL),
                    (30,30,NULL,'site-less item','normal',1,NULL,NULL,'active','["/uploads/site-less.jpg"]',NULL,NULL);
                INSERT INTO operation_attachments VALUES
                    (10,'/uploads/site-one.jpg',1,4,'site one item','site-one.jpg','pending',NULL,NULL,NULL,'inspection',10,0,1),
                    (20,'/uploads/site-two.jpg',2,4,'site two item','site-two.jpg','pending',NULL,NULL,NULL,'inspection',20,0,1),
                    (30,'/uploads/site-less.jpg',NULL,4,'site-less item','site-less.jpg','pending',NULL,NULL,NULL,'inspection',30,0,1);
                INSERT INTO notifications (user_id,source_type,source_id,title,content) VALUES
                    (2,'inspection_review_batch','insp_batch_10_1','site one pending','review'),
                    (2,'inspection_review_batch','insp_batch_20_2','site two pending','review');
                ALTER TABLE operation_attachments ADD COLUMN evidence_qualification TEXT DEFAULT 'qualified';
                ALTER TABLE operation_attachments ADD COLUMN evidence_reason TEXT DEFAULT '';
                ALTER TABLE operation_attachments ADD COLUMN evidence_next_action TEXT DEFAULT '';
                ALTER TABLE insp_plan_items ADD COLUMN required_photos INTEGER DEFAULT 1;
            ''')
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

    def test_l3_notification_honors_secondary_roles_site_scope_and_dedupes(self):
        with self.temporary_db() as db:
            review = db.execute('SELECT * FROM data_reviews WHERE id=100').fetchone()
            app_module._notify_review_l3(db, review)
            app_module._notify_review_l3(db, review)
            db.execute("UPDATE notifications SET is_read=1 WHERE source_type='data_review'")
            app_module._notify_review_l3(db, review)
            recipients = db.execute(
                "SELECT user_id FROM notifications WHERE source_type='data_review' ORDER BY user_id"
            ).fetchall()

        self.assertEqual([row['user_id'] for row in recipients], [1, 2])

    def test_only_review_roles_can_review_and_secondary_reviewer_is_site_scoped(self):
        denied = self.client.post('/api/data-reviews/100/manual-review',
                                  headers=self.headers('operator-token'), json={'action': 'approve'})
        self.assertEqual(denied.status_code, 403, denied.json)

        outside = self.client.post('/api/data-reviews/100/manual-review',
                                   headers=self.headers('out-of-scope-reviewer-token'), json={'action': 'approve'})
        self.assertEqual(outside.status_code, 403, outside.json)

        allowed = self.client.post('/api/data-reviews/100/manual-review',
                                   headers=self.headers('secondary-reviewer-token'), json={'action': 'approve'})
        self.assertEqual(allowed.status_code, 200, allowed.json)
        with self.temporary_db() as db:
            row = db.execute('SELECT status, reviewer_id FROM data_reviews WHERE id=100').fetchone()
        self.assertEqual((row['status'], row['reviewer_id']), ('archived', 2))

    def test_batch_rejects_out_of_scope_ids_without_partial_write(self):
        response = self.client.post('/api/data-reviews/batch-manual-review',
                                    headers=self.headers('secondary-reviewer-token'),
                                    json={'ids': [100, 200], 'action': 'approve'})
        self.assertEqual(response.status_code, 403, response.json)
        with self.temporary_db() as db:
            rows = db.execute('SELECT id, status, reviewer_id FROM data_reviews ORDER BY id').fetchall()
        self.assertEqual(
            [(row['id'], row['status'], row['reviewer_id']) for row in rows],
            [(100, 'smart_reviewed', None), (200, 'smart_reviewed', None)],
        )

    def test_admin_operator_can_self_review_and_closes_its_pending_notification(self):
        with self.temporary_db() as db:
            db.execute("""INSERT INTO notifications (user_id,source_type,source_id,title,content)
                VALUES (1,'data_review',100,'pending','review')""")

        response = self.client.post('/api/data-reviews/100/manual-review',
                                    headers=self.headers('dual-admin-token'), json={'action': 'approve'})
        self.assertEqual(response.status_code, 200, response.json)
        with self.temporary_db() as db:
            review = db.execute('SELECT reviewer_id FROM data_reviews WHERE id=100').fetchone()
            notice = db.execute("""SELECT is_read FROM notifications
                WHERE user_id=1 AND source_type='data_review' AND source_id=100""").fetchone()
        self.assertEqual(review['reviewer_id'], 1)
        self.assertEqual(notice['is_read'], 1)

    def test_data_review_stats_do_not_leak_other_sites(self):
        response = self.client.get('/api/data-reviews/stats', headers=self.headers('secondary-reviewer-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['total'], 1)
        self.assertEqual([row['site_id'] for row in response.json['by_site']], [1])

    def test_data_review_list_and_direct_target_are_site_scoped(self):
        scoped = self.client.get('/api/data-reviews?per_page=50',
                                 headers=self.headers('secondary-reviewer-token'))
        self.assertEqual(scoped.status_code, 200, scoped.json)
        self.assertEqual([row['id'] for row in scoped.json['items']], [100])

        direct = self.client.get('/api/data-reviews/100',
                                 headers=self.headers('secondary-reviewer-token'))
        self.assertEqual(direct.status_code, 200, direct.json)
        self.assertEqual(direct.json['id'], 100)

        hidden = self.client.get('/api/data-reviews/200',
                                 headers=self.headers('secondary-reviewer-token'))
        self.assertEqual(hidden.status_code, 404, hidden.json)

        admin = self.client.get('/api/data-reviews/200',
                                headers=self.headers('dual-admin-token'))
        self.assertEqual(admin.status_code, 200, admin.json)
        self.assertEqual(admin.json['id'], 200)

    def test_attachment_review_rejects_mixed_site_batch_atomically_and_admin_can_cross_site(self):
        payload = {
            'approve_ids': [10, 20],
            'reject_ids': [],
            'approve_item_ids': [10, 20],
        }
        denied = self.client.post('/api/operation-attachments/review',
                                  headers=self.headers('secondary-reviewer-token'), json=payload)
        self.assertEqual(denied.status_code, 403, denied.json)
        with self.temporary_db() as db:
            attachments = db.execute('SELECT id,review_status,reviewer_id FROM operation_attachments ORDER BY id').fetchall()
            items = db.execute('SELECT id,review_status,reviewer_id FROM insp_plan_items ORDER BY id').fetchall()
            notices = db.execute("""SELECT source_id,is_read FROM notifications
                WHERE source_type='inspection_review_batch' ORDER BY source_id""").fetchall()
        self.assertEqual([(row['id'], row['review_status'], row['reviewer_id']) for row in attachments],
                         [(10, 'pending', None), (20, 'pending', None), (30, 'pending', None)])
        self.assertEqual([(row['id'], row['review_status'], row['reviewer_id']) for row in items],
                         [(10, 1, None), (20, 1, None), (30, 1, None)])
        self.assertEqual([(row['source_id'], row['is_read']) for row in notices],
                         [('insp_batch_10_1', 0), ('insp_batch_20_2', 0)])

        site_less = self.client.post('/api/operation-attachments/review',
                                     headers=self.headers('secondary-reviewer-token'), json={
                                         'approve_ids': [10, 30],
                                         'reject_ids': [],
                                         'approve_item_ids': [10, 30],
                                     })
        self.assertEqual(site_less.status_code, 403, site_less.json)
        with self.temporary_db() as db:
            attachments = db.execute('SELECT id,review_status,reviewer_id FROM operation_attachments ORDER BY id').fetchall()
            items = db.execute('SELECT id,review_status,reviewer_id FROM insp_plan_items ORDER BY id').fetchall()
        self.assertEqual([(row['id'], row['review_status'], row['reviewer_id']) for row in attachments],
                         [(10, 'pending', None), (20, 'pending', None), (30, 'pending', None)])
        self.assertEqual([(row['id'], row['review_status'], row['reviewer_id']) for row in items],
                         [(10, 1, None), (20, 1, None), (30, 1, None)])

        allowed = self.client.post('/api/operation-attachments/review',
                                   headers=self.headers('dual-admin-token'), json=payload)
        self.assertEqual(allowed.status_code, 200, allowed.json)
        with self.temporary_db() as db:
            attachments = db.execute('SELECT id,review_status,reviewer_id FROM operation_attachments ORDER BY id').fetchall()
            items = db.execute('SELECT id,review_status,reviewer_id FROM insp_plan_items ORDER BY id').fetchall()
        self.assertEqual([(row['id'], row['review_status'], row['reviewer_id']) for row in attachments],
                         [(10, 'approved', 1), (20, 'approved', 1), (30, 'pending', None)])
        self.assertEqual([(row['id'], row['review_status'], row['reviewer_id']) for row in items],
                         [(10, 2, 1), (20, 2, 1), (30, 1, None)])


if __name__ == '__main__':
    unittest.main()
