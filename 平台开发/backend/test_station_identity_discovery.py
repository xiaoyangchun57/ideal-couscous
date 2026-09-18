import io
import hashlib
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
from hj212_parser import hj212_crc
from sl651_server import IngestionStorage, StationIngestServer, StorageError, credential_hmac
import station_ingest_discovery as discovery
import station_ingest_provision as provision
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
    return b"##" + f"{len(body):04d}".encode("ascii") + body + hj212_crc(body).encode("ascii") + b"\r\n"


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
        approval = mock.patch.dict(os.environ, {"STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT": self.source_fingerprint(self.formal_rows())})
        approval.start()
        self.addCleanup(approval.stop)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def formal_rows(self):
        rows = [("station name", "MN")]
        rows.extend((f"station-{number}", f"SYNTH-MN-{number:08d}") for number in range(1, 44))
        rows.append(("ignored station", None))
        return rows

    def mixed_formal_rows(self):
        rows = [("station name", "MN")]
        rows.extend((f"station-{number}", f"SYNTH-MN-{number:08d}") for number in range(1, 31))
        rows.extend((f"future-station-{number}", f"SYNTH-MN-{number:08d}") for number in range(31, 44))
        rows.append(("ignored station", None))
        return rows

    def expanded_formal_rows(self):
        rows = self.formal_rows()
        rows.insert(-1, ("future-approved-station", "SYNTH-MN-00000044"))
        return rows

    @staticmethod
    def source_fingerprint(rows):
        candidates = [{"station_name": name, "station_code": code} for name, code in rows
                      if name != "station name" and name and code]
        encoded = json.dumps({"candidates": sorted(candidates, key=lambda item: (item["station_name"], item["station_code"]))},
                             ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()[:16]

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
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,?, 'unbound')",
                ("UNRELATED-SYNTHETIC-MN", credential_hmac(b"synthetic-password", self.pepper), None),
            )
            connection.commit()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(provision_main([
                "station-identities-plan", "--database", str(self.database), "--workbook", str(workbook),
            ]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(
            (plan["accepted_rows"], plan["ignored_rows"], plan["retired_rows"], plan["source_status"], plan["conflict_categories"]),
            (43, 1, 1, "ready", []),
        )
        self.assertNotIn("station-1", output.getvalue())
        self.assertNotIn("SYNTH-MN", output.getvalue())
        arguments = [
            "--database", str(self.database), "--workbook", str(workbook), "--offline-confirmation", "--credential-stdin",
            "--confirm-disable", "--expected-fingerprint", plan["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
        ]
        no_retire_confirmation = [value for value in arguments if value != "--confirm-disable"]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *no_retire_confirmation]), 2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT enabled FROM trusted_endpoints WHERE station_code='OLD-SYNTHETIC-MN'").fetchone()[0], 1)
        before_wrong_credential = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"wrong-synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *arguments]), 2)
        self.assertEqual(self.endpoint_rows(), before_wrong_credential)
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *arguments]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM trusted_endpoints WHERE enabled=1").fetchone()[0], 44)
            self.assertEqual(connection.execute("SELECT endpoint_state FROM trusted_endpoints WHERE station_code='OLD-SYNTHETIC-MN'").fetchone()[0], "disabled")
            self.assertEqual(connection.execute("SELECT endpoint_state FROM trusted_endpoints WHERE station_code='UNRELATED-SYNTHETIC-MN'").fetchone()[0], "unbound")
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

    async def test_mixed_identity_import_binds_known_sites_and_retains_new_sites_until_explicit_binding(self):
        os.environ["STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT"] = self.source_fingerprint(self.mixed_formal_rows())
        workbook = self.root / "mixed-identities.xlsx"
        write_workbook(workbook, self.mixed_formal_rows())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM sites WHERE id>30")
            connection.commit()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(provision_main([
                "station-identities-plan", "--database", str(self.database), "--workbook", str(workbook),
            ]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(
            (plan["accepted_rows"], plan["ignored_rows"], plan["bound_rows"], plan["unbound_rows"], plan["conflict_categories"]),
            (43, 1, 30, 13, []),
        )
        self.assertNotIn("future-station", output.getvalue())
        arguments = [
            "--database", str(self.database), "--workbook", str(workbook), "--offline-confirmation", "--credential-stdin",
            "--expected-fingerprint", plan["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
        ]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *arguments]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM trusted_endpoints WHERE endpoint_state='bound'").fetchone()[0], 30)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM trusted_endpoints WHERE endpoint_state='unbound' AND business_site_id IS NULL").fetchone()[0], 13)
            unbound_id = connection.execute(
                "SELECT id FROM trusted_endpoints WHERE station_code='SYNTH-MN-00000031'"
            ).fetchone()[0]
            next_unbound_id = connection.execute(
                "SELECT id FROM trusted_endpoints WHERE station_code='SYNTH-MN-00000032'"
            ).fetchone()[0]
        first = self.server._process_raw_result(
            make_hj212(station="SYNTH-MN-00000031"), "2026-09-11T08:49:05+00:00",
        )
        second = self.server._process_raw_result(
            make_hj212(station="SYNTH-MN-00000031", qn="20260911165000002"), "2026-09-11T08:50:05+00:00",
        )
        self.assertTrue(first.keep_connection)
        self.assertTrue(second.keep_connection)
        with closing(sqlite3.connect(self.database)) as connection:
            raw_ids = [row[0] for row in connection.execute(
                "SELECT id FROM ingest_raw_frames WHERE endpoint_id=? ORDER BY id", (unbound_id,)
            )]
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM observation_batches WHERE endpoint_id=?", (unbound_id,)
            ).fetchone()[0], 0)
        discovered, discovery_summary = discovery.discover_hj212(
            self.database, start_id=raw_ids[0], end_id=raw_ids[-1],
        )
        self.assertEqual(discovery_summary["raw_frame_count"], 2)
        self.assertEqual(discovered["stations"][0]["authentication_results"], {"unbound_authenticated": 2})
        with self.assertRaisesRegex(discovery.DiscoveryError, "unavailable"):
            discovery.replay_hj212(self.database, endpoint_id=unbound_id, start_id=raw_ids[0], end_id=raw_ids[-1])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO sites(id,name) VALUES (?,?)", (31, "future-station-31"))
            connection.commit()
        bind_output = io.StringIO()
        with redirect_stdout(bind_output):
            self.assertEqual(provision_main([
                "station-identities-bind-plan", "--database", str(self.database), "--workbook", str(workbook),
                "--target-business-site-id", "31",
            ]), 0)
        bind_plan = json.loads(bind_output.getvalue())
        self.assertEqual(
            (bind_plan["binding_rows"], bind_plan["already_bound_rows"], bind_plan["deferred_rows"], bind_plan["conflict_categories"]),
            (1, 0, 12, []),
        )
        bind_arguments = [
            "--database", str(self.database), "--workbook", str(workbook), "--offline-confirmation", "--credential-stdin",
            "--target-business-site-id", "31",
            "--expected-fingerprint", bind_plan["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
        ]
        with closing(sqlite3.connect(self.database)) as connection:
            before_failed_binding = connection.execute(
                "SELECT id,business_site_id,endpoint_state FROM trusted_endpoints ORDER BY id"
            ).fetchall()
        stdin = io.TextIOWrapper(io.BytesIO(b"wrong-synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *bind_arguments]), 2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT id,business_site_id,endpoint_state FROM trusted_endpoints ORDER BY id"
            ).fetchall(), before_failed_binding)
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *bind_arguments]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint = connection.execute("SELECT id,business_site_id,endpoint_state FROM trusted_endpoints WHERE id=?", (unbound_id,)).fetchone()
            self.assertEqual(tuple(endpoint), (unbound_id, 31, "bound"))
            self.assertEqual(connection.execute(
                "SELECT DISTINCT authentication_status FROM ingest_raw_frames WHERE endpoint_id=?", (unbound_id,)
            ).fetchall(), [("unbound_authenticated",)])
        verified_plan = provision.preview_station_code_identity_binding(self.database, workbook, 31)
        verified_fingerprint = provision._station_code_identity_binding_summary(verified_plan)["fingerprint"]
        verified_arguments = bind_arguments.copy()
        verified_arguments[verified_arguments.index(bind_plan["fingerprint"])] = verified_fingerprint
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-verify", *verified_arguments]), 0)
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *verified_arguments]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT id FROM trusted_endpoints WHERE station_code='SYNTH-MN-00000031'"
            ).fetchone()[0], unbound_id)
            connection.execute("INSERT INTO sites(id,name) VALUES (?,?)", (32, "future-station-32"))
            connection.commit()
        second_output = io.StringIO()
        with redirect_stdout(second_output):
            self.assertEqual(provision_main([
                "station-identities-bind-plan", "--database", str(self.database), "--workbook", str(workbook),
                "--target-business-site-id", "32",
            ]), 0)
        second_plan = json.loads(second_output.getvalue())
        self.assertEqual(
            (second_plan["binding_rows"], second_plan["already_bound_rows"], second_plan["deferred_rows"], second_plan["conflict_categories"]),
            (1, 0, 11, []),
        )
        second_arguments = [
            "--database", str(self.database), "--workbook", str(workbook), "--offline-confirmation", "--credential-stdin",
            "--target-business-site-id", "32",
            "--expected-fingerprint", second_plan["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
        ]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *second_arguments]), 0)
        second_verified = provision.preview_station_code_identity_binding(self.database, workbook, 32)
        second_arguments[second_arguments.index(second_plan["fingerprint"])] = provision._station_code_identity_binding_summary(second_verified)["fingerprint"]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-verify", *second_arguments]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint = connection.execute("SELECT id,business_site_id,endpoint_state FROM trusted_endpoints WHERE id=?", (next_unbound_id,)).fetchone()
            self.assertEqual(tuple(endpoint), (next_unbound_id, 32, "bound"))
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                   expected_interval_seconds,effective_from) VALUES (?,31,'Asia/Shanghai',1,'realtime',60,'2020-01-01T00:00:00+00:00')""",
                (unbound_id,),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,business_metric,expected_interval_seconds,
                   tolerance_seconds,effective_from,enabled) VALUES (?, 'HJ212:w01001','ph',60,0,'2020-01-01T00:00:00+00:00',1)""",
                (unbound_id,),
            )
            connection.commit()
        replayed = discovery.replay_hj212(self.database, endpoint_id=unbound_id, start_id=raw_ids[0], end_id=raw_ids[-1])
        self.assertEqual(replayed["result"], "replayed")
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM observation_batches WHERE endpoint_id=?", (unbound_id,)
            ).fetchone()[0], 2)

    async def test_identity_conflicts_and_binding_credential_failure_leave_no_writes(self):
        os.environ["STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT"] = self.source_fingerprint(self.mixed_formal_rows())
        workbook = self.root / "mixed-identities.xlsx"
        write_workbook(workbook, self.mixed_formal_rows())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM sites WHERE id>30")
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,1,'bound')",
                ("SYNTH-MN-00000031", credential_hmac(b"synthetic-password", self.pepper)),
            )
            connection.commit()
        conflict = preview_station_code_identity_import(self.database, workbook)
        self.assertIn("station_code_conflict", conflict["errors"])
        before = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main([
                "station-identities-apply", "--database", str(self.database), "--workbook", str(workbook),
                "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", "sha256:invalid",
                "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
            ]), 2)
        self.assertEqual(self.endpoint_rows(), before)
        missing_rows = self.formal_rows()
        missing_rows.pop(-2)
        missing = self.root / "missing-identities.xlsx"
        write_workbook(missing, missing_rows)
        missing_preview = preview_station_code_identity_import(self.database, missing)
        self.assertEqual(missing_preview["accepted_rows"], 42)
        before = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main([
                "station-identities-apply", "--database", str(self.database), "--workbook", str(missing),
                "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", "sha256:missing",
                "--expected-accepted-rows", "42", "--expected-ignored-rows", "1",
            ]), 2)
        self.assertEqual(self.endpoint_rows(), before)

    async def test_binding_rejects_ambiguous_and_wrong_site_candidates_without_writes(self):
        os.environ["STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT"] = self.source_fingerprint(self.mixed_formal_rows())
        workbook = self.root / "mixed-identities.xlsx"
        write_workbook(workbook, self.mixed_formal_rows())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM sites WHERE id>30")
            connection.commit()
        import_output = io.StringIO()
        with redirect_stdout(import_output):
            self.assertEqual(provision_main([
                "station-identities-plan", "--database", str(self.database), "--workbook", str(workbook),
            ]), 0)
        import_plan = json.loads(import_output.getvalue())
        arguments = [
            "--database", str(self.database), "--workbook", str(workbook), "--offline-confirmation", "--credential-stdin",
            "--expected-fingerprint", import_plan["fingerprint"],
            "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
        ]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *arguments]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO sites(id,name) VALUES (?,?)", (31, "future-station-31"))
            connection.execute("INSERT INTO sites(id,name) VALUES (?,?)", (44, "future-station-31"))
            connection.commit()
        ambiguous_output = io.StringIO()
        with redirect_stdout(ambiguous_output):
            self.assertEqual(provision_main([
                "station-identities-bind-plan", "--database", str(self.database), "--workbook", str(workbook),
                "--target-business-site-id", "31",
            ]), 0)
        ambiguous_plan = json.loads(ambiguous_output.getvalue())
        self.assertIn("ambiguous_station", ambiguous_plan["conflict_categories"])
        before = self.endpoint_rows()
        bind_arguments = [
            "--database", str(self.database), "--workbook", str(workbook), "--offline-confirmation", "--credential-stdin",
            "--target-business-site-id", "31",
            "--expected-fingerprint", ambiguous_plan["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
        ]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *bind_arguments]), 2)
        self.assertEqual(self.endpoint_rows(), before)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM sites WHERE id=44")
            connection.execute(
                "UPDATE trusted_endpoints SET business_site_id=1,endpoint_state='bound' WHERE station_code='SYNTH-MN-00000031'"
            )
            connection.commit()
        wrong_site_output = io.StringIO()
        with redirect_stdout(wrong_site_output):
            self.assertEqual(provision_main([
                "station-identities-bind-plan", "--database", str(self.database), "--workbook", str(workbook),
                "--target-business-site-id", "31",
            ]), 0)
        wrong_site_plan = json.loads(wrong_site_output.getvalue())
        self.assertIn("station_code_conflict", wrong_site_plan["conflict_categories"])
        before = self.endpoint_rows()
        bind_arguments[bind_arguments.index(ambiguous_plan["fingerprint"])] = wrong_site_plan["fingerprint"]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *bind_arguments]), 2)
        self.assertEqual(self.endpoint_rows(), before)

    async def test_expanded_identity_source_is_explicitly_not_ready_without_writes(self):
        workbook = self.root / "expanded-identities.xlsx"
        write_workbook(workbook, self.expanded_formal_rows())
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(provision_main([
                "station-identities-plan", "--database", str(self.database), "--workbook", str(workbook),
            ]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual((plan["accepted_rows"], plan["source_status"], plan["result"]), (44, "not_ready", "not_ready"))
        before = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main([
                "station-identities-apply", "--database", str(self.database), "--workbook", str(workbook),
                "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", plan["fingerprint"],
                "--expected-accepted-rows", "44", "--expected-ignored-rows", "1",
            ]), 2)
        self.assertEqual(self.endpoint_rows(), before)

    async def test_independent_source_lock_rejects_repreviewed_changes_and_deferred_replacement(self):
        workbook = self.root / "approved.xlsx"
        write_workbook(workbook, self.mixed_formal_rows())
        os.environ["STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT"] = self.source_fingerprint(self.mixed_formal_rows())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM sites WHERE id>30")
            connection.commit()
        changed_rows = self.mixed_formal_rows()
        changed_rows[-2] = ("replacement-future-station", "SYNTH-MN-REPLACED")
        changed = self.root / "repreviewed.xlsx"
        write_workbook(changed, changed_rows)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(provision_main(["station-identities-plan", "--database", str(self.database), "--workbook", str(changed)]), 0)
        changed_plan = json.loads(output.getvalue())
        self.assertEqual(changed_plan["source_status"], "not_ready")
        before = self.endpoint_rows()
        args = ["--database", str(self.database), "--workbook", str(changed), "--offline-confirmation", "--credential-stdin",
                "--expected-fingerprint", changed_plan["fingerprint"], "--expected-accepted-rows", "43", "--expected-ignored-rows", "1"]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *args]), 2)
        self.assertEqual(self.endpoint_rows(), before)
        approved_plan = provision.preview_station_code_identity_import(self.database, workbook)
        approved_fingerprint = provision._station_code_identity_summary(approved_plan)["fingerprint"]
        with mock.patch.dict(os.environ, {"STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT": ""}):
            self.assertEqual(provision._station_code_identity_summary(approved_plan)["source_status"], "not_ready")
            stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
            with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
                self.assertEqual(provision_main([
                    "station-identities-apply", "--database", str(self.database), "--workbook", str(workbook),
                    "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", approved_fingerprint,
                    "--expected-accepted-rows", "43", "--expected-ignored-rows", "1",
                ]), 2)
            self.assertEqual(self.endpoint_rows(), before)
        args[args.index(str(changed))] = str(workbook)
        args[args.index(changed_plan["fingerprint"])] = approved_fingerprint
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *args]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO sites(id,name) VALUES (31,'future-station-31')")
            connection.commit()
        bind_output = io.StringIO()
        with redirect_stdout(bind_output):
            self.assertEqual(provision_main(["station-identities-bind-plan", "--database", str(self.database),
                                             "--workbook", str(changed), "--target-business-site-id", "31"]), 0)
        bind_plan = json.loads(bind_output.getvalue())
        self.assertEqual(bind_plan["source_status"], "not_ready")
        before = self.endpoint_rows()
        bind_args = ["--database", str(self.database), "--workbook", str(changed), "--target-business-site-id", "31",
                     "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", bind_plan["fingerprint"],
                     "--expected-accepted-rows", "43", "--expected-ignored-rows", "1"]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *bind_args]), 2)
        self.assertEqual(self.endpoint_rows(), before)
        with mock.patch.dict(os.environ, {"STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT": self.source_fingerprint(self.expanded_formal_rows())}):
            trimmed = self.root / "trimmed.xlsx"
            write_workbook(trimmed, self.formal_rows())
            trimmed_preview = provision.preview_station_code_identity_import(self.database, trimmed)
            self.assertEqual(provision._station_code_identity_summary(trimmed_preview)["source_status"], "not_ready")

    async def test_target_binding_retires_only_confirmed_old_identity(self):
        os.environ["STATION_IDENTITY_APPROVED_SOURCE_FINGERPRINT"] = self.source_fingerprint(self.mixed_formal_rows())
        workbook = self.root / "approved.xlsx"
        write_workbook(workbook, self.mixed_formal_rows())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM sites WHERE id>30")
            connection.commit()
        import_preview = provision.preview_station_code_identity_import(self.database, workbook)
        import_fingerprint = provision._station_code_identity_summary(import_preview)["fingerprint"]
        import_args = ["--database", str(self.database), "--workbook", str(workbook), "--offline-confirmation", "--credential-stdin",
                       "--expected-fingerprint", import_fingerprint, "--expected-accepted-rows", "43", "--expected-ignored-rows", "1"]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-apply", *import_args]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("INSERT INTO sites(id,name) VALUES (31,'future-station-31')")
            connection.execute("INSERT INTO sites(id,name) VALUES (32,'future-station-32')")
            old_id = connection.execute("INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,31,'bound')",
                                        ("OLD-SYNTHETIC-31", credential_hmac(b"synthetic-password", self.pepper))).lastrowid
            connection.commit()
        preview = provision.preview_station_code_identity_binding(self.database, workbook, 31)
        plan = provision._station_code_identity_binding_summary(preview)
        self.assertEqual((plan["binding_rows"], plan["retired_rows"], plan["conflict_categories"]), (1, 1, []))
        args = ["--database", str(self.database), "--workbook", str(workbook), "--target-business-site-id", "31",
                "--offline-confirmation", "--credential-stdin", "--expected-fingerprint", plan["fingerprint"],
                "--expected-accepted-rows", "43", "--expected-ignored-rows", "1"]
        before = self.endpoint_rows()
        for extra in ([], ["--confirm-disable"]):
            password = b"synthetic-password\n" if not extra else b"wrong-synthetic-password\n"
            stdin = io.TextIOWrapper(io.BytesIO(password), encoding="utf-8")
            with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
                self.assertEqual(provision_main(["station-identities-bind-apply", *args, *extra]), 2)
            self.assertEqual(self.endpoint_rows(), before)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE trusted_endpoints SET credential_hmac=? WHERE id=?",
                               (credential_hmac(b"different-old-password", self.pepper), old_id))
            connection.commit()
        before = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *args, "--confirm-disable"]), 2)
        self.assertEqual(self.endpoint_rows(), before)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE trusted_endpoints SET credential_hmac=? WHERE id=?",
                               (credential_hmac(b"synthetic-password", self.pepper), old_id))
            connection.commit()
        with closing(sqlite3.connect(self.database)) as connection:
            second_old = connection.execute("INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,31,'bound')",
                                            ("OTHER-SYNTHETIC-31", credential_hmac(b"synthetic-password", self.pepper))).lastrowid
            connection.commit()
        conflicted = provision.preview_station_code_identity_binding(self.database, workbook, 31)
        self.assertIn("ambiguous_superseded_station_identity", conflicted["errors"])
        before = self.endpoint_rows()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *args, "--confirm-disable"]), 2)
        self.assertEqual(self.endpoint_rows(), before)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM trusted_endpoints WHERE id=?", (second_old,))
            connection.commit()
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-apply", *args, "--confirm-disable"]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT enabled FROM trusted_endpoints WHERE id=?", (old_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT business_site_id FROM trusted_endpoints WHERE station_code='SYNTH-MN-00000031'").fetchone()[0], 31)
            self.assertEqual(connection.execute("SELECT business_site_id FROM trusted_endpoints WHERE station_code='SYNTH-MN-00000032'").fetchone()[0], None)
        verified = provision.preview_station_code_identity_binding(self.database, workbook, 31)
        verify_args = args.copy()
        verified_fingerprint = provision._station_code_identity_binding_summary(verified)["fingerprint"]
        verify_args[verify_args.index(plan["fingerprint"])] = verified_fingerprint
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-verify", *verify_args]), 0)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE trusted_endpoints SET enabled=1,endpoint_state='bound' WHERE id=?", (old_id,))
            connection.commit()
        verified = provision.preview_station_code_identity_binding(self.database, workbook, 31)
        verify_args[verify_args.index(verified_fingerprint)] = provision._station_code_identity_binding_summary(verified)["fingerprint"]
        stdin = io.TextIOWrapper(io.BytesIO(b"synthetic-password\n"), encoding="utf-8")
        with mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper}, clear=False), mock.patch("sys.stdin", stdin):
            self.assertEqual(provision_main(["station-identities-bind-verify", *verify_args]), 2)

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
