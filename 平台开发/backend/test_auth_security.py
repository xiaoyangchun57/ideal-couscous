import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class AuthSecurityTest(unittest.TestCase):
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

        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE, login_name TEXT UNIQUE,
                    password_hash TEXT, role TEXT, real_name TEXT, phone TEXT, status TEXT,
                    auth_version INTEGER DEFAULT 1, must_change_password INTEGER DEFAULT 0
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT, PRIMARY KEY(user_id, role));
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER, PRIMARY KEY(user_id, site_id));
                CREATE TABLE auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT UNIQUE, user_id INTEGER,
                    auth_version INTEGER, issued_at TEXT, expires_at TEXT, revoked_at TEXT,
                    revoke_reason TEXT, last_seen_at TEXT
                );
                CREATE TABLE auth_login_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, ip_address TEXT,
                    success INTEGER DEFAULT 0, attempted_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE threshold_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'metric',
                    site_id INTEGER, metric TEXT, rule_type TEXT NOT NULL, conditions TEXT NOT NULL DEFAULT '{}',
                    severity TEXT DEFAULT 'warning', enabled INTEGER DEFAULT 1, created_by INTEGER,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE UNIQUE INDEX uq_threshold_rules_semantic ON threshold_rules(
                    lower(trim(name)), scope, ifnull(site_id,-1), ifnull(metric,''), rule_type, conditions, severity
                );
                CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plate_no TEXT UNIQUE, model TEXT, seats INTEGER,
                    vehicle_name TEXT, department TEXT, fuel_type TEXT, purchase_date TEXT,
                    insurance_expiry TEXT, annual_inspection_expiry TEXT, status TEXT DEFAULT 'idle'
                );
                CREATE TABLE vehicle_maintenance_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER,
                    evidence_expected_count INTEGER DEFAULT 0,
                    evidence_status TEXT DEFAULT 'not_required'
                );
                CREATE TABLE vehicle_refueling_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER,
                    evidence_expected_count INTEGER DEFAULT 0,
                    evidence_status TEXT DEFAULT 'not_required'
                );
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, code TEXT, type TEXT);
                CREATE TABLE device_shadows (
                    id INTEGER PRIMARY KEY, device_code TEXT, device_name TEXT, device_type TEXT,
                    device_model TEXT, manufacturer TEXT, install_date TEXT, management_scope TEXT,
                    monitoring_enabled INTEGER, last_data_time TEXT, site_id INTEGER
                );
                CREATE TABLE inventory_logs (id INTEGER PRIMARY KEY, ref_type TEXT, ref_id INTEGER, created_at TEXT);
                CREATE TABLE operation_logs (id INTEGER PRIMARY KEY, target_type TEXT, target_id INTEGER, created_at TEXT);
                CREATE TABLE data_sources (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT);
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, site_id INTEGER, filename TEXT,
                    source_type TEXT, source_id INTEGER, category TEXT,
                    is_deleted INTEGER DEFAULT 0
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, title TEXT, site_id INTEGER,
                    status TEXT DEFAULT 'pending', created_at TEXT
                );
                CREATE TABLE reagents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, manufacturer TEXT, spec TEXT,
                    unit TEXT, shelf_life_days INTEGER, image_url TEXT, created_at TEXT
                );
                CREATE TABLE reagent_inventory (id INTEGER PRIMARY KEY, reagent_id INTEGER);
                CREATE TABLE reagent_alerts (id INTEGER PRIMARY KEY, reagent_id INTEGER);
                CREATE TABLE reagent_usage (id INTEGER PRIMARY KEY, reagent_id INTEGER);
            ''')
            users = [
                (1, 'admin', '管理员', app_module._hash_pw('AdminPass123'), 'admin', '管理员', '', 'active', 1, 0),
                (2, 'operator', '运维甲', app_module._hash_pw('OperatorPass123'), 'operator', '运维甲', '', 'active', 1, 0),
                (3, 'unassigned', '未分站', app_module._hash_pw('UnassignedPass123'), 'operator', '未分站', '', 'active', 1, 0),
                (4, 'reviewer', '审核甲', app_module._hash_pw('ReviewerPass123'), 'reviewer', '审核甲', '', 'active', 1, 0),
            ]
            db.executemany('INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?)', users)
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [(1, 'admin'), (2, 'operator'), (3, 'operator'), (4, 'reviewer')])
            db.executemany('INSERT INTO sites VALUES (?,?,?,?)', [(1, '站点一', 'S1', 'water_quality'), (2, '站点二', 'S2', 'water_quality')])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(2, 1), (4, 1)])
            db.execute("INSERT INTO device_shadows VALUES (1,'D1','设备一','sensor','','','','managed',1,'',1)")
            db.execute("INSERT INTO data_sources VALUES (1,'正式数据源','2026-08-04 08:00:00')")
            db.executemany(
                'INSERT INTO operation_attachments (id,site_id,filename,is_deleted) VALUES (?,?,?,0)',
                [(1, 1, 'one.jpg'), (2, 2, 'two.jpg')],
            )

        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    def login(self, username, password):
        response = self.client.post('/api/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['token']

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def test_logout_revokes_server_session(self):
        token = self.login('operator', 'OperatorPass123')
        self.assertEqual(self.client.post('/api/auth/logout', headers=self.headers(token)).status_code, 200)
        response = self.client.get('/api/auth/me', headers=self.headers(token))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json['code'], 'SESSION_REVOKED')

    def test_global_search_and_workorder_list_follow_page_roles(self):
        admin = self.login('admin', 'AdminPass123')
        operator = self.login('operator', 'OperatorPass123')
        reviewer = self.login('reviewer', 'ReviewerPass123')
        with self.temporary_db() as db:
            db.execute(
                "INSERT INTO work_orders (id,order_no,title,site_id,created_at) VALUES (1,'WO-1','站点一维修',1,'2026-08-06 08:00:00')"
            )

        reviewer_workorders = self.client.get('/api/workorders', headers=self.headers(reviewer))
        self.assertEqual(reviewer_workorders.status_code, 403, reviewer_workorders.json)

        reviewer_sites = self.client.get('/api/global-search?q=站点一', headers=self.headers(reviewer))
        self.assertEqual([item['type'] for item in reviewer_sites.json['results']], ['site'])
        reviewer_device = self.client.get('/api/global-search?q=D1', headers=self.headers(reviewer))
        self.assertEqual(reviewer_device.json['results'], [])

        operator_order = self.client.get('/api/global-search?q=WO-1', headers=self.headers(operator))
        self.assertEqual([item['type'] for item in operator_order.json['results']], ['workorder'])
        admin_device = self.client.get('/api/global-search?q=D1', headers=self.headers(admin))
        self.assertEqual([item['type'] for item in admin_device.json['results']], ['device'])

    def test_assignees_only_include_active_operator_roles(self):
        admin_token = self.login('admin', 'AdminPass123')
        with self.temporary_db() as db:
            db.execute(
                "INSERT INTO users VALUES (5,'inactive-op','停用运维',?,'operator','停用运维','','inactive',1,0)",
                (app_module._hash_pw('InactivePass123'),),
            )
            db.execute(
                "INSERT INTO users VALUES (6,'dual-role','双角色人员',?,'admin','双角色人员','','active',1,0)",
                (app_module._hash_pw('DualRolePass123'),),
            )
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [(5, 'operator'), (6, 'admin'), (6, 'operator')])

        response = self.client.get('/api/assignees', headers=self.headers(admin_token))
        self.assertEqual(response.status_code, 200, response.json)
        names = {row['name'] for row in response.json}
        self.assertIn('运维甲', names)
        self.assertIn('未分站', names)
        self.assertIn('双角色人员', names)
        self.assertNotIn('管理员', names)
        self.assertNotIn('审核甲', names)
        self.assertNotIn('停用运维', names)

    def test_dual_role_session_and_reagent_master_permissions(self):
        with self.temporary_db() as db:
            db.execute(
                "INSERT INTO users VALUES (6,'dual-role','双角色人员',?,'admin','双角色人员','','active',1,0)",
                (app_module._hash_pw('DualRolePass123'),),
            )
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [(6, 'admin'), (6, 'operator')])

        dual_login = self.client.post('/api/auth/login', json={
            'username': 'dual-role', 'password': 'DualRolePass123',
        })
        self.assertEqual(dual_login.status_code, 200, dual_login.json)
        self.assertEqual(set(dual_login.json['user']['roles']), {'admin', 'operator'})
        token = dual_login.json['token']
        created = self.client.post('/api/reagents', headers=self.headers(token), json={
            'name': 'COD试剂', 'manufacturer': '厂家甲', 'spec': '100mL/套',
            'unit': '套', 'shelf_life_days': 365,
        })
        self.assertEqual(created.status_code, 201, created.json)
        listed = self.client.get('/api/reagents', headers=self.headers(token))
        self.assertEqual(listed.status_code, 200, listed.json)
        self.assertEqual(listed.json[0]['name'], 'COD试剂')

    def test_reagent_master_requires_login_and_rejects_invalid_or_duplicate_data(self):
        self.assertEqual(self.client.get('/api/reagents').status_code, 401)
        admin = self.login('admin', 'AdminPass123')
        reviewer = self.login('reviewer', 'ReviewerPass123')
        self.assertEqual(self.client.get('/api/reagents', headers=self.headers(reviewer)).status_code, 200)
        self.assertEqual(self.client.post('/api/reagents', headers=self.headers(reviewer), json={
            'name': '无权新增', 'unit': '瓶', 'shelf_life_days': 365,
        }).status_code, 403)

        invalid_cases = [
            ({'name': '缺单位', 'unit': '', 'shelf_life_days': 365}, 'REAGENT_UNIT_INVALID'),
            ({'name': '零保质期', 'unit': '瓶', 'shelf_life_days': 0}, 'REAGENT_SHELF_LIFE_INVALID'),
            ({'name': '超长保质期', 'unit': '瓶', 'shelf_life_days': 3651}, 'REAGENT_SHELF_LIFE_INVALID'),
            ({'name': '小数保质期', 'unit': '瓶', 'shelf_life_days': 1.5}, 'REAGENT_SHELF_LIFE_INVALID'),
        ]
        for payload, code in invalid_cases:
            response = self.client.post('/api/reagents', headers=self.headers(admin), json=payload)
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(response.json['code'], code)

        first = self.client.post('/api/reagents', headers=self.headers(admin), json={
            'name': 'pH校准液', 'unit': '瓶', 'shelf_life_days': 365,
        })
        duplicate = self.client.post('/api/reagents', headers=self.headers(admin), json={
            'name': 'PH校准液', 'unit': '瓶', 'shelf_life_days': 365,
        })
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(duplicate.status_code, 409, duplicate.json)
        self.assertEqual(duplicate.json['code'], 'DUPLICATE_REAGENT_NAME')

    def test_expired_session_is_rejected(self):
        token = self.login('operator', 'OperatorPass123')
        with self.temporary_db() as db:
            db.execute("UPDATE auth_sessions SET expires_at='2000-01-01 00:00:00'")
        response = self.client.get('/api/auth/me', headers=self.headers(token))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json['code'], 'SESSION_EXPIRED')

    def test_disable_account_revokes_existing_session(self):
        user_token = self.login('operator', 'OperatorPass123')
        admin_token = self.login('admin', 'AdminPass123')
        response = self.client.put('/api/users/2/status', headers=self.headers(admin_token), json={'status': 'inactive'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.client.get('/api/auth/me', headers=self.headers(user_token)).status_code, 401)

    def test_reset_requires_password_change_and_rotates_token(self):
        old_token = self.login('operator', 'OperatorPass123')
        admin_token = self.login('admin', 'AdminPass123')
        reset = self.client.put('/api/users/2/reset-password', headers=self.headers(admin_token), json={})
        self.assertEqual(reset.status_code, 200, reset.json)
        temporary_password = reset.json['temporary_password']
        self.assertEqual(len(temporary_password), 12)
        self.assertEqual(self.client.get('/api/auth/me', headers=self.headers(old_token)).status_code, 401)

        temporary_token = self.login('operator', temporary_password)
        blocked = self.client.get('/api/sites/1', headers=self.headers(temporary_token))
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json['code'], 'PASSWORD_CHANGE_REQUIRED')
        changed = self.client.post('/api/auth/change-password', headers=self.headers(temporary_token), json={
            'current_password': temporary_password,
            'new_password': 'OperatorNewPass456',
        })
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(self.client.get('/api/auth/me', headers=self.headers(temporary_token)).status_code, 401)
        self.assertEqual(self.client.get('/api/auth/me', headers=self.headers(changed.json['token'])).status_code, 200)

    def test_login_rate_limit_blocks_account_and_ip_after_repeated_failures(self):
        for _ in range(app_module.LOGIN_FAILURE_LIMIT):
            response = self.client.post('/api/auth/login', json={'username': 'operator', 'password': 'wrong'})
            self.assertEqual(response.status_code, 401)
        blocked = self.client.post('/api/auth/login', json={'username': 'operator', 'password': 'OperatorPass123'})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json['code'], 'LOGIN_RATE_LIMITED')
        self.assertGreaterEqual(blocked.json['retry_after'], 1)
        with self.temporary_db() as db:
            db.execute("UPDATE auth_login_attempts SET attempted_at=datetime('now','-1 day')")
        recovered = self.client.post('/api/auth/login', json={'username': 'operator', 'password': 'OperatorPass123'})
        self.assertEqual(recovered.status_code, 200)

    def test_duplicate_threshold_rule_is_rejected(self):
        token = self.login('admin', 'AdminPass123')
        payload = {
            'name': '重复规则', 'scope': 'metric', 'metric': 'ph', 'rule_type': 'static',
            'severity': 'warning', 'conditions': {'max': 9, 'min': 6},
        }
        first = self.client.post('/api/threshold-rules', headers=self.headers(token), json=payload)
        second = self.client.post('/api/threshold-rules', headers=self.headers(token), json={
            **payload, 'conditions': {'min': 6.0, 'max': 9.0},
        })
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(second.status_code, 409, second.json)
        self.assertEqual(second.json['code'], 'DUPLICATE_THRESHOLD_RULE')
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM threshold_rules').fetchone()[0], 1)

    def test_duplicate_vehicle_plate_returns_business_conflict(self):
        token = self.login('admin', 'AdminPass123')
        payload = {'plate_no': '赣A12345', 'model': '测试车', 'seats': 5}
        first = self.client.post('/api/vehicles', headers=self.headers(token), json=payload)
        second = self.client.post('/api/vehicles', headers=self.headers(token), json=payload)
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(second.status_code, 409, second.json)
        self.assertEqual(second.json['code'], 'DUPLICATE_PLATE_NO')

    def test_vehicle_evidence_status_tracks_partial_uploads_and_persists(self):
        token = self.login('admin', 'AdminPass123')
        with self.temporary_db() as db:
            cursor = db.execute(
                "INSERT INTO vehicle_maintenance_records "
                "(vehicle_id,evidence_expected_count,evidence_status) VALUES (1,2,'pending')"
            )
            record_id = cursor.lastrowid

        pending = self.client.put(
            f'/api/vehicle/maintenance/{record_id}/evidence-status',
            headers=self.headers(token),
        )
        self.assertEqual(pending.status_code, 200, pending.json)
        self.assertEqual(pending.json['evidence_status'], 'pending')
        self.assertEqual(pending.json['uploaded'], 0)

        with self.temporary_db() as db:
            db.execute(
                "INSERT INTO operation_attachments "
                "(id,filename,source_type,source_id,category,is_deleted) "
                "VALUES (3,'first.jpg','vehicle',?,'养护记录',0)",
                (record_id,),
            )
        partial = self.client.put(
            f'/api/vehicle/maintenance/{record_id}/evidence-status',
            headers=self.headers(token),
        )
        self.assertEqual(partial.status_code, 200, partial.json)
        self.assertEqual(partial.json['evidence_status'], 'partial')
        self.assertEqual(partial.json['uploaded'], 1)

        with self.temporary_db() as db:
            db.execute(
                "INSERT INTO operation_attachments "
                "(id,filename,source_type,source_id,category,is_deleted) "
                "VALUES (4,'second.jpg','vehicle',?,'养护记录',0)",
                (record_id,),
            )
        complete = self.client.put(
            f'/api/vehicle/maintenance/{record_id}/evidence-status',
            headers=self.headers(token),
        )
        self.assertEqual(complete.status_code, 200, complete.json)
        self.assertEqual(complete.json['evidence_status'], 'complete')
        self.assertEqual(complete.json['uploaded'], 2)
        with self.temporary_db() as db:
            status = db.execute(
                'SELECT evidence_status FROM vehicle_maintenance_records WHERE id=?',
                (record_id,),
            ).fetchone()[0]
        self.assertEqual(status, 'complete')

    def test_role_and_site_matrix_blocks_direct_api_access(self):
        reviewer = self.login('reviewer', 'ReviewerPass123')
        unassigned = self.login('unassigned', 'UnassignedPass123')
        operator = self.login('operator', 'OperatorPass123')
        admin = self.login('admin', 'AdminPass123')

        self.assertEqual(self.client.post('/api/workorders', headers=self.headers(reviewer), json={'site_id': 1}).status_code, 403)
        self.assertEqual(self.client.post('/api/threshold-rules', headers=self.headers(reviewer), json={}).status_code, 403)
        self.assertEqual(self.client.put('/api/alert-escalation-config', headers=self.headers(operator), json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/rule-templates/water_quality_standard/apply', headers=self.headers(reviewer), json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/sites/import', headers=self.headers(operator)).status_code, 403)
        self.assertEqual(self.client.get('/api/sites/data-sources', headers=self.headers(reviewer)).status_code, 403)
        self.assertEqual(self.client.post('/api/sites/data-sources', headers=self.headers(operator), json={}).status_code, 403)
        self.assertEqual(self.client.delete('/api/sites/data-sources/1', headers=self.headers(reviewer)).status_code, 403)
        self.assertEqual(self.client.get('/api/devices', headers=self.headers(operator)).status_code, 403)
        self.assertEqual(self.client.get('/api/vehicles', headers=self.headers(operator)).status_code, 200)
        self.assertEqual(self.client.get('/api/sites', headers=self.headers(unassigned)).json, [])
        self.assertEqual(self.client.get('/api/sites/1', headers=self.headers(unassigned)).status_code, 403)
        self.assertEqual(self.client.get('/api/sites/2/archive', headers=self.headers(operator)).status_code, 403)
        self.assertEqual(self.client.get('/api/attachments/2', headers=self.headers(operator)).status_code, 403)
        self.assertEqual(self.client.put('/api/attachments/1', headers=self.headers(operator), json={}).status_code, 403)
        self.assertEqual(self.client.delete('/api/attachments/1', headers=self.headers(operator)).status_code, 403)
        self.assertEqual(self.client.post('/api/attachments/1/archive', headers=self.headers(operator), json={'archived_by': '伪造人员'}).status_code, 403)
        self.assertEqual(self.client.post('/api/attachments/1/unarchive', headers=self.headers(reviewer)).status_code, 403)
        self.assertEqual(self.client.get('/api/devices', headers=self.headers(admin)).status_code, 200)
        self.assertEqual(self.client.get('/api/sites/data-sources', headers=self.headers(admin)).status_code, 200)


if __name__ == '__main__':
    unittest.main()
