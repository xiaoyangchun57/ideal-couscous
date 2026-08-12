import os
import json
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
        app_module._tokens['operator-token'] = {'id': 8, 'role': 'operator'}
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
                    reject_reason TEXT,
                    review_required INTEGER DEFAULT 1,
                    evidence_qualification TEXT DEFAULT 'pending',
                    evidence_reason TEXT DEFAULT '',
                    evidence_next_action TEXT DEFAULT '',
                    extra_json TEXT DEFAULT '{}'
                );
            ''')
            db.execute('INSERT INTO user_sites VALUES (7, 1)')
            db.execute('INSERT INTO user_sites VALUES (8, 1)')
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

    def test_photo_auto_review_is_retired_without_writes(self):
        response = self.client.post('/api/operation-attachments/auto-review', headers=self.headers, json={})
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json['code'], 'PHOTO_AUTO_REVIEW_RETIRED')
        db = sqlite3.connect(self.db_file.name)
        try:
            states = dict(db.execute('SELECT id, review_status FROM operation_attachments').fetchall())
        finally:
            db.close()
        self.assertEqual(states, {1: 'pending', 2: 'pending', 3: 'pending'})

    def test_retired_auto_review_does_not_disclose_site_scope(self):
        response = self.client.post(
            '/api/operation-attachments/auto-review',
            headers=self.headers,
            json={'site_id': 2},
        )

        self.assertEqual(response.status_code, 410)

    def test_retired_preview_does_not_change_review_status(self):
        response = self.client.post(
            '/api/operation-attachments/auto-review',
            headers=self.headers,
            json={'dry_run': True},
        )

        self.assertEqual(response.status_code, 410)
        db = sqlite3.connect(self.db_file.name)
        try:
            states = dict(db.execute('SELECT id, review_status FROM operation_attachments').fetchall())
        finally:
            db.close()
        self.assertEqual(states, {1: 'pending', 2: 'pending', 3: 'pending'})

    def test_legacy_unlinked_upload_does_not_trust_client_capture_time(self):
        response = self.client.post(
            '/api/inspection/photos/upload',
            headers={'Authorization': 'Bearer operator-token'},
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
        self.assertIsNone(response.json['taken_at'])
        with app_module.get_db() as db:
            row = db.execute(
                'SELECT source_id,review_required,extra_json FROM operation_attachments '
                'WHERE id=?', (response.json['id'],)).fetchone()
        self.assertEqual((row['source_id'], row['review_required']), (0, 0))
        self.assertEqual(json.loads(row['extra_json'])['material_role'], 'supplement')
        self.assertEqual(response.json['is_flagged'], 0)


if __name__ == '__main__':
    unittest.main()
