import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class ManualReportClosureTest(unittest.TestCase):
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
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'operator-token': {'id': 9, 'role': 'operator', 'real_name': '现场人员'},
            'manager-token': {'id': 2, 'role': 'manager', 'real_name': '主管'},
            'admin-token': {'id': 1, 'role': 'admin', 'real_name': '管理员'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, real_name TEXT,
                    status TEXT DEFAULT 'active', openid TEXT DEFAULT '');
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, status TEXT, related_alert_id INTEGER,
                    used_parts TEXT, site_id INTEGER, check_in_time TEXT, check_in_lat REAL,
                    check_in_lng REAL, resolved_at TEXT, assignee TEXT, source TEXT,
                    images TEXT DEFAULT '', remark TEXT DEFAULT '', conclusion TEXT DEFAULT '',
                    review_submitted_at TEXT
                );
                CREATE TABLE manual_reports (
                    id INTEGER PRIMARY KEY, site_id INTEGER, status TEXT, order_no TEXT,
                    verification_note TEXT DEFAULT '', verified_by INTEGER, verified_at TEXT,
                    resolved_at TEXT, archived_by INTEGER, archived_at TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER, file_type TEXT,
                    is_deleted INTEGER DEFAULT 0, review_status TEXT, reviewer_id INTEGER,
                    reviewed_at TEXT, reject_reason TEXT, is_flagged INTEGER DEFAULT 0,
                    flag_reason TEXT DEFAULT '', taken_at TEXT, duplicate_of_id INTEGER,
                    evidence_qualification TEXT DEFAULT 'qualified'
                );
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, status TEXT, resolved_at TEXT,
                    resolve_reason TEXT, site_id INTEGER, metric TEXT, related_order_no TEXT);
                CREATE TABLE hotline_events (id INTEGER PRIMARY KEY, related_order_no TEXT, status TEXT);
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT
                );
            ''')
            db.executemany('INSERT INTO users (id, role, real_name) VALUES (?,?,?)', [
                (1, 'admin', '管理员'), (2, 'manager', '主管'), (9, 'operator', '现场人员'),
            ])
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [
                (1, 'admin'), (2, 'admin'), (9, 'operator'),
            ])
            db.execute('INSERT INTO user_sites VALUES (9, 1)')
            db.execute("""INSERT INTO work_orders
                (id,order_no,status,related_alert_id,used_parts,site_id,assignee,source)
                VALUES (1,'MR202607250001','reviewing',NULL,'',1,'现场人员','manual_report')""")
            db.execute("INSERT INTO manual_reports (id, site_id, status, order_no) VALUES (7, 1, 'dispatched', 'MR202607250001')")
            db.execute("INSERT INTO operation_attachments (id, source_type, source_id, file_type, taken_at) VALUES (1, 'workorder', 1, 'image', '2026-07-25 10:00:00')")
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        os.unlink(self.db_path)

    def headers(self, token):
        return {'Authorization': f'Bearer {token}'}

    def _approval_snapshot(self):
        with app_module.get_db() as db:
            return {
                'order': tuple(db.execute(
                    'SELECT status,resolved_at FROM work_orders WHERE id=1').fetchone()),
                'report': tuple(db.execute(
                    'SELECT status,resolved_at FROM manual_reports WHERE id=7').fetchone()),
                'attachment': tuple(db.execute(
                    'SELECT review_status,reviewer_id,reviewed_at FROM operation_attachments WHERE id=1').fetchone()),
                'alerts': [tuple(row) for row in db.execute(
                    'SELECT id,status,resolved_at,resolve_reason FROM alerts ORDER BY id')],
                'events': [tuple(row) for row in db.execute(
                    'SELECT source_type,source_id,event_type,operator,remark FROM timeline_events ORDER BY id')],
            }

    def test_verification_requires_approver_and_preserves_workorder(self):
        denied = self.client.post('/api/manual-reports/7/verify', headers=self.headers('operator-token'))
        self.assertEqual(denied.status_code, 403)

        missing_note = self.client.post('/api/manual-reports/7/verify', headers=self.headers('manager-token'), json={})
        self.assertEqual(missing_note.status_code, 400)

        verified = self.client.post('/api/manual-reports/7/verify', headers=self.headers('manager-token'), json={'note': '现场描述已核实'})
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json['status'], 'verified')
        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute('SELECT status FROM manual_reports WHERE id=7').fetchone()[0], 'verified')
            self.assertEqual(db.execute("SELECT status FROM work_orders WHERE order_no='MR202607250001'").fetchone()[0], 'reviewing')
        finally:
            db.close()

    def test_close_resolves_report_then_manager_can_archive(self):
        closed = self.client.post('/api/workorders/MR202607250001/approve', headers=self.headers('admin-token'), json={})
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json['status'], 'closed')
        db = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(db.execute('SELECT status FROM manual_reports WHERE id=7').fetchone()[0], 'resolved')
        finally:
            db.close()

        archived = self.client.post('/api/manual-reports/7/archive', headers=self.headers('manager-token'))
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.json['status'], 'archived')

    def test_close_resolves_reverse_only_linked_alert(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO alerts
                (id,status,site_id,metric,related_order_no)
                VALUES (3,'pending',1,'manual_report','MR202607250001')""")
        closed = self.client.post('/api/workorders/MR202607250001/approve',
                                  headers=self.headers('admin-token'), json={'conclusion': 'handled'})
        self.assertEqual(closed.status_code, 200, closed.json)
        with app_module.get_db() as db:
            alert = db.execute(
                'SELECT status,resolved_at,resolve_reason FROM alerts WHERE id=3').fetchone()
            self.assertEqual((alert['status'], alert['resolve_reason']), ('resolved', 'handled'))
            self.assertTrue(alert['resolved_at'])
            event = db.execute("""SELECT operator FROM timeline_events
                WHERE source_type='alert' AND source_id=3 AND event_type='resolved'""").fetchone()
            self.assertEqual(event['operator'], '管理员')

    def test_close_deduplicates_bidirectional_link_and_resolves_multiple_alerts_once(self):
        with app_module.get_db() as db:
            db.execute('UPDATE work_orders SET related_alert_id=3 WHERE id=1')
            db.executemany("""INSERT INTO alerts
                (id,status,site_id,metric,related_order_no) VALUES (?,?,?,?,?)""", [
                (3, 'pending', 1, 'manual_report', 'MR202607250001'),
                (4, 'acknowledged', 1, 'ph', 'MR202607250001'),
                (5, 'resolved', 1, 'ammonia', 'MR202607250001'),
            ])
        first = self.client.post('/api/workorders/MR202607250001/approve',
                                 headers=self.headers('admin-token'), json={'conclusion': 'handled'})
        self.assertEqual(first.status_code, 200, first.json)
        second = self.client.post('/api/workorders/MR202607250001/approve',
                                  headers=self.headers('admin-token'), json={'conclusion': 'handled'})
        self.assertEqual(second.status_code, 200, second.json)
        self.assertTrue(second.json['already_closed'])
        with app_module.get_db() as db:
            self.assertEqual(dict(db.execute('SELECT id,status FROM alerts ORDER BY id').fetchall()),
                             {3: 'resolved', 4: 'resolved', 5: 'resolved'})
            counts = dict(db.execute("""SELECT source_id,COUNT(*) FROM timeline_events
                WHERE source_type='alert' AND event_type='resolved' GROUP BY source_id""").fetchall())
            self.assertEqual(counts, {3: 1, 4: 1})

    def test_close_rejects_alert_link_conflicts_without_side_effects(self):
        cases = (
            ('wrong-site', None, (3, 'pending', 2, 'manual_report', 'MR202607250001'),
             'WORKORDER_ALERT_LINK_CONFLICT'),
            ('opposite-order', 3, (3, 'pending', 1, 'manual_report', 'WO-OTHER'),
             'WORKORDER_ALERT_LINK_CONFLICT'),
            ('invalid-state', None, (3, 'archived', 1, 'manual_report', 'MR202607250001'),
             'WORKORDER_ALERT_STATE_CONFLICT'),
        )
        for label, related_alert_id, alert, expected_code in cases:
            with self.subTest(label=label):
                with app_module.get_db() as db:
                    db.execute('UPDATE work_orders SET related_alert_id=? WHERE id=1', (related_alert_id,))
                    db.execute("""INSERT INTO alerts
                        (id,status,site_id,metric,related_order_no) VALUES (?,?,?,?,?)""", alert)
                baseline = self._approval_snapshot()
                response = self.client.post('/api/workorders/MR202607250001/approve',
                    headers=self.headers('admin-token'), json={'conclusion': 'handled'})
                self.assertEqual((response.status_code, response.json.get('code')),
                                 (409, expected_code), response.json)
                self.assertEqual(self._approval_snapshot(), baseline)
                with app_module.get_db() as db:
                    db.execute('DELETE FROM alerts')
                    db.execute('UPDATE work_orders SET related_alert_id=NULL WHERE id=1')

    def test_close_rolls_back_order_alert_and_audits_when_alert_audit_fails(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO alerts
                (id,status,site_id,metric,related_order_no)
                VALUES (3,'pending',1,'manual_report','MR202607250001')""")
            db.execute("""CREATE TRIGGER fail_alert_close_audit
                BEFORE INSERT ON timeline_events
                WHEN NEW.source_type='alert' AND NEW.event_type='resolved'
                BEGIN SELECT RAISE(ABORT, 'audit failure'); END""")
        baseline = self._approval_snapshot()
        response = self.client.post('/api/workorders/MR202607250001/approve',
            headers=self.headers('admin-token'), json={'conclusion': 'handled'})
        self.assertEqual((response.status_code, response.json.get('code')),
                         (503, 'WORKORDER_CLOSE_ROLLED_BACK'), response.json)
        self.assertEqual(self._approval_snapshot(), baseline)

    def test_cannot_archive_unresolved_report(self):
        archived = self.client.post('/api/manual-reports/7/archive', headers=self.headers('manager-token'))
        self.assertEqual(archived.status_code, 400)

    def _prepare_dismissable(self):
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='pending', related_alert_id=3 WHERE id=1")
            db.execute("UPDATE operation_attachments SET source_type='manual_report',source_id=7 WHERE id=1")
            db.execute("""INSERT INTO alerts (id,status,site_id,metric,related_order_no)
                VALUES (3,'pending',1,'manual_report','MR202607250001')""")

    def _snapshot(self):
        with app_module.get_db() as db:
            return {
                'report': tuple(db.execute("SELECT status,resolved_at FROM manual_reports WHERE id=7").fetchone()),
                'order': tuple(db.execute("SELECT status,resolved_at,remark,conclusion FROM work_orders WHERE id=1").fetchone()),
                'alert': tuple(db.execute("SELECT status,resolved_at,resolve_reason FROM alerts WHERE id=3").fetchone()),
                'events': db.execute("SELECT COUNT(*) FROM timeline_events").fetchone()[0],
                'attachments': db.execute("SELECT COUNT(*) FROM operation_attachments").fetchone()[0],
            }

    def test_admin_dismisses_unstarted_report_atomically_and_replay_is_idempotent(self):
        self._prepare_dismissable()
        first = self.client.post('/api/manual-reports/7/dismiss', headers=self.headers('admin-token'),
                                 json={'reason': '现场复核为误报，无需处置'})
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json['status'], 'resolved')
        with app_module.get_db() as db:
            self.assertEqual(db.execute("SELECT status FROM work_orders WHERE id=1").fetchone()[0], 'closed')
            self.assertEqual(db.execute("SELECT conclusion FROM work_orders WHERE id=1").fetchone()[0], 'false_alarm')
            self.assertEqual(db.execute("SELECT status FROM alerts WHERE id=3").fetchone()[0], 'resolved')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operation_attachments").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE event_type='dismissed'").fetchone()[0], 2)
        second = self.client.post('/api/manual-reports/7/dismiss', headers=self.headers('admin-token'),
                                  json={'reason': '响应丢失后重试'})
        self.assertEqual(second.status_code, 200, second.json)
        self.assertTrue(second.json['already_dismissed'])
        with app_module.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE event_type='dismissed'").fetchone()[0], 2)

    def test_dismiss_validates_reason_permission_and_relationship_without_writes(self):
        self._prepare_dismissable()
        baseline = self._snapshot()
        cases = [
            ('operator-token', {'reason': '误报'}, 403, 'FORBIDDEN'),
            ('admin-token', {}, 400, 'MANUAL_REPORT_DISMISS_REASON_REQUIRED'),
            ('admin-token', {'reason': 'x' * 501}, 400, 'MANUAL_REPORT_DISMISS_REASON_TOO_LONG'),
        ]
        for token, payload, status, code in cases:
            with self.subTest(code=code):
                response = self.client.post('/api/manual-reports/7/dismiss', headers=self.headers(token), json=payload)
                self.assertEqual((response.status_code, response.json.get('code')), (status, code), response.json)
                self.assertEqual(self._snapshot(), baseline)
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET source='auto' WHERE id=1")
        changed = self._snapshot()
        response = self.client.post('/api/manual-reports/7/dismiss', headers=self.headers('admin-token'), json={'reason': '误报'})
        self.assertEqual((response.status_code, response.json.get('code')), (409, 'MANUAL_REPORT_LINK_MISMATCH'))
        self.assertEqual(self._snapshot(), changed)

        missing = self.client.post('/api/manual-reports/999/dismiss', headers=self.headers('admin-token'),
                                   json={'reason': '误报'})
        self.assertEqual((missing.status_code, missing.json.get('code')),
                         (404, 'MANUAL_REPORT_NOT_FOUND'))

    def test_dismiss_blocks_each_started_fact_without_side_effects(self):
        facts = [
            ("status='accepted'", None),
            ("check_in_time='2026-08-21 09:00:00'", None),
            ("check_in_lat=28.6", None),
            ("images='[\"/uploads/work.jpg\"]'", None),
            ("remark='已处置'", None),
            ("review_submitted_at='2026-08-21 10:00:00'", None),
            ("used_parts='[{\"part_id\":1,\"quantity\":1}]'", None),
            (None, "INSERT INTO operation_attachments (id,source_type,source_id,file_type,is_deleted) VALUES (9,'workorder',1,'image',0)"),
        ]
        for index, (order_update, extra_sql) in enumerate(facts):
            with self.subTest(index=index):
                self._prepare_dismissable()
                with app_module.get_db() as db:
                    if order_update:
                        db.execute(f"UPDATE work_orders SET {order_update} WHERE id=1")
                    if extra_sql:
                        db.execute(extra_sql)
                baseline = self._snapshot()
                response = self.client.post('/api/manual-reports/7/dismiss', headers=self.headers('admin-token'), json={'reason': '误报'})
                self.assertEqual((response.status_code, response.json.get('code')), (409, 'MANUAL_REPORT_DISMISS_ALREADY_STARTED'), response.json)
                self.assertEqual(self._snapshot(), baseline)
                with app_module.get_db() as db:
                    db.execute("DELETE FROM alerts WHERE id=3")
                    db.execute("DELETE FROM operation_attachments WHERE id=9")
                    db.execute("""UPDATE work_orders SET status='reviewing',related_alert_id=NULL,
                        check_in_time=NULL,check_in_lat=NULL,check_in_lng=NULL,images='',remark='',
                        conclusion='',review_submitted_at=NULL,used_parts='' WHERE id=1""")

    def test_normally_resolved_report_is_not_treated_as_dismissed(self):
        with app_module.get_db() as db:
            db.execute("UPDATE manual_reports SET status='resolved',resolved_at='2026-08-21 11:00:00' WHERE id=7")
            db.execute("UPDATE work_orders SET status='closed',resolved_at='2026-08-21 11:00:00' WHERE id=1")
        response = self.client.post('/api/manual-reports/7/dismiss', headers=self.headers('admin-token'), json={'reason': '误报'})
        self.assertEqual((response.status_code, response.json.get('code')), (409, 'MANUAL_REPORT_ALREADY_RESOLVED'))

    def test_dismiss_audit_failure_rolls_back_every_state_change(self):
        self._prepare_dismissable()
        baseline = self._snapshot()
        with app_module.get_db() as db:
            db.execute("""CREATE TRIGGER fail_dismiss_audit BEFORE INSERT ON timeline_events
                WHEN NEW.event_type='dismissed' BEGIN SELECT RAISE(ABORT, 'audit failure'); END""")
        response = self.client.post('/api/manual-reports/7/dismiss', headers=self.headers('admin-token'),
                                    json={'reason': '误报'})
        self.assertEqual((response.status_code, response.json.get('code')),
                         (503, 'MANUAL_REPORT_DISMISS_FAILED'))
        self.assertEqual(self._snapshot(), baseline)

    def test_dismiss_rereads_active_admin_inside_transaction(self):
        for mutation in (
                "UPDATE users SET status='inactive' WHERE id=1",
                "UPDATE users SET role='operator' WHERE id=1; DELETE FROM user_roles WHERE user_id=1"):
            with self.subTest(mutation=mutation):
                self._prepare_dismissable()
                with app_module.get_db() as db:
                    db.executescript(mutation)
                baseline = self._snapshot()
                response = self.client.post('/api/manual-reports/7/dismiss',
                    headers=self.headers('admin-token'), json={'reason': '误报'})
                self.assertEqual((response.status_code, response.json.get('code')), (403, 'FORBIDDEN'))
                self.assertEqual(self._snapshot(), baseline)
                with app_module.get_db() as db:
                    db.execute("UPDATE users SET status='active',role='admin' WHERE id=1")
                    db.execute("INSERT INTO user_roles VALUES (1,'admin')")
                    db.execute("DELETE FROM alerts WHERE id=3")

    def test_dismiss_json_and_zero_coordinate_facts_fail_closed(self):
        cases = (
            ("check_in_lat=0", 'coordinate-zero'),
            ("images='not-json'", 'invalid-images'),
            ("used_parts='{}'", 'invalid-parts-shape'),
            ("images='[]',used_parts='[]'", 'valid-empty-arrays'),
        )
        for update, label in cases:
            with self.subTest(label=label):
                self._prepare_dismissable()
                with app_module.get_db() as db:
                    db.execute(f"UPDATE work_orders SET {update} WHERE id=1")
                response = self.client.post('/api/manual-reports/7/dismiss',
                    headers=self.headers('admin-token'), json={'reason': '误报'})
                if label == 'valid-empty-arrays':
                    self.assertEqual(response.status_code, 200, response.json)
                else:
                    self.assertEqual((response.status_code, response.json.get('code')),
                                     (409, 'MANUAL_REPORT_DISMISS_ALREADY_STARTED'), response.json)
                with app_module.get_db() as db:
                    db.execute("DELETE FROM alerts WHERE id=3")
                    db.execute("""UPDATE manual_reports SET status='dispatched',resolved_at=NULL,
                        verification_note='',verified_by=NULL,verified_at=NULL WHERE id=7""")
                    db.execute("""UPDATE work_orders SET status='reviewing',related_alert_id=NULL,
                        check_in_lat=NULL,images='',used_parts='',resolved_at=NULL,remark='',conclusion='' WHERE id=1""")
                    db.execute("DELETE FROM timeline_events")

    def test_dismiss_blocks_any_linked_resource_record(self):
        resource_tables = {
            'parts_requests': 'CREATE TABLE parts_requests (id INTEGER, work_order_no TEXT)',
            'spare_part_requests': 'CREATE TABLE spare_part_requests (id INTEGER, work_order_no TEXT)',
            'device_recycle': 'CREATE TABLE device_recycle (id INTEGER, work_order_no TEXT)',
            'vehicle_applications': 'CREATE TABLE vehicle_applications (id INTEGER, work_order_no TEXT)',
            'inventory_logs': 'CREATE TABLE inventory_logs (id INTEGER, work_order_no TEXT)',
        }
        with app_module.get_db() as db:
            for statement in resource_tables.values():
                db.execute(statement)
        for table in resource_tables:
            with self.subTest(table=table):
                self._prepare_dismissable()
                with app_module.get_db() as db:
                    db.execute(f"INSERT INTO {table} VALUES (1,'MR202607250001')")
                baseline = self._snapshot()
                response = self.client.post('/api/manual-reports/7/dismiss',
                    headers=self.headers('admin-token'), json={'reason': '误报'})
                self.assertEqual((response.status_code, response.json.get('code')),
                                 (409, 'MANUAL_REPORT_DISMISS_ALREADY_STARTED'), response.json)
                self.assertIn('用车/备件/回收或库存事实', response.json['error'])
                self.assertEqual(self._snapshot(), baseline)
                with app_module.get_db() as db:
                    db.execute(f'DELETE FROM {table}')
                    db.execute('DELETE FROM alerts WHERE id=3')

    def test_dismiss_enforces_linked_alert_state_consistency(self):
        for status, expected_status in (('pending', 200), ('resolved', 200), ('acknowledged', 409)):
            with self.subTest(status=status):
                self._prepare_dismissable()
                with app_module.get_db() as db:
                    db.execute('UPDATE alerts SET status=? WHERE id=3', (status,))
                    if status == 'resolved':
                        db.execute("""UPDATE alerts SET resolved_at='2026-08-20 08:00:00',
                            resolve_reason='previous_resolution' WHERE id=3""")
                    original_alert = tuple(db.execute(
                        'SELECT status,resolved_at,resolve_reason FROM alerts WHERE id=3').fetchone())
                baseline = self._snapshot()
                response = self.client.post('/api/manual-reports/7/dismiss',
                    headers=self.headers('admin-token'), json={'reason': '误报'})
                self.assertEqual(response.status_code, expected_status, response.json)
                with app_module.get_db() as db:
                    alert = db.execute('SELECT status FROM alerts WHERE id=3').fetchone()[0]
                    self.assertEqual(alert, 'resolved' if status == 'pending' else status)
                    if status == 'resolved':
                        self.assertEqual(tuple(db.execute(
                            'SELECT status,resolved_at,resolve_reason FROM alerts WHERE id=3').fetchone()),
                            original_alert)
                    if expected_status == 409:
                        self.assertEqual(self._snapshot(), baseline)
                    db.execute('DELETE FROM alerts WHERE id=3')
                    db.execute("""UPDATE manual_reports SET status='dispatched',resolved_at=NULL,
                        verification_note='',verified_by=NULL,verified_at=NULL WHERE id=7""")
                    db.execute("""UPDATE work_orders SET status='reviewing',related_alert_id=NULL,
                        resolved_at=NULL,remark='',conclusion='' WHERE id=1""")
                    db.execute('DELETE FROM timeline_events')

    def test_verified_report_can_be_dismissed_and_keeps_verification_audit(self):
        self._prepare_dismissable()
        with app_module.get_db() as db:
            db.execute("UPDATE manual_reports SET status='verified' WHERE id=7")
            db.execute("""INSERT INTO timeline_events
                (source_type,source_id,event_type,operator,remark)
                VALUES ('manual_report',7,'verified','管理员','此前确认需处置')""")
        response = self.client.post('/api/manual-reports/7/dismiss',
            headers=self.headers('admin-token'), json={'reason': '复核后确认无需处置'})
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='manual_report' AND source_id=7 AND event_type='verified'""").fetchone()[0], 1)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM timeline_events
                WHERE source_type='manual_report' AND source_id=7 AND event_type='dismissed'""").fetchone()[0], 1)

    def test_verified_report_still_obeys_every_existing_gate(self):
        with app_module.get_db() as db:
            db.execute('CREATE TABLE parts_requests (id INTEGER, work_order_no TEXT)')
        cases = (
            ('accepted', "UPDATE work_orders SET status='accepted' WHERE id=1", None),
            ('checkin', "UPDATE work_orders SET check_in_time='2026-08-21 09:00:00' WHERE id=1", None),
            ('attachment', None, """INSERT INTO operation_attachments
                (id,source_type,source_id,file_type,is_deleted) VALUES (9,'workorder',1,'image',0)"""),
            ('resource', None, "INSERT INTO parts_requests VALUES (1,'MR202607250001')"),
            ('alert', None, "UPDATE alerts SET status='acknowledged' WHERE id=3"),
        )
        for label, order_sql, extra_sql in cases:
            with self.subTest(label=label):
                self._prepare_dismissable()
                with app_module.get_db() as db:
                    db.execute("UPDATE manual_reports SET status='verified' WHERE id=7")
                    if order_sql:
                        db.execute(order_sql)
                    if extra_sql:
                        db.execute(extra_sql)
                baseline = self._snapshot()
                response = self.client.post('/api/manual-reports/7/dismiss',
                    headers=self.headers('admin-token'), json={'reason': '复核后确认无需处置'})
                self.assertEqual(response.status_code, 409, response.json)
                self.assertEqual(self._snapshot(), baseline)
                with app_module.get_db() as db:
                    db.execute('DELETE FROM alerts WHERE id=3')
                    db.execute('DELETE FROM operation_attachments WHERE id=9')
                    db.execute('DELETE FROM parts_requests')
                    db.execute("UPDATE manual_reports SET status='dispatched' WHERE id=7")
                    db.execute("""UPDATE work_orders SET status='reviewing',related_alert_id=NULL,
                        check_in_time=NULL WHERE id=1""")


if __name__ == '__main__':
    unittest.main()
