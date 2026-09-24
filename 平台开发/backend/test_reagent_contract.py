import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager


sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class ReagentContractTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_cache = dict(app_module._site_ids_cache)

        @contextmanager
        def temporary_db():
            db = sqlite3.connect(self.db_path, timeout=3)
            db.row_factory = sqlite3.Row
            try:
                yield db
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        self.temporary_db = temporary_db
        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()

        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT, role TEXT, real_name TEXT,
                    status TEXT DEFAULT 'active'
                );
                CREATE TABLE user_roles (
                    user_id INTEGER, role TEXT, PRIMARY KEY(user_id, role)
                );
                CREATE TABLE user_sites (
                    user_id INTEGER, site_id INTEGER, PRIMARY KEY(user_id, site_id)
                );
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE reagents (
                    id INTEGER PRIMARY KEY, name TEXT, manufacturer TEXT, spec TEXT,
                    unit TEXT, shelf_life_days INTEGER
                );
                CREATE TABLE reagent_inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL, reagent_id INTEGER NOT NULL,
                    current_qty REAL NOT NULL DEFAULT 0,
                    low_stock_threshold REAL DEFAULT 0.2,
                    last_replaced_at TEXT, expected_duration_days INTEGER,
                    warning_days INTEGER DEFAULT 7, updated_at TEXT,
                    UNIQUE(site_id, reagent_id)
                );
                CREATE TABLE reagent_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER, reagent_id INTEGER, used_qty REAL,
                    expected_duration_days INTEGER, operator_id INTEGER,
                    used_at TEXT DEFAULT (datetime('now','localtime')), remark TEXT
                );
                CREATE TABLE reagent_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER, reagent_id INTEGER, alert_type TEXT,
                    current_qty REAL, threshold_qty REAL,
                    alert_at TEXT DEFAULT (datetime('now','localtime')),
                    handled INTEGER DEFAULT 0, handled_at TEXT
                );
                CREATE TABLE reagent_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER, reagent_name TEXT, reagent_type TEXT,
                    usage_date TEXT, replacement_date TEXT, operator TEXT,
                    operator_id INTEGER, notes TEXT, old_batch_no TEXT,
                    new_batch_no TEXT, old_qty REAL, new_qty REAL
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER, source_type TEXT, source_id INTEGER,
                    title TEXT, content TEXT
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?,?)', [
                (1, 'admin', 'admin', '管理员', 'active'),
                (2, 'operator', 'operator', '运维甲', 'active'),
                (3, 'reviewer', 'reviewer', '审核甲', 'active'),
                (4, 'zero', 'operator', '零站点运维', 'active'),
                (5, 'dual', 'reviewer', '双角色人员', 'active'),
                (6, 'secondary-admin', 'reviewer', '副角色管理员', 'active'),
            ])
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [
                (1, 'admin'), (2, 'operator'), (3, 'reviewer'), (4, 'operator'),
                (5, 'reviewer'), (5, 'operator'), (6, 'reviewer'), (6, 'admin'),
            ])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [
                (2, 1), (3, 1), (5, 1),
            ])
            db.executemany('INSERT INTO sites VALUES (?,?)', [(1, '一号站'), (2, '二号站')])
            db.executemany('INSERT INTO reagents VALUES (?,?,?,?,?,?)', [
                (10, '氨氮试剂', '', '', '瓶', 365),
                (11, 'COD试剂', '', '', '升', 365),
                (12, '总磷试剂', '', '', '盒', 365),
            ])
            db.commit()

        app_module.migrate_reagent_qc()
        with temporary_db() as db:
            db.executemany('''INSERT INTO reagent_inventory
                (site_id,reagent_id,current_qty,low_stock_threshold,last_replaced_at,
                 expected_duration_days,warning_days,qc_status,batch_no)
                VALUES (?,?,?,?,?,?,?,?,?)''', [
                (1, 10, 0, 1, '2000-01-01 00:00:00', 10, 7, 'failed', 'OLD-1'),
                (1, 11, 5, 1, None, None, 7, 'pending', 'OLD-2'),
                (2, 10, 4, 1, None, None, 7, 'pending', 'OLD-3'),
            ])
            db.execute("""INSERT INTO reagent_alerts
                (site_id,reagent_id,alert_type,current_qty,threshold_qty)
                VALUES (1,10,'low_stock',0,1)""")
            db.commit()

        self.users = {
            'admin-token': {'id': 1, 'username': 'admin', 'real_name': '管理员',
                            'role': 'admin', 'roles': ['admin'], 'must_change_password': False},
            'operator-token': {'id': 2, 'username': 'operator', 'real_name': '运维甲',
                               'role': 'operator', 'roles': ['operator'], 'must_change_password': False},
            'reviewer-token': {'id': 3, 'username': 'reviewer', 'real_name': '审核甲',
                               'role': 'reviewer', 'roles': ['reviewer'], 'must_change_password': False},
            'zero-token': {'id': 4, 'username': 'zero', 'real_name': '零站点运维',
                           'role': 'operator', 'roles': ['operator'], 'must_change_password': False},
            'dual-token': {'id': 5, 'username': 'dual', 'real_name': '双角色人员',
                           'role': 'operator', 'roles': ['operator', 'reviewer'],
                           'must_change_password': False},
        }
        app_module._tokens.update(self.users)
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def db_value(self, sql, params=()):
        with self.temporary_db() as db:
            row = db.execute(sql, params).fetchone()
            return row[0] if row else None

    def test_migration_is_additive_and_repeatable(self):
        app_module.migrate_reagent_qc()
        with self.temporary_db() as db:
            inventory_columns = {row['name'] for row in db.execute(
                'PRAGMA table_info(reagent_inventory)').fetchall()}
            tables = {row['name'] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn('qc_status', inventory_columns)
        self.assertIn('batch_no', inventory_columns)
        self.assertIn('reagent_idempotency', tables)
        self.assertIn('reagent_inventory_deletion_audits', tables)

    def test_overview_scopes_multi_reason_zero_unit_and_capabilities(self):
        admin = self.client.get('/api/reagent-overview', headers=self.headers('admin-token'))
        self.assertEqual(admin.status_code, 200, admin.json)
        self.assertEqual(admin.json['total'], 3)
        self.assertEqual(admin.json['concern_count'], 3)
        self.assertEqual(admin.json['status_counts'], {
            'expired': 1, 'expiring': 0, 'low_volume': 1,
            'pending_qc': 2, 'failed_qc': 1,
        })
        first = next(item for item in admin.json['items']
                     if item['site_id'] == 1 and item['reagent_id'] == 10)
        self.assertEqual(first['current_qty'], 0)
        self.assertEqual(first['unit'], '瓶')
        self.assertEqual(first['attention_reasons'], ['expired', 'low_volume', 'failed_qc'])
        self.assertTrue(first['can_replace'])
        self.assertTrue(first['can_calibrate'])

        reviewer = self.client.get('/api/reagent-overview', headers=self.headers('reviewer-token'))
        self.assertEqual(reviewer.json['total'], 2)
        self.assertTrue(all(item['site_id'] == 1 for item in reviewer.json['items']))
        self.assertTrue(all(not item['can_replace'] and not item['can_calibrate']
                            for item in reviewer.json['items']))
        zero = self.client.get('/api/reagent-overview', headers=self.headers('zero-token'))
        self.assertEqual(zero.json, {
            'total': 0, 'concern_count': 0, 'items': [],
            'status_counts': {'expired': 0, 'expiring': 0, 'low_volume': 0,
                              'pending_qc': 0, 'failed_qc': 0},
        })

    def test_pending_scope_and_write_permissions(self):
        admin = self.client.get('/api/reagent-qc/pending', headers=self.headers('admin-token'))
        operator = self.client.get('/api/reagent-qc/pending', headers=self.headers('operator-token'))
        zero = self.client.get('/api/reagent-qc/pending', headers=self.headers('zero-token'))
        self.assertEqual({row['site_id'] for row in admin.json}, {1, 2})
        self.assertEqual([row['site_id'] for row in operator.json], [1])
        self.assertEqual(zero.json, [])

        payload = {
            'site_id': 1, 'reagent_id': 10, 'new_qty': 2,
            'expected_duration_days': 20, 'replaced_at': '2026-09-22 08:00:00',
            '_idempotency_key': 'permission-check',
        }
        reviewer = self.client.post('/api/reagent-inventory/replacement',
                                    headers=self.headers('reviewer-token'), json=payload)
        self.assertEqual((reviewer.status_code, reviewer.json['code']),
                         (403, 'REAGENT_WRITE_FORBIDDEN'))
        cross_site = self.client.post('/api/reagent-inventory/replacement',
                                      headers=self.headers('operator-token'),
                                      json=dict(payload, site_id=2))
        self.assertEqual((cross_site.status_code, cross_site.json['code']),
                         (403, 'REAGENT_SITE_FORBIDDEN'))
        missing_site = self.client.post('/api/reagent-inventory/replacement',
                                        headers=self.headers('operator-token'),
                                        json=dict(payload, site_id=999))
        self.assertEqual((missing_site.status_code, missing_site.json['code']),
                         (404, 'REAGENT_SITE_NOT_FOUND'))

    def test_replacement_uses_server_stock_and_is_idempotent(self):
        payload = {
            'site_id': 1, 'reagent_id': 10, 'old_qty': 999,
            'new_qty': 7, 'new_batch_no': 'NEW-1',
            'expected_duration_days': 30, 'replaced_at': '2026-09-22 09:00:00',
            '_idempotency_key': 'replacement-1',
        }
        first = self.client.post('/api/reagent-inventory/replacement',
                                 headers=self.headers('operator-token'), json=payload)
        replay = self.client.post('/api/reagent-inventory/replacement',
                                  headers=self.headers('operator-token'), json=payload)
        conflict = self.client.post('/api/reagent-inventory/replacement',
                                    headers=self.headers('operator-token'),
                                    json=dict(payload, new_qty=8))
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(replay.json, first.json)
        self.assertEqual((conflict.status_code, conflict.json['code']),
                         (409, 'IDEMPOTENCY_KEY_REUSED'))
        with self.temporary_db() as db:
            record = db.execute('SELECT old_qty,new_qty,old_batch_no,new_batch_no FROM reagent_records').fetchone()
            inventory = db.execute("""SELECT current_qty,batch_no,qc_status
                FROM reagent_inventory WHERE site_id=1 AND reagent_id=10""").fetchone()
        self.assertEqual(tuple(record), (0, 7, 'OLD-1', 'NEW-1'))
        self.assertEqual(tuple(inventory), (7, 'NEW-1', 'pending'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_records'), 1)

    def test_qc_replay_does_not_duplicate_records_or_notifications(self):
        payload = {
            'site_id': 1, 'reagent_id': 11,
            'standard_value': 10, 'measured_value': 12, 'passed': False,
            'fail_action': 'repair', 'qc_time': '2026-09-22 10:00:00',
            '_idempotency_key': 'qc-failed-1',
        }
        first = self.client.post('/api/reagent-qc', headers=self.headers('operator-token'), json=payload)
        replay = self.client.post('/api/reagent-qc', headers=self.headers('operator-token'), json=payload)
        conflict = self.client.post('/api/reagent-qc', headers=self.headers('operator-token'),
                                    json=dict(payload, measured_value=13))
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(replay.json, first.json)
        self.assertEqual((conflict.status_code, conflict.json['code']),
                         (409, 'IDEMPOTENCY_KEY_REUSED'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_qc_records'), 1)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM notifications'), 2)
        self.assertEqual(self.db_value("""SELECT qc_status FROM reagent_inventory
            WHERE site_id=1 AND reagent_id=11"""), 'failed')

    def test_create_usage_and_delete_are_scoped_and_idempotent(self):
        create_payload = {
            'site_id': 1, 'reagent_id': 12, 'current_qty': 0,
            'expected_duration_days': 20, '_idempotency_key': 'create-1',
        }
        first_create = self.client.post('/api/reagent-inventory',
                                        headers=self.headers('dual-token'), json=create_payload)
        replay_create = self.client.post('/api/reagent-inventory',
                                         headers=self.headers('dual-token'), json=create_payload)
        self.assertEqual(first_create.status_code, 201, first_create.json)
        self.assertEqual(replay_create.json, first_create.json)
        self.assertEqual(self.db_value(
            'SELECT COUNT(*) FROM reagent_inventory WHERE site_id=1 AND reagent_id=12'), 1)

        usage_payload = {
            'site_id': 1, 'reagent_id': 11, 'used_qty': 2,
            '_idempotency_key': 'usage-1',
        }
        first_usage = self.client.post('/api/reagent-inventory/usage',
                                       headers=self.headers('operator-token'), json=usage_payload)
        replay_usage = self.client.post('/api/reagent-inventory/usage',
                                        headers=self.headers('operator-token'), json=usage_payload)
        self.assertEqual(first_usage.json['remaining_qty'], 3)
        self.assertEqual(replay_usage.json, first_usage.json)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_usage'), 1)

        delete_url = '/api/reagent-inventory/1/12'
        delete_payload = {'_idempotency_key': 'delete-1', 'reason': '误建库存记录'}
        first_delete = self.client.delete(delete_url, headers=self.headers('dual-token'),
                                          json=delete_payload)
        replay_delete = self.client.delete(delete_url, headers=self.headers('dual-token'),
                                           json=delete_payload)
        conflict_delete = self.client.delete(
            delete_url, headers=self.headers('dual-token'),
            json={'_idempotency_key': 'delete-1', 'reason': '另一个删除原因'})
        self.assertEqual(first_delete.status_code, 200, first_delete.json)
        self.assertEqual(replay_delete.json, first_delete.json)
        self.assertEqual((conflict_delete.status_code, conflict_delete.json['code']),
                         (409, 'IDEMPOTENCY_KEY_REUSED'))
        self.assertEqual(self.db_value(
            'SELECT COUNT(*) FROM reagent_inventory WHERE site_id=1 AND reagent_id=12'), 0)
        with self.temporary_db() as db:
            audit = db.execute('''SELECT * FROM reagent_inventory_deletion_audits
                WHERE site_id=1 AND reagent_id=12''').fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(audit['reason'], '误建库存记录')
        self.assertEqual(audit['operator_id'], 5)
        self.assertEqual(audit['reagent_name'], '总磷试剂')
        self.assertEqual(audit['unit'], '盒')
        self.assertEqual(audit['idempotency_key'], 'delete-1')
        self.assertEqual(json.loads(audit['delete_before_state'])['current_qty'], 0)
        self.assertEqual(self.db_value(
            'SELECT COUNT(*) FROM reagent_inventory_deletion_audits'), 1)

    def test_delete_requires_reason_and_preserves_scope(self):
        delete_url = '/api/reagent-inventory/1/11'
        missing_reason = self.client.delete(
            delete_url, headers=self.headers('operator-token'),
            json={'_idempotency_key': 'missing-reason'})
        too_long = self.client.delete(
            delete_url, headers=self.headers('operator-token'),
            json={'_idempotency_key': 'long-reason', 'reason': '删' * 201})
        reviewer = self.client.delete(
            delete_url, headers=self.headers('reviewer-token'),
            json={'_idempotency_key': 'reviewer-delete', 'reason': '无权删除'})
        cross_site = self.client.delete(
            '/api/reagent-inventory/2/10', headers=self.headers('operator-token'),
            json={'_idempotency_key': 'cross-site-delete', 'reason': '跨站删除'})
        missing_site = self.client.delete(
            '/api/reagent-inventory/999/10', headers=self.headers('operator-token'),
            json={'_idempotency_key': 'missing-site-delete', 'reason': '站点不存在'})
        missing_inventory = self.client.delete(
            '/api/reagent-inventory/1/12', headers=self.headers('operator-token'),
            json={'_idempotency_key': 'missing-inventory-delete', 'reason': '库存不存在'})

        self.assertEqual((missing_reason.status_code, missing_reason.json['code']),
                         (400, 'DELETE_REASON_REQUIRED'))
        self.assertEqual((too_long.status_code, too_long.json['code']),
                         (400, 'DELETE_REASON_TOO_LONG'))
        self.assertEqual((reviewer.status_code, reviewer.json['code']),
                         (403, 'REAGENT_WRITE_FORBIDDEN'))
        self.assertEqual((cross_site.status_code, cross_site.json['code']),
                         (403, 'REAGENT_SITE_FORBIDDEN'))
        self.assertEqual((missing_site.status_code, missing_site.json['code']),
                         (404, 'REAGENT_SITE_NOT_FOUND'))
        self.assertEqual((missing_inventory.status_code, missing_inventory.json['code']),
                         (404, 'REAGENT_INVENTORY_NOT_FOUND'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_inventory'), 3)
        self.assertEqual(self.db_value(
            'SELECT COUNT(*) FROM reagent_inventory_deletion_audits'), 0)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 0)

    def test_delete_failure_rolls_back_every_write(self):
        with self.temporary_db() as db:
            db.execute('''CREATE TRIGGER fail_reagent_inventory_delete
                BEFORE DELETE ON reagent_inventory
                BEGIN SELECT RAISE(ABORT, 'forced inventory delete failure'); END''')
            db.commit()
        response = self.client.delete(
            '/api/reagent-inventory/1/10', headers=self.headers('operator-token'),
            json={'_idempotency_key': 'rollback-delete', 'reason': '测试回滚'})
        self.assertEqual((response.status_code, response.json['code']),
                         (503, 'REAGENT_RETRYABLE'))
        self.assertEqual(self.db_value('''SELECT COUNT(*) FROM reagent_inventory
            WHERE site_id=1 AND reagent_id=10'''), 1)
        self.assertEqual(self.db_value('''SELECT COUNT(*) FROM reagent_alerts
            WHERE site_id=1 AND reagent_id=10 AND handled=0'''), 1)
        self.assertEqual(self.db_value(
            'SELECT COUNT(*) FROM reagent_inventory_deletion_audits'), 0)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 0)

    def test_replacement_failure_rolls_back_every_write(self):
        with self.temporary_db() as db:
            db.execute('''CREATE TRIGGER fail_reagent_idempotency
                BEFORE INSERT ON reagent_idempotency
                BEGIN SELECT RAISE(ABORT, 'forced idempotency failure'); END''')
            db.commit()
        payload = {
            'site_id': 1, 'reagent_id': 10, 'new_qty': 9,
            'expected_duration_days': 30, 'replaced_at': '2026-09-22 11:00:00',
            '_idempotency_key': 'rollback-replacement',
        }
        response = self.client.post('/api/reagent-inventory/replacement',
                                    headers=self.headers('operator-token'), json=payload)
        self.assertEqual((response.status_code, response.json['code']),
                         (503, 'REAGENT_RETRYABLE'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_records'), 0)
        self.assertEqual(self.db_value("""SELECT current_qty FROM reagent_inventory
            WHERE site_id=1 AND reagent_id=10"""), 0)
        self.assertEqual(self.db_value("""SELECT COUNT(*) FROM reagent_alerts
            WHERE site_id=1 AND reagent_id=10 AND handled=0"""), 1)
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 0)

    def test_qc_notification_failure_rolls_back_state(self):
        with self.temporary_db() as db:
            db.execute('''CREATE TRIGGER fail_reagent_notification
                BEFORE INSERT ON notifications
                BEGIN SELECT RAISE(ABORT, 'forced notification failure'); END''')
            db.commit()
        payload = {
            'site_id': 1, 'reagent_id': 11,
            'standard_value': 10, 'measured_value': 12, 'passed': False,
            'fail_action': 'repair', 'qc_time': '2026-09-22 12:00:00',
            '_idempotency_key': 'rollback-qc',
        }
        response = self.client.post('/api/reagent-qc',
                                    headers=self.headers('operator-token'), json=payload)
        self.assertEqual((response.status_code, response.json['code']),
                         (503, 'REAGENT_RETRYABLE'))
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_qc_records'), 0)
        self.assertEqual(self.db_value("""SELECT qc_status FROM reagent_inventory
            WHERE site_id=1 AND reagent_id=11"""), 'pending')
        self.assertEqual(self.db_value('SELECT COUNT(*) FROM reagent_idempotency'), 0)

    def test_strict_replacement_and_qc_validation(self):
        replacement = self.client.post('/api/reagent-inventory/replacement',
                                       headers=self.headers('operator-token'), json={
            'site_id': 1, 'reagent_id': 10, 'new_qty': 0,
            'expected_duration_days': 0, 'replaced_at': 'not-a-time',
        })
        self.assertEqual(replacement.status_code, 400)
        qc = self.client.post('/api/reagent-qc', headers=self.headers('operator-token'), json={
            'site_id': 1, 'reagent_id': 10, 'standard_value': 'bad',
            'measured_value': 2, 'passed': True,
        })
        self.assertEqual(qc.status_code, 400)


if __name__ == '__main__':
    unittest.main()
