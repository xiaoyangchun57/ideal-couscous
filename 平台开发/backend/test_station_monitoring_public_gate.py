import sqlite3
from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
from migrate_station_ingestion import apply_migration


class StationMonitoringPublicGateTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = Path(self.temp_dir.name) / 'monitoring-gate.db'
        self.previous_database = web_app.DB_PATH
        self.previous_access_mode = web_app.STATION_MONITORING_ACCESS_MODE
        web_app.DB_PATH = str(self.database)
        web_app.STATION_MONITORING_ACCESS_MODE = 'disabled'
        web_app.init_db()
        apply_migration(self.database, Path(self.temp_dir.name) / 'backups')
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO sites(code,name,type,district,address,gps_lat,gps_lng) VALUES ('G-1','门禁站','water_quality','一区','一号路',30.1,120.2)")
            self.site_id = db.execute("SELECT id FROM sites WHERE code='G-1'").fetchone()[0]
            db.execute("INSERT INTO sites(code,name,type) VALUES ('G-2','其他站','water_quality')")
            self.other_site_id = db.execute("SELECT id FROM sites WHERE code='G-2'").fetchone()[0]
            users = (
                ('gate-admin', 'admin', '门禁管理员'),
                ('gate-operator', 'operator', '门禁运维'),
                ('gate-reviewer', 'reviewer', '门禁审核员'),
                ('gate-multi', 'operator', '门禁多角色'),
            )
            for username, role, real_name in users:
                db.execute(
                    'INSERT INTO users(username,password_hash,role,real_name) VALUES (?,?,?,?)',
                    (username, web_app._hash_pw('old-password'), role, real_name),
                )
            user_ids = {
                row[0]: row[1] for row in db.execute(
                    "SELECT username,id FROM users WHERE username LIKE 'gate-%'").fetchall()
            }
            self.admin_id = user_ids['gate-admin']
            self.operator_id = user_ids['gate-operator']
            self.reviewer_id = user_ids['gate-reviewer']
            self.multi_id = user_ids['gate-multi']
            db.executemany('INSERT INTO user_roles(user_id,role) VALUES (?,?)', (
                (self.admin_id, 'admin'),
                (self.operator_id, 'operator'),
                (self.reviewer_id, 'reviewer'),
                (self.multi_id, 'operator'),
                (self.multi_id, 'reviewer'),
            ))
            db.executemany('INSERT INTO user_sites(user_id,site_id) VALUES (?,?)', (
                (self.operator_id, self.site_id),
                (self.reviewer_id, self.site_id),
                (self.multi_id, self.site_id),
            ))
            db.execute(
                "INSERT INTO sensor_data(site_id,metric,value,unit,recorded_at) VALUES (?,?,?,?,datetime('now'))",
                (self.site_id, 'ph', 7.2, ''),
            )
            db.commit()
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.STATION_MONITORING_ACCESS_MODE = self.previous_access_mode
        web_app.DB_PATH = self.previous_database
        self.temp_dir.cleanup()

    def set_mode(self, mode):
        web_app.STATION_MONITORING_ACCESS_MODE = mode

    def login(self, username='gate-operator', password='old-password'):
        response = self.client.post('/api/auth/login', json={
            'username': username, 'password': password,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def headers(self, username='gate-operator'):
        return {'Authorization': 'Bearer ' + self.login(username)['token']}

    def assert_monitoring_routes_blocked(self, username, expected_code):
        headers = self.headers(username)
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
            self.assertEqual(response.status_code, 403, (username, path, response.get_json()))
            self.assertEqual(response.get_json()['code'], expected_code)
            self.assertEqual(set(response.get_json()), {'error', 'code'})
            tested += 1
        self.assertGreater(tested, 5)

    def test_access_mode_parser_defaults_closed_and_legacy_true_maps_to_public(self):
        for value in ('disabled', ' DISABLED ', 'admin', 'ADMIN', 'public', 'PUBLIC'):
            self.assertEqual(
                web_app._resolve_station_monitoring_access_mode(value, 'true'),
                value.strip().lower())
        for value in ('', ' ', 'invalid', 'true', '1'):
            self.assertEqual(
                web_app._resolve_station_monitoring_access_mode(value, 'true'), 'disabled')
        for legacy in (None, '', '0', 'false', 'NO', 'off', 'invalid'):
            self.assertEqual(
                web_app._resolve_station_monitoring_access_mode(None, legacy), 'disabled')
        for legacy in ('1', 'true', 'YES', 'on'):
            self.assertEqual(
                web_app._resolve_station_monitoring_access_mode(None, legacy), 'public')

    def test_login_me_and_password_change_share_role_aware_capability(self):
        self.set_mode('admin')
        expected = {
            'gate-admin': True,
            'gate-operator': False,
            'gate-reviewer': False,
            'gate-multi': False,
        }
        for username, allowed in expected.items():
            login = self.login(username)
            self.assertEqual(
                login['user']['capabilities'], {'station_monitoring_public': allowed})
            me = self.client.get('/api/auth/me', headers={
                'Authorization': 'Bearer ' + login['token'],
            })
            self.assertEqual(me.status_code, 200, me.get_json())
            self.assertEqual(me.get_json()['user']['capabilities'],
                             login['user']['capabilities'])

        operator = self.login('gate-operator')
        changed = self.client.post('/api/auth/change-password', headers={
            'Authorization': 'Bearer ' + operator['token'],
        }, json={'current_password': 'old-password', 'new_password': 'new-password'})
        self.assertEqual(changed.status_code, 200, changed.get_json())
        self.assertEqual(changed.get_json()['user']['capabilities'],
                         {'station_monitoring_public': False})

    def test_disabled_and_invalid_modes_block_every_role_and_route(self):
        for mode in ('disabled', 'invalid'):
            self.set_mode(mode)
            for username in ('gate-admin', 'gate-reviewer', 'gate-operator'):
                self.assertFalse(
                    self.login(username)['user']['capabilities']['station_monitoring_public'])
                self.assert_monitoring_routes_blocked(
                    username, 'STATION_MONITORING_PUBLIC_DISABLED')

    def test_admin_mode_allows_only_admin_and_preserves_admin_all_site_scope(self):
        self.set_mode('admin')
        for username in ('gate-reviewer', 'gate-operator', 'gate-multi'):
            self.assert_monitoring_routes_blocked(
                username, 'STATION_MONITORING_ADMIN_ONLY')

        admin = self.login('gate-admin')
        self.assertTrue(admin['user']['capabilities']['station_monitoring_public'])
        response = self.client.get('/api/station-monitoring/sites?scope=all', headers={
            'Authorization': 'Bearer ' + admin['token'],
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(len(response.get_json()['items']), 2)

    def test_public_mode_keeps_role_and_site_scope_rules(self):
        self.set_mode('public')
        operator_headers = self.headers('gate-operator')
        own = self.client.get('/api/station-monitoring/sites', headers=operator_headers)
        self.assertEqual(own.status_code, 200, own.get_json())
        self.assertEqual([item['site_id'] for item in own.get_json()['items']], [self.site_id])
        self.assertEqual(self.client.get(
            '/api/station-monitoring/sites?scope=all', headers=operator_headers).status_code, 403)
        self.assertEqual(self.client.get(
            f'/api/station-monitoring/sites/{self.other_site_id}/overview',
            headers=operator_headers).status_code, 403)

        reviewer = self.login('gate-reviewer')
        self.assertTrue(reviewer['user']['capabilities']['station_monitoring_public'])
        admin_all = self.client.get('/api/station-monitoring/sites?scope=all',
                                    headers=self.headers('gate-admin'))
        self.assertEqual(admin_all.status_code, 200, admin_all.get_json())
        self.assertEqual(len(admin_all.get_json()['items']), 2)

    def test_static_directory_scope_search_and_profile_do_not_expose_monitoring(self):
        self.set_mode('admin')
        operator_headers = self.headers('gate-operator')
        directory = self.client.get(
            '/api/mobile/responsible-sites?scope=mine&keyword=%E9%97%A8%E7%A6%81',
            headers=operator_headers)
        self.assertEqual(directory.status_code, 200, directory.get_json())
        self.assertEqual([item['id'] for item in directory.get_json()['items']], [self.site_id])
        self.assertEqual(directory.get_json()['available_scopes'], ['mine'])
        self.assertFalse(any(key.startswith('monitoring_') or key.startswith('last_')
                             for key in directory.get_json()['items'][0]))
        self.assertEqual(self.client.get(
            '/api/mobile/responsible-sites?scope=all', headers=operator_headers).status_code, 403)

        profile = self.client.get(
            f'/api/mobile/site-profile/{self.site_id}', headers=operator_headers)
        self.assertEqual(profile.status_code, 200, profile.get_json())
        self.assertTrue(profile.get_json()['site']['can_calibrate'])
        self.assertFalse(any(key.startswith('monitoring_') or key.startswith('last_')
                             for key in profile.get_json()['site']))
        self.assertEqual(self.client.get(
            f'/api/mobile/site-profile/{self.other_site_id}',
            headers=operator_headers).status_code, 403)

        admin_headers = self.headers('gate-admin')
        all_sites = self.client.get(
            '/api/mobile/responsible-sites?scope=all', headers=admin_headers)
        self.assertEqual(all_sites.status_code, 200, all_sites.get_json())
        self.assertEqual(all_sites.get_json()['available_scopes'], ['mine', 'all'])
        self.assertEqual(len(all_sites.get_json()['items']), 2)

    def test_retired_master_site_leaves_current_directories_but_keeps_profile_history(self):
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE sites SET master_status='retired' WHERE id=?", (self.other_site_id,))
            db.commit()
        self.set_mode('public')
        admin_headers = self.headers('gate-admin')
        simple = self.client.get('/api/sites', headers=admin_headers)
        static = self.client.get('/api/mobile/responsible-sites?scope=all', headers=admin_headers)
        monitoring = self.client.get('/api/station-monitoring/sites?scope=all', headers=admin_headers)
        self.assertEqual([item['id'] for item in simple.get_json()], [self.site_id])
        self.assertEqual([item['id'] for item in static.get_json()['items']], [self.site_id])
        self.assertEqual(static.get_json()['scope_counts']['all'], 1)
        self.assertEqual([item['site_id'] for item in monitoring.get_json()['items']], [self.site_id])
        self.assertEqual(monitoring.get_json()['scope_counts']['all'], 1)
        profile = self.client.get(
            f'/api/mobile/site-profile/{self.other_site_id}', headers=admin_headers)
        self.assertEqual(profile.status_code, 200, profile.get_json())
        self.assertEqual(profile.get_json()['site']['id'], self.other_site_id)

    def test_site_monitoring_projections_use_the_same_request_user_gate(self):
        operator_headers = self.headers('gate-operator')
        admin_headers = self.headers('gate-admin')

        self.set_mode('disabled')
        disabled_sites = self.client.get('/api/sites', headers=operator_headers).get_json()
        self.assertFalse(any(key.startswith('latest_') for key in disabled_sites[0]))

        self.set_mode('admin')
        operator_sites = self.client.get('/api/sites', headers=operator_headers).get_json()
        self.assertFalse(any(key.startswith('latest_') for key in operator_sites[0]))
        operator_archive = self.client.get(
            f'/api/sites/{self.site_id}/archive', headers=operator_headers).get_json()
        self.assertNotIn('has_sensor_data', operator_archive)
        self.assertNotIn('trend_data', operator_archive)

        admin_sites = self.client.get('/api/sites', headers=admin_headers).get_json()
        admin_site = next(item for item in admin_sites if item['id'] == self.site_id)
        self.assertEqual(admin_site['latest_metric'], 'ph')
        self.assertEqual(admin_site['latest_value'], 7.2)
        admin_archive = self.client.get(
            f'/api/sites/{self.site_id}/archive', headers=admin_headers).get_json()
        self.assertTrue(admin_archive['has_sensor_data'])
        self.assertEqual(admin_archive['trend_data']['ph'][0]['value'], 7.2)

        self.set_mode('public')
        public_sites = self.client.get('/api/sites', headers=operator_headers).get_json()
        self.assertEqual(public_sites[0]['latest_metric'], 'ph')


if __name__ == '__main__':
    unittest.main()
