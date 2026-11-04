import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class PlanSchedulePaginationTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_cache = dict(app_module._site_ids_cache)
        self.original_helpers = {
            '_ps_parse_row': app_module._ps_parse_row,
            '_ps_sync_field_status': app_module._ps_sync_field_status,
            '_ps_execution_item_summaries': app_module._ps_execution_item_summaries,
            '_ps_schedule_execution_status': app_module._ps_schedule_execution_status,
        }

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
            'admin-token': {'id': 1, 'username': 'admin', 'real_name': '管理员', 'role': 'admin', 'roles': ['admin']},
            'operator-token': {'id': 2, 'username': 'operator', 'real_name': '运维员', 'role': 'operator', 'roles': ['operator']},
        })
        def parse_schedule(row):
            item = dict(row)
            item['plan_data'] = json.loads(item.get('plan_data') or '{}')
            item['validation_snapshot'] = json.loads(item.get('validation_snapshot') or '{}')
            return item

        app_module._ps_parse_row = parse_schedule
        app_module._ps_sync_field_status = lambda _db, _schedule_id: 'active'
        app_module._ps_execution_item_summaries = lambda _db, _ids: {}
        app_module._ps_schedule_execution_status = lambda *_args, **_kwargs: {}

        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, real_name TEXT, role TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT,
                    schedule_type TEXT, period_start TEXT, period_end TEXT,
                    created_at TEXT, plan_data TEXT, validation_snapshot TEXT,
                    coverage_exception_reason TEXT, vehicle_adjustment_required INTEGER DEFAULT 0,
                    approver_id INTEGER
                );
                INSERT INTO users VALUES (1,'admin','管理员','admin');
                INSERT INTO users VALUES (2,'operator','运维员','operator');
                INSERT INTO user_roles VALUES (1,'admin');
                INSERT INTO user_roles VALUES (2,'operator');
            ''')
            rows = []
            for index in range(1, 31):
                owner = 2 if index <= 5 else 1
                rows.append((
                    index, owner, 'approved', 'weekly', f'2026-10-{index:02d}',
                    f'2026-10-{index:02d}', f'2026-10-{index:02d} 08:00:00',
                    json.dumps({'2026-10-01': {'sites': [index]}}), '{}', '', 0, 1,
                ))
            db.executemany('INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        for name, value in self.original_helpers.items():
            setattr(app_module, name, value)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def test_plan_list_keeps_legacy_array_and_pages_complete_authorized_results(self):
        legacy = self.client.get('/api/plan-schedules', headers=self.headers('admin-token'))
        self.assertEqual(legacy.status_code, 200, legacy.json)
        self.assertIsInstance(legacy.json, list)
        self.assertEqual(len(legacy.json), 30)

        first = self.client.get(
            '/api/plan-schedules?paginated=1&page=1&page_size=20',
            headers=self.headers('admin-token'),
        )
        last = self.client.get(
            '/api/plan-schedules?paginated=1&page=2&page_size=20',
            headers=self.headers('admin-token'),
        )
        self.assertEqual((first.status_code, last.status_code), (200, 200))
        self.assertEqual(first.json['total'], 30)
        self.assertEqual(len(first.json['items']), 20)
        self.assertEqual(len(last.json['items']), 10)
        ids = [row['id'] for row in first.json['items'] + last.json['items']]
        self.assertEqual(len(ids), len(set(ids)))

        mine = self.client.get(
            '/api/plan-schedules?paginated=1&page=1&page_size=20',
            headers=self.headers('operator-token'),
        )
        self.assertEqual(mine.status_code, 200, mine.json)
        self.assertEqual(mine.json['total'], 5)
        self.assertEqual({row['user_id'] for row in mine.json['items']}, {2})


if __name__ == '__main__':
    unittest.main()
