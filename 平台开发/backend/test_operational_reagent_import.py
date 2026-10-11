import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from import_operational_workbook import sync_devices_and_reagents


class OperationalReagentImportTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE device_shadows (
                id INTEGER PRIMARY KEY, site_id INTEGER, device_code TEXT, device_name TEXT,
                device_type TEXT, device_model TEXT, manufacturer TEXT, install_date TEXT,
                status TEXT, management_scope TEXT, monitoring_enabled INTEGER
            );
            CREATE TABLE reagents (
                id INTEGER PRIMARY KEY, name TEXT, manufacturer TEXT, spec TEXT,
                unit TEXT, shelf_life_days INTEGER
            );
            CREATE TABLE reagent_inventory (
                id INTEGER PRIMARY KEY, site_id INTEGER, reagent_id INTEGER, current_qty REAL,
                last_replaced_at TEXT, expected_duration_days INTEGER, qc_status TEXT
            );
        ''')
        self.db.executemany('INSERT INTO reagents VALUES (?,?,?,?,?,?)', [
            (1, '旧默认试剂', '', '', '套', 365),
            (2, '明确套装试剂', '', '', '套', 365),
            (3, '显式单位试剂', '', '', '套', 365),
            (4, '既有瓶装试剂', '', '', '瓶', 365),
        ])
        self.db.executemany('INSERT INTO reagent_inventory VALUES (?,?,?,?,?,?,?)', [
            (1, 1, 1, 1, None, None, 'passed'),
            (2, 99, 2, 1, None, None, 'passed'),
            (3, 1, 3, 1, None, None, 'passed'),
            (4, 1, 4, 1, None, None, 'passed'),
        ])

    def tearDown(self):
        self.db.close()

    def test_missing_units_default_to_bottle_and_only_proven_legacy_defaults_are_corrected(self):
        payload = {
            'sites': [{'name': '导入站', 'sequence': 1}],
            'equipment_blocks': [{
                'site': '导入站',
                'devices': [
                    {'name': '设备1', 'condition': '正常', 'reagent_name': '旧默认试剂'},
                    {'name': '设备2', 'condition': '正常', 'reagent_name': '明确套装试剂'},
                    {'name': '设备3', 'condition': '正常', 'reagent_name': '显式单位试剂', 'unit': 'mL'},
                    {'name': '设备4', 'condition': '正常', 'reagent_name': '既有瓶装试剂'},
                    {'name': '设备5', 'condition': '正常', 'reagent_name': '新增缺单位试剂'},
                ],
            }],
        }
        devices, inventories, corrected = sync_devices_and_reagents(self.db, payload, {'导入站': 1})
        self.assertEqual((devices, inventories, corrected), (5, 5, 1))
        units = {row['name']: row['unit'] for row in self.db.execute(
            'SELECT name,unit FROM reagents ORDER BY id').fetchall()}
        self.assertEqual(units['旧默认试剂'], '瓶')
        self.assertEqual(units['明确套装试剂'], '套', 'outside imported inventory prevents guessing user intent')
        self.assertEqual(units['显式单位试剂'], 'mL')
        self.assertEqual(units['既有瓶装试剂'], '瓶')
        self.assertEqual(units['新增缺单位试剂'], '瓶')
        imported_states = self.db.execute(
            'SELECT DISTINCT qc_status FROM reagent_inventory WHERE site_id=1').fetchall()
        self.assertEqual([row['qc_status'] for row in imported_states], ['not_required'])


if __name__ == '__main__':
    unittest.main()
