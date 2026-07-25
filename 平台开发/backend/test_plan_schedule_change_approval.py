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
                    previous_vehicle_days TEXT, previous_spare_parts TEXT,
                    previous_work_order_ids TEXT, previous_remarks TEXT,
                    approver_id INTEGER, reject_reason TEXT
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?)', [
                (9, 'operator', '执行人员', 'active'), (1, 'manager', '审批人员', 'active'),
            ])
            db.execute('INSERT INTO user_sites VALUES (9, 1)')
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,status,plan_data,vehicle_days,spare_parts,work_order_ids,remarks,version)
                VALUES (5,9,'approved',?,?,?,?,?,1)''', (
                    json.dumps({'2026-07-25': {'sites': [1], 'notes': '原路线'}}),
                    json.dumps({'2026-07-25': 3}), json.dumps([{'part_id': 8, 'quantity': 2}]),
                    json.dumps([101]), '原备注'))
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
                plan_data=?, vehicle_days=?, spare_parts=?, work_order_ids=?, remarks=? WHERE id=5""", (
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
            self.assertEqual(json.loads(row['plan_data'])['2026-07-25']['sites'], [1])
            self.assertEqual(json.loads(row['vehicle_days'])['2026-07-25'], 3)
            self.assertEqual(json.loads(row['spare_parts'])[0]['part_id'], 8)
            self.assertEqual(json.loads(row['work_order_ids']), [101])
            self.assertEqual(row['remarks'], '原备注')
            self.assertIsNone(row['previous_spare_parts'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_schedule_events WHERE event_type='change_requested'").fetchone()[0], 1)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
