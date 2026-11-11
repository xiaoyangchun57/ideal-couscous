import sqlite3
import unittest
from unittest import mock

import app as app_module


class ActiveHolderConflictTest(unittest.TestCase):
    def test_expired_reservation_still_blocks_other_operator(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript('''
            CREATE TABLE vehicles (id INTEGER PRIMARY KEY, plate_no TEXT, status TEXT,
                insurance_expiry TEXT, annual_inspection_expiry TEXT);
            CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT);
            CREATE TABLE vehicle_applications (id INTEGER PRIMARY KEY, vehicle_id INTEGER,
                applicant_id INTEGER, status TEXT, start_at TEXT, end_at TEXT);
            CREATE TABLE vehicle_use_records (id INTEGER PRIMARY KEY, application_id INTEGER,
                returned_at TEXT, status TEXT);
            CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, user_id INTEGER, vehicle_days TEXT,
                period_start TEXT, period_end TEXT, status TEXT);
            INSERT INTO vehicles VALUES (1, 'A', 'in_use', NULL, NULL);
            INSERT INTO users VALUES (2, 'holder'), (3, 'other');
            INSERT INTO vehicle_applications VALUES (1, 1, 2, 'approved',
                '2026-11-01 08:00:00', '2026-11-01 18:00:00');
            INSERT INTO vehicle_use_records VALUES (1, 1, NULL, 'checked_out');
        ''')
        with mock.patch.object(app_module, '_vehicle_can_schedule', return_value=(True, '')):
            own = app_module._ps_check_vehicle_conflicts(db, 2, {'2026-11-10': 1})
            other = app_module._ps_check_vehicle_conflicts(db, 3, {'2026-11-10': 1})
            self.assertEqual(own, [])
            self.assertEqual(len(other), 1)
            self.assertIn('仍由他人持有', other[0]['reason'])
            db.execute("UPDATE vehicle_use_records SET returned_at='2026-11-10 09:00:00', status='returned'")
            self.assertEqual(app_module._ps_check_vehicle_conflicts(db, 3, {'2026-11-10': 1}), [])
