import json
import os
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
from sl651_server import credential_hmac
import station_ingest_provision as provision


class StationB2IdentityResolutionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "isolated-identities.db"
        self.manifest = self.root / "resolutions.json"
        self.matrix = self.root / "matrix.json"
        self.pepper = "isolated-resolution-pepper"
        self.credential = b"sharedpw"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "CREATE TABLE sites (id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO sites(id,code,name,type) VALUES (?,?,?,'water')",
                ((1, "ZGJ", "张家港"), (2, "XZ", "星子"),
                 (3, "JAWYG", "吉安五岳观"), (4, "JA", "吉安")),
            )
            connection.commit()
        apply_migration(self.database, self.root / "backups")
        digest = credential_hmac(self.credential, self.pepper)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,enabled,endpoint_state)
                   VALUES (?,?,?,1,?)""",
                (("62484420", digest, None, "unbound"),
                 ("62381830", digest, 3, "bound")),
            )
            connection.commit()
        self.manifest.write_text(json.dumps({
            "report_type": "isolated_b2_identity_resolutions",
            "contains_credentials": False,
            "approved_resolutions": [
                {"canonical_system_name": "张家港", "station_code": "62484420",
                 "approved_aliases": ["上饶余干县自来水公司"]},
                {"canonical_system_name": "星子", "station_code": "62601200", "approved_aliases": []},
                {"canonical_system_name": "吉安五岳观", "station_code": "62381830",
                 "approved_aliases": ["吉安"]},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        self.environment = mock.patch.dict(os.environ, {"SL651_CREDENTIAL_PEPPER": self.pepper})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp_dir.cleanup()

    def endpoint_rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(
                """SELECT station_code,business_site_id,enabled,endpoint_state
                   FROM trusted_endpoints ORDER BY station_code"""
            ).fetchall()

    def write_matrix(self, name="上饶余干县自来水公司", include_all=False):
        identities = [{
            "system_name": name,
            "original_name": name,
            "station_code": "62484420",
            "binding_status": "bound",
            "observed_hj212_factor_codes": ["HJ212:w01001"],
        }]
        if include_all:
            identities.extend(({
                "system_name": "星子",
                "original_name": "星子",
                "station_code": "62601200",
                "binding_status": "bound",
                "observed_hj212_factor_codes": ["HJ212:w01001"],
            }, {
                "system_name": "吉安",
                "original_name": "吉安",
                "station_code": "62381830",
                "binding_status": "bound",
                "observed_hj212_factor_codes": ["HJ212:w01001"],
            }))
        self.matrix.write_text(json.dumps({
            "report_type": "isolated_b2_alias_matrix",
            "contains_raw_payloads": False,
            "contains_observed_values": False,
            "contains_credentials": False,
            "approved_identities": identities,
        }, ensure_ascii=False), encoding="utf-8")

    def test_preview_apply_verify_and_repeat_are_private_atomic_and_idempotent(self):
        preview = provision.preview_b2_identity_resolutions(self.database, self.manifest)
        self.assertEqual(preview["result"], "ready", preview)
        self.assertEqual(
            (preview["resolution_count"], preview["create_count"], preview["bind_count"], preview["reuse_count"]),
            (3, 1, 1, 1),
        )
        rendered = json.dumps(preview, ensure_ascii=False)
        for private_value in ("62484420", "62601200", "62381830", "张家港", "星子", "吉安"):
            self.assertNotIn(private_value, rendered)

        applied = provision.apply_b2_identity_resolutions(
            self.database, self.manifest, credential=self.credential,
            expected_fingerprint=preview["fingerprint"], offline_confirmed=True,
            retire_confirmed=False,
        )
        self.assertEqual(applied["write_count"], 2)
        self.assertEqual(self.endpoint_rows(), [
            ("62381830", 3, 1, "bound"),
            ("62484420", 1, 1, "bound"),
            ("62601200", 2, 1, "bound"),
        ])
        verified = provision.verify_b2_identity_resolutions(
            self.database, self.manifest, credential=self.credential)
        self.assertEqual(verified["verified_count"], 3)

        repeat = provision.preview_b2_identity_resolutions(self.database, self.manifest)
        self.assertEqual((repeat["create_count"], repeat["bind_count"], repeat["reuse_count"]), (0, 0, 3))
        repeated = provision.apply_b2_identity_resolutions(
            self.database, self.manifest, credential=self.credential,
            expected_fingerprint=repeat["fingerprint"], offline_confirmed=True,
            retire_confirmed=False,
        )
        self.assertEqual(repeated["write_count"], 0)

    def test_changed_preview_and_missing_retirement_confirmation_write_nothing(self):
        preview = provision.preview_b2_identity_resolutions(self.database, self.manifest)
        before = self.endpoint_rows()
        with self.assertRaisesRegex(provision.ProvisionError, "changed after preview"):
            provision.apply_b2_identity_resolutions(
                self.database, self.manifest, credential=self.credential,
                expected_fingerprint="sha256:changed", offline_confirmed=True,
                retire_confirmed=False,
            )
        self.assertEqual(self.endpoint_rows(), before)

        digest = credential_hmac(self.credential, self.pepper)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO trusted_endpoints(
                       station_code,credential_hmac,business_site_id,enabled,endpoint_state)
                   VALUES ('XZ-OLD',?,2,1,'bound')""",
                (digest,),
            )
            connection.commit()
        retirement = provision.preview_b2_identity_resolutions(self.database, self.manifest)
        self.assertEqual(retirement["retire_count"], 1)
        before_retirement = self.endpoint_rows()
        with self.assertRaisesRegex(provision.ProvisionError, "explicit confirmation"):
            provision.apply_b2_identity_resolutions(
                self.database, self.manifest, credential=self.credential,
                expected_fingerprint=retirement["fingerprint"], offline_confirmed=True,
                retire_confirmed=False,
            )
        self.assertEqual(self.endpoint_rows(), before_retirement)

    def test_approved_alias_drives_b2_preview_and_locks_manifest_and_matrix(self):
        identity_preview = provision.preview_b2_identity_resolutions(self.database, self.manifest)
        provision.apply_b2_identity_resolutions(
            self.database, self.manifest, credential=self.credential,
            expected_fingerprint=identity_preview["fingerprint"], offline_confirmed=True,
            retire_confirmed=False,
        )
        self.write_matrix(include_all=True)

        plan = provision.preview_b2_batch(
            self.database, self.matrix, identity_resolution_path=self.manifest,
            now=datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(plan["result"], "ready", plan)
        self.assertEqual((plan["eligible_endpoint_count"], plan["eligible_factor_count"]), (3, 3))
        applied = provision.apply_b2_batch(
            self.database, self.matrix, identity_resolution_path=self.manifest,
            expected_fingerprint=plan["fingerprint"], effective_from=plan["effective_from"],
            offline_confirmed=True, now=datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(applied["write_count"], 9)

        repeat = provision.preview_b2_batch(
            self.database, self.matrix, identity_resolution_path=self.manifest,
            now=datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
        )
        repeated = provision.apply_b2_batch(
            self.database, self.matrix, identity_resolution_path=self.manifest,
            expected_fingerprint=repeat["fingerprint"], effective_from=repeat["effective_from"],
            offline_confirmed=True, now=datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(repeated["write_count"], 0)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                """SELECT COUNT(*) FROM trusted_endpoints
                   WHERE station_code='62484420' AND business_site_id=1
                     AND enabled=1 AND endpoint_state='bound'"""
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                """SELECT COUNT(*) FROM monitoring_endpoint_profiles
                   WHERE business_site_id IN (1,2,3)"""
            ).fetchone()[0], 3)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM monitoring_endpoint_profiles WHERE business_site_id=4"
            ).fetchone()[0], 0)

        locked = provision.preview_b2_batch(
            self.database, self.matrix, identity_resolution_path=self.manifest,
            now=datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
        )
        document = json.loads(self.manifest.read_text(encoding="utf-8"))
        document["approved_resolutions"][0]["approved_aliases"].append("未锁定的新别名")
        self.manifest.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(provision.ProvisionError, "fingerprint or input matrix changed"):
            provision.apply_b2_batch(
                self.database, self.matrix, identity_resolution_path=self.manifest,
                expected_fingerprint=locked["fingerprint"], effective_from=locked["effective_from"],
                offline_confirmed=True, now=datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
            )

    def test_unapproved_alias_remains_a_hard_b2_conflict(self):
        identity_preview = provision.preview_b2_identity_resolutions(self.database, self.manifest)
        provision.apply_b2_identity_resolutions(
            self.database, self.manifest, credential=self.credential,
            expected_fingerprint=identity_preview["fingerprint"], offline_confirmed=True,
            retire_confirmed=False,
        )
        self.write_matrix("未批准张家港别名")
        plan = provision.preview_b2_batch(
            self.database, self.matrix, identity_resolution_path=self.manifest,
            now=datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(plan["result"], "conflicted")
        self.assertIn("business_site_binding_mismatch", {
            item["category"] for item in plan["hard_errors"]
        })


if __name__ == "__main__":
    unittest.main()
