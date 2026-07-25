import sqlite3
import unittest
from datetime import date

from schedule_draft_recommendations import (
    build_systemic_follow_up_recommendations,
    create_systemic_follow_up_draft,
)


class SystemicFollowUpRecommendationTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, status TEXT);
            CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
            CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE work_orders (
                id INTEGER PRIMARY KEY, site_id INTEGER, source TEXT, event_type TEXT, created_at TEXT
            );
            CREATE TABLE plan_schedules (
                id INTEGER PRIMARY KEY, user_id INTEGER, schedule_type TEXT, period_start TEXT,
                period_end TEXT, plan_data TEXT, vehicle_days TEXT, spare_parts TEXT,
                work_order_ids TEXT, status TEXT, remarks TEXT, tasks_generated INTEGER
            );
        ''')
        self.db.execute("INSERT INTO users VALUES (7, 'operator', 'active')")
        self.db.execute('INSERT INTO user_sites VALUES (7, 3)')
        self.db.execute("INSERT INTO sites VALUES (3, '三号站')")

    def tearDown(self):
        self.db.close()

    def test_two_same_inspection_anomalies_produce_one_follow_up_candidate(self):
        self.db.executemany('INSERT INTO work_orders VALUES (?,?,?,?,?)', [
            (1, 3, 'inspection', '设备异常', '2026-07-10 08:00:00'),
            (2, 3, 'inspection', '设备异常', '2026-07-24 09:00:00'),
        ])
        result = build_systemic_follow_up_recommendations(self.db, date(2026, 7, 25))

        self.assertEqual(len(result['recommendations']), 1)
        recommendation = result['recommendations'][0]
        self.assertEqual(recommendation['occurrence_count'], 2)
        self.assertEqual(recommendation['site_id'], 3)

    def test_created_follow_up_suppresses_future_duplicates_and_never_creates_execution(self):
        self.db.executemany('INSERT INTO work_orders VALUES (?,?,?,?,?)', [
            (1, 3, 'inspection', '设备异常', '2026-07-10 08:00:00'),
            (2, 3, 'inspection', '设备异常', '2026-07-24 09:00:00'),
        ])
        recommendation = build_systemic_follow_up_recommendations(self.db, date(2026, 7, 25))['recommendations'][0]
        draft_id = create_systemic_follow_up_draft(self.db, recommendation)

        self.assertIsNotNone(draft_id)
        self.assertEqual(build_systemic_follow_up_recommendations(self.db, date(2026, 7, 25))['recommendations'], [])
        row = self.db.execute('SELECT status, tasks_generated, remarks FROM plan_schedules').fetchone()
        self.assertEqual(row['status'], 'draft')
        self.assertEqual(row['tasks_generated'], 0)
        self.assertIn('系统性异常复查', row['remarks'])

    def test_different_anomaly_type_or_outside_window_does_not_trigger(self):
        self.db.executemany('INSERT INTO work_orders VALUES (?,?,?,?,?)', [
            (1, 3, 'inspection', '设备异常', '2026-06-24 08:00:00'),
            (2, 3, 'inspection', '环境异常', '2026-07-24 09:00:00'),
        ])
        result = build_systemic_follow_up_recommendations(self.db, date(2026, 7, 25))

        self.assertEqual(result['recommendations'], [])


if __name__ == '__main__':
    unittest.main()
