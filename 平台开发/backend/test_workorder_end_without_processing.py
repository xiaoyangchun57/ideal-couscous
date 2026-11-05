import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class WorkorderEndWithoutProcessingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, 'test.db')
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_testing = app_module.app.testing
        self._create_schema()
        db_path = self.db_path

        @contextmanager
        def temporary_db():
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            try:
                yield db
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module.app.config.update(TESTING=True)
        app_module._tokens.clear()
        app_module._tokens.update({
            'operator-token': self._user(1),
            'other-token': self._user(2),
            'reviewer-token': self._user(3),
            'remote-reviewer-token': self._user(4),
            'admin-token': self._user(5),
        })
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module.app.testing = self.original_testing
        self.tmp.cleanup()

    def _create_schema(self):
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, real_name TEXT,
                    role TEXT, status TEXT
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, site_id INTEGER,
                    source TEXT, title TEXT, description TEXT, assignee TEXT,
                    status TEXT, related_alert_id INTEGER, conclusion TEXT,
                    remark TEXT, resolved_at TEXT, check_in_time TEXT,
                    review_submitter_id INTEGER, created_at TEXT, creator TEXT
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, metric TEXT,
                    status TEXT, related_order_no TEXT, resolved_at TEXT,
                    resolve_reason TEXT
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT,
                    source_id INTEGER, event_type TEXT, operator TEXT,
                    remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, work_order_no TEXT, status TEXT,
                    reject_reason TEXT
                );
                CREATE TABLE parts_requests (
                    id INTEGER PRIMARY KEY, work_order_no TEXT, status TEXT,
                    approver_id INTEGER, approve_comment TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER,
                    storage_path TEXT
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    source_type TEXT, source_id INTEGER, title TEXT, content TEXT,
                    is_read INTEGER DEFAULT 0, dedupe_key TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE hotline_events (
                    id INTEGER PRIMARY KEY, related_order_no TEXT, status TEXT
                );
                INSERT INTO sites VALUES (10, 'A站'), (20, 'B站');
                INSERT INTO users VALUES
                    (1, 'operator1', '负责人', 'operator', 'active'),
                    (2, 'operator2', '无关人员', 'operator', 'active'),
                    (3, 'reviewer1', '本站审核员', 'reviewer', 'active'),
                    (4, 'reviewer2', '异站审核员', 'reviewer', 'active'),
                    (5, 'admin1', '管理员', 'admin', 'active');
                INSERT INTO user_roles VALUES
                    (1, 'operator'), (2, 'operator'), (3, 'reviewer'),
                    (4, 'reviewer'), (5, 'admin');
                INSERT INTO user_sites VALUES
                    (1, 10), (2, 10), (3, 10);
            """)

    def _user(self, user_id):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            row = dict(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone())
            row['roles'] = [role[0] for role in db.execute(
                'SELECT role FROM user_roles WHERE user_id=?', (user_id,)).fetchall()]
            return row

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def add_order(self, status='pending', source='manual', order_no='WO-1',
                  related_alert_id=None, site_id=10):
        with sqlite3.connect(self.db_path) as db:
            db.execute("""INSERT INTO work_orders
                (id,order_no,site_id,source,title,description,assignee,status,
                 related_alert_id,remark,check_in_time,review_submitter_id,
                 created_at,creator)
                VALUES (1,?,?,?,?,?,?,?,?,'原处置备注','2026-10-30 09:00:00',1,
                        '2026-10-30 08:00:00','创建人')""",
                (order_no, site_id, source, '现场工单', '原始描述', '负责人',
                 status, related_alert_id))
            db.execute("""INSERT INTO operation_attachments
                VALUES (1,'workorder',1,'evidence/original.jpg')""")

    def end(self, token='operator-token', reason='现场确认无需继续处理'):
        return self.client.post(
            '/api/workorders/WO-1/end-without-processing',
            headers=self.headers(token), json={'reason': reason})

    def test_operator_can_end_each_active_state_without_overwriting_evidence(self):
        for status in ('pending', 'accepted', 'dispatched', 'in_progress'):
            with self.subTest(status=status):
                with sqlite3.connect(self.db_path) as db:
                    db.execute('DELETE FROM work_orders')
                    db.execute('DELETE FROM timeline_events')
                    db.execute('DELETE FROM operation_attachments')
                self.add_order(status=status)
                response = self.end()
                self.assertEqual(response.status_code, 200, response.json)
                with sqlite3.connect(self.db_path) as db:
                    row = db.execute("""SELECT status,conclusion,remark,check_in_time
                        FROM work_orders WHERE order_no='WO-1'""").fetchone()
                    self.assertEqual(row, (
                        'closed', 'no_action_required', '原处置备注',
                        '2026-10-30 09:00:00'))
                    self.assertEqual(db.execute(
                        'SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 1)

    def test_permissions_are_rechecked_for_operator_and_reviewer_states(self):
        self.add_order(status='in_progress')
        self.assertEqual(self.end('other-token').status_code, 403)
        with sqlite3.connect(self.db_path) as db:
            db.execute('DELETE FROM user_sites WHERE user_id=1 AND site_id=10')
        self.assertEqual(self.end().status_code, 403)
        with sqlite3.connect(self.db_path) as db:
            db.execute('INSERT INTO user_sites VALUES (1,10)')
            db.execute("UPDATE work_orders SET status='reviewing'")
        self.assertEqual(self.end().status_code, 403)
        self.assertEqual(self.end('remote-reviewer-token').status_code, 200)

    def test_admin_active_state_keeps_existing_scope_and_receives_exact_reason(self):
        self.add_order(status='pending')
        detail = self.client.get(
            '/api/workorders/WO-1', headers=self.headers('admin-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertFalse(detail.json['can_end_without_processing'])
        self.assertEqual(
            detail.json['end_without_processing_block_reason'],
            '当前阶段只能由已指派且仍有该站点权限的运维负责人提前结束',
        )
        denied = self.end('admin-token')
        self.assertEqual(
            (denied.status_code, denied.json['code'], denied.json['error']),
            (403, 'WORKORDER_NO_ACTION_FORBIDDEN',
             '当前阶段只能由已指派且仍有该站点权限的运维负责人提前结束'),
        )

    def test_exact_detail_reads_order_outside_recent_fifty(self):
        self.add_order(status='pending')
        with sqlite3.connect(self.db_path) as db:
            for index in range(2, 57):
                db.execute(
                    """INSERT INTO work_orders
                       (id,order_no,site_id,source,title,description,assignee,status,
                        created_at,creator)
                       VALUES (?,?,10,'manual','较新工单','','负责人','pending',?,
                               '创建人')""",
                    (index, f'WO-NEW-{index}', f'2026-11-02 10:00:{index:02d}'),
                )
        recent = self.client.get(
            '/api/workorders', headers=self.headers('operator-token'))
        self.assertEqual(recent.status_code, 200, recent.json)
        self.assertEqual(len(recent.json), 50)
        self.assertNotIn('WO-1', {row['order_no'] for row in recent.json})
        detail = self.client.get(
            '/api/workorders/WO-1', headers=self.headers('operator-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['order_no'], 'WO-1')

    def test_reason_validation_and_normal_close_conflict_do_not_write(self):
        self.add_order(status='pending')
        self.assertEqual(self.end(reason='   ').status_code, 400)
        self.assertEqual(self.end(reason='x' * 501).status_code, 400)
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE work_orders SET status='closed',conclusion='repaired'")
        response = self.end()
        self.assertEqual((response.status_code, response.json['code']),
                         (409, 'WORKORDER_NO_ACTION_STATE_CONFLICT'))
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM timeline_events').fetchone()[0], 0)

    def test_linked_alert_pending_resources_and_current_notices_close_once(self):
        self.add_order(status='accepted', source='auto', related_alert_id=9)
        with sqlite3.connect(self.db_path) as db:
            db.executescript("""
                INSERT INTO alerts VALUES (9,10,'device_status','pending','WO-1',NULL,NULL);
                INSERT INTO vehicle_applications VALUES (1,'WO-1','pending','');
                INSERT INTO vehicle_applications VALUES (2,'WO-1','approved','');
                INSERT INTO parts_requests VALUES (1,'WO-1','pending',NULL,'');
                INSERT INTO parts_requests VALUES (2,'WO-1','approved',NULL,'');
                INSERT INTO notifications
                    (user_id,source_type,source_id,title,content,is_read)
                    VALUES (3,'workorder_review','WO-1','待核验','当前待办',0);
                INSERT INTO hotline_events VALUES (1,'WO-1','pending');
            """)
        first = self.end(reason='误建工单，无需现场维修')
        second = self.end(reason='重复提交不应新增记录')
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertTrue(second.json['already_ended'])
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute(
                'SELECT status,resolve_reason FROM alerts WHERE id=9').fetchone(),
                ('resolved', 'no_action_required'))
            self.assertEqual(db.execute(
                'SELECT status FROM vehicle_applications ORDER BY id').fetchall(),
                [('cancelled',), ('approved',)])
            self.assertEqual(db.execute(
                'SELECT status FROM parts_requests ORDER BY id').fetchall(),
                [('rejected',), ('approved',)])
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM timeline_events WHERE source_type='workorder' "
                "AND event_type='ended_without_processing'").fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT is_read FROM notifications WHERE source_type='workorder_review'"
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT status FROM hotline_events').fetchone()[0], 'closed')

    def test_link_conflict_and_transaction_failure_leave_everything_unchanged(self):
        self.add_order(status='pending', source='auto', related_alert_id=9)
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO alerts VALUES (9,20,'device_status','pending','WO-1',NULL,NULL)")
        conflict = self.end()
        self.assertEqual((conflict.status_code, conflict.json['code']),
                         (409, 'WORKORDER_ALERT_LINK_CONFLICT'))
        with sqlite3.connect(self.db_path) as db:
            db.execute('UPDATE alerts SET site_id=10')
        with mock.patch.object(
                app_module, '_end_workorder_pending_resources',
                side_effect=sqlite3.DatabaseError('forced failure')):
            failed = self.end()
        self.assertEqual((failed.status_code, failed.json['code']),
                         (503, 'WORKORDER_NO_ACTION_ROLLED_BACK'))
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute(
                "SELECT status,conclusion FROM work_orders WHERE order_no='WO-1'"
            ).fetchone(), ('pending', None))
            self.assertEqual(db.execute(
                'SELECT status FROM alerts WHERE id=9').fetchone()[0], 'pending')
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM timeline_events').fetchone()[0], 0)

    def test_projection_distinguishes_no_action_close_from_normal_completion(self):
        self.add_order(status='pending')
        self.assertEqual(self.end().status_code, 200)
        detail = self.client.get('/api/workorders/WO-1', headers=self.headers('operator-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['closure_type'], 'no_action_required')
        self.assertEqual(detail.json['closure_reason'], '现场确认无需继续处理')
        event_types = [item['type'] for item in detail.json['flow_events']]
        self.assertIn('ended_without_processing', event_types)
        self.assertNotIn('closed', event_types)

    def test_list_batches_timeline_closure_alert_and_shared_checkin_reads(self):
        self.add_order(status='pending', source='auto', related_alert_id=9)
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO alerts VALUES (9,10,'device_status','pending','WO-1',NULL,NULL)")
            db.execute("""INSERT INTO timeline_events
                (source_type,source_id,event_type,operator,remark,created_at)
                VALUES ('workorder',1,'ended_without_processing','负责人','无需继续处理','2026-11-02 09:00:00')""")
            for index in range(2, 26):
                db.execute("""INSERT INTO work_orders
                    (id,order_no,site_id,source,title,description,assignee,status,
                     created_at,creator)
                    VALUES (?,?,10,'manual','批量工单','','负责人','pending',?,'创建人')""",
                    (index, f'WO-BATCH-{index}', f'2026-11-02 10:{index:02d}:00'))

        statements = []
        db_path = self.db_path

        @contextmanager
        def traced_db():
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            db.set_trace_callback(statements.append)
            try:
                yield db
            finally:
                db.close()

        original_get_db = app_module.get_db
        app_module.get_db = traced_db
        try:
            response = self.client.get('/api/workorders?limit=50', headers=self.headers('operator-token'))
        finally:
            app_module.get_db = original_get_db

        self.assertEqual(response.status_code, 200, response.json)
        by_order = {row['order_no']: row for row in response.json}
        self.assertEqual(by_order['WO-1']['closure_type'], 'no_action_required')
        self.assertIn('ended_without_processing', {
            event['type'] for event in by_order['WO-1']['flow_events']})
        timeline_selects = [sql for sql in statements
                            if sql.lstrip().upper().startswith('SELECT')
                            and 'FROM timeline_events' in sql]
        alert_selects = [sql for sql in statements
                         if sql.lstrip().upper().startswith('SELECT')
                         and 'FROM alerts WHERE id IN' in sql]
        self.assertEqual(len(timeline_selects), 1)
        self.assertEqual(len(alert_selects), 1)


if __name__ == '__main__':
    unittest.main()
