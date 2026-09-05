import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class WorkorderFlowProjectionTest(unittest.TestCase):
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

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._site_ids_cache.clear()
        app_module._tokens.update({
            'operator-token': {'id': 2, 'role': 'operator', 'real_name': '现场人员'},
            'other-token': {'id': 3, 'role': 'operator', 'real_name': '无权人员'},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, role TEXT, real_name TEXT, status TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE work_orders (
                    id INTEGER PRIMARY KEY, order_no TEXT, site_id INTEGER, source TEXT, event_type TEXT,
                    level TEXT, title TEXT, description TEXT, images TEXT, assignee TEXT, status TEXT,
                    created_at TEXT, check_in_time TEXT, check_in_user TEXT, resolved_at TEXT,
                    related_alert_id INTEGER, used_parts TEXT
                );
                CREATE TABLE alerts (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE timeline_events (
                    id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER, event_type TEXT,
                    operator TEXT, remark TEXT, created_at TEXT
                );
                INSERT INTO users VALUES (2, 'operator', '现场人员', 'active');
                INSERT INTO users VALUES (3, 'operator', '无权人员', 'active');
                INSERT INTO user_roles VALUES (2, 'operator');
                INSERT INTO user_roles VALUES (3, 'operator');
                INSERT INTO user_sites VALUES (2, 7);
                INSERT INTO sites VALUES (7, '测试站');
                INSERT INTO work_orders VALUES
                    (10, 'WO-20260902-101', 7, 'manual', '', 'normal', '水泵异常', '现场上报', '[]',
                     '现场人员', 'closed', '2026-09-02 08:00:00', '2026-09-02 09:00:00', '现场人员',
                     '2026-09-02 12:00:00', NULL, '');
                INSERT INTO timeline_events VALUES
                    (1, 'order', 0, 'accepted', '系统', '工单WO-20260902-101 → 已受理', '2026-09-02 08:01:00');
                INSERT INTO timeline_events VALUES
                    (2, 'order', 0, 'approved', '审核人', '工单WO-20260902-101 核验通过', '2026-09-02 11:59:00');
                INSERT INTO timeline_events VALUES
                    (3, 'order', 0, 'accepted', '系统', '工单WO-20260902-1010 → 已受理', '2026-09-02 08:02:00');
                INSERT INTO timeline_events VALUES
                    (4, 'workorder', 10, 'in_progress', '现场人员', '开始现场处置', '2026-09-02 10:00:00');
                INSERT INTO timeline_events VALUES
                    (5, 'workorder', 10, 'submit_review', '现场人员', '提交核验', '2026-09-02 11:00:00');
            ''')
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.original_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.original_tokens)
        app_module._site_ids_cache.clear()
        app_module._site_ids_cache.update(self.original_cache)
        os.unlink(self.db_path)

    def test_flow_events_are_exact_read_only_and_site_scoped(self):
        with app_module.get_db() as db:
            before = db.execute('SELECT COUNT(*) FROM timeline_events').fetchone()[0]
        response = self.client.get('/api/workorders', headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json), 1)
        events = response.json[0]['flow_events']
        self.assertEqual([event['type'] for event in events],
                         ['created', 'accepted', 'check_in', 'in_progress', 'submit_review', 'approved'])
        self.assertEqual([event['tone'] for event in events], ['completed'] * len(events))
        self.assertEqual(events[1]['operator'], '系统')
        self.assertNotIn('1010', ' '.join(event['remark'] for event in events))
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM timeline_events').fetchone()[0], before)

        denied = self.client.get('/api/workorders', headers={'Authorization': 'Bearer other-token'})
        self.assertEqual(denied.status_code, 200, denied.json)
        self.assertEqual(denied.json, [])

    def test_flow_event_tones_are_server_authoritative_for_active_and_terminal_orders(self):
        with app_module.get_db() as db:
            order = dict(db.execute('SELECT * FROM work_orders WHERE id=10').fetchone())
            pending = dict(order, id=11, order_no='WO-PENDING', status='pending',
                           check_in_time=None, resolved_at=None)
            pending_events = app_module._workorder_flow_events(db, pending)
            self.assertEqual([(event['type'], event['tone']) for event in pending_events],
                             [('created', 'current')])

            db.execute("DELETE FROM timeline_events WHERE id IN (2,5)")
            in_progress = app_module._workorder_flow_events(db, dict(order, status='in_progress'))
            self.assertEqual((in_progress[-1]['type'], in_progress[-1]['tone']),
                             ('in_progress', 'current'))
            self.assertEqual([event['tone'] for event in in_progress[:-1]],
                             ['completed'] * (len(in_progress) - 1))

            db.execute("""INSERT INTO timeline_events VALUES
                (5,'workorder',10,'submit_review','现场人员','提交核验','2026-09-02 11:00:00')""")
            reviewing = app_module._workorder_flow_events(db, dict(order, status='reviewing'))
            self.assertEqual((reviewing[-1]['type'], reviewing[-1]['tone']),
                             ('submit_review', 'current'))
            self.assertEqual([event['tone'] for event in reviewing[:-1]],
                             ['completed'] * (len(reviewing) - 1))

            for status in ('resolved', 'closed'):
                with self.subTest(status=status):
                    events = app_module._workorder_flow_events(db, dict(order, status=status))
                    self.assertEqual([event['tone'] for event in events], ['completed'] * len(events))

    def test_flow_events_use_stable_business_fallback_when_times_are_missing_or_equal(self):
        with app_module.get_db() as db:
            db.executemany("""INSERT INTO timeline_events
                (id,source_type,source_id,event_type,operator,remark,created_at)
                VALUES (?,?,?,?,?,?,?)""", [
                (6, 'workorder', 10, 'submit_review', '现场人员', '无时间提交', None),
                (7, 'workorder', 10, 'in_progress', '现场人员', '无时间开始', None),
                (8, 'workorder', 10, 'dispatched', '调度员', '同时间派发', '2026-09-02 08:30:00'),
                (9, 'workorder', 10, 'accepted', '现场人员', '同时间接单', '2026-09-02 08:30:00'),
            ])
            order = dict(db.execute('SELECT * FROM work_orders WHERE id=10').fetchone())
            order['status'] = 'reviewing'
            first = app_module._workorder_flow_events(db, order)
            second = app_module._workorder_flow_events(db, order)
        self.assertEqual([event['type'] for event in first], [event['type'] for event in second])
        same_time = [event['type'] for event in first if event['time'] == '2026-09-02 08:30:00']
        self.assertEqual(same_time, ['accepted', 'dispatched'])
        self.assertEqual([event['type'] for event in first[-2:]], ['in_progress', 'submit_review'])
        self.assertEqual(first[-1]['tone'], 'current')

    def test_exact_detail_returns_server_action_version_and_scope_errors(self):
        with app_module.get_db() as db:
            db.execute("UPDATE work_orders SET status='pending', check_in_time=NULL WHERE id=10")
        detail = self.client.get('/api/workorders/WO-20260902-101',
                                 headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['actions']['primary'], 'accept')
        self.assertFalse(detail.json['actions']['can_apply_resources'])
        self.assertTrue(detail.json['version'])
        self.assertTrue(detail.json['flow_events'])

        missing = self.client.get('/api/workorders/WO-MISSING',
                                  headers={'Authorization': 'Bearer operator-token'})
        self.assertEqual((missing.status_code, missing.json['code']), (404, 'WORKORDER_NOT_FOUND'))
        forbidden = self.client.get('/api/workorders/WO-20260902-101',
                                    headers={'Authorization': 'Bearer other-token'})
        self.assertEqual(forbidden.status_code, 403, forbidden.json)

    def test_rework_photo_projection_distinguishes_evidence_cycles(self):
        item = {'id': 100, 'photo_urls': '["/uploads/original.jpg"]', 'required_photos': 4, 'review_status': 3,
                'evidence_status': 'supplement_required', 'rework_required_at': '2026-09-02'}
        pending = [{'stored_path': '/uploads/original.jpg', 'review_status': 'pending',
                    'source_type': 'inspection', 'source_id': 100,
                    'extra_json': '{"material_role":"formal"}', 'is_effective_evidence': False}]
        original_pending = app_module._mobile_inspection_photo_state(item, pending)
        self.assertEqual((original_pending['replacement_photo_status'], original_pending['replacement_required_photos']),
                         ('pending_review', None))
        self.assertIn('原照片待审核', original_pending['replacement_block_reason'])

        rejected = app_module._mobile_inspection_photo_state(item, [{
            'stored_path': '/uploads/original.jpg', 'review_status': 'rejected',
            'source_type': 'inspection', 'source_id': 100,
            'extra_json': '{"material_role":"formal"}', 'is_effective_evidence': False,
        }])
        self.assertEqual((rejected['replacement_photo_status'], rejected['replacement_required_photos']), ('ready', 4))

        replacement_item = dict(item, review_status=1, evidence_status='replacement_submitted')
        replacement_pending = app_module._mobile_inspection_photo_state(replacement_item, pending)
        self.assertEqual((replacement_pending['replacement_photo_status'], replacement_pending['replacement_required_photos']),
                         ('replacement_pending_review', None))
        self.assertIn('补拍照片待审核', replacement_pending['replacement_block_reason'])

        replacement_approved = app_module._mobile_inspection_photo_state(replacement_item, [{
            'stored_path': '/uploads/retained.jpg', 'review_status': 'approved',
            'source_type': 'inspection', 'source_id': 100,
            'extra_json': '{"material_role":"formal"}', 'is_effective_evidence': True,
        }])
        self.assertEqual((replacement_approved['retained_photo_count'], replacement_approved['replacement_required_photos']), (1, 3))

        no_evidence = app_module._mobile_inspection_photo_state(item, [])
        self.assertEqual((no_evidence['replacement_photo_status'], no_evidence['replacement_required_photos']), ('ready', 4))
        unknown = app_module._mobile_inspection_photo_state(dict(item, evidence_status='unknown'), pending)
        self.assertEqual(unknown['replacement_photo_status'], 'ready')

        supplemental = app_module._mobile_inspection_photo_state(item, [{
            'stored_path': '/uploads/supplement.jpg', 'review_status': 'pending',
            'source_type': 'inspection', 'source_id': 100,
            'extra_json': '{"material_role":"supplement"}', 'is_effective_evidence': False,
        }])
        self.assertEqual((supplemental['replacement_photo_status'], supplemental['original_formal_pending_photo_urls']),
                         ('ready', []))

        supplemental_approved = app_module._mobile_inspection_photo_state(item, [{
            'stored_path': '/uploads/supplement-approved.jpg', 'review_status': 'approved',
            'source_type': 'inspection', 'source_id': 100,
            'extra_json': '{"material_role":"supplement"}', 'is_effective_evidence': True,
        }])
        self.assertEqual((supplemental_approved['retained_photo_count'],
                          supplemental_approved['replacement_required_photos']), (0, 4))

        wrong_item = app_module._mobile_inspection_photo_state(item, [{
            'stored_path': '/uploads/wrong.jpg', 'review_status': 'pending',
            'source_type': 'inspection', 'source_id': 101,
            'extra_json': '{"material_role":"formal"}', 'is_effective_evidence': False,
        }])
        self.assertEqual(wrong_item['replacement_photo_status'], 'ready')


if __name__ == '__main__':
    unittest.main()
