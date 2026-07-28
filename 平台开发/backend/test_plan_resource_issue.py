import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class PlanResourceIssueTest(unittest.TestCase):
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
        app_module._tokens['operator-token'] = {
            'id': 2, 'role': 'operator', 'real_name': '现场运维', 'username': 'operator'
        }
        app_module._tokens['other-token'] = {
            'id': 3, 'role': 'operator', 'real_name': '其他运维', 'username': 'other'
        }
        today = datetime.now().strftime('%Y-%m-%d')
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, version INTEGER);
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_schedule_id INTEGER, assignee_id INTEGER,
                    generate_date TEXT, status TEXT
                );
                CREATE TABLE plan_departure_confirmations (
                    schedule_id INTEGER, user_id INTEGER, work_date TEXT,
                    vehicle_confirmed INTEGER, parts_confirmed INTEGER
                );
                CREATE TABLE plan_resource_reservations (
                    id INTEGER PRIMARY KEY, schedule_id INTEGER, part_id INTEGER,
                    planned_quantity INTEGER, reserved_quantity INTEGER,
                    issued_quantity INTEGER DEFAULT 0,
                    status TEXT, updated_at TEXT
                );
                CREATE TABLE spare_parts_inventory (
                    id INTEGER PRIMARY KEY, part_name TEXT, part_code TEXT, unit TEXT,
                    quantity INTEGER, updated_at TEXT
                );
                CREATE TABLE inventory_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, part_id INTEGER, type TEXT, quantity INTEGER,
                    ref_type TEXT, ref_id INTEGER, operator TEXT, remark TEXT
                );
                CREATE TABLE plan_schedule_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, version INTEGER,
                    event_type TEXT, operator_id INTEGER, payload TEXT
                );
                INSERT INTO users VALUES (2, '现场运维', 'operator');
                INSERT INTO plan_schedules VALUES (5, 2, 'approved', 1);
                INSERT INTO plan_resource_reservations VALUES (1, 5, 8, 5, 0, 0, 'planned', NULL);
                INSERT INTO spare_parts_inventory VALUES (8, '滤芯', 'P-008', '件', 10, NULL);
            ''')
            db.execute('INSERT INTO insp_plans VALUES (50, 5, 2, ?, ?)', (today, 'active'))
            db.execute('INSERT INTO plan_departure_confirmations VALUES (5, 2, ?, 1, 1)', (today,))
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    def issue(self, quantity):
        return self.client.post('/api/plan-schedules/5/parts/issue',
                                headers={'Authorization': 'Bearer operator-token'},
                                json={'items': [{'part_id': 8, 'quantity': quantity}]})

    def test_partial_issue_keeps_remaining_planned_demand_available(self):
        first = self.issue(2)
        self.assertEqual(first.status_code, 200, first.json)
        second = self.issue(3)
        self.assertEqual(second.status_code, 200, second.json)
        with app_module.get_db() as db:
            reservation = db.execute(
                'SELECT issued_quantity, status FROM plan_resource_reservations WHERE id=1'
            ).fetchone()
            stock = db.execute('SELECT quantity FROM spare_parts_inventory WHERE id=8').fetchone()['quantity']
            log_count = db.execute('SELECT COUNT(*) FROM inventory_logs').fetchone()[0]
        self.assertEqual((reservation['issued_quantity'], reservation['status']), (5, 'issued'))
        self.assertEqual(stock, 5)
        self.assertEqual(log_count, 2)

    def test_mobile_execution_package_can_issue_and_returns_current_balance(self):
        response = self.client.post('/api/mobile/execution-plans/50/parts/issue',
                                    headers={'Authorization': 'Bearer operator-token'},
                                    json={'items': [{'part_id': 8, 'quantity': 2}]})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['issued_quantity'], 2)
        self.assertEqual(response.json['resource_parts'][0]['issued_quantity'], 2)
        self.assertEqual(response.json['resource_parts'][0]['remaining_quantity'], 3)
        self.assertEqual(response.json['resource_parts'][0]['reserved_quantity'], 0)
        with app_module.get_db() as db:
            stock = db.execute('SELECT quantity FROM spare_parts_inventory WHERE id=8').fetchone()['quantity']
        self.assertEqual(stock, 8)

    def test_mobile_issue_rejects_other_operator_and_excess_quantity_without_deduction(self):
        forbidden = self.client.post('/api/mobile/execution-plans/50/parts/issue',
                                     headers={'Authorization': 'Bearer other-token'},
                                     json={'items': [{'part_id': 8, 'quantity': 1}]})
        excessive = self.client.post('/api/mobile/execution-plans/50/parts/issue',
                                     headers={'Authorization': 'Bearer operator-token'},
                                     json={'items': [{'part_id': 8, 'quantity': 6}]})
        self.assertEqual(forbidden.status_code, 404, forbidden.json)
        self.assertEqual(excessive.status_code, 400, excessive.json)
        with app_module.get_db() as db:
            stock = db.execute('SELECT quantity FROM spare_parts_inventory WHERE id=8').fetchone()['quantity']
            issued = db.execute('SELECT issued_quantity FROM plan_resource_reservations WHERE id=1').fetchone()['issued_quantity']
        self.assertEqual(stock, 10)
        self.assertEqual(issued, 0)

    def test_mobile_issue_requires_departure_resource_confirmation(self):
        with app_module.get_db() as db:
            db.execute('DELETE FROM plan_departure_confirmations')
        response = self.client.post('/api/mobile/execution-plans/50/parts/issue',
                                    headers={'Authorization': 'Bearer operator-token'},
                                    json={'items': [{'part_id': 8, 'quantity': 1}]})
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn('资源确认', response.json['error'])


if __name__ == '__main__':
    unittest.main()
