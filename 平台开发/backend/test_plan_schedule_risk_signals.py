import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class PlanScheduleRiskSignalRouteTest(unittest.TestCase):
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
        app_module._tokens['operator-token'] = {'id': 9, 'role': 'operator', 'real_name': '测试运维'}
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT, status TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, site_id INTEGER, title TEXT, level TEXT, created_at TEXT,
                    status TEXT, source TEXT, event_type TEXT
                );
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, site_id INTEGER, level TEXT, metric TEXT, status TEXT);
                CREATE TABLE manual_reports (
                    id INTEGER PRIMARY KEY, site_id INTEGER, report_type TEXT, description TEXT,
                    status TEXT, reported_at TEXT
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, schedule_type TEXT, period_start TEXT,
                    period_end TEXT, plan_data TEXT, vehicle_days TEXT, spare_parts TEXT,
                    work_order_ids TEXT, status TEXT, remarks TEXT, tasks_generated INTEGER
                );
                CREATE TABLE plan_schedule_events (
                    id INTEGER PRIMARY KEY, schedule_id INTEGER, version INTEGER, event_type TEXT,
                    operator_id INTEGER, payload TEXT
                );
            ''')
            db.execute("INSERT INTO users VALUES (9, '测试运维', 'operator', 'active')")
            db.execute('INSERT INTO user_sites VALUES (9, 1)')
            db.executemany('INSERT INTO sites VALUES (?,?)', [(1, '甲站'), (2, '乙站')])
            db.execute("""INSERT INTO manual_reports
                VALUES (1, 1, 'equipment', '采样泵异响', 'dispatched', datetime('now'))""")
        self.client = app_module.app.test_client()
        self.headers = {'Authorization': 'Bearer operator-token'}

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        os.unlink(self.db_path)

    def test_manual_report_is_visible_as_a_priority_signal(self):
        response = self.client.get('/api/plan-schedules/suggestions?site_ids=1', headers=self.headers)

        self.assertEqual(response.status_code, 200)
        report = next(item for item in response.json['suggestions'] if item['type'] == 'manual_report')
        self.assertEqual(report['ref_id'], 1)
        self.assertGreater(response.json['site_scores']['1'], 0)

    def test_operator_cannot_query_another_site(self):
        response = self.client.get('/api/plan-schedules/suggestions?site_ids=2', headers=self.headers)

        self.assertEqual(response.status_code, 403)

    def test_systemic_inspection_anomaly_can_only_create_a_follow_up_draft(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.executemany("""INSERT INTO work_orders
                (id, site_id, title, level, created_at, status, source, event_type)
                VALUES (?,?,?,?,?,?,?,?)""", [
                (10, 1, '巡检异常', 'normal', '2026-07-10 08:00:00', 'closed', 'inspection', '设备异常'),
                (11, 1, '巡检异常', 'normal', '2026-07-24 08:00:00', 'closed', 'inspection', '设备异常'),
            ])
            db.commit()
        finally:
            db.close()

        listed = self.client.get('/api/plan-schedules/follow-up-recommendations', headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        item = listed.json['recommendations'][0]
        created = self.client.post('/api/plan-schedules/follow-up-recommendations', headers=self.headers, json={
            'user_id': item['user_id'], 'site_id': item['site_id'], 'anomaly_type': item['anomaly_type'],
        })

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json['schedule']['status'], 'draft')
        self.assertEqual(created.json['schedule']['tasks_generated'], 0)


if __name__ == '__main__':
    unittest.main()
