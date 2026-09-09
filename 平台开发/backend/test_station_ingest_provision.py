import io
import json
import os
import sqlite3
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_station_ingestion import apply_migration
from sl651_parser import (UP_FLOW_CONTROL, crc16_modbus, encode_bcd_observation_time, encode_bcd_time,
                          encode_station_code, parse_frame)
from sl651_server import IngestionStorage, credential_hmac
from station_ingest_provision import ProvisionError, execute, load_configuration, main
from station_monitoring import normalize_raw_frame


def bcd_number(value, digits, precision=0):
    encoded = f"{int(round(value * (10 ** precision))):0{digits}d}"
    if len(encoded) % 2:
        encoded = "0" + encoded
    return bytes((int(encoded[index]) << 4) | int(encoded[index + 1]) for index in range(0, len(encoded), 2))


def make_32h(payload, station_code="0012345678", password=b"\x12\x34", serial=1):
    sent_at = datetime(2020, 6, 12, 2, 0, 0)
    body = b"\xf1\xf1" + encode_station_code(station_code) + b"\x51\xf0\xf0" + encode_bcd_observation_time(sent_at) + payload
    content = serial.to_bytes(2, "big") + encode_bcd_time(sent_at) + body
    prefix = (b"\x7e\x7e\x10" + encode_station_code(station_code) + password + b"\x32"
              + len(content).to_bytes(2, "big") + b"\x02" + content + bytes((UP_FLOW_CONTROL,)))
    return prefix + crc16_modbus(prefix).to_bytes(2, "big")


class StationIngestProvisionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "isolated-provision.db"
        self.config_path = self.root / "private-station.json"
        self.pepper = "isolated-provision-pepper"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL)")
            connection.execute("INSERT INTO sites(id,code,name,type) VALUES (1,'PROVISION-1','isolated station','water')")
            connection.execute("INSERT INTO sites(id,code,name,type) VALUES (2,'PROVISION-2','other station','water')")
            connection.commit()
        apply_migration(self.database, self.root / "backups")

    def tearDown(self):
        self.temp_dir.cleanup()

    def configuration(self, **changes):
        value = {
            "station_code": "0012345678",
            "business_site_id": 1,
            "rtu_asset_code": "RTU-TEST-01",
            "instrument_asset_code": "INST-TEST-01",
            "timezone": "Asia/Shanghai",
            "effective_from": "2020-01-01T00:00:00+00:00",
            "expected_granularity": "realtime",
            "expected_interval_seconds": 60,
            "mappings": [{
                "protocol_code": "0311", "business_metric": "water_temp", "instrument_asset_code": "INST-TEST-01",
                "expected_interval_seconds": 60, "tolerance_seconds": 15,
            }],
        }
        value.update(changes)
        return value

    def write_configuration(self, value=None):
        self.config_path.write_text(json.dumps(value or self.configuration()), encoding="utf-8")
        return load_configuration(self.config_path)

    def counts(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "trusted_endpoints", "monitoring_endpoint_profiles", "monitoring_factor_mappings",
                "ingest_raw_frames", "observation_batches", "observation_values",
            ))

    def apply(self, configuration, credential=b"\x12\x34"):
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False):
            return execute("apply", self.database, configuration, credential=credential, offline_confirmed=True)

    def test_plan_and_verify_do_not_write_and_apply_is_idempotent(self):
        configuration = self.write_configuration()
        before = self.counts()
        self.assertEqual(execute("plan", self.database, configuration)["result"], "ready")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.apply(configuration)["result"], "applied")
        after_apply = self.counts()
        self.assertEqual(execute("verify", self.database, configuration)["result"], "verified")
        self.assertEqual(self.counts(), after_apply)
        self.assertEqual(self.apply(configuration)["result"], "applied")
        self.assertEqual(self.counts(), after_apply)

    def test_applied_configuration_is_consumed_by_runtime_authentication_and_normalization(self):
        configuration = self.write_configuration()
        self.apply(configuration)
        storage = IngestionStorage(self.database, self.pepper)
        frame = parse_frame(make_32h(bytes.fromhex("0311") + bcd_number(12.3, 3, 1)))
        authentication = storage.authenticate(frame)
        self.assertEqual(authentication.status, "authenticated")
        raw_id, duplicate = storage.persist_parsed(frame, authentication, "2026-09-09T00:00:00+00:00")
        self.assertFalse(duplicate)
        self.assertEqual(normalize_raw_frame(self.database, raw_id), "accepted")
        with closing(sqlite3.connect(self.database)) as connection:
            value = connection.execute("SELECT standard_value FROM observation_values").fetchone()[0]
        self.assertEqual(value, 12.3)

    def test_hex_stdin_credential_decodes_to_the_protocol_two_byte_password(self):
        self.write_configuration()
        output = io.StringIO()
        errors = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(b"1234\n"), encoding="utf-8")
        with (mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False),
              mock.patch.object(sys, "stdin", stdin), redirect_stdout(output), redirect_stderr(errors)):
            result = main([
                "apply", "--database", str(self.database), "--config", str(self.config_path),
                "--offline-confirmation", "--credential-stdin",
            ])
        self.assertEqual(result, 0, errors.getvalue())
        frame = parse_frame(make_32h(bytes.fromhex("0311") + bcd_number(8.8, 3, 1), password=b"\x12\x34"))
        self.assertEqual(IngestionStorage(self.database, self.pepper).authenticate(frame).status, "authenticated")

    def test_invalid_hex_stdin_credentials_have_zero_write_side_effect(self):
        self.write_configuration()
        before = self.counts()
        for credential in (b"", b"123", b"12345", b"12xz"):
            errors = io.StringIO()
            with self.subTest(credential=credential), mock.patch.object(
                sys, "stdin", io.TextIOWrapper(io.BytesIO(credential + b"\n"), encoding="utf-8")
            ), mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), redirect_stderr(errors):
                self.assertEqual(main([
                    "apply", "--database", str(self.database), "--config", str(self.config_path),
                    "--offline-confirmation", "--credential-stdin",
                ]), 2)
            if credential:
                self.assertNotIn(credential.decode("ascii"), errors.getvalue())
            self.assertEqual(self.counts(), before)

    def test_invalid_configuration_and_existing_conflicts_leave_batch_unchanged(self):
        configuration = self.write_configuration()
        initial = self.counts()
        missing_site = self.write_configuration(self.configuration(business_site_id=999))
        with self.assertRaisesRegex(ProvisionError, "business site"):
            self.apply(missing_site)
        self.assertEqual(self.counts(), initial)
        unknown_factor = self.write_configuration(self.configuration(mappings=[{"protocol_code": "FFFF"}]))
        with self.assertRaisesRegex(ProvisionError, "undefined protocol"):
            self.apply(unknown_factor)
        self.assertEqual(self.counts(), initial)
        metric_conflict = self.write_configuration(self.configuration(mappings=[{"protocol_code": "0311", "business_metric": "ph"}]))
        with self.assertRaisesRegex(ProvisionError, "business metric conflicts"):
            self.apply(metric_conflict)
        self.assertEqual(self.counts(), initial)
        unpublished_factor = self.write_configuration(self.configuration(mappings=[{"protocol_code": "4B19", "business_metric": "forced_metric"}]))
        with self.assertRaisesRegex(ProvisionError, "unpublished"):
            self.apply(unpublished_factor)
        self.assertEqual(self.counts(), initial)
        overlapping = self.write_configuration(self.configuration(mappings=[{"protocol_code": "0311"}, {"protocol_code": "0311"}]))
        with self.assertRaisesRegex(ProvisionError, "overlapping"):
            self.apply(overlapping)
        self.assertEqual(self.counts(), initial)
        self.config_path.write_text(json.dumps(self.configuration(expected_interval_seconds=0)), encoding="utf-8")
        with self.assertRaisesRegex(ProvisionError, "positive integer"):
            load_configuration(self.config_path)
        self.assertEqual(self.counts(), initial)
        self.config_path.write_text(json.dumps(self.configuration(effective_from="not-a-time")), encoding="utf-8")
        with self.assertRaisesRegex(ProvisionError, "ISO timestamp"):
            load_configuration(self.config_path)
        self.assertEqual(self.counts(), initial)
        self.apply(configuration)
        applied = self.counts()
        conflicting_period = self.write_configuration(self.configuration(mappings=[{
            "protocol_code": "0311", "effective_from": "2020-06-01T00:00:00+00:00",
        }]))
        with self.assertRaisesRegex(ProvisionError, "overlaps an existing"):
            self.apply(conflicting_period)
        self.assertEqual(self.counts(), applied)
        with self.assertRaisesRegex(ProvisionError, "different credentials"):
            self.apply(configuration, b"different-credential")
        self.assertEqual(self.counts(), applied)
        other_site = self.write_configuration(self.configuration(business_site_id=2))
        with self.assertRaisesRegex(ProvisionError, "another business site"):
            self.apply(other_site)
        self.assertEqual(self.counts(), applied)

    def test_authoritative_asset_ownership_is_checked_when_asset_records_exist(self):
        configuration = self.write_configuration()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE authoritative_assets (asset_code TEXT PRIMARY KEY, site_id INTEGER NOT NULL)")
            connection.execute("INSERT INTO authoritative_assets(asset_code,site_id) VALUES ('INST-TEST-01',2)")
            connection.commit()
        before = self.counts()
        with self.assertRaisesRegex(ProvisionError, "belongs to another business site"):
            self.apply(configuration)
        self.assertEqual(self.counts(), before)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE authoritative_assets SET site_id=1 WHERE asset_code='INST-TEST-01'")
            connection.commit()
        self.assertEqual(self.apply(configuration)["result"], "applied")

    def test_disable_requires_confirmation_preserves_history_and_is_idempotent(self):
        configuration = self.write_configuration()
        self.apply(configuration)
        storage = IngestionStorage(self.database, self.pepper)
        frame = parse_frame(make_32h(bytes.fromhex("0311") + bcd_number(7.7, 3, 1), serial=2))
        raw_id, duplicate = storage.persist_parsed(frame, storage.authenticate(frame), "2026-09-09T00:00:00+00:00")
        self.assertFalse(duplicate)
        self.assertEqual(normalize_raw_frame(self.database, raw_id), "accepted")
        before_disable = self.counts()
        with self.assertRaisesRegex(ProvisionError, "confirmation"):
            execute("disable", self.database, configuration, offline_confirmed=True)
        self.assertEqual(self.counts(), before_disable)
        self.assertEqual(execute("disable", self.database, configuration, offline_confirmed=True,
                                 disable_confirmed=True, reason="isolated disable test")["result"], "disabled")
        self.assertEqual(self.counts(), before_disable)
        self.assertEqual(storage.authenticate(frame).status, "unknown_endpoint")
        self.assertEqual(execute("plan", self.database, configuration)["result"], "unavailable")
        with self.assertRaisesRegex(ProvisionError, "disabled and unavailable"):
            execute("verify", self.database, configuration)
        self.assertEqual(execute("disable", self.database, configuration, offline_confirmed=True,
                                 disable_confirmed=True, reason="repeat")["result"], "disabled")
        self.assertEqual(self.counts(), before_disable)

    def test_verify_rejects_an_invalid_credential_summary_without_writing(self):
        configuration = self.write_configuration()
        self.apply(configuration)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE trusted_endpoints SET credential_hmac='invalid-summary'")
            connection.commit()
        before_verify = self.counts()
        with self.assertRaisesRegex(ProvisionError, "credential summary"):
            execute("verify", self.database, configuration)
        self.assertEqual(self.counts(), before_verify)

    def test_all_actions_reject_missing_or_directory_database_paths_without_creating_them(self):
        configuration = self.write_configuration()
        missing = self.root / "not-created" / "station.db"
        directory = self.root / "database-directory"
        directory.mkdir()
        actions = (
            ("plan", {}),
            ("verify", {}),
            ("apply", {"credential": b"\x12\x34", "offline_confirmed": True}),
            ("disable", {"offline_confirmed": True, "disable_confirmed": True, "reason": "isolated"}),
        )
        for database in (missing, directory):
            for action, arguments in actions:
                with self.subTest(database=database.name, action=action):
                    with self.assertRaisesRegex(ProvisionError, "existing database file"):
                        execute(action, database, configuration, **arguments)
                    self.assertFalse(missing.exists())

    def test_cli_output_and_errors_do_not_leak_station_or_credentials(self):
        self.write_configuration()
        credential_hex = b"1234"
        credential = bytes.fromhex(credential_hex.decode("ascii"))
        output = io.StringIO()
        errors = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(credential_hex + b"\n"), encoding="utf-8")
        with (mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False),
              mock.patch.object(sys, "stdin", stdin), redirect_stdout(output), redirect_stderr(errors)):
            result = main([
                "apply", "--database", str(self.database), "--config", str(self.config_path),
                "--offline-confirmation", "--credential-stdin",
            ])
        self.assertEqual(result, 0, errors.getvalue())
        text = output.getvalue() + errors.getvalue()
        self.assertNotIn("0012345678", text)
        self.assertNotIn(credential_hex.decode("ascii"), text)
        self.assertNotIn(self.pepper, text)
        self.assertNotIn(credential_hmac(credential, self.pepper), text)
        self.assertEqual(json.loads(output.getvalue())["result"], "applied")


if __name__ == "__main__":
    unittest.main()
