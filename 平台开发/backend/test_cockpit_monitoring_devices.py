import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class CockpitMonitoringDevicesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temporary.close()
        self.db_path = temporary.name
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
            'operator-token': {'id': 2, 'role': 'operator', 'roles': ['operator']},
            'admin-token': {'id': 1, 'role': 'admin', 'roles': ['admin']},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, code TEXT, name TEXT);
                CREATE TABLE device_shadows (
                    id INTEGER PRIMARY KEY, site_id INTEGER, device_code TEXT,
                    device_name TEXT, device_type TEXT, device_model TEXT,
                    status TEXT, management_scope TEXT, monitoring_enabled INTEGER,
                    last_data_time TEXT
                );
                INSERT INTO user_sites VALUES (2, 1);
                INSERT INTO sites VALUES (1, 'S-1', '授权站'), (2, 'S-2', '无权站');
                INSERT INTO device_shadows VALUES
                    (1,1,'D-1','真实采集设备','sensor','M1','online','managed',1,'2026-08-13 10:00:00'),
                    (2,1,'D-2','无数据设备','sensor','M2','offline','managed',1,NULL),
                    (3,1,'D-3','未启用设备','sensor','M3','online','managed',0,'2026-08-13 10:00:00'),
                    (4,1,'D-4','退役设备','sensor','M4','offline','retired',1,'2026-08-13 10:00:00'),
                    (5,2,'D-5','其他站设备','sensor','M5','online','managed',1,'2026-08-13 10:00:00');
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

    def test_operator_receives_only_authorized_real_monitoring_devices(self):
        response = self.client.get('/api/devices/monitoring-summary',
                                   headers=self.headers('operator-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item['id'] for item in response.json], [1])

    def test_admin_receives_real_monitoring_devices_across_sites(self):
        response = self.client.get('/api/devices/monitoring-summary',
                                   headers=self.headers('admin-token'))
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item['id'] for item in response.json], [1, 5])


if __name__ == '__main__':
    unittest.main()
