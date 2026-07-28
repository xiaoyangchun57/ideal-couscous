import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class StartedPlanChangeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name

        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, real_name TEXT);
            CREATE TABLE sites (id INTEGER PRIMARY KEY, type TEXT);
            CREATE TABLE inspection_configs (site_type TEXT, template_id INTEGER, is_active INTEGER);
            CREATE TABLE inspection_templates (id INTEGER PRIMARY KEY, status TEXT, frequency TEXT);
            CREATE TABLE inspection_template_items (
                id INTEGER PRIMARY KEY, template_id INTEGER, item_name TEXT, category TEXT,
                photo_required INTEGER, max_photos INTEGER, need_review INTEGER, sort_order INTEGER
            );
            CREATE TABLE insp_plans (
                id INTEGER PRIMARY KEY, plan_schedule_id INTEGER, generate_date TEXT,
                status TEXT, schedule_version INTEGER
            );
            CREATE TABLE insp_plan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, site_id INTEGER,
                template_id INTEGER, item_name TEXT, category TEXT, frequency TEXT,
                required_photos INTEGER DEFAULT 0, result TEXT, execution_status TEXT DEFAULT 'active'
            );
            CREATE TABLE plan_schedules (
                id INTEGER PRIMARY KEY, plan_data TEXT, schedule_type TEXT, user_id INTEGER,
                version INTEGER, tasks_generated INTEGER
            );
            INSERT INTO users VALUES (7, '现场运维');
            INSERT INTO sites VALUES (1, 'water_quality');
            INSERT INTO sites VALUES (2, 'water_quality');
            INSERT INTO inspection_configs VALUES ('water_quality', 3, 1);
            INSERT INTO inspection_templates VALUES (3, 'active', 'weekly');
            INSERT INTO inspection_template_items VALUES (1, 3, '仪表读数', '设备', 1, 2, 1, 1);
            INSERT INTO insp_plans VALUES (10, 5, '2026-07-28', 'active', 1);
            INSERT INTO insp_plan_items
                (plan_id, site_id, item_name, result, execution_status)
                VALUES (10, 1, '已完成检查', 'normal', 'active');
            INSERT INTO plan_schedules VALUES
                (5, '{"2026-07-28":{"sites":[1,2]}}', 'weekly', 7, 2, 1);
        ''')
        self.db.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_change_adds_new_site_to_started_execution_package(self):
        schedule = self.db.execute('SELECT * FROM plan_schedules WHERE id=5').fetchone()
        result = app_module._ps_rebuild_tasks_on_change(self.db, schedule)
        self.db.commit()

        existing = self.db.execute(
            "SELECT result FROM insp_plan_items WHERE plan_id=10 AND site_id=1"
        ).fetchone()
        added = self.db.execute(
            "SELECT item_name, required_photos FROM insp_plan_items WHERE plan_id=10 AND site_id=2"
        ).fetchone()
        self.assertEqual(existing['result'], 'normal')
        self.assertEqual((added['item_name'], added['required_photos']), ('仪表读数', 2))
        self.assertEqual(result['items_added_to_started_plans'], 1)


if __name__ == '__main__':
    unittest.main()
