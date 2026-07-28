import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class PlanChangePartReservationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE spare_parts_inventory (id INTEGER PRIMARY KEY, quantity INTEGER);
            CREATE TABLE plan_resource_reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, part_id INTEGER,
                planned_quantity INTEGER, reserved_quantity INTEGER, issued_quantity INTEGER DEFAULT 0,
                status TEXT, updated_at TEXT
            );
            INSERT INTO spare_parts_inventory VALUES (8, 8);
            INSERT INTO plan_resource_reservations
                (schedule_id, part_id, planned_quantity, reserved_quantity, issued_quantity, status)
                VALUES (5, 8, 5, 5, 2, 'issued');
        ''')
        self.db.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_change_keeps_issued_fact_and_replaces_planned_demand_without_reservation(self):
        # Mirrors approved-change handling: keep the two parts already issued, then
        # replace the plan demand without occupying current inventory.
        self.db.execute("""UPDATE plan_resource_reservations
            SET reserved_quantity=0, planned_quantity=issued_quantity,
                status=CASE WHEN issued_quantity > 0 THEN 'issued' ELSE 'released' END
            WHERE schedule_id=? AND status IN ('planned','reserved','issued')""", (5,))
        schedule = {'id': 5, 'spare_parts': json.dumps([{'part_id': 8, 'quantity': 5}])}
        self.assertEqual(app_module._ps_reserve_parts(self.db, schedule), 1)
        rows = self.db.execute("""SELECT planned_quantity, reserved_quantity, issued_quantity, status
            FROM plan_resource_reservations WHERE schedule_id=5 ORDER BY id""").fetchall()
        self.assertEqual(
            [(r['planned_quantity'], r['reserved_quantity'], r['issued_quantity'], r['status']) for r in rows],
            [(5, 0, 2, 'issued')],
        )


if __name__ == '__main__':
    unittest.main()
