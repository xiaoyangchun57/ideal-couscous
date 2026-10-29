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
    drop_duplicate_cleanup_indexes,
    ensure_duplicate_cleanup_indexes,
    maintain_retention,
    prepare_recent_slot_summaries,
    prepare_startup_state,
    purge_exact_duplicate_batch,
    read_raw_evidence,
    reduce_confirmed_minute_duplicates,
    reduce_legacy_indexes,
    run_exact_duplicate_cleanup_job,
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
        duplicate_of=None,
    ):
        digest = hashlib.sha256(payload).hexdigest()
        endpoint_id = self.endpoint_id if endpoint_id is None else endpoint_id
        station_code = station_code or "RETENTION-MN"
        with closing(sqlite3.connect(self.database)) as connection:
            raw_id = connection.execute(
                """INSERT INTO ingest_raw_frames(
                       endpoint_id,station_code,received_at,frame_sha256,logical_key_sha256,
                       raw_frame,body_length,crc_status,authentication_status,disposition,
                       duplicate_of_raw_frame_id,persistence_state)
                   VALUES (?,?,?,?,?,?,?,'valid',?,?,?,?)""",
                (endpoint_id, station_code, received_at, digest, logical_key,
                 payload, len(payload), authentication, disposition, duplicate_of, persistence),
            ).lastrowid
            connection.execute(
                """INSERT INTO ingest_frame_protocols(raw_frame_id,protocol_family,parser_version)
                   VALUES (?,'hj212','test-parser')""",
                (raw_id,),
            )
            connection.commit()
        return raw_id

    def _mark_startup_ready(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO station_ingest_cutover_state(
                       cutover_name,migration_version,status,detail,updated_at)
                   VALUES ('station-ingest-startup-ready-v1',
                           '20261016_014_station_ingest_retention_windows',
                           'completed','{}','2026-10-16T12:00:00+00:00')""")
            connection.commit()

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

    def test_exact_duplicate_cleanup_preserves_canonical_results_and_active_protection(self):
        self._mark_startup_ready()
        index_result = ensure_duplicate_cleanup_indexes(self.database)
        self.assertEqual(set(index_result["created"]), set(retention.CLEANUP_INDEXES))
        self.assertTrue(any("INTEGER PRIMARY KEY" in item
                            for item in index_result["query_plans"]["bounded_raw_scan"]))
        self.assertTrue(any("idx_ingest_errors_raw_cleanup" in item
                            for item in index_result["query_plans"]["open_error_protection"]))
        self.assertTrue(any("idx_ingest_raw_duplicate_cleanup" in item
                            for item in index_result["query_plans"]["canonical_reference_protection"]))

        old = "2026-10-01T08:00:00+00:00"
        canonical = self._insert_raw(old, b"same-frame", logical_key="same-key")
        removable = self._insert_raw(
            old, b"same-frame", logical_key="same-key", disposition="duplicate",
            duplicate_of=canonical)
        expired_receipt = self._insert_raw(
            old, b"same-frame", logical_key="same-key", disposition="duplicate",
            duplicate_of=canonical)
        active_receipt = self._insert_raw(
            old, b"same-frame", logical_key="same-key", disposition="duplicate",
            duplicate_of=canonical)
        published = self._insert_raw(
            old, b"same-frame", logical_key="same-key", disposition="duplicate",
            duplicate_of=canonical)
        changed_frame = self._insert_raw(
            old, b"corrected-frame", logical_key="same-key", disposition="duplicate",
            duplicate_of=canonical)
        changed_key = self._insert_raw(
            old, b"same-frame", logical_key="corrected-key", disposition="duplicate",
            duplicate_of=canonical)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO station_ingest_recent_receipts(
                       raw_frame_id,source_key,endpoint_id,station_code,received_at,
                       authentication_status) VALUES (?,?,?,?,?,'authenticated')""",
                (
                    (expired_receipt, f"endpoint:{self.endpoint_id}", self.endpoint_id,
                     "RETENTION-MN", "2026-10-10T00:00:00+00:00"),
                    (active_receipt, f"endpoint:{self.endpoint_id}", self.endpoint_id,
                     "RETENTION-MN", "2026-10-15T00:00:00+00:00"),
                ),
            )
            batch_id = connection.execute(
                """INSERT INTO observation_batches(
                       raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                       reported_at,observed_at,received_at,granularity,aggregation_source,
                       idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                   VALUES (?,?,1,2011,0,?,?,?,'realtime','device_reported',?,'test',
                           'accepted','completed',?)""",
                (published, self.endpoint_id, old, old, old,
                 f"published-{published}", old),
            ).lastrowid
            connection.execute(
                """INSERT INTO observation_values(
                       observation_batch_id,protocol_code,business_metric,standard_value,
                       standard_unit,quality,parser_version,is_published)
                   VALUES (?,'HJ212:w01001','ph',7.1,'pH','valid','test',1)""",
                (batch_id,),
            )
            connection.commit()

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=changed_key, batch_limit=20, scan_limit=20)

        self.assertTrue(result["complete"])
        self.assertEqual(result["receipts_deleted"], 1)
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(result["retained_protected"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            remaining = {int(row[0]) for row in connection.execute(
                "SELECT id FROM ingest_raw_frames")}
            self.assertIn(canonical, remaining)
            self.assertNotIn(removable, remaining)
            self.assertNotIn(expired_receipt, remaining)
            self.assertIn(active_receipt, remaining)
            self.assertIn(published, remaining)
            self.assertIn(changed_frame, remaining)
            self.assertIn(changed_key, remaining)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM observation_values WHERE observation_batch_id=?",
                (batch_id,),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_recent_receipts"
            ).fetchone()[0], 1)

    def test_exact_duplicate_cleanup_archives_historical_open_error_and_keeps_latest_problem(self):
        self._mark_startup_ready()
        self._configure_hj212_ph()
        ensure_duplicate_cleanup_indexes(self.database)
        old = "2026-10-01T08:00:00+00:00"
        canonical = self._insert_raw(old, b"error-pair", logical_key="error-pair")
        with closing(sqlite3.connect(self.database)) as connection:
            batch_id = connection.execute(
                """INSERT INTO observation_batches(
                       raw_frame_id,endpoint_id,business_site_id,function_code,serial_number,
                       reported_at,observed_at,received_at,granularity,aggregation_source,
                       idempotency_key,normalization_version,batch_status,projection_state,normalized_at)
                   VALUES (?,?,1,2011,0,?,?,?,'realtime','device_reported',?,'test',
                           'accepted','completed',?)""",
                (canonical, self.endpoint_id, old, old, old,
                 f"canonical-result-{canonical}", old),
            ).lastrowid
            connection.execute(
                """INSERT INTO observation_values(
                       observation_batch_id,protocol_code,business_metric,raw_value_text,
                       standard_value,standard_unit,quality,parser_version,is_published)
                   VALUES (?,'HJ212:w01001','ph','7.100',7.1,'pH','valid','test',1)""",
                (batch_id,),
            )
            connection.commit()
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"error-pair", logical_key="error-pair",
            disposition="duplicate", duplicate_of=canonical)
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"latest-problem", logical_key="latest-problem")
        with closing(sqlite3.connect(self.database)) as connection:
            historical_error = connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,error_detail,status,created_at)
                   VALUES (?,'hj212_non_data_command','','open',?)""",
                (duplicate, "2026-10-01T08:01:01+00:00"),
            ).lastrowid
            latest_error = connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,error_detail,status,created_at)
                   VALUES (?,'hj212_non_data_command','current problem representative','open',?)""",
                (latest_raw, "2026-10-15T08:00:01+00:00"),
            ).lastrowid
            connection.commit()
            connection.row_factory = sqlite3.Row
            import station_monitoring
            before = station_monitoring.result_trend_page(
                connection, 1, "ph", "2026-10-01T00:00:00+00:00",
                "2026-10-02T00:00:00+00:00")[0]

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
            archive_root=self.root)

        self.assertTrue(result["complete"])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["errors_archived"], 1)
        self.assertGreaterEqual(result["batch_elapsed_seconds"], 0)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            current = connection.execute(
                "SELECT status,error_detail FROM ingest_errors WHERE id=?", (latest_error,)
            ).fetchone()
            self.assertEqual(tuple(current), ("open", "current problem representative"))
            archived = retention.read_detail_archive_record(
                connection, "ingest_error", historical_error)
            self.assertEqual(archived["status"], "open")
            self.assertEqual(archived["error_detail"], "")
            self.assertEqual(archived["evidence_source_raw_frame_id"], canonical)
            self.assertEqual(
                base64.b64decode(archived["evidence"]["frame_b64"]), b"error-pair")
            after = station_monitoring.result_trend_page(
                connection, 1, "ph", "2026-10-01T00:00:00+00:00",
                "2026-10-02T00:00:00+00:00")[0]
            self.assertEqual(after, before)

        repeated = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
            archive_root=self.root)
        self.assertEqual(repeated["errors_archived"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                """SELECT COUNT(*) FROM station_ingest_detail_archive_parts
                   WHERE detail_kind='ingest_error'"""
            ).fetchone()[0], 1)

    def test_exact_duplicate_cleanup_without_old_archive_uses_online_evidence(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"no-evidence", logical_key="no-evidence")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"no-evidence", logical_key="no-evidence",
            disposition="duplicate", duplicate_of=canonical)
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"newer-error", logical_key="newer-error")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'decode_problem','open',?)""",
                ((duplicate, "2026-10-01T08:01:01+00:00"),
                 (latest_raw, "2026-10-15T08:00:01+00:00")),
            )
            connection.commit()

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
            archive_root=self.root)

        self.assertTrue(result["complete"])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["errors_archived"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_errors WHERE raw_frame_id=?", (duplicate,)
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_detail_archive_parts"
            ).fetchone()[0], 1)

    def test_exact_duplicate_cleanup_ignores_unreadable_old_archive_when_online_raw_is_valid(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"corrupt-evidence", logical_key="corrupt")
        archive_raw_day(self.database, "2026-10-01", self.policy)
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"corrupt-evidence", logical_key="corrupt",
            disposition="duplicate", duplicate_of=canonical)
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"current-error", logical_key="current-error")
        with closing(sqlite3.connect(self.database)) as connection:
            historical_error = connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'decode_problem','open',?)""",
                (duplicate, "2026-10-01T08:01:01+00:00"),
            ).lastrowid
            connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'decode_problem','open',?)""",
                (latest_raw, "2026-10-15T08:00:01+00:00"),
            )
            archive_path = Path(connection.execute(
                "SELECT archive_path FROM monitoring_raw_archive_parts"
            ).fetchone()[0])
            connection.commit()
        archive_path.write_bytes(b"not-a-valid-gzip-archive")

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
            archive_root=self.root)

        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            self.assertTrue(result["complete"])
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_errors WHERE raw_frame_id=?", (duplicate,)
            ).fetchone()[0], 0)
            archived = retention.read_detail_archive_record(
                connection, "ingest_error", historical_error)
            self.assertEqual(
                base64.b64decode(archived["evidence"]["frame_b64"]), b"corrupt-evidence")

    def test_exact_duplicate_cleanup_bounds_error_archive_and_uses_open_error_index(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"bulk-error", logical_key="bulk-error")
        duplicates = [
            self._insert_raw(
                "2026-10-01T08:01:00+00:00", b"bulk-error",
                logical_key="bulk-error", disposition="duplicate", duplicate_of=canonical)
            for _ in range(120)
        ]
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"latest-bulk-error",
            logical_key="latest-bulk-error")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open',?)""",
                ((raw_id, "2026-10-01T08:01:01+00:00") for raw_id in duplicates),
            )
            latest_error = connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open','2026-10-15T08:00:01+00:00')""",
                (latest_raw,),
            ).lastrowid
            connection.commit()

        results = []
        while not results or not results[-1]["complete"]:
            results.append(purge_exact_duplicate_batch(
                self.database, cutoff="2026-10-09T12:00:00+00:00",
                receipt_cutoff="2026-10-13T12:00:00+00:00",
                upper_raw_id=duplicates[-1], batch_limit=100, scan_limit=200,
                archive_root=self.root))

        self.assertEqual([result["batch_deleted"] for result in results], [100, 20])
        self.assertEqual(
            [result["batch_errors_archived"] for result in results], [100, 20])
        self.assertEqual(results[-1]["deleted"], 120)
        self.assertEqual(results[-1]["errors_archived"], 120)
        plans = results[0]["error_representative_query_plans"][
            "hj212_non_data_command"]
        self.assertTrue(any("idx_ingest_errors_open" in plan for plan in plans))
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_errors WHERE status='open'"
            ).fetchone()[0], 1)
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_errors WHERE id=?", (latest_error,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_detail_archive_parts"
            ).fetchone()[0], len(results))

    def test_exact_duplicate_cleanup_prefers_current_window_representative_across_sources(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"source-a", logical_key="source-a")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"source-a", logical_key="source-a",
            disposition="duplicate", duplicate_of=canonical)
        newer_a = self._insert_raw(
            "2026-10-02T08:00:00+00:00", b"newer-a", logical_key="newer-a")
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint_b = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('RETENTION-B','credential-b',1,'bound')"""
            ).lastrowid
            connection.commit()
        noisy_b = self._insert_raw(
            "2026-10-27T08:00:00+00:00", b"noisy-b", logical_key="noisy-b",
            endpoint_id=endpoint_b, station_code="RETENTION-B")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open',?)""",
                ((duplicate, "2026-10-01T08:01:01+00:00"),
                 (newer_a, "2026-10-02T08:00:01+00:00")),
            )
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open',?)""",
                ((noisy_b, f"2026-10-27T08:{index // 60:02d}:{index % 60:02d}+00:00")
                 for index in range(2001)),
            )
            connection.commit()

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-21T00:00:00+00:00",
            receipt_cutoff="2026-10-25T00:00:00+00:00",
            upper_raw_id=noisy_b, batch_limit=100, scan_limit=2000,
            archive_root=self.root)

        self.assertTrue(result["complete"])
        self.assertEqual(result["cursor"], noisy_b)
        self.assertEqual(result["batch_deleted"], 1)
        self.assertEqual(result["representative_pending_count"], 0)
        self.assertEqual(result["representative_search_rows"], 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (newer_a,)
            ).fetchone())

    def test_exact_duplicate_cleanup_resumes_representative_page_and_processes_other_source(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint_b = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('RETENTION-B','credential-b',1,'bound')"""
            ).lastrowid
            endpoint_c = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('RETENTION-C','credential-c',1,'bound')"""
            ).lastrowid
            connection.commit()
        canonical_a = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"page-a", logical_key="page-a")
        duplicate_a = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"page-a", logical_key="page-a",
            disposition="duplicate", duplicate_of=canonical_a)
        canonical_b = self._insert_raw(
            "2026-10-01T09:00:00+00:00", b"page-b", logical_key="page-b",
            endpoint_id=endpoint_b, station_code="RETENTION-B")
        duplicate_b = self._insert_raw(
            "2026-10-01T09:01:00+00:00", b"page-b", logical_key="page-b",
            disposition="duplicate", duplicate_of=canonical_b,
            endpoint_id=endpoint_b, station_code="RETENTION-B")
        newer_b = self._insert_raw(
            "2026-10-02T09:00:00+00:00", b"newer-b", logical_key="newer-b",
            endpoint_id=endpoint_b, station_code="RETENTION-B")
        noisy_c = self._insert_raw(
            "2026-10-27T08:00:00+00:00", b"noisy-c", logical_key="noisy-c",
            endpoint_id=endpoint_c, station_code="RETENTION-C")
        newer_a = self._insert_raw(
            "2026-10-02T08:00:00+00:00", b"newer-page-a", logical_key="newer-page-a")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open',?)""",
                ((duplicate_a, "2026-10-01T08:01:01+00:00"),
                 (duplicate_b, "2026-10-01T09:01:01+00:00"),
                 (newer_b, "2026-10-02T09:00:01+00:00"),
                 (newer_a, "2026-10-02T08:00:01+00:00")),
            )
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open',?)""",
                ((noisy_c, f"2026-10-27T08:{index // 60:02d}:{index % 60:02d}+00:00")
                 for index in range(2001)),
            )
            connection.commit()

        first = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-21T00:00:00+00:00",
            receipt_cutoff="2026-10-25T00:00:00+00:00",
            upper_raw_id=newer_b, batch_limit=100, scan_limit=2000,
            archive_root=self.root)

        self.assertFalse(first["complete"])
        self.assertEqual(first["cursor"], newer_b)
        self.assertEqual(first["batch_deleted"], 1)
        self.assertEqual(first["pending_raw_ids"], [duplicate_a])
        self.assertEqual(first["representative_search_rows"], 2000)
        self.assertTrue(first["representative_search"])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate_a,)
            ).fetchone())
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate_b,)
            ).fetchone())

        second = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-21T00:00:00+00:00",
            receipt_cutoff="2026-10-25T00:00:00+00:00",
            upper_raw_id=newer_b, batch_limit=100, scan_limit=2000,
            archive_root=self.root)

        self.assertTrue(second["complete"])
        self.assertEqual(second["deleted"], 2)
        self.assertEqual(second["pending_raw_ids"], [])
        self.assertEqual(second["representative_search"], {})
        self.assertLess(second["representative_search_rows"], 2000)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate_a,)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (newer_a,)
            ).fetchone())

    def test_exact_duplicate_cleanup_retains_missing_representative_and_continues_source(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            endpoint_b = connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES ('RETENTION-B','credential-b',1,'bound')"""
            ).lastrowid
            connection.commit()
        canonical_a = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"last-a", logical_key="last-a")
        duplicate_a = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"last-a", logical_key="last-a",
            disposition="duplicate", duplicate_of=canonical_a)
        canonical_b = self._insert_raw(
            "2026-10-01T09:00:00+00:00", b"last-b", logical_key="last-b",
            endpoint_id=endpoint_b, station_code="RETENTION-B")
        duplicate_b = self._insert_raw(
            "2026-10-01T09:01:00+00:00", b"last-b", logical_key="last-b",
            disposition="duplicate", duplicate_of=canonical_b,
            endpoint_id=endpoint_b, station_code="RETENTION-B")
        newer_b = self._insert_raw(
            "2026-10-02T09:00:00+00:00", b"newer-last-b", logical_key="newer-last-b",
            endpoint_id=endpoint_b, station_code="RETENTION-B")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open',?)""",
                ((duplicate_a, "2026-10-01T08:01:01+00:00"),
                 (duplicate_b, "2026-10-01T09:01:01+00:00"),
                 (newer_b, "2026-10-02T09:00:01+00:00")),
            )
            connection.commit()

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-21T00:00:00+00:00",
            receipt_cutoff="2026-10-25T00:00:00+00:00",
            upper_raw_id=newer_b, batch_limit=100, scan_limit=100,
            archive_root=self.root)

        self.assertTrue(result["complete"])
        self.assertEqual(result["batch_deleted"], 1)
        self.assertEqual(result["retained_error_representative_raw_ids"], [duplicate_a])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate_a,)
            ).fetchone())
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate_b,)
            ).fetchone())

    def test_exact_duplicate_cleanup_never_deletes_same_batch_representative(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"same-batch", logical_key="same-batch")
        older = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"same-batch", logical_key="same-batch",
            disposition="duplicate", duplicate_of=canonical)
        newer = self._insert_raw(
            "2026-10-02T08:01:00+00:00", b"same-batch", logical_key="same-batch",
            disposition="duplicate", duplicate_of=canonical)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open',?)""",
                ((older, "2026-10-01T08:01:01+00:00"),
                 (newer, "2026-10-02T08:01:01+00:00")),
            )
            connection.commit()

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-21T00:00:00+00:00",
            receipt_cutoff="2026-10-25T00:00:00+00:00",
            upper_raw_id=newer, batch_limit=100, scan_limit=100,
            archive_root=self.root)

        self.assertTrue(result["complete"])
        self.assertEqual(result["batch_deleted"], 1)
        self.assertEqual(result["retained_error_representative_raw_ids"], [newer])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (older,)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (newer,)
            ).fetchone())

    def test_exact_duplicate_cleanup_shares_scan_budget_across_error_groups(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"many-groups", logical_key="many-groups")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"many-groups", logical_key="many-groups",
            disposition="duplicate", duplicate_of=canonical)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,?,'open','2026-10-01T08:01:01+00:00')""",
                ((duplicate, f"bounded_group_{index:02d}") for index in range(11)),
            )
            connection.commit()

        results = []
        while not results or not results[-1]["complete"]:
            results.append(purge_exact_duplicate_batch(
                self.database, cutoff="2026-10-21T00:00:00+00:00",
                receipt_cutoff="2026-10-25T00:00:00+00:00",
                upper_raw_id=duplicate, batch_limit=1, scan_limit=5,
                archive_root=self.root))

        self.assertEqual(
            [result["representative_search_rows"] for result in results], [5, 5, 1])
        self.assertEqual(
            [result["representative_search_searched_groups"] for result in results],
            [5, 5, 1])
        self.assertEqual(results[0]["pending_raw_ids"], [duplicate])
        self.assertEqual(results[1]["pending_raw_ids"], [duplicate])
        self.assertEqual(results[2]["pending_raw_ids"], [])
        self.assertEqual(results[2]["retained_error_representative_raw_ids"], [duplicate])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())

    def test_exact_duplicate_cleanup_retains_last_open_error_representative(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"last-error", logical_key="last-error")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"last-error", logical_key="last-error",
            disposition="duplicate", duplicate_of=canonical)
        with closing(sqlite3.connect(self.database)) as connection:
            error_id = connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open','2026-10-01T08:01:01+00:00')""",
                (duplicate,),
            ).lastrowid
            connection.commit()

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
            archive_root=self.root)

        self.assertTrue(result["complete"])
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["retained_error_representative_raw_ids"], [duplicate])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_errors WHERE id=?", (error_id,)
            ).fetchone())

    def test_exact_duplicate_cleanup_without_online_or_archived_raw_stops_before_object(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"", logical_key="empty-evidence")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"", logical_key="empty-evidence",
            disposition="duplicate", duplicate_of=canonical)
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"latest-empty-error",
            logical_key="latest-empty-error")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'hj212_non_data_command','open',?)""",
                ((duplicate, "2026-10-01T08:01:01+00:00"),
                 (latest_raw, "2026-10-15T08:00:01+00:00")),
            )
            connection.commit()

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
            archive_root=self.root)

        self.assertFalse(result["complete"])
        self.assertEqual(result["stop_reason"], "archive_evidence_unavailable")
        self.assertEqual(result["archive_blocked_raw_ids"], [duplicate])
        self.assertLess(result["cursor"], duplicate)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_detail_archive_parts"
            ).fetchone()[0], 0)

    def test_exact_duplicate_error_archive_deadline_keeps_database_unchanged(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"archive-deadline", logical_key="archive-deadline")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"archive-deadline",
            logical_key="archive-deadline", disposition="duplicate", duplicate_of=canonical)
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"latest-archive-deadline",
            logical_key="latest-archive-deadline")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'decode_problem','open',?)""",
                ((duplicate, "2026-10-01T08:01:01+00:00"),
                 (latest_raw, "2026-10-15T08:00:01+00:00")),
            )
            connection.commit()

        interruption = retention._StatementDeadlineExceeded("exact_error_archive_verify")
        with mock.patch.object(
                retention, "_write_exact_duplicate_error_bundle", side_effect=interruption):
            with self.assertRaises(retention._StatementDeadlineExceeded):
                purge_exact_duplicate_batch(
                    self.database, cutoff="2026-10-09T12:00:00+00:00",
                    receipt_cutoff="2026-10-13T12:00:00+00:00",
                    upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
                    archive_root=self.root, time_budget_seconds=30)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_errors WHERE raw_frame_id=?", (duplicate,)
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)

    def test_exact_duplicate_cleanup_rejects_online_raw_digest_mismatch(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"original", logical_key="digest-mismatch")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"original", logical_key="digest-mismatch",
            disposition="duplicate", duplicate_of=canonical)
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"latest-digest-error",
            logical_key="latest-digest-error")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE ingest_raw_frames SET raw_frame=? WHERE id=?",
                (b"tampered", duplicate),
            )
            connection.executemany(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'decode_problem','open',?)""",
                ((duplicate, "2026-10-01T08:01:01+00:00"),
                 (latest_raw, "2026-10-15T08:00:01+00:00")),
            )
            connection.commit()

        with self.assertRaisesRegex(RetentionError, "online exact duplicate evidence digest"):
            purge_exact_duplicate_batch(
                self.database, cutoff="2026-10-09T12:00:00+00:00",
                receipt_cutoff="2026-10-13T12:00:00+00:00",
                upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
                archive_root=self.root)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)

    def test_exact_duplicate_cleanup_concurrent_error_change_rolls_back_and_removes_bundle(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"concurrent", logical_key="concurrent")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"concurrent", logical_key="concurrent",
            disposition="duplicate", duplicate_of=canonical)
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"latest-concurrent",
            logical_key="latest-concurrent")
        with closing(sqlite3.connect(self.database)) as connection:
            historical_error = connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,error_detail,status,created_at)
                   VALUES (?,'decode_problem','before','open','2026-10-01T08:01:01+00:00')""",
                (duplicate,),
            ).lastrowid
            connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'decode_problem','open','2026-10-15T08:00:01+00:00')""",
                (latest_raw,),
            )
            connection.commit()
        original = retention._write_exact_duplicate_error_bundle

        def write_then_change(*args, **kwargs):
            result = original(*args, **kwargs)
            with closing(sqlite3.connect(self.database)) as connection:
                connection.execute(
                    "UPDATE ingest_errors SET error_detail='after' WHERE id=?",
                    (historical_error,),
                )
                connection.commit()
            return result

        with mock.patch.object(
                retention, "_write_exact_duplicate_error_bundle",
                side_effect=write_then_change):
            with self.assertRaisesRegex(RetentionError, "error rows changed"):
                purge_exact_duplicate_batch(
                    self.database, cutoff="2026-10-09T12:00:00+00:00",
                    receipt_cutoff="2026-10-13T12:00:00+00:00",
                    upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
                    archive_root=self.root)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_detail_archive_parts"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)
        self.assertEqual(list((self.root / "detail-archives").glob("*.gz")), [])

    def test_exact_duplicate_error_archive_rolls_back_with_cleanup_transaction_and_retries(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"archive-rollback", logical_key="rollback-error")
        archive_raw_day(self.database, "2026-10-01", self.policy)
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"archive-rollback",
            logical_key="rollback-error", disposition="duplicate", duplicate_of=canonical)
        latest_raw = self._insert_raw(
            "2026-10-15T08:00:00+00:00", b"latest-rollback-error",
            logical_key="latest-rollback-error")
        with closing(sqlite3.connect(self.database)) as connection:
            historical_error = connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'decode_problem','open','2026-10-01T08:01:01+00:00')""",
                (duplicate,),
            ).lastrowid
            connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                   VALUES (?,'decode_problem','open','2026-10-15T08:00:01+00:00')""",
                (latest_raw,),
            )
            connection.commit()
        original = retention._delete_confirmed_duplicate_rows

        def fail_after_delete(connection, raw_ids):
            original(connection, raw_ids)
            raise sqlite3.OperationalError("injected archived cleanup failure")

        with mock.patch.object(
                retention, "_delete_confirmed_duplicate_rows", side_effect=fail_after_delete):
            with self.assertRaisesRegex(sqlite3.OperationalError, "archived cleanup failure"):
                purge_exact_duplicate_batch(
                    self.database, cutoff="2026-10-09T12:00:00+00:00",
                    receipt_cutoff="2026-10-13T12:00:00+00:00",
                    upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
                    archive_root=self.root)

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_errors WHERE id=?", (historical_error,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_detail_archive_parts"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)

        retried = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
            archive_root=self.root)
        self.assertEqual(retried["deleted"], 1)
        self.assertEqual(retried["errors_archived"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            archived = retention.read_detail_archive_record(
                connection, "ingest_error", historical_error)
            self.assertEqual(archived["raw_frame_id"], duplicate)

    def test_exact_duplicate_cleanup_resumes_fixed_window_and_ignores_other_running_state(self):
        self._mark_startup_ready()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO station_ingest_cutover_state(
                       cutover_name,migration_version,status,detail,updated_at)
                   VALUES ('retention-windows-v1','20261016_014_station_ingest_retention_windows',
                           'running','{}','2026-10-16T12:00:00+00:00')""")
            connection.commit()
        ensure_duplicate_cleanup_indexes(self.database)
        duplicate_ids = []
        for index in range(4):
            payload = f"pair-{index}".encode()
            canonical = self._insert_raw(
                f"2026-10-01T08:{index:02d}:00+00:00", payload,
                logical_key=f"key-{index}")
            duplicate_ids.append(self._insert_raw(
                f"2026-10-01T08:{index:02d}:30+00:00", payload,
                logical_key=f"key-{index}", disposition="duplicate",
                duplicate_of=canonical))
        upper_raw_id = duplicate_ids[-1]
        later_canonical = self._insert_raw(
            "2026-10-01T09:00:00+00:00", b"later", logical_key="later")
        later_duplicate = self._insert_raw(
            "2026-10-01T09:00:30+00:00", b"later", logical_key="later",
            disposition="duplicate", duplicate_of=later_canonical)

        results = []
        while not results or not results[-1]["complete"]:
            results.append(purge_exact_duplicate_batch(
                self.database, cutoff="2026-10-09T12:00:00+00:00",
                receipt_cutoff="2026-10-13T12:00:00+00:00",
                upper_raw_id=upper_raw_id, batch_limit=1, scan_limit=3))

        self.assertGreater(len(results), 1)
        self.assertEqual(results[-1]["cursor"], upper_raw_id)
        self.assertEqual(results[-1]["deleted"], 4)
        with self.assertRaisesRegex(RetentionError, "cursor does not match"):
            purge_exact_duplicate_batch(
                self.database, cutoff="2026-10-09T12:00:00+00:00",
                receipt_cutoff="2026-10-13T12:00:00+00:00",
                upper_raw_id=upper_raw_id, cursor=0, batch_limit=1, scan_limit=3)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (later_duplicate,)
            ).fetchone())

    def test_scoped_cleanup_keeps_global_checkpoint_and_combines_empty_ranges(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"scoped", logical_key="scoped")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"scoped", logical_key="scoped",
            disposition="duplicate", duplicate_of=canonical)
        outside_canonical = self._insert_raw(
            "2026-10-01T08:02:00+00:00", b"outside", logical_key="outside")
        outside_duplicate = self._insert_raw(
            "2026-10-01T08:03:00+00:00", b"outside", logical_key="outside",
            disposition="duplicate", duplicate_of=outside_canonical)
        cutoff = "2026-10-09T12:00:00+00:00"
        receipt_cutoff = "2026-10-13T12:00:00+00:00"
        upper_raw_id = outside_duplicate + 100
        global_checkpoint = {
            "cutoff": cutoff, "receipt_cutoff": receipt_cutoff,
            "upper_raw_id": upper_raw_id, "cursor": 94000,
            "scanned": 92000, "deleted": 0, "complete": False,
        }
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO station_ingest_retention_runs(
                       cutoff_time,archived_parse_attempts,archived_errors,
                       archived_error_summaries,deleted_raw_frames,
                       retained_protected_frames,status,detail,created_at)
                   VALUES (?,0,0,0,0,0,'completed',?,?)""",
                (cutoff, json.dumps({"exact_duplicate_checkpoint": global_checkpoint}), cutoff),
            )
            connection.commit()

        ranges = ((canonical, duplicate), (outside_duplicate + 50, outside_duplicate + 60))
        result = run_exact_duplicate_cleanup_job(
            self.database, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
            upper_raw_id=upper_raw_id, candidate_ranges=ranges,
            batch_limit=10, scan_limit=10, time_budget_seconds=30,
            archive_root=self.root)

        self.assertFalse(result["retry_required"])
        self.assertTrue(result["result"]["complete"])
        self.assertEqual(result["result"]["deleted"], 1)
        self.assertEqual(result["result"]["range_index"], len(ranges))
        self.assertEqual(result["batches"], 2)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (outside_duplicate,)
            ).fetchone())
            preserved = retention._latest_exact_duplicate_checkpoint(
                connection, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
                upper_raw_id=upper_raw_id)
            self.assertEqual(preserved["cursor"], 94000)
            run_count = connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0]

        repeated = run_exact_duplicate_cleanup_job(
            self.database, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
            upper_raw_id=upper_raw_id, candidate_ranges=ranges,
            batch_limit=10, scan_limit=10, time_budget_seconds=30,
            archive_root=self.root)
        self.assertTrue(repeated["result"]["complete"])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], run_count)

    def test_scoped_cleanup_uses_r5_candidate_ranges_without_scanning_gaps(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        ranges = (
            (2000001, 2002000), (4000001, 4002000),
            (6000001, 6002000), (7107831, 7109830),
        )
        duplicate_ids = []
        canonical_ids = []
        for start, _ in ranges:
            with closing(sqlite3.connect(self.database)) as connection:
                connection.execute(
                    "DELETE FROM sqlite_sequence WHERE name='ingest_raw_frames'")
                connection.execute(
                    "INSERT INTO sqlite_sequence(name,seq) VALUES ('ingest_raw_frames',?)",
                    (start - 2,),
                )
                connection.commit()
            payload = f"range-{start}".encode()
            canonical = self._insert_raw(
                "2026-10-01T08:00:00+00:00", payload,
                logical_key=f"range-{start}")
            duplicate = self._insert_raw(
                "2026-10-01T08:01:00+00:00", payload,
                logical_key=f"range-{start}", disposition="duplicate",
                duplicate_of=canonical)
            self.assertEqual(duplicate, start)
            canonical_ids.append(canonical)
            duplicate_ids.append(duplicate)

        result = run_exact_duplicate_cleanup_job(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=ranges[-1][1], candidate_ranges=ranges,
            batch_limit=100, scan_limit=2000, time_budget_seconds=30,
            archive_root=self.root)

        self.assertTrue(result["result"]["complete"])
        self.assertEqual(result["result"]["scanned"], 4)
        self.assertEqual(result["result"]["exact_candidates"], 4)
        self.assertEqual(result["result"]["deleted"], 4)
        self.assertEqual(result["batches"], 5)
        with closing(sqlite3.connect(self.database)) as connection:
            remaining = {int(row[0]) for row in connection.execute(
                "SELECT id FROM ingest_raw_frames")}
        self.assertTrue(set(canonical_ids).issubset(remaining))
        self.assertTrue(set(duplicate_ids).isdisjoint(remaining))

    def test_checkpoint_recovery_survives_more_than_200_unrelated_runs(self):
        cutoff = "2026-10-09T12:00:00+00:00"
        receipt_cutoff = "2026-10-13T12:00:00+00:00"
        upper_raw_id = 7109830
        candidate_ranges = ((2000001, 2002000),)
        candidate_scope = retention._candidate_scope(candidate_ranges)
        global_checkpoint = {
            "cutoff": cutoff, "receipt_cutoff": receipt_cutoff,
            "upper_raw_id": upper_raw_id, "candidate_scope": None,
            "cursor": 94000, "complete": False,
        }
        scoped_checkpoint = {
            "cutoff": cutoff, "receipt_cutoff": receipt_cutoff,
            "upper_raw_id": upper_raw_id, "candidate_scope": candidate_scope,
            "candidate_ranges": [list(item) for item in candidate_ranges],
            "range_index": 1, "cursor": 2002000, "complete": True,
        }
        with closing(sqlite3.connect(self.database)) as connection:
            for checkpoint in (global_checkpoint, scoped_checkpoint):
                connection.execute(
                    """INSERT INTO station_ingest_retention_runs(
                           cutoff_time,archived_parse_attempts,archived_errors,
                           archived_error_summaries,deleted_raw_frames,
                           retained_protected_frames,status,detail,created_at)
                       VALUES (?,0,0,0,0,0,'completed',?,?)""",
                    (cutoff, json.dumps({"exact_duplicate_checkpoint": checkpoint}), cutoff),
                )
            for index in range(250):
                connection.execute(
                    """INSERT INTO station_ingest_retention_runs(
                           cutoff_time,archived_parse_attempts,archived_errors,
                           archived_error_summaries,deleted_raw_frames,
                           retained_protected_frames,status,detail,created_at)
                       VALUES (?,0,0,0,0,0,'completed',?,?)""",
                    ("2026-10-10T00:00:00+00:00",
                     json.dumps({"unrelated": index}),
                     "2026-10-28T00:00:00+00:00"),
                )
            connection.commit()

        with closing(sqlite3.connect(self.database)) as connection:
            recovered_global = retention._latest_exact_duplicate_checkpoint(
                connection, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
                upper_raw_id=upper_raw_id)
            recovered_scope = retention._latest_exact_duplicate_checkpoint(
                connection, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
                upper_raw_id=upper_raw_id, candidate_scope=candidate_scope)
            self.assertEqual(recovered_global["cursor"], 94000)
            self.assertTrue(recovered_scope["complete"])
            stable_count = connection.execute(
                """SELECT COUNT(*) FROM station_ingest_cutover_state
                   WHERE cutover_name LIKE ?""",
                (retention.EXACT_DUPLICATE_CHECKPOINT_VERSION + ":%",),
            ).fetchone()[0]
            self.assertEqual(stable_count, 2)
            connection.execute("DELETE FROM station_ingest_retention_runs")
            connection.commit()
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(retention._latest_exact_duplicate_checkpoint(
                connection, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
                upper_raw_id=upper_raw_id)["cursor"], 94000)
            self.assertTrue(retention._latest_exact_duplicate_checkpoint(
                connection, cutoff=cutoff, receipt_cutoff=receipt_cutoff,
                upper_raw_id=upper_raw_id,
                candidate_scope=candidate_scope)["complete"])

    def test_cleanup_job_caps_each_batch_at_30_seconds(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        candidate_ranges = ((1, 2),)
        result = {
            "candidate_scope": retention._candidate_scope(candidate_ranges),
            "candidate_ranges": [[1, 2]], "range_index": 1, "cursor": 2,
            "scanned": 2, "deleted": 1, "errors_archived": 0,
            "pending_raw_ids": [], "representative_search": {},
            "batch_deleted": 1, "batch_errors_archived": 0,
            "batch_elapsed_seconds": 0.01, "stop_reason": "upper_bound_reached",
            "complete": True,
        }
        with mock.patch.object(
                retention, "purge_exact_duplicate_batch", return_value=result) as purge:
            completed = run_exact_duplicate_cleanup_job(
                self.database, cutoff="2026-10-09T12:00:00+00:00",
                receipt_cutoff="2026-10-13T12:00:00+00:00",
                upper_raw_id=2, candidate_ranges=candidate_ranges,
                time_budget_seconds=900, archive_root=self.root)
        self.assertFalse(completed["retry_required"])
        self.assertEqual(
            purge.call_args.kwargs["time_budget_seconds"],
            retention.EXACT_DUPLICATE_BATCH_TIME_BUDGET_SECONDS)

    def test_cleanup_job_missing_indexes_fails_without_creation_or_cleanup(self):
        self._mark_startup_ready()
        with mock.patch.object(retention, "purge_exact_duplicate_batch") as purge:
            with self.assertRaisesRegex(RetentionError, "indexes are missing"):
                run_exact_duplicate_cleanup_job(
                    self.database, cutoff="2026-10-09T12:00:00+00:00",
                    receipt_cutoff="2026-10-13T12:00:00+00:00",
                    upper_raw_id=2, candidate_ranges=((1, 2),),
                    time_budget_seconds=900, archive_root=self.root)
        purge.assert_not_called()
        with closing(sqlite3.connect(self.database)) as connection:
            existing = {str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue(set(retention.CLEANUP_INDEXES).isdisjoint(existing))
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)

    def test_exact_duplicate_cleanup_failure_rolls_back_delete_and_checkpoint(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"rollback", logical_key="rollback")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"rollback", logical_key="rollback",
            disposition="duplicate", duplicate_of=canonical)
        original = retention._delete_confirmed_duplicate_rows

        def fail_after_delete(connection, raw_ids):
            original(connection, raw_ids)
            raise sqlite3.OperationalError("injected cleanup failure")

        with mock.patch.object(
                retention, "_delete_confirmed_duplicate_rows", side_effect=fail_after_delete):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected cleanup failure"):
                purge_exact_duplicate_batch(
                    self.database, cutoff="2026-10-09T12:00:00+00:00",
                    receipt_cutoff="2026-10-13T12:00:00+00:00",
                    upper_raw_id=duplicate, batch_limit=10, scan_limit=10)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)

    def test_exact_duplicate_cleanup_ignores_unrelated_expired_receipts(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"target", logical_key="target")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"target", logical_key="target",
            disposition="duplicate", duplicate_of=canonical)
        active = self._insert_raw(
            "2026-10-01T08:02:00+00:00", b"target", logical_key="target",
            disposition="duplicate", duplicate_of=canonical)
        unrelated = [
            self._insert_raw(
                "2026-10-01T09:00:00+00:00", f"unrelated-{index}".encode(),
                logical_key=f"unrelated-{index}")
            for index in range(600)
        ]
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO station_ingest_recent_receipts(
                       raw_frame_id,source_key,endpoint_id,station_code,received_at,
                       authentication_status) VALUES (?,?,?,?,?,'authenticated')""",
                (active, f"endpoint:{self.endpoint_id}", self.endpoint_id,
                 "RETENTION-MN", "2026-10-15T00:00:00+00:00"),
            )
            connection.executemany(
                """INSERT INTO station_ingest_recent_receipts(
                       raw_frame_id,source_key,endpoint_id,station_code,received_at,
                       authentication_status) VALUES (?,?,?,?,?,'authenticated')""",
                ((raw_id, f"unrelated:{raw_id}", self.endpoint_id, "RETENTION-MN",
                  "2026-10-01T00:00:00+00:00") for raw_id in unrelated),
            )
            connection.commit()

        result = purge_exact_duplicate_batch(
            self.database, cutoff="2026-10-09T12:00:00+00:00",
            receipt_cutoff="2026-10-13T12:00:00+00:00",
            upper_raw_id=active, batch_limit=10, scan_limit=10,
            time_budget_seconds=30)

        self.assertTrue(result["complete"])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["receipts_deleted"], 0)
        self.assertEqual(result["retained_protected"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (active,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_recent_receipts WHERE received_at<?",
                ("2026-10-13T12:00:00+00:00",),
            ).fetchone()[0], len(unrelated))

    def test_exact_duplicate_cleanup_deadline_rolls_back_receipts_raw_and_checkpoint(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        canonical = self._insert_raw(
            "2026-10-01T08:00:00+00:00", b"deadline", logical_key="deadline")
        duplicate = self._insert_raw(
            "2026-10-01T08:01:00+00:00", b"deadline", logical_key="deadline",
            disposition="duplicate", duplicate_of=canonical)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO station_ingest_recent_receipts(
                       raw_frame_id,source_key,endpoint_id,station_code,received_at,
                       authentication_status) VALUES (?,?,?,?,?,'authenticated')""",
                (duplicate, f"endpoint:{self.endpoint_id}", self.endpoint_id,
                 "RETENTION-MN", "2026-10-01T00:00:00+00:00"),
            )
            connection.commit()

        original = retention._delete_confirmed_duplicate_rows

        def expire_after_delete(connection, raw_ids):
            original(connection, raw_ids)
            raise retention._StatementDeadlineExceeded("exact_write_batch")

        with mock.patch.object(
                retention, "_delete_confirmed_duplicate_rows",
                side_effect=expire_after_delete):
            with self.assertRaises(retention._StatementDeadlineExceeded) as raised:
                purge_exact_duplicate_batch(
                    self.database, cutoff="2026-10-09T12:00:00+00:00",
                    receipt_cutoff="2026-10-13T12:00:00+00:00",
                    upper_raw_id=duplicate, batch_limit=10, scan_limit=10,
                    time_budget_seconds=30)

        self.assertEqual(raised.exception.checkpoint["cursor"], 0)
        self.assertEqual(raised.exception.checkpoint["receipts_deleted"], 0)
        self.assertEqual(raised.exception.checkpoint["deleted"], 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM ingest_raw_frames WHERE id=?", (duplicate,)
            ).fetchone())
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM station_ingest_recent_receipts WHERE raw_frame_id=?",
                (duplicate,),
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM station_ingest_retention_runs"
            ).fetchone()[0], 0)

    def test_exact_duplicate_index_deadline_reports_completed_stages(self):
        interruption = retention._StatementDeadlineExceeded(
            "exact_query_plan:bounded_raw_scan")
        with mock.patch.object(
                retention, "_exact_duplicate_cleanup_query_plans",
                side_effect=interruption):
            with self.assertRaises(retention._StatementDeadlineExceeded) as raised:
                ensure_duplicate_cleanup_indexes(self.database, time_budget_seconds=30)

        stages = raised.exception.checkpoint["index_stages"]
        self.assertEqual([stage["name"] for stage in stages], list(retention.CLEANUP_INDEXES))
        self.assertTrue(all("elapsed_seconds" in stage for stage in stages))
        self.assertTrue(all("database_growth_bytes" in stage for stage in stages))
        with closing(sqlite3.connect(self.database)) as connection:
            existing = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue(set(retention.CLEANUP_INDEXES).issubset(existing))

    def test_exact_duplicate_cleanup_cli_deadline_returns_partial_checkpoint(self):
        self._mark_startup_ready()
        ensure_duplicate_cleanup_indexes(self.database)
        interruption = retention._StatementDeadlineExceeded(
            "exact_write_batch", {"cursor": 0, "deleted": 0})
        stderr = io.StringIO()
        with mock.patch.object(
                retention, "purge_exact_duplicate_batch", side_effect=interruption), \
                mock.patch.object(sys, "stderr", stderr):
            code = retention.main([
                "purge-exact-duplicates", "--database", str(self.database),
                "--cutoff", "2026-10-09T12:00:00+00:00",
                "--receipt-cutoff", "2026-10-13T12:00:00+00:00",
                "--upper-raw-id", "1", "--time-budget-seconds", "30",
                "--raw-storage-dir", str(self.root),
            ])
        self.assertEqual(code, 75)
        result = json.loads(stderr.getvalue())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["checkpoint"], {"cursor": 0, "deleted": 0})

    def test_exact_duplicate_cleanup_cli_requires_ready_state_and_supports_index_exit(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(retention.main([
                "prepare-exact-duplicate-cleanup", "--database", str(self.database),
            ]), 0)
        prepared = json.loads(stdout.getvalue())
        self.assertEqual(prepared["action"], "prepare-exact-duplicate-cleanup")
        with self.assertRaisesRegex(RetentionError, "startup-ready state"):
            purge_exact_duplicate_batch(
                self.database, cutoff="2026-10-09T12:00:00+00:00",
                receipt_cutoff="2026-10-13T12:00:00+00:00",
                upper_raw_id=1)
        self._mark_startup_ready()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(retention.main([
                "purge-exact-duplicates", "--database", str(self.database),
                "--cutoff", "2026-10-09T12:00:00+00:00",
                "--receipt-cutoff", "2026-10-13T12:00:00+00:00",
                "--upper-raw-id", "1",
                "--raw-storage-dir", str(self.root),
            ]), 0)
        self.assertTrue(json.loads(stdout.getvalue())["result"]["complete"])
        self.assertEqual(
            set(drop_duplicate_cleanup_indexes(self.database)), set(retention.CLEANUP_INDEXES))

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
