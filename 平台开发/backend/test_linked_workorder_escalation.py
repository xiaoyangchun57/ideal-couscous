import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class LinkedWorkorderEscalationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE work_orders (
            order_no TEXT PRIMARY KEY, site_id INTEGER, title TEXT, level TEXT,
            status TEXT, source TEXT, event_type TEXT, related_alert_id INTEGER,
            created_at TEXT
        )''')
        self.db.executemany('INSERT INTO work_orders VALUES (?,?,?,?,?,?,?,?,?)', [
            ('WO-AUTO', 1, '[自动] 设备离线', 'normal', 'pending', 'auto', 'device_status', 9, '2026-07-27 08:00:00'),
            ('AL-ESC', 1, '【告警升级】设备状态：设备离线', 'urgent', 'in_progress', 'escalation', 'device_status', 9, '2026-07-27 09:00:00'),
            ('WO-OTHER', 1, '[自动] 数据延迟', 'normal', 'pending', 'auto', 'data_gap', 10, '2026-07-27 08:30:00'),
            # Legacy original: no alert id, so event type is the compatibility key.
            ('WO-LEGACY', 2, '[自动] 通讯中断', 'normal', 'pending', 'auto', 'communication', None, '2026-07-27 08:00:00'),
            ('AL-LEGACY', 2, '【告警升级】通讯中断', 'urgent', 'pending', 'escalation', 'communication', None, '2026-07-27 09:00:00'),
            ('WO-DEVICE', 3, '[自动] 聂城 设备离线（多参数水质分析仪）', 'normal', 'pending', 'auto', '告警自动转工单', 17585, '2026-07-27 08:00:00'),
            ('AL-DEVICE', 3, '【告警升级】设备状态：设备离线：聂城 多参数分析仪通信中断', 'urgent', 'in_progress', 'escalation', 'device_status', 16103, '2026-07-27 09:00:00'),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_escalation_replaces_original_auto_order_in_site_context(self):
        orders = app_module._effective_site_linked_workorders(self.db, 1)
        self.assertEqual([item['order_no'] for item in orders], ['AL-ESC', 'WO-OTHER'])

    def test_legacy_event_type_fallback_replaces_original_auto_order(self):
        orders = app_module._effective_site_linked_workorders(self.db, 2)
        self.assertEqual([item['order_no'] for item in orders], ['AL-LEGACY'])

    def test_legacy_title_similarity_replaces_unlinked_device_offline_order(self):
        orders = app_module._effective_site_linked_workorders(self.db, 3)
        self.assertEqual([item['order_no'] for item in orders], ['AL-DEVICE'])


if __name__ == '__main__':
    unittest.main()
