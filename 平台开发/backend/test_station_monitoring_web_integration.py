"""Optional browser-to-Flask check against a disposable formal-observation database."""
import os
from pathlib import Path
import subprocess
import threading
import unittest
from datetime import datetime

from werkzeug.serving import make_server

import app as web_app
import test_station_monitoring_normalization as normalization


class StationMonitoringWebIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('STATION_WEB_TEST_URL') and
                         os.environ.get('STATION_WEB_PLAYWRIGHT_MODULE'),
                         'Run only against an explicitly supplied local Vite and Edge runtime')
    def test_formal_observation_reaches_real_web_without_cross_site_leakage(self):
        fixture = normalization.StationMonitoringNormalizationTest()
        fixture.setUp()
        server = None
        try:
            raw_id = fixture._persist(
                bytes.fromhex('0311') + normalization.bcd_number(8.8, 3, 1), serial=63,
                sent_at=datetime(2020, 6, 12, 4, 0),
                observed_at=datetime(2020, 6, 12, 4, 0),
                received_at='2020-06-11T20:05:00+00:00',
            )
            self.assertEqual(normalization.normalize_raw_frame(fixture.database, raw_id), 'accepted')
            token = fixture._headers('monitor-admin')['Authorization'].split(' ', 1)[1]
            server = make_server('127.0.0.1', 0, web_app.app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            environment = os.environ.copy()
            environment.update(
                STATION_WEB_BACKEND_URL=f'http://127.0.0.1:{server.server_port}',
                STATION_WEB_BACKEND_TOKEN=token,
                STATION_WEB_INTEGRATED_SITE_ID=str(fixture.site_id),
            )
            test_path = (Path(__file__).resolve().parent.parent / 'react-vite' / 'src' /
                         'pages' / 'sites' / 'stationMonitoring.integrated.browser.test.js')
            result = subprocess.run(['node', '--test', str(test_path)],
                                    env=environment, timeout=90, check=False)
            self.assertEqual(result.returncode, 0, 'browser-to-Flask contract must pass')
        finally:
            if server:
                server.shutdown()
            fixture.tearDown()
