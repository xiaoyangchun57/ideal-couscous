"""v2.4: database-versioned list cache and real receiver publication."""
import asyncio
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import station_monitoring_api as monitoring_api
import test_station_expected_factors as fixture_module

METRICS = ['ph', 'water_temp', 'dissolved_oxygen', 'conductivity', 'turbidity',
           'codmn', 'ammonia', 'total_phosphorus', 'total_nitrogen']
CP_FACTORS = ('001-Rtd=7.198,001-Flag=N;004-Rtd=23.960,004-Flag=N;'
              '005-Rtd=7.754,005-Flag=N;006-Rtd=184.460,006-Flag=N;'
              '016-Rtd=1.805,016-Flag=N;022-Rtd=0.000,022-Flag=N;'
              '027-Rtd=0.000,027-Flag=N;029-Rtd=0.000,029-Flag=N;'
              '030-Rtd=0.000,030-Flag=N;060-Rtd=0.032,060-Flag=N;'
              '065-Rtd=1.117,065-Flag=N;081-Rtd=0.001,081-Flag=N;'
              '101-Rtd=0.041,101-Flag=N;153-Rtd=2.918,153-Flag=N;'
              '274-Rtd=1027870.000,274-Flag=N')


class StationResultCacheV24Test(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixture_module.StationExpectedFactorsTest()
        self.fixture.setUp()
        self.addAsyncCleanup(self._cleanup_fixture)
        self.endpoint, self.station, self.password, _, self.server = (
            self.fixture.install_bound_hj212_endpoint())
        self.headers = self.fixture.headers('expected-admin')
        saved = self.fixture.client.put(
            f'/api/station-monitoring/sites/{self.fixture.site_id}/expected-factors',
            headers=self.headers,
            json={'metrics': METRICS, 'version': 0,
                  'idempotency_key': 'v24-initial-selection'},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        monitoring_api._invalidate_station_monitoring_projection_caches()
        await self.server.start()
        self.reader, self.writer = await asyncio.open_connection(
            '127.0.0.1', self.server._server.sockets[0].getsockname()[1])

    async def _cleanup_fixture(self):
        self.writer.close()
        await self.writer.wait_closed()
        await self.server.close()
        self.fixture.tearDown()

    def list_sites(self):
        response = self.fixture.client.get(
            '/api/station-monitoring/sites?scope=all', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return next(item for item in response.get_json()['items']
                    if item['site_id'] == self.fixture.site_id)

    def latest(self):
        response = self.fixture.client.get(
            f'/api/station-monitoring/sites/{self.fixture.site_id}/latest',
            headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['items']

    def overview(self):
        response = self.fixture.client.get(
            f'/api/station-monitoring/sites/{self.fixture.site_id}/overview',
            headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    async def send_frame(self, sequence, *, ph='7.198'):
        observed = datetime.now(timezone.utc) - timedelta(minutes=10-sequence)
        data_time = (observed + timedelta(hours=8)).strftime('%Y%m%d%H%M%S')
        factors = CP_FACTORS.replace(
            '001-Rtd=7.198', f'001-Rtd={ph}')
        frame = fixture_module.make_hj212(
            station=self.station, password=self.password,
            qn=f'2026102712{sequence:02d}00001', data_time=data_time,
            factors=factors, command='2011')
        with closing(sqlite3.connect(self.fixture.database)) as db:
            previous_raw_id = db.execute(
                'SELECT COALESCE(MAX(id),0) FROM ingest_raw_frames '
                'WHERE endpoint_id=?', (self.endpoint,)).fetchone()[0]
        started = time.perf_counter()
        self.writer.write(frame)
        await self.writer.drain()
        deadline = started + 5
        while time.perf_counter() < deadline:
            with closing(sqlite3.connect(self.fixture.database)) as db:
                row = db.execute(
                    'SELECT id,persistence_state FROM ingest_raw_frames '
                    'WHERE endpoint_id=? AND id>? ORDER BY id DESC LIMIT 1',
                    (self.endpoint, previous_raw_id)).fetchone()
                published = (db.execute(
                    'SELECT COUNT(*) FROM observation_values value '
                    'JOIN observation_batches batch '
                    'ON batch.id=value.observation_batch_id '
                    'WHERE batch.raw_frame_id=? AND value.is_published=1',
                    (row[0],)).fetchone()[0] if row else 0)
            if row and row[1] == 'persisted' and published:
                return observed.replace(microsecond=0).isoformat(), time.perf_counter() - started
            await asyncio.sleep(0.01)
        self.fail('receiver did not publish the frame within the isolated timeout')

    async def test_next_list_request_observes_publication_and_configuration_changes(self):
        warm = self.list_sites()
        self.assertIsNone(warm['last_valid_observation_at'])
        self.assertEqual(warm['monitoring_status'], 'awaiting_first_frame')

        observed_one, publication_one = await self.send_frame(1)
        list_one = self.list_sites()
        latest_one = self.latest()
        overview_one = self.overview()
        self.assertEqual(list_one['last_valid_observation_at'], observed_one)
        self.assertEqual(len(latest_one), 9)
        self.assertEqual(len(overview_one['monitoring']['latest_values']), 9)
        self.assertLess(publication_one, 5)

        observed_two, publication_two = await self.send_frame(2, ph='7.298')
        list_two = self.list_sites()
        latest_two = self.latest()
        self.assertEqual(list_two['last_valid_observation_at'], observed_two)
        self.assertEqual(
            next(item for item in latest_two if item['business_metric'] == 'ph')
            ['standard_value_display'], '7.298')
        self.assertLess(publication_two, 5)

        version = 1
        for metrics, expected in (
                ([m for m in METRICS if m != 'water_temp'], 8),
                (METRICS, 9)):
            saved = self.fixture.client.put(
                f'/api/station-monitoring/sites/{self.fixture.site_id}/expected-factors',
                headers=self.headers,
                json={'metrics': metrics, 'version': version,
                      'idempotency_key': f'v24-selection-{version}'})
            self.assertEqual(saved.status_code, 200, saved.get_json())
            version += 1
            listed = self.list_sites()
            overview = self.overview()
            self.assertEqual(listed['latest_cycle']['expected_count'], expected)
            self.assertEqual(len(overview['factors']), expected)
            self.assertEqual(len(overview['monitoring']['latest_values']), expected)

    async def test_cache_keeps_permission_scope_and_short_failure_degradation(self):
        admin_list = self.fixture.client.get(
            '/api/station-monitoring/sites?scope=all', headers=self.headers)
        self.assertEqual(admin_list.status_code, 200, admin_list.get_json())
        self.assertEqual(len(admin_list.get_json()['items']), 2)

        operator_headers = self.fixture.headers('expected-operator')
        mine = self.fixture.client.get(
            '/api/station-monitoring/sites?scope=mine', headers=operator_headers)
        self.assertEqual(mine.status_code, 200, mine.get_json())
        self.assertEqual([item['site_id'] for item in mine.get_json()['items']],
                         [self.fixture.site_id])

        with mock.patch.object(
                monitoring_api, '_station_monitoring_list_version',
                side_effect=sqlite3.OperationalError('isolated version probe failure')):
            degraded = self.fixture.client.get(
                '/api/station-monitoring/sites?scope=mine', headers=operator_headers)
        self.assertEqual(degraded.status_code, 200, degraded.get_json())
        self.assertEqual(degraded.get_json()['result_snapshot']['state'], 'stale')
        self.assertFalse(degraded.get_json()['result_snapshot']['refreshing'])
        self.assertTrue(degraded.get_json()['result_snapshot']['last_refresh_error_at'])
        self.assertEqual([item['site_id'] for item in degraded.get_json()['items']],
                         [self.fixture.site_id])


if __name__ == '__main__':
    unittest.main()
