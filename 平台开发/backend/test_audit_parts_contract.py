import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class AuditPartsContractTest(unittest.TestCase):
    """Keep the two parts-request sources aligned with the audit clients."""

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
        app_module._tokens['admin-token'] = {
            'id': 1,
            'username': 'admin',
            'role': 'admin',
            'roles': ['admin'],
            'real_name': '审核管理员',
        }
        app_module._tokens['reviewer-token'] = {
            'id': 3,
            'username': 'reviewer',
            'role': 'reviewer',
            'roles': ['reviewer'],
            'real_name': '审核员',
        }
        app_module._tokens['operator-token'] = {
            'id': 2,
            'username': 'operator',
            'role': 'operator',
            'roles': ['operator'],
            'real_name': '计划执行人',
        }
        app_module.init_db()
        app_module.migrate_workorder_flow_columns()
        app_module.migrate_parts_requests_v2()
        app_module.migrate_plan_schedules()
        with temporary_db() as db:
            db.execute('DROP TABLE auth_sessions')
            db.execute("""CREATE TABLE vehicles (
                id INTEGER PRIMARY KEY, plate_no TEXT, model TEXT
            )""")
            db.execute("""CREATE TABLE vehicle_applications (
                id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER,
                start_at TEXT, end_at TEXT, destination TEXT, reason TEXT,
                status TEXT, created_at TEXT
            )""")
            db.execute('''INSERT INTO users
                (id,username,password_hash,role,real_name,status)
                VALUES (1,'admin','x','admin','审核管理员','active')''')
            db.execute('''INSERT INTO users
                (id,username,password_hash,role,real_name,status)
                VALUES (2,'operator','x','operator','计划执行人','active')''')
            db.execute("INSERT INTO user_roles (user_id,role) VALUES (1,'admin')")
            db.execute("INSERT INTO user_roles (user_id,role) VALUES (2,'operator')")
            db.execute("INSERT INTO sites (id,code,name,type,status) VALUES (1,'S-1','测试站点','water_quality','online')")
            db.execute("INSERT INTO user_sites (user_id,site_id) VALUES (2,1)")
            db.execute("""INSERT INTO spare_parts_inventory
                (id,part_code,part_name,manufacturer,model,quantity)
                VALUES (1,'P-001','采样泵','厂商A','M-1',10)""")
            db.execute("""INSERT INTO parts_requests
                (id,plan_id,requester_id,site_id,request_no,source,reason,status,
                 fulfillment_type,requested_part_name,specification,created_at)
                VALUES (11,0,2,1,'PR-11','field','巡检备用','pending','stock','采样泵','M-1',?)""",
                        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
            db.execute("""INSERT INTO parts_request_items
                (request_id,part_sku,quantity,part_id) VALUES (11,'P-001',2,1)""")
            db.execute("""INSERT INTO spare_part_requests
                (id,request_no,site_id,applicant,part_name,spare_part_id,quantity,
                 reason,status,created_at,updated_at)
                VALUES (21,'SPR-21',1,'现场申请人','采样泵',1,3,'历史现场更换','pending',?,?)""",
                        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            db.execute("""INSERT INTO plan_schedules
                (id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,
                 spare_parts,work_order_ids,status,submitted_at,version,tasks_generated)
                VALUES (31,2,'weekly','2026-08-10','2026-08-16',?,?,?,?, 'submitted',?,1,0)""",
                        (json.dumps({'2026-08-10': {'sites': [1], 'notes': '路线'}}),
                         '{}', '[]', '[]',
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            db.execute("""INSERT INTO plan_schedule_events
                (schedule_id,version,event_type,operator_id,payload)
                VALUES (31,1,'submitted',1,'{}')""")
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_path)

    def headers(self, token='admin-token'):
        return {'Authorization': f'Bearer {token}'}

    def test_locate_contract_distinguishes_pending_processed_missing_and_forbidden(self):
        pending = self.client.get(
            '/api/audit/locate?request_type=parts_request&id=11', headers=self.headers()
        )
        self.assertEqual(pending.status_code, 200, pending.json)
        self.assertEqual(pending.json['resolution'], 'found')
        self.assertEqual(pending.json['status'], 'pending')
        self.assertTrue(pending.json['found'])
        self.assertEqual(pending.json['item']['id'], 'pr_11')

        with app_module.get_db() as db:
            db.execute("UPDATE parts_requests SET status='approved' WHERE id=11")

        processed = self.client.get(
            '/api/audit/locate?request_type=parts_request&id=11', headers=self.headers()
        )
        self.assertEqual(processed.status_code, 200, processed.json)
        self.assertEqual(processed.json['resolution'], 'processed')
        self.assertEqual(processed.json['status'], 'processed')
        self.assertEqual(processed.json['state'], 'approved')

        missing = self.client.get(
            '/api/audit/locate?request_type=parts_request&id=999', headers=self.headers()
        )
        self.assertEqual(missing.status_code, 200, missing.json)
        self.assertEqual(missing.json['resolution'], 'missing')
        self.assertEqual(missing.json['status'], 'missing')

        forbidden = self.client.get(
            '/api/audit/locate?request_type=parts_request&id=11',
            headers=self.headers('reviewer-token'),
        )
        self.assertEqual(forbidden.status_code, 200, forbidden.json)
        self.assertEqual(forbidden.json['resolution'], 'forbidden')
        self.assertEqual(forbidden.json['status'], 'forbidden')

    def test_locate_contract_routes_same_id_by_request_type(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO parts_requests
                (id,plan_id,requester_id,site_id,request_no,source,reason,status,
                 fulfillment_type,requested_part_name,specification,created_at)
                VALUES (17,0,2,1,'PR-17','field','同编号 v2','pending','stock','采样泵','M-1',?)""",
                        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
            db.execute(
                "INSERT INTO parts_request_items (request_id,part_sku,quantity,part_id) VALUES (17,'P-001',1,1)"
            )
            db.execute("""INSERT INTO spare_part_requests
                (id,request_no,site_id,applicant,part_name,spare_part_id,quantity,
                 reason,status,created_at,updated_at)
                VALUES (17,'SPR-17',1,'历史申请人','采样泵',1,2,'同编号 legacy','pending',?,?)""",
                        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        v2 = self.client.get(
            '/api/audit/locate?request_type=parts_request&id=17', headers=self.headers()
        )
        legacy = self.client.get(
            '/api/audit/locate?request_type=spare_part_request&id=17', headers=self.headers()
        )
        self.assertEqual(v2.json['status'], 'pending')
        self.assertEqual(v2.json['item']['id'], 'pr_17')
        self.assertEqual(legacy.json['status'], 'pending')
        self.assertEqual(legacy.json['item']['id'], 'spr_17')

    def test_pending_and_actions_preserve_typed_parts_contract(self):
        pending = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(pending.status_code, 200, pending.json)
        parts = next(item for item in pending.json if item['source_type'] == 'parts_request')
        legacy = next(item for item in pending.json if item['source_type'] == 'spare_part_request')
        plan = next(item for item in pending.json if item['source_type'] == 'plan_schedule')

        self.assertEqual(parts['id'], 'pr_11')
        self.assertEqual(parts['parts_detail'][0]['part_sku'], 'P-001')
        self.assertEqual(legacy['id'], 'spr_21')
        self.assertEqual(legacy['request_id'], 21)
        self.assertEqual(legacy['requester_name'], '现场申请人')
        self.assertEqual(legacy['parts_detail'][0]['quantity'], 3)
        self.assertEqual(plan['executor_name'], '计划执行人')
        self.assertEqual(plan['requester_name'], '审核管理员')

        approved = self.client.put('/api/parts/requests/21/approve', headers=self.headers(), json={
            'request_type': 'spare_part_request',
        })
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(approved.json['source_type'], 'spare_part_request')
        self.assertEqual(approved.json['status'], 'approved')
        with app_module.get_db() as db:
            row = db.execute(
                'SELECT status,approver,approval_comment FROM spare_part_requests WHERE id=21'
            ).fetchone()
        self.assertEqual(tuple(row), ('approved', '审核管理员', '审批通过'))

    def test_change_schedule_is_pending_for_admin_but_operator_cannot_open_audit(self):
        with app_module.get_db() as db:
            db.execute("UPDATE plan_schedules SET status='change_submitted' WHERE id=31")
        pending = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertEqual(pending.status_code, 200, pending.json)
        plan = next(item for item in pending.json if item['source_type'] == 'plan_schedule')
        self.assertEqual(plan['schedule_id'], 31)
        self.assertTrue(plan['is_change'])
        forbidden = self.client.get('/api/audit/pending', headers=self.headers('operator-token'))
        self.assertEqual(forbidden.status_code, 403, forbidden.json)

    def test_legacy_reject_requires_reason_and_is_removed_from_pending(self):
        rejected = self.client.put('/api/parts/requests/21/reject', headers=self.headers(), json={
            'request_type': 'spare_part_request',
        })
        self.assertEqual(rejected.status_code, 400, rejected.json)
        rejected = self.client.put('/api/parts/requests/21/reject', headers=self.headers(), json={
            'request_type': 'spare_part_request', 'comment': '请补充现场用途',
        })
        self.assertEqual(rejected.status_code, 200, rejected.json)
        pending = self.client.get('/api/audit/pending', headers=self.headers())
        self.assertFalse(any(item['id'] == 'spr_21' for item in pending.json))


if __name__ == '__main__':
    unittest.main()
