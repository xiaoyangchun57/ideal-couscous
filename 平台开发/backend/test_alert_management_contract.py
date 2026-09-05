import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class AlertManagementContractTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
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
            'admin-token': {'id': 1, 'role': 'admin', 'roles': ['admin'], 'real_name': '管理员'},
            'reviewer-token': {'id': 2, 'role': 'reviewer', 'roles': ['reviewer'], 'real_name': '审核员'},
            'operator-token': {'id': 3, 'role': 'operator', 'roles': ['operator'], 'real_name': '运维员'},
        })

        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, role TEXT, real_name TEXT,
                    status TEXT DEFAULT 'active'
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, code TEXT);
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, metric TEXT,
                    value REAL, level TEXT, message TEXT, status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT,
                    resolve_reason TEXT, related_order_no TEXT, review_id INTEGER,
                    flow_type TEXT, flow_status TEXT, urge_count INTEGER DEFAULT 0,
                    last_urged_at TEXT, response_deadline TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE, site_id INTEGER,
                    source TEXT, event_type TEXT, level TEXT, title TEXT, description TEXT,
                    assignee TEXT, status TEXT, sla_deadline TEXT
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?,?)', [
                (1, 'admin', 'admin', '管理员', 'active'),
                (2, 'reviewer', 'reviewer', '审核员', 'active'),
                (3, 'operator', 'operator', '运维员', 'active'),
            ])
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [
                (1, 'admin'), (2, 'reviewer'), (3, 'operator'),
            ])
            db.executemany('INSERT INTO sites VALUES (?,?,?)', [(1, '站点一', 'S1'), (2, '站点二', 'S2')])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(2, 1), (3, 1)])
            db.executemany(
                "INSERT INTO alerts (id,site_id,metric,value,level,message,status,flow_type,flow_status) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (1, 1, 'ph', 9.2, 'yellow', '站点一 pH 偏高', 'pending', 'manual', 'pending_review'),
                    (2, 2, 'cod', 45, 'orange', '站点二 COD 偏高', 'pending', 'manual', 'pending_review'),
                    (3, 1, 'ph', 7.1, 'blue', '已办结记录', 'resolved', 'manual', 'dismissed'),
                ],
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
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def test_alert_writes_are_admin_only(self):
        calls = [
            ('post', '/api/alerts/1/acknowledge', {}),
            ('post', '/api/alerts/1/resolve', {'reason': 'manual_review'}),
            ('post', '/api/alerts/1/ack-resolve', {'remark': '确认无异常'}),
            ('post', '/api/alerts/1/urge', {'opinion': '尽快处理'}),
            ('post', '/api/alerts/1/undo-acknowledge', {'remark': '误受理'}),
            ('post', '/api/alerts/1/confirm-convert', {}),
            ('post', '/api/alerts/batch', {'ids': [1], 'action': 'convert'}),
            ('post', '/api/alerts/simulate', {'site_id': 1, 'metric': 'ph', 'value': 9.5, 'level': 'yellow'}),
        ]
        for token in ('reviewer-token', 'operator-token'):
            for method, path, payload in calls:
                response = getattr(self.client, method)(path, headers=self.headers(token), json=payload)
                self.assertEqual(response.status_code, 403, (token, path, response.json))

    def test_single_transition_validates_state_and_records_real_operator(self):
        accepted = self.client.post('/api/alerts/1/acknowledge', headers=self.headers('admin-token'), json={'operator': '伪造人员'})
        self.assertEqual(accepted.status_code, 200, accepted.json)
        repeated = self.client.post('/api/alerts/1/acknowledge', headers=self.headers('admin-token'))
        self.assertEqual(repeated.status_code, 409, repeated.json)
        missing_reason = self.client.post('/api/alerts/1/resolve', headers=self.headers('admin-token'), json={})
        self.assertEqual(missing_reason.status_code, 400, missing_reason.json)
        resolved = self.client.post('/api/alerts/1/resolve', headers=self.headers('admin-token'), json={
            'reason': 'manual_review', 'remark': '已核对现场数据', 'operator': '伪造人员',
        })
        self.assertEqual(resolved.status_code, 200, resolved.json)
        with self.temporary_db() as db:
            row = db.execute('SELECT status,resolve_reason FROM alerts WHERE id=1').fetchone()
            self.assertEqual(row['status'], 'resolved')
            self.assertIn('人工复核', row['resolve_reason'])
            operators = {item['operator'] for item in db.execute(
                "SELECT operator FROM timeline_events WHERE source_type='alert' AND source_id=1"
            ).fetchall()}
            self.assertEqual(operators, {'管理员'})

    def test_batch_convert_uses_supported_action_and_reports_skips(self):
        response = self.client.post('/api/alerts/batch', headers=self.headers('admin-token'), json={
            'ids': [1, 3], 'action': 'convert',
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['count'], 1)
        self.assertEqual(response.json['skipped'], 1)
        with self.temporary_db() as db:
            order = db.execute('SELECT assignee,status FROM work_orders').fetchone()
            self.assertEqual(order['assignee'], '运维员')
            self.assertEqual(order['status'], 'in_progress')
            alert = db.execute('SELECT related_order_no,flow_status FROM alerts WHERE id=1').fetchone()
            self.assertTrue(alert['related_order_no'])
            self.assertEqual(alert['flow_status'], 'converted')

    def test_simulation_is_committed_and_uses_current_timeline_schema(self):
        response = self.client.post('/api/alerts/simulate', headers=self.headers('admin-token'), json={
            'site_id': 1,
            'metric': 'ph',
            'value': 9.5,
            'level': 'yellow',
            'message': '[模拟] 站点一 pH 9.5，触发黄色警示',
        })
        self.assertEqual(response.status_code, 200, response.json)
        alert_id = response.json['id']
        with self.temporary_db() as db:
            alert = db.execute('SELECT message,flow_type,flow_status FROM alerts WHERE id=?', (alert_id,)).fetchone()
            self.assertTrue(alert['message'].startswith('[模拟]'))
            self.assertNotIn('[模拟] [模拟]', alert['message'])
            self.assertEqual(alert['flow_type'], 'simulation')
            self.assertEqual(alert['flow_status'], 'test')
            event = db.execute('SELECT event_type,operator FROM timeline_events WHERE source_id=?', (alert_id,)).fetchone()
            self.assertEqual(event['event_type'], 'simulated')
            self.assertEqual(event['operator'], '管理员')

    def test_statistics_respect_reviewer_site_scope(self):
        response = self.client.get('/api/alerts/statistics', headers=self.headers('reviewer-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['total'], 2)
        self.assertEqual(response.json['by_status']['pending'], 1)
        self.assertEqual(response.json['by_status']['resolved'], 1)
        self.assertEqual(response.json['by_level']['orange'], 0)

    def test_alert_action_projection_covers_workorder_stages_and_invalid_links(self):
        alert = {
            'id': 10, 'site_id': 1, 'status': 'pending',
            'flow_type': 'auto', 'flow_status': 'converted',
            'related_order_no': 'WO-10',
        }
        phases = {
            'pending': 'workorder_pending',
            'accepted': 'workorder_in_progress',
            'dispatched': 'workorder_in_progress',
            'in_progress': 'workorder_in_progress',
            'reviewing': 'workorder_reviewing',
        }
        for status, phase in phases.items():
            with self.subTest(status=status):
                projected = app_module._project_alert_workorder_action(alert, {
                    'order_no': 'WO-10', 'site_id': 1, 'status': status,
                    'assignee': '运维员', 'related_alert_id': 10,
                })
                self.assertEqual(projected['phase'], phase)
                self.assertEqual(projected['primary_action'], '查看关联工单')
                self.assertEqual(projected['related_workorder_target'], {'order_no': 'WO-10'})
                self.assertEqual(projected['workorder_status'], status)

        terminal = dict(alert, status='resolved')
        self.assertIsNone(app_module._project_alert_workorder_action(terminal)['primary_action'])
        for status in ('resolved', 'closed'):
            projected = app_module._project_alert_workorder_action(alert, {
                'order_no': 'WO-10', 'site_id': 1, 'status': status,
            })
            self.assertEqual(projected['phase'], 'closed')
            self.assertIsNone(projected['primary_action'])
            self.assertIsNone(projected['related_workorder_target'])

        management = app_module._project_alert_workorder_action({
            'id': 11, 'site_id': 1, 'status': 'pending',
            'flow_type': 'manual', 'flow_status': 'pending_review',
        })
        self.assertEqual(management['phase'], 'management_review')
        self.assertIsNone(management['primary_action'])
        self.assertEqual(management['block_reason'], '待管理研判，等待管理员处理')

        unlinked = app_module._project_alert_workorder_action({
            'id': 12, 'site_id': 1, 'status': 'pending',
            'flow_type': 'auto', 'flow_status': 'detected',
        })
        self.assertEqual(unlinked['phase'], 'link_unavailable')
        self.assertEqual(unlinked['block_reason'], '关联工单尚未生成，请刷新后重试')

        invalid_orders = [
            None,
            {'order_no': 'WO-OTHER', 'site_id': 1, 'status': 'pending'},
            {'order_no': 'WO-10', 'site_id': 2, 'status': 'pending'},
            {'order_no': 'WO-10', 'site_id': 1, 'status': 'unknown'},
            {'order_no': 'WO-10', 'site_id': 1, 'status': 'pending', 'related_alert_id': 99},
        ]
        for order in invalid_orders:
            with self.subTest(order=order):
                projected = app_module._project_alert_workorder_action(alert, order)
                self.assertEqual(projected['phase'], 'link_unavailable')
                self.assertIsNone(projected['primary_action'])
                self.assertIsNone(projected['related_workorder_target'])
                self.assertEqual(projected['block_reason'], '关联工单信息异常，请刷新后重试')

        forbidden = app_module._project_alert_workorder_action(alert, {
            'order_no': 'WO-10', 'site_id': 1, 'status': 'in_progress',
        }, can_view=False)
        self.assertFalse(forbidden['can_view'])
        self.assertIsNone(forbidden['primary_action'])
        self.assertEqual(forbidden['block_reason'], '当前角色无权查看关联工单')

    def test_alert_display_projection_keeps_manual_placeholder_and_monitoring_zero_distinct(self):
        manual = app_module._project_alert_display({
            'id': 60, 'site_id': 1, 'metric': 'manual_report', 'value': 0,
            'status': 'pending', 'flow_type': 'manual', 'flow_status': 'pending_review',
            'message': '不应作为优先说明',
        }, {
            'event_type': 'equipment', 'description': '水泵异响，请现场核查',
        })
        self.assertEqual(manual['display_kind'], 'manual_report')
        self.assertEqual(manual['display_title'], '设备异常')
        self.assertEqual(manual['display_summary'], '水泵异响，请现场核查')
        self.assertFalse(manual['has_monitoring_value'])
        self.assertIsNone(manual['monitoring_value'])
        self.assertIn('等待管理员', manual['disposition_detail'])

        monitoring = app_module._project_alert_display({
            'id': 61, 'site_id': 1, 'metric': 'ph', 'value': 0, 'unit': 'pH',
            'threshold': 7, 'status': 'pending', 'flow_type': 'manual',
            'flow_status': 'pending_review',
        })
        self.assertEqual(monitoring['display_kind'], 'monitoring')
        self.assertTrue(monitoring['has_monitoring_value'])
        self.assertEqual((monitoring['monitoring_value'], monitoring['monitoring_unit'],
                          monitoring['monitoring_threshold']), (0, 'pH', 7))

        active = app_module._project_alert_display({
            'id': 62, 'site_id': 1, 'metric': 'ph', 'value': 8.2,
            'status': 'pending', 'flow_type': 'auto', 'flow_status': 'converted',
            'related_order_no': 'WO-ACTIVE',
        }, {
            'order_no': 'WO-ACTIVE', 'site_id': 1, 'status': 'in_progress',
            'related_alert_id': 62,
        })
        self.assertEqual((active['disposition_label'], active['disposition_detail']),
                         ('工单处理中', ''))

        expected = {
            'management_review': ('待管理研判', 'pending'),
            'workorder_pending': ('工单待受理', 'pending'),
            'workorder_in_progress': ('工单处理中', 'active'),
            'workorder_reviewing': ('工单待核验', 'pending'),
            'closed': ('已闭环', 'completed'),
            'link_unavailable': ('关联信息异常', 'error'),
            'unavailable': ('信息异常', 'error'),
        }
        self.assertEqual(app_module._ALERT_DISPOSITION, expected)

    def test_closed_alert_disposition_omits_repeated_detail(self):
        alert = {
            'id': 63, 'site_id': 1, 'metric': 'ph', 'value': 8.2,
            'status': 'pending', 'flow_type': 'auto', 'flow_status': 'converted',
        }
        closed_cases = [
            ('alert_resolved', dict(alert, status='resolved'), None),
            ('flow_dismissed', dict(alert, flow_status='dismissed'), None),
            ('workorder_resolved', dict(alert, related_order_no='WO-CLOSED'), {
                'order_no': 'WO-CLOSED', 'site_id': 1, 'status': 'resolved',
                'assignee': '运维员', 'related_alert_id': 63,
            }),
            ('workorder_closed', dict(alert, related_order_no='WO-CLOSED'), {
                'order_no': 'WO-CLOSED', 'site_id': 1, 'status': 'closed',
                'assignee': '运维员', 'related_alert_id': 63,
            }),
        ]
        for name, current_alert, linked_order in closed_cases:
            with self.subTest(name=name):
                projected = app_module._project_alert_display(current_alert, linked_order)
                self.assertEqual(
                    (projected['phase'], projected['disposition_label'],
                     projected['disposition_tone'], projected['disposition_detail']),
                    ('closed', '已闭环', 'completed', ''),
                )
                self.assertEqual(projected['block_reason'], '')
                self.assertIsNone(projected['primary_action'])
                self.assertIsNone(projected['related_workorder_target'])
                if linked_order:
                    self.assertEqual(projected['workorder_status'], linked_order['status'])
                    self.assertEqual(projected['workorder_status_cn'],
                                     app_module._WORKORDER_STATUS_LABELS[linked_order['status']])
                    self.assertEqual(projected['workorder_handler_name'], '运维员')

    def test_alert_list_and_detail_share_display_projection_without_read_writes(self):
        with self.temporary_db() as db:
            db.execute("""INSERT INTO work_orders
                (order_no,site_id,source,event_type,level,title,description,assignee,status)
                VALUES ('WO-MANUAL',1,'manual_report','equipment','normal','人工上报','现场水泵异响','运维员','pending')""")
            db.executemany("""INSERT INTO alerts
                (id,site_id,metric,value,level,message,status,flow_type,flow_status,related_order_no)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", [
                    (70, 1, 'manual_report', 0, 'yellow', '兼容占位', 'pending', 'manual', 'converted', 'WO-MANUAL'),
                    (71, 1, 'ph', 0, 'yellow', '真实零值', 'pending', 'manual', 'pending_review', None),
                ])
            before = {
                'alerts': [tuple(row) for row in db.execute(
                    'SELECT id,status,flow_status,related_order_no,resolved_at FROM alerts ORDER BY id')],
                'timeline': [tuple(row) for row in db.execute(
                    'SELECT id,source_type,source_id,event_type,operator,remark FROM timeline_events ORDER BY id')],
            }

        listed = self.client.get('/api/alerts', headers=self.headers('operator-token'))
        self.assertEqual(listed.status_code, 200, listed.json)
        list_rows = {row['id']: row for row in listed.json}
        exact_manual = self.client.get('/api/alerts/70', headers=self.headers('operator-token'))
        exact_monitoring = self.client.get('/api/alerts/71', headers=self.headers('operator-token'))
        exact_closed = self.client.get('/api/alerts/3', headers=self.headers('operator-token'))
        self.assertEqual((exact_manual.status_code, exact_monitoring.status_code,
                          exact_closed.status_code), (200, 200, 200))
        projection_keys = ('display_kind', 'display_title', 'display_summary', 'has_monitoring_value',
                           'monitoring_value', 'monitoring_unit', 'monitoring_threshold',
                           'disposition_label', 'disposition_tone', 'disposition_detail')
        for key in projection_keys:
            self.assertEqual(list_rows[70][key], exact_manual.json[key], key)
            self.assertEqual(list_rows[71][key], exact_monitoring.json[key], key)
            self.assertEqual(list_rows[3][key], exact_closed.json[key], key)
        self.assertFalse(exact_manual.json['has_monitoring_value'])
        self.assertTrue(exact_monitoring.json['has_monitoring_value'])
        self.assertEqual(exact_monitoring.json['monitoring_value'], 0)
        self.assertEqual((exact_closed.json['phase'], exact_closed.json['disposition_label'],
                          exact_closed.json['disposition_tone'], exact_closed.json['disposition_detail']),
                         ('closed', '已闭环', 'completed', ''))
        with self.temporary_db() as db:
            self.assertEqual({
                'alerts': [tuple(row) for row in db.execute(
                    'SELECT id,status,flow_status,related_order_no,resolved_at FROM alerts ORDER BY id')],
                'timeline': [tuple(row) for row in db.execute(
                    'SELECT id,source_type,source_id,event_type,operator,remark FROM timeline_events ORDER BY id')],
            }, before)

    def test_alert_list_projects_only_authorized_exact_workorder_targets(self):
        with self.temporary_db() as db:
            db.execute(
                """INSERT INTO work_orders
                   (order_no,site_id,source,event_type,level,title,description,assignee,status)
                   VALUES ('WO-LINKED',1,'auto','设备异常','normal','设备异常','','运维员','dispatched')"""
            )
            db.executemany(
                """INSERT INTO alerts
                   (id,site_id,metric,value,level,message,status,flow_type,flow_status,related_order_no)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (4, 1, 'ph', 9.1, 'yellow', '已有工单', 'pending', 'auto', 'converted', 'WO-LINKED'),
                    (5, 1, 'ph', 9.0, 'yellow', '失效关联', 'pending', 'auto', 'converted', 'WO-MISSING'),
                ],
            )

        admin = self.client.get('/api/alerts', headers=self.headers('admin-token'))
        self.assertEqual(admin.status_code, 200, admin.json)
        rows = {row['id']: row for row in admin.json}
        self.assertEqual(rows[1]['phase'], 'management_review')
        self.assertIsNone(rows[1]['primary_action'])
        self.assertEqual(rows[4]['phase'], 'workorder_in_progress')
        self.assertEqual(rows[4]['related_workorder_target'], {'order_no': 'WO-LINKED'})
        self.assertEqual(rows[4]['workorder_status_cn'], '已派发')
        self.assertEqual(rows[4]['workorder_handler_name'], '运维员')
        self.assertEqual(rows[5]['phase'], 'link_unavailable')
        self.assertIsNone(rows[5]['primary_action'])

        reviewer = self.client.get('/api/alerts', headers=self.headers('reviewer-token'))
        reviewer_rows = {row['id']: row for row in reviewer.json}
        self.assertFalse(reviewer_rows[4]['can_view'])
        self.assertIsNone(reviewer_rows[4]['primary_action'])
        self.assertIsNone(reviewer_rows[4]['related_workorder_target'])

        with self.temporary_db() as db:
            states = dict(db.execute('SELECT id,status FROM alerts WHERE id IN (1,4,5)').fetchall())
        self.assertEqual(states, {1: 'pending', 4: 'pending', 5: 'pending'})

    def test_alert_exact_detail_is_not_bounded_by_list_and_preserves_projection(self):
        with self.temporary_db() as db:
            db.execute("""INSERT INTO work_orders
                (order_no,site_id,source,event_type,level,title,description,assignee,status)
                VALUES ('WO-DETAIL',1,'auto','设备异常','normal','设备异常','','运维员','in_progress')""")
            db.execute("""INSERT INTO alerts
                (id,site_id,metric,value,level,message,status,flow_type,flow_status,related_order_no)
                VALUES (41,1,'ph',9.1,'yellow','精确详情','pending','auto','converted','WO-DETAIL')""")
        detail = self.client.get('/api/alerts/41', headers=self.headers('operator-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['phase'], 'workorder_in_progress')
        self.assertEqual(detail.json['related_workorder_target'], {'order_no': 'WO-DETAIL'})
        missing = self.client.get('/api/alerts/4041', headers=self.headers('operator-token'))
        self.assertEqual((missing.status_code, missing.json['code']), (404, 'ALERT_NOT_FOUND'))


if __name__ == '__main__':
    unittest.main()
