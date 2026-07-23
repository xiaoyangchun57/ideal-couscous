import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from operations_baseline import build_baseline


class OperationsBaselineTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE insp_plan_items (id INTEGER, plan_id INTEGER, result TEXT, execution_status TEXT, completed_at TEXT);
            CREATE TABLE work_orders (id INTEGER, status TEXT, created_at TEXT, closed_at TEXT);
            CREATE TABLE alerts (id INTEGER, status TEXT, created_at TEXT, resolved_at TEXT);
            CREATE TABLE data_reviews (id INTEGER, status TEXT, created_at TEXT, reviewed_at TEXT);
        ''')
        self.db.executemany('INSERT INTO insp_plan_items VALUES (?,?,?,?,?)', [
            (1, 1, 'normal', 'active', '2026-07-20 08:00:00'),
            (2, 1, None, 'active', None),
            (3, 1, 'normal', 'cancelled', '2026-07-20 08:00:00'),
        ])
        self.db.executemany('INSERT INTO work_orders VALUES (?,?,?,?)', [
            (1, 'closed', '2026-07-20 08:00:00', '2026-07-20 10:00:00'),
            (2, 'in_progress', '2026-07-20 08:00:00', None),
        ])

    def tearDown(self):
        self.db.close()

    def test_uses_active_planned_items_and_marks_missing_event_metrics_collecting(self):
        baseline = build_baseline(self.db, '2026-07-01 00:00:00', '2026-08-01 00:00:00')
        coverage = baseline['north_star']['inspection_coverage']
        self.assertEqual(coverage['numerator'], 1)
        self.assertEqual(coverage['denominator'], 2)
        self.assertEqual(coverage['value'], 50.0)
        self.assertIsNone(baseline['frontline']['offline_closure_success_rate']['value'])
        self.assertEqual(baseline['frontline']['offline_closure_success_rate']['state'], 'collecting')


if __name__ == '__main__':
    unittest.main()
