import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class MobileMyTodayScopeTest(unittest.TestCase):
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
        app_module._tokens['operator-token'] = {
            'id': 2, 'role': 'operator', 'real_name': '甲运维', 'username': 'operator-a'
        }
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, code TEXT, gps_lat REAL, gps_lng REAL, type TEXT
                );
                CREATE TABLE plan_schedules (
                    id INTEGER PRIMARY KEY, user_id INTEGER, schedule_type TEXT, status TEXT, plan_data TEXT,
                    vehicle_days TEXT, spare_parts TEXT, work_order_ids TEXT, version INTEGER, remarks TEXT,
                    period_start TEXT, period_end TEXT
                );
                CREATE TABLE insp_plans (
                    id INTEGER PRIMARY KEY, plan_name TEXT, assignee_id INTEGER, plan_schedule_id INTEGER,
                    generate_date TEXT, status TEXT, completion_rate REAL
                );
                CREATE TABLE insp_plan_items (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, item_name TEXT,
                    category TEXT, frequency TEXT, result TEXT, calibrator TEXT,
                    calibration_values TEXT, photo_urls TEXT, remark TEXT, check_time TEXT, execution_status TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER, title TEXT, status TEXT,
                    source TEXT, level TEXT, assignee TEXT, created_at TEXT, sla_deadline TEXT,
                    event_type TEXT, related_alert_id INTEGER
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, metric TEXT, level TEXT,
                    message TEXT, status TEXT, created_at TEXT
                );
                CREATE TABLE plan_departure_confirmations (
                    schedule_id INTEGER, user_id INTEGER, work_date TEXT,
                    vehicle_confirmed INTEGER, parts_confirmed INTEGER, note TEXT, confirmed_at TEXT
                );
                CREATE TABLE vehicles (
                    id INTEGER PRIMARY KEY, plate_no TEXT, model TEXT, status TEXT, current_mileage INTEGER, fuel_type TEXT
                );
                CREATE TABLE vehicle_applications (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER, start_at TEXT, end_at TEXT, destination TEXT, status TEXT, reason TEXT
                );
                CREATE TABLE vehicle_use_records (
                    id INTEGER PRIMARY KEY, application_id INTEGER, start_mileage INTEGER, end_mileage INTEGER,
                    returned_at TEXT, status TEXT
                );
                CREATE TABLE vehicle_documents (
                    id INTEGER PRIMARY KEY, vehicle_id INTEGER, document_type TEXT, valid_until TEXT
                );
                CREATE TABLE plan_resource_reservations (
                    id INTEGER PRIMARY KEY, schedule_id INTEGER, part_id INTEGER, planned_quantity INTEGER,
                    issued_quantity INTEGER, status TEXT
                );
                CREATE TABLE spare_parts_inventory (
                    id INTEGER PRIMARY KEY, part_name TEXT, part_code TEXT, unit TEXT
                );
                CREATE TABLE inspection_checkins (
                    id INTEGER PRIMARY KEY, site_id INTEGER, user_id INTEGER, check_time TEXT
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?)', [(2, '甲运维', 'operator'), (3, '乙运维', 'operator')])
            db.execute('INSERT INTO user_sites VALUES (2, 1)')
            db.execute('INSERT INTO user_sites VALUES (2, 2)')
            db.executemany('INSERT INTO sites VALUES (?,?,?,?,?,?)', [
                (1, '测试站', 'S-01', 28.6, 115.7, 'water_quality'),
                (2, '昨日遗留站', 'S-02', 28.7, 115.8, 'water_quality'),
            ])
            db.executemany('''INSERT INTO plan_schedules
                (id,user_id,schedule_type,status,plan_data,vehicle_days,spare_parts,work_order_ids,version,remarks,period_start,period_end)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', [
                (11, 2, 'weekly', 'approved', '{}', '{"' + today + '": 1}', '[]', '[]', 1, '', today, today),
                (12, 3, 'weekly', 'approved', '{}', '{}', '[]', '[]', 1, '', today, today),
                (13, 2, 'weekly', 'approved', '{}', '{}', '[]', '[]', 1, '', yesterday, yesterday),
            ])
            db.executemany('INSERT INTO insp_plans VALUES (?,?,?,?,?,?,?)', [
                (101, '今日测试计划', 2, 11, today, 'active', 0),
                (102, '其他人今日计划', 3, 12, today, 'active', 0),
                (103, '昨日遗留计划', 2, 13, yesterday, 'active', 0),
            ])
            db.executemany('INSERT INTO insp_plan_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', [
                (1001, 101, 1, '甲的检查项', '设备', 'weekly', None, '', '', '[]', '', '', 'active'),
                (1002, 102, 1, '乙的检查项', '设备', 'weekly', None, '', '', '[]', '', '', 'active'),
                (1003, 103, 2, '昨日未完成检查项', '设备', 'weekly', None, '', '', '[]', '', '', 'active'),
            ])
            db.execute('INSERT INTO vehicles VALUES (1, ?, ?, ?, ?, ?)', ('赣A00001', '巡检车', 'idle', 12000, 'gasoline'))
            db.execute('''INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,start_at,end_at,destination,status,reason)
                VALUES (?,?,?,?,?,?,?,?)''',
                (1, 1, 2, yesterday + ' 08:00:00', (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d') + ' 18:00:00',
                 '巡检', 'approved', '巡检计划#11用车'))
            db.execute('''INSERT INTO vehicle_use_records
                (id,application_id,start_mileage,end_mileage,returned_at,status)
                VALUES (?,?,?,?,?,?)''', (1, 1, 12000, None, None, 'checked_out'))
            db.executemany('INSERT INTO work_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [
                (1, 'WO-A', 1, '甲的工单', 'in_progress', 'manual', 'normal', '甲运维', today, '', '', None),
                (2, 'WO-B', 1, '乙的工单', 'in_progress', 'manual', 'normal', '乙运维', today, '', '', None),
            ])
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    def test_homepage_returns_only_current_operators_tasks_and_workorders(self):
        response = self.client.get('/api/mobile/my-today', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['summary']['total_items'], 2)
        site_map = {item['site_name']: item for item in response.json['sites']}
        self.assertEqual(site_map['测试站']['pending_items'], 1)
        self.assertEqual(site_map['昨日遗留站']['pending_items'], 1)
        self.assertTrue(site_map['昨日遗留站']['has_carryover'])
        self.assertEqual(site_map['昨日遗留站']['carryover_items'], 1)
        self.assertTrue(response.json['sites'][0]['has_carryover'])
        self.assertEqual([item['order_no'] for item in response.json['workorders']], ['WO-A'])

    def test_today_execution_includes_unfinished_historical_package(self):
        response = self.client.get('/api/mobile/today-execution', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        packages = response.json['packages']
        carryovers = [item for item in packages if item['is_carryover']]
        self.assertEqual(len(carryovers), 1)
        self.assertEqual(carryovers[0]['work_date'], (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'))
        self.assertEqual([site['name'] for site in carryovers[0]['sites']], ['昨日遗留站'])
        self.assertEqual(carryovers[0]['sites'][0]['total'], 1)

    def test_today_execution_keeps_one_vehicle_trip_until_plan_end(self):
        response = self.client.get('/api/mobile/today-execution', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        today_package = next(item for item in response.json['packages'] if item['plan_id'] == 101)
        self.assertEqual(today_package['vehicle_application_id'], 1)
        self.assertEqual(today_package['vehicle_use']['id'], 1)
        self.assertFalse(today_package['vehicle_can_return'])

    def test_execution_site_returns_readable_category_names(self):
        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        category = response.json['categories'][0]
        self.assertEqual(category['category'], '设备')
        self.assertEqual(category['category_cn'], '设备检查')

    def test_plan_vehicle_cannot_return_before_trip_end(self):
        records = self.client.get('/api/vehicle/use-records', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(records.status_code, 200, records.json)
        self.assertEqual(records.json[0]['plan_schedule_id'], 11)
        response = self.client.post('/api/vehicle/use-records/1/return',
                                    headers={'Authorization': 'Bearer operator-token'},
                                    json={'end_mileage': 12001})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn('计划行程尚未结束', response.json['error'])


if __name__ == '__main__':
    unittest.main()
