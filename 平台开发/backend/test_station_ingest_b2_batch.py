import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from migrate_station_ingestion import apply_migration
import station_ingest_provision as provision


class StationIngestB2BatchTest(unittest.TestCase):
    NOW = datetime(2026, 9, 20, 1, 30, tzinfo=timezone.utc)
    BOUNDARY = "2026-09-20T04:00:00+00:00"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "isolated-b2.db"
        self.matrix = self.root / "identity-factor-matrix.json"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL)")
            connection.executemany(
                "INSERT INTO sites(id,code,name,type) VALUES (?,?,?,'water')",
                ((1, "SITE-A", "Alpha"), (2, "SITE-B", "Beta")),
            )
            connection.commit()
        apply_migration(self.database, self.root / "backups")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO trusted_endpoints(station_code,credential_hmac,business_site_id,enabled,endpoint_state)
                   VALUES (?,?,?,1,?)""",
                (("A001", "a" * 64, 1, "bound"), ("B001", "b" * 64, 2, "bound"),
                 ("U001", "c" * 64, None, "unbound")),
            )
            endpoint_id = connection.execute("SELECT id FROM trusted_endpoints WHERE station_code='A001'").fetchone()[0]
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(
                   endpoint_id,business_site_id,instrument_asset_code,timezone,enabled,expected_granularity,
                   expected_interval_seconds,effective_from)
                   VALUES (?,1,'INST-A','Asia/Shanghai',1,'realtime',3600,'2020-01-01T00:00:00+00:00')""",
                (endpoint_id,),
            )
            connection.execute(
                """INSERT INTO monitoring_factor_mappings(
                   endpoint_id,protocol_code,business_metric,instrument_asset_code,expected_interval_seconds,
                   tolerance_seconds,effective_from,enabled)
                   VALUES (?,'HJ212:w01001','ph','INST-A',3600,60,'2020-01-01T00:00:00+00:00',1)""",
                (endpoint_id,),
            )
            connection.execute(
                """INSERT INTO monitoring_business_schedules(
                   endpoint_id,protocol_code,timezone,interval_seconds,anchor_local_time,tolerance_seconds,
                   effective_from,enabled)
                   VALUES (?,'HJ212:w01001','Asia/Shanghai',3600,'00:00:00',600,
                           '2020-01-01T00:00:00+00:00',1)""",
                (endpoint_id,),
            )
            connection.commit()
        self.write_matrix()

    def tearDown(self):
        self.temp_dir.cleanup()

    def identity(self, name, station_code, binding_status, factors):
        return {
            "system_name": name,
            "station_code": station_code,
            "original_name": name,
            "binding_status": binding_status,
            "observed_hj212_factor_codes": factors,
        }

    def write_matrix(self, identities=None):
        document = {
            "report_type": "isolated_b2_matrix",
            "contains_raw_payloads": False,
            "contains_observed_values": False,
            "contains_credentials": False,
            "approved_identities": identities or [
                self.identity("Alpha", "A001", "bound", ["HJ212:w01001", "HJ212:unknown"]),
                self.identity("Beta", "B001", "bound", ["HJ212:w01010"]),
                self.identity("Unbound", "U001", "unbound", ["HJ212:w01001"]),
                self.identity("No Data", "N001", "bound", []),
            ],
        }
        self.matrix.write_text(json.dumps(document), encoding="utf-8")

    def configuration_rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "monitoring_endpoint_profiles", "monitoring_factor_mappings", "monitoring_business_schedules",
            ))

    def test_plan_apply_verify_and_repeat_are_consistent_and_idempotent(self):
        before = self.configuration_rows()
        plan = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        self.assertEqual(plan["result"], "ready")
        self.assertEqual(plan["effective_from"], self.BOUNDARY)
        self.assertEqual((plan["eligible_endpoint_count"], plan["eligible_factor_count"]), (2, 2))
        self.assertEqual({item["category"] for item in plan["soft_differences"]}, {
            "unknown_or_unpublished_factor", "unbound_identity", "no_observed_factors",
        })
        self.assertEqual(self.configuration_rows(), before)
        rendered = json.dumps(plan)
        for private_value in ("A001", "B001", "Alpha", "Beta"):
            self.assertNotIn(private_value, rendered)

        applied = provision.apply_b2_batch(
            self.database, self.matrix, expected_fingerprint=plan["fingerprint"],
            effective_from=plan["effective_from"], offline_confirmed=True, now=self.NOW,
        )
        self.assertEqual(applied["result"], "applied")
        self.assertEqual(self.configuration_rows(), (3, 3, 3))
        with closing(sqlite3.connect(self.database)) as connection:
            old_periods = tuple(connection.execute(
                """SELECT profile.expected_interval_seconds,profile.effective_to,
                          mapping.expected_interval_seconds,mapping.tolerance_seconds,mapping.effective_to,
                          schedule.interval_seconds,schedule.result_delay_seconds,schedule.effective_to
                   FROM monitoring_endpoint_profiles profile
                   JOIN monitoring_factor_mappings mapping ON mapping.endpoint_id=profile.endpoint_id
                   JOIN monitoring_business_schedules schedule ON schedule.endpoint_id=profile.endpoint_id
                   WHERE profile.effective_from='2020-01-01T00:00:00+00:00'
                     AND mapping.effective_from=profile.effective_from AND schedule.effective_from=profile.effective_from"""
            ).fetchone())
        self.assertEqual(old_periods, (
            3600, self.BOUNDARY, 3600, 60, self.BOUNDARY, 3600, 0, self.BOUNDARY,
        ))

        verified = provision.verify_b2_batch(self.database, self.matrix, effective_from=self.BOUNDARY)
        self.assertEqual(verified["result"], "verified", verified)
        self.assertEqual((verified["profile_count"], verified["mapping_count"], verified["schedule_count"]), (2, 2, 2))
        with closing(sqlite3.connect(self.database)) as connection:
            schedules = connection.execute(
                """SELECT DISTINCT interval_seconds,tolerance_seconds,result_delay_seconds
                   FROM monitoring_business_schedules WHERE effective_from=?""",
                (self.BOUNDARY,),
            ).fetchall()
        self.assertEqual(schedules, [(14400, 0, 7200)])
        after_verify = self.configuration_rows()
        self.assertEqual(after_verify, (3, 3, 3))

        repeat_plan = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        repeated = provision.apply_b2_batch(
            self.database, self.matrix, expected_fingerprint=repeat_plan["fingerprint"],
            effective_from=repeat_plan["effective_from"], offline_confirmed=True, now=self.NOW,
        )
        self.assertEqual(repeated["write_count"], 0)
        self.assertEqual(self.configuration_rows(), after_verify)

    def test_verify_before_apply_is_read_only_and_reports_missing_configuration(self):
        before = self.configuration_rows()
        result = provision.verify_b2_batch(self.database, self.matrix, effective_from=self.BOUNDARY)
        self.assertEqual(result["result"], "conflicted")
        self.assertGreater(result["hard_error_count"], 0)
        self.assertEqual(self.configuration_rows(), before)

    def test_real_published_factor_set_is_eligible_and_unknown_legacy_codes_are_soft(self):
        published = [
            "HJ212:005", "HJ212:022", "HJ212:027", "HJ212:029", "HJ212:030",
            "HJ212:w01001", "HJ212:w01003", "HJ212:w01009", "HJ212:w01010",
            "HJ212:w01014", "HJ212:w01019-Rtd", "HJ212:w21001", "HJ212:w21003", "HJ212:w21011",
        ]
        self.write_matrix([
            self.identity("Alpha", "A001", "bound", published + ["HJ212:001", "HJ212:274"]),
        ])
        plan = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        self.assertEqual(plan["result"], "ready", plan)
        self.assertEqual(plan["eligible_factor_count"], len(published))
        self.assertEqual(
            {item.get("protocol_code") for item in plan["soft_differences"]},
            {"HJ212:001", "HJ212:274"},
        )

    def test_changed_matrix_and_expired_boundary_are_rejected_without_writes(self):
        plan = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        before = self.configuration_rows()
        self.write_matrix([
            self.identity("Alpha", "A001", "bound", ["HJ212:w01001", "HJ212:w01010"]),
        ])
        with self.assertRaisesRegex(provision.ProvisionError, "fingerprint or input matrix changed"):
            provision.apply_b2_batch(
                self.database, self.matrix, expected_fingerprint=plan["fingerprint"],
                effective_from=plan["effective_from"], offline_confirmed=True, now=self.NOW,
            )
        self.assertEqual(self.configuration_rows(), before)
        self.write_matrix()
        with self.assertRaisesRegex(provision.ProvisionError, "boundary has passed"):
            provision.apply_b2_batch(
                self.database, self.matrix, expected_fingerprint=plan["fingerprint"],
                effective_from=plan["effective_from"], offline_confirmed=True,
                now=datetime(2026, 9, 20, 4, 0, 1, tzinfo=timezone.utc),
            )
        self.assertEqual(self.configuration_rows(), before)

    def test_binding_and_boundary_conflicts_are_hard_errors(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE sites SET name='Wrong Site' WHERE id=2")
            connection.commit()
        plan = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        self.assertEqual(plan["result"], "conflicted")
        self.assertIn("business_site_binding_mismatch", {item["category"] for item in plan["hard_errors"]})
        before = self.configuration_rows()
        with self.assertRaisesRegex(provision.ProvisionError, "hard configuration errors"):
            provision.apply_b2_batch(
                self.database, self.matrix, expected_fingerprint=plan["fingerprint"],
                effective_from=plan["effective_from"], offline_confirmed=True, now=self.NOW,
            )
        self.assertEqual(self.configuration_rows(), before)

        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE sites SET name='Beta' WHERE id=2")
            endpoint_id = connection.execute(
                "SELECT id FROM trusted_endpoints WHERE station_code='A001'"
            ).fetchone()[0]
            connection.execute(
                "UPDATE monitoring_endpoint_profiles SET effective_to=? WHERE endpoint_id=?",
                (self.BOUNDARY, endpoint_id),
            )
            connection.execute(
                """INSERT INTO monitoring_endpoint_profiles(
                   endpoint_id,business_site_id,timezone,enabled,expected_granularity,
                   expected_interval_seconds,effective_from)
                   VALUES (?,1,'Asia/Shanghai',1,'realtime',3600,?)""",
                (endpoint_id, self.BOUNDARY),
            )
            connection.commit()
        boundary_conflict = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        self.assertEqual(boundary_conflict["result"], "conflicted")
        self.assertIn("profile_boundary_conflict", {
            item["category"] for item in boundary_conflict["hard_errors"]
        })

    def test_database_failure_rolls_back_the_entire_batch(self):
        plan = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        before = self.configuration_rows()
        original = provision._apply_b2_operations

        def fail_after_first_operation(connection, operations, effective_from):
            original(connection, operations[:1], effective_from)
            raise sqlite3.IntegrityError("isolated forced failure")

        with mock.patch.object(provision, "_apply_b2_operations", side_effect=fail_after_first_operation):
            with self.assertRaises(sqlite3.IntegrityError):
                provision.apply_b2_batch(
                    self.database, self.matrix, expected_fingerprint=plan["fingerprint"],
                    effective_from=plan["effective_from"], offline_confirmed=True, now=self.NOW,
                )
        self.assertEqual(self.configuration_rows(), before)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT effective_to FROM monitoring_endpoint_profiles WHERE effective_from='2020-01-01T00:00:00+00:00'"
            ).fetchone()[0])

    def test_cross_site_asset_ownership_is_a_hard_error(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE authoritative_assets (asset_code TEXT PRIMARY KEY, site_id INTEGER NOT NULL)")
            connection.execute("INSERT INTO authoritative_assets(asset_code,site_id) VALUES ('INST-A',2)")
            connection.commit()
        before = self.configuration_rows()
        plan = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        self.assertEqual(plan["result"], "conflicted")
        self.assertIn("asset_business_site_mismatch", {
            item["category"] for item in plan["hard_errors"]
        })
        with self.assertRaisesRegex(provision.ProvisionError, "hard configuration errors"):
            provision.apply_b2_batch(
                self.database, self.matrix, expected_fingerprint=plan["fingerprint"],
                effective_from=plan["effective_from"], offline_confirmed=True, now=self.NOW,
            )
        self.assertEqual(self.configuration_rows(), before)

    def test_invalid_credential_summary_is_a_hard_zero_write_error(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE trusted_endpoints SET credential_hmac='invalid' WHERE station_code='B001'")
            connection.commit()
        before = self.configuration_rows()
        plan = provision.preview_b2_batch(self.database, self.matrix, now=self.NOW)
        self.assertEqual(plan["result"], "conflicted")
        self.assertIn("invalid_credential_summary", {
            item["category"] for item in plan["hard_errors"]
        })
        with self.assertRaisesRegex(provision.ProvisionError, "hard configuration errors"):
            provision.apply_b2_batch(
                self.database, self.matrix, expected_fingerprint=plan["fingerprint"],
                effective_from=plan["effective_from"], offline_confirmed=True, now=self.NOW,
            )
        self.assertEqual(self.configuration_rows(), before)


if __name__ == "__main__":
    unittest.main()
