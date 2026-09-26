"""Legacy weekly-plan assignee guard, exercised through the real Flask API."""

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class WeeklyPlanAssigneeApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, 'weekly.db')
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_sites = dict(app_module._site_ids_cache)

        @contextmanager
        def isolated_db():
            db = sqlite3.connect(self.db_path, timeout=3)
            db.row_factory = sqlite3.Row
            try:
                yield db
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        app_module.get_db = isolated_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'admin-token': {'id': 1, 'role': 'admin', 'roles': ['admin'], 'username': 'admin'},
            'operator-token': {'id': 2, 'role': 'operator', 'roles': ['operator'], 'username': 'operator'},
        })
        with isolated_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, status TEXT, deleted_at TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE weekly_inspection_plans (
                    id INTEGER PRIMARY KEY, user_id INTEGER, week_start TEXT, plan_data TEXT,
                    vehicle_id INTEGER, status TEXT, remarks TEXT, submitted_at TEXT,
                    approver_id INTEGER
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER,
                    start_at TEXT, end_at TEXT, destination TEXT, reason TEXT, status TEXT
                );
                INSERT INTO users VALUES (1, '管理员', 'active', NULL);
                INSERT INTO users VALUES (2, '运维', 'active', NULL);
                INSERT INTO users VALUES (3, '停用', 'inactive', NULL);
                INSERT INTO users VALUES (4, '注销', 'inactive', '2026-09-23 12:00:00');
                INSERT INTO users VALUES (5, '未停用但已注销', 'active', '2026-09-23 12:00:00');
                INSERT INTO sites VALUES (11, '测试站点');
                INSERT INTO user_sites VALUES (2, 11);
                INSERT INTO weekly_inspection_plans
                    (id, user_id, week_start, plan_data, status)
                    VALUES (100, 4, '2026-09-21', '{}', 'draft');
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_sites)
        self.temp_dir.cleanup()

    def counts(self):
        with app_module.get_db() as db:
            return tuple(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                         for table in ('weekly_inspection_plans', 'vehicle_applications'))

    def post(self, user_id, token='admin-token', **overrides):
        payload = {'user_id': user_id, 'week_start': '2026-09-28', 'plan_data': {}}
        payload.update(overrides)
        return self.client.post('/api/weekly-plans', json=payload,
                                headers={'Authorization': 'Bearer ' + token})

    def test_invalid_assignees_are_rejected_before_plan_or_vehicle_insert(self):
        for user_id, status, code in (
            (999999, 404, 'PLAN_EXECUTION_USER_NOT_FOUND'),
            (3, 409, 'PLAN_EXECUTION_USER_INACTIVE'),
            (4, 409, 'PLAN_EXECUTION_USER_INACTIVE'),
            (5, 409, 'PLAN_EXECUTION_USER_INACTIVE'),
        ):
            with self.subTest(user_id=user_id):
                before = self.counts()
                response = self.post(user_id, submit=True, vehicle_id=17,
                                     plan_data={'2026-09-28': [11]})
                self.assertEqual((response.status_code, response.json.get('code')),
                                 (status, code))
                self.assertTrue(response.json.get('error'))
                self.assertEqual(self.counts(), before)
                retry = self.post(user_id, submit=True, vehicle_id=17,
                                  plan_data={'2026-09-28': [11]})
                self.assertEqual((retry.status_code, retry.json.get('code')), (status, code))
                self.assertEqual(self.counts(), before)

    def test_valid_admin_delegation_and_self_assignment(self):
        for token in ('admin-token', 'operator-token'):
            before = self.counts()
            response = self.post(2, token=token, submit=True, vehicle_id=17,
                                 plan_data={'2026-09-28': [11]})
            self.assertEqual(response.status_code, 201, response.json)
            self.assertEqual(response.json['user_id'], 2)
            self.assertEqual(tuple(n - o for n, o in zip(self.counts(), before)), (1, 1))

    def test_unauthorized_delegation_and_invalid_parameters_have_no_writes(self):
        for user_id, token, status in (
            (3, 'operator-token', 403), (999999, 'operator-token', 403),
            ('bad', 'admin-token', 400), (None, 'admin-token', 400),
            (0, 'admin-token', 400),
        ):
            with self.subTest(user_id=user_id, token=token):
                before = self.counts()
                response = self.post(user_id, token=token)
                self.assertEqual(response.status_code, status, response.json)
                self.assertEqual(self.counts(), before)

    def test_user_id_requires_positive_json_integer_in_sqlite_id_range(self):
        for raw_user_id in (True, False, 2.9, 2.0, '2', '', 0, -1,
                            None, 2**63, -(2**63) - 1):
            with self.subTest(raw_user_id=raw_user_id):
                before = self.counts()
                response = self.post(raw_user_id, submit=True, vehicle_id=17,
                                     plan_data={'2026-09-28': [11]})
                self.assertEqual(response.status_code, 400, response.json)
                self.assertTrue(response.json.get('error'))
                self.assertEqual(self.counts(), before)
        before = self.counts()
        missing = self.client.post('/api/weekly-plans',
                                   json={'week_start': '2026-09-28'},
                                   headers={'Authorization': 'Bearer admin-token'})
        self.assertEqual(missing.status_code, 400, missing.json)
        self.assertEqual(self.counts(), before)

    def test_non_object_json_body_is_rejected_without_writes(self):
        for payload in ([2], '2', True):
            with self.subTest(payload=payload):
                before = self.counts()
                response = self.client.post('/api/weekly-plans', json=payload,
                                            headers={'Authorization': 'Bearer admin-token'})
                self.assertEqual(response.status_code, 400, response.json)
                self.assertTrue(response.json.get('error'))
                self.assertEqual(self.counts(), before)

    def test_historical_plan_stays_readable_after_user_deletion(self):
        response = self.client.get('/api/weekly-plans',
                                   headers={'Authorization': 'Bearer admin-token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json[0]['id'], 100)
        self.assertEqual(response.json[0]['user_name'], '注销')

    def test_vehicle_failure_rolls_back_plan_too(self):
        with app_module.get_db() as db:
            db.execute('''CREATE TRIGGER fail_weekly_vehicle BEFORE INSERT ON vehicle_applications
                          BEGIN SELECT RAISE(ABORT, 'forced vehicle failure'); END''')
            db.commit()
        before = self.counts()
        response = self.post(2, submit=True, vehicle_id=17,
                             plan_data={'2026-09-28': [11]})
        self.assertEqual((response.status_code, response.json.get('code')),
                         (503, 'WEEKLY_PLAN_RETRYABLE'))
        self.assertTrue(response.json.get('error'))
        self.assertEqual(self.counts(), before)

    def test_busy_database_returns_retryable_error_without_writes(self):
        before = self.counts()
        locker = sqlite3.connect(self.db_path, timeout=0.1)
        locker.execute('BEGIN IMMEDIATE')
        try:
            response = self.post(2)
            self.assertEqual((response.status_code, response.json.get('code')),
                             (503, 'WEEKLY_PLAN_RETRYABLE'))
            self.assertTrue(response.json.get('error'))
        finally:
            locker.rollback()
            locker.close()
        self.assertEqual(self.counts(), before)


if __name__ == '__main__':
    unittest.main()
