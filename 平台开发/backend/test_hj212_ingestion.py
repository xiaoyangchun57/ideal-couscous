import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hj212_parser import HJ212_PARSER_VERSION, build_hj212_9011_response, hj212_crc, parse_hj212_frame
from ingestion_framing import extract_ingestion_frames
from migrate_station_ingestion import apply_migration
from sl651_parser import UP_FLOW_CONTROL, crc16_modbus, encode_bcd_time, encode_station_code
from sl651_server import IngestionStorage, StationIngestServer, credential_hmac
from station_monitoring import normalize_raw_frame


def make_hj212(*, station="TEST-HJ212-01", password="testpw", command="2011", qn="20260911164900001",
               data_time="20260911164900", factors="w01001-Rtd=0,Flag=N"):
    body = (
        f"QN={qn};ST=91;CN={command};PW={password};MN={station};Flag=5;DataTime={data_time};"
        f"CP=&&{factors}&&"
    ).encode("ascii")
    return b"##" + f"{len(body):04d}".encode("ascii") + body + hj212_crc(body).encode("ascii") + b"\r\n"


def make_real_shape_hj212(*, station="TEST-HJ212-01", password="testpw", command="2011", qn="20260911164900001",
                         cp="DataTime=20260911164900;w01001-Rtd=0;w01001-Flag=N"):
    body = f"QN={qn};ST=91;CN={command};PW={password};MN={station};Flag=5;CP=&&{cp}&&".encode("ascii")
    return b"##" + f"{len(body):04d}".encode("ascii") + body + hj212_crc(body).encode("ascii") + b"\r\n"


def make_binary_frame(station="0012345678", password=b"\x12\x34"):
    content = (1).to_bytes(2, "big") + encode_bcd_time(datetime(2020, 1, 1, 0, 0, 0))
    prefix = b"\x7e\x7e\x10" + encode_station_code(station) + password + b"\x32" + len(content).to_bytes(2, "big")
    prefix += b"\x02" + content + bytes((UP_FLOW_CONTROL,))
    return prefix + crc16_modbus(prefix).to_bytes(2, "big")


class HJ212ParserTest(unittest.TestCase):
    def test_appendix_a_crc_vector_and_old_modbus_crc_are_distinct(self):
        self.assertEqual(hj212_crc(b"123456789"), "2F80")
        self.assertNotEqual(hj212_crc(b"123456789"), f"{crc16_modbus(b'123456789'):04X}")

    def test_real_cp_data_time_and_factor_qualified_flags_are_structurally_compatible(self):
        frame = parse_hj212_frame(make_real_shape_hj212(cp=";".join((
            "DataTime=20260911164900", "w01001-Rtd=0", "w01001-Flag=N",
            "w01010-Rtd=20.5", "w01010-Flag=D", "w21003-Rtd=0.4", "w21003-Flag=F",
        ))))
        self.assertEqual(frame.data_time, datetime(2026, 9, 11, 16, 49))
        self.assertEqual([(item.protocol_code, item.raw_value, item.quality) for item in frame.factors], [
            ("HJ212:w01001", 0.0, "valid"), ("HJ212:w01010", 20.5, "fault"),
            ("HJ212:w21003", 0.4, "fault"),
        ])
    def test_parser_keeps_zero_flags_and_unknown_numeric_code_without_guessing(self):
        frame = parse_hj212_frame(make_hj212(factors=";".join((
            "w01001-Rtd=0,Flag=N", "w01019-Rtd=1.2,Flag=F", "005-Rtd=3,Flag=Q",
            "022-Rtd=3,Flag=F", "027-Rtd=4,Flag=F", "029-Rtd=5,Flag=F", "030-Rtd=6,Flag=F",
        ))))
        self.assertEqual(frame.command, "2011")
        self.assertEqual(frame.data_time.strftime("%H:%M"), "16:49")
        self.assertEqual([(item.protocol_code, item.raw_value, item.quality, item.issue_code) for item in frame.factors], [
            ("HJ212:w01001", 0.0, "valid", None),
            ("HJ212:w01019-Rtd", 1.2, "fault", "hj212_flag_fault"),
            ("HJ212:005", 3.0, "invalid", "hj212_unknown_flag"),
            ("HJ212:022", 3.0, "fault", "hj212_flag_fault"),
            ("HJ212:027", 4.0, "fault", "hj212_flag_fault"),
            ("HJ212:029", 5.0, "fault", "hj212_flag_fault"),
            ("HJ212:030", 6.0, "fault", "hj212_flag_fault"),
        ])

    def test_permanganate_quality_control_fields_are_not_realtime_factors(self):
        frame = parse_hj212_frame(make_hj212(factors=";".join((
            "w01019-Rtd=1.2,Flag=N,w01019-Reference=1.0,w01019-Measured=1.1",
            "w01019-Blank=0,Flag=N", "w01019-Span=1.0,Flag=N",
            "w01019-Parallel=1.2,Flag=N", "w01019-Recovery=98,Flag=N",
        ))))
        self.assertEqual([(item.protocol_code, item.raw_value) for item in frame.factors], [
            ("HJ212:w01019-Rtd", 1.2),
        ])

    def test_3020_response_contains_only_confirmed_application_fields(self):
        response = build_hj212_9011_response(parse_hj212_frame(make_hj212(command="3020", factors="")))
        length = int(response[2:6])
        body = response[6:6 + length]
        self.assertEqual(body, b"ST=91;CN=9011;CP=&&QnRtn=1&&")
        self.assertEqual(response[6 + length:10 + length], hj212_crc(body).encode("ascii"))
        self.assertEqual(response[10 + length:], b"\r\n")

    def test_framing_recovers_after_noise_and_keeps_binary_and_text_frames(self):
        text = make_hj212()
        binary = make_binary_frame()
        frames, remainder, errors = extract_ingestion_frames(b"noise" + text[:11])
        self.assertEqual(frames, [])
        self.assertEqual(remainder, text[:11])
        self.assertEqual(errors, ["noise"])
        frames, remainder, errors = extract_ingestion_frames(remainder + text[11:] + binary + text)
        self.assertEqual(frames, [text, binary, text])
        self.assertEqual(remainder, b"")
        self.assertEqual(errors, [])

    def test_hj212_length_and_crc_fail_without_payload_echo(self):
        broken_length = b"##ABCD"
        with self.assertRaisesRegex(Exception, "HJ212 length"):
            parse_hj212_frame(broken_length)
        broken_crc = bytearray(make_hj212())
        broken_crc[-3] = ord("0") if broken_crc[-3] != ord("0") else ord("1")
        with self.assertRaisesRegex(Exception, "CRC"):
            parse_hj212_frame(bytes(broken_crc))

    def test_old_modbus_crc_frame_is_rejected(self):
        valid = make_hj212()
        body_length = int(valid[2:6])
        body = valid[6:6 + body_length]
        old_crc_frame = valid[:6 + body_length] + f"{crc16_modbus(body):04X}".encode("ascii") + b"\r\n"
        with self.assertRaisesRegex(Exception, "CRC"):
            parse_hj212_frame(old_crc_frame)


class HJ212IngestionContractTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "isolated-hj212.db"
        self.pepper = "isolated-hj212-pepper"
        self.station = "TEST-HJ212-01"
        self.password = "testpw"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            connection.execute("INSERT INTO sites(id,name) VALUES (1,'isolated HJ212 site')")
            connection.commit()
        apply_migration(self.database, self.root / "backups")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,endpoint_state) VALUES (?,?,1,'bound')",
                (self.station, credential_hmac(self.password.encode("ascii"), self.pepper)),
            )
            endpoint_id = connection.execute("SELECT id FROM trusted_endpoints WHERE station_code=?", (self.station,)).fetchone()[0]
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                   expected_interval_seconds,effective_from) VALUES (?,1,'Asia/Shanghai',1,'realtime',60,'2020-01-01T00:00:00+00:00')""",
                (endpoint_id,),
            )
            for code in (
                "HJ212:w01001", "HJ212:w01010", "HJ212:w01019-Rtd", "HJ212:w21001", "HJ212:w21003",
                "HJ212:w21011", "HJ212:022", "HJ212:027", "HJ212:029", "HJ212:030",
            ):
                connection.execute(
                    """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,expected_interval_seconds,tolerance_seconds,
                       effective_from,enabled) VALUES (?,?,?,?,?,1)""",
                    (endpoint_id, code, 60, 0, "2020-01-01T00:00:00+00:00"),
                )
            connection.commit()
        self.storage = IngestionStorage(self.database, self.pepper)
        self.server = StationIngestServer(self.storage, host="127.0.0.1", port=0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _raw_ids(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return [row[0] for row in connection.execute("SELECT id FROM ingest_raw_frames ORDER BY id")]

    def test_authenticated_2011_is_retained_normalized_and_duplicate_safe(self):
        raw = make_hj212(factors=";".join((
            "w01001-Rtd=0,Flag=N", "w01010-Rtd=20.5,Flag=N", "w01019-Rtd=1.2,Flag=F", "005-Rtd=3,Flag=N",
        )))
        self.assertIsNone(self.server._process_raw(raw, "2026-09-11T08:49:05+00:00"))
        self.assertIsNone(self.server._process_raw(raw, "2026-09-11T08:49:06+00:00"))
        first, second = self._raw_ids()
        self.assertEqual(normalize_raw_frame(self.database, first), "partial")
        self.assertEqual(normalize_raw_frame(self.database, second), "not_projectable")
        with closing(sqlite3.connect(self.database)) as connection:
            protocol = connection.execute("SELECT protocol_family,parser_version FROM ingest_frame_protocols WHERE raw_frame_id=?", (first,)).fetchone()
            attempt = connection.execute("SELECT parser_version,parse_status FROM ingest_parse_attempts WHERE raw_frame_id=?", (first,)).fetchone()
            raws = connection.execute("SELECT authentication_status,disposition FROM ingest_raw_frames ORDER BY id").fetchall()
            batch = connection.execute("SELECT function_code,serial_number,observed_at FROM observation_batches").fetchone()
            values = connection.execute(
                "SELECT protocol_code,business_metric,standard_unit,quality,is_published,standard_value FROM observation_values ORDER BY protocol_code"
            ).fetchall()
            issues = {row[0] for row in connection.execute("SELECT issue_type FROM monitoring_quality_issues")}
        self.assertEqual(protocol, ("hj212", HJ212_PARSER_VERSION))
        self.assertEqual(attempt, (HJ212_PARSER_VERSION, "parsed_header"))
        self.assertEqual(raws, [("authenticated", "accepted"), ("authenticated", "duplicate")])
        self.assertEqual(batch[:2], (2011, 0))
        self.assertEqual(batch[2], "2026-09-11T08:49:00+00:00")
        self.assertEqual(values, [
            ("HJ212:w01001", "ph", "pH", "valid", 1, 0.0),
            ("HJ212:w01010", "water_temp", "degC", "valid", 1, 20.5),
        ])
        self.assertIn("hj212_flag_fault", issues)
        self.assertIn("unmapped_factor", issues)
        self.assertNotIn("dissolved_oxygen", {row[1] for row in values})

    def test_real_cp_shape_only_projects_n_and_leaves_d_f_as_quality_evidence(self):
        raw = make_real_shape_hj212(cp=";".join((
            "DataTime=20260911164900", "w01001-Rtd=0", "w01001-Flag=N",
            "w01010-Rtd=20.5", "w01010-Flag=D", "w21003-Rtd=0.4", "w21003-Flag=F",
        )))
        self.assertIsNone(self.server._process_raw(raw, "2026-09-11T08:49:05+00:00"))
        self.assertEqual(normalize_raw_frame(self.database, self._raw_ids()[0]), "partial")
        with closing(sqlite3.connect(self.database)) as connection:
            values = connection.execute("SELECT protocol_code,quality FROM observation_values ORDER BY protocol_code").fetchall()
            issues = {row[0] for row in connection.execute("SELECT issue_type FROM monitoring_quality_issues")}
        self.assertEqual(values, [("HJ212:w01001", "valid")])
        self.assertIn("hj212_flag_fault", issues)

    def test_only_permanganate_realtime_value_can_be_mapped_or_projected(self):
        raw = make_hj212(factors=";".join((
            "w01019-Rtd=1.2,Flag=N,w01019-Reference=1.0,w01019-Measured=1.1",
            "w01019-Blank=0,Flag=N", "w01019-Span=1.0,Flag=N",
            "w01019-Parallel=1.2,Flag=N", "w01019-Recovery=98,Flag=N",
        )))
        self.assertIsNone(self.server._process_raw(raw, "2026-09-11T08:51:00+00:00"))
        self.assertEqual(normalize_raw_frame(self.database, self._raw_ids()[0]), "accepted")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            definitions = connection.execute(
                "SELECT protocol_code FROM monitoring_factor_definitions WHERE protocol_code LIKE 'HJ212:w01019%'"
            ).fetchall()
            mappings = connection.execute(
                "SELECT protocol_code FROM monitoring_factor_mappings WHERE endpoint_id=(SELECT id FROM trusted_endpoints WHERE station_code=?)",
                (self.station,),
            ).fetchall()
            values = connection.execute("SELECT protocol_code,business_metric,standard_value FROM observation_values").fetchall()
            issues = connection.execute("SELECT issue_type FROM monitoring_quality_issues").fetchall()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO monitoring_factor_mappings(endpoint_id,protocol_code,expected_interval_seconds,tolerance_seconds,
                       effective_from,enabled) VALUES ((SELECT id FROM trusted_endpoints WHERE station_code=?),?,60,0,'2020-01-01T00:00:00+00:00',1)""",
                    (self.station, "HJ212:w01019-Reference"),
                )
        self.assertEqual(definitions, [("HJ212:w01019-Rtd",)])
        self.assertEqual(mappings, [
            ("HJ212:022",), ("HJ212:027",), ("HJ212:029",), ("HJ212:030",),
            ("HJ212:w01001",), ("HJ212:w01010",), ("HJ212:w01019-Rtd",),
            ("HJ212:w21001",), ("HJ212:w21003",), ("HJ212:w21011",),
        ])
        self.assertEqual(values, [("HJ212:w01019-Rtd", "codmn", 1.2)])
        self.assertEqual(issues, [])

    def test_confirmed_standard_codes_and_legacy_aliases_keep_raw_codes_and_faults_unpublished(self):
        raw = make_hj212(factors=";".join((
            "w21001-Rtd=2.3,Flag=N", "w21003-Rtd=0.4,Flag=N", "w21011-Rtd=0.05,Flag=N",
            "022-Rtd=3,Flag=F", "027-Rtd=4,Flag=F", "029-Rtd=5,Flag=F", "030-Rtd=6,Flag=F",
            "005-Rtd=7,Flag=N",
        )))
        self.assertIsNone(self.server._process_raw(raw, "2026-09-11T08:52:00+00:00"))
        self.assertEqual(normalize_raw_frame(self.database, self._raw_ids()[0]), "partial")
        with closing(sqlite3.connect(self.database)) as connection:
            values = connection.execute(
                "SELECT protocol_code,business_metric,standard_unit,quality,is_published FROM observation_values ORDER BY protocol_code"
            ).fetchall()
            issues = {row[0] for row in connection.execute("SELECT issue_type FROM monitoring_quality_issues")}
        self.assertEqual(values, [
            ("HJ212:w21001", "total_nitrogen", "mg/L", "valid", 1),
            ("HJ212:w21003", "ammonia", "mg/L", "valid", 1),
            ("HJ212:w21011", "total_phosphorus", "mg/L", "valid", 1),
        ])
        self.assertIn("hj212_flag_fault", issues)
        self.assertIn("unmapped_factor", issues)

    def test_3020_response_requires_authenticated_durable_receipt(self):
        raw = make_hj212(command="3020", factors="")
        response = self.server._process_raw(raw, "2026-09-11T08:50:00+00:00")
        self.assertEqual(response, build_hj212_9011_response(parse_hj212_frame(raw)))
        with mock.patch.object(self.storage, "persist_parsed", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                self.server._process_raw(raw, "2026-09-11T08:50:01+00:00")
        self.assertIsNone(self.server._process_raw(make_hj212(command="3020", password="wrongpw", factors=""), "2026-09-11T08:50:02+00:00"))
        self.assertIsNone(self.server._process_raw(make_hj212(command="3020", station="UNKNOWN-HJ212", factors=""), "2026-09-11T08:50:03+00:00"))

    def test_bad_credentials_unknown_station_crc_and_unconfirmed_commands_never_project_or_reply(self):
        cases = (
            make_hj212(password="wrongpw"),
            make_hj212(station="UNKNOWN-HJ212"),
            make_hj212(command="2091"),
            make_hj212(command="3041"),
            make_hj212(command="9015"),
        )
        for raw in cases:
            self.assertIsNone(self.server._process_raw(raw, "2026-09-11T08:50:00+00:00"))
        bad_crc = bytearray(make_hj212())
        bad_crc[-3] = ord("0") if bad_crc[-3] != ord("0") else ord("1")
        self.assertIsNone(self.server._process_raw(bytes(bad_crc), "2026-09-11T08:50:01+00:00"))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observation_batches").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observation_values").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ingest_frame_protocols WHERE protocol_family='hj212'").fetchone()[0], 6)
            errors = {row[0] for row in connection.execute("SELECT error_type FROM ingest_errors")}
        self.assertTrue({"credential_failed", "unknown_endpoint", "hj212_non_data_command", "hj212_crc_mismatch"} <= errors)


if __name__ == "__main__":
    unittest.main()
