import io
import json
import os
import sqlite3
from contextlib import closing, redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_station_ingestion import apply_migration
from sl651_parser import crc16_modbus
from sl651_server import IngestionStorage, StationIngestServer, StorageError, credential_hmac
import station_ingest_discovery as discovery
from station_ingest_provision import ProvisionError, main as provision_main, preview_station_code_identity_import


def write_workbook(path: Path, rows):
    sheet_rows = []
    for number, (name, code) in enumerate(rows, 1):
        cells = []
        for column, value in (("B", name), ("C", code)):
            if value is not None:
                cells.append(f'<c r="{column}{number}" t="inlineStr"><is><t>{value}</t></is></c>')
        sheet_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData>' + "".join(sheet_rows) + "</sheetData></worksheet>")


def make_hj212(*, station="IDENTITY-MN", password="synthetic-password", qn="20260911164900001"):
    cp = "DataTime=20260911164900;w01001-Rtd=7.0;w01001-Flag=N;005-Rtd=3.0;005-Flag=N"
    body = f"QN={qn};ST=91;CN=2011;PW={password};MN={station};CP=&&{cp}&&".encode("ascii")
    return b"##" + f"{len(body):04d}".encode("ascii") + body + f"{crc16_modbus(body):04X}".encode("ascii") + b"\r\n"


class StationIdentityAndDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "isolated.db"
        self.pepper = "isolated-identity-pepper"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            for number in range(1, 44):
                connection.execute("INSERT INTO sites VALUES (?,?)", (number, f"station-{number}"))
            connection.commit()
        apply_migration(self.database, self.root / "backups")
        self.storage = IngestionStorage(self.database, self.pepper)
        self.server = StationIngestServer(self.storage, queue_max_frames=32, error_queue_max=8)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def formal_rows(self):
        rows = [("station name", "MN")]
        rows.extend((f"station-{number}", f"SYNTH-MN-{number:08d}") for number in range(1, 44))
        rows.append(("ignored station", None))
        return rows

    def endpoint_rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(
                "SELECT station_code,business_site_id,enabled,endpoint_state FROM trusted_endpoints ORDER BY station_code"
            ).fetchall()

    async def test_identity_cli_is_template_free_atomic_and_idempotent(self):
        workbook = self.root / "formal-identities.xlsx"
        write_workbook(workbook, self.formal_rows())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,1,'bound')",
                ("OLD-SYNTHETIC-MN", credential_hmac(b"synthetic-password", self.pepper)),
            )
            connection.commit()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(provision_main([
                "station-identities-plan", "--database", str(self.database), "--workbook", str(workbook),
            ]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual((plan["accepted_rows"], plan["ignored_rows"], plan["conflict_categories"]), (43, 1, []))
        self.assertNotIn("station-1", output.getvalue())
        self.assertNotIn("SYNTH-MN", output.getvalue())
        arguments = [
            "--database", str(self.database), "--workbook", str(workbook), "--offline-confirmation", "--credential-stdin",
            "--expected-fingerprint", plan["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
        ]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *arguments]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM trusted_endpoints WHERE enabled=1").fetchone()[0], 43)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM monitoring_endpoint_profiles").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM monitoring_factor_mappings").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT enabled FROM trusted_endpoints WHERE station_code='OLD-SYNTHETIC-MN'").fetchone()[0], 0)
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-verify", *arguments]), 0)
        stdin = io.TextIOWrapper(io.BytesIO(b"wrong-synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-verify", *arguments]), 2)
        before = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *arguments]), 0)
        self.assertEqual(self.endpoint_rows(), before)

    async def test_identity_rejects_changed_workbook_disabled_target_and_bad_credential_without_writes(self):
        workbook = self.root / "formal-identities.xlsx"
        write_workbook(workbook, self.formal_rows())
        preview = preview_station_code_identity_import(self.database, workbook)
        self.assertEqual(preview["errors"], [])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(provision_main(["station-identities-plan", "--database", str(self.database), "--workbook", str(workbook)]), 0)
        plan = json.loads(output.getvalue())
        changed_rows = self.formal_rows()
        changed_rows[1] = ("station-1", "CHANGED-SYNTH-MN")
        changed = self.root / "changed-identities.xlsx"
        write_workbook(changed, changed_rows)
        before = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main([
                "station-identities-apply", "--database", str(self.database), "--workbook", str(changed),
                "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", plan["fingerprint"],
                "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
            ]), 2)
        self.assertEqual(self.endpoint_rows(), before)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,enabled,endpoint_state) VALUES (?,?,1,0,'disabled')",
                ("SYNTH-MN-00000001", credential_hmac(b"synthetic-password", self.pepper)),
            )
            connection.commit()
        disabled = preview_station_code_identity_import(self.database, workbook)
        self.assertIn("disabled_station_code", disabled["errors"])
        before = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main([
                "station-identities-apply", "--database", str(self.database), "--workbook", str(workbook),
                "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", plan["fingerprint"],
                "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
            ]), 2)
        self.assertEqual(self.endpoint_rows(), before)

    async def test_unprofiled_hj212_receipts_are_retained_discovered_and_replayed_only_on_request(self):
        password = b"synthetic-password"
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint_id = connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,1,'bound')",
                ("IDENTITY-MN", credential_hmac(password, self.pepper)),
            ).lastrowid
            connection.commit()
        first = self.server._process_raw_result(make_hj212(), "2026-09-11T08:49:05+00:00")
        second = self.server._process_raw_result(make_hj212(qn="20260911165000002"), "2026-09-11T08:50:05+00:00")
        self.assertTrue(first.keep_connection)
        self.assertTrue(second.keep_connection)
        with closing(sqlite3.connect(self.database)) as connection:
            raw_ids = [row[0] for row in connection.execute("SELECT id FROM ingest_raw_frames ORDER BY id")]
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_errors WHERE error_type='configuration_pending'").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_raw_frames WHERE persistence_state='pending_parse'").fetchone()[0], 0)
            connection.execute(
                "INSERT INTO ingest_parse_attempts(raw_frame_id,parser_version,parse_status) VALUES (?,'extra-diagnostic','parsed_header')",
                (raw_ids[0],),
            )
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,2,'bound')",
                ("CREDENTIAL-FAIL-MN", credential_hmac(b"correct-password", self.pepper)),
            )
            connection.commit()
        self.server._process_raw_result(make_hj212(station="UNKNOWN-MN-A"), "2026-09-11T08:51:05+00:00")
        self.server._process_raw_result(make_hj212(station="UNKNOWN-MN-B"), "2026-09-11T08:52:05+00:00")
        self.server._process_raw_result(
            make_hj212(station="CREDENTIAL-FAIL-MN", password="wrong-password"), "2026-09-11T08:53:05+00:00",
        )
        report_path = self.root / "restricted" / "hj212-discovery.json"
        report_path.parent.mkdir()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(discovery.main([
                "discover", "--database", str(self.database), "--start-id", str(raw_ids[0]), "--end-id", str(raw_ids[-1]),
                "--output", str(report_path),
            ]), 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual((summary["station_count"], summary["raw_frame_count"]), (1, 2))
        self.assertNotIn("IDENTITY-MN", stdout.getvalue())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        station = report["stations"][0]
        self.assertEqual(station["valid_cn2011_count"], 2)
        self.assertEqual(station["authentication_results"], {"authenticated": 2})
        self.assertEqual(station["parse_results"], {"valid_cn2011": 2})
        self.assertEqual(station["protocol_codes"], ["HJ212:005", "HJ212:w01001"])
        self.assertEqual(station["quality_flags"], {"valid": 2})
        self.assertEqual(station["observed_receive_intervals_seconds"]["minimum"], 60)
        self.assertNotIn("7.0", report_path.read_text(encoding="utf-8"))
        all_report, all_summary = discovery.discover_hj212(self.database, start_id=raw_ids[0], end_id=raw_ids[-1] + 3)
        self.assertEqual(all_summary["raw_frame_count"], 5)
        grouped = {item["station_code"]: item for item in all_report["stations"]}
        self.assertEqual(grouped["IDENTITY-MN"]["raw_frame_count"], 2)
        self.assertEqual(grouped["IDENTITY-MN"]["observed_receive_intervals_seconds"]["minimum"], 60)
        self.assertEqual(grouped["UNKNOWN-MN-A"]["authentication_results"], {"unknown_endpoint": 1})
        self.assertEqual(grouped["UNKNOWN-MN-B"]["authentication_results"], {"unknown_endpoint": 1})
        self.assertEqual(grouped["CREDENTIAL-FAIL-MN"]["authentication_results"], {"credential_failed": 1})
        with self.assertRaises(discovery.DiscoveryError):
            discovery.write_restricted_report(report, report_path)
        with self.assertRaises(discovery.DiscoveryError):
            discovery.write_restricted_report(report, Path(__file__).resolve().parent / "must-not-write.json")
        with closing(sqlite3.connect(self.database)) as connection:
            before_profile_replay = (
                connection.execute("SELECT id,disposition,persistence_state FROM ingest_raw_frames ORDER BY id").fetchall(),
                connection.execute("SELECT COUNT(*) FROM monitoring_quality_issues").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM monitoring_status_events").fetchone()[0],
            )
        with self.assertRaisesRegex(discovery.DiscoveryError, "no active monitoring profile"):
            discovery.replay_hj212(self.database, endpoint_id=endpoint_id, start_id=raw_ids[0], end_id=raw_ids[-1])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(before_profile_replay, (
                connection.execute("SELECT id,disposition,persistence_state FROM ingest_raw_frames ORDER BY id").fetchall(),
                connection.execute("SELECT COUNT(*) FROM monitoring_quality_issues").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM monitoring_status_events").fetchone()[0],
            ))
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                   expected_interval_seconds,effective_from) VALUES (?,1,'Asia/Shanghai',1,'realtime',60,'2020-01-01T00:00:00+00:00')""",
                (endpoint_id,),
            )
            connection.commit()
        with closing(sqlite3.connect(self.database)) as connection:
            before_mapping_replay = (
                connection.execute("SELECT id,disposition,persistence_state FROM ingest_raw_frames ORDER BY id").fetchall(),
                connection.execute("SELECT COUNT(*) FROM monitoring_quality_issues").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM monitoring_status_events").fetchone()[0],
            )
        with self.assertRaisesRegex(discovery.DiscoveryError, "no effective factor mapping"):
            discovery.replay_hj212(self.database, endpoint_id=endpoint_id, start_id=raw_ids[0], end_id=raw_ids[-1])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(before_mapping_replay, (
                connection.execute("SELECT id,disposition,persistence_state FROM ingest_raw_frames ORDER BY id").fetchall(),
                connection.execute("SELECT COUNT(*) FROM monitoring_quality_issues").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM monitoring_status_events").fetchone()[0],
            ))
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,expected_interval_seconds,
                   tolerance_seconds,effective_from,enabled) VALUES (?, 'HJ212:w01001','ph',60,0,'2020-01-01T00:00:00+00:00',1)""",
                (endpoint_id,),
            )
            connection.commit()
        with mock.patch.object(discovery, "normalize_raw_frame", side_effect=sqlite3.OperationalError("locked")):
            failed = discovery.replay_hj212(self.database, endpoint_id=endpoint_id, start_id=raw_ids[0], end_id=raw_ids[-1])
        self.assertEqual(failed["result"], "attention_required")
        replayed = discovery.replay_hj212(self.database, endpoint_id=endpoint_id, start_id=raw_ids[0], end_id=raw_ids[-1])
        self.assertEqual(replayed["result"], "replayed")
        repeated = discovery.replay_hj212(self.database, endpoint_id=endpoint_id, start_id=raw_ids[0], end_id=raw_ids[-1])
        self.assertEqual(repeated["outcomes"], {"already_normalized": 2})
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observation_values WHERE is_published=1").fetchone()[0], 2)
        with mock.patch.object(self.storage, "persist_parsed", side_effect=StorageError("capacity")):
            with self.assertRaises(StorageError):
                self.server._process_raw_result(make_hj212(qn="20260911165100003"), "2026-09-11T08:51:05+00:00")


if __name__ == "__main__":
    unittest.main()
