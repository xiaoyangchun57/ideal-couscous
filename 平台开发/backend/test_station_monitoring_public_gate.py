import sqlite3
from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app


class StationMonitoringPublicGateTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = Path(self.temp_dir.name) / 'monitoring-gate.db'
        self.previous_database = web_app.DB_PATH
        self.previous_capability = web_app.STATION_MONITORING_PUBLIC
        web_app.DB_PATH = str(self.database)
        web_app.STATION_MONITORING_PUBLIC = False
        web_app.init_db()
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO sites(code,name,type,district,address,gps_lat,gps_lng) VALUES ('G-1','门禁站','water_quality','一区','一号路',30.1,120.2)")
            self.site_id = db.execute("SELECT id FROM sites WHERE code='G-1'").fetchone()[0]
            db.execute("INSERT INTO sites(code,name,type) VALUES ('G-2','其他站','water_quality')")
            self.other_site_id = db.execute("SELECT id FROM sites WHERE code='G-2'").fetchone()[0]
            db.execute("INSERT INTO users(username,password_hash,role,real_name) VALUES ('gate-admin',?,'admin','门禁管理员')", (web_app._hash_pw('old-password'),))
            self.admin_id = db.execute("SELECT id FROM users WHERE username='gate-admin'").fetchone()[0]
            db.execute("INSERT INTO users(username,password_hash,role,real_name) VALUES ('gate-operator',?,'operator','门禁运维')", (web_app._hash_pw('old-password'),))
            self.operator_id = db.execute("SELECT id FROM users WHERE username='gate-operator'").fetchone()[0]
            db.executemany("INSERT INTO user_roles(user_id,role) VALUES (?,?)", ((self.admin_id, 'admin'), (self.operator_id, 'operator')))
            db.execute("INSERT INTO user_sites(user_id,site_id) VALUES (?,?)", (self.operator_id, self.site_id))
            db.execute(
                "INSERT INTO sensor_data(site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,datetime('now'))",
                (self.site_id, 'ph', 7.2, '',),
            )
            db.commit()
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.STATION_MONITORING_PUBLIC = self.previous_capability
        web_app.DB_PATH = self.previous_database
        self.temp_dir.cleanup()

    def login(self, username='gate-operator', password='old-password'):
        response = self.client.post('/api/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_capability_parser_defaults_closed_and_accepts_explicit_values(self):
        for value in (None, '', ' ', 'invalid', '2'):
            self.assertFalse(web_app._resolve_public_capability(value))
        for value in ('0', 'false', 'NO', 'off'):
            self.assertFalse(web_app._resolve_public_capability(value))
        for value in ('1', 'true', 'YES', 'on'):
            self.assertTrue(web_app._resolve_public_capability(value))

    def test_login_me_and_password_change_share_authoritative_capability(self):
        login = self.login()
        self.assertEqual(login['user']['capabilities'], {'station_monitoring_public': False})
        headers = {'Authorization': 'Bearer ' + login['token']}
        me = self.client.get('/api/auth/me', headers=headers)
        self.assertEqual(me.get_json()['user']['capabilities'], login['user']['capabilities'])
        changed = self.client.post('/api/auth/change-password', headers=headers, json={
            'current_password': 'old-password', 'new_password': 'new-password',
        })
        self.assertEqual(changed.status_code, 200, changed.get_json())
        self.assertEqual(changed.get_json()['user']['capabilities'], login['user']['capabilities'])

    def test_every_public_monitoring_route_is_blocked_with_one_stable_code(self):
        login = self.login()
        headers = {'Authorization': 'Bearer ' + login['token']}
        substitutions = {'site_id': self.site_id, 'endpoint_id': 1}
        tested = 0
        for rule in web_app.app.url_map.iter_rules():
            if not rule.rule.startswith('/api/station-monitoring') or 'GET' not in rule.methods:
                continue
            path = rule.rule
            for name in rule.arguments:
                path = path.replace(f'<int:{name}>', str(substitutions.get(name, 1)))
                path = path.replace(f'<{name}>', str(substitutions.get(name, 1)))
            response = self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 403, (path, response.get_json()))
            self.assertEqual(response.get_json()['code'], 'STATION_MONITORING_PUBLIC_DISABLED')
            tested += 1
        self.assertGreater(tested, 5)

    def test_static_directory_scope_search_and_profile_do_not_expose_monitoring(self):
        operator = self.login()
        operator_headers = {'Authorization': 'Bearer ' + operator['token']}
        directory = self.client.get('/api/mobile/responsible-sites?scope=mine&keyword=%E9%97%A8%E7%A6%81', headers=operator_headers)
        self.assertEqual(directory.status_code, 200, directory.get_json())
        self.assertEqual([item['id'] for item in directory.get_json()['items']], [self.site_id])
        self.assertEqual(directory.get_json()['available_scopes'], ['mine'])
        self.assertFalse(any(key.startswith('monitoring_') or key.startswith('last_') for key in directory.get_json()['items'][0]))
        self.assertEqual(self.client.get('/api/mobile/responsible-sites?scope=all', headers=operator_headers).status_code, 403)

        profile = self.client.get(f'/api/mobile/site-profile/{self.site_id}', headers=operator_headers)
        self.assertEqual(profile.status_code, 200, profile.get_json())
        self.assertTrue(profile.get_json()['site']['can_calibrate'])
        self.assertFalse(any(key.startswith('monitoring_') or key.startswith('last_') for key in profile.get_json()['site']))
        self.assertEqual(self.client.get(
            f'/api/mobile/site-profile/{self.other_site_id}', headers=operator_headers).status_code, 403)

        admin = self.login('gate-admin')
        all_sites = self.client.get('/api/mobile/responsible-sites?scope=all', headers={'Authorization': 'Bearer ' + admin['token']})
        self.assertEqual(all_sites.status_code, 200, all_sites.get_json())
        self.assertEqual(all_sites.get_json()['available_scopes'], ['mine', 'all'])
        self.assertEqual(len(all_sites.get_json()['items']), 2)
        admin_profile = self.client.get(
            f'/api/mobile/site-profile/{self.other_site_id}',
            headers={'Authorization': 'Bearer ' + admin['token']})
        self.assertTrue(admin_profile.get_json()['site']['can_calibrate'])

    def test_explicit_enable_preserves_monitoring_route_and_helpers(self):
        web_app.STATION_MONITORING_PUBLIC = True
        login = self.login('gate-admin')
        response = self.client.get('/api/station-monitoring/sites', headers={'Authorization': 'Bearer ' + login['token']})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn('items', response.get_json())
        with sqlite3.connect(self.database) as db:
            self.assertIsNone(web_app._station_monitoring_projection(db, -999))

    def test_legacy_site_endpoints_hide_monitoring_fields_until_enabled(self):
        login = self.login()
        headers = {'Authorization': 'Bearer ' + login['token']}
        closed_sites = self.client.get('/api/sites', headers=headers).get_json()
        self.assertEqual(len(closed_sites), 1)
        self.assertFalse(any(key.startswith('latest_') for key in closed_sites[0]))
        closed_archive = self.client.get(
            f'/api/sites/{self.site_id}/archive', headers=headers)
        self.assertEqual(closed_archive.status_code, 200, closed_archive.get_json())
        self.assertNotIn('has_sensor_data', closed_archive.get_json())
        self.assertNotIn('trend_data', closed_archive.get_json())

        web_app.STATION_MONITORING_PUBLIC = True
        enabled_sites = self.client.get('/api/sites', headers=headers).get_json()
        self.assertEqual(enabled_sites[0]['latest_metric'], 'ph')
        self.assertEqual(enabled_sites[0]['latest_value'], 7.2)
        enabled_archive = self.client.get(
            f'/api/sites/{self.site_id}/archive', headers=headers).get_json()
        self.assertTrue(enabled_archive['has_sensor_data'])
        self.assertEqual(enabled_archive['trend_data']['ph'][0]['value'], 7.2)


if __name__ == '__main__':
    unittest.main()
