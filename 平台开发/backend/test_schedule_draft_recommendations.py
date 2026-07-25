import sqlite3
import unittest
from datetime import date

from schedule_draft_recommendations import build_draft_recommendations, create_recommended_drafts


class ScheduleDraftRecommendationTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, status TEXT);
            CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
            CREATE TABLE inspection_schedules (
                id INTEGER PRIMARY KEY, site_id INTEGER, frequency TEXT,
                next_due_date TEXT, status TEXT
            );
            CREATE TABLE plan_schedules (
                id INTEGER PRIMARY KEY, user_id INTEGER, schedule_type TEXT,
                period_start TEXT, period_end TEXT, plan_data TEXT, vehicle_days TEXT,
                spare_parts TEXT, work_order_ids TEXT, status TEXT, remarks TEXT,
                tasks_generated INTEGER
            );
            CREATE TABLE insp_plans (id INTEGER PRIMARY KEY);
        """)
        self.db.execute("INSERT INTO users VALUES (1, 'operator', 'active')")
        self.db.execute("INSERT INTO user_sites VALUES (1, 11)")

    def tearDown(self):
        self.db.close()

    def test_due_weekly_item_creates_one_draft_candidate_without_execution_plan(self):
        self.db.execute(
            "INSERT INTO inspection_schedules VALUES (1, 11, 'weekly', '2026-07-20', 'active')"
        )
        result = build_draft_recommendations(self.db, date(2026, 7, 25))

        self.assertEqual(len(result["recommendations"]), 1)
        candidate = result["recommendations"][0]
        self.assertEqual(candidate["schedule_type"], "weekly")
        self.assertEqual(candidate["site_ids"], [11])
        self.assertEqual(candidate["plan_data"], {"2026-07-25": {"sites": [11], "notes": ""}})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM insp_plans").fetchone()[0], 0)

    def test_existing_draft_suppresses_duplicate_candidate(self):
        self.db.execute(
            "INSERT INTO inspection_schedules VALUES (1, 11, 'weekly', '2026-07-25', 'active')"
        )
        self.db.execute(
            """INSERT INTO plan_schedules
                (id, user_id, schedule_type, period_start, period_end, status)
                VALUES (1, 1, 'weekly', '2026-07-20', '2026-07-26', 'draft')"""
        )
        result = build_draft_recommendations(self.db, date(2026, 7, 25))

        self.assertEqual(result["recommendations"], [])

    def test_future_and_unsupported_schedules_do_not_become_candidate(self):
        self.db.executemany(
            "INSERT INTO inspection_schedules VALUES (?, 11, ?, ?, 'active')",
            [(1, 'weekly', '2026-07-30'), (2, 'daily', '2026-07-25')],
        )
        result = build_draft_recommendations(self.db, date(2026, 7, 25), remind_days=1)

        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["unsupported_due_items"], 1)

    def test_scheduler_creates_one_draft_and_never_execution_plan(self):
        self.db.execute(
            "INSERT INTO inspection_schedules VALUES (1, 11, 'weekly', '2026-07-25', 'active')"
        )

        first = create_recommended_drafts(self.db, date(2026, 7, 25))
        second = create_recommended_drafts(self.db, date(2026, 7, 25))

        self.assertEqual(first["created_count"], 1)
        self.assertEqual(second["created_count"], 0)
        row = self.db.execute(
            "SELECT status, tasks_generated, vehicle_days, spare_parts FROM plan_schedules"
        ).fetchone()
        self.assertEqual(row["status"], "draft")
        self.assertEqual(row["tasks_generated"], 0)
        self.assertEqual(row["vehicle_days"], "{}")
        self.assertEqual(row["spare_parts"], "[]")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM insp_plans").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
