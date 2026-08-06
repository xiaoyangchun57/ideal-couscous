import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class B0BTruthAndClosureTest(unittest.TestCase):
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
            finally:
                db.close()

        self.temporary_db = temporary_db
        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'admin-token': {'id': 1, 'role': 'admin', 'roles': ['admin'], 'real_name': '管理员'},
            'reviewer-token': {'id': 2, 'role': 'reviewer', 'roles': ['reviewer'], 'real_name': '审核员'},
            'other-reviewer-token': {'id': 3, 'role': 'reviewer', 'roles': ['reviewer'], 'real_name': '外站审核员'},
        })

        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, real_name TEXT, role TEXT, openid TEXT DEFAULT '');
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT, type TEXT,
                    gps_lat REAL, gps_lng REAL, district TEXT, river TEXT, manager TEXT, phone TEXT
                );
                CREATE TABLE sensor_data (site_id INTEGER, metric TEXT, value REAL, recorded_at TEXT);
                CREATE TABLE device_shadows (
                    id INTEGER PRIMARY KEY, site_id INTEGER, device_name TEXT, device_type TEXT,
                    install_date TEXT, status TEXT
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, message TEXT, level TEXT, status TEXT,
                    created_at TEXT, metric TEXT, resolved_at TEXT, resolve_reason TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, site_id INTEGER, event_type TEXT,
                    title TEXT, description TEXT, status TEXT, images TEXT, assignee TEXT,
                    created_at TEXT, related_alert_id INTEGER, used_parts TEXT, resolved_at TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_name TEXT, period TEXT, status TEXT,
                    created_at TEXT, generate_date TEXT
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, review_status INTEGER DEFAULT 0
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, stored_path TEXT,
                    file_type TEXT, source_type TEXT, source_id INTEGER, site_id INTEGER,
                    extra_json TEXT, created_at TEXT, is_deleted INTEGER DEFAULT 0,
                    review_status TEXT, reviewer_id INTEGER, reviewed_at TEXT, reject_reason TEXT,
                    review_required INTEGER DEFAULT 0, is_flagged INTEGER DEFAULT 0,
                    flag_reason TEXT DEFAULT '', taken_at TEXT, duplicate_of_id INTEGER
                );
                CREATE TABLE reagent_records (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_name TEXT, reagent_type TEXT,
                    usage_date TEXT, replacement_date TEXT, operator TEXT, notes TEXT, created_at TEXT
                );
                CREATE TABLE timeline_events (source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT);
                CREATE TABLE notifications (user_id INTEGER, source_type TEXT, source_id TEXT, title TEXT, content TEXT);
                CREATE TABLE hotline_events (related_order_no TEXT, status TEXT);
                CREATE TABLE manual_reports (id INTEGER PRIMARY KEY, order_no TEXT, status TEXT, resolved_at TEXT);
                CREATE TABLE data_reviews (site_id INTEGER, metric TEXT, status TEXT, archived_at TEXT, archive_reason TEXT, related_order_no TEXT);
                CREATE TABLE spare_parts_inventory (id INTEGER PRIMARY KEY, quantity REAL, updated_at TEXT);
                CREATE TABLE inventory_logs (part_id INTEGER, type TEXT, quantity REAL, ref_type TEXT, ref_id INTEGER, operator TEXT, remark TEXT);
                CREATE TABLE data_sources (
                    id INTEGER PRIMARY KEY, name TEXT, source_type TEXT, protocol TEXT, url TEXT,
                    auth_type TEXT, auth_config TEXT, sync_interval INTEGER, status TEXT,
                    last_sync TEXT, remark TEXT, created_at TEXT
                );
                CREATE TABLE site_import_batches (
                    batch_id TEXT PRIMARY KEY, filename TEXT, total_rows INTEGER,
                    imported_count INTEGER, created_by INTEGER, created_at TEXT
                );
                CREATE TABLE site_import_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, row_number INTEGER,
                    site_id INTEGER, site_code TEXT, status TEXT, message TEXT, created_at TEXT
                );
                CREATE TABLE vehicles (id INTEGER PRIMARY KEY, plate_no TEXT, model TEXT, status TEXT);
                CREATE TABLE vehicle_documents (vehicle_id INTEGER, document_type TEXT, valid_until TEXT);
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER,
                    start_at TEXT, end_at TEXT, destination TEXT, reason TEXT, status TEXT,
                    approver_id INTEGER, approved_at TEXT, reject_reason TEXT, created_at TEXT,
                    site_id INTEGER, work_order_no TEXT
                );

                INSERT INTO users VALUES (1, 'admin', '管理员', 'admin', '');
                INSERT INTO users VALUES (2, 'reviewer', '审核员', 'reviewer', '');
                INSERT INTO users VALUES (3, 'other-reviewer', '外站审核员', 'reviewer', '');
                INSERT INTO users VALUES (4, 'operator', '现场运维', 'operator', '');
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO user_sites VALUES (3, 2);
                INSERT INTO sites (id,code,name,type,gps_lat,gps_lng,district,river,manager,phone)
                    VALUES (1,'WQ001','测试水质站','water_quality',28.68,115.89,'南昌市','赣江','现场运维','');
                INSERT INTO sites (id,code,name,type,gps_lat,gps_lng,district,river,manager,phone)
                    VALUES (2,'WQ002','外站','water_quality',28.7,115.9,'南昌市','赣江','','');
                INSERT INTO work_orders VALUES
                    (1,'WO-B0B-001',1,'设备故障','泵故障','现场已处理','reviewing','[]','现场运维','2026-08-04 08:00:00',NULL,'',NULL);
                INSERT INTO work_orders VALUES
                    (2,'WO-B0B-002',2,'设备故障','外站故障','现场已处理','reviewing','[]','','2026-08-04 08:00:00',NULL,'',NULL);
                INSERT INTO operation_attachments
                    (filename,stored_path,file_type,source_type,source_id,site_id,created_at,taken_at,is_deleted,review_status)
                    VALUES ('work.jpg','/uploads/work.jpg','image','workorder',1,1,'2026-08-04 09:00:00','2026-08-04 08:55:00',0,'pending');
                INSERT INTO operation_attachments
                    (filename,stored_path,file_type,source_type,source_id,site_id,created_at,taken_at,is_deleted,review_status)
                    VALUES ('other.jpg','/uploads/other.jpg','image','workorder',2,2,'2026-08-04 09:00:00','2026-08-04 08:55:00',0,'pending');
                INSERT INTO data_sources VALUES
                    (1,'历史配置','api','HTTP','https://invalid.example','token','{"token":"secret"}',60,'active','2026-08-04 08:00:00','', '2026-08-04 08:00:00');
                INSERT INTO vehicles VALUES (1,'赣A测试','皮卡','idle');
                INSERT INTO vehicle_applications VALUES
                    (1,1,4,'2026-08-05 08:00:00','2026-08-05 18:00:00','测试站','巡检','pending',NULL,NULL,'','2026-08-04 08:00:00',1,'');
            ''')
            db.commit()

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

    def post_csv(self, content, filename='sites.csv'):
        return self.client.post(
            '/api/sites/import',
            headers=self.headers('admin-token'),
            data={'file': (io.BytesIO(content.encode('utf-8')), filename)},
            content_type='multipart/form-data',
        )

    def test_archive_never_generates_calibration_evidence(self):
        response = self.client.get('/api/sites/1/archive', headers=self.headers('reviewer-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['calibration_reports'], [])
        self.assertNotIn('合格', json.dumps(response.json, ensure_ascii=False))

        with self.temporary_db() as db:
            db.execute('''INSERT INTO operation_attachments
                (filename,stored_path,file_type,source_type,source_id,site_id,extra_json,created_at,is_deleted)
                VALUES (?,?,?,?,?,?,?,?,0)''', (
                '真实校准.pdf', '/uploads/real-calibration.pdf', 'file', 'calibration', 1, 1,
                json.dumps({'cal_type': '年度校准', 'result': '待审核', 'valid_until': ''}, ensure_ascii=False),
                '2026-08-04 10:00:00',
            ))
            db.commit()
        real = self.client.get('/api/sites/1/archive', headers=self.headers('reviewer-token'))
        self.assertEqual(real.status_code, 200, real.json)
        self.assertEqual(len(real.json['calibration_reports']), 1)
        self.assertEqual(real.json['calibration_reports'][0]['file']['name'], '真实校准.pdf')

    def test_site_import_is_atomic_and_uses_gps_columns(self):
        mixed = self.post_csv(
            'code,name,type,lat,lng\n'
            'WQ003,新站,water_quality,28.5,115.5\n'
            'WQ004,错误类型,rainfall,28.5,115.5\n'
        )
        self.assertEqual(mixed.status_code, 422, mixed.json)
        self.assertTrue(mixed.json['rolled_back'])
        with self.temporary_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sites WHERE code='WQ003'").fetchone()[0], 0)

        valid = self.post_csv(
            'code,name,type,lat,lng,district,river,manager,phone\n'
            'WQ003,新站,water_quality,28.55,115.66,南昌市,赣江,张工,13800138000\n'
        )
        self.assertEqual(valid.status_code, 200, valid.json)
        self.assertTrue(valid.json['success'])
        self.assertTrue(valid.json['batch_id'].startswith('SITEIMP-'))
        with self.temporary_db() as db:
            site = db.execute("SELECT gps_lat,gps_lng FROM sites WHERE code='WQ003'").fetchone()
            self.assertEqual((site['gps_lat'], site['gps_lng']), (28.55, 115.66))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM site_import_rows').fetchone()[0], 1)

    def test_data_source_cannot_report_fake_success(self):
        listed = self.client.get('/api/sites/data-sources', headers=self.headers('admin-token'))
        self.assertEqual(listed.status_code, 200, listed.json)
        self.assertEqual(listed.json[0]['status'], 'unavailable')
        self.assertNotIn('auth_config', listed.json[0])
        created = self.client.post('/api/sites/data-sources', headers=self.headers('admin-token'), json={
            'name': '假数据源', 'url': 'https://invalid.example/new',
        })
        self.assertEqual(created.status_code, 501, created.json)
        tested = self.client.post('/api/sites/data-sources/1/test', headers=self.headers('admin-token'))
        self.assertEqual(tested.status_code, 501, tested.json)
        self.assertFalse(tested.json['success'])
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM data_sources').fetchone()[0], 1)

    def test_reviewer_can_close_own_site_workorder_but_not_other_site(self):
        stats = self.client.get('/api/audit/stats', headers=self.headers('reviewer-token'))
        self.assertEqual(stats.status_code, 200, stats.json)
        self.assertEqual(stats.json['workorder_pending'], 1)

        forbidden = self.client.post('/api/workorders/WO-B0B-002/approve', headers=self.headers('reviewer-token'))
        self.assertEqual(forbidden.status_code, 403, forbidden.json)

        rejected = self.client.post('/api/workorders/WO-B0B-001/reject', headers=self.headers('reviewer-token'), json={
            'reason': '请补充处理后仪表读数',
        })
        self.assertEqual(rejected.status_code, 200, rejected.json)
        with self.temporary_db() as db:
            db.execute("UPDATE work_orders SET status='reviewing' WHERE order_no='WO-B0B-001'")
            db.commit()
        approved = self.client.post('/api/workorders/WO-B0B-001/approve', headers=self.headers('reviewer-token'), json={})
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(approved.json['status'], 'closed')

    def test_vehicle_approval_uses_existing_post_contract(self):
        response = self.client.post('/api/vehicle/applications/1/approve', headers=self.headers('admin-token'), json={
            'action': 'approve',
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['status'], 'approved')


if __name__ == '__main__':
    unittest.main()
