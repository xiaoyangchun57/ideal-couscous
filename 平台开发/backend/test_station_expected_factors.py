import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
import station_monitoring as monitoring_module
from hj212_parser import hj212_crc
from migrate_station_ingestion import apply_migration
from sl651_server import IngestionStorage, StationIngestServer, credential_hmac
from station_monitoring import latest_cycle, latest_values, normalize_raw_frame


def make_hj212(*, station, password, qn, data_time, factors, command='2061'):
    body = (
        f'QN={qn};ST=21;CN={command};PW={password};MN={station};Flag=9;'
        f'CP=&&DataTime={data_time};{factors}&&'
    ).encode('ascii')
    return (b'##' + f'{len(body):04d}'.encode('ascii') + body
            + hj212_crc(body).encode('ascii') + b'\r\n')


class StationExpectedFactorsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = Path(self.temp_dir.name) / 'station-expected-factors.db'
        self.previous_database = web_app.DB_PATH
        self.previous_access_mode = web_app.STATION_MONITORING_ACCESS_MODE
        web_app.DB_PATH = str(self.database)
        web_app.STATION_MONITORING_ACCESS_MODE = 'public'
        web_app.init_db()
        apply_migration(self.database, Path(self.temp_dir.name) / 'backups')
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO sites(code,name,type) VALUES ('EF-1','因子站一','water_quality')")
            self.site_id = db.execute("SELECT id FROM sites WHERE code='EF-1'").fetchone()[0]
            db.execute("INSERT INTO sites(code,name,type) VALUES ('EF-2','因子站二','water_quality')")
            self.other_site_id = db.execute("SELECT id FROM sites WHERE code='EF-2'").fetchone()[0]
            for username, role in (
                    ('expected-admin', 'admin'),
                    ('expected-operator', 'operator'),
                    ('expected-reviewer', 'reviewer')):
                db.execute(
                    'INSERT INTO users(username,password_hash,role,real_name) VALUES (?,?,?,?)',
                    (username, web_app._hash_pw('pw'), role, username),
                )
            users = dict(db.execute(
                "SELECT username,id FROM users WHERE username LIKE 'expected-%'"))
            self.admin_id = users['expected-admin']
            self.operator_id = users['expected-operator']
            self.reviewer_id = users['expected-reviewer']
            db.executemany('INSERT INTO user_roles(user_id,role) VALUES (?,?)', (
                (self.admin_id, 'admin'),
                (self.operator_id, 'operator'),
                (self.reviewer_id, 'reviewer'),
            ))
            db.executemany('INSERT INTO user_sites(user_id,site_id) VALUES (?,?)', (
                (self.operator_id, self.site_id),
                (self.reviewer_id, self.site_id),
            ))
            db.commit()
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.STATION_MONITORING_ACCESS_MODE = self.previous_access_mode
        web_app.DB_PATH = self.previous_database
        self.temp_dir.cleanup()

    def headers(self, username):
        response = self.client.post('/api/auth/login', json={
            'username': username, 'password': 'pw',
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return {'Authorization': 'Bearer ' + response.get_json()['token']}

    def install_hj212_profile(self, *, conflicting_total_nitrogen=False):
        station = 'EXPECTED-HJ212'
        password = 'expected-hj212-password'
        pepper = 'expected-hj212-pepper'
        with sqlite3.connect(self.database) as db:
            endpoint_id = db.execute("""INSERT INTO trusted_endpoints(
                    station_code,credential_hmac,business_site_id,enabled,endpoint_state)
                VALUES (?,?,?,1,'bound')""", (
                    station, credential_hmac(password.encode('ascii'), pepper), self.site_id,
                )).lastrowid
            db.execute("""INSERT INTO monitoring_endpoint_profiles(
                    endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                    expected_interval_seconds,effective_from)
                VALUES (?,?,'Asia/Shanghai',1,'periodic',3600,'2020-01-01T00:00:00+00:00')""",
                (endpoint_id, self.site_id))
            db.execute("""INSERT INTO monitoring_factor_mappings(
                    endpoint_id,protocol_code,business_metric,expected_interval_seconds,
                    tolerance_seconds,effective_from,enabled)
                VALUES (?,'HJ212:w01001','ph',3600,600,'2020-01-01T00:00:00+00:00',1)""",
                (endpoint_id,))
            if conflicting_total_nitrogen:
                db.execute("""INSERT INTO monitoring_factor_mappings(
                        endpoint_id,protocol_code,business_metric,expected_interval_seconds,
                        tolerance_seconds,effective_from,enabled)
                    VALUES (?,'HJ212:w21001','ammonia',3600,600,
                            '2020-01-01T00:00:00+00:00',1)""", (endpoint_id,))
            db.execute("""INSERT INTO monitoring_business_schedules(
                    endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                    tolerance_seconds,result_delay_seconds,effective_from,enabled)
                VALUES (?,NULL,'Asia/Shanghai',3600,'00:00:00',600,0,
                        '2020-01-01T00:00:00+00:00',1)""", (endpoint_id,))
            db.commit()
        return endpoint_id, station, password, pepper

    def install_bound_hj212_endpoint(self):
        station = 'EXPECTED-BOUND-HJ212'
        password = 'expected-bound-hj212-password'
        pepper = 'expected-bound-hj212-pepper'
        with sqlite3.connect(self.database) as db:
            endpoint_id = db.execute("""INSERT INTO trusted_endpoints(
                    station_code,credential_hmac,business_site_id,enabled,endpoint_state)
                VALUES (?,?,?,1,'bound')""", (
                    station, credential_hmac(password.encode('ascii'), pepper), self.site_id,
                )).lastrowid
            db.commit()
        storage = IngestionStorage(self.database, pepper)
        server = StationIngestServer(storage, host='127.0.0.1', port=0)
        return endpoint_id, station, password, pepper, server

    def test_unconfigured_read_exposes_exact_candidates_and_role_capability(self):
        operator = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'))
        self.assertEqual(operator.status_code, 200, operator.get_json())
        payload = operator.get_json()
        self.assertFalse(payload['configured'])
        self.assertEqual(payload['version'], 0)
        self.assertTrue(payload['can_edit'])
        self.assertEqual(
            [item['business_metric'] for item in payload['candidates']],
            list(web_app._STATION_EXPECTED_FACTOR_METRICS),
        )
        self.assertEqual(payload['selected_metrics'], [])

        reviewer = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-reviewer'))
        self.assertEqual(reviewer.status_code, 200, reviewer.get_json())
        self.assertFalse(reviewer.get_json()['can_edit'])

    def test_save_replay_version_conflict_and_effective_history(self):
        headers = self.headers('expected-operator')
        path = f'/api/station-monitoring/sites/{self.site_id}/expected-factors'
        request = {
            'metrics': ['turbidity', 'ph'],
            'version': 0,
            'idempotency_key': 'operator-save-1',
        }
        saved = self.client.put(path, headers=headers, json=request)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        first = saved.get_json()
        self.assertEqual(first['version'], 1)
        self.assertEqual(first['selected_metrics'], ['ph', 'turbidity'])
        self.assertFalse(first['replayed'])

        replay = self.client.put(path, headers=headers, json=request)
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertTrue(replay.get_json()['replayed'])
        self.assertEqual(replay.get_json()['config_id'], first['config_id'])

        changed_replay = self.client.put(path, headers=headers, json={
            **request, 'metrics': ['ph'],
        })
        self.assertEqual(changed_replay.status_code, 409, changed_replay.get_json())
        self.assertEqual(
            changed_replay.get_json()['code'], 'EXPECTED_FACTORS_IDEMPOTENCY_CONFLICT')

        stale = self.client.put(path, headers=headers, json={
            'metrics': ['ph'], 'version': 0, 'idempotency_key': 'operator-stale',
        })
        self.assertEqual(stale.status_code, 409, stale.get_json())
        self.assertEqual(stale.get_json()['code'], 'EXPECTED_FACTORS_VERSION_CONFLICT')

        second = self.client.put(path, headers=headers, json={
            'metrics': ['ph'], 'version': 1, 'idempotency_key': 'operator-save-2',
        })
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertEqual(second.get_json()['version'], 2)
        with sqlite3.connect(self.database) as db:
            db.row_factory = sqlite3.Row
            configs = db.execute(
                'SELECT * FROM station_expected_factor_configs WHERE site_id=? ORDER BY version',
                (self.site_id,),
            ).fetchall()
            self.assertEqual(len(configs), 2)
            self.assertEqual(configs[0]['effective_to'], configs[1]['effective_from'])
            self.assertEqual(json.loads(configs[1]['previous_metrics_json']), ['ph', 'turbidity'])
            first_ph = db.execute(
                "SELECT effective_from FROM station_expected_factors WHERE config_id=? AND business_metric='ph'",
                (configs[0]['id'],),
            ).fetchone()[0]
            second_ph = db.execute(
                "SELECT effective_from FROM station_expected_factors WHERE config_id=? AND business_metric='ph'",
                (configs[1]['id'],),
            ).fetchone()[0]
            self.assertEqual(first_ph, second_ph)

    def test_preheated_overview_and_list_follow_saved_version_immediately(self):
        endpoint_id, _, _, _ = self.install_hj212_profile()
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM monitoring_business_schedules WHERE endpoint_id=?',
                       (endpoint_id,))
            db.execute("""UPDATE monitoring_endpoint_profiles
                SET expected_granularity='realtime',expected_interval_seconds=14400
                WHERE endpoint_id=?""", (endpoint_id,))
            db.execute("""UPDATE monitoring_factor_mappings
                SET expected_interval_seconds=14400,tolerance_seconds=NULL
                WHERE endpoint_id=? AND protocol_code='HJ212:w01001'""", (endpoint_id,))
            db.execute("""INSERT INTO monitoring_factor_mappings(
                    endpoint_id,protocol_code,business_metric,expected_interval_seconds,
                    tolerance_seconds,effective_from,enabled)
                VALUES (?,'HJ212:w01009','dissolved_oxygen',14400,NULL,
                        '2020-01-01T00:00:00+00:00',1)""", (endpoint_id,))
            db.commit()
        headers = self.headers('expected-operator')
        overview_path = f'/api/station-monitoring/sites/{self.site_id}/overview'
        list_path = '/api/station-monitoring/sites?scope=mine'
        expected_path = f'/api/station-monitoring/sites/{self.site_id}/expected-factors'

        before_overview = self.client.get(overview_path, headers=headers).get_json()
        before_list = self.client.get(list_path, headers=headers).get_json()
        self.assertFalse(before_overview['site']['expected_factors_configured'])
        self.assertEqual(before_overview['monitoring']['latest_cycle']['expected_count'], 0)
        self.assertFalse(before_list['items'][0]['expected_factors_configured'])

        first = self.client.put(expected_path, headers=headers, json={
            'metrics': ['ph', 'dissolved_oxygen'],
            'version': 0,
            'idempotency_key': 'preheated-two-factors',
        })
        self.assertEqual(first.status_code, 200, first.get_json())
        first_overview = self.client.get(overview_path, headers=headers).get_json()
        first_list = self.client.get(list_path, headers=headers).get_json()
        self.assertEqual(first_overview['site']['expected_factor_version'], 1)
        self.assertEqual(first_overview['site']['monitoring_status'], 'awaiting_first_frame')
        self.assertEqual(first_overview['monitoring']['latest_cycle']['expected_count'], 2)
        self.assertEqual(first_list['items'][0]['expected_factor_version'], 1)
        self.assertEqual(first_list['items'][0]['latest_cycle']['expected_count'], 2)

        second = self.client.put(expected_path, headers=headers, json={
            'metrics': ['ph'],
            'version': 1,
            'idempotency_key': 'preheated-one-factor',
        })
        self.assertEqual(second.status_code, 200, second.get_json())
        second_overview = self.client.get(overview_path, headers=headers).get_json()
        second_list = self.client.get(list_path, headers=headers).get_json()
        self.assertEqual(second_overview['site']['expected_factor_version'], 2)
        self.assertEqual(second_overview['monitoring']['latest_cycle']['expected_count'], 1)
        self.assertEqual(second_list['items'][0]['expected_factor_version'], 2)
        self.assertEqual(second_list['items'][0]['latest_cycle']['expected_count'], 1)

    def test_write_permissions_validation_and_missing_site(self):
        path = f'/api/station-monitoring/sites/{self.site_id}/expected-factors'
        reviewer = self.client.put(path, headers=self.headers('expected-reviewer'), json={
            'metrics': ['ph'], 'version': 0, 'idempotency_key': 'reviewer-save',
        })
        self.assertEqual(reviewer.status_code, 403, reviewer.get_json())
        self.assertEqual(reviewer.get_json()['code'], 'EXPECTED_FACTORS_FORBIDDEN')

        outside = self.client.put(
            f'/api/station-monitoring/sites/{self.other_site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['ph'], 'version': 0, 'idempotency_key': 'outside-save',
            })
        self.assertEqual(outside.status_code, 403, outside.get_json())

        admin_headers = self.headers('expected-admin')
        invalid = self.client.put(path, headers=admin_headers, json={
            'metrics': ['total_tin'], 'version': 0, 'idempotency_key': 'invalid-save',
        })
        self.assertEqual(invalid.status_code, 400, invalid.get_json())
        self.assertEqual(invalid.get_json()['code'], 'EXPECTED_FACTORS_INVALID')
        empty = self.client.put(path, headers=admin_headers, json={
            'metrics': [], 'version': 0, 'idempotency_key': 'empty-save',
        })
        self.assertEqual(empty.status_code, 400, empty.get_json())
        self.assertEqual(empty.get_json()['code'], 'EXPECTED_FACTORS_EMPTY')
        missing = self.client.put(
            '/api/station-monitoring/sites/999999/expected-factors',
            headers=admin_headers, json={
                'metrics': ['ph'], 'version': 0, 'idempotency_key': 'missing-save',
            })
        self.assertEqual(missing.status_code, 404, missing.get_json())
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM station_expected_factor_configs').fetchone()[0], 0)

    def test_selected_tn_tp_add_standard_hj212_mappings_and_form_complete_cycle(self):
        endpoint_id, station, password, pepper = self.install_hj212_profile()
        path = f'/api/station-monitoring/sites/{self.site_id}/expected-factors'
        request = {
            'metrics': ['ph', 'total_nitrogen', 'total_phosphorus'],
            'version': 0,
            'idempotency_key': 'hj212-tn-tp-save',
        }
        saved = self.client.put(
            path, headers=self.headers('expected-operator'), json=request)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        candidates = {
            item['business_metric']: item for item in saved.get_json()['candidates']
        }
        self.assertTrue(candidates['total_nitrogen']['mapping_available'])
        self.assertTrue(candidates['total_phosphorus']['mapping_available'])
        replay = self.client.put(
            path, headers=self.headers('expected-operator'), json=request)
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertTrue(replay.get_json()['replayed'])
        with sqlite3.connect(self.database) as db:
            rows = db.execute("""SELECT protocol_code,business_metric,
                       expected_interval_seconds,tolerance_seconds
                FROM monitoring_factor_mappings
                WHERE endpoint_id=? AND protocol_code IN ('HJ212:w21001','HJ212:w21011')
                ORDER BY protocol_code""", (endpoint_id,)).fetchall()
        self.assertEqual(rows, [
            ('HJ212:w21001', 'total_nitrogen', 3600, 600),
            ('HJ212:w21011', 'total_phosphorus', 3600, 600),
        ])

        observed = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(
            minute=0, second=0, microsecond=0)
        local_observed = observed.astimezone(timezone(timedelta(hours=8)))
        received = observed + timedelta(seconds=10)
        storage = IngestionStorage(self.database, pepper)
        server = StationIngestServer(storage, host='127.0.0.1', port=0)
        result = server._process_raw_result(make_hj212(
            station=station, password=password,
            qn=local_observed.strftime('%Y%m%d%H%M%S') + '001',
            data_time=local_observed.strftime('%Y%m%d%H%M%S'),
            factors=';'.join((
                'w01001-Avg=7.10,w01001-Flag=N',
                'w21001-Avg=1.23,w21001-Flag=N',
                'w21011-Avg=0.045,w21011-Flag=N',
            )),
        ), received.isoformat())
        self.assertTrue(result.keep_connection)
        with sqlite3.connect(self.database) as db:
            raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            values = latest_values(
                db, self.site_id, at_time=(received + timedelta(minutes=1)).isoformat())
            cycle = latest_cycle(
                db, self.site_id, at_time=(received + timedelta(minutes=1)).isoformat())
        self.assertEqual({item['business_metric'] for item in values}, {
            'ph', 'total_nitrogen', 'total_phosphorus',
        })
        self.assertEqual(cycle['status'], 'complete')
        self.assertEqual((cycle['formed_count'], cycle['expected_count']), (3, 3))

    def test_selected_standard_factor_adds_dissolved_oxygen_mapping(self):
        endpoint_id, _, _, _ = self.install_hj212_profile()
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['ph', 'dissolved_oxygen'],
                'version': 0,
                'idempotency_key': 'hj212-do-save',
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        with sqlite3.connect(self.database) as db:
            mapping = db.execute("""SELECT business_metric,expected_interval_seconds,
                       tolerance_seconds FROM monitoring_factor_mappings
                WHERE endpoint_id=? AND protocol_code='HJ212:w01009'""",
                (endpoint_id,)).fetchone()
        self.assertEqual(mapping, ('dissolved_oxygen', 3600, 600))

    def test_existing_selected_mappings_do_not_require_tolerance_to_save(self):
        endpoint_id, _, _, _ = self.install_hj212_profile()
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM monitoring_business_schedules WHERE endpoint_id=?',
                       (endpoint_id,))
            db.execute("""UPDATE monitoring_endpoint_profiles
                SET expected_interval_seconds=14400 WHERE endpoint_id=?""", (endpoint_id,))
            db.execute("""UPDATE monitoring_factor_mappings
                SET expected_interval_seconds=14400,tolerance_seconds=NULL
                WHERE endpoint_id=? AND protocol_code='HJ212:w01001'""", (endpoint_id,))
            db.execute("""INSERT INTO monitoring_factor_mappings(
                    endpoint_id,protocol_code,business_metric,expected_interval_seconds,
                    tolerance_seconds,effective_from,enabled)
                VALUES (?,'HJ212:w01009','dissolved_oxygen',14400,NULL,
                        '2020-01-01T00:00:00+00:00',1)""", (endpoint_id,))
            db.commit()
        path = f'/api/station-monitoring/sites/{self.site_id}/expected-factors'
        saved = self.client.put(path, headers=self.headers('expected-operator'), json={
            'metrics': ['ph', 'dissolved_oxygen'],
            'version': 0,
            'idempotency_key': 'existing-null-tolerance-save',
        })
        self.assertEqual(saved.status_code, 200, saved.get_json())
        reloaded = self.client.get(path, headers=self.headers('expected-operator'))
        self.assertEqual(reloaded.status_code, 200, reloaded.get_json())
        self.assertEqual(reloaded.get_json()['selected_metrics'], ['dissolved_oxygen', 'ph'])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_factor_mappings WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 2)

    def test_missing_mapping_still_requires_tolerance_and_rolls_back(self):
        endpoint_id, _, _, _ = self.install_hj212_profile()
        with sqlite3.connect(self.database) as db:
            db.execute('DELETE FROM monitoring_business_schedules WHERE endpoint_id=?',
                       (endpoint_id,))
            db.execute("""UPDATE monitoring_factor_mappings SET tolerance_seconds=NULL
                WHERE endpoint_id=?""", (endpoint_id,))
            db.commit()
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['ph', 'dissolved_oxygen'],
                'version': 0,
                'idempotency_key': 'missing-null-tolerance-save',
            })
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()['code'], 'EXPECTED_FACTORS_MAPPING_CONFLICT')
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM station_expected_factor_configs').fetchone()[0], 0)
            self.assertEqual(db.execute("""SELECT COUNT(*) FROM monitoring_factor_mappings
                WHERE endpoint_id=? AND protocol_code='HJ212:w01009'""",
                (endpoint_id,)).fetchone()[0], 0)

    def test_evidenced_numeric_family_adds_only_observed_selected_alias(self):
        endpoint_id, station, password, pepper = self.install_hj212_profile()
        storage = IngestionStorage(self.database, pepper)
        server = StationIngestServer(storage, host='127.0.0.1', port=0)
        evidence_time = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0)
        local_evidence = evidence_time.astimezone(timezone(timedelta(hours=8)))
        server._process_raw_result(make_hj212(
            station=station, password=password,
            qn=local_evidence.strftime('%Y%m%d%H%M%S') + '001',
            data_time=local_evidence.strftime('%Y%m%d%H%M%S'),
            factors='004-Avg=25.4,004-Flag=N;006-Avg=153.2,006-Flag=N',
        ), (evidence_time + timedelta(seconds=5)).isoformat())
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['water_temp'],
                'version': 0,
                'idempotency_key': 'hj212-numeric-water-temp',
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        with sqlite3.connect(self.database) as db:
            rows = db.execute("""SELECT protocol_code,business_metric
                FROM monitoring_factor_mappings
                WHERE endpoint_id=? AND protocol_code IN (
                    'HJ212:w01010','HJ212:004','HJ212:006')
                ORDER BY protocol_code""", (endpoint_id,)).fetchall()
        self.assertEqual(rows, [
            ('HJ212:004', 'water_temp'),
            ('HJ212:w01010', 'water_temp'),
        ])
        new_time = evidence_time + timedelta(hours=1)
        local_new = new_time.astimezone(timezone(timedelta(hours=8)))
        server._process_raw_result(make_hj212(
            station=station, password=password,
            qn=local_new.strftime('%Y%m%d%H%M%S') + '001',
            data_time=local_new.strftime('%Y%m%d%H%M%S'),
            factors='004-Avg=25.8,004-Flag=N',
        ), (new_time + timedelta(seconds=5)).isoformat())
        with sqlite3.connect(self.database) as db:
            raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            values = latest_values(
                db, self.site_id,
                at_time=(new_time + timedelta(minutes=1)).isoformat())
        self.assertEqual(
            [(item['business_metric'], item['standard_value']) for item in values],
            [('water_temp', 25.8)],
        )

    def test_process_only_numeric_code_does_not_authorize_alias_mapping(self):
        endpoint_id, station, password, pepper = self.install_hj212_profile()
        storage = IngestionStorage(self.database, pepper)
        server = StationIngestServer(storage, host='127.0.0.1', port=0)
        evidence_time = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0)
        local_evidence = evidence_time.astimezone(timezone(timedelta(hours=8)))
        server._process_raw_result(make_hj212(
            station=station, password=password,
            qn=local_evidence.strftime('%Y%m%d%H%M%S') + '001',
            data_time=local_evidence.strftime('%Y%m%d%H%M%S'),
            factors='004-Rtd=25.4,004-Flag=N', command='2011',
        ), (evidence_time + timedelta(seconds=5)).isoformat())
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['water_temp'],
                'version': 0,
                'idempotency_key': 'hj212-process-only-water-temp',
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        with sqlite3.connect(self.database) as db:
            rows = db.execute("""SELECT protocol_code,business_metric
                FROM monitoring_factor_mappings
                WHERE endpoint_id=? AND protocol_code IN ('HJ212:w01010','HJ212:004')
                ORDER BY protocol_code""", (endpoint_id,)).fetchall()
        self.assertEqual(rows, [('HJ212:w01010', 'water_temp')])

    def test_bound_hj212_evidence_creates_common_profile_and_reaches_all_read_paths(self):
        endpoint_id, station, password, _, server = self.install_bound_hj212_endpoint()
        evidence = make_hj212(
            station=station, password=password, qn='20261010000500001',
            data_time='20261010000000',
            factors='w01001-Avg=7.01,w01001-Flag=N;'
                    'w01009-Avg=7.20,w01009-Flag=N',
        )
        first = server._process_raw_result(evidence, '2026-10-09T16:05:00+00:00')
        self.assertTrue(first.keep_connection)
        with sqlite3.connect(self.database) as db:
            first_raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
            self.assertEqual(db.execute(
                "SELECT disposition FROM ingest_raw_frames WHERE id=?", (first_raw_id,)
            ).fetchone()[0], 'quarantined')
            self.assertEqual(db.execute("""SELECT error_type FROM ingest_errors
                WHERE raw_frame_id=? ORDER BY id DESC LIMIT 1""", (first_raw_id,)).fetchone()[0],
                'configuration_pending')

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 10, 9, 18, 0, 0, tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        request = {
            'metrics': ['ph', 'dissolved_oxygen'],
            'version': 0,
            'idempotency_key': 'bound-hj212-common-profile',
        }
        with mock.patch.object(web_app, 'datetime', FixedDateTime):
            saved = self.client.put(
                f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
                headers=self.headers('expected-operator'), json=request)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        with sqlite3.connect(self.database) as db:
            profile = db.execute("""SELECT timezone,expected_granularity,
                       expected_interval_seconds,effective_from
                FROM monitoring_endpoint_profiles WHERE endpoint_id=?""",
                (endpoint_id,)).fetchone()
            schedule = db.execute("""SELECT interval_seconds,anchor_local_time,
                       tolerance_seconds,result_delay_seconds
                FROM monitoring_business_schedules
                WHERE endpoint_id=? AND protocol_code IS NULL""", (endpoint_id,)).fetchone()
            mappings = db.execute("""SELECT protocol_code,business_metric
                FROM monitoring_factor_mappings WHERE endpoint_id=? ORDER BY protocol_code""",
                (endpoint_id,)).fetchall()
        self.assertEqual(profile, (
            'Asia/Shanghai', 'periodic', 14400, '2026-10-09T18:00:00.000000+00:00'))
        self.assertEqual(schedule, (14400, '00:00:00', 600, 7200))
        self.assertEqual(mappings, [
            ('HJ212:w01001', 'ph'),
            ('HJ212:w01009', 'dissolved_oxygen'),
        ])
        replay = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json=request)
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertTrue(replay.get_json()['replayed'])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_endpoint_profiles WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_factor_mappings WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 2)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_business_schedules WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 1)

        new_frame = server._process_raw_result(make_hj212(
            station=station, password=password, qn='20261010040500001',
            data_time='20261010040000',
            factors='w01001-Avg=7.12,w01001-Flag=N;'
                    'w01009-Avg=7.35,w01009-Flag=N',
        ), '2026-10-09T20:05:00+00:00')
        self.assertTrue(new_frame.keep_connection)
        with sqlite3.connect(self.database) as db:
            new_raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
        self.assertEqual(normalize_raw_frame(self.database, new_raw_id), 'accepted')

        class FixedReadDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 10, 9, 20, 6, 0, tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        headers = self.headers('expected-admin')
        with mock.patch.object(web_app, 'datetime', FixedReadDateTime), \
                mock.patch.object(monitoring_module, 'datetime', FixedReadDateTime):
            latest = self.client.get(
                f'/api/station-monitoring/sites/{self.site_id}/latest', headers=headers)
            self.assertEqual(latest.status_code, 200, latest.get_json())
            self.assertEqual({item['business_metric'] for item in latest.get_json()['items']}, {
                'ph', 'dissolved_oxygen',
            })
            overview = self.client.get(
                f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers)
            self.assertEqual(overview.status_code, 200, overview.get_json())
            self.assertEqual(overview.get_json()['site']['latest_cycle']['status'], 'complete')
            self.assertEqual(
                overview.get_json()['site']['latest_cycle']['formed_count'], 2)
            listed = self.client.get('/api/station-monitoring/sites?scope=all', headers=headers)
            self.assertEqual(listed.status_code, 200, listed.get_json())
            listed_site = next(item for item in listed.get_json()['items']
                               if item['site_id'] == self.site_id)
            self.assertEqual(listed_site['latest_cycle']['status'], 'complete')
            self.assertEqual(listed_site['latest_cycle']['formed_count'], 2)

    def test_bound_codmn_2061_rtd_creates_profile_and_reaches_all_read_paths(self):
        endpoint_id, station, password, _, server = self.install_bound_hj212_endpoint()
        first = server._process_raw_result(make_hj212(
            station=station, password=password, qn='20261010000500001',
            data_time='20261010000000',
            factors='w01019-Rtd=1.2,w01019-Flag=N',
        ), '2026-10-09T16:05:00+00:00')
        self.assertTrue(first.keep_connection)
        with sqlite3.connect(self.database) as db:
            first_raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
            self.assertEqual(db.execute(
                'SELECT disposition FROM ingest_raw_frames WHERE id=?', (first_raw_id,)
            ).fetchone()[0], 'quarantined')
            self.assertEqual(db.execute("""SELECT error_type FROM ingest_errors
                WHERE raw_frame_id=? ORDER BY id DESC LIMIT 1""", (first_raw_id,)).fetchone()[0],
                'configuration_pending')

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 10, 9, 18, 0, 0, tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        request = {
            'metrics': ['codmn'],
            'version': 0,
            'idempotency_key': 'bound-hj212-codmn-rtd-profile',
        }
        with mock.patch.object(web_app, 'datetime', FixedDateTime):
            saved = self.client.put(
                f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
                headers=self.headers('expected-operator'), json=request)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertTrue(next(item for item in saved.get_json()['candidates']
                             if item['business_metric'] == 'codmn')['mapping_available'])
        with sqlite3.connect(self.database) as db:
            profile = db.execute("""SELECT expected_interval_seconds
                FROM monitoring_endpoint_profiles WHERE endpoint_id=?""",
                (endpoint_id,)).fetchone()
            schedule = db.execute("""SELECT interval_seconds,tolerance_seconds,result_delay_seconds
                FROM monitoring_business_schedules
                WHERE endpoint_id=? AND protocol_code IS NULL""", (endpoint_id,)).fetchone()
            mappings = db.execute("""SELECT protocol_code,business_metric
                FROM monitoring_factor_mappings WHERE endpoint_id=?""", (endpoint_id,)).fetchall()
        self.assertEqual(profile, (14400,))
        self.assertEqual(schedule, (14400, 600, 7200))
        self.assertEqual(mappings, [('HJ212:w01019-Rtd', 'codmn')])

        replay = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json=request)
        self.assertEqual(replay.status_code, 200, replay.get_json())
        self.assertTrue(replay.get_json()['replayed'])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_endpoint_profiles WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_factor_mappings WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_business_schedules WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 1)

        new_frame = server._process_raw_result(make_hj212(
            station=station, password=password, qn='20261010040500001',
            data_time='20261010040000',
            factors='w01019-Rtd=1.3,w01019-Flag=N',
        ), '2026-10-09T20:05:00+00:00')
        self.assertTrue(new_frame.keep_connection)
        with sqlite3.connect(self.database) as db:
            new_raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
        self.assertEqual(normalize_raw_frame(self.database, new_raw_id), 'accepted')

        class FixedReadDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 10, 9, 20, 6, 0, tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        headers = self.headers('expected-admin')
        with mock.patch.object(web_app, 'datetime', FixedReadDateTime), \
                mock.patch.object(monitoring_module, 'datetime', FixedReadDateTime):
            latest = self.client.get(
                f'/api/station-monitoring/sites/{self.site_id}/latest', headers=headers)
            self.assertEqual(latest.status_code, 200, latest.get_json())
            self.assertEqual(
                [(item['business_metric'], item['standard_value'])
                 for item in latest.get_json()['items']], [('codmn', 1.3)])
            overview = self.client.get(
                f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers)
            self.assertEqual(overview.status_code, 200, overview.get_json())
            cycle = overview.get_json()['site']['latest_cycle']
            self.assertEqual((cycle['status'], cycle['formed_count'], cycle['expected_count']),
                             ('complete', 1, 1))
            listed = self.client.get('/api/station-monitoring/sites?scope=all', headers=headers)
            self.assertEqual(listed.status_code, 200, listed.get_json())
            listed_site = next(item for item in listed.get_json()['items']
                               if item['site_id'] == self.site_id)
            self.assertEqual(
                (listed_site['latest_cycle']['status'],
                 listed_site['latest_cycle']['formed_count'],
                 listed_site['latest_cycle']['expected_count']),
                ('complete', 1, 1))

    def test_bound_hj212_without_four_hour_average_fails_with_zero_writes(self):
        endpoint_id, station, password, _, server = self.install_bound_hj212_endpoint()
        server._process_raw_result(make_hj212(
            station=station, password=password, qn='20261010090500001',
            data_time='20261010090000',
            factors='w01001-Avg=7.01,w01001-Flag=N',
        ), '2026-10-10T01:05:00+00:00')
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['ph'],
                'version': 0,
                'idempotency_key': 'bound-hj212-no-cycle-evidence',
            })
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(
            response.get_json()['code'],
            'EXPECTED_FACTORS_CONFIGURATION_EVIDENCE_REQUIRED')
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM station_expected_factor_configs').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_endpoint_profiles WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_factor_mappings WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_business_schedules WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)

    def test_future_profile_conflict_fails_without_partial_configuration(self):
        endpoint_id, station, password, _, server = self.install_bound_hj212_endpoint()
        server._process_raw_result(make_hj212(
            station=station, password=password, qn='20261010000500001',
            data_time='20261010000000',
            factors='w01001-Avg=7.01,w01001-Flag=N',
        ), '2026-10-09T16:05:00+00:00')
        with sqlite3.connect(self.database) as db:
            db.execute("""INSERT INTO monitoring_endpoint_profiles(
                    endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                    expected_interval_seconds,effective_from,effective_to)
                VALUES (?,?,'Asia/Shanghai',1,'periodic',14400,
                        '2099-01-01T00:00:00+00:00',NULL)""", (endpoint_id, self.site_id))
            db.commit()
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['ph'],
                'version': 0,
                'idempotency_key': 'bound-hj212-future-profile-conflict',
            })
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(
            response.get_json()['code'], 'EXPECTED_FACTORS_CONFIGURATION_CONFLICT')
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM station_expected_factor_configs').fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_endpoint_profiles WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_factor_mappings WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_business_schedules WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)

    def test_approved_numeric_definitions_are_additive_and_precise(self):
        expected = [
            ('HJ212:001', 'ph', 'pH'),
            ('HJ212:004', 'water_temp', 'degC'),
            ('HJ212:006', 'conductivity', 'uS/cm'),
            ('HJ212:016', 'codmn', 'mg/L'),
            ('HJ212:060', 'ammonia', 'mg/L'),
            ('HJ212:065', 'total_nitrogen', 'mg/L'),
            ('HJ212:081', 'chlorophyll', 'mg/L'),
            ('HJ212:101', 'total_phosphorus', 'mg/L'),
            ('HJ212:153', 'turbidity', 'NTU'),
            ('HJ212:273', 'algae_density', '个/L'),
        ]
        with sqlite3.connect(self.database) as db:
            rows = db.execute("""SELECT protocol_code,business_metric,standard_unit
                FROM monitoring_factor_definitions
                WHERE protocol_code IN ('HJ212:001','HJ212:004','HJ212:006','HJ212:016',
                    'HJ212:060','HJ212:065','HJ212:081','HJ212:101','HJ212:153','HJ212:273')
                ORDER BY protocol_code""").fetchall()
        self.assertEqual(rows, expected)

    def test_tn_tp_mapping_conflict_rolls_back_expected_factor_save(self):
        endpoint_id, _, _, _ = self.install_hj212_profile(
            conflicting_total_nitrogen=True)
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['total_nitrogen'],
                'version': 0,
                'idempotency_key': 'hj212-tn-conflict',
            })
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(
            response.get_json()['code'], 'EXPECTED_FACTORS_MAPPING_CONFLICT')
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM station_expected_factor_configs').fetchone()[0], 0)
            self.assertEqual(db.execute("""SELECT COUNT(*)
                FROM monitoring_factor_mappings
                WHERE endpoint_id=? AND protocol_code='HJ212:w21001'""",
                (endpoint_id,)).fetchone()[0], 1)

    def test_tn_selection_without_hj212_profile_does_not_guess_mapping(self):
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['total_nitrogen'],
                'version': 0,
                'idempotency_key': 'tn-no-hj212-profile',
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        candidate = next(item for item in response.get_json()['candidates']
                         if item['business_metric'] == 'total_nitrogen')
        self.assertFalse(candidate['mapping_available'])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM monitoring_factor_mappings WHERE protocol_code='HJ212:w21001'"
            ).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
