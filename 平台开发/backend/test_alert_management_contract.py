import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class AlertManagementContractTest(unittest.TestCase):
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

        self.temporary_db = temporary_db
        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'admin-token': {'id': 1, 'role': 'admin', 'roles': ['admin'], 'real_name': '管理员'},
            'reviewer-token': {'id': 2, 'role': 'reviewer', 'roles': ['reviewer'], 'real_name': '审核员'},
            'operator-token': {'id': 3, 'role': 'operator', 'roles': ['operator'], 'real_name': '运维员'},
        })

        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, role TEXT, real_name TEXT,
                    status TEXT DEFAULT 'active'
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, code TEXT);
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, metric TEXT,
                    value REAL, level TEXT, message TEXT, status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT,
                    resolve_reason TEXT, related_order_no TEXT, review_id INTEGER,
                    flow_type TEXT, flow_status TEXT, urge_count INTEGER DEFAULT 0,
                    last_urged_at TEXT, response_deadline TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE, site_id INTEGER,
                    source TEXT, event_type TEXT, level TEXT, title TEXT, description TEXT,
                    assignee TEXT, status TEXT, sla_deadline TEXT
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?,?)', [
                (1, 'admin', 'admin', '管理员', 'active'),
                (2, 'reviewer', 'reviewer', '审核员', 'active'),
                (3, 'operator', 'operator', '运维员', 'active'),
            ])
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [
                (1, 'admin'), (2, 'reviewer'), (3, 'operator'),
            ])
            db.executemany('INSERT INTO sites VALUES (?,?,?)', [(1, '站点一', 'S1'), (2, '站点二', 'S2')])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(2, 1), (3, 1)])
            db.executemany(
                "INSERT INTO alerts (id,site_id,metric,value,level,message,status,flow_type,flow_status) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (1, 1, 'ph', 9.2, 'yellow', '站点一 pH 偏高', 'pending', 'manual', 'pending_review'),
                    (2, 2, 'cod', 45, 'orange', '站点二 COD 偏高', 'pending', 'manual', 'pending_review'),
                    (3, 1, 'ph', 7.1, 'blue', '已办结记录', 'resolved', 'manual', 'dismissed'),
                ],
            )

        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def test_alert_writes_are_admin_only(self):
        calls = [
            ('post', '/api/alerts/1/acknowledge', {}),
            ('post', '/api/alerts/1/resolve', {'reason': 'manual_review'}),
            ('post', '/api/alerts/1/ack-resolve', {'remark': '确认无异常'}),
            ('post', '/api/alerts/1/urge', {'opinion': '尽快处理'}),
            ('post', '/api/alerts/1/undo-acknowledge', {'remark': '误受理'}),
            ('post', '/api/alerts/1/confirm-convert', {}),
            ('post', '/api/alerts/batch', {'ids': [1], 'action': 'convert'}),
            ('post', '/api/alerts/simulate', {'site_id': 1, 'metric': 'ph', 'value': 9.5, 'level': 'yellow'}),
        ]
        for token in ('reviewer-token', 'operator-token'):
            for method, path, payload in calls:
                response = getattr(self.client, method)(path, headers=self.headers(token), json=payload)
                self.assertEqual(response.status_code, 403, (token, path, response.json))

    def test_single_transition_validates_state_and_records_real_operator(self):
        accepted = self.client.post('/api/alerts/1/acknowledge', headers=self.headers('admin-token'), json={'operator': '伪造人员'})
        self.assertEqual(accepted.status_code, 200, accepted.json)
        repeated = self.client.post('/api/alerts/1/acknowledge', headers=self.headers('admin-token'))
        self.assertEqual(repeated.status_code, 409, repeated.json)
        missing_reason = self.client.post('/api/alerts/1/resolve', headers=self.headers('admin-token'), json={})
        self.assertEqual(missing_reason.status_code, 400, missing_reason.json)
        resolved = self.client.post('/api/alerts/1/resolve', headers=self.headers('admin-token'), json={
            'reason': 'manual_review', 'remark': '已核对现场数据', 'operator': '伪造人员',
        })
        self.assertEqual(resolved.status_code, 200, resolved.json)
        with self.temporary_db() as db:
            row = db.execute('SELECT status,resolve_reason FROM alerts WHERE id=1').fetchone()
            self.assertEqual(row['status'], 'resolved')
            self.assertIn('人工复核', row['resolve_reason'])
            operators = {item['operator'] for item in db.execute(
                "SELECT operator FROM timeline_events WHERE source_type='alert' AND source_id=1"
            ).fetchall()}
            self.assertEqual(operators, {'管理员'})

    def test_batch_convert_uses_supported_action_and_reports_skips(self):
        response = self.client.post('/api/alerts/batch', headers=self.headers('admin-token'), json={
            'ids': [1, 3], 'action': 'convert',
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['count'], 1)
        self.assertEqual(response.json['skipped'], 1)
        with self.temporary_db() as db:
            order = db.execute('SELECT assignee,status FROM work_orders').fetchone()
            self.assertEqual(order['assignee'], '运维员')
            self.assertEqual(order['status'], 'in_progress')
            alert = db.execute('SELECT related_order_no,flow_status FROM alerts WHERE id=1').fetchone()
            self.assertTrue(alert['related_order_no'])
            self.assertEqual(alert['flow_status'], 'converted')

    def test_simulation_is_committed_and_uses_current_timeline_schema(self):
        response = self.client.post('/api/alerts/simulate', headers=self.headers('admin-token'), json={
            'site_id': 1,
            'metric': 'ph',
            'value': 9.5,
            'level': 'yellow',
            'message': '[模拟] 站点一 pH 9.5，触发黄色警示',
        })
        self.assertEqual(response.status_code, 200, response.json)
        alert_id = response.json['id']
        with self.temporary_db() as db:
            alert = db.execute('SELECT message,flow_type,flow_status FROM alerts WHERE id=?', (alert_id,)).fetchone()
            self.assertTrue(alert['message'].startswith('[模拟]'))
            self.assertNotIn('[模拟] [模拟]', alert['message'])
            self.assertEqual(alert['flow_type'], 'simulation')
            self.assertEqual(alert['flow_status'], 'test')
            event = db.execute('SELECT event_type,operator FROM timeline_events WHERE source_id=?', (alert_id,)).fetchone()
            self.assertEqual(event['event_type'], 'simulated')
            self.assertEqual(event['operator'], '管理员')

    def test_statistics_respect_reviewer_site_scope(self):
        response = self.client.get('/api/alerts/statistics', headers=self.headers('reviewer-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['total'], 2)
        self.assertEqual(response.json['by_status']['pending'], 1)
        self.assertEqual(response.json['by_status']['resolved'], 1)
        self.assertEqual(response.json['by_level']['orange'], 0)


if __name__ == '__main__':
    unittest.main()
