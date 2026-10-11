import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class ReagentOverviewAuthorityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        temp.close()
        self.db_path = temp.name
        self.original_get_db = app_module.get_db
        self.original_tokens = dict(app_module._tokens)
        self.original_cache = dict(app_module._site_ids_cache)

        @contextmanager
        def temporary_db():
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

        self.temporary_db = temporary_db
        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'operator-token': {'id': 2, 'role': 'operator', 'roles': ['operator'], 'real_name': '甲运维'},
            'admin-token': {'id': 2, 'role': 'admin', 'roles': ['admin', 'operator'], 'real_name': '甲运维'},
            'reviewer-token': {'id': 4, 'role': 'reviewer', 'roles': ['reviewer'], 'real_name': '甲审核'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, real_name TEXT, phone TEXT, role TEXT, status TEXT
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE reagents (
                    id INTEGER PRIMARY KEY, name TEXT, unit TEXT, shelf_life_days INTEGER
                );
                CREATE TABLE reagent_inventory (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_id INTEGER,
                    current_qty REAL, low_stock_threshold REAL, warning_days INTEGER,
                    last_replaced_at TEXT, expected_duration_days INTEGER,
                    qc_status TEXT, updated_at TEXT
                );
                CREATE TABLE reagent_records (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_name TEXT, reagent_type TEXT,
                    usage_date TEXT, replacement_date TEXT, operator TEXT, operator_id INTEGER,
                    notes TEXT, old_batch_no TEXT, new_batch_no TEXT, old_qty REAL, new_qty REAL
                );
                CREATE TABLE reagent_qc_records (
                    id INTEGER PRIMARY KEY, site_id INTEGER, reagent_id INTEGER,
                    standard_value REAL, measured_value REAL, deviation REAL, passed INTEGER,
                    fail_action TEXT, operator TEXT, operator_id INTEGER, qc_time TEXT, remark TEXT,
                    created_at TEXT
                );
            ''')
            db.executemany('INSERT INTO users VALUES (?,?,?,?,?)', [
                (2, '甲运维', '13800000000', 'operator', 'active'),
                (4, '甲审核', '13900000000', 'reviewer', 'active'),
            ])
            db.executemany('INSERT INTO user_roles VALUES (?,?)', [(2, 'operator'), (4, 'reviewer')])
            db.executemany('INSERT INTO user_sites VALUES (?,?)', [(2, 1), (4, 1)])
            db.executemany('INSERT INTO sites VALUES (?,?)', [(1, '负责站'), (2, '其他站')])
            db.executemany('INSERT INTO reagents VALUES (?,?,?,?)', [
                (index, f'试剂{index}', 'mL', 365) for index in range(1, 8)
            ])
            today = datetime.now()
            expired = (today - timedelta(days=20)).strftime('%Y-%m-%d 00:00:00')
            expiring = (today - timedelta(days=5)).strftime('%Y-%m-%d 00:00:00')
            normal = today.strftime('%Y-%m-%d 00:00:00')
            db.executemany('''
                INSERT INTO reagent_inventory
                    (id,site_id,reagent_id,current_qty,low_stock_threshold,warning_days,
                     last_replaced_at,expected_duration_days,qc_status,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            ''', [
                (1, 1, 1, 0, 1, 7, expired, 10, 'pending', normal),
                (2, 1, 2, 8, 1, 7, expiring, 10, 'passed', normal),
                (3, 1, 3, 1, 1, 7, None, None, 'passed', normal),
                (4, 1, 4, 8, 1, 7, normal, 30, 'pending', normal),
                (5, 1, 5, 8, 1, 7, normal, 30, 'failed', normal),
                (6, 1, 6, 0, -1, 7, normal, 30, 'passed', normal),
                (7, 2, 7, 8, 1, 7, normal, 30, 'passed', normal),
            ])
            db.executemany('''INSERT INTO reagent_qc_records
                (site_id,reagent_id,standard_value,measured_value,deviation,passed,
                 fail_action,operator,operator_id,qc_time,remark,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', [
                (1, 2, 1, 1, 0, 1, '', '甲运维', 2, normal, '', normal),
                (1, 5, 1, 2, 1, 0, 'repair', '甲运维', 2, normal, '', normal),
            ])
        app_module.app.config.update(TESTING=True)
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

    def test_scope_reasons_counts_zero_quantity_and_capabilities(self):
        response = self.client.get('/api/reagent-overview', headers=self.headers('operator-token'))
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['scope'], 'mine')
        self.assertEqual(body['scope_counts'], {'mine': 1, 'all': None})
        self.assertEqual(body['total'], 6)
        self.assertEqual(body['concern_count'], 5)
        self.assertEqual(body['status_counts'], {
            'expired': 1, 'expiring': 1, 'low_volume': 2, 'pending_qc': 2, 'failed_qc': 1,
        })
        composite = next(item for item in body['items'] if item['reagent_id'] == 1)
        self.assertEqual(composite['attention_reasons'], ['expired', 'low_volume', 'pending_qc'])
        self.assertEqual(next(item for item in body['items'] if item['reagent_id'] == 2)['attention_reasons'], ['expiring'])
        self.assertEqual(next(item for item in body['items'] if item['reagent_id'] == 3)['attention_reasons'], ['low_volume'])
        self.assertEqual(next(item for item in body['items'] if item['reagent_id'] == 4)['attention_reasons'], ['pending_qc'])
        self.assertEqual(next(item for item in body['items'] if item['reagent_id'] == 5)['attention_reasons'], ['failed_qc'])
        zero = next(item for item in body['items'] if item['reagent_id'] == 6)
        self.assertEqual(zero['current_qty'], 0)
        self.assertEqual(zero['attention_reasons'], [])
        self.assertEqual(zero['qc_status'], 'not_required')
        self.assertTrue(zero['can_replace'])
        self.assertFalse(zero['can_calibrate'])

        forbidden = self.client.get('/api/reagent-overview?scope=all', headers=self.headers('operator-token'))
        self.assertEqual(forbidden.status_code, 403)
        denied_write = self.client.post('/api/reagent-inventory/replacement', headers=self.headers('operator-token'), json={
            'site_id': 2, 'reagent_id': 7, 'old_qty': 8, 'new_qty': 9,
        })
        self.assertEqual(denied_write.status_code, 403)

        admin_mine = self.client.get('/api/reagent-overview', headers=self.headers('admin-token'))
        self.assertEqual(admin_mine.status_code, 200)
        self.assertEqual(admin_mine.get_json()['scope'], 'mine')
        self.assertEqual(admin_mine.get_json()['total'], 6)
        self.assertEqual({item['site_id'] for item in admin_mine.get_json()['items']}, {1})

        all_response = self.client.get('/api/reagent-overview?scope=all', headers=self.headers('admin-token'))
        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(all_response.get_json()['total'], 7)
        self.assertEqual(all_response.get_json()['scope_counts'], {'mine': 1, 'all': 2})
        site_one = [item for item in all_response.get_json()['items'] if item['site_id'] == 1]
        site_two = [item for item in all_response.get_json()['items'] if item['site_id'] == 2]
        self.assertTrue(all(item['can_replace'] for item in site_one))
        self.assertTrue(all(not item['can_replace'] and not item['can_calibrate'] for item in site_two))

    def test_qc_migration_uses_real_records_and_preserves_pending(self):
        result = app_module.migrate_reagent_qc()
        self.assertEqual(result['projected_from_qc'], 2)
        self.assertEqual(result['corrected_to_not_required'], 3)
        with self.temporary_db() as db:
            states = {row['reagent_id']: row['qc_status'] for row in db.execute(
                'SELECT reagent_id,qc_status FROM reagent_inventory ORDER BY reagent_id').fetchall()}
        self.assertEqual(states, {
            1: 'pending', 2: 'passed', 3: 'not_required', 4: 'pending',
            5: 'failed', 6: 'not_required', 7: 'not_required',
        })

    def test_replacement_idempotency_replays_and_conflicts_without_duplicate_writes(self):
        payload = {
            'site_id': 1, 'reagent_id': 1, 'old_qty': 0, 'new_qty': 9,
            'expected_duration_days': 20, 'replaced_at': '2026-09-30 08:00:00',
            '_idempotency_key': 'replace-stable-key',
        }
        first = self.client.post('/api/reagent-inventory/replacement',
                                 headers=self.headers('operator-token'), json=payload)
        second = self.client.post('/api/reagent-inventory/replacement',
                                  headers=self.headers('operator-token'), json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json(), first.get_json())
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_records').fetchone()[0], 1)
            inventory = db.execute('SELECT current_qty, expected_duration_days, qc_status '
                                   'FROM reagent_inventory WHERE site_id=1 AND reagent_id=1').fetchone()
            self.assertEqual((inventory['current_qty'], inventory['expected_duration_days'], inventory['qc_status']),
                             (9, 20, 'pending'))

        changed_payload = dict(payload, new_qty=10)
        conflict = self.client.post('/api/reagent-inventory/replacement',
                                    headers=self.headers('operator-token'), json=changed_payload)
        changed_target = dict(payload, site_id=2, reagent_id=7)
        with self.temporary_db() as db:
            db.execute('INSERT INTO user_sites VALUES (?,?)', (2, 2))
        target_conflict = self.client.post('/api/reagent-inventory/replacement',
                                           headers=self.headers('admin-token'), json=changed_target)
        with self.temporary_db() as db:
            db.execute('DELETE FROM user_sites WHERE user_id=2 AND site_id=2')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(target_conflict.status_code, 409)
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_records').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT current_qty FROM reagent_inventory WHERE id=1').fetchone()[0], 9)
            self.assertEqual(db.execute('SELECT current_qty FROM reagent_inventory WHERE id=7').fetchone()[0], 8)

        no_key = {key: value for key, value in payload.items() if key != '_idempotency_key'}
        self.assertEqual(self.client.post('/api/reagent-inventory/replacement',
                                          headers=self.headers('operator-token'), json=no_key).status_code, 200)
        no_key['new_qty'] = 11
        self.assertEqual(self.client.post('/api/reagent-inventory/replacement',
                                          headers=self.headers('operator-token'), json=no_key).status_code, 200)
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_records').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT current_qty FROM reagent_inventory WHERE id=1').fetchone()[0], 11)

        retry_after_denial = {
            'site_id': 2, 'reagent_id': 7, 'old_qty': 8, 'new_qty': 12,
            '_idempotency_key': 'replace-after-denial',
        }
        self.assertEqual(self.client.post('/api/reagent-inventory/replacement',
                                          headers=self.headers('operator-token'),
                                          json=retry_after_denial).status_code, 403)
        with self.temporary_db() as db:
            db.execute('INSERT INTO user_sites VALUES (?,?)', (2, 2))
        self.assertEqual(self.client.post('/api/reagent-inventory/replacement',
                                          headers=self.headers('admin-token'),
                                          json=retry_after_denial).status_code, 200)
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_records').fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT current_qty FROM reagent_inventory WHERE id=7').fetchone()[0], 12)

    def test_qc_idempotency_replays_and_conflicts_without_duplicate_writes(self):
        payload = {
            'site_id': 1, 'reagent_id': 1, 'standard_value': 1, 'measured_value': 1,
            'passed': True, 'qc_time': '2026-09-30 09:00:00',
            '_idempotency_key': 'qc-stable-key',
        }
        first = self.client.post('/api/reagent-qc', headers=self.headers('operator-token'), json=payload)
        second = self.client.post('/api/reagent-qc', headers=self.headers('operator-token'), json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json(), first.get_json())
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_qc_records WHERE reagent_id=1').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT qc_status FROM reagent_inventory WHERE id=1').fetchone()[0], 'passed')

        conflict = self.client.post('/api/reagent-qc', headers=self.headers('operator-token'),
                                    json=dict(payload, measured_value=2))
        with self.temporary_db() as db:
            db.execute('INSERT INTO user_sites VALUES (?,?)', (2, 2))
        target_conflict = self.client.post('/api/reagent-qc', headers=self.headers('admin-token'),
                                           json=dict(payload, site_id=2, reagent_id=7))
        with self.temporary_db() as db:
            db.execute('DELETE FROM user_sites WHERE user_id=2 AND site_id=2')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(target_conflict.status_code, 409)
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_qc_records WHERE reagent_id=1').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT qc_status FROM reagent_inventory WHERE id=1').fetchone()[0], 'passed')
            self.assertEqual(db.execute('SELECT qc_status FROM reagent_inventory WHERE id=7').fetchone()[0], 'passed')

        no_key = {
            'site_id': 1, 'reagent_id': 1, 'standard_value': 1, 'measured_value': 2,
            'passed': False, 'fail_action': 'calibrate', 'qc_time': '2026-09-30 12:00:00',
        }
        prepare_pending = {
            'site_id': 1, 'reagent_id': 1, 'old_qty': 0, 'new_qty': 8,
            'replaced_at': '2026-09-30 11:00:00', '_idempotency_key': 'qc-prepare-pending',
        }
        self.assertEqual(self.client.post('/api/reagent-inventory/replacement',
                                          headers=self.headers('operator-token'),
                                          json=prepare_pending).status_code, 200)
        self.assertEqual(self.client.post('/api/reagent-qc', headers=self.headers('operator-token'), json=no_key).status_code, 200)
        no_key['measured_value'] = 2.1
        self.assertEqual(self.client.post('/api/reagent-qc', headers=self.headers('operator-token'), json=no_key).status_code, 200)
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_qc_records WHERE reagent_id=1').fetchone()[0], 3)

        replacement = {
            'site_id': 2, 'reagent_id': 7, 'old_qty': 8, 'new_qty': 8,
            '_idempotency_key': 'qc-prepare-site-two',
        }
        retry_after_denial = {
            'site_id': 2, 'reagent_id': 7, 'standard_value': 1, 'measured_value': 1,
            'passed': True, '_idempotency_key': 'qc-after-denial',
        }
        self.assertEqual(self.client.post('/api/reagent-qc', headers=self.headers('operator-token'),
                                          json=retry_after_denial).status_code, 403)
        with self.temporary_db() as db:
            db.execute('INSERT INTO user_sites VALUES (?,?)', (2, 2))
        self.assertEqual(self.client.post('/api/reagent-inventory/replacement', headers=self.headers('admin-token'),
                                          json=replacement).status_code, 200)
        self.assertEqual(self.client.post('/api/reagent-qc', headers=self.headers('admin-token'),
                                          json=retry_after_denial).status_code, 200)
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_qc_records WHERE reagent_id=7').fetchone()[0], 1)

    def test_reviewer_is_read_only_and_denied_writes_have_zero_side_effects(self):
        overview = self.client.get('/api/reagent-overview', headers=self.headers('reviewer-token'))
        self.assertEqual(overview.status_code, 200)
        self.assertTrue(overview.get_json()['items'])
        self.assertTrue(all(not item['can_replace'] and not item['can_calibrate']
                            for item in overview.get_json()['items']))
        with self.temporary_db() as db:
            before_records = db.execute('SELECT COUNT(*) FROM reagent_records').fetchone()[0]
            before_qc = db.execute('SELECT COUNT(*) FROM reagent_qc_records').fetchone()[0]
            before_inventory = dict(db.execute('SELECT * FROM reagent_inventory WHERE id=1').fetchone())

        replacement = self.client.post('/api/reagent-inventory/replacement', headers=self.headers('reviewer-token'), json={
            'site_id': 1, 'reagent_id': 1, 'old_qty': 0, 'new_qty': 9,
        })
        qc = self.client.post('/api/reagent-qc', headers=self.headers('reviewer-token'), json={
            'site_id': 1, 'reagent_id': 1, 'passed': True,
        })
        self.assertEqual(replacement.status_code, 403)
        self.assertEqual(qc.status_code, 403)
        with self.temporary_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_records').fetchone()[0], before_records)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reagent_qc_records').fetchone()[0], before_qc)
            self.assertEqual(dict(db.execute('SELECT * FROM reagent_inventory WHERE id=1').fetchone()), before_inventory)

    def test_empty_mine_scope_returns_complete_authoritative_shape(self):
        app_module._tokens['empty-token'] = {
            'id': 9, 'role': 'operator', 'roles': ['operator'], 'real_name': '无站点运维'
        }
        response = self.client.get('/api/reagent-overview', headers=self.headers('empty-token'))
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['items'], [])
        self.assertEqual(body['concern_count'], 0)
        self.assertEqual(body['status_counts'], {
            'expired': 0, 'expiring': 0, 'low_volume': 0, 'pending_qc': 0, 'failed_qc': 0,
        })


if __name__ == '__main__':
    unittest.main()
