import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class AlertRuleValidationTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)

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
        app_module._tokens['admin-token'] = {
            'id': 1, 'username': 'admin', 'real_name': '管理员',
            'role': 'admin', 'roles': ['admin'],
        }
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE threshold_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    scope TEXT NOT NULL, site_id INTEGER, metric TEXT,
                    rule_type TEXT NOT NULL, conditions TEXT NOT NULL,
                    severity TEXT, enabled INTEGER DEFAULT 1, created_by INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX uq_threshold_rules_semantic ON threshold_rules(
                    lower(trim(name)), scope, ifnull(site_id,-1), ifnull(metric,''),
                    rule_type, conditions, severity
                );
                CREATE TABLE alert_rule_config (
                    id TEXT PRIMARY KEY, metric TEXT, metric_label TEXT,
                    description TEXT, enabled INTEGER, flow_type TEXT,
                    unit TEXT, thresholds TEXT, is_reversed INTEGER DEFAULT 0
                );
                CREATE TABLE operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT, action TEXT,
                    target_type TEXT, target_id INTEGER, operator TEXT,
                    operator_id INTEGER, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO sites VALUES (1, '测试站');
                INSERT INTO alert_rule_config VALUES
                    ('rule_data_gap', 'data_gap', '数据缺失', '', 1, 'auto', '分钟',
                     '{"blue":30,"yellow":60,"orange":120,"red":240}', 0);
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_path)

    @staticmethod
    def headers():
        return {'Authorization': 'Bearer admin-token'}

    def post_rule(self, **overrides):
        payload = {
            'name': 'pH 阈值', 'scope': 'metric', 'metric': 'ph',
            'rule_type': 'static', 'severity': 'warning',
            'conditions': {'min': 6, 'max': 9},
        }
        payload.update(overrides)
        return self.client.post('/api/threshold-rules', json=payload, headers=self.headers())

    def test_scope_and_condition_validation(self):
        self.assertEqual(self.post_rule(scope='metric', metric=None).status_code, 400)
        self.assertEqual(self.post_rule(scope='site', site_id=None).status_code, 400)
        self.assertEqual(self.post_rule(scope='site', site_id=999).status_code, 400)
        self.assertEqual(self.post_rule(conditions={}).status_code, 400)
        self.assertEqual(self.post_rule(conditions={'min': 9, 'max': 6}).status_code, 400)
        self.assertEqual(self.post_rule(rule_type='historical').status_code, 400)
        self.assertEqual(self.post_rule(rule_type='spc', conditions={'mean': 7, 'std': 0}).status_code, 400)

    def test_create_update_and_delete_rule(self):
        created = self.post_rule()
        self.assertEqual(created.status_code, 201, created.json)
        rule_id = created.json['id']
        updated = self.client.put(
            f'/api/threshold-rules/{rule_id}', headers=self.headers(),
            json={'conditions': {'min': 6.5, 'max': 8.5}},
        )
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual(updated.json['conditions']['min'], 6.5)
        deleted = self.client.delete(f'/api/threshold-rules/{rule_id}', headers=self.headers())
        self.assertEqual(deleted.status_code, 200, deleted.json)

    def test_disabling_alert_rule_requires_reason_and_is_audited(self):
        missing = self.client.put(
            '/api/alert-rules/rule_data_gap', headers=self.headers(), json={'enabled': False},
        )
        self.assertEqual(missing.status_code, 400, missing.json)
        saved = self.client.put(
            '/api/alert-rules/rule_data_gap', headers=self.headers(),
            json={'enabled': False, 'disable_reason': '现场设备停用检修'},
        )
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertFalse(saved.json['enabled'])
        with app_module.get_db() as db:
            log = db.execute("SELECT * FROM operation_logs WHERE action='disable'").fetchone()
            self.assertIsNotNone(log)
            self.assertIn('现场设备停用检修', log['details'])


if __name__ == '__main__':
    unittest.main()
