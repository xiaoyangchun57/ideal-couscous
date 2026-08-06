import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class AssetFulfillmentClosureTest(unittest.TestCase):
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
            'admin-token': {'id': 1, 'username': 'admin', 'real_name': '管理员', 'role': 'admin'},
            'operator-token': {'id': 2, 'username': 'operator', 'real_name': '现场人员', 'role': 'operator'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, real_name TEXT, role TEXT, status TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, code TEXT, name TEXT);
                CREATE TABLE device_shadows (
                    id INTEGER PRIMARY KEY, site_id INTEGER, device_code TEXT, device_name TEXT,
                    device_type TEXT, device_model TEXT, manufacturer TEXT, install_date TEXT,
                    status TEXT DEFAULT 'online', management_scope TEXT DEFAULT 'managed',
                    monitoring_enabled INTEGER DEFAULT 1, last_data_time TEXT
                );
                CREATE TABLE device_recycle (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER, device_code TEXT,
                    device_name TEXT, device_type TEXT, site_id INTEGER, site_name TEXT,
                    recycle_date TEXT, reason TEXT, destination TEXT, operator TEXT, remark TEXT,
                    status TEXT DEFAULT 'recycled', created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER,
                    event_type TEXT, operator TEXT, remark TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT, action TEXT,
                    target_type TEXT, target_id INTEGER, operator TEXT, operator_id INTEGER,
                    details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE spare_parts_inventory (
                    id INTEGER PRIMARY KEY, part_code TEXT, part_name TEXT, manufacturer TEXT DEFAULT '',
                    model TEXT DEFAULT '', category TEXT DEFAULT '', unit TEXT DEFAULT '件',
                    quantity INTEGER DEFAULT 0, min_quantity INTEGER DEFAULT 0, site_id INTEGER,
                    remark TEXT DEFAULT '', updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE parts_requests (
                    id INTEGER PRIMARY KEY, plan_id INTEGER DEFAULT 0, requester_id INTEGER,
                    site_id INTEGER, work_order_no TEXT, request_no TEXT, source TEXT, reason TEXT,
                    status TEXT, approver_id INTEGER, approve_comment TEXT, approved_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, fulfillment_type TEXT,
                    requested_part_name TEXT, specification TEXT, estimated_amount REAL,
                    actual_amount REAL, supplier TEXT DEFAULT '', receipt_no TEXT DEFAULT '',
                    tracking_no TEXT DEFAULT '', evidence_urls TEXT DEFAULT '[]', destination TEXT DEFAULT '',
                    old_part_disposition TEXT DEFAULT '', ordered_at TEXT, received_at TEXT, completed_at TEXT
                );
                CREATE TABLE parts_request_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, part_sku TEXT,
                    quantity INTEGER, part_id INTEGER
                );
                CREATE TABLE parts_request_reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, part_id INTEGER,
                    requested_quantity INTEGER, reserved_quantity INTEGER DEFAULT 0,
                    issued_quantity INTEGER DEFAULT 0, status TEXT, updated_at TEXT,
                    UNIQUE(request_id, part_id)
                );
                CREATE TABLE parts_request_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER, event_type TEXT,
                    operator_id INTEGER, operator TEXT, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE inventory_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, part_id INTEGER, type TEXT, quantity INTEGER,
                    ref_type TEXT, ref_id INTEGER, operator TEXT, operator_id INTEGER,
                    remark TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO users VALUES (1,'admin','管理员','admin','active');
                INSERT INTO users VALUES (2,'operator','现场人员','operator','active');
                INSERT INTO sites VALUES (1,'S001','测试站');
                INSERT INTO device_shadows VALUES (1,1,'DEV-001','分析仪','analyzer','','','2026-01-01','online','managed',1,NULL);
                INSERT INTO device_shadows VALUES (2,1,'DEV-002','离线分析仪','analyzer','','','2026-01-01','offline','managed',1,NULL);
                INSERT INTO device_shadows VALUES (3,1,'DEV-003','待删除离线设备','analyzer','','','2026-01-01','offline','managed',1,NULL);
                INSERT INTO spare_parts_inventory (id,part_code,part_name,quantity) VALUES (1,'P001','泵管',5);
                INSERT INTO parts_requests (id,requester_id,site_id,work_order_no,request_no,status,approved_at,fulfillment_type,requested_part_name)
                    VALUES (1,2,1,'WO-001','BJ-001','approved','2026-08-05 09:00:00','stock','泵管');
                INSERT INTO parts_request_items (request_id,part_sku,quantity,part_id) VALUES (1,'P001',2,1);
                INSERT INTO parts_requests (id,requester_id,site_id,request_no,status,approved_at,fulfillment_type,requested_part_name,estimated_amount)
                    VALUES (2,2,1,'BJ-002','approved','2026-08-05 09:00:00','vendor_order','传感器',300);
                INSERT INTO parts_request_items (request_id,part_sku,quantity,part_id) VALUES (2,'传感器',1,NULL);
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

    def test_recycle_is_immediate_unique_and_audited(self):
        # 模拟旧库缺少 work_order_no，入口迁移必须能在其他可选表缺失时独立补齐。
        app_module.migrate_workorder_flow_columns()
        with app_module.get_db() as db:
            recycle_columns = {row['name'] for row in db.execute('PRAGMA table_info(device_recycle)')}
            self.assertIn('work_order_no', recycle_columns)
            self.assertIn('operator_id', recycle_columns)
        response = self.client.post('/api/device-recycle', headers=self.headers(), json={
            'device_id': 1, 'destination': 'scrap', 'recycle_date': '2026-08-05',
            'reason': '无法修复', 'operator': '伪造操作人',
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            device = db.execute('SELECT status,management_scope FROM device_shadows WHERE id=1').fetchone()
            self.assertEqual((device['status'], device['management_scope']), ('offline', 'retired'))
            recycle = db.execute('SELECT operator,operator_id FROM device_recycle WHERE device_id=1').fetchone()
            self.assertEqual((recycle['operator'], recycle['operator_id']), ('管理员', 1))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operation_logs WHERE action='recycle'").fetchone()[0], 1)
        duplicate = self.client.post('/api/device-recycle', headers=self.headers(), json={
            'device_id': 1, 'destination': 'scrap', 'recycle_date': '2026-08-05', 'reason': '重复',
        })
        self.assertEqual(duplicate.status_code, 409, duplicate.json)
        self.assertEqual(self.client.put('/api/devices/1', headers=self.headers(), json={'site_id': 1}).status_code, 409)
        self.assertEqual(self.client.delete('/api/devices/1', headers=self.headers()).status_code, 409)
        listed = self.client.get('/api/devices', headers=self.headers()).json[0]
        self.assertEqual(listed['management_scope'], 'retired')
        self.assertEqual(listed['recycle_destination'], 'scrap')
        recycle_list = self.client.get('/api/device-recycle', headers=self.headers()).json
        self.assertEqual(recycle_list[0]['operator_name'], '管理员')

        # 旧库可能只有回收台账、没有统一操作日志，读取接口仍应补出审计事件。
        with app_module.get_db() as db:
            db.execute("DELETE FROM operation_logs WHERE action='recycle'")
        logs = self.client.get('/api/operation-logs?limit=50', headers=self.headers()).json
        recycle_log = next(item for item in logs if item['action'] == 'recycle')
        self.assertEqual(recycle_log['target_id'], 1)
        self.assertIn('DEV-001', recycle_log['details'])

    def test_offline_managed_device_is_not_treated_as_retired(self):
        app_module.migrate_workorder_flow_columns()
        updated = self.client.put('/api/devices/2', headers=self.headers(), json={'site_id': 1})
        self.assertEqual(updated.status_code, 200, updated.json)
        recycled = self.client.post('/api/device-recycle', headers=self.headers(), json={
            'device_id': 2, 'destination': 'repair', 'recycle_date': '2026-08-05',
            'reason': '离线排查后送修', 'operator': '管理员',
        })
        self.assertEqual(recycled.status_code, 200, recycled.json)
        deleted = self.client.delete('/api/devices/3', headers=self.headers())
        self.assertEqual(deleted.status_code, 200, deleted.json)

    def test_recycle_list_resolves_legacy_login_name_to_real_name(self):
        app_module.migrate_workorder_flow_columns()
        with app_module.get_db() as db:
            db.execute("""INSERT INTO device_recycle
                (device_id,device_code,device_name,device_type,site_id,site_name,recycle_date,
                 reason,destination,operator,status)
                VALUES (3,'DEV-003','待删除离线设备','analyzer',1,'测试站','2026-08-05',
                        '历史记录','scrap','admin','recycled')""")
        rows = self.client.get('/api/device-recycle', headers=self.headers()).json
        legacy = next(row for row in rows if row['device_id'] == 3)
        self.assertEqual(legacy['operator'], 'admin')
        self.assertEqual(legacy['operator_name'], '管理员')

    def test_management_scope_reconciliation_uses_recycle_ledger(self):
        with app_module.get_db() as db:
            db.execute("UPDATE device_shadows SET management_scope='invalid' WHERE id=1")
            db.execute("""INSERT INTO device_recycle
                (device_id,device_code,device_name,device_type,site_id,site_name,recycle_date,
                 reason,destination,operator,status)
                VALUES (2,'DEV-002','离线分析仪','analyzer',1,'测试站','2026-08-05',
                        '送修','repair','管理员','recycled')""")
            app_module._reconcile_device_management_scope(db)
            rows = db.execute("SELECT id,status,management_scope FROM device_shadows ORDER BY id").fetchall()
        self.assertEqual(rows[0]['management_scope'], 'managed')
        self.assertEqual((rows[1]['status'], rows[1]['management_scope']), ('offline', 'retired'))
        self.assertEqual(rows[2]['management_scope'], 'managed')

    def test_parts_list_exposes_owner_next_step_and_stock_issue(self):
        rows = self.client.get('/api/parts/requests', headers=self.headers()).json
        stock = next(row for row in rows if row['id'] == 1)
        self.assertEqual(stock['requester_name'], '现场人员')
        self.assertIn('现场领用', stock['next_action'])
        issued = self.client.post('/api/parts/requests/1/issue', headers=self.headers(), json={
            'items': [{'part_id': 1, 'quantity': 2}],
        })
        self.assertEqual(issued.status_code, 200, issued.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT quantity FROM spare_parts_inventory WHERE id=1').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT status FROM parts_requests WHERE id=1').fetchone()[0], 'issued')

    def test_vendor_order_and_arrival_generate_traceable_ledger(self):
        ordered = self.client.post('/api/parts/requests/2/order', headers=self.headers(), json={
            'supplier': '测试供应商', 'tracking_no': 'TRACK-001',
        })
        self.assertEqual(ordered.status_code, 200, ordered.json)
        fulfilled = self.client.post('/api/parts/requests/2/fulfill', headers=self.headers(), json={
            'actual_amount': 280, 'receipt_no': 'RCPT-001', 'destination': 'warehouse',
            'old_part_disposition': '无',
        })
        self.assertEqual(fulfilled.status_code, 200, fulfilled.json)
        ledger = self.client.get('/api/parts/requests/2/ledger', headers=self.headers()).json
        self.assertEqual(ledger['request']['status'], 'completed')
        self.assertEqual(ledger['request']['receipt_no'], 'RCPT-001')
        self.assertTrue(any(event['event_type'] == 'fulfilled' for event in ledger['events']))
        self.assertEqual(ledger['inventory_movements'][0]['type'], 'in')


if __name__ == '__main__':
    unittest.main()
