import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class AttachmentAutoReviewRouteTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_file.close()
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)

        @contextmanager
        def temporary_db():
            db = sqlite3.connect(self.db_file.name)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._tokens['reviewer-token'] = {'id': 7, 'role': 'reviewer'}
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, gps_lat REAL, gps_lng REAL);
                CREATE TABLE photo_requirements (id INTEGER PRIMARY KEY, review_required INTEGER);
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY,
                    filename TEXT,
                    stored_path TEXT,
                    file_type TEXT,
                    site_id INTEGER,
                    source_id INTEGER,
                    uploader_id INTEGER,
                    uploader_name TEXT,
                    gps_lat REAL,
                    gps_lng REAL,
                    taken_at TEXT,
                    category TEXT,
                    description TEXT,
                    requirement_id INTEGER,
                    is_deleted INTEGER DEFAULT 0,
                    is_flagged INTEGER DEFAULT 0,
                    review_status TEXT DEFAULT 'pending',
                    source_type TEXT,
                    reviewer_id INTEGER,
                    reviewed_at TEXT,
                    review_action TEXT DEFAULT '',
                    reject_reason TEXT
                );
            ''')
            db.execute('INSERT INTO user_sites VALUES (7, 1)')
            db.execute('INSERT INTO sites VALUES (1, 28.6833, 115.7333)')
            db.execute('INSERT INTO photo_requirements VALUES (10, 1)')
            db.executemany(
                'INSERT INTO operation_attachments (id, site_id, is_flagged, review_status, source_type) VALUES (?,?,?,?,?)',
                [
                    (1, 1, 0, 'pending', 'site_photo'),
                    (2, 2, 0, 'pending', 'site_photo'),
                    (3, 1, 1, 'pending', 'site_photo'),
                ],
            )
        self.client = app_module.app.test_client()
        self.headers = {'Authorization': 'Bearer reviewer-token'}

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_file.name)

    def test_reviewer_auto_passes_only_assigned_sites_and_keeps_flagged_photos(self):
        response = self.client.post('/api/operation-attachments/auto-review', headers=self.headers, json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['approved'], 1)
        self.assertEqual(response.json['remaining_flagged'], 1)
        self.assertEqual(response.json['site_ids'], [1])
        db = sqlite3.connect(self.db_file.name)
        try:
            states = dict(db.execute('SELECT id, review_status FROM operation_attachments').fetchall())
            review_action = db.execute('SELECT review_action FROM operation_attachments WHERE id=1').fetchone()[0]
        finally:
            db.close()
        self.assertEqual(states, {1: 'approved', 2: 'pending', 3: 'pending'})
        self.assertEqual(review_action, 'auto_pass_normal')

    def test_reviewer_cannot_request_a_site_outside_assigned_scope(self):
        response = self.client.post(
            '/api/operation-attachments/auto-review',
            headers=self.headers,
            json={'site_id': 2},
        )

        self.assertEqual(response.status_code, 403)

    def test_preview_reports_scope_without_changing_review_status(self):
        response = self.client.post(
            '/api/operation-attachments/auto-review',
            headers=self.headers,
            json={'dry_run': True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['preview'])
        self.assertEqual(response.json['approved'], 1)
        self.assertEqual(response.json['remaining_flagged'], 1)
        db = sqlite3.connect(self.db_file.name)
        try:
            states = dict(db.execute('SELECT id, review_status FROM operation_attachments').fetchall())
        finally:
            db.close()
        self.assertEqual(states, {1: 'pending', 2: 'pending', 3: 'pending'})

    def test_inspection_photo_uses_capture_time_for_flag_evaluation(self):
        response = self.client.post(
            '/api/inspection/photos/upload',
            headers=self.headers,
            json={
                'site_id': 1,
                'requirement_id': 10,
                'filename': 'inspection.jpg',
                'stored_path': '/uploads/inspection.jpg',
                'uploader_id': 7,
                'uploader_name': 'Reviewer',
                'gps_lat': 28.6835,
                'gps_lng': 115.7335,
                'taken_at': '2026-07-25 10:00:00',
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['taken_at'], '2026-07-25 10:00:00')
        self.assertEqual(response.json['is_flagged'], 0)


if __name__ == '__main__':
    unittest.main()
