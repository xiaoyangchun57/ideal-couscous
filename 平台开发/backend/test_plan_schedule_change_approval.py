import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class PlanScheduleChangeApprovalTest(unittest.TestCase):
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
        app_module._tokens.update({
            'operator-token': {'id': 9, 'role': 'operator', 'real_name': '执行人员'},
            'manager-token': {'id': 1, 'role': 'manager', 'real_name': '审批人员'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, real_name TEXT, status TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id INTEGER, title TEXT, content TEXT
                );
                CREATE TABLE plan_schedule_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, version INTEGER,
                    event_type TEXT, operator_id INTEGER, payload TEXT
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, plan_data TEXT,
                    vehicle_days TEXT, spare_parts TEXT, work_order_ids TEXT, remarks TEXT,
                    version INTEGER, change_reason TEXT, previous_plan_data TEXT,
                    period_start TEXT, period_end TEXT, coverage_exception_reason TEXT,
                    vehicle_exception_reason TEXT, vehicle_id INTEGER,
                    previous_vehicle_days TEXT, previous_vehicle_id INTEGER,
                    previous_spare_parts TEXT,
                    previous_work_order_ids TEXT, previous_remarks TEXT,
                    previous_period_start TEXT, previous_period_end TEXT,
                    previous_coverage_exception_reason TEXT,
                    previous_vehicle_exception_reason TEXT,
                    approver_id INTEGER, reject_reason TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_schedule_id INTEGER, status TEXT
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?)', [
                (9, 'operator', '执行人员', 'active'), (1, 'manager', '审批人员', 'active'),
            ])
            db.execute('INSERT INTO user_sites VALUES (9, 1)')
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,status,plan_data,vehicle_days,spare_parts,work_order_ids,remarks,version,
                 period_start,period_end,coverage_exception_reason,vehicle_exception_reason,vehicle_id)
                VALUES (5,9,'approved',?,?,?,?,?,1,?,?,?,?,?)''', (
                    json.dumps({'2026-07-25': {'sites': [1], 'notes': '原路线'}}),
                    json.dumps({'2026-07-25': 3}), json.dumps([{'part_id': 8, 'quantity': 2}]),
                    json.dumps([101]), '原备注', '2026-07-21', '2026-07-27',
                    '原覆盖说明', '', 3))
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_site_cache)
        os.unlink(self.db_path)

    def test_rejected_change_restores_every_editable_approved_field(self):
        start = self.client.post('/api/plan-schedules/5/request-change',
                                 headers={'Authorization': 'Bearer operator-token'},
                                 json={'change_reason': '车辆故障'} )
        self.assertEqual(start.status_code, 200)
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            db.execute("""UPDATE plan_schedules SET status='change_submitted',
                period_start='2026-07-22', period_end='2026-07-28',
                coverage_exception_reason='变更覆盖说明', vehicle_exception_reason='变更无车说明',
                vehicle_id=4, plan_data=?, vehicle_days=?, spare_parts=?, work_order_ids=?,
                remarks=?, version=version+1 WHERE id=5""", (
                json.dumps({'2026-07-25': {'sites': [2]}}), json.dumps({'2026-07-25': 4}),
                json.dumps([{'part_id': 9, 'quantity': 1}]), json.dumps([202]), '变更备注'))
            db.commit()
        finally:
            db.close()

        rejected = self.client.post('/api/plan-schedules/5/reject',
                                    headers={'Authorization': 'Bearer manager-token'},
                                    json={'reason': '请按原计划执行'})
        self.assertEqual(rejected.status_code, 200)
        self.assertTrue(rejected.json['rolled_back'])
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute('SELECT * FROM plan_schedules WHERE id=5').fetchone()
            self.assertEqual(row['status'], 'approved')
            self.assertEqual((row['period_start'], row['period_end']), ('2026-07-21', '2026-07-27'))
            self.assertEqual(json.loads(row['plan_data'])['2026-07-25']['sites'], [1])
            self.assertEqual(json.loads(row['vehicle_days'])['2026-07-25'], 3)
            self.assertEqual(row['vehicle_id'], 3)
            self.assertEqual(json.loads(row['spare_parts'])[0]['part_id'], 8)
            self.assertEqual(json.loads(row['work_order_ids']), [101])
            self.assertEqual(row['remarks'], '原备注')
            self.assertEqual(row['coverage_exception_reason'], '原覆盖说明')
            self.assertEqual(row['vehicle_exception_reason'], '')
            self.assertEqual(row['version'], 2)
            self.assertIsNone(row['previous_spare_parts'])
            for column in (
                'previous_plan_data', 'previous_vehicle_days', 'previous_vehicle_id',
                'previous_spare_parts', 'previous_work_order_ids', 'previous_remarks',
                'previous_period_start', 'previous_period_end',
                'previous_coverage_exception_reason', 'previous_vehicle_exception_reason',
            ):
                self.assertIsNone(row[column], column)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_schedule_events WHERE event_type='change_requested'").fetchone()[0], 1)
        finally:
            db.close()

    def test_rejected_change_restores_explicit_no_vehicle_choice(self):
        with app_module.get_db() as db:
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,status,plan_data,vehicle_days,spare_parts,work_order_ids,remarks,version,
                 period_start,period_end,coverage_exception_reason,vehicle_exception_reason,vehicle_id)
                VALUES (6,9,'approved',?,'{}','[]','[]','',4,'2026-07-21','2026-07-27','','步行巡检',NULL)''',
                       (json.dumps({'2026-07-25': {'sites': [1]}}),))
        started = self.client.post('/api/plan-schedules/6/request-change',
                                   headers={'Authorization': 'Bearer operator-token'},
                                   json={'change_reason': '改用车辆'})
        self.assertEqual(started.status_code, 200, started.json)
        with app_module.get_db() as db:
            db.execute("""UPDATE plan_schedules SET status='change_submitted', vehicle_id=4,
                vehicle_days=?, vehicle_exception_reason='' WHERE id=6""",
                       (json.dumps({'2026-07-25': 4}),))
        rejected = self.client.post('/api/plan-schedules/6/reject',
                                    headers={'Authorization': 'Bearer manager-token'},
                                    json={'reason': '保持步行巡检'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        with app_module.get_db() as db:
            row = db.execute('SELECT * FROM plan_schedules WHERE id=6').fetchone()
        self.assertIsNone(row['vehicle_id'])
        self.assertEqual(json.loads(row['vehicle_days']), {})
        self.assertEqual(row['vehicle_exception_reason'], '步行巡检')
        self.assertEqual(row['version'], 4)

    def test_rejected_historical_multi_vehicle_change_keeps_daily_facts_without_inventing_vehicle(self):
        previous_days = {'2026-07-25': 3, '2026-07-26': 4}
        with app_module.get_db() as db:
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,status,plan_data,vehicle_days,vehicle_id,spare_parts,work_order_ids,
                 remarks,version,period_start,period_end,coverage_exception_reason,
                 vehicle_exception_reason,previous_plan_data,previous_vehicle_days,
                 previous_vehicle_id,previous_spare_parts,previous_work_order_ids,
                 previous_remarks,previous_period_start,previous_period_end,
                 previous_coverage_exception_reason,previous_vehicle_exception_reason)
                VALUES (7,9,'change_submitted',?, ?,5,'[]','[]','变更中',9,
                        '2026-07-22','2026-07-28','','',?, ?,NULL,'[]','[]','历史计划',
                        '2026-07-21','2026-07-27','历史覆盖','')''', (
                    json.dumps({'2026-07-25': {'sites': [2]}}),
                    json.dumps({'2026-07-25': 5}),
                    json.dumps({'2026-07-25': {'sites': [1]}, '2026-07-26': {'sites': [1]}}),
                    json.dumps(previous_days),
                ))
        rejected = self.client.post('/api/plan-schedules/7/reject',
                                    headers={'Authorization': 'Bearer manager-token'},
                                    json={'reason': '保留历史多车事实'})
        self.assertEqual(rejected.status_code, 200, rejected.json)
        with app_module.get_db() as db:
            row = db.execute('SELECT * FROM plan_schedules WHERE id=7').fetchone()
        self.assertEqual(json.loads(row['vehicle_days']), previous_days)
        self.assertIsNone(row['vehicle_id'])
        self.assertEqual((row['period_start'], row['period_end']), ('2026-07-21', '2026-07-27'))
        self.assertEqual(row['coverage_exception_reason'], '历史覆盖')
        self.assertEqual(row['version'], 9)

    def test_completed_execution_cannot_request_a_change(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO insp_plans VALUES (88, 5, 'completed')")
            db.commit()
        response = self.client.post('/api/plan-schedules/5/request-change',
                                    headers={'Authorization': 'Bearer operator-token'},
                                    json={'change_reason': '任务已完成后尝试变更'})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'PLAN_EXECUTION_COMPLETED')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT status FROM plan_schedules WHERE id=5').fetchone()['status'],
                             'approved')


if __name__ == '__main__':
    unittest.main()
