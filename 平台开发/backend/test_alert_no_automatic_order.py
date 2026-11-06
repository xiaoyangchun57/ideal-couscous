import unittest
from unittest import mock

import test_postdeploy_web_repair_r1 as fixtures


class AlertNoAutomaticOrderTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DisabledAlertRuleConsumerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app = fixtures.app_module
        with self.fixture.temporary_db() as db:
            db.executescript("""
                ALTER TABLE alerts ADD COLUMN value REAL;
                ALTER TABLE alerts ADD COLUMN flow_type TEXT DEFAULT 'manual';
                ALTER TABLE alerts ADD COLUMN flow_status TEXT DEFAULT 'pending_review';
                ALTER TABLE alerts ADD COLUMN tracking_count INTEGER DEFAULT 0;
                INSERT INTO alert_rule_config VALUES ('device','device_status',1,'{}','manual');
                INSERT INTO work_orders (order_no,site_id,status) VALUES ('existing',1,'pending');
            """)

    def test_creation_dedup_and_escalation_never_create_orders(self):
        policy = {1: {'service_status': 'active', 'reporting_condition': 'normal'}}
        with mock.patch.object(self.app, '_station_service_policy_rows_batch', return_value=policy), \
             mock.patch.object(self.app, '_wx_push_alert'):
            with self.fixture.temporary_db() as db:
                first = self.app.create_alert_internal(db, 1, 'device_status', 1, 'red', 'device')
                self.app.create_alert_internal(db, 1, 'device_status', 1, 'red', 'device')
                self.app.create_alert_internal(db, 1, 'device_status', 1, 'orange', 'device')
                row = db.execute('SELECT flow_type,flow_status FROM alerts WHERE id=?', (first,)).fetchone()
                self.assertEqual(tuple(row), ('manual', 'pending_review'))
        self.app.alert_escalation_check()
        with self.fixture.temporary_db() as db:
            self.assertEqual([row['order_no'] for row in db.execute('SELECT order_no FROM work_orders')], ['existing'])
            self.assertEqual(db.execute('SELECT level FROM alerts WHERE id=1').fetchone()[0], 'orange')


if __name__ == '__main__':
    unittest.main()
