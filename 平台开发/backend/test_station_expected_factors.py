import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
import station_monitoring_api as monitoring_api
import station_monitoring as monitoring_module
from hj212_parser import hj212_crc
from migrate_station_ingestion import apply_migration
from sl651_server import IngestionStorage, StationIngestServer, credential_hmac
from station_ingest_cutover import prepare_cutover
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

    def test_save_does_not_scan_evidence_or_repair_ingestion_configuration(self):
        endpoint_id, _, _, _, _ = self.install_bound_hj212_endpoint()
        tables = (
            'ingest_raw_frames', 'monitoring_endpoint_profiles',
            'monitoring_factor_mappings', 'monitoring_business_schedules',
        )
        with sqlite3.connect(self.database) as db:
            before = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in tables}

        forbidden = AssertionError('expected-factor save must not repair ingestion configuration')
        with mock.patch.object(
                monitoring_api, '_prepare_station_expected_hj212_evidence', side_effect=forbidden), \
                mock.patch.object(
                    monitoring_api, '_ensure_station_expected_hj212_profiles', side_effect=forbidden), \
                mock.patch.object(
                    monitoring_api, '_ensure_station_expected_hj212_standard_mappings',
                    side_effect=forbidden):
            response = self.client.put(
                f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
                headers=self.headers('expected-operator'), json={
                    'metrics': ['conductivity', 'turbidity'],
                    'version': 0,
                    'idempotency_key': 'selection-only-no-ingestion-repair',
                })

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['selected_metrics'], [
            'turbidity', 'conductivity',
        ])
        with sqlite3.connect(self.database) as db:
            after = {table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                     for table in tables}
            self.assertEqual(after, before)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM station_expected_factor_configs WHERE site_id=?',
                (self.site_id,)).fetchone()[0], 1)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_endpoint_profiles WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)

    def test_bound_endpoint_without_profile_normalizes_and_reaches_read_paths(self):
        endpoint_id, station, password, _, server = self.install_bound_hj212_endpoint()
        saved = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['turbidity', 'conductivity'],
                'version': 0,
                'idempotency_key': 'bound-no-profile-realtime-values',
            })
        self.assertEqual(saved.status_code, 200, saved.get_json())

        effective_from = datetime.fromisoformat(saved.get_json()['effective_from'])
        observed_at = effective_from.replace(microsecond=0) + timedelta(seconds=1)
        remaining = (observed_at - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            time.sleep(remaining + 0.02)
        local_observed = observed_at.astimezone(timezone(timedelta(hours=8)))
        accepted = server._process_raw_result(make_hj212(
            station=station, password=password,
            qn=local_observed.strftime('%Y%m%d%H%M%S') + '001',
            data_time=local_observed.strftime('%Y%m%d%H%M%S'), command='2011',
            factors=('w01003-Rtd=7.00,w01003-Flag=N;'
                     'w01014-Rtd=74.58,w01014-Flag=N'),
        ), (observed_at + timedelta(seconds=5)).isoformat())
        self.assertTrue(accepted.keep_connection)
        with sqlite3.connect(self.database) as db:
            raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
            self.assertEqual(db.execute(
                'SELECT disposition FROM ingest_raw_frames WHERE id=?',
                (raw_id,)).fetchone()[0], 'pending_parse')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')

        headers = self.headers('expected-admin')
        latest = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/latest', headers=headers)
        self.assertEqual(latest.status_code, 200, latest.get_json())
        values = {item['business_metric']: item for item in latest.get_json()['items']}
        self.assertEqual(values['turbidity']['standard_value_display'], '7.00')
        self.assertEqual(values['conductivity']['standard_value_display'], '74.58')
        self.assertEqual(values['turbidity']['source_type'], 'realtime_observation')

        overview = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers)
        self.assertEqual(overview.status_code, 200, overview.get_json())
        self.assertEqual(overview.get_json()['site']['latest_cycle']['status'], 'not_configured')
        listed = self.client.get(
            '/api/station-monitoring/sites?scope=all', headers=headers)
        self.assertEqual(listed.status_code, 200, listed.get_json())
        listed_site = next(item for item in listed.get_json()['items']
                           if item['site_id'] == self.site_id)
        self.assertEqual(listed_site['last_valid_observation_at'], observed_at.isoformat())
        self.assertEqual(listed_site['published_factor_count'], 2)
        trend = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend',
            query_string={
                'metric': 'turbidity',
                'start': (observed_at - timedelta(minutes=1)).isoformat(),
                'end': (observed_at + timedelta(minutes=1)).isoformat(),
            }, headers=headers)
        self.assertEqual(trend.status_code, 200, trend.get_json())
        self.assertEqual(trend.get_json()['decimal_precision'], 2)
        self.assertEqual(trend.get_json()['points'][0]['value_display'], '7.00')
        self.assertEqual(trend.get_json()['coverage']['state'], 'unconfigured')

        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_endpoint_profiles WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_factor_mappings WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM monitoring_business_schedules WHERE endpoint_id=?',
                (endpoint_id,)).fetchone()[0], 0)

    def test_existing_profile_and_mapping_conflicts_do_not_block_selection_save(self):
        endpoint_id, _, _, _ = self.install_hj212_profile(
            conflicting_total_nitrogen=True)
        with sqlite3.connect(self.database) as db:
            before = {
                table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in (
                    'monitoring_endpoint_profiles', 'monitoring_factor_mappings',
                    'monitoring_business_schedules')
            }
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['total_nitrogen', 'total_phosphorus'],
                'version': 0,
                'idempotency_key': 'selection-ignores-ingestion-conflicts',
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        with sqlite3.connect(self.database) as db:
            after = {
                table: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in before
            }
            self.assertEqual(after, before)
            self.assertEqual(db.execute(
                """SELECT business_metric FROM monitoring_factor_mappings
                    WHERE endpoint_id=? AND protocol_code='HJ212:w21001'""",
                (endpoint_id,)).fetchone()[0], 'ammonia')

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
            list(monitoring_api._STATION_EXPECTED_FACTOR_METRICS),
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

    def legacy_contract_hj212_evidence_preparation_does_not_hold_the_sqlite_write_lock(self):
        self.install_hj212_profile()
        headers = self.headers('expected-operator')
        entered = threading.Event()
        release = threading.Event()
        result = {}
        original = monitoring_api._station_hj212_endpoint_evidence

        def paused_evidence(db, endpoint_id, *, limit=200, raw_ids=None):
            entered.set()
            if not release.wait(3):
                raise AssertionError('test did not release evidence preparation')
            return original(db, endpoint_id, limit=limit, raw_ids=raw_ids)

        def save_expected_factors():
            with web_app.app.test_client() as client:
                result['response'] = client.put(
                    f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
                    headers=headers,
                    json={
                        'metrics': ['ph'],
                        'version': 0,
                        'idempotency_key': 'prepare-outside-write-lock',
                    },
                )

        with mock.patch.object(
                monitoring_api, '_station_hj212_endpoint_evidence', side_effect=paused_evidence):
            worker = threading.Thread(target=save_expected_factors)
            worker.start()
            self.assertTrue(entered.wait(2), 'save did not reach evidence preparation')
            contender = sqlite3.connect(self.database, timeout=0.2)
            try:
                contender.execute('BEGIN IMMEDIATE')
                contender.rollback()
            finally:
                contender.close()
                release.set()
            worker.join(5)

        self.assertFalse(worker.is_alive())
        response = result['response']
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_retry_replays_committed_save_after_response_generation_is_interrupted(self):
        headers = self.headers('expected-operator')
        path = f'/api/station-monitoring/sites/{self.site_id}/expected-factors'
        request = {
            'metrics': ['ph'],
            'version': 0,
            'idempotency_key': 'committed-response-interrupted',
        }
        original = monitoring_api._station_expected_factor_payload
        interrupted = False

        def interrupt_once(*args, **kwargs):
            nonlocal interrupted
            payload = original(*args, **kwargs)
            if not interrupted:
                interrupted = True
                raise ConnectionResetError('simulated response interruption after commit')
            return payload

        previous_propagate = web_app.app.config.get('PROPAGATE_EXCEPTIONS')
        web_app.app.config['PROPAGATE_EXCEPTIONS'] = False
        try:
            with mock.patch.object(
                    monitoring_api, '_station_expected_factor_payload', side_effect=interrupt_once):
                first = self.client.put(path, headers=headers, json=request)
                retry = self.client.put(path, headers=headers, json=request)
        finally:
            web_app.app.config['PROPAGATE_EXCEPTIONS'] = previous_propagate

        self.assertEqual(first.status_code, 500)
        self.assertEqual(retry.status_code, 200, retry.get_json())
        self.assertTrue(retry.get_json()['replayed'])
        with sqlite3.connect(self.database) as db:
            rows = db.execute(
                'SELECT id,version FROM station_expected_factor_configs '
                'WHERE site_id=? AND idempotency_key=?',
                (self.site_id, request['idempotency_key']),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 1)

    def test_busy_writer_returns_retryable_response_without_partial_configuration(self):
        headers = self.headers('expected-operator')
        path = f'/api/station-monitoring/sites/{self.site_id}/expected-factors'
        request = {
            'metrics': ['ph'],
            'version': 0,
            'idempotency_key': 'busy-writer-retry',
        }
        writer = sqlite3.connect(self.database, timeout=0.1)
        writer.execute('BEGIN IMMEDIATE')
        try:
            blocked = self.client.put(path, headers=headers, json=request)
        finally:
            writer.rollback()
            writer.close()

        self.assertEqual(blocked.status_code, 503, blocked.get_json())
        self.assertEqual(blocked.get_json()['code'], 'EXPECTED_FACTORS_BUSY')
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM station_expected_factor_configs WHERE site_id=?',
                (self.site_id,),
            ).fetchone()[0], 0)

        retry = self.client.put(path, headers=headers, json=request)
        self.assertEqual(retry.status_code, 200, retry.get_json())
        self.assertEqual(retry.get_json()['version'], 1)

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

    def test_first_selection_and_reselection_keep_existing_mapped_results_visible(self):
        endpoint_id, station, password, pepper = self.install_hj212_profile()
        with sqlite3.connect(self.database) as db:
            db.execute("""INSERT INTO monitoring_factor_mappings(
                    endpoint_id,protocol_code,business_metric,expected_interval_seconds,
                    tolerance_seconds,effective_from,enabled)
                VALUES (?,'HJ212:w01009','dissolved_oxygen',14400,600,
                        '2020-01-01T00:00:00+00:00',1)""", (endpoint_id,))
            db.execute("""UPDATE monitoring_endpoint_profiles
                SET expected_interval_seconds=14400 WHERE endpoint_id=?""", (endpoint_id,))
            db.execute("""UPDATE monitoring_factor_mappings
                SET expected_interval_seconds=14400 WHERE endpoint_id=?""", (endpoint_id,))
            db.execute("""UPDATE monitoring_business_schedules
                SET interval_seconds=14400 WHERE endpoint_id=?""", (endpoint_id,))
            db.commit()
        server = StationIngestServer(
            IngestionStorage(self.database, pepper), host='127.0.0.1', port=0)
        accepted = server._process_raw_result(make_hj212(
            station=station, password=password, qn='20261021160500001',
            data_time='20261021160000',
            factors='w01001-Avg=7.2300,w01001-Flag=N;'
                    'w01009-Avg=8.40,w01009-Flag=N',
        ), '2026-10-21T08:05:00+00:00')
        self.assertTrue(accepted.keep_connection)
        with sqlite3.connect(self.database) as db:
            raw_id = db.execute('SELECT MAX(id) FROM ingest_raw_frames').fetchone()[0]
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')

        headers = self.headers('expected-operator')
        expected_path = f'/api/station-monitoring/sites/{self.site_id}/expected-factors'

        first = self.client.put(expected_path, headers=headers, json={
            'metrics': ['ph', 'dissolved_oxygen'],
            'version': 0,
            'idempotency_key': 'existing-results-first-selection',
        })
        self.assertEqual(first.status_code, 200, first.get_json())
        self._assert_existing_factor_results_visible(headers, expected_version=1)

        removed = self.client.put(expected_path, headers=headers, json={
            'metrics': ['ph'],
            'version': 1,
            'idempotency_key': 'existing-results-remove-do',
        })
        self.assertEqual(removed.status_code, 200, removed.get_json())
        latest_after_remove = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/latest', headers=headers)
        self.assertEqual(latest_after_remove.status_code, 200, latest_after_remove.get_json())
        self.assertEqual(
            [item['business_metric'] for item in latest_after_remove.get_json()['items']],
            ['ph'],
        )

        reselected = self.client.put(expected_path, headers=headers, json={
            'metrics': ['ph', 'dissolved_oxygen'],
            'version': 2,
            'idempotency_key': 'existing-results-reselect-do',
        })
        self.assertEqual(reselected.status_code, 200, reselected.get_json())
        self._assert_existing_factor_results_visible(headers, expected_version=3)

    def _assert_existing_factor_results_visible(self, headers, *, expected_version):
        latest = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/latest', headers=headers)
        overview = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/overview?detail=summary',
            headers=headers)
        listed = self.client.get(
            '/api/station-monitoring/sites?scope=mine', headers=headers)
        trend = self.client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend',
            headers=headers,
            query_string={
                'metric': 'dissolved_oxygen',
                'start': '2026-10-21T00:00:00+00:00',
                'end': '2026-10-21T12:00:00+00:00',
            },
        )
        for response in (latest, overview, listed, trend):
            self.assertEqual(response.status_code, 200, response.get_json())
        latest_metrics = {
            item['business_metric']: item
            for item in latest.get_json()['items']
        }
        self.assertEqual(set(latest_metrics), {'ph', 'dissolved_oxygen'})
        self.assertEqual(latest_metrics['ph']['standard_value_display'], '7.2300')
        self.assertEqual(latest_metrics['dissolved_oxygen']['standard_value_display'], '8.40')
        overview_body = overview.get_json()
        self.assertEqual(overview_body['site']['expected_factor_version'], expected_version)
        self.assertEqual(
            {item['business_metric'] for item in overview_body['monitoring']['latest_values']},
            {'ph', 'dissolved_oxygen'},
        )
        list_item = next(item for item in listed.get_json()['items']
                         if item['site_id'] == self.site_id)
        self.assertEqual(list_item['expected_factor_version'], expected_version)
        self.assertEqual(
            {item['business_metric'] for item in list_item['latest_cycle']['factors']
             if item['status'] == 'value'},
            {'ph', 'dissolved_oxygen'},
        )
        self.assertEqual(trend.get_json()['points'][0]['value_display'], '8.40')

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

    def legacy_contract_selected_tn_tp_add_standard_hj212_mappings_and_form_complete_cycle(self):
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

    def legacy_contract_selected_standard_factor_adds_dissolved_oxygen_mapping(self):
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

    def legacy_contract_missing_mapping_still_requires_tolerance_and_rolls_back(self):
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

    def legacy_contract_evidenced_numeric_family_adds_only_observed_selected_alias(self):
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

    def legacy_contract_process_only_numeric_code_does_not_authorize_alias_mapping(self):
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

    def legacy_contract_bound_hj212_evidence_creates_common_profile_and_reaches_all_read_paths(self):
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
        with mock.patch.object(monitoring_api, 'datetime', FixedDateTime):
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
        with mock.patch.object(monitoring_api, 'datetime', FixedReadDateTime), \
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

    def legacy_contract_bound_codmn_2061_rtd_creates_profile_and_reaches_all_read_paths(self):
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
        with mock.patch.object(monitoring_api, 'datetime', FixedDateTime):
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
        with mock.patch.object(monitoring_api, 'datetime', FixedReadDateTime), \
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

    def legacy_contract_bound_hj212_without_four_hour_average_fails_with_zero_writes(self):
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

    def legacy_contract_future_profile_conflict_fails_without_partial_configuration(self):
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

    def legacy_contract_tn_tp_mapping_conflict_rolls_back_expected_factor_save(self):
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

    def test_expected_factor_save_uses_endpoint_index_with_representative_history(self):
        endpoint_id, station, password, _, server = self.install_bound_hj212_endpoint()
        accepted = server._process_raw_result(make_hj212(
            station=station, password=password, qn='20261016080500001',
            data_time='20261016080000', factors='w01019-Rtd=1.2,w01019-Flag=N',
        ), '2026-10-16T00:05:00+00:00')
        self.assertTrue(accepted.keep_connection)
        with sqlite3.connect(self.database) as db:
            unrelated_endpoint = db.execute("""INSERT INTO trusted_endpoints(
                    station_code,credential_hmac,business_site_id,enabled,endpoint_state)
                VALUES ('EXPECTED-LARGE-HISTORY','credential',?,1,'bound')""",
                (self.other_site_id,)).lastrowid
            db.execute("""WITH RECURSIVE sequence(value) AS (
                    SELECT 1 UNION ALL SELECT value+1 FROM sequence WHERE value<20000
                )
                INSERT INTO ingest_raw_frames(
                    endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                    crc_status,authentication_status,disposition,persistence_state)
                SELECT ?,'EXPECTED-LARGE-HISTORY','2026-10-15T00:00:00+00:00',
                       printf('%064x',value),X'78',1,'valid','authenticated','accepted','persisted'
                FROM sequence""", (unrelated_endpoint,))
            db.execute("""INSERT INTO ingest_frame_protocols(raw_frame_id,protocol_family,parser_version)
                SELECT id,'hj212','large-history-test' FROM ingest_raw_frames
                WHERE endpoint_id=?""", (unrelated_endpoint,))
            db.commit()
        prepare_cutover(
            self.database, None,
            now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
            recovery_reference="isolated-expected-factor-test",
        )
        with sqlite3.connect(self.database) as db:
            plan = db.execute("""EXPLAIN QUERY PLAN SELECT raw.id
                FROM ingest_raw_frames raw
                     INDEXED BY idx_ingest_raw_authenticated_endpoint_received
                JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
                WHERE raw.endpoint_id=? AND raw.authentication_status='authenticated'
                  AND protocol.protocol_family='hj212'
                ORDER BY raw.id DESC LIMIT 200""", (endpoint_id,)).fetchall()
        detail = ' '.join(str(row[3]) for row in plan)
        self.assertIn('idx_ingest_raw_authenticated_endpoint_received', detail)
        self.assertNotIn('SCAN raw', detail)

        started = time.perf_counter()
        response = self.client.put(
            f'/api/station-monitoring/sites/{self.site_id}/expected-factors',
            headers=self.headers('expected-operator'), json={
                'metrics': ['codmn'], 'version': 0,
                'idempotency_key': 'large-history-indexed-save',
            })
        elapsed = time.perf_counter() - started
        self.expected_factor_save_elapsed = elapsed
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertLess(elapsed, 5.0, f'indexed expected-factor save took {elapsed:.3f}s')


if __name__ == '__main__':
    unittest.main()
