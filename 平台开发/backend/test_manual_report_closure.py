import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class ManualReportClosureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp.close()
        self.db_path = tmp.name
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

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'operator-token': {'id': 9, 'role': 'operator', 'real_name': '现场人员'},
            'manager-token': {'id': 2, 'role': 'manager', 'real_name': '主管'},
            'admin-token': {'id': 1, 'role': 'admin', 'real_name': '管理员'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, real_name TEXT, openid TEXT DEFAULT '');
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, status TEXT, related_alert_id INTEGER,
                    used_parts TEXT, site_id INTEGER, check_in_time TEXT, resolved_at TEXT, assignee TEXT
                );
                CREATE TABLE manual_reports (
                    id INTEGER PRIMARY KEY, site_id INTEGER, status TEXT, order_no TEXT,
                    verification_note TEXT DEFAULT '', verified_by INTEGER, verified_at TEXT,
                    resolved_at TEXT, archived_by INTEGER, archived_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER, file_type TEXT,
                    is_deleted INTEGER DEFAULT 0, review_status TEXT, reviewer_id INTEGER,
                    reviewed_at TEXT, reject_reason TEXT, is_flagged INTEGER DEFAULT 0,
                    flag_reason TEXT DEFAULT '', taken_at TEXT, duplicate_of_id INTEGER
                );
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, status TEXT, resolved_at TEXT,
                    resolve_reason TEXT, site_id INTEGER, metric TEXT);
                CREATE TABLE hotline_events (id INTEGER PRIMARY KEY, related_order_no TEXT, status TEXT);
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT
                );
            ''')
            db.executemany('INSERT INTO users (id, role, real_name) VALUES (?,?,?)', [
                (1, 'admin', '管理员'), (2, 'manager', '主管'), (9, 'operator', '现场人员'),
            ])
            db.execute('INSERT INTO user_sites VALUES (9, 1)')
            db.execute("INSERT INTO work_orders VALUES (1, 'MR202607250001', 'reviewing', NULL, '', 1, NULL, NULL, '现场人员')")
            db.execute("INSERT INTO manual_reports (id, site_id, status, order_no) VALUES (7, 1, 'dispatched', 'MR202607250001')")
            db.execute("INSERT INTO operation_attachments (id, source_type, source_id, file_type, taken_at) VALUES (1, 'workorder', 1, 'image', '2026-07-25 10:00:00')")
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        os.unlink(self.db_path)

    def headers(self, token):
        return {'Authorization': f'Bearer {token}'}

    def test_verification_requires_approver_and_preserves_workorder(self):
        denied = self.client.post('/api/manual-reports/7/verify', headers=self.headers('operator-token'))
        self.assertEqual(denied.status_code, 403)

        missing_note = self.client.post('/api/manual-reports/7/verify', headers=self.headers('manager-token'), json={})
        self.assertEqual(missing_note.status_code, 400)

        verified = self.client.post('/api/manual-reports/7/verify', headers=self.headers('manager-token'), json={'note': '现场描述已核实'})
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json['status'], 'verified')
        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute('SELECT status FROM manual_reports WHERE id=7').fetchone()[0], 'verified')
            self.assertEqual(db.execute("SELECT status FROM work_orders WHERE order_no='MR202607250001'").fetchone()[0], 'reviewing')
        finally:
            db.close()

    def test_close_resolves_report_then_manager_can_archive(self):
        closed = self.client.post('/api/workorders/MR202607250001/approve', headers=self.headers('admin-token'), json={})
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json['status'], 'closed')
        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute('SELECT status FROM manual_reports WHERE id=7').fetchone()[0], 'resolved')
        finally:
            db.close()

        archived = self.client.post('/api/manual-reports/7/archive', headers=self.headers('manager-token'))
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json['status'], 'archived')

    def test_cannot_archive_unresolved_report(self):
        archived = self.client.post('/api/manual-reports/7/archive', headers=self.headers('manager-token'))
        self.assertEqual(archived.status_code, 400)


if __name__ == '__main__':
    unittest.main()
