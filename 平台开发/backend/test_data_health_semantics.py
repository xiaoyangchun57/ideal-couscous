import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager, ExitStack
from datetime import datetime
from unittest.mock import patch

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
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT, status TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
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

    def test_period_and_current_responsibility_use_ids(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.executescript('''
                INSERT INTO sites VALUES (2, '无负责人站', 'WQ-002', 'water_quality', '旧负责人');
                INSERT INTO users VALUES (10, '同名', 'operator', 'active');
                INSERT INTO users VALUES (11, '同名', 'operator', 'active');
                INSERT INTO users VALUES (12, '旧负责人', 'operator', 'inactive');
                INSERT INTO user_sites VALUES (10, 1), (11, 1), (12, 2);
            ''')
            db.commit()
        finally:
            db.close()
        for period in ('month', 'quarter', 'year'):
            response = self.client.get(
                f'/api/data/health?period={period}',
                headers={'Authorization': 'Bearer admin-token'},
            )
            self.assertEqual(response.status_code, 200, response.json)
            data = response.json
            start, end, label, days = app_module._resolve_period(period)
            self.assertEqual((data['start'], data['end'], data['period_label'], data['days']),
                             (start, end, label, days))
            sites = {site['site_id']: site for site in data['by_site']}
            self.assertEqual([p['id'] for p in sites[1]['responsible_people']], [10, 11])
            self.assertEqual(sites[2]['responsible_people'], [])
            self.assertEqual(sites[2]['manager'], '未分配')
            self.assertEqual({row['user_id'] for row in data['by_manager']}, {10, 11, None})
            self.assertEqual(data['total']['site_count'], 2)

    def test_historical_days_use_the_full_expected_count(self):
        expected = app_module._expected_samples_for_period(
            96,
            datetime(2026, 8, 1, 0, 0, 0),
            datetime(2026, 8, 3, 23, 59, 59),
            datetime(2026, 8, 5, 9, 7, 0),
        )
        self.assertEqual(expected, 288)

    def test_formal_factor_records_match_summary_and_keep_missing_distinct(self):
        slots = [
            {'scheduled_at': f'2020-01-01T0{hour}:00:00+00:00',
             'result_window_end_at': f'2020-01-01T0{hour}:30:00+00:00'}
            for hour in (1, 2, 3)
        ]
        config = {'endpoint_id': 10, 'business_metric': 'ph', 'instrument_asset_code': None,
                  'expected_interval_seconds': 3600}
        observations = [{
            'id': 1, 'business_site_id': 1, 'endpoint_id': 10, 'business_metric': 'ph',
            'instrument_asset_code': None, 'scheduled_at': slots[0]['scheduled_at'],
            'observed_at': slots[0]['scheduled_at'], 'standard_value': 10, 'standard_unit': '',
        }]
        replacements = {
            '_formal_monitoring_schema_ready': True,
            '_station_service_policy_events_batch': {},
            '_station_service_policy_rows_batch': {1: {'service_status': 'active', 'reporting_condition': 'normal'}},
            'monitoring_factor_configurations_batch': {1: [config]},
            'monitoring_expected_business_slots': slots,
            '_formal_business_observation_rows': observations,
            '_site_reporting_required_at': True,
        }
        with ExitStack() as stack:
            for name, value in replacements.items():
                stack.enter_context(patch.object(app_module, name, return_value=value))
            query = 'period=custom&start=2020-01-01&end=2020-01-01'
            headers = {'Authorization': 'Bearer admin-token'}
            summary = self.client.get(f'/api/data/health?{query}', headers=headers)
            detail = self.client.get(f'/api/data/health/records?{query}&site_id=1&metric=ph', headers=headers)
        self.assertEqual(summary.status_code, 200, summary.json)
        self.assertEqual(detail.status_code, 200, detail.json)
        metric = summary.json['by_site'][0]['metrics'][0]
        records = detail.json['records']
        self.assertEqual((metric['expected'], metric['actual'], metric['missing'], metric['over_limit']), (3, 1, 2, 1))
        self.assertEqual((len(records), sum(not r['missing'] for r in records),
                          sum(r['missing'] for r in records), sum(r['over_limit'] for r in records)), (3, 1, 2, 1))
        self.assertTrue(all(r['observation'] is None for r in records if r['missing']))

    def test_factor_records_validate_scope_and_legacy_storage(self):
        headers = {'Authorization': 'Bearer admin-token'}
        self.assertEqual(self.client.get('/api/data/health/records', headers=headers).status_code, 400)
        response = self.client.get('/api/data/health/records?site_id=1&metric=ph', headers=headers)
        self.assertEqual(response.status_code, 409)
        with patch.object(app_module, '_filter_site_ids', return_value=[]):
            response = self.client.get('/api/data/health/records?site_id=1&metric=ph', headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_report_scope_matches_review_access_without_changing_default_scope(self):
        headers = {'Authorization': 'Bearer admin-token'}
        with patch.object(app_module, '_filter_site_ids', return_value=[]), \
             patch.object(app_module, '_readable_site_ids', return_value=None), \
             patch.object(app_module, 'require_reviewer', return_value=None):
            ordinary = self.client.get('/api/data/health', headers=headers)
            report = self.client.get('/api/data/health?scope=report', headers=headers)
        self.assertEqual(ordinary.status_code, 200, ordinary.json)
        self.assertEqual(report.status_code, 200, report.json)
        self.assertEqual(len(ordinary.json['by_site']), 0)
        self.assertEqual(len(report.json['by_site']), 1)


if __name__ == '__main__':
    unittest.main()
