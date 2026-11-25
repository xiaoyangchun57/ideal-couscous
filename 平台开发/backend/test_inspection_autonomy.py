import base64
import io
import json
from datetime import datetime, timedelta
from PIL import Image
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest import mock

import app as service
import inspection_visit_facts as visits
import test_plan_resource_archive_flow as fixtures


class InspectionAutonomyTest(unittest.TestCase):
    def setUp(self):
        fixtures.PlanResourceArchiveFlowTest.setUp(self)
        with self.db() as db:
            db.execute("ALTER TABLE work_orders ADD COLUMN level TEXT")
            db.execute("ALTER TABLE work_orders ADD COLUMN assignee TEXT")
            db.execute("ALTER TABLE work_orders ADD COLUMN event_type TEXT")
            db.execute("""CREATE TABLE plan_departure_confirmations (
                schedule_id INTEGER,user_id INTEGER,work_date TEXT,
                vehicle_confirmed INTEGER,parts_confirmed INTEGER,note TEXT,confirmed_at TEXT)""")
    tearDown = fixtures.PlanResourceArchiveFlowTest.tearDown
    headers = staticmethod(fixtures.PlanResourceArchiveFlowTest.headers)
    day = staticmethod(fixtures.PlanResourceArchiveFlowTest.day)
    add_submitted_schedule = fixtures.PlanResourceArchiveFlowTest.add_submitted_schedule
    creation_payload = fixtures.PlanResourceArchiveFlowTest.creation_payload

    def evidence_schema(self):
        # Same photo contract as the existing provenance fixture, on this test's
        # isolated DB/uploads; production and the shared UI service are untouched.
        fields = {
            'filename': 'TEXT', 'stored_path': 'TEXT', 'file_size': 'INTEGER',
            'description': 'TEXT', 'site_id': 'INTEGER', 'uploader_id': 'INTEGER',
            'uploader_name': 'TEXT', 'gps_lat': 'REAL', 'gps_lng': 'REAL',
            'taken_at': 'TEXT', 'created_at': 'TEXT', 'category': 'TEXT',
            'capture_source': 'TEXT', 'sha256_hash': 'TEXT', 'duplicate_of_id': 'INTEGER',
            'perceptual_hash': "TEXT DEFAULT ''", 'watermark_code': "TEXT DEFAULT ''",
            'watermark_text': "TEXT DEFAULT ''", 'recognized_category': "TEXT DEFAULT ''",
            'match_status': "TEXT DEFAULT 'manual'", 'match_confidence': 'REAL',
            'review_required': 'INTEGER DEFAULT 0', 'requirement_id': 'INTEGER',
            'reviewer_id': 'INTEGER', 'reviewed_at': 'TEXT', 'reject_reason': "TEXT DEFAULT ''",
            'extra_json': "TEXT DEFAULT '{}'",
            'archive_name': "TEXT DEFAULT ''", 'is_flagged': 'INTEGER DEFAULT 0',
            'flag_reason': "TEXT DEFAULT ''", 'flag_rule': "TEXT DEFAULT ''",
            'evidence_qualification': "TEXT DEFAULT 'review'", 'evidence_basis': "TEXT DEFAULT 'unknown'",
            'evidence_reason': "TEXT DEFAULT ''", 'evidence_next_action': "TEXT DEFAULT ''",
            'evidence_evaluated_at': 'TEXT', 'evidence_evaluation_id': 'INTEGER',
            'voided_at': 'TEXT', 'voided_by': 'INTEGER', 'void_reason': "TEXT DEFAULT ''",
        }
        with self.db() as db:
            for name, definition in fields.items():
                db.execute(f'ALTER TABLE operation_attachments ADD COLUMN {name} {definition}')
            db.executescript("""
                ALTER TABLE work_orders ADD COLUMN review_submitted_at TEXT;
                ALTER TABLE work_orders ADD COLUMN description TEXT;
                ALTER TABLE work_orders ADD COLUMN remark TEXT;
                CREATE TABLE parts_requests (
                    id INTEGER PRIMARY KEY, plan_id INTEGER, site_id INTEGER, work_order_no TEXT,
                    request_no TEXT, reason TEXT, fulfillment_type TEXT, requested_part_name TEXT,
                    specification TEXT, estimated_amount REAL, requester_id INTEGER, created_at TEXT,
                    status TEXT);
                CREATE TABLE photo_requirements (
                    id INTEGER PRIMARY KEY,item_name TEXT,review_required INTEGER,
                    watermark_keyword TEXT,category TEXT);
                CREATE TABLE user_roles (user_id INTEGER, role TEXT);
                INSERT INTO user_roles VALUES (1,'admin');
                UPDATE users SET role='admin' WHERE id=1;
                CREATE TABLE photo_capture_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,token_hash TEXT UNIQUE,
                    user_id INTEGER,site_id INTEGER,plan_id INTEGER,item_id INTEGER,
                    work_order_id INTEGER,issued_at TEXT,expires_at TEXT,used_at TEXT,
                    attachment_id INTEGER,gps_lat REAL,gps_lng REAL,distance_m REAL,
                    rework_required_at TEXT,capture_source TEXT DEFAULT '',
                    nonce TEXT DEFAULT '',anti_counterfeit_code TEXT DEFAULT '',
                    location_source TEXT DEFAULT '',location_fact_id INTEGER,checkin_time TEXT);
                CREATE TABLE attachment_evidence_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,attachment_id INTEGER,
                    qualification TEXT,basis TEXT,verified_taken_at TEXT,reason TEXT,
                    next_action TEXT,facts_json TEXT,evaluation_type TEXT,rule_version TEXT,
                    actor_id INTEGER,actor_name TEXT,replaces_evaluation_id INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """)

    def upload_submit(self, item_id, color='blue', site_id=1, plan_id=None, omit_plan=False):
        plan_id = plan_id or self.plan_id()
        image = io.BytesIO()
        Image.new('RGB', (64, 48), color).save(image, format='JPEG')
        uploaded = self.client.post('/api/mobile/upload-site-photo',
            headers=self.headers('operator-token'), json={
                'site_id': site_id, 'plan_id': plan_id, 'item_id': item_id,
                'capture_source': 'album',
                'image': 'data:image/jpeg;base64,' + base64.b64encode(image.getvalue()).decode(),
                '_idempotency_key': f'upload-{item_id}'})
        self.assertEqual(uploaded.status_code, 200, uploaded.json)
        payload = {'item_id': item_id, 'plan_id': plan_id, 'result': 'normal',
            'photo_urls': json.dumps([uploaded.json['url']]), 'remark': '真实接口提交',
            '_idempotency_key': f'submit-{item_id}'}
        if omit_plan:
            payload.pop('plan_id')
        submitted = self.client.post('/api/mobile/submit-item',
            headers=self.headers('operator-token'), json=payload)
        self.assertEqual(submitted.status_code, 200, submitted.json)
        return uploaded.json, payload

    def tasks(self, site_id=1, scope=None):
        suffix = ('?scope=' + scope) if scope else ''
        return self.client.get(f'/api/mobile/execution-plans/{self.plan_id()}/sites/{site_id}{suffix}',
            headers=self.headers('operator-token'))

    def prepare(self, vehicle_id=None, status='draft'):
        with self.db() as db:
            db.execute("INSERT INTO inspection_templates VALUES (10,'active','weekly','周检','',1)")
            db.execute("INSERT INTO inspection_configs VALUES ('water_quality',10,1)")
            db.executemany('INSERT INTO inspection_template_items VALUES (?,?,?,?,?,?,?,?)', [
                (101,10,'读数','常规',0,0,1,1), (102,10,'状态','常规',0,0,1,2)])
        self.add_submitted_schedule(5, vehicle_id=vehicle_id)
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET status=?,schedule_type='weekly' WHERE id=5", (status,))

    def confirm(self, version=1, token='operator-token'):
        return self.client.post('/api/plan-schedules/5/confirm',
            headers=self.headers(token), json={'version': version})

    def plan_id(self):
        with self.db() as db:
            return db.execute('SELECT id FROM insp_plans WHERE plan_schedule_id=5').fetchone()[0]

    def test_fifteen_items_depart_correct_continue_through_real_photo_and_submit_apis(self):
        clock = mock.patch.object(fixtures,'BUSINESS_NOW',
            datetime(2026,11,15,23,50,tzinfo=fixtures.BUSINESS_TIMEZONE))
        clock.start(); self.addCleanup(clock.stop)
        self.prepare(); self.evidence_schema()
        with self.db() as db:
            db.executemany('INSERT INTO inspection_template_items VALUES (?,?,?,?,?,?,?,?)',
                [(103+n,10,f'检查{n}','常规',0,0,1,n+3) for n in range(13)])
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(self.arrive().status_code, 200)
        with self.db() as db:
            items = [row[0] for row in db.execute('SELECT id FROM insp_plan_items ORDER BY id')]
        photos = [self.upload_submit(item)[0]['url'] for item in items[:11]]
        before = self.visit_state()
        self.assertEqual(self.leave(confirm=False).status_code, 409)
        self.assertEqual(self.visit_state()['visit_event_id'], before['visit_event_id'])
        left = self.leave()
        self.assertEqual((left.json['completed'], left.json['total']), (11, 15), left.json)
        read = self.tasks()
        self.assertEqual(read.status_code, 200, read.json)
        self.assertEqual((read.json['completed'], read.json['total']), (11,15))
        correction = self.visit_state()
        self.assertEqual(self.correct(correction).status_code, 200)
        self.assertTrue(self.correct(correction).json['already_corrected'])
        for item in items[11:]:
            self.upload_submit(item)
        final = self.leave()
        self.assertEqual(final.status_code, 200, final.json)
        self.assertEqual((final.json['completed'], final.json['total'], final.json['execution_status']),
            (15,15,'completed'))
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT generate_date FROM insp_plans').fetchone()[0],self.day())
            stored = [json.loads(row[0])[0] for row in db.execute('SELECT photo_urls FROM insp_plan_items ORDER BY id')]
            self.assertEqual(stored[:11], photos)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operation_attachments WHERE source_type='inspection'").fetchone()[0],15)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_use_records').fetchone()[0],0)

    def test_new_pending_photo_after_departure_blocks_stale_correction(self):
        self.prepare(); self.evidence_schema()
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(self.arrive().status_code, 200)
        self.assertEqual(self.leave().status_code, 200)
        departed = self.visit_state()
        self.assertTrue(departed['can_correct_checkout'])
        with self.db() as db:
            item = db.execute('SELECT id FROM insp_plan_items ORDER BY id').fetchone()[0]
        image = io.BytesIO()
        Image.new('RGB', (64, 48), 'green').save(image, format='JPEG')
        upload = self.client.post('/api/mobile/upload-site-photo',
            headers=self.headers('operator-token'), json={
                'site_id': 1, 'plan_id': self.plan_id(), 'item_id': item,
                'capture_source': 'album', '_idempotency_key': 'post-departure-photo',
                'image': 'data:image/jpeg;base64,' + base64.b64encode(image.getvalue()).decode()})
        self.assertEqual(upload.status_code, 200, upload.json)
        self.assertFalse(self.visit_state()['can_correct_checkout'])
        self.assertEqual(self.correct(departed).status_code, 409)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operation_attachments').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT result FROM insp_plan_items WHERE id=?', (item,)).fetchone()[0], None)

    def test_resource_conflict_and_unissued_parts_still_allow_real_submission(self):
        self.prepare(vehicle_id=1); self.evidence_schema()
        with self.db() as db:
            db.execute("INSERT INTO vehicle_applications(id,vehicle_id,applicant_id,status) VALUES(7,1,3,'approved')")
            db.execute("INSERT INTO vehicle_use_records(application_id,status) VALUES(7,'checked_out')")
            db.execute("UPDATE vehicles SET status='in_use' WHERE id=1")
            db.execute("INSERT INTO spare_parts_inventory VALUES(1,0,'滤芯','PART-1','只')")
            db.execute("UPDATE plan_schedules SET spare_parts=? WHERE id=5",
                (json.dumps([{'part_id':1,'quantity':3}]),))
        confirmed = self.confirm()
        self.assertEqual(confirmed.status_code,200,confirmed.json)
        self.assertTrue(confirmed.json['vehicle_unresolved'])
        self.assertEqual(self.arrive().status_code,200)
        with self.db() as db:
            item = db.execute('SELECT id FROM insp_plan_items ORDER BY id').fetchone()[0]
        self.upload_submit(item)
        read = self.tasks()
        self.assertEqual(read.status_code,200,read.json)
        self.assertEqual((read.json['completed'],read.json['total']),(1,2))
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_use_records').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COALESCE(SUM(issued_quantity),0) FROM plan_resource_reservations').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT quantity FROM spare_parts_inventory').fetchone()[0],0)

    def test_same_name_sites_keep_photos_and_departures_in_their_selected_task(self):
        self.prepare(); self.evidence_schema()
        with self.db() as db:
            db.execute("UPDATE sites SET name='同名站' WHERE id IN (1,2)")
            db.execute("INSERT INTO user_sites VALUES(2,2)")
            db.execute("UPDATE plan_schedules SET plan_data=? WHERE id=5",
                (json.dumps({self.day():{'sites':[1,2]}}),))
        self.assertEqual(self.confirm().status_code,200)
        self.assertEqual(self.arrive().status_code,200)
        arrival = self.client.post('/api/mobile/check-in',headers=self.headers('operator-token'),json={
            'plan_id':self.plan_id(),'site_id':2,'lat':28.69,'lng':115.74,'_idempotency_key':'second-site'})
        self.assertEqual(arrival.status_code,200,arrival.json)
        with self.db() as db:
            rows = db.execute('SELECT id,site_id FROM insp_plan_items ORDER BY site_id,id').fetchall()
        photo_a, _ = self.upload_submit(rows[0]['id'])
        photo_b, _ = self.upload_submit(rows[2]['id'],color='red',site_id=2)
        second_before = self.tasks(2).json
        self.assertEqual(self.leave().status_code,200)
        self.assertEqual(self.correct().status_code,200)
        second_after = self.tasks(2).json
        self.assertEqual((second_after['completed'],second_after['total']),(1,2))
        self.assertEqual(second_after['visit_version'],second_before['visit_version'])
        self.assertNotEqual(photo_a['url'],photo_b['url'])
        first = self.tasks().json
        self.assertEqual((first['completed'],first['total']),(1,2))
        a_photos = [url for cat in first['categories'] for item in cat['items']
            for url in json.loads(item['photo_urls'])]
        self.assertIn(photo_a['url'],a_photos)
        self.assertNotIn(photo_b['url'],a_photos)
        mismatch = self.client.post('/api/mobile/submit-item',headers=self.headers('operator-token'),json={
            'item_id':rows[1]['id'],'plan_id':99999,'result':'normal','remark':'错误任务'})
        self.assertEqual(mismatch.status_code,409,mismatch.json)
        with self.db() as db:
            self.assertIsNone(db.execute('SELECT result FROM insp_plan_items WHERE id=?',(rows[1]['id'],)).fetchone()[0])

    def test_stopped_partial_task_keeps_trip_material_and_quality_review(self):
        self.prepare(vehicle_id=1); self.evidence_schema()
        with self.db() as db:
            db.executemany('INSERT INTO inspection_template_items VALUES (?,?,?,?,?,?,?,?)',
                [(103+n,10,f'检查{n}','常规',0,0,1,n+3) for n in range(13)])
            db.execute("UPDATE users SET role='reviewer' WHERE id=3")
            db.execute("INSERT INTO user_roles VALUES(3,'reviewer')")
            db.execute("INSERT INTO user_sites VALUES(3,1)")
        service._tokens['other-token'] = {'id': 3, 'role': 'reviewer', 'roles': ['reviewer'], 'real_name': 'Other'}
        self.assertEqual(self.confirm().status_code,200)
        self.assertEqual(self.arrive().status_code,200)
        with self.db() as db:
            item = db.execute('SELECT id FROM insp_plan_items ORDER BY id').fetchone()[0]
        photo, _ = self.upload_submit(item)
        with self.db() as db:
            rest = [row[0] for row in db.execute('SELECT id FROM insp_plan_items WHERE id!=? ORDER BY id LIMIT 10', (item,))]
        for other_item in rest:
            self.upload_submit(other_item)
        with self.db() as db:
            db.execute('ALTER TABLE vehicle_applications ADD COLUMN execution_plan_id INTEGER')
            application = db.execute("SELECT id FROM vehicle_applications WHERE status='approved'").fetchone()[0]
            db.execute("INSERT INTO vehicle_use_records(application_id,status) VALUES(?,'checked_out')",(application,))
            db.execute("""INSERT INTO vehicle_applications(vehicle_id,applicant_id,execution_plan_id,status,reason)
                VALUES(1,2,?,'approved','本次继续巡检')""",(self.plan_id(),))
            db.execute("""INSERT INTO plan_resource_reservations(schedule_id,part_id,planned_quantity,
                reserved_quantity,issued_quantity,used_quantity,returned_quantity,status)
                VALUES(5,1,5,2,3,1,0,'issued')""")
        stopped = self.client.post('/api/plan-schedules/5/stop',headers=self.headers('operator-token'),
            json={'version':1,'reason':'天气原因，停止剩余'})
        self.assertEqual(stopped.status_code,200,stopped.json)
        self.assertEqual(stopped.json['progress'][0]['completed'],11)
        self.assertEqual(stopped.json['progress'][0]['total'],15)
        read = self.tasks(scope='result')
        self.assertEqual(read.status_code,200,read.json)
        self.assertEqual((read.json['completed'],read.json['total']),(11,15))
        reviewer = self.client.get(f'/api/mobile/execution-plans/{self.plan_id()}/sites/1?scope=review',
            headers=self.headers('other-token'))
        self.assertEqual(reviewer.status_code,200,reviewer.json)
        detail = self.client.get('/api/plan-schedules/5', headers=self.headers('operator-token'))
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json['cancellation']['event_type'], 'stopped_remaining')
        self.assertEqual(detail.json['cancellation']['reason'], '天气原因，停止剩余')
        self.assertEqual(detail.json['field_status'], 'stopped')
        pending = self.client.get('/api/audit/pending', headers=self.headers('other-token'))
        self.assertEqual(pending.status_code, 200, pending.json)
        self.assertTrue(any(row.get('plan_id') == self.plan_id() for row in pending.json), pending.json)
        stats = self.client.get('/api/audit/stats', headers=self.headers('other-token'))
        self.assertEqual(stats.status_code, 200, stats.json)
        self.assertEqual(stats.json['plan_pending'], 0)
        review_payload = {'attachment_ids':[photo['id']],'action':'reject','reject_reason':'照片模糊'}
        rejected = self.client.post('/api/operation-attachments/review',
            headers=self.headers('other-token'),json=review_payload)
        self.assertEqual(rejected.status_code,200,rejected.json)
        replay = self.client.post('/api/operation-attachments/review',
            headers=self.headers('other-token'),json=review_payload)
        self.assertEqual(replay.status_code,200,replay.json)
        quality = self.client.get('/api/mobile/photo-quality-monthly?month='+self.day()[:7],
            headers=self.headers('operator-token'))
        self.assertEqual(quality.status_code,200,quality.json)
        self.assertEqual(quality.json['deduction'],1)
        self.assertEqual((quality.json['photos'][0]['plan_id'],quality.json['photos'][0]['score_month']),
            (self.plan_id(),self.day()[:7]))
        correction = self.client.post('/api/operation-attachments/review',
            headers=self.headers('other-token'),json={'attachment_ids':[photo['id']],'action':'approve',
                'correction_type':'mistaken_rejection','reason':'复核清晰'})
        self.assertEqual(correction.status_code,200,correction.json)
        quality = self.client.get('/api/mobile/photo-quality-monthly?month='+self.day()[:7],
            headers=self.headers('operator-token'))
        self.assertEqual(quality.json['deduction'],0)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_use_records WHERE returned_at IS NULL').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM vehicle_applications WHERE id=?',(application,)).fetchone()[0],'approved')
            self.assertEqual(db.execute("SELECT status FROM vehicle_applications WHERE reason='本次继续巡检'").fetchone()[0],'cancelled')
            self.assertEqual(tuple(db.execute('SELECT planned_quantity,reserved_quantity,issued_quantity,used_quantity,returned_quantity FROM plan_resource_reservations').fetchone()),(3,0,3,1,0))
            self.assertEqual(db.execute('SELECT status FROM insp_plans').fetchone()[0],'cancelled')
            self.assertEqual(db.execute('SELECT result FROM insp_plan_items WHERE id=?',(item,)).fetchone()[0],'normal')
        self.assertEqual(self.arrive('stopped-arrival').status_code,403)

    def test_cross_midnight_change_keeps_task_photos_and_removes_only_unperformed_items(self):
        self.prepare(); self.evidence_schema()
        self.assertEqual(self.confirm().status_code,200)
        self.assertEqual(self.arrive().status_code,200)
        with self.db() as db:
            items = [row[0] for row in db.execute('SELECT id FROM insp_plan_items ORDER BY id')]
        photo, _ = self.upload_submit(items[0])
        change = self.client.post('/api/plan-schedules/5/request-change',
            headers=self.headers('operator-token'),json={'version':1,'change_reason':'删掉无执行事实的状态项'})
        self.assertEqual(change.status_code,200,change.json)
        during = self.client.get('/api/mobile/today-execution',headers=self.headers('operator-token'))
        self.assertEqual(during.status_code,200,during.json)
        self.assertEqual(during.json['packages'][0]['plan_id'],self.plan_id())
        self.assertEqual(self.tasks().status_code,200)
        # Advance the business clock rather than changing the task's original date.
        class Tomorrow(datetime):
            @classmethod
            def now(cls,tz=None):
                value = fixtures.BUSINESS_NOW + timedelta(days=1)
                return value.replace(tzinfo=None) if tz is None else value.astimezone(tz)
        with mock.patch.object(service,'datetime',Tomorrow):
            carry = self.client.get('/api/mobile/today-execution',headers=self.headers('operator-token'))
            self.assertEqual(carry.status_code,200,carry.json)
            self.assertTrue(carry.json['packages'][0]['is_carryover'])
            self.assertEqual(carry.json['packages'][0]['work_date'],self.day())
            self.assertEqual(self.arrive('next-day-return').status_code,200)
            photo_2, payload = self.upload_submit(items[1],color='green')
            replay = self.client.post('/api/mobile/submit-item',headers=self.headers('operator-token'),json=payload)
            self.assertEqual(replay.status_code,200,replay.json)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT generate_date FROM insp_plans').fetchone()[0],self.day())
            self.assertEqual(json.loads(db.execute('SELECT photo_urls FROM insp_plan_items WHERE id=?',(items[0],)).fetchone()[0]),[photo['url']])
            self.assertEqual(db.execute('SELECT completed_at FROM insp_plan_items WHERE id=?',(items[1],)).fetchone()[0][:10],
                (fixtures.BUSINESS_NOW+timedelta(days=1)).strftime('%Y-%m-%d'))
            # A third untouched item represents an optional periodic item removed by this change.
            db.execute("INSERT INTO insp_plan_items(plan_id,site_id,template_id,item_name,frequency) VALUES(?,1,10,'待删除项','weekly')",(self.plan_id(),))
        edited = self.client.put('/api/plan-schedules/5',headers=self.headers('operator-token'),json={
            'version':change.json['version'],'plan_data':{self.day():{'sites':[1],'inspection_items':{'1':[101]}}}})
        self.assertEqual(edited.status_code,200,edited.json)
        self.assertEqual(self.confirm(edited.json['version']).status_code,200)
        read = self.tasks(scope='result')
        self.assertEqual(read.status_code,200,read.json)
        self.assertEqual((read.json['completed'],read.json['total']),(2,2))
        with self.db() as db:
            self.assertEqual(db.execute("SELECT execution_status FROM insp_plan_items WHERE item_name='待删除项'").fetchone()[0],'cancelled')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0],1)
            self.assertEqual(json.loads(db.execute('SELECT photo_urls FROM insp_plan_items WHERE id=?',(items[1],)).fetchone()[0]),[photo_2['url']])

    def test_optional_periodic_item_removal_survives_both_task_entries_and_refresh(self):
        self.prepare(); self.evidence_schema()
        with self.db() as db:
            for tid, frequency in ((20, 'monthly'), (30, 'quarterly')):
                db.execute('INSERT INTO inspection_templates VALUES (?,?,?,?,?,?)',
                    (tid, 'active', frequency, frequency, '', tid))
                db.execute('INSERT INTO inspection_template_items VALUES (?,?,?,?,?,?,?,?)',
                    (tid * 10, tid, frequency, '设备', 0, 0, 1, 1))
            db.execute('UPDATE plan_schedules SET plan_data=? WHERE id=5',
                (json.dumps({self.day(): {'sites': [1], 'inspection_items': {'1': [101, 102, 200, 300]}}}),))
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(self.arrive().status_code, 200)
        original = self.tasks()
        rows = [item for cat in original.json['categories'] for item in cat['items']]
        self.assertEqual({row['frequency'] for row in rows}, {'weekly', 'monthly', 'quarterly'})
        weekly = next(row['item_id'] for row in rows if row['item_name'] == '读数')
        photo, _ = self.upload_submit(weekly)
        changed = self.client.post('/api/plan-schedules/5/request-change',
            headers=self.headers('operator-token'), json={'version': 1, 'change_reason': '取消未执行月检'})
        edited = self.client.put('/api/plan-schedules/5', headers=self.headers('operator-token'),
            json={'version': changed.json['version'], 'plan_data': {
                self.day(): {'sites': [1], 'inspection_items': {'1': [101, 102, 300]}}}})
        self.assertEqual(edited.status_code, 200, edited.json)
        self.assertEqual(self.confirm(edited.json['version']).status_code, 200)
        for attempt in range(2):
            for response in (self.tasks(), self.client.get('/api/mobile/site-tasks/1',
                    headers=self.headers('operator-token'))):
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual((response.json['completed'], response.json['total']), (1, 3))
                active = [row for cat in response.json['categories'] for row in cat['items']]
                self.assertEqual({row['frequency'] for row in active}, {'weekly', 'quarterly'})
                self.assertEqual(json.loads(next(row['photo_urls'] for row in active
                    if row['item_id'] == weekly)), [photo['url']])
        with self.db() as db:
            self.assertEqual(db.execute("SELECT execution_status FROM insp_plan_items WHERE item_name='monthly'").fetchone()[0], 'cancelled')

    def test_old_completed_same_site_does_not_steal_selected_current_task(self):
        self.prepare(); self.evidence_schema()
        self.assertEqual(self.confirm().status_code, 200)
        current = self.plan_id()
        with self.db() as db:
            old = db.execute("""INSERT INTO insp_plans(plan_name,assignee,assignee_id,period,
                generate_date,status,completion_rate) VALUES('旧已完成','Operator',2,'weekly',?,'completed',100)""",
                (self.day(),)).lastrowid
            db.execute("""INSERT INTO insp_plan_items(plan_id,site_id,item_name,category,frequency,result,
                completed_at,check_out_time,review_status) VALUES(?,1,'旧检查','常规','weekly',
                'normal',?,?,2)""", (old, self.day()+' 00:01:00', self.day()+' 00:02:00'))
            item = db.execute('SELECT id FROM insp_plan_items WHERE plan_id=? ORDER BY id', (current,)).fetchone()[0]
        self.assertEqual(self.arrive().status_code, 200)
        generic = self.client.get('/api/mobile/site-tasks/1', headers=self.headers('operator-token'))
        self.assertEqual(generic.status_code, 200, generic.json)
        self.assertTrue(any(row['plan_id'] == current for cat in generic.json['categories'] for row in cat['items']))
        authorized = self.client.post('/api/mobile/photo-capture-session',
            headers=self.headers('operator-token'), json={'site_id': 1, 'item_id': item,
                'plan_id': current, 'capture_source': 'camera'})
        self.assertEqual(authorized.status_code, 200, authorized.json)
        photo, payload = self.upload_submit(item, plan_id=current, omit_plan=True)
        self.assertEqual(self.client.post('/api/mobile/submit-item',
            headers=self.headers('operator-token'), json=payload).status_code, 200)
        with self.db() as db:
            attachment = db.execute('SELECT plan_id,item_id FROM operation_attachments WHERE id=?', (photo['id'],)).fetchone()
            self.assertEqual(tuple(attachment), (current, item))
            self.assertEqual(db.execute('SELECT completion_rate FROM insp_plans WHERE id=?', (old,)).fetchone()[0], 100)

    def test_list_detail_execution_and_audit_agree_through_departure_correction_and_stop(self):
        self.prepare(); self.evidence_schema()
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(self.arrive().status_code, 200)
        with self.db() as db:
            item = db.execute('SELECT id FROM insp_plan_items ORDER BY id').fetchone()[0]
        for phase in ('confirmed', 'departed', 'corrected', 'stopped'):
            if phase == 'departed':
                self.upload_submit(item)
                self.assertEqual(self.leave().status_code, 200)
            if phase == 'corrected':
                self.assertEqual(self.correct().status_code, 200)
            if phase == 'stopped':
                response = self.client.post('/api/plan-schedules/5/stop',
                    headers=self.headers('operator-token'), json={'version': 1, 'reason': '终止剩余'})
                self.assertEqual(response.status_code, 200, response.json)
            detail = self.client.get('/api/plan-schedules/5', headers=self.headers('operator-token'))
            listing = self.client.get('/api/plan-schedules?page=1&page_size=20',
                headers=self.headers('operator-token'))
            self.assertEqual((detail.status_code, listing.status_code), (200, 200),
                (detail.json, listing.json))
            row = next(row for row in listing.json['items'] if row['id'] == 5)
            self.assertEqual((row['field_status'], row['execution_status']),
                (detail.json['field_status'], detail.json['execution_status']))
            summary = detail.json['generated_site_tasks'][0]
            self.assertEqual((summary['completed_items'], summary['total_items']),
                (0 if phase == 'confirmed' else 1, 2), phase)
            tasks = self.tasks(scope='result' if phase == 'stopped' else None)
            self.assertEqual(tasks.status_code, 200, tasks.json)
            self.assertEqual((tasks.json['completed'], tasks.json['total']),
                (summary['completed_items'], summary['total_items']))
            pending = self.client.get('/api/audit/pending', headers=self.headers('manager-token'))
            stats = self.client.get('/api/audit/stats', headers=self.headers('manager-token'))
            self.assertEqual((pending.status_code, stats.status_code), (200, 200), (pending.json, stats.json))
            self.assertEqual(stats.json['plan_pending'], 0)
            self.assertFalse(any(row['source_type'] == 'plan_schedule' for row in pending.json))
            today = self.client.get('/api/mobile/today-execution', headers=self.headers('operator-token'))
            self.assertEqual(today.status_code, 200, today.json)
            self.assertEqual(any(row['plan_id'] == self.plan_id() for row in today.json['packages']),
                phase != 'stopped')

    def test_need_vehicle_without_selection_is_not_implicitly_no_vehicle(self):
        self.prepare()
        with self.db() as db:
            db.execute('UPDATE plan_schedules SET no_vehicle_required=0 WHERE id=5')
            db.execute("INSERT INTO vehicle_applications(id,vehicle_id,applicant_id,start_at,end_at,status) VALUES(8,1,3,?,?,'pending')",
                (self.day()+' 08:00:00',self.day()+' 18:00:00'))
        result = self.confirm()
        self.assertEqual(result.status_code,200,result.json)
        self.assertTrue(result.json['vehicle_unresolved'])
        self.assertEqual(self.arrive().status_code,200)
        current = self.client.get('/api/mobile/today-execution',headers=self.headers('operator-token'))
        self.assertEqual(current.status_code,200,current.json)
        self.assertFalse(current.json['packages'][0]['no_vehicle_required'])


    def _prepare_r1_vehicle_reapplication(self, *, superseded=False, vehicle_id=1):
        self.prepare(vehicle_id=1)
        self.assertEqual(self.confirm().status_code, 200)
        plan_id = self.plan_id()
        with self.db() as db:
            db.execute('ALTER TABLE vehicle_applications ADD COLUMN execution_plan_id INTEGER')
            original = db.execute('SELECT id FROM vehicle_applications').fetchone()[0]
            db.execute("UPDATE vehicle_applications SET status='returned' WHERE id=?", (original,))
            db.execute("""INSERT INTO vehicle_use_records
                (application_id,start_mileage,end_mileage,returned_at,status)
                VALUES(?,1000,1020,?,'returned')""", (original, self.day()+' 00:10:00'))
            db.execute("INSERT INTO vehicles VALUES(2,'TEST-002','Other car','idle',500,NULL,NULL)")
            db.execute("""INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,execution_plan_id,start_at,end_at,status,reason,reject_reason)
                VALUES(77,1,2,?,?,?,'pending','原任务旧申请','历史备注')""",
                (plan_id, self.day()+' 01:00:00', self.day()+' 04:00:00'))
            if superseded:
                event_id = db.execute("SELECT id FROM plan_schedule_events WHERE event_type='confirmed'").fetchone()[0]
                db.execute('INSERT INTO plan_vehicle_supersessions VALUES(77,5,?,?)',
                    (event_id, self.day()+' 00:20:00'))
        return {'execution_plan_id': plan_id, 'vehicle_id': vehicle_id,
            'start_at': self.day()+' 01:00:00', 'end_at': self.day()+' 04:00:00',
            'reason': '原任务继续巡检', '_idempotency_key': 'r1-reapplication'}

    def _assert_r1_reapplication_and_retries(self, payload):
        response = self.client.post('/api/vehicle/applications',
            headers=self.headers('operator-token'), json=payload)
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual((response.json['status'], response.json['vehicle_id']),
            ('approved', payload['vehicle_id']))
        for key in (payload['_idempotency_key'], 'r1-second-click'):
            duplicate = self.client.post('/api/vehicle/applications',
                headers=self.headers('operator-token'), json={**payload, '_idempotency_key': key})
            self.assertEqual(duplicate.status_code, 200, duplicate.json)
            self.assertEqual(duplicate.json['id'], response.json['id'])
        with self.db() as db:
            old = db.execute('SELECT * FROM vehicle_applications WHERE id=77').fetchone()
            self.assertEqual((old['status'], old['reason'], old['reject_reason']),
                ('pending', '原任务旧申请', '历史备注'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM plan_vehicle_supersessions WHERE application_id=77').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_applications').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0], 1)
            self.assertEqual(tuple(db.execute('SELECT start_mileage,end_mileage,status FROM vehicle_use_records').fetchone()),
                (1000, 1020, 'returned'))
            rearranged = db.execute("SELECT COUNT(*) FROM plan_schedule_events WHERE event_type='vehicle_rearranged'").fetchone()[0]
            self.assertLessEqual(rearranged, 1)

    def test_r1_reapplication_supersedes_old_pending_without_rewriting_decision(self):
        self._assert_r1_reapplication_and_retries(self._prepare_r1_vehicle_reapplication())

    def test_r1_reapplication_excludes_already_superseded_pending(self):
        self._assert_r1_reapplication_and_retries(
            self._prepare_r1_vehicle_reapplication(superseded=True))

    def test_r1_reapplication_can_select_another_vehicle(self):
        self._assert_r1_reapplication_and_retries(
            self._prepare_r1_vehicle_reapplication(vehicle_id=2))

    def test_r1_reapplication_switch_excludes_already_superseded_applicant_conflict(self):
        self._assert_r1_reapplication_and_retries(
            self._prepare_r1_vehicle_reapplication(superseded=True, vehicle_id=2))

    def test_r1_other_effective_vehicle_reservations_still_conflict_and_roll_back(self):
        payload = self._prepare_r1_vehicle_reapplication()
        with self.db() as db:
            db.execute("""INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,start_at,end_at,status,reason)
                VALUES(88,1,3,?,?,'pending','他人有效预约')""", (payload['start_at'], payload['end_at']))
        for status in ('pending', 'approved'):
            with self.subTest(status=status):
                with self.db() as db:
                    db.execute('UPDATE vehicle_applications SET status=? WHERE id=88', (status,))
                response = self.client.post('/api/vehicle/applications',
                    headers=self.headers('operator-token'), json=payload)
                self.assertEqual(response.status_code, 409, response.json)
                self.assertEqual(response.json['conflict_id'], 88)
                with self.db() as db:
                    self.assertFalse(service._vehicle_confirmation_superseded(db, 77))
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_schedule_events WHERE event_type='vehicle_rearranged'").fetchone()[0], 0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_applications').fetchone()[0], 3)

    def test_r1_applicant_independent_reservations_still_conflict(self):
        payload = self._prepare_r1_vehicle_reapplication()
        with self.db() as db:
            db.execute("""INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,start_at,end_at,status,reason)
                VALUES(88,2,2,?,?,'pending','独立用车预约')""", (payload['start_at'], payload['end_at']))
        for status in ('pending', 'approved'):
            with self.subTest(status=status):
                with self.db() as db:
                    db.execute('UPDATE vehicle_applications SET status=? WHERE id=88', (status,))
                response = self.client.post('/api/vehicle/applications',
                    headers=self.headers('operator-token'), json=payload)
                self.assertEqual(response.status_code, 409, response.json)
                self.assertEqual(response.json['error'], '申请人时间冲突')
                self.assertEqual(response.json['conflict_id'], 88)
                with self.db() as db:
                    self.assertFalse(service._vehicle_confirmation_superseded(db, 77))

    def test_r1_actual_held_trip_blocks_reapplication_for_either_holder(self):
        payload = self._prepare_r1_vehicle_reapplication(superseded=True)
        with self.db() as db:
            db.execute("""INSERT INTO vehicle_applications
                (id,vehicle_id,applicant_id,status,reason) VALUES(88,1,3,'approved','实际持车')""")
            db.execute("""INSERT INTO vehicle_use_records(application_id,status)
                VALUES(88,'checked_out')""")
        for holder in (3, 2):
            with self.subTest(holder=holder):
                with self.db() as db:
                    db.execute('UPDATE vehicle_applications SET applicant_id=? WHERE id=88', (holder,))
                response = self.client.post('/api/vehicle/applications',
                    headers=self.headers('operator-token'), json=payload)
                self.assertEqual(response.status_code, 409, response.json)
                with self.db() as db:
                    self.assertIsNone(db.execute('SELECT returned_at FROM vehicle_use_records WHERE application_id=88').fetchone()[0])
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_applications').fetchone()[0], 3)

    def test_r1_independent_request_keeps_its_existing_conflict_rule(self):
        payload = self._prepare_r1_vehicle_reapplication(superseded=True)
        payload.pop('execution_plan_id')
        response = self.client.post('/api/vehicle/applications',
            headers=self.headers('operator-token'), json=payload)
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['conflict_id'], 77)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_applications').fetchone()[0], 2)

    def test_routine_reapplication_is_approved_and_checkout_uses_the_existing_task(self):
        self.prepare(vehicle_id=1)
        self.assertEqual(self.confirm().status_code,200)
        with self.db() as db:
            db.execute('ALTER TABLE vehicle_applications ADD COLUMN execution_plan_id INTEGER')
            original = db.execute('SELECT id FROM vehicle_applications').fetchone()[0]
            db.execute("UPDATE vehicle_applications SET status='returned' WHERE id=?",(original,))
            db.execute("""INSERT INTO vehicle_use_records(application_id,start_mileage,end_mileage,returned_at,status)
                VALUES(?,1000,1020,?,'returned')""",(original,self.day()+' 00:10:00'))
            db.execute('UPDATE vehicles SET current_mileage=1020 WHERE id=1')
            db.execute("INSERT INTO vehicle_inspections VALUES(1,1,'dispatch','normal')")
        payload = {'execution_plan_id':self.plan_id(),'vehicle_id':1,
            'start_at':self.day()+' 01:00:00','end_at':self.day()+' 04:00:00',
            'reason':'原任务继续巡检','_idempotency_key':'new-trip'}
        response = self.client.post('/api/vehicle/applications',headers=self.headers('operator-token'),json=payload)
        self.assertEqual(response.status_code,201,response.json)
        self.assertEqual(response.json['status'],'approved')
        duplicate = self.client.post('/api/vehicle/applications',headers=self.headers('operator-token'),json=payload)
        self.assertEqual(duplicate.json['id'],response.json['id'])
        checkout = self.client.post('/api/vehicle/use-records',headers=self.headers('operator-token'),
            json={'application_id':response.json['id'],'start_mileage':1020,'out_inspection_id':1})
        self.assertEqual(checkout.status_code,201,checkout.json)
        today = self.client.get('/api/mobile/today-execution',headers=self.headers('operator-token'))
        self.assertEqual(today.status_code,200,today.json)
        package = today.json['packages'][0]
        self.assertEqual(package['plan_id'],self.plan_id())
        self.assertTrue(package['vehicle_use']['can_return'])
        self.assertEqual(package['vehicle_use']['application_id'],response.json['id'])
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_use_records').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT end_mileage FROM vehicle_use_records WHERE application_id=?',(original,)).fetchone()[0],1020)

    def test_two_clients_confirm_one_version_and_old_admin_cannot_decide(self):
        self.prepare(vehicle_id=1)
        def confirm_from_client(_):
            with service.app.test_client() as client:
                return client.post('/api/plan-schedules/5/confirm',headers=self.headers('operator-token'),
                    json={'version':1})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm_from_client,range(2)))
        self.assertEqual([r.status_code for r in results],[200,200])
        for action in ('approve','reject'):
            response = self.client.post(f'/api/plan-schedules/5/{action}',
                headers=self.headers('manager-token'),json={'version':1,'reason':'旧页面'})
            self.assertEqual(response.status_code,409,response.json)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_applications').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_schedule_events WHERE event_type='confirmed'").fetchone()[0],1)

    def test_confirmation_without_approver_is_atomic_and_replayable(self):
        self.prepare()
        with mock.patch.object(service, '_ps_approver_ids', side_effect=AssertionError('must not request approval')):
            first, second = self.confirm(), self.confirm()
        self.assertEqual(first.status_code, 200, first.json)
        self.assertTrue(second.json['already_confirmed'])
        with self.db() as db:
            row = db.execute('SELECT * FROM plan_schedules WHERE id=5').fetchone()
            self.assertEqual(row['status'], 'approved')
            self.assertIsNone(row['approver_id'])
            self.assertIsNone(row['approved_at'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plan_items').fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_schedule_events WHERE event_type='confirmed'").fetchone()[0], 1)

    def test_failure_preserves_draft_and_retry_generates_once(self):
        self.prepare()
        real = service._ps_generate_tasks
        def fail_after_tasks(db, schedule):
            real(db, schedule)
            raise RuntimeError('injected after task creation')
        with mock.patch.object(service, '_ps_generate_tasks', side_effect=fail_after_tasks):
            failed = self.confirm()
        self.assertEqual(failed.status_code, 409)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT status FROM plan_schedules WHERE id=5').fetchone()[0], 'draft')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0], 0)
        self.assertEqual(self.confirm().status_code, 200)

    def test_foreign_owner_and_stale_version_rejected(self):
        self.prepare()
        self.assertEqual(self.confirm(token='other-token').status_code, 403)
        self.assertEqual(self.confirm(token='manager-token').status_code, 403)
        self.assertEqual(self.confirm(version=8).status_code, 409)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0], 0)

    def test_notice_failure_does_not_undo_confirmation(self):
        self.prepare()
        with mock.patch.object(service, '_create_notification', side_effect=RuntimeError('notice unavailable')):
            response = self.confirm()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(self.plan_id())

    def test_other_holder_leaves_resource_unresolved_without_blocking_field(self):
        self.prepare(vehicle_id=1)
        with self.db() as db:
            db.execute("INSERT INTO vehicle_applications(id,vehicle_id,applicant_id,status) VALUES (7,1,3,'approved')")
            db.execute("INSERT INTO vehicle_use_records(application_id,status) VALUES (7,'checked_out')")
            db.execute("UPDATE vehicles SET status='in_use' WHERE id=1")
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['vehicle_unresolved'])
        arrival = self.arrive()
        self.assertEqual(arrival.status_code, 200, arrival.json)
        with self.db() as db:
            gate = service._inspection_arrival_gate(db, self.plan_id(), 2)
            self.assertTrue(gate['allowed'])
            self.assertFalse(gate['resource_ready'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_use_records').fetchone()[0], 1)

    def test_owner_same_vehicle_reuses_actual_trip(self):
        self.prepare(vehicle_id=1)
        with self.db() as db:
            db.execute("INSERT INTO vehicle_applications(id,vehicle_id,applicant_id,status) VALUES (7,1,2,'approved')")
            db.execute("INSERT INTO vehicle_use_records(application_id,status) VALUES (7,'checked_out')")
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['reused_trip_applications'], [7])
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vehicle_applications').fetchone()[0], 1)

    def test_legacy_decisions_preserved_and_pending_car_is_not_second_approval(self):
        self.prepare(vehicle_id=1, status='rejected')
        with self.db() as db:
            db.execute("UPDATE plan_schedules SET reject_reason='旧审核理由',approver_id=3,approved_at='2026-08-10 09:00:00' WHERE id=5")
            db.execute("INSERT INTO vehicle_applications(id,vehicle_id,applicant_id,reason,status,reject_reason) VALUES (7,1,2,'巡检计划#5用车','pending','历史理由')")
        response = self.confirm()
        self.assertEqual(response.status_code, 200, response.json)
        with self.db() as db:
            old = db.execute('SELECT * FROM vehicle_applications WHERE id=7').fetchone()
            self.assertEqual((old['status'],old['reject_reason']), ('pending','历史理由'))
            self.assertTrue(service._vehicle_confirmation_superseded(db,7))
            row = db.execute('SELECT * FROM plan_schedules WHERE id=5').fetchone()
            self.assertEqual((row['reject_reason'],row['approver_id']), ('旧审核理由',3))
        rejected = self.client.post('/api/vehicle/applications/7/approve', headers=self.headers('manager-token'), json={'action':'reject'})
        self.assertEqual(rejected.status_code, 409, rejected.json)

    def test_direct_create_confirmation_replays_one_task(self):
        self.prepare()
        with self.db() as db:
            db.execute('DELETE FROM plan_schedules WHERE id=5')
        payload = self.creation_payload()
        first = self.client.post('/api/plan-schedules',headers=self.headers('operator-token'),json=payload)
        second = self.client.post('/api/plan-schedules',headers=self.headers('operator-token'),json=payload)
        self.assertEqual(first.status_code,201,first.json)
        self.assertEqual(second.status_code,200,second.json)
        self.assertEqual(first.json['status'],'approved')
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0],1)

    def arrive(self, key='arrival-1'):
        return self.client.post('/api/mobile/check-in',headers=self.headers('operator-token'),json={
            'plan_id':self.plan_id(),'site_id':1,'lat':28.68,'lng':115.73,'_idempotency_key':key})

    def visit_state(self):
        with self.db() as db:
            return visits.state(db,self.plan_id(),1)

    def leave(self, confirm=True, version=None, legacy=False):
        plan = self.plan_id()
        if legacy:
            with self.db() as db:
                item = db.execute('SELECT id FROM insp_plan_items WHERE plan_id=?',(plan,)).fetchone()[0]
            url = f'/api/inspection-v2/plans/{plan}/items/{item}/checkout'
        else:
            url = f'/api/mobile/execution-plans/{plan}/sites/1/check-out'
        return self.client.post(url,headers=self.headers('operator-token'),json={
            'lat':28.68,'lng':115.73,'confirm_incomplete':confirm,
            'version':version or self.visit_state()['visit_version']})

    def correct(self, state=None, token='operator-token'):
        state = state or self.visit_state()
        return self.client.post(f'/api/mobile/execution-plans/{self.plan_id()}/sites/1/correct-check-out',
            headers=self.headers(token),json={'version':state['visit_version'],
                'event_id':state['visit_event_id'],'reason':'误触离站，本人仍在站内'})

    def test_partial_departure_retains_progress_and_correction_is_audited(self):
        self.prepare(); self.assertEqual(self.confirm().status_code,200)
        self.assertEqual(self.arrive().status_code,200)
        with self.db() as db:
            db.execute("UPDATE insp_plan_items SET result='normal',photo_urls='[\"kept.jpg\"]' WHERE item_name='读数'")
        self.assertEqual(self.leave(confirm=False).status_code,409)
        left = self.leave()
        self.assertEqual(left.status_code,200,left.json)
        self.assertEqual((left.json['completed'],left.json['total'],left.json['execution_status']),(1,2,'active'))
        self.assertTrue(left.json['can_correct_checkout'])
        corrected = self.correct()
        self.assertEqual(corrected.status_code,200,corrected.json)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_visit_events').fetchone()[0],3)
            self.assertEqual(db.execute("SELECT photo_urls FROM insp_plan_items WHERE item_name='读数'").fetchone()[0],'["kept.jpg"]')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plans').fetchone()[0],1)

    def test_correction_rejects_later_facts_foreign_actor_and_stop(self):
        self.prepare(); self.confirm(); self.arrive(); self.assertEqual(self.leave().status_code,200)
        before = self.visit_state()
        self.assertEqual(self.correct(before,token='other-token').status_code,403)
        with self.db() as db:
            db.execute("UPDATE insp_plan_items SET result='normal' WHERE item_name='读数'")
        self.assertEqual(self.correct(before).status_code,409)
        self.assertEqual(self.correct(self.visit_state()).status_code,409)
        stop = self.client.post('/api/plan-schedules/5/stop',headers=self.headers('operator-token'),json={'version':1,'reason':'停止剩余'})
        self.assertEqual(stop.status_code,200,stop.json)
        self.assertEqual(self.correct(before).status_code,403)

    def test_real_return_adds_arrival_and_delayed_checkout_does_not_close_new_visit(self):
        self.prepare(); self.confirm(); self.arrive()
        before = self.visit_state()
        self.assertEqual(self.leave().status_code,200)
        self.assertEqual(self.arrive('actual-return').status_code,200)
        self.assertEqual(self.leave(version=before['visit_version']).status_code,200)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inspection_checkins').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM insp_plan_items WHERE check_out_time IS NOT NULL').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inspection_visit_events WHERE event_type='return_arrival'").fetchone()[0],1)

    def test_legacy_checkout_entry_uses_same_partial_rule_and_full_completion(self):
        self.prepare(); self.confirm(); self.arrive()
        self.assertEqual(self.leave(confirm=False,legacy=True).status_code,410)
        with self.db() as db:
            db.execute("UPDATE insp_plan_items SET result='normal'")
        response = self.leave()
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['execution_status'],'completed')
        self.assertEqual(self.leave().status_code,200)

    def test_change_draft_keeps_original_executable_and_confirm_preserves_completed_item(self):
        self.prepare(); self.confirm(); self.arrive()
        with self.db() as db:
            db.execute("UPDATE insp_plan_items SET result='normal',photo_urls='[\"kept.jpg\"]' WHERE item_name='读数'")
        original_plan = self.plan_id()
        change = self.client.post('/api/plan-schedules/5/request-change',headers=self.headers('operator-token'),
            json={'version':1,'change_reason':'调整未做项目'})
        self.assertEqual(change.status_code,200,change.json)
        self.assertEqual(self.arrive('during-draft').status_code,200)
        edited = self.client.put('/api/plan-schedules/5',headers=self.headers('operator-token'),json={
            'version':change.json['version'],'plan_data':{self.day():{'sites':[1],'inspection_items':{'1':[102]}}}})
        self.assertEqual(edited.status_code,200,edited.json)
        confirmed = self.confirm(edited.json['version'])
        self.assertEqual(confirmed.status_code,200,confirmed.json)
        with self.db() as db:
            row=db.execute("SELECT * FROM insp_plan_items WHERE item_name='读数'").fetchone()
            self.assertEqual((row['result'],row['photo_urls'],row['execution_status']),('normal','["kept.jpg"]','active'))
        self.assertEqual(self.plan_id(),original_plan)


if __name__ == '__main__':
    unittest.main()

