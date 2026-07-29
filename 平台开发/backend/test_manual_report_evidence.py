import os
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
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE manual_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, report_type TEXT, description TEXT,
                    photo_urls TEXT, gps_lat REAL, gps_lng REAL, reporter_id INTEGER, reported_at TEXT,
                    order_no TEXT, status TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT, site_id INTEGER, source TEXT,
                    event_type TEXT, level TEXT, title TEXT, description TEXT, status TEXT
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, metric TEXT, value REAL, level TEXT,
                    message TEXT, status TEXT, related_order_no TEXT, flow_type TEXT, created_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, stored_path TEXT, site_id INTEGER, uploader_id INTEGER,
                    source_type TEXT, source_id INTEGER, description TEXT
                );
            ''')
            db.execute('INSERT INTO user_sites VALUES (9, 7)')
            db.execute("""INSERT INTO operation_attachments
                (stored_path, site_id, uploader_id, source_type, source_id, description)
                VALUES ('/uploads/site_photos/a.jpg', 7, 9, 'site_photo', 0, 'pending')""")
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
            attachment = db.execute('SELECT source_type, source_id FROM operation_attachments').fetchone()
            self.assertEqual(tuple(attachment), ('manual_report', report_id))
        finally:
            db.close()

    def test_report_requires_at_least_one_photo(self):
        response = self.client.post('/api/manual-reports', headers={'Authorization': 'Bearer operator-token'}, json={
            'site_id': 7, 'report_type': 'equipment', 'description': 'Pump failure', 'photo_urls': [],
        })
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
