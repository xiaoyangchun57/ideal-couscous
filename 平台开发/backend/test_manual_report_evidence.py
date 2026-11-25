import os
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class ManualReportEvidenceTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
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
        app_module._tokens['operator-token'] = {'id': 9, 'role': 'operator', 'real_name': 'Operator'}
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, role TEXT, real_name TEXT,
                    status TEXT, phone TEXT DEFAULT ''
                );
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                INSERT INTO sites VALUES (7, 'A'), (8, 'B'), (10, 'C');
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE manual_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, report_type TEXT, description TEXT,
                    photo_urls TEXT, gps_lat REAL, gps_lng REAL, reporter_id INTEGER, reported_at TEXT,
                    order_no TEXT, status TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT, site_id INTEGER, source TEXT,
                    event_type TEXT, level TEXT, title TEXT, description TEXT, assignee TEXT, status TEXT
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, metric TEXT, value REAL, level TEXT,
                    message TEXT, status TEXT, related_order_no TEXT, flow_type TEXT, created_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, stored_path TEXT, site_id INTEGER, uploader_id INTEGER,
                    source_type TEXT, source_id INTEGER, description TEXT, extra_json TEXT DEFAULT '{}',
                    is_deleted INTEGER DEFAULT 0
                );
            ''')
            db.execute("""INSERT INTO users (id,role,real_name,status,phone)
                VALUES (9,'operator','Operator','active','')""")
            db.execute('INSERT INTO user_sites VALUES (9, 7)')
            db.execute('INSERT INTO user_sites VALUES (9, 8)')
            db.execute("INSERT INTO user_roles VALUES (9, 'operator')")
            db.execute("""INSERT INTO operation_attachments
                (stored_path, site_id, uploader_id, source_type, source_id, description, extra_json)
                VALUES ('/uploads/site_photos/a.jpg', 7, 9, 'site_photo', 0, 'pending',
                        '{"material_role":"pending_manual_report","capture_context":"manual_report"}')""")
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    def test_submitting_report_promotes_uploaded_photo_to_report_evidence(self):
        response = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json={
            'site_id': 7, 'report_type': 'equipment', 'description': 'Pump failure',
            'photo_urls': ['/uploads/site_photos/a.jpg'],
        })
        self.assertEqual(response.status_code, 201, response.json)
        report_id = response.json['id']
        db = sqlite3.connect(self.db_path)
        try:
            attachment = db.execute(
                'SELECT source_type, source_id, extra_json FROM operation_attachments'
            ).fetchone()
            self.assertEqual(tuple(attachment[:2]), ('manual_report', report_id))
            metadata = json.loads(attachment[2])
            self.assertEqual(metadata['material_role'], 'formal')
            self.assertEqual(metadata['capture_context'], 'manual_report')
            assignee = db.execute('SELECT assignee FROM work_orders').fetchone()[0]
            self.assertEqual(assignee, 'Operator')
        finally:
            db.close()

    def test_report_requires_at_least_one_photo(self):
        response = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json={
            'site_id': 7, 'report_type': 'equipment', 'description': 'Pump failure', 'photo_urls': [],
        })
        self.assertEqual(response.status_code, 400)

    def test_repeat_submission_returns_existing_report_and_order(self):
        payload = {
            'site_id': 7, 'report_type': 'equipment', 'description': 'Pump failure',
            'photo_urls': ['/uploads/site_photos/a.jpg'],
        }
        first = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json=payload)
        with app_module.get_db() as db:
            db.execute("UPDATE manual_reports SET reported_at='2020-01-01 00:00:00' WHERE id=?", (first.json['id'],))
        second = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json=payload)
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertTrue(second.json['deduplicated'])
        self.assertEqual(second.json['id'], first.json['id'])
        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM manual_reports').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0], 1)
        finally:
            db.close()

    def test_correction_preserves_capture_and_rejects_foreign_or_formal_photos(self):
        payload = {'site_id': 8, 'report_type': 'equipment', 'description': 'Correction',
                   'photo_urls': ['/uploads/site_photos/a.jpg']}
        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET extra_json=? WHERE id=1", (
                json.dumps({'material_role': 'pending_manual_report', 'capture_session_id': 123}),))
        first = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json=payload)
        self.assertEqual(first.status_code, 201, first.json)
        retry = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json=payload)
        self.assertEqual(retry.status_code, 200, retry.json)
        with app_module.get_db() as db:
            photo = db.execute('SELECT * FROM operation_attachments WHERE id=1').fetchone()
            extra = json.loads(photo['extra_json'])
            self.assertEqual((photo['site_id'], photo['stored_path']), (8, '/uploads/site_photos/a.jpg'))
            self.assertEqual((extra['original_site_id'], extra['capture_session_id']), (7, 123))
            self.assertEqual(extra['site_correction']['to_site_id'], 8)
            self.assertEqual(db.execute('SELECT site_id FROM work_orders').fetchone()[0], 8)
        payload['description'] = 'Another report'
        denied = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json=payload)
        self.assertEqual(denied.status_code, 409, denied.json)
        payload['site_id'] = 10
        denied = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json=payload)
        self.assertEqual(denied.status_code, 403, denied.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM manual_reports').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0], 1)

    def test_mixed_photo_ownership_rejects_entire_report_without_changes(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (stored_path, site_id, uploader_id, source_type, source_id, extra_json)
                VALUES ('/uploads/site_photos/foreign.jpg', 7, 10, 'site_photo', 0,
                        '{"material_role":"pending_manual_report"}')""")
        response = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json={
            'site_id': 8, 'report_type': 'equipment', 'description': 'Mixed ownership',
            'photo_urls': ['/uploads/site_photos/a.jpg', '/uploads/site_photos/foreign.jpg'],
        })
        self.assertEqual(response.status_code, 409, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM manual_reports').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0], 0)
            photos = db.execute('SELECT site_id, source_type, source_id FROM operation_attachments ORDER BY id').fetchall()
            self.assertEqual([tuple(photo) for photo in photos], [(7, 'site_photo', 0), (7, 'site_photo', 0)])

    def test_three_photos_move_together_without_losing_capture_identity(self):
        paths = ['/uploads/site_photos/a.jpg', '/uploads/site_photos/b.jpg', '/uploads/site_photos/c.jpg']
        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET extra_json=? WHERE id=1", (
                json.dumps({'material_role': 'pending_manual_report', 'capture_session_id': 101}),))
            for index, path in enumerate(paths[1:], start=102):
                db.execute('''INSERT INTO operation_attachments
                    (stored_path,site_id,uploader_id,source_type,source_id,extra_json)
                    VALUES (?,7,9,'site_photo',0,?)''', (path, json.dumps({
                        'material_role': 'pending_manual_report', 'capture_session_id': index,
                    })))
        response = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json={
            'site_id': 8, 'report_type': 'equipment', 'description': 'three photos', 'photo_urls': paths,
        })
        self.assertEqual(response.status_code, 201, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT site_id FROM manual_reports').fetchone()[0], 8)
            self.assertEqual(db.execute('SELECT site_id FROM work_orders').fetchone()[0], 8)
            photos = db.execute('SELECT * FROM operation_attachments ORDER BY id').fetchall()
            self.assertEqual([row['stored_path'] for row in photos], paths)
            self.assertEqual([row['site_id'] for row in photos], [8, 8, 8])
            self.assertEqual([json.loads(row['extra_json'])['capture_session_id'] for row in photos], [101, 102, 103])
            self.assertTrue(all(json.loads(row['extra_json'])['original_site_id'] == 7 for row in photos))


if __name__ == '__main__':
    unittest.main()
