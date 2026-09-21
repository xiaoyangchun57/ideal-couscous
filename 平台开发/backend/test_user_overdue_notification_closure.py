import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
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
                    id INTEGER PRIMARY KEY, plan_id INTEGER, result TEXT,
                    execution_status TEXT DEFAULT 'active', site_id INTEGER DEFAULT 1
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
                    dedupe_key TEXT DEFAULT '', payload_json TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, site_id INTEGER, is_deleted INTEGER DEFAULT 0,
                    review_required INTEGER DEFAULT 1, review_status TEXT DEFAULT 'pending',
                    source_type TEXT DEFAULT 'inspection', source_id INTEGER DEFAULT 0,
                    uploader_id INTEGER
                );
                INSERT INTO users VALUES (1,'admin','管理员','x','admin','管理员','','active',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (2,'source','原运维','x','operator','原运维','','active',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (3,'target','接收人','x','operator','接收人','','active',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (4,'mistake','误建账号','x','operator','误建账号','','active',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (5,'reviewer','审核账号','x','reviewer','审核账号','','active',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (6,'inactive','停用运维','x','operator','停用运维','','inactive',1,NULL,CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (7,'closed_history','关闭工单人员','x','operator','关闭工单人员','','inactive',1,'2026-09-20 10:00:00',CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (8,'timeline_history','时间线人员','x','operator','时间线人员','','inactive',1,'2026-09-20 10:00:00',CURRENT_TIMESTAMP);
                INSERT INTO users VALUES (9,'concurrent_history','并发历史人员','x','operator','并发历史人员','','inactive',1,'2026-09-20 10:00:00',CURRENT_TIMESTAMP);
                INSERT INTO user_roles VALUES (1,'admin');
                INSERT INTO user_roles VALUES (2,'operator');
                INSERT INTO user_roles VALUES (3,'operator');
                INSERT INTO user_roles VALUES (4,'operator');
                INSERT INTO user_roles VALUES (5,'reviewer');
                INSERT INTO user_roles VALUES (6,'operator');
                INSERT INTO user_roles VALUES (7,'operator');
                INSERT INTO user_roles VALUES (8,'operator');
                INSERT INTO user_roles VALUES (9,'operator');
                INSERT INTO sites VALUES (1,'测试站');
                INSERT INTO sites VALUES (2,'第二站');
                INSERT INTO user_sites VALUES (2,1);
                INSERT INTO user_sites VALUES (3,2);
                INSERT INTO user_sites VALUES (4,2);
                INSERT INTO insp_plans VALUES (10,2,'原运维','逾期巡检','2026-01-01','active',20);
                INSERT INTO insp_plan_items VALUES (100,10,NULL,'active',1);
                INSERT INTO insp_plan_items VALUES (101,10,'已完成','active',1);
                INSERT INTO insp_plan_items VALUES (102,10,NULL,'active',1);
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
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_sites WHERE user_id=2').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_sites WHERE user_id=3 AND site_id=1').fetchone()[0], 1)
        disabled = self.client.put('/api/users/2/status', headers=self.headers(), json={'status': 'inactive'})
        self.assertEqual(disabled.status_code, 200, disabled.json)
        deleted = self.client.delete('/api/users/2', headers=self.headers())
        self.assertEqual(deleted.status_code, 200, deleted.json)
        with app_module.get_db() as db:
            row = db.execute('SELECT status,deleted_at FROM users WHERE id=2').fetchone()
            self.assertEqual(row['status'], 'inactive')
            self.assertIsNotNone(row['deleted_at'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_roles WHERE user_id=2').fetchone()[0], 1)

    def test_unique_site_owner_cannot_be_deactivated_before_transfer(self):
        with app_module.get_db() as db:
            db.execute("UPDATE insp_plans SET status='completed' WHERE id=10")
            db.execute("UPDATE plan_schedules SET status='archived' WHERE id=20")
            db.execute("UPDATE work_orders SET status='closed' WHERE id=30")
        response = self.client.delete('/api/users/2', headers=self.headers())
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'USER_SITE_TRANSFER_REQUIRED')
        self.assertEqual(response.json['sites'][0]['id'], 1)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT status FROM users WHERE id=2').fetchone()[0], 'active')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_sites WHERE user_id=2').fetchone()[0], 1)

    def test_permanent_delete_only_removes_unused_deactivated_identity(self):
        missing_reason = self.client.delete('/api/users/4/permanent', headers=self.headers(), json={})
        self.assertEqual(missing_reason.status_code, 400, missing_reason.json)
        forbidden = self.client.delete('/api/users/4/permanent', headers=self.headers('source-token'),
                                       json={'reason': '无效测试账号'})
        self.assertEqual(forbidden.status_code, 403, forbidden.json)
        current = self.client.delete('/api/users/1/permanent', headers=self.headers(),
                                     json={'reason': '不能删除自己'})
        self.assertEqual(current.status_code, 409, current.json)

        deactivated = self.client.delete('/api/users/4', headers=self.headers())
        self.assertEqual(deactivated.status_code, 200, deactivated.json)

        with app_module.get_db() as db:
            db.execute("""INSERT INTO operation_attachments
                (id,site_id,source_type,source_id,uploader_id) VALUES (1,1,'inspection',10,4)""")
        protected = self.client.delete('/api/users/4/permanent', headers=self.headers(),
                                       json={'reason': '不应删除有影像历史账号'})
        self.assertEqual(protected.status_code, 409, protected.json)
        self.assertEqual(protected.json['references']['operation_attachments'], 1)
        with app_module.get_db() as db:
            db.execute('DELETE FROM operation_attachments WHERE id=1')

        deleted = self.client.delete('/api/users/4/permanent', headers=self.headers(),
                                     json={'reason': '录入错误且从未使用'})
        replay = self.client.delete('/api/users/4/permanent', headers=self.headers(),
                                    json={'reason': '重复请求'})
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertEqual(replay.status_code, 200, replay.json)
        self.assertTrue(replay.json['already_deleted'])
        with app_module.get_db() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM users WHERE id=4').fetchone())
            audit = db.execute('''SELECT reason,real_name,roles_json,site_ids_json
                FROM user_deletion_audits WHERE user_id=4''').fetchone()
            self.assertEqual((audit['reason'], audit['real_name']), ('录入错误且从未使用', '误建账号'))
            self.assertEqual(json.loads(audit['roles_json']), ['operator'])
            self.assertEqual(json.loads(audit['site_ids_json']), [2])

        with app_module.get_db() as db:
            db.execute("UPDATE users SET status='inactive',deleted_at='2026-09-20 11:00:00' WHERE id=2")
        historical = self.client.delete('/api/users/2/permanent', headers=self.headers(),
                                        json={'reason': '不应删除有历史账号'})
        self.assertEqual(historical.status_code, 409, historical.json)
        self.assertEqual(historical.json['code'], 'USER_HISTORY_EXISTS')
        self.assertIn('insp_plans', historical.json['references'])

    def test_permanent_delete_blocks_closed_text_only_history(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO work_orders VALUES (71,'关闭工单人员','closed')")
            db.execute("""INSERT INTO timeline_events
                (source_type,source_id,event_type,operator,remark)
                VALUES ('workorder',72,'closed','时间线人员','已关闭')""")
        work_order = self.client.delete('/api/users/7/permanent', headers=self.headers(),
                                        json={'reason': '不应删除文本工单历史'})
        timeline = self.client.delete('/api/users/8/permanent', headers=self.headers(),
                                      json={'reason': '不应删除文本时间线历史'})
        self.assertEqual(work_order.status_code, 409, work_order.json)
        self.assertEqual(timeline.status_code, 409, timeline.json)
        self.assertEqual(work_order.json['references']['work_orders'], 1)
        self.assertEqual(timeline.json['references']['timeline_events'], 1)
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM users WHERE id=7').fetchone())
            self.assertIsNotNone(db.execute('SELECT 1 FROM users WHERE id=8').fetchone())

    def test_permanent_delete_rechecks_reference_after_concurrent_writer_commits(self):
        writer = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("""INSERT INTO timeline_events
            (source_type,source_id,event_type,operator,remark)
            VALUES ('workorder',73,'closed','并发历史人员','并发写入')""")
        result = {}
        started = threading.Event()

        def delete_user():
            started.set()
            client = app_module.app.test_client()
            result['response'] = client.delete('/api/users/9/permanent', headers=self.headers(),
                                               json={'reason': '并发校验'})

        worker = threading.Thread(target=delete_user)
        worker.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.1)
        writer.commit()
        writer.close()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        response = result['response']
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['references']['timeline_events'], 1)
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM users WHERE id=9').fetchone())

    def test_generic_user_update_cannot_bypass_status_transition(self):
        response = self.client.put('/api/users/2', headers=self.headers(), json={
            'real_name': '不应写入', 'status': 'inactive',
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(response.json['code'], 'USER_STATUS_ENDPOINT_REQUIRED')
        with app_module.get_db() as db:
            user = db.execute('SELECT real_name,status FROM users WHERE id=2').fetchone()
            self.assertEqual((user['real_name'], user['status']), ('原运维', 'active'))

    def test_user_site_assignment_validates_all_inputs_before_replace(self):
        cases = (
            (999, [2], 404, 'USER_NOT_FOUND'),
            (6, [2], 409, 'USER_INACTIVE'),
            (5, [2], 409, 'USER_OPERATOR_REQUIRED'),
            (2, [999], 404, 'SITE_NOT_FOUND'),
        )
        for uid, site_ids, expected_status, expected_code in cases:
            with self.subTest(uid=uid, site_ids=site_ids):
                response = self.client.put(f'/api/users/{uid}/sites', headers=self.headers(),
                                           json={'site_ids': site_ids})
                self.assertEqual(response.status_code, expected_status, response.json)
                self.assertEqual(response.json['code'], expected_code)
        duplicate = self.client.put('/api/users/2/sites', headers=self.headers(),
                                    json={'site_ids': [2, 2]})
        self.assertEqual(duplicate.status_code, 200, duplicate.json)
        self.assertEqual(duplicate.json['count'], 1)
        with app_module.get_db() as db:
            self.assertEqual([row[0] for row in db.execute(
                'SELECT site_id FROM user_sites WHERE user_id=2 ORDER BY site_id').fetchall()], [2])
            db.execute("""CREATE TRIGGER fail_user_site_insert AFTER INSERT ON user_sites
                WHEN NEW.site_id=1 BEGIN SELECT RAISE(ABORT, 'site failure'); END""")
        failed = self.client.put('/api/users/2/sites', headers=self.headers(),
                                 json={'site_ids': [1]})
        self.assertEqual(failed.status_code, 503, failed.json)
        with app_module.get_db() as db:
            self.assertEqual([row[0] for row in db.execute(
                'SELECT site_id FROM user_sites WHERE user_id=2 ORDER BY site_id').fetchall()], [2])

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
        self.assertEqual(closed.json['cancelled_items'], 2)
        with app_module.get_db() as db:
            items = db.execute('SELECT id,result,execution_status FROM insp_plan_items ORDER BY id').fetchall()
            self.assertEqual(items[0]['execution_status'], 'cancelled')
            self.assertEqual(items[1]['result'], '已完成')
            self.assertEqual(items[1]['execution_status'], 'active')
            self.assertEqual(items[2]['execution_status'], 'cancelled')
            self.assertEqual(db.execute('SELECT status FROM insp_plans WHERE id=10').fetchone()[0], 'cancelled')

    def test_parent_schedule_change_blocks_remind_and_close_without_side_effects(self):
        with app_module.get_db() as db:
            db.execute("UPDATE plan_schedules SET status='change_submitted' WHERE id=20")
            before = {
                'notifications': db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],
                'events': db.execute('SELECT COUNT(*) FROM timeline_events').fetchone()[0],
            }
        reminded = self.client.post('/api/insp-plans/10/overdue-action', headers=self.headers(),
                                    json={'action': 'remind'})
        closed = self.client.post('/api/insp-plans/10/overdue-action', headers=self.headers(),
                                  json={'action': 'close', 'reason': '不应写入'})
        self.assertEqual((reminded.status_code, closed.status_code), (409, 409))
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT status FROM insp_plans WHERE id=10').fetchone()[0], 'active')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM insp_plan_items WHERE execution_status='cancelled'").fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], before['notifications'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM timeline_events').fetchone()[0], before['events'])

    def test_stale_plan_notification_is_removed_from_unread_count(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,payload_json)
                VALUES (1,'plan_schedule',20,'计划状态更新','明确审核通知',
                        '{"notification_target":"review","review_type":"plan_schedule"}')""")
        count = self.client.get('/api/notifications/unread-count', headers=self.headers())
        self.assertEqual(count.status_code, 200, count.json)
        self.assertEqual(count.json['count'], 0)
        response = self.client.get('/api/notifications', headers=self.headers())
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['unread_count'], 0)
        notices = response.json['notifications']
        self.assertEqual(len(notices), 2)
        for notice in notices:
            self.assertTrue(notice['is_stale'])
            self.assertEqual(notice['current_status'], 'approved')
            self.assertEqual(notice['is_read'], 1)

    def test_notification_status_filter_separates_current_and_history(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read)
                VALUES (1,'alert',1,'当前消息','待处理',0)""")
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read)
                VALUES (1,'alert',2,'历史消息','已查看',1)""")
        current = self.client.get('/api/notifications?status=unread', headers=self.headers())
        history = self.client.get('/api/notifications?status=read', headers=self.headers())
        self.assertEqual(current.status_code, 200, current.json)
        self.assertEqual(history.status_code, 200, history.json)
        self.assertEqual([row['title'] for row in current.json['notifications']], ['当前消息'])
        self.assertIn('历史消息', [row['title'] for row in history.json['notifications']])

    def test_notification_pagination_retires_stale_rows_before_selecting_page(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read,created_at)
                VALUES (1,'alert',1,'后续消息一','仍待处理',0,'2026-08-10 10:00:00')""")
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read,created_at)
                VALUES (1,'alert',2,'后续消息二','仍待处理',0,'2026-08-10 10:00:00')""")
            # This newest row is already obsolete because schedule #20 is approved.
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read,created_at)
                VALUES (1,'plan_schedule',20,'有新的巡检计划待审批','已不需要处理',0,'2026-08-10 10:00:00')""")

        first = self.client.get('/api/notifications?status=unread&page=1&limit=1', headers=self.headers())
        second = self.client.get('/api/notifications?status=unread&page=2&limit=1', headers=self.headers())
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(first.json['total'], 2)
        self.assertTrue(first.json['has_more'])
        self.assertEqual(first.json['page'], 1)
        self.assertEqual(first.json['limit'], 1)
        self.assertEqual(first.json['notifications'][0]['title'], '后续消息二')
        self.assertEqual(second.json['notifications'][0]['title'], '后续消息一')
        self.assertNotEqual(first.json['notifications'][0]['id'], second.json['notifications'][0]['id'])

        invalid = self.client.get('/api/notifications?page=0', headers=self.headers())
        capped = self.client.get('/api/notifications?limit=999', headers=self.headers())
        self.assertEqual(invalid.status_code, 400, invalid.json)
        self.assertEqual(capped.status_code, 200, capped.json)
        self.assertEqual(capped.json['limit'], 100)

    def test_legacy_photo_batch_is_archived_and_never_reappears(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO operation_attachments (id,site_id) VALUES (10,1)")
            db.execute("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read)
                VALUES (1,'attachment_review_batch',1,'旧影像待审','',0)""")
            app_module._notify_attachment_reviewers(db, 1, 10, '测试站影像')

        for _ in range(3):
            response = self.client.get('/api/notifications?status=unread', headers=self.headers())
            self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            batches = db.execute("""SELECT is_read FROM notifications
                WHERE source_type='attachment_review_batch'""").fetchall()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]['is_read'], 1)

        with app_module.get_db() as db:
            db.execute("INSERT INTO operation_attachments (id,site_id) VALUES (11,1)")
        first = self.client.get('/api/notifications?status=unread', headers=self.headers())
        second = self.client.get('/api/notifications/unread-count', headers=self.headers())
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        with app_module.get_db() as db:
            batches = db.execute("""SELECT is_read FROM notifications
                WHERE source_type='attachment_review_batch' ORDER BY id""").fetchall()
        self.assertEqual(len(batches), 1)
        self.assertEqual([row['is_read'] for row in batches], [1])

    def test_unread_dedupe_migration_archives_older_duplicates_and_enforces_uniqueness(self):
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO notifications
                (user_id,source_type,source_id,title,content,is_read,dedupe_key,payload_json)
                VALUES (1,'attachment_review_batch',1,'Batch','',0,'batch:1','{}')""", [(), ()])
            self.assertTrue(app_module._ensure_notification_unread_dedupe_invariant(db))
            rows = db.execute("""SELECT id,is_read FROM notifications
                WHERE dedupe_key='batch:1' ORDER BY id""").fetchall()
            self.assertEqual([row['is_read'] for row in rows], [1, 0])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""INSERT INTO notifications
                    (user_id,source_type,source_id,title,content,is_read,dedupe_key,payload_json)
                    VALUES (1,'attachment_review_batch',1,'Duplicate','',0,'batch:1','{}')""")

    def test_repeated_deduped_creation_reuses_the_current_notification(self):
        with app_module.get_db() as db:
            app_module._ensure_notification_unread_dedupe_invariant(db)
            first_id = app_module._upsert_unread_notification(
                db, 1, 'attachment_review_batch', 1, 'Batch', 'First', 'batch:repeat', '{}')
            second_id = app_module._upsert_unread_notification(
                db, 1, 'attachment_review_batch', 1, 'Batch', 'Second', 'batch:repeat', '{}')
            rows = db.execute("""SELECT id,content FROM notifications
                WHERE user_id=1 AND dedupe_key='batch:repeat' AND is_read=0""").fetchall()
        self.assertEqual(first_id, second_id)
        self.assertEqual([(row['id'], row['content']) for row in rows], [(first_id, 'Second')])


if __name__ == '__main__':
    unittest.main()
