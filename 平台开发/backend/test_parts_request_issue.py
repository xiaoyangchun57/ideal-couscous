import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class PartsRequestIssueTest(unittest.TestCase):
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
            'operator-token': {'id': 9, 'role': 'operator', 'username': 'operator', 'real_name': '现场人员'},
            'other-token': {'id': 8, 'role': 'operator', 'username': 'other', 'real_name': '其他人员'},
            'manager-token': {'id': 2, 'role': 'manager', 'username': 'manager', 'real_name': '主管'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, real_name TEXT, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE spare_parts_inventory (
                    id INTEGER PRIMARY KEY, part_code TEXT, part_name TEXT, quantity INTEGER,
                    updated_at TEXT, unit TEXT
                );
                CREATE TABLE parts_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, requester_id INTEGER,
                    site_id INTEGER, work_order_no TEXT, request_no TEXT, source TEXT, reason TEXT,
                    status TEXT, approver_id INTEGER, approve_comment TEXT, approved_at TEXT,
                    created_at TEXT
                );
                CREATE TABLE parts_request_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, part_sku TEXT,
                    quantity INTEGER, part_id INTEGER
                );
                CREATE TABLE parts_request_reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, part_id INTEGER,
                    requested_quantity INTEGER, reserved_quantity INTEGER, issued_quantity INTEGER,
                    status TEXT, created_at TEXT, updated_at TEXT
                );
                CREATE TABLE inventory_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, part_id INTEGER, type TEXT, quantity INTEGER,
                    ref_type TEXT, ref_id INTEGER, operator TEXT, remark TEXT
                );
                CREATE TABLE spare_part_requests (
                    id INTEGER PRIMARY KEY, request_no TEXT, site_id INTEGER, applicant TEXT,
                    part_name TEXT, quantity INTEGER, reason TEXT, work_order_no TEXT,
                    status TEXT, created_at TEXT
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?)', [
                (9, 'operator', '现场人员', 'operator'), (8, 'other', '其他人员', 'operator'),
                (2, 'manager', '主管', 'manager'),
            ])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(9, 1), (8, 1)])
            db.execute("INSERT INTO spare_parts_inventory VALUES (1, 'P-001', '采样泵', 10, NULL, '件')")
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

    def create_request(self, quantity):
        response = self.client.post('/api/parts/requests', headers=self.headers('operator-token'), json={
            'site_id': 1, 'spare_part_id': 1, 'quantity': quantity, 'reason': '现场更换',
        })
        self.assertEqual(response.status_code, 200)
        return response.json['id']

    def read_one(self, sql, params=()):
        db = sqlite3.connect(self.db_path)
        try:
            return db.execute(sql, params).fetchone()
        finally:
            db.close()

    def test_approval_reserves_but_only_onsite_issue_deducts_stock(self):
        request_id = self.create_request(6)
        approved = self.client.put(f'/api/parts/requests/{request_id}/approve', headers=self.headers('manager-token'))
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.read_one('SELECT quantity FROM spare_parts_inventory WHERE id=1')[0], 10)
        self.assertEqual(self.read_one('SELECT reserved_quantity FROM parts_request_reservations WHERE request_id=?', (request_id,))[0], 6)

        issued = self.client.post(f'/api/parts/requests/{request_id}/issue', headers=self.headers('operator-token'), json={
            'items': [{'part_id': 1, 'quantity': 4}],
        })
        self.assertEqual(issued.status_code, 200)
        self.assertEqual(issued.json['remaining_reserved_quantity'], 2)
        self.assertEqual(self.read_one('SELECT quantity FROM spare_parts_inventory WHERE id=1')[0], 6)
        self.assertEqual(self.read_one('SELECT issued_quantity FROM parts_request_reservations WHERE request_id=?', (request_id,))[0], 4)

        over_issue = self.client.post(f'/api/parts/requests/{request_id}/issue', headers=self.headers('operator-token'), json={
            'items': [{'part_id': 1, 'quantity': 3}],
        })
        self.assertEqual(over_issue.status_code, 400)
        self.assertEqual(self.read_one('SELECT quantity FROM spare_parts_inventory WHERE id=1')[0], 6)

    def test_reservations_cannot_exceed_available_inventory_and_owner_is_enforced(self):
        first_id = self.create_request(7)
        self.assertEqual(self.client.put(f'/api/parts/requests/{first_id}/approve', headers=self.headers('manager-token')).status_code, 200)
        second_id = self.create_request(4)
        insufficient = self.client.put(f'/api/parts/requests/{second_id}/approve', headers=self.headers('manager-token'))
        self.assertEqual(insufficient.status_code, 400)

        forbidden = self.client.post(f'/api/parts/requests/{first_id}/issue', headers=self.headers('other-token'), json={
            'items': [{'part_id': 1, 'quantity': 1}],
        })
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(self.read_one('SELECT quantity FROM spare_parts_inventory WHERE id=1')[0], 10)

    def test_legacy_pending_requests_are_migrated_or_preserved_readonly(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("""INSERT INTO spare_part_requests VALUES
                (31, 'OLD-31', 1, '现场人员', '采样泵', 2, '历史待审', '', 'pending', '2026-07-25 08:00:00')""")
            db.execute("""INSERT INTO spare_part_requests VALUES
                (32, 'OLD-32', 1, '现场人员', '未知物料', 1, '无法映射', '', 'pending', '2026-07-25 08:00:00')""")
            db.commit()
        finally:
            db.close()

        app_module.retire_legacy_spare_part_requests()
        self.assertEqual(self.read_one('SELECT status FROM spare_part_requests WHERE id=31')[0], 'migrated')
        self.assertEqual(self.read_one('SELECT status FROM spare_part_requests WHERE id=32')[0], 'legacy_readonly')
        self.assertEqual(self.read_one("SELECT status FROM parts_requests WHERE request_no='LEGACY-31'")[0], 'pending')
        self.assertEqual(self.read_one("SELECT part_id FROM parts_request_items WHERE request_id=(SELECT id FROM parts_requests WHERE request_no='LEGACY-31')")[0], 1)


if __name__ == '__main__':
    unittest.main()
