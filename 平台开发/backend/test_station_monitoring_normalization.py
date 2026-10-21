import sqlite3
import json
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app
import station_monitoring_api as monitoring_api
from migrate_station_ingestion import apply_migration, verify_station_monitoring_schema
from sl651_parser import (FrameError, UP_FLOW_CONTROL, crc16_modbus, encode_bcd_observation_time,
                          encode_bcd_time, encode_station_code, parse_frame, parse_water_quality_payload,
                          parse_water_quality_report)
from sl651_server import IngestionStorage, credential_hmac
from station_ingest_retention import archive_raw_day, bootstrap_retention_state, storage_policy_for_mode
from station_monitoring import (_project_business_observation, _scheduled_at,
                                _current_collection_values_from_rows,
                                business_slot_deadline, expected_business_slots,
                                current_factor_configurations, format_business_value,
                                latest_cycle, latest_values, normalize_raw_frame,
                                result_trend_page, trend_slots)


def bcd_number(value, digits, precision=0):
    encoded = f"{int(round(value * (10 ** precision))):0{digits}d}"
    if len(encoded) % 2:
        encoded = "0" + encoded
    return bytes((int(encoded[index]) << 4) | int(encoded[index + 1]) for index in range(0, len(encoded), 2))


def make_32h(payload, serial=7, sent_at=None, observed_at=None,
             payload_station='0012345678', credential=b'\x12\x34'):
    sent_at = sent_at or datetime(2020, 6, 12, 2, 0, 0)
    observed_at = observed_at or sent_at.replace(second=0)
    body = b'\xf1\xf1' + encode_station_code(payload_station) + b'\x51\xf0\xf0' + encode_bcd_observation_time(observed_at) + payload
    content = serial.to_bytes(2, "big") + encode_bcd_time(sent_at) + body
    prefix = b"\x7e\x7e\x10" + encode_station_code("0012345678") + credential + b"\x32" + len(content).to_bytes(2, "big") + b"\x02" + content + bytes((UP_FLOW_CONTROL,))
    return prefix + crc16_modbus(prefix).to_bytes(2, "big")


class StationMonitoringNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "isolated-monitoring.db"
        self.previous_database = web_app.DB_PATH
        self.previous_monitoring_access_mode = web_app.STATION_MONITORING_ACCESS_MODE
        web_app.STATION_MONITORING_ACCESS_MODE = 'public'
        web_app.DB_PATH = str(self.database)
        with monitoring_api._station_monitoring_batch_cache_lock:
            monitoring_api._station_monitoring_batch_cache.clear()
            monitoring_api._station_monitoring_batch_inflight.clear()
        with monitoring_api._station_monitoring_list_cache_lock:
            monitoring_api._station_monitoring_list_cache.clear()
            monitoring_api._station_monitoring_list_inflight.clear()
        with monitoring_api._station_monitoring_overview_cache_lock:
            monitoring_api._station_monitoring_overview_cache.clear()
            monitoring_api._station_monitoring_overview_inflight.clear()
        with monitoring_api._station_monitoring_overview_projection_cache_lock:
            monitoring_api._station_monitoring_overview_projection_cache.clear()
            monitoring_api._station_monitoring_overview_projection_inflight.clear()
        web_app.init_db()
        apply_migration(self.database, Path(self.temp_dir.name) / "backups")
        with closing(sqlite3.connect(self.database)) as connection:
            inspection_columns = {
                row[1] for row in connection.execute('PRAGMA table_info(inspection_tasks)')
            }
            if 'created_at' not in inspection_columns:
                connection.execute('ALTER TABLE inspection_tasks ADD COLUMN created_at TEXT')
            connection.execute('''CREATE TABLE IF NOT EXISTS data_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER NOT NULL,
                metric TEXT NOT NULL, value REAL NOT NULL, recorded_at TEXT NOT NULL,
                auto_result TEXT, auto_reason TEXT, smart_result TEXT, smart_score REAL,
                manual_result TEXT, manual_reason TEXT, reviewer_id INTEGER, reviewed_at TEXT,
                status TEXT DEFAULT 'pending', created_at TEXT DEFAULT (datetime('now','localtime'))
            )''')
            connection.commit()
        web_app.migrate_formal_observation_consumers()
        web_app.migrate_alerts_messages()
        web_app.migrate_alert_flow()
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
            config_id = connection.execute(
                """INSERT INTO station_expected_factor_configs(
                       site_id,version,effective_from,previous_metrics_json,
                       selected_metrics_json,changed_by_user_id,idempotency_key,created_at)
                   VALUES (?,1,?,'[]',?,?,?,?)""",
                (self.site_id, '2020-01-01T00:00:00+00:00',
                 '["ph","water_temp","codmn","ammonia"]', self.admin_id,
                 'normalization-fixture-v1', '2020-01-01T00:00:00+00:00'),
            ).lastrowid
            connection.executemany(
                """INSERT INTO station_expected_factors(
                       config_id,site_id,business_metric,display_order,effective_from)
                   VALUES (?,?,?,?,?)""",
                ((config_id, self.site_id, metric, index, '2020-01-01T00:00:00+00:00')
                 for index, metric in enumerate(('ph', 'water_temp', 'codmn', 'ammonia'))),
            )
            connection.commit()
        self.storage = IngestionStorage(self.database, pepper)

    def tearDown(self):
        with monitoring_api._station_monitoring_batch_cache_lock:
            monitoring_api._station_monitoring_batch_cache.clear()
            monitoring_api._station_monitoring_batch_inflight.clear()
        with monitoring_api._station_monitoring_list_cache_lock:
            monitoring_api._station_monitoring_list_cache.clear()
            monitoring_api._station_monitoring_list_inflight.clear()
        with monitoring_api._station_monitoring_overview_cache_lock:
            monitoring_api._station_monitoring_overview_cache.clear()
            monitoring_api._station_monitoring_overview_inflight.clear()
        with monitoring_api._station_monitoring_overview_projection_cache_lock:
            monitoring_api._station_monitoring_overview_projection_cache.clear()
            monitoring_api._station_monitoring_overview_projection_inflight.clear()
        web_app.STATION_MONITORING_ACCESS_MODE = self.previous_monitoring_access_mode
        web_app.DB_PATH = self.previous_database
        self.temp_dir.cleanup()

    def _headers(self, username):
        client = web_app.app.test_client()
        response = client.post('/api/auth/login', json={'username': username, 'password': 'pw'})
        self.assertEqual(response.status_code, 200, response.get_json())
        return {'Authorization': 'Bearer ' + response.get_json()['token']}

    def _set_expected_factors(self, connection, metrics):
        metrics = list(metrics)
        config = connection.execute(
            'SELECT id FROM station_expected_factor_configs '
            'WHERE site_id=? AND effective_to IS NULL',
            (self.site_id,),
        ).fetchone()
        self.assertIsNotNone(config)
        config_id = config[0]
        connection.execute(
            'UPDATE station_expected_factor_configs SET selected_metrics_json=? WHERE id=?',
            (json.dumps(metrics, separators=(',', ':')), config_id),
        )
        connection.execute(
            'DELETE FROM station_expected_factors WHERE config_id=?', (config_id,))
        connection.executemany(
            """INSERT INTO station_expected_factors(
                   config_id,site_id,business_metric,display_order,effective_from)
               VALUES (?,?,?,?,?)""",
            ((config_id, self.site_id, metric, index, '2020-01-01T00:00:00+00:00')
             for index, metric in enumerate(metrics)),
        )

    def _persist(self, payload, serial=7, *, sent_at=None, observed_at=None, payload_station='0012345678', received_at='2026-09-09T00:00:00+00:00'):
        frame = parse_frame(make_32h(payload, serial=serial, sent_at=sent_at, observed_at=observed_at, payload_station=payload_station))
        raw_id, duplicate = self.storage.persist_parsed(frame, self.storage.authenticate(frame), received_at)
        self.assertFalse(duplicate)
        return raw_id

    def _insert_formal_observation(self, metric, value, scheduled_at, *, protocol_code='4612',
                                   quality='valid'):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            schedule_id = connection.execute(
                'SELECT id FROM monitoring_business_schedules WHERE endpoint_id=? ORDER BY id LIMIT 1',
                (self.endpoint_id,),
            ).fetchone()[0]
            suffix = f'{metric}-{scheduled_at}-{value}-{quality}'
            raw = connection.execute(
                """INSERT INTO ingest_raw_frames(
                       endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                       crc_status,authentication_status,disposition,persistence_state)
                   VALUES (?,?,?,?,?,8,'valid','authenticated','accepted','persisted')""",
                (self.endpoint_id, '0012345678', scheduled_at, 'formal-' + suffix, b'formal'),
            )
            batch = connection.execute(
                """INSERT INTO observation_batches(
                       raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                       reported_at,observed_at,received_at,granularity,aggregation_source,
                       idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                   VALUES (?,?,?,?,1,?,?,?,'periodic','device_reported',?,?,'accepted','completed',?)""",
                (raw.lastrowid, self.endpoint_id, self.site_id, 2061, scheduled_at,
                 scheduled_at, scheduled_at, 'formal-' + suffix,
                 web_app.MONITORING_BUSINESS_PROJECTION_VERSION, scheduled_at),
            )
            definition = connection.execute(
                'SELECT standard_unit FROM monitoring_factor_definitions WHERE protocol_code=?',
                (protocol_code,),
            ).fetchone()
            unit = definition['standard_unit'] or ''
            source = connection.execute(
                """INSERT INTO observation_values(
                       observation_batch_id,protocol_code,business_metric,raw_value,raw_unit,
                       standard_value,standard_unit,quality,instrument_asset_code,parser_version)
                   VALUES (?,?,?,?,?,?,?,?,?,'formal-test')""",
                (batch.lastrowid, protocol_code, metric, value, unit, value, unit, quality, 'INST-A'),
            )
            business = connection.execute(
                """INSERT INTO monitoring_business_observations(
                       source_observation_value_id,observation_batch_id,schedule_id,endpoint_id,
                       business_site_id,protocol_code,business_metric,instrument_asset_code,
                       scheduled_at,observed_at,received_at,standard_value,standard_unit,quality,
                       timeliness,slot_state,projection_version,is_current)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'on_time','selected',?,1)""",
                (source.lastrowid, batch.lastrowid, schedule_id, self.endpoint_id, self.site_id,
                 protocol_code, metric, 'INST-A', scheduled_at, scheduled_at, scheduled_at,
                  value, unit, quality, web_app.MONITORING_BUSINESS_PROJECTION_VERSION),
            )
            connection.commit()
            return business.lastrowid

    def test_delayed_result_window_is_half_open_and_crosses_midnight(self):
        schedule = {
            'timezone': 'Asia/Shanghai', 'anchor_local_time': '00:00:00',
            'interval_seconds': 14400, 'result_delay_seconds': 7200,
        }
        self.assertEqual(
            _scheduled_at(schedule, '2026-09-11T17:59:59+00:00', 2011),
            '2026-09-11T12:00:00+00:00',
        )

        self.assertEqual(
            _scheduled_at(schedule, '2026-09-11T18:00:00+00:00', 2011),
            '2026-09-11T16:00:00+00:00',
        )
        self.assertEqual(
            _scheduled_at(schedule, '2026-09-11T04:00:00+00:00', 2061),
            '2026-09-11T04:00:00+00:00',
        )
        self.assertIsNone(_scheduled_at(schedule, '2026-09-11T05:00:00+00:00', 2061))
        self.assertEqual(business_slot_deadline({
            'result_delay_seconds': 7200, 'tolerance_seconds': 900,
            'expected_interval_seconds': 14400,
        }, '2026-09-11T04:00:00+00:00'), '2026-09-11T06:15:00+00:00')

    def test_business_value_display_uses_each_approved_factor_precision(self):
        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute("""SELECT protocol_code,business_metric,decimal_precision
                FROM monitoring_factor_definitions
                WHERE protocol_code IN (
                    '4612','4818','4910','4D1B','HJ212:w01003','HJ212:w01014',
                    'HJ212:006','HJ212:153')""").fetchall()
        precision = {row[1]: row[2] for row in rows}
        by_protocol = {row[0]: row[2] for row in rows}
        self.assertEqual(
            {code: by_protocol[code] for code in (
                '4818', '4910', 'HJ212:w01003', 'HJ212:w01014',
                'HJ212:006', 'HJ212:153')},
            {code: 2 for code in (
                '4818', '4910', 'HJ212:w01003', 'HJ212:w01014',
                'HJ212:006', 'HJ212:153')},
        )
        self.assertEqual(precision, {
            'ph': 2, 'conductivity': 2, 'turbidity': 2,
            'total_phosphorus': 3,
        })
        self.assertEqual(format_business_value(7.34, precision['ph']), '7.34')
        self.assertEqual(format_business_value(7.00, precision['ph']), '7')
        self.assertEqual(format_business_value(7.34, precision['turbidity']), '7.34')
        self.assertEqual(format_business_value(7.00, precision['turbidity']), '7')
        self.assertEqual(format_business_value(74.58, precision['conductivity']), '74.58')
        self.assertEqual(format_business_value(74.00, precision['conductivity']), '74')
        self.assertEqual(format_business_value(7.00, precision['total_phosphorus']), '7')
        self.assertEqual(format_business_value(0.1 + 0.2, 2), '0.3')
        self.assertEqual(format_business_value(7.123456789, 2), '7.123456789')
        self.assertEqual(format_business_value('7.2300', 2), '7.2300')
        self.assertEqual(format_business_value(
            7.23, 2, source_value_text='7.2300', raw_unit='pH', standard_unit='pH'), '7.2300')
        self.assertEqual(format_business_value(
            7.23, 2, source_value_text='7.2300', raw_unit='mV', standard_unit='pH'), '7.23')
        with closing(sqlite3.connect(self.database)) as connection:
            published = connection.execute("""SELECT protocol_code,decimal_precision
                FROM monitoring_factor_definitions WHERE is_published=1 ORDER BY protocol_code""").fetchall()
        self.assertGreater(len(published), 3)
        for protocol_code, configured_precision in published:
            display = format_business_value(7.34, configured_precision)
            self.assertEqual(float(display), 7.34, protocol_code)

    def test_current_collection_does_not_mix_older_factor_values(self):
        configurations = [
            {
                'endpoint_id': 1, 'business_metric': 'ph',
                'instrument_asset_code': None, 'protocol_codes': ['HJ212:w01001'],
                'mapping_effective_from': '2020-01-01T00:00:00+00:00',
                'mapping_effective_to': None, 'decimal_precision': 2,
            },
            {
                'endpoint_id': 1, 'business_metric': 'turbidity',
                'instrument_asset_code': None, 'protocol_codes': ['HJ212:w01003'],
                'mapping_effective_from': '2020-01-01T00:00:00+00:00',
                'mapping_effective_to': None, 'decimal_precision': 2,
            },
            {
                'endpoint_id': 1, 'business_metric': 'conductivity',
                'instrument_asset_code': None, 'protocol_codes': ['HJ212:w01014'],
                'mapping_effective_from': '2020-01-01T00:00:00+00:00',
                'mapping_effective_to': None, 'decimal_precision': 2,
            },
        ]
        batches = [
            {
                'id': 3, 'endpoint_id': 1, 'observed_at': '2026-10-20T08:00:00+00:00',
                'received_at': '2026-10-20T08:00:05+00:00',
                'normalized_at': '2026-10-20T08:00:06+00:00', 'function_code': 2011,
            },
            {
                'id': 2, 'endpoint_id': 1, 'observed_at': '2026-10-20T04:00:00+00:00',
                'received_at': '2026-10-20T04:00:05+00:00',
                'normalized_at': '2026-10-20T04:00:06+00:00', 'function_code': 2011,
            },
        ]
        values = [
            {
                'id': 31, 'observation_batch_id': 3, 'endpoint_id': 1,
                'observed_at': '2026-10-20T08:00:00+00:00',
                'protocol_code': 'HJ212:w01001', 'business_metric': 'ph',
                'standard_value': 7.1, 'standard_unit': 'pH', 'quality': 'valid',
                'instrument_asset_code': None, 'parser_version': 'hj212-text-v3:rtd',
            },
            {
                'id': 32, 'observation_batch_id': 3, 'endpoint_id': 1,
                'observed_at': '2026-10-20T08:00:00+00:00',
                'protocol_code': 'HJ212:w01014', 'business_metric': 'conductivity',
                'standard_value': 74.58, 'standard_unit': 'uS/cm', 'quality': 'valid',
                'instrument_asset_code': None, 'parser_version': 'hj212-text-v3:rtd',
            },
            {
                'id': 21, 'observation_batch_id': 2, 'endpoint_id': 1,
                'observed_at': '2026-10-20T04:00:00+00:00',
                'protocol_code': 'HJ212:w01003', 'business_metric': 'turbidity',
                'standard_value': 3.2, 'standard_unit': 'NTU', 'quality': 'valid',
                'instrument_asset_code': None, 'parser_version': 'hj212-text-v3:rtd',
            },
        ]

        current = _current_collection_values_from_rows(
            configurations, batches, values)

        self.assertEqual(
            {item['business_metric'] for item in current}, {'ph', 'conductivity'})
        self.assertEqual(
            {item['collection_group_at'] for item in current},
            {'2026-10-20T08:00:00+00:00'})

    def test_current_collection_uses_trend_source_priority_and_preserves_conflicts(self):
        configurations = [
            {
                'endpoint_id': 1, 'business_metric': 'ph',
                'instrument_asset_code': None, 'protocol_codes': ['HJ212:w01001'],
                'mapping_effective_from': '2020-01-01T00:00:00+00:00',
                'mapping_effective_to': None, 'decimal_precision': 2,
            },
            {
                'endpoint_id': 1, 'business_metric': 'turbidity',
                'instrument_asset_code': None, 'protocol_codes': ['HJ212:w01003'],
                'mapping_effective_from': '2020-01-01T00:00:00+00:00',
                'mapping_effective_to': None, 'decimal_precision': 2,
            },
        ]

        def batch(batch_id, observed_at, function_code):
            return {
                'id': batch_id, 'endpoint_id': 1, 'observed_at': observed_at,
                'received_at': observed_at, 'normalized_at': observed_at,
                'function_code': function_code,
            }

        def value(value_id, batch_id, observed_at, metric, standard_value, kind):
            protocol_code = 'HJ212:w01001' if metric == 'ph' else 'HJ212:w01003'
            unit = 'pH' if metric == 'ph' else 'NTU'
            return {
                'id': value_id, 'observation_batch_id': batch_id, 'endpoint_id': 1,
                'observed_at': observed_at, 'protocol_code': protocol_code,
                'business_metric': metric, 'standard_value': standard_value,
                'standard_unit': unit, 'quality': 'valid',
                'instrument_asset_code': None, 'parser_version': f'hj212-text-v3:{kind}',
            }

        same_at = '2026-10-20T08:00:00+00:00'
        batches = [batch(3, same_at, 2011), batch(2, same_at, 2061)]
        values = [
            value(31, 3, same_at, 'ph', 7.1, 'rtd'),
            value(32, 3, same_at, 'turbidity', 3.2, 'rtd'),
            value(21, 2, same_at, 'ph', 7.3, 'avg'),
        ]
        current = _current_collection_values_from_rows(configurations, batches, values)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]['business_metric'], 'ph')
        self.assertEqual(current[0]['standard_value'], 7.3)
        self.assertEqual(current[0]['function_code'], 2061)
        self.assertEqual(current[0]['selection_reason'], 'preferred_2061_same_collection')
        self.assertEqual(current[0]['source_candidate_count'], 2)

        newer_at = '2026-10-20T08:01:00+00:00'
        newer = _current_collection_values_from_rows(
            configurations, [batch(4, newer_at, 2011), *batches],
            [value(41, 4, newer_at, 'ph', 7.5, 'rtd'), *values])
        self.assertEqual(len(newer), 1)
        self.assertEqual(newer[0]['standard_value'], 7.5)
        self.assertEqual(newer[0]['function_code'], 2011)
        self.assertEqual(newer[0]['observed_at'], newer_at)

        conflict_at = '2026-10-20T12:00:00+00:00'
        conflicted = _current_collection_values_from_rows(
            configurations,
            [batch(6, conflict_at, 2061), batch(5, conflict_at, 2061),
             batch(4, conflict_at, 2011)],
            [value(61, 6, conflict_at, 'ph', 7.6, 'avg'),
             value(51, 5, conflict_at, 'ph', 7.4, 'avg'),
             value(41, 4, conflict_at, 'ph', 7.2, 'rtd')])
        self.assertEqual(len(conflicted), 1)
        self.assertEqual(conflicted[0]['status'], 'review_required')
        self.assertIsNone(conflicted[0]['standard_value'])
        self.assertIsNone(conflicted[0]['source_value_text'])
        self.assertEqual(conflicted[0]['selection_reason'], 'valid_value_conflict')
        self.assertEqual(conflicted[0]['candidate_count'], 2)
        self.assertEqual(conflicted[0]['source_candidate_count'], 3)
        states = monitoring_api._station_monitoring_factor_states(
            None, None, configurations, conflicted, faults={})
        self.assertEqual(states[0]['state'], 'review_required')
        self.assertIsNone(states[0]['last_valid'])
        axis = monitoring_api._station_monitoring_data_axis(
            configurations, conflicted, factor_states=states)
        self.assertEqual(axis['state'], 'review_required')
        self.assertEqual(axis['reason_code'], 'valid_value_conflict')

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
        self.assertEqual(
            next(point['value'] for point in trend.get_json()['points'] if point['status'] == 'value'),
            0.0,
        )
        self.assertEqual(trend.get_json()['standard_unit'], 'mg/L')

    def test_historical_reparse_reads_verified_archived_raw_evidence(self):
        payload = bytes.fromhex('0311') + bcd_number(12.3, 3, 1)
        raw_id = self._persist(
            payload, sent_at=datetime(2020, 6, 12, 4, 0),
            observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2026-09-09T00:00:00+00:00')
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        self.assertEqual(archive_raw_day(
            self.database, '2026-09-09',
            storage_policy_for_mode(Path(self.temp_dir.name), 'validation')), 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                'SELECT length(raw_frame) FROM ingest_raw_frames WHERE id=?', (raw_id,)
            ).fetchone()[0], 0)

        self.assertEqual(normalize_raw_frame(
            self.database, raw_id, normalization_version='archived-reparse-v2'), 'accepted')

    def test_unconfigured_expected_factors_keep_formal_values_readable_without_completeness(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('DELETE FROM station_expected_factors WHERE site_id=?', (self.site_id,))
            connection.execute('DELETE FROM station_expected_factor_configs WHERE site_id=?', (self.site_id,))
            connection.commit()
        raw_id = self._persist(
            bytes.fromhex('4612') + bcd_number(7.12, 4, 2),
            sent_at=datetime(2020, 6, 12, 4, 0),
            observed_at=datetime(2020, 6, 12, 4, 0),
        )
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        client = web_app.app.test_client()
        headers = self._headers('monitor-admin')
        latest = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/latest', headers=headers)
        self.assertEqual(latest.status_code, 200, latest.get_json())
        self.assertEqual(
            [(item['business_metric'], item['standard_value'])
             for item in latest.get_json()['items']],
            [('ph', 7.12)],
        )
        overview = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers)
        self.assertEqual(overview.status_code, 200, overview.get_json())
        body = overview.get_json()
        self.assertEqual(body['site']['reason_code'], 'expected_factors_unconfigured')
        self.assertEqual(body['site']['latest_cycle']['status'], 'not_configured')
        self.assertEqual(
            body['site']['latest_cycle']['reason_code'], 'expected_factors_unconfigured')
        self.assertEqual(body['site']['latest_cycle']['expected_count'], 0)
        self.assertEqual(body['site']['latest_cycle']['arrived_count'], 0)
        self.assertEqual(
            [(item['business_metric'], item['standard_value'])
             for item in body['monitoring']['latest_values']],
            [('ph', 7.12)],
        )
        listed = client.get('/api/station-monitoring/sites?scope=all', headers=headers)
        self.assertEqual(listed.status_code, 200, listed.get_json())
        item = next(
            row for row in listed.get_json()['items'] if row['site_id'] == self.site_id)
        self.assertEqual(item['reason_code'], 'expected_factors_unconfigured')
        self.assertEqual(item['last_valid_observation_at'], '2020-06-11T20:00:00+00:00')
        self.assertEqual(item['latest_cycle']['status'], 'not_configured')

    def test_latest_cycle_explains_missing_time_configuration(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('DELETE FROM monitoring_business_schedules')
            connection.commit()
            cycle = latest_cycle(
                connection, self.site_id, at_time='2020-06-11T21:00:00+00:00')
        self.assertEqual(cycle['status'], 'not_configured')
        self.assertEqual(cycle['reason_code'], 'time_configuration_missing')
        self.assertEqual(cycle['arrived_count'], 0)

    def test_latest_cycle_explains_when_no_period_is_yet_evaluable(self):
        effective_from = '2020-06-11T20:30:00+00:00'
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                'UPDATE station_expected_factor_configs SET effective_from=?',
                (effective_from,),
            )
            connection.execute(
                'UPDATE station_expected_factors SET effective_from=?',
                (effective_from,),
            )
            connection.commit()
            cycle = latest_cycle(
                connection, self.site_id, at_time='2020-06-11T21:00:00+00:00')
        self.assertEqual(cycle['status'], 'not_configured')
        self.assertEqual(cycle['reason_code'], 'no_evaluable_period')
        self.assertEqual(cycle['arrived_count'], 0)

    def test_partial_unmapped_evidence_and_read_permission_boundaries(self):
        raw_id = self._persist(bytes.fromhex('0311') + bcd_number(10.0, 3, 1) + bytes.fromhex('FEED'), serial=8)
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'partial')
        client = web_app.app.test_client()
        operator = self._headers('monitor-operator')
        operator_latest = client.get(
            f'/api/station-monitoring/sites/{self.other_site_id}/latest', headers=operator)
        self.assertEqual(operator_latest.status_code, 409)
        self.assertEqual(operator_latest.get_json()['code'], 'MONITORING_NOT_CONNECTED')
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
                   VALUES (?, '0311', 'turbidity', 'INST-A', 60, 15, ?)""",
                (self.endpoint_id, '2020-06-11T18:30:00+00:00'),
            )
            self._set_expected_factors(
                connection, ('ph', 'turbidity', 'codmn', 'ammonia'))
            connection.commit()
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        response = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=turbidity&start=2020-06-11T18:00:00%2B00:00&end=2020-06-11T18:10:00%2B00:00',
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

    def test_expired_profile_does_not_override_bound_endpoint_identity(self):
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
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['status'], 'no_observations')

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
            connection.execute(
                "UPDATE observation_values SET quality='suspect' "
                "WHERE observation_batch_id=(SELECT id FROM observation_batches WHERE raw_frame_id=?)",
                (raw_id,),
            )
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
        self.assertEqual(coverage['displayed_points'], 0)
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
                "UPDATE observation_values SET quality='suspect' "
                "WHERE observation_batch_id=(SELECT id FROM observation_batches WHERE raw_frame_id=?)",
                (suspect,),
            )
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

    def test_same_slot_same_value_is_collapsed_but_different_value_is_conflict(self):
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
                ['selected'],
            )
        self.assertEqual(normalize_raw_frame(
            self.database, first, normalization_version='reproject-selected-v3'), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                [row[0] for row in connection.execute(
                    "SELECT slot_state FROM monitoring_business_observations WHERE is_current=1 ORDER BY id")],
                ['selected'],
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
        self.assertEqual(coverage['duplicate_records'], 0)
        self.assertEqual(coverage['conflict_slots'], 0)
        conflicting = self._persist(
            bytes.fromhex('0311') + bcd_number(5.6, 3, 1), serial=54,
            sent_at=datetime(2020, 6, 12, 4, 2), observed_at=datetime(2020, 6, 12, 4, 0),
            received_at='2020-06-11T20:05:02+00:00')
        self.assertEqual(normalize_raw_frame(self.database, conflicting), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            states = [row[0] for row in connection.execute(
                'SELECT slot_state FROM monitoring_business_observations WHERE is_current=1 ORDER BY id')]
        self.assertEqual(states, ['conflict', 'conflict'])
        conflicted = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-11T20:00:00%2B00:00&end=2020-06-11T20:10:00%2B00:00',
            headers=self._headers('monitor-admin'),
        ).get_json()
        self.assertEqual(conflicted['points'][0]['status'], 'review_required')
        self.assertIsNone(conflicted['points'][0]['value'])
        self.assertEqual(conflicted['coverage']['missing_points'], 0)
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
            configuration = next(item for item in current_factor_configurations(
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
            items = latest_values(connection, self.site_id)
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
        self.assertEqual(default_window.get_json()['window']['preset'], '7d')
        default_start = datetime.fromisoformat(default_window.get_json()['window']['start'])
        default_end = datetime.fromisoformat(default_window.get_json()['window']['end'])
        self.assertEqual(default_end - default_start, timedelta(days=7))
        day = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&range=24h',
            headers=admin,
        )
        self.assertEqual(day.status_code, 200, day.get_json())
        self.assertEqual(day.get_json()['window']['preset'], '24h')
        long_window = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&start=2020-06-01T00:00:00%2B00:00&end=2020-06-03T00:01:00%2B00:00',
            headers=admin,
        )
        self.assertEqual(long_window.status_code, 200, long_window.get_json())
        self.assertEqual(long_window.get_json()['window']['preset'], 'custom')
        month = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&range=30d',
            headers=admin,
        )
        self.assertEqual(month.status_code, 200, month.get_json())
        self.assertEqual(month.get_json()['window']['preset'], '30d')
        invalid_range = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp&range=90d',
            headers=admin,
        )
        self.assertEqual(invalid_range.status_code, 400, invalid_range.get_json())
        self.assertEqual(invalid_range.get_json()['code'], 'MONITORING_TREND_RANGE_INVALID')
        invalid_cursor = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend'
            '?metric=water_temp&cursor=not-a-cursor', headers=admin)
        self.assertEqual(invalid_cursor.status_code, 400, invalid_cursor.get_json())
        self.assertEqual(invalid_cursor.get_json()['code'], 'MONITORING_INVALID_CURSOR')
        for serial in range(40, 43):
            raw_id = self._persist(bytes.fromhex('FEED'), serial=serial)
            self.assertEqual(normalize_raw_frame(self.database, raw_id), 'partial')
        page = client.get('/api/station-monitoring/quality-issues?page=1&page_size=2', headers=admin)
        self.assertEqual(page.status_code, 200, page.get_json())
        self.assertEqual(len(page.get_json()['items']), 2)
        self.assertGreaterEqual(page.get_json()['total'], 3)
        self.assertEqual(client.get('/api/station-monitoring/quality-issues?page_size=101', headers=admin).status_code, 400)

    def test_trend_paginates_newest_1000_points_without_silent_truncation(self):
        start = datetime(2020, 6, 11, 0, tzinfo=timezone.utc)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('DELETE FROM monitoring_business_schedules')
            for index in range(1005):
                observed_at = (start + timedelta(minutes=index)).isoformat()
                raw = connection.execute(
                    """INSERT INTO ingest_raw_frames(
                           endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                           crc_status,authentication_status,disposition,persistence_state)
                       VALUES (?,?,?,?,?,?,'valid','authenticated','accepted','persisted')""",
                    (self.endpoint_id, '0012345678', observed_at,
                     f'bounded-trend-{index}', b'isolated', 8),
                )
                batch = connection.execute(
                    """INSERT INTO observation_batches(
                           raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                           reported_at,observed_at,received_at,granularity,aggregation_source,
                           idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'device_reported',?,?,'accepted','completed',?)""",
                    (raw.lastrowid, self.endpoint_id, self.site_id, 2011, index,
                     observed_at, observed_at, observed_at, 'realtime',
                     f'bounded-trend-{index}', 'station-monitoring-normalizer-v4', observed_at),
                )
                connection.execute(
                    """INSERT INTO observation_values(
                           observation_batch_id,protocol_code,business_metric,raw_value,raw_unit,
                           standard_value,standard_unit,quality,instrument_asset_code,
                           parser_version,is_published)
                       VALUES (?, '4612', 'ph', ?, 'pH', ?, 'pH', 'valid',
                               'INST-A', 'isolated', 1)""",
                    (batch.lastrowid, index, index),
                )
            connection.commit()
        client = web_app.app.test_client()
        headers = self._headers('monitor-admin')
        query = (
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=ph'
            f'&start={start.isoformat().replace("+", "%2B")}'
            f'&end={(start + timedelta(minutes=1006)).isoformat().replace("+", "%2B")}'
            '&limit=1000'
        )
        first = client.get(query, headers=headers)
        self.assertEqual(first.status_code, 200, first.get_json())
        first_body = first.get_json()
        self.assertEqual(len(first_body['points']), 1000)
        self.assertEqual(first_body['points'][0]['value'], 5)
        self.assertEqual(first_body['points'][-1]['value'], 1004)
        self.assertTrue(first_body['pagination']['has_more'])
        self.assertIsNotNone(first_body['pagination']['next_cursor'])
        self.assertEqual(first_body['coverage']['state'], 'partial_page')

        second = client.get(
            query + '&cursor=' + first_body['pagination']['next_cursor'], headers=headers)
        self.assertEqual(second.status_code, 200, second.get_json())
        second_body = second.get_json()
        self.assertEqual([point['value'] for point in second_body['points']], list(range(5)))
        self.assertFalse(second_body['pagination']['has_more'])
        self.assertIsNone(second_body['pagination']['next_cursor'])

    def test_result_trend_prefers_2061_at_same_collection_and_keeps_group_conflicts(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,business_metric,instrument_asset_code,
                       expected_interval_seconds,tolerance_seconds,effective_from)
                   VALUES (?,'HJ212:w01001','ph','INST-A',14400,900,
                           '2020-01-01T00:00:00+00:00')""",
                (self.endpoint_id,),
            )

            def add_candidate(observed_at, function_code, value, serial):
                raw = connection.execute(
                    """INSERT INTO ingest_raw_frames(
                           endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                           crc_status,authentication_status,disposition,persistence_state)
                       VALUES (?,?,?,?,?,8,'valid','authenticated','accepted','persisted')""",
                    (self.endpoint_id, '0012345678', observed_at,
                     f'trend-source-{serial}', b'isolated'),
                )
                batch = connection.execute(
                    """INSERT INTO observation_batches(
                           raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                           reported_at,observed_at,received_at,granularity,aggregation_source,
                           idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'device_reported',?,?,'accepted','completed',?)""",
                    (raw.lastrowid, self.endpoint_id, self.site_id, function_code, serial,
                     observed_at, observed_at, observed_at, 'periodic', f'trend-source-{serial}',
                     'station-monitoring-normalizer-v4', observed_at),
                )
                connection.execute(
                    """INSERT INTO observation_values(
                           observation_batch_id,protocol_code,business_metric,raw_value,raw_unit,
                           standard_value,standard_unit,quality,instrument_asset_code,
                           parser_version,is_published)
                       VALUES (?,'HJ212:w01001','ph',?,'pH',?,'pH','valid',
                               'INST-A',?,1)""",
                    (batch.lastrowid, value, value,
                     'isolated:avg' if function_code == 2061 else 'isolated:rtd'),
                )

            preferred_at = '2020-06-11T20:00:00+00:00'
            conflict_at = '2020-06-12T00:00:00+00:00'
            add_candidate(preferred_at, 2011, 7.1, 1)
            add_candidate(preferred_at, 2061, 7.3, 2)
            add_candidate(conflict_at, 2011, 7.2, 3)
            add_candidate(conflict_at, 2061, 7.4, 4)
            add_candidate(conflict_at, 2061, 7.5, 5)
            connection.commit()

            points, has_more, next_before = result_trend_page(
                connection, self.site_id, 'ph',
                '2020-06-11T19:00:00+00:00', '2020-06-12T01:00:00+00:00')

        self.assertFalse(has_more)
        self.assertIsNone(next_before)
        self.assertEqual(len(points), 2)
        preferred = next(point for point in points if point['observed_at'] == preferred_at)
        self.assertEqual(preferred['function_code'], 2061)
        self.assertEqual(preferred['value'], 7.3)
        self.assertEqual(preferred['selection_reason'], 'preferred_2061_same_collection')
        self.assertEqual(preferred['source_candidate_count'], 2)
        conflict = next(point for point in points if point['observed_at'] == conflict_at)
        self.assertEqual(conflict['function_code'], 2061)
        self.assertEqual(conflict['status'], 'review_required')
        self.assertIsNone(conflict['value'])
        self.assertIsNone(conflict['source_value_text'])
        self.assertEqual(conflict['selection_reason'], 'valid_value_conflict')
        self.assertEqual(conflict['candidate_count'], 2)
        self.assertEqual(conflict['source_candidate_count'], 3)

    def test_result_trend_cursor_pages_complete_logical_groups(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,business_metric,instrument_asset_code,
                       expected_interval_seconds,tolerance_seconds,effective_from)
                   VALUES (?,'HJ212:w01001','ph','INST-A',14400,900,
                           '2020-01-01T00:00:00+00:00')""",
                (self.endpoint_id,),
            )

            def add_candidate(observed_at, function_code, value, serial):
                raw = connection.execute(
                    """INSERT INTO ingest_raw_frames(
                           endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                           crc_status,authentication_status,disposition,persistence_state)
                       VALUES (?,?,?,?,?,8,'valid','authenticated','accepted','persisted')""",
                    (self.endpoint_id, '0012345678', observed_at,
                     f'trend-page-group-{serial}', b'isolated'),
                )
                batch = connection.execute(
                    """INSERT INTO observation_batches(
                           raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                           reported_at,observed_at,received_at,granularity,aggregation_source,
                           idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'device_reported',?,?,'accepted','completed',?)""",
                    (raw.lastrowid, self.endpoint_id, self.site_id, function_code, serial,
                     observed_at, observed_at, observed_at, 'periodic',
                     f'trend-page-group-{serial}', 'station-monitoring-normalizer-v4', observed_at),
                )
                connection.execute(
                    """INSERT INTO observation_values(
                           observation_batch_id,protocol_code,business_metric,raw_value,raw_unit,
                           standard_value,standard_unit,quality,instrument_asset_code,
                           parser_version,is_published)
                       VALUES (?,'HJ212:w01001','ph',?,'pH',?,'pH','valid',
                               'INST-A',?,1)""",
                    (batch.lastrowid, value, value,
                     'isolated:avg' if function_code == 2061 else 'isolated:rtd'),
                )

            oldest_at = '2020-06-11T16:00:00+00:00'
            preferred_at = '2020-06-11T20:00:00+00:00'
            conflict_at = '2020-06-12T00:00:00+00:00'
            add_candidate(oldest_at, 2011, 7.0, 1)
            add_candidate(preferred_at, 2011, 7.1, 2)
            add_candidate(preferred_at, 2061, 7.3, 3)
            add_candidate(conflict_at, 2011, 7.2, 4)
            add_candidate(conflict_at, 2061, 7.4, 5)
            add_candidate(conflict_at, 2061, 7.5, 6)
            connection.commit()

        client = web_app.app.test_client()
        query = (
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=ph'
            '&start=2020-06-11T15:00:00%2B00:00&end=2020-06-12T01:00:00%2B00:00'
            '&limit=1'
        )
        headers = self._headers('monitor-admin')
        pages = []
        cursor = None
        for expected_more in (True, True, False):
            response = client.get(
                query + (f'&cursor={cursor}' if cursor else ''), headers=headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertEqual(len(body['points']), 1)
            self.assertEqual(body['pagination']['has_more'], expected_more)
            pages.append(body['points'][0])
            cursor = body['pagination']['next_cursor']

        self.assertIsNone(cursor)
        self.assertEqual(
            [point['observed_at'] for point in pages],
            [conflict_at, preferred_at, oldest_at])
        self.assertEqual(pages[0]['status'], 'review_required')
        self.assertEqual(pages[0]['source_candidate_count'], 3)
        self.assertEqual(pages[1]['value'], 7.3)
        self.assertEqual(pages[1]['selection_reason'], 'preferred_2061_same_collection')
        self.assertEqual(pages[1]['source_candidate_count'], 2)
        self.assertEqual(pages[2]['value'], 7.0)

    def test_minute_results_remain_visible_while_business_slots_stay_exact(self):
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
            business_count = connection.execute(
                "SELECT COUNT(*) FROM monitoring_business_observations WHERE business_site_id=?",
                (self.site_id,),
            ).fetchone()[0]
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
        self.assertEqual(business_count, 4)
        self.assertEqual(len(body['points']), 859)
        self.assertEqual(body['points'][0]['observed_at'], start.isoformat())
        self.assertEqual(body['points'][-1]['observed_at'], (end - timedelta(minutes=1)).isoformat())
        self.assertEqual(body['standard_unit'], 'pH')
        self.assertEqual(body['coverage']['displayed_points'], 859)
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
        self.assertEqual(response.get_json()['coverage']['missing_points'], 6)
        self.assertEqual(response.get_json()['points'], [])
        self.assertNotIn('2020-06-12T16:00:00+00:00', [
            point['scheduled_at'] for point in response.get_json()['points']])

    def test_unfinished_result_window_is_pending_not_missing_coverage(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                'UPDATE monitoring_business_schedules SET result_delay_seconds=7200,tolerance_seconds=0'
            )
            connection.commit()

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2020, 6, 12, 1, 0, 0, tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        with mock.patch.object(monitoring_api, 'datetime', FixedDateTime):
            response = web_app.app.test_client().get(
                f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp'
                '&start=2020-06-12T00:00:00%2B00:00&end=2020-06-12T04:00:00%2B00:00',
                headers=self._headers('monitor-admin'),
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        coverage = response.get_json()['coverage']
        self.assertEqual(coverage['expected_points'], 0)
        self.assertEqual(coverage['pending_points'], 1)
        self.assertEqual(coverage['missing_points'], 0)
        self.assertEqual(coverage['gap_count'], 0)

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
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE sites SET manager='旧负责人文本' WHERE id=?", (self.site_id,))
            connection.commit()
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
        self.assertEqual(body['site']['manager'], 'Operator')
        self.assertEqual([person['id'] for person in body['site']['responsible_people']], [self.operator_id])
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
            connection.execute("INSERT INTO user_roles(user_id,role) VALUES (?,?)", (self.admin_id, 'operator'))
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

    def test_station_monitoring_list_cache_is_permission_scoped_and_ttl_bounded(self):
        client = web_app.app.test_client()
        admin = self._headers('monitor-admin')
        operator = self._headers('monitor-operator')
        original = monitoring_api._station_monitoring_sites_body
        with mock.patch.object(
                monitoring_api, '_station_monitoring_sites_body', wraps=original) as projected:
            first = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
            second = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
            self.assertEqual((first.status_code, second.status_code), (200, 200))
            self.assertEqual(projected.call_count, 1)

            mine = client.get('/api/station-monitoring/sites?scope=mine', headers=operator)
            self.assertEqual(mine.status_code, 200, mine.get_json())
            self.assertEqual(projected.call_count, 2)

            self._insert_formal_observation('ph', 7.1, '2020-06-11T16:00:00+00:00')
            still_cached = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
            self.assertEqual(still_cached.status_code, 200, still_cached.get_json())
            self.assertEqual(projected.call_count, 2)

            with monitoring_api._station_monitoring_list_cache_lock:
                admin_key = [key for key in monitoring_api._station_monitoring_list_cache
                             if key[-3:] == ('all', 'current', '')][0]
                monitoring_api._station_monitoring_list_cache[admin_key]['refresh_after'] = 0
            refreshed = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
            self.assertEqual(refreshed.status_code, 200, refreshed.get_json())
            deadline = time.monotonic() + 3
            refreshing = True
            while time.monotonic() < deadline:
                with monitoring_api._station_monitoring_list_cache_lock:
                    refreshing = monitoring_api._station_monitoring_list_cache[admin_key]['refreshing']
                if projected.call_count >= 3 and not refreshing:
                    break
                time.sleep(0.01)
            self.assertEqual(projected.call_count, 3)
            self.assertFalse(refreshing)
            again = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
            self.assertEqual(again.status_code, 200, again.get_json())
            self.assertEqual(projected.call_count, 3)

        original_overview = monitoring_api._station_monitoring_overview
        with mock.patch.object(
                monitoring_api, '_station_monitoring_overview', wraps=original_overview) as projected:
            first = client.get(
                f'/api/station-monitoring/sites/{self.site_id}/overview', headers=admin)
            second = client.get(
                f'/api/station-monitoring/sites/{self.site_id}/overview', headers=admin)
            self.assertEqual((first.status_code, second.status_code), (200, 200))
            self.assertEqual(projected.call_count, 1)

    def test_station_monitoring_list_stale_refresh_is_single_flight_and_non_blocking(self):
        client = web_app.app.test_client()
        admin = self._headers('monitor-admin')
        seeded = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
        self.assertEqual(seeded.status_code, 200, seeded.get_json())
        with monitoring_api._station_monitoring_list_cache_lock:
            cache_key = next(iter(monitoring_api._station_monitoring_list_cache))
            cached_body = monitoring_api._station_monitoring_list_cache[cache_key]['body']
            monitoring_api._station_monitoring_list_cache[cache_key]['refresh_after'] = 0
            monitoring_api._station_monitoring_list_cache[cache_key]['fresh_until'] = 0
            monitoring_api._station_monitoring_list_cache[cache_key]['stale_until'] = time.monotonic() + 15

        started = threading.Event()
        release = threading.Event()
        original = monitoring_api._station_monitoring_sites_body

        def blocked_refresh(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(3), 'background cache refresh was not released')
            return original(*args, **kwargs)

        with mock.patch.object(monitoring_api, '_station_monitoring_sites_body', side_effect=blocked_refresh) as refreshed:
            try:
                first_started = time.perf_counter()
                first = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
                first_elapsed = time.perf_counter() - first_started
                self.assertEqual(first.status_code, 200, first.get_json())
                self.assertEqual(first.get_json()['items'], cached_body['items'])
                self.assertEqual(first.get_json()['result_snapshot']['state'], 'stale')
                self.assertTrue(first.get_json()['result_snapshot']['refreshing'])
                self.assertLess(first_elapsed, 1)
                self.assertTrue(started.wait(1), 'background cache refresh did not start')

                second_started = time.perf_counter()
                second = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
                second_elapsed = time.perf_counter() - second_started
                self.assertEqual(second.status_code, 200, second.get_json())
                self.assertEqual(second.get_json()['items'], cached_body['items'])
                self.assertEqual(second.get_json()['result_snapshot']['state'], 'stale')
                self.assertTrue(second.get_json()['result_snapshot']['refreshing'])
                self.assertLess(second_elapsed, 1)
                self.assertEqual(refreshed.call_count, 1)
            finally:
                release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with monitoring_api._station_monitoring_list_cache_lock:
                    refreshing = monitoring_api._station_monitoring_list_cache[cache_key]['refreshing']
                if not refreshing:
                    break
                time.sleep(0.01)
            self.assertFalse(refreshing)
            third = client.get('/api/station-monitoring/sites?scope=all', headers=admin)
            self.assertEqual(third.status_code, 200, third.get_json())
            self.assertEqual(refreshed.call_count, 1)

    def test_station_monitoring_list_different_users_cold_start_in_parallel(self):
        admin = self._headers('monitor-admin')
        operator = self._headers('monitor-operator')
        entered = []
        entered_lock = threading.Lock()
        both_entered = threading.Event()
        release = threading.Event()
        responses = {}
        original = monitoring_api._station_monitoring_sites_body

        def blocked_body(*args, **kwargs):
            with entered_lock:
                entered.append(args[4]['id'])
                if len(entered) == 2:
                    both_entered.set()
            self.assertTrue(release.wait(3), 'parallel list projections were not released')
            return original(*args, **kwargs)

        def request_sites(name, path, headers):
            responses[name] = web_app.app.test_client().get(path, headers=headers)

        with mock.patch.object(monitoring_api, '_station_monitoring_sites_body', side_effect=blocked_body) as projected:
            admin_thread = threading.Thread(
                target=request_sites,
                args=('admin', '/api/station-monitoring/sites?scope=all', admin))
            operator_thread = threading.Thread(
                target=request_sites,
                args=('operator', '/api/station-monitoring/sites?scope=mine', operator))
            admin_thread.start()
            operator_thread.start()
            parallel = both_entered.wait(2)
            release.set()
            admin_thread.join(5)
            operator_thread.join(5)

        self.assertTrue(parallel, 'different list cache keys were serialized by the global lock')
        self.assertFalse(admin_thread.is_alive())
        self.assertFalse(operator_thread.is_alive())
        self.assertEqual(projected.call_count, 2)
        self.assertCountEqual(entered, [self.admin_id, self.operator_id])
        self.assertEqual(responses['admin'].status_code, 200, responses['admin'].get_json())
        self.assertEqual(responses['admin'].get_json()['summary']['total'], 2)
        self.assertEqual(responses['operator'].status_code, 200, responses['operator'].get_json())
        self.assertEqual(
            [item['site_id'] for item in responses['operator'].get_json()['items']],
            [self.site_id],
        )

    def test_station_monitoring_list_same_user_cold_start_is_single_flight(self):
        admin = self._headers('monitor-admin')
        entered = threading.Event()
        release = threading.Event()
        responses = []
        original = monitoring_api._station_monitoring_sites_body

        def blocked_body(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3), 'single-flight list projection was not released')
            return original(*args, **kwargs)

        def request_sites():
            responses.append(web_app.app.test_client().get(
                '/api/station-monitoring/sites?scope=all', headers=admin))

        with mock.patch.object(monitoring_api, '_station_monitoring_sites_body', side_effect=blocked_body) as projected:
            first = threading.Thread(target=request_sites)
            second = threading.Thread(target=request_sites)
            first.start()
            self.assertTrue(entered.wait(1), 'first cold list projection did not start')
            second.start()
            time.sleep(0.2)
            self.assertEqual(projected.call_count, 1)
            release.set()
            first.join(5)
            second.join(5)

        self.assertEqual(len(responses), 2)
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(projected.call_count, 1)

    def test_station_monitoring_neutral_projection_expires_at_pre_refresh_boundary(self):
        rows = [(self.site_id, 'MON-1')]
        old_items = [{'site_id': self.site_id, 'marker': 'old'}]
        new_items = [{'site_id': self.site_id, 'marker': 'new'}]
        with mock.patch.object(
                monitoring_api, '_station_monitoring_batch_projections',
                side_effect=(old_items, new_items)) as projected:
            with mock.patch.object(web_app.time, 'monotonic', return_value=100.0):
                first = monitoring_api._station_monitoring_batch_projections_cached(None, rows)
                final_entry = monitoring_api._station_monitoring_cache_entry
                with mock.patch.object(monitoring_api, '_station_monitoring_list_version', return_value=('v1',)):
                    final_entry = final_entry(None, {'items': first})
                with monitoring_api._station_monitoring_batch_cache_lock:
                    neutral_entry = next(iter(monitoring_api._station_monitoring_batch_cache.values()))
                    neutral_expires_at = neutral_entry['expires_at']

            with mock.patch.object(web_app.time, 'monotonic', return_value=108.0):
                second = monitoring_api._station_monitoring_batch_projections_cached(None, rows)

        self.assertEqual(projected.call_count, 2)
        self.assertEqual(first[0]['marker'], 'old')
        self.assertEqual(second[0]['marker'], 'new')
        self.assertEqual(neutral_expires_at, final_entry['refresh_after'])
        self.assertLessEqual(final_entry['stale_until'] - 100.0, 15.0)

    def test_station_monitoring_overview_user_cache_does_not_extend_projection_expiry(self):
        clock = [100.0]
        old_overview = {'site': {'id': self.site_id}, 'marker': 'old'}
        new_overview = {'site': {'id': self.site_id}, 'marker': 'new'}

        with mock.patch.object(
                web_app.time, 'monotonic', side_effect=lambda: clock[0]):
            with mock.patch.object(
                    monitoring_api, '_station_monitoring_overview',
                    side_effect=(old_overview, new_overview)) as projected:
                first = monitoring_api._station_monitoring_overview_cached_body(
                    ('user-a', self.site_id), self.site_id)

                clock[0] = 109.0
                shared = monitoring_api._station_monitoring_overview_cached_body(
                    ('user-b', self.site_id), self.site_id)

                clock[0] = 111.0
                refreshed = monitoring_api._station_monitoring_overview_cached_body(
                    ('user-b', self.site_id), self.site_id)

        self.assertEqual(first['marker'], 'old')
        self.assertEqual(shared['marker'], 'old')
        self.assertEqual(refreshed['marker'], 'new')
        self.assertEqual(projected.call_count, 2)

    def test_station_monitoring_overview_different_users_cold_start_in_parallel(self):
        admin = self._headers('monitor-admin')
        operator = self._headers('monitor-operator')
        entered = 0
        entered_lock = threading.Lock()
        both_entered = threading.Event()
        release = threading.Event()
        responses = {}
        original = monitoring_api._station_monitoring_overview_projection_body

        def blocked_overview(*args, **kwargs):
            nonlocal entered
            with entered_lock:
                entered += 1
                if entered == 2:
                    both_entered.set()
            self.assertTrue(release.wait(3), 'parallel overview projections were not released')
            return original(*args, **kwargs)

        def request_overview(name, headers):
            responses[name] = web_app.app.test_client().get(
                f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers)

        with mock.patch.object(
                monitoring_api, '_station_monitoring_overview_projection_body',
                side_effect=blocked_overview) as projected:
            admin_thread = threading.Thread(target=request_overview, args=('admin', admin))
            operator_thread = threading.Thread(target=request_overview, args=('operator', operator))
            admin_thread.start()
            operator_thread.start()
            parallel = both_entered.wait(2)
            release.set()
            admin_thread.join(5)
            operator_thread.join(5)

        self.assertTrue(parallel, 'different overview cache keys were serialized by the global lock')
        self.assertFalse(admin_thread.is_alive())
        self.assertFalse(operator_thread.is_alive())
        self.assertEqual(projected.call_count, 2)
        self.assertEqual(responses['admin'].status_code, 200, responses['admin'].get_json())
        self.assertEqual(responses['operator'].status_code, 200, responses['operator'].get_json())

    def test_station_monitoring_overview_same_user_cold_start_is_single_flight(self):
        admin = self._headers('monitor-admin')
        entered = threading.Event()
        release = threading.Event()
        responses = []
        original = monitoring_api._station_monitoring_overview_projection_body

        def blocked_overview(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3), 'single-flight overview projection was not released')
            return original(*args, **kwargs)

        def request_overview():
            responses.append(web_app.app.test_client().get(
                f'/api/station-monitoring/sites/{self.site_id}/overview', headers=admin))

        with mock.patch.object(
                monitoring_api, '_station_monitoring_overview_projection_body',
                side_effect=blocked_overview) as projected:
            first = threading.Thread(target=request_overview)
            second = threading.Thread(target=request_overview)
            first.start()
            self.assertTrue(entered.wait(1), 'first cold overview projection did not start')
            second.start()
            time.sleep(0.2)
            self.assertEqual(projected.call_count, 1)
            release.set()
            first.join(5)
            second.join(5)

        self.assertEqual(len(responses), 2)
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(projected.call_count, 1)

    def test_station_monitoring_scope_enforces_operator_and_zero_assignment_boundaries(self):
        client = web_app.app.test_client()
        operator = self._headers('monitor-operator')
        mine = client.get('/api/station-monitoring/sites?scope=mine', headers=operator)
        self.assertEqual(mine.status_code, 200, mine.get_json())
        self.assertEqual(mine.get_json()['available_scopes'], ['mine', 'all'])
        self.assertEqual(mine.get_json()['scope_counts'], {'mine': 1, 'all': 2})
        self.assertEqual([item['site_id'] for item in mine.get_json()['items']], [self.site_id])
        operator_all = client.get('/api/station-monitoring/sites?scope=all', headers=operator)
        self.assertEqual(operator_all.status_code, 200, operator_all.get_json())
        self.assertEqual(
            [item['site_id'] for item in operator_all.get_json()['items']],
            [self.site_id, self.other_site_id],
        )
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
        self.assertEqual(body['axes']['data']['reason'], '完整结果窗口结束后仍有配置因子缺测')
        self.assertNotEqual(body['axes']['data']['reason'], body['axes']['communication']['reason'])
        self.assertTrue(all(item['standard_unit'] for item in body['monitoring']['factors']))

    def test_presented_value_keeps_actual_observation_and_planned_slot_separate(self):
        presented = monitoring_api._station_monitoring_presented_value({
            'scheduled_at': '2026-09-21T04:00:00+00:00',
            'observed_at': '2026-09-21T06:01:00+00:00',
            'received_at': '2026-09-21T06:01:05+00:00',
        })
        self.assertEqual(presented['observed_at'], '2026-09-21T06:01:00+00:00')
        self.assertEqual(presented['scheduled_at'], '2026-09-21T04:00:00+00:00')
        self.assertEqual(presented['source_data_time'], '2026-09-21T06:01:00+00:00')
        self.assertEqual(presented['received_at'], '2026-09-21T06:01:05+00:00')

    def test_factor_projection_exposes_each_fault_reason_independently(self):
        now = '2026-09-21T06:01:00+00:00'
        with closing(sqlite3.connect(self.database)) as connection:
            raw = connection.execute(
                """INSERT INTO ingest_raw_frames(
                       endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                       crc_status,authentication_status,disposition,persistence_state)
                   VALUES (?,?,?,?,?,?,'valid','authenticated','accepted','persisted')""",
                (self.endpoint_id, '0012345678', now, 'four-faults', b'isolated', 8),
            )
            batch = connection.execute(
                """INSERT INTO observation_batches(
                       raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                       reported_at,observed_at,received_at,granularity,aggregation_source,
                       idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'device_reported',?,?,'partial','completed',?)""",
                (raw.lastrowid, self.endpoint_id, self.site_id, 2011, 1, now, now, now,
                 'realtime', 'four-faults', 'isolated', now),
            )
            connection.executemany(
                """INSERT INTO monitoring_quality_issues(
                       raw_frame_id,observation_batch_id,business_site_id,issue_type,object_summary,
                       first_seen_at,last_seen_at)
                   VALUES (?,?,?,'hj212_flag_fault',?,?,?)""",
                [(raw.lastrowid, batch.lastrowid, self.site_id, code, now, now)
                 for code in ('0311', '4612', '4A11', '4C1A')],
            )
            connection.commit()
            connection.row_factory = sqlite3.Row
            configs = current_factor_configurations(connection, self.site_id)
            states = monitoring_api._station_monitoring_factor_states(connection, self.site_id, configs, [])
        self.assertEqual(len(states), 4)
        self.assertEqual({item['state'] for item in states}, {'fault'})
        self.assertEqual({item['reason_code'] for item in states}, {'hj212_flag_fault'})

    def test_station_monitoring_normal_requires_every_configured_factor_fresh(self):
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        profile = {'endpoint_id': self.endpoint_id, 'expected_interval_seconds': 60}
        configs = [
            {'endpoint_id': self.endpoint_id, 'protocol_code': code, 'business_metric': metric,
             'instrument_asset_code': 'INST-A', 'expected_interval_seconds': 60, 'tolerance_seconds': 15}
            for code, metric in (('0311', 'water_temp'), ('4612', 'ph'), ('4A11', 'codmn'), ('4C1A', 'ammonia'))
        ]
        result = monitoring_api._station_monitoring_summary_projection(
            None, self.site_id, profile, configs, [dict(configs[0], observed_at=now)], now)
        self.assertEqual(result['status'], 'attention')
        self.assertEqual(result['reason_code'], 'missing_observation')

    def test_station_monitoring_marks_stale_endpoint_communication(self):
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        profile = {'endpoint_id': self.endpoint_id, 'expected_interval_seconds': 60}
        config = {'endpoint_id': self.endpoint_id, 'protocol_code': '0311', 'business_metric': 'water_temp',
                  'instrument_asset_code': 'INST-A', 'expected_interval_seconds': 60, 'tolerance_seconds': 15}
        result = monitoring_api._station_monitoring_summary_projection(
            None, self.site_id, profile, [config], [dict(config, observed_at=now)], '2020-01-01T00:00:00+00:00')
        self.assertEqual(result['status'], 'attention')
        self.assertEqual(result['reason_code'], 'stale_communication')
        data_axis = monitoring_api._station_monitoring_data_axis([config], [dict(config, observed_at=now)])
        self.assertEqual(data_axis['state'], 'fresh')
        self.assertEqual(data_axis['reason'], '最近正式业务观测仍在应到周期内')

    def test_all_published_monitoring_factors_have_distinct_chinese_names(self):
        with closing(sqlite3.connect(self.database)) as connection:
            metrics = [row[0] for row in connection.execute(
                'SELECT DISTINCT business_metric FROM monitoring_factor_definitions WHERE is_published=1 AND business_metric IS NOT NULL')]
        labels = [monitoring_api._STATION_MONITORING_FACTOR_LABELS.get(metric) for metric in metrics]
        self.assertNotIn(None, labels)
        self.assertEqual(len(labels), len(set(labels)))

    def test_station_monitoring_overview_permissions_and_missing_site(self):
        client = web_app.app.test_client()
        operator = self._headers('monitor-operator')
        self.assertTrue(client.get(f'/api/station-monitoring/sites/{self.site_id}/overview', headers=operator).get_json()['site']['can_calibrate'])
        summary = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/overview?detail=summary', headers=operator)
        self.assertEqual(summary.status_code, 200, summary.get_json())
        self.assertNotIn('axes', summary.get_json())
        diagnostics = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/diagnostics', headers=operator)
        self.assertEqual(diagnostics.status_code, 200, diagnostics.get_json())
        self.assertEqual(diagnostics.get_json()['site_id'], self.site_id)
        self.assertEqual(set(diagnostics.get_json()['axes']), {'communication', 'data'})
        public_overview = client.get(
            f'/api/station-monitoring/sites/{self.other_site_id}/overview', headers=operator)
        self.assertEqual(public_overview.status_code, 200, public_overview.get_json())
        self.assertFalse(public_overview.get_json()['site']['can_calibrate'])
        self.assertEqual(client.get(f'/api/station-monitoring/sites/{self.other_site_id}/diagnostics', headers=operator).status_code, 403)
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
        self.assertEqual(item['latest_cycle']['status'], 'incomplete')
        self.assertEqual(item['latest_cycle']['missing_count'], 4)

    def test_unbound_station_cycle_reason_follows_expected_factor_configuration(self):
        client = web_app.app.test_client()
        headers = self._headers('monitor-admin')
        before = client.get(
            f'/api/station-monitoring/sites/{self.other_site_id}/overview', headers=headers)
        self.assertEqual(before.status_code, 200, before.get_json())
        self.assertEqual(
            before.get_json()['site']['latest_cycle']['reason_code'],
            'expected_factors_unconfigured',
        )

        with closing(sqlite3.connect(self.database)) as connection:
            config_id = connection.execute(
                """INSERT INTO station_expected_factor_configs(
                       site_id,version,effective_from,previous_metrics_json,
                       selected_metrics_json,changed_by_user_id,idempotency_key,created_at)
                   VALUES (?,1,?,'[]','[\"ph\"]',?,?,?)""",
                (self.other_site_id, '2020-01-01T00:00:00+00:00', self.admin_id,
                 'unbound-configured-v1', '2020-01-01T00:00:00+00:00'),
            ).lastrowid
            connection.execute(
                """INSERT INTO station_expected_factors(
                       config_id,site_id,business_metric,display_order,effective_from)
                   VALUES (?,?,'ph',0,'2020-01-01T00:00:00+00:00')""",
                (config_id, self.other_site_id),
            )
            connection.commit()
        web_app._invalidate_station_monitoring_projection_caches()

        after = client.get(
            f'/api/station-monitoring/sites/{self.other_site_id}/overview', headers=headers)
        self.assertEqual(after.status_code, 200, after.get_json())
        cycle = after.get_json()['site']['latest_cycle']
        self.assertEqual(cycle['reason_code'], 'time_configuration_missing')
        self.assertEqual(cycle['expected_count'], 1)

    def test_recent_credential_failure_is_visible_without_becoming_authenticated_data(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        local_now = now.astimezone().replace(tzinfo=None)
        frame = parse_frame(make_32h(
            bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=89,
            sent_at=local_now, observed_at=local_now.replace(second=0),
            credential=b'\x99\x99'))
        authentication = self.storage.authenticate(frame)
        self.assertEqual(authentication.status, 'credential_failed')
        raw_id, duplicate = self.storage.persist_parsed(
            frame, authentication, now.isoformat())
        self.assertFalse(duplicate)

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                'SELECT authentication_status FROM ingest_raw_frames WHERE id=?',
                (raw_id,),
            ).fetchone()[0], 'credential_failed')
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM observation_batches WHERE raw_frame_id=?',
                (raw_id,),
            ).fetchone()[0], 0)

        headers = self._headers('monitor-admin')
        client = web_app.app.test_client()
        summary = client.get('/api/station-monitoring/access-summary', headers=headers)
        listed = client.get('/api/station-monitoring/sites?scope=all', headers=headers)
        detail = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers)
        for response in (summary, listed, detail):
            self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(summary.get_json()['authentication']['credential_failed_frames'], 1)
        self.assertEqual(summary.get_json()['authentication']['authenticated_frames'], 0)
        item = next(
            row for row in listed.get_json()['items'] if row['site_id'] == self.site_id)
        self.assertEqual(item['reason_code'], 'authentication_failed')
        self.assertEqual(item['recent_authentication_failures'], 1)
        self.assertEqual(item['communication']['status'], 'authentication_failed')
        self.assertEqual(detail.get_json()['site']['reason_code'], 'authentication_failed')

    def test_access_summary_keeps_historical_last_authenticated_time_outside_diagnostic_window(self):
        received_at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=9)
        local_received_at = received_at.astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
        self._persist(
            bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=88,
            sent_at=local_received_at, observed_at=local_received_at.replace(second=0),
            received_at=received_at.isoformat(),
        )

        summary = web_app.app.test_client().get(
            '/api/station-monitoring/access-summary',
            headers=self._headers('monitor-admin'),
        ).get_json()

        self.assertEqual(summary['authentication']['authenticated_frames'], 0)
        self.assertEqual(
            summary['authentication']['last_authenticated_received_at'],
            received_at.isoformat(),
        )

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

    def test_post_r22_frame_reaches_storage_projection_summary_list_detail_and_trend(self):
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Shanghai'))
        current_slot_local = now_local.replace(
            hour=(now_local.hour // 4) * 4, minute=0, second=0, microsecond=0)
        current_slot_utc = current_slot_local.astimezone(timezone.utc).isoformat()
        completed_slot_local = current_slot_local - timedelta(hours=4)
        completed_slot_utc = completed_slot_local.astimezone(timezone.utc)
        received_at = completed_slot_utc + timedelta(minutes=5)
        sent_at = (completed_slot_local + timedelta(minutes=5)).replace(tzinfo=None)
        observed_at = completed_slot_local.replace(tzinfo=None)
        scheduled_at = completed_slot_utc.isoformat()
        trend_start = (completed_slot_utc - timedelta(hours=1)).isoformat().replace('+', '%2B')
        trend_end = (completed_slot_utc + timedelta(hours=1)).isoformat().replace('+', '%2B')
        raw_id = self._persist(
            bytes.fromhex('0311') + bcd_number(8.8, 3, 1), serial=90,
            sent_at=sent_at, observed_at=observed_at,
            received_at=received_at.isoformat())
        self.assertEqual(normalize_raw_frame(self.database, raw_id), 'accepted')
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                'SELECT authentication_status FROM ingest_raw_frames WHERE id=?',
                (raw_id,),
            ).fetchone()[0], 'authenticated')
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM observation_batches WHERE raw_frame_id=? AND batch_status=?',
                (raw_id, 'accepted'),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                """SELECT COUNT(*) FROM monitoring_business_observations business
                   JOIN observation_batches batch ON batch.id=business.observation_batch_id
                   WHERE batch.raw_frame_id=? AND business.business_metric='water_temp'
                     AND business.standard_value=8.8 AND business.quality='valid'""",
                (raw_id,),
            ).fetchone()[0], 1)

        headers = self._headers('monitor-admin')
        client = web_app.app.test_client()
        summary = client.get('/api/station-monitoring/access-summary', headers=headers)
        listed = client.get('/api/station-monitoring/sites?scope=all', headers=headers)
        detail = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/overview', headers=headers)
        trend = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp'
            f'&start={trend_start}&end={trend_end}',
            headers=headers,
        )
        for response in (summary, listed, detail, trend):
            self.assertEqual(response.status_code, 200, response.get_json())

        summary_body = summary.get_json()
        self.assertEqual(summary_body['authentication']['authenticated_frames'], 1)
        self.assertEqual(summary_body['observation']['valid_sites'], 1)
        list_item = next(
            item for item in listed.get_json()['items'] if item['site_id'] == self.site_id)
        self.assertEqual(list_item['last_valid_observation_at'], scheduled_at)
        list_factor = next(
            item for item in list_item['latest_cycle']['factors']
            if item['business_metric'] == 'water_temp')
        self.assertEqual(list_item['latest_cycle']['scheduled_at'], current_slot_utc)
        self.assertIn(list_factor['status'], ('pending', 'missing'))
        self.assertIsNone(list_factor['value'])
        detail_value = next(
            item for item in detail.get_json()['monitoring']['latest_values']
            if item['business_metric'] == 'water_temp')
        self.assertEqual(detail_value['standard_value'], 8.8)
        trend_point = next(
            item for item in trend.get_json()['points']
            if item['scheduled_at'] == scheduled_at)
        self.assertEqual((trend_point['status'], trend_point['value']), ('value', 8.8))

    def test_unknown_schedule_does_not_gate_latest_result_or_trend(self):
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
        self.assertNotEqual(item['monitoring_status'], 'interval_unconfigured')
        self.assertEqual(detail['site']['monitoring_status'], item['monitoring_status'])
        self.assertEqual(item['last_valid_observation_at'], '2020-06-11T18:00:00+00:00')
        latest_value = next(
            value for value in detail['monitoring']['latest_values']
            if value['business_metric'] == 'water_temp')
        self.assertEqual(latest_value['standard_value'], 8.8)
        self.assertEqual(latest_value['observed_at'], '2020-06-11T18:00:00+00:00')
        self.assertEqual(
            detail['monitoring']['axes']['data']['state'], 'result_available')
        trend = web_app.app.test_client().get(
            f'/api/station-monitoring/sites/{self.site_id}/trend?metric=water_temp'
            '&start=2020-06-11T16:00:00%2B00:00&end=2020-06-12T16:00:00%2B00:00',
            headers=headers,
        ).get_json()
        self.assertEqual(len(trend['points']), 1)
        self.assertEqual(trend['points'][0]['source_type'], 'normalized_observation')
        self.assertEqual(trend['points'][0]['observed_at'], '2020-06-11T18:00:00+00:00')
        self.assertEqual(trend['points'][0]['value'], 8.8)
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

    def test_hj212_minute_candidates_never_compete_with_formal_2061_avg(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,business_metric,instrument_asset_code,
                       expected_interval_seconds,tolerance_seconds,effective_from)
                   VALUES (?,'HJ212:w01001','ph','INST-A',14400,900,'2020-01-01T00:00:00+00:00')""",
                (self.endpoint_id,),
            )
            connection.execute(
                "UPDATE monitoring_business_schedules SET result_delay_seconds=7200,tolerance_seconds=900"
            )

            def project(function_code, value, serial):
                observed_at = ('2020-06-11T22:00:00+00:00' if function_code == 2011
                               else '2020-06-11T20:00:00+00:00')
                raw = connection.execute(
                    """INSERT INTO ingest_raw_frames(
                           endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                           crc_status,authentication_status,disposition,persistence_state)
                       VALUES (?,?,?,?,?,8,'valid','authenticated','accepted','persisted')""",
                    (self.endpoint_id, '0012345678', observed_at, f'hj-semantic-{serial}', b'isolated'),
                )
                batch = connection.execute(
                    """INSERT INTO observation_batches(
                           raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                           reported_at,observed_at,received_at,granularity,aggregation_source,
                           idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'device_reported',?,?,'accepted','completed',?)""",
                    (raw.lastrowid, self.endpoint_id, self.site_id, function_code, serial,
                     observed_at, observed_at, observed_at, 'realtime', f'hj-semantic-{serial}',
                     'station-monitoring-normalizer-v3', observed_at),
                )
                item = connection.execute(
                    """INSERT INTO observation_values(
                           observation_batch_id,protocol_code,business_metric,raw_value,raw_unit,
                           standard_value,standard_unit,quality,instrument_asset_code,parser_version)
                       VALUES (?,'HJ212:w01001','ph',?,'pH',?,'pH','valid','INST-A','isolated')""",
                    (batch.lastrowid, value, value),
                )
                self.assertTrue(_project_business_observation(
                    connection, value_id=item.lastrowid, batch_id=batch.lastrowid,
                    endpoint_id=self.endpoint_id, site_id=self.site_id,
                    protocol_code='HJ212:w01001', business_metric='ph',
                    instrument_asset_code='INST-A', observed_at=observed_at,
                    received_at=observed_at, standard_value=value, standard_unit='pH',
                    quality='valid', function_code=function_code,
                    source_value_kind='avg' if function_code == 2061 else 'rtd',
                ))

            project(2011, 7.1, 1)
            project(2011, 7.2, 2)
            cycle = latest_cycle(connection, self.site_id, at_time='2020-06-11T21:00:00+00:00')
            ph = next(item for item in cycle['factors'] if item['business_metric'] == 'ph')
            self.assertEqual(ph['status'], 'pending')
            self.assertEqual(ph['process_candidate_count'], 2)
            self.assertEqual(ph['formal_candidate_count'], 0)
            connection.commit()
            process_only_summary = web_app.app.test_client().get(
                '/api/station-monitoring/access-summary',
                headers=self._headers('monitor-admin'),
            ).get_json()
            self.assertEqual(process_only_summary['observation']['valid_sites'], 0)

            project(2061, 7.3, 3)
            cycle = latest_cycle(connection, self.site_id, at_time='2020-06-11T21:00:00+00:00')
            ph = next(item for item in cycle['factors'] if item['business_metric'] == 'ph')
            self.assertEqual((ph['status'], ph['value'], ph['source_type']),
                             ('value', 7.3, 'hj212_2061_avg'))
            connection.commit()
            formal_summary = web_app.app.test_client().get(
                '/api/station-monitoring/access-summary',
                headers=self._headers('monitor-admin'),
            ).get_json()
            self.assertEqual(formal_summary['observation']['valid_sites'], 1)

            project(2061, 7.4, 4)
            cycle = latest_cycle(connection, self.site_id, at_time='2020-06-11T21:00:00+00:00')
            ph = next(item for item in cycle['factors'] if item['business_metric'] == 'ph')
            self.assertEqual(ph['status'], 'review_required')
            self.assertIsNone(ph['value'])

    def test_batch_last_receipt_query_uses_current_state_index(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.executemany(
                """INSERT INTO ingest_raw_frames(
                       endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                       crc_status,authentication_status,disposition,persistence_state)
                   VALUES (?,?,?,?,?,8,'valid',?,?,'persisted')""",
                (
                    (self.endpoint_id, '0012345678', '2026-10-13T00:00:00+00:00',
                     'last-receipt-old', b'old', 'authenticated', 'accepted'),
                    (self.endpoint_id, '0012345678', '2026-10-13T01:00:00+00:00',
                     'last-receipt-new', b'new', 'authenticated', 'accepted'),
                    (self.endpoint_id, '0012345678', '2026-10-13T02:00:00+00:00',
                     'last-receipt-rejected', b'rejected', 'credential_failed', 'quarantined'),
                ),
            )
            connection.commit()
            bootstrap_retention_state(
                self.database, now=datetime(2026, 10, 13, 3, tzinfo=timezone.utc))
            latest = monitoring_api._station_monitoring_latest_authenticated_receipts(
                connection, [self.endpoint_id])
            self.assertEqual(
                latest[self.endpoint_id]['received_at'], '2026-10-13T01:00:00+00:00')
            marks = '?'
            plan = connection.execute(
                f"""EXPLAIN QUERY PLAN
                    SELECT endpoint_id,latest_authenticated_at AS received_at
                    FROM station_ingest_current_state
                    WHERE endpoint_id IN ({marks}) AND latest_authenticated_at IS NOT NULL""",
                (self.endpoint_id,),
            ).fetchall()
        detail = ' '.join(str(row[3]) for row in plan)
        self.assertIn('idx_station_ingest_current_endpoint', detail)
        self.assertNotIn('ingest_raw_frames', detail)

    def test_batch_projection_query_count_is_constant_for_one_ten_and_fifty_sites(self):
        def ensure_sites(connection, target):
            current = connection.execute(
                "SELECT COUNT(*) FROM sites WHERE code LIKE 'PERF-%'"
            ).fetchone()[0]
            base_mappings = connection.execute(
                """SELECT protocol_code,business_metric,expected_interval_seconds,tolerance_seconds
                   FROM monitoring_factor_mappings WHERE endpoint_id=? ORDER BY id""",
                (self.endpoint_id,),
            ).fetchall()
            for index in range(current + 1, target + 1):
                site_id = connection.execute(
                    "INSERT INTO sites(code,name,type) VALUES (?,?,?)",
                    (f'PERF-{index:03}', f'性能隔离站{index}', 'water'),
                ).lastrowid
                endpoint_id = connection.execute(
                    """INSERT INTO trusted_endpoints(
                           station_code,credential_hmac,business_site_id,endpoint_state)
                       VALUES (?,?,?,'bound')""",
                    (f'PERF-MN-{index:03}', f'isolated-hmac-{index}', site_id),
                ).lastrowid
                instrument = f'PERF-INST-{index}'
                connection.execute(
                    """INSERT INTO monitoring_endpoint_profiles(
                           endpoint_id,business_site_id,instrument_asset_code,
                           expected_granularity,expected_interval_seconds,effective_from)
                       VALUES (?,?,?,'realtime',60,'2020-01-01T00:00:00+00:00')""",
                    (endpoint_id, site_id, instrument),
                )
                connection.executemany(
                    """INSERT INTO monitoring_factor_mappings(
                           endpoint_id,protocol_code,business_metric,instrument_asset_code,
                           expected_interval_seconds,tolerance_seconds,effective_from)
                       VALUES (?,?,?,?,?,?,'2020-01-01T00:00:00+00:00')""",
                    [(endpoint_id, row[0], row[1], instrument, row[2], row[3])
                     for row in base_mappings],
                )
                connection.execute(
                    """INSERT INTO monitoring_business_schedules(
                           endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                           tolerance_seconds,effective_from)
                       VALUES (?,NULL,'Asia/Shanghai',14400,'00:00:00',600,
                               '2020-01-01T00:00:00+00:00')""",
                    (endpoint_id,),
                )
                config_id = connection.execute(
                    """INSERT INTO station_expected_factor_configs(
                           site_id,version,effective_from,previous_metrics_json,
                           selected_metrics_json,changed_by_user_id,idempotency_key,created_at)
                       VALUES (?,1,'2020-01-01T00:00:00+00:00','[]',?,?,?,
                               '2020-01-01T00:00:00+00:00')""",
                    (site_id, '["ph","water_temp","codmn","ammonia"]',
                     self.admin_id, f'performance-fixture-{site_id}'),
                ).lastrowid
                connection.executemany(
                    """INSERT INTO station_expected_factors(
                           config_id,site_id,business_metric,display_order,effective_from)
                       VALUES (?,?,?,?,?)""",
                    ((config_id, site_id, metric, order, '2020-01-01T00:00:00+00:00')
                     for order, metric in enumerate(('ph', 'water_temp', 'codmn', 'ammonia'))),
                )
            connection.commit()

        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            counts = {}
            for size in (1, 10, 50):
                ensure_sites(connection, size)
                site_rows = connection.execute(
                    """SELECT id,code,name,type,district,address,river,basin,
                              gps_lat AS lat,gps_lng AS lng,manager,phone
                       FROM sites WHERE code LIKE 'PERF-%' ORDER BY id LIMIT ?""",
                    (size,),
                ).fetchall()
                statements = []
                connection.set_trace_callback(
                    lambda sql: statements.append(sql)
                    if sql.lstrip().upper().startswith(('SELECT', 'WITH')) else None)
                try:
                    items = monitoring_api._station_monitoring_batch_projections(connection, site_rows)
                finally:
                    connection.set_trace_callback(None)
                counts[size] = len(statements)
                self.assertEqual(len(items), size)
                self.assertTrue(all(item['published_factor_count'] == 4 for item in items))
                statement_text = '\n'.join(statements)
                self.assertIn(
                    'WITH targets(business_site_id,scheduled_at) AS', statement_text)
                self.assertNotIn('business.scheduled_at>=', statement_text)
                self.assertNotIn('ROW_NUMBER() OVER', statement_text)
        self.assertEqual(counts, {1: 15, 10: 15, 50: 15})

    def test_latest_cycle_uses_nine_expected_factors_even_when_thirteen_are_mapped(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            all_definitions = connection.execute(
                """SELECT protocol_code,business_metric,standard_unit
                   FROM monitoring_factor_definitions
                   WHERE is_published=1 AND business_metric IS NOT NULL
                   GROUP BY business_metric ORDER BY business_metric"""
            ).fetchall()
            expected_metrics = list(monitoring_api._STATION_EXPECTED_FACTOR_METRICS)
            all_by_metric = {row['business_metric']: row for row in all_definitions}
            definitions = [all_by_metric[metric] for metric in expected_metrics]
            definitions.extend(row for row in all_definitions
                               if row['business_metric'] not in expected_metrics
                               and len(definitions) < 13)
            self.assertEqual(len(definitions), 13)
            definition_by_metric = {row['business_metric']: row for row in definitions}
            self.assertTrue(all(metric in definition_by_metric for metric in expected_metrics))
            connection.execute('DELETE FROM monitoring_factor_mappings WHERE endpoint_id=?', (self.endpoint_id,))
            connection.execute(
                'UPDATE monitoring_business_schedules SET result_delay_seconds=7200,tolerance_seconds=900'
            )
            connection.executemany(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,business_metric,instrument_asset_code,
                       expected_interval_seconds,tolerance_seconds,effective_from)
                   VALUES (?,?,?,?,14400,900,'2020-01-01T00:00:00+00:00')""",
                [(self.endpoint_id, row['protocol_code'], row['business_metric'], 'INST-A')
                 for row in definitions],
            )
            self._set_expected_factors(connection, expected_metrics)
            def project_cycle(definition, index, observed_at, prefix):
                raw = connection.execute(
                    """INSERT INTO ingest_raw_frames(
                           endpoint_id,station_code,received_at,frame_sha256,raw_frame,body_length,
                           crc_status,authentication_status,disposition,persistence_state)
                       VALUES (?,?,?,?,?,8,'valid','authenticated','accepted','persisted')""",
                    (self.endpoint_id, '0012345678', observed_at, f'{prefix}-{index}', b'isolated'),
                )
                batch = connection.execute(
                    """INSERT INTO observation_batches(
                           raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                           reported_at,observed_at,received_at,granularity,aggregation_source,
                           idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'device_reported',?,?,'accepted','completed',?)""",
                    (raw.lastrowid, self.endpoint_id, self.site_id, 2061, index, observed_at,
                     observed_at, observed_at, 'periodic', f'{prefix}-{index}',
                     'station-monitoring-normalizer-v3', observed_at),
                )
                value = connection.execute(
                    """INSERT INTO observation_values(
                           observation_batch_id,protocol_code,business_metric,raw_value,raw_unit,
                           standard_value,standard_unit,quality,instrument_asset_code,parser_version)
                       VALUES (?,?,?,?,?,?,?,?,?,'isolated')""",
                    (batch.lastrowid, definition['protocol_code'], definition['business_metric'],
                     index + 0.5, definition['standard_unit'], index + 0.5,
                     definition['standard_unit'], 'valid', 'INST-A'),
                )
                self.assertTrue(_project_business_observation(
                    connection, value_id=value.lastrowid, batch_id=batch.lastrowid,
                    endpoint_id=self.endpoint_id, site_id=self.site_id,
                    protocol_code=definition['protocol_code'],
                    business_metric=definition['business_metric'], instrument_asset_code='INST-A',
                    observed_at=observed_at, received_at=observed_at,
                    standard_value=index + 0.5, standard_unit=definition['standard_unit'],
                    quality='valid', function_code=2061, source_value_kind='avg',
                ))
            for index, definition in enumerate(definitions):
                project_cycle(definition, index, '2020-06-11T16:00:00+00:00', 'cycle-complete')
            latest_metrics = expected_metrics[:6]
            latest_definitions = [definition_by_metric[metric] for metric in latest_metrics]
            outside_denominator = [row for row in definitions
                                   if row['business_metric'] not in expected_metrics][:3]
            for index, definition in enumerate([*latest_definitions, *outside_denominator]):
                project_cycle(definition, index, '2020-06-11T20:00:00+00:00', 'cycle-latest')
            cycle = latest_cycle(connection, self.site_id, at_time='2020-06-11T23:00:00+00:00')
        self.assertEqual(cycle['status'], 'incomplete')
        self.assertIsNone(cycle['reason_code'])
        self.assertEqual(
            (cycle['arrived_count'], cycle['formed_count'], cycle['expected_count'],
             cycle['missing_count']),
            (6, 6, 9, 3))
        self.assertEqual(len([item for item in cycle['factors'] if item['status'] == 'missing']), 3)
        self.assertEqual({item['business_metric'] for item in cycle['factors']},
                         set(expected_metrics))
        self.assertTrue(set(row['business_metric'] for row in outside_denominator).isdisjoint(
            item['business_metric'] for item in cycle['factors']))
        self.assertEqual(cycle['latest_complete_cycle_at'], '2020-06-11T16:00:00+00:00')

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

    def test_formal_observation_drives_detail_analysis_and_health_without_sensor_data(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        observation_id = self._insert_formal_observation('ph', 7.34, yesterday.isoformat())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO param_thresholds(metric,label,unit,low,high,critical_low,critical_high) "
                "VALUES ('ph','pH','',6,9,5,10)"
            )
            connection.executemany(
                'INSERT INTO sensor_data(site_id,metric,value,recorded_at) VALUES (?,?,?,?)',
                [(self.site_id, 'ph', 99.0, yesterday.strftime('%Y-%m-%d %H:%M:%S'))] * 8,
            )
            connection.commit()

        client = web_app.app.test_client()
        headers = self._headers('monitor-admin')
        detail = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/latest', headers=headers)
        self.assertEqual(detail.status_code, 200, detail.get_json())
        latest = [item for item in detail.get_json()['items']
                  if item['business_metric'] == 'ph'][0]
        self.assertEqual(latest['standard_value'], 7.34)
        self.assertEqual(latest['standard_value_display'], '7.34')

        local_day = yesterday.astimezone().strftime('%Y-%m-%d')
        summary = client.get(f'/api/data/arrival/summary?date={local_day}', headers=headers)
        self.assertEqual(summary.status_code, 200, summary.get_json())
        ph_summary = [item for item in summary.get_json()['by_metric']
                      if item['metric'] == 'ph'][0]
        self.assertEqual(ph_summary['actual_count'], 1)

        trends = client.get('/api/analysis/trends', headers=headers)
        self.assertEqual(trends.status_code, 200, trends.get_json())
        day_point = [item for item in trends.get_json()['arrival']
                     if item['date'] == local_day][0]
        self.assertIsNotNone(day_point['rate'])

        health = client.get(
            f'/api/data/health?period=custom&start={local_day}&end={local_day}',
            headers=headers,
        )
        self.assertEqual(health.status_code, 200, health.get_json())
        site = [item for item in health.get_json()['by_site']
                if item['site_id'] == self.site_id][0]
        self.assertEqual(site['actual'], 1, 'legacy sensor_data rows must not enter formal health')
        self.assertEqual(site['over_limit'], 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM monitoring_business_observations WHERE id=?',
                (observation_id,),
            ).fetchone()[0], 1)

    def test_two_digit_factor_keeps_formal_decimal_in_detail_projection(self):
        observed_at = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,business_metric,instrument_asset_code,
                       expected_interval_seconds,tolerance_seconds,effective_from)
                   VALUES (?,?,?,?,?,?,?)""",
                (self.endpoint_id, '4910', 'turbidity', 'INST-A', 14400, 600,
                 '2020-01-01T00:00:00+00:00'),
            )
            self._set_expected_factors(
                connection, ('ph', 'water_temp', 'turbidity', 'codmn', 'ammonia'))
            connection.commit()
        observation_id = self._insert_formal_observation(
            'turbidity', 7.34, observed_at.isoformat(), protocol_code='4910')

        client = web_app.app.test_client()
        response = client.get(
            f'/api/station-monitoring/sites/{self.site_id}/latest',
            headers=self._headers('monitor-admin'),
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        item = [row for row in response.get_json()['items']
                if row['business_metric'] == 'turbidity'][0]
        self.assertEqual(item['standard_value'], 7.34)
        self.assertEqual(item['decimal_precision'], 2)
        self.assertEqual(item['standard_value_display'], '7.34')
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                'SELECT standard_value FROM monitoring_business_observations WHERE id=?',
                (observation_id,),
            ).fetchone()[0], 7.34)

    def test_formal_review_and_alert_consumers_are_traceable_and_idempotent(self):
        base = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        normal_id = self._insert_formal_observation('ph', 7.00, base.isoformat())
        abnormal_id = self._insert_formal_observation(
            'ph', 15.00, (base + timedelta(hours=4)).isoformat())
        suspect_id = self._insert_formal_observation(
            'ph', 7.20, (base + timedelta(hours=8)).isoformat(), quality='suspect')
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "INSERT OR REPLACE INTO param_thresholds(metric,label,unit,low,high,critical_low,critical_high) "
                "VALUES ('ph','pH','',6,9,5,10)"
            )
            generated = web_app._generate_formal_data_reviews(
                connection, (base - timedelta(hours=1)).isoformat())
            self.assertEqual(generated['generated'], 2)
            self.assertEqual(web_app._generate_formal_data_reviews(
                connection, (base - timedelta(hours=1)).isoformat())['generated'], 0)
            abnormal_review = connection.execute(
                'SELECT * FROM data_reviews WHERE monitoring_business_observation_id=?',
                (abnormal_id,),
            ).fetchone()
            self.assertIsNotNone(abnormal_review)
            suspect_review = connection.execute(
                'SELECT * FROM data_reviews WHERE monitoring_business_observation_id=?',
                (suspect_id,),
            ).fetchone()
            self.assertIsNotNone(suspect_review)
            self.assertEqual(suspect_review['status'], 'smart_reviewed')
            self.assertIn('suspect', suspect_review['auto_reason'])
            self.assertIsNone(connection.execute(
                'SELECT id FROM data_reviews WHERE monitoring_business_observation_id=?',
                (normal_id,),
            ).fetchone())
            first_alerts = web_app._evaluate_formal_observation_alerts(
                connection, (base - timedelta(hours=1)).isoformat())
            second_alerts = web_app._evaluate_formal_observation_alerts(
                connection, (base - timedelta(hours=1)).isoformat())
            connection.commit()
            self.assertEqual(first_alerts, {'evaluated': 2, 'triggered': 1})
            self.assertEqual(second_alerts, {'evaluated': 0, 'triggered': 0})
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM monitoring_alert_observation_events'
            ).fetchone()[0], 2)
            self.assertIsNone(connection.execute(
                'SELECT observation_id FROM monitoring_alert_observation_events WHERE observation_id=?',
                (suspect_id,),
            ).fetchone())
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM alerts').fetchone()[0], 1)
            review_id = suspect_review['id']

        client = web_app.app.test_client()
        headers = self._headers('monitor-admin')
        listing = client.get('/api/data-reviews?per_page=50', headers=headers)
        self.assertEqual(listing.status_code, 200, listing.get_json())
        self.assertIn(review_id, [row['id'] for row in listing.get_json()['items']])
        detail = client.get(f'/api/data-reviews/{review_id}', headers=headers)
        self.assertEqual(detail.status_code, 200, detail.get_json())
        self.assertEqual(detail.get_json()['monitoring_business_observation_id'], suspect_id)
        decided = client.post(
            f'/api/data-reviews/{review_id}/manual-review', headers=headers,
            json={'action': 'approve', 'reason': '隔离测试确认'},
        )
        self.assertEqual(decided.status_code, 200, decided.get_json())
        reread = client.get(f'/api/data-reviews/{review_id}', headers=headers)
        self.assertEqual(reread.get_json()['status'], 'archived')
        self.assertEqual(reread.get_json()['manual_result'], 'approved')


if __name__ == '__main__':
    unittest.main()
