import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class LegacyWeeklyPlanRetirementTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT);
            CREATE TABLE weekly_inspection_plans (
                id INTEGER PRIMARY KEY, user_id INTEGER, week_start TEXT, plan_data TEXT,
                vehicle_id INTEGER, status TEXT, approver_id INTEGER, submitted_at TEXT,
                approved_at TEXT, remarks TEXT
            );
            CREATE TABLE plan_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,schedule_type TEXT,
                period_start TEXT,period_end TEXT,plan_data TEXT,vehicle_days TEXT,
                status TEXT,approver_id INTEGER,submitted_at TEXT,approved_at TEXT,
                remarks TEXT,tasks_generated INTEGER
            );
            CREATE TABLE app_migrations (name TEXT PRIMARY KEY, applied_at TEXT);
            CREATE TABLE timeline_events (
                id INTEGER PRIMARY KEY,source_type TEXT,source_id INTEGER,event_type TEXT,
                operator TEXT,remark TEXT
            );
            INSERT INTO users VALUES (7,'肖永平');
        ''')

    def tearDown(self):
        self.db.close()

    def insert_source(self, row_id=1, payload=None, week='2026-07-20'):
        self.db.execute('''INSERT INTO weekly_inspection_plans
            VALUES (?,?,?,?,NULL,'approved',NULL,NULL,NULL,'')''',
            (row_id, 7, week, json.dumps(payload or {'1': [274]}, ensure_ascii=False)))

    def test_confirmed_seed_is_removed_once_and_never_resurrects(self):
        self.insert_source()
        self.db.commit()
        self.db.execute('BEGIN')
        self.assertEqual(app_module._migrate_legacy_weekly_plans_once(self.db), 1)
        self.assertTrue(app_module._cleanup_confirmed_xiao_seed_plan(self.db))
        self.db.commit()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM weekly_inspection_plans').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM timeline_events WHERE source_type='plan_seed_cleanup'").fetchone()[0], 1)
        self.assertEqual(app_module._migrate_legacy_weekly_plans_once(self.db), 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 0)

    def test_similar_source_is_preserved_and_migration_marker_prevents_reimport(self):
        self.insert_source(payload={'1': [275]})
        self.assertEqual(app_module._migrate_legacy_weekly_plans_once(self.db), 1)
        self.assertFalse(app_module._cleanup_confirmed_xiao_seed_plan(self.db))
        self.db.commit()
        self.db.execute('DELETE FROM plan_schedules')
        self.db.commit()
        self.assertEqual(app_module._migrate_legacy_weekly_plans_once(self.db), 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM weekly_inspection_plans').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 0)

    def test_business_relation_rolls_back_exact_cleanup(self):
        self.insert_source()
        app_module._migrate_legacy_weekly_plans_once(self.db)
        schedule_id = self.db.execute('SELECT id FROM plan_schedules').fetchone()[0]
        self.db.execute('CREATE TABLE insp_plans (id INTEGER PRIMARY KEY,plan_schedule_id INTEGER)')
        self.db.execute('INSERT INTO insp_plans VALUES (1,?)', (schedule_id,))
        self.db.commit()
        self.db.execute('BEGIN')
        with self.assertRaisesRegex(RuntimeError, 'business facts'):
            app_module._cleanup_confirmed_xiao_seed_plan(self.db)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM weekly_inspection_plans').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM plan_schedules').fetchone()[0], 1)
        self.assertFalse(self.db.execute(
            "SELECT 1 FROM app_migrations WHERE name='remove_xiao_yongping_20260720_weekly_seed_v1'").fetchone())


if __name__ == '__main__':
    unittest.main()
