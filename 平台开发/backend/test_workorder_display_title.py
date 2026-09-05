import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class WorkorderDisplayTitleProjectionTest(unittest.TestCase):
    def test_source_specific_prefixes_and_safe_fallbacks(self):
        cases = [
            ({'source': 'manual_report', 'title': '【人工上报】感官异常'}, '感官异常'),
            ({'source': 'auto', 'title': '[自动] 设备离线'}, '设备离线'),
            ({'source': 'alert_convert', 'title': '[复核] 浊度异常'}, '浊度异常'),
            ({'source': 'auto', 'title': '[告警转] pH异常'}, 'pH异常'),
            ({'source': 'inspection', 'title': '【巡检异常】药剂余量不足'}, '药剂余量不足'),
            ({'source': 'hotline', 'title': '[热线]护栏损坏'}, '护栏损坏'),
            ({'source': 'escalation', 'title': '【告警升级】设备状态：离线'}, '设备状态：离线'),
            ({'source': 'manual', 'title': '【现场确认】水泵异响'}, '【现场确认】水泵异响'),
            ({'source': 'manual', 'title': '[自动] 用户原始标题'}, '[自动] 用户原始标题'),
            ({'source': 'auto', 'title': '[热线]用户原始标题'}, '[热线]用户原始标题'),
            ({'source': 'auto', 'title': '', 'event_type': 'device_status'}, '设备状态'),
            ({'source': 'inspection', 'title': '', 'event_type': '设备异常'}, '设备异常'),
            ({'source': 'auto', 'title': '', 'event_type': 'unknown_type'}, '工单事项'),
            ({'source': '', 'title': '', 'event_type': ''}, '工单事项'),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(app_module._workorder_display_title(payload), expected)

    def test_generated_titles_never_store_source_prefixes(self):
        cases = [
            ('auto', '[自动] 设备离线', 'device_status', '设备离线'),
            ('alert_convert', '[复核] 浊度异常', '告警复核转工单', '浊度异常'),
            ('inspection', '【巡检异常】', 'equipment', '设备异常'),
            ('hotline', '[热线]', '设施维修', '设施维修'),
        ]
        for source, title, event_type, expected in cases:
            with self.subTest(source=source):
                generated = app_module._generated_workorder_title(source, title, event_type)
                self.assertEqual(generated, expected)
                self.assertNotRegex(generated, r'^(?:\[自动\]|\[复核\]|\[告警转\]|\[热线\]|【人工上报】|【巡检异常】|【告警升级】)')


class WorkorderTitleApiTest(unittest.TestCase):
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
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens['operator-token'] = {
            'id': 1, 'username': 'operator', 'login_name': 'operator',
            'real_name': '现场人员', 'role': 'operator', 'roles': ['operator'],
        }
        with temporary_db() as db:
            db.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, login_name TEXT,
                    real_name TEXT, role TEXT, status TEXT
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE,
                    site_id INTEGER, source TEXT, event_type TEXT, level TEXT,
                    title TEXT, description TEXT, images TEXT, assignee TEXT,
                    status TEXT, sla_deadline TEXT, related_alert_id INTEGER,
                    check_in_time TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, status TEXT, related_order_no TEXT
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT,
                    source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT
                );
                INSERT INTO users VALUES (1,'operator','operator','现场人员','operator','active');
                INSERT INTO user_roles VALUES (1,'operator');
                INSERT INTO user_sites VALUES (1,10);
                INSERT INTO sites VALUES (10,'测试站');
                INSERT INTO work_orders
                    (order_no,site_id,source,event_type,level,title,description,assignee,status)
                    VALUES ('WO-OLD',10,'auto','device_status','normal',
                            '[自动] 设备离线','历史事实','现场人员','pending');
            """)
        self.client = app_module.app.test_client()
        self.headers = {'Authorization': 'Bearer operator-token'}

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    def count_orders(self):
        db = sqlite3.connect(self.db_path)
        try:
            return db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0]
        finally:
            db.close()

    def test_list_preserves_audit_title_and_adds_authoritative_display_title(self):
        response = self.client.get('/api/workorders', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        item = response.json[0]
        self.assertEqual(item['title'], '[自动] 设备离线')
        self.assertEqual(item['display_title'], '设备离线')

    def test_manual_title_validation_is_normalized_and_zero_write_on_failure(self):
        before = self.count_orders()
        invalid = [
            ('  \n\t ', '请填写工单标题'),
            ('事项' * 21, '工单标题最长40个字符'),
        ]
        for title, error in invalid:
            response = self.client.post('/api/workorders', headers=self.headers, json={
                'site_id': 10, 'source': 'manual', 'title': title,
            })
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(response.json['error'], error)
            self.assertEqual(self.count_orders(), before)

        created = self.client.post('/api/workorders', headers=self.headers, json={
            'site_id': 10, 'source': 'manual', 'event_type': '现场处置',
            'title': '  TEST\t【保留】\n  水泵异响  ',
        })
        self.assertEqual(created.status_code, 200, created.json)
        db = sqlite3.connect(self.db_path)
        try:
            stored = db.execute(
                'SELECT title FROM work_orders WHERE order_no=?',
                (created.json['order_no'],),
            ).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(stored, 'TEST 【保留】 水泵异响')


if __name__ == '__main__':
    unittest.main()
