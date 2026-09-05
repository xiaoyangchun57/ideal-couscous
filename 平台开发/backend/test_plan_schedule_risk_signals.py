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
                CREATE TABLE user_roles (user_id INTEGER, role TEXT, UNIQUE(user_id, role));
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, site_id INTEGER, title TEXT, level TEXT, created_at TEXT,
                    status TEXT, source TEXT, event_type TEXT, order_no TEXT
                );
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, site_id INTEGER, level TEXT, metric TEXT,
                    status TEXT, related_order_no TEXT);
                CREATE TABLE manual_reports (
                    id INTEGER PRIMARY KEY, site_id INTEGER, report_type TEXT, description TEXT,
                    status TEXT, reported_at TEXT, order_no TEXT
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
            db.execute("INSERT INTO user_roles VALUES (9, 'operator')")
            db.execute('INSERT INTO user_sites VALUES (9, 1)')
            db.executemany('INSERT INTO sites VALUES (?,?)', [(1, '甲站'), (2, '乙站')])
            db.execute("""INSERT INTO manual_reports
                VALUES (1, 1, 'equipment', '采样泵异响', 'dispatched', datetime('now'), NULL)""")
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
        with app_module.get_db() as db:
            db.executemany("INSERT INTO alerts VALUES (?,?,?,?,?,NULL)", [
                (10, 1, 'yellow', 'manual_report', 'pending'),
                (11, 1, 'yellow', 'turbidity', 'pending'),
            ])
        response = self.client.get('/api/plan-schedules/suggestions?site_ids=1', headers=self.headers)

        self.assertEqual(response.status_code, 200)
        report = next(item for item in response.json['suggestions'] if item['type'] == 'manual_report')
        self.assertEqual(report['ref_id'], 1)
        self.assertGreater(response.json['site_scores']['1'], 0)
        visible_text = '\n'.join(item['text'] for item in response.json['suggestions'])
        reason_text = '\n'.join(response.json['site_reasons']['1'])
        for expected in ('人工上报', '浊度', '设备'):
            self.assertIn(expected, visible_text + '\n' + reason_text)
        for internal in ('manual_report', 'turbidity', 'equipment'):
            self.assertNotIn(internal, visible_text + '\n' + reason_text)

    def test_linked_manual_report_alert_and_active_order_count_once(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO work_orders
                (id,site_id,title,level,created_at,status,source,event_type,order_no)
                VALUES (20,1,'【人工上报】感官异常','urgent',datetime('now'),'pending',
                        'manual_report','sensory','WO-MANUAL')""")
            db.execute("""INSERT INTO manual_reports
                VALUES (2,1,'sensory','异味','dispatched',datetime('now'),'WO-MANUAL')""")
            db.execute("""INSERT INTO alerts
                VALUES (30,1,'yellow','manual_report','pending','WO-MANUAL')""")

        response = self.client.get('/api/plan-schedules/suggestions?site_ids=1', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        linked = [item for item in response.json['suggestions'] if item.get('ref_id') in (20, 30, 2)]
        self.assertEqual([(item['type'], item['ref_id']) for item in linked], [('work_order', 20)])
        self.assertFalse(any(item['type'] == 'priority' for item in response.json['suggestions']))
        linked_reasons = [reason for reason in response.json['site_reasons']['1']
                          if '感官异常' in reason or 'manual_report' in reason or 'sensory' in reason]
        self.assertEqual(len(linked_reasons), 1)

        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='closed' WHERE id=20")
        closed = self.client.get('/api/plan-schedules/suggestions?site_ids=1', headers=self.headers)
        self.assertEqual(closed.status_code, 200, closed.json)
        self.assertEqual({item['type'] for item in closed.json['suggestions'] if item.get('ref_id') in (30, 2)},
                         {'alert', 'manual_report'})

    def test_operator_cannot_query_another_site(self):
        response = self.client.get('/api/plan-schedules/suggestions?site_ids=2', headers=self.headers)

        self.assertEqual(response.status_code, 403)

    def test_systemic_follow_up_routes_are_absent_without_plan_writes(self):
        with app_module.get_db() as db:
            before = db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0]

        created = self.client.post('/api/plan-schedules/follow-up-recommendations', headers=self.headers, json={
            'user_id': 9, 'site_id': 1, 'anomaly_type': '设备异常',
        })

        route = '/api/plan-schedules/follow-up-recommendations'
        self.assertFalse(any(str(rule) == route for rule in app_module.app.url_map.iter_rules()))
        self.assertEqual(created.status_code, 405)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], before)


if __name__ == '__main__':
    unittest.main()
