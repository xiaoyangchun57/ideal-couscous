import base64
from contextlib import closing
from contextlib import redirect_stdout
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hj212_parser import hj212_crc
from migrate_station_ingestion import apply_migration
import station_ingest_retention as retention
from station_ingest_retention import (
    RetentionError,
    StoragePolicy,
    archive_raw_day,
    backfill_archive_evidence_refs,
    bootstrap_retention_state,
    compact_isolated_copy,
    compact_prepared_database,
    maintain_retention,
    prepare_recent_slot_summaries,
    prepare_startup_state,
    read_raw_evidence,
    reduce_confirmed_minute_duplicates,
    reduce_legacy_indexes,
    run_periodic_maintenance,
)
from station_ingest_cutover import prepare_cutover


class StationIngestRetentionWindowsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "retention.db"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            connection.execute("INSERT INTO sites VALUES (1,'retention station')")
            connection.commit()
        apply_migration(self.database, self.root / "backups")
        with closing(sqlite3.connect(self.database)) as connection:
            self.endpoint_id = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('RETENTION-MN','credential',1,'bound')"""
            ).lastrowid
            connection.commit()
        self.policy = StoragePolicy(
            self.root,
            retention.VALIDATION_MINIMUM_TOTAL_BYTES,
            retention.VALIDATION_MINIMUM_FREE_BYTES,
            "validation",
        )
        self.now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def _insert_raw(
        self, received_at, payload, *, logical_key=None, authentication="authenticated",
        disposition="accepted", persistence="persisted", endpoint_id=None, station_code=None,
    ):
        digest = hashlib.sha256(payload).hexdigest()
        endpoint_id = self.endpoint_id if endpoint_id is None else endpoint_id
        station_code = station_code or "RETENTION-MN"
        with closing(sqlite3.connect(self.database)) as connection:
            raw_id = connection.execute(
                """INSERT INTO ingest_raw_frames(
                       endpoint_id,station_code,received_at,frame_sha256,logical_key_sha256,
                       raw_frame,body_length,crc_status,authentication_status,disposition,
                       persistence_state)
                   VALUES (?,?,?,?,?,?,?,'valid',?,?,?)""",
                (endpoint_id, station_code, received_at, digest, logical_key,
                 payload, len(payload), authentication, disposition, persistence),
            ).lastrowid
            connection.execute(
                """INSERT INTO ingest_frame_protocols(raw_frame_id,protocol_family,parser_version)
                   VALUES (?,'hj212','test-parser')""",
                (raw_id,),
            )
            connection.commit()
        return raw_id

    def _copy_archive_to_mirror(self, archive_path, mirror_root):
        relative = Path(archive_path).resolve().relative_to(self.root.resolve())
        mirror_path = Path(mirror_root) / relative
        mirror_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive_path, mirror_path)
        return mirror_path

    def _configure_hj212_ph(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(
                       endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                       expected_interval_seconds,effective_from)
                   VALUES (?,1,'Asia/Shanghai',1,'realtime',60,'2020-01-01T00:00:00+00:00')""",
                (self.endpoint_id,),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(
                       endpoint_id,protocol_code,expected_interval_seconds,tolerance_seconds,
                       effective_from,enabled)
                   VALUES (?,'HJ212:w01001',60,0,'2020-01-01T00:00:00+00:00',1)""",
                (self.endpoint_id,),
            )
            connection.commit()

    def _hj212(self, *, qn, data_time, value="7.1", command="2011"):
        kind = "Avg" if command == "2061" else "Rtd"
        body = (
            f"QN={qn};ST=91;CN={command};PW=testpw;MN=RETENTION-MN;Flag=5;"
            f"DataTime={data_time};CP=&&w01001-{kind}={value},Flag=N&&"
        ).encode("ascii")
        return (b"##" + f"{len(body):04d}".encode("ascii") + body
                + hj212_crc(body).encode("ascii") + b"\r\n")

    def test_bootstrap_prepares_only_the_real_consumer_window(self):
        self._insert_raw(
            "2026-07-18T11:59:59+00:00", b"outside-dedup", logical_key="outside-dedup")
        dedup_boundary_id = self._insert_raw(
            "2026-07-18T12:00:00+00:00", b"dedup-boundary", logical_key="dedup-boundary")
        old_id = self._insert_raw(
            "2026-10-13T11:59:59+00:00", b"old", logical_key="same-key")
        boundary_id = self._insert_raw(
            "2026-10-13T12:00:00+00:00", b"boundary", logical_key="same-key")
        latest_id = self._insert_raw(
            "2026-10-16T11:00:00+00:00", b"latest", logical_key="latest-key")

        counts = bootstrap_retention_state(self.database, now=self.now)

        self.assertEqual(counts["recent_receipts"], 1)
        self.assertEqual(counts["receipt_hour_summaries"], 1)
        self.assertEqual(counts["consumer_window_hours"], 8)
        self.assertEqual(counts["historical_error_summaries"], 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                [row[0] for row in connection.execute(
                    "SELECT raw_frame_id FROM station_ingest_recent_receipts ORDER BY raw_frame_id")],
                [latest_id],
            )
            state = connection.execute(
                """SELECT latest_received_at,latest_raw_frame_id,latest_authenticated_at
                   FROM station_ingest_current_state WHERE endpoint_id=?""",
                (self.endpoint_id,),
            ).fetchone()
            self.assertEqual(
                state,
                ("2026-10-16T11:00:00+00:00", latest_id, "2026-10-16T11:00:00+00:00"),
            )
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_dedup_keys"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT SUM(receipt_count) FROM station_ingest_receipt_hourly"
            ).fetchone()[0], 1)

    def test_startup_prepares_recent_slot_summaries_idempotently(self):
        self._configure_hj212_ph()
        outside = self._insert_raw(
            "2026-10-16T03:59:59+00:00",
            self._hj212(qn="outside", data_time="20261016120000"),
            logical_key="outside-slot")
        first = self._insert_raw(
            "2026-10-16T10:00:00+00:00",
            self._hj212(qn="recent-1", data_time="20261016160000"),
            logical_key="recent-slot-1", disposition="pending_parse", persistence="pending_parse")
        second = self._insert_raw(
            "2026-10-16T10:01:00+00:00",
            self._hj212(qn="recent-2", data_time="20261016160100"),
            logical_key="recent-slot-2", disposition="pending_parse", persistence="pending_parse")

        startup = prepare_startup_state(self.database, now=self.now)
        prepared = prepare_recent_slot_summaries(self.database, now=self.now)
        repeated = prepare_recent_slot_summaries(self.database, now=self.now)

        self.assertEqual(startup["recent_receipts"], 2)
        self.assertEqual(prepared["scanned_frames"], 2)
        self.assertEqual(prepared["eligible_frames"], 2)
        self.assertEqual(prepared["slot_identities"], 1)
        self.assertEqual(repeated, prepared)
        with closing(sqlite3.connect(self.database)) as connection:
            summary = connection.execute(
                """SELECT canonical_raw_frame_id,receipt_count,first_received_at,last_received_at
                   FROM station_ingest_slot_summaries""").fetchone()
            self.assertEqual(summary, (
                first, 2, "2026-10-16T10:00:00+00:00", "2026-10-16T10:01:00+00:00"))
            self.assertNotEqual(summary[0], outside)

    def test_startup_refresh_preserves_folded_receipts_and_adds_r25_rows_once(self):
        self._insert_raw(
            "2026-10-16T11:00:00+00:00", b"r26-canonical", logical_key="r26-canonical")
        prepare_startup_state(self.database, now=self.now)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE station_ingest_receipt_hourly SET receipt_count=5")
            connection.commit()

        prepare_startup_state(self.database, now=self.now)
        self._insert_raw(
            "2026-10-16T11:30:00+00:00", b"r25-new-row", logical_key="r25-new-row")
        prepare_startup_state(self.database, now=self.now)
        prepare_startup_state(self.database, now=self.now)

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT SUM(receipt_count) FROM station_ingest_receipt_hourly"
            ).fetchone()[0], 6)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_recent_receipts"
            ).fetchone()[0], 2)

    def test_current_state_selects_one_latest_open_error_per_source(self):
        with closing(sqlite3.connect(self.database)) as connection:
            second_endpoint = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('RETENTION-SECOND','credential-2',1,'bound')"""
            ).lastrowid
            connection.commit()

        source_rows = ((self.endpoint_id, "RETENTION-MN", 20),
                       (second_endpoint, "RETENTION-SECOND", 5))
        expected = {}
        errors = []
        for source_index, (endpoint_id, station_code, count) in enumerate(source_rows):
            for error_index in range(count):
                received_at = (
                    f"2026-10-0{source_index + 1}T00:{error_index:02d}:00+00:00")
                raw_id = self._insert_raw(
                    received_at, f"open-{source_index}-{error_index}".encode("ascii"),
                    logical_key=f"open-{source_index}-{error_index}",
                    endpoint_id=endpoint_id, station_code=station_code,
                )
                error_type = f"source-{source_index}-error-{error_index}"
                errors.append((raw_id, error_type, received_at))
                expected[endpoint_id] = (received_at, error_type)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,?,'open',?)""",
                errors,
            )
            connection.commit()

        original_connect = sqlite3.connect
        statements = []

        def traced_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with mock.patch.object(sqlite3, "connect", side_effect=traced_connect):
            bootstrap_retention_state(self.database, now=self.now)

        error_state_writes = [
            statement for statement in statements
            if "RANKED_OPEN_ERRORS" in statement.upper()
            and "INSERT INTO STATION_INGEST_CURRENT_STATE" in statement.upper()
        ]
        self.assertEqual(len(error_state_writes), 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_errors WHERE status='open'"
            ).fetchone()[0], 25)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_dedup_keys WHERE protected=1"
            ).fetchone()[0], 25)
            for endpoint_id, latest in expected.items():
                self.assertEqual(connection.execute(
                    """SELECT latest_error_at,latest_error_type
                       FROM station_ingest_current_state WHERE source_key=?""",
                    (f"endpoint:{endpoint_id}",),
                ).fetchone(), latest)

    def test_confirmed_minute_duplicates_are_resumable_and_preserve_exceptions(self):
        self._configure_hj212_ph()
        raws = [
            self._hj212(qn="20260911120000001", data_time="20260911120000"),
            self._hj212(qn="20260911120100001", data_time="20260911120100"),
            self._hj212(qn="20260911120200001", data_time="20260911120200"),
            self._hj212(qn="20260911120400001", data_time="20260911120400"),
            self._hj212(qn="20260911120300001", data_time="20260911120300", value="7.2"),
            self._hj212(qn="20260911160000001", data_time="20260911160000"),
            self._hj212(qn="20260911120500001", data_time="20260911120000", command="2061"),
        ]
        raw_ids = [
            self._insert_raw(
                f"2026-09-11T04:0{index}:05+00:00", raw,
                logical_key=f"historical-{index}",
            )
            for index, raw in enumerate(raws)
        ]
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'manual_review','open','2026-09-11T04:01:06+00:00')""",
                (raw_ids[1],),
            )
            connection.execute(
                """INSERT INTO ingest_parse_attempts(
                       raw_frame_id,parser_version,parse_status,created_at)
                   VALUES (?,'historical','parsed_header','2026-09-11T04:02:06+00:00')""",
                (raw_ids[2],),
            )
            schedule_id = connection.execute(
                """INSERT INTO monitoring_business_schedules(
                       endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                       effective_from)
                   VALUES (?,'HJ212:w01001','Asia/Shanghai',14400,'00:00:00',
                           '2020-01-01T00:00:00+00:00')""",
                (self.endpoint_id,),
            ).lastrowid
            batch_id = connection.execute(
                """INSERT INTO observation_batches(
                       raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                       reported_at,observed_at,received_at,granularity,aggregation_source,
                       idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                   VALUES (?,?,1,2011,0,'2026-09-11T04:04:00+00:00',
                           '2026-09-11T04:04:00+00:00','2026-09-11T04:03:05+00:00',
                           'realtime','device_reported','formal-duplicate','test','accepted',
                           'completed','2026-09-11T04:04:06+00:00')""",
                (raw_ids[3], self.endpoint_id),
            ).lastrowid
            value_id = connection.execute(
                """INSERT INTO observation_values(
                       observation_batch_id,protocol_code,business_metric,standard_value,
                       standard_unit,quality,parser_version,is_published)
                   VALUES (?,'HJ212:w01001','ph',7.1,'pH','valid','test',1)""",
                (batch_id,),
            ).lastrowid
            connection.execute(
                """INSERT INTO monitoring_business_observations(
                       source_observation_value_id,observation_batch_id,schedule_id,endpoint_id,
                       business_site_id,protocol_code,business_metric,scheduled_at,observed_at,
                       received_at,standard_value,standard_unit,quality,timeliness,slot_state,
                       projection_version)
                   VALUES (?,?,?,?,1,'HJ212:w01001','ph','2026-09-11T04:00:00+00:00',
                           '2026-09-11T04:04:00+00:00','2026-09-11T04:03:05+00:00',7.1,
                           'pH','valid','on_time','selected','test')""",
                (value_id, batch_id, schedule_id, self.endpoint_id),
            )
            connection.commit()

        first = reduce_confirmed_minute_duplicates(
            self.database, batch_limit=2, now=self.now)
        second = reduce_confirmed_minute_duplicates(
            self.database, after_id=first["cursor_raw_id"], batch_limit=10, now=self.now)

        self.assertFalse(first["complete"])
        self.assertTrue(second["complete"])
        self.assertEqual(second["deleted"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            remaining = {row[0] for row in connection.execute(
                "SELECT id FROM ingest_raw_frames ORDER BY id")}
            self.assertIn(raw_ids[0], remaining)
            self.assertIn(raw_ids[1], remaining)
            self.assertNotIn(raw_ids[2], remaining)
            self.assertIn(raw_ids[3], remaining)
            self.assertIn(raw_ids[4], remaining)
            self.assertIn(raw_ids[5], remaining)
            self.assertIn(raw_ids[6], remaining)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_parse_attempts WHERE raw_frame_id=?", (raw_ids[2],)
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM monitoring_business_observations WHERE observation_batch_id=?",
                (batch_id,),
            ).fetchone()[0], 1)
            summaries = connection.execute(
                """SELECT slot_started_at,content_sha256,canonical_raw_frame_id
                   FROM station_ingest_slot_summaries ORDER BY slot_started_at,content_sha256"""
            ).fetchall()
            self.assertEqual(len(summaries), 3)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 2)

    def test_maintenance_keeps_summary_boundary_and_prunes_strictly_older_rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            for suffix, received_at in (
                ("old", "2026-09-16T11:59:59+00:00"),
                ("boundary", "2026-09-16T12:00:00+00:00"),
            ):
                connection.execute(
                    """INSERT INTO station_ingest_receipt_hourly(
                           source_key,endpoint_id,station_code,authentication_status,bucket_hour,
                           receipt_count,first_received_at,last_received_at,last_complete_raw_frame_id,updated_at)
                       VALUES (?,?,?,?,?,1,?,?,NULL,?)""",
                    (f"endpoint:{self.endpoint_id}:{suffix}", self.endpoint_id, "RETENTION-MN",
                     "authenticated", received_at, received_at, received_at, received_at),
                )
            for suffix, received_at in (
                ("old", "2026-07-18T11:59:59+00:00"),
                ("boundary", "2026-07-18T12:00:00+00:00"),
            ):
                connection.execute(
                    """INSERT INTO station_ingest_slot_summaries(
                           endpoint_id,station_code,command,slot_started_at,content_sha256,
                           canonical_raw_frame_id,canonical_frame_sha256,receipt_count,
                           first_received_at,last_received_at,last_authentication_status,created_at,updated_at)
                       VALUES (?,?, '2011',?,?,?,?,1,?,?,?,?,?)""",
                    (self.endpoint_id, "RETENTION-MN", received_at, suffix, 100,
                     f"frame-{suffix}", received_at, received_at, "authenticated",
                     received_at, received_at),
                )
            connection.commit()

        result = maintain_retention(self.database, self.policy, now=self.now)

        self.assertEqual(result["deleted_receipt_summaries"], 1)
        self.assertEqual(result["deleted_slot_summaries"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT last_received_at FROM station_ingest_receipt_hourly"
            ).fetchall(), [("2026-09-16T12:00:00+00:00",)])
            self.assertEqual(connection.execute(
                "SELECT last_received_at FROM station_ingest_slot_summaries"
            ).fetchall(), [("2026-07-18T12:00:00+00:00",)])

    def test_maintenance_deletes_unprotected_hot_relation_but_keeps_evidence_readable(self):
        payload = b"archived-unprotected-frame"
        raw_id = self._insert_raw(
            "2026-10-01T08:00:00+00:00", payload, logical_key="unprotected-key")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO ingest_parse_attempts(
                       raw_frame_id,parser_version,parse_status,created_at)
                   VALUES (?,'test','parsed_header','2026-10-01T08:00:01+00:00')""",
                (raw_id,),
            )
            connection.commit()

        result = maintain_retention(self.database, self.policy, now=self.now)

        self.assertEqual(result["deleted_raw_frames"], 1)
        self.assertEqual(result["archived_parse_attempts"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (raw_id,)).fetchone())
            self.assertEqual(read_raw_evidence(connection, raw_id), payload)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_maintenance_keeps_168_hour_boundary_and_active_issue(self):
        boundary_id = self._insert_raw(
            "2026-10-09T12:00:00+00:00", b"boundary", logical_key="boundary-key")
        protected_id = self._insert_raw(
            "2026-10-01T09:00:00+00:00", b"protected", logical_key="protected-key")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO monitoring_quality_issues(
                       raw_frame_id,issue_type,object_summary,first_seen_at,last_seen_at,status)
                   VALUES (?,'manual_review','active evidence','2026-10-01T09:00:00+00:00',
                           '2026-10-01T09:00:00+00:00','open')""",
                (protected_id,),
            )
            connection.commit()

        result = maintain_retention(self.database, self.policy, now=self.now)

        self.assertEqual(result["deleted_raw_frames"], 0)
        self.assertEqual(result["retained_protected_frames"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            remaining = {row[0] for row in connection.execute("SELECT id FROM ingest_raw_frames")}
            self.assertEqual(remaining, {boundary_id, protected_id})
            protected = connection.execute(
                """SELECT protected,expires_at FROM station_ingest_dedup_keys
                   WHERE logical_key_sha256='protected-key'"""
            ).fetchone()
            self.assertEqual(protected, (1, None))

    def test_mirror_retirement_deletes_only_strictly_older_than_90_days(self):
        old_payload = b"older-than-90-days"
        boundary_payload = b"exactly-90-days"
        old_id = self._insert_raw(
            "2026-07-17T11:59:59+00:00", old_payload, logical_key="old-mirror-key")
        boundary_id = self._insert_raw(
            "2026-07-18T12:00:00+00:00", boundary_payload, logical_key="boundary-mirror-key")
        archive_raw_day(self.database, "2026-07-17", self.policy)
        archive_raw_day(self.database, "2026-07-18", self.policy)
        with closing(sqlite3.connect(self.database)) as connection:
            paths = dict(connection.execute(
                "SELECT archive_date,archive_path FROM monitoring_raw_archive_parts"
            ).fetchall())
        mirror_root = self.root / "mirror"
        mirror_root.mkdir()
        old_mirror = self._copy_archive_to_mirror(paths["2026-07-17"], mirror_root)

        result = maintain_retention(
            self.database, self.policy, now=self.now, mirror_storage_dir=mirror_root)

        self.assertEqual(result["retired_raw_archive_parts"], 1)
        self.assertEqual(result["mirror_failures"], 0)
        self.assertEqual(result["overdue_archive_parts"], 0)
        self.assertFalse(Path(paths["2026-07-17"]).exists())
        self.assertTrue(Path(paths["2026-07-18"]).exists())
        self.assertTrue(old_mirror.exists())
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(read_raw_evidence(connection, old_id), old_payload)
            self.assertEqual(read_raw_evidence(connection, boundary_id), boundary_payload)
            retired = connection.execute(
                """SELECT mirror_path,mirror_verified_at,local_deleted_at
                   FROM monitoring_raw_archive_part_metadata
                   WHERE last_received_at<'2026-07-18T12:00:00+00:00'"""
            ).fetchone()
            self.assertEqual(retired[0], str(old_mirror.resolve()))
            self.assertTrue(retired[1])
            self.assertTrue(retired[2])

    def test_detail_archive_is_deleted_only_after_mirror_verification(self):
        records = [{"id": 41, "created_at": "2026-07-17T10:00:00+00:00", "state": "done"}]
        local_path, digest = retention._write_detail_archive(
            self.root, "parse_attempt", records)
        with closing(sqlite3.connect(self.database)) as connection:
            part_id = connection.execute(
                """INSERT INTO station_ingest_detail_archive_parts(
                       detail_kind,archive_path,content_sha256,first_source_id,last_source_id,
                       record_count,range_start,range_end,verified_at,created_at)
                   VALUES ('parse_attempt',?,?,41,41,1,
                           '2026-07-17T10:00:00+00:00','2026-07-17T10:00:00+00:00',
                           '2026-07-17T10:01:00+00:00','2026-07-17T10:01:00+00:00')""",
                (str(local_path), digest),
            ).lastrowid
            connection.commit()
        mirror_root = self.root / "mirror"
        mirror_root.mkdir()
        mirror_path = self._copy_archive_to_mirror(local_path, mirror_root)

        result = maintain_retention(
            self.database, self.policy, now=self.now, mirror_storage_dir=mirror_root)

        self.assertEqual(result["retired_detail_archive_parts"], 1)
        self.assertFalse(local_path.exists())
        with closing(sqlite3.connect(self.database)) as connection:
            stored = connection.execute(
                """SELECT mirror_path,mirror_verified_at,local_deleted_at
                   FROM station_ingest_detail_archive_parts WHERE id=?""",
                (part_id,),
            ).fetchone()
        self.assertEqual(stored[0], str(mirror_path.resolve()))
        self.assertTrue(stored[1])
        self.assertTrue(stored[2])
        with gzip.open(mirror_path, "rt", encoding="utf-8") as stream:
            self.assertEqual([json.loads(line) for line in stream], records)

    def test_missing_or_corrupt_mirror_keeps_local_archives(self):
        missing_id = self._insert_raw(
            "2026-07-16T10:00:00+00:00", b"missing-mirror", logical_key="missing-mirror-key")
        corrupt_id = self._insert_raw(
            "2026-07-17T10:00:00+00:00", b"corrupt-mirror", logical_key="corrupt-mirror-key")
        archive_raw_day(self.database, "2026-07-16", self.policy)
        archive_raw_day(self.database, "2026-07-17", self.policy)
        with closing(sqlite3.connect(self.database)) as connection:
            paths = dict(connection.execute(
                "SELECT archive_date,archive_path FROM monitoring_raw_archive_parts"
            ).fetchall())
        mirror_root = self.root / "mirror"
        mirror_root.mkdir()
        corrupt_mirror = self._copy_archive_to_mirror(paths["2026-07-17"], mirror_root)
        corrupt_mirror.write_bytes(b"not-a-valid-gzip-archive")

        result = maintain_retention(
            self.database, self.policy, now=self.now, mirror_storage_dir=mirror_root)

        self.assertEqual(result["retired_raw_archive_parts"], 0)
        self.assertEqual(result["mirror_failures"], 2)
        self.assertEqual(result["overdue_archive_parts"], 2)
        self.assertTrue(Path(paths["2026-07-16"]).exists())
        self.assertTrue(Path(paths["2026-07-17"]).exists())
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(read_raw_evidence(connection, missing_id), b"missing-mirror")
            self.assertEqual(read_raw_evidence(connection, corrupt_id), b"corrupt-mirror")
            health = connection.execute(
                "SELECT status,detail FROM monitoring_storage_health WHERE storage_key='raw_archive'"
            ).fetchone()
        self.assertEqual(health[0], "degraded")
        self.assertIn("overdue=2", health[1])

    def test_formal_business_result_keeps_source_identity_and_dedup_key(self):
        raw_id = self._insert_raw(
            "2026-07-17T10:00:00+00:00", b"formal-source", logical_key="formal-key")
        with closing(sqlite3.connect(self.database)) as connection:
            schedule_id = connection.execute(
                """INSERT INTO monitoring_business_schedules(
                       endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,
                       effective_from)
                   VALUES (?,'0311','Asia/Shanghai',14400,'00:00:00',
                           '2020-01-01T00:00:00+00:00')""",
                (self.endpoint_id,),
            ).lastrowid
            batch_id = connection.execute(
                """INSERT INTO observation_batches(
                       raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                       reported_at,observed_at,received_at,granularity,aggregation_source,
                       idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                    VALUES (?,?,1,32,1,'2026-07-17T10:00:00+00:00','2026-07-17T10:00:00+00:00',
                            '2026-07-17T10:00:00+00:00','realtime','device_reported','formal-key',
                            'test','accepted','completed','2026-07-17T10:00:01+00:00')""",
                (raw_id, self.endpoint_id),
            ).lastrowid
            value_id = connection.execute(
                """INSERT INTO observation_values(
                       observation_batch_id,protocol_code,business_metric,standard_value,
                       standard_unit,quality,parser_version,is_published)
                   VALUES (?,'0311','water_temp',20.0,'degC','valid','test',1)""",
                (batch_id,),
            ).lastrowid
            connection.execute(
                """INSERT INTO monitoring_business_observations(
                       source_observation_value_id,observation_batch_id,schedule_id,endpoint_id,
                       business_site_id,protocol_code,business_metric,scheduled_at,observed_at,
                       received_at,standard_value,standard_unit,quality,timeliness,slot_state,
                       projection_version)
                    VALUES (?,?,?,?,1,'0311','water_temp','2026-07-17T08:00:00+00:00',
                            '2026-07-17T10:00:00+00:00','2026-07-17T10:00:00+00:00',20.0,
                           'degC','valid','late','selected','test')""",
                (value_id, batch_id, schedule_id, self.endpoint_id),
            )
            connection.commit()
        archive_raw_day(self.database, "2026-07-17", self.policy)
        with closing(sqlite3.connect(self.database)) as connection:
            local_path = Path(connection.execute(
                "SELECT archive_path FROM monitoring_raw_archive_parts"
            ).fetchone()[0])
        mirror_root = self.root / "mirror"
        mirror_root.mkdir()
        self._copy_archive_to_mirror(local_path, mirror_root)

        result = maintain_retention(
            self.database, self.policy, now=self.now, mirror_storage_dir=mirror_root)

        self.assertEqual(result["deleted_raw_frames"], 0)
        self.assertEqual(result["retained_protected_frames"], 1)
        self.assertEqual(result["retired_raw_archive_parts"], 1)
        self.assertFalse(local_path.exists())
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (raw_id,)
            ).fetchone())
            self.assertEqual(connection.execute(
                """SELECT protected,expires_at FROM station_ingest_dedup_keys
                   WHERE logical_key_sha256='formal-key'"""
            ).fetchone(), (1, None))
            self.assertEqual(read_raw_evidence(connection, raw_id), b"formal-source")

    def test_legacy_archive_backfill_survives_raw_identity_cleanup(self):
        payload = b"legacy-archive-frame"
        raw_id = self._insert_raw(
            "2026-09-01T00:00:00+00:00", payload, logical_key="legacy-key")
        digest = hashlib.sha256(payload).hexdigest()
        item = {
            "id": raw_id,
            "received_at": "2026-09-01T00:00:00+00:00",
            "sha256": digest,
            "frame_b64": base64.b64encode(payload).decode("ascii"),
        }
        line = (json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        path = self.root / "legacy.jsonl.gz"
        with gzip.open(path, "wb") as stream:
            stream.write(line)
        with closing(sqlite3.connect(self.database)) as connection:
            part_id = connection.execute(
                """INSERT INTO monitoring_raw_archive_parts(
                       archive_date,archive_path,content_sha256,frame_count,created_at)
                   VALUES ('2026-09-01',?,?,1,'2026-09-02T00:00:00+00:00')""",
                (str(path), hashlib.sha256(line).hexdigest()),
            ).lastrowid
            connection.execute(
                """INSERT INTO monitoring_raw_archive_part_frames(
                       raw_frame_id,archive_part_id,frame_sha256) VALUES (?,?,?)""",
                (raw_id, part_id, digest),
            )
            connection.execute("UPDATE ingest_raw_frames SET raw_frame=X'' WHERE id=?", (raw_id,))
            connection.commit()

        self.assertEqual(backfill_archive_evidence_refs(self.database), 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT format_version FROM monitoring_raw_archive_part_metadata"
            ).fetchone()[0], 1)
            connection.execute("DELETE FROM monitoring_raw_archive_part_frames WHERE raw_frame_id=?", (raw_id,))
            connection.execute("DELETE FROM ingest_frame_protocols WHERE raw_frame_id=?", (raw_id,))
            connection.execute("DELETE FROM ingest_raw_frames WHERE id=?", (raw_id,))
            connection.commit()
            self.assertEqual(read_raw_evidence(connection, raw_id), payload)

    def test_detail_archive_failure_deletes_no_source_rows(self):
        raw_id = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"detail-failure", logical_key="detail-key")
        with closing(sqlite3.connect(self.database)) as connection:
            attempt_id = connection.execute(
                """INSERT INTO ingest_parse_attempts(
                       raw_frame_id,parser_version,parse_status,created_at)
                   VALUES (?,'test','parsed_header','2026-10-01T08:00:01+00:00')""",
                (raw_id,),
            ).lastrowid
            connection.commit()
        archive_raw_day(self.database, "2026-10-01", self.policy)

        with mock.patch.object(
            retention, "_write_detail_archive", side_effect=RetentionError("injected failure")
        ), self.assertRaisesRegex(RetentionError, "injected failure"):
            retention._archive_detail_batch(
                self.database, self.root, "parse_attempt",
                "2026-10-13T12:00:00+00:00", batch_limit=100,
            )

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_parse_attempts WHERE id=?", (attempt_id,)
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_detail_archive_parts"
            ).fetchone()[0], 0)

    def test_r25_indexes_remain_after_startup_and_reduction_is_disabled(self):
        self._insert_raw(
            "2026-10-16T11:00:00+00:00", b"recent", logical_key="recent-key")
        prepare_cutover(self.database, self.root / "backups", now=self.now)
        with self.assertRaisesRegex(RetentionError, "r25 remains the rollback version"):
            reduce_legacy_indexes(self.database, now=self.now)

        with closing(sqlite3.connect(self.database)) as connection:
            indexes = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue(set(retention.LEGACY_RAW_INDEXES) <= indexes)
            self.assertIn("idx_ingest_raw_logical_key", indexes)
            self.assertIn("idx_ingest_raw_authenticated_endpoint_received", indexes)
            self.assertIn("idx_ingest_raw_received", indexes)
            self.assertIn("idx_ingest_raw_pending_normalization", indexes)
            expected_factor_plan = connection.execute(
                """EXPLAIN QUERY PLAN SELECT raw.id
                   FROM ingest_raw_frames raw
                        INDEXED BY idx_ingest_raw_authenticated_endpoint_received
                   JOIN ingest_frame_protocols protocol ON protocol.raw_frame_id=raw.id
                   WHERE raw.endpoint_id=? AND raw.authentication_status='authenticated'
                     AND protocol.protocol_family='hj212'
                   ORDER BY raw.id DESC LIMIT 200""",
                (self.endpoint_id,),
            ).fetchall()
            expected_factor_detail = " ".join(str(row[3]) for row in expected_factor_plan)
            self.assertIn("idx_ingest_raw_authenticated_endpoint_received", expected_factor_detail)
            self.assertNotIn("SCAN raw", expected_factor_detail)
            dedup_plan = connection.execute(
                """EXPLAIN QUERY PLAN SELECT id FROM ingest_raw_frames
                   WHERE endpoint_id=? AND logical_key_sha256=?
                     AND duplicate_of_raw_frame_id IS NULL ORDER BY id LIMIT 1""",
                (self.endpoint_id, "recent-key"),
            ).fetchall()
            self.assertIn(
                "idx_ingest_raw_logical_key",
                " ".join(str(row[3]) for row in dedup_plan),
            )
            plan = connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT endpoint_id,latest_authenticated_at
                   FROM station_ingest_current_state
                        INDEXED BY idx_station_ingest_current_endpoint
                   WHERE endpoint_id=? AND latest_authenticated_at IS NOT NULL""",
                (self.endpoint_id,),
            ).fetchall()
            self.assertIn(
                "idx_station_ingest_current_endpoint",
                " ".join(str(row[3]) for row in plan),
            )

    def test_compact_prepared_writes_one_final_copy_without_modifying_source(self):
        payload = b"source-must-remain"
        raw_id = self._insert_raw(
            "2026-10-01T08:00:00+00:00", payload, logical_key="compact-key")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO ingest_parse_attempts(
                       raw_frame_id,parser_version,parse_status,created_at)
                   VALUES (?,'test','parsed_header','2026-10-01T08:00:01+00:00')""",
                (raw_id,),
            )
            connection.commit()
        target = self.root / "compacted.db"

        prepare_cutover(self.database, self.root / "backups", now=self.now)
        original_connect = sqlite3.connect
        statements = []

        def traced_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with mock.patch.object(sqlite3, "connect", side_effect=traced_connect):
            result = compact_prepared_database(self.database, target)

        self.assertFalse(result["source_modified"])
        self.assertEqual(sum(
            statement.strip().upper().startswith("PRAGMA QUICK_CHECK")
            for statement in statements
        ), 1)
        self.assertEqual(sum(
            statement.strip().upper().startswith("PRAGMA INTEGRITY_CHECK")
            for statement in statements
        ), 0)
        with closing(sqlite3.connect(self.database)) as source:
            self.assertEqual(source.execute(
                "SELECT raw_frame FROM ingest_raw_frames WHERE id=?", (raw_id,)
            ).fetchone()[0], payload)
        with closing(sqlite3.connect(target)) as compacted:
            self.assertIsNotNone(compacted.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (raw_id,)
            ).fetchone())
            self.assertEqual(compacted.execute("PRAGMA quick_check").fetchone()[0], "ok")
        with self.assertRaisesRegex(RetentionError, "compact-copy is retired"):
            compact_isolated_copy(
                self.database, self.root / "legacy.db", self.policy,
                now=self.now, batch_limit=100)

    def test_periodic_runner_skips_historical_governance_without_retry(self):
        prepare_cutover(self.database, self.root / "backups", now=self.now)
        lock_file = self.root / "maintenance.lock"
        expected = {
            "status": "skipped",
            "reason": (
                "historical station-ingestion maintenance is disabled for this release; "
                "run it only after separate approval"
            ),
            "historical_maintenance_called": 0,
            "retry_required": False,
        }
        with mock.patch.object(
            retention, "maintain_retention",
            side_effect=AssertionError("scheduled entry must not run governance"),
        ) as maintained, mock.patch.object(
            retention, "MaintenanceLock",
            side_effect=AssertionError("disabled entry must not acquire the governance lock"),
        ) as locked, mock.patch.object(
            retention, "_record_periodic_failure",
            side_effect=AssertionError("a normal skip must not be recorded as a failure"),
        ) as failed:
            self.assertEqual(run_periodic_maintenance(
                self.database, self.policy, lock_file=lock_file, now=self.now), expected)
        maintained.assert_not_called()
        locked.assert_not_called()
        failed.assert_not_called()
        self.assertFalse(lock_file.exists())
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)

        stdout = io.StringIO()
        with mock.patch.object(
            retention, "maintain_retention",
            side_effect=AssertionError("CLI must not run governance"),
        ), redirect_stdout(stdout):
            exit_code = retention.main([
                "maintain-once", "--database", str(self.database),
                "--raw-storage-dir", str(self.root), "--storage-mode", "validation",
            ])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "maintain-once")
        self.assertEqual(payload["result"], expected)


if __name__ == "__main__":
    unittest.main()
