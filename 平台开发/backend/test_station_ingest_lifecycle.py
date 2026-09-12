import hashlib
import io
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from zipfile import ZipFile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_station_ingestion import apply_migration
from sl651_server import credential_hmac
from station_ingest_provision import ProvisionError, apply_station_code_import, main as provision_main, preview_station_code_import
import station_ingest_retention as retention
from station_ingest_retention import RetentionError, StoragePolicy, aggregate_hourly, archive_raw_day, storage_policy_for_mode


def write_workbook(path: Path, rows):
    cells = []
    for number, (name, code) in enumerate(rows, 1):
        parts = []
        for column, value in (("B", name), ("C", code)):
            if value is not None:
                parts.append(f'<c r="{column}{number}" t="inlineStr"><is><t>{value}</t></is></c>')
        cells.append(f'<row r="{number}">{"".join(parts)}</row>')
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData>' + "".join(cells) + '</sheetData></worksheet>')


class StationIngestLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "isolated.db"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            connection.execute("INSERT INTO sites VALUES (1,'alpha station'),(2,'beta station')")
            connection.commit()
        apply_migration(self.database, self.root / "backups")
        self.pepper = "isolated-pepper"
        self.template = {
            "station_code": "BULKTEMPLATE", "business_site_id": 1, "timezone": "Asia/Shanghai",
            "effective_from": "2020-01-01T00:00:00+00:00", "expected_granularity": "realtime",
            "expected_interval_seconds": 60,
            "mappings": [{"protocol_code": "HJ212:w01001", "business_metric": "ph", "instrument_asset_code": None,
                          "expected_interval_seconds": 60, "tolerance_seconds": 0,
                          "effective_from": "2020-01-01T00:00:00+00:00", "effective_to": None}],
            "rtu_asset_code": None, "instrument_asset_code": None, "effective_to": None,
        }

    def tearDown(self):
        self.temp.cleanup()

    def endpoints(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute("SELECT station_code,business_site_id,enabled,endpoint_state FROM trusted_endpoints ORDER BY station_code").fetchall()

    def test_station_code_import_rejects_non_formal_candidate_count_without_writes(self):
        workbook = self.root / "codes.xlsx"
        write_workbook(workbook, [("station name", "MN"), ("alpha station", "ORIGINAL-MN-LONG"), ("beta station", "BETA-MN"), ("ignored", None)])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,1,'bound')", ("SELF-CODE", credential_hmac(b'\x12\x34', self.pepper)))
            connection.commit()
        preview = preview_station_code_import(self.database, workbook)
        self.assertEqual((preview["accepted_rows"], preview["ignored_rows"]), (2, 1))
        self.assertIn("unexpected_record_count", preview["errors"])
        before = self.endpoints()
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), self.assertRaisesRegex(ProvisionError, "formal preview"):
            apply_station_code_import(self.database, workbook, credential=b'text-password', offline_confirmed=True, template=self.template)
        self.assertEqual(self.endpoints(), before)

    def test_station_code_conflict_rejects_whole_batch_without_writes(self):
        workbook = self.root / "conflict.xlsx"
        write_workbook(workbook, [("station name", "MN"), ("alpha station", "DUP"), ("beta station", "DUP")])
        before = self.endpoints()
        preview = preview_station_code_import(self.database, workbook)
        self.assertIn("duplicate_station_code", preview["errors"])
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), self.assertRaisesRegex(ProvisionError, "validation failed"):
            apply_station_code_import(self.database, workbook, credential=b'text-password', offline_confirmed=True, template=self.template,
                                      expected_fingerprint="sha256:test", expected_accepted_rows=43, expected_ignored_rows=1)
        self.assertEqual(self.endpoints(), before)

    def test_station_code_cli_handles_43_text_mn_records_with_template_and_safe_output(self):
        workbook = self.root / "codes-43.xlsx"
        template = self.root / "hj212-template.json"
        with closing(sqlite3.connect(self.database)) as connection:
            for number in range(3, 44):
                connection.execute("INSERT INTO sites(id,name) VALUES (?,?)", (number, f"station-{number}"))
            connection.commit()
        rows = [("station name", "MN"), ("alpha station", "MN-ALPHA-00000001"), ("beta station", "MN-BETA-00000002")]
        rows.extend((f"station-{number}", f"MN-SYNTH-{number:08d}") for number in range(3, 44))
        rows.append(("ignored", None))
        write_workbook(workbook, rows)
        template.write_text(json.dumps({
            "timezone": "Asia/Shanghai", "effective_from": "2020-01-01T00:00:00+00:00",
            "expected_interval_seconds": 60,
            "mappings": [{"protocol_code": "HJ212:w01001"}],
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(provision_main(["station-codes-plan", "--database", str(self.database), "--workbook", str(workbook), "--template", str(template)]), 0)
        planned = json.loads(output.getvalue())
        self.assertEqual((planned["accepted_rows"], planned["ignored_rows"], planned["conflict_categories"]), (43, 1, []))
        self.assertNotIn("alpha station", output.getvalue())
        self.assertNotIn("MN-ALPHA", output.getvalue())
        output = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(b"long-ascii-hj212-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin), redirect_stdout(output):
            self.assertEqual(provision_main(["station-codes-apply", "--database", str(self.database), "--workbook", str(workbook), "--template", str(template), "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", planned["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1"]), 0)
        self.assertNotIn("long-ascii-hj212-password", output.getvalue())
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM trusted_endpoints WHERE enabled=1").fetchone()[0], 43)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM monitoring_endpoint_profiles").fetchone()[0], 43)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM monitoring_factor_mappings").fetchone()[0], 43)
        output = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(b"long-ascii-hj212-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin), redirect_stdout(output):
            self.assertEqual(provision_main(["station-codes-verify", "--database", str(self.database), "--workbook", str(workbook), "--template", str(template), "--credential-stdin", "--expected-fingerprint", planned["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1"]), 0)
        self.assertEqual(json.loads(output.getvalue())["result"], "verified")
        changed = self.root / "codes-43-changed.xlsx"
        changed_rows = list(rows)
        changed_rows[1] = ("alpha station", "MN-ALPHA-CHANGED")
        write_workbook(changed, changed_rows)
        changed_output = io.StringIO()
        with redirect_stdout(changed_output):
            self.assertEqual(provision_main(["station-codes-plan", "--database", str(self.database), "--workbook", str(changed), "--template", str(template)]), 0)
        self.assertNotEqual(json.loads(changed_output.getvalue())["fingerprint"], planned["fingerprint"])
        stdin = io.TextIOWrapper(io.BytesIO(b"long-ascii-hj212-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-codes-apply", "--database", str(self.database), "--workbook", str(changed), "--template", str(template), "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", planned["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1"]), 2)
        stdin = io.TextIOWrapper(io.BytesIO(b"different-private-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-codes-verify", "--database", str(self.database), "--workbook", str(workbook), "--template", str(template), "--credential-stdin", "--expected-fingerprint", planned["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1"]), 2)

    def test_archive_retains_reference_and_low_space_is_visible(self):
        raw = b"synthetic raw evidence"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("""INSERT INTO ingest_raw_frames(received_at,frame_sha256,raw_frame,crc_status,authentication_status,disposition,persistence_state)
                                VALUES ('2026-09-01T12:00:00+00:00',?,?, 'valid','authenticated','accepted','persisted')""", (hashlib.sha256(raw).hexdigest(), raw))
            connection.commit()
        policy = storage_policy_for_mode(self.root, "validation")
        self.assertEqual(archive_raw_day(self.database, "2026-09-01", policy), 1)
        self.assertEqual(archive_raw_day(self.database, "2026-09-01", policy), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT length(raw_frame) FROM ingest_raw_frames").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM monitoring_raw_archive_part_frames").fetchone()[0], 1)
        with self.assertRaises(RetentionError):
            archive_raw_day(self.database, "2026-09-02", StoragePolicy(self.root, minimum_total_bytes=10**18, minimum_free_bytes=10 * 1024 ** 3))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT status FROM monitoring_storage_health WHERE storage_key='raw_archive'").fetchone()[0], "degraded")

    def test_validation_storage_mode_is_explicit_and_long_term_mode_keeps_100_gib_floor(self):
        with self.assertRaisesRegex(RetentionError, "100 GiB"):
            StoragePolicy(self.root, minimum_total_bytes=1, minimum_free_bytes=1)
        with self.assertRaisesRegex(RetentionError, "20 GiB"):
            StoragePolicy(self.root, minimum_total_bytes=1, minimum_free_bytes=1, mode="validation")
        validation = storage_policy_for_mode(self.root, "validation")
        self.assertEqual(validation.mode, "validation")

    def test_archive_late_frames_and_file_then_database_failure_are_retry_safe(self):
        first = b"first synthetic raw evidence"
        late = b"late synthetic raw evidence"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO ingest_raw_frames(received_at,frame_sha256,raw_frame,crc_status,authentication_status,disposition,persistence_state) VALUES ('2026-09-01T12:00:00+00:00',?,?, 'valid','authenticated','accepted','persisted')", (hashlib.sha256(first).hexdigest(), first))
            connection.commit()
        policy = storage_policy_for_mode(self.root, "validation")
        self.assertEqual(archive_raw_day(self.database, "2026-09-01", policy), 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO ingest_raw_frames(received_at,frame_sha256,raw_frame,crc_status,authentication_status,disposition,persistence_state) VALUES ('2026-09-01T12:01:00+00:00',?,?, 'valid','authenticated','accepted','persisted')", (hashlib.sha256(late).hexdigest(), late))
            connection.commit()
        original = retention._write_and_verify_archive
        with mock.patch.object(retention, "_write_and_verify_archive", side_effect=lambda *args: (original(*args), (_ for _ in ()).throw(RetentionError("injected after file")))):
            with self.assertRaises(RetentionError):
                archive_raw_day(self.database, "2026-09-01", policy)
        self.assertEqual(archive_raw_day(self.database, "2026-09-01", policy), 1)
        with closing(sqlite3.connect(self.database)) as connection:
            parts = connection.execute("SELECT archive_path,content_sha256,frame_count FROM monitoring_raw_archive_parts ORDER BY id").fetchall()
            self.assertEqual(len(parts), 2)
            self.assertNotEqual(parts[0][0], parts[1][0])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM monitoring_raw_archive_part_frames").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE length(raw_frame)>0").fetchone()[0], 0)

    def test_hourly_aggregation_is_idempotent_and_excludes_fault_values(self):
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint = connection.execute("INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES ('AGG-MN','x',1,'bound')").lastrowid
            for index, (value, quality) in enumerate(((10.0, "valid"), (14.0, "suspect"), (99.0, "fault")), 1):
                raw_id = connection.execute("""INSERT INTO ingest_raw_frames(endpoint_id,station_code,received_at,frame_sha256,raw_frame,crc_status,authentication_status,disposition,persistence_state)
                                               VALUES (?, 'AGG-MN', ?, ?, X'01', 'valid','authenticated','accepted','persisted')""",
                                           (endpoint, f"2026-09-01T10:0{index}:00+00:00", f"hash-{index}")).lastrowid
                batch_id = connection.execute("""INSERT INTO observation_batches(raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,reported_at,observed_at,received_at,granularity,aggregation_source,idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                                               VALUES (?,?,1,32,?, ?, ?, ?, 'realtime','device_reported',?,'test','accepted','completed',?)""",
                                              (raw_id, endpoint, index, f"2026-09-01T10:0{index}:00+00:00", f"2026-09-01T10:0{index}:00+00:00", f"2026-09-01T10:0{index}:00+00:00", f"key-{index}", f"2026-09-01T10:0{index}:00+00:00")).lastrowid
                connection.execute("""INSERT INTO observation_values(observation_batch_id,protocol_code,business_metric,standard_value,standard_unit,quality,parser_version,is_published)
                                      VALUES (?, '0311','water_temp',?,'degC',?,?,1)""", (batch_id, value, quality, "test"))
            connection.commit()
        self.assertEqual(aggregate_hourly(self.database, "2026-09-01"), 1)
        self.assertEqual(aggregate_hourly(self.database, "2026-09-01"), 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT sample_count,minimum_value,maximum_value,average_value FROM monitoring_hourly_value_series").fetchone(), (2, 10.0, 14.0, 12.0))
            endpoint = connection.execute("SELECT id FROM trusted_endpoints WHERE station_code='AGG-MN'").fetchone()[0]
            raw_id = connection.execute("INSERT INTO ingest_raw_frames(endpoint_id,station_code,received_at,frame_sha256,raw_frame,crc_status,authentication_status,disposition,persistence_state) VALUES (?, 'AGG-MN','2026-09-01T10:30:00+00:00','mapping-change',X'01','valid','authenticated','accepted','persisted')", (endpoint,)).lastrowid
            batch_id = connection.execute("INSERT INTO observation_batches(raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,reported_at,observed_at,received_at,granularity,aggregation_source,idempotency_key,normalization_version,batch_status,projection_state,normalized_at) VALUES (?,?,1,32,9,'2026-09-01T10:30:00+00:00','2026-09-01T10:30:00+00:00','2026-09-01T10:30:00+00:00','realtime','device_reported','mapping-change','test','accepted','completed','2026-09-01T10:30:00+00:00')", (raw_id, endpoint)).lastrowid
            connection.execute("INSERT INTO observation_values(observation_batch_id,protocol_code,business_metric,standard_value,standard_unit,quality,parser_version,is_published) VALUES (?, '0311','water_temp_revised',300.0,'K','valid','test',1)", (batch_id,))
            connection.commit()
        self.assertEqual(aggregate_hourly(self.database, "2026-09-01"), 2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM monitoring_hourly_value_series").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
