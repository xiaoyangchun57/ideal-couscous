import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class VehicleLifecycleRouteTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
        self.old_get_db = app_module.get_db
        self.old_tokens = dict(app_module._tokens)
        self.old_upload_dir = app_module.UPLOAD_DIR
        self.upload_dir = tempfile.mkdtemp(prefix='vehicle-upload-test-')
        app_module.UPLOAD_DIR = self.upload_dir

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
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': '运维员'},
            'other-token': {'id': 3, 'role': 'operator', 'real_name': '其他运维'},
            'reviewer-token': {'id': 4, 'role': 'reviewer', 'real_name': '审核员'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plate_no TEXT UNIQUE, model TEXT, seats INTEGER,
                    status TEXT DEFAULT 'idle', current_mileage REAL DEFAULT 0,
                    insurance_expiry TEXT, annual_inspection_expiry TEXT, fuel_type TEXT,
                    last_inspection_at TEXT, last_inspection_status TEXT,
                    last_maintenance_at TEXT, next_maintenance_mileage REAL
                );
                CREATE TABLE vehicle_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, document_type TEXT,
                    document_no TEXT, valid_until TEXT, attachment TEXT, remark TEXT, created_by INTEGER,
                    created_at TEXT
                );
                CREATE TABLE vehicle_inspections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, inspection_type TEXT,
                    inspection_date TEXT, inspector_id INTEGER, inspector_name TEXT, overall_status TEXT,
                    odometer REAL, items_json TEXT, remarks TEXT, photos TEXT, created_at TEXT
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, applicant_id INTEGER,
                    start_at TEXT, end_at TEXT, destination TEXT, reason TEXT, status TEXT DEFAULT 'pending',
                    approver_id INTEGER, approved_at TEXT, reject_reason TEXT, created_at TEXT,
                    site_id TEXT, work_order_no TEXT
                );
                CREATE TABLE vehicle_use_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER, start_mileage REAL,
                    end_mileage REAL, returned_at TEXT, checked_out_at TEXT, status TEXT,
                    out_inspection_id INTEGER, return_inspection_id INTEGER,
                    checkout_operator_id INTEGER, return_operator_id INTEGER
                );
                CREATE TABLE vehicle_refueling_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, refuel_at TEXT,
                    liters REAL, amount REAL, mileage_at REAL, remark TEXT, unit_price REAL,
                    operator_id INTEGER, operator_name TEXT, fuel_type TEXT,
                    energy_quantity REAL, energy_unit TEXT,
                    evidence_expected_count INTEGER DEFAULT 0,
                    evidence_status TEXT DEFAULT 'not_required'
                );
                CREATE TABLE vehicle_maintenance_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, maint_type TEXT, maint_at TEXT,
                    mileage_at REAL, next_maint_mileage REAL, items TEXT, cost REAL, remark TEXT,
                    maint_status TEXT, vendor TEXT, expected_return_at TEXT, actual_return_at TEXT,
                    fault_description TEXT, evidence_expected_count INTEGER DEFAULT 0,
                    evidence_status TEXT DEFAULT 'not_required'
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, stored_path TEXT NOT NULL,
                    thumbnail_path TEXT DEFAULT '', file_type TEXT DEFAULT 'image', mime_type TEXT DEFAULT '',
                    file_size INTEGER DEFAULT 0, description TEXT DEFAULT '', source_type TEXT DEFAULT '',
                    source_id INTEGER DEFAULT 0, site_id INTEGER, uploader_id INTEGER, uploader_name TEXT DEFAULT '',
                    gps_lat REAL, gps_lng REAL, taken_at TEXT, category TEXT DEFAULT '', is_deleted INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, watermark_text TEXT DEFAULT '',
                    recognized_category TEXT DEFAULT '', match_status TEXT DEFAULT '', match_confidence REAL,
                    review_required INTEGER DEFAULT 0, requirement_id INTEGER
                );
                CREATE TABLE photo_requirements (
                    id INTEGER PRIMARY KEY, item_name TEXT, review_required INTEGER,
                    watermark_keyword TEXT, category TEXT
                );
                INSERT INTO vehicles (id, plate_no, model, seats, status, current_mileage)
                    VALUES (1, '赣A测试1', '皮卡', 5, 'idle', 1000);
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.old_get_db
        app_module.UPLOAD_DIR = self.old_upload_dir
        app_module._tokens.clear()
        app_module._tokens.update(self.old_tokens)
        shutil.rmtree(self.upload_dir, ignore_errors=True)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def inspect(self, inspection_type, overall_status='normal'):
        response = self.client.post('/api/vehicle/inspections', headers=self.headers('operator-token'), json={
            'vehicle_id': 1, 'inspection_type': inspection_type, 'overall_status': overall_status,
            'items': [{'key': '四轮磨损及胎压', 'status': overall_status}], 'odometer': 1000,
        })
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['id']

    def test_mobile_operator_can_read_vehicle_choices_but_reviewer_cannot(self):
        operator = self.client.get('/api/vehicles', headers=self.headers('operator-token'))
        reviewer = self.client.get('/api/vehicles', headers=self.headers('reviewer-token'))
        self.assertEqual(operator.status_code, 200, operator.json)
        self.assertEqual(len(operator.json), 1)
        self.assertEqual(operator.json[0]['plate_no'], '赣A测试1')
        self.assertEqual(reviewer.status_code, 403, reviewer.json)

    def test_expired_document_blocks_application_and_schedule_check(self):
        response = self.client.post('/api/vehicle/documents', headers=self.headers('admin-token'), json={
            'vehicle_id': 1, 'document_type': 'insurance', 'valid_until': '2000-01-01',
        })
        self.assertEqual(response.status_code, 201)
        response = self.client.post('/api/vehicle/applications', headers=self.headers('operator-token'), json={
            'vehicle_id': 1, 'start_at': '2026-07-28 08:00:00', 'end_at': '2026-07-28 17:00:00', 'reason': '巡检',
        })
        self.assertEqual(response.status_code, 409)
        with app_module.get_db() as db:
            self.assertTrue(app_module._ps_check_vehicle_conflicts(db, 2, {'2026-07-28': 1}))

    def test_checkout_return_requires_checks_and_prevents_mileage_rollback(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO vehicle_applications (vehicle_id, applicant_id, reason, status) VALUES (1,2,'巡检','approved')")
        checkout = self.client.post('/api/vehicle/use-records', headers=self.headers('operator-token'), json={
            'application_id': 1, 'start_mileage': 1000,
        })
        self.assertEqual(checkout.status_code, 400)
        out_check = self.inspect('dispatch')
        checkout = self.client.post('/api/vehicle/use-records', headers=self.headers('operator-token'), json={
            'application_id': 1, 'start_mileage': 1000, 'out_inspection_id': out_check,
        })
        self.assertEqual(checkout.status_code, 201, checkout.json)
        record_id = checkout.json['id']
        duplicate = self.client.post('/api/vehicle/use-records', headers=self.headers('operator-token'), json={
            'application_id': 1, 'start_mileage': 1000, 'out_inspection_id': out_check,
        })
        self.assertEqual(duplicate.status_code, 409)
        return_check = self.inspect('return')
        rollback = self.client.post(f'/api/vehicle/use-records/{record_id}/return', headers=self.headers('operator-token'), json={
            'end_mileage': 999, 'return_inspection_id': return_check,
        })
        self.assertEqual(rollback.status_code, 400)
        returned = self.client.post(f'/api/vehicle/use-records/{record_id}/return', headers=self.headers('operator-token'), json={
            'end_mileage': 1025, 'return_inspection_id': return_check,
        })
        self.assertEqual(returned.status_code, 200, returned.json)
        self.assertEqual(returned.json['vehicle_status'], 'idle')
        retry = self.client.post('/api/vehicle/use-records', headers=self.headers('operator-token'), json={
            'application_id': 1, 'start_mileage': 1025, 'out_inspection_id': out_check,
        })
        self.assertEqual(retry.status_code, 409, retry.json)

    def test_blocked_return_check_restricts_vehicle_and_refuel_captures_operator(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO vehicle_applications (vehicle_id, applicant_id, reason, status) VALUES (1,2,'抢修','approved')")
        out_check = self.inspect('dispatch')
        record = self.client.post('/api/vehicle/use-records', headers=self.headers('operator-token'), json={
            'application_id': 1, 'start_mileage': 1000, 'out_inspection_id': out_check,
        }).json['id']
        return_check = self.inspect('return', 'blocked')
        returned = self.client.post(f'/api/vehicle/use-records/{record}/return', headers=self.headers('operator-token'), json={
            'end_mileage': 1020, 'return_inspection_id': return_check,
        })
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.json['vehicle_status'], 'restricted')
        refuel = self.client.post('/api/vehicle/refueling', headers=self.headers('operator-token'), json={
            'vehicle_id': 1, 'liters': 20, 'amount': 150, 'mileage_at': 1020,
        })
        self.assertEqual(refuel.status_code, 201, refuel.json)
        with app_module.get_db() as db:
            row = db.execute('SELECT unit_price, operator_name FROM vehicle_refueling_records').fetchone()
        self.assertEqual(row['operator_name'], '运维员')
        self.assertAlmostEqual(row['unit_price'], 7.5)

    def test_refueling_and_maintenance_reject_same_vehicle_mileage_with_different_content(self):
        first_refuel = self.client.post('/api/vehicle/refueling', headers=self.headers('admin-token'), json={
            'vehicle_id': 1, 'energy_quantity': 20, 'amount': 150, 'mileage_at': 1000,
            'remark': '第一次能源补给',
        })
        self.assertEqual(first_refuel.status_code, 201, first_refuel.json)
        duplicate_refuel = self.client.post('/api/vehicle/refueling', headers=self.headers('admin-token'), json={
            'vehicle_id': 1, 'energy_quantity': 30, 'amount': 240, 'mileage_at': 1000,
            'remark': '内容不同但里程相同',
        })
        self.assertEqual(duplicate_refuel.status_code, 409, duplicate_refuel.json)
        self.assertEqual(duplicate_refuel.json['code'], 'DUPLICATE_VEHICLE_MILEAGE')

        first_maintenance = self.client.post('/api/vehicle/maintenance', headers=self.headers('admin-token'), json={
            'vehicle_id': 1, 'maint_type': 'routine', 'mileage_at': 1000,
            'items': '更换机油', 'cost': 300,
        })
        self.assertEqual(first_maintenance.status_code, 201, first_maintenance.json)
        duplicate_maintenance = self.client.post('/api/vehicle/maintenance', headers=self.headers('admin-token'), json={
            'vehicle_id': 1, 'maint_type': 'minor', 'mileage_at': 1000,
            'items': '更换滤芯', 'cost': 500,
        })
        self.assertEqual(duplicate_maintenance.status_code, 409, duplicate_maintenance.json)
        self.assertEqual(duplicate_maintenance.json['code'], 'DUPLICATE_VEHICLE_MILEAGE')

        with app_module.get_db() as db:
            refueling_count = db.execute(
                'SELECT COUNT(*) FROM vehicle_refueling_records WHERE vehicle_id=1 AND mileage_at=1000',
            ).fetchone()[0]
            maintenance_count = db.execute(
                'SELECT COUNT(*) FROM vehicle_maintenance_records WHERE vehicle_id=1 AND mileage_at=1000',
            ).fetchone()[0]
        self.assertEqual((refueling_count, maintenance_count), (1, 1))
        with app_module.get_db() as db:
            maintenance = db.execute(
                'SELECT mileage_at, next_maint_mileage FROM vehicle_maintenance_records WHERE vehicle_id=1',
            ).fetchone()
            vehicle = db.execute(
                'SELECT current_mileage, next_maintenance_mileage FROM vehicles WHERE id=1',
            ).fetchone()
        self.assertEqual((maintenance['mileage_at'], maintenance['next_maint_mileage']), (1000, 6000))
        self.assertEqual((vehicle['current_mileage'], vehicle['next_maintenance_mileage']), (1000, 6000))

    def test_vehicle_mileage_is_managed_and_client_values_cannot_bypass_duplicates(self):
        refuel = self.client.post('/api/vehicle/refueling', headers=self.headers('admin-token'), json={
            'vehicle_id': 1, 'energy_quantity': 20, 'mileage_at': 1001,
        })
        self.assertEqual(refuel.status_code, 409, refuel.json)
        self.assertEqual(refuel.json['code'], 'VEHICLE_MILEAGE_MISMATCH')

        maintenance = self.client.post('/api/vehicle/maintenance', headers=self.headers('admin-token'), json={
            'vehicle_id': 1, 'maint_type': 'routine', 'mileage_at': 1001,
            'next_maint_mileage': 999999,
        })
        self.assertEqual(maintenance.status_code, 409, maintenance.json)
        self.assertEqual(maintenance.json['code'], 'VEHICLE_MILEAGE_MISMATCH')

        edited = self.client.put('/api/vehicles/1', headers=self.headers('admin-token'), json={
            'current_mileage': 1001, 'next_maintenance_mileage': 999999,
        })
        self.assertEqual(edited.status_code, 400, edited.json)
        self.assertEqual(edited.json['code'], 'VEHICLE_MILEAGE_MANAGED')

        with app_module.get_db() as db:
            vehicle = db.execute(
                'SELECT current_mileage, next_maintenance_mileage FROM vehicles WHERE id=1',
            ).fetchone()
        self.assertEqual((vehicle['current_mileage'], vehicle['next_maintenance_mileage']), (1000, None))

    def test_expired_annual_inspection_is_unavailable_in_vehicle_list(self):
        with app_module.get_db() as db:
            db.execute("UPDATE vehicles SET annual_inspection_expiry='2000-01-01', status='idle' WHERE id=1")
        response = self.client.get('/api/vehicles', headers=self.headers('admin-token'))
        self.assertEqual(response.status_code, 200, response.json)
        vehicle = next(row for row in response.json if row['id'] == 1)
        self.assertFalse(vehicle['dispatchable'])
        self.assertEqual(vehicle['status'], 'idle')
        self.assertIn('annual_inspection', vehicle['document_state']['expired'])
        self.assertIn('证照已到期：年检', vehicle['dispatch_block_reason'])

    def test_month_only_annual_inspection_expires_at_month_end(self):
        current_month = app_module.datetime.now().strftime('%Y-%m')
        with app_module.get_db() as db:
            db.execute('UPDATE vehicles SET annual_inspection_expiry=?, status=\'idle\' WHERE id=1', (current_month,))
        response = self.client.get('/api/vehicles', headers=self.headers('admin-token'))
        self.assertEqual(response.status_code, 200, response.json)
        vehicle = next(row for row in response.json if row['id'] == 1)
        self.assertTrue(vehicle['dispatchable'])
        self.assertNotIn('annual_inspection', vehicle['document_state']['expired'])
        self.assertIn('annual_inspection', vehicle['document_state']['due_soon'])

    def test_refueling_photo_upload_updates_evidence_to_complete(self):
        created = self.client.post('/api/vehicle/refueling', headers=self.headers('admin-token'), json={
            'vehicle_id': 1, 'energy_quantity': 20, 'amount': 150, 'mileage_at': 1000,
            'evidence_expected_count': 1,
        })
        self.assertEqual(created.status_code, 201, created.json)
        record_id = created.json['id']
        png = bytes([
            137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82,
            0, 0, 0, 1, 0, 0, 0, 1, 8, 4, 0, 0, 0, 181, 28, 12, 2,
            0, 0, 0, 11, 73, 68, 65, 84, 120, 218, 99, 100, 248, 15, 0, 1,
            5, 1, 1, 39, 24, 227, 102, 0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96, 130,
        ])
        uploaded = self.client.post('/api/upload/attachment', headers=self.headers('admin-token'), data={
            'file': (io.BytesIO(png), 'vehicle-proof.png'),
            'source_type': 'vehicle',
            'source_id': str(record_id),
            'category': '车辆加油',
            'uploader_name': '管理员',
        }, content_type='multipart/form-data')
        self.assertEqual(uploaded.status_code, 200, uploaded.json)

        status = self.client.put(
            f'/api/vehicle/refueling/{record_id}/evidence-status',
            headers=self.headers('admin-token'), json={},
        )
        self.assertEqual(status.status_code, 200, status.json)
        self.assertEqual(status.json, {
            'ok': True, 'evidence_status': 'complete', 'uploaded': 1, 'expected': 1,
        })
        self.assertTrue((Path(self.upload_dir) / uploaded.json['url'].replace('/uploads/', '')).exists())

    def test_mobile_trip_refuel_and_fault_keep_vehicle_restricted_after_return(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO vehicle_applications (vehicle_id, applicant_id, reason, status) VALUES (1,2,'巡检','approved')")
        out_check = self.inspect('dispatch')
        record_id = self.client.post('/api/vehicle/use-records', headers=self.headers('operator-token'), json={
            'application_id': 1, 'start_mileage': 1000, 'out_inspection_id': out_check,
        }).json['id']
        refuel = self.client.post(f'/api/mobile/vehicle-use-records/{record_id}/refueling', headers=self.headers('operator-token'), json={
            'liters': 20, 'amount': 150, 'mileage_at': 1010, 'remark': '途中加油',
        })
        self.assertEqual(refuel.status_code, 201, refuel.json)
        fault = self.client.post(f'/api/mobile/vehicle-use-records/{record_id}/faults', headers=self.headers('operator-token'), json={
            'fault_type': '轮胎', 'mileage_at': 1015, 'description': '右前轮胎压报警',
        })
        self.assertEqual(fault.status_code, 201, fault.json)
        return_check = self.inspect('return', 'normal')
        returned = self.client.post(f'/api/vehicle/use-records/{record_id}/return', headers=self.headers('operator-token'), json={
            'end_mileage': 1020, 'return_inspection_id': return_check,
        })
        self.assertEqual(returned.status_code, 200, returned.json)
        self.assertEqual(returned.json['vehicle_status'], 'restricted')
        with app_module.get_db() as db:
            maintenance = db.execute('SELECT maint_status, fault_description FROM vehicle_maintenance_records').fetchone()
            vehicle = db.execute('SELECT status FROM vehicles WHERE id=1').fetchone()
        self.assertEqual((maintenance['maint_status'], maintenance['fault_description']), ('open', '右前轮胎压报警'))
        self.assertEqual(vehicle['status'], 'restricted')

    def test_empty_vehicle_can_delete_but_history_is_preserved_and_electric_uses_kwh(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO vehicles (id, plate_no, model, status, current_mileage, fuel_type) VALUES (2,'赣A空车','SUV','idle',0,'electric')")
            db.execute("INSERT INTO vehicle_applications (vehicle_id, applicant_id, reason, status) VALUES (1,2,'巡检','approved')")
        protected = self.client.delete('/api/vehicles/1', headers=self.headers('admin-token'))
        self.assertEqual(protected.status_code, 409, protected.json)
        removable = self.client.delete('/api/vehicles/2', headers=self.headers('admin-token'))
        self.assertEqual(removable.status_code, 200, removable.json)

        with app_module.get_db() as db:
            db.execute("INSERT INTO vehicles (id, plate_no, model, status, current_mileage, fuel_type) VALUES (3,'赣A电车','SUV','idle',1000,'electric')")
            db.execute("INSERT INTO vehicle_applications (vehicle_id, applicant_id, reason, status) VALUES (3,2,'巡检','approved')")
        out_check = self.client.post('/api/vehicle/inspections', headers=self.headers('operator-token'), json={
            'vehicle_id': 3, 'inspection_type': 'dispatch', 'overall_status': 'normal', 'items': [], 'odometer': 1000,
        }).json['id']
        record_id = self.client.post('/api/vehicle/use-records', headers=self.headers('operator-token'), json={
            'application_id': 2, 'start_mileage': 1000, 'out_inspection_id': out_check,
        }).json['id']
        charged = self.client.post(f'/api/mobile/vehicle-use-records/{record_id}/refueling', headers=self.headers('operator-token'), json={
            'energy_quantity': 32.5, 'amount': 42, 'mileage_at': 1010,
        })
        self.assertEqual(charged.status_code, 201, charged.json)
        with app_module.get_db() as db:
            energy = db.execute('SELECT energy_quantity, energy_unit, liters FROM vehicle_refueling_records WHERE vehicle_id=3').fetchone()
        self.assertEqual((energy['energy_quantity'], energy['energy_unit'], energy['liters']), (32.5, 'kWh', 32.5))


if __name__ == '__main__':
    unittest.main()
