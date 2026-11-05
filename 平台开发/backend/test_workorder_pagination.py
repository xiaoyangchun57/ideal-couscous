import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class WorkorderPaginationTest(unittest.TestCase):
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

        self.temporary_db = temporary_db
        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'admin-token': {
                'id': 1, 'username': 'admin', 'real_name': '管理员',
                'role': 'admin', 'roles': ['admin'],
            },
            'operator-token': {
                'id': 2, 'username': 'operator', 'real_name': '现场人员',
                'role': 'operator', 'roles': ['operator'],
            },
            'other-token': {
                'id': 3, 'username': 'other', 'real_name': '其他人员',
                'role': 'operator', 'roles': ['operator'],
            },
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, role TEXT,
                    real_name TEXT, status TEXT
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, status TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER,
                    source TEXT, event_type TEXT, level TEXT, title TEXT,
                    description TEXT, images TEXT, assignee TEXT, status TEXT,
                    created_at TEXT, check_in_time TEXT, check_in_user TEXT,
                    resolved_at TEXT, related_alert_id INTEGER, used_parts TEXT
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, status TEXT, related_order_no TEXT
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT, created_at TEXT
                );
                INSERT INTO users VALUES
                    (1,'admin','admin','管理员','active'),
                    (2,'operator','operator','现场人员','active'),
                    (3,'other','operator','其他人员','active');
                INSERT INTO user_roles VALUES
                    (1,'admin'),(2,'operator'),(3,'operator');
                INSERT INTO user_sites VALUES (2,7),(2,9),(3,8);
                INSERT INTO sites VALUES
                    (7,'普通站','online'),(8,'无权站','online'),(9,'目标站','online');
            ''')
            rows = []
            for index in range(1, 60):
                rows.append((
                    index,
                    f'WO-20261102-{index:03d}',
                    7,
                    'manual',
                    '',
                    'normal',
                    f'普通工单 {index}',
                    '分页样本',
                    '[]',
                    '负责人',
                    'pending',
                    f'2026-11-02 10:{index % 60:02d}:00',
                    None, None, None, None, '',
                ))
            rows.append((
                60, 'WO-TARGET-LATE', 9, 'manual', '', 'critical',
                '后页目标泵站故障', '只存在于最后一页', '[]', '负责人',
                'closed', '2026-10-01 08:00:00', None, None,
                '2026-10-01 12:00:00', None, '',
            ))
            db.executemany(
                '''INSERT INTO work_orders VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                rows,
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
    def headers(token='admin-token'):
        return {'Authorization': f'Bearer {token}'}

    def test_legacy_default_remains_array_with_recent_limit(self):
        response = self.client.get('/api/workorders', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsInstance(response.json, list)
        self.assertEqual(len(response.json), 50)
        self.assertNotIn('WO-TARGET-LATE', {item['order_no'] for item in response.json})

        pending = self.client.get('/api/workorders?status=pending', headers=self.headers())
        self.assertIsInstance(pending.json, list)
        self.assertEqual(len(pending.json), 50)

    def test_paginated_response_reaches_later_page_and_reports_total(self):
        first = self.client.get(
            '/api/workorders?paginated=1&page=1&page_size=20',
            headers=self.headers(),
        )
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual((first.json['total'], first.json['page'], first.json['page_size']), (60, 1, 20))
        self.assertEqual(first.json['total_pages'], 3)
        self.assertEqual(len(first.json['items']), 20)

        last = self.client.get(
            '/api/workorders?paginated=1&page=3&page_size=20',
            headers=self.headers(),
        )
        self.assertEqual(len(last.json['items']), 20)
        self.assertIn('WO-TARGET-LATE', {item['order_no'] for item in last.json['items']})

    def test_server_search_and_filters_find_only_later_page_target(self):
        cases = [
            'search=WO-TARGET-LATE',
            'search=%E6%B3%B5%E7%AB%99%E6%95%85%E9%9A%9C',
            'search=%E7%9B%AE%E6%A0%87%E7%AB%99',
            'status=closed',
            'level=critical',
        ]
        for query in cases:
            with self.subTest(query=query):
                response = self.client.get(
                    f'/api/workorders?paginated=1&page=1&page_size=20&{query}',
                    headers=self.headers(),
                )
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['total'], 1)
                self.assertEqual(response.json['items'][0]['order_no'], 'WO-TARGET-LATE')
        status = self.client.get(
            '/api/workorders?paginated=1&status=closed', headers=self.headers(),
        )
        self.assertEqual(status.json['counts'], {'closed': 1})

    def test_direct_target_and_paginated_results_remain_site_scoped(self):
        allowed = self.client.get(
            '/api/workorders/WO-TARGET-LATE', headers=self.headers('operator-token'),
        )
        self.assertEqual(allowed.status_code, 200, allowed.json)

        denied_list = self.client.get(
            '/api/workorders?paginated=1&page=1&page_size=20&search=WO-TARGET-LATE',
            headers=self.headers('other-token'),
        )
        self.assertEqual((denied_list.status_code, denied_list.json['total']), (200, 0))
        self.assertEqual(denied_list.json['items'], [])
        denied_detail = self.client.get(
            '/api/workorders/WO-TARGET-LATE', headers=self.headers('other-token'),
        )
        self.assertEqual(denied_detail.status_code, 403, denied_detail.json)


if __name__ == '__main__':
    unittest.main()
