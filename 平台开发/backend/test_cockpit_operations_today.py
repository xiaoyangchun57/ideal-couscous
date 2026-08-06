import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class CockpitOperationsTodayTest(unittest.TestCase):
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
                db.commit()
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'admin-token': {'id': 1, 'role': 'admin', 'real_name': '管理员'},
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': '甲运维'},
        })
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT, status TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT, PRIMARY KEY(user_id, role));
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT,
                    period_start TEXT, period_end TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_name TEXT, assignee_id INTEGER,
                    generate_date TEXT, status TEXT
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, result TEXT,
                    completed_at TEXT, required_photos INTEGER, actual_photos INTEGER,
                    execution_status TEXT
                );
                CREATE TABLE inspection_checkins (
                    id INTEGER PRIMARY KEY, site_id INTEGER, site_name TEXT,
                    user_id INTEGER, check_time TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER, title TEXT,
                    status TEXT, assignee TEXT, created_at TEXT, check_in_time TEXT,
                    resolved_at TEXT
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?)', [
                (1, '管理员', 'admin', 'active'),
                (2, '甲运维', 'operator', 'active'),
                (3, '乙运维', 'operator', 'active'),
                (4, '丙运维', 'operator', 'active'),
                (5, '丁运维', 'operator', 'active'),
                (6, '双角色人员', 'admin', 'active'),
                (7, '测试用户', 'operator', 'active'),
                (8, '停用运维', 'operator', 'inactive'),
            ])
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [
                (1, 'admin'), (2, 'operator'), (3, 'operator'), (4, 'operator'),
                (5, 'operator'), (6, 'admin'), (6, 'operator'), (7, 'operator'), (8, 'operator'),
            ])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(2, 10), (3, 11)])
            db.executemany('INSERT INTO sites VALUES (?,?)', [(10, '甲站'), (11, '乙站')])
            db.executemany('INSERT INTO plan_schedules VALUES (?,?,?,?,?)', [
                (20, 2, 'approved', today, today),
                (21, 3, 'approved', today, today),
            ])
            db.executemany('INSERT INTO insp_plans VALUES (?,?,?,?,?)', [
                (30, '甲今日周检', 2, today, 'active'),
                (31, '乙今日周检', 3, today, 'active'),
                (32, '甲昨日遗留', 2, yesterday, 'active'),
                (33, '丁昨日遗留', 5, yesterday, 'active'),
            ])
            db.executemany('INSERT INTO insp_plan_items VALUES (?,?,?,?,?,?,?,?)', [
                (40, 30, 10, 'normal', today + ' 09:05:00', 1, 1, 'active'),
                (41, 30, 10, None, None, 1, 0, 'active'),
                (42, 31, 11, None, None, 1, 0, 'active'),
                (43, 32, 10, None, None, 0, 0, 'active'),
                (44, 33, 11, None, None, 0, 0, 'active'),
            ])
            db.execute('INSERT INTO inspection_checkins VALUES (?,?,?,?,?)',
                       (50, 10, '甲站', 2, today + ' 08:42:00'))
            db.execute('INSERT INTO work_orders VALUES (?,?,?,?,?,?,?,?,?)',
                       (60, 'WO-1', 10, '现场工单', 'in_progress', '甲运维',
                        today + ' 08:00:00', today + ' 08:50:00', None))
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
        return {'Authorization': 'Bearer ' + token}

    def test_admin_sees_people_status_checkin_and_attention(self):
        response = self.client.get('/api/cockpit/operations-today', headers=self.headers('admin-token'))
        self.assertEqual(response.status_code, 200, response.json)
        people = {row['real_name']: row for row in response.json['people']}
        self.assertEqual(response.json['summary']['people_with_tasks'], 2)
        self.assertEqual(response.json['summary']['people_checked_in'], 1)
        self.assertEqual(people['甲运维']['first_checkin_at'][-8:], '08:42:00')
        self.assertEqual(people['甲运维']['latest_site_name'], '甲站')
        self.assertEqual(people['甲运维']['status_code'], 'mixed')
        self.assertEqual(people['甲运维']['completed_items'], 1)
        self.assertEqual(people['甲运维']['today_items'], 2)
        self.assertEqual(people['甲运维']['carryover_executions'], 1)
        self.assertEqual(people['乙运维']['status_code'], 'no_checkin')
        self.assertEqual(people['丙运维']['status_code'], 'no_task')
        self.assertEqual(people['丁运维']['status_code'], 'carryover')
        self.assertFalse(people['丁运维']['has_task'])
        self.assertIn('双角色人员', people)
        self.assertNotIn('测试用户', people)
        self.assertNotIn('停用运维', people)
        attention_types = {(row['real_name'], row['type']) for row in response.json['attention']}
        self.assertIn(('甲运维', 'carryover'), attention_types)
        self.assertIn(('乙运维', 'no_checkin'), attention_types)
        self.assertIn(('丁运维', 'carryover'), attention_types)

    def test_operator_only_sees_self(self):
        response = self.client.get('/api/cockpit/operations-today', headers=self.headers('operator-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([row['real_name'] for row in response.json['people']], ['甲运维'])


if __name__ == '__main__':
    unittest.main()
