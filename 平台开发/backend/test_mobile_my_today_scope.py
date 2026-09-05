import os
import json
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
        app_module._tokens['admin-operator-token'] = {
            'id': 2, 'role': 'admin', 'roles': ['admin', 'operator'],
            'real_name': '甲运维', 'username': 'operator-a'
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
                    calibration_values TEXT, photo_urls TEXT, remark TEXT, check_time TEXT, execution_status TEXT,
                    check_out_time TEXT, review_status INTEGER DEFAULT 0,
                    evidence_status TEXT DEFAULT '', rework_required_at TEXT DEFAULT ''
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER, title TEXT, status TEXT,
                    source TEXT, level TEXT, assignee TEXT, check_in_time TEXT, check_in_user TEXT,
                    created_at TEXT, sla_deadline TEXT, event_type TEXT, related_alert_id INTEGER
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, metric TEXT, level TEXT,
                    message TEXT, status TEXT, created_at TEXT, related_order_no TEXT
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
                CREATE TABLE operation_attachments (
                    id INTEGER PRIMARY KEY, stored_path TEXT, source_type TEXT, source_id INTEGER,
                    site_id INTEGER, uploader_id INTEGER, uploader_name TEXT DEFAULT '',
                    item_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    is_deleted INTEGER DEFAULT 0,
                    review_status TEXT DEFAULT 'pending', evidence_qualification TEXT DEFAULT 'qualified',
                    extra_json TEXT DEFAULT '{}'
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
            db.executemany('''INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,calibrator,
                 calibration_values,photo_urls,remark,check_time,execution_status,check_out_time)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [
                (1001, 101, 1, '甲的检查项', '设备', 'weekly', None, '', '', '[]', '', '', 'active', None),
                (1002, 102, 1, '乙的检查项', '设备', 'weekly', None, '', '', '[]', '', '', 'active', None),
                (1003, 103, 2, '昨日未完成检查项', '设备', 'weekly', None, '', '', '[]', '', '', 'active', None),
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
            db.executemany('''INSERT INTO work_orders
                (id,order_no,site_id,title,status,source,level,assignee,created_at,sla_deadline,event_type,related_alert_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', [
                (1, 'WO-A', 1, '甲的工单', 'in_progress', 'manual', 'normal', '甲运维', today, '', '', None),
                (2, 'WO-B', 1, '乙的工单', 'in_progress', 'manual', 'normal', '乙运维', today, '', '', None),
            ])
        self.client = app_module.app.test_client()

    def test_home_collapses_linked_manual_alert_into_visible_workorder(self):
        today = datetime.now().strftime('%Y-%m-%d')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO work_orders
                (id,order_no,site_id,title,status,source,level,assignee,created_at,sla_deadline,event_type)
                VALUES (10,'WO-MANUAL',1,'【人工上报】感官异常','pending','manual_report','normal',
                        '甲运维',?,'','sensory')""", (today,))
            db.executemany("""INSERT INTO alerts
                (id,site_id,metric,level,message,status,created_at,related_order_no)
                VALUES (?,?,?,?,?,'pending',?,?)""", [
                (20, 1, 'manual_report', 'yellow', '派生告警', today, 'WO-MANUAL'),
                (21, 1, 'turbidity', 'yellow', '独立告警', today, None),
            ])
        response = self.client.get('/api/mobile/my-today',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        manual_order = next(item for item in response.json['workorders'] if item['order_no'] == 'WO-MANUAL')
        self.assertEqual(manual_order['source_cn'], '人工上报')
        self.assertEqual(manual_order['title'], '【人工上报】感官异常')
        self.assertEqual(manual_order['display_title'], '感官异常')
        self.assertEqual(manual_order['title'], '【人工上报】感官异常')
        self.assertEqual(manual_order['display_title'], '感官异常')
        self.assertEqual([item['id'] for item in response.json['alerts']], [21])
        self.assertEqual(response.json['alerts'][0]['metric_cn'], '浊度')
        self.assertEqual(response.json['summary']['pending_alerts'], 1)

    def test_home_filters_linked_alerts_before_applying_fifteen_item_limit(self):
        today = datetime.now().strftime('%Y-%m-%d')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO work_orders
                (id,order_no,site_id,title,status,source,level,assignee,created_at,sla_deadline,event_type)
                VALUES (10,'WO-BULK',1,'人工上报工单','pending','manual_report','normal',
                        '甲运维',?,'','sensory')""", (today,))
            db.executemany("""INSERT INTO alerts
                (id,site_id,metric,level,message,status,created_at,related_order_no)
                VALUES (?,1,'manual_report','red','派生告警','pending',?,'WO-BULK')""",
                [(100 + index, today) for index in range(15)])
            db.execute("""INSERT INTO alerts
                (id,site_id,metric,level,message,status,created_at,related_order_no)
                VALUES (200,1,'turbidity','blue','独立第十六条','pending',?,NULL)""", (today,))
        response = self.client.get('/api/mobile/my-today',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item['id'] for item in response.json['alerts']], [200])
        self.assertEqual(response.json['summary']['pending_alerts'], 1)
        self.assertLessEqual(len(response.json['alerts']), 15)

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
        self.assertEqual(response.json['summary']['rework_items'], 0)
        site_map = {item['site_name']: item for item in response.json['sites']}
        self.assertEqual(site_map['测试站']['pending_items'], 1)
        self.assertEqual(site_map['测试站']['rework_items'], 0)
        self.assertEqual(site_map['昨日遗留站']['pending_items'], 1)
        self.assertTrue(site_map['昨日遗留站']['has_carryover'])
        self.assertEqual(site_map['昨日遗留站']['carryover_items'], 1)
        self.assertTrue(response.json['sites'][0]['has_carryover'])
        self.assertEqual([item['order_no'] for item in response.json['workorders']], ['WO-A'])
        self.assertEqual(response.json['workorders'][0]['display_title'], '甲的工单')
        self.assertEqual(response.json['workorders'][0]['display_title'], '甲的工单')
        package = response.json['work_package']
        self.assertTrue(package['has_plan'])
        self.assertIn(13, package['schedule_ids'])
        self.assertEqual(package['carryover_package_count'], 1)
        self.assertIn('昨日遗留站', [site['name'] for site in package['sites']])

    def test_future_plan_is_discoverable_but_not_today_executable(self):
        future = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
        plan_data = {future: {'sites': [1], 'inspection_items': {'1': []}}}
        with app_module.get_db() as db:
            db.execute("""INSERT INTO plan_schedules
                (id,user_id,schedule_type,status,plan_data,vehicle_days,spare_parts,work_order_ids,
                 version,remarks,period_start,period_end)
                VALUES (20,2,'weekly','approved',?,'{}','[]','[]',1,'',?,?)""",
                (json.dumps(plan_data), future, future))
        home = self.client.get('/api/mobile/my-today',
                               headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(home.status_code, 200, home.json)
        self.assertEqual(home.json['summary']['pending_items'], 2)
        upcoming = next(item for item in home.json['upcoming'] if item['schedule_id'] == 20)
        self.assertEqual(upcoming['next_date'], future)
        self.assertEqual(upcoming['site_names'], ['测试站'])
        self.assertFalse(upcoming['operable'])
        self.assertNotIn(20, home.json['work_package']['schedule_ids'])

        execution = self.client.get('/api/mobile/today-execution',
                                    headers={'Authorization': 'Bearer operator-token'})
        self.assertFalse(any(package.get('schedule_id') == 20
                             for package in execution.json['packages']))

    def test_today_execution_includes_unfinished_historical_package(self):
        response = self.client.get('/api/mobile/today-execution', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        packages = response.json['packages']
        carryovers = [item for item in packages if item['is_carryover']]
        self.assertEqual(len(carryovers), 1)
        self.assertEqual(carryovers[0]['work_date'], (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'))
        self.assertEqual([site['name'] for site in carryovers[0]['sites']], ['昨日遗留站'])
        self.assertEqual(carryovers[0]['sites'][0]['total'], 1)

    def test_completed_historical_site_stays_until_checkout_then_is_archived(self):
        db = sqlite3.connect(self.db_path)
        try:
            db.execute("UPDATE insp_plan_items SET result='normal' WHERE id=1003")
            db.execute("INSERT INTO inspection_checkins (site_id, user_id, check_time) VALUES (2, 2, datetime('now','localtime'))")
            db.commit()
        finally:
            db.close()
        response = self.client.get('/api/mobile/today-execution', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        carryover = next(item for item in response.json['packages'] if item['plan_id'] == 103)
        self.assertTrue(carryover['sites'][0]['checked_in'])
        self.assertFalse(carryover['sites'][0]['checked_out'])

        checkout = self.client.post('/api/mobile/execution-plans/103/sites/2/check-out',
                                    headers={'Authorization': 'Bearer operator-token'},
                                    json={'lat': 28.7001, 'lng': 115.8001})
        self.assertEqual(checkout.status_code, 200, checkout.json)
        response = self.client.get('/api/mobile/today-execution', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(any(item['plan_id'] == 103 for item in response.json['packages']))

    def test_homepage_excludes_packages_whose_sites_are_all_checked_out(self):
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET result='normal', check_out_time=datetime('now','localtime')
                WHERE id IN (1001,1003)""")

        home = self.client.get('/api/mobile/my-today',
                               headers={'Authorization': 'Bearer operator-token'})
        execution = self.client.get('/api/mobile/today-execution',
                                    headers={'Authorization': 'Bearer operator-token'})

        self.assertEqual(home.status_code, 200, home.json)
        self.assertFalse(home.json['work_package']['has_plan'])
        self.assertEqual(home.json['work_package']['schedule_ids'], [])
        self.assertEqual(home.json['work_package']['sites'], [])
        self.assertEqual(execution.status_code, 200, execution.json)
        closed_today = next(item for item in execution.json['packages'] if item['plan_id'] == 101)
        self.assertTrue(all(site['checked_out'] for site in closed_today['sites']))

    def test_late_rejection_keeps_checked_out_site_available_for_item_retake(self):
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items
                SET result='normal', check_out_time=datetime('now','localtime','-20 hour'),
                    review_status=3, evidence_status='supplement_required',
                    rework_required_at=datetime('now','localtime','-1 hour')
                WHERE id=1003""")
            db.execute("UPDATE insp_plans SET status='completed', completion_rate=100 WHERE id=103")
            db.execute("""INSERT INTO inspection_checkins (id,site_id,user_id,check_time)
                VALUES (99,2,2,datetime('now','localtime','-21 hour'))""")

        home = self.client.get('/api/mobile/my-today',
                               headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(home.status_code, 200, home.json)
        self.assertEqual(home.json['summary']['rework_items'], 1)
        self.assertEqual(home.json['summary']['pending_items'], 1)
        self.assertEqual(home.json['summary']['abnormal_items'], 0)
        home_site = next(item for item in home.json['sites'] if item['site_id'] == 2)
        self.assertEqual((home_site['pending_items'], home_site['rework_items']), (0, 1))
        self.assertEqual((home_site['target_plan_id'], home_site['target_item_id']), (103, 1003))

        response = self.client.get('/api/mobile/today-execution',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        package = next(item for item in response.json['packages'] if item['plan_id'] == 103)
        self.assertEqual(len(package['sites']), 1)
        self.assertTrue(package['sites'][0]['checked_in'])
        self.assertTrue(package['sites'][0]['checked_out'])

        detail = self.client.get('/api/mobile/execution-plans/103/sites/2',
                                 headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertTrue(detail.json['site']['checked_in'])
        self.assertTrue(detail.json['site']['checked_out'])
        self.assertFalse(detail.json['rework_checkin_required'])

        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items SET review_status=1,
                evidence_status='replacement_submitted' WHERE id=1003""")
        home = self.client.get('/api/mobile/my-today',
                               headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(home.status_code, 200, home.json)
        self.assertEqual(home.json['summary']['rework_items'], 0)
        self.assertFalse(any(item['site_id'] == 2 for item in home.json['sites']))
        response = self.client.get('/api/mobile/today-execution',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(any(item['plan_id'] == 103 for item in response.json['packages']))

    def test_current_field_progress_excludes_rejected_result_and_blocks_checkout(self):
        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items SET result='normal',review_status=3,
                evidence_status='supplement_required' WHERE id=1001""")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 review_status,evidence_status)
                VALUES (1010,101,1,'已提交补拍项','设备','weekly','normal','[]','active',1,
                        'replacement_submitted')""")
            db.execute("""INSERT INTO inspection_checkins
                (site_id,user_id,check_time) VALUES (1,2,datetime('now','localtime'))""")

        execution = self.client.get('/api/mobile/today-execution',
                                    headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(execution.status_code, 200, execution.json)
        package = next(item for item in execution.json['packages'] if item['plan_id'] == 101)
        site = next(item for item in package['sites'] if item['site_id'] == 1)
        self.assertEqual((site['completed'], site['total']), (1, 2))

        detail = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                 headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual((detail.json['completed'], detail.json['total']), (1, 2))

        checkout = self.client.post('/api/mobile/execution-plans/101/sites/1/check-out',
                                    headers={'Authorization': 'Bearer operator-token'},
                                    json={'lat': 28.6001, 'lng': 115.7001})
        self.assertEqual(checkout.status_code, 400, checkout.json)
        with app_module.get_db() as db:
            rows = db.execute("""SELECT check_out_time FROM insp_plan_items
                WHERE plan_id=101 AND site_id=1""").fetchall()
        self.assertTrue(all(row['check_out_time'] is None for row in rows))

    def test_homepage_rework_todos_combine_with_pending_and_keep_scope(self):
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 review_status,evidence_status,rework_required_at)
                VALUES (1004,101,1,'同站补拍项','设备','weekly','normal','[]','active',3,
                        'supplement_required',datetime('now','localtime','-1 hour'))""")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 review_status,evidence_status,rework_required_at)
                VALUES (1005,101,1,'已重新提交项','设备','weekly','normal','[]','active',1,
                        'replacement_submitted',datetime('now','localtime','-2 hour'))""")
            db.execute("""UPDATE insp_plan_items SET result='normal',review_status=3,
                evidence_status='supplement_required',rework_required_at=datetime('now','localtime','-1 hour')
                WHERE id=1003""")
            db.execute("""UPDATE insp_plan_items SET result='normal',review_status=3,
                evidence_status='supplement_required' WHERE id=1002""")
            db.execute("INSERT INTO sites VALUES (3,'无权限站','S-03',28.8,115.9,'water_quality')")
            db.execute("""INSERT INTO plan_schedules
                (id,user_id,schedule_type,status,plan_data,vehicle_days,spare_parts,work_order_ids,
                 version,remarks,period_start,period_end)
                VALUES (14,2,'weekly','approved','{}','{}','[]','[]',1,'',?,?)""",
                (yesterday, today))
            db.execute("""INSERT INTO insp_plans VALUES
                (104,'无权限站计划',2,14,?,'completed',100)""", (yesterday,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 review_status,evidence_status,rework_required_at)
                VALUES (1006,104,3,'无权限补拍项','设备','weekly','normal','[]','active',3,
                        'supplement_required',datetime('now','localtime','-1 hour'))""")

        response = self.client.get('/api/mobile/my-today',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['summary']['pending_items'], 1)
        self.assertEqual(response.json['summary']['rework_items'], 2)
        self.assertEqual(response.json['summary']['abnormal_items'], 0)
        site_map = {item['site_id']: item for item in response.json['sites']}
        self.assertEqual((site_map[1]['pending_items'], site_map[1]['rework_items']), (1, 1))
        self.assertEqual((site_map[2]['pending_items'], site_map[2]['rework_items']), (0, 1))
        self.assertNotIn(3, site_map)

    def test_homepage_same_site_rework_targets_its_execution_package(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plans VALUES
                (105,'同站历史返场计划',2,11,?,'completed',100)""", (yesterday,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 review_status,evidence_status,rework_required_at)
                VALUES (1007,105,1,'同站历史补拍项','设备','weekly','normal','[]','active',3,
                        'supplement_required',datetime('now','localtime','-1 hour'))""")
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 review_status,evidence_status,rework_required_at)
                VALUES (1008,105,1,'同站已重新提交项','设备','weekly',NULL,'[]','active',1,
                        'replacement_submitted',datetime('now','localtime','-2 hour'))""")

        response = self.client.get('/api/mobile/my-today',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        site = next(item for item in response.json['sites'] if item['site_id'] == 1)
        self.assertEqual((site['pending_items'], site['rework_items']), (1, 1))
        self.assertEqual(site['target_plan_id'], 105)
        self.assertEqual(site['target_item_id'], 1007)

        with app_module.get_db() as db:
            db.execute("""UPDATE insp_plan_items SET result=NULL, review_status=3,
                evidence_status='supplement_required' WHERE id=1007""")
        response = self.client.get('/api/mobile/my-today',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        site = next(item for item in response.json['sites'] if item['site_id'] == 1)
        self.assertEqual((site['pending_items'], site['rework_items']), (1, 1))
        self.assertEqual(response.json['summary']['pending_items'], 2)
        self.assertEqual(response.json['summary']['rework_items'], 1)

    def test_legacy_unscheduled_rework_is_reachable_from_home_and_execution(self):
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plans VALUES
                (106,'旧有效返场计划',2,NULL,?,'completed',100)""", (yesterday,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 check_out_time,review_status,evidence_status,rework_required_at)
                VALUES (1009,106,1,'旧计划补拍项','设备','weekly','normal','[]','active',
                        ?,3,'supplement_required',datetime('now','localtime','-1 hour'))""",
                (yesterday + ' 18:00:00',))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status)
                VALUES (1014,106,1,'同站普通待检项','设备','weekly',NULL,'[]','active')""")
            db.executemany('INSERT INTO insp_plans VALUES (?,?,?,?,?,?,?)', [
                (107, '旧草稿计划', 2, None, today, 'draft', 0),
                (108, '旧待提交计划', 2, None, today, 'submitted', 0),
                (109, '旧取消计划', 2, None, today, 'cancelled', 0),
            ])
            db.executemany("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status)
                VALUES (?,?,?,?,?,'weekly',NULL,'[]','active')""", [
                (1010, 107, 1, '草稿项', '设备'),
                (1011, 108, 1, '待提交项', '设备'),
                (1012, 109, 1, '取消项', '设备'),
            ])
            db.execute("""INSERT INTO plan_schedules
                (id,user_id,schedule_type,status,plan_data,vehicle_days,spare_parts,work_order_ids,
                 version,remarks,period_start,period_end)
                VALUES (14,2,'weekly','submitted','{}','{}','[]','[]',1,'',?,?)""",
                (today, today))
            db.execute("""INSERT INTO insp_plans VALUES
                (110,'有关联未批准计划',2,14,?,'active',0)""", (today,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status)
                VALUES (1013,110,1,'未批准排程项','设备','weekly',NULL,'[]','active')""")

        home = self.client.get('/api/mobile/my-today',
                               headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(home.status_code, 200, home.json)
        site = next(item for item in home.json['sites'] if item['site_id'] == 1)
        self.assertEqual(site['target_plan_id'], 106)
        self.assertEqual(site['target_item_id'], 1009)

        execution = self.client.get('/api/mobile/today-execution',
                                    headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(execution.status_code, 200, execution.json)
        packages = {item['plan_id']: item for item in execution.json['packages']}
        self.assertIn(101, packages)
        self.assertIn(106, packages)
        self.assertNotIn(107, packages)
        self.assertNotIn(108, packages)
        self.assertNotIn(109, packages)
        self.assertNotIn(110, packages)
        legacy = packages[106]
        self.assertIsNone(legacy['schedule_id'])
        self.assertEqual(legacy['resource_parts'], [])
        self.assertIsNone(legacy['vehicle'])
        self.assertEqual(legacy['sites'][0]['site_id'], 1)
        self.assertEqual(legacy['sites'][0]['rework_items'], 1)

        detail = self.client.get('/api/mobile/execution-plans/106/sites/1',
                                 headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(detail.status_code, 200, detail.json)
        item_ids = [item['item_id'] for category in detail.json['categories']
                    for item in category['items']]
        self.assertEqual(item_ids, [1014, 1009])

        rework_detail = self.client.get('/api/mobile/execution-plans/106/sites/1?scope=rework',
                                        headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(rework_detail.status_code, 200, rework_detail.json)
        rework_item_ids = [item['item_id'] for category in rework_detail.json['categories']
                           for item in category['items']]
        self.assertEqual(rework_item_ids, [1009])
        self.assertTrue(rework_detail.json['rework_only'])

        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 review_status,evidence_status,rework_required_at)
                VALUES (1015,106,1,'旧计划第二补拍项','设备','weekly','normal','[]','active',3,
                        'supplement_required',datetime('now','localtime'))""")
        multi_rework = self.client.get('/api/mobile/execution-plans/106/sites/1?scope=rework',
                                       headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(multi_rework.status_code, 200, multi_rework.json)
        self.assertEqual({item['item_id'] for category in multi_rework.json['categories']
                          for item in category['items']}, {1009, 1015})

        with app_module.get_db() as db:
            db.execute('DELETE FROM user_sites WHERE user_id=2 AND site_id=1')

        home = self.client.get('/api/mobile/my-today',
                               headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(home.status_code, 200, home.json)
        self.assertFalse(any(item['site_id'] == 1 for item in home.json['sites']))

        execution = self.client.get('/api/mobile/today-execution',
                                    headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(execution.status_code, 200, execution.json)
        visible_plan_ids = {item['plan_id'] for item in execution.json['packages']}
        self.assertNotIn(101, visible_plan_ids)
        self.assertNotIn(106, visible_plan_ids)

        for plan_id in (101, 106):
            detail = self.client.get(f'/api/mobile/execution-plans/{plan_id}/sites/1',
                                     headers={'Authorization': 'Bearer operator-token'})
            self.assertEqual(detail.status_code, 404, detail.json)

    def test_admin_operator_home_uses_field_site_scope_for_tasks_and_stats(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        headers = {'Authorization': 'Bearer admin-operator-token'}
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status)
                VALUES (1014,101,1,'异常统计项','设备','weekly','abnormal','[]','active')""")
            db.execute("""INSERT INTO insp_plans VALUES
                (106,'旧有效返场计划',2,NULL,?,'completed',100)""", (yesterday,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status,
                 check_out_time,review_status,evidence_status,rework_required_at)
                VALUES (1009,106,1,'旧计划补拍项','设备','weekly','normal','[]','active',
                        ?,3,'supplement_required',datetime('now','localtime','-1 hour'))""",
                (yesterday + ' 18:00:00',))
            db.execute('DELETE FROM user_sites WHERE user_id=2')

        home = self.client.get('/api/mobile/my-today', headers=headers)
        self.assertEqual(home.status_code, 200, home.json)
        self.assertEqual(home.json['sites'], [])
        self.assertEqual(home.json['summary']['total_sites'], 0)
        self.assertEqual(home.json['summary']['total_items'], 0)
        self.assertEqual(home.json['summary']['completed_items'], 0)
        self.assertEqual(home.json['summary']['pending_items'], 0)
        self.assertEqual(home.json['summary']['rework_items'], 0)
        self.assertEqual(home.json['summary']['abnormal_items'], 0)
        self.assertEqual(home.json['work_package']['sites'], [])
        self.assertFalse(home.json['work_package']['has_plan'])

        execution = self.client.get('/api/mobile/today-execution', headers=headers)
        self.assertEqual(execution.status_code, 200, execution.json)
        self.assertFalse(any(package['plan_id'] in (101, 106)
                             for package in execution.json['packages']))

        with app_module.get_db() as db:
            db.execute('INSERT INTO user_sites VALUES (2, 1)')

        home = self.client.get('/api/mobile/my-today', headers=headers)
        self.assertEqual(home.status_code, 200, home.json)
        self.assertEqual([site['site_id'] for site in home.json['sites']], [1])
        site = home.json['sites'][0]
        self.assertEqual(site['target_plan_id'], 106)
        self.assertEqual(site['target_item_id'], 1009)
        self.assertEqual(home.json['summary']['total_items'], 2)
        self.assertEqual(home.json['summary']['completed_items'], 1)
        self.assertEqual(home.json['summary']['pending_items'], 1)
        self.assertEqual(home.json['summary']['rework_items'], 1)
        self.assertEqual(home.json['summary']['abnormal_items'], 1)

        execution = self.client.get('/api/mobile/today-execution', headers=headers)
        self.assertEqual(execution.status_code, 200, execution.json)
        visible_plan_ids = {package['plan_id'] for package in execution.json['packages']}
        self.assertIn(101, visible_plan_ids)
        self.assertIn(106, visible_plan_ids)
        for plan_id in (101, 106):
            detail = self.client.get(f'/api/mobile/execution-plans/{plan_id}/sites/1',
                                     headers=headers)
            self.assertEqual(detail.status_code, 200, detail.json)

    def test_today_execution_keeps_one_vehicle_trip_until_plan_end(self):
        with app_module.get_db() as db:
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            db.execute('''INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,start_at,end_at,destination,status,reason)
                VALUES (10,1,2,?,?,?,?,?)''', (
                    yesterday + ' 08:00:00', tomorrow + ' 18:00:00',
                    '巡检', 'approved', '巡检计划#110用车',
                ))
        response = self.client.get('/api/mobile/today-execution', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        today_package = next(item for item in response.json['packages'] if item['plan_id'] == 101)
        self.assertEqual(today_package['vehicle_application_id'], 1)
        self.assertEqual(today_package['vehicle_use']['id'], 1)
        self.assertFalse(today_package['vehicle_can_return'])
        self.assertTrue(today_package['arrival_gate']['allowed'])
        detail = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                 headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertTrue(detail.json['arrival_gate']['allowed'])

    def test_today_execution_vehicle_gate_does_not_treat_acknowledgement_as_checkout(self):
        headers = {'Authorization': 'Bearer operator-token'}
        with app_module.get_db() as db:
            db.execute('DELETE FROM vehicle_use_records WHERE application_id=1')
            db.execute('''INSERT INTO plan_departure_confirmations
                (schedule_id,user_id,work_date,vehicle_confirmed,parts_confirmed,note,confirmed_at)
                VALUES (11,2,date('now','localtime'),1,1,'已核对',datetime('now','localtime'))''')
        response = self.client.get('/api/mobile/today-execution', headers=headers)
        package = next(item for item in response.json['packages'] if item['plan_id'] == 101)
        self.assertEqual(package['departure_confirmation']['vehicle_confirmed'], 1)
        self.assertFalse(package['arrival_gate']['allowed'])
        self.assertEqual(package['arrival_gate']['code'], 'VEHICLE_CHECKOUT_REQUIRED')

        detail = self.client.get('/api/mobile/execution-plans/101/sites/1', headers=headers)
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['arrival_gate'], package['arrival_gate'])

        with app_module.get_db() as db:
            db.execute("UPDATE plan_schedules SET vehicle_days='{}' WHERE id=11")
        no_vehicle = self.client.get('/api/mobile/today-execution', headers=headers)
        package = next(item for item in no_vehicle.json['packages'] if item['plan_id'] == 101)
        self.assertTrue(package['arrival_gate']['allowed'])

    def test_today_execution_projects_only_current_rework_application_and_use(self):
        today = datetime.now().strftime('%Y-%m-%d')
        headers = {'Authorization': 'Bearer operator-token'}
        with app_module.get_db() as db:
            db.execute("ALTER TABLE plan_schedules ADD COLUMN vehicle_exception_reason TEXT DEFAULT ''")
            db.execute("UPDATE plan_schedules SET vehicle_exception_reason='原计划步行执行' WHERE id=11")
            db.execute('ALTER TABLE insp_plans ADD COLUMN rework_of_plan_id INTEGER')
            db.execute("ALTER TABLE insp_plans ADD COLUMN resource_state TEXT DEFAULT 'ready'")
            db.execute('ALTER TABLE vehicle_applications ADD COLUMN no_vehicle_required INTEGER DEFAULT 0')
            db.execute("ALTER TABLE vehicle_applications ADD COLUMN vehicle_exception_reason TEXT DEFAULT ''")
            db.execute('ALTER TABLE vehicle_applications ADD COLUMN rework_plan_id INTEGER')
            db.execute("ALTER TABLE vehicle_applications ADD COLUMN reject_reason TEXT DEFAULT ''")
            db.execute("""INSERT INTO insp_plans
                (id,plan_name,assignee_id,plan_schedule_id,generate_date,status,completion_rate,
                 rework_of_plan_id,resource_state)
                VALUES (120,'整改返场',2,11,?,'active',0,101,'arrangement_required')""",
                (today,))
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,photo_urls,execution_status)
                VALUES (1200,120,1,'补拍项','设备','weekly',NULL,'[]','active')""")
            db.execute("INSERT INTO vehicles VALUES (2,'赣A00002','整改车','idle',8000,'gasoline')")

        response = self.client.get('/api/mobile/today-execution', headers=headers)
        normal = next(item for item in response.json['packages'] if item['plan_id'] == 101)
        self.assertEqual(normal['vehicle_exception_reason'], '原计划步行执行')
        rework = next(item for item in response.json['packages'] if item['plan_id'] == 120)
        self.assertIsNone(rework['vehicle'])
        self.assertEqual(rework['vehicle_exception_reason'], '')
        self.assertIsNone(rework['vehicle_application_id'])
        self.assertEqual(rework['rework_resource']['state'], 'arrangement_required')
        self.assertFalse(rework['rework_resource']['arrival_allowed'])
        detail = self.client.get('/api/mobile/execution-plans/120/sites/1', headers=headers)
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertFalse(detail.json['arrival_gate']['allowed'])
        blocked_checkout = self.client.post('/api/mobile/execution-plans/120/sites/1/check-out', headers=headers,
                                            json={'lat': 28.6, 'lng': 115.7})
        self.assertEqual(blocked_checkout.status_code, 403, blocked_checkout.json)

        with app_module.get_db() as db:
            db.execute("""INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,start_at,end_at,destination,status,reason,
                 no_vehicle_required,vehicle_exception_reason,rework_plan_id)
                VALUES (20,2,2,?,?,?,'pending','整改补检#120用车',0,'',120)""",
                (today + ' 08:00:00', today + ' 18:00:00', '整改补检'))
            db.execute("UPDATE insp_plans SET resource_state='pending_approval' WHERE id=120")

        response = self.client.get('/api/mobile/today-execution', headers=headers)
        rework = next(item for item in response.json['packages'] if item['plan_id'] == 120)
        self.assertEqual(rework['vehicle']['id'], 2)
        self.assertEqual(rework['vehicle_application_id'], 20)
        self.assertIsNone(rework['vehicle_use'])
        self.assertEqual(rework['rework_resource']['state'], 'pending_approval')
        self.assertFalse(rework['rework_resource']['arrival_allowed'])
        self.assertNotEqual(rework['vehicle_application_id'], 1,
                            'the original schedule application must not leak into rework')

        with app_module.get_db() as db:
            db.execute("UPDATE vehicle_applications SET status='approved' WHERE id=20")
            db.execute("UPDATE insp_plans SET resource_state='ready' WHERE id=120")
        response = self.client.get('/api/mobile/today-execution', headers=headers)
        rework = next(item for item in response.json['packages'] if item['plan_id'] == 120)
        self.assertEqual(rework['rework_resource']['state'], 'vehicle_checkout_required')
        self.assertFalse(rework['rework_resource']['arrival_allowed'])

        with app_module.get_db() as db:
            db.execute("""INSERT INTO vehicle_use_records
                (id,application_id,start_mileage,end_mileage,returned_at,status)
                VALUES (20,20,8000,NULL,NULL,'checked_out')""")
        response = self.client.get('/api/mobile/today-execution', headers=headers)
        rework = next(item for item in response.json['packages'] if item['plan_id'] == 120)
        self.assertEqual(rework['vehicle_use']['id'], 20)
        self.assertEqual(rework['rework_resource']['state'], 'vehicle_checked_out')
        self.assertTrue(rework['rework_resource']['arrival_allowed'])

        with app_module.get_db() as db:
            db.execute('DELETE FROM vehicle_use_records WHERE application_id=20')
            db.execute("""UPDATE vehicle_applications SET vehicle_id=NULL,no_vehicle_required=1,
                vehicle_exception_reason='站点步行可达' WHERE id=20""")
        response = self.client.get('/api/mobile/today-execution', headers=headers)
        rework = next(item for item in response.json['packages'] if item['plan_id'] == 120)
        self.assertIsNone(rework['vehicle'])
        self.assertEqual(rework['vehicle_exception_reason'], '站点步行可达')
        self.assertEqual(rework['rework_resource']['state'], 'no_vehicle_approved')
        self.assertTrue(rework['rework_resource']['arrival_allowed'])

        with app_module.get_db() as db:
            db.execute("""UPDATE vehicle_applications SET status='rejected',no_vehicle_required=0,
                vehicle_exception_reason='',reject_reason='安排不合理' WHERE id=20""")
            db.execute("UPDATE insp_plans SET resource_state='arrangement_required' WHERE id=120")
        response = self.client.get('/api/mobile/today-execution', headers=headers)
        rework = next(item for item in response.json['packages'] if item['plan_id'] == 120)
        self.assertEqual(rework['rework_resource']['state'], 'rejected')
        self.assertFalse(rework['rework_resource']['arrival_allowed'])
        self.assertIn('安排不合理', rework['rework_resource']['gate_message'])

    def test_execution_site_returns_readable_category_names(self):
        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        category = response.json['categories'][0]
        self.assertEqual(category['category'], '设备')
        self.assertEqual(category['category_cn'], '设备检查')

    def test_execution_site_projects_calibration_capability_from_category(self):
        with app_module.get_db() as db:
            db.execute("""INSERT INTO insp_plan_items
                (id,plan_id,site_id,item_name,category,frequency,result,calibrator,
                 calibration_values,photo_urls,remark,check_time,execution_status,check_out_time)
                VALUES (1004,101,1,'pH 标准液校准','qaqc_calibration','weekly',NULL,'','',
                        '[]','','','active',NULL)""")
        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        items = {
            item['item_name']: item
            for category in response.json['categories']
            for item in category['items']
        }
        self.assertIs(items['甲的检查项']['requiresCalibration'], False)
        self.assertIs(items['pH 标准液校准']['requiresCalibration'], True)

    def test_execution_site_projects_exact_replacement_photo_requirement(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN required_photos INTEGER DEFAULT 0")
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN actual_photos INTEGER DEFAULT 0")
            current = ['/uploads/keep-a.jpg', '/uploads/rejected.jpg',
                       '/uploads/keep-b.jpg', '/uploads/keep-c.jpg']
            db.execute("""UPDATE insp_plan_items SET result='normal', required_photos=4,
                actual_photos=4, review_status=3, evidence_status='supplement_required',
                rework_required_at=datetime('now','localtime'), photo_urls=? WHERE id=1001""",
                       (json.dumps(current),))
            db.executemany("""INSERT INTO operation_attachments
                (id,stored_path,source_type,source_id,site_id,uploader_id,item_id,
                 review_status,evidence_qualification,extra_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", [
                    (301, '/uploads/keep-a.jpg', 'inspection', 1001, 1, 2, 1001, 'approved', 'qualified', '{"material_role":"formal"}'),
                    (302, '/uploads/rejected.jpg', 'inspection', 1001, 1, 2, 1001, 'rejected', 'qualified', '{"material_role":"formal"}'),
                    (303, '/uploads/keep-b.jpg', 'inspection', 1001, 1, 2, 1001, 'approved', 'qualified', '{"material_role":"formal"}'),
                    (304, '/uploads/keep-c.jpg', 'inspection', 1001, 1, 2, 1001, 'approved', 'qualified', '{"material_role":"formal"}'),
                    (305, '/uploads/keep-a.jpg', 'inspection', 1001, 1, 2, 1001, 'approved', 'qualified', '{"material_role":"formal"}'),
                    (306, '/uploads/voided.jpg', 'inspection', 1001, 1, 2, 1001, 'voided', 'qualified', '{"material_role":"formal"}'),
                    (307, '/uploads/superseded.jpg', 'inspection', 1001, 1, 2, 1001, 'superseded', 'qualified', '{"material_role":"formal"}'),
                ])

        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['required_photos'], 4)
        self.assertEqual(item['current_photo_urls'],
                         ['/uploads/keep-a.jpg', '/uploads/keep-b.jpg', '/uploads/keep-c.jpg'])
        self.assertEqual(item['retained_photo_count'], 3)
        self.assertEqual(item['replacement_required_photos'], 1)

        with app_module.get_db() as db:
            db.execute("UPDATE insp_plan_items SET photo_urls='[]', actual_photos=0 WHERE id=1001")
        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['current_photo_urls'],
                         ['/uploads/keep-a.jpg', '/uploads/keep-b.jpg', '/uploads/keep-c.jpg'])
        self.assertEqual(item['retained_photo_count'], 3)
        self.assertEqual(item['replacement_required_photos'], 1)

        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET is_deleted=1 WHERE id IN (303,304,305)")
        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['current_photo_urls'], ['/uploads/keep-a.jpg'])
        self.assertEqual(item['retained_photo_count'], 1)
        self.assertEqual(item['replacement_required_photos'], 3)

        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET is_deleted=1 WHERE id=301")
        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['current_photo_urls'], [])
        self.assertEqual(item['retained_photo_count'], 0)
        self.assertEqual(item['replacement_required_photos'], 4)

    def test_execution_site_recovers_explicit_legacy_attachment_link(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN required_photos INTEGER DEFAULT 0")
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN actual_photos INTEGER DEFAULT 0")
            db.execute("""UPDATE insp_plan_items SET result='normal', required_photos=4,
                actual_photos=0, review_status=3, evidence_status='supplement_required',
                rework_required_at=datetime('now','localtime'), photo_urls='[]' WHERE id=1001""")
            db.executemany("""INSERT INTO operation_attachments
                (id,stored_path,source_type,source_id,site_id,uploader_id,item_id,
                 review_status,evidence_qualification,extra_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", [
                    (401, '/uploads/retained.jpg', 'site_photo', 0, 1, 2, 0,
                     'approved', 'qualified', '{"plan_id":101,"item_id":1001,"material_role":"formal"}'),
                    (402, '/uploads/pending.jpg', 'site_photo', 0, 1, 2, 0,
                     'approved', 'qualified', '{"plan_id":101,"item_id":1001,"material_role":"pending_inspection"}'),
                    (403, '/uploads/other-plan.jpg', 'site_photo', 0, 1, 2, 0,
                     'approved', 'qualified', '{"plan_id":103,"item_id":1001,"material_role":"formal"}'),
                    (404, '/uploads/rejected.jpg', 'site_photo', 0, 1, 2, 0,
                     'rejected', 'qualified', '{"plan_id":101,"item_id":1001,"material_role":"formal"}'),
                    (405, '/uploads/retained.jpg', 'site_photo', 0, 1, 2, 0,
                     'approved', 'qualified', '{"plan_id":101,"item_id":1001,"material_role":"formal"}'),
                ])

        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['current_photo_urls'], ['/uploads/retained.jpg'])
        self.assertEqual(item['retained_photo_count'], 1)
        self.assertEqual(item['replacement_required_photos'], 3)

    def test_execution_site_blocks_retake_until_current_photo_review_finishes(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN required_photos INTEGER DEFAULT 0")
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN actual_photos INTEGER DEFAULT 0")
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN review_comment TEXT DEFAULT ''")
            db.execute("""UPDATE insp_plan_items SET result='normal', required_photos=4,
                actual_photos=0, review_status=3, evidence_status='supplement_required',
                review_comment='1', rework_required_at=datetime('now','localtime'),
                photo_urls='["/uploads/pending-review.jpg"]'
                WHERE id=1001""")
            db.execute("""INSERT INTO operation_attachments
                (id,stored_path,source_type,source_id,site_id,uploader_id,item_id,
                 review_status,evidence_qualification,extra_json)
                VALUES (409,'/uploads/pending-review.jpg','inspection',1001,1,2,1001,
                        'pending','qualified','{\"plan_id\":101,\"item_id\":1001,\"material_role\":\"formal\"}')""")

        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['replacement_photo_status'], 'pending_review')
        self.assertFalse(item['replacement_submission_allowed'])
        self.assertIsNone(item['replacement_required_photos'])
        self.assertEqual(item['replacement_block_reason'], '原照片待审核，审核完成后才能补拍')
        self.assertEqual(item['supplement_display_message'], '原照片待审核，审核完成后才能补拍')

        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET review_status='approved' WHERE id=409")
        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['replacement_photo_status'], 'ready')
        self.assertTrue(item['replacement_submission_allowed'])
        self.assertEqual(item['retained_photo_count'], 1)
        self.assertEqual(item['replacement_required_photos'], 3)
        self.assertEqual(item['supplement_display_message'], '审核说明不完整，请联系审核人')

        for status in ('rejected', 'voided'):
            with app_module.get_db() as db:
                db.execute("UPDATE operation_attachments SET review_status=? WHERE id=409", (status,))
            response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                       headers={'Authorization': 'Bearer operator-token'})
            item = response.json['categories'][0]['items'][0]
            self.assertEqual(item['replacement_photo_status'], 'ready')
            self.assertEqual(item['retained_photo_count'], 0)
            self.assertEqual(item['replacement_required_photos'], 4)

        with app_module.get_db() as db:
            db.execute("UPDATE operation_attachments SET review_status='approved', is_deleted=1 WHERE id=409")
        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['retained_photo_count'], 0)
        self.assertEqual(item['replacement_required_photos'], 4)

    def test_execution_site_ignores_historical_pending_outside_current_snapshot(self):
        with app_module.get_db() as db:
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN required_photos INTEGER DEFAULT 0")
            db.execute("ALTER TABLE insp_plan_items ADD COLUMN actual_photos INTEGER DEFAULT 0")
            db.execute("""UPDATE insp_plan_items SET result='normal', required_photos=4,
                actual_photos=1, review_status=3, evidence_status='supplement_required',
                rework_required_at=datetime('now','localtime'),
                photo_urls='[\"/uploads/current-approved.jpg\"]' WHERE id=1001""")
            db.executemany("""INSERT INTO operation_attachments
                (id,stored_path,source_type,source_id,site_id,uploader_id,item_id,
                 review_status,evidence_qualification,extra_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", [
                    (411, '/uploads/current-approved.jpg', 'inspection', 1001, 1, 2, 1001,
                     'approved', 'qualified', '{"material_role":"formal"}'),
                    (412, '/uploads/historical-pending.jpg', 'inspection', 1001, 1, 2, 1001,
                     'pending', 'qualified', '{"material_role":"formal"}'),
                ])

        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['replacement_photo_status'], 'ready')
        self.assertTrue(item['replacement_submission_allowed'])
        self.assertEqual(item['retained_photo_count'], 1)
        self.assertEqual(item['replacement_required_photos'], 3)
        self.assertEqual(item['original_formal_pending_photo_urls'], [])

    def test_execution_site_recovers_only_current_operator_pending_inspection_photos(self):
        rows = [
            (201, '/uploads/site_photos/current.jpg', 'site_photo', 0, 1, 2, 0,
             'pending', 'qualified', '{"material_role":"pending_inspection","plan_id":101,"item_id":1001}'),
            (202, '/uploads/site_photos/other-user.jpg', 'site_photo', 0, 1, 3, 0,
             'pending', 'qualified', '{"material_role":"pending_inspection","plan_id":101,"item_id":1001}'),
            (203, '/uploads/site_photos/other-plan.jpg', 'site_photo', 0, 1, 2, 0,
             'pending', 'qualified', '{"material_role":"pending_inspection","plan_id":103,"item_id":1001}'),
            (204, '/uploads/site_photos/other-site.jpg', 'site_photo', 0, 2, 2, 0,
             'pending', 'qualified', '{"material_role":"pending_inspection","plan_id":101,"item_id":1001}'),
            (205, '/uploads/site_photos/other-item.jpg', 'site_photo', 0, 1, 2, 0,
             'pending', 'qualified', '{"material_role":"pending_inspection","plan_id":101,"item_id":9999}'),
            (206, '/uploads/site_photos/deleted.jpg', 'site_photo', 0, 1, 2, 1,
             'pending', 'qualified', '{"material_role":"pending_inspection","plan_id":101,"item_id":1001}'),
            (207, '/uploads/site_photos/supplement.jpg', 'site_photo', 0, 1, 2, 0,
             'pending', 'qualified', '{"material_role":"supplement","plan_id":101,"item_id":1001}'),
            (208, '/uploads/site_photos/formal.jpg', 'inspection', 1001, 1, 2, 0,
             'pending', 'qualified', '{"material_role":"formal","plan_id":101,"item_id":1001}'),
            (209, '/uploads/site_photos/rejected.jpg', 'site_photo', 0, 1, 2, 0,
             'rejected', 'qualified', '{"material_role":"pending_inspection","plan_id":101,"item_id":1001}'),
            (210, '/uploads/site_photos/unqualified.jpg', 'site_photo', 0, 1, 2, 0,
             'pending', 'rejected', '{"material_role":"pending_inspection","plan_id":101,"item_id":1001}'),
        ]
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO operation_attachments
                (id,stored_path,source_type,source_id,site_id,uploader_id,is_deleted,
                 review_status,evidence_qualification,extra_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)

        response = self.client.get('/api/mobile/execution-plans/101/sites/1',
                                   headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json['categories'][0]['items'][0]
        self.assertEqual(item['pending_photo_urls'], ['/uploads/site_photos/current.jpg'])

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
