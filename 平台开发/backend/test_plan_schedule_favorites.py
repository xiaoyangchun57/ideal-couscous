import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class PlanScheduleFavoritesTest(unittest.TestCase):
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

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'owner-token': {'id': 2, 'role': 'operator', 'real_name': '固定运维', 'username': 'owner'},
            'other-token': {'id': 3, 'role': 'operator', 'real_name': '其他运维', 'username': 'other'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, gps_lat REAL, gps_lng REAL);
                CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY, plate_no TEXT, status TEXT,
                    insurance_expiry TEXT, annual_inspection_expiry TEXT
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER,
                    start_at TEXT, end_at TEXT, status TEXT
                );
                CREATE TABLE vehicle_documents (
                    vehicle_id INTEGER, document_type TEXT, valid_until TEXT
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, schedule_type TEXT,
                    period_start TEXT, period_end TEXT, plan_data TEXT, vehicle_days TEXT,
                    spare_parts TEXT, work_order_ids TEXT, status TEXT, remarks TEXT,
                    tasks_generated INTEGER DEFAULT 0
                );
                CREATE TABLE plan_schedule_favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    source_schedule_id INTEGER, name TEXT, snapshot TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE plan_schedule_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, version INTEGER,
                    event_type TEXT, operator_id INTEGER, payload TEXT
                );
                INSERT INTO users VALUES (2, '固定运维', 'operator');
                INSERT INTO users VALUES (3, '其他运维', 'operator');
                INSERT INTO user_sites VALUES (2, 10);
                INSERT INTO user_sites VALUES (2, 11);
                INSERT INTO sites VALUES (10, '一号站', 28.1, 115.1);
                INSERT INTO sites VALUES (11, '二号站', 28.2, 115.2);
                INSERT INTO vehicles VALUES (7, '赣A00007', 'available', NULL, NULL);
            ''')
            plan_data = {
                '2026-07-21': {'sites': [10], 'notes': '先做一号站'},
                '2026-07-23': {'sites': [11], 'notes': ''},
            }
            db.execute('''INSERT INTO plan_schedules
                (id,user_id,schedule_type,period_start,period_end,plan_data,vehicle_days,
                 spare_parts,work_order_ids,status,remarks,tasks_generated)
                VALUES (1,2,'weekly','2026-07-20','2026-07-26',?,?,?,?, 'approved',?,1)''',
                (json.dumps(plan_data), json.dumps({'2026-07-21': 7}),
                 json.dumps([{'part_id': 8, 'quantity': 2, 'part_name': '滤芯'}]),
                 json.dumps([99]), '固定路线'))
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token='owner-token'):
        return {'Authorization': 'Bearer ' + token}

    def test_favorite_generates_shifted_editable_draft_without_one_time_orders(self):
        created = self.client.post('/api/plan-schedule-favorites', headers=self.headers(),
                                   json={'schedule_id': 1, 'name': '固定周二周四路线'})
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual((created.json['site_count'], created.json['day_count']), (2, 2))
        favorite_id = created.json['id']

        applied = self.client.post(f'/api/plan-schedule-favorites/{favorite_id}/draft',
                                   headers=self.headers(), json={'period_start': '2026-08-03'})
        self.assertEqual(applied.status_code, 201, applied.json)
        schedule = applied.json['schedule']
        self.assertEqual(schedule['status'], 'draft')
        self.assertEqual(schedule['period_end'], '2026-08-09')
        self.assertEqual(schedule['plan_data']['2026-08-04']['sites'], [10])
        self.assertEqual(schedule['plan_data']['2026-08-06']['sites'], [11])
        self.assertEqual(schedule['vehicle_days'], {'2026-08-04': 7})
        self.assertEqual(schedule['spare_parts'][0]['part_id'], 8)
        self.assertEqual(schedule['work_order_ids'], [])
        with app_module.get_db() as db:
            event = db.execute("SELECT event_type, payload FROM plan_schedule_events WHERE schedule_id=?",
                               (schedule['id'],)).fetchone()
        self.assertEqual(event['event_type'], 'created_from_favorite')
        self.assertIn('work_order_ids', json.loads(event['payload'])['excluded_fields'])

    def test_duplicate_and_cross_user_favorites_are_rejected(self):
        first = self.client.post('/api/plan-schedule-favorites', headers=self.headers(),
                                 json={'schedule_id': 1})
        duplicate = self.client.post('/api/plan-schedule-favorites', headers=self.headers(),
                                     json={'schedule_id': 1})
        other = self.client.post('/api/plan-schedule-favorites', headers=self.headers('other-token'),
                                 json={'schedule_id': 1})
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(duplicate.status_code, 409, duplicate.json)
        self.assertEqual(other.status_code, 403, other.json)


if __name__ == '__main__':
    unittest.main()
