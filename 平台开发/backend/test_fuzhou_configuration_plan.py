import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hj212_parser import hj212_crc
from migrate_station_ingestion import apply_migration
from sl651_server import IngestionStorage, StationIngestServer, credential_hmac
from station_ingest_provision import execute, load_configuration
from station_monitoring import latest_cycle, latest_values, normalize_raw_frame


def make_hj212(*, station: str, password: str, qn: str, data_time: str,
               factors: str) -> bytes:
    body = (
        f"QN={qn};ST=21;CN=2061;PW={password};MN={station};Flag=9;"
        f"CP=&&DataTime={data_time};{factors}&&"
    ).encode("ascii")
    return (b"##" + f"{len(body):04d}".encode("ascii") + body
            + hj212_crc(body).encode("ascii") + b"\r\n")


class FuzhouConfigurationPlanTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = self.root / "isolated-fuzhou.db"
        self.pepper = "isolated-fuzhou-pepper"
        self.password = "isolated-fuzhou-password"
        self.station = "62481540"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """CREATE TABLE sites(
                       id INTEGER PRIMARY KEY, code TEXT NOT NULL,
                       name TEXT NOT NULL, type TEXT NOT NULL)""")
            connection.execute(
                "INSERT INTO sites(id,code,name,type) VALUES (325,'FUZHOU-WW','抚州水厂','water')")
            connection.commit()
        apply_migration(self.database, self.root / "backups")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO trusted_endpoints(
                       id,station_code,credential_hmac,business_site_id,endpoint_state)
                   VALUES (6,?,?,325,'bound')""",
                (self.station, credential_hmac(self.password.encode("ascii"), self.pepper)),
            )
            connection.commit()
        self.plan_path = (
            Path(__file__).resolve().parent / "configuration_plans"
            / "20261013_fuzhou_waterworks_hj212.json")
        self.configuration = load_configuration(self.plan_path)
        self.storage = IngestionStorage(self.database, self.pepper)
        self.server = StationIngestServer(self.storage, host="127.0.0.1", port=0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _raw_ids(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return [row[0] for row in connection.execute(
                "SELECT id FROM ingest_raw_frames ORDER BY id")]

    def _set_expected_factors(self):
        metrics = (
            "dissolved_oxygen", "ph", "turbidity", "water_temp",
            "conductivity", "codmn", "ammonia",
        )
        with closing(sqlite3.connect(self.database)) as connection:
            config_id = connection.execute(
                """INSERT INTO station_expected_factor_configs(
                       site_id,version,effective_from,previous_metrics_json,
                       selected_metrics_json,changed_by_user_id,idempotency_key,created_at)
                   VALUES (325,1,'2026-10-10T01:00:00+00:00','[]',?,0,
                           'isolated-fuzhou-v1','2026-10-10T01:00:00+00:00')""",
                ('["dissolved_oxygen","ph","turbidity","water_temp",'
                 '"conductivity","codmn","ammonia"]',),
            ).lastrowid
            connection.executemany(
                """INSERT INTO station_expected_factors(
                       config_id,site_id,business_metric,display_order,effective_from)
                   VALUES (?,325,?,?, '2026-10-10T01:00:00+00:00')""",
                ((config_id, metric, index) for index, metric in enumerate(metrics)),
            )
            connection.commit()

    def _persist_evidence_frames(self):
        frames = (
            ("20261010030604001", "20261010090000",
             "w01019-Avg=5.9,w01019-Flag=N", "2026-10-10T03:06:04+00:00"),
            ("20261010031024001", "20261010100000",
             "w01019-Avg=5.8,w01019-Flag=N", "2026-10-10T03:10:24+00:00"),
            ("20261010050605001", "20261010120000",
             "w01019-Avg=5.7,w01019-Flag=N", "2026-10-10T05:06:05+00:00"),
            ("20261010170805001", "20261011000000", ";".join((
                "w01001-Avg=7.04,w01001-Flag=N",
                "w01010-Avg=26.37,w01010-Flag=N",
                "w01014-Avg=153.826,w01014-Flag=N",
                "w01009-Avg=7.658,w01009-Flag=N",
                "w01003-Avg=5.04,w01003-Flag=N",
            )), "2026-10-10T17:08:05+00:00"),
            ("20261010220809001", "20261011040000",
             "w21003-Avg=0.0303,w21003-Flag=N", "2026-10-10T22:08:09+00:00"),
        )
        for qn, data_time, factors, received_at in frames:
            result = self.server._process_raw_result(make_hj212(
                station=self.station, password=self.password, qn=qn,
                data_time=data_time, factors=factors), received_at)
            self.assertTrue(result.keep_connection)

    def test_plan_replays_real_shapes_without_combining_different_cycles(self):
        before = self.database.stat().st_size
        self.assertEqual(execute(
            "plan", self.database, self.configuration)["result"], "ready")
        self.assertEqual(self.database.stat().st_size, before)
        self.assertEqual(execute(
            "apply", self.database, self.configuration,
            offline_confirmed=True)["result"], "applied")
        self.assertEqual(execute(
            "verify", self.database, self.configuration)["result"], "verified")
        self._set_expected_factors()
        self._persist_evidence_frames()
        raw_ids = self._raw_ids()
        self.assertEqual(len(raw_ids), 5)
        self.assertEqual(
            [normalize_raw_frame(self.database, raw_id) for raw_id in raw_ids],
            ["partial", "partial", "accepted", "accepted", "accepted"],
        )

        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM ingest_errors WHERE error_type='configuration_pending'"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM observation_values WHERE is_current=1 AND is_published=1"
            ).fetchone()[0], 9)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM monitoring_business_observations WHERE is_current=1"
            ).fetchone()[0], 7)
            values = latest_values(
                connection, 325, at_time="2026-10-10T22:11:00+00:00")
            cycle = latest_cycle(
                connection, 325, at_time="2026-10-10T22:11:00+00:00")
        self.assertEqual({item["business_metric"] for item in values}, {
            "dissolved_oxygen", "ph", "turbidity", "water_temp",
            "conductivity", "codmn", "ammonia",
        })
        self.assertEqual(cycle["status"], "incomplete")
        self.assertEqual(
            (cycle["formed_count"], cycle["expected_count"], cycle["missing_count"]),
            (1, 7, 6),
        )
        self.assertIsNone(cycle["latest_complete_cycle_at"])

        full_cycle = ";".join((
            "w01001-Avg=7.10,w01001-Flag=N",
            "w01010-Avg=25.10,w01010-Flag=N",
            "w01014-Avg=150.0,w01014-Flag=N",
            "w01009-Avg=7.2,w01009-Flag=N",
            "w01003-Avg=4.0,w01003-Flag=N",
            "w01019-Avg=5.6,w01019-Flag=N",
            "w21003-Avg=0.04,w21003-Flag=N",
        ))
        self.server._process_raw_result(make_hj212(
            station=self.station, password=self.password,
            qn="20261011050500001", data_time="20261011120000",
            factors=full_cycle), "2026-10-11T05:05:00+00:00")
        newest = self._raw_ids()[-1]
        self.assertEqual(normalize_raw_frame(self.database, newest), "accepted")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            cycle = latest_cycle(
                connection, 325, at_time="2026-10-11T07:00:00+00:00")
        self.assertEqual(cycle["status"], "complete")
        self.assertEqual((cycle["formed_count"], cycle["expected_count"]), (7, 7))


if __name__ == "__main__":
    unittest.main()
