import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
from migrate_station_ingestion import apply_migration, verify_station_monitoring_schema
from sl651_parser import (FrameError, UP_FLOW_CONTROL, crc16_modbus, encode_bcd_observation_time,
                          encode_bcd_time, encode_station_code, parse_frame, parse_water_quality_payload,
                          parse_water_quality_report)
from sl651_server import IngestionStorage, credential_hmac
from station_monitoring import _project_business_observation, expected_business_slots, normalize_raw_frame


def bcd_number(value, digits, precision=0):
    encoded = f"{int(round(value * (10 ** precision))):0{digits}d}"
    if len(encoded) % 2:
        encoded = "0" + encoded
    return bytes((int(encoded[index]) << 4) | int(encoded[index + 1]) for index in range(0, len(encoded), 2))


def make_32h(payload, serial=7, sent_at=None, observed_at=None, payload_station='0012345678'):
    sent_at = sent_at or datetime(2020, 6, 12, 2, 0, 0)
    observed_at = observed_at or sent_at.replace(second=0)
    body = b'\xf1\xf1' + encode_station_code(payload_station) + b'\x51\xf0\xf0' + encode_bcd_observation_time(observed_at) + payload
    content = serial.to_bytes(2, "big") + encode_bcd_time(sent_at) + body
    prefix = b"\x7e\x7e\x10" + encode_station_code("0012345678") + b"\x12\x34\x32" + len(content).to_bytes(2, "big") + b"\x02" + content + bytes((UP_FLOW_CONTROL,))
    return prefix + crc16_modbus(prefix).to_bytes(2, "big")


class StationMonitoringNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "isolated-monitoring.db"
        self.previous_database = web_app.DB_PATH
        self.previous_monitoring_access_mode = web_app.STATION_MONITORING_ACCESS_MODE
        web_app.STATION_MONITORING_ACCESS_MODE = 'public'
        web_app.DB_PATH = str(self.database)
        web_app.init_db()
        apply_migration(self.database, Path(self.temp_dir.name) / "backups")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO sites(code,name,type) VALUES ('MON-1','隔离监测站','water')")
            self.site_id = connection.execute("SELECT id FROM sites WHERE code='MON-1'").fetchone()[0]
            connection.execute("INSERT INTO sites(code,name,type) VALUES ('MON-2','未接入站','water')")
            self.other_site_id = connection.execute("SELECT id FROM sites WHERE code='MON-2'").fetchone()[0]
            connection.execute("INSERT INTO users(username,password_hash,role,real_name) VALUES ('monitor-admin',?,'admin','Admin')", (web_app._hash_pw("pw"),))
            self.admin_id = connection.execute("SELECT id FROM users WHERE username='monitor-admin'").fetchone()[0]
            connection.execute("INSERT INTO users(username,password_hash,role,real_name) VALUES ('monitor-operator',?,'operator','Operator')", (web_app._hash_pw("pw"),))
            self.operator_id = connection.execute("SELECT id FROM users WHERE username='monitor-operator'").fetchone()[0]
            connection.executemany("INSERT INTO user_roles(user_id,role) VALUES (?,?)", ((self.admin_id, 'admin'), (self.operator_id, 'operator')))
            connection.execute("INSERT INTO user_sites(user_id,site_id) VALUES (?,?)", (self.operator_id, self.site_id))
            pepper = "isolated-monitoring-pepper"
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,?,'bound')",
                ("0012345678", credential_hmac(b"\x12\x34", pepper), self.site_id),
            )
            self.endpoint_id = connection.execute("SELECT id FROM trusted_endpoints").fetchone()[0]
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,instrument_asset_code,
                   expected_granularity,expected_interval_seconds,effective_from) VALUES (?,?,?,'realtime',60,?)""",
                (self.endpoint_id, self.site_id, 'INST-A', '2020-01-01T00:00:00+00:00'),
            )
            connection.executemany(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   expected_interval_seconds,tolerance_seconds,effective_from) VALUES (?,?,?,?,?,?,?)""",
                ((self.endpoint_id, '0311', 'water_temp', 'INST-A', 60, 15, '2020-01-01T00:00:00+00:00'),
                 (self.endpoint_id, '4612', 'ph', 'INST-A', 60, 15, '2020-01-01T00:00:00+00:00'),
                 (self.endpoint_id, '4C1A', 'ammonia', 'INST-A', 60, 15, '2020-01-01T00:00:00+00:00'),
                 (self.endpoint_id, '4A11', 'codmn', 'INST-A', 60, 15, '2020-01-01T00:00:00+00:00'),
                 (self.endpoint_id, '4B19', None, 'INST-A', None, None, '2020-01-01T00:00:00+00:00')),
            )
            connection.execute(
                """INSERT INTO monitoring_business_schedules(
                       endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                       tolerance_seconds,effective_from)
                   VALUES (?,NULL,'Asia/Shanghai',14400,'00:00:00',600,'2020-01-01T00:00:00+00:00')""",
                (self.endpoint_id,),
            )
            connection.commit()
        self.storage = IngestionStorage(self.database, pepper)

    def tearDown(self):
        web_app.STATION_MONITORING_ACCESS_MODE = self.previous_monitoring_access_mode
        web_app.DB_PATH = self.previous_database
        self.temp_dir.cleanup()

    def _headers(self, username):
        client = web_app.app.test_client()
        response = client.post('/api/auth/login', json={'username': username, 'password': 'pw'})
        self.assertEqual(response.status_code, 200, response.get_json())
        return {'Authorization': 'Bearer ' + response.get_json()['token']}

    def _persist(self, payload, serial=7, *, sent_at=None, observed_at=None, payload_station='0012345678', received_at='2026-09-09T00:00:00+00:00'):
        frame = parse_frame(make_32h(payload, serial=serial, sent_at=sent_at, observed_at=observed_at, payload_station=payload_station))
        raw_id, duplicate = self.storage.persist_parsed(frame, self.storage.authenticate(frame), received_at)
        self.assertFalse(duplicate)
        return raw_id

    def test_32h_golden_factor_payload_covers_current_water_quality_and_status_definitions(self):
        payload = (
            bytes.fromhex('0311') + bcd_number(12.3, 3, 1) + bytes.fromhex('4612') + bcd_number(7.12, 4, 2)
            + bytes.fromhex('4711') + bcd_number(8.0, 4, 1) + bytes.fromhex('4818') + bcd_number(12345, 5)
            + bytes.fromhex('4910') + bcd_number(100, 3) + bytes.fromhex('4A11') + bcd_number(5.4, 4, 1)
            + bytes.fromhex('4B19') + bcd_number(200.0, 5, 1) + bytes.fromhex('4C1A') + bcd_number(0, 6, 2)
            + bytes.fromhex('4D1B') + bcd_number(0.123, 5, 3) + bytes.fromhex('4E1A') + bcd_number(1.23, 5, 2)
            + bytes.fromhex('4F12') + bcd_number(1.23, 4, 2) + bytes.fromhex('4520') + b'\x00\x00\x00\x01'
            + bytes.fromhex('3812') + bcd_number(12.34, 4, 2) + bytes.fromhex('FF0108') + bcd_number(98, 2)
        )
        factors = parse_water_quality_payload(payload)
        self.assertEqual(len(factors), 14)
        self.assertEqual({factor.protocol_code for factor in factors}, {
            '0311', '4612', '4711', '4818', '4910', '4A11', '4B19', '4C1A', '4D1B', '4E1A', '4F12', '4520', '3812', 'FF0108',
        })
        self.assertEqual(next(factor.raw_value for factor in factors if factor.protocol_code == '4C1A'), 0.0)
        invalid = parse_water_quality_payload(bytes.fromhex('0311') + b'\xfa\x00')
        self.assertEqual(invalid[0].quality, 'invalid')

    def test_normalizes_aliases_zero_status_and_current_reparse_version(self):
        payload = (
            bytes.fromhex('0311') + bcd_number(12.3, 3, 1)
            + bytes.fromhex('4C1A') + bcd_number(0, 6, 2)
            + bytes.fromhex('4A11') + bcd_number(5.4, 4, 1)
            + bytes.fromhex('4B19') + bcd_number(200.0, 5, 1)
            + bytes.fromhex('4520') + b'\x00\x00\x00\x01'
        )
        raw_id = self._persist(
            payload, sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0))
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'already_normalized')
        self.assertEqual(normalize_raw_frame(self.database, raw_id, normalization_version='reparse-v2'), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            verify_station_monitoring_schema(connection)
            raw = connection.execute("SELECT raw_frame, persistence_state FROM ingest_raw_frames WHERE id=?", (raw_id,)).fetchone()
            self.assertTrue(raw['raw_frame'])
            self.assertEqual(raw['persistence_state'], 'persisted')
            current = connection.execute("SELECT COUNT(*) FROM observation_batches WHERE raw_frame_id=? AND is_current=1", (raw_id,)).fetchone()[0]
            self.assertEqual(current, 1)
            values = {row['business_metric']: row['standard_value'] for row in connection.execute(
                """SELECT business_metric,standard_value FROM observation_values WHERE is_current=1 AND is_published=1"""
            )}
            self.assertEqual(values, {'water_temp': 12.3, 'ammonia': 0.0, 'codmn': 5.4})
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM monitoring_status_events WHERE event_axis='rtu'").fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM monitoring_business_observations WHERE is_current=1 AND slot_state='selected'"
            ).fetchone()[0], 3)
        client = web_app.app.test_client()
        operator = self._headers('monitor-operator')
        latest = client.get(f'/api/station-monitoring/sites/{self.site_id}/latest', headers=operator)
        self.assertEqual(latest.status_code, 200, latest.get_json())
        self.assertEqual({item['business_metric'] for item in latest.get_json()['items']}, {'water_temp', 'ammonia', 'codmn'})
        self.assertEqual(next(item['standard_value'] for item in latest.get_json()['items'] if item['business_metric'] == 'ammonia'), 0.0)
        trend = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=ammonia&start=2020-06-11T16:00:00%2B00:00&end=2020-06-12T15:59:59%2B00:00',
            headers=operator,
        )
        self.assertEqual(trend.status_code, 200, trend.get_json())
        self.assertEqual(trend.get_json()['points'][0]['value'], 0.0)
        self.assertEqual(trend.get_json()['standard_unit'], 'mg/L')

    def test_partial_unmapped_evidence_and_read_permission_boundaries(self):
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(10.0, 3, 1) + bytes.fromhex('FEED'), serial=8)
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'partial')
        client = web_app.app.test_client()
        operator = self._headers('monitor-operator')
        self.assertEqual(client.get(f'/api/station-monitoring/sites/{self.other_site_id}/latest', headers=operator).status_code, 403)
        self.assertEqual(client.get(f'/api/station-monitoring/sites/{self.other_site_id}/latest', headers=self._headers('monitor-admin')).status_code, 409)
        self.assertEqual(client.get('/api/station-monitoring/quality-issues', headers=operator).status_code, 403)
        issues = client.get('/api/station-monitoring/quality-issues', headers=self._headers('monitor-admin'))
        self.assertEqual(issues.status_code, 200, issues.get_json())
        self.assertEqual(issues.get_json()['items'][0]['issue_type'], 'unmapped_factor')

    def test_profile_and_factor_mapping_effective_periods_preserve_instrument_history(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE monitoring_endpoint_profiles SET effective_to=? WHERE endpoint_id=?",
                ('2020-06-11T18:30:00+00:00', self.endpoint_id),
            )
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,instrument_asset_code,
                   expected_granularity,expected_interval_seconds,effective_from)
                   VALUES (?,?,?,'realtime',60,?)""",
                (self.endpoint_id, self.site_id, 'INST-B', '2020-06-11T18:30:00+00:00'),
            )
            connection.execute(
                "UPDATE monitoring_factor_mappings SET effective_to=? WHERE endpoint_id=? AND protocol_code='0311'",
                ('2020-06-11T18:30:00+00:00', self.endpoint_id),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,effective_from)
                   VALUES (?, '0311', 'water_temp', 'INST-B', '2020-06-11T18:30:00+00:00')""",
                (self.endpoint_id,),
            )
            connection.execute(
                "UPDATE monitoring_endpoint_profiles SET effective_to=? WHERE endpoint_id=? AND instrument_asset_code='INST-B'",
                ('2020-06-11T19:30:00+00:00', self.endpoint_id),
            )
            connection.execute(
                "UPDATE monitoring_factor_mappings SET effective_to=? WHERE endpoint_id=? AND protocol_code='0311' AND instrument_asset_code='INST-B'",
                ('2020-06-11T19:30:00+00:00', self.endpoint_id),
            )
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,instrument_asset_code,
                   expected_granularity,expected_interval_seconds,effective_from)
                   VALUES (?,?,?,'realtime',60,?)""",
                (self.endpoint_id, self.site_id, 'INST-A', '2020-06-11T19:30:00+00:00'),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,effective_from)
                   VALUES (?, '0311', 'water_temp', 'INST-A', '2020-06-11T19:30:00+00:00')""",
                (self.endpoint_id,),
            )
            connection.commit()
        before = self._persist(bytes.fromhex('0311') + bcd_number(10.0, 3, 1), serial=9)
        after_frame = parse_frame(make_32h(bytes.fromhex('0311') + bcd_number(11.0, 3, 1), serial=10,
                                            sent_at=datetime(2020, 6, 12, 2, 30, 0)))
        after, duplicate = self.storage.persist_parsed(
            after_frame, self.storage.authenticate(after_frame), '2026-09-09T00:00:00+00:00'
        )
        self.assertFalse(duplicate)
        returned = self._persist(bytes.fromhex('0311') + bcd_number(12.0, 3, 1), serial=11,
                                 sent_at=datetime(2020, 6, 12, 3, 30, 0), observed_at=datetime(2020, 6, 12, 3, 30))
        self.assertEqual(normalize_raw_frame(self.database, before), 'accepted')
        self.assertEqual(normalize_raw_frame(self.database, after), 'accepted')
        self.assertEqual(normalize_raw_frame(self.database, returned), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            instruments = [row[0] for row in connection.execute(
                """SELECT v.instrument_asset_code FROM observation_values v
                   JOIN observation_batches b ON b.id=v.observation_batch_id
                   WHERE v.protocol_code='0311' ORDER BY b.observed_at"""
            )]
        self.assertEqual(instruments, ['INST-A', 'INST-B', 'INST-A'])

    def test_latest_excludes_old_value_after_a_to_b_to_a_mapping_returns_without_new_report(self):
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(10.0, 3, 1), serial=21)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE monitoring_factor_mappings SET effective_to=? WHERE endpoint_id=? AND protocol_code='0311'",
                ('2020-06-11T18:30:00+00:00', self.endpoint_id),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   effective_from,effective_to) VALUES (?, '0311', 'water_temp', 'INST-B', ?, ?)""",
                (self.endpoint_id, '2020-06-11T18:30:00+00:00', '2020-06-11T19:30:00+00:00'),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   effective_from) VALUES (?, '0311', 'water_temp', 'INST-A', ?)""",
                (self.endpoint_id, '2020-06-11T19:30:00+00:00'),
            )
            connection.commit()
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/latest', headers=self._headers('monitor-admin')
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn('water_temp', {item['business_metric'] for item in response.get_json()['items']})

    def test_trend_excludes_old_business_metric_after_current_mapping_changes_metric(self):
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(10.0, 3, 1), serial=22)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE monitoring_factor_mappings SET effective_to=? WHERE endpoint_id=? AND protocol_code='0311'",
                ('2020-06-11T18:30:00+00:00', self.endpoint_id),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   expected_interval_seconds,tolerance_seconds,effective_from)
                   VALUES (?, '0311', 'new_water_temp', 'INST-A', 60, 15, ?)""",
                (self.endpoint_id, '2020-06-11T18:30:00+00:00'),
            )
            connection.commit()
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=new_water_temp&start=2020-06-11T18:00:00%2B00:00&end=2020-06-11T18:10:00%2B00:00',
            headers=self._headers('monitor-admin'),
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['points'], [])

    def test_overlapping_enabled_factor_mapping_is_rejected(self):
        with closing(sqlite3.connect(self.database)) as connection:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'overlapping factor mapping'):
                connection.execute(
                    """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                       effective_from) VALUES (?, '0311', 'water_temp', 'CONFLICT', ?)""",
                    (self.endpoint_id, '2020-06-11T18:00:00+00:00'),
                )

    def test_expired_endpoint_profile_is_not_reported_as_connected(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE monitoring_endpoint_profiles SET effective_to=? WHERE endpoint_id=?",
                ('2021-01-01T00:00:00+00:00', self.endpoint_id),
            )
            connection.commit()
        client = web_app.app.test_client()
        response = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/latest', headers=self._headers('monitor-admin')
        )
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()['code'], 'MONITORING_NOT_CONNECTED')

    def test_complete_pdf_body_preserves_distinct_times_and_late_observation_position(self):
        reported = datetime(2020, 6, 12, 2, 0, 45)
        observed = datetime(2020, 6, 11, 23, 30)
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(9.9, 3, 1), serial=31,
                               sent_at=reported, observed_at=observed,
                               received_at='2026-09-09T00:00:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                'SELECT reported_at, observed_at, received_at FROM observation_batches WHERE raw_frame_id=?', (raw_id,)
            ).fetchone()
        self.assertEqual(row[0], '2020-06-11T18:00:45+00:00')
        self.assertEqual(row[1], '2020-06-11T15:30:00+00:00')
        self.assertEqual(row[2], '2026-09-09T00:00:00+00:00')
        self.assertLess(row[1], row[0])

    def test_payload_station_mismatch_is_quarantined_without_projection(self):
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(9.9, 3, 1), serial=32, payload_station='0099999999')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'waiting_reparse')
        with closing(sqlite3.connect(self.database)) as connection:
            raw = connection.execute('SELECT disposition,persistence_state FROM ingest_raw_frames WHERE id=?', (raw_id,)).fetchone()
            issue = connection.execute('SELECT issue_type FROM monitoring_quality_issues WHERE raw_frame_id=?', (raw_id,)).fetchone()
            batches = connection.execute('SELECT COUNT(*) FROM observation_batches WHERE raw_frame_id=?', (raw_id,)).fetchone()[0]
        self.assertEqual(tuple(raw), ('quarantined', 'persisted'))
        self.assertEqual(issue[0], 'payload_station_mismatch')
        self.assertEqual(batches, 0)

    def test_invalid_factor_is_partial_without_losing_surrounding_values_or_inflating_issue_count(self):
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(10.0, 3, 1) + bytes.fromhex('4612') + b'\xfa\x00'
            + bytes.fromhex('4C1A') + bcd_number(1.25, 6, 2), serial=33
        )
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'partial')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'already_normalized')
        with closing(sqlite3.connect(self.database)) as connection:
            values = connection.execute(
                'SELECT protocol_code,quality,standard_value FROM observation_values ORDER BY id'
            ).fetchall()
            issue = connection.execute(
                "SELECT occurrence_count FROM monitoring_quality_issues WHERE raw_frame_id=? AND issue_type='invalid_factor_value'",
                (raw_id,),
            ).fetchone()[0]
        self.assertEqual(values, [('0311', 'valid', 10.0), ('4612', 'invalid', None), ('4C1A', 'valid', 1.25)])
        self.assertEqual(issue, 1)

    def test_summary_uses_received_communication_and_observed_data_freshness(self):
        received_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(10.0, 3, 1), serial=34, received_at=received_at)
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/summary', headers=self._headers('monitor-admin')
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(body['axes']['communication']['state'], 'fresh')
        self.assertEqual(body['axes']['data']['state'], 'no_valid_observation')
        self.assertEqual(set(body['axes']), {'communication', 'data'})
        self.assertEqual(len(body['axes']['data']['factors']), 4)
        self.assertEqual(body['attention_level'], 'attention')
        self.assertEqual(body['last_received_at'], received_at)

    def test_endpoint_timezone_is_resolved_before_utc_profile_validity_and_communication_uses_received_at(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE monitoring_endpoint_profiles SET effective_to=? WHERE endpoint_id=?",
                ('2020-06-11T19:00:00+00:00', self.endpoint_id),
            )
            connection.commit()
        received_at = '2026-09-09T01:02:03+00:00'
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(7.7, 3, 1), serial=35,
            sent_at=datetime(2020, 6, 12, 2, 0, 0), observed_at=datetime(2020, 6, 12, 2, 0),
            received_at=received_at,
        )
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            event = connection.execute(
                "SELECT occurred_at,received_at FROM monitoring_status_events WHERE observation_batch_id=(SELECT id FROM observation_batches WHERE raw_frame_id=?)",
                (raw_id,),
            ).fetchone()
        self.assertEqual(tuple(event), (received_at, received_at))

    def test_ambiguous_endpoint_timezone_and_naive_configuration_are_rejected(self):
        with closing(sqlite3.connect(self.database)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,effective_from)
                       VALUES (?, '0311', 'water_temp', '2020-01-01T00:00:00')""", (self.endpoint_id,)
                )
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,timezone,effective_from)
                   VALUES (?,?,'UTC','2022-01-01T00:00:00+00:00')""", (self.endpoint_id, self.site_id),
            )
            connection.commit()
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(7.7, 3, 1), serial=36)
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'waiting_reparse')
        with closing(sqlite3.connect(self.database)) as connection:
            issue = connection.execute('SELECT issue_type FROM monitoring_quality_issues WHERE raw_frame_id=?', (raw_id,)).fetchone()[0]
        self.assertEqual(issue, 'ambiguous_endpoint_timezone')

    def test_profile_instrument_and_definition_metric_fallback_are_visible_in_latest_and_trend(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE monitoring_factor_mappings SET instrument_asset_code=NULL,business_metric=NULL WHERE endpoint_id=? AND protocol_code='0311'",
                (self.endpoint_id,),
            )
            connection.commit()
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(6.6, 3, 1), serial=37,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0))
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        client = web_app.app.test_client()
        headers = self._headers('monitor-admin')
        latest = client.get(f'/api/station-monitoring/sites/{self.site_id}/latest', headers=headers)
        self.assertEqual(latest.status_code, 200, latest.get_json())
        water = next(item for item in latest.get_json()['items'] if item['business_metric'] == 'water_temp')
        self.assertEqual(water['instrument_asset_code'], 'INST-A')
        trend = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-11T20:00:00%2B00:00&end=2020-06-11T20:10:00%2B00:00',
            headers=headers,
        )
        self.assertEqual(trend.status_code, 200, trend.get_json())
        self.assertEqual(trend.get_json()['points'][0]['value'], 6.6)

    def test_trend_counts_business_schedule_gaps_across_day_boundary(self):
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(5.5, 3, 1), serial=38,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-11T16:00:00%2B00:00&end=2020-06-12T15:59:59%2B00:00',
            headers=self._headers('monitor-admin'),
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        coverage = response.get_json()['coverage']
        self.assertEqual(coverage['expected_points'], 6)
        self.assertEqual(coverage['valid_points'], 1)
        self.assertEqual(coverage['missing_points'], 5)
        self.assertEqual(coverage['gap_count'], 2)
        self.assertAlmostEqual(coverage['coverage_rate'], 1 / 6)

    def test_trend_coverage_uses_window_slots_and_separates_suspect_from_valid(self):
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(5.5, 3, 1), serial=39,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE monitoring_business_observations SET quality='suspect' WHERE observation_batch_id=(SELECT id FROM observation_batches WHERE raw_frame_id=?)", (raw_id,))
            connection.commit()
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-11T16:00:00%2B00:00&end=2020-06-12T15:59:59%2B00:00',
            headers=self._headers('monitor-admin'),
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        coverage = response.get_json()['coverage']
        self.assertEqual(coverage['expected_points'], 6)
        self.assertEqual(coverage['valid_points'], 0)
        self.assertEqual(coverage['displayed_points'], 1)
        self.assertEqual(coverage['missing_points'], 6)
        self.assertEqual(coverage['gap_count'], 1)
        self.assertEqual(coverage['coverage_rate'], 0)
        self.assertEqual(coverage['suspect_points'], 1)
        latest = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/latest',
            headers=self._headers('monitor-admin'),
        ).get_json()
        self.assertNotIn('water_temp', {item['business_metric'] for item in latest['items']})

    def test_valid_observation_supersedes_suspect_candidate_in_same_slot(self):
        suspect = self._persist(
            bytes.fromhex('0311') + bcd_number(5.5, 3, 1), serial=40,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, suspect), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE monitoring_business_observations SET quality='suspect' "
                "WHERE observation_batch_id=(SELECT id FROM observation_batches WHERE raw_frame_id=?)",
                (suspect,),
            )
            connection.commit()
        valid = self._persist(
            bytes.fromhex('0311') + bcd_number(5.6, 3, 1), serial=41,
            sent_at=datetime(2020, 6, 12, 4, 1), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:01+00:00')
        self.assertEqual(normalize_raw_frame(self.database, valid), 'accepted')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp'
            '&start=2020-06-11T20:00:00%2B00:00&end=2020-06-11T20:10:00%2B00:00',
            headers=self._headers('monitor-admin'),
        ).get_json()
        self.assertEqual([(point['quality'], point['value']) for point in response['points']], [('valid', 5.6)])
        self.assertEqual(response['coverage']['valid_points'], 1)
        self.assertEqual(response['coverage']['coverage_rate'], 1)
        self.assertEqual(response['coverage']['conflict_slots'], 0)
        self.assertEqual(response['coverage']['suspect_points'], 1)

    def test_same_slot_same_value_is_duplicate_but_different_value_is_conflict(self):
        first = self._persist(
            bytes.fromhex('0311') + bcd_number(5.5, 3, 1), serial=52,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:00+00:00')
        second = self._persist(
            bytes.fromhex('0311') + bcd_number(5.5, 3, 1), serial=53,
            sent_at=datetime(2020, 6, 12, 4, 1), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:01+00:00',
        )
        self.assertEqual(normalize_raw_frame(self.database, first), 'accepted')
        self.assertEqual(normalize_raw_frame(self.database, second), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                [row[0] for row in connection.execute(
                    "SELECT slot_state FROM monitoring_business_observations WHERE is_current=1 ORDER BY id")],
                ['selected', 'duplicate'],
            )
        self.assertEqual(normalize_raw_frame(
            self.database, first, normalization_version='reproject-selected-v3'), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                [row[0] for row in connection.execute(
                    "SELECT slot_state FROM monitoring_business_observations WHERE is_current=1 ORDER BY id")],
                ['selected', 'duplicate'],
            )
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-11T20:00:00%2B00:00&end=2020-06-11T20:10:00%2B00:00',
            headers=self._headers('monitor-admin'),
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        coverage = response.get_json()['coverage']
        self.assertEqual(len(response.get_json()['points']), 1)
        self.assertEqual(coverage['valid_points'], 1)
        self.assertEqual(coverage['displayed_points'], 1)
        self.assertEqual(coverage['duplicate_records'], 1)
        self.assertEqual(coverage['conflict_slots'], 0)
        conflicting = self._persist(
            bytes.fromhex('0311') + bcd_number(5.6, 3, 1), serial=54,
            sent_at=datetime(2020, 6, 12, 4, 2), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:02+00:00')
        self.assertEqual(normalize_raw_frame(self.database, conflicting), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            states = [row[0] for row in connection.execute(
                'SELECT slot_state FROM monitoring_business_observations WHERE is_current=1 ORDER BY id')]
        self.assertEqual(states, ['conflict', 'conflict', 'conflict'])
        conflicted = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-11T20:00:00%2B00:00&end=2020-06-11T20:10:00%2B00:00',
            headers=self._headers('monitor-admin'),
        ).get_json()
        self.assertEqual(conflicted['points'], [])
        self.assertEqual(conflicted['coverage']['missing_points'], 1)
        self.assertEqual(conflicted['coverage']['conflict_slots'], 1)

    def test_exact_slot_nearby_minute_and_receipt_tolerance_remain_distinct(self):
        exact = self._persist(
            bytes.fromhex('0311') + bcd_number(5.0, 3, 1), serial=55,
            sent_at=datetime(2020, 6, 12, 8, 0), observed_at=datetime(2020, 6, 12, 8, 0),
            received_at='2020-06-12T00:10:00+00:00')
        nearby = self._persist(
            bytes.fromhex('0311') + bcd_number(5.1, 3, 1), serial=56,
            sent_at=datetime(2020, 6, 12, 8, 1), observed_at=datetime(2020, 6, 12, 8, 1),
            received_at='2020-06-12T00:01:00+00:00')
        late = self._persist(
            bytes.fromhex('0311') + bcd_number(5.2, 3, 1), serial=57,
            sent_at=datetime(2020, 6, 12, 12, 0), observed_at=datetime(2020, 6, 12, 12, 0),
            received_at='2020-06-12T04:10:01+00:00')
        for raw_id in (exact, nearby, late):
            self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute(
                "SELECT observed_at,timeliness,slot_state FROM monitoring_business_observations ORDER BY observed_at"
            ).fetchall()
        self.assertEqual(rows, [
            ('2020-06-12T00:00:00+00:00', 'on_time', 'selected'),
            ('2020-06-12T04:00:00+00:00', 'late', 'selected'),
        ])
        trend = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-12T00:00:00%2B00:00&end=2020-06-12T04:00:01%2B00:00',
            headers=self._headers('monitor-admin'),
        ).get_json()
        self.assertEqual(trend['coverage']['late_points'], 1)
        self.assertEqual(trend['coverage']['valid_points'], 2)
        self.assertEqual(trend['coverage']['coverage_rate'], 1)

    def test_factor_schedule_override_effective_period_changes_expected_slots(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO monitoring_business_schedules(
                       endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                       tolerance_seconds,effective_from,effective_to)
                   VALUES (?,'0311','Asia/Shanghai',3600,'00:00:00',600,
                           '2020-06-12T00:00:00+00:00','2020-06-12T04:00:00+00:00')""",
                (self.endpoint_id,),
            )
            connection.row_factory = sqlite3.Row
            configuration = next(item for item in web_app.monitoring_factor_configurations(
                connection, self.site_id, at_time='2020-06-12T01:00:00+00:00')
                if item['protocol_code'] == '0311')
            slots = expected_business_slots(
                connection, configuration, '2020-06-11T20:00:00+00:00', '2020-06-12T08:00:00+00:00')
        self.assertEqual([item['scheduled_at'] for item in slots], [
            '2020-06-11T20:00:00+00:00', '2020-06-12T00:00:00+00:00',
            '2020-06-12T01:00:00+00:00', '2020-06-12T02:00:00+00:00',
            '2020-06-12T03:00:00+00:00', '2020-06-12T04:00:00+00:00',
        ])

    def test_formal_projection_survives_new_database_connection(self):
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(6.2, 3, 1), serial=58,
            sent_at=datetime(2020, 6, 12, 8, 0), observed_at=datetime(2020, 6, 12, 8, 0),
            received_at='2020-06-12T00:05:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            items = web_app.monitoring_latest_values(connection, self.site_id)
        water = next(item for item in items if item['business_metric'] == 'water_temp')
        self.assertEqual(water['standard_value'], 6.2)
        self.assertEqual(water['scheduled_at'], '2020-06-12T00:00:00+00:00')

    def test_trend_window_and_quality_pagination_are_bounded(self):
        client = web_app.app.test_client()
        admin = self._headers('monitor-admin')
        default_window = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp', headers=admin
        )
        self.assertEqual(default_window.status_code, 200, default_window.get_json())
        self.assertEqual(default_window.get_json()['coverage']['point_limit'], 1000)
        long_window = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-01T00:00:00%2B00:00&end=2020-06-03T00:01:00%2B00:00',
            headers=admin,
        )
        self.assertEqual(long_window.status_code, 422)
        self.assertEqual(long_window.get_json()['code'], 'MONITORING_TREND_WINDOW_UNSUPPORTED')
        for serial in range(40, 43):
            raw_id = self._persist(bytes.fromhex('FEED'), serial=serial)
            self.assertEqual(normalize_raw_frame(self.database, raw_id), 'partial')
        page = client.get('/api/station-monitoring/quality-issues?page=1&page_size=2', headers=admin)
        self.assertEqual(page.status_code, 200, page.get_json())
        self.assertEqual(len(page.get_json()['items']), 2)
        self.assertGreaterEqual(page.get_json()['total'], 3)
        self.assertEqual(client.get('/api/station-monitoring/quality-issues?page_size=101', headers=admin).status_code, 400)

    def test_860_minute_facts_project_only_exact_four_hour_business_slots(self):
        start = datetime(2020, 6, 11, 16, tzinfo=timezone.utc)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            for index in range(860):
                observed_at = (start + timedelta(minutes=index)).replace(microsecond=0).isoformat()
                raw = connection.execute(
                    """INSERT INTO ingest_raw_frames(
                           endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                           crc_status,authentication_status,disposition,persistence_state)
                       VALUES (?,?,?,?,?,?,'valid','authenticated','accepted','persisted')""",
                    (self.endpoint_id, '0012345678', observed_at, f'trend-860-{index}', b'isolated', 8),
                )
                batch = connection.execute(
                    """INSERT INTO observation_batches(
                           raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                           reported_at,observed_at,received_at,granularity,aggregation_source,
                           idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'device_reported',?,?,'accepted','completed',?)""",
                    (raw.lastrowid, self.endpoint_id, self.site_id, 2011, index, observed_at,
                     observed_at, observed_at, 'realtime', f'trend-860-{index}',
                     'station-monitoring-normalizer-v2', observed_at),
                )
                value = connection.execute(
                    """INSERT INTO observation_values(
                           observation_batch_id,protocol_code,business_metric,raw_value,raw_unit,
                           standard_value,standard_unit,quality,instrument_asset_code,parser_version)
                       VALUES (?, '4612', 'ph', ?, 'pH', ?, 'pH', 'valid', 'INST-A', 'isolated')""",
                    (batch.lastrowid, 7 + index / 10000, 7 + index / 10000),
                )
                _project_business_observation(
                    connection, value_id=value.lastrowid, batch_id=batch.lastrowid,
                    endpoint_id=self.endpoint_id, site_id=self.site_id, protocol_code='4612',
                    business_metric='ph', instrument_asset_code='INST-A', observed_at=observed_at,
                    received_at=observed_at, standard_value=7 + index / 10000,
                    standard_unit='pH', quality='valid')
            connection.commit()
        end = start + timedelta(minutes=859)
        start_query = start.isoformat().replace('+', '%2B')
        end_query = end.isoformat().replace('+', '%2B')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=ph'
            f'&start={start_query}&end={end_query}',
            headers=self._headers('monitor-admin'),
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(len(body['points']), 4)
        self.assertEqual(body['standard_unit'], 'pH')
        self.assertEqual(body['coverage']['displayed_points'], 4)
        self.assertEqual(body['coverage']['expected_points'], 4)
        self.assertEqual(body['coverage']['coverage_rate'], 1)
        self.assertEqual(body['coverage']['gap_count'], 0)

    def test_exact_24_hour_window_is_half_open_with_six_expected_slots(self):
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp'
            '&start=2020-06-11T16:00:00%2B00:00&end=2020-06-12T16:00:00%2B00:00',
            headers=self._headers('monitor-admin'),
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['coverage']['expected_points'], 6)
        self.assertNotIn('2020-06-12T16:00:00+00:00', [
            point['scheduled_at'] for point in response.get_json()['points']])

    def test_instrument_route_exposes_factor_configuration_without_health_projection(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,instrument_asset_code,
                   expected_interval_seconds,effective_from,effective_to)
                   VALUES (?, '0311', 'water_temp', 'RETIRED-INST', 60, '2019-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')""",
                (self.endpoint_id,),
            )
            connection.commit()
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=50)
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/instruments', headers=self._headers('monitor-admin')
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        items = response.get_json()['items']
        water = next(item for item in items if item['business_metric'] == 'water_temp')
        self.assertEqual(water['factor_name_cn'], '水温')
        self.assertNotIn('instrument_asset_code', water)
        self.assertNotIn('last_valid', water)
        self.assertNotIn('status', water)

    def test_station_monitoring_overview_uses_independent_monitoring_fields(self):
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/overview', headers=self._headers('monitor-admin'))
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertIn('axes', body)
        self.assertIn('capabilities', body)
        self.assertIn('monitoring_status', body['site'])
        self.assertNotIn('status', body['site'])
        self.assertNotIn('status_label', body['site'])
        self.assertNotIn('reason', body['site'])
        self.assertTrue(body['site']['can_calibrate'])
        self.assertEqual(body['site']['type_cn'], '其他站点')
        self.assertEqual(set(body['axes']), {'communication', 'data'})
        self.assertNotIn('instruments', body)
        self.assertNotIn('recent_items', body)
        for axis in ('communication', 'data'):
            self.assertIn('name', body['axes'][axis])
            self.assertIn('status', body['axes'][axis])
            self.assertIn('status_label', body['axes'][axis])

    def test_station_monitoring_list_uses_explicit_server_scopes_and_responsibility(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('INSERT INTO user_sites(user_id,site_id) VALUES (?,?)', (self.admin_id, self.site_id))
            connection.execute("UPDATE sites SET name='本人水站', code='MINE-001' WHERE id=?", (self.site_id,))
            connection.execute("UPDATE sites SET name='其他水站', code='OTHER-002' WHERE id=?", (self.other_site_id,))
            connection.commit()
        client = web_app.app.test_client()
        admin = self._headers('monitor-admin')
        default_response = client.get('/api/station-monitoring/sites', headers=admin)
        self.assertEqual(default_response.status_code, 200, default_response.get_json())
        default_body = default_response.get_json()
        self.assertEqual(default_body['scope'], 'mine')
        self.assertEqual(default_body['available_scopes'], ['mine', 'all'])
        self.assertEqual(default_body['scope_counts'], {'mine': 1, 'all': 2})
        self.assertEqual(default_body['summary']['total'], 1)
        self.assertEqual([item['site_id'] for item in default_body['items']], [self.site_id])
        self.assertTrue(default_body['items'][0]['is_responsible'])
        all_body = client.get('/api/station-monitoring/sites?scope=all', headers=admin).get_json()
        self.assertEqual(all_body['summary']['total'], 2)
        self.assertEqual(all_body['items'][0]['site_id'], self.site_id)
        self.assertEqual([item['is_responsible'] for item in all_body['items']], [True, False])
        by_name = client.get('/api/station-monitoring/sites?scope=all&keyword=%E5%85%B6%E4%BB%96', headers=admin).get_json()
        self.assertEqual([item['site_id'] for item in by_name['items']], [self.other_site_id])
        self.assertEqual(by_name['summary']['total'], 1)
        self.assertEqual(by_name['scope_counts'], {'mine': 1, 'all': 2})
        by_code = client.get('/api/station-monitoring/sites?scope=all&keyword=MINE-001', headers=admin).get_json()
        self.assertEqual([item['site_id'] for item in by_code['items']], [self.site_id])
        cleared = client.get('/api/station-monitoring/sites?scope=all&keyword=%20%20', headers=admin).get_json()
        self.assertEqual(cleared['summary']['total'], 2)

    def test_station_monitoring_scope_enforces_operator_and_zero_assignment_boundaries(self):
        client = web_app.app.test_client()
        operator = self._headers('monitor-operator')
        mine = client.get('/api/station-monitoring/sites?scope=mine', headers=operator)
        self.assertEqual(mine.status_code, 200, mine.get_json())
        self.assertEqual(mine.get_json()['available_scopes'], ['mine'])
        self.assertEqual(mine.get_json()['scope_counts'], {'mine': 1, 'all': None})
        self.assertEqual([item['site_id'] for item in mine.get_json()['items']], [self.site_id])
        self.assertEqual(client.get('/api/station-monitoring/sites?scope=all', headers=operator).status_code, 403)
        self.assertEqual(client.get('/api/station-monitoring/sites?scope=team', headers=operator).status_code, 400)
        admin = self._headers('monitor-admin')
        zero_mine = client.get('/api/station-monitoring/sites?scope=mine', headers=admin)
        self.assertEqual(zero_mine.status_code, 200, zero_mine.get_json())
        self.assertEqual(zero_mine.get_json()['items'], [])
        self.assertEqual(zero_mine.get_json()['scope_counts'], {'mine': 0, 'all': 2})
        all_response = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
        self.assertEqual(all_response.status_code, 200, all_response.get_json())
        self.assertEqual(all_response.get_json()['summary']['total'], 2)

    def test_station_monitoring_keyword_is_literal_and_stays_inside_mine_scope(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE sites SET name='百分号%站', code='LITERAL_01' WHERE id=?", (self.site_id,))
            connection.execute("UPDATE sites SET name='普通站', code='OTHER-02' WHERE id=?", (self.other_site_id,))
            connection.commit()
        client = web_app.app.test_client()
        operator = self._headers('monitor-operator')
        percent = client.get('/api/station-monitoring/sites?scope=mine&keyword=%25', headers=operator)
        self.assertEqual([item['site_id'] for item in percent.get_json()['items']], [self.site_id])
        underscore = client.get('/api/station-monitoring/sites?scope=mine&keyword=_', headers=operator)
        self.assertEqual([item['site_id'] for item in underscore.get_json()['items']], [self.site_id])
        outside = client.get('/api/station-monitoring/sites?scope=mine&keyword=OTHER', headers=operator)
        self.assertEqual(outside.get_json()['items'], [])
        self.assertEqual(outside.get_json()['scope_counts']['mine'], 1)

    def test_station_monitoring_overview_projects_chinese_factor_name(self):
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=62,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0))
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/overview', headers=self._headers('monitor-admin'))
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        latest = body['monitoring']['latest_values']
        self.assertEqual(latest[0]['factor_name_cn'], '水温')
        self.assertEqual(body['axes']['communication']['state'], 'stale')
        self.assertEqual(body['axes']['communication']['status_label'], '最近未收到报文')
        self.assertEqual(body['axes']['communication']['reason'], '最后收到报文时间已超出配置周期')
        self.assertEqual(body['axes']['data']['state'], 'no_valid_observation')
        self.assertEqual(body['axes']['data']['reason'], '当前配置因子中仍有因子尚未形成正式业务观测')
        self.assertNotEqual(body['axes']['data']['reason'], body['axes']['communication']['reason'])
        self.assertTrue(all(item['standard_unit'] for item in body['monitoring']['factors']))

    def test_station_monitoring_normal_requires_every_configured_factor_fresh(self):
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        profile = {'endpoint_id': self.endpoint_id, 'expected_interval_seconds': 60}
        configs = [
            {'endpoint_id': self.endpoint_id, 'protocol_code': code, 'business_metric': metric,
             'instrument_asset_code': 'INST-A', 'expected_interval_seconds': 60, 'tolerance_seconds': 15}
            for code, metric in (('0311', 'water_temp'), ('4612', 'ph'), ('4A11', 'codmn'), ('4C1A', 'ammonia'))
        ]
        result = web_app._station_monitoring_summary_projection(
            None, self.site_id, profile, configs, [dict(configs[0], observed_at=now)], now)
        self.assertEqual(result['status'], 'attention')
        self.assertEqual(result['reason_code'], 'missing_observation')

    def test_station_monitoring_marks_stale_endpoint_communication(self):
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        profile = {'endpoint_id': self.endpoint_id, 'expected_interval_seconds': 60}
        config = {'endpoint_id': self.endpoint_id, 'protocol_code': '0311', 'business_metric': 'water_temp',
                  'instrument_asset_code': 'INST-A', 'expected_interval_seconds': 60, 'tolerance_seconds': 15}
        result = web_app._station_monitoring_summary_projection(
            None, self.site_id, profile, [config], [dict(config, observed_at=now)], '2020-01-01T00:00:00+00:00')
        self.assertEqual(result['status'], 'attention')
        self.assertEqual(result['reason_code'], 'stale_communication')
        data_axis = web_app._station_monitoring_data_axis([config], [dict(config, observed_at=now)])
        self.assertEqual(data_axis['state'], 'fresh')
        self.assertEqual(data_axis['reason'], '最近正式业务观测仍在应到周期内')

    def test_all_published_monitoring_factors_have_distinct_chinese_names(self):
        with closing(sqlite3.connect(self.database)) as connection:
            metrics = [row[0] for row in connection.execute(
                'SELECT DISTINCT business_metric FROM monitoring_factor_definitions WHERE is_published=1 AND business_metric IS NOT NULL')]
        labels = [web_app._STATION_MONITORING_FACTOR_LABELS.get(metric) for metric in metrics]
        self.assertNotIn(None, labels)
        self.assertEqual(len(labels), len(set(labels)))

    def test_station_monitoring_overview_permissions_and_missing_site(self):
        client = web_app.app.test_client()
        operator = self._headers('monitor-operator')
        self.assertTrue(client.get(f'/api/station-monitoring/sites/{self.site_id}/overview', headers=operator).get_json()['site']['can_calibrate'])
        self.assertEqual(client.get(f'/api/station-monitoring/sites/{self.other_site_id}/overview', headers=operator).status_code, 403)
        self.assertEqual(client.get('/api/station-monitoring/sites/99999/overview', headers=self._headers('monitor-admin')).status_code, 404)

    def test_access_summary_counts_independent_identity_axes(self):
        client = web_app.app.test_client()
        admin = self._headers('monitor-admin')
        response = client.get('/api/station-monitoring/access-summary', headers=admin)
        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertEqual(body['runtime']['enabled_endpoints'], 1)
        self.assertEqual(body['identity']['bound_sites'], 1)
        self.assertEqual(body['identity']['received_raw_sites'], 0)
        self.assertEqual(client.get('/api/station-monitoring/access-summary', headers=self._headers('monitor-operator')).status_code, 403)

    def test_access_summary_counts_only_selected_valid_business_sites(self):
        suspect = self._persist(
            bytes.fromhex('0311') + bcd_number(5.5, 3, 1), serial=65,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, suspect), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE monitoring_business_observations SET quality='suspect' "
                "WHERE observation_batch_id=(SELECT id FROM observation_batches WHERE raw_frame_id=?)",
                (suspect,),
            )
            connection.commit()
        client = web_app.app.test_client()
        headers = self._headers('monitor-admin')
        suspect_only = client.get('/api/station-monitoring/access-summary', headers=headers).get_json()
        self.assertEqual(suspect_only['observation']['valid_sites'], 0)

        late_valid = self._persist(
            bytes.fromhex('0311') + bcd_number(5.6, 3, 1), serial=66,
            sent_at=datetime(2020, 6, 12, 4, 1), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:10:01+00:00')
        self.assertEqual(normalize_raw_frame(self.database, late_valid), 'accepted')
        valid = client.get('/api/station-monitoring/access-summary', headers=headers).get_json()
        self.assertEqual(valid['observation']['valid_sites'], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            selected = connection.execute(
                """SELECT quality,timeliness FROM monitoring_business_observations
                   WHERE business_site_id=? AND slot_state='selected' AND is_current=1""",
                (self.site_id,),
            ).fetchall()
        self.assertIn(('valid', 'late'), selected)

    def test_bound_identity_without_profile_is_not_reported_as_not_connected(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('UPDATE monitoring_endpoint_profiles SET enabled=0 WHERE endpoint_id=?', (self.endpoint_id,))
            connection.commit()
        response = web_app.app.test_client().get('/api/station-monitoring/sites?scope=all', headers=self._headers('monitor-admin'))
        self.assertEqual(response.status_code, 200, response.get_json())
        item = next(item for item in response.get_json()['items'] if item['site_id'] == self.site_id)
        self.assertEqual(item['monitoring_status'], 'awaiting_first_frame')

    def test_list_and_detail_share_bound_identity_and_exclude_expired_profile(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE monitoring_endpoint_profiles SET effective_to='2020-01-01T00:00:00+00:00'")
            connection.commit()
        headers = self._headers('monitor-admin')
        listed = web_app.app.test_client().get('/api/station-monitoring/sites?scope=all', headers=headers)
        detail = web_app.app.test_client().get(f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers)
        self.assertEqual(listed.status_code, 200, listed.get_json())
        self.assertEqual(detail.status_code, 200, detail.get_json())
        item = next(item for item in listed.get_json()['items'] if item['site_id'] == self.site_id)
        self.assertEqual(item['monitoring_status'], 'awaiting_first_frame')
        self.assertEqual(detail.get_json()['site']['monitoring_status'], item['monitoring_status'])

    def test_list_and_detail_share_formal_observation_projection(self):
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=63,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        headers = self._headers('monitor-admin')
        listed = web_app.app.test_client().get(
            '/api/station-monitoring/sites?scope=all', headers=headers).get_json()
        detail = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers).get_json()
        item = next(row for row in listed['items'] if row['site_id'] == self.site_id)
        self.assertEqual(item['last_valid_observation_at'], '2020-06-11T20:00:00+00:00')
        self.assertEqual(item['last_valid_observation_at'], detail['site']['last_valid_observation_at'])
        self.assertEqual(item['monitoring_status'], detail['site']['monitoring_status'])
        self.assertEqual(item['reason_code'], detail['site']['reason_code'])

    def test_unknown_schedule_and_minute_value_stay_unconfigured_in_list_and_detail(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('DELETE FROM monitoring_business_schedules')
            connection.commit()
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=64)
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        headers = self._headers('monitor-admin')
        listed = web_app.app.test_client().get(
            '/api/station-monitoring/sites?scope=all', headers=headers).get_json()
        detail = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers).get_json()
        item = next(row for row in listed['items'] if row['site_id'] == self.site_id)
        self.assertEqual(item['monitoring_status'], 'interval_unconfigured')
        self.assertEqual(detail['site']['monitoring_status'], 'interval_unconfigured')
        self.assertIsNone(item['last_valid_observation_at'])
        self.assertEqual(detail['monitoring']['latest_values'], [])
        trend = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp'
            '&start=2020-06-11T16:00:00%2B00:00&end=2020-06-12T16:00:00%2B00:00',
            headers=headers,
        ).get_json()
        self.assertEqual(trend['points'], [])
        self.assertEqual(trend['coverage']['state'], 'unconfigured')
        self.assertEqual(trend['coverage']['expected_points'], 0)

    def test_rebound_endpoint_does_not_leak_identity_or_configuration_to_old_site(self):
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=61,
            sent_at=datetime(2020, 6, 12, 4, 0), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        before = web_app.app.test_client().get('/api/station-monitoring/access-summary', headers=self._headers('monitor-admin')).get_json()
        self.assertEqual(before['observation']['valid_sites'], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('UPDATE trusted_endpoints SET business_site_id=? WHERE id=?', (self.other_site_id, self.endpoint_id))
            connection.commit()
        headers = self._headers('monitor-admin')
        listed = web_app.app.test_client().get('/api/station-monitoring/sites?scope=all', headers=headers).get_json()
        item = next(item for item in listed['items'] if item['site_id'] == self.site_id)
        detail = web_app.app.test_client().get(f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers).get_json()
        self.assertEqual(item['monitoring_status'], 'not_connected')
        self.assertEqual(detail['site']['monitoring_status'], 'not_connected')
        self.assertEqual(item['published_factor_count'], 0)
        summary = web_app.app.test_client().get('/api/station-monitoring/access-summary', headers=headers).get_json()
        self.assertEqual(summary['configuration']['configured_sites'], 0)
        self.assertEqual(summary['observation']['valid_sites'], 0)

    def test_retry_exhaustion_is_bounded_and_stable(self):
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=51)
        for _ in range(3):
            self.storage.record_normalization_retry(raw_id, 'OperationalError')
        with closing(sqlite3.connect(self.database)) as connection:
            retry = connection.execute(
                'SELECT attempt_count,state FROM monitoring_normalization_retries WHERE raw_frame_id=?', (raw_id,)
            ).fetchone()
            raw_state = connection.execute('SELECT persistence_state FROM ingest_raw_frames WHERE id=?', (raw_id,)).fetchone()[0]
            issue = connection.execute(
                "SELECT occurrence_count FROM monitoring_quality_issues WHERE raw_frame_id=? AND issue_type='normalization_retry_exhausted'",
                (raw_id,),
            ).fetchone()[0]
        self.assertEqual(tuple(retry), (3, 'exhausted'))
        self.assertEqual(raw_state, 'persisted')
        self.assertEqual(issue, 1)


if __name__ == '__main__':
    unittest.main()
