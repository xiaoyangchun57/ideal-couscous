import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class ProductFeedbackPf06Test(unittest.TestCase):
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
            'admin-token': {'id': 1, 'role': 'admin', 'real_name': '管理员'},
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': '运维甲'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT, status TEXT, phone TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, code TEXT, name TEXT, type TEXT,
                    gps_lat REAL, gps_lng REAL, district TEXT, address TEXT,
                    river TEXT, manager TEXT, phone TEXT, status TEXT
                );
                CREATE TABLE inspection_templates (id INTEGER PRIMARY KEY, template_name TEXT, category TEXT, frequency TEXT, description TEXT, status TEXT, sort_order INTEGER);
                CREATE TABLE inspection_template_items (id INTEGER PRIMARY KEY, template_id INTEGER, item_name TEXT, category TEXT, frequency_level TEXT, photo_required INTEGER, sort_order INTEGER);
                CREATE TABLE inspection_configs (id INTEGER PRIMARY KEY, site_type TEXT, device_types TEXT, template_id INTEGER, is_active INTEGER, remark TEXT);
                CREATE TABLE inspection_schedules (id INTEGER PRIMARY KEY, site_id INTEGER, template_id INTEGER, template_item_id INTEGER, status TEXT);
                CREATE TABLE insp_plan_items (id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, template_id INTEGER, item_name TEXT, category TEXT, frequency TEXT, required_photos INTEGER, need_review INTEGER, inspection_standard TEXT);
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, user_id INTEGER, status TEXT, period_start TEXT, period_end TEXT, created_at TEXT, tasks_generated INTEGER, plan_data TEXT, vehicle_days TEXT, spare_parts TEXT, work_order_ids TEXT);
                CREATE TABLE plan_schedule_events (id INTEGER PRIMARY KEY, schedule_id INTEGER, event_type TEXT);
                CREATE TABLE insp_plans (id INTEGER PRIMARY KEY, plan_schedule_id INTEGER);
                CREATE TABLE plan_resource_reservations (id INTEGER PRIMARY KEY, schedule_id INTEGER);
                CREATE TABLE plan_departure_confirmations (id INTEGER PRIMARY KEY, schedule_id INTEGER);
                CREATE TABLE vehicle_applications (id INTEGER PRIMARY KEY, reason TEXT);
                CREATE TABLE timeline_events (id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT);
                CREATE TABLE work_orders (id INTEGER PRIMARY KEY, order_no TEXT, title TEXT, status TEXT, source TEXT, created_at TEXT, site_id INTEGER, check_in_time TEXT, related_alert_id INTEGER, images TEXT);
                CREATE TABLE operation_attachments (id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER);
                CREATE TABLE inspection_checkins (id INTEGER PRIMARY KEY, site_id INTEGER, plan_id INTEGER);
                CREATE TABLE notifications (id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER);
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, related_order_no TEXT);
                CREATE TABLE hotline_events (id INTEGER PRIMARY KEY, related_order_no TEXT);
                CREATE TABLE parts_requests (id INTEGER PRIMARY KEY, work_order_no TEXT);
                CREATE TABLE spare_part_requests (id INTEGER PRIMARY KEY, work_order_no TEXT);
                CREATE TABLE device_recycle (id INTEGER PRIMARY KEY, work_order_no TEXT);
                INSERT INTO users VALUES (1, '管理员', 'admin', 'active', '');
                INSERT INTO users VALUES (2, '运维甲', 'operator', 'active', '');
                INSERT INTO users VALUES (3, '审核员', 'reviewer', 'active', '');
                INSERT INTO users VALUES (4, '角色缺失人员', '', 'active', '');
                INSERT INTO user_roles VALUES (1, 'admin');
                INSERT INTO user_roles VALUES (2, 'operator');
                INSERT INTO user_roles VALUES (3, 'reviewer');
            ''')
            db.execute("ALTER TABLE inspection_template_items ADD COLUMN need_review INTEGER DEFAULT 0")
            db.execute("ALTER TABLE inspection_template_items ADD COLUMN max_photos INTEGER DEFAULT 0")
            db.execute("ALTER TABLE inspection_template_items ADD COLUMN inspection_standard TEXT DEFAULT ''")
            db.execute('''CREATE TABLE device_shadows (
                id INTEGER PRIMARY KEY, site_id INTEGER, device_type TEXT, status TEXT
            )''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_path)

    @staticmethod
    def headers():
        return {'Authorization': 'Bearer admin-token'}

    def _insert_template(self, template_id=1, item_id=11):
        with app_module.get_db() as db:
            db.execute("INSERT INTO inspection_templates VALUES (?,?,?,?,?,?,?)",
                       (template_id, '水质模板', '水质', 'monthly', '', 'active', 1))
            db.execute("INSERT INTO inspection_template_items (id,template_id,item_name,category,frequency_level,photo_required,sort_order) VALUES (?,?,?,?,?,?,?)",
                       (item_id, template_id, '浊度', '水质', 'mid', 1, 1))

    def test_template_config_and_item_history_are_delete_protected(self):
        self._insert_template()
        with app_module.get_db() as db:
            db.execute("INSERT INTO inspection_configs VALUES (?,?,?,?,?,?)", (21, 'water_quality', '[]', 1, 1, ''))
            db.execute("INSERT INTO inspection_schedules VALUES (?,?,?,?,?)", (31, 1, 1, 11, 'active'))
            db.execute("INSERT INTO insp_plan_items (id,plan_id,site_id,template_id,item_name) VALUES (?,?,?,?,?)",
                       (41, 1, 1, 1, '浊度'))

        for path in ('/api/inspection-v2/templates/1',
                     '/api/inspection-v2/configs/21',
                     '/api/inspection-v2/templates/1/items/11'):
            response = self.client.delete(path, headers=self.headers())
            self.assertEqual(response.status_code, 409, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_templates').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_configs').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_template_items').fetchone()[0], 1)

    def test_config_template_reference_is_validated_before_write(self):
        response = self.client.post('/api/inspection-v2/configs', headers=self.headers(), json={
            'site_type': 'water_quality', 'template_id': 999,
        })
        self.assertEqual(response.status_code, 404, response.json)
        self.assertEqual(response.json['code'], 'INSPECTION_TEMPLATE_NOT_FOUND')
        self._insert_template()
        created = self.client.post('/api/inspection-v2/configs', headers=self.headers(), json={
            'site_type': 'water_quality', 'template_id': 1, 'is_active': False,
        })
        self.assertEqual(created.status_code, 200, created.json)
        config_id = created.json['id']
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT is_active FROM inspection_configs WHERE id=?', (config_id,)).fetchone()[0], 0)
        response = self.client.put(f'/api/inspection-v2/configs/{config_id}', headers=self.headers(), json={'template_id': 999})
        self.assertEqual(response.status_code, 404, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT template_id FROM inspection_configs WHERE id=?', (config_id,)).fetchone()[0], 1)

    def test_item_sort_order_update_rejects_invalid_value_without_write(self):
        self._insert_template()
        response = self.client.put('/api/inspection-v2/templates/1/items/11', headers=self.headers(), json={'sort_order': 'bad'})
        self.assertEqual(response.status_code, 400, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT sort_order FROM inspection_template_items WHERE id=11').fetchone()[0], 1)

    def test_cleanup_keeps_saved_drafts_and_rechecks_changed_batch_without_write(self):
        with app_module.get_db() as db:
            for sid in (1, 2):
                db.execute("INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (sid, 1, 'draft', '2026-08-01', '2026-08-07', '2026-08-01', 0,
                            '{\"2026-08-01\":{\"sites\":[10]}}', '{}', '[]', '[]'))
            db.execute("INSERT INTO plan_schedule_events VALUES (?,?,?)", (101, 1, 'created'))
            candidates = app_module._cleanup_candidates(db)
            self.assertEqual([(x['kind'], x['id']) for x in candidates], [('plan_schedule', 1), ('plan_schedule', 2)])
            db.execute("INSERT INTO plan_resource_reservations VALUES (?,?)", (102, 2))
        response = self.client.post('/api/admin/data-cleanup/apply', headers=self.headers(), json={
            'items': [
                {'kind': 'plan_schedule', 'id': 1},
                {'kind': 'plan_schedule', 'id': 2},
            ],
        })
        self.assertEqual(response.status_code, 409, response.json)
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM plan_schedules WHERE id=1').fetchone())
            self.assertIsNotNone(db.execute('SELECT 1 FROM plan_schedules WHERE id=2').fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_resource_reservations WHERE schedule_id=2').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE source_type='data_cleanup'").fetchone()[0], 0)

    def test_cleanup_allows_unmarked_workorder_without_business_facts(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO work_orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (70, 'WO-NO-SOURCE', '无来源历史工单', 'pending', '', '2026-08-01', None, '', 0, ''))
            candidates = app_module._cleanup_candidates(db)
        candidate = next(item for item in candidates if item['kind'] == 'workorder' and item['id'] == 70)
        self.assertEqual(candidate['activity_facts'], {
            'locations': 0,
            'checkins': 0,
            'photos': 0,
            'reviews': 0,
            'attachments': 0,
            'notifications': 0,
            'execution_records': 0,
            'resource_records': 0,
        })

    def test_match_uses_frequency_active_devices_and_site_scope(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO sites (id,code,name,type,status) VALUES (?,?,?,?,?)", (10, 'S10', '匹配站点', 'water_quality', 'normal'))
            db.execute("INSERT INTO sites (id,code,name,type,status) VALUES (?,?,?,?,?)", (11, 'S11', '未授权站点', 'water_quality', 'normal'))
            db.execute("INSERT INTO user_sites VALUES (?,?)", (2, 10))
            db.execute("INSERT INTO inspection_templates VALUES (?,?,?,?,?,?,?)", (1, '周检模板', '水质', 'weekly', '', 'active', 1))
            db.execute("INSERT INTO inspection_templates VALUES (?,?,?,?,?,?,?)", (2, '月检模板', '水质', 'monthly', '', 'active', 2))
            db.execute("INSERT INTO inspection_template_items (id,template_id,item_name,category,frequency_level,photo_required,sort_order) VALUES (?,?,?,?,?,?,?)", (11, 1, '周检项', '水质', 'mid', 0, 1))
            db.execute("INSERT INTO inspection_template_items (id,template_id,item_name,category,frequency_level,photo_required,sort_order) VALUES (?,?,?,?,?,?,?)", (12, 2, '月检项', '水质', 'mid', 0, 1))
            db.execute("INSERT INTO inspection_configs VALUES (?,?,?,?,?,?)", (21, 'water_quality', '[\"pump\"]', 1, 1, ''))
            db.execute("INSERT INTO inspection_configs VALUES (?,?,?,?,?,?)", (22, 'water_quality', '[\"pump\"]', 2, 1, ''))
            db.execute("INSERT INTO device_shadows VALUES (?,?,?,?)", (1, 10, 'pump', 'online'))
        weekly = self.client.get('/api/inspection-v2/configs/match?site_id=10&schedule_type=weekly', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(weekly.status_code, 200, weekly.json)
        self.assertEqual([item['id'] for item in weekly.json['items']], [11])
        monthly = self.client.get('/api/inspection-v2/configs/match?site_id=10&schedule_type=monthly', headers=self.headers())
        self.assertEqual([item['id'] for item in monthly.json['items']], [12])
        with app_module.get_db() as db:
            db.execute("UPDATE device_shadows SET status='retired' WHERE id=1")
        retired = self.client.get('/api/inspection-v2/configs/match?site_id=10&schedule_type=weekly', headers=self.headers())
        self.assertEqual(retired.json['items'], [])
        forbidden = self.client.get('/api/inspection-v2/configs/match?site_id=11&schedule_type=weekly', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(forbidden.status_code, 403, forbidden.json)

    def test_cleanup_excludes_legacy_workorder_relations_and_images(self):
        order_numbers = ['WO-CLEAN', 'WO-ALERT', 'WO-HOTLINE', 'WO-PARTS', 'WO-SPARE', 'WO-RECYCLE', 'WO-IMAGE', 'WO-BAD-IMAGE']
        with app_module.get_db() as db:
            for index, order_no in enumerate(order_numbers, 1):
                db.execute(
                    "INSERT INTO work_orders (id,order_no,title,status,source,created_at,site_id,check_in_time,related_alert_id,images) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (index, order_no, order_no, 'pending', 'auto', '2026-08-01', None, '', 0, ''),
                )
            db.execute("INSERT INTO alerts VALUES (?,?)", (1, 'WO-ALERT'))
            db.execute("INSERT INTO hotline_events VALUES (?,?)", (1, 'WO-HOTLINE'))
            db.execute("INSERT INTO parts_requests VALUES (?,?)", (1, 'WO-PARTS'))
            db.execute("INSERT INTO spare_part_requests VALUES (?,?)", (1, 'WO-SPARE'))
            db.execute("INSERT INTO device_recycle VALUES (?,?)", (1, 'WO-RECYCLE'))
            db.execute("UPDATE work_orders SET images=? WHERE order_no='WO-IMAGE'", ('["/uploads/legacy.jpg"]',))
            db.execute("UPDATE work_orders SET images=? WHERE order_no='WO-BAD-IMAGE'", ('legacy-not-json',))
            candidates = app_module._cleanup_candidates(db)
        self.assertEqual([(item['kind'], item['order_no']) for item in candidates], [('workorder', 'WO-CLEAN')])

    def test_cleanup_remains_compatible_when_legacy_relation_tables_are_absent(self):
        with app_module.get_db() as db:
            for table in ('alerts', 'hotline_events', 'parts_requests', 'spare_part_requests', 'device_recycle'):
                db.execute(f'DROP TABLE {table}')
            db.execute(
                "INSERT INTO work_orders (id,order_no,title,status,source,created_at,site_id,check_in_time,related_alert_id,images) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (99, 'WO-LEGACY', '旧表兼容', 'pending', 'auto', '2026-08-01', None, '', 0, ''),
            )
            candidates = app_module._cleanup_candidates(db)
        self.assertEqual([(item['kind'], item['order_no']) for item in candidates], [('workorder', 'WO-LEGACY')])

    def test_cleanup_excludes_structured_workorder_plan_vehicle_and_inventory_links(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE vehicle_applications ADD COLUMN work_order_no TEXT DEFAULT ''")
            db.execute("CREATE TABLE inventory_logs (id INTEGER PRIMARY KEY, work_order_no TEXT)")
            for order_id, order_no in enumerate(('WO-CLEAN', 'WO-PLAN', 'WO-VEHICLE', 'WO-INVENTORY'), 1):
                db.execute(
                    "INSERT INTO work_orders (id,order_no,title,status,source,created_at,site_id,check_in_time,related_alert_id,images) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (order_id, order_no, order_no, 'pending', '', '2026-08-01', None, '', 0, ''),
                )
            db.execute("INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                71, 1, 'draft', '2026-08-01', '2026-08-07', '2026-08-01', 0,
                '{}', '{}', '[]', '[2]'))
            db.execute("INSERT INTO vehicle_applications (id,reason,work_order_no) VALUES (?,?,?)",
                       (81, '', 'WO-VEHICLE'))
            db.execute("INSERT INTO inventory_logs VALUES (?,?)", (91, 'WO-INVENTORY'))
            candidates = app_module._cleanup_candidates(db)
        self.assertEqual(
            [item['order_no'] for item in candidates if item['kind'] == 'workorder'],
            ['WO-CLEAN'],
        )

        with app_module.get_db() as db:
            db.execute("UPDATE plan_schedules SET work_order_ids='[1,2]' WHERE id=71")
        response = self.client.post('/api/admin/data-cleanup/apply', headers=self.headers(), json={
            'items': [{'kind': 'workorder', 'id': 1}],
        })
        self.assertEqual(response.status_code, 409, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE source_type='data_cleanup'").fetchone()[0], 0)

    def test_cleanup_notification_only_records_are_previewed_and_deleted_with_notifications(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO sites (id,code,name,type,status) VALUES (?,?,?,?,?)",
                       (10, 'S10', '十号站', 'water_quality', 'normal'))
            db.execute("INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                72, 2, 'rejected', '2026-08-01', '2026-08-07', '2026-08-01', 0,
                '{"2026-08-02":{"sites":[10]}}', '{}', '[]', '[]'))
            db.execute("INSERT INTO work_orders (id,order_no,title,status,source,created_at,site_id,check_in_time,related_alert_id,images) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (73, 'WO-NOTICE', '待确认泵房工单', 'pending', '', '2026-08-01', 10, '', 0, ''))
            db.execute("INSERT INTO notifications VALUES (?,?,?)", (1, 'plan_schedule', 72))
            db.execute("INSERT INTO notifications VALUES (?,?,?)", (2, 'workorder', 73))
            candidates = app_module._cleanup_candidates(db)
        plan = next(item for item in candidates if item['kind'] == 'plan_schedule' and item['id'] == 72)
        order = next(item for item in candidates if item['kind'] == 'workorder' and item['id'] == 73)
        self.assertEqual((plan['owner_name'], plan['period_start'], plan['period_end'], plan['status']),
                         ('运维甲', '2026-08-01', '2026-08-07', 'rejected'))
        self.assertEqual((order['order_no'], order['title'], order['site_name'], order['status']),
                         ('WO-NOTICE', '待确认泵房工单', '十号站', 'pending'))
        self.assertEqual((plan['activity_facts']['notifications'], order['activity_facts']['notifications']), (1, 1))
        response = self.client.post('/api/admin/data-cleanup/apply', headers=self.headers(), json={
            'items': [{'kind': 'plan_schedule', 'id': 72}, {'kind': 'workorder', 'id': 73}],
        })
        self.assertEqual(response.status_code, 200, response.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], 0)
            self.assertIsNone(db.execute('SELECT 1 FROM plan_schedules WHERE id=72').fetchone())
            self.assertIsNone(db.execute('SELECT 1 FROM work_orders WHERE id=73').fetchone())

    def test_cleanup_plan_vehicle_reason_does_not_prefix_match_other_schedule(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                1, 1, 'draft', '2026-08-01', '2026-08-07', '2026-08-01', 0,
                '{}', '{}', '[]', '[]'))
            db.execute("INSERT INTO vehicle_applications VALUES (?,?)", (10, '巡检计划#10用车（2026-08-01至2026-08-02）'))
            candidates = app_module._cleanup_candidates(db)
        self.assertIn(1, [item['id'] for item in candidates if item['kind'] == 'plan_schedule'])

    def test_cleanup_rejects_duplicate_selection_without_audit_write(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (77, 1, 'draft', '2026-08-01', '2026-08-07', '2026-08-01', 0, '{}', '{}', '[]', '[]'))
        response = self.client.post('/api/admin/data-cleanup/apply', headers=self.headers(), json={
            'items': [{'kind': 'plan_schedule', 'id': 77}, {'kind': 'plan_schedule', 'id': 77}],
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(response.json['code'], 'CLEANUP_SELECTION_DUPLICATE')
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM plan_schedules WHERE id=77').fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE source_type='data_cleanup' AND source_id=77").fetchone()[0], 0)

    def test_site_create_requires_operator_manager_and_assigns_scope(self):
        payload = {'code': 'SITE-PF06', 'name': '新增站点', 'manager_id': 2,
                   'gps_lat': 28.1, 'gps_lng': 115.1}
        response = self.client.post('/api/sites', headers=self.headers(), json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        site_id = response.json['site']['id']
        with app_module.get_db() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM user_sites WHERE user_id=2 AND site_id=?', (site_id,)).fetchone())
        rejected = self.client.post('/api/sites', headers=self.headers(), json={**payload, 'code': 'SITE-PF06-BAD', 'manager_id': 3})
        self.assertEqual(rejected.status_code, 409, rejected.json)
        missing_role = self.client.post('/api/sites', headers=self.headers(), json={
            **payload, 'code': 'SITE-PF06-NO-ROLE', 'manager_id': 4,
        })
        self.assertEqual(missing_role.status_code, 409, missing_role.json)
        with app_module.get_db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sites WHERE code='SITE-PF06-NO-ROLE'").fetchone())

    def test_site_template_download_requires_admin(self):
        anonymous = self.client.get('/api/sites/template')
        self.assertEqual(anonymous.status_code, 401, anonymous.json)
        operator = self.client.get(
            '/api/sites/template', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(operator.status_code, 403, operator.json)
        admin = self.client.get('/api/sites/template', headers=self.headers())
        self.assertEqual(admin.status_code, 200)
        self.assertTrue(admin.data.startswith(b'\xef\xbb\xbfcode,name,type'))
        self.assertIn('attachment; filename=site_import_template.csv',
                      admin.headers.get('Content-Disposition', ''))

    def test_plan_level_vehicle_expands_to_legacy_days(self):
        plan_data = {
            '2026-08-20': {'sites': [1]},
            '2026-08-21': {'sites': [2]},
        }
        expanded = app_module._ps_expand_plan_vehicle(
            plan_data, 7, {'2026-08-20': 8, '2026-08-21': 9},
        )
        self.assertEqual(expanded, {'2026-08-20': 7, '2026-08-21': 7})
        self.assertEqual(
            app_module._ps_expand_plan_vehicle(plan_data, None, {'2026-08-20': 8}),
            {'2026-08-20': 8, '2026-08-21': 8},
        )
        with self.assertRaises(app_module.PlanScheduleSiteScopeError) as multi:
            app_module._ps_expand_plan_vehicle(
                plan_data, None, {'2026-08-20': 8, '2026-08-21': 9})
        self.assertEqual(multi.exception.code, 'PLAN_MULTIPLE_VEHICLES_NOT_ALLOWED')
        with self.assertRaises(app_module.PlanScheduleSiteScopeError) as error:
            app_module._ps_expand_plan_vehicle({}, 7, [])
        self.assertEqual(error.exception.code, 'PLAN_RESOURCE_DATA_INVALID')

    def test_create_rejects_legacy_multiple_vehicles_without_write(self):
        response = self.client.post('/api/plan-schedules', headers=self.headers(), json={
            'period_start': '2026-08-20', 'period_end': '2026-08-21',
            'plan_data': {},
            'vehicle_days': {'2026-08-20': 7, '2026-08-21': 8},
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(response.json['code'], 'PLAN_MULTIPLE_VEHICLES_NOT_ALLOWED')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 0)

    def test_create_rejects_non_dict_vehicle_days_without_write(self):
        response = self.client.post('/api/plan-schedules', headers=self.headers(), json={
            'period_start': '2026-08-20', 'period_end': '2026-08-20',
            'plan_data': {}, 'vehicle_id': 7, 'vehicle_days': [7],
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(response.json['code'], 'PLAN_RESOURCE_DATA_INVALID')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 0)

    def test_update_rejects_non_dict_vehicle_days_without_write(self):
        with app_module.get_db() as db:
            for definition in (
                'version INTEGER DEFAULT 1', 'remarks TEXT DEFAULT \'\'',
                'coverage_exception_reason TEXT DEFAULT \'\'',
                'vehicle_exception_reason TEXT DEFAULT \'\'',
                'reject_reason TEXT DEFAULT \'\'',
            ):
                db.execute(f'ALTER TABLE plan_schedules ADD COLUMN {definition}')
            db.execute("INSERT INTO plan_schedules (id,user_id,status,period_start,period_end,plan_data,vehicle_days,spare_parts,work_order_ids,version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (88, 1, 'draft', '2026-08-20', '2026-08-20', '{}', '{}', '[]', '[]', 1))
        response = self.client.put('/api/plan-schedules/88', headers=self.headers(), json={
            'version': 1, 'vehicle_id': 7, 'vehicle_days': [],
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(response.json['code'], 'PLAN_RESOURCE_DATA_INVALID')
        with app_module.get_db() as db:
            row = db.execute('SELECT vehicle_days, version FROM plan_schedules WHERE id=88').fetchone()
        self.assertEqual(row['vehicle_days'], '{}')
        self.assertEqual(row['version'], 1)

    def test_legacy_schedule_schema_gains_vehicle_column_without_losing_vehicle_days(self):
        with app_module.get_db() as db:
            self.assertFalse(app_module._table_has_column(db, 'plan_schedules', 'vehicle_id'))
            self.assertTrue(app_module._ensure_plan_schedule_vehicle_column(db))
            self.assertTrue(app_module._table_has_column(db, 'plan_schedules', 'vehicle_id'))
            db.execute("INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (100, 1, 'draft', '2026-08-01', '2026-08-07', '2026-08-01', 0,
                        '{}', '{"2026-08-01": 8}', '[]', '[]', 8))
            row = db.execute('SELECT vehicle_id, vehicle_days FROM plan_schedules WHERE id=100').fetchone()
        self.assertEqual(row['vehicle_id'], 8)
        self.assertEqual(json.loads(row['vehicle_days']), {'2026-08-01': 8})

    def test_template_item_fields_and_site_item_selection_are_validated(self):
        self._insert_template()
        self.client.put('/api/inspection-v2/templates/1/items/11', headers=self.headers(), json={
            'need_review': True, 'max_photos': 2, 'inspection_standard': '现场读数清晰',
        })
        with app_module.get_db() as db:
            row = db.execute('SELECT need_review,max_photos,inspection_standard FROM inspection_template_items WHERE id=11').fetchone()
            self.assertEqual(tuple(row), (1, 2, '现场读数清晰'))
            db.execute("INSERT INTO sites (id,code,name,type,status) VALUES (?,?,?,?,?)",
                       (10, 'SITE-10', '设备站', 'water_quality', 'normal'))
            db.execute("INSERT INTO user_sites VALUES (?,?)", (2, 10))
            db.execute("INSERT INTO device_shadows VALUES (?,?,?,?)", (101, 10, 'pump', 'online'))
            db.execute("UPDATE inspection_configs SET device_types=? WHERE id=21", ('["pump"]',))
            db.execute("INSERT INTO inspection_configs VALUES (?,?,?,?,?,?)", (21, 'water_quality', '["pump"]', 1, 1, ''))
        plan_data = {'2026-08-20': {'sites': [10], 'inspection_items': {'10': [11]}}}
        with app_module.get_db() as db:
            normalized = app_module._ps_validate_item_selections(db, plan_data, 'monthly')
            self.assertEqual(normalized['2026-08-20']['inspection_items'], {'10': [11]})
            with self.assertRaises(app_module.PlanScheduleSiteScopeError) as error:
                app_module._ps_validate_item_selections(
                    db, {'2026-08-20': {'sites': [10], 'inspection_items': {'10': [999]}}}, 'monthly')
            self.assertEqual(error.exception.code, 'PLAN_INSPECTION_ITEM_SELECTION_INVALID')

    def test_validate_rejects_non_dict_vehicle_days_without_write(self):
        response = self.client.post('/api/plan-schedules/validate', headers=self.headers(), json={
            'period_start': '2026-08-20', 'period_end': '2026-08-20',
            'plan_data': {}, 'vehicle_id': 7, 'vehicle_days': [],
        })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(response.json['code'], 'PLAN_RESOURCE_DATA_INVALID')
        valid_shape = self.client.post('/api/plan-schedules/validate', headers=self.headers(), json={
            'period_start': '2026-08-20', 'period_end': '2026-08-20',
            'plan_data': {}, 'vehicle_days': {},
        })
        self.assertEqual(valid_shape.status_code, 200, valid_shape.json)
        self.assertIn('请至少安排一个巡检日期和站点', valid_shape.json['errors'])

    def test_submit_rejects_malformed_persisted_vehicle_days_without_write(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE plan_schedules ADD COLUMN version INTEGER DEFAULT 1")
            db.execute("INSERT INTO plan_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (89, 1, 'draft', '2026-08-20', '2026-08-20', '2026-08-01', 0,
                        '{}', '[]', '[]', '[]', 1))
        response = self.client.post('/api/plan-schedules/89/submit', headers=self.headers(), json={'version': 1})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'PLAN_RESOURCE_DATA_INVALID')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT status FROM plan_schedules WHERE id=89').fetchone()[0], 'draft')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_schedule_events WHERE schedule_id=89').fetchone()[0], 0)

    def test_task_generation_uses_device_filtered_selection_and_keeps_snapshot(self):
        self._insert_template()
        with app_module.get_db() as db:
            db.execute("INSERT INTO sites (id,code,name,type,status) VALUES (?,?,?,?,?)",
                       (10, 'SITE-10', '设备站', 'water_quality', 'normal'))
            db.execute("INSERT INTO device_shadows VALUES (?,?,?,?)", (101, 10, 'pump', 'online'))
            db.execute("INSERT INTO inspection_configs VALUES (?,?,?,?,?,?)",
                       (21, 'water_quality', '[\"pump\"]', 1, 1, ''))
            db.execute("INSERT INTO inspection_configs VALUES (?,?,?,?,?,?)",
                       (22, 'water_quality', '[\"pump\"]', 1, 1, '重复配置'))
            db.execute("UPDATE inspection_template_items SET need_review=1, inspection_standard='读数在合格范围内' WHERE id=11")
            created = app_module._ps_add_site_tasks(db, 501, 10, 'monthly', [11])
            self.assertEqual(created, 1)
            generated = db.execute(
                'SELECT item_name,required_photos,need_review,inspection_standard FROM insp_plan_items WHERE plan_id=501').fetchall()
            self.assertEqual([tuple(row) for row in generated], [('浊度', 1, 1, '读数在合格范围内')])
            db.execute("UPDATE inspection_template_items SET item_name='新名称', need_review=0, inspection_standard='新标准' WHERE id=11")
            db.execute("UPDATE device_shadows SET status='retired' WHERE id=101")
            self.assertEqual(tuple(db.execute(
                'SELECT item_name,need_review,inspection_standard FROM insp_plan_items WHERE plan_id=501').fetchone()),
                             ('浊度', 1, '读数在合格范围内'))
            normalized = app_module._ps_validate_item_selections(
                db, {'2026-08-21': {'sites': [10], 'inspection_items': {}}}, 'monthly')
            self.assertEqual(normalized['2026-08-21']['inspection_items'], {'10': []})
            self.assertEqual(app_module._ps_add_site_tasks(db, 502, 10, 'monthly', []), 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM insp_plan_items WHERE plan_id=502').fetchone()[0], 0)

    def test_cleanup_anomaly_close_excludes_any_field_fact_and_keeps_audit(self):
        with app_module.get_db() as db:
            for sql in (
                "ALTER TABLE plan_schedules ADD COLUMN reject_reason TEXT DEFAULT ''",
                "ALTER TABLE insp_plans ADD COLUMN status TEXT DEFAULT 'active'",
                "ALTER TABLE insp_plan_items ADD COLUMN result TEXT",
                "ALTER TABLE insp_plan_items ADD COLUMN execution_status TEXT DEFAULT 'active'",
                "ALTER TABLE insp_plan_items ADD COLUMN check_in_time TEXT",
                "ALTER TABLE insp_plan_items ADD COLUMN check_out_time TEXT",
                "ALTER TABLE insp_plan_items ADD COLUMN gps_lat REAL",
                "ALTER TABLE insp_plan_items ADD COLUMN gps_lng REAL",
                "ALTER TABLE insp_plan_items ADD COLUMN location_lat REAL",
                "ALTER TABLE insp_plan_items ADD COLUMN photo_urls TEXT",
                "ALTER TABLE insp_plan_items ADD COLUMN review_status INTEGER DEFAULT 0",
                "ALTER TABLE inspection_checkins ADD COLUMN user_id INTEGER",
                "ALTER TABLE inspection_checkins ADD COLUMN check_time TEXT",
                "ALTER TABLE plan_resource_reservations ADD COLUMN planned_quantity INTEGER DEFAULT 0",
                "ALTER TABLE plan_resource_reservations ADD COLUMN reserved_quantity INTEGER DEFAULT 0",
                "ALTER TABLE plan_resource_reservations ADD COLUMN issued_quantity INTEGER DEFAULT 0",
                "ALTER TABLE plan_resource_reservations ADD COLUMN used_quantity INTEGER DEFAULT 0",
                "ALTER TABLE plan_resource_reservations ADD COLUMN returned_quantity INTEGER DEFAULT 0",
                "ALTER TABLE plan_resource_reservations ADD COLUMN status TEXT DEFAULT 'planned'",
                "ALTER TABLE vehicle_applications ADD COLUMN status TEXT DEFAULT 'approved'",
            ):
                db.execute(sql)
            db.execute("CREATE TABLE vehicle_use_records (id INTEGER PRIMARY KEY, application_id INTEGER, status TEXT)")
            for sid in (90, 91, 92, 93, 94, 95, 96, 97, 98, 99):
                db.execute("""INSERT INTO plan_schedules
                    (id,user_id,status,period_start,period_end,created_at,tasks_generated,plan_data,vehicle_days,spare_parts,work_order_ids)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, 1, 'approved', '2026-07-01', '2026-07-02', '2026-07-01', 1,
                     '{}', '{}', '[]', '[]'))
                db.execute("INSERT INTO insp_plans (id,plan_schedule_id,status) VALUES (?,?,?)", (sid, sid, 'active'))
                db.execute("""INSERT INTO insp_plan_items
                    (id,plan_id,site_id,template_id,item_name,result,execution_status,check_in_time,location_lat)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (sid + 100, sid, sid, 1, 'item', None, 'active', None, None))
            db.execute("UPDATE insp_plan_items SET check_in_time='2026-07-01 09:00:00' WHERE id=191")
            # Attachment source_id is the item ID, deliberately different from its plan ID.
            db.execute("INSERT INTO operation_attachments VALUES (?,?,?)", (920, 'inspection', 192))
            db.execute("UPDATE insp_plan_items SET gps_lat=28.6, gps_lng=115.8 WHERE id=193")
            db.execute("INSERT INTO inspection_checkins (id,site_id,plan_id,user_id,check_time) VALUES (?,?,?,?,?)",
                       (1, 94, 94, 1, '2026-07-01 09:00:00'))
            db.execute("INSERT INTO inspection_checkins (id,site_id,plan_id,user_id,check_time) VALUES (?,?,?,?,?)",
                       (2, 97, 0, 1, '2026-07-01 10:00:00'))
            db.execute("UPDATE insp_plan_items SET photo_urls='[\"/uploads/field.jpg\"]' WHERE id=195")
            db.execute("UPDATE insp_plan_items SET review_status=2 WHERE id=196")
            db.execute("INSERT INTO plan_resource_reservations (id,schedule_id,planned_quantity,reserved_quantity,status) VALUES (?,?,?,?,?)",
                       (1, 90, 2, 2, 'planned'))
            db.execute("INSERT INTO vehicle_applications (id,reason,status) VALUES (?,?,?)",
                       (2, '巡检计划#90用车（2026-07-01至2026-07-02）', 'approved'))
            db.execute("INSERT INTO notifications VALUES (?,?,?)", (3, 'plan_schedule', 90))
            db.execute("INSERT INTO plan_resource_reservations (id,schedule_id,planned_quantity,issued_quantity,status) VALUES (?,?,?,?,?)",
                       (4, 98, 2, 1, 'issued'))
            db.execute("INSERT INTO vehicle_applications (id,reason,status) VALUES (?,?,?)",
                       (5, '巡检计划#99用车（2026-07-01至2026-07-02）', 'approved'))
            db.execute("INSERT INTO vehicle_use_records VALUES (?,?,?)", (6, 5, 'in_use'))
            candidates = app_module._cleanup_candidates(db)
        anomaly_ids = [item['id'] for item in candidates if item.get('cleanup_action') == 'anomaly_close']
        self.assertEqual(anomaly_ids, [90])
        safe = next(item for item in candidates if item['kind'] == 'plan_schedule' and item['id'] == 90)
        self.assertEqual(safe['activity_facts'], {
            'locations': 0,
            'checkins': 0,
            'photos': 0,
            'reviews': 0,
            'attachments': 0,
            'notifications': 1,
            'execution_records': 1,
            'resource_records': 2,
        })
        rejected = self.client.post('/api/admin/data-cleanup/apply', headers=self.headers(), json={
            'items': [
                {'kind': 'plan_schedule', 'id': 92},
                {'kind': 'plan_schedule', 'id': 94},
            ],
        })
        self.assertEqual(rejected.status_code, 409, rejected.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT status FROM plan_schedules WHERE id=92').fetchone()[0], 'approved')
            self.assertEqual(db.execute('SELECT status FROM plan_schedules WHERE id=94').fetchone()[0], 'approved')
            self.assertEqual(db.execute('SELECT status FROM insp_plans WHERE id=92').fetchone()[0], 'active')
            self.assertEqual(db.execute('SELECT status FROM insp_plans WHERE id=94').fetchone()[0], 'active')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE source_id IN (92,94)").fetchone()[0], 0)
        response = self.client.post('/api/admin/data-cleanup/apply', headers=self.headers(), json={
            'items': [{'kind': 'plan_schedule', 'id': 90}],
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['deleted'][0]['action'], 'anomaly_close')
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT status FROM plan_schedules WHERE id=90').fetchone()[0], 'archived')
            self.assertEqual(db.execute('SELECT status FROM insp_plans WHERE id=90').fetchone()[0], 'cancelled')
            self.assertEqual(db.execute('SELECT execution_status FROM insp_plan_items WHERE id=190').fetchone()[0], 'cancelled')
            resource = db.execute('SELECT planned_quantity,reserved_quantity,status FROM plan_resource_reservations WHERE id=1').fetchone()
            vehicle = db.execute('SELECT status FROM vehicle_applications WHERE id=2').fetchone()
            self.assertEqual(tuple(resource), (0, 0, 'released'))
            self.assertEqual(vehicle['status'], 'cancelled')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notifications WHERE source_type='plan_schedule' AND source_id=90").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM timeline_events WHERE source_type='plan_schedule' AND source_id=90 AND event_type='anomaly_closed'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
