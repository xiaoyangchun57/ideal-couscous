import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class CrossModuleVehicleWorkorderTest(unittest.TestCase):
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
            'admin-token': {'id': 1, 'role': 'admin', 'real_name': 'Admin'},
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': 'Operator'},
            'other-token': {'id': 3, 'role': 'operator', 'real_name': 'Other operator'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, real_name TEXT, username TEXT, role TEXT,
                    status TEXT DEFAULT 'active'
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, gps_lat REAL, gps_lng REAL);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT UNIQUE, site_id INTEGER, status TEXT,
                    assignee TEXT, title TEXT DEFAULT '', check_in_lat REAL, check_in_lng REAL, check_in_time TEXT,
                    check_in_user TEXT, images TEXT DEFAULT '', remark TEXT DEFAULT '',
                    review_submitted_at TEXT
                );
                CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY, plate_no TEXT, model TEXT, status TEXT, current_mileage REAL,
                    insurance_expiry TEXT, annual_inspection_expiry TEXT
                );
                CREATE TABLE vehicle_documents (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, document_type TEXT, valid_until TEXT
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, applicant_id INTEGER,
                    start_at TEXT, end_at TEXT, destination TEXT, reason TEXT, status TEXT DEFAULT 'pending',
                    approver_id INTEGER, approved_at TEXT, reject_reason TEXT, created_at TEXT,
                    site_id TEXT, work_order_no TEXT, no_vehicle_required INTEGER DEFAULT 0,
                    vehicle_exception_reason TEXT DEFAULT ''
                );
                CREATE TABLE vehicle_use_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER, start_mileage REAL,
                    end_mileage REAL, returned_at TEXT, checked_out_at TEXT, status TEXT,
                    out_inspection_id INTEGER, return_inspection_id INTEGER,
                    checkout_operator_id INTEGER, return_operator_id INTEGER
                );
                CREATE TABLE vehicle_inspections (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, inspection_type TEXT,
                    overall_status TEXT
                );
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER, file_type TEXT,
                    is_deleted INTEGER DEFAULT 0
                );
                CREATE TABLE timeline_events (
                    source_type TEXT, source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id INTEGER, title TEXT, content TEXT, is_read INTEGER DEFAULT 0,
                    dedupe_key TEXT DEFAULT '', payload_json TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX uq_notifications_unread_dedupe
                    ON notifications(user_id,dedupe_key)
                    WHERE is_read=0 AND COALESCE(dedupe_key,'')!='';
                INSERT INTO users (id,real_name,username,role,status) VALUES
                    (1,'Admin','admin','admin','active'),
                    (2,'Operator','operator','operator','active'),
                    (3,'Other operator','other','operator','active');
                INSERT INTO user_roles VALUES (1,'admin'),(2,'operator'),(3,'operator');
                INSERT INTO sites VALUES (1, 'Test site', 28.6800, 115.7300);
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO work_orders (id,order_no,site_id,status,assignee,title)
                    VALUES (1, 'WO-CROSS-1', 1, 'in_progress', 'Operator', 'Cross-module test');
                INSERT INTO vehicles VALUES (1, 'TEST-001', 'Service vehicle', 'idle', 1000, NULL, NULL);
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def workorder_checkin(self):
        return self.client.post('/api/mobile/check-in', headers=self.headers('operator-token'), json={
            'order_no': 'WO-CROSS-1', 'lat': 28.6800, 'lng': 115.7300,
        })

    def create_application(self, *, applicant_id=2, vehicle_id=None, status='pending',
                           no_vehicle_required=False, exception_reason=''):
        with app_module.get_db() as db:
            cur = db.execute('''INSERT INTO vehicle_applications
                (vehicle_id, applicant_id, start_at, end_at, reason, status, work_order_no,
                 no_vehicle_required, vehicle_exception_reason)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (vehicle_id, applicant_id, '2099-08-10 08:00:00', '2099-08-10 18:00:00',
                 'Cross-module work order', status, 'WO-CROSS-1', int(no_vehicle_required),
                 exception_reason))
            return cur.lastrowid

    def assert_checkin_blocked(self, code):
        response = self.workorder_checkin()
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], code)

    def test_workorder_arrival_requires_resource_preparation(self):
        self.assert_checkin_blocked('WORKORDER_RESOURCE_PREPARATION_REQUIRED')

    def test_workorder_arrival_rejects_pending_or_rejected_resource(self):
        self.create_application(vehicle_id=1, status='pending')
        self.assert_checkin_blocked('WORKORDER_RESOURCE_APPROVAL_REQUIRED')

        with app_module.get_db() as db:
            db.execute("UPDATE vehicle_applications SET status='rejected'")
        self.assert_checkin_blocked('WORKORDER_RESOURCE_APPROVAL_REQUIRED')

    def test_approved_vehicle_requires_active_checkout_before_arrival(self):
        self.create_application(vehicle_id=1, status='approved')
        self.assert_checkin_blocked('WORKORDER_VEHICLE_CHECKOUT_REQUIRED')

    def test_active_vehicle_use_allows_arrival_for_the_requesting_operator(self):
        application_id = self.create_application(vehicle_id=1, status='approved')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,checked_out_at,status)
                VALUES (?,1000,'2099-08-10 08:00:00','checked_out')""", (application_id,))
        arrived = self.workorder_checkin()
        self.assertEqual(arrived.status_code, 200, arrived.json)
        self.assertTrue(arrived.json['location_verified'])

    def test_approved_no_vehicle_exception_allows_arrival(self):
        self.create_application(status='approved', no_vehicle_required=True,
                                exception_reason='Within the station campus')
        arrived = self.workorder_checkin()
        self.assertEqual(arrived.status_code, 200, arrived.json)

    def test_another_operators_approved_resource_cannot_unlock_arrival(self):
        application_id = self.create_application(applicant_id=3, vehicle_id=1, status='approved')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,checked_out_at,status)
                VALUES (?,1000,'2099-08-10 08:00:00','checked_out')""", (application_id,))
        self.assert_checkin_blocked('WORKORDER_RESOURCE_PREPARATION_REQUIRED')

    def test_returned_vehicle_journey_cannot_unlock_arrival(self):
        application_id = self.create_application(vehicle_id=1, status='returned')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,end_mileage,checked_out_at,returned_at,status)
                VALUES (?,1000,1010,'2099-08-10 08:00:00','2099-08-10 18:00:00','returned')""",
                (application_id,))
        self.assert_checkin_blocked('WORKORDER_RESOURCE_PREPARATION_REQUIRED')

    def test_submit_review_requires_arrival_even_from_web_or_legacy_client(self):
        response = self.client.post('/api/workorders/WO-CROSS-1/submit-review',
                                    headers=self.headers('operator-token'), json={
                                        'resolution_note': 'Disposal completed', 'client': 'web',
                                    })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'WORKORDER_CHECKIN_REQUIRED')

    def test_same_vehicle_pending_conflicts_but_terminal_history_does_not(self):
        first = self.client.post('/api/vehicle/applications', headers=self.headers('operator-token'), json={
            'vehicle_id': 1, 'start_at': '2099-08-10 08:00:00',
            'end_at': '2099-08-10 18:00:00', 'reason': 'First request',
        })
        self.assertEqual(first.status_code, 201, first.json)
        with app_module.get_db() as db:
            first_notices = db.execute("""SELECT user_id,source_type,source_id,title,is_read,dedupe_key
                FROM notifications ORDER BY id""").fetchall()
        self.assertEqual(len(first_notices), 1)
        self.assertEqual(
            (first_notices[0]['user_id'], first_notices[0]['source_type'],
             first_notices[0]['source_id'], first_notices[0]['title'], first_notices[0]['is_read']),
            (1, 'vehicle_application', first.json['id'], '用车申请待审核', 0),
        )
        self.assertEqual(first_notices[0]['dedupe_key'],
                         f"vehicle_application:{first.json['id']}")
        conflict = self.client.post('/api/vehicle/applications', headers=self.headers('other-token'), json={
            'vehicle_id': 1, 'start_at': '2099-08-10 09:00:00',
            'end_at': '2099-08-10 17:00:00', 'reason': 'Concurrent request',
        })
        self.assertEqual(conflict.status_code, 409, conflict.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_applications').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], 1)

        rejected = self.client.post(
            f"/api/vehicle/applications/{first.json['id']}/approve", headers=self.headers('admin-token'),
            json={'action': 'reject', 'reject_reason': 'Reassigned'},
        )
        self.assertEqual(rejected.status_code, 200, rejected.json)
        with app_module.get_db() as db:
            archived = db.execute("""SELECT is_read FROM notifications
                WHERE source_type='vehicle_application' AND source_id=?""",
                                  (first.json['id'],)).fetchone()
        self.assertEqual(archived['is_read'], 1)
        replacement = self.client.post('/api/vehicle/applications', headers=self.headers('other-token'), json={
            'vehicle_id': 1, 'start_at': '2099-08-10 09:00:00',
            'end_at': '2099-08-10 17:00:00', 'reason': 'Replacement request',
        })
        self.assertEqual(replacement.status_code, 201, replacement.json)
        with app_module.get_db() as db:
            notices = db.execute("""SELECT source_id,is_read FROM notifications
                WHERE source_type='vehicle_application' ORDER BY id""").fetchall()
        self.assertEqual([(row['source_id'], row['is_read']) for row in notices],
                         [(first.json['id'], 1), (replacement.json['id'], 0)])

    def test_return_marks_application_terminal_and_allows_a_new_reservation(self):
        application_id = self.create_application(vehicle_id=1, status='approved')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,checked_out_at,status)
                VALUES (?,1000,'2099-08-10 08:00:00','checked_out')""", (application_id,))
            record_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            db.execute("INSERT INTO vehicle_inspections VALUES (1,1,'return','normal')")
        returned = self.client.post(f'/api/vehicle/use-records/{record_id}/return',
                                    headers=self.headers('operator-token'), json={
                                        'end_mileage': 1010, 'return_inspection_id': 1,
                                    })
        self.assertEqual(returned.status_code, 200, returned.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT status FROM vehicle_applications WHERE id=?',
                                        (application_id,)).fetchone()['status'], 'returned')
        replacement = self.client.post('/api/vehicle/applications', headers=self.headers('other-token'), json={
            'vehicle_id': 1, 'start_at': '2099-08-10 09:00:00',
            'end_at': '2099-08-10 17:00:00', 'reason': 'Subsequent reservation',
        })
        self.assertEqual(replacement.status_code, 201, replacement.json)
        with app_module.get_db() as db:
            notice = db.execute("""SELECT user_id,source_id,is_read FROM notifications
                WHERE source_type='vehicle_application'""").fetchone()
        self.assertEqual((notice['user_id'], notice['source_id'], notice['is_read']),
                         (1, replacement.json['id'], 0))


if __name__ == '__main__':
    unittest.main()
