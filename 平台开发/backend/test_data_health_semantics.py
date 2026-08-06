import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class DataHealthSemanticsTest(unittest.TestCase):
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
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens['admin-token'] = {
            'id': 1, 'role': 'admin', 'roles': ['admin'], 'real_name': '管理员',
        }
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, code TEXT, type TEXT, manager TEXT
                );
                CREATE TABLE param_thresholds (
                    metric TEXT PRIMARY KEY, label TEXT, low REAL, high REAL
                );
                CREATE TABLE sensor_data (
                    site_id INTEGER, metric TEXT, value REAL, recorded_at TEXT
                );
                CREATE TABLE device_shadows (
                    id INTEGER PRIMARY KEY, site_id INTEGER, monitoring_enabled INTEGER
                );
                INSERT INTO sites VALUES (1, '零样本站', 'WQ-001', 'water_quality', '运维甲');
                INSERT INTO param_thresholds VALUES ('ph', 'pH', 6, 9);
                INSERT INTO device_shadows VALUES (1, 1, 0);
            ''')
            db.commit()
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    def test_no_samples_never_report_perfect_validity_or_timeliness(self):
        response = self.client.get(
            '/api/data/health?period=month',
            headers={'Authorization': 'Bearer admin-token'},
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsNone(response.json['total']['validity_rate'])
        self.assertIsNone(response.json['total']['timeliness_rate'])
        self.assertIsNone(response.json['by_site'][0]['validity_rate'])
        self.assertIsNone(response.json['by_site'][0]['timeliness_rate'])
        self.assertIsNone(response.json['by_manager'][0]['validity_rate'])
        self.assertIsNone(response.json['by_manager'][0]['timeliness_rate'])
        self.assertEqual(response.json['total']['expected'], 0)
        self.assertEqual(response.json['total']['missing'], 0)
        self.assertIsNone(response.json['total']['completeness_rate'])
        self.assertEqual(response.json['by_site'][0]['configuration_status'], 'monitoring_disabled')

    def test_enabled_site_uses_only_metrics_evidenced_by_monitoring_records(self):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db = sqlite3.connect(self.db_path)
        try:
            db.execute('UPDATE device_shadows SET monitoring_enabled=1 WHERE site_id=1')
            db.execute('INSERT INTO sensor_data VALUES (1,?,?,?)', ('ph', 7.1, now))
            db.commit()
        finally:
            db.close()

        response = self.client.get(
            '/api/data/health?period=month',
            headers={'Authorization': 'Bearer admin-token'},
        )
        self.assertEqual(response.status_code, 200, response.json)
        site = response.json['by_site'][0]
        self.assertEqual(site['configuration_status'], 'configured')
        self.assertEqual([metric['metric'] for metric in site['metrics']], ['ph'])
        self.assertGreater(site['expected'], 0)
        self.assertEqual(site['actual'], 1)

    def test_current_day_excludes_future_sampling_slots(self):
        expected = app_module._expected_samples_for_period(
            96,
            datetime(2026, 8, 5, 0, 0, 0),
            datetime(2026, 8, 5, 23, 59, 59),
            datetime(2026, 8, 5, 9, 7, 0),
        )
        self.assertEqual(expected, 37)
        self.assertLess(expected, 96)

    def test_historical_days_use_the_full_expected_count(self):
        expected = app_module._expected_samples_for_period(
            96,
            datetime(2026, 8, 1, 0, 0, 0),
            datetime(2026, 8, 3, 23, 59, 59),
            datetime(2026, 8, 5, 9, 7, 0),
        )
        self.assertEqual(expected, 288)


if __name__ == '__main__':
    unittest.main()
