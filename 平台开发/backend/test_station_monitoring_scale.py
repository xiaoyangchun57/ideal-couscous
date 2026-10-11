"""Production-shape station monitoring reads.

Prepare and run against an isolated fixture:

  python backend/prepare_station_monitoring_scale_fixture.py --output <temp>/station-monitoring-scale.db
  STATION_MONITORING_SCALE_DB=<temp>/station-monitoring-scale.db python -m unittest backend.test_station_monitoring_scale -v
"""

import json
import os
import sqlite3
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as web_app


SCALE_DATABASE = os.environ.get('STATION_MONITORING_SCALE_DB', '').strip()


@unittest.skipUnless(SCALE_DATABASE, 'set STATION_MONITORING_SCALE_DB to an isolated production-shape fixture')
class StationMonitoringScaleTest(unittest.TestCase):
    """Opt-in gate for the large isolated fixture; never points at the product database."""

    @classmethod
    def setUpClass(cls):
        cls.database = Path(SCALE_DATABASE).resolve()
        if not cls.database.is_file():
            raise AssertionError(f'scale database does not exist: {cls.database}')
        cls.previous_database = web_app.DB_PATH
        cls.previous_access_mode = web_app.STATION_MONITORING_ACCESS_MODE
        web_app.DB_PATH = str(cls.database)
        web_app.STATION_MONITORING_ACCESS_MODE = 'public'

    @classmethod
    def tearDownClass(cls):
        web_app.DB_PATH = cls.previous_database
        web_app.STATION_MONITORING_ACCESS_MODE = cls.previous_access_mode

    def setUp(self):
        self.auth = mock.patch.object(
            web_app,
            '_authenticate_token',
            return_value=({
                'id': 1,
                'username': 'isolated-scale-admin',
                'role': 'admin',
                'roles': ['admin'],
            }, None, None),
        )
        self.auth.start()
        self.addCleanup(self.auth.stop)
        self.client = web_app.app.test_client()
        self.headers = {'Authorization': 'Bearer isolated-scale-token'}

    def test_production_shape_reads_finish_under_five_seconds_and_keep_results(self):
        with closing(sqlite3.connect(self.database)) as connection:
            raw_count = connection.execute('SELECT COUNT(*) FROM ingest_raw_frames').fetchone()[0]
            observation_count = connection.execute(
                'SELECT COUNT(*) FROM monitoring_business_observations').fetchone()[0]
            site_count = connection.execute('SELECT COUNT(*) FROM sites').fetchone()[0]
        self.assertGreaterEqual(raw_count, 2_000_000)
        self.assertGreaterEqual(observation_count, 400_000)
        self.assertEqual(site_count, 36)

        results = {}
        timings = {}
        for path in (
            '/api/station-monitoring/access-summary',
            '/api/station-monitoring/sites?scope=all',
            '/api/station-monitoring/sites/1/overview',
        ):
            samples = []
            response = None
            for _ in range(3):
                started = time.perf_counter()
                response = self.client.get(path, headers=self.headers)
                samples.append(time.perf_counter() - started)
                self.assertEqual(response.status_code, 200, response.get_json())
            self.assertLess(max(samples), 5.0, (path, samples))
            results[path] = response.get_json()
            timings[path] = [round(sample, 4) for sample in samples]

        print('STATION_MONITORING_SCALE_TIMINGS=' + json.dumps(timings, sort_keys=True))

        summary = results['/api/station-monitoring/access-summary']
        self.assertEqual(summary['identity']['received_raw_sites'], 36)
        self.assertEqual(summary['observation']['valid_sites'], 36)
        self.assertIsNotNone(summary['authentication']['last_authenticated_received_at'])
        listed = results['/api/station-monitoring/sites?scope=all']
        self.assertEqual(len(listed['items']), 36)
        self.assertTrue(all(item['published_factor_count'] == 13 for item in listed['items']))
        overview = results['/api/station-monitoring/sites/1/overview']
        self.assertEqual(overview['site']['site_id'], 1)
        self.assertEqual(len(overview['factors']), 13)

    def test_production_shape_query_plans_use_bounded_read_indexes(self):
        with closing(sqlite3.connect(self.database)) as connection:
            raw_plan = connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT endpoint_id,MAX(received_at) FROM ingest_raw_frames
                   INDEXED BY idx_ingest_raw_authenticated_endpoint_received
                   WHERE authentication_status='authenticated' AND endpoint_id IN (?)
                   GROUP BY endpoint_id""",
                (1,),
            ).fetchall()
            recent_plan = connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT id FROM monitoring_business_observations
                   INDEXED BY idx_monitoring_business_current_site_scheduled
                   WHERE business_site_id IN (?) AND is_current=1
                     AND scheduled_at>=? AND scheduled_at<=?""",
                (1, '2026-10-01T00:00:00+00:00', '2026-10-11T00:00:00+00:00'),
            ).fetchall()
            auth_window_plan = connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT authentication_status,COUNT(*),MAX(received_at)
                   FROM ingest_raw_frames INDEXED BY idx_ingest_raw_received
                   WHERE received_at>=? GROUP BY authentication_status""",
                ('2026-10-11T00:00:00+00:00',),
            ).fetchall()
            last_authenticated_plan = connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT MAX(received_at) FROM ingest_raw_frames
                   INDEXED BY idx_ingest_raw_authenticated_received
                   WHERE authentication_status='authenticated'""",
            ).fetchall()
        self.assertIn(
            'idx_ingest_raw_authenticated_endpoint_received',
            ' '.join(str(row[3]) for row in raw_plan),
        )
        self.assertIn(
            'idx_monitoring_business_current_site_scheduled',
            ' '.join(str(row[3]) for row in recent_plan),
        )
        self.assertIn(
            'idx_ingest_raw_received',
            ' '.join(str(row[3]) for row in auth_window_plan),
        )
        self.assertIn(
            'idx_ingest_raw_authenticated_received',
            ' '.join(str(row[3]) for row in last_authenticated_plan),
        )
        print('STATION_MONITORING_SCALE_PLANS=' + json.dumps({
            'last_authenticated': [row[3] for row in last_authenticated_plan],
            'last_by_endpoint': [row[3] for row in raw_plan],
            'current_observations': [row[3] for row in recent_plan],
            'authentication_window': [row[3] for row in auth_window_plan],
        }, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
