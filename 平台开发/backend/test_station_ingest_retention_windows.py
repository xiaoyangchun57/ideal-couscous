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
import time
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

    def test_published_observation_without_business_row_survives_retention(self):
        self._assert_observation_retention('valid')

    def test_suspect_published_observation_and_source_survive_retention(self):
        self._assert_observation_retention('suspect')

    def test_invalid_unreferenced_observation_can_be_retired(self):
        self._assert_observation_retention('invalid')

    def _assert_observation_retention(self, quality):
        self._configure_hj212_ph()
        payload = b'published-observation-source'
        raw_id = self._insert_raw('2026-10-01T08:00:00+00:00', payload)
        with closing(sqlite3.connect(self.database)) as db:
            batch = db.execute("""INSERT INTO observation_batches(
                raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                reported_at,observed_at,received_at,granularity,aggregation_source,
                idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                VALUES (?,?,1,2011,1,'2026-10-01T08:00:00+00:00','2026-10-01T08:00:00+00:00',
                '2026-10-01T08:00:00+00:00','realtime','device_reported','published-test',
                'test','accepted','completed','2026-10-01T08:00:01+00:00')""",
                (raw_id,self.endpoint_id)).lastrowid
            db.execute("""INSERT INTO observation_values(observation_batch_id,protocol_code,
                business_metric,raw_value_text,standard_value,standard_unit,quality,parser_version,is_published)
                VALUES (?,'HJ212:w01001','ph','7.100',7.1,'pH',?,'test',1)""", (batch,quality))
            db.commit()
        import station_monitoring
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            before = station_monitoring.result_trend_page(db,1,'ph',
                '2026-10-01T00:00:00+00:00','2026-10-02T00:00:00+00:00')[0]
            self.assertEqual(len(before),int(quality == 'valid'))
        result = maintain_retention(self.database,self.policy,now=self.now,
                                    batch_limit=10,time_budget_seconds=30)
        keep = quality in ('valid','suspect')
        self.assertEqual(result['deleted_raw_frames'],int(not keep))
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            after = station_monitoring.result_trend_page(db,1,'ph',
                '2026-10-01T00:00:00+00:00','2026-10-02T00:00:00+00:00')[0]
            self.assertEqual(after,before)
            self.assertEqual(read_raw_evidence(db,raw_id),payload)
            self.assertEqual(retention._protected_raw_ids(db,[raw_id]),{raw_id} if keep else set())
            value = db.execute('SELECT quality,raw_value_text FROM observation_values WHERE observation_batch_id=?',(batch,)).fetchone()
            self.assertEqual(tuple(value) if value else None,(quality,'7.100') if keep else None)

    def test_periodic_skips_incomplete_legacy_archive_and_cleans_independent_rows(self):
        legacy = self._insert_raw('2026-09-01T00:00:00+00:00',b'legacy')
        disposable = self._insert_raw('2026-10-01T00:00:00+00:00',b'disposable')
        with closing(sqlite3.connect(self.database)) as db:
            part = db.execute("""INSERT INTO monitoring_raw_archive_parts(
                archive_date,archive_path,content_sha256,frame_count,created_at)
                VALUES ('2026-09-01',?,'unknown',1000000,'2026-09-02T00:00:00+00:00')""",
                (str(self.root/'unavailable-legacy.gz'),)).lastrowid
            db.execute("INSERT INTO monitoring_raw_archive_part_frames(raw_frame_id,archive_part_id,frame_sha256) VALUES (?,?,'unknown')",(legacy,part))
            db.execute("UPDATE ingest_raw_frames SET raw_frame=X'' WHERE id=?",(legacy,))
            db.commit()
        with mock.patch.object(retention,'backfill_archive_evidence_refs',
                               side_effect=AssertionError('periodic must not unpack legacy archives')):
            result=maintain_retention(self.database,self.policy,now=self.now,
                                     batch_limit=10,time_budget_seconds=30)
        self.assertEqual(result['deleted_raw_frames'],1)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM ingest_raw_frames WHERE id=?',(legacy,)).fetchone())
            self.assertIsNotNone(db.execute('SELECT 1 FROM monitoring_raw_archive_part_frames WHERE raw_frame_id=?',(legacy,)).fetchone())
            self.assertIsNone(db.execute('SELECT 1 FROM ingest_raw_frames WHERE id=?',(disposable,)).fetchone())
            self.assertEqual(read_raw_evidence(db,disposable),b'disposable')

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

    def _legacy_part(self, part_id, *, count=1, corrupt=False):
        path = self.root / f'legacy-{part_id}.jsonl.gz'
        lines, ids = [], []
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("""INSERT INTO monitoring_raw_archive_parts
                (id,archive_date,archive_path,content_sha256,frame_count,created_at)
                VALUES (?,'2026-10-01',?,'pending',?,'2026-10-02T00:00:00+00:00')""",
                (part_id,str(path),count))
            for index in range(count):
                payload = f'part-{part_id}-record-{index}-value=7.100'.encode()
                digest = hashlib.sha256(payload).hexdigest()
                raw_id = db.execute("""INSERT INTO ingest_raw_frames(endpoint_id,station_code,
                    received_at,frame_sha256,raw_frame,body_length,crc_status,authentication_status,
                    disposition,persistence_state) VALUES (?,'RETENTION-MN',
                    '2026-10-01T08:00:00+00:00',?,X'',?,'valid','authenticated','accepted','persisted')""",
                    (self.endpoint_id,digest,len(payload))).lastrowid
                db.execute('INSERT INTO monitoring_raw_archive_part_frames(raw_frame_id,archive_part_id,frame_sha256) VALUES (?,?,?)',
                           (raw_id,part_id,digest))
                item={'id':raw_id,'received_at':'2026-10-01T08:00:00+00:00',
                      'sha256':digest,'frame_b64':base64.b64encode(payload).decode()}
                lines.append((json.dumps(item,sort_keys=True)+'\n').encode('ascii'))
                ids.append(raw_id)
            content=b''.join(lines)
            db.execute('UPDATE monitoring_raw_archive_parts SET content_sha256=? WHERE id=?',
                       ('bad' if corrupt else hashlib.sha256(content).hexdigest(),part_id))
            db.commit()
        with gzip.open(path,'wb') as stream:
            stream.write(content)
        return path,ids

    def test_exact_legacy_part_skips_earlier_archive_and_reentry_is_noop(self):
        earlier,early_ids=self._legacy_part(1)
        target,ids=self._legacy_part(23)
        original_open=gzip.open
        def only_target(path,*args,**kwargs):
            self.assertEqual(Path(path),target)
            return original_open(path,*args,**kwargs)
        with mock.patch.object(retention.gzip,'open',side_effect=only_target):
            self.assertEqual(backfill_archive_evidence_refs(self.database,part_id=23),1)
        with closing(sqlite3.connect(self.database)) as db:
            before=list(db.iterdump())
            self.assertEqual(db.execute('SELECT archive_part_id FROM monitoring_raw_archive_part_metadata').fetchall(),[(23,)])
            self.assertEqual(read_raw_evidence(db,ids[0]),b'part-23-record-0-value=7.100')
            self.assertEqual(db.execute('SELECT received_at FROM ingest_raw_frames WHERE id=?',(ids[0],)).fetchone()[0],
                             '2026-10-01T08:00:00+00:00')
        with mock.patch.object(retention.gzip,'open',side_effect=AssertionError('completed target must not reopen')):
            self.assertEqual(backfill_archive_evidence_refs(self.database,part_id=23),0)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(list(db.iterdump()),before)
        self.assertTrue(earlier.exists())
        self.assertEqual(backfill_archive_evidence_refs(self.database,part_limit=1),1)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(read_raw_evidence(db,early_ids[0]),b'part-1-record-0-value=7.100')

    def test_exact_legacy_part_digest_failure_and_missing_target_write_nothing(self):
        self._legacy_part(1)
        self._legacy_part(23,corrupt=True)
        with closing(sqlite3.connect(self.database)) as db:
            before=list(db.iterdump())
        for part_id,message in ((23,'verification failed'),(99,'does not exist')):
            with self.assertRaisesRegex(RetentionError,message):
                backfill_archive_evidence_refs(self.database,part_id=part_id)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(list(db.iterdump()),before)

    def test_exact_legacy_part_context_query_respects_sql_variable_limit(self):
        self._legacy_part(1)
        self._legacy_part(23,count=501)
        original_connect=sqlite3.connect
        def limited(*args,**kwargs):
            db=original_connect(*args,**kwargs)
            db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,500)
            return db
        with mock.patch.object(retention.sqlite3,'connect',side_effect=limited):
            self.assertEqual(backfill_archive_evidence_refs(self.database,part_id=23),501)

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

    def test_bounded_batches_stop_and_reenter_until_complete(self):
        with closing(sqlite3.connect(self.database)) as connection:
            for raw_id, received_at in (
                (901, "2026-10-01T10:00:00+00:00"),
                (902, "2026-10-01T10:01:00+00:00"),
            ):
                connection.execute(
                    """INSERT INTO station_ingest_recent_receipts(
                           raw_frame_id,source_key,endpoint_id,station_code,received_at,
                           authentication_status)
                       VALUES (?,?,?,?,?,'authenticated')""",
                    (raw_id, f"endpoint:{self.endpoint_id}", self.endpoint_id,
                     "RETENTION-MN", received_at),
                )
            connection.commit()

        first = maintain_retention(
            self.database, self.policy, now=self.now, batch_limit=1)
        self.assertEqual(first["status"], "partial")
        self.assertTrue(first["retry_required"])
        self.assertEqual(first["deleted_recent_receipts"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_recent_receipts").fetchone()[0], 1)

        second = maintain_retention(
            self.database, self.policy, now=self.now, batch_limit=1)
        third = maintain_retention(
            self.database, self.policy, now=self.now, batch_limit=1)
        self.assertEqual(second["deleted_recent_receipts"], 1)
        self.assertEqual(third["status"], "completed")
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_recent_receipts").fetchone()[0], 0)

    def test_time_budget_stops_before_mutation_and_later_run_reenters(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO station_ingest_recent_receipts(
                       raw_frame_id,source_key,endpoint_id,station_code,received_at,
                       authentication_status)
                   VALUES (903,?,?,?,?, 'authenticated')""",
                (f"endpoint:{self.endpoint_id}", self.endpoint_id, "RETENTION-MN",
                 "2026-10-01T10:00:00+00:00"),
            )
            connection.commit()
        ticks = iter([0.0])
        with mock.patch.object(
            retention.time, "monotonic", side_effect=lambda: next(ticks, 2.0)
        ):
            stopped = maintain_retention(
                self.database, self.policy, now=self.now,
                batch_limit=10, time_budget_seconds=1)
        self.assertEqual(stopped["status"], "partial")
        self.assertIn("time_budget_exhausted", stopped["partial_reasons"])
        self.assertFalse(stopped["backlog_scan_completed"])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_recent_receipts").fetchone()[0], 1)

        resumed = maintain_retention(
            self.database, self.policy, now=self.now, batch_limit=10)
        self.assertEqual(resumed["deleted_recent_receipts"], 1)

    def test_raw_prune_sparse_matches_use_fixed_scan_windows(self):
        raw_ids = []
        old_ids = []
        for index in range(40):
            is_old = index % 4 == 0
            received_at = (f"2026-10-01T08:{index:02d}:00+00:00" if is_old
                           else f"2026-10-16T08:{index:02d}:00+00:00")
            raw_id = self._insert_raw(received_at, f"sparse-{index}".encode())
            raw_ids.append(raw_id)
            if is_old:
                old_ids.append(raw_id)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_parse_attempts(
                       raw_frame_id,parser_version,parse_status,created_at)
                   VALUES (?,'test','parsed_header','2026-10-01T09:00:00+00:00')""",
                ((raw_id,) for raw_id in old_ids if raw_id != raw_ids[32]),
            )
            connection.commit()
        archive_raw_day(self.database, "2026-10-01", self.policy, batch_limit=100)
        cutoff = "2026-10-09T12:00:00+00:00"

        first = retention._delete_unprotected_raw_batch(
            self.database, cutoff, now=self.now.isoformat(), batch_limit=1, scan_limit=16)
        second = retention._delete_unprotected_raw_batch(
            self.database, "2026-10-10T12:00:00+00:00",
            now=self.now.isoformat(), batch_limit=1, scan_limit=16)
        third = retention._delete_unprotected_raw_batch(
            self.database, cutoff, now=self.now.isoformat(), batch_limit=1, scan_limit=16)

        self.assertEqual(first[0], 0)
        self.assertEqual(first[2]["cursor"], raw_ids[15])
        self.assertEqual(first[2]["scanned"], 16)
        self.assertEqual(second[2]["cursor"], raw_ids[31])
        self.assertEqual(second[2]["scanned"], 32)
        self.assertEqual(second[2]["cutoff"], cutoff)
        self.assertEqual(third[0], 1)
        self.assertEqual(third[2]["cursor"], raw_ids[32])
        self.assertEqual(third[2]["stop_reason"], "delete_batch_limit")
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (raw_ids[32],)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (raw_ids[33],)
            ).fetchone())

    def test_completed_raw_checkpoint_blocks_historical_resume_and_starts_new_window(self):
        first_raw_id = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"completed-window-old")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO ingest_parse_attempts(
                       raw_frame_id,parser_version,parse_status,created_at)
                   VALUES (?,'test','parsed_header','2026-10-01T08:00:01+00:00')""",
                (first_raw_id,),
            )
            for detail in (
                {"raw_prune_checkpoint": {
                    "cutoff": "2026-10-09T12:00:00+00:00", "cursor": 0,
                    "upper_raw_id": first_raw_id, "scanned": 0, "deleted": 0,
                    "retained": 0, "stop_reason": "scan_window_limit",
                }},
                {"status": "completed_without_raw_stage"},
                {"raw_prune_checkpoint": {
                    "cutoff": "2026-10-09T12:00:00+00:00", "cursor": first_raw_id,
                    "upper_raw_id": first_raw_id, "scanned": 1, "deleted": 0,
                    "retained": 0, "stop_reason": "upper_bound_reached",
                }},
                {"status": "later_run_without_checkpoint"},
            ):
                connection.execute(
                    """INSERT INTO station_ingest_retention_runs(
                           cutoff_time,archived_parse_attempts,archived_errors,
                           archived_error_summaries,deleted_raw_frames,
                           retained_protected_frames,status,detail,created_at)
                       VALUES ('2026-10-09T12:00:00+00:00',0,0,0,0,0,'completed',?,
                               '2026-10-27T00:00:00+00:00')""",
                    (json.dumps(detail, sort_keys=True),),
                )
            connection.commit()
            self.assertIsNone(retention._latest_raw_prune_checkpoint(connection))

        new_raw_id = self._insert_raw(
            "2026-10-01T09:00:00+00:00", b"new-window-candidate")
        archive_raw_day(self.database, "2026-10-01", self.policy)
        new_cutoff = "2026-10-10T12:00:00+00:00"
        result = retention._delete_unprotected_raw_batch(
            self.database, new_cutoff, now=self.now.isoformat(), batch_limit=1)

        self.assertEqual(result[0], 1)
        self.assertEqual(result[2]["cutoff"], new_cutoff)
        self.assertEqual(result[2]["upper_raw_id"], new_raw_id)
        self.assertEqual(result[2]["cursor"], new_raw_id)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (first_raw_id,)
            ).fetchone())
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (new_raw_id,)
            ).fetchone())

    def test_raw_prune_deadline_rolls_back_and_reentry_does_not_skip(self):
        raw_id = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"deadline-rollback", logical_key="deadline-key")
        archive_raw_day(self.database, "2026-10-01", self.policy)
        original_protect = retention._protect_active_dedup_keys

        def expire_inside_write(connection, raw_ids, protected_ids):
            connection.execute(
                "UPDATE ingest_raw_frames SET station_code='MUTATED' WHERE id=?", (raw_id,))
            time.sleep(0.06)
            connection.execute(
                """WITH RECURSIVE counter(value) AS (
                       SELECT 1 UNION ALL SELECT value+1 FROM counter WHERE value<1000000)
                   SELECT SUM(value) FROM counter"""
            ).fetchone()
            return original_protect(connection, raw_ids, protected_ids)

        with mock.patch.object(
            retention, "_protect_active_dedup_keys", side_effect=expire_inside_write
        ):
            with self.assertRaisesRegex(
                retention._StatementDeadlineExceeded,
                "statement_deadline_exhausted:raw_write_revalidation",
            ) as stopped:
                retention._delete_unprotected_raw_batch(
                    self.database, "2026-10-09T12:00:00+00:00",
                    now=self.now.isoformat(), batch_limit=1,
                    deadline=time.monotonic() + 0.05,
                )
        self.assertEqual(stopped.exception.checkpoint["cursor"], 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT station_code FROM ingest_raw_frames WHERE id=?", (raw_id,)
            ).fetchone()[0], "RETENTION-MN")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)

        resumed = retention._delete_unprotected_raw_batch(
            self.database, "2026-10-09T12:00:00+00:00",
            now=self.now.isoformat(), batch_limit=1)
        self.assertEqual(resumed[0], 1)
        self.assertEqual(resumed[2]["cursor"], raw_id)

    def test_raw_prune_deadline_keeps_committed_earlier_archive_stage(self):
        raw_id = self._insert_raw("2026-10-01T08:00:00+00:00", b"earlier-stage")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO ingest_parse_attempts(
                       raw_frame_id,parser_version,parse_status,created_at)
                   VALUES (?,'test','parsed_header','2026-10-01T08:00:01+00:00')""",
                (raw_id,),
            )
            connection.commit()
        checkpoint = {
            "cutoff": "2026-10-09T12:00:00+00:00", "cursor": 0,
            "upper_raw_id": raw_id, "scanned": 0, "deleted": 0,
            "retained": 0, "stop_reason": "statement_deadline_exhausted:raw_checkpoint_load",
        }
        interruption = retention._StatementDeadlineExceeded(
            "raw_checkpoint_load", checkpoint)

        with mock.patch.object(
            retention, "_delete_unprotected_raw_batch", side_effect=interruption
        ):
            result = maintain_retention(
                self.database, self.policy, now=self.now, batch_limit=10,
                time_budget_seconds=30,
            )

        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["retry_required"])
        self.assertIn(
            "statement_deadline_exhausted:raw_checkpoint_load", result["partial_reasons"])
        self.assertEqual(result["archived_parse_attempts"], 1)
        self.assertEqual(result["raw_prune_checkpoint"], checkpoint)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_parse_attempts WHERE raw_frame_id=?", (raw_id,)
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                """SELECT COUNT(*) FROM station_ingest_detail_archive_parts
                   WHERE detail_kind='parse_attempt'"""
            ).fetchone()[0], 1)

    def test_raw_prune_non_deadline_operational_error_remains_failure(self):
        raw_id = self._insert_raw("2026-10-01T08:00:00+00:00", b"operational-error")
        archive_raw_day(self.database, "2026-10-01", self.policy)
        original = retention._protected_raw_ids
        calls = 0

        def fail_write_revalidation(connection, raw_ids):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("disk I/O error")
            return original(connection, raw_ids)

        with mock.patch.object(
            retention, "_protected_raw_ids", side_effect=fail_write_revalidation
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "disk I/O error"):
                retention._delete_unprotected_raw_batch(
                    self.database, "2026-10-09T12:00:00+00:00",
                    now=self.now.isoformat(), batch_limit=1,
                    deadline=time.monotonic() + 10,
                )
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (raw_id,)
            ).fetchone())

    def test_raw_prune_read_phase_does_not_block_receiver_insert(self):
        old_id = self._insert_raw("2026-10-01T08:00:00+00:00", b"old-reader")
        archive_raw_day(self.database, "2026-10-01", self.policy)
        original = retention._protected_raw_ids
        inserted = []

        def insert_during_read(connection, raw_ids):
            if not inserted:
                inserted.append(self._insert_raw(
                    "2026-10-16T11:59:00+00:00", b"receiver-continues"))
            return original(connection, raw_ids)

        with mock.patch.object(
            retention, "_protected_raw_ids", side_effect=insert_during_read
        ):
            result = retention._delete_unprotected_raw_batch(
                self.database, "2026-10-09T12:00:00+00:00",
                now=self.now.isoformat(), batch_limit=1)
        self.assertEqual(result[0], 1)
        self.assertEqual(result[2]["upper_raw_id"], old_id)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (inserted[0],)
            ).fetchone())

    def test_raw_prune_revalidates_protection_added_after_read(self):
        raw_id = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"late-protection", logical_key="late-protection")
        archive_raw_day(self.database, "2026-10-01", self.policy)
        original = retention._protected_raw_ids
        calls = 0

        def add_protection_before_write(connection, raw_ids):
            nonlocal calls
            calls += 1
            if calls == 2:
                connection.execute(
                    """INSERT INTO monitoring_quality_issues(
                           raw_frame_id,issue_type,object_summary,first_seen_at,last_seen_at,status)
                       VALUES (?,'late_review','added after read',?,?, 'open')""",
                    (raw_id, "2026-10-01T09:00:00+00:00", "2026-10-01T09:00:00+00:00"),
                )
            return original(connection, raw_ids)

        with mock.patch.object(
            retention, "_protected_raw_ids", side_effect=add_protection_before_write
        ):
            result = retention._delete_unprotected_raw_batch(
                self.database, "2026-10-09T12:00:00+00:00",
                now=self.now.isoformat(), batch_limit=1)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (raw_id,)
            ).fetchone())

    def test_raw_prune_all_protected_window_advances_checkpoint(self):
        raw_ids = [
            self._insert_raw(
                f"2026-10-01T08:{index:02d}:00+00:00", f"protected-{index}".encode(),
                logical_key=f"protected-{index}")
            for index in range(8)
        ]
        archive_raw_day(self.database, "2026-10-01", self.policy, batch_limit=20)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO monitoring_quality_issues(
                       raw_frame_id,issue_type,object_summary,first_seen_at,last_seen_at,status)
                   VALUES (?,'manual_review','protected',?,?, 'open')""",
                ((raw_id, "2026-10-01T09:00:00+00:00", "2026-10-01T09:00:00+00:00")
                 for raw_id in raw_ids),
            )
            connection.commit()

        result = retention._delete_unprotected_raw_batch(
            self.database, "2026-10-09T12:00:00+00:00",
            now=self.now.isoformat(), batch_limit=1, scan_limit=5)

        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 5)
        self.assertEqual(result[2]["cursor"], raw_ids[4])
        self.assertEqual(result[2]["scanned"], 5)
        self.assertEqual(result[2]["stop_reason"], "scan_window_limit")

    def test_periodic_runner_executes_governance_and_cli_reports_result(self):
        lock_file = self.root / "maintenance.lock"
        result = run_periodic_maintenance(
            self.database, self.policy, lock_file=lock_file, now=self.now,
            time_budget_seconds=10)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["historical_maintenance_called"], 1)
        self.assertFalse(result["retry_required"])
        self.assertTrue(lock_file.exists())
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 1)

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = retention.main([
                "maintain-once", "--database", str(self.database),
                "--raw-storage-dir", str(self.root), "--storage-mode", "governance",
                "--archive-minimum-total-bytes", "1",
                "--archive-minimum-free-bytes", "1",
                "--hot-minimum-total-bytes", "1",
                "--hot-minimum-free-bytes", "1",
                "--time-budget-seconds", "10",
            ])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "maintain-once")
        self.assertEqual(payload["result"]["historical_maintenance_called"], 1)
        self.assertEqual(payload["result"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
