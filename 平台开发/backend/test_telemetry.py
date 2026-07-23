import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

from flask import Flask, g

sys.path.insert(0, os.path.dirname(__file__))
from telemetry import create_telemetry_blueprint


class TelemetryRouteTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_file.close()
        app = Flask(__name__)

        @contextmanager
        def get_db():
            db = sqlite3.connect(self.db_file.name)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

        @app.before_request
        def set_user():
            g.current_user = {'id': 7}

        app.register_blueprint(create_telemetry_blueprint(get_db, lambda fn: fn))
        self.client = app.test_client()

    def tearDown(self):
        os.unlink(self.db_file.name)

    def test_accepts_one_field_event_and_deduplicates_event_id(self):
        payload = {
            'event_id': 'evt-arrival-1',
            'event_name': 'inspection.checkin.queued',
            'occurred_at': '2026-07-23T10:00:00',
            'context': {'site_id': 22, 'offline': True},
        }
        first = self.client.post('/api/telemetry/events', json=payload)
        second = self.client.post('/api/telemetry/events', json=payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json['duplicate'])


if __name__ == '__main__':
    unittest.main()
