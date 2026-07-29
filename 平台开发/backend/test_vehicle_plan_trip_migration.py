import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class VehiclePlanTripMigrationTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE vehicle_applications (
                id INTEGER PRIMARY KEY, vehicle_id INTEGER, applicant_id INTEGER,
                start_at TEXT, end_at TEXT, status TEXT, reason TEXT, reject_reason TEXT
            );
            CREATE TABLE vehicle_use_records (id INTEGER PRIMARY KEY, application_id INTEGER);
        ''')
        self.db.executemany('INSERT INTO vehicle_applications VALUES (?,?,?,?,?,?,?,?)', [
            (1, 7, 3, '2026-07-28 08:00:00', '2026-07-28 18:00:00', 'approved', '巡检计划#9用车', None),
            (2, 7, 3, '2026-07-29 08:00:00', '2026-07-29 18:00:00', 'approved', '巡检计划#9用车', None),
            (3, 7, 3, '2026-07-30 08:00:00', '2026-07-30 18:00:00', 'approved', '巡检计划#9用车', None),
        ])
        self.db.execute('INSERT INTO vehicle_use_records VALUES (1, 2)')

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_merges_daily_reservations_and_preserves_used_audit_record(self):
        self.assertEqual(app_module._merge_legacy_plan_vehicle_trips(self.db), 1)
        rows = self.db.execute('SELECT * FROM vehicle_applications ORDER BY id').fetchall()
        self.assertEqual((rows[0]['start_at'], rows[0]['end_at'], rows[0]['status']),
                         ('2026-07-28 08:00:00', '2026-07-30 18:00:00', 'approved'))
        self.assertIn('连续行程', rows[0]['reason'])
        self.assertEqual(rows[1]['status'], 'archived')
        self.assertEqual(rows[2]['status'], 'cancelled')
        self.assertEqual(self.db.execute('SELECT application_id FROM vehicle_use_records').fetchone()['application_id'], 2)


if __name__ == '__main__':
    unittest.main()
