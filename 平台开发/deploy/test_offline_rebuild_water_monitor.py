from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("offline-rebuild-water-monitor.py")
SPEC = importlib.util.spec_from_file_location("offline_rebuild_water_monitor", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


AS_OF = "2026-10-21T19:28:59+08:00"
REVIEWED_016_TEMPLATE = (
    MODULE_PATH.parent / "reviewed-templates" / "rebuild-rules-016-reviewed.json"
)
REVIEWED_014_TEMPLATE = (
    MODULE_PATH.parents[3]
    / "governance-offline-rehearsal-20261028"
    / "evidence"
    / "rebuild-rules.json"
)
MIGRATION_PATH = MODULE_PATH.parents[1] / "backend" / "migrate_station_ingestion.py"
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "offline_rebuild_station_migration", MIGRATION_PATH,
)
MIGRATION = importlib.util.module_from_spec(MIGRATION_SPEC)
assert MIGRATION_SPEC and MIGRATION_SPEC.loader
MIGRATION_SPEC.loader.exec_module(MIGRATION)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _create_source(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                reading REAL,
                payload BLOB,
                optional_value TEXT
            );
            CREATE TABLE site_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                FOREIGN KEY (site_id) REFERENCES sites(id)
            );
            CREATE VIEW site_names AS SELECT id,name FROM sites;
            CREATE TRIGGER sites_insert_audit AFTER INSERT ON sites
            BEGIN
                INSERT INTO site_audit(site_id,action) VALUES (NEW.id,'insert');
            END;

            CREATE TABLE ingest_raw_frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint_id INTEGER,
                station_code TEXT,
                received_at TEXT NOT NULL,
                frame_sha256 TEXT NOT NULL,
                logical_key_sha256 TEXT,
                raw_frame BLOB NOT NULL,
                body_length INTEGER,
                crc_status TEXT NOT NULL,
                authentication_status TEXT NOT NULL,
                disposition TEXT NOT NULL,
                duplicate_of_raw_frame_id INTEGER,
                persistence_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (duplicate_of_raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE ingest_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_frame_id INTEGER,
                error_type TEXT NOT NULL,
                status TEXT NOT NULL,
                error_detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '2026-01-01 00:00:00',
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE ingest_parse_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_frame_id INTEGER NOT NULL,
                parser_version TEXT NOT NULL,
                parse_status TEXT NOT NULL DEFAULT 'parsed_header',
                error_code TEXT,
                created_at TEXT NOT NULL DEFAULT '2026-01-01 00:00:00',
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE ingest_frame_protocols (
                raw_frame_id INTEGER PRIMARY KEY,
                protocol_family TEXT NOT NULL,
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE observation_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_frame_id INTEGER NOT NULL,
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE monitoring_quality_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_frame_id INTEGER,
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE monitoring_normalization_retries (
                raw_frame_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE station_ingest_slot_summaries (
                id INTEGER PRIMARY KEY,
                canonical_raw_frame_id INTEGER NOT NULL,
                FOREIGN KEY (canonical_raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE station_ingest_dedup_keys (
                id INTEGER PRIMARY KEY,
                canonical_raw_frame_id INTEGER NOT NULL,
                protected INTEGER NOT NULL,
                FOREIGN KEY (canonical_raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE station_ingest_duplicate_links (
                raw_frame_id INTEGER PRIMARY KEY,
                canonical_raw_frame_id INTEGER NOT NULL,
                canonical_evidence_ref_id INTEGER,
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE station_ingest_recent_receipts (
                raw_frame_id INTEGER PRIMARY KEY,
                received_at TEXT NOT NULL,
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE station_ingest_current_state (
                id INTEGER PRIMARY KEY,
                latest_raw_frame_id INTEGER,
                latest_authenticated_raw_frame_id INTEGER,
                latest_credential_failed_raw_frame_id INTEGER
            );
            CREATE TABLE monitoring_raw_archive_part_frames (
                id INTEGER PRIMARY KEY,
                raw_frame_id INTEGER NOT NULL,
                FOREIGN KEY (raw_frame_id) REFERENCES ingest_raw_frames(id)
            );
            CREATE TABLE station_ingest_receipt_hourly (
                id INTEGER PRIMARY KEY,
                last_complete_raw_frame_id INTEGER
            );
            CREATE TABLE station_ingest_error_hourly (
                id INTEGER PRIMARY KEY,
                last_raw_frame_id INTEGER
            );
            """
        )
        connection.execute(
            "INSERT INTO sites(name,reading,payload,optional_value) VALUES (?,?,?,?)",
            ("source-site", 1.25, sqlite3.Binary(b"\x00\xff"), None),
        )
        connection.execute("DELETE FROM site_audit")
        connection.execute(
            "INSERT INTO sites(id,name,reading,payload) VALUES (99,'deleted-high-water',0,X'00')"
        )
        connection.execute("DELETE FROM site_audit WHERE site_id=99")
        connection.execute("DELETE FROM sites WHERE id=99")
        raw_rows = []
        for raw_id in range(1, 9):
            raw_rows.append((
                raw_id, 1, "S1", "2026-01-01T00:00:00+00:00", "frame-hash",
                "logical-key", sqlite3.Binary(b"same-frame"), 10, "valid",
                "authenticated", "accepted" if raw_id == 1 else "duplicate",
                None if raw_id == 1 else (6 if raw_id == 7 else 1),
                "persisted", "2026-01-01T00:00:00+00:00",
            ))
        connection.executemany(
            """INSERT INTO ingest_raw_frames(
                   id,endpoint_id,station_code,received_at,frame_sha256,
                   logical_key_sha256,raw_frame,body_length,crc_status,
                   authentication_status,disposition,duplicate_of_raw_frame_id,
                   persistence_state,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            raw_rows,
        )
        connection.execute("INSERT INTO observation_batches(raw_frame_id) VALUES (3)")
        connection.execute(
            "INSERT INTO ingest_errors(raw_frame_id,error_type,status) VALUES (4,'parse','open')"
        )
        connection.execute(
            "INSERT INTO station_ingest_recent_receipts(raw_frame_id,received_at) VALUES (5,'2026-10-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO station_ingest_current_state(id,latest_raw_frame_id) VALUES (1,8)"
        )
        connection.execute(
            "INSERT INTO station_ingest_dedup_keys(id,canonical_raw_frame_id,protected) VALUES (1,2,0)"
        )
        for raw_id in (2, 7):
            connection.execute(
                "INSERT INTO ingest_parse_attempts(raw_frame_id,parser_version) VALUES (?, 'v1')",
                (raw_id,),
            )
            connection.execute(
                "INSERT INTO ingest_frame_protocols(raw_frame_id,protocol_family) VALUES (?, 'hj212')",
                (raw_id,),
            )
            connection.execute(
                "INSERT INTO monitoring_raw_archive_part_frames(id,raw_frame_id) VALUES (?,?)",
                (raw_id, raw_id),
            )
            connection.execute(
                "INSERT INTO station_ingest_duplicate_links(raw_frame_id,canonical_raw_frame_id) VALUES (?,1)",
                (raw_id,),
            )
            connection.execute(
                "INSERT INTO station_ingest_recent_receipts(raw_frame_id,received_at) VALUES (?,'2026-01-01T00:00:00+00:00')",
                (raw_id,),
            )
        connection.execute(
            "INSERT INTO station_ingest_receipt_hourly(id,last_complete_raw_frame_id) VALUES (1,2)"
        )
        connection.execute(
            "INSERT INTO station_ingest_error_hourly(id,last_raw_frame_id) VALUES (1,7)"
        )
        connection.commit()


def _manifest(path: Path, source: Path, as_of: str = AS_OF) -> None:
    value = {
        "backup_time": as_of,
        "files": [{
            "local_path": str(source),
            "size_bytes": source.stat().st_size,
            "sha256": _sha256(source),
        }],
    }
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _create_reviewed_016_source(path: Path) -> dict:
    template = json.loads(REVIEWED_016_TEMPLATE.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(path)) as connection:
        for item in template["reviewed_schema_objects"]:
            connection.execute(item["sql"])
        connection.executemany(
            """INSERT INTO schema_migrations(version,checksum,applied_at,app_version)
               VALUES (?,?,?,?)""",
            [
                (version, MIGRATION.migration_checksum(version),
                 "2026-10-21T00:00:00+00:00", "reviewed-016-fixture")
                for version, _migration_path in MIGRATION.MIGRATIONS
            ],
        )
        connection.execute(
            "INSERT INTO sites(id,name,code) VALUES (1,'Reviewed 016 site','R7-016')"
        )
        connection.execute(
            """INSERT INTO parts_requests(id,plan_id,requester_id,status)
               VALUES (17,0,26,'approved')"""
        )
        connection.execute(
            """INSERT INTO trusted_endpoints(
                   id,station_code,credential_hmac,business_site_id,endpoint_state)
               VALUES (1,'R7-016-ENDPOINT','fixture-hmac',1,'bound')"""
        )
        raw_frame = b"##0060QN=20261028035500001;ST=32;CN=2011;CP=&&DataTime=20261028035500;w01001-Rtd=1.8050&&0000\r\n"
        connection.execute(
            """INSERT INTO ingest_raw_frames(
                   id,endpoint_id,station_code,received_at,frame_sha256,
                   logical_key_sha256,raw_frame,body_length,crc_status,
                   authentication_status,disposition,persistence_state)
               VALUES (1,1,'R7-016-ENDPOINT','2026-10-28T03:55:00+00:00',
                       ?,?, ?,?,'valid','authenticated','accepted','persisted')""",
            ("a" * 64, "b" * 64, raw_frame, len(raw_frame)),
        )
        connection.execute(
            """INSERT INTO observation_batches(
                   id,raw_frame_id,endpoint_id,business_site_id,function_code,
                   serial_number,reported_at,observed_at,received_at,granularity,
                   aggregation_source,idempotency_key,normalization_version,
                   batch_status,projection_state,normalized_at)
               VALUES (1,1,1,1,2011,1,'2026-10-28T03:55:00+00:00',
                       '2026-10-28T03:55:00+00:00','2026-10-28T03:55:00+00:00',
                       'instant','device_reported','r7-016-batch','fixture-v1',
                       'accepted','completed','2026-10-28T03:55:01+00:00')"""
        )
        connection.execute(
            """INSERT INTO monitoring_factor_definitions(
                   protocol_code,protocol_factor,raw_unit,parse_format,
                   business_metric,standard_unit,decimal_precision,is_published)
               VALUES ('w01001','w01001',NULL,'decimal','ph',NULL,4,1)"""
        )
        connection.execute(
            """INSERT INTO observation_values(
                   id,observation_batch_id,protocol_code,business_metric,
                   raw_value,raw_value_text,standard_value,quality,parser_version)
               VALUES (1,1,'w01001','ph',1.805,'1.8050',1.805,'valid','fixture-v1')"""
        )
        connection.commit()
    return template


class OfflineRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        self.manifest = self.root / "SOURCE_MANIFEST.json"
        self.rules = self.root / "rules.json"
        self.plan = self.root / "REBUILD_PLAN.md"
        self.temp_dir = self.root / "sqlite-temp"
        _create_source(self.source)
        _manifest(self.manifest, self.source)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _inventory(self, as_of: str = AS_OF) -> dict:
        return MODULE.inventory(argparse.Namespace(
            source=self.source,
            source_manifest=self.manifest,
            as_of=as_of,
            rules_version="fixture-r1",
            rules_output=self.rules,
            plan_output=self.plan,
            code_root=self.root / "missing-code-root",
        ))

    def _approved_rules(self) -> dict:
        self._inventory()
        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        rules["review_status"] = "approved"
        for rule in rules["tables"].values():
            rule["review_status"] = "approved"
        rules["exact_duplicate_policy"] = {
            "enabled": True,
            "cutoff": "2026-09-01T00:00:00+00:00",
            "receipt_cutoff": "2026-09-15T00:00:00+00:00",
            "candidate_ranges": [[1, 8]],
        }
        actions = {
            "ingest_raw_frames": ("skip_excluded_raw_rows", None),
            "ingest_parse_attempts": ("skip_rows_for_excluded_raw", "raw_frame_id"),
            "ingest_frame_protocols": ("skip_rows_for_excluded_raw", "raw_frame_id"),
            "monitoring_raw_archive_part_frames": (
                "skip_rows_for_excluded_raw", "raw_frame_id"
            ),
            "station_ingest_duplicate_links": (
                "skip_rows_for_excluded_raw", "raw_frame_id"
            ),
            "station_ingest_recent_receipts": (
                "skip_rows_for_excluded_raw", "raw_frame_id"
            ),
            "station_ingest_dedup_keys": (
                "skip_excluded_canonical_rows", "canonical_raw_frame_id"
            ),
        }
        for table, (action, column) in actions.items():
            rules["tables"][table]["action"] = action
            if column:
                rules["tables"][table]["raw_reference_column"] = column
        rules["tables"]["station_ingest_receipt_hourly"].update({
            "action": "null_excluded_raw_reference",
            "raw_reference_columns": ["last_complete_raw_frame_id"],
        })
        rules["tables"]["station_ingest_error_hourly"].update({
            "action": "null_excluded_raw_reference",
            "raw_reference_columns": ["last_raw_frame_id"],
        })
        self.rules.write_text(json.dumps(rules, indent=2), encoding="utf-8")
        return rules

    def _rebuild(
        self, *, as_of: str = AS_OF, minimum_free_bytes: int | None = None,
        space_requirement_basis: str | None = None, temp_dir: Path | None = None,
    ) -> tuple[dict, Path, Path, Path]:
        target = self.root / "target.db"
        report = self.root / "report.json"
        archive = self.root / "target.zip"
        result = MODULE.rebuild(argparse.Namespace(
            source=self.source,
            source_manifest=self.manifest,
            target=target,
            rules=self.rules,
            as_of=as_of,
            report=report,
            archive=archive,
            temp_dir=temp_dir or self.temp_dir,
            minimum_free_bytes=minimum_free_bytes,
            space_requirement_basis=space_requirement_basis,
        ))
        return result, target, report, archive

    def test_inventory_requires_explicit_review_for_every_actual_table(self) -> None:
        result = self._inventory()
        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        with closing(sqlite3.connect(self.source)) as connection:
            actual = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        self.assertEqual(result["tables"], len(actual))
        self.assertEqual(set(rules["tables"]), actual)
        self.assertTrue(all(
            rule["review_status"] == "pending" for rule in rules["tables"].values()
        ))
        self.assertIn("product rules pending", self.plan.read_text(encoding="utf-8"))

    def test_selection_boundaries_follow_as_of_and_accept_equivalent_timezones(self) -> None:
        as_of = "2026-10-22T19:28:59+08:00"
        manifest_as_of = "2026-10-22T11:28:59Z"
        _manifest(self.manifest, self.source, manifest_as_of)
        self._inventory(as_of=as_of)
        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        rules["review_status"] = "approved"
        rules["as_of"] = manifest_as_of
        rules["selection_policy"] = {
            "review_status": "approved",
            "raw_received_from": "2026-10-15T19:28:59+08:00",
            "parse_created_from": "2026-10-19T11:28:59Z",
            "error_policy": "active_unknown_endpoint_reauthentication_only",
        }
        for rule in rules["tables"].values():
            rule["review_status"] = "approved"
        rules["tables"]["ingest_raw_frames"]["action"] = "copy_retained_raw_rows"
        with closing(MODULE._source_connection(self.source)) as connection:
            objects = MODULE._schema_objects(connection)
        MODULE._validate_rules(rules, objects, _sha256(self.manifest), as_of)
        self.assertEqual(MODULE._selection_boundaries(as_of), {
            "as_of_utc": manifest_as_of,
            "raw_received_from": "2026-10-15T11:28:59Z",
            "parse_created_from": "2026-10-19T11:28:59Z",
        })

        rules["selection_policy"]["raw_received_from"] = "2026-10-15T11:28:58Z"
        with self.assertRaisesRegex(MODULE.RebuildError, "minus 168 hours"):
            MODULE._validate_rules(rules, objects, _sha256(self.manifest), as_of)

    def test_explicit_capacity_and_temp_directory_override_local_defaults(self) -> None:
        rules = self._approved_rules()
        rules["local_rehearsal_minimum_free_bytes"] = 2**62
        self.rules.write_text(json.dumps(rules, indent=2), encoding="utf-8")
        result, _target, _report, _archive = self._rebuild(
            minimum_free_bytes=1,
            space_requirement_basis="fixture target and archive estimate",
        )
        self.assertEqual(result["space_check"]["required_free_bytes"], 1)
        self.assertEqual(result["space_check"]["requirement_source"], "explicit_cli")
        self.assertEqual(
            result["space_check"]["requirement_basis"],
            "fixture target and archive estimate",
        )
        self.assertGreater(result["space_check"]["local_default_free_bytes"], 1)
        self.assertEqual(result["sqlite_temp"]["path"], str(self.temp_dir.resolve()))
        self.assertEqual(result["sqlite_temp"]["temp_store"], "FILE")
        self.assertEqual(result["sqlite_temp"]["pragma_temp_store"], 1)
        self.assertEqual(
            Path(result["sqlite_temp"]["pragma_temp_store_directory"]).resolve(),
            self.temp_dir.resolve(),
        )
        self.assertIn("free_bytes_before", result["sqlite_temp"])
        self.assertIn("free_bytes_after", result["sqlite_temp"])

    def test_prepare_rebinds_reviewed_rules_for_two_final_sources_then_rebuilds(self) -> None:
        self._inventory()
        template = json.loads(self.rules.read_text(encoding="utf-8"))
        template["review_status"] = "approved"
        template["selection_policy"] = {
            "review_status": "approved",
            "raw_received_from": "2026-10-14T11:28:59Z",
            "parse_created_from": "2026-10-18T11:28:59Z",
            "error_policy": "active_unknown_endpoint_reauthentication_only",
        }
        for rule in template["tables"].values():
            rule["review_status"] = "approved"
        template["tables"]["ingest_raw_frames"]["action"] = "copy_retained_raw_rows"
        template["tables"]["ingest_parse_attempts"]["action"] = "copy_retained_parse_rows"
        template["tables"]["ingest_errors"]["action"] = "copy_retained_error_rows"
        for table in (
            "ingest_frame_protocols", "monitoring_raw_archive_part_frames",
            "station_ingest_duplicate_links", "station_ingest_recent_receipts",
        ):
            template["tables"][table].update({
                "action": "copy_rows_for_retained_raw",
                "raw_reference_column": "raw_frame_id",
            })
        template_path = self.root / "reviewed-template.json"
        template_path.write_text(json.dumps(template, indent=2), encoding="utf-8")

        second_source = self.root / "second-source.db"
        _create_source(second_source)
        with closing(sqlite3.connect(second_source)) as connection:
            connection.execute(
                "INSERT INTO sites(name,reading,payload) VALUES ('larger-source',2.0,zeroblob(262144))"
            )
            connection.commit()
            connection.execute("VACUUM")
        self.assertNotEqual(self.source.stat().st_size, second_source.stat().st_size)

        cases = (
            (self.source, "2026-10-22T19:28:59+08:00"),
            (second_source, "2026-10-24T11:28:59Z"),
        )
        manifest_digests = []
        for index, (source, as_of) in enumerate(cases, start=1):
            case_root = self.root / f"prepare-{index}"
            manifest = case_root / "SOURCE_MANIFEST.json"
            rules = case_root / "rules.json"
            prepare_report = case_root / "prepare-report.json"
            source_hash = _sha256(source)
            prepare_process = subprocess.run([
                sys.executable, str(MODULE_PATH), "prepare",
                "--source", str(source),
                "--template-rules", str(template_path),
                "--as-of", as_of,
                "--rules-version", f"fixture-final-{index}",
                "--manifest-output", str(manifest),
                "--rules-output", str(rules),
                "--report", str(prepare_report),
                "--consistent-source-confirmed",
            ], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(prepare_process.returncode, 0, prepare_process.stderr)
            prepared = json.loads(prepare_report.read_text(encoding="utf-8"))
            self.assertEqual(prepared["status"], "completed")
            self.assertEqual(prepared["source_identity"]["sha256"], source_hash)
            self.assertEqual(_sha256(source), source_hash)
            prepared_rules = json.loads(rules.read_text(encoding="utf-8"))
            self.assertEqual(prepared_rules["review_status"], "approved")
            self.assertTrue(all(
                rule["review_status"] == "approved"
                for rule in prepared_rules["tables"].values()
            ))
            self.assertEqual(
                prepared_rules["tables"]["ingest_raw_frames"]["action"],
                "copy_retained_raw_rows",
            )
            self.assertEqual(
                prepared_rules["selection_policy"]["raw_received_from"],
                MODULE._selection_boundaries(as_of)["raw_received_from"],
            )
            target = case_root / "target.db"
            rebuild_report = case_root / "rebuild-report.json"
            rebuild_process = subprocess.run([
                sys.executable, str(MODULE_PATH), "rebuild",
                "--source", str(source),
                "--source-manifest", str(manifest),
                "--target", str(target),
                "--rules", str(rules),
                "--as-of", as_of,
                "--report", str(rebuild_report),
                "--archive", str(case_root / "target.zip"),
                "--temp-dir", str(case_root / "sqlite-temp"),
                "--minimum-free-bytes", "1",
                "--space-requirement-basis", "fixture prepare to rebuild",
            ], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(rebuild_process.returncode, 0, rebuild_process.stderr)
            result = json.loads(rebuild_report.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["source_manifest_evidence"]["declared_sha256"], source_hash)
            self.assertEqual(
                result["source_manifest_evidence"]["declared_mtime_ns"],
                source.stat().st_mtime_ns,
            )
            manifest_digests.append(prepared["source_manifest_sha256"])
        self.assertEqual(len(set(manifest_digests)), 2)

    def test_reviewed_016_template_runs_real_schema_prepare_to_rebuild(self) -> None:
        source = self.root / "reviewed-016-source.db"
        template = _create_reviewed_016_source(source)
        self.assertEqual(len(template["tables"]), 136)
        self.assertEqual(sum(
            rule["action"] == "copy_all" for rule in template["tables"].values()
        ), 128)
        self.assertEqual(sum(
            rule["action"] != "copy_all" for rule in template["tables"].values()
        ), 8)
        with closing(MODULE._source_connection(source)) as connection:
            objects = MODULE._schema_objects(connection)
        self.assertEqual(
            MODULE._schema_digest(objects),
            "5cf495be1b5cc6d27e8a0526134dcb6f1eda4a2811d754cea8a12cafa9344e4f",
        )

        source_hash = _sha256(source)
        first_applied, first_backup = MIGRATION.apply_migration(
            source, None, external_recovery_confirmed=True,
        )
        second_applied, second_backup = MIGRATION.apply_migration(
            source, None, external_recovery_confirmed=True,
        )
        self.assertFalse(first_applied)
        self.assertIsNone(first_backup)
        self.assertFalse(second_applied)
        self.assertIsNone(second_backup)
        self.assertEqual(_sha256(source), source_hash)

        case_root = self.root / "reviewed-016-run"
        manifest = case_root / "SOURCE_MANIFEST.json"
        rules = case_root / "rules.json"
        prepare_report = case_root / "prepare-report.json"
        as_of = "2026-10-28T12:00:00+08:00"
        prepare_process = subprocess.run([
            sys.executable, str(MODULE_PATH), "prepare",
            "--source", str(source),
            "--template-rules", str(REVIEWED_016_TEMPLATE),
            "--as-of", as_of,
            "--rules-version", "fixture-r7-016-final",
            "--manifest-output", str(manifest),
            "--rules-output", str(rules),
            "--report", str(prepare_report),
            "--consistent-source-confirmed",
        ], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(prepare_process.returncode, 0, prepare_process.stderr)
        prepared = json.loads(prepare_report.read_text(encoding="utf-8"))
        self.assertTrue(prepared["schema_comparison"]["reviewed_schema_objects_present"])
        self.assertEqual(prepared["schema_comparison"]["changed_objects"], [])
        prepared_rules = json.loads(rules.read_text(encoding="utf-8"))
        self.assertNotIn("reviewed_schema_objects", prepared_rules)
        self.assertEqual(prepared_rules["source_schema_sha256"], template["source_schema_sha256"])

        target = case_root / "target.db"
        rebuild_report = case_root / "rebuild-report.json"
        rebuild_process = subprocess.run([
            sys.executable, str(MODULE_PATH), "rebuild",
            "--source", str(source),
            "--source-manifest", str(manifest),
            "--target", str(target),
            "--rules", str(rules),
            "--as-of", as_of,
            "--report", str(rebuild_report),
            "--archive", str(case_root / "target.zip"),
            "--temp-dir", str(case_root / "sqlite-temp"),
            "--minimum-free-bytes", "1",
            "--space-requirement-basis", "reviewed 016 schema fixture",
        ], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(rebuild_process.returncode, 0, rebuild_process.stderr)
        result = json.loads(rebuild_report.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["target_migrations"]["applied"])
        with closing(sqlite3.connect(target)) as connection:
            value = connection.execute(
                "SELECT raw_value,raw_value_text FROM observation_values WHERE id=1"
            ).fetchone()
            versions = {
                row[0] for row in connection.execute(
                    "SELECT version FROM schema_migrations WHERE version IN (?,?)",
                    tuple(template["target_migrations"]),
                )
            }
        self.assertEqual(value, (1.805, "1.8050"))
        self.assertEqual(versions, set(template["target_migrations"]))

    def test_reviewed_014_template_remains_applicable(self) -> None:
        source = self.root / "reviewed-014-source.db"
        template_016 = json.loads(REVIEWED_016_TEMPLATE.read_text(encoding="utf-8"))
        schema_objects = template_016["reviewed_schema_objects"]
        changed = 0
        with closing(sqlite3.connect(source)) as connection:
            for item in schema_objects:
                sql = item["sql"]
                if item["type"] == "table" and item["name"] == "observation_values":
                    updated = sql.replace(", raw_value_text TEXT", "")
                    self.assertNotEqual(updated, sql)
                    sql = updated
                    changed += 1
                connection.execute(sql)
            connection.commit()
        self.assertEqual(changed, 1)
        with closing(MODULE._source_connection(source)) as connection:
            objects = MODULE._schema_objects(connection)
        self.assertEqual(
            MODULE._schema_digest(objects),
            "993e61170b0e427c28ea2a783801f1d9dc2bbb53c9ecbff7195c1187d8fd45b4",
        )
        template_014 = json.loads(REVIEWED_014_TEMPLATE.read_text(encoding="utf-8"))
        self.assertTrue(MODULE._validate_reviewed_template(template_014, objects)["matches"])
        result = MODULE.prepare(argparse.Namespace(
            source=source,
            template_rules=REVIEWED_014_TEMPLATE,
            as_of="2026-10-28T12:00:00+08:00",
            rules_version="fixture-r7-014-retained",
            manifest_output=self.root / "reviewed-014-manifest.json",
            rules_output=self.root / "reviewed-014-rules.json",
            report=self.root / "reviewed-014-prepare.json",
            consistent_source_confirmed=True,
        ))
        self.assertEqual(result["status"], "completed")

    def test_reviewed_016_template_reports_changed_schema_object(self) -> None:
        source = self.root / "reviewed-016-changed.db"
        _create_reviewed_016_source(source)
        with closing(sqlite3.connect(source)) as connection:
            connection.execute(
                "ALTER TABLE observation_values ADD COLUMN unexpected_precision_note TEXT"
            )
            connection.commit()
        report = self.root / "reviewed-016-rejected.json"
        with self.assertRaisesRegex(MODULE.RebuildError, "source schema differs"):
            MODULE.prepare(argparse.Namespace(
                source=source,
                template_rules=REVIEWED_016_TEMPLATE,
                as_of="2026-10-28T12:00:00+08:00",
                rules_version="fixture-r7-016-rejected",
                manifest_output=self.root / "rejected-manifest.json",
                rules_output=self.root / "rejected-rules.json",
                report=report,
                consistent_source_confirmed=True,
            ))
        evidence = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(evidence["schema_comparison"]["added_tables"], [])
        self.assertEqual(evidence["schema_comparison"]["removed_tables"], [])
        self.assertEqual(
            evidence["schema_comparison"]["changed_objects"],
            ["table:observation_values"],
        )
        self.assertFalse((self.root / "rejected-manifest.json").exists())
        self.assertFalse((self.root / "rejected-rules.json").exists())

    def test_prepare_rejects_schema_change_without_executable_outputs(self) -> None:
        template = self._approved_rules()
        template_path = self.root / "reviewed-template.json"
        template_path.write_text(json.dumps(template, indent=2), encoding="utf-8")
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("CREATE TABLE unexpected_final_source_table(id INTEGER PRIMARY KEY)")
            connection.commit()
        manifest = self.root / "final-manifest.json"
        rules = self.root / "final-rules.json"
        report = self.root / "prepare-rejected.json"
        with self.assertRaisesRegex(MODULE.RebuildError, "source schema differs"):
            MODULE.prepare(argparse.Namespace(
                source=self.source,
                template_rules=template_path,
                as_of=AS_OF,
                rules_version="fixture-schema-change",
                manifest_output=manifest,
                rules_output=rules,
                report=report,
                consistent_source_confirmed=True,
            ))
        evidence = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "schema_rejected")
        self.assertEqual(
            evidence["schema_comparison"]["added_tables"],
            ["unexpected_final_source_table"],
        )
        self.assertFalse(manifest.exists())
        self.assertFalse(rules.exists())

    def test_rebuild_rejects_pending_rules_and_marks_incomplete(self) -> None:
        self._inventory()
        report = self.root / "report.json"
        with self.assertRaisesRegex(MODULE.RebuildError, "rules are not approved"):
            MODULE.rebuild(argparse.Namespace(
                source=self.source,
                source_manifest=self.manifest,
                target=self.root / "target.db",
                rules=self.rules,
                as_of=AS_OF,
                report=report,
                archive=self.root / "target.zip",
            ))
        marker = report.with_suffix(".json.incomplete.json")
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["status"], "incomplete")

    def test_rebuild_preserves_source_schema_types_sequences_and_safe_closure(self) -> None:
        self._approved_rules()
        source_hash = _sha256(self.source)
        result, target, report, archive = self._rebuild()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(_sha256(self.source), source_hash)
        self.assertTrue(report.is_file())
        self.assertTrue(archive.is_file())
        self.assertEqual(result["integrity_check"], "ok")
        self.assertEqual(result["foreign_key_violations"], [])
        self.assertEqual(result["excluded_exact_duplicate_raw_ids"]["count"], 2)
        with closing(sqlite3.connect(target)) as connection:
            connection.row_factory = sqlite3.Row
            raw_ids = [row[0] for row in connection.execute(
                "SELECT id FROM ingest_raw_frames ORDER BY id"
            )]
            self.assertEqual(raw_ids, [1, 3, 4, 5, 6, 8])
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM ingest_parse_attempts").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM ingest_frame_protocols").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM station_ingest_dedup_keys").fetchone()[0], 0
            )
            self.assertIsNone(connection.execute(
                "SELECT last_complete_raw_frame_id FROM station_ingest_receipt_hourly"
            ).fetchone()[0])
            self.assertIsNone(connection.execute(
                "SELECT last_raw_frame_id FROM station_ingest_error_hourly"
            ).fetchone()[0])
            site = connection.execute(
                "SELECT name,reading,payload,optional_value FROM sites WHERE id=1"
            ).fetchone()
            self.assertEqual(tuple(site), ("source-site", 1.25, b"\x00\xff", None))
            sequence = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='sites'"
            ).fetchone()[0]
            self.assertEqual(sequence, 99)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM site_audit").fetchone()[0], 0)
            connection.execute(
                "INSERT INTO sites(name,reading,payload) VALUES ('after-rebuild',2,X'01')"
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM site_audit").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM site_names").fetchone()[0], 2)

    def test_existing_target_is_never_overwritten(self) -> None:
        self._approved_rules()
        target = self.root / "target.db"
        target.write_bytes(b"keep-me")
        with self.assertRaisesRegex(MODULE.RebuildError, "output already exists"):
            MODULE.rebuild(argparse.Namespace(
                source=self.source,
                source_manifest=self.manifest,
                target=target,
                rules=self.rules,
                as_of=AS_OF,
                report=self.root / "report.json",
                archive=self.root / "target.zip",
            ))
        self.assertEqual(target.read_bytes(), b"keep-me")

    def test_offline_r4_selection_keeps_business_activity_without_diagnostic_history(self) -> None:
        self._inventory()
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute(
                "UPDATE ingest_raw_frames SET received_at='2026-09-01 00:00:00'"
            )
            connection.execute(
                "UPDATE ingest_raw_frames SET received_at='2026-10-14 11:28:59' WHERE id=5"
            )
            connection.execute(
                """UPDATE ingest_raw_frames
                      SET endpoint_id=NULL,authentication_status='unknown_endpoint',
                          disposition='quarantined'
                    WHERE id=4"""
            )
            connection.execute(
                "UPDATE ingest_errors SET error_type='unknown_endpoint' WHERE raw_frame_id=4"
            )
            connection.execute(
                """INSERT INTO ingest_errors(raw_frame_id,error_type,status,created_at)
                     VALUES (2,'source_cooling','open','2026-09-01 00:00:00'),
                            (5,'source_connection_limit','open','2026-10-21 11:00:00')"""
            )
            connection.execute(
                "INSERT INTO ingest_frame_protocols(raw_frame_id,protocol_family) VALUES (4,'hj212')"
            )
            connection.executemany(
                """INSERT INTO ingest_parse_attempts(raw_frame_id,parser_version,created_at)
                     VALUES (?,'v2',?)""",
                [
                    (3, "2026-09-01 00:00:00"),
                    (4, "2026-09-01T00:00:00Z"),
                    (5, "2026-10-18 11:28:59"),
                ],
            )
            connection.execute("DELETE FROM station_ingest_dedup_keys")
            connection.execute("DELETE FROM station_ingest_receipt_hourly")
            connection.execute("DELETE FROM station_ingest_error_hourly")
            connection.execute(
                "DELETE FROM station_ingest_recent_receipts WHERE raw_frame_id IN (2,7)"
            )
            connection.commit()
        _manifest(self.manifest, self.source)

        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        rules["version"] = "GOV-OFFLINE-REBUILD-20261028-R4-TEST"
        rules["review_status"] = "approved"
        rules["source_manifest_sha256"] = _sha256(self.manifest)
        rules["selection_policy"] = {
            "review_status": "approved",
            "raw_received_from": "2026-10-14T11:28:59Z",
            "parse_created_from": "2026-10-18T11:28:59Z",
            "error_policy": "active_unknown_endpoint_reauthentication_only",
        }
        for rule in rules["tables"].values():
            rule["review_status"] = "approved"
        rules["tables"]["ingest_raw_frames"]["action"] = "copy_retained_raw_rows"
        rules["tables"]["ingest_parse_attempts"]["action"] = "copy_retained_parse_rows"
        rules["tables"]["ingest_errors"]["action"] = "copy_retained_error_rows"
        for table in (
            "ingest_frame_protocols", "monitoring_raw_archive_part_frames",
            "station_ingest_duplicate_links", "station_ingest_recent_receipts",
        ):
            rules["tables"][table].update({
                "action": "copy_rows_for_retained_raw",
                "raw_reference_column": "raw_frame_id",
            })
        self.rules.write_text(json.dumps(rules, indent=2), encoding="utf-8")

        result, target, _report, _archive = self._rebuild()
        self.assertEqual(result["selection"]["retained_raw_rows"], 5)
        self.assertEqual(result["selection"]["retained_parse_rows"], 3)
        self.assertEqual(result["selection"]["retained_error_rows"], 1)
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(
                [row[0] for row in connection.execute(
                    "SELECT id FROM ingest_raw_frames ORDER BY id")],
                [1, 3, 4, 5, 8],
            )
            self.assertEqual(
                [row[0] for row in connection.execute(
                    "SELECT raw_frame_id FROM ingest_parse_attempts ORDER BY raw_frame_id")],
                [3, 4, 5],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT raw_frame_id,error_type,status FROM ingest_errors"
                ).fetchone(),
                (4, "unknown_endpoint", "open"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT raw_frame_id FROM ingest_frame_protocols"
                ).fetchone()[0],
                4,
            )
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM monitoring_raw_archive_part_frames"
            ).fetchone()[0], 0)

    def test_preexisting_source_foreign_key_violation_is_reported_without_becoming_new(self) -> None:
        self._approved_rules()
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute(
                "INSERT INTO site_audit(site_id,action) VALUES (999,'legacy-orphan')"
            )
            connection.commit()
            source_violation = dict(zip(
                ("table", "rowid", "parent", "fkid"),
                connection.execute("PRAGMA foreign_key_check").fetchone(),
            ))
        _manifest(self.manifest, self.source)
        rules = json.loads(self.rules.read_text(encoding="utf-8"))
        rules["source_manifest_sha256"] = _sha256(self.manifest)
        rules["allowed_source_foreign_key_violations"] = [source_violation]
        self.rules.write_text(json.dumps(rules, indent=2), encoding="utf-8")

        result, _target, _report, _archive = self._rebuild()
        self.assertEqual(result["integrity_check"], "ok")
        self.assertEqual(result["new_foreign_key_violations"], [])
        self.assertEqual(result["foreign_key_violations"], [source_violation])


if __name__ == "__main__":
    unittest.main()
