import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class DisabledAlertRuleConsumerTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
        self.original_get_db = app_module.get_db
        self.original_rules_cache = app_module._alert_rules_cache
        self.original_rules_cache_time = app_module._alert_rules_cache_time

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
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE alert_rule_config (
                    id TEXT PRIMARY KEY, metric TEXT, enabled INTEGER,
                    thresholds TEXT, flow_type TEXT
                );
                CREATE TABLE alert_escalation_config (
                    level TEXT PRIMARY KEY, sla_minutes INTEGER,
                    escalate_to_level TEXT, auto_workorder INTEGER,
                    is_active INTEGER
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, metric TEXT,
                    message TEXT, level TEXT, status TEXT, created_at TEXT,
                    related_order_no TEXT, urge_count INTEGER DEFAULT 0,
                    last_urged_at TEXT
                );
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, manager TEXT, status TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT,
                    site_id INTEGER, source TEXT, event_type TEXT, level TEXT,
                    title TEXT, description TEXT, status TEXT, assignee TEXT,
                    created_at TEXT, related_alert_id INTEGER
                );
                INSERT INTO alert_rule_config VALUES
                    ('rule_data_freeze','data_freeze',0,'{}','manual'),
                    ('rule_data_spike','data_spike',1,'{}','manual');
                INSERT INTO alert_escalation_config VALUES
                    ('yellow',1,'orange',1,1);
                INSERT INTO sites VALUES (1,'测试站','负责人','online');
            ''')
            old_time = (app_module.datetime.now() - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
            db.executemany(
                '''INSERT INTO alerts
                   (id,site_id,metric,message,level,status,created_at,related_order_no)
                   VALUES (?,?,?,?,?,'pending',?,NULL)''',
                [
                    (1, 1, 'data_freeze', '冻结告警', 'yellow', old_time),
                    (2, 1, 'data_spike', '突变告警', 'yellow', old_time),
                ],
            )

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._alert_rules_cache = self.original_rules_cache
        app_module._alert_rules_cache_time = self.original_rules_cache_time
        os.unlink(self.db_path)

    def test_disabled_metric_cannot_create_escalation_workorder_but_other_metric_can(self):
        with mock.patch.object(app_module, '_station_operator', return_value='负责人'):
            app_module.alert_escalation_check()

        with self.temporary_db() as db:
            orders = [dict(row) for row in db.execute(
                'SELECT event_type,related_alert_id FROM work_orders ORDER BY related_alert_id'
            ).fetchall()]
            disabled = db.execute('SELECT level,related_order_no FROM alerts WHERE id=1').fetchone()
            enabled = db.execute('SELECT level,related_order_no FROM alerts WHERE id=2').fetchone()
        self.assertEqual(orders, [{'event_type': 'data_spike', 'related_alert_id': 2}])
        self.assertEqual(disabled['level'], 'orange')
        self.assertIsNone(disabled['related_order_no'])
        self.assertEqual(enabled['level'], 'orange')
        self.assertTrue(enabled['related_order_no'])

    def test_reenabling_metric_restores_escalation_workorder_creation(self):
        with self.temporary_db() as db:
            db.execute("UPDATE alert_rule_config SET enabled=1 WHERE metric='data_freeze'")
        with mock.patch.object(app_module, '_station_operator', return_value='负责人'):
            app_module.alert_escalation_check()
        with self.temporary_db() as db:
            order = db.execute(
                "SELECT related_alert_id FROM work_orders WHERE event_type='data_freeze'"
            ).fetchone()
        self.assertEqual(order['related_alert_id'], 1)

    def test_disabled_metric_is_blocked_at_central_alert_creation_fallback(self):
        with self.temporary_db() as db, mock.patch.object(
                app_module, '_station_service_policy_rows_batch') as policy:
            result = app_module.create_alert_internal(
                db, 1, 'data_freeze', 7.0, 'yellow', '不应创建',
            )
        self.assertIsNone(result)
        policy.assert_not_called()


class DeviceStatusLifecycleTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
        self.original_rules_cache = app_module._alert_rules_cache
        self.original_rules_cache_time = app_module._alert_rules_cache_time
        with closing(self.connect()) as db:
            db.executescript('''
                CREATE TABLE alert_rule_config (
                    id TEXT PRIMARY KEY, metric TEXT, enabled INTEGER,
                    thresholds TEXT, flow_type TEXT
                );
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, status TEXT
                );
                CREATE TABLE device_shadows (
                    id INTEGER PRIMARY KEY, site_id INTEGER, status TEXT,
                    device_name TEXT, monitoring_enabled INTEGER,
                    last_data_time TEXT
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER,
                    metric TEXT, status TEXT, created_at TEXT
                );
                INSERT INTO alert_rule_config VALUES
                    ('rule_device_status','device_status',0,
                     '{"yellow":60}','auto'),
                    ('rule_data_gap','data_gap',0,'{}','auto');
                INSERT INTO sites VALUES (1,'测试站','online');
            ''')
            stale = (app_module.datetime.now() - timedelta(minutes=45)).strftime('%Y-%m-%d %H:%M:%S')
            db.execute(
                "INSERT INTO device_shadows VALUES (1,1,'online','分析仪',1,?)",
                (stale,),
            )
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def tearDown(self):
        app_module._alert_rules_cache = self.original_rules_cache
        app_module._alert_rules_cache_time = self.original_rules_cache_time
        os.unlink(self.db_path)

    def run_check(self):
        def scheduler_db():
            return self.connect()

        app_module._alert_rules_cache = None
        app_module._alert_rules_cache_time = 0
        with mock.patch.object(app_module, '_scheduler_db', side_effect=scheduler_db), \
                mock.patch.object(app_module, 'create_alert_internal') as create_alert:
            app_module.check_device_offline()
        with closing(self.connect()) as db:
            device_status = db.execute('SELECT status FROM device_shadows WHERE id=1').fetchone()['status']
            site_status = db.execute('SELECT status FROM sites WHERE id=1').fetchone()['status']
        return device_status, site_status, create_alert

    def test_device_rule_switch_does_not_change_device_or_site_status_lifecycle(self):
        disabled_device, disabled_site, disabled_create = self.run_check()
        self.assertEqual((disabled_device, disabled_site), ('offline', 'offline'))
        disabled_create.assert_not_called()

        with closing(self.connect()) as db:
            db.execute("UPDATE alert_rule_config SET enabled=1 WHERE metric='device_status'")
            db.execute("UPDATE device_shadows SET status='online' WHERE id=1")
            db.execute("UPDATE sites SET status='online' WHERE id=1")
            db.commit()
        enabled_device, enabled_site, enabled_create = self.run_check()
        self.assertEqual((enabled_device, enabled_site), ('offline', 'offline'))
        self.assertTrue(any(
            call.args[2] == 'device_status' for call in enabled_create.call_args_list
        ))


if __name__ == '__main__':
    unittest.main()
