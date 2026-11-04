import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as app_module


class AutoAlertConversionIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, 'conversion.db')
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE sites (
                    id INTEGER PRIMARY KEY, name TEXT, manager TEXT
                );
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, site_id INTEGER, metric TEXT,
                    level TEXT, message TEXT, status TEXT DEFAULT 'pending',
                    flow_type TEXT DEFAULT 'auto', flow_status TEXT DEFAULT 'pending',
                    related_order_no TEXT
                );
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE,
                    site_id INTEGER, source TEXT, event_type TEXT, metric TEXT,
                    level TEXT, title TEXT, description TEXT, assignee TEXT,
                    status TEXT, sla_deadline TEXT, related_alert_id INTEGER
                );
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT,
                    source_id INTEGER, event_type TEXT, operator TEXT, remark TEXT
                );
                INSERT INTO sites VALUES (1, '测试站', '');
            """)

    def tearDown(self):
        self.tmp.cleanup()

    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        return db

    @staticmethod
    def add_alert(db, alert_id, metric='turbidity'):
        db.execute(
            """INSERT INTO alerts
               (id,site_id,metric,level,message,status,flow_type,flow_status)
               VALUES (?,1,?,'red','超出阈值','pending','auto','pending')""",
            (alert_id, metric),
        )

    def convert(self, db, alert_id, metric='turbidity'):
        return app_module._auto_convert_alert(
            db, alert_id, 1, 'red', '超出阈值', metric
        )

    @mock.patch.object(app_module, '_station_operator', return_value='运维员')
    def test_repeated_conversion_reuses_the_same_workorder(self, _operator):
        with self.connection() as db:
            self.add_alert(db, 1)
            first = self.convert(db, 1)
            db.execute("UPDATE alerts SET flow_status='pending' WHERE id=1")
            second = self.convert(db, 1)
            db.commit()
            orders = db.execute(
                'SELECT order_no,related_alert_id FROM work_orders'
            ).fetchall()
            alert = db.execute(
                'SELECT related_order_no,flow_status FROM alerts WHERE id=1'
            ).fetchone()

        self.assertEqual(first, second)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]['related_alert_id'], 1)
        self.assertEqual(alert['related_order_no'], first)
        self.assertEqual(alert['flow_status'], 'converted')

    @mock.patch.object(app_module, '_station_operator', return_value='运维员')
    def test_reverse_link_is_repaired_without_creating_another_order(self, _operator):
        with self.connection() as db:
            self.add_alert(db, 2)
            db.execute(
                """INSERT INTO work_orders
                   (order_no,site_id,status,related_alert_id)
                   VALUES ('WO-EXISTING',1,'pending',2)"""
            )
            result = self.convert(db, 2)
            db.commit()
            count = db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0]
            alert = db.execute(
                'SELECT related_order_no,flow_status FROM alerts WHERE id=2'
            ).fetchone()

        self.assertEqual(result, 'WO-EXISTING')
        self.assertEqual(count, 1)
        self.assertEqual(tuple(alert), ('WO-EXISTING', 'converted'))

    @mock.patch.object(app_module, '_station_operator', return_value='运维员')
    def test_concurrent_conversion_creates_one_workorder(self, _operator):
        with self.connection() as db:
            self.add_alert(db, 3)
            db.commit()

        barrier = threading.Barrier(2)
        results = []
        errors = []

        def worker():
            try:
                db = self.connection()
                try:
                    barrier.wait(timeout=2)
                    results.append(self.convert(db, 3))
                    db.commit()
                finally:
                    db.close()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(errors, errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        with self.connection() as db:
            orders = db.execute(
                'SELECT order_no,related_alert_id FROM work_orders'
            ).fetchall()
            alert = db.execute(
                'SELECT related_order_no,flow_status FROM alerts WHERE id=3'
            ).fetchone()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]['related_alert_id'], 3)
        self.assertEqual(alert['related_order_no'], orders[0]['order_no'])
        self.assertEqual(alert['flow_status'], 'converted')
        self.assertEqual(results, [orders[0]['order_no'], orders[0]['order_no']])

    @mock.patch.object(app_module, '_station_operator', return_value='运维员')
    def test_failure_rolls_back_claim_order_and_timeline(self, _operator):
        with self.connection() as db:
            self.add_alert(db, 4)
            db.execute("""
                CREATE TRIGGER fail_auto_timeline
                BEFORE INSERT ON timeline_events
                BEGIN
                    SELECT RAISE(ABORT, 'injected timeline failure');
                END;
            """)
            db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                self.convert(db, 4)
            db.rollback()
            alert = db.execute(
                'SELECT related_order_no,flow_status FROM alerts WHERE id=4'
            ).fetchone()
            order_count = db.execute('SELECT COUNT(*) FROM work_orders').fetchone()[0]
            timeline_count = db.execute('SELECT COUNT(*) FROM timeline_events').fetchone()[0]

        self.assertEqual(tuple(alert), (None, 'pending'))
        self.assertEqual(order_count, 0)
        self.assertEqual(timeline_count, 0)

    @mock.patch.object(app_module, '_station_operator', return_value='运维员')
    def test_different_alerts_create_independent_workorders(self, _operator):
        with self.connection() as db:
            self.add_alert(db, 5)
            self.add_alert(db, 6)
            first = self.convert(db, 5)
            second = self.convert(db, 6)
            db.commit()
            links = db.execute(
                'SELECT related_alert_id,order_no FROM work_orders ORDER BY related_alert_id'
            ).fetchall()

        self.assertNotEqual(first, second)
        self.assertEqual([row['related_alert_id'] for row in links], [5, 6])


if __name__ == '__main__':
    unittest.main()
