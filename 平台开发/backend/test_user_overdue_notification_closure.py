import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class UserOverdueNotificationClosureTest(unittest.TestCase):
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
        app_module._tokens.update({
            'admin-token': {'id': 1, 'username': 'admin', 'real_name': '管理员', 'role': 'admin', 'roles': ['admin']},
            'source-token': {'id': 2, 'username': 'source', 'real_name': '原运维', 'role': 'operator', 'roles': ['operator']},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, login_name TEXT, password_hash TEXT,
                    role TEXT, real_name TEXT, phone TEXT, status TEXT, auth_version INTEGER DEFAULT 1,
                    deleted_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT, UNIQUE(user_id, role));
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER, UNIQUE(user_id, site_id));
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, assignee_id INTEGER, assignee TEXT, plan_name TEXT,
                    generate_date TEXT, status TEXT, plan_schedule_id INTEGER
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, result TEXT, execution_status TEXT DEFAULT 'active'
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, period_start TEXT, period_end TEXT
                );
                CREATE TABLE work_orders (id INTEGER PRIMARY KEY, assignee TEXT, status TEXT);
                CREATE TABLE vehicle_applications (id INTEGER PRIMARY KEY, applicant_id INTEGER, status TEXT);
                CREATE TABLE operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT, action TEXT, target_type TEXT,
                    target_id INTEGER, operator TEXT, operator_id INTEGER, details TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id INTEGER, title TEXT, content TEXT, is_read INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO users VALUES (1,'admin','管理员','x','admin','管理员','','active',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (2,'source','原运维','x','operator','原运维','','active',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (3,'target','接收人','x','operator','接收人','','active',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO user_roles VALUES (1,'admin');
                INSERT INTO user_roles VALUES (2,'operator');
                INSERT INTO user_roles VALUES (3,'operator');
                INSERT INTO sites VALUES (1,'测试站');
                INSERT INTO user_sites VALUES (2,1);
                INSERT INTO insp_plans VALUES (10,2,'原运维','逾期巡检','2026-01-01','active',20);
                INSERT INTO insp_plan_items VALUES (100,10,NULL,'active');
                INSERT INTO insp_plan_items VALUES (101,10,'已完成','active');
                INSERT INTO plan_schedules VALUES (20,2,'approved','2026-01-01','2026-01-07');
                INSERT INTO work_orders VALUES (30,'原运维','in_progress');
                INSERT INTO notifications (user_id,source_type,source_id,title,content)
                    VALUES (1,'plan_schedule',20,'有新的巡检计划待审批','历史通知');
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token='admin-token'):
        return {'Authorization': f'Bearer {token}'}

    def test_pending_work_must_transfer_before_disable_and_preserves_history(self):
        blocked = self.client.put('/api/users/2/status', headers=self.headers(), json={'status': 'inactive'})
        self.assertEqual(blocked.status_code, 409, blocked.json)
        self.assertEqual(blocked.json['pending_work']['巡检计划'], 1)
        transferred = self.client.post('/api/users/2/transfer-work', headers=self.headers(), json={'target_user_id': 3})
        self.assertEqual(transferred.status_code, 200, transferred.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT assignee_id FROM insp_plans WHERE id=10').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT user_id FROM plan_schedules WHERE id=20').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT assignee FROM work_orders WHERE id=30').fetchone()[0], '接收人')
            self.assertIsNotNone(db.execute("SELECT 1 FROM notifications WHERE user_id=3 AND source_type='user_work_transfer'").fetchone())
        disabled = self.client.put('/api/users/2/status', headers=self.headers(), json={'status': 'inactive'})
        self.assertEqual(disabled.status_code, 200, disabled.json)
        deleted = self.client.delete('/api/users/2', headers=self.headers())
        self.assertEqual(deleted.status_code, 200, deleted.json)
        with app_module.get_db() as db:
            row = db.execute('SELECT status,deleted_at FROM users WHERE id=2').fetchone()
            self.assertEqual(row['status'], 'inactive')
            self.assertIsNotNone(row['deleted_at'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_roles WHERE user_id=2').fetchone()[0], 1)

    def test_open_vehicle_application_cannot_be_transferred(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO vehicle_applications VALUES (1,2,'approved')")
        response = self.client.post('/api/users/2/transfer-work', headers=self.headers(), json={'target_user_id': 3})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn('不能转交', response.json['error'])

    def test_overdue_remind_and_close_preserve_completed_item(self):
        reminded = self.client.post('/api/insp-plans/10/overdue-action', headers=self.headers(), json={'action': 'remind'})
        self.assertEqual(reminded.status_code, 200, reminded.json)
        missing_reason = self.client.post('/api/insp-plans/10/overdue-action', headers=self.headers(), json={'action': 'close'})
        self.assertEqual(missing_reason.status_code, 400, missing_reason.json)
        closed = self.client.post('/api/insp-plans/10/overdue-action', headers=self.headers(), json={
            'action': 'close', 'reason': '道路封闭，已登记改期',
        })
        self.assertEqual(closed.status_code, 200, closed.json)
        self.assertEqual(closed.json['cancelled_items'], 1)
        with app_module.get_db() as db:
            items = db.execute('SELECT id,result,execution_status FROM insp_plan_items ORDER BY id').fetchall()
            self.assertEqual(items[0]['execution_status'], 'cancelled')
            self.assertEqual(items[1]['result'], '已完成')
            self.assertEqual(items[1]['execution_status'], 'active')
            self.assertEqual(db.execute('SELECT status FROM insp_plans WHERE id=10').fetchone()[0], 'cancelled')

    def test_stale_plan_notification_is_removed_from_unread_count(self):
        response = self.client.get('/api/notifications', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['unread_count'], 0)
        notice = response.json['notifications'][0]
        self.assertTrue(notice['is_stale'])
        self.assertEqual(notice['current_status'], 'approved')
        self.assertEqual(notice['is_read'], 1)


if __name__ == '__main__':
    unittest.main()
