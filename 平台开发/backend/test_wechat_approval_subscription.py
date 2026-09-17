import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module


class WechatApprovalSubscriptionTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        self.db_path = handle.name
        self.old_get_db = app_module.get_db
        self.old_tokens = dict(app_module._tokens)

        @contextmanager
        def temporary_db():
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row
            try:
                yield db
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

        app_module.get_db = temporary_db
        app_module._tokens.clear()
        app_module._tokens.update({
            'operator': {'id': 2, 'role': 'operator', 'roles': ['operator']},
            'admin': {'id': 1, 'role': 'admin', 'roles': ['admin', 'operator']},
            'reviewer': {'id': 3, 'role': 'reviewer', 'roles': ['reviewer']},
        })
        with temporary_db() as db:
            db.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, real_name TEXT, role TEXT,
                    status TEXT, openid TEXT, auth_version INTEGER DEFAULT 1
                );
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                CREATE TABLE user_sites (user_id INTEGER, site_id INTEGER);
                CREATE TABLE plan_schedules (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE plan_schedule_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, version INTEGER,
                    event_type TEXT, operator_id INTEGER, payload TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source_type TEXT,
                    source_id TEXT, title TEXT, content TEXT, is_read INTEGER DEFAULT 0,
                    dedupe_key TEXT DEFAULT '', payload_json TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE vehicle_applications (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE parts_requests (id INTEGER PRIMARY KEY, status TEXT);
                CREATE TABLE work_orders (
                    order_no TEXT PRIMARY KEY, status TEXT, review_cycle INTEGER DEFAULT 0,
                    site_id INTEGER, remark TEXT DEFAULT ''
                );
                CREATE TABLE auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT, user_id INTEGER,
                    auth_version INTEGER, issued_at TEXT, expires_at TEXT,
                    revoked_at TEXT, revoke_reason TEXT, last_seen_at TEXT
                );
                CREATE TABLE operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT, action TEXT,
                    target_type TEXT, target_id INTEGER, operator TEXT, operator_id INTEGER,
                    details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO users VALUES
                    (1,'管理员','operator','active','openid-admin',1),
                    (2,'申请人','operator','active','openid-applicant',1),
                    (3,'审核员','reviewer','active','openid-reviewer',1),
                    (4,'停用管理员','admin','inactive','openid-inactive',1);
                INSERT INTO user_roles VALUES
                    (1,'admin'),(1,'operator'),(2,'operator'),(3,'reviewer'),(4,'admin');
                INSERT INTO user_sites VALUES (3,10);
            ''')
            db.executemany("""INSERT INTO auth_sessions
                (token_hash,user_id,auth_version,issued_at,expires_at,last_seen_at)
                VALUES (?,?,?,?,?,?)""", [
                (app_module._hash_token('admin'), 1, 1, '2026-09-07 00:00:00', '2099-01-01 00:00:00', '2026-09-07 00:00:00'),
                (app_module._hash_token('operator'), 2, 1, '2026-09-07 00:00:00', '2099-01-01 00:00:00', '2026-09-07 00:00:00'),
                (app_module._hash_token('reviewer'), 3, 1, '2026-09-07 00:00:00', '2099-01-01 00:00:00', '2026-09-07 00:00:00'),
            ])
            db.commit()
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.get_db = self.old_get_db
        app_module._tokens.clear()
        app_module._tokens.update(self.old_tokens)
        os.unlink(self.db_path)

    @staticmethod
    def headers(token):
        return {'Authorization': f'Bearer {token}'}

    def test_template_contract_is_role_scoped_and_old_template_is_absent(self):
        ordinary = self.client.get('/api/mobile/subscription-templates', headers=self.headers('operator'))
        reviewer = self.client.get('/api/mobile/subscription-templates', headers=self.headers('reviewer'))
        self.assertEqual(ordinary.status_code, 200, ordinary.json)
        self.assertEqual(reviewer.status_code, 200, reviewer.json)
        self.assertEqual([row['purpose'] for row in ordinary.json['templates']],
                         ['alert', 'approval_result'])
        self.assertEqual([row['purpose'] for row in reviewer.json['templates']],
                         ['alert', 'approval_result', 'approval_pending'])
        rendered = json.dumps(reviewer.json, ensure_ascii=False)
        self.assertNotIn('4MrY8lzIXYyujudoJGsG7gka5X_ySpxg5eVKVqC__mw', rendered)

    def test_pending_and_result_intents_use_exact_recipients_keys_and_dedupe(self):
        with app_module.get_db() as db:
            pending = app_module._wx_queue_pending_approval(
                db, 'plan_schedule', 12, 'submitted:v3', [1, 2, 3, 4], 2,
                '巡检计划审批', '非常长的审核项目名称用于验证微信字段安全截断不会越界',
                'PLAN-12', '2026-09-05 16:00:00', cycle_key='event:1',
                page='/pages/review/view?target_type=plan_schedule&target_id=12')
            repeated = app_module._wx_queue_pending_approval(
                db, 'plan_schedule', 12, 'submitted:v3', [1], 2,
                '巡检计划审批', '同一计划', 'PLAN-12', '2026-09-05 16:00:00',
                cycle_key='event:1', page='/pages/review/view?target_type=plan_schedule&target_id=12')
            workorder = app_module._wx_queue_pending_approval(
                db, 'workorder', 'WO-1', 'reviewing', [3], 2,
                '工单核验', '现场工单', 'WO-1', '2026-09-05 16:01:00', site_id=10,
                cycle_key='review:1', page='/pages/review/view?target_type=workorder_review&target_id=WO-1')
            result_id = app_module._wx_queue_approval_result(
                db, 'parts_request', 8, 'rejected', 2, '备件申请', '浊度仪耗材',
                '已退回', '2026-09-05 16:02:00', '退回原因过长需要在微信字段范围内安全截断且保留结果语义',
                cycle_key='request:8', page='/pages/message/message?notification_id=88')
            db.commit()
            rows = db.execute('SELECT * FROM wx_subscription_outbox ORDER BY id').fetchall()
        self.assertEqual(pending, repeated)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row['recipient_user_id'] for row in rows], [1, 3, 2])
        self.assertEqual(rows[0]['template_id'], app_module.WX_TMPL_APPROVAL_PENDING)
        self.assertEqual(rows[2]['template_id'], app_module.WX_TMPL_APPROVAL_RESULT)
        self.assertEqual(set(json.loads(rows[0]['payload_json'])),
                         {'thing9', 'thing8', 'thing4', 'character_string7', 'time6'})
        self.assertEqual(set(json.loads(rows[2]['payload_json'])),
                         {'thing18', 'thing1', 'thing5', 'time3', 'thing4'})
        self.assertLessEqual(len(json.loads(rows[0]['payload_json'])['thing8']['value']), 20)
        self.assertLessEqual(len(json.loads(rows[2]['payload_json'])['thing4']['value']), 20)
        self.assertTrue(all(row['page'].startswith('/pages/') for row in rows))
        self.assertIn('/pages/review/view?', rows[0]['page'])
        self.assertIn('/pages/review/view?', rows[1]['page'])
        self.assertNotIn('/pages/review/view', rows[2]['page'])
        self.assertTrue(result_id)

    def test_outbox_rolls_back_with_business_and_delivery_is_retry_safe(self):
        with app_module.get_db() as db:
            db.execute('BEGIN IMMEDIATE')
            app_module._wx_queue_approval_result(
                db, 'vehicle_application', 9, 'approved', 2, '车辆申请',
                '抢修用车', '已通过', '2026-09-05 16:03:00', '请按安排用车',
                cycle_key='application:9', page='/pages/vehicle/vehicle?application_id=9&source=approval_result')
            db.rollback()
        with app_module.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='wx_subscription_outbox'").fetchone()[0], 0)

        with app_module.get_db() as db:
            intent_id = app_module._wx_queue_approval_result(
                db, 'vehicle_application', 10, 'approved', 2, '车辆申请',
                '现场用车', '已通过', '2026-09-05 16:04:00', '请按安排用车',
                cycle_key='application:10', page='/pages/vehicle/vehicle?application_id=10&source=approval_result')
            db.commit()
        with mock.patch.object(app_module, '_wx_send_subscribe_result', return_value={
                'ok': True, 'permanent': True, 'status': 'sent', 'errcode': '0'}) as sender:
            app_module._wx_flush_outbox([intent_id])
            app_module._wx_flush_outbox([intent_id])
        self.assertEqual(sender.call_count, 1)
        with app_module.get_db() as db:
            row = db.execute('SELECT status,attempts FROM wx_subscription_outbox WHERE id=?', (intent_id,)).fetchone()
        self.assertEqual((row['status'], row['attempts']), ('sent', 1))

    def test_two_dispatchers_claim_one_intent_and_send_once(self):
        with app_module.get_db() as db:
            intent_id = app_module._wx_queue_approval_result(
                db, 'vehicle_application', 20, 'approved', 2, '车辆申请', '并发测试',
                '已通过', '2026-09-05 16:05:00', '请按安排用车',
                cycle_key='application:20', page='/pages/review/view?target_type=vehicle_application&target_id=20')
            db.commit()
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def send(*args):
            calls.append(args)
            entered.set()
            release.wait(2)
            return {'ok': True, 'permanent': True, 'status': 'sent', 'errcode': '0'}

        with mock.patch.object(app_module, '_wx_send_subscribe_result', side_effect=send):
            first = threading.Thread(target=app_module._wx_flush_outbox, args=([intent_id],))
            second = threading.Thread(target=app_module._wx_flush_outbox, args=([intent_id],))
            first.start()
            entered.wait(2)
            second.start()
            second.join(2)
            release.set()
            first.join(2)
        self.assertEqual(len(calls), 1)

    def test_closed_pending_business_intents_are_superseded_without_send(self):
        fixtures = [
            ('plan_schedule', '31', "INSERT INTO plan_schedules VALUES (31,'submitted')", "UPDATE plan_schedules SET status='approved' WHERE id=31", 'event:31', None),
            ('vehicle_application', '32', "INSERT INTO vehicle_applications VALUES (32,'pending')", "UPDATE vehicle_applications SET status='approved' WHERE id=32", 'application:32', None),
            ('parts_request', '33', "INSERT INTO parts_requests VALUES (33,'pending')", "UPDATE parts_requests SET status='rejected' WHERE id=33", 'request:33', None),
            ('workorder', 'WO-34', "INSERT INTO work_orders VALUES ('WO-34','reviewing',2,10,'')", "UPDATE work_orders SET status='closed' WHERE order_no='WO-34'", 'review:2', 10),
        ]
        ids = []
        with app_module.get_db() as db:
            for business_type, business_id, insert_sql, close_sql, cycle_key, site_id in fixtures:
                db.execute(insert_sql)
                queued = app_module._wx_queue_pending_approval(
                    db, business_type, business_id,
                    'submitted' if business_type == 'plan_schedule' else ('reviewing' if business_type == 'workorder' else 'pending'),
                    [1] if business_type != 'workorder' else [3], 2,
                    '待审核', business_id, business_id, '2026-09-05 16:06:00',
                    site_id=site_id, cycle_key=cycle_key,
                    page=f'/pages/review/view?target_type={business_type}&target_id={business_id}')
                ids.extend(queued)
                db.execute(close_sql)
            db.commit()
        with mock.patch.object(app_module, '_wx_send_subscribe_result') as sender:
            app_module._wx_flush_outbox(ids)
        self.assertEqual(sender.call_count, 0)
        with app_module.get_db() as db:
            statuses = [row['status'] for row in db.execute(
                'SELECT status FROM wx_subscription_outbox ORDER BY id').fetchall()]
        self.assertEqual(statuses, ['superseded'] * 4)

    def test_plan_endpoint_reject_and_same_version_resubmit_create_new_cycle_without_sync_send(self):
        with app_module.get_db() as db:
            db.execute('DROP TABLE plan_schedules')
            db.execute("""CREATE TABLE plan_schedules (
                id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,version INTEGER,
                plan_data TEXT,vehicle_days TEXT,schedule_type TEXT,period_start TEXT,period_end TEXT,
                vehicle_id INTEGER,no_vehicle_required INTEGER DEFAULT 0,
                vehicle_exception_reason TEXT,coverage_exception_reason TEXT,
                reject_reason TEXT,validation_snapshot TEXT,submitted_at TEXT,change_reason TEXT,
                approver_id INTEGER
            )""")
            db.execute("""INSERT INTO plan_schedules VALUES
                (50,2,'draft',1,'{"2026-09-06":{"sites":[10]}}','{}','weekly',
                 '2026-09-06','2026-09-12',NULL,1,'无需用车','','',NULL,NULL,NULL,NULL)""")
            db.commit()
        validation = {'ok': True, 'errors': [], 'warnings': []}
        patches = (
            mock.patch.object(app_module, '_ps_validate_execution_sites', side_effect=lambda db, uid, value: value),
            mock.patch.object(app_module, '_ps_validate_item_selections', side_effect=lambda db, value, kind: value),
            mock.patch.object(app_module, '_ps_persisted_vehicle_id', return_value=None),
            mock.patch.object(app_module, '_ps_expand_plan_vehicle', side_effect=lambda plan, vehicle, days: days),
            mock.patch.object(app_module, '_ps_validate', return_value=validation),
            mock.patch.object(app_module, '_ps_coverage_exception_required', return_value=None),
            mock.patch.object(app_module, '_wx_send_subscribe_result'),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as sender:
            submitted = self.client.post('/api/plan-schedules/50/submit',
                                         headers=self.headers('operator'), json={'version': 1})
            self.assertEqual(submitted.status_code, 200, submitted.json)
            self.assertEqual(sender.call_count, 0)
            rejected = self.client.post('/api/plan-schedules/50/reject',
                                        headers=self.headers('admin'), json={'reason': '调整日期'})
            self.assertEqual(rejected.status_code, 200, rejected.json)
            resubmitted = self.client.post('/api/plan-schedules/50/submit',
                                           headers=self.headers('operator'), json={'version': 1})
            self.assertEqual(resubmitted.status_code, 200, resubmitted.json)
            replay = self.client.post('/api/plan-schedules/50/submit',
                                      headers=self.headers('operator'), json={'version': 1})
            self.assertTrue(replay.json['already_submitted'])
            with app_module.get_db() as db:
                db.execute("""INSERT INTO plan_schedules VALUES
                    (51,2,'modifying',1,'{\"2026-09-06\":{\"sites\":[10]}}','{}','weekly',
                     '2026-09-06','2026-09-12',NULL,1,'无需用车','','',NULL,NULL,'路线调整',NULL)""")
                db.commit()
            change_submitted = self.client.post('/api/plan-schedules/51/submit',
                                                 headers=self.headers('operator'), json={'version': 1})
            self.assertEqual(change_submitted.status_code, 200, change_submitted.json)
            self.assertEqual(sender.call_count, 0)
        with app_module.get_db() as db:
            rows = db.execute("""SELECT purpose,business_id,cycle_key,page,recipient_user_id
                FROM wx_subscription_outbox WHERE business_type='plan_schedule'
                ORDER BY id""").fetchall()
            events = db.execute("""SELECT id,event_type FROM plan_schedule_events
                WHERE schedule_id=50 ORDER BY id""").fetchall()
        pending = [row for row in rows if row['purpose'] == 'approval_pending']
        results = [row for row in rows if row['purpose'] == 'approval_result']
        pending_50 = [row for row in pending if row['business_id'] == '50']
        self.assertEqual(len(pending), 3)
        self.assertEqual(len(pending_50), 2)
        self.assertEqual(len({row['cycle_key'] for row in pending}), 3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['page'], '/pages/plan-detail/plan-detail?id=50&notification_id=2')
        self.assertNotIn('/pages/review/view', results[0]['page'])
        self.assertEqual([row['event_type'] for row in events], ['submitted', 'submitted'])
        self.assertTrue(all('target_type=plan_schedule' in row['page'] for row in pending))
        with app_module.get_db() as db:
            change_pending = db.execute("""SELECT payload_json FROM wx_subscription_outbox
                WHERE business_type='plan_schedule' AND business_id='51' AND purpose='approval_pending'""").fetchone()
        self.assertEqual(json.loads(change_pending['payload_json'])['thing9']['value'], '巡检计划变更')
        self.assertTrue(all('cycle_key=event%3A' in row['page'] for row in pending))
        with app_module.get_db() as db:
            pending_ids = [row['id'] for row in db.execute("""SELECT id FROM wx_subscription_outbox
                WHERE business_type='plan_schedule' AND business_id='50' AND purpose='approval_pending' ORDER BY id""").fetchall()]
        with mock.patch.object(app_module, '_wx_send_subscribe_result', return_value={
                'ok': True, 'permanent': True, 'status': 'sent', 'errcode': '0'}) as sender:
            app_module._wx_flush_outbox(pending_ids)
        self.assertEqual(sender.call_count, 1)
        with app_module.get_db() as db:
            pending_statuses = [row['status'] for row in db.execute("""SELECT status
                FROM wx_subscription_outbox WHERE id IN (?,?) ORDER BY id""", pending_ids).fetchall()]
        self.assertEqual(pending_statuses, ['superseded', 'sent'])
        with app_module.get_db() as db:
            result_notice = db.execute("""SELECT user_id,title FROM notifications
                WHERE source_type='plan_schedule' AND title='巡检计划被退回'""").fetchone()
        self.assertEqual(result_notice['user_id'], 2)

    def test_audit_target_status_uses_exact_plan_and_workorder_cycles(self):
        with app_module.get_db() as db:
            app_module._ensure_wx_outbox_schema(db)
            db.execute("INSERT INTO plan_schedules VALUES (81,'approved')")
            db.execute("INSERT INTO plan_schedule_events VALUES (811,81,1,'change_submitted',2,'{}','2026-09-07 10:00:00')")
            db.execute("""INSERT INTO wx_subscription_outbox
                (dedupe_key,purpose,business_type,business_id,business_status,recipient_user_id,template_id,payload_json,context_json,cycle_key,page)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                'pending-plan-811','approval_pending','plan_schedule','81','change_submitted',1,
                'template','{}','{}','event:811','/pages/review/view?target_type=plan_schedule&target_id=81&cycle_key=event%3A811'))
            db.execute("""INSERT INTO wx_subscription_outbox
                (dedupe_key,purpose,business_type,business_id,business_status,recipient_user_id,template_id,payload_json,context_json,cycle_key,page)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                'result-plan-811','approval_result','plan_schedule','81','change_rejected',2,
                'template',json.dumps({'thing5': {'value': '已退回'}, 'thing4': {'value': '原计划继续有效；调整日期'}}),
                '{}','event:811','/pages/plan-detail/plan-detail?id=81'))
            db.execute("INSERT INTO work_orders VALUES ('WO-82','reviewing',2,10,'')")
            db.execute("""INSERT INTO wx_subscription_outbox
                (dedupe_key,purpose,business_type,business_id,business_status,recipient_user_id,template_id,payload_json,context_json,cycle_key,page)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                'pending-work-1','approval_pending','workorder','WO-82','reviewing',3,
                'template','{}',json.dumps({'review_cycle': 1}),'review:1','/pages/review/view?target_type=workorder_review&target_id=WO-82&cycle_key=review%3A1'))
            db.execute("""INSERT INTO wx_subscription_outbox
                (dedupe_key,purpose,business_type,business_id,business_status,recipient_user_id,template_id,payload_json,context_json,cycle_key,page)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                'result-work-1','approval_result','workorder','WO-82','in_progress',2,
                'template',json.dumps({'thing5': {'value': '已退回'}, 'thing4': {'value': '请补拍'}}),
                json.dumps({'review_cycle': 1}),'review:1','/pages/workorder/workorder?order_no=WO-82'))
            db.execute("""INSERT INTO wx_subscription_outbox
                (dedupe_key,purpose,business_type,business_id,business_status,recipient_user_id,template_id,payload_json,context_json,cycle_key,page)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                'pending-work-2','approval_pending','workorder','WO-82','reviewing',3,
                'template','{}',json.dumps({'review_cycle': 2}),'review:2','/pages/review/view?target_type=workorder_review&target_id=WO-82&cycle_key=review%3A2'))
            db.commit()
        plan = self.client.get('/api/audit/target-status?target_type=plan_schedule&target_id=81&cycle_key=event%3A811', headers=self.headers('admin'))
        old_work = self.client.get('/api/audit/target-status?target_type=workorder_review&target_id=WO-82&cycle_key=review%3A1', headers=self.headers('reviewer'))
        current_work = self.client.get('/api/audit/target-status?target_type=workorder_review&target_id=WO-82&cycle_key=review%3A2', headers=self.headers('reviewer'))
        wrong = self.client.get('/api/audit/target-status?target_type=workorder_review&target_id=WO-82&cycle_key=review%3A9', headers=self.headers('reviewer'))
        self.assertEqual((plan.status_code, old_work.status_code, current_work.status_code, wrong.status_code), (200, 200, 200, 404))
        self.assertEqual((plan.json['state'], plan.json['result_label']), ('processed', '已退回'))
        self.assertIn('原计划继续有效', plan.json['result_detail'])
        self.assertEqual((old_work.json['state'], old_work.json['result_label']), ('processed', '已退回'))
        self.assertEqual(current_work.json['state'], 'pending')

    def test_binding_failure_is_explicit_without_secret_details(self):
        with mock.patch.object(app_module, '_wx_code2openid', return_value=''):
            response = self.client.post('/api/mobile/bind-openid', headers=self.headers('operator'), json={'code': 'bad'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json['bound'])
        self.assertNotIn('openid-applicant', json.dumps(response.json))

    def test_openid_binding_is_first_bind_or_idempotent_never_an_overwrite(self):
        with app_module.get_db() as db:
            db.execute("UPDATE users SET openid='' WHERE id=2")
            db.commit()
        with mock.patch.object(app_module, '_wx_code2openid', return_value='openid-new'):
            first = self.client.post('/api/mobile/bind-openid', headers=self.headers('operator'), json={'code': 'first'})
            repeated = self.client.post('/api/mobile/bind-openid', headers=self.headers('operator'), json={'code': 'same'})
            with app_module.get_db() as db:
                after_same = db.execute('SELECT openid FROM users WHERE id=2').fetchone()['openid']
        with mock.patch.object(app_module, '_wx_code2openid', return_value='openid-other'):
            conflict_account = self.client.post('/api/mobile/bind-openid', headers=self.headers('operator'), json={'code': 'other'})
        self.assertEqual((first.status_code, repeated.status_code, conflict_account.status_code), (200, 200, 409))
        self.assertTrue(first.json['bound'])
        self.assertTrue(repeated.json['idempotent'])
        self.assertEqual(after_same, 'openid-new')
        self.assertEqual(conflict_account.json['code'], 'ACCOUNT_OPENID_CONFLICT')

        with app_module.get_db() as db:
            db.execute("UPDATE users SET openid='' WHERE id=2")
            db.execute("UPDATE users SET openid='openid-owned' WHERE id=1")
            db.commit()
        with mock.patch.object(app_module, '_wx_code2openid', return_value='openid-owned'):
            conflict_openid = self.client.post('/api/mobile/bind-openid', headers=self.headers('operator'), json={'code': 'owned'})
        with app_module.get_db() as db:
            current = db.execute('SELECT openid FROM users WHERE id=2').fetchone()['openid']
        self.assertEqual(conflict_openid.status_code, 409)
        self.assertEqual(conflict_openid.json['code'], 'OPENID_ACCOUNT_CONFLICT')
        self.assertEqual(current, '')
        self.assertNotIn('openid-owned', json.dumps(conflict_openid.json))

        with app_module.get_db() as db:
            db.execute("UPDATE users SET openid='openid-duplicate' WHERE id IN (1,2)")
            db.commit()
        with mock.patch.object(app_module, '_wx_code2openid', return_value='openid-duplicate'):
            duplicate = self.client.post('/api/mobile/bind-openid', headers=self.headers('operator'), json={'code': 'duplicate'})
        with app_module.get_db() as db:
            owners = db.execute("SELECT id FROM users WHERE openid='openid-duplicate' ORDER BY id").fetchall()
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json['code'], 'OPENID_ACCOUNT_CONFLICT')
        self.assertEqual([row['id'] for row in owners], [1, 2])

    def test_admin_unbinds_wechat_with_audit_and_session_revocation(self):
        with app_module.get_db() as db:
            db.execute("UPDATE users SET auth_version=7,openid='openid-target' WHERE id=2")
            db.execute("INSERT INTO auth_sessions (token_hash,user_id) VALUES ('session-target',2)")
            db.commit()
        success = self.client.delete('/api/users/2/wechat-binding', headers=self.headers('admin'), json={
            'reason': '体验账号审核完成，按流程回收绑定'
        })
        self.assertEqual(success.status_code, 200, success.json)
        with app_module.get_db() as db:
            user = db.execute('SELECT openid,auth_version FROM users WHERE id=2').fetchone()
            session = db.execute('SELECT revoked_at,revoke_reason FROM auth_sessions WHERE user_id=2').fetchone()
            audit = db.execute('SELECT action,target_id,operator_id,details FROM operation_logs ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual((user['openid'], user['auth_version']), ('', 8))
        self.assertEqual(session['revoke_reason'], 'wechat_binding_removed')
        self.assertIsNotNone(session['revoked_at'])
        self.assertEqual((audit['action'], audit['target_id'], audit['operator_id']),
                         ('wechat_binding_removed', 2, 1))
        self.assertIn('体验账号审核完成', audit['details'])
        self.assertNotIn('openid-target', audit['details'])

        repeated = self.client.delete('/api/users/2/wechat-binding', headers=self.headers('admin'), json={'reason': '重复操作'})
        forbidden = self.client.delete('/api/users/1/wechat-binding', headers=self.headers('reviewer'), json={'reason': '越权'})
        missing = self.client.delete('/api/users/999/wechat-binding', headers=self.headers('admin'), json={'reason': '不存在'})
        empty = self.client.delete('/api/users/1/wechat-binding', headers=self.headers('admin'), json={'reason': ' '})
        long_reason = self.client.delete('/api/users/1/wechat-binding', headers=self.headers('admin'), json={'reason': 'x' * 501})
        self.assertEqual((repeated.status_code, forbidden.status_code, missing.status_code, empty.status_code, long_reason.status_code),
                         (409, 403, 404, 400, 400))

    def test_current_admin_unbind_returns_revoked_marker_and_invalidates_its_token(self):
        response = self.client.delete('/api/users/1/wechat-binding', headers=self.headers('admin'), json={
            'reason': '管理员主动更换绑定微信'
        })
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['current_session_revoked'])
        with app_module.get_db() as db:
            user = db.execute('SELECT openid,auth_version FROM users WHERE id=1').fetchone()
            session = db.execute('SELECT revoked_at,revoke_reason FROM auth_sessions WHERE user_id=1').fetchone()
        self.assertEqual((user['openid'], user['auth_version']), ('', 2))
        self.assertEqual(session['revoke_reason'], 'wechat_binding_removed')
        self.assertIsNotNone(session['revoked_at'])
        current_session = self.client.get('/api/auth/me', headers=self.headers('admin'))
        self.assertEqual(current_session.status_code, 401, current_session.json)

    def test_unbind_stale_and_database_failures_leave_everything_unchanged(self):
        with app_module.get_db() as db:
            db.execute("UPDATE users SET openid='openid-stale' WHERE id=2")
            db.execute("""CREATE TRIGGER ignore_unbind BEFORE UPDATE OF openid ON users
                WHEN OLD.id=2 AND NEW.openid='' BEGIN SELECT RAISE(IGNORE); END""")
            db.commit()
        stale = self.client.delete('/api/users/2/wechat-binding', headers=self.headers('admin'), json={'reason': '并发变化'})
        self.assertEqual((stale.status_code, stale.json['code']), (409, 'WECHAT_BINDING_STALE'))
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT openid FROM users WHERE id=2').fetchone()['openid'], 'openid-stale')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_logs').fetchone()[0], 0)

        with app_module.get_db() as db:
            db.execute('DROP TRIGGER ignore_unbind')
            db.execute("""CREATE TRIGGER fail_unbind BEFORE UPDATE OF openid ON users
                WHEN OLD.id=2 AND NEW.openid='' BEGIN SELECT RAISE(ABORT, 'forced unbind failure'); END""")
            db.commit()
        failed = self.client.delete('/api/users/2/wechat-binding', headers=self.headers('admin'), json={'reason': '数据库失败'})
        self.assertEqual(failed.status_code, 503, failed.json)
        with app_module.get_db() as db:
            self.assertEqual(db.execute('SELECT openid FROM users WHERE id=2').fetchone()['openid'], 'openid-stale')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_logs').fetchone()[0], 0)

    def test_template_roles_come_from_current_database_and_token_expiry_is_retryable(self):
        with app_module.get_db() as db:
            db.execute("DELETE FROM user_roles WHERE user_id=1 AND role='admin'")
            db.commit()
        response = self.client.get('/api/mobile/subscription-templates', headers=self.headers('admin'))
        self.assertNotIn('approval_pending', [row['purpose'] for row in response.json['templates']])

        fake_response = mock.MagicMock()
        fake_response.__enter__.return_value.read.return_value = json.dumps({
            'errcode': 42001, 'errmsg': 'access token expired'
        }).encode()
        app_module._WX_TOKEN_CACHE.update(token='stale-token', expire_at=9999999999)
        with (mock.patch.object(app_module, 'WX_APPSECRET', 'test-secret'),
              mock.patch('urllib.request.urlopen', return_value=fake_response)):
            result = app_module._wx_send_subscribe_result(
                'openid-applicant', app_module.WX_TMPL_APPROVAL_RESULT,
                app_module._wx_result_data('车辆申请', '测试', '已通过',
                                           '2026-09-05 17:00:00', '请按安排用车'),
                '/pages/review/view?target_type=vehicle_application&target_id=1')
        self.assertEqual(result['status'], 'temporary_failure')
        self.assertEqual(app_module._WX_TOKEN_CACHE, {'token': '', 'expire_at': 0})

    def test_miniprogram_state_accepts_only_supported_values_and_reaches_send_payload(self):
        self.assertEqual(app_module._resolve_miniprogram_state('developer'), 'developer')
        self.assertEqual(app_module._resolve_miniprogram_state('trial'), 'trial')
        self.assertEqual(app_module._resolve_miniprogram_state('formal'), 'formal')
        self.assertIn(app_module._resolve_miniprogram_state('unsupported'), ('developer', 'formal'))
        captured = {}
        fake_response = mock.MagicMock()
        fake_response.__enter__.return_value.read.return_value = b'{"errcode":0}'
        def capture(request, timeout=10):
            captured['payload'] = json.loads(request.data.decode('utf-8'))
            return fake_response
        with (mock.patch.object(app_module, 'WX_APPSECRET', 'test-secret'),
              mock.patch.object(app_module, 'WX_MINIPROGRAM_STATE', 'trial'),
              mock.patch.object(app_module, '_wx_get_access_token', return_value='token'),
              mock.patch('urllib.request.urlopen', side_effect=capture)):
            sent = app_module._wx_send_subscribe_result(
                'openid-applicant', app_module.WX_TMPL_APPROVAL_RESULT,
                app_module._wx_result_data('车辆申请', '测试', '已通过', '2026-09-07 10:00:00', '请按安排用车'),
                '/pages/vehicle/vehicle?application_id=1')
        self.assertTrue(sent['ok'])
        self.assertEqual(captured['payload']['miniprogram_state'], 'trial')

    def test_send_result_classifies_wechat_and_configuration_failures(self):
        payload = app_module._wx_result_data('车辆申请', '测试', '已通过', '2026-09-07 10:00:00', '请按安排用车')
        self.assertEqual(app_module._resolve_miniprogram_state('invalid'), 'developer')
        with mock.patch.object(app_module, 'APP_RUNTIME_PROFILE', 'production'):
            self.assertEqual(app_module._resolve_miniprogram_state('invalid'), 'formal')
        cases = [
            (0, 'sent', True),
            (43101, 'no_authorization', True),
            (40003, 'permanent_failure', True),
            (42001, 'temporary_failure', False),
        ]
        for errcode, status, permanent in cases:
            with self.subTest(errcode=errcode):
                response = mock.MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps({'errcode': errcode}).encode()
                with (mock.patch.object(app_module, 'WX_APPSECRET', 'test-secret'),
                      mock.patch.object(app_module, '_wx_get_access_token', return_value='token'),
                      mock.patch('urllib.request.urlopen', return_value=response)):
                    result = app_module._wx_send_subscribe_result('openid-applicant', 'template', payload, '/pages/message/message')
                self.assertEqual((result['status'], result['permanent']), (status, permanent))
        with mock.patch.object(app_module, 'WX_APPSECRET', ''):
            self.assertEqual(app_module._wx_send_subscribe_result('openid-applicant', 'template', payload)['status'],
                             'config_missing')
        with (mock.patch.object(app_module, 'WX_APPSECRET', 'test-secret'),
              mock.patch.object(app_module, '_wx_get_access_token', return_value='token'),
              mock.patch('urllib.request.urlopen', side_effect=OSError('offline'))):
            self.assertEqual(app_module._wx_send_subscribe_result('openid-applicant', 'template', payload)['status'],
                             'temporary_failure')

    def test_invalid_page_target_never_creates_or_sends_an_intent(self):
        with app_module.get_db() as db:
            intent_id = app_module._wx_queue_approval_result(
                db, 'vehicle_application', 99, 'approved', 2, '车辆申请', '无效目标',
                '已通过', '2026-09-05 17:01:00', '请查看', cycle_key='application:99',
                page='https://invalid.example/path')
            db.commit()
            count = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='wx_subscription_outbox'").fetchone()[0]
        self.assertIsNone(intent_id)
        self.assertEqual(count, 0)

    def test_notification_exact_query_is_user_scoped_and_ignores_current_history_partition(self):
        with app_module.get_db() as db:
            notice_id = app_module._create_notification(
                2, 'parts_request_result', 8, '备件申请已通过', '等待领用', db=db)
            other_id = app_module._create_notification(
                1, 'parts_request_result', 9, '其他人的通知', '不可见', db=db)
            db.execute('UPDATE notifications SET is_read=1 WHERE id=?', (notice_id,))
            db.commit()
        exact = self.client.get(
            f'/api/notifications?status=unread&notification_id={notice_id}',
            headers=self.headers('operator'))
        hidden = self.client.get(
            f'/api/notifications?notification_id={other_id}', headers=self.headers('operator'))
        self.assertEqual([row['id'] for row in exact.json['notifications']], [notice_id])
        self.assertEqual(hidden.json['notifications'], [])

    def test_audit_target_status_distinguishes_processed_missing_and_forbidden(self):
        with app_module.get_db() as db:
            db.execute("INSERT INTO plan_schedules VALUES (70,'approved')")
            db.execute("INSERT INTO vehicle_applications VALUES (71,'rejected')")
            db.execute("INSERT INTO parts_requests VALUES (72,'approved')")
            db.execute("INSERT INTO work_orders VALUES ('WO-73','closed',1,10,'')")
            db.commit()
        processed = self.client.get('/api/audit/target-status?target_type=vehicle_application&target_id=71',
                                    headers=self.headers('admin'))
        missing = self.client.get('/api/audit/target-status?target_type=vehicle_application&target_id=999',
                                  headers=self.headers('admin'))
        forbidden = self.client.get('/api/audit/target-status?target_type=vehicle_application&target_id=71',
                                    headers=self.headers('reviewer'))
        workorder = self.client.get('/api/audit/target-status?target_type=workorder_review&target_id=WO-73',
                                    headers=self.headers('reviewer'))
        self.assertEqual((processed.status_code, missing.status_code, forbidden.status_code, workorder.status_code),
                         (200, 404, 403, 200))
        self.assertEqual(processed.json['state'], 'processed')
        self.assertEqual(processed.json['result_label'], '已退回')
        self.assertEqual(workorder.json['result_label'], '已通过')


if __name__ == '__main__':
    unittest.main()
