import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class AutoAlertWorkorderDismissTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, 'test.db')
        self.original_db_path = app_module.DB_PATH
        self.original_get_db = app_module.get_db
        self.original_testing = app_module.app.testing
        app_module.DB_PATH = self.db_path
        app_module.app.config.update(TESTING=True)
        app_module._tokens.clear()
        self._create_schema()
        db_path = self.db_path

        @contextmanager
        def temporary_db():
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            try:
                yield db
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        app_module.get_db = temporary_db
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module._tokens.clear()
        app_module.get_db = self.original_get_db
        app_module.DB_PATH = self.original_db_path
        app_module.app.testing = self.original_testing
        self.tmp.cleanup()

    def _create_schema(self):
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, real_name TEXT,
                    role TEXT, status TEXT
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, site_id INTEGER,
                    source TEXT, assignee TEXT, status TEXT, related_alert_id INTEGER,
                    conclusion TEXT, remark TEXT, resolved_at TEXT, check_in_time TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, status TEXT,
                    related_order_no TEXT, resolve_reason TEXT, resolved_at TEXT
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT,
                    source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT
                );
                INSERT INTO sites VALUES (10, 'A站');
                INSERT INTO users VALUES (1, 'operator1', '负责人', 'operator', 'active');
                INSERT INTO users VALUES (2, 'admin1', '管理员', 'admin', 'active');
                INSERT INTO users VALUES (3, 'reviewer1', '审核员', 'reviewer', 'active');
                INSERT INTO users VALUES (4, 'multi1', '多角色负责人', 'admin', 'active');
                INSERT INTO user_roles VALUES (1, 'operator');
                INSERT INTO user_roles VALUES (2, 'admin');
                INSERT INTO user_roles VALUES (3, 'reviewer');
                INSERT INTO user_roles VALUES (4, 'admin');
                INSERT INTO user_roles VALUES (4, 'operator');
                INSERT INTO user_sites VALUES (1, 10);
                INSERT INTO user_sites VALUES (2, 10);
                INSERT INTO user_sites VALUES (3, 10);
                INSERT INTO user_sites VALUES (4, 10);
            """)
        self.add_pair()

    def add_pair(self, order_id=100, order_no='WO-AUTO-1', assignee='负责人',
                 source='auto', order_status='pending', alert_id=200,
                 alert_status='pending', related_order_no='WO-AUTO-1'):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """INSERT INTO work_orders
                   (id,order_no,site_id,source,assignee,status,related_alert_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (order_id, order_no, 10, source, assignee, order_status, alert_id),
            )
            db.execute(
                """INSERT INTO alerts (id,site_id,status,related_order_no)
                   VALUES (?,?,?,?)""",
                (alert_id, 10, alert_status, related_order_no),
            )

    def headers(self, user_id=1):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            user = dict(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())
            roles = [r['role'] for r in db.execute(
                'SELECT role FROM user_roles WHERE user_id=?', (user_id,)).fetchall()]
        user['roles'] = roles
        token = f'token-{user_id}'
        app_module._tokens[token] = user
        return {'Authorization': f'Bearer {token}'}

    def post(self, payload, user_id=1, order_no='WO-AUTO-1'):
        return self.client.post(
            f'/api/workorders/{order_no}/dismiss-auto-alert',
            json=payload,
            headers=self.headers(user_id),
        )

    def facts(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            order = dict(db.execute('SELECT * FROM work_orders WHERE id=100').fetchone())
            alert = dict(db.execute('SELECT * FROM alerts WHERE id=200').fetchone())
            events = [dict(r) for r in db.execute(
                'SELECT * FROM timeline_events ORDER BY id').fetchall()]
        return order, alert, events

    def test_list_exposes_only_server_computed_eligible_hint(self):
        response = self.client.get('/api/workorders', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json[0]['can_dismiss_auto_alert'])
        self.assertTrue(response.json[0]['can_operate'])

        admin = self.client.get('/api/workorders', headers=self.headers(2))
        self.assertFalse(admin.json[0]['can_dismiss_auto_alert'])

    def test_three_conclusions_close_both_objects_and_write_exact_events(self):
        for index, conclusion in enumerate(('false_alarm', 'normal_deviation', 'other')):
            if index:
                self.add_pair(100 + index, f'WO-AUTO-{index + 1}', '负责人',
                              alert_id=200 + index,
                              related_order_no=f'WO-AUTO-{index + 1}')
            order_no = f'WO-AUTO-{index + 1}'
            response = self.post(
                {'conclusion': conclusion, 'reason': '  完整原因文本  '},
                order_no=order_no,
            )
            self.assertEqual(response.status_code, 200)
            with sqlite3.connect(self.db_path) as db:
                db.row_factory = sqlite3.Row
                order = db.execute(
                    'SELECT * FROM work_orders WHERE order_no=?', (order_no,)).fetchone()
                alert = db.execute(
                    'SELECT * FROM alerts WHERE id=?', (200 + index,)).fetchone()
                events = db.execute(
                    'SELECT * FROM timeline_events WHERE source_id IN (?,?) ORDER BY id',
                    (100 + index, 200 + index),
                ).fetchall()
            self.assertEqual((order['status'], order['conclusion'], order['remark']),
                             ('closed', conclusion, '完整原因文本'))
            self.assertEqual((alert['status'], alert['resolve_reason']),
                             ('resolved', conclusion))
            self.assertEqual([(e['source_type'], e['source_id']) for e in events],
                             [('order', 100 + index), ('alert', 200 + index)])
            self.assertTrue(all('完整原因文本' in e['remark'] for e in events))

    def test_reason_and_conclusion_validation_have_zero_side_effects(self):
        cases = [
            ({'conclusion': 'invalid', 'reason': '原因'}, 400,
             'AUTO_ALERT_DISMISS_CONCLUSION_INVALID'),
            ({'conclusion': 'false_alarm', 'reason': '  '}, 400,
             'AUTO_ALERT_DISMISS_REASON_REQUIRED'),
            ({'conclusion': 'false_alarm', 'reason': 'x' * 501}, 400,
             'AUTO_ALERT_DISMISS_REASON_TOO_LONG'),
        ]
        for payload, status, code in cases:
            response = self.post(payload)
            self.assertEqual((response.status_code, response.json['code']), (status, code))
            order, alert, events = self.facts()
            self.assertEqual((order['status'], alert['status'], events), ('pending', 'pending', []))

    def test_manual_or_unlinked_workorders_are_rejected(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE work_orders SET source='manual_report' WHERE id=100")
        response = self.post({'conclusion': 'false_alarm', 'reason': '原因'})
        self.assertEqual((response.status_code, response.json['code']),
                         (409, 'AUTO_ALERT_WORKORDER_REQUIRED'))

    def test_only_assigned_operator_with_current_site_scope_can_dismiss(self):
        payload = {'conclusion': 'false_alarm', 'reason': '原因'}
        for user_id in (2, 3):
            response = self.post(payload, user_id)
            self.assertEqual((response.status_code, response.json['code']),
                             (403, 'AUTO_ALERT_DISMISS_FORBIDDEN'))
        with sqlite3.connect(self.db_path) as db:
            db.execute('DELETE FROM user_sites WHERE user_id=1')
        no_site = self.post(payload)
        self.assertEqual((no_site.status_code, no_site.json['code']),
                         (403, 'AUTO_ALERT_DISMISS_FORBIDDEN'))
        with sqlite3.connect(self.db_path) as db:
            db.execute('INSERT INTO user_sites VALUES (1,10)')
            db.execute("UPDATE work_orders SET assignee='其他人' WHERE id=100")
        other_assignee = self.post(payload)
        self.assertEqual((other_assignee.status_code, other_assignee.json['code']),
                         (403, 'AUTO_ALERT_DISMISS_FORBIDDEN'))

    def test_multirole_assignee_is_allowed_by_operator_responsibility(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE work_orders SET assignee='多角色负责人' WHERE id=100")
        response = self.post({'conclusion': 'normal_deviation', 'reason': '季节性正常偏差'}, 4)
        self.assertEqual(response.status_code, 200)

    def test_state_and_bidirectional_link_conflicts_are_zero_side_effect(self):
        payload = {'conclusion': 'false_alarm', 'reason': '原因'}
        mutations = [
            ("UPDATE work_orders SET status='accepted' WHERE id=100",
             'AUTO_ALERT_WORKORDER_NOT_PENDING'),
            ("UPDATE alerts SET status='resolved' WHERE id=200",
             'AUTO_ALERT_NOT_PENDING'),
            ("UPDATE alerts SET related_order_no='WO-OTHER' WHERE id=200",
             'AUTO_ALERT_LINK_MISMATCH'),
        ]
        for sql, code in mutations:
            with sqlite3.connect(self.db_path) as db:
                db.execute("UPDATE work_orders SET status='pending' WHERE id=100")
                db.execute("UPDATE alerts SET status='pending',related_order_no='WO-AUTO-1' WHERE id=200")
                db.execute(sql)
            response = self.post(payload)
            self.assertEqual((response.status_code, response.json['code']), (409, code))
            with sqlite3.connect(self.db_path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM timeline_events').fetchone()[0], 0)

    def test_same_request_is_idempotent_but_different_payload_conflicts(self):
        payload = {'conclusion': 'false_alarm', 'reason': '同一原因'}
        first = self.post(payload)
        second = self.post(payload)
        different = self.post({'conclusion': 'other', 'reason': '另一原因'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual((second.status_code, second.json['already_dismissed']), (200, True))
        self.assertEqual((different.status_code, different.json['code']),
                         (409, 'AUTO_ALERT_WORKORDER_NOT_PENDING'))
        _, _, events = self.facts()
        self.assertEqual(len(events), 2)

    def test_database_failure_rolls_back_order_alert_and_events(self):
        real_get_db = app_module.get_db

        class FailingConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, params=()):
                if "VALUES ('alert',?,'dismissed'" in sql:
                    raise sqlite3.OperationalError('injected event failure')
                return self.connection.execute(sql, params)

            def commit(self):
                return self.connection.commit()

            def rollback(self):
                return self.connection.rollback()

        class FailingContext:
            def __enter__(self):
                self.connection = sqlite3.connect(self_path)
                self.connection.row_factory = sqlite3.Row
                return FailingConnection(self.connection)

            def __exit__(self, exc_type, exc, tb):
                if exc_type:
                    self.connection.rollback()
                self.connection.close()

        self_path = self.db_path
        with mock.patch.object(app_module, 'get_db', side_effect=lambda: FailingContext()):
            response = self.post({'conclusion': 'false_alarm', 'reason': '故障回滚'})
        self.assertEqual((response.status_code, response.json['code']),
                         (503, 'AUTO_ALERT_DISMISS_ROLLED_BACK'))
        order, alert, events = self.facts()
        self.assertEqual((order['status'], alert['status'], events), ('pending', 'pending', []))
        self.assertIsNotNone(real_get_db)


if __name__ == '__main__':
    unittest.main()
